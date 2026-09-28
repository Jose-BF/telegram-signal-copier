"""Build bounded byte-chunk time ranges without assuming journal time order."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sys
import time

MAX_CHUNK_BYTES = 64_000_000
MAX_LINE_BYTES = 1_000_000
MAX_SECONDS = 120


def utc(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("explicit UTC required")
    return parsed


def _next_line_start(source, offset, prefix_end):
    if offset == 0 or offset == prefix_end:
        return offset
    source.seek(offset - 1)
    if source.read(1) == b"\n":
        return offset
    source.seek(offset)
    skipped = source.readline(MAX_LINE_BYTES + 1)
    if len(skipped) > MAX_LINE_BYTES or not skipped.endswith(b"\n"):
        raise ValueError("journal line boundary missing or oversized")
    return source.tell()


def scan_chunk(path, begin, end, *, prefix_end):
    path = Path(path)
    if (type(begin) is not int or type(end) is not int or type(prefix_end) is not int
            or not 0 <= begin < end <= prefix_end or end - begin > MAX_CHUNK_BYTES):
        raise ValueError("invalid bounded journal chunk")
    started = time.monotonic()
    with path.open("rb") as source:
        size = source.seek(0, 2)
        if size < prefix_end:
            raise ValueError("journal prefix truncated")
        source.seek(prefix_end - 1)
        if source.read(1) != b"\n":
            raise ValueError("journal prefix ends in an incomplete line")
        actual_start = _next_line_start(source, begin, prefix_end)
        actual_end = _next_line_start(source, end, prefix_end)
        if actual_end - actual_start > MAX_CHUNK_BYTES + MAX_LINE_BYTES:
            raise ValueError("aligned journal chunk exceeds byte budget")
        source.seek(actual_start)
        sha, count, earliest, latest, first, previous, regressions = (
            hashlib.sha256(), 0, None, None, None, None, 0)
        while source.tell() < actual_end:
            if time.monotonic() - started > MAX_SECONDS:
                raise TimeoutError("journal chunk wall budget exceeded")
            raw = source.readline(MAX_LINE_BYTES + 1)
            if len(raw) > MAX_LINE_BYTES or not raw.endswith(b"\n") or source.tell() > actual_end:
                raise ValueError("invalid journal line in indexed chunk")
            stamp = utc(json.loads(raw)["ts"])
            if first is None:
                first = stamp
            if previous is not None and stamp < previous:
                regressions += 1
            previous = stamp
            earliest = stamp if earliest is None else min(earliest, stamp)
            latest = stamp if latest is None else max(latest, stamp)
            sha.update(raw)
            count += 1
        if path.stat().st_size < prefix_end:
            raise ValueError("journal prefix truncated during chunk scan")
    return {"contract": "journal_byte_chunk_time_index_v1", "source": str(path.resolve()),
            "prefix_end": prefix_end, "begin": begin, "end": end,
            "actual_start": actual_start, "actual_end": actual_end,
            "source_size_at_start": size, "sha256": sha.hexdigest(),
            "event_count": count, "adjacent_time_regressions": regressions,
            "first_utc": first.isoformat() if first is not None else None,
            "last_utc": previous.isoformat() if previous is not None else None,
            "minimum_utc": earliest.isoformat() if earliest is not None else None,
            "maximum_utc": latest.isoformat() if latest is not None else None}


def chunks_for_window(chunks, start, end):
    lower, upper = utc(start), utc(end)
    if not lower < upper or not chunks:
        raise ValueError("invalid indexed window")
    ordered = sorted(chunks, key=lambda row: row["begin"])
    source, prefix_end = ordered[0]["source"], ordered[0]["prefix_end"]
    if ordered[0]["begin"] != 0 or ordered[0]["actual_start"] != 0:
        raise ValueError("journal prefix index does not start at zero")
    selected = []
    indexed_min, indexed_max = None, None
    previous = None
    for chunk in ordered:
        if (chunk.get("contract") != "journal_byte_chunk_time_index_v1"
                or chunk.get("source") != source or chunk.get("prefix_end") != prefix_end
                or chunk["begin"] < 0 or chunk["begin"] >= chunk["end"]
                or chunk["actual_start"] > chunk["actual_end"]
                or previous is not None and (chunk["begin"] != previous["end"]
                                             or chunk["actual_start"] != previous["actual_end"])):
            raise ValueError("journal chunk index has a gap or mixed source")
        minimum, maximum = chunk["minimum_utc"], chunk["maximum_utc"]
        if (minimum is None) != (maximum is None) or (minimum is None) != (chunk["event_count"] == 0):
            raise ValueError("journal chunk time range or count invalid")
        if minimum is not None:
            first, last = utc(minimum), utc(maximum)
            if first > last:
                raise ValueError("journal chunk time range inverted")
            indexed_min = first if indexed_min is None else min(indexed_min, first)
            indexed_max = last if indexed_max is None else max(indexed_max, last)
            if first < upper and last >= lower:
                selected.append(chunk)
        previous = chunk
    if ordered[-1]["end"] != prefix_end or ordered[-1]["actual_end"] != prefix_end:
        raise ValueError("journal prefix index incomplete")
    if indexed_min is None or lower < indexed_min or upper > indexed_max:
        raise ValueError("requested window outside indexed time span")
    return selected


def main():
    if len(sys.argv) != 3 or sys.argv[1] != "--index-worker":
        raise ValueError("expected bounded index worker specification")
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    spec = json.loads(base64.b64decode(sys.argv[2], validate=True))
    report = scan_chunk(spec["source"], spec["begin"], spec["end"],
                        prefix_end=spec["prefix_end"])
    print(json.dumps(report, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
