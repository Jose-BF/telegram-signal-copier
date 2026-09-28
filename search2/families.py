"""Search 2 families (M6): each family is one hypothesis with a small parameter grid, run with
the exact engine on the real universe and on a placebo universe. Catalogue codes in brackets
(plan-busqueda-2-ejecucion.md section 2).

Inverted families run on the flipped universe (same time, opposite direction) and their
placebo is the random-time placebo with flipped direction (built by `ensure_flipped_placebo`).
carril: "A" (fondeo: designed stop required) / "B" (own capital: 555-style, stop optional).
"""
from __future__ import annotations

import itertools
import json
import random
from dataclasses import dataclass
from pathlib import Path

from research.basket_bridge import rule_to_genome
from research.basket_grid import BasketRule

U = "runtime_data/signal_universe_v1/"
REAL, FLIP = U + "signals.jsonl", U + "placebo_flipped_s0.jsonl"
PLAC, PLAC_FLIP = U + "placebo_random_time_s0.jsonl", U + "placebo_random_time_s0_flipped.jsonl"


def placebo_universes(seed: int):
    """(same-direction, flipped) random-time placebo universes of a seed; builds the flipped file."""
    base = U + f"placebo_random_time_s{seed}.jsonl"
    flip = U + f"placebo_random_time_s{seed}_flipped.jsonl"
    if not Path(flip).exists():
        rows = [json.loads(l) for l in open(base, encoding="utf-8")]
        for r in rows:
            r["direction"] = "SELL" if r["direction"] == "BUY" else "BUY"
            r["id"] = r["id"] + "_f"
        Path(flip).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return base, flip


def ensure_flipped_placebo():
    dst = Path(PLAC_FLIP)
    if not dst.exists():
        rows = [json.loads(l) for l in open(PLAC, encoding="utf-8")]
        for r in rows:
            r["direction"] = "SELL" if r["direction"] == "BUY" else "BUY"
            r["id"] = r["id"] + "_f"
        dst.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return str(dst)


@dataclass(frozen=True)
class Family:
    name: str
    channel: str
    universe: str
    placebo: str
    carril: str
    codes: str
    rules: tuple

    def genomes(self):
        return [rule_to_genome(r) for r in self.rules]


def _single(tps, sls, times):
    out = []
    for tp, sl, t in itertools.product(tps, sls, times):
        out.append(BasketRule(weights=(0.01,), tp_steps=(tp,) if tp else (), sl_move=sl or 0.0,
                              time_min=t or 0.0, time_mode="always", expiry_min=15))
    return tuple(out)


def _sample(rules, n, seed):
    rules = list(dict.fromkeys(rules))
    random.Random(seed).shuffle(rules)
    return tuple(rules[:n])


def families():
    single = _single((5.0, 10.0, 15.0, 20.0, 30.0, None), (10.0, 20.0, 40.0, None), (30, 60, 90, 120, 240))
    ladder_c1 = [BasketRule(weights=w, step=s, basket_stop_eur=c, lock_arm_eur=la, lock_give_eur=lg,
                            time_min=t, time_mode="loss_only", expiry_min=15)
                 for w in ((0.01, 0.04, 0.04), (0.01, 0.02, 0.04), (0.02, 0.02, 0.02), (0.01, 0.01, 0.02, 0.04))
                 for s in (2.0, 3.0, 4.0, 6.0) for c in (15.0, 25.0, 35.0, 50.0, 80.0)
                 for la, lg in ((10.0, 2.0), (15.0, 5.0), (0.0, 0.0)) for t in (30, 60, 120)]
    w555 = (0.04, 0.03, 0.03, 0.03, 0.03)
    base555 = dict(entry="adverse_reversal", weights=w555, trail=30.0, lock_arm_eur=30.0, lock_give_eur=1.0,
                   time_min=180, time_mode="non_negative", expiry_min=30)
    f555 = [BasketRule(e_x=x, e_y=y, step=s, tp_steps=tuple(round(k * v, 2) for v in (0.5, 1.0, 1.5, 2.0, 2.5)), **base555)
            for x in (0.5, 1.0, 1.5, 2.0, 3.0) for y in (0.5, 1.0, 1.5, 2.0) for s in (1.0, 1.5, 2.0, 3.0) for k in (0.5, 1.0)]
    f555_cap = [BasketRule(e_x=x, e_y=y, step=s, basket_stop_eur=c,
                           tp_steps=tuple(round(k * v, 2) for v in (0.5, 1.0, 1.5, 2.0, 2.5)),
                           entry="adverse_reversal", weights=w555, time_min=180, time_mode="non_negative", expiry_min=30)
                for x in (0.5, 1.0, 1.5, 2.0, 3.0) for y in (0.5, 1.0, 1.5, 2.0) for s in (1.0, 1.5, 2.0, 3.0)
                for k in (0.5, 1.0) for c in (30.0, 60.0, 100.0)]
    ladder_c2 = [BasketRule(weights=(0.01,) * n, step=s, basket_stop_eur=c, lock_arm_eur=la, lock_give_eur=lg,
                            time_min=t, time_mode=m, expiry_min=15)
                 for n in (3, 4, 6) for s in (0.5, 1.0, 1.5, 2.0) for c in (30.0, 60.0, 100.0)
                 for la, lg in ((15.0, 5.0), (30.0, 10.0)) for t, m in ((3, "profit_only"), (30, "loss_only"), (120, "loss_only"))]
    pullback = [BasketRule(entry="pullback", e_x=x, weights=(0.01,), tp_steps=(tp,), sl_move=sl, expiry_min=e)
                for x in (0.5, 1.0, 2.0, 3.0) for tp in (1.0, 2.0, 3.0, 5.0) for sl in (3.0, 5.0, 10.0) for e in (15, 30)]
    return [
        Family("c1_invertir_1pata", "canal1", FLIP, PLAC_FLIP, "A", "D2 E1 L1 X1 X7 R1 R2", single),
        Family("c1_seguir_1pata", "canal1", REAL, PLAC, "A", "D1 E1 L1 X1 X7 R1 R2", single),
        Family("c1_escalera", "canal1", REAL, PLAC, "A", "D1 E1 L2 L4 X5 X7 R3", _sample(ladder_c1, 240, 1)),
        Family("c1_invertir_escalera", "canal1", FLIP, PLAC_FLIP, "A", "D2 E1 L2 L4 X5 X7 R3", _sample(ladder_c1, 160, 2)),
        Family("c2_555", "canal2", REAL, PLAC, "B", "D1 E3 L2 X1 X4 X5 X7 R1", tuple(f555)),
        Family("c2_555_tope", "canal2", REAL, PLAC, "A", "D1 E3 L2 X1 X7 R3", _sample(f555_cap, 160, 3)),
        Family("c2_seguir_1pata", "canal2", REAL, PLAC, "A", "D1 E1 L1 X1 X7 R1 R2", single),
        Family("c2_invertir_1pata", "canal2", FLIP, PLAC_FLIP, "A", "D2 E1 L1 X1 X7 R1 R2", single),
        Family("c2_escalera", "canal2", REAL, PLAC, "A", "D1 E1 L2 X5 X7 R3", tuple(ladder_c2)),
        Family("c2_pullback", "canal2", REAL, PLAC, "A", "D1 E2 L1 X1 R2", tuple(pullback)),
    ]
