"""Durable execution-intent ledger for isolated broker actions."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable, Mapping

from mt5_protocol import (
    BrokerOutcome,
    BrokerRequest,
    ImmutableJsonMapping,
    IntentKey,
    IntentState,
    classify_broker_result,
)
from mt5_trade_protocol import TradePreparation


class IntentConflictError(RuntimeError):
    pass


class DispatchNotAllowedError(RuntimeError):
    pass


class OutcomePersistenceError(RuntimeError):
    def __init__(self, outcome: BrokerOutcome, cause: Exception):
        super().__init__(f"broker outcome could not be persisted: {type(cause).__name__}")
        self.outcome = outcome
        self.cause = cause


class PreDispatchError(RuntimeError):
    def __init__(self, cause: Exception):
        super().__init__(f"broker request failed before dispatch: {type(cause).__name__}")
        self.cause = cause


class RetryNotAllowedError(RuntimeError):
    pass


class ProjectionConflictError(RuntimeError):
    pass


class ReservationConflictError(RuntimeError):
    pass


class PendingRevisionBlockedError(ReservationConflictError):
    """A prior execution must be reconciled before another logical action."""


_PENDING_SCOPE_FIELDS = ("account_fingerprint", "channel", "signal_root", "generation", "leg", "operation")
_PENDING_SCOPE_WHERE = " AND ".join(
    f"json_extract(identity_json, '$.{name}') = ?" for name in _PENDING_SCOPE_FIELDS)


class ReconciliationConflictError(RuntimeError):
    pass


_RECOVERY_UNKNOWN_REASONS = {
    "gateway_closed_with_unresolved_dispatch",
    "worker_died_with_unresolved_request",
    "worker_exited_before_restart",
    "worker_terminated_with_unresolved_dispatch",
}


@dataclass(frozen=True)
class IntentRecord:
    intent_id: str
    state: IntentState
    attempt_id: str | None
    payload: Mapping[str, Any]
    outcome: Mapping[str, Any] | None
    outcome_revision: int
    applied_utc: str | None


@dataclass(frozen=True)
class CurrentIntentSnapshot:
    intent_key: IntentKey
    record: IntentRecord


@dataclass(frozen=True)
class DispatchSnapshot:
    request: BrokerRequest
    prepared_payload: Mapping[str, Any] | None
    preparation: TradePreparation | None
    worker_session_id: str | None
    worker_pid: int | None
    dispatched_utc: str
    dispatched_monotonic_ns: int | None


@dataclass(frozen=True)
class EffectProjection:
    intent_id: str
    outcome_revision: int
    projection_key: str
    projection: Mapping[str, Any]
    event_id: str
    outcome: Mapping[str, Any]
    created_utc: str


@dataclass(frozen=True)
class IntentReservation:
    reservation_key: str
    intent_id: str
    policy_revision: int
    payload: Mapping[str, Any]
    state: str
    expires_utc: str | None
    superseded_by_intent_id: str | None
    release_reason: str | None


class IntentStore:
    def __init__(self, path: str | Path, *, busy_timeout_ms: int = 250):
        self.path = Path(path)
        self.busy_timeout_ms = busy_timeout_ms
        if not isinstance(busy_timeout_ms, int) or busy_timeout_ms <= 0:
            raise ValueError("busy_timeout_ms must be a positive integer")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS execution_intent_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS execution_intents (
                    intent_id TEXT PRIMARY KEY,
                    identity_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempt_id TEXT,
                    outcome_json TEXT,
                    outcome_revision INTEGER NOT NULL DEFAULT 0,
                    applied_utc TEXT,
                    created_utc TEXT NOT NULL,
                    updated_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS execution_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    intent_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    dispatched_utc TEXT NOT NULL,
                    completed_utc TEXT,
                    outcome_json TEXT,
                    request_id TEXT,
                    action_id TEXT,
                    request_json TEXT,
                    worker_session_id TEXT,
                    worker_pid INTEGER,
                    dispatched_monotonic_ns INTEGER,
                    completed_monotonic_ns INTEGER,
                    prepared_payload_json TEXT,
                    preparation_json TEXT,
                    FOREIGN KEY(intent_id) REFERENCES execution_intents(intent_id)
                );
                CREATE TABLE IF NOT EXISTS execution_admissions (
                    request_id TEXT PRIMARY KEY,
                    attempt_id TEXT NOT NULL UNIQUE,
                    intent_id TEXT NOT NULL,
                    action_id TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    worker_session_id TEXT,
                    state TEXT NOT NULL,
                    admitted_utc TEXT NOT NULL,
                    completed_utc TEXT,
                    error TEXT,
                    FOREIGN KEY(intent_id) REFERENCES execution_intents(intent_id)
                );
                CREATE INDEX IF NOT EXISTS execution_attempts_intent
                    ON execution_attempts(intent_id);
                CREATE INDEX IF NOT EXISTS execution_admissions_intent
                    ON execution_admissions(intent_id);
                CREATE TABLE IF NOT EXISTS execution_retries (
                    attempt_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE,
                    intent_id TEXT NOT NULL,
                    previous_attempt_id TEXT NOT NULL,
                    action_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    rejected_retcode INTEGER NOT NULL,
                    request_json TEXT NOT NULL,
                    created_utc TEXT NOT NULL,
                    FOREIGN KEY(intent_id) REFERENCES execution_intents(intent_id)
                );
                CREATE INDEX IF NOT EXISTS execution_retries_intent
                    ON execution_retries(intent_id);
                CREATE TABLE IF NOT EXISTS execution_effect_projections (
                    intent_id TEXT NOT NULL,
                    outcome_revision INTEGER NOT NULL,
                    projection_key TEXT NOT NULL,
                    projection_json TEXT NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    outcome_json TEXT NOT NULL,
                    created_utc TEXT NOT NULL,
                    PRIMARY KEY(intent_id, outcome_revision),
                    FOREIGN KEY(intent_id) REFERENCES execution_intents(intent_id)
                );
                CREATE INDEX IF NOT EXISTS execution_effect_projection_key
                    ON execution_effect_projections(projection_key, created_utc);
                CREATE TABLE IF NOT EXISTS execution_reservations (
                    reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reservation_key TEXT NOT NULL,
                    intent_id TEXT NOT NULL UNIQUE,
                    policy_revision INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    expires_utc TEXT,
                    superseded_by_intent_id TEXT,
                    release_reason TEXT,
                    created_utc TEXT NOT NULL,
                    updated_utc TEXT NOT NULL,
                    FOREIGN KEY(intent_id) REFERENCES execution_intents(intent_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS execution_reservations_one_held
                    ON execution_reservations(reservation_key) WHERE state = 'HELD';
                CREATE INDEX IF NOT EXISTS execution_reservations_intent
                    ON execution_reservations(intent_id);
                CREATE TABLE IF NOT EXISTS execution_reconciliations (
                    intent_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL,
                    from_revision INTEGER NOT NULL,
                    to_revision INTEGER NOT NULL,
                    from_state TEXT NOT NULL,
                    to_state TEXT NOT NULL,
                    outcome_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    created_utc TEXT NOT NULL,
                    PRIMARY KEY(intent_id, to_revision),
                    FOREIGN KEY(intent_id) REFERENCES execution_intents(intent_id)
                );
                CREATE TABLE IF NOT EXISTS execution_broker_entry_claims (
                    account_fingerprint TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('order', 'position', 'deal')),
                    broker_id INTEGER NOT NULL CHECK(broker_id > 0),
                    intent_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL,
                    PRIMARY KEY(account_fingerprint, kind, broker_id),
                    FOREIGN KEY(intent_id) REFERENCES execution_intents(intent_id),
                    FOREIGN KEY(attempt_id) REFERENCES execution_attempts(attempt_id)
                );
                CREATE INDEX IF NOT EXISTS execution_attempt_broker_order
                    ON execution_attempts(json_extract(outcome_json, '$.order'));
                CREATE INDEX IF NOT EXISTS execution_attempt_broker_deal
                    ON execution_attempts(json_extract(outcome_json, '$.deal'));
                """
            )
            _ensure_column(connection, "execution_attempts", "request_id", "TEXT")
            _ensure_column(connection, "execution_attempts", "action_id", "TEXT")
            _ensure_column(connection, "execution_attempts", "request_json", "TEXT")
            _ensure_column(connection, "execution_attempts", "worker_session_id", "TEXT")
            _ensure_column(connection, "execution_attempts", "worker_pid", "INTEGER")
            _ensure_column(connection, "execution_attempts", "dispatched_monotonic_ns", "INTEGER")
            _ensure_column(connection, "execution_attempts", "completed_monotonic_ns", "INTEGER")
            _ensure_column(connection, "execution_attempts", "prepared_payload_json", "TEXT")
            _ensure_column(connection, "execution_attempts", "preparation_json", "TEXT")
            _ensure_column(
                connection,
                "execution_intents",
                "outcome_revision",
                "INTEGER NOT NULL DEFAULT 0",
            )
            _migrate_execution_admissions(connection)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS execution_pending_revisions (
                    scope_json TEXT NOT NULL,
                    action_id TEXT NOT NULL,
                    source_revision INTEGER NOT NULL,
                    revision INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(scope_json, action_id, source_revision),
                    UNIQUE(scope_json, revision)
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS execution_pending_lineage ON execution_intents ("
                + ", ".join(f"json_extract(identity_json, '$.{name}')" for name in (*_PENDING_SCOPE_FIELDS, "revision"))
                + ")")
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _write(self):
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise
        finally:
            connection.close()

    def bind_pending_request(self, request: BrokerRequest) -> BrokerRequest:
        """Atomically bind a spool action/version to a persistent effect revision.

        Local queue revisions remain unchanged. Gaps from cancelled allocations
        are intentional; an allocated revision must never identify another effect.
        """
        request.to_dict()
        key = request.intent_key
        if key.operation not in {"MODIFY_SLTP", "CLOSE_POSITION", "CANCEL_PENDING"}:
            raise ValueError("pending revision binding requires a management operation")
        scope = {name: getattr(key, name) for name in _PENDING_SCOPE_FIELDS}
        scope_json = _canonical_json(scope)
        scope_values = tuple(scope.values())
        payload_json = _canonical_json(dict(request.payload))
        with self._write() as connection:
            bound = connection.execute("""
                SELECT revision, payload_json FROM execution_pending_revisions
                WHERE scope_json = ? AND action_id = ? AND source_revision = ?
            """, (scope_json, request.action_id, key.revision)).fetchone()
            if bound is not None:
                if bound["payload_json"] != payload_json:
                    raise IntentConflictError("pending action/version was redelivered with different payload")
                revision = int(bound["revision"])
            else:
                # Old databases have no bindings. Admissions/attempts establish
                # ownership of an old revision; payload equality alone does not.
                legacy = connection.execute("SELECT * FROM execution_intents WHERE intent_id = ?",
                                            (request.intent_id,)).fetchone()
                occupied = connection.execute("""
                    SELECT 1 FROM execution_pending_revisions WHERE scope_json = ? AND revision = ?
                """, (scope_json, key.revision)).fetchone()
                owners = set()
                if legacy is not None and occupied is None:
                    owners.update(row[0] for row in connection.execute(
                        "SELECT action_id FROM execution_admissions WHERE intent_id = ?", (request.intent_id,)))
                    owners.update(row[0] for row in connection.execute(
                        "SELECT action_id FROM execution_attempts WHERE intent_id = ?", (request.intent_id,)))
                owns_legacy = legacy is not None and occupied is None and (
                    request.action_id in owners or (not owners and legacy["state"] == "PREPARED"
                                                   and legacy["payload_json"] == payload_json))
                if owns_legacy:
                    if legacy["payload_json"] != payload_json:
                        raise IntentConflictError("legacy pending action changed its payload without a revision")
                    revision = key.revision
                else:
                    maximum = connection.execute(
                        "SELECT json_extract(identity_json, '$.revision') FROM execution_intents WHERE "
                        + _PENDING_SCOPE_WHERE + " ORDER BY json_extract(identity_json, '$.revision') DESC LIMIT 1",
                        scope_values).fetchone()
                    allocated = connection.execute("""
                        SELECT revision FROM execution_pending_revisions WHERE scope_json = ?
                        ORDER BY revision DESC LIMIT 1
                    """, (scope_json,)).fetchone()
                    revision = max(key.revision, int(maximum[0]) + 1 if maximum else 0,
                                   int(allocated[0]) + 1 if allocated else 0)
            durable_key = replace(key, revision=revision)
            _check_pending_execution_barrier(connection, durable_key)
            if bound is None:
                connection.execute("""
                    INSERT INTO execution_pending_revisions
                    (scope_json, action_id, source_revision, revision, payload_json) VALUES (?, ?, ?, ?, ?)
                """, (scope_json, request.action_id, key.revision, revision, payload_json))
        return replace(request, intent_key=durable_key, intent_id=durable_key.intent_id)

    def prepare(self, request: BrokerRequest) -> IntentRecord:
        request.to_dict()
        identity_json = _canonical_json(request.intent_key.to_dict())
        payload_json = _canonical_json(dict(request.payload))
        now = _utc_now()
        with self._write() as connection:
            row = connection.execute(
                "SELECT * FROM execution_intents WHERE intent_id = ?",
                (request.intent_id,),
            ).fetchone()
            if row is not None:
                if row["identity_json"] != identity_json or row["payload_json"] != payload_json:
                    raise IntentConflictError(
                        f"intent {request.intent_id} was redelivered with different identity or payload"
                    )
                return _record(row)
            connection.execute(
                """
                INSERT INTO execution_intents (
                    intent_id, identity_json, payload_json, state, attempt_id,
                    outcome_json, outcome_revision, applied_utc, created_utc, updated_utc
                ) VALUES (?, ?, ?, ?, NULL, NULL, 0, NULL, ?, ?)
                """,
                (
                    request.intent_id,
                    identity_json,
                    payload_json,
                    IntentState.PREPARED.value,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM execution_intents WHERE intent_id = ?",
                (request.intent_id,),
            ).fetchone()
            return _record(row)

    def prepare_reserved(
        self,
        request: BrokerRequest,
        *,
        reservation_key: str,
        policy_revision: int,
        expires_utc: str | None = None,
    ) -> IntentRecord:
        """Atomically persist an intent and acquire its exposure reservation."""
        request.to_dict()
        if not isinstance(reservation_key, str) or not reservation_key:
            raise ValueError("reservation_key must be a non-empty string")
        if (
            not isinstance(policy_revision, int)
            or isinstance(policy_revision, bool)
            or policy_revision < 0
            or policy_revision != request.intent_key.revision
        ):
            raise ValueError("policy_revision must match the intent revision")
        _parse_utc(expires_utc, "expires_utc", optional=True)
        identity_json = _canonical_json(request.intent_key.to_dict())
        payload_json = _canonical_json(dict(request.payload))
        now = _utc_now()
        with self._write() as connection:
            intent = connection.execute(
                "SELECT * FROM execution_intents WHERE intent_id = ?",
                (request.intent_id,),
            ).fetchone()
            if intent is not None and (
                intent["identity_json"] != identity_json
                or intent["payload_json"] != payload_json
            ):
                raise IntentConflictError(
                    f"intent {request.intent_id} was redelivered with different identity or payload"
                )

            own_reservation = connection.execute(
                "SELECT * FROM execution_reservations WHERE intent_id = ?",
                (request.intent_id,),
            ).fetchone()
            if own_reservation is not None:
                if (
                    own_reservation["reservation_key"] != reservation_key
                    or own_reservation["policy_revision"] != policy_revision
                    or own_reservation["payload_json"] != payload_json
                    or own_reservation["expires_utc"] != expires_utc
                ):
                    raise ReservationConflictError(
                        "reservation was redelivered with different content"
                    )
                if own_reservation["state"] == "RELEASED":
                    if IntentState(intent["state"]) not in {
                        IntentState.DONE,
                        IntentState.REJECTED,
                    }:
                        raise ReservationConflictError(
                            "released reservation does not have a terminal outcome"
                        )
                    return _record(intent)
                if own_reservation["state"] != "HELD":
                    raise ReservationConflictError(
                        f"reservation is already {own_reservation['state']}"
                    )
                return _record(intent)

            active = connection.execute(
                """
                SELECT r.*, i.state AS intent_state, i.identity_json AS intent_identity_json
                  FROM execution_reservations AS r
                  JOIN execution_intents AS i ON i.intent_id = r.intent_id
                 WHERE r.reservation_key = ? AND r.state = 'HELD'
                """,
                (reservation_key,),
            ).fetchone()
            if active is not None:
                previous_key = IntentKey.from_dict(json.loads(active["intent_identity_json"]))
                if not _same_reservation_lineage(previous_key, request.intent_key):
                    raise ReservationConflictError(
                        "reservation key collides with another causal lineage"
                    )
                if IntentState(active["intent_state"]) not in {
                    IntentState.PREPARED,
                    IntentState.REJECTED,
                }:
                    raise ReservationConflictError(
                        f"reservation is held by {active['intent_state']} exposure"
                    )
                if policy_revision <= active["policy_revision"]:
                    raise ReservationConflictError(
                        "reservation is held by an equal or newer policy revision"
                    )
                connection.execute(
                    """
                    UPDATE execution_reservations
                       SET state = 'SUPERSEDED', superseded_by_intent_id = ?,
                           release_reason = 'newer_policy_revision', updated_utc = ?
                     WHERE reservation_id = ? AND state = 'HELD'
                    """,
                    (request.intent_id, now, active["reservation_id"]),
                )

            if intent is None:
                connection.execute(
                    """
                    INSERT INTO execution_intents (
                        intent_id, identity_json, payload_json, state, attempt_id,
                        outcome_json, outcome_revision, applied_utc, created_utc, updated_utc
                    ) VALUES (?, ?, ?, ?, NULL, NULL, 0, NULL, ?, ?)
                    """,
                    (
                        request.intent_id,
                        identity_json,
                        payload_json,
                        IntentState.PREPARED.value,
                        now,
                        now,
                    ),
                )
            elif IntentState(intent["state"]) is not IntentState.PREPARED:
                raise ReservationConflictError(
                    f"intent cannot acquire a reservation in state {intent['state']}"
                )
            try:
                connection.execute(
                    """
                    INSERT INTO execution_reservations (
                        reservation_key, intent_id, policy_revision, payload_json,
                        state, expires_utc, superseded_by_intent_id, release_reason,
                        created_utc, updated_utc
                    ) VALUES (?, ?, ?, ?, 'HELD', ?, NULL, NULL, ?, ?)
                    """,
                    (
                        reservation_key,
                        request.intent_id,
                        policy_revision,
                        payload_json,
                        expires_utc,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ReservationConflictError("reservation is already held") from exc
            row = connection.execute(
                "SELECT * FROM execution_intents WHERE intent_id = ?",
                (request.intent_id,),
            ).fetchone()
            return _record(row)

    def admit(
        self,
        request: BrokerRequest,
        *,
        worker_session_id: str | None = None,
    ) -> None:
        request_json = _canonical_json(request.to_dict())
        identity_json = _canonical_json(request.intent_key.to_dict())
        payload_json = _canonical_json(dict(request.payload))
        _validate_worker_session(worker_session_id)
        now = _utc_now()
        with self._write() as connection:
            intent = connection.execute(
                "SELECT * FROM execution_intents WHERE intent_id = ?",
                (request.intent_id,),
            ).fetchone()
            if intent is None:
                raise KeyError(request.intent_id)
            if (
                intent["identity_json"] != identity_json
                or intent["payload_json"] != payload_json
            ):
                raise IntentConflictError(
                    f"intent {request.intent_id} changed before durable admission"
                )
            if IntentState(intent["state"]) is not IntentState.PREPARED:
                raise DispatchNotAllowedError(
                    f"intent {request.intent_id} is already {intent['state']}"
                )
            existing = connection.execute(
                """
                SELECT * FROM execution_admissions
                 WHERE request_id = ? OR attempt_id = ?
                """,
                (request.request_id, request.attempt_id),
            ).fetchone()
            if existing is not None:
                _validate_admission_request(
                    existing,
                    request,
                    worker_session_id=worker_session_id,
                )
                if existing["state"] != "ADMITTED":
                    raise DispatchNotAllowedError(
                        f"request {request.request_id} is already {existing['state']}"
                    )
                return
            try:
                connection.execute(
                    """
                    INSERT INTO execution_admissions (
                        request_id, attempt_id, intent_id, action_id,
                        request_json, worker_session_id, state, admitted_utc,
                        completed_utc, error
                    ) VALUES (?, ?, ?, ?, ?, ?, 'ADMITTED', ?, NULL, NULL)
                    """,
                    (
                        request.request_id,
                        request.attempt_id,
                        request.intent_id,
                        request.action_id,
                        request_json,
                        worker_session_id,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise IntentConflictError(
                    "request_id and attempt_id must be globally unique"
                ) from exc

    def begin_dispatch(
        self,
        request: BrokerRequest,
        *,
        worker_session_id: str | None = None,
        worker_pid: int | None = None,
        monotonic_ns: int | None = None,
        prepared_payload: Mapping[str, Any] | None = None,
        preparation: TradePreparation | None = None,
    ) -> IntentRecord:
        request_value = request.to_dict()
        identity_json = _canonical_json(request.intent_key.to_dict())
        payload_json = _canonical_json(dict(request.payload))
        request_json = _canonical_json(request_value)
        prepared_payload_json = _canonical_json(
            dict(request.payload) if prepared_payload is None else dict(prepared_payload)
        )
        _validate_worker_session(worker_session_id)
        if worker_pid is None:
            worker_pid = os.getpid()
        if not isinstance(worker_pid, int) or isinstance(worker_pid, bool) or worker_pid <= 0:
            raise ValueError("worker_pid must be a positive integer")
        preparation_json = None
        if preparation is not None:
            _validate_dispatch_preparation(
                preparation, request, worker_session_id=worker_session_id,
                worker_pid=worker_pid, prepared_payload_json=prepared_payload_json,
            )
            preparation_json = _canonical_json(preparation.to_dict())
        if monotonic_ns is None:
            monotonic_ns = time.monotonic_ns()
        if not isinstance(monotonic_ns, int) or isinstance(monotonic_ns, bool) or monotonic_ns < 0:
            raise ValueError("monotonic_ns must be a non-negative integer")
        self.admit(request, worker_session_id=worker_session_id)
        now = _utc_now()
        with self._write() as connection:
            row = connection.execute(
                "SELECT * FROM execution_intents WHERE intent_id = ?",
                (request.intent_id,),
            ).fetchone()
            if row is None:
                raise KeyError(request.intent_id)
            if row["identity_json"] != identity_json or row["payload_json"] != payload_json:
                raise IntentConflictError(
                    f"intent {request.intent_id} changed after durable admission"
                )
            if IntentState(row["state"]) is not IntentState.PREPARED:
                raise DispatchNotAllowedError(
                    f"intent {request.intent_id} is already {row['state']}"
                )
            pending_scope = _canonical_json({name: getattr(request.intent_key, name) for name in _PENDING_SCOPE_FIELDS})
            if connection.execute("""
                SELECT 1 FROM execution_pending_revisions WHERE scope_json = ? AND revision = ?
            """, (pending_scope, request.intent_key.revision)).fetchone() is not None:
                # Allocation precedes broker preparation. Recheck atomically at
                # dispatch so a late ambiguous outcome cannot be overtaken.
                _check_pending_execution_barrier(connection, request.intent_key)
            reservation = connection.execute(
                "SELECT * FROM execution_reservations WHERE intent_id = ?",
                (request.intent_id,),
            ).fetchone()
            if reservation is not None:
                if reservation["state"] != "HELD":
                    raise ReservationConflictError(
                        f"reservation is {reservation['state']}, not dispatchable"
                    )
                if reservation["policy_revision"] != request.intent_key.revision:
                    raise ReservationConflictError("reservation policy revision changed")
                if reservation["payload_json"] != payload_json:
                    raise ReservationConflictError("reservation payload changed")
                expires_at = _parse_utc(
                    reservation["expires_utc"],
                    "expires_utc",
                    optional=True,
                )
                if expires_at is not None and expires_at <= datetime.now(timezone.utc):
                    raise ReservationConflictError("reservation expired before dispatch")
            admission = connection.execute(
                "SELECT * FROM execution_admissions WHERE request_id = ?",
                (request.request_id,),
            ).fetchone()
            _validate_admission_request(
                admission,
                request,
                worker_session_id=worker_session_id,
            )
            if admission["state"] != "ADMITTED":
                raise DispatchNotAllowedError(
                    f"request {request.request_id} is already {admission['state']}"
                )
            try:
                connection.execute(
                    """
                    INSERT INTO execution_attempts (
                        attempt_id, intent_id, state, dispatched_utc,
                        completed_utc, outcome_json, request_id, action_id,
                        request_json, worker_session_id, worker_pid,
                        dispatched_monotonic_ns, completed_monotonic_ns,
                        prepared_payload_json, preparation_json
                    ) VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                    """,
                    (
                        request.attempt_id,
                        request.intent_id,
                        IntentState.DISPATCHING.value,
                        now,
                        request.request_id,
                        request.action_id,
                        request_json,
                        worker_session_id,
                        worker_pid,
                        monotonic_ns,
                        prepared_payload_json,
                        preparation_json,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise DispatchNotAllowedError(
                    f"attempt {request.attempt_id} already exists"
                ) from exc
            connection.execute(
                """
                UPDATE execution_admissions
                   SET state = 'DISPATCHING'
                 WHERE request_id = ? AND state = 'ADMITTED'
                """,
                (request.request_id,),
            )
            connection.execute(
                """
                UPDATE execution_intents
                   SET state = ?, attempt_id = ?, updated_utc = ?
                 WHERE intent_id = ?
                """,
                (IntentState.DISPATCHING.value, request.attempt_id, now, request.intent_id),
            )
            row = connection.execute(
                "SELECT * FROM execution_intents WHERE intent_id = ?",
                (request.intent_id,),
            ).fetchone()
            return _record(row)

    def record_outcome(
        self,
        request: BrokerRequest,
        outcome: BrokerOutcome,
        *,
        monotonic_ns: int | None = None,
        projection_key: str | None = None,
        release_reservation_reason: str | None = None,
    ) -> IntentRecord:
        if outcome.state in {IntentState.PREPARED, IntentState.DISPATCHING}:
            raise ValueError("broker outcome cannot remain pre-dispatch")
        outcome_json = _canonical_json(outcome.to_dict())
        if monotonic_ns is None:
            monotonic_ns = time.monotonic_ns()
        if not isinstance(monotonic_ns, int) or isinstance(monotonic_ns, bool) or monotonic_ns < 0:
            raise ValueError("monotonic_ns must be a non-negative integer")
        _validate_atomic_projection_options(
            projection_key,
            release_reservation_reason,
        )
        now = _utc_now()
        with self._write() as connection:
            row = connection.execute(
                "SELECT * FROM execution_intents WHERE intent_id = ?",
                (request.intent_id,),
            ).fetchone()
            if row is None:
                raise KeyError(request.intent_id)
            if IntentState(row["state"]) is not IntentState.DISPATCHING:
                raise DispatchNotAllowedError(
                    f"cannot complete intent in state {row['state']}"
                )
            if row["attempt_id"] != request.attempt_id:
                raise IntentConflictError("outcome attempt does not match dispatched attempt")
            attempt = connection.execute(
                """
                SELECT * FROM execution_attempts
                 WHERE attempt_id = ? AND intent_id = ?
                """,
                (request.attempt_id, request.intent_id),
            ).fetchone()
            _validate_attempt_request(attempt, request)
            _check_entry_claim_owner(connection, request, outcome)
            connection.execute(
                """
                UPDATE execution_attempts
                   SET state = ?, completed_utc = ?, outcome_json = ?,
                       completed_monotonic_ns = ?
                 WHERE attempt_id = ? AND intent_id = ?
                """,
                (
                    outcome.state.value,
                    now,
                    outcome_json,
                    monotonic_ns,
                    request.attempt_id,
                    request.intent_id,
                ),
            )
            connection.execute(
                """
                UPDATE execution_admissions
                   SET state = ?, completed_utc = ?, error = ?
                 WHERE request_id = ? AND attempt_id = ? AND intent_id = ?
                """,
                (
                    outcome.state.value,
                    now,
                    outcome.error,
                    request.request_id,
                    request.attempt_id,
                    request.intent_id,
                ),
            )
            connection.execute(
                """
                UPDATE execution_intents
                   SET state = ?, outcome_json = ?,
                       outcome_revision = outcome_revision + 1,
                       applied_utc = NULL, updated_utc = ?
                 WHERE intent_id = ?
                """,
                (outcome.state.value, outcome_json, now, request.intent_id),
            )
            row = connection.execute(
                "SELECT * FROM execution_intents WHERE intent_id = ?",
                (request.intent_id,),
            ).fetchone()
            if projection_key is not None:
                _apply_effect_projection_in_transaction(
                    connection,
                    row,
                    projection_key=projection_key,
                    projection=_effect_projection(
                        request,
                        outcome,
                        int(row["outcome_revision"]),
                    ),
                    event_id=(
                        f"{request.intent_id}:{int(row['outcome_revision'])}"
                    ),
                    release_reservation_reason=release_reservation_reason,
                    now=now,
                )
                row = connection.execute(
                    "SELECT * FROM execution_intents WHERE intent_id = ?",
                    (request.intent_id,),
                ).fetchone()
            return _record(row)

    def validate_admitted_request(
        self,
        request: BrokerRequest,
        *,
        worker_session_id: str | None,
    ) -> str:
        request.to_dict()
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM execution_admissions WHERE request_id = ?",
                (request.request_id,),
            ).fetchone()
        finally:
            connection.close()
        _validate_admission_request(
            row,
            request,
            worker_session_id=worker_session_id,
        )
        return str(row["state"])

    def record_predispatch_failure(
        self,
        request: BrokerRequest,
        reason: str,
        *,
        worker_session_id: str | None,
    ) -> IntentRecord:
        now = _utc_now()
        with self._write() as connection:
            admission = connection.execute(
                "SELECT * FROM execution_admissions WHERE request_id = ?",
                (request.request_id,),
            ).fetchone()
            _validate_admission_request(
                admission,
                request,
                worker_session_id=worker_session_id,
            )
            if admission["state"] == "FAILED_PRE_DISPATCH":
                row = connection.execute(
                    "SELECT * FROM execution_intents WHERE intent_id = ?",
                    (request.intent_id,),
                ).fetchone()
                return _record(row)
            if admission["state"] != "ADMITTED":
                raise DispatchNotAllowedError(
                    f"cannot record pre-dispatch failure in state {admission['state']}"
                )
            connection.execute(
                """
                UPDATE execution_admissions
                   SET state = 'FAILED_PRE_DISPATCH', completed_utc = ?, error = ?
                 WHERE request_id = ?
                """,
                (now, reason, request.request_id),
            )
            row = connection.execute(
                "SELECT * FROM execution_intents WHERE intent_id = ?",
                (request.intent_id,),
            ).fetchone()
            if row is None:
                raise KeyError(request.intent_id)
            if IntentState(row["state"]) is not IntentState.PREPARED:
                raise DispatchNotAllowedError(
                    f"pre-dispatch failure found intent in state {row['state']}"
                )
            return _record(row)

    def get_predispatch_failure(self, request_id: str) -> str | None:
        """Return the durable reason for a request that never reached dispatch."""
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("request_id must be a non-empty string")
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT state, error
                  FROM execution_admissions
                 WHERE request_id = ?
                """,
                (request_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None or row["state"] != "FAILED_PRE_DISPATCH":
            return None
        return row["error"]

    def resolve_predispatch_outcome(
        self,
        request: BrokerRequest,
        outcome: BrokerOutcome,
        *,
        projection_key: str | None = None,
        release_reservation_reason: str | None = None,
    ) -> IntentRecord:
        """Resolve a proven no-send request without fabricating a dispatch attempt."""
        request.to_dict()
        if outcome.state not in {IntentState.DONE, IntentState.REJECTED}:
            raise ValueError("pre-dispatch outcome must be DONE or REJECTED")
        _validate_atomic_projection_options(
            projection_key,
            release_reservation_reason,
        )
        outcome_json = _canonical_json(outcome.to_dict())
        now = _utc_now()
        with self._write() as connection:
            admission = connection.execute(
                "SELECT * FROM execution_admissions WHERE request_id = ?",
                (request.request_id,),
            ).fetchone()
            _validate_admission_request(admission, request)
            intent = connection.execute(
                "SELECT * FROM execution_intents WHERE intent_id = ?",
                (request.intent_id,),
            ).fetchone()
            if intent is None:
                raise KeyError(request.intent_id)
            if admission["state"] in {
                IntentState.DONE.value,
                IntentState.REJECTED.value,
            }:
                if (
                    admission["state"] != outcome.state.value
                    or intent["state"] != outcome.state.value
                    or intent["outcome_json"] != outcome_json
                ):
                    raise IntentConflictError(
                        "pre-dispatch outcome was redelivered with different content"
                    )
                return _record(intent)
            if admission["state"] != "FAILED_PRE_DISPATCH":
                raise DispatchNotAllowedError(
                    f"cannot resolve pre-dispatch outcome in {admission['state']}"
                )
            if IntentState(intent["state"]) is not IntentState.PREPARED:
                raise DispatchNotAllowedError(
                    f"pre-dispatch intent is already {intent['state']}"
                )
            connection.execute(
                """
                UPDATE execution_admissions
                   SET state = ?, completed_utc = ?, error = ?
                 WHERE request_id = ? AND state = 'FAILED_PRE_DISPATCH'
                """,
                (
                    outcome.state.value,
                    now,
                    outcome.error,
                    request.request_id,
                ),
            )
            connection.execute(
                """
                UPDATE execution_intents
                   SET state = ?, outcome_json = ?,
                       outcome_revision = outcome_revision + 1,
                       applied_utc = NULL, updated_utc = ?
                 WHERE intent_id = ? AND state = ?
                """,
                (
                    outcome.state.value,
                    outcome_json,
                    now,
                    request.intent_id,
                    IntentState.PREPARED.value,
                ),
            )
            row = connection.execute(
                "SELECT * FROM execution_intents WHERE intent_id = ?",
                (request.intent_id,),
            ).fetchone()
            if projection_key is not None:
                _apply_effect_projection_in_transaction(
                    connection,
                    row,
                    projection_key=projection_key,
                    projection=_effect_projection(
                        request,
                        outcome,
                        int(row["outcome_revision"]),
                    ),
                    event_id=(
                        f"{request.intent_id}:{int(row['outcome_revision'])}"
                    ),
                    release_reservation_reason=release_reservation_reason,
                    now=now,
                )
                row = connection.execute(
                    "SELECT * FROM execution_intents WHERE intent_id = ?",
                    (request.intent_id,),
                ).fetchone()
            return _record(row)

    def get_intent_key(self, intent_id: str) -> IntentKey:
        if not isinstance(intent_id, str) or not intent_id:
            raise ValueError("intent_id must be a non-empty string")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT identity_json FROM execution_intents WHERE intent_id = ?",
                (intent_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise KeyError(intent_id)
        return IntentKey.from_dict(json.loads(row["identity_json"]))

    def resolve_recovered_outcome(
        self,
        request: BrokerRequest,
        outcome: BrokerOutcome,
        *,
        worker_session_id: str | None,
        monotonic_ns: int | None = None,
    ) -> IntentRecord:
        if outcome.state in {IntentState.PREPARED, IntentState.DISPATCHING}:
            raise ValueError("recovered broker outcome must be post-dispatch")
        if monotonic_ns is None:
            monotonic_ns = time.monotonic_ns()
        if not isinstance(monotonic_ns, int) or isinstance(monotonic_ns, bool) or monotonic_ns < 0:
            raise ValueError("monotonic_ns must be a non-negative integer")
        outcome_json = _canonical_json(outcome.to_dict())
        now = _utc_now()
        with self._write() as connection:
            intent = connection.execute(
                "SELECT * FROM execution_intents WHERE intent_id = ?",
                (request.intent_id,),
            ).fetchone()
            if intent is None:
                raise KeyError(request.intent_id)
            if IntentState(intent["state"]) is not IntentState.UNKNOWN:
                raise DispatchNotAllowedError(
                    f"cannot resolve recovered intent in state {intent['state']}"
                )
            previous = json.loads(intent["outcome_json"] or "{}")
            if previous.get("error") not in _RECOVERY_UNKNOWN_REASONS:
                raise DispatchNotAllowedError("unknown outcome was not created by gateway recovery")
            if intent["attempt_id"] != request.attempt_id:
                raise IntentConflictError("recovered outcome attempt does not match intent")
            admission = connection.execute(
                "SELECT * FROM execution_admissions WHERE request_id = ?",
                (request.request_id,),
            ).fetchone()
            _validate_admission_request(
                admission,
                request,
                worker_session_id=worker_session_id,
            )
            attempt = connection.execute(
                """
                SELECT * FROM execution_attempts
                 WHERE attempt_id = ? AND intent_id = ?
                """,
                (request.attempt_id, request.intent_id),
            ).fetchone()
            _validate_attempt_request(
                attempt,
                request,
                worker_session_id=worker_session_id,
            )
            connection.execute(
                """
                UPDATE execution_attempts
                   SET state = ?, completed_utc = ?, outcome_json = ?,
                       completed_monotonic_ns = ?
                 WHERE attempt_id = ? AND intent_id = ?
                """,
                (
                    outcome.state.value,
                    now,
                    outcome_json,
                    monotonic_ns,
                    request.attempt_id,
                    request.intent_id,
                ),
            )
            connection.execute(
                """
                UPDATE execution_admissions
                   SET state = ?, completed_utc = ?, error = ?
                 WHERE request_id = ?
                """,
                (outcome.state.value, now, outcome.error, request.request_id),
            )
            connection.execute(
                """
                UPDATE execution_intents
                   SET state = ?, outcome_json = ?,
                       outcome_revision = outcome_revision + 1,
                       applied_utc = NULL, updated_utc = ?
                 WHERE intent_id = ?
                """,
                (outcome.state.value, outcome_json, now, request.intent_id),
            )
            row = connection.execute(
                "SELECT * FROM execution_intents WHERE intent_id = ?",
                (request.intent_id,),
            ).fetchone()
            return _record(row)

    def reconcile_entry_history(
        self,
        request: BrokerRequest,
        *,
        history: Mapping[str, Any],
        projection_key: str | None = None,
        release_reservation_reason: str | None = None,
    ) -> IntentRecord:
        history = ImmutableJsonMapping(history)
        return self.reconcile_outcome(
            request, None, evidence={"history": history.to_dict()},
            projection_key=projection_key, release_reservation_reason=release_reservation_reason,
            _entry_history=history,
        )

    def reconcile_outcome(
        self,
        request: BrokerRequest,
        outcome: BrokerOutcome | None,
        *,
        evidence: Mapping[str, Any],
        monotonic_ns: int | None = None,
        projection_key: str | None = None,
        release_reservation_reason: str | None = None,
        _entry_history: Mapping[str, Any] | None = None,
    ) -> IntentRecord:
        """Persist a broker reinspection without ever creating a new dispatch."""
        if (outcome is None) != (_entry_history is not None):
            raise ValueError("history reconciliation derives its own outcome")
        if outcome is not None and outcome.state in {IntentState.PREPARED, IntentState.DISPATCHING, IntentState.UNKNOWN}:
            raise ValueError("reconciliation must provide a more specific broker state")
        if not isinstance(evidence, Mapping) or not evidence:
            raise ValueError("reconciliation evidence must be a non-empty mapping")
        if monotonic_ns is None:
            monotonic_ns = time.monotonic_ns()
        if not isinstance(monotonic_ns, int) or isinstance(monotonic_ns, bool) or monotonic_ns < 0:
            raise ValueError("monotonic_ns must be a non-negative integer")
        _validate_atomic_projection_options(
            projection_key,
            release_reservation_reason,
        )
        now = _utc_now()
        with self._write() as connection:
            intent = connection.execute(
                "SELECT * FROM execution_intents WHERE intent_id = ?",
                (request.intent_id,),
            ).fetchone()
            if intent is None:
                raise KeyError(request.intent_id)
            current_state = IntentState(intent["state"])
            if current_state not in {
                IntentState.UNKNOWN,
                IntentState.PLACED,
                IntentState.DONE_PARTIAL,
            }:
                raise ReconciliationConflictError(
                    f"intent in {current_state.value} does not require reconciliation"
                )
            if intent["attempt_id"] != request.attempt_id:
                raise IntentConflictError("reconciliation attempt does not match intent")
            attempt = connection.execute(
                """
                SELECT * FROM execution_attempts
                 WHERE attempt_id = ? AND intent_id = ?
                """,
                (request.attempt_id, request.intent_id),
            ).fetchone()
            _validate_attempt_request(attempt, request)
            previous = BrokerOutcome.from_dict(json.loads(intent["outcome_json"] or "{}"))
            if _entry_history is not None:
                match, preparation = _match_entry_history(attempt, request, _entry_history)
                if match.order != match.position_id:
                    raise ReconciliationConflictError("entry consumer requires matching order/position identity")
                deal = previous.deal if _positive_broker_id(previous.deal) else match.deal_ids[0]
                if deal not in match.deal_ids:
                    raise ReconciliationConflictError("history omitted previously confirmed deal")
                outcome = BrokerOutcome(
                    IntentState.DONE, order=match.order, deal=deal,
                    filled_volume=match.volume, price=match.price,
                )
                evidence = {"history": dict(_entry_history), "preparation": preparation.to_dict(),
                            "match": {"order": match.order, "position_id": match.position_id,
                                      "deal_ids": list(match.deal_ids), "volume": match.volume,
                                      "price": match.price, "first_fill_msc": match.first_fill_msc,
                                      "last_fill_msc": match.last_fill_msc}}
                _claim_entry_history(connection, request, preparation, match)
            _check_entry_claim_owner(connection, request, outcome)
            _validate_reconciliation_progress(previous, outcome)
            evidence_json = _canonical_json(dict(evidence))
            outcome_json = _canonical_json(outcome.to_dict())
            from_revision = int(intent["outcome_revision"])
            to_revision = from_revision + 1
            connection.execute(
                """
                INSERT INTO execution_reconciliations (
                    intent_id, attempt_id, from_revision, to_revision,
                    from_state, to_state, outcome_json, evidence_json, created_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.intent_id,
                    request.attempt_id,
                    from_revision,
                    to_revision,
                    current_state.value,
                    outcome.state.value,
                    outcome_json,
                    evidence_json,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE execution_attempts
                   SET state = ?, completed_utc = ?, outcome_json = ?,
                       completed_monotonic_ns = ?
                 WHERE attempt_id = ? AND intent_id = ?
                """,
                (
                    outcome.state.value,
                    now,
                    outcome_json,
                    monotonic_ns,
                    request.attempt_id,
                    request.intent_id,
                ),
            )
            connection.execute(
                """
                UPDATE execution_admissions
                   SET state = ?, completed_utc = ?, error = ?
                 WHERE request_id = ? AND attempt_id = ? AND intent_id = ?
                """,
                (
                    outcome.state.value,
                    now,
                    outcome.error,
                    request.request_id,
                    request.attempt_id,
                    request.intent_id,
                ),
            )
            connection.execute(
                """
                UPDATE execution_intents
                   SET state = ?, outcome_json = ?, outcome_revision = ?,
                       applied_utc = NULL, updated_utc = ?
                 WHERE intent_id = ?
                """,
                (
                    outcome.state.value,
                    outcome_json,
                    to_revision,
                    now,
                    request.intent_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM execution_intents WHERE intent_id = ?",
                (request.intent_id,),
            ).fetchone()
            if projection_key is not None:
                _apply_effect_projection_in_transaction(
                    connection,
                    row,
                    projection_key=projection_key,
                    projection=_effect_projection(
                        request,
                        outcome,
                        int(row["outcome_revision"]),
                    ),
                    event_id=(
                        f"{request.intent_id}:{int(row['outcome_revision'])}"
                    ),
                    release_reservation_reason=release_reservation_reason,
                    now=now,
                )
                row = connection.execute(
                    "SELECT * FROM execution_intents WHERE intent_id = ?",
                    (request.intent_id,),
                ).fetchone()
            return _record(row)

    def validate_dispatched_request(
        self,
        request: BrokerRequest,
        *,
        worker_session_id: str | None,
    ) -> None:
        request.to_dict()
        self.validate_admitted_request(
            request,
            worker_session_id=worker_session_id,
        )
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT * FROM execution_attempts
                 WHERE attempt_id = ? AND intent_id = ?
                """,
                (request.attempt_id, request.intent_id),
            ).fetchone()
        finally:
            connection.close()
        _validate_attempt_request(
            row,
            request,
            worker_session_id=worker_session_id,
        )

    def mark_unknown(self, request: BrokerRequest, reason: str) -> IntentRecord:
        return self.record_outcome(
            request,
            BrokerOutcome(state=IntentState.UNKNOWN, error=reason),
        )

    def recover_dispatching(self, reason: str, *, account_fingerprint: str | None = None) -> int:
        now = _utc_now()
        outcome_json = _canonical_json(
            BrokerOutcome(state=IntentState.UNKNOWN, error=reason).to_dict()
        )
        with self._write() as connection:
            rows = connection.execute(
                "SELECT intent_id, attempt_id, identity_json FROM execution_intents WHERE state = ?",
                (IntentState.DISPATCHING.value,),
            ).fetchall()
            if account_fingerprint is not None:
                rows = [row for row in rows if json.loads(row["identity_json"])["account_fingerprint"] == account_fingerprint]
            for row in rows:
                connection.execute(
                    """
                    UPDATE execution_attempts
                       SET state = ?, completed_utc = ?, outcome_json = ?
                     WHERE attempt_id = ? AND intent_id = ?
                    """,
                    (
                        IntentState.UNKNOWN.value,
                        now,
                        outcome_json,
                        row["attempt_id"],
                        row["intent_id"],
                    ),
                )
                connection.execute(
                    """
                    UPDATE execution_admissions
                       SET state = ?, completed_utc = ?, error = ?
                     WHERE attempt_id = ? AND intent_id = ?
                    """,
                    (
                        IntentState.UNKNOWN.value,
                        now,
                        reason,
                        row["attempt_id"],
                        row["intent_id"],
                    ),
                )
                connection.execute(
                    """
                    UPDATE execution_intents
                       SET state = ?, outcome_json = ?,
                           outcome_revision = outcome_revision + 1,
                           applied_utc = NULL, updated_utc = ?
                     WHERE intent_id = ?
                    """,
                    (IntentState.UNKNOWN.value, outcome_json, now, row["intent_id"]),
                )
            return len(rows)

    def get(self, intent_id: str) -> IntentRecord:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM execution_intents WHERE intent_id = ?",
                (intent_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise KeyError(intent_id)
        return _record(row)

    def get_current_request(self, intent_id: str) -> BrokerRequest:
        connection = self._connect()
        try:
            intent = connection.execute(
                "SELECT attempt_id FROM execution_intents WHERE intent_id = ?",
                (intent_id,),
            ).fetchone()
            if intent is None:
                raise KeyError(intent_id)
            if not intent["attempt_id"]:
                raise KeyError(f"intent {intent_id} has no dispatched attempt")
            attempt = connection.execute(
                """
                SELECT request_json FROM execution_attempts
                 WHERE intent_id = ? AND attempt_id = ?
                """,
                (intent_id, intent["attempt_id"]),
            ).fetchone()
        finally:
            connection.close()
        if attempt is None:
            raise KeyError(f"intent {intent_id} current attempt was not found")
        return BrokerRequest.from_dict(json.loads(attempt["request_json"]))

    def get_current_dispatch(self, intent_id: str) -> DispatchSnapshot:
        """Read one coherent attempt; legacy missing evidence remains absent."""
        connection = self._connect()
        try:
            attempt = connection.execute(
                """
                SELECT a.*, i.identity_json AS intent_identity_json,
                       i.payload_json AS intent_payload_json
                  FROM execution_intents AS i
                  JOIN execution_attempts AS a
                    ON a.intent_id = i.intent_id AND a.attempt_id = i.attempt_id
                 WHERE i.intent_id = ?
                """, (intent_id,),
            ).fetchone()
        finally:
            connection.close()
        if attempt is None:
            raise KeyError(f"intent {intent_id} has no current dispatched attempt")
        request = BrokerRequest.from_dict(json.loads(attempt["request_json"]))
        _validate_attempt_request(attempt, request)
        if (
            request.intent_id != intent_id
            or request.attempt_id != attempt["attempt_id"]
            or _canonical_json(request.intent_key.to_dict()) != attempt["intent_identity_json"]
            or _canonical_json(dict(request.payload)) != attempt["intent_payload_json"]
        ):
            raise IntentConflictError("dispatch snapshot does not match current intent")
        prepared_payload = (
            ImmutableJsonMapping(json.loads(attempt["prepared_payload_json"]))
            if attempt["prepared_payload_json"] is not None else None
        )
        preparation = None
        if attempt["preparation_json"] is not None:
            preparation = TradePreparation.from_dict(json.loads(attempt["preparation_json"]))
            _validate_dispatch_preparation(
                preparation, request, worker_session_id=attempt["worker_session_id"],
                worker_pid=attempt["worker_pid"],
                prepared_payload_json=(
                    _canonical_json(dict(prepared_payload)) if prepared_payload is not None else None
                ),
            )
        return DispatchSnapshot(
            request=request, prepared_payload=prepared_payload, preparation=preparation,
            worker_session_id=attempt["worker_session_id"], worker_pid=attempt["worker_pid"],
            dispatched_utc=attempt["dispatched_utc"],
            dispatched_monotonic_ns=attempt["dispatched_monotonic_ns"],
        )

    def mark_applied(self, intent_id: str, *, outcome_revision: int) -> bool:
        if (
            not isinstance(outcome_revision, int)
            or isinstance(outcome_revision, bool)
            or outcome_revision <= 0
        ):
            raise ValueError("outcome_revision must be a positive integer")
        now = _utc_now()
        with self._write() as connection:
            row = connection.execute(
                """
                SELECT state, outcome_revision, applied_utc
                  FROM execution_intents
                 WHERE intent_id = ?
                """,
                (intent_id,),
            ).fetchone()
            if row is None:
                raise KeyError(intent_id)
            if IntentState(row["state"]) in {IntentState.PREPARED, IntentState.DISPATCHING}:
                raise DispatchNotAllowedError("cannot apply an unresolved dispatch")
            if row["outcome_revision"] != outcome_revision or row["applied_utc"] is not None:
                return False
            updated = connection.execute(
                """
                UPDATE execution_intents
                   SET applied_utc = ?, updated_utc = ?
                 WHERE intent_id = ?
                   AND outcome_revision = ?
                   AND applied_utc IS NULL
                """,
                (now, now, intent_id, outcome_revision),
            )
            return updated.rowcount == 1

    def apply_projection(
        self,
        intent_id: str,
        *,
        outcome_revision: int,
        projection_key: str,
        projection: Mapping[str, Any],
        event_id: str,
        release_reservation_reason: str | None = None,
    ) -> bool:
        """Persist a reconstructible effect and acknowledge its outcome atomically."""
        if not isinstance(intent_id, str) or not intent_id:
            raise ValueError("intent_id must be a non-empty string")
        if (
            not isinstance(outcome_revision, int)
            or isinstance(outcome_revision, bool)
            or outcome_revision <= 0
        ):
            raise ValueError("outcome_revision must be a positive integer")
        for name, value in (("projection_key", projection_key), ("event_id", event_id)):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(projection, Mapping):
            raise TypeError("projection must be a mapping")
        if release_reservation_reason is not None and (
            not isinstance(release_reservation_reason, str)
            or not release_reservation_reason
            or len(release_reservation_reason) > 1024
        ):
            raise ValueError("release_reservation_reason must be a non-empty string")
        projection_json = _canonical_json(dict(projection))
        now = _utc_now()
        with self._write() as connection:
            intent = connection.execute(
                "SELECT * FROM execution_intents WHERE intent_id = ?",
                (intent_id,),
            ).fetchone()
            if intent is None:
                raise KeyError(intent_id)
            if IntentState(intent["state"]) in {IntentState.PREPARED, IntentState.DISPATCHING}:
                raise DispatchNotAllowedError("cannot project an unresolved dispatch")
            if intent["outcome_revision"] != outcome_revision:
                raise ProjectionConflictError("projection targets a stale outcome revision")
            if not intent["outcome_json"]:
                raise ProjectionConflictError("projection requires a durable broker outcome")

            existing = connection.execute(
                """
                SELECT * FROM execution_effect_projections
                 WHERE (intent_id = ? AND outcome_revision = ?) OR event_id = ?
                """,
                (intent_id, outcome_revision, event_id),
            ).fetchone()
            if existing is not None:
                matches = (
                    existing["intent_id"] == intent_id
                    and existing["outcome_revision"] == outcome_revision
                    and existing["projection_key"] == projection_key
                    and existing["projection_json"] == projection_json
                    and existing["event_id"] == event_id
                    and existing["outcome_json"] == intent["outcome_json"]
                )
                if not matches:
                    raise ProjectionConflictError(
                        "projection identity was reused with different content"
                    )
                if intent["applied_utc"] is None:
                    raise ProjectionConflictError(
                        "projection exists without its atomic application acknowledgement"
                    )
                if release_reservation_reason is not None:
                    reservation = connection.execute(
                        "SELECT * FROM execution_reservations WHERE intent_id = ?",
                        (intent_id,),
                    ).fetchone()
                    if (
                        reservation is None
                        or reservation["state"] != "RELEASED"
                        or reservation["release_reason"] != release_reservation_reason
                    ):
                        raise ReservationConflictError(
                            "projection was acknowledged without the requested reservation release"
                        )
                return False
            if intent["applied_utc"] is not None:
                raise ProjectionConflictError(
                    "outcome was acknowledged without a durable effect projection"
                )

            try:
                connection.execute(
                    """
                    INSERT INTO execution_effect_projections (
                        intent_id, outcome_revision, projection_key, projection_json,
                        event_id, outcome_json, created_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        intent_id,
                        outcome_revision,
                        projection_key,
                        projection_json,
                        event_id,
                        intent["outcome_json"],
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ProjectionConflictError("projection identity already exists") from exc
            if release_reservation_reason is not None:
                if IntentState(intent["state"]) not in {
                    IntentState.DONE,
                    IntentState.REJECTED,
                }:
                    raise ReservationConflictError(
                        f"reservation cannot be released from {intent['state']}"
                    )
                reservation = connection.execute(
                    "SELECT * FROM execution_reservations WHERE intent_id = ?",
                    (intent_id,),
                ).fetchone()
                if reservation is None or reservation["state"] != "HELD":
                    raise ReservationConflictError(
                        "effect projection requires a held reservation to release"
                    )
                connection.execute(
                    """
                    UPDATE execution_reservations
                       SET state = 'RELEASED', release_reason = ?, updated_utc = ?
                     WHERE intent_id = ? AND state = 'HELD'
                    """,
                    (release_reservation_reason, now, intent_id),
                )
            updated = connection.execute(
                """
                UPDATE execution_intents
                   SET applied_utc = ?, updated_utc = ?
                 WHERE intent_id = ?
                   AND outcome_revision = ?
                   AND applied_utc IS NULL
                """,
                (now, now, intent_id, outcome_revision),
            )
            if updated.rowcount != 1:
                raise ProjectionConflictError("outcome changed while applying its projection")
            return True

    def list_projections(self, *, projection_key: str | None = None) -> list[EffectProjection]:
        if projection_key is not None and (
            not isinstance(projection_key, str) or not projection_key
        ):
            raise ValueError("projection_key must be a non-empty string or null")
        connection = self._connect()
        try:
            if projection_key is None:
                rows = connection.execute(
                    """
                    SELECT * FROM execution_effect_projections
                     ORDER BY created_utc, intent_id, outcome_revision
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM execution_effect_projections
                     WHERE projection_key = ?
                     ORDER BY created_utc, intent_id, outcome_revision
                    """,
                    (projection_key,),
                ).fetchall()
        finally:
            connection.close()
        return [
            EffectProjection(
                intent_id=row["intent_id"],
                outcome_revision=int(row["outcome_revision"]),
                projection_key=row["projection_key"],
                projection=json.loads(row["projection_json"]),
                event_id=row["event_id"],
                outcome=json.loads(row["outcome_json"]),
                created_utc=row["created_utc"],
            )
            for row in rows
        ]

    def prepare_retry(
        self,
        request: BrokerRequest,
        *,
        previous_attempt_id: str,
        reason: str,
        retryable_retcodes: set[int] | frozenset[int],
    ) -> IntentRecord:
        """Move one known rejection back to PREPARED under an explicit policy."""
        request_json = _canonical_json(request.to_dict())
        identity_json = _canonical_json(request.intent_key.to_dict())
        payload_json = _canonical_json(dict(request.payload))
        if not isinstance(previous_attempt_id, str) or not previous_attempt_id:
            raise ValueError("previous_attempt_id must be a non-empty string")
        if not isinstance(reason, str) or not reason or len(reason) > 1024:
            raise ValueError("reason must be a non-empty string of at most 1024 characters")
        if not isinstance(retryable_retcodes, (set, frozenset)) or not retryable_retcodes:
            raise ValueError("retryable_retcodes must be a non-empty set")
        if any(type(value) is not int for value in retryable_retcodes):
            raise ValueError("retryable_retcodes must contain integers")
        now = _utc_now()
        with self._write() as connection:
            intent = connection.execute(
                "SELECT * FROM execution_intents WHERE intent_id = ?",
                (request.intent_id,),
            ).fetchone()
            if intent is None:
                raise KeyError(request.intent_id)
            if (
                intent["identity_json"] != identity_json
                or intent["payload_json"] != payload_json
            ):
                raise IntentConflictError("retry changed intent identity or payload")

            existing = connection.execute(
                """
                SELECT * FROM execution_retries
                 WHERE attempt_id = ? OR request_id = ?
                """,
                (request.attempt_id, request.request_id),
            ).fetchone()
            if existing is not None:
                if (
                    existing["attempt_id"] != request.attempt_id
                    or existing["request_id"] != request.request_id
                    or existing["intent_id"] != request.intent_id
                    or existing["previous_attempt_id"] != previous_attempt_id
                    or existing["action_id"] != request.action_id
                    or existing["reason"] != reason
                    or existing["request_json"] != request_json
                ):
                    raise IntentConflictError("retry identity was reused with different content")
                if IntentState(intent["state"]) is not IntentState.PREPARED:
                    raise RetryNotAllowedError("durable retry no longer awaits dispatch")
                return _record(intent)

            if IntentState(intent["state"]) is not IntentState.REJECTED:
                raise RetryNotAllowedError(
                    f"only a known rejection may be retried, not {intent['state']}"
                )
            if intent["attempt_id"] != previous_attempt_id:
                raise IntentConflictError("retry references a stale rejected attempt")
            outcome = json.loads(intent["outcome_json"] or "{}")
            retcode = outcome.get("retcode")
            if type(retcode) is not int or retcode not in retryable_retcodes:
                raise RetryNotAllowedError("rejection retcode is not retryable by this policy")
            previous = connection.execute(
                """
                SELECT * FROM execution_attempts
                 WHERE attempt_id = ? AND intent_id = ?
                """,
                (previous_attempt_id, request.intent_id),
            ).fetchone()
            if previous is None:
                raise IntentConflictError("rejected attempt was not found")
            previous_request = BrokerRequest.from_dict(json.loads(previous["request_json"]))
            if request.attempt_id == previous_attempt_id or request.request_id == previous_request.request_id:
                raise IntentConflictError("retry must use new request and attempt identities")
            if request.action_id != previous_request.action_id:
                raise IntentConflictError("retry must preserve the logical action identity")
            try:
                connection.execute(
                    """
                    INSERT INTO execution_retries (
                        attempt_id, request_id, intent_id, previous_attempt_id,
                        action_id, reason, rejected_retcode, request_json, created_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request.attempt_id,
                        request.request_id,
                        request.intent_id,
                        previous_attempt_id,
                        request.action_id,
                        reason,
                        retcode,
                        request_json,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise IntentConflictError("retry request or attempt identity already exists") from exc
            connection.execute(
                """
                UPDATE execution_intents
                   SET state = ?, attempt_id = NULL, outcome_json = NULL,
                       applied_utc = NULL, updated_utc = ?
                 WHERE intent_id = ?
                """,
                (IntentState.PREPARED.value, now, request.intent_id),
            )
            row = connection.execute(
                "SELECT * FROM execution_intents WHERE intent_id = ?",
                (request.intent_id,),
            ).fetchone()
            return _record(row)

    def list_unresolved(self) -> list[IntentRecord]:
        unresolved = (
            IntentState.PREPARED.value,
            IntentState.DISPATCHING.value,
            IntentState.PLACED.value,
            IntentState.DONE_PARTIAL.value,
            IntentState.UNKNOWN.value,
        )
        placeholders = ",".join("?" for _ in unresolved)
        connection = self._connect()
        try:
            rows = connection.execute(
                f"SELECT * FROM execution_intents WHERE state IN ({placeholders}) ORDER BY created_utc",
                unresolved,
            ).fetchall()
        finally:
            connection.close()
        return [_record(row) for row in rows]

    def list_current_intents(self) -> list[CurrentIntentSnapshot]:
        """Return identity and current state from one consistent ledger read."""
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT i.*
                  FROM execution_intents AS i
                  LEFT JOIN execution_reservations AS r
                    ON r.intent_id = i.intent_id
                 WHERE r.state IS NULL OR r.state <> 'SUPERSEDED'
                 ORDER BY i.created_utc, i.intent_id
                """
            ).fetchall()
        finally:
            connection.close()
        return [
            CurrentIntentSnapshot(
                IntentKey.from_dict(json.loads(row["identity_json"])),
                _record(row),
            )
            for row in rows
        ]

    def list_unprojected_terminal(self) -> list[IntentRecord]:
        """Return terminal outcomes that still lack a reconstructible effect."""
        terminal = (IntentState.DONE.value, IntentState.REJECTED.value)
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT *
                  FROM execution_intents
                 WHERE state IN (?, ?)
                   AND outcome_json IS NOT NULL
                   AND applied_utc IS NULL
                 ORDER BY created_utc, intent_id
                """,
                terminal,
            ).fetchall()
        finally:
            connection.close()
        return [_record(row) for row in rows]

    def get_reservation(self, intent_id: str) -> IntentReservation:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM execution_reservations WHERE intent_id = ?",
                (intent_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise KeyError(intent_id)
        return _reservation(row)

    def list_reservations(self, *, state: str | None = None) -> list[IntentReservation]:
        if state is not None and state not in {"HELD", "SUPERSEDED", "RELEASED"}:
            raise ValueError("invalid reservation state")
        connection = self._connect()
        try:
            if state is None:
                rows = connection.execute(
                    "SELECT * FROM execution_reservations ORDER BY reservation_id"
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM execution_reservations
                     WHERE state = ? ORDER BY reservation_id
                    """,
                    (state,),
                ).fetchall()
        finally:
            connection.close()
        return [_reservation(row) for row in rows]

    def release_reservation(
        self,
        intent_id: str,
        *,
        outcome_revision: int,
        reason: str,
    ) -> bool:
        if not isinstance(reason, str) or not reason or len(reason) > 1024:
            raise ValueError("reason must be a non-empty string of at most 1024 characters")
        if (
            not isinstance(outcome_revision, int)
            or isinstance(outcome_revision, bool)
            or outcome_revision <= 0
        ):
            raise ValueError("outcome_revision must be a positive integer")
        now = _utc_now()
        with self._write() as connection:
            intent = connection.execute(
                "SELECT * FROM execution_intents WHERE intent_id = ?",
                (intent_id,),
            ).fetchone()
            if intent is None:
                raise KeyError(intent_id)
            reservation = connection.execute(
                "SELECT * FROM execution_reservations WHERE intent_id = ?",
                (intent_id,),
            ).fetchone()
            if reservation is None:
                raise KeyError(intent_id)
            if reservation["state"] == "RELEASED":
                if reservation["release_reason"] != reason:
                    raise ReservationConflictError(
                        "reservation release was redelivered with a different reason"
                    )
                return False
            if reservation["state"] != "HELD":
                raise ReservationConflictError(
                    f"reservation is {reservation['state']}, not releasable"
                )
            intent_state = IntentState(intent["state"])
            if intent_state not in {IntentState.DONE, IntentState.REJECTED}:
                raise ReservationConflictError(
                    f"reservation cannot be released from {intent_state.value}"
                )
            if intent["outcome_revision"] != outcome_revision:
                raise ReservationConflictError("reservation release targets a stale outcome")
            connection.execute(
                """
                UPDATE execution_reservations
                   SET state = 'RELEASED', release_reason = ?, updated_utc = ?
                 WHERE intent_id = ? AND state = 'HELD'
                """,
                (reason, now, intent_id),
            )
            return True


def execute_once(
    store: IntentStore,
    request: BrokerRequest,
    broker_call: Callable[[Mapping[str, Any]], Mapping[str, Any] | None],
    *,
    preflight: Callable[[], None] | None = None,
    worker_session_id: str | None = None,
    worker_pid: int | None = None,
) -> IntentRecord:
    try:
        record = store.prepare(request)
    except (IntentConflictError, DispatchNotAllowedError):
        raise
    except Exception as exc:
        raise PreDispatchError(exc) from exc
    if record.state is not IntentState.PREPARED:
        return record
    if preflight is not None:
        try:
            preflight()
        except Exception:
            return store.get(request.intent_id)
    try:
        store.begin_dispatch(
            request,
            worker_session_id=worker_session_id,
            worker_pid=worker_pid,
        )
    except DispatchNotAllowedError:
        return store.get(request.intent_id)
    except IntentConflictError:
        raise
    except Exception as exc:
        raise PreDispatchError(exc) from exc
    try:
        result = broker_call(request.to_dict())
    except Exception as exc:
        outcome = BrokerOutcome(
            state=IntentState.UNKNOWN,
            error=f"broker_exception:{type(exc).__name__}",
        )
    else:
        outcome = classify_broker_result(result)
    try:
        return store.record_outcome(request, outcome)
    except sqlite3.Error as exc:
        raise OutcomePersistenceError(outcome, exc) from exc


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _validate_atomic_projection_options(
    projection_key: str | None,
    release_reservation_reason: str | None,
) -> None:
    if projection_key is not None and (
        not isinstance(projection_key, str) or not projection_key
    ):
        raise ValueError("projection_key must be a non-empty string or null")
    if release_reservation_reason is not None:
        if projection_key is None:
            raise ValueError("reservation release requires an atomic projection")
        if (
            not isinstance(release_reservation_reason, str)
            or not release_reservation_reason
            or len(release_reservation_reason) > 1024
        ):
            raise ValueError(
                "release_reservation_reason must be a non-empty string"
            )


def _effect_projection(
    request: BrokerRequest,
    outcome: BrokerOutcome,
    outcome_revision: int,
) -> dict[str, Any]:
    key = request.intent_key
    return {
        "intent_id": request.intent_id,
        "outcome_revision": outcome_revision,
        "state": outcome.state.value,
        "channel": key.channel,
        "signal_root": key.signal_root,
        "generation": key.generation,
        "leg": key.leg,
        "operation": key.operation,
        "policy_revision": key.revision,
        "request_payload": dict(request.payload),
        "outcome": outcome.to_dict(),
    }


def _apply_effect_projection_in_transaction(
    connection: sqlite3.Connection,
    intent: sqlite3.Row,
    *,
    projection_key: str,
    projection: Mapping[str, Any],
    event_id: str,
    release_reservation_reason: str | None,
    now: str,
) -> None:
    """Insert the effect while the outcome transaction is still open."""
    intent_id = str(intent["intent_id"])
    outcome_revision = int(intent["outcome_revision"])
    if IntentState(intent["state"]) in {
        IntentState.PREPARED,
        IntentState.DISPATCHING,
    }:
        raise DispatchNotAllowedError("cannot project an unresolved dispatch")
    if not intent["outcome_json"]:
        raise ProjectionConflictError("projection requires a durable broker outcome")
    if intent["applied_utc"] is not None:
        raise ProjectionConflictError(
            "outcome was acknowledged before its atomic effect projection"
        )
    projection_json = _canonical_json(dict(projection))
    try:
        connection.execute(
            """
            INSERT INTO execution_effect_projections (
                intent_id, outcome_revision, projection_key, projection_json,
                event_id, outcome_json, created_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                intent_id,
                outcome_revision,
                projection_key,
                projection_json,
                event_id,
                intent["outcome_json"],
                now,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise ProjectionConflictError("projection identity already exists") from exc

    if release_reservation_reason is not None:
        if IntentState(intent["state"]) not in {
            IntentState.DONE,
            IntentState.REJECTED,
        }:
            raise ReservationConflictError(
                f"reservation cannot be released from {intent['state']}"
            )
        reservation = connection.execute(
            "SELECT * FROM execution_reservations WHERE intent_id = ?",
            (intent_id,),
        ).fetchone()
        if reservation is None or reservation["state"] != "HELD":
            raise ReservationConflictError(
                "effect projection requires a held reservation to release"
            )
        connection.execute(
            """
            UPDATE execution_reservations
               SET state = 'RELEASED', release_reason = ?, updated_utc = ?
             WHERE intent_id = ? AND state = 'HELD'
            """,
            (release_reservation_reason, now, intent_id),
        )

    updated = connection.execute(
        """
        UPDATE execution_intents
           SET applied_utc = ?, updated_utc = ?
         WHERE intent_id = ?
           AND outcome_revision = ?
           AND applied_utc IS NULL
        """,
        (now, now, intent_id, outcome_revision),
    )
    if updated.rowcount != 1:
        raise ProjectionConflictError(
            "outcome changed while applying its atomic projection"
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record(row: sqlite3.Row) -> IntentRecord:
    return IntentRecord(
        intent_id=row["intent_id"],
        state=IntentState(row["state"]),
        attempt_id=row["attempt_id"],
        payload=json.loads(row["payload_json"]),
        outcome=json.loads(row["outcome_json"]) if row["outcome_json"] else None,
        outcome_revision=int(row["outcome_revision"]),
        applied_utc=row["applied_utc"],
    )


def _reservation(row: sqlite3.Row) -> IntentReservation:
    return IntentReservation(
        reservation_key=row["reservation_key"],
        intent_id=row["intent_id"],
        policy_revision=int(row["policy_revision"]),
        payload=json.loads(row["payload_json"]),
        state=row["state"],
        expires_utc=row["expires_utc"],
        superseded_by_intent_id=row["superseded_by_intent_id"],
        release_reason=row["release_reason"],
    )


def _parse_utc(value: str | None, name: str, *, optional: bool) -> datetime | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be an ISO-8601 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{name} must include the UTC timezone")
    return parsed


def _check_pending_execution_barrier(connection: sqlite3.Connection, key: IntentKey) -> None:
    scope_values = tuple(getattr(key, name) for name in _PENDING_SCOPE_FIELDS)
    ambiguous = connection.execute(
        "SELECT state FROM execution_intents WHERE " + _PENDING_SCOPE_WHERE
        + " AND state IN ('DISPATCHING', 'UNKNOWN', 'PLACED', 'DONE_PARTIAL') AND intent_id != ? LIMIT 1",
        (*scope_values, key.intent_id)).fetchone()
    if ambiguous is None and key.operation == "MODIFY_SLTP":
        # TP prerequisites retain their existing peer identity/reservation. A
        # different SL/TP action must not route around their uncertain outcome.
        prefix = key.leg + "-tp-prerequisite-r"
        fields = _PENDING_SCOPE_FIELDS[:4]
        where = " AND ".join(f"json_extract(identity_json, '$.{name}') = ?" for name in fields)
        ambiguous = connection.execute(
            "SELECT state FROM execution_intents WHERE " + where
            + " AND json_extract(identity_json, '$.leg') >= ? AND json_extract(identity_json, '$.leg') < ?"
            + " AND json_extract(identity_json, '$.operation') = 'MODIFY_SLTP'"
            + " AND state IN ('DISPATCHING', 'UNKNOWN', 'PLACED', 'DONE_PARTIAL') LIMIT 1",
            (*(getattr(key, name) for name in fields), prefix, key.leg + "-tp-prerequisite-s")).fetchone()
    if ambiguous is not None:
        raise PendingRevisionBlockedError(f"prior pending execution is {ambiguous['state']}")


def _same_reservation_lineage(first: IntentKey, second: IntentKey) -> bool:
    return (
        first.account_fingerprint == second.account_fingerprint
        and first.channel == second.channel
        and first.signal_root == second.signal_root
        and first.generation == second.generation
        and first.leg == second.leg
        and first.operation == second.operation
    )


def _positive_broker_id(value) -> bool:
    return type(value) is int and 0 < value < 2**63


def _check_entry_claim_owner(connection, request, outcome):
    if request.intent_key.operation != "OPEN_MARKET":
        return
    for kind, broker_id in (("order", outcome.order), ("deal", outcome.deal)):
        if not _positive_broker_id(broker_id):
            continue
        owner = connection.execute("""
            SELECT intent_id, attempt_id FROM execution_broker_entry_claims
             WHERE account_fingerprint = ? AND kind = ? AND broker_id = ?
        """, (request.intent_key.account_fingerprint, kind, broker_id)).fetchone()
        if owner is not None and (owner["intent_id"], owner["attempt_id"]) != (
            request.intent_id, request.attempt_id
        ):
            # Native responses use the existing retained-outcome persistence path.
            raise sqlite3.IntegrityError("broker entry already attributed to another attempt")


def _match_entry_history(attempt, request, history):
    from entry_history_reconciliation import match_market_entry

    if attempt["preparation_json"] is None:
        raise ReconciliationConflictError("missing durable entry preparation")
    preparation = TradePreparation.from_dict(json.loads(attempt["preparation_json"]))
    _validate_dispatch_preparation(
        preparation, request, worker_session_id=attempt["worker_session_id"],
        worker_pid=attempt["worker_pid"], prepared_payload_json=attempt["prepared_payload_json"],
    )
    match = match_market_entry(
        request, preparation, deals=history["deals"], orders=history["orders"],
        date_from_msc=history["date_from_msc"], date_to_msc=history["date_to_msc"],
        account_fingerprint=history["account_fingerprint"],
    )
    return match, preparation


def _claim_entry_history(connection, request, preparation, match):
    account = request.intent_key.account_fingerprint
    # Confirmed native results predate this attribution table and remain owners.
    owner = connection.execute("""
        SELECT a.attempt_id FROM execution_attempts AS a
          JOIN execution_intents AS i ON i.intent_id = a.intent_id
         WHERE json_extract(i.identity_json, '$.account_fingerprint') = ?
           AND json_extract(i.identity_json, '$.operation') = 'OPEN_MARKET'
           AND a.attempt_id <> ?
           AND (json_extract(a.outcome_json, '$.order') = ?
                OR json_extract(a.outcome_json, '$.deal') IN (SELECT value FROM json_each(?)))
         LIMIT 1
    """, (account, request.attempt_id, match.order, _canonical_json(list(match.deal_ids)))).fetchone()
    if owner is not None:
        raise ReconciliationConflictError("broker entry already attributed by another native outcome")

    # Consider all sent attempts, including superseded intents. Missing legacy
    # ownership fields cannot be used to exclude a possible competing attempt.
    fields = {}
    for name in ("symbol", "magic", "comment"):
        fields[name] = (f"COALESCE(json_extract(a.prepared_payload_json, '$.{name}'), "
                        f"json_extract(a.request_json, '$.payload.{name}'), "
                        f"json_extract(i.payload_json, '$.{name}'))")
    fields["type"] = ("COALESCE(json_extract(a.prepared_payload_json, '$.type'), "
                      "CASE COALESCE(json_extract(a.request_json, '$.payload.direction'), "
                      "json_extract(i.payload_json, '$.direction')) "
                      "WHEN 'BUY' THEN 0 WHEN 'SELL' THEN 1 END)")
    filters = " AND ".join(f"({expression} IS NULL OR {expression} = ?)"
                           for expression in fields.values())
    native = preparation.native_request
    competitors = connection.execute("""
        SELECT a.* FROM execution_attempts AS a
          JOIN execution_intents AS i ON i.intent_id = a.intent_id
         WHERE json_extract(i.identity_json, '$.account_fingerprint') = ?
           AND json_extract(i.identity_json, '$.operation') = 'OPEN_MARKET'
           AND a.attempt_id <> ? AND a.state <> 'REJECTED'
           AND COALESCE(json_extract(a.outcome_json, '$.order'), 0) <= 0
           AND """ + filters + " LIMIT 257",
        (account, request.attempt_id, *(native[name] for name in fields)),
    ).fetchall()
    if len(competitors) > 256:
        raise ReconciliationConflictError("competing entry attribution budget exceeded")
    for competitor in competitors:
        anchor = None
        if competitor["preparation_json"] is not None:
            other = TradePreparation.from_dict(json.loads(competitor["preparation_json"]))
            other_request = BrokerRequest.from_dict(json.loads(competitor["request_json"]))
            _validate_attempt_request(competitor, other_request)
            _validate_dispatch_preparation(
                other, other_request, worker_session_id=competitor["worker_session_id"],
                worker_pid=competitor["worker_pid"],
                prepared_payload_json=competitor["prepared_payload_json"],
            )
            tick = other.evidence.get("source_tick")
            anchor = tick.get("time_msc") if isinstance(tick, Mapping) else None
        if type(anchor) is not int or anchor <= match.last_fill_msc:
            raise ReconciliationConflictError("competing unresolved entry could own the historical fill")
    for kind, broker_id in (("order", match.order), ("position", match.position_id),
                           *(("deal", deal) for deal in match.deal_ids)):
        if not _positive_broker_id(broker_id):
            raise ReconciliationConflictError("broker entry identity out of durable range")
        try:
            connection.execute("""
                INSERT INTO execution_broker_entry_claims
                    (account_fingerprint, kind, broker_id, intent_id, attempt_id)
                VALUES (?, ?, ?, ?, ?)
            """, (account, kind, broker_id, request.intent_id, request.attempt_id))
        except sqlite3.IntegrityError as exc:
            raise ReconciliationConflictError("broker entry already attributed") from exc


def _validate_reconciliation_progress(
    previous: BrokerOutcome,
    current: BrokerOutcome,
) -> None:
    if _positive_broker_id(previous.order) and current.order != previous.order:
        raise ReconciliationConflictError("reconciliation changed the broker order identity")
    if _positive_broker_id(previous.deal) and current.deal != previous.deal:
        raise ReconciliationConflictError("reconciliation changed the broker deal identity")
    if (
        previous.filled_volume is not None
        and (current.filled_volume is None or current.filled_volume < previous.filled_volume)
    ):
        raise ReconciliationConflictError("reconciliation reduced confirmed filled volume")
    allowed = {
        IntentState.UNKNOWN: {
            IntentState.PLACED,
            IntentState.DONE_PARTIAL,
            IntentState.DONE,
            IntentState.REJECTED,
        },
        IntentState.PLACED: {
            IntentState.DONE_PARTIAL,
            IntentState.DONE,
            IntentState.REJECTED,
        },
        IntentState.DONE_PARTIAL: {
            IntentState.DONE_PARTIAL,
            IntentState.DONE,
        },
    }
    if current.state not in allowed.get(previous.state, set()):
        raise ReconciliationConflictError(
            f"invalid reconciliation transition {previous.state.value}->{current.state.value}"
        )
    if current.state is previous.state and current.to_dict() == previous.to_dict():
        raise ReconciliationConflictError("reconciliation did not add new broker evidence")


def _ensure_column(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    existing = {
        row[1]
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in existing:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _migrate_execution_admissions(connection: sqlite3.Connection) -> None:
    meta = connection.execute(
        "SELECT value FROM execution_intent_meta WHERE key = 'schema_version'"
    ).fetchone()
    version = None if meta is None else str(meta["value"])
    if version not in {None, "2", "3", "4", "5"}:
        raise RuntimeError(f"unsupported execution intent schema version: {version}")

    connection.execute("BEGIN IMMEDIATE")
    try:
        attempts = connection.execute(
            "SELECT * FROM execution_attempts ORDER BY dispatched_utc, attempt_id"
        ).fetchall()
        for attempt in attempts:
            raw_request = attempt["request_json"]
            if not all(
                isinstance(attempt[name], str) and attempt[name]
                for name in ("request_id", "action_id", "request_json")
            ):
                raise RuntimeError(
                    f"attempt {attempt['attempt_id']} cannot be migrated without durable request identity"
                )
            try:
                request = BrokerRequest.from_dict(json.loads(raw_request))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"attempt {attempt['attempt_id']} contains an invalid durable request"
                ) from exc
            if (
                request.request_id != attempt["request_id"]
                or request.attempt_id != attempt["attempt_id"]
                or request.intent_id != attempt["intent_id"]
                or request.action_id != attempt["action_id"]
                or _canonical_json(request.to_dict()) != raw_request
            ):
                raise RuntimeError(
                    f"attempt {attempt['attempt_id']} request identity is inconsistent"
                )

            try:
                state = IntentState(attempt["state"])
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"attempt {attempt['attempt_id']} contains an invalid state"
                ) from exc
            error = None
            if attempt["outcome_json"]:
                try:
                    outcome = BrokerOutcome.from_dict(json.loads(attempt["outcome_json"]))
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        f"attempt {attempt['attempt_id']} contains an invalid outcome"
                    ) from exc
                if outcome.state is not state:
                    raise RuntimeError(
                        f"attempt {attempt['attempt_id']} outcome state is inconsistent"
                    )
                error = outcome.error

            existing = connection.execute(
                """
                SELECT * FROM execution_admissions
                 WHERE request_id = ? OR attempt_id = ?
                """,
                (request.request_id, request.attempt_id),
            ).fetchone()
            if existing is not None:
                _validate_admission_request(
                    existing,
                    request,
                    worker_session_id=attempt["worker_session_id"],
                )
                if existing["state"] != state.value:
                    raise RuntimeError(
                        f"attempt {attempt['attempt_id']} admission state is inconsistent"
                    )
                continue

            connection.execute(
                """
                INSERT INTO execution_admissions (
                    request_id, attempt_id, intent_id, action_id,
                    request_json, worker_session_id, state, admitted_utc,
                    completed_utc, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.request_id,
                    request.attempt_id,
                    request.intent_id,
                    request.action_id,
                    raw_request,
                    attempt["worker_session_id"],
                    state.value,
                    attempt["dispatched_utc"],
                    attempt["completed_utc"],
                    error,
                ),
            )

        connection.execute(
            """
            UPDATE execution_intents
               SET outcome_revision = 1
             WHERE outcome_json IS NOT NULL AND outcome_revision = 0
            """
        )
        connection.execute(
            """
            INSERT INTO execution_intent_meta(key, value) VALUES('schema_version', '5')
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """
        )
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise


_WORKER_SESSION_UNCHECKED = object()


def _validate_worker_session(worker_session_id: str | None) -> None:
    if worker_session_id is not None and (
        not isinstance(worker_session_id, str) or not worker_session_id
    ):
        raise ValueError("worker_session_id must be a non-empty string or null")


def _validate_admission_request(
    row: sqlite3.Row | None,
    request: BrokerRequest,
    *,
    worker_session_id: str | None | object = _WORKER_SESSION_UNCHECKED,
) -> None:
    if row is None:
        raise IntentConflictError("durable request admission was not found")
    request_json = _canonical_json(request.to_dict())
    if (
        row["request_id"] != request.request_id
        or row["attempt_id"] != request.attempt_id
        or row["intent_id"] != request.intent_id
        or row["action_id"] != request.action_id
        or row["request_json"] != request_json
    ):
        raise IntentConflictError("request does not match durable admission")
    if (
        worker_session_id is not _WORKER_SESSION_UNCHECKED
        and row["worker_session_id"] != worker_session_id
    ):
        raise IntentConflictError("worker session does not match durable admission")


def _validate_dispatch_preparation(
    preparation: TradePreparation,
    request: BrokerRequest,
    *,
    worker_session_id: str | None,
    worker_pid: int | None,
    prepared_payload_json: str | None,
) -> None:
    if not isinstance(preparation, TradePreparation):
        raise TypeError("preparation must be a TradePreparation")
    if (
        preparation.error is not None
        or preparation.request_id != request.request_id
        or preparation.intent_id != request.intent_id
        or preparation.attempt_id != request.attempt_id
        or preparation.action_id != request.action_id
        or preparation.worker_session_id != worker_session_id
        or preparation.worker_pid != worker_pid
        or _canonical_json(dict(preparation.native_request)) != prepared_payload_json
    ):
        raise IntentConflictError("preparation does not match durable dispatch")


def _validate_attempt_request(
    row: sqlite3.Row | None,
    request: BrokerRequest,
    *,
    worker_session_id: str | None | object = _WORKER_SESSION_UNCHECKED,
) -> None:
    if row is None:
        raise IntentConflictError("dispatched attempt was not found")
    request_json = _canonical_json(request.to_dict())
    if (
        row["request_id"] != request.request_id
        or row["action_id"] != request.action_id
        or row["request_json"] != request_json
    ):
        raise IntentConflictError("response request does not match dispatched request")
    if (
        worker_session_id is not _WORKER_SESSION_UNCHECKED
        and row["worker_session_id"] != worker_session_id
    ):
        raise IntentConflictError("response worker session does not match dispatched attempt")
