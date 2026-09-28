"""Bounded mixed-transport endurance probe; no native MT5 or live account."""

import argparse
import asyncio
from collections import Counter
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

import psutil

from mt5_client import MT5ReadClient
from mt5_protocol import BrokerRequest, IntentKey, IntentState, LookupState
from mt5_read_protocol import ReadOperation, ReadRequest
from mt5_worker import WorkerConfig
from tests.mt5_load_fakes import MixedLoadMT5
from tools.probe_mt5_read_isolation import heartbeat, stats


def request(index):
    operations = ("OPEN_MARKET", "MODIFY_SLTP", "CLOSE_POSITION")
    operation = operations[index % len(operations)]
    payload = {"symbol": "XAUUSD"}
    if operation == "OPEN_MARKET":
        payload.update(direction="BUY", volume=.01, sl=2499.0, tp=2502.0,
                       loss_budget=None, protection_policy="required", magic=111,
                       comment="offline-endurance", deviation=30)
    else:
        payload.update(ticket=42, expected_magic=111)
        if operation == "MODIFY_SLTP":
            payload.update(new_sl=2499.0, new_tp=2502.0)
        else:
            payload.update(deviation=30)
    return BrokerRequest.create(
        IntentKey("demo/7", "canal1", f"canal1_{index + 1}", 0, "probe", operation, 0),
        payload,
    )


async def measure(directory, seconds, read_rate, *, entry_evidence=False):
    journal = service = None
    checkpoint_latencies = []
    checkpoints = Counter()
    if entry_evidence:
        # A dedicated CLI process binds storage before importing the journal.
        if "journal" in sys.modules:
            raise RuntimeError("entry-evidence probe requires a fresh process")
        os.environ["BOT_RUNTIME_DATA_DIR"] = str(directory / "journal")
        import journal
        import runtime_storage
        from durable_execution import DurableExecutionService
    client = MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",), owner_lock_path=str(directory / "owner.lock")),
        backend_factory=MixedLoadMT5, queue_size=8,
        store_path=directory / "intents.sqlite3",
    )
    if entry_evidence:
        service = DurableExecutionService(client)
        service.entry_guard = journal.assert_entry_evidence_ready

        async def checkpoint(operation, *, timeout):
            begin = time.monotonic()
            confirmed = await journal.confirm_entry_intent(operation, timeout=timeout)
            checkpoint_latencies.append(time.monotonic() - begin)
            checkpoints["confirmed" if confirmed else "failed"] += 1
            return confirmed

        service.entry_evidence_probe = checkpoint
    stop = asyncio.Event()
    pulse = None
    guard = None
    read_latencies, trade_latencies, memory = [], [], []
    reads, trades = Counter(), Counter()
    started = time.monotonic()
    try:
        ready = await client.start()
        assert ready.state is LookupState.FOUND
        worker = psutil.Process(ready.worker_pid)
        guard, = [p for p in worker.children() if any(
            Path(arg).name == "mt5_worker_lifetime.py" for arg in p.cmdline()
        )]
        parent = psutil.Process()
        started = time.monotonic()
        deadline = started + seconds
        pulse = asyncio.create_task(heartbeat(stop))

        async def read_loop(index):
            due = started + index / read_rate
            while due < deadline:
                await asyncio.sleep(max(0, due - time.monotonic()))
                if time.monotonic() >= deadline:
                    break
                begin = time.monotonic()
                result = await client.read(ReadRequest(ReadOperation.TICK, {"symbol": "XAUUSD"}), timeout=1)
                reads[result.state.value] += 1
                read_latencies.append(time.monotonic() - begin)
                due += 4 / read_rate

        async def trade_loop():
            for index in range(seconds):
                await asyncio.sleep(max(0, started + index - time.monotonic()))
                if time.monotonic() >= deadline:
                    break
                begin = time.monotonic()
                operation = request(index)
                if service is None:
                    result = await client.execute(operation, reservation_key=f"probe-{index}", policy_revision=0, timeout=2)
                else:
                    durable = await service.execute(
                        operation, reservation_key=f"probe-{index}",
                        projection_key=f"probe-{index}", timeout=2,
                    )
                    result = durable.record
                trades[f"{operation.intent_key.operation}:{result.state.value}"] += 1
                trade_latencies.append(time.monotonic() - begin)

        async def sample():
            while time.monotonic() < deadline:
                journal_health = {}
                if journal is not None:
                    storage = await asyncio.to_thread(runtime_storage.storage_health, journal.DATA_DIR)
                    journal.observe_storage_health(storage)
                    journal_health = journal.persistence_health()
                guard_cpu = guard.cpu_times()
                memory.append({
                    "elapsed_s": time.monotonic() - started,
                    "parent_rss": parent.memory_info().rss,
                    "worker_rss": worker.memory_info().rss,
                    "guard_rss": guard.memory_info().rss,
                    "guard_cpu_seconds": guard_cpu.user + guard_cpu.system,
                    **client.transport_snapshot(),
                    "journal": journal_health,
                })
                await asyncio.sleep(1)

        async with asyncio.TaskGroup() as group:
            group.create_task(sample())
            group.create_task(trade_loop())
            for index in range(4):
                group.create_task(read_loop(index))
        drained = client.pending_count == 0
        journal_flushed = journal is None or await asyncio.to_thread(journal.flush_events, timeout=5)
    finally:
        stop.set()
        gaps = await pulse if pulse is not None else []
        await client.close()
        if guard is not None:
            await asyncio.to_thread(guard.wait, timeout=5)
    loops = stats(gaps)
    all_done = all(key.endswith(":" + IntentState.DONE.value) for key in trades)
    return {
        "schema_version": 1,
        "passed": reads.get("FOUND", 0) >= seconds * read_rate * .95
        and sum(reads.values()) == reads.get("FOUND", 0)
        and sum(trades.values()) == seconds and all_done and drained
        and loops["p99_ms"] <= 100 and loops["max_ms"] <= 1000 and not client.is_alive
        and guard is not None and not guard.is_running() and journal_flushed
        and (not entry_evidence or checkpoints["confirmed"] == (seconds + 2) // 3),
        "scope": (
            "offline_durable_service_and_journal_not_live_VM_or_strategy_certification"
            if entry_evidence else "offline_synthetic_transport_not_live_VM_or_strategy_certification"
        ),
        "entry_evidence_enabled": entry_evidence,
        "entry_checkpoints": dict(checkpoints),
        "entry_checkpoint_latency": stats(checkpoint_latencies) if checkpoint_latencies else None,
        "journal_flushed": journal_flushed,
        "journal_health_final": journal.persistence_health() if journal is not None else None,
        "journal_bytes": journal.EVENTS_FILE.stat().st_size if journal is not None else None,
        "seconds_requested": seconds, "elapsed_seconds": time.monotonic() - started,
        "read_rate": read_rate, "trade_rate": 1, "read_producers": 4,
        "measured_production_peak": None,
        "reads": dict(reads), "trades": dict(trades),
        "heartbeat": loops, "read_latency": stats(read_latencies), "trade_latency": stats(trade_latencies),
        "max_pending": max(row["pending_count"] for row in memory),
        "memory_first": memory[0], "memory_last": memory[-1],
        "parent_rss_peak": max(row["parent_rss"] for row in memory),
        "worker_rss_peak": max(row["worker_rss"] for row in memory),
        "guard_rss_peak": max(row["guard_rss"] for row in memory),
        "guard_cpu_seconds_sampled": memory[-1]["guard_cpu_seconds"] - memory[0]["guard_cpu_seconds"],
        "guard_stopped": not guard.is_running(),
        "worker_stopped": not client.is_alive, "drained": drained,
        "limitations": [
            "No measured production peak: not the 2x-peak acceptance test.",
            "No live quotes, strategy, VM resource equivalence or broker fills.",
            "Latency samples use bounded memory proportional to configured duration and rate.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=3600)
    parser.add_argument("--read-rate", type=int, default=40)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--entry-evidence", action="store_true", help="Include durable service and real journal fsync in an isolated output directory")
    args = parser.parse_args()
    if not 10 <= args.seconds <= 3600 or not 1 <= args.read_rate <= 50:
        parser.error("bounded run: 10..3600 seconds, 1..50 reads/second")
    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    files = [
        "mt5_client.py", "mt5_scheduling.py", "mt5_worker.py", "mt5_worker_entry.py", "mt5_owner_lock.py",
        "mt5_worker_lifetime.py",
        "mt5_protocol.py", "mt5_read_protocol.py", "mt5_trade_protocol.py",
        "mt5_trade_worker.py", "execution_intents.py", "tests/mt5_load_fakes.py",
        "tests/mt5_read_fakes.py", "tools/probe_mt5_read_isolation.py",
        "tools/probe_mt5_mixed_load.py",
    ]
    if args.entry_evidence:
        files.extend(["durable_execution.py", "journal.py", "runtime_storage.py", "runtime_paths.py", "causal_trace.py"])
    hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files}
    report = asyncio.run(measure(directory, args.seconds, args.read_rate, entry_evidence=args.entry_evidence))
    after = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files}
    report.update(sha256=hashes, sources_unchanged=hashes == after, python=sys.version,
                  platform=platform.platform(), utc_ns=time.time_ns(),
                  monotonic_resolution=time.get_clock_info("monotonic").resolution)
    report["passed"] = report["passed"] and hashes == after
    with (directory / "result.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
