"""Read-only MT5 owner. No bot startup, journal, strategy or native import on load."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from functools import partial
import importlib
import json
import os
import time
from typing import Any, Callable, Mapping

from mt5_protocol import ImmutableJsonMapping, LookupState
from mt5_owner_lock import OwnerBusyError, OwnerLock, native_owner_lock_path
from mt5_read_protocol import (
    COLLECTION_OPERATIONS, MAX_RECORDS, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES,
    ReadOperation, ReadRequest, ReadResponse, decode_message, encode_message,
    finite_number, integer,
)


@dataclass(frozen=True)
class WorkerConfig:
    expected_login: int
    expected_server: str
    symbols: tuple[str, ...]
    initialize_options: Mapping[str, Any] = field(default_factory=dict, repr=False)
    max_records: int = MAX_RECORDS
    max_response_bytes: int = MAX_RESPONSE_BYTES
    trade_symbols: tuple[str, ...] | None = None
    owner_lock_path: str | None = field(default=None, repr=False)

    def __post_init__(self):
        integer(self.expected_login, "expected_login", 1, 2**64 - 1)
        if not isinstance(self.expected_server, str) or not self.expected_server:
            raise ValueError("expected_server required")
        if not self.symbols or isinstance(self.symbols, str):
            raise ValueError("exact symbols required")
        for symbol in self.symbols:
            ReadRequest(ReadOperation.SYMBOL, {"symbol": symbol})
        object.__setattr__(self, "symbols", tuple(self.symbols))
        trade_symbols = self.symbols if self.trade_symbols is None else self.trade_symbols
        if not trade_symbols or isinstance(trade_symbols, str):
            raise ValueError("exact trade symbols required")
        for symbol in trade_symbols:
            ReadRequest(ReadOperation.SYMBOL, {"symbol": symbol})
            if symbol not in self.symbols:
                raise ValueError("trade symbols must be readable")
        object.__setattr__(self, "trade_symbols", tuple(trade_symbols))
        integer(self.max_records, "max_records", 1, MAX_RECORDS)
        integer(self.max_response_bytes, "max_response_bytes", 4096, MAX_RESPONSE_BYTES)
        options = dict(self.initialize_options)
        if options.keys() - {"path", "login", "password", "server", "timeout", "portable"}:
            raise ValueError("unsupported initialize option")
        if options.get("login", self.expected_login) != self.expected_login:
            raise ValueError("initialize login mismatch")
        if options.get("server", self.expected_server) != self.expected_server:
            raise ValueError("initialize server mismatch")
        object.__setattr__(self, "initialize_options", ImmutableJsonMapping(options))
        if self.owner_lock_path is not None and (
            not isinstance(self.owner_lock_path, str)
            or not os.path.isabs(self.owner_lock_path)
        ):
            raise ValueError("owner_lock_path must be absolute")

    def to_dict(self) -> dict[str, Any]:
        return {
            "expected_login": self.expected_login,
            "expected_server": self.expected_server,
            "symbols": list(self.symbols),
            "trade_symbols": list(self.trade_symbols),
            "initialize_options": self.initialize_options.to_dict(),
            "max_records": self.max_records,
            "max_response_bytes": self.max_response_bytes,
            **({"owner_lock_path": self.owner_lock_path} if self.owner_lock_path is not None else {}),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WorkerConfig":
        expected = {
            "expected_login", "expected_server", "symbols", "initialize_options",
            "max_records", "max_response_bytes", "trade_symbols",
        }
        legacy_expected = expected - {"trade_symbols"}
        if not isinstance(value, Mapping) or set(value) - {"owner_lock_path"} not in {frozenset(expected), frozenset(legacy_expected)}:
            raise ValueError("invalid worker config")
        symbols = value.get("symbols")
        if not isinstance(symbols, list):
            raise ValueError("symbols must be a list")
        return cls(
            expected_login=value.get("expected_login"),
            expected_server=value.get("expected_server"),
            symbols=tuple(symbols),
            trade_symbols=(
                tuple(value["trade_symbols"])
                if "trade_symbols" in value else None
            ),
            initialize_options=value.get("initialize_options"),
            max_records=value.get("max_records"),
            max_response_bytes=value.get("max_response_bytes"),
            owner_lock_path=value.get("owner_lock_path"),
        )


def native_backend():
    """Explicit opt-in factory; only the child calls this, never local tests."""
    import MetaTrader5
    return MetaTrader5


class _ReadFailure(Exception):
    def __init__(self, reason, native_error=None):
        self.reason, self.native_error = reason, native_error


_FORBIDDEN_BACKEND_MODULES = {
    "executor", "listener", "main", "state", "journal", "execution_intents",
}


def resolve_backend_factory(spec: Mapping[str, Any]) -> Callable:
    if not isinstance(spec, Mapping) or set(spec) != {"module", "qualname", "kwargs"}:
        raise ValueError("invalid backend factory spec")
    module_name = spec.get("module")
    qualname = spec.get("qualname")
    kwargs = spec.get("kwargs")
    if (
        not isinstance(module_name, str)
        or not module_name
        or module_name.split(".", 1)[0] in _FORBIDDEN_BACKEND_MODULES
        or not isinstance(qualname, str)
        or not qualname
        or "<locals>" in qualname
        or not isinstance(kwargs, Mapping)
    ):
        raise ValueError("unsafe backend factory spec")
    target = importlib.import_module(module_name)
    for part in qualname.split("."):
        if not part or part.startswith("_"):
            raise ValueError("unsafe backend factory attribute")
        target = getattr(target, part)
    if not callable(target):
        raise ValueError("backend factory is not callable")
    frozen_kwargs = ImmutableJsonMapping(kwargs).to_dict()
    return partial(target, **frozen_kwargs) if frozen_kwargs else target


def _plain(value: Any, *, max_records: int, max_bytes: int, depth=0, budget=None) -> Any:
    budget = [max_bytes] if budget is None else budget
    def consume(size):
        budget[0] -= size
        if budget[0] < 0:
            raise _ReadFailure("response_size_limit")
    if depth > 12:
        raise _ReadFailure("response_depth_limit")
    if value is None or type(value) in {bool, int, float, str}:
        if isinstance(value, str) and len(value) > max_bytes:
            raise _ReadFailure("response_size_limit")
        if type(value) is float:
            finite_number(value, "native number")
        consume(len(json.dumps(value, ensure_ascii=True, allow_nan=False)))
        return value
    if hasattr(value, "_asdict"):
        value = value._asdict()
    elif getattr(getattr(value, "dtype", None), "names", None) and getattr(value, "ndim", 0) == 0:
        value = {name: value[name] for name in value.dtype.names}
    elif getattr(value, "ndim", None) == 0 and hasattr(value, "item"):
        return _plain(value.item(), max_records=max_records, max_bytes=max_bytes, depth=depth + 1, budget=budget)
    convert = lambda item: _plain(item, max_records=max_records, max_bytes=max_bytes, depth=depth + 1, budget=budget)
    if isinstance(value, Mapping):
        if len(value) > 512 or any(not isinstance(k, str) for k in value):
            raise _ReadFailure("invalid_record_fields")
        consume(2 + len(value) * 2)
        for key in value:
            if len(key) > max_bytes:
                raise _ReadFailure("response_size_limit")
            consume(len(json.dumps(key, ensure_ascii=True)))
        return {key: convert(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)) or getattr(value, "ndim", None) == 1:
        if len(value) > max_records:
            raise _ReadFailure("response_record_limit")
        consume(2 + len(value))
        return [convert(item) for item in value]
    raise _ReadFailure("unsupported_native_result")


class ReadWorker:
    """Synchronous dispatcher, used only inside the isolated child (or a test double)."""

    def __init__(self, backend, config: WorkerConfig, session_id: str):
        self.backend, self.config, self.session_id = backend, config, session_id
        self.ready = False
        self.initialization_attempted = False
        self.recovery_allowed = False
        self._owner_lock = OwnerLock(config.owner_lock_path) if config.owner_lock_path else None

    def _invoke(self, name, *args, **kwargs):
        failure = None
        try:
            value = getattr(self.backend, name)(*args, **kwargs)
        except Exception as exc:
            failure = f"native_exception:{name}:{type(exc).__name__}"
            value = None
        # No other native call is allowed between the operation and last_error.
        try:
            code, message = self.backend.last_error()
            code = integer(code, "native error code", -2**31, 2**31 - 1)
            error = (code, str(message)[:1024])
        except Exception:
            raise _ReadFailure(f"last_error_unavailable:{name}") from None
        if failure:
            raise _ReadFailure(failure, error)
        if (name != "shutdown" and value is None) or code != 1:
            raise _ReadFailure(f"native_read_failed:{name}", error)
        return value, error

    def _validate_account(self, account, native_error=None):
        login = account.get("login") if isinstance(account, Mapping) else getattr(account, "login", None)
        server = account.get("server") if isinstance(account, Mapping) else getattr(account, "server", None)
        if login != self.config.expected_login or server != self.config.expected_server:
            self.ready = False
            self.recovery_allowed = False
            raise _ReadFailure("account_mismatch", native_error)

    def _validate_terminal(self, terminal, native_error=None):
        connected = terminal.get("connected") if isinstance(terminal, Mapping) else getattr(terminal, "connected", None)
        if connected is not True:
            self.ready = False
            self.recovery_allowed = self.initialization_attempted
            raise _ReadFailure("terminal_disconnected", native_error)

    def _account_guard(self):
        account, _ = self._invoke("account_info")
        self._validate_account(account)
        terminal, _ = self._invoke("terminal_info")
        self._validate_terminal(terminal)

    def _ensure_ready(self):
        if not self.initialization_attempted:
            raise _ReadFailure("worker_not_initialized")
        if not self.ready and not self.recovery_allowed:
            raise _ReadFailure("worker_not_initialized")
        self._account_guard()
        self.ready = True
        self.recovery_allowed = False

    def _initialize(self):
        if self.initialization_attempted:
            raise _ReadFailure("initialization_already_attempted")
        if self._owner_lock is not None:
            try:
                self._owner_lock.acquire()
            except OwnerBusyError:
                raise _ReadFailure("owner_already_active") from None
            except OSError:
                raise _ReadFailure("owner_lock_unavailable") from None
        self.initialization_attempted = True
        options = self.config.initialize_options.to_dict()
        options.setdefault("login", self.config.expected_login)
        options.setdefault("server", self.config.expected_server)
        path = options.pop("path", None)
        initialized, error = self._invoke("initialize", *((path,) if path else ()), **options)
        if initialized is not True:
            raise _ReadFailure("initialize_failed", error)
        self._account_guard()
        for symbol in self.config.symbols:
            selected, error = self._invoke("symbol_select", symbol, True)
            if selected is not True:
                raise _ReadFailure("symbol_select_failed", error)
            info, error = self._invoke("symbol_info", symbol)
            name = info.get("name") if isinstance(info, Mapping) else getattr(info, "name", None)
            if name != symbol:
                raise _ReadFailure("symbol_mismatch", error)
        self.ready = True
        self.recovery_allowed = False
        return {"ready": True, "symbols": list(self.config.symbols)}, (1, "Success")

    def handle(self, request: ReadRequest, deadline: float) -> ReadResponse:
        started = time.monotonic()
        data, error, native_error, state = None, None, None, LookupState.UNKNOWN
        try:
            finite_number(deadline, "deadline", positive=True)
            if started >= deadline:
                raise _ReadFailure("deadline_before_dispatch")
            operation, params = request.operation, request.params.to_dict()
            if params.get("symbol") is not None and params["symbol"] not in self.config.symbols:
                raise _ReadFailure("symbol_not_allowed")
            if params.get("count", 0) > self.config.max_records:
                raise _ReadFailure("request_record_limit")
            if operation is ReadOperation.INITIALIZE:
                data, native_error = self._initialize()
            elif operation is ReadOperation.SHUTDOWN:
                self.ready = False
                self.recovery_allowed = False
                if self.initialization_attempted:
                    self._invoke("shutdown")
                self.initialization_attempted = False
                data, native_error = {"shutdown": True}, (1, "Success")
            else:
                self._ensure_ready()
                if time.monotonic() >= deadline:
                    raise _ReadFailure("deadline_before_dispatch")
                args, kwargs = self._arguments(operation, params)
                native_name = (
                    "history_deals_get"
                    if operation is ReadOperation.DEALS_POSITION
                    else operation.value
                )
                raw, native_error = self._invoke(native_name, *args, **kwargs)
                if operation is ReadOperation.ACCOUNT:
                    self._validate_account(raw, native_error)
                elif operation is ReadOperation.TERMINAL:
                    self._validate_terminal(raw, native_error)
                elif operation is ReadOperation.SYMBOL:
                    name = raw.get("name") if isinstance(raw, Mapping) else getattr(raw, "name", None)
                    if name != params["symbol"]:
                        raise _ReadFailure("symbol_mismatch", native_error)
                data = _plain(raw, max_records=self.config.max_records,
                              max_bytes=self.config.max_response_bytes)
                self._account_guard()
            if time.monotonic() >= deadline:
                raise _ReadFailure("deadline_after_dispatch", native_error)
            if operation in COLLECTION_OPERATIONS and not isinstance(data, list):
                raise _ReadFailure("invalid_collection_result", native_error)
            if operation in COLLECTION_OPERATIONS and any(not isinstance(row, dict) or not row for row in data):
                raise _ReadFailure("invalid_record_result", native_error)
            if operation in {ReadOperation.PROFIT, ReadOperation.MARGIN}:
                finite_number(data, "calculation result")
            elif operation not in COLLECTION_OPERATIONS and not isinstance(data, dict):
                raise _ReadFailure("invalid_record_result", native_error)
            state = LookupState.EMPTY if operation in COLLECTION_OPERATIONS and not data else LookupState.FOUND
        except _ReadFailure as exc:
            data, error, native_error = None, exc.reason, exc.native_error
        except Exception as exc:
            data, error = None, f"worker_exception:{type(exc).__name__}"
        response = ReadResponse(
            request.request_id, request.operation, state, self.session_id, os.getpid(),
            started, time.monotonic(), time.time_ns(), {"data": data}, error, native_error,
        )
        try:
            encode_message(response.to_dict(), self.config.max_response_bytes)
        except (ValueError, TypeError, OverflowError):
            response = ReadResponse(
                request.request_id, request.operation, LookupState.UNKNOWN,
                self.session_id, os.getpid(), started, time.monotonic(), time.time_ns(),
                error="response_size_limit", native_error=native_error,
            )
        return response

    def _arguments(self, operation, params):
        if operation in {ReadOperation.SYMBOL, ReadOperation.TICK}:
            return (params["symbol"],), {}
        if operation in {ReadOperation.DEALS, ReadOperation.HISTORY_ORDERS}:
            return (params["date_from"], params["date_to"]), ({"group": params["symbol"]} if "symbol" in params else {})
        if operation is ReadOperation.DEALS_POSITION:
            return (), {"position": params["position"]}
        if operation is ReadOperation.RATES:
            return tuple(params[k] for k in ("symbol", "timeframe", "start_pos", "count")), {}
        if operation is ReadOperation.TICKS:
            start_msc = (
                params["date_from_msc"]
                if "date_from_msc" in params
                else params["date_from"] * 1000
            )
            stamp = datetime.fromtimestamp(
                start_msc / 1000.0,
                tz=timezone.utc,
            )
            return (params["symbol"], stamp, params["count"], params["flags"]), {}
        if operation is ReadOperation.TICKS_RANGE:
            start_msc = (
                params["date_from_msc"]
                if "date_from_msc" in params
                else params["date_from"] * 1000
            )
            end_msc = (
                params["date_to_msc"]
                if "date_to_msc" in params
                else params["date_to"] * 1000
            )
            start = datetime.fromtimestamp(
                start_msc / 1000.0,
                tz=timezone.utc,
            )
            end = datetime.fromtimestamp(
                end_msc / 1000.0,
                tz=timezone.utc,
            )
            return (params["symbol"], start, end, params["flags"]), {}
        if operation in {ReadOperation.PROFIT, ReadOperation.MARGIN}:
            fields = ("action", "symbol", "volume", "price_open", "price_close") if operation is ReadOperation.PROFIT else ("action", "symbol", "volume", "price")
            return tuple(params[k] for k in fields), {}
        return (), params

    def cleanup(self):
        try:
            if self.initialization_attempted:
                self.ready = False
                self.recovery_allowed = False
                try:
                    self._invoke("shutdown")
                except Exception:
                    pass
                self.initialization_attempted = False
        finally:
            if self._owner_lock is not None:
                self._owner_lock.close()


def worker_stream_main(input_stream, output_stream, *, lifetime_check=None) -> int:
    """Minimal subprocess entry protocol; imports no application entrypoint."""
    worker = None
    try:
        boot = decode_message(_read_frame(input_stream, MAX_REQUEST_BYTES), MAX_REQUEST_BYTES)
        if set(boot) != {"type", "session_id", "config", "backend"} or boot["type"] != "bootstrap":
            raise ValueError("invalid worker bootstrap")
        session_id = boot["session_id"]
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("invalid worker session")
        config = WorkerConfig.from_dict(boot["config"])
        if boot["backend"].get("module") == "mt5_worker" and boot["backend"].get("qualname") == "native_backend":
            # Native workers cannot bypass the shared lock through a new checkout.
            config = replace(config, owner_lock_path=str(native_owner_lock_path()))
        if lifetime_check is not None:
            lifetime_check()
        backend_factory = resolve_backend_factory(boot["backend"])
        worker = ReadWorker(backend_factory(), config, session_id)
        from mt5_protocol import BrokerRequest
        from mt5_trade_worker import TradeWorker

        trade_worker = TradeWorker(worker)
        while True:
            envelope = decode_message(_read_frame(input_stream, MAX_REQUEST_BYTES), MAX_REQUEST_BYTES)
            if lifetime_check is not None:
                lifetime_check()
            if envelope.get("session_id") != session_id:
                raise ValueError("invalid worker envelope")
            phase = envelope.get("phase")
            if phase is None:
                if set(envelope) != {"request", "deadline", "session_id"}:
                    raise ValueError("invalid worker envelope")
                request = ReadRequest.from_dict(envelope["request"])
                response = worker.handle(request, envelope["deadline"])
            elif phase == "prepare":
                if set(envelope) != {"phase", "request", "deadline", "session_id"}:
                    raise ValueError("invalid trade prepare envelope")
                response = trade_worker.prepare(
                    BrokerRequest.from_dict(envelope["request"]),
                    envelope["deadline"],
                )
            elif phase == "commit":
                if set(envelope) != {"phase", "request_id", "deadline", "session_id"}:
                    raise ValueError("invalid trade commit envelope")
                response = trade_worker.commit(envelope["request_id"], envelope["deadline"])
            elif phase == "abort":
                if set(envelope) != {"phase", "request_id", "session_id"}:
                    raise ValueError("invalid trade abort envelope")
                response = {"aborted": trade_worker.abort(envelope["request_id"])}
            else:
                raise ValueError("invalid trade phase")
            response_value = response.to_dict() if hasattr(response, "to_dict") else response
            _write_frame(output_stream, encode_message(response_value, config.max_response_bytes))
            if phase is None and request.operation is ReadOperation.SHUTDOWN:
                return 0
    except (EOFError, OSError, ValueError, TypeError, AttributeError):
        return 2
    finally:
        if worker is not None:
            worker.cleanup()


def _read_frame(stream, limit: int) -> bytes:
    raw = stream.readline(limit + 2)
    if not raw:
        raise EOFError("worker stream closed")
    if len(raw) > limit + 1 or not raw.endswith(b"\n"):
        raise ValueError("message_size_limit")
    return raw[:-1]


def _write_frame(stream, payload: bytes) -> None:
    stream.write(payload + b"\n")
    stream.flush()
