"""Strategy-free study of what price does after each provider signal.

For every signal (signal_universe_v1): entry = first broker tick at or after
publication + the channel's median live Telegram->bot delay, at the price a
follower pays (ask for BUY, bid for SELL). Then, per horizon, the best move in
favour (MFE), the worst move against (MAE) and the result of closing at the
horizon, all in USD per ounce and net of the spread (exits use the opposite
quote). "Race" = which comes first within 4 h: +X in favour or -X against.
Pre-signal context uses mid prices (signed in the signal's direction: > 0 means
the signal follows the last move, < 0 means it goes against it).
Broker server time: UTC+2 before 2026-03-08 07:00 UTC, UTC+3 after.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from research.history_replay import server_offset_s

HORIZONS_MIN = (1, 5, 15, 30, 60, 120, 240)
RACES = (2.0, 3.0, 5.0, 10.0, 20.0)
MEDIAN_DELAY_S = {"canal1": 1.21, "canal2": 1.40}
CLOSED_GAP_S = 900  # a quote gap longer than this means the market was closed


class TickStore:
    def __init__(self, root: Path):
        self.root = Path(root); self.cache = {}

    def day(self, day: str):
        if day not in self.cache:
            path = self.root / "XAUUSD" / f"{day}.parquet"
            if not path.exists():
                self.cache[day] = None
            else:
                f = pd.read_parquet(path, columns=["time_msc", "bid", "ask"])
                d0 = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
                off = server_offset_s(d0 + timedelta(hours=12))
                t = f.time_msc.to_numpy(np.int64) - off * 1000
                self.cache[day] = (t, f.bid.to_numpy(float), f.ask.to_numpy(float))
            if len(self.cache) > 6:
                self.cache.pop(next(iter(self.cache)))
        return self.cache[day]

    def window(self, start_ms: int, end_ms: int):
        parts = []
        d = datetime.fromtimestamp(start_ms / 1000, timezone.utc).date() - timedelta(days=1)
        last = datetime.fromtimestamp(end_ms / 1000, timezone.utc).date() + timedelta(days=1)
        while d <= last:
            got = self.day(d.isoformat())
            if got is not None:
                t, b, a = got
                i, j = np.searchsorted(t, start_ms), np.searchsorted(t, end_ms, side="right")
                if j > i:
                    parts.append((t[i:j], b[i:j], a[i:j]))
            d += timedelta(days=1)
        if not parts:
            return None
        t = np.concatenate([p[0] for p in parts]); b = np.concatenate([p[1] for p in parts]); a = np.concatenate([p[2] for p in parts])
        order = np.argsort(t, kind="stable")
        return t[order], b[order], a[order]


def study_signal(sig: dict, store: TickStore) -> dict:
    pub = datetime.fromisoformat(sig["published_utc"])
    t0 = int((pub.timestamp() + MEDIAN_DELAY_S[sig["channel"]]) * 1000)
    sign = 1 if sig["direction"] == "BUY" else -1
    w = store.window(t0 - 65 * 60_000, t0 + (max(HORIZONS_MIN) + 5) * 60_000)
    row = {k: sig[k] for k in ("id", "channel", "kind", "direction", "published_utc", "forward", "source")}
    if w is None:
        return {**row, "status": "no_ticks"}
    t, bid, ask = w
    i0 = int(np.searchsorted(t, t0))
    if i0 >= len(t) or t[i0] - t0 > 60_000:
        return {**row, "status": "market_closed_at_signal"}
    entry = ask[i0] if sign == 1 else bid[i0]
    exitq = bid if sign == 1 else ask
    row.update(status="ok", entry_lag_s=round((t[i0] - t0) / 1000, 3), entry=float(entry),
               spread=round(float(ask[i0] - bid[i0]), 3), hour_utc=pub.hour, weekday=pub.weekday())
    mid = (bid + ask) / 2
    for m in (15, 60):
        k = int(np.searchsorted(t, t0 - m * 60_000))
        if k < i0 and t0 - t[k] <= (m + 5) * 60_000:
            row[f"pre_move_{m}"] = round(float(sign * (mid[i0] - mid[k])), 3)
    k60 = int(np.searchsorted(t, t0 - 60 * 60_000))
    if k60 < i0:
        row["pre_range_60"] = round(float(mid[k60:i0 + 1].max() - mid[k60:i0 + 1].min()), 3)
    for h in HORIZONS_MIN:
        j = int(np.searchsorted(t, t0 + h * 60_000, side="right"))
        seg = exitq[i0:j]; ts = t[i0:j]
        if len(seg) < 2:
            continue
        move = sign * (seg - entry)
        gap = float(np.max(np.diff(ts))) / 1000 if len(ts) > 1 else 0.0
        row[f"mfe_{h}"] = round(float(move.max()), 3)
        row[f"mae_{h}"] = round(float(-move.min()), 3)
        row[f"end_{h}"] = round(float(move[-1]), 3)
        if gap > CLOSED_GAP_S:
            row[f"closed_gap_{h}"] = round(gap)
    j = int(np.searchsorted(t, t0 + max(HORIZONS_MIN) * 60_000, side="right"))
    move = sign * (exitq[i0:j] - entry); ts = t[i0:j]
    for x in RACES:
        up = np.flatnonzero(move >= x); dn = np.flatnonzero(move <= -x)
        fu = up[0] if len(up) else None; fd = dn[0] if len(dn) else None
        if fu is None and fd is None:
            row[f"race_{x:g}"] = "none"
        elif fd is None or (fu is not None and fu < fd):
            row[f"race_{x:g}"] = "win"; row[f"race_{x:g}_min"] = round((ts[fu] - t[i0]) / 60000, 2)
        else:
            row[f"race_{x:g}"] = "loss"; row[f"race_{x:g}_min"] = round((ts[fd] - t[i0]) / 60000, 2)
    return row


def run(universe: Path, tick_root: Path, out_dir: Path):
    sigs = [json.loads(l) for l in Path(universe).read_text(encoding="utf-8").splitlines()]
    store = TickStore(tick_root)
    rows = [study_signal(s, store) for s in sorted(sigs, key=lambda s: s["published_utc"])]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "per_signal.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return rows


if __name__ == "__main__":
    import sys
    rows = run(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
    from collections import Counter
    print(Counter((r["channel"], r["status"]) for r in rows))
