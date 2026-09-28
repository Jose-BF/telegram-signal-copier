"""Frozen raw Telegram slice (frozen_raw_telegram_slice_v1) from journal extracts.

Inputs are JSONL files of telegram_raw journal events (vm_extract_raw.py day
extracts). Rows are deduplicated by event_id (day extracts overlap), projected
to the fields the causal compilers read, and sorted by observation time.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

KEYS = ("channel", "date_utc", "edit_date_utc", "ev", "is_edit", "message_id",
        "message_revision_id", "reply_to_msg_id", "sticker_id", "text", "ts")


def build(paths, start_utc, end_utc, note):
    seen, rows, inputs = set(), [], {}
    for path in paths:
        data = Path(path).read_bytes()
        inputs[Path(path).name] = hashlib.sha256(data).hexdigest()
        for line in data.decode("utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get("ev") != "telegram_raw":
                continue
            key = event.get("event_id") or json.dumps(event, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            rows.append({k: event.get(k) for k in KEYS})
    rows.sort(key=lambda row: row["ts"])
    digest = hashlib.sha256("\n".join(json.dumps(r, sort_keys=True) for r in rows).encode()).hexdigest()
    return {"contract": "frozen_raw_telegram_slice_v1", "status": "diagnostic_only",
            "causal_input_only": True, "complete_week_claim": False,
            "start_utc": start_utc, "end_utc": end_utc, "start_offset": None, "end_offset": None,
            "raw_row_count": len(rows), "source_slice_sha256": digest, "input_sha256": inputs,
            "limitations": [note, "Only telegram_raw rows, deduplicated by event_id; not a hashed byte slice of the whole journal."],
            "rows": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--start-utc", required=True)
    parser.add_argument("--end-utc", required=True)
    parser.add_argument("--note", default="Extracted by Claude from the live VM journal by day windows (background priority).")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("refusing to overwrite")
    report = build(args.inputs, args.start_utc, args.end_utc, args.note)
    args.output.write_text(json.dumps(report, ensure_ascii=True) + "\n", encoding="utf-8")
    print(json.dumps({"rows": report["raw_row_count"], "sha": report["source_slice_sha256"][:12]}))


if __name__ == "__main__":
    main()
