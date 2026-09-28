from types import SimpleNamespace

import pytest

from execution_latency import summarize_events, terminal_network_fields


def test_terminal_network_fields_normalize_background_mt5_metrics():
    fields = terminal_network_fields(
        SimpleNamespace(ping_last=123_456, retransmission=0.75)
    )

    assert fields == {
        "terminal_ping_us": 123_456,
        "terminal_ping_ms": 123.456,
        "terminal_retransmission_pct": 0.75,
    }


def test_terminal_network_fields_fail_open_for_old_terminal_builds():
    assert terminal_network_fields(SimpleNamespace()) == {
        "terminal_ping_us": None,
        "terminal_ping_ms": None,
        "terminal_retransmission_pct": None,
    }


def test_latency_summary_separates_decision_broker_and_terminal_stages():
    events = [
        {
            "ev": "telegram_decision_started",
            "sig": "canal2_380",
            "session_id": "session_1",
            "decision_id": "decision_1",
            "monotonic_ns": 50_000_000,
        },
        {
            "ev": "mt5_action_attempt",
            "sig": "canal2_380",
            "session_id": "session_1",
            "decision_id": "decision_1",
            "operation": "OPEN_MARKET",
            "attempt_started_monotonic_ns": 100_000_000,
            "broker_request_started_monotonic_ns": 200_000_000,
            "broker_response_received_monotonic_ns": 350_000_000,
            "attempt_finished_monotonic_ns": 400_000_000,
            "pre_broker_duration_ns": 100_000_000,
            "broker_roundtrip_ns": 150_000_000,
            "post_broker_duration_ns": 50_000_000,
            "adverse_slippage_xau": 0.02,
            "result": {"retcode": 10009},
        },
        {
            "ev": "handler_entry",
            "sig": "canal2_380",
            "channel": "canal2",
            "kind": "new",
            "telegram_to_handler_ms": 25,
        },
        {
            "ev": "mt5_connection_beat",
            "sig": "bot",
            "terminal_ping_ms": 123.456,
        },
    ]

    report = summarize_events(events)

    assert report["broker_roundtrip_ms"]["overall"] == {
        "count": 1,
        "mean": 150.0,
        "p50": 150.0,
        "p90": 150.0,
        "p95": 150.0,
        "p99": 150.0,
        "max": 150.0,
    }
    assert report["pre_broker_ms"]["overall"]["p50"] == 100.0
    assert report["post_broker_ms"]["overall"]["p50"] == 50.0
    assert report["decision_to_broker_response_ms"]["overall"]["p50"] == 300.0
    assert report["telegram_transport_ms"]["overall"]["p50"] == 25.0
    assert report["terminal_ping_ms"]["overall"]["p50"] == 123.456
    assert report["adverse_slippage_xau"]["overall"]["p50"] == 0.02
    assert report["simulation_latency_scenarios"] == {
        "status": "diagnostic_only",
        "reason": "fewer_than_30_identified_successful_market_samples",
        "sample_count": 0,
        "diagnostic_sample_count": 1,
        "basis": "decision_to_broker_response_ms",
        "p50_ms": None,
        "p90_ms": None,
        "p99_ms": None,
        "scenarios_ms": [],
    }


def test_cross_event_latency_never_joins_different_process_sessions():
    report = summarize_events([
        {
            "ev": "telegram_decision_started",
            "sig": "canal1_10",
            "session_id": "old",
            "decision_id": "reused",
            "monotonic_ns": 10,
        },
        {
            "ev": "mt5_action_attempt",
            "sig": "canal1_10",
            "session_id": "new",
            "decision_id": "reused",
            "operation": "OPEN_MARKET",
            "broker_response_received_monotonic_ns": 30,
            "broker_roundtrip_ns": 10,
            "result": {"retcode": 10009},
        },
    ])

    assert report["decision_to_broker_response_ms"]["overall"] is None
    assert report["simulation_latency_scenarios"]["sample_count"] == 0


def test_tampered_versioned_stage_durations_are_excluded():
    report = summarize_events([{
        "ev": "mt5_action_attempt",
        "sig": "canal2_380",
        "timing_schema_version": 1,
        "broker_request_sent": True,
        "operation": "OPEN_MARKET",
        "attempt_started_monotonic_ns": 100,
        "broker_request_started_monotonic_ns": 200,
        "broker_response_received_monotonic_ns": 400,
        "attempt_finished_monotonic_ns": 500,
        "pre_broker_duration_ns": 100,
        "broker_roundtrip_ns": 999,
        "post_broker_duration_ns": 100,
        "result": {"retcode": 10009},
    }])

    assert report["invalid_timing_samples"] == 1
    assert report["broker_roundtrip_ms"]["overall"] is None
    assert report["simulation_latency_scenarios"]["sample_count"] == 0


def test_duplicate_attempt_identity_cannot_satisfy_latency_sample_gate():
    start = {
        "ev": "bot_internal_decision_started",
        "session_id": "session-1",
        "decision_id": "decision-1",
        "event_id": "decision-start",
        "monotonic_ns": 0,
    }
    attempt = {
        "ev": "mt5_action_attempt",
        "session_id": "session-1",
        "decision_id": "decision-1",
        "event_id": "one-attempt",
        "attempt_id": "one-attempt",
        "operation": "OPEN_MARKET",
        "result": {"retcode": 10009},
        "timing_schema_version": 1,
        "broker_request_sent": True,
        "attempt_timing_complete": True,
        "duration_ns": 5_000_000,
        "attempt_started_monotonic_ns": 0,
        "broker_request_started_monotonic_ns": 1_000_000,
        "broker_response_received_monotonic_ns": 4_000_000,
        "attempt_finished_monotonic_ns": 5_000_000,
        "pre_broker_duration_ns": 1_000_000,
        "broker_roundtrip_ns": 3_000_000,
        "post_broker_duration_ns": 1_000_000,
    }

    report = summarize_events([start, *[dict(attempt) for _ in range(30)]])

    assert report["attempt_samples"] == 1
    assert report["duplicate_attempt_samples"] == 29
    assert report["simulation_latency_scenarios"]["sample_count"] == 1
    assert report["simulation_latency_scenarios"]["status"] == "diagnostic_only"


def test_conflicting_decision_start_identity_is_not_joined_to_attempt():
    starts = [
        {
            "ev": "bot_internal_decision_started",
            "session_id": "session-1",
            "decision_id": "decision-1",
            "event_id": event_id,
            "monotonic_ns": monotonic_ns,
        }
        for event_id, monotonic_ns in (("start-a", 1_000_000), ("start-b", 2_000_000))
    ]
    attempt = {
        "ev": "mt5_action_attempt",
        "session_id": "session-1",
        "decision_id": "decision-1",
        "event_id": "attempt-a",
        "attempt_id": "attempt-a",
        "operation": "OPEN_MARKET",
        "broker_response_received_monotonic_ns": 4_000_000,
        "broker_roundtrip_ns": 1_000_000,
        "result": {"retcode": 10009},
    }

    report = summarize_events([*starts, attempt])

    assert report["conflicting_decision_identities"] == 1
    assert report["decision_to_broker_response_ms"]["overall"] is None
    assert report["simulation_latency_scenarios"]["sample_count"] == 0


def test_conflicting_attempt_identity_is_excluded_from_latency_metrics():
    attempt = {
        "ev": "mt5_action_attempt",
        "session_id": "session-1",
        "decision_id": "decision-1",
        "event_id": "attempt-event-a",
        "attempt_id": "attempt-a",
        "operation": "OPEN_MARKET",
        "broker_response_received_monotonic_ns": 4_000_000,
        "broker_roundtrip_ns": 1_000_000,
        "result": {"retcode": 10009},
    }
    conflicting = {
        **attempt,
        "event_id": "attempt-event-b",
        "broker_response_received_monotonic_ns": 5_000_000,
        "broker_roundtrip_ns": 2_000_000,
    }

    report = summarize_events([attempt, conflicting])

    assert report["attempt_samples"] == 0
    assert report["duplicate_attempt_samples"] == 0
    assert report["conflicting_attempt_identities"] == 1
    assert report["broker_roundtrip_ms"]["overall"] is None
    assert report["simulation_latency_scenarios"]["sample_count"] == 0


def test_unidentified_attempt_copies_are_diagnostic_only():
    start = {
        "ev": "bot_internal_decision_started",
        "session_id": "session-1",
        "decision_id": "decision-1",
        "event_id": "decision-start",
        "monotonic_ns": 0,
    }
    attempt = {
        "ev": "mt5_action_attempt",
        "session_id": "session-1",
        "decision_id": "decision-1",
        "operation": "OPEN_MARKET",
        "broker_response_received_monotonic_ns": 4_000_000,
        "broker_roundtrip_ns": 1_000_000,
        "result": {"retcode": 10009},
    }

    report = summarize_events([start, *[dict(attempt) for _ in range(30)]])

    assert report["attempt_samples"] == 30
    assert report["unidentified_attempt_samples"] == 30
    assert report["simulation_latency_scenarios"]["sample_count"] == 0
    assert report["simulation_latency_scenarios"]["diagnostic_sample_count"] == 30
    assert report["simulation_latency_scenarios"]["status"] == "diagnostic_only"


def test_shared_event_id_with_incompatible_attempt_ids_is_conflicting():
    attempts = [
        {
            "ev": "mt5_action_attempt",
            "session_id": "session-1",
            "decision_id": "decision-1",
            "event_id": "shared-event",
            "attempt_id": f"attempt-{index}",
            "operation": "OPEN_MARKET",
            "broker_response_received_monotonic_ns": 4_000_000,
            "broker_roundtrip_ns": 1_000_000,
            "result": {"retcode": 10009},
        }
        for index in range(30)
    ]

    report = summarize_events(attempts)

    assert report["attempt_samples"] == 0
    assert report["conflicting_attempt_identities"] == 1
    assert report["simulation_latency_scenarios"]["sample_count"] == 0


def test_nonfinite_optional_metric_does_not_abort_latency_summary():
    attempt = {
        "ev": "mt5_action_attempt",
        "event_id": "event-a",
        "attempt_id": "attempt-a",
        "operation": "OPEN_MARKET",
        "broker_roundtrip_ns": 1_000_000,
        "adverse_slippage_xau": float("nan"),
        "result": {"retcode": 10009},
    }

    report = summarize_events([attempt])

    assert report["attempt_samples"] == 1
    assert report["invalid_numeric_samples"] == 1
    assert report["adverse_slippage_xau"]["overall"] is None


def test_shared_decision_event_id_with_incompatible_decision_ids_is_conflicting():
    starts = [
        {
            "ev": "bot_internal_decision_started",
            "session_id": "session-1",
            "decision_id": f"decision-{index}",
            "event_id": "shared-decision-event",
            "monotonic_ns": 0,
        }
        for index in range(30)
    ]
    attempts = [
        {
            "ev": "mt5_action_attempt",
            "session_id": "session-1",
            "decision_id": f"decision-{index}",
            "event_id": f"attempt-event-{index}",
            "attempt_id": f"attempt-{index}",
            "operation": "OPEN_MARKET",
            "broker_response_received_monotonic_ns": 4_000_000,
            "broker_roundtrip_ns": 1_000_000,
            "result": {"retcode": 10009},
        }
        for index in range(30)
    ]

    report = summarize_events([*starts, *attempts])

    assert report["conflicting_decision_identities"] == 1
    assert report["simulation_latency_scenarios"]["sample_count"] == 0
    assert report["simulation_latency_scenarios"]["status"] == "diagnostic_only"


def test_shared_attempt_event_id_across_sessions_is_conflicting():
    events = []
    for index in range(30):
        events.extend([
            {
                "ev": "bot_internal_decision_started",
                "session_id": f"session-{index}",
                "decision_id": "decision",
                "event_id": f"decision-event-{index}",
                "monotonic_ns": 0,
            },
            {
                "ev": "mt5_action_attempt",
                "session_id": f"session-{index}",
                "decision_id": "decision",
                "event_id": "shared-attempt-event",
                "attempt_id": f"attempt-{index}",
                "operation": "OPEN_MARKET",
                "broker_response_received_monotonic_ns": 4_000_000,
                "broker_roundtrip_ns": 1_000_000,
                "result": {"retcode": 10009},
            },
        ])

    report = summarize_events(events)

    assert report["attempt_samples"] == 0
    assert report["conflicting_attempt_identities"] == 1
    assert report["simulation_latency_scenarios"]["sample_count"] == 0


def _identified_latency_cohort(count=30):
    events = []
    for index in range(count):
        common = {
            "session_id": f"session-{index}",
            "decision_id": f"decision-{index}",
        }
        events.extend([
            {
                **common,
                "ev": "bot_internal_decision_started",
                "event_id": f"decision-event-{index}",
                "monotonic_ns": 0,
            },
            {
                **common,
                "ev": "mt5_action_attempt",
                "event_id": f"attempt-event-{index}",
                "attempt_id": f"attempt-{index}",
                "operation": "OPEN_MARKET",
                "broker_response_received_monotonic_ns": 4_000_000,
                "broker_roundtrip_ns": 1_000_000,
                "result": {"retcode": 10009},
            },
        ])
    return events


@pytest.mark.parametrize("reverse", [False, True])
def test_event_id_reused_between_decisions_and_attempts_is_conflicting(reverse):
    events = _identified_latency_cohort()
    starts = events[::2]
    attempts = events[1::2]
    for index, attempt in enumerate(attempts):
        attempt["event_id"] = starts[(index + 1) % len(starts)]["event_id"]
    if reverse:
        events.reverse()

    report = summarize_events(events)

    assert report["conflicting_decision_identities"] == 30
    assert report["conflicting_attempt_identities"] == 30
    assert report["attempt_samples"] == 0
    assert report["simulation_latency_scenarios"]["sample_count"] == 0
    assert report["simulation_latency_scenarios"]["status"] == "diagnostic_only"


def test_cross_kind_event_id_conflict_preserves_unrelated_samples():
    events = _identified_latency_cohort()
    events[1]["event_id"] = events[2]["event_id"]

    report = summarize_events(events)

    assert report["conflicting_decision_identities"] == 1
    assert report["conflicting_attempt_identities"] == 1
    assert report["attempt_samples"] == 29
    assert report["simulation_latency_scenarios"]["sample_count"] == 28
    assert report["simulation_latency_scenarios"]["status"] == "diagnostic_only"


@pytest.mark.parametrize("retcode", [float("inf"), -float("inf")])
def test_nonfinite_retcode_does_not_abort_latency_summary(retcode):
    valid = {
        "ev": "mt5_action_attempt",
        "event_id": "valid-event",
        "attempt_id": "valid-attempt",
        "operation": "OPEN_MARKET",
        "broker_roundtrip_ns": 1_000_000,
        "result": {"retcode": 10009},
    }
    invalid = {
        **valid,
        "event_id": "invalid-event",
        "attempt_id": "invalid-attempt",
        "result": {"retcode": retcode},
    }

    report = summarize_events([valid, invalid])

    assert report["schema_version"] == 4
    assert report["attempt_samples"] == 2
    assert report["invalid_numeric_samples"] == 1
