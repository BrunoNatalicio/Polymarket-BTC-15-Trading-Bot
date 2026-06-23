"""Standalone tests for local_signal.guppy (run: uv run python local_signal/test_guppy.py).

No pytest — mirrors the repo's standalone test convention. Exits non-zero on the
first failed assertion so the Maestro Harness test runner catches regressions.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from local_signal.guppy import (  # noqa: E402
    Candle,
    GuppyParams,
    ema,
    guppy_signal,
    sma,
    wilder_rsi,
)

PARAMS = GuppyParams()


def _approx(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= tol


def _candle(
    close: float, open_: float, volume: float, *, closed: bool = True
) -> Candle:
    hi = max(close, open_)
    lo = min(close, open_)
    return {
        "open": open_,
        "high": hi,
        "low": lo,
        "close": close,
        "volume": volume,
        "is_closed": closed,
    }


# --------------------------------------------------------------------------- #
# Indicators (exact, hand-computable vectors)
# --------------------------------------------------------------------------- #
def test_sma_known_vector():
    out = sma([1.0, 2.0, 3.0, 4.0, 5.0], 3)
    assert _approx(out[2], 2.0), out
    assert _approx(out[3], 3.0), out
    assert _approx(out[4], 4.0), out


def test_ema_known_vector():
    # seed (SMA of first 3) at index 2 = 2.0; alpha = 0.5
    out = ema([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], 3)
    assert _approx(out[2], 2.0), out
    assert _approx(out[3], 3.0), out
    assert _approx(out[4], 4.0), out
    assert _approx(out[5], 5.0), out


def test_wilder_rsi_known_vector():
    # length=2, closes [10, 11, 10.5, 11.5]: hand-computed below.
    out = wilder_rsi([10.0, 11.0, 10.5, 11.5], 2)
    assert _approx(out[2], 66.666667, 1e-4), out
    assert _approx(out[3], 85.714286, 1e-4), out


def test_wilder_rsi_extremes():
    rising = [float(i) for i in range(1, 40)]
    falling = [float(i) for i in range(40, 1, -1)]
    assert _approx(wilder_rsi(rising, 14)[-1], 100.0), "all gains -> 100"
    assert _approx(wilder_rsi(falling, 14)[-1], 0.0), "all losses -> 0"


# --------------------------------------------------------------------------- #
# Signal logic (behavioural; warmup-respecting series)
# --------------------------------------------------------------------------- #
def _series(
    direction: str, *, last_volume: float, last_open_delta: float
) -> list[Candle]:
    """200 flat bars (RSI~50) then 20 bars trending, so the fast EMA of RSI
    crosses the slow EMA in `direction`. `last_open_delta` sets the final candle
    colour (close - open); `last_volume` sets its volume vs the SMA(20) of ~1.0.
    """
    candles: list[Candle] = []
    price = 100.0
    for _ in range(200):
        candles.append(_candle(price, price - 0.1, 1.0))  # flat, green, low vol
    step = 0.5 if direction == "up" else -0.5
    for _ in range(19):
        price += step
        candles.append(_candle(price, price - step, 1.0))
    price += step
    last_open = price - last_open_delta
    candles.append(_candle(price, last_open, last_volume))
    return candles


def test_guppy_up_happy():
    candles = _series("up", last_volume=1000.0, last_open_delta=0.5)  # green, high vol
    assert guppy_signal(candles, PARAMS) == "UP"


def test_guppy_down_happy():
    candles = _series("down", last_volume=1000.0, last_open_delta=-0.5)  # red, high vol
    assert guppy_signal(candles, PARAMS) == "DOWN"


def test_no_signal_volume_unconfirmed():
    candles = _series("up", last_volume=0.5, last_open_delta=0.5)  # green but low vol
    assert guppy_signal(candles, PARAMS) is None


def test_no_signal_doji():
    candles = _series("up", last_volume=1000.0, last_open_delta=0.0)  # close == open
    assert guppy_signal(candles, PARAMS) is None


def test_no_signal_warmup_incomplete():
    candles = [_candle(100.0 + i, 100.0 + i - 0.1, 1.0) for i in range(50)]
    assert guppy_signal(candles, PARAMS) is None


def test_no_signal_last_candle_open():
    candles = _series("up", last_volume=1000.0, last_open_delta=0.5)
    candles[-1]["is_closed"] = False  # in-progress bar must not fire (BR4)
    assert guppy_signal(candles, PARAMS) is None


def test_determinism():
    candles = _series("up", last_volume=1000.0, last_open_delta=0.5)
    assert guppy_signal(candles, PARAMS) == guppy_signal(candles, PARAMS)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
