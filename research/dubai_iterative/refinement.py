"""Deterministic one-block neighborhoods for bounded self-refinement."""

from __future__ import annotations

import math

from .contracts import SearchSpace, StrategyGenome
from .evolution import deduplicate


def parameter_neighborhood(
    parent: StrategyGenome,
    search_space: SearchSpace,
) -> tuple[StrategyGenome, ...]:
    """Explore every strategy block without recursive or unbounded expansion."""

    proposals: list[StrategyGenome] = []

    def propose(reason: str, **changes) -> None:
        candidate = parent.with_change(**changes).with_lineage(
            parent_fingerprints=(parent.fingerprint,),
            mutation_reason=reason,
            lineage_depth=parent.lineage_depth + 1,
        )
        if candidate.fingerprint == parent.fingerprint:
            return
        if candidate.validation_errors() or search_space.validation_errors(candidate):
            return
        proposals.append(candidate)

    # Entry timing and price behaviour.
    propose("entry_actual", entry_mode="actual_mt5", entry_value=None)
    for mode, values in (
        ("delay", (0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0)),
        (
            "pullback",
            (0.1, 0.25, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5,
             4.0, 4.5, 5.0, 6.0, 8.0, 10.0, 12.0),
        ),
        (
            "momentum",
            (0.1, 0.25, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5,
             4.0, 4.5, 5.0, 6.0, 8.0, 10.0, 12.0),
        ),
    ):
        for value in values:
            expiry = min(parent.entry_expiry_min, search_space.max_entry_expiry_min)
            propose(
                "entry_family",
                entry_mode=mode,
                entry_value=value,
                entry_expiry_min=expiry,
                time_exit_min=_bounded_exit(parent.time_exit_min, expiry, search_space),
            )
    for expiry in _bounded_values(
        (1, 3, 5, 10, 15, 30, 60, 90, 120, 180, 240),
        search_space.max_entry_expiry_min,
    ):
        changes = {"entry_expiry_min": expiry}
        if parent.entry_mode != "actual_mt5":
            changes["time_exit_min"] = _bounded_exit(
                parent.time_exit_min,
                expiry,
                search_space,
            )
        propose("entry_expiry", **changes)

    # Simultaneous, averaging-down and pyramiding entry structures.
    propose(
        "ladder_simultaneous",
        entry_ladder_mode="simultaneous",
        entry_ladder_step=None,
    )
    if parent.leg_count >= 2:
        for mode in ("adverse", "favourable"):
            for step in (
                0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.75,
                1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 10.0, 12.0,
            ):
                propose(
                    "ladder_structure",
                    entry_ladder_mode=mode,
                    entry_ladder_step=step,
                )

    # Exposure can move both below and far above the observed 0.04 lots.
    for weights in _volume_neighbors(parent, search_space):
        propose(
            "exposure_plan",
            leg_count=len(weights),
            volume_weights=weights,
        )

    # Profit realization families.
    propose(
        "target_provider_legs",
        target_mode="provider_per_leg",
        target_value=None,
        partial_fraction=0.0,
        runner_target=None,
    )
    for target in (1.0, 2.0, 3.0, 4.0, 5.0):
        propose(
            "target_provider_all",
            target_mode="provider_target_all",
            target_value=target,
            partial_fraction=0.0,
            runner_target=None,
        )
    for target in (
        1.0, 2.0, 3.0, 5.0, 8.0, 10.0, 15.0, 20.0, 30.0,
        40.0, 50.0, 60.0, 75.0, 100.0, 120.0,
    ):
        propose(
            "target_basket_money",
            target_mode="fixed_basket",
            target_value=target,
            partial_fraction=0.0,
            runner_target=None,
            **_compatible_lock(parent, target),
        )
    for target in (
        0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0,
        6.0, 8.0, 10.0, 12.0,
    ):
        propose(
            "target_price_move",
            target_mode="fixed_move",
            target_value=target,
            partial_fraction=0.0,
            runner_target=None,
        )
    propose(
        "target_none",
        target_mode="none",
        target_value=None,
        partial_fraction=0.0,
        runner_target=None,
    )
    if _half_close_executable(parent.volume_weights, search_space.volume_step):
        for target, runner in ((1.0, 3.0), (2.0, 6.0), (5.0, 12.0), (10.0, 25.0)):
            propose(
                "target_partial_runner",
                target_mode="partial_runner",
                target_value=target,
                partial_fraction=0.5,
                runner_target=runner,
            )

    # Protection and stop behaviour.
    propose("be_provider", be_mode="provider", be_trigger=None)
    propose("be_none", be_mode="none", be_trigger=None)
    for mode, values in (
        ("price", (0.25, 0.5, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0)),
        ("partial", (0.25, 0.5, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0)),
        ("delayed", (1.0, 2.0, 3.0, 5.0, 10.0, 20.0, 30.0, 60.0)),
    ):
        for trigger in values:
            propose("break_even", be_mode=mode, be_trigger=trigger)
    propose("stop_provider", stop_mode="provider", stop_value=None)
    for value in (
        0.5, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0,
        8.0, 10.0, 12.0, 15.0, 20.0, 30.0,
    ):
        propose("stop_price_move", stop_mode="fixed_move", stop_value=value)
    for value in (
        2.0, 3.0, 5.0, 8.0, 10.0, 12.0, 15.0, 20.0,
        25.0, 30.0, 40.0, 50.0, 60.0, 80.0, 100.0, 120.0,
    ):
        propose("stop_basket_money", stop_mode="basket_money", stop_value=value)
    propose("stop_none", stop_mode="none", stop_value=None)

    propose(
        "profit_lock_none",
        profit_lock_arm=None,
        profit_lock_giveback=None,
    )
    for arm, giveback in (
        (2.0, 0.5),
        (3.0, 1.0),
        (5.0, 1.0),
        (5.0, 2.0),
        (10.0, 2.0),
        (10.0, 5.0),
        (20.0, 5.0),
        (30.0, 10.0),
    ):
        propose(
            "profit_lock",
            profit_lock_arm=arm,
            profit_lock_giveback=giveback,
        )

    for minutes in _bounded_values(
        (1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30,
         40, 45, 60, 75, 90, 120, 150, 180, 240),
        search_space.max_time_exit_min,
    ):
        if (
            parent.entry_mode == "actual_mt5"
            or minutes + parent.entry_expiry_min <= search_space.max_path_horizon_min
        ):
            propose("time_exit", time_exit_min=minutes)
    for mode in ("exact", "close_only", "ignore"):
        propose("provider_management", provider_management_mode=mode)

    propose(
        "context_none",
        context_filter_mode="none",
        context_filter_value=None,
    )
    for mode, values in (
        ("max_spread", (0.15, 0.20, 0.30, 0.50, 0.80)),
        ("time_window", (8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 22.0)),
        ("max_volatility", (0.5, 1.0, 2.0, 4.0, 8.0, 12.0)),
        ("min_reward_risk", (0.25, 0.5, 1.0, 1.5, 2.0, 3.0)),
    ):
        for value in values:
            propose("context_filter", context_filter_mode=mode, context_filter_value=value)

    return tuple(sorted(
        deduplicate(proposals),
        key=lambda item: item.fingerprint,
    ))


def _volume_neighbors(parent, search_space):
    step = search_space.volume_step
    minimum = max(1, math.ceil(search_space.min_total_volume / step - 1e-9))
    maximum = max(minimum, math.floor(search_space.max_total_volume / step + 1e-9))
    current = max(minimum, min(maximum, round(sum(parent.volume_weights) / step)))
    totals = {
        minimum,
        maximum,
        current,
        *(
            max(minimum, min(maximum, round(current * multiplier)))
            for multiplier in (0.25, 0.5, 0.75, 1.25, 1.5, 2.0, 3.0)
        ),
        *(
            max(minimum, min(maximum, round(value / step)))
            for value in (
                0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08,
                0.09, 0.10, 0.12, 0.15, 0.20, 0.30, 0.40,
                0.60, 0.80, 1.0,
            )
        ),
    }
    plans = []
    leg_counts = {
        1,
        parent.leg_count,
        max(1, parent.leg_count - 1),
        min(search_space.max_legs, parent.leg_count + 1),
        min(search_space.max_legs, 4),
        min(search_space.max_legs, 8),
        search_space.max_legs,
    }
    for total in sorted(totals):
        for legs in sorted(leg_counts):
            if legs > total:
                continue
            equal = _allocate(total, legs)
            plans.append(tuple(round(value * step, 10) for value in equal))
            if legs > 1:
                front = [1] * legs
                front[0] += total - legs
                back = list(reversed(front))
                plans.append(tuple(round(value * step, 10) for value in front))
                plans.append(tuple(round(value * step, 10) for value in back))
    return tuple(dict.fromkeys(plans))


def _allocate(total, legs):
    base, remainder = divmod(total, legs)
    return [base + (1 if index < remainder else 0) for index in range(legs)]


def _bounded_exit(minutes, expiry, search_space):
    available = search_space.max_path_horizon_min - expiry
    return max(1, min(minutes, search_space.max_time_exit_min, available))


def _bounded_values(values, maximum):
    return tuple(sorted({maximum, *(value for value in values if value <= maximum)}))


def _compatible_lock(parent, target):
    if parent.profit_lock_arm is not None and parent.profit_lock_arm >= target:
        return {"profit_lock_arm": None, "profit_lock_giveback": None}
    return {}


def _half_close_executable(weights, step):
    tolerance = step * 1e-9
    return all(
        value / 2 >= step - tolerance
        and abs((value / 2) / step - round((value / 2) / step)) <= tolerance
        for value in weights
    )
