"""Application-like entry script used to verify worker bootstrap isolation."""

import asyncio
import json
import os
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.modules["mt5_read_application_marker"] = types.ModuleType("mt5_read_application_marker")

from mt5_client import MT5ReadClient
from mt5_read_protocol import ReadOperation, ReadRequest
from mt5_worker import WorkerConfig
from tests.mt5_read_fakes import EntryProbeBackend


async def main():
    client = MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",)),
        backend_factory=EntryProbeBackend,
    )
    async with client:
        response = await client.read(
            ReadRequest(ReadOperation.TICK, {"symbol": "XAUUSD"})
        )
        print(json.dumps({"parent_pid": os.getpid(), "response": response.to_dict()}))


if __name__ == "__main__":
    asyncio.run(main())
