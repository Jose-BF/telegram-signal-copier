from datetime import datetime, timezone

import numpy as np

from research.history_replay import server_offset_s
from research.signal_paths import LEVELS, NS, TIME_EXITS_MIN, products, second_path

T0 = 1_700_000_000 * NS


def tape(points, spread=0.2):
    """points: [(seconds_after_T0, bid)] -> times, bid, ask with a fixed spread."""
    t = np.array([T0 + int(s * NS) for s, _ in points], dtype=np.int64)
    b = np.array([p for _, p in points], dtype=float)
    return t, b, b + spread


def lvl(x):
    return int(np.flatnonzero(np.isclose(LEVELS, x))[0])


def test_entry_after_latency_and_first_passage_both_directions():
    # entry tick at 1.0 s (latency 800 ms): bid 100, ask 100.2
    t, b, a = tape([(0.0, 99.0), (1.0, 100.0), (2.5, 101.0), (4.2, 103.2), (10.0, 99.0), (20.0, 96.0)])
    p = second_path(t, b, a, T0, T0 + 3600 * NS, latency_ms=800)
    assert p.entry_ns == T0 + NS and p.ask0 == 100.2 and p.bid0 == 100.0
    pr = products(p)
    # BUY exits at the bid: +3 reached when bid >= 103.2 at 4.2 s -> bucket 3 (entry at 1 s)
    assert pr["fp_fav"][0, lvl(3.0)] == 3
    assert pr["fp_fav"][0, lvl(3.25)] == -1
    # BUY adverse -1: bid <= 99.2 first at 10 s -> bucket 9
    assert pr["fp_adv"][0, lvl(1.0)] == 9
    # SELL (entry bid 100.0, exits at the ask): +3 needs ask <= 97.0 -> ask 96.2 at 20 s -> bucket 19
    assert pr["fp_fav"][1, lvl(3.0)] == 19
    # SELL adverse 3: ask >= 103.0 -> ask 103.4 at 4.2 s -> bucket 3
    assert pr["fp_adv"][1, lvl(3.0)] == 3
    assert pr["fp_adv"][1, lvl(3.5)] == -1


def test_time_exit_values_mfe_mae_and_censoring():
    t, b, a = tape([(0.0, 100.0), (60.0, 102.0), (400.0, 98.0), (2000.0, 101.0)])
    p = second_path(t, b, a, T0, T0 + 1000 * NS, latency_ms=0)  # cutoff after 1000 s
    pr = products(p)
    j5 = TIME_EXITS_MIN.index(5)
    # at 5 min (second 299) last bid 102 -> BUY 102 - 100.2 = 1.8; SELL 100.0 - 102.2 = -2.2
    assert np.isclose(pr["exit_val"][0, j5], 1.8) and np.isclose(pr["exit_val"][1, j5], -2.2)
    assert np.isclose(pr["mfe"][0, j5], 1.8) and np.isclose(pr["mae"][0, j5], 0.0)
    j10 = TIME_EXITS_MIN.index(10)
    assert np.isclose(pr["exit_val"][0, j10], 98 - 100.2)
    assert np.isclose(pr["mae"][0, j10], 98 - 100.2)
    # 20 min is beyond the 1000 s cutoff: censored (NaN); the tick at 2000 s is never used
    assert np.isnan(pr["exit_val"][0, TIME_EXITS_MIN.index(20)])
    assert p.n == 1000 and np.isclose(pr["end_val"][0], 98 - 100.2)


def test_no_entry_when_first_quote_is_after_cutoff():
    t, b, a = tape([(0.0, 100.0), (50.0, 100.0)])
    assert second_path(t, b, a, T0 + 10 * NS, T0 + 40 * NS, latency_ms=0) is None


def test_same_second_touch_is_not_split():
    # both +1 and -1 inside the same second: both tables report that second (bracket_grid resolves worst case)
    t, b, a = tape([(0.0, 100.0), (5.1, 101.3), (5.6, 98.8)])
    pr = products(second_path(t, b, a, T0, T0 + 60 * NS, latency_ms=0))
    assert pr["fp_fav"][0, lvl(1.0)] == 5 and pr["fp_adv"][0, lvl(1.0)] == 5


def test_broker_clock_switches_at_us_dst():
    assert server_offset_s(datetime(2026, 3, 8, 6, 59, tzinfo=timezone.utc)) == 7200
    assert server_offset_s(datetime(2026, 3, 8, 7, 0, tzinfo=timezone.utc)) == 10800
