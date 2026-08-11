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
import math
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

import causal_trace
import broker_money
import config
import executor
import journal
import live_basket_guard
import live_auditor
import pending_actions
from tools import capture_broker_money_contract as broker_contract
from listener import (
    _is_transient_telegram_history_error,
    canal2_zone_touch_loop,
    client,
    notify,
    poll_loop_supervised,
    restore_canal2_zone_plans_from_journal,
)
from parser import canal2_entry_command_key, predict_sl_from_entry
from state import state

_freeze_traceback_file_handle = None
_freeze_traceback_file_path: Path | None = None
_TELEGRAM_RUN_BACKOFF_BASE_S = 15.0
_TELEGRAM_RUN_BACKOFF_MAX_S = 120.0
_last_broker_contract_error: str | None = None
_broker_contract_ready: bool | None = None


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


async def _heartbeat(interval_sec: float | None = None):
    """Escribe un evento 'heartbeat' cada N segundos al journal.
    Si el bot se cae en silencio, el log mostrará exactamente cuándo paró."""
    interval = (
        float(interval_sec)
        if interval_sec is not None
        else float(config.BOT_JOURNAL_HEARTBEAT_SEC)
    )
    interval = max(1.0, interval)
    await asyncio.sleep(interval)   # primer beat tras arrancar
    while True:
        from state import state
        open_signals = _count_open_signals_unique(state)
        journal.event("bot", "heartbeat",
                      open_signals=open_signals,
                      utc=datetime.utcnow().isoformat(timespec="seconds"))
        await asyncio.sleep(interval)


def _write_runtime_heartbeat(path: Path | None = None) -> None:
    path = path or Path(config.BOT_RUNTIME_HEARTBEAT_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 2,
        "pid": os.getpid(),
        "utc": datetime.utcnow().isoformat(timespec="milliseconds"),
        **_runtime_exposure_snapshot(),
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


def _runtime_exposure_snapshot(state_manager=None, positions_get=None) -> dict:
    """Return the fail-closed exposure contract consumed by the watcher."""
    state_manager = state if state_manager is None else state_manager
    try:
        open_signal_count = _count_open_signals_unique(state_manager)
    except Exception:
        open_signal_count = None

    if positions_get is None:
        try:
            import MetaTrader5 as mt5
            positions_get = mt5.positions_get
        except Exception:
            positions_get = None

    bot_position_count = None
    if positions_get is not None:
        try:
            positions = positions_get()
            if positions is not None:
                bot_magics = {
                    int(config.magic_for("canal1")),
                    int(config.magic_for("canal2")),
                }
                bot_position_count = sum(
                    1 for position in positions
                    if int(getattr(position, "magic", -1)) in bot_magics
                )
        except Exception:
            bot_position_count = None

    if ((open_signal_count is not None and open_signal_count > 0)
            or (bot_position_count is not None
                and bot_position_count > 0)):
        exposure_state = "open"
    elif open_signal_count is None or bot_position_count is None:
        exposure_state = "unknown"
    else:
        exposure_state = "flat"

    return {
        "exposure_state": exposure_state,
        "bot_position_count": bot_position_count,
        "open_signal_count": open_signal_count,
    }


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
            await asyncio.to_thread(_write_runtime_heartbeat)
            _arm_freeze_traceback_dump()
        except Exception as e:
            print(f"[Heartbeat] runtime heartbeat write failed: {e}")
        await asyncio.sleep(interval)


def _capture_broker_money_contract_snapshot(
    *,
    path: Path | None = None,
    events_path: Path | None = None,
    force: bool = False,
) -> str | None:
    output = path or Path(config.BOT_BROKER_MONEY_CONTRACT_FILE)
    contract = broker_contract.build_contract(
        executor.mt5,
        instrument_symbol=config.MT5_SYMBOL,
    )
    blockers = broker_money.validate_contract_metadata(contract)
    if blockers:
        raise RuntimeError(
            "invalid broker money contract: " + ",".join(blockers)
        )

    account = contract["account"]
    instrument = contract["instrument"]
    previous_snapshots: list[dict] = []
    existing: dict | None = None
    output_exists = output.is_file()
    if output_exists:
        existing = json.loads(output.read_text(encoding="utf-8"))
        if not isinstance(existing, dict):
            raise ValueError("broker money contract must be an object")
        existing_account = existing.get("account") or {}
        existing_instrument = existing.get("instrument") or {}
        if (
            existing_account.get("server") == account.get("server")
            and existing_account.get("fingerprint")
            == account.get("fingerprint")
            and existing_instrument.get("symbol")
            == instrument.get("symbol")
        ):
            previous_snapshots = list(
                existing.get("swap_snapshots") or []
            )
    if force or not output_exists:
        previous_snapshots = broker_contract.merge_swap_snapshots(
            previous_snapshots,
            broker_contract.load_event_snapshots(
                events_path or Path(journal.EVENTS_FILE),
                account_server=account["server"],
                account_fingerprint=account["fingerprint"],
                instrument_symbol=instrument["symbol"],
            ),
        )

    current = contract["swap_snapshots"][-1]
    previous = previous_snapshots[-1] if previous_snapshots else None
    reason = (
        "startup"
        if force
        else broker_contract.snapshot_record_reason(current, previous)
    )
    if reason is None:
        contract["swap_snapshots"] = previous_snapshots
        blockers = broker_money.validate_contract_metadata(contract)
        if blockers:
            raise RuntimeError(
                "invalid broker money snapshot history: "
                + ",".join(blockers)
            )
        if (
            existing is None
            or broker_money.validate_contract_metadata(existing)
        ):
            broker_contract.write_contract(contract, output)
        return None

    contract["swap_snapshots"] = broker_contract.merge_swap_snapshots(
        previous_snapshots,
        [current],
    )
    blockers = broker_money.validate_contract_metadata(contract)
    if blockers:
        raise RuntimeError(
            "invalid broker money snapshot history: " + ",".join(blockers)
        )
    receipt = journal.event(
        "bot",
        "broker_money_contract_snapshot",
        record_reason=reason,
        snapshot=current,
    )
    if not journal.confirm_event(receipt, timeout=10.0):
        raise RuntimeError("broker money snapshot journal write failed")
    broker_contract.write_contract(contract, output)
    return reason


def _try_capture_broker_money_contract_snapshot(
    *,
    path: Path | None = None,
    force: bool = False,
) -> bool:
    global _last_broker_contract_error, _broker_contract_ready
    try:
        _capture_broker_money_contract_snapshot(path=path, force=force)
        if _last_broker_contract_error is not None:
            receipt = journal.event(
                "bot",
                "broker_money_contract_capture_recovered",
                previous_error=_last_broker_contract_error,
            )
            if not journal.confirm_event(receipt, timeout=10.0):
                raise RuntimeError(
                    "broker money recovery journal write failed"
                )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        if error != _last_broker_contract_error:
            journal.anomaly(
                "bot",
                "mt5",
                "warning",
                f"captura de contrato monetario fallo: {error}",
            )
        _last_broker_contract_error = error
        _broker_contract_ready = False
        return False
    _last_broker_contract_error = None
    _broker_contract_ready = True
    return True


async def _broker_money_contract_monitor(
    interval_sec: float | None = None,
) -> None:
    interval = (
        float(interval_sec)
        if interval_sec is not None
        else float(config.BOT_BROKER_CONTRACT_POLL_SEC)
    )
    interval = max(30.0, interval)
    previous_ready = _broker_contract_ready
    while True:
        await asyncio.sleep(interval)
        current_ready = await asyncio.to_thread(
            _try_capture_broker_money_contract_snapshot
        )
        if previous_ready is True and current_ready is False:
            await _notify_broker_contract_status(
                "REGISTRO DE SIMULACION INTERRUMPIDO\n"
                "El bot sigue operando. Las operaciones nocturnas podrian "
                "quedar sin coste exacto hasta que se recupere."
            )
        elif previous_ready is False and current_ready is True:
            await _notify_broker_contract_status(
                "REGISTRO DE SIMULACION RECUPERADO\n"
                "La captura monetaria de MT5 vuelve a estar activa."
            )
        previous_ready = current_ready


async def _notify_broker_contract_status(text: str) -> None:
    try:
        await notify(text)
    except Exception as exc:
        journal.event(
            "bot",
            "broker_money_contract_status_notify_failed",
            error=f"{type(exc).__name__}: {exc}",
        )


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

    Problema: el auto-finalize del position_lifecycle_monitor solo dispara con n_open==0
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
    PERIODIC_BEAT_S = float(config.BOT_CONNECTION_BEAT_SEC)

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
    PERIODIC_BEAT_S = float(config.BOT_CONNECTION_BEAT_SEC)

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


def _load_resync_entry_metadata(path, signal_ids) -> dict[str, dict]:
    """Recover durable Telegram entry identity for live MT5 positions."""
    from datetime import timezone

    targets = {str(signal_id) for signal_id in signal_ids}
    source = Path(path)
    if not targets or not source.exists():
        return {}

    recovered: dict[str, dict] = {}

    def parse_telegram_timestamp(value):
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed

    with source.open("rb") as handle:
        for raw_line in handle:
            try:
                row = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(row, dict):
                continue
            sig_id = str(row.get("sig") or "")
            if sig_id not in targets or not sig_id.startswith("canal2_"):
                continue

            event = row.get("ev")
            if event == "canal2_duplicate_alias_registered":
                try:
                    alias_message_id = int(row.get("alias_message_id"))
                except (TypeError, ValueError):
                    continue
                metadata = recovered.setdefault(sig_id, {})
                aliases = metadata.setdefault(
                    "telegram_alias_message_ids",
                    [],
                )
                if alias_message_id not in aliases:
                    aliases.append(alias_message_id)
                continue

            if event == "telegram_raw":
                command_key = canal2_entry_command_key(
                    str(row.get("text") or "")
                )
                if command_key is None:
                    continue
                reply_to = row.get("reply_to_msg_id")
                metadata = recovered.setdefault(sig_id, {})
                metadata.update({
                    "telegram_entry_command_key": command_key,
                    "telegram_entry_was_reply": bool(
                        row.get("is_reply") or reply_to is not None
                    ),
                    "telegram_entry_reply_to_message_id": (
                        int(reply_to) if reply_to is not None else None
                    ),
                })
                telegram_ts = parse_telegram_timestamp(row.get("date_utc"))
                if telegram_ts is not None:
                    metadata["telegram_entry_timestamp"] = telegram_ts
                continue

            if event != "signal_received":
                continue
            command_key = (
                row.get("telegram_entry_command_key")
                or canal2_entry_command_key(str(row.get("raw_text") or ""))
            )
            if command_key is None:
                continue
            metadata = recovered.setdefault(sig_id, {})
            metadata["telegram_entry_command_key"] = str(command_key)
            if "telegram_entry_was_reply" in row:
                metadata["telegram_entry_was_reply"] = bool(
                    row["telegram_entry_was_reply"]
                )
            if "telegram_entry_reply_to_message_id" in row:
                reply_to = row.get("telegram_entry_reply_to_message_id")
                metadata["telegram_entry_reply_to_message_id"] = (
                    int(reply_to) if reply_to is not None else None
                )
            telegram_ts = parse_telegram_timestamp(
                row.get("telegram_entry_ts_utc") or row.get("tg_ts")
            )
            if telegram_ts is not None:
                metadata["telegram_entry_timestamp"] = telegram_ts

    return recovered


def _resync_orphan_positions():
    """Recupera posiciones huérfanas en MT5 al arrancar el bot.

    Cuando el bot crashea o se reinicia, el state.py se pierde (in-memory).
    Pero las posiciones MT5 siguen abiertas con TPs/SL configurados.

    Esta función:
      1. Query MT5 por posiciones con nuestros magic numbers
      2. Las agrupa por signal_id (parsing comments c1_X / c2_X / DCA_cN_X)
      3. Reconstruye Signal skeleton mínimos en state
      4. Arranca position_lifecycle_monitor para cada uno → auto-finalize detecta cierres

    Esto resuelve los ghost signals (señales que quedaban open forever
    porque el bot reinició entre apertura y cierre por TP/SL en MT5).
    """
    import position_lifecycle_monitor
    from state import Signal, state

    try:
        groups = executor.list_open_positions_grouped()
    except Exception as e:
        print(f"[Resync] error consultando MT5: {e}")
        return

    if not groups:
        print("[Resync] sin posiciones huérfanas en MT5. OK.")
        return

    entry_metadata = _load_resync_entry_metadata(
        journal.EVENTS_FILE,
        groups.keys(),
    )
    try:
        basket_guard_states = live_basket_guard.load_guard_states(
            journal.EVENTS_FILE,
            groups.keys(),
        )
    except OSError as exc:
        basket_guard_states = {}
        print(f"[Resync] no pude cargar proteccion de cestas: {exc}")
    try:
        causal_origins, causal_conflicts, invalid_causal_lines = (
            causal_trace.load_signal_origin_index(journal.EVENTS_FILE)
        )
    except OSError as exc:
        causal_origins = {}
        causal_conflicts = {}
        invalid_causal_lines = []
        print(f"[Resync] no pude cargar origen causal: {exc}")
    if causal_conflicts or invalid_causal_lines:
        journal.event(
            "bot",
            "causal_origin_index_degraded",
            conflicting_signals=sorted(causal_conflicts),
            invalid_jsonl_lines=invalid_causal_lines[:100],
            invalid_jsonl_line_count=len(invalid_causal_lines),
        )

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
        _tick = executor.mt5.symbol_info_tick(config.MT5_SYMBOL)
        if _tick and _tick.time:
            _srv_now = datetime.fromtimestamp(_tick.time, tz=timezone.utc).replace(tzinfo=None)
            server_offset_h = round((_srv_now - datetime.utcnow()).total_seconds() / 3600)
            print(f"[Resync] offset servidor MT5 detectado: UTC+{server_offset_h}h")
    except Exception as e:
        print(f"[Resync] no pude calcular offset servidor ({e}), asumo 0")

    print(f"[Resync] encontradas {len(groups)} señales huérfanas en MT5:")
    for sig_id, g in groups.items():
        causal_origin = causal_origins.get(sig_id, {})
        entry_identity = entry_metadata.get(sig_id, {})
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
            source_message_revision_id=causal_origin.get(
                "message_revision_id"
            ),
            source_decision_id=causal_origin.get("decision_id"),
            telegram_entry_command_key=entry_identity.get(
                "telegram_entry_command_key"
            ),
            telegram_entry_was_reply=bool(
                entry_identity.get("telegram_entry_was_reply", False)
            ),
            telegram_entry_reply_to_message_id=entry_identity.get(
                "telegram_entry_reply_to_message_id"
            ),
            telegram_entry_timestamp=entry_identity.get(
                "telegram_entry_timestamp"
            ),
        )
        recovered_guard = basket_guard_states.get(sig_id)
        if recovered_guard is not None:
            sig.basket_guard_armed = recovered_guard.armed
            sig.basket_guard_triggered = recovered_guard.triggered
            sig.basket_guard_peak_pl = recovered_guard.peak_pl
            sig.basket_guard_trigger_reason = recovered_guard.trigger_reason
            sig.basket_guard_recovery_pending = (
                recovered_guard.recovery_pending
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
        for alias_message_id in entry_identity.get(
            "telegram_alias_message_ids",
            [],
        ):
            state.alias(sig, int(alias_message_id))
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
            position_lifecycle_monitor.start(sig, [])
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
                          telegram_alias_message_ids=entry_identity.get(
                              "telegram_alias_message_ids",
                              [],
                          ),
                          time_stop_at=time_stop_at.isoformat(timespec="seconds")
                          if time_stop_at else None,
                          be_at_tp_index=be_at_tp_index,
                          causal_origin_status=(
                              "conflict"
                              if sig_id in causal_conflicts
                              else "restored"
                              if causal_origin
                              else "missing"
                          ),
                          message_revision_id=(
                              sig.source_message_revision_id
                          ),
                          decision_id=sig.source_decision_id)
        except Exception:
            pass


def _orphan_history_query_end(now_utc=None):
    """Future-safe bound for MT5 histories stored in broker-server time."""
    from datetime import datetime, timezone, timedelta

    now_utc = now_utc or datetime.now(timezone.utc)
    return now_utc + timedelta(days=1)


def _closed_position_ids(deals) -> set[int]:
    closed = set()
    for deal in deals or ():
        if getattr(deal, "entry", None) not in (1, 3):
            continue
        position_id = getattr(deal, "position_id", None)
        if position_id is not None:
            closed.add(int(position_id))
    return closed


def _fetch_orphan_deals_synced(
    t_from,
    t_to,
    expected_position_ids: set[int],
    *,
    history_get=None,
    sleep_fn=None,
    retries: int = 10,
    pause_s: float = 1.0,
):
    """Wait until expected closes are present and history is stable."""
    import time

    if history_get is None:
        import MetaTrader5 as _mt5

        history_get = _mt5.history_deals_get
    sleep_fn = sleep_fn or time.sleep
    expected = {int(ticket) for ticket in expected_position_ids}
    deals = tuple(history_get(t_from, t_to) or ())
    previous_signature = None
    for attempt in range(max(1, retries)):
        signature = tuple(sorted(
            (
                getattr(deal, "ticket", None),
                getattr(deal, "position_id", None),
                getattr(deal, "entry", None),
                getattr(deal, "time_msc", None),
            )
            for deal in deals
        ))
        expected_closed = expected.issubset(_closed_position_ids(deals))
        if expected_closed and signature == previous_signature:
            return deals
        previous_signature = signature
        if attempt + 1 >= max(1, retries):
            break
        sleep_fn(pause_s)
        deals = tuple(history_get(t_from, t_to) or ())
    missing = sorted(expected - _closed_position_ids(deals))
    if missing:
        print(
            "[OrphanFinalizer] historial MT5 aun incompleto; "
            f"faltan cierres de tickets {missing}"
        )
    return deals


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

    events_file = journal.EVENTS_FILE
    if not events_file.exists():
        return

    # 1. Detectar huerfanos del journal
    received = {}
    closed = set()
    filled_tickets = defaultdict(set)
    fill_events = {
        "market_filled",
        "market_b_filled",
        "scale_out_leg_filled",
        "dca_filled",
        "rescue_market_filled",
    }
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
            elif ev in fill_events and e.get("ticket") is not None:
                try:
                    filled_tickets[sid].add(int(e["ticket"]))
                except (TypeError, ValueError):
                    pass
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
    t_to = _orphan_history_query_end()
    expected_tickets = {
        ticket
        for sid in orphans
        for ticket in filled_tickets.get(sid, set())
    }
    try:
        deals = _fetch_orphan_deals_synced(
            t_from,
            t_to,
            expected_tickets,
        )
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
    sig_closures = defaultdict(list)
    sig_close_times = defaultdict(list)
    for pid, dl in pos_deals.items():
        sig = pos_to_sig.get(pid)
        if not sig:
            continue
        sig_has_deals.add(sig)
        exits = [d for d in dl if getattr(d, "entry", None) in (1, 3)]
        if not exits:
            sig_has_open[sig] = True
            continue
        position_pnl = sum(
            float(getattr(d, field, 0.0) or 0.0)
            for d in dl
            for field in ("profit", "commission", "swap", "fee")
        )
        sig_pnl[sig] += position_pnl
        close_deal = max(
            exits,
            key=lambda d: (
                getattr(d, "time_msc", 0) or 0,
                getattr(d, "time", 0) or 0,
            ),
        )
        from mt5_deal_reason import close_reason_from_deal
        broker_reason = close_reason_from_deal(close_deal)
        tag = {
            "tp": "TP",
            "sl": "SL",
            "be": "LOSS_BE",
            "bot_close": "BOT_CLOSE",
        }.get(broker_reason, "MT5_AUTO")
        sig_closures[sig].append({
            "ticket": int(pid),
            "exit_price": round(float(getattr(close_deal, "price", 0.0)), 3),
            "pnl": round(position_pnl, 2),
            "closed_by_tag": tag,
            "distance_to_tag": None,
            "broker_close_reason": broker_reason,
            "broker_deal_reason": getattr(close_deal, "reason", None),
            "classification_source": "startup_broker_history",
        })
        raw_close_msc = getattr(close_deal, "time_msc", None)
        if raw_close_msc is not None:
            sig_close_times[sig].append(float(raw_close_msc) / 1000.0)
        elif getattr(close_deal, "time", None) is not None:
            sig_close_times[sig].append(float(close_deal.time))

    server_offset_s = 0
    try:
        tick = executor.mt5.symbol_info_tick(config.MT5_SYMBOL)
        if tick and tick.time:
            server_now = datetime.fromtimestamp(tick.time, tz=timezone.utc)
            server_offset_s = round(
                (server_now - datetime.now(timezone.utc)).total_seconds()
                / 3600
            ) * 3600
    except Exception:
        pass

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
        closures = sig_closures.get(sid, [])
        tags = defaultdict(int)
        for closure in closures:
            tags[closure["closed_by_tag"]] += 1
        dominant_tag = (
            max(tags.items(), key=lambda item: item[1])[0]
            if tags else "MT5_AUTO"
        )
        close_epoch = max(sig_close_times.get(sid, [0]))
        close_dt = (
            datetime.fromtimestamp(
                close_epoch - server_offset_s,
                tz=timezone.utc,
            )
            if close_epoch else datetime.now(timezone.utc)
        )
        opened_dt = None
        try:
            opened_dt = datetime.fromisoformat(
                str(received[sid]).replace("Z", "+00:00")
            )
            if opened_dt.tzinfo is None:
                opened_dt = opened_dt.replace(tzinfo=timezone.utc)
            else:
                opened_dt = opened_dt.astimezone(timezone.utc)
        except (TypeError, ValueError):
            pass
        duration_s = (
            max(0.0, (close_dt - opened_dt).total_seconds())
            if opened_dt is not None else None
        )
        try:
            journal.event(
                sid,
                "positions_closed_by_mt5",
                closures=closures,
                summary_by_tag=dict(tags),
                recovery_source="startup_history",
            )
            journal.event(
                sid,
                "pos_summary",
                n_positions=len(closures),
                positions=[{
                    "ticket": item["ticket"],
                    "type": "recovered",
                    "tp_override_idx": None,
                    "pl": item["pnl"],
                    "close_price": item["exit_price"],
                } for item in closures],
                entry_mode="recovered",
                had_double_market=len(closures) > 1,
            )
            journal.event(sid, "journal_orphan_finalized",
                          total_pl=pnl, source="mt5_history_startup",
                          expected_tickets=sorted(filled_tickets.get(sid, set())),
                          recovered_tickets=sorted(
                              item["ticket"] for item in closures),
                          mt5_server_offset_s=server_offset_s)
            journal.finalize_trade(
                sid,
                closed_at_utc=close_dt.isoformat(timespec="milliseconds"),
                closed_by=dominant_tag,
                duration_sec=(
                    round(duration_s, 1) if duration_s is not None else None
                ),
                total_pnl_usd=pnl,
                n_tickets_opened=len(closures),
                notes="startup orphan recovery from synchronized MT5 history",
            )
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
    versión del código lo ejecutó (rollup en reconcile_mt5_ledger.py)."""
    import subprocess

    def _run(args):
        try:
            return subprocess.check_output(
                args, cwd=Path(__file__).parent,
                stderr=subprocess.DEVNULL, text=True, timeout=10).strip()
        except Exception:
            return None

    commit = _run(["git", "rev-parse", "--short", "HEAD"])
    remote_commit = _run(["git", "rev-parse", "--short", "origin/main"])
    commit_full = _run(["git", "rev-parse", "HEAD"])
    remote_commit_full = _run(["git", "rev-parse", "origin/main"])
    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    dirty_out = _run(["git", "status", "--porcelain"])
    dirty = bool(dirty_out) if dirty_out is not None else None
    synced = (
        commit_full is not None
        and commit_full == remote_commit_full
        and branch == "main"
        and dirty is False
    )
    return {
        "git_commit": commit,
        "git_remote_commit": remote_commit,
        "git_commit_full": commit_full,
        "git_remote_commit_full": remote_commit_full,
        "git_branch": branch,
        "git_dirty": dirty,
        "git_synced": synced,
    }


def _watcher_attestation_error(
    git_info: dict,
    expected_head: str | None,
    *,
    allow_remote_mismatch: bool = False,
) -> str | None:
    """Return why this process is not the exact build verified by the watcher."""
    if not expected_head:
        return "sin atestacion del supervisor"

    local_head = git_info.get("git_commit_full")
    remote_head = git_info.get("git_remote_commit_full")
    if local_head != expected_head:
        return (
            f"HEAD={(local_head or 'desconocido')[:8]} no coincide con "
            f"watcher={expected_head[:8]}"
        )
    if remote_head != expected_head and not allow_remote_mismatch:
        return (
            f"origin/main={(remote_head or 'desconocido')[:8]} no coincide "
            f"con watcher={expected_head[:8]}"
        )
    if git_info.get("git_branch") != "main":
        return f"rama={git_info.get('git_branch') or 'desconocida'}; se exige main"
    if git_info.get("git_dirty") is not False:
        return "el arbol de trabajo tiene cambios locales"
    return None


def _terminate_legacy_watcher_parent(parent=None) -> bool:
    """Stop an old watcher that cannot attest the child it just launched."""
    try:
        if parent is None:
            import psutil
            parent = psutil.Process(os.getppid())
        if not str(parent.name()).lower().startswith("python"):
            return False
        script_names = {
            str(arg).replace("\\", "/").lower().rsplit("/", 1)[-1]
            for arg in parent.cmdline()
        }
        if "run_bot_watch.py" not in script_names:
            return False
        parent.terminate()
        return True
    except Exception:
        return False


def _live_strategy_contract() -> dict:
    """Return the exact live policy deployed by this process."""
    guard = live_basket_guard.GuardPolicy(
        enabled=config.STRATEGY_C1_BASKET_GUARD_ENABLED,
        channel="canal1",
        loss_cap=config.STRATEGY_C1_BASKET_LOSS_CAP,
        profit_arm=config.STRATEGY_C1_BASKET_PROFIT_ARM,
        profit_lock=config.STRATEGY_C1_BASKET_PROFIT_LOCK,
    )
    max_lots = float(config.STRATEGY_MAX_PLANNED_LOTS_PER_SIGNAL)
    if (
        not math.isfinite(max_lots)
        or max_lots < max(0.01, round(float(config.LOT_SIZE), 2))
    ):
        raise ValueError(
            "STRATEGY_MAX_PLANNED_LOTS_PER_SIGNAL no permite ni una posicion"
        )
    return {
        "contract_schema_version": 1,
        "evidence_status": "forward_trial",
        "dubai": {
            "entry_mode": config.STRATEGY_C1_ENTRY_MODE,
            "num_entries": int(config.STRATEGY_C1_NUM_ENTRIES),
            "basket_guard": {
                "enabled": bool(guard.enabled),
                "loss_cap": float(guard.loss_cap),
                "profit_arm": float(guard.profit_arm),
                "profit_lock": float(guard.profit_lock),
                "poll_seconds": max(
                    0.1,
                    float(config.STRATEGY_C1_BASKET_GUARD_POLL_S),
                ),
                "money_source": (
                    "mt5_position_profit_account_currency"
                ),
            },
        },
        "gold": {
            "immediate_entry_mode": config.STRATEGY_C2_ENTRY_MODE,
            "num_entries": int(config.STRATEGY_C2_NUM_ENTRIES),
            "zone_first_touch_execution": bool(
                config.STRATEGY_C2_ZONE_FIRST_TOUCH_EXECUTION_ENABLED
            ),
            "zone_explicit_activation": True,
        },
        "risk": {
            "lot_per_position": float(config.LOT_SIZE),
            "max_planned_lots_per_signal": round(max_lots, 8),
            "exposure_cap_enforced": True,
            "volume_increased_by_trial": False,
        },
    }


def _live_strategy_summary(contract: dict) -> str:
    guard = contract["dubai"]["basket_guard"]
    gold = contract["gold"]
    risk = contract["risk"]
    guard_text = (
        f"ON <= {guard['loss_cap']:.2f}; arma +{guard['profit_arm']:.2f}; "
        f"asegura +{guard['profit_lock']:.2f}"
        if guard["enabled"]
        else "OFF"
    )
    zone_text = (
        "primer toque ejecuta"
        if gold["zone_first_touch_execution"]
        else "primer toque observa; solo Active ejecuta"
    )
    return (
        f"[Strategy] Dubai guard {guard_text} EUR | Gold zonas: "
        f"{zone_text} | max {risk['max_planned_lots_per_signal']:.2f} lot"
    )


def _publish_live_strategy_contract() -> dict:
    contract = _live_strategy_contract()
    journal.event("bot", "live_strategy_contract", **contract)
    print(_live_strategy_summary(contract))
    return contract


def _startup_status_message(
    git_info: dict,
    *,
    money_capture_ready: bool | None = None,
) -> str:
    """Build the concise production-version confirmation sent to the owner."""
    commit = git_info.get("git_commit") or "desconocida"
    branch = git_info.get("git_branch") or "desconocida"
    synced = (
        git_info.get("git_synced") is True
        and commit != "desconocida"
        and branch == "main"
        and git_info.get("git_dirty") is False
    )
    runtime_verified = (
        git_info.get("git_runtime_verified") is True
        and commit != "desconocida"
        and branch == "main"
        and git_info.get("git_dirty") is False
    )
    if synced:
        code_status = "limpio y sincronizado"
    elif runtime_verified:
        code_status = "verificado; datos pendientes de subir"
    else:
        code_status = "estado local sin verificar"
    lines = [
        "BOT ACTIVO",
        f"Version: {commit}",
        f"Rama: {branch}",
        f"Codigo: {code_status}",
    ]
    if not runtime_verified and git_info.get("git_verification_error"):
        lines.append(f"Motivo: {git_info['git_verification_error']}")
    lines.extend([
        "MT5: conectado",
        "Telegram: canales 1 y 2 activos",
        f"Dubai Investing: {config.CANAL_1_ID}",
        f"Gold Signals: {config.CANAL_2_ID}",
    ])
    if money_capture_ready is True:
        lines.append("Registro simulacion: activo")
    elif money_capture_ready is False:
        lines.extend([
            "Registro simulacion: INCOMPLETO",
            "El bot sigue operando; revisa el registro antes de simular.",
        ])
    return "\n".join(lines)


async def main():
    print("=" * 60)
    print("  Telegram Signal Copier")
    print("=" * 60)

    journal.set_notify_loop(asyncio.get_running_loop())

    # Only the watcher may authorize the exact, already-published build.
    # Run this before MT5 and Telegram so an unsafe process cannot trade.
    git_info = _git_info()
    expected_head = os.getenv("BOT_WATCHER_VERIFIED_HEAD")
    allow_remote_mismatch = os.getenv("BOT_WATCHER_RUNTIME_SAFE") == "1"
    verification_error = _watcher_attestation_error(
        git_info,
        expected_head,
        allow_remote_mismatch=allow_remote_mismatch,
    )
    git_info["git_verification_error"] = verification_error
    git_info["git_runtime_verified"] = verification_error is None
    git_info["git_synced"] = bool(
        git_info.get("git_synced") is True and verification_error is None
    )
    if verification_error:
        print(f"[Startup] ARRANQUE BLOQUEADO: {verification_error}")
        print("[Startup] Usa run_bot.bat; no ejecutes main.py directamente.")
        journal.event(
            "bot",
            "startup_blocked_unverified",
            **git_info,
            watcher_verified_head=expected_head,
            watcher_pid=os.getenv("BOT_WATCHER_PID"),
            started_utc=datetime.utcnow().isoformat(timespec="seconds"),
        )
        journal.flush_events(timeout=10.0)
        if not expected_head and _terminate_legacy_watcher_parent():
            print("[Startup] Watcher antiguo terminado para cortar el relanzamiento.")
        raise SystemExit(78)

    # Marca de sesión — versión del código que ejecuta este arranque.
    # El reconcile asocia cada trade con el session_started cuya ventana
    # lo cubre → cada fila del ledger carga su `bot_version`.
    journal.event("bot", "session_started", **git_info,
                  started_utc=datetime.utcnow().isoformat(timespec="seconds"))
    try:
        _publish_live_strategy_contract()
    except ValueError as exc:
        print(f"[Startup] ARRANQUE BLOQUEADO: estrategia invalida: {exc}")
        journal.event(
            "bot",
            "startup_blocked_invalid_strategy",
            error=str(exc),
        )
        journal.flush_events(timeout=10.0)
        raise SystemExit(78) from exc

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
    pending_actions.queue.restore_from_spool(state)
    restored_zone_plans = restore_canal2_zone_plans_from_journal(
        journal.EVENTS_FILE
    )
    print(
        f"[Resync] contextos vigentes de zonas Gold Signals: "
        f"{restored_zone_plans}"
    )

    # Finaliza huerfanos del journal: senales que cerraron en MT5 mientras
    # el bot no las trackeaba (reinicio + posiciones ya cerradas). Registra
    # el signal_closed retroactivo con el P&L real — sin esto el journal
    # descuadra la contabilidad (auditoria 2026-05-16).
    _finalize_journal_orphans()

    # Captura local y barata: no descarga ticks, no usa Git y no toca ordenes.
    # Se ejecuta fuera del loop y despues del resync para que una recuperacion
    # historica grande nunca retrase la proteccion de posiciones abiertas.
    money_capture_ready = await asyncio.to_thread(
        _try_capture_broker_money_contract_snapshot,
        force=True,
    )

    # Iniciar Telethon
    await client.start(phone=config.TELEGRAM_PHONE)
    me = await client.get_me()
    print(f"\n[Telegram] Conectado como: {me.first_name} (@{me.username})")
    print(f"[Telegram] Escuchando Canal 1 (ID={config.CANAL_1_ID})")
    print(f"[Telegram] Escuchando Canal 2 (ID={config.CANAL_2_ID})")
    print("\n  Bot activo. Ctrl+C para detener.\n")

    journal.event(
        "bot",
        "startup_version_confirmed",
        **git_info,
        mt5_connected=True,
        telegram_connected=True,
        channels=["canal1", "canal2"],
        channel_ids={
            "canal1": config.CANAL_1_ID,
            "canal2": config.CANAL_2_ID,
        },
        money_capture_ready=money_capture_ready,
    )
    await notify(_startup_status_message(
        git_info,
        money_capture_ready=money_capture_ready,
    ))

    asyncio.ensure_future(_runtime_heartbeat())
    asyncio.ensure_future(_heartbeat())
    asyncio.ensure_future(_broker_money_contract_monitor())
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
    asyncio.ensure_future(canal2_zone_touch_loop())
    # Poller activo: bypass del updateChannelTooLong de Telethon.
    # Entrega mensajes y edits de Canal 2 en ~1s en lugar de 60-600s.
    asyncio.ensure_future(poll_loop_supervised())

    try:
        await _run_until_disconnected_with_backoff()
    finally:
        journal.event("bot", "session_closed",
                      ended_utc=datetime.utcnow().isoformat(timespec="seconds"))
        if not journal.flush_events(timeout=10.0):
            print("[journal] ERROR: eventos pendientes al cerrar")
        journal.set_notify_loop(None)
        executor.shutdown()
        print("\nBot detenido.")


if __name__ == "__main__":
    asyncio.run(main())
