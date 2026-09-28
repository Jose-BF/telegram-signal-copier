"""Process-isolated transport for durable broker requests."""

from __future__ import annotations

import asyncio
import multiprocessing
from pathlib import Path
import queue
import threading
import time
from typing import Any, Callable, Mapping
import uuid

from execution_intents import (
    IntentConflictError,
    IntentRecord,
    IntentStore,
    OutcomePersistenceError,
    PreDispatchError,
    execute_once,
)
from mt5_protocol import BrokerOutcome, BrokerRequest, IntentState


class WorkerUnavailableError(RuntimeError):
    pass


class UnresolvedRequestError(RuntimeError):
    pass


class AdmissionTimeoutError(TimeoutError):
    pass


_IN_TRANSIT_RESPONSE_GRACE_SECONDS = 0.1


class ProcessBrokerGateway:
    def __init__(
        self,
        store_path: str | Path,
        broker_handler: Callable[[Mapping[str, Any]], Mapping[str, Any] | None],
        *,
        queue_size: int = 8,
        context_name: str = "spawn",
    ):
        if not isinstance(queue_size, int) or queue_size <= 0:
            raise ValueError("queue_size must be a positive integer")
        self.store_path = Path(store_path)
        self.store = IntentStore(self.store_path)
        self._handler = broker_handler
        self._context = multiprocessing.get_context(context_name)
        self._queue_size = queue_size
        self._requests = self._context.Queue(maxsize=queue_size)
        self._responses = self._context.Queue(maxsize=queue_size)
        self._process = None
        self._lock = threading.Lock()
        self._sync_admission = threading.BoundedSemaphore(queue_size)
        self._async_admission = None
        self._response_backlog: dict[str, dict[str, Any]] = {}
        self._pending_envelope: dict[str, Any] | None = None
        self._pending_request_id: str | None = None
        self._pending_intent_id: str | None = None
        self._pending_attempt_id: str | None = None
        self._pending_request: BrokerRequest | None = None
        self._worker_session_id = uuid.uuid4().hex
        self._closed = False

    @property
    def is_alive(self) -> bool:
        return self._process is not None and self._process.is_alive()

    @property
    def has_unresolved_request(self) -> bool:
        return self._pending_request_id is not None

    def start(self) -> None:
        with self._lock:
            self._start_locked()

    def _start_locked(self) -> None:
        if self._closed:
            raise WorkerUnavailableError("gateway is closed")
        if self.is_alive:
            return
        if self._process is not None:
            self._drain_late_responses(
                wait_timeout=(
                    _IN_TRANSIT_RESPONSE_GRACE_SECONDS
                    if self._pending_request_id is not None
                    else 0.0
                )
            )
            self._retry_retained_envelopes()
            if self._response_backlog:
                raise UnresolvedRequestError(
                    "retained worker outcomes must be persisted before restart"
                )
            self.store.recover_dispatching("worker_exited_before_restart")
            self._clear_pending()
            self._replace_queues()
        self._worker_session_id = uuid.uuid4().hex
        self._process = self._context.Process(
            target=_worker_main,
            args=(
                str(self.store_path),
                self._requests,
                self._responses,
                self._handler,
                self._worker_session_id,
            ),
            name="mt5-isolated-worker",
            daemon=True,
        )
        self._process.start()

    def call(self, request: BrokerRequest, *, timeout: float) -> IntentRecord:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        return self._call_until(request, time.monotonic() + timeout)

    def _call_until(self, request: BrokerRequest, deadline: float) -> IntentRecord:
        if not self._sync_admission.acquire(timeout=_remaining(deadline)):
            raise AdmissionTimeoutError("broker admission capacity timed out before dispatch")
        acquired = False
        try:
            acquired = self._lock.acquire(timeout=_remaining(deadline))
            if not acquired:
                raise AdmissionTimeoutError("broker admission lock timed out before dispatch")
            self._drain_late_responses()
            self._retry_pending_envelope()
            if self._pending_request_id is not None:
                raise UnresolvedRequestError(
                    f"request {self._pending_request_id} still has an unknown transport outcome"
                )
            record = self.store.prepare(request)
            if record.state is not IntentState.PREPARED:
                return record
            if _remaining(deadline) <= 0:
                raise AdmissionTimeoutError("broker admission timed out before dispatch")
            if not self.is_alive:
                raise WorkerUnavailableError("worker unavailable before dispatch")
            self.store.admit(
                request,
                worker_session_id=getattr(self, "_worker_session_id", None),
            )
            if _remaining(deadline) <= 0:
                self.store.record_predispatch_failure(
                    request,
                    "broker_admission_timed_out_before_dispatch",
                    worker_session_id=getattr(self, "_worker_session_id", None),
                )
                raise AdmissionTimeoutError("broker admission timed out before dispatch")
            try:
                self._requests.put(
                    request.to_dict(),
                    block=True,
                    timeout=min(_remaining(deadline), 0.25),
                )
            except queue.Full as exc:
                self.store.record_predispatch_failure(
                    request,
                    "worker_queue_full_before_dispatch",
                    worker_session_id=getattr(self, "_worker_session_id", None),
                )
                raise WorkerUnavailableError("worker queue full before dispatch") from exc
            self._pending_request_id = request.request_id
            self._pending_intent_id = request.intent_id
            self._pending_attempt_id = request.attempt_id
            self._pending_request = request
            envelope = self._wait_response(request.request_id, deadline)
            if envelope is None:
                return self._settle_after_wait(request.intent_id, request=request)
            record, resolved = self._process_envelope(envelope)
            if resolved:
                self._clear_pending()
            return record
        finally:
            if acquired:
                self._lock.release()
            self._sync_admission.release()

    async def call_async(self, request: BrokerRequest, *, timeout: float) -> IntentRecord:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if self._async_admission is None:
            self._async_admission = asyncio.Semaphore(self._queue_size)
        deadline = time.monotonic() + timeout
        try:
            async with asyncio.timeout(_remaining(deadline)):
                await self._async_admission.acquire()
        except TimeoutError as exc:
            raise AdmissionTimeoutError(
                "async broker admission capacity timed out before dispatch"
            ) from exc
        release_on_exit = True
        loop = asyncio.get_running_loop()
        started = loop.create_future()

        def run_call() -> IntentRecord:
            try:
                loop.call_soon_threadsafe(_mark_future_done, started)
            except RuntimeError:
                pass
            return self._call_until(request, deadline)

        def release_after_completion(completed: asyncio.Task) -> None:
            if not completed.cancelled():
                completed.exception()
            self._async_admission.release()

        try:
            remaining = _remaining(deadline)
            if remaining <= 0:
                raise AdmissionTimeoutError("async broker admission timed out before dispatch")
            worker = asyncio.create_task(asyncio.to_thread(run_call))
            try:
                async with asyncio.timeout(_remaining(deadline)):
                    await asyncio.shield(started)
            except asyncio.CancelledError:
                release_on_exit = False
                worker.add_done_callback(release_after_completion)
                raise
            except TimeoutError as exc:
                release_on_exit = False
                worker.add_done_callback(release_after_completion)
                raise AdmissionTimeoutError(
                    "async broker executor admission timed out before dispatch"
                ) from exc
            try:
                return await asyncio.shield(worker)
            except asyncio.CancelledError:
                release_on_exit = False
                worker.add_done_callback(release_after_completion)
                raise
        finally:
            if release_on_exit:
                self._async_admission.release()

    def await_late(self, *, timeout: float) -> IntentRecord:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        deadline = time.monotonic() + timeout
        acquired = self._lock.acquire(timeout=_remaining(deadline))
        if not acquired:
            raise AdmissionTimeoutError("late response lock timed out")
        try:
            if self._pending_request_id is None or self._pending_intent_id is None:
                raise UnresolvedRequestError("there is no unresolved request")
            request_id = self._pending_request_id
            intent_id = self._pending_intent_id
            if self._pending_envelope is not None:
                envelope = self._pending_envelope
            else:
                envelope = self._wait_response(request_id, deadline)
            if envelope is None:
                return self._settle_after_wait(
                    intent_id,
                    request=getattr(self, "_pending_request", None),
                )
            record, resolved = self._process_envelope(envelope)
            if resolved:
                self._clear_pending()
            return record
        finally:
            self._lock.release()

    async def await_late_async(self, *, timeout: float) -> IntentRecord:
        return await asyncio.to_thread(self.await_late, timeout=timeout)

    def recover_after_worker_loss(self) -> int:
        with self._lock:
            self._drain_late_responses(
                wait_timeout=(
                    _IN_TRANSIT_RESPONSE_GRACE_SECONDS
                    if self._pending_request_id is not None
                    else 0.0
                )
            )
            if self._process is not None and self._process.is_alive():
                self._process.terminate()
                self._process.join(timeout=2.0)
            self._drain_late_responses(
                wait_timeout=(
                    _IN_TRANSIT_RESPONSE_GRACE_SECONDS
                    if self._pending_request_id is not None
                    else 0.0
                )
            )
            self._retry_retained_envelopes()
            if self._response_backlog:
                raise UnresolvedRequestError(
                    "retained worker outcomes must be persisted before recovery"
                )
            recovered = self.store.recover_dispatching("worker_terminated_with_unresolved_dispatch")
            self._clear_pending()
            return recovered

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._drain_late_responses(
                wait_timeout=(
                    _IN_TRANSIT_RESPONSE_GRACE_SECONDS
                    if self._pending_request_id is not None
                    else 0.0
                )
            )
            self._retry_retained_envelopes()
            if self._response_backlog:
                raise UnresolvedRequestError(
                    "retained worker outcomes must be persisted before close"
                )
            if self._process is not None and self._process.is_alive():
                if self._pending_request_id is None:
                    try:
                        self._requests.put_nowait(None)
                    except queue.Full:
                        pass
                    self._process.join(timeout=1.0)
                if self._process.is_alive():
                    self._process.terminate()
                    self._process.join(timeout=2.0)
            self._drain_late_responses(
                wait_timeout=(
                    _IN_TRANSIT_RESPONSE_GRACE_SECONDS
                    if self._pending_request_id is not None
                    else 0.0
                )
            )
            self._retry_retained_envelopes()
            if self._response_backlog:
                raise UnresolvedRequestError(
                    "retained worker outcomes must be persisted before close"
                )
            if self._pending_request_id is not None:
                self.store.recover_dispatching("gateway_closed_with_unresolved_dispatch")
                self._clear_pending()
            for transport_queue in (self._requests, self._responses):
                transport_queue.close()
                transport_queue.join_thread()
            self._closed = True

    def _wait_response(self, request_id: str, deadline: float) -> dict[str, Any] | None:
        backlog = self._response_backlog.get(request_id)
        if backlog is not None and self._matches_pending(backlog):
            return backlog
        while _remaining(deadline) > 0:
            try:
                envelope = self._responses.get(
                    block=True,
                    timeout=_remaining(deadline),
                )
            except queue.Empty:
                return None
            _validate_envelope(envelope)
            envelope_request_id = str(envelope["request_id"])
            if envelope_request_id == request_id and self._matches_pending(envelope):
                return envelope
            _record, resolved = self._process_envelope(envelope)
            if resolved and self._matches_pending(envelope):
                self._clear_pending()
        return None

    def _process_envelope(
        self,
        envelope: Mapping[str, Any],
    ) -> tuple[IntentRecord, bool]:
        _validate_envelope(envelope)
        request_value = envelope.get("request")
        if not isinstance(request_value, Mapping):
            raise RuntimeError("worker response missing original request")
        request = BrokerRequest.from_dict(request_value)
        if (
            envelope.get("request_id") != request.request_id
            or envelope.get("intent_id") != request.intent_id
            or envelope.get("attempt_id") != request.attempt_id
        ):
            raise RuntimeError("worker response identity mismatch")
        retained = dict(envelope)
        existing = self._response_backlog.get(request.request_id)
        if existing is not None and existing != retained:
            raise IntentConflictError(
                "conflicting worker responses share the same request identity"
            )
        if existing is None:
            self._response_backlog[request.request_id] = retained
        else:
            retained = existing
        if self._matches_pending(envelope):
            self._pending_envelope = retained
        admission_state = self.store.validate_admitted_request(
            request,
            worker_session_id=envelope.get("worker_session_id"),
        )
        record = self.store.get(request.intent_id)
        if envelope.get("status") == "predispatch_error":
            if admission_state == "ADMITTED":
                try:
                    record = self.store.record_predispatch_failure(
                        request,
                        str(envelope.get("error") or "predispatch_error"),
                        worker_session_id=envelope.get("worker_session_id"),
                    )
                except Exception as exc:
                    if _is_storage_error(exc):
                        return self.store.get(request.intent_id), False
                    raise
            elif admission_state != "FAILED_PRE_DISPATCH":
                raise IntentConflictError(
                    f"pre-dispatch response conflicts with admission state {admission_state}"
                )
            self._release_envelope(request.request_id)
            return record, True
        self.store.validate_dispatched_request(
            request,
            worker_session_id=envelope.get("worker_session_id"),
        )
        outcome_value = envelope.get("outcome")
        if isinstance(outcome_value, Mapping):
            outcome = BrokerOutcome.from_dict(outcome_value)
            if record.state is IntentState.DISPATCHING:
                try:
                    record = self.store.record_outcome(request, outcome)
                except Exception as exc:
                    if _is_storage_error(exc):
                        return self.store.get(request.intent_id), False
                    raise
            elif record.state is IntentState.UNKNOWN:
                if record.outcome != outcome.to_dict():
                    try:
                        record = self.store.resolve_recovered_outcome(
                            request,
                            outcome,
                            worker_session_id=envelope.get("worker_session_id"),
                        )
                    except Exception as exc:
                        if _is_storage_error(exc):
                            return self.store.get(request.intent_id), False
                        raise
            elif record.state is not outcome.state:
                raise IntentConflictError(
                    f"durable state {record.state.value} conflicts with worker outcome {outcome.state.value}"
                )
            elif record.outcome != outcome.to_dict():
                raise IntentConflictError(
                    "worker outcome does not match the durable terminal outcome"
                )
            self._release_envelope(request.request_id)
            return record, True
        error = envelope.get("error")
        if error:
            expected = BrokerOutcome(
                state=IntentState.UNKNOWN,
                error=str(error),
            )
            if record.state is IntentState.DISPATCHING:
                try:
                    record = self.store.record_outcome(request, expected)
                except Exception as exc:
                    if _is_storage_error(exc):
                        return self.store.get(request.intent_id), False
                    raise
            elif record.state is IntentState.UNKNOWN:
                if record.outcome != expected.to_dict():
                    try:
                        record = self.store.resolve_recovered_outcome(
                            request,
                            expected,
                            worker_session_id=envelope.get("worker_session_id"),
                        )
                    except Exception as exc:
                        if _is_storage_error(exc):
                            return self.store.get(request.intent_id), False
                        raise
            elif record.state is not IntentState.PREPARED:
                raise IntentConflictError(
                    "worker error conflicts with the durable terminal outcome"
                )
            self._release_envelope(request.request_id)
            return record, True
        if record.state not in {IntentState.PREPARED, IntentState.DISPATCHING}:
            self._release_envelope(request.request_id)
            return record, True
        raise RuntimeError("worker response contains neither outcome nor error")

    def _retry_pending_envelope(self) -> None:
        if self._pending_request_id is None:
            return
        envelope = self._response_backlog.get(self._pending_request_id)
        if envelope is None:
            return
        _record, resolved = self._process_envelope(envelope)
        if resolved:
            self._clear_pending()

    def _retry_retained_envelopes(self) -> None:
        for envelope in list(self._response_backlog.values()):
            _record, resolved = self._process_envelope(envelope)
            if resolved and self._matches_pending(envelope):
                self._clear_pending()

    def _drain_late_responses(self, *, wait_timeout: float = 0.0) -> None:
        if wait_timeout < 0:
            raise ValueError("wait_timeout must be non-negative")
        deadline = time.monotonic() + wait_timeout
        while True:
            try:
                remaining = _remaining(deadline)
                if remaining > 0 and self._pending_request_id is not None:
                    envelope = self._responses.get(block=True, timeout=remaining)
                else:
                    envelope = self._responses.get_nowait()
            except queue.Empty:
                return
            _validate_envelope(envelope)
            _record, resolved = self._process_envelope(envelope)
            if resolved and self._matches_pending(envelope):
                self._clear_pending()

    def _matches_pending(self, envelope: Mapping[str, Any]) -> bool:
        return (
            envelope.get("request_id") == self._pending_request_id
            and envelope.get("intent_id") == self._pending_intent_id
            and (
                getattr(self, "_pending_attempt_id", None) is None
                or envelope.get("attempt_id") == self._pending_attempt_id
            )
        )

    def _release_envelope(self, request_id: str) -> None:
        self._response_backlog.pop(request_id, None)
        if (
            self._pending_envelope is not None
            and self._pending_envelope.get("request_id") == request_id
        ):
            self._pending_envelope = None

    def _clear_pending(self) -> None:
        self._pending_request_id = None
        self._pending_intent_id = None
        self._pending_attempt_id = None
        self._pending_request = None
        self._pending_envelope = None

    def _settle_after_wait(
        self,
        intent_id: str,
        *,
        request: BrokerRequest | None,
    ) -> IntentRecord:
        self._drain_late_responses(wait_timeout=_IN_TRANSIT_RESPONSE_GRACE_SECONDS)
        self._retry_pending_envelope()
        record = self.store.get(intent_id)
        if (
            self._pending_request_id is not None
            and self._pending_request_id in self._response_backlog
        ):
            return record
        if record.state not in {IntentState.PREPARED, IntentState.DISPATCHING}:
            self._clear_pending()
            return record
        if self.is_alive:
            return record
        self._drain_late_responses(wait_timeout=_IN_TRANSIT_RESPONSE_GRACE_SECONDS)
        self._retry_pending_envelope()
        record = self.store.get(intent_id)
        if (
            self._pending_request_id is not None
            and self._pending_request_id in self._response_backlog
        ):
            return record
        if record.state is IntentState.DISPATCHING:
            self.store.recover_dispatching("worker_died_with_unresolved_request")
        elif record.state is IntentState.PREPARED and request is not None:
            self.store.record_predispatch_failure(
                request,
                "worker_died_before_dispatch",
                worker_session_id=getattr(self, "_worker_session_id", None),
            )
        self._clear_pending()
        return self.store.get(intent_id)

    def _replace_queues(self) -> None:
        for transport_queue in (self._requests, self._responses):
            try:
                transport_queue.close()
                transport_queue.cancel_join_thread()
            except (AttributeError, OSError, ValueError):
                pass
        self._requests = self._context.Queue(maxsize=self._queue_size)
        self._responses = self._context.Queue(maxsize=self._queue_size)

    def __enter__(self) -> "ProcessBrokerGateway":
        self.start()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()


def _worker_main(
    store_path: str,
    requests,
    responses,
    broker_handler: Callable[[Mapping[str, Any]], Mapping[str, Any] | None],
    worker_session_id: str | None = None,
) -> None:
    store = IntentStore(store_path)
    session_id = worker_session_id or uuid.uuid4().hex
    while True:
        payload = requests.get()
        if payload is None:
            return
        request = None
        request_id = payload.get("request_id") if isinstance(payload, dict) else None
        try:
            request = BrokerRequest.from_dict(payload)
            record = execute_once(
                store,
                request,
                broker_handler,
                worker_session_id=session_id,
                worker_pid=multiprocessing.current_process().pid,
            )
            outcome = (
                BrokerOutcome.from_dict(record.outcome)
                if isinstance(record.outcome, Mapping)
                else None
            )
            responses.put(
                _envelope(
                    request,
                    "persisted",
                    outcome=outcome,
                    worker_session_id=session_id,
                )
            )
        except PreDispatchError as exc:
            responses.put(
                _envelope(
                    request,
                    "predispatch_error",
                    error=f"predispatch_error:{type(exc.cause).__name__}",
                    worker_session_id=session_id,
                )
            )
        except OutcomePersistenceError as exc:
            responses.put(
                _envelope(
                    request,
                    "persistence_error",
                    outcome=exc.outcome,
                    error=f"persistence_error:{type(exc.cause).__name__}",
                    worker_session_id=session_id,
                )
            )
        except Exception as exc:
            responses.put(
                _envelope(
                    request if request is not None else payload,
                    "worker_error",
                    error=f"worker_exception:{type(exc).__name__}",
                    fallback_request_id=request_id,
                    worker_session_id=session_id,
                )
            )


def _envelope(
    request: BrokerRequest | Mapping[str, Any],
    status: str,
    *,
    outcome: BrokerOutcome | None = None,
    error: str | None = None,
    fallback_request_id: Any = None,
    worker_session_id: str | None = None,
) -> dict[str, Any]:
    request_value = request.to_dict() if isinstance(request, BrokerRequest) else request
    if isinstance(request_value, Mapping):
        request_id = request_value.get("request_id")
        intent_id = request_value.get("intent_id")
        attempt_id = request_value.get("attempt_id")
        serialized_request = dict(request_value)
    else:
        request_id = fallback_request_id
        intent_id = None
        attempt_id = None
        serialized_request = None
    return {
        "request_id": request_id,
        "intent_id": intent_id,
        "attempt_id": attempt_id,
        "request": serialized_request,
        "status": status,
        "worker_session_id": worker_session_id,
        "outcome": outcome.to_dict() if outcome is not None else None,
        "error": error,
    }


def _validate_envelope(envelope: Any) -> None:
    if not isinstance(envelope, dict) or not isinstance(envelope.get("request_id"), str):
        raise RuntimeError("invalid worker response envelope")


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _mark_future_done(future: asyncio.Future) -> None:
    if not future.done():
        future.set_result(None)


def _is_storage_error(exc: Exception) -> bool:
    import sqlite3

    return isinstance(exc, sqlite3.Error)
