"""Composite trade preparation and dispatch inside the isolated MT5 owner."""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
import time
from typing import Any, Mapping

from mt5_protocol import BrokerOutcome, BrokerRequest, IntentState, classify_broker_result
from mt5_trade_protocol import TradePreparation, TradeResponse, validate_trade_request
from mt5_worker import ReadWorker, _ReadFailure, _plain


@dataclass(frozen=True)
class _PendingTrade:
    request: BrokerRequest
    native_request: Mapping[str, Any]
    evidence: Mapping[str, Any]
    prepared_monotonic: float


class TradeWorker:
    """Two-phase trade dispatcher sharing the read worker's single backend."""

    def __init__(self, read_worker: ReadWorker):
        self.reader = read_worker
        self._pending: dict[str, _PendingTrade] = {}

    def prepare(self, request: BrokerRequest, deadline: float) -> TradePreparation:
        validate_trade_request(request)
        started = time.monotonic()
        existing = self._pending.get(request.request_id)
        if existing is not None:
            if existing.request != request:
                raise ValueError("duplicate request_id with different trade payload")
            return self._preparation(existing)
        native_request = None
        evidence: dict[str, Any] = {}
        error = None
        try:
            self.reader._ensure_ready()
            if started >= deadline:
                raise _ReadFailure("deadline_before_trade_prepare")
            payload = dict(request.payload)
            symbol = payload["symbol"]
            expected_fingerprint = (
                f"{self.reader.config.expected_server}/"
                f"{self.reader.config.expected_login}"
            )
            if request.intent_key.account_fingerprint != expected_fingerprint:
                raise _ReadFailure("account_fingerprint_mismatch")
            if symbol not in self.reader.config.trade_symbols:
                raise _ReadFailure("symbol_not_allowed")
            native_request, evidence = self._build(request.intent_key.operation, payload)
            if time.monotonic() >= deadline:
                raise _ReadFailure("deadline_after_trade_prepare")
        except _ReadFailure as exc:
            error = exc.reason
        except Exception as exc:
            error = f"trade_prepare_exception:{type(exc).__name__}"
        if error is not None:
            return TradePreparation(
                request.request_id,
                request.intent_id,
                request.attempt_id,
                request.action_id,
                self.reader.session_id,
                os.getpid(),
                time.monotonic(),
                evidence=evidence,
                error=error,
            )
        pending = _PendingTrade(
            request=request,
            native_request=native_request,
            evidence=evidence,
            prepared_monotonic=time.monotonic(),
        )
        self._pending[request.request_id] = pending
        return self._preparation(pending)

    def commit(self, request_id: str, deadline: float) -> TradeResponse:
        pending = self._pending.pop(request_id)
        request = pending.request
        started = time.monotonic()
        native_error = None
        if started >= deadline:
            outcome = BrokerOutcome(IntentState.UNKNOWN, error="deadline_before_order_send")
        else:
            try:
                self.reader._account_guard()
                if time.monotonic() >= deadline:
                    raise _ReadFailure("deadline_before_order_send")
                try:
                    broker_exception = None
                    result = self.reader.backend.order_send(dict(pending.native_request))
                except Exception as exc:
                    result = None
                    broker_exception = exc
                try:
                    code, message = self.reader.backend.last_error()
                    native_error = (int(code), str(message)[:1024])
                except Exception:
                    native_error = None
                    outcome = BrokerOutcome(
                        IntentState.UNKNOWN,
                        error="last_error_unavailable:order_send",
                    )
                else:
                    if broker_exception is not None:
                        outcome = BrokerOutcome(
                            IntentState.UNKNOWN,
                            error=f"broker_exception:{type(broker_exception).__name__}",
                        )
                    elif result is None:
                        outcome = BrokerOutcome(
                            IntentState.UNKNOWN,
                            error="broker_result_missing",
                        )
                    else:
                        plain = _plain(
                            result,
                            max_records=self.reader.config.max_records,
                            max_bytes=self.reader.config.max_response_bytes,
                        )
                        outcome = classify_broker_result(plain)
            except _ReadFailure as exc:
                outcome = BrokerOutcome(IntentState.UNKNOWN, error=exc.reason)
            except Exception as exc:
                outcome = BrokerOutcome(
                    IntentState.UNKNOWN,
                    error=f"trade_dispatch_exception:{type(exc).__name__}",
                )
        return TradeResponse(
            request.request_id,
            request.intent_id,
            request.attempt_id,
            request.action_id,
            self.reader.session_id,
            os.getpid(),
            started,
            time.monotonic(),
            outcome,
            native_error,
        )

    def abort(self, request_id: str) -> bool:
        return self._pending.pop(request_id, None) is not None

    def _preparation(self, pending: _PendingTrade) -> TradePreparation:
        request = pending.request
        return TradePreparation(
            request.request_id,
            request.intent_id,
            request.attempt_id,
            request.action_id,
            self.reader.session_id,
            os.getpid(),
            pending.prepared_monotonic,
            pending.native_request,
            pending.evidence,
        )

    def _build(self, operation: str, payload: Mapping[str, Any]):
        if operation == "OPEN_MARKET":
            return self._open_market(payload)
        if operation == "PLACE_LIMIT":
            return self._place_limit(payload)
        if operation == "MODIFY_SLTP":
            return self._modify_sltp(payload)
        if operation == "CLOSE_POSITION":
            return self._close_position(payload)
        if operation == "CANCEL_PENDING":
            return self._cancel_pending(payload)
        raise _ReadFailure("unsupported_trade_operation")

    def _open_market(self, payload):
        symbol = payload["symbol"]
        try:
            tick, _ = self.reader._invoke("symbol_info_tick", symbol)
        except _ReadFailure as exc:
            raise _ReadFailure("source_tick_unavailable", exc.native_error) from None
        info, _ = self.reader._invoke("symbol_info", symbol)
        direction = payload["direction"]
        price = float(_field(tick, "ask" if direction == "BUY" else "bid"))
        sl = payload["sl"]
        if sl is None and payload["loss_budget"] is not None:
            sl = self._loss_stop_price(
                direction,
                symbol,
                float(payload["volume"]),
                price,
                float(payload["loss_budget"]),
                info,
            )
        if sl is not None:
            sl = _legal_sl(direction, float(sl), tick, info)
        if sl is None and payload["protection_policy"] == "required":
            raise _ReadFailure("required_protection_unavailable")
        request = {
            "action": self._constant("TRADE_ACTION_DEAL"),
            "symbol": symbol,
            "volume": float(payload["volume"]),
            "type": self._constant(
                "ORDER_TYPE_BUY" if direction == "BUY" else "ORDER_TYPE_SELL"
            ),
            "price": price,
            "deviation": payload["deviation"],
            "magic": payload["magic"],
            "comment": payload["comment"],
            "type_time": self._constant("ORDER_TIME_GTC"),
            "type_filling": self._constant("ORDER_FILLING_IOC"),
        }
        if sl is not None:
            request["sl"] = sl
        if payload["tp"] is not None:
            request["tp"] = float(payload["tp"])
        return request, {
            "source_tick": _plain(
                tick,
                max_records=self.reader.config.max_records,
                max_bytes=self.reader.config.max_response_bytes,
            ),
            "symbol_contract": _plain(
                info,
                max_records=self.reader.config.max_records,
                max_bytes=self.reader.config.max_response_bytes,
            ),
            "requested_sl": payload["sl"],
            "effective_sl": sl,
        }

    def _place_limit(self, payload):
        direction = payload["direction"]
        request = {
            "action": self._constant("TRADE_ACTION_PENDING"),
            "symbol": payload["symbol"],
            "volume": float(payload["volume"]),
            "type": self._constant(
                "ORDER_TYPE_BUY_LIMIT" if direction == "BUY" else "ORDER_TYPE_SELL_LIMIT"
            ),
            "price": float(payload["price"]),
            "deviation": payload["deviation"],
            "magic": payload["magic"],
            "comment": payload["comment"],
            "type_time": self._constant("ORDER_TIME_GTC"),
            "type_filling": self._constant("ORDER_FILLING_IOC"),
        }
        if payload["sl"] is not None:
            request["sl"] = float(payload["sl"])
        if payload["tp"] is not None:
            request["tp"] = float(payload["tp"])
        return request, {}

    def _modify_sltp(self, payload):
        ticket = payload["ticket"]
        positions, _ = self.reader._invoke("positions_get", ticket=ticket)
        if positions:
            position = positions[0]
            self._guard_owned(position, payload)
            current_sl = float(_field(position, "sl", 0.0) or 0.0)
            sl = payload["new_sl"] if payload["new_sl"] is not None else current_sl
            tp = payload["new_tp"] if payload["new_tp"] is not None else _field(position, "tp", 0.0)
            if payload["new_sl"] is not None:
                tick, _ = self.reader._invoke("symbol_info_tick", payload["symbol"])
                info, _ = self.reader._invoke("symbol_info", payload["symbol"])
                gap = max(
                    float(_field(info, "trade_stops_level", 0.0) or 0.0),
                    float(_field(info, "trade_freeze_level", 0.0) or 0.0),
                ) * float(_field(info, "point", 0.0) or 0.0)
                is_buy = _field(position, "type") == self._constant("ORDER_TYPE_BUY")
                market_side = float(_field(tick, "bid" if is_buy else "ask"))
                valid = (
                    float(sl) < market_side - gap
                    if is_buy
                    else float(sl) > market_side + gap
                )
                if not valid:
                    raise _ReadFailure("requested_sl_waits_for_market")
            request = {
                "action": self._constant("TRADE_ACTION_SLTP"),
                "position": ticket,
                "sl": float(sl or 0.0),
                "tp": float(tp or 0.0),
            }
            return request, {
                "observed_kind": "position",
                "observed_ticket": ticket,
                "effective_sl": float(sl or 0.0),
                "effective_tp": float(tp or 0.0),
            }
        orders, _ = self.reader._invoke("orders_get", ticket=ticket)
        if not orders:
            raise _ReadFailure("ticket_not_found")
        order = orders[0]
        self._guard_owned(order, payload)
        sl = payload["new_sl"] if payload["new_sl"] is not None else _field(order, "sl", 0.0)
        tp = payload["new_tp"] if payload["new_tp"] is not None else _field(order, "tp", 0.0)
        request = {
            "action": self._constant("TRADE_ACTION_MODIFY"),
            "order": ticket,
            "price": float(_field(order, "price_open")),
            "sl": float(sl or 0.0),
            "tp": float(tp or 0.0),
        }
        return request, {"observed_kind": "order", "observed_ticket": ticket}

    def _close_position(self, payload):
        ticket = payload["ticket"]
        positions, _ = self.reader._invoke("positions_get", ticket=ticket)
        if not positions:
            raise _ReadFailure("ticket_not_found")
        position = positions[0]
        self._guard_owned(position, payload)
        position_type = _field(position, "type")
        buying = position_type != self._constant("POSITION_TYPE_BUY")
        tick, _ = self.reader._invoke("symbol_info_tick", payload["symbol"])
        price = float(_field(tick, "ask" if buying else "bid"))
        request = {
            "action": self._constant("TRADE_ACTION_DEAL"),
            "symbol": payload["symbol"],
            "volume": float(_field(position, "volume")),
            "type": self._constant("ORDER_TYPE_BUY" if buying else "ORDER_TYPE_SELL"),
            "position": ticket,
            "price": price,
            "deviation": payload["deviation"],
            "magic": payload["expected_magic"],
            "comment": "bot_close",
            "type_time": self._constant("ORDER_TIME_GTC"),
            "type_filling": self._constant("ORDER_FILLING_IOC"),
        }
        return request, {"observed_kind": "position", "observed_ticket": ticket}

    def _cancel_pending(self, payload):
        ticket = payload["ticket"]
        orders, _ = self.reader._invoke("orders_get", ticket=ticket)
        if not orders:
            raise _ReadFailure("ticket_not_found")
        order = orders[0]
        self._guard_owned(order, payload)
        return {
            "action": self._constant("TRADE_ACTION_REMOVE"),
            "order": ticket,
        }, {"observed_kind": "order", "observed_ticket": ticket}

    def _guard_owned(self, value, payload):
        if _field(value, "symbol") != payload["symbol"]:
            raise _ReadFailure("symbol_mismatch")
        if _field(value, "magic") != payload["expected_magic"]:
            raise _ReadFailure("magic_mismatch")

    def _constant(self, name: str) -> int:
        value = getattr(self.reader.backend, name, None)
        if type(value) is not int:
            raise _ReadFailure(f"native_constant_unavailable:{name}")
        return value

    def _loss_stop_price(self, direction, symbol, volume, entry, budget, info):
        point = float(_field(info, "point", 0.0) or 0.0)
        digits = int(_field(info, "digits", 2) or 2)
        if point <= 0:
            return None
        order_type = self._constant(
            "ORDER_TYPE_BUY" if direction == "BUY" else "ORDER_TYPE_SELL"
        )

        def projected(price):
            try:
                value, _ = self.reader._invoke(
                    "order_calc_profit", order_type, symbol, volume, entry, float(price)
                )
            except _ReadFailure:
                return None
            value = float(value)
            return value if math.isfinite(value) else None

        if projected(entry) is None:
            return None
        target, safe, step = -budget, entry, max(1.0, point)
        adverse = entry - step if direction == "BUY" else entry + step
        for _ in range(64):
            value = projected(adverse)
            if value is None:
                return None
            if value <= target:
                break
            step *= 2.0
            adverse = entry - step if direction == "BUY" else entry + step
            if adverse <= point:
                adverse = point
        else:
            return None
        for _ in range(80):
            midpoint = (safe + adverse) / 2.0
            value = projected(midpoint)
            if value is None:
                return None
            if value < target:
                adverse = midpoint
            else:
                safe = midpoint
        units = safe / point
        units = math.ceil(units - 1e-10) if direction == "BUY" else math.floor(units + 1e-10)
        return round(units * point, digits)


def _field(value: Any, name: str, default=None):
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _legal_sl(direction: str, desired: float, tick: Any, info: Any) -> float:
    distance = float(_field(info, "trade_stops_level", 0.0) or 0.0) * float(
        _field(info, "point", 0.0) or 0.0
    )
    if direction == "BUY":
        return min(desired, float(_field(tick, "bid")) - distance)
    return max(desired, float(_field(tick, "ask")) + distance)
