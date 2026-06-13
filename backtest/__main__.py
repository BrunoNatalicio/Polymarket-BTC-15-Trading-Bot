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
        f"{s['unfilled_no_liquidity']} no-liquidity"
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
            series=args.series,
            source_like=source,
        )
        trades = load_bot_trades(args.bot_trades)
        bot = evaluate_bot_trades(con, trades)
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
    print(f"Série   : {args.series} | fonte={source} | stake=${args.stake:.2f}")
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
    p_replay.add_argument("--fee-rate", type=float, default=0.0)
    p_replay.add_argument(
        "--signal-source",
        help="SQL LIKE filter on signals.source (e.g. tradingview, "
        "tradingview_csv_300s) — replay one signal stream at a time",
    )
    p_replay.add_argument(
        "--fill-policy", choices=["partial", "all_or_nothing"], default="partial"
    )
    p_replay.add_argument("--out-csv", help="optional path to dump per-trade rows")

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
    p_report.add_argument("--bot-trades", default="tv_dry_run_trades.json")

    args = parser.parse_args(argv)
    handlers = {
        "record": cmd_record,
        "settle": cmd_settle,
        "replay": cmd_replay,
        "import-signals": cmd_import_signals,
        "report": cmd_report,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
