"""Collect one bounded material-event segment from the read-only VM journal."""

from __future__ import annotations

import argparse
import base64
from collections import Counter
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.probe_vm_signal_lifecycle import MAX_SECONDS, digest, utc
from tools.audit_journal_chunk_index import chunks_for_window
from tools.collect_vm_journal_index import WORKER as INDEX_WORKER, assemble_index


MAX_SEGMENTS = 800
WORKER = Path(__file__).with_name("probe_vm_signal_lifecycle.py")
MAX_MERGED_MATERIAL_EVENTS = 10_000
MAX_MERGED_MANAGEMENT_EVENTS = 25_000
MAX_MERGED_MANAGEMENT_BYTES = 32_000_000


def build_segments(plan):
    if plan.get("contract") != "week_native_lifecycle_window_plan_v1" or plan.get("native_basket_count") != 40:
        raise ValueError("unexpected lifecycle plan contract or denominator")
    spans = []
    for row in plan["rows"]:
        windows = row["segments"]
        if not windows:
            raise ValueError("native basket has no planned lifecycle interval")
        start, end = utc(windows[0]["start_utc"]), utc(windows[-1]["end_utc"])
        if not start < end:
            raise ValueError("native basket interval invalid")
        spans.append((start, end, row["signal_id"]))
    if len(spans) != 40 or len({item[2] for item in spans}) != 40:
        raise ValueError("lifecycle span identity mismatch")
    merged = []
    for start, end, _ in sorted(spans):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    segments = []
    for start, end in merged:
        cursor = start
        while cursor < end:
            stop = min(cursor + timedelta(minutes=5), end)
            active = sorted(signal for lower, upper, signal in spans if lower < stop and cursor < upper)
            if not active:
                raise ValueError("material segment without native basket")
            segments.append({"start_utc": cursor.isoformat(timespec="milliseconds"),
                             "end_utc": stop.isoformat(timespec="milliseconds"),
                             "active_signals": active})
            if len(segments) > MAX_SEGMENTS:
                raise ValueError("material segment budget exceeded")
            cursor = stop
    return segments


def validate_segment_result(result, segment):
    contract = result.get("contract")
    if (contract not in {"bounded_vm_week_material_segment_v2",
                         "bounded_vm_week_material_segment_v3"}
            or result.get("segment") != segment
            or result.get("material_records_complete_within_segment") is not True
            or result.get("management_records_complete_within_segment") is not True
            or contract == "bounded_vm_week_material_segment_v3"
            and result.get("indexed_prefix_time_coverage_verified") is not True
            or sum(row["count"] for row in result["material_event_counts"]) != len(result["events"])
            or sum(row["count"] for row in result["management_event_counts"]) != len(result["management_events"])):
        raise ValueError("remote material or management segment contract mismatch")
    start, end = utc(segment["start_utc"]), utc(segment["end_utc"])
    for key in ("events", "management_events"):
        rows = result[key]
        ids = [row.get("event_id") for row in rows]
        if (not all(isinstance(value, str) and value for value in ids)
                or len(ids) != len(set(ids))
                or any(row.get("sig") not in segment["active_signals"]
                       or not start <= utc(row["ts"]) < end for row in rows)):
            raise ValueError(f"remote {key} identity or interval mismatch")


def aggregate_indexed_segment(segment, index_chunks, chunk_reports):
    selected = chunks_for_window(index_chunks, segment["start_utc"], segment["end_utc"])
    by_start = {report.get("scanned_offset_start"): report for report in chunk_reports}
    if (len(by_start) != len(chunk_reports)
            or set(by_start) != {chunk["actual_start"] for chunk in selected}):
        raise ValueError("indexed segment has missing or extraneous chunk reports")
    total_counts, material_counts, management_counts = Counter(), Counter(), Counter()
    events, management, selected_proofs = [], [], []
    scanned_bytes = 0
    for chunk in selected:
        report = by_start[chunk["actual_start"]]
        if (report.get("contract") != "bounded_vm_week_material_chunk_v3"
                or report.get("segment") != segment
                or report.get("records_complete_within_indexed_chunk") is not True
                or report.get("source") != chunk["source"]
                or report.get("scanned_offset_end") != chunk["actual_end"]
                or report.get("source_window_sha256") != chunk["sha256"]
                or report.get("scanned_bytes") != chunk["actual_end"] - chunk["actual_start"]):
            raise ValueError("indexed material chunk proof inconsistent")
        for target, key in ((total_counts, "event_counts"),
                            (material_counts, "material_event_counts"),
                            (management_counts, "management_event_counts")):
            for row in report[key]:
                if (type(row.get("count")) is not int or row["count"] <= 0
                        or row.get("signal_id") not in segment["active_signals"]):
                    raise ValueError("indexed material chunk counts invalid")
                target[(row["signal_id"], row["event"])] += row["count"]
        events.extend(report["events"])
        management.extend(report["management_events"])
        scanned_bytes += report["scanned_bytes"]
        selected_proofs.append({"actual_start": chunk["actual_start"],
                                "actual_end": chunk["actual_end"], "sha256": chunk["sha256"]})
    if (scanned_bytes > 64_000_000 or len(events) > MAX_MERGED_MATERIAL_EVENTS
            or len(management) > MAX_MERGED_MANAGEMENT_EVENTS
            or len(json.dumps(management, ensure_ascii=True).encode("utf-8"))
            > MAX_MERGED_MANAGEMENT_BYTES
            or sum(material_counts.values()) != len(events)
            or sum(management_counts.values()) != len(management)):
        raise ValueError("indexed segment output or byte budget exceeded")
    index_sha = hashlib.sha256(json.dumps(index_chunks, sort_keys=True,
                                          separators=(",", ":")).encode("utf-8")).hexdigest()
    def count_rows(values):
        return [{"signal_id": signal, "event": event, "count": count}
                for (signal, event), count in sorted(values.items())]
    result = {"contract": "bounded_vm_week_material_segment_v3", "status": "diagnostic_only",
              "segment": segment, "source": index_chunks[0]["source"],
              "indexed_prefix_end": index_chunks[0]["prefix_end"],
              "index_chunk_count": len(index_chunks), "index_sha256": index_sha,
              "selected_chunks": selected_proofs, "scanned_bytes": scanned_bytes,
              "event_counts": count_rows(total_counts),
              "material_event_counts": count_rows(material_counts), "events": events,
              "management_event_counts": count_rows(management_counts),
              "management_events": management,
              "material_records_complete_within_segment": True,
              "management_records_complete_within_segment": True,
              "indexed_prefix_time_coverage_verified": True,
              "full_source_time_coverage_verified": False,
              "limitations": ["The indexed prefix must be a frozen, complete source for the requested week.",
                              "A complete byte prefix does not prove no later event was appended for this week.",
                              "Allowlisted journal records are not broker execution or risk-path parity."]}
    validate_segment_result(result, segment)
    return result


def run_remote_material(spec, host, identity):
    encoded = base64.b64encode(json.dumps(spec, separators=(",", ":")).encode("ascii")).decode("ascii")
    command = ["ssh", "-i", str(identity), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
               "-o", "ConnectTimeout=5", host, f"python - --material-worker {encoded}"]
    run = subprocess.run(command, input=WORKER.read_bytes(), capture_output=True,
                         timeout=MAX_SECONDS + 15, check=False)
    if run.returncode or run.stderr:
        last_line = run.stderr.decode("utf-8", errors="replace").splitlines()[-1:] or [""]
        detail = last_line[0] if re.fullmatch(r"(?:ValueError|TimeoutError|RuntimeError): [\w -]{1,120}", last_line[0]) else "unclassified remote error"
        raise RuntimeError(f"bounded material collection failed (exit={run.returncode}, {detail}); no report written")
    return json.loads(run.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--segment-index", type=int)
    parser.add_argument("--remote-source")
    parser.add_argument("--host")
    parser.add_argument("--identity", type=Path)
    parser.add_argument("--scope-start-offset", type=int)
    parser.add_argument("--scope-end-offset", type=int)
    parser.add_argument("--index-manifest", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    segments = build_segments(plan)
    if args.segment_index is None:
        print(json.dumps({"segment_count": len(segments),
                          "first_utc": segments[0]["start_utc"],
                          "last_utc": segments[-1]["end_utc"]}))
        return
    if (not 0 <= args.segment_index < len(segments)
            or any(value is None for value in (args.remote_source, args.host, args.identity, args.output))
            or args.index_manifest is None and (args.scope_start_offset is None or args.scope_end_offset is None)
            or args.index_manifest is not None and (args.scope_start_offset is not None or args.scope_end_offset is not None)):
        raise ValueError("remote material segment arguments or index invalid")
    if args.output.exists():
        raise FileExistsError(args.output)
    segment = segments[args.segment_index]
    if args.index_manifest is None:
        spec = {"source": args.remote_source, "scope_start_offset": args.scope_start_offset,
                "scope_end_offset": args.scope_end_offset, "segment": segment}
        result = run_remote_material(spec, args.host, args.identity)
    else:
        manifest = json.loads(args.index_manifest.read_text(encoding="utf-8"))
        if (manifest.get("contract") != "journal_prefix_chunk_index_v1"
                or manifest.get("source") != args.remote_source
                or manifest.get("worker_sha256") != digest(INDEX_WORKER)):
            raise ValueError("journal prefix index manifest identity or worker mismatch")
        checked = assemble_index(manifest["chunks"], source=args.remote_source,
                                 prefix_end=manifest["prefix_end"],
                                 chunk_bytes=manifest["chunk_bytes"])
        if manifest != checked:
            raise ValueError("journal prefix index manifest contract mismatch")
        selected = chunks_for_window(manifest["chunks"], segment["start_utc"], segment["end_utc"])
        if sum(chunk["actual_end"] - chunk["actual_start"] for chunk in selected) > 64_000_000:
            raise ValueError("indexed material segment read budget exceeded")
        reports = [run_remote_material({"source": args.remote_source,
                                       "scope_start_offset": chunk["actual_start"],
                                       "scope_end_offset": chunk["actual_end"],
                                       "indexed_chunk_sha256": chunk["sha256"],
                                       "segment": segment}, args.host, args.identity)
                   for chunk in selected]
        result = aggregate_indexed_segment(segment, manifest["chunks"], reports)
    validate_segment_result(result, segment)
    result["segment_index"] = args.segment_index
    result["segment_count"] = len(segments)
    inputs = [args.plan, WORKER, Path(__file__)]
    if args.index_manifest is not None:
        inputs.extend((args.index_manifest, INDEX_WORKER))
    result["local_inputs_sha256"] = {str(path): digest(path) for path in inputs}
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(result, target, indent=2, ensure_ascii=True)
        target.write("\n")
    print(json.dumps({"segment_index": args.segment_index, "scanned_bytes": result["scanned_bytes"],
                      "active_signals": len(segment["active_signals"]), "material_events": len(result["events"])}))


if __name__ == "__main__":
    main()
