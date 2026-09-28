"""Prepare an offline, chat-isolated Gold observation catalog without simulation."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import platform
import sys
from typing import Iterable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parser import is_canal2_entry
import provider_signal_catalog
from tools.audit_causal_lineage import _has_raw_message_evidence

CURRENT_GOLD_CHAT_ID = -1003908582492


def gold_chat_id(manifest: dict) -> int:
    messages = manifest["telegram_raw_coverage"]["messages"]
    identities = {row["chat_id"] for row in messages}
    if identities != {CURRENT_GOLD_CHAT_ID}:
        raise ValueError("source manifest must contain only the current Gold chat")
    if len({row["message_id"] for row in messages}) != len(messages):
        raise ValueError("duplicate inventory message identity")
    return CURRENT_GOLD_CHAT_ID


def parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        return None
    return stamp.astimezone(timezone.utc)


def file_identity(path: Path) -> dict:
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": digest}


def validate_manifest(payload: dict) -> str:
    unsigned = dict(payload)
    expected = unsigned.pop("manifest_identity_sha256", None)
    encoded = json.dumps(unsigned, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    actual = hashlib.sha256(encoded).hexdigest()
    if expected != actual:
        raise ValueError("source readiness manifest identity mismatch")
    return actual


def select_observations(events: Iterable[dict], *, chat_id: int, cutoff: datetime) -> tuple[list[dict], dict, dict]:
    selected = []
    history: dict[int, list[dict]] = defaultdict(list)
    counts = Counter()
    for row in events:
        if row.get("ev") != "telegram_raw":
            counts["non_raw_events_not_used_for_provider_catalog"] += 1
            continue
        if row.get("chat_id") != chat_id or isinstance(row.get("chat_id"), bool):
            counts["other_chat_receipts_not_used"] += 1
            continue
        message_id = row.get("message_id")
        if type(message_id) is not int or message_id <= 0:
            raise ValueError("target-chat receipt has invalid message identity")
        history[message_id].append(row)
        counts["target_chat_receipts"] += 1
        stamp = parse_utc(row.get("ts"))
        if row.get("channel") != "canal2":
            counts["target_chat_channel_mismatch"] += 1
        elif stamp is None:
            counts["target_chat_receipt_time_invalid"] += 1
        elif stamp >= cutoff:
            counts["target_chat_receipts_after_cutoff"] += 1
        else:
            selected.append(row)
            counts["selected_receipts"] += 1
    return selected, dict(history), dict(counts)


def trigger_revisions(signal: dict) -> list[dict]:
    contract = signal.get("entry_contract") or {}
    when = parse_utc(contract.get("trigger_observed_utc"))
    if when is None:
        return []
    return [revision for revision in signal.get("revisions") or []
            if revision.get("message_id") == contract.get("trigger_message_id")
            and parse_utc(revision.get("observed_ts_utc")) == when]


def unedited_receipt(row: dict) -> bool:
    return (not row.get("edit_date_utc") and row.get("is_edit") is False
            and "edit" not in str(row.get("update_kind") or "").lower())


def coverage_requirements(trigger: datetime, minutes: int, tick_records: dict, conflicts: set) -> dict:
    if type(minutes) is not int or minutes <= 0:
        raise ValueError("screening horizon must be a positive integer")
    end = trigger + timedelta(minutes=minutes)
    current = trigger.date()
    requirements = []
    while current <= end.date():
        day = current.isoformat()
        for symbol in ("XAUUSD", "EURUSD"):
            key = (day, symbol)
            record = tick_records.get(key)
            blockers = []
            if record is None:
                blockers.append("missing_tick_source")
            else:
                if not record.get("sha256_verified") or not record.get("metadata_sha256_verified"):
                    blockers.append("tick_source_hash_not_verified")
                coverage = record.get("coverage") or {}
                start = parse_utc(coverage.get("complete_from_utc"))
                through = parse_utc(coverage.get("complete_through_utc"))
                required_from = max(trigger, datetime.combine(current, datetime.min.time(), timezone.utc))
                required_through = min(end, datetime.combine(current + timedelta(days=1), datetime.min.time(), timezone.utc))
                if start is None or through is None or start > required_from or through < required_through:
                    blockers.append("declared_tick_coverage_incomplete")
                if key in conflicts:
                    blockers.append("non_identical_tick_alternatives_unresolved")
            requirements.append({"day_utc": day, "symbol": symbol,
                                 "source_path": record.get("path") if record else None,
                                 "sha256": record.get("sha256") if record else None,
                                 "blockers": blockers})
        current += timedelta(days=1)
    return {"screening_horizon_minutes": minutes, "through_utc": end.isoformat(),
            "status": "blocked" if any(row["blockers"] for row in requirements) else "declared_sources_available",
            "requirements": requirements,
            "not_full_tick_or_money_admission": True}


def build_readiness(catalog: dict, history: dict, manifest: dict, *, cutoff: datetime,
                    horizons: tuple[int, ...] = (240, 1440), resolved_conflicts: set | None = None) -> dict:
    gold_chat_id(manifest)
    period = manifest["proposed_period"]
    start = date.fromisoformat(period["start_date_utc"])
    end = date.fromisoformat(period["end_date_utc"])
    inventory_messages = {int(row["message_id"]): row for row in manifest["telegram_raw_coverage"]["messages"]}
    tick_selection = manifest["tick_source_selection"]
    ticks = {(row["day"], row["symbol"]): row for row in tick_selection["selected_records"]}
    conflicts = {(row["day"], row["symbol"]) for row in tick_selection["conflicting_consistent_duplicate_groups"]}
    conflicts -= resolved_conflicts or set()
    catalog_by_message: dict[int, set[str]] = defaultdict(set)
    signals = []
    for signal in catalog["signals"]:
        signal_id = signal["provider_signal_id"]
        source_ids = set(signal.get("source_message_ids") or [])
        source_ids.update(row["message_id"] for row in signal.get("management_events") or []
                          if type(row.get("message_id")) is int)
        for message_id in source_ids:
            catalog_by_message[message_id].add(signal_id)
        contract = signal.get("entry_contract") or {}
        trigger = parse_utc(contract.get("trigger_observed_utc"))
        revisions = trigger_revisions(signal)
        now_at_trigger = [row for row in revisions if is_canal2_entry(str(row.get("text") or ""))]
        any_now = any(is_canal2_entry(str(row.get("text") or "")) for row in signal.get("revisions") or [])
        causal_blockers = list(contract.get("blockers") or [])
        if signal["record_type"] != "formal_signal":
            scope = "outside_formal_now_universe"
        elif not now_at_trigger:
            scope = "non_now_entry_with_later_now" if any_now else "formal_non_now_entry"
        elif trigger is None or not start <= trigger.date() <= end:
            scope = "now_trigger_outside_period"
        else:
            scope = "observed_formal_now"
        if len({row.get("text") for row in revisions}) > 1:
            causal_blockers.append("ambiguous_trigger_revision_same_timestamp")
        trigger_id = contract.get("trigger_message_id")
        raw_matches = [row for row in history.get(trigger_id, [])
                       if parse_utc(row.get("ts")) == trigger
                       and row.get("text") in {revision.get("text") for revision in now_at_trigger}]
        provider_time = parse_utc(contract.get("trigger_telegram_utc"))
        published_times = [stamp for row in raw_matches if (stamp := parse_utc(row.get("date_utc"))) is not None]
        publication = min(published_times, default=None)
        delay = (trigger - provider_time).total_seconds() if trigger and provider_time else None
        publication_age = (trigger - publication).total_seconds() if trigger and publication else None
        timing = {"provider_to_receipt_seconds": delay, "publication_to_receipt_seconds": publication_age,
                  "publication_utc": publication.isoformat() if publication else None,
                  "received_on_later_utc_date": bool(trigger and provider_time and trigger.date() > provider_time.date()),
                  "update_kinds": sorted({str(row.get("update_kind") or "unknown") for row in raw_matches}),
                  "freshness_admission": "not_evaluated_entry_expiry_policy_not_frozen"}
        source_warnings = []
        original_by_trigger = bool(trigger and any(
            unedited_receipt(row) and row.get("channel") == "canal2"
            and (stamp := parse_utc(row.get("ts"))) is not None and stamp <= trigger
            for row in history.get(trigger_id, [])))
        if scope == "observed_formal_now":
            if delay is None:
                source_warnings.append("provider_revision_time_unknown")
            elif delay < 0:
                causal_blockers.append("provider_revision_time_after_receipt")
            if not raw_matches:
                causal_blockers.append("trigger_receipt_not_found_in_raw_source")
            elif not any(_has_raw_message_evidence(row) for row in raw_matches):
                source_warnings.append("trigger_lacks_canonical_revision_identity")
            if not original_by_trigger:
                source_warnings.append("unedited_original_not_observed")
        if any((parse_utc(row.get("observed_ts_utc")) or cutoff) >= cutoff
               for key in ("revisions", "management_events", "level_timeline")
               for row in signal.get(key) or []):
            causal_blockers.append("catalog_contains_invalid_or_post_cutoff_observation")
        if contract.get("direction") and signal.get("direction") != contract["direction"]:
            source_warnings.append("final_direction_differs_from_causal_entry_direction")
        signal_row = {
            "provider_signal_id": signal_id, "chat_id": gold_chat_id(manifest),
            "root_message_id": signal["root_message_id"], "source_message_ids": sorted(source_ids),
            "record_type": signal["record_type"], "scope": scope,
            "trigger_observed_utc": contract.get("trigger_observed_utc"),
            "trigger_telegram_utc": contract.get("trigger_telegram_utc"),
            "trigger_direction": contract.get("direction"), "final_catalog_direction": signal.get("direction"),
            "trigger_message_id": trigger_id, "trigger_kind": contract.get("trigger_kind"),
            "trigger_timing": timing,
            "unedited_original_observed_by_trigger": original_by_trigger,
            "trigger_receipt_references": [{"event_id": row.get("event_id"),
                                            "message_revision_id": row.get("message_revision_id"),
                                            "session_id": row.get("session_id"), "ts": row.get("ts")}
                                           for row in raw_matches],
            "source_warnings": sorted(set(source_warnings)), "causal_blockers": sorted(set(causal_blockers)),
            "policy_dependent_semantic_gaps": signal.get("semantic_gaps") or [],
            "execution_observed": None,
            "coverage_screening": [coverage_requirements(trigger, minutes, ticks, conflicts) for minutes in horizons]
                                 if scope == "observed_formal_now" else [],
            "study_admission": "not_evaluated_money_policy_and_horizon_not_frozen",
        }
        signals.append(signal_row)
    messages = []
    for message_id, inventory in sorted(inventory_messages.items()):
        receipts = history.get(message_id, [])
        before_cutoff = [row for row in receipts
                         if (stamp := parse_utc(row.get("ts"))) is not None and stamp < cutoff
                         and row.get("channel") == "canal2"]
        linked = sorted(catalog_by_message.get(message_id, set()))
        raw_now = [row for row in before_cutoff if is_canal2_entry(str(row.get("text") or ""))]
        if not before_cutoff:
            disposition = "no_usable_receipt_before_cutoff"
        elif linked:
            disposition = "catalogued"
        else:
            disposition = "uncatalogued_reply_or_context_retained"
        messages.append({"chat_id": inventory["chat_id"], "message_id": message_id,
                         "publish_utc": inventory.get("publish_utc"),
                         "first_receipt_utc": inventory.get("first_receipt_utc"),
                         "raw_receipts_total": len(receipts), "receipts_before_cutoff": len(before_cutoff),
                         "raw_now_seen_before_cutoff": bool(raw_now),
                         "catalog_signal_ids": linked, "disposition": disposition,
                         "source_blockers": inventory.get("causal_source_blockers") or [],
                         "unedited_original_observed": any(unedited_receipt(row) for row in before_cutoff),
                         "inventory_unedited_original_observed_anytime": inventory.get("unedited_revision_observed"),
                         "blockers": ["raw_now_missing_from_catalog"] if raw_now and not linked else []})
    now = [row for row in signals if row["scope"] == "observed_formal_now"]
    summary = {
        "inventory_messages": len(messages), "inventory_receipts_total": sum(row["raw_receipts_total"] for row in messages),
        "messages_by_disposition": dict(Counter(row["disposition"] for row in messages)),
        "catalog_records_by_type": dict(Counter(row["record_type"] for row in signals)),
        "records_by_scope": dict(Counter(row["scope"] for row in signals)),
        "now_signals": len(now), "now_by_day": dict(sorted(Counter((row["trigger_observed_utc"] or "")[:10] for row in now).items())),
        "now_with_causal_blockers": sum(bool(row["causal_blockers"]) for row in now),
        "now_source_warnings": dict(Counter(warning for row in now for warning in row["source_warnings"])),
        "now_received_on_later_utc_date": sum(row["trigger_timing"]["received_on_later_utc_date"] for row in now),
        "now_by_trigger_update_kind": dict(Counter(kind for row in now for kind in row["trigger_timing"]["update_kinds"])),
        "target_chat_messages_outside_inventory_retained_in_raw": sorted(set(history) - set(inventory_messages)),
        "raw_now_messages_missing_from_catalog": [row["message_id"] for row in messages if row["blockers"]],
        "coverage_screening": {str(minutes): dict(Counter(check["status"] for row in now for check in row["coverage_screening"]
                                                        if check["screening_horizon_minutes"] == minutes)) for minutes in horizons},
    }
    return {"schema_version": 1, "status": "diagnostic_only_not_admitted", "summary": summary,
            "scope": {"chat_id": gold_chat_id(manifest), "period": period,
                      "cutoff_utc_exclusive": cutoff.isoformat(), "execution_evidence_loaded": False,
                      "coverage_screening_minutes_not_policy_parameters": list(horizons), "strategy_simulations_run": 0},
            "signals": signals, "messages": messages,
            "global_gates": ["channel_history_completeness_unproven", "historical_data_not_untouched_oos",
                             "policy_capital_exposure_and_horizon_not_frozen", "broker_money_contract_not_admitted",
                             "full_tick_sequence_admission_pending", "execution_model_not_certified"],
            "limitations": ["Observed receipts do not reconstruct deleted or unobserved original messages.",
                            "Catalog execution counts are empty because only raw provider observations were supplied; they do not mean trades were unexecuted.",
                            "Coverage screening checks declared source bounds, not actual intraday continuity or executable fills.",
                            "Missing provider levels are policy-dependent gaps, not permission to synthesize them."]}


def resolved_tick_conflicts(path: Path, manifest: dict, source_manifest: Path) -> set:
    report = json.loads(path.read_text(encoding="utf-8-sig"))
    unsigned = dict(report)
    expected = unsigned.pop("report_identity_sha256", None)
    encoded = json.dumps(unsigned, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
    if hashlib.sha256(encoded).hexdigest() != expected:
        raise ValueError("tick diagnostics identity mismatch")
    if report["input_manifest"]["observed_file_sha256"] != file_identity(source_manifest)["sha256"]:
        raise ValueError("tick diagnostics refer to a different source manifest")
    selected = {(row["day"], row["symbol"]): row for row in manifest["tick_source_selection"]["selected_records"]}
    conflicts = {(row["day"], row["symbol"]): row for row in manifest["tick_source_selection"]["conflicting_consistent_duplicate_groups"]}
    resolved = set()
    for group in report["per_day_results"]:
        key = (group["day"], group["symbol"])
        if key not in conflicts:
            raise ValueError("tick diagnostic group outside manifest conflicts")
        record = selected[key]
        proof_by_path = {item["manifest_record_original"]["path"]: item for item in group["candidate_results"]}
        expected_candidates = conflicts[key]["candidates"]
        if len(proof_by_path) != len(expected_candidates):
            raise ValueError("incomplete tick alternative proof")
        for candidate in expected_candidates:
            item = proof_by_path.get(candidate["path"])
            if item is None or item["observed"]["parquet_sha256"] != candidate["sha256"] or item["observed"]["sidecar_sha256"] != candidate["metadata_sha256"]:
                raise ValueError("tick diagnostic candidate identity mismatch")
        selected_proof = proof_by_path.get(record["path"])
        comparisons = group["pairwise_comparisons"]
        count = len(expected_candidates)
        complete_pairs = {tuple(sorted((pair["left_candidate_index"], pair["right_candidate_index"]))) for pair in comparisons}
        if complete_pairs != {(left, right) for left in range(count) for right in range(left + 1, count)}:
            raise ValueError("incomplete pairwise tick alternative proof")
        if (selected_proof and selected_proof["admission"]["admissible_with_own_parquet_and_own_sidecar"]
                and group["resolution"]["exact_full_sequence_equal"]
                and all(pair["semantic"]["relationship"] == "exact_ordered_semantic_equal"
                        and pair["all_columns_values_and_dtypes_equal"] for pair in comparisons)):
            resolved.add(key)
    return resolved


def write_immutable(path: Path, payload: bytes) -> None:
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError(f"immutable output conflict: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)


def json_bytes(payload: object) -> bytes:
    return (json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode()


def run(source_manifest: Path, output_dir: Path, horizons: tuple[int, ...], tick_diagnostics: Path | None = None) -> dict:
    manifest = json.loads(source_manifest.read_text(encoding="utf-8-sig"))
    source_identity = validate_manifest(manifest)
    source = Path(manifest["input_sources"]["trade_events"]["path"])
    source_contract = manifest["input_sources"]["trade_events"]
    actual = file_identity(source)
    if any(actual[key] != source_contract[key] for key in ("bytes", "sha256")):
        raise ValueError("raw journal differs from frozen source manifest")
    cutoff = datetime.combine(date.fromisoformat(manifest["proposed_period"]["end_date_utc"]) + timedelta(days=1),
                              datetime.min.time(), timezone.utc)
    chat_id = gold_chat_id(manifest)
    # Recovered-source summaries can omit coverage; the hashed sidecar is authoritative.
    for record in manifest["tick_source_selection"]["selected_records"]:
        metadata_path = Path(record["metadata_path"])
        metadata_identity = file_identity(metadata_path)
        if metadata_identity["sha256"] != record["metadata_sha256"]:
            raise ValueError("tick metadata differs from source manifest")
        if file_identity(Path(record["path"]))["sha256"] != record["sha256"]:
            raise ValueError("tick file differs from source manifest")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        record["coverage"] = metadata.get("coverage")
    resolved = resolved_tick_conflicts(tick_diagnostics, manifest, source_manifest) if tick_diagnostics else set()
    with source.open(encoding="utf-8-sig") as stream:
        selected, history, selection = select_observations((json.loads(line) for line in stream if line.strip()),
                                                           chat_id=chat_id, cutoff=cutoff)
    catalog = provider_signal_catalog.build_catalog_report(selected, [])
    catalog["foundation_scope"] = {"chat_id": chat_id, "cutoff_utc_exclusive": cutoff.isoformat(),
                                    "execution_evidence_loaded": False, "not_study_admission": True}
    readiness = build_readiness(catalog, history, manifest, cutoff=cutoff, horizons=horizons, resolved_conflicts=resolved)
    source_code = [Path(__file__), Path(provider_signal_catalog.__file__),
                   Path(provider_signal_catalog.__file__).with_name("parser.py"),
                   Path(provider_signal_catalog.__file__).with_name("interpretation_firewall.py"),
                   Path(provider_signal_catalog.__file__).with_name("runtime_paths.py"),
                   Path(__file__).with_name("audit_causal_lineage.py")]
    readiness["source_contract"] = {"source_readiness_identity": source_identity,
                                     "source_manifest": file_identity(source_manifest), "raw_journal": actual,
                                     "code": [file_identity(path) for path in source_code], "selection": selection,
                                     "runtime": {"python": platform.python_version(), "platform": platform.platform()},
                                     "tick_diagnostics": file_identity(tick_diagnostics) if tick_diagnostics else None,
                                     "resolved_tick_conflicts": [{"day": day, "symbol": symbol} for day, symbol in sorted(resolved)]}
    readiness["package_identity_sha256"] = hashlib.sha256(json_bytes(readiness)).hexdigest()
    for name, payload in (("provider_catalog.json", catalog), ("signal_readiness.json", readiness)):
        write_immutable(output_dir / name, json_bytes(payload))
    raw_bytes = b"".join((json.dumps(row, ensure_ascii=True, separators=(",", ":")) + "\n").encode() for row in selected)
    write_immutable(output_dir / "scoped_telegram_raw.jsonl", raw_bytes)
    package = {"schema_version": 1, "identity": readiness["package_identity_sha256"],
               "files": [file_identity(output_dir / name) for name in ("provider_catalog.json", "signal_readiness.json", "scoped_telegram_raw.jsonl")],
               "source_contract": readiness["source_contract"], "simulation_runs": 0}
    write_immutable(output_dir / "package_manifest.json", json_bytes(package))
    print(json.dumps({"identity": package["identity"], "selection": selection, "summary": readiness["summary"]}, indent=2))
    return readiness


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--screen-horizon-minutes", action="append", type=int)
    parser.add_argument("--tick-diagnostics", type=Path)
    args = parser.parse_args()
    horizons = tuple(args.screen_horizon_minutes or (240, 1440))
    if any(minutes <= 0 for minutes in horizons) or len(set(horizons)) != len(horizons):
        parser.error("screening horizons must be unique positive minutes")
    run(args.source_manifest, args.output_dir, horizons, args.tick_diagnostics)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
