from __future__ import annotations

from parity_incident_registry import reconcile_parity_incidents


def _signal(*, blockers=(), candidate_blockers=()):
    return {
        "channel": "canal2",
        "signal_id": "canal2_2320",
        "actual": {
            "entry_count": 1,
            "exit_reason": "tp",
            "net_eur": 1.72,
            "complete": True,
            "mt5_reconciled": True,
            "control_mirror_match": not blockers,
            "control_parity": {
                "match": not blockers,
                "differences": list(blockers),
            },
            "source_commit": "a" * 40,
        },
        "candidates": {
            "gold_now_555_v1": {
                "candidate_id": "gold_now_555_v1",
                "role": "live_control",
                "source_commit": "a" * 40,
                "entry_count": 1,
                "exit_reason": "tp",
                "status": "closed",
                "net_eur": 1.72,
                "complete": not candidate_blockers,
                "blockers": list(candidate_blockers),
            }
        },
        "blockers": list(blockers) + list(candidate_blockers),
    }


def test_mismatch_opens_actionable_stable_incident() -> None:
    first = reconcile_parity_incidents(
        signal_rows=(_signal(blockers=("control_mirror_mismatch",)),),
        global_blockers=("control_mirror_mismatch",),
        observation_id="2026-09-03",
    )
    repeated = reconcile_parity_incidents(
        signal_rows=(_signal(blockers=("control_mirror_mismatch",)),),
        global_blockers=("control_mirror_mismatch",),
        previous=first,
        observation_id="2026-09-03",
    )

    assert first["open_count"] == 1
    incident = first["incidents"][0]
    assert incident["status"] == "open"
    assert incident["channel"] == "canal2"
    assert incident["signal_id"] == "canal2_2320"
    assert incident["candidate_id"] is None
    assert incident["blocker"] == "control_mirror_mismatch"
    assert incident["category"] == "strategy_parity"
    assert incident["required_action"] == (
        "compare_live_and_shadow_logic_signatures_then_fix_shared_contract_or_adapter"
    )
    assert incident["verification_required"] == (
        "replay_same_signal_without_this_blocker"
    )
    assert repeated["incidents"][0]["incident_id"] == incident["incident_id"]
    assert repeated["incidents"][0]["occurrences"] == 1


def test_same_signal_exact_replay_resolves_but_keeps_history() -> None:
    opened = reconcile_parity_incidents(
        signal_rows=(_signal(blockers=("control_mirror_mismatch",)),),
        global_blockers=("control_mirror_mismatch",),
        observation_id="before_fix",
    )

    resolved = reconcile_parity_incidents(
        signal_rows=(_signal(),),
        global_blockers=(),
        previous=opened,
        observation_id="after_fix",
    )

    assert resolved["open_count"] == 0
    assert resolved["resolved_count"] == 1
    incident = resolved["incidents"][0]
    assert incident["status"] == "resolved"
    assert incident["resolved_observation_id"] == "after_fix"
    assert incident["resolution_evidence"] == (
        "same_signal_replayed_without_blocker"
    )


def test_missing_signal_never_silently_resolves_incident() -> None:
    opened = reconcile_parity_incidents(
        signal_rows=(_signal(candidate_blockers=("tick_gap",)),),
        global_blockers=("tick_gap",),
        observation_id="before",
    )

    not_rechecked = reconcile_parity_incidents(
        signal_rows=(),
        global_blockers=(),
        previous=opened,
        observation_id="different_window",
    )

    assert not_rechecked["open_count"] == 1
    incident = not_rechecked["incidents"][0]
    assert incident["status"] == "open"
    assert incident["verification_state"] == "not_rechecked"


def test_new_blocker_cannot_disguise_unverified_outcome_as_resolved() -> None:
    mismatched = _signal(blockers=("control_outcome_mismatch",))
    mismatched["control_outcome_parity"] = {"status": "mismatch"}
    opened = reconcile_parity_incidents(
        signal_rows=(mismatched,),
        global_blockers=("control_outcome_mismatch",),
        observation_id="before",
    )
    unavailable = _signal(
        candidate_blockers=("candidate_source_code_unverified",),
    )
    unavailable["control_outcome_parity"] = {"status": "unverified"}

    rechecked = reconcile_parity_incidents(
        signal_rows=(unavailable,),
        global_blockers=("candidate_source_code_unverified",),
        previous=opened,
        observation_id="engine_changed",
    )

    original = next(
        row for row in rechecked["incidents"]
        if row["blocker"] == "control_outcome_mismatch"
    )
    assert original["status"] == "open"
    assert original["verification_state"] == "recheck_blocked"
    assert original["resolved_observation_id"] is None


def test_resolved_incident_reopens_as_regression() -> None:
    opened = reconcile_parity_incidents(
        signal_rows=(_signal(blockers=("control_mirror_mismatch",)),),
        global_blockers=("control_mirror_mismatch",),
        observation_id="first",
    )
    resolved = reconcile_parity_incidents(
        signal_rows=(_signal(),),
        global_blockers=(),
        previous=opened,
        observation_id="fixed",
    )

    regressed = reconcile_parity_incidents(
        signal_rows=(_signal(blockers=("control_mirror_mismatch",)),),
        global_blockers=("control_mirror_mismatch",),
        previous=resolved,
        observation_id="later",
    )

    incident = regressed["incidents"][0]
    assert regressed["open_count"] == 1
    assert incident["status"] == "regressed"
    assert incident["regression_count"] == 1
    assert incident["resolved_observation_id"] is None


def test_transient_open_state_is_not_recorded_as_defect() -> None:
    report = reconcile_parity_incidents(
        signal_rows=(
            _signal(candidate_blockers=(
                "candidate_not_terminal",
                "incomplete_candidate_result",
            )),
        ),
        global_blockers=(
            "candidate_not_terminal",
            "incomplete_candidate_result",
            "minimum_sample_not_reached",
        ),
        observation_id="open_market",
    )

    assert report["open_count"] == 0
    assert report["incidents"] == []


def test_repair_mismatch_resolves_only_when_same_repair_becomes_exact() -> None:
    broken = _signal(blockers=("control_repair_outcome_mismatch",))
    broken["control_repair_outcome"] = {
        "status": "mismatch",
        "evidence_role": "retrospective_same_signal_repair",
        "actual_entry_count": 1,
        "shadow_entry_count": 2,
        "entry_count_delta": 1,
        "actual_net_eur": 1.72,
        "shadow_net_eur": 4.30,
        "net_eur_delta": 2.58,
    }
    opened = reconcile_parity_incidents(
        signal_rows=(broken,),
        global_blockers=("control_repair_outcome_mismatch",),
        observation_id="before_repair",
    )

    fixed = _signal()
    fixed["control_repair_outcome"] = {
        "status": "exact",
        "evidence_role": "retrospective_same_signal_repair",
        "actual_entry_count": 1,
        "shadow_entry_count": 1,
        "entry_count_delta": 0,
        "actual_net_eur": 1.72,
        "shadow_net_eur": 1.72,
        "net_eur_delta": 0.0,
    }
    resolved = reconcile_parity_incidents(
        signal_rows=(fixed,),
        global_blockers=(),
        previous=opened,
        observation_id="after_repair",
    )

    incident = next(
        row for row in resolved["incidents"]
        if row["blocker"] == "control_repair_outcome_mismatch"
    )
    assert incident["status"] == "resolved"
    assert incident["verification_state"] == "exact_repair_replay"
    assert incident["resolution_evidence"] == (
        "same_signal_repaired_with_current_engine"
    )
