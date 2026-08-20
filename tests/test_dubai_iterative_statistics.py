from __future__ import annotations

from decimal import Decimal

from research.dubai_iterative.statistics import (
    _bootstrap_day_counts,
    assess_daily_stability,
)


def test_daily_stability_groups_signals_and_measures_single_day_dependence():
    assessment = assess_daily_stability((
        ("2026-08-10", Decimal("4.00")),
        ("2026-08-10", Decimal("6.00")),
        ("2026-08-11", Decimal("-5.00")),
        ("2026-08-12", Decimal("15.00")),
    ), samples=1_000, seed=17)

    assert assessment.day_totals == (
        ("2026-08-10", Decimal("10.00")),
        ("2026-08-11", Decimal("-5.00")),
        ("2026-08-12", Decimal("15.00")),
    )
    assert assessment.observed_net_eur == Decimal("20.00")
    assert assessment.worst_day_eur == Decimal("-5.00")
    assert assessment.best_day_eur == Decimal("15.00")
    assert assessment.leave_one_day_out_worst_eur == Decimal("5.00")
    assert assessment.leave_one_day_out_positive_ratio == 1.0
    assert assessment.largest_positive_day_share == 0.6
    assert assessment.blockers == ()


def test_daily_bootstrap_is_deterministic_and_resamples_whole_days():
    rows = (
        ("2026-08-10", Decimal("10.00")),
        ("2026-08-11", Decimal("-5.00")),
        ("2026-08-12", Decimal("15.00")),
    )

    first = assess_daily_stability(rows, samples=10_000, seed=91)
    second = assess_daily_stability(rows, samples=10_000, seed=91)

    assert first == second
    assert first.bootstrap_samples == 10_000
    assert Decimal("-15.00") <= first.bootstrap_p05_eur <= Decimal("45.00")
    assert Decimal("-15.00") <= first.bootstrap_median_eur <= Decimal("45.00")
    assert Decimal("-15.00") <= first.bootstrap_p95_eur <= Decimal("45.00")
    assert 0.0 <= first.bootstrap_probability_positive <= 1.0


def test_daily_stability_fails_closed_on_missing_money():
    assessment = assess_daily_stability((
        ("2026-08-10", Decimal("3.00")),
        ("2026-08-11", None),
    ))

    assert assessment.evidence_complete is False
    assert assessment.observed_net_eur is None
    assert assessment.blockers == ("missing_daily_money:2026-08-11",)


def test_daily_stability_requires_multiple_days_for_resampling_claims():
    assessment = assess_daily_stability((
        ("2026-08-10", Decimal("3.00")),
    ))

    assert assessment.evidence_complete is False
    assert assessment.blockers == ("insufficient_daily_sample",)


def test_daily_stability_rejects_invalid_sampling_budget():
    try:
        assess_daily_stability((
            ("2026-08-10", Decimal("3.00")),
            ("2026-08-11", Decimal("4.00")),
        ), samples=0)
    except ValueError as exc:
        assert str(exc) == "samples must be a positive integer"
    else:
        raise AssertionError("expected ValueError")


def test_bootstrap_day_draws_are_cached_and_preserve_sample_size():
    first = _bootstrap_day_counts(15, 10_000, 91)
    second = _bootstrap_day_counts(15, 10_000, 91)

    assert first is second
    assert first.shape == (10_000, 15)
    assert (first.sum(axis=1) == 15).all()
    assert first.flags.writeable is False
