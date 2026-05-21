"""
analyze_decisions.py — Análisis EXHAUSTIVO punto por punto para tomar decisiones
                       informadas sobre cada propuesta de mejora.

Cada sección responde a UNA pregunta concreta con números reales.

Estructura:
  P1) HIGH RISK: ¿lot/2 es rentable o mejor ignorar?
  P2) Re-enter: ¿realmente perdemos dinero?
  P3) Delay condicional: cuántas señales SÍ llegan completas (sin necesitar delay)
  P4) Post-SL momentum: ¿cómo llegar a TP5 sin sobre-exposición?
  P5) Burst sin delay: ¿podemos detectar la primera RETROACTIVAMENTE?
  P6) 14h UTC SELL: cálculo neto de P/L
  P7) DCA optimization: ¿cuándo el DCA salva trades, cuándo los empeora?

Modelo de costes (Vantage Standard XAUUSD):
  - Spread:     $0.30 por trade
  - Slippage:   $0.50 promedio (más en momentos de noticia)
  - Comisión:   $0
  - TOTAL:      $0.80 por trade abierto

Modelo de lot:
  - LOT_BASE = 0.01 → 1 oz → 1$ por dólar de movimiento
"""

import json
import re
import sys
import io
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

CANAL_2_ID = 2614601304
JSON_PATH = r"C:\Users\josea\Downloads\Telegram Desktop\DataExport_2026-04-22\result.json"
CUTOFF_DATE = datetime(2025, 1, 1)

COST_PER_TRADE = 0.80
LOT_BASE = 0.01


def extract_text(field) -> str:
    if isinstance(field, str):
        return field
    if isinstance(field, list):
        out = []
        for p in field:
            if isinstance(p, str):
                out.append(p)
            elif isinstance(p, dict):
                out.append(p.get("text", ""))
        return "".join(out)
    return ""


def parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def section(title):
    print(f"\n{'='*78}")
    print(f"  {title}")
    print(f"{'='*78}\n")


def subsection(title):
    print(f"\n  ─── {title} ───\n")


# ═════════════════════════════════════════════════════════════════════════════
#  Carga y construcción de señales
# ═════════════════════════════════════════════════════════════════════════════

def load_msgs():
    with open(JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)
    chats = data.get("chats", {}).get("list", [])
    c2 = next((c for c in chats if c.get("id") == CANAL_2_ID), None)
    return c2.get("messages", [])


def build_signals(msgs):
    sigs = []
    replies_to = defaultdict(list)
    for m in msgs:
        if "reply_to_message_id" in m:
            replies_to[m["reply_to_message_id"]].append(m)

    for m in msgs:
        txt = extract_text(m.get("text", ""))
        if not txt:
            continue
        t_upper = txt.upper()
        if not ("BUY NOW" in t_upper or "SELL NOW" in t_upper):
            continue
        try:
            dt = parse_dt(m["date"])
        except Exception:
            continue
        if dt < CUTOFF_DATE:
            continue

        direction = "BUY" if "BUY" in t_upper else "SELL"

        # Rango
        rng = None
        m_rng = re.search(r"(\d{4}(?:\.\d+)?)\s*[-–]\s*(\d{4}(?:\.\d+)?)", txt)
        if m_rng:
            try:
                lo, hi = float(m_rng.group(1)), float(m_rng.group(2))
                if lo > hi:
                    lo, hi = hi, lo
                if 1500 <= lo <= 5000 and 1500 <= hi <= 5000:
                    rng = (lo, hi)
            except ValueError:
                pass

        # TPs y SL
        tps = []
        for m_tp in re.finditer(r"TP\d+\s*[:\s]\s*(\d{3,5}(?:\.\d{1,3})?)\b", txt, re.IGNORECASE):
            try:
                tps.append(float(m_tp.group(1)))
            except ValueError:
                pass

        sl = None
        m_sl = re.search(r"SL\s*[:\s]\s*(\d{3,5}(?:\.\d{1,3})?)\b", txt, re.IGNORECASE)
        if m_sl:
            try:
                sl = float(m_sl.group(1))
            except ValueError:
                pass

        # Replies
        rs = []
        for r in replies_to.get(m["id"], []):
            r_txt = extract_text(r.get("text", ""))
            try:
                r_dt = parse_dt(r["date"])
            except Exception:
                continue
            rs.append({"text": r_txt, "dt": r_dt, "txt_lower": r_txt.lower()})
        rs.sort(key=lambda x: x["dt"])

        # Outcome
        max_tp_hit = 0
        sl_hit = False
        be_set = False
        time_to_sl = None
        time_to_first_tp = None
        time_to_last_tp = None
        re_enter_mentioned = False
        worst_entry_mentioned = False

        for r in rs:
            t = r["txt_lower"]
            tp_match = re.search(r"tp[\s_]*(\d)", t)
            if tp_match and ("hit" in t or "tap" in t or "reach" in t or
                           "smash" in t or "secur" in t or "✅" in r["text"] or
                           "done" in t or "closed" in t):
                tp_n = int(tp_match.group(1))
                if tp_n > max_tp_hit:
                    max_tp_hit = tp_n
                    delta = (r["dt"] - dt).total_seconds() / 60
                    if time_to_first_tp is None:
                        time_to_first_tp = delta
                    time_to_last_tp = delta

            if "sl" in t and ("hit" in t or "out" in t or "❌" in r["text"]):
                sl_hit = True
                if time_to_sl is None:
                    time_to_sl = (r["dt"] - dt).total_seconds() / 60

            if re.search(r"\bbreak\s*even\b|\bbe\b|\bbreakeven\b", t):
                be_set = True

            if re.search(r"\bre[\s-]*enter|still\s+valid", t):
                re_enter_mentioned = True

            if "worst entr" in t or "lowest" in t or "highest" in t:
                worst_entry_mentioned = True

        is_high_risk = bool(re.search(r"high\s*risk", txt, re.IGNORECASE))

        # ¿El mensaje LLEGA COMPLETO o necesitará espera?
        first_msg_complete = bool(rng and tps and sl)

        sigs.append({
            "id": m["id"],
            "dt": dt,
            "date": dt.strftime("%Y-%m-%d"),
            "hour": dt.hour,
            "weekday": dt.strftime("%a"),
            "direction": direction,
            "range": rng,
            "range_size": (rng[1] - rng[0]) if rng else None,
            "tps": tps,
            "sl": sl,
            "num_tps": len(tps),
            "replies": rs,
            "max_tp_hit": max_tp_hit,
            "sl_hit": sl_hit,
            "be_set": be_set,
            "time_to_sl": time_to_sl,
            "time_to_first_tp": time_to_first_tp,
            "time_to_last_tp": time_to_last_tp,
            "is_high_risk": is_high_risk,
            "re_enter_mentioned": re_enter_mentioned,
            "worst_entry_mentioned": worst_entry_mentioned,
            "first_msg_complete": first_msg_complete,
            "raw_text": txt,
        })

    sigs.sort(key=lambda s: s["dt"])
    return sigs


# ═════════════════════════════════════════════════════════════════════════════
#  CÁLCULO DE P/L de una señal con lógica de bot
# ═════════════════════════════════════════════════════════════════════════════

def signal_pl_escalonado(sig, n_positions=4, lot_mult=1.0, costs=True,
                          extend_to_tp5=False):
    """
    Calcula P/L con lógica del bot:
      - n_positions abiertas (1 entry + DCA)
      - cada posición i cierra en TP[i] (escalonado)
      - si SL → todas pierden
      - si extend_to_tp5=True y posición no llegó a su TP escalonado, intenta TP5
    """
    if not sig["range"] or not sig["tps"] or sig["sl"] is None:
        return None  # no se puede simular

    rl, rh = sig["range"]
    direction = sig["direction"]
    entry = rh if direction == "BUY" else rl
    sl = sig["sl"]
    tps = sig["tps"]
    sl_dist = abs(entry - sl)

    cost = COST_PER_TRADE * lot_mult if costs else 0

    if sig["sl_hit"]:
        # Todas las posiciones pierden
        # Asumimos las posiciones se abren en escalera dentro del rango
        # Pérdida promedio = sl_dist + (rango/2 para las posiciones DCA promedio)
        avg_loss = sl_dist + (sig["range_size"] or 0) / 2
        return -(avg_loss * lot_mult * n_positions + cost * n_positions)

    if sig["max_tp_hit"] == 0:
        # Sin confirmación. Asumir BE (sin ganancia, costes pagados)
        return -cost * n_positions

    # Hay TPs alcanzados
    pl = 0.0
    for i in range(n_positions):
        tp_idx = min(i, len(tps) - 1)

        if extend_to_tp5 and len(tps) >= 5:
            # En modo extended, TODAS las posiciones intentan TP5
            target_tp_idx = len(tps) - 1  # último TP (TP5)
            target_tp = tps[target_tp_idx]
            if sig["max_tp_hit"] >= len(tps):  # llegó al último
                pl += abs(target_tp - entry) * lot_mult - cost
            elif tp_idx < sig["max_tp_hit"]:
                # Cerró en su TP escalonado normal
                pl += abs(tps[tp_idx] - entry) * lot_mult - cost
            else:
                # No llegó a su TP escalonado → BE (gracias al modo trailing)
                pl += -cost
        else:
            # Modo normal escalonado
            if tp_idx < sig["max_tp_hit"]:
                pl += abs(tps[tp_idx] - entry) * lot_mult - cost
            else:
                # No llegó a TP escalonado → BE
                pl += -cost
    return pl


# ═════════════════════════════════════════════════════════════════════════════
#  P1) HIGH RISK: ¿lot/2 es rentable?
# ═════════════════════════════════════════════════════════════════════════════

def analyze_high_risk_ev(sigs):
    section("P1) HIGH RISK — ¿lot/2 es rentable o mejor ignorar?")

    high_risk = [s for s in sigs if s["is_high_risk"]]
    print(f"  Señales HIGH RISK detectadas: {len(high_risk)}")

    if not high_risk:
        print("  Sin muestra suficiente.")
        return

    # Escenarios: ignorar (P/L=0), tomar lot completo, tomar lot/2
    scenarios = {
        "Ignorar":         {"lot_mult": 0.0, "skip": True},
        "Lot completo":    {"lot_mult": 1.0, "skip": False},
        "Lot a la mitad":  {"lot_mult": 0.5, "skip": False},
    }

    print(f"\n  {'Escenario':<20} {'P/L total':>12} {'P/L medio':>12} {'WR':>6} {'Max DD':>10}")
    print(f"  {'-'*70}")
    for name, cfg in scenarios.items():
        if cfg["skip"]:
            print(f"  {name:<20} {'$0':>12} {'$0':>12} {'N/A':>6} {'$0':>10}")
            continue
        pls = []
        for s in high_risk:
            pl = signal_pl_escalonado(s, n_positions=4, lot_mult=cfg["lot_mult"], costs=True)
            if pl is not None:
                pls.append(pl)
        if not pls:
            continue
        total = sum(pls)
        avg = total / len(pls)
        wins = sum(1 for p in pls if p > 0)
        wr = wins / len(pls) * 100
        # Max drawdown sequential
        equity = 0
        peak = 0
        max_dd = 0
        for p in pls:
            equity += p
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd
        print(f"  {name:<20} ${total:+11.0f} ${avg:+11.2f} {wr:5.0f}% ${max_dd:9.0f}")

    # Decisión recomendada
    print(f"\n  💡 Para que 'lot/2' sea PREFERIBLE a 'ignorar': P/L total con lot/2 > 0")
    print(f"     Para que 'lot completo' sea PREFERIBLE: P/L lot completo > P/L lot/2 (y > 0)")


# ═════════════════════════════════════════════════════════════════════════════
#  P2) Re-enter: ¿realmente perdemos dinero?
# ═════════════════════════════════════════════════════════════════════════════

def analyze_reenter_ev(sigs):
    section("P2) Re-enter — ¿realmente perdemos dinero o solo es WR menor?")

    reenter = [s for s in sigs if s["re_enter_mentioned"]]
    print(f"  Señales con re-enter mencionado: {len(reenter)}")

    if not reenter:
        return

    # Tomar todas con escalonado vs no tomar
    pls = []
    for s in reenter:
        pl = signal_pl_escalonado(s, n_positions=4, lot_mult=1.0, costs=True)
        if pl is not None:
            pls.append(pl)

    if pls:
        total = sum(pls)
        avg = total / len(pls)
        wins = sum(1 for p in pls if p > 0)
        wr = wins / len(pls) * 100
        worst = min(pls)
        best = max(pls)
        print(f"\n  Si TOMAMOS las re-enter:")
        print(f"    P/L total: ${total:+.0f}")
        print(f"    P/L medio: ${avg:+.2f}/señal")
        print(f"    WR: {wr:.0f}%  ({wins}/{len(pls)})")
        print(f"    Peor caso: ${worst:.0f}   Mejor caso: ${best:.0f}")

        if total > 0:
            print(f"\n  ✅ Total POSITIVO → tomarlas igual sigue siendo rentable")
            print(f"     PERO: avg/señal vs base. Comparemos con base rate.")
        else:
            print(f"\n  ❌ Total NEGATIVO → IGNORAR es la decisión correcta")
            print(f"     Saltarlas evita ${-total:.0f} de pérdidas")

    # Comparar con base rate
    base_pls = []
    for s in sigs:
        if s["re_enter_mentioned"]:
            continue
        pl = signal_pl_escalonado(s, n_positions=4, lot_mult=1.0, costs=True)
        if pl is not None:
            base_pls.append(pl)
    if base_pls:
        base_avg = sum(base_pls) / len(base_pls)
        print(f"\n  Comparativa con base rate:")
        print(f"    Re-enter:  ${avg:+.2f}/señal  ({wr:.0f}% WR)")
        print(f"    Base:      ${base_avg:+.2f}/señal")
        print(f"    Diferencia: ${avg - base_avg:+.2f}/señal (re-enter pierde {(base_avg-avg):.2f}$ vs base)")


# ═════════════════════════════════════════════════════════════════════════════
#  P3) Delay condicional: ¿cuántas señales SÍ llegan completas?
# ═════════════════════════════════════════════════════════════════════════════

def analyze_delay_conditional(sigs, msgs):
    section("P3) Delay CONDICIONAL — ¿cuántas señales llegan ya completas?")

    # Para cada señal Canal 2, ver si el primer mensaje (sin edits) ya tenía
    # rango + TPs + SL. Como solo tenemos el texto FINAL, vamos a usar
    # un proxy: si el mensaje editado tiene <1min de delay = era casi instantáneo

    # Cargamos los mensajes raw para ver edit_delay
    n_complete = 0
    n_incomplete = 0
    n_no_data = 0

    for s in sigs:
        # Como el JSON solo guarda texto final, usaremos el dato de "raw_text completo"
        # como aproximación: si el texto FINAL tiene rango+TPs+SL → eventualmente está completo
        if s["range"] and s["tps"] and s["sl"]:
            n_complete += 1
        elif s["range"] or s["tps"] or s["sl"]:
            n_incomplete += 1
        else:
            n_no_data += 1

    n = len(sigs)
    print(f"  Total señales analizadas: {n}")
    print(f"\n  Estado FINAL del mensaje BUY/SELL NOW:")
    print(f"    Completo (rango+TPs+SL): {n_complete} ({n_complete/n*100:.0f}%)")
    print(f"    Parcial:                 {n_incomplete} ({n_incomplete/n*100:.0f}%)")
    print(f"    Vacío (solo BUY NOW):    {n_no_data} ({n_no_data/n*100:.0f}%)")

    # AHORA: cuántas tienen el TEXTO con TODO desde el inicio
    # Esto requiere mirar el mensaje raw y verificar si tiene rango/TPs/SL
    # ya en el texto que llegó. Pero el JSON solo guarda el FINAL.
    # → Usamos el edit_delay como proxy: si edit_delay < 5s → probablemente
    #   no añadieron nada importante en el edit.

    msgs_by_id = {m["id"]: m for m in msgs}
    fast_complete = 0
    needs_wait = 0
    for s in sigs:
        m = msgs_by_id.get(s["id"])
        if not m:
            continue
        if "edited" not in m:
            # No editado → texto original = texto final
            if s["range"] and s["tps"] and s["sl"]:
                fast_complete += 1
            else:
                needs_wait += 1
        else:
            try:
                created = parse_dt(m["date"])
                edited = parse_dt(m["edited"])
                delay = (edited - created).total_seconds()
            except Exception:
                continue
            if delay < 5:
                # Edit muy rápido → probablemente solo correción de typo, contenido similar
                if s["range"] and s["tps"] and s["sl"]:
                    fast_complete += 1
                else:
                    needs_wait += 1
            else:
                # Edit tardío → contenido se construyó por edits
                needs_wait += 1

    print(f"\n  Análisis de edit_delay:")
    print(f"    Completas en <5s (no necesitan delay): {fast_complete} ({fast_complete/n*100:.0f}%)")
    print(f"    Necesitan esperar a edit/mensaje aparte: {needs_wait} ({needs_wait/n*100:.0f}%)")

    # Diseño propuesto
    print(f"\n  💡 IMPLEMENTACIÓN PROPUESTA (delay SOLO si necesario):")
    print(f"     ┌─ Recibir mensaje 'BUY/SELL NOW' ──────────────────────────────────┐")
    print(f"     │                                                                    │")
    print(f"     │  ¿Tiene rango + TP1 + SL desde el primer momento?                  │")
    print(f"     │                                                                    │")
    print(f"     │     SÍ → ejecutar INMEDIATAMENTE  ({fast_complete/n*100:.0f}% de los casos)               │")
    print(f"     │                                                                    │")
    print(f"     │     NO → esperar hasta:                                            │")
    print(f"     │           - edit con datos completos, O                            │")
    print(f"     │           - mensaje SEPARADO con TPs/SL, O                         │")
    print(f"     │           - timeout 90s → abortar                                  │")
    print(f"     │                                                                    │")
    print(f"     └────────────────────────────────────────────────────────────────────┘")
    print(f"\n  Ventaja: 0 delay en {fast_complete/n*100:.0f}% de las señales (las completas)")
    print(f"           Solo {needs_wait/n*100:.0f}% sufren delay (las que SÍ lo necesitan)")


# ═════════════════════════════════════════════════════════════════════════════
#  P4) Post-SL momentum sin sobre-exposición
# ═════════════════════════════════════════════════════════════════════════════

def analyze_post_sl_momentum(sigs):
    section("P4) Post-SL momentum — cómo llegar a TP5 sin sobre-exposición")

    # Identificar señales POST-SL (tras SL en <10min)
    post_sl_sigs = []
    for i, s in enumerate(sigs):
        if i == 0:
            continue
        prev = sigs[i-1]
        if prev["sl_hit"] and prev["time_to_sl"] is not None and prev["time_to_sl"] <= 10:
            # Solo si la siguiente señal está cerca (<2h)
            gap_min = (s["dt"] - prev["dt"]).total_seconds() / 60
            if gap_min < 120:
                post_sl_sigs.append(s)

    n = len(post_sl_sigs)
    print(f"  Señales identificadas como 'post-SL momentum' (n={n})")

    if n == 0:
        return

    # Estadísticas
    max_tps = [s["max_tp_hit"] for s in post_sl_sigs]
    p_tp5 = sum(1 for x in max_tps if x >= 5) / n * 100
    p_tp4 = sum(1 for x in max_tps if x >= 4) / n * 100
    p_tp3 = sum(1 for x in max_tps if x >= 3) / n * 100
    sl_again = sum(1 for s in post_sl_sigs if s["sl_hit"]) / n * 100
    print(f"    P(TP3) = {p_tp3:.0f}%  P(TP4) = {p_tp4:.0f}%  P(TP5) = {p_tp5:.0f}%")
    print(f"    P(SL) = {sl_again:.0f}%")

    subsection("Comparación de 4 estrategias en estas señales")

    strategies = {
        "ESCALONADO normal (4-pos, TP1-4)":     {"n": 4, "extend": False},
        "ESCALONADO + 1 pos extra a TP5":       {"n": 5, "extend": False},
        "TODAS las posiciones a TP5":           {"n": 4, "extend": True},
        "Solo 2 posiciones a TP5 (no DCA)":     {"n": 2, "extend": True},
        "1 posición a TP5":                     {"n": 1, "extend": True},
    }

    print(f"\n  {'Estrategia':<42} {'P/L total':>12} {'P/L medio':>12} {'Win':>6}")
    print(f"  {'-'*78}")
    for name, cfg in strategies.items():
        pls = []
        for s in post_sl_sigs:
            pl = signal_pl_escalonado(s, n_positions=cfg["n"],
                                       lot_mult=1.0, costs=True,
                                       extend_to_tp5=cfg["extend"])
            if pl is not None:
                pls.append(pl)
        if not pls:
            continue
        total = sum(pls)
        avg = total / len(pls)
        wins = sum(1 for p in pls if p > 0) / len(pls) * 100
        print(f"  {name:<42} ${total:+11.0f} ${avg:+11.2f} {wins:5.0f}%")

    print(f"\n  💡 IDEA CLAVE: en modo post-SL, el bot puede NO escalonar TPs")
    print(f"     Mantener TODAS las posiciones apuntando a TP5 (o trail stop tras TP3)")
    print(f"     Riesgo: si llega a TP3 y revierte, no captura nada.")
    print(f"     Mitigación: TRAILING — al tocar TP3, mover SL a TP1 (asegurar mínimo)")


# ═════════════════════════════════════════════════════════════════════════════
#  P5) Burst sin delay obligatorio
# ═════════════════════════════════════════════════════════════════════════════

def analyze_burst_detection(sigs):
    section("P5) Burst — detección SIN delay obligatorio")

    # Identificar todos los bursts (3+ señales en <30min)
    bursts = []
    i = 0
    while i < len(sigs):
        cluster = [sigs[i]]
        j = i + 1
        while j < len(sigs):
            gap = (sigs[j]["dt"] - sigs[j-1]["dt"]).total_seconds() / 60
            if gap <= 30:
                cluster.append(sigs[j])
                j += 1
            else:
                break
        if len(cluster) >= 3:
            bursts.append(cluster)
            i = j
        else:
            i += 1

    print(f"  Bursts detectados: {len(bursts)}")
    print(f"  Total señales 'primera del burst' = {len(bursts)}")

    subsection("ESTRATEGIA A — Operar todo (statu quo)")
    pls_statu_quo = []
    for s in sigs:
        pl = signal_pl_escalonado(s, n_positions=4, lot_mult=1.0, costs=True)
        if pl is not None:
            pls_statu_quo.append(pl)
    print(f"  P/L total tomando TODAS las señales: ${sum(pls_statu_quo):+.0f}")
    print(f"  P/L promedio: ${sum(pls_statu_quo)/len(pls_statu_quo):+.2f}/señal")

    subsection("ESTRATEGIA B — Cerrar primera del burst si llega segunda <10min")
    # Para cada burst, la primera se ABRE, pero si llega segunda en <10min →
    # se cierra al precio actual (asumimos pérdida pequeña = spread + slippage)
    burst_first_ids = set()
    early_close_loss = 0
    for b in bursts:
        first = b[0]
        if len(b) >= 2:
            gap = (b[1]["dt"] - b[0]["dt"]).total_seconds() / 60
            if gap <= 10:
                burst_first_ids.add(first["id"])
                # Asumimos pérdida del spread × n posiciones (aún no DCA completo)
                early_close_loss += COST_PER_TRADE * 1  # solo 1 posición abierta probablemente

    pls_strategy_b = []
    for s in sigs:
        if s["id"] in burst_first_ids:
            # Cerramos antes → pérdida = spread, sin operación completada
            pls_strategy_b.append(-COST_PER_TRADE)
        else:
            pl = signal_pl_escalonado(s, n_positions=4, lot_mult=1.0, costs=True)
            if pl is not None:
                pls_strategy_b.append(pl)

    total_b = sum(pls_strategy_b)
    print(f"  Primeras de burst que se cerrarían: {len(burst_first_ids)}")
    print(f"  P/L total estrategia B: ${total_b:+.0f}")
    print(f"  Diferencia vs A: ${total_b - sum(pls_statu_quo):+.0f}")

    subsection("ESTRATEGIA C — Saltar primera del burst (RETROACTIVO)")
    # Esto requiere "saber el futuro". Simulación: no operar la primera del burst.
    pls_strategy_c = []
    for s in sigs:
        if s["id"] in burst_first_ids:
            # Skip
            continue
        pl = signal_pl_escalonado(s, n_positions=4, lot_mult=1.0, costs=True)
        if pl is not None:
            pls_strategy_c.append(pl)

    total_c = sum(pls_strategy_c)
    print(f"  Primeras de burst saltadas: {len(burst_first_ids)}")
    print(f"  P/L total estrategia C (saltar): ${total_c:+.0f}")
    print(f"  Diferencia vs A: ${total_c - sum(pls_statu_quo):+.0f}")

    subsection("ESTRATEGIA D — Lot/2 en primera, lot doble en segunda+")
    pls_strategy_d = []
    burst_second_ids = set()
    for b in bursts:
        if len(b) >= 2:
            burst_second_ids.add(b[1]["id"])
        if len(b) >= 3:
            burst_second_ids.add(b[2]["id"])
    for s in sigs:
        if s["id"] in burst_first_ids:
            pl = signal_pl_escalonado(s, n_positions=4, lot_mult=0.5, costs=True)
        elif s["id"] in burst_second_ids:
            pl = signal_pl_escalonado(s, n_positions=4, lot_mult=1.5, costs=True)
        else:
            pl = signal_pl_escalonado(s, n_positions=4, lot_mult=1.0, costs=True)
        if pl is not None:
            pls_strategy_d.append(pl)
    total_d = sum(pls_strategy_d)
    print(f"  P/L estrategia D (lot variable): ${total_d:+.0f}")
    print(f"  Diferencia vs A: ${total_d - sum(pls_statu_quo):+.0f}")

    print(f"\n  💡 CONCLUSIÓN: la mejor estrategia es la que dé MÁS $ total")
    print(f"     Estrategia A (statu quo): ${sum(pls_statu_quo):+.0f}")
    print(f"     Estrategia B (cerrar):    ${total_b:+.0f}")
    print(f"     Estrategia C (saltar):    ${total_c:+.0f}")
    print(f"     Estrategia D (lot var):   ${total_d:+.0f}")


# ═════════════════════════════════════════════════════════════════════════════
#  P6) 14h UTC SELL: cálculo neto
# ═════════════════════════════════════════════════════════════════════════════

def analyze_14h_sell(sigs):
    section("P6) 14h UTC SELL — ¿operar o filtrar?")

    sells_14h = [s for s in sigs if s["hour"] == 14 and s["direction"] == "SELL"]
    buys_14h = [s for s in sigs if s["hour"] == 14 and s["direction"] == "BUY"]

    print(f"  SELLs a las 14h UTC: {len(sells_14h)}")
    print(f"  BUYs a las 14h UTC:  {len(buys_14h)}")

    # Calcular P/L tomando los SELLs
    pls = []
    for s in sells_14h:
        pl = signal_pl_escalonado(s, n_positions=4, lot_mult=1.0, costs=True)
        if pl is not None:
            pls.append(pl)

    if not pls:
        return

    total = sum(pls)
    avg = total / len(pls)
    wins = sum(1 for p in pls if p > 0)
    losses = sum(1 for p in pls if p < 0)
    sls_count = sum(1 for s in sells_14h if s["sl_hit"])

    print(f"\n  Si TOMAMOS los SELLs de las 14h:")
    print(f"    P/L total:     ${total:+.0f}")
    print(f"    P/L medio:     ${avg:+.2f}/señal")
    print(f"    SLs: {sls_count}  ({sls_count/len(sells_14h)*100:.0f}%)")
    print(f"    Wins (P/L>0): {wins}  Losses (P/L<0): {losses}")

    if total > 0:
        print(f"\n  ✅ Total POSITIVO. Filtrar PIERDE ${total:.0f} de beneficio")
        print(f"     RECOMENDACIÓN: NO filtrar")
    else:
        print(f"\n  ❌ Total NEGATIVO. Filtrar AHORRA ${-total:.0f} de pérdidas")
        print(f"     RECOMENDACIÓN: FILTRAR")

    # Mismo análisis para BUYs (control)
    pls_buy = []
    for s in buys_14h:
        pl = signal_pl_escalonado(s, n_positions=4, lot_mult=1.0, costs=True)
        if pl is not None:
            pls_buy.append(pl)
    if pls_buy:
        print(f"\n  CONTROL — BUYs 14h UTC:")
        print(f"    P/L total: ${sum(pls_buy):+.0f}  P/L medio: ${sum(pls_buy)/len(pls_buy):+.2f}/señal")


# ═════════════════════════════════════════════════════════════════════════════
#  P7) DCA — cuándo ayuda y cuándo perjudica (EL MÁS IMPORTANTE)
# ═════════════════════════════════════════════════════════════════════════════

def analyze_dca_optimization(sigs):
    section("P7) DCA — cuándo ayuda y cuándo perjudica (CRÍTICO)")

    # Comparar diferentes configuraciones de DCA
    print(f"  Comparación de configuraciones DCA en {len(sigs)} señales 2025+:")
    print(f"\n  Modelo de simulación:")
    print(f"    - n_pos = número de posiciones (1 entry + DCA)")
    print(f"    - Si SL hit → todas las posiciones pierden (cada una entra a precio distinto)")
    print(f"    - Si TPs hit → cierre escalonado (pos i → TP_i)")

    configs = [
        ("Sin DCA (1 pos)",         {"n": 1}),
        ("DCA conservador (2 pos)", {"n": 2}),
        ("DCA medio (3 pos)",       {"n": 3}),
        ("DCA actual (4 pos)",      {"n": 4}),
        ("DCA agresivo (5 pos)",    {"n": 5}),
    ]

    print(f"\n  {'Config':<25} {'P/L total':>12} {'P/L medio':>12} {'WR%':>6} {'Max DD':>10} {'Sharpe':>8}")
    print(f"  {'-'*82}")
    for name, cfg in configs:
        pls = []
        for s in sigs:
            pl = signal_pl_escalonado(s, n_positions=cfg["n"], lot_mult=1.0, costs=True)
            if pl is not None:
                pls.append(pl)
        if not pls:
            continue
        total = sum(pls)
        avg = total / len(pls)
        wins = sum(1 for p in pls if p > 0)
        wr = wins / len(pls) * 100
        # Max DD
        equity = 0
        peak = 0
        max_dd = 0
        for p in pls:
            equity += p
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd
        # Sharpe simple
        if len(pls) > 1:
            sd = statistics.stdev(pls)
            sharpe = avg / sd if sd > 0 else 0
        else:
            sharpe = 0
        print(f"  {name:<25} ${total:+11.0f} ${avg:+11.2f} {wr:5.0f}% ${max_dd:9.0f} {sharpe:7.3f}")

    subsection("¿Cuántas señales con SL llegaron a abrir TODAS las posiciones DCA?")

    # Para señales con SL: ¿el precio llegó a recorrer el rango entero?
    # Como no tenemos tick data, asumimos que si la duración fue >5min, hubo tiempo
    # para abrir varias posiciones.
    sl_signals = [s for s in sigs if s["sl_hit"]]
    print(f"  Señales con SL hit: {len(sl_signals)}")

    by_time = Counter()
    for s in sl_signals:
        if s["time_to_sl"] is None:
            by_time["?"] += 1
        elif s["time_to_sl"] < 2:
            by_time["<2min"] += 1
        elif s["time_to_sl"] < 10:
            by_time["<10min"] += 1
        elif s["time_to_sl"] < 30:
            by_time["<30min"] += 1
        else:
            by_time[">30min"] += 1

    order = ["<2min", "<10min", "<30min", ">30min", "?"]
    for k in order:
        v = by_time.get(k, 0)
        bar = "█" * int(v / max(by_time.values()) * 40) if by_time else ""
        print(f"    SL hit en {k:8s} {bar} {v}")
    print(f"\n  💡 Si SL en <2min → probablemente solo 1-2 posiciones abiertas")
    print(f"     Si SL >30min → todas las 4 posiciones abiertas → pérdida masiva")

    subsection("CÁLCULO: pérdida REAL por escenario de duración antes del SL")

    # Modelo más realista: para cada SL, estimar cuántas posiciones se llegaron a abrir
    # Si time_to_sl < 2min → ~1.5 posiciones
    # Si time_to_sl 2-10min → ~2.5
    # Si time_to_sl >10min → ~4 (todas)

    def positions_opened(t):
        if t is None:
            return 4
        if t < 2:
            return 1.5
        if t < 5:
            return 2.5
        if t < 10:
            return 3.5
        return 4

    realistic_pl = []
    for s in sigs:
        if not s["range"] or not s["tps"] or s["sl"] is None:
            continue
        rl, rh = s["range"]
        direction = s["direction"]
        entry = rh if direction == "BUY" else rl
        sl_dist = abs(entry - s["sl"])
        cost = COST_PER_TRADE

        if s["sl_hit"]:
            n_open = positions_opened(s["time_to_sl"])
            avg_loss = sl_dist + (s["range_size"] or 0) / 2
            pl = -(avg_loss * n_open + cost * n_open)
        elif s["max_tp_hit"] == 0:
            pl = -cost * 4  # asumir 4 abiertas en BE
        else:
            tps = s["tps"]
            pl = 0.0
            for i in range(4):
                tp_idx = min(i, len(tps) - 1)
                if tp_idx < s["max_tp_hit"]:
                    pl += abs(tps[tp_idx] - entry) - cost
                else:
                    pl += -cost
        realistic_pl.append(pl)

    total_real = sum(realistic_pl)
    print(f"\n  P/L MÁS REALISTA (asumiendo posiciones abiertas según duración):")
    print(f"    Total: ${total_real:+.0f}  Medio: ${total_real/len(realistic_pl):+.2f}/señal")

    subsection("PROPUESTAS DE OPTIMIZACIÓN DCA")

    print(f"  Idea 1: DCA con TIME-STOP DEFENSIVO")
    print(f"    Si tras X minutos sin moverse a favor → cerrar todo en BE/-1")
    print(f"    Análisis: {sum(1 for s in sl_signals if s['time_to_sl'] and s['time_to_sl'] > 30)} "
          f"señales con SL >30min → tiempo de cerrar antes")

    print(f"\n  Idea 2: DCA LIMITADO a 2 posiciones (en vez de 4)")
    print(f"    Reduce pérdida en SL pero también ganancia en TPs altos")
    print(f"    → Ver tabla arriba: comparar config 2 pos vs 4 pos")

    print(f"\n  Idea 3: DCA con LOT DECRECIENTE (anti-martingala)")
    print(f"    Pos 1: lot completo (precio del rango)")
    print(f"    Pos 2: lot/2 (2$ después)")
    print(f"    Pos 3: lot/4 (4$ después)")
    print(f"    Riesgo total = ~lot × 1.75 (vs 4 con DCA igual lot)")

    print(f"\n  Idea 4: DCA con SL DINÁMICO")
    print(f"    Cada nueva posición DCA reduce el SL global del bloque")
    print(f"    Si abrimos 2 posiciones a -1$ y -2$, el SL se mueve -1$ menos")

    print(f"\n  Idea 5: NO DCA en señales HIGH RISK / Re-enter / 14h SELL")
    print(f"    Solo abrir 1 posición en señales 'sospechosas'")


# ═════════════════════════════════════════════════════════════════════════════
#  Main
# ═════════════════════════════════════════════════════════════════════════════

def main():
    print(f"📅 Filtrando solo señales >= {CUTOFF_DATE.date()}")
    print(f"💰 Modelo costes: ${COST_PER_TRADE}/trade  |  Lot base: {LOT_BASE} (1 oz)\n")

    msgs = load_msgs()
    sigs = build_signals(msgs)
    print(f"Señales construidas: {len(sigs)}")

    analyze_high_risk_ev(sigs)
    analyze_reenter_ev(sigs)
    analyze_delay_conditional(sigs, msgs)
    analyze_post_sl_momentum(sigs)
    analyze_burst_detection(sigs)
    analyze_14h_sell(sigs)
    analyze_dca_optimization(sigs)


if __name__ == "__main__":
    main()
