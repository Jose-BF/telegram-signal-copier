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
    return _load_gold_dataset(
        replay_path=replay_path,
        audit_path=audit_path,
        provider_catalog_path=provider_catalog_path,
        raw_events_path=raw_events_path,
        market_ticks=market_ticks,
        conversion_ticks=conversion_ticks,
        money_contract=money_contract,
        from_date=from_date,
        to_date=to_date,
        max_hold_minutes=max_hold_minutes,
        signal_scope="now",
    )


def load_gold_direct_dataset(
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
    """Load NOW plus explicit priced direct entries; zone plans stay excluded."""

    return _load_gold_dataset(
        replay_path=replay_path,
        audit_path=audit_path,
        provider_catalog_path=provider_catalog_path,
        raw_events_path=raw_events_path,
        market_ticks=market_ticks,
        conversion_ticks=conversion_ticks,
        money_contract=money_contract,
        from_date=from_date,
        to_date=to_date,
        max_hold_minutes=max_hold_minutes,
        signal_scope="direct",
    )


def _load_gold_dataset(
    *,
    replay_path: Path,
    audit_path: Path,
    provider_catalog_path: Path,
    raw_events_path: Path,
    market_ticks: TickSource,
    conversion_ticks: TickSource | None,
    money_contract: Mapping[str, Any],
    from_date: str | None,
    to_date: str | None,
    max_hold_minutes: int,
    signal_scope: str,
) -> StrategyDataset:
    catalog_path = Path(provider_catalog_path)
    raw_events_path = Path(raw_events_path)
    scopes = _load_scopes(
        catalog_path,
        from_date=from_date,
        to_date=to_date,
        signal_scope=signal_scope,
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
        required_entry_source_kind=(
            "telegram_now" if signal_scope == "now" else None
        ),
        signal_scopes=scopes,
        audit_reason_prefix="tick_replay_",
        extra_source_paths={
            "provider_catalog": catalog_path,
            "raw_events": raw_events_path,
        },
    )


def _load_scopes(
    path: Path,
    *,
    from_date: str | None,
    to_date: str | None,
    signal_scope: str,
) -> tuple[SignalScope, ...]:
    if signal_scope not in {"now", "direct"}:
        raise ValueError(f"unsupported Gold signal scope: {signal_scope}")
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
        is_now = _has_now_revision(row, direction)
        is_direct_priced = str(
            (row.get("entry_contract") or {}).get("trigger_kind") or ""
        ).startswith("direct_priced_")
        if direction not in {"BUY", "SELL"}:
            continue
        if signal_scope == "now" and not is_now:
            continue
        if signal_scope == "direct" and not (is_now or is_direct_priced):
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
                provider_trade=_provider_trade(row, observed_at),
            ),
        ))
    return tuple(
        scope
        for _observed_at, scope in sorted(
            scopes,
            key=lambda item: (item[0], item[1].signal_id),
        )
    )


def _provider_trade(
    row: Mapping[str, Any],
    observed_at: datetime,
) -> Mapping[str, Any]:
    """Compile one formal NOW signal into an execution-independent template."""

    entry_contract = row.get("entry_contract") or {}
    trigger = _parse_datetime(
        entry_contract.get("trigger_telegram_utc")
        or row.get("signal_ts_utc")
    ) or observed_at
    direction = str(row.get("direction") or "").upper()
    level_rows = _provider_level_rows(row, trigger)
    target_count = max(
        [len(item[1]) for item in level_rows]
        + [len(row.get("effective_tps") or ()), 1]
    )
    effective_range = [
        float(value)
        for value in row.get("effective_range") or ()
        if _positive_number(value) is not None
    ]
    template_price = (
        sum(effective_range) / len(effective_range)
        if effective_range
        else 1.0
    )
    tickets = []
    for index in range(target_count):
        tp_history = [
            {
                "ts": timestamp.isoformat(),
                "tp": targets[index],
                "status": "confirmed",
                "source": "provider_catalog_telegram",
            }
            for timestamp, targets, _stop in level_rows
            if index < len(targets)
        ]
        sl_history = [
            {
                "ts": timestamp.isoformat(),
                "sl": stop,
                "status": "confirmed",
                "source": "provider_catalog_telegram",
            }
            for timestamp, _targets, stop in level_rows
            if stop is not None
        ]
        tickets.append({
            "ticket": f"provider_template_{index + 1}",
            "role": f"provider_target_{index + 1}",
            "volume": 0.01,
            "open_dt_utc": trigger.isoformat(),
            "open_price": template_price,
            "pnl_net": None,
            "tp_history": tp_history,
            "sl_history": sl_history,
        })

    management = []
    for event in row.get("management_events") or ():
        if not isinstance(event, Mapping):
            continue
        event_time = _parse_datetime(
            event.get("telegram_ts_utc")
            or event.get("observed_ts_utc")
        )
        if event_time is None:
            continue
        normalized = dict(event)
        normalized["captured_observed_ts_utc"] = event.get(
            "observed_ts_utc"
        )
        normalized["observed_ts_utc"] = event_time.isoformat()
        management.append(normalized)

    return {
        "sig_id": str(row.get("provider_signal_id") or ""),
        "channel": "canal2",
        "direction": direction,
        "signal_dt_utc": trigger.isoformat(),
        "entry_evidence_kind": "provider_telegram",
        "entry_provenance": {"source_kind": "provider_telegram"},
        "tickets": tickets,
        "management": management,
    }


def _provider_level_rows(
    row: Mapping[str, Any],
    trigger: datetime,
) -> tuple[tuple[datetime, tuple[float, ...], float | None], ...]:
    candidates = list(row.get("level_timeline") or ())
    if not candidates:
        candidates = [
            {
                "telegram_ts_utc": revision.get("telegram_ts_utc"),
                "observed_ts_utc": revision.get("observed_ts_utc"),
                "tps": (revision.get("parsed") or {}).get("tps"),
                "sl": (revision.get("parsed") or {}).get("sl"),
            }
            for revision in row.get("revisions") or ()
            if isinstance(revision, Mapping)
        ]
    rows: list[tuple[datetime, tuple[float, ...], float | None]] = []
    seen: set[tuple[datetime, tuple[float, ...], float | None]] = set()
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        timestamp = _parse_datetime(
            candidate.get("telegram_ts_utc")
            or candidate.get("observed_ts_utc")
        )
        if timestamp is None:
            continue
        targets = tuple(
            value
            for raw in candidate.get("tps") or ()
            if (value := _positive_number(raw)) is not None
        )
        stop = _positive_number(candidate.get("sl"))
        identity = (timestamp, targets, stop)
        if (targets or stop is not None) and identity not in seen:
            seen.add(identity)
            rows.append(identity)
    if not rows:
        targets = tuple(
            value
            for raw in row.get("effective_tps") or ()
            if (value := _positive_number(raw)) is not None
        )
        stop = _positive_number(row.get("effective_sl"))
        if targets or stop is not None:
            rows.append((trigger, targets, stop))
    return tuple(sorted(rows, key=lambda item: item[0]))


def _positive_number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


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
