"""
mt5_layered_logic_analysis.py — Sweep de la nueva lógica "layered".

Modela el delay entre el disparo del market (sticker C1 / "BUY NOW" C2) y la
llegada del rango/TPs/SL. Compara contra la baseline (range_delay_sec=0,
safety_mode="cancel") para responder:

  1. ¿Esperar el rango (delay > 0) mejora resultados frente a cancelar
     inmediatamente la safety check actual?
  2. De las 4 opciones para Caso C (precio fuera adverso al llegar el rango):
     ¿cuál gana?
  3. ¿DCA (extremes/intra_dca) sigue siendo perjudicial bajo el nuevo modelo?
  4. ¿El BE@TP1 hardcodeado de C2 ayuda o solo enmascara el problema?
  5. ¿Qué time-stop es óptimo bajo cada combinación?

Reporta P/L IS/OOS + métricas extra:
  - Distribución de casos al llegar el rango (A_inside / B_favorable / C_adverse)
  - % de señales que tocan TP/SL DURANTE la ventana pre-range
  - Pérdidas atribuibles a cada rama
"""

import sys
import statistics
from collections import Counter
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


def evaluate(sigs, strat, cache, contract_size) -> dict:
    """Corre una strat sobre todas las señales y devuelve métricas agregadas."""
    pls = []
    cases = Counter()
    pre_outcomes = Counter()
    final_outcomes = Counter()
    n_status = Counter()
    pls_by_case = {"A_inside": [], "B_favorable": [], "C_adverse": [],
                   "no_range": [], "n/a": []}

    for s in sigs:
        t_from = s["dt"] - timedelta(seconds=30)
        t_to = s["dt"] + timedelta(minutes=strat.horizon_min)
        ticks = cache.get_ticks(t_from, t_to)
        if len(ticks) == 0:
            continue
        result = simulate(s, strat, ticks, contract_size, verbose=False)
        st = result["status"]
        n_status[st] += 1
        if st in ("NO_FILL_TICK", "NO_RANGE_FOR_LIMITS"):
            continue
        pl = result["pl"]
        pls.append(pl)
        cur_case = result.get("range_arrival_decision") or "n/a"
        cases[cur_case] += 1
        if cur_case in pls_by_case:
            pls_by_case[cur_case].append(pl)
        pre = result.get("pre_range_outcome")
        if pre and pre != "none":
            pre_outcomes[pre] += 1
        fo = result.get("final_outcome") or "n/a"
        final_outcomes[fo] += 1

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
        "cases": cases,
        "pre_outcomes": pre_outcomes,
        "final_outcomes": final_outcomes,
        "pls_by_case": {k: (len(v), sum(v)) for k, v in pls_by_case.items()
                        if v},
    }


def fmt_money(x):
    return f"${x:>+7.0f}" if x is not None else "  n/a  "


def print_canal_header(name, base_dims):
    print(f"\n{'='*120}")
    print(f"  {name}  — base: {base_dims}")
    print(f"{'='*120}")
    hdr = (f"  {'config':<60} {'IS P/L':>9} {'OOS P/L':>9} {'IS WR':>6} "
           f"{'OOS WR':>6} {'IS DD':>7} {'OOS DD':>7} {'sharpe O':>9}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))


def print_row(label, is_m, oos_m):
    def f(m):
        return (fmt_money(m["total"]) if m else "  n/a  ")
    def w(m):
        return (f"{m['wr']:>5.0f}%" if m else "  n/a")
    def d(m):
        return (fmt_money(m["max_dd"]) if m else "  n/a  ")
    def s(m):
        return (f"{m['sharpe']:>+.3f}" if m else " n/a   ")
    print(f"  {label:<60} {f(is_m):>9} {f(oos_m):>9} {w(is_m):>6} "
          f"{w(oos_m):>6} {d(is_m):>7} {d(oos_m):>7} {s(oos_m):>9}")


def case_breakdown(m):
    """String compacto del case breakdown."""
    if not m or "cases" not in m or not m["cases"]:
        return ""
    parts = []
    for case in ("A_inside", "B_favorable", "C_adverse", "no_range", "n/a"):
        n = m["cases"].get(case, 0)
        if n == 0: continue
        if case in m.get("pls_by_case", {}):
            n_c, total_c = m["pls_by_case"][case]
            parts.append(f"{case[0]}={n}(${total_c:+.0f})")
        else:
            parts.append(f"{case[0]}={n}")
    return " ".join(parts)


def main():
    # Flags opcionales para correr sólo un canal (útil cuando ya tienes
    # resultados parciales y quieres completar el otro sin repetir trabajo).
    only_c1 = "--c1-only" in sys.argv
    only_c2 = "--c2-only" in sys.argv
    if only_c1 and only_c2:
        print("ERROR: --c1-only y --c2-only son mutuamente excluyentes")
        sys.exit(1)

    print("=" * 120)
    title = "  LAYERED LOGIC SWEEP — modela el delay range vs cancelar inmediato"
    if only_c1:
        title += "  [SOLO CANAL 1]"
    elif only_c2:
        title += "  [SOLO CANAL 2]"
    print(title)
    print("=" * 120)

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

    # ── Dimensiones del sweep ─────────────────────────────────────────────
    delays_c1 = [0, 30, 60, 120]   # texto C1 ~2 min según el listener
    delays_c2 = [0, 15, 30, 60]    # edits C2 en segundos
    adverse_actions = ["close", "hold_with_limits",
                       "hold_no_limits", "hold_sl_to_extreme"]

    # Bases por canal: (entry_mode, n_entries, target_tp_index, be_default, ts_default)
    c1_bases = [
        ("extremes",    2, 2, -1, 60),  # config actual C1
        ("market_only", 1, 2, -1, 60),  # sin DCA
    ]
    c2_bases = [
        ("intra_dca",   4, 3, 0, 0),    # config actual C2 (TP4, BE@TP1)
        ("intra_dca",   4, 3, -1, 0),   # SIN BE auto (la pregunta clave)
        ("market_only", 1, 3, 0, 0),    # sin DCA, con BE
        ("market_only", 1, 3, -1, 0),   # sin DCA, sin BE
    ]
    time_stops = [0, 60]  # segundario — sólo para configs prometedoras

    try:
        # ── CANAL 1 ─────────────────────────────────────────────────────
        if only_c2:
            print("\n  [SKIP] Canal 1 (--c2-only)")
        for i_base, base in enumerate(c1_bases):
            if only_c2:
                break
            em, ne, tp, be, ts = base
            base_dims = (f"entry={em} ne={ne} TP={tp+1} "
                         f"be={'TP'+str(be+1) if be>=0 else 'no'} ts={ts}m")
            print_canal_header(f"CANAL 1 (base #{i_base+1})", base_dims)
            for delay in delays_c1:
                if delay == 0:
                    # baseline: safety_mode=cancel, sin layered
                    strat = Strategy(em, ne, tp, be, ts, safety_mode="cancel")
                    label = f"delay=0 (legacy cancel)"
                    is_m = evaluate(is1, strat, cache, mt5s.contract_size)
                    oos_m = evaluate(oos1, strat, cache, mt5s.contract_size)
                    print_row(label, is_m, oos_m)
                else:
                    for act in adverse_actions:
                        strat = Strategy(em, ne, tp, be, ts,
                                         range_delay_sec=delay,
                                         adverse_action=act)
                        label = f"delay={delay}s adverse={act}"
                        is_m = evaluate(is1, strat, cache, mt5s.contract_size)
                        oos_m = evaluate(oos1, strat, cache, mt5s.contract_size)
                        print_row(label, is_m, oos_m)
                        if oos_m and oos_m.get("cases"):
                            print(f"      OOS cases: {case_breakdown(oos_m)}")

        # ── CANAL 2 ─────────────────────────────────────────────────────
        if only_c1:
            print("\n  [SKIP] Canal 2 (--c1-only)")
        for i_base, base in enumerate(c2_bases):
            if only_c1:
                break
            em, ne, tp, be, ts = base
            base_dims = (f"entry={em} ne={ne} TP={tp+1} "
                         f"be={'TP'+str(be+1) if be>=0 else 'no'} ts={ts}m")
            print_canal_header(f"CANAL 2 (base #{i_base+1})", base_dims)
            for delay in delays_c2:
                if delay == 0:
                    strat = Strategy(em, ne, tp, be, ts, safety_mode="cancel")
                    label = f"delay=0 (legacy cancel)"
                    is_m = evaluate(is2, strat, cache, mt5s.contract_size)
                    oos_m = evaluate(oos2, strat, cache, mt5s.contract_size)
                    print_row(label, is_m, oos_m)
                else:
                    for act in adverse_actions:
                        strat = Strategy(em, ne, tp, be, ts,
                                         range_delay_sec=delay,
                                         adverse_action=act)
                        label = f"delay={delay}s adverse={act}"
                        is_m = evaluate(is2, strat, cache, mt5s.contract_size)
                        oos_m = evaluate(oos2, strat, cache, mt5s.contract_size)
                        print_row(label, is_m, oos_m)
                        if oos_m and oos_m.get("cases"):
                            print(f"      OOS cases: {case_breakdown(oos_m)}")

        print(f"\n  Lote: {LOT}  Símbolo: {SYMBOL}")
        print("=" * 120)
    finally:
        mt5s.shutdown()
        print("\n  MT5 desconectado.")


if __name__ == "__main__":
    main()
