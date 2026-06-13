"""Recorder daemon: polls Polymarket CLOB orderbooks and taps TradingView
signals from Redis into SQLite.

Data collection is the project's gold: Polymarket has no historical L2 API,
so this process is the only source of backtest data. Run it continuously:

    uv run python -m backtest record

Failure policy: an HTTP error skips one tick; a Redis outage never stops
orderbook recording (signals resume when Redis returns); SIGINT closes
cleanly. The recorder is the ONLY writer to the database.
"""

import os
import sqlite3
import time
from typing import Any, cast

import httpx
import redis
from dotenv import load_dotenv
from loguru import logger

import backtest.db as db

load_dotenv()

CLOB_BASE = "https://clob.polymarket.com"
HTTP_TIMEOUT = 5.0
# Polymarket BTC up/down recurring series: slug prefix -> window length (s).
SERIES = {
    "15m": ("btc-updown-15m-", 900),
    "5m": ("btc-updown-5m-", 300),
}
PREFETCH_LEAD_S = 60  # fetch next market's tokens this long before rollover
EXPIRY_GRACE_S = 30  # keep polling an expired market this long past its end
TV_SIGNAL_LOG_KEY = "btc_trading:tv_signal_log"
SIGNAL_DRAIN_BATCH = 100


def fetch_book(token_id: str) -> dict[str, Any] | None:
    """Fetch one CLOB orderbook (same sync-httpx pattern as the live bot)."""
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            resp = client.get(f"{CLOB_BASE}/book", params={"token_id": token_id})
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning(f"Book fetch failed for {token_id[:16]}…: {e}")
        return None


def normalize_levels(
    raw_levels: list[dict[str, Any]], *, descending: bool, depth: int
) -> list[tuple[float, float]]:
    """Convert CLOB level dicts to (price, size) best-first, depth-capped.

    The raw CLOB response lists bids ascending by price — bids must be
    re-sorted descending (best first); asks ascending (best first).
    """
    parsed: list[tuple[float, float]] = []
    for level in raw_levels:
        try:
            price = float(level.get("price", 0))
            size = float(level.get("size", 0))
        except (TypeError, ValueError):
            continue
        if price > 0 and size > 0:
            parsed.append((price, size))
    parsed.sort(key=lambda ps: ps[0], reverse=descending)
    return parsed[:depth]


def current_window_start(window_seconds: int, now: float | None = None) -> int:
    ts = int(now if now is not None else time.time())
    return ts - (ts % window_seconds)


class Recorder:
    def __init__(
        self,
        con: sqlite3.Connection,
        poll_seconds: float,
        depth: int,
        series: list[str] | None = None,
    ):
        self.con = con
        self.poll_seconds = poll_seconds
        self.depth = depth
        self.series = [s for s in (series or list(SERIES)) if s in SERIES]
        # slug -> {yes_token_id, no_token_id, window_start, window_end}
        self.tracked: dict[str, dict[str, Any]] = {}
        self.redis_client: redis.Redis | None = None
        self._next_redis_retry = 0.0

    # -- market tracking -----------------------------------------------------

    def _track_market(
        self, prefix: str, window_start: int, window_seconds: int
    ) -> None:
        import backtest.gamma as gamma  # late import keeps module load light

        slug = f"{prefix}{window_start}"
        if slug in self.tracked:
            return
        tokens = gamma.get_market_tokens(slug)
        if tokens is None:
            return  # warned already; retried next tick
        info = {
            "yes_token_id": tokens["yes_token_id"],
            "no_token_id": tokens["no_token_id"],
            "window_start": window_start,
            "window_end": window_start + window_seconds,
        }
        self.tracked[slug] = info
        db.upsert_market(
            self.con,
            market_slug=slug,
            yes_token_id=info["yes_token_id"],
            no_token_id=info["no_token_id"],
            window_start=window_start,
            window_end=info["window_end"],
            condition_id=tokens.get("condition_id") or None,
        )
        logger.info(f"Tracking market {slug}")

    def refresh_tracked_markets(self, now: float) -> None:
        for name in self.series:
            prefix, win_s = SERIES[name]
            start = current_window_start(win_s, now)
            self._track_market(prefix, start, win_s)
            lead = min(PREFETCH_LEAD_S, win_s // 3)
            if now >= start + win_s - lead:
                self._track_market(prefix, start + win_s, win_s)
        expired = [
            slug
            for slug, info in self.tracked.items()
            if now > info["window_end"] + EXPIRY_GRACE_S
        ]
        for slug in expired:
            del self.tracked[slug]
            logger.info(f"Stopped tracking expired market {slug}")

    # -- orderbook polling ---------------------------------------------------

    def record_books(self, now: float) -> int:
        written = 0
        for slug, info in list(self.tracked.items()):
            for side_label, token_key in (
                ("YES", "yes_token_id"),
                ("NO", "no_token_id"),
            ):
                book = fetch_book(info[token_key])
                if book is None:
                    continue
                bids = normalize_levels(
                    book.get("bids") or [], descending=True, depth=self.depth
                )
                asks = normalize_levels(
                    book.get("asks") or [], descending=False, depth=self.depth
                )
                db.insert_snapshot(
                    self.con,
                    ts=now,
                    market_slug=slug,
                    token_id=info[token_key],
                    side_label=side_label,
                    bids=bids,
                    asks=asks,
                )
                written += 1
        return written

    # -- signal tap ----------------------------------------------------------

    def _get_redis(self) -> redis.Redis | None:
        if self.redis_client is not None:
            return self.redis_client
        if time.time() < self._next_redis_retry:
            return None
        try:
            client = redis.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", 6379)),
                db=int(os.getenv("REDIS_DB", 2)),
                decode_responses=True,
                socket_connect_timeout=5,
            )
            client.ping()
            self.redis_client = client
            logger.info("Redis connected (signal tap active)")
        except Exception as e:
            logger.warning(f"Redis unavailable (books still recording): {e}")
            self._next_redis_retry = time.time() + 30
        return self.redis_client

    def drain_signals(self) -> int:
        """LPOP the signal log key (owned exclusively by the recorder)."""
        import json

        client = self._get_redis()
        if client is None:
            return 0
        drained = 0
        try:
            while True:
                # redis-py types lpop as a sync/async union; this client is sync
                batch = cast(Any, client.lpop(TV_SIGNAL_LOG_KEY, SIGNAL_DRAIN_BATCH))
                if not batch:
                    break
                items: list[str] = batch if isinstance(batch, list) else [batch]
                for raw in items:
                    try:
                        msg = json.loads(raw)
                        new = db.insert_signal(
                            self.con,
                            signal_id=str(msg["id"]),
                            ts=float(msg["received_at"]),
                            direction=str(msg["signal"]).upper(),
                            source="tradingview",
                            raw_json=raw,
                        )
                        drained += 1 if new else 0
                    except (KeyError, TypeError, ValueError) as e:
                        logger.warning(f"Skipping malformed signal log entry: {e}")
                if len(items) < SIGNAL_DRAIN_BATCH:
                    break
        except Exception as e:
            logger.warning(f"Redis drain failed, will reconnect: {e}")
            self.redis_client = None
            self._next_redis_retry = time.time() + 30
        return drained

    # -- main loop -----------------------------------------------------------

    def run_forever(self) -> None:
        logger.info(
            f"Recorder started: poll={self.poll_seconds}s depth={self.depth} "
            f"db={os.getenv('BACKTEST_DB_PATH', db.DEFAULT_DB_PATH)}"
        )
        while True:
            tick_start = time.time()
            self.refresh_tracked_markets(tick_start)
            snaps = self.record_books(tick_start)
            sigs = self.drain_signals()
            if sigs:
                logger.info(f"Tapped {sigs} new signal(s)")
            if snaps == 0 and self.tracked:
                logger.warning("No snapshots written this tick (all fetches failed?)")
            elapsed = time.time() - tick_start
            time.sleep(max(0.0, self.poll_seconds - elapsed))


def main() -> int:
    from log_setup import setup_file_logging

    setup_file_logging("recorder.log")

    poll_seconds = float(os.getenv("BACKTEST_POLL_SECONDS", "2.0"))
    depth = int(os.getenv("BACKTEST_BOOK_DEPTH", "20"))
    series = [
        s.strip()
        for s in os.getenv("BACKTEST_SERIES", "15m,5m").split(",")
        if s.strip()
    ]
    con = db.connect()
    recorder = Recorder(con, poll_seconds=poll_seconds, depth=depth, series=series)
    try:
        recorder.run_forever()
    except KeyboardInterrupt:
        logger.info("Recorder shutting down")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
