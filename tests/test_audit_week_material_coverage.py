import pytest
from datetime import datetime

from tools.audit_week_material_coverage import (AUDITOR_SOURCES, audit, classify_exits,
                                                pair_management_evaluations,
                                                summarize_modification_actions)
from tools.collect_vm_week_material import build_segments
from tools.plan_vm_week_lifecycle import OFFSET_MS
from tools.probe_vm_signal_lifecycle import selected_fields, selected_management_fields


def sources():
    plan = {"contract": "week_native_lifecycle_window_plan_v1", "native_basket_count": 40,
            "rows": [{"signal_id": f"canal2_{index}", "channel": "canal2",
                      "clock_status": "direct_for_all_native_days", "position_count": 1, "deal_count": 1,
                      "native_deal_tickets": [index + 1], "segments": [{
                          "start_utc": "2026-09-18T12:00:00+00:00",
                          "end_utc": "2026-09-18T12:07:00+00:00"}]}
                     for index in range(40)]}
    native = {"currency": "EUR", "deals": [{"ticket": index + 1, "entry": 0, "order": index + 101,
                         "type": 0, "price": 100.0, "volume": 0.01,
                         "time_msc": 1789743660000} for index in range(40)]}
    segments = build_segments(plan)
    reports = [{"contract": "bounded_vm_week_material_segment_v1", "segment_index": index,
                "segment_count": 2, "segment": segment,
                "local_inputs_sha256": {"plan": "same", "worker": "same"},
                "material_records_complete_within_segment": True,
                "material_event_counts": [], "events": []}
               for index, segment in enumerate(segments)]
    lineage = {"sig": "canal2_0", "action_id": "a1", "attempt_id": "t1",
               "decision_id": "d1", "session_id": "s1"}
    reports[0]["events"] = [
        {**lineage, "ev": "mt5_order_requested", "ts": "2026-09-18T12:00:30+00:00",
         "event_id": "event-request", "lot": 0.01, "direction": "BUY"},
        {**lineage, "ev": "mt5_action_attempt", "ts": "2026-09-18T12:01:05+00:00",
         "event_id": "event-attempt", "operation": "OPEN_MARKET", "broker_request_sent": True,
         "result_retcode": 10009, "broker_request_started_utc": "2026-09-18T12:00:40+00:00",
         "broker_response_received_utc": "2026-09-18T12:01:05+00:00"},
        {**lineage, "ev": "mt5_order_result", "ts": "2026-09-18T12:01:06+00:00",
         "event_id": "event-result", "retcode": 10009, "deal": 1, "order": 101,
         "price": 100.0, "volume": 0.01, "direction": "BUY"},
    ]
    reports[0]["material_event_counts"] = [
        {"signal_id": "canal2_0", "event": event, "count": 1}
        for event in ("mt5_order_requested", "mt5_action_attempt", "mt5_order_result")]
    return plan, native, reports


def test_auditor_provenance_includes_all_imported_tool_sources():
    assert {path.name for path in AUDITOR_SOURCES} == {
        "audit_week_material_coverage.py", "collect_vm_week_material.py",
        "probe_vm_signal_lifecycle.py", "plan_vm_week_lifecycle.py",
        "audit_native_money_anchor.py"}
    assert all(path.is_file() for path in AUDITOR_SOURCES)


def test_partial_coverage_keeps_all_40_baskets_and_missing_segments():
    plan, native, reports = sources()
    result = audit(plan, native, reports[:1])
    assert result["native_basket_count"] == 40
    assert result["planned_segment_count"] == 2
    assert result["collected_segment_count"] == 1
    assert result["fully_covered_baskets"] == 0
    assert result["opening_bound_baskets"] == 0
    assert result["rows"][0]["opening_binding"] == "not_evaluated_incomplete_material_segments"
    assert result["rows"][0]["missing_segment_indices"] == [1]
    assert result["rows"][0]["modification_evidence"]["status"] == "incomplete_segments"
    assert result["full_simulator_path_parity_verified"] is False


def test_complete_segments_still_flag_unbound_native_deals():
    plan, native, reports = sources()
    result = audit(plan, native, reports)
    assert result["fully_covered_baskets"] == 40
    assert result["full_cohort_coverage_verified"] is False
    assert result["full_source_time_coverage_verified"] is False
    assert result["opening_bound_baskets"] == 1
    assert result["rows"][0]["opening_binding"] == "native_entries_bound_to_request_attempt_result"
    assert result["rows"][0]["entry_facts"][0]["clock_order"] == "inside_call"
    assert result["rows"][1]["opening_binding"] == "opening_result_native_deal_mismatch"
    with pytest.raises(ValueError, match="duplicate or invalid"):
        audit(plan, native, [reports[0], reports[0]])
    reports[1]["local_inputs_sha256"]["worker"] = "different"
    with pytest.raises(ValueError, match="mixed material collector versions"):
        audit(plan, native, reports)
    reports[1]["local_inputs_sha256"]["worker"] = "same"
    reports[1].update(contract="bounded_vm_week_material_segment_v2",
                      management_records_complete_within_segment=True,
                      management_event_counts=[], management_events=[])
    with pytest.raises(ValueError, match="mixed material collector versions"):
        audit(plan, native, reports)


def test_v3_indexed_prefix_is_accepted_but_not_full_source_coverage():
    plan, native, reports = sources()
    for report in reports:
        report.update(contract="bounded_vm_week_material_segment_v3",
                      management_records_complete_within_segment=True,
                      management_event_counts=[], management_events=[],
                      indexed_prefix_time_coverage_verified=True,
                      full_source_time_coverage_verified=False,
                      index_sha256="same-index")
    result = audit(plan, native, reports)
    assert result["fully_covered_baskets"] == 40
    assert result["full_source_time_coverage_verified"] is False
    assert result["full_cohort_coverage_verified"] is False
    reports[0]["indexed_prefix_time_coverage_verified"] = False
    with pytest.raises(ValueError, match="indexed prefix"):
        audit(plan, native, reports)
    reports[0]["indexed_prefix_time_coverage_verified"] = True
    reports[1]["contract"] = "bounded_vm_week_material_segment_v2"
    with pytest.raises(ValueError, match="mixed material collector versions"):
        audit(plan, native, reports)


def test_exit_classification_never_claims_exact_expert_fill_without_deal_identity():
    native_exits = [{"ticket": 10, "position_id": 1, "reason": 4},
                    {"ticket": 11, "position_id": 2, "reason": 3}]
    events = [
        {"ev": "mt5_close_requested", "ticket": 2, "action_id": "a"},
        {"ev": "mt5_action_attempt", "operation": "CLOSE_POSITION", "ticket": 2,
         "action_id": "a", "attempt_id": "t", "broker_request_sent": True, "result_retcode": 10009},
        {"ev": "mt5_close_result", "ticket": 2, "action_id": "a", "attempt_id": "t", "retcode": 10009},
    ]
    result = classify_exits(events, native_exits)
    assert result[0]["status"] == "native_sl"
    assert result[1]["status"] == "expert_close_candidate_without_deal_identity"
    assert all(row["exact_native_close_binding_verified"] is False for row in result)


def test_native_sl_uses_observed_post_modify_position_without_claiming_install_time():
    exit_msc = int(datetime.fromisoformat("2026-09-18T12:02:00+00:00").timestamp() * 1000) + OFFSET_MS
    deal = {"ticket": 10, "position_id": 1, "reason": 4, "time_msc": exit_msc, "price": 99.98}
    events = [
        {"ev": "mt5_modify_requested", "ticket": 1, "action_id": "a", "new_sl": 100,
         "ts": "2026-09-18T12:00:00+00:00"},
        {"ev": "mt5_action_attempt", "ticket": 1, "action_id": "a", "attempt_id": "t",
         "operation": "MODIFY_SLTP", "broker_request_sent": True, "result_retcode": 10009,
         "request_sl": 100, "broker_request_started_utc": "2026-09-18T12:00:01+00:00",
         "broker_response_received_utc": "2026-09-18T12:00:02+00:00"},
        {"ev": "mt5_modify_confirmed", "ticket": 1, "action_id": "a", "attempt_id": "t",
         "retcode": 10009, "ts": "2026-09-18T12:00:03+00:00"},
        {"ev": "mt5_position_snapshot", "ticket": 1, "action_id": "a", "attempt_id": "t",
         "ts": "2026-09-18T12:01:00+00:00", "after_action": "MODIFY_SLTP",
         "position_exists": True, "retcode": 10009, "sl": 100},
    ]
    row = classify_exits(events, [deal])[0]
    assert row["status"] == "native_sl"
    assert row["protection_observation"]["status"] == "observed_after_accepted_modify"
    assert row["protection_observation"]["chain_verified"] is True
    assert row["protection_observation"]["server_install_time_known"] is False
    assert row["exact_native_close_binding_verified"] is False
    events[0]["new_sl"] = None
    malformed = classify_exits(events, [deal])[0]["protection_observation"]
    assert malformed["status"] == "protection_level_evidence_invalid"
    assert malformed["chain_verified"] is False
    events[0]["new_sl"] = 100
    events[2]["ts"] = "2026-09-18T12:00:00+00:00"
    out_of_order = classify_exits(events, [deal])[0]["protection_observation"]
    assert out_of_order["status"] == "snapshot_lineage_unverified"
    events[2]["ts"] = "2026-09-18T12:00:03+00:00"
    hypothetical = classify_exits(events, [deal], "offset_hypothesis")[0]["protection_observation"]
    assert hypothetical["status"] == "observed_after_accepted_modify_clock_hypothesis"


def test_modify_outcomes_separate_requested_rejected_and_observed_levels():
    common = {"ticket": 101, "decision_id": "d", "session_id": "s"}
    events = [
        {**common, "ev": "mt5_modify_requested", "action_id": "rejected",
         "new_sl": 99.0, "ts": "2026-09-18T12:00:00+00:00"},
        {**common, "ev": "mt5_action_attempt", "action_id": "rejected", "attempt_id": "r1",
         "operation": "MODIFY_SLTP", "broker_request_sent": True, "result_retcode": 10016,
         "ts": "2026-09-18T12:00:01+00:00"},
        {**common, "ev": "mt5_modify_requested", "action_id": "accepted",
         "new_sl": 98.0, "ts": "2026-09-18T12:00:02+00:00"},
        {**common, "ev": "mt5_action_attempt", "action_id": "accepted", "attempt_id": "a1",
         "operation": "MODIFY_SLTP", "broker_request_sent": True, "result_retcode": 10009,
         "request_sl": 98.0, "broker_request_started_utc": "2026-09-18T12:00:03+00:00",
         "broker_response_received_utc": "2026-09-18T12:00:04+00:00"},
        {**common, "ev": "mt5_modify_confirmed", "action_id": "accepted", "attempt_id": "a1",
         "retcode": 10009, "ts": "2026-09-18T12:00:05+00:00"},
        {**common, "ev": "mt5_position_snapshot", "action_id": "accepted", "attempt_id": "a1",
         "after_action": "MODIFY_SLTP", "position_exists": True, "retcode": 10009,
         "sl": 98.0, "ts": "2026-09-18T12:00:06+00:00"},
        {**common, "ev": "mt5_modify_requested", "action_id": "queued",
         "new_sl": 97.0, "ts": "2026-09-18T12:00:07+00:00"},
    ]
    result = summarize_modification_actions(events, complete=True)
    assert result["requested_action_count"] == 3
    assert result["attempt_count"] == 2
    assert result["statuses"] == {"observed_after_accepted_modify": 1,
                                  "rejected_without_accepted_modify": 1,
                                  "requested_without_direct_attempt": 1}
    assert {row["action_id"]: row["status"] for row in result["actions"]} == {
        "rejected": "rejected_without_accepted_modify",
        "accepted": "observed_after_accepted_modify",
        "queued": "requested_without_direct_attempt"}
    assert result["actions"][0]["server_install_time_known"] is False
    assert result["full_source_time_coverage_verified"] is False
    events[5]["sl"] = 97.5
    wrong = summarize_modification_actions(events, complete=True)
    assert wrong["statuses"]["accepted_without_matching_snapshot"] == 1
    events[5]["sl"] = 98.0
    events[4]["decision_id"] = "other"
    wrong_lineage = summarize_modification_actions(events, complete=True)
    assert wrong_lineage["statuses"]["accepted_without_matching_snapshot"] == 1
    events.append({**common, "ev": "mt5_action_coalesced", "action_id": "queued"})
    coalesced = summarize_modification_actions(events, complete=True)
    assert coalesced["statuses"]["coalesced_without_direct_attempt"] == 1


def test_modify_outcomes_keep_orphan_attempts_and_partial_capture_blocked():
    events = [{"ev": "mt5_action_attempt", "action_id": "missing", "ticket": 101,
               "operation": "MODIFY_SLTP", "broker_request_sent": True,
               "result_retcode": 10009}]
    assert summarize_modification_actions(events, complete=True)["orphan_attempt_count"] == 1
    assert summarize_modification_actions(events, complete=False)["status"] == "incomplete_segments"


def test_expert_close_deal_identity_requires_full_attempt_facts_and_stays_provisional():
    exit_msc = int(datetime.fromisoformat("2026-09-18T12:00:02+00:00").timestamp() * 1000) + OFFSET_MS
    deal = {"ticket": 900, "order": 800, "position_id": 700, "reason": 3,
            "type": 1, "volume": 0.02, "price": 4369.25, "time_msc": exit_msc}
    lineage = {"action_id": "a", "decision_id": "d", "session_id": "s"}
    events = [
        {"ev": "mt5_close_requested", "ticket": 700, "ts": "2026-09-18T12:00:00+00:00", **lineage},
        selected_fields({"ev": "mt5_action_attempt", "ticket": 700, "operation": "CLOSE_POSITION",
         "attempt_id": "t", "broker_request_sent": True, "result_retcode": 10009,
         "result": {"retcode": 10009, "deal": 900, "order": 800,
                    "price": 4369.25, "volume": 0.02},
         "request": {"action": 1, "position": 700, "type": 1, "volume": 0.02},
         "broker_request_started_utc": "2026-09-18T12:00:01+00:00",
         "broker_response_received_utc": "2026-09-18T12:00:03+00:00",
         "ts": "2026-09-18T12:00:04+00:00", **lineage}),
        {"ev": "mt5_close_result", "ticket": 700, "attempt_id": "t", "retcode": 10009,
         "ts": "2026-09-18T12:00:05+00:00", **lineage},
    ]
    row = classify_exits(events, [deal])[0]
    assert row["status"] == "expert_close_deal_identity_matched_order_history_missing"
    assert row["native_deal_identity_matched"] is True
    assert row["exact_native_close_binding_verified"] is False
    events[1]["result_price"] = 4369.2
    mismatch = classify_exits(events, [deal])[0]
    assert mismatch["status"] == "expert_close_deal_identity_mismatch"
    assert mismatch["close_identity_issues"] == ["close_price_or_volume_mismatch"]
    events[1]["result_price"] = 4369.25
    events[1]["broker_request_started_utc"] = "2026-09-18T12:00:03+00:00"
    assert "close_clock_order_mismatch" in classify_exits(events, [deal])[0]["close_identity_issues"]
    events[1]["broker_request_started_utc"] = "2026-09-18T12:00:01+00:00"
    hypothetical = classify_exits(events, [deal], "offset_hypothesis")[0]
    assert hypothetical["status"] == "expert_close_deal_identity_matched_clock_hypothesis"
    events[1]["result_deal"] = 901
    wrong_deal = classify_exits(events, [deal])[0]
    assert wrong_deal["status"] == "expert_close_deal_identity_mismatch"
    assert wrong_deal["close_identity_issues"] == ["successful_attempt_claims_other_deal"]


def test_management_pairing_checks_no_action_decision_across_segment_boundary():
    common = {"sig": "canal2_3086", "session_id": "s", "code_commit": "c",
              "decision_id": "d", "management_contract": "management_decision_inputs_v1",
              "management_kind": "gold_555_trailing", "strategy_id": "gold_now_555_v1",
              "strategy_fingerprint": "f", "direction": "BUY"}
    start = {**common, "ev": "bot_internal_decision_started", "event_id": "start",
             "ts": "2026-09-18T12:04:59+00:00"}
    final = {**common, "ev": "bot_internal_decision", "event_id": "final",
             "ts": "2026-09-18T12:05:00+00:00", "decision_status": "completed",
             "declared_action_count": 0, "declared_action_ids": []}
    assert pair_management_evaluations([start, final])["status"] == "paired"
    assert pair_management_evaluations([start])["issues"] == ["management_start_or_completion_missing"]
    assert pair_management_evaluations([])["status"] == "no_evaluations_observed"
    final["strategy_fingerprint"] = "different"
    assert "management_pair_lineage_mismatch" in pair_management_evaluations([start, final])["issues"]


def test_v2_week_report_counts_paired_management_without_claiming_replay_parity():
    plan, native, reports = sources()
    for report in reports:
        report.update(contract="bounded_vm_week_material_segment_v2",
                      management_records_complete_within_segment=True,
                      management_event_counts=[], management_events=[])
    common = {"sig": "canal2_0", "session_id": "s", "code_commit": "c",
              "decision_id": "d", "event_id": "mgmt-start",
              "management_contract": "management_decision_inputs_v1",
              "management_kind": "gold_555_trailing", "strategy_id": "gold_now_555_v1",
              "strategy_fingerprint": "f", "direction": "BUY"}
    start = selected_management_fields({**common, "ev": "bot_internal_decision_started",
        "ts": "2026-09-18T12:00:10+00:00", "decision_inputs": {
            "bid": 100, "ask": 100.1, "tick_time_msc": 1, "open_tickets": [101]},
        "state_before": {"status": "active"}})
    final = selected_management_fields({**common, "ev": "bot_internal_decision",
        "event_id": "mgmt-final", "ts": "2026-09-18T12:00:11+00:00",
        "decision_status": "completed", "declared_action_count": 0,
        "declared_action_ids": [], "decision_result": 0,
        "state_after": {"status": "active"}})
    reports[0]["management_events"] = [start, final]
    reports[0]["management_event_counts"] = [
        {"signal_id": "canal2_0", "event": "bot_internal_decision", "count": 1},
        {"signal_id": "canal2_0", "event": "bot_internal_decision_started", "count": 1}]
    result = audit(plan, native, reports)
    assert result["management_paired_baskets"] == 1
    assert result["rows"][0]["management_capture"]["status"] == "paired"
    assert result["rows"][0]["modification_evidence"]["status"] == "diagnostic_only"
    assert result["rows"][1]["management_capture"]["status"] == "no_evaluations_observed"
    assert result["full_simulator_path_parity_verified"] is False


def test_risk_reference_is_diagnostic_and_rejects_ledger_conflict():
    plan, native, reports = sources()
    native["deals"][0]["profit"] = -7.8100000000000005
    risk = {"contract": "native_week_full_tick_risk_diagnostic_v1", "basket_count": 40,
            "clock_admitted": False,
            "baskets": [{"signal_id": f"canal2_{index}", "native_net_eur": "0",
                         "direct_clock_anchor_for_all_event_days": True,
                         "path": {"sample_count": 2, "known_sample_metrics": {"known_samples": 2},
                                  "fx_coverage_mode": "retrospective_bracketed", "blockers": [],
                                  "metrics": {"max_drawdown": "1.20", "minimum_from_origin": "-1.20",
                                              "max_gross_volume": "0.01"}, "sample_stream_sha256": "hash"}}
                        for index in range(40)]}
    risk["baskets"][0]["native_net_eur"] = "-7.81"
    report = audit(plan, native, reports, risk=risk)
    assert report["native_risk_reference_attached"] is True
    assert report["native_risk_global_clock_admitted"] is False
    assert report["rows"][0]["native_risk_reference"]["max_drawdown_eur"] == "1.20"
    assert report["rows"][0]["native_risk_reference"]["net_eur"] == "-7.81"
    assert report["full_simulator_path_parity_verified"] is False
    risk["baskets"][0]["native_net_eur"] = "1"
    with pytest.raises(ValueError, match="contradicts ledger"):
        audit(plan, native, reports, risk=risk)
