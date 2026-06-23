"""Pure, dependency-free Guppy RSI signal — local replica of the TradingView
"Guppy RSI Polymarket Bot" indicator.

No I/O, no NautilusTrader: identical to run in the backtest and live, which is
what makes the parity gate (TRK-001 T3) honest. Only the *trigger* logic of the
Pine script is reproduced — `RSIFast(7)`, `RSISlow(14)` and `ma2/ma3/ma4` are
cosmetic plots in the original and are intentionally omitted.

Pine trigger (evaluated once per CLOSED 15m candle):

    RSINorm = ta.rsi(close, 10)            # Wilder/RMA smoothing
    ma1     = ta.ema(RSINorm, 3)
    ma5     = ta.ema(RSINorm, 21)
    volMA   = ta.sma(volume, 20)

    UP   = change(ma1) >= 0 and ma1 > ma5 and volume > volMA and close > open
    DOWN = change(ma1) <= 0 and ma1 < ma5 and volume > volMA and close < open

See `.context/plans/TRK-001/01-business-rules.md` for BR1–BR16.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, TypedDict

Signal = Literal["UP", "DOWN"]


class Candle(TypedDict):
    """One OHLCV bar. Only close/open/volume/is_closed drive the signal."""

    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool


@dataclass(frozen=True)
class GuppyParams:
    """Indicator lengths. Defaults mirror the deployed Pine inputs.

    `min_warmup` is the minimum number of CLOSED candles required before any
    signal may be emitted (BR5): the EMA(21)-of-RSI must converge so the tail is
    independent of seeding — that is what reproduces TradingView, which computes
    from the full chart history.
    """

    rsi_len: int = 10
    fast: int = 3
    slow: int = 21
    vol_len: int = 20
    min_warmup: int = 200


#: Shared immutable default — frozen dataclass, safe as a module-level singleton.
DEFAULT_PARAMS = GuppyParams()


def _rsi_from(avg_gain: float, avg_loss: float) -> float:
    """RSI value from smoothed average gain/loss."""
    if avg_loss == 0.0:
        # No losses in the window: 100 if there were gains, else neutral (flat).
        return 100.0 if avg_gain > 0.0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def wilder_rsi(closes: Sequence[float], length: int) -> list[float]:
    """RSI with Wilder's smoothing (RMA, alpha = 1/length) — matches Pine ta.rsi.

    Returns a list aligned with `closes`. The first `length` entries are warmup
    and set to 50.0 (neutral); the converged tail is what callers use. The
    initial average is the simple mean of the first `length` deltas, then Wilder
    smoothing follows — identical to ta.rsi's RMA seeding.
    """
    n = len(closes)
    rsi = [50.0] * n
    if n <= length:
        return rsi

    gains = 0.0
    losses = 0.0
    for i in range(1, length + 1):
        delta = closes[i] - closes[i - 1]
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    avg_gain = gains / length
    avg_loss = losses / length
    rsi[length] = _rsi_from(avg_gain, avg_loss)

    for i in range(length + 1, n):
        delta = closes[i] - closes[i - 1]
        gain = delta if delta > 0 else 0.0
        loss = -delta if delta < 0 else 0.0
        avg_gain = (avg_gain * (length - 1) + gain) / length
        avg_loss = (avg_loss * (length - 1) + loss) / length
        rsi[i] = _rsi_from(avg_gain, avg_loss)
    return rsi


def ema(values: Sequence[float], length: int) -> list[float]:
    """Standard EMA (alpha = 2/(length+1)) — matches Pine ta.ema.

    Pine seeds ta.ema with an SMA of the first `length` values, then applies the
    EMA recurrence. Output is aligned with the input; warmup entries (before the
    seed index) carry the raw value and are never read by the tail logic.
    """
    n = len(values)
    out = [0.0] * n
    if n == 0:
        return out
    alpha = 2.0 / (length + 1.0)

    if n < length:
        # Not enough for a full SMA seed: running cumulative mean (warmup only).
        running = values[0]
        out[0] = running
        for i in range(1, n):
            running += (values[i] - running) / (i + 1)
            out[i] = running
        return out

    for i in range(length - 1):
        out[i] = values[i]
    seed = sum(values[:length]) / length
    out[length - 1] = seed
    for i in range(length, n):
        out[i] = alpha * values[i] + (1.0 - alpha) * out[i - 1]
    return out


def sma(values: Sequence[float], length: int) -> list[float]:
    """Simple moving average — matches Pine ta.sma. Warmup entries are the
    partial mean (window not yet full); aligned with the input."""
    n = len(values)
    out = [0.0] * n
    if n == 0 or length <= 0:
        return out
    running = 0.0
    for i in range(n):
        running += values[i]
        if i >= length:
            running -= values[i - length]
        window = length if i >= length - 1 else i + 1
        out[i] = running / window
    return out


def _decide(
    prev_ma1: float,
    ma1: float,
    ma5: float,
    volume: float,
    vol_ma: float,
    open_: float,
    close: float,
) -> Signal | None:
    """Per-bar Guppy trigger (BR1, BR3) — the single source of truth shared by
    `guppy_signal` and `guppy_signal_series`. Strict on volume/colour, non-strict
    on momentum."""
    change_ma1 = ma1 - prev_ma1
    vol_ok = volume > vol_ma
    if change_ma1 >= 0.0 and ma1 > ma5 and vol_ok and close > open_:
        return "UP"
    if change_ma1 <= 0.0 and ma1 < ma5 and vol_ok and close < open_:
        return "DOWN"
    return None


def guppy_signal_series(
    candles: Sequence[Candle], params: GuppyParams = DEFAULT_PARAMS
) -> list[Signal | None]:
    """Per-bar signal for every candle, computed in a single O(n) pass.

    Entry `i` is what `guppy_signal(candles[:i+1])` would return — equal because
    RSI/EMA/SMA are causal (a bar depends only on `[0..i]`). `None` while the
    warmup is incomplete (BR5) or a bar is not closed (BR4). Assumes a gapless,
    closed-terminated history (gaps are handled upstream by the generator, BR16).
    """
    n = len(candles)
    out: list[Signal | None] = [None] * n
    if n == 0:
        return out

    closes = [c["close"] for c in candles]
    vols = [c["volume"] for c in candles]
    rsi = wilder_rsi(closes, params.rsi_len)
    ma1 = ema(rsi, params.fast)
    ma5 = ema(rsi, params.slow)
    vol_ma = sma(vols, params.vol_len)

    closed_count = 0
    for i in range(n):
        if candles[i]["is_closed"]:
            closed_count += 1
        if i < 1 or closed_count < params.min_warmup or not candles[i]["is_closed"]:
            continue
        out[i] = _decide(
            ma1[i - 1],
            ma1[i],
            ma5[i],
            vols[i],
            vol_ma[i],
            candles[i]["open"],
            candles[i]["close"],
        )
    return out


def guppy_signal(
    candles: Sequence[Candle], params: GuppyParams = DEFAULT_PARAMS
) -> Signal | None:
    """Evaluate the latest CLOSED candle of `candles`. Returns 'UP'/'DOWN'/None.

    Pure: same input -> same output, no I/O (BR15). Returns None when the warmup
    is incomplete (BR5) or the last candle is not closed (BR4). Thin wrapper over
    `guppy_signal_series` so the trigger logic has one source of truth.
    """
    if not candles:
        return None
    return guppy_signal_series(candles, params)[-1]
