"""
analyze_patterns.py — Detección PROFUNDA de patrones que se nos escapan.

Áreas que cubre y que NO estaban en analyze_deep.py:

  1. SEÑALES NO DETECTADAS por el parser (formatos alternativos)
  2. SEÑALES "HIGH RISK" — el canal avisa que son arriesgadas
  3. INSTRUMENTOS NO-GOLD en Canal 1 (debemos filtrarlos)
  4. RACHAS de ganancias/pérdidas (¿hay clusters?)
  5. TIEMPO ENTRE SEÑALES (¿bursts? ¿cooldown tras pérdida?)
  6. MENSAJES "+N pips" — outcomes precisos en pips
  7. EDITS — cuántos edits por señal y cuánto tarda el último
  8. RE-ENTRY signals — "Re enter" después de SL
  9. CORRELACIÓN Canal1 vs Canal2 (¿coinciden en hora/dirección?)
 10. PERFORMANCE BUY vs SELL por canal
 11. PERFORMANCE rangos pequeños vs grandes
 12. PROBABILIDAD CONDICIONAL P(TP_n+1 | TP_n)
 13. PERFORMANCE primeras vs últimas semanas (¿degradación?)
 14. CLUSTERING de señales en mismo día (¿más en un día = peor?)
 15. PRESENCIA de palabras clave predictivas (TENDER, RISK, BREAKEVEN, REVERSE)
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


# ─── Bucket: cargar mensajes ────────────────────────────────────────────────

def load_msgs():
    with open(JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)
    chats = data.get("chats", {}).get("list", [])
    c1 = next((c for c in chats if c.get("id") == CANAL_1_ID), None)
    c2 = next((c for c in chats if c.get("id") == CANAL_2_ID), None)
    return c1.get("messages", []), c2.get("messages", [])


def section(title):
    print(f"\n{'='*72}")
    print(f"  {title}")
    print(f"{'='*72}\n")


# ═════════════════════════════════════════════════════════════════════════════
#  1. SEÑALES NO-XAUUSD EN CANAL 1
# ═════════════════════════════════════════════════════════════════════════════

def patterns_canal1_other_instruments(msgs1):
    section("1. CANAL 1 — Señales de OTROS instrumentos (no XAUUSD)")

    other_pattern = re.compile(
        r"(BUY|SELL)\s+(GBPJPY|USDCAD|EURUSD|EURJPY|GBPUSD|AUDUSD|USDJPY|NAS100|US30|BTCUSD)",
        re.IGNORECASE
    )

    by_instrument = Counter()
    by_direction = Counter()
    samples = defaultdict(list)

    for m in msgs1:
        txt = extract_text(m.get("text", ""))
        match = other_pattern.search(txt.upper())
        if match:
            instrument = match.group(2).upper()
            direction = match.group(1).upper()
            by_instrument[instrument] += 1
            by_direction[(instrument, direction)] += 1
            if len(samples[instrument]) < 3:
                samples[instrument].append(txt[:120].replace("\n", " | "))

    print(f"  Total señales de instrumentos NO-GOLD detectadas: {sum(by_instrument.values())}")
    print(f"  Por instrumento:")
    for inst, n in by_instrument.most_common():
        print(f"    {inst:10s} {n} señales")
        for s in samples[inst]:
            print(f"      • {s}")

    print(f"\n  ⚠ El bot DEBE ignorar estos: ya filtramos por 'GOLD'/'XAU' en parser.is_canal1_signal_text")
    print(f"     pero estos formatos no usan 'NOW' → confirmar que parser los rechaza.")


# ═════════════════════════════════════════════════════════════════════════════
#  2. SEÑALES "HIGH RISK" — el canal las marca explícitamente
# ═════════════════════════════════════════════════════════════════════════════

def patterns_high_risk(msgs2):
    section("2. CANAL 2 — Señales 'HIGH RISK' marcadas por el canal")

    high_risk_msgs = []
    for i, m in enumerate(msgs2):
        txt = extract_text(m.get("text", ""))
        if re.search(r"HIGH\s*RISK", txt, re.IGNORECASE):
            high_risk_msgs.append((i, m, txt))

    print(f"  Mensajes con 'HIGH RISK': {len(high_risk_msgs)}")
    print(f"  Muestras:")
    for i, m, txt in high_risk_msgs[:10]:
        date = m.get("date", "")[:10]
        preview = txt.replace("\n", " | ")[:120]
        print(f"    {date}  {preview}")

    # ¿Qué outcome tienen? Buscamos replies a estos mensajes
    print(f"\n  → Si el canal lo marca como HIGH RISK, el bot debería:")
    print(f"    - Reducir lot a la mitad, O")
    print(f"    - Saltarse la señal completamente")


# ═════════════════════════════════════════════════════════════════════════════
#  3. EDITS — ¿cuántas veces se edita una señal y cuánto tarda?
# ═════════════════════════════════════════════════════════════════════════════

def patterns_edits(msgs, channel_name):
    section(f"3. {channel_name} — Patrón de EDICIONES")

    edits_count = Counter()
    edit_delays = []  # segundos entre creación y última edición

    for m in msgs:
        if "edited" in m and "date" in m:
            try:
                created = parse_dt(m["date"])
                edited = parse_dt(m["edited"])
                delay = (edited - created).total_seconds()
                edit_delays.append(delay)
                # bucket
                if delay < 10:
                    bucket = "<10s"
                elif delay < 60:
                    bucket = "<1min"
                elif delay < 300:
                    bucket = "<5min"
                elif delay < 1800:
                    bucket = "<30min"
                elif delay < 3600:
                    bucket = "<1h"
                elif delay < 14400:
                    bucket = "<4h"
                else:
                    bucket = ">4h"
                edits_count[bucket] += 1
            except Exception:
                pass

    if edit_delays:
        print(f"  Mensajes editados: {len(edit_delays)}")
        print(f"  Delay creación→última edición:")
        print(f"    Mediana: {statistics.median(edit_delays):.0f}s")
        print(f"    Media:   {statistics.mean(edit_delays):.0f}s")
        order = ["<10s", "<1min", "<5min", "<30min", "<1h", "<4h", ">4h"]
        print(f"  Distribución:")
        for k in order:
            n = edits_count.get(k, 0)
            bar = "█" * int(n / max(edits_count.values()) * 40) if edits_count else ""
            print(f"    {k:8s} {bar} {n}")

    print(f"\n  💡 Implicación bot: si un edit llega <10s, probablemente añade TPs/SL")
    print(f"     a la señal inicial. Si llega >5min, es gestión (BE, close).")


# ═════════════════════════════════════════════════════════════════════════════
#  4. PALABRAS CLAVE EN REPLIES (con frecuencia)
# ═════════════════════════════════════════════════════════════════════════════

def patterns_reply_keywords(msgs, channel_name):
    section(f"4. {channel_name} — Palabras clave en REPLIES (gestión)")

    reply_msgs = [m for m in msgs if "reply_to_message_id" in m]
    print(f"  Total replies: {len(reply_msgs)}")

    keywords = {
        "BREAKEVEN/BE": r"\b(breakeven|break\s*even|^be\b|set\s+be|move\s+to\s+be)",
        "CLOSE TRADE":  r"\b(close\s+(trade|trades|position|all))",
        "TP HIT":       r"\b(tp\d*\s*(hit|tapped|reached|smashed|done)|target\s+hit)",
        "SL HIT":       r"\bsl\s+(hit|gone|out)",
        "PIPS GAINED":  r"\+\d+\s*pips",
        "PIPS LOST":    r"-\d+\s*pips",
        "RUNNING":      r"\brunning",
        "SECURE":       r"\bsecur(e|ing)",
        "WORST/BEST":   r"\b(worst|best)\s+entr",
        "RE-ENTER":     r"\bre[\s-]*enter",
        "HOLD":         r"\bhold(ing)?",
        "REVERSE":      r"\breverse",
        "VALID":        r"\b(still\s+valid|trade\s+valid)",
        "WATCH":        r"\bwatch\s*(ing|out|the)",
        "SCALPING":     r"\bscalp(ing)?",
        "SWING":        r"\bswing",
        "TRAILING":     r"\btrail(ing)?",
        "PARTIAL":      r"\bpartial",
    }

    counts = Counter()
    for m in reply_msgs:
        txt = extract_text(m.get("text", "")).lower()
        for label, pat in keywords.items():
            if re.search(pat, txt):
                counts[label] += 1

    print(f"\n  Top frases (% de replies que la contienen):")
    for label, n in sorted(counts.items(), key=lambda x: -x[1]):
        pct = n / len(reply_msgs) * 100
        bar = "█" * int(pct)
        print(f"    {label:15s} {bar} {n:5d} ({pct:.1f}%)")


# ═════════════════════════════════════════════════════════════════════════════
#  5. EXTRACCIÓN DE PIPS GANADOS/PERDIDOS
# ═════════════════════════════════════════════════════════════════════════════

def patterns_pips_outcomes(msgs, channel_name):
    section(f"5. {channel_name} — Outcomes en PIPS extraídos")

    pip_pos = re.compile(r"\+(\d{1,4})\s*pips", re.IGNORECASE)
    pip_neg = re.compile(r"-(\d{1,4})\s*pips", re.IGNORECASE)

    positive_pips = []
    negative_pips = []
    for m in msgs:
        txt = extract_text(m.get("text", ""))
        for match in pip_pos.finditer(txt):
            val = int(match.group(1))
            if 1 <= val <= 1000:
                positive_pips.append(val)
        for match in pip_neg.finditer(txt):
            val = int(match.group(1))
            if 1 <= val <= 1000:
                negative_pips.append(val)

    if positive_pips:
        print(f"  Mensajes con '+N pips' (ganancias mencionadas): {len(positive_pips)}")
        print(f"    Mediana: {statistics.median(positive_pips):.0f} pips")
        print(f"    Media:   {statistics.mean(positive_pips):.0f} pips")
        print(f"    Máximo:  {max(positive_pips)} pips")
        # Distribución por bucket
        buckets = Counter()
        for p in positive_pips:
            if p < 20: buckets["1-19"] += 1
            elif p < 50: buckets["20-49"] += 1
            elif p < 100: buckets["50-99"] += 1
            elif p < 200: buckets["100-199"] += 1
            else: buckets["200+"] += 1
        for k in ["1-19", "20-49", "50-99", "100-199", "200+"]:
            n = buckets.get(k, 0)
            bar = "█" * int(n / max(buckets.values()) * 40) if buckets else ""
            print(f"    +{k:8s} pips {bar} {n}")

    if negative_pips:
        print(f"\n  Mensajes con '-N pips' (pérdidas mencionadas): {len(negative_pips)}")
        print(f"    Mediana: {statistics.median(negative_pips):.0f} pips")
        print(f"    Máximo:  {max(negative_pips)} pips")
        buckets = Counter()
        for p in negative_pips:
            if p < 20: buckets["1-19"] += 1
            elif p < 50: buckets["20-49"] += 1
            elif p < 100: buckets["50-99"] += 1
            else: buckets["100+"] += 1
        for k in ["1-19", "20-49", "50-99", "100+"]:
            n = buckets.get(k, 0)
            bar = "█" * int(n / max(buckets.values()) * 40) if buckets else ""
            print(f"    -{k:8s} pips {bar} {n}")


# ═════════════════════════════════════════════════════════════════════════════
#  6. TIEMPO ENTRE SEÑALES (¿hay bursts?)
# ═════════════════════════════════════════════════════════════════════════════

def patterns_time_between_signals(msgs2):
    section("6. CANAL 2 — Tiempo entre señales (bursts)")

    signal_dates = []
    for m in msgs2:
        txt = extract_text(m.get("text", "")).upper()
        if "BUY NOW" in txt or "SELL NOW" in txt:
            try:
                signal_dates.append(parse_dt(m["date"]))
            except Exception:
                pass

    signal_dates.sort()
    gaps = []
    for i in range(1, len(signal_dates)):
        gap = (signal_dates[i] - signal_dates[i-1]).total_seconds() / 60
        gaps.append(gap)

    if gaps:
        print(f"  Total señales analizadas: {len(signal_dates)}")
        print(f"  Tiempo entre señales:")
        print(f"    Mediana: {statistics.median(gaps):.0f} min")
        print(f"    Media:   {statistics.mean(gaps):.0f} min")

        buckets = Counter()
        for g in gaps:
            if g < 5: buckets["<5min"] += 1
            elif g < 15: buckets["<15min"] += 1
            elif g < 30: buckets["<30min"] += 1
            elif g < 60: buckets["<1h"] += 1
            elif g < 240: buckets["<4h"] += 1
            elif g < 1440: buckets["<1día"] += 1
            else: buckets[">1día"] += 1

        order = ["<5min", "<15min", "<30min", "<1h", "<4h", "<1día", ">1día"]
        print(f"  Distribución gap entre señales:")
        for k in order:
            n = buckets.get(k, 0)
            bar = "█" * int(n / max(buckets.values()) * 40) if buckets else ""
            print(f"    {k:8s} {bar} {n}")

        # Bursts: 3+ señales en <30min
        bursts = 0
        for i in range(2, len(signal_dates)):
            window = (signal_dates[i] - signal_dates[i-2]).total_seconds() / 60
            if window < 30:
                bursts += 1
        print(f"\n  Bursts detectados (3+ señales en <30min): {bursts}")
        print(f"  💡 Cuando el canal saca señales en burst, podría estar 'forzando' tras una pérdida")


# ═════════════════════════════════════════════════════════════════════════════
#  7. CORRELACIÓN CANAL 1 vs CANAL 2 (¿coinciden?)
# ═════════════════════════════════════════════════════════════════════════════

def patterns_cross_channel(msgs1, msgs2):
    section("7. CORRELACIÓN Canal 1 vs Canal 2")

    sigs1 = []
    for m in msgs1:
        txt = extract_text(m.get("text", "")).upper()
        if "BUY GOLD NOW" in txt or "SELL GOLD NOW" in txt:
            try:
                d = parse_dt(m["date"])
                direction = "BUY" if "BUY" in txt else "SELL"
                sigs1.append((d, direction))
            except Exception:
                pass

    sigs2 = []
    for m in msgs2:
        txt = extract_text(m.get("text", "")).upper()
        if "BUY NOW" in txt or "SELL NOW" in txt:
            try:
                d = parse_dt(m["date"])
                direction = "BUY" if "BUY" in txt else "SELL"
                sigs2.append((d, direction))
            except Exception:
                pass

    print(f"  Canal 1: {len(sigs1)} señales | Canal 2: {len(sigs2)} señales")

    # Para cada señal de Canal 1, busca señales de Canal 2 en ventana ±30 min
    coincidences_same = 0
    coincidences_opposite = 0
    no_coincidence = 0
    for d1, dir1 in sigs1:
        found = False
        for d2, dir2 in sigs2:
            delta = abs((d2 - d1).total_seconds()) / 60
            if delta <= 30:
                found = True
                if dir1 == dir2:
                    coincidences_same += 1
                else:
                    coincidences_opposite += 1
                break
        if not found:
            no_coincidence += 1

    print(f"  Para cada señal Canal 1, buscamos Canal 2 en ±30min:")
    print(f"    Misma dirección:    {coincidences_same}")
    print(f"    Dirección opuesta:  {coincidences_opposite}")
    print(f"    Sin coincidencia:   {no_coincidence}")
    if coincidences_same + coincidences_opposite > 0:
        agree_pct = coincidences_same / (coincidences_same + coincidences_opposite) * 100
        print(f"    → Cuando coinciden, ambos canales acuerdan dirección {agree_pct:.0f}% de las veces")
        if agree_pct > 70:
            print(f"    💡 ALTA correlación → señal CONFIRMADA podría ser más rentable")
        elif agree_pct < 30:
            print(f"    💡 ALTA discordancia → uno se equivoca cuando el otro acierta?")


# ═════════════════════════════════════════════════════════════════════════════
#  8. PERFORMANCE BUY vs SELL por canal (necesita el CSV)
# ═════════════════════════════════════════════════════════════════════════════

def patterns_buy_vs_sell(csv_path):
    section("8. PERFORMANCE BUY vs SELL por canal")

    import csv as csvmod
    rows = []
    with open(csv_path, encoding="utf-8") as f:
        for r in csvmod.DictReader(f):
            rows.append(r)

    for ch in ("canal1", "canal2"):
        print(f"\n  {ch.upper()}:")
        for direction in ("BUY", "SELL"):
            sub = [r for r in rows if r["channel"] == ch and r["direction"] == direction]
            if not sub:
                continue
            sl_hits = sum(1 for r in sub if r["sl_hit"] == "True")
            tp_hits = sum(1 for r in sub if r["max_tp_hit"] not in ("", "None", "0"))
            max_tps = []
            for r in sub:
                try:
                    max_tps.append(int(float(r["max_tp_hit"])))
                except (ValueError, TypeError):
                    pass
            avg_tp = statistics.mean(max_tps) if max_tps else 0
            wr = tp_hits / (tp_hits + sl_hits) * 100 if (tp_hits + sl_hits) else 0
            print(f"    {direction}  n={len(sub):4} TP_hits={tp_hits} SL_hits={sl_hits} WR={wr:.0f}%  avg_max_tp={avg_tp:.1f}")


# ═════════════════════════════════════════════════════════════════════════════
#  9. PERFORMANCE por TAMAÑO DE RANGO
# ═════════════════════════════════════════════════════════════════════════════

def patterns_by_range_size(csv_path):
    section("9. PERFORMANCE por TAMAÑO DE RANGO")

    import csv as csvmod
    rows = []
    with open(csv_path, encoding="utf-8") as f:
        for r in csvmod.DictReader(f):
            rows.append(r)

    for ch in ("canal1", "canal2"):
        print(f"\n  {ch.upper()}:")
        by_size = defaultdict(list)
        for r in rows:
            if r["channel"] != ch:
                continue
            try:
                sz = float(r["range_size"])
            except (ValueError, TypeError):
                continue
            sz_int = int(sz)
            by_size[sz_int].append(r)

        for sz in sorted(by_size.keys()):
            sub = by_size[sz]
            sl_hits = sum(1 for r in sub if r["sl_hit"] == "True")
            tp_hits = sum(1 for r in sub if r["max_tp_hit"] not in ("", "None", "0"))
            wr = tp_hits / (tp_hits + sl_hits) * 100 if (tp_hits + sl_hits) else 0
            tps_list = [int(float(r["max_tp_hit"])) for r in sub
                        if r["max_tp_hit"] not in ("", "None", "0")]
            avg_tp = statistics.mean(tps_list) if tps_list else 0
            print(f"    rango={sz}$  n={len(sub):4}  WR={wr:3.0f}%  avg_max_tp_hit={avg_tp:.1f}")


# ═════════════════════════════════════════════════════════════════════════════
# 10. RACHAS / STREAKS de wins y losses (¿se concentran?)
# ═════════════════════════════════════════════════════════════════════════════

def patterns_streaks(csv_path):
    section("10. RACHAS de wins/losses (¿se agrupan?)")

    import csv as csvmod
    rows = []
    with open(csv_path, encoding="utf-8") as f:
        for r in csvmod.DictReader(f):
            rows.append(r)

    for ch in ("canal1", "canal2"):
        sub = [r for r in rows if r["channel"] == ch]
        sub.sort(key=lambda r: r["date"])

        # Construye secuencia W/L/U
        seq = []
        for r in sub:
            tp = r["max_tp_hit"]
            sl = r["sl_hit"] == "True"
            if sl and tp in ("", "None", "0"):
                seq.append("L")
            elif tp not in ("", "None", "0"):
                seq.append("W")
            else:
                seq.append("?")

        # Rachas
        win_streaks = []
        loss_streaks = []
        cur, cur_type = 0, None
        for s in seq:
            if s == cur_type:
                cur += 1
            else:
                if cur_type == "W":
                    win_streaks.append(cur)
                elif cur_type == "L":
                    loss_streaks.append(cur)
                cur_type = s
                cur = 1
        if cur_type == "W":
            win_streaks.append(cur)
        elif cur_type == "L":
            loss_streaks.append(cur)

        print(f"\n  {ch.upper()}:  secuencia: {len(seq)} señales")
        if win_streaks:
            print(f"    Rachas de WINS:   max={max(win_streaks)}  promedio={statistics.mean(win_streaks):.1f}")
        if loss_streaks:
            print(f"    Rachas de LOSSES: max={max(loss_streaks)}  promedio={statistics.mean(loss_streaks):.1f}")
            top5 = sorted(loss_streaks, reverse=True)[:5]
            print(f"    Top 5 peores rachas L: {top5}")
        # Probabilidad de L tras L
        p_l_after_l = 0
        n_after_l = 0
        for i in range(1, len(seq)):
            if seq[i-1] == "L":
                n_after_l += 1
                if seq[i] == "L":
                    p_l_after_l += 1
        if n_after_l:
            print(f"    P(L | L anterior) = {p_l_after_l/n_after_l*100:.0f}%   (vs base = {seq.count('L')/len(seq)*100:.0f}%)")


# ═════════════════════════════════════════════════════════════════════════════
# 11. PERFORMANCE TEMPORAL — ¿el canal se está degradando?
# ═════════════════════════════════════════════════════════════════════════════

def patterns_temporal_drift(csv_path):
    section("11. EVOLUCIÓN TEMPORAL — ¿degradación?")

    import csv as csvmod
    rows = []
    with open(csv_path, encoding="utf-8") as f:
        for r in csvmod.DictReader(f):
            rows.append(r)

    for ch in ("canal1", "canal2"):
        sub = [r for r in rows if r["channel"] == ch]
        sub.sort(key=lambda r: r["date"])
        if len(sub) < 30:
            continue

        # Bucket por mes
        by_month = defaultdict(list)
        for r in sub:
            ym = r["date"][:7]  # YYYY-MM
            by_month[ym].append(r)

        print(f"\n  {ch.upper()}:")
        for ym in sorted(by_month.keys()):
            mr = by_month[ym]
            sl = sum(1 for r in mr if r["sl_hit"] == "True")
            tp = sum(1 for r in mr if r["max_tp_hit"] not in ("", "None", "0"))
            wr = tp / (tp + sl) * 100 if (tp + sl) else 0
            print(f"    {ym}  n={len(mr):4}  TPs={tp:4}  SLs={sl:3}  WR={wr:3.0f}%")


# ═════════════════════════════════════════════════════════════════════════════
# 12. ROBUSTEZ del filtro horario 07h
# ═════════════════════════════════════════════════════════════════════════════

def patterns_hour_robustness(csv_path):
    section("12. ROBUSTEZ filtro 07h UTC — ¿es consistente mes a mes?")

    import csv as csvmod
    rows = []
    with open(csv_path, encoding="utf-8") as f:
        for r in csvmod.DictReader(f):
            rows.append(r)

    sub = [r for r in rows if r["channel"] == "canal2"]
    by_ym_hour = defaultdict(lambda: defaultdict(list))
    for r in sub:
        try:
            h = int(r["hour_utc"])
            ym = r["date"][:7]
        except (ValueError, KeyError):
            continue
        # Outcome simple: tp_hit > 0 → +1, sl_hit → -1, else 0
        tp = r["max_tp_hit"]
        sl = r["sl_hit"] == "True"
        if sl and tp in ("", "None", "0"):
            outcome = -1
        elif tp not in ("", "None", "0"):
            outcome = int(float(tp))
        else:
            outcome = 0
        by_ym_hour[ym][h].append(outcome)

    print(f"  Para hora 07h UTC, mes a mes:")
    for ym in sorted(by_ym_hour.keys()):
        h7 = by_ym_hour[ym].get(7, [])
        if not h7:
            continue
        avg = statistics.mean(h7)
        n_l = sum(1 for x in h7 if x == -1)
        n_w = sum(1 for x in h7 if x > 0)
        print(f"    {ym}  n={len(h7):3}  W={n_w} L={n_l}  outcome_avg={avg:+.2f}")

    print(f"\n  Para hora 09h UTC (la 'hora dorada'):")
    for ym in sorted(by_ym_hour.keys()):
        h9 = by_ym_hour[ym].get(9, [])
        if not h9:
            continue
        avg = statistics.mean(h9)
        n_l = sum(1 for x in h9 if x == -1)
        n_w = sum(1 for x in h9 if x > 0)
        print(f"    {ym}  n={len(h9):3}  W={n_w} L={n_l}  outcome_avg={avg:+.2f}")


# ═════════════════════════════════════════════════════════════════════════════
# 13. PROBABILIDAD CONDICIONAL P(TP_n | TP_n-1)
# ═════════════════════════════════════════════════════════════════════════════

def patterns_conditional_tp(csv_path):
    section("13. PROBABILIDADES CONDICIONALES de TPs")

    import csv as csvmod
    rows = []
    with open(csv_path, encoding="utf-8") as f:
        for r in csvmod.DictReader(f):
            rows.append(r)

    for ch in ("canal1", "canal2"):
        sub = [r for r in rows if r["channel"] == ch]
        n_total = len(sub)

        # Cuántas alcanzan al menos TPi
        max_tps = []
        for r in sub:
            try:
                mtp = int(float(r["max_tp_hit"]))
            except (ValueError, TypeError):
                mtp = 0
            sl = r["sl_hit"] == "True"
            if sl and mtp == 0:
                continue  # SL puro
            max_tps.append(mtp)

        max_n = max(max_tps) if max_tps else 0
        print(f"\n  {ch.upper()}:  evaluables={len(max_tps)} (excl. SL puros)")

        for tp_target in range(1, max_n + 1):
            reached = sum(1 for x in max_tps if x >= tp_target)
            if tp_target == 1:
                p = reached / len(max_tps) * 100 if max_tps else 0
                print(f"    P(TP{tp_target}) = {p:5.1f}%   (n={reached}/{len(max_tps)})")
            else:
                prev = sum(1 for x in max_tps if x >= tp_target - 1)
                if prev:
                    p_cond = reached / prev * 100
                    p_abs = reached / len(max_tps) * 100
                    print(f"    P(TP{tp_target}|TP{tp_target-1}) = {p_cond:5.1f}%   |   P(TP{tp_target}) = {p_abs:5.1f}%")


# ═════════════════════════════════════════════════════════════════════════════
#  Main
# ═════════════════════════════════════════════════════════════════════════════

def main():
    msgs1, msgs2 = load_msgs()
    csv_path = Path(__file__).parent / "signals_detail.csv"

    patterns_canal1_other_instruments(msgs1)
    patterns_high_risk(msgs2)
    patterns_edits(msgs1, "CANAL 1")
    patterns_edits(msgs2, "CANAL 2")
    patterns_reply_keywords(msgs1, "CANAL 1")
    patterns_reply_keywords(msgs2, "CANAL 2")
    patterns_pips_outcomes(msgs1, "CANAL 1")
    patterns_pips_outcomes(msgs2, "CANAL 2")
    patterns_time_between_signals(msgs2)
    patterns_cross_channel(msgs1, msgs2)
    if csv_path.exists():
        patterns_buy_vs_sell(str(csv_path))
        patterns_by_range_size(str(csv_path))
        patterns_streaks(str(csv_path))
        patterns_temporal_drift(str(csv_path))
        patterns_hour_robustness(str(csv_path))
        patterns_conditional_tp(str(csv_path))


if __name__ == "__main__":
    main()
