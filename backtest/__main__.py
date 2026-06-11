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
        f"Stake      : ${args.stake:.2f} | policy={args.fill_policy} "
        f"| tolerance={args.tolerance}s | fee={args.fee_rate:.4f}"
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backtest")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("record", help="run the orderbook/signal recorder daemon")
    sub.add_parser("settle", help="backfill outcomes for expired markets")

    p_replay = sub.add_parser("replay", help="replay signals against recorded books")
    p_replay.add_argument("--start", help="ISO date/datetime or unix seconds")
    p_replay.add_argument("--end", help="ISO date/datetime or unix seconds")
    p_replay.add_argument("--stake", type=float, default=50.0)
    p_replay.add_argument("--tolerance", type=float, default=10.0)
    p_replay.add_argument("--fee-rate", type=float, default=0.0)
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

    args = parser.parse_args(argv)
    handlers = {
        "record": cmd_record,
        "settle": cmd_settle,
        "replay": cmd_replay,
        "import-signals": cmd_import_signals,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
