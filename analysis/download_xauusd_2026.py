"""
Descarga histórico XAUUSD M1 desde MT5 para todo 2026 disponible.
Guarda a parquet (rápido + compacto) en `data/xauusd_m1_2026.parquet`.

Por trozos semanales para no saturar la API.
"""

import sys
import io
from pathlib import Path
from datetime import datetime, timezone, timedelta

import MetaTrader5 as mt5
import pandas as pd

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

OUT = Path(__file__).parent / "data"
OUT.mkdir(exist_ok=True)
OUT_FILE = OUT / "xauusd_m1_2026.parquet"

SYMBOL = "XAUUSD"
START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END   = datetime(2026, 4, 23, tzinfo=timezone.utc)


def main():
    if not mt5.initialize():
        print("ERROR initialize:", mt5.last_error())
        return
    print(f"MT5 v{mt5.version()}")
    mt5.symbol_select(SYMBOL, True)

    all_rates = []
    cursor = START
    week = timedelta(days=7)

    while cursor < END:
        nxt = min(cursor + week, END)
        print(f"  {cursor.date()} -> {nxt.date()}", end=" ", flush=True)
        r = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M1, cursor, nxt)
        if r is None or len(r) == 0:
            print("(0 rates)")
        else:
            all_rates.append(r)
            print(f"({len(r)} rates)")
        cursor = nxt

    if not all_rates:
        print("Sin datos descargados.")
        mt5.shutdown()
        return

    import numpy as np
    arr = np.concatenate(all_rates)
    df = pd.DataFrame(arr)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.drop_duplicates(subset="time").sort_values("time").reset_index(drop=True)

    print(f"\nTotal rates: {len(df):,}  ({df['time'].min()} -> {df['time'].max()})")
    print(f"Guardando en {OUT_FILE}")
    df.to_parquet(OUT_FILE, index=False)
    print(f"OK ({OUT_FILE.stat().st_size/1024:.1f} KB)")
    mt5.shutdown()


if __name__ == "__main__":
    main()
