import numpy as np
import pytest
from datetime import datetime, timezone

from tools.audit_tp_preack_touches import direct_same_day_offset, preack_touch


POSITION = {"position_id": 101, "signal_id": "canal2_1", "direction": "BUY",
            "entry_msc": 11_000, "exit_msc": 18_000, "exit_price": "101.0"}
LEG = {"native_position_id": 101, "signal_id": "canal2_1", "leg_index": 0,
       "target_level": "101.0", "first_target_accepted_response_utc_msc": 5_000,
       "virtual_close_tick_utc_msc": 2_000,
       "attempts": [{"attempt_id": "rejected", "started_utc_msc": 1_900,
                     "responded_utc_msc": 3_000, "retcode": 10016}]}
TIMES = np.array([10_999, 11_000, 12_000, 13_000, 15_999, 16_000])
BIDS = np.array([102.0, 100.8, 100.9, 101.0, 101.1, 102.0])
ASKS = BIDS + 0.1


def test_preack_touch_uses_native_bid_and_excludes_response_boundary():
    row = preack_touch(POSITION, LEG, TIMES, BIDS, ASKS, offset_seconds=11)
    assert row["retained_tick_count_before_response"] == 4
    assert row["native_target_touch_count_before_response"] == 2
    assert row["first_target_touch_utc_msc"] == 2_000
    assert row["first_target_touch_minus_virtual_close_tick_ms"] == 0
    assert row["inflight_attempt_ids_at_first_touch"] == ["rejected"]
    assert row["inflight_later_rejected_at_first_touch_count"] == 1


def test_preack_touch_sell_uses_ask_and_preserves_no_touch():
    position = {**POSITION, "direction": "SELL", "exit_price": "100.8"}
    leg = {**LEG, "target_level": "100.8"}
    row = preack_touch(position, leg, TIMES, BIDS, ASKS, offset_seconds=11)
    assert row["native_target_touch_count_before_response"] == 0
    assert row["first_target_touch_utc_msc"] is None


def test_preack_touch_rejects_unbound_or_impossible_timeline():
    with pytest.raises(ValueError, match="identity"):
        preack_touch(POSITION, {**LEG, "native_position_id": 102}, TIMES, BIDS, ASKS,
                     offset_seconds=11)
    with pytest.raises(ValueError, match="chronology"):
        preack_touch(POSITION, {**LEG, "first_target_accepted_response_utc_msc": 7_000},
                     TIMES, BIDS, ASKS, offset_seconds=11)
    with pytest.raises(ValueError, match="exit price"):
        preack_touch(POSITION, {**LEG, "target_level": "100.0"}, TIMES, BIDS, ASKS,
                     offset_seconds=11)


def test_direct_clock_requires_single_anchored_day():
    day = "2027-01-15"
    day_ms = int(datetime(2027, 1, 15, 12, tzinfo=timezone.utc).timestamp() * 1000)
    position = {**POSITION, "entry_msc": day_ms + 10_800_000,
                "exit_msc": day_ms + 10_820_000}
    leg = {**LEG, "first_target_accepted_response_utc_msc": day_ms + 15_000}
    anchor = {"independent_clock_evidence": {"days": {
        day: {"status": "direct_anchor_available", "offset_seconds": 10_800}}}}
    assert direct_same_day_offset(position, leg, anchor) == 10_800
    with pytest.raises(ValueError, match="crosses"):
        direct_same_day_offset(position, {**leg, "first_target_accepted_response_utc_msc":
                                           day_ms + 50_000_000}, anchor)
    anchor["independent_clock_evidence"]["days"][day]["status"] = "unanchored"
    with pytest.raises(ValueError, match="directly anchored"):
        direct_same_day_offset(position, leg, anchor)
