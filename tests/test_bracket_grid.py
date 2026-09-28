import numpy as np

from research.bracket_grid import SimpleRule, simple_grid, simple_outcomes
from research.signal_paths import LEVELS, NS, TIME_EXITS_MIN, products, second_path

T0 = 1_700_000_000 * NS


def z_from(points_list, directions, fx=1.0, spread=0.2, cutoff_s=3600):
    """Build a signal_paths-like dict from synthetic bid tapes."""
    rows = []
    for pts in points_list:
        t = np.array([T0 + int(s * NS) for s, _ in pts], dtype=np.int64)
        b = np.array([p for _, p in pts], dtype=float)
        p = second_path(t, b, b + spread, T0, T0 + cutoff_s * NS, latency_ms=0)
        rows.append((p, products(p)))
    st = lambda k: np.stack([pr[k] for _, pr in rows])
    return {"ids": np.array([f"s{i}" for i in range(len(rows))]), "direction": np.array(directions, dtype=np.int8),
            "fx": np.full(len(rows), fx), "n_seconds": np.array([p.n for p, _ in rows]),
            "fp_fav": st("fp_fav"), "fp_adv": st("fp_adv"), "exit_val": st("exit_val"), "end_val": st("end_val")}


def test_tp_sl_time_and_invert():
    # BUY signal; entry ask 100.2; bid goes to 103.2 at 10 s then 97.2 at 20 s
    z = z_from([[(0, 100.0), (10, 103.2), (20, 97.2)]], [0], fx=1.25)
    rules = [SimpleRule(False, 3.0, 3.0, None), SimpleRule(False, 5.0, 3.0, None),
             SimpleRule(True, 3.0, 3.0, None), SimpleRule(False, None, None, 5)]
    out = simple_outcomes(z, rules)
    eur = 1 / 1.25
    assert np.isclose(out[0, 0], 3.0 * eur)          # TP 3 first
    assert np.isclose(out[0, 1], -3.0 * eur)         # TP 5 never, SL 3 at 20 s
    # invert = SELL at bid 100.0, exits at ask: ask 103.4 at 10 s -> adverse 3 first
    assert np.isclose(out[0, 2], -3.0 * eur)
    # time exit 5 min: last bid 97.2 -> 97.2 - 100.2 = -3.0
    assert np.isclose(out[0, 3], -3.0 * eur)


def test_same_second_counts_as_stop_and_censored_time_uses_cutoff_value():
    z = z_from([[(0, 100.0), (5.1, 101.3), (5.6, 98.8)]], [0], cutoff_s=120)
    out = simple_outcomes(z, [SimpleRule(False, 1.0, 1.0, None), SimpleRule(False, None, None, 60)])
    assert np.isclose(out[0, 0], -1.0)
    # 60 min exit beyond the 120 s cutoff -> value at the cutoff (last bid 98.8 - 100.2)
    assert np.isclose(out[0, 1], 98.8 - 100.2)


def test_grid_levels_exist_in_tables():
    for r in simple_grid():
        for x in (r.tp, r.sl):
            assert x is None or np.isclose(LEVELS, x).any()
        assert r.time_min is None or r.time_min in TIME_EXITS_MIN
