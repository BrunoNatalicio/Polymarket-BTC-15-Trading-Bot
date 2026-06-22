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

    # Polymarket 15m-crypto taker fee: fee = C × r × p × (1−p), collected in
    # SHARES on a buy. At p=0.50 the fee peaks. stake $50 at 0.50 -> gross 100
    # shares; fee_usd = 100 × 0.07 × 0.5 × 0.5 = $1.75; fee_shares = 1.75/0.50
    # = 3.5; net 96.5 shares. The full $50 is still spent.
    fill = simulate_market_buy([(0.50, 1000.0)], stake_usd=50.0, fee_rate=0.07)
    check("fee_usd = C*r*p*(1-p) at 0.50", abs(fill.fee_usd - 1.75) < 1e-9)
    check("fee skimmed in shares -> net 96.5", abs(fill.filled_tokens - 96.5) < 1e-9)
    check("filled_usd unchanged by fee ($50)", abs(fill.filled_usd - 50.0) < 1e-9)
    check("vwap is the gross avg (0.50)", abs(fill.vwap - 0.50) < 1e-12)

    # Fee is symmetric around 0.50: 100 shares at 0.30 vs 0.70 cost the same.
    f30 = simulate_market_buy([(0.30, 1000.0)], stake_usd=30.0, fee_rate=0.07)
    f70 = simulate_market_buy([(0.70, 1000.0)], stake_usd=70.0, fee_rate=0.07)
    check("fee symmetric 0.30 vs 0.70", abs(f30.fee_usd - f70.fee_usd) < 1e-9)

    # Negligible at the extremes: 100 shares at 0.99 -> 100×0.07×0.99×0.01 ≈ $0.069
    f99 = simulate_market_buy([(0.99, 1000.0)], stake_usd=99.0, fee_rate=0.07)
    check("fee negligible at 0.99", f99.fee_usd < 0.08)

    # Default fee_rate=0 -> no fee, full shares (back-compat / non-fee markets).
    fill = simulate_market_buy([(0.50, 1000.0)], stake_usd=50.0)
    check("no fee by default -> 100 shares", abs(fill.filled_tokens - 100.0) < 1e-9)
    check("fee_usd zero by default", fill.fee_usd == 0.0)


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
            rows[0] == ("csv900s-900000900-UP", 900000900.0, "UP"),
        )
        check(
            "DOWN at bar close",
            rows[1] == ("csv900s-900002700-DOWN", 900002700.0, "DOWN"),
        )
        n2 = import_tradingview_csv(con, path, up_col="upX", down_col="downX")
        check("re-import is idempotent", n2 == 0)
        con.close()
    finally:
        os.unlink(path)


def test_clob_outcome():
    print("\n7. CLOB outcome resolution (recorded orderbook)")
    import backtest.db as db
    from backtest.settlement import clob_outcome

    con = db.connect(":memory:")

    def expired_book(slug, ws, winner):
        we = ws + 900
        db.upsert_market(con, slug, "Y", "N", ws, we)
        yes_win = winner == "YES"
        # Winning side: bid ~0.99 / no ask; losing side: ask ~0.01 / no bid.
        db.insert_snapshot(
            con,
            ts=we - 1,
            market_slug=slug,
            token_id="Y",
            side_label="YES",
            bids=[(0.99, 100.0)] if yes_win else [],
            asks=[] if yes_win else [(0.01, 100.0)],
        )
        db.insert_snapshot(
            con,
            ts=we - 1,
            market_slug=slug,
            token_id="N",
            side_label="NO",
            bids=[] if yes_win else [(0.99, 100.0)],
            asks=[(0.01, 100.0)] if yes_win else [],
        )
        return we

    we1 = expired_book("btc-updown-15m-1000000", 1000000, "YES")
    check(
        "YES winner detected", clob_outcome(con, "btc-updown-15m-1000000", we1) == "YES"
    )

    we2 = expired_book("btc-updown-15m-2000000", 2000000, "NO")
    check(
        "NO winner detected", clob_outcome(con, "btc-updown-15m-2000000", we2) == "NO"
    )

    # Ambiguous book (0.60/0.40) before resolution -> None (fall back to gamma/candle)
    slug3, ws3 = "btc-updown-15m-3000000", 3000000
    db.upsert_market(con, slug3, "Y", "N", ws3, ws3 + 900)
    db.insert_snapshot(
        con,
        ts=ws3 + 899,
        market_slug=slug3,
        token_id="Y",
        side_label="YES",
        bids=[(0.60, 100.0)],
        asks=[(0.62, 100.0)],
    )
    db.insert_snapshot(
        con,
        ts=ws3 + 899,
        market_slug=slug3,
        token_id="N",
        side_label="NO",
        bids=[(0.38, 100.0)],
        asks=[(0.40, 100.0)],
    )
    check("ambiguous 0.60/0.40 -> None", clob_outcome(con, slug3, ws3 + 900) is None)
    check(
        "missing books -> None",
        clob_outcome(con, "btc-updown-15m-9999999", 9999999) is None,
    )
    con.close()


def test_bot_trades():
    print("\n8. bot trade evaluation vs CLOB")
    import backtest.db as db
    from backtest.bot_trades import evaluate_bot_trades

    con = db.connect(":memory:")
    wa, wb = 4000000, 5000000
    sa, sb = f"btc-updown-15m-{wa}", f"btc-updown-15m-{wb}"
    # Market A resolves NO; market B resolves YES.
    for slug, ws, yes_win in ((sa, wa, False), (sb, wb, True)):
        db.upsert_market(con, slug, "Y", "N", ws, ws + 900)
        db.insert_snapshot(
            con,
            ts=ws + 899,
            market_slug=slug,
            token_id="Y",
            side_label="YES",
            bids=[(0.99, 100.0)] if yes_win else [],
            asks=[] if yes_win else [(0.01, 100.0)],
        )
        db.insert_snapshot(
            con,
            ts=ws + 899,
            market_slug=slug,
            token_id="N",
            side_label="NO",
            bids=[] if yes_win else [(0.99, 100.0)],
            asks=[(0.01, 100.0)] if yes_win else [],
        )

    trades = [
        # UP on market A (NO wins) -> LOSS: stake $1 -> pnl -1
        {
            "trade_label": "YES (UP)",
            "price": 0.50,
            "usd_amount": 1.0,
            "market_slug": sa,
        },
        # UP on market B (YES wins) -> WIN: $1 at 0.50 = 2 tokens, payout 2, pnl +1
        {
            "trade_label": "YES (UP)",
            "price": 0.50,
            "usd_amount": 1.0,
            "market_slug": sb,
        },
    ]
    res = evaluate_bot_trades(con, trades, fee_rate=0.0)
    check("1 win / 1 loss", res["wins"] == 1 and res["losses"] == 1)
    check("win rate 50%", abs(res["win_rate"] - 0.5) < 1e-9)
    check("pnl 0.0 (+1 -1)", abs(res["total_pnl"] - 0.0) < 1e-9)
    check("staked $2", abs(res["total_staked"] - 2.0) < 1e-9)

    # With the 15m-crypto taker fee, the WIN at 0.50 pays less (fee skimmed in
    # shares: 2 - 0.035/0.5 = 1.93 -> pnl +0.93) while the LOSS stays -1.00.
    # Net total moves from 0.00 to -0.07 (the fee on the winning leg).
    res_fee = evaluate_bot_trades(con, trades, fee_rate=0.07)
    check("with fee: total pnl -0.07", abs(res_fee["total_pnl"] - (-0.07)) < 1e-9)
    con.close()


def test_z_mom_ingest():
    print("\n9. z_mom ingest (closes from signals + CSV, Phase 2)")
    import json
    import tempfile

    import backtest.db as db
    from backtest.ingest import (
        load_bar_closes_csv,
        load_closes_from_signals,
        z_mom_by_window,
    )
    from tv_market_select import z_momentum

    con = db.connect(":memory:")
    con.executescript(db.SCHEMA)

    # 30 signals at consecutive 15m boundaries, each carrying preco_fechamento.
    w0 = 1_000_000_800  # multiple of 900
    closes = [100.0 * (1.001**i) for i in range(30)]
    for i, c in enumerate(closes):
        ts = w0 + i * 900 + 0.5  # fires just after the boundary
        db.insert_signal(
            con,
            f"sig-{i}",
            float(ts),
            "UP",
            raw_json=json.dumps({"preco_fechamento": str(c)}),
        )

    pairs = load_closes_from_signals(con, "tradingview", 0.0, 2_000_000_000.0)
    check("one close per window, sorted", len(pairs) == 30 and pairs[0][0] == w0)
    check("close ts == window_start (boundary)", pairs[5][0] == w0 + 5 * 900)
    check("close value parsed from raw_json", abs(pairs[0][1] - closes[0]) < 1e-9)

    zmap = z_mom_by_window(pairs)
    # No-lookahead: the entry for window W uses only closes up to and incl. W.
    w_last = pairs[-1][0]
    expected = z_momentum([c for _, c in pairs])
    check(
        "z_mom at last window matches full series", abs(zmap[w_last] - expected) < 1e-9
    )
    check("early window (too little history) -> 0.0", zmap[pairs[3][0]] == 0.0)
    check("uptrend -> positive z at last window", zmap[w_last] > 0.0)
    con.close()

    # CSV loader: bar-close ts = time + bar_seconds.
    with tempfile.NamedTemporaryFile(
        "w", suffix=".csv", delete=False, newline=""
    ) as fh:
        fh.write("time,open,close\n1000000800,100,101\n1000001700,101,102\n")
        csv_path = fh.name
    csv_pairs = load_bar_closes_csv(csv_path, bar_seconds=900)
    os.unlink(csv_path)
    check(
        "CSV close ts = time+900, value=close",
        csv_pairs[0] == (1_000_001_700, 101.0)
        and csv_pairs[1] == (1_000_002_600, 102.0),
    )


def test_fusion_replay():
    print("\n10. fusion replay (late-window favorite-follower, L0)")
    import backtest.db as db
    from backtest.fusion_replay import run_fusion_replay

    con = db.connect(":memory:")
    ws = [900_000_000 + i * 900 for i in range(4)]
    # m0: favorite YES (mid 0.62) > 0.60 -> UP; outcome YES -> WIN
    # m1: favorite NO  (YES mid 0.30) < 0.40 -> DOWN (buy NO); outcome NO -> WIN
    # m2: deadband (YES mid 0.50) -> skip
    # m3: UP favorite but NO snapshot in the entry band -> unfilled_no_data
    for w in ws:
        db.upsert_market(con, f"btc-updown-15m-{w}", f"Y{w}", f"N{w}", w, w + 900)
    db.set_market_outcome(con, f"btc-updown-15m-{ws[0]}", "YES", "clob")
    db.set_market_outcome(con, f"btc-updown-15m-{ws[1]}", "NO", "clob")
    db.set_market_outcome(con, f"btc-updown-15m-{ws[2]}", "YES", "clob")
    db.set_market_outcome(con, f"btc-updown-15m-{ws[3]}", "YES", "clob")

    def snap(w, tok, side, bid, ask, depth=500.0, at=810):
        db.insert_snapshot(
            con,
            ts=w + at,
            market_slug=f"btc-updown-15m-{w}",
            token_id=tok,
            side_label=side,
            bids=[(bid, depth)],
            asks=[(ask, depth)],
        )

    # m0: YES mid 0.62 -> UP, buy YES at 0.63
    snap(ws[0], f"Y{ws[0]}", "YES", 0.61, 0.63)
    # m1: YES mid 0.30 -> DOWN, buy NO at 0.71 (NO mid 0.70)
    snap(ws[1], f"Y{ws[1]}", "YES", 0.29, 0.31)
    snap(ws[1], f"N{ws[1]}", "NO", 0.69, 0.71)
    # m2: deadband 0.50
    snap(ws[2], f"Y{ws[2]}", "YES", 0.49, 0.51)
    # m3: YES snapshot OUTSIDE the entry band (at 100s) -> no_data
    snap(ws[3], f"Y{ws[3]}", "YES", 0.61, 0.63, at=100)

    rep = run_fusion_replay(
        con, start_ts=ws[0], end_ts=ws[3] + 900, stake_usd=3.0, fee_rate=0.0
    )
    s = rep.summary()
    check("4 markets scanned", s["markets_total"] == 4)
    check("1 deadband skip (m2)", s["deadband_skip"] == 1)
    check("1 no-data (m3 out of band)", s["unfilled_no_data"] == 1)
    check("2 fills (m0 UP, m1 DOWN)", s["fills"] == 2)
    check("both settled & won", s["settled"] == 2 and s["wins"] == 2)

    by_dir = {str(t["direction"]): t for t in rep.settled}
    check("m0 direction UP (bought YES)", "UP" in by_dir)
    check("m1 direction DOWN (bought NO)", "DOWN" in by_dir)
    # UP: $3 at 0.63 -> 4.7619 tokens, YES wins -> payout 4.7619, pnl +1.7619
    up = by_dir["UP"]
    check("UP pnl = 3/0.63 - 3", abs(up["pnl"] - (3.0 / 0.63 - 3.0)) < 1e-9)
    # DOWN: $3 at 0.71 -> 4.2254 tokens, NO wins -> pnl +1.2254
    dn = by_dir["DOWN"]
    check("DOWN pnl = 3/0.71 - 3", abs(dn["pnl"] - (3.0 / 0.71 - 3.0)) < 1e-9)

    # With the taker fee, the same wins pay a little less (fee skimmed in shares).
    rep_fee = run_fusion_replay(
        con, start_ts=ws[0], end_ts=ws[3] + 900, stake_usd=3.0, fee_rate=0.07
    )
    check(
        "fee reduces total PnL",
        rep_fee.summary()["total_pnl"] < s["total_pnl"],
    )

    # Deadband knobs: widen so 0.50 is no longer skipped (trend_up=0.49 -> m2 trades UP).
    rep_wide = run_fusion_replay(
        con,
        start_ts=ws[2],
        end_ts=ws[2] + 900,
        stake_usd=3.0,
        fee_rate=0.0,
        trend_up=0.49,
        trend_down=0.40,
    )
    check("widening trend_up trades the 0.50 market", rep_wide.summary()["fills"] == 1)
    con.close()


def test_calibration():
    print("\n11. Platt calibrator + EV gate (L1)")
    from backtest.calibration import ev_gate, fit_platt, platt_prob

    # Separable-ish training data: favorites above 0.60 win, below lose.
    samples = [(x / 100.0, 1.0 if x > 60 else 0.0) for x in range(40, 96, 5)]
    a, b = fit_platt(samples)
    check("positive slope (higher price -> higher P_win)", a > 0)
    check(
        "calibrated prob monotonic (0.80 > 0.50)",
        platt_prob(a, b, 0.80) > platt_prob(a, b, 0.50),
    )
    check("prob stays in (0,1)", 0.0 < platt_prob(a, b, 0.80) < 1.0)

    # Degenerate: all wins -> ridge keeps coefficients finite, no overflow/NaN.
    a2, b2 = fit_platt([(0.7, 1.0), (0.8, 1.0), (0.9, 1.0)])
    p2 = platt_prob(a2, b2, 0.8)
    check("all-wins data: finite prob", 0.0 < p2 < 1.0)
    check("empty data -> (0,0)", fit_platt([]) == (0.0, 0.0))

    # EV gate with a hand-set steep sigmoid (deterministic): trade only when the
    # calibrated win-prob beats the fee-adjusted breakeven.
    gate = ev_gate(20.0, -13.0, fee_rate=0.07)
    # p=0.80: P_cal=sigmoid(3)=0.953 > breakeven(0.80)=0.811 -> TRADE
    check("strong favorite passes the EV gate", gate("UP", 0.80) is True)
    # p=0.55: P_cal=sigmoid(-2)=0.119 < breakeven(0.55)=0.568 -> skip
    check("thin favorite is gated out", gate("DOWN", 0.55) is False)


def test_cpcv():
    print("\n12. CPCV out-of-sample validation of the L1 gate")
    from backtest.cpcv import (
        Fill,
        cpcv_splits,
        purge_embargo,
        run_cpcv,
        score_path,
    )

    # cpcv_splits: C(4,1)=4 paths, each group tested once; train+test partition.
    splits = cpcv_splits(n_items=12, n_groups=4, k_test=1)
    check("C(4,1) = 4 splits", len(splits) == 4)
    check(
        "train+test cover all, disjoint",
        all(sorted(tr + te) == list(range(12)) for tr, te in splits),
    )

    # purge_embargo: a train fill within embargo_s of a test fill is dropped.
    fills_pe = [Fill(ts=float(i * 900), p_side=0.6, won=1.0, pnl=1.0) for i in range(6)]
    kept = purge_embargo(fills_pe, train_idx=[0, 1, 2], test_idx=[3], embargo_s=900)
    check("embargo drops the neighbor (idx 2 at 1 window)", 2 not in kept)
    check("embargo keeps the far ones (0,1)", kept == [0, 1])

    # score_path: a clearly -EV thin favorite in test should be gated OUT, so L1
    # avoids its loss -> delta > 0. Train must teach the calibrator that low
    # p_side loses. Build separable train: p<0.6 loses, p>=0.7 wins.
    train_fills = (
        [Fill(0.0, 0.50, 0.0, -3.0) for _ in range(40)]  # thin favorites lose
        + [Fill(0.0, 0.85, 1.0, +0.5) for _ in range(40)]  # strong favorites win
    )
    test_fills = [
        Fill(0.0, 0.50, 0.0, -3.0),  # -EV: gate should drop -> avoids -3
        Fill(0.0, 0.85, 1.0, +0.5),  # +EV: gate keeps -> +0.5
    ]
    allf = train_fills + test_fills
    tr = list(range(len(train_fills)))
    te = [len(train_fills), len(train_fills) + 1]
    sp = score_path(allf, tr, te, fee_rate=0.07)
    check("gate drops the thin -EV test fill", sp["gated_in"] == 1)
    check("L1 beats L0 when a -EV fill is avoided", sp["delta"] > 0)

    # run_cpcv verdict: with few losses, minority guard fires INSUFFICIENT.
    few_loss = [Fill(float(i * 900), 0.8, 1.0, 0.5) for i in range(50)] + [
        Fill(float(50 * 900), 0.5, 0.0, -3.0)
    ]
    res = run_cpcv(few_loss, n_groups=4, k_test=1, min_minority=100)
    check("scarce losses -> INSUFFICIENT verdict", res["verdict"] == "INSUFFICIENT")
    check("reports total losses", res["total_losses"] == 1)

    # With enough minority and a real edge, the verdict can be ADDS EDGE.
    import random

    rng = random.Random(0)
    many: list[Fill] = []
    for i in range(400):
        p = rng.choice([0.50, 0.85])
        won = 1.0 if (p > 0.6 or rng.random() < 0.45) else 0.0
        pnl = (0.5 if won else -3.0) if p > 0.6 else (2.0 if won else -3.0)
        many.append(Fill(float(i * 900), p, won, pnl))
    res2 = run_cpcv(many, n_groups=5, k_test=1, min_minority=50)
    check("sufficient minority -> not INSUFFICIENT", res2["verdict"] != "INSUFFICIENT")
    check("aggregate has all paths", res2["n_paths"] == 5)


def test_candidate_window_starts():
    print("\n13. phase-robust 4h window discovery")
    from backtest.recorder import candidate_window_starts

    win_s = 14400  # 4h => 4 hour-phase candidates
    # "now" 1h into the epoch-aligned window starting at 1782144000 (phase 0).
    now = 1782144000 + 3600
    cands = candidate_window_starts(win_s, now)
    check("4h => 4 hour-aligned candidates", len(cands) == 4)
    check("candidates are most-recent-first", cands == sorted(cands, reverse=True))
    check("candidates are hour-aligned", all(c % 3600 == 0 for c in cands))
    check(
        "every candidate's window covers now", all(c <= now < c + win_s for c in cands)
    )
    check("epoch-aligned (phase 0) start is a candidate", 1782144000 in cands)

    # Phase-shifted window (start % 14400 == 3600): still discoverable because we
    # enumerate hour phases, not just the epoch-aligned one.
    shifted_start = 1782144000 + 3600  # phase 3600
    now2 = shifted_start + 7200  # 2h into the shifted window
    cands2 = candidate_window_starts(win_s, now2)
    check("phase-shifted start is among candidates", shifted_start in cands2)


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
    test_clob_outcome()
    test_bot_trades()
    test_z_mom_ingest()
    test_fusion_replay()
    test_calibration()
    test_cpcv()
    test_candidate_window_starts()

    print("\n" + "=" * 60)
    print(f"RESULT: {PASSED} passed, {FAILED} failed")
    print("=" * 60)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
