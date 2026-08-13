"""
journal.py — Sistema de registro de operaciones del bot.

Dos artefactos complementarios (ambos append-only, thread-safe):

  • data/trade_events.jsonl
        Una línea por evento atómico. Sirve para forensics: filtrar por
        signal_id reconstruye el timeline completo de cualquier operación.

  • data/trade_journal.csv
        Una fila por señal cuando se cierra. Sirve para análisis macro
        en pandas/Excel: winrate, distribución de tags, PnL por canal, etc.

Auto-etiquetado: cada trade cerrado recibe un tag semántico (WIN_CLEAN,
LOSS_REVERSAL, TIMESTOP, etc.) que permite filtrar y comparar grupos de
operaciones sin tener que leer el JSONL.

Uso típico desde el listener:

    journal.event(sig_id, "signal_received", raw="XAU USD SELL NOW")
    journal.begin_trade(sig_id, channel="canal2", direction="SELL", ...)
    journal.event(sig_id, "market_filled", ticket=12345, price=4692.05)
    journal.update_trade(sig_id, market_filled_utc="...", market_entry=4692.05)
    journal.append_mgmt(sig_id, classified="MOVE_SL_TO_PRICE", applied=True)
    journal.update_extremes(sig_id, current_pl=-22.5, ts="...")
    journal.event(sig_id, "ticket_closed", ticket=12345, reason="SL", pl=-22.5)
    journal.finalize_trade(sig_id, closed_by="SL", total_pnl_usd=-22.5, ...)
"""

import atexit
import asyncio
import contextvars
import csv
import hashlib
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue
from threading import Event as ThreadEvent, Lock, Thread
from typing import Optional

import causal_trace
from provider_names import provider_display_name
import runtime_paths

# ─── Paths ──────────────────────────────────────────────────────────────────
DATA_DIR = runtime_paths.active_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)

EVENTS_FILE = DATA_DIR / "trade_events.jsonl"
JOURNAL_FILE = DATA_DIR / "trade_journal.csv"

# Ficheros separados para señales del canal de pruebas. Mismo formato pero
# aislados de los reales para no contaminar análisis de performance.
EVENTS_TEST_FILE = DATA_DIR / "trade_events_TEST.jsonl"
JOURNAL_TEST_FILE = DATA_DIR / "trade_journal_TEST.csv"

# ─── Test mode aislamiento ──────────────────────────────────────────────────
# ContextVar: el listener envuelve los handlers del canal de pruebas con
# `_test_context.set(True)`. Cualquier llamada a journal/logger/strategies
# dentro de esa pila async ve is_test=True. Las tareas globales se separan de
# esa contextvar; los eventos posteriores de la misma señal siguen aislados
# mediante el registro persistente por signal_id definido abajo.
_test_context: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "journal_is_test", default=False
)
# Registry persistente por signal_id: una vez una señal está marcada como
# test (cuando el primer event/begin_trade llega bajo contextvar=True), el
# flag se guarda aquí para que llamadas posteriores (que pueden venir desde
# contextos sin la contextvar) sigan ruteando al fichero TEST.
_test_signals: set[str] = set()
_test_signals_lock = Lock()


def is_test_mode() -> bool:
    """True si el código actual está corriendo bajo contextvar de test."""
    return _test_context.get()


@contextmanager
def detached_test_mode():
    """Create background work without inheriting a test-handler flag."""
    token = _test_context.set(False)
    try:
        yield
    finally:
        _test_context.reset(token)


def is_test_signal(signal_id: str) -> bool:
    """True si esta señal específica fue marcada como test."""
    with _test_signals_lock:
        return signal_id in _test_signals


def _mark_and_get_test(signal_id: str) -> bool:
    """Lazy-mark + lookup: si la signal ya está en el registry es test;
    si no, mira la contextvar — y si está activa, lo añade al registry y
    devuelve True. Idempotente, thread-safe.
    """
    with _test_signals_lock:
        if signal_id in _test_signals:
            return True
        if _test_context.get():
            _test_signals.add(signal_id)
            return True
        return False


def _forget_test_signal(signal_id: str):
    """Llamado en finalize_trade para no acumular memoria infinitamente."""
    with _test_signals_lock:
        _test_signals.discard(signal_id)

# Schema CSV — orden fijo de columnas
CSV_FIELDS = [
    "signal_id", "channel", "direction",
    "signal_received_utc", "market_filled_utc", "fill_latency_ms",
    "range_arrived_utc", "range_delay_sec",
    "tps_arrived_utc",
    "range_low", "range_high",
    "market_entry_price",
    "tps_initial", "sl_initial", "sl_final",
    "range_decision", "adverse_action",
    "n_tickets_opened", "n_dca_filled", "total_lot",
    "mfe_usd", "mae_usd", "mfe_at", "mae_at",
    "n_mgmt_msgs", "mgmt_msgs_classified", "mgmt_msgs_applied",
    "closed_at_utc", "closed_by", "duration_sec",
    "total_pnl_usd", "tag",
    "notes",
]

# ─── Estado interno ─────────────────────────────────────────────────────────
_file_lock = Lock()         # protege escrituras a disco
_trades_lock = Lock()       # protege el dict en memoria
_trades: dict[str, dict] = {}   # signal_id -> dict acumulador
_notify_loop: Optional[asyncio.AbstractEventLoop] = None
_critical_notify_lock = Lock()
_critical_notify_seen: dict[str, float] = {}
_critical_notify_inflight: set[str] = set()


def _critical_notify_cooldown_s() -> float:
    try:
        return max(0.0, float(os.getenv("CRITICAL_NOTIFY_COOLDOWN_S", "600")))
    except ValueError:
        return 600.0


def _critical_notify_fingerprint(
    signal_id: str,
    category: str,
    detail: str,
    ctx: dict | None = None,
) -> str:
    identity_fields = (
        "ticket",
        "tickets",
        "retcode",
        "last_retcode",
        "preflight_status",
        "preflight_reason",
        "reason",
        "action",
        "kind",
        "exception_type",
        "direction",
    )
    identity = {
        key: ctx[key]
        for key in identity_fields
        if ctx is not None and key in ctx
    }
    payload = json.dumps(
        {
            "signal_id": str(signal_id),
            "category": str(category),
            "detail": str(detail),
            "identity": identity,
        },
        default=str,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _critical_notify_claim(
    signal_id: str,
    category: str,
    detail: str,
    *,
    ctx: dict | None = None,
    now: float | None = None,
) -> str | None:
    observed_at = time.monotonic() if now is None else float(now)
    cooldown = _critical_notify_cooldown_s()
    fingerprint = _critical_notify_fingerprint(
        signal_id,
        category,
        detail,
        ctx,
    )
    with _critical_notify_lock:
        previous = _critical_notify_seen.get(fingerprint)
        if (
            fingerprint in _critical_notify_inflight
            or previous is not None and observed_at - previous < cooldown
        ):
            return None
        _critical_notify_inflight.add(fingerprint)
    return fingerprint


def _critical_notify_complete(
    fingerprint: str,
    delivered: bool,
    *,
    now: float | None = None,
) -> None:
    observed_at = time.monotonic() if now is None else float(now)
    cooldown = _critical_notify_cooldown_s()
    with _critical_notify_lock:
        _critical_notify_inflight.discard(fingerprint)
        if not delivered:
            return
        _critical_notify_seen[fingerprint] = observed_at
        if len(_critical_notify_seen) > 2000:
            cutoff = observed_at - max(cooldown, 1.0)
            for key, timestamp in list(_critical_notify_seen.items()):
                if timestamp < cutoff:
                    del _critical_notify_seen[key]


def _reset_critical_notify_rate_limit() -> None:
    with _critical_notify_lock:
        _critical_notify_seen.clear()
        _critical_notify_inflight.clear()
_event_queue: Queue = Queue()
_event_writer_guard = Lock()
_event_writer_thread: Optional[Thread] = None
_event_failure_guard = Lock()
_event_write_failures = 0
_event_acknowledged_failures = 0
PROCESS_SESSION_ID = causal_trace.new_session_id()


# ─── Helpers ────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _serialize(value):
    """Convierte tipos no JSON-friendly (datetime, set, etc.) a string."""
    if isinstance(value, datetime):
        return value.isoformat(timespec="milliseconds")
    if isinstance(value, (set, frozenset)):
        return sorted(
            value,
            key=lambda item: json.dumps(
                item,
                default=_serialize,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
    return value


def _payload_sha256(payload: dict) -> str:
    canonical = json.dumps(
        payload,
        default=_serialize,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


# ─── API: eventos atómicos (JSONL) ──────────────────────────────────────────

class _FlushBarrier:
    def __init__(self) -> None:
        self.ready = ThreadEvent()
        self.failure_count: Optional[int] = None


class _EventReceipt:
    def __init__(self) -> None:
        self.ready = ThreadEvent()
        self.succeeded = False
        self.error: Optional[str] = None


def _record_event_write_failure() -> None:
    global _event_write_failures
    with _event_failure_guard:
        _event_write_failures += 1


def _event_failure_count() -> int:
    with _event_failure_guard:
        return _event_write_failures


def _event_writer_loop() -> None:
    while True:
        (
            target_file,
            line,
            ev,
            signal_id,
            barrier,
            receipt,
        ) = _event_queue.get()
        try:
            if target_file is not None:
                with _file_lock:
                    with open(target_file, "a", encoding="utf-8") as handle:
                        handle.write(line)
                if receipt is not None:
                    receipt.succeeded = True
        except Exception as exc:
            _record_event_write_failure()
            if receipt is not None:
                receipt.error = f"{type(exc).__name__}: {exc}"
            print(
                f"[journal] ERROR escribiendo evento {ev} "
                f"para {signal_id}: {exc}"
            )
        finally:
            if receipt is not None:
                receipt.ready.set()
            if barrier is not None:
                barrier.failure_count = _event_failure_count()
                barrier.ready.set()
            _event_queue.task_done()


def _ensure_event_writer() -> None:
    global _event_writer_thread
    with _event_writer_guard:
        if (
            _event_writer_thread is not None
            and _event_writer_thread.is_alive()
        ):
            return
        _event_writer_thread = Thread(
            target=_event_writer_loop,
            name="journal-event-writer",
            daemon=True,
        )
        _event_writer_thread.start()


def flush_events(timeout: float = 10.0) -> bool:
    """Wait until every event queued before this call is durable on disk."""
    global _event_acknowledged_failures
    with _event_failure_guard:
        acknowledged_before = _event_acknowledged_failures
        failures_before = _event_write_failures
    if (
        _event_writer_thread is None
        and _event_queue.empty()
        and failures_before == acknowledged_before
    ):
        return True
    _ensure_event_writer()
    barrier = _FlushBarrier()
    _event_queue.put((None, None, None, None, barrier, None))
    if not barrier.ready.wait(timeout=max(0.0, float(timeout))):
        return False
    observed_failures = int(barrier.failure_count or 0)
    with _event_failure_guard:
        _event_acknowledged_failures = max(
            _event_acknowledged_failures,
            observed_failures,
        )
    return observed_failures == acknowledged_before


def confirm_event(receipt, timeout: float = 10.0) -> bool:
    """Confirm one exact queued event instead of a process-global flush."""
    if receipt is None:
        return flush_events(timeout=timeout)
    if not isinstance(receipt, _EventReceipt):
        return False
    if not receipt.ready.wait(timeout=max(0.0, float(timeout))):
        return False
    return receipt.succeeded


def _flush_events_at_exit() -> None:
    if not flush_events(timeout=10.0):
        print("[journal] ERROR: timeout vaciando eventos al cerrar")


atexit.register(_flush_events_at_exit)


def event(signal_id: str, ev: str, **fields):
    """Queue one JSONL event without blocking Telegram on disk I/O."""
    receipt = _EventReceipt()
    is_test = _mark_and_get_test(signal_id)
    target_file = EVENTS_TEST_FILE if is_test else EVENTS_FILE
    try:
        semantic = {"sig": signal_id, "ev": ev}
        if is_test:
            semantic["test"] = True
        semantic.update(fields)
        for key, value in causal_trace.current_fields().items():
            semantic.setdefault(key, value)
        record = {
            **semantic,
            "schema_version": 2,
            "event_id": causal_trace.new_event_id(),
            "session_id": PROCESS_SESSION_ID,
            "ts": _now_iso(),
            "monotonic_ns": time.monotonic_ns(),
            "code_commit": os.getenv("BOT_WATCHER_VERIFIED_HEAD"),
            "payload_sha256": _payload_sha256(semantic),
        }
        line = json.dumps(record, default=_serialize) + "\n"
        _ensure_event_writer()
        _event_queue.put((
            target_file,
            line,
            ev,
            signal_id,
            None,
            receipt,
        ))
    except Exception as exc:
        _record_event_write_failure()
        receipt.error = f"{type(exc).__name__}: {exc}"
        receipt.ready.set()
        print(
            f"[journal] ERROR encolando evento {ev} "
            f"para {signal_id}: {exc}"
        )
    return receipt


# Structured anomalies layered on the atomic event stream.
SEVERITIES = ("info", "warning", "critical")
CATEGORIES = (
    "naked", "sl_be", "fill", "channel_msg", "levels", "mt5", "outcome"
)


def anomaly(signal_id: str, category: str, severity: str,
            detail: str, **ctx):
    """Registra una anomalía estructurada (capa sobre event()).

    Escribe ev='anomaly' al journal con esquema fijo
    {category, severity, detail, **ctx}. Si severity='critical' dispara
    notify() automáticamente vía _notify_critical().

    Antes había ~6 sitios hardcoded que hacían journal.event() + notify()
    a mano con texto ad-hoc. Esto los unifica: las alertas de Telegram
    pasan a ser un subproducto — cualquier anomalía crítica notifica, con
    categoría y severidad serializadas en notify_sent (filtrable).

    Spec: docs/superpowers/specs/2026-05-19-registro-anomalias-design.md
    """
    if severity not in SEVERITIES:
        raise ValueError(
            f"severity '{severity}' inválida; debe ser una de {SEVERITIES}")
    if category not in CATEGORIES:
        raise ValueError(
            f"category '{category}' inválida; debe ser una de {CATEGORIES}")
    event(signal_id, "anomaly", category=category, severity=severity,
          detail=detail, **ctx)
    if severity == "critical":
        _notify_critical(signal_id, category, detail, ctx)


def set_notify_loop(loop: Optional[asyncio.AbstractEventLoop]):
    """Registra el loop principal para notificar desde threads auxiliares."""
    global _notify_loop
    _notify_loop = loop


def format_critical_notification(signal_id: str, category: str,
                                 detail: str, ctx: dict) -> str:
    headings = {
        "mt5": "🚨 MT5 NECESITA ATENCIÓN",
        "naked": "🚨 OPERACIÓN SIN PROTECCIÓN",
        "sl_be": "🚨 PROTECCIÓN NO APLICADA",
        "fill": "🚨 APERTURA NO CONFIRMADA",
        "channel_msg": "⚠️ MENSAJE SIN ASOCIAR",
        "levels": "🚨 NIVELES NO APLICADOS",
        "outcome": "🚨 CIERRE NO CONFIRMADO",
    }
    actions = {
        "mt5": "Acción: revisa la conexión y el estado de MT5.",
        "naked": "Acción urgente: comprueba la operación y coloca protección.",
        "sl_be": "Acción: comprueba el SL de las posiciones indicadas.",
        "fill": "Acción: confirma en MT5 si la posición llegó a abrirse.",
        "channel_msg": "Acción: revisa el mensaje y la operación relacionada.",
        "levels": "Acción: comprueba los niveles actuales en MT5.",
        "outcome": "Acción: confirma qué posiciones siguen abiertas en MT5.",
    }
    lines = [headings.get(category, "🚨 REVISIÓN URGENTE"), str(detail)]

    if signal_id.startswith("canal1_") or signal_id.startswith("canal2_"):
        channel, message_id = signal_id.split("_", 1)
        label = provider_display_name(channel)
        lines.append(f"Referencia: {label} · mensaje {message_id}")
    if ctx.get("direction"):
        lines.append(f"Dirección: {ctx['direction']}")

    tickets = ctx.get("tickets")
    if tickets is None and ctx.get("ticket") is not None:
        tickets = [ctx["ticket"]]
    if tickets:
        if not isinstance(tickets, (list, tuple, set)):
            tickets = [tickets]
        ticket_text = ", ".join(str(ticket) for ticket in tickets)
        label = "Ticket" if len(tickets) == 1 else "Tickets"
        lines.append(f"{label}: {ticket_text}")

    market_parts = []
    for key, label in (("entry", "Entrada"), ("sl", "SL"),
                       ("current_price", "Mercado"), ("price", "Precio")):
        if ctx.get(key) is not None:
            market_parts.append(f"{label} {ctx[key]}")
    if market_parts:
        lines.append(" · ".join(market_parts))
    if ctx.get("text_preview"):
        preview = " ".join(str(ctx["text_preview"]).split())[:180]
        lines.append(f"Proveedor: “{preview}”")

    lines.append(actions.get(category, "Acción: revisa la situación en MT5."))
    return "\n".join(lines)


def _notify_critical(signal_id: str, category: str, detail: str, ctx: dict):
    """Dispara notify() para una anomalía crítica. Defensivo: nunca lanza
    al caller (try/except). Import lazy de listener.notify para evitar
    import circular journal→listener (listener importa journal)."""
    try:
        from listener import notify
        text = format_critical_notification(signal_id, category, detail, ctx)

        async def deliver(fingerprint: str):
            delivered = False
            try:
                delivered = await notify(text) is not False
            except Exception as exc:
                print(f"[journal.anomaly] notify delivery failed: {exc}")
            finally:
                _critical_notify_complete(fingerprint, delivered)

        def schedule(loop: asyncio.AbstractEventLoop, fingerprint: str):
            try:
                loop.create_task(deliver(fingerprint))
            except Exception as e:
                _critical_notify_complete(fingerprint, False)
                print(f"[journal.anomaly] notify schedule failed: {e}")

        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop is not None and running_loop.is_running():
            fingerprint = _critical_notify_claim(
                signal_id,
                category,
                detail,
                ctx=ctx,
            )
            if fingerprint is None:
                event(
                    signal_id,
                    "critical_notify_suppressed",
                    category=category,
                    detail=detail,
                    cooldown_s=_critical_notify_cooldown_s(),
                )
                return
            schedule(running_loop, fingerprint)
            return

        loop = _notify_loop
        if loop is not None and loop.is_running():
            fingerprint = _critical_notify_claim(
                signal_id,
                category,
                detail,
                ctx=ctx,
            )
            if fingerprint is None:
                event(
                    signal_id,
                    "critical_notify_suppressed",
                    category=category,
                    detail=detail,
                    cooldown_s=_critical_notify_cooldown_s(),
                )
                return
            try:
                loop.call_soon_threadsafe(schedule, loop, fingerprint)
            except Exception:
                _critical_notify_complete(fingerprint, False)
                raise
            return

        print("[journal.anomaly] no asyncio loop available for critical notify")
    except Exception as e:
        print(f"[journal.anomaly] _notify_critical failed: {e}")


def health_verdict(anomalies: list[dict]) -> str:
    """Veredicto de salud de un trade según la severidad máxima de sus
    anomalías. Función PURA — base del campo `health` del ledger.

    failed   ← alguna severity='critical'
    degraded ← alguna severity='warning' (y ninguna critical)
    ok       ← ninguna anomalía o solo 'info'
    """
    if any(a.get("severity") == "critical" for a in anomalies):
        return "failed"
    if any(a.get("severity") == "warning" for a in anomalies):
        return "degraded"
    return "ok"


# ─── API: tracking acumulativo en memoria ───────────────────────────────────

def begin_trade(signal_id: str, **initial_fields):
    """Inicializa el tracking de una señal en memoria.

    Llama a esto cuando se crea la Signal en el listener (al recibir el
    sticker C1 / "BUY NOW" C2). A partir de aquí update_trade/append_mgmt/
    update_extremes irán acumulando datos.
    """
    with _trades_lock:
        _trades[signal_id] = {
            "signal_id": signal_id,
            "mgmt_msgs_classified": [],
            "mgmt_msgs_applied": [],
            "mgmt_msgs_required": [],
            "mgmt_msgs_outcomes": [],
            "mfe_usd": 0.0,
            "mae_usd": 0.0,
            "n_dca_filled": 0,
            **initial_fields,
        }


def update_trade(signal_id: str, **fields):
    """Actualiza campos del trade en memoria (no escribe a disco)."""
    with _trades_lock:
        if signal_id in _trades:
            _trades[signal_id].update(fields)


_MANAGEMENT_OUTCOMES = {"requested", "deferred", "ignored", "failed"}


def append_mgmt(signal_id: str, classified: str, applied: bool,
                required: bool = True, outcome: Optional[str] = None):
    """Registra una gestión sin confundir solicitud con confirmación MT5."""
    resolved_outcome = outcome or ("requested" if applied else "ignored")
    if resolved_outcome not in _MANAGEMENT_OUTCOMES:
        raise ValueError(f"invalid management outcome: {resolved_outcome}")
    with _trades_lock:
        if signal_id in _trades:
            t = _trades[signal_id]
            t["mgmt_msgs_classified"].append(classified)
            t["mgmt_msgs_applied"].append(bool(applied))
            t.setdefault("mgmt_msgs_required", []).append(bool(required))
            t.setdefault("mgmt_msgs_outcomes", []).append(resolved_outcome)


def update_extremes(signal_id: str, current_pl: float, ts: Optional[str] = None):
    """Trackea MFE (máximo favorable) y MAE (máximo adverso) del PnL flotante."""
    if ts is None:
        ts = _now_iso()
    with _trades_lock:
        t = _trades.get(signal_id)
        if t is None:
            return
        if current_pl > (t.get("mfe_usd") or 0):
            t["mfe_usd"] = current_pl
            t["mfe_at"] = ts
        if current_pl < (t.get("mae_usd") or 0):
            t["mae_usd"] = current_pl
            t["mae_at"] = ts


def increment_dca_filled(signal_id: str):
    """Incrementa el contador de DCAs filled."""
    with _trades_lock:
        if signal_id in _trades:
            _trades[signal_id]["n_dca_filled"] = (
                _trades[signal_id].get("n_dca_filled", 0) + 1
            )


def get_trade(signal_id: str) -> Optional[dict]:
    """Devuelve copia del estado actual del trade en memoria."""
    with _trades_lock:
        t = _trades.get(signal_id)
        return dict(t) if t else None


# ─── API: cierre y escritura al CSV ────────────────────────────────────────

def finalize_trade(signal_id: str, **final_fields):
    """Cierra el trade en memoria y escribe la fila final al CSV.

    final_fields debe incluir como mínimo:
        closed_at_utc, closed_by, total_pnl_usd, duration_sec
    """
    with _trades_lock:
        t = _trades.pop(signal_id, None)
    if t is None:
        # Por si finalize_trade se llama dos veces o sin begin_trade previo
        t = {"signal_id": signal_id, "mgmt_msgs_classified": [],
             "mgmt_msgs_applied": [], "mgmt_msgs_required": [],
             "mgmt_msgs_outcomes": [],
             "mfe_usd": 0.0, "mae_usd": 0.0}

    t.update(final_fields)
    t["n_mgmt_msgs"] = len(t.get("mgmt_msgs_classified", []))
    t["tag"] = _auto_tag(t)

    # Serializar listas/dicts para el CSV
    for key in ("tps_initial", "mgmt_msgs_classified", "mgmt_msgs_applied"):
        if key in t and isinstance(t[key], (list, tuple, dict)):
            t[key] = json.dumps(t[key], default=_serialize)

    # Rutear a fichero TEST si la señal estuvo marcada como test
    is_test = _mark_and_get_test(signal_id)
    target_file = JOURNAL_TEST_FILE if is_test else JOURNAL_FILE

    try:
        with _file_lock:
            write_header = not target_file.exists()
            with open(target_file, "a", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(
                    f, fieldnames=CSV_FIELDS, extrasaction="ignore"
                )
                if write_header:
                    writer.writeheader()
                writer.writerow(t)
    except Exception as e:
        print(f"[journal] ERROR escribiendo fila final {signal_id}: {e}")

    # También dejamos rastro en eventos (ya rutea solo según _mark_and_get_test)
    event(
        signal_id,
        "signal_closed",
        tag=t.get("tag"),
        total_pl=t.get("total_pnl_usd"),
        total_pl_account_currency=t.get(
            "total_pnl_account_currency",
            t.get("total_pnl_usd"),
        ),
        mfe_account_currency=t.get(
            "mfe_account_currency",
            t.get("mfe_usd"),
        ),
        mae_account_currency=t.get(
            "mae_account_currency",
            t.get("mae_usd"),
        ),
        money_unit="account_currency",
        account_currency=t.get("account_currency"),
        closed_at_utc=t.get("closed_at_utc"),
        time_basis="UTC",
        mt5_server_offset_s=t.get("mt5_server_offset_s"),
    )

    # Limpieza del registry para no acumular memoria de tests pasados
    if is_test:
        _forget_test_signal(signal_id)


# ─── Auto-etiquetado ────────────────────────────────────────────────────────

def _auto_tag(row: dict) -> str:
    """Asigna una etiqueta semántica al trade según resultado y métricas.

    Tags:
        WIN_CLEAN         — Cerró en TP, mae < $5 (operación ideal)
        WIN_NORMAL        — Cerró en TP, sin nada raro
        WIN_SCARY         — Cerró en TP pero mae > $15 (sufrimos pero salimos)
        WIN_SHORT         — Cerró en TP pero mfe > pnl×1.5 (cerramos pronto)
        LOSS_CLEAN        — Cerró en SL, mfe < $3 (señal mala desde inicio)
        LOSS_NORMAL       — Cerró en SL, sin patrón claro
        LOSS_REVERSAL     — Cerró en SL pero mfe > $10 (BE habría salvado)
        LOSS_MGMT_IGNORED — Cerró en SL y se ignoró >=1 msg de gestión
        TIMESTOP          — Cerró por time-stop o notificación
        MANUAL            — Cerrado manualmente por el usuario
        OTHER             — Cualquier otro caso
    """
    closed_by = (row.get("closed_by") or "").upper()
    pl = row.get("total_pnl_usd") or 0
    mfe = row.get("mfe_usd") or 0
    mae = abs(row.get("mae_usd") or 0)

    classified = row.get("mgmt_msgs_classified", [])
    applied = row.get("mgmt_msgs_applied", [])
    required = row.get("mgmt_msgs_required")
    if isinstance(required, list) and isinstance(applied, list):
        n_ignored = sum(
            1 for index, is_required in enumerate(required)
            if is_required
            and not bool(applied[index] if index < len(applied) else False)
        )
    else:
        n_mgmt = len(classified) if isinstance(classified, list) else 0
        n_applied = sum(applied) if isinstance(applied, list) else 0
        n_ignored = max(0, n_mgmt - n_applied)

    if "TIMESTOP" in closed_by or "TIME_STOP" in closed_by:
        return "TIMESTOP"
    if "MANUAL" in closed_by:
        return "MANUAL"

    if pl > 0:
        if mae < 5:
            return "WIN_CLEAN"
        if mfe > pl * 1.5:
            return "WIN_SHORT"
        if mae > 15:
            return "WIN_SCARY"
        return "WIN_NORMAL"

    if pl < 0:
        if n_ignored > 0:
            return "LOSS_MGMT_IGNORED"
        if mfe < 3:
            return "LOSS_CLEAN"
        if mfe > 10:
            return "LOSS_REVERSAL"
        return "LOSS_NORMAL"

    return "OTHER"


# ─── Reader util ────────────────────────────────────────────────────────────

def get_timeline(signal_id: str) -> list[dict]:
    """Reconstruye el timeline de eventos de una señal desde el JSONL.

    Útil para forensics manual o para herramientas de visualización.
    """
    if not EVENTS_FILE.exists():
        return []
    out = []
    with open(EVENTS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("sig") == signal_id:
                out.append(rec)
    return out
