import json
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone

import pytest

from provider_trade_spec import ProviderTradeSpec, build_trade_spec


def _formal_signal(**overrides):
    signal = {
        "schema_version": 3,
        "provider_signal_id": "canal2_3200",
        "record_type": "formal_signal",
        "channel": "canal2",
        "direction": "SELL",
        "effective_tps": [4108.0, 4110.0, 4112.0, 4115.0, 4118.0, 4120.0],
        "effective_sl": 4095.0,
        "level_timeline": [],
        "management_events": [],
        "execution_sig_ids": [],
        "entry_contract": {
            "status": "ready",
            "trigger_observed_utc": "2026-07-08T11:00:02.300+00:00",
            "trigger_telegram_utc": "2026-07-08T11:00:00+00:00",
            "trigger_message_id": 3200,
            "trigger_kind": "text",
            "direction": "BUY",
            "direction_source": "revision_parser:3200",
            "blockers": [],
        },
    }
    signal.update(overrides)
    return signal


def test_executed_canal2_signal_builds_six_leg_spec_with_execution_evidence():
    signal = _formal_signal(
        execution_sig_ids=["canal2_3200", "canal2_3200_retry"],
    )

    spec = build_trade_spec(signal, latency_ms=250, volume_per_leg=0.01)

    assert isinstance(spec, ProviderTradeSpec)
    assert spec.provider_signal_id == "canal2_3200"
    assert spec.channel == "canal2"
    assert spec.direction == "BUY"
    assert spec.trigger_observed_utc == datetime(
        2026, 7, 8, 11, 0, 2, 300000, tzinfo=timezone.utc
    )
    assert spec.latency_ms == 250
    assert spec.volume_per_leg == 0.01
    assert spec.leg_count == 6
    assert spec.provider_tps == (
        4108.0,
        4110.0,
        4112.0,
        4115.0,
        4118.0,
        4120.0,
    )
    assert spec.provider_sl == 4095.0
    assert spec.execution_sig_ids == (
        "canal2_3200",
        "canal2_3200_retry",
    )
    assert spec.entry_ready is True
    assert spec.policy_evidence_gaps == ()


def test_unexecuted_canal2_signal_builds_same_spec_without_execution_evidence():
    executed = build_trade_spec(
        _formal_signal(execution_sig_ids=["canal2_3200"]),
        latency_ms=250,
        volume_per_leg=0.01,
    )

    unexecuted = build_trade_spec(
        _formal_signal(execution_sig_ids=[]),
        latency_ms=250,
        volume_per_leg=0.01,
    )

    assert unexecuted.execution_sig_ids == ()
    assert unexecuted.entry_ready is True
    assert unexecuted == replace(executed, execution_sig_ids=())


def test_scalar_execution_sig_id_becomes_one_evidence_item_not_characters():
    spec = build_trade_spec(
        _formal_signal(execution_sig_ids="canal2_3200"),
        latency_ms=0,
        volume_per_leg=0.01,
    )

    assert spec.execution_sig_ids == ("canal2_3200",)


def test_execution_sig_id_sequence_is_copied_into_an_independent_tuple():
    execution_sig_ids = ["canal2_3200", "canal2_3201"]

    spec = build_trade_spec(
        _formal_signal(execution_sig_ids=execution_sig_ids),
        latency_ms=0,
        volume_per_leg=0.01,
    )
    execution_sig_ids[0] = "mutated-input"
    execution_sig_ids.append("canal2_3202")

    assert spec.execution_sig_ids == ("canal2_3200", "canal2_3201")


@pytest.mark.parametrize(
    "malformed_ids",
    [
        3200,
        {"sig_id": "canal2_3200"},
        [None],
        [["canal2_3200"]],
        ["canal2_3200", {"sig_id": "canal2_3201"}],
    ],
)
def test_malformed_execution_sig_ids_are_rejected(malformed_ids):
    with pytest.raises(ValueError, match="execution_sig_ids"):
        build_trade_spec(
            _formal_signal(execution_sig_ids=malformed_ids),
            latency_ms=0,
            volume_per_leg=0.01,
        )


def test_direction_only_sticker_remains_entry_ready_with_policy_evidence_gaps():
    signal = _formal_signal(
        effective_tps=[],
        effective_sl=None,
        entry_contract={
            "status": "ready",
            "trigger_observed_utc": "2026-07-08T11:00:02.181Z",
            "trigger_telegram_utc": "2026-07-08T11:00:01+00:00",
            "trigger_message_id": 3200,
            "trigger_kind": "sticker",
            "direction": "SELL",
            "direction_source": "telegram_understood",
            "blockers": [],
        },
    )

    spec = build_trade_spec(signal, latency_ms=0, volume_per_leg=1)

    assert spec.direction == "SELL"
    assert spec.leg_count == 1
    assert spec.provider_tps == ()
    assert spec.provider_sl is None
    assert spec.entry_ready is True
    assert spec.policy_evidence_gaps == (
        "missing_provider_tps",
        "missing_provider_sl",
    )


def test_trade_spec_uses_causal_contract_direction_not_final_signal_direction():
    signal = _formal_signal(direction="SELL")

    spec = build_trade_spec(signal, latency_ms=0, volume_per_leg=0.01)

    assert signal["direction"] == "SELL"
    assert spec.direction == "BUY"


def test_provider_tp_order_is_preserved_instead_of_price_sorted():
    signal = _formal_signal(effective_tps=[4112, 4108, 4115, 4110])

    spec = build_trade_spec(signal, latency_ms=0, volume_per_leg=0.01)

    assert spec.provider_tps == (4112.0, 4108.0, 4115.0, 4110.0)
    assert spec.leg_count == 4


def test_mixed_provider_levels_keep_safe_prices_and_report_original_indexes():
    signal = _formal_signal(
        effective_tps=[
            4112,
            True,
            "not-a-price",
            float("nan"),
            float("inf"),
            -1,
            0,
            4108.5,
        ],
        effective_sl=False,
    )

    spec = build_trade_spec(signal, latency_ms=0, volume_per_leg=0.01)

    assert spec.provider_tps == (4112.0, 4108.5)
    assert spec.leg_count == 2
    assert spec.provider_sl is None
    assert spec.policy_evidence_gaps == (
        "invalid_provider_tp:1",
        "invalid_provider_tp:2",
        "invalid_provider_tp:3",
        "invalid_provider_tp:4",
        "invalid_provider_tp:5",
        "invalid_provider_tp:6",
        "invalid_provider_sl",
        "missing_provider_sl",
    )


def test_all_invalid_provider_tps_leave_one_leg_and_missing_tp_gap():
    signal = _formal_signal(
        effective_tps=[False, "bad", float("nan"), float("inf"), 0, -1],
    )

    spec = build_trade_spec(signal, latency_ms=0, volume_per_leg=0.01)

    assert spec.provider_tps == ()
    assert spec.leg_count == 1
    assert spec.policy_evidence_gaps == (
        "invalid_provider_tp:0",
        "invalid_provider_tp:1",
        "invalid_provider_tp:2",
        "invalid_provider_tp:3",
        "invalid_provider_tp:4",
        "invalid_provider_tp:5",
        "missing_provider_tps",
    )


@pytest.mark.parametrize(
    "unsafe_sl",
    [
        True,
        "4095",
        "bad",
        float("nan"),
        float("inf"),
        pytest.param(10**10000, id="overflowing-int"),
        0,
        -1,
    ],
)
def test_unsafe_provider_sl_is_not_exposed_as_a_valid_price(unsafe_sl):
    spec = build_trade_spec(
        _formal_signal(effective_sl=unsafe_sl),
        latency_ms=0,
        volume_per_leg=0.01,
    )

    assert spec.provider_sl is None
    assert spec.policy_evidence_gaps == (
        "invalid_provider_sl",
        "missing_provider_sl",
    )


@pytest.mark.parametrize("malformed_tps", [True, "4108", {"tp": 4108}])
def test_malformed_tp_container_is_audited_without_dropping_signal(malformed_tps):
    spec = build_trade_spec(
        _formal_signal(effective_tps=malformed_tps),
        latency_ms=0,
        volume_per_leg=0.01,
    )

    assert spec.provider_tps == ()
    assert spec.leg_count == 1
    assert spec.policy_evidence_gaps == (
        "invalid_provider_tp:0",
        "missing_provider_tps",
    )


@pytest.mark.parametrize(
    ("trigger", "expected_timestamp_blocker"),
    [
        (None, "missing_trigger_observed_utc"),
        ("not-a-timestamp", "invalid_trigger_observed_utc"),
        ("2026-07-08T11:00:02.300", "invalid_trigger_observed_utc"),
    ],
)
def test_contract_and_timestamp_blockers_are_inherited_added_and_deduplicated(
    trigger,
    expected_timestamp_blocker,
):
    signal = _formal_signal(
        entry_contract={
            "status": "blocked",
            "trigger_observed_utc": trigger,
            "direction": None,
            "blockers": ["manual_review", "missing_direction", "manual_review"],
        }
    )

    spec = build_trade_spec(signal, latency_ms=0, volume_per_leg=0.01)

    assert spec.trigger_observed_utc is None
    assert spec.entry_ready is False
    assert spec.entry_blockers == (
        "manual_review",
        "missing_direction",
        expected_timestamp_blocker,
    )


def test_blocked_contract_without_reason_gets_explicit_status_blocker():
    entry_contract = {
        **_formal_signal()["entry_contract"],
        "status": "blocked",
        "blockers": [],
    }

    spec = build_trade_spec(
        _formal_signal(entry_contract=entry_contract),
        latency_ms=0,
        volume_per_leg=0.01,
    )

    assert spec.entry_ready is False
    assert spec.entry_blockers == ("contract_status_blocked",)


@pytest.mark.parametrize("status", [None, "pending", "unexpected"])
def test_unknown_contract_status_cannot_silently_become_entry_ready(status):
    entry_contract = {
        **_formal_signal()["entry_contract"],
        "status": status,
        "blockers": [],
    }

    spec = build_trade_spec(
        _formal_signal(entry_contract=entry_contract),
        latency_ms=0,
        volume_per_leg=0.01,
    )

    assert spec.entry_ready is False
    assert spec.entry_blockers == ("invalid_contract_status",)


@pytest.mark.parametrize("record_type", [None, "management_only", "unknown_candidate"])
def test_non_formal_records_are_rejected(record_type):
    signal = _formal_signal(record_type=record_type)

    with pytest.raises(ValueError, match="formal_signal"):
        build_trade_spec(signal, latency_ms=0, volume_per_leg=0.01)


@pytest.mark.parametrize("latency_ms", [-1, 1.5, "1", True, None])
def test_invalid_latency_is_rejected(latency_ms):
    with pytest.raises(ValueError, match="latency_ms"):
        build_trade_spec(
            _formal_signal(),
            latency_ms=latency_ms,
            volume_per_leg=0.01,
        )


@pytest.mark.parametrize(
    "volume_per_leg",
    [0, -0.01, "0.01", True, None, float("nan"), float("inf")],
)
def test_invalid_volume_is_rejected(volume_per_leg):
    with pytest.raises(ValueError, match="volume_per_leg"):
        build_trade_spec(
            _formal_signal(),
            latency_ms=0,
            volume_per_leg=volume_per_leg,
        )


def test_timelines_are_causally_sorted_deeply_immutable_and_detached_from_input():
    offset_early = {
        "observed_ts_utc": "2026-07-08T09:00:00+02:00",
        "source_message_id": 3201,
        "metadata": {"tags": ["provider-edit"]},
    }
    utc_late = {
        "observed_ts_utc": "2026-07-08T08:00:00+00:00",
        "source_message_id": 3202,
        "metadata": {"tags": ["provider-text"]},
    }
    management_late = {
        "observed_ts_utc": "2026-07-08T08:30:00+00:00",
        "message_id": 3204,
        "execution_options": [{"action": "CLOSE_ALL"}],
    }
    management_early = {
        "observed_ts_utc": "2026-07-08T10:00:00+03:00",
        "message_id": 3203,
        "execution_options": [{"action": "MOVE_SL_TO_BE"}],
    }
    signal = _formal_signal(
        level_timeline=[utc_late, offset_early],
        management_events=[management_late, management_early],
        entry_contract={
            **_formal_signal()["entry_contract"],
            "trigger_observed_utc": "2026-07-08T13:00:02.300+02:00",
        },
    )

    spec = build_trade_spec(signal, latency_ms=0, volume_per_leg=0.01)

    assert spec.trigger_observed_utc == datetime(
        2026, 7, 8, 11, 0, 2, 300000, tzinfo=timezone.utc
    )
    assert spec.trigger_observed_utc.tzinfo is timezone.utc
    assert [row["source_message_id"] for row in spec.level_timeline] == [
        3201,
        3202,
    ]
    assert [row["message_id"] for row in spec.management_events] == [3203, 3204]

    offset_early["metadata"]["tags"].append("mutated-input")
    management_early["execution_options"][0]["action"] = "MUTATED_INPUT"
    signal["level_timeline"].clear()

    assert spec.level_timeline[0]["metadata"]["tags"] == ("provider-edit",)
    assert (
        spec.management_events[0]["execution_options"][0]["action"]
        == "MOVE_SL_TO_BE"
    )

    with pytest.raises(FrozenInstanceError):
        spec.latency_ms = 100
    with pytest.raises(TypeError):
        spec.level_timeline[0]["source_message_id"] = 9999
    with pytest.raises(TypeError):
        spec.level_timeline[0]["metadata"]["new_key"] = "forbidden"
    with pytest.raises(AttributeError):
        spec.level_timeline[0]["metadata"]["tags"].append("forbidden")


def test_invalid_timeline_timestamps_are_retained_and_reported_by_source_index():
    valid_level_late = {
        "observed_ts_utc": "2026-07-08T08:30:00+00:00",
        "source_message_id": 3302,
    }
    invalid_level = {
        "observed_ts_utc": "not-a-timestamp",
        "source_message_id": 3399,
        "raw": {"versions": ["edit"]},
    }
    valid_level_early = {
        "observed_ts_utc": "2026-07-08T08:00:00+00:00",
        "source_message_id": 3301,
    }
    invalid_management = {
        "observed_ts_utc": "2026-07-08T09:00:00",
        "message_id": 3499,
        "raw": {"actions": ["CLOSE_ALL"]},
    }
    valid_management = {
        "observed_ts_utc": "2026-07-08T08:15:00Z",
        "message_id": 3401,
    }

    spec = build_trade_spec(
        _formal_signal(
            level_timeline=[valid_level_late, invalid_level, valid_level_early],
            management_events=[invalid_management, valid_management],
        ),
        latency_ms=0,
        volume_per_leg=0.01,
    )

    assert [row["source_message_id"] for row in spec.level_timeline] == [
        3301,
        3302,
        3399,
    ]
    assert [row["message_id"] for row in spec.management_events] == [3401, 3499]
    assert spec.policy_evidence_gaps == (
        "invalid_level_timeline_observed_ts:1",
        "invalid_management_event_observed_ts:0",
    )

    invalid_level["raw"]["versions"].append("mutated-input")
    invalid_management["raw"]["actions"].clear()

    assert spec.level_timeline[-1]["raw"]["versions"] == ("edit",)
    assert spec.management_events[-1]["raw"]["actions"] == ("CLOSE_ALL",)
    with pytest.raises(TypeError):
        spec.level_timeline[-1]["raw"]["new_key"] = "forbidden"


def test_to_dict_returns_detached_plain_json_safe_content():
    observed = datetime(
        2026,
        7,
        8,
        10,
        0,
        1,
        123456,
        tzinfo=timezone.utc,
    )
    signal = _formal_signal(
        execution_sig_ids=["canal2_3200"],
        level_timeline=[{
            "observed_ts_utc": observed,
            "source_message_id": 3200,
            "metadata": {
                "tags": ["provider-edit"],
                "seen_at": observed,
            },
        }],
    )

    spec = build_trade_spec(signal, latency_ms=250, volume_per_leg=0.01)
    payload = spec.to_dict()
    encoded = json.dumps(payload, sort_keys=True)

    assert "to_dict" in (ProviderTradeSpec.__doc__ or "")
    assert payload["trigger_observed_utc"] == "2026-07-08T11:00:02.300000+00:00"
    assert payload["provider_tps"] == [
        4108.0,
        4110.0,
        4112.0,
        4115.0,
        4118.0,
        4120.0,
    ]
    assert payload["execution_sig_ids"] == ["canal2_3200"]
    assert payload["level_timeline"] == [{
        "observed_ts_utc": "2026-07-08T10:00:01.123456+00:00",
        "source_message_id": 3200,
        "metadata": {
            "tags": ["provider-edit"],
            "seen_at": "2026-07-08T10:00:01.123456+00:00",
        },
    }]
    assert json.loads(encoded) == payload

    payload["provider_tps"].append(9999.0)
    payload["level_timeline"][0]["metadata"]["tags"].append("mutated-output")

    assert spec.provider_tps[-1] == 4120.0
    assert spec.level_timeline[0]["metadata"]["tags"] == ("provider-edit",)
