"""Quick DB inspection: row counts and latest snapshots.

Run with: uv run python backtest/inspect_db.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtest.db as db  # noqa: E402


def main() -> int:
    con = db.connect()
    snaps, tokens = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT token_id) FROM orderbook_snapshots"
    ).fetchone()
    levels = con.execute("SELECT COUNT(*) FROM orderbook_levels").fetchone()[0]
    signals = con.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    print(
        f"snapshots: {snaps} ({tokens} tokens) | level rows: {levels} | signals: {signals}"
    )
    for row in con.execute(
        "SELECT market_slug, outcome, outcome_source FROM markets ORDER BY window_start"
    ):
        print("market:", row)
    for row in con.execute(
        "SELECT ts, side_label, best_bid_m, best_ask_m, n_bid_levels, n_ask_levels "
        "FROM orderbook_snapshots ORDER BY snapshot_id DESC LIMIT 2"
    ):
        print("snapshot:", row)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
