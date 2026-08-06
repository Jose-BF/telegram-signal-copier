from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict
from datetime import date, datetime, timezone
from math import isfinite
from pathlib import Path

import pandas as pd

from broker_money import BrokerMoneyConverter, load_contract
from provider_zone_simulator import DEPTH_AUDIT_FRACTIONS, simulate_zone_policy
from provider_zone_spec import ProviderZoneSpec, build_zone_trade_spec
from simulation_oracle import IndependentTickCache
from zone_entry_policies import (
    ZoneEntryPolicy,
    default_zone_entry_policies,
)
from zone_fill_auditor import audit_zone_depths


SCHEMA_VERSION = 1
BASELINE_POLICY_ID = "all_first_touch_live"


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
        "observed_baseline_proofs": [],
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


def _maximum_drawdown(values: Sequence[float]) -> float:
    equity = 0.0
    peak = 0.0
    maximum = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        maximum = max(maximum, peak - equity)
    return round(maximum, 2)


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
    positive = sum(value for value in values if value > 0)
    negative = abs(sum(value for value in values if value < 0))
    days: dict[str, float] = defaultdict(float)
    for row, value in zip(verified, values, strict=True):
        day = str(row.get("signal_date") or "unknown")
        days[day] += value
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
        "depth_touch_counts": depth_counts,
        "ranking_eligible": (
            len(verified) == len(ordered)
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

    day_cache: dict[date, tuple[pd.DataFrame, dict | None, list[str]]] = {}
    rows: list[dict] = []
    audit_disagreements: list[dict] = []
    baseline_proofs: list[dict] = []
    for record in records:
        spec = build_zone_trade_spec(record)
        day = spec.ready_at_utc.date() if spec.ready_at_utc else _record_day(record)
        if day is None:
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
        ticks, _tick_evidence, tick_blockers = day_cache.get(
            day,
            (pd.DataFrame(), None, []),
        )
        if spec.blockers:
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
            for policy in policy_catalog:
                rows.append(_blocked_policy_row(
                    spec,
                    policy,
                    blockers,
                    day,
                ))
            continue
        horizon = pd.to_datetime(ticks["time_utc"], utc=True).max().to_pydatetime()
        for policy in policy_catalog:
            row = simulate_zone_policy(
                spec,
                ticks,
                policy,
                horizon_at=horizon,
                money_converter=money_converter,
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
            row["observed_baseline_proofs"] = proofs
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
        }

    blocked_rows = sum(row.get("status") == "blocked" for row in rows)
    selection_blockers = ["missing_untouched_forward_days"]
    if blocked_rows:
        selection_blockers.append("blocked_source_rows_present")
    if audit_disagreements:
        selection_blockers.append("independent_audit_disagreement")
    verified_candidates = [
        (policy_id, metrics)
        for policy_id, metrics in policy_summaries.items()
        if metrics["verified_money_plans"] > 0
    ]
    best_subset = (
        max(
            verified_candidates,
            key=lambda item: item[1]["verified_net_pnl"],
        )[0]
        if verified_candidates
        else None
    )
    policies_payload = [asdict(policy) for policy in policy_catalog]
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": {
            "channel": "canal2",
            "since": since_day.isoformat() if since_day else None,
            "until": until_day.isoformat() if until_day else None,
            "zone_plans": len(records),
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
        "observed_baseline_summary": {
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
        since=args.since,
        until=args.until,
        source_fingerprints={
            "catalog_sha256": _file_sha256(args.catalog),
            "money_contract_sha256": _file_sha256(args.money_contract),
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
    "build_zone_farm_report",
    "calculate_zone_policy_metrics",
    "validate_observed_baseline",
    "write_report",
]
