"""
analyze_deep.py — Análisis exhaustivo de Canal 1 y Canal 2.

Para cada señal extrae:
  - Datos básicos: dirección, rango, TPs, SL
  - Métricas derivadas: tamaño rango, R:R, distancias TP/SL
  - Replies de gestión: TPs alcanzados, SL hit, BE, cierre manual
  - Tiempos: minutos hasta primer reply, hasta último reply (= duración trade)
  - Patrón temporal: hora UTC, día de la semana

Genera:
  - Estadísticas comparativas por canal
  - Win rate inferido (basado en mensajes "TP hit" / "SL hit" del canal)
  - Histogramas: tamaño rango, R:R, distancia TP, distancia SL
  - Análisis horario
  - Estimación de rentabilidad por canal
  - CSV detallado con todo

Uso:
    python analyze_deep.py --json "C:/.../result.json"
    python analyze_deep.py --json "C:/.../result.json" --csv-detail signals_detail.csv
"""

import json
import re
import csv
import argparse
import sys
import io
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Forzar UTF-8 en consola Windows (evita UnicodeEncodeError con cp1252)
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))
from parser import (
    is_canal2_entry, is_canal1_signal_text,
    parse_canal2, parse_canal1_text,
)

CANAL_1_ID = 1642806869   # DT Investing
CANAL_2_ID = 2614601304   # Gold Standard


# ═════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def extract_text(field) -> str:
    if isinstance(field, str):
        return field
    if isinstance(field, list):
        return "".join(item if isinstance(item, str) else item.get("text", "") for item in field)
    return ""


def is_sticker(msg: dict) -> bool:
    return msg.get("media_type") == "sticker" or "sticker_emoji" in msg


def parse_dt(s: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def load_channel_msgs(data: dict, channel_id: int) -> list[dict]:
    for chat in data.get("chats", {}).get("list", []):
        if chat.get("id") == channel_id:
            return chat.get("messages", [])
    return []


# ═════════════════════════════════════════════════════════════════════════════
#  DETECTOR DE OUTCOMES (qué pasó tras la señal)
# ═════════════════════════════════════════════════════════════════════════════

# Detectar TP hit explícito en los replies/edits
RE_TP_HIT = [
    re.compile(r"tp\s*(\d)\s*(?:hit|reached|done|smashed|secured|nailed|achieved|✅|💰|🎯|target)", re.I),
    re.compile(r"target\s*(\d)\s*(?:hit|reached|done|✅|💰)", re.I),
    re.compile(r"(\d)(?:st|nd|rd|th)?\s*tp\s*(?:hit|reached|done|✅)", re.I),
]

RE_SL_HIT = [
    re.compile(r"sl\s*(?:hit|triggered|out|❌|stopped)", re.I),
    re.compile(r"stop\s*loss\s*(?:hit|triggered|out)", re.I),
    re.compile(r"trade\s*closed.*loss", re.I),
    re.compile(r"\bstopped\s*out\b", re.I),
]

RE_BE = [
    re.compile(r"\bmove\s*(?:sl|stop)\s*to\s*be\b", re.I),
    re.compile(r"\bmove\s*(?:sl|stop).*break.?even\b", re.I),
    re.compile(r"\b0\s*%\s*risk\b", re.I),
    re.compile(r"risk.?free", re.I),
    re.compile(r"move\s*sl\s*to\s*entry", re.I),
]

RE_CLOSE = [
    re.compile(r"close\s+(?:the\s+)?(?:trade|position|all|entries|rest)", re.I),
    re.compile(r"close\s+(?:my|your)\s+(?:trade|positions?)", re.I),
    re.compile(r"\bexit\s+(?:trade|now|all)", re.I),
    re.compile(r"book\s+(?:full\s+)?profit", re.I),
]

RE_PIPS_PROFIT = re.compile(r"\+\s*(\d{2,4})\s*pips?", re.I)


def detect_outcomes(reply_texts: list[str]) -> dict:
    """De la lista de mensajes posteriores a la señal, detecta qué pasó."""
    tps_hit = set()
    sl_hit = False
    be_set = False
    closed_manual = False
    pips_mentioned: list[int] = []

    for txt in reply_texts:
        if not txt:
            continue
        # TP hit
        for pat in RE_TP_HIT:
            for m in pat.finditer(txt):
                try:
                    n = int(m.group(1))
                    if 1 <= n <= 10:
                        tps_hit.add(n)
                except Exception:
                    pass
        # SL hit
        if any(p.search(txt) for p in RE_SL_HIT):
            sl_hit = True
        # BE
        if any(p.search(txt) for p in RE_BE):
            be_set = True
        # Close manual
        if any(p.search(txt) for p in RE_CLOSE):
            closed_manual = True
        # Pips menciones
        for m in RE_PIPS_PROFIT.finditer(txt):
            try:
                pips_mentioned.append(int(m.group(1)))
            except Exception:
                pass

    return {
        "tps_hit": sorted(tps_hit),
        "max_tp": max(tps_hit) if tps_hit else 0,
        "sl_hit": sl_hit,
        "be_set": be_set,
        "closed_manual": closed_manual,
        "pips_mentioned": pips_mentioned,
        "max_pips": max(pips_mentioned) if pips_mentioned else 0,
    }


# ═════════════════════════════════════════════════════════════════════════════
#  EXTRACCIÓN DE SEÑALES CON CONTEXTO COMPLETO
# ═════════════════════════════════════════════════════════════════════════════

def build_signal_record(channel: str, msg_id: int, sig_dt: Optional[datetime],
                         parsed: dict, raw_text: str, replies: list[dict],
                         sticker_id: Optional[int] = None) -> dict:
    rng = parsed.get("range")
    tps = parsed.get("tps", [])
    sl  = parsed.get("sl")
    direction = parsed["direction"]

    reply_texts = [extract_text(r.get("text", "")) for r in replies]
    reply_dates = [parse_dt(r.get("date", "")) for r in replies if parse_dt(r.get("date", ""))]

    first_reply_min = None
    last_reply_min  = None
    if reply_dates and sig_dt:
        first_reply_min = (min(reply_dates) - sig_dt).total_seconds() / 60.0
        last_reply_min  = (max(reply_dates) - sig_dt).total_seconds() / 60.0

    outcomes = detect_outcomes(reply_texts)

    # Distancias y R:R
    range_size = (rng[1] - rng[0]) if rng else None
    entry_edge = (rng[1] if direction == "BUY" else rng[0]) if rng else None
    sl_edge    = (rng[0] if direction == "BUY" else rng[1]) if rng else None

    tp1_dist = abs(tps[0]  - entry_edge) if (tps and entry_edge is not None) else None
    tpL_dist = abs(tps[-1] - entry_edge) if (tps and entry_edge is not None) else None
    sl_dist  = abs(sl_edge - sl) if (sl is not None and sl_edge is not None) else None

    rr1 = (tp1_dist / sl_dist) if (tp1_dist and sl_dist) else None
    rrL = (tpL_dist / sl_dist) if (tpL_dist and sl_dist) else None

    return {
        "channel":         channel,
        "message_id":      msg_id,
        "sticker_id":      sticker_id,
        "date":            sig_dt.isoformat() if sig_dt else "",
        "hour_utc":        sig_dt.hour if sig_dt else None,
        "weekday":         sig_dt.strftime("%a") if sig_dt else "",
        "direction":       direction,
        "range_low":       rng[0] if rng else None,
        "range_high":      rng[1] if rng else None,
        "range_size":      range_size,
        "num_tps":         len(tps),
        "tps":             tps,
        "tp1":             tps[0]  if tps else None,
        "tp_last":         tps[-1] if tps else None,
        "sl":              sl,
        "tp1_dist":        tp1_dist,
        "tp_last_dist":    tpL_dist,
        "sl_dist":         sl_dist,
        "rr_to_tp1":       rr1,
        "rr_to_tp_last":   rrL,
        "num_replies":     len(replies),
        "first_reply_min": first_reply_min,
        "last_reply_min":  last_reply_min,
        "duration_min":    last_reply_min,  # asumimos que último reply ≈ cierre del trade
        "tps_hit":         outcomes["tps_hit"],
        "max_tp_hit":      outcomes["max_tp"],
        "sl_hit":          outcomes["sl_hit"],
        "be_set":          outcomes["be_set"],
        "closed_manual":   outcomes["closed_manual"],
        "max_pips_mentioned": outcomes["max_pips"],
        "raw_text":        raw_text,
        "reply_texts":     reply_texts,
    }


def extract_canal2(messages: list[dict]) -> list[dict]:
    """Canal 2: cada entry message edita su texto, los replies van al mismo msg_id."""
    replies = defaultdict(list)
    for m in messages:
        if m.get("type") != "message":
            continue
        rid = m.get("reply_to_message_id")
        if rid:
            replies[rid].append(m)

    out = []
    for msg in messages:
        if msg.get("type") != "message":
            continue
        if msg.get("reply_to_message_id"):
            continue

        text = extract_text(msg.get("text", ""))
        if not is_canal2_entry(text):
            continue

        parsed = parse_canal2(text)
        if "direction" not in parsed:
            continue

        msg_replies = sorted(replies.get(msg["id"], []), key=lambda r: r.get("id", 0))
        sig_dt = parse_dt(msg.get("date", ""))
        out.append(build_signal_record("canal2", msg["id"], sig_dt, parsed, text, msg_replies))

    return out


def extract_canal1(messages: list[dict]) -> list[dict]:
    """
    Canal 1: empareja sticker + texto posterior.
    Los replies pueden ir al sticker O al texto. Asociar ambos a la señal completa.
    """
    by_id = {m["id"]: m for m in messages if m.get("type") == "message"}

    replies = defaultdict(list)
    for m in messages:
        if m.get("type") != "message":
            continue
        rid = m.get("reply_to_message_id")
        if rid:
            replies[rid].append(m)

    sorted_msgs = sorted(messages, key=lambda m: m.get("id", 0))
    msg_index = {m["id"]: i for i, m in enumerate(sorted_msgs) if m.get("type") == "message"}

    out = []
    used_text_ids = set()  # evitar usar el mismo texto para 2 stickers

    for msg in sorted_msgs:
        if msg.get("type") != "message":
            continue
        if not is_sticker(msg):
            continue
        if msg.get("reply_to_message_id"):
            continue  # ignora stickers que son replies

        sticker_id = msg["id"]
        sticker_dt = parse_dt(msg.get("date", ""))
        if not sticker_dt:
            continue

        # Buscar texto posterior dentro de 10 min que sea is_canal1_signal_text
        idx = msg_index.get(sticker_id, 0)
        text_msg = None
        for next_msg in sorted_msgs[idx+1:idx+150]:  # mira hasta 150 mensajes posteriores
            if next_msg.get("type") != "message":
                continue
            if next_msg.get("reply_to_message_id"):
                continue
            if next_msg["id"] in used_text_ids:
                continue
            txt = extract_text(next_msg.get("text", ""))
            if not txt or not is_canal1_signal_text(txt):
                continue
            n_dt = parse_dt(next_msg.get("date", ""))
            if not n_dt:
                continue
            if (n_dt - sticker_dt).total_seconds() > 600:  # más de 10 min, abandonar
                break
            text_msg = next_msg
            used_text_ids.add(next_msg["id"])
            break

        if not text_msg:
            continue

        text = extract_text(text_msg.get("text", ""))
        parsed = parse_canal1_text(text)
        if "direction" not in parsed:
            continue

        # Replies al sticker O al texto
        all_replies = replies.get(sticker_id, []) + replies.get(text_msg["id"], [])
        all_replies.sort(key=lambda r: r.get("id", 0))

        out.append(build_signal_record(
            "canal1", text_msg["id"], sticker_dt, parsed, text, all_replies,
            sticker_id=sticker_id
        ))

    return out


# ═════════════════════════════════════════════════════════════════════════════
#  ESTADÍSTICAS Y REPORTES
# ═════════════════════════════════════════════════════════════════════════════

def hist(values: list, bins=None, label="", unit="", max_bar=60):
    """Histograma textual simple."""
    if not values:
        print(f"  (sin datos para {label})")
        return
    rounded = [round(v) for v in values]
    cnt = Counter(rounded)
    top = max(cnt.values())
    print(f"  {label} (n={len(values)}, media={statistics.mean(values):.2f}, mediana={statistics.median(values):.2f})")
    for v in sorted(cnt):
        bar = "█" * int(cnt[v] / top * max_bar)
        pct = cnt[v] / len(values) * 100
        print(f"    {v:>5}{unit}: {bar} {cnt[v]} ({pct:.1f}%)")


def section(title):
    print(f"\n{'═'*70}")
    print(f"  {title}")
    print(f"{'═'*70}")


def basic_stats(signals: list[dict], channel: str):
    n = len(signals)
    if not n:
        print(f"\n[{channel}] Sin señales.")
        return

    section(f"{channel.upper()} — VOLUMEN Y SESGO ({n} señales)")
    buys  = sum(1 for s in signals if s["direction"] == "BUY")
    sells = n - buys
    print(f"  BUY: {buys} ({buys/n*100:.0f}%)  |  SELL: {sells} ({sells/n*100:.0f}%)")

    # Datos completos
    with_rng  = sum(1 for s in signals if s["range_low"])
    with_tps  = sum(1 for s in signals if s["tps"])
    with_sl   = sum(1 for s in signals if s["sl"])
    with_repl = sum(1 for s in signals if s["num_replies"] > 0)
    print(f"  Con rango:  {with_rng}/{n}   Con TPs: {with_tps}/{n}   Con SL: {with_sl}/{n}")
    print(f"  Con replies de gestión: {with_repl}/{n} ({with_repl/n*100:.0f}%)")

    # Distribución por número de TPs (Canal 1 puede tener menos TPs)
    tp_counts = Counter(s["num_tps"] for s in signals if s["num_tps"])
    print(f"\n  Número de TPs por señal:")
    for k in sorted(tp_counts):
        bar = "█" * int(tp_counts[k] / max(tp_counts.values()) * 40)
        pct = tp_counts[k] / n * 100
        print(f"    {k} TPs: {bar} {tp_counts[k]} ({pct:.0f}%)")


def time_stats(signals: list[dict], channel: str):
    section(f"{channel.upper()} — DISTRIBUCIÓN HORARIA Y SEMANAL")

    # Por hora UTC
    hours = Counter(s["hour_utc"] for s in signals if s["hour_utc"] is not None)
    print(f"\n  Por hora UTC:")
    if hours:
        top = max(hours.values())
        for h in range(24):
            c = hours.get(h, 0)
            bar = "█" * int(c / top * 60) if top else ""
            print(f"    {h:02d}h  {bar} {c}")

    # Por día semana
    weekdays = Counter(s["weekday"] for s in signals if s["weekday"])
    print(f"\n  Por día de semana:")
    order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    if weekdays:
        top = max(weekdays.values())
        for d in order:
            c = weekdays.get(d, 0)
            bar = "█" * int(c / top * 60) if top else ""
            print(f"    {d}  {bar} {c}")


def range_and_levels_stats(signals: list[dict], channel: str):
    section(f"{channel.upper()} — TAMAÑO DEL RANGO Y NIVELES")

    sizes = [s["range_size"] for s in signals if s["range_size"] and 0 < s["range_size"] < 30]
    print(f"\n  Tamaño del rango (range_high - range_low):")
    hist(sizes, label="rango", unit="$")

    tp1_d = [s["tp1_dist"] for s in signals if s["tp1_dist"] and 0 < s["tp1_dist"] < 50]
    print(f"\n  Distancia TP1 desde entry:")
    hist(tp1_d, label="TP1 dist", unit="$")

    tpL_d = [s["tp_last_dist"] for s in signals if s["tp_last_dist"] and 0 < s["tp_last_dist"] < 200]
    print(f"\n  Distancia ÚLTIMO TP desde entry (objetivo final):")
    hist(tpL_d, label="TPlast dist", unit="$")

    sl_d = [s["sl_dist"] for s in signals if s["sl_dist"] and 0 < s["sl_dist"] < 30]
    print(f"\n  Distancia SL desde edge del rango:")
    hist(sl_d, label="SL dist", unit="$")


def rr_stats(signals: list[dict], channel: str):
    section(f"{channel.upper()} — RATIO RIESGO/BENEFICIO (R:R)")

    rr1 = [s["rr_to_tp1"]    for s in signals if s["rr_to_tp1"]    and 0 < s["rr_to_tp1"]    < 10]
    rrL = [s["rr_to_tp_last"] for s in signals if s["rr_to_tp_last"] and 0 < s["rr_to_tp_last"] < 50]

    if rr1:
        print(f"\n  R:R hasta TP1 — n={len(rr1)}, media={statistics.mean(rr1):.2f}, mediana={statistics.median(rr1):.2f}")
        if statistics.mean(rr1) < 1:
            print(f"    ⚠ R:R < 1 → necesitas >50% win rate para break-even")
        else:
            print(f"    ✓ R:R ≥ 1 → con 50% WR ya eres positivo en TP1")

    if rrL:
        print(f"\n  R:R hasta ÚLTIMO TP — n={len(rrL)}, media={statistics.mean(rrL):.2f}, mediana={statistics.median(rrL):.2f}")


def duration_stats(signals: list[dict], channel: str):
    section(f"{channel.upper()} — DURACIÓN DE LOS TRADES")

    durs = [s["duration_min"] for s in signals if s["duration_min"] and 0 < s["duration_min"] < 1440]
    if not durs:
        print("  (sin datos de duración — replies vacíos)")
        return

    print(f"\n  Duración hasta último reply (proxy del cierre):")
    print(f"    n={len(durs)}, media={statistics.mean(durs):.0f}min, mediana={statistics.median(durs):.0f}min")

    # Bucketizar por horas
    buckets = Counter()
    for d in durs:
        if   d < 30:    buckets["< 30min"] += 1
        elif d < 60:    buckets["30-60min"] += 1
        elif d < 120:   buckets["1-2h"] += 1
        elif d < 240:   buckets["2-4h"] += 1
        elif d < 480:   buckets["4-8h"] += 1
        else:           buckets["8h+"] += 1

    order = ["< 30min", "30-60min", "1-2h", "2-4h", "4-8h", "8h+"]
    top = max(buckets.values())
    print(f"\n  Distribución por buckets:")
    for k in order:
        c = buckets.get(k, 0)
        bar = "█" * int(c / top * 50) if top else ""
        pct = c / len(durs) * 100
        print(f"    {k:>10}  {bar} {c} ({pct:.0f}%)")

    # Tiempo al primer reply (proxy de cuándo empieza a moverse)
    firsts = [s["first_reply_min"] for s in signals if s["first_reply_min"] and 0 < s["first_reply_min"] < 240]
    if firsts:
        print(f"\n  Tiempo hasta PRIMER reply (proxy 'cuándo empieza a pasar algo'):")
        print(f"    n={len(firsts)}, media={statistics.mean(firsts):.0f}min, mediana={statistics.median(firsts):.0f}min")


def outcome_stats(signals: list[dict], channel: str):
    section(f"{channel.upper()} — OUTCOMES INFERIDOS DESDE REPLIES")

    n = len(signals)
    with_replies = [s for s in signals if s["num_replies"] > 0]
    nr = len(with_replies)
    print(f"\n  Señales con al menos 1 reply: {nr}/{n} ({nr/n*100:.0f}%)")

    if not nr:
        print("  (no hay replies en este canal — outcomes no disponibles)")
        return

    sl_count    = sum(1 for s in with_replies if s["sl_hit"])
    be_count    = sum(1 for s in with_replies if s["be_set"])
    close_count = sum(1 for s in with_replies if s["closed_manual"])
    any_tp      = sum(1 for s in with_replies if s["max_tp_hit"] >= 1)

    print(f"\n  De las {nr} con replies (lo que el canal CONFIRMÓ por mensaje):")
    print(f"    SL hit menc.:  {sl_count} ({sl_count/nr*100:.0f}%)")
    print(f"    BE menc.:      {be_count} ({be_count/nr*100:.0f}%)")
    print(f"    Close manual:  {close_count} ({close_count/nr*100:.0f}%)")
    print(f"    Algún TP hit:  {any_tp} ({any_tp/nr*100:.0f}%)")

    # Distribución de max TP hit
    max_tps = Counter(s["max_tp_hit"] for s in with_replies)
    print(f"\n  Máximo TP alcanzado (según mensajes del canal):")
    print(f"    (TP=0 significa: sin confirmación de TP en los replies)")
    top = max(max_tps.values()) if max_tps else 1
    for k in sorted(max_tps):
        bar = "█" * int(max_tps[k] / top * 50)
        pct = max_tps[k] / nr * 100
        label = "ninguno detectado" if k == 0 else f"hasta TP{k}"
        print(f"    {label:>22}  {bar} {max_tps[k]} ({pct:.0f}%)")

    # Win rate inferido (señales con cualquier TP hit / total señales con info)
    info_signals = [s for s in with_replies if s["sl_hit"] or s["max_tp_hit"] >= 1]
    if info_signals:
        wins = sum(1 for s in info_signals if s["max_tp_hit"] >= 1 and not s["sl_hit"])
        losses = sum(1 for s in info_signals if s["sl_hit"] and s["max_tp_hit"] == 0)
        win_rate = wins / len(info_signals) * 100 if info_signals else 0
        print(f"\n  WIN RATE INFERIDO (señales con outcome claro):")
        print(f"    Wins (TP sin SL): {wins} | Losses (SL sin TP): {losses} | Total: {len(info_signals)}")
        print(f"    Win rate ≈ {win_rate:.0f}%")
        print(f"    NOTA: muchas señales no tienen mensaje explícito de TP/SL,")
        print(f"          pueden ganar/perder sin que el canal lo escriba.")


def profitability_estimate(signals: list[dict], channel: str, lot_size=0.01):
    """Estimación de rentabilidad ASUMIENDO que las señales se ejecutan literalmente
    sin DCA y sin gestión avanzada. Es un cálculo grueso, sirve para comparar canales."""
    section(f"{channel.upper()} — RENTABILIDAD ESTIMADA (literal, sin DCA)")

    # Para XAUUSD con lote 0.01, $1 de movimiento ≈ $1 de P/L
    # (aproximado: el contract size es 100 oz, 1 pip = 1$ con 0.01 lot)
    pnl_total = 0.0
    trades_evaluable = 0
    wins = 0
    losses = 0

    for s in signals:
        if not (s["tps_hit"] or s["sl_hit"]):
            continue
        if not s["sl_dist"] or not s["tp1_dist"]:
            continue
        trades_evaluable += 1

        # Si SL hit y no TP, asumir pérdida = sl_dist
        if s["sl_hit"] and s["max_tp_hit"] == 0:
            pnl_total -= s["sl_dist"]
            losses += 1
        # Si TP hit, asumir ganancia hasta max TP
        elif s["max_tp_hit"] >= 1 and not s["sl_hit"]:
            tp_idx = min(s["max_tp_hit"], len(s["tps"])) - 1
            entry = s["range_high"] if s["direction"] == "BUY" else s["range_low"]
            tp_price = s["tps"][tp_idx]
            pnl_total += abs(tp_price - entry)
            wins += 1
        # Si SL hit Y TP hit, parcial (asumimos partial close al TP1, resto a SL)
        elif s["sl_hit"] and s["max_tp_hit"] >= 1:
            entry = s["range_high"] if s["direction"] == "BUY" else s["range_low"]
            tp_price = s["tps"][min(s["max_tp_hit"], len(s["tps"]))-1]
            pnl_total += abs(tp_price - entry) * 0.5  # mitad ganada
            pnl_total -= s["sl_dist"] * 0.5            # mitad perdida
            wins += 0.5

    if trades_evaluable == 0:
        print("  (sin señales con outcome claro para evaluar)")
        return

    pnl_eur = pnl_total * lot_size * 100  # 0.01 lot × 100 oz × $/oz
    print(f"\n  Trades evaluables: {trades_evaluable}")
    print(f"  Wins: {wins} | Losses: {losses}")
    print(f"  P/L total estimado en $/oz: {pnl_total:+.0f}")
    print(f"  P/L total estimado con lot {lot_size}: ${pnl_eur:+.0f}")
    print(f"  P/L promedio por trade: ${pnl_total/trades_evaluable*lot_size*100:+.2f}")
    print(f"  ⚠ Esto NO incluye spread, comisión, slippage. Es solo orientativo.")


def channel_comparison(c1: list[dict], c2: list[dict]):
    section("COMPARATIVA CANAL 1 vs CANAL 2")

    def metric(signals, fn, filter_fn=None):
        vals = [fn(s) for s in signals if (filter_fn(s) if filter_fn else fn(s) is not None)]
        return statistics.median(vals) if vals else None

    m_table = [
        ("Señales totales",         len(c1),                                   len(c2)),
        ("BUY %",                   f"{sum(1 for s in c1 if s['direction']=='BUY')/len(c1)*100:.0f}%" if c1 else "-",
                                    f"{sum(1 for s in c2 if s['direction']=='BUY')/len(c2)*100:.0f}%" if c2 else "-"),
        ("Mediana num TPs",         metric(c1, lambda s: s["num_tps"]),        metric(c2, lambda s: s["num_tps"])),
        ("Mediana tamaño rango",    metric(c1, lambda s: s["range_size"]),     metric(c2, lambda s: s["range_size"])),
        ("Mediana TP1 dist",        metric(c1, lambda s: s["tp1_dist"]),       metric(c2, lambda s: s["tp1_dist"])),
        ("Mediana TP último dist",  metric(c1, lambda s: s["tp_last_dist"]),   metric(c2, lambda s: s["tp_last_dist"])),
        ("Mediana SL dist",         metric(c1, lambda s: s["sl_dist"]),        metric(c2, lambda s: s["sl_dist"])),
        ("Mediana R:R hasta TP1",   metric(c1, lambda s: s["rr_to_tp1"]),      metric(c2, lambda s: s["rr_to_tp1"])),
        ("Mediana duración (min)",  metric(c1, lambda s: s["duration_min"]),   metric(c2, lambda s: s["duration_min"])),
        ("% con replies gestión",   f"{sum(1 for s in c1 if s['num_replies']>0)/len(c1)*100:.0f}%" if c1 else "-",
                                    f"{sum(1 for s in c2 if s['num_replies']>0)/len(c2)*100:.0f}%" if c2 else "-"),
    ]

    print(f"\n  {'Métrica':<30} {'Canal 1':>15} {'Canal 2':>15}")
    print(f"  {'-'*30} {'-'*15} {'-'*15}")
    for label, v1, v2 in m_table:
        s1 = f"{v1:.2f}" if isinstance(v1, float) else str(v1)
        s2 = f"{v2:.2f}" if isinstance(v2, float) else str(v2)
        print(f"  {label:<30} {s1:>15} {s2:>15}")


def save_csv_detail(signals: list[dict], path: str):
    fieldnames = [
        "channel", "message_id", "sticker_id", "date", "hour_utc", "weekday",
        "direction", "range_low", "range_high", "range_size",
        "num_tps", "tp1", "tp_last", "sl",
        "tp1_dist", "tp_last_dist", "sl_dist", "rr_to_tp1", "rr_to_tp_last",
        "num_replies", "first_reply_min", "duration_min",
        "max_tp_hit", "sl_hit", "be_set", "closed_manual", "max_pips_mentioned",
        "tps", "raw_text",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for s in signals:
            row = dict(s)
            row["tps"] = "|".join(str(t) for t in s.get("tps", []))
            w.writerow(row)
    print(f"\n  CSV detalle guardado: {Path(path).resolve()}")


# ═════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True)
    ap.add_argument("--csv-detail", default="signals_detail.csv")
    args = ap.parse_args()

    print(f"\nCargando {args.json} ...")
    with open(args.json, encoding="utf-8") as f:
        data = json.load(f)

    c1_msgs = load_channel_msgs(data, CANAL_1_ID)
    c2_msgs = load_channel_msgs(data, CANAL_2_ID)
    print(f"  Canal 1: {len(c1_msgs)} mensajes brutos")
    print(f"  Canal 2: {len(c2_msgs)} mensajes brutos")

    print("\nExtrayendo señales con todos sus replies...")
    c1 = extract_canal1(c1_msgs)
    c2 = extract_canal2(c2_msgs)
    print(f"  Canal 1: {len(c1)} señales")
    print(f"  Canal 2: {len(c2)} señales")

    # Análisis por canal
    for sigs, name in [(c1, "canal1"), (c2, "canal2")]:
        if not sigs:
            continue
        basic_stats(sigs, name)
        time_stats(sigs, name)
        range_and_levels_stats(sigs, name)
        rr_stats(sigs, name)
        duration_stats(sigs, name)
        outcome_stats(sigs, name)
        profitability_estimate(sigs, name)

    # Comparativa final
    channel_comparison(c1, c2)

    # CSV
    all_sigs = c1 + c2
    if all_sigs:
        save_csv_detail(all_sigs, args.csv_detail)


if __name__ == "__main__":
    main()
