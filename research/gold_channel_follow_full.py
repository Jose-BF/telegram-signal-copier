"""Follow a Gold (canal2) signal exactly as the channel instructs, in time order.

Position: 3 limit entries in the zone (near edge, middle, far edge; placed when the
zone is received, valid `valid_min`), each split in equal parts closed at TP1..TPn.
Levels: the latest credible published version at each moment (typos skipped using
the quote at that moment), including edits during the trade. Management replies to
the signal are applied when the bot receives them (+`react_ms`):
  move/adjust/extend SL to X (abbreviations like "75" resolved to the nearest price),
  reply carrying new "TP x / SL y", risk free / SL to BE (SL -> each entry's price,
  pending entries cancelled), close partials (close `partial` of open volume, the
  entries furthest from the TPs first), close overall (close everything),
  "close overall OR risk free" resolved by `either_choice` ("close" | "be").
Exits: TP at its level; SL and closes at the current quote. Spread included.
Result in USD for 1 oz in total (0.01 lot spread over all parts).
"""
from __future__ import annotations

import re

import numpy as np

from parser import parse_canal2
from research.gold_channel_follow import MAX_EDIT_MIN, MAX_LEVEL_DIST, MAX_ZONE_DIST, levels
from research.gold_channel_ticks import ms

NUM = r"(\d{2,4}(?:\.\d+)?)"


def resolve_level(raw: float, price: float) -> float:
    if raw >= 1000:
        return raw
    mod = 10 ** len(str(int(raw)))
    base = int(price // mod) * mod
    cands = [base - mod + raw, base + raw, base + mod + raw]
    return min(cands, key=lambda c: abs(c - price))


def parse_mgmt(text: str) -> list[dict]:
    t = (text or "").lower()
    acts = []
    m = re.search(r"(?:move|adjust|extend|set|put)\s*(?:my\s*)?(?:the\s*)?sl\s*(?:to\s*)?" + NUM, t) or \
        re.search(r"sl\s*(?:to|at|@)\s*" + NUM, t)
    if m:
        acts.append({"type": "sl", "raw": float(m.group(1))})
    elif re.search(r"sl\s*to\s*(?:be|breakeven|entry)|set\s*be\b|risk\s*free|breakeven|break even", t):
        pass  # handled below with the either/or logic
    layered = re.search(r"(?:from|on)\s*(?:my\s*|the\s*)?(?:top|first|higher|highest|lower|lowest|upper)\s*(?:layers?|entries|entry)", t)
    if layered and re.search(r"clos", t):
        acts.append({"type": "partial"})
    elif re.search(r"close\s*(?:overall|all|everything|the trade)|i am closing (?:my |the )?(?:trade|overall|all)", t):
        if re.search(r"\bor\b.*risk\s*free|risk\s*free.*\bor\b", t):
            acts.append({"type": "either"})
        else:
            acts.append({"type": "close"})
    elif re.search(r"close\s*(?:partial|partials)|closing\s*(?:partial|partials|my first entries)|take\s*partial", t):
        acts.append({"type": "partial"})
    if not any(a["type"] == "either" for a in acts) and re.search(
            r"risk\s*free|sl\s*to\s*(?:be|breakeven|entry)|set\s*be\b|\bbe\b.*close partials|close partials.*\bbe\b", t):
        if not re.search(r"be ready to|get ready to|if you haven", t):
            acts.append({"type": "be"})
    p = parse_canal2(text or "")
    if p.get("sl") is not None and not m:
        acts.append({"type": "sl", "raw": float(p["sl"])})
    if p.get("tps"):
        acts.append({"type": "tps", "tps": p["tps"]})
    return acts


def simulate(sig, store, valid_min=60, horizon_h=8, react_ms=1500, either_choice="be", partial=0.5,
             legs=("near", "mid", "far"), n_tps=4, trace=None, first_leg_market=True, cancel_after_tp1=True):
    t_pub = ms(sig["published_utc"])
    w = store.window(t_pub - 60_000, t_pub + horizon_h * 3_600_000)
    if w is None:
        return None
    t, bid, ask = w
    L = levels(sig, t, bid, ask)
    if L is None or L["sl"] is None or 1 not in L["tps"]:
        return None
    sign = 1 if sig["direction"] == "BUY" else -1
    eq, xq = (ask, bid) if sign == 1 else (bid, ask)
    lo, hi = L["zone"]
    near, far = (hi, lo) if sign == 1 else (lo, hi)
    leg_px = {"near": near, "mid": (lo + hi) / 2, "far": far}
    tz = ms(L["zone_ts"])
    # ---- events: level versions (credible, any time until horizon) and management replies
    events = []
    for v in sig.get("timeline") or []:
        tv = ms(v["ts"])
        if tv < tz:
            continue
        events.append((tv, "version", v))
    for r in sig.get("replies", []):
        tr = ms(r["ts"]) + react_ms
        for a in parse_mgmt(r["text"]):
            events.append((tr, a["type"], a))
    events.sort(key=lambda e: e[0])
    # ---- state
    sl, sl_known = L["sl"][0], ms(L["sl"][1])
    tps = {k: L["tps"][k][0] for k in L["tps"]}
    tp_known = {k: ms(L["tps"][k][1]) for k in L["tps"]}
    ks = sorted(tps)[:n_tps]
    part = 1.0 / (len(legs) * len(ks))
    if first_leg_market and sig["kind"] == "now":
        legs = ("market",) + tuple(legs[1:])
    units = [{"leg": g, "tp": k, "state": "pending", "entry": None, "stop": None} for g in legs for k in ks]
    if first_leg_market and sig["kind"] == "now":
        i_m = int(np.searchsorted(t, ms(sig["first_seen_utc"]) + react_ms))
        for u in units:
            if u["leg"] == "market":
                u["state"], u["entry"] = "open", float(eq[i_m])
    tp1_hit = [False]
    pnl, log = 0.0, []
    end_ms = min(tz + horizon_h * 3_600_000, int(t[-1]))
    pending_until = tz + valid_min * 60_000
    now_i = int(np.searchsorted(t, tz))
    cur_t = tz
    ev_i = 0

    def note(ti, what):
        if trace is not None:
            trace.append((str(np.datetime64(int(t[min(ti, len(t) - 1)]), "ms"))[11:23], what))

    def arm_be(u, i):
        # a broker only accepts SL = entry when the price is already beyond it
        if sign * (xq[i] - u["entry"]) > 0.05:
            u["stop"] = u["entry"]
        else:
            u["be_wait"] = True

    def close_unit(u, px, why):
        nonlocal pnl
        pnl += part * sign * (px - u["entry"])
        u["state"] = why

    while True:
        next_ev = events[ev_i][0] if ev_i < len(events) else end_ms
        seg_end = min(next_ev, end_ms)
        j = int(np.searchsorted(t, seg_end))
        # process ticks in [now_i, j): find earliest trigger among units
        while now_i < j:
            best = None
            seg_e, seg_x, seg_t = eq[now_i:j], xq[now_i:j], t[now_i:j]
            for u in units:
                if u["state"] == "pending" and not (cancel_after_tp1 and tp1_hit[0]):
                    lim_end = int(np.searchsorted(seg_t, pending_until))
                    idx = np.flatnonzero(sign * (seg_e[:lim_end] - leg_px[u["leg"]]) <= 0)
                    if len(idx):
                        cand = (idx[0], "fill", u)
                        best = cand if best is None or cand[0] < best[0] else best
                elif u["state"] == "open":
                    tpk = tps.get(u["tp"])
                    if tpk is not None:
                        s0 = max(0, int(np.searchsorted(seg_t, tp_known.get(u["tp"], 0))))
                        idx = np.flatnonzero(sign * (seg_x[s0:] - tpk) >= 0)
                        if len(idx):
                            cand = (s0 + idx[0], "tp", u)
                            best = cand if best is None or cand[0] < best[0] else best
                    if u.get("be_wait"):
                        idx = np.flatnonzero(sign * (seg_x - u["entry"]) > 0.05)
                        if len(idx):
                            cand = (idx[0], "arm", u)
                            best = cand if best is None or cand[0] < best[0] else best
                    stop = u["stop"] if u["stop"] is not None else sl
                    s0 = max(0, int(np.searchsorted(seg_t, sl_known)))
                    idx = np.flatnonzero(sign * (seg_x[s0:] - stop) <= 0)
                    if len(idx):
                        cand = (s0 + idx[0], "sl", u)
                        best = cand if best is None or cand[0] < best[0] else best
            if best is None:
                now_i = j
                break
            k, kind, u = best
            gi = now_i + k
            if kind == "fill":
                u["state"], u["entry"] = "open", min(leg_px[u["leg"]], float(eq[gi])) if sign == 1 else max(leg_px[u["leg"]], float(eq[gi]))
                note(gi, f"fill {u['leg']}/TP{u['tp']} @ {u['entry']:.2f}")
            elif kind == "arm":
                u["stop"], u["be_wait"] = u["entry"], False
                note(gi, f"BE armed {u['leg']}/TP{u['tp']} at {u['entry']:.2f}")
            elif kind == "tp":
                if u["tp"] == min(tps):
                    tp1_hit[0] = True
                close_unit(u, tps[u["tp"]], "tp"); note(gi, f"TP{u['tp']} {u['leg']} @ {tps[u['tp']]:.2f}")
            else:
                close_unit(u, float(xq[gi]), "sl" if u["stop"] is None else "be")
                note(gi, f"{'SL' if u['stop'] is None else 'BE'} {u['leg']}/TP{u['tp']} @ {float(xq[gi]):.2f} (stop {u['stop'] if u['stop'] is not None else sl})")
            now_i = gi  # re-scan from this tick: other units may trigger on the same quote
        if seg_end >= end_ms or ev_i >= len(events):
            break
        # apply event
        te, typ, payload = events[ev_i]; ev_i += 1
        i = min(int(np.searchsorted(t, te)), len(t) - 1)
        px_mid = (bid[i] + ask[i]) / 2
        if typ == "version":
            v = payload
            # edits during the trade (e.g. "TPs edited"): accept credible levels only
            edge_sl, edge_tp = (lo, hi) if sign == 1 else (hi, lo)
            if v["sl"] is not None and 0 < sign * (edge_sl - v["sl"]) <= MAX_LEVEL_DIST:
                sl = v["sl"]; sl_known = min(sl_known, te)
            for kk, x in enumerate(v["tps"], 1):
                if kk in tps and 0 < sign * (x - edge_tp) <= MAX_LEVEL_DIST:
                    tps[kk] = x
        elif typ == "sl":
            lvl = resolve_level(payload["raw"], px_mid)
            if abs(lvl - px_mid) <= MAX_LEVEL_DIST:
                sl = lvl; sl_known = min(sl_known, te)
                for u in units:
                    if u["state"] == "open":
                        u["stop"] = None; u["be_wait"] = False
        elif typ == "tps":
            for kk, x in enumerate(payload["tps"], 1):
                lvl = resolve_level(x, px_mid)
                if abs(lvl - px_mid) <= MAX_LEVEL_DIST and kk in tps:
                    tps[kk] = lvl
        elif typ in ("be", "either", "close", "partial"):
            action = typ if typ != "either" else either_choice
            for u in units:
                if u["state"] == "pending":
                    u["state"] = "cancelled"
            opened = [u for u in units if u["state"] == "open"]
            if action == "close":
                for u in opened:
                    close_unit(u, float(xq[i]), "close")
            elif action == "partial":
                opened.sort(key=lambda u: -sign * u["entry"])  # worst entries first
                for u in opened[:int(round(len(opened) * partial))]:
                    close_unit(u, float(xq[i]), "partial")
                for u in opened[int(round(len(opened) * partial)):]:
                    arm_be(u, i)
            elif action == "be":
                for u in opened:
                    arm_be(u, i)
        log.append((te, typ)); note(i, f"MSG {typ} {payload.get('raw', '') if isinstance(payload, dict) else ''} sl_now={sl} tps={[tps[k] for k in sorted(tps)][:4]}")
        now_i = max(now_i, int(np.searchsorted(t, te)))
    last = len(t) - 1 if end_ms >= int(t[-1]) else int(np.searchsorted(t, end_ms)) - 1
    for u in units:
        if u["state"] == "open":
            close_unit(u, float(xq[last]), "time")
    filled = [u for u in units if u["entry"] is not None]
    states = {}
    for u in units:
        states[u["state"]] = states.get(u["state"], 0) + 1
    return {"filled_parts": len(filled), "parts": len(units), "pnl": round(pnl, 2), "states": states,
            "mgmt_events": sum(1 for e in log if e[1] != "version")}
