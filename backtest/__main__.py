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
    p.add_argument(
        "--max-entry-prob",
        type=float,
        default=1.0,
        help="band ceiling: skip a signal whose bought-side book prob >= this "
        "(1.0 = off). With --min-entry-prob forms the entry-prob band, e.g. 0.42-0.50",
    )
    p.add_argument(
        "--trade-hours",
        default="",
        help="session filter: UTC hour whitelist, e.g. '8-15' (EU) or '0,4,8-10'; "
        "empty = all hours (off)",
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
    from tv_market_select import parse_trade_hours

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
            max_entry_prob=args.max_entry_prob,
            trade_hours=parse_trade_hours(args.trade_hours),
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


def _bar_seconds(args: argparse.Namespace) -> int:
    """Bar length in seconds for the selected series (15m=900, 5m=300)."""
    return 300 if args.series == "5m" else 900


def _side_stats(report: Any, side: str) -> tuple[int, int, float]:
    """(settled, wins, pnl) restricted to one direction of a ReplayReport."""
    trades = [t for t in report.settled if str(t["direction"]) == side]
    wins = sum(1 for t in trades if t["won"])
    pnl = sum(t["pnl"] for t in trades)
    return len(trades), wins, pnl


MIN_LIVE_SAMPLE = 200  # do not act on the model live until >= this many settled


def cmd_tune_confirm(args: argparse.Namespace, side: str) -> int:
    """Sweep the calibrated confirmation gate for one side and pick argmax(PnL).

    The prior ``base_rate`` is ESTIMATED from data (unconditional win-rate of the
    side, no gate) — not swept. Only ``beta_book``/``beta_mom``/``tau`` are tuned.
    ``--closes-csv`` enables the Phase-2 spot-momentum feature ``z_mom``; without
    it the sweep is book-only (Phase 1) and ``beta_mom`` is held at 0. The current
    live gate (``--current-floor``) is the comparison baseline. ``p_bar`` is fixed
    at 0.5 (book coin-flip -> posterior == prior).

    Go-live guard: when the side's settled sample is below ``MIN_LIVE_SAMPLE`` the
    report still prints, but the suggested env defaults are SUPPRESSED — any
    argmax over a tiny sample is noise, not an edge.
    """
    from backtest.engine import run_replay
    from backtest.ingest import (
        load_bar_closes_csv,
        load_closes_from_signals,
        z_mom_by_window,
    )

    start = _parse_when(args.start) if args.start else 0.0
    end = _parse_when(args.end) if args.end else time.time()
    bar_s = _bar_seconds(args)

    con = db.connect()
    try:
        zmom = None
        if args.closes_from_signals:
            zmom = z_mom_by_window(
                load_closes_from_signals(con, args.signal_source, start, end, bar_s)
            )
        elif args.closes_csv:
            zmom = z_mom_by_window(load_bar_closes_csv(args.closes_csv, bar_s))
        # Prior: unconditional win-rate of this side (no gate, flat stake).
        base = run_replay(
            con,
            start_ts=start,
            end_ts=end,
            stake_usd=args.stake,
            fee_rate=args.fee_rate,
            series=args.series,
            source_like=args.signal_source,
        )
        n_all, w_all, _ = _side_stats(base, side)
        base_rate = (w_all / n_all) if n_all else 0.5

        # Comparison baseline: the CURRENT live gate (book-prob floor) on this side.
        cur = run_replay(
            con,
            start_ts=start,
            end_ts=end,
            stake_usd=args.stake,
            fee_rate=args.fee_rate,
            series=args.series,
            source_like=args.signal_source,
            min_entry_prob=args.current_floor,
        )
        cur_n, cur_w, cur_pnl = _side_stats(cur, side)

        betas = [0.0, 5.0]
        beta_moms = [0.0, 0.25, 0.5, 1.0] if zmom else [0.0]
        taus = [0.50, 0.55]
        results: list[dict[str, Any]] = []
        for b in betas:
            for bm in beta_moms:
                for t in taus:
                    rep = run_replay(
                        con,
                        start_ts=start,
                        end_ts=end,
                        stake_usd=args.stake,
                        fee_rate=args.fee_rate,
                        series=args.series,
                        source_like=args.signal_source,
                        confirm_side=side,
                        confirm_base_rate=base_rate,
                        confirm_beta=b,
                        confirm_tau=t,
                        confirm_p_bar=0.5,
                        confirm_beta_mom=bm,
                        z_mom_by_window=zmom,
                    )
                    n, w, pnl = _side_stats(rep, side)
                    results.append(
                        {
                            "beta": b,
                            "beta_mom": bm,
                            "tau": t,
                            "settled": n,
                            "win_rate": (w / n) if n else 0.0,
                            "pnl": pnl,
                            "ev": (pnl / n) if n else 0.0,
                        }
                    )
    finally:
        con.close()

    best = max(results, key=lambda r: r["pnl"])
    results.sort(key=lambda r: r["pnl"], reverse=True)

    line = "=" * 80
    feat = "book+z_mom (Fase 2)" if zmom else "book-only (Fase 1)"
    print(line)
    print(f"CONFIRMATION TUNE [{side}] — feature: {feat} — critério: PnL total")
    print(line)
    print(
        f"prior base_rate (win-rate {side} sem gate) = {base_rate:.3f}  "
        f"({w_all}/{n_all})   |   p_bar fixo = 0.50"
    )
    print(
        f"baseline (gate atual floor {args.current_floor:.2f}): "
        f"PnL ${cur_pnl:+.2f}  win {cur_w}/{cur_n}"
    )
    print("-" * 80)
    print(
        f"{'beta':>6} {'b_mom':>6} {'tau':>6} {'settled':>8} {'win%':>6} "
        f"{'PnL':>9} {'EV/trade':>9}"
    )
    for r in results:
        tag = "  <- BEST PnL" if r is best else ""
        print(
            f"{r['beta']:>6.1f} {r['beta_mom']:>6.2f} {r['tau']:>6.2f} "
            f"{r['settled']:>8} {r['win_rate'] * 100:>5.0f}% {r['pnl']:>+9.2f} "
            f"{r['ev']:>+9.3f}{tag}"
        )
    print("-" * 80)
    delta = best["pnl"] - cur_pnl
    verdict = "MELHORA" if delta > 1e-9 else "SEM GANHO vs gate atual"
    print(
        f"best PnL ${best['pnl']:+.2f} vs gate atual ${cur_pnl:+.2f} | "
        f"delta ${delta:+.2f}  -> {verdict}"
    )
    if n_all < MIN_LIVE_SAMPLE:
        print(
            f"AMOSTRA INSUFICIENTE: n={n_all} < {MIN_LIVE_SAMPLE} settled — "
            f"NÃO acionar ao vivo (qualquer argmax aqui é ruído). "
            f"Defaults suprimidos até n>={MIN_LIVE_SAMPLE}."
        )
    else:
        print(
            f"defaults sugeridos -> TV_CONFIRM_SIDE={side} "
            f"TV_CONFIRM_BASE_RATE={base_rate:.3f} TV_CONFIRM_BETA={best['beta']} "
            f"TV_CONFIRM_BETA_MOM={best['beta_mom']} TV_CONFIRM_TAU={best['tau']} "
            f"TV_CONFIRM_P_BAR=0.5"
        )
    print(line)
    return 0


def cmd_tune(args: argparse.Namespace) -> int:
    """Sweep gate+sizing configs and pick argmax(total PnL).

    The baseline (floor 0, frac 1.0 = flat stake, no gate) is always in the
    grid, so the recommended config's PnL is >= baseline by construction — the
    "no downgrade" guarantee is mechanical, not a promise.

    With ``--confirm-side`` set, delegates to the calibrated-confirmation sweep.
    """
    if getattr(args, "confirm_side", None):
        return cmd_tune_confirm(args, args.confirm_side)

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
    from tv_market_select import parse_trade_hours

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
            max_entry_prob=args.max_entry_prob,
            trade_hours=parse_trade_hours(args.trade_hours),
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


def cmd_loss_postmortem(args: argparse.Namespace) -> int:
    from backtest.loss_postmortem import run_loss_postmortem

    start = _parse_when(args.start) if args.start else 0.0
    end = _parse_when(args.end) if args.end else time.time()
    con = db.connect()
    try:
        from backtest.settlement import settle_backfill

        settle_backfill(con)
        run_loss_postmortem(
            con,
            start_ts=start,
            end_ts=end,
            stake_usd=args.stake,
            fee_rate=args.fee_rate,
            series=args.series,
            source_like=args.signal_source,
            gate_floor=args.gate_floor,
        )
    finally:
        con.close()
    return 0


def cmd_bias(args: argparse.Namespace) -> int:
    from backtest.bias_scan import SERIES_SPEC, run_bias_scan
    from backtest.settlement import settle_backfill

    start = _parse_when(args.start) if args.start else 0.0
    end = _parse_when(args.end) if args.end else time.time()
    series_list = [args.series] if args.series else list(SERIES_SPEC)
    con = db.connect()
    try:
        settle_backfill(con)
        for series in series_list:
            run_bias_scan(
                con,
                start_ts=start,
                end_ts=end,
                series=series,
                fee_rate=args.fee_rate,
                stake_usd=args.stake,
                ref_seconds=args.ref_seconds,
                sweep=not args.no_sweep,
            )
    finally:
        con.close()
    return 0


def cmd_fusion_replay(args: argparse.Namespace) -> int:
    from backtest.fusion_replay import FusionReplayReport, run_fusion_replay
    from backtest.settlement import settle_backfill

    start = _parse_when(args.start) if args.start else 0.0
    end = _parse_when(args.end) if args.end else time.time()
    con = db.connect()
    coeffs: tuple[float, float] | None = None
    rep_l1: FusionReplayReport | None = None
    rep_l1_gross: FusionReplayReport | None = None
    rep_vol: FusionReplayReport | None = None
    rep_vol_gross: FusionReplayReport | None = None
    vol_cut: float | None = None
    try:
        settle_backfill(con)
        common: dict[str, Any] = dict(
            start_ts=start,
            end_ts=end,
            stake_usd=args.stake,
            series=args.series,
            entry_second=args.entry_second,
            entry_tolerance=args.entry_tolerance,
            trend_up=args.trend_up,
            trend_down=args.trend_down,
        )
        rep = run_fusion_replay(con, fee_rate=args.fee_rate, **common)
        rep_gross = run_fusion_replay(con, fee_rate=0.0, **common)
        if args.vol_gate or args.vol_min is not None:
            # vol threshold from L0's causal vols. side=high keeps vol >= upper
            # tercile; side=low keeps vol <= lower tercile (the inverted gate).
            from backtest.cpcv import _quantile

            vols = [float(t["vol"]) for t in rep.settled]
            if args.vol_side == "low":
                vol_cut = (
                    args.vol_min
                    if args.vol_min is not None
                    else _quantile(vols, 1.0 - args.vol_quantile)
                )
                vol_kwargs: dict[str, Any] = {"vol_max": vol_cut}
            else:
                vol_cut = (
                    args.vol_min
                    if args.vol_min is not None
                    else _quantile(vols, args.vol_quantile)
                )
                vol_kwargs = {"vol_min": vol_cut}
            rep_vol = run_fusion_replay(
                con, fee_rate=args.fee_rate, **vol_kwargs, **common
            )
            rep_vol_gross = run_fusion_replay(con, fee_rate=0.0, **vol_kwargs, **common)
        if args.gate == "ev":
            # L1: fit the Platt calibrator on L0's settled fills (IN-SAMPLE — smoke
            # test only, see calibration.py), then re-run gated on +EV vs the fee.
            from backtest.calibration import ev_gate, fit_platt

            samples = [
                (float(t["p_side"]), 1.0 if t["won"] else 0.0) for t in rep.settled
            ]
            a, b = fit_platt(samples)
            coeffs = (a, b)
            gate = ev_gate(a, b, args.fee_rate)
            rep_l1 = run_fusion_replay(
                con, fee_rate=args.fee_rate, gate_fn=gate, **common
            )
            rep_l1_gross = run_fusion_replay(con, fee_rate=0.0, gate_fn=gate, **common)
    finally:
        con.close()

    def pct(w: float, n: float) -> str:
        return f"{w / n:.0%}" if n else "n/a"

    def stats_block(
        title: str, r: FusionReplayReport, r_gross: FusionReplayReport
    ) -> None:
        s, s0 = r.summary(), r_gross.summary()
        strat: dict[str, list[float]] = {"UP": [0, 0, 0.0], "DOWN": [0, 0, 0.0]}
        for t in r.settled:
            d = str(t["direction"])
            strat[d][1] += 1
            strat[d][0] += 1 if t["won"] else 0
            strat[d][2] += t["pnl"]
        print(f"[{title}]")
        print(
            f"  Mercados         : {s['markets_total']}  "
            f"(deadband-skip: {s['deadband_skip']}, gate-skip: {s['gate_skip']}, "
            f"sem book: {s['unfilled_no_data']}, sem liquidez: {s['unfilled_no_liquidity']})"
        )
        if s["settled"]:
            print(
                f"  Resolvidos       : {s['settled']} -> {s['wins']} WIN / "
                f"{s['settled'] - s['wins']} LOSS ({s['win_rate']:.0%})"
            )
            for d in ("UP", "DOWN"):
                w, n, pnl = strat[d]
                print(
                    f"    {d:4}: {int(n)} trades | {int(w)} WIN | {pct(w, n)} | PnL ${pnl:+.2f}"
                )
            print(
                f"  PnL com fee      : ${s['total_pnl']:+.2f} sobre ${s['total_staked']:.2f} "
                f"| slip {s['avg_slippage_bps']:.0f} bps"
            )
            print(
                f"  PnL sem fee      : ${s0['total_pnl']:+.2f}  (fee custou "
                f"${s0['total_pnl'] - s['total_pnl']:+.2f})"
            )
        else:
            print("  (nenhum mercado resolvido na janela)")

    line = "=" * 70
    print(line)
    print("FUSION REPLAY — late-window favorite-follower")
    print(line)
    print(
        f"Período : {datetime.fromtimestamp(start, tz=UTC):%Y-%m-%d %H:%M} -> "
        f"{datetime.fromtimestamp(end, tz=UTC):%Y-%m-%d %H:%M} UTC"
    )
    print(
        f"Série   : {args.series} | entrada ~{args.entry_second:.0f}s (±{args.entry_tolerance:.0f}s) "
        f"| favorita >{args.trend_up:.2f} / <{args.trend_down:.2f} | stake=${args.stake:.2f} "
        f"| fee={args.fee_rate:.2f}"
    )
    print("-" * 70)
    stats_block("L0 — sempre a favorita", rep, rep_gross)

    if rep_vol is not None and rep_vol_gross is not None and vol_cut is not None:
        from backtest.cpcv import _quantile

        print("-" * 70)
        cmp_op = "<=" if args.vol_side == "low" else ">="
        band = "baixa" if args.vol_side == "low" else "alta"
        print(
            f"  Gate de VOLATILIDADE (causal, YES-mid em [ws, entrada]): "
            f"vol {cmp_op} {vol_cut:.3f}  (lado {args.vol_side}, "
            f"quantil {args.vol_quantile:.2f} do L0)"
        )
        stats_block(f"VOL — só janelas de {band}-vol", rep_vol, rep_vol_gross)

        # 2D: vol-tercil × p_side-bin — a alta-vol adiciona edge ALÉM do p_side?
        vols = [float(t["vol"]) for t in rep.settled]
        c1, c2 = _quantile(vols, 1 / 3), _quantile(vols, 2 / 3)

        def vol_band(v: float) -> str:
            return "baixa" if v < c1 else ("media" if v < c2 else "ALTA")

        p_bins = [0.0, 0.60, 0.70, 0.80, 0.90, 1.01]
        p_labels = ["<.60", ".60-.70", ".70-.80", ".80-.90", ">=.90"]

        def p_band(p: float) -> str:
            for i in range(len(p_bins) - 1):
                if p_bins[i] <= p < p_bins[i + 1]:
                    return p_labels[i]
            return p_labels[-1]

        cells: dict[tuple[str, str], list[float]] = {}
        for t in rep.settled:
            key = (vol_band(float(t["vol"])), p_band(float(t["p_side"])))
            agg = cells.setdefault(key, [0.0, 0.0, 0.0])  # [n, sum_pnl, sum_filled]
            agg[0] += 1
            agg[1] += float(t["pnl"])
            agg[2] += float(t["filled_usd"])

        print("-" * 70)
        print("  EV% por vol-tercil × p_side (controle do confound):")
        print(f"  {'vol\\p_side':<11}" + "".join(f"{lbl:>12}" for lbl in p_labels))
        for vb in ("baixa", "media", "ALTA"):
            row = f"  {vb:<11}"
            for pl in p_labels:
                agg = cells.get((vb, pl))
                if agg and agg[2] > 0:
                    row += f"{agg[1] / agg[2]:>+10.1%}({int(agg[0])})".rjust(12)
                else:
                    row += f"{'-':>12}"
            print(row)

    if coeffs is not None and rep_l1 is not None and rep_l1_gross is not None:
        from backtest.calibration import platt_prob
        from tv_market_select import fee_breakeven_prob

        a, b = coeffs
        print("-" * 70)
        print(
            "  [AVISO] SMOKE TEST: calibrador Platt ajustado IN-SAMPLE (mesmos dados) - "
            "sanity de pipeline, NAO edge out-of-sample (validacao CPCV e o proximo passo)."
        )
        print(f"  Platt: P(win) = sigmoid({a:+.3f}*p + {b:+.3f})")
        print(f"  {'p_side':>7} {'P_cal':>7} {'breakeven':>10} {'decisão':>9}")
        for p in (0.50, 0.60, 0.70, 0.80):
            pc = platt_prob(a, b, p)
            be = fee_breakeven_prob(p, args.fee_rate)
            print(
                f"  {p:>7.2f} {pc:>7.2f} {be:>10.2f} "
                f"{'TRADE' if pc > be else 'skip':>9}"
            )
        print("-" * 70)
        stats_block("L1 — gate de EV (Platt, in-sample)", rep_l1, rep_l1_gross)
    print(line)

    if args.out_csv and rep.trades:
        from backtest.engine import trades_dataframe

        trades_dataframe(rep_l1 or rep).to_csv(args.out_csv, index=False)
        print(f"Trades written to {args.out_csv}")
    return 0


def cmd_fusion_cpcv(args: argparse.Namespace) -> int:
    from backtest.cpcv import fills_from_trades, run_cpcv
    from backtest.fusion_replay import run_fusion_replay
    from backtest.settlement import settle_backfill

    start = _parse_when(args.start) if args.start else 0.0
    end = _parse_when(args.end) if args.end else time.time()
    con = db.connect()
    try:
        settle_backfill(con)
        rep = run_fusion_replay(
            con,
            start_ts=start,
            end_ts=end,
            stake_usd=args.stake,
            fee_rate=args.fee_rate,
            series=args.series,
            entry_second=args.entry_second,
            entry_tolerance=args.entry_tolerance,
            trend_up=args.trend_up,
            trend_down=args.trend_down,
        )
    finally:
        con.close()

    fills = fills_from_trades(rep.settled)
    line = "=" * 70

    if args.gate == "vol":
        from backtest.cpcv import run_cpcv_vol

        window_seconds = 300 if args.series == "5m" else 900
        res = run_cpcv_vol(
            fills,
            n_groups=args.n_groups,
            k_test=args.k_test,
            embargo_windows=args.embargo_windows,
            quantile=args.vol_quantile,
            side=args.vol_side,
            window_seconds=window_seconds,
        )
        print(line)
        print(
            f"FUSION CPCV — validacao OOS do gate de VOLATILIDADE (lado {args.vol_side})"
        )
        print(line)
        if res["n_paths"] == 0:
            print(f"  Sem caminhos (fills={len(fills)}). Janela/dados insuficientes.")
            print(line)
            return 0
        print(
            f"Fills L0 settled : {res['n_fills']} | C({args.n_groups},{args.k_test}) = "
            f"{res['n_paths']} caminhos | quantil-vol {res['quantile']:.2f} | "
            f"embargo {args.embargo_windows:.0f} janela(s)"
        )
        print("-" * 70)
        print(
            f"  {'path':>4} {'n_test':>6} {'gated':>6} {'thr':>7} "
            f"{'L0 PnL':>9} {'L1 PnL':>9} {'delta':>9}"
        )
        for i, p in enumerate(res["paths"]):
            print(
                f"  {i:>4} {p['n_test']:>6} {p['gated_in']:>6} {p['threshold']:>7.3f} "
                f"{p['l0_pnl']:>+9.2f} {p['l1_pnl']:>+9.2f} {p['delta']:>+9.2f}"
            )
        print("-" * 70)
        print(
            f"  delta medio (L1-L0): ${res['mean_delta']:+.2f} "
            f"(desvio ${res['stdev_delta']:.2f})"
        )
        print(
            f"  L1 > L0          : {res['pct_l1_beats_l0']:.0%} dos caminhos | "
            f"L1 PnL>0: {res['pct_l1_positive']:.0%}"
        )
        print(
            f"  PnL medio/path   : L0 ${res['mean_l0_pnl']:+.2f}  vs  "
            f"L1 ${res['mean_l1_pnl']:+.2f}"
        )
        print(
            f"  vol-thr medio    : {res['mean_threshold']:.3f} | fills mantidos/path: "
            f"{res['mean_gated_in']:.0f} (min {res['min_gated_in']}, limite {res['min_kept']})"
        )
        print("-" * 70)
        vol_verdict_msg = {
            "INSUFFICIENT": "INSUFICIENTE — fatia de alta-vol fina demais em algum "
            "caminho. Colete mais dados.",
            "ADDS EDGE": "AGREGA — o gate de vol bate o L0 out-of-sample na maioria "
            "dos caminhos.",
            "NO GAIN": "SEM GANHO — o gate de vol nao supera o L0 baseline OOS.",
        }
        print(
            f"  VEREDITO: {res['verdict']} — {vol_verdict_msg.get(res['verdict'], '')}"
        )
        print(line)
        return 0

    res = run_cpcv(
        fills,
        n_groups=args.n_groups,
        k_test=args.k_test,
        embargo_windows=args.embargo_windows,
        fee_rate=args.fee_rate,
        l2=args.l2,
        min_minority=args.min_minority,
    )

    print(line)
    print("FUSION CPCV — validacao out-of-sample do gate de EV (L1)")
    print(line)
    if res["n_paths"] == 0:
        print(f"  Sem caminhos (fills={len(fills)}). Janela/dados insuficientes.")
        print(line)
        return 0
    print(
        f"Fills L0 settled : {res['n_fills']}  "
        f"(wins {res['total_wins']} / losses {res['total_losses']})"
    )
    print(
        f"CPCV             : C({args.n_groups},{args.k_test}) = {res['n_paths']} caminhos "
        f"| embargo {args.embargo_windows:.0f} janela(s) | fee={args.fee_rate:.2f}"
    )
    print("-" * 70)
    print(
        f"  {'path':>4} {'n_test':>6} {'gated':>6} {'L0 PnL':>9} {'L1 PnL':>9} {'delta':>9}"
    )
    for i, p in enumerate(res["paths"]):
        print(
            f"  {i:>4} {p['n_test']:>6} {p['gated_in']:>6} "
            f"{p['l0_pnl']:>+9.2f} {p['l1_pnl']:>+9.2f} {p['delta']:>+9.2f}"
        )
    print("-" * 70)
    print(
        f"  delta medio (L1-L0): ${res['mean_delta']:+.2f} (desvio ${res['stdev_delta']:.2f})"
    )
    print(
        f"  L1 > L0          : {res['pct_l1_beats_l0']:.0%} dos caminhos | "
        f"L1 PnL>0: {res['pct_l1_positive']:.0%}"
    )
    print(
        f"  PnL medio/path   : L0 ${res['mean_l0_pnl']:+.2f}  vs  L1 ${res['mean_l1_pnl']:+.2f}"
    )
    print(
        f"  min losses treino: {res['min_train_losses']} (limite min-minority "
        f"{res['min_minority']})"
    )
    print("-" * 70)
    verdict_msg = {
        "INSUFFICIENT": "INSUFICIENTE — poucas perdas para validar o calibrador "
        "(amostra da classe minoritaria abaixo do limite). Colete mais dados.",
        "L1 ADDS EDGE": "L1 AGREGA — bate o L0 out-of-sample na maioria dos caminhos.",
        "NO GAIN": "SEM GANHO — L1 nao supera o L0 baseline out-of-sample.",
    }
    print(f"  VEREDITO: {res['verdict']} — {verdict_msg.get(res['verdict'], '')}")
    print(line)
    return 0


def cmd_guppy_parity(args: argparse.Namespace) -> int:
    from backtest.guppy_parity import run_parity
    from local_signal.guppy import GuppyParams

    params = GuppyParams(min_warmup=args.min_warmup)
    rep = run_parity(args.csv, params, up_col=args.up_col, down_col=args.down_col)

    print(f"\nGuppy parity vs TradingView export: {rep.csv_path}")
    print(
        f"  bars={rep.n_bars}  evaluated(post-warmup={params.min_warmup})={rep.n_eval}"
    )
    print(f"  TV fired: UP={rep.tv_up} DOWN={rep.tv_down} (total {rep.tv_fired})")
    print(f"  local fired: UP={rep.local_up} DOWN={rep.local_down}")
    print(
        f"  agree: UP={rep.agree_up} DOWN={rep.agree_down} none={rep.agree_none}  "
        f"| matched={rep.matched}"
    )
    print(
        f"  disagree: tv_only(miss)={rep.tv_extra} local_only(extra)={rep.local_extra} "
        f"conflict={rep.conflict}"
    )
    print(
        f"  fired_match_rate={rep.fired_match_rate:.4f}  "
        f"overall_agree_rate={rep.overall_agree_rate:.4f}"
    )
    print(
        f"  max|err| vs TV cols: RSINorm={rep.rsi_max_abs_err:.4g} "
        f"ma1={rep.ma1_max_abs_err:.4g} ma5={rep.ma5_max_abs_err:.4g}"
    )
    if rep.mismatches:
        print("  first mismatches (bar, local, tv):")
        for bar, local, tv in rep.mismatches[:25]:
            print(f"    #{bar}: local={local} tv={tv}")
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
    p_tune.add_argument(
        "--confirm-side",
        choices=["UP", "DOWN"],
        help="sweep the calibrated confirmation gate for this side only "
        "(prior estimated from data); omit for the floor/sizing sweep",
    )
    p_tune.add_argument(
        "--current-floor",
        type=float,
        default=0.42,
        help="book-prob floor of the CURRENT live gate, used as the "
        "comparison baseline for the confirmation sweep (default 0.42)",
    )
    p_tune.add_argument(
        "--closes-csv",
        help="OHLC CSV (time+close) feeding the Phase-2 z_mom feature; "
        "omit for a book-only (Phase 1) confirmation sweep",
    )
    p_tune.add_argument(
        "--closes-from-signals",
        action="store_true",
        help="feed z_mom from the signals' own raw_json.preco_fechamento "
        "(live-aligned close source); takes precedence over --closes-csv",
    )

    p_pm = sub.add_parser(
        "loss-postmortem",
        help="slice the strategy's LOSSES by prob/direction/session/momentum/slippage",
    )
    p_pm.add_argument("--start", help="ISO date/datetime or unix seconds")
    p_pm.add_argument("--end", help="ISO date/datetime or unix seconds")
    p_pm.add_argument("--series", choices=["15m", "5m"], default="15m")
    p_pm.add_argument("--signal-source", default="tradingview")
    p_pm.add_argument("--stake", type=float, default=3.0)
    p_pm.add_argument("--fee-rate", type=float, default=0.07)
    p_pm.add_argument(
        "--gate-floor",
        type=float,
        default=0.42,
        help="live book-agreement floor, used to partition caught vs surviving losses",
    )

    p_bias = sub.add_parser(
        "bias",
        help="structural Yes-Bias scan: calibration + symmetric YES/NO EV over "
        "EVERY recorded market (no signal filter)",
    )
    p_bias.add_argument("--start", help="ISO date/datetime or unix seconds")
    p_bias.add_argument("--end", help="ISO date/datetime or unix seconds")
    p_bias.add_argument(
        "--series",
        choices=["15m", "5m", "4h"],
        default=None,
        help="series to scan; omit to run all three (btc 15m, 5m, 4h)",
    )
    p_bias.add_argument("--stake", type=float, default=3.0)
    p_bias.add_argument("--fee-rate", type=float, default=0.07)
    p_bias.add_argument(
        "--ref-seconds",
        type=int,
        default=None,
        help="seconds into the window to read the YES mid (default: near-close "
        "per series); the EV test and cuts use this reference",
    )
    p_bias.add_argument(
        "--no-sweep",
        action="store_true",
        help="skip the time-to-expiry calibration sweep (only the primary ref)",
    )

    p_fusion = sub.add_parser(
        "fusion-replay",
        help="replay the fusion strategy (late-window favorite-follower) on recorded books",
    )
    p_fusion.add_argument("--start", help="ISO date/datetime or unix seconds")
    p_fusion.add_argument("--end", help="ISO date/datetime or unix seconds")
    p_fusion.add_argument("--series", choices=["15m", "5m"], default="15m")
    p_fusion.add_argument("--stake", type=float, default=3.0)
    p_fusion.add_argument(
        "--fee-rate",
        type=float,
        default=0.07,
        help="taker fee rate; 15m/5m crypto = 0.07. PnL is reported with AND without it.",
    )
    p_fusion.add_argument(
        "--entry-second",
        type=float,
        default=810.0,
        help="seconds into the window to read the favorite (default 810 = minute 13.5)",
    )
    p_fusion.add_argument(
        "--entry-tolerance",
        type=float,
        default=30.0,
        help="± seconds band to find a snapshot around --entry-second (default 30 => 780-840s)",
    )
    p_fusion.add_argument(
        "--trend-up",
        type=float,
        default=0.60,
        help="buy YES when the UP mid is above this (default 0.60)",
    )
    p_fusion.add_argument(
        "--trend-down",
        type=float,
        default=0.40,
        help="buy NO when the UP mid is below this (default 0.40); deadband between = skip",
    )
    p_fusion.add_argument(
        "--gate",
        choices=["none", "ev"],
        default="none",
        help="none = L0 baseline; ev = L1 Platt-calibrated +EV gate "
        "(fit IN-SAMPLE for a smoke test, prints L0 vs L1)",
    )
    p_fusion.add_argument("--out-csv", help="optional path to dump per-trade rows")
    p_fusion.add_argument(
        "--vol-gate",
        action="store_true",
        help="also run L0 restricted to the high-vol tercile (causal YES-mid vol) "
        "and print a vol×p_side EV table",
    )
    p_fusion.add_argument(
        "--vol-min",
        type=float,
        default=None,
        help="explicit causal-vol floor for the vol gate (overrides the tercile "
        "cutoff); implies --vol-gate",
    )
    p_fusion.add_argument(
        "--vol-quantile",
        type=float,
        default=0.667,
        help="quantile of L0 vols used as the vol-gate cutoff (default 0.667 = "
        "upper tercile)",
    )
    p_fusion.add_argument(
        "--vol-side",
        choices=["high", "low"],
        default="high",
        help="high = keep high-vol windows (vol >= cutoff); low = inverted gate, "
        "keep low-vol windows (vol <= lower-tercile cutoff)",
    )

    p_cpcv = sub.add_parser(
        "fusion-cpcv",
        help="validate the L1 EV gate out-of-sample via Combinatorial Purged CV",
    )
    p_cpcv.add_argument("--start", help="ISO date/datetime or unix seconds")
    p_cpcv.add_argument("--end", help="ISO date/datetime or unix seconds")
    p_cpcv.add_argument("--series", choices=["15m", "5m"], default="15m")
    p_cpcv.add_argument("--stake", type=float, default=3.0)
    p_cpcv.add_argument("--fee-rate", type=float, default=0.07)
    p_cpcv.add_argument("--entry-second", type=float, default=810.0)
    p_cpcv.add_argument("--entry-tolerance", type=float, default=30.0)
    p_cpcv.add_argument("--trend-up", type=float, default=0.60)
    p_cpcv.add_argument("--trend-down", type=float, default=0.40)
    p_cpcv.add_argument(
        "--n-groups", type=int, default=6, help="contiguous time blocks (default 6)"
    )
    p_cpcv.add_argument(
        "--k-test", type=int, default=2, help="test blocks per split (default 2)"
    )
    p_cpcv.add_argument(
        "--embargo-windows",
        type=float,
        default=1.0,
        help="purge train fills within this many 15m windows of a test fill (default 1)",
    )
    p_cpcv.add_argument("--l2", type=float, default=1e-3, help="Platt ridge strength")
    p_cpcv.add_argument(
        "--min-minority",
        type=int,
        default=100,
        help="min train losses to trust a fit; below this the verdict is INSUFFICIENT",
    )
    p_cpcv.add_argument(
        "--gate",
        choices=["ev", "vol"],
        default="ev",
        help="ev = Platt +EV gate (default); vol = causal high-vol gate "
        "(threshold fit on train, scored OOS on test)",
    )
    p_cpcv.add_argument(
        "--vol-quantile",
        type=float,
        default=0.667,
        help="train-fold vol quantile used as the gate cutoff (default 0.667)",
    )
    p_cpcv.add_argument(
        "--vol-side",
        choices=["high", "low"],
        default="high",
        help="high = gate keeps high-vol fills; low = inverted gate (low-vol)",
    )

    p_parity = sub.add_parser(
        "guppy-parity",
        help="validate local_signal.guppy against a TradingView CSV export (TRK-001 T3)",
    )
    p_parity.add_argument(
        "--csv", required=True, help="TradingView chart-data CSV export"
    )
    p_parity.add_argument("--up-col", default=None, help="override UP signal column")
    p_parity.add_argument(
        "--down-col", default=None, help="override DOWN signal column"
    )
    p_parity.add_argument(
        "--min-warmup",
        type=int,
        default=200,
        help="bars to skip before comparing (RSI/EMA convergence; default 200)",
    )

    args = parser.parse_args(argv)
    handlers = {
        "record": cmd_record,
        "settle": cmd_settle,
        "replay": cmd_replay,
        "import-signals": cmd_import_signals,
        "report": cmd_report,
        "tune": cmd_tune,
        "loss-postmortem": cmd_loss_postmortem,
        "bias": cmd_bias,
        "fusion-replay": cmd_fusion_replay,
        "fusion-cpcv": cmd_fusion_cpcv,
        "guppy-parity": cmd_guppy_parity,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
