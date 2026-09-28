"""Tick checks for the reconstructed Gold signals (no look-ahead: every decision
uses only quotes at or after the moment the bot received the relevant message)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np

from research.signal_study import TickStore

PIP = 0.1  # gold pip as the channel counts it (USD)


def ms(s):
    return int(datetime.fromisoformat(s).timestamp() * 1000)


def first_index(cond):
    idx = np.flatnonzero(cond)
    return int(idx[0]) if len(idx) else None


def analyse(sig, store, horizon_h=8):
    out = {"id": sig["id"], "chat": sig["chat"], "kind": sig["kind"], "direction": sig["direction"],
           "published_utc": sig["published_utc"], "high_risk": sig["high_risk"],
           "claim_tp_max": sig["claim_tp_max"], "claim_sl_hit": sig["claim_sl_hit"], "claim_pips_max": sig["claim_pips_max"]}
    t_first = ms(sig["first_seen_utc"])
    w = store.window(t_first - 60_000, t_first + horizon_h * 3_600_000)
    if w is None:
        return {**out, "status": "no_ticks"}
    t, bid, ask = w
    sign = 1 if sig["direction"] == "BUY" else -1
    entryq = ask if sign == 1 else bid  # price a follower pays
    exitq = bid if sign == 1 else ask   # price a follower closes at
    i0 = int(np.searchsorted(t, t_first))
    if i0 >= len(t) or t[i0] - t_first > 60_000:
        return {**out, "status": "market_closed"}
    out["status"] = "ok"
    out["market_at_first"] = float(entryq[i0])
    if "zone" not in sig:
        return out
    lo, hi = sig["zone"]
    near, far = (hi, lo) if sign == 1 else (lo, hi)   # near edge fills first; far edge = "best entry"
    tl = ms(sig["levels_seen_utc"])
    il = int(np.searchsorted(t, tl))
    if il >= len(t):
        return {**out, "status": "levels_after_tape"}
    out["levels_delay_s"] = round((tl - t_first) / 1000, 1)
    out["market_at_levels"] = float(entryq[il])
    # where is the market when the zone becomes known: >0 = market already better than the near edge
    out["market_vs_near_at_levels"] = round(float(sign * (near - entryq[il])), 2)
    out["move_first_to_levels"] = round(float(sign * (exitq[il] - exitq[i0])), 2)
    tps, sl = sig.get("tps") or [], sig.get("sl")
    out["tps"], out["sl"], out["zone"] = tps, sl, sig["zone"]
    seg_e, seg_x, seg_t = entryq[il:], exitq[il:], t[il:]
    # zone fills after levels are known
    f_near = first_index(sign * (seg_e - near) <= 0)
    f_far = first_index(sign * (seg_e - far) <= 0)
    out["near_filled_min"] = None if f_near is None else round((seg_t[f_near] - tl) / 60000, 2)
    out["far_filled_min"] = None if f_far is None else round((seg_t[f_far] - tl) / 60000, 2)
    # truth of the channel's claims, measured from the moment the levels were known
    def reach(level):
        k = first_index(sign * (seg_x - level) >= 0)
        return None if k is None else round((seg_t[k] - tl) / 60000, 2)
    out["tp_reached_min"] = [reach(x) for x in tps]
    out["sl_reached_min"] = None if sl is None else (lambda k: None if k is None else round((seg_t[k] - tl) / 60000, 2))(
        first_index(sign * (seg_x - sl) <= 0))
    return out


def run(signals_path, tick_root, out_path):
    store = TickStore(Path(tick_root))
    sigs = [json.loads(l) for l in open(signals_path)]
    rows = [analyse(s, store) for s in sigs]
    Path(out_path).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return rows


if __name__ == "__main__":
    import sys
    from collections import Counter
    rows = run(*sys.argv[1:4])
    print(Counter(r["status"] for r in rows))
