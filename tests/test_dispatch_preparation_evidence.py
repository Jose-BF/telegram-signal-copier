"""Precommit evidence must survive missing replies without another native send."""

from dataclasses import replace
from functools import partial
import json
import os
import sqlite3

import pytest

from execution_intents import IntentConflictError, IntentStore
from mt5_client import MT5ReadClient
from mt5_protocol import BrokerOutcome, IntentState, LookupState
from mt5_trade_protocol import TradePreparation
from mt5_worker import WorkerConfig
from tests.mt5_read_fakes import FakeTradeMT5
from tests.test_mt5_trade_client import trade_request


class EvidenceProbeMT5(FakeTradeMT5):
    """Observe the durable ledger in the child, before its native effect."""

    def __init__(self, *, store_path, probe_path, **kwargs):
        super().__init__(**kwargs)
        self.store_path, self.probe_path = store_path, probe_path

    def symbol_info_tick(self, symbol):
        tick = super().symbol_info_tick(symbol)
        self.source_tick = tick._asdict()
        return tick

    def order_send(self, request):
        with sqlite3.connect(self.store_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM execution_attempts").fetchone()
        with open(self.probe_path, "w", encoding="ascii") as stream:
            json.dump({"attempt": dict(row), "source_tick": self.source_tick,
                       "native_request": request, "pid": os.getpid()}, stream)
        return super().order_send(request)


def probe_client(tmp_path, trade_mode="done"):
    return MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",)),
        backend_factory=partial(
            EvidenceProbeMT5, store_path=str(tmp_path / "intents.sqlite3"),
            probe_path=str(tmp_path / "probe.json"), marker=str(tmp_path / "sends.txt"),
            trade_mode=trade_mode),
        store_path=tmp_path / "intents.sqlite3")


@pytest.mark.asyncio
@pytest.mark.parametrize("trade_mode,state", [
    ("done", IntentState.DONE), ("none", IntentState.UNKNOWN), ("die", IntentState.UNKNOWN)])
async def test_preparation_is_durable_before_native_send_and_survives_worker_restart(
    tmp_path, trade_mode, state,
):
    request = trade_request()
    c = probe_client(tmp_path, trade_mode)
    assert (await c.start()).state is LookupState.FOUND
    try:
        record = await c.execute(request, reservation_key="entry", policy_revision=0)
    finally:
        await c.close()
    assert record.state is state
    probe = json.loads((tmp_path / "probe.json").read_text(encoding="ascii"))
    attempt = probe["attempt"]
    assert attempt.get("preparation_json") is not None, "precommit evidence was discarded"
    preparation = TradePreparation.from_dict(json.loads(attempt["preparation_json"]))
    assert attempt["state"] == "DISPATCHING"
    assert preparation.evidence["source_tick"] == probe["source_tick"]
    assert preparation.evidence["symbol_contract"]["name"] == "XAUUSD"
    assert preparation.evidence["requested_sl"] == 2499.0
    assert preparation.evidence["effective_sl"] == 2499.0
    assert dict(preparation.native_request) == probe["native_request"]
    assert preparation.worker_pid == probe["pid"]

    restarted = probe_client(tmp_path)
    assert (await restarted.start()).state is LookupState.FOUND
    try:
        snapshot = restarted.store.get_current_dispatch(request.intent_id)
        assert snapshot.request == request
        assert snapshot.preparation == preparation
        assert snapshot.worker_pid == probe["pid"]
        assert snapshot.worker_session_id == preparation.worker_session_id
        assert snapshot.prepared_payload == preparation.native_request
        assert snapshot.dispatched_monotonic_ns > 0
        assert snapshot.dispatched_utc
        redelivery = await restarted.execute(
            trade_request(attempt="redelivery"), reservation_key="entry", policy_revision=0)
        assert redelivery.state is state
        assert restarted.store.get_current_dispatch(request.intent_id) == snapshot
    finally:
        await restarted.close()
    assert (tmp_path / "sends.txt").read_text(encoding="ascii").splitlines() == ["send"]


def preparation_for(request):
    return TradePreparation(
        request_id=request.request_id, intent_id=request.intent_id,
        attempt_id=request.attempt_id, action_id=request.action_id,
        worker_session_id="worker-a", worker_pid=123, prepared_monotonic=12.5,
        native_request={"symbol": "XAUUSD", "type": 0, "volume": 0.01, "price": 2500.2},
        evidence={"source_tick": {"time_msc": 1700000000123, "bid": 2500, "ask": 2500.2},
                  "symbol_contract": {"point": 0.01}})


def dispatch(store, request, preparation):
    return store.begin_dispatch(
        request, worker_session_id="worker-a", worker_pid=123,
        prepared_payload=preparation.native_request, preparation=preparation)


@pytest.mark.parametrize("field,value", [
    ("request_id", "other-request"), ("intent_id", "other-intent"),
    ("attempt_id", "other-attempt"), ("action_id", "other-action"),
    ("worker_session_id", "other-worker"), ("worker_pid", 456),
])
def test_dispatch_rejects_crossed_preparation_before_changing_ledger(tmp_path, field, value):
    store = IntentStore(tmp_path / "intents.sqlite3")
    request = trade_request()
    store.prepare(request)
    preparation = replace(preparation_for(request), **{field: value})
    with pytest.raises(IntentConflictError, match="preparation"):
        dispatch(store, request, preparation)
    assert store.get(request.intent_id).state is IntentState.PREPARED
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT count(*) FROM execution_attempts").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM execution_admissions").fetchone()[0] == 0


@pytest.mark.parametrize("failed", [False, True])
def test_invalid_preparation_never_becomes_dispatchable(tmp_path, failed):
    store = IntentStore(tmp_path / "intents.sqlite3")
    request = trade_request()
    store.prepare(request)
    preparation = preparation_for(request)
    if failed:
        preparation = replace(preparation, error="no quote", native_request=None)
    with pytest.raises(IntentConflictError, match="preparation"):
        store.begin_dispatch(
            request, worker_session_id="worker-a", worker_pid=123,
            prepared_payload={"symbol": "other"}, preparation=preparation)
    assert store.get(request.intent_id).state is IntentState.PREPARED


@pytest.mark.parametrize("missing_native_payload", [False, True])
def test_legacy_dispatch_is_explicitly_missing_preparation_after_migration(
    tmp_path, missing_native_payload,
):
    path = tmp_path / "legacy.sqlite3"
    store = IntentStore(path)
    request = trade_request()
    store.prepare(request)
    store.begin_dispatch(request)
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(execution_attempts)")}
        if "preparation_json" in columns:
            connection.execute("ALTER TABLE execution_attempts DROP COLUMN preparation_json")
        if missing_native_payload:
            connection.execute("ALTER TABLE execution_attempts DROP COLUMN prepared_payload_json")
    migrated = IntentStore(path)
    snapshot = migrated.get_current_dispatch(request.intent_id)
    assert snapshot.request == request
    assert snapshot.preparation is None
    assert snapshot.prepared_payload == (None if missing_native_payload else request.payload)


def test_snapshot_is_immutable_and_requires_an_actual_dispatch(tmp_path):
    store = IntentStore(tmp_path / "intents.sqlite3")
    request = trade_request()
    with pytest.raises(KeyError):
        store.get_current_dispatch(request.intent_id)
    store.prepare(request)
    with pytest.raises(KeyError):
        store.get_current_dispatch(request.intent_id)
    preparation = preparation_for(request)
    dispatch(store, request, preparation)
    snapshot = store.get_current_dispatch(request.intent_id)
    snapshot.preparation.evidence["source_tick"]["time_msc"] = 99
    with pytest.raises(TypeError):
        snapshot.prepared_payload["price"] = 1
    assert store.get_current_dispatch(request.intent_id) == snapshot
    assert snapshot.preparation.evidence["source_tick"]["time_msc"] == 1700000000123


@pytest.mark.parametrize("column,value", [
    ("worker_pid", 456), ("worker_session_id", "worker-b"),
    ("prepared_payload_json", '{"symbol":"other"}'),
    ("request_id", "different-request"),
])
def test_snapshot_rejects_mismatched_durable_identity(tmp_path, column, value):
    store = IntentStore(tmp_path / "intents.sqlite3")
    request = trade_request()
    store.prepare(request)
    dispatch(store, request, preparation_for(request))
    with sqlite3.connect(store.path) as connection:
        connection.execute(f"UPDATE execution_attempts SET {column} = ?", (value,))
    with pytest.raises(IntentConflictError):
        store.get_current_dispatch(request.intent_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ["before_insert", "after_insert"])
async def test_evidence_write_failure_prevents_commit_and_native_effect(tmp_path, failure_point):
    c = probe_client(tmp_path)
    assert (await c.start()).state is LookupState.FOUND
    with sqlite3.connect(c.store.path) as connection:
        timing = ("BEFORE INSERT ON execution_attempts" if failure_point == "before_insert"
                  else "BEFORE UPDATE ON execution_intents WHEN NEW.state = 'DISPATCHING'")
        connection.executescript(f"""
            CREATE TRIGGER fail_dispatch {timing}
            BEGIN SELECT RAISE(ABORT, 'simulated full disk'); END;
        """)
    request = trade_request()
    try:
        record = await c.execute(request, reservation_key="entry", policy_revision=0)
    finally:
        await c.close()
    assert record.state is IntentState.PREPARED
    assert not (tmp_path / "sends.txt").exists()
    assert not (tmp_path / "probe.json").exists()
    with sqlite3.connect(c.store.path) as connection:
        assert connection.execute("SELECT count(*) FROM execution_attempts").fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM execution_admissions WHERE state = 'DISPATCHING'"
        ).fetchone()[0] == 0


def test_unknown_outcome_does_not_replace_dispatch_evidence(tmp_path):
    store = IntentStore(tmp_path / "intents.sqlite3")
    request = trade_request()
    store.prepare(request)
    dispatch(store, request, preparation_for(request))
    snapshot = store.get_current_dispatch(request.intent_id)
    store.record_outcome(request, BrokerOutcome(IntentState.UNKNOWN, error="response lost"))
    assert IntentStore(store.path).get_current_dispatch(request.intent_id) == snapshot


def test_policy_retry_selects_only_new_current_dispatch_and_preserves_old_evidence(tmp_path):
    store = IntentStore(tmp_path / "intents.sqlite3")
    first, retry = trade_request(), trade_request(attempt="retry")
    store.prepare(first)
    dispatch(store, first, preparation_for(first))
    first_snapshot = store.get_current_dispatch(first.intent_id)
    store.record_outcome(first, BrokerOutcome(IntentState.REJECTED, retcode=10020))
    store.prepare_retry(retry, previous_attempt_id=first.attempt_id,
                        reason="price_changed", retryable_retcodes={10020})
    with pytest.raises(KeyError):
        store.get_current_dispatch(retry.intent_id)
    preparation = replace(preparation_for(retry), prepared_monotonic=13.5,
                          evidence={"source_tick": {"time_msc": 1700000001123}})
    dispatch(store, retry, preparation)
    snapshot = store.get_current_dispatch(retry.intent_id)
    assert snapshot.request == retry
    assert snapshot.preparation == preparation
    with sqlite3.connect(store.path) as connection:
        old = connection.execute(
            "SELECT preparation_json FROM execution_attempts WHERE attempt_id = ?",
            (first.attempt_id,),
        ).fetchone()[0]
    assert TradePreparation.from_dict(json.loads(old)) == first_snapshot.preparation


@pytest.mark.parametrize("raw", ['{"missing":"fields"}', 'not json', 'null'])
def test_corrupt_evidence_is_not_mistaken_for_missing_legacy_evidence(tmp_path, raw):
    store = IntentStore(tmp_path / "intents.sqlite3")
    request = trade_request()
    store.prepare(request)
    dispatch(store, request, preparation_for(request))
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE execution_attempts SET preparation_json = ?", (raw,))
    with pytest.raises(ValueError):
        store.get_current_dispatch(request.intent_id)
