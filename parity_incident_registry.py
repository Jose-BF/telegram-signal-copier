"""Persistent, deterministic repair loop for shadow parity defects."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import hashlib
import json
from typing import Any


_TRANSIENT_BLOCKERS = {
    "candidate_not_terminal",
    "incomplete_candidate_result",
    "minimum_sample_not_reached",
    "no_eligible_signals",
    "open_parity_incidents",
    "open_comparison_incidents",
}

_RANKING_ONLY_BLOCKERS = {
    "actual_evidence_missing",
    "control_mirror_unverified",
    "control_outcome_unverified",
    "control_repair_outcome_unverified",
    "source_commit_mismatch",
    "source_commit_unverified",
}

_IGNORE_WHEN_ONLY_GLOBAL = {
    # The report already scopes this to a channel. With no signals in that
    # channel there is no basket that can be replayed to resolve an incident.
    "live_control_changed",
}


def reconcile_parity_incidents(
    *,
    signal_rows: Iterable[Mapping[str, Any]],
    global_blockers: Iterable[object],
    previous: Mapping[str, Any] | None = None,
    observation_id: str | None = None,
) -> dict[str, Any]:
    """Open, retain, resolve, or reopen parity defects without losing history."""

    signals = [dict(row) for row in signal_rows]
    blockers = sorted({str(value) for value in global_blockers if str(value)})
    observation = str(observation_id or "").strip() or _canonical_hash({
        "signals": signals,
        "global_blockers": blockers,
    })
    previous_items = _previous_items(previous)
    findings = _current_findings(signals, blockers)
    signals_by_key = {
        (str(row.get("channel") or ""), str(row.get("signal_id") or "")): row
        for row in signals
        if str(row.get("channel") or "") and str(row.get("signal_id") or "")
    }

    incidents: list[dict[str, Any]] = []
    for incident_id in sorted(set(previous_items) | set(findings)):
        prior = previous_items.get(incident_id)
        finding = findings.get(incident_id)
        if finding is not None:
            incidents.append(_observe_failure(
                incident_id=incident_id,
                finding=finding,
                prior=prior,
                observation_id=observation,
            ))
            continue
        if prior is None:
            continue
        incidents.append(_observe_absence(
            prior=prior,
            signals_by_key=signals_by_key,
            global_blockers=set(blockers),
            observation_id=observation,
        ))

    incidents.sort(key=lambda row: (
        row["status"] == "resolved",
        str(row.get("channel") or ""),
        str(row.get("signal_id") or ""),
        str(row.get("blocker") or ""),
        str(row.get("incident_id") or ""),
    ))
    open_items = [row for row in incidents if row["status"] != "resolved"]
    comparison_open = [
        row for row in open_items if row.get("blocks_comparison") is True
    ]
    return {
        "schema_version": 1,
        "observation_id": observation,
        "open_count": len(open_items),
        "comparison_blocking_open_count": len(comparison_open),
        "regressed_count": sum(
            row["status"] == "regressed" for row in incidents
        ),
        "resolved_count": sum(
            row["status"] == "resolved" for row in incidents
        ),
        "ranking_blocked": bool(open_items),
        "comparison_blocked": bool(comparison_open),
        "unresolved_incident_ids": [
            row["incident_id"] for row in open_items
        ],
        "incidents": incidents,
    }


def _previous_items(
    previous: Mapping[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    if previous is None:
        return {}
    if previous.get("schema_version") != 1:
        raise ValueError("unsupported parity incident registry schema")
    raw = previous.get("incidents")
    if not isinstance(raw, list):
        raise ValueError("invalid parity incident registry")
    result: dict[str, dict[str, Any]] = {}
    for value in raw:
        if not isinstance(value, Mapping):
            raise ValueError("invalid parity incident row")
        incident = dict(value)
        incident_id = str(incident.get("incident_id") or "")
        if not incident_id or incident_id in result:
            raise ValueError("invalid or duplicate parity incident identity")
        if incident.get("status") not in {"open", "resolved", "regressed"}:
            raise ValueError("invalid parity incident status")
        result[incident_id] = incident
    return result


def _current_findings(
    signals: list[dict[str, Any]],
    global_blockers: list[str],
) -> dict[str, dict[str, Any]]:
    findings: dict[str, dict[str, Any]] = {}
    represented: set[str] = set()
    for signal in signals:
        channel = str(signal.get("channel") or "")
        signal_id = str(signal.get("signal_id") or "")
        if not channel or not signal_id:
            continue
        signal_blockers = sorted({
            str(value)
            for value in signal.get("blockers") or ()
            if str(value) and str(value) not in _TRANSIENT_BLOCKERS
        })
        for blocker in signal_blockers:
            represented.add(blocker)
            finding = _finding(
                scope="signal",
                channel=channel,
                signal_id=signal_id,
                blocker=blocker,
                evidence=_signal_evidence(signal, blocker),
            )
            findings[finding["incident_id"]] = finding

    for blocker in global_blockers:
        if (
            blocker in represented
            or blocker in _TRANSIENT_BLOCKERS
            or blocker in _IGNORE_WHEN_ONLY_GLOBAL
        ):
            continue
        finding = _finding(
            scope="report",
            channel=None,
            signal_id=None,
            blocker=blocker,
            evidence={"global_blocker": blocker},
        )
        findings[finding["incident_id"]] = finding
    return findings


def _signal_evidence(
    signal: Mapping[str, Any],
    blocker: str,
) -> dict[str, Any]:
    actual = signal.get("actual")
    actual_evidence: dict[str, Any] | None = None
    if isinstance(actual, Mapping):
        actual_evidence = {
            key: actual.get(key)
            for key in (
                "entry_count",
                "exit_reason",
                "net_eur",
                "complete",
                "mt5_reconciled",
                "telegram_lineage_complete",
                "control_mirror_match",
                "control_parity",
                "control_outcome_parity",
                "logic_signature_blockers",
                "source_commit",
            )
        }
    affected_candidates: list[dict[str, Any]] = []
    candidates = signal.get("candidates")
    if isinstance(candidates, Mapping):
        for candidate_id, raw in sorted(candidates.items()):
            if not isinstance(raw, Mapping) or blocker not in {
                str(value) for value in raw.get("blockers") or ()
            }:
                continue
            affected_candidates.append({
                "candidate_id": str(candidate_id),
                "role": raw.get("role"),
                "source_commit": raw.get("source_commit"),
                "status": raw.get("status"),
                "complete": raw.get("complete"),
                "entry_count": raw.get("entry_count"),
                "exit_reason": raw.get("exit_reason"),
                "observed_net_eur": raw.get("observed_net_eur"),
            })
    return {
        "blocker": blocker,
        "actual": actual_evidence,
        "control_outcome_parity": signal.get("control_outcome_parity"),
        "control_repair_outcome": signal.get("control_repair_outcome"),
        "affected_candidates": affected_candidates,
    }


def _finding(
    *,
    scope: str,
    channel: str | None,
    signal_id: str | None,
    blocker: str,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    category, action = _classification(blocker)
    identity = {
        "scope": scope,
        "channel": channel,
        "signal_id": signal_id,
        "blocker": blocker,
    }
    return {
        "incident_id": "parity_" + _canonical_hash(identity)[:20],
        **identity,
        "candidate_id": None,
        "category": category,
        "required_action": action,
        "diagnosis": _diagnosis(blocker, evidence),
        "verification_required": "replay_same_signal_without_this_blocker",
        "blocks_comparison": blocker not in _RANKING_ONLY_BLOCKERS,
        "evidence": dict(evidence),
        "evidence_digest": _canonical_hash(evidence),
    }


def _diagnosis(
    blocker: str,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    outcome_key = {
        "control_outcome_mismatch": "control_outcome_parity",
        "control_repair_outcome_mismatch": "control_repair_outcome",
    }.get(blocker)
    if outcome_key is None:
        return {
            "repair_focus": "collect_or_repair_required_evidence",
            "entry_count_delta": None,
            "net_eur_delta": None,
        }
    outcome = evidence.get(outcome_key)
    if not isinstance(outcome, Mapping):
        return {
            "repair_focus": "outcome_evidence",
            "entry_count_delta": None,
            "net_eur_delta": None,
        }
    entry_delta = outcome.get("entry_count_delta")
    money_delta = outcome.get("net_eur_delta")
    try:
        entry_changed = int(entry_delta) != 0
    except (TypeError, ValueError):
        entry_changed = False
    return {
        "repair_focus": (
            "entry_lifecycle" if entry_changed else "money_or_exit_execution"
        ),
        "entry_count_delta": entry_delta,
        "net_eur_delta": money_delta,
        "checks": (
            [
                "compare_expected_and_accepted_leg_indexes",
                "trace_entry_window_and_terminal_lifecycle",
                "verify_rejection_retry_and_restart_paths",
            ]
            if entry_changed
            else [
                "compare_open_and_close_prices_to_mt5_deals",
                "verify_target_stop_spread_commission_and_swap",
                "calibrate_only_from_observed_execution_evidence",
            ]
        ),
    }


def _observe_failure(
    *,
    incident_id: str,
    finding: Mapping[str, Any],
    prior: Mapping[str, Any] | None,
    observation_id: str,
) -> dict[str, Any]:
    digest = str(finding["evidence_digest"])
    if prior is None:
        evidence_digests = [digest]
        status = "open"
        regression_count = 0
        first_observation = observation_id
    else:
        evidence_digests = [
            str(value) for value in prior.get("evidence_digests") or ()
            if str(value)
        ]
        if digest not in evidence_digests:
            evidence_digests.append(digest)
        was_resolved = prior.get("status") == "resolved"
        status = "regressed" if was_resolved else str(prior.get("status"))
        regression_count = int(prior.get("regression_count") or 0) + int(
            was_resolved
        )
        first_observation = str(
            prior.get("first_observation_id") or observation_id
        )
    return {
        **dict(finding),
        "incident_id": incident_id,
        "status": status,
        "verification_state": "failing",
        "first_observation_id": first_observation,
        "last_observation_id": observation_id,
        "resolved_observation_id": None,
        "resolution_evidence": None,
        "occurrences": len(evidence_digests),
        "regression_count": regression_count,
        "evidence_digests": evidence_digests,
    }


def _observe_absence(
    *,
    prior: Mapping[str, Any],
    signals_by_key: Mapping[tuple[str, str], Mapping[str, Any]],
    global_blockers: set[str],
    observation_id: str,
) -> dict[str, Any]:
    incident = dict(prior)
    scope = str(incident.get("scope") or "")
    if scope == "signal":
        signal_key = (
            str(incident.get("channel") or ""),
            str(incident.get("signal_id") or ""),
        )
        current_signal = signals_by_key.get(signal_key)
        rechecked = current_signal is not None
    else:
        current_signal = None
        rechecked = str(incident.get("blocker") or "") not in global_blockers
    if not rechecked:
        if incident.get("status") != "resolved":
            incident["verification_state"] = "not_rechecked"
        return incident
    if incident.get("status") == "resolved":
        return incident
    verification = (
        None
        if current_signal is None
        else _signal_resolution_mode(incident, current_signal)
    )
    if current_signal is not None and verification is None:
        incident.update({
            "verification_state": "recheck_blocked",
            "last_observation_id": observation_id,
            "resolved_observation_id": None,
            "resolution_evidence": None,
        })
        return incident
    incident.update({
        "status": "resolved",
        "verification_state": verification or "exact_replay",
        "last_observation_id": observation_id,
        "resolved_observation_id": observation_id,
        "resolution_evidence": (
            "same_signal_repaired_with_current_engine"
            if verification == "exact_repair_replay"
            else "same_signal_replayed_without_blocker"
        ),
    })
    return incident


def _signal_resolution_mode(
    incident: Mapping[str, Any],
    signal: Mapping[str, Any],
) -> str | None:
    blocker = str(incident.get("blocker") or "")
    current_blockers = {
        str(value)
        for value in signal.get("blockers") or ()
        if str(value) and str(value) not in _TRANSIENT_BLOCKERS
    }
    if blocker in current_blockers:
        return None
    outcome = signal.get("control_outcome_parity")
    repair_outcome = signal.get("control_repair_outcome")
    actual = signal.get("actual")
    if blocker in {"control_outcome_mismatch", "control_outcome_unverified"}:
        if isinstance(outcome, Mapping) and outcome.get("status") == "exact":
            return "exact_replay"
        if (
            isinstance(repair_outcome, Mapping)
            and repair_outcome.get("status") == "exact"
            and repair_outcome.get("evidence_role")
            == "retrospective_same_signal_repair"
        ):
            return "exact_repair_replay"
        return None
    if blocker in {
        "control_repair_outcome_mismatch",
        "control_repair_outcome_unverified",
    }:
        if (
            isinstance(repair_outcome, Mapping)
            and repair_outcome.get("status") == "exact"
            and repair_outcome.get("evidence_role")
            == "retrospective_same_signal_repair"
        ):
            return "exact_repair_replay"
        return None
    if blocker in {"control_mirror_mismatch", "control_mirror_unverified"}:
        return "exact_replay" if bool(
            isinstance(actual, Mapping)
            and actual.get("control_mirror_match") is True
        ) else None
    if blocker == "mt5_reconciliation_incomplete":
        return "exact_replay" if bool(
            isinstance(actual, Mapping)
            and actual.get("complete") is True
            and actual.get("mt5_reconciled") is True
        ) else None
    if blocker == "telegram_lineage_incomplete":
        return "exact_replay" if bool(
            isinstance(actual, Mapping)
            and actual.get("telegram_lineage_complete") is True
        ) else None
    if current_blockers:
        return None

    evidence = incident.get("evidence")
    affected = (
        evidence.get("affected_candidates")
        if isinstance(evidence, Mapping)
        else None
    )
    candidate_ids = {
        str(row.get("candidate_id") or "")
        for row in affected or ()
        if isinstance(row, Mapping) and str(row.get("candidate_id") or "")
    }
    if not candidate_ids:
        return "exact_replay"
    candidates = signal.get("candidates")
    if not isinstance(candidates, Mapping):
        return None
    return "exact_replay" if all(
        isinstance(candidates.get(candidate_id), Mapping)
        and candidates[candidate_id].get("complete") is True
        and blocker not in {
            str(value)
            for value in candidates[candidate_id].get("blockers") or ()
        }
        for candidate_id in candidate_ids
    ) else None


def _classification(blocker: str) -> tuple[str, str]:
    if blocker == "control_outcome_mismatch":
        return (
            "prospective_control_parity",
            "fix_entry_fill_or_money_model_then_replay_same_signal",
        )
    if blocker == "control_outcome_unverified":
        return (
            "prospective_control_parity",
            "restore_complete_mt5_and_control_evidence_then_replay_same_signal",
        )
    if blocker == "control_repair_outcome_mismatch":
        return (
            "retrospective_control_repair",
            "fix_current_engine_then_replay_same_historical_basket",
        )
    if blocker == "control_repair_outcome_unverified":
        return (
            "retrospective_control_repair",
            "restore_repair_and_mt5_evidence_then_replay_same_historical_basket",
        )
    if blocker == "control_mirror_mismatch":
        return (
            "strategy_parity",
            "compare_live_and_shadow_logic_signatures_then_fix_shared_contract_or_adapter",
        )
    if "reconcil" in blocker or blocker.startswith("actual_"):
        return "broker_accounting", "rebuild_and_reconcile_mt5_evidence"
    if "telegram" in blocker or "registration" in blocker:
        return "source_lineage", "repair_source_event_lineage_and_replay_signal"
    if "commit" in blocker or "fingerprint" in blocker or "identity" in blocker:
        return "version_contract", "align_frozen_contract_identity_and_replay_signal"
    if any(token in blocker for token in ("tick", "money_contract", "conversion")):
        return "market_evidence", "repair_market_evidence_then_replay_signal"
    if "broker" in blocker or "fill" in blocker:
        return "broker_execution", "calibrate_broker_execution_model_and_replay_signal"
    if "lifecycle" in blocker or "terminal" in blocker or "post_flat" in blocker:
        return "strategy_lifecycle", "repair_shared_lifecycle_contract_and_replay_signal"
    return "unclassified", "investigate_root_cause_then_add_regression_replay"


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()
