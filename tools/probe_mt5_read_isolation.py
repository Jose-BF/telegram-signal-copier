"""Offline native-GIL experiment, with negative control; never imports MT5."""

from __future__ import annotations

import argparse
import asyncio
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mt5_client import MT5ReadClient
from mt5_protocol import LookupState
from mt5_read_protocol import ReadOperation, ReadRequest
from mt5_worker import WorkerConfig
from tests.mt5_read_fakes import FakeMT5, hold_native_gil


async def heartbeat(stop):
    moments = [time.monotonic()]
    while not stop.is_set():
        await asyncio.sleep(0.01)
        moments.append(time.monotonic())
    return [b - a for a, b in zip(moments, moments[1:])]


def stats(gaps):
    ordered = sorted(gaps)
    return {"samples": len(gaps), "max_ms": max(gaps) * 1000,
            "p99_ms": ordered[min(len(ordered) - 1, int(len(ordered) * 0.99))] * 1000}


async def measure(seconds):
    stop = asyncio.Event()
    pulse = asyncio.create_task(heartbeat(stop))
    await asyncio.sleep(0.04)
    await asyncio.to_thread(hold_native_gil, 0.4)
    await asyncio.sleep(0.04)
    stop.set()
    negative = stats(await pulse)
    client = MT5ReadClient(WorkerConfig(7, "demo", ("XAUUSD",)),
                           backend_factory=partial(FakeMT5, delay=seconds, native=True))
    async with client:
        stop = asyncio.Event()
        pulse = asyncio.create_task(heartbeat(stop))
        await asyncio.sleep(0.04)
        start = time.monotonic()
        response = await client.read(ReadRequest(ReadOperation.TICK, {"symbol": "XAUUSD"}), timeout=seconds + 10)
        elapsed = time.monotonic() - start
        await asyncio.sleep(0.04)
        stop.set()
        isolated = stats(await pulse)
        pid = client.worker_pid
    passed = (negative["max_ms"] >= 350 and isolated["max_ms"] <= 1000
              and isolated["p99_ms"] <= 100 and elapsed >= seconds
              and response.state is LookupState.FOUND and pid != os.getpid())
    return {"schema_version": 1, "passed": passed,
            "scope": "local_offline_fake_backend_native_PyDLL_GIL_hold_not_MT5_or_VM",
            "requested_native_seconds": seconds, "elapsed_seconds": elapsed,
            "negative_control": negative, "isolated": isolated,
            "parent_pid": os.getpid(), "worker_pid": pid,
            "state": response.state.value, "worker_stopped": not client.is_alive,
            "python": sys.version, "platform": platform.platform(), "utc_ns": time.time_ns(),
            "sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
                "mt5_client.py", "mt5_scheduling.py", "mt5_worker.py", "mt5_worker_entry.py",
                "mt5_read_protocol.py", "mt5_protocol.py",
                "tests/mt5_read_fakes.py", "tools/probe_mt5_read_isolation.py")}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 120:
        parser.error("seconds must be between 1 and 120")
    if args.output.exists():
        parser.error("output already exists; evidence is immutable")
    result = asyncio.run(measure(args.seconds))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
