"""
mt5_safety_modes_analysis.py — Comparativa de los 4 safety modes sobre la
mejor estrategia de cada canal (de la corrida anterior).

Estrategias base (las mejores OOS de la corrida anterior):
  Canal 1: extremes/2p TP3 (no TS)  — OOS +$414
  Canal 2: intra_dca/4p TP3+BE      — IS -$34, OOS -$1836 (la "menos mala")

Safety modes a comparar:
  - cancel              (actual del bot)
  - tolerance $1.00
  - tolerance $2.00
  - tolerance $5.00
  - limit_fallback      (cierra market, mantiene limits)
  - limit_first         (nunca abre market, solo limits)

Y también — métricas diagnósticas:
  - Histograma de distancias del would-be market entry al rango (para ver si
    las señales fallidas estaban "casi dentro" o "muy fuera").
"""

import sys, statistics
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pandas as pd

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from mt5_tick_simulator import (
    MT5Session, Strategy, simulate, SYMBOL, LOT, DATA_DIR
)
from mt5_tick_cache import TickCache
from mt5_tick_analysis import load_signals

PARQUET_C1 = DATA_DIR / "canal1_signals_2026.parquet"
PARQUET_C2 = DATA_DIR / "canal2_signals_2026.parquet"
IS_END = datetime(2026, 3, 1, tzinfo=timezone.utc)


def evaluate(sigs, strat, cache, contract_size) -> dict:
    pls = []
    distances = []  # del entry al rango (negativo = abajo, positivo = arriba)
    n_safety = 0
    n_no_ticks = 0
    n_no_fill = 0
    n_no_range = 0
    n_operated = 0  # señales con al menos 1 fill
    for s in sigs:
        t_from = s["dt"] - timedelta(seconds=30)
        t_to   = s["dt"] + timedelta(minutes=strat.horizon_min)
        ticks = cache.get_ticks(t_from, t_to)
        if len(ticks) == 0:
            n_no_ticks += 1
            continue
        result = simulate(s, strat, ticks, contract_size, verbose=False)
        st = result["status"]
        if st == "NO_FILL_TICK":
            n_no_fill += 1
            continue
        if st == "NO_RANGE_FOR_LIMITS":
            n_no_range += 1
            continue
        if st == "SAFETY_CANCEL":
            n_safety += 1
        if result.get("entry_distance") is not None:
            distances.append(result["entry_distance"])
        if result.get("n_filled", 0) > 0:
            n_operated += 1
        pls.append(result["pl"])
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
    operated_pls = [p for p in pls if p != 0]
    wr = ((sum(1 for p in operated_pls if p > 0) / len(operated_pls) * 100)
          if operated_pls else 0)
    # Histograma de distancias para señales que dispararon safety
    safety_d = [abs(d) for d in distances if abs(d) > 0.001]
    return {
        "total": sum(pls), "avg": avg, "wr": wr, "max_dd": max_dd,
        "sharpe": sharpe, "n": len(pls),
        "operated": n_operated, "safety": n_safety,
        "no_ticks": n_no_ticks, "no_fill": n_no_fill,
        "no_range": n_no_range,
        "outside_count": len(safety_d),
        "outside_med": statistics.median(safety_d) if safety_d else 0,
        "outside_p90": sorted(safety_d)[int(len(safety_d)*0.9)] if safety_d else 0,
        "outside_max": max(safety_d) if safety_d else 0,
    }


def fmt(m):
    if m is None: return "n/a"
    return (f"${m['total']:+8.0f} avg${m['avg']:+5.2f} WR{m['wr']:3.0f}% "
            f"DD${m['max_dd']:5.0f} S{m['sharpe']:+.3f} "
            f"op:{m['operated']:>3}/{m['n']:>3} safe:{m['safety']:>3}")


def main():
    print("="*100)
    print("  COMPARATIVA SAFETY MODES — 4 modos × 2 estrategias × IS/OOS")
    print("="*100)

    sigs1 = load_signals(PARQUET_C1)
    sigs2 = load_signals(PARQUET_C2)
    is1 = [s for s in sigs1 if s["dt"] < IS_END]
    oos1 = [s for s in sigs1 if s["dt"] >= IS_END]
    is2 = [s for s in sigs2 if s["dt"] < IS_END]
    oos2 = [s for s in sigs2 if s["dt"] >= IS_END]

    print(f"\n  Canal 1: {len(sigs1)} señales (IS={len(is1)} | OOS={len(oos1)})")
    print(f"  Canal 2: {len(sigs2)} señales (IS={len(is2)} | OOS={len(oos2)})")

    mt5s = MT5Session(SYMBOL)
    cache = TickCache(mt5s)
    try:
        # ── Estrategia base por canal ────────────────────────────────────────
        # Canal 1: la mejor OOS de la corrida anterior
        # Canal 2: la "menos mala" para tener baseline de comparación
        c1_base = ("extremes", 2, 2, -1, 0)   # entry_mode, n_entries, target_tp_idx, be, ts
        c2_base = ("intra_dca", 4, 2, 0, 0)   # 4 entradas, TP3, BE@TP1, no TS

        modes_c1 = [
            ("cancel",            dict(safety_mode="cancel")),
            ("tolerance $1",      dict(safety_mode="tolerance", safety_tolerance=1.0)),
            ("tolerance $2",      dict(safety_mode="tolerance", safety_tolerance=2.0)),
            ("tolerance $5",      dict(safety_mode="tolerance", safety_tolerance=5.0)),
            ("limit_fallback",    dict(safety_mode="limit_fallback")),
            ("limit_first",       dict(safety_mode="limit_first")),
        ]
        modes_c2 = modes_c1  # mismos modos

        def make_strat(base, mode_kwargs):
            em, ne, tp, be, ts = base
            return Strategy(em, ne, tp, be, ts, **mode_kwargs)

        # ── Canal 1 ──────────────────────────────────────────────────────────
        print(f"\n{'='*100}")
        print(f"  CANAL 1 — base: extremes/2p TP3 (no TS)")
        print(f"{'='*100}")
        print(f"\n  {'mode':<18} {'split':<4} {'P/L':>9} {'avg':>7} {'WR':>4} "
              f"{'DD':>6} {'sharpe':>7} {'op':>4}/{'n':<4} {'safe':>4} "
              f"| outside (med, p90, max)")
        print(f"  " + "-"*120)
        c1_results = {}
        for name, kw in modes_c1:
            strat = make_strat(c1_base, kw)
            is_m  = evaluate(is1,  strat, cache, mt5s.contract_size)
            oos_m = evaluate(oos1, strat, cache, mt5s.contract_size)
            c1_results[name] = (is_m, oos_m)
            for split, m in [("IS", is_m), ("OOS", oos_m)]:
                if m is None:
                    print(f"  {name:<18} {split:<4} n/a")
                else:
                    out = (f"({m['outside_med']:.2f}, {m['outside_p90']:.2f}, "
                           f"{m['outside_max']:.2f}) n={m['outside_count']}")
                    print(f"  {name:<18} {split:<4} ${m['total']:>+7.0f} "
                          f"${m['avg']:>+5.2f} {m['wr']:>3.0f}% "
                          f"${m['max_dd']:>5.0f} {m['sharpe']:>+.3f} "
                          f"{m['operated']:>3}/{m['n']:<3} {m['safety']:>4} "
                          f"| {out}")

        # ── Canal 2 ──────────────────────────────────────────────────────────
        print(f"\n{'='*100}")
        print(f"  CANAL 2 — base: intra_dca/4p TP3+BE@TP1")
        print(f"{'='*100}")
        print(f"\n  {'mode':<18} {'split':<4} {'P/L':>9} {'avg':>7} {'WR':>4} "
              f"{'DD':>6} {'sharpe':>7} {'op':>4}/{'n':<4} {'safe':>4} "
              f"| outside (med, p90, max)")
        print(f"  " + "-"*120)
        c2_results = {}
        for name, kw in modes_c2:
            strat = make_strat(c2_base, kw)
            is_m  = evaluate(is2,  strat, cache, mt5s.contract_size)
            oos_m = evaluate(oos2, strat, cache, mt5s.contract_size)
            c2_results[name] = (is_m, oos_m)
            for split, m in [("IS", is_m), ("OOS", oos_m)]:
                if m is None:
                    print(f"  {name:<18} {split:<4} n/a")
                else:
                    out = (f"({m['outside_med']:.2f}, {m['outside_p90']:.2f}, "
                           f"{m['outside_max']:.2f}) n={m['outside_count']}")
                    print(f"  {name:<18} {split:<4} ${m['total']:>+7.0f} "
                          f"${m['avg']:>+5.2f} {m['wr']:>3.0f}% "
                          f"${m['max_dd']:>5.0f} {m['sharpe']:>+.3f} "
                          f"{m['operated']:>3}/{m['n']:<3} {m['safety']:>4} "
                          f"| {out}")

        # ── Resumen final ────────────────────────────────────────────────────
        print(f"\n{'='*100}")
        print(f"  RESUMEN — total OOS por modo:")
        print(f"{'='*100}")
        print(f"\n  {'mode':<18} {'C1 IS':>10} {'C1 OOS':>10} {'C2 IS':>10} {'C2 OOS':>10}")
        print(f"  " + "-"*68)
        for name, _ in modes_c1:
            c1 = c1_results[name]
            c2 = c2_results[name]
            def f(m): return f"${m['total']:>+7.0f}" if m else "n/a"
            print(f"  {name:<18} {f(c1[0]):>10} {f(c1[1]):>10} "
                  f"{f(c2[0]):>10} {f(c2[1]):>10}")

        print(f"\n  Lote: {LOT}  Símbolo: {SYMBOL}")
        print(f"{'='*100}")

    finally:
        mt5s.shutdown()
        print(f"\n  MT5 desconectado.")


if __name__ == "__main__":
    main()
