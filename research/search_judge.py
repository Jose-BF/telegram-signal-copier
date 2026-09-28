"""Stage-1 judge: every (management genome x signal filter) on the SEARCH period.

score = t = mean / sd * sqrt(n) of the per-basket results (rewards steady profit,
penalises small samples and erratic results). Eligible only with n >= MIN_N and a
positive net in BOTH halves of the search period. The luck bar is the best eligible
score reached with random-time placebo signals over the very same set of
combinations: a real combination only counts if it clearly beats it.
"""
from __future__ import annotations

import glob
import gzip
import json
from pathlib import Path

import numpy as np

from research.signal_filters import grid, load_features, passes

MIN_N = {"canal1": 40, "canal2": 30}


def load_rows(stage_dir, universe):
    rows = []
    for f in sorted(glob.glob(f"{stage_dir}/rows_{universe}_*.jsonl.gz")):
        with gzip.open(f, "rt") as fh:
            rows += [json.loads(l) for l in fh]
    return rows


def matrices(rows, ch, n_genomes, sig_ids):
    idx = {s: i for i, s in enumerate(sig_ids)}
    P = np.zeros((len(sig_ids), n_genomes)); C = np.zeros_like(P)
    for rch, gi, day, tid, pnl, dd, cens in rows:
        if rch != ch or tid not in idx or pnl is None:
            continue
        P[idx[tid], gi] = pnl; C[idx[tid], gi] = 1
    return P, C


def score_all(P, C, F, H1):
    n = F @ C
    net = F @ P
    sq = F @ (P * P)
    with np.errstate(divide="ignore", invalid="ignore"):
        mean = net / n
        var = np.maximum(sq / n - mean ** 2, 0) * n / np.maximum(n - 1, 1)
        t = mean / np.sqrt(var) * np.sqrt(n)
    h1 = (F * H1) @ P
    h2 = net - h1
    return n, net, t, h1, h2


def judge(stage_dir, universes_features, n_genomes, search_split, filters=None):
    filters = filters or grid()
    out = {}
    for ch in ("canal1", "canal2"):
        res = {}
        for uni, feat_path in universes_features.items():
            feats = load_features(feat_path)
            rows = load_rows(stage_dir, uni)
            gmax = max((r[1] for r in rows if r[0] == ch), default=-1) + 1
            sig_ids = sorted({r[3] for r in rows if r[0] == ch})
            P, C = matrices(rows, ch, gmax, sig_ids)
            F = np.array([[passes(f, feats.get(s)) for s in sig_ids] for f in filters], dtype=float)
            day_of = {r[3]: r[2] for r in rows if r[0] == ch}
            H1 = np.array([day_of[s] < search_split[ch] for s in sig_ids], dtype=float)
            n, net, t, h1, h2 = score_all(P, C, F, H1)
            eligible = (n >= MIN_N[ch]) & (h1 > 0) & (h2 > 0) & np.isfinite(t)
            res[uni] = {"n": n, "net": net, "t": np.where(eligible, t, -np.inf), "h1": h1, "h2": h2,
                        "P": P, "C": C, "F": F, "sig_ids": sig_ids, "genomes": gmax}
        out[ch] = res
    return out, filters
