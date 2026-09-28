import numpy as np
import pytest

from tools.audit_tp_response_exit_path import post_response_touch


POSITION = {"position_id": 101, "signal_id": "canal2_1", "direction": "BUY",
            "entry_msc": 11_000, "exit_msc": 18_000, "exit_price": "101.0"}
LEG = {"native_position_id": 101, "signal_id": "canal2_1", "leg_index": 0,
       "direction": "BUY", "native_entry_source_msc": 11_000,
       "broker_clock_offset_seconds": 11, "target_level": "101.0",
       "first_accepted_response_utc_msc": 4_000}
TIMES = np.array([14_999, 15_000, 16_000, 17_999, 18_000, 18_001])
BIDS = np.array([101.5, 100.8, 101.0, 100.9, 101.1, 102.0])
ASKS = BIDS + 0.1


def test_post_response_touch_uses_bid_and_excludes_equal_time_boundaries():
    row = post_response_touch(POSITION, LEG, TIMES, BIDS, ASKS, offset_seconds=11)
    assert row["retained_tick_count_response_to_exit"] == 2
    assert row["native_target_touch_count_response_to_exit"] == 1
    assert row["first_target_touch_utc_msc"] == 5_000
    assert row["first_touch_to_native_exit_ms"] == 2_000
    assert row["quote_min_first_touch_to_exit"] == "100.9"
    assert row["quote_max_first_touch_to_exit"] == "101.0"
    assert row["unfavorable_to_target_tick_count_after_first_touch"] == 1
    assert row["status"] == "retained_touch_precedes_native_tp_exit"


def test_post_response_touch_sell_uses_ask_and_preserves_missing_touch():
    position = {**POSITION, "direction": "SELL", "exit_price": "100.8"}
    leg = {**LEG, "direction": "SELL", "target_level": "100.8"}
    row = post_response_touch(position, leg, TIMES, BIDS, ASKS, offset_seconds=11)
    assert row["native_target_touch_count_response_to_exit"] == 0
    assert row["first_touch_to_native_exit_ms"] is None
    assert row["status"] == "no_retained_touch_before_exit"


def test_post_response_touch_rejects_unbound_and_impossible_timeline():
    with pytest.raises(ValueError, match="identity"):
        post_response_touch(POSITION, {**LEG, "native_entry_source_msc": 12_000},
                            TIMES, BIDS, ASKS, offset_seconds=11)
    with pytest.raises(ValueError, match="chronology"):
        post_response_touch(POSITION, {**LEG, "first_accepted_response_utc_msc": 8_000},
                            TIMES, BIDS, ASKS, offset_seconds=11)
    with pytest.raises(ValueError, match="exit price"):
        post_response_touch(POSITION, {**LEG, "target_level": "100.9"},
                            TIMES, BIDS, ASKS, offset_seconds=11)
