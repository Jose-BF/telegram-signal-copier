"""
⚠️ DEPRECATED ⚠️ — usar bot_execution_quality.py en su lugar.

Este módulo conceptualmente equivocaba el alcance. Penalizaba "drawdown
profundo = lucky win" cuando en realidad el drawdown intermedio es FEATURE
del DCA, no bug. El canal hace análisis de mercado; el bot solo ejecuta.
Ver bot_execution_quality.py para la versión correcta que solo evalúa lo
que el bot controla.

Mantengo el archivo para no romper imports antiguos. NO USAR en código nuevo.

────────────── DOCUMENTACIÓN HISTÓRICA (texto original) ──────────────

SKILL vs LUCK — clasifica cada trade cerrado según si el resultado fue por
habilidad del bot o por suerte del mercado.

Insight clave del usuario: "muchas operaciones acaban en positivo pero por
pura suerte, no porque el bot sea ultra preciso". El WR headline puede ser
engañoso si la mitad de los wins son recuperaciones improbables.

Para distinguir, calculamos para CADA trade cerrado:

  1. MAE/Final ratio: cuán profundo fue el drawdown vs el resultado final.
     • <1: nunca estuvo en pérdida → SKILL (entrada buena)
     • 1-3: drawdown moderado, recuperación normal
     • >3: drawdown profundo, recuperación = LUCK
     • >5: muy profundo, casi pérdida → MUY LUCKY

  2. Time in profit %: % del trade pasado en P&L positivo.
     • >70%: bot fue acertado desde el inicio
     • 40-70%: oscilación normal
     • <40%: estaba perdiendo casi todo el tiempo, recuperó al final

  3. Drawdown vs SL distance: cuán cerca del SL estuvo.
     • <30%: cómodo
     • 30-70%: stress
     • >70%: casi stop-out (escapó por los pelos)

  4. MFE/Final ratio: cuán cerca del peak cerramos.
     • 1-1.5: cerramos en/cerca del máximo (skill timing)
     • 1.5-3: dejamos algo en la mesa
     • >3: cerramos MUY lejos del peak (early or late exit)

  5. Channel adherence: si el trader pidió mgmt actions, ¿se aplicaron?
     ¿Fueron las que llevaron al cierre? O cerró por SL/TP independiente.

SKILL_SCORE final (0-100):
  Score alto = trade bien ejecutado, bot tomó decisiones acertadas
  Score bajo = resultado por suerte (positivo o negativo) pese al bot

Esto NO juzga el outcome (positivo/negativo) sino el PROCESO. Un loss
limpio (entry bueno, SL hit, sin chances) es alto skill score. Un win
chapucero (deep DD recuperado por suerte) es bajo skill score.

Uso:
    from analysis.skill_vs_luck import classify_trade
    classification = classify_trade(events_for_signal)
    # → {"skill_score": 78, "category": "SKILL_WIN", "factors": {...}}
"""
from typing import Optional


def _floating_pl_curve(events: list[dict]) -> list[tuple[float, float]]:
    """Extrae [(elapsed_s, pl), ...] de los floating_pl_snapshot events.

    También usa los positions_closed_by_mt5 al final como punto de cierre.
    Si no hay snapshots, devuelve curva mínima [(0, 0), (final, pnl_final)].
    """
    snapshots = [e for e in events if e.get("ev") == "floating_pl_snapshot"]
    curve = [(e.get("elapsed_s", 0), e.get("pl", 0)) for e in snapshots]
    return sorted(curve)


def _extract_metrics_from_curve(curve: list[tuple[float, float]],
                                final_pnl: Optional[float]) -> dict:
    """Calcula MFE, MAE, time_in_profit_pct, etc. desde la curva."""
    out = {
        "mfe": 0.0, "mae": 0.0, "mfe_at_s": None, "mae_at_s": None,
        "time_in_profit_pct": None, "samples": len(curve),
    }
    if not curve:
        return out

    pls = [pl for _, pl in curve]
    out["mfe"] = max(pls + [final_pnl or 0])
    out["mae"] = min(pls + [final_pnl or 0])

    for ts, pl in curve:
        if pl == out["mfe"] and out["mfe_at_s"] is None:
            out["mfe_at_s"] = ts
        if pl == out["mae"] and out["mae_at_s"] is None:
            out["mae_at_s"] = ts

    n_positive = sum(1 for _, pl in curve if pl > 0)
    if curve:
        out["time_in_profit_pct"] = round(n_positive / len(curve) * 100, 1)

    return out


def _ratio(num: float, den: float) -> Optional[float]:
    if den == 0 or den is None:
        return None
    return abs(num / den) if den != 0 else None


def classify_trade(events: list[dict]) -> dict:
    """Clasifica un trade según SKILL vs LUCK.

    Args:
        events: lista de eventos del JSONL para una sola signal_id.

    Returns:
        dict con:
          • skill_score: 0-100
          • category: SKILL_WIN | LUCKY_WIN | NORMAL_WIN | NORMAL_LOSS |
                       SKILL_LOSS | UNLUCKY_LOSS | OPEN | UNKNOWN
          • factors: dict con métricas individuales
          • reasoning: lista de razones
    """
    closed_evs = [e for e in events if e.get("ev") == "signal_closed"]
    if not closed_evs:
        return {"skill_score": None, "category": "OPEN",
                "factors": {}, "reasoning": ["señal no cerrada todavía"]}

    closed = closed_evs[-1]
    final_pnl = closed.get("total_pl", 0) or 0

    # Curva de P&L
    curve = _floating_pl_curve(events)
    metrics = _extract_metrics_from_curve(curve, final_pnl)

    # Recuperar SL para calcular DD vs SL distance
    sl_arrived = next((e for e in events if e.get("ev") == "sl_arrived"), None)
    market = next((e for e in events if e.get("ev") == "market_filled"), None)
    sl_distance_usd = None
    if sl_arrived and market:
        try:
            sl = sl_arrived.get("sl")
            entry = market.get("price")
            # Estimar dólar perdido si llegamos a SL: 1 lot=100$/$, 0.01=1$/$
            # (simplificación, real depende de lot_multiplier)
            sl_distance_price = abs(entry - sl) if (entry and sl) else None
        except Exception:
            sl_distance_price = None

    factors = {
        "final_pnl": round(final_pnl, 2),
        "mfe": round(metrics["mfe"], 2),
        "mae": round(metrics["mae"], 2),
        "time_in_profit_pct": metrics["time_in_profit_pct"],
        "mae_to_final_ratio": (
            _ratio(metrics["mae"], final_pnl) if final_pnl != 0 else None
        ),
        "mfe_to_final_ratio": (
            _ratio(metrics["mfe"], final_pnl) if final_pnl != 0 else None
        ),
        "snapshots_count": metrics["samples"],
        "tag": closed.get("tag"),
    }

    # ── Cálculo del skill_score (0-100) ──
    # Empezamos en 50 (neutro). Sumamos/restamos según factores.
    score = 50.0
    reasoning = []

    # Factor 1: time in profit (peso máximo +/- 25)
    tip = metrics["time_in_profit_pct"]
    if tip is not None:
        if tip >= 80:
            score += 25
            reasoning.append(f"alto time_in_profit ({tip}%) → buena entrada")
        elif tip >= 60:
            score += 15
            reasoning.append(f"time_in_profit decente ({tip}%)")
        elif tip >= 40:
            score += 0
            reasoning.append(f"time_in_profit medio ({tip}%) → oscilación")
        elif tip >= 20:
            score -= 15
            reasoning.append(f"time_in_profit bajo ({tip}%) → mayor parte en loss")
        else:
            score -= 25
            reasoning.append(f"time_in_profit muy bajo ({tip}%) → siempre perdiendo")

    # Factor 2: MAE vs final (drawdown profundidad relativa)
    mae_ratio = factors["mae_to_final_ratio"]
    if mae_ratio is not None and final_pnl > 0:
        # Win con drawdown
        if mae_ratio < 0.5:
            score += 15
            reasoning.append(f"DD bajo ({mae_ratio:.1f}x final) → win clean")
        elif mae_ratio < 1.5:
            score += 0
            reasoning.append(f"DD moderado ({mae_ratio:.1f}x final)")
        elif mae_ratio < 3:
            score -= 15
            reasoning.append(f"DD profundo ({mae_ratio:.1f}x final) → recuperación")
        else:
            score -= 30
            reasoning.append(f"DD MUY profundo ({mae_ratio:.1f}x final) → LUCKY")
    elif mae_ratio is not None and final_pnl < 0:
        # Loss — comparamos MFE
        mfe_ratio = factors["mfe_to_final_ratio"]
        if mfe_ratio is not None:
            if mfe_ratio > 2:
                score -= 20
                reasoning.append(f"loss con MFE alto → no cerramos en profit")
            elif mfe_ratio < 0.3:
                score += 10
                reasoning.append("loss limpio (apenas tocó profit)")

    # Factor 3: MFE vs final (timing del cierre)
    if final_pnl > 0:
        mfe_r = factors["mfe_to_final_ratio"]
        if mfe_r is not None:
            if mfe_r <= 1.2:
                score += 10
                reasoning.append("cerró cerca del peak ✓")
            elif mfe_r > 3:
                score -= 5
                reasoning.append(f"cerró muy lejos del peak ({mfe_r:.1f}x)")

    # Factor 4: tag indica algo específico
    tag = factors["tag"]
    if tag == "WIN_CLEAN":
        score += 10
        reasoning.append("tag WIN_CLEAN → ejecución limpia")
    elif tag == "LOSS_CLEAN":
        score += 5  # un loss limpio es mejor que uno chapucero
        reasoning.append("tag LOSS_CLEAN → loss controlado")
    elif tag == "WIN_SCARY":
        score -= 15
        reasoning.append("tag WIN_SCARY → drawdown grande")
    elif tag == "LOSS_REVERSAL":
        score -= 10
        reasoning.append("tag LOSS_REVERSAL → BE habría salvado")
    elif tag == "MANUAL":
        score -= 5
        reasoning.append("tag MANUAL → usuario tuvo que intervenir")

    score = max(0, min(100, round(score, 1)))

    # Categorización final
    if final_pnl > 0:
        if score >= 70:
            category = "SKILL_WIN"
        elif score >= 50:
            category = "NORMAL_WIN"
        else:
            category = "LUCKY_WIN"
    elif final_pnl < 0:
        if score >= 70:
            category = "SKILL_LOSS"  # loss inevitable, bot bien
        elif score >= 50:
            category = "NORMAL_LOSS"
        else:
            category = "UNLUCKY_LOSS"  # loss + bot mal = peor caso
    else:
        category = "BREAKEVEN"

    return {
        "skill_score": score,
        "category": category,
        "factors": factors,
        "reasoning": reasoning,
    }


def aggregate_skill_stats(classifications: list[dict]) -> dict:
    """Agrega stats de skill a nivel de canal/sesión.

    Devuelve: WR real (skill wins / closed), avg skill score, distribución
    de categorías, etc.
    """
    closed = [c for c in classifications if c.get("category") != "OPEN"]
    if not closed:
        return {"n_closed": 0}

    cats = {}
    for c in closed:
        cat = c.get("category", "?")
        cats[cat] = cats.get(cat, 0) + 1

    scores = [c["skill_score"] for c in closed if c.get("skill_score") is not None]
    avg_score = round(sum(scores) / len(scores), 1) if scores else None

    n_skill_wins = cats.get("SKILL_WIN", 0)
    n_lucky_wins = cats.get("LUCKY_WIN", 0)
    n_normal_wins = cats.get("NORMAL_WIN", 0)
    n_total_wins = n_skill_wins + n_lucky_wins + n_normal_wins
    n_total = len(closed)

    headline_wr = round(n_total_wins / n_total * 100, 1) if n_total else 0
    skill_wr = round(n_skill_wins / n_total * 100, 1) if n_total else 0
    lucky_pct_of_wins = round(n_lucky_wins / n_total_wins * 100, 1) if n_total_wins else 0

    return {
        "n_closed": n_total,
        "categories": cats,
        "avg_skill_score": avg_score,
        "headline_wr_pct": headline_wr,
        "skill_wr_pct": skill_wr,
        "lucky_pct_of_wins": lucky_pct_of_wins,
        "summary": (
            f"Headline WR {headline_wr}% pero SKILL WR {skill_wr}%. "
            f"De {n_total_wins} wins, {n_lucky_wins} fueron por suerte "
            f"({lucky_pct_of_wins}%). Avg skill score: {avg_score}/100."
        ),
    }
