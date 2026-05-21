"""
analyze_dca_deep.py — Análisis EXHAUSTIVO de DCA con modelo REALISTA.

Mejoras sobre el modelo anterior:

  1. MODELO REALISTA de posiciones abiertas:
     - Si SL en <2min → solo se abrió 1 posición (no había tiempo)
     - Si SL en 2-5min → 2 posiciones
     - Si SL en 5-15min → 3 posiciones
     - Si SL >15min → 4 posiciones (todas)

  2. Test de DCA LOT DECRECIENTE (anti-martingala):
     - Pos 1: lot completo
     - Pos 2: lot/2
     - Pos 3: lot/3
     - Pos 4: lot/4
     - Riesgo total ≈ 2.08 lots (vs 4 con DCA igual)

  3. Test de DCA con TIME-STOP DEFENSIVO:
     - Si tras X min sin moverse a favor → cerrar todo
     - X = 30, 60, 90 min

  4. Test de DCA LIMITADO con condición:
     - 4 pos solo si NO es high_risk / NO es re-enter / NO es 14h SELL
     - 2 pos en señales sospechosas
     - 1 pos en post-SL momentum (con TP5 directo)

  5. Cálculo de Max DD REALISTA + drawdown como % capital

  6. Probabilidad de RUINA — Monte Carlo simple barajando trades
"""

import json
import re
import sys
import io
import random
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
CAPITAL = 500  # EUR


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


def load_msgs():
    with open(JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)
    chats = data.get("chats", {}).get("list", [])
    c2 = next((c for c in chats if c.get("id") == CANAL_2_ID), None)
    return c2.get("messages", [])


def build_signals(msgs):
    """Igual que antes."""
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

            if re.search(r"\bre[\s-]*enter|still\s+valid", t):
                re_enter_mentioned = True

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
            "max_tp_hit": max_tp_hit,
            "sl_hit": sl_hit,
            "time_to_sl": time_to_sl,
            "time_to_first_tp": time_to_first_tp,
            "time_to_last_tp": time_to_last_tp,
            "is_high_risk": is_high_risk,
            "re_enter_mentioned": re_enter_mentioned,
        })

    sigs.sort(key=lambda s: s["dt"])
    return sigs


# ═════════════════════════════════════════════════════════════════════════════
#  MODELO REALISTA — posiciones abiertas según duración
# ═════════════════════════════════════════════════════════════════════════════

def positions_actually_opened(time_to_event_min, max_positions, dca_step=1.0):
    """
    Estima cuántas posiciones DCA se llegaron a abrir.
    Asume que el precio se mueve ~1$/minuto en momento de tendencia,
    y el DCA abre 1 posición por cada $dca_step de movimiento.
    """
    if time_to_event_min is None:
        return max_positions
    # Velocidad promedio de movimiento del oro: 0.5-1$/min en tendencia
    move_per_min = 0.7  # conservador
    expected_dollars_moved = time_to_event_min * move_per_min
    n_dca_extra = expected_dollars_moved / dca_step  # cuántas DCA después de la 1ª
    n = 1 + min(n_dca_extra, max_positions - 1)
    return min(round(n), max_positions)


def signal_pl_realistic(sig, dca_config):
    """
    Calcula P/L realista con configuración DCA flexible.

    dca_config:
      - n_max: número máximo de posiciones DCA
      - lot_pattern: lista de multiplicadores (ej: [1, 1, 1, 1] o [1, 0.5, 0.33, 0.25])
      - tp_strategy: "escalonado" | "all_to_tp5" | "all_to_tp4"
      - time_stop_min: cerrar todo si tras X min sin moverse a favor (None = sin time stop)
      - lot_global_mult: multiplicador global (ej. 0.5 para HIGH RISK)
    """
    if not sig["range"] or not sig["tps"] or sig["sl"] is None:
        return None

    rl, rh = sig["range"]
    direction = sig["direction"]
    entry = rh if direction == "BUY" else rl
    sl = sig["sl"]
    tps = sig["tps"]
    sl_dist = abs(entry - sl)

    n_max = dca_config["n_max"]
    lot_pattern = dca_config.get("lot_pattern", [1.0] * n_max)
    tp_strategy = dca_config.get("tp_strategy", "escalonado")
    time_stop = dca_config.get("time_stop_min", None)
    lot_global = dca_config.get("lot_global_mult", 1.0)

    # 1. ¿TIME STOP se activa?
    # Si la señal NO llega a primer TP en time_stop minutos, cerrar todo en BE.
    if time_stop is not None:
        if sig["time_to_first_tp"] is None or sig["time_to_first_tp"] > time_stop:
            # Cerrar todo en BE → pérdida = solo costes
            n_pos_realistic = positions_actually_opened(time_stop, n_max)
            cost = sum(COST_PER_TRADE * lot_pattern[i] * lot_global
                       for i in range(int(n_pos_realistic)))
            return -cost

    # 2. SL hit
    if sig["sl_hit"]:
        n_pos = positions_actually_opened(sig["time_to_sl"], n_max)
        # Cada posición pierde su distancia individual al SL
        loss = 0
        for i in range(int(n_pos)):
            # Posición i abrió a entry - i*dca_step (BUY) o entry + i*dca_step (SELL)
            # Pérdida = (entry - i*step) - sl  para BUY
            pos_entry_offset = i * 1.0  # dca_step = 1
            if direction == "BUY":
                pos_entry = entry - pos_entry_offset
                pos_loss = pos_entry - sl  # positivo = pérdida
            else:
                pos_entry = entry + pos_entry_offset
                pos_loss = sl - pos_entry
            lot_i = lot_pattern[i] if i < len(lot_pattern) else lot_pattern[-1]
            loss += (pos_loss + COST_PER_TRADE) * lot_i * lot_global
        return -loss

    # 3. Sin outcome confirmado
    if sig["max_tp_hit"] == 0:
        n_pos = n_max  # asumimos abrieron todas
        return -sum(COST_PER_TRADE * lot_pattern[i] * lot_global
                    for i in range(int(n_pos)))

    # 4. Algún TP hit
    pl = 0.0
    for i in range(int(n_max)):
        lot_i = lot_pattern[i] if i < len(lot_pattern) else lot_pattern[-1]
        cost_i = COST_PER_TRADE * lot_i * lot_global

        if tp_strategy == "escalonado":
            tp_idx = min(i, len(tps) - 1)
            if tp_idx < sig["max_tp_hit"]:
                # cerró en TP_i (con su precio individual de entrada)
                pos_entry_offset = i * 1.0
                if direction == "BUY":
                    pos_entry = entry - pos_entry_offset
                    profit = tps[tp_idx] - pos_entry
                else:
                    pos_entry = entry + pos_entry_offset
                    profit = pos_entry - tps[tp_idx]
                pl += (profit - COST_PER_TRADE) * lot_i * lot_global
            else:
                pl += -cost_i  # BE
        elif tp_strategy == "all_to_tp5":
            # Todas apuntan a TP5
            target = tps[-1]
            if sig["max_tp_hit"] >= len(tps):
                pos_entry_offset = i * 1.0
                if direction == "BUY":
                    pos_entry = entry - pos_entry_offset
                    profit = target - pos_entry
                else:
                    pos_entry = entry + pos_entry_offset
                    profit = pos_entry - target
                pl += (profit - COST_PER_TRADE) * lot_i * lot_global
            else:
                # No llegó al final → aproximamos: cerró en max_tp con su lot
                tp_reached = tps[min(sig["max_tp_hit"] - 1, len(tps) - 1)]
                pos_entry_offset = i * 1.0
                if direction == "BUY":
                    pos_entry = entry - pos_entry_offset
                    profit = tp_reached - pos_entry
                else:
                    pos_entry = entry + pos_entry_offset
                    profit = pos_entry - tp_reached
                if profit > 0:
                    pl += (profit - COST_PER_TRADE) * lot_i * lot_global
                else:
                    pl += -cost_i
        elif tp_strategy == "all_to_tp4":
            target = tps[min(3, len(tps) - 1)]  # TP4 o último
            if sig["max_tp_hit"] >= 4:
                pos_entry_offset = i * 1.0
                if direction == "BUY":
                    pos_entry = entry - pos_entry_offset
                    profit = target - pos_entry
                else:
                    pos_entry = entry + pos_entry_offset
                    profit = pos_entry - target
                pl += (profit - COST_PER_TRADE) * lot_i * lot_global
            else:
                tp_reached = tps[min(sig["max_tp_hit"] - 1, len(tps) - 1)]
                pos_entry_offset = i * 1.0
                if direction == "BUY":
                    pos_entry = entry - pos_entry_offset
                    profit = tp_reached - pos_entry
                else:
                    pos_entry = entry + pos_entry_offset
                    profit = pos_entry - tp_reached
                if profit > 0:
                    pl += (profit - COST_PER_TRADE) * lot_i * lot_global
                else:
                    pl += -cost_i
    return pl


def equity_metrics(pls):
    """Calcula equity curve, max DD y otros."""
    equity = 0
    peak = 0
    max_dd = 0
    equity_curve = []
    for p in pls:
        equity += p
        equity_curve.append(equity)
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    avg = sum(pls) / len(pls) if pls else 0
    sd = statistics.stdev(pls) if len(pls) > 1 else 0
    sharpe = avg / sd if sd > 0 else 0
    wins = sum(1 for p in pls if p > 0)
    return {
        "total": sum(pls),
        "avg": avg,
        "wr": wins / len(pls) * 100 if pls else 0,
        "max_dd": max_dd,
        "sharpe": sharpe,
        "n": len(pls),
        "equity_curve": equity_curve,
    }


def evaluate(sigs, dca_config, name=""):
    """Aplica config a todas las señales y devuelve métricas."""
    pls = []
    for s in sigs:
        # Configuración condicional
        cfg = dict(dca_config)
        if s["is_high_risk"]:
            cfg["lot_global_mult"] = cfg.get("lot_global_mult", 1.0) * 0.5
        # Re-enter: si la config tiene "skip_reenter" y es re-enter, skip
        if cfg.get("skip_reenter") and s["re_enter_mentioned"]:
            continue
        # 14h SELL: si config tiene "skip_14h_sell"
        if cfg.get("skip_14h_sell") and s["hour"] == 14 and s["direction"] == "SELL":
            continue

        pl = signal_pl_realistic(s, cfg)
        if pl is not None:
            pls.append(pl)
    m = equity_metrics(pls)
    m["name"] = name
    return m


# ═════════════════════════════════════════════════════════════════════════════
#  TESTS de configuraciones
# ═════════════════════════════════════════════════════════════════════════════

def test_dca_configurations(sigs):
    section("ANÁLISIS PROFUNDO — Configuraciones DCA con MODELO REALISTA")

    configs = [
        ("BASELINE 4-pos lot igual",
         {"n_max": 4, "lot_pattern": [1.0, 1.0, 1.0, 1.0], "tp_strategy": "escalonado"}),
        ("4-pos lot DECRECIENTE [1,0.75,0.5,0.25]",
         {"n_max": 4, "lot_pattern": [1.0, 0.75, 0.5, 0.25], "tp_strategy": "escalonado"}),
        ("4-pos lot DECRECIENTE [1,0.5,0.33,0.25]",
         {"n_max": 4, "lot_pattern": [1.0, 0.5, 0.33, 0.25], "tp_strategy": "escalonado"}),
        ("3-pos lot igual",
         {"n_max": 3, "lot_pattern": [1.0, 1.0, 1.0], "tp_strategy": "escalonado"}),
        ("2-pos lot igual",
         {"n_max": 2, "lot_pattern": [1.0, 1.0], "tp_strategy": "escalonado"}),
        ("1-pos sin DCA",
         {"n_max": 1, "lot_pattern": [1.0], "tp_strategy": "escalonado"}),
        ("4-pos + TIME-STOP 90min",
         {"n_max": 4, "lot_pattern": [1.0, 1.0, 1.0, 1.0], "tp_strategy": "escalonado",
          "time_stop_min": 90}),
        ("4-pos + TIME-STOP 60min",
         {"n_max": 4, "lot_pattern": [1.0, 1.0, 1.0, 1.0], "tp_strategy": "escalonado",
          "time_stop_min": 60}),
        ("4-pos + TIME-STOP 30min",
         {"n_max": 4, "lot_pattern": [1.0, 1.0, 1.0, 1.0], "tp_strategy": "escalonado",
          "time_stop_min": 30}),
        ("4-pos lot DEC + TIME-STOP 60min + skip re-enter",
         {"n_max": 4, "lot_pattern": [1.0, 0.5, 0.33, 0.25],
          "tp_strategy": "escalonado",
          "time_stop_min": 60, "skip_reenter": True}),
    ]

    print(f"  {'Config':<55} {'Total':>9} {'Medio':>8} {'WR%':>5} {'MaxDD':>8} {'Sharpe':>7}")
    print(f"  {'-'*98}")

    results = []
    for name, cfg in configs:
        m = evaluate(sigs, cfg, name)
        results.append(m)
        # Calcular riesgo: cuántas veces lot total
        risk_units = sum(cfg["lot_pattern"])
        print(f"  {name:<55} ${m['total']:+8.0f} ${m['avg']:+7.2f} "
              f"{m['wr']:4.0f}% ${m['max_dd']:7.0f} {m['sharpe']:6.3f}")

    return results


def test_high_risk_modes(sigs):
    section("HIGH RISK — Análisis EV con muestra completa")

    high_risk = [s for s in sigs if s["is_high_risk"]]
    no_risk = [s for s in sigs if not s["is_high_risk"]]
    print(f"  Total HIGH RISK: {len(high_risk)}  (vs {len(no_risk)} normales)")

    if not high_risk:
        return

    cfg_base = {"n_max": 4, "lot_pattern": [1.0, 1.0, 1.0, 1.0], "tp_strategy": "escalonado"}
    cfg_half = {"n_max": 4, "lot_pattern": [1.0, 1.0, 1.0, 1.0], "tp_strategy": "escalonado",
                "lot_global_mult": 0.5}
    cfg_quart = {"n_max": 4, "lot_pattern": [1.0, 1.0, 1.0, 1.0], "tp_strategy": "escalonado",
                 "lot_global_mult": 0.25}
    cfg_only_1 = {"n_max": 1, "lot_pattern": [1.0], "tp_strategy": "escalonado"}

    print(f"\n  Solo en señales HIGH RISK:")
    for name, cfg in [("Lot completo (4 pos)", cfg_base),
                       ("Lot/2 (4 pos)", cfg_half),
                       ("Lot/4 (4 pos)", cfg_quart),
                       ("1 sola pos (no DCA)", cfg_only_1)]:
        pls = []
        for s in high_risk:
            pl = signal_pl_realistic(s, cfg)
            if pl is not None:
                pls.append(pl)
        if not pls:
            continue
        m = equity_metrics(pls)
        print(f"    {name:<25} Total ${m['total']:+5.0f}  Avg ${m['avg']:+5.2f}  "
              f"WR {m['wr']:.0f}%  MaxDD ${m['max_dd']:.0f}")


def test_post_sl_modes(sigs):
    section("POST-SL — Configs específicas para señales tras SL rápido")

    post_sl = []
    for i in range(1, len(sigs)):
        prev = sigs[i-1]
        if prev["sl_hit"] and prev["time_to_sl"] is not None and prev["time_to_sl"] <= 10:
            gap = (sigs[i]["dt"] - prev["dt"]).total_seconds() / 60
            if gap < 120:
                post_sl.append(sigs[i])

    print(f"  Señales 'post-SL momentum' (n={len(post_sl)})")

    configs = [
        ("Escalonado normal 4-pos",
         {"n_max": 4, "lot_pattern": [1.0]*4, "tp_strategy": "escalonado"}),
        ("Escalonado 4-pos a TP4 (no TP5)",
         {"n_max": 4, "lot_pattern": [1.0]*4, "tp_strategy": "all_to_tp4"}),
        ("4-pos TODAS a TP5",
         {"n_max": 4, "lot_pattern": [1.0]*4, "tp_strategy": "all_to_tp5"}),
        ("2-pos TODAS a TP5",
         {"n_max": 2, "lot_pattern": [1.0]*2, "tp_strategy": "all_to_tp5"}),
        ("1-pos a TP5",
         {"n_max": 1, "lot_pattern": [1.0], "tp_strategy": "all_to_tp5"}),
        ("2-pos a TP5 con LOT DOBLE",
         {"n_max": 2, "lot_pattern": [2.0, 2.0], "tp_strategy": "all_to_tp5"}),
    ]

    print(f"\n  {'Estrategia':<45} {'Total':>9} {'Medio':>9} {'WR%':>5} {'MaxDD':>8}")
    for name, cfg in configs:
        pls = []
        for s in post_sl:
            pl = signal_pl_realistic(s, cfg)
            if pl is not None:
                pls.append(pl)
        if not pls:
            continue
        m = equity_metrics(pls)
        print(f"  {name:<45} ${m['total']:+8.0f} ${m['avg']:+8.2f} "
              f"{m['wr']:4.0f}% ${m['max_dd']:7.0f}")


def test_filtered_strategies(sigs):
    section("ESTRATEGIA COMBINADA — Mejor configuración encontrada")

    configs = [
        ("Statu quo: 4-pos sin filtros",
         {"n_max": 4, "lot_pattern": [1.0]*4, "tp_strategy": "escalonado"}),
        ("4-pos + skip RE-ENTER",
         {"n_max": 4, "lot_pattern": [1.0]*4, "tp_strategy": "escalonado",
          "skip_reenter": True}),
        ("4-pos + skip 14h SELL",
         {"n_max": 4, "lot_pattern": [1.0]*4, "tp_strategy": "escalonado",
          "skip_14h_sell": True}),
        ("4-pos + HIGH RISK lot/2 (auto)",
         {"n_max": 4, "lot_pattern": [1.0]*4, "tp_strategy": "escalonado"}),
        ("4-pos + skip RE-ENTER + skip 14h SELL + HIGH RISK lot/2",
         {"n_max": 4, "lot_pattern": [1.0]*4, "tp_strategy": "escalonado",
          "skip_reenter": True, "skip_14h_sell": True}),
        ("DEC [1,0.5,0.33,0.25] + skip RE + skip 14h SELL + HR lot/2",
         {"n_max": 4, "lot_pattern": [1.0, 0.5, 0.33, 0.25], "tp_strategy": "escalonado",
          "skip_reenter": True, "skip_14h_sell": True}),
        ("DEC + TIME-STOP 60min + skip RE + skip 14h SELL + HR lot/2",
         {"n_max": 4, "lot_pattern": [1.0, 0.5, 0.33, 0.25], "tp_strategy": "escalonado",
          "time_stop_min": 60, "skip_reenter": True, "skip_14h_sell": True}),
    ]

    print(f"  {'Estrategia':<60} {'Total':>9} {'Avg':>7} {'WR%':>5} {'MaxDD':>8} {'Sharpe':>7}")
    print(f"  {'-'*100}")
    for name, cfg in configs:
        m = evaluate(sigs, cfg, name)
        # Risk units
        risk_units = sum(cfg.get("lot_pattern", [1.0]))
        print(f"  {name:<60} ${m['total']:+8.0f} ${m['avg']:+6.2f} "
              f"{m['wr']:4.0f}% ${m['max_dd']:7.0f} {m['sharpe']:6.3f}")


def monte_carlo_ruin(sigs, dca_config, capital=500, n_paths=2000):
    """Probabilidad de ruina con bootstrap del histograma de P/L."""
    pls = []
    for s in sigs:
        cfg = dict(dca_config)
        if s["is_high_risk"]:
            cfg["lot_global_mult"] = cfg.get("lot_global_mult", 1.0) * 0.5
        if cfg.get("skip_reenter") and s["re_enter_mentioned"]:
            continue
        if cfg.get("skip_14h_sell") and s["hour"] == 14 and s["direction"] == "SELL":
            continue
        pl = signal_pl_realistic(s, cfg)
        if pl is not None:
            pls.append(pl)

    if not pls:
        return None, None

    n_signals_per_month = len(pls) / 16  # ~16 meses de datos
    n_per_path = int(n_signals_per_month * 3)  # 3 meses

    ruined = 0
    final_equities = []
    for _ in range(n_paths):
        eq = capital
        peak = capital
        max_dd = 0
        for _ in range(n_per_path):
            p = random.choice(pls)
            eq += p
            if eq <= 0:
                ruined += 1
                eq = 0
                break
            if eq > peak:
                peak = eq
            dd = peak - eq
            if dd > max_dd:
                max_dd = dd
        final_equities.append(eq)

    p_ruin = ruined / n_paths * 100
    median_final = statistics.median(final_equities)
    p25 = sorted(final_equities)[int(n_paths * 0.25)]
    p75 = sorted(final_equities)[int(n_paths * 0.75)]
    return p_ruin, {"median": median_final, "p25": p25, "p75": p75,
                    "n_signals_per_path": n_per_path}


def test_monte_carlo(sigs):
    section("MONTE CARLO — Probabilidad de ruina (3 meses, capital €500)")

    configs = [
        ("4-pos baseline",
         {"n_max": 4, "lot_pattern": [1.0]*4, "tp_strategy": "escalonado"}),
        ("4-pos lot DEC [1,0.5,0.33,0.25]",
         {"n_max": 4, "lot_pattern": [1.0, 0.5, 0.33, 0.25], "tp_strategy": "escalonado"}),
        ("MEJOR: DEC + filtros completos",
         {"n_max": 4, "lot_pattern": [1.0, 0.5, 0.33, 0.25], "tp_strategy": "escalonado",
          "skip_reenter": True, "skip_14h_sell": True}),
        ("Conservador 2-pos lot igual",
         {"n_max": 2, "lot_pattern": [1.0, 1.0], "tp_strategy": "escalonado"}),
        ("Mínimo: 1-pos sin DCA",
         {"n_max": 1, "lot_pattern": [1.0], "tp_strategy": "escalonado"}),
    ]

    print(f"  {'Estrategia':<50} {'P(ruina)':>10} {'Median final':>14} {'P25':>10} {'P75':>10}")
    print(f"  {'-'*98}")
    for name, cfg in configs:
        p_ruin, stats_ = monte_carlo_ruin(sigs, cfg)
        if p_ruin is None:
            continue
        print(f"  {name:<50} {p_ruin:9.1f}%   €{stats_['median']:11.0f}   "
              f"€{stats_['p25']:8.0f}   €{stats_['p75']:8.0f}")


def main():
    print(f"📅 Filtrando solo señales >= {CUTOFF_DATE.date()}")
    print(f"💰 Modelo: lot {LOT_BASE} (1 oz)  |  Capital €{CAPITAL}  |  Cost ${COST_PER_TRADE}/trade\n")

    msgs = load_msgs()
    sigs = build_signals(msgs)
    print(f"Señales 2025+: {len(sigs)}\n")

    test_dca_configurations(sigs)
    test_high_risk_modes(sigs)
    test_post_sl_modes(sigs)
    test_filtered_strategies(sigs)
    test_monte_carlo(sigs)


if __name__ == "__main__":
    main()
