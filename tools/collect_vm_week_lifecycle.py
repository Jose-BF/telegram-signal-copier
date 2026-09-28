"""Read bounded lifecycle controls from a frozen VM journal slice.

The remote worker reads only and runs at Windows background resource priority.
These observed outcomes are validation controls, never replay inputs.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


CHUNK_BYTES = 1_048_576
MAX_SLICE_BYTES = 4_000_000_000
MAX_LINE_BYTES = 1_000_000
MAX_ROWS = 10_000
MAX_OUTPUT_BYTES = 8_000_000
MAX_SECONDS = 900
EVENTS = frozenset({
    "signal_received", "signal_closed", "lifecycle_finalization_deferred",
    "positions_closed_by_mt5", "canal1_text_processing", "canal1_text_rejected",
    "canal1_text_applied", "signal_skipped",
})
FIELDS = frozenset({
    "ev", "ts", "sig", "event_id", "session_id", "code_commit",
    "strategy_id", "strategy_fingerprint", "trigger", "source_msg_id",
    "reason", "closed_by", "source", "open_position_count",
})


def utc(value):
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("explicit timestamp offset required")
    return stamp.astimezone(timezone.utc)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _background_mode():
    import ctypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.SetPriorityClass.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    kernel.SetPriorityClass.restype = ctypes.c_int
    if not kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x00100000):
        raise OSError(ctypes.get_last_error(), "cannot enter background resource priority")


def validate_spec(spec):
    start, end = utc(spec["start_utc"]), utc(spec["end_utc"])
    lower, upper = spec["start_offset"], spec["end_offset"]
    if (not start < end or type(lower) is not int or type(upper) is not int
            or not 0 <= lower < upper or upper - lower > MAX_SLICE_BYTES
            or not re.fullmatch(r"[0-9a-f]{64}", spec["source_slice_sha256"])):
        raise ValueError("invalid bounded lifecycle source specification")
    return start, end


def scan_slice(spec, *, sleep_seconds=0.04):
    start, end = validate_spec(spec)
    source_path = Path(spec["source"])
    lower, upper = spec["start_offset"], spec["end_offset"]
    rows, source_hash, pending = [], hashlib.sha256(), b""
    began = time.monotonic()
    with source_path.open("rb") as source:
        if source.seek(0, 2) < upper:
            raise ValueError("frozen lifecycle source truncated")
        if lower:
            source.seek(lower - 1)
            if source.read(1) != b"\n":
                raise ValueError("lifecycle slice start is not line-aligned")
        source.seek(upper - 1)
        if source.read(1) != b"\n":
            raise ValueError("lifecycle slice end is not line-aligned")
        source.seek(lower)
        while source.tell() < upper:
            if time.monotonic() - began > MAX_SECONDS:
                raise TimeoutError("bounded lifecycle scan exceeded wall budget")
            chunk = source.read(min(CHUNK_BYTES, upper - source.tell()))
            if not chunk:
                raise ValueError("frozen lifecycle source truncated during scan")
            source_hash.update(chunk)
            lines = (pending + chunk).split(b"\n")
            pending = lines.pop()
            if len(pending) > MAX_LINE_BYTES:
                raise ValueError("oversized lifecycle journal line")
            for line in lines:
                if len(line) > MAX_LINE_BYTES:
                    raise ValueError("oversized lifecycle journal line")
                if not any(name.encode() in line for name in EVENTS):
                    continue
                row = json.loads(line)
                if row.get("ev") not in EVENTS:
                    continue
                observed = utc(row["ts"])
                if not start <= observed < end:
                    continue
                signal_id = row.get("sig")
                if not isinstance(signal_id, str) or not re.fullmatch(r"canal[12]_[1-9][0-9]*", signal_id):
                    continue
                selected = {key: row[key] for key in FIELDS & row.keys()
                            if row[key] is None or type(row[key]) in (str, int, float, bool)
                            and (not isinstance(row[key], str) or len(row[key]) <= 128)}
                rows.append(selected)
                if len(rows) > MAX_ROWS:
                    raise ValueError("lifecycle row budget exceeded")
            if sleep_seconds:
                time.sleep(sleep_seconds)
    if pending or source_hash.hexdigest() != spec["source_slice_sha256"]:
        raise ValueError("frozen lifecycle source hash or boundary mismatch")
    payload = json.dumps(rows, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    if len(payload) > MAX_OUTPUT_BYTES:
        raise ValueError("lifecycle output byte budget exceeded")
    return {"contract": "frozen_week_lifecycle_controls_v1",
            "source_slice_sha256": source_hash.hexdigest(),
            "start_offset": lower, "end_offset": upper,
            "start_utc": start.isoformat(), "end_utc": end.isoformat(),
            "event_count": len(rows),
            "signal_ids": sorted({row["sig"] for row in rows}),
            "events": rows, "observed_outcomes_not_replay_inputs": True,
            "complete_week_claim": False}


def collect(manifest_path, host, identity, output_path, start_utc, end_utc):
    manifest_path, identity, output_path = map(Path, (manifest_path, identity, output_path))
    if output_path.exists():
        raise FileExistsError(output_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("status") != "complete" or manifest.get("incomplete_tail_bytes") != 0
            or manifest.get("scanned_bytes") != manifest["end_offset"] - manifest["start_offset"]):
        raise ValueError("source slice is not a complete frozen byte interval")
    spec = {"source": manifest["source"], "start_offset": manifest["start_offset"],
            "end_offset": manifest["end_offset"],
            "source_slice_sha256": manifest["source_slice_sha256"],
            "start_utc": start_utc, "end_utc": end_utc}
    validate_spec(spec)
    encoded = base64.b64encode(json.dumps(spec, separators=(",", ":")).encode()).decode("ascii")
    command = ["ssh", "-i", str(identity), "-o", "BatchMode=yes",
               "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=5",
               host, f"python - --worker {encoded}"]
    run = subprocess.run(command, input=Path(__file__).read_bytes(), capture_output=True,
                         timeout=MAX_SECONDS + 30, check=False)
    if run.returncode or run.stderr:
        raise RuntimeError("bounded VM lifecycle collection failed; no output written")
    if len(run.stdout) > MAX_OUTPUT_BYTES + 1_000_000:
        raise ValueError("remote lifecycle output exceeds byte budget")
    report = json.loads(run.stdout)
    if (report.get("contract") != "frozen_week_lifecycle_controls_v1"
            or report.get("source_slice_sha256") != spec["source_slice_sha256"]
            or report.get("start_offset") != spec["start_offset"]
            or report.get("end_offset") != spec["end_offset"]
            or report.get("start_utc") != utc(start_utc).isoformat()
            or report.get("end_utc") != utc(end_utc).isoformat()
            or report.get("event_count") != len(report["events"])
            or report.get("observed_outcomes_not_replay_inputs") is not True):
        raise ValueError("remote lifecycle source binding incomplete")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("xb") as stream:
        stream.write(json.dumps(report, sort_keys=True, ensure_ascii=True, indent=2).encode() + b"\n")
    return {"events": report["event_count"], "signal_ids": len(report["signal_ids"]),
            "output_sha256": digest(output_path)}


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        spec = json.loads(base64.b64decode(sys.argv[2], validate=True))
        if sys.platform == "win32":
            _background_mode()
        elif sys.platform.startswith("linux"):
            os.nice(19)
        sys.stdout.write(json.dumps(scan_slice(spec), sort_keys=True, ensure_ascii=True) + "\n")
        sys.stdout.flush()
        return
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "host", "identity", "output", "start-utc", "end-utc"):
        parser.add_argument(f"--{name}", required=True)
    args = parser.parse_args()
    result = collect(args.manifest, args.host, args.identity, args.output,
                     args.start_utc, args.end_utc)
    sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
