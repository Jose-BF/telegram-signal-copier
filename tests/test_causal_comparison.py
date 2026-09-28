from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from research.causal_comparison import SequenceEvent, compare_sequences


BASE = datetime(2026, 9, 8, 10, tzinfo=timezone.utc)


def event(slot=1, kind="entry", seconds=0, **values):
    return SequenceEvent(slot, kind, BASE + timedelta(seconds=seconds),
                         values.get("direction", "BUY"), values.get("price", "100.2"),
                         values.get("volume", "0.04"), values.get("money", "0.00"),
                         values.get("mechanism", "market" if kind == "entry" else "tp"))


def test_exact_facts_do_not_claim_request_or_decision_fidelity():
    result = compare_sequences([event()], [event()])
    assert result["first_divergence"] is None
    assert result["full_live_parity_verified"] is False
    assert "modify_reject_confirm_sequence" in result["unverified"]


def test_first_divergence_is_entry_even_when_exit_is_closer_in_price():
    observed = [event(), event(kind="exit", seconds=2, price="100.7", money="2")]
    simulated = [replace(observed[0], at=BASE + timedelta(milliseconds=80)), observed[1]]
    result = compare_sequences(observed, simulated)
    assert result["first_divergence"]["kind"] == "entry"
    assert result["first_divergence"]["time_delta_ms"] == 80


def test_nearby_fill_cannot_be_reassigned_to_another_logical_leg():
    result = compare_sequences([event(slot=1)], [event(slot=2)])
    assert len(result["comparisons"]) == 2
    assert all(row["differences"] == ["missing_event"] for row in result["comparisons"])


def test_same_time_partials_preserve_multiplicity_without_inventing_order():
    partials = [event(kind="exit", seconds=2, volume="0.01", money="0.5"),
                event(kind="exit", seconds=2, volume="0.03", money="1.5")]
    assert compare_sequences(partials, reversed(partials))["first_divergence"] is None
    combined = [event(kind="exit", seconds=2, volume="0.04", money="2")]
    result = compare_sequences(partials, combined)
    assert result["observed_event_count"] == 2 and result["simulated_event_count"] == 1
    assert result["first_structural_divergence"] is not None


def test_compensating_money_errors_still_fail_each_position():
    actual = [event(slot=1, kind="exit", money="1"), event(slot=2, kind="exit", money="1")]
    simulated = [replace(actual[0], money=Decimal("0.99")), replace(actual[1], money=Decimal("1.01"))]
    result = compare_sequences(actual, simulated)
    assert all(row["differences"] == ["money"] for row in result["comparisons"])


def test_duplicate_entry_identity_is_not_silently_deduplicated():
    with pytest.raises(ValueError, match="multiple entries"):
        compare_sequences([event(), event()], [event()])


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, True])
def test_invalid_fill_price_cannot_be_compared_as_known(value):
    with pytest.raises(ValueError):
        event(price=value)
