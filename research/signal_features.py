"""Causal features of every signal, for filters ("only take signals when ...").

Everything is computed from quotes strictly before t0 = publication + the channel's
median live delay (the moment the bot acts), so a filter never looks ahead.
Signed features are oriented with the signal: > 0 means "in the signal's favour"
(e.g. pre_move_60 > 0: price already moved the signal's way in the last hour).
  hour_utc, weekday, month, kind, forward, high_risk (Gold text contains "high risk"),
  spread, pre_move_5/15/60 (USD), pre_range_60 (USD), atr_m15 (USD, 14 bars),
  rsi_m5 / rsi_m15 (RSI14 of the direction: < 30 = price fell hard for a BUY),
  trend_m15 (signed distance to EMA50 on M15, in ATRs),
  loc_4h (0 = signal at the favourable extreme of the last 4 h range, i.e. BUY at the
  low / SELL at the high = "support/resistance"; 1 = at the opposite extreme),
  day_move (signed move since the broker day's open), gap_prev_min (minutes since
  the previous signal of the channel), prev_same_dir (previous signal same direction).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from research.history_replay import server_offset_s
from research.signal_study import MEDIAN_DELAY_S, TickStore


def rsi(closes, n=14):
    if len(closes) < n + 1:
        return None
    d = np.diff(closes[-(n + 1):])
    up, dn = d[d > 0].sum() / n, -d[d < 0].sum() / n
    if dn == 0:
        return 100.0
    return float(100 - 100 / (1 + up / dn))


def features(sig, store, prev=None, texts=None):
    pub = datetime.fromisoformat(sig["published_utc"])
    t0 = int((pub.timestamp() + MEDIAN_DELAY_S[sig["channel"]]) * 1000)
    sign = 1 if sig["direction"] == "BUY" else -1
    f = {"id": sig["id"], "channel": sig["channel"], "hour_utc": pub.hour, "weekday": pub.weekday(),
         "month": sig["published_utc"][:7], "kind": sig["kind"], "forward": sig["forward"],
         "high_risk": bool(texts and texts.get(sig["id"]))}
    if prev is not None:
        f["gap_prev_min"] = round((pub - datetime.fromisoformat(prev["published_utc"])).total_seconds() / 60, 1)
        f["prev_same_dir"] = prev["direction"] == sig["direction"]
    # strictly before t0 (TickStore.window includes its end; a tick stamped exactly at t0 is not known yet)
    w = store.window(t0 - 5 * 3_600_000, t0 - 1)
    if w is None or len(w[0]) < 50:
        return {**f, "status": "no_ticks"}
    t, bid, ask = w
    mid = (bid + ask) / 2
    f["status"] = "ok"
    f["spread"] = round(float(ask[-1] - bid[-1]), 3)
    for m in (5, 15, 60):
        k = int(np.searchsorted(t, t0 - m * 60_000))
        if k < len(t) - 1 and t0 - t[k] <= (m + 5) * 60_000:
            f[f"pre_move_{m}"] = round(float(sign * (mid[-1] - mid[k])), 2)
    k60 = int(np.searchsorted(t, t0 - 3_600_000))
    if k60 < len(mid):
        f["pre_range_60"] = round(float(mid[k60:].max() - mid[k60:].min()), 2)
    s = pd.Series(mid, index=pd.to_datetime(t, unit="ms"))
    m5 = s.resample("5min").last().dropna().values[:-1]    # completed bars only
    m15b = s.resample("15min").agg(["max", "min", "last"]).dropna()
    m15b = m15b.iloc[:-1]
    r5 = rsi(m5); r15 = rsi(m15b["last"].values)
    if r5 is not None:
        f["rsi_m5"] = round(r5 if sign == 1 else 100 - r5, 1)
    if r15 is not None:
        f["rsi_m15"] = round(r15 if sign == 1 else 100 - r15, 1)
    if len(m15b) >= 15:
        hi, lo, cl = m15b["max"].values, m15b["min"].values, m15b["last"].values
        tr = np.maximum(hi[1:] - lo[1:], np.maximum(abs(hi[1:] - cl[:-1]), abs(lo[1:] - cl[:-1])))
        atr = float(tr[-14:].mean())
        f["atr_m15"] = round(atr, 2)
        ema = pd.Series(cl).ewm(span=50, adjust=False).mean().values[-1]
        if atr > 0:
            f["trend_m15"] = round(float(sign * (mid[-1] - ema) / atr), 2)
    k4 = int(np.searchsorted(t, t0 - 4 * 3_600_000))
    hi4, lo4 = float(mid[k4:].max()), float(mid[k4:].min())
    if hi4 > lo4:
        loc = (mid[-1] - lo4) / (hi4 - lo4)
        f["loc_4h"] = round(float(loc if sign == 1 else 1 - loc), 3)
    off = server_offset_s(pub)
    day_open_ms = int(((pub.timestamp() + off) // 86400 * 86400 - off) * 1000)
    wd = store.window(day_open_ms, min(day_open_ms + 3 * 3_600_000, t0 - 1))
    if wd is not None and len(wd[0]):
        f["day_move"] = round(float(sign * (mid[-1] - (wd[1][0] + wd[2][0]) / 2)), 2)
    return f


def build(universe, tick_root, gold_texts_paths, out):
    sigs = sorted((json.loads(l) for l in open(universe)), key=lambda s: s["published_utc"])
    hr = {}
    ex = json.load(open(gold_texts_paths["export"]))
    for s in sigs:
        if s["channel"] != "canal2":
            continue
        mid = s["id"].split("_")[-1]
        if s["source"] == "export_old_channel":
            hr[s["id"]] = ex.get(mid, {}).get("high_risk", False)
    for p in gold_texts_paths["journal"]:
        for l in open(p):
            r = json.loads(l)
            hr[f"c2_{abs(r['chat_id'])}_{r['message_id']}"] = "high risk" in (r.get("first_text") or "").lower()
    store = TickStore(Path(tick_root))
    rows, prev = [], {}
    for s in sigs:
        rows.append(features(s, store, prev.get(s["channel"]), hr))
        prev[s["channel"]] = s
    Path(out).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return rows


if __name__ == "__main__":
    import sys
    T = "runtime_data/history_triggers_v1/"
    rows = build(sys.argv[1], sys.argv[2], {"export": "runtime_data/signal_universe_v1/gold_export_texts.json",
                 "journal": [T + "gold_journal_triggers.jsonl", T + "gold_journal_old_triggers.jsonl"]}, sys.argv[3])
    from collections import Counter
    print(len(rows), Counter(r["status"] for r in rows), "high_risk", sum(r["high_risk"] for r in rows))
    ok = [r for r in rows if r["status"] == "ok"]
    for k in ("rsi_m5", "rsi_m15", "trend_m15", "loc_4h", "atr_m15", "day_move", "pre_move_60"):
        v = sorted(r[k] for r in ok if k in r)
        print(k, len(v), "p10", v[len(v) // 10], "p50", v[len(v) // 2], "p90", v[9 * len(v) // 10])
