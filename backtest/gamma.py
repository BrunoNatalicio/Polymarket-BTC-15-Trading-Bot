"""Polymarket Gamma API lookups for the backtest engine.

slug -> token ids / condition id (market discovery for the recorder) and
slug -> resolved outcome (authoritative settlement source).

All parsing is defensive: Gamma field shapes occasionally drift, and a
malformed response must never crash the recorder (warn + return None).
"""

import json
from typing import Any

import httpx
from loguru import logger

GAMMA_BASE = "https://gamma-api.polymarket.com"
HTTP_TIMEOUT = 10.0


def _get_market_raw(slug: str) -> dict[str, Any] | None:
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            resp = client.get(f"{GAMMA_BASE}/markets", params={"slug": slug})
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning(f"Gamma fetch failed for {slug}: {e}")
        return None
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    logger.warning(f"Gamma returned no market for slug {slug}")
    return None


def _parse_json_list(value: Any) -> list[Any] | None:
    """Gamma encodes some list fields as JSON strings; accept both forms."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, list) else None
    return None


def get_market_tokens(slug: str) -> dict[str, str] | None:
    """Resolve a market slug to its YES/NO token ids and condition id.

    Token ordering convention (verified against bot.py's instrument pairing):
    clobTokenIds index 0 = YES/Up, index 1 = NO/Down.
    """
    market = _get_market_raw(slug)
    if market is None:
        return None
    token_ids = _parse_json_list(market.get("clobTokenIds"))
    if not token_ids or len(token_ids) < 2:
        logger.warning(f"Gamma market {slug}: unexpected clobTokenIds shape")
        return None
    return {
        "yes_token_id": str(token_ids[0]),
        "no_token_id": str(token_ids[1]),
        "condition_id": str(market.get("conditionId", "")),
    }


def get_resolved_outcome(slug: str) -> str | None:
    """Return 'YES' / 'NO' for a resolved market, or None if not resolved yet.

    Only trusts outcomePrices when the market is flagged closed: a live
    market also carries outcomePrices, but they are current quotes there,
    not the resolution.
    """
    market = _get_market_raw(slug)
    if market is None:
        return None
    if not market.get("closed", False):
        return None
    prices = _parse_json_list(market.get("outcomePrices"))
    if not prices or len(prices) < 2:
        logger.warning(f"Gamma market {slug}: unexpected outcomePrices shape")
        return None
    try:
        yes_price, no_price = float(prices[0]), float(prices[1])
    except (TypeError, ValueError):
        logger.warning(f"Gamma market {slug}: non-numeric outcomePrices")
        return None
    if yes_price >= 0.99 and no_price <= 0.01:
        return "YES"
    if no_price >= 0.99 and yes_price <= 0.01:
        return "NO"
    logger.warning(
        f"Gamma market {slug}: closed but outcomePrices ambiguous "
        f"({yes_price}, {no_price})"
    )
    return None
