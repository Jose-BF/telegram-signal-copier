"""Re-evaluate captured guard decisions without replaying broker execution."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta

import basket_management
import dubai_live_candidate as dubai
import gold_555_live_candidate as gold


GUARD_STATE_FIELDS = {
    "basket_guard_armed": "armed", "basket_guard_triggered": "triggered",
    "basket_guard_peak_pl": "peak_pl", "basket_guard_trigger_reason": "trigger_reason",
    "basket_guard_recovery_pending": "recovery_pending",
}


def _utc(value):
    if not isinstance(value, str):
        raise ValueError("explicit UTC clock required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("explicit UTC clock required")
    return parsed


def verify_captured_guard(start, final, *, expected_commit=None):
    issues = []
    identity = ("sig", "session_id", "code_commit", "decision_id", "message_revision_id",
                "parent_decision_id", "management_contract", "management_kind",
                "strategy_id", "strategy_fingerprint", "direction")
    if (start.get("ev") != "bot_internal_decision_started"
            or final.get("ev") != "bot_internal_decision"
            or any(start.get(key) != final.get(key) for key in identity)
            or start.get("management_contract") != "management_decision_inputs_v1"
            or not all(start.get(key) for key in ("sig", "decision_id", "management_kind",
                                                   "strategy_id", "strategy_fingerprint"))):
        issues.append("pair_lineage_mismatch")
    actual_commit = start.get("code_commit")
    source_version_status = ("declared_commit_matches" if expected_commit and actual_commit == expected_commit
                             else "declared_commit_mismatch" if expected_commit and actual_commit
                             else "missing_commit" if expected_commit else "not_admitted")
    kind = start.get("management_kind")
    if kind == "gold_555_basket_guard":
        policy, state_type, evaluate = gold.Gold555Policy(), gold.Gold555GuardState, gold.evaluate_guard
        expected_id, fingerprint = gold.CANDIDATE_ID, gold.CANDIDATE_FINGERPRINT
    elif kind == "dubai_basket_guard":
        policy, state_type, evaluate = dubai.DubaiLivePolicy(), dubai.DubaiGuardState, dubai.evaluate_guard
        expected_id, fingerprint = dubai.CANDIDATE_ID, policy.fingerprint
    else:
        issues.append("unsupported_guard_kind")
        policy = state_type = evaluate = expected_id = fingerprint = None
    if policy is not None and (start.get("strategy_id") != expected_id
                               or start.get("strategy_fingerprint") != fingerprint):
        issues.append("strategy_contract_mismatch")
    if final.get("decision_status") != "completed":
        issues.append("decision_not_completed")
    if issues:
        return {"signal_id": start.get("sig"), "decision_id": start.get("decision_id"),
                "status": "blocked", "issues": sorted(set(issues)),
                "source_version_status": source_version_status,
                "full_live_path_parity_verified": False}
    inputs, before = start.get("decision_inputs"), start.get("state_before")
    if not isinstance(inputs, dict) or not isinstance(before, dict):
        issues.append("captured_guard_inputs_missing")
    else:
        if any(source not in before for source in GUARD_STATE_FIELDS):
            issues.append("guard_state_before_missing")
        first = before.get("candidate_first_fill_at") or before.get("timestamp")
        if not first:
            issues.append("first_fill_clock_missing")
        else:
            try:
                now = _utc(inputs["now_utc"])
                elapsed_min = max(0.0, (now - _utc(first)).total_seconds() / 60.0)
            except (KeyError, TypeError, ValueError):
                issues.append("guard_clock_invalid")
        try:
            observation = basket_management.guard_observation(inputs["summary"])
        except (KeyError, TypeError, ValueError, RuntimeError, ArithmeticError):
            issues.append("guard_observation_unavailable")
        if not issues:
            try:
                state = state_type(**{field: before.get(source) for source, field in GUARD_STATE_FIELDS.items()})
                decision = evaluate(policy=policy, state=state, total_pl=observation.observed_pl,
                                    n_open=observation.n_open, elapsed_min=elapsed_min,
                                    money_evidence_complete=observation.realized_complete)
            except (TypeError, ValueError, RuntimeError, ArithmeticError):
                issues.append("guard_evaluation_unavailable")
    if issues:
        return {"signal_id": start.get("sig"), "decision_id": start.get("decision_id"),
                "status": "blocked", "issues": sorted(set(issues)),
                "source_version_status": source_version_status,
                "full_live_path_parity_verified": False}
    expected_result = asdict(decision)
    if final.get("decision_result") != expected_result:
        issues.append("decision_result_mismatch")
    after = final.get("state_after")
    if (not isinstance(after, dict) or
            any(source not in after or after[source] != getattr(decision.state, field)
                for source, field in GUARD_STATE_FIELDS.items())):
        issues.append("guard_state_after_mismatch")
    return {"signal_id": start.get("sig"), "decision_id": start.get("decision_id"),
            "status": "matches_pure_guard" if not issues else "mismatch",
            "issues": issues, "source_version_status": source_version_status,
            "observed_action": (final.get("decision_result") or {}).get("action")
            if isinstance(final.get("decision_result"), dict) else None,
            "replayed_action": decision.action,
            "full_live_path_parity_verified": False}
