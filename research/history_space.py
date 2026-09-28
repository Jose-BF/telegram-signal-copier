"""Coarse strategy spaces around the live strategies (trigger-only, own management).

family "A": only parameters the live strategies already use (deployable by
configuration); "B_cap": Gold basket loss cap, "B_leg": Gold per-leg price stop (need bot work before
live use). Sampling is seeded and recorded.
"""
from __future__ import annotations

import random

from tools.run_week_causal_controls import policies


def live():
    return {k: v.with_change(provider_management_mode="ignore") for k, v in policies().items()}


def sample_canal1(rng: random.Random):
    base = live()["canal1"]
    legs = rng.choice([2, 3, 3, 4])
    weights = rng.choice({2: [(0.01, 0.04), (0.02, 0.02), (0.01, 0.02)],
                          3: [(0.01, 0.04, 0.04), (0.01, 0.02, 0.04), (0.02, 0.02, 0.02), (0.01, 0.01, 0.02)],
                          4: [(0.01, 0.02, 0.03, 0.04), (0.01, 0.01, 0.02, 0.04), (0.01, 0.02, 0.02, 0.02)]}[legs])
    entry = rng.choice(["signal_market"] * 3 + ["pullback", "adverse_reversal"])
    changes = dict(leg_count=legs, volume_weights=weights, entry_ladder_step=rng.choice([2.0, 3.0, 4.0, 5.0, 6.0]),
                   stop_value=rng.choice([10.0, 15.0, 20.0, 25.0, 35.0, 50.0]),
                   profit_lock_arm=rng.choice([5.0, 8.0, 10.0, 15.0, 20.0]),
                   profit_lock_giveback=rng.choice([1.0, 2.0, 3.0, 5.0]),
                   time_exit_min=rng.choice([20, 30, 40, 60, 90, 120]),
                   time_exit_mode=rng.choice(["loss_only", "always", "none", "non_negative"]),
                   entry_expiry_min=rng.choice([5, 15, 30]), entry_mode=entry,
                   entry_value=None, entry_confirmation_value=None)
    if entry == "pullback":
        changes["entry_value"] = rng.choice([0.5, 1.0, 2.0])
    elif entry == "adverse_reversal":
        changes["entry_value"] = rng.choice([0.5, 1.0, 2.0]); changes["entry_confirmation_value"] = rng.choice([0.5, 1.0, 1.5])
    if changes["profit_lock_giveback"] >= changes["profit_lock_arm"]:
        changes["profit_lock_giveback"] = changes["profit_lock_arm"] / 2
    return base.with_change(**changes), "A"


def sample_canal2(rng: random.Random):
    base = live()["canal2"]
    legs = rng.choice([3, 4, 5, 5])
    first = rng.choice([0.03, 0.04]); rest = rng.choice([0.02, 0.03])
    step = rng.choice([0.3, 0.5, 0.8, 1.0])
    changes = dict(leg_count=legs, volume_weights=(first,) + (rest,) * (legs - 1),
                   target_steps=tuple(round(step * (i + 1), 2) for i in range(legs)),
                   entry_value=rng.choice([0.5, 1.0, 1.5, 2.0, 3.0]), entry_confirmation_value=rng.choice([0.5, 1.0, 1.5, 2.0]),
                   entry_ladder_step=rng.choice([1.0, 1.5, 2.0, 3.0, 4.0]),
                   profit_lock_arm=rng.choice([10.0, 20.0, 30.0, 50.0]), profit_lock_giveback=rng.choice([1.0, 2.0, 5.0]),
                   trailing_distance=rng.choice([None, 10.0, 20.0, 30.0]),
                   time_exit_min=rng.choice([60, 120, 180, 240]),
                   time_exit_mode=rng.choice(["non_negative", "always", "loss_only", "none"]))
    family = "A"
    u = rng.random()
    if u < 0.45:  # basket loss cap (client-side market close of the whole basket)
        changes.update(stop_mode="basket_money", stop_value=rng.choice([30.0, 45.0, 60.0, 80.0, 100.0, 150.0, 200.0])); family = "B_cap"
    elif u < 0.65:  # per-leg price stop (USD distance)
        changes.update(stop_mode="fixed_move", stop_value=rng.choice([3.0, 5.0, 8.0, 12.0, 20.0])); family = "B_leg"
    if rng.random() < 0.3:  # enter at market instead of waiting for the adverse/rebound
        changes.update(entry_mode="signal_market", entry_value=None, entry_confirmation_value=None)
    return base.with_change(**changes), family


def population(n_per_channel: int, seed: int):
    rng = random.Random(seed)
    out = {"canal1": [(live()["canal1"], "live")], "canal2": [(live()["canal2"], "live")]}
    for ch, sampler in (("canal1", sample_canal1), ("canal2", sample_canal2)):
        seen = {out[ch][0][0].fingerprint}
        while len(out[ch]) < n_per_channel + 1:
            g, fam = sampler(rng)
            if g.validation_errors() or g.fingerprint in seen:
                continue
            seen.add(g.fingerprint); out[ch].append((g, fam))
    return out
