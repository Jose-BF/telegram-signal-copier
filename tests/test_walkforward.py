import numpy as np

from research.walkforward import final_pick, month_stats, walk_forward

MONTHS = [f"2026-{m:02d}" for m in range(1, 7)]


def data(effect, n_per_month=60, G=5, seed=0):
    rng = np.random.default_rng(seed)
    months = np.repeat(MONTHS, n_per_month)
    pnl = rng.normal(0, 5, size=(len(months), G))
    pnl[:, 2] += effect
    return pnl, months


def test_procedure_finds_a_persistent_edge_out_of_sample():
    pnl, months = data(effect=3.0)
    F = np.ones((1, len(months)))
    S, Q, N = month_stats(pnl, months, MONTHS, F)
    wf = walk_forward(S, Q, N, MONTHS, start=2, n_min=30)
    assert wf["pos_months"] == wf["n_months"] == 4
    assert all(m["choice"] == (0, 2) for m in wf["months"])
    assert final_pick(S, Q, N)[:2] == (0, 2)


def test_uses_only_past_months():
    # genome 1 is great only in the last month: the walk-forward cannot pick it for that month
    pnl, months = data(effect=0.0, seed=1)
    pnl[months == MONTHS[-1], 1] += 50
    S, Q, N = month_stats(pnl, months, MONTHS, np.ones((1, len(months))))
    wf = walk_forward(S, Q, N, MONTHS, start=2, n_min=30)
    assert wf["months"][-1]["choice"] != (0, 1)


def test_not_filled_signals_do_not_count_and_min_n_blocks_trading():
    pnl, months = data(effect=3.0)
    pnl[:, :] = np.nan
    pnl[:10, 2] = 5.0                        # only 10 filled baskets in the first month
    S, Q, N = month_stats(pnl, months, MONTHS, np.ones((1, len(months))))
    wf = walk_forward(S, Q, N, MONTHS, start=2, n_min=30)
    assert wf["traded_months"] == 0 and wf["total"] == 0.0


def test_filters_select_subsets():
    pnl, months = data(effect=0.0, seed=2)
    good = np.arange(len(months)) % 2 == 0
    pnl[good, 0] += 4.0
    F = np.vstack([np.ones(len(months)), good.astype(float)])
    S, Q, N = month_stats(pnl, months, MONTHS, F)
    assert final_pick(S, Q, N)[:2] == (1, 0)
