"""Certified summaries for prospective strategy shadows.

The report deliberately separates observed values from evidence gates.  It may
show incomplete arithmetic, but it never names a leader while causal evidence
or the live-control mirror is unverified.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import math
from typing import Any, Iterable, Mapping

from strategy_shadow_catalog import build_shadow_catalog


_CHANNELS = ("canal1", "canal2")
_TERMINAL_STATUSES = {"closed", "cancelled"}


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
    }


def _actual_template() -> dict[str, Any]:
    return {"signals": 0, "entries": 0, "net_eur": 0.0}


def _rounded(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("net_eur", "mfe_eur", "mae_eur"):
        if key in payload:
            payload[key] = round(float(payload[key]), 2)
    return payload


def build_report(
    candidate_rows: Iterable[Mapping[str, Any]],
    actual_rows: Iterable[Mapping[str, Any]],
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
        if row.get("control_mirror_match") is not True:
            blockers.add("control_mirror_mismatch")
            signal_blockers[key].add("control_mirror_mismatch")
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
            blockers.add("live_control_identity_mismatch")
            signal_blockers[signal_key].add("live_control_identity_mismatch")
        if key in candidate_by_key:
            blockers.add("duplicate_candidate_result")
            signal_blockers[signal_key].add("duplicate_candidate_result")
            continue
        candidate_by_key[key] = row

        if (
            row.get("strategy_fingerprint") != policy.strategy_fingerprint
            or row.get("execution_fingerprint") != policy.execution_fingerprint
        ):
            blockers.add("candidate_fingerprint_changed")
            signal_blockers[signal_key].add("candidate_fingerprint_changed")

        row_blockers = {
            str(value)
            for value in row.get("evidence_blockers", ())
            if str(value)
        }
        if str(row.get("status") or "") not in _TERMINAL_STATUSES:
            row_blockers.add("candidate_not_terminal")
        if row.get("complete") is not True:
            row_blockers.add("incomplete_candidate_result")
        if (
            not _is_non_negative_count(row.get("entry_count"))
            or any(
                not _is_finite_number(row.get(field))
                for field in ("net_eur", "mfe_eur", "mae_eur")
            )
            or not str(row.get("exit_reason") or "")
            or not str(row.get("day") or "")
        ):
            row_blockers.add("invalid_candidate_result")
        blockers.update(row_blockers)
        signal_blockers[signal_key].update(row_blockers)

        registered = _parse_time(row.get("registered_at_utc"))
        outcome = _parse_time(row.get("outcome_at_utc"))
        if registered is None or outcome is None:
            blockers.add("causal_time_missing")
            signal_blockers[signal_key].add("causal_time_missing")
        elif registered > outcome:
            blockers.add("registered_after_outcome")
            signal_blockers[signal_key].add("registered_after_outcome")

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
    if any(len(values) != 1 for values in controls_by_channel.values()):
        blockers.add("live_control_changed")

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
            day_summary["actual"]["net_eur"] += _finite(actual.get("net_eur"))

        candidates: dict[str, Any] = {}
        control_prediction: dict[str, Any] = {}
        for policy in catalog.get(channel, ()):
            row = candidate_by_key.get((channel, signal_id, policy.candidate_id))
            if row is None:
                continue
            candidate = {
                "candidate_id": policy.candidate_id,
                "role": str(row.get("role") or ""),
                "entry_count": int(row.get("entry_count") or 0),
                "exit_reason": row.get("exit_reason"),
                "net_eur": round(_finite(row.get("net_eur")), 2),
                "mfe_eur": round(_finite(row.get("mfe_eur")), 2),
                "mae_eur": round(_finite(row.get("mae_eur")), 2),
                "complete": row.get("complete") is True,
                "blockers": sorted(signal_blockers[(channel, signal_id)]),
            }
            candidates[policy.candidate_id] = candidate
            total = candidate_totals[policy.candidate_id]
            daily = day_summary["candidates"][policy.candidate_id]
            for summary in (total, daily):
                summary["signals"] += 1
                summary["entries"] += candidate["entry_count"]
                summary["net_eur"] += candidate["net_eur"]
                summary["mfe_eur"] += candidate["mfe_eur"]
                summary["mae_eur"] += candidate["mae_eur"]
                summary["complete_signals"] += int(candidate["complete"])
            if row.get("role") == "live_control":
                prediction = row.get("control_prediction")
                if isinstance(prediction, Mapping):
                    control_prediction = dict(prediction)

        signal_rows.append(
            {
                "channel": channel,
                "signal_id": signal_id,
                "day": day,
                "actual": None if actual is None else {
                    "entry_count": int(actual.get("entry_count") or 0),
                    "exit_reason": actual.get("exit_reason"),
                    "net_eur": round(_finite(actual.get("net_eur")), 2),
                    "control_mirror_match": actual.get("control_mirror_match") is True,
                },
                "candidates": candidates,
                "control_prediction": control_prediction,
                "blockers": sorted(signal_blockers[(channel, signal_id)]),
            }
        )

    for total in candidate_totals.values():
        _rounded(total)
    for day in day_totals.values():
        _rounded(day["actual"])
        for total in day["candidates"].values():
            _rounded(total)

    pairings: list[dict[str, Any]] = []
    for dubai in catalog["canal1"]:
        for gold in catalog["canal2"]:
            pairings.append(
                {
                    "pairing": f"{dubai.candidate_id}+{gold.candidate_id}",
                    "canal1": dubai.candidate_id,
                    "canal2": gold.candidate_id,
                    "net_eur": round(
                        candidate_totals[dubai.candidate_id]["net_eur"]
                        + candidate_totals[gold.candidate_id]["net_eur"],
                        2,
                    ),
                }
            )
    pairings.sort(key=lambda row: (-row["net_eur"], row["pairing"]))

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

    label = _checkpoint_label(checkpoint_count)
    return {
        "schema_version": 1,
        "ranking_allowed": ranking_allowed,
        "claim_allowed": ranking_allowed and checkpoint_count >= 100,
        "promotion_allowed": False,
        "winner": winner,
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
    }
