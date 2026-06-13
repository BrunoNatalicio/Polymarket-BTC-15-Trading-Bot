"""Oracle resolution and PnL settlement.

Outcome sources, in order of trust:
1. 'gamma'  — Polymarket's own resolution (outcomePrices on a closed market).
2. 'candle' — approximation from the underlying BTC candle on Coinbase
   (close >= open => UP/YES, matching Polymarket's stated rule "greater
   than or equal"; the official oracle is the Chainlink BTC/USD stream, so
   candle settlements are an approximation and are marked
   outcome_source='candle'). The repo's CoinbaseDataSource only returns
   recent candles (no time-range parameters), so this module calls the same
   public REST endpoint directly with start/end to reach any historical
   window. Candle granularity follows the market's window length
   (900s for the 15m series, 300s for the 5m series).

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

# Matches the recorder's post-expiry polling grace: the last snapshot we keep
# for a market lands at most this long after window_end.
EXPIRY_GRACE_S = 30
# Decisiveness thresholds (integer thousandths): the winning token's bid sits
# near $1.00 and the loser's ask near $0.00 once the market has resolved.
CLOB_WIN_BID_M = 900  # winner best_bid >= 0.90
CLOB_LOSE_ASK_M = 100  # loser best_ask <= 0.10


def clob_outcome(
    con: sqlite3.Connection, market_slug: str, window_end: int
) -> str | None:
    """Resolve an expired market from the recorded CLOB orderbook.

    Authoritative for these btc-updown micro-markets: Gamma de-indexes them
    by slug/condition_id once closed, but the recorder captured the book right
    up to expiry, where the winning token bids ~$0.99 and the loser asks
    ~$0.01. Uses the last snapshot per side within the recorder's grace; returns
    None when the books are missing or not yet decisive (caller falls back).
    """
    cutoff = window_end + EXPIRY_GRACE_S
    best_bid: dict[str, int] = {}
    best_ask: dict[str, int] = {}
    for side in ("YES", "NO"):
        row = con.execute(
            "SELECT best_bid_m, best_ask_m FROM orderbook_snapshots "
            "WHERE market_slug = ? AND side_label = ? AND ts <= ? "
            "ORDER BY ts DESC LIMIT 1",
            (market_slug, side, cutoff),
        ).fetchone()
        if row is None:
            return None
        # Missing bid => 0 (nothing bids the loser); missing ask => 1000 (no one
        # sells the near-certain winner). Both keep the decisiveness check honest.
        best_bid[side] = row[0] if row[0] is not None else 0
        best_ask[side] = row[1] if row[1] is not None else 1000
    winner = "YES" if best_bid["YES"] >= best_bid["NO"] else "NO"
    loser = "NO" if winner == "YES" else "YES"
    if best_bid[winner] >= CLOB_WIN_BID_M and best_ask[loser] <= CLOB_LOSE_ASK_M:
        return winner
    return None


def _candle_outcome(window_start: int, window_end: int) -> str | None:
    """Approximate outcome from the Coinbase BTC-USD candle for the window."""
    granularity = window_end - window_start
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            resp = client.get(
                f"{COINBASE_BASE}/products/BTC-USD/candles",
                params={
                    "granularity": granularity,
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
                # Polymarket rule: end >= start resolves Up
                return "YES" if close >= open_ else "NO"
        except (TypeError, ValueError, IndexError):
            continue
    logger.warning(f"No Coinbase candle found for window {window_start}")
    return None


def resolve_outcome(
    con: sqlite3.Connection, market_slug: str, window_start: int, window_end: int
) -> tuple[str, str] | None:
    """Return (outcome, source) for an expired market, or None if unresolvable.

    Trust order: recorded CLOB orderbook (authoritative for these de-indexed
    micro-markets) -> Gamma resolution -> Coinbase candle approximation.
    """
    outcome = clob_outcome(con, market_slug, window_end)
    if outcome is not None:
        return outcome, "clob"
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
        result = resolve_outcome(con, slug, window_start, window_end)
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
