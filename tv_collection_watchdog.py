"""Collection watchdog: guards the data-collection pipeline.

Silent while healthy. Exits (notifying the caller) on:
  - STALL: recorder stopped writing orderbook snapshots (> STALL_SECONDS)
  - REDIS_DOWN: Redis unreachable (signal tap would be paused)
  - HEARTBEAT: periodic all-good summary every HEARTBEAT_SECONDS

The recorder is the only writer of the irreplaceable L2 data, so a stalled
recorder is the high-priority alarm. Read-only against the DB and Redis.

Usage: python tv_collection_watchdog.py [heartbeat_s] [stall_s] [poll_s]
"""

import datetime
import os
import sqlite3
import sys
import time

import redis

import backtest.db as db

DB_PATH = os.getenv("BACKTEST_DB_PATH", db.DEFAULT_DB_PATH)
HEARTBEAT_S = float(sys.argv[1]) if len(sys.argv) > 1 else 10800.0  # 3h
STALL_S = float(sys.argv[2]) if len(sys.argv) > 2 else 180.0
POLL_S = float(sys.argv[3]) if len(sys.argv) > 3 else 60.0


def snapshot_stats():
    con = sqlite3.connect(DB_PATH, timeout=5)
    try:
        total = con.execute("SELECT count(*) FROM orderbook_snapshots").fetchone()[0]
        mx = con.execute("SELECT max(ts) FROM orderbook_snapshots").fetchone()[0]
        sigs = con.execute(
            "SELECT count(*) FROM signals WHERE source='tradingview'"
        ).fetchone()[0]
        return total, mx, sigs
    finally:
        con.close()


def now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def main():
    r = redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", 6379)),
        db=int(os.getenv("REDIS_DB", 2)),
        decode_responses=True,
    )
    total0, _, sigs0 = snapshot_stats()
    started = time.time()
    print(
        f"WATCHDOG_START {now_str()} snapshots={total0} tv_signals={sigs0} "
        f"heartbeat={HEARTBEAT_S / 3600:.1f}h stall={STALL_S:.0f}s",
        flush=True,
    )

    while True:
        time.sleep(POLL_S)
        nowt = time.time()

        # Redis health (signal tap)
        try:
            r.ping()
        except Exception as e:
            print(f"REDIS_DOWN {now_str()} {e}", flush=True)
            print("WATCHDOG_END", flush=True)
            return

        # Recorder health (the irreplaceable L2 stream)
        try:
            total, mx, sigs = snapshot_stats()
        except Exception as e:
            print(f"DB_READ_ERR {now_str()} {e}", flush=True)
            continue
        age = nowt - mx if mx else 1e9
        if age > STALL_S:
            last = (
                datetime.datetime.fromtimestamp(mx).strftime("%H:%M:%S") if mx else "?"
            )
            print(
                f"STALL {now_str()} recorder nao grava ha {age:.0f}s "
                f"(ultimo snapshot {last}) — coleta L2 PAROU",
                flush=True,
            )
            print("WATCHDOG_END", flush=True)
            return

        # Periodic all-good heartbeat
        if nowt - started >= HEARTBEAT_S:
            rate = total - total0
            print(
                f"HEARTBEAT {now_str()} OK | snapshots {total0}->{total} "
                f"(+{rate}) | tv_signals {sigs0}->{sigs} (+{sigs - sigs0}) | "
                f"ultimo snapshot ha {age:.0f}s",
                flush=True,
            )
            print("WATCHDOG_END", flush=True)
            return


if __name__ == "__main__":
    main()
