from datetime import datetime, timedelta, timezone

from tools.audit_shadow_live_protection import (bind_live_legs, compare_exit_targets,
                                                compare_level_snapshots,
                                                validate_shadow_transition_coverage)
import pytest


BASE = datetime(2026, 9, 18, 12, tzinfo=timezone.utc)
BASE_MS = int(BASE.timestamp() * 1000)


def event(kind, at_ms, **fields):
    return {"ev": kind, "ts": (BASE + timedelta(milliseconds=at_ms)).isoformat(),
            "sig": "canal2_1", "action_id": "modify", "attempt_id": "attempt",
            "decision_id": "decision", "session_id": "session", "ticket": 101, **fields}


def native_position():
    return {"signal_id": "canal2_1", "position_id": 101, "direction": "BUY",
            "entry_msc": BASE_MS + 10_800_000, "exit_msc": BASE_MS + 10_800_000 + 10_000,
            "entry_price": 4381.79, "volume": 0.04}


def test_live_leg_binding_requires_fill_result_and_native_deal_identity():
    position = native_position()
    result = event("mt5_order_result", 100, action_id="entry", attempt_id="entry_attempt",
                   deal=201, order=101, price=4381.79, volume=0.04, retcode=10009)
    fill = event("gold_555_first_leg_filled", 110, action_id=None, attempt_id=None,
                 fill_price=4381.79, volume=0.04, strategy_id="gold_now_555_v1",
                 strategy_fingerprint="f")
    deal = {"ticket": 201, "position_id": 101, "entry": 0, "price": 4381.79,
            "volume": 0.04, "time_msc": position["entry_msc"]}
    bound = bind_live_legs([result, fill], [position], [deal],
                           strategy_id="gold_now_555_v1", fingerprint="f")
    assert bound["status"] == "verified_live_leg_journal_binding"
    assert bound["leg_to_ticket"] == {0: 101}
    deal["price"] = 4381.80
    assert bind_live_legs([result, fill], [position], [deal],
                          strategy_id="gold_now_555_v1", fingerprint="f")["status"] == "blocked_native_entry"
    deal["price"] = 4381.79
    result["ts"] = result["ts"].replace("+00:00", "")
    assert bind_live_legs([result, fill], [position], [deal],
                          strategy_id="gold_now_555_v1", fingerprint="f")["status"] == "blocked_native_entry"


def test_snapshot_comparison_uses_emitted_shadow_state_and_keeps_missing_existence_flag():
    position = native_position()
    rows = [
        event("mt5_modify_requested", 1000, new_sl=4351.79),
        event("mt5_action_attempt", 2000, operation="MODIFY_SLTP",
              broker_request_sent=True, result_retcode=10009, request_sl=4351.79,
              request_tp=4382.29,
              broker_request_started_utc=(BASE + timedelta(milliseconds=1500)).isoformat(),
              broker_response_received_utc=(BASE + timedelta(milliseconds=2000)).isoformat()),
        event("mt5_modify_confirmed", 2100, retcode=10009, new_sl=4351.79),
        event("mt5_position_snapshot", 2200, retcode=10009, sl=4351.79, tp=4382.29,
              volume=0.04),
    ]
    shadow = [{"ev": "strategy_shadow_transition", "ts": (BASE + timedelta(milliseconds=1800)).isoformat(),
               "transition_tick_msc": BASE_MS + 1700,
               "state": {"positions": [{"leg_index": 0, "status": "open", "stop_price": 4351.35,
                                         "target_price": 4381.85}]}}]
    result = compare_level_snapshots(rows, shadow, [position], {0: 101})
    assert result["snapshot_count"] == 1
    assert result["statuses"] == {"compared_level_without_existence_flag": 1}
    assert result["rows"][0]["virtual_minus_observed_sl"] == "-0.44"
    assert result["rows"][0]["virtual_minus_observed_tp"] == "-0.44"
    assert result["rows"][0]["broker_install_time_verified"] is False
    rows[3]["position_exists"] = True
    assert compare_level_snapshots(rows, shadow, [position], {0: 101})["statuses"] == {
        "compared_level": 1}
    rows[3]["position_exists"] = False
    assert compare_level_snapshots(rows, shadow, [position], {0: 101})["statuses"] == {
        "blocked_position_absent": 1}
    del rows[3]["position_exists"]
    rows[2]["new_sl"] = 4351.80
    assert compare_level_snapshots(rows, shadow, [position], {0: 101})["statuses"] == {
        "blocked_snapshot_lineage": 1}
    rows[2]["new_sl"] = 4351.79
    rows[3]["volume"] = 0.05
    assert compare_level_snapshots(rows, shadow, [position], {0: 101})["statuses"] == {
        "blocked_snapshot_lineage": 1}
    rows[3]["volume"] = 0.04
    shadow[0]["ts"] = (BASE + timedelta(milliseconds=2300)).isoformat()
    assert compare_level_snapshots(rows, shadow, [position], {0: 101})["statuses"] == {
        "blocked_no_prior_emitted_shadow_state": 1}
    shadow[0]["ts"] = (BASE + timedelta(milliseconds=1800)).isoformat()
    position["exit_msc"] = BASE_MS + 10_800_000 + 2150
    assert compare_level_snapshots(rows, shadow, [position], {0: 101})["statuses"] == {
        "blocked_native_exit_before_snapshot_log": 1}
    position["exit_msc"] = BASE_MS + 10_800_000 + 10_000
    missing_confirm = [row for row in rows if row["ev"] != "mt5_modify_confirmed"]
    assert compare_level_snapshots(missing_confirm, shadow, [position], {0: 101})["statuses"] == {
        "blocked_snapshot_lineage": 1}


def test_exit_target_alignment_requires_prior_level_and_matching_closed_leg():
    position = native_position()
    position["exit_price"] = 101.1
    position["entry_price"] = 100.6
    position["exit_msc"] = BASE_MS + 10_800_000 + 5_000
    deal = {"position_id": 101, "entry": 1, "price": 101.1,
            "volume": 0.04, "time_msc": position["exit_msc"], "reason": 5}
    fill = {"transition": "virtual_fill", "ts": (BASE + timedelta(milliseconds=1200)).isoformat(),
            "transition_tick_msc": BASE_MS + 1000,
            "transition_details": {"leg_index": 0, "entry_price": 100.0, "target_price": 100.5}}
    close = {"transition": "virtual_position_closed", "event_id": "close",
             "ts": (BASE + timedelta(milliseconds=3600)).isoformat(),
             "transition_tick_msc": BASE_MS + 3500,
             "transition_details": {"leg_indexes": [0]},
             "state": {"positions": [{"leg_index": 0, "status": "closed",
                                      "close_price": 100.5, "closed_tick_msc": BASE_MS + 3500}]}}
    bound = [{"leg_index": 0, "native_position_id": 101, "entry_price": "100.6"}]
    snapshot = {"ticket": 101, "event_id": "snapshot", "observed_tp": "101.1",
                "status": "compared_level_without_existence_flag",
                "snapshot_logged_at": (BASE + timedelta(milliseconds=2000)).isoformat(),
                "broker_response_received_utc": (BASE + timedelta(milliseconds=1900)).isoformat()}
    rejected = {"ev": "mt5_action_attempt", "operation": "MODIFY_SLTP", "ticket": 101,
                "broker_request_sent": True, "result_retcode": 10016, "request_tp": 101.1,
                "broker_response_received_utc": (BASE + timedelta(milliseconds=1800)).isoformat()}
    result = compare_exit_targets([position], [deal], [fill, close], bound,
                                  [snapshot], [rejected])
    assert result["statuses"] == {"broker_tp_exit_matches_fill_relative_target": 1}
    assert result["rows"][0]["target_shift_minus_entry_shift"] == "0.0"
    assert result["rows"][0]["native_exit_minus_virtual_close_tick_ms"] == 1500
    assert result["rows"][0]["broker_tp_install_verified"] is False
    assert result["rows"][0]["tp_rejected_attempts_before_confirmation"] == 1
    assert result["rows"][0]["virtual_close_tick_before_accepted_tp_response"] is False
    assert result["rows"][0]["virtual_close_emitted_before_accepted_tp_response"] is False
    snapshot["observed_tp"] = "101.2"
    assert compare_exit_targets([position], [deal], [fill, close], bound, [snapshot], [])["statuses"] == {
        "observed_target_or_exit_mechanism_diverges": 1}
    snapshot["observed_tp"] = "101.1"
    deal["reason"] = 3
    assert compare_exit_targets([position], [deal], [fill, close], bound, [snapshot], [])["statuses"] == {
        "observed_target_or_exit_mechanism_diverges": 1}
    deal["reason"] = 5
    snapshot["snapshot_logged_at"] = (BASE + timedelta(milliseconds=5100)).isoformat()
    assert compare_exit_targets([position], [deal], [fill, close], bound, [snapshot], [])["statuses"] == {
        "blocked_no_pre_exit_tp_snapshot": 1}
    snapshot["snapshot_logged_at"] = (BASE + timedelta(milliseconds=2000)).isoformat()
    close["state"]["positions"][0]["closed_tick_msc"] += 1
    assert compare_exit_targets([position], [deal], [fill, close], bound, [snapshot], [])["statuses"] == {
        "blocked_exit_lineage": 1}


def test_exit_chronology_does_not_confuse_old_tick_with_late_virtual_close():
    position = native_position()
    position.update(entry_price=100.6, exit_price=101.1,
                    exit_msc=BASE_MS + 10_800_000 + 5_000)
    deal = {"position_id": 101, "entry": 1, "price": 101.1, "volume": 0.04,
            "time_msc": position["exit_msc"], "reason": 5}
    fill = {"transition": "virtual_fill", "ts": (BASE + timedelta(milliseconds=1100)).isoformat(),
            "transition_tick_msc": BASE_MS + 1000,
            "transition_details": {"leg_index": 0, "entry_price": 100.0, "target_price": 100.5}}
    close = {"transition": "virtual_position_closed", "event_id": "close",
             "ts": (BASE + timedelta(milliseconds=2100)).isoformat(),
             "transition_tick_msc": BASE_MS + 1500,
             "transition_details": {"leg_indexes": [0]},
             "state": {"positions": [{"leg_index": 0, "status": "closed",
                                       "close_price": 100.5, "closed_tick_msc": BASE_MS + 1500}]}}
    snapshot = {"ticket": 101, "event_id": "snapshot", "observed_tp": "101.1",
                "status": "compared_level_without_existence_flag",
                "snapshot_logged_at": (BASE + timedelta(milliseconds=2000)).isoformat(),
                "broker_response_received_utc": (BASE + timedelta(milliseconds=1900)).isoformat()}
    result = compare_exit_targets([position], [deal], [fill, close],
                                  [{"leg_index": 0, "native_position_id": 101, "entry_price": "100.6"}],
                                  [snapshot], [])
    row = result["rows"][0]
    assert row["virtual_close_tick_before_accepted_tp_response"] is True
    assert row["virtual_close_emitted_before_accepted_tp_response"] is False
    assert row["shadow_close_emit_minus_tick_ms"] == 600
    assert row["tp_response_minus_shadow_close_emit_ms"] == -200


def test_capped_probe_shadow_events_require_complete_hashed_slice_and_matching_ids():
    source = [{"ev": "strategy_shadow_transition", "sig": "canal2_1",
               "event_id": f"event_{index}", "strategy_fingerprint": "control",
               "ts": (BASE + timedelta(milliseconds=index)).isoformat()}
              for index in range(101)]
    probe = {"event_counts": {"strategy_shadow_transition": 101},
             "sampled_kinds": {"strategy_shadow_transition": 101},
             "events": source[:100]}
    coverage = validate_shadow_transition_coverage(
        probe, source, source, signal="canal2_1", fingerprint="control",
        window_start=BASE_MS, window_end=BASE_MS + 200)
    assert coverage["probe_shadow_transition_sampled"] is True
    assert coverage["hashed_shadow_slice_count"] == 101
    with pytest.raises(ValueError, match="coverage differs"):
        validate_shadow_transition_coverage(probe, source[:100], source[:100],
                                            signal="canal2_1", fingerprint="control",
                                            window_start=BASE_MS, window_end=BASE_MS + 200)
    probe["events"][-1] = {**probe["events"][-1], "event_id": "unknown"}
    with pytest.raises(ValueError, match="coverage differs"):
        validate_shadow_transition_coverage(probe, source, source,
                                            signal="canal2_1", fingerprint="control",
                                            window_start=BASE_MS, window_end=BASE_MS + 200)
