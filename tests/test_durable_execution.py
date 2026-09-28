from functools import partial

import pytest

from durable_execution import DurableExecutionService, ExecutionDisposition
from execution_intents import IntentRecord
from mt5_protocol import BrokerRequest, IntentKey
from mt5_client import MT5ReadClient
from mt5_protocol import BrokerOutcome, IntentState, LookupState
from mt5_worker import WorkerConfig
from tests.mt5_read_fakes import FakeTradeMT5


def client(tmp_path, **backend_kwargs):
    return MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",)),
        backend_factory=partial(FakeTradeMT5, **backend_kwargs),
        store_path=tmp_path / "intents.sqlite3",
    )


def open_payload():
    return {
        "symbol": "XAUUSD",
        "direction": "BUY",
        "volume": 0.01,
        "sl": 2499.0,
        "tp": 2502.0,
        "loss_budget": None,
        "protection_policy": "required",
        "magic": 111,
        "comment": "c1_3086_1",
        "deviation": 30,
    }


class _PredispatchStore:
    def __init__(self, record):
        self.record = record

    def get_predispatch_failure(self, request_id):
        assert request_id == "request-prepare"
        return "requested_sl_waits_for_market"


class _PredispatchClient:
    def __init__(self, record):
        self.config = WorkerConfig(7, "demo", ("XAUUSD",))
        self.store = _PredispatchStore(record)

    async def execute(self, request, **_kwargs):
        return self.store.record


@pytest.mark.asyncio
async def test_service_exposes_typed_predispatch_failure_without_projection():
    key = IntentKey(
        "demo/7", "canal2", "canal2_1", 0, "ticket-42", "MODIFY_SLTP", 0
    )
    request = BrokerRequest.create(
        key,
        {
            "symbol": "XAUUSD",
            "ticket": 42,
            "expected_magic": 222,
            "new_sl": 2500.0,
            "new_tp": 2510.0,
        },
        request_id="request-prepare",
        attempt_id="attempt-prepare",
        action_id="action-prepare",
    )
    record = IntentRecord(
        request.intent_id,
        IntentState.PREPARED,
        None,
        dict(request.payload),
        None,
        0,
        None,
    )
    service = DurableExecutionService(_PredispatchClient(record))

    result = await service.execute(
        request,
        reservation_key="demo/7/canal2/canal2_1/g0/ticket-42",
        projection_key="signal:canal2_1",
    )

    assert result.disposition is ExecutionDisposition.NOT_SENT
    assert result.predispatch_error == "requested_sl_waits_for_market"
    assert result.effect is None


@pytest.mark.asyncio
async def test_service_projects_result_for_reconstruction(tmp_path):
    c = client(tmp_path)
    assert (await c.start()).state is LookupState.FOUND
    service = DurableExecutionService(c)
    request = service.request(
        channel="canal1",
        signal_root="canal1_3086",
        generation=1,
        leg="entry-1",
        operation="OPEN_MARKET",
        revision=0,
        payload=open_payload(),
        action_id="action-open",
    )
    try:
        result = await service.execute(
            request,
            reservation_key="demo/7/canal1/canal1_3086/g1/entry-1",
            projection_key="signal:canal1_3086",
        )
        rebuilt = service.reconstruct(projection_key="signal:canal1_3086")
    finally:
        await c.close()

    assert result.disposition is ExecutionDisposition.APPLIED
    assert result.record.state is IntentState.DONE
    assert result.record.applied_utc is not None
    assert rebuilt[0].projection["outcome"]["deal"] == 702
    assert rebuilt[0].projection["operation"] == "OPEN_MARKET"
    assert rebuilt[0].projection["request_payload"] == open_payload()
    assert c.store.get_reservation(request.intent_id).state == "HELD"


@pytest.mark.asyncio
async def test_broker_outcome_and_projection_do_not_have_a_crash_window(
    tmp_path,
    monkeypatch,
):
    c = client(tmp_path)
    assert (await c.start()).state is LookupState.FOUND
    service = DurableExecutionService(c)
    request = service.request(
        channel="canal1",
        signal_root="canal1_3086",
        generation=0,
        leg="entry-0",
        operation="OPEN_MARKET",
        revision=0,
        payload=open_payload(),
        action_id="action-atomic-open",
    )

    def fail_late_projection(*_args, **_kwargs):
        raise AssertionError("projection was attempted after outcome commit")

    monkeypatch.setattr(c.store, "apply_projection", fail_late_projection)
    try:
        result = await service.execute(
            request,
            reservation_key="demo/7/canal1/canal1_3086/g0/entry-0",
            projection_key="signal:canal1_3086",
        )
    finally:
        await c.close()

    assert result.disposition is ExecutionDisposition.APPLIED
    assert result.record.state is IntentState.DONE
    assert result.record.applied_utc is not None
    projections = c.store.list_projections(projection_key="signal:canal1_3086")
    assert len(projections) == 1
    assert projections[0].projection["outcome"]["order"] == 701


@pytest.mark.asyncio
async def test_management_terminal_projection_releases_reservation_atomically(tmp_path):
    c = client(tmp_path)
    assert (await c.start()).state is LookupState.FOUND
    service = DurableExecutionService(c)
    request = service.request(
        channel="canal2",
        signal_root="canal2_9001",
        generation=0,
        leg="ticket-55",
        operation="PLACE_LIMIT",
        revision=3,
        payload={
            "symbol": "XAUUSD",
            "direction": "SELL",
            "volume": 0.02,
            "price": 2510.0,
            "sl": 2520.0,
            "tp": 2490.0,
            "magic": 222,
            "comment": "c2_9001_55",
            "deviation": 30,
        },
        action_id="action-limit",
    )
    try:
        result = await service.execute(
            request,
            reservation_key="demo/7/canal2/canal2_9001/g0/ticket-55",
            projection_key="signal:canal2_9001",
            release_on_terminal=True,
        )
    finally:
        await c.close()

    assert result.disposition is ExecutionDisposition.APPLIED
    reservation = c.store.get_reservation(request.intent_id)
    assert reservation.state == "RELEASED"
    assert reservation.release_reason == "done_effect_projected"
    assert result.record.applied_utc is not None


@pytest.mark.asyncio
async def test_retry_is_explicit_same_intent_new_attempt_after_known_rejection(tmp_path):
    marker = tmp_path / "sends.txt"
    c = client(tmp_path, marker=str(marker), trade_mode="reject")
    assert (await c.start()).state is LookupState.FOUND
    service = DurableExecutionService(c)
    request = service.request(
        channel="canal1",
        signal_root="canal1_3086",
        generation=1,
        leg="entry-1",
        operation="OPEN_MARKET",
        revision=0,
        payload=open_payload(),
        action_id="action-open",
    )
    reservation_key = "demo/7/canal1/canal1_3086/g1/entry-1"
    try:
        rejected = await service.execute(
            request,
            reservation_key=reservation_key,
            projection_key="signal:canal1_3086",
        )
        c._factory = partial(FakeTradeMT5, marker=str(marker), trade_mode="done")
        # The running worker keeps its backend; switch the deterministic fake in place
        # through a second client sharing the durable store, as a restart would.
        await c.close()
        c2 = client(tmp_path, marker=str(marker), trade_mode="done")
        assert (await c2.start()).state is LookupState.FOUND
        service2 = DurableExecutionService(c2)
        retried = await service2.retry_rejected(
            request,
            retryable_retcodes={10020},
            reason="price_changed_policy_retry",
            reservation_key=reservation_key,
            projection_key="signal:canal1_3086",
        )
    finally:
        if 'c2' in locals():
            await c2.close()
        elif not c._closed:
            await c.close()

    assert rejected.disposition is ExecutionDisposition.REJECTED
    assert retried.disposition is ExecutionDisposition.APPLIED
    assert retried.record.outcome_revision == 2
    assert marker.read_text(encoding="ascii").splitlines() == ["send", "send"]
    projections = c2.store.list_projections(projection_key="signal:canal1_3086")
    assert [row.projection["state"] for row in projections] == ["REJECTED", "DONE"]


@pytest.mark.asyncio
async def test_partial_projection_remains_reconcile_and_holds_reservation(tmp_path):
    c = client(tmp_path, trade_mode="partial")
    assert (await c.start()).state is LookupState.FOUND
    service = DurableExecutionService(c)
    request = service.request(
        channel="canal2",
        signal_root="canal2_9001",
        generation=0,
        leg="entry-1",
        operation="OPEN_MARKET",
        revision=0,
        payload={**open_payload(), "magic": 222, "comment": "c2_9001_1"},
    )
    try:
        result = await service.execute(
            request,
            reservation_key="demo/7/canal2/canal2_9001/g0/entry-1",
            projection_key="signal:canal2_9001",
            release_on_terminal=True,
        )
    finally:
        await c.close()

    assert result.disposition is ExecutionDisposition.RECONCILE
    assert result.record.state is IntentState.DONE_PARTIAL
    assert result.record.applied_utc is not None
    assert c.store.get_reservation(request.intent_id).state == "HELD"

    reconciled = service.reconcile(
        request,
        BrokerOutcome(
            IntentState.DONE,
            retcode=10009,
            order=result.record.outcome["order"],
            deal=result.record.outcome["deal"],
            filled_volume=0.01,
            price=result.record.outcome["price"],
        ),
        evidence={"positions_get": {"ticket": result.record.outcome["order"], "volume": 0.01}},
        projection_key="signal:canal2_9001",
        release_on_terminal=True,
    )
    assert reconciled.disposition is ExecutionDisposition.APPLIED
    assert reconciled.record.outcome_revision == 2
    assert c.store.get_reservation(request.intent_id).state == "RELEASED"
