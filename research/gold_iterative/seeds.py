"""Deterministic, structurally diverse generation zero for Gold NOW."""

from __future__ import annotations

import random

from research.dubai_iterative.contracts import SearchSpace, StrategyGenome
from research.dubai_iterative.evolution import deduplicate
from research.dubai_iterative.refinement import parameter_neighborhood

from .contracts import gold_555_genome, gold_c490_genome


def gold_parameter_neighborhood(
    parent: StrategyGenome,
    search_space: SearchSpace,
) -> tuple[StrategyGenome, ...]:
    """Return only entry rules supported by provider-first Gold evidence."""

    return tuple(
        candidate
        for candidate in parameter_neighborhood(parent, search_space)
        if candidate.entry_mode != "actual_mt5"
    )


def gold_seed_population(
    search_space: SearchSpace,
    *,
    seed: int,
) -> tuple[StrategyGenome, ...]:
    population: list[StrategyGenome] = []

    def add(family: str, genome: StrategyGenome) -> None:
        labelled = genome.with_lineage(
            parent_fingerprints=(),
            mutation_reason=f"seed:{family}",
            lineage_depth=0,
        )
        if labelled.validation_errors():
            return
        if search_space.validation_errors(labelled):
            return
        population.append(labelled)

    base = _base()
    add(
        "no_entry_control",
        base.with_change(
            entry_mode="no_entry",
            entry_expiry_min=1,
            leg_count=1,
            volume_weights=(0.01,),
            time_exit_min=1,
        ),
    )
    add(
        "provider_baseline",
        base.with_change(
            target_mode="provider_per_leg",
            be_mode="provider",
            stop_mode="provider",
            provider_management_mode="exact",
        ),
    )

    for steps in (
        (0.5, 1.0, 1.5, 2.0, 2.5),
        (1.0, 2.0, 3.0, 4.0, 5.0),
        (2.0, 3.0, 4.0, 5.0, 6.0),
    ):
        add(
            "immediate_scale_out",
            base.with_change(
                target_mode="per_leg_steps",
                target_steps=steps,
                provider_management_mode="explicit_close_only",
            ),
        )

    for step in (0.5, 1.0, 1.5, 2.0, 3.0):
        add(
            "adverse_ladder",
            base.with_change(
                entry_ladder_mode="adverse",
                entry_ladder_step=step,
                target_mode="fixed_basket",
                target_value=10.0,
                stop_mode="basket_money",
                stop_value=60.0,
                pending_entry_policy="until_expiry",
            ),
        )

    for adverse, reversal in (
        (0.5, 0.5),
        (0.5, 1.0),
        (1.0, 1.0),
        (1.0, 1.5),
        (1.5, 1.0),
        (2.0, 1.5),
    ):
        add(
            "adverse_reversal",
            base.with_change(
                entry_mode="adverse_reversal",
                entry_value=adverse,
                entry_confirmation_value=reversal,
                target_mode="fixed_basket",
                target_value=10.0,
                stop_mode="basket_money",
                stop_value=60.0,
            ),
        )

    for first, runner in ((3.0, 10.0), (5.0, 15.0), (10.0, 30.0)):
        add(
            "partial_runner",
            base.with_change(
                leg_count=4,
                volume_weights=(0.02, 0.02, 0.02, 0.02),
                target_mode="partial_runner",
                target_value=first,
                partial_fraction=0.5,
                runner_target=runner,
                stop_mode="basket_money",
                stop_value=60.0,
            ),
        )

    for target, stop in ((5.0, 30.0), (10.0, 50.0), (20.0, 80.0)):
        add(
            "basket_capture",
            base.with_change(
                target_mode="fixed_basket",
                target_value=target,
                stop_mode="basket_money",
                stop_value=stop,
            ),
        )

    for be_trigger, arm, giveback in (
        (1.0, 5.0, 1.0),
        (3.0, 10.0, 3.0),
        (6.0, 20.0, 5.0),
        (12.0, 30.0, 10.0),
    ):
        add(
            "staged_protection",
            base.with_change(
                target_mode="none",
                be_mode="price",
                be_trigger=be_trigger,
                stop_mode="basket_money",
                stop_value=80.0,
                profit_lock_arm=arm,
                profit_lock_giveback=giveback,
            ),
        )

    for minutes in (5, 10, 15, 20, 30):
        add(
            "short_hold",
            base.with_change(
                target_mode="none",
                stop_mode="basket_money",
                stop_value=60.0,
                time_exit_min=minutes,
                time_exit_mode="loss_only",
            ),
        )
    for minutes in (90, 120, 180, 210):
        add(
            "long_hold",
            base.with_change(
                target_mode="none",
                stop_mode="basket_money",
                stop_value=100.0,
                time_exit_min=minutes,
                time_exit_mode="non_negative",
            ),
        )

    add("gold_555", gold_555_genome())
    add("gold_c490", gold_c490_genome())

    unique = list(deduplicate(population))
    random.Random(seed).shuffle(unique)
    return tuple(unique)


def sample_gold_population(
    search_space: SearchSpace,
    *,
    seed: int,
    count: int,
) -> tuple[StrategyGenome, ...]:
    """Draw deterministic Gold scouts from seeds and their valid neighbours."""

    if count < 0:
        raise ValueError("count must be non-negative")
    if count == 0:
        return ()
    candidates = list(gold_seed_population(search_space, seed=seed))
    unique = {item.fingerprint: item for item in candidates}
    cursor = 0
    while len(unique) < count and cursor < len(candidates):
        parent = candidates[cursor]
        cursor += 1
        for child in gold_parameter_neighborhood(parent, search_space):
            if child.schema_version != 2:
                continue
            if child.validation_errors() or search_space.validation_errors(child):
                continue
            if child.fingerprint not in unique:
                unique[child.fingerprint] = child
                candidates.append(child)
            if len(unique) >= count:
                break
    population = list(unique.values())
    random.Random(seed).shuffle(population)
    return tuple(population[:count])


def _base() -> StrategyGenome:
    return StrategyGenome(
        schema_version=2,
        entry_mode="signal_market",
        entry_expiry_min=30,
        entry_ladder_mode="simultaneous",
        leg_count=5,
        volume_weights=(0.01, 0.01, 0.01, 0.01, 0.01),
        target_mode="none",
        be_mode="none",
        stop_mode="none",
        time_exit_min=180,
        time_exit_mode="none",
        provider_management_mode="ignore",
        pending_entry_policy="none",
    )
