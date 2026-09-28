from functools import partial
import asyncio
import json
from pathlib import Path
import subprocess
import sys

import psutil

import pytest

from mt5_client import MT5ReadClient
from mt5_owner_lock import OwnerBusyError, OwnerLock
from mt5_protocol import LookupState, IntentState
from mt5_worker import WorkerConfig
from tests.mt5_read_fakes import FakeMT5, FakeTradeMT5
from tests.test_mt5_trade_client import trade_request


def test_exclusive_lock_is_released_without_removing_identity(tmp_path):
    path = tmp_path / "owner.lock"
    first, second = OwnerLock(path), OwnerLock(path)
    try:
        first.acquire()
        with pytest.raises(OwnerBusyError):
            second.acquire()
        first.close()
        second.acquire()
        assert path.exists()
    finally:
        first.close()
        second.close()


@pytest.mark.asyncio
async def test_second_worker_cannot_initialize_until_first_has_stopped(tmp_path):
    config = WorkerConfig(7, "demo", ("XAUUSD",), owner_lock_path=str(tmp_path / "owner.lock"))
    first = MT5ReadClient(config, backend_factory=FakeMT5)
    second = MT5ReadClient(config, backend_factory=FakeMT5)
    third = MT5ReadClient(config, backend_factory=FakeMT5)
    try:
        assert (await first.start()).state is LookupState.FOUND
        rejected = await second.start()
        assert rejected.state is LookupState.UNKNOWN
        assert rejected.error == "owner_already_active"
        assert first.is_alive
        await first.close()
        assert not first.is_alive
        assert (await third.start()).state is LookupState.FOUND
    finally:
        await first.close()
        await second.close()
        await third.close()


@pytest.mark.asyncio
async def test_parent_death_automatically_releases_owner_without_repeating_order(tmp_path):
    parent = subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("mt5_orphan_parent.py")), str(tmp_path)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    config = WorkerConfig(7, "demo", ("XAUUSD",), owner_lock_path=str(tmp_path / "owner.lock"))
    blocked = MT5ReadClient(config, backend_factory=FakeMT5)
    replacement = MT5ReadClient(
        config, backend_factory=partial(FakeTradeMT5, marker=str(tmp_path / "send.txt")),
        store_path=tmp_path / "intents.sqlite3",
    )
    orphan = None
    try:
        deadline = asyncio.get_running_loop().time() + 10
        while not (tmp_path / "send.txt").exists():
            assert parent.poll() is None, "disposable parent exited before dispatch"
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(.02)
        metadata = json.loads((tmp_path / "worker.json").read_text(encoding="ascii"))
        candidate = psutil.Process(metadata["pid"])
        assert candidate.create_time() == metadata["created"]
        orphan = candidate
        assert (await blocked.start()).error == "owner_already_active"
        parent.kill()
        await asyncio.to_thread(parent.wait, timeout=5)
        assert (tmp_path / "send.txt").read_text().splitlines() == ["send"]
        # No manual orphan termination: recovery must work during a native
        # call holding the worker's GIL, without waiting for its 60 s delay.
        await asyncio.to_thread(orphan.wait, timeout=5)
        assert (await replacement.start()).state is LookupState.FOUND
        record = await replacement.execute(
            trade_request(attempt="redelivery"), reservation_key="orphan-test",
            policy_revision=0,
        )
        assert record.state is IntentState.UNKNOWN
        assert (tmp_path / "send.txt").read_text().splitlines() == ["send"]
    finally:
        if parent.poll() is None:
            parent.kill()
        await asyncio.to_thread(parent.wait, timeout=5)
        if orphan is None and (tmp_path / "worker.json").exists():
            metadata = json.loads((tmp_path / "worker.json").read_text(encoding="ascii"))
            try:
                candidate = psutil.Process(metadata["pid"])
                if candidate.create_time() == metadata["created"]:
                    orphan = candidate
            except psutil.NoSuchProcess:
                pass
        if orphan is not None and orphan.is_running():
            orphan.kill()
            await asyncio.to_thread(orphan.wait, timeout=5)
        await blocked.close()
        await replacement.close()
