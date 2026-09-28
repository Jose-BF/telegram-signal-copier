import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import threading

import pytest

from mt5_client import MT5ReadClient
from mt5_protocol import LookupState
from mt5_read_protocol import ReadOperation as Op, ReadRequest, ReadResponse, MAX_RESPONSE_BYTES, encode_message
from mt5_worker import WorkerConfig
from tests.mt5_read_fakes import (
    FakeMT5, NoisyBackend, SlowInitializeBackend, hold_native_gil,
)


def client(**kwargs):
    capacity = kwargs.pop("queue_size", 8)
    return MT5ReadClient(WorkerConfig(7, "demo", ("XAUUSD",)),
                         backend_factory=partial(FakeMT5, **kwargs), queue_size=capacity)


async def drain(c, timeout=2):
    deadline = time.monotonic() + timeout
    while c.pending_count and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert c.pending_count == 0


async def test_spawn_read_shutdown_in_different_process():
    c = client()
    async with c:
        response = await c.read(ReadRequest(Op.POSITIONS))
        assert response.state is LookupState.FOUND
        assert response.value[0]["ticket"] == 42
        assert response.worker_pid == c.worker_pid != os.getpid()
        assert response.worker_session_id == c.session_id
    assert not c.is_alive and c.pending_count == 0


async def test_timeout_holds_capacity_until_late_response_drained():
    c = client(delay=0.35, queue_size=1)
    async with c:
        response = await c.read(ReadRequest(Op.TICK, {"symbol": "XAUUSD"}), timeout=0.05)
        assert response.state is LookupState.UNKNOWN
        assert response.error == "read_deadline_exceeded"
        assert c.pending_count == 1
        for _ in range(30):
            blocked = await c.read(ReadRequest(Op.POSITIONS))
            assert blocked.error == "read_capacity_exhausted"
        await drain(c)
        assert c.late_responses == 1
        next_read = await c.read(ReadRequest(Op.POSITIONS))
        assert next_read.operation is Op.POSITIONS
        assert next_read.value[0]["ticket"] == 42


async def test_cancel_after_dispatch_keeps_transport_owned_and_next_response_correct():
    c = client(delay=0.3, queue_size=1)
    async with c:
        task = asyncio.create_task(c.read(ReadRequest(Op.TICK, {"symbol": "XAUUSD"})))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert c.pending_count == 1
        await drain(c)
        assert (await c.read(ReadRequest(Op.ORDERS))).state is LookupState.EMPTY


async def test_queued_expired_read_never_enters_native_call():
    c = client(delay=0.25, queue_size=2)
    async with c:
        first = asyncio.create_task(c.read(ReadRequest(Op.TICK, {"symbol": "XAUUSD"})))
        await asyncio.sleep(0.03)
        expired = await c.read(ReadRequest(Op.TICK, {"symbol": "XAUUSD"}), timeout=0.03)
        assert expired.state is LookupState.UNKNOWN
        await first
        started = time.monotonic()
        await drain(c)
        assert time.monotonic() - started < 0.15


async def test_duplicate_active_identity_rejected():
    c = client(delay=0.25)
    req = ReadRequest(Op.TICK, {"symbol": "XAUUSD"})
    async with c:
        first = asyncio.create_task(c.read(req))
        await asyncio.sleep(0.03)
        with pytest.raises(ValueError, match="duplicate"):
            await c.read(req)
        await first


async def test_worker_death_returns_unknown_without_implicit_restart():
    c = client(mode="die")
    async with c:
        response = await c.read(ReadRequest(Op.TICK, {"symbol": "XAUUSD"}))
        assert response.state is LookupState.UNKNOWN
        deadline = time.monotonic() + 1
        while c.is_alive and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert not c.is_alive
        assert (await c.read(ReadRequest(Op.POSITIONS))).state is LookupState.UNKNOWN


async def test_stuck_native_read_has_bounded_close_and_no_child_left():
    c = client(delay=10, native=True)
    await c.start()
    response = await c.read(ReadRequest(Op.TICK, {"symbol": "XAUUSD"}), timeout=0.04)
    assert response.state is LookupState.UNKNOWN
    start = time.monotonic()
    await c.close()
    assert time.monotonic() - start < 3
    assert not c.is_alive and c.pending_count == 0
    await c.close()


async def test_cancel_start_does_not_leave_an_orphan():
    c = client()
    start = asyncio.create_task(c.start())
    await asyncio.sleep(0.001)
    start.cancel()
    with pytest.raises(asyncio.CancelledError):
        await start
    assert not c.is_alive
    assert c._closed


async def test_close_during_start_never_reopens_client():
    c = client()
    start = asyncio.create_task(c.start())
    await asyncio.sleep(0.001)
    await c.close()
    await start
    assert not c.is_alive
    assert (await c.read(ReadRequest(Op.POSITIONS))).error == "client_not_ready"


async def test_read_before_start_and_after_close_never_starts_terminal():
    c = client()
    assert (await c.read(ReadRequest(Op.POSITIONS))).state is LookupState.UNKNOWN
    assert c.worker_pid is None
    await c.close()
    with pytest.raises(RuntimeError, match="closed"):
        await c.start()
    with pytest.raises(ValueError, match="lifecycle"):
        await c.read(ReadRequest(Op.INITIALIZE))


async def test_invalid_timeout_does_not_spawn():
    c = client()
    try:
        for value in (0, -1, float("nan"), float("inf"), True):
            with pytest.raises(ValueError):
                await c.start(timeout=value)
        assert c.worker_pid is None
    finally:
        await c.close()


async def test_start_deadline_includes_slow_spawn_and_close_still_owns_child(monkeypatch):
    c = client()
    original = c._spawn

    def slow_spawn():
        time.sleep(0.25)
        original()

    monkeypatch.setattr(c, "_spawn", slow_spawn)
    try:
        started = time.monotonic()
        response = await c.start(timeout=0.03)
        assert response.state is LookupState.UNKNOWN
        assert time.monotonic() - started < 0.15
    finally:
        await c.close()
    assert not c.is_alive


async def test_queued_cancellation_does_not_execute_a_second_native_wait():
    c = client(delay=0.25, queue_size=2)
    async with c:
        first = asyncio.create_task(c.read(ReadRequest(Op.TICK, {"symbol": "XAUUSD"})))
        await asyncio.sleep(0.02)
        queued = asyncio.create_task(c.read(ReadRequest(Op.TICK, {"symbol": "XAUUSD"})))
        await asyncio.sleep(0.02)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        await first
        started = time.monotonic()
        await drain(c)
        assert time.monotonic() - started < 0.15


async def test_concurrent_start_callers_have_their_own_deadline(monkeypatch):
    c = client()
    original = c._spawn
    def slow_spawn():
        time.sleep(0.2)
        original()
    monkeypatch.setattr(c, "_spawn", slow_spawn)
    first = asyncio.create_task(c.start(timeout=3))
    await asyncio.sleep(0.01)
    try:
        started = time.monotonic()
        second = await c.start(timeout=0.02)
        assert second.state is LookupState.UNKNOWN
        assert time.monotonic() - started < 0.15
        assert (await first).state is LookupState.FOUND
    finally:
        await c.close()


async def heartbeat_for(duration):
    moments = [time.monotonic()]
    end = moments[0] + duration
    while time.monotonic() < end:
        await asyncio.sleep(0.01)
        moments.append(time.monotonic())
    return [b - a for a, b in zip(moments, moments[1:])]


async def test_native_gil_hold_does_not_block_parent_loop():
    c = client(delay=0.5, native=True)
    async with c:
        heartbeat = asyncio.create_task(heartbeat_for(0.7))
        await asyncio.sleep(0.03)
        response = await c.read(ReadRequest(Op.TICK, {"symbol": "XAUUSD"}), timeout=2)
        gaps = await heartbeat
        assert response.state is LookupState.FOUND
        assert len(gaps) > 30 and max(gaps) < 0.2


async def test_native_negative_control_really_holds_parent_gil():
    heartbeat = asyncio.create_task(heartbeat_for(0.5))
    await asyncio.sleep(0.03)
    await asyncio.to_thread(hold_native_gil, 0.3)
    assert max(await heartbeat) >= 0.28


def test_worker_bootstrap_does_not_reexecute_application_entrypoint():
    probe = Path(__file__).with_name("mt5_read_entry_probe.py")
    result = subprocess.run(
        [sys.executable, str(probe)], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    assert evidence["response"]["data"]["pid"] != evidence["parent_pid"]
    assert evidence["response"]["data"]["application_entry_reran"] is False


async def test_close_does_not_depend_on_saturated_default_executor():
    loop = asyncio.get_running_loop()
    default_executor = ThreadPoolExecutor(max_workers=1)
    loop.set_default_executor(default_executor)
    c = client(delay=10, native=True)
    await c.start()
    gate = threading.Event()
    entered = threading.Event()

    def occupy_default_pool():
        entered.set()
        gate.wait(5)

    blocker = loop.run_in_executor(None, occupy_default_pool)
    while not entered.is_set():
        await asyncio.sleep(0.005)
    closing = None
    try:
        response = await c.read(
            ReadRequest(Op.TICK, {"symbol": "XAUUSD"}), timeout=0.04
        )
        assert response.state is LookupState.UNKNOWN
        closing = asyncio.create_task(c.close())
        await asyncio.wait_for(asyncio.shield(closing), timeout=3.0)
        assert not c.is_alive
    finally:
        gate.set()
        await blocker
        if closing is not None and not closing.done():
            await closing
        elif not c._closed:
            await c.close()
        default_executor.shutdown(wait=True)


async def test_bootstrap_keeps_config_secrets_out_of_process_arguments():
    secret = "review-secret-must-not-appear"
    c = MT5ReadClient(
        WorkerConfig(
            7,
            "demo",
            ("XAUUSD",),
            initialize_options={"login": 7, "server": "demo", "password": secret},
        ),
        backend_factory=FakeMT5,
    )
    try:
        assert (await c.start()).state is LookupState.FOUND
        assert secret not in " ".join(c._process.args)
    finally:
        await c.close()


async def test_backend_stdout_cannot_corrupt_protocol_stream():
    c = MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",)), backend_factory=NoisyBackend
    )
    async with c:
        response = await c.read(ReadRequest(Op.POSITIONS))
        assert response.state is LookupState.FOUND
        assert response.value[0]["ticket"] == 42


@pytest.mark.parametrize("noise", ["descriptor", "original_stdout"])
async def test_backend_native_stdout_cannot_corrupt_protocol_stream(noise):
    c = MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",)),
        backend_factory=partial(NoisyBackend, noise=noise),
    )
    async with c:
        response = await c.read(ReadRequest(Op.POSITIONS))
        assert response.state is LookupState.FOUND
        assert response.value[0]["ticket"] == 42


async def wait_for_file(path, timeout=3):
    deadline = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < deadline
        await asyncio.sleep(0.005)


async def test_close_interrupts_blocked_worker_initialization(tmp_path):
    marker = tmp_path / "initializing"
    c = MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",)),
        backend_factory=partial(
            SlowInitializeBackend, marker=str(marker), seconds=10,
        ),
    )
    starting = asyncio.create_task(c.start(timeout=20))
    await wait_for_file(marker)
    started = time.monotonic()
    await c.close()
    assert time.monotonic() - started < 3
    assert (await starting).state is LookupState.UNKNOWN
    assert not c.is_alive and c.pending_count == 0


async def test_close_interrupts_blocked_bootstrap_write(tmp_path, monkeypatch):
    marker = tmp_path / "child_created"
    original_popen = subprocess.Popen
    children = []

    def delayed_popen(args, **kwargs):
        script = (
            "import pathlib,runpy,sys,time; "
            "pathlib.Path(sys.argv[1]).touch(); time.sleep(10); "
            "runpy.run_path(sys.argv[2],run_name='__main__')"
        )
        process = original_popen(
            [sys.executable, "-u", "-c", script, str(marker), args[-1]], **kwargs
        )
        children.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", delayed_popen)
    c = MT5ReadClient(
        WorkerConfig(
            7, "demo", ("XAUUSD",), initialize_options={"password": "x" * 12000}
        ),
        backend_factory=FakeMT5,
    )
    assert (await c.start(timeout=0.1)).state is LookupState.UNKNOWN
    await wait_for_file(marker)
    assert c.worker_pid == children[0].pid
    started = time.monotonic()
    await c.close()
    assert time.monotonic() - started < 3
    assert children[0].poll() is not None
    assert not c.is_alive and c.pending_count == 0


@pytest.mark.parametrize("mutation", ["request_id", "operation", "worker_session_id", "worker_pid", "clock", "size", "malformed"])
def test_transport_rejects_cross_request_session_clock_and_oversized_response(mutation):
    c = client()
    req = ReadRequest(Op.POSITIONS)

    class Process:
        pid = 12345
        def is_alive(self):
            return True

    class Connection:
        def send_bytes(self, raw):
            pass
        def poll(self, timeout):
            return True
        def recv_bytes(self, limit):
            now = time.monotonic()
            fields = ReadResponse(req.request_id, req.operation, LookupState.EMPTY,
                                  c.session_id, 12345, now, now, time.time_ns(), {"data": []},
                                  native_error=(1, "Success")).to_dict()
            if mutation in {"request_id", "worker_session_id"}:
                fields[mutation] = "wrong"
            elif mutation == "operation":
                fields[mutation] = Op.ORDERS.value
            elif mutation == "worker_pid":
                fields[mutation] = 54321
            elif mutation == "clock":
                fields["started_monotonic"] += 60
                fields["completed_monotonic"] += 60
            elif mutation == "size":
                return b" " * (limit + 1)
            elif mutation == "malformed":
                return b"{broken"
            return encode_message(fields, MAX_RESPONSE_BYTES)

    c._process, c._connection = Process(), Connection()
    try:
        response = c._exchange(req, time.monotonic() + 1, threading.Event())
        assert response.state is LookupState.UNKNOWN
        assert response.error == "read_transport_failed"
        assert c._stop.is_set()
    finally:
        c._executor.shutdown()
