"""Disposable parent for the offline orphan-owner regression."""

import asyncio
from functools import partial
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psutil

from mt5_client import MT5ReadClient
from mt5_protocol import LookupState
from mt5_worker import WorkerConfig
from tests.mt5_read_fakes import FakeTradeMT5, SlowInitializeBackend
from tests.mt5_lifetime_fakes import DescendantBackend
from tests.test_mt5_trade_client import trade_request


async def run(directory, mode="trade"):
    backend = (
        partial(SlowInitializeBackend, marker=str(directory / "initialize.txt"), seconds=60)
        if mode == "initialize" else
        partial(FakeTradeMT5, marker=str(directory / "send.txt"), trade_native=True, trade_delay=60)
    )
    if mode == "terminal":
        backend = partial(DescendantBackend, marker=str(directory / "terminal.json"))
    client = MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",), owner_lock_path=str(directory / "owner.lock")),
        backend_factory=backend,
        store_path=directory / "intents.sqlite3",
    )
    try:
        starting = asyncio.create_task(client.start(timeout=90))
        if mode == "initialize":
            while not (directory / "initialize.txt").exists():
                if starting.done():
                    raise RuntimeError("initialization exited before native wait")
                await asyncio.sleep(.01)
        else:
            response = await starting
            assert response.state is LookupState.FOUND
        worker = psutil.Process(client.worker_pid)
        guards = [p for p in worker.children() if any(
            Path(arg).name == "mt5_worker_lifetime.py" for arg in p.cmdline()
        )]
        metadata_path = directory / "worker.tmp"
        metadata_path.write_text(json.dumps({
            "pid": worker.pid, "created": worker.create_time(),
            "guard": {"pid": guards[0].pid, "created": guards[0].create_time()} if guards else None,
        }), encoding="ascii")
        metadata_path.replace(directory / "worker.json")
        if mode == "initialize":
            await starting
            return
        if mode in {"idle", "terminal"}:
            await asyncio.Event().wait()
        await client.execute(
            trade_request(), reservation_key="orphan-test", policy_revision=0,
            timeout=90,
        )
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(run(Path(sys.argv[1]).resolve(), sys.argv[2] if len(sys.argv) > 2 else "trade"))
