"""Real venv subprocess probe, never a native MT5 connection."""

import asyncio
from functools import partial
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psutil

from mt5_client import MT5ReadClient
from mt5_protocol import LookupState
from mt5_read_protocol import ReadOperation, ReadRequest
from mt5_worker import WorkerConfig
from tests.mt5_lifetime_fakes import EnvironmentBackend
from tests.test_mt5_trade_client import trade_request


async def run(directory, mode):
    marker = directory / "send.txt"
    client = MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",), owner_lock_path=str(directory / "owner.lock")),
        backend_factory=partial(EnvironmentBackend, marker=str(marker), trade_native=True, trade_delay=60),
        store_path=directory / "intents.sqlite3",
    )
    try:
        response = await client.start()
        assert response.state is LookupState.FOUND, response.error
        assert response.worker_pid == client.worker_pid
        account = await client.read(ReadRequest(ReadOperation.ACCOUNT))
        assert account.value["python_prefix"] == sys.prefix
        worker = psutil.Process(client.worker_pid)
        guard, = [p for p in worker.children() if any(
            Path(arg).name == "mt5_worker_lifetime.py" for arg in p.cmdline()
        )]
        (directory / "processes.json").write_text(json.dumps({
            "processes": [{"pid": p.pid, "created": p.create_time()} for p in (worker, guard)],
            "prefix": sys.prefix,
        }), encoding="ascii")
        if mode == "kill":
            pending = asyncio.create_task(client.execute(
                trade_request(), reservation_key="venv-probe", policy_revision=0, timeout=90,
            ))
            while not marker.exists():
                assert not pending.done(), "trade finished before native wait"
                await asyncio.sleep(.01)
            os._exit(23)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(run(Path(sys.argv[1]), sys.argv[2]))
