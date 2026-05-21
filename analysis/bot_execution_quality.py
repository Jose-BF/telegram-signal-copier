"""
BOT EXECUTION QUALITY — evalúa solo lo que está bajo control del bot.

PRINCIPIO RECTOR (acordado con usuario):
   El bot es EJECUTOR, no analista. El canal hace análisis de mercado y
   decide dirección. Si el canal falla la dirección y hit SL → no es
   nuestra culpa. Si el bot pierde un BE move por modify_failed → SÍ es
   nuestra culpa.

Esta clasificación NO juzga outcome (P&L positivo/negativo). Juzga si el
bot ejecutó disciplinadamente lo que recibió. Las ganancias reales vienen
de optimizar EJECUCIÓN (lot size, num DCAs, BE timing, classifier conf
threshold, etc.) — no de predecir mercado.

Categorías:
   PERFECT_EXECUTION    100-90  todo aplicado a tiempo, sin failures
   GOOD_EXECUTION        90-70  pequeños issues no costaron dinero
   DEGRADED_EXECUTION    70-50  algo no fue óptimo, podría costar
   FAULTY_EXECUTION       <50   failure concreto que probablemente costó

CRUCE CRÍTICO: signals con FAULTY_EXECUTION + P&L negativo son los casos
URGENTES — el bot causó pérdida (no el mercado). Lista priorizada para
investigación.

Cosas que NO penalizamos (porque son features deseables o externos):
   • MAE depth (drawdown es feature de DCA, no bug)
   • Time to TP (depende del mercado)
   • SL hits con ejecución limpia (cosa del canal)
   • ambiguous_notifications (es comportamiento conservador correcto)
   • proximity_guard fires (es safety net trabajando)
   • Telegram delays (no controlamos Telegram)

Cosas que SÍ penalizamos:
   • mt5_action_failed (modify/close fail definitivo)
   • Ghost signals (señal sin signal_closed)
   • DCA dupes (race condition residual — debería ser 0 con fixes actuales)
   • Adverse slippage > $0.5 en DCAs (señal de timing/conexión malo)
   • Fill latency > 500ms (red lenta MT5 broker)
   • Predictor predicciones muy fuera (>$5 vs reales)
   • mgmt MOVE_SL_TO_BE marcado applied pero be_armed event ausente
"""
from typing import Optional


def classify_execution(events: list[dict]) -> dict:
    """Clasifica calidad de EJECUCIÓN del bot para una señal.

    Args:
        events: lista de eventos del JSONL para una sola signal_id.

    Returns:
        dict con execution_score (0-100), category, factors, issues
    """
    factors = {}
    issues = []

    # ── Factor 1: Fill latency ──────────────────────────────────────────
    market = next((e for e in events if e.get("ev") == "market_filled"), None)
    if market:
        latency = market.get("latency_ms")
        factors["fill_latency_ms"] = latency
        if latency and latency > 500:
            issues.append(f"fill latency outlier {latency}ms (esperado <150ms)")

    # ── Factor 2: mt5_action_failed events (BUG concreto) ───────────────
    failures = [e for e in events if e.get("ev") == "mt5_action_failed"]
    factors["mt5_failures_count"] = len(failures)
    for f in failures:
        issues.append(
            f"mt5_action_failed: {f.get('kind')} ticket={f.get('ticket')} "
            f"tras {f.get('attempts')} intentos ({f.get('reason')})"
        )

    # ── Factor 3: Ghost signal (no signal_closed) ───────────────────────
    closed = next((e for e in events if e.get("ev") == "signal_closed"), None)
    factors["is_ghost"] = bool(market) and not bool(closed)
    if factors["is_ghost"]:
        issues.append("ghost: market_filled OK pero sin signal_closed")

    # ── Factor 4: BE coverage (mgmt aplicado pero be_armed ausente) ──────
    move_sl_be_msgs = [
        e for e in events
        if e.get("ev") == "mgmt_msg"
        and e.get("action") == "MOVE_SL_TO_BE"
        and e.get("will_apply")
    ]
    be_armed_evs = [e for e in events if e.get("ev") == "be_armed"]
    factors["move_sl_be_msgs"] = len(move_sl_be_msgs)
    factors["be_armed_events"] = len(be_armed_evs)
    if move_sl_be_msgs and not be_armed_evs and not failures:
        issues.append(
            f"{len(move_sl_be_msgs)} MOVE_SL_TO_BE marcado applied pero "
            f"sin be_armed event correspondiente — ¿BE no se aplicó?"
        )

    # ── Factor 5: DCA race condition residual ───────────────────────────
    dca_filled = [e for e in events if e.get("ev") == "dca_filled"]
    dca_keys = [
        (d.get("ts", "")[:23], d.get("position_index"), d.get("level"))
        for d in dca_filled
    ]
    dupes = len(dca_keys) - len(set(dca_keys))
    factors["dca_dupes"] = dupes
    if dupes > 0:
        issues.append(f"{dupes} DCA fills duplicados — race condition (Bug 1 fix?)")

    # ── Factor 6: Adverse slippage en DCAs ──────────────────────────────
    dca_slips = [
        e.get("slippage_favorable_usd")
        for e in dca_filled
        if e.get("slippage_favorable_usd") is not None
    ]
    if dca_slips:
        adverse_slips = [s for s in dca_slips if s < -0.5]
        favorable_slips = [s for s in dca_slips if s > 0]
        factors["adverse_slippage_count"] = len(adverse_slips)
        factors["favorable_slippage_count"] = len(favorable_slips)
        if adverse_slips:
            avg_adv = sum(adverse_slips) / len(adverse_slips)
            issues.append(
                f"{len(adverse_slips)} DCA fills con slippage adverso "
                f"(media ${avg_adv:.2f}) — ¿conexión MT5 lenta?"
            )

    # ── Factor 7: Predictor accuracy ────────────────────────────────────
    pred_validations = [e for e in events if e.get("ev") == "prediction_validated"]
    if pred_validations:
        worst = max(pred_validations, key=lambda x: x.get("max_diff_usd", 0) or 0)
        max_diff = worst.get("max_diff_usd", 0) or 0
        factors["predictor_max_diff_usd"] = max_diff
        if max_diff > 5:
            issues.append(
                f"predictor desviado ${max_diff:.2f} de TPs reales — "
                f"considerar recalibrar predictor"
            )

    # ── Factor 8: Mgmt actions coverage (informativo, no penaliza) ───────
    mgmt_msgs = [e for e in events if e.get("ev") == "mgmt_msg"]
    applied = sum(1 for e in mgmt_msgs if e.get("will_apply"))
    factors["mgmt_msgs_total"] = len(mgmt_msgs)
    factors["mgmt_msgs_applied"] = applied
    factors["mgmt_msgs_ignored"] = len(mgmt_msgs) - applied

    # Ambiguous notifications no son issues — son comportamiento correcto
    ambig = sum(1 for e in mgmt_msgs if e.get("ambiguous_notified"))
    factors["ambiguous_notifications"] = ambig

    # ── Factor 9: tg_to_bot_ms (informativo, no penaliza al bot) ─────────
    sig_recv = next((e for e in events if e.get("ev") == "signal_received"), None)
    if sig_recv:
        tg_delay = sig_recv.get("tg_to_bot_ms")
        factors["tg_to_bot_ms"] = tg_delay
        # No penalizamos — Telegram no es nuestro

    # ── Cálculo del score ──────────────────────────────────────────────
    # Empezamos en 100. Restamos por cada issue real (no por features).
    score = 100.0

    if factors.get("fill_latency_ms") and factors["fill_latency_ms"] > 500:
        # Hasta -20 puntos según gravedad
        score -= min(20, (factors["fill_latency_ms"] - 500) / 100)

    # mt5_action_failed: cada uno -25, máximo -50
    score -= min(50, 25 * factors.get("mt5_failures_count", 0))

    # Ghost: -30 (significa que perdimos tracking)
    if factors.get("is_ghost"):
        score -= 30

    # DCA dupes: -15 cada uno (race condition real)
    score -= 15 * factors.get("dca_dupes", 0)

    # Adverse slippage: -3 por cada DCA con slip > $0.5
    score -= 3 * factors.get("adverse_slippage_count", 0)

    # BE missing: -20 si hay mgmt aplicado pero no be_armed
    if move_sl_be_msgs and not be_armed_evs and not failures:
        score -= 20

    # Predictor muy fuera: -5 (informativo, no crítico)
    if factors.get("predictor_max_diff_usd", 0) > 5:
        score -= 5

    score = max(0, round(score, 1))

    # Categorización
    if score >= 90:
        category = "PERFECT_EXECUTION"
    elif score >= 70:
        category = "GOOD_EXECUTION"
    elif score >= 50:
        category = "DEGRADED_EXECUTION"
    else:
        category = "FAULTY_EXECUTION"

    return {
        "execution_score": score,
        "category": category,
        "factors": factors,
        "issues": issues,
    }


def aggregate_execution_stats(classifications: list[dict]) -> dict:
    """Agrega stats de ejecución a nivel canal/sesión."""
    if not classifications:
        return {"n": 0}

    cats = {}
    scores = []
    for c in classifications:
        cat = c.get("category", "?")
        cats[cat] = cats.get(cat, 0) + 1
        if c.get("execution_score") is not None:
            scores.append(c["execution_score"])

    avg_score = round(sum(scores) / len(scores), 1) if scores else None
    n = len(classifications)
    n_perfect = cats.get("PERFECT_EXECUTION", 0)
    n_faulty = cats.get("FAULTY_EXECUTION", 0)

    return {
        "n": n,
        "categories": cats,
        "avg_execution_score": avg_score,
        "perfect_pct": round(n_perfect / n * 100, 1) if n else 0,
        "faulty_pct": round(n_faulty / n * 100, 1) if n else 0,
    }


def find_urgent_cases(metrics: list[dict]) -> list[dict]:
    """Encuentra casos URGENTES: bot falló Y outcome fue negativo.

    Estos son los casos donde el BOT (no el canal) probablemente costó
    dinero. Prioridad alta para investigar y arreglar.

    Args:
        metrics: lista de dicts con keys 'sig_id', 'pnl', 'execution' (dict).

    Returns:
        Lista de casos urgentes ordenados por mayor pérdida.
    """
    urgent = []
    for m in metrics:
        exec_q = m.get("execution") or {}
        cat = exec_q.get("category", "")
        pnl = m.get("pnl") or 0
        if cat in ("FAULTY_EXECUTION", "DEGRADED_EXECUTION") and pnl < 0:
            urgent.append({
                "sig_id": m["sig_id"],
                "channel": m.get("channel"),
                "pnl": pnl,
                "execution_score": exec_q.get("execution_score"),
                "category": cat,
                "issues": exec_q.get("issues", []),
            })
    return sorted(urgent, key=lambda x: x["pnl"])  # más negativo primero


def find_pattern_correlations(metrics: list[dict]) -> list[str]:
    """Detecta correlaciones interesantes para investigar.

    Por ejemplo: ¿adverse slippage es más frecuente a ciertas horas?
    ¿FAULTY_EXECUTION correlaciona con tg_delay alto?

    Devuelve lista de hipótesis con los datos que las soportan.
    """
    findings = []
    if not metrics:
        return findings

    # Hipótesis 1: FAULTY correlaciona con hora del día
    by_hour_faulty = {}
    by_hour_total = {}
    for m in metrics:
        h = m.get("received_hour")
        if h is None:
            continue
        by_hour_total[h] = by_hour_total.get(h, 0) + 1
        cat = (m.get("execution") or {}).get("category", "")
        if cat in ("FAULTY_EXECUTION", "DEGRADED_EXECUTION"):
            by_hour_faulty[h] = by_hour_faulty.get(h, 0) + 1

    if by_hour_faulty:
        worst_hour = max(by_hour_faulty.items(), key=lambda x: x[1])
        if worst_hour[1] >= 2 and worst_hour[1] >= by_hour_total.get(worst_hour[0], 1) * 0.5:
            findings.append(
                f"Hora {worst_hour[0]:02d}h UTC concentra {worst_hour[1]} "
                f"degraded/faulty (>50% de las {by_hour_total[worst_hour[0]]} "
                f"señales de esa hora) — investigar"
            )

    # Hipótesis 2: tg_delay alto correlaciona con FAULTY
    high_delay_faulty = 0
    high_delay_total = 0
    for m in metrics:
        exec_q = m.get("execution") or {}
        tg_d = exec_q.get("factors", {}).get("tg_to_bot_ms")
        if tg_d and tg_d > 10000:  # >10s
            high_delay_total += 1
            if exec_q.get("category") in ("FAULTY_EXECUTION", "DEGRADED_EXECUTION"):
                high_delay_faulty += 1
    if high_delay_total >= 3 and high_delay_faulty / high_delay_total > 0.5:
        findings.append(
            f"De {high_delay_total} señales con tg_delay >10s, "
            f"{high_delay_faulty} ({high_delay_faulty*100//high_delay_total}%) "
            f"tuvieron ejecución degradada — el delay correlaciona con "
            f"problemas de ejecución"
        )

    # Hipótesis 3: por canal
    by_ch = {}
    for m in metrics:
        ch = m.get("channel", "?")
        by_ch.setdefault(ch, []).append(m)
    for ch, ms in by_ch.items():
        agg = aggregate_execution_stats(
            [m.get("execution", {}) for m in ms]
        )
        if agg.get("faulty_pct", 0) > 30:
            findings.append(
                f"Canal {ch} tiene {agg['faulty_pct']}% señales con ejecución "
                f"degradada/faulty — bot tiene problema con este canal"
            )

    return findings
