"""
Cola de acciones pendientes con reintentos tick-a-tick.

Cuando un SL todavía no es legal (p.ej. BE con precio adverso), queda en espera
sin enviar solicitudes repetidas a MT5 hasta que el mercado cumpla la condición:
  - tenga éxito,
  - la señal se cierre,
  - o la acción expire por timeout.
"""

import asyncio
import json
import os
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional

import MetaTrader5 as mt5

import causal_trace
import config
import executor
import mt5_errors
import runtime_paths
from provider_names import provider_display_name
from state import Signal


# Timeout por defecto: una hora. Si tras ese tiempo la acción no se ha podido
# ejecutar, se descarta para no acumular basura indefinidamente.
DEFAULT_TIMEOUT_S = 3600

# Batch E: cuando una pending action sigue en TRANSIENT >5min, el queue
# esta atascado por algo estructural (autotrading off, broker desconectado).
# A diferencia de STOPS no dropeamos — el mercado puede volver y queremos
# que la accion se ejecute cuando lo haga. Solo emitimos warning una vez
# para que el user sepa.
TRANSIENT_STUCK_THRESHOLD_S = 300
STOPS_STUCK_THRESHOLD_S = 30
EXACT_BE_WAIT_ALERT_THRESHOLD_S = 30

# Batch E: cuando _run() ve N ticks consecutivos None de mt5.symbol_info_tick,
# es indicacion fuerte de broker/MT5 down. Emitimos anomaly criticla.
NULL_TICK_STREAK_THRESHOLD = 500   # ~5s a 10ms por ciclo
BROKER_RETRY_COOLDOWN_S = 1.0
CONFIRMED_ACTION_EVIDENCE_TTL_S = 120.0
PENDING_SPOOL_FILE = Path(os.getenv(
    "BOT_PENDING_ACTIONS_FILE",
    str(runtime_paths.data_path("runtime_pending_actions.json")),
))


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

    Solo aplica a TRANSIENT (10004/10008/10018/10021/10027). Los stops
    temporalmente ilegales esperan antes de llamar a MT5. Aqui cubrimos el
    resto: si retcode TRANSIENT persiste >threshold_s,
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


def _should_alert_stuck_stops(retcode: int, age_s: float,
                              threshold_s: float,
                              already_alerted: bool) -> bool:
    """True once when MT5 keeps rejecting an otherwise valid SL/TP."""
    return bool(
        mt5_errors.classify(retcode) == "STOPS"
        and age_s >= threshold_s
        and not already_alerted
    )


def _should_alert_waiting_exact_be(action, age_s: float,
                                   threshold_s: float) -> bool:
    """Alert once when an exact provider BE remains illegal at market."""
    return bool(
        action.kind == "MODIFY_SLTP"
        and action.persist_until_signal_close
        and action.new_sl is not None
        and action.waiting_reason
        and age_s >= threshold_s
        and not action.stops_alerted
    )


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
    applied_tp: Optional[float] = None
    waiting_reason: Optional[str] = None
    label: str = ""                 # descripción humana para logs
    persist_until_signal_close: bool = False
    retry_not_before: float = 0.0
    stops_alerted: bool = False
    revision: int = 0
    action_id: str = field(default_factory=causal_trace.new_action_id)
    decision_id: Optional[str] = None
    message_revision_id: Optional[str] = None
    last_attempt_id: Optional[str] = None
    lineage_recovered_from_legacy_spool: bool = False
    parent_decision_id: Optional[str] = None
    decision_origin: Optional[str] = None
    internal_reason: str = ""
    last_preflight_status: Optional[str] = None
    last_preflight_reason: Optional[str] = None
    last_preflight_effective_sl: Optional[float] = None
    last_preflight_effective_tp: Optional[float] = None
    last_preflight_deferred_sl: Optional[float] = None
    last_preflight_observed_ticket: Optional[int] = None
    last_preflight_observed_magic: Optional[int] = None
    last_preflight_observed_kind: Optional[str] = None

    def __post_init__(self) -> None:
        active = causal_trace.current_context()
        decision_was_supplied = self.decision_id is not None
        if self.message_revision_id is None:
            self.message_revision_id = (
                active.message_revision_id
                or getattr(
                    self.signal,
                    "source_message_revision_id",
                    None,
                )
            )
        if self.decision_id is None:
            if active.decision_id is not None:
                self.decision_id = active.decision_id
            else:
                self.decision_id = causal_trace.new_decision_id()
        if self.decision_origin is None:
            if active.decision_id == self.decision_id:
                self.decision_origin = (
                    active.decision_kind or "telegram"
                )
            elif decision_was_supplied:
                self.decision_origin = "explicit"
            else:
                self.decision_origin = "internal"
        if self.decision_origin == "internal":
            if self.parent_decision_id is None:
                self.parent_decision_id = (
                    active.parent_decision_id
                    or getattr(
                        self.signal,
                        "source_decision_id",
                        None,
                    )
                )
            if not self.internal_reason:
                self.internal_reason = (
                    active.decision_reason
                    or self.label
                    or self.kind
                )

    def expired(self) -> bool:
        if self.persist_until_signal_close and self.signal.status == "open":
            return False
        return (time.time() - self.created_at) > self.timeout_s


def _lineage_fields(action: PendingAction) -> dict:
    return {
        "action_id": action.action_id,
        "decision_id": action.decision_id,
        "message_revision_id": action.message_revision_id,
        "action_revision": action.revision,
    }


def _preflight_fields(action: PendingAction) -> dict:
    return {
        "preflight_status": action.last_preflight_status,
        "preflight_reason": action.last_preflight_reason,
        "preflight_effective_sl": action.last_preflight_effective_sl,
        "preflight_effective_tp": action.last_preflight_effective_tp,
        "preflight_deferred_sl": action.last_preflight_deferred_sl,
        "preflight_observed_ticket": (
            action.last_preflight_observed_ticket
        ),
        "preflight_observed_magic": action.last_preflight_observed_magic,
        "preflight_observed_kind": action.last_preflight_observed_kind,
    }


def _remember_preflight(action: PendingAction, decision) -> None:
    action.last_preflight_status = getattr(decision, "status", None)
    action.last_preflight_reason = getattr(decision, "reason", None)
    action.last_preflight_effective_sl = getattr(
        decision,
        "effective_sl",
        None,
    )
    action.last_preflight_effective_tp = getattr(
        decision,
        "effective_tp",
        None,
    )
    action.last_preflight_deferred_sl = getattr(
        decision,
        "deferred_sl",
        None,
    )
    action.last_preflight_observed_ticket = getattr(
        decision,
        "observed_ticket",
        None,
    )
    action.last_preflight_observed_magic = getattr(
        decision,
        "observed_magic",
        None,
    )
    action.last_preflight_observed_kind = getattr(
        decision,
        "observed_kind",
        None,
    )


def _new_attempt_trace(action: PendingAction) -> dict:
    attempt_id = causal_trace.new_attempt_id()
    action.last_attempt_id = attempt_id
    return {
        "sig_id": f"{action.signal.channel}_{action.signal.message_id}",
        **_lineage_fields(action),
        "attempt_id": attempt_id,
        "expected_magic": action.signal.magic,
        **_preflight_fields(action),
    }


def _record_confirmed_levels(action: PendingAction) -> bool:
    """Persist per-ticket levels only after MT5 confirms a modification."""
    if action.kind != "MODIFY_SLTP":
        return False
    if mt5_errors.classify(action.last_retcode) != "OK":
        return False

    recorded = False
    if action.new_sl is not None:
        action.signal.sl_by_ticket[action.ticket] = action.new_sl
        recorded = True
    effective_tp = (
        action.new_tp if action.new_tp is not None else action.applied_tp
    )
    if effective_tp is not None:
        action.signal.tp_by_ticket[action.ticket] = effective_tp
        recorded = True
    return recorded


def _effective_action_tp(action: PendingAction) -> Optional[float]:
    """Return the requested TP, including a TP completed in an earlier stage."""
    return action.new_tp if action.new_tp is not None else action.applied_tp


class PendingQueue:
    def __init__(self, spool_path: Path | None = None):
        self._actions: list[PendingAction] = []
        self._recent_confirmed_actions: list[dict] = []
        self._task: Optional[asyncio.Task] = None
        self._structural_incidents: dict[tuple, list[PendingAction]] = {}
        self._structural_flush_tasks: dict[tuple, asyncio.Task] = {}
        self._spool_path = Path(spool_path) if spool_path is not None else None

    @staticmethod
    def _spool_payload(action: PendingAction) -> dict:
        return {
            "kind": action.kind,
            "ticket": int(action.ticket),
            "channel": action.signal.channel,
            "message_id": int(action.signal.message_id),
            "direction": action.signal.direction,
            "new_sl": action.new_sl,
            "new_tp": action.new_tp,
            "created_at": action.created_at,
            "timeout_s": action.timeout_s,
            "applied_tp": action.applied_tp,
            "label": action.label,
            "persist_until_signal_close": action.persist_until_signal_close,
            "revision": action.revision,
            "action_id": action.action_id,
            "decision_id": action.decision_id,
            "message_revision_id": action.message_revision_id,
            "parent_decision_id": action.parent_decision_id,
            "decision_origin": action.decision_origin,
            "internal_reason": action.internal_reason,
            "lineage_recovered_from_legacy_spool": (
                action.lineage_recovered_from_legacy_spool
            ),
        }

    def _spool_fingerprint(self) -> tuple:
        return tuple(
            json.dumps(
                self._spool_payload(action),
                sort_keys=True,
                separators=(",", ":"),
            )
            for action in self._actions
        )

    def _persist_spool(self) -> None:
        if self._spool_path is None:
            return
        payload = {
            "version": 2,
            "saved_at": time.time(),
            "actions": [
                self._spool_payload(action) for action in self._actions
            ],
        }
        self._spool_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._spool_path.with_name(
            f"{self._spool_path.name}.tmp"
        )
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self._spool_path)

    def restore_from_spool(self, state_manager) -> int:
        """Restore unresolved MT5 actions after state was rebuilt from MT5."""
        if self._spool_path is None or not self._spool_path.is_file():
            return 0
        try:
            payload = json.loads(self._spool_path.read_text(encoding="utf-8"))
            rows = payload.get("actions") or []
        except (OSError, TypeError, ValueError) as exc:
            print(f"[Pending] spool ilegible, se conserva para revision: {exc}")
            return 0

        restored = 0
        skipped = 0
        for row in rows:
            try:
                channel = str(row["channel"])
                message_id = int(row["message_id"])
                signal = state_manager.get(channel, message_id)
                if signal is None:
                    signal = Signal(
                        channel=channel,
                        message_id=message_id,
                        direction=str(row["direction"]),
                    )
                legacy_lineage = not (
                    row.get("action_id") and row.get("decision_id")
                )
                action = PendingAction(
                    kind=str(row["kind"]),
                    ticket=int(row["ticket"]),
                    signal=signal,
                    new_sl=row.get("new_sl"),
                    new_tp=row.get("new_tp"),
                    created_at=time.time(),
                    timeout_s=float(row.get("timeout_s") or DEFAULT_TIMEOUT_S),
                    applied_tp=row.get("applied_tp"),
                    label=str(row.get("label") or "restored pending action"),
                    persist_until_signal_close=bool(
                        row.get("persist_until_signal_close", False)
                    ),
                    revision=int(row.get("revision") or 0),
                    action_id=str(
                        row.get("action_id")
                        or causal_trace.new_action_id()
                    ),
                    decision_id=str(
                        row.get("decision_id")
                        or causal_trace.new_decision_id()
                    ),
                    message_revision_id=row.get("message_revision_id"),
                    last_attempt_id=row.get("last_attempt_id"),
                    parent_decision_id=row.get("parent_decision_id"),
                    decision_origin=row.get("decision_origin"),
                    internal_reason=str(
                        row.get("internal_reason") or ""
                    ),
                    lineage_recovered_from_legacy_spool=bool(
                        row.get(
                            "lineage_recovered_from_legacy_spool",
                            legacy_lineage,
                        )
                    ),
                )
            except (KeyError, TypeError, ValueError):
                skipped += 1
                continue
            self._actions.append(action)
            restored += 1
            try:
                import journal
                journal.event(
                    f"{channel}_{message_id}",
                    "mt5_pending_action_restored",
                    kind=action.kind,
                    ticket=action.ticket,
                    new_sl=action.new_sl,
                    new_tp=action.new_tp,
                    label=action.label,
                    expected_magic=action.signal.magic,
                    lineage_recovered_from_legacy_spool=(
                        action.lineage_recovered_from_legacy_spool
                    ),
                    **_lineage_fields(action),
                )
            except Exception:
                pass

        self._persist_spool()
        if restored:
            print(
                f"[Pending] Recuperadas {restored} acciones MT5 pendientes "
                f"tras reinicio (omitidas={skipped})."
            )
            self._ensure_runner()
        return restored

    def add(self, action: PendingAction):
        if causal_trace.current_decision_id() == action.decision_id:
            causal_trace.register_action_id(action.action_id)
        owns_internal_decision = (
            action.decision_origin == "internal"
            and causal_trace.current_decision_id() != action.decision_id
        )
        if owns_internal_decision:
            self._log_internal_decision_started(action)
        for existing in self._actions:
            same_signal = (
                existing.signal.channel == action.signal.channel
                and existing.signal.message_id == action.signal.message_id
            )
            if not (
                same_signal
                and existing.kind == action.kind
                and existing.ticket == action.ticket
            ):
                continue
            if action.kind == "MODIFY_SLTP":
                previous_action_id = existing.action_id
                changed = False
                if action.new_sl is not None and action.new_sl != existing.new_sl:
                    existing.new_sl = action.new_sl
                    changed = True
                if action.new_tp is not None and action.new_tp != existing.new_tp:
                    existing.new_tp = action.new_tp
                    existing.applied_tp = None
                    changed = True
                if changed:
                    existing.created_at = action.created_at
                    existing.last_retcode = None
                    existing.waiting_reason = None
                    existing.retry_not_before = 0.0
                    existing.stops_alerted = False
                    existing.last_preflight_status = None
                    existing.last_preflight_reason = None
                    existing.last_preflight_effective_sl = None
                    existing.last_preflight_effective_tp = None
                    existing.last_preflight_deferred_sl = None
                    existing.last_preflight_observed_ticket = None
                    existing.last_preflight_observed_magic = None
                    existing.last_preflight_observed_kind = None
                next_label = action.label or existing.label
                label_changed = next_label != existing.label
                existing.label = next_label
                persistence_changed = (
                    action.persist_until_signal_close
                    and not existing.persist_until_signal_close
                )
                existing.persist_until_signal_close = (
                    existing.persist_until_signal_close
                    or action.persist_until_signal_close)
                if changed or label_changed or persistence_changed:
                    existing.revision += 1
                action.revision = existing.revision
                self._log_request(action)
                if changed or label_changed or persistence_changed:
                    existing.attempts = 0
                    existing.last_retcode = None
                    existing.waiting_reason = None
                    existing.retry_not_before = 0.0
                    existing.stops_alerted = False
                    existing.last_preflight_status = None
                    existing.last_preflight_reason = None
                    existing.last_preflight_effective_sl = None
                    existing.last_preflight_effective_tp = None
                    existing.last_preflight_deferred_sl = None
                    existing.last_preflight_observed_ticket = None
                    existing.last_preflight_observed_magic = None
                    existing.last_preflight_observed_kind = None
                    existing.action_id = action.action_id
                    existing.decision_id = action.decision_id
                    existing.message_revision_id = action.message_revision_id
                    existing.parent_decision_id = (
                        action.parent_decision_id
                    )
                    existing.decision_origin = action.decision_origin
                    existing.internal_reason = action.internal_reason
                    existing.last_attempt_id = None
                    self._log_coalesced(
                        existing,
                        changed=changed,
                        label_changed=label_changed,
                        persistence_changed=persistence_changed,
                        supersedes_action_id=previous_action_id,
                    )
                else:
                    self._log_coalesced(
                        action,
                        changed=False,
                        coalesced_into_action_id=previous_action_id,
                    )
                self._persist_spool()
                self._ensure_runner()
                if owns_internal_decision:
                    self._log_internal_decision(action)
                return
        self._actions.append(action)
        print(f"[Pending] Encolado: {action.label} (ticket={action.ticket})")
        self._log_request(action)
        if owns_internal_decision:
            self._log_internal_decision(action)
        self._persist_spool()
        self._ensure_runner()

    def _log_internal_decision_started(
        self,
        action: PendingAction,
    ) -> None:
        try:
            import journal
            sig_id = f"{action.signal.channel}_{action.signal.message_id}"
            journal.event(
                sig_id,
                "bot_internal_decision_started",
                decision_id=action.decision_id,
                message_revision_id=action.message_revision_id,
                parent_decision_id=action.parent_decision_id,
                decision_reason=action.internal_reason,
            )
        except Exception:
            pass

    def _log_internal_decision(self, action: PendingAction) -> None:
        try:
            import journal
            sig_id = f"{action.signal.channel}_{action.signal.message_id}"
            journal.event(
                sig_id,
                "bot_internal_decision",
                decision_id=action.decision_id,
                message_revision_id=action.message_revision_id,
                parent_decision_id=action.parent_decision_id,
                decision_reason=action.internal_reason,
                declared_action_ids=[action.action_id],
                declared_action_count=1,
            )
        except Exception:
            pass

    def _log_coalesced(
        self,
        action: PendingAction,
        *,
        changed: bool,
        label_changed: bool = False,
        persistence_changed: bool = False,
        coalesced_into_action_id: Optional[str] = None,
        supersedes_action_id: Optional[str] = None,
    ) -> None:
        try:
            import journal
            sig_id = f"{action.signal.channel}_{action.signal.message_id}"
            journal.event(
                sig_id,
                "mt5_action_coalesced",
                kind=action.kind,
                ticket=action.ticket,
                new_sl=action.new_sl,
                new_tp=action.new_tp,
                payload_changed=changed,
                label_changed=label_changed,
                persistence_changed=persistence_changed,
                queue_slots=1,
                expected_magic=action.signal.magic,
                coalesced_into_action_id=coalesced_into_action_id,
                supersedes_action_id=supersedes_action_id,
                **_lineage_fields(action),
            )
        except Exception:
            pass

    def _ensure_runner(self):
        if self._task is None or self._task.done():
            import journal
            with (
                causal_trace.detached_context(),
                journal.detached_test_mode(),
            ):
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
            spool_before = self._spool_fingerprint()
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
                                      exc_type=type(e).__name__,
                                      **_lineage_fields(act))
                    except Exception:
                        pass
                    # Marca RETRY para que la cola siga
                    result = "RETRY"

                age = time.time() - act.created_at
                if (
                    result == "WAIT_PRECONDITION"
                    and _should_alert_waiting_exact_be(
                        act,
                        age,
                        EXACT_BE_WAIT_ALERT_THRESHOLD_S,
                    )
                ):
                    self._record_stuck_stops(act)
                    act.stops_alerted = True

                # Batch E: stuck transient warning. Si el retcode es TRANSIENT
                # y la accion lleva >5min en cola, algo estructural bloquea
                # (autotrading desactivado permanentemente, broker disconnected
                # >5min, etc). Warning una vez por accion para no spammear.
                if result == "RETRY" and act.last_retcode is not None:
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
                                          attempts=act.attempts,
                                          **_lineage_fields(act))
                        except Exception:
                            pass
                        act._stuck_warned = True

                    if _should_alert_stuck_stops(
                            act.last_retcode,
                            age,
                            STOPS_STUCK_THRESHOLD_S,
                            act.stops_alerted):
                        self._record_stuck_stops(act)
                        act.stops_alerted = True

                if result == "DONE":
                    print(f"[Pending] ✓ Resuelto tras {act.attempts} intentos: {act.label}")
                    # SL movido y CONFIRMADO en MT5 → registrar el SL real de
                    # este ticket en la senal. Sin esto, signal.sl (el SL del
                    # proveedor) queda obsoleto y _classify_closures etiqueta
                    # el cierre como "MANUAL" en vez de "SL". Solo en exito
                    # real (retcode OK): si fue POSITION_GONE el modify no se
                    # aplico (la posicion ya estaba cerrada).
                    _record_confirmed_levels(act)
                    cls_done = mt5_errors.classify(act.last_retcode)
                    if (act.kind != "MODIFY_SLTP"
                            or cls_done in ("OK", "POSITION_GONE")):
                        self._log_done(act)
                elif result == "DROP":
                    print(f"[Pending] Descartado (error permanente {act.last_retcode}): {act.label}")
                    self._log_failure(act, reason=f"permanent_error_retcode_{act.last_retcode}")
                else:
                    still_pending.append(act)

            self._actions = still_pending
            if self._spool_fingerprint() != spool_before:
                self._persist_spool()
            await asyncio.sleep(0)

    @staticmethod
    def _structural_incident_key(act: PendingAction) -> tuple:
        return (
            act.signal.channel,
            act.signal.message_id,
            act.signal.direction,
            act.kind,
            act.last_retcode,
            act.last_preflight_status,
            act.last_preflight_reason,
        )

    @staticmethod
    def _format_level_range(values: list[float]) -> str:
        numeric = sorted({float(value) for value in values if value is not None})
        if not numeric:
            return "sin cambio"
        if len(numeric) == 1:
            return str(numeric[0])
        return f"{numeric[0]} a {numeric[-1]}"

    @staticmethod
    def _format_structural_notification(actions: list[PendingAction]) -> str:
        first = actions[0]
        channel = provider_display_name(first.signal.channel)
        tickets = ", ".join(str(action.ticket) for action in actions)
        count = len(actions)
        position_label = "posicion" if count == 1 else "posiciones"
        attempts = max(action.attempts for action in actions)
        attempt_label = "intento" if attempts == 1 else "intentos"
        sl_text = PendingQueue._format_level_range([
            action.new_sl for action in actions
        ])
        tp_text = PendingQueue._format_level_range([
            action.new_tp if action.new_tp is not None else action.applied_tp
            for action in actions
        ])
        if first.last_preflight_status == "wait_market":
            return (
                "BE AUN NO APLICADO\n"
                f"{channel} - {first.signal.direction}\n"
                f"BE exacto {sl_text}\n"
                f"{count} {position_label}: {tickets}\n\n"
                "El precio actual no permite colocar exactamente ese SL.\n"
                "Bot: sigue reintentando hasta aplicarlo o cerrar la senal.\n"
                "Accion: revisa MT5 si necesitas intervenir ahora."
            )
        return (
            "🚨 PROTECCIÓN PENDIENTE\n"
            f"{channel} · {first.signal.direction}\n"
            f"SL {sl_text} · TP {tp_text}\n"
            f"{count} {position_label}: {tickets}\n\n"
            f"MT5 la rechazó tras {attempts} {attempt_label} MT5 por posicion "
            f"(código {first.last_retcode}).\n"
            "Bot: continua reintentando con el precio actualizado.\n"
            "Acción: revisa el SL en MT5 si sigue sin aplicarse."
        )

    def _record_stuck_stops(self, act: PendingAction) -> None:
        key = self._structural_incident_key(act)
        self._structural_incidents.setdefault(key, []).append(act)
        task = self._structural_flush_tasks.get(key)
        if task is None or task.done():
            self._structural_flush_tasks[key] = asyncio.create_task(
                self._flush_structural_incident(key)
            )

    async def _flush_structural_incident(self, key: tuple) -> None:
        await asyncio.sleep(0.25)
        actions = self._structural_incidents.pop(key, [])
        self._structural_flush_tasks.pop(key, None)
        if not actions:
            return
        first = actions[0]
        sig_id = f"{first.signal.channel}_{first.signal.message_id}"
        try:
            import journal
            journal.event(
                sig_id,
                "mt5_structural_incident",
                kind=first.kind,
                tickets=[action.ticket for action in actions],
                ticket_count=len(actions),
                new_sl_values=sorted({
                    action.new_sl for action in actions
                    if action.new_sl is not None
                }),
                new_tp_values=sorted({
                    action.new_tp if action.new_tp is not None else action.applied_tp
                    for action in actions
                    if action.new_tp is not None or action.applied_tp is not None
                }),
                retcode=first.last_retcode,
                preflight_status=first.last_preflight_status,
                preflight_reason=first.last_preflight_reason,
                mt5_attempts=sum(action.attempts for action in actions),
                action_ids=[action.action_id for action in actions],
                **_lineage_fields(first),
            )
        except Exception:
            pass
        try:
            from listener import notify
            await notify(self._format_structural_notification(actions))
        except Exception as exc:
            print(f"[Pending] structural incident notify error: {exc}")

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
                          new_tp=_effective_action_tp(act),
                          expected_magic=act.signal.magic,
                          **_preflight_fields(act),
                          attempt_id=act.last_attempt_id,
                          age_seconds=round(time.time() - act.created_at, 1),
                          **_lineage_fields(act))
            # POSITION_CLOSED es benigno. Timeouts y errores permanentes
            # quedan como warning; los stops atascados se notifican por
            # separado mientras la cola sigue reintentando.
            sev = "info" if act.last_retcode == 10036 else "warning"
            journal.anomaly(sig_id, "mt5", sev,
                            f"{act.kind} falló: {reason}",
                            ticket=act.ticket, retcode=act.last_retcode,
                            attempts=act.attempts, label=act.label,
                            age_seconds=round(
                                time.time() - act.created_at, 1),
                            **_lineage_fields(act))
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
                              new_tp=act.new_tp, label=act.label,
                              expected_magic=act.signal.magic,
                              **_lineage_fields(act))
            elif act.kind == "CLOSE_POSITION":
                journal.event(sig_id, "mt5_close_requested",
                              ticket=act.ticket, label=act.label,
                              expected_magic=act.signal.magic,
                              **_lineage_fields(act))
            elif act.kind == "CANCEL_PENDING":
                journal.event(sig_id, "mt5_cancel_requested",
                              ticket=act.ticket, label=act.label,
                              expected_magic=act.signal.magic,
                              **_lineage_fields(act))
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
            "attempt_id": act.last_attempt_id,
            **_lineage_fields(act),
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

    def _remember_confirmed_modify(self, sig_id: str, act: PendingAction,
                                   position_snapshot: dict) -> None:
        if (act.kind != "MODIFY_SLTP"
                or mt5_errors.classify(act.last_retcode) != "OK"
                or position_snapshot.get("position_exists") is not True):
            return

        actual_sl = position_snapshot.get("sl")
        actual_tp = position_snapshot.get("tp")
        if actual_sl is not None:
            act.signal.sl_by_ticket[act.ticket] = actual_sl
        if actual_tp is not None:
            act.signal.tp_by_ticket[act.ticket] = actual_tp

        self._recent_confirmed_actions = [
            item for item in self._recent_confirmed_actions
            if not (
                item["sig_id"] == sig_id
                and item["kind"] == act.kind
                and item["ticket"] == act.ticket
            )
        ]
        self._recent_confirmed_actions.append({
            "sig_id": sig_id,
            "kind": act.kind,
            "ticket": act.ticket,
            "new_sl": actual_sl,
            "new_tp": actual_tp,
            "confirmed_at": time.time(),
            "attempts": act.attempts,
            "last_retcode": act.last_retcode,
            "state": "confirmed_recent",
            "waiting_reason": None,
            "applied_tp": actual_tp,
            "label": act.label,
            "attempt_id": act.last_attempt_id,
            **_lineage_fields(act),
        })
        if len(self._recent_confirmed_actions) > 500:
            self._recent_confirmed_actions = (
                self._recent_confirmed_actions[-500:])

    def _log_position_snapshot(self, sig_id: str, act: PendingAction, journal):
        if act.kind not in ("MODIFY_SLTP", "CLOSE_POSITION"):
            return None
        position_snapshot = self._position_snapshot(act)
        self._remember_confirmed_modify(sig_id, act, position_snapshot)
        journal.event(
            sig_id, "mt5_position_snapshot", **position_snapshot)
        return position_snapshot

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
                "attempt_id": act.last_attempt_id,
                "expected_magic": act.signal.magic,
                **_preflight_fields(act),
                **_lineage_fields(act),
            }
            if act.kind == "MODIFY_SLTP":
                effective_tp = _effective_action_tp(act)
                if mt5_errors.classify(act.last_retcode) == "POSITION_GONE":
                    journal.event(sig_id, "mt5_modify_skipped_position_gone",
                                  new_sl=act.new_sl, new_tp=effective_tp,
                                  **payload)
                    return
                journal.event(sig_id, "mt5_modify_confirmed",
                              new_sl=act.new_sl, new_tp=effective_tp,
                              **payload)
                self._log_position_snapshot(sig_id, act, journal)
            elif act.kind == "CLOSE_POSITION":
                journal.event(sig_id, "mt5_close_result", **payload)
                self._log_position_snapshot(sig_id, act, journal)
            elif act.kind == "CANCEL_PENDING":
                journal.event(sig_id, "mt5_cancel_result", **payload)
        except Exception:
            pass

    def _log_waiting_precondition(self, act: PendingAction, decision) -> None:
        if act.waiting_reason == decision.reason:
            return
        act.waiting_reason = decision.reason
        try:
            import journal
            sig_id = f"{act.signal.channel}_{act.signal.message_id}"
            journal.event(
                sig_id,
                "mt5_modify_waiting_precondition",
                ticket=act.ticket,
                requested_sl=act.new_sl,
                requested_tp=act.new_tp,
                effective_sl=decision.effective_sl,
                effective_tp=decision.effective_tp,
                reason=decision.reason,
                mt5_attempts=act.attempts,
                **_lineage_fields(act),
            )
        except Exception:
            pass

    def _log_precondition_satisfied(self, act: PendingAction) -> None:
        if act.waiting_reason is None:
            return
        previous_reason = act.waiting_reason
        act.waiting_reason = None
        try:
            import journal
            sig_id = f"{act.signal.channel}_{act.signal.message_id}"
            journal.event(
                sig_id,
                "mt5_modify_precondition_satisfied",
                ticket=act.ticket,
                previous_reason=previous_reason,
                requested_sl=act.new_sl,
                requested_tp=act.new_tp,
                mt5_attempts=act.attempts,
                **_lineage_fields(act),
            )
        except Exception:
            pass

    def _log_partial_modify(self, act: PendingAction, applied_tp: float) -> None:
        partial = replace(
            act,
            new_sl=None,
            new_tp=applied_tp,
            label=f"{act.label} (TP aplicado; SL en espera)",
        )
        self._log_done(partial)

    def _finish_superseded_attempt(
        self,
        current: PendingAction,
        completed: PendingAction,
    ) -> str:
        """Record the immutable MT5 attempt and retain the newer payload."""
        cls = mt5_errors.classify(completed.last_retcode)
        if cls in ("OK", "POSITION_GONE"):
            _record_confirmed_levels(completed)
            self._log_done(completed)
        else:
            try:
                import journal
                sig_id = (
                    f"{completed.signal.channel}_"
                    f"{completed.signal.message_id}"
                )
                journal.event(
                    sig_id,
                    "mt5_modify_attempt_superseded",
                    ticket=completed.ticket,
                    attempted_sl=completed.new_sl,
                    attempted_tp=completed.new_tp,
                    attempted_label=completed.label,
                    attempted_revision=completed.revision,
                    current_revision=current.revision,
                    retcode=completed.last_retcode,
                    attempt_id=completed.last_attempt_id,
                    **_lineage_fields(completed),
                )
            except Exception:
                pass
        current.last_retcode = None
        current.retry_not_before = 0.0
        return "RETRY"

    async def _try_once(self, act: PendingAction) -> str:
        """Ejecuta el intento. Devuelve 'DONE', 'RETRY' o 'DROP'.

        Todas las acciones verifican el magic del canal (act.signal.magic):
        si el ticket pertenece a otro canal o a una operación manual, el
        executor devuelve INVALID y la acción se descarta sin tocar nada."""
        loop = asyncio.get_event_loop()
        expected_magic = act.signal.magic
        # Never read the mutable queue payload again during this attempt.
        # add() may coalesce a newer SL/TP while MT5 is still responding.
        attempt = replace(act)
        attempt_revision = act.revision

        if act.kind == "MODIFY_SLTP":
            if act.retry_not_before > time.time():
                return "WAIT_RETRY_COOLDOWN"
            decision = await loop.run_in_executor(
                None,
                lambda: executor.preflight_modify_sltp(
                    attempt.ticket,
                    attempt.new_sl,
                    attempt.new_tp,
                    expected_magic=expected_magic,
                ),
            )
            _remember_preflight(act, decision)
            _remember_preflight(attempt, decision)
            if act.revision != attempt_revision:
                act.last_retcode = None
                return "RETRY"
            if decision.status == "mt5_unavailable":
                act.last_retcode = mt5.TRADE_RETCODE_MARKET_CLOSED
                return "RETRY"
            if decision.status == "position_gone":
                act.last_retcode = 10036
                return "DONE"
            if decision.status in {"invalid_magic", "invalid_request"}:
                act.last_retcode = mt5.TRADE_RETCODE_INVALID
                return "DROP"
            if decision.status == "wait_market":
                self._log_waiting_precondition(act, decision)
                return "WAIT_PRECONDITION"
            if decision.status == "apply_tp_defer_sl":
                self._log_waiting_precondition(act, decision)
                tp_to_apply = attempt.new_tp
                if tp_to_apply is None:
                    return "WAIT_PRECONDITION"
                act.attempts += 1
                attempt_trace = _new_attempt_trace(attempt)
                act.last_attempt_id = attempt.last_attempt_id
                retcode = await loop.run_in_executor(
                    None,
                    lambda: executor.modify_sltp_rc(
                        attempt.ticket,
                        None,
                        tp_to_apply,
                        expected_magic=expected_magic,
                        trace=attempt_trace,
                    ),
                )
                act.last_retcode = retcode
                completed = replace(
                    attempt,
                    new_sl=None,
                    new_tp=tp_to_apply,
                    attempts=act.attempts,
                    last_retcode=retcode,
                )
                if act.revision != attempt_revision:
                    return self._finish_superseded_attempt(act, completed)
                if mt5_errors.classify(retcode) == "OK":
                    act.applied_tp = tp_to_apply
                    act.new_tp = None
                    _record_confirmed_levels(completed)
                    self._log_partial_modify(act, tp_to_apply)
                    return "WAIT_PRECONDITION"
            else:
                self._log_precondition_satisfied(act)
                act.attempts += 1
                attempt_trace = _new_attempt_trace(attempt)
                act.last_attempt_id = attempt.last_attempt_id
                retcode = await loop.run_in_executor(
                    None, lambda: executor.modify_sltp_rc(
                        attempt.ticket, attempt.new_sl, attempt.new_tp,
                        expected_magic=expected_magic,
                        trace=attempt_trace,
                    )
                )
                completed = replace(
                    attempt,
                    attempts=act.attempts,
                    last_retcode=retcode,
                )
                if act.revision != attempt_revision:
                    return self._finish_superseded_attempt(act, completed)
        elif act.kind == "CLOSE_POSITION":
            act.attempts += 1
            attempt_trace = _new_attempt_trace(attempt)
            act.last_attempt_id = attempt.last_attempt_id
            retcode = await loop.run_in_executor(
                None, lambda: executor.close_position_rc(
                    attempt.ticket,
                    expected_magic=expected_magic,
                    trace=attempt_trace,
                )
            )
        elif act.kind == "CANCEL_PENDING":
            act.attempts += 1
            attempt_trace = _new_attempt_trace(attempt)
            act.last_attempt_id = attempt.last_attempt_id
            retcode = await loop.run_in_executor(
                None, lambda: executor.cancel_pending_rc(
                    attempt.ticket,
                    expected_magic=expected_magic,
                    trace=attempt_trace,
                )
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
            if retcode == 10029:
                act.retry_not_before = time.time() + BROKER_RETRY_COOLDOWN_S
            return "RETRY"
        if cls == "STOPS":
            # The preflight is recalculated on every retry. A broker-side
            # race can still reject a level that was valid milliseconds ago,
            # so wait briefly and evaluate the current market again.
            act.retry_not_before = time.time() + BROKER_RETRY_COOLDOWN_S
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


queue = PendingQueue(spool_path=PENDING_SPOOL_FILE)


def snapshot(queue_obj: PendingQueue | None = None,
             now: float | None = None) -> list[dict]:
    """Snapshot of active actions plus short-lived confirmed evidence."""
    q = queue_obj or queue
    ts = time.time() if now is None else now
    out = []
    for act in list(q._actions):
        sig_id = f"{act.signal.channel}_{act.signal.message_id}"
        out.append({
            "sig_id": sig_id,
            "kind": act.kind,
            "ticket": act.ticket,
            **_lineage_fields(act),
            "new_sl": act.new_sl,
            "new_tp": act.new_tp,
            "age_s": round(ts - act.created_at, 1),
            "attempts": act.attempts,
            "last_retcode": act.last_retcode,
            "state": "waiting_market" if act.waiting_reason else "retrying",
            "waiting_reason": act.waiting_reason,
            "applied_tp": act.applied_tp,
            "label": act.label,
        })

    recent_confirmed = []
    for item in list(q._recent_confirmed_actions):
        age_s = max(0.0, ts - float(item["confirmed_at"]))
        if age_s > CONFIRMED_ACTION_EVIDENCE_TTL_S:
            continue
        recent_confirmed.append(item)
        out.append({
            "sig_id": item["sig_id"],
            "kind": item["kind"],
            "ticket": item["ticket"],
            "action_id": item["action_id"],
            "decision_id": item["decision_id"],
            "message_revision_id": item["message_revision_id"],
            "action_revision": item["action_revision"],
            "new_sl": item["new_sl"],
            "new_tp": item["new_tp"],
            "age_s": round(age_s, 1),
            "attempts": item["attempts"],
            "last_retcode": item["last_retcode"],
            "state": "confirmed_recent",
            "waiting_reason": None,
            "applied_tp": item["applied_tp"],
            "label": item["label"],
        })
    q._recent_confirmed_actions = recent_confirmed
    return out


# ─── Helpers de alto nivel ─────────────────────────────────────────────────────

def enqueue_modify_sl(
    signal: Signal,
    ticket: int,
    new_sl: float,
    label: str = "",
    *,
    persist_until_signal_close: bool = False,
):
    queue.add(PendingAction(
        kind="MODIFY_SLTP",
        ticket=ticket,
        signal=signal,
        new_sl=new_sl,
        persist_until_signal_close=persist_until_signal_close,
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
