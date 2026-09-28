from dataclasses import replace

import pytest

from research.dubai_break_even_study import first_direct_be, managed_genome, summary
from tests.test_absolute_level_execution import policy


def message(at, modality="direct", line=1):
    return {"signal_id": "s", "kind": "management", "received_utc": at,
        "source_line_1based": line, "parsed": {"action": "MOVE_SL_TO_BE", "modality": modality}}


def test_first_direct_be_is_received_after_levels_and_not_backdated_from_optional():
    row = {"signal_id": "s", "first_priced": {"received_utc": "2026-06-05T12:00:00Z"}}
    old = message("2026-06-05T11:59:59Z")
    conditional = message("2026-06-05T12:00:01Z", "conditional")
    actual = message("2026-06-05T12:00:03Z")
    assert first_direct_be(row, [message("2026-06-05T12:00:04Z"), old, conditional, actual]) == actual


@pytest.mark.parametrize("direction,quote", [("BUY", 101), ("SELL", 99)])
def test_management_changes_only_be_fields_not_levels_or_size(direction, quote):
    base = policy(direction)
    price = managed_genome(base, "half_tp1_be", quote, direction)
    assert price.be_mode == "price" and price.be_trigger == 2
    assert replace(price, be_mode=base.be_mode, be_trigger=base.be_trigger) == base
    received = managed_genome(base, "provider_direct_be", quote, direction)
    assert received.be_mode == "provider" and received.be_trigger is None
    assert managed_genome(base, "no_be", quote, direction) == base


def test_already_crossed_target_keeps_positive_trigger_while_entry_gate_cancels():
    assert managed_genome(policy(), "half_tp1_be", 106, "BUY").be_trigger == .01


def test_half_price_between_ticks_rounds_up_without_arming_early():
    assert managed_genome(policy(), "half_tp1_be", 101.33, "BUY").be_trigger == 1.84


def test_unknown_management_is_not_silently_mapped():
    with pytest.raises(ValueError):
        managed_genome(policy(), "unknown", 100, "BUY")


def test_filtered_population_cannot_turn_new_engine_block_into_zero():
    row = {"management": "provider_direct_be", "profile": "reference", "status": "engine_blocked",
        "received_utc": "2026-06-05T12:00:00Z", "result": None, "net_after_cost_eur": None,
        "r05_decision": {"decision": "abstain"}}
    result = summary([row])
    for population in ("broad", "r05"):
        assert result["populations"][population]["provider_direct_be|reference"]["net_after_cost_eur"] is None
