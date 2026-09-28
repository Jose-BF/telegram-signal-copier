from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from research.captured_guard_replay import verify_captured_guard
from tools.collect_vm_week_material import build_segments
from tools.probe_vm_signal_lifecycle import digest, selected_management_fields
from tools.run_captured_guard_replay import (compare_native_ticket_universe, run_journal,
                                             run_segments, summarize_guard_risk)


def captured(kind, observed, *, armed=False):
    gold = kind == "gold_555_basket_guard"
    fingerprint = ("555124a24b534aa2abda53ddaaa2ee35fd3afd07e61d05937eb14c80ad0676f0"
                   if gold else "32cb5c0fe8205ad00a0c655bacd5446c6cc219d1ad7338967212c71781860631")
    common = {"sig": "canal2_1" if gold else "canal1_1", "session_id": "s",
              "code_commit": "historical", "decision_id": "d", "management_kind": kind,
              "management_contract": "management_decision_inputs_v1",
              "strategy_id": "gold_now_555_v1" if gold else "dubai_balanced_v1",
              "strategy_fingerprint": fingerprint, "direction": "BUY"}
    state = {"armed": armed, "triggered": False, "peak_pl": None,
             "trigger_reason": None, "recovery_pending": False}
    start = {**common, "ev": "bot_internal_decision_started", "ts": "2026-09-18T12:00:00+00:00",
             "decision_inputs": {"summary": {"positions_complete": True, "n_open": 1,
                                             "floating_pl": observed, "realized_pl": 0,
                                             "realized_complete": True, "total_pl": observed,
                                             "open_tickets": [101], "lots_total": 0.04},
                                 "now_utc": "2026-09-18T12:00:00+00:00"},
             "state_before": {"candidate_first_fill_at": "2026-09-18T11:50:00+00:00",
                              "basket_guard_armed": armed, "basket_guard_triggered": False,
                              "basket_guard_peak_pl": None, "basket_guard_trigger_reason": None,
                              "basket_guard_recovery_pending": False}}
    final = {**common, "ev": "bot_internal_decision", "ts": "2026-09-18T12:00:01+00:00",
             "decision_status": "completed", "decision_result": None, "state_after": {}}
    return start, final, state


def test_gold_guard_replays_result_and_guard_state_not_just_net():
    start, final, state = captured("gold_555_basket_guard", 5.0)
    state["peak_pl"] = 5.0
    final["decision_result"] = {"action": "none", "reason": None, "observed_pl": 5.0, "state": state}
    final["state_after"] = {"basket_guard_armed": False, "basket_guard_triggered": False,
                            "basket_guard_peak_pl": 5.0, "basket_guard_trigger_reason": None,
                            "basket_guard_recovery_pending": False}
    result = verify_captured_guard(start, final, expected_commit="historical")
    assert result["status"] == "matches_pure_guard"
    assert result["source_version_status"] == "declared_commit_matches"
    assert result["full_live_path_parity_verified"] is False
    changed = deepcopy(final)
    changed["state_after"]["basket_guard_peak_pl"] = 0.0
    assert verify_captured_guard(start, changed)["issues"] == ["guard_state_after_mismatch"]


def test_dubai_guard_stop_replays_and_rejects_same_money_wrong_action():
    start, final, state = captured("dubai_basket_guard", -26.0)
    state["triggered"] = True
    state["trigger_reason"] = "basket_stop"
    final["decision_result"] = {"action": "close", "reason": "basket_stop",
                                "observed_pl": -26.0, "state": state}
    final["state_after"] = {"basket_guard_armed": False, "basket_guard_triggered": True,
                            "basket_guard_peak_pl": None,
                            "basket_guard_trigger_reason": "basket_stop",
                            "basket_guard_recovery_pending": False}
    assert verify_captured_guard(start, final)["status"] == "matches_pure_guard"
    final["decision_result"]["action"] = "none"
    assert verify_captured_guard(start, final)["issues"] == ["decision_result_mismatch"]


def test_guard_replay_blocks_missing_clock_or_policy_identity():
    start, final, _ = captured("gold_555_basket_guard", 5.0)
    start["state_before"].pop("candidate_first_fill_at")
    result = verify_captured_guard(start, final)
    assert result["status"] == "blocked"
    assert "first_fill_clock_missing" in result["issues"]
    start["strategy_fingerprint"] = "wrong"
    assert "pair_lineage_mismatch" in verify_captured_guard(start, final)["issues"]
    start["strategy_fingerprint"] = final["strategy_fingerprint"]
    start["management_contract"] = "other"
    assert "pair_lineage_mismatch" in verify_captured_guard(start, final)["issues"]
    start["management_contract"] = final["management_contract"]
    start["state_before"].pop("basket_guard_armed")
    assert "guard_state_before_missing" in verify_captured_guard(start, final)["issues"]


def test_guard_runner_writes_immutable_diagnostic_with_source_hashes(tmp_path):
    start, final, state = captured("gold_555_basket_guard", 5.0)
    state["peak_pl"] = 5.0
    final["decision_result"] = {"action": "none", "reason": None, "observed_pl": 5.0, "state": state}
    final["state_after"] = {"basket_guard_armed": False, "basket_guard_triggered": False,
                            "basket_guard_peak_pl": 5.0, "basket_guard_trigger_reason": None,
                            "basket_guard_recovery_pending": False}
    journal, output = tmp_path / "journal.jsonl", tmp_path / "report.json"
    journal.write_text("".join(json.dumps(row) + "\n" for row in (start, final)), encoding="utf-8")
    report = run_journal(journal, output)
    assert report["statuses"] == {"matches_pure_guard": 1}
    assert len(report["source_sha256"]) == 6
    assert report["source_version_admitted"] is False
    assert json.loads(output.read_text(encoding="utf-8"))["guard_pair_count"] == 1
    with pytest.raises(FileExistsError):
        run_journal(journal, output)


def test_segment_guard_runner_requires_full_basket_coverage_and_pairs(tmp_path):
    def broker_ms(value):
        return int((datetime.fromisoformat(value) + timedelta(hours=3)).timestamp() * 1000)

    native_path = tmp_path / "native_deals.json"
    native_path.write_text(json.dumps({"currency": "EUR", "deals": [
        {"ticket": 11, "position_id": 101, "entry": 0,
         "time_msc": broker_ms("2026-09-18T11:50:00+00:00"), "volume": 0.04},
        {"ticket": 12, "position_id": 101, "entry": 1,
         "time_msc": broker_ms("2026-09-18T12:05:00+00:00"), "volume": 0.04}]}),
        encoding="utf-8")
    plan = {"contract": "week_native_lifecycle_window_plan_v1", "native_basket_count": 40,
            "rows": [{"signal_id": f"canal2_{index + 1}", "segments": [
                {"start_utc": "2026-09-18T12:00:00+00:00",
                 "end_utc": "2026-09-18T12:07:00+00:00"}]} for index in range(40)]}
    plan["rows"][0].update(clock_status="direct_for_all_native_days",
                           receipt_utc="2026-09-18T11:45:00+00:00",
                           native_deal_tickets=[11, 12], position_count=1, deal_count=2)
    plan["inputs_sha256"] = {str(native_path): digest(native_path)}
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    segments = build_segments(plan)
    start, final, state = captured("gold_555_basket_guard", 5.0)
    start["event_id"] = "started"
    final["event_id"] = "finished"
    state["peak_pl"] = 5.0
    final["decision_result"] = {"action": "none", "reason": None, "observed_pl": 5.0, "state": state}
    final["state_after"] = {"basket_guard_armed": False, "basket_guard_triggered": False,
                            "basket_guard_peak_pl": 5.0, "basket_guard_trigger_reason": None,
                            "basket_guard_recovery_pending": False}
    final["declared_action_count"] = 0
    final["declared_action_ids"] = []
    management = [selected_management_fields(row) for row in (start, final)]
    paths = []
    for index, segment in enumerate(segments):
        report = {"contract": "bounded_vm_week_material_segment_v2", "segment_index": index,
                  "segment_count": len(segments), "segment": segment,
                  "local_inputs_sha256": {str(plan_path): digest(plan_path), "worker": "same"},
                  "material_records_complete_within_segment": True,
                  "material_event_counts": [], "events": [],
                  "management_records_complete_within_segment": True,
                  "management_events": management if index == 0 else [],
                  "management_event_counts": ([
                      {"event": "bot_internal_decision_started", "count": 1},
                      {"event": "bot_internal_decision", "count": 1}]
                      if index == 0 else [])}
        path = tmp_path / f"segment_{index}.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        paths.append(path)
    output = tmp_path / "segment_guard_report.json"
    with pytest.raises(ValueError, match="incomplete native basket"):
        run_segments(plan_path, "canal2_1", paths[:1], output)
    result = run_segments(plan_path, "canal2_1", paths, output,
                          native_deals_path=native_path)
    assert result["management_capture"]["status"] == "paired"
    assert result["statuses"] == {"matches_pure_guard": 1}
    assert result["observed_guard_risk"]["known_sample_metrics"]["max_drawdown_eur"] == "0"
    assert result["native_ticket_diagnostic"]["statuses"] == {
        "matches_at_guard_eval_not_read_time": 1}
    assert result["native_ticket_diagnostic"]["native_position_universe_verified"] is False
    assert result["source_version_admitted"] is False
    for path in paths:
        segment_report = json.loads(path.read_text(encoding="utf-8"))
        segment_report.update(contract="bounded_vm_week_material_segment_v3",
                              indexed_prefix_time_coverage_verified=True,
                              full_source_time_coverage_verified=False,
                              index_sha256="same-index")
        path.write_text(json.dumps(segment_report), encoding="utf-8")
    indexed = run_segments(plan_path, "canal2_1", paths,
                           tmp_path / "indexed_guard_report.json", native_deals_path=native_path)
    assert indexed["input_mode"] == "bounded_v3_indexed_prefix_segments"
    assert indexed["source_version_admitted"] is False
    native_path.write_text(native_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="not bound"):
        run_segments(plan_path, "canal2_1", paths, tmp_path / "changed_source.json",
                     native_deals_path=native_path)


def test_observed_guard_risk_keeps_missing_samples_and_sampled_drawdown():
    first, _, _ = captured("gold_555_basket_guard", 10.0)
    first["event_id"] = "one"
    first["decision_inputs"]["summary"]["source_tick_time_msc"] = 1789650000000
    second, _, _ = captured("gold_555_basket_guard", -20.0)
    second.update(event_id="two", decision_id="two", ts="2026-09-18T12:01:00+00:00")
    second["decision_inputs"]["now_utc"] = "2026-09-18T12:01:00+00:00"
    missing = deepcopy(second)
    missing.update(event_id="three", decision_id="three", ts="2026-09-18T12:02:00+00:00")
    missing["decision_inputs"]["now_utc"] = "2026-09-18T12:02:00+00:00"
    missing["decision_inputs"]["summary"]["total_pl"] = None
    result = summarize_guard_risk([first, second, missing])
    assert result["evaluation_count"] == 3
    assert result["known_sample_count"] == 2
    assert result["blocked_sample_count"] == 1
    assert result["known_sample_metrics"]["minimum_from_origin_eur"] == "-20.00"
    assert result["known_sample_metrics"]["max_drawdown_eur"] == "30.00"
    assert result["all_guard_samples_metrics"] is None
    assert result["native_position_universe_verified"] is False
    assert result["max_observed_open_volume"] == "0.04"
    assert result["rows"][0]["guard_evaluated_at_utc"] == "2026-09-18T12:00:00+00:00"
    assert result["rows"][0]["source_tick_time_msc"] == 1789650000000
    assert "observed_at_utc" not in result["rows"][0]
    assert result["observation_clock_semantics"] == "mt5_read_completed_before_guard_evaluation_exact_read_time_unknown"
    assert summarize_guard_risk([])["status"] == "no_guard_evaluations"


def test_observed_guard_risk_blocks_duplicate_decision_identity():
    first, _, _ = captured("gold_555_basket_guard", 10.0)
    second = deepcopy(first)
    second["event_id"] = "another-capture"
    result = summarize_guard_risk([first, second])
    assert result["evaluation_count"] == 2
    assert result["known_sample_count"] == 0
    assert result["blocked_sample_count"] == 2
    assert result["blockers"] == {"guard_decision_identity_duplicated_or_missing": 2}
    assert result["known_sample_metrics"] is None


def test_guard_capture_clock_respects_journal_millisecond_truncation_only():
    start, _, _ = captured("gold_555_basket_guard", 10.0)
    start["ts"] = "2026-09-18T12:00:00.000+00:00"
    start["decision_inputs"]["now_utc"] = "2026-09-18T12:00:00.000999+00:00"
    accepted = summarize_guard_risk([start])
    assert accepted["known_sample_count"] == 1
    start["decision_inputs"]["now_utc"] = "2026-09-18T12:00:00.001000+00:00"
    rejected = summarize_guard_risk([start])
    assert rejected["blockers"] == {"observation_after_capture_start": 1}
    start["ts"] = "2026-09-18T12:00:00.000001+00:00"
    start["decision_inputs"]["now_utc"] = "2026-09-18T12:00:00.000002+00:00"
    precise = summarize_guard_risk([start])
    assert precise["blockers"] == {"observation_after_capture_start": 1}


def test_native_ticket_diagnostic_retains_differences_without_certifying_read_time():
    def broker_ms(value):
        return int((datetime.fromisoformat(value) + timedelta(hours=3)).timestamp() * 1000)

    first, _, _ = captured("gold_555_basket_guard", 10.0)
    second = deepcopy(first)
    second.update(event_id="second", decision_id="second", ts="2026-09-18T12:06:00+00:00")
    second["decision_inputs"]["now_utc"] = second["ts"]
    second["decision_inputs"]["summary"].update(
        n_open=0, open_tickets=[], lots_total=0, floating_pl=0, realized_pl=10, total_pl=10)
    risk = summarize_guard_risk([first, second])
    basket = {"signal_id": "canal2_1", "clock_status": "direct_for_all_native_days",
              "receipt_utc": "2026-09-18T11:50:00+00:00", "native_deal_tickets": [11, 12],
              "position_count": 1, "deal_count": 2}
    native = {"currency": "EUR", "deals": [
        {"ticket": 11, "position_id": 101, "entry": 0, "time_msc": broker_ms("2026-09-18T11:55:00+00:00"), "volume": 0.04},
        {"ticket": 12, "position_id": 101, "entry": 1, "time_msc": broker_ms("2026-09-18T12:05:00+00:00"), "volume": 0.04}]}
    result = compare_native_ticket_universe(risk["rows"], basket, native)
    assert [row["status"] for row in result["rows"]] == ["matches_at_guard_eval_not_read_time"] * 2
    assert result["native_position_universe_verified"] is False
    assert result["rows"][0]["native_open_tickets_at_guard_eval"] == [101]
    assert result["rows"][1]["native_open_tickets_at_guard_eval"] == []
    changed = deepcopy(risk["rows"])
    changed[0]["open_tickets"] = [999]
    assert compare_native_ticket_universe(changed, basket, native)["rows"][0]["status"] == (
        "differs_at_guard_eval_read_time_unknown")
    changed[0]["guard_evaluated_at_utc"] = "2026-09-18T12:05:00+00:00"
    assert compare_native_ticket_universe(changed, basket, native)["rows"][0]["status"] == (
        "blocked_native_deal_at_guard_time")
    basket["clock_status"] = "offset_hypothesis"
    assert compare_native_ticket_universe(risk["rows"], basket, native)["rows"][0]["status"] == (
        "blocked_clock_not_direct")
    incomplete = deepcopy(native)
    incomplete["deals"][1]["volume"] = 0.03
    with pytest.raises(ValueError, match="closures incomplete"):
        compare_native_ticket_universe(risk["rows"], basket, incomplete)


def test_native_ticket_interval_blocks_deal_during_positions_read():
    def broker_ms(value):
        return int((datetime.fromisoformat(value) + timedelta(hours=3)).timestamp() * 1000)

    start, _, _ = captured("gold_555_basket_guard", 10.0)
    start["decision_inputs"]["summary"].update(
        positions_read_started_utc="2026-09-18T11:59:59.500+00:00",
        positions_read_completed_utc="2026-09-18T11:59:59.700+00:00",
        positions_read_elapsed_ms=200.0)
    basket = {"signal_id": "canal2_1", "clock_status": "direct_for_all_native_days",
              "receipt_utc": "2026-09-18T11:50:00+00:00", "native_deal_tickets": [11, 12],
              "position_count": 1, "deal_count": 2}
    native = {"currency": "EUR", "deals": [
        {"ticket": 11, "position_id": 101, "entry": 0,
         "time_msc": broker_ms("2026-09-18T11:55:00+00:00"), "volume": 0.04},
        {"ticket": 12, "position_id": 101, "entry": 1,
         "time_msc": broker_ms("2026-09-18T12:05:00+00:00"), "volume": 0.04}]}
    risk = summarize_guard_risk([start])
    assert risk["rows"][0]["positions_read_started_utc"] == "2026-09-18T11:59:59.500000+00:00"
    result = compare_native_ticket_universe(risk["rows"], basket, native)
    assert result["rows"][0]["status"] == "matches_at_bounded_read_interval"
    assert result["rows"][0]["native_open_tickets_at_read"] == [101]
    native["deals"][1]["time_msc"] = broker_ms("2026-09-18T11:59:59.600+00:00")
    blocked = compare_native_ticket_universe(risk["rows"], basket, native)
    assert blocked["rows"][0]["status"] == "blocked_native_deal_during_position_read"
    invalid = deepcopy(start)
    invalid["decision_inputs"]["summary"]["positions_read_elapsed_ms"] = 5000.0
    rejected = summarize_guard_risk([invalid])
    assert rejected["rows"][0]["status"] == "blocked"
    assert rejected["blockers"] == {"positions_read_wall_monotonic_disagree": 1}
