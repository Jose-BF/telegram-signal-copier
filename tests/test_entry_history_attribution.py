"""History attribution is exclusive and commits with the recovered outcome."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import sqlite3

import pytest

from execution_intents import IntentStore, ProjectionConflictError, ReconciliationConflictError
from mt5_protocol import BrokerOutcome, BrokerRequest, IntentState
from tests.test_dispatch_preparation_evidence import preparation_for
from tests.test_mt5_trade_client import trade_request


BASE = 1700000000000


def pending(store, *, name="one", account="demo/7", comment="c1_3086_1", tick=BASE):
    template = trade_request(attempt=name, signal_root=f"canal1_{name}")
    request = BrokerRequest.create(
        replace(template.intent_key, account_fingerprint=account),
        {**dict(template.payload), "comment": comment}, request_id=template.request_id,
        attempt_id=name, action_id=template.action_id)
    prep = replace(preparation_for(request), native_request={
        "action": 1, "symbol": "XAUUSD", "magic": 111, "comment": comment,
        "type": 0, "volume": 0.01, "price": 2500.2, "sl": 2499., "tp": 2502.,
    }, evidence={"source_tick": {"time_msc": tick, "bid": 2500., "ask": 2500.2}})
    store.prepare(request)
    store.begin_dispatch(request, worker_session_id="worker-a", worker_pid=123,
                         prepared_payload=prep.native_request, preparation=prep)
    store.record_outcome(request, BrokerOutcome(IntentState.UNKNOWN, error="missing reply"))
    return request


def history(*, account="demo/7", comment="c1_3086_1", order=701, deal=702):
    return {
        "account_fingerprint": account, "date_from_msc": BASE, "date_to_msc": BASE + 3000,
        "deals": [{"ticket": deal, "order": order, "position_id": order, "time_msc": BASE + 2,
                   "symbol": "XAUUSD", "magic": 111, "comment": comment,
                   "type": 0, "entry": 0, "volume": .01, "price": 2500.25}],
        "orders": [{"ticket": order, "position_id": order, "symbol": "XAUUSD", "magic": 111,
                    "comment": comment, "type": 0, "state": 4, "volume_initial": .01,
                    "volume_current": 0., "time_setup_msc": BASE + 1, "time_done_msc": BASE + 2}],
    }


def reconcile(store, request, evidence=None, **kwargs):
    return store.reconcile_entry_history(
        request, history=history() if evidence is None else evidence,
        projection_key=f"signal:{request.intent_key.signal_root}", **kwargs)


def counts(store):
    with sqlite3.connect(store.path) as connection:
        return tuple(connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0] for name in (
            "execution_attempts", "execution_reconciliations", "execution_broker_entry_claims"))


def test_history_confirmation_preserves_attempt_and_does_not_invent_native_reply(tmp_path):
    store = IntentStore(tmp_path / "ledger.db")
    request = pending(store)
    snapshot = store.get_current_dispatch(request.intent_id)
    result = reconcile(store, request)
    assert result.state is IntentState.DONE
    assert result.attempt_id == request.attempt_id
    assert result.outcome["retcode"] is None
    assert result.outcome["order"] == 701
    assert result.outcome["deal"] == 702
    assert result.outcome["filled_volume"] == .01
    assert result.outcome["price"] == 2500.25
    assert result.applied_utc is not None
    assert counts(store) == (1, 1, 3)
    reopened = IntentStore(store.path)
    assert reopened.get_current_dispatch(request.intent_id) == snapshot
    with pytest.raises(ReconciliationConflictError):
        reconcile(reopened, request)
    assert counts(reopened) == (1, 1, 3)
    with sqlite3.connect(store.path) as connection:
        evidence = json.loads(connection.execute(
            "SELECT evidence_json FROM execution_reconciliations").fetchone()[0])
    assert evidence["history"] == history()
    assert evidence["preparation"] == snapshot.preparation.to_dict()


@pytest.mark.parametrize("state", [IntentState.UNKNOWN, IntentState.DISPATCHING])
def test_competing_unresolved_attempt_blocks_automatic_attribution(tmp_path, state):
    store = IntentStore(tmp_path / "ledger.db")
    request = pending(store)
    other = pending(store, name="other")
    if state is IntentState.DISPATCHING:
        with sqlite3.connect(store.path) as connection:
            connection.execute("UPDATE execution_attempts SET state = ? WHERE attempt_id = ?",
                               (state.value, other.attempt_id))
    with pytest.raises(ReconciliationConflictError, match="competing"):
        reconcile(store, request)
    assert counts(store) == (2, 0, 0)
    assert store.get(request.intent_id).state is IntentState.UNKNOWN


def test_competitor_begun_after_fill_is_not_assigned_older_execution(tmp_path):
    store = IntentStore(tmp_path / "ledger.db")
    first = pending(store)
    second = pending(store, name="later", tick=BASE + 1000)
    assert reconcile(store, first).state is IntentState.DONE
    with pytest.raises((ReconciliationConflictError, ValueError)):
        reconcile(store, second)
    assert counts(store) == (2, 1, 3)


def test_existing_confirmed_native_order_of_another_signal_blocks_history(tmp_path):
    store = IntentStore(tmp_path / "ledger.db")
    first = pending(store, name="known", comment="different")
    store.reconcile_outcome(first, BrokerOutcome(IntentState.DONE, order=701, deal=702),
                            evidence={"native": "known"})
    candidate = pending(store)
    with pytest.raises(ReconciliationConflictError, match="attributed"):
        reconcile(store, candidate)
    assert counts(store) == (2, 1, 0)


def test_claims_are_scoped_to_account(tmp_path):
    store = IntentStore(tmp_path / "ledger.db")
    first = pending(store)
    other = pending(store, name="other", account="other/8")
    assert reconcile(store, first).state is IntentState.DONE
    assert reconcile(store, other, history(account="other/8")).state is IntentState.DONE
    assert counts(store) == (2, 2, 6)


def test_transaction_failure_rolls_back_claims_outcome_and_projection(tmp_path):
    store = IntentStore(tmp_path / "ledger.db")
    request = pending(store)
    with sqlite3.connect(store.path) as connection:
        connection.executescript("""
            CREATE TRIGGER fail_projection BEFORE INSERT ON execution_effect_projections
            BEGIN SELECT RAISE(ABORT, 'disk failure'); END;
        """)
    with pytest.raises(ProjectionConflictError):
        reconcile(store, request)
    assert counts(store) == (1, 0, 0)
    assert store.get(request.intent_id).state is IntentState.UNKNOWN
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER fail_projection")
    assert reconcile(store, request).state is IntentState.DONE
    assert counts(store) == (1, 1, 3)


def test_concurrent_recovery_has_one_winner_and_one_projection(tmp_path):
    store = IntentStore(tmp_path / "ledger.db", busy_timeout_ms=2000)
    request = pending(store)

    def attempt(_):
        try:
            return reconcile(store, request).state
        except ReconciliationConflictError:
            return "already reconciled"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, range(2)))
    assert set(outcomes) == {IntentState.DONE, "already reconciled"}
    assert counts(store) == (1, 1, 3)


@pytest.mark.parametrize("field", ["order", "deal", "filled_volume"])
def test_reconciliation_cannot_erase_previously_known_identity_or_volume(tmp_path, field):
    store = IntentStore(tmp_path / "ledger.db")
    request = pending(store)
    previous = BrokerOutcome(IntentState.DONE_PARTIAL, order=701, deal=702, filled_volume=.005)
    store.reconcile_outcome(request, previous, evidence={"native": "partial"})
    outcome = replace(previous, state=IntentState.DONE, filled_volume=.01)
    with pytest.raises(ReconciliationConflictError):
        store.reconcile_outcome(request, replace(outcome, **{field: None}), evidence={"bad": field})


def test_zero_native_ids_do_not_prevent_discovery_of_real_ids(tmp_path):
    store = IntentStore(tmp_path / "ledger.db")
    request = pending(store)
    store.reconcile_outcome(request, BrokerOutcome(IntentState.PLACED, order=701, deal=0,
                                                   filled_volume=0.), evidence={"native": "placed"})
    assert reconcile(store, request).outcome["deal"] == 702
