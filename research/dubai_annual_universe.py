"""Offline, reviewed entry hypotheses and rolling cohorts; never engine admission."""

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

from research import telegram_export


def _bytes(value):
    return telegram_export._json_bytes(value)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _read(path):
    return json.loads(Path(path).read_bytes(),
                      object_pairs_hook=telegram_export._unique_object,
                      parse_constant=telegram_export._invalid_constant)


def _time(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        raise ValueError("UTC timestamp required")
    return result


def _utc(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def load_catalog(directory):
    directory = Path(directory).resolve()
    manifest = _read(directory / "manifest.json")
    identity = manifest.pop("catalog_identity_sha256")
    if _sha(_bytes(manifest)) != identity:
        raise ValueError("catalog identity mismatch")
    manifest["catalog_identity_sha256"] = identity
    expected = {"summary.json", "messages.jsonl", "sticker_signals.jsonl", "text_entries.jsonl"}
    if set(manifest["artifacts"]) != expected:
        raise ValueError("unexpected catalog artifacts")
    for name, spec in manifest["artifacts"].items():
        data = (directory / name).read_bytes()
        if len(data) != spec["bytes"] or _sha(data) != spec["sha256"]:
            raise ValueError("catalog artifact mismatch")
    inputs = manifest["inputs"]
    evidence = [*inputs["source_exports"], *inputs["media"],
                {"path": inputs["labels_path"], "sha256": inputs["labels_sha256"]},
                {"path": str(Path(inputs["admission_dir"]) / "manifest.json"),
                 "sha256": inputs["admission_manifest_sha256"]}]
    for item in evidence:
        if not item["sha256"] or _sha(Path(item["path"]).read_bytes()) != item["sha256"]:
            raise ValueError("catalog source evidence mismatch")
    telegram_export.load_admission(inputs["admission_dir"])
    rows = [json.loads(line, object_pairs_hook=telegram_export._unique_object,
                       parse_constant=telegram_export._invalid_constant)
            for line in (directory / "messages.jsonl").read_bytes().splitlines()]
    return manifest, rows


def resolve_entries(rows, ledger):
    """Apply a frozen retrospective grouping policy, keeping every source row.

    Group membership may use later context. Publication references and known
    snapshot times are diagnostics, not permission to backdate that context.
    """
    by_id = {row["message_id"]: row for row in rows}
    if len(by_id) != len(rows) or any(type(mid) is not int for mid in by_id):
        raise ValueError("invalid or duplicate message identity")
    if any(row["chat_id"] != 1642806869 for row in rows):
        raise ValueError("wrong channel")
    overrides = {}
    for decision in ledger["decisions"]:
        mid = decision["message_id"]
        if mid in overrides or mid not in by_id or not decision.get("reason"):
            raise ValueError("invalid or duplicate review decision")
        if not decision.get("evidence_ids") or not set(decision["evidence_ids"]) <= by_id.keys():
            raise ValueError("review evidence missing")
        if "direction" in decision:
            hashes = decision.get("reviewed_snapshot_sha256", [])
            available = {snapshot["raw_snapshot_sha256"] for snapshot in by_id[mid]["snapshots"]}
            if (decision["direction"] not in ("BUY", "SELL") or not hashes
                    or set(hashes) != available or by_id[mid]["kind"] != "directional_text_review"):
                raise ValueError("text label must bind every reviewed snapshot")
        overrides[mid] = decision
    assignments = {}
    for row in rows:
        mid = row["message_id"]
        decision = overrides.get(mid)
        if decision:
            assignments[mid] = dict(decision, basis="manual_retrospective_review")
        elif row["kind"] == "gold_sticker":
            assignments[mid] = {"message_id": mid, "action": "entry", "target_id": mid,
                "basis": "verified_gold_sticker", "reason": "Verified GOLD directional media bytes."}
        elif row["kind"] == "text_now_candidate":
            if row["review_reasons"] or not row["companion_proposal"]:
                raise ValueError(f"unreviewed text relationship: {mid}")
            target = int(row["companion_proposal"]["signal_id"].rsplit(":", 1)[1])
            assignments[mid] = {"message_id": mid, "action": "attach", "target_id": target,
                "basis": "declared_nearby_pair_hypothesis", "reason": "First clear NOW complement under catalog proposal rules."}
        else:
            assignments[mid] = {"message_id": mid, "action": "retain", "target_id": None,
                "basis": "outside_self_contained_entry_scope",
                "reason": "Preserved; not established as an independent explicit GOLD entry."}
    groups = defaultdict(list)
    for mid, decision in assignments.items():
        if decision["action"] not in ("entry", "attach", "context", "retain"):
            raise ValueError("invalid review action")
        target = decision.get("target_id")
        if decision["action"] == "entry" and target != mid:
            raise ValueError("entry must target itself")
        if decision["action"] in ("entry", "attach", "context"):
            if target not in assignments or assignments[target]["action"] != "entry":
                raise ValueError("target must be a root entry, not a chain or cycle")
            groups[target].append(mid)
        elif target is not None:
            raise ValueError("retained message cannot target an entry")
    entries = []
    for root, members in groups.items():
        directional = [mid for mid in members if assignments[mid]["action"] != "context"]
        directions = {overrides.get(mid, {}).get("direction", by_id[mid]["direction"]) for mid in directional}
        if len(directions) != 1 or not directions <= {"BUY", "SELL"}:
            raise ValueError("group direction missing or conflicting")
        direction = next(iter(directions))
        publications, known_initial, known_revision, issues = [], [], [], set()
        for mid in directional:
            row = by_id[mid]
            decision = overrides.get(mid, {})
            issues.update(row["issues"])
            if not row["published_utc"]:
                raise ValueError("entry publication unresolved")
            _time(row["published_utc"])
            publications.append((row["published_utc"], mid))
            for snapshot in row["snapshots"]:
                # Extra textual labels bind individual existing snapshots, not
                # future messages or a whole identity with changing contents.
                labeled = snapshot["raw_snapshot_sha256"] in decision.get("reviewed_snapshot_sha256", [])
                snapshot_direction = direction if labeled else snapshot["direction"]
                if snapshot_direction != direction:
                    continue
                if not snapshot["has_edit_marker"]:
                    known_initial.append((snapshot["published_utc"], mid))
                elif snapshot["edited_utc"]:
                    known_revision.append((snapshot["edited_utc"], mid))
        reference, reference_id = min(publications)
        initial = min(known_initial) if known_initial else None
        revision = min([*known_initial, *known_revision]) if known_initial or known_revision else None
        notes = sorted({flag for mid in members for flag in assignments[mid].get("flags", [])})
        origins = {_bytes(origin) for mid in members for origin in by_id[mid]["forward_origins"]}
        if len(origins) > 1:
            notes.append("mixed_forward_origins")
        entries.append({"entry_id": f"dubai_annual:{root}", "root_message_id": root,
            "message_ids": sorted(members), "directional_message_ids": sorted(directional),
            "chat_id": 1642806869, "symbol": "XAUUSD", "direction": direction,
            "publication_reference_utc": reference, "publication_reference_message_id": reference_id,
            "known_unedited_component_utc": initial[0] if initial and not issues else None,
            "known_unedited_component_message_id": initial[1] if initial and not issues else None,
            "known_revision_or_initial_component_utc": revision[0] if revision and not issues else None,
            "initial_at_reference_supported": bool(initial and initial[0] == reference and not issues),
            "received_utc": None, "trigger_utc": None, "engine_admitted": False,
            "grouping_status": "retrospective_working_hypothesis",
            "forwarded": any(by_id[mid]["forwarded"] for mid in members),
            "forward_origins": [json.loads(origin) for origin in sorted(origins)],
            "review_flags": sorted(set(notes)), "integrity_issues": sorted(issues),
            "open_gates": ["online_dedup_policy", "scenario_clock_and_edits", "bid_ask_horizon_and_money"],
            "evidence_signal_ids": [by_id[mid]["signal_id"] for mid in sorted(members)]})
    entries.sort(key=lambda row: (row["publication_reference_utc"], row["root_message_id"]))
    return entries, [assignments[mid] for mid in sorted(assignments)]


def rolling_cohorts(entries, *, start, end_exclusive, development_days=56,
                    check_days=14, horizon_seconds=14400):
    for value in (development_days, check_days, horizon_seconds):
        if type(value) is not int or value <= 0:
            raise ValueError("positive integer rolling durations required")
    if horizon_seconds >= development_days * 86400:
        raise ValueError("horizon must be shorter than development window")
    start_time, end_time = _time(start), _time(end_exclusive)
    if end_time <= start_time + timedelta(days=development_days):
        raise ValueError("insufficient period for rolling check")
    if len({row["entry_id"] for row in entries}) != len(entries):
        raise ValueError("duplicate entry identity")
    times = {row["entry_id"]: _time(row["publication_reference_utc"]) for row in entries}
    if any(not start_time <= value < end_time for value in times.values()):
        raise ValueError("entry outside declared period")
    folds, cursor = [], start_time + timedelta(days=development_days)
    while cursor < end_time:
        dev_start = cursor - timedelta(days=development_days)
        nominal_end = cursor + timedelta(days=check_days)
        check_end = min(nominal_end, end_time)
        purge_start = cursor - timedelta(seconds=horizon_seconds)
        folds.append({"fold_id": f"rolling_{len(folds) + 1:02d}",
            "development_start_utc": _utc(dev_start), "development_end_exclusive_utc": _utc(cursor),
            "purge_start_utc": _utc(purge_start), "check_start_utc": _utc(cursor),
            "check_end_exclusive_utc": _utc(check_end), "nominal_check_end_utc": _utc(nominal_end),
            "complete_calendar_check": nominal_end <= end_time,
            "development_entry_ids": sorted(key for key, value in times.items() if dev_start <= value < purge_start),
            "purged_entry_ids": sorted(key for key, value in times.items() if purge_start <= value < cursor),
            "check_entry_ids": sorted(key for key, value in times.items() if cursor <= value < check_end),
            "status": "retrospective_cohort_not_fresh_oos", "engine_ready": False})
        cursor = nominal_end
    usage = []
    for entry in entries:
        key = entry["entry_id"]
        usage.append({"entry_id": key,
            "development_folds": [fold["fold_id"] for fold in folds if key in fold["development_entry_ids"]],
            "check_folds": [fold["fold_id"] for fold in folds if key in fold["check_entry_ids"]],
            "purged_folds": [fold["fold_id"] for fold in folds if key in fold["purged_entry_ids"]],
            "warmup_only": times[key] < start_time + timedelta(days=development_days)})
    return folds, usage


def build_universe(catalog_dir, ledger_path):
    manifest, rows = load_catalog(catalog_dir)
    ledger_path = Path(ledger_path).resolve()
    ledger = _read(ledger_path)
    if ledger.get("schema_version") != "dubai_annual_review_v1":
        raise ValueError("unsupported annual review")
    if ledger["catalog_identity_sha256"] != manifest["catalog_identity_sha256"]:
        raise ValueError("review bound to another catalog")
    entries, assignments = resolve_entries(rows, ledger)
    protocol = ledger["rolling_protocol"]
    folds, usage = rolling_cohorts(entries, **protocol)
    monthly = defaultdict(Counter)
    for entry in entries:
        count = monthly[entry["publication_reference_utc"][:7]]
        count["entry_hypotheses"] += 1
        count[entry["direction"]] += 1
        count["initial_at_reference_supported"] += entry["initial_at_reference_supported"]
        count["forwarded"] += entry["forwarded"]
        count["review_flagged"] += bool(entry["review_flags"])
    summary = {"source_messages": len(rows), "entry_hypotheses": len(entries),
        "message_assignment_counts": dict(Counter(row["action"] for row in assignments)),
        "initial_at_reference_supported": sum(row["initial_at_reference_supported"] for row in entries),
        "has_known_unedited_component": sum(row["known_unedited_component_utc"] is not None for row in entries),
        "review_flagged_entries": sum(bool(row["review_flags"]) for row in entries),
        "complete_rolling_checks": sum(row["complete_calendar_check"] for row in folds),
        "partial_rolling_checks": sum(not row["complete_calendar_check"] for row in folds),
        "warmup_only_entries": sum(row["warmup_only"] for row in usage),
        "engine_admitted": 0, "monthly": {key: dict(value) for key, value in sorted(monthly.items())}}
    return {"inputs": {"catalog_dir": str(Path(catalog_dir).resolve()),
            "catalog_identity_sha256": manifest["catalog_identity_sha256"],
            "catalog_manifest_sha256": _sha((Path(catalog_dir) / "manifest.json").read_bytes()),
            "ledger_path": str(ledger_path), "ledger_sha256": _sha(ledger_path.read_bytes())},
        "contract": {"schema_version": "dubai_annual_universe_v1", "timezone": "UTC",
            "entry_scope": ledger["entry_scope"], "rolling_protocol": protocol,
            "grouping_is_retrospective": True, "engine_dataset_ready": False,
            "provider_management_drives_strategy": False, "provider_context_used_for_review": True,
            "strategy_search_authorized_by_artifact": False, "fresh_oos": False,
            "availability_must_be_recomputed_for_selected_clock": True,
            "catalog_inputs": manifest["inputs"]},
        "implementation": {"module_sha256": _sha(Path(__file__).read_bytes())},
        "summary": summary, "entries": entries, "message_assignments": assignments,
        "rolling_folds": folds, "entry_fold_usage": usage}


def write_universe(universe, output_dir):
    output = Path(output_dir).resolve()
    inputs = universe["inputs"]
    catalog_inputs = universe["contract"]["catalog_inputs"]
    protected = {Path(inputs["ledger_path"]).resolve(), Path(catalog_inputs["labels_path"]).resolve()}
    protected.update(Path(row["path"]).resolve() for row in [*catalog_inputs["source_exports"], *catalog_inputs["media"]])
    if any(output.is_relative_to(Path(root).resolve()) for root in
           (inputs["catalog_dir"], catalog_inputs["admission_dir"])):
        raise ValueError("output inside frozen input archive")
    payloads = {"summary.json": _bytes(universe["summary"])}
    for key in ("entries", "message_assignments", "rolling_folds", "entry_fold_usage"):
        payloads[f"{key}.jsonl"] = b"".join(_bytes(row) for row in universe[key])
    manifest = {key: universe[key] for key in ("inputs", "contract", "implementation")}
    manifest["artifacts"] = {name: {"sha256": _sha(data), "bytes": len(data)} for name, data in payloads.items()}
    manifest["universe_identity_sha256"] = _sha(_bytes(manifest))
    payloads["manifest.json"] = _bytes(manifest)
    for name, data in payloads.items():
        target = output / name
        if target.resolve() in protected:
            raise ValueError("output would overwrite evidence")
        if target.exists() and target.read_bytes() != data:
            raise ValueError("immutable annual universe conflict")
    output.mkdir(parents=True, exist_ok=True)
    for name, data in payloads.items():
        if not (output / name).exists():
            with (output / name).open("xb") as stream:
                stream.write(data)
    return manifest
