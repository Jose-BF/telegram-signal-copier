"""
Wrapper MT5. Todas las funciones son síncronas.
El listener las ejecuta en un executor thread para no bloquear el loop async.
"""

import math
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import MetaTrader5 as mt5
from typing import Optional
import causal_trace
import config
import mt5_errors


@dataclass(frozen=True)
class ModifySLTPDecision:
    status: str
    effective_sl: Optional[float]
    effective_tp: Optional[float]
    deferred_sl: Optional[float] = None
    reason: Optional[str] = None
    observed_ticket: Optional[int] = None
    observed_magic: Optional[int] = None
    observed_kind: Optional[str] = None


def _finite_level(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def evaluate_position_sltp(
    position,
    tick,
    symbol_info,
    *,
    new_sl: Optional[float],
    new_tp: Optional[float],
) -> ModifySLTPDecision:
    """Decide whether a position SL/TP payload is legal at the current tick."""
    current_sl = _finite_level(getattr(position, "sl", None))
    current_tp = _finite_level(getattr(position, "tp", None))
    if new_sl is not None and _finite_level(new_sl) is None:
        return ModifySLTPDecision(
            "invalid_request", current_sl, current_tp,
            reason="invalid_sl_value",
        )
    if new_tp is not None and _finite_level(new_tp) is None:
        return ModifySLTPDecision(
            "invalid_request", current_sl, current_tp,
            reason="invalid_tp_value",
        )

    requested_sl = _finite_level(new_sl)
    requested_tp = _finite_level(new_tp) if new_tp is not None else current_tp
    if requested_sl is None:
        return ModifySLTPDecision("ready", current_sl, requested_tp)

    point = float(getattr(symbol_info, "point", 0.0) or 0.0)
    stops_level = float(getattr(symbol_info, "trade_stops_level", 0.0) or 0.0)
    freeze_level = float(getattr(symbol_info, "trade_freeze_level", 0.0) or 0.0)
    minimum_gap = max(stops_level, freeze_level) * point
    is_buy = getattr(position, "type", None) == mt5.ORDER_TYPE_BUY
    market_side = float(tick.bid if is_buy else tick.ask)
    if is_buy:
        requested_valid = requested_sl < market_side - minimum_gap
        existing_valid = (
            current_sl is not None and current_sl < market_side - minimum_gap
        )
    else:
        requested_valid = requested_sl > market_side + minimum_gap
        existing_valid = (
            current_sl is not None and current_sl > market_side + minimum_gap
        )

    if requested_valid:
        return ModifySLTPDecision("ready", requested_sl, requested_tp)
    if new_tp is not None and existing_valid:
        return ModifySLTPDecision(
            "apply_tp_defer_sl",
            current_sl,
            requested_tp,
            deferred_sl=requested_sl,
            reason="requested_sl_waits_for_market",
        )
    return ModifySLTPDecision(
        "wait_market",
        current_sl,
        current_tp,
        deferred_sl=requested_sl,
        reason="requested_sl_waits_for_market",
    )


# ─── Helpers PUROS de Batch D (anomalías executor) ───────────────────────

def _slippage_severity(slip_pts: float, deviation_pts: float):
    """Severidad del slippage entre tick price y res.price del fill.

    > 50% del deviation permitido → warning (fill muy degradado).
    Mide MAGNITUD (abs), no dirección — el ruido del mercado es lo
    que importa, no si el slip fue a favor o en contra.

    deviation_pts == 0 → None (probable bug de config, no alertar).
    """
    if deviation_pts <= 0:
        return None
    if abs(slip_pts) > deviation_pts / 2:
        return "warning"
    return None


def _classify_position_pnls_query(raw):
    """Clasifica el resultado de mt5.positions_get() para distinguir
    'sin posiciones' de 'MT5 IPC down'. Crítico: tratar None como []
    silenciosamente (bug pre-Batch-D) hace que CLOSE_FIRST piense que
    no hay nada abierto cuando en realidad MT5 no responde.

    Devuelve: "ok" | "empty" | "mt5_down".
    """
    if raw is None:
        return "mt5_down"
    if not raw:                 # [] o () o cualquier iterable vacío
        return "empty"
    return "ok"


_RX_ORDER_SIG = re.compile(r"^(DCA_)?c([12])_([1-9]\d*)(?:_[A-Za-z0-9.]+)?")


def _sig_id_from_order_comment(comment: str | None) -> Optional[str]:
    """Extrae el sig_id del comment MT5 usado por el bot."""
    if not comment:
        return None
    m = _RX_ORDER_SIG.match(comment)
    if not m:
        return None
    return f"canal{m.group(2)}_{m.group(3)}"


def _emit_event(sig_id: Optional[str], ev: str, **ctx):
    """Wrapper defensivo: el executor nunca debe fallar por logging."""
    if not sig_id:
        return
    try:
        import journal as _j_exe
        _j_exe.event(sig_id, ev, **ctx)
    except Exception:
        pass


def _result_ctx(res) -> dict:
    """Campos serializables del MqlTradeResult para el journal forense."""
    if res is None:
        return {"retcode": None}
    return {
        "retcode": getattr(res, "retcode", None),
        "comment": getattr(res, "comment", None),
        "order": getattr(res, "order", None),
        "deal": getattr(res, "deal", None),
        "volume": getattr(res, "volume", None),
        "price": getattr(res, "price", None),
        "bid": getattr(res, "bid", None),
        "ask": getattr(res, "ask", None),
        "request_id": getattr(res, "request_id", None),
        "retcode_external": getattr(res, "retcode_external", None),
    }


def _object_ctx(value, fields: tuple[str, ...]) -> Optional[dict]:
    if value is None:
        return None
    return {field: getattr(value, field, None) for field in fields}


def _tick_ctx(tick) -> Optional[dict]:
    return _object_ctx(
        tick,
        (
            "time",
            "time_msc",
            "bid",
            "ask",
            "last",
            "volume",
            "flags",
            "volume_real",
        ),
    )


def _position_ctx(position) -> Optional[dict]:
    return _object_ctx(
        position,
        (
            "ticket",
            "symbol",
            "magic",
            "type",
            "volume",
            "price_open",
            "price_current",
            "sl",
            "tp",
            "profit",
            "comment",
        ),
    )


def _order_ctx(order) -> Optional[dict]:
    return _object_ctx(
        order,
        (
            "ticket",
            "symbol",
            "magic",
            "type",
            "volume_initial",
            "volume_current",
            "price_open",
            "price_current",
            "sl",
            "tp",
            "comment",
        ),
    )


def _symbol_contract_ctx(symbol_info) -> Optional[dict]:
    return _object_ctx(
        symbol_info,
        (
            "point",
            "digits",
            "trade_stops_level",
            "trade_freeze_level",
        ),
    )


def _lookup_state(value) -> str:
    if value is None:
        return "unavailable"
    try:
        return "found" if bool(value) else "empty"
    except Exception:
        return "found"


def _new_action_trace(sig_id: Optional[str]) -> dict:
    current = causal_trace.current_fields()
    return {
        "sig_id": sig_id,
        "action_id": causal_trace.new_action_id(),
        "attempt_id": causal_trace.new_attempt_id(),
        "decision_id": (
            current.get("decision_id")
            or causal_trace.new_decision_id()
        ),
        "message_revision_id": current.get("message_revision_id"),
        "action_revision": 0,
    }


def _trace_event_fields(trace: Optional[dict]) -> dict:
    if not trace:
        return {}
    return {
        key: trace.get(key)
        for key in (
            "action_id",
            "attempt_id",
            "decision_id",
            "message_revision_id",
            "action_revision",
        )
    }


class _MT5AttemptEvidence:
    """Collect one executor attempt without issuing any MT5 reads."""

    def __init__(
        self,
        trace: Optional[dict],
        operation: str,
        *,
        ticket: Optional[int] = None,
    ):
        self.trace = dict(trace or {})
        self.operation = operation
        self.ticket = ticket
        self.started_utc = datetime.now(timezone.utc).isoformat(
            timespec="milliseconds"
        )
        self.started_monotonic_ns = time.monotonic_ns()
        self.source_tick = None
        self.validation_tick = None
        self.position_before = None
        self.order_before = None
        self.symbol_contract = None
        self.source_tick_lookup_state = "not_queried"
        self.validation_tick_lookup_state = "not_queried"
        self.position_lookup_state = "not_queried"
        self.order_lookup_state = "not_queried"
        self.symbol_info_lookup_state = "not_queried"
        self.request = None
        self.broker_request_sent = False
        self.last_error = None
        self._emitted = False

    def observe(
        self,
        *,
        tick=None,
        position=None,
        order=None,
        symbol_info=None,
    ) -> None:
        if tick is not None:
            self.source_tick = _tick_ctx(tick)
        if position is not None:
            self.position_before = _position_ctx(position)
        if order is not None:
            self.order_before = _order_ctx(order)
        if symbol_info is not None:
            self.symbol_contract = _symbol_contract_ctx(symbol_info)

    def observe_source_tick(self, tick) -> None:
        self.source_tick_lookup_state = _lookup_state(tick)
        if tick is not None:
            self.source_tick = _tick_ctx(tick)

    def observe_position_query(self, positions) -> None:
        self.position_lookup_state = _lookup_state(positions)
        if positions:
            self.position_before = _position_ctx(positions[0])

    def observe_order_query(self, orders) -> None:
        self.order_lookup_state = _lookup_state(orders)
        if orders:
            self.order_before = _order_ctx(orders[0])

    def observe_symbol_info(self, symbol_info) -> None:
        self.symbol_info_lookup_state = _lookup_state(symbol_info)
        if symbol_info is not None:
            self.symbol_contract = _symbol_contract_ctx(symbol_info)

    def observe_validation(self, tick, symbol_info) -> None:
        self.validation_tick_lookup_state = _lookup_state(tick)
        self.symbol_info_lookup_state = _lookup_state(symbol_info)
        self.validation_tick = _tick_ctx(tick)
        if symbol_info is not None:
            self.symbol_contract = _symbol_contract_ctx(symbol_info)

    def mark_request(self, request: dict) -> None:
        self.request = dict(request)
        self.broker_request_sent = True

    def observe_last_error(self, value) -> None:
        self.last_error = str(value)

    def finish(
        self,
        *,
        result=None,
        retcode=None,
        last_error=None,
        exception: BaseException | None = None,
    ) -> None:
        if self._emitted:
            return
        self._emitted = True
        sig_id = self.trace.get("sig_id")
        if not sig_id:
            return

        result_payload = _result_ctx(result)
        if result is None and retcode is not None:
            result_payload["retcode"] = retcode
        finished_monotonic_ns = time.monotonic_ns()
        exception_payload = None
        if exception is not None:
            exception_payload = {
                "type": type(exception).__name__,
                "message": str(exception)[:500],
            }
        _emit_event(
            sig_id,
            "mt5_action_attempt",
            operation=self.operation,
            ticket=self.ticket,
            attempt_started_utc=self.started_utc,
            attempt_finished_utc=datetime.now(timezone.utc).isoformat(
                timespec="milliseconds"
            ),
            attempt_started_monotonic_ns=self.started_monotonic_ns,
            attempt_finished_monotonic_ns=finished_monotonic_ns,
            duration_ns=max(
                0,
                finished_monotonic_ns - self.started_monotonic_ns,
            ),
            broker_request_sent=self.broker_request_sent,
            request=self.request,
            result=result_payload,
            last_error=last_error,
            exception=exception_payload,
            source_tick=self.source_tick,
            validation_tick=self.validation_tick,
            position_before=self.position_before,
            order_before=self.order_before,
            symbol_contract=self.symbol_contract,
            source_tick_lookup_state=self.source_tick_lookup_state,
            validation_tick_lookup_state=(
                self.validation_tick_lookup_state
            ),
            position_lookup_state=self.position_lookup_state,
            order_lookup_state=self.order_lookup_state,
            symbol_info_lookup_state=self.symbol_info_lookup_state,
            terminal_state=None,
            account_state=None,
            expected_magic=self.trace.get("expected_magic"),
            preflight_status=self.trace.get("preflight_status"),
            preflight_reason=self.trace.get("preflight_reason"),
            preflight_effective_sl=self.trace.get(
                "preflight_effective_sl"
            ),
            preflight_effective_tp=self.trace.get(
                "preflight_effective_tp"
            ),
            preflight_deferred_sl=self.trace.get(
                "preflight_deferred_sl"
            ),
            **_trace_event_fields(self.trace),
        )


def _probe_position_from_non_done_result(res):
    """Return MT5 position if a non-DONE market result actually opened one."""
    if not config.STRATEGY_MARKET_OPEN_ORDER_PROBE_ENABLED:
        return None

    ticket = getattr(res, "order", None)
    if not ticket:
        return None

    attempts = max(1, int(config.STRATEGY_MARKET_OPEN_ORDER_PROBE_ATTEMPTS))
    sleep_s = max(0.0, float(config.STRATEGY_MARKET_OPEN_ORDER_PROBE_SLEEP_S))
    for attempt in range(attempts):
        try:
            pos = mt5.positions_get(ticket=ticket)
        except Exception:
            pos = None
        if pos:
            return pos[0]
        if attempt < attempts - 1 and sleep_s:
            time.sleep(sleep_s)
    return None


def _emit_anomaly(sig_id: str, category: str, severity: str,
                  detail: str, **ctx):
    """Wrapper defensivo: never-raise. journal.anomaly puede no estar
    disponible en tests; el executor NO debe romper por eso."""
    try:
        import journal as _j_exe
        _j_exe.anomaly(sig_id, category, severity, detail, **ctx)
    except Exception:
        pass


def _account_matches_config(info) -> bool:
    if not info:
        return False
    try:
        login_ok = int(getattr(info, "login", 0)) == int(config.MT5_LOGIN)
    except (TypeError, ValueError):
        login_ok = False
    server = getattr(info, "server", None)
    server_ok = (
        not server
        or str(server).lower() == str(config.MT5_SERVER).lower()
    )
    return login_ok and server_ok


def account_evidence(info=None) -> dict:
    """Return JSON-safe evidence for the MT5 account used by this session."""
    if info is None:
        try:
            info = mt5.account_info()
        except Exception:
            return {}
    if info is None:
        return {}

    def _number(name: str, cast):
        value = getattr(info, name, None)
        if value is None:
            return None
        try:
            return cast(value)
        except (TypeError, ValueError):
            return None

    def _text(name: str):
        value = getattr(info, name, None)
        return str(value) if value is not None else None

    return {
        "login": _number("login", int),
        "server": _text("server"),
        "name": _text("name"),
        "currency": _text("currency"),
        "balance": _number("balance", float),
        "equity": _number("equity", float),
    }


def init() -> bool:
    if not mt5.initialize():
        print(f"[MT5] initialize() falló: {mt5.last_error()}")
        return False

    # Evita llamar mt5.login() si el terminal ya está en la cuenta correcta.
    # En MT5, la opción "Disable algorithmic trading when the account has been
    # changed" puede apagar Algo Trading cuando se fuerza un login al arrancar.
    # Si ya estamos en la cuenta esperada, no provocamos ese cambio.
    info = mt5.account_info()
    if _account_matches_config(info):
        print(f"[MT5] Cuenta ya activa: {info.login} | {getattr(info, 'server', '')}")
    else:
        if not mt5.login(config.MT5_LOGIN, password=config.MT5_PASSWORD, server=config.MT5_SERVER):
            print(f"[MT5] login() falló: {mt5.last_error()}")
            return False
        info = mt5.account_info()

    # Asegura que el símbolo esté en Market Watch. Sin esto, symbol_info_tick()
    # devuelve None aunque el símbolo exista en el broker. Causa real del crash
    # "No se puede obtener precio de XAUUSD" tras un reinicio del terminal.
    if not mt5.symbol_select(config.MT5_SYMBOL, True):
        print(f"[MT5] No se pudo añadir {config.MT5_SYMBOL} a Market Watch: {mt5.last_error()}")
        return False
    info = info or mt5.account_info()
    _emit_event("bot", "mt5_account_connected", **account_evidence(info))
    print(f"[MT5] Conectado: {info.name} | Balance: {info.balance} {info.currency}")
    print(f"[MT5] {config.MT5_SYMBOL} añadido a Market Watch")
    return True


def shutdown():
    mt5.shutdown()


# ─── Precios ──────────────────────────────────────────────────────────────────

def current_tick() -> dict:
    """Devuelve el tick actual de XAUUSD: {bid, ask, mid}.
    Útil para helpers de simulación o monitoring sin enviar orden.
    """
    tick = mt5.symbol_info_tick(config.MT5_SYMBOL)
    if tick is None:
        mt5.symbol_select(config.MT5_SYMBOL, True)
        tick = mt5.symbol_info_tick(config.MT5_SYMBOL)
        if tick is None:
            raise RuntimeError(f"No se puede obtener precio de {config.MT5_SYMBOL}")
    return {"bid": tick.bid, "ask": tick.ask, "mid": (tick.bid + tick.ask) / 2}


def current_tick_safe() -> Optional[dict]:
    """Como current_tick pero NUNCA lanza excepción.

    Devuelve precio y reloj nativo del broker, o None si MT5 no responde.
    Usado en logging enriquecido (no critical path) — preferimos no romper
    el flujo del bot por un fallo de tick.

    Batch D: el except no logueaba nada (NI siquiera print). Si MT5 estaba
    en estado raro perdiamos toda info. Ahora print del tipo+msg para
    diagnostico, pero sin anomaly (es non-critical path, llama el monitor
    de mt5 si MT5 esta down).
    """
    try:
        tick = mt5.symbol_info_tick(config.MT5_SYMBOL)
        if tick is None:
            return None
        return {
            "bid": round(tick.bid, 2),
            "ask": round(tick.ask, 2),
            "mid": round((tick.bid + tick.ask) / 2, 2),
            "spread": round(tick.ask - tick.bid, 2),
            "time": getattr(tick, "time", None),
            "time_msc": getattr(tick, "time_msc", None),
        }
    except Exception as e:
        print(f"[MT5] current_tick_safe excepcion: {type(e).__name__}: {e}")
        return None


def _send_safe(
    req: dict,
    label: str,
    *,
    evidence: Optional[_MT5AttemptEvidence] = None,
):
    """mt5.order_send con guard de None.

    mt5.order_send puede devolver None en escenarios de desconexion / IPC
    error con el terminal MT5. Sin este guard, los callers que hacen
    `res.retcode` directamente crashean con AttributeError y la operacion
    queda en estado indefinido (visto en place_limit antes del fix C2).

    Devuelve el res original o None. Caller debe verificar y devolver
    un retcode TRANSIENT (e.g. 10018 MARKET_CLOSED) para que pending_actions
    reintente, en lugar de crashear.

    Batch D: cuando order_send devuelve None emitimos anomaly critical
    para que la falla quede registrada en el ledger (antes solo print).
    """
    res = mt5.order_send(req)
    if res is None:
        last_err = mt5.last_error()
        if evidence is not None:
            evidence.observe_last_error(last_err)
        print(f"[MT5] {label}: order_send devolvio None — "
              f"last_error={last_err}")
        _emit_anomaly("bot", "mt5", "critical",
                      f"mt5.order_send devolvio None ({label}) — IPC con "
                      f"terminal MT5 fallo, operacion en estado indefinido",
                      label=label, last_error=str(last_err),
                      action=req.get("action"), symbol=req.get("symbol"))
    return res


def _ask_bid_with_tick(direction: str):
    tick = mt5.symbol_info_tick(config.MT5_SYMBOL)
    if tick is None:
        # Defensivo: si MT5 perdió la suscripción del símbolo (puede pasar tras
        # reconexión del terminal o weekend rollover), reintentamos una vez
        # tras volver a añadirlo a Market Watch. Si vuelve a fallar, es real.
        mt5.symbol_select(config.MT5_SYMBOL, True)
        tick = mt5.symbol_info_tick(config.MT5_SYMBOL)
        if tick is None:
            raise RuntimeError(f"No se puede obtener precio de {config.MT5_SYMBOL} (last_error={mt5.last_error()})")
    price = tick.ask if direction == "BUY" else tick.bid
    return price, tick


def _ask_bid(direction: str) -> float:
    price, _ = _ask_bid_with_tick(direction)
    return price


# ─── Abrir órdenes ────────────────────────────────────────────────────────────

def open_market(direction: str, lot: float,
                sl: Optional[float] = None,
                tp: Optional[float] = None,
                comment: str = "",
                magic: Optional[int] = None) -> Optional[int]:
    """Abre una posición a mercado y devuelve el ticket.

    Para obtener también el precio de fill real (incluyendo slippage), usar
    open_market_with_fill() — ahorra una segunda llamada MT5 a entry_price().
    """
    result = open_market_with_fill(direction, lot, sl, tp, comment, magic)
    return result[0] if result else None


def open_market_with_fill(direction: str, lot: float,
                          sl: Optional[float] = None,
                          tp: Optional[float] = None,
                          comment: str = "",
                          magic: Optional[int] = None
                          ) -> Optional[tuple[int, float]]:
    """Como open_market pero devuelve (ticket, fill_price) en una sola llamada.

    mt5.order_send() ya devuelve `res.price` con el precio real de fill
    (incluyendo cualquier slippage). Antes lo descartábamos y llamábamos
    entry_price(ticket) por separado, gastando un round-trip extra a MT5
    (~5ms). Esta versión lo aprovecha.
    """
    sig_id = _sig_id_from_order_comment(comment) or "bot"
    trace = _new_action_trace(sig_id)
    attempt = _MT5AttemptEvidence(trace, "OPEN_MARKET")
    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    requested_sl = sl
    # _ask_bid puede lanzar RuntimeError si MT5 esta desconectado. Sin
    # try/except, la excepcion sube hasta el handler de Telethon y la senal
    # se pierde sin journal de fallo. Mejor: log + return None, el caller
    # en listener vera el None y registrara market_fill_failed.
    try:
        price, source_tick = _ask_bid_with_tick(direction)
        attempt.observe_source_tick(source_tick)
    except RuntimeError as e:
        attempt.observe_source_tick(None)
        _emit_event(
            sig_id,
            "mt5_order_requested",
            order_kind="market",
            direction=direction,
            lot=lot,
            requested_price=None,
            sl=requested_sl,
            tp=tp,
            magic=magic if magic is not None else config.MT5_MAGIC,
            comment=comment[:31],
            deviation=30,
            preflight_status="source_tick_unavailable",
            **_trace_event_fields(trace),
        )
        attempt.finish(exception=e)
        print(f"[MT5] open_market: no pude leer tick ({e}) -> abort")
        return None
    except Exception as exc:
        attempt.observe_source_tick(None)
        _emit_event(
            sig_id,
            "mt5_order_requested",
            order_kind="market",
            direction=direction,
            lot=lot,
            requested_price=None,
            sl=requested_sl,
            tp=tp,
            magic=magic if magic is not None else config.MT5_MAGIC,
            comment=comment[:31],
            deviation=30,
            preflight_status="source_tick_error",
            **_trace_event_fields(trace),
        )
        attempt.finish(exception=exc)
        raise

    # Si el SL queda dentro del stops_level del broker, lo ajustamos al mínimo legal
    # para que la orden no sea rechazada con "invalid stops".
    try:
        sl = safe_sl(direction, sl, evidence=attempt)
    except Exception as exc:
        _emit_event(
            sig_id,
            "mt5_order_requested",
            order_kind="market",
            direction=direction,
            lot=lot,
            requested_price=price,
            sl=requested_sl,
            tp=tp,
            magic=magic if magic is not None else config.MT5_MAGIC,
            comment=comment[:31],
            deviation=30,
            preflight_status="sl_validation_error",
            **_trace_event_fields(trace),
        )
        attempt.finish(exception=exc)
        raise

    req = {
        "action":      mt5.TRADE_ACTION_DEAL,
        "symbol":      config.MT5_SYMBOL,
        "volume":      lot,
        "type":        order_type,
        "price":       price,
        "deviation":   30,
        "magic":       magic if magic is not None else config.MT5_MAGIC,
        "comment":     comment[:31],
        "type_time":   mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    if sl:
        req["sl"] = sl
    if tp:
        req["tp"] = tp

    _emit_event(sig_id, "mt5_order_requested",
                order_kind="market",
                direction=direction,
                lot=lot,
                requested_price=price,
                requested_sl_original=requested_sl,
                sl=req.get("sl"),
                tp=req.get("tp"),
                magic=req.get("magic"),
                comment=req.get("comment"),
                deviation=req.get("deviation"),
                **_trace_event_fields(trace))
    attempt.mark_request(req)
    try:
        res = _send_safe(
            req,
            f"open_market {direction} lot={lot}",
            evidence=attempt,
        )
    except Exception as exc:
        attempt.finish(exception=exc)
        raise
    attempt.finish(
        result=res,
        last_error=attempt.last_error,
    )
    _emit_event(sig_id, "mt5_order_result",
                order_kind="market",
                direction=direction,
                requested_price=price,
                **_trace_event_fields(trace),
                **_result_ctx(res))
    if res is None:
        return None
    if res.retcode != mt5.TRADE_RETCODE_DONE:
        # Logueamos TODO el response para poder diagnosticar (a veces MT5
        # devuelve retcodes raros como DONE_PARTIAL, REJECT, etc.).
        print(f"[MT5] market order fallo: retcode={res.retcode} comment={res.comment!r} "
              f"deal={res.deal} order={res.order} volume={res.volume} price={res.price} "
              f"bid={res.bid} ask={res.ask}")
        pos = _probe_position_from_non_done_result(res)
        if pos is not None:
            recovered_ticket = getattr(pos, "ticket", None) or res.order
            fill_price = (
                getattr(pos, "price_open", None)
                or getattr(res, "price", None)
                or price
            )
            _emit_event(sig_id, "market_fill_recovered_from_non_done",
                        retcode=res.retcode,
                        comment=getattr(res, "comment", None),
                        ticket=recovered_ticket,
                        order=res.order,
                        fill_price=round(float(fill_price), 2),
                        **_trace_event_fields(trace))
            _emit_anomaly(sig_id or "bot", "fill", "warning",
                          f"market order retcode={res.retcode} pero "
                          f"positions_get encontro posicion abierta; "
                          f"trackeando ticket={recovered_ticket}",
                          retcode=res.retcode,
                          comment=getattr(res, "comment", None),
                          order=res.order,
                          ticket=recovered_ticket,
                          fill_price=round(float(fill_price), 2))
            return (int(recovered_ticket), float(fill_price))
        # Batch D: DONE_PARTIAL = 10009 NO existe en MT5 (DONE es 10009).
        # Pero retcodes 10008 (PLACED) y 10010 (DONE_PARTIAL real) pueden
        # significar que abrieron volumen parcial y nosotros perdemos la
        # referencia → posicion huerfana sin nuestro tracking.
        # Verificamos si hay deal real asociado.
        try:
            if res.order:
                deals = mt5.history_deals_get(position=res.order)
                if deals and len(deals) > 0:
                    _emit_anomaly("bot", "fill", "critical",
                                  f"market order non-DONE retcode={res.retcode} "
                                  f"PERO history_deals tiene {len(deals)} deals — "
                                  f"posible posicion abierta sin nuestro tracking "
                                  f"(huerfano potencial ticket={res.order})",
                                  retcode=res.retcode,
                                  comment=getattr(res, "comment", None),
                                  order=res.order, volume=res.volume,
                                  n_deals=len(deals))
        except Exception:
            pass
        return None
    # res.price es el fill REAL (puede diferir de price si hubo slippage)
    fill_price = res.price if res.price else price
    print(f"[MT5] Mercado {direction} {lot} @ {fill_price:.2f} | "
          f"ticket={res.order} deal={res.deal} volume={res.volume} | "
          f"magic={req['magic']} comment={res.comment!r}")

    # Batch D: slippage warning si el fill se aleja mucho del precio leido
    # cuando armamos la orden. Util para detectar mercados volatiles donde
    # el bot opera a precios degradados.
    if fill_price and price:
        slip = fill_price - price
        sev = _slippage_severity(slip, req.get("deviation", 30))
        if sev:
            _emit_anomaly("bot", "fill", sev,
                          f"slippage alto en market {direction}: "
                          f"tick={price:.2f} fill={fill_price:.2f} "
                          f"slip={slip:+.2f} (deviation={req.get('deviation')})",
                          tick_price=round(price, 2),
                          fill_price=round(fill_price, 2),
                          slip_pts=round(slip, 2),
                          deviation=req.get("deviation"),
                          ticket=res.order, direction=direction)

    # Verificacion best-effort: la posicion debe existir tras el fill.
    # Si MT5 confirmo DONE pero positions_get no la encuentra, hay algo muy
    # raro (broker bug, IOC sin fill, posicion cerrada en mismo tick por
    # alguna regla del broker, etc). Logueamos para no perderlo silenciosamente.
    # (Ticket mismo nombre que order id en MT5: res.order.)
    try:
        pos = mt5.positions_get(ticket=res.order)
        if not pos:
            # Quiza esta como deal en el historial sin posicion abierta:
            deals = mt5.history_deals_get(position=res.order)
            n_deals = len(deals) if deals else 0
            print(f"[MT5] ⚠ Order DONE pero positions_get(ticket={res.order}) vacio. "
                  f"history_deals: {n_deals}. Posible cierre instantaneo o "
                  f"deal sin posicion (rejected/canceled tras el ack).")
            # Batch D: phantom fill. Order ack DONE pero la posicion no
            # existe → bot puede estar perdiendo dinero sin rastro.
            _emit_anomaly("bot", "fill", "critical",
                          f"order DONE pero positions_get vacio (phantom fill) — "
                          f"ticket={res.order}, deals={n_deals}, "
                          f"posible cierre instantaneo o reject post-ack",
                          ticket=res.order, n_deals=n_deals,
                          direction=direction, lot=lot,
                          fill_price=round(fill_price, 2))
    except Exception as e:
        print(f"[MT5] verificacion post-fill fallo: {e}")
        _emit_anomaly("bot", "mt5", "warning",
                      f"verificacion post-fill crasheo: {type(e).__name__}: "
                      f"{str(e)[:200]}",
                      ticket=res.order, exc_type=type(e).__name__)

    return (res.order, float(fill_price))


def place_limit(direction: str, price: float, lot: float,
                sl: Optional[float] = None,
                tp: Optional[float] = None,
                comment: str = "",
                magic: Optional[int] = None) -> Optional[int]:
    sig_id = _sig_id_from_order_comment(comment) or "bot"
    trace = _new_action_trace(sig_id)
    attempt = _MT5AttemptEvidence(trace, "PLACE_LIMIT")
    order_type = mt5.ORDER_TYPE_BUY_LIMIT if direction == "BUY" else mt5.ORDER_TYPE_SELL_LIMIT

    req = {
        "action":      mt5.TRADE_ACTION_PENDING,
        "symbol":      config.MT5_SYMBOL,
        "volume":      lot,
        "type":        order_type,
        "price":       price,
        "deviation":   30,
        "magic":       magic if magic is not None else config.MT5_MAGIC,
        "comment":     comment[:31],
        "type_time":   mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    if sl:
        req["sl"] = sl
    if tp:
        req["tp"] = tp

    _emit_event(sig_id, "mt5_order_requested",
                order_kind="pending_limit",
                direction=direction,
                lot=lot,
                requested_price=price,
                sl=req.get("sl"),
                tp=req.get("tp"),
                magic=req.get("magic"),
                comment=req.get("comment"),
                deviation=req.get("deviation"),
                **_trace_event_fields(trace))
    attempt.mark_request(req)
    try:
        res = _send_safe(
            req,
            f"place_limit {direction} @ {price}",
            evidence=attempt,
        )
    except Exception as exc:
        attempt.finish(exception=exc)
        raise
    attempt.finish(result=res, last_error=attempt.last_error)
    _emit_event(sig_id, "mt5_order_result",
                order_kind="pending_limit",
                direction=direction,
                requested_price=price,
                **_trace_event_fields(trace),
                **_result_ctx(res))
    if res is None:
        return None
    if res.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"[MT5] limit order fallo @ {price}: retcode={res.retcode} "
              f"comment={res.comment!r}")
        return None
    print(f"[MT5] Limite {direction} {lot} @ {price:.2f} | ticket={res.order}")
    return res.order


# ─── Modificar ────────────────────────────────────────────────────────────────

def _assert_magic(ticket: int, actual_magic: int, expected_magic: Optional[int]) -> bool:
    """Safety: verifica que la operación pertenece al magic esperado.
    Evita tocar por error operaciones del otro canal o manuales.

    Batch D: magic mismatch es un NEAR-MISS de safety — significa que
    estuvimos a punto de tocar una posicion ajena (otro canal o manual).
    Emite anomaly critical para que quede claro en el ledger.
    """
    if expected_magic is None:
        return True
    if actual_magic != expected_magic:
        print(f"[MT5][SAFETY] ticket={ticket} magic mismatch: "
              f"esperado={expected_magic} real={actual_magic}. ABORT.")
        _emit_anomaly("bot", "mt5", "critical",
                      f"safety abort: magic mismatch en ticket={ticket} "
                      f"(esperado={expected_magic}, real={actual_magic}) — "
                      f"el bot estuvo a punto de tocar una posicion ajena",
                      ticket=ticket, expected_magic=expected_magic,
                      actual_magic=actual_magic)
        return False
    return True


def preflight_modify_sltp(
    ticket: int,
    new_sl: Optional[float] = None,
    new_tp: Optional[float] = None,
    expected_magic: Optional[int] = None,
) -> ModifySLTPDecision:
    """Read-only MT5 preflight used by the async pending-action queue."""
    positions = mt5.positions_get(ticket=ticket)
    if positions is None:
        return ModifySLTPDecision(
            "mt5_unavailable", None, None, reason="positions_get_failed")
    if positions:
        position = positions[0]
        if not _assert_magic(ticket, position.magic, expected_magic):
            return ModifySLTPDecision(
                "invalid_magic",
                None,
                None,
                reason="magic_mismatch",
                observed_ticket=getattr(position, "ticket", ticket),
                observed_magic=getattr(position, "magic", None),
                observed_kind="position",
            )
        tick = mt5.symbol_info_tick(config.MT5_SYMBOL)
        symbol_info = mt5.symbol_info(config.MT5_SYMBOL)
        if tick is None or symbol_info is None:
            return ModifySLTPDecision(
                "mt5_unavailable",
                _finite_level(getattr(position, "sl", None)),
                _finite_level(getattr(position, "tp", None)),
                reason="tick_or_symbol_info_unavailable",
            )
        return evaluate_position_sltp(
            position,
            tick,
            symbol_info,
            new_sl=new_sl,
            new_tp=new_tp,
        )

    orders = mt5.orders_get(ticket=ticket)
    if orders is None:
        return ModifySLTPDecision(
            "mt5_unavailable", None, None, reason="orders_get_failed")
    if orders:
        order = orders[0]
        if not _assert_magic(ticket, order.magic, expected_magic):
            return ModifySLTPDecision(
                "invalid_magic",
                None,
                None,
                reason="magic_mismatch",
                observed_ticket=getattr(order, "ticket", ticket),
                observed_magic=getattr(order, "magic", None),
                observed_kind="order",
            )
        return ModifySLTPDecision(
            "ready",
            _finite_level(new_sl) if new_sl is not None else _finite_level(order.sl),
            _finite_level(new_tp) if new_tp is not None else _finite_level(order.tp),
        )
    return ModifySLTPDecision(
        "position_gone", None, None, reason="ticket_not_found")


def modify_sltp_rc(ticket: int, new_sl: Optional[float] = None,
                   new_tp: Optional[float] = None,
                   expected_magic: Optional[int] = None,
                   *,
                   trace: Optional[dict] = None) -> int:
    """Modifica SL y/o TP. Si uno es None, conserva el valor actual.
    Si expected_magic no es None, verifica que la posición tiene ese magic.
    Devuelve el retcode MT5 para poder clasificarlo."""
    attempt = _MT5AttemptEvidence(trace, "MODIFY_SLTP", ticket=ticket)
    try:
        pos = mt5.positions_get(ticket=ticket)
    except Exception as exc:
        attempt.finish(exception=exc)
        raise
    attempt.observe_position_query(pos)
    if pos:
        p = pos[0]
        if not _assert_magic(ticket, p.magic, expected_magic):
            attempt.finish(retcode=mt5.TRADE_RETCODE_INVALID)
            return mt5.TRADE_RETCODE_INVALID
        try:
            tick = mt5.symbol_info_tick(config.MT5_SYMBOL)
            symbol_info = mt5.symbol_info(config.MT5_SYMBOL)
        except Exception as exc:
            attempt.finish(exception=exc)
            raise
        attempt.observe_source_tick(tick)
        attempt.observe_symbol_info(symbol_info)
        if tick is None or symbol_info is None:
            attempt.finish(retcode=mt5.TRADE_RETCODE_MARKET_CLOSED)
            return mt5.TRADE_RETCODE_MARKET_CLOSED
        try:
            decision = evaluate_position_sltp(
                p,
                tick,
                symbol_info,
                new_sl=new_sl,
                new_tp=new_tp,
            )
        except Exception as exc:
            attempt.finish(exception=exc)
            raise
        if decision.status == "invalid_request":
            attempt.finish(retcode=mt5.TRADE_RETCODE_INVALID)
            return mt5.TRADE_RETCODE_INVALID
        if decision.status == "wait_market":
            attempt.finish(retcode=mt5.TRADE_RETCODE_INVALID_STOPS)
            return mt5.TRADE_RETCODE_INVALID_STOPS
        if (
            decision.status == "apply_tp_defer_sl"
            and new_sl is not None
        ):
            # The market moved after the queue preflight. Do not turn a
            # partial TP-only update into a false SL+TP success. Returning a
            # transient stops result makes the queue preflight again and use
            # its explicit TP-only path while the SL remains pending.
            attempt.finish(retcode=mt5.TRADE_RETCODE_INVALID_STOPS)
            return mt5.TRADE_RETCODE_INVALID_STOPS
        sl_final = decision.effective_sl or 0.0
        tp_final = decision.effective_tp or 0.0
        req = {
            "action":   mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "sl":       sl_final,
            "tp":       tp_final,
        }
        attempt.mark_request(req)
        try:
            res = _send_safe(
                req,
                f"modify_sltp position={ticket}",
                evidence=attempt,
            )
        except Exception as exc:
            attempt.finish(exception=exc)
            raise
        if res is None:
            attempt.finish(
                retcode=mt5.TRADE_RETCODE_MARKET_CLOSED,
                last_error=attempt.last_error,
            )
            return mt5.TRADE_RETCODE_MARKET_CLOSED  # TRANSIENT -> RETRY
        attempt.finish(result=res)
        tag = f"SL={sl_final} TP={tp_final}"
        if res.retcode == mt5.TRADE_RETCODE_DONE:
            print(f"[MT5] SLTP modificado ticket={ticket} → {tag}")
        else:
            print(f"[MT5] modify_sltp ticket={ticket} → {tag}: {res.retcode} — {res.comment}")
        return res.retcode

    try:
        orders = mt5.orders_get(ticket=ticket)
    except Exception as exc:
        attempt.finish(exception=exc)
        raise
    attempt.observe_order_query(orders)
    if orders:
        o = orders[0]
        if not _assert_magic(ticket, o.magic, expected_magic):
            attempt.finish(retcode=mt5.TRADE_RETCODE_INVALID)
            return mt5.TRADE_RETCODE_INVALID
        sl_final = new_sl if new_sl is not None else o.sl
        tp_final = new_tp if new_tp is not None else o.tp
        req = {
            "action": mt5.TRADE_ACTION_MODIFY,
            "order":  ticket,
            "price":  o.price_open,
            "sl":     sl_final,
            "tp":     tp_final,
        }
        attempt.mark_request(req)
        try:
            res = _send_safe(
                req,
                f"modify_sltp pending order={ticket}",
                evidence=attempt,
            )
        except Exception as exc:
            attempt.finish(exception=exc)
            raise
        if res is None:
            attempt.finish(
                retcode=mt5.TRADE_RETCODE_MARKET_CLOSED,
                last_error=attempt.last_error,
            )
            return mt5.TRADE_RETCODE_MARKET_CLOSED  # TRANSIENT -> RETRY
        attempt.finish(result=res)
        tag = f"SL={sl_final} TP={tp_final}"
        if res.retcode == mt5.TRADE_RETCODE_DONE:
            print(f"[MT5] SLTP límite modificado ticket={ticket} → {tag}")
        else:
            print(f"[MT5] modify_sltp pendiente ticket={ticket}: {res.retcode} — {res.comment}")
        return res.retcode

    print(f"[MT5] modify_sltp: ticket {ticket} no encontrado")
    attempt.finish(retcode=mt5.TRADE_RETCODE_INVALID)
    return mt5.TRADE_RETCODE_INVALID


def modify_sl_rc(ticket: int, new_sl: float, expected_magic: Optional[int] = None) -> int:
    """Wrapper: solo SL, conserva TP."""
    return modify_sltp_rc(ticket, new_sl=new_sl, new_tp=None, expected_magic=expected_magic)


def modify_tp_rc(ticket: int, new_tp: float, expected_magic: Optional[int] = None) -> int:
    """Wrapper: solo TP, conserva SL."""
    return modify_sltp_rc(ticket, new_sl=None, new_tp=new_tp, expected_magic=expected_magic)


def modify_sl(ticket: int, new_sl: float, expected_magic: Optional[int] = None) -> bool:
    return modify_sl_rc(ticket, new_sl, expected_magic) == mt5.TRADE_RETCODE_DONE


def modify_tp(ticket: int, new_tp: float, expected_magic: Optional[int] = None) -> bool:
    return modify_tp_rc(ticket, new_tp, expected_magic) == mt5.TRADE_RETCODE_DONE


# ─── Cerrar / cancelar ────────────────────────────────────────────────────────

def close_position_rc(
    ticket: int,
    expected_magic: Optional[int] = None,
    *,
    trace: Optional[dict] = None,
) -> int:
    attempt = _MT5AttemptEvidence(trace, "CLOSE_POSITION", ticket=ticket)
    try:
        pos = mt5.positions_get(ticket=ticket)
    except Exception as exc:
        attempt.finish(exception=exc)
        raise
    attempt.observe_position_query(pos)
    if not pos:
        print(f"[MT5] close_position: ticket {ticket} no encontrado")
        attempt.finish(retcode=mt5.TRADE_RETCODE_INVALID)
        return mt5.TRADE_RETCODE_INVALID

    p = pos[0]
    if not _assert_magic(ticket, p.magic, expected_magic):
        attempt.finish(retcode=mt5.TRADE_RETCODE_INVALID)
        return mt5.TRADE_RETCODE_INVALID
    close_dir = "SELL" if p.type == mt5.POSITION_TYPE_BUY else "BUY"
    order_type = mt5.ORDER_TYPE_SELL if p.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
    # _ask_bid puede lanzar RuntimeError si MT5 perdio el tick (desconexion
    # o weekend). Sin este try/except, la excepcion sube hasta
    # pending_actions._try_once, no es retcode -> accion queda colgada.
    # Devolvemos un retcode TRANSIENT para que la cola reintente cuando
    # MT5 vuelva a responder.
    try:
        price, source_tick = _ask_bid_with_tick(close_dir)
        attempt.observe_source_tick(source_tick)
    except RuntimeError as e:
        attempt.observe_source_tick(None)
        attempt.finish(
            retcode=mt5.TRADE_RETCODE_MARKET_CLOSED,
            exception=e,
        )
        print(f"[MT5] close_position ticket={ticket}: no pude leer tick "
              f"({e}) -> retorno TRANSIENT para reintento")
        return mt5.TRADE_RETCODE_MARKET_CLOSED
    except Exception as exc:
        attempt.observe_source_tick(None)
        attempt.finish(exception=exc)
        raise

    # Conservamos el magic original de la posición para que el deal de cierre
    # quede atribuido al mismo canal que la abrió.
    req = {
        "action":      mt5.TRADE_ACTION_DEAL,
        "symbol":      config.MT5_SYMBOL,
        "volume":      p.volume,
        "type":        order_type,
        "position":    ticket,
        "price":       price,
        "deviation":   30,
        "magic":       p.magic,
        "comment":     "bot_close",
        "type_time":   mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    attempt.mark_request(req)
    try:
        res = _send_safe(
            req,
            f"close_position ticket={ticket}",
            evidence=attempt,
        )
    except Exception as exc:
        attempt.finish(exception=exc)
        raise
    if res is None:
        attempt.finish(
            retcode=mt5.TRADE_RETCODE_MARKET_CLOSED,
            last_error=attempt.last_error,
        )
        return mt5.TRADE_RETCODE_MARKET_CLOSED  # TRANSIENT -> RETRY
    attempt.finish(result=res)
    if res.retcode == mt5.TRADE_RETCODE_DONE:
        print(f"[MT5] Posición cerrada ticket={ticket}")
    else:
        print(f"[MT5] close_position falló ticket={ticket}: {res.retcode} — {res.comment}")
    return res.retcode


def close_position(ticket: int, expected_magic: Optional[int] = None) -> bool:
    return close_position_rc(ticket, expected_magic) == mt5.TRADE_RETCODE_DONE


def cancel_pending_rc(
    ticket: int,
    expected_magic: Optional[int] = None,
    *,
    trace: Optional[dict] = None,
) -> int:
    attempt = _MT5AttemptEvidence(trace, "CANCEL_PENDING", ticket=ticket)
    if expected_magic is not None:
        try:
            orders = mt5.orders_get(ticket=ticket)
        except Exception as exc:
            attempt.finish(exception=exc)
            raise
        attempt.observe_order_query(orders)
        if not orders:
            print(f"[MT5] cancel_pending: ticket {ticket} no encontrado")
            attempt.finish(retcode=mt5.TRADE_RETCODE_INVALID)
            return mt5.TRADE_RETCODE_INVALID
        order = orders[0]
        if not _assert_magic(ticket, order.magic, expected_magic):
            attempt.finish(retcode=mt5.TRADE_RETCODE_INVALID)
            return mt5.TRADE_RETCODE_INVALID

    req = {"action": mt5.TRADE_ACTION_REMOVE, "order": ticket}
    attempt.mark_request(req)
    try:
        res = _send_safe(
            req,
            f"cancel_pending ticket={ticket}",
            evidence=attempt,
        )
    except Exception as exc:
        attempt.finish(exception=exc)
        raise
    if res is None:
        attempt.finish(
            retcode=mt5.TRADE_RETCODE_MARKET_CLOSED,
            last_error=attempt.last_error,
        )
        return mt5.TRADE_RETCODE_MARKET_CLOSED  # TRANSIENT -> RETRY
    attempt.finish(result=res)
    if res.retcode == mt5.TRADE_RETCODE_DONE:
        print(f"[MT5] Pendiente cancelado ticket={ticket}")
    else:
        print(f"[MT5] cancel_pending falló ticket={ticket}: {res.retcode} — {res.comment}")
    return res.retcode


def cancel_pending(ticket: int, expected_magic: Optional[int] = None) -> bool:
    return cancel_pending_rc(ticket, expected_magic) == mt5.TRADE_RETCODE_DONE


# ─── Apertura con ajuste de SL ilegal ─────────────────────────────────────────

def safe_sl(
    direction: str,
    desired_sl: Optional[float],
    *,
    evidence: Optional[_MT5AttemptEvidence] = None,
) -> Optional[float]:
    """Ajusta SL al mínimo legal del broker. None si no hay tick.

    Batch D: si desired_sl ESTABA seteado pero adjust_sl_to_legal devuelve
    None (broker no responde tick), DEVOLVEMOS None y el caller dropea
    el SL del request → la posicion se abre SIN STOP LOSS. Emite anomaly
    critical en ese caso para que la apertura sin proteccion quede
    registrada.
    """
    if desired_sl is None:
        return None
    adjusted = mt5_errors.adjust_sl_to_legal(
        direction,
        desired_sl,
        observation_callback=(
            evidence.observe_validation if evidence is not None else None
        ),
    )
    if adjusted is None:
        _emit_anomaly("bot", "naked", "critical",
                      f"safe_sl(adjusted=None) — broker no devolvio tick "
                      f"para ajustar SL={desired_sl}. La posicion se va a "
                      f"abrir SIN STOP LOSS",
                      desired_sl=desired_sl, direction=direction)
        return None
    if abs(adjusted - desired_sl) > 0.001:
        print(f"[MT5] SL {desired_sl} ajustado a {adjusted:.2f} (stops_level del broker)")
    return adjusted


def entry_price(ticket: int) -> Optional[float]:
    pos = mt5.positions_get(ticket=ticket)
    return pos[0].price_open if pos else None


def open_entry_prices(tickets: list[int]) -> Optional[dict[int, float]]:
    """Return exact entries for requested tickets that are currently open.

    ``None`` means the MT5 query failed; an empty/partial dict means the
    absent tickets are already closed.
    """
    if not tickets:
        return {}
    all_open = mt5.positions_get()
    if all_open is None:
        return None
    wanted = set(tickets)
    return {
        int(position.ticket): float(position.price_open)
        for position in all_open
        if position.ticket in wanted
    }


def open_position_levels(tickets: list[int]) -> Optional[dict[int, dict]]:
    """Return the SL/TP currently installed on requested open positions.

    ``None`` means the MT5 query failed. Missing keys are positions that are
    no longer open, so callers must not treat them as zero-valued levels.
    """
    if not tickets:
        return {}
    all_open = mt5.positions_get()
    if all_open is None:
        return None

    wanted = set(int(ticket) for ticket in tickets)
    symbol_specs = {}
    levels = {}
    for position in all_open:
        ticket = int(position.ticket)
        if ticket not in wanted:
            continue
        symbol = getattr(position, "symbol", config.MT5_SYMBOL)
        if symbol not in symbol_specs:
            symbol_specs[symbol] = mt5.symbol_info(symbol)
        spec = symbol_specs[symbol]
        levels[ticket] = {
            "sl": float(getattr(position, "sl", 0.0) or 0.0),
            "tp": float(getattr(position, "tp", 0.0) or 0.0),
            "digits": int(getattr(spec, "digits", 2) if spec else 2),
            "point": float(getattr(spec, "point", 0.01) if spec else 0.01),
        }
    return levels


def risk_free_basket_snapshot(tickets: list[int]) -> Optional[dict]:
    """Capture the account-currency evidence needed to secure a basket.

    ``None`` means MT5 did not answer the open-position query. Absent tickets
    only contribute realized P&L when their deal history proves a complete
    round trip; otherwise ``realized_complete`` is false and callers must
    preserve the live trade.
    """
    requested = list(dict.fromkeys(
        int(ticket) for ticket in tickets
        if not isinstance(ticket, bool) and int(ticket) > 0
    ))
    all_open = mt5.positions_get()
    if all_open is None:
        return None

    wanted = set(requested)
    open_by_ticket = {
        int(position.ticket): position
        for position in all_open
        if int(getattr(position, "ticket", 0) or 0) in wanted
    }
    open_legs = []
    for ticket in requested:
        position = open_by_ticket.get(ticket)
        if position is None:
            continue
        position_type = getattr(position, "type", None)
        is_buy = position_type == getattr(mt5, "POSITION_TYPE_BUY", 0)
        order_type = (
            getattr(mt5, "ORDER_TYPE_BUY", 0)
            if is_buy else getattr(mt5, "ORDER_TYPE_SELL", 1)
        )
        symbol = str(getattr(position, "symbol", config.MT5_SYMBOL))
        volume = float(getattr(position, "volume", 0.0) or 0.0)
        entry = float(getattr(position, "price_open", 0.0) or 0.0)
        current = float(getattr(position, "price_current", 0.0) or 0.0)
        sl = float(getattr(position, "sl", 0.0) or 0.0)
        tp = float(getattr(position, "tp", 0.0) or 0.0)
        swap = float(getattr(position, "swap", 0.0) or 0.0)
        current_pnl = float(getattr(position, "profit", 0.0) or 0.0) + swap

        stop_pnl = None
        if volume > 0 and entry > 0 and sl > 0:
            calculated = mt5.order_calc_profit(
                order_type, symbol, volume, entry, sl,
            )
            if calculated is not None:
                stop_pnl = float(calculated) + swap

        target_distance = None
        if current > 0 and tp > 0:
            target_distance = max(
                (tp - current) if is_buy else (current - tp),
                0.0,
            )

        open_legs.append({
            "ticket": ticket,
            "current_pnl": current_pnl,
            "stop_pnl": stop_pnl,
            "target_distance": target_distance,
            "sl": sl,
            "tp": tp,
        })

    entry_in = getattr(mt5, "DEAL_ENTRY_IN", 0)
    entry_out = getattr(mt5, "DEAL_ENTRY_OUT", 1)
    entry_inout = getattr(mt5, "DEAL_ENTRY_INOUT", 2)
    entry_out_by = getattr(mt5, "DEAL_ENTRY_OUT_BY", 3)
    realized_total = 0.0
    missing_realized = []
    for ticket in requested:
        deals = mt5.history_deals_get(position=ticket)
        if not deals:
            missing_realized.append(ticket)
            continue
        in_volume = 0.0
        out_volume = 0.0
        for deal in deals:
            volume = float(getattr(deal, "volume", 0.0) or 0.0)
            deal_entry = getattr(deal, "entry", None)
            if deal_entry == entry_in:
                in_volume += volume
            elif deal_entry in {entry_out, entry_out_by}:
                out_volume += volume
            elif deal_entry == entry_inout:
                in_volume += volume
                out_volume += volume
        open_position = open_by_ticket.get(ticket)
        if open_position is not None:
            current_volume = float(
                getattr(open_position, "volume", 0.0) or 0.0
            )
            history_is_complete = (
                in_volume > 0
                and current_volume > 0
                and abs((in_volume - out_volume) - current_volume) <= 1e-8
            )
        else:
            history_is_complete = (
                in_volume > 0 and out_volume + 1e-9 >= in_volume
            )
        if not history_is_complete:
            missing_realized.append(ticket)
            continue
        realized_total += sum(
            float(getattr(deal, field, 0.0) or 0.0)
            for deal in deals
            for field in ("profit", "commission", "swap", "fee")
        )

    account = mt5.account_info()
    return {
        "open_legs": open_legs,
        "realized_pnl": (
            realized_total if not missing_realized else None
        ),
        "realized_complete": not missing_realized,
        "missing_realized_tickets": missing_realized,
        "account_currency": (
            str(getattr(account, "currency", "") or "") or None
        ),
    }


def position_pnls(tickets: list[int]) -> list[tuple[int, float]]:
    """Devuelve [(ticket, floating_pnl), ...] para los tickets que SIGUEN abiertos.

    Hace 1 sola llamada a MT5 (positions_get sin filtro) y filtra en Python,
    evitando N round-trips cuando hay varios tickets que consultar. Usado por
    CLOSE_FIRST contextual (ordenar por P&L para cerrar la mitad peor).

    Batch D: distinguir None (MT5 IPC FAILURE) de [] (sin posiciones). Antes
    ambos devolvian [], y CLOSE_FIRST asumia "nada abierto" cuando en
    realidad MT5 no respondia → decisiones de gestion sobre datos vacios.
    """
    if not tickets:
        return []
    all_open = mt5.positions_get()  # 1 IPC al terminal, devuelve TODAS las posiciones
    classification = _classify_position_pnls_query(all_open)
    if classification == "mt5_down":
        _emit_anomaly("bot", "mt5", "warning",
                      "mt5.positions_get() devolvio None — IPC con terminal "
                      "fallo. CLOSE_FIRST/PnL decisions caminaran sobre datos "
                      "vacios hasta que vuelva.",
                      tickets_querid=tickets[:10],
                      n_tickets=len(tickets))
        return []
    if classification == "empty":
        return []
    wanted = set(tickets)
    return [(p.ticket, p.profit) for p in all_open if p.ticket in wanted]


# ─── Resync de posiciones huérfanas (on startup) ──────────────────────────────

import re as _re_resync

# Regex para parsear comentarios de posiciones del bot:
#   "c1_19236"               → market canal 1, signal 19236
#   "c2_12015"               → market canal 2, signal 12015
#   "c2_12015_rescue"        → rescue market canal 2 signal 12015
#   "c2_12015_B"             → Market B del doble market (commit 2026-05-14)
#   "c2_12015_B1".."_B4"     → legs extra del modo scale_out (2026-05-17)
#   "DCA_c1_19236_4593.5"    → DCA nuevo (con signal_id) — formato actual
#   "DCA_4593.5"             → DCA viejo (sin signal_id) — backward compat
# _RX_MARKET acepta sufijo _rescue o _B/_BN. Sin _B el resync ignoraba el
# Market B del doble market (canal2_12497 perdio +$6.05); _BN cubre las
# legs del modo scale_out (una posicion market por TP).
_RX_MARKET = _re_resync.compile(r"^c([12])_(\d+)(?:_rescue|_B\d*)?$")
_RX_DCA_NEW = _re_resync.compile(r"^DCA_c([12])_(\d+)_")
_RX_DCA_OLD = _re_resync.compile(r"^DCA_[\d.]+$")


def _parse_signal_id_from_comment(comment: str) -> Optional[tuple[str, int]]:
    """Parsea el comment de una posición y devuelve (channel, message_id)
    si reconoce el formato. None si no es una posición del bot o si es
    un DCA viejo sin signal_id (esos se agrupan por proximidad temporal).
    """
    if not comment:
        return None
    m = _RX_MARKET.match(comment)
    if m:
        return (f"canal{m.group(1)}", int(m.group(2)))
    m = _RX_DCA_NEW.match(comment)
    if m:
        return (f"canal{m.group(1)}", int(m.group(2)))
    return None


def list_open_positions_grouped() -> dict[str, dict]:
    """Devuelve dict {signal_id: {market_ticket, market_price, market_sl,
    market_tp, dca_tickets, direction, magic, channel, message_id}}.

    Para resync on startup: encuentra TODAS las posiciones abiertas con
    nuestros magic numbers, agrupa por signal_id (parseando comments),
    e identifica cuál es el market vs los DCAs.

    DCAs viejos (comment "DCA_X" sin signal_id) se asocian al market más
    cercano en tiempo de apertura (mismo magic, abierto antes que el DCA).

    Solo devuelve grupos que tengan al menos 1 market position. Los DCAs
    huérfanos sin market identificable se ignoran (no podemos saber a qué
    señal pertenecen).
    """
    positions = mt5.positions_get()
    if not positions:
        return {}

    our_magics = (config.MT5_MAGIC_CANAL1, config.MT5_MAGIC_CANAL2)
    our_positions = [p for p in positions if p.magic in our_magics]
    if not our_positions:
        return {}

    # Step 1: identificar markets (con signal_id en comment)
    groups: dict[str, dict] = {}
    dcas_old_format = []  # DCAs sin signal_id, asociar después

    for p in our_positions:
        parsed = _parse_signal_id_from_comment(p.comment)
        if parsed is None and _RX_DCA_OLD.match(p.comment or ""):
            # DCA formato viejo — asociar después por tiempo
            dcas_old_format.append(p)
            continue
        if parsed is None:
            continue
        channel, message_id = parsed
        sig_id = f"{channel}_{message_id}"

        comment = p.comment or ""
        # _B (doble market) o _B1.._B4 (legs del scale_out) → posicion extra
        is_market_b = bool(_re_resync.search(r"_B\d*$", comment))
        is_rescue = "_rescue" in comment
        is_market = (bool(_RX_MARKET.match(comment))
                     and not is_rescue and not is_market_b)
        is_dca = bool(_RX_DCA_NEW.match(comment))

        if sig_id not in groups:
            groups[sig_id] = {
                "channel": channel,
                "message_id": message_id,
                "magic": p.magic,
                "direction": "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
                "market_ticket": None,
                "market_price": None,
                "market_sl": None,
                "market_tp": None,
                "market_open_time": None,  # epoch UTC (MT5 position.time)
                "extra_market_tickets": [],   # Market B del doble market
                "dca_tickets": [],
                "resync_anchor_role": None,
                "_surviving_market_candidates": [],
            }

        if is_market:
            groups[sig_id]["market_ticket"] = p.ticket
            groups[sig_id]["market_price"] = p.price_open
            groups[sig_id]["market_sl"] = p.sl if p.sl > 0 else None
            groups[sig_id]["market_tp"] = p.tp if p.tp > 0 else None
            # p.time es el epoch UTC de apertura de la posición en MT5.
            # Lo necesitamos para reconstruir signal.timestamp tras un restart
            # y que el time-stop dispare al cabo del tiempo correcto.
            groups[sig_id]["market_open_time"] = int(p.time) if p.time else None
            groups[sig_id]["resync_anchor_role"] = "original_market"
        elif is_market_b:
            # Market B del doble market — antes se ignoraba (no matcheaba
            # ningun regex). canal2_12497 perdio +$6.05 por esto.
            groups[sig_id]["extra_market_tickets"].append(p.ticket)
            groups[sig_id]["_surviving_market_candidates"].append(p)
        elif is_rescue or is_dca:
            groups[sig_id]["dca_tickets"].append(p.ticket)

    # Step 2: asociar DCAs viejos al market más cercano del mismo magic
    # abierto antes que el DCA. Heurística simple pero suficiente.
    # The original leg can close before a restart while later scale-out legs
    # remain open. Use the earliest survivor as a stable reconstruction anchor.
    for group in groups.values():
        if group["market_ticket"] or not group["_surviving_market_candidates"]:
            continue
        anchor = min(
            group["_surviving_market_candidates"],
            key=lambda p: (int(p.time or 0), int(p.ticket)),
        )
        group["market_ticket"] = anchor.ticket
        group["market_price"] = anchor.price_open
        group["market_sl"] = anchor.sl if anchor.sl > 0 else None
        group["market_tp"] = anchor.tp if anchor.tp > 0 else None
        group["market_open_time"] = int(anchor.time) if anchor.time else None
        group["extra_market_tickets"].remove(anchor.ticket)
        group["resync_anchor_role"] = "surviving_scale_out_leg"

    for dca in dcas_old_format:
        candidate_groups = [
            (sid, g) for sid, g in groups.items()
            if g["magic"] == dca.magic and g["market_ticket"]
            and g["market_ticket"] < dca.ticket  # market abierto antes (ticket asciendente)
        ]
        if not candidate_groups:
            continue
        # El más cercano = mayor market_ticket entre los anteriores al DCA
        best_sid, best_g = max(candidate_groups, key=lambda x: x[1]["market_ticket"])
        best_g["dca_tickets"].append(dca.ticket)

    # Step 3: filtrar grupos sin market (no podemos reconstruir signal sin él)
    result = {}
    for sid, group in groups.items():
        group.pop("_surviving_market_candidates", None)
        if group["market_ticket"]:
            result[sid] = group
    return result
