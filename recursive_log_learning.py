"""Build deterministic, review-gated reliability learning artifacts.

This module is deliberately offline. Runtime execution modules must never
import it: logs can propose and measure patterns, but only reviewed source
changes and regression tests may alter live behavior.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
DEFAULT_EVENTS = DATA_DIR / "trade_events.jsonl"
DEFAULT_REPLAY = DATA_DIR / "replay_trades.jsonl"
DEFAULT_ACCOUNTING = DATA_DIR / "accounting_replay_audit.jsonl"
DEFAULT_OBSERVED = DATA_DIR / "observed_tick_replay_audit.jsonl"
DEFAULT_PROVIDER = DATA_DIR / "provider_signal_catalog.json"
DEFAULT_STRATEGY_FARM = DATA_DIR / "strategy_farm.json"
DEFAULT_REVIEWS = DATA_DIR / "log_pattern_reviews.json"
DEFAULT_REPORT = DATA_DIR / "log_learning_report.json"
DEFAULT_REGISTRY = DATA_DIR / "log_pattern_registry.json"

SCHEMA_VERSION = 1
ALLOWED_STATUSES = {
    "observed", "candidate", "covered", "regressed", "dismissed"
}
SEVERITY_WEIGHT = {
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


@dataclass(frozen=True)
class PatternObservation:
    pattern_id: str
    category: str
    template: str
    severity: str
    incident_key: str
    raw_count: int = 1
    ts_utc: str | None = None
    signal: str | None = None
    channel: str | None = None
    event: str | None = None
    detail: str | None = None
    financial_impact: float | None = None


@dataclass(frozen=True)
class LearningOutputs:
    report: dict
    registry: dict
    report_bytes: bytes
    registry_bytes: bytes


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_json(path: Path, default: dict | None = None) -> dict:
    if not path.exists():
        return dict(default or {})
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _parse_dt(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _day(value: object) -> str | None:
    parsed = _parse_dt(value)
    return parsed.date().isoformat() if parsed else None


def _time_bucket(value: object, seconds: int = 300) -> str:
    parsed = _parse_dt(value)
    if parsed is None:
        return "unknown_time"
    epoch = int(parsed.timestamp())
    bucket = epoch - (epoch % seconds)
    return datetime.fromtimestamp(bucket, tz=timezone.utc).isoformat()


def _slug(value: object) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "unknown").lower())
    return text.strip("_") or "unknown"


def _blocker_code(value: object) -> str:
    text = str(value or "unknown").strip().lower()
    prefix = text.split(":", 1)[0]
    prefix = re.sub(r"ticket_?\d+", "ticket", prefix)
    prefix = re.sub(r"\d+(?:\.\d+)?", "n", prefix)
    return _slug(prefix)


def _signal_ts(row: dict) -> str | None:
    for key in (
        "signal_dt_utc", "first_observed_utc", "signal_ts_utc",
        "open_dt_utc", "ts", "telegram_ts_utc",
    ):
        if row.get(key):
            return str(row[key])
    return None


def _signal_channel(signal: object, explicit: object = None) -> str | None:
    if explicit:
        return str(explicit)
    text = str(signal or "")
    return text.rsplit("_", 1)[0] if "_" in text else None


def _semantic_cluster(text: object) -> tuple[str, str]:
    raw = " ".join(str(text or "").split())
    upper = raw.upper()
    if not raw:
        return "empty_or_media", "empty text or media-only provider message"
    if re.search(
        r"\b(?:VIP|JOIN|GROUP|COURSE|SPOTS?|ZOOM|LIVE\s+NOW|WE\s+ARE\s+LIVE|"
        r"ANNOUNCEMENT|GOLDTRADINGSUPPORT)\b",
        upper,
    ):
        return "non_trading_announcement", "provider announcement outside trade management"
    if re.search(r"\b(?:IF|UNLESS|AS\s+LONG\s+AS|ONCE|WHEN)\b", upper):
        return "conditional_plan", "conditional provider management or market plan"
    if re.search(r"\b(?:CLOSE|SECURE|PARTIALS?|PROTECT)\b", upper):
        return "exit_or_risk_guidance", "exit, partial-close or risk-management guidance"
    if re.search(r"\b(?:TP|TARGET|SL|STOP\s*LOSS)\b", upper):
        return "level_or_outcome_variant", "target, stop or level vocabulary variant"
    if re.search(r"\b(?:PROFIT|PIPS?|RUNNING|ENTRY|ENTRIES|TRADE)\b", upper):
        return "trade_progress_commentary", "trade progress or floating-profit commentary"
    if re.search(r"\b(?:ZONE|SUPPORT|RESISTANCE|CANDLE|GOLD|MARKET)\b", upper):
        return "market_context", "market context without a canonical action"
    if re.search(r"\b(?:WIN|FULL\s+TARGET|CONGRAT|BAM+)\b", upper):
        return "performance_commentary", "provider performance commentary"
    normalized = re.sub(r"\d+(?:\.\d+)?", "n", raw.lower())
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized).strip()
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:10]
    return f"other_{digest}", "unclassified provider vocabulary family"


def _collect_event_patterns(events: Iterable[dict]) -> list[PatternObservation]:
    observations: list[PatternObservation] = []
    for row in events:
        event = str(row.get("ev") or "")
        ts = row.get("ts")
        signal = str(row.get("sig") or "") or None
        channel = _signal_channel(signal, row.get("channel"))

        retcode = row.get("last_retcode", row.get("retcode"))
        if (
            event == "mt5_action_failed"
            and str(row.get("kind") or "").upper() == "MODIFY_SLTP"
            and int(retcode or 0) == 10016
        ):
            incident = "|".join((
                signal or "unknown_signal",
                "MODIFY_SLTP",
                _time_bucket(ts),
            ))
            observations.append(PatternObservation(
                pattern_id="execution.invalid_stops.modify_sltp",
                category="execution",
                template="broker rejected a structurally invalid sl/tp modification",
                severity="critical",
                incident_key=incident,
                raw_count=max(1, int(row.get("attempts") or 1)),
                ts_utc=str(ts) if ts else None,
                signal=signal,
                channel=channel,
                event=event,
                detail=str(row.get("reason") or "retcode 10016"),
            ))
            continue

        if event == "management_reply_unresolved":
            reason = _slug(row.get("reason"))
            actionable = bool(row.get("actionable"))
            pattern_id = (
                f"semantics.unresolved_actionable_management.{reason}"
                if actionable
                else f"semantics.unresolved_informational_reply.{reason}"
            )
            observations.append(PatternObservation(
                pattern_id=pattern_id,
                category="semantics",
                template=(
                    "actionable provider management could not be linked"
                    if actionable
                    else "informational provider reply could not be linked"
                ),
                severity="high" if actionable else "low",
                incident_key="|".join((
                    signal or "unknown_signal",
                    str(row.get("reply_to_msg_id") or "unknown_root"),
                    reason,
                )),
                ts_utc=str(ts) if ts else None,
                signal=signal,
                channel=channel,
                event=event,
                detail=str(row.get("text_preview") or reason),
            ))
            continue

        if (
            event == "audit_issue_detected"
            and row.get("code") == "mt5_orphan_position"
        ):
            observations.append(PatternObservation(
                pattern_id="execution.mt5_orphan_position",
                category="execution",
                template="bot position appeared before a live signal adopted it",
                severity="high",
                incident_key="|".join((
                    str(row.get("parsed_signal_id") or signal or "unknown_signal"),
                    str(row.get("ticket") or "unknown_ticket"),
                )),
                ts_utc=str(ts) if ts else None,
                signal=str(row.get("parsed_signal_id") or signal or "") or None,
                channel=channel,
                event=event,
                detail=str(row.get("detail") or row.get("code")),
            ))
            continue

        if event == "notify_failed":
            observations.append(PatternObservation(
                pattern_id="observability.notification_delivery_failed",
                category="observability",
                template="human-review notification could not be delivered",
                severity="high",
                incident_key="|".join((
                    signal or "bot",
                    _time_bucket(ts, seconds=60),
                    str(row.get("method") or "unknown_method"),
                )),
                ts_utc=str(ts) if ts else None,
                signal=signal,
                channel=channel,
                event=event,
                detail=str(row.get("error") or row.get("status") or "delivery failed"),
            ))
            continue

        if event in {"telegram_connection_change", "mt5_connection_change"}:
            if row.get("connected") is False:
                source = "telegram" if event.startswith("telegram") else "mt5"
                observations.append(PatternObservation(
                    pattern_id=f"capture.{source}_connection_lost",
                    category="capture",
                    template=f"{source} connection entered a disconnected state",
                    severity="high",
                    incident_key="|".join((source, _time_bucket(ts, seconds=60))),
                    ts_utc=str(ts) if ts else None,
                    signal=signal,
                    channel=channel,
                    event=event,
                    detail=str(row.get("reason") or "connected=false"),
                ))
    return observations


def _collect_replay_patterns(
    replay_rows: Iterable[dict],
) -> tuple[list[PatternObservation], dict[str, dict]]:
    observations: list[PatternObservation] = []
    signal_meta: dict[str, dict] = {}
    for row in replay_rows:
        signal = str(row.get("sig_id") or "") or None
        if signal:
            signal_meta[signal] = row
        channel = _signal_channel(signal, row.get("channel"))
        ts = _signal_ts(row)
        blockers = row.get("simulation_blockers") or []
        for code, count in Counter(_blocker_code(item) for item in blockers).items():
            observations.append(PatternObservation(
                pattern_id=f"replay.{code}",
                category="replay",
                template=f"causal replay blocker: {code.replace('_', ' ')}",
                severity="high",
                incident_key=f"{signal or 'unknown_signal'}|{code}",
                raw_count=count,
                ts_utc=ts,
                signal=signal,
                channel=channel,
                event="simulation_blocker",
                detail=code,
            ))
    return observations, signal_meta


def _collect_accounting_patterns(
    rows: Iterable[dict],
    signal_meta: dict[str, dict],
) -> list[PatternObservation]:
    observations: list[PatternObservation] = []
    for row in rows:
        status = str(row.get("status") or "unknown").lower()
        if status == "exact":
            continue
        signal = str(row.get("sig_id") or "") or None
        source = signal_meta.get(signal or "", {})
        pattern_suffix = "reconstructed_trade" if status == "reconstructed" else status
        observations.append(PatternObservation(
            pattern_id=f"accounting.{_slug(pattern_suffix)}",
            category="accounting",
            template=f"accounting replay status is {status}",
            severity="medium" if status == "reconstructed" else "critical",
            incident_key=f"{signal or 'unknown_signal'}|{status}",
            ts_utc=_signal_ts(row) or _signal_ts(source),
            signal=signal,
            channel=_signal_channel(signal, row.get("channel") or source.get("channel")),
            event="accounting_replay",
            detail="; ".join(row.get("blockers") or row.get("assumptions") or [status]),
            financial_impact=(
                abs(float(row.get("diff")))
                if row.get("diff") is not None else None
            ),
        ))
    return observations


def _collect_observed_patterns(
    rows: Iterable[dict],
    signal_meta: dict[str, dict],
) -> list[PatternObservation]:
    observations: list[PatternObservation] = []
    for row in rows:
        if str(row.get("status") or "").lower() == "exact":
            continue
        signal = str(row.get("sig_id") or "") or None
        source = signal_meta.get(signal or "", {})
        blockers = row.get("blockers") or [str(row.get("status") or "blocked")]
        for code, count in Counter(_blocker_code(item) for item in blockers).items():
            observations.append(PatternObservation(
                pattern_id=f"market_replay.{code}",
                category="market_replay",
                template=f"observed-tick replay blocker: {code.replace('_', ' ')}",
                severity="critical",
                incident_key=f"{signal or 'unknown_signal'}|{code}",
                raw_count=count,
                ts_utc=_signal_ts(source),
                signal=signal,
                channel=_signal_channel(signal, row.get("channel") or source.get("channel")),
                event="observed_tick_replay",
                detail=code,
            ))
    return observations


def _collect_provider_patterns(catalog: dict) -> list[PatternObservation]:
    observations: list[PatternObservation] = []
    for record in catalog.get("signals") or []:
        signal = str(record.get("provider_signal_id") or "") or None
        channel = _signal_channel(signal, record.get("channel"))
        ts = _signal_ts(record)
        record_type = str(record.get("record_type") or "formal_signal")

        if record_type == "unknown_candidate":
            revisions = record.get("revisions") or []
            latest_text = revisions[-1].get("text") if revisions else ""
            cluster, template = _semantic_cluster(latest_text)
            observations.append(PatternObservation(
                pattern_id=f"semantics.unknown_provider_record.{cluster}",
                category="semantics",
                template=f"unknown provider record: {template}",
                severity="medium",
                incident_key=signal or f"unknown|{ts}",
                ts_utc=ts,
                signal=signal,
                channel=channel,
                event="provider_catalog",
                detail=str(record.get("record_type_reason") or record_type),
            ))

        if record_type == "formal_signal":
            for gap in record.get("semantic_gaps") or []:
                category = "capture" if gap == "missing_root_message" else "semantics"
                observations.append(PatternObservation(
                    pattern_id=f"{category}.{_slug(gap)}",
                    category=category,
                    template=f"formal signal has canonical gap: {gap.replace('_', ' ')}",
                    severity="critical",
                    incident_key=f"{signal or 'unknown_signal'}|{gap}",
                    ts_utc=ts,
                    signal=signal,
                    channel=channel,
                    event="provider_catalog",
                    detail=gap,
                ))
            if record.get("duplicate_execution"):
                observations.append(PatternObservation(
                    pattern_id="execution.duplicate_signal_execution",
                    category="execution",
                    template="one provider signal is linked to duplicate executions",
                    severity="critical",
                    incident_key=signal or f"duplicate|{ts}",
                    raw_count=max(2, int(record.get("execution_count") or 2)),
                    ts_utc=ts,
                    signal=signal,
                    channel=channel,
                    event="provider_catalog",
                    detail="duplicate execution links",
                ))

        for item in record.get("management_events") or []:
            if item.get("classified_action") or item.get("semantic_source") != "unclassified":
                continue
            message_id = item.get("message_id")
            cluster, template = _semantic_cluster(item.get("text"))
            observations.append(PatternObservation(
                pattern_id=f"semantics.unclassified_management.{cluster}",
                category="semantics",
                template=f"unclassified provider management: {template}",
                severity="high",
                incident_key=f"{signal or 'unknown_signal'}|{message_id or _signal_ts(item)}",
                raw_count=max(1, int(item.get("raw_versions") or 1)),
                ts_utc=_signal_ts(item) or ts,
                signal=signal,
                channel=channel,
                event="provider_catalog_management",
                detail=str(item.get("text") or "unclassified management"),
            ))

        media = record.get("media") or {}
        if (
            record_type == "context_setup"
            and media.get("availability") not in (None, "none")
            and media.get("extraction_status") in (None, "not_extracted", "pending")
        ):
            observations.append(PatternObservation(
                pattern_id="semantics.context_media_not_extracted",
                category="semantics",
                template="provider context image is retained but not machine-extracted",
                severity="low",
                incident_key=signal or f"media|{ts}",
                ts_utc=ts,
                signal=signal,
                channel=channel,
                event="provider_catalog_media",
                detail=str(media.get("availability") or "metadata_only"),
            ))
    return observations


def collect_normalized_patterns(
    *,
    events: Iterable[dict],
    replay_rows: Iterable[dict],
    accounting_rows: Iterable[dict],
    observed_rows: Iterable[dict],
    provider_catalog: dict,
) -> list[PatternObservation]:
    replay_patterns, signal_meta = _collect_replay_patterns(replay_rows)
    return (
        _collect_event_patterns(events)
        + replay_patterns
        + _collect_accounting_patterns(accounting_rows, signal_meta)
        + _collect_observed_patterns(observed_rows, signal_meta)
        + _collect_provider_patterns(provider_catalog)
    )


def merge_review_metadata(pattern: dict, review: dict) -> dict:
    result = dict(pattern)
    status = str(review.get("status") or "")
    if status not in {"covered", "dismissed"}:
        raise ValueError("review evidence must set status covered or dismissed")
    common = ("reviewed_by", "reviewed_at_utc")
    if any(not review.get(field) for field in common):
        raise ValueError("review evidence requires reviewer and review timestamp")
    if _parse_dt(review.get("reviewed_at_utc")) is None:
        raise ValueError("review evidence has an invalid review timestamp")

    if status == "covered":
        required = (
            "rule_version", "regression_test", "covered_after_utc",
            "shadow_corpus_passed",
        )
        if any(field not in review or review.get(field) in (None, "") for field in required):
            raise ValueError("review evidence requires rule, test, coverage time and shadow result")
        if review.get("shadow_corpus_passed") is not True:
            raise ValueError("review evidence requires a successful shadow corpus evaluation")
        covered_after = _parse_dt(review.get("covered_after_utc"))
        if covered_after is None:
            raise ValueError("review evidence has an invalid coverage timestamp")
        last_seen = _parse_dt(result.get("last_seen_utc"))
        result["status"] = (
            "regressed"
            if last_seen is not None and last_seen > covered_after
            else "covered"
        )
        result["coverage"] = {
            "rule_version": review["rule_version"],
            "regression_test": review["regression_test"],
            "covered_after_utc": review["covered_after_utc"],
            "shadow_corpus_passed": bool(review["shadow_corpus_passed"]),
            "reviewed_by": review["reviewed_by"],
            "reviewed_at_utc": review["reviewed_at_utc"],
        }
    else:
        if not review.get("dismissal_reason"):
            raise ValueError("review evidence requires a dismissal reason")
        result["status"] = "dismissed"
        result["coverage"] = {
            "rule_version": None,
            "regression_test": None,
            "covered_after_utc": None,
            "shadow_corpus_passed": False,
            "reviewed_by": review["reviewed_by"],
            "reviewed_at_utc": review["reviewed_at_utc"],
            "dismissal_reason": review["dismissal_reason"],
        }
    return result


def _candidate_reason(
    severity: str,
    occurrences: int,
    affected_days: int,
) -> str | None:
    if severity in {"critical", "high"}:
        return "material reliability impact"
    if affected_days > 1:
        return "pattern recurred across retained sessions"
    if occurrences > 1:
        return "pattern affected multiple independent incidents"
    return None


def _aggregate_patterns(
    observations: Iterable[PatternObservation],
    review_metadata: dict,
) -> list[dict]:
    grouped: dict[str, list[PatternObservation]] = defaultdict(list)
    for observation in observations:
        grouped[observation.pattern_id].append(observation)

    reviews = review_metadata.get("reviews", review_metadata)
    patterns: list[dict] = []
    for pattern_id, rows in sorted(grouped.items()):
        rows = sorted(
            rows,
            key=lambda row: (
                row.ts_utc or "", row.incident_key, row.signal or "", row.detail or ""
            ),
        )
        incidents = {row.incident_key for row in rows}
        signals = sorted({row.signal for row in rows if row.signal})
        channels = sorted({row.channel for row in rows if row.channel})
        days = sorted({day for row in rows if (day := _day(row.ts_utc))})
        timestamps = sorted(row.ts_utc for row in rows if _parse_dt(row.ts_utc))
        severity = max(rows, key=lambda row: SEVERITY_WEIGHT[row.severity]).severity
        raw_events = sum(max(1, int(row.raw_count)) for row in rows)
        occurrences = len(incidents)
        reason = _candidate_reason(severity, occurrences, len(days))
        financial_values = [
            row.financial_impact for row in rows
            if row.financial_impact is not None
        ]

        evidence_by_incident: dict[str, dict] = {}
        for row in rows:
            evidence_by_incident.setdefault(row.incident_key, {
                "ts_utc": row.ts_utc,
                "signal": row.signal,
                "channel": row.channel,
                "event": row.event,
                "detail": row.detail,
            })
        evidence = [
            evidence_by_incident[key]
            for key in sorted(evidence_by_incident)
        ][:12]

        pattern = {
            "pattern_id": pattern_id,
            "category": rows[0].category,
            "template": rows[0].template,
            "status": "candidate" if reason else "observed",
            "first_seen_utc": timestamps[0] if timestamps else None,
            "last_seen_utc": timestamps[-1] if timestamps else None,
            "occurrences": occurrences,
            "raw_events": raw_events,
            "affected_signal_count": len(signals),
            "affected_signals": signals,
            "affected_day_count": len(days),
            "affected_days": days,
            "affected_channels": channels,
            "financial_impact": (
                round(sum(financial_values), 2) if financial_values else None
            ),
            "severity": severity,
            "recurrence": (
                "cross_session" if len(days) > 1
                else "multiple_incidents" if occurrences > 1
                else "single_incident"
            ),
            "candidate_reason": reason,
            "priority_score": (
                SEVERITY_WEIGHT[severity] * 10_000
                + min(len(days), 100) * 20
                + min(len(signals), 100) * 10
                + min(raw_events, 1000) // 10
            ),
            "coverage": {
                "rule_version": None,
                "regression_test": None,
                "covered_after_utc": None,
                "shadow_corpus_passed": False,
                "reviewed_by": None,
                "reviewed_at_utc": None,
            },
            "evidence": evidence,
        }
        review = reviews.get(pattern_id) if isinstance(reviews, dict) else None
        if review:
            pattern = merge_review_metadata(pattern, review)
        patterns.append(pattern)

    patterns.sort(key=lambda row: (-row["priority_score"], row["pattern_id"]))
    return patterns


def _layer(passed: bool, metrics: dict, blockers: list[str]) -> dict:
    return {
        "passed": bool(passed),
        "status": "passed" if passed else "blocked",
        "hard_gate": True,
        "metrics": metrics,
        "blockers": sorted(set(blockers)),
    }


def build_health(
    *,
    provider_catalog: dict,
    accounting_rows: list[dict],
    observed_rows: list[dict],
    replay_rows: list[dict],
    strategy_farm: dict,
    patterns: list[dict],
) -> dict:
    records = provider_catalog.get("signals") or []
    formal = [
        row for row in records
        if row.get("record_type", "formal_signal") == "formal_signal"
    ]
    missing_root = sum(
        "missing_root_message" in (row.get("semantic_gaps") or [])
        for row in formal
    )
    capture_passed = bool(formal) and missing_root == 0
    capture = _layer(
        capture_passed,
        {
            "formal_signals": len(formal),
            "root_messages_captured": len(formal) - missing_root,
            "missing_root_messages": missing_root,
        },
        ([] if formal else ["no_formal_signals"])
        + ([f"missing_root_messages:{missing_root}"] if missing_root else []),
    )

    incomplete = sum(row.get("semantic_status") != "complete" for row in formal)
    unknown = sum(row.get("record_type") == "unknown_candidate" for row in records)
    management = [
        item
        for record in records
        for item in record.get("management_events") or []
    ]
    unclassified_management = sum(
        not item.get("classified_action")
        and item.get("semantic_source") == "unclassified"
        for item in management
    )
    semantics_passed = (
        bool(formal)
        and incomplete == 0
        and unknown == 0
        and unclassified_management == 0
    )
    semantics = _layer(
        semantics_passed,
        {
            "formal_signals": len(formal),
            "complete_formal_signals": len(formal) - incomplete,
            "incomplete_formal_signals": incomplete,
            "unknown_provider_records": unknown,
            "management_events": len(management),
            "unclassified_management_events": unclassified_management,
        },
        ([f"incomplete_formal_signals:{incomplete}"] if incomplete else [])
        + ([f"unknown_provider_records:{unknown}"] if unknown else [])
        + ([f"unclassified_management:{unclassified_management}"]
           if unclassified_management else []),
    )

    uncontrolled_execution = [
        row["pattern_id"]
        for row in patterns
        if row["category"] == "execution"
        and row["severity"] in {"high", "critical"}
        and row["status"] not in {"covered", "dismissed"}
    ]
    execution = _layer(
        not uncontrolled_execution,
        {
            "uncontrolled_material_patterns": len(uncontrolled_execution),
            "covered_patterns": sum(
                row["category"] == "execution" and row["status"] == "covered"
                for row in patterns
            ),
            "regressed_patterns": sum(
                row["category"] == "execution" and row["status"] == "regressed"
                for row in patterns
            ),
        },
        uncontrolled_execution,
    )

    accounting_statuses = Counter(
        str(row.get("status") or "unknown").lower() for row in accounting_rows
    )
    accounting_passed = bool(accounting_rows) and accounting_statuses["exact"] == len(
        accounting_rows)
    accounting = _layer(
        accounting_passed,
        {
            "trades": len(accounting_rows),
            "exact": accounting_statuses["exact"],
            "reconstructed": accounting_statuses["reconstructed"],
            "blocked_or_mismatched": (
                len(accounting_rows)
                - accounting_statuses["exact"]
                - accounting_statuses["reconstructed"]
            ),
        },
        ([] if accounting_rows else ["no_accounting_rows"])
        + ([f"accounting_not_exact:{len(accounting_rows) - accounting_statuses['exact']}"]
           if accounting_rows and accounting_statuses["exact"] != len(accounting_rows)
           else []),
    )

    observed_statuses = Counter(
        str(row.get("status") or "unknown").lower() for row in observed_rows
    )
    market_passed = bool(observed_rows) and observed_statuses["exact"] == len(
        observed_rows)
    market_replay = _layer(
        market_passed,
        {
            "trades": len(observed_rows),
            "exact": observed_statuses["exact"],
            "blocked": observed_statuses["blocked"],
            "mismatch": observed_statuses["mismatch"],
        },
        ([] if observed_rows else ["no_observed_tick_rows"])
        + ([f"market_replay_not_exact:{len(observed_rows) - observed_statuses['exact']}"]
           if observed_rows and observed_statuses["exact"] != len(observed_rows)
           else []),
    )

    validation = strategy_farm.get("validation") or {}
    integrity = validation.get("artifact_integrity_verified") is True
    provenance = _layer(
        integrity,
        {
            "artifact_integrity_verified": validation.get(
                "artifact_integrity_verified"),
            "market_replay_verified": validation.get("market_replay_verified"),
            "farm_mode": validation.get("mode"),
            "replay_rows": len(replay_rows),
        },
        [] if integrity else ["farm_artifact_integrity_not_verified"],
    )

    health = {
        "capture": capture,
        "semantics": semantics,
        "execution": execution,
        "accounting": accounting,
        "market_replay": market_replay,
        "provenance": provenance,
    }
    prerequisite_names = list(health)
    blockers = [name for name in prerequisite_names if not health[name]["passed"]]
    health["strategy_simulation"] = _layer(
        not blockers,
        {
            "hard_layers_passed": len(prerequisite_names) - len(blockers),
            "hard_layers_total": len(prerequisite_names),
        },
        blockers,
    )
    return health


def _review_map(review_metadata: dict | None) -> dict:
    value = review_metadata or {}
    reviews = value.get("reviews", value)
    return reviews if isinstance(reviews, dict) else {}


def _latest_day_delta(patterns: Iterable[dict]) -> dict:
    patterns = list(patterns)
    days = sorted({
        day
        for row in patterns
        for day in (row.get("affected_days") or [])
        if day
    })
    latest_day = days[-1] if days else None
    new_patterns: list[str] = []
    recurring_patterns: list[str] = []
    regressed_patterns: list[str] = []

    if latest_day:
        for row in patterns:
            affected_days = row.get("affected_days") or []
            if latest_day not in affected_days:
                continue
            first_day = _day(row.get("first_seen_utc"))
            if first_day == latest_day:
                new_patterns.append(row["pattern_id"])
            elif first_day and first_day < latest_day:
                recurring_patterns.append(row["pattern_id"])
            if row.get("status") == "regressed":
                regressed_patterns.append(row["pattern_id"])

    new_patterns.sort()
    recurring_patterns.sort()
    regressed_patterns.sort()
    return {
        "evidence_day": latest_day,
        "new_pattern_count": len(new_patterns),
        "recurring_pattern_count": len(recurring_patterns),
        "regressed_pattern_count": len(regressed_patterns),
        "new_patterns": new_patterns,
        "recurring_patterns": recurring_patterns,
        "regressed_patterns": regressed_patterns,
    }


def build_learning_outputs(
    *,
    events: Iterable[dict],
    replay_rows: Iterable[dict],
    accounting_rows: Iterable[dict],
    observed_rows: Iterable[dict],
    provider_catalog: dict,
    strategy_farm: dict | None = None,
    review_metadata: dict | None = None,
) -> LearningOutputs:
    events = list(events)
    replay_rows = list(replay_rows)
    accounting_rows = list(accounting_rows)
    observed_rows = list(observed_rows)
    strategy_farm = dict(strategy_farm or {})
    review_metadata = dict(review_metadata or {})

    observations = collect_normalized_patterns(
        events=events,
        replay_rows=replay_rows,
        accounting_rows=accounting_rows,
        observed_rows=observed_rows,
        provider_catalog=provider_catalog,
    )
    patterns = _aggregate_patterns(observations, review_metadata)
    fingerprints = {
        "events": _fingerprint(events),
        "replay": _fingerprint(replay_rows),
        "accounting": _fingerprint(accounting_rows),
        "observed_ticks": _fingerprint(observed_rows),
        "provider_catalog": _fingerprint(provider_catalog),
        "strategy_farm": _fingerprint(strategy_farm),
        "review_metadata": _fingerprint(_review_map(review_metadata)),
    }
    registry = {
        "schema_version": SCHEMA_VERSION,
        "source_fingerprints": fingerprints,
        "summary": {
            "patterns": len(patterns),
            "observed": sum(row["status"] == "observed" for row in patterns),
            "candidates": sum(row["status"] == "candidate" for row in patterns),
            "covered": sum(row["status"] == "covered" for row in patterns),
            "regressed": sum(row["status"] == "regressed" for row in patterns),
            "dismissed": sum(row["status"] == "dismissed" for row in patterns),
        },
        "patterns": patterns,
    }
    health = build_health(
        provider_catalog=provider_catalog,
        accounting_rows=accounting_rows,
        observed_rows=observed_rows,
        replay_rows=replay_rows,
        strategy_farm=strategy_farm,
        patterns=patterns,
    )
    hard_gate_blockers = [
        name
        for name, layer in health.items()
        if name != "strategy_simulation" and not layer["passed"]
    ]
    safe = not hard_gate_blockers
    candidates = [
        {
            "pattern_id": row["pattern_id"],
            "priority_score": row["priority_score"],
            "severity": row["severity"],
            "candidate_reason": row["candidate_reason"],
            "occurrences": row["occurrences"],
            "affected_days": row["affected_days"],
            "suggested_fixture_id": f"fixture.{row['pattern_id']}",
            "required_promotion_evidence": [
                "deterministic_fixture",
                "regression_test",
                "whole_corpus_shadow_pass",
                "human_review",
            ],
        }
        for row in patterns
        if row["status"] in {"candidate", "regressed"}
    ]
    candidates.sort(key=lambda row: (-row["priority_score"], row["pattern_id"]))
    report = {
        "schema_version": SCHEMA_VERSION,
        "mode": "verified_simulation" if safe else "diagnostic_only",
        "safe_for_strategy_simulation": safe,
        "hard_gate_blockers": hard_gate_blockers,
        "corpus": {
            "event_rows": len(events),
            "replay_trades": len(replay_rows),
            "accounting_trades": len(accounting_rows),
            "observed_tick_trades": len(observed_rows),
            "provider_records": len(provider_catalog.get("signals") or []),
            "source_fingerprints": fingerprints,
        },
        "health": health,
        "learning_flywheel": {
            "patterns_total": len(patterns),
            "candidate_patterns": len(candidates),
            "covered_patterns": sum(row["status"] == "covered" for row in patterns),
            "regressed_patterns": sum(row["status"] == "regressed" for row in patterns),
            "cross_session_patterns": sum(
                row["recurrence"] == "cross_session" for row in patterns),
            "coverage_ratio": (
                round(
                    sum(row["status"] in {"covered", "dismissed"} for row in patterns)
                    / len(patterns),
                    4,
                )
                if patterns else 1.0
            ),
            "latest_day_delta": _latest_day_delta(patterns),
            "promotion_boundary": (
                "logs propose; fixtures, tests, whole-corpus shadow evaluation "
                "and human review promote"
            ),
        },
        "candidate_queue": candidates,
        "registry_fingerprint": _fingerprint(registry),
    }
    registry_bytes = _canonical_bytes(registry)
    report_bytes = _canonical_bytes(report)
    return LearningOutputs(
        report=report,
        registry=registry,
        report_bytes=report_bytes,
        registry_bytes=registry_bytes,
    )


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def write_learning_outputs(
    *,
    output_dir: Path,
    **kwargs,
) -> LearningOutputs:
    outputs = build_learning_outputs(**kwargs)
    _atomic_write(output_dir / DEFAULT_REPORT.name, outputs.report_bytes)
    _atomic_write(output_dir / DEFAULT_REGISTRY.name, outputs.registry_bytes)
    return outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build deterministic recursive reliability evidence")
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--replay", type=Path, default=DEFAULT_REPLAY)
    parser.add_argument("--accounting", type=Path, default=DEFAULT_ACCOUNTING)
    parser.add_argument("--observed", type=Path, default=DEFAULT_OBSERVED)
    parser.add_argument("--provider", type=Path, default=DEFAULT_PROVIDER)
    parser.add_argument("--strategy-farm", type=Path, default=DEFAULT_STRATEGY_FARM)
    parser.add_argument("--reviews", type=Path, default=DEFAULT_REVIEWS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    outputs = build_learning_outputs(
        events=load_jsonl(args.events),
        replay_rows=load_jsonl(args.replay),
        accounting_rows=load_jsonl(args.accounting),
        observed_rows=load_jsonl(args.observed),
        provider_catalog=load_json(args.provider),
        strategy_farm=load_json(args.strategy_farm),
        review_metadata=load_json(args.reviews),
    )
    _atomic_write(args.report, outputs.report_bytes)
    _atomic_write(args.registry, outputs.registry_bytes)
    if not args.quiet:
        print(f"Mode: {outputs.report['mode']}")
        print(f"Patterns: {outputs.registry['summary']['patterns']}")
        print(f"Candidates: {len(outputs.report['candidate_queue'])}")
        print(f"Safe for strategy simulation: "
              f"{outputs.report['safe_for_strategy_simulation']}")
        print(f"Report: {args.report}")
        print(f"Registry: {args.registry}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
