import numpy as np
import pandas as pd

from research.signal_features_v2 import HOUR, MIN, Bars, market_features, provider_features
from research.signal_paths import NS

T_DAY = 1_780_000_000_000 // 86_400_000 * 86_400_000   # a UTC midnight in 2026 (ms)


def synthetic_bars(days=25, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(T_DAY - days * 86_400_000, T_DAY + 86_400_000, MIN, dtype=np.int64)
    close = 4000 + np.cumsum(rng.normal(0, 0.3, len(t)))
    return pd.DataFrame({"high": close + 0.2, "low": close - 0.2, "close": close}, index=t)


def test_market_features_ignore_everything_after_t0():
    df = synthetic_bars()
    t0 = T_DAY + 14 * HOUR + 17 * MIN + 30_000
    cal = np.array([T_DAY + 12 * HOUR + 30 * MIN], dtype=np.int64)
    full = market_features(Bars(df), t0, 1, cal)
    cut = market_features(Bars(df[df.index + MIN <= t0]), t0, 1, cal)
    spiked = df.copy()
    after = spiked.index + MIN > t0
    spiked.loc[after, ["high", "low", "close"]] += 500.0          # a huge move after t0 must not matter
    spike = market_features(Bars(spiked), t0, 1, cal)
    assert full == cut == spike
    for key in ("session", "pd_loc", "asia_loc", "sweep_fav", "dist_round50", "vol_regime", "news_min"):
        assert key in full, key
    assert full["session"] == 2 and full["news_min"] == 107.5 and not full["news_30"]


def test_orientation_flips_with_direction():
    df = synthetic_bars(seed=3)
    t0 = T_DAY + 9 * HOUR
    buy = market_features(Bars(df), t0, 1, np.array([], dtype=np.int64))
    sell = market_features(Bars(df), t0, -1, np.array([], dtype=np.int64))
    assert np.isclose(buy["pd_loc"] + sell["pd_loc"], 1.0)
    assert buy["sweep_fav"] == sell["sweep_adv"] and buy["sweep_adv"] == sell["sweep_fav"]


def test_previous_outcome_only_if_decided_before_t0():
    sig = {"channel": "canal1", "direction": "BUY", "published_utc": "2026-05-01T10:00:00+00:00", "id": "b"}
    prev = {"channel": "canal1", "direction": "SELL", "published_utc": "2026-05-01T09:50:00+00:00", "id": "a"}
    t_prev = int(pd.Timestamp("2026-05-01T09:50:01Z").value)
    # won 5 USD 120 s after its entry -> decided before the new signal
    f = provider_features(sig, [prev], {"a": (t_prev, 120, -1)})
    assert f["prev_outcome"] == 1 and f["dir_streak"] == 0 and f["burst_60"] == 1
    # decided only 20 min later -> unknown at t0
    f = provider_features(sig, [prev], {"a": (t_prev, 1200, 1300)})
    assert f["prev_outcome"] == 0
