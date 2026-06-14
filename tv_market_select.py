"""Pure helpers for picking which Polymarket window a TradingView signal trades.

Kept dependency-free (no NautilusTrader) so it is unit-testable on its own and
shared by `bot.py`. The whole point is the rollover fix: a TradingView alert
fires at the bar close (`:00/:15/:30/:45`), which is exactly when a 15m window
EXPIRES. Selecting by the bot's `current_instrument_index` lags (the timer loop
switches every ~10s), so the signal sometimes hits the expiring window at ~$0.99
with no edge. Instead we map by wall clock to the window that CONTAINS the
signal — `floor(ts/900)*900` — identical to the backtest's
`attach_target_tokens`, which lands on the fresh "N+1" window.
"""

from typing import Any

WINDOW_SECONDS = 900


def target_window_start(now_ts: float, window_seconds: int = WINDOW_SECONDS) -> int:
    """Window start that CONTAINS now_ts: floor(now_ts/window)*window.

    Matches `backtest.ingest.attach_target_tokens` exactly. At a bar-close
    boundary this is the freshly-opened window, not the one that just closed.
    """
    return (int(now_ts) // window_seconds) * window_seconds


def select_target_market(
    instruments: list[dict[str, Any]],
    now_ts: float,
    window_seconds: int = WINDOW_SECONDS,
) -> dict[str, Any] | None:
    """Return the market whose `market_timestamp` is the window containing now.

    `None` when that window is not in the loaded list — the caller must then
    discard the signal rather than fall back to an expiring market.
    """
    ws = target_window_start(now_ts, window_seconds)
    return next((m for m in instruments if m.get("market_timestamp") == ws), None)


def fresh_quote(
    cache: dict[Any, tuple[float, float, float]],
    instrument_id: Any,
    now_ts: float,
    max_age_s: float = 30.0,
) -> tuple[float, float] | None:
    """Latest (bid, ask) for an instrument if seen within `max_age_s`, else None.

    `cache` maps instrument_id -> (bid, ask, ts). Returns None when there is no
    entry or it is staler than `max_age_s` — so a market we pre-subscribed but
    have no recent book for never trades on a stale price.
    """
    entry = cache.get(instrument_id)
    if entry is None:
        return None
    bid, ask, ts = entry
    if now_ts - ts > max_age_s:
        return None
    return (bid, ask)


def entry_prob_and_price(
    signal: str, yes_bid: float, yes_ask: float
) -> tuple[float, float]:
    """Book-implied probability AND entry price of the side a TV signal buys.

    UP buys the YES token; DOWN buys the NO token. The live bot only caches the
    YES instrument's book, but the NO price is its complement: NO_mid = 1 -
    YES_mid. Returning the BOUGHT side's own price fixes the bug where DOWN
    trades recorded the YES mid (~0.59) instead of the NO price (~0.41), and
    yields ``p_side`` — the market's implied probability of the bet — for the
    conviction gate. Both returned values are equal (mid == fair entry price).
    """
    yes_mid = (yes_bid + yes_ask) / 2.0
    if signal == "UP":
        return yes_mid, yes_mid
    return 1.0 - yes_mid, 1.0 - yes_mid


def conviction_stake(
    p_side: float,
    base_usd: float,
    p_floor: float,
    p_full: float,
    min_frac: float,
) -> float:
    """Stake (USD) for a bet whose book-implied probability is ``p_side``.

    Hybrid gate + conviction sizing — the fix for "betting against the book":
      * ``p_side < p_floor``  -> 0.0       (gate: skip; the book disagrees)
      * ``p_side >= p_full``  -> base_usd  (full conviction)
      * in between            -> linear ramp from ``min_frac*base_usd`` up to
                                 ``base_usd``

    The result never exceeds ``base_usd`` (= MARKET_BUY_USD), so the risk-engine
    cap and the single-knob bet-size invariant hold (sizing only scales DOWN).
    ``p_floor == 0.0 and min_frac == 1.0`` reproduces the flat-stake baseline
    (no gate, no sizing) exactly — which is what keeps the backtest sweep honest.
    """
    if p_side < p_floor:
        return 0.0
    if p_side >= p_full or p_full <= p_floor:
        return base_usd
    frac = min_frac + (1.0 - min_frac) * (p_side - p_floor) / (p_full - p_floor)
    return base_usd * min(1.0, max(min_frac, frac))
