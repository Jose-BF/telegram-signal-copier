"""Independent Astra review regressions for the E3-B candidate."""

import time
from types import SimpleNamespace

import pytest

from durable_entry_execution import DurableEntryExecutor, EntryDispatchState
from durable_execution import (
    DurableExecutionResult,
    DurableExecutionService,
    ExecutionDisposition,
)
from execution_intents import IntentRecord, IntentStore
from mt5_protocol import BrokerOutcome, BrokerRequest, IntentKey, IntentState
from pending_actions import PendingAction, PendingQueue
from state import Signal


def _open_request() -> BrokerRequest:
    return BrokerRequest.create(
        IntentKey(
            "demo/7",
            "canal1",
            "canal1_3086",
            0,
            "entry-0",
            "OPEN_MARKET",
            0,
        ),
        {
            "symbol": "XAUUSD",
            "direction": "BUY",
            "volume": 0.01,
            "sl": 2480.0,
            "tp": None,
            "loss_budget": None,
            "protection_policy": "required",
            "magic": 7001,
            "comment": "review",
            "deviation": 30,
        },
    )


def _record_terminal(store: IntentStore, request: BrokerRequest, outcome: BrokerOutcome):
    store.prepare_reserved(
        request,
        reservation_key="demo/7/canal1/canal1_3086/g0/entry-0",
        policy_revision=0,
    )
    store.begin_dispatch(request, worker_session_id="review", worker_pid=1234)
    return store.record_outcome(request, outcome)


def _service(store: IntentStore):
    client = SimpleNamespace(
        store=store,
        config=SimpleNamespace(expected_server="demo", expected_login=7),
    )
    return DurableExecutionService(client)


def test_terminal_entry_without_projection_remains_reconstructible(tmp_path):
    """A crash between broker outcome and projection must not hide exposure."""
    store = IntentStore(tmp_path / "review.sqlite3")
    request = _open_request()
    _record_terminal(
        store,
        request,
        BrokerOutcome(
            IntentState.DONE,
            retcode=10009,
            order=81234,
            deal=91234,
            filled_volume=0.01,
            price=2500.25,
        ),
    )

    recovered = DurableEntryExecutor(_service(store), symbol="XAUUSD").reconstruct()

    assert len(recovered) == 1
    assert recovered[0].state is EntryDispatchState.CONFIRMED
    assert recovered[0].ticket == 81234


def test_known_rejected_entry_is_not_reconstructed_as_ambiguous_exposure(tmp_path):
    store = IntentStore(tmp_path / "review.sqlite3")
    request = _open_request()
    record = _record_terminal(
        store,
        request,
        BrokerOutcome(IntentState.REJECTED, retcode=10019, error="no_money"),
    )
    service = _service(store)
    service._project(
        request,
        record,
        projection_key="signal:canal1_3086",
        release_on_terminal=False,
    )

    recovered = DurableEntryExecutor(service, symbol="XAUUSD").reconstruct()

    assert len(recovered) == 1
    assert recovered[0].state is EntryDispatchState.REJECTED
    assert recovered[0].ticket is None


class _CooldownService:
    def __init__(self, result=None):
        self.execute_calls = 0
        self.retry_calls = 0
        self.result = result
        self.store = SimpleNamespace()

    def request(self, **values):
        key = IntentKey(
            "demo/7",
            values["channel"],
            values["signal_root"],
            values["generation"],
            values["leg"],
            values["operation"],
            values["revision"],
        )
        return BrokerRequest.create(key, values["payload"], action_id=values["action_id"])

    async def execute(self, request, **_kwargs):
        self.execute_calls += 1
        if self.result is not None:
            return self.result
        record = IntentRecord(
            request.intent_id,
            IntentState.DONE,
            request.attempt_id,
            dict(request.payload),
            BrokerOutcome(IntentState.DONE, retcode=10009).to_dict(),
            1,
            "2026-09-20T00:00:00+00:00",
        )
        return DurableExecutionResult(record, ExecutionDisposition.APPLIED, {"state": "DONE"})

    async def retry_rejected(self, *_args, **_kwargs):
        self.retry_calls += 1
        raise AssertionError("a fresh rejection must wait for the next queue cycle")


@pytest.mark.asyncio
async def test_durable_management_honors_existing_retry_cooldown():
    service = _CooldownService()
    queue = PendingQueue(execution_service=service)
    signal = Signal(channel="canal2", message_id=3086, direction="BUY")
    action = PendingAction(
        kind="MODIFY_SLTP",
        ticket=81234,
        signal=signal,
        new_sl=2490.0,
        label="review cooldown",
    )
    action.retry_not_before = time.time() + 30.0

    result = await queue._try_once(action)

    assert result == "WAIT_RETRY_COOLDOWN"
    assert service.execute_calls == 0
    assert action.attempts == 0


@pytest.mark.asyncio
async def test_fresh_durable_rejection_does_not_retry_inside_the_same_tick():
    record = IntentRecord(
        "intent-frozen",
        IntentState.REJECTED,
        "attempt-frozen",
        {"ticket": 81234},
        BrokerOutcome(
            IntentState.REJECTED,
            retcode=10029,
            error="frozen",
        ).to_dict(),
        1,
        "2026-09-20T00:00:00+00:00",
    )
    service = _CooldownService(DurableExecutionResult(
        record,
        ExecutionDisposition.REJECTED,
        {"state": "REJECTED"},
    ))
    queue = PendingQueue(execution_service=service)
    signal = Signal(channel="canal2", message_id=3086, direction="BUY")
    action = PendingAction(
        kind="MODIFY_SLTP",
        ticket=81234,
        signal=signal,
        new_sl=2490.0,
        label="review frozen",
    )

    result = await queue._try_once(action)

    assert result == "RETRY"
    assert service.execute_calls == 1
    assert service.retry_calls == 0
    assert action.attempts == 1
    assert action.retry_not_before > time.time()


@pytest.mark.asyncio
async def test_polling_same_ambiguous_attempt_does_not_inflate_attempt_count():
    record = IntentRecord(
        "intent-unknown",
        IntentState.UNKNOWN,
        "attempt-unknown",
        {"ticket": 81234},
        BrokerOutcome(IntentState.UNKNOWN, error="transport").to_dict(),
        1,
        "2026-09-20T00:00:00+00:00",
    )
    service = _CooldownService(DurableExecutionResult(
        record,
        ExecutionDisposition.RECONCILE,
        {"state": "UNKNOWN"},
    ))
    queue = PendingQueue(execution_service=service)
    signal = Signal(channel="canal2", message_id=3086, direction="BUY")
    action = PendingAction(
        kind="CLOSE_POSITION",
        ticket=81234,
        signal=signal,
        label="review ambiguous",
    )

    assert await queue._try_once(action) == "WAIT_RECONCILIATION"
    assert await queue._try_once(action) == "WAIT_RECONCILIATION"

    assert service.execute_calls == 2
    assert action.attempts == 1
    assert action.last_attempt_id == "attempt-unknown"
