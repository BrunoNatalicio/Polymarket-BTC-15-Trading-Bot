"""Ingestion and timeline alignment: SQLite -> pandas + pd.merge_asof.

Answers the core replay question: "given this signal at 14:00:00, what was
the real ask of the YES token at 14:00:01?" — via merge_asof with
direction='forward' (the order reaches the book AFTER the alert; never
match a book from before the signal, which would be lookahead bias).

Memory discipline: always filter in SQL (token ids + time range), load slim
columns, downcast dtypes, and fetch orderbook levels only for the matched
snapshot ids.
"""

import sqlite3
from datetime import UTC, datetime
from typing import cast

import pandas as pd

from backtest.db import m_to_price

WINDOW_SECONDS = 900


def load_signals(
    con: sqlite3.Connection, start_ts: float, end_ts: float
) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT signal_id, ts, direction, source FROM signals "
        "WHERE ts >= ? AND ts < ? ORDER BY ts",
        con,
        params=[start_ts, end_ts],
    )
    df["direction"] = df["direction"].astype("category")
    return df


def load_markets(con: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT market_slug, yes_token_id, no_token_id, window_start, window_end, "
        "outcome, outcome_source FROM markets",
        con,
    )


def attach_target_tokens(signals: pd.DataFrame, markets: pd.DataFrame) -> pd.DataFrame:
    """Map each signal to the market window it trades and the token it buys.

    UP buys the YES token, DOWN buys the NO token (same direction mapping as
    the live bot). Signals whose market was never recorded keep NaN token_id
    and are reported as unfilled, never silently dropped.
    """
    out = signals.copy()
    out["window_start"] = (out["ts"].astype("int64") // WINDOW_SECONDS) * WINDOW_SECONDS
    out["market_slug"] = "btc-updown-15m-" + out["window_start"].astype(str)
    out = out.merge(
        markets[["market_slug", "yes_token_id", "no_token_id", "outcome"]],
        on="market_slug",
        how="left",
    )
    out["token_id"] = out["yes_token_id"].where(
        out["direction"].astype(str) == "UP", out["no_token_id"]
    )
    return out.drop(columns=["yes_token_id", "no_token_id"])


def load_snapshot_meta(
    con: sqlite3.Connection,
    token_ids: list[str],
    start_ts: float,
    end_ts: float,
) -> pd.DataFrame:
    if not token_ids:
        return pd.DataFrame(
            {
                "snapshot_id": [],
                "ts": [],
                "token_id": [],
                "best_bid_m": [],
                "best_ask_m": [],
            }
        )
    placeholders = ",".join("?" for _ in token_ids)
    # placeholders is only a generated "?,?,..." string — safe parametrized SQL
    query = (
        "SELECT snapshot_id, ts, token_id, best_bid_m, best_ask_m "  # noqa: S608
        "FROM orderbook_snapshots "
        f"WHERE token_id IN ({placeholders}) AND ts >= ? AND ts < ? "
        "ORDER BY ts"
    )
    df = pd.read_sql_query(query, con, params=[*token_ids, start_ts, end_ts])
    df["snapshot_id"] = df["snapshot_id"].astype("uint32")
    df["best_bid_m"] = df["best_bid_m"].astype("Int16")
    df["best_ask_m"] = df["best_ask_m"].astype("Int16")
    df["token_id"] = df["token_id"].astype("category")
    return df


def align_signals_to_snapshots(
    signals: pd.DataFrame,
    snaps: pd.DataFrame,
    tolerance_s: float = 10.0,
) -> pd.DataFrame:
    """merge_asof: first orderbook snapshot AT OR AFTER each signal.

    by=token_id guarantees a YES-bound signal only ever matches YES-token
    snapshots. Unmatched rows (no snapshot within tolerance) keep NaN
    snapshot_id and become `unfilled_no_data` in the report.
    """
    sig = signals.dropna(subset=["token_id"]).copy()
    no_market = signals[signals["token_id"].isna()].copy()
    if sig.empty or snaps.empty:
        out = signals.copy()
        out["snapshot_id"] = pd.NA
        out["snap_ts"] = pd.NA
        out["best_ask_m"] = pd.NA
        return out

    sig["ts_dt"] = pd.to_datetime(sig["ts"], unit="s", utc=True)
    snaps = snaps.copy()
    snaps["ts_dt"] = pd.to_datetime(snaps["ts"], unit="s", utc=True)
    snaps = snaps.rename(columns={"ts": "snap_ts"})

    # merge_asof requires both frames sorted by the `on` key and matching
    # dtypes for `by`
    sig = sig.sort_values("ts_dt")
    snaps = snaps.sort_values("ts_dt")
    sig["token_id"] = sig["token_id"].astype(str)
    snaps["token_id"] = snaps["token_id"].astype(str)

    aligned = pd.merge_asof(
        sig,
        snaps[
            ["ts_dt", "snap_ts", "token_id", "snapshot_id", "best_bid_m", "best_ask_m"]
        ],
        on="ts_dt",
        by="token_id",
        direction="forward",
        tolerance=cast(pd.Timedelta, pd.Timedelta(seconds=tolerance_s)),
    )
    if not no_market.empty:
        aligned = cast(pd.DataFrame, pd.concat([aligned, no_market], ignore_index=True))
    aligned["snapshot_age_s"] = aligned["snap_ts"] - aligned["ts"]
    return aligned


def load_levels_for_snapshots(
    con: sqlite3.Connection, snapshot_ids: list[int]
) -> dict[int, dict[str, list[tuple[float, float]]]]:
    """Load full levels only for the matched snapshots (chunked IN-lists).

    Returns {snapshot_id: {"bids": [(price, size) best-first], "asks": [...]}}.
    """
    books: dict[int, dict[str, list[tuple[float, float]]]] = {
        sid: {"bids": [], "asks": []} for sid in snapshot_ids
    }
    chunk_size = 500
    for i in range(0, len(snapshot_ids), chunk_size):
        chunk = snapshot_ids[i : i + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        # placeholders is only a generated "?,?,..." string — parametrized SQL
        query = (
            "SELECT snapshot_id, side, level, price_m, size "  # noqa: S608
            "FROM orderbook_levels "
            f"WHERE snapshot_id IN ({placeholders}) "
            "ORDER BY snapshot_id, side, level"
        )
        for sid, side, _level, price_m, size in con.execute(query, chunk):
            key = "bids" if side == "bid" else "asks"
            books[sid][key].append((m_to_price(price_m), size))
    return books


def import_tradingview_csv(
    con: sqlite3.Connection,
    csv_path: str,
    up_col: str,
    down_col: str,
    bar_seconds: int = WINDOW_SECONDS,
) -> int:
    """Import historical signals from a TradingView chart-data CSV export.

    TradingView (Essential plan and up) exports the chart's OHLCV plus every
    indicator plot as columns. Expose the Pine conditions as plots, e.g.:

        plot(sinalUP ? 1 : 0,   title="sinalUP_export")
        plot(sinalDOWN ? 1 : 0, title="sinalDOWN_export")

    then pass those column names as up_col/down_col. A bar with a nonzero
    value in the column emits a signal at the BAR CLOSE time (the intrabar
    firing moment cannot be reconstructed from an export — closes are the
    conservative approximation). Imports are idempotent (deterministic ids).
    """
    from backtest.db import insert_signal

    df = pd.read_csv(csv_path)
    time_col = next((c for c in ("time", "Time", "timestamp") if c in df.columns), None)
    if time_col is None:
        raise ValueError("CSV has no 'time' column (expected TradingView export)")
    for col in (up_col, down_col):
        if col not in df.columns:
            raise ValueError(f"CSV has no column {col!r}; columns: {list(df.columns)}")

    if pd.api.types.is_numeric_dtype(df[time_col]):
        bar_open_ts = df[time_col].astype("int64")
    else:
        bar_open_ts = (
            pd.to_datetime(df[time_col], utc=True).astype("int64") // 1_000_000_000
        )

    inserted = 0
    for direction, col in (("UP", up_col), ("DOWN", down_col)):
        active = df[col].fillna(0).astype(float) > 0
        for open_ts in bar_open_ts[active]:
            close_ts = int(open_ts) + bar_seconds
            signal_id = f"csv-{close_ts}-{direction}"
            iso = datetime.fromtimestamp(close_ts, tz=UTC).isoformat()
            if insert_signal(
                con,
                signal_id=signal_id,
                ts=float(close_ts),
                direction=direction,
                source="tradingview_csv",
                raw_json=f'{{"bar_close": "{iso}"}}',
            ):
                inserted += 1
    return inserted
