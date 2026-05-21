"""
analyze_advanced.py — Análisis profundo de hipótesis específicas:

  A) HIPÓTESIS SL→TP5: ¿tras SL rápido, siguiente señal llega más lejos?
  B) DCA detallado en Canal 2: extraer "best/worst entry" prices de los replies
     y ver qué patrones siguen
  C) BURSTS: análisis uno a uno (dirección dominante, outcomes)
  D) Validación HIGH RISK: WR real y outcome promedio
  E) Filtro temporal: solo 2025+ para todo (relevancia actual)
  F) Mensajes "Re-enter" — outcome después de reentry
  G) Análisis de la SECUENCIA EXACTA del mensaje Canal 2 (BUY NOW → edit rango → TPs)
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

CANAL_1_ID = 1642806869
CANAL_2_ID = 2614601304
JSON_PATH = r"C:\Users\josea\Downloads\Telegram Desktop\DataExport_2026-04-22\result.json"

CUTOFF_DATE = datetime(2025, 1, 1)  # solo señales desde 2025


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
    print(f"\n{'='*72}")
    print(f"  {title}")
    print(f"{'='*72}\n")


def load_msgs():
    with open(JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)
    chats = data.get("chats", {}).get("list", [])
    c2 = next((c for c in chats if c.get("id") == CANAL_2_ID), None)
    return c2.get("messages", [])


# ═════════════════════════════════════════════════════════════════════════════
#  Construcción base: lista de señales con sus replies y outcomes
# ═════════════════════════════════════════════════════════════════════════════

def build_signals(msgs):
    """
    Devuelve lista de señales Canal 2 con:
      id, date, dt, direction, range, tps, sl,
      replies (lista de dicts), outcome (max_tp_hit, sl_hit, time_to_outcome)
    Filtra solo 2025+.
    """
    sigs = []
    msgs_by_id = {m["id"]: m for m in msgs if "id" in m}

    # Index replies por reply_to_message_id
    replies_to = defaultdict(list)
    for m in msgs:
        if "reply_to_message_id" in m:
            replies_to[m["reply_to_message_id"]].append(m)

    for m in msgs:
        txt = extract_text(m.get("text", ""))
        if not txt:
            continue
        t_upper = txt.upper()
        if not ("BUY NOW" in t_upper or "SELL NOW" in t_upper or
                "XAUUSD BUY" in t_upper or "XAUUSD SELL" in t_upper):
            continue
        try:
            dt = parse_dt(m["date"])
        except Exception:
            continue
        if dt < CUTOFF_DATE:
            continue

        direction = "BUY" if "BUY" in t_upper else "SELL"

        # Extraer rango
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

        # Outcome (basado en replies)
        max_tp_hit = 0
        sl_hit = False
        be_set = False
        time_to_sl = None
        time_to_first_tp = None
        time_to_last_tp = None

        for r in rs:
            t = r["txt_lower"]
            # TP detection
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

            # SL detection
            if "sl" in t and ("hit" in t or "out" in t or "❌" in r["text"]):
                sl_hit = True
                if time_to_sl is None:
                    time_to_sl = (r["dt"] - dt).total_seconds() / 60

            # BE detection
            if re.search(r"\bbreak\s*even\b|\bbe\b|\bbreakeven\b", t):
                be_set = True

        # HIGH RISK?
        is_high_risk = bool(re.search(r"high\s*risk", txt, re.IGNORECASE))

        sigs.append({
            "id": m["id"],
            "dt": dt,
            "date": dt.strftime("%Y-%m-%d"),
            "hour": dt.hour,
            "direction": direction,
            "range": rng,
            "range_size": (rng[1] - rng[0]) if rng else None,
            "tps": tps,
            "sl": sl,
            "replies": rs,
            "max_tp_hit": max_tp_hit,
            "sl_hit": sl_hit,
            "be_set": be_set,
            "time_to_sl": time_to_sl,
            "time_to_first_tp": time_to_first_tp,
            "time_to_last_tp": time_to_last_tp,
            "is_high_risk": is_high_risk,
            "raw_text": txt,
        })

    sigs.sort(key=lambda s: s["dt"])
    return sigs


# ═════════════════════════════════════════════════════════════════════════════
#  A) HIPÓTESIS: tras SL rápido → siguiente señal va más lejos
# ═════════════════════════════════════════════════════════════════════════════

def test_sl_to_tp5_hypothesis(sigs):
    section("A) HIPÓTESIS — Tras SL RÁPIDO, siguiente señal va más lejos")

    # Definimos "SL rápido" como SL hit en <30min
    sl_quick_thresholds = [10, 20, 30, 60]

    print(f"  Total señales 2025+: {len(sigs)}")
    print(f"  Señales con SL_hit confirmado: {sum(1 for s in sigs if s['sl_hit'])}")

    # Base rate: P(TPn) en TODAS las señales
    base_n = len(sigs)
    base_tp = Counter()
    for s in sigs:
        if s["max_tp_hit"] > 0:
            for k in range(1, s["max_tp_hit"] + 1):
                base_tp[k] += 1
    print(f"\n  BASE RATE (todas las señales 2025+):")
    for k in range(1, 6):
        pct = base_tp[k] / base_n * 100 if base_n else 0
        print(f"    P(TP{k}) = {pct:.1f}%   ({base_tp[k]}/{base_n})")

    print(f"\n  --- AHORA: ¿qué pasa con la SIGUIENTE señal tras un SL? ---\n")

    for thresh in sl_quick_thresholds:
        # Encuentra señales con SL_hit en <thresh min
        sl_quick = [s for s in sigs if s["sl_hit"] and s["time_to_sl"] is not None and s["time_to_sl"] <= thresh]

        # Para cada una, busca la siguiente señal
        next_outcomes = []
        next_outcomes_max_tp = []
        for sl_sig in sl_quick:
            # Buscar la siguiente señal después del momento del SL hit
            sl_moment = sl_sig["dt"] + timedelta(minutes=sl_sig["time_to_sl"])
            for nxt in sigs:
                if nxt["dt"] > sl_moment:
                    next_outcomes.append(nxt)
                    next_outcomes_max_tp.append(nxt["max_tp_hit"])
                    break

        n = len(next_outcomes)
        if n == 0:
            continue

        print(f"  ┌─ SL hit en <{thresh}min  ({len(sl_quick)} casos, {n} con siguiente señal)")
        # Distribución de max_tp_hit en la siguiente
        tp_counter = Counter(next_outcomes_max_tp)
        for tp_target in range(1, 6):
            reached = sum(1 for x in next_outcomes_max_tp if x >= tp_target)
            pct = reached / n * 100
            base_pct = base_tp[tp_target] / base_n * 100 if base_n else 0
            delta = pct - base_pct
            arrow = "📈" if delta > 5 else ("📉" if delta < -5 else "  ")
            print(f"  │  P(TP{tp_target} | SL previo rápido) = {pct:5.1f}%   "
                  f"vs base {base_pct:5.1f}%   {arrow} ({delta:+.1f}pp)")
        # SL vs SL en siguiente
        sl_after_sl = sum(1 for x in next_outcomes if x["sl_hit"])
        print(f"  │  P(SL otra vez) = {sl_after_sl/n*100:.1f}%")
        print(f"  └─\n")

    # Ahora al revés: ¿tras señal RÁPIDA al TP5 ganadora, qué pasa con la siguiente?
    print(f"\n  --- ¿Y al revés: tras una señal que llegó a TP5, qué pasa con la siguiente? ---\n")

    tp5_signals = [s for s in sigs if s["max_tp_hit"] >= 5]
    next_after_tp5 = []
    for ts in tp5_signals:
        # Última actualización del TP5
        end_moment = ts["dt"] + timedelta(minutes=(ts["time_to_last_tp"] or 60))
        for nxt in sigs:
            if nxt["dt"] > end_moment:
                next_after_tp5.append(nxt)
                break

    n = len(next_after_tp5)
    print(f"  Tras señal que llegó a TP5: {n} casos analizados")
    for tp_target in range(1, 6):
        reached = sum(1 for x in next_after_tp5 if x["max_tp_hit"] >= tp_target)
        pct = reached / n * 100 if n else 0
        base_pct = base_tp[tp_target] / base_n * 100 if base_n else 0
        delta = pct - base_pct
        arrow = "📈" if delta > 5 else ("📉" if delta < -5 else "  ")
        print(f"    P(TP{tp_target} | TP5 previo) = {pct:5.1f}%   vs base {base_pct:5.1f}%   {arrow} ({delta:+.1f}pp)")


# ═════════════════════════════════════════════════════════════════════════════
#  B) DCA — Extracción de "best/worst entry" prices
# ═════════════════════════════════════════════════════════════════════════════

def analyze_dca_patterns(sigs):
    section("B) DCA — Patrones de 'best/worst entry' en Canal 2")

    dca_signals = []
    for s in sigs:
        for r in s["replies"]:
            t = r["txt_lower"]
            if "best entr" in t or "worst entr" in t or "lowest" in t or "highest" in t:
                dca_signals.append(s)
                break

    print(f"  Señales con menciones DCA: {len(dca_signals)}/{len(sigs)} ({len(dca_signals)/len(sigs)*100:.0f}%)")

    # Estadísticas comparativas
    n_dca = len(dca_signals)
    n_no_dca = len(sigs) - n_dca

    # Outcomes
    print(f"\n  ¿Las señales con DCA mencionado tienen mejor outcome?")
    for label, group in [("Con DCA mencionado", dca_signals),
                          ("Sin DCA mencionado", [s for s in sigs if s not in dca_signals])]:
        n = len(group)
        if n == 0:
            continue
        max_tps = [s["max_tp_hit"] for s in group]
        sl_pct = sum(1 for s in group if s["sl_hit"]) / n * 100
        wr = sum(1 for s in group if s["max_tp_hit"] > 0) / n * 100
        avg_tp = statistics.mean([s["max_tp_hit"] for s in group if s["max_tp_hit"] > 0]) \
                 if any(s["max_tp_hit"] > 0 for s in group) else 0
        print(f"    {label}: n={n}  WR={wr:.0f}%  SL={sl_pct:.0f}%  avg_tp={avg_tp:.1f}")

    # Tamaño de rango en señales con DCA mencionado
    dca_with_range = [s for s in dca_signals if s["range_size"] is not None]
    if dca_with_range:
        avg_range = statistics.mean([s["range_size"] for s in dca_with_range])
        print(f"\n  Tamaño rango medio en señales con DCA: {avg_range:.1f}$")
        print(f"  Tamaño rango medio en TODAS:           "
              f"{statistics.mean([s['range_size'] for s in sigs if s['range_size'] is not None]):.1f}$")

    # Ahora: extraer precios "best entry" / "worst entry" mencionados
    print(f"\n  Extracción de precios 'best/worst entry' (muestras):")
    samples = []
    for s in dca_signals[:30]:
        if s["range"] is None:
            continue
        for r in s["replies"]:
            t = r["text"]
            t_low = t.lower()
            if "best entr" in t_low:
                # buscar precio cercano
                m_p = re.search(r"(\d{4}(?:\.\d+)?)", t)
                if m_p:
                    price = float(m_p.group(1))
                    if 1500 <= price <= 5000:
                        samples.append({
                            "direction": s["direction"],
                            "range": s["range"],
                            "best_price": price,
                            "preview": t[:80]
                        })
                        break

    for sm in samples[:15]:
        rl, rh = sm["range"]
        position_in_range = ((sm["best_price"] - rl) / (rh - rl) * 100) if rh > rl else 0
        edge_label = "RANGE_LOW" if sm["best_price"] <= rl else (
                     "RANGE_HIGH" if sm["best_price"] >= rh else f"{position_in_range:.0f}% del rango")
        print(f"    {sm['direction']}  range=[{rl}-{rh}]  best_entry={sm['best_price']}  → {edge_label}")
        print(f"      {sm['preview']}")


# ═════════════════════════════════════════════════════════════════════════════
#  C) BURSTS — Análisis detallado uno a uno
# ═════════════════════════════════════════════════════════════════════════════

def analyze_bursts(sigs):
    section("C) BURSTS — Análisis detallado uno a uno (3+ señales en <30min)")

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

    print(f"  Total bursts detectados (clusters de 3+ señales con gap <30min): {len(bursts)}")

    # Estadísticas globales
    burst_sigs_total = sum(len(b) for b in bursts)
    print(f"  Señales totales involucradas: {burst_sigs_total}")

    # ¿Qué pasa antes del burst? (¿hubo SL?)
    bursts_after_sl = 0
    bursts_after_tp = 0
    bursts_after_unknown = 0
    for b in bursts:
        first = b[0]
        # busca señal anterior
        prev_sig = None
        for s in sigs:
            if s["dt"] < first["dt"]:
                prev_sig = s
            else:
                break
        if prev_sig:
            if prev_sig["sl_hit"]:
                bursts_after_sl += 1
            elif prev_sig["max_tp_hit"] > 0:
                bursts_after_tp += 1
            else:
                bursts_after_unknown += 1

    print(f"\n  Contexto previo al burst:")
    print(f"    Tras SL anterior:   {bursts_after_sl} ({bursts_after_sl/len(bursts)*100:.0f}%)")
    print(f"    Tras TP anterior:   {bursts_after_tp} ({bursts_after_tp/len(bursts)*100:.0f}%)")
    print(f"    Tras señal sin outcome: {bursts_after_unknown} ({bursts_after_unknown/len(bursts)*100:.0f}%)")

    # Direcciones dentro del burst: ¿son todas iguales o mixtas?
    same_dir = 0
    mixed_dir = 0
    opposite_2plus = 0
    for b in bursts:
        dirs = set(s["direction"] for s in b)
        if len(dirs) == 1:
            same_dir += 1
        else:
            mixed_dir += 1
            buys = sum(1 for s in b if s["direction"] == "BUY")
            sells = sum(1 for s in b if s["direction"] == "SELL")
            if min(buys, sells) >= 2:
                opposite_2plus += 1

    print(f"\n  Direcciones dentro del burst:")
    print(f"    Todas misma dirección:  {same_dir} ({same_dir/len(bursts)*100:.0f}%)")
    print(f"    Mixto (BUY+SELL):        {mixed_dir} ({mixed_dir/len(bursts)*100:.0f}%)")
    print(f"      → con 2+ en dirección opuesta: {opposite_2plus}")
    print(f"  💡 Interpretación: si el burst es mixto, el canal está 'fishing' direcciones")

    # Outcomes del burst: ¿cuántas señales del burst ganan?
    print(f"\n  Outcome de cada señal DENTRO del burst:")
    print(f"    Posición   #señales   WR    avg_max_tp_hit")
    for pos in range(0, 5):
        sub = [b[pos] for b in bursts if len(b) > pos]
        if not sub:
            continue
        sl = sum(1 for s in sub if s["sl_hit"])
        wr = sum(1 for s in sub if s["max_tp_hit"] > 0) / len(sub) * 100
        avg = statistics.mean([s["max_tp_hit"] for s in sub if s["max_tp_hit"] > 0]) \
              if any(s["max_tp_hit"] > 0 for s in sub) else 0
        print(f"      Sig #{pos+1}     n={len(sub):3}      WR={wr:3.0f}%   avg_TP={avg:.1f}   SLs={sl}")

    # Mostrar 3 ejemplos de bursts
    print(f"\n  Ejemplos de 3 bursts (más recientes):")
    for b in bursts[-3:]:
        print(f"\n  ── Burst del {b[0]['date']} ──")
        for s in b:
            outcome = f"TP{s['max_tp_hit']}" if s["max_tp_hit"] > 0 else ("SL" if s["sl_hit"] else "?")
            t = s["dt"].strftime("%H:%M")
            print(f"    {t}  {s['direction']:4}  range={s['range']}  → {outcome}")


# ═════════════════════════════════════════════════════════════════════════════
#  D) HIGH RISK — outcomes reales
# ═════════════════════════════════════════════════════════════════════════════

def analyze_high_risk(sigs):
    section("D) HIGH RISK — outcomes reales (lotaje a la mitad recomendado)")

    high_risk = [s for s in sigs if s["is_high_risk"]]
    no_risk = [s for s in sigs if not s["is_high_risk"]]

    print(f"  HIGH RISK signals (2025+): {len(high_risk)}")
    print(f"  Resto:                    {len(no_risk)}")

    for label, group in [("HIGH RISK", high_risk), ("NORMAL", no_risk)]:
        if not group:
            continue
        n = len(group)
        sl = sum(1 for s in group if s["sl_hit"])
        wr = sum(1 for s in group if s["max_tp_hit"] > 0) / n * 100
        sl_pct = sl / n * 100
        max_tps = [s["max_tp_hit"] for s in group if s["max_tp_hit"] > 0]
        avg_tp = statistics.mean(max_tps) if max_tps else 0
        # Distribución de TPs
        tp_counts = Counter(s["max_tp_hit"] for s in group)
        print(f"\n  {label}: n={n}")
        print(f"    WR={wr:.0f}%  SL_rate={sl_pct:.0f}%  avg_max_TP={avg_tp:.1f}")
        for k in range(0, 6):
            pct = tp_counts[k] / n * 100
            bar = "█" * int(pct / 2)
            print(f"    TP={k}  {bar} {tp_counts[k]} ({pct:.0f}%)")


# ═════════════════════════════════════════════════════════════════════════════
#  E) Re-Enter signals — ¿qué pasa después?
# ═════════════════════════════════════════════════════════════════════════════

def analyze_reenter(sigs):
    section("E) RE-ENTER signals — qué pasa después de una reentrada")

    reentries = []
    for s in sigs:
        for r in s["replies"]:
            if re.search(r"\bre[\s-]*enter", r["txt_lower"]):
                reentries.append(s)
                break

    print(f"  Señales con menciones de 'Re-enter' en replies: {len(reentries)}")

    if reentries:
        n = len(reentries)
        wr = sum(1 for s in reentries if s["max_tp_hit"] > 0) / n * 100
        sl = sum(1 for s in reentries if s["sl_hit"]) / n * 100
        avg_tp = statistics.mean([s["max_tp_hit"] for s in reentries if s["max_tp_hit"] > 0]) \
                 if any(s["max_tp_hit"] > 0 for s in reentries) else 0
        print(f"    WR={wr:.0f}%  SL={sl:.0f}%  avg_max_TP={avg_tp:.1f}")
        print(f"  Comparar con base rate:")
        n_all = len(sigs)
        wr_base = sum(1 for s in sigs if s["max_tp_hit"] > 0) / n_all * 100
        print(f"    Base WR={wr_base:.0f}%")


# ═════════════════════════════════════════════════════════════════════════════
#  F) Análisis de la SECUENCIA de mensajes Canal 2
# ═════════════════════════════════════════════════════════════════════════════

def analyze_message_evolution(msgs):
    section("F) SECUENCIA de mensajes Canal 2 (BUY NOW → rango → TPs)")

    # Encuentra mensajes editados con texto inicial muy distinto del final
    # Esto es complicado porque el JSON solo nos da el texto FINAL.
    # Pero podemos ver el patrón de tiempos: edits agrupados.

    # Para mensajes BUY NOW / SELL NOW, ver cuántos edits llevan
    signal_msgs = []
    for m in msgs:
        txt = extract_text(m.get("text", ""))
        if not txt:
            continue
        t_upper = txt.upper()
        if "BUY NOW" in t_upper or "SELL NOW" in t_upper:
            try:
                dt = parse_dt(m["date"])
            except Exception:
                continue
            if dt < CUTOFF_DATE:
                continue

            # ¿está editado?
            edited_dt = None
            if "edited" in m:
                try:
                    edited_dt = parse_dt(m["edited"])
                except Exception:
                    pass

            # Detectar componentes en el texto FINAL
            has_range = bool(re.search(r"\d{4}\s*-\s*\d{4}", txt))
            has_tps = bool(re.search(r"TP\d+", txt, re.IGNORECASE))
            has_sl = bool(re.search(r"\bSL\b", txt, re.IGNORECASE))

            signal_msgs.append({
                "id": m["id"],
                "dt": dt,
                "edited_dt": edited_dt,
                "has_range": has_range,
                "has_tps": has_tps,
                "has_sl": has_sl,
                "edit_delay_s": (edited_dt - dt).total_seconds() if edited_dt else None,
            })

    n = len(signal_msgs)
    edited = sum(1 for s in signal_msgs if s["edited_dt"])
    print(f"  Total señales BUY/SELL NOW (2025+): {n}")
    print(f"  Editadas tras creación:             {edited} ({edited/n*100:.0f}%)")

    # Edit delays
    delays = [s["edit_delay_s"] for s in signal_msgs if s["edit_delay_s"] is not None]
    if delays:
        print(f"\n  Edit delay (creación → última edición):")
        print(f"    Mediana: {statistics.median(delays):.0f}s")
        print(f"    Media:   {statistics.mean(delays):.0f}s")
        # buckets
        buckets = Counter()
        for d in delays:
            if d < 5: buckets["<5s"] += 1
            elif d < 15: buckets["<15s"] += 1
            elif d < 30: buckets["<30s"] += 1
            elif d < 60: buckets["<1min"] += 1
            elif d < 300: buckets["<5min"] += 1
            elif d < 1800: buckets["<30min"] += 1
            else: buckets[">30min"] += 1
        order = ["<5s", "<15s", "<30s", "<1min", "<5min", "<30min", ">30min"]
        for k in order:
            v = buckets.get(k, 0)
            bar = "█" * int(v / max(buckets.values()) * 40) if buckets else ""
            print(f"    {k:8s} {bar} {v}")

    # ¿Cuál es la composición del mensaje final?
    print(f"\n  Estado FINAL del mensaje 'BUY/SELL NOW':")
    has_range_only = sum(1 for s in signal_msgs if s["has_range"] and not s["has_tps"])
    has_full = sum(1 for s in signal_msgs if s["has_range"] and s["has_tps"] and s["has_sl"])
    has_no_range = sum(1 for s in signal_msgs if not s["has_range"])
    print(f"    Sólo 'BUY NOW' (sin rango): {has_no_range}")
    print(f"    Con rango pero sin TPs:     {has_range_only}")
    print(f"    Completo (rango + TPs + SL): {has_full}")
    print(f"\n  💡 Esto valida tu observación:")
    print(f"     - Primer mensaje suele ser solo 'XAUUSD BUY NOW'")
    print(f"     - Edits añaden el rango")
    print(f"     - Mensaje SEPARADO (no editado) suele tener TPs/SL")
    print(f"     - El mensaje original a veces se completa con todo")


# ═════════════════════════════════════════════════════════════════════════════
#  G) Análisis BUY vs SELL EN HORARIO específico
# ═════════════════════════════════════════════════════════════════════════════

def analyze_hour_direction(sigs):
    section("G) Hora UTC × Dirección — ¿hay horas donde BUY funciona y SELL no?")

    by_hour_dir = defaultdict(lambda: {"BUY": [], "SELL": []})
    for s in sigs:
        by_hour_dir[s["hour"]][s["direction"]].append(s)

    print(f"  Hora    BUY (n)  WR   |  SELL (n)  WR    Diff")
    for h in sorted(by_hour_dir.keys()):
        buys = by_hour_dir[h]["BUY"]
        sells = by_hour_dir[h]["SELL"]
        wr_buy = sum(1 for s in buys if s["max_tp_hit"] > 0) / len(buys) * 100 if buys else 0
        wr_sell = sum(1 for s in sells if s["max_tp_hit"] > 0) / len(sells) * 100 if sells else 0
        diff = wr_buy - wr_sell if buys and sells else 0
        flag = ""
        if buys and sells and len(buys) >= 5 and len(sells) >= 5 and abs(diff) > 15:
            flag = "  ⚠"
        print(f"  {h:02d}h    {len(buys):4} {wr_buy:5.0f}%   |   {len(sells):4} {wr_sell:5.0f}%    {diff:+5.0f}pp{flag}")


# ═════════════════════════════════════════════════════════════════════════════
#  Main
# ═════════════════════════════════════════════════════════════════════════════

def main():
    print(f"📅 Filtrando solo señales >= {CUTOFF_DATE.date()}\n")
    msgs = load_msgs()
    sigs = build_signals(msgs)
    print(f"Construidas {len(sigs)} señales válidas (Canal 2, 2025+)\n")

    test_sl_to_tp5_hypothesis(sigs)
    analyze_dca_patterns(sigs)
    analyze_bursts(sigs)
    analyze_high_risk(sigs)
    analyze_reenter(sigs)
    analyze_message_evolution(msgs)
    analyze_hour_direction(sigs)


if __name__ == "__main__":
    main()
