"""Signal filters: which signals a strategy takes. Uses only causal features
(research/signal_features.py). A missing feature means "not taken" (conservative).

A filter is a dict, e.g.
  {"hours": [7, 16], "weekdays": [0,1,2,3], "high_risk": False,
   "cond": [["loc_4h", "<", 0.3], ["rsi_m15", "<", 40]]}
hours = [start, end) in UTC; cond uses "<" or ">".
"""
from __future__ import annotations

import itertools
import json
import random

OPS = {"<": lambda a, b: a < b, ">": lambda a, b: a > b}


def load_features(path):
    return {r["id"]: r for r in map(json.loads, open(path)) if r.get("status") == "ok"}


def passes(spec, f) -> bool:
    if not spec:
        return True
    if f is None:
        return False
    if "hours" in spec:
        a, b = spec["hours"]
        if not a <= f["hour_utc"] < b:
            return False
    if "weekdays" in spec and f["weekday"] not in spec["weekdays"]:
        return False
    if spec.get("high_risk") is not None and f["high_risk"] != spec["high_risk"]:
        return False
    if "kinds" in spec and f["kind"] not in spec["kinds"]:
        return False
    for name, op, value in spec.get("cond", []):
        if name not in f or not OPS[op](f[name], value):
            return False
    return True


def describe(spec) -> str:
    """Plain Spanish description for reports."""
    if not spec:
        return "todas las señales"
    parts = []
    if "hours" in spec:
        parts.append(f"de {spec['hours'][0]}:00 a {spec['hours'][1]}:00 UTC")
    if "weekdays" in spec:
        names = "LMXJVSD"
        parts.append("días " + "".join(names[d] for d in spec["weekdays"]))
    if spec.get("high_risk") is not None:
        parts.append("solo High risk" if spec["high_risk"] else "sin High risk")
    labels = {"loc_4h": "posición en rango 4h", "rsi_m5": "RSI5m", "rsi_m15": "RSI15m", "trend_m15": "tendencia15m",
              "pre_move_15": "mov. previo 15m", "pre_move_60": "mov. previo 60m", "pre_range_60": "rango 60m",
              "atr_m15": "ATR15m", "day_move": "mov. del día", "gap_prev_min": "min. desde señal anterior",
              "spread": "spread"}
    for name, op, value in spec.get("cond", []):
        parts.append(f"{labels.get(name, name)} {op} {value}")
    return ", ".join(parts)


# grid used by the search (single conditions and pairs); thresholds from feature quantiles
SINGLE = [
    {"hours": [6, 12]}, {"hours": [12, 17]}, {"hours": [7, 16]}, {"hours": [13, 20]},
    {"weekdays": [0, 1, 2, 3]}, {"weekdays": [1, 2, 3, 4]}, {"weekdays": [0, 1, 2]},
    {"high_risk": False}, {"high_risk": True},
    *[{"cond": [["loc_4h", "<", v]]} for v in (0.2, 0.35, 0.5)],
    *[{"cond": [["loc_4h", ">", v]]} for v in (0.5, 0.65, 0.8)],
    *[{"cond": [["rsi_m15", "<", v]]} for v in (35, 45)],
    *[{"cond": [["rsi_m15", ">", v]]} for v in (55, 65)],
    *[{"cond": [["rsi_m5", "<", v]]} for v in (30, 45)],
    *[{"cond": [["rsi_m5", ">", v]]} for v in (55, 70)],
    *[{"cond": [["trend_m15", ">", v]]} for v in (0.0, 1.0)],
    *[{"cond": [["trend_m15", "<", v]]} for v in (0.0, -1.0)],
    *[{"cond": [["pre_move_60", ">", v]]} for v in (0.0, 10.0)],
    *[{"cond": [["pre_move_60", "<", v]]} for v in (0.0, -10.0)],
    *[{"cond": [["pre_move_15", "<", v]]} for v in (-3.0, 0.0)],
    *[{"cond": [["pre_move_15", ">", v]]} for v in (0.0, 3.0)],
    *[{"cond": [["atr_m15", "<", v]]} for v in (8.0, 12.0)],
    *[{"cond": [["atr_m15", ">", v]]} for v in (8.0, 12.0)],
    *[{"cond": [["pre_range_60", "<", v]]} for v in (15.0, 25.0)],
    *[{"cond": [["day_move", ">", v]]} for v in (0.0, 20.0)],
    *[{"cond": [["day_move", "<", v]]} for v in (0.0, -20.0)],
    *[{"cond": [["gap_prev_min", ">", v]]} for v in (15.0, 60.0)],
]


def merge(a, b):
    out = {k: v for k, v in a.items() if k != "cond"}
    for k, v in b.items():
        if k != "cond":
            if k in out and out[k] != v:
                return None
            out[k] = v
    cond = a.get("cond", []) + b.get("cond", [])
    if len({c[0] for c in cond}) < len(cond):
        return None
    if cond:
        out["cond"] = cond
    return out


def grid(max_pairs=400, seed=0):
    """No filter + every single filter + a seeded sample of pairs."""
    pairs = [m for a, b in itertools.combinations(SINGLE, 2) if (m := merge(a, b))]
    random.Random(seed).shuffle(pairs)
    return [{}] + list(SINGLE) + pairs[:max_pairs]


# search 2 (M5): conditions on the v2 features (research/signal_features_v2.py)
SINGLE_V2 = [
    *[{"cond": [["session", ">", s - 0.5], ["session", "<", s + 0.5]]} for s in (0, 1, 2, 3)],
    *[{"cond": [["min_in_session", "<", v]]} for v in (30, 90)],
    *[{"cond": [["pd_loc", "<", v]]} for v in (0.2, 0.4)],
    *[{"cond": [["pd_loc", ">", v]]} for v in (0.6, 0.8)],
    {"cond": [["pd_break", ">", 0.5]]}, {"cond": [["pd_break", "<", -0.5]]},
    {"cond": [["pd_break", ">", -0.5], ["pd_break", "<", 0.5]]},
    *[{"cond": [["asia_loc", "<", v]]} for v in (0.2, 0.5)],
    *[{"cond": [["asia_loc", ">", v]]} for v in (0.5, 0.8)],
    {"cond": [["sweep_fav", ">", 0.5]]}, {"cond": [["sweep_adv", ">", 0.5]]},
    {"cond": [["sweep_fav", "<", 0.5], ["sweep_adv", "<", 0.5]]},
    *[{"cond": [["dist_round50", "<", v]]} for v in (3.0, 8.0)],
    *[{"cond": [["dist_round50", ">", v]]} for v in (15.0,)],
    *[{"cond": [["vol_regime", "<", v]]} for v in (0.8, 1.0)],
    *[{"cond": [["vol_regime", ">", v]]} for v in (1.2, 1.6)],
    {"cond": [["news_30", "<", 0.5]]}, {"cond": [["news_day", "<", 0.5]]}, {"cond": [["news_day", ">", 0.5]]},
    *[{"cond": [["burst_60", "<", v]]} for v in (0.5, 1.5)],
    *[{"cond": [["burst_60", ">", v]]} for v in (0.5, 1.5)],
    *[{"cond": [["dir_streak", "<", v]]} for v in (0.5,)],
    *[{"cond": [["dir_streak", ">", v]]} for v in (0.5, 1.5)],
    {"cond": [["prev_outcome", ">", 0.5]]}, {"cond": [["prev_outcome", "<", -0.5]]},
]


def _merge_v2(a, b):
    """Like merge(), but a feature may appear twice when it forms a range (session, pd_break)."""
    out = {k: v for k, v in a.items() if k != "cond"}
    for k, v in b.items():
        if k != "cond":
            if k in out and out[k] != v:
                return None
            out[k] = v
    ca, cb = a.get("cond", []), b.get("cond", [])
    if {c[0] for c in ca} & {c[0] for c in cb}:
        return None
    if ca or cb:
        out["cond"] = ca + cb
    return out


def grid_v2(max_pairs=800, seed=0):
    """v1 grid + v2 singles + a seeded sample of pairs (v2 x v2 and v2 x v1 singles)."""
    pool = SINGLE + SINGLE_V2
    pairs = [m for a, b in itertools.combinations(pool, 2)
             if (a in SINGLE_V2 or b in SINGLE_V2) and (m := _merge_v2(a, b))]
    random.Random(seed).shuffle(pairs)
    return grid() + list(SINGLE_V2) + pairs[:max_pairs]
