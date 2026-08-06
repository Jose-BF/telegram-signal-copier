from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from math import isfinite
from pathlib import Path

import numpy as np
import pandas as pd

from broker_money import BrokerMoneyConverter, load_contract
from canal2_zone_lifecycle import classify_followup
from provider_zone_simulator import DEPTH_AUDIT_FRACTIONS, simulate_zone_policy
from provider_zone_spec import ProviderZoneSpec, build_zone_trade_spec
from simulation_oracle import IndependentTickCache, prepare_tick_window
from zone_entry_policies import (
    ZoneEntryPolicy,
    default_zone_entry_policies,
)
from zone_fill_auditor import audit_zone_depths


SCHEMA_VERSION = 1
BASELINE_POLICY_ID = "current_live_zone_trigger"
RISK_REFERENCE_VOLUME = 0.05


def _utc(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _date(value: object) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            parsed = _utc(value)
            return parsed.date() if parsed else None
    parsed = _utc(value)
    return parsed.date() if parsed else None


def _record_day(record: Mapping[str, object]) -> date | None:
    for field in ("signal_ts_utc", "first_observed_utc"):
        parsed = _date(record.get(field))
        if parsed is not None:
            return parsed
    return None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line_number, line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL line {line_number}: {path}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"JSONL line {line_number} is not an object: {path}")
        rows.append(row)
    return rows


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stable_strings(values: Iterable[object]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value)))


def _blocked_policy_row(
    spec: ProviderZoneSpec,
    policy: ZoneEntryPolicy,
    blockers: Iterable[object],
    record_day: date | None,
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "provider_signal_id": spec.provider_signal_id,
        "channel": spec.channel,
        "signal_date": record_day.isoformat() if record_day else None,
        "ready_at_utc": (
            spec.ready_at_utc.isoformat() if spec.ready_at_utc else None
        ),
        "policy_id": policy.policy_id,
        "status": "blocked",
        "blockers": _stable_strings(blockers),
        "warnings": list(spec.warnings),
        "source_sha256": spec.source_sha256,
        "planned_leg_count": len(policy.depth_fractions),
        "filled_leg_count": 0,
        "planned_volume": policy.total_planned_volume,
        "filled_volume": 0.0,
        "average_fill_price": None,
        "result_unit": "xauusd_price_lots",
        "strategy_value": None,
        "money_status": "not_applicable",
        "strategy_pnl": None,
        "pnl_currency": None,
        "profit_currency_pnl": None,
        "money_blockers": [],
        "zone_diagnostics": None,
        "basket_excursions": None,
        "filled_legs": [],
        "unfilled_legs": [],
        "audit_status": "not_applicable",
        "audit": None,
        "modeled_baseline_proofs": [],
        "observed_execution_proofs": [],
    }


def validate_observed_baseline(
    simulated_row: Mapping[str, object],
    execution_batch: Mapping[str, object],
    *,
    time_tolerance_ms: int = 3000,
    price_tolerance: float = 1.0,
) -> dict:
    simulated = list(simulated_row.get("filled_legs") or [])
    actual = list(execution_batch.get("fills") or [])
    simulated.sort(key=lambda row: str(row.get("open_time_utc") or ""))
    actual.sort(key=lambda row: str(row.get("observed_utc") or ""))
    blockers: list[str] = []
    time_deltas: list[int] = []
    price_deltas: list[float] = []
    for index, (expected, observed) in enumerate(zip(simulated, actual)):
        expected_time = _utc(expected.get("open_time_utc"))
        observed_time = _utc(observed.get("observed_utc"))
        try:
            expected_price = float(expected.get("open_price"))
            observed_price = float(observed.get("price"))
        except (TypeError, ValueError):
            expected_price = float("nan")
            observed_price = float("nan")
        if expected_time is None or observed_time is None:
            blockers.append(f"invalid_baseline_fill_time:{index}")
        else:
            time_deltas.append(int(round(abs(
                (observed_time - expected_time).total_seconds() * 1000
            ))))
        if not isfinite(expected_price) or not isfinite(observed_price):
            blockers.append(f"invalid_baseline_fill_price:{index}")
        else:
            price_deltas.append(abs(observed_price - expected_price))
    count_match = len(simulated) == len(actual)
    max_time = max(time_deltas, default=None)
    max_price = max(price_deltas, default=None)
    within = (
        count_match
        and not blockers
        and max_time is not None
        and max_price is not None
        and max_time <= time_tolerance_ms
        and max_price <= price_tolerance
    )
    return {
        "status": "verified" if within else "mismatch",
        "within_tolerance": within,
        "actual_fill_count": len(actual),
        "simulated_fill_count": len(simulated),
        "fill_count_match": count_match,
        "time_tolerance_ms": time_tolerance_ms,
        "price_tolerance": price_tolerance,
        "max_time_delta_ms": max_time,
        "max_price_delta": (
            round(max_price, 8) if max_price is not None else None
        ),
        "blockers": _stable_strings(blockers),
        "execution_batch_id": execution_batch.get("execution_batch_id"),
    }


def _observed_trigger_utc(
    provenance: Mapping[str, object],
    verified_utc_offset_seconds: int | None,
) -> datetime | None:
    normalized = _utc(provenance.get("zone_trigger_normalized_utc"))
    if normalized is not None:
        return normalized
    raw_msc = provenance.get("zone_trigger_time_msc")
    if (
        isinstance(raw_msc, bool)
        or not isinstance(raw_msc, (int, float))
        or isinstance(verified_utc_offset_seconds, bool)
        or not isinstance(verified_utc_offset_seconds, int)
    ):
        return None
    try:
        return (
            datetime.fromtimestamp(float(raw_msc) / 1000.0, timezone.utc)
            - timedelta(seconds=verified_utc_offset_seconds)
        )
    except (OverflowError, OSError, ValueError):
        return None


def _expected_provider_active(
    spec: ProviderZoneSpec,
    observed_trigger: datetime | None,
) -> datetime | None:
    candidates: list[datetime] = []
    for event in spec.management_events:
        observed = _utc(event.get("observed_ts_utc"))
        if observed is None:
            continue
        action = str(event.get("classified_action") or "").upper()
        lifecycle = classify_followup(str(event.get("text") or ""))
        if action in {"ACTIVATE", "ACTIVATE_ZONE"} or "ACTIVATE" in lifecycle:
            candidates.append(observed)
    if not candidates:
        return None
    if observed_trigger is None:
        return min(candidates)
    return min(candidates, key=lambda value: abs(value - observed_trigger))


def audit_observed_zone_execution(
    spec: ProviderZoneSpec,
    execution_batch: Mapping[str, object],
    ticks: pd.DataFrame,
    *,
    zone_audit: Mapping[str, object],
    verified_utc_offset_seconds: int | None,
    expected_fill_count: int = 5,
    trigger_tolerance_ms: int = 1500,
    fill_tick_tolerance_ms: int = 250,
    fill_price_tolerance: float = 1.0,
    execution_window_ms: int = 5000,
) -> dict:
    blockers: list[str] = []
    provenance = execution_batch.get("entry_provenance")
    if not isinstance(provenance, Mapping):
        provenance = {}
        blockers.append("missing_execution_entry_provenance")
    trigger_kind = str(provenance.get("zone_trigger_kind") or "")
    observed_trigger = _observed_trigger_utc(
        provenance,
        verified_utc_offset_seconds,
    )
    expected_trigger: datetime | None = None
    if trigger_kind == "first_touch":
        first_by_depth = zone_audit.get("first_touch_by_depth")
        expected_trigger = _utc(zone_audit.get("first_touch_utc"))
        if expected_trigger is None and isinstance(first_by_depth, Mapping):
            expected_trigger = _utc(first_by_depth.get("0.0"))
        if expected_trigger is None:
            blockers.append("missing_independent_first_touch")
    elif trigger_kind == "explicit_active":
        expected_trigger = _expected_provider_active(spec, observed_trigger)
        if expected_trigger is None:
            blockers.append("missing_provider_active_event")
    else:
        blockers.append(f"unsupported_execution_trigger:{trigger_kind or 'missing'}")
    if observed_trigger is None:
        blockers.append("missing_normalized_execution_trigger")
    trigger_delta_ms = (
        int(round(abs((observed_trigger - expected_trigger).total_seconds()) * 1000))
        if observed_trigger is not None and expected_trigger is not None
        else None
    )
    if trigger_delta_ms is not None and trigger_delta_ms > trigger_tolerance_ms:
        blockers.append("execution_trigger_outside_tolerance")

    prepared, tick_blockers = prepare_tick_window(ticks)
    if tick_blockers or prepared is None:
        blockers.extend(tick_blockers or ["invalid_execution_audit_ticks"])
    fills = list(execution_batch.get("fills") or [])
    if len(fills) != expected_fill_count:
        blockers.append("observed_execution_fill_count_mismatch")
    direction = spec.ready_states[0].direction if spec.ready_states else ""
    if direction not in {"BUY", "SELL"}:
        blockers.append("missing_execution_audit_direction")
    fill_details: list[dict] = []
    fill_times: list[datetime] = []
    if prepared is not None and direction in {"BUY", "SELL"}:
        quote_values = prepared.ask if direction == "BUY" else prepared.bid
        quote_side = "ask" if direction == "BUY" else "bid"
        for index, fill in enumerate(fills):
            observed = _utc(fill.get("observed_utc"))
            try:
                fill_price = float(fill.get("price"))
            except (TypeError, ValueError):
                fill_price = float("nan")
            if observed is None or not isfinite(fill_price):
                blockers.append(f"invalid_observed_execution_fill:{index}")
                continue
            fill_times.append(observed)
            observed_ns = pd.Timestamp(observed).value
            insertion = int(np.searchsorted(
                prepared.times_ns,
                observed_ns,
                side="left",
            ))
            candidates = [
                candidate
                for candidate in (insertion - 1, insertion)
                if 0 <= candidate < len(prepared.times_ns)
            ]
            if not candidates:
                blockers.append(f"missing_tick_near_execution_fill:{index}")
                continue
            tick_index = min(
                candidates,
                key=lambda candidate: abs(
                    int(prepared.times_ns[candidate]) - observed_ns
                ),
            )
            tick_delta_ms = int(round(abs(
                int(prepared.times_ns[tick_index]) - observed_ns
            ) / 1_000_000))
            quote = float(quote_values[tick_index])
            price_delta = round(abs(fill_price - quote), 8)
            if tick_delta_ms > fill_tick_tolerance_ms:
                blockers.append(f"execution_fill_tick_too_far:{index}")
            if price_delta > fill_price_tolerance:
                blockers.append(f"execution_fill_price_outside_tolerance:{index}")
            fill_details.append({
                "fill_index": index,
                "observed_utc": observed.isoformat(),
                "fill_price": fill_price,
                "quote_side": quote_side,
                "tick_utc": pd.Timestamp(
                    int(prepared.times_ns[tick_index]),
                    unit="ns",
                    tz="UTC",
                ).to_pydatetime().isoformat(),
                "tick_quote": quote,
                "tick_delta_ms": tick_delta_ms,
                "price_delta": price_delta,
            })

    signal_received = _utc(execution_batch.get("signal_received_utc"))
    trigger_to_signal_ms = None
    signal_to_first_fill_ms = None
    trigger_to_last_fill_ms = None
    if observed_trigger is not None and signal_received is not None:
        trigger_to_signal_ms = int(round(
            (signal_received - observed_trigger).total_seconds() * 1000
        ))
        if trigger_to_signal_ms < 0:
            blockers.append("signal_received_before_observed_trigger")
    elif signal_received is None:
        blockers.append("missing_execution_signal_received_time")
    if fill_times:
        fill_times.sort()
        if signal_received is not None:
            signal_to_first_fill_ms = int(round(
                (fill_times[0] - signal_received).total_seconds() * 1000
            ))
            if signal_to_first_fill_ms < 0:
                blockers.append("execution_fill_before_signal_received")
        if observed_trigger is not None:
            trigger_to_last_fill_ms = int(round(
                (fill_times[-1] - observed_trigger).total_seconds() * 1000
            ))
            if not 0 <= trigger_to_last_fill_ms <= execution_window_ms:
                blockers.append("execution_batch_outside_time_window")

    stable_blockers = _stable_strings(blockers)
    return {
        "status": "verified" if not stable_blockers else "blocked",
        "within_tolerance": not stable_blockers,
        "execution_batch_id": execution_batch.get("execution_batch_id"),
        "provider_signal_id": spec.provider_signal_id,
        "trigger_kind": trigger_kind or None,
        "observed_trigger_utc": (
            observed_trigger.isoformat() if observed_trigger else None
        ),
        "expected_trigger_utc": (
            expected_trigger.isoformat() if expected_trigger else None
        ),
        "trigger_delta_ms": trigger_delta_ms,
        "trigger_tolerance_ms": trigger_tolerance_ms,
        "fill_count": len(fills),
        "expected_fill_count": expected_fill_count,
        "fill_tick_tolerance_ms": fill_tick_tolerance_ms,
        "fill_price_tolerance": fill_price_tolerance,
        "max_fill_tick_delta_ms": max(
            (row["tick_delta_ms"] for row in fill_details),
            default=None,
        ),
        "max_fill_price_delta": max(
            (row["price_delta"] for row in fill_details),
            default=None,
        ),
        "trigger_to_signal_ms": trigger_to_signal_ms,
        "signal_to_first_fill_ms": signal_to_first_fill_ms,
        "trigger_to_last_fill_ms": trigger_to_last_fill_ms,
        "execution_window_ms": execution_window_ms,
        "fills": fill_details,
        "blockers": stable_blockers,
    }


def _maximum_drawdown(values: Sequence[float]) -> float:
    equity = 0.0
    peak = 0.0
    maximum = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        maximum = max(maximum, peak - equity)
    return round(maximum, 2)


def _risk_normalized_pnl(row: Mapping[str, object]) -> float | None:
    try:
        value = float(row["strategy_pnl"])
        planned_volume = float(row["planned_volume"])
    except (KeyError, TypeError, ValueError):
        return None
    if not isfinite(value) or not isfinite(planned_volume) or planned_volume <= 0:
        return None
    return value * RISK_REFERENCE_VOLUME / planned_volume


def calculate_zone_policy_metrics(rows: Iterable[Mapping[str, object]]) -> dict:
    ordered = sorted(
        list(rows),
        key=lambda row: (
            str(row.get("ready_at_utc") or ""),
            str(row.get("provider_signal_id") or ""),
        ),
    )
    verified = [
        row
        for row in ordered
        if row.get("money_status") in {"verified", "verified_no_fill"}
        and row.get("strategy_pnl") is not None
    ]
    values = [float(row["strategy_pnl"]) for row in verified]
    normalized_values = [_risk_normalized_pnl(row) for row in verified]
    normalization_complete = bool(verified) and all(
        value is not None for value in normalized_values
    )
    normalized = [
        float(value) for value in normalized_values if value is not None
    ]
    planned_volumes = sorted({
        round(float(row["planned_volume"]), 8)
        for row in verified
        if _risk_normalized_pnl(row) is not None
    })
    positive = sum(value for value in values if value > 0)
    negative = abs(sum(value for value in values if value < 0))
    days: dict[str, float] = defaultdict(float)
    daily_buckets: dict[str, dict[str, object]] = {}
    leg_buckets: dict[int, dict[str, object]] = {}
    for row, value in zip(verified, values, strict=True):
        day = str(row.get("signal_date") or "unknown")
        days[day] += value
        normalized_value = _risk_normalized_pnl(row)
        daily = daily_buckets.setdefault(day, {
            "verified_plans": 0,
            "filled_plans": 0,
            "verified_net_pnl": 0.0,
            "risk_normalized_net_pnl": 0.0,
            "normalization_complete": True,
        })
        daily["verified_plans"] = int(daily["verified_plans"]) + 1
        daily["filled_plans"] = int(daily["filled_plans"]) + int(
            row.get("status") == "filled"
        )
        daily["verified_net_pnl"] = (
            float(daily["verified_net_pnl"]) + value
        )
        if normalized_value is None:
            daily["normalization_complete"] = False
        else:
            daily["risk_normalized_net_pnl"] = (
                float(daily["risk_normalized_net_pnl"])
                + normalized_value
            )

        planned_legs: dict[int, Mapping[str, object]] = {}
        for leg in (
            *(row.get("unfilled_legs") or []),
            *(row.get("filled_legs") or []),
        ):
            if not isinstance(leg, Mapping):
                continue
            try:
                leg_index = int(leg["leg_index"])
            except (KeyError, TypeError, ValueError):
                continue
            planned_legs[leg_index] = leg
        for leg_index, leg in planned_legs.items():
            bucket = leg_buckets.setdefault(leg_index, {
                "depth_fraction": float(leg.get("depth_fraction") or 0.0),
                "planned_occurrences": 0,
                "filled_occurrences": 0,
                "verified_net_pnl": 0.0,
                "risk_normalized_net_pnl": 0.0,
                "normalization_complete": True,
            })
            bucket["planned_occurrences"] = (
                int(bucket["planned_occurrences"]) + 1
            )
        for leg in row.get("filled_legs") or []:
            if not isinstance(leg, Mapping):
                continue
            money = leg.get("money")
            if not isinstance(money, Mapping) or money.get("status") != "verified":
                continue
            try:
                leg_index = int(leg["leg_index"])
                leg_pnl = float(money["strategy_pnl"])
            except (KeyError, TypeError, ValueError):
                continue
            if not isfinite(leg_pnl):
                continue
            bucket = leg_buckets[leg_index]
            bucket["filled_occurrences"] = (
                int(bucket["filled_occurrences"]) + 1
            )
            bucket["verified_net_pnl"] = (
                float(bucket["verified_net_pnl"]) + leg_pnl
            )
            try:
                planned_volume = float(row["planned_volume"])
            except (KeyError, TypeError, ValueError):
                planned_volume = 0.0
            if not isfinite(planned_volume) or planned_volume <= 0:
                bucket["normalization_complete"] = False
            else:
                bucket["risk_normalized_net_pnl"] = (
                    float(bucket["risk_normalized_net_pnl"])
                    + leg_pnl * RISK_REFERENCE_VOLUME / planned_volume
                )
    daily_results = [
        {
            "signal_date": day,
            "verified_plans": int(bucket["verified_plans"]),
            "filled_plans": int(bucket["filled_plans"]),
            "verified_net_pnl": round(float(bucket["verified_net_pnl"]), 2),
            "risk_normalized_net_pnl": (
                round(float(bucket["risk_normalized_net_pnl"]), 2)
                if bucket["normalization_complete"]
                else None
            ),
        }
        for day, bucket in sorted(daily_buckets.items())
    ]
    leg_contributions = []
    for leg_index, bucket in sorted(leg_buckets.items()):
        planned_occurrences = int(bucket["planned_occurrences"])
        filled_occurrences = int(bucket["filled_occurrences"])
        leg_contributions.append({
            "leg_index": leg_index,
            "depth_fraction": float(bucket["depth_fraction"]),
            "planned_occurrences": planned_occurrences,
            "filled_occurrences": filled_occurrences,
            "fill_rate": (
                round(filled_occurrences / planned_occurrences, 4)
                if planned_occurrences
                else None
            ),
            "verified_net_pnl": round(float(bucket["verified_net_pnl"]), 2),
            "risk_normalized_net_pnl": (
                round(float(bucket["risk_normalized_net_pnl"]), 2)
                if bucket["normalization_complete"]
                else None
            ),
        })
    depth_counts = {str(depth): 0 for depth in DEPTH_AUDIT_FRACTIONS}
    for row in ordered:
        diagnostics = row.get("zone_diagnostics")
        if not isinstance(diagnostics, Mapping):
            continue
        for depth in diagnostics.get("touched_depths") or []:
            key = str(float(depth))
            if key in depth_counts:
                depth_counts[key] += 1
    net = round(sum(values), 2)
    drawdown = _maximum_drawdown(values)
    normalized_net = (
        round(sum(normalized), 2) if normalization_complete else None
    )
    normalized_drawdown = (
        _maximum_drawdown(normalized) if normalization_complete else None
    )
    return {
        "plans": len(ordered),
        "filled_plans": sum(row.get("status") == "filled" for row in ordered),
        "unfilled_plans": sum(
            row.get("status") == "unfilled" for row in ordered
        ),
        "blocked_plans": sum(
            row.get("status") == "blocked" for row in ordered
        ),
        "verified_money_plans": len(verified),
        "verified_net_pnl": net,
        "expectancy_per_verified_plan": (
            round(net / len(verified), 4) if verified else None
        ),
        "profit_factor": (
            round(positive / negative, 4)
            if negative > 0
            else (None if positive == 0 else "infinite")
        ),
        "maximum_drawdown": drawdown,
        "worst_basket": round(min(values), 2) if values else None,
        "worst_day": round(min(days.values()), 2) if days else None,
        "return_over_drawdown": (
            round(net / drawdown, 4) if drawdown > 0 else None
        ),
        "risk_reference_volume": RISK_REFERENCE_VOLUME,
        "policy_planned_volume": (
            planned_volumes[0] if len(planned_volumes) == 1 else None
        ),
        "risk_normalization_complete": normalization_complete,
        "risk_normalized_net_pnl": normalized_net,
        "risk_normalized_expectancy_per_verified_plan": (
            round(normalized_net / len(verified), 4)
            if normalized_net is not None and verified
            else None
        ),
        "risk_normalized_maximum_drawdown": normalized_drawdown,
        "risk_normalized_return_over_drawdown": (
            round(normalized_net / normalized_drawdown, 4)
            if (
                normalized_net is not None
                and normalized_drawdown is not None
                and normalized_drawdown > 0
            )
            else None
        ),
        "daily_results": daily_results,
        "leg_contributions": leg_contributions,
        "depth_touch_counts": depth_counts,
        "ranking_eligible": (
            len(verified) == len(ordered)
            and normalization_complete
            and not any(row.get("status") == "blocked" for row in ordered)
        ),
    }


def _record_in_scope(
    record: Mapping[str, object],
    since: date | None,
    until: date | None,
) -> bool:
    if record.get("record_type") != "zone_plan" or record.get("channel") != "canal2":
        return False
    day = _record_day(record)
    if day is None:
        return since is None and until is None
    return not ((since is not None and day < since) or (until is not None and day > until))


def _audit_matches(row: Mapping[str, object], audit: Mapping[str, object]) -> bool:
    diagnostics = row.get("zone_diagnostics")
    if not isinstance(diagnostics, Mapping) or audit.get("status") != "audited":
        return False
    return (
        list(diagnostics.get("touched_depths") or [])
        == list(audit.get("touched_depths") or [])
        and diagnostics.get("maximum_penetration_pct")
        == audit.get("maximum_penetration_pct")
    )


def build_zone_farm_report(
    catalog: Mapping[str, object],
    tick_source,
    *,
    policies: Iterable[ZoneEntryPolicy] | None = None,
    money_converter=None,
    observed_trades: Iterable[Mapping[str, object]] | None = None,
    since: str | date | None = None,
    until: str | date | None = None,
    source_fingerprints: Mapping[str, object] | None = None,
) -> dict:
    since_day = _date(since) if since is not None else None
    until_day = _date(until) if until is not None else None
    if since is not None and since_day is None:
        raise ValueError("invalid since date")
    if until is not None and until_day is None:
        raise ValueError("invalid until date")
    if since_day and until_day and until_day < since_day:
        raise ValueError("until date cannot precede since date")

    policy_catalog = tuple(policies or default_zone_entry_policies())
    if not policy_catalog:
        raise ValueError("at least one zone policy is required")
    if len({policy.policy_id for policy in policy_catalog}) != len(policy_catalog):
        raise ValueError("zone policy ids must be unique")
    raw_signals = catalog.get("signals") or []
    if isinstance(raw_signals, Mapping) or not isinstance(raw_signals, Sequence):
        raise ValueError("catalog signals must be a sequence")
    records = [
        record
        for record in raw_signals
        if isinstance(record, Mapping)
        and _record_in_scope(record, since_day, until_day)
    ]
    records.sort(key=lambda row: (
        str(row.get("signal_ts_utc") or row.get("first_observed_utc") or ""),
        str(row.get("provider_signal_id") or ""),
    ))
    execution_sig_to_provider: dict[str, str] = {}
    for record in records:
        provider_id = str(record.get("provider_signal_id") or "")
        if provider_id:
            execution_sig_to_provider[provider_id] = provider_id
        for sig_id in record.get("execution_sig_ids") or []:
            execution_sig_to_provider[str(sig_id)] = provider_id
        for batch in record.get("execution_batches") or []:
            if isinstance(batch, Mapping) and batch.get("sig_id"):
                execution_sig_to_provider[str(batch["sig_id"])] = provider_id

    day_cache: dict[date, tuple[pd.DataFrame, dict | None, list[str]]] = {}
    rows: list[dict] = []
    audit_disagreements: list[dict] = []
    baseline_proofs: list[dict] = []
    observed_execution_proofs: list[dict] = []
    complete_plans = 0
    incomplete_plans = 0
    tick_valid_complete_plans = 0
    for record in records:
        spec = build_zone_trade_spec(record)
        execution_proofs_for_spec: list[dict] = []

        def block_execution_proofs(*blockers: object) -> None:
            for batch in spec.execution_batches:
                proof = {
                    "status": "blocked",
                    "within_tolerance": False,
                    "execution_batch_id": batch.get("execution_batch_id"),
                    "provider_signal_id": spec.provider_signal_id,
                    "blockers": _stable_strings(blockers),
                }
                execution_proofs_for_spec.append(proof)
                observed_execution_proofs.append(proof)

        if spec.entry_ready:
            complete_plans += 1
        else:
            incomplete_plans += 1
        day = spec.ready_at_utc.date() if spec.ready_at_utc else _record_day(record)
        if day is None:
            block_execution_proofs("missing_signal_date")
            for policy in policy_catalog:
                rows.append(_blocked_policy_row(
                    spec,
                    policy,
                    (*spec.blockers, "missing_signal_date"),
                    None,
                ))
            continue
        if day not in day_cache and spec.entry_ready:
            day_cache[day] = tick_source.load_day(day)
        ticks, tick_evidence, tick_blockers = day_cache.get(
            day,
            (pd.DataFrame(), None, []),
        )
        if spec.blockers:
            block_execution_proofs(*spec.blockers)
            for policy in policy_catalog:
                rows.append(_blocked_policy_row(
                    spec,
                    policy,
                    spec.blockers,
                    day,
                ))
            continue
        if tick_blockers or ticks.empty:
            blockers = tick_blockers or [f"missing_ticks:{day.isoformat()}"]
            block_execution_proofs(*blockers)
            for policy in policy_catalog:
                rows.append(_blocked_policy_row(
                    spec,
                    policy,
                    blockers,
                    day,
                ))
            continue
        tick_valid_complete_plans += 1
        horizon = pd.to_datetime(ticks["time_utc"], utc=True).max().to_pydatetime()
        tick_offset = (
            tick_evidence.get("utc_offset_seconds")
            if isinstance(tick_evidence, Mapping)
            else None
        )
        observed_zone_audit = audit_zone_depths(
            spec,
            ticks,
            fractions=DEPTH_AUDIT_FRACTIONS,
            horizon_at=horizon,
            expiry_mode="session_end",
        )
        for batch in spec.execution_batches:
            proof = audit_observed_zone_execution(
                spec,
                batch,
                ticks,
                zone_audit=observed_zone_audit,
                verified_utc_offset_seconds=tick_offset,
            )
            execution_proofs_for_spec.append(proof)
            observed_execution_proofs.append(proof)
        for policy in policy_catalog:
            row = simulate_zone_policy(
                spec,
                ticks,
                policy,
                horizon_at=horizon,
                money_converter=money_converter,
                verified_utc_offset_seconds=tick_offset,
            )
            row["signal_date"] = day.isoformat()
            row["ready_at_utc"] = spec.ready_at_utc.isoformat()
            audit = audit_zone_depths(
                spec,
                ticks,
                fractions=DEPTH_AUDIT_FRACTIONS,
                horizon_at=horizon,
                expiry_mode=policy.expiry_mode,
            )
            row["audit"] = audit
            row["audit_status"] = (
                "verified" if _audit_matches(row, audit) else "disagreement"
            )
            if row["audit_status"] == "disagreement":
                disagreement = {
                    "provider_signal_id": spec.provider_signal_id,
                    "policy_id": policy.policy_id,
                    "candidate": row.get("zone_diagnostics"),
                    "audit": audit,
                }
                audit_disagreements.append(disagreement)
                row["blockers"] = _stable_strings((
                    *row.get("blockers", []),
                    "independent_zone_audit_disagreement",
                ))
                row["status"] = "blocked"
                row["strategy_pnl"] = None
                row["money_status"] = "not_applicable"
            proofs: list[dict] = []
            if policy.policy_id == BASELINE_POLICY_ID:
                for batch in spec.execution_batches:
                    proof = validate_observed_baseline(row, batch)
                    proof["provider_signal_id"] = spec.provider_signal_id
                    proofs.append(proof)
                    baseline_proofs.append(proof)
            row["modeled_baseline_proofs"] = proofs
            row["observed_execution_proofs"] = execution_proofs_for_spec
            rows.append(row)

    policy_summaries = {
        policy.policy_id: calculate_zone_policy_metrics(
            row for row in rows if row.get("policy_id") == policy.policy_id
        )
        for policy in policy_catalog
    }
    baseline_rows = {
        str(row["provider_signal_id"]): row
        for row in rows
        if row.get("policy_id") == BASELINE_POLICY_ID
        and row.get("strategy_pnl") is not None
    }
    for policy_id, metrics in policy_summaries.items():
        policy_rows = {
            str(row["provider_signal_id"]): row
            for row in rows
            if row.get("policy_id") == policy_id
            and row.get("strategy_pnl") is not None
        }
        shared = sorted(set(baseline_rows) & set(policy_rows))
        metrics["paired_vs_live_baseline"] = {
            "plans": len(shared),
            "pnl_delta": round(sum(
                float(policy_rows[signal_id]["strategy_pnl"])
                - float(baseline_rows[signal_id]["strategy_pnl"])
                for signal_id in shared
            ), 2),
            "risk_normalized_pnl_delta": round(sum(
                float(_risk_normalized_pnl(policy_rows[signal_id]) or 0.0)
                - float(_risk_normalized_pnl(baseline_rows[signal_id]) or 0.0)
                for signal_id in shared
            ), 2),
        }

    blocked_rows = sum(row.get("status") == "blocked" for row in rows)
    selection_blockers = ["missing_untouched_forward_days"]
    if blocked_rows:
        selection_blockers.append("blocked_source_rows_present")
    if audit_disagreements:
        selection_blockers.append("independent_audit_disagreement")
    if any(
        proof.get("within_tolerance") is not True
        for proof in observed_execution_proofs
    ):
        selection_blockers.append("observed_execution_not_verified")
    verified_candidates = [
        (policy_id, metrics)
        for policy_id, metrics in policy_summaries.items()
        if (
            metrics["verified_money_plans"] > 0
            and metrics["risk_normalized_net_pnl"] is not None
        )
    ]
    best_subset = (
        max(
            verified_candidates,
            key=lambda item: item[1]["risk_normalized_net_pnl"],
        )[0]
        if verified_candidates
        else None
    )
    policies_payload = [asdict(policy) for policy in policy_catalog]
    observed_details: list[dict] = []
    for trade in observed_trades or ():
        sig_id = str(trade.get("sig_id") or "")
        provider_id = execution_sig_to_provider.get(sig_id)
        if provider_id is None:
            continue
        blockers: list[str] = []
        try:
            pnl = float(trade.get("pnl_real_mt5"))
        except (TypeError, ValueError):
            pnl = float("nan")
        if not isfinite(pnl):
            blockers.append("missing_observed_mt5_pnl")
        if trade.get("status") != "closed":
            blockers.append("observed_trade_not_closed")
        if trade.get("pnl_mt5_complete") is not True:
            blockers.append("observed_mt5_pnl_incomplete")
        if trade.get("reconciled_ok") is not True:
            blockers.append("observed_trade_not_reconciled")
        if trade.get("analysis_excluded") is True:
            blockers.append("observed_trade_analysis_excluded")
        stable = _stable_strings(blockers)
        observed_details.append({
            "provider_signal_id": provider_id,
            "sig_id": sig_id,
            "status": "verified" if not stable else "blocked",
            "pnl_real_mt5": round(pnl, 2) if isfinite(pnl) else None,
            "ticket_count": len(trade.get("tickets") or []),
            "blockers": stable,
        })
    observed_details.sort(key=lambda row: (
        str(row["provider_signal_id"]),
        str(row["sig_id"]),
    ))
    verified_observed = [
        row for row in observed_details if row["status"] == "verified"
    ]
    observed_provider_ids = {
        str(row["provider_signal_id"]) for row in verified_observed
    }
    modeled_on_observed: dict[str, dict] = {}
    for policy in policy_catalog:
        comparable = [
            row
            for row in rows
            if (
                row.get("policy_id") == policy.policy_id
                and str(row.get("provider_signal_id")) in observed_provider_ids
                and row.get("money_status") in {"verified", "verified_no_fill"}
                and row.get("strategy_pnl") is not None
            )
        ]
        modeled_on_observed[policy.policy_id] = {
            "plans": len(comparable),
            "verified_net_pnl": round(sum(
                float(row["strategy_pnl"]) for row in comparable
            ), 2),
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": {
            "channel": "canal2",
            "since": since_day.isoformat() if since_day else None,
            "until": until_day.isoformat() if until_day else None,
            "zone_plans": len(records),
            "complete_zone_plans": complete_plans,
            "incomplete_zone_plans": incomplete_plans,
            "tick_valid_complete_zone_plans": tick_valid_complete_plans,
            "policy_count": len(policy_catalog),
            "expected_rows": len(records) * len(policy_catalog),
        },
        "source_fingerprints": dict(source_fingerprints or {}),
        "policy_catalog": policies_payload,
        "policy_catalog_sha256": _canonical_sha256(policies_payload),
        "rows": rows,
        "policy_summaries": policy_summaries,
        "summary": {
            "rows": len(rows),
            "blocked_rows": blocked_rows,
            "filled_rows": sum(row.get("status") == "filled" for row in rows),
            "unfilled_rows": sum(
                row.get("status") == "unfilled" for row in rows
            ),
        },
        "audit_summary": {
            "audited_rows": sum(
                row.get("audit_status") in {"verified", "disagreement"}
                for row in rows
            ),
            "disagreements": len(audit_disagreements),
            "details": audit_disagreements,
        },
        "modeled_baseline_summary": {
            "proofs": len(baseline_proofs),
            "verified": sum(
                proof.get("within_tolerance") is True
                for proof in baseline_proofs
            ),
            "mismatches": sum(
                proof.get("within_tolerance") is not True
                for proof in baseline_proofs
            ),
            "details": baseline_proofs,
        },
        "observed_execution_summary": {
            "proofs": len(observed_execution_proofs),
            "verified": sum(
                proof.get("within_tolerance") is True
                for proof in observed_execution_proofs
            ),
            "blocked": sum(
                proof.get("within_tolerance") is not True
                for proof in observed_execution_proofs
            ),
            "details": observed_execution_proofs,
        },
        "observed_live_result": {
            "comparison_role": "context_only",
            "reason": (
                "observed live management and fills differ from the common "
                "modeled exit contract"
            ),
            "trades": len(observed_details),
            "verified_trades": len(verified_observed),
            "blocked_trades": len(observed_details) - len(verified_observed),
            "verified_net_pnl": round(sum(
                float(row["pnl_real_mt5"]) for row in verified_observed
            ), 2),
            "pnl_currency": (
                str(money_converter.currency)
                if money_converter is not None
                else None
            ),
            "details": observed_details,
            "modeled_common_exit_by_policy": modeled_on_observed,
        },
        "selection": {
            "status": "exploratory_only",
            "blockers": selection_blockers,
            "highest_verified_subset_pnl_policy": best_subset,
        },
    }


def write_report(report: Mapping[str, object], path: Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Offline Gold Signals zone strategy farm",
    )
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--tick-cache", type=Path, required=True)
    parser.add_argument("--money-contract", type=Path, required=True)
    parser.add_argument("--money-tick-cache", type=Path, required=True)
    parser.add_argument("--observed-replay", type=Path)
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    contract = load_contract(args.money_contract)
    tick_source = IndependentTickCache(
        args.tick_cache,
        expected_symbol=str(contract["instrument"]["symbol"]),
        require_market_session=True,
    )
    money_converter = BrokerMoneyConverter(
        contract,
        tick_cache_dir=args.money_tick_cache,
    )
    report = build_zone_farm_report(
        catalog,
        tick_source,
        money_converter=money_converter,
        observed_trades=(
            _load_jsonl(args.observed_replay)
            if args.observed_replay is not None
            else None
        ),
        since=args.since,
        until=args.until,
        source_fingerprints={
            "catalog_sha256": _file_sha256(args.catalog),
            "money_contract_sha256": _file_sha256(args.money_contract),
            **(
                {"observed_replay_sha256": _file_sha256(args.observed_replay)}
                if args.observed_replay is not None
                else {}
            ),
        },
    )
    report["source_fingerprints"]["tick_days"] = tick_source.evidence_by_day
    write_report(report, args.output)
    if not args.quiet:
        print(
            "Zone strategy farm: "
            f"{report['scope']['zone_plans']} plans, "
            f"{report['summary']['blocked_rows']} blocked rows, "
            f"{report['audit_summary']['disagreements']} audit disagreements"
        )
        print(f"Output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "audit_observed_zone_execution",
    "build_zone_farm_report",
    "calculate_zone_policy_metrics",
    "validate_observed_baseline",
    "write_report",
]
