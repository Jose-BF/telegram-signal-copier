"""Walk-forward selection inside a family (search 2, M7): the honest money estimate.

For every month m from `start`, the procedure picks the best (filter, genome) of the family using
only months before m (expanding window, or the last `window` months) and "trades" month m with it.
The sequence of traded months is what the procedure would have earned. The same procedure on a
placebo universe measures what luck alone earns.

Selection score in the choose window: t = mean / sd * sqrt(n) over the baskets taken, eligible
only with n >= n_min baskets and a positive net. If nothing is eligible the month is not traded.
"""
from __future__ import annotations

import numpy as np

from research.signal_filters import passes


def filter_matrix(ids, feats: dict, filters) -> np.ndarray:
    return np.array([[passes(f, feats.get(i)) for i in ids] for f in filters], dtype=np.float64)


def month_stats(pnl: np.ndarray, months: np.ndarray, month_list, F: np.ndarray):
    """pnl [n, G] (NaN = not filled) -> S, Q, N arrays [month, filter, genome]."""
    filled = (~np.isnan(pnl)).astype(np.float64)
    P = np.nan_to_num(pnl.astype(np.float64))
    M, nF, G = len(month_list), F.shape[0], P.shape[1]
    S = np.zeros((M, nF, G)); Q = np.zeros_like(S); N = np.zeros_like(S)
    for k, m in enumerate(month_list):
        sel = months == m
        Fm = F[:, sel]
        S[k] = Fm @ P[sel]; Q[k] = Fm @ (P[sel] ** 2); N[k] = Fm @ filled[sel]
    return S, Q, N


def score(S, Q, N, rows, n_min):
    s, q, n = S[rows].sum(0), Q[rows].sum(0), N[rows].sum(0)
    with np.errstate(divide="ignore", invalid="ignore"):
        mean = s / n
        var = np.maximum(q / n - mean ** 2, 0) * n / np.maximum(n - 1, 1)
        t = mean / np.sqrt(var) * np.sqrt(n)
    ok = (n >= n_min) & (s > 0) & np.isfinite(t)
    return np.where(ok, t, -np.inf)


def walk_forward(S, Q, N, month_list, start=3, n_min=30, window=None, allowed=None):
    """allowed: optional bool mask [filter, genome] (e.g. carril constraints)."""
    traded = []
    for i in range(start, len(month_list)):
        rows = list(range(max(0, i - window), i)) if window else list(range(i))
        sc = score(S, Q, N, rows, n_min)
        if allowed is not None:
            sc = np.where(allowed, sc, -np.inf)
        if not np.isfinite(sc.max()):
            traded.append({"month": month_list[i], "net": 0.0, "n": 0, "choice": None}); continue
        f, g = np.unravel_index(int(np.argmax(sc)), sc.shape)
        traded.append({"month": month_list[i], "net": float(S[i, f, g]), "n": int(N[i, f, g]),
                       "choice": (int(f), int(g)), "choose_t": float(sc[f, g])})
    nets = [x["net"] for x in traded]
    choices = [x["choice"] for x in traded if x["choice"] is not None]
    return {"months": traded, "total": float(sum(nets)), "pos_months": int(sum(v > 0 for v in nets)),
            "traded_months": int(sum(1 for x in traded if x["choice"] is not None)), "n_months": len(traded),
            "distinct_choices": len(set(choices))}


def final_pick(S, Q, N, n_min=30, allowed=None):
    sc = score(S, Q, N, list(range(S.shape[0])), n_min)
    if allowed is not None:
        sc = np.where(allowed, sc, -np.inf)
    if not np.isfinite(sc.max()):
        return None
    f, g = np.unravel_index(int(np.argmax(sc)), sc.shape)
    return int(f), int(g), float(sc[f, g])
