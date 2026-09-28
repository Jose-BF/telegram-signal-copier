from dataclasses import replace
import json
import os
import subprocess
import sys
import time

import pytest

from mt5_protocol import LookupState
from mt5_read_protocol import (
    MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, ReadOperation as Op,
    ReadRequest, ReadResponse, decode_message, encode_message,
)
from mt5_worker import ReadWorker, WorkerConfig
from tests.mt5_read_fakes import FakeMT5


def config(**kwargs):
    return WorkerConfig(7, "demo", ("XAUUSD", "EURUSD"), **kwargs)


def request(op, **params):
    return ReadRequest(op, params)


def run(worker, req):
    return worker.handle(req, time.monotonic() + 5)


def initialized(backend=None, **kwargs):
    worker = ReadWorker(backend or FakeMT5(), config(**kwargs), "session-test")
    assert run(worker, request(Op.INITIALIZE)).state is LookupState.FOUND
    return worker


@pytest.mark.parametrize("op,params", [
    (Op.ACCOUNT, {}), (Op.TERMINAL, {}), (Op.SYMBOL, {"symbol": "XAUUSD"}),
    (Op.TICK, {"symbol": "XAUUSD"}), (Op.POSITIONS, {}), (Op.POSITIONS, {"ticket": 42}),
    (Op.ORDERS, {"symbol": "XAUUSD"}),
    (Op.DEALS, {"date_from": 10, "date_to": 20}),
    (Op.HISTORY_ORDERS, {"date_from": 10, "date_to": 20, "symbol": "XAUUSD"}),
    (Op.TICKS, {"symbol": "XAUUSD", "date_from": 10, "count": 20, "flags": -1}),
    (Op.RATES, {"symbol": "XAUUSD", "timeframe": 5, "start_pos": 0, "count": 20}),
    (Op.PROFIT, {"symbol": "XAUUSD", "action": 0, "volume": 0.01, "price_open": 2500, "price_close": 2501}),
    (Op.MARGIN, {"symbol": "XAUUSD", "action": 1, "volume": 0.01, "price": 2500}),
])
def test_read_roundtrip_and_worker_contract(op, params):
    req = ReadRequest.from_dict(decode_message(encode_message(request(op, **params).to_dict(), MAX_REQUEST_BYTES), MAX_REQUEST_BYTES))
    worker = initialized()
    response = run(worker, req)
    restored = ReadResponse.from_dict(decode_message(encode_message(response.to_dict(), MAX_RESPONSE_BYTES), MAX_RESPONSE_BYTES))
    assert restored == response
    assert response.state is not LookupState.UNKNOWN
    assert response.worker_pid == os.getpid()
    assert response.worker_session_id == "session-test"
    assert response.is_fresh(1)
    calls = worker.backend.calls
    for index, (name, _, _) in enumerate(calls):
        if name != "last_error":
            assert calls[index + 1][0] == "last_error"


@pytest.mark.parametrize("mode,state", [("found", LookupState.FOUND), ("empty", LookupState.EMPTY), ("none", LookupState.UNKNOWN), ("error_empty", LookupState.UNKNOWN)])
def test_empty_is_not_failure(mode, state):
    response = run(initialized(FakeMT5(mode=mode)), request(Op.POSITIONS))
    assert response.state is state
    if state is LookupState.UNKNOWN:
        assert response.value is None and response.native_error[0] < 0
        assert not response.is_fresh(1)


@pytest.mark.parametrize("op,params", [
    ("order_send", {}), ("eval", {}), ("last_error", {}),
    (Op.ACCOUNT, {"unexpected": 1}), (Op.POSITIONS, {"ticket": True}),
    (Op.POSITIONS, {"symbol": "*"}), (Op.POSITIONS, {"ticket": 1, "symbol": "XAUUSD"}),
    (Op.DEALS, {"date_from": 0, "date_to": 86401}),
    (Op.DEALS, {"date_from": 20, "date_to": 10}),
    (Op.DEALS, {"date_from": 0.0, "date_to": 10}),
    (Op.RATES, {"symbol": "XAUUSD", "timeframe": 5, "start_pos": 0, "count": 10001}),
    (Op.PROFIT, {"symbol": "XAUUSD", "action": 0, "volume": float("nan"), "price_open": 1, "price_close": 2}),
])
def test_disallowed_requests_fail_before_any_backend_call(op, params):
    with pytest.raises((ValueError, TypeError)):
        ReadRequest(op, params)


def test_protocol_version_and_unknown_fields_rejected():
    data = request(Op.ACCOUNT).to_dict()
    for version in (0, 2, True, "1"):
        with pytest.raises(ValueError):
            ReadRequest.from_dict({**data, "protocol_version": version})
    with pytest.raises(ValueError):
        ReadRequest.from_dict({**data, "trade_intent": "not-allowed"})


def test_expected_account_and_symbol_validation():
    fake = FakeMT5()
    fake.login = 99
    worker = ReadWorker(fake, config(), "session")
    assert run(worker, request(Op.INITIALIZE)).error == "account_mismatch"
    assert run(worker, request(Op.POSITIONS)).error == "worker_not_initialized"
    worker.cleanup()
    assert fake.calls[-2][0] == "shutdown"
    worker = initialized()
    assert run(worker, request(Op.TICK, symbol="GBPUSD")).error == "symbol_not_allowed"
    assert not any(name == "symbol_info_tick" for name, *_ in worker.backend.calls)


def test_account_switch_during_read_discards_data_and_disables_session():
    worker = initialized(FakeMT5(mode="switch_account"))
    response = run(worker, request(Op.TICK, symbol="XAUUSD"))
    assert response.error == "account_mismatch" and response.value is None
    assert run(worker, request(Op.POSITIONS)).error == "worker_not_initialized"


def test_actual_account_sample_is_validated_before_publication():
    worker = initialized()
    good = worker.backend.account_info()
    wrong = good._replace(login=99, server="other", currency="USD")
    samples = iter((good, wrong, good))
    worker.backend.account_info = lambda: next(samples)
    response = run(worker, request(Op.ACCOUNT))
    assert response.state is LookupState.UNKNOWN
    assert response.error == "account_mismatch"
    assert response.value is None
    assert worker.ready is False


def test_actual_terminal_sample_is_validated_before_publication():
    worker = initialized()
    connected = worker.backend.terminal_info()
    disconnected = connected._replace(connected=False)
    samples = iter((connected, disconnected, connected))
    worker.backend.terminal_info = lambda: next(samples)
    response = run(worker, request(Op.TERMINAL))
    assert response.state is LookupState.UNKNOWN
    assert response.error == "terminal_disconnected"
    assert response.value is None


def test_disconnected_terminal_does_not_report_empty():
    worker = initialized(FakeMT5(mode="empty"))
    worker.backend.connected = False
    assert run(worker, request(Op.POSITIONS)).error == "terminal_disconnected"


def test_initialization_and_shutdown_lifecycle():
    worker = initialized()
    assert run(worker, request(Op.INITIALIZE)).error == "initialization_already_attempted"
    assert run(worker, request(Op.SHUTDOWN)).value == {"shutdown": True}
    assert run(worker, request(Op.POSITIONS)).error == "worker_not_initialized"


def test_failure_captures_last_error_before_any_other_native_call():
    worker = initialized(FakeMT5(mode="none"))
    response = run(worker, request(Op.POSITIONS))
    assert response.native_error == (-10004, "No connection")
    assert [name for name, *_ in worker.backend.calls[-2:]] == ["positions_get", "last_error"]


def test_response_limits_never_truncate_to_a_known_result():
    worker = initialized(max_records=1, max_response_bytes=4096)
    worker.backend.positions_get = lambda: [{"ticket": 1}, {"ticket": 2}]
    assert run(worker, request(Op.POSITIONS)).error == "response_record_limit"
    worker.backend.positions_get = lambda: [{"comment": "x" * 5000}]
    assert run(worker, request(Op.POSITIONS)).error == "response_size_limit"
    worker.backend.positions_get = lambda: [{"a": "x" * 3000, "b": "x" * 3000}]
    assert run(worker, request(Op.POSITIONS)).error == "response_size_limit"
    assert run(worker, request(Op.RATES, symbol="XAUUSD", timeframe=5, start_pos=0, count=2)).error == "request_record_limit"


def test_numpy_arrays_keep_fields_and_empty_semantics():
    import numpy as np
    worker = initialized()
    worker.backend.copy_rates_from_pos = lambda *args: np.array([(12, 4.2)], dtype=[("time", "i8"), ("close", "f8")])
    req = request(Op.RATES, symbol="XAUUSD", timeframe=5, start_pos=0, count=1)
    assert run(worker, req).value == [{"time": 12, "close": 4.2}]
    worker.backend.copy_rates_from_pos = lambda *args: np.array([], dtype=[("time", "i8")])
    assert run(worker, req).state is LookupState.EMPTY


def test_deadline_before_and_after_dispatch():
    worker = initialized(FakeMT5(delay=0.05))
    req = request(Op.TICK, symbol="XAUUSD")
    before = len(worker.backend.calls)
    assert worker.handle(req, time.monotonic() - 1).error == "deadline_before_dispatch"
    assert len(worker.backend.calls) == before
    assert worker.handle(req, time.monotonic() + 0.02).error == "deadline_after_dispatch"


def test_result_is_immutable_and_age_is_capture_not_tick_time():
    response = run(initialized(), request(Op.POSITIONS))
    response.value[0]["ticket"] = 99
    assert response.value[0]["ticket"] == 42
    old = replace(response, started_monotonic=response.started_monotonic - 10,
                  completed_monotonic=response.completed_monotonic - 10)
    assert not old.is_fresh(1)


def test_parent_import_never_imports_native_or_bot_modules():
    code = """
import sys
class Block:
    def find_spec(self, fullname, *args):
        if fullname in {'MetaTrader5', 'executor', 'listener', 'main', 'state', 'journal', 'execution_intents'}:
            raise AssertionError('forbidden import: ' + fullname)
sys.meta_path.insert(0, Block())
import mt5_client, mt5_worker, mt5_read_protocol, mt5_trade_protocol, mt5_trade_worker
print('isolated-import-ok')
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "isolated-import-ok" in result.stdout


def test_copy_tick_query_flags_match_native_public_constants_without_importing_extension():
    for flag in (-1, 1, 2):
        ReadRequest(Op.TICKS, {"symbol": "XAUUSD", "date_from": 10, "count": 1, "flags": flag})
    for flag in (0, 3, 6):
        with pytest.raises(ValueError):
            ReadRequest(Op.TICKS, {"symbol": "XAUUSD", "date_from": 10, "count": 1, "flags": flag})


def test_tick_range_millisecond_clock_reaches_native_backend_exactly():
    worker = initialized()
    response = run(
        worker,
        request(
            Op.TICKS_RANGE,
            symbol="XAUUSD",
            date_from_msc=20_001,
            date_to_msc=20_900,
            flags=-1,
        ),
    )

    assert response.state is LookupState.EMPTY
    call = next(
        call for call in worker.backend.calls if call[0] == "copy_ticks_range"
    )
    assert call[1][1].timestamp() == pytest.approx(20.001)
    assert call[1][2].timestamp() == pytest.approx(20.9)


@pytest.mark.parametrize("method,value,reason", [
    ("initialize", False, "initialize_failed"),
    ("symbol_select", False, "symbol_select_failed"),
    ("symbol_info", {"name": "OTHER"}, "symbol_mismatch"),
])
def test_initialization_failures_never_mark_ready(method, value, reason):
    fake = FakeMT5()
    setattr(fake, method, lambda *args, **kwargs: value)
    worker = ReadWorker(fake, config(), "session")
    assert run(worker, request(Op.INITIALIZE)).error == reason
    assert not worker.ready
    worker.cleanup()


def test_native_exception_is_sanitized_but_immediate_error_is_preserved():
    worker = initialized()
    def fail():
        worker.backend.error = (-1, "native failure")
        raise RuntimeError("password must not leak")
    worker.backend.positions_get = fail
    response = run(worker, request(Op.POSITIONS))
    assert response.error == "native_exception:positions_get:RuntimeError"
    assert response.native_error == (-1, "native failure")
    assert "password" not in json.dumps(response.to_dict())


def test_missing_last_error_and_invalid_collection_stay_unknown():
    worker = initialized()
    worker.backend.positions_get = lambda: [False]
    assert run(worker, request(Op.POSITIONS)).error == "invalid_record_result"
    def missing():
        raise RuntimeError("missing")
    worker.backend.last_error = missing
    assert run(worker, request(Op.POSITIONS)).error == "last_error_unavailable:account_info"


def test_slow_query_is_not_fresh_just_because_it_just_completed():
    response = run(initialized(FakeMT5(delay=0.04)), request(Op.TICK, symbol="XAUUSD"))
    assert not response.is_fresh(0.02)


def test_zero_profit_is_found_not_empty():
    response = run(initialized(), request(Op.PROFIT, action=0, symbol="XAUUSD", volume=0.01,
                                           price_open=2500, price_close=2500))
    assert response.state is LookupState.FOUND and response.value == 0.0


def test_nested_result_budget_and_nonfinite_native_data():
    worker = initialized(max_response_bytes=4096)
    worker.backend.positions_get = lambda: [{"a": ["x" * 1000] * 5}]
    assert run(worker, request(Op.POSITIONS)).error == "response_size_limit"
    worker.backend.positions_get = lambda: [{"profit": float("inf")}]
    assert run(worker, request(Op.POSITIONS)).state is LookupState.UNKNOWN


def test_response_requires_version_and_known_empty_shape():
    response = run(initialized(), request(Op.POSITIONS)).to_dict()
    del response["protocol_version"]
    with pytest.raises(ValueError):
        ReadResponse.from_dict(response)
    response["protocol_version"] = 1
    response["state"] = "EMPTY"
    with pytest.raises(ValueError):
        ReadResponse.from_dict(response)


def test_new_modules_parse_using_python311_grammar_only_not_runtime_compatibility():
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for name in (
        "mt5_read_protocol.py",
        "mt5_trade_protocol.py",
        "mt5_trade_worker.py",
        "mt5_worker.py",
        "mt5_client.py",
        "durable_execution.py",
    ):
        ast.parse((root / name).read_text(encoding="utf-8"), feature_version=(3, 11))
