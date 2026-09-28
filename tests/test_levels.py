import numpy as np
import pandas as pd

from research.levels import MIN, LevelBook, nearest

T0 = 1_780_000_000_000 // 86_400_000 * 86_400_000


def bars(prices, start):
    t = start + np.arange(len(prices), dtype=np.int64) * MIN
    p = np.asarray(prices, float)
    return pd.DataFrame({"high": p + 0.1, "low": p - 0.1, "close": p}, index=t)


def test_levels_are_causal_and_found():
    rng = np.random.default_rng(0)
    p = 4000 + np.cumsum(rng.normal(0, 0.5, 6 * 1440))
    df = bars(p, T0 - 5 * 86_400_000)
    t0 = T0 + 10 * 3_600_000
    full = LevelBook(df).levels(t0)
    spiked = df.copy(); after = spiked.index + MIN > t0
    spiked.loc[after, ["high", "low", "close"]] += 300
    assert LevelBook(spiked).levels(t0) == full
    for fam in ("swing_15m", "swing_1h", "prev_day", "prev_week", "asia", "round50"):
        assert full.get(fam), fam


def test_nearest_distance():
    lv = {"round50": [4000.0, 4050.0], "prev_day": [4031.5]}
    assert nearest(4030.0, lv) == 1.5
    assert nearest(4030.0, lv, families=("round50",)) == 20.0
