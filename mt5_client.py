"""Async, bounded client for the single isolated MT5 owner."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import os
from pathlib import Path
import sqlite3
import subprocess
import threading
import time
from typing import Any, Mapping
import uuid

from mt5_protocol import LookupState
from mt5_protocol import BrokerOutcome, BrokerRequest, IntentState
from mt5_read_protocol import (
    MAX_REQUEST_BYTES, ReadOperation, ReadRequest, ReadResponse,
    decode_message, encode_message, finite_number, integer,
)
from mt5_worker import WorkerConfig
from mt5_trade_protocol import ENTRY_OPERATIONS, TradePreparation, TradeResponse, validate_trade_request
from mt5_scheduling import (
    MAX_CONSECUTIVE_TRADES, POLICY_ID, admission_available, next_transport_kind,
)


class _DispatchDecision:
    """Linearize abandonment against authorization without locking disk or IPC."""

    def __init__(self):
        self._lock = threading.Lock()
        self._abandoned = threading.Event()
        self._authorized = False

    def set(self):
        with self._lock:
            self._abandoned.set()

    def is_set(self):
        return self._abandoned.is_set()

    def authorize(self, deadline, stopped):
        with self._lock:
            if self._authorized:
                raise RuntimeError("dispatch already authorized")
            if self._abandoned.is_set() or stopped.is_set() or time.monotonic() >= deadline:
                return False
            self._authorized = True
            return True


class _FramedConnection:
    def __init__(self, input_stream, output_stream):
        self._input = input_stream
        self._output = output_stream

    def send_bytes(self, payload: bytes) -> None:
        self._output.write(payload + b"\n")
        self._output.flush()

    def recv_bytes(self, maxlength: int) -> bytes:
        raw = self._input.readline(maxlength + 2)
        if not raw:
            raise EOFError("worker stream closed")
        if len(raw) > maxlength + 1 or not raw.endswith(b"\n"):
            raise ValueError("message_size_limit")
        return raw[:-1]

    def close(self) -> None:
        for stream in (self._output, self._input):
            try:
                stream.close()
            except (OSError, ValueError):
                pass


def _backend_factory_spec(factory) -> dict[str, Any]:
    kwargs: Mapping[str, Any] = {}
    target = factory
    if isinstance(factory, partial):
        if factory.args:
            raise ValueError("backend factory positional arguments are not supported")
        target = factory.func
        kwargs = factory.keywords or {}
    module = getattr(target, "__module__", None)
    qualname = getattr(target, "__qualname__", None)
    if (
        not isinstance(module, str)
        or not module
        or not isinstance(qualname, str)
        or not qualname
        or "<locals>" in qualname
    ):
        raise ValueError("backend factory must be an importable top-level callable")
    spec = {"module": module, "qualname": qualname, "kwargs": dict(kwargs)}
    # Validate serializability and size before creating a process.
    encode_message(spec, MAX_REQUEST_BYTES)
    return spec


def _process_alive(process) -> bool:
    if process is None:
        return False
    if hasattr(process, "poll"):
        return process.poll() is None
    return process.is_alive()


class MT5ReadClient:
    """One-use session. A timed-out read occupies capacity until drained or closed.

    The factory is mandatory: importing/constructing this object cannot connect
    a terminal. Production integration must explicitly choose native_backend.
    """

    def __init__(
        self,
        config: WorkerConfig,
        *,
        backend_factory,
        queue_size: int = 8,
        store_path: str | Path | None = None,
    ):
        integer(queue_size, "queue_size", 1, 128)
        if not callable(backend_factory):
            raise ValueError("an explicit backend factory is required")
        self.config, self._factory, self._capacity = config, backend_factory, queue_size
        self._factory_spec = _backend_factory_spec(backend_factory)
        if store_path is not None:
            from execution_intents import IntentStore

            self.store = IntentStore(store_path)
        else:
            self.store = None
        self.session_id = uuid.uuid4().hex
        self._initialize_request = ReadRequest(ReadOperation.INITIALIZE)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mt5-read-transport")
        self._control_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mt5-read-control")
        self._storage_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mt5-intent-store")
        self._process = self._connection = None
        self._stop = threading.Event()
        self._ownership_lock = threading.Lock()
        self._termination_lock = threading.Lock()
        self._inflight = {}
        self._admitted: set[str] = set()
        self._transport_condition = asyncio.Condition()
        self._transport_busy = False
        self._transport_started: float | None = None
        self._transport_kind: str | None = None
        self._transport_waiters = {"trade": 0, "management": 0, "read": 0}
        self._consecutive_trades = 0
        self._max_consecutive_trades = MAX_CONSECUTIVE_TRADES
        self._loop = None
        self._start_task = self._close_task = None
        self._spawn_future = None
        self._ready = self._closing = self._closed = False
        self.late_responses = 0
        self._retained_trade_outcomes: dict[
            str,
            tuple[BrokerRequest, BrokerOutcome, str | None, str | None],
        ] = {}

    @property
    def pending_count(self):
        return len(self._admitted)

    def transport_snapshot(self) -> dict[str, Any]:
        return {
            "active": self._transport_busy,
            "active_kind": self._transport_kind,
            "active_age_seconds": (
                max(0.0, time.monotonic() - self._transport_started)
                if self._transport_started is not None else 0.0
            ),
            "trade_waiters": self._transport_waiters["trade"] + self._transport_waiters["management"],
            "management_waiters": self._transport_waiters["management"],
            "entry_waiters": self._transport_waiters["trade"],
            "read_waiters": self._transport_waiters["read"],
            "consecutive_trades": self._consecutive_trades,
            "pending_count": self.pending_count,
            "capacity": self._capacity + 1,
            "regular_capacity": self._capacity,
            "scheduling_policy": POLICY_ID,
        }

    def _admit(self, request_id: str, *, management: bool = False) -> bool:
        if request_id in self._admitted:
            raise ValueError("duplicate active request_id")
        if not admission_available(len(self._admitted), self._capacity, management=management):
            return False
        self._admitted.add(request_id)
        return True

    def _withdraw(self, request_id: str) -> None:
        self._admitted.discard(request_id)

    async def _acquire_transport(self, kind: str, deadline: float) -> bool:
        if kind not in self._transport_waiters:
            raise ValueError("invalid transport kind")
        condition = self._transport_condition
        async with condition:
            self._transport_waiters[kind] += 1
            try:
                while True:
                    if self._stop.is_set() or time.monotonic() >= deadline:
                        return False
                    turn = next_transport_kind(self._transport_waiters, self._consecutive_trades,
                                               self._max_consecutive_trades)
                    if not self._transport_busy and kind == turn:
                        self._transport_busy = True
                        self._transport_kind = kind
                        self._transport_started = time.monotonic()
                        return True
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    try:
                        async with asyncio.timeout(remaining):
                            await condition.wait()
                    except asyncio.TimeoutError:
                        return False
            finally:
                self._transport_waiters[kind] -= 1

    async def _release_transport(self, kind: str) -> None:
        async with self._transport_condition:
            self._transport_busy = False
            self._transport_kind = None
            self._transport_started = None
            if kind in {"trade", "management"}:
                self._consecutive_trades += 1
            else:
                self._consecutive_trades = 0
            self._transport_condition.notify_all()

    async def _wake_transport_waiters(self) -> None:
        async with self._transport_condition:
            self._transport_condition.notify_all()

    @property
    def worker_pid(self):
        return self._process.pid if self._process is not None else None

    @property
    def is_alive(self):
        return _process_alive(self._process)

    @property
    def retained_trade_outcome_count(self):
        return len(self._retained_trade_outcomes)

    def _bind_loop(self):
        loop = asyncio.get_running_loop()
        if self._loop is not None and loop is not self._loop:
            raise RuntimeError("client belongs to another event loop")
        self._loop = loop
        return loop

    def _spawn(self):
        from mt5_worker_lifetime import current_identity, python_subprocess_spec

        entrypoint = Path(__file__).with_name("mt5_worker_entry.py")
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        executable, env = python_subprocess_spec()
        process = subprocess.Popen(
            [executable, "-u", str(entrypoint), str(os.getpid()), current_identity()],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=str(entrypoint.parent),
            bufsize=0,
            creationflags=creationflags,
            env=env,
        )
        connection = None
        try:
            if process.stdin is None or process.stdout is None:
                raise RuntimeError("worker pipes unavailable")
            connection = _FramedConnection(process.stdout, process.stdin)
            # Publish ownership before any pipe write can block. A concurrent
            # close can now terminate the child and release that write.
            with self._ownership_lock:
                self._process = process
                self._connection = connection
            if self._stop.is_set():
                return
            connection.send_bytes(encode_message({
                "type": "bootstrap",
                "session_id": self.session_id,
                "config": self.config.to_dict(),
                "backend": self._factory_spec,
            }, MAX_REQUEST_BYTES))
        except Exception:
            self._stop.set()
            self._terminate_process(process)
            if connection is not None:
                connection.close()
            raise

    async def start(self, *, timeout: float = 10.0) -> ReadResponse:
        finite_number(timeout, "timeout", positive=True)
        self._bind_loop()
        if self._closed or self._closing:
            raise RuntimeError("client is closed")
        if self._start_task is None:
            self._start_task = asyncio.create_task(self._start(time.monotonic() + timeout))
        try:
            async with asyncio.timeout(timeout):
                return await asyncio.shield(self._start_task)
        except asyncio.TimeoutError:
            return self._unknown(self._initialize_request, "startup_deadline_exceeded")
        except asyncio.CancelledError:
            # Do not orphan a spawn that is still running in its transport thread.
            await self.close()
            raise

    async def _start(self, deadline):
        request = self._initialize_request
        try:
            self._spawn_future = self._loop.run_in_executor(self._executor, self._spawn)
            async with asyncio.timeout(max(0, deadline - time.monotonic())):
                await asyncio.shield(self._spawn_future)
            response = await self._submit(request, deadline)
            exclusive_owner = self.config.owner_lock_path is not None or (
                self._factory_spec.get("module") == "mt5_worker"
                and self._factory_spec.get("qualname") == "native_backend"
            )
            if response.state is LookupState.FOUND and exclusive_owner and self.store is not None:
                recovery = self._loop.run_in_executor(
                    self._storage_executor,
                    partial(
                        self.store.recover_dispatching,
                        "worker_exited_before_restart",
                        account_fingerprint=f"{self.config.expected_server}/{self.config.expected_login}",
                    ),
                )
                async with asyncio.timeout(max(0, deadline - time.monotonic())):
                    await asyncio.shield(recovery)
            self._ready = response.state is LookupState.FOUND and not self._closing
            return response
        except asyncio.TimeoutError:
            return self._unknown(request, "startup_deadline_exceeded")
        except Exception as exc:
            return self._unknown(request, f"startup_failed:{type(exc).__name__}")

    async def read(self, request: ReadRequest, *, timeout: float = 1.0) -> ReadResponse:
        finite_number(timeout, "timeout", positive=True)
        self._bind_loop()
        if not isinstance(request, ReadRequest):
            raise TypeError("expected ReadRequest")
        if request.operation in {ReadOperation.INITIALIZE, ReadOperation.SHUTDOWN}:
            raise ValueError("use start/close for lifecycle operations")
        deadline = time.monotonic() + timeout
        if not self._ready or self._closing or self._closed:
            return self._unknown(request, "client_not_ready")
        return await self._submit(request, deadline)

    async def execute(
        self,
        request: BrokerRequest,
        *,
        reservation_key: str,
        policy_revision: int,
        timeout: float = 2.0,
        expires_utc: str | None = None,
        dispatch_guard=None,
        projection_key: str | None = None,
        release_on_terminal: bool = False,
    ):
        """Durably prepare, revalidate and dispatch one broker effect."""
        finite_number(timeout, "timeout", positive=True)
        validate_trade_request(request)
        self._bind_loop()
        if self.store is None:
            raise RuntimeError("trade execution requires a durable intent store")
        if not self._ready or self._closing or self._closed:
            raise RuntimeError("client is not ready for trade execution")
        if dispatch_guard is not None and not callable(dispatch_guard):
            raise TypeError("dispatch_guard must be callable")
        if projection_key is not None and (
            not isinstance(projection_key, str) or not projection_key
        ):
            raise ValueError("projection_key must be a non-empty string or null")
        if type(release_on_terminal) is not bool:
            raise TypeError("release_on_terminal must be a boolean")
        if release_on_terminal and projection_key is None:
            raise ValueError("reservation release requires an atomic projection")
        deadline = time.monotonic() + timeout
        transport_kind = "trade" if request.intent_key.operation in ENTRY_OPERATIONS else "management"
        if not self._admit(request.request_id, management=transport_kind == "management"):
            raise TimeoutError("trade capacity exhausted before admission")
        preparation = None
        try:
            preparation = self._loop.run_in_executor(
                self._storage_executor,
                partial(
                    self.store.prepare_reserved,
                    request,
                    reservation_key=reservation_key,
                    policy_revision=policy_revision,
                    expires_utc=expires_utc,
                ),
            )
            record = await asyncio.shield(preparation)
        except BaseException:
            # Cancelling an await does not stop its filesystem operation.
            # Retain admission until that operation drains, without dispatch.
            if preparation is not None and not preparation.done():
                def preparation_finished(done):
                    self._withdraw(request.request_id)
                    if not done.cancelled():
                        done.exception()

                preparation.add_done_callback(preparation_finished)
            else:
                self._withdraw(request.request_id)
            raise
        if record.state is not IntentState.PREPARED:
            self._withdraw(request.request_id)
            return record
        if (
            self._stop.is_set()
            or self._closing
            or self._closed
            or time.monotonic() >= deadline
        ):
            self._withdraw(request.request_id)
            return record
        try:
            acquired = await self._acquire_transport(transport_kind, deadline)
        except BaseException:
            self._withdraw(request.request_id)
            raise
        if not acquired:
            self._withdraw(request.request_id)
            return record
        abandoned = _DispatchDecision()
        try:
            future = self._loop.run_in_executor(
                self._executor,
                self._trade_exchange,
                request,
                reservation_key,
                policy_revision,
                expires_utc,
                deadline,
                dispatch_guard,
                abandoned,
                projection_key,
                release_on_terminal,
            )
        except BaseException:
            self._withdraw(request.request_id)
            await self._release_transport(transport_kind)
            raise
        self._inflight[request.request_id] = future

        def finished(done):
            self._inflight.pop(request.request_id, None)
            self._withdraw(request.request_id)
            self._loop.create_task(self._release_transport(transport_kind))
            if abandoned.is_set():
                self.late_responses += 1
            if not done.cancelled():
                done.exception()

        future.add_done_callback(finished)
        try:
            async with asyncio.timeout(max(0, deadline - time.monotonic())):
                return await asyncio.shield(future)
        except asyncio.TimeoutError:
            abandoned.set()
            return await self._loop.run_in_executor(self._storage_executor, self.store.get, request.intent_id)
        except asyncio.CancelledError:
            abandoned.set()
            raise

    async def recover_trade_outcomes(self) -> int:
        self._bind_loop()
        if self.store is None:
            return 0
        return await self._loop.run_in_executor(
            self._storage_executor,
            self._retry_retained_trade_outcomes,
        )

    async def _submit(self, request, deadline):
        if time.monotonic() >= deadline:
            return self._unknown(request, "deadline_before_admission")
        if self._stop.is_set():
            return self._unknown(request, "client_closed")
        if not self._admit(request.request_id):
            return self._unknown(request, "read_capacity_exhausted")
        try:
            acquired = await self._acquire_transport("read", deadline)
        except BaseException:
            self._withdraw(request.request_id)
            raise
        if not acquired:
            self._withdraw(request.request_id)
            return self._unknown(request, "deadline_before_dispatch")
        abandoned = threading.Event()
        try:
            future = self._loop.run_in_executor(
                self._executor,
                self._exchange,
                request,
                deadline,
                abandoned,
            )
        except BaseException:
            self._withdraw(request.request_id)
            await self._release_transport("read")
            raise
        self._inflight[request.request_id] = future

        def finished(done):
            self._inflight.pop(request.request_id, None)
            self._withdraw(request.request_id)
            self._loop.create_task(self._release_transport("read"))
            if abandoned.is_set():
                self.late_responses += 1
            if not done.cancelled():
                done.exception()

        future.add_done_callback(finished)
        try:
            async with asyncio.timeout(max(0, deadline - time.monotonic())):
                response = await asyncio.shield(future)
            if time.monotonic() >= deadline:
                return self._unknown(request, "response_arrived_after_deadline")
            return response
        except asyncio.TimeoutError:
            abandoned.set()
            return self._unknown(request, "read_deadline_exceeded")
        except asyncio.CancelledError:
            abandoned.set()
            raise

    def _exchange(self, request, deadline, abandoned):
        if self._stop.is_set() or abandoned.is_set() or time.monotonic() >= deadline:
            return self._unknown(request, "cancelled_or_expired_before_dispatch")
        if not self.is_alive:
            return self._unknown(request, "worker_unavailable")
        sent_at = time.monotonic()
        try:
            self._connection.send_bytes(encode_message({
                "request": request.to_dict(), "deadline": deadline, "session_id": self.session_id,
            }, MAX_REQUEST_BYTES))
            raw = self._connection.recv_bytes(self.config.max_response_bytes)
            received_at = time.monotonic()
            response = ReadResponse.from_dict(decode_message(raw, self.config.max_response_bytes))
            if (response.request_id != request.request_id or response.operation is not request.operation
                    or response.worker_session_id != self.session_id or response.worker_pid != self.worker_pid
                    or not sent_at <= response.started_monotonic <= response.completed_monotonic <= received_at):
                raise ValueError("response_identity_or_clock_mismatch")
            if abandoned.is_set() or received_at >= deadline:
                return self._unknown(request, "late_read_discarded")
            return response
        except (OSError, EOFError, ValueError, TypeError, KeyError):
            self._stop.set()
            return self._unknown(request, "read_transport_failed")

    def _trade_exchange(
        self,
        request,
        reservation_key,
        policy_revision,
        expires_utc,
        deadline,
        dispatch_guard,
        abandoned,
        projection_key,
        release_on_terminal,
    ):
        self._retry_retained_trade_outcomes()
        record = self.store.prepare_reserved(
            request,
            reservation_key=reservation_key,
            policy_revision=policy_revision,
            expires_utc=expires_utc,
        )
        if record.state is not IntentState.PREPARED:
            return record
        if self._stop.is_set() or abandoned.is_set() or time.monotonic() >= deadline:
            return record
        if not self.is_alive:
            return record
        dispatched = False
        try:
            self.store.admit(request, worker_session_id=self.session_id)
            self._connection.send_bytes(encode_message({
                "phase": "prepare",
                "request": request.to_dict(),
                "deadline": deadline,
                "session_id": self.session_id,
            }, MAX_REQUEST_BYTES))
            raw = self._connection.recv_bytes(self.config.max_response_bytes)
            preparation = TradePreparation.from_dict(
                decode_message(raw, self.config.max_response_bytes)
            )
            self._validate_trade_preparation(request, preparation)
            if preparation.error is not None:
                return self.store.record_predispatch_failure(
                    request,
                    preparation.error,
                    worker_session_id=self.session_id,
                )
            if abandoned.is_set() or time.monotonic() >= deadline:
                self._abort_prepared_trade(request.request_id)
                return self.store.record_predispatch_failure(
                    request,
                    "trade_deadline_before_durable_dispatch",
                    worker_session_id=self.session_id,
                )
            if dispatch_guard is not None:
                try:
                    dispatch_guard(request)
                except Exception as exc:
                    self._abort_prepared_trade(request.request_id)
                    return self.store.record_predispatch_failure(
                        request,
                        f"dispatch_guard:{type(exc).__name__}",
                        worker_session_id=self.session_id,
                    )
            if not abandoned.authorize(deadline, self._stop):
                self._abort_prepared_trade(request.request_id)
                return self.store.record_predispatch_failure(
                    request,
                    "trade_cancelled_or_expired_before_authorization",
                    worker_session_id=self.session_id,
                )
            self.store.begin_dispatch(
                request,
                worker_session_id=self.session_id,
                worker_pid=preparation.worker_pid,
                prepared_payload=dict(preparation.native_request),
                preparation=preparation,
            )
            dispatched = True
            self._connection.send_bytes(encode_message({
                "phase": "commit",
                "request_id": request.request_id,
                "deadline": deadline,
                "session_id": self.session_id,
            }, MAX_REQUEST_BYTES))
            raw = self._connection.recv_bytes(self.config.max_response_bytes)
            response = TradeResponse.from_dict(
                decode_message(raw, self.config.max_response_bytes)
            )
            self._validate_trade_response(request, preparation, response)
            release_reason = (
                "done_effect_projected"
                if release_on_terminal
                and response.outcome.state is IntentState.DONE
                else None
            )
            try:
                return self.store.record_outcome(
                    request,
                    response.outcome,
                    projection_key=projection_key,
                    release_reservation_reason=release_reason,
                )
            except sqlite3.Error as exc:
                from execution_intents import OutcomePersistenceError

                self._retained_trade_outcomes[request.request_id] = (
                    request,
                    response.outcome,
                    projection_key,
                    release_reason,
                )
                raise OutcomePersistenceError(response.outcome, exc) from exc
        except Exception as exc:
            from execution_intents import OutcomePersistenceError

            if isinstance(exc, OutcomePersistenceError):
                raise
            self._stop.set()
            if dispatched:
                outcome = BrokerOutcome(
                    IntentState.UNKNOWN,
                    error=f"trade_transport_failed:{type(exc).__name__}",
                )
                try:
                    return self.store.record_outcome(
                        request,
                        outcome,
                        projection_key=projection_key,
                    )
                except sqlite3.Error as storage_exc:
                    from execution_intents import OutcomePersistenceError

                    self._retained_trade_outcomes[request.request_id] = (
                        request,
                        outcome,
                        projection_key,
                        None,
                    )
                    raise OutcomePersistenceError(outcome, storage_exc) from storage_exc
            try:
                return self.store.record_predispatch_failure(
                    request,
                    f"trade_prepare_transport_failed:{type(exc).__name__}",
                    worker_session_id=self.session_id,
                )
            except Exception:
                return self.store.get(request.intent_id)

    def _abort_prepared_trade(self, request_id):
        self._connection.send_bytes(encode_message({
            "phase": "abort",
            "request_id": request_id,
            "session_id": self.session_id,
        }, MAX_REQUEST_BYTES))
        raw = self._connection.recv_bytes(self.config.max_response_bytes)
        response = decode_message(raw, self.config.max_response_bytes)
        if response != {"aborted": True}:
            raise ValueError("trade abort was not acknowledged")

    def _validate_trade_preparation(self, request, preparation):
        if (
            preparation.request_id != request.request_id
            or preparation.intent_id != request.intent_id
            or preparation.attempt_id != request.attempt_id
            or preparation.action_id != request.action_id
            or preparation.worker_session_id != self.session_id
            or preparation.worker_pid != self.worker_pid
        ):
            raise ValueError("trade preparation identity mismatch")

    def _validate_trade_response(self, request, preparation, response):
        if (
            response.request_id != request.request_id
            or response.intent_id != request.intent_id
            or response.attempt_id != request.attempt_id
            or response.action_id != request.action_id
            or response.worker_session_id != self.session_id
            or response.worker_pid != self.worker_pid
            or response.started_monotonic < preparation.prepared_monotonic
        ):
            raise ValueError("trade response identity or clock mismatch")

    def _retry_retained_trade_outcomes(self):
        persisted = 0
        for request_id, retained in list(self._retained_trade_outcomes.items()):
            request, outcome, projection_key, release_reason = retained
            self.store.record_outcome(
                request,
                outcome,
                projection_key=projection_key,
                release_reservation_reason=release_reason,
            )
            self._retained_trade_outcomes.pop(request_id, None)
            persisted += 1
        return persisted

    def _unknown(self, request, reason):
        now = time.monotonic()
        return ReadResponse(request.request_id, request.operation, LookupState.UNKNOWN,
                            self.session_id, None, now, now, time.time_ns(), error=reason)

    async def close(self):
        self._bind_loop()
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._close())
        try:
            await asyncio.shield(self._close_task)
        except asyncio.CancelledError:
            await asyncio.shield(self._close_task)
            raise

    async def _close(self):
        # A completed, idle session may shut down gracefully. During startup or
        # a native wait, make the control path able to terminate the child first.
        if (self._start_task is not None and self._start_task.done()
                and not self._admitted and self.is_alive and self._ready):
            self._stop.clear()
            await self._submit(ReadRequest(ReadOperation.SHUTDOWN), time.monotonic() + 0.5)
        self._ready = False
        self._stop.set()
        await self._wake_transport_waiters()
        try:
            await self._loop.run_in_executor(self._control_executor, self._stop_worker)
            if self._spawn_future is not None:
                # If close raced Popen itself, _spawn observes _stop immediately
                # after publishing the late child and performs no bootstrap write.
                await asyncio.gather(self._spawn_future, return_exceptions=True)
                await self._loop.run_in_executor(self._control_executor, self._stop_worker)
            if self._start_task is not None:
                await asyncio.gather(self._start_task, return_exceptions=True)
            if self._inflight:
                await asyncio.gather(*list(self._inflight.values()), return_exceptions=True)
            await self._loop.run_in_executor(self._control_executor, self._shutdown_transport)
        finally:
            self._control_executor.shutdown(wait=True, cancel_futures=False)
            self._closed = True

    def _shutdown_transport(self):
        self._executor.shutdown(wait=True, cancel_futures=False)
        self._storage_executor.shutdown(wait=True, cancel_futures=False)

    def _stop_worker(self):
        with self._ownership_lock:
            process = self._process
            connection = self._connection
        self._terminate_process(process)
        if connection is not None:
            connection.close()

    def _terminate_process(self, process):
        if process is not None and process.pid is not None:
            with self._termination_lock:
                if hasattr(process, "poll"):
                    try:
                        process.wait(timeout=0.2)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        try:
                            process.wait(timeout=2.0)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=2.0)
                    if process.poll() is None:
                        raise RuntimeError("read worker failed to stop")
                else:
                    process.join(timeout=0.2)
                    if process.is_alive():
                        process.terminate()
                        process.join(timeout=2.0)
                    if process.is_alive():
                        process.kill()
                        process.join(timeout=2.0)
                    if process.is_alive():
                        raise RuntimeError("read worker failed to stop")

    async def __aenter__(self):
        response = await self.start()
        if response.state is not LookupState.FOUND:
            await self.close()
            raise RuntimeError(f"read worker initialization failed: {response.error}")
        return self

    async def __aexit__(self, *_exc):
        await self.close()
