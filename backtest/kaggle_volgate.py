"""CPCV of the volatility gate on the Kaggle btc-updown-15m sample.

Hypothesis (from the price x vol cross-tab): a MARGINAL favourite (entry prob in
``band``, default 0.60-0.75) wins less when recent BTC volatility is high, dropping
it below the fee breakeven — while DEEP favourites (>0.75) are robust. So the gate
is: **trade everything EXCEPT marginal favourites in a high-vol regime.**

This validates it OUT-OF-SAMPLE via Combinatorial Purged CV: the high-vol cutoff
is fit on each TRAIN fold (a quantile of train vol) and applied to the TEST fold,
so the threshold never sees the data it is judged on. Reuses the tested
``cpcv_splits``/``purge_embargo`` from ``backtest.cpcv``.

Same honest LIMITS as kaggle_validate.py: PRICE-ONLY (fill at the close-quote mid,
no slippage/depth) and the quote is the frozen near-close quote, not the live
minute-13 entry. The BTC vol feature is a leakage-safe trailing realised vol
(hourly closes strictly BEFORE the window) fetched from the Coinbase public API
(same source ``backtest.settlement`` already uses).
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import re
import statistics
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import pandas as pd

import backtest.matching as matching
import backtest.settlement as settlement
from backtest.cpcv import cpcv_splits, purge_embargo

_SLUG = re.compile(r"btc-updown-15m-(\d+)")
_CB = "https://api.exchange.coinbase.com/products/BTC-USD/candles"


@dataclass(frozen=True)
class VFill:
    ts: float
    p_side: float  # bought favourite's entry prob (>0.60 by construction)
    won: float
    pnl: float
    vol: float  # trailing realised vol of BTC before the window (regime)


def _btc_hourly_closes(lo: int, hi: int) -> dict[int, float]:
    """Hourly BTC-USD closes in [lo-12h, hi] from Coinbase (300/req, paginated)."""
    closes: dict[int, float] = {}
    with httpx.Client(timeout=15.0, headers={"User-Agent": "research"}) as c:
        t = lo - 12 * 3600
        while t <= hi:
            end = min(t + 300 * 3600, hi + 3600)
            try:
                r = c.get(
                    _CB,
                    params={
                        "granularity": 3600,
                        "start": datetime.fromtimestamp(t, UTC).isoformat(),
                        "end": datetime.fromtimestamp(end, UTC).isoformat(),
                    },
                )
                for row in r.json():  # [time, low, high, open, close, volume]
                    closes[int(row[0])] = float(row[4])
            except Exception as e:  # noqa: BLE001
                print(f"  coinbase fetch err: {e}")
            t = end
            time.sleep(0.34)
    return closes


def build_fills(
    csv_path: str, stake: float, fee_rate: float, vol_hours: int
) -> list[VFill]:
    mk = pd.read_csv(csv_path, low_memory=False)
    closed = mk["closed"].astype(str).str.lower().isin(("true", "1"))
    b = mk[mk["slug"].astype(str).str.startswith("btc-updown-15m-") & closed]
    raw: list[tuple[int, float, bool]] = []
    for r in b.itertuples():
        try:
            outs = json.loads(r.outcomes)
            ops = [float(x) for x in json.loads(r.outcomePrices)]
            bid, ask = float(r.bestBid), float(r.bestAsk)
            ws = int(_SLUG.search(str(r.slug)).group(1))  # type: ignore[union-attr]
        except (TypeError, ValueError, AttributeError, json.JSONDecodeError):
            continue
        if len(outs) != 2 or len(ops) != 2 or not (bid > 0 or ask > 0):
            continue
        up_i = 0 if str(outs[0]).lower() in ("up", "yes") else 1
        up_mid = (bid + ask) / 2.0
        if up_i == 1:
            up_mid = 1.0 - up_mid
        raw.append((ws, up_mid, ops[up_i] > 0.5))
    raw.sort()
    lo, hi = raw[0][0], raw[-1][0]
    print(
        f"  markets {len(raw)} | fetching BTC hourly {datetime.fromtimestamp(lo, UTC):%Y-%m-%d}.."
    )
    closes = _btc_hourly_closes(lo, hi)
    hrs = sorted(closes)
    clos = [closes[h] for h in hrs]
    print(f"  BTC hours: {len(hrs)}")

    def tvol(ws: int) -> float | None:
        base = (ws // 3600) * 3600 - 3600  # last hour strictly before the window
        j = bisect.bisect_right(hrs, base) - 1
        if j < vol_hours:
            return None
        seg = clos[j - vol_hours + 1 : j + 1]
        rets = [math.log(seg[k] / seg[k - 1]) for k in range(1, len(seg))]
        if len(rets) < 2:
            return None
        return statistics.pstdev(rets)

    fills: list[VFill] = []
    for ws, up_mid, up_won in raw:
        if up_mid > 0.60:
            direction, p_side, won = "UP", up_mid, up_won
        elif up_mid < 0.40:
            direction, p_side, won = "DOWN", 1.0 - up_mid, not up_won
        else:
            continue
        v = tvol(ws)
        if v is None:
            continue
        fill = matching.simulate_market_buy([(p_side, 1e9)], stake, fee_rate=fee_rate)
        if fill.filled_tokens <= 0:
            continue
        outcome = "YES" if up_won else "NO"
        s = settlement.settle_fill(
            direction, fill.filled_usd, fill.filled_tokens, outcome
        )
        fills.append(VFill(float(ws), p_side, 1.0 if won else 0.0, s["pnl"], v))
    return fills


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kaggle_volgate")
    ap.add_argument("--csv", required=True)
    ap.add_argument("--stake", type=float, default=3.0)
    ap.add_argument("--fee-rate", type=float, default=0.07)
    ap.add_argument("--vol-hours", type=int, default=6)
    ap.add_argument("--band-lo", type=float, default=0.60)
    ap.add_argument("--band-hi", type=float, default=0.75)
    ap.add_argument(
        "--vol-q", type=float, default=0.667, help="train quantile = high-vol cutoff"
    )
    ap.add_argument("--n-groups", type=int, default=6)
    ap.add_argument("--k-test", type=int, default=2)
    ap.add_argument("--embargo-windows", type=float, default=1.0)
    args = ap.parse_args(argv)

    print("=" * 72)
    print("KAGGLE VOL-GATE CPCV — skip marginal favourites in high-vol regimes")
    print("=" * 72)
    fills = build_fills(args.csv, args.stake, args.fee_rate, args.vol_hours)
    fills.sort(key=lambda f: f.ts)
    n = len(fills)
    losses = sum(1 for f in fills if not f.won)
    print(
        f"  fills {n} | losses {losses} | banda marginal "
        f"({args.band_lo:.2f},{args.band_hi:.2f}) | vol {args.vol_hours}h q={args.vol_q}"
    )

    # In-sample intuition: the high-vol marginal cohort that the gate would skip.
    thr_all = statistics.quantiles([f.vol for f in fills], n=1000)[
        int(args.vol_q * 1000) - 1
    ]
    skip = [
        f for f in fills if args.band_lo < f.p_side < args.band_hi and f.vol > thr_all
    ]
    if skip:
        sk_pnl = sum(f.pnl for f in skip)
        sk_win = sum(f.won for f in skip) / len(skip)
        print(
            f"  [in-sample] cohort do gate: {len(skip)} trades | win {sk_win:.1%} | "
            f"PnL ${sk_pnl:+.2f}  (gate add value sse este PnL < 0)"
        )

    embargo_s = args.embargo_windows * 900
    deltas: list[float] = []
    paths = 0
    skipped_tot = 0
    for tr, te in cpcv_splits(n, args.n_groups, args.k_test):
        tr = purge_embargo(fills, tr, te, embargo_s)  # type: ignore[arg-type]  # VFill has .ts
        if not tr or not te:
            continue
        train_vols = sorted(fills[i].vol for i in tr)
        cutoff = train_vols[min(len(train_vols) - 1, int(args.vol_q * len(train_vols)))]
        l0 = sum(fills[i].pnl for i in te)
        l1 = 0.0
        for i in te:
            f = fills[i]
            if args.band_lo < f.p_side < args.band_hi and f.vol > cutoff:
                skipped_tot += 1
                continue
            l1 += f.pnl
        deltas.append(l1 - l0)
        paths += 1

    print("-" * 72)
    if not paths:
        print("  sem caminhos")
        return 1
    mean_d = statistics.fmean(deltas)
    pos = sum(1 for d in deltas if d > 1e-9)
    print(
        f"  CPCV C({args.n_groups},{args.k_test}) = {paths} caminhos | skips totais {skipped_tot}"
    )
    print(
        f"  delta medio (vol-gate - L0): ${mean_d:+.2f} (desvio ${statistics.pstdev(deltas):.2f})"
    )
    print(f"  gate > L0 em {pos}/{paths} caminhos ({pos / paths:.0%})")
    verdict = (
        "VOL-GATE AGREGA (bate L0 OOS)"
        if mean_d > 1e-9 and pos * 2 >= paths
        else "SEM GANHO (nao supera L0 OOS)"
    )
    print(f"  VEREDITO: {verdict}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
