"""Local Guppy RSI signal generator — replaces the TradingView webhook source.

Streams BINANCE BTCUSDT 15m klines, computes the Guppy signal locally
(`local_signal.guppy`, validated bar-for-bar against TradingView — see
`backtest guppy-parity`) and pushes the SAME JSON the webhook receiver pushes
to the SAME Redis queue. The bot's `_handle_tradingview_signal` consumes it
unchanged, so the whole trade path (N+1 window select, book gate, conviction
sizing, dedup, dry-run) is reused — TradingView is just the source we replace.

Runs as a SEPARATE process (like `tradingview_webhook_receiver.py`): the bot is
restarted every ~90 min and this must keep a warm RSI/EMA history across those
restarts. Warmup is re-seeded from Binance REST on every (re)connect, so a
WebSocket gap never produces a signal on stale data (BR16).

Exclusivity (BR12): run EITHER this OR the TradingView receiver — never both
feeding the queue. `btc_trading:active_strategy` must be "tradingview" for the
bot to act on these signals.

Usage:
    uv run python local_signal_generator.py

Required .env: REDIS_* (same as the bot). Optional:
    LOCAL_SIGNAL_SYMBOL    (default BTCUSDT)
    LOCAL_SIGNAL_INTERVAL  (default 15m)
"""

import asyncio
import os
import sys

from dotenv import load_dotenv
from loguru import logger

load_dotenv()

from log_setup import enable_log_redaction  # noqa: E402

enable_log_redaction()

from data_sources.binance.websocket import BinanceWebSocketSource  # noqa: E402
from local_signal.guppy import Candle, GuppyParams, guppy_signal  # noqa: E402

# Reuse the receiver's message contract and queue keys verbatim (BR7/BR8) — the
# local generator must be a drop-in source, never a re-implementation.
from tradingview_webhook_receiver import (  # noqa: E402
    MAX_QUEUE_LEN,
    MAX_SIGNAL_LOG_LEN,
    SIGNALS_KEY,
    TV_SIGNAL_LOG_KEY,
    build_signal_message,
    get_redis_client,
)

SYMBOL = os.getenv("LOCAL_SIGNAL_SYMBOL", "BTCUSDT")
INTERVAL = os.getenv("LOCAL_SIGNAL_INTERVAL", "15m")


def to_guppy_candle(c: dict) -> Candle:
    """Convert a Binance source candle (Decimal OHLCV) to a guppy Candle (float)."""
    return {
        "open": float(c["open"]),
        "high": float(c["high"]),
        "low": float(c["low"]),
        "close": float(c["close"]),
        "volume": float(c["volume"]),
        "is_closed": bool(c["is_closed"]),
    }


class GuppyGenerator:
    """Holds the rolling kline history and emits a queued signal per closed bar."""

    def __init__(self, redis_client, params: GuppyParams | None = None):
        self.redis = redis_client
        self.params = params or GuppyParams()
        self.history: list[Candle] = []
        # Bound memory: keep just enough for the slow EMA/SMA to be converged.
        self.history_cap = self.params.min_warmup + 100

    def seed(self, candles: list[dict]) -> None:
        """Seed the warmup history from REST klines (closed candles only)."""
        self.history = [to_guppy_candle(c) for c in candles if c["is_closed"]]
        self._trim()

    def _trim(self) -> None:
        if len(self.history) > self.history_cap:
            self.history = self.history[-self.history_cap :]

    def on_closed_candle(self, binance_candle: dict) -> None:
        """Stream callback: append the just-closed bar and emit if it triggers."""
        self.history.append(to_guppy_candle(binance_candle))
        self._trim()
        signal = guppy_signal(self.history, self.params)
        if signal in ("UP", "DOWN"):
            last = self.history[-1]
            self._emit(signal, last)

    def _emit(self, signal: str, candle: Candle) -> None:
        # Enrich for the backtest recorder (the bot ignores extra fields); the
        # canonical id/signal/received_at are written last by build_signal_message.
        extra = {
            "preco_fechamento": str(candle["close"]),
            "volume": str(candle["volume"]),
            "source_local": "guppy",
        }
        message = build_signal_message(signal, extra=extra)
        try:
            self.redis.rpush(SIGNALS_KEY, message)
            self.redis.ltrim(SIGNALS_KEY, -MAX_QUEUE_LEN, -1)
        except Exception as e:
            logger.error(f"Failed to queue local signal: {e}")
            return
        # Best-effort copy for the recorder; never let it fail the emit.
        try:
            self.redis.rpush(TV_SIGNAL_LOG_KEY, message)
            self.redis.ltrim(TV_SIGNAL_LOG_KEY, -MAX_SIGNAL_LOG_LEN, -1)
        except Exception:
            logger.warning("Signal log copy failed (backtest tap); continuing")
        logger.info(
            f"Local Guppy signal queued: {signal} "
            f"(close={candle['close']}, vol={candle['volume']})"
        )


async def run(generator: GuppyGenerator, src: BinanceWebSocketSource) -> None:
    """Seed → stream → reconnect loop. Re-seeds on every connect (gapless, BR16)."""
    backoff = 1.0
    while True:
        try:
            seed = src.fetch_klines(
                interval=INTERVAL, limit=generator.params.min_warmup + 50
            )
            generator.seed(seed)
            logger.info(
                f"Seeded {len(generator.history)} closed {INTERVAL} candles "
                f"for {SYMBOL}; streaming…"
            )
            backoff = 1.0
            await src.stream_klines(INTERVAL, on_closed=generator.on_closed_candle)
            logger.warning("Kline stream ended; reconnecting…")
        except Exception as e:
            logger.error(f"Generator loop error: {e}")
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2.0, 30.0)


def main() -> int:
    from log_setup import setup_file_logging

    setup_file_logging("local_signal_generator.log")

    redis_client = get_redis_client()
    if redis_client is None:
        logger.error("Cannot start without Redis (the bot consumes signals from it)")
        return 1

    src = BinanceWebSocketSource(symbol=SYMBOL.lower())
    generator = GuppyGenerator(redis_client)
    logger.info(
        f"Local Guppy signal generator starting — {SYMBOL} {INTERVAL} "
        f"(min_warmup={generator.params.min_warmup})"
    )
    try:
        asyncio.run(run(generator, src))
    except KeyboardInterrupt:
        logger.info("Shutting down")
    return 0


if __name__ == "__main__":
    sys.exit(main())
