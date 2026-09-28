from types import SimpleNamespace

import pytest

from durable_entry_execution import (
    DurableEntryExecutor,
    EntryDispatchState,
)
from durable_execution import DurableExecutionResult, ExecutionDisposition
from execution_intents import CurrentIntentSnapshot, IntentRecord
from mt5_protocol import BrokerRequest, IntentKey, IntentState


class _Service:
    def __init__(self, result):
        self.result = result
        self.config = SimpleNamespace(expected_server="demo", expected_login=7)
        self.client = SimpleNamespace(config=self.config)
        self.requests = []
        self.current_records = []
        self.intent_keys = {}
        self.releases = []
        self.store = SimpleNamespace(
            get_intent_key=self.intent_keys.__getitem__,
            release_reservation=lambda *args, **kwargs: self.releases.append(
                (args, kwargs)
            ),
        )

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
        request = BrokerRequest.create(
            key,
            values["payload"],
            request_id="request-entry",
            attempt_id="attempt-entry",
            action_id=values["action_id"],
        )
        self.requests.append(("request", request, values))
        return request

    async def execute(self, request, **values):
        self.requests.append(("execute", request, values))
        return self.result

    def current_intents(self):
        return self.current_records


def _result(state, disposition, *, order=701, price=2500.25):
    outcome = None
    revision = 0
    if state is not IntentState.PREPARED:
        outcome = {
            "state": state.value,
            "retcode": 10009 if state is IntentState.DONE else 10008,
            "order": order,
            "deal": 702 if state is IntentState.DONE else None,
            "filled_volume": 0.01 if state is IntentState.DONE else None,
            "price": price,
            "error": None,
            "raw_result": None,
        }
        revision = 1
    record = IntentRecord(
        "intent-entry",
        state,
        "attempt-entry" if revision else None,
        {},
        outcome,
        revision,
        "2026-09-20T00:00:00+00:00" if revision else None,
    )
    return DurableExecutionResult(record, disposition, None)


async def _open(adapter):
    return await adapter.open_market(
        channel="canal1",
        signal_root="canal1_3086",
        generation=2,
        leg="entry-1",
        revision=3,
        direction="BUY",
        volume=0.01,
        sl=None,
        tp=None,
        loss_budget=8.0,
        protection_policy="required",
        magic=111,
        comment="c1_3086",
        action_id="action-entry",
    )


@pytest.mark.asyncio
async def test_confirmed_done_exposes_ticket_and_fill():
    service = _Service(_result(IntentState.DONE, ExecutionDisposition.APPLIED))
    adapter = DurableEntryExecutor(service, symbol="XAUUSD")

    result = await _open(adapter)

    assert result.state is EntryDispatchState.CONFIRMED
    assert (result.ticket, result.fill_price) == (701, 2500.25)
    _, request, _ = service.requests[0]
    assert dict(request.payload) == {
        "symbol": "XAUUSD",
        "direction": "BUY",
        "volume": 0.01,
        "sl": None,
        "tp": None,
        "loss_budget": 8.0,
        "protection_policy": "required",
        "magic": 111,
        "comment": "c1_3086",
        "deviation": 30,
    }
    _, _, execute_values = service.requests[1]
    assert execute_values["reservation_key"] == (
        "demo/7/canal1/canal1_3086/g2/entry-1"
    )


@pytest.mark.asyncio
async def test_redelivery_exposes_original_requested_stop_not_retry_stop():
    service = _Service(_result(IntentState.DONE, ExecutionDisposition.APPLIED))
    original_payload = {
        "symbol": "XAUUSD", "direction": "BUY", "volume": 0.01,
        "sl": 2470.0, "tp": None, "loss_budget": 8.0,
        "protection_policy": "required", "magic": 111,
        "comment": "c1_3086", "deviation": 30,
    }
    service.store.get = lambda intent_id: SimpleNamespace(payload=original_payload)
    result = await _open(DurableEntryExecutor(service, symbol="XAUUSD"))
    assert result.requested_sl == 2470.0
    assert service.requests[-1][1].payload["sl"] == 2470.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "disposition", "expected"),
    [
        (IntentState.PREPARED, ExecutionDisposition.NOT_SENT, EntryDispatchState.NOT_SENT),
        (IntentState.REJECTED, ExecutionDisposition.REJECTED, EntryDispatchState.REJECTED),
        (IntentState.PLACED, ExecutionDisposition.RECONCILE, EntryDispatchState.RECONCILE),
        (IntentState.DONE_PARTIAL, ExecutionDisposition.RECONCILE, EntryDispatchState.RECONCILE),
        (IntentState.UNKNOWN, ExecutionDisposition.RECONCILE, EntryDispatchState.RECONCILE),
    ],
)
async def test_nonconfirmed_states_never_expose_a_fill(state, disposition, expected):
    service = _Service(_result(state, disposition))

    result = await _open(DurableEntryExecutor(service, symbol="XAUUSD"))

    assert result.state is expected
    assert result.ticket is None
    assert result.fill_price is None
    assert bool(service.releases) is (state is IntentState.REJECTED)


@pytest.mark.asyncio
async def test_done_without_reconstructible_ticket_is_held_for_reconciliation():
    service = _Service(
        _result(IntentState.DONE, ExecutionDisposition.APPLIED, order=None)
    )

    result = await _open(DurableEntryExecutor(service, symbol="XAUUSD"))

    assert result.state is EntryDispatchState.RECONCILE
    assert result.reason == "confirmed_open_missing_ticket_or_price"


def test_reconstruct_exposes_confirmed_and_ambiguous_open_entries():
    service = _Service(_result(IntentState.DONE, ExecutionDisposition.APPLIED))
    done_key = IntentKey(
        "demo/7", "canal1", "canal1_3086", 2, "entry-0", "OPEN_MARKET", 3
    )
    unknown_key = IntentKey(
        "demo/7", "canal2", "canal2_99", 0, "entry-0", "OPEN_MARKET", 0
    )
    dispatching_key = IntentKey(
        "demo/7",
        "canal1",
        "canal1_4000",
        1,
        "entry-0",
        "OPEN_MARKET",
        0,
    )
    service.intent_keys.update({
        done_key.intent_id: done_key,
        unknown_key.intent_id: unknown_key,
        dispatching_key.intent_id: dispatching_key,
    })
    service.current_records = [
        CurrentIntentSnapshot(done_key, IntentRecord(
            done_key.intent_id, IntentState.DONE, "attempt-done",
            {"direction": "BUY", "volume": 0.01, "sl": 2490.0,
             "tp": None, "loss_budget": None, "magic": 111,
             "comment": "c1_3086"},
            {"order": 701, "price": 2500.25, "retcode": 10009},
            1, "2026-09-20T00:00:00+00:00",
        )),
        CurrentIntentSnapshot(unknown_key, IntentRecord(
            unknown_key.intent_id, IntentState.UNKNOWN, "attempt-unknown",
            {"direction": "SELL", "volume": 0.04, "sl": 2530.0,
             "tp": None, "loss_budget": None, "magic": 222,
             "comment": "c2_99"},
            {"order": None, "price": None, "retcode": None},
            1, "2026-09-20T00:00:00+00:00",
        )),
        CurrentIntentSnapshot(dispatching_key, IntentRecord(
            dispatching_key.intent_id,
            IntentState.DISPATCHING,
            "attempt-dispatching",
            {
                "direction": "BUY",
                "volume": 0.02,
                "sl": 2490.0,
                "tp": None,
                "loss_budget": None,
                "magic": 111,
                "comment": "c1_4000",
            },
            None,
            0,
            None,
        )),
    ]

    recovered = DurableEntryExecutor(service, symbol="XAUUSD").reconstruct()

    assert [row.state for row in recovered] == [
        EntryDispatchState.CONFIRMED,
        EntryDispatchState.RECONCILE,
        EntryDispatchState.RECONCILE,
    ]
    assert recovered[0].ticket == 701
    assert recovered[0].signal_root == "canal1_3086"
    assert recovered[0].payload["volume"] == 0.01
    assert recovered[1].reason == "durable_unknown"
    assert recovered[2].signal_root == "canal1_4000"
    assert recovered[2].reason == "durable_dispatching"
