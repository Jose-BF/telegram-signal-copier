"""Certified summaries for prospective strategy shadows.

The report deliberately separates hypothetical comparison from adoption.  It
may name a shadow-only leader when that candidate matrix is complete, but it
never names a final winner while actual/control evidence is unverified.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import math
from typing import Any, Iterable, Mapping

from parity_incident_registry import reconcile_parity_incidents
from strategy_shadow_catalog import build_shadow_catalog
from strategy_shadow_parity import compare_logic_signatures


_CHANNELS = ("canal1", "canal2")
_TERMINAL_STATUSES = {"closed", "cancelled"}
_ADOPTION_ONLY_BLOCKERS = {
    "actual_evidence_missing",
    "control_mirror_mismatch",
    "control_mirror_unverified",
    "control_outcome_unverified",
    "control_repair_outcome_unverified",
    "duplicate_actual_result",
    "invalid_actual_identity",
    "invalid_actual_result",
    "live_control_changed",
    "live_control_identity_mismatch",
    "minimum_sample_not_reached",
    "mt5_reconciliation_incomplete",
    "source_commit_mismatch",
    "source_commit_unverified",
    "telegram_lineage_incomplete",
    "open_parity_incidents",
}
_UNSCOPED_INTEGRITY_BLOCKERS = {
    "invalid_actual_identity",
    "invalid_candidate_identity",
}


def _as_rows(values: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(value) for value in values]


def _finite(value: object, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _is_finite_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _is_non_negative_count(value: object) -> bool:
    if isinstance(value, bool):
        return False
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return False
    return normalized >= 0 and normalized == value


def _normalized_commit(value: object) -> str:
    commit = str(value or "").strip().lower()
    if len(commit) < 7 or any(
        char not in "0123456789abcdef" for char in commit
    ):
        return ""
    return commit


def _commits_match(left: object, right: object) -> bool:
    left_commit = _normalized_commit(left)
    right_commit = _normalized_commit(right)
    if not left_commit or not right_commit:
        return False
    return (
        left_commit == right_commit
        or left_commit.startswith(right_commit)
        or right_commit.startswith(left_commit)
    )


def _parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _checkpoint_label(count: int) -> str:
    if count >= 100:
        return "evidence"
    if count >= 45:
        return "provisional"
    if count >= 15:
        return "diagnostic"
    return "collecting"


def _candidate_template() -> dict[str, Any]:
    return {
        "signals": 0,
        "entries": 0,
        "net_eur": 0.0,
        "mfe_eur": 0.0,
        "mae_eur": 0.0,
        "complete_signals": 0,
        "blocked_signals": 0,
        "open_signals": 0,
    }


def _actual_template() -> dict[str, Any]:
    return {
        "signals": 0,
        "entries": 0,
        "net_eur": 0.0,
        "complete_signals": 0,
        "blocked_signals": 0,
    }


def _rounded(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("net_eur", "mfe_eur", "mae_eur"):
        if key in payload:
            payload[key] = round(float(payload[key]), 2)
    return payload


def _optional_metric(value: object) -> float | None:
    return round(float(value), 2) if _is_finite_number(value) else None


def _money_cents(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not amount.is_finite():
        return None
    return int(
        amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 100
    )


def _control_outcome_parity(
    *,
    actual: Mapping[str, Any] | None,
    control: Mapping[str, Any] | None,
    control_blockers: set[str],
) -> dict[str, Any]:
    candidate_id = None if control is None else str(
        control.get("candidate_id") or ""
    ) or None
    reasons: list[str] = []
    if actual is None:
        reasons.append("actual_evidence_missing")
    elif (
        not _is_non_negative_count(actual.get("entry_count"))
        or _money_cents(actual.get("net_eur")) is None
        or actual.get("mt5_reconciled") is not True
    ):
        reasons.append("actual_result_unverified")
    if control is None:
        reasons.append("live_control_missing")
    elif (
        str(control.get("status") or "") not in _TERMINAL_STATUSES
        or control.get("complete") is not True
        or control_blockers
        or not _is_non_negative_count(control.get("entry_count"))
        or _money_cents(control.get("net_eur")) is None
    ):
        reasons.append("live_control_result_unverified")
    if reasons:
        return {
            "status": "unverified",
            "candidate_id": candidate_id,
            "reasons": sorted(set(reasons)),
        }

    actual_entries = int(actual["entry_count"])
    shadow_entries = int(control["entry_count"])
    actual_cents = _money_cents(actual["net_eur"])
    shadow_cents = _money_cents(control["net_eur"])
    assert actual_cents is not None and shadow_cents is not None
    entry_delta = shadow_entries - actual_entries
    money_delta = shadow_cents - actual_cents
    return {
        "status": (
            "exact" if entry_delta == 0 and money_delta == 0 else "mismatch"
        ),
        "candidate_id": candidate_id,
        "actual_entry_count": actual_entries,
        "shadow_entry_count": shadow_entries,
        "entry_count_delta": entry_delta,
        "actual_net_eur": actual_cents / 100,
        "shadow_net_eur": shadow_cents / 100,
        "net_eur_delta": money_delta / 100,
    }


def _repair_replay_view(value: object) -> dict[str, Any] | None:
    """Normalize diagnostic repair evidence without making it rankable."""

    if not isinstance(value, Mapping):
        return None
    blockers = sorted({
        str(item) for item in value.get("blockers") or () if str(item)
    })
    status = str(value.get("status") or "")
    complete = bool(
        value.get("evidence_role") == "retrospective_same_signal_repair"
        and value.get("engine_contract") == "current_worktree"
        and value.get("complete") is True
        and status in _TERMINAL_STATUSES
        and not blockers
        and _is_non_negative_count(value.get("entry_count"))
        and all(
            _is_finite_number(value.get(field))
            for field in ("net_eur", "mfe_eur", "mae_eur")
        )
    )
    return {
        "evidence_role": "retrospective_same_signal_repair",
        "engine_contract": "current_worktree",
        "source_registration_commit": value.get(
            "source_registration_commit"
        ),
        "status": status or "incomplete",
        "entry_count": (
            int(value["entry_count"])
            if _is_non_negative_count(value.get("entry_count"))
            else None
        ),
        "exit_reason": value.get("exit_reason"),
        "net_eur": _optional_metric(value.get("net_eur")) if complete else None,
        "mfe_eur": _optional_metric(value.get("mfe_eur")) if complete else None,
        "mae_eur": _optional_metric(value.get("mae_eur")) if complete else None,
        "complete": complete,
        "blockers": blockers,
    }


def _finalize_candidate_summary(payload: dict[str, Any]) -> None:
    _rounded(payload)
    payload["settled_net_eur"] = payload["net_eur"]
    payload["settled_mfe_eur"] = payload["mfe_eur"]
    payload["settled_mae_eur"] = payload["mae_eur"]
    payload["complete"] = bool(
        payload["signals"] > 0
        and payload["complete_signals"] == payload["signals"]
    )
    if not payload["complete"]:
        payload["net_eur"] = None
        payload["mfe_eur"] = None
        payload["mae_eur"] = None


def _finalize_actual_summary(payload: dict[str, Any]) -> None:
    _rounded(payload)
    payload["settled_net_eur"] = payload["net_eur"]
    payload["complete"] = bool(
        payload["signals"] > 0
        and payload["complete_signals"] == payload["signals"]
    )
    if not payload["complete"]:
        payload["net_eur"] = None


def build_report(
    candidate_rows: Iterable[Mapping[str, Any]],
    actual_rows: Iterable[Mapping[str, Any]],
    *,
    previous_incident_register: Mapping[str, Any] | None = None,
    observation_id: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic report without fitting or mutating candidates."""

    catalog = build_shadow_catalog()
    expected = {
        policy.candidate_id: policy
        for policies in catalog.values()
        for policy in policies
    }
    blockers: set[str] = set()
    signal_blockers: dict[tuple[str, str], set[str]] = defaultdict(set)
    candidate_blockers: dict[
        tuple[str, str, str], set[str]
    ] = defaultdict(set)

    actual_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in _as_rows(actual_rows):
        key = (str(row.get("channel") or ""), str(row.get("signal_id") or ""))
        if key[0] not in _CHANNELS or not key[1]:
            blockers.add("invalid_actual_identity")
            continue
        if key in actual_by_key:
            blockers.add("duplicate_actual_result")
            signal_blockers[key].add("duplicate_actual_result")
            continue
        actual_by_key[key] = row
        if (
            not _is_non_negative_count(row.get("entry_count"))
            or not _is_finite_number(row.get("net_eur"))
            or not str(row.get("exit_reason") or "")
            or not str(row.get("day") or "")
        ):
            blockers.add("invalid_actual_result")
            signal_blockers[key].add("invalid_actual_result")
        if row.get("control_mirror_match") is False:
            blockers.add("control_mirror_mismatch")
            signal_blockers[key].add("control_mirror_mismatch")
        if row.get("mt5_reconciled") is not True:
            blockers.add("mt5_reconciliation_incomplete")
            signal_blockers[key].add("mt5_reconciliation_incomplete")
        if row.get("telegram_lineage_complete") is not True:
            blockers.add("telegram_lineage_incomplete")
            signal_blockers[key].add("telegram_lineage_incomplete")

    candidate_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in _as_rows(candidate_rows):
        channel = str(row.get("channel") or "")
        signal_id = str(row.get("signal_id") or "")
        candidate_id = str(row.get("candidate_id") or "")
        signal_key = (channel, signal_id)
        key = (channel, signal_id, candidate_id)
        policy = expected.get(candidate_id)
        if channel not in _CHANNELS or not signal_id or policy is None:
            blockers.add("invalid_candidate_identity")
            signal_blockers[signal_key].add("invalid_candidate_identity")
            continue
        if policy.channel != channel:
            blockers.add("candidate_channel_mismatch")
            signal_blockers[signal_key].add("candidate_channel_mismatch")
            continue
        role = str(row.get("role") or "")
        if role not in {"live_control", "candidate"}:
            blockers.add("invalid_candidate_role")
            signal_blockers[signal_key].add("invalid_candidate_role")
            candidate_blockers[key].add("invalid_candidate_role")
            blockers.add("live_control_identity_mismatch")
            signal_blockers[signal_key].add("live_control_identity_mismatch")
            candidate_blockers[key].add("live_control_identity_mismatch")
        if key in candidate_by_key:
            blockers.add("duplicate_candidate_result")
            signal_blockers[signal_key].add("duplicate_candidate_result")
            candidate_blockers[key].add("duplicate_candidate_result")
            continue
        candidate_by_key[key] = row

        if (
            row.get("strategy_fingerprint") != policy.strategy_fingerprint
            or row.get("execution_fingerprint") != policy.execution_fingerprint
        ):
            blockers.add("candidate_fingerprint_changed")
            signal_blockers[signal_key].add("candidate_fingerprint_changed")
            candidate_blockers[key].add("candidate_fingerprint_changed")

        evidence_blockers = {
            str(value)
            for value in row.get("evidence_blockers", ())
            if str(value)
        }
        row_blockers = set(evidence_blockers)
        if str(row.get("status") or "") not in _TERMINAL_STATUSES:
            row_blockers.add("candidate_not_terminal")
        if row.get("complete") is not True:
            row_blockers.add("incomplete_candidate_result")
        metrics_valid = all(
            _is_finite_number(row.get(field))
            for field in ("net_eur", "mfe_eur", "mae_eur")
        )
        explicit_unknown_result = bool(
            str(row.get("status") or "") == "incomplete"
            and row.get("complete") is not True
            and evidence_blockers
            and all(
                row.get(field) is None
                for field in ("net_eur", "mfe_eur", "mae_eur")
            )
        )
        if (
            not _is_non_negative_count(row.get("entry_count"))
            or not (metrics_valid or explicit_unknown_result)
            or not str(row.get("exit_reason") or "")
            or not str(row.get("day") or "")
        ):
            row_blockers.add("invalid_candidate_result")
        blockers.update(row_blockers)
        signal_blockers[signal_key].update(row_blockers)
        candidate_blockers[key].update(row_blockers)

        registered = _parse_time(row.get("registered_at_utc"))
        outcome = _parse_time(row.get("outcome_at_utc"))
        if registered is None or outcome is None:
            blockers.add("causal_time_missing")
            signal_blockers[signal_key].add("causal_time_missing")
            candidate_blockers[key].add("causal_time_missing")
        elif registered > outcome:
            blockers.add("registered_after_outcome")
            signal_blockers[signal_key].add("registered_after_outcome")
            candidate_blockers[key].add("registered_after_outcome")

    candidate_signal_keys = {
        (channel, signal_id)
        for channel, signal_id, _candidate_id in candidate_by_key
    }
    for key in candidate_signal_keys - set(actual_by_key):
        blockers.add("actual_evidence_missing")
        signal_blockers[key].add("actual_evidence_missing")
    for key in set(actual_by_key) - candidate_signal_keys:
        blockers.add("candidate_evidence_missing")
        signal_blockers[key].add("candidate_evidence_missing")

    for key in set(actual_by_key) | candidate_signal_keys:
        for policy in catalog.get(key[0], ()):
            if (key[0], key[1], policy.candidate_id) not in candidate_by_key:
                blockers.add("candidate_evidence_missing")
                signal_blockers[key].add("candidate_evidence_missing")

    controls_by_channel: dict[str, set[str]] = {
        channel: set() for channel in _CHANNELS
    }
    for signal_key in set(actual_by_key) | candidate_signal_keys:
        control_ids = [
            candidate_id
            for (channel, signal_id, candidate_id), row in candidate_by_key.items()
            if (channel, signal_id) == signal_key
            and row.get("role") == "live_control"
        ]
        if len(control_ids) != 1:
            blockers.add("live_control_identity_mismatch")
            signal_blockers[signal_key].add("live_control_identity_mismatch")
            continue
        controls_by_channel[signal_key[0]].add(control_ids[0])
    for channel, values in controls_by_channel.items():
        if len(values) == 1:
            continue
        blockers.add("live_control_changed")
        for signal_key in set(actual_by_key) | candidate_signal_keys:
            if signal_key[0] == channel:
                signal_blockers[signal_key].add("live_control_changed")

    for signal_key, actual in actual_by_key.items():
        control_rows = [
            row
            for (channel, signal_id, _candidate_id), row
            in candidate_by_key.items()
            if (channel, signal_id) == signal_key
            and row.get("role") == "live_control"
        ]
        if len(control_rows) != 1:
            blockers.add("control_mirror_unverified")
            signal_blockers[signal_key].add("control_mirror_unverified")
            continue
        actual_commit = str(actual.get("source_commit") or "")
        shadow_commit = str(control_rows[0].get("source_commit") or "")
        if not _normalized_commit(actual_commit) or not _normalized_commit(
            shadow_commit
        ):
            blockers.add("source_commit_unverified")
            signal_blockers[signal_key].add("source_commit_unverified")
        elif not _commits_match(actual_commit, shadow_commit):
            blockers.add("source_commit_mismatch")
            signal_blockers[signal_key].add("source_commit_mismatch")
        explicit_mirror = actual.get("control_mirror_match")
        if (
            "control_mirror_match" in actual
            and explicit_mirror is not True
            and explicit_mirror is not False
        ):
            blockers.add("control_mirror_unverified")
            signal_blockers[signal_key].add("control_mirror_unverified")
            continue
        actual_signature = actual.get("logic_signature")
        shadow_signature = control_rows[0].get("logic_signature")
        if not isinstance(actual_signature, Mapping) or not isinstance(
            shadow_signature, Mapping
        ):
            blockers.add("control_mirror_unverified")
            signal_blockers[signal_key].add("control_mirror_unverified")
            continue
        parity = compare_logic_signatures(
            actual_signature, shadow_signature
        )
        actual["_control_parity"] = parity
        actual["control_mirror_match"] = bool(
            parity["match"] and explicit_mirror is not False
        )
        if not actual["control_mirror_match"]:
            blockers.add("control_mirror_mismatch")
            signal_blockers[signal_key].add("control_mirror_mismatch")

    for signal_key in set(actual_by_key) | candidate_signal_keys:
        control_rows = [
            row
            for (channel, signal_id, _candidate_id), row
            in candidate_by_key.items()
            if (channel, signal_id) == signal_key
            and row.get("role") == "live_control"
        ]
        if len(control_rows) != 1:
            continue
        control_commit = control_rows[0].get("source_commit")
        for (channel, signal_id, _candidate_id), row in candidate_by_key.items():
            if (channel, signal_id) != signal_key:
                continue
            candidate_commit = row.get("source_commit")
            if not _normalized_commit(candidate_commit):
                blockers.add("source_commit_unverified")
                signal_blockers[signal_key].add("source_commit_unverified")
            elif not _commits_match(control_commit, candidate_commit):
                blockers.add("source_commit_mismatch")
                signal_blockers[signal_key].add("source_commit_mismatch")

    control_outcomes: dict[tuple[str, str], dict[str, Any]] = {}
    control_repair_outcomes: dict[tuple[str, str], dict[str, Any] | None] = {}
    for signal_key in set(actual_by_key) | candidate_signal_keys:
        controls = [
            (key, row)
            for key, row in candidate_by_key.items()
            if key[:2] == signal_key and row.get("role") == "live_control"
        ]
        control_key: tuple[str, str, str] | None = None
        control_row: dict[str, Any] | None = None
        if len(controls) == 1:
            control_key, control_row = controls[0]
        outcome = _control_outcome_parity(
            actual=actual_by_key.get(signal_key),
            control=control_row,
            control_blockers=(
                set() if control_key is None else candidate_blockers[control_key]
            ),
        )
        control_outcomes[signal_key] = outcome
        repair_outcome = None
        repair = (
            None
            if control_row is None
            else _repair_replay_view(control_row.get("repair_replay"))
        )
        if repair is not None:
            repair_outcome = _control_outcome_parity(
                actual=actual_by_key.get(signal_key),
                control=repair,
                control_blockers=set(repair.get("blockers") or ()),
            )
            repair_outcome["candidate_id"] = (
                None if control_row is None else control_row.get("candidate_id")
            )
            repair_outcome["evidence_role"] = (
                "retrospective_same_signal_repair"
            )
            if repair_outcome["status"] == "mismatch":
                blockers.add("control_repair_outcome_mismatch")
                signal_blockers[signal_key].add(
                    "control_repair_outcome_mismatch"
                )
            elif repair_outcome["status"] == "unverified":
                blockers.add("control_repair_outcome_unverified")
                signal_blockers[signal_key].add(
                    "control_repair_outcome_unverified"
                )
        control_repair_outcomes[signal_key] = repair_outcome
        if outcome["status"] == "mismatch":
            blockers.add("control_outcome_mismatch")
            signal_blockers[signal_key].add("control_outcome_mismatch")
        elif outcome["status"] == "unverified":
            blockers.add("control_outcome_unverified")
            signal_blockers[signal_key].add("control_outcome_unverified")

    channel_counts = {
        channel: sum(1 for key in actual_by_key if key[0] == channel)
        for channel in _CHANNELS
    }
    checkpoint_count = min(channel_counts.values())
    if checkpoint_count < 15:
        blockers.add("minimum_sample_not_reached")

    candidate_totals = {
        candidate_id: _candidate_template() for candidate_id in expected
    }
    day_totals: dict[str, dict[str, Any]] = {}
    signal_rows: list[dict[str, Any]] = []

    all_signal_keys = sorted(
        set(actual_by_key) | candidate_signal_keys,
        key=lambda item: (str(actual_by_key.get(item, {}).get("day") or ""), item),
    )
    for channel, signal_id in all_signal_keys:
        actual = actual_by_key.get((channel, signal_id))
        day = str(
            (actual or {}).get("day")
            or next(
                (
                    row.get("day")
                    for (row_channel, row_signal, _candidate_id), row
                    in candidate_by_key.items()
                    if row_channel == channel and row_signal == signal_id
                ),
                "unknown",
            )
        )
        day_summary = day_totals.setdefault(
            day,
            {
                "day": day,
                "actual": _actual_template(),
                "candidates": {
                    candidate_id: _candidate_template()
                    for candidate_id in expected
                },
            },
        )
        if actual is not None:
            day_summary["actual"]["signals"] += 1
            day_summary["actual"]["entries"] += int(actual.get("entry_count") or 0)
            actual_complete = bool(
                _is_finite_number(actual.get("net_eur"))
                and actual.get("mt5_reconciled") is True
            )
            if actual_complete:
                day_summary["actual"]["net_eur"] += float(actual["net_eur"])
                day_summary["actual"]["complete_signals"] += 1
            else:
                day_summary["actual"]["blocked_signals"] += 1

        candidates: dict[str, Any] = {}
        for policy in catalog.get(channel, ()):
            candidate_key = (channel, signal_id, policy.candidate_id)
            row = candidate_by_key.get(candidate_key)
            if row is None:
                continue
            row_status = str(row.get("status") or "")
            result_complete = bool(
                row_status in _TERMINAL_STATUSES
                and row.get("complete") is True
                and not candidate_blockers[candidate_key]
            )
            observed_net = _optional_metric(row.get("net_eur"))
            observed_mfe = _optional_metric(row.get("mfe_eur"))
            observed_mae = _optional_metric(row.get("mae_eur"))
            candidate = {
                "candidate_id": policy.candidate_id,
                "role": str(row.get("role") or ""),
                "registration_source": row.get("registration_source"),
                "source_commit": row.get("source_commit"),
                "entry_count": int(row.get("entry_count") or 0),
                "exit_reason": row.get("exit_reason"),
                "status": row_status,
                "net_eur": observed_net if result_complete else None,
                "mfe_eur": observed_mfe if result_complete else None,
                "mae_eur": observed_mae if result_complete else None,
                "observed_net_eur": observed_net,
                "observed_mfe_eur": observed_mfe,
                "observed_mae_eur": observed_mae,
                "complete": result_complete,
                "blockers": sorted(candidate_blockers[candidate_key]),
            }
            repair_replay = _repair_replay_view(row.get("repair_replay"))
            if repair_replay is not None:
                candidate["repair_replay"] = repair_replay
            candidates[policy.candidate_id] = candidate
            total = candidate_totals[policy.candidate_id]
            daily = day_summary["candidates"][policy.candidate_id]
            for summary in (total, daily):
                summary["signals"] += 1
                summary["entries"] += candidate["entry_count"]
                summary["complete_signals"] += int(candidate["complete"])
                if candidate["complete"]:
                    summary["net_eur"] += candidate["net_eur"]
                    summary["mfe_eur"] += candidate["mfe_eur"]
                    summary["mae_eur"] += candidate["mae_eur"]
                elif row_status == "incomplete":
                    summary["blocked_signals"] += 1
                else:
                    summary["open_signals"] += 1

        control_outcome = control_outcomes[(channel, signal_id)]
        control_strategy_id = control_outcome.get("candidate_id")

        signal_rows.append(
            {
                "channel": channel,
                "signal_id": signal_id,
                "day": day,
                "actual": None if actual is None else {
                    "entry_count": int(actual.get("entry_count") or 0),
                    "exit_reason": actual.get("exit_reason"),
                    "net_eur": _optional_metric(actual.get("net_eur")),
                    "complete": actual_complete,
                    "mt5_reconciled": actual.get("mt5_reconciled") is True,
                    "telegram_lineage_complete": (
                        actual.get("telegram_lineage_complete") is True
                    ),
                    "control_mirror_match": actual.get("control_mirror_match") is True,
                    "control_parity": actual.get("_control_parity"),
                    "control_strategy_id": control_strategy_id,
                    "logic_signature_blockers": list(
                        actual.get("logic_signature_blockers") or []
                    ),
                    "source_commit": actual.get("source_commit"),
                },
                "candidates": candidates,
                "control_outcome_parity": control_outcome,
                "control_repair_outcome": control_repair_outcomes[
                    (channel, signal_id)
                ],
                "blockers": sorted(signal_blockers[(channel, signal_id)]),
            }
        )

    for total in candidate_totals.values():
        _finalize_candidate_summary(total)
    for day in day_totals.values():
        _finalize_actual_summary(day["actual"])
        for total in day["candidates"].values():
            _finalize_candidate_summary(total)

    pairings: list[dict[str, Any]] = []
    for dubai in catalog["canal1"]:
        for gold in catalog["canal2"]:
            dubai_net = candidate_totals[dubai.candidate_id]["net_eur"]
            gold_net = candidate_totals[gold.candidate_id]["net_eur"]
            pairings.append(
                {
                    "pairing": f"{dubai.candidate_id}+{gold.candidate_id}",
                    "canal1": dubai.candidate_id,
                    "canal2": gold.candidate_id,
                    "net_eur": (
                        None
                        if dubai_net is None or gold_net is None
                        else round(dubai_net + gold_net, 2)
                    ),
                }
            )
    pairings.sort(key=lambda row: (
        row["net_eur"] is None,
        -(row["net_eur"] or 0.0),
        row["pairing"],
    ))

    matrix: dict[str, dict[str, Any]] = {}
    for channel in _CHANNELS:
        signal_keys = {
            key for key in candidate_signal_keys if key[0] == channel
        }
        expected_rows = len(signal_keys) * len(catalog[channel])
        channel_rows = [
            (key, row)
            for key, row
            in candidate_by_key.items()
            if key[0] == channel
        ]
        settled_rows = sum(
            1
            for key, row in channel_rows
            if (
                str(row.get("status") or "") in _TERMINAL_STATUSES
                and row.get("complete") is True
                and not row.get("evidence_blockers")
                and not candidate_blockers[key]
            )
        )
        open_rows = sum(
            1
            for key, row in channel_rows
            if (
                str(row.get("status") or "") not in {
                    *_TERMINAL_STATUSES,
                    "incomplete",
                }
                and candidate_blockers[key].issubset({
                    "candidate_not_terminal",
                    "incomplete_candidate_result",
                })
            )
        )
        blocked_rows = max(
            0,
            expected_rows - settled_rows - open_rows,
        )
        matrix[channel] = {
            "eligible_signals": len(signal_keys),
            "expected_rows": expected_rows,
            "observed_rows": len(channel_rows),
            "settled_rows": settled_rows,
            "blocked_rows": blocked_rows,
            "open_rows": open_rows,
            "complete": (
                len(channel_rows) == expected_rows
                and settled_rows == expected_rows
            ),
        }

    if not any(values["eligible_signals"] for values in matrix.values()):
        blockers.add("no_eligible_signals")

    parity_incidents = reconcile_parity_incidents(
        signal_rows=signal_rows,
        global_blockers=blockers,
        previous=previous_incident_register,
        observation_id=observation_id,
    )
    incident_channels = {
        str(row.get("channel") or "")
        for row in parity_incidents["incidents"]
        if row.get("status") != "resolved" and row.get("channel")
    }
    has_global_open_incident = any(
        row.get("status") != "resolved" and not row.get("channel")
        for row in parity_incidents["incidents"]
    )
    if parity_incidents["open_count"]:
        blockers.add("open_parity_incidents")
    if parity_incidents["comparison_blocking_open_count"]:
        blockers.add("open_comparison_incidents")

    comparison_blockers = {
        blocker
        for blocker in blockers
        if blocker not in _ADOPTION_ONLY_BLOCKERS
    }
    comparison_allowed = not comparison_blockers

    shadow_leader = None
    if comparison_allowed:
        channel_leaders: dict[str, str | None] = {}
        for channel in _CHANNELS:
            if matrix[channel]["eligible_signals"] == 0:
                channel_leaders[channel] = None
                continue
            policies = list(catalog[channel])
            channel_leaders[channel] = max(
                policies,
                key=lambda policy: (
                    (
                        float("-inf")
                        if candidate_totals[policy.candidate_id]["net_eur"]
                        is None
                        else candidate_totals[policy.candidate_id]["net_eur"]
                    ),
                    -policies.index(policy),
                ),
            ).candidate_id
        pairing = None
        if all(channel_leaders.values()):
            pairing = (
                f"{channel_leaders['canal1']}+"
                f"{channel_leaders['canal2']}"
            )
        shadow_leader = {
            "canal1": channel_leaders["canal1"],
            "canal2": channel_leaders["canal2"],
            "pairing": pairing,
        }

    ranking_allowed = not blockers
    winner = None
    if ranking_allowed:
        best_dubai = max(
            catalog["canal1"],
            key=lambda policy: (
                candidate_totals[policy.candidate_id]["net_eur"],
                -list(catalog["canal1"]).index(policy),
            ),
        )
        best_gold = max(
            catalog["canal2"],
            key=lambda policy: (
                candidate_totals[policy.candidate_id]["net_eur"],
                -list(catalog["canal2"]).index(policy),
            ),
        )
        winner = {
            "canal1": best_dubai.candidate_id,
            "canal2": best_gold.candidate_id,
            "pairing": f"{best_dubai.candidate_id}+{best_gold.candidate_id}",
        }

    channel_verdicts: dict[str, dict[str, Any]] = {}
    for channel in _CHANNELS:
        channel_blockers = {
            blocker
            for key, values in signal_blockers.items()
            if key[0] == channel
            for blocker in values
        }
        channel_blockers.update(blockers & _UNSCOPED_INTEGRITY_BLOCKERS)
        if channel in incident_channels or has_global_open_incident:
            channel_blockers.add("open_parity_incidents")
        if any(
            row.get("status") != "resolved"
            and row.get("blocks_comparison") is True
            and (not row.get("channel") or row.get("channel") == channel)
            for row in parity_incidents["incidents"]
        ):
            channel_blockers.add("open_comparison_incidents")
        if len(controls_by_channel[channel]) != 1:
            channel_blockers.add("live_control_changed")
        if channel_counts[channel] < 15:
            channel_blockers.add("minimum_sample_not_reached")
        if matrix[channel]["eligible_signals"] == 0:
            channel_blockers.add("no_eligible_signals")

        channel_comparison_blockers = {
            blocker
            for blocker in channel_blockers
            if blocker not in _ADOPTION_ONLY_BLOCKERS
        }
        channel_comparison_allowed = not channel_comparison_blockers
        channel_leader = None
        if channel_comparison_allowed:
            policies = list(catalog[channel])
            channel_leader = max(
                policies,
                key=lambda policy: (
                    float("-inf")
                    if candidate_totals[policy.candidate_id]["net_eur"] is None
                    else candidate_totals[policy.candidate_id]["net_eur"],
                    -policies.index(policy),
                ),
            ).candidate_id
        channel_ranking_allowed = not channel_blockers
        channel_verdicts[channel] = {
            "comparison_allowed": channel_comparison_allowed,
            "comparison_blockers": sorted(channel_comparison_blockers),
            "ranking_allowed": channel_ranking_allowed,
            "claim_allowed": (
                channel_ranking_allowed and channel_counts[channel] >= 100
            ),
            "winner": channel_leader if channel_ranking_allowed else None,
            "shadow_leader": channel_leader,
            "blockers": sorted(channel_blockers),
            "checkpoint": {
                "label": _checkpoint_label(channel_counts[channel]),
                "untouched_signals": channel_counts[channel],
            },
            "matrix": matrix[channel],
        }

    control_parity: dict[str, dict[str, Any]] = {}
    for channel in _CHANNELS:
        summary = {
            "matched": 0,
            "mismatched": 0,
            "unverified": 0,
            "by_source_commit": {},
        }
        for (row_channel, _signal_id), actual in actual_by_key.items():
            if row_channel != channel:
                continue
            commit = str(actual.get("source_commit") or "unknown")
            cohort = summary["by_source_commit"].setdefault(commit, {
                "matched": 0,
                "mismatched": 0,
                "unverified": 0,
            })
            if actual.get("control_mirror_match") is True:
                status = "matched"
            elif actual.get("control_mirror_match") is False:
                status = "mismatched"
            else:
                status = "unverified"
            summary[status] += 1
            cohort[status] += 1
        control_parity[channel] = summary

    control_outcome_summary = {
        channel: {"exact": 0, "mismatch": 0, "unverified": 0}
        for channel in _CHANNELS
    }
    for (channel, _signal_id), outcome in control_outcomes.items():
        status = str(outcome.get("status") or "unverified")
        if status not in control_outcome_summary[channel]:
            status = "unverified"
        control_outcome_summary[channel][status] += 1

    label = _checkpoint_label(checkpoint_count)
    return {
        "schema_version": 1,
        "comparison_allowed": comparison_allowed,
        "comparison_blockers": sorted(comparison_blockers),
        "ranking_allowed": ranking_allowed,
        "claim_allowed": ranking_allowed and checkpoint_count >= 100,
        "promotion_allowed": False,
        "winner": winner,
        "shadow_leader": shadow_leader,
        "blockers": sorted(blockers),
        "checkpoint": {
            "label": label,
            "untouched_signals": sum(channel_counts.values()),
            "minimum_per_channel": checkpoint_count,
            "per_channel": {
                channel: _checkpoint_label(count)
                for channel, count in channel_counts.items()
            },
        },
        "signals": signal_rows,
        "days": [day_totals[day] for day in sorted(day_totals)],
        "candidate_totals": candidate_totals,
        "pairings": pairings,
        "matrix": matrix,
        "channels": channel_verdicts,
        "control_parity": control_parity,
        "control_outcome_parity": control_outcome_summary,
        "parity_incidents": parity_incidents,
    }
