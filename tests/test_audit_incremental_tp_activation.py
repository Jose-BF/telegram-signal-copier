import numpy as np
import pytest

from tools.audit_incremental_tp_activation import (
    first_model_modify_release, first_model_tp_install, pre_model_tp_touches,
)


@pytest.mark.parametrize("direction,quotes,target,expected", [
    ("SELL", [100.0, 99.4, 99.3], 99.5, [500]),
    ("BUY", [100.0, 100.6, 100.7], 100.5, [500]),
])
def test_tp_touches_use_exit_side_and_exclude_install_tick(
    direction, quotes, target, expected,
):
    times = np.array([10_800_000, 10_800_500, 10_801_000], dtype=np.int64)
    bids = np.array(quotes if direction == "BUY" else [x - 0.2 for x in quotes])
    asks = np.array(quotes if direction == "SELL" else [x + 0.2 for x in quotes])
    result = pre_model_tp_touches(
        times, bids, asks, direction=direction, target=target,
        entry_utc_msc=0, install_utc_msc=1000, offset_seconds=10_800)
    assert result["touch_count"] == 1
    assert result["first_touch_utc_msc"] in expected
    assert result["first_touch_quote"] == ("99.4" if direction == "SELL" else "100.6")


def test_modify_release_requires_started_call_and_keeps_missing_unknown():
    events = [
        {"operation": "modify", "ticket": "a", "kind": "queued", "time_ns": 100},
        {"operation": "modify", "ticket": "a", "kind": "started", "time_ns": 120},
        {"operation": "modify", "ticket": "a", "kind": "released", "time_ns": 200},
        {"operation": "modify", "ticket": "a", "kind": "started", "time_ns": 300},
        {"operation": "modify", "ticket": "a", "kind": "released", "time_ns": 400},
    ]
    assert first_model_modify_release(events, "a", entry_ns=50) == 200
    assert first_model_modify_release(events, "b", entry_ns=50) is None
    with pytest.raises(ValueError, match="without start"):
        first_model_modify_release(
            [{"operation": "modify", "ticket": "a", "kind": "released",
              "time_ns": 200}], "a", entry_ns=50)


def test_tp_install_uses_installed_event_not_later_acknowledgement():
    events = [
        {"ticket": "a", "kind": "open", "request_id": 0,
         "timestamp_ns": 50, "tp": None},
        {"ticket": "a", "kind": "requested", "request_id": 1,
         "timestamp_ns": 100, "tp": 99.5},
        {"ticket": "a", "kind": "installed", "request_id": 1,
         "timestamp_ns": 200, "tp": 99.5},
        {"ticket": "a", "kind": "acknowledged", "request_id": 1,
         "timestamp_ns": 300, "tp": 99.5},
    ]
    assert first_model_tp_install(events, "a", entry_ns=50) == (200, 99.5)
    assert first_model_tp_install(events, "b", entry_ns=50) is None
    with pytest.raises(ValueError, match="without request"):
        first_model_tp_install(
            [events[0], events[2]], "a", entry_ns=50)


def test_tp_install_skips_rejected_attempt_and_early_acknowledgement():
    events = [
        {"ticket": "a", "kind": "open", "request_id": 0,
         "timestamp_ns": 50, "tp": None},
        {"ticket": "a", "kind": "requested", "request_id": 1,
         "timestamp_ns": 100, "tp": 99.5},
        {"ticket": "a", "kind": "rejected", "request_id": 1,
         "timestamp_ns": 200, "tp": 99.5},
        {"ticket": "a", "kind": "acknowledged", "request_id": 1,
         "timestamp_ns": 300, "tp": None},
        {"ticket": "a", "kind": "requested", "request_id": 2,
         "timestamp_ns": 400, "tp": 99.5},
        {"ticket": "a", "kind": "installed", "request_id": 2,
         "timestamp_ns": 500, "tp": 99.5},
    ]
    assert first_model_tp_install(events, "a", entry_ns=50) == (500, 99.5)
