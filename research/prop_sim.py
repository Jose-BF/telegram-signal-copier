"""Challenge simulation with floating equity (search 2, M8), on top of research/account_sim.

Input: the account's daily table from account_sim.daily_table (per broker day: open equity,
lowest equity incl. floating, close equity, traded?) in EUR at the strategy's base lots.
`k` scales the lots. Rules come from research/prop_rules.PropRuleset and add, over
account_sim.run_phase: the FTMO best-day rule (the target only counts once the best day is
<= share of the positive days' profit; otherwise trading continues) and The5ers' minimum of
profitable days (closed profit >= pct of the initial balance; approximated with the day's
equity change). An unlimited time limit is capped at `horizon_days` (reported as unfinished).
"""
from __future__ import annotations

import numpy as np

from research.prop_rules import PropRuleset


def _eu_dst(year: int):
    """EU summer time: last Sunday of March 01:00 UTC -> last Sunday of October 01:00 UTC."""
    import datetime as _dt
    def last_sunday(month):
        d = _dt.datetime(year, month + 1, 1, tzinfo=_dt.timezone.utc) - _dt.timedelta(days=1)
        return d - _dt.timedelta(days=(d.weekday() + 1) % 7)
    return (int(last_sunday(3).replace(hour=1).timestamp() * 1000), int(last_sunday(10).replace(hour=1).timestamp() * 1000))


def daily_table_cet(grid, eq_min, eq_last):
    """Like account_sim.daily_table but with FTMO's day: 00:00 CE(S)T (UTC+1 winter, UTC+2 summer)."""
    import datetime as _dt
    years = {_dt.datetime.fromtimestamp(int(t) / 1000, _dt.timezone.utc).year for t in (grid[0], grid[-1])}
    off = np.full(len(grid), 3_600_000, dtype=np.int64)
    for y in years:
        a, b = _eu_dst(y)
        off[(grid >= a) & (grid < b)] = 7_200_000
    day = (grid + off) // 86_400_000
    out = []
    for d in np.unique(day):
        sel = day == d
        mn, ls = eq_min[sel], eq_last[sel]
        name = _dt.datetime.fromtimestamp(int(d) * 86400, _dt.timezone.utc).date().isoformat()
        out.append((name, float(ls[0]), float(mn.min()), float(ls[-1]), bool(np.any(np.diff(ls) != 0))))
    return out


def run_phase(days, start, k, balance, target_pct, rules: PropRuleset, horizon_days=365):
    base = days[start][1]
    traded = 0
    day_pnls = []
    limit = rules.max_days if rules.max_days is not None else horizon_days
    for j in range(start, min(len(days), start + limit)):
        _, open_eq, low, close_eq, active = days[j]
        rel_open, rel_low, rel_close = ((x - base) * k for x in (open_eq, low, close_eq))
        traded += bool(active)
        if rel_low - rel_open <= -rules.daily_pct / 100 * balance:
            return "fail", "diaria", j - start + 1, j
        if rel_low <= -rules.max_pct / 100 * balance:
            return "fail", "total", j - start + 1, j
        day_pnls.append(rel_close - rel_open)
        if rel_close < target_pct / 100 * balance or traded < rules.min_days:
            continue
        if rules.best_day_max_share is not None:
            pos = [p for p in day_pnls if p > 0]
            if pos and max(pos) > rules.best_day_max_share * sum(pos):
                continue
        if rules.min_profitable_days:
            good = sum(p >= (rules.profitable_day_pct or 0) / 100 * balance for p in day_pnls)
            if good < rules.min_profitable_days:
                continue
        return "pass", None, j - start + 1, j
    return "unfinished", "tiempo", min(len(days) - start, limit), None


def challenge_stats(days, k, rules: PropRuleset, balance=100_000.0, horizon_days=365):
    """Start the challenge on every day; both phases (phase 2 starts the day after passing phase 1)."""
    res = {"pass": 0, "fail_diaria": 0, "fail_total": 0, "unfinished": 0}
    days_to_pass = []
    for s in range(len(days)):
        o, why, used, end = run_phase(days, s, k, balance, rules.target1_pct, rules, horizon_days)
        if o == "pass" and rules.target2_pct:
            if end + 1 >= len(days):
                res["unfinished"] += 1; continue
            o2, why2, used2, _ = run_phase(days, end + 1, k, balance, rules.target2_pct, rules, horizon_days)
            o, why, used = o2, why2, used + used2
        if o == "pass":
            res["pass"] += 1; days_to_pass.append(used)
        elif o == "fail":
            res[f"fail_{why}"] += 1
        else:
            res["unfinished"] += 1
    decided = res["pass"] + res["fail_diaria"] + res["fail_total"]
    res["pass_rate"] = round(res["pass"] / decided, 3) if decided else None
    res["decided"] = decided
    res["median_days"] = float(np.median(days_to_pass)) if days_to_pass else None
    return res


def monte_carlo(days, k, rules: PropRuleset, balance=100_000.0, n=4000, block=5, seed=0, horizon_days=365):
    """Resample blocks of consecutive days (keeps intraday dips and short streaks) into new
    sequences; returns the two-phase pass / fail / unfinished shares and median days."""
    rng = np.random.default_rng(seed)
    rows = [(o, low, c, a) for _, o, low, c, a in days]
    if len(rows) < block:
        return None
    out = {"pass": 0, "fail": 0, "unfinished": 0}
    used_days = []
    for _ in range(n):
        seq = []
        while len(seq) < horizon_days:
            s = int(rng.integers(0, len(rows) - block + 1))
            seq.extend(rows[s:s + block])
        # rebuild a cumulative daily table from day changes
        eq, table = 0.0, []
        for o, low, c, a in seq:
            table.append((None, eq, eq + (low - o), eq + (c - o), a))
            eq += c - o
        o1, _, u1, e1 = run_phase(table, 0, k, balance, rules.target1_pct, rules, horizon_days)
        if o1 == "pass" and rules.target2_pct and e1 + 1 < len(table):
            o1, _, u2, _ = run_phase(table, e1 + 1, k, balance, rules.target2_pct, rules, horizon_days)
            u1 += u2
        out["pass" if o1 == "pass" else "fail" if o1 == "fail" else "unfinished"] += 1
        if o1 == "pass":
            used_days.append(u1)
    return {"pass": out["pass"] / n, "fail": out["fail"] / n, "unfinished": out["unfinished"] / n,
            "median_days": float(np.median(used_days)) if used_days else None}


def best_multiplier(days, rules: PropRuleset, balance=100_000.0, ks=None, max_fail=0.2, seed=0):
    """Lot multiplier with the highest Monte-Carlo pass rate whose fail rate stays <= max_fail."""
    ks = ks or [0.5, 1, 2, 3, 5, 8, 12, 16, 20, 30, 40, 60, 80]
    best = None
    table = []
    for k in ks:
        mc = monte_carlo(days, k, rules, balance, n=1500, seed=seed)
        table.append((k, mc))
        if mc and mc["fail"] <= max_fail and (best is None or mc["pass"] > best[1]["pass"]):
            best = (k, mc)
    return best, table


def capital_needed(days, stress_worst_eur=0.0, margin=2.0):
    """Carril B: capital to survive the deepest equity drop (floating included) and the stress case."""
    closes = np.array([c for _, o, low, c, a in days]); lows = np.array([low for _, o, low, c, a in days])
    peak = np.maximum.accumulate(np.concatenate([[days[0][1]], closes]))[:-1]
    deepest = float(np.min(lows - peak)) if len(days) else 0.0
    return round(margin * max(-deepest, -stress_worst_eur, 0.0), 2), round(deepest, 2)
