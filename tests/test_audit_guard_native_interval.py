from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tools.audit_guard_native_interval import _assert_unchanged, compare_guard_intervals
from tools.audit_native_money_anchor import digest


BASE = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)


def sample(offset_ms, total, *, ticket=101):
    at = BASE + timedelta(milliseconds=offset_ms)
    return {"at": at, "ordinal": 0, "total": None if total is None else Decimal(total),
            "positions": {str(ticket): {"volume": Decimal("0.04")}},
            "blockers": [] if total is not None else ["stale_conversion_quote"]}


def guard(observed="11.00", *, tick_ms=0, at_ms=150):
    at = BASE + timedelta(milliseconds=at_ms)
    return {"decision_id": "d", "status": "observed", "observed_total_eur": observed,
            "source_tick_time_msc": int((BASE.timestamp() + 10_800) * 1000) + tick_ms,
            "guard_evaluated_at_utc": at.isoformat(), "open_tickets": [101]}


def test_guard_value_must_fit_exact_causal_tick_interval():
    model = [sample(0, "10.00"), sample(100, "12.00"), sample(200, "8.00")]
    result = compare_guard_intervals([guard()], model, [])
    assert result["statuses"] == {"within_modeled_interval": 1}
    assert result["rows"][0]["model_min_eur"] == "10.00"
    assert result["rows"][0]["model_max_eur"] == "12.00"
    outside = compare_guard_intervals([guard("13.00")], model, [])
    assert outside["statuses"] == {"outside_modeled_interval": 1}
    assert outside["rows"][0]["outside_gap_eur"] == "1.00"
    assert outside["rows"][0]["model_marks"] == [
        {"at": BASE.isoformat(), "total_eur": "10.00"},
        {"at": (BASE + timedelta(milliseconds=100)).isoformat(), "total_eur": "12.00"}]


def test_guard_interval_blocks_missing_tick_stale_fx_and_native_deal():
    model = [sample(0, "10.00"), sample(100, "12.00")]
    assert compare_guard_intervals([guard(tick_ms=50)], model, [])["statuses"] == {
        "blocked_source_tick_not_in_native_tape": 1}
    stale = [sample(0, "10.00"), sample(100, None)]
    assert compare_guard_intervals([guard()], stale, [])["statuses"] == {
        "blocked_model_mark_unknown": 1}
    assert compare_guard_intervals([guard()], model, [BASE + timedelta(milliseconds=75)])["statuses"] == {
        "blocked_native_deal_within_interval": 1}


def test_guard_interval_blocks_wrong_position_or_unanchored_clock():
    model = [sample(0, "10.00", ticket=999), sample(100, "12.00", ticket=999)]
    assert compare_guard_intervals([guard()], model, [])["statuses"] == {
        "blocked_native_position_set_mismatch": 1}
    assert compare_guard_intervals([guard(at_ms=6000)], model, [])["statuses"] == {
        "blocked_interval_over_market_age_budget": 1}


def test_outlier_retains_pre_source_tick_model_diagnostic():
    model = [sample(-100, "9.00"), sample(0, "10.00")]
    result = compare_guard_intervals([guard("9.00")], model, [])
    row = result["rows"][0]
    assert row["status"] == "outside_modeled_interval"
    assert row["prior_model_total_eur"] == "9.00"
    assert row["prior_model_age_ms"] == 100
    assert row["prior_model_marks"] == [
        {"at": (BASE - timedelta(milliseconds=100)).isoformat(), "total_eur": "9.00"}]


def test_audit_rejects_input_mutated_during_reconstruction(tmp_path):
    source = tmp_path / "capture.json"
    source.write_text('{"version": 1}', encoding="utf-8")
    initial = {source: digest(source)}
    _assert_unchanged(initial)
    source.write_text('{"version": 2}', encoding="utf-8")
    with pytest.raises(ValueError, match="changed during reconstruction"):
        _assert_unchanged(initial)
