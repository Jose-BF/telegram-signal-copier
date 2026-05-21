"""
compare_canal1_escalonado.py — Compara EXPLÍCITAMENTE en canal 1:

  A) TP único TPn (todas las pos cierran al mismo TP)
  B) Escalonado: pos 0 → TP_a, pos 1 → TP_b (cada una su propio TP)

Hipótesis: con WR alto (73-76%), escalonar puede ser bueno o malo:
  + escalonado: pos 0 cierra rápido en TP1, libera capital, pos 1 captura TP2/3
  - escalonado: cuando precio va fuerte, "cortas" la ganancia de pos 0 muy temprano
  - escalonado: en pull-back que toca limit y luego sigue, pos 0 ya cerró,
                pos 1 queda sin diluir y puede acabar en SL completo

Solo medir lo cierra el debate.
"""

import sys
import statistics
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass

import pandas as pd

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

PARQUET_M1 = Path(__file__).parent / "data" / "xauusd_m1_2026.parquet"
PARQUET_C1 = Path(__file__).parent / "data" / "canal1_signals_2026.parquet"

SPREAD_USD = 0.30
SLIPPAGE_USD = 0.50

IS_END = datetime(2026, 3, 1, tzinfo=timezone.utc)


def load_m1():
    df = pd.read_parquet(PARQUET_M1)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.sort_values("time").reset_index(drop=True)


def load_signals(path):
    df = pd.read_parquet(path)
    df["dt"] = pd.to_datetime(df["dt"], utc=True)
    sigs = []
    for _, row in df.iterrows():
        tps = [row[f"tp{i}"] for i in range(1, 6) if pd.notna(row[f"tp{i}"])]
        rng = (row["range_low"], row["range_high"]) if pd.notna(row["range_low"]) else None
        sigs.append({
            "id": row["id"], "dt": row["dt"].to_pydatetime(),
            "direction": row["direction"],
            "entry": row["entry"] if pd.notna(row["entry"]) else None,
            "range": rng, "tps": tps, "sl": row["sl"],
        })
    return sigs


@dataclass
class StrategyConfig:
    """Para canal 1: 1 o 2 entradas + TPs por posición.
    tps_per_pos = lista de índices de TP para cada posición.
       [2]      → 1 pos a TP3 (market_only single)
       [2, 2]   → 2 pos ambas a TP3 (mi recomendación actual)
       [0, 1]   → escalonado pos 0 → TP1, pos 1 → TP2
       [1, 2]   → escalonado pos 0 → TP2, pos 1 → TP3
    """
    name: str
    tps_per_pos: list[int]    # índice 0-based del TP por posición
    use_extremes: bool = True  # si False → solo market (incluso con rango)
    be_at_tp: int = -1         # índice TP que dispara BE (-1 = sin BE)
    time_stop_min: int = 60


def simulate(sig, cfg, m1):
    direction = sig["direction"]
    rng = sig.get("range")
    entry_unique = sig.get("entry")

    # Precio del market
    if entry_unique is not None and rng is None:
        market_price = entry_unique
    elif rng is not None:
        market_price = rng[1] if direction == "BUY" else rng[0]
    elif entry_unique is not None:
        market_price = entry_unique
    else:
        return None

    # Si solo 1 TP en config → solo 1 pos. Si 2 TPs → market + limit (si hay rango)
    n_pos = len(cfg.tps_per_pos)
    has_limit = (n_pos == 2 and cfg.use_extremes and rng is not None)

    sig_dt = sig["dt"]
    if sig_dt.tzinfo is None:
        sig_dt = sig_dt.replace(tzinfo=timezone.utc)

    bars = m1[m1["time"] >= sig_dt.replace(second=0, microsecond=0)]
    if len(bars) == 0:
        return None
    bars = bars.head(240)

    tps = sig["tps"]

    def tp_for(idx):
        return tps[min(cfg.tps_per_pos[idx], len(tps) - 1)]

    be_tp = tps[min(cfg.be_at_tp, len(tps) - 1)] if cfg.be_at_tp >= 0 else None

    # Crear posiciones
    slip = SLIPPAGE_USD if direction == "BUY" else -SLIPPAGE_USD
    positions = [{
        "kind": "market", "open": True, "filled": True,
        "entry": market_price + slip,
        "sl": sig["sl"], "tp": tp_for(0),
        "limit_price": None, "closed_at": None, "reason": "",
    }]
    if has_limit:
        far = rng[0] if direction == "BUY" else rng[1]
        positions.append({
            "kind": "limit", "open": False, "filled": False,
            "entry": None, "sl": sig["sl"], "tp": tp_for(1),
            "limit_price": far, "closed_at": None, "reason": "",
        })

    be_active = False
    minutes_elapsed = 0

    for _, bar in bars.iterrows():
        minutes_elapsed += 1
        bh, bl = bar["high"], bar["low"]
        bo, bc = bar["open"], bar["close"]

        # 1. Limit fills
        for p in positions:
            if not p["filled"] and bl <= p["limit_price"] <= bh:
                p["entry"] = p["limit_price"]
                p["filled"] = True
                p["open"] = True

        # 2. BE trigger
        if not be_active and be_tp is not None:
            tp_touched = ((direction == "BUY" and bh >= be_tp) or
                          (direction == "SELL" and bl <= be_tp))
            if tp_touched:
                be_active = True
                for p in positions:
                    if p["filled"] and p["open"]:
                        p["sl"] = p["entry"]

        # 3. SL/TP por posición
        for p in positions:
            if not p["open"] or not p["filled"]: continue
            sl_hit = ((direction == "BUY" and bl <= p["sl"]) or
                      (direction == "SELL" and bh >= p["sl"]))
            tp_hit = ((direction == "BUY" and bh >= p["tp"]) or
                      (direction == "SELL" and bl <= p["tp"]))

            if sl_hit and tp_hit:
                if be_active and abs(p["sl"] - p["entry"]) < 0.5:
                    p["closed_at"] = p["sl"]; p["reason"] = "BE"
                else:
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

        # 4. Time-stop
        if cfg.time_stop_min and minutes_elapsed >= cfg.time_stop_min and not be_active:
            for p in positions:
                if p["open"] and p["filled"]:
                    p["closed_at"] = bc
                    p["reason"] = "TIME"
                    p["open"] = False
            break

        if all(not p["open"] or not p["filled"] for p in positions):
            for p in positions:
                if not p["filled"]:
                    p["open"] = False
            break

    # Cierre al fin
    if len(bars):
        last_close = bars.iloc[-1]["close"]
        for p in positions:
            if p["open"] and p["filled"]:
                p["closed_at"] = last_close
                p["reason"] = "EOH"
                p["open"] = False

    # P/L
    pl = 0.0
    n_filled = 0
    for p in positions:
        if not p["filled"]: continue
        n_filled += 1
        d = (p["closed_at"] - p["entry"]) if direction == "BUY" else (p["entry"] - p["closed_at"])
        d -= SPREAD_USD
        pl += d
    return pl, n_filled


def evaluate(sigs, cfg, m1):
    pls = []
    for s in sigs:
        r = simulate(s, cfg, m1)
        if r is None: continue
        pls.append(r[0])
    if not pls: return None
    eq, peak, max_dd = 0, 0, 0
    for p in pls:
        eq += p
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
    avg = sum(pls) / len(pls)
    sd = statistics.stdev(pls) if len(pls) > 1 else 0
    sharpe = avg / sd if sd > 0 else 0
    wr = sum(1 for p in pls if p > 0) / len(pls) * 100
    return {"total": sum(pls), "avg": avg, "wr": wr, "max_dd": max_dd,
            "sharpe": sharpe, "n": len(pls)}


def fmt(m):
    if m is None: return "n/a"
    return (f"${m['total']:+8.0f} avg${m['avg']:+5.2f} WR{m['wr']:3.0f}% "
            f"DD${m['max_dd']:5.0f} S{m['sharpe']:+.3f} n={m['n']}")


def main():
    print("Cargando M1 + canal 1...")
    m1 = load_m1()
    sigs = load_signals(PARQUET_C1)
    is_set = [s for s in sigs if s["dt"] < IS_END]
    oos    = [s for s in sigs if s["dt"] >= IS_END]
    print(f"Canal 1: {len(sigs)} señales | IS={len(is_set)} | OOS={len(oos)}\n")

    # Cantidad de señales con rango vs sin rango
    with_range = sum(1 for s in sigs if s.get("range") is not None)
    print(f"  Con rango: {with_range} ({with_range/len(sigs)*100:.0f}%)")
    print(f"  Sin rango: {len(sigs)-with_range} ({(len(sigs)-with_range)/len(sigs)*100:.0f}%)")

    # ── Configuraciones ──
    configs = [
        # TP único — recomendación actual
        StrategyConfig("TP único TP1 (1pos)",       [0]),
        StrategyConfig("TP único TP2 (1pos)",       [1]),
        StrategyConfig("TP único TP3 (1pos)",       [2]),
        StrategyConfig("TP único TP4 (1pos)",       [3]),

        StrategyConfig("TP único TP2 (extremes 2p)",[1, 1]),
        StrategyConfig("TP único TP3 (extremes 2p)",[2, 2]),  # mi recomendación
        StrategyConfig("TP único TP4 (extremes 2p)",[3, 3]),

        # Escalonado — la pregunta del usuario
        StrategyConfig("Escal pos0→TP1, pos1→TP2",  [0, 1]),
        StrategyConfig("Escal pos0→TP1, pos1→TP3",  [0, 2]),
        StrategyConfig("Escal pos0→TP2, pos1→TP3",  [1, 2]),
        StrategyConfig("Escal pos0→TP2, pos1→TP4",  [1, 3]),
        StrategyConfig("Escal pos0→TP3, pos1→TP4",  [2, 3]),

        # Sin time-stop para comparar
        StrategyConfig("TP único TP3 (extremes, NO TS)", [2, 2], time_stop_min=0),
        StrategyConfig("Escal pos0→TP2, pos1→TP4 (NO TS)",[1, 3], time_stop_min=0),
    ]

    print(f"\n{'Estrategia':<42} {'IN-SAMPLE':<48}  {'OUT-OF-SAMPLE':<48}")
    print("-"*140)
    rows = []
    for cfg in configs:
        is_m  = evaluate(is_set, cfg, m1)
        oos_m = evaluate(oos, cfg, m1)
        print(f"  {cfg.name:<40} {fmt(is_m):<48}  {fmt(oos_m):<48}")
        rows.append((cfg.name, is_m, oos_m))

    # ── Resumen comparativo ──
    print("\n" + "="*88)
    print("  COMPARATIVA TP ÚNICO vs ESCALONADO (canal 1)")
    print("="*88)
    print("  Decisión: ¿qué configuración tiene MEJOR OOS y mantiene IS positivo?\n")

    rows_robust = [r for r in rows if r[1] and r[2] and r[2]["total"] > 0 and r[1]["total"] > 0]
    rows_robust.sort(key=lambda r: -r[2]["total"])

    print(f"  Top 5 por OOS total (con IS también positivo):")
    for name, is_m, oos_m in rows_robust[:5]:
        ratio = oos_m["total"] / is_m["total"] if is_m["total"] > 0 else 0
        print(f"    {name:<40}")
        print(f"       IS:  {fmt(is_m)}")
        print(f"       OOS: {fmt(oos_m)}  (OOS/IS ratio = {ratio:.2f})")


if __name__ == "__main__":
    main()
