"""Order matching simulator: depth-walking fills with VWAP/slippage.

The simulator never assumes the last traded quote — it walks the recorded
orderbook levels best-first and computes how much of the stake could
actually be filled and at what average price.
"""

from dataclasses import dataclass

_EPS = 1e-9


@dataclass(frozen=True)
class FillResult:
    filled_usd: float
    filled_tokens: float
    vwap: float  # filled_usd / filled_tokens (0.0 when nothing filled)
    best_quote: float  # top-of-book price before the walk (0.0 if empty book)
    slippage: float  # vwap - best_quote for buys (signed)
    slippage_bps: float
    levels_consumed: int
    exhausted: bool  # True when book depth < stake (partial or zero fill)


def _zero_fill(best_quote: float = 0.0) -> FillResult:
    return FillResult(
        filled_usd=0.0,
        filled_tokens=0.0,
        vwap=0.0,
        best_quote=best_quote,
        slippage=0.0,
        slippage_bps=0.0,
        levels_consumed=0,
        exhausted=True,
    )


def simulate_market_buy(
    asks: list[tuple[float, float]],
    stake_usd: float,
    fee_rate: float = 0.0,
) -> FillResult:
    """Simulate a market BUY of `stake_usd` against ask levels.

    `asks` must be best-first (ascending price): [(price, size_tokens), ...].
    Returns a partial fill with exhausted=True when the book is too thin.
    Fees reduce the budget available for tokens: effective stake is
    stake_usd / (1 + fee_rate); reported filled_usd includes fees paid.
    """
    if stake_usd <= 0 or not asks:
        return _zero_fill(best_quote=asks[0][0] if asks else 0.0)

    best_quote = asks[0][0]
    budget = stake_usd / (1.0 + fee_rate)
    remaining = budget
    tokens = 0.0
    levels = 0

    for price, size in asks:
        if remaining <= _EPS:
            break
        if price <= 0 or size <= 0:
            continue
        level_usd = price * size
        take_usd = min(remaining, level_usd)
        tokens += take_usd / price
        remaining -= take_usd
        levels += 1

    spent = budget - remaining
    if tokens <= _EPS or spent <= _EPS:
        return _zero_fill(best_quote=best_quote)

    filled_usd = spent * (1.0 + fee_rate)
    vwap = spent / tokens
    slippage = vwap - best_quote
    slippage_bps = (slippage / best_quote) * 10_000 if best_quote > 0 else 0.0
    return FillResult(
        filled_usd=filled_usd,
        filled_tokens=tokens,
        vwap=vwap,
        best_quote=best_quote,
        slippage=slippage,
        slippage_bps=slippage_bps,
        levels_consumed=levels,
        exhausted=remaining > _EPS,
    )


def simulate_market_sell(
    bids: list[tuple[float, float]],
    tokens_to_sell: float,
    fee_rate: float = 0.0,
) -> FillResult:
    """Simulate a market SELL of `tokens_to_sell` against bid levels.

    `bids` must be best-first (descending price). Unused by the current
    buy-and-hold-to-expiry strategy, provided for completeness.
    Slippage is signed as best_quote - vwap (positive = worse than top).
    """
    if tokens_to_sell <= 0 or not bids:
        return _zero_fill(best_quote=bids[0][0] if bids else 0.0)

    best_quote = bids[0][0]
    remaining = tokens_to_sell
    proceeds = 0.0
    levels = 0

    for price, size in bids:
        if remaining <= _EPS:
            break
        if price <= 0 or size <= 0:
            continue
        take_tokens = min(remaining, size)
        proceeds += take_tokens * price
        remaining -= take_tokens
        levels += 1

    sold = tokens_to_sell - remaining
    if sold <= _EPS:
        return _zero_fill(best_quote=best_quote)

    vwap = proceeds / sold
    slippage = best_quote - vwap
    slippage_bps = (slippage / best_quote) * 10_000 if best_quote > 0 else 0.0
    return FillResult(
        filled_usd=proceeds * (1.0 - fee_rate),
        filled_tokens=sold,
        vwap=vwap,
        best_quote=best_quote,
        slippage=slippage,
        slippage_bps=slippage_bps,
        levels_consumed=levels,
        exhausted=remaining > _EPS,
    )
