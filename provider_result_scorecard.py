"""Structured, auditable claims from Gold Signals result summaries."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import re
from typing import Any, Mapping


_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


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
        r"\bpips\s+gained\s*([+-]?\s*[\d,]+(?:\.\d+)?)",
        normalized,
    )
    win_rate = _metric(
        r"\bwin\s*rate\s*([\d.]+)\s*%", normalized
    )
    if breakeven is None and all(
        value is not None for value in (signals_sent, wins, losses)
    ):
        remainder = int(signals_sent) - int(wins) - int(losses)
        if remainder == 0:
            breakeven = 0

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


def build_scorecard(catalog: Mapping[str, Any]) -> dict[str, Any]:
    records = catalog.get("signals")
    if not isinstance(records, list):
        records = []
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
        linked = sorted(
            str(record.get("provider_signal_id") or "")
            for record, signal_day in formal_dates
            if start is not None
            and end is not None
            and signal_day is not None
            and start <= signal_day <= end
        )
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
        summaries.append({
            "provider_signal_id": str(record.get("provider_signal_id") or ""),
            "revision_count": revision_count,
            "selected_revision_utc": revision_timestamp,
            "claim": claim,
            "observed_formal_signals": len(linked),
            "observed_signal_ids": linked,
            "signal_count_delta": signal_count_delta,
            "diagnostic_ready": bool(start is not None and end is not None),
        })
    summaries.sort(key=lambda row: (
        str(row["claim"].get("period_start") or ""),
        row["provider_signal_id"],
    ))
    return {
        "schema_version": 1,
        "provider": "Gold Signals",
        "channel": "canal2",
        "summaries": summaries,
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
