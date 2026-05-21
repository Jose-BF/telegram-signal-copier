"""
tick_cache.py — Cache de ticks XAUUSD por día UTC.

Estructura en disco:
  data/ticks_cache/<YYYY-MM-DD>.parquet  → todos los ticks de ese día UTC

Uso:
  cache = TickCache(mt5_session)
  ticks_df = cache.get_ticks(t_from_utc, t_to_utc)
  # Si falta algún día → lo descarga primero, lo cachea, devuelve subset.

Diseño:
  - Día = ventana UTC [00:00:00, 24:00:00). Independiente del server time.
  - Si una señal cruza medianoche, get_ticks carga 2 archivos y los une.
  - Días vacíos (fin de semana, festivos) se cachean también con DataFrame vacío
    para no re-intentar descarga inútil.
  - Compresión zstd → ratio ~3x mejor que default snappy.
"""

import sys
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from typing import Optional

import pandas as pd

DATA_DIR = Path(__file__).parent / "data"
TICKS_CACHE = DATA_DIR / "ticks_cache"
TICKS_CACHE.mkdir(exist_ok=True, parents=True)


class TickCache:
    """Cache por día UTC. Necesita un MT5Session para descargas."""

    EMPTY_COLS = ["time_msc", "bid", "ask", "last", "volume", "flags", "time_utc"]

    def __init__(self, mt5_session, mem_days_cap: int = 25):
        """
        mem_days_cap: máximo de DataFrames de día mantenidos en RAM (LRU).
        Cada día son ~5-7 MB en RAM, así que 30 días = ~200 MB. Esto evita
        re-leer parquet del disco en cada `get_ticks` durante un sweep
        masivo (78 configs × 300 sigs = 24k lecturas — sin caché es el
        bottleneck principal).
        """
        self.mt5 = mt5_session
        self._mem: dict[date, pd.DataFrame] = {}
        self._mem_order: list[date] = []  # insertion order para LRU
        self._mem_cap = mem_days_cap

    def _file_for_date(self, d: date) -> Path:
        return TICKS_CACHE / f"{d.isoformat()}.parquet"

    def _load_day_into_mem(self, d: date) -> pd.DataFrame:
        """Devuelve el DataFrame del día d, cargándolo en RAM si no está.

        Aplica LRU: si superamos `_mem_cap`, elimina el día más antiguo.
        """
        if d in self._mem:
            # refresh LRU position
            self._mem_order.remove(d)
            self._mem_order.append(d)
            return self._mem[d]

        path = self._file_for_date(d)
        if not path.exists():
            self.ensure_day(d)
        df = pd.read_parquet(path)
        if len(df) > 0:
            df["time_utc"] = pd.to_datetime(df["time_utc"], utc=True)

        # LRU eviction
        while len(self._mem_order) >= self._mem_cap:
            old = self._mem_order.pop(0)
            self._mem.pop(old, None)

        self._mem[d] = df
        self._mem_order.append(d)
        return df

    def is_cached(self, d: date) -> bool:
        return self._file_for_date(d).exists()

    def ensure_day(self, d: date) -> int:
        """Descarga y guarda los ticks de ese día UTC. Devuelve nº ticks."""
        path = self._file_for_date(d)
        if path.exists():
            return -1  # ya existía
        t_from = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        t_to   = t_from + timedelta(days=1)
        df = self.mt5.fetch_ticks(t_from, t_to)
        if len(df) == 0:
            # Marcar día como vacío para no re-intentar
            empty = pd.DataFrame(columns=self.EMPTY_COLS)
            empty.to_parquet(path, index=False, compression="zstd")
            return 0
        df.to_parquet(path, index=False, compression="zstd")
        return len(df)

    def bulk_ensure(self, dates: list[date], verbose: bool = True) -> dict:
        """Asegura cache de varios días. Reporta progreso."""
        stats = {"downloaded": 0, "cached_already": 0,
                 "total_ticks": 0, "empty_days": 0}
        n = len(dates)
        for i, d in enumerate(dates):
            if self.is_cached(d):
                stats["cached_already"] += 1
                continue
            ticks = self.ensure_day(d)
            stats["downloaded"] += 1
            stats["total_ticks"] += max(ticks, 0)
            if ticks == 0:
                stats["empty_days"] += 1
            if verbose:
                print(f"  [{i+1:3d}/{n}] {d.isoformat()}: "
                      f"{ticks:>7,} ticks" if ticks > 0 else
                      f"  [{i+1:3d}/{n}] {d.isoformat()}: vacío (fin de semana/festivo)")
        return stats

    def get_ticks(self, t_from_utc: datetime, t_to_utc: datetime) -> pd.DataFrame:
        """Devuelve ticks en [t_from, t_to]. Usa caché RAM (LRU) sobre días."""
        d_from = t_from_utc.date()
        d_to   = t_to_utc.date()
        days = []
        d = d_from
        while d <= d_to:
            days.append(d)
            d += timedelta(days=1)
        dfs = []
        for d in days:
            df = self._load_day_into_mem(d)
            if len(df) > 0:
                dfs.append(df)
        if not dfs:
            return pd.DataFrame(columns=self.EMPTY_COLS)
        # Caso común: 1 solo día → evita concat
        if len(dfs) == 1:
            all_df = dfs[0]
        else:
            all_df = pd.concat(dfs, ignore_index=True)
        mask = (all_df["time_utc"] >= t_from_utc) & (all_df["time_utc"] <= t_to_utc)
        return all_df[mask].sort_values("time_msc").reset_index(drop=True)

    def disk_usage_mb(self) -> float:
        total = sum(f.stat().st_size for f in TICKS_CACHE.glob("*.parquet"))
        return total / (1024 * 1024)

    def cache_summary(self) -> dict:
        files = list(TICKS_CACHE.glob("*.parquet"))
        # Solo contamos los con formato YYYY-MM-DD
        day_files = [f for f in files if len(f.stem) == 10 and f.stem[4] == "-"]
        return {
            "n_days": len(day_files),
            "size_mb": sum(f.stat().st_size for f in day_files) / (1024 * 1024),
            "first_day": min((f.stem for f in day_files), default=None),
            "last_day": max((f.stem for f in day_files), default=None),
        }
