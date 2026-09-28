"""Explain changes from retrospective groups without changing entry decisions."""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.dubai_annual_coverage import digest, read
from research.dubai_annual_universe import load_catalog
from research.dubai_entry_stream import make_events
from research.telegram_export import _hash, _json_bytes, _utc


def explain(directory, catalog_dir):
    directory = Path(directory)
    manifest = read(directory / "manifest.json")
    if _hash({k: v for k, v in manifest.items() if k != "stream_identity_sha256"}) != manifest["stream_identity_sha256"]:
        raise ValueError("stream identity mismatch")
    for name, proof in manifest["artifacts"].items():
        if Path(name).name != name or digest(directory / name) != proof["sha256"]:
            raise ValueError("stream artifact mismatch")
    protocol = read(directory / "protocol.json")
    catalog, messages = load_catalog(catalog_dir)
    if catalog["catalog_identity_sha256"] != protocol["catalog_identity_sha256"]:
        raise ValueError("mixed source catalog")
    events, _, _ = make_events(messages, protocol, "revision_time")
    event_map = {e["event_id"]: e for e in events}
    assignments = {}
    for line in (directory / "decisions.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["scenario"] == "revision_time" and row["action"] in ("new_entry", "pair_complement"):
            assignments[row["message_id"]] = {**event_map[row["event_id"]], "action": row["action"], "trigger_id": row["trigger_id"]}
    output = []
    for line in (directory / "legacy_lineage.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["scenario"] != "revision_time" or row["same_directional_membership"]:
            continue
        components = [assignments[mid] for mid in row["legacy_message_ids"] if mid in assignments]
        components.sort(key=lambda e: (_utc(e["available_utc"]), e["message_id"]))
        times = [_utc(e["available_utc"]) for e in components]
        pubs = [_utc(e["published_utc"]) for e in components]
        availability_span = (max(times) - min(times)).total_seconds() if times else None
        publication_span = (max(pubs) - min(pubs)).total_seconds() if pubs else None
        members = set(row["legacy_message_ids"])
        intervening = [e["message_id"] for e in assignments.values() if e["message_id"] not in members
                       and times and min(times) <= _utc(e["available_utc"]) <= max(times)]
        facts = []
        if availability_span is not None and availability_span > protocol["pair_seconds"]:
            facts.append("component_availability_span_over_pair_window")
        if publication_span is not None and publication_span > protocol["pair_seconds"]:
            facts.append("component_publication_span_over_pair_window")
        if len({_hash(e["origin"]) for e in components}) > 1:
            facts.append("different_forward_origins")
        if len(set(times)) < len(times):
            facts.append("simultaneous_component_availability")
        if any(count > 1 for count in Counter(e["kind"] for e in components).values()):
            facts.append("more_than_one_component_of_the_same_form")
        if intervening:
            facts.append("other_eligible_messages_between_component_times")
        if row["other_legacy_entries_merged"]:
            facts.append("online_pair_includes_another_retrospective_group")
        if row["messages_without_assignment"]:
            facts.append("legacy_component_not_assigned")
        output.append({"legacy_entry_id": row["legacy_entry_id"], "status": row["status"],
            "trigger_ids": row["trigger_ids"], "facts_not_performance_selection": facts,
            "availability_span_seconds": availability_span, "publication_span_seconds": publication_span,
            "intervening_eligible_message_ids": sorted(set(intervening)),
            "other_legacy_entries_merged": row["other_legacy_entries_merged"],
            "components": [{k: e[k] for k in ("message_id", "published_utc", "available_utc", "kind", "direction", "origin", "trigger_id", "action")} for e in components]})
    return {"schema_version": "entry_stream_relationship_diagnostics_v1", "stream_identity_sha256": manifest["stream_identity_sha256"],
        "stream_manifest_sha256": digest(directory / "manifest.json"), "implementation_sha256": digest(__file__),
        "summary": {"changed_legacy_groups": len(output), "groups_without_explanatory_fact": sum(not r["facts_not_performance_selection"] for r in output),
            "fact_counts_not_disjoint": dict(Counter(fact for r in output for fact in r["facts_not_performance_selection"]))},
        "rows": output}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stream", required=True, type=Path)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve().is_relative_to(args.stream.resolve()) or args.output.resolve().is_relative_to(args.catalog.resolve()):
        raise ValueError("output exists or would alter a frozen archive")
    result = explain(args.stream, args.catalog)
    with args.output.open("xb") as stream:
        stream.write(_json_bytes(result))
    print(json.dumps(result["summary"]), flush=True)


if __name__ == "__main__":
    main()
