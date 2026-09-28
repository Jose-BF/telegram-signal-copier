"""Disposable owner that is killed after a mock broker effect and before its reply."""

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
from tests.test_entry_history_transport import LostReplyHistoryMT5, entry_request


async def run(directory):
    client = MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",), owner_lock_path=str(directory / "owner.lock")),
        backend_factory=partial(
            LostReplyHistoryMT5, ledger_path=str(directory / "broker.json"),
            marker=str(directory / "sends.txt"), hang_after_send=60),
        store_path=directory / "intents.sqlite3",
    )
    try:
        assert (await client.start()).state is LookupState.FOUND
        worker = psutil.Process(client.worker_pid)
        metadata = directory / "worker.tmp"
        metadata.write_text(json.dumps({"pid": worker.pid, "created": worker.create_time()}), encoding="ascii")
        metadata.replace(directory / "worker.json")
        await client.execute(
            entry_request("BUY"),
            reservation_key="demo/7/canal1/canal1_3086/g1/entry-1",
            policy_revision=0, timeout=90,
        )
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(run(Path(sys.argv[1]).resolve()))
