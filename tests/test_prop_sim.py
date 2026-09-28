import dataclasses

from research.prop_rules import FTMO_2STEP, THE5ERS_HIGH_STAKES
from research.prop_sim import capital_needed, challenge_stats, run_phase


def table(changes, dips=None):
    """daily table from day changes (EUR at base lots) and optional intraday dips."""
    eq, out = 0.0, []
    for i, c in enumerate(changes):
        d = (dips or [0.0] * len(changes))[i]
        out.append((f"d{i}", eq, eq + min(d, 0.0, c), eq + c, True))
        eq += c
    return out


def test_pass_needs_target_min_days_and_best_day_rule():
    # 10k account: +600 on day 1 then +100/day. Target 10 % = 1,000.
    days = table([600] + [100] * 10)
    no_best = dataclasses.replace(FTMO_2STEP, best_day_max_share=None, sources={**FTMO_2STEP.sources})
    o, _, used, _ = run_phase(days, 0, 1, 10_000, 10.0, no_best)
    assert o == "pass" and used == 5                      # 600+4*100 = 1000 on day 5
    # FTMO best day <= 50 % of positive days: 600 must be <= half of the positive sum -> needs 1,200 total
    o, _, used, _ = run_phase(days, 0, 1, 10_000, 10.0, FTMO_2STEP)
    assert o == "pass" and used == 7


def test_daily_loss_counts_the_floating_dip():
    days = table([100, 100, 100], dips=[0, -520, 0])
    o, why, used, _ = run_phase(days, 0, 1, 10_000, 10.0, FTMO_2STEP)
    assert (o, why, used) == ("fail", "diaria", 2)


def test_the5ers_profitable_days():
    days = table([1100, 0, 0, 0])
    o, *_ = run_phase(days, 0, 1, 10_000, 10.0, THE5ERS_HIGH_STAKES)
    assert o == "unfinished"                               # only 1 profitable day of the 3 required
    o, _, used, _ = run_phase(table([400, 400, 400]), 0, 1, 10_000, 10.0, THE5ERS_HIGH_STAKES)
    assert o == "pass" and used == 3


def test_ftmo_day_is_cet_with_eu_dst():
    import numpy as np
    from datetime import datetime, timezone
    from research.prop_sim import daily_table_cet
    ms = lambda s: int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000)
    # winter: 23:30 UTC on 10/01 is already 11/01 in CET (UTC+1); summer: 22:30 UTC on 10/07 is 11/07 in CEST
    grid = np.array([ms("2026-01-10T22:30"), ms("2026-01-10T23:30"), ms("2026-07-10T21:30"), ms("2026-07-10T22:30")], dtype=np.int64)
    eq = np.array([0.0, -1.0, -2.0, -3.0])
    days = [d[0] for d in daily_table_cet(grid, eq, eq)]
    assert days == ["2026-01-10", "2026-01-11", "2026-07-10", "2026-07-11"]


def test_challenge_stats_and_capital():
    days = table([60] * 40)
    r = challenge_stats(days, k=1, rules=FTMO_2STEP, balance=10_000)
    assert r["pass"] > 0 and r["fail_diaria"] == 0
    cap, deepest = capital_needed(table([50, -300, 20, 10], dips=[0, -500, 0, 0]), stress_worst_eur=-200)
    assert deepest == -500 and cap == 1000
