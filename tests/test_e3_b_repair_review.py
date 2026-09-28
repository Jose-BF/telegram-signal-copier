"""Independent checks of recovery across outcome revisions and redelivery."""

import asyncio
import sqlite3
from functools import partial

import pytest

import execution_intents
from durable_entry_execution import DurableEntryExecutor, EntryDispatchState
from durable_execution import DurableExecutionService, ExecutionDisposition
from execution_intents import (
    IntentConflictError,
    IntentStore,
    ReservationConflictError,
)
from mt5_client import MT5ReadClient
from mt5_protocol import BrokerOutcome, BrokerRequest, IntentState, LookupState
from mt5_protocol import IntentKey
from mt5_worker import WorkerConfig
from tests.test_e3_b_review_regressions import (
    _open_request, _record_terminal, _service,
)
from tests.mt5_trade_review_fakes import OwnedTradeMT5


@pytest.mark.parametrize("project_terminal", [False, True])
def test_reconstruction_uses_latest_reconciled_outcome(tmp_path, project_terminal):
    path = tmp_path / "review.sqlite3"
    store = IntentStore(path)
    request = _open_request()
    record = _record_terminal(
        store, request, BrokerOutcome(IntentState.UNKNOWN, error="response_lost")
    )
    service = _service(store)
    service._project(
        request, record, projection_key="signal:canal1_3086",
        release_on_terminal=False,
    )
    store.reconcile_outcome(
        request,
        BrokerOutcome(IntentState.DONE, retcode=10009, order=81234,
                      deal=91234, filled_volume=0.01, price=2500.25),
        evidence={"position_ticket": 81234},
        projection_key="signal:canal1_3086" if project_terminal else None,
    )

    recovered = DurableEntryExecutor(_service(IntentStore(path)), symbol="XAUUSD").reconstruct()

    assert [(r.state, r.ticket, r.outcome_revision) for r in recovered] == [
        (EntryDispatchState.CONFIRMED, 81234, 2)
    ]


def test_reconstruction_does_not_hide_a_pending_retry_behind_old_rejection(tmp_path):
    store = IntentStore(tmp_path / "review.sqlite3")
    request = _open_request()
    record = _record_terminal(
        store, request, BrokerOutcome(IntentState.REJECTED, retcode=10020)
    )
    service = _service(store)
    service._project(
        request, record, projection_key="signal:canal1_3086",
        release_on_terminal=False,
    )
    retry = BrokerRequest.create(
        request.intent_key, dict(request.payload), action_id=request.action_id
    )
    store.prepare_retry(retry, previous_attempt_id=request.attempt_id,
                        reason="price changed", retryable_retcodes={10020})
    store.begin_dispatch(retry, worker_session_id="review", worker_pid=1234)

    recovered = DurableEntryExecutor(service, symbol="XAUUSD").reconstruct()

    assert len(recovered) == 1
    assert recovered[0].state is EntryDispatchState.RECONCILE
    assert recovered[0].reason == "durable_dispatching"


def test_reconstruction_excludes_superseded_unsent_policy_revision(tmp_path):
    store = IntentStore(tmp_path / "review.sqlite3")
    old = _open_request()
    reservation_key = "demo/7/canal1/canal1_3086/g0/entry-0"
    store.prepare_reserved(old, reservation_key=reservation_key, policy_revision=0)
    new_key = IntentKey(
        "demo/7", "canal1", "canal1_3086", 0,
        "entry-0", "OPEN_MARKET", 1,
    )
    new = BrokerRequest.create(new_key, dict(old.payload), action_id=old.action_id)
    store.prepare_reserved(new, reservation_key=reservation_key, policy_revision=1)

    recovered = DurableEntryExecutor(_service(store), symbol="XAUUSD").reconstruct()

    assert len(recovered) == 1
    assert recovered[0].intent_id == new.intent_id
    assert recovered[0].policy_revision == 1
    assert recovered[0].state is EntryDispatchState.NOT_SENT


@pytest.mark.asyncio
async def test_confirmed_management_is_readable_after_reservation_release(tmp_path):
    marker = tmp_path / "sends.txt"
    c = MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",)),
        backend_factory=partial(OwnedTradeMT5, marker=str(marker)),
        store_path=tmp_path / "intents.sqlite3",
    )
    assert (await c.start()).state is LookupState.FOUND
    service = DurableExecutionService(c)
    request = service.request(
        channel="canal2", signal_root="canal2_9001", generation=0,
        leg="ticket-55", operation="CLOSE_POSITION", revision=3,
        payload={"symbol": "XAUUSD", "ticket": 55,
                 "expected_magic": 222, "deviation": 30},
    )
    kwargs = dict(reservation_key="demo/7/canal2/canal2_9001/g0/ticket-55",
                  projection_key="signal:canal2_9001", release_on_terminal=True)
    try:
        first = await service.execute(request, **kwargs)
        assert first.disposition is ExecutionDisposition.APPLIED
        assert c.store.get_reservation(request.intent_id).state == "RELEASED"
        redelivery = BrokerRequest.create(
            request.intent_key, dict(request.payload), action_id=request.action_id
        )
        second = await service.execute(redelivery, **kwargs)
        assert second.disposition is ExecutionDisposition.APPLIED
        assert second.record.attempt_id == first.record.attempt_id
    finally:
        await c.close()
        assert marker.read_text(encoding="ascii").splitlines() == ["send"]


async def wait_for_marker(marker, *, timeout=2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not marker.exists():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("native order_send did not start")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_late_confirmed_management_is_readable_without_second_send(tmp_path):
    marker = tmp_path / "sends.txt"
    c = MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",)),
        backend_factory=partial(
            OwnedTradeMT5,
            marker=str(marker),
            trade_delay=0.2,
            trade_native=True,
        ),
        store_path=tmp_path / "intents.sqlite3",
    )
    assert (await c.start()).state is LookupState.FOUND
    service = DurableExecutionService(c)
    request = service.request(
        channel="canal2", signal_root="canal2_9001", generation=0,
        leg="ticket-55", operation="CLOSE_POSITION", revision=3,
        payload={"symbol": "XAUUSD", "ticket": 55,
                 "expected_magic": 222, "deviation": 30},
    )
    kwargs = dict(reservation_key="demo/7/canal2/canal2_9001/g0/ticket-55",
                  projection_key="signal:canal2_9001", release_on_terminal=True)
    try:
        execution = asyncio.create_task(service.execute(request, timeout=2.0, **kwargs))
        await wait_for_marker(marker)
        execution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await execution
        assert c.store.get(request.intent_id).state is IntentState.DISPATCHING
        while c.pending_count:
            await asyncio.sleep(0.02)
        redelivery = BrokerRequest.create(
            request.intent_key, dict(request.payload), action_id=request.action_id
        )
        recovered = await service.execute(redelivery, **kwargs)
        assert recovered.disposition is ExecutionDisposition.APPLIED
        assert recovered.record.state is IntentState.DONE
    finally:
        await c.close()
    assert marker.read_text(encoding="ascii").splitlines() == ["send"]


def test_reopened_store_reads_released_terminal_but_rejects_drift(tmp_path):
    path = tmp_path / "review.sqlite3"
    store = IntentStore(path)
    key = IntentKey(
        "demo/7", "canal2", "canal2_9001", 0,
        "ticket-55", "CLOSE_POSITION", 3,
    )
    payload = {"symbol": "XAUUSD", "ticket": 55,
               "expected_magic": 222, "deviation": 30}
    request = BrokerRequest.create(key, payload, action_id="close-action")
    reservation_key = "demo/7/canal2/canal2_9001/g0/ticket-55"
    store.prepare_reserved(request, reservation_key=reservation_key,
                           policy_revision=3)
    store.begin_dispatch(request, worker_session_id="review", worker_pid=1234)
    store.record_outcome(
        request,
        BrokerOutcome(IntentState.DONE, retcode=10009, order=81234),
        projection_key="signal:canal2_9001",
        release_reservation_reason="done_effect_projected",
    )

    restarted = IntentStore(path)
    same = BrokerRequest.create(key, payload, action_id="close-action")
    record = restarted.prepare_reserved(
        same, reservation_key=reservation_key, policy_revision=3
    )
    assert record.state is IntentState.DONE
    assert record.attempt_id == request.attempt_id

    drifted = BrokerRequest.create(
        key, {**payload, "deviation": 31}, action_id="close-action"
    )
    with pytest.raises(IntentConflictError, match="different identity or payload"):
        restarted.prepare_reserved(
            drifted, reservation_key=reservation_key, policy_revision=3
        )
    with pytest.raises(ReservationConflictError, match="different content"):
        restarted.prepare_reserved(
            same, reservation_key=f"{reservation_key}-other", policy_revision=3
        )


def test_atomic_projection_failure_rolls_back_outcome_and_release(tmp_path, monkeypatch):
    path = tmp_path / "review.sqlite3"
    store = IntentStore(path)
    request = _open_request()
    store.prepare_reserved(request, reservation_key="review", policy_revision=0)
    store.begin_dispatch(request, worker_session_id="review", worker_pid=1234)
    original = execution_intents._apply_effect_projection_in_transaction

    def fail_after_projection(*args, **kwargs):
        original(*args, **kwargs)
        raise sqlite3.OperationalError("injected failure before commit")

    monkeypatch.setattr(execution_intents, "_apply_effect_projection_in_transaction",
                        fail_after_projection)
    outcome = BrokerOutcome(IntentState.DONE, retcode=10009, order=81234,
                            filled_volume=0.01, price=2500.25)
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        store.record_outcome(request, outcome, projection_key="signal:canal1_3086",
                             release_reservation_reason="done_effect_projected")
    restarted = IntentStore(path)
    assert restarted.get(request.intent_id).state is IntentState.DISPATCHING
    assert restarted.list_projections() == []
    assert restarted.get_reservation(request.intent_id).state == "HELD"
    monkeypatch.setattr(execution_intents, "_apply_effect_projection_in_transaction", original)
    record = restarted.record_outcome(
        request, outcome, projection_key="signal:canal1_3086",
        release_reservation_reason="done_effect_projected",
    )
    assert record.state is IntentState.DONE
    assert record.applied_utc is not None
    assert len(restarted.list_projections()) == 1
    assert restarted.get_reservation(request.intent_id).state == "RELEASED"
