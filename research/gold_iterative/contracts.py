"""Frozen Gold strategy genomes used as reproducible research seeds."""

from __future__ import annotations

from research.dubai_iterative.contracts import StrategyGenome


GOLD_555_STRATEGY_FINGERPRINT = (
    "555124a24b534aa2abda53ddaaa2ee35fd3afd07e61d05937eb14c80ad0676f0"
)
GOLD_555_FLAT_CANCEL_FINGERPRINT = (
    "0d740b1afaa2b6d1d578590db896351e4eced43828a49fd553aa5946665dc3c6"
)
GOLD_555_UNTIL_EXPIRY_FINGERPRINT = (
    "9b8a08950d6bd319b06f495a9844628c6e9d65bf49770b733f9c123f397ccc91"
)
GOLD_C490_STRATEGY_FINGERPRINT = (
    "c4900550abae98de1500bf5b849072956175fdecda102fad69be9f7975cbf8d6"
)


def gold_555_genome() -> StrategyGenome:
    return StrategyGenome(
        schema_version=2,
        entry_mode="adverse_reversal",
        entry_value=1.0,
        entry_confirmation_value=1.5,
        entry_expiry_min=30,
        entry_ladder_mode="adverse",
        entry_ladder_step=1.5,
        leg_count=5,
        volume_weights=(0.04, 0.03, 0.03, 0.03, 0.03),
        target_mode="per_leg_steps",
        target_steps=(0.5, 1.0, 1.5, 2.0, 2.5),
        be_mode="none",
        stop_mode="none",
        trailing_distance=30.0,
        profit_lock_arm=30.0,
        profit_lock_giveback=1.0,
        time_exit_min=180,
        time_exit_mode="non_negative",
        provider_management_mode="explicit_close_only",
        pending_entry_policy="until_expiry",
        source_strategy_fingerprint=GOLD_555_STRATEGY_FINGERPRINT,
    )


def gold_555_flat_cancel_genome() -> StrategyGenome:
    """Deterministic candidate that cancels pending legs when first flat."""

    genome = gold_555_genome().with_change(
        pending_entry_policy="none",
        mutation_reason="cancel_remaining_entries_when_first_flat",
    )
    if genome.fingerprint != GOLD_555_FLAT_CANCEL_FINGERPRINT:
        raise RuntimeError("flat-cancel Gold 555 research fingerprint changed")
    return genome


def gold_555_until_expiry_genome() -> StrategyGenome:
    """Alternative 555 that keeps later legs alive until the 30m expiry."""

    genome = gold_555_genome()
    if genome.fingerprint != GOLD_555_UNTIL_EXPIRY_FINGERPRINT:
        raise RuntimeError("until-expiry Gold 555 research fingerprint changed")
    return genome


def gold_555_observed_flat_cancel_genome() -> StrategyGenome:
    """Compatibility alias; this is not the scheduler-dependent live trace."""

    return gold_555_flat_cancel_genome()


def gold_c490_genome() -> StrategyGenome:
    return StrategyGenome(
        schema_version=2,
        entry_mode="signal_market",
        entry_expiry_min=15,
        entry_ladder_mode="simultaneous",
        leg_count=5,
        volume_weights=(0.01, 0.01, 0.01, 0.01, 0.01),
        target_mode="none",
        be_mode="price",
        be_trigger=12.0,
        stop_mode="basket_money",
        stop_value=100.0,
        hard_stop_eur_per_leg=20.0,
        profit_lock_arm=10.0,
        profit_lock_giveback=8.0,
        time_exit_min=40,
        time_exit_mode="loss_only",
        provider_management_mode="ignore",
        pending_entry_policy="none",
        source_strategy_fingerprint=GOLD_C490_STRATEGY_FINGERPRINT,
    )
