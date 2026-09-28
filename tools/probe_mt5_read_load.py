"""Bounded offline read-load smoke test, not an E4 production certification."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import hashlib
import json
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psutil

from mt5_client import MT5ReadClient
from mt5_protocol import LookupState
from mt5_read_protocol import ReadOperation, ReadRequest
from mt5_worker import WorkerConfig
from tests.mt5_load_fakes import BoundedReadMT5
from tools.probe_mt5_read_isolation import heartbeat, stats


async def measure(seconds: int, rate: int, concurrency: int) -> dict:
    client = MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",)),
        backend_factory=BoundedReadMT5,
        queue_size=8,
    )
    pulse = None
    stop = asyncio.Event()
    latencies = []
    memory = []
    states = Counter()
    parent = psutil.Process()
    started = time.monotonic()
    try:
        response = await client.start()
        if response.state is not LookupState.FOUND:
            raise RuntimeError("offline worker initialization failed")
        worker_pid = response.worker_pid
        worker = psutil.Process(worker_pid)
        started = time.monotonic()
        deadline = started + seconds
        pulse = asyncio.create_task(heartbeat(stop))

        async def producer(index):
            due = started + index / rate
            while due < deadline:
                await asyncio.sleep(max(0, due - time.monotonic()))
                if time.monotonic() >= deadline:
                    break
                begin = time.monotonic()
                result = await client.read(
                    ReadRequest(ReadOperation.TICK, {"symbol": "XAUUSD"}),
                    timeout=1.0,
                )
                latencies.append(time.monotonic() - begin)
                states[result.state.value] += 1
                due += concurrency / rate

        async def sample():
            while time.monotonic() < deadline:
                snapshot = client.transport_snapshot()
                memory.append({
                    "elapsed_s": time.monotonic() - started,
                    "parent_rss": parent.memory_info().rss,
                    "worker_rss": worker.memory_info().rss,
                    "pending": snapshot["pending_count"],
                    "read_waiters": snapshot["read_waiters"],
                })
                await asyncio.sleep(0.25)

        await asyncio.gather(sample(), *(producer(i) for i in range(concurrency)))
        drained = client.pending_count == 0
    finally:
        stop.set()
        gaps = await pulse if pulse is not None else []
        await client.close()
    loop_stats = stats(gaps)
    failures = sum(count for state, count in states.items() if state != "FOUND")
    return {
        "schema_version": 1,
        "scope": "local_offline_read_only_smoke_not_MT5_VM_or_full_E4",
        "passed": failures == 0 and drained and not client.is_alive
        and loop_stats["p99_ms"] <= 100 and loop_stats["max_ms"] <= 1000
        and len(latencies) >= seconds * rate * 0.95,
        "seconds_requested": seconds,
        "elapsed_seconds": time.monotonic() - started,
        "configured_reads_per_second": rate,
        "measured_production_peak": None,
        "concurrency": concurrency,
        "attempts": len(latencies),
        "states": dict(states),
        "heartbeat": loop_stats,
        "read_latency": stats(latencies),
        "max_pending": max(row["pending"] for row in memory),
        "max_read_waiters": max(row["read_waiters"] for row in memory),
        "memory_first": memory[0],
        "memory_last": memory[-1],
        "parent_rss_peak": max(row["parent_rss"] for row in memory),
        "worker_rss_peak": max(row["worker_rss"] for row in memory),
        "worker_pid": worker_pid,
        "worker_stopped": not client.is_alive,
        "drained": drained,
        "limitations": [
            "No trade load, management latency, broker connection or strategy runtime.",
            "Not the required 60-minute test at twice a measured production peak.",
            "Memory observations do not by themselves prove absence of leaks.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=120, choices=range(10, 601))
    parser.add_argument("--rate", type=int, default=40, choices=range(1, 51))
    parser.add_argument("--concurrency", type=int, default=4, choices=range(1, 17))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; preserve previous evidence")
    sources = [
        "mt5_client.py", "mt5_scheduling.py", "mt5_worker.py", "mt5_worker_entry.py",
        "mt5_protocol.py", "mt5_read_protocol.py", "tests/mt5_read_fakes.py",
        "tests/mt5_load_fakes.py", "tools/probe_mt5_read_isolation.py",
        "tools/probe_mt5_read_load.py",
    ]
    before = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources}
    report = asyncio.run(measure(args.seconds, args.rate, args.concurrency))
    after = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources}
    report.update(
        python=sys.version, platform=platform.platform(), utc_ns=time.time_ns(),
        sha256=before, sources_unchanged=before == after,
    )
    report["passed"] = report["passed"] and before == after
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
