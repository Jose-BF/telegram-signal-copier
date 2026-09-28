"""Per-signal price paths after the moment the bot sees a signal (search 2, milestone M1).

For every trigger of a universe (real or placebo) the path starts at the market entry tick:
first quote at or after observed_at + entry latency (broker C_p50 by default), BUY at the
ask, SELL at the bid. It runs until min(tape end of the broker day, observed_at + 6 h), the
same cutoff as research/history_replay.make_trigger_path.

Stored products (compact, the 1 s paths themselves are rebuilt in memory on demand because
the disk is small):
  - first-passage tables: for X in LEVELS ($/oz), the first second at which a position opened
    at market reaches +X (favourable) or -X (adverse) at its real exit price (bid for a BUY,
    ask for a SELL); -1 = not reached before the cutoff. Both directions (0 = BUY, 1 = SELL),
    so the same table serves "follow" and "invert".
  - value at each time exit (TIME_EXITS_MIN), and the running best/worst (MFE/MAE) there.
Values are $/oz relative to the entry price; research/bracket_grid.py converts to EUR with the
EURUSD mid at entry (`fx`): 1 $/oz on 1 lot = 100 / fx EUR.
"""
from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import numpy as np

from research.history_replay import MAX_BASKET_H, load_universe

LEVELS = np.round(np.arange(0.25, 60.0001, 0.25), 2)
TIME_EXITS_MIN = (5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 360)
HORIZON_S = MAX_BASKET_H * 3600
NS = 1_000_000_000


@dataclass
class SecondPath:
    """1 s buckets after the entry tick. hi/lo are NaN in seconds without quotes; *_last carry
    the last known quote forward. Prices are relative: bid/ask minus the entry reference."""
    entry_ns: int
    ask0: float
    bid0: float
    bid_hi: np.ndarray
    bid_lo: np.ndarray
    ask_hi: np.ndarray
    ask_lo: np.ndarray
    bid_last: np.ndarray
    ask_last: np.ndarray

    @property
    def n(self) -> int:
        return len(self.bid_last)


def second_path(times_ns, bid, ask, t0_ns: int, cutoff_ns: int, latency_ms: int) -> SecondPath | None:
    """Entry at the first quote with time >= t0 + latency; buckets cover (entry, cutoff]."""
    start = np.searchsorted(times_ns, t0_ns + latency_ms * 1_000_000, side="left")
    if start >= len(times_ns) or times_ns[start] >= cutoff_ns:
        return None
    te = int(times_ns[start])
    ask0, bid0 = float(ask[start]), float(bid[start])
    end = np.searchsorted(times_ns, cutoff_ns, side="right")
    n = int(min((cutoff_ns - te) // NS, HORIZON_S))
    if n < 1:
        return None
    t = times_ns[start + 1:end]
    b = bid[start + 1:end]
    a = ask[start + 1:end]
    k = ((t - te) // NS).astype(np.int64)
    keep = k < n
    k, b, a = k[keep], b[keep], a[keep]
    out = {name: np.full(n, np.nan) for name in ("bid_hi", "bid_lo", "ask_hi", "ask_lo", "bid_last", "ask_last")}
    if len(k):
        starts = np.flatnonzero(np.r_[True, np.diff(k) > 0])
        secs = k[starts]
        ends = np.r_[starts[1:], len(k)] - 1
        out["bid_hi"][secs] = np.maximum.reduceat(b, starts)
        out["bid_lo"][secs] = np.minimum.reduceat(b, starts)
        out["ask_hi"][secs] = np.maximum.reduceat(a, starts)
        out["ask_lo"][secs] = np.minimum.reduceat(a, starts)
        out["bid_last"][secs] = b[ends]
        out["ask_last"][secs] = a[ends]
    for name, first in (("bid_last", bid0), ("ask_last", ask0)):
        v = out[name]
        if np.isnan(v[0]):
            v[0] = first
        idx = np.where(~np.isnan(v), np.arange(n), 0)
        np.maximum.accumulate(idx, out=idx)
        out[name] = v[idx]
    ref = {"bid_hi": ask0, "bid_lo": ask0, "bid_last": ask0, "ask_hi": bid0, "ask_lo": bid0, "ask_last": bid0}
    # store bid relative to ask0 (BUY view) and ask relative to bid0 (SELL view)
    # gold quotes have 2 decimals: round so an exact touch (103.20 - 100.20) is 3.00, not 2.9999
    rel = {name: np.round(out[name] - ref[name], 2) for name in out}
    return SecondPath(te, ask0, bid0, **rel)


def _first_reach(running: np.ndarray, levels: np.ndarray) -> np.ndarray:
    """running is non-decreasing (NaN-free); first index with running >= level, -1 if never."""
    idx = np.searchsorted(running, levels, side="left")
    return np.where(idx < len(running), idx, -1).astype(np.int32)


def _running(values: np.ndarray, fn) -> np.ndarray:
    v = np.where(np.isnan(values), -np.inf, values)
    return fn.accumulate(v)


def products(p: SecondPath) -> dict:
    """First-passage tables, time-exit values and MFE/MAE for BUY (0) and SELL (1) at market."""
    # BUY: gain = bid - ask0 (stored as bid_*); SELL: gain = bid0 - ask (stored ask_* = ask - bid0)
    fav_run = [_running(p.bid_hi, np.maximum), _running(-p.ask_lo, np.maximum)]
    adv_run = [_running(-p.bid_lo, np.maximum), _running(p.ask_hi, np.maximum)]
    last = [p.bid_last, -p.ask_last]
    fp_fav = np.stack([_first_reach(r, LEVELS) for r in fav_run])
    fp_adv = np.stack([_first_reach(r, LEVELS) for r in adv_run])
    T = len(TIME_EXITS_MIN)
    exit_val = np.full((2, T), np.nan, dtype=np.float32)
    mfe = np.full((2, T), np.nan, dtype=np.float32)
    mae = np.full((2, T), np.nan, dtype=np.float32)
    for j, m in enumerate(TIME_EXITS_MIN):
        s = m * 60 - 1
        if s >= p.n:
            continue
        for d in (0, 1):
            exit_val[d, j] = last[d][s]
            mfe[d, j] = max(fav_run[d][s], 0.0)
            mae[d, j] = -max(adv_run[d][s], 0.0)
    return {"fp_fav": fp_fav, "fp_adv": fp_adv, "exit_val": exit_val, "mfe": mfe, "mae": mae,
            "end_val": np.array([last[0][-1], last[1][-1]], dtype=np.float32)}


def _fx_mid(conversion, at_ns: int, max_age_ms: int = 30_000) -> float | None:
    t, b, a = conversion
    i = np.searchsorted(t, at_ns, side="right") - 1
    if i < 0 or at_ns - t[i] > max_age_ms * 1_000_000:
        return None
    return float((b[i] + a[i]) / 2)


def iter_paths(tick_root, universe, days=None, seed=0, latency_ms=789):
    """Yield (trigger, signal, SecondPath|None, fx|None, reason) for the given broker days."""
    from research.history_search import build_history
    hist = build_history(str(tick_root), seed, universe)
    for day, market, conversion, items in hist.days(only=None if days is None else set(days)):
        times, bid, ask = market
        for trig, sig, tape_end in items:
            t0 = int(sig.observed_at.timestamp() * NS)
            cutoff = min(tape_end, sig.observed_at + timedelta(hours=MAX_BASKET_H))
            p = second_path(times, bid, ask, t0, int(cutoff.timestamp() * NS), latency_ms)
            if p is None:
                yield trig, sig, None, None, "no_quote_before_cutoff"; continue
            fx = _fx_mid(conversion, p.entry_ns)
            if fx is None:
                yield trig, sig, p, None, "no_fresh_eurusd"; continue
            yield trig, sig, p, fx, None


def _worker(job):
    tick_root, universe, days, seed, latency_ms = job
    rows, excluded = [], []
    for trig, sig, p, fx, reason in iter_paths(tick_root, universe, days, seed, latency_ms):
        if reason:
            excluded.append((trig.trigger_id, reason)); continue
        pr = products(p)
        rows.append((trig.trigger_id, trig.channel, 0 if trig.direction == "BUY" else 1,
                     sig.observed_at.isoformat(), p.entry_ns, p.n, p.ask0 - p.bid0, fx, pr))
    return rows, excluded


def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build(tick_root, universe, out_dir, name, workers=4, seed=0, latency_ms=789):
    """Compute the products of every trigger of `universe`; write <out_dir>/<name>.npz + .json."""
    tick_root, universe, out_dir = Path(tick_root), Path(universe), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    days = sorted(p.stem for p in (tick_root / "XAUUSD").glob("*.parquet"))
    chunks = [days[i::workers] for i in range(workers)]
    ctx = multiprocessing.get_context(os.environ.get("HS_START") or None)
    rows, excluded = [], []
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
        for r, e in pool.map(_worker, [(str(tick_root), str(universe), c, seed, latency_ms) for c in chunks]):
            rows += r; excluded += e
    rows.sort(key=lambda r: (r[3], r[0]))
    seen = {r[0] for r in rows} | {e[0] for e in excluded}
    for t in load_universe(universe):  # triggers History never yielded: day without tape, or seen after tape end
        if t.trigger_id not in seen:
            excluded.append((t.trigger_id, "no_tape_or_after_tape_end"))
    arr = lambda k: np.stack([r[8][k] for r in rows])
    np.savez_compressed(
        out_dir / f"{name}.npz",
        ids=np.array([r[0] for r in rows]), channel=np.array([r[1] for r in rows]),
        direction=np.array([r[2] for r in rows], dtype=np.int8), observed=np.array([r[3] for r in rows]),
        entry_ns=np.array([r[4] for r in rows], dtype=np.int64), n_seconds=np.array([r[5] for r in rows], dtype=np.int32),
        spread0=np.array([r[6] for r in rows], dtype=np.float32), fx=np.array([r[7] for r in rows]),
        fp_fav=arr("fp_fav"), fp_adv=arr("fp_adv"), exit_val=arr("exit_val"), mfe=arr("mfe"), mae=arr("mae"),
        end_val=arr("end_val"), levels=LEVELS, time_exits_min=np.array(TIME_EXITS_MIN))
    total = len(load_universe(universe))
    meta = {"contract": "signal_paths_v1", "universe": str(universe).replace("\\", "/"), "universe_sha256": _sha(universe),
            "seed": seed, "entry_latency_ms": latency_ms, "horizon_s": HORIZON_S, "triggers": total,
            "with_path": len(rows), "excluded": len(excluded),
            "excluded_by_reason": {k: sum(1 for _, r in excluded if r == k) for k in sorted({r for _, r in excluded})},
            "excluded_ids": sorted(excluded), "npz_sha256": _sha(out_dir / f"{name}.npz")}
    if meta["with_path"] + meta["excluded"] != total:
        raise RuntimeError(f"count mismatch: {meta['with_path']} + {meta['excluded']} != {total}")
    (out_dir / f"{name}.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return meta


def load(path) -> dict:
    with np.load(path, allow_pickle=False) as z:
        return {k: z[k] for k in z.files}
