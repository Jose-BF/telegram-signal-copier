"""Follower variants on the reconstructed Gold signals, one position of 1 oz (0.01 lot).

No look-ahead: the entry order is placed when the zone is received (first version with
a range, bot receipt time); the SL and TP_k are only active from the moment the version
that first contains them was received. Order valid `valid_min`. Exit at TP_k or SL,
whichever the quotes touch first once active; otherwise closed at the horizon.
Result in USD per ounce, spread included (entry at ask/bid, exit at bid/ask).
Typos are skipped using only the quote at each version's time (see levels()).
"""
from __future__ import annotations

import numpy as np

from research.gold_channel_ticks import first_index, ms


MAX_EDIT_MIN = 15      # versions published later than this after the signal are ignored
MAX_ZONE_DIST = 15.0   # a zone further than this from the market when published is a typo
MAX_LEVEL_DIST = 40.0  # SL/TP further than this from the zone are typos


def levels(sig, t, bid, ask):
    """First credible zone, SL and TP_k as known at each version's receipt time.

    The channel fixes typos by editing (e.g. zone 4132-4137 with price at 4031,
    corrected to 4030-4037 30 s later); an implausible version is skipped, which
    only uses information available at that moment (the current quote).
    """
    tl = sig.get("timeline") or []
    t_pub = ms(sig["published_utc"])
    sign = 1 if sig["direction"] == "BUY" else -1
    zone = sl = None
    tps = {}
    for v in tl:
        tv = ms(v["ts"])
        if tv - t_pub > MAX_EDIT_MIN * 60_000:
            break
        i = min(int(np.searchsorted(t, tv)), len(t) - 1)
        mid = (bid[i] + ask[i]) / 2
        lo, hi = v["zone"]
        if zone is None:
            if abs((lo + hi) / 2 - mid) > MAX_ZONE_DIST:
                continue
            zone = (lo, hi, v["ts"])
        zlo, zhi = zone[0], zone[1]
        edge_sl = zlo if sign == 1 else zhi
        edge_tp = zhi if sign == 1 else zlo
        if sl is None and v["sl"] is not None:
            d = sign * (edge_sl - v["sl"])
            if 0 < d <= MAX_LEVEL_DIST:
                sl = (v["sl"], v["ts"])
        for k, x in enumerate(v["tps"], 1):
            d = sign * (x - edge_tp)
            if k not in tps and 0 < d <= MAX_LEVEL_DIST:
                tps[k] = (x, v["ts"])
    if zone is None:
        return None
    return {"zone": [zone[0], zone[1]], "zone_ts": zone[2], "sl": sl, "tps": tps}


def follow(sig, store, entry="near", tp_k=1, valid_min=60, horizon_h=8, use_sl=True):
    if not sig.get("timeline"):
        return None
    t_pub = ms(sig["published_utc"])
    w = store.window(t_pub - 60_000, t_pub + horizon_h * 3_600_000)
    if w is None:
        return None
    t, bid, ask = w
    L = levels(sig, t, bid, ask)
    if L is None or (use_sl and L["sl"] is None) or tp_k not in L["tps"]:
        return None
    tz = ms(L["zone_ts"])
    sign = 1 if sig["direction"] == "BUY" else -1
    eq, xq = (ask, bid) if sign == 1 else (bid, ask)
    iz = int(np.searchsorted(t, tz))
    if iz >= len(t):
        return None
    lo, hi = L["zone"]
    near, far = (hi, lo) if sign == 1 else (lo, hi)
    if entry == "market":
        fi, price = iz, float(eq[iz])
    else:
        level = {"near": near, "mid": (lo + hi) / 2, "far": far}[entry]
        end = int(np.searchsorted(t, tz + valid_min * 60_000))
        k = first_index(sign * (eq[iz:end] - level) <= 0)
        if k is None:
            return {"filled": False}
        fi = iz + k
        price = min(level, float(eq[fi])) if sign == 1 else max(level, float(eq[fi]))
    tp, tp_ts = L["tps"][tp_k]
    i_tp = max(fi, int(np.searchsorted(t, ms(tp_ts))))
    k_tp = first_index(sign * (xq[i_tp:] - tp) >= 0)
    k_tp = None if k_tp is None else i_tp + k_tp
    k_sl = None
    if use_sl:
        sl, sl_ts = L["sl"]
        i_sl = max(fi, int(np.searchsorted(t, ms(sl_ts))))
        k = first_index(sign * (xq[i_sl:] - sl) <= 0)
        k_sl = None if k is None else i_sl + k
    if k_tp is not None and (k_sl is None or k_tp < k_sl):
        return {"filled": True, "exit": "tp", "pnl": round(sign * (tp - price), 2), "min": round((t[k_tp] - t[fi]) / 60000, 1)}
    if k_sl is not None:
        return {"filled": True, "exit": "sl", "pnl": round(float(sign * (xq[k_sl] - price)), 2), "min": round((t[k_sl] - t[fi]) / 60000, 1)}
    return {"filled": True, "exit": "time", "pnl": round(float(sign * (xq[-1] - price)), 2), "min": None}


def table(sigs, store, **kw):
    res = [(s, follow(s, store, **kw)) for s in sigs]
    res = [(s, r) for s, r in res if r is not None]
    filled = [r for _, r in res if r["filled"]]
    n, f = len(res), len(filled)
    pnl = [r["pnl"] for r in filled]
    return {"signals": n, "filled%": round(100 * f / n, 1) if n else None,
            "tp%": round(100 * sum(r["exit"] == "tp" for r in filled) / f, 1) if f else None,
            "sl%": round(100 * sum(r["exit"] == "sl" for r in filled) / f, 1) if f else None,
            "time%": round(100 * sum(r["exit"] == "time" for r in filled) / f, 1) if f else None,
            "net_usd_oz": round(sum(pnl), 1), "avg": round(sum(pnl) / f, 2) if f else None,
            "worst": min(pnl) if pnl else None}


def follow_channel_style(sig, store, entry="market", n_tps=4, be_after_tp1=True, valid_min=60, horizon_h=8):
    """Split 1 oz into n equal parts closed at TP1..TPn (as published); SL as published;
    after TP1 the remaining parts move their SL to the entry price (channel's 'risk free')."""
    t_pub = ms(sig["published_utc"])
    w = store.window(t_pub - 60_000, t_pub + horizon_h * 3_600_000)
    if w is None:
        return None
    t, bid, ask = w
    L = levels(sig, t, bid, ask)
    if L is None or L["sl"] is None or 1 not in L["tps"]:
        return None
    ks = [k for k in range(1, n_tps + 1) if k in L["tps"]]
    sign = 1 if sig["direction"] == "BUY" else -1
    eq, xq = (ask, bid) if sign == 1 else (bid, ask)
    tz = ms(L["zone_ts"]); iz = int(np.searchsorted(t, tz))
    if iz >= len(t):
        return None
    lo, hi = L["zone"]; near = hi if sign == 1 else lo
    if entry == "market":
        fi, price = iz, float(eq[iz])
    else:
        end = int(np.searchsorted(t, tz + valid_min * 60_000))
        k = first_index(sign * (eq[iz:end] - near) <= 0)
        if k is None:
            return {"filled": False}
        fi = iz + k; price = min(near, float(eq[fi])) if sign == 1 else max(near, float(eq[fi]))
    part = 1.0 / len(ks)
    sl, sl_ts = L["sl"]; i_sl = max(fi, int(np.searchsorted(t, ms(sl_ts))))
    pnl, open_parts, stop, stop_from = 0.0, list(ks), sl, i_sl
    i = fi
    while open_parts:
        k = open_parts[0]
        tp, tp_ts = L["tps"][k]; i_tp = max(i, int(np.searchsorted(t, ms(tp_ts))))
        a = first_index(sign * (xq[i_tp:] - tp) >= 0); a = None if a is None else i_tp + a
        s0 = max(i, stop_from)
        b = first_index(sign * (xq[s0:] - stop) <= 0); b = None if b is None else s0 + b
        if a is not None and (b is None or a < b):
            pnl += part * sign * (tp - price); open_parts.pop(0); i = a
            if be_after_tp1 and k == ks[0]:
                stop, stop_from = price, a
        elif b is not None:
            pnl += part * len(open_parts) * float(sign * (xq[b] - price)); open_parts = []
        else:
            pnl += part * len(open_parts) * float(sign * (xq[-1] - price)); open_parts = []
    return {"filled": True, "pnl": round(pnl, 2)}
