import asyncio
from functools import partial
import json
import os
from pathlib import Path
import subprocess
import sys

import psutil
import pytest

from mt5_client import MT5ReadClient
from mt5_protocol import LookupState
from mt5_read_protocol import ReadOperation, ReadRequest
from mt5_worker import WorkerConfig
import mt5_worker_lifetime as lifetime
from tests.mt5_read_fakes import FakeMT5, FakeTradeMT5, SlowInitializeBackend
from tests.test_mt5_trade_client import trade_request


def guards_of(worker_pid):
    return [p for p in psutil.Process(worker_pid).children() if any(
        Path(arg).name == "mt5_worker_lifetime.py" for arg in p.cmdline()
    )]


def test_creation_identity_mismatch_never_terminates_process():
    with lifetime.ProcessReference(os.getpid()) as own:
        with pytest.raises(ValueError, match="identity mismatch"):
            lifetime.ProcessReference(os.getpid(), own.identity + "0", can_kill=True)
        assert own.is_alive()


def test_guard_rejects_any_target_other_than_its_actual_parent():
    with pytest.raises(ValueError, match="direct child"):
        lifetime.guard_main(os.getppid(), "unused", os.getpid(), "unused")


@pytest.mark.asyncio
async def test_invalid_parent_identity_prevents_native_initialize(tmp_path, monkeypatch):
    marker = tmp_path / "initialize.txt"
    monkeypatch.setattr(lifetime, "current_identity", lambda: "invalid")
    client = MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",)),
        backend_factory=partial(SlowInitializeBackend, marker=str(marker), seconds=60),
    )
    try:
        assert (await client.start()).state is LookupState.UNKNOWN
        assert not marker.exists()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_guard_exits_after_normal_close_without_touching_parent():
    client = MT5ReadClient(WorkerConfig(7, "demo", ("XAUUSD",)), backend_factory=FakeMT5)
    try:
        assert (await client.start()).state is LookupState.FOUND
        guards = guards_of(client.worker_pid)
        assert len(guards) == 1
        await asyncio.sleep(.2)
        assert client.is_alive and guards[0].is_running()
    finally:
        await client.close()
    await asyncio.to_thread(guards[0].wait, timeout=5)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["read", "trade"])
async def test_guard_loss_prevents_next_native_request(tmp_path, operation):
    marker = tmp_path / "send.txt"
    client = MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",)),
        backend_factory=partial(FakeTradeMT5, marker=str(marker)),
        store_path=tmp_path / "intents.sqlite3",
    )
    try:
        assert (await client.start()).state is LookupState.FOUND
        guard, = guards_of(client.worker_pid)
        guard.kill()
        await asyncio.to_thread(guard.wait, timeout=5)
        if operation == "read":
            result = await client.read(ReadRequest(ReadOperation.POSITIONS))
            assert result.state is LookupState.UNKNOWN
            assert result.error == "read_transport_failed"
        else:
            from mt5_protocol import IntentState

            result = await client.execute(trade_request(), reservation_key="guard-loss", policy_revision=0)
            # The guard died before dispatch: this is still a prepared intent,
            # not an ambiguous committed order and never a completed one.
            assert result.state is IntentState.PREPARED
        assert not marker.exists()
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["initialize", "idle", "terminal"])
async def test_parent_death_recovery_before_startup_finishes_or_when_idle(tmp_path, mode):
    parent = subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("mt5_orphan_parent.py")), str(tmp_path), mode],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    worker = guard = terminal = None
    replacement = MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",), owner_lock_path=str(tmp_path / "owner.lock")),
        backend_factory=FakeMT5,
    )
    try:
        deadline = asyncio.get_running_loop().time() + 10
        while not (tmp_path / "worker.json").exists():
            assert parent.poll() is None
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(.02)
        metadata = json.loads((tmp_path / "worker.json").read_text(encoding="ascii"))
        candidate = psutil.Process(metadata["pid"])
        assert candidate.create_time() == metadata["created"]
        worker = candidate
        candidate = psutil.Process(metadata["guard"]["pid"])
        assert candidate.create_time() == metadata["guard"]["created"]
        guard = candidate
        if mode == "terminal":
            info = json.loads((tmp_path / "terminal.json").read_text(encoding="ascii"))
            candidate = psutil.Process(info["pid"])
            assert candidate.create_time() == info["created"]
            terminal = candidate
        parent.kill()
        await asyncio.to_thread(parent.wait, timeout=5)
        await asyncio.to_thread(worker.wait, timeout=5)
        await asyncio.to_thread(guard.wait, timeout=5)
        assert (await replacement.start()).state is LookupState.FOUND
        assert not (tmp_path / "send.txt").exists()
        if terminal is not None:
            assert terminal.is_running(), "guardian must not kill worker descendants"
    finally:
        if parent.poll() is None:
            parent.kill()
        await asyncio.to_thread(parent.wait, timeout=5)
        # Parent death normally performs this cleanup; retain explicit failure
        # cleanup for the disposable worker only, never a system-wide scan.
        for process in (worker, guard, terminal):
            if process is not None and process.is_running():
                process.kill()
                await asyncio.to_thread(process.wait, timeout=5)
        await replacement.close()
