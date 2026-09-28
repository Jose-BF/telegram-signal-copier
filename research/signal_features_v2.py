"""Causal signal features v2 (search 2, M5). The v1 features (research/signal_features.py) are
kept as they are; v2 adds market-structure, calendar and provider-behaviour features.

Everything uses information strictly before t0 = publication + the channel's median live
delay: 1-minute mid bars that ended at or before t0, the signal list up to the previous
signal, and outcomes of earlier signals only if they were already decided before t0.
Signed features are oriented with the signal (> 0 / "fav" = in the signal's favour).

  session (0 Asia 0-7 UTC, 1 London 7-12, 2 New York 12-17, 3 late 17-24), min_in_session
  pd_loc      position in the previous broker day's range, oriented (0 = favourable extreme,
              i.e. a BUY at yesterday's low); pd_break: +1 beyond the favourable-side... see code
  asia_loc, asia_range   same for today's 00:00-07:00 UTC range (only after 07:00 UTC)
  sweep_fav / sweep_adv  in the last 30 min price pierced the 4 h extreme on the adverse side and
              came back (sweep_fav: e.g. stops under the low taken, then back up, for a BUY),
              or pierced the favourable-side extreme and came back (sweep_adv)
  dist_round50           distance to the nearest multiple of 50 USD
  vol_regime             mean hourly range of the last 14 h / mean hourly range of the prior 20 days
  news_min               signed minutes from the nearest NFP/CPI/FOMC (negative = before it)
  news_30                |news_min| <= 30; news_day: an event on the same UTC date
  burst_60               signals of the same channel in the previous 60 min
  dir_streak             consecutive previous signals with the same direction
  prev_outcome           previous signal, follow direction, +-5 USD race: +1 won, -1 lost,
                         0 = not decided before t0 (or no previous signal)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from research.history_replay import server_offset_s
from research.signal_features import features as features_v1
from research.signal_paths import LEVELS, NS, load as load_paths
from research.signal_study import MEDIAN_DELAY_S, TickStore

MIN = 60_000
HOUR = 3_600_000


def build_minute_bars(tick_root) -> pd.DataFrame:
    """1-minute mid bars (start ms UTC -> high, low, close) of every XAUUSD tape day."""
    store = TickStore(Path(tick_root))
    frames = []
    for p in sorted((Path(tick_root) / "XAUUSD").glob("*.parquet")):
        got = store.day(p.stem)
        if got is None:
            continue
        t, b, a = got
        mid = (b + a) / 2
        m = t // MIN * MIN
        df = pd.DataFrame({"m": m, "mid": mid}).groupby("m")["mid"].agg(["max", "min", "last"])
        frames.append(df)
    bars = pd.concat(frames)
    bars = bars[~bars.index.duplicated(keep="last")].sort_index()
    bars.columns = ["high", "low", "close"]
    return bars


class Bars:
    def __init__(self, df: pd.DataFrame):
        self.t = df.index.to_numpy(np.int64)
        self.h = df["high"].to_numpy(float)
        self.l = df["low"].to_numpy(float)
        self.c = df["close"].to_numpy(float)

    def upto(self, t0_ms: int) -> int:
        """Number of bars that ended at or before t0 (bar [s, s+1min) ends at s + 1 min)."""
        return int(np.searchsorted(self.t, t0_ms - MIN, side="right"))


def _broker_day_start_ms(t_ms: int) -> int:
    d = datetime.fromtimestamp(t_ms / 1000, timezone.utc)
    off = server_offset_s(d)
    return int(((t_ms / 1000 + off) // 86400 * 86400 - off) * 1000)


def load_calendar(path="runtime_data/news_calendar_2026.json") -> np.ndarray:
    ev = json.load(open(path))["events"]
    return np.array(sorted(int(datetime.fromisoformat(e["utc"]).timestamp() * 1000) for e in ev), dtype=np.int64)


def market_features(bars: Bars, t0_ms: int, sign: int, calendar: np.ndarray) -> dict:
    f = {}
    k = bars.upto(t0_ms)
    if k < 60:
        return f
    now = bars.c[k - 1]
    hour = datetime.fromtimestamp(t0_ms / 1000, timezone.utc).hour
    sess_start = {0: 0, 1: 7, 2: 12, 3: 17}
    s = 0 if hour < 7 else 1 if hour < 12 else 2 if hour < 17 else 3
    f["session"] = s
    day0 = t0_ms // 86_400_000 * 86_400_000
    f["min_in_session"] = int((t0_ms - day0 - sess_start[s] * HOUR) // MIN)
    # previous broker day range
    today = _broker_day_start_ms(t0_ms)
    i_today = int(np.searchsorted(bars.t, today, side="left"))
    if i_today > 0:
        prev_day = _broker_day_start_ms(int(bars.t[i_today - 1]))
        i_prev = int(np.searchsorted(bars.t, prev_day, side="left"))
        hi, lo = bars.h[i_prev:i_today].max(), bars.l[i_prev:i_today].min()
        if hi > lo:
            loc = (now - lo) / (hi - lo)
            f["pd_loc"] = round(float(loc if sign == 1 else 1 - loc), 3)
            f["pd_range"] = round(float(hi - lo), 2)
            fav_side_break = (now > hi) if sign == 1 else (now < lo)
            adv_side_break = (now < lo) if sign == 1 else (now > hi)
            f["pd_break"] = 1 if fav_side_break else -1 if adv_side_break else 0
    # Asia range of the current UTC day
    if hour >= 7:
        a0, a1 = int(np.searchsorted(bars.t, day0)), int(np.searchsorted(bars.t, day0 + 7 * HOUR))
        a1 = min(a1, k)
        if a1 - a0 >= 60:
            hi, lo = bars.h[a0:a1].max(), bars.l[a0:a1].min()
            if hi > lo:
                loc = (now - lo) / (hi - lo)
                f["asia_loc"] = round(float(loc if sign == 1 else 1 - loc), 3)
                f["asia_range"] = round(float(hi - lo), 2)
    # liquidity sweep in the last 30 min against the 4 h before it
    j30 = int(np.searchsorted(bars.t, t0_ms - 30 * MIN))
    j4 = int(np.searchsorted(bars.t, t0_ms - 30 * MIN - 4 * HOUR))
    if j30 - j4 >= 60 and k - j30 >= 5:
        hi_b, lo_b = bars.h[j4:j30].max(), bars.l[j4:j30].min()
        hi_r, lo_r = bars.h[j30:k].max(), bars.l[j30:k].min()
        swept_low = lo_r < lo_b and now > lo_b
        swept_high = hi_r > hi_b and now < hi_b
        f["sweep_fav"] = int(swept_low if sign == 1 else swept_high)
        f["sweep_adv"] = int(swept_high if sign == 1 else swept_low)
    f["dist_round50"] = round(float(min(now % 50, 50 - now % 50)), 2)
    # volatility regime: hourly ranges
    h14 = int(np.searchsorted(bars.t, t0_ms - 14 * HOUR))
    h20d = int(np.searchsorted(bars.t, t0_ms - 20 * 24 * HOUR))
    if k - h14 >= 300 and h14 - h20d >= 3000:
        def hourly_range(a, b):
            hrs = bars.t[a:b] // HOUR
            df = pd.DataFrame({"h": hrs, "hi": bars.h[a:b], "lo": bars.l[a:b]}).groupby("h").agg({"hi": "max", "lo": "min"})
            return float((df.hi - df.lo).mean())
        base = hourly_range(h20d, h14)
        if base > 0:
            f["vol_regime"] = round(hourly_range(h14, k) / base, 3)
    # news
    if len(calendar):
        i = int(np.searchsorted(calendar, t0_ms))
        cands = [calendar[j] for j in (i - 1, i) if 0 <= j < len(calendar)]
        near = min(cands, key=lambda e: abs(e - t0_ms))
        f["news_min"] = round((t0_ms - near) / MIN, 1)
        f["news_30"] = bool(abs(t0_ms - near) <= 30 * MIN)
        f["news_day"] = bool(any(e // 86_400_000 == t0_ms // 86_400_000 for e in cands))
    return f


def provider_features(sig, history, paths_index) -> dict:
    """history: earlier signals of the same channel (dicts with published_utc, direction, id)."""
    f = {}
    pub = datetime.fromisoformat(sig["published_utc"])
    t0_ms = int((pub.timestamp() + MEDIAN_DELAY_S[sig["channel"]]) * 1000)
    f["burst_60"] = sum(1 for h in history if 0 < (pub - datetime.fromisoformat(h["published_utc"])).total_seconds() <= 3600)
    streak = 0
    for h in reversed(history):
        if h["direction"] != sig["direction"]:
            break
        streak += 1
    f["dir_streak"] = streak
    f["prev_outcome"] = 0
    if history:
        p = paths_index.get(history[-1]["id"])
        if p is not None:
            entry_ns, fav, adv = p
            tf = entry_ns + fav * NS if fav >= 0 else None
            ta = entry_ns + adv * NS if adv >= 0 else None
            first = min([x for x in (tf, ta) if x is not None], default=None)
            if first is not None and first < t0_ms * 1_000_000:
                f["prev_outcome"] = 1 if (tf is not None and (ta is None or tf < ta)) else -1
    return f


def paths_index_for(npz_path):
    z = load_paths(npz_path)
    j5 = int(np.flatnonzero(np.isclose(LEVELS, 5.0))[0])
    i = np.arange(len(z["ids"])); d = z["direction"].astype(int)
    return {sid: (int(e), int(fa), int(ad)) for sid, e, fa, ad in
            zip(z["ids"], z["entry_ns"], z["fp_fav"][i, d, j5], z["fp_adv"][i, d, j5])}


def build(universe, tick_root, bars_df, paths_npz, out, v1_path=None, calendar_path="runtime_data/news_calendar_2026.json"):
    """v1 features are reused from `v1_path` when given (keeps high_risk from the provider texts);
    otherwise recomputed without texts."""
    sigs = sorted((json.loads(l) for l in open(universe, encoding="utf-8")), key=lambda s: s["published_utc"])
    v1 = {r["id"]: r for r in map(json.loads, open(v1_path, encoding="utf-8"))} if v1_path else {}
    store = TickStore(Path(tick_root))
    bars = Bars(bars_df)
    cal = load_calendar(calendar_path)
    pidx = paths_index_for(paths_npz)
    hist = {"canal1": [], "canal2": []}
    rows = []
    for s in sigs:
        prev = hist[s["channel"]][-1] if hist[s["channel"]] else None
        f = dict(v1[s["id"]]) if s["id"] in v1 else features_v1(s, store, prev, None)
        if f.get("status") == "ok":
            pub = datetime.fromisoformat(s["published_utc"])
            t0 = int((pub.timestamp() + MEDIAN_DELAY_S[s["channel"]]) * 1000)
            f.update(market_features(bars, t0, 1 if s["direction"] == "BUY" else -1, cal))
        f.update(provider_features(s, hist[s["channel"]], pidx))
        rows.append(f)
        hist[s["channel"]].append(s)
    Path(out).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return rows
