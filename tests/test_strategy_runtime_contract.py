from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

import dubai_live_candidate
import gold_555_live_candidate
import gold_live_candidate
from strategy_runtime_contract import (
    all_strategy_contracts,
    strategy_contract_by_id,
)


EXPECTED_IDS = (
    "dubai_balanced_v1",
    "dubai_frontloaded_30m_v1",
    "dubai_frontloaded_40m_v1",
    "gold_now_555_v1",
    "gold_now_b210_v1",
    "gold_now_c490_v1",
)


def test_registry_contains_every_current_live_and_shadow_strategy_once():
    contracts = all_strategy_contracts()

    assert tuple(contract.strategy_id for contract in contracts) == EXPECTED_IDS
    assert len({contract.strategy_id for contract in contracts}) == len(contracts)


@pytest.mark.parametrize("strategy_id", EXPECTED_IDS)
def test_live_and_shadow_compile_from_the_same_execution_contract(strategy_id):
    contract = strategy_contract_by_id(strategy_id)
    live = contract.to_live_plan()
    shadow = contract.to_shadow_policy(role="candidate")

    assert live.strategy_id == shadow.candidate_id == strategy_id
    assert live.strategy_fingerprint == shadow.strategy_fingerprint
    assert live.execution_fingerprint == shadow.execution_fingerprint
    assert live.execution_payload == shadow.execution_payload()


def test_gold_555_contract_preserves_declared_pending_entry_window():
    contract = strategy_contract_by_id("gold_now_555_v1")
    deployed = gold_555_live_candidate.Gold555Policy()

    assert contract.strategy_fingerprint == (
        gold_555_live_candidate.CANDIDATE_FINGERPRINT
    )
    assert contract.terminal.pending_entry_policy == "until_expiry"
    assert contract.terminal.automatic_flat_policy == "keep_if_eligible"
    assert contract.entry.volumes == deployed.entry_volumes
    assert contract.entry.ladder_step == deployed.ladder_step
    assert contract.entry.expiry_minutes == deployed.entry_expiry_minutes
    assert contract.entry.adverse == deployed.entry_adverse
    assert contract.entry.reversal == deployed.entry_reversal
    assert contract.protection.target_steps == deployed.target_steps
    assert contract.protection.trailing_distance == deployed.trailing_distance
    assert contract.protection.profit_arm_eur == deployed.profit_arm_eur
    assert contract.protection.profit_giveback_eur == deployed.profit_giveback_eur
    assert (
        contract.protection.time_exit_minutes
        == deployed.non_negative_exit_minutes
    )
    assert contract.protection.time_exit_mode == "non_negative"
    assert (
        contract.terminal.provider_management_mode
        == deployed.provider_management_mode
    )


def test_current_live_candidate_fingerprints_are_frozen_in_registry():
    assert strategy_contract_by_id("dubai_balanced_v1").strategy_fingerprint == (
        dubai_live_candidate.CANDIDATE_FINGERPRINT
    )
    assert strategy_contract_by_id("gold_now_555_v1").strategy_fingerprint == (
        gold_555_live_candidate.CANDIDATE_FINGERPRINT
    )
    assert strategy_contract_by_id("gold_now_c490_v1").strategy_fingerprint == (
        gold_live_candidate.CANDIDATE_FINGERPRINT
    )


def test_display_role_cannot_change_strategy_execution_identity():
    contract = strategy_contract_by_id("gold_now_555_v1")
    control = contract.to_shadow_policy(role="live_control")
    candidate = contract.to_shadow_policy(role="candidate")

    assert control.execution_fingerprint == candidate.execution_fingerprint
    assert control.strategy_fingerprint == candidate.strategy_fingerprint


def test_strategy_contracts_are_immutable_and_unknown_ids_fail_closed():
    contract = strategy_contract_by_id("gold_now_555_v1")

    with pytest.raises(FrozenInstanceError):
        contract.strategy_id = "changed"
    with pytest.raises(KeyError, match="unknown strategy contract"):
        strategy_contract_by_id("missing")
