"""Structured, auditable claims from Gold Signals result summaries."""

from __future__ import annotations

import base64
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


class ProviderMediaEvidenceError(ValueError):
    """Raised when an image transcription cannot be tied to exact bytes."""


def _parse_utc(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _metric(pattern: str, text: str) -> int | float | None:
    match = re.search(pattern, text, re.IGNORECASE)
    if match is None:
        return None
    raw = match.group(1).replace(",", "").replace(" ", "")
    try:
        value = float(raw)
    except ValueError:
        return None
    return int(value) if value.is_integer() else value


def _short_year(value: str) -> int:
    year = int(value)
    return 2000 + year if year < 100 else year


def _explicit_period(text: str) -> tuple[date, date] | None:
    match = re.search(
        r"(\d{1,2})/(\d{1,2})/(\d{2,4})\s*[-–]\s*"
        r"(\d{1,2})/(\d{1,2})/(\d{2,4})",
        text,
    )
    if match is None:
        return None
    try:
        start = date(
            _short_year(match.group(3)),
            int(match.group(2)),
            int(match.group(1)),
        )
        end = date(
            _short_year(match.group(6)),
            int(match.group(5)),
            int(match.group(4)),
        )
    except ValueError:
        return None
    return (start, end) if start <= end else None


def parse_provider_summary(
    text: str,
    *,
    observed_at_utc: str,
) -> dict[str, Any]:
    normalized = re.sub(r"[*_`]", "", str(text or ""))
    observed = _parse_utc(observed_at_utc)
    blockers: set[str] = set()

    explicit = _explicit_period(normalized)
    weekday_match = re.search(
        r"\b(" + "|".join(_WEEKDAYS) + r")\s+summary\b",
        normalized,
        re.IGNORECASE,
    )
    weekly = bool(re.search(r"\bweekly\s+summary\b", normalized, re.I))
    period_kind = "weekly" if weekly else "daily" if weekday_match else "unknown"
    period_start = None
    period_end = None
    period_basis = None
    if explicit is not None:
        period_start, period_end = explicit
        period_basis = "explicit_text_range"
    elif weekday_match is not None and observed is not None:
        target = _WEEKDAYS[weekday_match.group(1).lower()]
        offset = (observed.weekday() - target) % 7
        period_start = observed.date() - timedelta(days=offset)
        period_end = period_start
        period_basis = "named_weekday_before_observation"
        blockers.add("provider_timezone_unverified")
    else:
        blockers.add("summary_period_unresolved")

    signals_sent = _metric(
        r"\b(\d+)\s+(?:signals?|trades?)\s+sent\b", normalized
    )
    wins = _metric(
        r"\b(\d+)\s+(?:wins?|winning\s+trades?)\b", normalized
    )
    losses = _metric(
        r"\b(\d+)\s+(?:loss(?:es)?|stop\s+loss(?:es)?)\b", normalized
    )
    breakeven = _metric(
        r"\b(\d+)\s+(?:b\s*/\s*e|break\s*even)\b", normalized
    )
    pips_gained = _metric(
        r"\bpips(?:\s+gained)?\s*([+-]?\s*[\d,]+(?:\.\d+)?)",
        normalized,
    )
    win_rate = _metric(
        r"\bwin\s*rate\s*([\d.]+)\s*%", normalized
    )
    inferred_metrics = []
    outcomes = {
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
    }
    if signals_sent is not None:
        missing = [name for name, value in outcomes.items() if value is None]
        known_total = sum(
            int(value) for value in outcomes.values() if value is not None
        )
        remainder = int(signals_sent) - known_total
        if remainder >= 0 and (
            len(missing) == 1 or (missing and remainder == 0)
        ):
            for name in missing:
                outcomes[name] = remainder if len(missing) == 1 else 0
                inferred_metrics.append(name)
    wins = outcomes["wins"]
    losses = outcomes["losses"]
    breakeven = outcomes["breakeven"]

    required = (signals_sent, wins, losses, pips_gained)
    if any(value is None for value in required):
        blockers.add("summary_metrics_incomplete")
    arithmetic_consistent = None
    if all(value is not None for value in (signals_sent, wins, losses)):
        arithmetic_consistent = (
            int(wins) + int(losses) + int(breakeven or 0)
            == int(signals_sent)
        )
        if not arithmetic_consistent:
            blockers.add("summary_arithmetic_inconsistent")
    if (
        win_rate is not None
        and signals_sent is not None
        and wins is not None
        and int(signals_sent) > 0
    ):
        expected_win_rate = (float(wins) / float(signals_sent)) * 100.0
        if abs(float(win_rate) - expected_win_rate) > 0.5:
            blockers.add("summary_win_rate_inconsistent")
    partial = bool(re.search(r"\bso\s+far\b|\bwant\s+more\b", normalized, re.I))
    if partial:
        blockers.add("summary_marked_partial")

    return {
        "period_kind": period_kind,
        "period_start": None if period_start is None else period_start.isoformat(),
        "period_end": None if period_end is None else period_end.isoformat(),
        "period_basis": period_basis,
        "signals_sent": signals_sent,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "pips_gained": pips_gained,
        "win_rate_percent": win_rate,
        "potential_wording": bool(re.search(r"\bpotential\s+pips\b", normalized, re.I)),
        "inferred_metrics": sorted(inferred_metrics),
        "partial": partial,
        "arithmetic_consistent": arithmetic_consistent,
        "calibration_ready": not blockers,
        "blockers": sorted(blockers),
    }


def _latest_revision(record: Mapping[str, Any]) -> Mapping[str, Any] | None:
    revisions = record.get("revisions")
    if not isinstance(revisions, list):
        return None
    candidates = [
        (index, revision)
        for index, revision in enumerate(revisions)
        if isinstance(revision, Mapping) and str(revision.get("text") or "").strip()
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            _parse_utc(
                item[1].get("telegram_ts_utc")
                or item[1].get("observed_ts_utc")
            ) or datetime.min.replace(tzinfo=timezone.utc),
            item[0],
        ),
    )[1]


def _formal_signal_date(record: Mapping[str, Any]) -> date | None:
    contract = record.get("entry_contract")
    value = None
    if isinstance(contract, Mapping):
        value = (
            contract.get("trigger_telegram_utc")
            or contract.get("trigger_observed_utc")
        )
    observed = _parse_utc(value or record.get("first_observed_utc"))
    return None if observed is None else observed.date()


def load_hash_bound_media_summaries(
    annotations_path: Path,
    media_evidence_path: Path,
) -> tuple[dict[str, Any], ...]:
    """Load visual transcriptions only when their archived bytes still match."""

    annotations_document = json.loads(
        Path(annotations_path).read_text(encoding="utf-8")
    )
    if annotations_document.get("schema_version") != 1:
        raise ProviderMediaEvidenceError("unsupported annotation schema")
    channel = str(annotations_document.get("channel") or "")
    if not channel:
        raise ProviderMediaEvidenceError("annotation channel is required")
    annotations = annotations_document.get("annotations")
    if not isinstance(annotations, list):
        raise ProviderMediaEvidenceError("annotations must be a list")

    normalized_annotations = []
    message_ids: set[int] = set()
    wanted: set[tuple[str, int, str, str]] = set()
    for raw in annotations:
        if not isinstance(raw, Mapping):
            raise ProviderMediaEvidenceError("annotation must be an object")
        try:
            message_id = int(raw["message_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderMediaEvidenceError(
                "annotation message_id is invalid"
            ) from exc
        revision_id = str(raw.get("message_revision_id") or "").strip()
        digest = str(raw.get("media_sha256") or "").strip().lower()
        text = str(raw.get("transcribed_text") or "").strip()
        method = str(raw.get("transcription_method") or "").strip()
        if message_id in message_ids:
            raise ProviderMediaEvidenceError(
                f"duplicate annotation message_id: {message_id}"
            )
        if not revision_id or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ProviderMediaEvidenceError(
                f"annotation identity is incomplete: {message_id}"
            )
        if not text or not method:
            raise ProviderMediaEvidenceError(
                f"annotation transcription is incomplete: {message_id}"
            )
        key = (channel, message_id, revision_id, digest)
        message_ids.add(message_id)
        wanted.add(key)
        normalized_annotations.append((raw, key, text, method))

    evidence_by_key: dict[tuple[str, int, str, str], Mapping[str, Any]] = {}
    with Path(media_evidence_path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ProviderMediaEvidenceError(
                    f"invalid media evidence JSON at line {line_number}"
                ) from exc
            try:
                key = (
                    str(row.get("channel") or ""),
                    int(row.get("message_id")),
                    str(row.get("message_revision_id") or ""),
                    str(row.get("sha256") or "").lower(),
                )
            except (TypeError, ValueError):
                continue
            if key not in wanted:
                continue
            if key in evidence_by_key:
                raise ProviderMediaEvidenceError(
                    f"duplicate media evidence: {key[1]}"
                )
            evidence_by_key[key] = row

    records = []
    for _raw, key, text, method in normalized_annotations:
        row = evidence_by_key.get(key)
        if row is None:
            raise ProviderMediaEvidenceError(
                f"media evidence not found: {key[1]}"
            )
        if row.get("payload_encoding") != "base64":
            raise ProviderMediaEvidenceError(
                f"unsupported media payload encoding: {key[1]}"
            )
        try:
            payload = base64.b64decode(
                str(row.get("payload_base64") or ""),
                validate=True,
            )
        except (ValueError, TypeError) as exc:
            raise ProviderMediaEvidenceError(
                f"invalid media payload: {key[1]}"
            ) from exc
        payload_digest = hashlib.sha256(payload).hexdigest()
        if payload_digest != key[3]:
            raise ProviderMediaEvidenceError(
                f"payload hash mismatch: {key[1]}"
            )
        if len(payload) != int(row.get("size_bytes") or -1):
            raise ProviderMediaEvidenceError(
                f"payload size mismatch: {key[1]}"
            )
        captured_at = str(row.get("captured_at_utc") or "")
        if _parse_utc(captured_at) is None:
            raise ProviderMediaEvidenceError(
                f"capture timestamp is invalid: {key[1]}"
            )
        media_evidence = {
            "message_id": key[1],
            "message_revision_id": key[2],
            "sha256": key[3],
            "size_bytes": len(payload),
            "captured_at_utc": captured_at,
            "mime_type": row.get("mime_type"),
            "transcription_method": method,
            "payload_sha256_verified": True,
        }
        records.append({
            "provider_signal_id": (
                f"{channel}_media_summary_{key[1]}_{key[3][:12]}"
            ),
            "channel": channel,
            "record_type": "daily_summary",
            "first_observed_utc": captured_at,
            "source_message_ids": [key[1]],
            "revisions": [{
                "telegram_ts_utc": captured_at,
                "observed_ts_utc": captured_at,
                "text": text,
            }],
            "media_evidence": media_evidence,
        })
    return tuple(records)


def build_scorecard(
    catalog: Mapping[str, Any],
    *,
    supplemental_records: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    records = catalog.get("signals")
    if not isinstance(records, list):
        records = []
    records = [
        *records,
        *(record for record in supplemental_records if isinstance(record, Mapping)),
    ]
    formal = [
        record
        for record in records
        if isinstance(record, Mapping)
        and record.get("channel") == "canal2"
        and record.get("record_type") == "formal_signal"
    ]
    formal_dates = [
        (record, _formal_signal_date(record)) for record in formal
    ]
    summaries = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        if (
            record.get("channel") != "canal2"
            or record.get("record_type") != "daily_summary"
        ):
            continue
        revision = _latest_revision(record)
        revision_count = len(record.get("revisions") or [])
        if revision is None:
            claim = parse_provider_summary(
                "",
                observed_at_utc=str(record.get("first_observed_utc") or ""),
            )
            blockers = sorted(set(claim["blockers"]) | {"summary_text_missing"})
            claim = {**claim, "calibration_ready": False, "blockers": blockers}
            revision_timestamp = None
        else:
            revision_timestamp = str(
                revision.get("telegram_ts_utc")
                or revision.get("observed_ts_utc")
                or record.get("first_observed_utc")
                or ""
            )
            claim = parse_provider_summary(
                str(revision.get("text") or ""),
                observed_at_utc=revision_timestamp,
            )
        start = (
            date.fromisoformat(claim["period_start"])
            if claim["period_start"]
            else None
        )
        end = (
            date.fromisoformat(claim["period_end"])
            if claim["period_end"]
            else None
        )
        linked_records = [
            formal_record
            for formal_record, signal_day in formal_dates
            if start is not None
            and end is not None
            and signal_day is not None
            and start <= signal_day <= end
        ]
        linked_id_values = [
            str(formal_record.get("provider_signal_id") or "")
            for formal_record in linked_records
        ]
        linked = sorted({value for value in linked_id_values if value})
        duplicate_ids = sorted({
            value
            for value in linked_id_values
            if value and linked_id_values.count(value) > 1
        })
        missing_identity = any(not value for value in linked_id_values)
        claimed_count = claim.get("signals_sent")
        signal_count_delta = (
            None
            if claimed_count is None
            else len(linked) - int(claimed_count)
        )
        if signal_count_delta not in {None, 0}:
            claim_blockers = sorted(
                set(claim.get("blockers") or ())
                | {"provider_signal_count_mismatch"}
            )
            claim = {
                **claim,
                "calibration_ready": False,
                "blockers": claim_blockers,
            }
        identity_blockers = set(claim.get("blockers") or ())
        if duplicate_ids:
            identity_blockers.add("provider_signal_identity_duplicate")
        if missing_identity:
            identity_blockers.add("provider_signal_identity_missing")
        if identity_blockers != set(claim.get("blockers") or ()):
            claim = {
                **claim,
                "calibration_ready": False,
                "blockers": sorted(identity_blockers),
            }
        summary = {
            "provider_signal_id": str(record.get("provider_signal_id") or ""),
            "revision_count": revision_count,
            "selected_revision_utc": revision_timestamp,
            "claim": claim,
            "observed_formal_signals": len(linked),
            "observed_signal_ids": linked,
            "duplicate_signal_ids": duplicate_ids,
            "signal_count_delta": signal_count_delta,
            "diagnostic_ready": bool(start is not None and end is not None),
        }
        media_evidence = record.get("media_evidence")
        if isinstance(media_evidence, Mapping):
            summary["media_evidence"] = dict(media_evidence)
        summaries.append(summary)
    summaries.sort(key=lambda row: (
        str(row["claim"].get("period_start") or ""),
        row["provider_signal_id"],
    ))
    return {
        "schema_version": 1,
        "provider": "Gold Signals",
        "channel": "canal2",
        "summaries": summaries,
        "period_consistency": _period_consistency(summaries),
        "summary": {
            "records": len(summaries),
            "calibration_ready": sum(
                int(row["claim"]["calibration_ready"]) for row in summaries
            ),
            "blocked": sum(
                int(not row["claim"]["calibration_ready"])
                for row in summaries
            ),
        },
    }


def _period_consistency(
    summaries: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    daily_by_day: dict[str, list[Mapping[str, Any]]] = {}
    weekly = []
    for row in summaries:
        claim = row.get("claim")
        if not isinstance(claim, Mapping):
            continue
        kind = claim.get("period_kind")
        start = str(claim.get("period_start") or "")
        end = str(claim.get("period_end") or "")
        if kind == "daily" and start and start == end:
            daily_by_day.setdefault(start, []).append(row)
        elif kind == "weekly" and start and end:
            weekly.append(row)

    rows = []
    metric_names = (
        "signals_sent",
        "wins",
        "losses",
        "breakeven",
        "pips_gained",
    )
    for weekly_row in weekly:
        claim = weekly_row["claim"]
        start = date.fromisoformat(str(claim["period_start"]))
        end = date.fromisoformat(str(claim["period_end"]))
        expected_days = []
        cursor = start
        while cursor <= end:
            if cursor.weekday() < 5:
                expected_days.append(cursor.isoformat())
            cursor += timedelta(days=1)
        day_rows = {
            day: daily_by_day.get(day, []) for day in expected_days
        }
        complete_coverage = all(len(items) == 1 for items in day_rows.values())
        blockers = []
        if not complete_coverage:
            blockers.append("incomplete_daily_coverage")

        daily_totals: dict[str, int | float] = {}
        if complete_coverage:
            selected = [day_rows[day][0]["claim"] for day in expected_days]
            complete_metrics: dict[str, int | float] = {}
            for metric in metric_names:
                values = [item.get(metric) for item in selected]
                if any(value is None for value in values):
                    blockers.append("daily_metrics_incomplete")
                    complete_metrics = {}
                    break
                complete_metrics[metric] = sum(values)
            daily_totals = complete_metrics
        weekly_totals = {
            metric: claim.get(metric) for metric in metric_names
        }
        if any(value is None for value in weekly_totals.values()):
            blockers.append("weekly_metrics_incomplete")

        outcome_accounting = "unresolved"
        pips_delta = None
        if daily_totals and not any(
            value is None for value in weekly_totals.values()
        ):
            if daily_totals["signals_sent"] != weekly_totals["signals_sent"]:
                blockers.append("weekly_signal_count_differs_from_daily")
            exact_outcomes = all(
                daily_totals[name] == weekly_totals[name]
                for name in ("wins", "losses", "breakeven")
            )
            breakeven_as_win = (
                daily_totals["wins"] + daily_totals["breakeven"]
                == weekly_totals["wins"]
                and daily_totals["losses"] == weekly_totals["losses"]
                and weekly_totals["breakeven"] == 0
            )
            if exact_outcomes:
                outcome_accounting = "exact_daily_outcomes"
            elif breakeven_as_win:
                outcome_accounting = "breakeven_counted_as_win"
            else:
                blockers.append("weekly_outcome_accounting_unresolved")
            pips_delta = (
                daily_totals["pips_gained"]
                - weekly_totals["pips_gained"]
            )
            if pips_delta != 0:
                blockers.append("weekly_pips_differ_from_daily")

        rows.append({
            "weekly_provider_signal_id": weekly_row.get("provider_signal_id"),
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "expected_daily_periods": expected_days,
            "daily_summary_ids": [
                str(day_rows[day][0].get("provider_signal_id") or "")
                for day in expected_days
                if len(day_rows[day]) == 1
            ],
            "complete_daily_coverage": complete_coverage,
            "daily_totals": daily_totals,
            "weekly_totals": weekly_totals,
            "pips_daily_minus_weekly": pips_delta,
            "outcome_accounting": outcome_accounting,
            "blockers": list(dict.fromkeys(blockers)),
        })
    return rows
