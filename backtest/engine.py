"""Replay orchestration: ingest -> align -> match -> settle -> report.

Processes one 15-minute market window at a time, so peak memory depends on
a single window's data (a few signals + ~450 snapshot rows per token at the
default 2s poll cadence), never on total history length.
"""

import sqlite3
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

import backtest.ingest as ingest
import backtest.matching as matching
import backtest.settlement as settlement
from tv_market_select import conviction_stake

SERIES = {
    "15m": ("btc-updown-15m-", 900),
    "5m": ("btc-updown-5m-", 300),
}


@dataclass
class ReplayReport:
    start_ts: float
    end_ts: float
    stake_usd: float
    trades: list[dict[str, Any]] = field(default_factory=list)
    unfilled_no_data: int = 0
    unfilled_no_market: int = 0
    unfilled_no_liquidity: int = 0
    unfilled_min_book_prob: int = 0
    unsettled: int = 0

    @property
    def settled(self) -> list[dict[str, Any]]:
        return [t for t in self.trades if t.get("outcome") is not None]

    def summary(self) -> dict[str, Any]:
        settled = self.settled
        pnl = sum(t["pnl"] for t in settled)
        wins = sum(1 for t in settled if t["won"])
        slippages = [t["slippage_bps"] for t in self.trades]
        return {
            "signals_total": len(self.trades)
            + self.unfilled_no_data
            + self.unfilled_no_market
            + self.unfilled_no_liquidity
            + self.unfilled_min_book_prob,
            "fills": len(self.trades),
            "settled": len(settled),
            "unsettled": self.unsettled,
            "unfilled_no_data": self.unfilled_no_data,
            "unfilled_no_market": self.unfilled_no_market,
            "unfilled_no_liquidity": self.unfilled_no_liquidity,
            "unfilled_min_book_prob": self.unfilled_min_book_prob,
            "wins": wins,
            "win_rate": (wins / len(settled)) if settled else None,
            "total_pnl": pnl,
            "total_staked": sum(t["filled_usd"] for t in settled),
            "avg_slippage_bps": (sum(slippages) / len(slippages)) if slippages else 0,
            "exhausted_books": sum(1 for t in self.trades if t["exhausted"]),
        }

    def equity_curve(self) -> list[float]:
        equity, curve = 0.0, []
        for t in sorted(self.settled, key=lambda t: t["signal_ts"]):
            equity += t["pnl"]
            curve.append(equity)
        return curve


def _price_m(value: Any) -> float | None:
    """A nullable integer-thousandths field -> price in [0,1], or None."""
    if value is None or not pd.notna(value):
        return None
    return float(value) / 1000.0


def _bought_side_prob(row: dict[str, Any], asks: list[tuple[float, float]]) -> float:
    """Implied probability of the bought token from its own book at entry.

    Prefers the snapshot mid (best_bid_m+best_ask_m)/2000 — matching the live
    bot's mid-based ``p_side`` — and falls back to the top ask when the bid is
    missing (or the top ask alone when meta is absent).
    """
    bid = _price_m(row.get("best_bid_m"))
    ask = _price_m(row.get("best_ask_m"))
    if bid is not None and ask is not None:
        return (bid + ask) / 2.0
    if ask is not None:
        return ask
    return asks[0][0] if asks else 0.0


def run_replay(
    con: sqlite3.Connection,
    start_ts: float,
    end_ts: float,
    stake_usd: float = 50.0,
    tolerance_s: float = 10.0,
    fee_rate: float = 0.0,
    fill_policy: str = "partial",
    series: str = "15m",
    source_like: str | None = None,
    min_entry_prob: float = 0.0,
    size_full_prob: float = 1.0,
    size_min_frac: float = 1.0,
) -> ReplayReport:
    """Replay signals against recorded books.

    ``min_entry_prob`` / ``size_full_prob`` / ``size_min_frac`` drive the hybrid
    book-agreement gate + conviction sizing (shared with the live bot via
    ``tv_market_select.conviction_stake``). Their defaults (floor 0, min_frac 1)
    reproduce the flat-stake baseline exactly, so a sweep that includes the
    defaults can never report a config worse than baseline.
    """
    slug_prefix, window_seconds = SERIES[series]
    report = ReplayReport(start_ts=start_ts, end_ts=end_ts, stake_usd=stake_usd)
    markets = ingest.load_markets(con)
    signals = ingest.load_signals(con, start_ts, end_ts, source_like=source_like)
    if signals.empty:
        return report
    signals = ingest.attach_target_tokens(
        signals, markets, window_seconds=window_seconds, slug_prefix=slug_prefix
    )

    # One market window at a time keeps memory flat regardless of history.
    for _slug, group in signals.groupby("market_slug", sort=True):
        sig_group: pd.DataFrame = group
        no_market = sig_group["token_id"].isna()
        report.unfilled_no_market += int(no_market.sum())
        sig_group = sig_group.loc[~no_market]
        if sig_group.empty:
            continue

        token_ids = sorted({str(t) for t in sig_group["token_id"]})
        w_start = float(sig_group["window_start"].min())
        snaps = ingest.load_snapshot_meta(
            con, token_ids, w_start, w_start + window_seconds + tolerance_s
        )
        aligned = ingest.align_signals_to_snapshots(sig_group, snaps, tolerance_s)

        matched = aligned.dropna(subset=["snapshot_id"])
        report.unfilled_no_data += len(aligned) - len(matched)
        if matched.empty:
            continue

        books = ingest.load_levels_for_snapshots(
            con, [int(s) for s in matched["snapshot_id"].tolist()]
        )
        for row in matched.to_dict("records"):
            asks = books.get(int(row["snapshot_id"]), {}).get("asks", [])
            # p_side = bought-token implied prob (its own book mid). The bought
            # token is YES for UP / NO for DOWN (attach_target_tokens), so this
            # is the real probability of the bet — same quantity the live gate
            # uses. Hybrid gate + sizing decides the stake before the fill.
            p_side = _bought_side_prob(row, asks)
            stake = conviction_stake(
                p_side, stake_usd, min_entry_prob, size_full_prob, size_min_frac
            )
            if stake <= 0:
                report.unfilled_min_book_prob += 1
                continue
            fill = matching.simulate_market_buy(asks, stake, fee_rate=fee_rate)
            if fill.filled_tokens <= 0:
                # snapshot existed but its ask book was empty — a real
                # liquidity condition (e.g. nobody sells the near-certain
                # winner close to expiry), distinct from missing data
                report.unfilled_no_liquidity += 1
                continue
            if fill_policy == "all_or_nothing" and fill.exhausted:
                report.unfilled_no_liquidity += 1
                continue

            outcome = row["outcome"] if isinstance(row["outcome"], str) else None
            direction = str(row["direction"])
            trade: dict[str, Any] = {
                "signal_id": row["signal_id"],
                "signal_ts": row["ts"],
                "direction": direction,
                "market_slug": row["market_slug"],
                "token_id": str(row["token_id"]),
                "snapshot_age_s": float(row["snapshot_age_s"]),
                "best_quote": fill.best_quote,
                "vwap": fill.vwap,
                "slippage_bps": fill.slippage_bps,
                "filled_usd": fill.filled_usd,
                "filled_tokens": fill.filled_tokens,
                "levels_consumed": fill.levels_consumed,
                "exhausted": fill.exhausted,
                "outcome": outcome,
                "won": None,
                "payout": None,
                "pnl": None,
            }
            if outcome is not None:
                trade.update(
                    settlement.settle_fill(
                        direction, fill.filled_usd, fill.filled_tokens, outcome
                    )
                )
            else:
                report.unsettled += 1
            report.trades.append(trade)
    return report


def trades_dataframe(report: ReplayReport) -> pd.DataFrame:
    return pd.DataFrame(report.trades)
