from datetime import datetime, timedelta, timezone

from canal2_zone_lifecycle import (
    LIFECYCLE_SCHEMA_VERSION,
    classify_followup,
    is_executable,
    is_expired,
    merge_plan_record,
    new_plan_record,
    touch_decision,
)


def _complete_plan(direction="BUY"):
    return {
        "direction": direction,
        "zones": [[4053.0, 4058.0]],
        "target": None,
        "tps": [4060.0, 4062.0],
        "sl": 4050.0 if direction == "BUY" else 4061.0,
        "has_open_runner": True,
    }


def _record(direction="BUY", now=None):
    observed_now = now or datetime.now(timezone.utc)
    return new_plan_record(
        _complete_plan(direction),
        message_id=500,
        root_message_id=500,
        raw_text="formal plan",
        tg_ts="2026-08-05T09:00:00+00:00",
        source_kind="new",
        now_utc=observed_now,
    )


def test_complete_plan_is_armed_with_versioned_identity():
    record = _record()

    assert record["lifecycle_schema_version"] == LIFECYCLE_SCHEMA_VERSION == 2
    assert record["status"] == "armed"
    assert record["message_id"] == 500
    assert record["thread_root_message_id"] == 500
    assert record["aliases"] == [500]
    assert record["consumed"] is False
    assert is_executable(record) is True


def test_incomplete_or_multi_zone_context_is_not_executable():
    incomplete = _complete_plan()
    incomplete["sl"] = None
    multi_zone = _complete_plan()
    multi_zone["zones"] = [[4053.0, 4058.0], [4048.0, 4050.0]]

    assert is_executable(incomplete) is False
    assert is_executable(multi_zone) is False
    assert new_plan_record(
        multi_zone,
        message_id=501,
        root_message_id=501,
        raw_text="session map",
        tg_ts=None,
        source_kind="new",
    )["status"] == "draft"


def test_buy_touch_uses_ask_and_sell_touch_uses_bid():
    buy = _record("BUY")
    sell = _record("SELL")
    sell["zones"] = [[4053.0, 4058.0]]
    tick = {
        "bid": 4057.90,
        "ask": 4058.10,
        "time": 1785920400,
        "time_msc": 1785920400123,
    }

    assert touch_decision(buy, tick) is None
    assert touch_decision(sell, tick) == {
        "trigger": "first_touch",
        "side": "bid",
        "price": 4057.9,
        "time": 1785920400,
        "time_msc": 1785920400123,
        "zone": [4053.0, 4058.0],
    }


def test_consumed_plan_cannot_trigger_twice():
    record = _record()
    record["consumed"] = True

    assert touch_decision(record, {"bid": 4054.0, "ask": 4054.2}) is None


def test_recovered_legacy_context_cannot_trigger_without_fresh_rearm():
    record = _record()
    record["execution_eligible"] = False

    assert touch_decision(record, {"bid": 4054.0, "ask": 4054.2}) is None


def test_followup_classifier_keeps_statuses_distinct():
    assert classify_followup("Approaching the buy zone") == ["APPROACHING"]
    assert classify_followup("Active") == ["ACTIVATE"]
    assert classify_followup("You can enter now") == ["ACTIVATE"]
    assert classify_followup("Left without us") == ["MISSED"]
    assert classify_followup("Still valid if it comes down") == ["REARM"]
    assert classify_followup("I am re entering") == ["REENTRY"]
    assert classify_followup("Do not re-enter") == ["NO_REENTRY"]
    assert classify_followup("Zone failed and is no longer valid") == ["INVALIDATE"]
    assert classify_followup("This remains valid for Asia overnight") == [
        "EXTEND_VALIDITY"
    ]


def test_full_update_arms_draft_and_preserves_activation_request():
    parsed = _complete_plan()
    parsed["tps"] = []
    parsed["sl"] = None
    record = new_plan_record(
        parsed,
        message_id=600,
        root_message_id=600,
        raw_text="draft",
        tg_ts=None,
        source_kind="new",
    )
    record["activation_requested"] = True

    merged, changes = merge_plan_record(
        record,
        {"tps": [4060.0, 4062.0], "sl": 4050.0},
        raw_text="Active with final levels",
        tg_ts="2026-08-05T09:01:00+00:00",
    )

    assert set(changes) == {"tps", "sl", "raw_text", "tg_ts", "status"}
    assert merged["status"] == "armed"
    assert merged["activation_requested"] is True
    assert is_executable(merged) is True


def test_expiry_is_timezone_safe_and_default_is_24_hours():
    started = datetime(2026, 8, 5, 9, 0, tzinfo=timezone.utc)
    record = _record(now=started)

    assert is_expired(record, started + timedelta(hours=23, minutes=59)) is False
    assert is_expired(record, started + timedelta(hours=24)) is True


def test_extended_validity_moves_expiry_forward():
    started = datetime(2026, 8, 5, 9, 0, tzinfo=timezone.utc)
    record = _record(now=started)
    merged, changes = merge_plan_record(
        record,
        {},
        extend_validity_hours=12,
        now_utc=started + timedelta(hours=12),
    )

    assert changes == ["expires_utc"]
    assert is_expired(merged, started + timedelta(hours=35)) is False
    assert is_expired(merged, started + timedelta(hours=36)) is True
