"""
Punto de entrada. Arranca MT5 y el listener de Telegram.

Uso:
    python main.py

Primera vez (sin sticker IDs de Canal 1):
    El bot arrancará y cuando llegue un sticker imprimirá el ID en consola.
    Copia esos IDs al .env y reinicia.
"""

import asyncio
import builtins
import faulthandler
import io
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Reconfigura stdout/stderr a UTF-8 en Windows para que los acentos y
# símbolos como -> / 'señal' / emojis no rompan la consola con cp1252.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ─── Monkey-patch de print() para añadir timestamp a TODA la salida ──────────
# Sin esto el terminal era una sopa de líneas sin orden temporal, imposible
# de correlacionar con eventos del journal o con tu Telegram. Ahora cada print
# del proyecto sale como [HH:MM:SS.mmm] (hora LOCAL del sistema, que coincide
# con la que ves en tu Telegram). El journal sigue usando UTC por estándar.
_original_print = builtins.print


def _timestamped_print(*args, **kwargs):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    _original_print(f"[{ts}]", *args, **kwargs)


builtins.print = _timestamped_print

import config
import executor
import journal
import live_auditor
import pending_actions
from listener import client, poll_loop, _is_transient_telegram_history_error
from parser import predict_sl_from_entry

_freeze_traceback_file_handle = None
_freeze_traceback_file_path: Path | None = None
_TELEGRAM_RUN_BACKOFF_BASE_S = 15.0
_TELEGRAM_RUN_BACKOFF_MAX_S = 120.0


def _should_alert_sustained_disconnect(connected: bool,
                                        last_state,
                                        age_s: float,
                                        already_alerted: bool,
                                        threshold_s: float) -> bool:
    """Decisión PURA: ¿emitir anomaly de disconnect sostenido?

    True solo si — está desconectado AHORA, lo estaba también en la
    última observación (no es una transición recién detectada), llevamos
    >= threshold_s en este estado, y aún no se alertó para este disconnect.
    Usado por _telegram_connection_monitor y _mt5_connection_monitor.
    """
    return (connected is False
            and last_state is False
            and age_s >= threshold_s
            and not already_alerted)


def _count_open_signals_unique(state_manager) -> int:
    """Cuenta senales abiertas sin duplicar aliases del StateManager."""
    seen: set[int] = set()
    count = 0
    for sig in state_manager._signals.values():
        if sig.status != "open":
            continue
        obj_id = id(sig)
        if obj_id in seen:
            continue
        seen.add(obj_id)
        count += 1
    return count


def _telegram_run_backoff_seconds(failures: int) -> float:
    exponent = max(0, failures - 1)
    return min(
        _TELEGRAM_RUN_BACKOFF_MAX_S,
        _TELEGRAM_RUN_BACKOFF_BASE_S * (2 ** exponent),
    )


async def _run_until_disconnected_with_backoff() -> None:
    failures = 0
    while True:
        try:
            await client.run_until_disconnected()
            return
        except Exception as e:
            if not _is_transient_telegram_history_error(e):
                raise

            failures += 1
            cooldown_s = _telegram_run_backoff_seconds(failures)
            error = str(e)
            print(
                "[Telegram] Error temporal GetHistoryRequest; "
                f"reintento en {cooldown_s:.0f}s: {error}"
            )
            journal.event(
                "bot",
                "telegram_run_until_disconnected_backoff",
                failures=failures,
                cooldown_s=cooldown_s,
                error=error,
            )
            await asyncio.sleep(cooldown_s)

            try:
                if not client.is_connected():
                    await client.connect()
            except Exception as reconnect_error:
                journal.event(
                    "bot",
                    "telegram_reconnect_after_history_error_failed",
                    failures=failures,
                    error=str(reconnect_error),
                )


def _unique_open_signals(state_manager) -> list:
    seen: set[int] = set()
    out = []
    for sig in state_manager._signals.values():
        if sig.status != "open":
            continue
        obj_id = id(sig)
        if obj_id in seen:
            continue
        seen.add(obj_id)
        out.append(sig)
    return out


def _should_audit_mt5_reconnect(connected: bool, previous_state,
                                open_signals: int) -> bool:
    return connected is True and previous_state is False and open_signals > 0


def _should_alert_mt5_trade_disabled(connected: bool,
                                     trade_allowed,
                                     tradeapi_disabled,
                                     already_alerted: bool) -> bool:
    """True si MT5 está conectado pero no acepta trading algorítmico."""
    if connected is not True or already_alerted:
        return False
    return trade_allowed is False or tradeapi_disabled is True


async def _post_mt5_reconnect_audit(disconnected_duration_s: float | None):
    """Log-only audit tras recuperar MT5 con senales abiertas."""
    from state import state

    try:
        for sig in _unique_open_signals(state):
            tickets = list(sig.all_filled_tickets)
            try:
                open_positions = await asyncio.to_thread(
                    executor.position_pnls, tickets)
            except Exception as e:
                open_positions = []
                journal.anomaly(
                    f"{sig.channel}_{sig.message_id}", "mt5", "warning",
                    f"post-reconnect audit fallo leyendo position_pnls: "
                    f"{type(e).__name__}: {str(e)[:160]}",
                    disconnected_duration_s=disconnected_duration_s)

            open_tickets = [int(t) for t, _ in open_positions]
            missing_tickets = [int(t) for t in tickets if t not in open_tickets]
            journal.event(
                f"{sig.channel}_{sig.message_id}",
                "mt5_reconnect_signal_audit",
                disconnected_duration_s=disconnected_duration_s,
                known_tickets=[int(t) for t in tickets],
                open_tickets=open_tickets,
                missing_tickets=missing_tickets,
                n_open=len(open_tickets),
                has_tps=bool(sig.tps),
                has_sl=sig.sl is not None,
                status=sig.status,
            )
    except Exception as e:
        journal.anomaly("bot", "mt5", "warning",
                        f"post-reconnect audit crasheo: "
                        f"{type(e).__name__}: {str(e)[:200]}",
                        exc_type=type(e).__name__)


async def _heartbeat(interval_sec: int = 300):
    """Escribe un evento 'heartbeat' cada N segundos al journal.
    Si el bot se cae en silencio, el log mostrará exactamente cuándo paró."""
    await asyncio.sleep(interval_sec)   # primer beat tras arrancar
    while True:
        from state import state
        open_signals = _count_open_signals_unique(state)
        journal.event("bot", "heartbeat",
                      open_signals=open_signals,
                      utc=datetime.utcnow().isoformat(timespec="seconds"))
        await asyncio.sleep(interval_sec)


def _write_runtime_heartbeat(path: Path | None = None) -> None:
    path = path or Path(config.BOT_RUNTIME_HEARTBEAT_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": os.getpid(),
        "utc": datetime.utcnow().isoformat(timespec="milliseconds"),
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


def _freeze_traceback_enabled(timeout_sec: float) -> bool:
    return (
        timeout_sec > 0
        and hasattr(faulthandler, "cancel_dump_traceback_later")
        and hasattr(faulthandler, "dump_traceback_later")
    )


def _freeze_traceback_file(path: Path):
    global _freeze_traceback_file_handle, _freeze_traceback_file_path

    if (_freeze_traceback_file_handle is not None
            and not _freeze_traceback_file_handle.closed
            and _freeze_traceback_file_path == path):
        return _freeze_traceback_file_handle

    if (_freeze_traceback_file_handle is not None
            and not _freeze_traceback_file_handle.closed):
        _freeze_traceback_file_handle.close()

    path.parent.mkdir(parents=True, exist_ok=True)
    _freeze_traceback_file_path = path
    _freeze_traceback_file_handle = path.open(
        "a", encoding="utf-8", buffering=1)
    return _freeze_traceback_file_handle


def _arm_freeze_traceback_dump(timeout_sec: float | None = None,
                               path: Path | None = None) -> bool:
    timeout = (
        float(timeout_sec)
        if timeout_sec is not None
        else float(config.BOT_FREEZE_TRACEBACK_SEC)
    )
    if not _freeze_traceback_enabled(timeout):
        return False

    trace_path = path or Path(config.BOT_FREEZE_TRACEBACK_FILE)
    f = _freeze_traceback_file(trace_path)
    faulthandler.cancel_dump_traceback_later()
    faulthandler.dump_traceback_later(
        timeout, repeat=False, file=f, exit=False)
    return True


async def _runtime_heartbeat(interval_sec: float | None = None):
    interval = (
        float(interval_sec)
        if interval_sec is not None
        else float(config.BOT_RUNTIME_HEARTBEAT_SEC)
    )
    interval = max(1.0, interval)
    while True:
        try:
            _write_runtime_heartbeat()
            _arm_freeze_traceback_dump()
        except Exception as e:
            print(f"[Heartbeat] runtime heartbeat write failed: {e}")
        await asyncio.sleep(interval)


def _should_apply_naked_protective_sl(sig) -> bool:
    return (
        config.STRATEGY_NAKED_PROTECTIVE_SL_ENABLED
        and sig.status == "open"
        and not sig.tps
        and sig.sl is None
        and sig.market_fill_price is not None
        and bool(sig.all_filled_tickets)
        and not getattr(sig, "_naked_protective_sl_applied", False)
    )


async def _apply_naked_protective_sl(sig, elapsed_s: float):
    if not _should_apply_naked_protective_sl(sig):
        return None

    sl = predict_sl_from_entry(
        sig.direction,
        sig.market_fill_price,
        config.STRATEGY_NAKED_PROTECTIVE_SL_OFFSET_USD,
    )
    sig.sl = sl
    sig.levels_predicted = True
    sig._naked_protective_sl_applied = True

    tickets = list(sig.all_filled_tickets)
    for ticket in tickets:
        pending_actions.enqueue_modify_sl(
            sig,
            ticket,
            sl,
            label=f"NAKED_PROTECTIVE_SL #{ticket}",
        )

    sig_id = f"{sig.channel}_{sig.message_id}"
    journal.event(sig_id, "naked_protective_sl_applied",
                  channel=sig.channel,
                  direction=sig.direction,
                  tickets=tickets,
                  entry=sig.market_fill_price,
                  sl=sl,
                  offset_usd=config.STRATEGY_NAKED_PROTECTIVE_SL_OFFSET_USD,
                  elapsed_s=round(elapsed_s, 1))
    journal.anomaly(sig_id, "naked", "warning",
                    "SL provisional aplicado por watchdog a signal naked",
                    channel=sig.channel,
                    direction=sig.direction,
                    tickets=tickets,
                    entry=sig.market_fill_price,
                    sl=sl,
                    elapsed_s=round(elapsed_s, 1))
    return sl


async def _naked_signal_watchdog(check_interval_s: int = 60,
                                  alert_after_s: int = 120):
    """Vigila senales abiertas sin SL/TP aplicados en el bot.

    Para cada senal con status=open, si han pasado >alert_after_s segundos
    desde apertura y NO tiene tps NI sl en el estado interno, manda push
    notification URGENTE al usuario para que aplique manualmente o cierre
    la posicion en MT5.

    Defensa contra escenarios donde el flujo normal fallo:
      - Texto canal1 nunca llego o no se aplico (sesion 2026-05-07
        canal1_19484 y canal1_19498 quedaron NAKED y solo nos dimos
        cuenta al revisar logs al dia siguiente).
      - Edits canal2 que el bot no proceso por algun edge case.
      - _handle_canal1_text rechazado por algun motivo no esperado.

    Marca cada signal alertada con _naked_alerted=True (atributo dinamico
    en el dataclass) para no spamear notifies cada minuto.
    """
    from state import state
    print(f"[Watchdog] naked_signal activo. Check cada {check_interval_s}s, "
          f"alerta tras {alert_after_s}s sin TPs/SL.")

    while True:
        try:
            now = datetime.utcnow()
            for sig_id, sig in list(state._signals.items()):
                if sig.status != "open":
                    continue
                if sig.tps or sig.sl:
                    continue
                # Edad desde apertura
                elapsed = (now - sig.timestamp).total_seconds()
                if elapsed < alert_after_s:
                    continue
                # Ya alertada — no spamear
                if getattr(sig, "_naked_alerted", False):
                    continue

                # Notify URGENT
                full_sig_id = f"{sig.channel}_{sig.message_id}"
                print(f"[Watchdog] 🚨 NAKED detectada: {full_sig_id} "
                      f"({elapsed:.0f}s sin tps/sl)")
                journal.event(full_sig_id, "naked_signal_detected",
                              channel=sig.channel,
                              direction=sig.direction,
                              ticket=sig.market_ticket,
                              entry=sig.market_fill_price,
                              elapsed_s=round(elapsed, 1))
                protective_sl = await _apply_naked_protective_sl(sig, elapsed)
                # Migrado a anomaly() — _notify_critical dispara la alerta
                # de Telegram automáticamente (T3 del plan). El bloque
                # manual de notify queda eliminado.
                journal.anomaly(full_sig_id, "naked", "critical",
                                f"posicion abierta hace {elapsed/60:.0f} min sin "
                                f"TPs ni SL aplicados por el bot",
                                channel=sig.channel,
                                direction=sig.direction,
                                ticket=sig.market_ticket,
                                entry=sig.market_fill_price,
                                elapsed_s=round(elapsed, 1),
                                protective_sl=protective_sl)

                # Marcar para no re-alertar
                sig._naked_alerted = True
        except Exception as e:
            print(f"[Watchdog] error iterando signals: {e}")

        await asyncio.sleep(check_interval_s)


async def _pending_correction_watchdog(check_interval_s: int = 30,
                                        alert_after_s: int = 60):
    """Vigila signals con valores incoherentes pendientes de correccion.

    Cuando _update_signal_from_parsed detecta que el canal mando SL/TPs
    incoherentes con la direccion (typo del trader), marca
    signal.pending_correction y conserva los valores anteriores. Este
    watchdog notifica URGENT al usuario si pasan >alert_after_s segundos
    sin que el canal corrija.

    Casos reales que lo motivan (sesion 2026-05-13):
      - canal2_12334: SL=4796 para BUY @ 4704 (typo, corregido en 12s)
      - canal2_12338: range absurdo para SELL (typo, corregido en 43s)

    En ambos el canal corrigio rapido, pero si NO corrige, el usuario
    debe enterarse pronto para decidir manualmente.

    Marca pending_correction["notified_urgent"]=True tras notify para
    no spamear cada 30s.
    """
    from state import state
    print(f"[Watchdog] pending_correction activo. Check cada {check_interval_s}s, "
          f"URGENT tras {alert_after_s}s sin correccion.")

    while True:
        try:
            now = datetime.utcnow()
            for sig_id, sig in list(state._signals.items()):
                if sig.status != "open":
                    continue
                pc = getattr(sig, "pending_correction", None)
                if not pc:
                    continue
                if pc.get("notified_urgent"):
                    continue
                # Edad desde que se detecto el typo
                try:
                    since = datetime.fromisoformat(pc["since_utc"])
                except Exception:
                    continue
                elapsed = (now - since).total_seconds()
                if elapsed < alert_after_s:
                    continue

                # Notify URGENT
                full_sig_id = f"{sig.channel}_{sig.message_id}"
                print(f"[Watchdog] 🚨 PENDING CORRECTION sin resolver: {full_sig_id} "
                      f"({elapsed:.0f}s)")
                journal.event(full_sig_id, "pending_correction_urgent",
                              channel=sig.channel,
                              direction=sig.direction,
                              ticket=sig.market_ticket,
                              entry=sig.market_fill_price,
                              field=pc.get("field"),
                              received_tps=pc.get("received_tps"),
                              received_sl=pc.get("received_sl"),
                              kept_tps=pc.get("kept_tps"),
                              kept_sl=pc.get("kept_sl"),
                              reason=pc.get("reason"),
                              elapsed_s=round(elapsed, 1))
                try:
                    from listener import notify
                    received_str = (
                        f"  TPs recibidos: {pc.get('received_tps')}\n"
                        if pc.get("received_tps") else ""
                    ) + (
                        f"  SL recibido: {pc.get('received_sl')}\n"
                        if pc.get("received_sl") is not None else ""
                    )
                    kept_str = (
                        f"  TPs en uso: {pc.get('kept_tps')}\n"
                        if pc.get("kept_tps") else ""
                    ) + (
                        f"  SL en uso: {pc.get('kept_sl')}\n"
                        if pc.get("kept_sl") is not None else ""
                    )
                    await notify(
                        f"🚨 [URGENT] Pending correction sin resolver — {full_sig_id}\n"
                        f"\n"
                        f"Hace {elapsed:.0f}s el canal mando valores incoherentes con\n"
                        f"la direccion ({sig.direction} entry={sig.market_fill_price}).\n"
                        f"El bot NO los aplico esperando correccion del canal.\n"
                        f"La correccion NO ha llegado.\n"
                        f"\n"
                        f"Problema: {pc.get('reason')}\n"
                        f"\n"
                        f"Recibido del canal:\n{received_str}\n"
                        f"En uso por el bot:\n{kept_str}\n"
                        f"Ticket: #{sig.market_ticket}\n"
                        f"\n"
                        f"ACCION: revisa el canal manualmente y aplica/corrige\n"
                        f"SL/TP en MT5 si procede. La posicion sigue abierta\n"
                        f"con los valores 'En uso' (predictor o anterior)."
                    )
                except Exception as e:
                    print(f"[Watchdog] notify error: {e}")

                pc["notified_urgent"] = True
        except Exception as e:
            print(f"[Watchdog] pending_correction iterando: {e}")

        await asyncio.sleep(check_interval_s)


async def _position_reconciler(check_interval_s: int = 60,
                                stale_alert_h: float = 12.0):
    """Reconcilia el estado interno del bot con MT5 — CAUSA RAIZ de los
    trades huerfanos.

    Problema: el auto-finalize del dca_monitor solo dispara con n_open==0
    exacto Y depende de que el monitor de esa senal siga vivo. Si el
    monitor murio (crash/restart) o un cierre se escapo, la senal queda
    'open' para siempre en el journal. Caso real canal1_19684: el trader
    anuncio FULL TARGET, las posiciones cerraron en MT5, pero el journal
    nunca registro signal_closed (21h+ marcada open).

    Este reconciler es INDEPENDIENTE del monitor individual: cada
    check_interval_s consulta MT5 directamente. Para cada senal open:
      - Si tiene tickets registrados pero NINGUNO sigue abierto en MT5
        (y >90s desde apertura) → la senal cerro → _finalize_signal.
      - Si lleva >stale_alert_h horas abierta → notify URGENT (backup
        defensivo por si el arreglo principal tiene algun agujero).
    """
    import MetaTrader5 as _mt5
    from state import state
    print(f"[Reconciler] activo. Check cada {check_interval_s}s, "
          f"alerta stale a {stale_alert_h}h.")

    while True:
        try:
            now = datetime.utcnow()
            for sig_id, sig in list(state._signals.items()):
                if sig.status != "open":
                    continue
                tickets = list(sig.all_filled_tickets)
                if not tickets:
                    continue  # market aun no abierto — nada que reconciliar
                age_s = (now - sig.timestamp).total_seconds()
                if age_s < 90:
                    continue  # demasiado reciente — dar margen al flujo normal

                # ¿Cuantos tickets siguen REALMENTE abiertos en MT5?
                n_open_mt5 = 0
                for t in tickets:
                    try:
                        pos = await asyncio.to_thread(_mt5.positions_get, ticket=t)
                        if pos:
                            n_open_mt5 += 1
                    except Exception:
                        n_open_mt5 += 1  # ante duda, asumir abierta (no cerrar)

                full_sig_id = f"{sig.channel}_{sig.message_id}"

                if n_open_mt5 == 0:
                    # MT5 cerro todas las posiciones pero el bot no lo registro.
                    print(f"[Reconciler] {full_sig_id}: 0/{len(tickets)} tickets "
                          f"abiertos en MT5 → finalizar (cierre no detectado)")
                    journal.event(full_sig_id, "reconciler_closed_detected",
                                  n_tickets=len(tickets),
                                  age_s=round(age_s, 1))
                    sig.status = "closed"  # corta el monitor de esa senal
                    try:
                        from listener import _finalize_signal
                        await _finalize_signal(
                            sig, closed_by="RECONCILER",
                            notes="cierre MT5 detectado por reconciler "
                                  "(monitor no lo registro)")
                    except Exception as e:
                        print(f"[Reconciler] error finalizando {full_sig_id}: {e}")
                elif age_s > stale_alert_h * 3600:
                    # Backup: lleva demasiado abierta. Avisar (una vez).
                    if not getattr(sig, "_stale_alerted", False):
                        print(f"[Reconciler] 🚨 {full_sig_id} STALE "
                              f"({age_s/3600:.1f}h abierta, {n_open_mt5} pos)")
                        journal.event(full_sig_id, "stale_signal_detected",
                                      age_h=round(age_s / 3600, 1),
                                      n_open_mt5=n_open_mt5)
                        try:
                            from listener import notify
                            await notify(
                                f"🚨 [URGENT] Trade STALE — {full_sig_id}\n"
                                f"\n"
                                f"Lleva {age_s/3600:.1f}h abierta con {n_open_mt5} "
                                f"posicion(es) aun en MT5.\n"
                                f"El canal no ha mandado cierre y no se ha\n"
                                f"tocado TP/SL.\n"
                                f"\n"
                                f"ACCION: revisa la posicion en MT5 — puede ser\n"
                                f"un trade olvidado o un fallo de gestion."
                            )
                        except Exception as e:
                            print(f"[Reconciler] notify error: {e}")
                        sig._stale_alerted = True
        except Exception as e:
            print(f"[Reconciler] error iterando signals: {e}")

        await asyncio.sleep(check_interval_s)


async def _telegram_connection_monitor(interval_sec: int = 5):
    """Vigila el estado de la conexión de Telethon y loguea cambios.

    Por qué: cuando Telethon pierde la conexión (network jitter, Telegram
    time-out, sleep del SO, etc.) los mensajes del canal se acumulan en el
    servidor. Al reconectar, Telegram entrega TODOS de golpe — y el bot
    los procesa con `msg.date` antiguo vs `datetime.utcnow()` actual.
    Resultado: picos de 30-60s en tg→bot delay.

    Visto en sesión 28-abr: 2 señales con ~57s de delay (canal2_11986 y
    canal2_11989). Sin este monitor era hipótesis; con él será dato.

    Eventos al journal:
      • telegram_connection_change : edge-trigger cuando is_connected() cambia
      • telegram_connection_beat   : cada hora si no hubo cambio (= sigo OK)

    El intervalo de 5s es suficiente para capturar la mayoría de
    desconexiones reales y no satura el log (un beat cada 720 ciclos).
    """
    last_state = None
    last_change = datetime.utcnow()
    last_periodic_beat = datetime.utcnow()
    alerted_disconnect = False
    DISCONNECT_ALERT_THRESHOLD_S = 300   # 5 min — antes era silencio total
    PERIODIC_BEAT_S = 3600  # cada hora un "sigo en este estado"

    while True:
        try:
            connected = client.is_connected()
        except Exception as e:
            print(f"[Telegram Monitor] error chequeando is_connected(): {e}")
            await asyncio.sleep(interval_sec)
            continue

        now = datetime.utcnow()

        if connected != last_state:
            duration_s = ((now - last_change).total_seconds()
                          if last_state is not None else None)
            label = "CONECTADO" if connected else "DESCONECTADO"
            extra = f" (estado anterior duró {duration_s:.1f}s)" if duration_s else ""
            print(f"[Telegram Monitor] {label}{extra}")
            journal.event("bot", "telegram_connection_change",
                          connected=connected,
                          previous_state=last_state,
                          previous_state_duration_sec=
                              round(duration_s, 1) if duration_s else None,
                          utc=now.isoformat(timespec="seconds"))
            # Reset del flag al reconectar → permite alertar la próxima caída
            if connected:
                alerted_disconnect = False
            last_state = connected
            last_change = now
            last_periodic_beat = now

        elif (now - last_periodic_beat).total_seconds() >= PERIODIC_BEAT_S:
            uptime_in_state = (now - last_change).total_seconds()
            journal.event("bot", "telegram_connection_beat",
                          connected=connected,
                          uptime_in_state_sec=round(uptime_in_state, 0),
                          utc=now.isoformat(timespec="seconds"))
            last_periodic_beat = now

        # Detector de disconnect SOSTENIDO (>5min): anomaly crítica para que
        # la falta de mensajes del canal no pase desapercibida.
        age_in_state = (now - last_change).total_seconds()
        if _should_alert_sustained_disconnect(
                connected, last_state, age_in_state,
                alerted_disconnect, DISCONNECT_ALERT_THRESHOLD_S):
            journal.anomaly("bot", "channel_msg", "critical",
                            f"Telegram desconectado >{DISCONNECT_ALERT_THRESHOLD_S//60}min "
                            f"— señales del canal acumulándose en el servidor",
                            disconnect_age_s=round(age_in_state, 1))
            alerted_disconnect = True

        await asyncio.sleep(interval_sec)


async def _mt5_connection_monitor(interval_sec: int = 10):
    """Vigila la conexión del terminal MT5 igual que su gemelo de Telethon.

    Antes: la caída de MT5 solo se detectaba cuando fallaba una operación
    (intentar modify/open). Gap silencioso entre la caída y el primer
    intento → posiciones potencialmente naked, sin gestión, sin aviso.

    Ahora: poll cada {interval_sec}s a terminal_info().connected, loguea
    cambios (mt5_connection_change), y emite anomaly crítica si lleva
    >60s desconectado. Umbral más bajo que Telegram porque MT5 down
    significa "no se puede ejecutar nada" — mucho más grave.
    """
    import MetaTrader5 as _mt5
    last_state = None
    last_change = datetime.utcnow()
    last_periodic_beat = datetime.utcnow()
    alerted_disconnect = False
    alerted_trade_disabled = False
    DISCONNECT_ALERT_THRESHOLD_S = 60   # 1 min — MT5 down es más grave
    PERIODIC_BEAT_S = 3600

    print(f"[MT5 Monitor] activo. Check cada {interval_sec}s, alerta "
          f"tras {DISCONNECT_ALERT_THRESHOLD_S}s desconectado.")

    while True:
        try:
            info = await asyncio.to_thread(_mt5.terminal_info)
            connected = bool(info and getattr(info, "connected", False))
            trade_allowed = getattr(info, "trade_allowed", None) if info else None
            tradeapi_disabled = (
                getattr(info, "tradeapi_disabled", None) if info else None
            )
        except Exception as e:
            print(f"[MT5 Monitor] error terminal_info: {e}")
            await asyncio.sleep(interval_sec)
            continue

        now = datetime.utcnow()

        if connected != last_state:
            duration_s = ((now - last_change).total_seconds()
                          if last_state is not None else None)
            label = "CONECTADO" if connected else "DESCONECTADO"
            extra = f" (estado anterior duró {duration_s:.1f}s)" if duration_s else ""
            print(f"[MT5 Monitor] {label}{extra}")
            journal.event("bot", "mt5_connection_change",
                          connected=connected,
                          previous_state=last_state,
                          trade_allowed=trade_allowed,
                          tradeapi_disabled=tradeapi_disabled,
                          previous_state_duration_sec=
                              round(duration_s, 1) if duration_s else None,
                          utc=now.isoformat(timespec="seconds"))
            if connected:
                alerted_disconnect = False
                from state import state
                open_count = _count_open_signals_unique(state)
                if _should_audit_mt5_reconnect(
                        connected, last_state, open_count):
                    journal.event("bot", "mt5_reconnect_audit_requested",
                                  open_signals=open_count,
                                  disconnected_duration_sec=
                                      round(duration_s, 1) if duration_s else None)
                    asyncio.create_task(_post_mt5_reconnect_audit(
                        round(duration_s, 1) if duration_s else None))
            last_state = connected
            last_change = now
            last_periodic_beat = now

        elif (now - last_periodic_beat).total_seconds() >= PERIODIC_BEAT_S:
            uptime = (now - last_change).total_seconds()
            journal.event("bot", "mt5_connection_beat",
                          connected=connected,
                          trade_allowed=trade_allowed,
                          tradeapi_disabled=tradeapi_disabled,
                          uptime_in_state_sec=round(uptime, 0),
                          utc=now.isoformat(timespec="seconds"))
            last_periodic_beat = now

        trade_disabled = (
            trade_allowed is False or tradeapi_disabled is True
        )
        if connected and not trade_disabled and alerted_trade_disabled:
            journal.event("bot", "mt5_trade_permission_restored",
                          trade_allowed=trade_allowed,
                          tradeapi_disabled=tradeapi_disabled,
                          utc=now.isoformat(timespec="seconds"))
            alerted_trade_disabled = False

        if _should_alert_mt5_trade_disabled(
                connected, trade_allowed, tradeapi_disabled,
                alerted_trade_disabled):
            journal.anomaly(
                "bot", "mt5", "critical",
                "MT5 conectado pero AutoTrading/API trading está desactivado "
                "— las órdenes serán rechazadas con retcode 10027",
                connected=connected,
                trade_allowed=trade_allowed,
                tradeapi_disabled=tradeapi_disabled,
                terminal_name=getattr(info, "name", None) if info else None,
                terminal_company=getattr(info, "company", None) if info else None,
                last_error=str(_mt5.last_error()),
            )
            alerted_trade_disabled = True

        age_in_state = (now - last_change).total_seconds()
        if _should_alert_sustained_disconnect(
                connected, last_state, age_in_state,
                alerted_disconnect, DISCONNECT_ALERT_THRESHOLD_S):
            journal.anomaly("bot", "mt5", "critical",
                            f"MT5 desconectado >{DISCONNECT_ALERT_THRESHOLD_S}s — "
                            f"operaciones del bot bloqueadas",
                            disconnect_age_s=round(age_in_state, 1))
            alerted_disconnect = True

        await asyncio.sleep(interval_sec)


def _resync_orphan_positions():
    """Recupera posiciones huérfanas en MT5 al arrancar el bot.

    Cuando el bot crashea o se reinicia, el state.py se pierde (in-memory).
    Pero las posiciones MT5 siguen abiertas con TPs/SL configurados.

    Esta función:
      1. Query MT5 por posiciones con nuestros magic numbers
      2. Las agrupa por signal_id (parsing comments c1_X / c2_X / DCA_cN_X)
      3. Reconstruye Signal skeleton mínimos en state
      4. Arranca dca_monitor para cada uno → auto-finalize detecta cierres

    Esto resuelve los ghost signals (señales que quedaban open forever
    porque el bot reinició entre apertura y cierre por TP/SL en MT5).
    """
    import dca_monitor
    from state import Signal, state

    try:
        groups = executor.list_open_positions_grouped()
    except Exception as e:
        print(f"[Resync] error consultando MT5: {e}")
        return

    if not groups:
        print("[Resync] sin posiciones huérfanas en MT5. OK.")
        return

    from datetime import datetime, timezone, timedelta
    import config

    # ── Offset hora servidor MT5 → UTC (fix 2026-05-16) ──────────────────
    # MT5 position.time viene en hora del SERVIDOR del broker (Vantage =
    # UTC+2/+3 segun DST), NO en UTC. Antes el resync lo trataba como UTC
    # directo → el timestamp quedaba +2/+3h en el FUTURO → elapsed negativo
    # → time-stop roto. Caso real canal2_12497 (15-may): elapsed_min=-177.
    # Calculamos el offset comparando el tick actual del servidor con UTC.
    server_offset_h = 0
    try:
        _tick = mt5.symbol_info_tick(config.MT5_SYMBOL)
        if _tick and _tick.time:
            _srv_now = datetime.fromtimestamp(_tick.time, tz=timezone.utc).replace(tzinfo=None)
            server_offset_h = round((_srv_now - datetime.utcnow()).total_seconds() / 3600)
            print(f"[Resync] offset servidor MT5 detectado: UTC+{server_offset_h}h")
    except Exception as e:
        print(f"[Resync] no pude calcular offset servidor ({e}), asumo 0")

    print(f"[Resync] encontradas {len(groups)} señales huérfanas en MT5:")
    for sig_id, g in groups.items():
        # Reconstruir timestamp real de apertura desde MT5 (no datetime.utcnow,
        # que reseteaba el reloj y rompía el time-stop). Sin esto, una posición
        # abierta hace 2h se quedaba con timestamp=ahora y nunca disparaba el
        # time-stop a 60min (visto en sesión 2026-05-06, canal2_12161).
        # El offset servidor se RESTA para obtener UTC real.
        if g.get("market_open_time"):
            opened_at = (datetime.fromtimestamp(
                g["market_open_time"], tz=timezone.utc
            ).replace(tzinfo=None) - timedelta(hours=server_offset_h))
            # Sanity: si el timestamp queda en el futuro (offset mal calculado
            # o reloj raro), fallback a utcnow para no romper el time-stop.
            if opened_at > datetime.utcnow():
                print(f"[Resync] ⚠ {sig_id}: opened_at futuro tras offset "
                      f"({opened_at}) → fallback a utcnow")
                opened_at = datetime.utcnow()
        else:
            opened_at = datetime.utcnow()

        # Re-aplicar defensas según canal: time-stop notify y BE auto.
        # Sin esto, las posiciones huérfanas quedaban sin time-stop ni BE
        # tras un restart, expuestas indefinidamente.
        if g["channel"] == "canal2":
            ts_min = config.STRATEGY_C2_TIME_STOP_MIN
            be_idx_cfg = config.STRATEGY_C2_BE_TP_INDEX
        else:
            ts_min = config.STRATEGY_C1_TIME_STOP_MIN
            be_idx_cfg = config.STRATEGY_C1_BE_TP_INDEX
        time_stop_at = (opened_at + timedelta(minutes=ts_min)) if ts_min > 0 else None
        be_at_tp_index = be_idx_cfg if be_idx_cfg >= 0 else None

        extra_markets = list(g.get("extra_market_tickets", []))
        sig = Signal(
            channel=g["channel"],
            message_id=g["message_id"],
            direction=g["direction"],
            timestamp=opened_at,
            market_ticket=g["market_ticket"],
            market_fill_price=g["market_price"],
            sl=g["market_sl"],
            tps=[g["market_tp"]] if g["market_tp"] else [],
            dca_tickets=list(g["dca_tickets"]),
            extra_market_tickets=extra_markets,
            time_stop_at=time_stop_at,
            be_at_tp_index=be_at_tp_index,
        )
        # Reconstruir tp_overrides del Market B (doble market): el Market B
        # cierra en TP3 (STRATEGY_DOUBLE_MARKET_TP_INDEX). Sin esto, tras el
        # resync el Market B se quedaba sin override (canal2_12497 lo perdio
        # entero — fix 2026-05-16).
        for tk in extra_markets:
            sig.tp_overrides[tk] = config.STRATEGY_DOUBLE_MARKET_TP_INDEX
        sig.dca_placed = True  # ya están abiertos, no abrir más
        sig.status = "open"
        state.add(sig)
        elapsed_min = (datetime.utcnow() - opened_at).total_seconds() / 60
        print(f"  • {sig_id}: {sig.direction} entry={sig.market_fill_price} "
              f"market={sig.market_ticket} marketB={len(extra_markets)} "
              f"dcas={len(sig.dca_tickets)} "
              f"opened={opened_at.strftime('%H:%M:%S')} "
              f"({elapsed_min:.0f}min ago) time_stop={time_stop_at} "
              f"be_idx={be_at_tp_index}")
        # Arranca monitor solo para que auto-finalize detecte cuando MT5 cierre
        # las posiciones. Sin niveles DCA pendientes (ya están todos abiertos).
        try:
            dca_monitor.start(sig, [])
        except Exception as e:
            print(f"  ! error arrancando monitor para {sig_id}: {e}")

        # Journal: registrar el resync para análisis posterior
        try:
            journal.event(sig_id, "signal_resync_from_mt5",
                          market_ticket=sig.market_ticket,
                          extra_market_tickets=extra_markets,
                          market_price=sig.market_fill_price,
                          n_dcas=len(sig.dca_tickets),
                          sl=sig.sl,
                          tp=sig.tps[0] if sig.tps else None,
                          opened_at=opened_at.isoformat(timespec="seconds"),
                          elapsed_min=round(elapsed_min, 1),
                          time_stop_at=time_stop_at.isoformat(timespec="seconds")
                          if time_stop_at else None,
                          be_at_tp_index=be_at_tp_index)
        except Exception:
            pass


def _finalize_journal_orphans():
    """Finaliza señales HUERFANAS del journal usando el historial de MT5.

    Causa raiz (auditoria 2026-05-16): si el bot se reinicia y las
    posiciones de una senal YA cerraron en MT5, _resync_orphan_positions
    no las recupera (solo reconstruye posiciones ABIERTAS). La senal queda
    'signal_received' sin 'signal_closed' para siempre — el journal
    descuadra la contabilidad.

    Esta funcion (corre al startup, despues del resync):
      1. Lee el journal, detecta huerfanos recientes (received sin closed).
      2. Consulta MT5 history_deals.
      3. Si todas las posiciones del huerfano cerraron en MT5 → emite el
         signal_closed retroactivo con el P&L REAL de MT5.

    Solo mira los ultimos 7 dias — huerfanos mas viejos no afectan a la
    operativa actual.
    """
    import json as _json
    import re as _re
    from collections import defaultdict
    from datetime import datetime, timezone, timedelta
    from pathlib import Path

    events_file = Path(__file__).parent / "data" / "trade_events.jsonl"
    if not events_file.exists():
        return

    # 1. Detectar huerfanos del journal
    received = {}
    closed = set()
    try:
        for line in events_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            e = _json.loads(line)
            ev, sid = e.get("ev"), e.get("sig")
            if ev == "signal_received":
                received[sid] = e.get("ts", "")
            elif ev == "signal_closed":
                closed.add(sid)
    except Exception as e:
        print(f"[OrphanFinalizer] error leyendo journal: {e}")
        return

    cutoff_iso = (datetime.utcnow() - timedelta(days=7)).isoformat()
    orphans = {s: t for s, t in received.items()
               if s not in closed and s and s.startswith("canal")
               and t >= cutoff_iso}
    if not orphans:
        print("[OrphanFinalizer] sin huerfanos recientes en el journal. OK.")
        return

    print(f"[OrphanFinalizer] {len(orphans)} huerfanos recientes en journal: "
          f"{sorted(orphans.keys())}")

    # 2. Consultar MT5 history
    t_from = datetime.now(timezone.utc) - timedelta(days=7)
    t_to = datetime.now(timezone.utc) + timedelta(hours=1)
    try:
        deals = mt5.history_deals_get(t_from, t_to) or []
    except Exception as e:
        print(f"[OrphanFinalizer] error consultando MT5: {e}")
        return

    rx = _re.compile(r"(?:DCA_)?c([12])_(\d+)")
    pos_deals = defaultdict(list)
    for d in deals:
        pos_deals[d.position_id].append(d)
    pos_to_sig = {}
    for pid, dl in pos_deals.items():
        for d in dl:
            m = rx.match(d.comment or "")
            if m:
                pos_to_sig[pid] = f"canal{m.group(1)}_{m.group(2)}"
                break

    # P&L y estado por señal
    sig_pnl = defaultdict(float)
    sig_has_open = defaultdict(bool)
    sig_has_deals = set()
    for pid, dl in pos_deals.items():
        sig = pos_to_sig.get(pid)
        if not sig:
            continue
        sig_has_deals.add(sig)
        if any(d.entry == 1 for d in dl):
            sig_pnl[sig] += sum(d.profit for d in dl if d.entry == 1)
        else:
            sig_has_open[sig] = True

    # 3. Finalizar los huerfanos cuyas posiciones cerraron TODAS en MT5
    n_fixed = 0
    for sid in orphans:
        if sid not in sig_has_deals:
            # Nunca abrio posicion (ej. market_fill_failed) — no finalizar
            continue
        if sig_has_open.get(sid):
            # Aun tiene posiciones abiertas — el resync/reconciler la maneja
            continue
        pnl = round(sig_pnl.get(sid, 0.0), 2)
        try:
            journal.event(sid, "journal_orphan_finalized",
                          total_pl=pnl, source="mt5_history_startup")
            journal.event(sid, "signal_closed",
                          tag="ORPHAN_RECONCILED", total_pl=pnl)
            print(f"  • {sid}: finalizado retroactivo P&L=${pnl:+.2f} "
                  f"(cierre detectado en MT5 history)")
            n_fixed += 1
        except Exception as e:
            print(f"  ! error finalizando {sid}: {e}")

    print(f"[OrphanFinalizer] {n_fixed} huerfanos finalizados con P&L de MT5.")


def _git_info() -> dict:
    """Devuelve info git de la sesión actual (commit, branch, dirty).
    Best-effort: si git falla devuelve None en cada campo. Va al evento
    session_started para que cada trade del ledger pueda saber qué
    versión del código lo ejecutó (rollup en reconcile.py)."""
    import subprocess

    def _run(args):
        try:
            return subprocess.check_output(
                args, cwd=Path(__file__).parent,
                stderr=subprocess.DEVNULL, text=True).strip()
        except Exception:
            return None

    commit = _run(["git", "rev-parse", "--short", "HEAD"])
    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    dirty_out = _run(["git", "status", "--porcelain"])
    dirty = bool(dirty_out) if dirty_out is not None else None
    return {"git_commit": commit, "git_branch": branch, "git_dirty": dirty}


async def main():
    print("=" * 60)
    print("  Telegram Signal Copier")
    print("=" * 60)

    journal.set_notify_loop(asyncio.get_running_loop())

    # Marca de sesión — versión del código que ejecuta este arranque.
    # El reconcile asocia cada trade con el session_started cuya ventana
    # lo cubre → cada fila del ledger carga su `bot_version`.
    journal.event("bot", "session_started", **_git_info(),
                  started_utc=datetime.utcnow().isoformat(timespec="seconds"))

    # Validar configuración mínima
    if not config.CANAL1_BUY_STICKER_ID or not config.CANAL1_SELL_STICKER_ID:
        print("\n⚠  AVISO: IDs de stickers de Canal 1 no configurados.")
        print("   El bot abrirá mercado solo cuando reconozca el sticker.")
        print("   Cuando llegue un sticker nuevo, el ID aparecerá en consola.\n")

    # Conectar MT5
    if not executor.init():
        print("[ERROR] No se puede conectar a MT5. Asegúrate de que el terminal está abierto.")
        sys.exit(1)

    # Resync posiciones huérfanas: si el bot reinició dejando posiciones
    # abiertas en MT5, las recoge para que auto-finalize las trackee.
    _resync_orphan_positions()

    # Finaliza huerfanos del journal: senales que cerraron en MT5 mientras
    # el bot no las trackeaba (reinicio + posiciones ya cerradas). Registra
    # el signal_closed retroactivo con el P&L real — sin esto el journal
    # descuadra la contabilidad (auditoria 2026-05-16).
    _finalize_journal_orphans()

    # Iniciar Telethon
    await client.start(phone=config.TELEGRAM_PHONE)
    me = await client.get_me()
    print(f"\n[Telegram] Conectado como: {me.first_name} (@{me.username})")
    print(f"[Telegram] Escuchando Canal 1 (ID={config.CANAL_1_ID})")
    print(f"[Telegram] Escuchando Canal 2 (ID={config.CANAL_2_ID})")
    print("\n  Bot activo. Ctrl+C para detener.\n")

    asyncio.ensure_future(_runtime_heartbeat())
    asyncio.ensure_future(_heartbeat())
    asyncio.ensure_future(_telegram_connection_monitor())
    asyncio.ensure_future(_mt5_connection_monitor())
    # Watchdog: notify URGENT si una signal abierta lleva >2min sin TPs/SL
    # aplicados. Defensa de ultimo recurso para casos como canal1_19484
    # y canal1_19498 (sesion 2026-05-07) donde el flujo normal fallo y
    # las posiciones quedaron NAKED en MT5 sin que el bot avisara.
    asyncio.ensure_future(_naked_signal_watchdog())
    asyncio.ensure_future(_pending_correction_watchdog())
    asyncio.ensure_future(_position_reconciler())
    live_auditor.start()
    # Poller activo: bypass del updateChannelTooLong de Telethon.
    # Entrega mensajes y edits de Canal 2 en ~1s en lugar de 60-600s.
    asyncio.ensure_future(poll_loop())

    try:
        await _run_until_disconnected_with_backoff()
    finally:
        journal.event("bot", "session_closed",
                      ended_utc=datetime.utcnow().isoformat(timespec="seconds"))
        journal.set_notify_loop(None)
        executor.shutdown()
        print("\nBot detenido.")


if __name__ == "__main__":
    asyncio.run(main())
