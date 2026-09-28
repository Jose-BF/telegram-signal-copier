"""Additional offline checks for E3-B recovery and terminal redelivery."""

from dataclasses import replace
from functools import partial

import pytest

from durable_entry_execution import DurableEntryExecutor, EntryDispatchState
from durable_execution import DurableExecutionService, ExecutionDisposition
from execution_intents import IntentStore, ReservationConflictError
from mt5_client import MT5ReadClient
from mt5_protocol import BrokerOutcome, BrokerRequest, IntentKey, IntentState, LookupState
from mt5_worker import WorkerConfig
from tests.mt5_trade_review_fakes import OwnedTradeMT5
from tests.test_e3_b_review_regressions import _open_request, _service


@pytest.mark.parametrize("channel", ["canal1", "canal2"])
def test_recovery_is_current_while_history_stays_intact(tmp_path, channel):
    path = tmp_path / "ledger.sqlite3"
    store = IntentStore(path)
    original = _open_request()
    request = BrokerRequest.create(
        replace(original.intent_key, channel=channel, signal_root=f"{channel}_42"),
        dict(original.payload),
    )
    # Legacy unreserved rows must remain visible in the current snapshot.
    store.prepare(request)
    store.begin_dispatch(request)
    store.record_outcome(
        request, BrokerOutcome(IntentState.UNKNOWN, error="lost"),
        projection_key="signal:42",
    )
    store.reconcile_outcome(
        request,
        BrokerOutcome(IntentState.DONE, retcode=10009, order=77,
                      price=2501.5, filled_volume=0.01),
        evidence={"position_ticket": 77}, projection_key="signal:42",
    )
    history = store.list_projections()
    assert [item.projection["state"] for item in history] == ["UNKNOWN", "DONE"]

    restarted = IntentStore(path)
    rows = DurableEntryExecutor(_service(restarted), symbol="XAUUSD").reconstruct()

    assert len(rows) == 1
    assert (rows[0].channel, rows[0].state, rows[0].ticket, rows[0].fill_price) == (
        channel, EntryDispatchState.CONFIRMED, 77, 2501.5,
    )
    assert rows[0].payload == dict(request.payload)
    assert rows[0].outcome_revision == 2
    assert restarted.list_projections() == history


@pytest.mark.parametrize("terminal", [IntentState.DONE, IntentState.REJECTED])
def test_old_terminal_redelivery_leaves_new_reservation_untouched(tmp_path, terminal):
    path = tmp_path / "ledger.sqlite3"
    store = IntentStore(path)
    key = IntentKey("demo/7", "canal2", "canal2_42", 0,
                    "ticket-55", "MODIFY_SLTP", 0)
    payload = {"symbol": "XAUUSD", "ticket": 55, "expected_magic": 222,
               "new_sl": 2490.0, "new_tp": None}
    old = BrokerRequest.create(key, payload)
    store.prepare_reserved(old, reservation_key="position-55", policy_revision=0)
    store.begin_dispatch(old)
    store.record_outcome(
        old, BrokerOutcome(terminal, retcode=10009 if terminal is IntentState.DONE else 10019),
        projection_key="signal:42", release_reservation_reason="terminal_projected",
    )
    new = BrokerRequest.create(replace(key, revision=1), {**payload, "new_sl": 2495.0})
    store.prepare_reserved(new, reservation_key="position-55", policy_revision=1)
    restarted = IntentStore(path)
    history = restarted.list_projections()
    reservations = restarted.list_reservations()
    replay = BrokerRequest.create(key, payload, action_id=old.action_id)

    result = restarted.prepare_reserved(replay, reservation_key="position-55", policy_revision=0)

    assert result.state is terminal
    assert result.attempt_id == old.attempt_id
    assert restarted.get(new.intent_id).state is IntentState.PREPARED
    assert restarted.list_reservations() == reservations
    assert restarted.list_projections() == history


@pytest.mark.parametrize("mismatch", ["expiry", "policy"])
def test_released_terminal_still_validates_reservation_contract(tmp_path, mismatch):
    store = IntentStore(tmp_path / "ledger.sqlite3")
    request = _open_request()
    store.prepare_reserved(request, reservation_key="entry", policy_revision=0)
    store.begin_dispatch(request)
    store.record_outcome(
        request, BrokerOutcome(IntentState.REJECTED, retcode=10019),
        projection_key="signal:42", release_reservation_reason="rejected",
    )
    before = store.get(request.intent_id)
    kwargs = {"reservation_key": "entry", "policy_revision": 0}
    if mismatch == "expiry":
        kwargs["expires_utc"] = "2099-01-01T00:00:00+00:00"
    else:
        kwargs["policy_revision"] = 1
    with pytest.raises((ValueError, ReservationConflictError)):
        store.prepare_reserved(request, **kwargs)
    assert store.get(request.intent_id) == before
    assert store.get_reservation(request.intent_id).state == "RELEASED"


def test_superseded_rejection_cannot_be_replayed_as_released_terminal(tmp_path):
    store = IntentStore(tmp_path / "ledger.sqlite3")
    old = _open_request()
    store.prepare_reserved(old, reservation_key="entry", policy_revision=0)
    store.begin_dispatch(old)
    store.record_outcome(old, BrokerOutcome(IntentState.REJECTED, retcode=10020))
    new = BrokerRequest.create(replace(old.intent_key, revision=1), dict(old.payload))
    store.prepare_reserved(new, reservation_key="entry", policy_revision=1)

    with pytest.raises(ReservationConflictError, match="SUPERSEDED"):
        store.prepare_reserved(old, reservation_key="entry", policy_revision=0)
    recovered = DurableEntryExecutor(_service(store), symbol="XAUUSD").reconstruct()
    assert [(row.intent_id, row.state) for row in recovered] == [
        (new.intent_id, EntryDispatchState.NOT_SENT),
    ]


@pytest.mark.asyncio
async def test_management_redelivery_after_client_restart_never_sends_again(tmp_path):
    marker = tmp_path / "sends.txt"
    path = tmp_path / "ledger.sqlite3"
    key = IntentKey("demo/7", "canal2", "canal2_42", 0,
                    "ticket-55", "CLOSE_POSITION", 0)
    payload = {"symbol": "XAUUSD", "ticket": 55, "expected_magic": 222, "deviation": 30}
    results = []
    for _ in range(2):
        client = MT5ReadClient(
            WorkerConfig(7, "demo", ("XAUUSD",)), store_path=path,
            backend_factory=partial(OwnedTradeMT5, marker=str(marker)),
        )
        try:
            assert (await client.start()).state is LookupState.FOUND
            service = DurableExecutionService(client)
            results.append(await service.execute(
                BrokerRequest.create(key, payload, action_id="close-55"),
                reservation_key="position-55", projection_key="signal:42",
                release_on_terminal=True,
            ))
        finally:
            await client.close()
    assert all(result.disposition is ExecutionDisposition.APPLIED for result in results)
    assert results[0].record == results[1].record
    assert marker.read_text(encoding="ascii").splitlines() == ["send"]
    assert len(IntentStore(path).list_projections()) == 1
