"""Compatibility boundary backed by the single isolated MT5 owner."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
import threading
import time
from typing import Any

from durable_execution import DurableExecutionService
from mt5_protocol import LookupState
from mt5_read_protocol import ReadOperation, ReadRequest


MAX_RUNTIME_SNAPSHOTS = 256
_SNAPSHOT_OPERATIONS = frozenset({
    ReadOperation.ACCOUNT,
    ReadOperation.TERMINAL,
    ReadOperation.TICK,
    ReadOperation.POSITIONS,
})


def _timestamp(value: Any) -> int:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp())
    return int(value)


def _timestamp_msc(value: Any) -> int:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        delta = value.astimezone(timezone.utc) - epoch
        return (
            delta.days * 86_400_000
            + delta.seconds * 1000
            + delta.microseconds // 1000
        )
    return int(float(value) * 1000)


def _native_shape(value: Any) -> Any:
    if isinstance(value, dict):
        return SimpleNamespace(**{
            key: _native_shape(item) for key, item in value.items()
        })
    if isinstance(value, list):
        return tuple(_native_shape(item) for item in value)
    return value


class MT5Runtime:
    """Own one client, its durable service and read freshness evidence."""

    def __init__(self, client):
        self.client = client
        self.service = DurableExecutionService(client)
        self.loop: asyncio.AbstractEventLoop | None = None
        self._snapshots: dict[tuple[str, tuple], dict[str, Any]] = {}
        self._snapshot_lock = threading.Lock()

    async def start(self, *, timeout: float = 10.0):
        self.loop = asyncio.get_running_loop()
        response = await self.client.start(timeout=timeout)
        self._remember(response, {})
        if response.state is LookupState.FOUND:
            mt5.install(self)
        return response

    async def read(self, operation: ReadOperation, params=None, *, timeout=1.0):
        query = params or {}
        response = await self.client.read(
            ReadRequest(operation, query), timeout=timeout,
        )
        self._remember(response, query)
        return response

    async def close(self) -> None:
        if mt5.runtime is self:
            mt5.install(None)
        await self.client.close()

    def snapshot(self, operation: str | ReadOperation, params=None) -> dict[str, Any] | None:
        name = operation.value if isinstance(operation, ReadOperation) else str(operation)
        key = (name, self._snapshot_scope(params or {}))
        with self._snapshot_lock:
            value = self._snapshots.get(key)
            if value is None:
                return None
            current = dict(value)
        current["age_seconds"] = max(
            0.0, time.monotonic() - current["completed_monotonic"]
        )
        return current

    @staticmethod
    def _snapshot_scope(params) -> tuple:
        return tuple(sorted((str(key), repr(value)) for key, value in params.items()))

    def _remember(self, response, params) -> None:
        if response.operation not in _SNAPSHOT_OPERATIONS:
            return
        snapshot = {
            "state": response.state.value,
            "error": response.error,
            "native_error": response.native_error,
            "worker_session_id": response.worker_session_id,
            "worker_pid": response.worker_pid,
            "completed_monotonic": response.completed_monotonic,
            "completed_utc_ns": response.completed_utc_ns,
        }
        snapshot["value"] = (
            response.value
            if response.state is not LookupState.UNKNOWN
            else None
        )
        key = (
            response.operation.value,
            self._snapshot_scope(params),
        )
        with self._snapshot_lock:
            if key not in self._snapshots:
                while len(self._snapshots) >= MAX_RUNTIME_SNAPSHOTS:
                    removable = next(
                        (
                            existing for existing in self._snapshots
                            if not self._critical_snapshot_key(existing)
                        ),
                        None,
                    )
                    if removable is None:
                        return
                    self._snapshots.pop(removable, None)
            self._snapshots[key] = snapshot

    @staticmethod
    def _critical_snapshot_key(key: tuple[str, tuple]) -> bool:
        operation, scope = key
        return (
            operation in {
                ReadOperation.ACCOUNT.value,
                ReadOperation.TERMINAL.value,
            }
            or (operation == ReadOperation.POSITIONS.value and not scope)
        )


class MT5RuntimeProxy:
    """Native-shaped synchronous reads for legacy pure calculation helpers."""

    ACCOUNT_TRADE_MODE_DEMO = 0
    ACCOUNT_TRADE_MODE_CONTEST = 1
    ACCOUNT_TRADE_MODE_REAL = 2
    COPY_TICKS_ALL = -1
    COPY_TICKS_INFO = 1
    COPY_TICKS_TRADE = 2
    DEAL_ENTRY_IN = 0
    DEAL_ENTRY_OUT = 1
    DEAL_ENTRY_INOUT = 2
    DEAL_ENTRY_OUT_BY = 3
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TYPE_BUY_LIMIT = 2
    ORDER_TYPE_SELL_LIMIT = 3
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_PENDING = 5
    TRADE_ACTION_SLTP = 6
    TRADE_ACTION_MODIFY = 7
    TRADE_ACTION_REMOVE = 8
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_INVALID = 10013
    TRADE_RETCODE_INVALID_STOPS = 10016
    TRADE_RETCODE_MARKET_CLOSED = 10018
    TIMEFRAME_M5 = 5

    def __init__(self):
        self.runtime: MT5Runtime | None = None
        self._last_error = (1, "Success")
        self._lock = threading.Lock()

    def install(self, runtime: MT5Runtime | None) -> None:
        with self._lock:
            self.runtime = runtime
            self._last_error = (1, "Success")

    def _read(self, operation: ReadOperation, params=None, *, timeout=1.0):
        runtime = self.runtime
        if runtime is None or runtime.loop is None:
            raise RuntimeError("MT5 is owned by the isolated worker")
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is runtime.loop:
            raise RuntimeError("synchronous MT5 read on the owner event loop")
        future = asyncio.run_coroutine_threadsafe(
            runtime.read(operation, params, timeout=timeout), runtime.loop,
        )
        response = future.result(timeout=timeout + 1.0)
        with self._lock:
            self._last_error = response.native_error or (
                1 if response.state is not LookupState.UNKNOWN else -1,
                response.error or "Success",
            )
        if response.state is LookupState.UNKNOWN:
            return None
        return _native_shape(response.value)

    def last_error(self):
        with self._lock:
            return self._last_error

    def initialize(self, *args, **kwargs):
        raise RuntimeError("MT5 lifecycle is owned by the isolated worker")

    def login(self, *args, **kwargs):
        raise RuntimeError("MT5 lifecycle is owned by the isolated worker")

    def shutdown(self):
        raise RuntimeError("MT5 lifecycle is owned by the isolated worker")

    def order_send(self, _request):
        raise RuntimeError("broker effects require durable execution")

    def symbol_select(self, symbol, enabled=True):
        if not enabled:
            raise RuntimeError("symbol ownership belongs to the isolated worker")
        return self.symbol_info(symbol) is not None

    def account_info(self):
        return self._read(ReadOperation.ACCOUNT)

    def terminal_info(self):
        return self._read(ReadOperation.TERMINAL)

    def symbol_info(self, symbol):
        return self._read(ReadOperation.SYMBOL, {"symbol": symbol})

    def symbol_info_tick(self, symbol):
        return self._read(ReadOperation.TICK, {"symbol": symbol})

    def positions_get(self, **kwargs):
        return self._read(ReadOperation.POSITIONS, kwargs)

    def orders_get(self, **kwargs):
        return self._read(ReadOperation.ORDERS, kwargs)

    def history_deals_get(self, *args, **kwargs):
        if "position" in kwargs:
            return self._read(
                ReadOperation.DEALS_POSITION,
                {"position": int(kwargs["position"])},
            )
        if len(args) < 2:
            raise ValueError("history range requires start and end")
        params = {"date_from": _timestamp(args[0]), "date_to": _timestamp(args[1])}
        group = kwargs.get("group")
        if group:
            params["symbol"] = str(group)
        return self._read(ReadOperation.DEALS, params)

    def history_orders_get(self, start, end, **kwargs):
        params = {"date_from": _timestamp(start), "date_to": _timestamp(end)}
        group = kwargs.get("group")
        if group:
            params["symbol"] = str(group)
        return self._read(ReadOperation.HISTORY_ORDERS, params)

    def copy_rates_from_pos(self, symbol, timeframe, start_pos, count):
        return self._read(ReadOperation.RATES, {
            "symbol": symbol, "timeframe": int(timeframe),
            "start_pos": int(start_pos), "count": int(count),
        })

    def copy_ticks_from(self, symbol, start, count, flags):
        return self._read(ReadOperation.TICKS, {
            "symbol": symbol, "date_from_msc": _timestamp_msc(start),
            "count": int(count), "flags": int(flags),
        })

    def copy_ticks_range(self, symbol, start, end, flags):
        return self._read(ReadOperation.TICKS_RANGE, {
            "symbol": symbol, "date_from_msc": _timestamp_msc(start),
            "date_to_msc": _timestamp_msc(end), "flags": int(flags),
        })

    def order_calc_profit(self, action, symbol, volume, price_open, price_close):
        return self._read(ReadOperation.PROFIT, {
            "action": int(action), "symbol": symbol, "volume": float(volume),
            "price_open": float(price_open), "price_close": float(price_close),
        })

    def order_calc_margin(self, action, symbol, volume, price):
        return self._read(ReadOperation.MARGIN, {
            "action": int(action), "symbol": symbol, "volume": float(volume),
            "price": float(price),
        })


mt5 = MT5RuntimeProxy()
