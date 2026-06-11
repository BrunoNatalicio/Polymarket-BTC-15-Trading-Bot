"""Standalone tests for the backtest replay engine.

Run with: uv run python backtest/test_backtest.py
Fully offline: in-memory SQLite, no network, no Redis.
"""

import os
import sys

# Allow running directly as `uv run python backtest/test_backtest.py`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASSED = 0
FAILED = 0


def check(name: str, condition: bool) -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  [PASS] {name}")
    else:
        FAILED += 1
        print(f"  [FAIL] {name}")


def test_schema_and_roundtrip():
    print("\n1. schema + price_m round-trip")
    import backtest.db as db

    con = db.connect(":memory:")
    con.executescript(db.SCHEMA)  # idempotent: second run must not raise
    check("DDL idempotent", True)

    db.upsert_market(
        con, "btc-updown-15m-1000000800", "YTOK", "NTOK", 1000000800, 1000001700
    )
    db.upsert_market(
        con, "btc-updown-15m-1000000800", "YTOK2", "NTOK", 1000000800, 1000001700
    )
    row = con.execute(
        "SELECT yes_token_id FROM markets WHERE market_slug='btc-updown-15m-1000000800'"
    ).fetchone()
    check("market upsert overwrites tokens", row[0] == "YTOK2")

    sid = db.insert_snapshot(
        con,
        ts=1000000810.0,
        market_slug="btc-updown-15m-1000000800",
        token_id="YTOK2",
        side_label="YES",
        bids=[(0.614, 100.0), (0.613, 50.0)],
        asks=[(0.615, 80.0), (0.617, 40.0)],
    )
    levels = con.execute(
        "SELECT side, level, price_m, size FROM orderbook_levels "
        "WHERE snapshot_id=? ORDER BY side, level",
        (sid,),
    ).fetchall()
    check("levels persisted", len(levels) == 4)
    check(
        "price_m exact (0.615 -> 615 -> 0.615)",
        levels[0] == ("ask", 0, 615, 80.0) and db.m_to_price(615) == 0.615,
    )

    new = db.insert_signal(con, "sig-1", 1000000805.0, "UP")
    dup = db.insert_signal(con, "sig-1", 1000000805.0, "UP")
    check("signal insert idempotent", new is True and dup is False)
    con.close()


def test_merge_asof_alignment():
    print("\n2. merge_asof alignment (forward, tolerance, token isolation)")
    import pandas as pd

    import backtest.ingest as ingest

    signals = pd.DataFrame(
        {
            "signal_id": ["s1", "s2", "s3"],
            "ts": [100.0, 200.0, 300.0],
            "direction": ["UP", "UP", "DOWN"],
            "source": ["tradingview"] * 3,
            "token_id": ["YTOK", "YTOK", "NTOK"],
            "market_slug": ["m"] * 3,
            "window_start": [0] * 3,
            "outcome": [None] * 3,
        }
    )
    snaps = pd.DataFrame(
        {
            "snapshot_id": [1, 2, 3, 4],
            "ts": [99.0, 101.0, 250.0, 301.0],
            "token_id": ["YTOK", "YTOK", "NTOK", "NTOK"],
            "best_bid_m": [610, 611, 612, 613],
            "best_ask_m": [615, 616, 617, 618],
        }
    )
    aligned = ingest.align_signals_to_snapshots(signals, snaps, tolerance_s=10.0)
    by_id = {r["signal_id"]: r for r in aligned.to_dict("records")}

    check(
        "forward: signal@100 matches snap@101, not snap@99",
        int(by_id["s1"]["snapshot_id"]) == 2,
    )
    check(
        "tolerance: signal@200 has no YTOK snap within 10s -> NaN",
        pd.isna(by_id["s2"]["snapshot_id"]),
    )
    check(
        "token isolation: DOWN signal@300 matches NTOK snap@301 (id 4)",
        int(by_id["s3"]["snapshot_id"]) == 4,
    )
    check(
        "snapshot age computed",
        abs(float(by_id["s1"]["snapshot_age_s"]) - 1.0) < 1e-9,
    )


def test_depth_walk():
    print("\n3. depth-walking fill simulator")
    from backtest.matching import simulate_market_buy

    # Stake smaller than level 1: VWAP == best ask, zero slippage
    fill = simulate_market_buy([(0.60, 1000.0)], stake_usd=50.0)
    check("stake < L1: vwap == best ask", abs(fill.vwap - 0.60) < 1e-12)
    check("stake < L1: zero slippage", fill.slippage_bps == 0.0)
    check("stake < L1: not exhausted", fill.exhausted is False)
    check("tokens = 50/0.60", abs(fill.filled_tokens - 50.0 / 0.60) < 1e-9)

    # Stake spanning exactly 3 levels: hand-computed VWAP
    # L0: 0.60 x 50 tokens = $30 ; L1: 0.62 x 50 = $31 ; L2: 0.65 x 20 = $13
    # stake $74 -> consumes L0 ($30), L1 ($31), and $13 of L2 (20 tokens)
    asks = [(0.60, 50.0), (0.62, 50.0), (0.65, 20.0)]
    fill = simulate_market_buy(asks, stake_usd=74.0)
    expected_tokens = 50.0 + 50.0 + 13.0 / 0.65
    check("3 levels consumed", fill.levels_consumed == 3)
    check("tokens hand-computed", abs(fill.filled_tokens - expected_tokens) < 1e-9)
    check("vwap = 74/tokens", abs(fill.vwap - 74.0 / expected_tokens) < 1e-12)
    check("slippage positive", fill.slippage > 0)
    check("fully filled: not exhausted", fill.exhausted is False)

    # Stake > total depth: partial fill, exhausted
    fill = simulate_market_buy([(0.60, 10.0)], stake_usd=50.0)
    check("thin book: exhausted", fill.exhausted is True)
    check("thin book: partial fill $6", abs(fill.filled_usd - 6.0) < 1e-9)

    # Empty book: zero fill
    fill = simulate_market_buy([], stake_usd=50.0)
    check("empty book: zero fill", fill.filled_tokens == 0.0 and fill.exhausted)

    # Fees reduce token budget
    fill = simulate_market_buy([(0.50, 1000.0)], stake_usd=50.0, fee_rate=0.02)
    check(
        "fee reduces tokens (50/1.02/0.5)",
        abs(fill.filled_tokens - 50.0 / 1.02 / 0.5) < 1e-9,
    )
    check("filled_usd includes fee", abs(fill.filled_usd - 50.0) < 1e-9)


def test_settlement():
    print("\n4. settlement")
    from backtest.settlement import settle_fill

    up_win = settle_fill("UP", filled_usd=50.0, filled_tokens=80.0, outcome="YES")
    check("UP wins on YES: payout 80", up_win["payout"] == 80.0)
    check("UP wins on YES: pnl +30", abs(up_win["pnl"] - 30.0) < 1e-9)

    up_lose = settle_fill("UP", filled_usd=50.0, filled_tokens=80.0, outcome="NO")
    check("UP loses on NO: pnl -50", abs(up_lose["pnl"] + 50.0) < 1e-9)

    down_win = settle_fill("DOWN", filled_usd=40.0, filled_tokens=100.0, outcome="NO")
    check("DOWN wins on NO: pnl +60", abs(down_win["pnl"] - 60.0) < 1e-9)


def test_end_to_end_replay():
    print("\n5. end-to-end replay (two markets, known PnL)")
    import backtest.db as db
    from backtest.engine import run_replay

    con = db.connect(":memory:")
    w1, w2 = 900_000_000, 900_000_900  # two adjacent 15-min windows
    db.upsert_market(con, f"btc-updown-15m-{w1}", "Y1", "N1", w1, w1 + 900)
    db.upsert_market(con, f"btc-updown-15m-{w2}", "Y2", "N2", w2, w2 + 900)
    db.set_market_outcome(con, f"btc-updown-15m-{w1}", "YES", "gamma")
    db.set_market_outcome(con, f"btc-updown-15m-{w2}", "NO", "candle")

    # Market 1: UP signal, book has plenty of depth at 0.50
    db.insert_signal(con, "e2e-1", w1 + 100.0, "UP")
    db.insert_snapshot(
        con,
        ts=w1 + 101.0,
        market_slug=f"btc-updown-15m-{w1}",
        token_id="Y1",
        side_label="YES",
        bids=[(0.49, 500.0)],
        asks=[(0.50, 500.0)],
    )
    # Market 2: UP signal but outcome NO -> full loss of the $50
    db.insert_signal(con, "e2e-2", w2 + 100.0, "UP")
    db.insert_snapshot(
        con,
        ts=w2 + 102.0,
        market_slug=f"btc-updown-15m-{w2}",
        token_id="Y2",
        side_label="YES",
        bids=[(0.59, 500.0)],
        asks=[(0.60, 500.0)],
    )
    # A signal with no market recorded at all
    db.insert_signal(con, "e2e-3", w2 + 2000.0, "DOWN")

    report = run_replay(
        con, start_ts=w1, end_ts=w2 + 3000, stake_usd=50.0, tolerance_s=10.0
    )
    s = report.summary()
    # Trade 1: 100 tokens at 0.50, YES wins -> payout 100, pnl +50
    # Trade 2: 83.33 tokens at 0.60, NO wins -> payout 0, pnl -50
    check("two fills", s["fills"] == 2)
    check("both settled", s["settled"] == 2)
    check("one unmatched market", s["unfilled_no_market"] == 1)
    check("win rate 50%", abs(s["win_rate"] - 0.5) < 1e-9)
    check("total pnl 0.0", abs(s["total_pnl"] - 0.0) < 1e-9)
    check("equity curve [+50, 0]", report.equity_curve() == [50.0, 0.0])
    con.close()


def test_csv_import():
    print("\n6. TradingView CSV signal import")
    import os
    import tempfile

    import backtest.db as db
    from backtest.ingest import import_tradingview_csv

    csv_content = (
        "time,open,high,low,close,Volume,upX,downX\n"
        "900000000,100,101,99,100.5,10,1,0\n"
        "900000900,100.5,102,100,101,12,0,0\n"
        "900001800,101,101,98,99,15,0,1\n"
    )
    fd, path = tempfile.mkstemp(suffix=".csv")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(csv_content)
        con = db.connect(":memory:")
        n = import_tradingview_csv(con, path, up_col="upX", down_col="downX")
        check("two signals imported", n == 2)
        rows = con.execute(
            "SELECT signal_id, ts, direction FROM signals ORDER BY ts"
        ).fetchall()
        check(
            "UP at bar close (open+900)",
            rows[0] == ("csv-900000900-UP", 900000900.0, "UP"),
        )
        check(
            "DOWN at bar close",
            rows[1] == ("csv-900002700-DOWN", 900002700.0, "DOWN"),
        )
        n2 = import_tradingview_csv(con, path, up_col="upX", down_col="downX")
        check("re-import is idempotent", n2 == 0)
        con.close()
    finally:
        os.unlink(path)


def main() -> int:
    print("=" * 60)
    print("BACKTEST REPLAY ENGINE - TESTS")
    print("=" * 60)

    test_schema_and_roundtrip()
    test_merge_asof_alignment()
    test_depth_walk()
    test_settlement()
    test_end_to_end_replay()
    test_csv_import()

    print("\n" + "=" * 60)
    print(f"RESULT: {PASSED} passed, {FAILED} failed")
    print("=" * 60)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
