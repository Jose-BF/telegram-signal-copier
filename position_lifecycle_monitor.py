"""
Monitor de ciclo de vida de posiciones abiertas.
Escanea ticks en tiempo real para proteger y cerrar correctamente las
posiciones asociadas a una senal ya aceptada por el bot.

Implementa estos mecanismos defensivos:

  1. Legacy DCA fill: conserva compatibilidad con senales antiguas o
     recuperadas que todavia tengan niveles pendientes.

  2. BE move (validado IS/OOS 2026 para Canal 2):
     Cuando el precio toca el TP indicado por `signal.be_at_tp_index`,
     mueve el SL de TODAS las posiciones a su entry price respectivo.
     Reduce DD OOS de $1050 a $893 en intra_dca/4p TP4 (Gold Standard).

  3. TIME-STOP defensivo (point 7 del analisis original):
     Si la senal sigue abierta tras `signal.time_stop_at` UTC sin haber
     tocado SL ni TP, hay dos modos:

       a) STRATEGY_TIME_STOP_NOTIFY_ONLY=1 (default, semana de prueba):
          NOTIFICA al usuario por Telegram con un resumen de la situacion
          y una recomendacion contextual. NO cierra. El usuario decide.

       b) STRATEGY_TIME_STOP_NOTIFY_ONLY=0 (legacy):
          Cierra TODAS las posiciones acumuladas. Datos historicos:
          TIME-STOP 60min sobre 1492 senales 2025+ -> +$5.552 vs +$1.710.
"""

import asyncio
import time
from datetime import datetime

import MetaTrader5 as mt5

import causal_trace
import config
import executor
import live_basket_guard
import pending_actions
import strategies
from mt5_deal_reason import close_reason_from_deal
from state import Signal


# Batch E: si el monitor ve N ticks consecutivos None de mt5.symbol_info_tick,
# el broker o MT5 esta down. Antes giraba indefinidamente sin anomaly. Ahora
# tras alcanzar threshold emite warning una vez por episodio.
NULL_TICK_STREAK_THRESHOLD = 3000   # ~30s a 10ms por ciclo


def _basket_guard_policy() -> live_basket_guard.GuardPolicy:
    return live_basket_guard.GuardPolicy(
        enabled=config.STRATEGY_C1_BASKET_GUARD_ENABLED,
        channel="canal1",
        loss_cap=config.STRATEGY_C1_BASKET_LOSS_CAP,
        profit_arm=config.STRATEGY_C1_BASKET_PROFIT_ARM,
        profit_lock=config.STRATEGY_C1_BASKET_PROFIT_LOCK,
    )


def _basket_guard_enabled_for(signal: Signal) -> bool:
    return bool(
        config.STRATEGY_C1_BASKET_GUARD_ENABLED
        and signal.channel == "canal1"
    )


def _journal_event(signal_id: str, event: str, **fields) -> None:
    import journal
    journal.event(signal_id, event, **fields)


def _guard_state(signal: Signal) -> live_basket_guard.GuardState:
    return live_basket_guard.GuardState(
        armed=bool(signal.basket_guard_armed),
        triggered=bool(signal.basket_guard_triggered),
        peak_pl=signal.basket_guard_peak_pl,
        trigger_reason=signal.basket_guard_trigger_reason,
        recovery_pending=bool(signal.basket_guard_recovery_pending),
    )


def _store_guard_state(
    signal: Signal,
    state: live_basket_guard.GuardState,
) -> None:
    signal.basket_guard_armed = state.armed
    signal.basket_guard_triggered = state.triggered
    signal.basket_guard_peak_pl = state.peak_pl
    signal.basket_guard_trigger_reason = state.trigger_reason
    signal.basket_guard_recovery_pending = state.recovery_pending


def _apply_live_basket_guard(
    signal: Signal,
    summary: dict,
) -> live_basket_guard.GuardDecision:
    if summary.get("positions_complete", True) is False:
        raise RuntimeError("MT5 positions_get unavailable for basket guard")

    policy = _basket_guard_policy()
    floating_pl = float(summary.get("floating_pl", summary.get("pl")) or 0.0)
    realized_pl = float(summary.get("realized_pl") or 0.0)
    realized_complete = bool(summary.get("realized_complete", True))
    total_pl = summary.get("total_pl")
    if total_pl is None and realized_complete:
        total_pl = summary.get("pl", floating_pl + realized_pl)
    observed_pl = float(total_pl) if total_pl is not None else floating_pl

    signal_id = f"{signal.channel}_{signal.message_id}"
    if not realized_complete and not signal.basket_guard_realized_degraded_logged:
        signal.basket_guard_realized_degraded_logged = True
        _journal_event(
            signal_id,
            "basket_guard_total_pl_degraded",
            floating_pl=round(floating_pl, 2),
            realized_pl=round(realized_pl, 2),
            total_pl=None,
            missing_realized_tickets=list(
                summary.get("missing_realized_tickets") or []
            ),
        )
    elif realized_complete and signal.basket_guard_realized_degraded_logged:
        signal.basket_guard_realized_degraded_logged = False
        _journal_event(
            signal_id,
            "basket_guard_total_pl_recovered",
            floating_pl=round(floating_pl, 2),
            realized_pl=round(realized_pl, 2),
            total_pl=round(float(total_pl), 2),
        )

    decision = live_basket_guard.evaluate_guard(
        channel=signal.channel,
        floating_pl=observed_pl,
        n_open=int(summary.get("n_open") or 0),
        state=_guard_state(signal),
        policy=policy,
        profit_evidence_complete=realized_complete,
    )
    _store_guard_state(signal, decision.state)
    common = {
        "channel": signal.channel,
        "observed_pl": round(decision.observed_pl, 2),
        "floating_pl": round(floating_pl, 2),
        "realized_pl": round(realized_pl, 2),
        "total_pl": (
            round(float(total_pl), 2) if total_pl is not None else None
        ),
        "realized_complete": realized_complete,
        "peak_pl": (
            round(float(decision.state.peak_pl), 2)
            if decision.state.peak_pl is not None else None
        ),
        "n_open": int(summary.get("n_open") or 0),
        "open_tickets": [
            int(ticket) for ticket in summary.get("open_tickets") or []
        ],
        "loss_cap": float(policy.loss_cap),
        "profit_arm": float(policy.profit_arm),
        "profit_lock": float(policy.profit_lock),
        "money_source": "realized_plus_floating_account_currency",
    }

    if decision.action == "arm":
        _journal_event(signal_id, "basket_guard_armed", **common)
        return decision
    if decision.action != "close":
        return decision

    tickets = common["open_tickets"]
    try:
        for ticket in tickets:
            if ticket not in signal.basket_guard_close_tickets:
                signal.basket_guard_close_tickets.append(ticket)
            pending_actions.enqueue_close_position(
                signal,
                ticket,
                label=f"BASKET_GUARD_{decision.reason.upper()} #{ticket}",
            )
        for ticket in signal.pending_tickets:
            pending_actions.enqueue_cancel_pending(
                signal,
                ticket,
                label=(
                    f"BASKET_GUARD_{decision.reason.upper()} pend #{ticket}"
                ),
            )
    except Exception:
        # A partial spool/write failure must be retried on the next sample.
        signal.basket_guard_recovery_pending = True
        raise
    signal.basket_guard_recovery_pending = False
    event = (
        "basket_guard_close_recovered"
        if decision.reason == "recovery"
        else "basket_guard_triggered"
    )
    _journal_event(
        signal_id,
        event,
        reason=decision.reason,
        queued_close_count=len(tickets),
        **common,
    )
    return decision


def _should_alert_null_tick_streak(streak: int, already_alerted: bool,
                                    threshold: int) -> bool:
    """True solo si N ticks consecutivos None y aun no alertamos.

    Misma semantica que pending_actions._should_alert_null_tick_streak —
    duplicado intencional para evitar import cruzado raro (pending_actions
    importa de position_lifecycle_monitor indirectamente via listener).
    """
    return (streak >= threshold) and (not already_alerted)


def _should_emit_periodic_snapshot(now_ts: float, last_ts: float,
                                   interval_s: float) -> bool:
    """True when non-causal telemetry may emit another periodic snapshot."""
    if interval_s <= 0:
        return True
    return (now_ts - last_ts) >= interval_s


def _auto_finalize_grace_elapsed(
    monitor_started_monotonic: float,
    *,
    now_monotonic: float | None = None,
    grace_s: float = 30.0,
) -> bool:
    """Use a local monotonic clock for the post-fill close grace period."""
    now = time.monotonic() if now_monotonic is None else now_monotonic
    return (now - monitor_started_monotonic) >= grace_s


def _signal_still_alive(signal: Signal) -> bool:
    """True si la señal sigue abierta y tiene posiciones que vigilar."""
    return signal.status == "open"


async def _open_market_internal(
    signal: Signal,
    *,
    level: float,
    lot: float,
    sl,
    tp,
    comment: str,
):
    """Open a delayed DCA under a fresh, auditable bot decision."""
    with causal_trace.bind_internal_decision(
        message_revision_id=signal.source_message_revision_id,
        parent_decision_id=signal.source_decision_id,
        reason="position_lifecycle_dca",
    ) as decision:
        try:
            import journal
            journal.event(
                f"{signal.channel}_{signal.message_id}",
                "bot_internal_decision_started",
                decision_id=decision.decision_id,
                message_revision_id=decision.message_revision_id,
                parent_decision_id=decision.parent_decision_id,
                decision_reason=decision.decision_reason,
                decision_level=level,
            )
        except Exception:
            pass
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                causal_trace.context_bound_call(
                    executor.open_market,
                    signal.direction,
                    lot,
                    sl=sl,
                    tp=tp,
                    comment=comment,
                    magic=signal.magic,
                ),
            )
        finally:
            try:
                import journal
                action_ids = causal_trace.declared_action_ids(decision)
                journal.event(
                    f"{signal.channel}_{signal.message_id}",
                    "bot_internal_decision",
                    decision_id=decision.decision_id,
                    message_revision_id=decision.message_revision_id,
                    parent_decision_id=decision.parent_decision_id,
                    decision_reason=decision.decision_reason,
                    decision_level=level,
                    declared_action_ids=action_ids,
                    declared_action_count=len(action_ids),
                )
            except Exception:
                pass


def _close_all_positions(signal: Signal, reason: str):
    """Encola el cierre de TODAS las posiciones de la señal con motivo."""
    print(f"[Position Monitor] [TIME-STOP] {reason} -> cerrando todas las posiciones de "
          f"senal {signal.message_id}")

    for t in signal.all_filled_tickets:
        pending_actions.enqueue_close_position(
            signal, t, label=f"{reason} #{t}"
        )
    for t in signal.pending_tickets:
        pending_actions.enqueue_cancel_pending(
            signal, t, label=f"{reason} pend #{t}"
        )
    signal.status = "closed"


def _dominant_close_tag(signal: Signal, by_tag: dict) -> str:
    requested = getattr(signal, "requested_close_reason", None)
    if requested:
        return str(requested)
    if not by_tag:
        return "MT5_AUTO"
    return max(by_tag.items(), key=lambda item: item[1])[0]


def _decide_close_tag(exit_price: float, ticket_entry: float, direction: str,
                      tps: list, effective_sl,
                      be_armed: bool, *, effective_tp=None,
                      broker_close_reason: str | None = None
                      ) -> tuple[str, "float | None"]:
    """Decide el motivo de cierre de UNA posicion. FUNCION PURA (testeable).

    DIRECCIONAL: un TP/SL solo cuenta si el precio lo CRUZO en la direccion
    correcta (no solo "se acerco"). Tolerancia $0.5 (spread + slippage).

    `effective_sl` debe ser el SL REAL de ESTE ticket: si un mensaje de
    gestion lo movio (BE, MOVE_SL_TO_PRICE, trailing) hay que pasar ese
    valor, NO el SL del proveedor — si no, un cierre por el SL movido se
    etiquetaba "MANUAL" (bug 2026-05-18: canal1_19754 y canal1_19759).

    Devuelve (tag, distance_to_tag):
      TP1..TPn  — cerro en un take-profit.
      SL        — cerro en un stop (del proveedor o movido por gestion).
      LOSS_BE   — cerro en breakeven (SL ~en el entry del propio ticket).
      MANUAL    — ni TP ni SL ni BE → cierre externo desconocido.
    """
    TOL = 0.5

    broker_reason = str(broker_close_reason or "").lower()
    if broker_reason == "tp":
        if effective_tp is None:
            effective_tp = min(
                (tp for tp in (tps or [])),
                key=lambda tp: abs(exit_price - tp),
                default=None,
            )
        if effective_tp is None:
            return "TP", None
        provider_match = min(
            enumerate(tps or [], start=1),
            key=lambda item: abs(float(effective_tp) - float(item[1])),
            default=None,
        )
        if (provider_match is not None
                and abs(float(effective_tp) - float(provider_match[1])) <= TOL):
            tag = f"TP{provider_match[0]}"
        else:
            tag = "TP_PROVISIONAL"
        return tag, round(abs(exit_price - float(effective_tp)), 3)

    if broker_reason in {"sl", "be"}:
        is_be = (
            broker_reason == "be"
            or (effective_sl is not None
                and abs(float(effective_sl) - ticket_entry) <= 1.0)
            or (be_armed and abs(exit_price - ticket_entry) <= 1.5)
        )
        distance = (
            round(abs(exit_price - float(effective_sl)), 3)
            if effective_sl is not None else None
        )
        return ("LOSS_BE" if is_be else "SL"), distance

    if broker_reason in {"manual", "mobile", "web"}:
        return "MANUAL", None

    if broker_reason == "bot_close":
        return "BOT_CLOSE", None

    def _tp_crossed(price, tp_level):
        if direction == "SELL":
            return price <= tp_level + TOL
        return price >= tp_level - TOL   # BUY

    def _sl_crossed(price, sl_level):
        if direction == "SELL":
            return price >= sl_level - TOL
        return price <= sl_level + TOL   # BUY

    tag = "UNKNOWN"
    best_dist = float("inf")

    for i, tp in enumerate(tps or []):
        if _tp_crossed(exit_price, tp):
            d = abs(exit_price - tp)
            if d < best_dist:
                tag, best_dist = f"TP{i + 1}", d

    if effective_sl is not None and _sl_crossed(exit_price, effective_sl):
        d = abs(exit_price - effective_sl)
        if d < best_dist:
            # Si el SL efectivo esta ~en el entry del ticket, fue un BE move
            # y el cierre es un breakeven, no una perdida por stop.
            tag = "LOSS_BE" if abs(effective_sl - ticket_entry) <= 1.0 else "SL"
            best_dist = d

    if tag == "UNKNOWN":
        # Fallback: cerca del entry del propio ticket con BE armado = breakeven.
        if be_armed and abs(exit_price - ticket_entry) <= 1.5:
            tag = "LOSS_BE"
        else:
            tag = "MANUAL"
        return tag, None

    return tag, round(best_dist, 3)


def _classify_closures(signal: Signal) -> list[dict]:
    """Para cada ticket de la señal: lee history MT5, identifica precio/PnL
    de cierre, y determina si fue TP1/TP2/.../SL/MANUAL/UNKNOWN.

    DIRECCIONAL: un TP solo se considera tocado si el precio CRUZÓ el TP
    en la dirección esperada (no solo "se acercó dentro de tolerancia").

      • SELL TP X (X < entry): close válido si exit_price <= X + tolerancia
        (puede ser X mismo o algo por debajo; nunca por encima de X+tol)
      • BUY  TP X (X > entry): close válido si exit_price >= X - tolerancia

    Sin esto, un cierre manual a precio "cerca" de un TP pero al lado
    EQUIVOCADO se clasificaba como TP. Ejemplo real (canal1_19236 SELL):
      TP4 = 4525, exit_price = 4525.94 → POR ENCIMA del TP (TP no tocado),
      pero TOL=1.5 lo etiquetaba TP4. Era un cierre manual del usuario.

    Tolerancia $0.5 (volvemos al original) cubre el slippage normal cuando
    el TP SÍ se cruza (broker fillea pocos ticks pasado el TP).

    Devuelve: [{ticket, exit_price, pnl, closed_by_tag, distance_to_tag}, ...]
    """
    out = []
    try:
        for ticket in signal.all_filled_tickets:
            deals = mt5.history_deals_get(position=ticket)
            if not deals:
                continue
            # El último deal de la posición es el de cierre (el primero es apertura)
            close_deal = max(deals, key=lambda d: d.time_msc)
            # El primer deal es apertura — sin ese no hay PnL realizado
            open_deal = min(deals, key=lambda d: d.time_msc)
            if close_deal.ticket == open_deal.ticket:
                # Solo hay 1 deal → posición aún abierta o algo raro
                continue
            exit_price = close_deal.price
            ticket_entry = open_deal.price  # precio real de apertura de ESTE ticket (no necesariamente = market fill)
            # PnL realizado de TODOS los deals de esta posición (apertura+cierre)
            pnl_total = sum(
                float(getattr(deal, field, 0.0) or 0.0)
                for deal in deals
                for field in ("profit", "commission", "swap", "fee")
            )
            broker_reason = close_reason_from_deal(close_deal)
            broker_deal_reason = getattr(close_deal, "reason", None)
            effective_sl = signal.sl_by_ticket.get(
                int(ticket), signal.sl_by_ticket.get(str(ticket), signal.sl))
            effective_tp = signal.tp_by_ticket.get(
                int(ticket), signal.tp_by_ticket.get(str(ticket)))
            evidence = {
                "broker_close_reason": broker_reason,
                "broker_deal_reason": broker_deal_reason,
                "effective_tp": effective_tp,
                "effective_sl": effective_sl,
            }
            if ticket in getattr(signal, "basket_guard_close_tickets", []):
                reason = str(
                    getattr(signal, "basket_guard_trigger_reason", None)
                    or "triggered"
                ).upper()
                out.append({
                    "ticket": int(ticket),
                    "exit_price": round(exit_price, 2),
                    "pnl": round(pnl_total, 2),
                    "closed_by_tag": f"BASKET_GUARD_{reason}",
                    "distance_to_tag": None,
                    **evidence,
                    "classification_source": "bot_state",
                })
                continue
            if ticket in getattr(signal, "be_rescue_tickets", []):
                out.append({
                    "ticket": int(ticket),
                    "exit_price": round(exit_price, 2),
                    "pnl": round(pnl_total, 2),
                    "closed_by_tag": "BE_RESCUE_TIMEOUT",
                    "distance_to_tag": None,
                    **evidence,
                    "classification_source": "bot_state",
                })
                continue

            # ── CLOSE_FIRST: ticket cerrado explícitamente por acción de gestión ──
            # Este cierre no corresponde a ningún TP ni SL; es intencional.
            # Se registra antes del bloque TP/SL para evitar falsos positivos
            # cuando el precio en ese momento está cerca de un nivel.
            if ticket in signal.close_first_tickets:
                out.append({
                    "ticket": int(ticket),
                    "exit_price": round(exit_price, 2),
                    "pnl": round(pnl_total, 2),
                    "closed_by_tag": "CLOSE_FIRST",
                    "distance_to_tag": None,
                    **evidence,
                    "classification_source": "bot_state",
                })
                continue

            # SL EFECTIVO de este ticket: si un mensaje de gestión movió su
            # SL (BE, MOVE_SL_TO_PRICE, trailing) usamos el SL real que
            # pending_actions registró en sl_by_ticket al confirmarlo en MT5;
            # si no, el SL del proveedor. Sin esto el cierre por un SL movido
            # se etiquetaba "MANUAL" (bug 2026-05-18).
            tag, dist = _decide_close_tag(
                exit_price, ticket_entry, signal.direction,
                signal.tps, effective_sl, signal.be_armed,
                effective_tp=effective_tp,
                broker_close_reason=broker_reason)

            if broker_reason in {"tp", "sl", "be"}:
                relevant_level = (
                    effective_tp if broker_reason == "tp" else effective_sl)
                source = (
                    "broker_reason_and_effective_level"
                    if relevant_level is not None
                    else "broker_reason"
                )
            elif broker_reason in {"manual", "mobile", "web", "bot_close"}:
                source = "broker_reason"
            else:
                source = "price_vs_effective_level"

            out.append({
                "ticket": int(ticket),
                "exit_price": round(exit_price, 2),
                "pnl": round(pnl_total, 2),
                "closed_by_tag": tag,
                "distance_to_tag": dist,
                **evidence,
                "classification_source": source,
            })
    except Exception as e:
        print(f"[Position Monitor] _classify_closures error: {e}")
    return out


def _floating_pl_summary(signal: Signal) -> dict:
    """Calcula P/L flotante actual + métricas de posición.

    Devuelve dict con: pl, n_open, avg_entry, current_price.
    Si no se puede leer el tick o las posiciones, devuelve dict con None.
    """
    out = {
        "pl": 0.0,
        "n_open": 0,
        "avg_entry": None,
        "current_price": None,
        "lots_total": 0.0,
        "open_tickets": [],
        "positions_complete": True,
    }
    tick = mt5.symbol_info_tick(config.MT5_SYMBOL)
    if tick:
        # Para BUY el "precio actual relevante" para cierre es bid; para SELL ask.
        out["current_price"] = (tick.bid if signal.direction == "BUY" else tick.ask)

    all_open = mt5.positions_get()
    if all_open is None:
        out["positions_complete"] = False
        return out

    wanted = set(int(ticket) for ticket in signal.all_filled_tickets)
    weighted_entry = 0.0
    for p in all_open:
        if int(p.ticket) not in wanted:
            continue
        out["pl"] += p.profit
        out["n_open"] += 1
        out["open_tickets"].append(int(p.ticket))
        out["lots_total"] += p.volume
        weighted_entry += p.price_open * p.volume

    if out["lots_total"] > 0:
        out["avg_entry"] = weighted_entry / out["lots_total"]

    return out


def _confirmed_realized_ticket_pl(ticket: int) -> float | None:
    deals = mt5.history_deals_get(position=int(ticket))
    if deals is None or len(deals) < 2:
        return None
    return sum(
        float(getattr(deal, field, 0.0) or 0.0)
        for deal in deals
        for field in ("profit", "commission", "swap", "fee")
    )


def _signal_pl_summary(signal: Signal) -> dict:
    """Combine current MT5 exposure with confirmed account-currency closes."""
    summary = _floating_pl_summary(signal)
    floating_pl = float(summary.get("pl") or 0.0)
    summary["floating_pl"] = floating_pl

    known_tickets = []
    seen = set()
    for ticket in (
        list(signal.basket_guard_known_tickets)
        + list(signal.all_filled_tickets)
    ):
        ticket = int(ticket)
        if ticket in seen:
            continue
        seen.add(ticket)
        known_tickets.append(ticket)
    signal.basket_guard_known_tickets = known_tickets

    cache = signal.basket_guard_realized_by_ticket
    open_tickets = set(int(ticket) for ticket in summary.get("open_tickets") or [])
    missing = []
    if summary.get("positions_complete", True):
        for ticket in known_tickets:
            if ticket in open_tickets or ticket in cache or str(ticket) in cache:
                continue
            realized = _confirmed_realized_ticket_pl(ticket)
            if realized is None:
                missing.append(ticket)
                continue
            cache[ticket] = float(realized)
            _journal_event(
                f"{signal.channel}_{signal.message_id}",
                "basket_guard_realized_ticket_confirmed",
                ticket=ticket,
                realized_pl=round(float(realized), 8),
                money_fields=["profit", "commission", "swap", "fee"],
            )
    else:
        missing = [ticket for ticket in known_tickets if ticket not in open_tickets]

    realized_pl = sum(float(value) for value in cache.values())
    realized_complete = bool(
        summary.get("positions_complete", True) and not missing
    )
    summary.update({
        "realized_pl": realized_pl,
        "realized_complete": realized_complete,
        "missing_realized_tickets": missing,
        "total_pl": (
            floating_pl + realized_pl if realized_complete else None
        ),
    })
    return summary


def _next_tp_for_signal(signal: Signal) -> float | None:
    """Devuelve el siguiente TP relevante: el del próximo ticket en orden
    escalonado, o el target_tp_index si está fijo.

    Útil para que la recomendación incluya cuán lejos estamos del próximo
    objetivo del trade.
    """
    if not signal.tps:
        return None
    n_open = len(signal.all_filled_tickets)
    # Si TP fijo (target_tp_index), todos van al mismo
    if signal.target_tp_index is not None:
        idx = min(signal.target_tp_index, len(signal.tps) - 1)
        return signal.tps[idx]
    # Escalonado: el siguiente TP es el del último ticket abierto (su tp asignado)
    idx = min(max(0, n_open - 1), len(signal.tps) - 1)
    return signal.tps[idx]


def _build_time_stop_recommendation(signal: Signal, summary: dict, mfe: float | None,
                                    mae: float | None) -> str:
    """Construye una recomendación contextual para el usuario.

    Reglas (orden de prioridad):
      • P/L > +5      → "en profit, considera cerrar para asegurar"
      • P/L >= -2     → "en BE, considera cerrar y liberar margen"
      • P/L < -2 con MFE > +5 → "perdimos profit pasado, valora cerrar"
      • P/L < -2 sin MFE → "señal mala desde inicio, considera limitar"
      • Cerca del SL  → "precio cerca del SL, valora cerrar para evitar pérdida completa"
    """
    pl = summary["pl"]
    cur = summary["current_price"]
    sl = signal.sl

    # Cercanía al SL (60% o más del recorrido entry→SL ya consumido)
    near_sl = False
    if cur is not None and sl is not None and summary["avg_entry"] is not None:
        sl_dist = abs(cur - sl)
        entry_sl_dist = abs(summary["avg_entry"] - sl)
        if entry_sl_dist > 0 and sl_dist / entry_sl_dist < 0.4:
            near_sl = True

    if pl > 5:
        return ("Floating en profit. Considera cerrar manualmente para asegurar "
                "antes de que se pueda revertir.")
    if pl >= -2:
        return ("Trade en break-even tras el time-stop. Considera cerrar y liberar "
                "margen — el momentum no ha llegado.")
    if near_sl:
        return ("Precio cerca del SL. Valora cerrar manualmente para limitar la "
                "pérdida antes del SL completo.")
    if mfe is not None and mfe > 5:
        return ("Llegamos a tener profit pero se ha revertido. Si crees que vuelve, "
                "deja correr; si no, cierra para limitar.")
    return ("Señal sin haber visto profit significativo. Considera cerrar para "
            "evitar que llegue al SL.")


async def _notify_time_stop(signal: Signal, elapsed_min: float):
    """Envía notificación al usuario con resumen + recomendación.

    NO cierra. Marca `signal.time_stop_at = None` para no renotificar.
    """
    # Lazy import para evitar circular: position_lifecycle_monitor ↔ listener
    try:
        from listener import notify
    except Exception as e:
        print(f"[Position Monitor] No pude importar notify(): {e}")
        notify = None

    summary = _floating_pl_summary(signal)
    if summary["n_open"] <= 0:
        signal.time_stop_at = None
        try:
            import journal
            journal.event(
                f"{signal.channel}_{signal.message_id}",
                "time_stop_skipped_no_positions",
                elapsed_min=round(elapsed_min, 1),
            )
        except Exception as e:
            print(
                "[Position Monitor] journal.event "
                f"time_stop_skipped_no_positions error: {e}"
            )
        return False

    next_tp = _next_tp_for_signal(signal)

    # MFE/MAE del journal si existe
    mfe = mae = None
    try:
        import journal
        sig_id = f"{signal.channel}_{signal.message_id}"
        trade = journal.get_trade(sig_id)
        if trade:
            mfe = trade.get("mfe_usd")
            mae = trade.get("mae_usd")
    except Exception:
        pass

    rec = _build_time_stop_recommendation(signal, summary, mfe, mae)

    cur = summary["current_price"]
    avg = summary["avg_entry"]
    cur_str = f"{cur:.2f}" if cur is not None else "n/a"
    avg_str = f"{avg:.2f}" if avg is not None else "n/a"
    next_tp_str = (f"{next_tp:.2f} ({(next_tp-cur):+.2f}$ desde precio)"
                   if next_tp is not None and cur is not None else "n/a")
    sl_str = f"{signal.sl:.2f}" if signal.sl is not None else "sin SL"
    mfe_str = f"${mfe:+.2f}" if mfe is not None else "n/a"
    mae_str = f"${mae:+.2f}" if mae is not None else "n/a"

    text = (
        f"⏰ TIME-STOP {elapsed_min:.0f}min — {signal.channel} {signal.direction} #{signal.message_id}\n"
        f"\n"
        f"Floating P/L: ${summary['pl']:+.2f} | Tickets abiertos: {summary['n_open']} | Lotes: {summary['lots_total']:.2f}\n"
        f"Precio: {cur_str} | Entry promedio: {avg_str}\n"
        f"Próximo TP: {next_tp_str}\n"
        f"SL: {sl_str}\n"
        f"MFE durante el trade: {mfe_str} | MAE: {mae_str}\n"
        f"\n"
        f"💡 {rec}\n"
        f"\n"
        f"_(El bot NO cerrará automáticamente — decide tú.)_"
    )
    print(f"[Position Monitor] [TIME-STOP NOTIFY] señal {signal.message_id} elapsed={elapsed_min:.1f}min "
          f"pl=${summary['pl']:+.2f}")
    if notify is not None:
        await notify(text)

    # Apaga el time-stop para no renotificar.
    signal.time_stop_at = None

    # Journal hook (best-effort)
    try:
        import journal
        sig_id = f"{signal.channel}_{signal.message_id}"
        journal.event(sig_id, "time_stop_notified",
                      elapsed_min=round(elapsed_min, 1),
                      pl=round(summary["pl"], 2),
                      n_open=summary["n_open"],
                      recommendation=rec)
        journal.anomaly(sig_id, "outcome", "warning",
                        "notify-only time-stop fired; trade left open for "
                        "post-trade analysis",
                        elapsed_min=round(elapsed_min, 1),
                        pl=round(summary["pl"], 2),
                        n_open=summary["n_open"])
    except Exception as e:
        print(f"[Position Monitor] journal.event time_stop_notified error: {e}")
    return True


async def _arm_be(signal: Signal):
    """Mueve el SL de TODAS las posiciones filled a su entry price respectivo.

    Encola la modificación a través de pending_actions para que use el sistema
    de retry tick-a-tick. No espera el resultado: la cola se encarga.

    GUARD anti-Invalid-stops (sesion 2026-05-12, ticket 1291100284):
      Para SELL: BE válido requiere SL > current_ask + stops_level.
                 Si current_ask ya está >= entry, MT5 rechaza con 10016.
      Para BUY:  BE válido requiere SL < current_bid - stops_level.
                 Si current_bid ya está <= entry, MT5 rechaza con 10016.

      Sin este guard, la cola reintenta 340+ veces hasta el cap de 30s,
      satura el log con "Invalid stops" y bloquea el event loop. Al
      siguiente _apply_sl_tp se reencola y vuelve a fallar otros 30s.

      Si la posicion ya esta peor que su entry, NO mover SL — mantenemos
      el SL del proveedor (que sigue siendo valido). El BE es proteccion
      ante futuras adversidades, no rescate de adversidades pasadas.
    """
    loop = asyncio.get_event_loop()
    # Lee tick actual una vez para todos los tickets
    try:
        import MetaTrader5 as _mt5
        tick = await loop.run_in_executor(
            None, lambda: _mt5.symbol_info_tick(config.MT5_SYMBOL))
        si = await loop.run_in_executor(
            None, lambda: _mt5.symbol_info(config.MT5_SYMBOL))
        # stops_level en puntos → en USD: puntos * point_size
        # Para XAUUSD digits=2, point=0.01. stops_level=20 puntos = $0.20
        min_dist = (si.trade_stops_level * si.point) if si else 0.20
    except Exception:
        tick = None
        min_dist = 0.30  # fallback conservador

    moved = 0
    skipped_invalid = 0
    for t in signal.all_filled_tickets:
        entry = await loop.run_in_executor(None, lambda tt=t: executor.entry_price(tt))
        if entry is None:
            print(f"[Position Monitor] BE: ticket {t} sin entry price legible, omitido")
            continue

        # Validar BE contra precio actual antes de encolar
        if tick is not None:
            if signal.direction == "SELL":
                # BE valido: entry > current_ask + min_dist
                if entry <= tick.ask + min_dist:
                    print(f"[Position Monitor] BE SKIP ticket {t}: SELL entry={entry:.2f} "
                          f"<= ask {tick.ask:.2f} + {min_dist:.2f} (MT5 rechazaria). "
                          f"Mantengo SL del proveedor.")
                    skipped_invalid += 1
                    continue
            else:  # BUY
                # BE valido: entry < current_bid - min_dist
                if entry >= tick.bid - min_dist:
                    print(f"[Position Monitor] BE SKIP ticket {t}: BUY entry={entry:.2f} "
                          f">= bid {tick.bid:.2f} - {min_dist:.2f} (MT5 rechazaria). "
                          f"Mantengo SL del proveedor.")
                    skipped_invalid += 1
                    continue

        pending_actions.enqueue_modify_sl(
            signal, t, entry, label=f"BE→{entry:.2f} #{t}"
        )
        moved += 1
    print(f"[Position Monitor] BE armado: {moved} posiciones movidas a entry "
          f"({skipped_invalid} skipped por precio adverso)")
    try:
        import journal
        sig_id = f"{signal.channel}_{signal.message_id}"
        journal.event(sig_id, "be_armed", n_moved=moved,
                      n_skipped_invalid=skipped_invalid,
                      trigger_tp_idx=signal.be_at_tp_index)
    except Exception as e:
        print(f"[Position Monitor] journal.event be_armed error: {e}")


async def run(signal: Signal, levels: list[float]):
    """
    Lanza el monitor en background para una señal activa.
    levels: precios donde el monitor abrirá market cuando el precio los toque.
      • Modo "intra_dca" (Canal 2): N-1 niveles equiespaciados dentro del rango.
      • Modo "extremes" (Canal 1 con rango): 1 solo nivel (extremo opuesto).
      • Modo "market_only" o legacy con [] : sin niveles, monitor solo para BE/TS.

    El monitor termina cuando:
      - Se han abierto todas las DCAs Y BE no está pendiente Y no hay time-stop
      - La señal pasa a status='closed' (SL hit, CLOSE_ALL, time-stop)
      - El TIME-STOP defensivo se dispara → cierra todo
    """
    if (
        not levels
        and signal.time_stop_at is None
        and signal.be_at_tp_index is None
        and not _basket_guard_enabled_for(signal)
    ):
        return

    pending = list(levels)
    direction = signal.direction
    symbol = config.MT5_SYMBOL
    be_trigger = signal.be_trigger_price()

    print(f"[Position Monitor] Iniciado señal {signal.message_id}. "
          f"Niveles: {pending} | time_stop={signal.time_stop_at} | "
          f"be_trigger={be_trigger} (idx={signal.be_at_tp_index})")

    last_tick_ms = 0
    monitor_started_monotonic = time.monotonic()
    # Throttle de update_extremes: cada 5s actualiza MFE/MAE en el journal.
    # Más frecuente sería I/O excesivo; menos frecuente perdería extremos breves.
    last_extremes_ts = 0.0
    EXTREMES_INTERVAL_S = 5.0

    # Local MT5 sampling for the Dubai basket guard. It is independent from
    # journal snapshots so logging latency cannot delay a risk decision.
    last_basket_guard_ts = 0.0
    basket_guard_interval_s = min(
        0.1,
        max(0.01, float(config.STRATEGY_C1_BASKET_GUARD_POLL_S)),
    )
    basket_guard_error_alerted = False

    # Batch E: track null-tick streak para detectar broker/MT5 down dentro
    # del monitor. Antes giraba en silencio.
    null_tick_streak = 0
    null_tick_alerted = False

    # Track de TPs ya tocados (set local al monitor). Util para emitir el
    # evento `tp_hit` UNA SOLA VEZ por TP, no en cada tick que el precio
    # cruza. Permite analisis posterior tipo: "el precio toco TP3 a los X
    # segundos, pero la posicion ya habia cerrado en TP1 — esto es exactamente
    # el regret que el doble market intenta capturar".
    tps_touched: set[int] = set()

    # Track del momento en que cada nivel DCA empezo a diferirse (defer
    # anti-TP-duplicado). Permite el timeout: si un nivel lleva difiriendo
    # mas de STRATEGY_DCA_DEFER_TIMEOUT_S, se abre igual con TP duplicado.
    defer_since: dict = {}  # level (float) -> timestamp del primer defer

    # Snapshot de P&L floating cada 30s para reconstruir la curva del trade
    # — esencial para distinguir "skill win" (low drawdown, clean) de
    # "lucky win" (deep drawdown, recuperó por suerte).
    last_pl_snapshot_ts = 0.0
    PL_SNAPSHOT_INTERVAL_S = float(config.POSITION_MONITOR_PL_SNAPSHOT_INTERVAL_S)

    # Polling rate del monitor: 10ms (antes era 50ms).
    # XAUUSD genera ticks cada 50-200ms en horas activas. Con 50ms de sleep
    # nos saltábamos ticks intermedios cuando el mercado iba rápido. A 10ms
    # capturamos prácticamente todos sin coste CPU notable (~0.1%).
    # Siguen siendo invisibles para el bot los mechazos < ~150ms (la latencia
    # red broker→tu MT5 + round-trip de la market order es la barrera dura).
    # Para cazar mechazos ultracortos haría falta pasar a órdenes LIMIT en
    # el broker — cambio arquitectónico que se discutió y aplazó.

    # El monitor sigue vivo MIENTRAS la señal esté abierta — no solo
    # mientras queden niveles DCA pendientes. Esto es necesario para que
    # el TIME-STOP y el BE move puedan dispararse aun cuando ya no queden
    # DCAs por abrir.
    while _signal_still_alive(signal):
        # ── TIME-STOP defensivo (solo si BE NO está armado todavía) ──
        # Si BE ya disparó, el SL está a entry y el riesgo está controlado:
        # dejamos correr la posición hasta TP o el SL=BE. No queremos cortar
        # ganadoras con el time-stop.
        if (signal.time_stop_at is not None
                and not signal.be_armed
                and datetime.utcnow() >= signal.time_stop_at):
            elapsed_min = (datetime.utcnow() - signal.timestamp).total_seconds() / 60
            if config.STRATEGY_TIME_STOP_NOTIFY_ONLY:
                # Modo nuevo: notificar al usuario y seguir vigilando.
                # _notify_time_stop apaga signal.time_stop_at para no renotificar.
                await _notify_time_stop(signal, elapsed_min)
                # Continuamos el loop — el monitor sigue para BE/DCAs/cierre normal
            else:
                # Modo legacy: cierra automáticamente
                _close_all_positions(
                    signal,
                    f"TIME-STOP (elapsed {elapsed_min:.1f}min)"
                )
                # Journal finalize via lazy import (evita circular)
                try:
                    from listener import _finalize_signal
                    await _finalize_signal(
                        signal, closed_by="TIMESTOP",
                        notes=f"elapsed={elapsed_min:.1f}min legacy auto-close"
                    )
                except Exception as e:
                    print(f"[Position Monitor] _finalize_signal time-stop error: {e}")
                break

        tick = await asyncio.to_thread(mt5.symbol_info_tick, symbol)

        # Batch E: detector de null-tick streak (broker/MT5 down)
        if not tick:
            null_tick_streak += 1
            if _should_alert_null_tick_streak(
                    null_tick_streak, null_tick_alerted,
                    NULL_TICK_STREAK_THRESHOLD):
                try:
                    import journal as _j_nt
                    sig_id_nt = f"{signal.channel}_{signal.message_id}"
                    _j_nt.anomaly(sig_id_nt, "mt5", "warning",
                                  f"DCA monitor: {null_tick_streak} ticks "
                                  f"consecutivos None — broker/MT5 disconnected, "
                                  f"senal sin vigilancia hasta que vuelva",
                                  streak=null_tick_streak,
                                  last_error=str(mt5.last_error()))
                except Exception:
                    pass
                null_tick_alerted = True
            await asyncio.sleep(0.01)
            continue
        if null_tick_alerted:
            try:
                import journal as _j_nt
                sig_id_nt = f"{signal.channel}_{signal.message_id}"
                _j_nt.event(sig_id_nt, "mt5_tick_recovered_monitor",
                            streak_resolved=null_tick_streak)
            except Exception:
                pass
        null_tick_streak = 0
        null_tick_alerted = False

        # Solo procesa si es un tick nuevo (time_msc cambia en cada tick real)
        if tick.time_msc == last_tick_ms:
            await asyncio.sleep(0.01)  # cede al event loop, mínimo de espera
            continue

        last_tick_ms = tick.time_msc

        guard_summary = None
        now_monotonic = time.monotonic()
        if (
            _basket_guard_enabled_for(signal)
            and now_monotonic - last_basket_guard_ts
            >= basket_guard_interval_s
        ):
            last_basket_guard_ts = now_monotonic
            try:
                guard_summary = await asyncio.to_thread(
                    _signal_pl_summary,
                    signal,
                )
                decision = _apply_live_basket_guard(signal, guard_summary)
                if decision.action == "arm":
                    print(
                        f"[Basket Guard] Dubai {signal.message_id}: "
                        f"proteccion armada en {decision.observed_pl:.2f}"
                    )
                elif decision.action == "close":
                    print(
                        f"[Basket Guard] Dubai {signal.message_id}: "
                        f"cierre {decision.reason} en "
                        f"{decision.observed_pl:.2f}"
                    )
                basket_guard_error_alerted = False
            except Exception as exc:
                print(
                    f"[Basket Guard] error Dubai {signal.message_id}: "
                    f"{type(exc).__name__}: {exc}"
                )
                if not basket_guard_error_alerted:
                    try:
                        import journal as _j_bg
                        _j_bg.anomaly(
                            f"{signal.channel}_{signal.message_id}",
                            "mt5",
                            "critical",
                            "proteccion de cesta Dubai sin lectura valida",
                            exc_type=type(exc).__name__,
                            exc_msg=str(exc)[:300],
                        )
                    except Exception:
                        pass
                basket_guard_error_alerted = True

        # ── Track MFE/MAE + auto-finalize (throttled cada 5s) ──
        now_ts = datetime.utcnow().timestamp()
        if now_ts - last_extremes_ts >= EXTREMES_INTERVAL_S:
            last_extremes_ts = now_ts
            try:
                summary = guard_summary
                if summary is None:
                    summary = await asyncio.to_thread(
                        _floating_pl_summary,
                        signal,
                    )
                if summary["n_open"] > 0:
                    import journal as _j
                    sig_id = f"{signal.channel}_{signal.message_id}"
                    _j.update_extremes(sig_id, summary["pl"])

                    # ── Floating P&L snapshot cada 30s ──
                    # Para reconstruir la curva del trade tick-a-tick y
                    # distinguir SKILL vs LUCK. Un win con drawdown profundo
                    # NO es lo mismo que un win clean. Esto lo cuantifica.
                    if _should_emit_periodic_snapshot(
                            now_ts, last_pl_snapshot_ts,
                            PL_SNAPSHOT_INTERVAL_S):
                        last_pl_snapshot_ts = now_ts
                        _j.event(sig_id, "floating_pl_snapshot",
                                 pl=round(summary["pl"], 2),
                                 n_open=summary["n_open"],
                                 current_price=summary.get("current_price"),
                                 avg_entry=round(summary["avg_entry"], 2)
                                     if summary.get("avg_entry") else None,
                                 elapsed_s=round((datetime.utcnow() - signal.timestamp).total_seconds(), 0))
                elif (
                    summary.get("positions_complete", True)
                    and
                    signal.all_filled_tickets
                    and _auto_finalize_grace_elapsed(
                        monitor_started_monotonic
                    )
                ):
                    # n_open == 0 pero hay tickets registrados → MT5 cerró todas
                    # las posiciones vía TP o SL sin que el canal mandara mensaje.
                    # El listener nunca supo del cierre → nunca llamó _finalize_signal.
                    # Detectamos aquí y finalizamos para que el journal quede completo.
                    closures = _classify_closures(signal)
                    by_tag = {}
                    for c in closures:
                        by_tag[c["closed_by_tag"]] = by_tag.get(c["closed_by_tag"], 0) + 1
                    summary_str = ", ".join(f"{tag}×{n}" for tag, n in by_tag.items())
                    print(f"[Position Monitor] Auto-finalize señal {signal.message_id}: "
                          f"n_open=0 ({summary_str if summary_str else 'no_history'})")
                    signal.status = "closed"
                    try:
                        import journal as _j
                        sig_id = f"{signal.channel}_{signal.message_id}"
                        # Evento detallado: qué cerró cada ticket exactamente
                        _j.event(sig_id, "positions_closed_by_mt5",
                                 closures=closures,
                                 summary_by_tag=by_tag)
                        from listener import _finalize_signal
                        # closed_by usa el tag dominante (mayoría de tickets)
                        dominant_tag = _dominant_close_tag(signal, by_tag)
                        await _finalize_signal(
                            signal, closed_by=dominant_tag,
                            notes=f"auto-finalize: {summary_str}"
                        )
                    except Exception as fe:
                        print(f"[Position Monitor] auto-finalize error: {fe}")
                        # Batch E: la senal esta marcada status=closed pero
                        # _finalize_signal fallo → no se loguea
                        # positions_closed_by_mt5, no hay PnL, el trade
                        # desaparece de analytics. Critical.
                        try:
                            import journal as _j_af
                            sig_id_af = f"{signal.channel}_{signal.message_id}"
                            _j_af.anomaly(sig_id_af, "mt5", "critical",
                                          f"auto-finalize fallo tras "
                                          f"status=closed: "
                                          f"{type(fe).__name__}: "
                                          f"{str(fe)[:200]} — trade no "
                                          f"aparecera en el ledger del dia",
                                          exc_type=type(fe).__name__)
                        except Exception:
                            pass
                    break
            except Exception as e:
                # Errores aquí no deben matar el monitor — solo log y seguir
                print(f"[Position Monitor] update_extremes error: {e}")

        # ── TP HIT INSTRUMENTATION ──────────────────────────────────────
        # Emite evento `tp_hit` la primera vez que el precio toca cada TP
        # disponible — independientemente de si la posicion estaba abierta o
        # ya habia cerrado. Permite responder preguntas como:
        #   "El precio toco TP3 a los X segundos. ¿Mi Pos B con TP=TP3 lo
        #    capturo o ya habia cerrado?"
        # Necesario para validar empiricamente la estrategia double_market
        # y futuras (trailing, partial close).
        if signal.tps:
            price_for_tp = tick.bid if direction == "BUY" else tick.ask
            for tp_idx, tp_price in enumerate(signal.tps):
                if tp_idx in tps_touched:
                    continue
                hit = ((direction == "BUY" and price_for_tp >= tp_price) or
                       (direction == "SELL" and price_for_tp <= tp_price))
                if hit:
                    tps_touched.add(tp_idx)
                    signal.observed_tp_hits[tp_idx] = datetime.utcnow()
                    elapsed_s = round(
                        (datetime.utcnow() - signal.timestamp).total_seconds(), 1)
                    try:
                        import journal as _j
                        sig_id = f"{signal.channel}_{signal.message_id}"
                        _j.event(sig_id, "tp_hit",
                                 tp_index=tp_idx,
                                 tp_price=tp_price,
                                 hit_price=price_for_tp,
                                 elapsed_s=elapsed_s,
                                 n_pos_open=len(signal.all_filled_tickets))
                    except Exception:
                        pass

        # ── BE MOVE: si el precio toca el TP indicado, mover SL a entry ──
        # Refrescamos be_trigger por si los TPs llegaron después (canal 2 edits).
        if not signal.be_armed and signal.be_at_tp_index is not None:
            be_trigger = signal.be_trigger_price()
            if be_trigger is not None:
                # Para BUY: TP está por encima de entry, se toca cuando precio sube → mirar bid
                # Para SELL: TP está por debajo, se toca cuando precio baja → mirar ask
                price_for_tp = tick.bid if direction == "BUY" else tick.ask
                tp_hit = ((direction == "BUY"  and price_for_tp >= be_trigger) or
                          (direction == "SELL" and price_for_tp <= be_trigger))
                if tp_hit:
                    print(f"[Position Monitor] BE TRIGGER tocado @ {price_for_tp:.2f} "
                          f"(TP{signal.be_at_tp_index+1}={be_trigger}) → arma BE")
                    await _arm_be(signal)
                    signal.be_armed = True

        # Si quedan niveles DCA, comprueba si toca abrir alguno
        if pending:
            current = tick.ask if direction == "BUY" else tick.bid
            next_level = pending[0]

            hit = (direction == "BUY"  and current <= next_level) or \
                  (direction == "SELL" and current >= next_level)

            if hit:
                # ── GUARD ANTI-DUPLICATE TP (deferring fix sesion 2026-05-12) ─
                # Si la posicion a abrir tendria position_index >= len(tps),
                # el TP seria duplicado del ultimo. Pero los TPs del canal
                # llegan en oleadas (canal 2 edita primero TP1, luego TPs
                # completos 20-30s despues). Si popeamos el level cuando
                # solo hay TP1, perdemos la oportunidad para siempre.
                #
                # FIX: NO popear. Diferir y re-evaluar en proxima iteracion
                # cuando los TPs reales puedan haber llegado completos.
                # backoff de 0.5s para no spamear el log/journal.
                #
                # Caso real perdido: canal2_12279 (-$13.72) abrio solo 2
                # DCAs en vez de 4 porque popeo el primer level cuando
                # solo habia llegado TP1 todavia.
                next_position_index = 1 + len(signal.dca_tickets)
                if signal.tps and next_position_index >= len(signal.tps):
                    # ── DEFER CON TIMEOUT (cambio 2026-05-15, Commit C) ──
                    # Diferir esperando que lleguen mas TPs del canal. PERO
                    # con timeout: si tras STRATEGY_DCA_DEFER_TIMEOUT_S no
                    # llegan, abrir IGUAL con el ultimo TP (duplicado). Sin
                    # timeout, canal2_12379 perdio -$16.49: el canal mando
                    # TP1 solo, el bot defirio para siempre, 0 DCAs, SL.
                    now_t = datetime.utcnow().timestamp()
                    if next_level not in defer_since:
                        defer_since[next_level] = now_t
                    elapsed_defer = now_t - defer_since[next_level]

                    if elapsed_defer < config.STRATEGY_DCA_DEFER_TIMEOUT_S:
                        print(f"[Position Monitor] DEFER nivel {next_level} "
                              f"({elapsed_defer:.1f}s): posicion "
                              f"#{next_position_index} sin TP propio "
                              f"({len(signal.tps)} TPs, esperando mas).")
                        try:
                            import journal as _j
                            sig_id = f"{signal.channel}_{signal.message_id}"
                            _j.event(sig_id, "dca_deferred_dup_tp",
                                     level=next_level,
                                     position_index=next_position_index,
                                     n_tps_available=len(signal.tps),
                                     elapsed_defer_s=round(elapsed_defer, 1))
                        except Exception:
                            pass
                        # NO pending.pop(0) — re-evaluar tras backoff.
                        await asyncio.sleep(0.5)
                        continue
                    else:
                        # Timeout — abrir igual con el ultimo TP disponible.
                        print(f"[Position Monitor] DEFER TIMEOUT nivel {next_level} "
                              f"({elapsed_defer:.1f}s sin TPs nuevos) → abro "
                              f"igual con TP duplicado (mejor que perder el DCA)")
                        try:
                            import journal as _j
                            sig_id = f"{signal.channel}_{signal.message_id}"
                            _j.event(sig_id, "dca_defer_timeout",
                                     level=next_level,
                                     position_index=next_position_index,
                                     n_tps_available=len(signal.tps),
                                     elapsed_defer_s=round(elapsed_defer, 1))
                        except Exception:
                            pass
                        # NO continue — cae a la apertura normal abajo.

                # ── GUARD DE PROXIMIDAD ─────────────────────────────────────
                # Cuando el precio se mueve muy rápido (dump/spike), varios
                # niveles consecutivos pueden ejecutarse en el mismo o en ticks
                # adyacentes → fills a $0.06 entre sí, sin promediar nada y
                # multiplicando la pérdida si la dirección es errónea.
                # Visto en sesión 28-abr (canal2_12003 y 12006).
                #
                # Política: si el fill que tendríamos AHORA quedaría a menos de
                # STRATEGY_DCA_MIN_SEPARATION_USD del fill ANTERIOR (último
                # DCA o el market original), se DESCARTA este nivel — pop sin
                # abrir orden. Niveles más alejados se siguen vigilando.
                # MIN_SEPARATION DINAMICO (cambio 2026-05-15, Commit C):
                # umbral = max(floor USD, ancho_rango * PCT). El 0.5 fijo
                # anterior saltaba mechazos legitimos; el dinamico (~0.25
                # para rangos de $4-5) captura el doble sin fills redundantes.
                _floor = config.STRATEGY_DCA_MIN_SEPARATION_USD
                _pct = config.STRATEGY_DCA_MIN_SEPARATION_PCT
                if signal.range_high is not None and signal.range_low is not None:
                    _range_width = abs(signal.range_high - signal.range_low)
                else:
                    _range_width = 5.0  # fallback rango tipico
                min_sep = max(_floor, _range_width * _pct)
                if min_sep > 0:
                    # Último fill conocido: el del DCA anterior si lo hay,
                    # si no el market original.
                    if signal.dca_tickets:
                        last_ticket = signal.dca_tickets[-1]
                        last_fill = await asyncio.get_event_loop().run_in_executor(
                            None, lambda: executor.entry_price(last_ticket)
                        )
                    else:
                        last_fill = signal.market_fill_price
                    if last_fill is not None:
                        gap = abs(current - last_fill)
                        if gap < min_sep:
                            print(f"[Position Monitor] SKIP nivel {next_level}: fill"
                                  f" sería @{current:.2f}, gap ${gap:.2f} vs "
                                  f"último fill {last_fill:.2f} (< ${min_sep} "
                                  f"min). Niveles más alejados siguen vivos.")
                            try:
                                import journal as _j
                                sig_id = f"{signal.channel}_{signal.message_id}"
                                _j.event(sig_id, "dca_skipped_proximity",
                                         level=next_level,
                                         current_price=round(current, 2),
                                         last_fill=round(last_fill, 2),
                                         gap=round(gap, 2),
                                         min_separation=min_sep)
                            except Exception:
                                pass
                            pending.pop(0)
                            await asyncio.sleep(0.01)
                            continue

                # Índice de posición: 0 = market inicial, 1+ = DCAs en orden de apertura.
                position_index = 1 + len(signal.dca_tickets)
                # ── TP del DCA: con o sin doble market ────────────────────
                # Sin doble market (legacy): escalonado natural.
                #   DCA[0] → tps[1] = TP2, DCA[1] → tps[2] = TP3.
                # CON doble market activo (Market B en TP3 vía override):
                #   Saltar el slot de Market B para que DCAs no compartan TP.
                #   DCA[0] → tps[1] = TP2  (primer slot libre)
                #   DCA[1] → tps[3] = TP4  (saltando tps[2] que es el de B)
                # Esto se almacena como override en signal.tp_overrides[ticket]
                # despues del fill, para que _apply_sl_tp lo respete cuando
                # lleguen TPs reales (sustituyen los provisionales).
                tp_override_idx = None
                if (signal.extra_market_tickets and signal.tps):
                    b_idx = config.STRATEGY_DOUBLE_MARKET_TP_INDEX
                    target_idx = position_index
                    if target_idx >= b_idx:
                        target_idx += 1  # salta slot de Market B
                    tp_override_idx = min(target_idx, len(signal.tps) - 1)
                    tp = signal.tps[tp_override_idx]
                else:
                    # Escalonado natural (sin doble market): pos i → tps[i]
                    tp = signal.tp_for_position(position_index)

                # Si BE ya está armado, el SL del nuevo DCA es su propio entry
                # (el level), no el SL original — coherente con BE move.
                sl_for_dca = next_level if signal.be_armed else signal.sl

                print(f"[Position Monitor] Nivel alcanzado: {next_level} @ {current:.2f} "
                      f"({tick.time_msc}ms) | pos#{position_index} -> TP={tp} SL={sl_for_dca}")
                lv = next_level  # captura para el lambda
                # Usa el lotaje efectivo de la señal (HIGH RISK → x0.5)
                lot = signal.effective_lot
                # Comment con signal_id embebido — esencial para resync on
                # startup. Format "DCA_c1_12345_4593.5" (max 31 chars MT5).
                # Backward compat: el resync detecta tanto format viejo
                # ("DCA_4593.5") como nuevo.
                dca_comment = f"DCA_c{signal.channel[-1]}_{signal.message_id}_{lv}"
                ticket = await _open_market_internal(
                    signal,
                    level=lv,
                    lot=lot,
                    sl=sl_for_dca,
                    tp=tp,
                    comment=dca_comment,
                )
                if ticket:
                    signal.dca_tickets.append(ticket)
                    # Si calculamos override por doble market, registrarlo en
                    # signal para que _apply_sl_tp lo respete cuando lleguen
                    # TPs reales (sustituyen provisionales).
                    if tp_override_idx is not None:
                        signal.tp_overrides[ticket] = tp_override_idx
                    try:
                        import journal
                        sig_id = f"{signal.channel}_{signal.message_id}"
                        # Slippage del DCA: diferencia entre el level planeado
                        # y el fill real (current). Para BUY positivo si fillamos
                        # MÁS BARATO; para SELL positivo si fillamos MÁS CARO.
                        slip = (lv - current) if direction == "BUY" else (current - lv)
                        journal.event(sig_id, "dca_filled",
                                      ticket=ticket, level=lv, fill_price=current,
                                      position_index=position_index, lot=lot,
                                      tp=tp, sl=sl_for_dca,
                                      tp_override_idx=tp_override_idx,
                                      slippage_favorable_usd=round(slip, 3),
                                      bid=round(tick.bid, 2),
                                      ask=round(tick.ask, 2),
                                      spread=round(tick.ask - tick.bid, 2))
                        journal.increment_dca_filled(sig_id)
                    except Exception as e:
                        print(f"[Position Monitor] journal.event dca_filled error: {e}")

                    # ── REAPPLY SL/TP TRAS NUEVA DCA ──────────────────────
                    # Tras añadir un DCA, llamamos _apply_sl_tp para que el
                    # nuevo ticket reciba su SL/TP correspondiente sin esperar
                    # al próximo update de la señal. También re-aplica los
                    # tickets previos (idempotente: MT5 retorna NO_CHANGES si
                    # los valores ya coinciden, clasificado como OK).
                    try:
                        import listener as _ln
                        await _ln._apply_sl_tp(signal)
                    except Exception as e:
                        print(f"[Position Monitor] reapply SL/TP tras DCA error: {e}")
                        # Batch E: el DCA acabade abrir SIN SL/TP. Si esto
                        # falla, el ticket vive desnudo hasta el proximo
                        # update — anomaly critical para revision urgente.
                        try:
                            import journal as _j_dca
                            sig_id_b = f"{signal.channel}_{signal.message_id}"
                            _j_dca.anomaly(sig_id_b, "naked", "critical",
                                           f"reapply SL/TP tras DCA fill "
                                           f"FALLO ({type(e).__name__}: "
                                           f"{str(e)[:200]}) — ticket "
                                           f"{ticket} abierto SIN proteccion",
                                           ticket=ticket,
                                           exc_type=type(e).__name__)
                        except Exception:
                            pass
                    pending.pop(0)
                else:
                    # Batch E: ticket None significa executor.open_market
                    # devolvio None (ya emite su propia anomaly via _send_safe).
                    # Aqui NO popeamos el level: queremos reintentar si el
                    # broker vuelve. Pero emitimos un evento para auditoria.
                    try:
                        import journal as _j_dca
                        sig_id_b = f"{signal.channel}_{signal.message_id}"
                        _j_dca.event(sig_id_b, "dca_fill_returned_none",
                                     level=lv, retry_pending=True)
                    except Exception:
                        pass
                    # NO pop — volveremos a intentar en proximo tick.
                    break  # no procesar mas niveles este tick para no apilar fallos

        await asyncio.sleep(0.01)

    print(f"[Position Monitor] Finalizado para señal {signal.message_id}")


def _monitor_done_callback(signal: Signal):
    """Factory: callback que recibe la task terminada y emite anomaly si
    crasheo. asyncio.create_task() NO re-lanza excepciones — sin esto la
    task del monitor podria morir en silencio dejando la senal sin
    vigilancia (TPs, SL, time-stop) y nadie se enteraria hasta el
    reconcile diario.

    CancelledError → no anomaly (shutdown ordenado del bot).
    Cualquier otra excepcion → critical (bug en run()).
    """
    sig_id = f"{signal.channel}_{signal.message_id}"

    def _cb(task):
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        except Exception:
            return
        if exc is None:
            return
        if isinstance(exc, asyncio.CancelledError):
            return
        # Bug grave: la task murio. Lazy import journal para no crear
        # circular con state.py (position_lifecycle_monitor importa Signal de state).
        try:
            import journal as _j_dca
            _j_dca.anomaly(sig_id, "mt5", "critical",
                           f"DCA monitor CRASHEO con {type(exc).__name__}: "
                           f"{str(exc)[:200]} — la senal queda sin "
                           f"vigilancia (TPs/SL/time-stop no se ejecutan)",
                           exc_type=type(exc).__name__,
                           exc_msg=str(exc)[:300])
        except Exception:
            pass

    return _cb


def start(signal: Signal, levels: list[float]) -> asyncio.Task:
    """Lanza el monitor como tarea asyncio en background.

    Si no hay niveles DCA pero sí TIME-STOP, también arranca para vigilar.
    Adjunta done_callback que detecta crash del monitor (silent failure
    pre-Batch-C: la task moria sin trazo).
    """
    import journal
    with causal_trace.detached_context(), journal.detached_test_mode():
        task = asyncio.create_task(run(signal, levels))
    task.add_done_callback(_monitor_done_callback(signal))
    return task
