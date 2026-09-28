import threading

import pytest

from durable_entry_execution import DurableEntryExecutor
from durable_execution import DurableExecutionService, ExecutionDisposition
from mt5_protocol import IntentState, LookupState
from tests.test_durable_entry_execution import _Service, _result, _open
from tests.test_durable_execution import client, open_payload


@pytest.mark.asyncio
async def test_frozen_entry_lookup_does_not_read_disk_on_event_loop():
    loop_thread = threading.get_ident()
    service = _Service(_result(IntentState.DONE, ExecutionDisposition.APPLIED))

    def read(intent_id):
        assert threading.get_ident() != loop_thread, "disk lookup on event loop"
        raise KeyError(intent_id)

    service.store.get = read
    await _open(DurableEntryExecutor(service, symbol="XAUUSD"))


@pytest.mark.asyncio
async def test_entry_rejection_release_does_not_write_disk_on_event_loop():
    loop_thread = threading.get_ident()
    service = _Service(_result(IntentState.REJECTED, ExecutionDisposition.REJECTED))

    def release(*a, **k):
        assert threading.get_ident() != loop_thread, "disk release on event loop"

    service.store.release_reservation = release
    await _open(DurableEntryExecutor(service, symbol="XAUUSD"))


@pytest.mark.asyncio
async def test_terminal_projection_does_not_read_disk_on_event_loop(tmp_path, monkeypatch):
    loop_thread = threading.get_ident()
    transport = client(tmp_path)
    service = DurableExecutionService(transport)
    original = service.store.get

    def read(*a, **k):
        assert threading.get_ident() != loop_thread, "projection read on event loop"
        return original(*a, **k)

    monkeypatch.setattr(service.store, "get", read)
    request = service.request(
        channel="canal1", signal_root="canal1_1", generation=0,
        leg="entry-0", operation="OPEN_MARKET", revision=0, payload=open_payload(),
    )
    try:
        assert (await transport.start()).state is LookupState.FOUND
        result = await service.execute(request, reservation_key="storage-test", projection_key="signal:canal1_1")
        assert result.disposition is ExecutionDisposition.APPLIED
    finally:
        await transport.close()
