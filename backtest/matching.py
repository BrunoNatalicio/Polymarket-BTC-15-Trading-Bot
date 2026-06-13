"""Order matching simulator: depth-walking fills with VWAP/slippage.

The simulator never assumes the last traded quote — it walks the recorded
orderbook levels best-first and computes how much of the stake could
actually be filled and at what average price.
"""

from dataclasses import dataclass

_EPS = 1e-9


@dataclass(frozen=True)
class FillResult:
    filled_usd: float  # USDC spent into the book (the stake, capped by depth)
    filled_tokens: float  # NET shares received, after the taker fee is skimmed
    fee_usd: float  # taker fee in USDC: C × fee_rate × p × (1−p), 0 when fee_rate=0
    vwap: float  # gross avg fill price = filled_usd / gross_tokens (0 if nothing)
    best_quote: float  # top-of-book price before the walk (0.0 if empty book)
    slippage: float  # vwap - best_quote for buys (signed)
    slippage_bps: float
    levels_consumed: int
    exhausted: bool  # True when book depth < stake (partial or zero fill)


def _zero_fill(best_quote: float = 0.0) -> FillResult:
    return FillResult(
        filled_usd=0.0,
        filled_tokens=0.0,
        fee_usd=0.0,
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

    Taker fee (Polymarket 15m/5m crypto): `fee = C × fee_rate × p × (1−p)` per
    matched lot, collected in SHARES on a buy. The full `stake_usd` is spent;
    the fee is skimmed from the shares received, so `filled_tokens` is NET of
    fee (`gross − fee_usd / vwap`) while `filled_usd` stays the stake. With the
    default `fee_rate=0.0` this is a no-op (non-fee markets / back-compat).
    """
    if stake_usd <= 0 or not asks:
        return _zero_fill(best_quote=asks[0][0] if asks else 0.0)

    best_quote = asks[0][0]
    remaining = stake_usd
    tokens = 0.0
    fee_usd = 0.0
    levels = 0

    for price, size in asks:
        if remaining <= _EPS:
            break
        if price <= 0 or size <= 0:
            continue
        level_usd = price * size
        take_usd = min(remaining, level_usd)
        take_tokens = take_usd / price
        tokens += take_tokens
        # Fee is per-lot at the lot's own price (exact across levels), not on vwap.
        fee_usd += take_tokens * fee_rate * price * (1.0 - price)
        remaining -= take_usd
        levels += 1

    spent = stake_usd - remaining
    if tokens <= _EPS or spent <= _EPS:
        return _zero_fill(best_quote=best_quote)

    vwap = spent / tokens
    # Fee skimmed in shares; net shares are what you hold into resolution.
    net_tokens = max(0.0, tokens - (fee_usd / vwap if vwap > 0 else 0.0))
    slippage = vwap - best_quote
    slippage_bps = (slippage / best_quote) * 10_000 if best_quote > 0 else 0.0
    return FillResult(
        filled_usd=spent,
        filled_tokens=net_tokens,
        fee_usd=fee_usd,
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
    # Sell path is unused by the buy-and-hold-to-expiry strategy; keep the simple
    # notional fee here (fee_usd left 0.0 — the p(1−p) model is buy-side only).
    return FillResult(
        filled_usd=proceeds * (1.0 - fee_rate),
        filled_tokens=sold,
        fee_usd=0.0,
        vwap=vwap,
        best_quote=best_quote,
        slippage=slippage,
        slippage_bps=slippage_bps,
        levels_consumed=levels,
        exhausted=remaining > _EPS,
    )
