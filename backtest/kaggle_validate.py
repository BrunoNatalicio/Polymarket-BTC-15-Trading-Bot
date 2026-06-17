"""Price-only L0/L1 validation on the Kaggle Polymarket dataset.

The Kaggle "polymarket-prediction-markets" dump (ismetsemedov) carries ~5k closed
`btc-updown-15m` markets (Oct–Dec 2025), each with a frozen near-close
`bestBid`/`bestAsk` quote + the resolved outcome. That is a large, INDEPENDENT,
earlier sample than our own recorder (~485 markets / ~22 losses) — crucially with
~660 losses, finally enough minority class to validate the L1 EV gate via CPCV
(which came back INSUFFICIENT on the recorder data).

LIMITS — read results as approximate, NOT execution-grade:
- **Price-only.** The dataset has top-of-book `bestBid`/`bestAsk`, no L2 depth, so
  the fill is modelled at the quoted mid with **no slippage and no depth limit**.
  PnL (especially at larger stakes) is therefore **optimistic** — this cannot test
  $10 market impact, only a midpoint approximation.
- The quote is the frozen near-**close** quote, not our controlled minute-13 entry,
  so this validates "follow the favourite at the closing quote" — an approximation
  of the live min-13 rule.

Reuses the same tested pieces as the recorder backtest: `matching.simulate_market_buy`
(exact taker-fee model), `settlement.settle_fill`, `cpcv.run_cpcv`
(`calibration.fit_platt`/`platt_prob` + `tv_market_select.fee_breakeven_prob`).
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys

import pandas as pd

import backtest.matching as matching
import backtest.settlement as settlement
from backtest.cpcv import Fill, run_cpcv

_SLUG_TS = re.compile(r"btc-updown-15m-(\d+)")


def load_fills(
    csv_path: str,
    stake: float,
    fee_rate: float,
    trend_up: float = 0.60,
    trend_down: float = 0.40,
) -> tuple[list[Fill], dict[str, int]]:
    """Turn the Kaggle CSV's closed btc-updown-15m rows into follow-favourite fills.

    Direction = follow the favourite on the near-close Up mid (`>trend_up` buy Up,
    `<trend_down` buy Down, deadband skip). The fill is priced at that side's mid
    with the real taker-fee model but **no slippage** (single deep synthetic level).
    """
    mk = pd.read_csv(csv_path, low_memory=False)
    closed = mk["closed"].astype(str).str.lower().isin(("true", "1"))
    b = mk[mk["slug"].astype(str).str.startswith("btc-updown-15m-") & closed]
    fills: list[Fill] = []
    stats = {"rows": len(b), "deadband": 0, "skipped": 0}
    for r in b.itertuples():
        try:
            outs = json.loads(r.outcomes)
            ops = [float(x) for x in json.loads(r.outcomePrices)]
            bid, ask = float(r.bestBid), float(r.bestAsk)
            ws = int(_SLUG_TS.search(str(r.slug)).group(1))  # type: ignore[union-attr]
        except (TypeError, ValueError, AttributeError, json.JSONDecodeError):
            stats["skipped"] += 1
            continue
        if len(outs) != 2 or len(ops) != 2 or not (bid > 0 or ask > 0):
            stats["skipped"] += 1
            continue
        up_i = 0 if str(outs[0]).lower() in ("up", "yes") else 1
        up_mid = (bid + ask) / 2.0
        if up_i == 1:
            up_mid = 1.0 - up_mid
        up_won = ops[up_i] > 0.5

        if up_mid > trend_up:
            direction, p_side, won = "UP", up_mid, up_won
        elif up_mid < trend_down:
            direction, p_side, won = "DOWN", 1.0 - up_mid, not up_won
        else:
            stats["deadband"] += 1
            continue

        # Price-only fill: a single deep level at p_side (no slippage), real fee.
        fill = matching.simulate_market_buy([(p_side, 1e9)], stake, fee_rate=fee_rate)
        if fill.filled_tokens <= 0:
            stats["skipped"] += 1
            continue
        outcome = "YES" if up_won else "NO"
        s = settlement.settle_fill(
            direction, fill.filled_usd, fill.filled_tokens, outcome
        )
        fills.append(
            Fill(ts=float(ws), p_side=p_side, won=1.0 if won else 0.0, pnl=s["pnl"])
        )
    return fills, stats


def bankroll_sim(fills: list[Fill], bankroll: float) -> dict[str, float]:
    """Sequential flat-stake bankroll walk over the fills (ordered by window)."""
    ts = sorted(fills, key=lambda f: f.ts)
    bk = peak = minbk = bankroll
    maxdd = 0.0
    streak = maxstreak = 0
    for f in ts:
        bk += f.pnl
        peak = max(peak, bk)
        maxdd = max(maxdd, peak - bk)
        minbk = min(minbk, bk)
        streak = 0 if f.won else streak + 1
        maxstreak = max(maxstreak, streak)
    return {
        "final": bk,
        "ret_pct": (bk / bankroll - 1) * 100 if bankroll else 0.0,
        "maxdd": maxdd,
        "maxdd_pct": (maxdd / peak * 100) if peak else 0.0,
        "minbk": minbk,
        "maxstreak": float(maxstreak),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kaggle_validate")
    ap.add_argument("--csv", required=True, help="path to polymarket_markets.csv")
    ap.add_argument("--stake", type=float, default=10.0)
    ap.add_argument("--bankroll", type=float, default=75.98)
    ap.add_argument("--fee-rate", type=float, default=0.07)
    ap.add_argument("--trend-up", type=float, default=0.60)
    ap.add_argument("--trend-down", type=float, default=0.40)
    ap.add_argument("--n-groups", type=int, default=6)
    ap.add_argument("--k-test", type=int, default=2)
    ap.add_argument("--embargo-windows", type=float, default=1.0)
    ap.add_argument("--min-minority", type=int, default=100)
    args = ap.parse_args(argv)

    fills, st = load_fills(
        args.csv, args.stake, args.fee_rate, args.trend_up, args.trend_down
    )
    n = len(fills)
    line = "=" * 72
    print(line)
    print("KAGGLE PRICE-ONLY VALIDATION — btc-updown-15m (favorite-follower)")
    print(line)
    print(
        "  [AVISO] price-only: fill no mid SEM slippage/profundidade (otimista p/ "
        "stakes altos); entrada = cotacao de fechamento, nao minuto-13."
    )
    print(
        f"  Universo: {st['rows']} mercados fechados | usados {n} | "
        f"deadband-skip {st['deadband']} | descartados {st['skipped']}"
    )
    if n == 0:
        print("  (nenhum fill — CSV vazio ou colunas inesperadas)")
        print(line)
        return 1

    wins = int(sum(f.won for f in fills))
    losses = n - wins
    net = sum(f.pnl for f in fills)
    avg_p = statistics.fmean([f.p_side for f in fills])
    print("-" * 72)
    print(f"[L0] seguir a favorita (>{args.trend_up:.2f} / <{args.trend_down:.2f})")
    print(
        f"  Trades {n} | {wins} WIN / {losses} LOSS ({wins / n:.1%}) | "
        f"entrada media ${avg_p:.3f} | EV/trade +${net / n:.4f}"
    )
    print(f"  PnL liquido (stake ${args.stake:.0f}): ${net:+.2f}")
    bk = bankroll_sim(fills, args.bankroll)
    print(
        f"  Banca ${args.bankroll:.2f} -> ${bk['final']:.2f} ({bk['ret_pct']:+.1f}%) | "
        f"maxDD ${bk['maxdd']:.2f} ({bk['maxdd_pct']:.1f}%) | min ${bk['minbk']:.2f} | "
        f"pior sequencia {int(bk['maxstreak'])}"
    )

    print("-" * 72)
    print("[L1] gate de EV calibrado (Platt) — validado OUT-OF-SAMPLE via CPCV")
    res = run_cpcv(
        fills,
        n_groups=args.n_groups,
        k_test=args.k_test,
        embargo_windows=args.embargo_windows,
        fee_rate=args.fee_rate,
        min_minority=args.min_minority,
    )
    if res["n_paths"]:
        print(
            f"  C({args.n_groups},{args.k_test}) = {res['n_paths']} caminhos | "
            f"total losses {res['total_losses']} | min losses/treino {res['min_train_losses']}"
        )
        print(
            f"  delta medio L1-L0 ${res['mean_delta']:+.2f} (desvio ${res['stdev_delta']:.2f}) | "
            f"L1>L0 em {res['pct_l1_beats_l0']:.0%} | L1 PnL>0 em {res['pct_l1_positive']:.0%}"
        )
        print(
            f"  PnL medio/path L0 ${res['mean_l0_pnl']:+.2f} vs L1 ${res['mean_l1_pnl']:+.2f}"
        )
        verdict = {
            "INSUFFICIENT": "INSUFICIENTE (poucas perdas/treino)",
            "L1 ADDS EDGE": "L1 AGREGA (bate L0 OOS na maioria dos caminhos)",
            "NO GAIN": "SEM GANHO (L1 nao supera L0 OOS)",
        }.get(res["verdict"], res["verdict"])
        print(f"  VEREDITO: {verdict}")
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
