"""Collect one read-only VM journal chunk, or assemble a complete prefix index."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.audit_journal_chunk_index import MAX_CHUNK_BYTES, MAX_SECONDS, utc


WORKER = Path(__file__).with_name("audit_journal_chunk_index.py")


def chunk_bounds(prefix_end, chunk_bytes, index):
    if (type(prefix_end) is not int or type(chunk_bytes) is not int or type(index) is not int
            or prefix_end <= 0 or not 0 < chunk_bytes <= MAX_CHUNK_BYTES
            or not 0 <= index < math.ceil(prefix_end / chunk_bytes)):
        raise ValueError("invalid journal prefix chunk arguments")
    begin = index * chunk_bytes
    return begin, min(begin + chunk_bytes, prefix_end)


def validate_chunk(report, *, source, prefix_end, begin, end):
    if (report.get("contract") != "journal_byte_chunk_time_index_v1"
            or report.get("source") != source
            or report.get("prefix_end") != prefix_end
            or report.get("begin") != begin or report.get("end") != end
            or type(report.get("actual_start")) is not int
            or type(report.get("actual_end")) is not int
            or not begin <= report["actual_start"] <= report["actual_end"] <= prefix_end
            or report["actual_end"] < end
            or type(report.get("source_size_at_start")) is not int
            or report["source_size_at_start"] < prefix_end
            or type(report.get("event_count")) is not int or report["event_count"] < 0
            or not re.fullmatch(r"[0-9a-f]{64}", str(report.get("sha256")))):
        raise ValueError("journal index chunk identity or bounds mismatch")
    minimum, maximum = report.get("minimum_utc"), report.get("maximum_utc")
    if (minimum is None) != (maximum is None) or (minimum is None) != (report["event_count"] == 0):
        raise ValueError("journal index chunk time range or count invalid")
    if minimum is not None and utc(minimum) > utc(maximum):
        raise ValueError("journal index chunk time range inverted")


def collect_chunk(*, source, prefix_end, chunk_bytes, index, host, identity, runner=subprocess.run):
    begin, end = chunk_bounds(prefix_end, chunk_bytes, index)
    spec = {"source": source, "begin": begin, "end": end, "prefix_end": prefix_end}
    encoded = base64.b64encode(json.dumps(spec, separators=(",", ":")).encode()).decode("ascii")
    command = ["ssh", "-i", str(identity), "-o", "BatchMode=yes",
               "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=5",
               host, f"python - --index-worker {encoded}"]
    run = runner(command, input=WORKER.read_bytes(), capture_output=True,
                 timeout=MAX_SECONDS + 15, check=False)
    if run.returncode or run.stderr:
        raise RuntimeError("bounded journal index collection failed; no report written")
    report = json.loads(run.stdout)
    validate_chunk(report, source=source, prefix_end=prefix_end, begin=begin, end=end)
    return report


def assemble_index(reports, *, source, prefix_end, chunk_bytes):
    chunk_bounds(prefix_end, chunk_bytes, 0)
    count = math.ceil(prefix_end / chunk_bytes)
    if len(reports) != count:
        raise ValueError("incomplete journal prefix chunk set")
    previous_end = 0
    worker_hash = reports[0].get("local_worker_sha256")
    if not re.fullmatch(r"[0-9a-f]{64}", str(worker_hash)):
        raise ValueError("journal index worker hash missing")
    for index, report in enumerate(reports):
        begin, end = chunk_bounds(prefix_end, chunk_bytes, index)
        validate_chunk(report, source=source, prefix_end=prefix_end, begin=begin, end=end)
        if report.get("local_worker_sha256") != worker_hash:
            raise ValueError("journal index chunks use mixed workers")
        if report["actual_start"] != previous_end:
            raise ValueError("journal prefix has a byte gap or overlap")
        previous_end = report["actual_end"]
    if previous_end != prefix_end:
        raise ValueError("journal prefix index incomplete")
    return {"contract": "journal_prefix_chunk_index_v1", "source": source,
            "prefix_end": prefix_end, "chunk_bytes": chunk_bytes,
            "chunk_count": count, "chunks": reports,
            "worker_sha256": worker_hash,
            "full_prefix_bytes_indexed": True,
            "full_source_time_coverage_verified": False}


def capture_batch(*, source, prefix_end, chunk_bytes, output_dir, max_new_chunks,
                  pause_seconds, host, identity, collector=collect_chunk, sleeper=time.sleep):
    chunk_bounds(prefix_end, chunk_bytes, 0)
    if (type(max_new_chunks) is not int or not 1 <= max_new_chunks <= 32
            or not 1 <= pause_seconds <= 300):
        raise ValueError("invalid bounded journal index batch budget")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    worker_sha = hashlib.sha256(WORKER.read_bytes()).hexdigest()
    count, new, reused = math.ceil(prefix_end / chunk_bytes), 0, 0
    for index in range(count):
        path = output_dir / f"chunk_{index:04d}.json"
        begin, end = chunk_bounds(prefix_end, chunk_bytes, index)
        if path.exists():
            report = json.loads(path.read_text(encoding="utf-8"))
            validate_chunk(report, source=source, prefix_end=prefix_end, begin=begin, end=end)
            if report.get("local_worker_sha256") != worker_sha:
                raise ValueError("existing journal index chunk uses another worker")
            reused += 1
            continue
        if new == max_new_chunks:
            break
        if new:
            sleeper(pause_seconds)
        report = collector(source=source, prefix_end=prefix_end, chunk_bytes=chunk_bytes,
                           index=index, host=host, identity=identity)
        validate_chunk(report, source=source, prefix_end=prefix_end, begin=begin, end=end)
        report["local_worker_sha256"] = worker_sha
        with path.open("x", encoding="utf-8") as target:
            json.dump(report, target, indent=2, ensure_ascii=True)
            target.write("\n")
        new += 1
    return {"contract": "journal_index_batch_progress_v1", "total_chunks": count,
            "new_chunks": new, "reused_chunks": reused,
            "complete": new + reused == count}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remote-source", required=True)
    parser.add_argument("--prefix-end", type=int, required=True)
    parser.add_argument("--chunk-bytes", type=int, default=32_000_000)
    parser.add_argument("--chunk-index", type=int)
    parser.add_argument("--chunk-dir", type=Path)
    parser.add_argument("--batch-dir", type=Path)
    parser.add_argument("--max-new-chunks", type=int)
    parser.add_argument("--pause-seconds", type=float, default=2)
    parser.add_argument("--host")
    parser.add_argument("--identity", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output is not None and args.output.exists():
        raise FileExistsError(args.output)
    if sum(value is not None for value in (args.chunk_index, args.chunk_dir, args.batch_dir)) != 1:
        raise ValueError("choose one chunk, one batch, or assemble a chunk directory")
    if args.batch_dir is not None:
        if (not args.host or args.identity is None or args.output is not None
                or args.max_new_chunks is None):
            raise ValueError("bounded journal index batch arguments invalid")
        progress = capture_batch(source=args.remote_source, prefix_end=args.prefix_end,
                                 chunk_bytes=args.chunk_bytes, output_dir=args.batch_dir,
                                 max_new_chunks=args.max_new_chunks,
                                 pause_seconds=args.pause_seconds,
                                 host=args.host, identity=args.identity)
        print(json.dumps(progress))
        return
    if args.output is None:
        raise ValueError("output required for one chunk or assembled index")
    if args.chunk_index is not None:
        if not args.host or args.identity is None:
            raise ValueError("remote host and identity required for chunk capture")
        report = collect_chunk(source=args.remote_source, prefix_end=args.prefix_end,
                               chunk_bytes=args.chunk_bytes, index=args.chunk_index,
                               host=args.host, identity=args.identity)
        report["local_worker_sha256"] = hashlib.sha256(WORKER.read_bytes()).hexdigest()
    else:
        count = math.ceil(args.prefix_end / args.chunk_bytes)
        reports = [json.loads((args.chunk_dir / f"chunk_{index:04d}.json").read_text(encoding="utf-8"))
                   for index in range(count)]
        report = assemble_index(reports, source=args.remote_source,
                                prefix_end=args.prefix_end, chunk_bytes=args.chunk_bytes)
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(report, target, indent=2, ensure_ascii=True)
        target.write("\n")
    print(json.dumps({"contract": report["contract"], "output": str(args.output)}))


if __name__ == "__main__":
    main()
