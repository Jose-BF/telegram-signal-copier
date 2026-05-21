"""Lista las últimas señales de cada canal (lectura simple del parquet)."""
import sys
import pandas as pd
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

DATA = Path(__file__).parent / "data"

for canal in ("canal1", "canal2"):
    df = pd.read_parquet(DATA / f"{canal}_signals_2026.parquet")
    df["dt"] = pd.to_datetime(df["dt"], utc=True)
    df = df.sort_values("dt", ascending=False).head(8)

    print(f"\n{'='*90}\n  {canal.upper()} — últimas 8 señales\n{'='*90}")
    for _, r in df.iterrows():
        rng = (f"{r['range_low']:.2f}-{r['range_high']:.2f}"
               if pd.notna(r['range_low']) else "sin rango")
        entry = f"{r['entry']:.2f}" if pd.notna(r['entry']) else "—"
        tps = [f"{r[f'tp{i}']:.2f}" for i in range(1, 6) if pd.notna(r[f'tp{i}'])]
        print(f"  {r['dt']} {r['direction']:4s}  entry={entry:>8s}  range={rng:>16s}  "
              f"SL={r['sl']:.2f}  TPs={tps}")
