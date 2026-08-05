import json

import log_analysis


def _write_jsonl(path, rows):
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_scan_uses_cursor_and_reads_only_appended_events(tmp_path):
    path = tmp_path / "events.jsonl"
    _write_jsonl(path, [
        {"ts": "2026-07-20T08:00:00+00:00", "sig": "bot",
         "ev": "session_started"},
        {"ts": "2026-07-20T08:01:00+00:00", "sig": "canal2_1",
         "ev": "signal_received", "channel": "canal2"},
    ])

    first = log_analysis.scan_jsonl(path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "ts": "2026-07-20T08:05:00+00:00",
            "sig": "canal2_1",
            "ev": "signal_closed",
            "total_pnl_usd": 4.25,
        }) + "\n")
    second = log_analysis.scan_jsonl(path, cursor=first.cursor)

    assert first.mode == "full"
    assert len(first.events) == 2
    assert second.mode == "incremental"
    assert [event["ev"] for event in second.events] == ["signal_closed"]
    assert second.start_offset == first.end_offset


def test_scan_falls_back_to_full_when_log_was_rewritten(tmp_path):
    path = tmp_path / "events.jsonl"
    _write_jsonl(path, [
        {"ts": "2026-07-20T08:00:00+00:00", "sig": "bot",
         "ev": "heartbeat"},
    ])
    first = log_analysis.scan_jsonl(path)
    _write_jsonl(path, [
        {"ts": "2026-07-21T08:00:00+00:00", "sig": "bot",
         "ev": "session_started"},
    ])

    second = log_analysis.scan_jsonl(path, cursor=first.cursor)

    assert second.mode == "full_rebuild"
    assert second.reset_reason == "prefix_changed"
    assert second.events[0]["ev"] == "session_started"


def test_scan_does_not_consume_incomplete_trailing_json(tmp_path):
    path = tmp_path / "events.jsonl"
    complete = json.dumps({
        "ts": "2026-07-20T08:00:00+00:00", "sig": "bot",
        "ev": "heartbeat",
    }) + "\n"
    path.write_bytes(complete.encode("utf-8") + b'{"ts": "unfinished')

    scan = log_analysis.scan_jsonl(path)

    assert len(scan.events) == 1
    assert scan.end_offset == len(complete.encode("utf-8"))
    assert scan.incomplete_tail is True


def test_summary_is_compact_and_deduplicates_repeated_anomalies():
    events = [
        {"ts": "2026-07-20T08:00:00+00:00", "sig": "canal2_3470",
         "ev": "signal_received", "channel": "canal2"},
        {"ts": "2026-07-20T08:00:02+00:00", "sig": "canal2_3470",
         "ev": "handler_entry", "channel": "canal2", "kind": "new",
         "telegram_to_handler_ms": 2500},
        {"ts": "2026-07-20T08:00:03+00:00", "sig": "canal2_3470",
         "ev": "handler_entry", "channel": "canal2", "kind": "poll_new",
         "telegram_to_handler_ms": 2700},
        {"ts": "2026-07-20T08:05:00+00:00", "sig": "canal2_3470",
         "ev": "telegram_understood",
         "classifications": [{"action": "CLOSE_PARTIAL"},
                             {"action": "MOVE_SL_TO_BE"}]},
        {"ts": "2026-07-20T08:06:00+00:00", "sig": "canal2_3470",
         "ev": "mt5_action_failed", "last_retcode": 10016},
        {"ts": "2026-07-20T08:06:01+00:00", "sig": "canal2_3470",
         "ev": "anomaly", "category": "sl_be", "severity": "critical",
         "detail": "BE failed", "ticket": 123},
        {"ts": "2026-07-20T08:06:02+00:00", "sig": "canal2_3470",
         "ev": "anomaly", "category": "sl_be", "severity": "critical",
         "detail": "BE failed", "ticket": 123},
        {"ts": "2026-07-20T08:07:00+00:00", "sig": "canal2_3473",
         "ev": "management_reply_unresolved", "reply_to_msg_id": 3470,
         "text_preview": "BE hit"},
        {"ts": "2026-07-20T08:07:01+00:00", "sig": "canal2_3473",
         "ev": "management_reply_unresolved", "reply_to_msg_id": 3470,
         "text_preview": "BE hit"},
        {"ts": "2026-07-20T08:30:00+00:00", "sig": "canal2_3470",
         "ev": "signal_closed", "total_pl": -8.38},
    ]

    report = log_analysis.summarize_events(events)

    assert report["operations"]["signals_received"] == 1
    assert report["operations"]["signals_closed"] == 1
    assert report["operations"]["recorded_pnl"] == -8.38
    assert report["execution"]["mt5_action_failed"] == 1
    assert report["interpretation"]["close_partial_evidence"] == 1
    assert report["interpretation"]["unresolved_management"] == 1
    assert report["interpretation"]["unresolved_management_events"] == 2
    assert report["anomalies"]["critical"] == 2
    assert report["anomalies"]["unique"] == 1
    assert report["anomalies"]["top"][0]["count"] == 2
    assert report["latency_ms"]["canal2"]["count"] == 1
    assert report["latency_ms"]["canal2"]["p50"] == 2500.0
    report["scan"] = {"mode": "incremental"}
    report["status_snapshots"] = {}
    rendered = log_analysis.render_compact_report(report)
    assert "Latencia" in rendered
    assert "Canal 2 p95=2500ms" in rendered


def test_summary_recovers_raw_partial_evidence_without_double_counting():
    text = (
        "+35 pips from best entry. I am closing partial profits "
        "and making my trade risk free."
    )
    events = [
        {"ts": "2026-07-20T08:05:15+00:00", "sig": "canal2_3472",
         "ev": "telegram_raw", "channel": "canal2", "message_id": 3472,
         "text": text, "text_sha1": "partial-message"},
        {"ts": "2026-07-20T08:05:22+00:00", "sig": "canal2_3470",
         "ev": "telegram_understood", "channel": "canal2",
         "message_id": 3470, "raw_text_sha1": "partial-message",
         "classifications": [{"action": "CLOSE_PARTIAL"}]},
        {"ts": "2026-07-20T08:05:23+00:00", "sig": "canal2_3470",
         "ev": "telegram_understood", "channel": "canal2",
         "message_id": 3470, "raw_text_sha1": "partial-message",
         "classifications": [{"action": "CLOSE_PARTIAL"}]},
    ]

    report = log_analysis.summarize_events(events)

    assert report["interpretation"]["close_partial_evidence"] == 1


def test_summary_ignores_legacy_handler_entries_without_delay():
    report = log_analysis.summarize_events([{
        "ts": "2026-06-05T08:00:00+00:00",
        "sig": "canal1_20000",
        "ev": "handler_entry",
        "channel": "canal1",
    }])

    assert report["latency_ms"] == {}


def test_incremental_summary_counts_zone_transitions_and_triggers():
    events = [
        {"ev": "canal2_zone_plan_created", "status": "armed"},
        {"ev": "canal2_zone_plan_waiting_for_trigger"},
        {"ev": "canal2_zone_plan_alias_registered"},
        {
            "ev": "canal2_zone_plan_transition",
            "status": "approaching",
            "lifecycle_actions": ["APPROACHING"],
        },
        {"ev": "canal2_zone_entry_attempted"},
        {
            "ev": "canal2_zone_entry_confirmed",
            "trigger": {"trigger": "first_touch"},
        },
        {"ev": "canal2_zone_entry_attempted"},
        {
            "ev": "canal2_zone_entry_confirmed",
            "last_trigger": {"trigger": "explicit_active"},
        },
        {"ev": "canal2_zone_entry_failed", "reason": "broker_tick_unavailable"},
        {
            "ev": "canal2_zone_plan_management",
            "actionable": True,
            "zone_plan_status": "draft",
        },
    ]

    summary = log_analysis.summarize_events(events)["zone_lifecycle"]

    assert summary["plans_created"] == 1
    assert summary["armed_waiting"] == 1
    assert summary["plans_updated"] == 0
    assert summary["aliases_registered"] == 1
    assert summary["transitions"] == 1
    assert summary["transitions_by_action"] == {"APPROACHING": 1}
    assert summary["trigger_attempts"] == 2
    assert summary["confirmed_entries"] == 2
    assert summary["entries_by_trigger"] == {
        "activation": 1,
        "first_touch": 1,
    }
    assert summary["entry_failures"] == 1
    assert summary["failures_by_reason"] == {"broker_tick_unavailable": 1}
    assert summary["unresolved_messages"] == 1
