"""Read only raw Telegram inputs from a frozen VM journal byte slice.

The remote worker streams an already-hashed slice to stdout. It never writes
on the VM and does not read broker outcomes, positions or shadow states.
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
MAX_ROWS = 20_000
MAX_OUTPUT_BYTES = 32_000_000
MAX_SECONDS = 900
RAW_FIELDS = (
    "ev", "channel", "message_id", "message_revision_id", "date_utc", "ts",
    "text", "is_edit", "edit_date_utc", "reply_to_msg_id", "sticker_id",
)


def utc(value):
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("explicit UTC timestamp required")
    return stamp.astimezone(timezone.utc)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _enter_windows_background_mode():
    import ctypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.SetPriorityClass.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    kernel.SetPriorityClass.restype = ctypes.c_int
    if not kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x00100000):
        raise OSError(ctypes.get_last_error(), "cannot enter Windows background resource priority")


def validate_spec(spec):
    start, end = utc(spec["start_utc"]), utc(spec["end_utc"])
    lower, upper = spec["start_offset"], spec["end_offset"]
    if (not start < end or type(lower) is not int or type(upper) is not int
            or not 0 <= lower < upper or upper - lower > MAX_SLICE_BYTES
            or not re.fullmatch(r"[0-9a-f]{64}", spec["source_slice_sha256"])):
        raise ValueError("invalid frozen raw-message slice specification")
    return start, end


def scan_slice(spec, *, sleep_seconds=0.04):
    start, end = validate_spec(spec)
    source_path = Path(spec["source"])
    lower, upper = spec["start_offset"], spec["end_offset"]
    rows, source_hash, read_bytes, pending = [], hashlib.sha256(), 0, b""
    began = time.monotonic()
    with source_path.open("rb") as source:
        if source.seek(0, 2) < upper:
            raise ValueError("frozen journal slice truncated")
        if lower:
            source.seek(lower - 1)
            if source.read(1) != b"\n":
                raise ValueError("slice does not begin at a line boundary")
        source.seek(upper - 1)
        if source.read(1) != b"\n":
            raise ValueError("slice does not end at a line boundary")
        source.seek(lower)
        while source.tell() < upper:
            if time.monotonic() - began > MAX_SECONDS:
                raise TimeoutError("frozen raw-message scan wall budget exceeded")
            chunk = source.read(min(CHUNK_BYTES, upper - source.tell()))
            if not chunk:
                raise ValueError("frozen journal slice truncated during scan")
            source_hash.update(chunk)
            read_bytes += len(chunk)
            lines = (pending + chunk).split(b"\n")
            pending = lines.pop()
            if len(pending) > MAX_LINE_BYTES:
                raise ValueError("oversized journal line")
            for line in lines:
                if len(line) > MAX_LINE_BYTES:
                    raise ValueError("oversized journal line")
                if b"telegram_raw" not in line:
                    continue
                row = json.loads(line)
                if row.get("ev") != "telegram_raw" or not start <= utc(row["ts"]) < end:
                    continue
                if (row.get("channel") not in {"canal1", "canal2"}
                        or type(row.get("message_id")) is not int
                        or row["message_id"] <= 0
                        or not isinstance(row.get("message_revision_id"), str)
                        or not row["message_revision_id"]):
                    raise ValueError("raw Telegram input identity incomplete")
                rows.append({name: row.get(name) for name in RAW_FIELDS})
                if len(rows) > MAX_ROWS:
                    raise ValueError("raw Telegram row budget exceeded")
            if sleep_seconds:
                time.sleep(sleep_seconds)
    if (read_bytes != upper - lower or pending
            or source_hash.hexdigest() != spec["source_slice_sha256"]):
        raise ValueError("frozen journal slice hash or boundary mismatch")
    payload = json.dumps(rows, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    if len(payload) > MAX_OUTPUT_BYTES:
        raise ValueError("raw Telegram output byte budget exceeded")
    return {"contract": "frozen_raw_telegram_slice_v1", "status": "diagnostic_only",
            "start_utc": start.isoformat(), "end_utc": end.isoformat(),
            "start_offset": lower, "end_offset": upper,
            "source_slice_sha256": source_hash.hexdigest(),
            "rows": rows, "raw_row_count": len(rows),
            "causal_input_only": True,
            "complete_week_claim": False,
            "limitations": [
                "The frozen byte slice is not proof that no relevant event exists outside it.",
                "Raw messages require separate parser, revision and causal-availability admission.",
            ]}


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
        raise RuntimeError("bounded VM raw-message collection failed; no output written")
    if len(run.stdout) > MAX_OUTPUT_BYTES + 1_000_000:
        raise ValueError("remote raw-message result exceeds byte budget")
    result = json.loads(run.stdout)
    if (result.get("contract") != "frozen_raw_telegram_slice_v1"
            or result.get("status") != "diagnostic_only"
            or result.get("source_slice_sha256") != spec["source_slice_sha256"]
            or result.get("start_offset") != spec["start_offset"]
            or result.get("end_offset") != spec["end_offset"]
            or result.get("start_utc") != utc(start_utc).isoformat()
            or result.get("end_utc") != utc(end_utc).isoformat()
            or result.get("raw_row_count") != len(result["rows"])
            or result.get("causal_input_only") is not True):
        raise ValueError("remote raw-message source binding incomplete")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("xb") as stream:
        stream.write(json.dumps(result, sort_keys=True, ensure_ascii=True, indent=2).encode() + b"\n")
    return {"raw_rows": result["raw_row_count"], "output_sha256": digest(output_path),
            "source_slice_sha256": result["source_slice_sha256"]}


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        spec = json.loads(base64.b64decode(sys.argv[2], validate=True))
        if sys.platform == "win32":
            _enter_windows_background_mode()
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
