"""Polarization index (0–100) for one analysis unit (topic / subtopic / event).

For each axis (綠藍 lean, 抗中友中 China stance):
  1. eligible outlets have ≥ m articles scored on that axis (K outlets);
  2. outlet position x_o = mean score / 5 ∈ [−1, 1], shrunk toward the unit mean:
         x̃_o = (n_o·x_o + κ·x̄) / (n_o + κ)
  3. P_axis = 100 · (1/K²) Σ_o Σ_p |x̃_o − x̃_p|        (Gini mean difference; Esteban–Ray α = 0)
     0 when all outlets agree; 100 when outlets split evenly between the two extremes.
P = mean of the available axes. The China axis is used only when enough of the unit's coverage
involves China; otherwise P = P_lean. Outlets are weighted equally.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class Params:
    min_articles_per_outlet: int = 3
    shrinkage_kappa: float = 5.0
    min_total_articles: int = 15
    min_outlets: int = 4
    china_min_share: float = 0.30
    china_min_outlets: int = 3
    bootstrap: int = 1000
    ci: float = 0.90


def gini_mean_difference(x: np.ndarray) -> float:
    """100 · mean |x_i − x_j| over all ordered pairs (including i = j), for x in [−1, 1]."""
    k = len(x)
    if k < 2:
        return math.nan
    return float(100.0 * np.abs(x[:, None] - x[None, :]).sum() / (k * k))


def _positions(groups: list[np.ndarray], kappa: float) -> np.ndarray:
    ns = np.array([len(g) for g in groups], dtype=float)
    means = np.array([g.mean() for g in groups]) / 5.0
    grand = float(np.concatenate(groups).mean()) / 5.0
    return (ns * means + kappa * grand) / (ns + kappa)


def axis_index(groups: list[np.ndarray], kappa: float) -> float:
    return gini_mean_difference(_positions(groups, kappa)) if len(groups) >= 2 else math.nan


def _eligible(scores_by_outlet: dict[str, np.ndarray], m: int) -> dict[str, np.ndarray]:
    return {o: s for o, s in scores_by_outlet.items() if len(s) >= m}


def _bootstrap(groups: list[np.ndarray], kappa: float, b: int, rng: np.random.Generator) -> np.ndarray:
    """B replicate indices for one axis, resampling articles within each outlet."""
    k = len(groups)
    ns = np.array([len(g) for g in groups], dtype=float)
    reps = np.empty((b, k))
    for j, g in enumerate(groups):
        idx = rng.integers(0, len(g), size=(b, len(g)))
        reps[:, j] = g[idx].mean(axis=1) / 5.0
    grand = (reps * ns).sum(axis=1, keepdims=True) / ns.sum()
    pos = (ns * reps + kappa * grand) / (ns + kappa)
    diffs = np.abs(pos[:, :, None] - pos[:, None, :]).sum(axis=(1, 2))
    return 100.0 * diffs / (k * k)


def polarization(
    lean_by_outlet: dict[str, list[float]],
    china_by_outlet: dict[str, list[float]],
    n_total: int,
    params: Params | None = None,
    seed: int = 0,
) -> dict:
    """lean_by_outlet: every scored article's lean per outlet; china_by_outlet: only articles
    with china_relevance ≥ 1. n_total: all in-scope articles in the unit."""
    p = params or Params()
    lean = _eligible({o: np.asarray(v, float) for o, v in lean_by_outlet.items()}, p.min_articles_per_outlet)
    china = _eligible({o: np.asarray(v, float) for o, v in china_by_outlet.items()}, p.min_articles_per_outlet)
    n_china = sum(len(v) for v in china_by_outlet.values())
    use_china = len(china) >= p.china_min_outlets and n_total > 0 and n_china / n_total >= p.china_min_share

    lean_groups = list(lean.values())
    china_groups = list(china.values())
    p_lean = axis_index(lean_groups, p.shrinkage_kappa)
    p_china = axis_index(china_groups, p.shrinkage_kappa) if use_china else math.nan
    value = float(np.nanmean([p_lean, p_china])) if use_china else p_lean

    lo = hi = math.nan
    if len(lean_groups) >= 2 and p.bootstrap > 0:
        rng = np.random.default_rng(seed)
        reps = _bootstrap(lean_groups, p.shrinkage_kappa, p.bootstrap, rng)
        if use_china:
            reps = (reps + _bootstrap(china_groups, p.shrinkage_kappa, p.bootstrap, rng)) / 2
        alpha = (1 - p.ci) / 2
        lo, hi = (float(x) for x in np.quantile(reps, [alpha, 1 - alpha]))

    sufficient = n_total >= p.min_total_articles and len(lean_groups) >= p.min_outlets
    return {
        "P": _round(value),
        "P_lean": _round(p_lean),
        "P_china": _round(p_china),
        "ci_low": _round(lo),
        "ci_high": _round(hi),
        "china_axis_used": bool(use_china),
        "n_outlets": len(lean_groups),
        "n_outlets_china": len(china_groups) if use_china else 0,
        "n_articles": int(n_total),
        "sufficient": bool(sufficient),
    }


def attention_skew(attention: dict[str, float], baseline: dict[str, float]) -> float | None:
    """Coverage-weighted mean of outlets' baseline lean minus the unweighted mean (lean units).
    attention[o] = share of outlet o's political output devoted to this unit.
    > 0: the unit is mostly covered by blue-leaning outlets; < 0: by green-leaning outlets."""
    common = [o for o in attention if o in baseline and baseline[o] is not None and not math.isnan(baseline[o])]
    total = sum(attention[o] for o in common)
    if len(common) < 2 or total <= 0:
        return None
    weighted = sum(attention[o] * baseline[o] for o in common) / total
    return _round(weighted - sum(baseline[o] for o in common) / len(common), 2)


def _round(x: float, nd: int = 1) -> float | None:
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), nd)
