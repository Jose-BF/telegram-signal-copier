"""
Wrapper MT5. Todas las funciones son síncronas.
El listener las ejecuta en un executor thread para no bloquear el loop async.
"""

import re
import time

import MetaTrader5 as mt5
from typing import Optional
import config
import mt5_errors


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


_RX_ORDER_SIG = re.compile(r"^(DCA_)?c([12])_(\d{4,})(?:_[A-Za-z0-9.]+)?")


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
    }


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


def init() -> bool:
    if not mt5.initialize():
        print(f"[MT5] initialize() falló: {mt5.last_error()}")
        return False
    if not mt5.login(config.MT5_LOGIN, password=config.MT5_PASSWORD, server=config.MT5_SERVER):
        print(f"[MT5] login() falló: {mt5.last_error()}")
        return False
    # Asegura que el símbolo esté en Market Watch. Sin esto, symbol_info_tick()
    # devuelve None aunque el símbolo exista en el broker. Causa real del crash
    # "No se puede obtener precio de XAUUSD" tras un reinicio del terminal.
    if not mt5.symbol_select(config.MT5_SYMBOL, True):
        print(f"[MT5] No se pudo añadir {config.MT5_SYMBOL} a Market Watch: {mt5.last_error()}")
        return False
    info = mt5.account_info()
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

    Devuelve dict {bid, ask, mid, spread} o None si MT5 no responde.
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
        }
    except Exception as e:
        print(f"[MT5] current_tick_safe excepcion: {type(e).__name__}: {e}")
        return None


def _send_safe(req: dict, label: str):
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
        print(f"[MT5] {label}: order_send devolvio None — "
              f"last_error={last_err}")
        _emit_anomaly("bot", "mt5", "critical",
                      f"mt5.order_send devolvio None ({label}) — IPC con "
                      f"terminal MT5 fallo, operacion en estado indefinido",
                      label=label, last_error=str(last_err),
                      action=req.get("action"), symbol=req.get("symbol"))
    return res


def _ask_bid(direction: str) -> float:
    tick = mt5.symbol_info_tick(config.MT5_SYMBOL)
    if tick is None:
        # Defensivo: si MT5 perdió la suscripción del símbolo (puede pasar tras
        # reconexión del terminal o weekend rollover), reintentamos una vez
        # tras volver a añadirlo a Market Watch. Si vuelve a fallar, es real.
        mt5.symbol_select(config.MT5_SYMBOL, True)
        tick = mt5.symbol_info_tick(config.MT5_SYMBOL)
        if tick is None:
            raise RuntimeError(f"No se puede obtener precio de {config.MT5_SYMBOL} (last_error={mt5.last_error()})")
    return tick.ask if direction == "BUY" else tick.bid


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
    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    # _ask_bid puede lanzar RuntimeError si MT5 esta desconectado. Sin
    # try/except, la excepcion sube hasta el handler de Telethon y la senal
    # se pierde sin journal de fallo. Mejor: log + return None, el caller
    # en listener vera el None y registrara market_fill_failed.
    try:
        price = _ask_bid(direction)
    except RuntimeError as e:
        print(f"[MT5] open_market: no pude leer tick ({e}) -> abort")
        return None

    # Si el SL queda dentro del stops_level del broker, lo ajustamos al mínimo legal
    # para que la orden no sea rechazada con "invalid stops".
    sl = safe_sl(direction, sl)

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

    sig_id = _sig_id_from_order_comment(req.get("comment"))
    _emit_event(sig_id, "mt5_order_requested",
                order_kind="market",
                direction=direction,
                lot=lot,
                requested_price=price,
                sl=req.get("sl"),
                tp=req.get("tp"),
                magic=req.get("magic"),
                comment=req.get("comment"),
                deviation=req.get("deviation"))
    res = _send_safe(req, f"open_market {direction} lot={lot}")
    _emit_event(sig_id, "mt5_order_result",
                order_kind="market",
                direction=direction,
                requested_price=price,
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
                        fill_price=round(float(fill_price), 2))
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

    sig_id = _sig_id_from_order_comment(req.get("comment"))
    _emit_event(sig_id, "mt5_order_requested",
                order_kind="pending_limit",
                direction=direction,
                lot=lot,
                requested_price=price,
                sl=req.get("sl"),
                tp=req.get("tp"),
                magic=req.get("magic"),
                comment=req.get("comment"),
                deviation=req.get("deviation"))
    res = _send_safe(req, f"place_limit {direction} @ {price}")
    _emit_event(sig_id, "mt5_order_result",
                order_kind="pending_limit",
                direction=direction,
                requested_price=price,
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


def modify_sltp_rc(ticket: int, new_sl: Optional[float] = None,
                    new_tp: Optional[float] = None,
                    expected_magic: Optional[int] = None) -> int:
    """Modifica SL y/o TP. Si uno es None, conserva el valor actual.
    Si expected_magic no es None, verifica que la posición tiene ese magic.
    Devuelve el retcode MT5 para poder clasificarlo."""
    pos = mt5.positions_get(ticket=ticket)
    if pos:
        p = pos[0]
        if not _assert_magic(ticket, p.magic, expected_magic):
            return mt5.TRADE_RETCODE_INVALID
        sl_final = new_sl if new_sl is not None else p.sl
        tp_final = new_tp if new_tp is not None else p.tp
        req = {
            "action":   mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "sl":       sl_final,
            "tp":       tp_final,
        }
        res = _send_safe(req, f"modify_sltp position={ticket}")
        if res is None:
            return mt5.TRADE_RETCODE_MARKET_CLOSED  # TRANSIENT -> RETRY
        tag = f"SL={sl_final} TP={tp_final}"
        if res.retcode == mt5.TRADE_RETCODE_DONE:
            print(f"[MT5] SLTP modificado ticket={ticket} → {tag}")
        else:
            print(f"[MT5] modify_sltp ticket={ticket} → {tag}: {res.retcode} — {res.comment}")
        return res.retcode

    orders = mt5.orders_get(ticket=ticket)
    if orders:
        o = orders[0]
        if not _assert_magic(ticket, o.magic, expected_magic):
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
        res = _send_safe(req, f"modify_sltp pending order={ticket}")
        if res is None:
            return mt5.TRADE_RETCODE_MARKET_CLOSED  # TRANSIENT -> RETRY
        tag = f"SL={sl_final} TP={tp_final}"
        if res.retcode == mt5.TRADE_RETCODE_DONE:
            print(f"[MT5] SLTP límite modificado ticket={ticket} → {tag}")
        else:
            print(f"[MT5] modify_sltp pendiente ticket={ticket}: {res.retcode} — {res.comment}")
        return res.retcode

    print(f"[MT5] modify_sltp: ticket {ticket} no encontrado")
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

def close_position_rc(ticket: int, expected_magic: Optional[int] = None) -> int:
    pos = mt5.positions_get(ticket=ticket)
    if not pos:
        print(f"[MT5] close_position: ticket {ticket} no encontrado")
        return mt5.TRADE_RETCODE_INVALID

    p = pos[0]
    if not _assert_magic(ticket, p.magic, expected_magic):
        return mt5.TRADE_RETCODE_INVALID
    close_dir = "SELL" if p.type == mt5.POSITION_TYPE_BUY else "BUY"
    order_type = mt5.ORDER_TYPE_SELL if p.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
    # _ask_bid puede lanzar RuntimeError si MT5 perdio el tick (desconexion
    # o weekend). Sin este try/except, la excepcion sube hasta
    # pending_actions._try_once, no es retcode -> accion queda colgada.
    # Devolvemos un retcode TRANSIENT para que la cola reintente cuando
    # MT5 vuelva a responder.
    try:
        price = _ask_bid(close_dir)
    except RuntimeError as e:
        print(f"[MT5] close_position ticket={ticket}: no pude leer tick "
              f"({e}) -> retorno TRANSIENT para reintento")
        return mt5.TRADE_RETCODE_MARKET_CLOSED

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
    res = _send_safe(req, f"close_position ticket={ticket}")
    if res is None:
        return mt5.TRADE_RETCODE_MARKET_CLOSED  # TRANSIENT -> RETRY
    if res.retcode == mt5.TRADE_RETCODE_DONE:
        print(f"[MT5] Posición cerrada ticket={ticket}")
    else:
        print(f"[MT5] close_position falló ticket={ticket}: {res.retcode} — {res.comment}")
    return res.retcode


def close_position(ticket: int, expected_magic: Optional[int] = None) -> bool:
    return close_position_rc(ticket, expected_magic) == mt5.TRADE_RETCODE_DONE


def cancel_pending_rc(ticket: int, expected_magic: Optional[int] = None) -> int:
    if expected_magic is not None:
        orders = mt5.orders_get(ticket=ticket)
        if not orders:
            print(f"[MT5] cancel_pending: ticket {ticket} no encontrado")
            return mt5.TRADE_RETCODE_INVALID
        if not _assert_magic(ticket, orders[0].magic, expected_magic):
            return mt5.TRADE_RETCODE_INVALID

    res = _send_safe(
        {"action": mt5.TRADE_ACTION_REMOVE, "order": ticket},
        f"cancel_pending ticket={ticket}",
    )
    if res is None:
        return mt5.TRADE_RETCODE_MARKET_CLOSED  # TRANSIENT -> RETRY
    if res.retcode == mt5.TRADE_RETCODE_DONE:
        print(f"[MT5] Pendiente cancelado ticket={ticket}")
    else:
        print(f"[MT5] cancel_pending falló ticket={ticket}: {res.retcode} — {res.comment}")
    return res.retcode


def cancel_pending(ticket: int, expected_magic: Optional[int] = None) -> bool:
    return cancel_pending_rc(ticket, expected_magic) == mt5.TRADE_RETCODE_DONE


# ─── Apertura con ajuste de SL ilegal ─────────────────────────────────────────

def safe_sl(direction: str, desired_sl: Optional[float]) -> Optional[float]:
    """Ajusta SL al mínimo legal del broker. None si no hay tick.

    Batch D: si desired_sl ESTABA seteado pero adjust_sl_to_legal devuelve
    None (broker no responde tick), DEVOLVEMOS None y el caller dropea
    el SL del request → la posicion se abre SIN STOP LOSS. Emite anomaly
    critical en ese caso para que la apertura sin proteccion quede
    registrada.
    """
    if desired_sl is None:
        return None
    adjusted = mt5_errors.adjust_sl_to_legal(direction, desired_sl)
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
        elif is_market_b:
            # Market B del doble market — antes se ignoraba (no matcheaba
            # ningun regex). canal2_12497 perdio +$6.05 por esto.
            groups[sig_id]["extra_market_tickets"].append(p.ticket)
        elif is_rescue or is_dca:
            groups[sig_id]["dca_tickets"].append(p.ticket)

    # Step 2: asociar DCAs viejos al market más cercano del mismo magic
    # abierto antes que el DCA. Heurística simple pero suficiente.
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
    return {sid: g for sid, g in groups.items() if g["market_ticket"]}
