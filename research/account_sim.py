"""Account-level equity (all baskets together, floating included) and prop-firm checks.

Baskets come as minute curves (research.history_search.basket_curve): the account's
equity at each minute is the sum of every basket's value (curve inside its span,
final result after it). Prop rules (FTMO-like 2-step, verify with the firm):
daily loss 5 % of the initial balance measured from the start-of-day equity
(broker midnight), maximum loss 10 % of the initial balance, targets 10 % then 5 %,
at least 4 trading days per phase. Lot size is scaled by `k` (1 = the bot's live lots).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from research.history_replay import server_offset_s
from datetime import datetime, timezone


def account_equity(baskets, start_ms, end_ms, step_ms=60_000):
    """baskets: [(minutes[], min_eq[], last_eq[], final_pnl)] -> (grid, eq_min, eq_last)."""
    grid = np.arange(start_ms // step_ms * step_ms, end_ms + step_ms, step_ms, dtype=np.int64)
    eq_min = np.zeros(len(grid)); eq_last = np.zeros(len(grid)); after = np.zeros(len(grid) + 1)
    for mins, mn, last, final in baskets:
        if mins is None or not len(mins):
            continue
        idx = np.searchsorted(grid, np.asarray(mins, dtype=np.int64))
        ok = (idx >= 0) & (idx < len(grid))
        # inside the span: minute values (a later minute of the same basket overwrites nothing: unique minutes)
        np.add.at(eq_min, idx[ok], np.asarray(mn)[ok])
        np.add.at(eq_last, idx[ok], np.asarray(last)[ok])
        # between minutes of the span without ticks, carry the last value: fill gaps
        span_lo, span_hi = idx[ok].min(), idx[ok].max()
        full = np.arange(span_lo, span_hi + 1)
        have = np.zeros(len(full), dtype=bool); have[idx[ok] - span_lo] = True
        if not have.all():
            vals = np.full(len(full), np.nan); vals[idx[ok] - span_lo] = np.asarray(last)[ok]
            filled = _ffill(vals)
            miss = full[~have]
            eq_min[miss] += filled[~have]; eq_last[miss] += filled[~have]
        after[span_hi + 1] += final
    carry = np.cumsum(after)[:-1]
    return grid, eq_min + carry, eq_last + carry


def _ffill(a):
    mask = np.isnan(a)
    idx = np.where(~mask, np.arange(len(a)), 0)
    np.maximum.accumulate(idx, out=idx)
    return a[idx]


def day_key(ms):
    d = datetime.fromtimestamp(ms / 1000, timezone.utc)
    return datetime.fromtimestamp(ms / 1000 + server_offset_s(d), timezone.utc).date().isoformat()


@dataclass
class Rules:
    daily_pct: float = 5.0
    max_pct: float = 10.0
    target1_pct: float = 10.0
    target2_pct: float = 5.0
    min_days: int = 4
    max_days: int = 60


def daily_table(grid, eq_min, eq_last):
    """Per broker day: equity at the start, lowest equity, equity at the end, traded?"""
    from research.history_replay import DST_SWITCH_UTC
    switch = int(DST_SWITCH_UTC.timestamp() * 1000)
    off = np.where(grid < switch, 7200_000, 10800_000)
    day = (grid + off) // 86_400_000
    out = []
    for d in np.unique(day):
        sel = day == d
        mn, ls = eq_min[sel], eq_last[sel]
        name = datetime.fromtimestamp(int(d) * 86400, timezone.utc).date().isoformat()
        out.append((name, float(ls[0]), float(mn.min()), float(ls[-1]), bool(np.any(np.diff(ls) != 0))))
    return out


def run_phase(days, start, k, balance, target_pct, rules):
    """Walk days from index `start`; returns (outcome, reason, days_used, end_index)."""
    base = 0.0  # account pnl (in bot-lot EUR) at phase start
    start_eq = days[start][1]
    traded = 0
    for j in range(start, min(len(days), start + rules.max_days)):
        d, open_eq, low, close_eq, active = days[j]
        rel_open = (open_eq - start_eq) * k
        rel_low = (low - start_eq) * k
        rel_close = (close_eq - start_eq) * k
        traded += active
        if rel_low - rel_open <= -rules.daily_pct / 100 * balance:
            return "fail", "diaria", j - start + 1, j
        if rel_low <= -rules.max_pct / 100 * balance:
            return "fail", "total", j - start + 1, j
        if rel_close >= target_pct / 100 * balance and traded >= rules.min_days:
            return "pass", None, j - start + 1, j
    return ("unfinished", "tiempo", min(len(days) - start, rules.max_days), None)


def challenge_stats(days, k, balance=10_000.0, rules=Rules()):
    """Start a 2-step challenge on every trading day; count outcomes."""
    res = {"pass": 0, "fail_diaria": 0, "fail_total": 0, "unfinished": 0, "days_to_pass": []}
    for s in range(len(days)):
        o, why, used, end = run_phase(days, s, k, balance, rules.target1_pct, rules)
        if o == "pass":
            o2, why2, used2, _ = run_phase(days, end + 1, k, balance, rules.target2_pct, rules) if end + 1 < len(days) else ("unfinished", "tiempo", 0, None)
            if o2 == "pass":
                res["pass"] += 1; res["days_to_pass"].append(used + used2)
            elif o2 == "fail":
                res[f"fail_{why2}"] += 1
            else:
                res["unfinished"] += 1
        elif o == "fail":
            res[f"fail_{why}"] += 1
        else:
            res["unfinished"] += 1
    done = res["pass"] + res["fail_diaria"] + res["fail_total"]
    res["pass_rate"] = round(100 * res["pass"] / done, 1) if done else None
    res["median_days"] = int(np.median(res["days_to_pass"])) if res["days_to_pass"] else None
    del res["days_to_pass"]
    return res


def safe_multiplier(days, balance=10_000.0, rules=Rules(), margin=0.5):
    """Largest lot multiplier whose worst day loss and worst overall drop in these days stay
    within `margin` of the prop limits (computed on the SEARCH period only)."""
    worst_day = min((low - open_eq for d, open_eq, low, close_eq, a in days), default=0.0)
    eq_lows = np.array([low for d, o, low, c, a in days]); eq_closes = np.array([c for d, o, l, c, a in days])
    peak = np.maximum.accumulate(np.concatenate([[days[0][1]], eq_closes]))[:-1] if days else np.array([0])
    worst_drop = float(np.min(eq_lows - peak)) if days else 0.0
    ks = []
    if worst_day < 0:
        ks.append(margin * rules.daily_pct / 100 * balance / -worst_day)
    if worst_drop < 0:
        ks.append(margin * rules.max_pct / 100 * balance / -worst_drop)
    return round(min(ks), 2) if ks else None


def monte_carlo(days, k, balance=10_000.0, rules=Rules(), n=2000, seed=0):
    """Bootstrap whole days (keeps each day's intraday dip) into new sequences; phase-1 pass rate."""
    rng = np.random.default_rng(seed)
    rows = [(o, low, c, a) for d, o, low, c, a in days]
    if not rows:
        return None
    passed = failed = 0
    for _ in range(n):
        eq, traded = 0.0, 0
        for _day in range(rules.max_days):
            o, low, c, a = rows[rng.integers(len(rows))]
            dip, move = (low - o) * k, (c - o) * k
            if dip <= -rules.daily_pct / 100 * balance or eq + dip <= -rules.max_pct / 100 * balance:
                failed += 1; break
            eq += move; traded += a
            if eq >= rules.target1_pct / 100 * balance and traded >= rules.min_days:
                passed += 1; break
    return round(100 * passed / n, 1), round(100 * failed / n, 1)
