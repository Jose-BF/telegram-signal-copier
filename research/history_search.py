"""Population evaluation of trigger-only strategies over the historical sample.

Every signal path is built once per day and reused by every strategy and
broker scenario (the path does not depend on the strategy). canal1 keeps one
basket at a time per strategy; canal2 baskets overlap. Days are independent
(baskets are censored at the day's tape end), so days are split across
worker processes.
"""
from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
import multiprocessing
import os
from pathlib import Path

import numpy as np
import pandas as pd

from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.engine import ExecutionAssumptions
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.market_contract import MarketProfile
from research.dubai_iterative.protection_contract import ProtectionProfile
from research.history_replay import TEXT_KINDS, History, load_universe, make_trigger_path
from research.signal_filters import load_features, passes

T = Path("runtime_data/history_triggers_v1")
UNIVERSE = Path("runtime_data/signal_universe_v1/signals.jsonl")


def execution_for(cal: dict, channel: str) -> ExecutionAssumptions:
    d = cal["delays_ms"]
    extra = dict(policy_extension="basket_guard_v1", request_quote_binding="timestamp_and_ordinal") if channel == "canal1" else {}
    # optional per-channel override, e.g. {"canal2": "own_rule_be_partial_v1"} for price BE / partial runner
    override = cal.get("policy_extension", {}).get(channel)
    if override:
        extra = dict(policy_extension=override)
    return ExecutionAssumptions(
        entry_fill_latency_ms=int(d["entry_fill_latency_ms"]), quote_view_lag_ms=int(d.get("quote_view_lag_ms", 0)),
        protection=ProtectionProfile(0.01, 2, 20, 0, int(d["protection_processing_ms"]), int(d["protection_ack_ms"]),
                                     int(d["protection_retry_ms"]), **extra),
        market=MarketProfile(int(d["entry_ack_ms"]), int(d["close_processing_ms"]), int(d["close_ack_ms"]), 0.01, 1.0, 0.01))


def build_history(tick_root: str, seed: int = 0, universe: str | Path = UNIVERSE) -> History:
    triggers = load_universe(Path(universe))
    delays = json.loads((T / "tg_delay_samples_v2.json").read_text())
    return History(Path(tick_root), triggers, {"canal1": delays["canal1"], "canal2": delays["canal2"]}, seed=seed)


def mark_to_market(path, result) -> float | None:
    """Realized exits plus open legs valued at the last quote of the path (EUR)."""
    realized = sum(float(x.pnl_eur) for x in result.exits if x.pnl_eur is not None)
    closed = {}
    for x in result.exits:
        closed[x.ticket] = closed.get(x.ticket, 0.0) + x.volume
    last = len(path.times_ns) - 1
    valid = [i for i in range(last, max(last - 5000, -1), -1) if path.fx_valid[i]]
    if not valid:
        return None
    fx = (float(path.fx_bid[valid[0]]) + float(path.fx_ask[valid[0]])) / 2
    sign = 1 if path.direction == "BUY" else -1
    quote = float(path.exit_quotes[last])
    floating = 0.0
    for e in result.entries:
        open_volume = e.volume - closed.get(e.ticket, 0.0)
        if open_volume > 1e-9:
            floating += sign * (quote - e.entry_price) * open_volume * path.contract_size / fx
    return round(realized + floating, 2)


def basket_curve(path, result, step_ms=60_000):
    """Minute-by-minute equity of one basket (EUR): realized exits so far plus open legs
    valued at the exit quote. Returns (minute_start_ms, min_equity_in_minute, last_equity)."""
    if not result.entries:
        return None
    t = path.times_ns // 1_000_000
    sign = 1 if path.direction == "BUY" else -1
    xq = np.asarray(path.exit_quotes, dtype=float)
    fx = (np.asarray(path.fx_bid, dtype=float) + np.asarray(path.fx_ask, dtype=float)) / 2
    fx = pd.Series(fx).ffill().bfill().to_numpy()
    i0 = min(e.tick_index for e in result.entries)
    i1 = max([x.tick_index for x in result.exits] + [i0]) if result.exits else len(t) - 1
    if len(result.exits) < len(result.entries):
        i1 = len(t) - 1
    idx = np.arange(i0, i1 + 1)
    eq = np.zeros(len(idx))
    closed = {}
    for x in result.exits:
        closed.setdefault(x.ticket, []).append(x)
    for e in result.entries:
        exits = sorted(closed.get(e.ticket, []), key=lambda x: x.tick_index)
        open_from = e.tick_index
        vol_open = e.volume
        for x in exits:
            seg = (idx >= open_from) & (idx < x.tick_index)
            eq[seg] += sign * (xq[idx[seg]] - e.entry_price) * vol_open * path.contract_size / fx[idx[seg]]
            eq[idx >= x.tick_index] += float(x.pnl_eur) if x.pnl_eur is not None else 0.0
            vol_open -= x.volume
            open_from = x.tick_index
        if vol_open > 1e-9:
            seg = idx >= open_from
            eq[seg] += sign * (xq[idx[seg]] - e.entry_price) * vol_open * path.contract_size / fx[idx[seg]]
    minute = (t[idx] // step_ms) * step_ms
    df = pd.DataFrame({"m": minute, "eq": eq})
    g = df.groupby("m")["eq"]
    return g.min().index.to_numpy(np.int64), g.min().to_numpy(), g.last().to_numpy()


def _worker(job):
    tick_root, seed, days, pops, cals, universe = job[:6]
    opts = job[6] if len(job) > 6 else {}
    filters = opts.get("filters")          # {ch: [filter spec per genome]} or None
    feats = load_features(opts["features"]) if opts.get("features") else None
    want_curves = opts.get("curves", False)
    pops = {ch: [StrategyGenome.from_dict(g) for g in gs] for ch, gs in pops.items()}
    evaluators = {sc: {ch: FastEvaluator(execution=execution_for(cal, ch)) for ch in pops} for sc, cal in cals.items()}
    hist = build_history(tick_root, seed, universe)
    out = []  # (scenario, channel, genome_index, day, trigger_id, pnl, dd, censored)
    wanted = set(days)
    for day, market, conversion, items in hist.days(only=wanted):
        items = sorted(items, key=lambda x: x[1].observed_at)
        paths = {}
        for trig, sig, tape_end in items:
            if sig.channel in pops:
                try:
                    paths[trig.trigger_id] = make_trigger_path(sig, pops[sig.channel][0], market, conversion, tape_end)
                except Exception:  # noqa: BLE001
                    paths[trig.trigger_id] = None
        for sc, evs in evaluators.items():
            for ch, genomes in pops.items():
                for gi, genome in enumerate(genomes):
                    open_until = []
                    for trig, sig, _ in items:
                        if sig.channel != ch or paths.get(trig.trigger_id) is None:
                            continue
                        if filters is not None and not passes(filters[ch][gi], feats.get(trig.trigger_id)):
                            continue
                        if ch == "canal1" and trig.kind in TEXT_KINDS and any(u > sig.observed_at for u in open_until):
                            continue
                        path, cutoff = paths[trig.trigger_id]
                        r = evs[ch](path, genome)
                        if not r.filled_volume:
                            continue
                        closed = max((x.closed_at for x in r.exits), default=None)
                        if ch == "canal1":
                            open_until.append(closed or cutoff)
                        censored = r.pnl_eur is None or closed is None or closed >= cutoff
                        pnl = float(r.pnl_eur) if r.pnl_eur is not None else mark_to_market(path, r)
                        row = (sc, ch, gi, day, trig.trigger_id, pnl,
                               None if r.max_floating_drawdown_eur is None else float(r.max_floating_drawdown_eur),
                               bool(censored))
                        if want_curves:
                            c = basket_curve(path, r)
                            row = row + (None if c is None else [c[0].tolist(), np.round(c[1], 2).tolist(), np.round(c[2], 2).tolist()],)
                        out.append(row)
        for evs in evaluators.values():
            for ev in evs.values():
                ev.clear_cache()
    return out


def evaluate(tick_root, days, pops, cals, workers=2, seed=0, universe=UNIVERSE, filters=None, features=None, curves=False):
    days = sorted(days)
    chunks = [days[i::workers] for i in range(workers)]
    payload = {ch: [g.to_dict() for g in gs] for ch, gs in pops.items()}
    opts = {"filters": filters, "features": features, "curves": curves}
    jobs = [(tick_root, seed, c, payload, cals, str(universe), opts) for c in chunks if c]
    rows = []
    # HS_START=spawn reproduces Windows behaviour on Linux (tests); default = platform default.
    ctx = multiprocessing.get_context(os.environ.get('HS_START') or None)
    with ProcessPoolExecutor(max_workers=len(jobs), mp_context=ctx) as pool:
        for part in pool.map(_worker, jobs):
            rows += part
    return rows


def metrics(rows, n_genomes: dict):
    """Per (scenario, channel, genome) summary."""
    acc = defaultdict(lambda: {"n": 0, "net": 0.0, "gross_win": 0.0, "gross_loss": 0.0, "worst": 0.0,
                               "dd_sum": 0.0, "dd_max": 0.0, "censored": 0, "month": defaultdict(float),
                               "week": defaultdict(float)})
    from datetime import date
    for sc, ch, gi, day, _tid, pnl, dd, cens in rows:
        a = acc[(sc, ch, gi)]
        a["n"] += 1
        if cens:
            a["censored"] += 1  # valued at the day's last quote (mark-to-market)
        if pnl is None:
            continue
        a["net"] += pnl
        a["gross_win" if pnl > 0 else "gross_loss"] += abs(pnl)
        a["worst"] = min(a["worst"], pnl)
        a["dd_sum"] += dd or 0.0
        a["dd_max"] = max(a["dd_max"], dd or 0.0)
        a["month"][day[:7]] += pnl
        y, w, _ = date.fromisoformat(day).isocalendar()
        a["week"][f"{y}-W{w:02d}"] += pnl
    out = {}
    for key, a in acc.items():
        months = list(a["month"].values()); weeks = list(a["week"].values())
        out[key] = {"n": a["n"], "net": round(a["net"], 2), "censored": a["censored"],
                    "pf": round(a["gross_win"] / a["gross_loss"], 3) if a["gross_loss"] else math.inf,
                    "worst_basket": round(a["worst"], 2), "dd_max": round(a["dd_max"], 2),
                    "avg_dd": round(a["dd_sum"] / max(a["n"], 1), 2),
                    "pos_months": sum(m > 0 for m in months), "months": len(months),
                    "worst_month": round(min(months), 2) if months else 0.0,
                    "worst_week": round(min(weeks), 2) if weeks else 0.0}
    return out
