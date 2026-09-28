import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from execution_intents import (
    IntentConflictError,
    IntentStore,
    OutcomePersistenceError,
    ProjectionConflictError,
    ReconciliationConflictError,
    ReservationConflictError,
    RetryNotAllowedError,
    execute_once,
)
from mt5_protocol import BrokerOutcome, BrokerRequest, IntentKey, IntentState


def _request(payload=None, *, attempt_id="attempt-1"):
    key = IntentKey(
        account_fingerprint="server/account",
        channel="canal1",
        signal_root="canal1_3086",
        generation=1,
        leg="entry-1",
        operation="OPEN_MARKET",
        revision=0,
    )
    return BrokerRequest.create(
        key,
        payload or {"symbol": "XAUUSD", "volume": 0.01},
        request_id=f"request-{attempt_id}",
        attempt_id=attempt_id,
        action_id="action-1",
    )


def _revision_request(revision, *, attempt_id, volume=0.01):
    base = _request()
    key = replace(base.intent_key, revision=revision)
    return BrokerRequest.create(
        key,
        {"symbol": "XAUUSD", "volume": volume},
        request_id=f"request-{attempt_id}",
        attempt_id=attempt_id,
        action_id="action-1",
    )


def test_prepare_is_idempotent_but_rejects_payload_drift(tmp_path):
    store = IntentStore(tmp_path / "intents.sqlite3")
    request = _request()

    first = store.prepare(request)
    second = store.prepare(_request(attempt_id="attempt-redelivery"))

    assert first.intent_id == second.intent_id
    assert second.state is IntentState.PREPARED
    with pytest.raises(IntentConflictError):
        store.prepare(_request({"symbol": "XAUUSD", "volume": 0.02}, attempt_id="attempt-conflict"))


def test_predispatch_failure_reason_is_queryable_by_request(tmp_path):
    store = IntentStore(tmp_path / "intents.sqlite3")
    request = _request()
    store.prepare(request)
    store.admit(request, worker_session_id="worker-1")

    store.record_predispatch_failure(
        request,
        "requested_sl_waits_for_market",
        worker_session_id="worker-1",
    )

    assert store.get_predispatch_failure(request.request_id) == (
        "requested_sl_waits_for_market"
    )
    assert store.get_predispatch_failure("missing-request") is None
    assert store.get_intent_key(request.intent_id) == request.intent_key

    with pytest.raises(KeyError):
        store.get_intent_key("missing-intent")


def test_predispatch_no_effect_can_be_resolved_and_released_without_attempt(tmp_path):
    store = IntentStore(tmp_path / "intents.sqlite3")
    request = _request()
    reservation_key = "account/canal1/canal1_3086/g1/entry-1"
    store.prepare_reserved(
        request,
        reservation_key=reservation_key,
        policy_revision=0,
    )
    store.admit(request, worker_session_id="worker-1")
    store.record_predispatch_failure(
        request,
        "ticket_not_found",
        worker_session_id="worker-1",
    )

    resolved = store.resolve_predispatch_outcome(
        request,
        BrokerOutcome(IntentState.DONE, retcode=10036, error="ticket_not_found"),
    )
    projected = store.apply_projection(
        request.intent_id,
        outcome_revision=resolved.outcome_revision,
        projection_key="signal:canal1_3086",
        projection={"state": "DONE", "reason": "ticket_not_found"},
        event_id=f"{request.intent_id}:1",
        release_reservation_reason="done_effect_projected",
    )

    assert projected is True
    assert resolved.state is IntentState.DONE
    assert resolved.attempt_id is None
    assert store.get_reservation(request.intent_id).state == "RELEASED"
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM execution_attempts"
        ).fetchone()[0] == 0


def test_store_uses_wal_and_full_synchronous_durability(tmp_path):
    path = tmp_path / "intents.sqlite3"
    IntentStore(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2


def test_done_result_is_durable_before_redelivery_and_is_never_resent(tmp_path):
    path = tmp_path / "intents.sqlite3"
    calls = []

    def broker(payload):
        calls.append(payload)
        return {"retcode": 10009, "order": 41, "deal": 42, "price": 3600.5}

    first = execute_once(IntentStore(path), _request(), broker)
    redelivered = execute_once(IntentStore(path), _request(attempt_id="attempt-2"), broker)

    assert first.state is IntentState.DONE
    assert redelivered.state is IntentState.DONE
    assert redelivered.outcome["deal"] == 42
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("broker_result", "expected"),
    [
        (None, IntentState.UNKNOWN),
        ({"retcode": 10011}, IntentState.UNKNOWN),
        ({"retcode": 10008, "order": 88}, IntentState.PLACED),
        ({"retcode": 10010, "deal": 89, "volume": 0.005}, IntentState.DONE_PARTIAL),
    ],
)
def test_ambiguous_or_incomplete_results_are_not_resent(tmp_path, broker_result, expected):
    path = tmp_path / "intents.sqlite3"
    calls = 0

    def broker(_payload):
        nonlocal calls
        calls += 1
        return broker_result

    first = execute_once(IntentStore(path), _request(), broker)
    second = execute_once(IntentStore(path), _request(attempt_id="attempt-2"), broker)

    assert first.state is expected
    assert second.state is expected
    assert calls == 1


def test_worker_death_after_dispatch_is_recovered_as_unknown_without_resend(tmp_path):
    path = tmp_path / "intents.sqlite3"
    request = _request()
    store = IntentStore(path)
    store.prepare(request)
    store.begin_dispatch(request)

    recovered = IntentStore(path)
    assert recovered.recover_dispatching("worker_replaced") == 1
    calls = 0

    def broker(_payload):
        nonlocal calls
        calls += 1
        return {"retcode": 10009}

    record = execute_once(recovered, _request(attempt_id="attempt-2"), broker)
    assert record.state is IntentState.UNKNOWN
    assert calls == 0


def test_restart_recovery_does_not_change_another_accounts_dispatch(tmp_path):
    store = IntentStore(tmp_path / "intents.sqlite3")
    ours = _request()
    foreign = BrokerRequest.create(
        replace(ours.intent_key, account_fingerprint="other/account"),
        dict(ours.payload), request_id="foreign-request", attempt_id="foreign-attempt",
    )
    for request in (ours, foreign):
        store.prepare(request)
        store.begin_dispatch(request)
    assert store.recover_dispatching("worker_exited_before_restart", account_fingerprint="server/account") == 1
    assert store.get(ours.intent_id).state is IntentState.UNKNOWN
    assert store.get(foreign.intent_id).state is IntentState.DISPATCHING


def test_preflight_failure_leaves_intent_prepared_and_unsent(tmp_path):
    store = IntentStore(tmp_path / "intents.sqlite3")
    calls = 0

    def unavailable():
        raise RuntimeError("worker unavailable before dispatch")

    def broker(_payload):
        nonlocal calls
        calls += 1
        return {"retcode": 10009}

    record = execute_once(store, _request(), broker, preflight=unavailable)
    assert record.state is IntentState.PREPARED
    assert calls == 0

    record = execute_once(store, _request(attempt_id="attempt-2"), broker)
    assert record.state is IntentState.DONE
    assert calls == 1


def test_result_application_is_idempotent(tmp_path):
    store = IntentStore(tmp_path / "intents.sqlite3")
    record = execute_once(store, _request(), lambda _payload: {"retcode": 10009})

    assert store.mark_applied(
        record.intent_id,
        outcome_revision=record.outcome_revision,
    ) is True
    assert store.mark_applied(
        record.intent_id,
        outcome_revision=record.outcome_revision,
    ) is False
    assert store.get(record.intent_id).applied_utc is not None


def test_concurrent_application_ack_accepts_one_consumer(tmp_path):
    store = IntentStore(tmp_path / "intents.sqlite3", busy_timeout_ms=2_000)
    record = execute_once(store, _request(), lambda _payload: {"retcode": 10009})

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda _index: store.mark_applied(
                record.intent_id,
                outcome_revision=record.outcome_revision,
            ),
            range(8),
        ))

    assert results.count(True) == 1
    assert results.count(False) == 7


def test_concurrent_redelivery_has_exactly_one_broker_dispatch(tmp_path):
    store = IntentStore(tmp_path / "intents.sqlite3", busy_timeout_ms=2_000)
    calls = 0

    def broker(_payload):
        nonlocal calls
        calls += 1
        return {"retcode": 10009}

    requests = [_request(attempt_id=f"attempt-{index}") for index in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        records = list(pool.map(lambda request: execute_once(store, request, broker), requests))

    assert calls == 1
    assert {record.state for record in records} <= {IntentState.DISPATCHING, IntentState.DONE}
    assert store.get(requests[0].intent_id).state is IntentState.DONE


def test_persistence_error_retains_confirmed_broker_outcome_for_redelivery(tmp_path, monkeypatch):
    store = IntentStore(tmp_path / "intents.sqlite3")
    request = _request()

    def fail(_request, _outcome):
        raise sqlite3.OperationalError("disk unavailable")

    monkeypatch.setattr(store, "record_outcome", fail)
    with pytest.raises(OutcomePersistenceError) as captured:
        execute_once(store, request, lambda _payload: {"retcode": 10009, "deal": 501})

    assert captured.value.outcome.state is IntentState.DONE
    assert captured.value.outcome.deal == 501
    assert store.get(request.intent_id).state is IntentState.DISPATCHING


def test_attempt_persists_request_action_worker_and_clock_identity(tmp_path):
    path = tmp_path / "intents.sqlite3"
    request = _request()
    store = IntentStore(path)
    store.prepare(request)
    store.begin_dispatch(
        request,
        worker_session_id="worker-session-a",
        worker_pid=321,
        monotonic_ns=123456,
    )

    with sqlite3.connect(path) as connection:
        row = connection.execute(
            """
            SELECT request_id, action_id, worker_session_id, worker_pid,
                   dispatched_monotonic_ns, request_json
              FROM execution_attempts
            """
        ).fetchone()
        schema_version = connection.execute(
            "SELECT value FROM execution_intent_meta WHERE key = 'schema_version'"
        ).fetchone()[0]

    assert row[:5] == ("request-attempt-1", "action-1", "worker-session-a", 321, 123456)
    assert json.loads(row[5]) == request.to_dict()
    assert schema_version == "5"


def test_validate_dispatched_request_checks_full_request_and_worker_session(tmp_path):
    store = IntentStore(tmp_path / "intents.sqlite3")
    request = _request()
    store.prepare(request)
    store.begin_dispatch(request, worker_session_id="worker-a", worker_pid=321)

    store.validate_dispatched_request(request, worker_session_id="worker-a")
    with pytest.raises(IntentConflictError):
        store.validate_dispatched_request(
            replace(request, action_id="other-action"),
            worker_session_id="worker-a",
        )
    with pytest.raises(IntentConflictError):
        store.validate_dispatched_request(request, worker_session_id="worker-b")


def test_admission_rejects_request_id_reuse_across_intents(tmp_path):
    store = IntentStore(tmp_path / "intents.sqlite3")
    first = _request()
    second = BrokerRequest.create(
        replace(first.intent_key, signal_root="canal1_other"),
        dict(first.payload),
        request_id=first.request_id,
        attempt_id="attempt-other",
        action_id="action-other",
    )
    store.prepare(first)
    store.begin_dispatch(first, worker_session_id="worker-a")
    store.prepare(second)

    with pytest.raises(IntentConflictError):
        store.begin_dispatch(second, worker_session_id="worker-a")


def test_recovery_unknown_can_be_resolved_by_same_workers_confirmed_outcome(tmp_path):
    store = IntentStore(tmp_path / "intents.sqlite3")
    request = _request()
    store.prepare(request)
    store.begin_dispatch(request, worker_session_id="worker-a")
    store.recover_dispatching("worker_died_with_unresolved_request")

    record = store.resolve_recovered_outcome(
        request,
        BrokerOutcome(IntentState.DONE, retcode=10009, deal=772),
        worker_session_id="worker-a",
    )

    assert record.state is IntentState.DONE
    assert record.outcome["deal"] == 772


def test_resolved_recovery_outcome_requires_a_new_application_ack(tmp_path):
    store = IntentStore(tmp_path / "intents.sqlite3")
    request = _request()
    store.prepare(request)
    store.begin_dispatch(request, worker_session_id="worker-a")
    store.recover_dispatching("worker_died_with_unresolved_request")
    recovered = store.get(request.intent_id)
    assert store.mark_applied(
        request.intent_id,
        outcome_revision=recovered.outcome_revision,
    ) is True

    record = store.resolve_recovered_outcome(
        request,
        BrokerOutcome(IntentState.DONE, retcode=10009, deal=773),
        worker_session_id="worker-a",
    )

    assert record.state is IntentState.DONE
    assert record.applied_utc is None
    assert record.outcome_revision > recovered.outcome_revision
    assert store.mark_applied(
        request.intent_id,
        outcome_revision=record.outcome_revision,
    ) is True
    assert store.mark_applied(
        request.intent_id,
        outcome_revision=record.outcome_revision,
    ) is False


def test_stale_recovery_ack_cannot_consume_a_later_confirmed_outcome(tmp_path):
    store = IntentStore(tmp_path / "intents.sqlite3")
    request = _request()
    store.prepare(request)
    store.begin_dispatch(request, worker_session_id="worker-a")
    store.recover_dispatching("worker_died_with_unresolved_request")
    recovered = store.get(request.intent_id)

    confirmed = store.resolve_recovered_outcome(
        request,
        BrokerOutcome(IntentState.DONE, retcode=10009, deal=774),
        worker_session_id="worker-a",
    )

    assert confirmed.outcome_revision > recovered.outcome_revision
    assert store.mark_applied(
        request.intent_id,
        outcome_revision=recovered.outcome_revision,
    ) is False
    assert store.get(request.intent_id).applied_utc is None
    assert store.mark_applied(
        request.intent_id,
        outcome_revision=confirmed.outcome_revision,
    ) is True


def test_schema_two_attempts_are_backfilled_into_durable_admissions(tmp_path):
    path = tmp_path / "intents.sqlite3"
    request = _request()
    store = IntentStore(path)
    store.prepare(request)
    store.begin_dispatch(request, worker_session_id="worker-a", worker_pid=321)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE execution_admissions")
        connection.execute(
            "UPDATE execution_intent_meta SET value = '2' WHERE key = 'schema_version'"
        )

    migrated = IntentStore(path)
    migrated.validate_dispatched_request(request, worker_session_id="worker-a")
    with sqlite3.connect(path) as connection:
        admission = connection.execute(
            """
            SELECT request_id, attempt_id, intent_id, action_id, state,
                   worker_session_id
              FROM execution_admissions
            """
        ).fetchone()
        version = connection.execute(
            "SELECT value FROM execution_intent_meta WHERE key = 'schema_version'"
        ).fetchone()[0]

    assert admission == (
        request.request_id,
        request.attempt_id,
        request.intent_id,
        request.action_id,
        IntentState.DISPATCHING.value,
        "worker-a",
    )
    assert version == "5"


def test_schema_three_terminal_outcome_gains_revision_without_losing_ack(tmp_path):
    path = tmp_path / "intents.sqlite3"
    store = IntentStore(path)
    request = _request()
    record = execute_once(store, request, lambda _payload: {"retcode": 10009, "deal": 775})
    assert store.mark_applied(
        record.intent_id,
        outcome_revision=record.outcome_revision,
    ) is True
    applied_utc = store.get(record.intent_id).applied_utc

    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE execution_intents DROP COLUMN outcome_revision")
        connection.execute(
            "UPDATE execution_intent_meta SET value = '3' WHERE key = 'schema_version'"
        )

    migrated = IntentStore(path)
    current = migrated.get(request.intent_id)
    with sqlite3.connect(path) as connection:
        version = connection.execute(
            "SELECT value FROM execution_intent_meta WHERE key = 'schema_version'"
        ).fetchone()[0]

    assert version == "5"
    assert current.state is IntentState.DONE
    assert current.outcome["deal"] == 775
    assert current.outcome_revision == 1
    assert current.applied_utc == applied_utc
    assert migrated.mark_applied(
        current.intent_id,
        outcome_revision=current.outcome_revision,
    ) is False


def test_result_projection_and_application_ack_are_one_transaction(tmp_path):
    store = IntentStore(tmp_path / "intents.sqlite3")
    record = execute_once(
        store,
        _request(),
        lambda _payload: {"retcode": 10009, "deal": 901, "volume": 0.01},
    )

    assert store.apply_projection(
        record.intent_id,
        outcome_revision=record.outcome_revision,
        projection_key="signal:canal1_3086",
        projection={"ticket": 901, "status": "OPEN", "volume": 0.01},
        event_id="effect-901",
    ) is True
    assert store.apply_projection(
        record.intent_id,
        outcome_revision=record.outcome_revision,
        projection_key="signal:canal1_3086",
        projection={"ticket": 901, "status": "OPEN", "volume": 0.01},
        event_id="effect-901",
    ) is False

    current = store.get(record.intent_id)
    assert current.applied_utc is not None
    assert [effect.projection for effect in store.list_projections()] == [
        {"status": "OPEN", "ticket": 901, "volume": 0.01}
    ]


def test_projection_conflict_rolls_back_without_acknowledging_result(tmp_path):
    store = IntentStore(tmp_path / "intents.sqlite3")
    first = execute_once(
        store,
        _request(),
        lambda _payload: {"retcode": 10009, "deal": 902},
    )
    other = BrokerRequest.create(
        replace(_request().intent_key, signal_root="canal1_other"),
        {"symbol": "XAUUSD", "volume": 0.01},
        request_id="request-other",
        attempt_id="attempt-other",
        action_id="action-other",
    )
    second = execute_once(
        store,
        other,
        lambda _payload: {"retcode": 10009, "deal": 903},
    )
    assert store.apply_projection(
        first.intent_id,
        outcome_revision=first.outcome_revision,
        projection_key="signal:canal1_3086",
        projection={"ticket": 902},
        event_id="shared-event",
    ) is True

    with pytest.raises(ProjectionConflictError):
        store.apply_projection(
            second.intent_id,
            outcome_revision=second.outcome_revision,
            projection_key="signal:canal1_other",
            projection={"ticket": 903},
            event_id="shared-event",
        )

    assert store.get(second.intent_id).applied_utc is None
    assert len(store.list_projections()) == 1


def test_rejected_intent_requires_explicit_policy_retry_transition(tmp_path):
    store = IntentStore(tmp_path / "intents.sqlite3")
    first_request = _request()
    rejected = execute_once(
        store,
        first_request,
        lambda _payload: {"retcode": 10020, "comment": "price changed"},
    )
    retry = _request(attempt_id="attempt-2")

    redelivery = execute_once(store, retry, lambda _payload: {"retcode": 10009})
    assert redelivery.state is IntentState.REJECTED

    prepared = store.prepare_retry(
        retry,
        previous_attempt_id=first_request.attempt_id,
        reason="price_changed_policy_retry",
        retryable_retcodes={10020},
    )
    assert prepared.state is IntentState.PREPARED
    assert prepared.attempt_id is None

    done = execute_once(store, retry, lambda _payload: {"retcode": 10009, "deal": 904})
    assert done.state is IntentState.DONE
    assert done.outcome_revision == rejected.outcome_revision + 1


def test_predispatch_resolution_after_retry_advances_outcome_revision(tmp_path):
    store = IntentStore(tmp_path / "intents.sqlite3")
    first = _request()
    rejected = execute_once(
        store,
        first,
        lambda _payload: {"retcode": 10020, "comment": "price changed"},
    )
    retry = _request(attempt_id="attempt-2")
    store.prepare_retry(
        retry,
        previous_attempt_id=first.attempt_id,
        reason="price_changed_policy_retry",
        retryable_retcodes={10020},
    )
    store.admit(retry, worker_session_id="worker-2")
    store.record_predispatch_failure(
        retry,
        "ticket_not_found",
        worker_session_id="worker-2",
    )

    resolved = store.resolve_predispatch_outcome(
        retry,
        BrokerOutcome(IntentState.DONE, retcode=10036, error="ticket_not_found"),
    )

    assert resolved.outcome_revision == rejected.outcome_revision + 1
    assert resolved.state is IntentState.DONE


@pytest.mark.parametrize(
    "result",
    [
        None,
        {"retcode": 10008, "order": 88},
        {"retcode": 10010, "deal": 89, "volume": 0.005},
        {"retcode": 10011},
    ],
)
def test_ambiguous_or_incomplete_outcome_can_never_enter_retry_transition(tmp_path, result):
    store = IntentStore(tmp_path / "intents.sqlite3")
    first_request = _request()
    record = execute_once(store, first_request, lambda _payload: result)

    with pytest.raises(RetryNotAllowedError):
        store.prepare_retry(
            _request(attempt_id="attempt-2"),
            previous_attempt_id=first_request.attempt_id,
            reason="unsafe_retry",
            retryable_retcodes={10020},
        )

    assert store.get(record.intent_id).state is record.state


def test_retry_rejects_payload_revision_or_stale_attempt_drift(tmp_path):
    store = IntentStore(tmp_path / "intents.sqlite3")
    first_request = _request()
    execute_once(store, first_request, lambda _payload: {"retcode": 10020})

    with pytest.raises(IntentConflictError):
        store.prepare_retry(
            _request({"symbol": "XAUUSD", "volume": 0.02}, attempt_id="attempt-2"),
            previous_attempt_id=first_request.attempt_id,
            reason="payload_drift",
            retryable_retcodes={10020},
        )
    with pytest.raises(IntentConflictError):
        store.prepare_retry(
            _request(attempt_id="attempt-2"),
            previous_attempt_id="stale-attempt",
            reason="stale_retry",
            retryable_retcodes={10020},
        )


def test_intent_and_exposure_reservation_are_prepared_atomically(tmp_path):
    path = tmp_path / "intents.sqlite3"
    store = IntentStore(path)
    request = _revision_request(3, attempt_id="attempt-r3")

    record = store.prepare_reserved(
        request,
        reservation_key="account/canal1/canal1_3086/g1/entry-1",
        policy_revision=3,
        expires_utc=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    )

    assert record.state is IntentState.PREPARED
    reservation = store.get_reservation(request.intent_id)
    assert reservation.state == "HELD"
    assert reservation.policy_revision == 3
    assert reservation.payload == {"symbol": "XAUUSD", "volume": 0.01}

    native_request = {"action": 1, "symbol": "XAUUSD", "volume": 0.01, "price": 2500.2}
    store.begin_dispatch(request, prepared_payload=native_request)
    assert store.get(request.intent_id).state is IntentState.DISPATCHING
    with sqlite3.connect(path) as connection:
        prepared_json = connection.execute(
            "SELECT prepared_payload_json FROM execution_attempts WHERE attempt_id = ?",
            (request.attempt_id,),
        ).fetchone()[0]
    assert json.loads(prepared_json) == native_request


def test_newer_revision_supersedes_only_an_unsent_reservation(tmp_path):
    store = IntentStore(tmp_path / "intents.sqlite3")
    old = _revision_request(1, attempt_id="attempt-r1")
    new = _revision_request(2, attempt_id="attempt-r2")
    key = "account/canal1/canal1_3086/g1/entry-1"
    store.prepare_reserved(old, reservation_key=key, policy_revision=1)

    prepared = store.prepare_reserved(new, reservation_key=key, policy_revision=2)

    assert prepared.state is IntentState.PREPARED
    assert store.get_reservation(old.intent_id).state == "SUPERSEDED"
    assert store.get_reservation(new.intent_id).state == "HELD"
    with pytest.raises(ReservationConflictError, match="reservation"):
        store.begin_dispatch(old)
    store.begin_dispatch(new)


@pytest.mark.parametrize(
    "result",
    [
        None,
        {"retcode": 10008, "order": 88},
        {"retcode": 10010, "deal": 89, "volume": 0.005},
        {"retcode": 10009, "deal": 90},
    ],
)
def test_exposed_or_ambiguous_reservation_cannot_be_superseded_or_released(tmp_path, result):
    store = IntentStore(tmp_path / "intents.sqlite3")
    old = _revision_request(1, attempt_id="attempt-r1")
    new = _revision_request(2, attempt_id="attempt-r2")
    key = "account/canal1/canal1_3086/g1/entry-1"
    store.prepare_reserved(old, reservation_key=key, policy_revision=1)
    current = execute_once(store, old, lambda _payload: result)

    with pytest.raises(ReservationConflictError, match="reservation"):
        store.prepare_reserved(new, reservation_key=key, policy_revision=2)
    if current.state in {IntentState.UNKNOWN, IntentState.PLACED, IntentState.DONE_PARTIAL}:
        with pytest.raises(ReservationConflictError, match="reservation"):
            store.release_reservation(
                old.intent_id,
                outcome_revision=current.outcome_revision,
                reason="unsafe_release",
            )
    assert store.get_reservation(old.intent_id).state == "HELD"


def test_expired_reservation_is_revalidated_before_dispatch(tmp_path):
    store = IntentStore(tmp_path / "intents.sqlite3")
    request = _revision_request(1, attempt_id="attempt-expired")
    store.prepare_reserved(
        request,
        reservation_key="account/canal1/canal1_3086/g1/entry-1",
        policy_revision=1,
        expires_utc=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    )

    with pytest.raises(ReservationConflictError, match="expired"):
        store.begin_dispatch(request)
    assert store.get(request.intent_id).state is IntentState.PREPARED


def test_concurrent_reservation_allows_only_one_policy_revision(tmp_path):
    store = IntentStore(tmp_path / "intents.sqlite3", busy_timeout_ms=2_000)
    requests = [
        _revision_request(index, attempt_id=f"attempt-r{index}")
        for index in range(1, 9)
    ]
    key = "account/canal1/canal1_3086/g1/entry-1"

    def reserve(request):
        try:
            return store.prepare_reserved(
                request,
                reservation_key=key,
                policy_revision=request.intent_key.revision,
            ).intent_id
        except ReservationConflictError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(reserve, requests))

    held = [row for row in store.list_reservations() if row.state == "HELD"]
    assert len(held) == 1
    assert held[0].policy_revision == 8


def test_reconciliation_advances_outcome_revision_without_new_dispatch(tmp_path):
    store = IntentStore(tmp_path / "intents.sqlite3")
    request = _request()
    partial = execute_once(
        store,
        request,
        lambda _payload: {
            "retcode": 10010,
            "order": 901,
            "deal": 902,
            "volume": 0.005,
            "price": 2500.2,
        },
    )
    assert store.apply_projection(
        request.intent_id,
        outcome_revision=partial.outcome_revision,
        projection_key="signal:canal1_3086",
        projection={"state": "DONE_PARTIAL", "volume": 0.005},
        event_id=f"{request.intent_id}:1",
    ) is True

    reconciled = store.reconcile_outcome(
        request,
        BrokerOutcome(
            IntentState.DONE,
            retcode=10009,
            order=901,
            deal=902,
            filled_volume=0.01,
            price=2500.2,
        ),
        evidence={"positions_get": {"ticket": 901, "volume": 0.01}},
    )

    assert reconciled.state is IntentState.DONE
    assert reconciled.outcome_revision == partial.outcome_revision + 1
    assert reconciled.applied_utc is None
    with sqlite3.connect(store.path) as connection:
        rows = connection.execute(
            "SELECT from_state, to_state, from_revision, to_revision FROM execution_reconciliations"
        ).fetchall()
        attempts = connection.execute("SELECT COUNT(*) FROM execution_attempts").fetchone()[0]
    assert rows == [("DONE_PARTIAL", "DONE", 1, 2)]
    assert attempts == 1


def test_reconciliation_rejects_ticket_change_or_partial_regression(tmp_path):
    store = IntentStore(tmp_path / "intents.sqlite3")
    request = _request()
    execute_once(
        store,
        request,
        lambda _payload: {"retcode": 10010, "order": 901, "volume": 0.005},
    )

    with pytest.raises(ReconciliationConflictError):
        store.reconcile_outcome(
            request,
            BrokerOutcome(
                IntentState.DONE,
                retcode=10009,
                order=999,
                filled_volume=0.01,
            ),
            evidence={"positions_get": {"ticket": 999}},
        )
    with pytest.raises(ReconciliationConflictError):
        store.reconcile_outcome(
            request,
            BrokerOutcome(
                IntentState.DONE_PARTIAL,
                retcode=10010,
                order=901,
                filled_volume=0.001,
            ),
            evidence={"positions_get": {"ticket": 901, "volume": 0.001}},
        )
