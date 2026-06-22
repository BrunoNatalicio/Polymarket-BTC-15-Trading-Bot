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

# Install the secret-redaction safety net right after env vars are loaded, so no
# loguru line can leak a credential in the window before main() configures the
# file sink. Idempotent with the call inside log_setup.setup_file_logging.
from log_setup import enable_log_redaction  # noqa: E402

enable_log_redaction()

CLOB_BASE = "https://clob.polymarket.com"
HTTP_TIMEOUT = 5.0
# Polymarket up/down recurring series: name -> (slug prefix, window length s,
# poll cadence s). The 15m/5m btc series are epoch-aligned and polled densely;
# the 4h multi-asset pool is phase-discovered (see candidate_window_starts) and
# polled sparsely so a 4h window doesn't flood the DB (~960 snaps/window @ 15s).
SERIES = {
    "15m": ("btc-updown-15m-", 900, 2.0),
    "5m": ("btc-updown-5m-", 300, 2.0),
    "btc-4h": ("btc-updown-4h-", 14400, 15.0),
    "eth-4h": ("eth-updown-4h-", 14400, 15.0),
    "sol-4h": ("sol-updown-4h-", 14400, 15.0),
    "xrp-4h": ("xrp-updown-4h-", 14400, 15.0),
}
HOUR_S = 3600
PHASE_DISCOVERY_MIN_WINDOW_S = HOUR_S  # win_s >= this => probe-discover the phase
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


def candidate_window_starts(window_seconds: int, now: float | None = None) -> list[int]:
    """Hour-aligned window starts that could contain ``now``, most recent first.

    Polymarket's 4h up/down windows are 14400s, consecutive and non-overlapping,
    but NOT stably epoch-aligned: the phase (``window_start % window_seconds``) can
    shift by an hour after a gap. They always start on an hour boundary, though, so
    the active window's start is one of the ``window_seconds // 3600`` hour marks at
    or before ``now`` whose window still covers ``now``. The recorder probes these
    against Gamma and tracks whichever resolves — phase-shift proof. Returns the
    most-recent candidate first so the live window is found on the first probe.
    """
    ts = int(now if now is not None else time.time())
    hour = ts - (ts % HOUR_S)
    n = max(1, window_seconds // HOUR_S)
    return [hour - k * HOUR_S for k in range(n)]


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
        # slug -> {yes_token_id, no_token_id, window_start, window_end, poll_s}
        self.tracked: dict[str, dict[str, Any]] = {}
        # slug -> last orderbook fetch time, for per-market poll cadence
        self.last_polled: dict[str, float] = {}
        self.redis_client: redis.Redis | None = None
        self._next_redis_retry = 0.0

    # -- market tracking -----------------------------------------------------

    def _track_market(
        self, prefix: str, window_start: int, window_seconds: int, poll_s: float
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
            "poll_s": poll_s,
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
            prefix, win_s, poll_s = SERIES[name]
            if win_s >= PHASE_DISCOVERY_MIN_WINDOW_S:
                self._refresh_long_series(prefix, win_s, poll_s, now)
            else:
                start = current_window_start(win_s, now)
                self._track_market(prefix, start, win_s, poll_s)
                lead = min(PREFETCH_LEAD_S, win_s // 3)
                if now >= start + win_s - lead:
                    self._track_market(prefix, start + win_s, win_s, poll_s)
        expired = [
            slug
            for slug, info in self.tracked.items()
            if now > info["window_end"] + EXPIRY_GRACE_S
        ]
        for slug in expired:
            del self.tracked[slug]
            self.last_polled.pop(slug, None)
            logger.info(f"Stopped tracking expired market {slug}")

    def _refresh_long_series(
        self, prefix: str, win_s: int, poll_s: float, now: float
    ) -> None:
        """Track the active window for a phase-discovered (long) series.

        Probes Gamma only at rollover: once the live window is tracked, the
        wrong-phase candidates are never re-queried. Prefetches the next window
        (start + win_s) just before rollover so the book is warm at the boundary.
        """
        active_start: int | None = next(
            (
                info["window_start"]
                for slug, info in self.tracked.items()
                if slug.startswith(prefix)
                and info["window_start"] <= now < info["window_end"]
            ),
            None,
        )
        if active_start is None:
            for cand in candidate_window_starts(win_s, now):
                self._track_market(prefix, cand, win_s, poll_s)
                if f"{prefix}{cand}" in self.tracked:
                    active_start = cand
                    break
        if active_start is not None and now >= active_start + win_s - PREFETCH_LEAD_S:
            self._track_market(prefix, active_start + win_s, win_s, poll_s)

    # -- orderbook polling ---------------------------------------------------

    def record_books(self, now: float) -> int:
        written = 0
        for slug, info in list(self.tracked.items()):
            if now - self.last_polled.get(slug, 0.0) < info["poll_s"]:
                continue  # per-market cadence: long windows poll sparsely
            self.last_polled[slug] = now
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
        for s in os.getenv(
            "BACKTEST_SERIES", "15m,5m,btc-4h,eth-4h,sol-4h,xrp-4h"
        ).split(",")
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
