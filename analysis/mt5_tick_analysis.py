"""
mt5_tick_analysis.py — Análisis IS/OOS REAL con ticks de MT5.

Reemplaza todos los análisis previos (que estaban rotos por el bug de timezone
del parquet M1). Usa ticks reales tick a tick, con bid/ask, spread real, y
las mismas reglas de fill que MT5 Strategy Tester.

Flujo:
  1. Carga señales canal 1 + canal 2
  2. Determina los días UTC necesarios para cubrir todas las ventanas
  3. Descarga ticks faltantes (cache por día) con progreso
  4. Para cada (canal × estrategia × split IS/OOS): simula cada señal
  5. Imprime tabla comparativa IS vs OOS
"""

import sys, statistics
from datetime import datetime, timezone, timedelta, date
from pathlib import Path

import pandas as pd

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from mt5_tick_simulator import (
    MT5Session, Strategy, simulate, SYMBOL, LOT, DATA_DIR
)
from mt5_tick_cache import TickCache

PARQUET_C1 = DATA_DIR / "canal1_signals_2026.parquet"
PARQUET_C2 = DATA_DIR / "canal2_signals_2026.parquet"
IS_END = datetime(2026, 3, 1, tzinfo=timezone.utc)


def load_signals(path: Path) -> list[dict]:
    df = pd.read_parquet(path)
    df["dt"] = pd.to_datetime(df["dt"], utc=True)
    sigs = []
    for _, row in df.iterrows():
        tps = [float(row[f"tp{i}"]) for i in range(1, 6) if pd.notna(row[f"tp{i}"])]
        rng = ((float(row["range_low"]), float(row["range_high"]))
               if pd.notna(row["range_low"]) else None)
        sigs.append({
            "id": int(row["id"]),
            "dt": row["dt"].to_pydatetime(),
            "direction": row["direction"],
            "entry": float(row["entry"]) if pd.notna(row["entry"]) else None,
            "range": rng,
            "tps": tps,
            "sl": float(row["sl"]),
        })
    return sigs


def collect_dates_needed(sigs1, sigs2, horizon_min=240) -> list[date]:
    dates = set()
    for s in sigs1 + sigs2:
        t = s["dt"]
        d_from = (t - timedelta(seconds=30)).date()
        d_to   = (t + timedelta(minutes=horizon_min)).date()
        d = d_from
        while d <= d_to:
            dates.add(d)
            d += timedelta(days=1)
    return sorted(dates)


def evaluate(sigs, strat, cache, contract_size, label="") -> dict:
    pls = []
    n_safety = 0
    n_no_ticks = 0
    n_no_fill = 0
    for i, s in enumerate(sigs):
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
        if st == "SAFETY_CANCEL":
            n_safety += 1
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
    operated = [p for p in pls if p != 0]
    wr = (sum(1 for p in operated if p > 0) / len(operated) * 100) if operated else 0
    return {
        "total": sum(pls), "avg": avg, "wr": wr, "max_dd": max_dd,
        "sharpe": sharpe, "n": len(pls), "operated": len(operated),
        "safety": n_safety, "no_ticks": n_no_ticks, "no_fill": n_no_fill,
    }


def fmt(m):
    if m is None: return "n/a"
    return (f"${m['total']:+8.0f} avg${m['avg']:+5.2f} WR{m['wr']:3.0f}% "
            f"DD${m['max_dd']:5.0f} S{m['sharpe']:+.3f} "
            f"n={m['n']}(op:{m['operated']} safe:{m['safety']})")


def main():
    print("="*100)
    print("  ANÁLISIS IS/OOS — TICKS REALES MT5 (Strategy Tester equivalente)")
    print("="*100)

    sigs1 = load_signals(PARQUET_C1)
    sigs2 = load_signals(PARQUET_C2)

    is1 = [s for s in sigs1 if s["dt"] < IS_END]
    oos1 = [s for s in sigs1 if s["dt"] >= IS_END]
    is2 = [s for s in sigs2 if s["dt"] < IS_END]
    oos2 = [s for s in sigs2 if s["dt"] >= IS_END]

    print(f"\n  Canal 1: {len(sigs1)} señales (IS={len(is1)} | OOS={len(oos1)})")
    print(f"  Canal 2: {len(sigs2)} señales (IS={len(is2)} | OOS={len(oos2)})")

    print(f"\n[1] Conectando MT5...")
    mt5s = MT5Session(SYMBOL)
    cache = TickCache(mt5s)

    try:
        # ── 2. Días necesarios + descarga ────────────────────────────────────
        dates = collect_dates_needed(sigs1, sigs2)
        print(f"\n[2] Días UTC requeridos: {len(dates)} ({dates[0]} → {dates[-1]})")
        not_cached = [d for d in dates if not cache.is_cached(d)]
        n_cached_already = len(dates) - len(not_cached)
        print(f"    Ya cacheados: {n_cached_already}")
        print(f"    Por descargar: {len(not_cached)}")

        if not_cached:
            print(f"\n    Iniciando descarga (esto puede tardar varios minutos)...")
            stats = cache.bulk_ensure(not_cached, verbose=True)
            print(f"\n    ── Descarga completada ──")
            print(f"    Días descargados: {stats['downloaded']}")
            print(f"    Días vacíos (fin de semana): {stats['empty_days']}")
            print(f"    Total ticks descargados: {stats['total_ticks']:,}")

        summary = cache.cache_summary()
        print(f"\n[3] Cache final: {summary['n_days']} días, "
              f"{summary['size_mb']:.1f} MB en disco")

        # ── 4. Estrategias a evaluar ─────────────────────────────────────────
        c1_strategies = [
            ("market_only TP3+TS60",  Strategy("market_only", 1, 2, -1, 60)),
            ("extremes TP2+TS60",     Strategy("extremes",    2, 1, -1, 60)),
            ("extremes TP3+TS60",     Strategy("extremes",    2, 2, -1, 60)),
            ("extremes TP4+TS60",     Strategy("extremes",    2, 3, -1, 60)),
            ("extremes TP3+TS120",    Strategy("extremes",    2, 2, -1, 120)),
            ("extremes TP3 (no TS)",  Strategy("extremes",    2, 2, -1, 0)),
        ]
        c2_strategies = [
            ("market_only TP4+BE",    Strategy("market_only", 1, 3, 0, 0)),
            ("intra_dca/2p TP4+BE",   Strategy("intra_dca",   2, 3, 0, 0)),
            ("intra_dca/3p TP4+BE",   Strategy("intra_dca",   3, 3, 0, 0)),
            ("intra_dca/4p TP4+BE",   Strategy("intra_dca",   4, 3, 0, 0)),
            ("intra_dca/4p TP4 noBE", Strategy("intra_dca",   4, 3, -1, 0)),
            ("intra_dca/4p TP3+BE",   Strategy("intra_dca",   4, 2, 0, 0)),
        ]

        print(f"\n{'='*100}")
        print(f"  CANAL 1 — TICKS REALES")
        print(f"{'='*100}")
        for name, strat in c1_strategies:
            print(f"\n  {name}")
            is_m  = evaluate(is1,  strat, cache, mt5s.contract_size)
            oos_m = evaluate(oos1, strat, cache, mt5s.contract_size)
            print(f"    IS:  {fmt(is_m)}")
            print(f"    OOS: {fmt(oos_m)}")

        print(f"\n{'='*100}")
        print(f"  CANAL 2 — TICKS REALES")
        print(f"{'='*100}")
        for name, strat in c2_strategies:
            print(f"\n  {name}")
            is_m  = evaluate(is2,  strat, cache, mt5s.contract_size)
            oos_m = evaluate(oos2, strat, cache, mt5s.contract_size)
            print(f"    IS:  {fmt(is_m)}")
            print(f"    OOS: {fmt(oos_m)}")

        print(f"\n{'='*100}")
        print(f"  Leyenda:")
        print(f"    n=N(op:X safe:Y)   N señales totales, X operadas, Y canceladas por safety")
        print(f"    DD                 max drawdown de la curva equity")
        print(f"    S                  sharpe per-trade (avg/std)")
        print(f"  Lote: {LOT}  Símbolo: {SYMBOL}")
        print(f"{'='*100}")

    finally:
        mt5s.shutdown()
        print(f"\n  MT5 desconectado.")


if __name__ == "__main__":
    main()
