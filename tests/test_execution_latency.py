from types import SimpleNamespace

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
        "reason": "fewer_than_30_successful_market_samples",
        "sample_count": 1,
        "basis": "decision_to_broker_response_ms",
        "p50_ms": 300,
        "p90_ms": 300,
        "p99_ms": 300,
        "scenarios_ms": [300],
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
