"""Standalone tests for local_signal_generator (run: uv run python test_local_signal_generator.py).

No pytest — mirrors the repo's standalone test convention. Uses a fake Redis so
no live server is needed; asserts the queued message is shape-compatible with the
TradingView webhook's (BR7) and that the generator only fires on a triggering
closed candle.
"""

import json
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from local_signal_generator import (  # noqa: E402
    SIGNALS_KEY,
    TV_SIGNAL_LOG_KEY,
    GuppyGenerator,
    to_guppy_candle,
)


class FakeRedis:
    """Captures rpush/ltrim calls instead of talking to a server."""

    def __init__(self):
        self.lists: dict[str, list[str]] = {}
        self.ltrims: list[tuple] = []

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)

    def ltrim(self, key, start, end):
        self.ltrims.append((key, start, end))


def _binance_candle(close, open_, vol, closed=True) -> dict:
    return {
        "open": Decimal(str(open_)),
        "high": Decimal(str(max(open_, close))),
        "low": Decimal(str(min(open_, close))),
        "close": Decimal(str(close)),
        "volume": Decimal(str(vol)),
        "is_closed": closed,
    }


def _up_series() -> list[dict]:
    """200 flat bars then 20 rising; final bar green + high volume -> UP."""
    candles: list[dict] = []
    price = 100.0
    for _ in range(200):
        candles.append(_binance_candle(price, price - 0.1, 1.0))
    for _ in range(19):
        price += 0.5
        candles.append(_binance_candle(price, price - 0.5, 1.0))
    price += 0.5
    candles.append(_binance_candle(price, price - 0.5, 1000.0))  # green, high vol
    return candles


def test_to_guppy_candle_converts_to_float():
    c = to_guppy_candle(_binance_candle(101.0, 100.0, 5.0))
    assert isinstance(c["close"], float) and c["close"] == 101.0
    assert c["is_closed"] is True


def test_triggering_candle_queues_signal():
    series = _up_series()
    fake = FakeRedis()
    gen = GuppyGenerator(fake)
    gen.seed(series[:-1])  # warmup, no last bar yet
    assert SIGNALS_KEY not in fake.lists  # seeding never emits
    gen.on_closed_candle(series[-1])

    queued = fake.lists.get(SIGNALS_KEY, [])
    assert len(queued) == 1, "exactly one signal expected"
    msg = json.loads(queued[0])
    # Shape parity with the webhook receiver (BR7): canonical fields present.
    assert msg["signal"] == "UP"
    assert "id" in msg and "received_at" in msg
    # Enriched fields for the recorder.
    assert msg["source_local"] == "guppy" and "preco_fechamento" in msg
    # Recorder copy written too.
    assert len(fake.lists.get(TV_SIGNAL_LOG_KEY, [])) == 1


def test_non_triggering_candle_is_silent():
    series = _up_series()
    series[-1] = _binance_candle(
        series[-1]["close"], series[-1]["close"], 1000.0
    )  # doji
    fake = FakeRedis()
    gen = GuppyGenerator(fake)
    gen.seed(series[:-1])
    gen.on_closed_candle(series[-1])
    assert SIGNALS_KEY not in fake.lists


def test_history_is_capped():
    fake = FakeRedis()
    gen = GuppyGenerator(fake)
    gen.seed(_up_series())
    for _ in range(50):
        gen.on_closed_candle(_binance_candle(100.0, 100.1, 1.0))
    assert len(gen.history) <= gen.history_cap


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
    sys.exit(main())
