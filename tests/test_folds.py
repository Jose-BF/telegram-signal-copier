import numpy as np

from research.folds import oos_record, pbo, splits

MONTHS = [f"2026-{m:02d}" for m in range(1, 10)]


def test_split_families_and_counts():
    s = splits(MONTHS)
    fams = [f for f, _, _ in s]
    assert fams.count("forward") == 1 and fams.count("backward") == 1
    assert fams.count("rolling") == 5            # windows starting Jan..May (3 + 2 months)
    assert fams.count("leave_one_out") == 9
    assert fams.count("cscv") == 126             # C(9, 4)
    for f, a, b in s:
        assert not set(a) & set(b)
    six = splits(MONTHS[3:])
    assert [f for f, _, _ in six].count("cscv") == 20   # C(6, 3), Gold months


def test_pure_noise_gives_pbo_near_half_on_average():
    # one draw is noisy (the 70 CSCV splits are strongly correlated): average over draws
    s = splits(MONTHS[:8], families=("cscv",))
    values = [pbo(np.random.default_rng(seed).normal(size=(8, 400)), MONTHS[:8], s)["pbo"] for seed in range(40)]
    assert 0.40 <= float(np.mean(values)) <= 0.60


def test_dominant_candidate_gives_pbo_near_zero():
    rng = np.random.default_rng(1)
    M = rng.normal(size=(8, 200))
    M[:, 7] += 5.0
    r = pbo(M, MONTHS[:8], splits(MONTHS[:8], families=("cscv",)))
    assert r["pbo"] <= 0.02 and r["chosen_positive_out_of_sample"] >= 0.98


def test_overfit_candidates_give_high_pbo():
    # each candidate is great in exactly one month and bad elsewhere -> the in-sample winner fails out of sample
    M = -np.ones((8, 8)) + np.eye(8) * 20
    r = pbo(M, MONTHS[:8], splits(MONTHS[:8], families=("cscv",)))
    assert r["pbo"] >= 0.5


def test_oos_record_for_a_fixed_candidate():
    M = np.array([[1.0], [-2.0], [3.0], [1.0]])
    rec = oos_record(M, MONTHS[:4], splits(MONTHS[:4], families=("leave_one_out",)), 0)
    assert rec == {"positive_share": 0.75, "n": 4}
