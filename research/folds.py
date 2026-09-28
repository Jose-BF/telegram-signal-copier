"""Time splits by whole months and probability of backtest overfitting (search 2, M4).

A split is (choose_months, check_months). Families:
  forward / backward      first half -> second half, and the reverse
  rolling                 3 consecutive months -> the next 2, stepping one month
  leave_one_out           all months but one -> that month
  cscv                    every way to split the months into two equal halves (Bailey et al.)
  recent_first / recent_last  (vertiente S4) last 3 months -> the rest, and the reverse

pbo(M, splits): M is [month, candidate] (net per month). For each split the best candidate
on the choose side is ranked on the check side; PBO = share of splits where it falls at or below
the median (relative rank <= 0.5), as in CSCV. Also returns how often the chosen one is simply
positive on the check side, which is what matters for money.
"""
from __future__ import annotations

import itertools

import numpy as np


def splits(months: list[str], families=("forward", "backward", "rolling", "leave_one_out", "cscv",
                                         "recent_first", "recent_last")) -> list[tuple[str, tuple, tuple]]:
    ms = sorted(months)
    n = len(ms)
    out = []
    half = n // 2
    if "forward" in families:
        out.append(("forward", tuple(ms[:half]), tuple(ms[half:])))
    if "backward" in families:
        out.append(("backward", tuple(ms[half:]), tuple(ms[:half])))
    if "rolling" in families:
        for i in range(0, n - 4):
            out.append(("rolling", tuple(ms[i:i + 3]), tuple(ms[i + 3:i + 5])))
    if "leave_one_out" in families:
        for m in ms:
            out.append(("leave_one_out", tuple(x for x in ms if x != m), (m,)))
    if "cscv" in families:
        k = n // 2
        for combo in itertools.combinations(range(n), k):
            a = tuple(ms[i] for i in combo)
            b = tuple(ms[i] for i in range(n) if i not in combo)
            out.append(("cscv", a, b))
    if "recent_first" in families and n >= 6:
        out.append(("recent_first", tuple(ms[-3:]), tuple(ms[:-3])))
    if "recent_last" in families and n >= 6:
        out.append(("recent_last", tuple(ms[:-3]), tuple(ms[-3:])))
    return out


def pbo(M: np.ndarray, months: list[str], split_list) -> dict:
    """M[month_index, candidate] = net of that candidate in that month (months aligned with `months`)."""
    idx = {m: i for i, m in enumerate(months)}
    rel_ranks, positive, by_family = [], [], {}
    for fam, a, b in split_list:
        ia = [idx[m] for m in a]; ib = [idx[m] for m in b]
        choose = M[ia].sum(0); check = M[ib].sum(0)
        best = int(np.argmax(choose))
        # relative rank of the chosen candidate on the check side, in (0, 1]
        rank = (np.sum(check < check[best]) + 0.5 * (np.sum(check == check[best]) - 1) + 1) / len(check)
        rel_ranks.append(rank); positive.append(check[best] > 0)
        f = by_family.setdefault(fam, {"n": 0, "below_median": 0, "positive": 0})
        f["n"] += 1; f["below_median"] += int(rank <= 0.5); f["positive"] += int(check[best] > 0)
    rel = np.array(rel_ranks)
    return {"pbo": float(np.mean(rel <= 0.5)) if len(rel) else None,
            "chosen_positive_out_of_sample": float(np.mean(positive)) if positive else None,
            "n_splits": len(rel), "by_family": by_family}


def oos_record(M: np.ndarray, months: list[str], split_list, candidate: int) -> dict:
    """For one fixed candidate: share of splits where it is positive on the check side."""
    idx = {m: i for i, m in enumerate(months)}
    pos = [M[[idx[m] for m in b], candidate].sum() > 0 for _, _, b in split_list]
    return {"positive_share": float(np.mean(pos)) if pos else None, "n": len(pos)}
