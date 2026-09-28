"""Plain summary of a stage-2 run: per strategy, per period and broker scenario, plus
account-level (floating) risk and prop-challenge checks on the slow-broker curves."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime

import numpy as np

from research.account_sim import Rules, account_equity, challenge_stats, daily_table, monte_carlo, safe_multiplier

PERIODS = {"busqueda": ("2026-01-01", "2026-07-01"), "validacion": ("2026-07-01", "2026-09-12"),
           "final": ("2026-09-14", "2026-09-26")}


def period_of(day):
    for k, (a, b) in PERIODS.items():
        if a <= day < b:
            return k
    return None


def summarize(stage2_json, balance=10_000.0):
    d = json.load(open(stage2_json))
    sel = d["selection"]
    out = []
    for ch, items in sel.items():
        for gi, item in enumerate(items):
            rec = {"channel": ch, "index": gi, "label": item.get("label"), "filter": item.get("filter")}
            for sc, rows in d["rows"].items():
                mine = [r for r in rows if r[1] == ch and r[2] == gi]
                per = defaultdict(lambda: {"n": 0, "net": 0.0, "wins": 0, "worst": 0.0})
                weeks = defaultdict(float)
                for r in mine:
                    p = period_of(r[3]); pnl = r[5] or 0.0
                    if p is None:
                        continue
                    x = per[p]; x["n"] += 1; x["net"] += pnl; x["wins"] += pnl > 0; x["worst"] = min(x["worst"], pnl)
                    y, w, _ = date.fromisoformat(r[3]).isocalendar(); weeks[(p, f"{y}-W{w:02d}")] += pnl
                for p, x in per.items():
                    wk = [v for (pp, _), v in weeks.items() if pp == p]
                    x.update(net=round(x["net"], 1), worst=round(x["worst"], 1),
                             win_pct=round(100 * x["wins"] / x["n"], 1) if x["n"] else None,
                             weeks=len(wk), weeks_pos=sum(v > 0 for v in wk), worst_week=round(min(wk), 1) if wk else None)
                    del x["wins"]
                rec[sc] = dict(per)
            # account-level on slow broker curves
            rows = [r for r in d["rows"].get("C_p50", []) if r[1] == ch and r[2] == gi and len(r) > 8]
            acct = {}
            for p in ("busqueda", "validacion", "final"):
                a, b = PERIODS[p]
                bs = [(r[8][0], r[8][1], r[8][2], r[5] or 0.0) for r in rows if a <= r[3] < b and r[8]]
                if not bs:
                    continue
                start = min(m[0][0] for m in bs) - 60_000
                end = max(m[0][-1] for m in bs) + 60_000
                grid, mn, ls = account_equity(bs, start, end)
                days = daily_table(grid, mn, ls)
                peak = np.maximum.accumulate(ls)
                acct[p] = {"days": days, "max_drop_eur": round(float(np.min(mn - peak)), 1),
                           "worst_day_eur": round(min(low - o for _, o, low, c, _a in days), 1)}
            if "busqueda" in acct:
                k = safe_multiplier(acct["busqueda"]["days"], balance)
                rec["lot_multiplier_10k"] = k
                for p in ("validacion", "final"):
                    if p in acct and k:
                        acct[p]["challenge"] = challenge_stats(acct[p]["days"], k, balance)
                        acct[p]["montecarlo_pass_fail_pct"] = monte_carlo(acct[p]["days"], k, balance)
            for p in acct:
                acct[p].pop("days", None)
            rec["account"] = acct
            out.append(rec)
    return out


if __name__ == "__main__":
    import sys
    print(json.dumps(summarize(sys.argv[1]), indent=1, default=str)[:6000])
