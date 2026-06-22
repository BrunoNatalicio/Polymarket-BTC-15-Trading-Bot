"""Loss post-mortem for the TradingView strategy — slice the LOSSES.

Universe: the strategy view (every signal -> N+1 window -> CLOB), replayed
UNGATED so every loss is visible (including the ones the live book-agreement gate
would skip). Each loss is then sliced by the dimensions that might explain it:

- entry probability ``p_side`` (the bought side's book mid — the gate's own knob),
- direction (UP/DOWN),
- hour-of-day / trading session (UTC),
- BTC spot momentum agreement (does the signal fight the spot trend?),
- execution (slippage, exhausted book).

This grades the *signal*, not execution reality — see ``bot_trades`` for the bot's
realized trades. Reuses ``engine.run_replay`` and ``ingest`` momentum helpers; it
adds no new outcome logic. Pure read against the recorded CLOB.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any, cast

import pandas as pd

import backtest.ingest as ingest
from backtest.bot_trades import _window_start_from_slug
from backtest.engine import run_replay

SESSION_BOUNDS = (("Asia", 0, 8), ("EU", 8, 16), ("US", 16, 24))


def _session(hour: int) -> str:
    for name, lo, hi in SESSION_BOUNDS:
        if lo <= hour < hi:
            return name
    return "?"


def _mom_relation(direction: str, z: float) -> str:
    """How the bet relates to BTC spot momentum at entry.

    UP agrees with positive momentum, DOWN with negative. ``flat`` is the near-zero
    deadband; ``sem dado`` (NaN) means the window had no recorded close.
    """
    if pd.isna(z):
        return "sem dado"
    if abs(z) < 0.25:
        return "flat"
    bullish = z > 0
    wants_up = direction == "UP"
    return "concorda" if bullish == wants_up else "briga"


def _enrich(con: sqlite3.Connection, settled: list[dict[str, Any]],
            source_like: str | None, start_ts: float, end_ts: float,
            window_seconds: int) -> pd.DataFrame:
    """settled trades -> one row per trade with the post-mortem dimensions."""
    df = pd.DataFrame(settled)
    zmap = ingest.z_mom_by_window(
        ingest.load_closes_from_signals(con, source_like, start_ts, end_ts, window_seconds)
    )
    ws = df["market_slug"].map(lambda s: _window_start_from_slug(str(s)))
    df["window_start"] = ws
    df["z_mom"] = ws.map(lambda w: zmap.get(int(w)) if pd.notna(w) else None)
    hours = df["signal_ts"].map(lambda t: datetime.fromtimestamp(float(t), tz=UTC).hour)
    df["hour"] = hours
    df["session"] = hours.map(_session)
    df["mom"] = df.apply(lambda r: _mom_relation(str(r["direction"]), r["z_mom"]), axis=1)
    df["loss"] = ~df["won"].astype(bool)
    return df


def _breakdown(df: pd.DataFrame, key: Any, title: str) -> None:
    """Print win-rate / losses / PnL per bucket of ``key`` (col name or Series)."""
    base_wr = df["won"].mean()
    print(f"\n{title}  (baseline win {base_wr:.0%})")
    print(f"  {'bucket':<14} {'n':>4} {'losses':>7} {'win%':>6} {'lift':>6} {'PnL':>9}")
    grouped = df.groupby(key) if isinstance(key, str) else df.groupby(key)
    for bucket, sub in grouped:
        n = len(sub)
        losses = int(sub["loss"].sum())
        wr = sub["won"].mean()
        lift = wr - base_wr
        pnl = sub["pnl"].sum()
        print(
            f"  {str(bucket):<14} {n:>4} {losses:>7} {wr:>5.0%} "
            f"{lift:>+5.0%} {pnl:>+9.2f}"
        )


def run_loss_postmortem(
    con: sqlite3.Connection,
    start_ts: float,
    end_ts: float,
    stake_usd: float = 3.0,
    fee_rate: float = 0.07,
    series: str = "15m",
    source_like: str | None = "tradingview",
    gate_floor: float = 0.42,
) -> None:
    window_seconds = 300 if series == "5m" else 900
    report = run_replay(
        con, start_ts=start_ts, end_ts=end_ts, stake_usd=stake_usd,
        fee_rate=fee_rate, series=series, source_like=source_like,
        min_entry_prob=0.0,
    )
    settled = report.settled
    if not settled:
        print("Nenhum trade resolvido na janela — nada a analisar.")
        return
    df = _enrich(con, settled, source_like, start_ts, end_ts, window_seconds)

    losses = df[df["loss"]]
    n, nl = len(df), len(losses)
    line = "=" * 72
    print(line)
    print("POST-MORTEM DOS LOSSES — estratégia TradingView (sinal -> N+1 -> CLOB)")
    print(line)
    print(
        f"Período : {datetime.fromtimestamp(start_ts, tz=UTC):%Y-%m-%d %H:%M} -> "
        f"{datetime.fromtimestamp(end_ts, tz=UTC):%Y-%m-%d %H:%M} UTC | série {series} "
        f"| fonte={source_like} | stake=${stake_usd:.2f} | fee={fee_rate:.2f}"
    )
    print(
        f"Universo: {n} resolvidos | {nl} LOSSES ({nl / n:.0%}) | "
        f"$ perdido nos losses {losses['pnl'].sum():+.2f} | "
        f"perda média {losses['pnl'].mean():+.2f}"
    )

    # --- gate partition: which losses the live floor already removes -------------
    caught = losses[losses["p_side"] < gate_floor]
    survive = losses[losses["p_side"] >= gate_floor]
    print("-" * 72)
    print(f"GATE (floor {gate_floor:.2f}) — partição dos {nl} losses:")
    print(
        f"  já pega  (p_side < {gate_floor:.2f}): {len(caught):>3} losses "
        f"| ${caught['pnl'].sum():+.2f}"
    )
    print(
        f"  SOBREVIVE (p_side >= {gate_floor:.2f}): {len(survive):>3} losses "
        f"| ${survive['pnl'].sum():+.2f}  <- resíduo a explicar"
    )

    # --- full-universe slices -----------------------------------------------------
    prob_bins = [0.0, gate_floor, 0.50, 0.60, 1.01]
    prob_labels = [f"<{gate_floor:.2f}", f"{gate_floor:.2f}-0.50", "0.50-0.60", ">=0.60"]
    df["prob_bucket"] = pd.cut(df["p_side"], bins=prob_bins, labels=prob_labels,
                               right=False)
    _breakdown(df, "prob_bucket", "[1] Por prob de entrada (p_side)")
    _breakdown(df, "direction", "[2] Por direção")
    _breakdown(df, "session", "[3] Por sessão (UTC)")
    _breakdown(df, "mom", "[4] Por relação com o momentum do BTC")

    slip_bins = [-1e9, 0, 25, 75, 1e9]
    slip_labels = ["<=0", "0-25", "25-75", ">75"]
    df["slip_bucket"] = pd.cut(df["slippage_bps"], bins=slip_bins, labels=slip_labels)
    _breakdown(df, "slip_bucket", "[5] Por slippage (bps)")

    # --- residual (gate-surviving) loss patterns ---------------------------------
    surv_all = cast("pd.DataFrame", df[df["p_side"] >= gate_floor])
    print("\n" + "-" * 72)
    print(f"RESÍDUO — só trades que passam o gate (p_side >= {gate_floor:.2f}), "
          f"{len(surv_all)} trades:")
    _breakdown(surv_all, "direction", "  resíduo · por direção")
    _breakdown(surv_all, "session", "  resíduo · por sessão")
    _breakdown(surv_all, "mom", "  resíduo · por momentum")
    print(line)
