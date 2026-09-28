"""Support/resistance levels known at a moment t0 (causal), from 1-minute mid bars.

Level families (each tagged):
  swing_15m / swing_1h / swing_4h   fractal highs/lows (w=2 bars each side), confirmed before t0,
                                    from the last 5 days
  prev_day / prev_week              high and low of the previous broker day / previous 5 broker days
  asia                              today's 00:00-07:00 UTC high and low (only after 07:00)
  round50 / round10                 multiples of 50 and of 10 USD near the price
Used by block P to test whether a provider's prices (signal, zone, SL, TP) sit on levels more often
than random prices around them.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MIN = 60_000
HOUR = 3_600_000
DAY = 86_400_000


class LevelBook:
    def __init__(self, bars: pd.DataFrame):
        self.t = bars.index.to_numpy(np.int64)
        self.h = bars["high"].to_numpy(float)
        self.l = bars["low"].to_numpy(float)
        self.c = bars["close"].to_numpy(float)

    def _upto(self, t0_ms):
        return int(np.searchsorted(self.t, t0_ms - MIN, side="right"))

    def _swings(self, a, k, minutes, w=2):
        if k - a < minutes * (2 * w + 1):
            return []
        t = self.t[a:k]
        bucket = (t - t[0]) // (minutes * MIN)
        df = pd.DataFrame({"b": bucket, "h": self.h[a:k], "l": self.l[a:k]}).groupby("b").agg({"h": "max", "l": "min"})
        hs, ls = df["h"].to_numpy(), df["l"].to_numpy()
        n = len(hs)
        out = []
        # the last w buckets cannot be confirmed yet; the current (incomplete) bucket is excluded too
        for i in range(w, n - w - 1):
            if hs[i] == hs[i - w:i + w + 1].max():
                out.append(hs[i])
            if ls[i] == ls[i - w:i + w + 1].min():
                out.append(ls[i])
        return out

    def levels(self, t0_ms: int) -> dict[str, list[float]]:
        k = self._upto(t0_ms)
        if k < 300:
            return {}
        a5 = int(np.searchsorted(self.t, t0_ms - 5 * DAY))
        now = self.c[k - 1]
        out = {"swing_15m": self._swings(max(a5, k - 3 * DAY // MIN), k, 15),
               "swing_1h": self._swings(a5, k, 60), "swing_4h": self._swings(a5, k, 240)}
        day0 = t0_ms // DAY * DAY
        i_today = int(np.searchsorted(self.t, day0))
        i_prev = int(np.searchsorted(self.t, day0 - DAY))
        if i_today - i_prev > 60:
            out["prev_day"] = [float(self.h[i_prev:i_today].max()), float(self.l[i_prev:i_today].min())]
        if i_today - a5 > 300:
            out["prev_week"] = [float(self.h[a5:i_today].max()), float(self.l[a5:i_today].min())]
        hour = (t0_ms - day0) // HOUR
        if hour >= 7:
            j = min(int(np.searchsorted(self.t, day0 + 7 * HOUR)), k)
            if j - i_today > 60:
                out["asia"] = [float(self.h[i_today:j].max()), float(self.l[i_today:j].min())]
        base50 = np.floor(now / 50) * 50
        out["round50"] = [base50 + 50 * i for i in range(-2, 4)]
        base10 = np.floor(now / 10) * 10
        out["round10"] = [base10 + 10 * i for i in range(-3, 5)]
        return out


def nearest(price: float, levels: dict[str, list[float]], families=None) -> float:
    vals = [v for f, vs in levels.items() if families is None or f in families for v in vs]
    return float(min(abs(price - v) for v in vals)) if vals else np.nan
