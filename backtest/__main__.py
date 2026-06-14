"""CLI for the backtest replay engine.

    uv run python -m backtest record
    uv run python -m backtest settle
    uv run python -m backtest replay --start 2026-06-01 --end 2026-06-12 --stake 50
    uv run python -m backtest import-signals --csv export.csv \
        --up-col sinalUP_export --down-col sinalDOWN_export
"""

import argparse
import sys
import time
from datetime import UTC, datetime
from typing import Any

import backtest.db as db


def _parse_when(value: str) -> float:
    """Accept 'YYYY-MM-DD', full ISO datetimes, or raw unix seconds."""
    try:
        return float(value)
    except ValueError:
        pass
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


def _add_entry_filter_args(p: argparse.ArgumentParser) -> None:
    """Shared hybrid gate + conviction-sizing knobs (defaults = flat baseline)."""
    p.add_argument(
        "--min-entry-prob",
        type=float,
        default=0.0,
        help="gate: skip a signal whose bought-side book prob < this (0 = off)",
    )
    p.add_argument(
        "--size-full-prob",
        type=float,
        default=1.0,
        help="conviction sizing: full stake at/above this book prob",
    )
    p.add_argument(
        "--size-min-frac",
        type=float,
        default=1.0,
        help="conviction sizing: min stake fraction at the gate (1.0 = flat)",
    )


def cmd_record(_args: argparse.Namespace) -> int:
    from backtest.recorder import main as recorder_main

    return recorder_main()


def cmd_settle(_args: argparse.Namespace) -> int:
    from backtest.settlement import settle_backfill

    con = db.connect()
    try:
        resolved = settle_backfill(con)
        print(f"Resolved {resolved} market(s)")
    finally:
        con.close()
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    from backtest.engine import run_replay, trades_dataframe

    start = _parse_when(args.start) if args.start else 0.0
    end = _parse_when(args.end) if args.end else time.time()
    con = db.connect()
    try:
        report = run_replay(
            con,
            start_ts=start,
            end_ts=end,
            stake_usd=args.stake,
            tolerance_s=args.tolerance,
            fee_rate=args.fee_rate,
            fill_policy=args.fill_policy,
            series=args.series,
            source_like=args.signal_source,
            min_entry_prob=args.min_entry_prob,
            size_full_prob=args.size_full_prob,
            size_min_frac=args.size_min_frac,
        )
    finally:
        con.close()

    s = report.summary()
    print("=" * 60)
    print("BACKTEST REPLAY REPORT")
    print("=" * 60)
    print(
        f"Window     : {datetime.fromtimestamp(start, tz=UTC):%Y-%m-%d %H:%M} -> "
        f"{datetime.fromtimestamp(end, tz=UTC):%Y-%m-%d %H:%M} UTC"
    )
    print(
        f"Stake      : ${args.stake:.2f} | series={args.series} "
        f"| policy={args.fill_policy} | tolerance={args.tolerance}s "
        f"| fee={args.fee_rate:.4f}"
    )
    print("-" * 60)
    print(f"Signals    : {s['signals_total']}")
    print(f"Fills      : {s['fills']}  (exhausted books: {s['exhausted_books']})")
    print(
        f"Unfilled   : {s['unfilled_no_data']} no-data, "
        f"{s['unfilled_no_market']} no-market, "
        f"{s['unfilled_no_liquidity']} no-liquidity, "
        f"{s['unfilled_min_book_prob']} below-book-prob"
    )
    print(f"Settled    : {s['settled']}  (pending outcome: {s['unsettled']})")
    if s["settled"]:
        print(f"Win rate   : {s['win_rate']:.1%}  ({s['wins']}/{s['settled']})")
        print(f"Total PnL  : ${s['total_pnl']:+.2f} on ${s['total_staked']:.2f} staked")
    print(f"Avg slip   : {s['avg_slippage_bps']:.1f} bps")
    print("=" * 60)

    if args.out_csv and report.trades:
        trades_dataframe(report).to_csv(args.out_csv, index=False)
        print(f"Trades written to {args.out_csv}")
    return 0


def cmd_tune(args: argparse.Namespace) -> int:
    """Sweep gate+sizing configs and pick argmax(total PnL).

    The baseline (floor 0, frac 1.0 = flat stake, no gate) is always in the
    grid, so the recommended config's PnL is >= baseline by construction — the
    "no downgrade" guarantee is mechanical, not a promise.
    """
    from backtest.engine import run_replay

    start = _parse_when(args.start) if args.start else 0.0
    end = _parse_when(args.end) if args.end else time.time()
    floors = [0.0, 0.40, 0.42, 0.44, 0.45, 0.48, 0.50]
    fulls = [0.55, 0.60]
    fracs = [1.0, 0.5, 0.33]

    configs: list[tuple[float, float, float]] = [(0.0, 1.0, 1.0)]  # baseline first
    for fl in floors:
        for fu in fulls:
            for fr in fracs:
                if fl == 0.0 and fr == 1.0:
                    continue  # equivalent to baseline
                configs.append((fl, fu, fr))

    con = db.connect()
    results: list[dict[str, Any]] = []
    try:
        for fl, fu, fr in configs:
            rep = run_replay(
                con,
                start_ts=start,
                end_ts=end,
                stake_usd=args.stake,
                fee_rate=args.fee_rate,
                series=args.series,
                source_like=args.signal_source,
                min_entry_prob=fl,
                size_full_prob=fu,
                size_min_frac=fr,
            )
            s = rep.summary()
            n = s["settled"]
            results.append(
                {
                    "floor": fl,
                    "full": fu,
                    "frac": fr,
                    "settled": n,
                    "win_rate": s["win_rate"] or 0.0,
                    "pnl": s["total_pnl"],
                    "ev": (s["total_pnl"] / n) if n else 0.0,
                    "skipped": s["unfilled_min_book_prob"],
                }
            )
    finally:
        con.close()

    baseline = results[0]
    best = max(results, key=lambda r: r["pnl"])
    results.sort(key=lambda r: r["pnl"], reverse=True)

    line = "=" * 80
    print(line)
    print("ENTRY-FILTER TUNE — critério: PnL total máximo (baseline incluído no grid)")
    print(line)
    print(
        f"{'floor':>6} {'full':>5} {'frac':>5} {'settled':>8} {'win%':>6} "
        f"{'PnL':>9} {'EV/trade':>9} {'skip':>5}"
    )
    for r in results:
        tag = "  <- BEST PnL" if r is best else ""
        if r["floor"] == 0.0 and r["frac"] == 1.0:
            tag += "  (baseline)"
        print(
            f"{r['floor']:>6.2f} {r['full']:>5.2f} {r['frac']:>5.2f} "
            f"{r['settled']:>8} {r['win_rate'] * 100:>5.0f}% {r['pnl']:>+9.2f} "
            f"{r['ev']:>+9.3f} {r['skipped']:>5}{tag}"
        )
    print("-" * 80)
    delta = best["pnl"] - baseline["pnl"]
    verdict = "NO DOWNGRADE" if delta >= -1e-9 else "WARNING: DOWNGRADE"
    print(
        f"baseline PnL ${baseline['pnl']:+.2f} | best PnL ${best['pnl']:+.2f} | "
        f"delta ${delta:+.2f}  -> {verdict}"
    )
    print(
        f"defaults sugeridos -> TV_MIN_BOOK_PROB={best['floor']} "
        f"TV_SIZE_FULL_PROB={best['full']} TV_SIZE_MIN_FRAC={best['frac']}"
    )
    print(line)
    return 0


def cmd_import_signals(args: argparse.Namespace) -> int:
    from backtest.ingest import import_tradingview_csv

    con = db.connect()
    try:
        inserted = import_tradingview_csv(
            con,
            csv_path=args.csv,
            up_col=args.up_col,
            down_col=args.down_col,
            bar_seconds=args.bar_seconds,
        )
        print(f"Imported {inserted} new signal(s) from {args.csv}")
    finally:
        con.close()
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from backtest.bot_trades import (
        conversion_stats,
        evaluate_bot_trades,
        load_bot_trades,
    )
    from backtest.engine import run_replay
    from backtest.settlement import settle_backfill

    start = _parse_when(args.start) if args.start else 0.0
    end = _parse_when(args.end) if args.end else time.time()
    source = args.signal_source or "tradingview"
    con = db.connect()
    try:
        settle_backfill(con)
        report = run_replay(
            con,
            start_ts=start,
            end_ts=end,
            stake_usd=args.stake,
            fee_rate=args.fee_rate,
            series=args.series,
            source_like=source,
            min_entry_prob=args.min_entry_prob,
            size_full_prob=args.size_full_prob,
            size_min_frac=args.size_min_frac,
        )
        trades = load_bot_trades(args.bot_trades)
        bot = evaluate_bot_trades(con, trades, fee_rate=args.fee_rate)
        conv = conversion_stats(con, start, end, source_like=source, trades=trades)
    finally:
        con.close()

    s = report.summary()
    # Per-direction breakdown for the strategy view (summary() is aggregate).
    strat: dict[str, list[int]] = {"UP": [0, 0], "DOWN": [0, 0]}  # [wins, settled]
    for t in report.settled:
        d = str(t["direction"])
        if d in strat:
            strat[d][1] += 1
            strat[d][0] += 1 if t["won"] else 0

    def pct(w: int, n: int) -> str:
        return f"{w / n:.0%}" if n else "n/a"

    line = "=" * 70
    print(line)
    print("FONTE DA VERDADE — SINAIS vs BOT (resolvido via CLOB)")
    print(line)
    print(
        f"Período : {datetime.fromtimestamp(start, tz=UTC):%Y-%m-%d %H:%M} -> "
        f"{datetime.fromtimestamp(end, tz=UTC):%Y-%m-%d %H:%M} UTC"
    )
    print(
        f"Série   : {args.series} | fonte={source} | stake=${args.stake:.2f} "
        f"| fee={args.fee_rate:.2f} (taker, em shares)"
    )
    print("-" * 70)
    print("[ESTRATÉGIA] sinal -> janela N+1 -> CLOB")
    print(
        f"  Sinais recebidos : {s['signals_total']}  "
        f"(sem mercado: {s['unfilled_no_market']}, sem book: {s['unfilled_no_data']}, "
        f"sem liquidez: {s['unfilled_no_liquidity']})"
    )
    if s["settled"]:
        print(
            f"  Resolvidos       : {s['settled']} -> {s['wins']} WIN / "
            f"{s['settled'] - s['wins']} LOSS ({s['win_rate']:.0%})"
        )
        for d in ("UP", "DOWN"):
            w, n = strat[d]
            print(f"    {d:4}: {n} sinais | {w} WIN | {pct(w, n)}")
        print(
            f"  PnL              : ${s['total_pnl']:+.2f} sobre "
            f"${s['total_staked']:.2f} | slip {s['avg_slippage_bps']:.0f} bps"
        )
    else:
        print("  (nenhum sinal resolvido na janela)")
    print("-" * 70)
    print("[BOT] trades realmente executados")
    print(
        f"  Convertidos      : {conv['converted']} de {conv['received']} sinais "
        f"(dropados: {len(conv['dropped'])})"
    )
    if bot["settled"]:
        print(
            f"  Resolvidos       : {bot['settled']} -> {bot['wins']} WIN / "
            f"{bot['losses']} LOSS ({bot['win_rate']:.0%})  "
            f"(sem CLOB: {bot['unresolved']})"
        )
        print(
            f"  PnL              : ${bot['total_pnl']:+.2f} sobre "
            f"${bot['total_staked']:.2f}"
        )
    else:
        print(f"  (nenhum trade resolvido; {bot['unresolved']} sem CLOB)")
    if conv["dropped"]:
        print("-" * 70)
        print("[GAP] sinais recebidos que o bot NÃO negociou:")
        for d in conv["dropped"]:
            when = datetime.fromtimestamp(float(d["ts"]), tz=UTC)
            print(f"    {when:%Y-%m-%d %H:%M:%S} UTC  {d['direction']}")
    print(line)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backtest")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("record", help="run the orderbook/signal recorder daemon")
    sub.add_parser("settle", help="backfill outcomes for expired markets")

    p_replay = sub.add_parser("replay", help="replay signals against recorded books")
    p_replay.add_argument("--start", help="ISO date/datetime or unix seconds")
    p_replay.add_argument("--end", help="ISO date/datetime or unix seconds")
    p_replay.add_argument("--stake", type=float, default=50.0)
    p_replay.add_argument(
        "--series",
        choices=["15m", "5m"],
        default="15m",
        help="Polymarket up/down series to replay against",
    )
    p_replay.add_argument("--tolerance", type=float, default=10.0)
    p_replay.add_argument(
        "--fee-rate",
        type=float,
        default=0.07,
        help="taker fee rate; 15m/5m crypto = 0.07 (fee = C*r*p*(1-p), in shares). "
        "Pass 0 for non-fee markets.",
    )
    p_replay.add_argument(
        "--signal-source",
        help="SQL LIKE filter on signals.source (e.g. tradingview, "
        "tradingview_csv_300s) — replay one signal stream at a time",
    )
    p_replay.add_argument(
        "--fill-policy", choices=["partial", "all_or_nothing"], default="partial"
    )
    p_replay.add_argument("--out-csv", help="optional path to dump per-trade rows")
    _add_entry_filter_args(p_replay)

    p_imp = sub.add_parser(
        "import-signals", help="import signals from a TradingView CSV export"
    )
    p_imp.add_argument("--csv", required=True)
    p_imp.add_argument("--up-col", required=True)
    p_imp.add_argument("--down-col", required=True)
    p_imp.add_argument("--bar-seconds", type=int, default=900)

    p_report = sub.add_parser(
        "report", help="hit-rate + PnL: sinais (estratégia) vs bot, resolvido via CLOB"
    )
    p_report.add_argument("--start", help="ISO date/datetime or unix seconds")
    p_report.add_argument("--end", help="ISO date/datetime or unix seconds")
    p_report.add_argument("--series", choices=["15m", "5m"], default="15m")
    p_report.add_argument(
        "--signal-source",
        default="tradingview",
        help="SQL LIKE filter on signals.source (default: live webhook stream)",
    )
    p_report.add_argument("--stake", type=float, default=1.0)
    p_report.add_argument(
        "--fee-rate",
        type=float,
        default=0.07,
        help="taker fee rate; 15m/5m crypto = 0.07 (fee = C*r*p*(1-p), in shares). "
        "Pass 0 for non-fee markets.",
    )
    p_report.add_argument("--bot-trades", default="tv_dry_run_trades.json")
    _add_entry_filter_args(p_report)

    p_tune = sub.add_parser(
        "tune",
        help="sweep gate+sizing configs, pick argmax(PnL) — proves no downgrade",
    )
    p_tune.add_argument("--start", help="ISO date/datetime or unix seconds")
    p_tune.add_argument("--end", help="ISO date/datetime or unix seconds")
    p_tune.add_argument("--series", choices=["15m", "5m"], default="15m")
    p_tune.add_argument("--signal-source", default="tradingview")
    p_tune.add_argument("--stake", type=float, default=1.0)
    p_tune.add_argument("--fee-rate", type=float, default=0.07)

    args = parser.parse_args(argv)
    handlers = {
        "record": cmd_record,
        "settle": cmd_settle,
        "replay": cmd_replay,
        "import-signals": cmd_import_signals,
        "report": cmd_report,
        "tune": cmd_tune,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
