import json

import pytest

import recursive_log_learning as learning


def _invalid_stop(ticket, *, ts="2026-07-13T06:42:46+00:00", attempts=234):
    return {
        "ts": ts,
        "sig": "canal2_3099",
        "ev": "mt5_action_failed",
        "kind": "MODIFY_SLTP",
        "ticket": ticket,
        "attempts": attempts,
        "last_retcode": 10016,
        "reason": f"stops_structural_after_{attempts}_attempts_30s",
        "new_sl": 4059.61,
    }


def _formal_catalog(*, complete=True, unknown=0, unclassified_management=0):
    management = [
        {
            "message_id": 101 + idx,
            "semantic_source": "unclassified",
            "classified_action": None,
            "raw_versions": 1,
        }
        for idx in range(unclassified_management)
    ]
    signals = [{
        "provider_signal_id": "canal2_100",
        "record_type": "formal_signal",
        "channel": "canal2",
        "signal_ts_utc": "2026-07-13T06:00:00+00:00",
        "semantic_status": "complete" if complete else "incomplete",
        "semantic_gaps": [] if complete else ["missing_tps"],
        "management_events": management,
        "execution_count": 1,
        "duplicate_execution": False,
    }]
    for idx in range(unknown):
        signals.append({
            "provider_signal_id": f"canal2_{200 + idx}",
            "record_type": "unknown_candidate",
            "channel": "canal2",
            "signal_ts_utc": "2026-07-13T07:00:00+00:00",
            "semantic_status": "needs_review",
            "semantic_gaps": ["unclassified_record_type"],
            "management_events": [],
            "execution_count": 0,
            "duplicate_execution": False,
        })
    return {"schema_version": 2, "signals": signals}


def _exact_accounting():
    return [{
        "sig_id": "canal2_100",
        "channel": "canal2",
        "signal_dt_utc": "2026-07-13T06:00:00+00:00",
        "status": "exact",
        "diff": 0.0,
    }]


def _exact_observed():
    return [{
        "sig_id": "canal2_100",
        "channel": "canal2",
        "status": "exact",
        "ticket_count": 1,
        "exact_tickets": 1,
        "blocked_tickets": 0,
        "mismatch_tickets": 0,
        "blockers": [],
    }]


def _verified_strategy():
    return {
        "validation": {
            "artifact_integrity_verified": True,
            "market_replay_verified": True,
            "conclusions_allowed": True,
            "mode": "verified_simulation",
        }
    }


def _build(**overrides):
    values = {
        "events": [],
        "replay_rows": [{"sig_id": "canal2_100", "simulation_blockers": []}],
        "accounting_rows": _exact_accounting(),
        "observed_rows": _exact_observed(),
        "provider_catalog": _formal_catalog(),
        "strategy_farm": _verified_strategy(),
        "review_metadata": {},
    }
    values.update(overrides)
    return learning.build_learning_outputs(**values)


def test_repeated_ticket_failures_collapse_to_one_structural_incident():
    events = [_invalid_stop(ticket) for ticket in range(1, 6)]

    outputs = _build(events=events)
    pattern = next(
        row for row in outputs.registry["patterns"]
        if row["pattern_id"] == "execution.invalid_stops.modify_sltp"
    )

    assert pattern["occurrences"] == 1
    assert pattern["raw_events"] == 1170
    assert pattern["affected_signal_count"] == 1
    assert pattern["affected_day_count"] == 1
    assert pattern["status"] == "candidate"
    assert pattern["evidence"][0]["signal"] == "canal2_3099"


def test_more_logs_increase_recurrence_without_changing_pattern_identity():
    events = [
        _invalid_stop(1),
        {
            **_invalid_stop(2, ts="2026-07-14T07:00:00+00:00", attempts=1),
            "sig": "canal2_3200",
        },
    ]

    outputs = _build(events=events)
    pattern = next(
        row for row in outputs.registry["patterns"]
        if row["pattern_id"] == "execution.invalid_stops.modify_sltp"
    )

    assert pattern["pattern_id"] == "execution.invalid_stops.modify_sltp"
    assert pattern["occurrences"] == 2
    assert pattern["affected_day_count"] == 2
    assert pattern["recurrence"] == "cross_session"
    assert pattern["raw_events"] == 235


def test_latest_day_delta_separates_new_patterns_from_known_recurrences():
    events = [
        _invalid_stop(1, ts="2026-07-13T07:00:00+00:00", attempts=1),
        _invalid_stop(2, ts="2026-07-14T07:00:00+00:00", attempts=1),
        {
            "ts": "2026-07-14T08:00:00+00:00",
            "sig": "bot",
            "ev": "notify_failed",
            "method": "telegram",
            "error": "timeout",
        },
    ]

    outputs = _build(events=events)
    delta = outputs.report["learning_flywheel"]["latest_day_delta"]

    assert delta["evidence_day"] == "2026-07-14"
    assert delta["new_patterns"] == [
        "observability.notification_delivery_failed"]
    assert delta["recurring_patterns"] == [
        "execution.invalid_stops.modify_sltp"]


def test_rerunning_same_corpus_is_byte_deterministic(tmp_path):
    kwargs = {
        "events": [_invalid_stop(ticket) for ticket in range(1, 3)],
        "replay_rows": [{"sig_id": "canal2_100", "simulation_blockers": []}],
        "accounting_rows": _exact_accounting(),
        "observed_rows": _exact_observed(),
        "provider_catalog": _formal_catalog(),
        "strategy_farm": _verified_strategy(),
        "review_metadata": {},
    }

    first = learning.write_learning_outputs(output_dir=tmp_path, **kwargs)
    second = learning.write_learning_outputs(output_dir=tmp_path, **kwargs)

    assert first.report_bytes == second.report_bytes
    assert first.registry_bytes == second.registry_bytes
    assert (tmp_path / "log_learning_report.json").read_bytes() == first.report_bytes
    assert (tmp_path / "log_pattern_registry.json").read_bytes() == first.registry_bytes


def test_strategy_farm_generation_time_is_not_part_of_learning_identity():
    first = _build(strategy_farm={
        **_verified_strategy(),
        "generated_at": "2026-07-14T08:00:00+00:00",
    })
    second = _build(strategy_farm={
        **_verified_strategy(),
        "generated_at": "2026-07-14T09:00:00+00:00",
    })

    assert first.registry_bytes == second.registry_bytes
    assert first.report_bytes == second.report_bytes


@pytest.mark.parametrize(
    "review",
    [
        {"status": "covered"},
        {
            "status": "covered",
            "rule_version": "executor.preflight.v1",
            "regression_test": "tests/test_pending_actions.py::test_preflight",
        },
        {"status": "dismissed"},
    ],
)
def test_reviewed_status_requires_auditable_human_evidence(review):
    with pytest.raises(ValueError, match="review evidence"):
        learning.merge_review_metadata(
            {"pattern_id": "execution.invalid_stops.modify_sltp"}, review)


def test_covered_status_requires_successful_whole_corpus_shadow_evaluation():
    review = {
        "status": "covered",
        "rule_version": "executor.preflight.v1",
        "regression_test": "tests/test_pending_actions.py::test_preflight",
        "reviewed_by": "project_owner",
        "reviewed_at_utc": "2026-07-14T08:00:00+00:00",
        "covered_after_utc": "2026-07-14T08:00:00+00:00",
        "shadow_corpus_passed": False,
    }

    with pytest.raises(ValueError, match="shadow corpus"):
        learning.merge_review_metadata(
            {"pattern_id": "execution.invalid_stops.modify_sltp"}, review)


def test_covered_pattern_becomes_regressed_only_after_coverage_timestamp():
    review = {
        "status": "covered",
        "rule_version": "executor.preflight.v1",
        "regression_test": (
            "tests/test_pending_actions.py::"
            "test_temporarily_invalid_stop_waits_without_mt5_submission"
        ),
        "reviewed_by": "project_owner",
        "reviewed_at_utc": "2026-07-14T08:00:00+00:00",
        "covered_after_utc": "2026-07-14T08:00:00+00:00",
        "shadow_corpus_passed": True,
    }
    old = _build(
        events=[_invalid_stop(1)],
        review_metadata={"execution.invalid_stops.modify_sltp": review},
    )
    new = _build(
        events=[_invalid_stop(1, ts="2026-07-14T09:00:00+00:00", attempts=1)],
        review_metadata={"execution.invalid_stops.modify_sltp": review},
    )

    assert old.registry["patterns"][0]["status"] == "covered"
    assert new.registry["patterns"][0]["status"] == "regressed"
    assert new.report["learning_flywheel"]["regressed_patterns"] == 1


def test_strategy_gate_requires_every_independent_health_layer():
    outputs = _build(observed_rows=[{
        "sig_id": "canal2_100",
        "channel": "canal2",
        "status": "blocked",
        "ticket_count": 1,
        "exact_tickets": 0,
        "blocked_tickets": 1,
        "mismatch_tickets": 0,
        "blockers": ["missing_tick_contract:2026-07-13"],
    }])

    assert outputs.report["health"]["market_replay"]["passed"] is False
    assert outputs.report["safe_for_strategy_simulation"] is False
    assert outputs.report["mode"] == "diagnostic_only"
    assert "market_replay" in outputs.report["hard_gate_blockers"]


def test_unknown_provider_records_feed_candidate_queue_and_semantic_gate():
    outputs = _build(provider_catalog=_formal_catalog(unknown=2))

    pattern = next(
        row for row in outputs.registry["patterns"]
        if row["pattern_id"].startswith("semantics.unknown_provider_record.")
    )
    assert pattern["occurrences"] == 2
    assert outputs.report["health"]["semantics"]["passed"] is False
    assert outputs.report["candidate_queue"][0]["pattern_id"] == pattern["pattern_id"]


def test_unclassified_messages_form_stable_semantic_families():
    catalog = _formal_catalog()
    catalog["signals"][0]["management_events"] = [
        {
            "message_id": 101,
            "text": "If M5 closes below 4325, wait for the next setup",
            "telegram_ts_utc": "2026-07-13T06:10:00+00:00",
            "semantic_source": "unclassified",
            "classified_action": None,
            "raw_versions": 1,
        },
        {
            "message_id": 102,
            "text": "If M5 closes below 4100, wait for the next setup",
            "telegram_ts_utc": "2026-07-14T06:10:00+00:00",
            "semantic_source": "unclassified",
            "classified_action": None,
            "raw_versions": 1,
        },
        {
            "message_id": 103,
            "text": "Join our VIP group for the strategy course",
            "telegram_ts_utc": "2026-07-14T07:10:00+00:00",
            "semantic_source": "unclassified",
            "classified_action": None,
            "raw_versions": 1,
        },
    ]

    outputs = _build(provider_catalog=catalog)
    by_id = {row["pattern_id"]: row for row in outputs.registry["patterns"]}

    conditional = by_id[
        "semantics.unclassified_management.conditional_plan"]
    announcement = by_id[
        "semantics.unclassified_management.non_trading_announcement"]
    assert conditional["occurrences"] == 2
    assert conditional["affected_day_count"] == 2
    assert announcement["occurrences"] == 1


def test_serialized_outputs_do_not_contain_a_volatile_generation_timestamp():
    outputs = _build()
    payload = json.loads(outputs.report_bytes)

    assert "generated_at" not in payload
    assert payload["corpus"]["event_rows"] == 0


def test_severity_tier_cannot_be_overridden_by_high_volume_low_risk_noise():
    low_risk_rows = [
        learning.PatternObservation(
            pattern_id="semantics.context_media_not_extracted",
            category="semantics",
            template="context media has no extracted semantics",
            severity="low",
            incident_key=f"media:{day}",
            ts_utc=f"2026-06-{day:02d}T08:00:00+00:00",
            signal=f"canal1_{day}",
            channel="canal1",
            event="provider_record",
            detail="media",
            raw_count=100,
        )
        for day in range(1, 29)
    ]
    critical = learning.PatternObservation(
        pattern_id="execution.invalid_stops.modify_sltp",
        category="execution",
        template="invalid stops while modifying sl/tp",
        severity="critical",
        incident_key="invalid-stop:1",
        ts_utc="2026-07-13T08:00:00+00:00",
        signal="canal2_1",
        channel="canal2",
        event="mt5_action_failed",
        detail="invalid stops",
    )

    patterns = learning._aggregate_patterns([*low_risk_rows, critical], {})
    by_id = {row["pattern_id"]: row for row in patterns}

    assert (
        by_id["execution.invalid_stops.modify_sltp"]["priority_score"]
        > by_id["semantics.context_media_not_extracted"]["priority_score"]
    )
    assert patterns[0]["pattern_id"] == "execution.invalid_stops.modify_sltp"
