"""
analyze_optimal_2026.py — Estrategias CREATIVAS sobre datos 2026.

Filosofia: el canal nos da senales (BUY/SELL + rango + 5 TPs + SL), pero
NOSOTROS decidimos como operarlas. Probamos:

  A) Salidas tempranas       — todo en TP1, TP2, TP3 (no esperar TP5)
  B) Cierre parcial          — 50% en TP1, resto trail
  C) Trailing stop           — sube SL conforme avanza
  D) SL fijo propio          — ignorar SL canal, usar -3/-5/-7$
  E) DCA agresivo selectivo  — lot doble en post-SL momentum
  F) Salida por tiempo       — cerrar a 30/45 min con lo que haya
  G) Pyramid lot             — mas lotaje en contextos favorables
  H) Capital sweep           — Monte Carlo con €500/€1000/€1500/€2000

Output: tabla comparativa con EV total, avg, WR, MaxDD, Sharpe y P(ruina).
"""

import json
import re
import sys
import io
import random
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

CANAL_2_ID = 2614601304
JSON_PATH = r"C:\Users\josea\Downloads\Telegram Desktop\DataExport_2026-04-22\result.json"
CUTOFF_DATE = datetime(2026, 1, 1)   # SOLO 2026

COST_PER_TRADE = 0.80                # spread + slippage por orden (lot 0.01)
LOT_BASE = 0.01                      # 1 oz


# ─── Helpers ──────────────────────────────────────────────────────────────────

def extract_text(field):
    if isinstance(field, str):
        return field
    if isinstance(field, list):
        return "".join(p if isinstance(p, str) else p.get("text", "") for p in field)
    return ""


def parse_dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def section(title):
    print(f"\n{'='*82}")
    print(f"  {title}")
    print(f"{'='*82}\n")


# ─── Carga y parseo ───────────────────────────────────────────────────────────

def load_signals():
    with open(JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)
    chats = data.get("chats", {}).get("list", [])
    c2 = next((c for c in chats if c.get("id") == CANAL_2_ID), None)
    msgs = c2.get("messages", [])

    replies_to = defaultdict(list)
    for m in msgs:
        if "reply_to_message_id" in m:
            replies_to[m["reply_to_message_id"]].append(m)

    sigs = []
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
        rng = None
        m_rng = re.search(r"(\d{4}(?:\.\d+)?)\s*[-\u2013]\s*(\d{4}(?:\.\d+)?)", txt)
        if m_rng:
            try:
                lo, hi = float(m_rng.group(1)), float(m_rng.group(2))
                if lo > hi:
                    lo, hi = hi, lo
                if 1500 <= lo <= 5000 and 1500 <= hi <= 5000:
                    rng = (lo, hi)
            except ValueError:
                pass

        tps = []
        for m_tp in re.finditer(r"TP\d+\s*[:\s]\s*(\d{3,5}(?:\.\d{1,3})?)\b",
                                 txt, re.IGNORECASE):
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

        # Replies → outcome
        rs = []
        for r in replies_to.get(m["id"], []):
            r_txt = extract_text(r.get("text", ""))
            try:
                r_dt = parse_dt(r["date"])
            except Exception:
                continue
            rs.append({"text": r_txt, "dt": r_dt, "txt_lower": r_txt.lower()})
        rs.sort(key=lambda x: x["dt"])

        max_tp_hit = 0
        sl_hit = False
        time_to_sl = None
        time_to_first_tp = None
        time_to_last_tp = None
        re_enter_mentioned = False
        tp_times = {}  # {tp_n: minutes}

        for r in rs:
            t = r["txt_lower"]
            tp_match = re.search(r"tp[\s_]*(\d)", t)
            if tp_match and ("hit" in t or "tap" in t or "reach" in t or
                           "smash" in t or "secur" in t or "\u2705" in r["text"] or
                           "done" in t or "closed" in t):
                tp_n = int(tp_match.group(1))
                if tp_n > max_tp_hit:
                    max_tp_hit = tp_n
                    delta = (r["dt"] - dt).total_seconds() / 60
                    if time_to_first_tp is None:
                        time_to_first_tp = delta
                    time_to_last_tp = delta
                if tp_n not in tp_times:
                    tp_times[tp_n] = (r["dt"] - dt).total_seconds() / 60

            if "sl" in t and ("hit" in t or "out" in t or "\u274c" in r["text"]):
                sl_hit = True
                if time_to_sl is None:
                    time_to_sl = (r["dt"] - dt).total_seconds() / 60

            if re.search(r"\bre[\s-]*enter|still\s+valid", t):
                re_enter_mentioned = True

        is_high_risk = bool(re.search(r"high\s*risk", txt, re.IGNORECASE))

        sigs.append({
            "id": m["id"],
            "dt": dt,
            "hour": dt.hour,
            "weekday": dt.weekday(),  # 0=Mon, 6=Sun
            "direction": direction,
            "range": rng,
            "tps": tps,
            "sl": sl,
            "max_tp_hit": max_tp_hit,
            "sl_hit": sl_hit,
            "time_to_sl": time_to_sl,
            "time_to_first_tp": time_to_first_tp,
            "time_to_last_tp": time_to_last_tp,
            "tp_times": tp_times,
            "is_high_risk": is_high_risk,
            "re_enter_mentioned": re_enter_mentioned,
        })

    sigs.sort(key=lambda s: s["dt"])
    return sigs


# ─── Modelo realista de DCA ───────────────────────────────────────────────────

def positions_opened(time_to_event_min, max_pos):
    """Modelo: 0.7$/min mov medio, DCA cada $1 → tiempo limita # posiciones."""
    if time_to_event_min is None:
        return max_pos
    n = 1 + min((time_to_event_min * 0.7) / 1.0, max_pos - 1)
    return min(round(n), max_pos)


# ─── Simulador flexible de estrategia ─────────────────────────────────────────

def simulate(sig, strategy):
    """
    strategy es un dict con cualquier combinacion de:

      n_max:           num maximo posiciones DCA (default 4)
      lot_pattern:     lista lotes relativos por posicion (default [1,1,1,1])
      lot_global_mult: multiplicador global (default 1.0)
      exit_tp:         "escalonado" | int 1..5 | "partial_tp1" | "trail"
                       - escalonado: pos i -> tps[i]
                       - int N:      todas cierran en tps[N-1]
                       - partial_tp1: 50% cierra TP1, 50% queda trail con SL=BE
                       - trail:      mover SL conforme avanza (simulado)
      sl_mode:         "canal" | float (distancia $ propia)
      time_stop_min:   cerrar todo a los X min en BE (None = sin)
      time_close_min:  cerrar todo a los X min con lo que haya (None = sin)
      hr_mult:         multiplicador especifico para HIGH RISK
      skip_reenter:    bool
      skip_14h_sell:   bool
      post_sl_mult:    multiplicador si la senal anterior tuvo SL <10min
      _is_post_sl:     interno (set por evaluate)
    """
    if not sig["range"] or not sig["tps"] or sig["sl"] is None:
        return None

    rl, rh = sig["range"]
    direction = sig["direction"]
    entry = rh if direction == "BUY" else rl
    tps = sig["tps"]
    sl_canal = sig["sl"]

    n_max         = strategy.get("n_max", 4)
    lot_pattern   = strategy.get("lot_pattern", [1.0]*n_max)
    lot_global    = strategy.get("lot_global_mult", 1.0)
    exit_tp       = strategy.get("exit_tp", "escalonado")
    sl_mode       = strategy.get("sl_mode", "canal")
    time_stop     = strategy.get("time_stop_min")
    time_close    = strategy.get("time_close_min")
    hr_mult       = strategy.get("hr_mult", 1.0)
    post_sl_mult  = strategy.get("post_sl_mult", 1.0)

    # Modificadores por contexto
    if sig["is_high_risk"]:
        lot_global *= hr_mult
    if strategy.get("_is_post_sl"):
        lot_global *= post_sl_mult

    # SL efectivo
    if isinstance(sl_mode, (int, float)):
        sl = entry - sl_mode if direction == "BUY" else entry + sl_mode
    else:
        sl = sl_canal

    # Recompute distancia SL
    sl_dist = abs(entry - sl)

    # ── 1) TIME-STOP en BE: si no hay TP1 antes de X min, cerrar todo en BE ──
    if time_stop is not None:
        tp1_t = sig["tp_times"].get(1)
        if tp1_t is None or tp1_t > time_stop:
            n = positions_opened(time_stop, n_max)
            cost = sum(COST_PER_TRADE * lot_pattern[i] * lot_global
                       for i in range(int(n)))
            return -cost

    # ── 2) TIME-CLOSE: cerrar a los X min con lo que haya alcanzado ──
    if time_close is not None:
        # Que TP estaba alcanzado antes de time_close?
        max_tp_before = 0
        for tp_n, t in sig["tp_times"].items():
            if t <= time_close and tp_n > max_tp_before:
                max_tp_before = tp_n
        # SL antes?
        if sig["sl_hit"] and sig["time_to_sl"] is not None and sig["time_to_sl"] <= time_close:
            return _compute_sl_loss(sig, n_max, lot_pattern, lot_global, entry, sl, direction)
        # No SL pero ningun TP → BE costes
        if max_tp_before == 0:
            n = positions_opened(time_close, n_max)
            return -sum(COST_PER_TRADE * lot_pattern[i] * lot_global
                        for i in range(int(n)))
        # Tiene TP alcanzado parcial → cerramos en TP[max_tp_before-1]
        target_idx = min(max_tp_before - 1, len(tps) - 1)
        return _compute_tp_pl(sig, n_max, lot_pattern, lot_global,
                              entry, tps[target_idx], direction)

    # ── 3) SL hit ──
    if sig["sl_hit"]:
        return _compute_sl_loss(sig, n_max, lot_pattern, lot_global, entry, sl, direction)

    # ── 4) Sin outcome (BE costes) ──
    if sig["max_tp_hit"] == 0:
        return -sum(COST_PER_TRADE * lot_pattern[i] * lot_global
                    for i in range(n_max))

    # ── 5) TP hit con estrategia de salida ──
    return _compute_exit(sig, strategy, entry, tps, direction, n_max,
                          lot_pattern, lot_global)


def _compute_sl_loss(sig, n_max, lot_pattern, lot_global, entry, sl, direction):
    n_pos = positions_opened(sig["time_to_sl"], n_max)
    loss = 0
    for i in range(int(n_pos)):
        offset = i * 1.0
        pos_entry = entry - offset if direction == "BUY" else entry + offset
        pos_loss = (pos_entry - sl) if direction == "BUY" else (sl - pos_entry)
        lot_i = lot_pattern[i] if i < len(lot_pattern) else lot_pattern[-1]
        loss += (pos_loss + COST_PER_TRADE) * lot_i * lot_global
    return -loss


def _compute_tp_pl(sig, n_max, lot_pattern, lot_global, entry, target, direction):
    """Todas las posiciones cierran en target."""
    pl = 0
    for i in range(n_max):
        offset = i * 1.0
        pos_entry = entry - offset if direction == "BUY" else entry + offset
        profit = (target - pos_entry) if direction == "BUY" else (pos_entry - target)
        lot_i = lot_pattern[i] if i < len(lot_pattern) else lot_pattern[-1]
        if profit > 0:
            pl += (profit - COST_PER_TRADE) * lot_i * lot_global
        else:
            pl += -COST_PER_TRADE * lot_i * lot_global
    return pl


def _compute_exit(sig, strategy, entry, tps, direction, n_max, lot_pattern, lot_global):
    exit_tp = strategy.get("exit_tp", "escalonado")
    max_tp = sig["max_tp_hit"]

    # ── escalonado ──
    if exit_tp == "escalonado":
        pl = 0
        for i in range(n_max):
            tp_idx = min(i, len(tps) - 1)
            offset = i * 1.0
            pos_entry = entry - offset if direction == "BUY" else entry + offset
            lot_i = lot_pattern[i] if i < len(lot_pattern) else lot_pattern[-1]
            if tp_idx < max_tp:
                profit = (tps[tp_idx] - pos_entry) if direction == "BUY" else (pos_entry - tps[tp_idx])
                pl += (profit - COST_PER_TRADE) * lot_i * lot_global
            else:
                pl += -COST_PER_TRADE * lot_i * lot_global
        return pl

    # ── todas a TP_N ──
    if isinstance(exit_tp, int):
        target_idx = min(exit_tp - 1, len(tps) - 1)
        if max_tp >= exit_tp:
            return _compute_tp_pl(sig, n_max, lot_pattern, lot_global,
                                   entry, tps[target_idx], direction)
        # No alcanzo: cerramos en max_tp si hubo alguno, sino BE
        if max_tp > 0:
            tp_reached = tps[min(max_tp - 1, len(tps) - 1)]
            return _compute_tp_pl(sig, n_max, lot_pattern, lot_global,
                                   entry, tp_reached, direction)
        return -sum(COST_PER_TRADE * lot_pattern[i] * lot_global
                    for i in range(n_max))

    # ── partial: 50% en TP1 + 50% trail (asumimos se llega a TP3 si max_tp>=3) ──
    if exit_tp == "partial_tp1":
        if max_tp == 0:
            return -sum(COST_PER_TRADE * lot_pattern[i] * lot_global for i in range(n_max))
        pl_half_tp1 = 0
        pl_half_trail = 0
        # Mitad cierra en TP1
        for i in range(n_max):
            offset = i * 1.0
            pos_entry = entry - offset if direction == "BUY" else entry + offset
            lot_i = (lot_pattern[i] if i < len(lot_pattern) else lot_pattern[-1]) * 0.5
            if max_tp >= 1:
                profit = (tps[0] - pos_entry) if direction == "BUY" else (pos_entry - tps[0])
                pl_half_tp1 += (profit - COST_PER_TRADE) * lot_i * lot_global
        # La otra mitad: si llego a TP3+, cierra ahi; si no, BE
        target_trail_idx = min(2, len(tps) - 1)  # TP3
        for i in range(n_max):
            offset = i * 1.0
            pos_entry = entry - offset if direction == "BUY" else entry + offset
            lot_i = (lot_pattern[i] if i < len(lot_pattern) else lot_pattern[-1]) * 0.5
            if max_tp >= 3:
                profit = (tps[target_trail_idx] - pos_entry) if direction == "BUY" else (pos_entry - tps[target_trail_idx])
                pl_half_trail += (profit - COST_PER_TRADE) * lot_i * lot_global
            elif max_tp >= 1:
                # cerro en BE despues de TP1 con SL movido
                pl_half_trail += -COST_PER_TRADE * lot_i * lot_global
        return pl_half_tp1 + pl_half_trail

    # ── trail: mover SL al ultimo TP alcanzado, P/L = TP_alcanzado mas alto ──
    if exit_tp == "trail":
        if max_tp == 0:
            return -sum(COST_PER_TRADE * lot_pattern[i] * lot_global for i in range(n_max))
        # Asume que cerramos al ultimo TP alcanzado (trail conservador)
        tp_idx = min(max_tp - 1, len(tps) - 1)
        return _compute_tp_pl(sig, n_max, lot_pattern, lot_global,
                               entry, tps[tp_idx], direction)

    return None


# ─── Evaluador ────────────────────────────────────────────────────────────────

def evaluate(sigs, strategy):
    pls = []
    last_sl_dt = None
    last_sl_dur = None
    for s in sigs:
        # Filtros opcionales
        if strategy.get("skip_reenter") and s["re_enter_mentioned"]:
            continue
        if strategy.get("skip_14h_sell") and s["hour"] == 14 and s["direction"] == "SELL":
            continue
        if strategy.get("skip_high_risk") and s["is_high_risk"]:
            continue

        # Marcar post-SL si la previa hizo SL en <10min en los ultimos 30min
        is_post_sl = False
        if (last_sl_dt is not None and last_sl_dur is not None and
                last_sl_dur <= 10 and
                (s["dt"] - last_sl_dt).total_seconds() / 60 <= 30):
            is_post_sl = True

        cfg = dict(strategy)
        cfg["_is_post_sl"] = is_post_sl

        pl = simulate(s, cfg)
        if pl is not None:
            pls.append(pl)

        # Actualiza last SL
        if s["sl_hit"]:
            last_sl_dt = s["dt"]
            last_sl_dur = s["time_to_sl"]
        else:
            last_sl_dt = None
            last_sl_dur = None

    return _metrics(pls)


def _metrics(pls):
    if not pls:
        return {"total": 0, "avg": 0, "wr": 0, "max_dd": 0, "sharpe": 0, "n": 0, "pls": []}
    eq = 0
    peak = 0
    max_dd = 0
    for p in pls:
        eq += p
        if eq > peak: peak = eq
        if peak - eq > max_dd: max_dd = peak - eq
    avg = sum(pls) / len(pls)
    sd = statistics.stdev(pls) if len(pls) > 1 else 0
    sharpe = avg / sd if sd > 0 else 0
    wins = sum(1 for p in pls if p > 0)
    return {
        "total": sum(pls), "avg": avg, "wr": wins/len(pls)*100,
        "max_dd": max_dd, "sharpe": sharpe, "n": len(pls), "pls": pls
    }


def monte_carlo(pls, capital=500, months=3, n_paths=2000, signals_per_month=140):
    if not pls:
        return None
    n_per_path = signals_per_month * months
    ruined = 0
    finals = []
    for _ in range(n_paths):
        eq = capital
        for _ in range(n_per_path):
            eq += random.choice(pls)
            if eq <= 0:
                ruined += 1
                eq = 0
                break
        finals.append(eq)
    return {
        "p_ruin": ruined / n_paths * 100,
        "median": statistics.median(finals),
        "p25": sorted(finals)[int(n_paths * 0.25)],
        "p75": sorted(finals)[int(n_paths * 0.75)],
    }


# ─── Tests ────────────────────────────────────────────────────────────────────

def test_exit_strategies(sigs):
    section("A) SALIDAS TEMPRANAS — todas las posiciones a un mismo TP")

    base_lot = [1.0, 1.0, 1.0, 1.0]
    configs = [
        ("Escalonado (statu quo)",        {"n_max":4, "lot_pattern":base_lot, "exit_tp":"escalonado"}),
        ("Todas a TP1",                    {"n_max":4, "lot_pattern":base_lot, "exit_tp":1}),
        ("Todas a TP2",                    {"n_max":4, "lot_pattern":base_lot, "exit_tp":2}),
        ("Todas a TP3",                    {"n_max":4, "lot_pattern":base_lot, "exit_tp":3}),
        ("Todas a TP4",                    {"n_max":4, "lot_pattern":base_lot, "exit_tp":4}),
        ("Todas a TP5",                    {"n_max":4, "lot_pattern":base_lot, "exit_tp":5}),
        ("1 sola pos, todo a TP1",         {"n_max":1, "lot_pattern":[1.0],    "exit_tp":1}),
        ("1 sola pos, todo a TP3",         {"n_max":1, "lot_pattern":[1.0],    "exit_tp":3}),
        ("1 sola pos, todo a TP5",         {"n_max":1, "lot_pattern":[1.0],    "exit_tp":5}),
        ("Trail (cierra al ultimo TP)",    {"n_max":4, "lot_pattern":base_lot, "exit_tp":"trail"}),
        ("Partial: 50% TP1 + 50% TP3",     {"n_max":4, "lot_pattern":base_lot, "exit_tp":"partial_tp1"}),
    ]

    print(f"  {'Estrategia':<40} {'Total':>9} {'Avg':>7} {'WR%':>5} {'MaxDD':>8} {'Sharpe':>7}")
    print(f"  {'-'*82}")
    for name, cfg in configs:
        m = evaluate(sigs, cfg)
        print(f"  {name:<40} ${m['total']:+8.0f} ${m['avg']:+6.2f} "
              f"{m['wr']:4.0f}% ${m['max_dd']:7.0f} {m['sharpe']:6.3f}")


def test_sl_modes(sigs):
    section("D) SL FIJO PROPIO vs SL DEL CANAL")

    base = {"n_max":4, "lot_pattern":[1.0]*4, "exit_tp":"escalonado"}
    configs = [
        ("SL del canal (statu quo)",       dict(base, sl_mode="canal")),
        ("SL fijo -3$ propio",              dict(base, sl_mode=3.0)),
        ("SL fijo -5$ propio",              dict(base, sl_mode=5.0)),
        ("SL fijo -7$ propio",              dict(base, sl_mode=7.0)),
        ("SL fijo -10$ propio",             dict(base, sl_mode=10.0)),
    ]

    print(f"  {'Estrategia':<35} {'Total':>9} {'Avg':>7} {'WR%':>5} {'MaxDD':>8} {'Sharpe':>7}")
    print(f"  {'-'*80}")
    for name, cfg in configs:
        m = evaluate(sigs, cfg)
        print(f"  {name:<35} ${m['total']:+8.0f} ${m['avg']:+6.2f} "
              f"{m['wr']:4.0f}% ${m['max_dd']:7.0f} {m['sharpe']:6.3f}")


def test_time_close(sigs):
    section("F) SALIDA POR TIEMPO — cerrar a los X min con lo que haya")

    base = {"n_max":4, "lot_pattern":[1.0]*4, "exit_tp":"escalonado"}
    configs = [
        ("Sin time-close (statu quo)",      dict(base)),
        ("Time-close 30min",                dict(base, time_close_min=30)),
        ("Time-close 45min",                dict(base, time_close_min=45)),
        ("Time-close 60min",                dict(base, time_close_min=60)),
        ("Time-close 90min",                dict(base, time_close_min=90)),
        ("Time-close 120min",               dict(base, time_close_min=120)),
        ("Time-stop BE 60min (statu quo prev)", dict(base, time_stop_min=60)),
    ]

    print(f"  {'Estrategia':<40} {'Total':>9} {'Avg':>7} {'WR%':>5} {'MaxDD':>8} {'Sharpe':>7}")
    print(f"  {'-'*82}")
    for name, cfg in configs:
        m = evaluate(sigs, cfg)
        print(f"  {name:<40} ${m['total']:+8.0f} ${m['avg']:+6.2f} "
              f"{m['wr']:4.0f}% ${m['max_dd']:7.0f} {m['sharpe']:6.3f}")


def test_aggressive_lot(sigs):
    section("E + G) DCA AGRESIVO + PYRAMID por contexto")

    base = {"n_max":4, "lot_pattern":[1.0]*4, "exit_tp":"escalonado"}
    configs = [
        ("Statu quo (lot igual)",                   dict(base)),
        ("HIGH RISK lot x2.0",                      dict(base, hr_mult=2.0)),
        ("HIGH RISK lot x1.5",                      dict(base, hr_mult=1.5)),
        ("HIGH RISK lot x0.5 (recomendacion prev)", dict(base, hr_mult=0.5)),
        ("Post-SL momentum lot x1.5",               dict(base, post_sl_mult=1.5)),
        ("Post-SL momentum lot x2.0",               dict(base, post_sl_mult=2.0)),
        ("HIGH RISK x2 + Post-SL x1.5",             dict(base, hr_mult=2.0, post_sl_mult=1.5)),
        ("Lot pattern PYRAMID [1,1.5,2,2.5]",       dict(base, lot_pattern=[1.0,1.5,2.0,2.5])),
        ("Lot pattern MARTINGALA [1,2,3,4]",        dict(base, lot_pattern=[1.0,2.0,3.0,4.0])),
        ("Lot pattern DEFENSIVO [1,0.75,0.5,0.25]", dict(base, lot_pattern=[1.0,0.75,0.5,0.25])),
    ]

    print(f"  {'Estrategia':<45} {'Total':>9} {'Avg':>7} {'WR%':>5} {'MaxDD':>8} {'Sharpe':>7}")
    print(f"  {'-'*87}")
    for name, cfg in configs:
        m = evaluate(sigs, cfg)
        print(f"  {name:<45} ${m['total']:+8.0f} ${m['avg']:+6.2f} "
              f"{m['wr']:4.0f}% ${m['max_dd']:7.0f} {m['sharpe']:6.3f}")


def test_combined_optimal(sigs):
    section("COMBINACIONES OPTIMAS — buscando el mejor mix")

    configs = [
        ("Statu quo escalonado 4-pos",
         {"n_max":4, "lot_pattern":[1.0]*4, "exit_tp":"escalonado"}),

        ("BEST PREV: 4-pos + TIME-STOP 60min",
         {"n_max":4, "lot_pattern":[1.0]*4, "exit_tp":"escalonado",
          "time_stop_min":60}),

        ("Todas TP3 + TIME-CLOSE 60min + skip RE + skip 14h",
         {"n_max":4, "lot_pattern":[1.0]*4, "exit_tp":3,
          "time_close_min":60, "skip_reenter":True, "skip_14h_sell":True}),

        ("Todas TP2 + TIME-CLOSE 45min + skip RE + skip 14h",
         {"n_max":4, "lot_pattern":[1.0]*4, "exit_tp":2,
          "time_close_min":45, "skip_reenter":True, "skip_14h_sell":True}),

        ("Trail + TIME-STOP 60min + skip RE + skip 14h",
         {"n_max":4, "lot_pattern":[1.0]*4, "exit_tp":"trail",
          "time_stop_min":60, "skip_reenter":True, "skip_14h_sell":True}),

        ("Escalonado + SL fijo -5 + TIME-STOP 60min",
         {"n_max":4, "lot_pattern":[1.0]*4, "exit_tp":"escalonado",
          "sl_mode":5.0, "time_stop_min":60}),

        ("Escalonado + post-SL x1.5 + skip RE + skip 14h",
         {"n_max":4, "lot_pattern":[1.0]*4, "exit_tp":"escalonado",
          "post_sl_mult":1.5, "skip_reenter":True, "skip_14h_sell":True}),

        ("FULL MIX: Escalonado + TIME-STOP 60 + post-SL x1.5 + HR x0.5 + skip RE + skip 14h",
         {"n_max":4, "lot_pattern":[1.0]*4, "exit_tp":"escalonado",
          "time_stop_min":60, "post_sl_mult":1.5, "hr_mult":0.5,
          "skip_reenter":True, "skip_14h_sell":True}),

        ("FULL MIX AGGRESSIVE: HR x2 + post-SL x2 + TIME-STOP 60 + skip RE",
         {"n_max":4, "lot_pattern":[1.0]*4, "exit_tp":"escalonado",
          "time_stop_min":60, "post_sl_mult":2.0, "hr_mult":2.0,
          "skip_reenter":True}),

        ("1-pos + todas TP2 + skip RE + skip 14h (minimalista)",
         {"n_max":1, "lot_pattern":[1.0], "exit_tp":2,
          "skip_reenter":True, "skip_14h_sell":True}),

        ("Partial 50% TP1 + 50% trail + TIME-STOP 60 + skip RE",
         {"n_max":4, "lot_pattern":[1.0]*4, "exit_tp":"partial_tp1",
          "time_stop_min":60, "skip_reenter":True, "skip_14h_sell":True}),
    ]

    print(f"  {'Estrategia':<70} {'Total':>9} {'Avg':>7} {'WR%':>5} {'MaxDD':>8} {'Sharpe':>7}")
    print(f"  {'-'*112}")
    for name, cfg in configs:
        m = evaluate(sigs, cfg)
        print(f"  {name:<70} ${m['total']:+8.0f} ${m['avg']:+6.2f} "
              f"{m['wr']:4.0f}% ${m['max_dd']:7.0f} {m['sharpe']:6.3f}")


def test_capital_sweep(sigs):
    section("H) MONTE CARLO con DIFERENTES CAPITALES y mejor estrategia")

    # Toma la mejor estrategia identificada (la calculamos antes y tomamos esta)
    best = {"n_max":4, "lot_pattern":[1.0]*4, "exit_tp":"escalonado",
            "time_stop_min":60, "post_sl_mult":1.5, "hr_mult":0.5,
            "skip_reenter":True, "skip_14h_sell":True}

    m = evaluate(sigs, best)
    pls = m["pls"]

    # 559 senales en 4 meses ≈ 140/mes
    sigs_per_month = len(sigs) // 4

    print(f"  Estrategia: FULL MIX (TIME-STOP 60 + post-SL x1.5 + HR x0.5 + skips)")
    print(f"  Senales 2026: {len(sigs)}  |  ~{sigs_per_month}/mes  |  EV ${m['avg']:+.2f}/sig")
    print()
    print(f"  {'Capital':<12} {'P(ruina) 3m':>12} {'P(ruina) 6m':>12} "
          f"{'Median 3m':>11} {'Median 6m':>11}")
    print(f"  {'-'*68}")

    for cap in [500, 750, 1000, 1500, 2000, 3000]:
        mc3 = monte_carlo(pls, capital=cap, months=3, signals_per_month=sigs_per_month)
        mc6 = monte_carlo(pls, capital=cap, months=6, signals_per_month=sigs_per_month)
        print(f"  €{cap:<10} {mc3['p_ruin']:11.1f}% {mc6['p_ruin']:11.1f}% "
              f"€{mc3['median']:9.0f} €{mc6['median']:9.0f}")


def test_tp_reach_distribution(sigs):
    section("DISTRIBUCION TP MAX ALCANZADO — entender el techo realista")

    counts = defaultdict(int)
    for s in sigs:
        if s["max_tp_hit"] > 0 or s["sl_hit"]:
            counts[s["max_tp_hit"] if not s["sl_hit"] else -1] += 1

    total = sum(counts.values())
    print(f"  Sobre {total} senales con outcome confirmado:\n")
    print(f"  {'Outcome':<15} {'N':>6} {'%':>7} {'% acumulado (TP>=N)':>22}")
    print(f"  {'-'*55}")

    # Calcula % acumulado (% de señales que llegaron al menos a TPN)
    cum = 0
    for tp_n in [-1, 0, 1, 2, 3, 4, 5]:
        n = counts.get(tp_n, 0)
        if tp_n == -1:
            label = "SL"
        elif tp_n == 0:
            label = "BE/timeout"
        else:
            label = f"max=TP{tp_n}"
        pct = n / total * 100
        # acumulado: senales con max_tp >= tp_n (solo para tp_n>0)
        if tp_n > 0:
            n_at_least = sum(counts.get(k, 0) for k in [1,2,3,4,5] if k >= tp_n)
            pct_cum = n_at_least / total * 100
            print(f"  {label:<15} {n:>6} {pct:>6.1f}% {f'>=TP{tp_n}: {pct_cum:.1f}%':>22}")
        else:
            print(f"  {label:<15} {n:>6} {pct:>6.1f}%")


def main():
    print(f"Filtrando: solo senales >= {CUTOFF_DATE.date()}")
    print(f"Modelo: lot {LOT_BASE} (1 oz)  |  Cost ${COST_PER_TRADE}/trade")
    sigs = load_signals()
    print(f"Senales 2026: {len(sigs)}")

    test_tp_reach_distribution(sigs)
    test_exit_strategies(sigs)
    test_sl_modes(sigs)
    test_time_close(sigs)
    test_aggressive_lot(sigs)
    test_combined_optimal(sigs)
    test_capital_sweep(sigs)


if __name__ == "__main__":
    main()
