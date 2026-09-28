from copy import deepcopy
import pytest

from research.dubai_range_entry_quality import decision, overlay_row


def base():
    return {"status": "simulated", "direction": "BUY", "nominal_risk_usd": 100,
        "net_after_cost_eur": "35", "genome": {"entry_mode": "signal_market", "leg_count": 1,
            "target_mode": "per_leg_levels", "volume_weights": [.1], "stop_value": 90, "target_steps": [105]},
        "result": {"market_events": [{"kind": "entry_requested", "price": 100, "tick_index": 0, "request_id": 1}],
            "entries": [{"entry_price": 103}]}}


@pytest.mark.parametrize("threshold,expected", [(".25", "retain"), (".5", "retain"), (".75", "abstain"), ("1", "abstain")])
def test_uses_observed_request_quote_not_later_fill(threshold, expected):
    assert decision(base(), threshold)["decision"] == expected


def test_sell_geometry_and_exact_boundary():
    row = base()
    row["direction"] = "SELL"
    row["genome"].update(stop_value=110, target_steps=[95])
    assert decision(row, ".5")["decision"] == "retain"


def test_blocked_is_not_a_filter_rejection_or_zero_profit():
    row = {"status": "data_blocked", "net_after_cost_eur": None}
    result = decision(row, ".5")
    assert result["decision"] == "base_blocked_or_not_applicable"
    assert overlay_row(row, result) == row


def test_abstention_preserves_original_archive_row():
    row = base()
    before = deepcopy(row)
    adapted = overlay_row(row, decision(row, "1"))
    assert adapted["net_after_cost_eur"] == "0" and adapted["status"] == "unfilled"
    assert row == before


@pytest.mark.parametrize("field,value", [("tick_index", 1), ("price", 101), ("request_id", 2)])
def test_changed_causal_quote_or_risk_contract_rejected(field, value):
    row = base()
    row["result"]["market_events"][0][field] = value
    with pytest.raises(ValueError):
        decision(row, ".5")
