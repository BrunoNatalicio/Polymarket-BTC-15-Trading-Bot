"""Platt-scaling calibrator for the fusion L1 EV gate.

Fits ``P(win | x) = sigmoid(a*x + b)`` on ``(feature, label)`` pairs by
L2-regularised logistic regression (IRLS). For L1 the feature ``x`` is the
favourite's entry probability (``p_side``) and the label is whether the bought
side won. The EV gate then trades a favourite only when its **calibrated**
win-probability clears the fee-adjusted breakeven (``tv_market_select.
fee_breakeven_prob``) — the smooth, data-driven version of the hard
"follow the favourite > 0.60" rule. Pure numpy; no sklearn.

WARNING: fitting and evaluating on the same data is **in-sample** (leakage).
Use a purged/embargoed split (CPCV) before trusting L1 live — see
``.context/docs/fusion-strategy.md`` §7. The ``--gate ev`` smoke test fits
in-sample on purpose, to exercise the pipeline, not to prove an out-of-sample edge.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

import numpy as np

_Z_CLIP = 30.0  # clamp a*x+b so exp never overflows


def fit_platt(
    samples: Sequence[tuple[float, float]], l2: float = 1e-3, iters: int = 50
) -> tuple[float, float]:
    """Fit ``sigmoid(a*x + b)`` to ``(x, y)`` pairs via ridge-regularised IRLS.

    ``y`` is a 0/1 label. The L2 ridge keeps ``(a, b)`` finite when the data is
    (near-)separable — favourites win almost always, which would otherwise send
    the coefficients to infinity. Returns ``(0.0, 0.0)`` for empty input.
    """
    if not samples:
        return 0.0, 0.0
    x = np.asarray([s[0] for s in samples], dtype=float)
    y = np.asarray([s[1] for s in samples], dtype=float)
    design = np.column_stack([x, np.ones_like(x)])  # columns map to (a, b)
    w = np.zeros(2)
    ridge = l2 * np.eye(2)
    for _ in range(iters):
        z = np.clip(design @ w, -_Z_CLIP, _Z_CLIP)
        p = 1.0 / (1.0 + np.exp(-z))
        weights = p * (1.0 - p)
        grad = design.T @ (p - y) + l2 * w
        hess = design.T @ (design * weights[:, None]) + ridge
        try:
            step = np.linalg.solve(hess, grad)
        except np.linalg.LinAlgError:
            break
        w = w - step
        if float(np.max(np.abs(step))) < 1e-9:
            break
    return float(w[0]), float(w[1])


def platt_prob(a: float, b: float, x: float) -> float:
    """Calibrated win-probability ``sigmoid(a*x + b)`` (overflow-safe)."""
    z = max(-_Z_CLIP, min(_Z_CLIP, a * x + b))
    return 1.0 / (1.0 + math.exp(-z))


def ev_gate(a: float, b: float, fee_rate: float) -> Callable[[str, float], bool]:
    """Build a ``gate_fn(direction, p_side) -> bool`` for ``run_fusion_replay``.

    Trades only when the calibrated win-probability of the bought favourite beats
    the fee-adjusted breakeven for its entry price — i.e. the bet is +EV net of
    the taker fee. ``direction`` is unused (the gate is symmetric in UP/DOWN; the
    calibrator already saw both sides), kept for the seam's signature.
    """
    from tv_market_select import fee_breakeven_prob

    def gate(_direction: str, p_side: float) -> bool:
        return platt_prob(a, b, p_side) > fee_breakeven_prob(p_side, fee_rate)

    return gate
