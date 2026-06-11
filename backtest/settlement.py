"""Oracle resolution and PnL settlement.

Outcome sources, in order of trust:
1. 'gamma'  — Polymarket's own resolution (outcomePrices on a closed market).
2. 'candle' — approximation from the underlying BTC 15-min candle on
   Coinbase (close > open => UP/YES). The repo's CoinbaseDataSource only
   returns recent candles (no time-range parameters), so this module calls
   the same public REST endpoint directly with start/end to reach any
   historical window. Tie rule (close == open) resolves to NO/DOWN — flag
   kept explicit; candle settlements are marked outcome_source='candle' so
   approximate results are distinguishable in reports.

Payout model: winning outcome token redeems $1.00, losing token $0.
pnl = payout - filled_usd (fees already inside filled_usd from matching).
"""

import sqlite3
import time
from typing import Any

import httpx
from loguru import logger

import backtest.db as db
import backtest.gamma as gamma

COINBASE_BASE = "https://api.exchange.coinbase.com"
HTTP_TIMEOUT = 10.0
WINDOW_SECONDS = 900


def _candle_outcome(window_start: int, window_end: int) -> str | None:
    """Approximate outcome from the Coinbase BTC-USD 15-min candle."""
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            resp = client.get(
                f"{COINBASE_BASE}/products/BTC-USD/candles",
                params={
                    "granularity": WINDOW_SECONDS,
                    "start": window_start,
                    "end": window_end,
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning(f"Coinbase candle fetch failed for window {window_start}: {e}")
        return None
    # Format per candle: [time, low, high, open, close, volume]
    for candle in data if isinstance(data, list) else []:
        try:
            if int(candle[0]) == window_start:
                open_, close = float(candle[3]), float(candle[4])
                return "YES" if close > open_ else "NO"
        except (TypeError, ValueError, IndexError):
            continue
    logger.warning(f"No Coinbase candle found for window {window_start}")
    return None


def resolve_outcome(
    market_slug: str, window_start: int, window_end: int
) -> tuple[str, str] | None:
    """Return (outcome, source) for an expired market, or None if unresolvable."""
    outcome = gamma.get_resolved_outcome(market_slug)
    if outcome is not None:
        return outcome, "gamma"
    outcome = _candle_outcome(window_start, window_end)
    if outcome is not None:
        return outcome, "candle"
    return None


def settle_backfill(con: sqlite3.Connection, grace_s: int = 120) -> int:
    """Resolve every expired market still missing an outcome. Returns count."""
    cutoff = time.time() - grace_s
    rows = con.execute(
        "SELECT market_slug, window_start, window_end FROM markets "
        "WHERE outcome IS NULL AND window_end < ? ORDER BY window_start",
        (cutoff,),
    ).fetchall()
    resolved = 0
    for slug, window_start, window_end in rows:
        result = resolve_outcome(slug, window_start, window_end)
        if result is None:
            logger.warning(f"Could not resolve {slug} yet")
            continue
        outcome, source = result
        db.set_market_outcome(con, slug, outcome, source)
        logger.info(f"Settled {slug}: {outcome} (source={source})")
        resolved += 1
    return resolved


def settle_fill(
    direction: str,
    filled_usd: float,
    filled_tokens: float,
    outcome: str,
) -> dict[str, Any]:
    """Settle one fill at expiry.

    UP bought YES tokens, DOWN bought NO tokens; the bought side wins when
    it matches the market outcome, paying $1 per token.
    """
    side = "YES" if direction == "UP" else "NO"
    won = side == outcome
    payout = filled_tokens * 1.0 if won else 0.0
    return {
        "won": won,
        "payout": payout,
        "pnl": payout - filled_usd,
    }
