import asyncio
from concurrent.futures import ThreadPoolExecutor
import ctypes
from dataclasses import replace
from functools import partial
import multiprocessing
import os
import queue
import sqlite3
import threading
import time

import pytest

from execution_intents import IntentConflictError, IntentStore, execute_once
from mt5_gateway import (
    AdmissionTimeoutError,
    ProcessBrokerGateway,
    UnresolvedRequestError,
    _worker_main,
)
from mt5_protocol import BrokerOutcome
from mt5_protocol import BrokerRequest, IntentKey, IntentState


def _request(*, attempt_id="attempt-1", block_seconds=0.0, fail=False):
    key = IntentKey(
        account_fingerprint="server/account",
        channel="canal1",
        signal_root="canal1_gateway_test",
        generation=1,
        leg="entry-1",
        operation="OPEN_MARKET",
        revision=0,
    )
    return BrokerRequest.create(
        key,
        {"block_seconds": block_seconds, "fail": fail},
        request_id=f"request-{attempt_id}",
        attempt_id=attempt_id,
        action_id="action-1",
    )


def blocking_broker(request):
    payload = request["payload"]
    deadline = time.perf_counter() + float(payload["block_seconds"])
    value = 0
    while time.perf_counter() < deadline:
        value = (value + 1) % 1_000_003
    if payload["fail"]:
        raise RuntimeError("broker double failed after dispatch")
    return {"retcode": 10009, "deal": os.getpid(), "price": 3600.0 + value * 0.0}


def gated_broker(request, *, entered):
    entered.set()
    # This call ends only when its owning test terminates the worker. Do not
    # leave a shared Condition waiter that cleanup must notify after death.
    while True:
        time.sleep(1.)


def native_gil_broker(request):
    milliseconds = int(request["payload"]["block_seconds"] * 1000)
    if os.name == "nt":
        sleep = ctypes.PyDLL("kernel32").Sleep
        sleep.argtypes = [ctypes.c_ulong]
        sleep.restype = None
        sleep(milliseconds)
    else:
        sleep = ctypes.PyDLL(None).usleep
        sleep.argtypes = [ctypes.c_uint]
        sleep.restype = ctypes.c_int
        sleep(milliseconds * 1000)
    return {"retcode": 10009, "deal": os.getpid(), "price": 3600.0}


async def _heartbeat(duration, interval=0.02):
    moments = []
    deadline = time.perf_counter() + duration
    while time.perf_counter() < deadline:
        moments.append(time.perf_counter())
        await asyncio.sleep(interval)
    return moments


@pytest.mark.slow
async def test_five_second_gil_holding_broker_call_does_not_freeze_parent_loop(tmp_path):
    gateway = ProcessBrokerGateway(tmp_path / "intents.sqlite3", native_gil_broker)
    gateway.start()
    try:
        heartbeat = asyncio.create_task(_heartbeat(5.3))
        await asyncio.sleep(0.05)
        record = await gateway.call_async(_request(block_seconds=5.0), timeout=8.0)
        moments = await heartbeat
    finally:
        gateway.close()

    gaps = [right - left for left, right in zip(moments, moments[1:])]
    assert record.state is IntentState.DONE
    assert record.outcome["deal"] != os.getpid()
    assert len(moments) > 150
    assert max(gaps) < 0.25


async def test_same_process_negative_control_does_freeze_parent_loop():
    heartbeat = asyncio.create_task(_heartbeat(0.7))
    await asyncio.sleep(0.05)
    await asyncio.to_thread(native_gil_broker, _request(block_seconds=0.4).to_dict())
    moments = await heartbeat

    gaps = [right - left for left, right in zip(moments, moments[1:])]
    assert max(gaps) > 0.35


async def test_timeout_keeps_single_request_unresolved_until_late_result_arrives(tmp_path):
    gateway = ProcessBrokerGateway(tmp_path / "intents.sqlite3", blocking_broker)
    gateway.start()
    try:
        request = _request(block_seconds=0.4)
        timed_out = await gateway.call_async(request, timeout=0.05)
        assert timed_out.state in {IntentState.PREPARED, IntentState.DISPATCHING}

        with pytest.raises(UnresolvedRequestError):
            await gateway.call_async(
                _request(attempt_id="attempt-2", block_seconds=0.4),
                timeout=1.0,
            )

        completed = await gateway.await_late_async(timeout=2.0)
        redelivered = await gateway.call_async(
            _request(attempt_id="attempt-2", block_seconds=0.4),
            timeout=1.0,
        )
    finally:
        gateway.close()

    assert completed.state is IntentState.DONE
    assert redelivered.state is IntentState.DONE
    assert redelivered.attempt_id == "attempt-1"


async def test_broker_exception_after_dispatch_becomes_unknown(tmp_path):
    gateway = ProcessBrokerGateway(tmp_path / "intents.sqlite3", blocking_broker)
    gateway.start()
    try:
        record = await gateway.call_async(_request(fail=True), timeout=2.0)
    finally:
        gateway.close()

    assert record.state is IntentState.UNKNOWN
    assert record.outcome["error"] == "broker_exception:RuntimeError"


@pytest.mark.parametrize("caller_timeout", [0.5, 1.0])
async def test_worker_death_during_native_call_is_unknown_and_redelivery_is_not_sent(tmp_path, caller_timeout):
    context = multiprocessing.get_context("spawn")
    entered = context.Event()
    gateway = ProcessBrokerGateway(tmp_path / "intents.sqlite3",
        partial(gated_broker, entered=entered))
    gateway.start()
    request = _request(block_seconds=5.0)
    try:
        timed_out = await gateway.call_async(request, timeout=caller_timeout)
        assert timed_out.state in {IntentState.PREPARED, IntentState.DISPATCHING}
        # Kill inside the call, not at an assumed process-startup deadline.
        assert await asyncio.to_thread(entered.wait, 10.)
        assert gateway.store.get(request.intent_id).state is IntentState.DISPATCHING

        recovered = await asyncio.to_thread(gateway.recover_after_worker_loss)
        redelivered = await gateway.call_async(
            _request(attempt_id="attempt-2", block_seconds=5.0),
            timeout=0.5,
        )
    finally:
        gateway.close()

    assert recovered == 1
    assert redelivered.state is IntentState.UNKNOWN
    assert redelivered.attempt_id == "attempt-1"


class _AliveProcess:
    def __init__(self):
        self.alive = True

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.alive = False

    def join(self, timeout=None):
        return None


class _ClosableQueue(queue.Queue):
    def close(self):
        return None

    def join_thread(self):
        return None

    def cancel_join_thread(self):
        return None


class _StartableProcess(_AliveProcess):
    def __init__(self):
        super().__init__()
        self.alive = False

    def start(self):
        self.alive = True


class _FakeContext:
    def Queue(self, maxsize):
        return _ClosableQueue(maxsize)

    def Process(self, **_kwargs):
        return _StartableProcess()


def _fake_gateway(path):
    gateway = object.__new__(ProcessBrokerGateway)
    gateway.store = IntentStore(path)
    gateway._process = _AliveProcess()
    gateway._lock = threading.Lock()
    gateway._sync_admission = threading.BoundedSemaphore(8)
    gateway._requests = queue.Queue(8)
    gateway._responses = queue.Queue(8)
    gateway._response_backlog = {}
    gateway._pending_envelope = None
    gateway._pending_request_id = None
    gateway._pending_intent_id = None
    gateway._pending_attempt_id = None
    gateway._pending_request = None
    gateway._closed = False
    return gateway


def _envelope(request, outcome, *, status="persisted", error=None):
    return {
        "request_id": request.request_id,
        "intent_id": request.intent_id,
        "attempt_id": request.attempt_id,
        "request": request.to_dict(),
        "status": status,
        "outcome": outcome.to_dict() if outcome else None,
        "error": error,
    }


def test_late_notice_is_consumed_without_breaking_the_next_request(tmp_path, monkeypatch):
    gateway = _fake_gateway(tmp_path / "intents.sqlite3")
    first = _request(attempt_id="first")
    second = BrokerRequest.create(
        first.intent_key.__class__(
            account_fingerprint="server/account",
            channel="canal1",
            signal_root="canal1_second",
            generation=1,
            leg="entry-1",
            operation="OPEN_MARKET",
            revision=0,
        ),
        {"block_seconds": 0.0, "fail": False},
        request_id="request-second",
        attempt_id="second",
        action_id="action-2",
    )
    original_wait = gateway._wait_response

    def finish_without_notice(_request_id, _deadline):
        record = execute_once(gateway.store, first, lambda _payload: {"retcode": 10009, "deal": 41})
        assert record.state is IntentState.DONE
        gateway._responses.put(
            _envelope(first, BrokerOutcome.from_dict(record.outcome))
        )
        return None

    monkeypatch.setattr(gateway, "_wait_response", finish_without_notice)
    assert gateway.call(first, timeout=0.1).state is IntentState.DONE
    monkeypatch.setattr(gateway, "_wait_response", original_wait)

    def complete_second():
        while gateway._requests.qsize() < 2:
            time.sleep(0.005)
        record = execute_once(
            gateway.store,
            second,
            lambda _payload: {"retcode": 10009, "deal": 42},
        )
        gateway._responses.put(
            _envelope(second, BrokerOutcome.from_dict(record.outcome))
        )

    helper = threading.Thread(target=complete_second)
    helper.start()
    try:
        result = gateway.call(second, timeout=1.0)
    finally:
        helper.join(timeout=1.0)

    assert result.state is IntentState.DONE
    assert result.outcome["deal"] == 42
    assert gateway.has_unresolved_request is False


def test_worker_persistence_failure_delivers_outcome_for_parent_to_store(tmp_path, monkeypatch):
    path = tmp_path / "intents.sqlite3"
    request = _request()
    inputs, outputs = queue.Queue(), queue.Queue()
    inputs.put(request.to_dict())
    inputs.put(None)
    original = IntentStore.record_outcome
    calls = 0

    def fail_child_once(self, dispatched_request, outcome):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError("disk unavailable")
        return original(self, dispatched_request, outcome)

    import sqlite3

    monkeypatch.setattr(IntentStore, "record_outcome", fail_child_once)
    _worker_main(str(path), inputs, outputs, lambda _payload: {"retcode": 10009, "deal": 501})
    monkeypatch.setattr(IntentStore, "record_outcome", original)

    gateway = _fake_gateway(path)
    gateway._pending_request_id = request.request_id
    gateway._pending_intent_id = request.intent_id
    gateway._responses.put(outputs.get_nowait())
    result = gateway.await_late(timeout=0.2)

    assert result.state is IntentState.DONE
    assert result.outcome["deal"] == 501
    assert gateway.has_unresolved_request is False


def test_persistent_outcome_write_failure_retains_envelope_without_resending(tmp_path, monkeypatch):
    gateway = _fake_gateway(tmp_path / "intents.sqlite3")
    request = _request()
    gateway.store.prepare(request)
    gateway.store.begin_dispatch(request)
    gateway._pending_request_id = request.request_id
    gateway._pending_intent_id = request.intent_id
    gateway._responses.put(
        _envelope(request, BrokerOutcome(IntentState.DONE, retcode=10009, deal=502))
    )
    original = gateway.store.record_outcome

    def unavailable(*_args, **_kwargs):
        raise sqlite3.OperationalError("disk unavailable")

    import sqlite3

    monkeypatch.setattr(gateway.store, "record_outcome", unavailable)
    unresolved = gateway.await_late(timeout=0.2)

    assert unresolved.state is IntentState.DISPATCHING
    assert gateway.has_unresolved_request is True
    assert gateway._pending_envelope["outcome"]["deal"] == 502

    monkeypatch.setattr(gateway.store, "record_outcome", original)
    completed = gateway.await_late(timeout=0.2)

    assert completed.state is IntentState.DONE
    assert completed.outcome["deal"] == 502
    assert gateway.has_unresolved_request is False


def test_late_worker_error_becomes_unknown_and_clears_pending(tmp_path):
    gateway = _fake_gateway(tmp_path / "intents.sqlite3")
    request = _request()
    gateway.store.prepare(request)
    gateway.store.begin_dispatch(request)
    gateway._pending_request_id = request.request_id
    gateway._pending_intent_id = request.intent_id
    gateway._responses.put(
        _envelope(request, None, status="worker_error", error="worker_exception:RuntimeError")
    )

    result = gateway.await_late(timeout=0.2)

    assert result.state is IntentState.UNKNOWN
    assert result.outcome["error"] == "worker_exception:RuntimeError"
    assert gateway.has_unresolved_request is False


def test_admission_timeout_includes_lock_wait_and_never_enqueues(tmp_path):
    gateway = _fake_gateway(tmp_path / "intents.sqlite3")
    gateway._lock.acquire()
    timer = threading.Timer(0.3, gateway._lock.release)
    timer.start()
    started = time.perf_counter()
    try:
        with pytest.raises(AdmissionTimeoutError):
            gateway.call(_request(), timeout=0.02)
        elapsed = time.perf_counter() - started
    finally:
        timer.join()

    assert elapsed < 0.2
    assert gateway._requests.empty()


async def test_async_deadline_does_not_restart_while_waiting_for_executor(tmp_path, monkeypatch):
    gateway = _fake_gateway(tmp_path / "intents.sqlite3")
    gateway._queue_size = 1
    gateway._sync_admission = threading.BoundedSemaphore(1)
    gateway._async_admission = None
    request = _request(attempt_id="pool-delay")
    loop = asyncio.get_running_loop()
    pool = ThreadPoolExecutor(max_workers=1)
    loop.set_default_executor(pool)
    entered = threading.Event()
    release = threading.Event()
    sends = []

    def occupy_executor():
        entered.set()
        release.wait(timeout=1.0)

    blocker = pool.submit(occupy_executor)
    for _ in range(20):
        if entered.is_set():
            break
        await asyncio.sleep(0.005)
    assert entered.is_set()

    def should_not_send(_request_id, _deadline):
        sends.append(time.monotonic())
        raise AssertionError("expired request reached response wait")

    monkeypatch.setattr(gateway, "_wait_response", should_not_send)
    loop.call_later(0.15, release.set)
    started = time.monotonic()
    with pytest.raises(AdmissionTimeoutError):
        await gateway.call_async(request, timeout=0.02)
    elapsed = time.monotonic() - started
    release.set()
    blocker.result(timeout=1.0)

    assert elapsed < 0.1
    assert sends == []
    assert gateway._requests.empty()


def test_read_failure_retains_consumed_outcome_until_storage_recovers(tmp_path, monkeypatch):
    gateway = _fake_gateway(tmp_path / "intents.sqlite3")
    request = _request(attempt_id="read-failure")
    gateway.store.prepare(request)
    gateway.store.begin_dispatch(request)
    gateway._pending_request_id = request.request_id
    gateway._pending_intent_id = request.intent_id
    gateway._responses.put(
        _envelope(request, BrokerOutcome(IntentState.DONE, retcode=10009, deal=601))
    )
    original = gateway.store.get
    calls = 0

    def fail_once(intent_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError("read unavailable")
        return original(intent_id)

    monkeypatch.setattr(gateway.store, "get", fail_once)
    with pytest.raises(sqlite3.OperationalError):
        gateway.await_late(timeout=0.1)

    completed = gateway.await_late(timeout=0.1)
    assert completed.state is IntentState.DONE
    assert completed.outcome["deal"] == 601
    assert gateway.has_unresolved_request is False


def test_worker_recovery_persists_retained_outcome_before_marking_unknown(tmp_path, monkeypatch):
    gateway = _fake_gateway(tmp_path / "intents.sqlite3")
    request = _request(attempt_id="recovery-outcome")
    gateway.store.prepare(request)
    gateway.store.begin_dispatch(request)
    gateway._pending_request_id = request.request_id
    gateway._pending_intent_id = request.intent_id
    gateway._responses.put(
        _envelope(request, BrokerOutcome(IntentState.DONE, retcode=10009, deal=602))
    )
    original = gateway.store.record_outcome

    def fail_write(*_args, **_kwargs):
        raise sqlite3.OperationalError("write unavailable")

    monkeypatch.setattr(gateway.store, "record_outcome", fail_write)
    unresolved = gateway.await_late(timeout=0.1)
    assert unresolved.state is IntentState.DISPATCHING
    monkeypatch.setattr(gateway.store, "record_outcome", original)
    gateway._process = _AliveProcess()

    recovered = gateway.recover_after_worker_loss()
    completed = gateway.store.get(request.intent_id)
    assert recovered == 0
    assert completed.state is IntentState.DONE
    assert completed.outcome["deal"] == 602


def test_worker_recovery_waits_briefly_for_an_in_transit_outcome(tmp_path):
    gateway = _fake_gateway(tmp_path / "intents.sqlite3")
    request = _request(attempt_id="in-transit")
    gateway.store.prepare(request)
    gateway.store.begin_dispatch(request)
    gateway._pending_request_id = request.request_id
    gateway._pending_intent_id = request.intent_id

    def deliver_after_queue_feeder_delay():
        time.sleep(0.03)
        gateway._responses.put(
            _envelope(request, BrokerOutcome(IntentState.DONE, retcode=10009, deal=606))
        )

    helper = threading.Thread(target=deliver_after_queue_feeder_delay)
    helper.start()
    try:
        recovered = gateway.recover_after_worker_loss()
    finally:
        helper.join(timeout=1.0)

    completed = gateway.store.get(request.intent_id)
    assert recovered == 0
    assert completed.state is IntentState.DONE
    assert completed.outcome["deal"] == 606


def test_close_waits_briefly_for_an_in_transit_outcome(tmp_path):
    gateway = _fake_gateway(tmp_path / "intents.sqlite3")
    gateway._requests = _ClosableQueue(8)
    gateway._responses = _ClosableQueue(8)
    request = _request(attempt_id="close-in-transit")
    gateway.store.prepare(request)
    gateway.store.begin_dispatch(request)
    gateway._pending_request_id = request.request_id
    gateway._pending_intent_id = request.intent_id

    def deliver_after_queue_feeder_delay():
        time.sleep(0.03)
        gateway._responses.put(
            _envelope(request, BrokerOutcome(IntentState.DONE, retcode=10009, deal=607))
        )

    helper = threading.Thread(target=deliver_after_queue_feeder_delay)
    helper.start()
    try:
        gateway.close()
    finally:
        helper.join(timeout=1.0)

    completed = gateway.store.get(request.intent_id)
    assert completed.state is IntentState.DONE
    assert completed.outcome["deal"] == 607
    assert gateway._closed is True


def test_restart_waits_briefly_for_an_in_transit_outcome(tmp_path):
    gateway = _fake_gateway(tmp_path / "intents.sqlite3")
    gateway.store_path = tmp_path / "intents.sqlite3"
    gateway._queue_size = 8
    gateway._requests = _ClosableQueue(8)
    gateway._responses = _ClosableQueue(8)
    gateway._context = _FakeContext()
    gateway._handler = lambda _payload: {"retcode": 10009}
    gateway._process.alive = False
    request = _request(attempt_id="restart-in-transit")
    gateway.store.prepare(request)
    gateway.store.begin_dispatch(request)
    gateway._pending_request_id = request.request_id
    gateway._pending_intent_id = request.intent_id

    def deliver_after_queue_feeder_delay():
        time.sleep(0.03)
        gateway._responses.put(
            _envelope(request, BrokerOutcome(IntentState.DONE, retcode=10009, deal=608))
        )

    helper = threading.Thread(target=deliver_after_queue_feeder_delay)
    helper.start()
    try:
        gateway.start()
    finally:
        helper.join(timeout=1.0)

    completed = gateway.store.get(request.intent_id)
    assert completed.state is IntentState.DONE
    assert completed.outcome["deal"] == 608
    assert gateway.is_alive is True


def test_drained_late_outcome_clears_only_its_matching_pending_request(tmp_path):
    gateway = _fake_gateway(tmp_path / "intents.sqlite3")
    request = _request(attempt_id="drained")
    gateway.store.prepare(request)
    gateway.store.begin_dispatch(request)
    gateway._pending_request_id = request.request_id
    gateway._pending_intent_id = request.intent_id
    gateway._responses.put(
        _envelope(request, BrokerOutcome(IntentState.DONE, retcode=10009, deal=603))
    )

    gateway._drain_late_responses()
    assert gateway.store.get(request.intent_id).state is IntentState.DONE
    assert gateway.has_unresolved_request is False


def test_response_must_match_the_full_durable_dispatched_request(tmp_path):
    gateway = _fake_gateway(tmp_path / "intents.sqlite3")
    request = _request(attempt_id="identity")
    gateway.store.prepare(request)
    gateway.store.begin_dispatch(request, worker_session_id="worker-a", worker_pid=321)
    gateway._pending_request_id = request.request_id
    gateway._pending_intent_id = request.intent_id
    altered = replace(
        request,
        request_id="other-request",
        action_id="other-action",
        payload={"block_seconds": 0.0, "fail": False, "volume": 1.0},
    )
    envelope = _envelope(
        altered,
        BrokerOutcome(IntentState.DONE, retcode=10009, deal=604),
    )
    envelope["worker_session_id"] = "worker-a"

    with pytest.raises(IntentConflictError):
        gateway._process_envelope(envelope)
    assert gateway.store.get(request.intent_id).state is IntentState.DISPATCHING
    assert gateway.has_unresolved_request is True


def test_repeated_response_must_match_the_full_durable_outcome(tmp_path):
    gateway = _fake_gateway(tmp_path / "intents.sqlite3")
    request = _request(attempt_id="outcome-identity")
    gateway.store.prepare(request)
    gateway.store.begin_dispatch(request)
    gateway.store.record_outcome(
        request,
        BrokerOutcome(IntentState.DONE, retcode=10009, deal=605),
    )
    conflicting = _envelope(
        request,
        BrokerOutcome(IntentState.DONE, retcode=10009, deal=999),
    )

    with pytest.raises(IntentConflictError):
        gateway._process_envelope(conflicting)
    assert gateway.store.get(request.intent_id).outcome["deal"] == 605
    assert gateway._response_backlog[request.request_id]["outcome"]["deal"] == 999


def test_conflicting_response_cannot_replace_a_retained_outcome(tmp_path, monkeypatch):
    gateway = _fake_gateway(tmp_path / "intents.sqlite3")
    request = _request(attempt_id="retained-conflict")
    gateway.store.prepare(request)
    gateway.store.begin_dispatch(request)
    first = _envelope(
        request,
        BrokerOutcome(IntentState.DONE, retcode=10009, deal=609),
    )
    second = _envelope(
        request,
        BrokerOutcome(IntentState.DONE, retcode=10009, deal=999),
    )

    def unavailable(*_args, **_kwargs):
        raise sqlite3.OperationalError("write unavailable")

    monkeypatch.setattr(gateway.store, "record_outcome", unavailable)
    _record, resolved = gateway._process_envelope(first)
    assert resolved is False

    with pytest.raises(IntentConflictError):
        gateway._process_envelope(second)
    assert gateway._response_backlog[request.request_id]["outcome"]["deal"] == 609


async def test_async_cancellation_keeps_capacity_reserved_until_thread_finishes(tmp_path, monkeypatch):
    gateway = _fake_gateway(tmp_path / "intents.sqlite3")
    gateway._queue_size = 1
    gateway._sync_admission = threading.BoundedSemaphore(1)
    gateway._async_admission = None
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def blocking_call(request, deadline):
        calls.append(request.request_id)
        entered.set()
        release.wait(timeout=1.0)
        return gateway.store.prepare(request)

    monkeypatch.setattr(gateway, "_call_until", blocking_call)
    first = asyncio.create_task(gateway.call_async(_request(attempt_id="cancelled"), timeout=0.8))
    assert await asyncio.to_thread(entered.wait, 0.2)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    with pytest.raises(AdmissionTimeoutError):
        await gateway.call_async(_request(attempt_id="blocked"), timeout=0.02)
    assert calls == ["request-cancelled"]

    release.set()
    for _ in range(20):
        await asyncio.sleep(0.01)
        if gateway._async_admission._value == 1:
            break
    completed = await gateway.call_async(_request(attempt_id="after-cancel"), timeout=0.2)

    assert completed.state is IntentState.PREPARED
    assert calls == ["request-cancelled", "request-after-cancel"]


def test_predispatch_storage_error_releases_gateway_without_broker_call(tmp_path, monkeypatch):
    gateway = _fake_gateway(tmp_path / "intents.sqlite3")
    gateway._worker_session_id = "worker-a"
    request = _request(attempt_id="predispatch")
    broker_calls = []

    def return_predispatch_error(_request_id, _deadline):
        inputs, outputs = queue.Queue(), queue.Queue()
        inputs.put(request.to_dict())
        inputs.put(None)
        with monkeypatch.context() as context:
            context.setattr(
                IntentStore,
                "begin_dispatch",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    sqlite3.OperationalError("database is locked")
                ),
            )
            _worker_main(
                str(gateway.store.path),
                inputs,
                outputs,
                broker_calls.append,
                gateway._worker_session_id,
            )
        return outputs.get_nowait()

    monkeypatch.setattr(gateway, "_wait_response", return_predispatch_error)
    record = gateway.call(request, timeout=0.2)

    assert record.state is IntentState.PREPARED
    assert broker_calls == []
    assert gateway.has_unresolved_request is False
    assert gateway.recover_after_worker_loss() == 0


def test_timeout_boundary_preserves_confirmed_outcome_after_worker_exit(tmp_path, monkeypatch):
    gateway = _fake_gateway(tmp_path / "intents.sqlite3")
    gateway._worker_session_id = "worker-a"
    request = _request(attempt_id="timeout-boundary")
    broker_calls = []
    original_wait = gateway._wait_response

    def deliver_after_wait_expires(request_id, deadline):
        inputs, outputs = queue.Queue(), queue.Queue()
        inputs.put(request.to_dict())
        inputs.put(None)

        def broker(payload):
            broker_calls.append(payload)
            return {"retcode": 10009, "deal": 772}

        with monkeypatch.context() as context:
            context.setattr(
                IntentStore,
                "record_outcome",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    sqlite3.OperationalError("disk unavailable")
                ),
            )
            _worker_main(
                str(gateway.store.path),
                inputs,
                outputs,
                broker,
                gateway._worker_session_id,
            )
        expired = original_wait(request_id, deadline)
        gateway._responses.put(outputs.get_nowait())
        gateway._process.alive = False
        return expired

    monkeypatch.setattr(gateway, "_wait_response", deliver_after_wait_expires)
    record = gateway.call(request, timeout=0.04)

    assert len(broker_calls) == 1
    assert record.state is IntentState.DONE
    assert record.outcome["deal"] == 772
    assert gateway.has_unresolved_request is False


def test_start_and_close_are_one_serialized_lifecycle_transition(tmp_path):
    gateway = _fake_gateway(tmp_path / "intents.sqlite3")
    gateway.store_path = tmp_path / "intents.sqlite3"
    gateway._queue_size = 8
    gateway._requests = _ClosableQueue(8)
    gateway._responses = _ClosableQueue(8)
    gateway._handler = lambda _payload: {"retcode": 10009}
    gateway._process = None
    entered = threading.Event()
    release = threading.Event()

    class PausedContext:
        def Process(self, **_kwargs):
            entered.set()
            assert release.wait(1.0)
            return _StartableProcess()

    gateway._context = PausedContext()
    starter = threading.Thread(target=gateway.start)
    starter.start()
    assert entered.wait(0.5)
    closer = threading.Thread(target=gateway.close)
    closer.start()
    release.set()
    starter.join(timeout=1.0)
    closer.join(timeout=1.0)

    assert starter.is_alive() is False
    assert closer.is_alive() is False
    assert gateway._closed is True
    assert gateway.is_alive is False


def test_reused_request_id_cannot_return_another_intents_outcome(tmp_path, monkeypatch):
    gateway = _fake_gateway(tmp_path / "intents.sqlite3")
    first = _request(attempt_id="first-identity")
    second = BrokerRequest.create(
        replace(first.intent_key, signal_root="canal1_second"),
        dict(first.payload),
        request_id=first.request_id,
        attempt_id="second-identity",
        action_id="action-2",
    )
    gateway.store.prepare(first)
    gateway.store.begin_dispatch(first)
    outcome = BrokerOutcome(IntentState.DONE, retcode=10009, deal=42)
    gateway.store.record_outcome(first, outcome)
    original_wait = gateway._wait_response

    def delayed_old_notice(request_id, deadline):
        gateway.store.begin_dispatch(second)
        gateway._responses.put(_envelope(first, outcome))
        return original_wait(request_id, deadline)

    monkeypatch.setattr(gateway, "_wait_response", delayed_old_notice)
    with pytest.raises(IntentConflictError):
        gateway.call(second, timeout=0.2)

    assert gateway.store.get(second.intent_id).state is IntentState.PREPARED
    assert gateway.has_unresolved_request is False


def test_expired_deadline_after_durable_admission_never_enqueues(tmp_path, monkeypatch):
    gateway = _fake_gateway(tmp_path / "intents.sqlite3")
    request = _request(attempt_id="slow-admission")
    original_admit = gateway.store.admit

    def delayed_admit(*args, **kwargs):
        time.sleep(0.04)
        return original_admit(*args, **kwargs)

    monkeypatch.setattr(gateway.store, "admit", delayed_admit)
    with pytest.raises(AdmissionTimeoutError):
        gateway.call(request, timeout=0.01)

    assert gateway._requests.empty()
    assert gateway.store.get(request.intent_id).state is IntentState.PREPARED
    assert gateway.has_unresolved_request is False


def test_retained_confirmation_prevents_recovery_downgrade(tmp_path, monkeypatch):
    gateway = _fake_gateway(tmp_path / "intents.sqlite3")
    request = _request(attempt_id="retained-recovery")
    gateway.store.prepare(request)
    gateway.store.begin_dispatch(request, worker_session_id="worker-a")
    gateway._pending_request_id = request.request_id
    gateway._pending_intent_id = request.intent_id
    gateway._pending_attempt_id = request.attempt_id
    gateway._pending_request = request
    gateway._process.alive = False
    envelope = _envelope(
        request,
        BrokerOutcome(IntentState.DONE, retcode=10009, deal=902),
        status="persistence_error",
    )
    envelope["worker_session_id"] = "worker-a"
    gateway._responses.put(envelope)
    original = gateway.store.record_outcome

    def unavailable(*_args, **_kwargs):
        raise sqlite3.OperationalError("write unavailable")

    monkeypatch.setattr(gateway.store, "record_outcome", unavailable)
    unresolved = gateway._settle_after_wait(request.intent_id, request=request)

    assert unresolved.state is IntentState.DISPATCHING
    assert gateway.has_unresolved_request is True
    assert gateway._response_backlog[request.request_id]["outcome"]["deal"] == 902

    monkeypatch.setattr(gateway.store, "record_outcome", original)
    completed = gateway.call(request, timeout=0.2)
    assert completed.state is IntentState.DONE
    assert completed.outcome["deal"] == 902
    assert gateway.has_unresolved_request is False


def test_late_broker_unknown_replaces_gateway_recovery_unknown(tmp_path):
    gateway = _fake_gateway(tmp_path / "intents.sqlite3")
    request = _request(attempt_id="late-broker-unknown")
    gateway.store.prepare(request)
    gateway.store.begin_dispatch(request, worker_session_id="worker-a")
    gateway.store.recover_dispatching("worker_died_with_unresolved_request")
    outcome = BrokerOutcome(IntentState.UNKNOWN, retcode=10012, error="broker timeout")
    envelope = _envelope(request, outcome, status="persistence_error")
    envelope["worker_session_id"] = "worker-a"

    record, resolved = gateway._process_envelope(envelope)

    assert resolved is True
    assert record.state is IntentState.UNKNOWN
    assert record.outcome == outcome.to_dict()
    assert gateway._response_backlog == {}


def test_late_worker_error_replaces_gateway_recovery_unknown(tmp_path):
    gateway = _fake_gateway(tmp_path / "intents.sqlite3")
    request = _request(attempt_id="late-worker-error")
    gateway.store.prepare(request)
    gateway.store.begin_dispatch(request, worker_session_id="worker-a")
    gateway.store.recover_dispatching("worker_died_with_unresolved_request")
    envelope = {
        "request_id": request.request_id,
        "intent_id": request.intent_id,
        "attempt_id": request.attempt_id,
        "request": request.to_dict(),
        "status": "worker_error",
        "worker_session_id": "worker-a",
        "outcome": None,
        "error": "worker_exception:ValueError",
    }

    record, resolved = gateway._process_envelope(envelope)

    assert resolved is True
    assert record.state is IntentState.UNKNOWN
    assert record.outcome["error"] == "worker_exception:ValueError"
    assert gateway._response_backlog == {}
