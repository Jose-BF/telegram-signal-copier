"""Check frozen raw Telegram coverage before any independent weekly replay.

Observed signal IDs are used only after raw parsing as a coverage control,
never to select messages or to create the replay's signals.
"""

from __future__ import annotations

import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.causal_replay import RAW_FIELDS, WEEKLY_ENTRY_MAX_AGE_S, compile_signals, utc
from research.causal_text_admission import candidate_text_entries
from tools.audit_native_money_anchor import digest
from tools.run_causal_controls import STICKERS


MAX_SHADOW_ROWS = 10_000
MAX_RAW_ROWS = 20_000


def coverage(compiled_ids, received_ids):
    compiled, received = set(compiled_ids), set(received_ids)
    if len(compiled) != len(compiled_ids):
        raise ValueError("compiled signal IDs are duplicated")
    return {"received_signal_count": len(received),
            "compiled_signal_count": len(compiled),
            "received_missing_from_raw_replay": sorted(received - compiled),
            "raw_replay_not_received_by_bot": sorted(compiled - received),
            "all_received_signals_covered": bool(received) and received <= compiled}


def audit(raw_path, shadow_path, shadow_manifest_path, *, start_utc, end_utc):
    raw_path, shadow_path, shadow_manifest_path = map(
        Path, (raw_path, shadow_path, shadow_manifest_path))
    start, end = utc(start_utc), utc(end_utc)
    if not start < end:
        raise ValueError("invalid weekly causal input window")
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    manifest = json.loads(shadow_manifest_path.read_text(encoding="utf-8"))
    if (raw.get("contract") != "frozen_raw_telegram_slice_v1"
            or raw.get("causal_input_only") is not True
            or raw.get("complete_week_claim") is not False
            or raw.get("raw_row_count") != len(raw.get("rows", []))
            or raw["raw_row_count"] > MAX_RAW_ROWS
            or utc(raw["start_utc"]) != start or utc(raw["end_utc"]) != end
            or manifest.get("status") != "complete"
            or manifest.get("output_sha256") != digest(shadow_path)
            or manifest.get("source_slice_sha256") != raw.get("source_slice_sha256")
            or manifest.get("start_offset") != raw.get("start_offset")
            or manifest.get("end_offset") != raw.get("end_offset")):
        raise ValueError("raw and shadow extracts do not bind to the same frozen source slice")
    for row in raw["rows"]:
        if (not isinstance(row, dict) or set(row) != set(RAW_FIELDS)
                or row["ev"] != "telegram_raw" or not start <= utc(row["ts"]) < end):
            raise ValueError("raw extract contains noncausal or out-of-window fields")
    signals, diagnostics = compile_signals(
        raw["rows"], start=start, cutoff=end, sticker_directions=STICKERS,
        max_entry_age_s=WEEKLY_ENTRY_MAX_AGE_S)
    text_candidates, text_exclusions = candidate_text_entries(
        raw["rows"], start=start, cutoff=end,
        max_entry_age_s=WEEKLY_ENTRY_MAX_AGE_S)
    received, counts = set(), Counter()
    with gzip.open(shadow_path, "rt", encoding="utf-8") as stream:
        for count, line in enumerate(stream, 1):
            if count > MAX_SHADOW_ROWS:
                raise ValueError("shadow source row budget exceeded")
            row = json.loads(line)
            if row.get("ev") != "signal_received" or not start <= utc(row["ts"]) < end:
                continue
            signal = row.get("sig")
            if not isinstance(signal, str) or not re.fullmatch(r"canal[12]_[1-9]\d*", signal):
                raise ValueError("received signal identity missing")
            received.add(signal)
            counts[signal] += 1
    result = coverage([row.signal_id for row in signals], sorted(received))
    compiled_ids = {row.signal_id for row in signals}
    candidate_ids = {row.signal_id for row in text_candidates}
    unresolved_received = received - compiled_ids
    return {"contract": "weekly_raw_causal_input_readiness_v1",
            "status": "diagnostic_only" if result["all_received_signals_covered"] else "blocked",
            "start_utc": start.isoformat(),
            "end_utc": end.isoformat(), "raw_message_count": len(raw["rows"]),
            "received_event_count": sum(counts.values()),
            "received_duplicate_event_ids": sorted(key for key, count in counts.items() if count > 1),
            **result,
            "text_candidate_signal_ids": sorted(candidate_ids),
            "text_candidate_exclusions": text_exclusions,
            "received_matching_raw_candidates": sorted(unresolved_received & candidate_ids),
            "received_unexplained_by_raw": sorted(unresolved_received - candidate_ids),
            "text_candidate_route_status": "unresolved_scenario_state",
            "max_entry_age_s": WEEKLY_ENTRY_MAX_AGE_S,
            "stale_entry_candidate_ids": sorted({
                f"{row['channel']}_{row['message_id']}" for row in diagnostics
                if row["reason"] == "stale_entry_candidate"}),
            "compiled_signal_ids": sorted(row.signal_id for row in signals),
            "input_diagnostics": diagnostics,
            "frozen_slice_temporal_completeness_proven": False,
            "full_simulator_parity_verified": False,
            "inputs_sha256": {str(path): digest(path) for path in (
                raw_path, shadow_path, shadow_manifest_path, Path(__file__),
                Path(__file__).resolve().parents[1] / "research" / "causal_text_admission.py")},
            "limitations": [
                "The frozen journal byte range does not establish that no messages exist outside it.",
                "A received-signal match checks parser coverage, not broker execution or full decision parity.",
                "Raw-only additional signals must remain in the replay universe, not be dropped.",
                "A raw text candidate is not an admitted entry until scenario-state routing proves it.",
            ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("raw", "shadow", "shadow-manifest", "start-utc", "end-utc", "output"):
        parser.add_argument(f"--{name}", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        raise FileExistsError(output)
    result = audit(args.raw, args.shadow, args.shadow_manifest,
                   start_utc=args.start_utc, end_utc=args.end_utc)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"received": result["received_signal_count"],
                      "compiled": result["compiled_signal_count"],
                      "missing": result["received_missing_from_raw_replay"]}))


if __name__ == "__main__":
    main()
