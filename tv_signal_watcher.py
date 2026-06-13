"""Robust watcher: notifies when the NEXT real TradingView webhook is accepted.

Primary detector: a new row in the recorder DB with source='tradingview'
(the receiver writes every ACCEPTED signal to tv_signal_log -> recorder -> DB,
independent of the bot consumer and of the dry-run path). Also reports whether
the bot consumer then recorded a dry-run trade and claimed the market lock.

Read-only. Exits on first new real signal so the caller is notified.
"""

import datetime
import os
import sqlite3
import time

import redis

import backtest.db as db

DB_PATH = os.getenv("BACKTEST_DB_PATH", db.DEFAULT_DB_PATH)


def tv_count_and_latest():
    con = sqlite3.connect(DB_PATH, timeout=5)
    try:
        n = con.execute(
            "SELECT count(*) FROM signals WHERE source='tradingview'"
        ).fetchone()[0]
        row = con.execute(
            "SELECT direction, ts FROM signals WHERE source='tradingview' "
            "ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        return n, row
    finally:
        con.close()


def dryrun_count():
    import json

    p = "tv_dry_run_trades.json"
    if not os.path.exists(p):
        return 0
    try:
        with open(p) as f:
            return len(json.load(f))
    except Exception:
        return -1


def main():
    r = redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", 6379)),
        db=int(os.getenv("REDIS_DB", 2)),
        decode_responses=True,
    )

    base_n, _ = tv_count_and_latest()
    base_dry = dryrun_count()
    base_lock = r.get("btc_trading:tv_last_traded_market")
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(
        f"WATCH_START {stamp} base_signals={base_n} base_dryrun={base_dry} "
        f"base_lock={base_lock!r}",
        flush=True,
    )

    while True:
        time.sleep(15)
        try:
            n, latest = tv_count_and_latest()
        except Exception as e:
            print(f"DB_READ_ERR {e}", flush=True)
            continue

        if n > base_n:
            direction, ts = latest
            t = datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S")
            dry = dryrun_count()
            lock = r.get("btc_trading:tv_last_traded_market")
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(
                f"SIGNAL_ARRIVED {now} direction={direction} signal_ts={t} "
                f"total_signals={n}",
                flush=True,
            )
            print(
                f"  consumer_followup: dryrun_trades {base_dry}->{dry} "
                f"(appended={dry > base_dry}) | market_lock={lock!r} "
                f"(changed={lock != base_lock})",
                flush=True,
            )
            if dry <= base_dry:
                print(
                    "  WARN: signal accepted but NO dry-run trade recorded "
                    "(consumer bailed in _execute_webhook_trade — see bot window)",
                    flush=True,
                )
            print("WATCH_END", flush=True)
            return


if __name__ == "__main__":
    main()
