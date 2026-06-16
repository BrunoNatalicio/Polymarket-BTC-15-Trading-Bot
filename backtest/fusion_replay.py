"""Fusion-strategy replay: the late-window favorite-follower, on recorded books.

Unlike ``engine.run_replay`` (driven by external TradingView signals), this
GENERATES the entry itself for every recorded market — mirroring the live fusion
path in ``bot.py``: at minute ~13 of the 15-minute window, read the Polymarket UP
(YES) mid and **buy the favorite** (YES if mid > ``trend_up``, NO if < ``trend_down``,
skip the 0.40–0.60 deadband), then hold to settlement.

This is the **L0 baseline** of the calibration-brain ladder (see
``.context/docs/fusion-strategy.md`` §7). The 6-signal vote is NOT a direction
input live — it is only an activity gate — so L0 omits it; the gate (L1) and
conviction sizing (L2) plug into the ``gate_fn``/``stake_fn`` seams here without
refactoring. Reuses ``ingest`` (load), ``matching`` (depth-walk + taker fee) and
``settlement`` (CLOB outcome + payout) unchanged.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pandas as pd

import backtest.ingest as ingest
import backtest.matching as matching
import backtest.settlement as settlement
from backtest.engine import ReplayReport

SERIES = {
    "15m": ("btc-updown-15m-", 900),
    "5m": ("btc-updown-5m-", 300),
}


@dataclass
class FusionReplayReport(ReplayReport):
    """``ReplayReport`` plus fusion-specific counters."""

    deadband_skip: int = 0  # favorite price inside [trend_down, trend_up] -> no trade
    gate_skip: int = 0  # blocked by an optional L1+ gate_fn
    markets_total: int = 0

    def summary(self) -> dict[str, Any]:
        s = super().summary()
        s["deadband_skip"] = self.deadband_skip
        s["gate_skip"] = self.gate_skip
        s["markets_total"] = self.markets_total
        return s


def _mid(bid_m: Any, ask_m: Any) -> float | None:
    """Mid price in [0,1] from integer-thousandths bid/ask, or None."""
    bid = float(bid_m) / 1000.0 if pd.notna(bid_m) else None
    ask = float(ask_m) / 1000.0 if pd.notna(ask_m) else None
    if bid is not None and ask is not None:
        return (bid + ask) / 2.0
    return ask if ask is not None else bid


def _nearest(snaps: pd.DataFrame, token_id: str, target_ts: float) -> pd.Series | None:
    """Row of the snapshot whose ts is closest to ``target_ts`` for ``token_id``."""
    if snaps.empty:
        return None
    sub = snaps[snaps["token_id"].astype(str) == str(token_id)]
    if sub.empty:
        return None
    return sub.loc[(sub["ts"] - target_ts).abs().idxmin()]


def run_fusion_replay(
    con: sqlite3.Connection,
    start_ts: float,
    end_ts: float,
    stake_usd: float = 3.0,
    fee_rate: float = 0.07,
    series: str = "15m",
    entry_second: float = 810.0,
    entry_tolerance: float = 30.0,
    trend_up: float = 0.60,
    trend_down: float = 0.40,
    gate_fn: Callable[[str, float], bool] | None = None,
    stake_fn: Callable[[str, float, float], float] | None = None,
) -> FusionReplayReport:
    """Replay the late-window favorite-follower over recorded markets.

    For each recorded market in ``[start_ts, end_ts)`` of ``series``: take the YES
    snapshot nearest ``window_start + entry_second`` (within ``entry_tolerance``),
    follow the favorite (``trend_up``/``trend_down`` with a skip deadband), buy the
    bought token at its own recorded asks (``matching.simulate_market_buy`` with the
    taker ``fee_rate``), and settle via the market's CLOB outcome.

    ``gate_fn(direction, p_side) -> bool`` (L1 EV gate) and
    ``stake_fn(direction, p_side, base) -> float`` (L2 sizing) are the plug points
    for the calibration brain; both default to pass-through so L0 is flat-stake,
    no-gate — exactly the deployed strategy.
    """
    slug_prefix = SERIES[series][0]
    report = FusionReplayReport(start_ts=start_ts, end_ts=end_ts, stake_usd=stake_usd)
    markets = ingest.load_markets(con)
    if markets.empty:
        return report
    sel = markets[markets["market_slug"].str.startswith(slug_prefix)]
    sel = sel[(sel["window_start"] >= start_ts) & (sel["window_start"] < end_ts)]

    for row in sel.to_dict("records"):
        report.markets_total += 1
        ws = int(row["window_start"])
        yes_tok, no_tok = str(row["yes_token_id"]), str(row["no_token_id"])
        outcome = row["outcome"] if isinstance(row["outcome"], str) else None
        target = ws + entry_second

        snaps = ingest.load_snapshot_meta(
            con, [yes_tok, no_tok], target - entry_tolerance, target + entry_tolerance
        )
        yes_snap = _nearest(snaps, yes_tok, target)
        if yes_snap is None:
            report.unfilled_no_data += 1
            continue
        yes_mid = _mid(yes_snap["best_bid_m"], yes_snap["best_ask_m"])
        if yes_mid is None:
            report.unfilled_no_data += 1
            continue

        # TREND FILTER (follow the favorite) — identical to bot.py's live rule.
        if yes_mid > trend_up:
            direction, bought_tok = "UP", yes_tok
            bought_snap: pd.Series | None = yes_snap
        elif yes_mid < trend_down:
            direction, bought_tok = "DOWN", no_tok
            bought_snap = _nearest(snaps, no_tok, target)
        else:
            report.deadband_skip += 1
            continue
        if bought_snap is None:
            report.unfilled_no_data += 1
            continue

        # p_side = implied prob of the bought token (its own mid); YES for UP,
        # 1-YES≈NO mid for DOWN. Feeds the optional L1 gate / L2 sizing.
        p_side = _mid(bought_snap["best_bid_m"], bought_snap["best_ask_m"]) or (
            yes_mid if direction == "UP" else 1.0 - yes_mid
        )
        if gate_fn is not None and not gate_fn(direction, p_side):
            report.gate_skip += 1
            continue
        stake = stake_fn(direction, p_side, stake_usd) if stake_fn else stake_usd
        if stake <= 0:
            report.gate_skip += 1
            continue

        books = ingest.load_levels_for_snapshots(con, [int(bought_snap["snapshot_id"])])
        asks = books.get(int(bought_snap["snapshot_id"]), {}).get("asks", [])
        fill = matching.simulate_market_buy(asks, stake, fee_rate=fee_rate)
        if fill.filled_tokens <= 0:
            report.unfilled_no_liquidity += 1
            continue

        trade: dict[str, Any] = {
            "signal_id": row["market_slug"],
            "signal_ts": float(target),
            "direction": direction,
            "market_slug": row["market_slug"],
            "token_id": bought_tok,
            "entry_mid": yes_mid,
            "p_side": p_side,
            "snapshot_age_s": float(bought_snap["ts"]) - target,
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
