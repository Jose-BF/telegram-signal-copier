"""
Smoke test rápido: una sola señal en los 4 safety modes para validar
que el simulador modificado funciona y los modos producen resultados
distintos esperables.

Señal: canal 1 SELL 12:24 UTC 2026-04-22 (la del bug previo).
Estrategia base: extremes/2p TP3+TS60.
"""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pandas as pd

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from mt5_tick_simulator import MT5Session, Strategy, simulate, SYMBOL, LOT, DATA_DIR
from tick_cache import TickCache


def main():
    df = pd.read_parquet(DATA_DIR / "canal1_signals_2026.parquet")
    df["dt"] = pd.to_datetime(df["dt"], utc=True)
    target = datetime(2026, 4, 22, 12, 24, tzinfo=timezone.utc)
    cand = df[(df["dt"] >= target) & (df["dt"] < target + timedelta(minutes=2))]
    row = cand.iloc[0]
    sig = {
        "id": int(row["id"]),
        "dt": row["dt"].to_pydatetime(),
        "direction": row["direction"],
        "range": ((float(row["range_low"]), float(row["range_high"]))
                  if pd.notna(row["range_low"]) else None),
        "tps": [float(row[f"tp{i}"]) for i in range(1, 6) if pd.notna(row[f"tp{i}"])],
        "sl": float(row["sl"]),
    }
    print(f"Señal {sig['id']}: {sig['dt']} {sig['direction']}")
    print(f"  range={sig['range']}  TPs={sig['tps']}  SL={sig['sl']}")

    mt5s = MT5Session(SYMBOL)
    cache = TickCache(mt5s)
    try:
        t_from = sig["dt"] - timedelta(seconds=30)
        t_to   = sig["dt"] + timedelta(minutes=240)
        ticks = cache.get_ticks(t_from, t_to)
        print(f"  Ticks: {len(ticks)}")

        modes = [
            # Legacy safety modes (range_delay_sec=0)
            ("cancel (actual)",       Strategy("extremes", 2, 2, -1, 60,
                                                safety_mode="cancel")),
            ("tolerance $1.00",       Strategy("extremes", 2, 2, -1, 60,
                                                safety_mode="tolerance",
                                                safety_tolerance=1.0)),
            ("tolerance $5.00",       Strategy("extremes", 2, 2, -1, 60,
                                                safety_mode="tolerance",
                                                safety_tolerance=5.0)),
            ("limit_fallback",        Strategy("extremes", 2, 2, -1, 60,
                                                safety_mode="limit_fallback")),
            ("limit_first",           Strategy("extremes", 2, 2, -1, 60,
                                                safety_mode="limit_first")),
            # Layered logic (range_delay_sec > 0) — pre-range window modela el
            # delay entre disparo del market y llegada del rango/TPs/SL.
            ("layered 60s close",     Strategy("extremes", 2, 2, -1, 60,
                                                range_delay_sec=60,
                                                adverse_action="close")),
            ("layered 60s hold+lim",  Strategy("extremes", 2, 2, -1, 60,
                                                range_delay_sec=60,
                                                adverse_action="hold_with_limits")),
            ("layered 60s hold-lim",  Strategy("extremes", 2, 2, -1, 60,
                                                range_delay_sec=60,
                                                adverse_action="hold_no_limits")),
            ("layered 60s hold+SLext",Strategy("extremes", 2, 2, -1, 60,
                                                range_delay_sec=60,
                                                adverse_action="hold_sl_to_extreme")),
        ]
        print(f"\n{'Mode':<25} {'Status':<18} {'P/L':>9} {'pre':>14} "
              f"{'arrival':>13} {'fills':>5}")
        print("-" * 90)
        for name, strat in modes:
            r = simulate(sig, strat, ticks, mt5s.contract_size, verbose=False)
            wf = r.get('would_market_fill')
            pre = r.get('pre_range_outcome') or "—"
            arr = r.get('range_arrival_decision') or "—"
            print(f"{name:<25} {r['status']:<18} ${r['pl']:>+7.2f} {pre:>14} "
                  f"{arr:>13} {r.get('n_filled', 0):>5}")
        print(f"\nWould-be market fill: ${wf:.2f}  (range: {sig['range']})")
    finally:
        mt5s.shutdown()


if __name__ == "__main__":
    main()
