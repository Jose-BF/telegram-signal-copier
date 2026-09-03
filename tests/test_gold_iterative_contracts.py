from __future__ import annotations

from research.dubai_iterative.contracts import StrategyGenome
from research.gold_iterative.contracts import (
    GOLD_555_FLAT_CANCEL_FINGERPRINT,
    GOLD_555_UNTIL_EXPIRY_FINGERPRINT,
    gold_c490_genome,
    gold_555_genome,
    gold_555_flat_cancel_genome,
    gold_555_until_expiry_genome,
)
from strategy_runtime_contract import strategy_contract_by_id


SCHEMA_V2_FIELDS = {
    "entry_confirmation_value",
    "target_steps",
    "trailing_distance",
    "hard_stop_eur_per_leg",
    "time_exit_mode",
    "pending_entry_policy",
    "source_strategy_fingerprint",
}


def test_schema_one_payload_and_fingerprint_remain_byte_compatible():
    baseline = StrategyGenome.baseline()

    assert SCHEMA_V2_FIELDS.isdisjoint(baseline.to_dict())
    assert baseline.fingerprint == (
        "544b846be69936c177008f456d1118838427209a567be18b534bd18787669cbe"
    )


def test_gold_555_genome_encodes_the_complete_runtime_contract():
    genome = gold_555_genome()
    runtime = strategy_contract_by_id("gold_now_555_v1")

    assert genome.schema_version == 2
    assert genome.source_strategy_fingerprint == runtime.strategy_fingerprint
    assert genome.entry_mode == "adverse_reversal"
    assert genome.entry_value == runtime.entry.adverse == 1.0
    assert genome.entry_confirmation_value == runtime.entry.reversal == 1.5
    assert genome.entry_ladder_step == runtime.entry.ladder_step == 1.5
    assert genome.volume_weights == runtime.entry.volumes
    assert genome.target_steps == runtime.protection.target_steps
    assert genome.trailing_distance == runtime.protection.trailing_distance
    assert genome.time_exit_mode == runtime.protection.time_exit_mode
    assert genome.pending_entry_policy == runtime.terminal.pending_entry_policy
    assert genome.provider_management_mode == runtime.terminal.provider_management_mode
    assert genome.validation_errors() == ()


def test_declared_until_expiry_and_deterministic_flat_have_distinct_identities():
    declared = gold_555_genome()
    until_expiry = gold_555_until_expiry_genome()
    flat_cancel = gold_555_flat_cancel_genome()

    assert declared.pending_entry_policy == "until_expiry"
    assert until_expiry.pending_entry_policy == "until_expiry"
    assert flat_cancel.pending_entry_policy == "none"
    assert flat_cancel.fingerprint == GOLD_555_FLAT_CANCEL_FINGERPRINT
    assert until_expiry.fingerprint == GOLD_555_UNTIL_EXPIRY_FINGERPRINT
    assert until_expiry.fingerprint == declared.fingerprint
    assert flat_cancel.fingerprint != declared.fingerprint
    assert flat_cancel.source_strategy_fingerprint == declared.source_strategy_fingerprint
    assert flat_cancel.validation_errors() == ()
    assert until_expiry.validation_errors() == ()


def test_gold_c490_genome_encodes_every_live_protection_rule():
    genome = gold_c490_genome()
    runtime = strategy_contract_by_id("gold_now_c490_v1")

    assert genome.schema_version == 2
    assert genome.source_strategy_fingerprint == runtime.strategy_fingerprint
    assert genome.entry_mode == "signal_market"
    assert genome.volume_weights == runtime.entry.volumes
    assert genome.hard_stop_eur_per_leg == runtime.protection.hard_stop_eur_per_leg
    assert genome.be_trigger == runtime.protection.break_even_trigger_xau
    assert genome.stop_value == runtime.protection.basket_stop_eur
    assert genome.profit_lock_arm == runtime.protection.profit_arm_eur
    assert genome.profit_lock_giveback == runtime.protection.profit_giveback_eur
    assert genome.time_exit_mode == runtime.protection.time_exit_mode
    assert genome.pending_entry_policy == runtime.terminal.pending_entry_policy
    assert genome.provider_management_mode == runtime.terminal.provider_management_mode
    assert genome.validation_errors() == ()


def test_schema_two_round_trip_preserves_vectors_and_runtime_identity():
    expected = gold_555_genome()

    actual = StrategyGenome.from_dict(expected.to_dict())

    assert actual == expected
    assert actual.fingerprint == expected.fingerprint


def test_schema_two_rejects_target_vectors_that_do_not_match_legs():
    invalid = gold_555_genome().with_change(target_steps=(0.5, 1.0))

    assert "target_step_count_mismatch" in invalid.validation_errors()


def test_schema_two_rejects_unknown_time_and_pending_entry_modes():
    invalid = gold_555_genome().with_change(
        time_exit_mode="sometimes",
        pending_entry_policy="maybe",
    )

    assert "unsupported_time_exit_mode" in invalid.validation_errors()
    assert "unsupported_pending_entry_policy" in invalid.validation_errors()


def test_schema_one_cannot_silently_activate_schema_two_semantics():
    invalid = StrategyGenome.baseline().with_change(
        trailing_distance=30.0,
    )

    assert "schema_v2_fields_require_schema_v2" in invalid.validation_errors()
