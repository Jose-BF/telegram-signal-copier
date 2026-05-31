"""
Cola de acciones pendientes con reintentos tick-a-tick.

Cuando MT5 rechaza una acción por "invalid stops" (p.ej. BE con precio adverso),
no la descartamos: la encolamos y la reintentamos en cada tick hasta que:
  - tenga éxito,
  - la señal se cierre,
  - o la acción expire por timeout.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import MetaTrader5 as mt5

import config
import executor
import mt5_errors
from state import Signal


# Timeout por defecto: una hora. Si tras ese tiempo la acción no se ha podido
# ejecutar, se descarta para no acumular basura indefinidamente.
DEFAULT_TIMEOUT_S = 3600

# Cap defensivo para errores STOPS estructurales. Si retcode 10016 (INVALID_STOPS)
# persiste durante este tiempo, es un error de configuración del SL/TP (al lado
# equivocado del precio para el entry actual), no un edge case temporal: ningún
# retry lo va a arreglar. Dropear y notificar al usuario para acción manual.
# Sesión 2026-05-06: 3 acciones inválidas hicieron 27.000+ retries en 173 min,
# saturando el event loop y congelando el bot 115 min. Esto lo previene.
STOPS_STRUCTURAL_THRESHOLD_S = 30

# Batch E: cuando una pending action sigue en TRANSIENT >5min, el queue
# esta atascado por algo estructural (autotrading off, broker desconectado).
# A diferencia de STOPS no dropeamos — el mercado puede volver y queremos
# que la accion se ejecute cuando lo haga. Solo emitimos warning una vez
# para que el user sepa.
TRANSIENT_STUCK_THRESHOLD_S = 300

# Batch E: cuando _run() ve N ticks consecutivos None de mt5.symbol_info_tick,
# es indicacion fuerte de broker/MT5 down. Emitimos anomaly criticla.
NULL_TICK_STREAK_THRESHOLD = 500   # ~5s a 10ms por ciclo


# ─── Helpers PUROS de Batch E (alertas de loops async atascados) ──────────

def _should_alert_null_tick_streak(streak: int, already_alerted: bool,
                                    threshold: int) -> bool:
    """True solo si el streak de None-ticks consecutivos alcanza el
    threshold y aun no se emitio anomaly para este episodio.

    Mismo patron que main._should_alert_sustained_disconnect: se resetea
    a False cuando el broker vuelve a dar ticks (en el caller).
    """
    return (streak >= threshold) and (not already_alerted)


def _stuck_transient_severity(retcode: int, age_s: float,
                               threshold_s: float,
                               already_warned: bool):
    """Severidad para una pending action TRANSIENT atascada en cola.

    Solo aplica a TRANSIENT (10004/10008/10018/10021/10027) — los STOPS
    se dropean en STOPS_STRUCTURAL_THRESHOLD_S (30s) por otro path. Aqui
    cubrimos el resto: si retcode TRANSIENT persiste >threshold_s,
    emitimos warning una vez para que el user sepa que algo bloquea la
    cola.
    """
    import mt5_errors as _mt5e_b
    if _mt5e_b.classify(retcode) != "TRANSIENT":
        return None
    if already_warned:
        return None
    if age_s < threshold_s:
        return None
    return "warning"


@dataclass
class PendingAction:
    kind: str                       # "MODIFY_SLTP" | "CLOSE_POSITION" | "CANCEL_PENDING"
    ticket: int
    signal: Signal
    new_sl: Optional[float] = None  # solo para MODIFY_SLTP (None = conservar)
    new_tp: Optional[float] = None  # solo para MODIFY_SLTP (None = conservar)
    created_at: float = field(default_factory=time.time)
    timeout_s: float = DEFAULT_TIMEOUT_S
    attempts: int = 0
    last_retcode: Optional[int] = None
    label: str = ""                 # descripción humana para logs

    def expired(self) -> bool:
        return (time.time() - self.created_at) > self.timeout_s


class PendingQueue:
    def __init__(self):
        self._actions: list[PendingAction] = []
        self._task: Optional[asyncio.Task] = None

    def add(self, action: PendingAction):
        self._actions.append(action)
        print(f"[Pending] Encolado: {action.label} (ticket={action.ticket})")
        self._log_request(action)
        self._ensure_runner()

    def _ensure_runner(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
            # Batch E: detector de crash del runner. Si _run() lanza
            # excepcion no manejada, la task muere y la cola queda
            # bloqueada hasta el proximo add(). Mas grave que un crash de
            # DCA monitor (afecta TODA la gestion del bot, no una senal).
            self._task.add_done_callback(_runner_done_callback)

    async def _run(self):
        """Escanea la cola en cada tick nuevo hasta vaciarla.

        Batch E: instrumentamos contra los 2 modos de atasco silencioso:
          1) Null-tick streak — mt5.symbol_info_tick devolviendo None
             repetidamente (broker/MT5 down): emite anomaly tras
             NULL_TICK_STREAK_THRESHOLD None consecutivos.
          2) Stuck transient retcode — una accion lleva >5min con retcode
             TRANSIENT (10018/10027): emite warning una vez.
        """
        symbol = config.MT5_SYMBOL
        last_tick_ms = 0
        null_tick_streak = 0
        null_tick_alerted = False

        while self._actions:
            tick = await asyncio.to_thread(mt5.symbol_info_tick, symbol)
            if not tick:
                null_tick_streak += 1
                if _should_alert_null_tick_streak(
                        null_tick_streak, null_tick_alerted,
                        NULL_TICK_STREAK_THRESHOLD):
                    try:
                        import journal as _j_pa
                        _j_pa.anomaly("bot", "mt5", "critical",
                                      f"pending_actions: {null_tick_streak} "
                                      f"ticks consecutivos None de MT5 — "
                                      f"broker/terminal disconnected, cola "
                                      f"de {len(self._actions)} acciones bloqueada",
                                      streak=null_tick_streak,
                                      n_queued=len(self._actions),
                                      last_error=str(mt5.last_error()))
                    except Exception:
                        pass
                    null_tick_alerted = True
                await asyncio.sleep(0)
                continue
            if null_tick_alerted:
                # Recovery — el broker volvio. Reset para permitir alertar
                # el proximo episodio.
                try:
                    import journal as _j_pa
                    _j_pa.event("bot", "mt5_tick_recovered",
                                streak_resolved=null_tick_streak)
                except Exception:
                    pass
            null_tick_streak = 0
            null_tick_alerted = False
            if tick.time_msc == last_tick_ms:
                await asyncio.sleep(0)
                continue
            last_tick_ms = tick.time_msc

            still_pending: list[PendingAction] = []
            for act in self._actions:
                # MODIFY_SLTP pierde sentido si la señal ya está cerrada: la posición
                # puede haber sido cerrada por SL/TP, o por una orden de CLOSE_ALL.
                # CLOSE_POSITION y CANCEL_PENDING, en cambio, son precisamente las
                # acciones que cierran la señal → siguen válidas aunque esté "closed".
                if act.kind == "MODIFY_SLTP" and act.signal.status != "open":
                    print(f"[Pending] Descartado (señal cerrada): {act.label}")
                    self._log_failure(act, reason="signal_closed_during_retry")
                    continue
                if act.expired():
                    print(f"[Pending] Descartado (timeout {act.timeout_s}s): {act.label}")
                    self._log_failure(act, reason="timeout")
                    continue

                # Batch E: wrap _try_once en try/except. Si executor crashea
                # (e.g. MT5 timeout dentro de run_in_executor), antes la
                # excepcion subia hasta _run y mataba TODA la cola — el
                # add_done_callback de _ensure_runner ni siquiera deteccion
                # consistente (Python no re-lanza task exceptions). Aqui
                # la atrapamos para que el item se reintente y el resto de
                # la cola siga corriendo.
                try:
                    result = await self._try_once(act)
                except Exception as e:
                    print(f"[Pending] _try_once excepcion {type(e).__name__}: "
                          f"{e} → reintento (intentos={act.attempts})")
                    try:
                        import journal as _j_pa
                        sig_id = f"{act.signal.channel}_{act.signal.message_id}"
                        _j_pa.anomaly(sig_id, "mt5", "warning",
                                      f"pending_actions._try_once excepcion en "
                                      f"{act.kind}: {type(e).__name__}: "
                                      f"{str(e)[:200]}",
                                      kind=act.kind, ticket=act.ticket,
                                      attempts=act.attempts,
                                      exc_type=type(e).__name__)
                    except Exception:
                        pass
                    # Marca RETRY para que la cola siga
                    result = "RETRY"

                # Batch E: stuck transient warning. Si el retcode es TRANSIENT
                # y la accion lleva >5min en cola, algo estructural bloquea
                # (autotrading desactivado permanentemente, broker disconnected
                # >5min, etc). Warning una vez por accion para no spammear.
                if result == "RETRY" and act.last_retcode is not None:
                    age = time.time() - act.created_at
                    stuck_sev = _stuck_transient_severity(
                        act.last_retcode, age, TRANSIENT_STUCK_THRESHOLD_S,
                        getattr(act, "_stuck_warned", False))
                    if stuck_sev:
                        try:
                            import journal as _j_pa
                            sig_id = f"{act.signal.channel}_{act.signal.message_id}"
                            _j_pa.anomaly(sig_id, "mt5", stuck_sev,
                                          f"{act.kind} atascada con retcode "
                                          f"TRANSIENT {act.last_retcode} "
                                          f">{int(age)}s — posible autotrading "
                                          f"OFF, broker desconectado, etc. "
                                          f"Cola sigue reintentando.",
                                          kind=act.kind, ticket=act.ticket,
                                          retcode=act.last_retcode,
                                          age_s=int(age),
                                          attempts=act.attempts)
                        except Exception:
                            pass
                        act._stuck_warned = True

                if result == "DONE":
                    print(f"[Pending] ✓ Resuelto tras {act.attempts} intentos: {act.label}")
                    # SL movido y CONFIRMADO en MT5 → registrar el SL real de
                    # este ticket en la senal. Sin esto, signal.sl (el SL del
                    # proveedor) queda obsoleto y _classify_closures etiqueta
                    # el cierre como "MANUAL" en vez de "SL". Solo en exito
                    # real (retcode OK): si fue POSITION_GONE el modify no se
                    # aplico (la posicion ya estaba cerrada).
                    if (act.kind == "MODIFY_SLTP" and act.new_sl is not None
                            and mt5_errors.classify(act.last_retcode) == "OK"):
                        act.signal.sl_by_ticket[act.ticket] = act.new_sl
                    cls_done = mt5_errors.classify(act.last_retcode)
                    if (act.kind != "MODIFY_SLTP"
                            or cls_done in ("OK", "POSITION_GONE")):
                        self._log_done(act)
                elif result == "DROP":
                    print(f"[Pending] Descartado (error permanente {act.last_retcode}): {act.label}")
                    self._log_failure(act, reason=f"permanent_error_retcode_{act.last_retcode}")
                elif result == "DROP_STOPS_STRUCTURAL":
                    # Retcode 10016/10017/10015 atascado >30s — error estructural,
                    # no temporal. Logueamos y mandamos notificación urgente.
                    age_s = int(time.time() - act.created_at)
                    print(f"[Pending] ⚠ STOPS estructural ({act.last_retcode}) tras "
                          f"{act.attempts} intentos en {age_s}s: {act.label}")
                    self._log_failure(
                        act,
                        reason=f"stops_structural_after_{act.attempts}_attempts_{age_s}s"
                    )
                    asyncio.create_task(self._notify_structural_failure(act, age_s))
                else:
                    still_pending.append(act)

            self._actions = still_pending
            await asyncio.sleep(0)

    async def _notify_structural_failure(self, act: PendingAction, age_s: int):
        """Notifica al usuario por Telegram que MT5 está rechazando la acción.

        Best-effort: si notify() o el import falla, log y continúa.
        """
        try:
            from listener import notify
        except Exception as e:
            print(f"[Pending] No pude importar notify(): {e}")
            return

        sl_str = f"{act.new_sl}" if act.new_sl is not None else "(sin cambio)"
        tp_str = f"{act.new_tp}" if act.new_tp is not None else "(sin cambio)"
        text = (
            f"🚨 ACCIÓN MT5 BLOQUEADA — {act.signal.channel} #{act.signal.message_id}\n"
            f"\n"
            f"Tipo: {act.kind}\n"
            f"Ticket: {act.ticket}\n"
            f"Nuevo SL: {sl_str} | Nuevo TP: {tp_str}\n"
            f"\n"
            f"MT5 rechazó {act.attempts} intentos en {age_s}s con retcode "
            f"{act.last_retcode} (STOPS inválidos).\n"
            f"\n"
            f"Probable causa: SL/TP al lado equivocado del precio para el entry "
            f"actual. Revisa la posición #{act.ticket} en MT5 y ajústala "
            f"manualmente. El bot dejó de reintentar para no bloquearse."
        )
        try:
            await notify(text)
        except Exception as e:
            print(f"[Pending] notify() error: {e}")

    def _log_failure(self, act: PendingAction, reason: str):
        """Loguea fallo definitivo de una pending action al journal.

        Crítico para diagnosticar: si BE moves fallan repetidamente porque
        el precio ya pasó, o si un CLOSE_POSITION falla por desconexión MT5,
        antes no nos enterábamos. Ahora queda en logs para análisis.

        NOTA: NO resetear self._task aqui (bug C4). El runner sigue activo
        procesando otras acciones de la cola cuando una falla. Resetear
        self._task = None mientras _run sigue corriendo creaba race: la
        siguiente add() veia self._task is None y _ensure_runner arrancaba
        un SEGUNDO runner coexistiendo con el primero -> 2 runners
        procesando la misma queue, posibles double-execution. El task se
        autolimpia cuando _run termina por queue vacia (Python GC al perder
        referencia, o el if self._task.done() de _ensure_runner).
        """
        try:
            import journal
            sig_id = f"{act.signal.channel}_{act.signal.message_id}"
            journal.event(sig_id, "mt5_action_failed",
                          kind=act.kind,
                          ticket=act.ticket,
                          attempts=act.attempts,
                          last_retcode=act.last_retcode,
                          reason=reason,
                          label=act.label,
                          new_sl=act.new_sl,
                          new_tp=act.new_tp,
                          age_seconds=round(time.time() - act.created_at, 1))
            # Capa de anomalía estructurada (T3 del plan). Severidad por retcode:
            #   10036 (POSITION_CLOSED): pos ya cerrada → info (benigno).
            #   reason='stops_structural_*': MT5 lleva bloqueado >30s sin poder
            #       aplicar el modify → critical (dinero potencialmente en riesgo).
            #   resto (timeout, otros permanent_errors): warning.
            if act.last_retcode == 10036:
                sev = "info"
            elif reason.startswith("stops_structural"):
                sev = "critical"
            else:
                sev = "warning"
            journal.anomaly(sig_id, "mt5", sev,
                            f"{act.kind} falló: {reason}",
                            ticket=act.ticket, retcode=act.last_retcode,
                            attempts=act.attempts, label=act.label,
                            age_seconds=round(time.time() - act.created_at, 1))
        except Exception as e:
            print(f"[Pending] _log_failure error: {e}")

    def _log_request(self, act: PendingAction):
        """Journal forense: accion MT5 encolada por el bot."""
        try:
            import journal
            sig_id = f"{act.signal.channel}_{act.signal.message_id}"
            if act.kind == "MODIFY_SLTP":
                journal.event(sig_id, "mt5_modify_requested",
                              ticket=act.ticket, new_sl=act.new_sl,
                              new_tp=act.new_tp, label=act.label)
            elif act.kind == "CLOSE_POSITION":
                journal.event(sig_id, "mt5_close_requested",
                              ticket=act.ticket, label=act.label)
            elif act.kind == "CANCEL_PENDING":
                journal.event(sig_id, "mt5_cancel_requested",
                              ticket=act.ticket, label=act.label)
        except Exception:
            pass

    def _position_snapshot(self, act: PendingAction) -> dict:
        base = {
            "ticket": act.ticket,
            "after_action": act.kind,
            "retcode": act.last_retcode,
            "label": act.label,
            "requested_sl": act.new_sl,
            "requested_tp": act.new_tp,
        }
        try:
            positions = mt5.positions_get(ticket=act.ticket)
            if positions is None:
                base.update({
                    "position_exists": None,
                    "snapshot_error": str(mt5.last_error()),
                })
                return base
            if not positions:
                base["position_exists"] = False
                return base

            pos = positions[0]
            base.update({
                "position_exists": True,
                "symbol": getattr(pos, "symbol", None),
                "magic": getattr(pos, "magic", None),
                "position_type": getattr(pos, "type", None),
                "volume": getattr(pos, "volume", None),
                "price_open": getattr(pos, "price_open", None),
                "price_current": getattr(pos, "price_current", None),
                "sl": getattr(pos, "sl", None),
                "tp": getattr(pos, "tp", None),
                "profit": getattr(pos, "profit", None),
                "comment": getattr(pos, "comment", None),
            })
            return base
        except Exception as e:
            base.update({
                "position_exists": None,
                "snapshot_error": f"{type(e).__name__}: {str(e)[:160]}",
            })
            return base

    def _log_position_snapshot(self, sig_id: str, act: PendingAction, journal):
        if act.kind not in ("MODIFY_SLTP", "CLOSE_POSITION"):
            return
        journal.event(sig_id, "mt5_position_snapshot",
                      **self._position_snapshot(act))

    def _log_done(self, act: PendingAction):
        """Journal forense: accion MT5 confirmada o ya innecesaria."""
        try:
            import journal
            sig_id = f"{act.signal.channel}_{act.signal.message_id}"
            payload = {
                "ticket": act.ticket,
                "attempts": act.attempts,
                "retcode": act.last_retcode,
                "label": act.label,
            }
            if act.kind == "MODIFY_SLTP":
                if mt5_errors.classify(act.last_retcode) == "POSITION_GONE":
                    journal.event(sig_id, "mt5_modify_skipped_position_gone",
                                  new_sl=act.new_sl, new_tp=act.new_tp,
                                  **payload)
                    return
                journal.event(sig_id, "mt5_modify_confirmed",
                              new_sl=act.new_sl, new_tp=act.new_tp,
                              **payload)
                self._log_position_snapshot(sig_id, act, journal)
            elif act.kind == "CLOSE_POSITION":
                journal.event(sig_id, "mt5_close_result", **payload)
                self._log_position_snapshot(sig_id, act, journal)
            elif act.kind == "CANCEL_PENDING":
                journal.event(sig_id, "mt5_cancel_result", **payload)
        except Exception:
            pass

    async def _try_once(self, act: PendingAction) -> str:
        """Ejecuta el intento. Devuelve 'DONE', 'RETRY' o 'DROP'.

        Todas las acciones verifican el magic del canal (act.signal.magic):
        si el ticket pertenece a otro canal o a una operación manual, el
        executor devuelve INVALID y la acción se descarta sin tocar nada."""
        act.attempts += 1
        loop = asyncio.get_event_loop()
        expected_magic = act.signal.magic

        if act.kind == "MODIFY_SLTP":
            retcode = await loop.run_in_executor(
                None, lambda: executor.modify_sltp_rc(
                    act.ticket, act.new_sl, act.new_tp, expected_magic=expected_magic
                )
            )
        elif act.kind == "CLOSE_POSITION":
            retcode = await loop.run_in_executor(
                None, lambda: executor.close_position_rc(act.ticket, expected_magic=expected_magic)
            )
        elif act.kind == "CANCEL_PENDING":
            retcode = await loop.run_in_executor(
                None, lambda: executor.cancel_pending_rc(act.ticket, expected_magic=expected_magic)
            )
        else:
            return "DROP"

        act.last_retcode = retcode
        cls = mt5_errors.classify(retcode)

        if cls == "OK":
            return "DONE"
        if cls == "POSITION_GONE":
            # La posición ya no existe → nada que hacer, éxito implícito
            return "DONE"
        if cls == "TRANSIENT":
            return "RETRY"
        if cls == "STOPS":
            # SL/TP inválidos respecto al precio actual. Si lleva poco tiempo
            # encolada, probablemente el precio se movió temporalmente cerca
            # del nivel y el retry resolverá. Si lleva >30s, es estructural
            # (SL al lado equivocado del precio para el entry actual): ningún
            # retry lo arregla. Cortamos para no bloquear el event loop.
            age = time.time() - act.created_at
            if age > STOPS_STRUCTURAL_THRESHOLD_S:
                return "DROP_STOPS_STRUCTURAL"
            return "RETRY"
        return "DROP"


def _runner_done_callback(task):
    """Batch E: si el runner de PendingQueue muere por excepcion no
    manejada (no CancelledError), emite anomaly critical — la cola
    queda BLOQUEADA hasta el proximo add(), ninguna gestion se ejecuta.
    """
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    except Exception:
        return
    if exc is None or isinstance(exc, asyncio.CancelledError):
        return
    try:
        import journal as _j_runner
        _j_runner.anomaly("bot", "mt5", "critical",
                          f"PendingQueue runner CRASHEO con "
                          f"{type(exc).__name__}: {str(exc)[:200]} — la cola "
                          f"de gestion MT5 queda bloqueada hasta el proximo "
                          f"enqueue. Acciones encoladas no se ejecutaran.",
                          exc_type=type(exc).__name__, exc_msg=str(exc)[:300])
    except Exception:
        pass


queue = PendingQueue()


def snapshot(queue_obj: PendingQueue | None = None,
             now: float | None = None) -> list[dict]:
    """Snapshot read-only de la cola para auditoria externa."""
    q = queue_obj or queue
    ts = time.time() if now is None else now
    out = []
    for act in list(q._actions):
        sig_id = f"{act.signal.channel}_{act.signal.message_id}"
        out.append({
            "sig_id": sig_id,
            "kind": act.kind,
            "ticket": act.ticket,
            "new_sl": act.new_sl,
            "new_tp": act.new_tp,
            "age_s": round(ts - act.created_at, 1),
            "attempts": act.attempts,
            "last_retcode": act.last_retcode,
            "label": act.label,
        })
    return out


# ─── Helpers de alto nivel ─────────────────────────────────────────────────────

def enqueue_modify_sl(signal: Signal, ticket: int, new_sl: float, label: str = ""):
    queue.add(PendingAction(
        kind="MODIFY_SLTP",
        ticket=ticket,
        signal=signal,
        new_sl=new_sl,
        label=label or f"modify SL→{new_sl}",
    ))


def enqueue_modify_tp(signal: Signal, ticket: int, new_tp: float, label: str = ""):
    queue.add(PendingAction(
        kind="MODIFY_SLTP",
        ticket=ticket,
        signal=signal,
        new_tp=new_tp,
        label=label or f"modify TP→{new_tp}",
    ))


def enqueue_modify_sltp(signal: Signal, ticket: int, new_sl: float, new_tp: float, label: str = ""):
    queue.add(PendingAction(
        kind="MODIFY_SLTP",
        ticket=ticket,
        signal=signal,
        new_sl=new_sl,
        new_tp=new_tp,
        label=label or f"modify SL→{new_sl} TP→{new_tp}",
    ))


def enqueue_close_position(signal: Signal, ticket: int, label: str = ""):
    queue.add(PendingAction(
        kind="CLOSE_POSITION",
        ticket=ticket,
        signal=signal,
        label=label or "close position",
    ))


def enqueue_cancel_pending(signal: Signal, ticket: int, label: str = ""):
    queue.add(PendingAction(
        kind="CANCEL_PENDING",
        ticket=ticket,
        signal=signal,
        label=label or "cancel pending",
    ))
