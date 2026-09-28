from copy import deepcopy

import pytest

from research.dubai_current_comparison import current_genome, dates, inventory
from research.dubai_iterative.market import market_blockers
from research.dubai_iterative.protection import profile_blockers
from tests.test_absolute_be_execution import assumptions


def test_current_contract_keeps_pending_entries_and_native_risk():
    genome, contract = current_genome()
    assert genome.pending_entry_policy == "until_expiry"
    assert contract.terminal.automatic_flat_policy == "keep_if_eligible"
    assert genome.volume_weights == (.01, .04, .04)
    assert genome.stop_mode == "basket_money" and genome.stop_value == 25
    assert genome.time_exit_mode == "loss_only" and genome.time_exit_min == 40
    assert genome.provider_management_mode == "exact"
    assert not genome.validation_errors()


def test_existing_absolute_profile_does_not_admit_current_basket_policy():
    genome, _ = current_genome()
    execution = assumptions()
    errors = profile_blockers(None, genome, execution.protection) + market_blockers(None, genome, execution)
    assert "absolute_level_policy_contract" in errors
    assert "protection_extension_requires_own_rule_market" in errors


def test_dates_compare_instants_not_offset_strings():
    result = dates(["2026-06-05T14:00:00+02:00", "2026-06-05T11:59:00Z", None])
    assert result == {"first_utc": "2026-06-05T11:59:00+00:00", "last_utc": "2026-06-05T12:00:00+00:00"}
    assert dates([]) == {"first_utc": None, "last_utc": None}


def fixture_rows():
    signals = [{"signal_id": "a", "received_utc": "2026-06-05T12:00:00Z"},
               {"signal_id": "b", "received_utc": "2026-09-07T12:00:00Z"}]
    rows = [{**s, "management": "no_be", "profile": "adverse_execution",
             "priced_received_utc": s["received_utc"], "status": "simulated", "reasons": [],
             "r05_decision": {"decision": "retain"}} for s in signals]
    rows[1].update(status="data_blocked", reasons=["coverage_missing"])
    return signals, rows


def test_missing_signals_remain_unknown_not_zero_or_selected():
    signals, rows = fixture_rows()
    result = inventory(signals, rows)
    assert result["source_signal_count"] == 2
    assert result["covered_range_count"] == result["r05_filled_count"] == 1
    assert result["r05_ids"] == ["a"]
    assert all(r["dubai_net_eur"] is None for r in result["cohort"])
    assert result["cohort"][1]["base_reasons"] == ["coverage_missing"]


@pytest.mark.parametrize("failure", ["missing", "duplicate", "mixed"])
def test_incomplete_or_mixed_matrix_fails(failure):
    signals, rows = fixture_rows()
    if failure == "missing":
        rows.pop()
    elif failure == "duplicate":
        rows.append(deepcopy(rows[0]))
    else:
        rows[1]["signal_id"] = "different"
    with pytest.raises(ValueError, match="comparison cohort"):
        inventory(signals, rows)
