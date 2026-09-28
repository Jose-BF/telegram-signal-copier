import pytest

from tools.audit_shadow_fill_availability import compare_first_fill
from tools.audit_week_shadow_control_path import utc_ms


TICK = utc_ms("2026-09-18T11:00:00+00:00")


def inputs(*, record_ms=3000, fill_ms=1400):
    shadow = {"signal_id": "canal2_1", "channel": "canal2",
              "clock_direct_for_all_native_event_days": True,
              "first_native_entry_source_msc": TICK + fill_ms + 10_800_000,
              "first_virtual_fill_utc_msc": TICK,
              "first_virtual_fill_journal_utc": (
                  "2026-09-18T11:00:03+00:00" if record_ms == 3000 else
                  "2026-09-18T11:00:01+00:00"),
              "first_native_entry_price": 4381.79,
              "first_native_minus_virtual_ms": fill_ms,
              "first_virtual_fill_emit_minus_tick_ms": record_ms,
              "first_native_minus_shadow_emit_ms": fill_ms - record_ms,
              "entry_pairing": {"status": "verified_journal_entry_binding"}}
    native = {"signal_id": "canal2_1", "channel": "canal2",
              "status": "exact_opening_deal_and_call_bound",
              "native_entry_source_msc": TICK + fill_ms + 10_800_000,
              "virtual_first_tick_utc_msc": TICK,
              "native_entry_price": 4381.79,
              "native_entry_utc_msc": TICK + fill_ms,
              "request_utc": "2026-09-18T11:00:01+00:00",
              "request_minus_virtual_tick_ms": 1000,
              "native_entry_minus_virtual_tick_ms": fill_ms}
    return shadow, native


def test_retrospective_virtual_fill_is_separate_from_market_tick():
    shadow, native = inputs()
    row = compare_first_fill(shadow, native, offset_seconds=10_800)
    assert row["availability"] == "recorded_after_native_fill"
    assert row["shadow_record_after_native_fill_ms"] == 1600
    assert row["native_request_after_virtual_tick_ms"] == 1000


def test_earlier_record_is_not_mislabeled_retrospective():
    shadow, native = inputs(record_ms=1000)
    row = compare_first_fill(shadow, native, offset_seconds=10_800)
    assert row["availability"] == "recorded_before_native_fill"
    assert row["shadow_record_after_native_fill_ms"] == -400


def test_identity_and_clock_arithmetic_are_fail_closed():
    shadow, native = inputs()
    with pytest.raises(ValueError, match="identity"):
        compare_first_fill(shadow, {**native, "signal_id": "canal2_2"},
                           offset_seconds=10_800)
    with pytest.raises(ValueError, match="arithmetic"):
        compare_first_fill(shadow, {**native, "native_entry_utc_msc": TICK + 1401},
                           offset_seconds=10_800)
    with pytest.raises(ValueError, match="direct clock"):
        compare_first_fill({**shadow, "clock_direct_for_all_native_event_days": False},
                           native, offset_seconds=10_800)
