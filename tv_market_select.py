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

import math
from collections.abc import Sequence
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


def parse_trade_hours(spec: str) -> set[int]:
    """Parse a UTC hour-of-day whitelist into a set of hours in [0, 23].

    Accepts comma-separated hours and/or inclusive ranges; an empty string means
    NO filter (trade every hour)::

        ""        -> set()                (no filter)
        "8-15"    -> {8, 9, ..., 15}      (EU session 08-16 UTC)
        "0,4,8"   -> {0, 4, 8}
        "8-11,14" -> {8, 9, 10, 11, 14}

    Hours outside [0, 23] are dropped; malformed parts raise ``ValueError`` (a
    bad operator-set knob should fail loud, not silently trade 24/7).
    """
    hours: set[int] = set()
    for raw in spec.split(","):
        part = raw.strip()
        if not part:
            continue
        if "-" in part:
            lo_s, hi_s = part.split("-", 1)
            lo, hi = int(lo_s), int(hi_s)
            hours.update(h for h in range(lo, hi + 1) if 0 <= h <= 23)
        else:
            h = int(part)
            if 0 <= h <= 23:
                hours.add(h)
    return hours


def window_hour_utc(window_start_ts: float) -> int:
    """UTC hour-of-day (0-23) of a window-start unix timestamp."""
    return (int(window_start_ts) // 3600) % 24


def passes_session_band(
    window_start_ts: float,
    p_side: float,
    allowed_hours: set[int],
    p_ceiling: float = 1.0,
) -> bool:
    """Opt-in session + entry-prob-ceiling filter — default is a NO-OP.

    Layered ON TOP of the book-agreement floor in ``conviction_stake`` (which
    still owns the lower bound). This adds two optional cuts, both off by default:

    * ``allowed_hours`` non-empty and the window's UTC hour not in it -> skip
      (trade only the chosen sessions, e.g. ``parse_trade_hours("8-15")`` = EU).
    * ``p_side >= p_ceiling`` -> skip (upper bound of the entry-prob band).

    Empty ``allowed_hours`` AND ``p_ceiling == 1.0`` reproduce current behaviour
    exactly (always ``True``), so the filter is inert until configured. Motivated
    by the loss post-mortem: the strategy's edge sits in the EU session and the
    ``p_side`` 0.42-0.50 band; outside it the 15m up/down is a coin-flip.
    """
    if allowed_hours and window_hour_utc(window_start_ts) not in allowed_hours:
        return False
    return p_side < p_ceiling


def _logit(p: float) -> float:
    """Log-odds of ``p``, clamped just inside (0, 1) so it stays finite."""
    p = min(1.0 - 1e-9, max(1e-9, p))
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    """Inverse of ``_logit`` — maps a log-odds back to a probability in (0, 1)."""
    return 1.0 / (1.0 + math.exp(-x))


def z_momentum(closes: Sequence[float], k: int = 3, n: int = 20) -> float:
    """Volatility-normalised momentum of the last ``k`` bar log-returns.

    ``closes`` is a chronological series of bar closes (oldest first, newest
    last). Returns a unitless Sharpe-like z-score::

        r_i   = ln(close_i / close_{i-1})
        sigma = stdev(last n returns)
        z     = (sum of last k returns) / (sigma * sqrt(k))

    A large ``|z|`` means the recent move is big relative to its own noise; the
    sign is the move's direction. This is the one feature partially ORTHOGONAL
    to the book — the thin 15m Polymarket book may not yet price a real spot
    move. Returns ``0.0`` (neutral, no influence on the posterior) when there is
    too little history or volatility is zero, so it can never fabricate a signal.
    """
    if k < 1 or n < 2 or len(closes) < n + 1:
        return 0.0
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    window = rets[-n:]
    mean = sum(window) / len(window)
    var = sum((r - mean) ** 2 for r in window) / (len(window) - 1)
    sigma = math.sqrt(var)
    if sigma <= 0.0:
        return 0.0
    momentum = sum(rets[-k:])
    return momentum / (sigma * math.sqrt(k))


def confirm_probability(
    p_side: float,
    base_rate: float,
    beta_book: float,
    p_bar: float = 0.5,
    beta_mom: float = 0.0,
    z_mom: float = 0.0,
) -> float:
    """Calibrated win-probability for a signal whose book-implied prob is ``p_side``.

    This is the smooth generalisation of the hard book-agreement floor in
    ``conviction_stake``: instead of a binary ``p_side >= p_floor`` cut, it
    updates the signal's historical hit-rate (``base_rate``, the prior) with the
    market's view AND the underlying's own move in log-odds space::

        logit(P_win) = logit(base_rate)
                       + beta_book * (p_side - p_bar)     # the order book
                       + beta_mom  * z_mom                # spot momentum (Phase 2)

    The book term nudges the prior up when the market agrees the bet is likely
    (``p_side > p_bar``) and down when it disagrees; ``z_mom`` (from
    ``z_momentum``) adds the orthogonal spot-momentum lean. Degenerate settings
    keep the change honest and tunable from the backtest:

    * ``beta_mom == 0``       -> Phase-1 book-only posterior (z_mom ignored).
    * ``beta_book == 0``      -> flat prior, ``P_win == base_rate`` for every book.
    * ``base_rate == 0.5`` with a large ``beta_book`` and ``p_bar == p_floor``
      (and ``beta_mom == 0``) reproduces the hard floor exactly.

    All coefficients are fit via ``backtest tune``, never hand-set (mirrors
    ``conviction_stake``'s floor/full/frac being swept there).
    """
    return _sigmoid(_logit(base_rate) + beta_book * (p_side - p_bar) + beta_mom * z_mom)


def fee_breakeven_prob(price: float, fee_rate: float) -> float:
    """Minimum true win-prob to break even buying one side at ``price`` after fee.

    The Polymarket taker fee is skimmed in shares (``matching.simulate_market_buy``):
    a buy of ``S`` at ``price`` nets ``(S/price) * (1 - fee_rate*(1-price))`` shares,
    so expected PnL is zero at::

        q_breakeven = price / (1 - fee_rate * (1 - price))

    With ``fee_rate == 0`` this collapses to ``price`` itself (pay-the-probability
    fair value). A confirmation must clear this, not merely ``P_win > 0.5`` — the
    fee bites hardest near ``price == 0.5`` where these signals tend to land.
    """
    denom = 1.0 - fee_rate * (1.0 - price)
    return price / denom if denom > 0.0 else 1.0


def confirm_signal(
    p_side: float,
    price: float,
    base_rate: float,
    beta_book: float,
    tau: float,
    fee_rate: float = 0.0,
    p_bar: float = 0.5,
    beta_mom: float = 0.0,
    z_mom: float = 0.0,
) -> bool:
    """Calibrated confirmation gate: trade iff the posterior win-prob clears both
    the tuned threshold ``tau`` AND the fee-adjusted breakeven for ``price``.

    Generalises the ``stake > 0`` book-agreement gate. ``base_rate == 0.5``,
    ``p_bar == p_floor``, large ``beta_book``, ``tau == 0.5``, ``fee_rate == 0``
    and ``beta_mom == 0`` reproduce the old ``p_side >= p_floor`` decision
    exactly, so a backtest sweep that includes that config can never report worse
    than the current gate. ``beta_mom``/``z_mom`` add the Phase-2 momentum term.
    """
    p_win = confirm_probability(p_side, base_rate, beta_book, p_bar, beta_mom, z_mom)
    return p_win >= tau and p_win > fee_breakeven_prob(price, fee_rate)
