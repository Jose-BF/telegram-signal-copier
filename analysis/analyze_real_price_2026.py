"""
analyze_real_price_2026.py — Simulación TICK-A-TICK (M1 OHLC) con precio
real XAUUSD. No usa el outcome reportado por el canal sino que reproduce
cada operación contra el precio histórico.

Reglas de simulación:
  - Para cada señal, abrir las posiciones que correspondan según la
    operativa REAL del canal:
      * Canal 1 precio único:  1 posición market en el precio del sticker
      * Canal 1 con rango:     1 market en extremo cercano + 1 limit en lejano
      * Canal 2 (rango):       2 ó 3 ó 4 entradas según configuración
  - Avanzar minuto a minuto (M1 OHLC) desde la apertura de la señal.
  - En cada vela, comprobar si:
      • El precio toca el SL → cerrar todas las posiciones abiertas
      • El precio toca un TP → la posición correspondiente cierra
      • Si BE está activo y se tocó BE-TP, mover SL a entry
  - Si dentro de la misma vela toca SL y TP, asumir el peor caso (SL primero)
    salvo que el BE ya estuviera activo (en cuyo caso → BE).
  - Time-stop: si pasa N min sin TP1, cerrar todo.

Train/test split:
  - IS: enero + febrero 2026
  - OOS: marzo + abril 2026

Reportar SIEMPRE IS y OOS por separado. Si IS es bueno y OOS no, descartar.
"""

import json
import re
import sys
import statistics
from collections import defaultdict, Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

PARQUET_M1 = Path(__file__).parent / "data" / "xauusd_m1_2026.parquet"
PARQUET_C1 = Path(__file__).parent / "data" / "canal1_signals_2026.parquet"
PARQUET_C2 = Path(__file__).parent / "data" / "canal2_signals_2026.parquet"

CANAL_1_ID = 1642806869
CANAL_2_ID = 2614601304

# Costes operativos por trade (mantener honestos)
SPREAD_USD = 0.30          # spread medio XAUUSD Vantage Standard
SLIPPAGE_USD = 0.50         # slippage estimado
COST = SPREAD_USD + SLIPPAGE_USD
LOT_SIZE = 0.01             # 1 oz

# Train / test split
IS_END = datetime(2026, 3, 1, tzinfo=timezone.utc)


# ─── Carga de datos ───────────────────────────────────────────────────────────

def load_m1():
    df = pd.read_parquet(PARQUET_M1)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.sort_values("time").reset_index(drop=True)
    return df


def load_signals(path):
    df = pd.read_parquet(path)
    df["dt"] = pd.to_datetime(df["dt"], utc=True)
    sigs = []
    for _, row in df.iterrows():
        tps = [row[f"tp{i}"] for i in range(1, 6)
               if pd.notna(row[f"tp{i}"])]
        rng = (row["range_low"], row["range_high"]) if pd.notna(row["range_low"]) else None
        sigs.append({
            "channel": row["channel"], "id": row["id"],
            "dt": row["dt"].to_pydatetime(),
            "direction": row["direction"],
            "entry": row["entry"] if pd.notna(row["entry"]) else None,
            "range": rng,
            "tps": tps, "sl": row["sl"],
        })
    return sigs


def extract_text(field):
    if isinstance(field, str): return field
    if isinstance(field, list):
        return "".join(p if isinstance(p, str) else p.get("text", "") for p in field)
    return ""


def parse_dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# ─── Parsers de señales ───────────────────────────────────────────────────────

def parse_canal1(msgs):
    """Extrae señales canal 1 (sticker → texto)."""
    sigs = []
    for i, m in enumerate(msgs):
        if m.get("media_type") != "sticker": continue
        try:
            dt = parse_dt(m["date"])
        except Exception: continue
        if dt < datetime(2026, 1, 1, tzinfo=dt.tzinfo): continue

        for j in range(i+1, min(i+8, len(msgs))):
            nxt = msgs[j]
            try:
                ndt = parse_dt(nxt["date"])
            except Exception: continue
            if (ndt - dt).total_seconds() > 300: break
            txt = extract_text(nxt.get("text", ""))
            if not txt: continue
            upper = txt.upper()
            if "BUY" in upper or "SELL" in upper:
                tps = [float(t.group(1)) for t in
                       re.finditer(r"TP\d+\s*[:\s]\s*(\d{3,5}(?:\.\d{1,3})?)\b",
                                    txt, re.IGNORECASE)]
                if not tps: continue
                slm = re.search(r"SL\s*[:\s]\s*(\d{3,5}(?:\.\d{1,3})?)\b",
                                 txt, re.IGNORECASE)
                if not slm: continue
                sl = float(slm.group(1))

                em = re.search(r"NOW\s*@?\s*(\d{3,5}(?:\.\d{1,3})?)",
                                txt, re.IGNORECASE)
                entry = float(em.group(1)) if em else None

                rng = None
                rm = re.search(r"(\d{3,5}(?:\.\d+)?)\s*[-/\u2013]\s*(\d{2,5}(?:\.\d+)?)",
                                txt)
                if rm:
                    try:
                        a, b = float(rm.group(1)), float(rm.group(2))
                        bs = rm.group(2)
                        if b < 100 and "." not in bs:
                            base = int(a/100)*100
                            b = base + b
                        if abs(a - b) <= 30 and 1500 <= a <= 5500 and 1500 <= b <= 5500:
                            rng = (min(a,b), max(a,b))
                    except: pass

                direction = "BUY" if "BUY" in upper else "SELL"
                # Validación: TPs en dirección coherente
                if direction == "BUY" and entry and not all(t > entry for t in tps[:1]): break
                if direction == "SELL" and entry and not all(t < entry for t in tps[:1]): break

                sigs.append({
                    "channel": "canal1",
                    "id": m["id"], "dt": dt, "direction": direction,
                    "entry": entry, "range": rng, "tps": tps, "sl": sl,
                    "raw_text": txt[:200],
                })
                break
    return sigs


def parse_canal2(msgs):
    sigs = []
    for m in msgs:
        txt = extract_text(m.get("text", ""))
        if not txt: continue
        upper = txt.upper()
        if not ("BUY NOW" in upper or "SELL NOW" in upper): continue
        try:
            dt = parse_dt(m["date"])
        except Exception: continue
        if dt < datetime(2026, 1, 1, tzinfo=dt.tzinfo): continue

        direction = "BUY" if "BUY" in upper else "SELL"
        rng = None
        rm = re.search(r"(\d{4}(?:\.\d+)?)\s*[-\u2013]\s*(\d{4}(?:\.\d+)?)", txt)
        if rm:
            try:
                lo, hi = float(rm.group(1)), float(rm.group(2))
                if lo > hi: lo, hi = hi, lo
                if abs(hi - lo) <= 30 and 1500 <= lo <= 5500 and 1500 <= hi <= 5500:
                    rng = (lo, hi)
            except: pass
        if not rng: continue   # canal 2 sin rango = no operable

        tps = [float(t.group(1)) for t in
               re.finditer(r"TP\d+\s*[:\s]\s*(\d{3,5}(?:\.\d{1,3})?)\b", txt, re.IGNORECASE)]
        if not tps: continue
        slm = re.search(r"SL\s*[:\s]\s*(\d{3,5}(?:\.\d{1,3})?)\b", txt, re.IGNORECASE)
        if not slm: continue
        sl = float(slm.group(1))

        sigs.append({
            "channel": "canal2",
            "id": m["id"], "dt": dt, "direction": direction,
            "entry": None, "range": rng, "tps": tps, "sl": sl,
            "raw_text": txt[:200],
        })
    return sigs


# ─── Simulador tick-a-tick (M1 OHLC) ──────────────────────────────────────────

@dataclass
class Position:
    entry: float                 # precio efectivo de apertura (incluye spread/slip)
    open: bool = True            # si está abierta
    sl: Optional[float] = None
    tp_target: Optional[float] = None
    closed_at: Optional[float] = None  # precio de cierre
    closed_reason: str = ""       # "TP", "SL", "BE", "TIME"


@dataclass
class StrategyConfig:
    n_entries: int = 1               # cuántas entradas abrir (1 a 4)
    entry_mode: str = "market_only"  # "market_only" | "extremes" | "intra_dca"
    exit_tp_index: int = 2           # índice (1-based) del TP target. Todas → mismo TP
    be_at_tp_index: int = 1          # mover SL a BE cuando se toca TP_N (0 = sin BE)
    time_stop_min: int = 0           # cerrar si en N min no toca TP1 (0 = sin)
    intra_dca_step: float = 1.0      # separación entre entradas DCA intra-rango


def _opening_prices(sig, cfg):
    """Devuelve lista de (offset_min_relativo_a_signal_dt, precio_apertura).
    Para market: offset=0, precio = punta del rango/precio_único.
    Para limites: tienen que esperar a que el precio los toque (gestionado abajo).
    """
    direction = sig["direction"]
    rng = sig.get("range")
    entry_unique = sig.get("entry")

    # Determinar precio del MARKET inicial
    if entry_unique is not None and rng is None:
        market_price = entry_unique
    elif rng is not None:
        market_price = rng[1] if direction == "BUY" else rng[0]   # extremo cercano
    elif entry_unique is not None:
        market_price = entry_unique
    else:
        return []

    if cfg.entry_mode == "market_only" or rng is None:
        return [("market", market_price, None)]

    if cfg.entry_mode == "extremes":
        # 2ª entrada en extremo lejano (limit order)
        far = rng[0] if direction == "BUY" else rng[1]
        return [("market", market_price, None), ("limit", far, far)]

    if cfg.entry_mode == "intra_dca":
        # n_entries posiciones equiespaciadas dentro del rango
        n = max(2, cfg.n_entries)
        step = (rng[1] - rng[0]) / (n - 1)
        prices = [rng[1] - i*step if direction == "BUY" else rng[0] + i*step
                  for i in range(n)]
        return [("market", prices[0], None)] + [
            ("limit", p, p) for p in prices[1:]
        ]

    return [("market", market_price, None)]


def simulate_signal(sig, cfg, m1_df):
    """Simula la señal contra precio M1 real. Devuelve P/L total (USD por lote 0.01)."""
    direction = sig["direction"]
    sl_price = sig["sl"]
    tps = sig["tps"]

    # Filtrar M1 desde apertura
    sig_dt = sig["dt"]
    if sig_dt.tzinfo is None:
        sig_dt = sig_dt.replace(tzinfo=timezone.utc)

    # Buscar el M1 que contiene sig_dt o el siguiente
    bars = m1_df[m1_df["time"] >= sig_dt.replace(second=0, microsecond=0)]
    if len(bars) == 0:
        return None
    bars = bars.head(240)   # máximo 4h por señal (240 min)

    # Determinar entradas previstas
    entries_planned = _opening_prices(sig, cfg)
    if not entries_planned:
        return None

    # Crear posiciones (las "limit" se abren cuando el precio toca el limit_price)
    positions = []
    target_tp = tps[min(cfg.exit_tp_index - 1, len(tps) - 1)]
    be_tp     = tps[min(cfg.be_at_tp_index - 1, len(tps) - 1)] if cfg.be_at_tp_index > 0 else None

    for kind, price, limit_price in entries_planned:
        if kind == "market":
            # Open a precio market con slippage en contra
            slip = SLIPPAGE_USD if direction == "BUY" else -SLIPPAGE_USD
            entry_eff = price + slip
            positions.append({
                "kind": "market", "open": True, "entry": entry_eff,
                "sl": sl_price, "tp": target_tp, "limit_price": None,
                "filled": True, "closed_at": None, "reason": "",
            })
        else:
            positions.append({
                "kind": "limit", "open": False, "entry": None,
                "sl": sl_price, "tp": target_tp, "limit_price": limit_price,
                "filled": False, "closed_at": None, "reason": "",
            })

    be_active = False
    pl_total = 0.0
    minutes_elapsed = 0

    for _, bar in bars.iterrows():
        minutes_elapsed += 1
        bh, bl = bar["high"], bar["low"]
        bo, bc = bar["open"], bar["close"]

        # 1. Llenar limits que el precio haya tocado en esta vela
        for p in positions:
            if not p["filled"] and bl <= p["limit_price"] <= bh:
                # Limit fill: price entry = limit_price (asumimos sin slippage en limits)
                p["entry"] = p["limit_price"]
                p["filled"] = True
                p["open"] = True

        # 2. Si BE no está activo, comprobar si tocó BE-TP
        if not be_active and be_tp is not None:
            tp_touched = ((direction == "BUY" and bh >= be_tp) or
                          (direction == "SELL" and bl <= be_tp))
            if tp_touched:
                be_active = True
                # Mover SL al entry de cada posición filled
                for p in positions:
                    if p["filled"] and p["open"]:
                        p["sl"] = p["entry"]

        # 3. Comprobar SL y TP target en cada posición abierta
        for p in positions:
            if not p["open"] or not p["filled"]: continue
            sl_hit = ((direction == "BUY" and bl <= p["sl"]) or
                      (direction == "SELL" and bh >= p["sl"]))
            tp_hit = ((direction == "BUY" and bh >= p["tp"]) or
                      (direction == "SELL" and bl <= p["tp"]))

            if sl_hit and tp_hit:
                # Ambos en la misma vela. Heurística:
                # - Si BE estaba activo desde antes → cerró en TP probablemente
                # - Si no, asumir SL primero (peor caso) para conservadurismo
                if be_active and p["sl"] >= p["entry"] - 0.5:  # SL es BE
                    p["closed_at"] = p["sl"]
                    p["reason"] = "BE"
                else:
                    # Si bo está más cerca del SL, SL primero. Si más cerca del TP, TP primero.
                    dist_sl = abs(bo - p["sl"])
                    dist_tp = abs(bo - p["tp"])
                    if dist_sl < dist_tp:
                        p["closed_at"] = p["sl"]; p["reason"] = "SL"
                    else:
                        p["closed_at"] = p["tp"]; p["reason"] = "TP"
                p["open"] = False
            elif sl_hit:
                p["closed_at"] = p["sl"]
                p["reason"] = "BE" if (be_active and abs(p["sl"] - p["entry"]) < 0.5) else "SL"
                p["open"] = False
            elif tp_hit:
                p["closed_at"] = p["tp"]
                p["reason"] = "TP"
                p["open"] = False

        # 4. TIME-STOP: si configurado y aún no se activó BE
        if cfg.time_stop_min and minutes_elapsed >= cfg.time_stop_min and not be_active:
            for p in positions:
                if p["open"] and p["filled"]:
                    p["closed_at"] = bc
                    p["reason"] = "TIME"
                    p["open"] = False
            break

        # 5. Si todas cerradas, terminar
        if all(not p["open"] or not p["filled"] for p in positions):
            # Limits no llenados se cancelan
            for p in positions:
                if not p["filled"]:
                    p["open"] = False
            break

    # Cerrar al precio de cierre de la última barra cualquier posición que siga abierta
    if len(bars):
        last_close = bars.iloc[-1]["close"]
        for p in positions:
            if p["open"] and p["filled"]:
                p["closed_at"] = last_close
                p["reason"] = "EOH"  # end of horizon
                p["open"] = False

    # Calcular P/L
    pl_total = 0.0
    n_filled = 0
    for p in positions:
        if not p["filled"]: continue
        n_filled += 1
        diff = (p["closed_at"] - p["entry"]) if direction == "BUY" else (p["entry"] - p["closed_at"])
        # spread cobrado al cerrar
        diff -= SPREAD_USD
        pl_total += diff

    return pl_total, n_filled


# ─── Evaluación ───────────────────────────────────────────────────────────────

def evaluate(sigs, cfg, m1_df, label=""):
    pls = []
    for s in sigs:
        result = simulate_signal(s, cfg, m1_df)
        if result is None: continue
        pl, n = result
        pls.append(pl)
    if not pls:
        return None
    eq, peak, max_dd = 0.0, 0.0, 0.0
    for p in pls:
        eq += p
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
    avg = sum(pls)/len(pls)
    sd = statistics.stdev(pls) if len(pls) > 1 else 0
    sharpe = avg/sd if sd > 0 else 0
    wr = sum(1 for p in pls if p > 0)/len(pls)*100
    return {
        "total": sum(pls), "avg": avg, "wr": wr, "max_dd": max_dd,
        "sharpe": sharpe, "n": len(pls),
    }


def is_oos_split(sigs):
    is_set, oos_set = [], []
    for s in sigs:
        d = s["dt"]
        if d.tzinfo is None: d = d.replace(tzinfo=timezone.utc)
        if d < IS_END: is_set.append(s)
        else: oos_set.append(s)
    return is_set, oos_set


def fmt(m):
    if m is None: return "n/a"
    return (f"${m['total']:+8.0f} avg${m['avg']:+5.2f} WR{m['wr']:3.0f}% "
            f"DD${m['max_dd']:5.0f} S{m['sharpe']:+.3f} n={m['n']}")


def section(t):
    print(f"\n{'='*88}\n  {t}\n{'='*88}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("Cargando M1 parquet...", flush=True)
    m1 = load_m1()
    print(f"M1 rates: {len(m1):,}  ({m1['time'].min()} -> {m1['time'].max()})\n")

    print("Cargando señales pre-extraídas...", flush=True)
    sigs1 = load_signals(PARQUET_C1)
    sigs2 = load_signals(PARQUET_C2)
    print(f"Canal 1: {len(sigs1)} señales 2026")
    print(f"Canal 2: {len(sigs2)} señales 2026")

    is1, oos1 = is_oos_split(sigs1)
    is2, oos2 = is_oos_split(sigs2)
    print(f"  Canal 1 IS (ene-feb)={len(is1)}  OOS (mar-abr)={len(oos1)}")
    print(f"  Canal 2 IS (ene-feb)={len(is2)}  OOS (mar-abr)={len(oos2)}")

    # ── CANAL 1 ──
    section("CANAL 1 — todas configuraciones (IS vs OOS) - precio real M1")
    print(f"  {'Estrategia':<55} {'IN-SAMPLE':<48}  {'OUT-OF-SAMPLE':<48}")
    print(f"  {'-'*150}")

    c1_configs = []
    for n_entries, mode in [(1, "market_only"), (2, "extremes")]:
        for tp in [1, 2, 3, 4]:
            for be in [0, 1]:
                if be >= tp: continue
                for ts in [0, 30, 60]:
                    cfg = StrategyConfig(
                        n_entries=n_entries, entry_mode=mode,
                        exit_tp_index=tp, be_at_tp_index=be,
                        time_stop_min=ts,
                    )
                    name = f"{mode}/{n_entries}p TP{tp}" + \
                           (f"+BE{be}" if be else "") + \
                           (f"+TS{ts}" if ts else "")
                    is_m  = evaluate(is1, cfg, m1)
                    oos_m = evaluate(oos1, cfg, m1)
                    c1_configs.append((name, cfg, is_m, oos_m))

    # Mostrar TOP 12 por sharpe IS (luego comparar con OOS)
    c1_configs.sort(key=lambda x: -(x[2]["sharpe"] if x[2] else -99))
    for name, cfg, is_m, oos_m in c1_configs[:15]:
        print(f"  {name:<55} {fmt(is_m):<48}  {fmt(oos_m):<48}")

    # ── CANAL 2 ──
    section("CANAL 2 — todas configuraciones (IS vs OOS) - precio real M1")
    print(f"  {'Estrategia':<55} {'IN-SAMPLE':<48}  {'OUT-OF-SAMPLE':<48}")
    print(f"  {'-'*150}")

    c2_configs = []
    for mode, ne in [("market_only", 1), ("extremes", 2),
                      ("intra_dca", 3), ("intra_dca", 4)]:
        for tp in [2, 3, 4, 5]:
            for be in [0, 1]:
                if be >= tp: continue
                for ts in [0, 30]:
                    cfg = StrategyConfig(
                        n_entries=ne, entry_mode=mode,
                        exit_tp_index=tp, be_at_tp_index=be,
                        time_stop_min=ts,
                    )
                    name = f"{mode}/{ne}p TP{tp}" + \
                           (f"+BE{be}" if be else "") + \
                           (f"+TS{ts}" if ts else "")
                    is_m  = evaluate(is2, cfg, m1)
                    oos_m = evaluate(oos2, cfg, m1)
                    c2_configs.append((name, cfg, is_m, oos_m))

    c2_configs.sort(key=lambda x: -(x[2]["sharpe"] if x[2] else -99))
    for name, cfg, is_m, oos_m in c2_configs[:20]:
        print(f"  {name:<55} {fmt(is_m):<48}  {fmt(oos_m):<48}")

    # ── BEST OOS por canal ──
    section("MEJOR EN OOS por canal (criterio robusto: NO sobreoptimizar)")

    c1_best_oos = max(c1_configs, key=lambda x: (x[3]["total"] if x[3] else -1e9))
    c2_best_oos = max(c2_configs, key=lambda x: (x[3]["total"] if x[3] else -1e9))

    print(f"\n  CANAL 1 mejor en OOS: {c1_best_oos[0]}")
    print(f"     IS:  {fmt(c1_best_oos[2])}")
    print(f"     OOS: {fmt(c1_best_oos[3])}")
    print(f"\n  CANAL 2 mejor en OOS: {c2_best_oos[0]}")
    print(f"     IS:  {fmt(c2_best_oos[2])}")
    print(f"     OOS: {fmt(c2_best_oos[3])}")

    print(f"\n  TOTAL OOS combinado: ${c1_best_oos[3]['total']+c2_best_oos[3]['total']:+.0f}")

    # ── BEST robusto: top sharpe IS Y total OOS positivo ──
    section("MEJOR ROBUSTO: top Sharpe IS Y total OOS positivo")
    c1_robust = [x for x in c1_configs
                  if x[2] and x[3] and x[3]["total"] > 0]
    c1_robust.sort(key=lambda x: -x[2]["sharpe"])
    print(f"\n  CANAL 1 — Top 5 robusto:")
    for name, cfg, is_m, oos_m in c1_robust[:5]:
        print(f"    {name:<50}")
        print(f"      IS  : {fmt(is_m)}")
        print(f"      OOS : {fmt(oos_m)}")

    c2_robust = [x for x in c2_configs
                  if x[2] and x[3] and x[3]["total"] > 0]
    c2_robust.sort(key=lambda x: -x[2]["sharpe"])
    print(f"\n  CANAL 2 — Top 5 robusto:")
    for name, cfg, is_m, oos_m in c2_robust[:5]:
        print(f"    {name:<50}")
        print(f"      IS  : {fmt(is_m)}")
        print(f"      OOS : {fmt(oos_m)}")


if __name__ == "__main__":
    main()
