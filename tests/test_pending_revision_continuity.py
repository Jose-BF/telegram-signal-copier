"""Durable revisions belong to logical actions, not queue occupancy or retries."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import threading
from types import SimpleNamespace

import pytest

from durable_execution import DurableExecutionService
from execution_intents import IntentConflictError, IntentStore
from mt5_protocol import BrokerOutcome, BrokerRequest, IntentKey, IntentState
from pending_actions import PendingAction, PendingQueue
from state import Signal, StateManager


class LedgerClient:
    """Deterministic effects, real reservations/admissions/outcomes/projections."""

    def __init__(self, path, state=IntentState.DONE):
        self.config = SimpleNamespace(expected_server="demo", expected_login=7)
        self.store, self.state = IntentStore(path), state
        self.requests, self.effects = [], []

    async def execute(self, request, **kwargs):
        self.requests.append(request)
        record = self.store.prepare_reserved(request, reservation_key=kwargs["reservation_key"],
            policy_revision=request.intent_key.revision, expires_utc=kwargs.get("expires_utc"))
        if record.state is not IntentState.PREPARED:
            return record
        kwargs["dispatch_guard"](request)
        self.store.admit(request, worker_session_id="test-worker")
        self.store.begin_dispatch(request, worker_session_id="test-worker")
        self.effects.append(request)
        return self.store.record_outcome(request, BrokerOutcome(self.state,
            retcode=10009 if self.state is IntentState.DONE else None))


def queue(tmp_path, monkeypatch, *, state=IntentState.DONE):
    client = LedgerClient(tmp_path / "intents.db", state)
    q = PendingQueue(spool_path=tmp_path / "pending.json", execution_service=DurableExecutionService(client))
    monkeypatch.setattr(q, "_ensure_runner", lambda: None)
    return q, client


def action(sl=90., *, action_id=None, revision=0):
    values = {} if action_id is None else {"action_id": action_id}
    return PendingAction("MODIFY_SLTP", 123, Signal("canal1", 42, "BUY"),
                         new_sl=sl, revision=revision, **values)


async def complete(q, act):
    q.add(act)
    assert await q._try_once(q._actions[0]) == "DONE"
    q._actions.clear()
    q._persist_spool()


@pytest.mark.asyncio
@pytest.mark.parametrize("levels", [(90., 95.), (90., 90.), (90., 95., 90.)])
@pytest.mark.parametrize("restart", [False, True])
async def test_completed_then_new_logical_action_has_new_revision(tmp_path, monkeypatch, levels, restart):
    q, client = queue(tmp_path, monkeypatch)
    sent = []
    for level in levels:
        await complete(q, action(level))
        sent.append(client.effects[-1])
        if restart:
            q, client = queue(tmp_path, monkeypatch)
    assert [r.intent_key.revision for r in sent] == list(range(len(levels)))
    assert len({r.intent_id for r in sent}) == len(levels)
    assert {r.intent_key.leg for r in sent} == {"ticket-123"}


@pytest.mark.asyncio
@pytest.mark.parametrize("restart", [False, True])
async def test_same_action_redelivery_preserves_done_identity(tmp_path, monkeypatch, restart):
    q, client = queue(tmp_path, monkeypatch)
    act = action(action_id="stable")
    await complete(q, act)
    original = client.requests[-1]
    if restart:
        q, client = queue(tmp_path, monkeypatch)
    before = len(client.effects)
    await complete(q, replace(act))
    assert client.requests[-1].intent_id == original.intent_id
    assert len(client.effects) == before


@pytest.mark.asyncio
async def test_restored_unknown_retries_same_identity_and_blocks_successor(tmp_path, monkeypatch):
    q, client = queue(tmp_path, monkeypatch, state=IntentState.UNKNOWN)
    act = action(action_id="unknown")
    q.add(act)
    assert await q._try_once(act) == "WAIT_RECONCILIATION"
    original = client.requests[-1]
    q._persist_spool()
    recovered, next_client = queue(tmp_path, monkeypatch)
    manager = StateManager()
    manager.add(act.signal)
    assert recovered.restore_from_spool(manager) == 1
    current = recovered._actions[0]
    assert await recovered._try_once(current) == "WAIT_RECONCILIATION"
    assert next_client.requests[-1].intent_id == original.intent_id
    assert not next_client.effects
    recovered.add(action(95.))
    assert await recovered._try_once(current) == "WAIT_RECONCILIATION"
    assert await recovered._try_once(current) == "WAIT_RECONCILIATION"
    assert not next_client.effects
    assert next_client.store.get(original.intent_id).state is IntentState.UNKNOWN


def request(*, action_id="a", revision=0, sl=90., **scope):
    key = IntentKey("demo/7", "canal1", "canal1_42", 0, "ticket-123", "MODIFY_SLTP", revision)
    return BrokerRequest.create(replace(key, **scope), {"new_sl": sl}, action_id=action_id)


def test_atomic_binding_survives_restart_before_spool_or_dispatch(tmp_path):
    path = tmp_path / "db"
    store = IntentStore(path)
    first = store.bind_pending_request(request())
    second = IntentStore(path).bind_pending_request(request(action_id="b"))
    replay = IntentStore(path).bind_pending_request(request())
    assert [first.intent_key.revision, second.intent_key.revision] == [0, 1]
    assert replay.intent_id == first.intent_id
    with pytest.raises(IntentConflictError):
        store.bind_pending_request(request(sl=95.))


@pytest.mark.parametrize("field,value", [("account_fingerprint", "demo/8"), ("channel", "canal2"),
    ("signal_root", "canal1_43"), ("generation", 1), ("leg", "ticket-124"), ("operation", "CLOSE_POSITION")])
def test_revision_counters_have_exact_causal_scope(tmp_path, field, value):
    store = IntentStore(tmp_path / "db")
    store.bind_pending_request(request())
    assert store.bind_pending_request(request(action_id="b")).intent_key.revision == 1
    assert store.bind_pending_request(request(**{field: value})).intent_key.revision == 0


def test_two_store_instances_allocate_unique_revisions_atomically(tmp_path):
    stores = [IntentStore(tmp_path / "db", busy_timeout_ms=2000) for _ in range(2)]
    barrier = threading.Barrier(2)
    def bind(index):
        barrier.wait(timeout=5)
        return stores[index].bind_pending_request(request(action_id=str(index)))
    with ThreadPoolExecutor(2) as pool:
        rows = list(pool.map(bind, range(2)))
    assert sorted(r.intent_key.revision for r in rows) == [0, 1]


@pytest.mark.parametrize("state", [IntentState.PREPARED, IntentState.UNKNOWN, IntentState.DONE])
def test_legacy_admitted_action_restores_original_revision(tmp_path, state):
    store = IntentStore(tmp_path / "db")
    old = request(action_id="legacy", revision=7)
    store.prepare(old)
    store.admit(old, worker_session_id="legacy-worker")
    if state is not IntentState.PREPARED:
        store.begin_dispatch(old, worker_session_id="legacy-worker")
        store.record_outcome(old, BrokerOutcome(state))
    restored = IntentStore(store.path).bind_pending_request(request(action_id="legacy", revision=7))
    assert restored.intent_id == old.intent_id
    if state is IntentState.DONE:
        newer = store.bind_pending_request(request(action_id="new"))
        assert newer.intent_key.revision == 8


@pytest.mark.asyncio
async def test_coalescing_while_revision_storage_waits_cannot_dispatch_old_payload(tmp_path, monkeypatch):
    q, client = queue(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    original = client.store.bind_pending_request
    def held(req):
        result = original(req)
        entered.set()
        assert release.wait(5)
        return result
    monkeypatch.setattr(client.store, "bind_pending_request", held)
    first = action(90.)
    q.add(first)
    running = asyncio.create_task(q._try_once(first))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        q.add(action(95.))
        release.set()
        assert await running == "RETRY"
        assert not client.effects
        monkeypatch.setattr(client.store, "bind_pending_request", original)
        assert await q._try_once(first) == "DONE"
        assert len(client.effects) == 1
        assert client.effects[0].payload["new_sl"] == 95.
        assert client.effects[0].intent_key.revision == 1
    finally:
        release.set()
        if not running.done():
            running.cancel()
        await asyncio.gather(running, return_exceptions=True)


@pytest.mark.asyncio
async def test_unknown_tp_prerequisite_blocks_new_main_revision(tmp_path, monkeypatch):
    q, client = queue(tmp_path, monkeypatch, state=IntentState.UNKNOWN)
    act = action(95.)
    act.new_tp = 110.
    q.add(act)
    original_execute = client.execute

    async def deferred_sl(req, **kwargs):
        if req.intent_key.leg == "ticket-123":
            client.requests.append(req)
            record = client.store.prepare_reserved(req, reservation_key=kwargs["reservation_key"],
                                                   policy_revision=req.intent_key.revision)
            client.store.admit(req, worker_session_id="test-worker")
            client.store.record_predispatch_failure(req, "requested_sl_waits_for_market",
                                                    worker_session_id="test-worker")
            return record
        return await original_execute(req, **kwargs)

    monkeypatch.setattr(client, "execute", deferred_sl)
    assert await q._try_once(act) == "WAIT_RECONCILIATION"
    assert len(client.effects) == 1 and "tp-prerequisite" in client.effects[0].intent_key.leg
    q.add(action(96.))
    monkeypatch.setattr(client, "execute", original_execute)
    client.state = IntentState.DONE
    assert await q._try_once(act) == "WAIT_RECONCILIATION"
    assert len(client.effects) == 1


def test_new_request_cannot_dispatch_if_previous_becomes_unknown_after_allocation(tmp_path):
    store = IntentStore(tmp_path / "db")
    first = store.bind_pending_request(request())
    second = store.bind_pending_request(request(action_id="b", sl=95.))
    store.prepare(first)
    store.admit(first, worker_session_id="test-worker")
    store.begin_dispatch(first, worker_session_id="test-worker")
    store.mark_unknown(first, "uncertain")
    store.prepare(second)
    store.admit(second, worker_session_id="test-worker")
    with pytest.raises(Exception, match="prior pending execution"):
        store.begin_dispatch(second, worker_session_id="test-worker")
    assert store.get(second.intent_id).state is IntentState.PREPARED


def test_query_for_lineage_high_water_uses_index(tmp_path):
    from execution_intents import _PENDING_SCOPE_FIELDS, _PENDING_SCOPE_WHERE
    store = IntentStore(tmp_path / "db")
    with store._connect() as connection:
        plan = connection.execute("EXPLAIN QUERY PLAN SELECT json_extract(identity_json, '$.revision') "
            "FROM execution_intents WHERE " + _PENDING_SCOPE_WHERE +
            " ORDER BY json_extract(identity_json, '$.revision') DESC LIMIT 1",
            tuple(getattr(request().intent_key, name) for name in _PENDING_SCOPE_FIELDS)).fetchall()
    assert any("execution_pending_lineage" in row[3] for row in plan)
    assert not any("SCAN execution_intents" in row[3] for row in plan)
