"""Search 2 level-1 calculator (milestone M2): fast approximate outcome of many rules x signals.

Part 1 - simple rules from the first-passage tables of research/signal_paths.py:
one market entry at the signal (entry latency already in the table), direction follow or
invert, take profit X, stop Y (or none), time exit T (or the 6 h / end-of-day cutoff).
If X and Y are first reached in the same second the stop is assumed (worst case). Stops and
targets fill exactly at their level (no slippage): an approximation that the engine check
(search2/check_grid_vs_engine.py) quantifies.

Money: per signal, EUR for 0.01 lot = value_usd_per_oz * 100 oz * 0.01 / fx.
The calculator only ranks zones for the exact engine; no final recipe comes from here.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np

from research.signal_paths import LEVELS, TIME_EXITS_MIN

NEVER = np.iinfo(np.int32).max
TP_GRID = (0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 7.0, 10.0, 15.0, 20.0, 30.0, None)
SL_GRID = (1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 15.0, 20.0, 30.0, 40.0, 60.0, None)
TIME_GRID = TIME_EXITS_MIN + (None,)   # None = hold until the cutoff


@dataclass(frozen=True)
class SimpleRule:
    invert: bool
    tp: float | None
    sl: float | None
    time_min: int | None

    def label(self) -> str:
        return (f"{'invertir' if self.invert else 'seguir'} TP {self.tp or '-'} SL {self.sl or '-'} "
                f"T {self.time_min or 'fin'}")


def simple_grid(tp=TP_GRID, sl=SL_GRID, times=TIME_GRID):
    return [SimpleRule(inv, a, b, t) for inv, a, b, t in itertools.product((False, True), tp, sl, times)
            if not (a is None and b is None and t is None and inv)]


def _level_index(x):
    j = np.flatnonzero(np.isclose(LEVELS, x))
    if not len(j):
        raise ValueError(f"level {x} not in signal_paths.LEVELS")
    return int(j[0])


def simple_outcomes(z: dict, rules) -> np.ndarray:
    """Matrix [signal, rule] of EUR at 0.01 lot. `z` = research.signal_paths.load(...)."""
    n = len(z["ids"])
    i = np.arange(n)
    sig_dir = z["direction"].astype(np.int64)
    eur = 1.0 / z["fx"]                       # 0.01 lot * 100 oz = 1 oz -> USD / fx
    last_s = z["n_seconds"].astype(np.int64) - 1
    out = np.empty((n, len(rules)), dtype=np.float32)
    tcol = {m: j for j, m in enumerate(TIME_EXITS_MIN)}
    for r, rule in enumerate(rules):
        d = 1 - sig_dir if rule.invert else sig_dir
        tf = np.full(n, NEVER, dtype=np.int64)
        ta = np.full(n, NEVER, dtype=np.int64)
        if rule.tp is not None:
            v = z["fp_fav"][i, d, _level_index(rule.tp)].astype(np.int64)
            tf = np.where(v < 0, NEVER, v)
        if rule.sl is not None:
            v = z["fp_adv"][i, d, _level_index(rule.sl)].astype(np.int64)
            ta = np.where(v < 0, NEVER, v)
        if rule.time_min is None:
            t_exit, v_exit = last_s, z["end_val"][i, d]
        else:
            s = rule.time_min * 60 - 1
            censored = s > last_s
            t_exit = np.where(censored, last_s, s)
            v_exit = np.where(censored, z["end_val"][i, d], z["exit_val"][i, d, tcol[rule.time_min]])
        hit = np.minimum(tf, ta) <= t_exit
        stop_first = ta <= tf
        usd = np.where(hit, np.where(stop_first, -(rule.sl or 0.0), rule.tp or 0.0), v_exit)
        out[:, r] = usd * eur
    return out
