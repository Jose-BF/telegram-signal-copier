"""Neighbours of a candidate: each parameter moved one step on the search grid (genome)
and each filter threshold moved one notch. A robust candidate keeps winning when its
neighbours are tested; an isolated spike is luck."""
from __future__ import annotations

import copy

from research.dubai_iterative.contracts import StrategyGenome

GRIDS = {
    "canal1": {"entry_ladder_step": [2.0, 3.0, 4.0, 5.0, 6.0], "stop_value": [10.0, 15.0, 20.0, 25.0, 35.0, 50.0],
               "profit_lock_arm": [5.0, 8.0, 10.0, 15.0, 20.0], "profit_lock_giveback": [1.0, 2.0, 3.0, 5.0],
               "time_exit_min": [20, 30, 40, 60, 90, 120], "entry_expiry_min": [5, 15, 30],
               "entry_value": [0.5, 1.0, 2.0], "entry_confirmation_value": [0.5, 1.0, 1.5]},
    "canal2": {"entry_value": [0.5, 1.0, 1.5, 2.0, 3.0], "entry_confirmation_value": [0.5, 1.0, 1.5, 2.0],
               "entry_ladder_step": [1.0, 1.5, 2.0, 3.0, 4.0], "profit_lock_arm": [10.0, 20.0, 30.0, 50.0],
               "profit_lock_giveback": [1.0, 2.0, 5.0], "time_exit_min": [60, 120, 180, 240],
               "trailing_distance": [None, 10.0, 20.0, 30.0]},
}
CAPS = [30.0, 45.0, 60.0, 80.0, 100.0, 150.0, 200.0]
LEGSTOPS = [3.0, 5.0, 8.0, 12.0, 20.0]
STEPS = [0.3, 0.5, 0.8, 1.0]
FILTER_STEPS = {"loc_4h": 0.05, "rsi_m5": 5, "rsi_m15": 5, "trend_m15": 0.5, "pre_move_60": 5.0, "pre_move_15": 1.5,
                "atr_m15": 2.0, "pre_range_60": 5.0, "day_move": 10.0, "gap_prev_min": 15.0, "spread": 0.05}


def _adjacent(values, v):
    if v not in values:
        return []
    i = values.index(v)
    return [values[j] for j in (i - 1, i + 1) if 0 <= j < len(values)]


def genome_neighbors(ch, genome: dict) -> list[dict]:
    out = []
    for field, values in GRIDS[ch].items():
        if genome.get(field) is None and None not in values:
            continue
        for nv in _adjacent(values, genome.get(field)):
            g = dict(genome); g[field] = nv
            out.append(g)
    if ch == "canal2":
        if genome.get("stop_mode") == "basket_money":
            for nv in _adjacent(CAPS, genome["stop_value"]):
                out.append({**genome, "stop_value": nv})
        if genome.get("stop_mode") == "fixed_move":
            for nv in _adjacent(LEGSTOPS, genome["stop_value"]):
                out.append({**genome, "stop_value": nv})
        steps = genome.get("target_steps") or []
        if steps:
            base = round(steps[0], 2)
            for nb in _adjacent(STEPS, base):
                out.append({**genome, "target_steps": [round(nb * (i + 1), 2) for i in range(len(steps))]})
    valid = []
    for g in out:
        try:
            sg = StrategyGenome.from_dict(g)
        except TypeError:
            continue
        if not sg.validation_errors():
            valid.append(sg.to_dict())
    return valid


def filter_neighbors(spec: dict) -> list[dict]:
    out = []
    if "hours" in spec:
        a, b = spec["hours"]
        for na, nb in ((a - 1, b), (a + 1, b), (a, b - 1), (a, b + 1)):
            if 0 <= na < nb <= 24:
                out.append({**spec, "hours": [na, nb]})
    for i, (name, op, value) in enumerate(spec.get("cond", [])):
        step = FILTER_STEPS.get(name)
        if step is None:
            continue
        for nv in (value - step, value + step):
            s = copy.deepcopy(spec); s["cond"][i] = [name, op, round(nv, 3)]
            out.append(s)
    return out
