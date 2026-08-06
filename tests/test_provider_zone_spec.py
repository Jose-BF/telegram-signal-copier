from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from provider_zone_spec import build_zone_trade_spec


def utc(clock: str) -> datetime:
    return datetime.fromisoformat(f"2026-08-04T{clock}+00:00")


def event(clock: str, **values) -> dict:
    return {"observed_ts_utc": utc(clock).isoformat(), **values}


def zone_record(*, ranges, levels, direction="BUY", management=()) -> dict:
    return {
        "provider_signal_id": "canal2_9000",
        "channel": "canal2",
        "record_type": "zone_plan",
        "zone_plan_timeline": [
            event("10:00:00", direction=direction, message_id=9000)
        ],
        "entry_zone_timeline": list(ranges),
        "level_timeline": list(levels),
        "runtime_level_timeline": [],
        "management_events": list(management),
        "execution_batches": [],
    }


def test_zone_becomes_ready_only_when_sl_is_observed():
    record = zone_record(
        ranges=[event("10:00:00", range=[100, 105])],
        levels=[
            event("10:00:01", tps=[110], sl=None),
            event("10:00:02", tps=[110], sl=95),
        ],
    )

    spec = build_zone_trade_spec(record)

    assert spec.ready_at_utc == utc("10:00:02")
    assert len(spec.ready_states) == 1
    assert spec.ready_states[0].zone == (100.0, 105.0)
    assert spec.ready_states[0].tps == (110.0,)
    assert spec.ready_states[0].sl == 95.0
    assert spec.blockers == ()


def test_later_range_revision_is_ordered_not_backfilled():
    record = zone_record(
        ranges=[
            event("10:00:00", range=[100, 105]),
            event("10:05:00", range=[98, 103]),
        ],
        levels=[event("10:00:00", tps=[110], sl=95)],
    )

    spec = build_zone_trade_spec(record)

    assert [state.observed_utc for state in spec.ready_states] == [
        utc("10:00:00"),
        utc("10:05:00"),
    ]
    assert [state.zone for state in spec.ready_states] == [
        (100.0, 105.0),
        (98.0, 103.0),
    ]


def test_invalid_buy_geometry_is_named_and_not_dropped():
    record = zone_record(
        ranges=[event("10:00:00", range=[100, 105])],
        levels=[event("10:00:00", tps=[110], sl=102)],
    )

    spec = build_zone_trade_spec(record)

    assert spec.ready_at_utc is None
    assert spec.ready_states == ()
    assert spec.blockers == ("invalid_buy_zone_geometry",)


def test_sell_geometry_is_directional_and_targets_are_sorted_by_distance():
    record = zone_record(
        direction="SELL",
        ranges=[event("10:00:00", range=[100, 105])],
        levels=[event("10:00:00", tps=[90, 98, 95], sl=110)],
    )

    spec = build_zone_trade_spec(record)

    assert spec.blockers == ()
    assert spec.ready_states[0].direction == "SELL"
    assert spec.ready_states[0].tps == (98.0, 95.0, 90.0)


def test_equal_timestamp_updates_are_stable_and_spec_is_detached():
    record = zone_record(
        ranges=[
            event("10:00:00", range=[100, 105]),
            event("10:00:00", range=[99, 104]),
        ],
        levels=[event("10:00:00", tps=[110], sl=95)],
    )
    original = deepcopy(record)

    spec = build_zone_trade_spec(record)
    record["entry_zone_timeline"][1]["range"][0] = 1

    assert spec.ready_states[-1].zone == (99.0, 104.0)
    assert spec.source_sha256 == build_zone_trade_spec(original).source_sha256
    assert spec.source_sha256 != build_zone_trade_spec(record).source_sha256


def test_management_events_are_sorted_and_deeply_frozen():
    record = zone_record(
        ranges=[event("10:00:00", range=[100, 105])],
        levels=[event("10:00:00", tps=[110], sl=95)],
        management=[
            event("10:03:00", classified_action="PROGRESS_UPDATE"),
            event("10:02:00", classified_action="MOVE_SL_TO_BE"),
        ],
    )

    spec = build_zone_trade_spec(record)

    assert [row["classified_action"] for row in spec.management_events] == [
        "MOVE_SL_TO_BE",
        "PROGRESS_UPDATE",
    ]
    with pytest.raises(TypeError):
        spec.management_events[0]["classified_action"] = "CLOSE_ALL"
    with pytest.raises(FrozenInstanceError):
        spec.channel = "other"


def test_incomplete_zone_returns_one_named_blocked_spec():
    record = zone_record(
        ranges=[],
        levels=[event("10:00:00", tps=[110], sl=95)],
    )

    spec = build_zone_trade_spec(record)

    assert spec.ready_at_utc is None
    assert spec.blockers == ("missing_causal_zone_range",)


def test_adapter_rejects_non_zone_records():
    record = zone_record(
        ranges=[event("10:00:00", range=[100, 105])],
        levels=[event("10:00:00", tps=[110], sl=95)],
    )
    record["record_type"] = "formal_signal"

    with pytest.raises(ValueError, match="zone_plan"):
        build_zone_trade_spec(record)


def test_execution_batch_uses_signal_received_time_not_management_time():
    record = zone_record(
        ranges=[event("10:00:00", range=[100, 105])],
        levels=[event("10:00:00", tps=[110], sl=95)],
    )
    record["execution_batches"] = [{
        "execution_batch_id": "canal2_9000#exec1",
        "signal_received_utc": utc("10:01:00").isoformat(),
        "first_fill_utc": utc("10:01:01").isoformat(),
        "fills": [{
            "observed_utc": utc("10:01:01").isoformat(),
            "price": 104.9,
        }],
    }]

    spec = build_zone_trade_spec(record)

    assert spec.blockers == ()
    assert len(spec.execution_batches) == 1
    assert spec.execution_batches[0]["execution_batch_id"] == (
        "canal2_9000#exec1"
    )
