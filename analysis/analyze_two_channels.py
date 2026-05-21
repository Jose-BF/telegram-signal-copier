"""
analyze_two_channels.py — Analiza por SEPARADO canal 1 (DT Investing) y
canal 2 (Gold Standard) sobre datos 2026 ya que cada canal opera de forma
totalmente distinta:

  CANAL 1 (DT Investing):
    - Sticker BUY/SELL → entrada inmediata
    - Texto post-sticker con: precio único o rango + 4 TPs + SL
    - Updates manuales en chat: "Move SL XXXX", "TP1 & TP2 HIT → BE"
    - El operador recomienda BE en TP2 generalmente

  CANAL 2 (Gold Standard):
    - Mensaje único: "XAU USD BUY NOW 4390-4386" + 5 TPs + SL
    - Trade management explícito en CADA mensaje:
      * "fill the entire entry zone → close worst entries, leave best with SL ahead of BE"
      * "At TP1 → move SL to BE"

Output: caracterización completa de cada canal + simulación honesta
diferenciada (no se puede tratar a ambos con la misma estrategia).
"""

import json
import re
import sys
import statistics
from collections import defaultdict, Counter
from datetime import datetime

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

JSON_PATH = r"C:\Users\josea\Downloads\Telegram Desktop\DataExport_2026-04-22\result.json"
CUTOFF = datetime(2026, 1, 1)

CANAL_1_ID = 1642806869   # DT Investing
CANAL_2_ID = 2614601304   # Gold Standard

COST = 0.80   # spread + slippage por trade lot 0.01


def extract_text(field):
    if isinstance(field, str):
        return field
    if isinstance(field, list):
        return "".join(p if isinstance(p, str) else p.get("text", "") for p in field)
    return ""


def parse_dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def section(title):
    print(f"\n{'='*82}\n  {title}\n{'='*82}\n")


# ─── Carga ────────────────────────────────────────────────────────────────────

def load_chat(chat_id):
    with open(JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)
    chats = data.get("chats", {}).get("list", [])
    chat = next(c for c in chats if c["id"] == chat_id)
    msgs = chat.get("messages", [])
    return msgs


# ─── CANAL 1: parseo basado en sticker + texto siguiente ──────────────────────

def parse_canal1(msgs):
    """
    Empareja sticker → siguiente mensaje texto con TP/SL.
    Recoge updates en los siguientes ~2h:
      - "Move SL XXXX"  → SL movido manualmente
      - "TP1 hit", "TP2 hit", etc. → outcomes
      - "Close this one" → cierre manual
      - "SL hit"/"❌"   → SL final
    """
    sigs = []
    for i, m in enumerate(msgs):
        if m.get("media_type") != "sticker":
            continue
        try:
            dt = parse_dt(m["date"])
        except Exception:
            continue
        if dt < CUTOFF:
            continue

        # Buscamos siguiente mensaje texto BUY/SELL en siguientes 5 min
        sig_data = None
        for j in range(i + 1, min(i + 8, len(msgs))):
            nxt = msgs[j]
            try:
                ndt = parse_dt(nxt["date"])
            except Exception:
                continue
            if (ndt - dt).total_seconds() > 300:
                break
            txt = extract_text(nxt.get("text", ""))
            if not txt:
                continue
            upper = txt.upper()
            if "BUY" in upper or "SELL" in upper:
                # Tiene TPs?
                tps = []
                for tm in re.finditer(r"TP\d+\s*[:\s]\s*(\d{3,5}(?:\.\d{1,3})?)\b",
                                       txt, re.IGNORECASE):
                    try: tps.append(float(tm.group(1)))
                    except: pass
                if not tps:
                    continue
                sl = None
                slm = re.search(r"SL\s*[:\s]\s*(\d{3,5}(?:\.\d{1,3})?)\b",
                                txt, re.IGNORECASE)
                if slm:
                    try: sl = float(slm.group(1))
                    except: pass
                # Entry: número antes de TP1, normalmente en línea NOW
                entry = None
                em = re.search(r"NOW\s*@?\s*(\d{3,5}(?:\.\d{1,3})?)", txt, re.IGNORECASE)
                if em:
                    try: entry = float(em.group(1))
                    except: pass
                # Rango: dos números separados por -
                rng = None
                rm = re.search(r"(\d{3,5}(?:\.\d+)?)\s*[-/\u2013]\s*(\d{2,5}(?:\.\d+)?)", txt)
                if rm:
                    try:
                        a, b = float(rm.group(1)), float(rm.group(2))
                        b_str = rm.group(2)
                        if b < 100 and "." not in b_str:
                            base = int(a / 100) * 100
                            b = base + b
                        if abs(a - b) <= 50:
                            rng = (min(a,b), max(a,b))
                    except:
                        pass
                direction = "BUY" if "BUY" in upper else "SELL"
                sig_data = {
                    "id": m["id"],
                    "dt": dt,
                    "direction": direction,
                    "entry": entry,
                    "range": rng,
                    "tps": tps,
                    "sl": sl,
                    "raw_text": txt,
                }
                break

        if sig_data is None:
            continue

        # Buscar updates en las siguientes 4 horas
        updates = []
        max_tp_hit = 0
        sl_hit = False
        time_to_first_tp = None
        time_to_sl = None
        manual_close = False
        be_move_mentioned = False
        sl_move_to = None
        tp_times = {}

        # Regex estrictos para no confundir "Move SL to BE" con "SL hit"
        SL_HIT_RE = re.compile(
            r"\bsl\b\s*(?:hit|out)\b|\bstopped\s+out\b|\bstop[\s-]?loss\s+hit\b|"
            r"\bsl\s+taken\b|\btake\s+the\s+(?:sl|loss)\b",
            re.IGNORECASE,
        )
        TP_HIT_RE = re.compile(
            r"\btp\s*(\d)\s*(?:hit|reached|smashed|secured?|done|✅)|"
            r"\btp\s*(\d)\s*[✅]\s*",
            re.IGNORECASE,
        )

        for j in range(i + 1, min(i + 80, len(msgs))):
            nxt = msgs[j]
            try:
                ndt = parse_dt(nxt["date"])
            except Exception:
                continue
            if (ndt - dt).total_seconds() > 4 * 3600:
                break
            txt = extract_text(nxt.get("text", ""))
            if not txt:
                continue
            tl = txt.lower()
            updates.append({"dt": ndt, "txt": txt})

            # TP hits — busca PATRON ESTRICTO "TPN hit/reached/secured/✅"
            for tpm in TP_HIT_RE.finditer(txt):
                tp_n = int(tpm.group(1) or tpm.group(2))
                if tp_n > max_tp_hit:
                    max_tp_hit = tp_n
                    if time_to_first_tp is None:
                        time_to_first_tp = (ndt - dt).total_seconds() / 60
                if tp_n not in tp_times:
                    tp_times[tp_n] = (ndt - dt).total_seconds() / 60
            # También capturar "TP1 ✅ TP2 ✅ TP3 ✅" en una línea
            for tpm in re.finditer(r"\btp\s*(\d)\s*✅", txt, re.IGNORECASE):
                tp_n = int(tpm.group(1))
                if tp_n > max_tp_hit:
                    max_tp_hit = tp_n
                    if time_to_first_tp is None:
                        time_to_first_tp = (ndt - dt).total_seconds() / 60

            # SL hit — patron estricto
            if SL_HIT_RE.search(txt):
                sl_hit = True
                if time_to_sl is None:
                    time_to_sl = (ndt - dt).total_seconds() / 60

            # Move SL
            mv = re.search(r"move\s+sl\s+(?:to\s+)?(\d{3,5}(?:\.\d+)?)", tl)
            if mv:
                try: sl_move_to = float(mv.group(1))
                except: pass

            # BE move mentioned
            if re.search(r"move\s+sl\s+to\s+be|sl\s+to\s+be|risk[-\s]?free", tl):
                be_move_mentioned = True

            # Manual close
            if re.search(r"close\s+(?:this|the\s+trade|now)|exit\s+now", tl):
                manual_close = True

        sig_data["max_tp_hit"] = max_tp_hit
        sig_data["sl_hit"] = sl_hit
        sig_data["time_to_first_tp"] = time_to_first_tp
        sig_data["time_to_sl"] = time_to_sl
        sig_data["tp_times"] = tp_times
        sig_data["manual_close"] = manual_close
        sig_data["be_move_mentioned"] = be_move_mentioned
        sig_data["sl_move_to"] = sl_move_to
        sig_data["n_updates"] = len(updates)
        sigs.append(sig_data)

    return sigs


# ─── CANAL 2: parseo basado en mensaje único ──────────────────────────────────

def parse_canal2(msgs):
    """Mensajes 'XAU USD BUY/SELL NOW XXXX-YYYY' + replies con outcomes."""
    replies_to = defaultdict(list)
    for m in msgs:
        if "reply_to_message_id" in m:
            replies_to[m["reply_to_message_id"]].append(m)

    sigs = []
    for m in msgs:
        txt = extract_text(m.get("text", ""))
        if not txt:
            continue
        upper = txt.upper()
        if not ("BUY NOW" in upper or "SELL NOW" in upper):
            continue
        try:
            dt = parse_dt(m["date"])
        except Exception:
            continue
        if dt < CUTOFF:
            continue

        direction = "BUY" if "BUY" in upper else "SELL"
        rng = None
        rm = re.search(r"(\d{4}(?:\.\d+)?)\s*[-\u2013]\s*(\d{4}(?:\.\d+)?)", txt)
        if rm:
            try:
                lo, hi = float(rm.group(1)), float(rm.group(2))
                if lo > hi: lo, hi = hi, lo
                if 1500 <= lo <= 5000 and 1500 <= hi <= 5000:
                    rng = (lo, hi)
            except: pass

        tps = []
        for tm in re.finditer(r"TP\d+\s*[:\s]\s*(\d{3,5}(?:\.\d{1,3})?)\b",
                               txt, re.IGNORECASE):
            try: tps.append(float(tm.group(1)))
            except: pass

        sl = None
        slm = re.search(r"SL\s*[:\s]\s*(\d{3,5}(?:\.\d{1,3})?)\b", txt, re.IGNORECASE)
        if slm:
            try: sl = float(slm.group(1))
            except: pass

        # Replies → outcome
        rs = []
        for r in replies_to.get(m["id"], []):
            r_txt = extract_text(r.get("text", ""))
            try:
                r_dt = parse_dt(r["date"])
            except: continue
            rs.append({"text": r_txt, "dt": r_dt, "tl": r_txt.lower()})
        rs.sort(key=lambda x: x["dt"])

        max_tp_hit = 0
        sl_hit = False
        time_to_sl = None
        time_to_first_tp = None
        tp_times = {}

        SL_HIT_RE = re.compile(
            r"\bsl\b\s*(?:hit|out)\b|\bstopped\s+out\b|\bstop[\s-]?loss\s+hit\b|"
            r"\bsl\s+taken\b|\btake\s+the\s+(?:sl|loss)\b",
            re.IGNORECASE,
        )
        TP_HIT_PATTERNS = [
            re.compile(r"\btp\s*(\d)\s*(?:hit|reached|smashed|secured?|done|✅)",
                       re.IGNORECASE),
            re.compile(r"\btp\s*(\d)\s*✅", re.IGNORECASE),
        ]

        for r in rs:
            text = r["text"]
            for pat in TP_HIT_PATTERNS:
                for tpm in pat.finditer(text):
                    tp_n = int(tpm.group(1))
                    if tp_n > max_tp_hit:
                        max_tp_hit = tp_n
                        if time_to_first_tp is None:
                            time_to_first_tp = (r["dt"] - dt).total_seconds() / 60
                    if tp_n not in tp_times:
                        tp_times[tp_n] = (r["dt"] - dt).total_seconds() / 60

            if SL_HIT_RE.search(text):
                sl_hit = True
                if time_to_sl is None:
                    time_to_sl = (r["dt"] - dt).total_seconds() / 60

        sigs.append({
            "id": m["id"], "dt": dt, "direction": direction,
            "range": rng, "tps": tps, "sl": sl,
            "max_tp_hit": max_tp_hit, "sl_hit": sl_hit,
            "time_to_sl": time_to_sl, "time_to_first_tp": time_to_first_tp,
            "tp_times": tp_times,
        })
    return sigs


# ─── Caracterización ──────────────────────────────────────────────────────────

def characterize(sigs, name):
    section(f"CARACTERIZACIÓN — {name} (n={len(sigs)})")
    if not sigs: return

    has_range = sum(1 for s in sigs if s.get("range"))
    has_entry = sum(1 for s in sigs if s.get("entry"))
    has_sl = sum(1 for s in sigs if s.get("sl"))
    has_tps = sum(1 for s in sigs if s.get("tps"))

    print(f"  Tiene rango (X-Y):       {has_range:4d}/{len(sigs)} ({100*has_range/len(sigs):.0f}%)")
    print(f"  Tiene entry único:       {has_entry:4d}/{len(sigs)} ({100*has_entry/len(sigs):.0f}%)")
    print(f"  Tiene SL:                {has_sl:4d}/{len(sigs)} ({100*has_sl/len(sigs):.0f}%)")
    print(f"  Tiene TPs:               {has_tps:4d}/{len(sigs)} ({100*has_tps/len(sigs):.0f}%)")

    # Distribución # TPs
    tp_counts = Counter(len(s.get("tps", [])) for s in sigs)
    print(f"\n  # TPs distribución:")
    for n, c in sorted(tp_counts.items()):
        print(f"    {n} TPs: {c} ({100*c/len(sigs):.0f}%)")

    # Outcomes
    sl_count = sum(1 for s in sigs if s.get("sl_hit"))
    tp_dist = Counter(s.get("max_tp_hit", 0) for s in sigs if s.get("tps"))
    print(f"\n  Outcomes:")
    print(f"    SL hit:               {sl_count} ({100*sl_count/len(sigs):.0f}%)")
    for tp_n in sorted(tp_dist.keys()):
        c = tp_dist[tp_n]
        print(f"    Max TP{tp_n}:               {c} ({100*c/len(sigs):.0f}%)")

    # Ranges (canal 2)
    if has_range > 5:
        sizes = [abs(s["range"][1] - s["range"][0]) for s in sigs if s.get("range")]
        print(f"\n  Tamaño rango (US$):  median={statistics.median(sizes):.1f}  "
              f"mean={statistics.mean(sizes):.1f}  max={max(sizes):.1f}")

    # Time to TP1
    t1s = [s["time_to_first_tp"] for s in sigs if s.get("time_to_first_tp")]
    if t1s:
        print(f"\n  Time to TP1:         median={statistics.median(t1s):.1f}min  "
              f"mean={statistics.mean(t1s):.1f}min")

    t_sls = [s["time_to_sl"] for s in sigs if s.get("time_to_sl")]
    if t_sls:
        print(f"  Time to SL:          median={statistics.median(t_sls):.1f}min  "
              f"mean={statistics.mean(t_sls):.1f}min")


# ─── Simuladores específicos por canal ────────────────────────────────────────

def simulate_canal1_one_pos(sig, exit_tp_n=2, be_at_tp=1):
    """
    Canal 1: 1 posición a precio único. Entry = sig['entry'].
    exit_tp_n: cierra todo en TP_N. Si be_at_tp se alcanza → SL movido a BE.

    Modelo:
      - Si max_tp_hit >= exit_tp_n → ganancia hasta tps[exit_tp_n-1]
      - Si max_tp_hit < exit_tp_n y >= be_at_tp y luego SL → cierre en BE (-cost)
      - Si max_tp_hit < be_at_tp y SL → SL completo
      - Si no hay outcome → BE (-cost)
    """
    if not sig.get("tps") or sig.get("sl") is None or sig.get("entry") is None:
        return None
    entry = sig["entry"]
    tps = sig["tps"]
    sl = sig["sl"]
    direction = sig["direction"]
    max_tp = sig.get("max_tp_hit", 0)
    sl_hit = sig.get("sl_hit", False)

    target_idx = min(exit_tp_n - 1, len(tps) - 1)

    if max_tp >= exit_tp_n:
        # Cierra en TP_N
        target = tps[target_idx]
        profit = (target - entry) if direction == "BUY" else (entry - target)
        return profit - COST

    # max_tp < exit_tp_n
    if max_tp >= be_at_tp:
        # Movió SL a BE → si después tocó SL, sale en BE; si no, sale al max TP alcanzado
        if sl_hit:
            return -COST  # BE
        # No SL → cerró en max TP alcanzado (manualmente o time)
        target = tps[min(max_tp - 1, len(tps) - 1)]
        profit = (target - entry) if direction == "BUY" else (entry - target)
        return profit - COST

    # No alcanzó ni siquiera el BE level
    if sl_hit:
        loss = abs(entry - sl)
        return -(loss + COST)

    return -COST  # BE costes


def simulate_canal2_extremos(sig, mode="2pos_extremos", exit_tp_n=4, be_at_tp=1,
                               include_no_outcome=False):
    """
    Canal 2 — operativa REAL del canal: solo abre en los EXTREMOS del rango.

      mode="1pos":         solo market en extremo cercano (sin DCA)
      mode="2pos_extremos": market en extremo cercano + límite en extremo lejano
      mode="dca_intra":    DCA escalonado intra-rango cada $1 (modelo viejo)

    BE move: si max_tp >= be_at_tp → SL→BE para todas las posiciones abiertas.

    include_no_outcome=False: descarta señales sin SL_hit y sin TP detectado
                              (asumiendo problema de parser, no de operativa).
    """
    if not sig.get("range") or not sig.get("tps") or sig.get("sl") is None:
        return None

    rl, rh = sig["range"]
    range_size = rh - rl
    direction = sig["direction"]
    entry = rh if direction == "BUY" else rl  # market inicial en el extremo cercano
    tps = sig["tps"]
    sl = sig["sl"]
    max_tp = sig.get("max_tp_hit", 0)
    sl_hit = sig.get("sl_hit", False)

    # Si no se detecta nada (ni SL ni TP), excluimos para no sesgar negativo
    if not include_no_outcome and not sl_hit and max_tp == 0:
        return None

    # Configuración de posiciones según modo
    if mode == "1pos":
        offsets = [0.0]
    elif mode == "2pos_extremos":
        # 2ª posición en el otro extremo del rango (a -range_size del entry para BUY)
        offsets = [0.0, range_size]
    elif mode == "dca_intra":
        # DCA cada $1
        n_intra = int(range_size) + 1
        offsets = [float(i) for i in range(n_intra)]
    else:
        return None

    n_pos = len(offsets)
    target_idx = min(exit_tp_n - 1, len(tps) - 1)

    # Determina qué posiciones se llegaron a abrir realmente.
    # Para modo "2pos_extremos": la 2ª solo se abre si el precio recorrió el rango.
    # Aproximación: si max_tp >= 1, el precio hizo el camino → ambas abiertas.
    # Si SL_hit y NO llegó a TP1, también ambas abiertas (precio recorrió rango y luego SL).
    # Si NO SL y NO TP → solo la 1ª (precio se quedó en el medio sin recorrer).
    n_opened = n_pos  # asumimos todas abiertas para simplicidad

    if max_tp >= exit_tp_n:
        # Todas las abiertas cierran en TP_N
        pl = 0
        for i in range(n_opened):
            pos_entry = entry - offsets[i] if direction == "BUY" else entry + offsets[i]
            target = tps[target_idx]
            profit = (target - pos_entry) if direction == "BUY" else (pos_entry - target)
            pl += profit - COST
        return pl

    if max_tp >= be_at_tp:
        # BE move: si después SL → BE; si no → cierre en TP máximo alcanzado
        if sl_hit:
            return -n_opened * COST
        target = tps[min(max_tp - 1, len(tps) - 1)]
        pl = 0
        for i in range(n_opened):
            pos_entry = entry - offsets[i] if direction == "BUY" else entry + offsets[i]
            profit = (target - pos_entry) if direction == "BUY" else (pos_entry - target)
            pl += profit - COST
        return pl

    if sl_hit:
        # SL completo en las posiciones abiertas
        pl = 0
        for i in range(n_opened):
            pos_entry = entry - offsets[i] if direction == "BUY" else entry + offsets[i]
            loss = (pos_entry - sl) if direction == "BUY" else (sl - pos_entry)
            pl += -(loss + COST)
        return pl

    # No outcome (excluido arriba si include_no_outcome=False)
    return -n_opened * COST


# ─── Eval ─────────────────────────────────────────────────────────────────────

def eval_strategy(sigs, sim_fn, **kwargs):
    pls = [sim_fn(s, **kwargs) for s in sigs]
    pls = [p for p in pls if p is not None]
    if not pls:
        return None
    eq, peak, max_dd = 0, 0, 0
    for p in pls:
        eq += p
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
    avg = sum(pls) / len(pls)
    sd = statistics.stdev(pls) if len(pls) > 1 else 0
    sharpe = avg / sd if sd > 0 else 0
    wr = sum(1 for p in pls if p > 0) / len(pls) * 100
    return {"total": sum(pls), "avg": avg, "wr": wr,
            "max_dd": max_dd, "sharpe": sharpe, "n": len(pls), "pls": pls}


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("Cargando JSON ~500MB...", flush=True)

    print("\n>> CANAL 1 (DT Investing)")
    msgs1 = load_chat(CANAL_1_ID)
    print(f"   Mensajes totales: {len(msgs1)}")
    sigs1 = parse_canal1(msgs1)
    print(f"   Señales 2026 detectadas: {len(sigs1)}")

    print("\n>> CANAL 2 (Gold Standard)")
    msgs2 = load_chat(CANAL_2_ID)
    print(f"   Mensajes totales: {len(msgs2)}")
    sigs2 = parse_canal2(msgs2)
    print(f"   Señales 2026 detectadas: {len(sigs2)}")

    characterize(sigs1, "CANAL 1 — DT Investing")
    characterize(sigs2, "CANAL 2 — Gold Standard")

    # ── CANAL 1 — Estrategias ──
    section("CANAL 1 — Estrategias 1-pos (entry único)")
    sigs1_clean = [s for s in sigs1 if s.get("entry") and s.get("tps") and s.get("sl")]
    print(f"  Señales válidas: {len(sigs1_clean)}\n")
    print(f"  {'Estrategia':<48} {'Total':>9} {'Avg':>7} {'WR%':>5} {'MaxDD':>8} {'Sharpe':>7}")
    print(f"  {'-'*82}")

    for exit_tp in [1, 2, 3, 4]:
        for be in [0, 1, 2]:  # 0 = sin BE
            if be >= exit_tp: continue
            name = f"Cierre TP{exit_tp}" + (f" + BE en TP{be}" if be > 0 else " (sin BE)")
            m = eval_strategy(sigs1_clean, simulate_canal1_one_pos,
                              exit_tp_n=exit_tp, be_at_tp=be if be > 0 else 99)
            if m:
                print(f"  {name:<48} ${m['total']:+8.1f} ${m['avg']:+6.2f} "
                      f"{m['wr']:4.0f}% ${m['max_dd']:7.1f} {m['sharpe']:6.3f}")

    # ── CANAL 2 — Estrategias por modo de apertura ──
    section("CANAL 2 — operativa REAL del canal: solo extremos del rango")
    sigs2_clean = [s for s in sigs2 if s.get("range") and s.get("tps") and s.get("sl")
                   and abs(s["range"][1] - s["range"][0]) <= 30]   # filtra rango outlier
    print(f"  Señales válidas (rango <= $30): {len(sigs2_clean)}")
    print("  (excluyendo señales sin SL_hit y sin TP detectado: probable ruido del parser)\n")
    print(f"  {'Estrategia':<48} {'Total':>9} {'Avg':>7} {'WR%':>5} {'MaxDD':>8} {'Sharpe':>7}  N")
    print(f"  {'-'*88}")

    modes = [("1pos", "1 pos market only"),
             ("2pos_extremos", "2 pos extremos rango"),
             ("dca_intra", "DCA $1 intra-rango (modelo viejo)")]

    for mode_id, mode_name in modes:
        for exit_tp in [2, 3, 4, 5]:
            for be in [0, 1]:
                if be >= exit_tp: continue
                label = f"{mode_name}, TP{exit_tp}" + (f", BE TP{be}" if be > 0 else "")
                m = eval_strategy(sigs2_clean, simulate_canal2_extremos,
                                  mode=mode_id, exit_tp_n=exit_tp,
                                  be_at_tp=be if be > 0 else 99,
                                  include_no_outcome=False)
                if m:
                    print(f"  {label:<48} ${m['total']:+8.1f} ${m['avg']:+6.2f} "
                          f"{m['wr']:4.0f}% ${m['max_dd']:7.1f} {m['sharpe']:6.3f}  {m['n']}")
        print()

    # ── Combinado ──
    section("RESUMEN — total combinado mejor estrategia por canal")
    # Mejor c1 por sharpe
    best_c1 = max(
        [(f"TP{t}+BE{b}", eval_strategy(sigs1_clean, simulate_canal1_one_pos,
                                          exit_tp_n=t, be_at_tp=b if b > 0 else 99))
         for t in [1,2,3,4] for b in [0,1,2] if b < t],
        key=lambda x: x[1]["sharpe"] if x[1] else -99
    )
    c2_candidates = []
    for mode_id, mode_name in [("1pos","1pos"),("2pos_extremos","2pos"),("dca_intra","dca")]:
        for t in [2,3,4,5]:
            for b in [0,1]:
                if b >= t: continue
                m = eval_strategy(sigs2_clean, simulate_canal2_extremos,
                                  mode=mode_id, exit_tp_n=t, be_at_tp=b if b > 0 else 99,
                                  include_no_outcome=False)
                if m:
                    label = f"{mode_name}+TP{t}+BE{b}"
                    c2_candidates.append((label, m))
    best_c2 = max(c2_candidates, key=lambda x: x[1]["sharpe"])

    print(f"  CANAL 1 mejor por sharpe: {best_c1[0]:<25}  Total ${best_c1[1]['total']:+7.1f}  "
          f"Avg ${best_c1[1]['avg']:+5.2f}  WR {best_c1[1]['wr']:.0f}%  Sharpe {best_c1[1]['sharpe']:.3f}")
    print(f"  CANAL 2 mejor por sharpe: {best_c2[0]:<25}  Total ${best_c2[1]['total']:+7.1f}  "
          f"Avg ${best_c2[1]['avg']:+5.2f}  WR {best_c2[1]['wr']:.0f}%  Sharpe {best_c2[1]['sharpe']:.3f}")

    print(f"\n  TOTAL COMBINADO 2026: ${best_c1[1]['total'] + best_c2[1]['total']:+8.1f}")


if __name__ == "__main__":
    main()
