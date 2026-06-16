"""Combinatorial Purged Cross-Validation (CPCV) for the L1 EV gate.

Validates the Platt EV gate (``calibration.py``) **out-of-sample**: does gating
the favourite-follower on +EV-vs-fee beat the raw L0 baseline *off the data it
was fit on*? Walk-forward tests a single path and overfits; CPCV (López de Prado)
runs every C(n_groups, k_test) train/test split with a purge/embargo gap, giving
a *distribution* of OOS deltas instead of one fragile number.

Key simplification: L1 is a **pure gate over the same L0 fills** — entry, fee and
PnL per trade are unchanged; the gate only drops some. So CPCV operates on the
in-memory list of settled fills ``(signal_ts, p_side, won, pnl)`` — no DB re-query,
no re-matching. Fully deterministic and unit-testable.

Honest guard: with a ~93% win rate the losing class is tiny (~dozens). A
calibrator can't be validated without enough of the minority class, so the report
tracks the minimum train-loss count across paths and marks the verdict
``INSUFFICIENT`` below ``min_minority`` — that "we can't validate yet" is itself a
finding (matches the multi-agent debate's risk stance), not a failure to hide.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import Any

from backtest.calibration import fit_platt, platt_prob
from tv_market_select import fee_breakeven_prob

WINDOW_SECONDS = 900


@dataclass(frozen=True)
class Fill:
    """One settled L0 fill — the unit CPCV resamples."""

    ts: float  # signal_ts (entry time), used for ordering + embargo
    p_side: float  # bought favourite's implied prob (calibrator feature + price)
    won: float  # 1.0 win / 0.0 loss (the label)
    pnl: float  # realized PnL of this fill, net of fee (unchanged by the gate)


def fills_from_trades(trades: Sequence[dict[str, Any]]) -> list[Fill]:
    """Build the CPCV unit list from ``run_fusion_replay(...).settled`` dicts."""
    out = [
        Fill(
            ts=float(t["signal_ts"]),
            p_side=float(t["p_side"]),
            won=1.0 if t["won"] else 0.0,
            pnl=float(t["pnl"]),
        )
        for t in trades
        if t.get("pnl") is not None
    ]
    out.sort(key=lambda f: f.ts)
    return out


def _groups(n_items: int, n_groups: int) -> list[list[int]]:
    """Partition ``range(n_items)`` into ``n_groups`` contiguous near-equal blocks."""
    n_groups = max(1, min(n_groups, n_items))
    base, extra = divmod(n_items, n_groups)
    groups: list[list[int]] = []
    start = 0
    for g in range(n_groups):
        size = base + (1 if g < extra else 0)
        groups.append(list(range(start, start + size)))
        start += size
    return groups


def cpcv_splits(
    n_items: int, n_groups: int, k_test: int
) -> list[tuple[list[int], list[int]]]:
    """All C(n_groups, k_test) (train_idx, test_idx) splits over contiguous groups."""
    groups = _groups(n_items, n_groups)
    n_groups = len(groups)
    k_test = max(1, min(k_test, n_groups - 1)) if n_groups > 1 else 1
    splits: list[tuple[list[int], list[int]]] = []
    for combo in combinations(range(n_groups), k_test):
        test_idx = [i for g in combo for i in groups[g]]
        train_idx = [i for g in range(n_groups) if g not in combo for i in groups[g]]
        splits.append((train_idx, test_idx))
    return splits


def purge_embargo(
    fills: Sequence[Fill],
    train_idx: Sequence[int],
    test_idx: Sequence[int],
    embargo_s: float,
) -> list[int]:
    """Drop train indices whose ts is within ``embargo_s`` of any test fill's ts.

    Labels resolve inside one ~15m window, so purging overlap reduces to a time
    embargo around the test blocks — prevents an adjacent window (correlated by
    regime) leaking train info into the test fold.
    """
    if embargo_s <= 0:
        return list(train_idx)
    test_ts = sorted(fills[i].ts for i in test_idx)
    kept: list[int] = []
    for i in train_idx:
        t = fills[i].ts
        # nearest test ts via bisect-like scan (test_ts sorted, small folds)
        near = min((abs(t - tt) for tt in test_ts), default=embargo_s + 1.0)
        if near > embargo_s:
            kept.append(i)
    return kept


def score_path(
    fills: Sequence[Fill],
    train_idx: Sequence[int],
    test_idx: Sequence[int],
    fee_rate: float,
    l2: float = 1e-3,
) -> dict[str, Any]:
    """Fit Platt on train, apply the EV gate to test, score L0 vs L1 on test."""
    train = [fills[i] for i in train_idx]
    test = [fills[i] for i in test_idx]
    a, b = fit_platt([(f.p_side, f.won) for f in train], l2=l2)

    l0_pnl = sum(f.pnl for f in test)
    l1_pnl = 0.0
    gated_in = 0
    l1_wins = 0
    for f in test:
        if platt_prob(a, b, f.p_side) > fee_breakeven_prob(f.p_side, fee_rate):
            l1_pnl += f.pnl
            gated_in += 1
            l1_wins += int(f.won)
    return {
        "a": a,
        "b": b,
        "n_test": len(test),
        "gated_in": gated_in,
        "l0_pnl": l0_pnl,
        "l1_pnl": l1_pnl,
        "delta": l1_pnl - l0_pnl,
        "train_wins": int(sum(f.won for f in train)),
        "train_losses": int(sum(1.0 - f.won for f in train)),
        "test_wins": int(sum(f.won for f in test)),
        "test_losses": int(sum(1.0 - f.won for f in test)),
        "l0_wins": int(sum(f.won for f in test)),
        "l1_wins": l1_wins,
    }


def run_cpcv(
    fills: Sequence[Fill],
    n_groups: int = 6,
    k_test: int = 2,
    embargo_windows: float = 1.0,
    fee_rate: float = 0.07,
    l2: float = 1e-3,
    min_minority: int = 100,
) -> dict[str, Any]:
    """Run CPCV over the fills and aggregate the OOS distribution + a verdict."""
    embargo_s = embargo_windows * WINDOW_SECONDS
    paths: list[dict[str, Any]] = []
    for train_idx, test_idx in cpcv_splits(len(fills), n_groups, k_test):
        train_idx = purge_embargo(fills, train_idx, test_idx, embargo_s)
        if not train_idx or not test_idx:
            continue
        paths.append(score_path(fills, train_idx, test_idx, fee_rate, l2))

    if not paths:
        return {"n_paths": 0, "verdict": "NO PATHS", "paths": []}

    deltas = [p["delta"] for p in paths]
    min_train_losses = min(p["train_losses"] for p in paths)
    n = len(paths)
    l1_beats_l0 = sum(1 for p in paths if p["delta"] > 1e-9)
    l1_positive = sum(1 for p in paths if p["l1_pnl"] > 1e-9)
    mean_delta = statistics.fmean(deltas)

    if min_train_losses < min_minority:
        verdict = "INSUFFICIENT"  # minority (loss) class too thin to trust the fit
    elif mean_delta > 1e-9 and l1_beats_l0 * 2 >= n:
        verdict = "L1 ADDS EDGE"
    else:
        verdict = "NO GAIN"

    return {
        "n_fills": len(fills),
        "n_paths": n,
        "mean_delta": mean_delta,
        "stdev_delta": statistics.pstdev(deltas) if n > 1 else 0.0,
        "pct_l1_beats_l0": l1_beats_l0 / n,
        "pct_l1_positive": l1_positive / n,
        "mean_l0_pnl": statistics.fmean([p["l0_pnl"] for p in paths]),
        "mean_l1_pnl": statistics.fmean([p["l1_pnl"] for p in paths]),
        "min_train_losses": min_train_losses,
        "total_losses": int(sum(1.0 - f.won for f in fills)),
        "total_wins": int(sum(f.won for f in fills)),
        "min_minority": min_minority,
        "verdict": verdict,
        "paths": paths,
    }
