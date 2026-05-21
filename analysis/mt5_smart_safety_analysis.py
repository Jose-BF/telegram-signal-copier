"""
mt5_smart_safety_analysis.py — Análisis del modo `smart` (safety asimétrico).

Lógica `smart_safety`:
  - Entry dentro del rango → opera normal
  - Entry fuera HACIA EL TP (favorable, ya en profit) → mantener market,
    cancelar limits que ya no aplican
  - Entry fuera HACIA EL SL (adverso) → cerrar todo (igual que cancel)

Compara contra `cancel` baseline para ambas estrategias base.

Adicionalmente reporta:
  - cuántas señales caen en cada categoría (dentro / favorable / adversa)
  - P/L atribuible a cada categoría
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
from tick_cache import TickCache
from mt5_tick_analysis import load_signals

PARQUET_C1 = DATA_DIR / "canal1_signals_2026.parquet"
PARQUET_C2 = DATA_DIR / "canal2_signals_2026.parquet"
IS_END = datetime(2026, 3, 1, tzinfo=timezone.utc)


def classify_entry(sig, would_fill_price):
    """Devuelve 'inside' | 'favorable' | 'adverse' | 'no_range'."""
    rng = sig.get("range")
    if rng is None:
        return "no_range"
    if rng[0] <= would_fill_price <= rng[1]:
        return "inside"
    direction = sig["direction"]
    if direction == "BUY":
        return "favorable" if would_fill_price > rng[1] else "adverse"
    else:  # SELL
        return "favorable" if would_fill_price < rng[0] else "adverse"


def evaluate(sigs, strat, cache, contract_size) -> dict:
    pls = []
    pl_by_cat = {"inside": [], "favorable": [], "adverse": [], "no_range": []}
    n_status = {}
    for s in sigs:
        t_from = s["dt"] - timedelta(seconds=30)
        t_to   = s["dt"] + timedelta(minutes=strat.horizon_min)
        ticks = cache.get_ticks(t_from, t_to)
        if len(ticks) == 0:
            continue
        result = simulate(s, strat, ticks, contract_size, verbose=False)
        st = result["status"]
        n_status[st] = n_status.get(st, 0) + 1
        if st in ("NO_FILL_TICK", "NO_RANGE_FOR_LIMITS"):
            continue
        wf = result.get("would_market_fill")
        cat = classify_entry(s, wf) if wf is not None else "no_range"
        pl = result["pl"]
        pls.append(pl)
        pl_by_cat[cat].append(pl)

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
    return {
        "total": sum(pls), "avg": avg, "wr": wr, "max_dd": max_dd,
        "sharpe": sharpe, "n": len(pls),
        "by_cat": {k: (len(v), sum(v)) for k, v in pl_by_cat.items()},
        "status": n_status,
    }


def main():
    print("="*100)
    print("  SMART SAFETY (asimétrico) — comparativa vs cancel baseline")
    print("="*100)

    sigs1 = load_signals(PARQUET_C1)
    sigs2 = load_signals(PARQUET_C2)
    is1 = [s for s in sigs1 if s["dt"] < IS_END]
    oos1 = [s for s in sigs1 if s["dt"] >= IS_END]
    is2 = [s for s in sigs2 if s["dt"] < IS_END]
    oos2 = [s for s in sigs2 if s["dt"] >= IS_END]

    mt5s = MT5Session(SYMBOL)
    cache = TickCache(mt5s)
    try:
        c1_base = ("extremes", 2, 2, -1, 0)
        c2_base = ("intra_dca", 4, 2, 0, 0)

        modes = [
            ("cancel", dict(safety_mode="cancel")),
            ("smart",  dict(safety_mode="smart")),
        ]

        def make_strat(base, kw):
            em, ne, tp, be, ts = base
            return Strategy(em, ne, tp, be, ts, **kw)

        for canal_name, base, sigs_is, sigs_oos in [
            ("CANAL 1 (extremes/2p TP3 noTS)", c1_base, is1, oos1),
            ("CANAL 2 (intra_dca/4p TP3+BE)",  c2_base, is2, oos2),
        ]:
            print(f"\n{'='*100}")
            print(f"  {canal_name}")
            print(f"{'='*100}")
            for name, kw in modes:
                strat = make_strat(base, kw)
                is_m = evaluate(sigs_is, strat, cache, mt5s.contract_size)
                oos_m = evaluate(sigs_oos, strat, cache, mt5s.contract_size)
                print(f"\n  --- mode: {name} ---")
                for split, m in [("IS", is_m), ("OOS", oos_m)]:
                    if m is None:
                        print(f"    {split}: n/a")
                        continue
                    print(f"    {split}:  ${m['total']:>+7.0f}  avg${m['avg']:>+5.2f}  "
                          f"WR{m['wr']:>3.0f}%  DD${m['max_dd']:>5.0f}  "
                          f"S{m['sharpe']:>+.3f}  n={m['n']}")
                    print(f"        by_cat: ", end="")
                    for cat in ("inside", "favorable", "adverse", "no_range"):
                        n, total = m["by_cat"][cat]
                        if n > 0:
                            print(f"{cat}=${total:+5.0f}(n={n})  ", end="")
                    print()
                    if m["status"]:
                        print(f"        status: {m['status']}")

        print(f"\n  Lote: {LOT}  Símbolo: {SYMBOL}")
        print(f"{'='*100}")
    finally:
        mt5s.shutdown()
        print(f"\n  MT5 desconectado.")


if __name__ == "__main__":
    main()
