"""Frozen Gold strategy genomes used as reproducible research seeds."""

from __future__ import annotations

from research.dubai_iterative.contracts import StrategyGenome


GOLD_555_STRATEGY_FINGERPRINT = (
    "555124a24b534aa2abda53ddaaa2ee35fd3afd07e61d05937eb14c80ad0676f0"
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
