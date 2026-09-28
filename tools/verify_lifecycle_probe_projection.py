"""Verify bounded probe recaptures preserve every old event and source byte."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.audit_native_money_anchor import digest, read
from tools.probe_vm_signal_lifecycle import FIELDS


DETAIL_FIELDS = {"symbol", "magic", "position_type", "price_open", "price_current", "comment"}
REQUIRED_OPEN_DETAILS = {"symbol", "magic", "position_type", "price_open", "price_current"}
SNAPSHOT_INTERVAL_FIELDS = {"positions_read_started_utc", "positions_read_completed_utc",
                            "positions_read_elapsed_ms"}
REQUEST_DERIVED = {f"request_{key}" for key in
                   ("sl", "tp", "type", "action", "position", "volume")}
RESULT_DERIVED = {f"result_{key}" for key in ("retcode", "deal", "order", "price", "volume")}


def allowed_schema_additions(kind, inventory):
    allowed = (FIELDS & inventory) - {"declared_action_ids"}
    if kind == "bot_internal_decision" and "declared_action_ids" in inventory:
        allowed.add("declared_action_ids")
    if kind == "mt5_action_attempt":
        if "request" in inventory:
            allowed |= REQUEST_DERIVED
        if "result" in inventory:
            allowed |= RESULT_DERIVED
    if kind == "mt5_position_snapshot":
        allowed |= SNAPSHOT_INTERVAL_FIELDS & inventory
    return allowed


def validate_snapshot_interval(row):
    present = SNAPSHOT_INTERVAL_FIELDS & row.keys()
    if not present:
        return
    if present != SNAPSHOT_INTERVAL_FIELDS:
        raise ValueError("native snapshot read interval incomplete")
    try:
        started = datetime.fromisoformat(row["positions_read_started_utc"].replace("Z", "+00:00"))
        completed = datetime.fromisoformat(row["positions_read_completed_utc"].replace("Z", "+00:00"))
    except (TypeError, AttributeError, ValueError) as exc:
        raise ValueError("native snapshot read interval invalid") from exc
    elapsed = row["positions_read_elapsed_ms"]
    if (started.tzinfo is None or completed.tzinfo is None
            or started.utcoffset() != timedelta(0) or completed.utcoffset() != timedelta(0)
            or started > completed or type(elapsed) not in (int, float)
            or not math.isfinite(elapsed) or elapsed < 0):
        raise ValueError("native snapshot read interval invalid")


def compare_projection(old, new, *, restore_native_details=False,
                       allow_schema_expansion=False):
    if (old.get("contract") != "bounded_vm_signal_lifecycle_probe_v1"
            or new.get("contract") != old["contract"]
            or old.get("status") != "diagnostic_only"
            or new.get("status") != "diagnostic_only"):
        raise ValueError("bounded lifecycle probe contract mismatch")
    if old.get("source_window_sha256") != new.get("source_window_sha256"):
        raise ValueError("source window hash changed")
    for key in ("window", "scope_start_offset", "scope_end_offset",
                "scanned_offset_start", "scanned_offset_end", "scanned_bytes",
                "event_counts", "sampled_kinds", "field_names_by_kind",
                "decision_status_counts", "action_attempt_status_counts"):
        if old.get(key) != new.get(key):
            raise ValueError(f"bounded probe source or inventory changed: {key}")
    if new.get("source_file_size_at_start", 0) < old.get("source_file_size_at_start", 0):
        raise ValueError("journal size regressed")
    inventory = set(old.get("field_names_by_kind", {}).get("mt5_position_snapshot", []))
    if "position_exists" not in inventory:
        raise ValueError("source field inventory lacks position_exists")
    before, after = old.get("events", []), new.get("events", [])
    old_ids = [row.get("event_id") for row in before]
    new_ids = [row.get("event_id") for row in after]
    if (old_ids != new_ids or not all(isinstance(value, str) and value for value in old_ids)
            or len(set(old_ids)) != len(old_ids)):
        raise ValueError("event identity or order changed")
    expected = old["event_counts"].get("mt5_position_snapshot", 0)
    snapshots = [row for row in after if row.get("ev") == "mt5_position_snapshot"]
    if not expected or len(snapshots) != expected:
        raise ValueError("snapshot capture truncated or missing")
    detailed = 0
    expanded_events = 0
    for prior, current in zip(before, after, strict=True):
        for key, value in prior.items():
            if key not in current or current[key] != value:
                raise ValueError(f"old event field changed: {prior['event_id']}/{key}")
        kind = prior.get("ev")
        snapshot = kind == "mt5_position_snapshot"
        if snapshot and "position_exists" not in current:
            raise ValueError(f"position_exists missing: {prior['event_id']}")
        added = set(current) - set(prior)
        native_allowed = ({"position_exists"} if snapshot and "position_exists" not in prior else set())
        if restore_native_details and snapshot:
            native_allowed |= (DETAIL_FIELDS & inventory) - set(prior)
        allowed = set(native_allowed)
        if allow_schema_expansion:
            source_fields = set(old.get("field_names_by_kind", {}).get(kind, []))
            allowed |= allowed_schema_additions(kind, source_fields)
        if (not added <= allowed or not allow_schema_expansion and not restore_native_details
                and added != allowed):
            raise ValueError(f"unexpected added field: {prior['event_id']}/{sorted(added - allowed)}")
        if added - native_allowed:
            expanded_events += 1
        if snapshot and type(current["position_exists"]) not in (bool, type(None)):
            raise ValueError("position_exists missing or not boolean/null")
        if snapshot:
            validate_snapshot_interval(current)
        if restore_native_details and snapshot:
            if current["position_exists"] is True and REQUIRED_OPEN_DETAILS <= inventory:
                if not REQUIRED_OPEN_DETAILS <= set(current):
                    raise ValueError("native open-position detail missing")
                if (not isinstance(current["symbol"], str) or not current["symbol"]
                        or type(current["magic"]) is not int
                        or type(current["position_type"]) is not int
                        or any(type(current[key]) not in (int, float)
                               or not math.isfinite(current[key]) or current[key] <= 0
                               for key in ("price_open", "price_current"))):
                    raise ValueError("native open-position detail invalid")
                detailed += 1
            elif current["position_exists"] is not True and added & DETAIL_FIELDS:
                raise ValueError("closed or unknown snapshot added position detail")
    values = [row["position_exists"] for row in snapshots]
    return {"contract": ("lifecycle_probe_native_projection_verification_v3"
                         if allow_schema_expansion else
                         "lifecycle_probe_native_projection_verification_v2"
                         if restore_native_details else "lifecycle_probe_projection_verification_v1"),
            "status": "projection_verified", "signal_id": old["window"]["signal_id"],
            "source_window_sha256": old["source_window_sha256"],
            "snapshot_count": len(values),
            "position_exists_true": sum(value is True for value in values),
            "position_exists_false": sum(value is False for value in values),
            "position_exists_unknown": sum(value is None for value in values),
            "native_open_position_detail_count": detailed,
            "schema_expansion_event_count": expanded_events,
            "broker_install_time_verified": False,
            "limitations": ["Restored values describe bounded snapshot reads, not continuous position state.",
                            "A snapshot journal timestamp is not the precise broker read or installation time."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old", type=Path, required=True)
    parser.add_argument("--new", type=Path, required=True)
    parser.add_argument("--restore-native-details", action="store_true")
    parser.add_argument("--allow-schema-expansion", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    old, new = read(args.old), read(args.new)
    current_extractor = Path(__file__).resolve().with_name("probe_vm_signal_lifecycle.py")
    sources = new.get("local_inputs_sha256", {})
    matches = [sha for name, sha in sources.items()
               if Path(name).resolve() == current_extractor.resolve()]
    if len(matches) != 1 or matches[0] != digest(current_extractor):
        raise ValueError("recapture was not made with current extractor")
    report = compare_projection(old, new, restore_native_details=args.restore_native_details,
                                allow_schema_expansion=args.allow_schema_expansion)
    report["inputs_sha256"] = {str(args.old): digest(args.old),
                               str(args.new): digest(args.new),
                               str(current_extractor): digest(current_extractor),
                               str(Path(__file__).resolve()): digest(Path(__file__))}
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"signal": report["signal_id"], "snapshots": report["snapshot_count"],
                      "position_exists_true": report["position_exists_true"]}), flush=True)


if __name__ == "__main__":
    main()
