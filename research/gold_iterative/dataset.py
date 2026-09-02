"""Certified Gold Signals BUY/SELL NOW dataset adapter."""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Mapping

from research.dubai_iterative.dataset import (
    SignalScope,
    StrategyDataset,
    TickSource,
    load_strategy_dataset,
)


NOW_TOKEN = re.compile(r"\bNOW\b", re.IGNORECASE)


def load_gold_now_dataset(
    *,
    replay_path: Path,
    audit_path: Path,
    provider_catalog_path: Path,
    raw_events_path: Path,
    market_ticks: TickSource,
    conversion_ticks: TickSource | None,
    money_contract: Mapping[str, Any],
    from_date: str | None = None,
    to_date: str | None = None,
    max_hold_minutes: int = 240,
) -> StrategyDataset:
    catalog_path = Path(provider_catalog_path)
    raw_events_path = Path(raw_events_path)
    scopes = _load_now_scopes(
        catalog_path,
        from_date=from_date,
        to_date=to_date,
    )
    return load_strategy_dataset(
        replay_path=replay_path,
        audit_path=audit_path,
        market_ticks=market_ticks,
        conversion_ticks=conversion_ticks,
        money_contract=money_contract,
        channel="canal2",
        from_date=from_date,
        to_date=to_date,
        max_hold_minutes=max_hold_minutes,
        required_entry_source_kind="telegram_now",
        signal_scopes=scopes,
        audit_reason_prefix="tick_replay_",
        extra_source_paths={
            "provider_catalog": catalog_path,
            "raw_events": raw_events_path,
        },
    )


def _load_now_scopes(
    path: Path,
    *,
    from_date: str | None,
    to_date: str | None,
) -> tuple[SignalScope, ...]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid provider catalog: {path}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("signals"), list):
        raise ValueError("provider catalog must contain a signals list")

    start = date.fromisoformat(from_date) if from_date else date.min
    end = date.fromisoformat(to_date) if to_date else date.max
    scopes: list[tuple[datetime, SignalScope]] = []
    seen_signal_ids: set[str] = set()
    seen_execution_ids: set[str] = set()
    for row in payload["signals"]:
        if not isinstance(row, Mapping):
            continue
        if row.get("channel") != "canal2" or row.get("record_type") != "formal_signal":
            continue
        observed_at = _parse_datetime(
            row.get("signal_ts_utc") or row.get("first_observed_utc")
        )
        if observed_at is None or not start <= observed_at.date() <= end:
            continue
        direction = str(row.get("direction") or "").upper()
        if direction not in {"BUY", "SELL"} or not _has_now_revision(row, direction):
            continue
        signal_id = str(row.get("provider_signal_id") or "")
        if not signal_id:
            raise ValueError("formal Gold NOW signal is missing provider_signal_id")
        if signal_id in seen_signal_ids:
            raise ValueError(f"duplicate provider signal identity: {signal_id}")
        execution_ids = tuple(
            str(value)
            for value in row.get("execution_sig_ids") or ()
            if str(value)
        )
        duplicates = seen_execution_ids.intersection(execution_ids)
        if duplicates:
            duplicate = sorted(duplicates)[0]
            raise ValueError(f"execution signal maps to multiple roots: {duplicate}")
        seen_signal_ids.add(signal_id)
        seen_execution_ids.update(execution_ids)
        scopes.append((
            observed_at,
            SignalScope(
                signal_id=signal_id,
                execution_signal_ids=execution_ids,
                observed_at=observed_at,
            ),
        ))
    return tuple(
        scope
        for _observed_at, scope in sorted(
            scopes,
            key=lambda item: (item[0], item[1].signal_id),
        )
    )


def _has_now_revision(row: Mapping[str, Any], direction: str) -> bool:
    for revision in row.get("revisions") or ():
        if not isinstance(revision, Mapping):
            continue
        text = str(revision.get("text") or "")
        if NOW_TOKEN.search(text) and re.search(rf"\b{direction}\b", text, re.IGNORECASE):
            return True
    return False


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)
