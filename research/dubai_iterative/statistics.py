"""Day-level stability checks for retrospective Dubai candidates."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
from typing import Iterable

import numpy as np


@dataclass(frozen=True)
class DailyStabilityAssessment:
    day_totals: tuple[tuple[str, Decimal], ...]
    observed_net_eur: Decimal | None
    worst_day_eur: Decimal | None
    best_day_eur: Decimal | None
    positive_days: int
    losing_days: int
    leave_one_day_out_worst_eur: Decimal | None
    leave_one_day_out_positive_ratio: float | None
    largest_positive_day_share: float | None
    bootstrap_samples: int
    bootstrap_probability_positive: float | None
    bootstrap_p05_eur: Decimal | None
    bootstrap_median_eur: Decimal | None
    bootstrap_p95_eur: Decimal | None
    blockers: tuple[str, ...]

    @property
    def evidence_complete(self) -> bool:
        return self.observed_net_eur is not None and not self.blockers


def assess_daily_stability(
    rows: Iterable[tuple[str, Decimal | None]],
    *,
    samples: int = 10_000,
    seed: int = 20260817,
) -> DailyStabilityAssessment:
    """Resample complete trading days without claiming untouched OOS proof."""

    if isinstance(samples, bool) or not isinstance(samples, int) or samples <= 0:
        raise ValueError("samples must be a positive integer")
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    blockers: list[str] = []
    for day, value in rows:
        key = str(day)
        if value is None:
            blockers.append(f"missing_daily_money:{key}")
            continue
        money = Decimal(str(value))
        if not money.is_finite():
            blockers.append(f"invalid_daily_money:{key}")
            continue
        totals[key] += money
    day_totals = tuple(
        (day, _money(value))
        for day, value in sorted(totals.items())
    )
    if blockers:
        return _blocked(day_totals, samples, blockers)
    if len(day_totals) < 2:
        return _blocked(day_totals, samples, ("insufficient_daily_sample",))

    values = tuple(value for _day, value in day_totals)
    observed = _money(sum(values, start=Decimal("0")))
    leave_one_out = tuple(_money(observed - value) for value in values)
    positive = tuple(value for value in values if value > 0)
    positive_total = sum(positive, start=Decimal("0"))
    concentration = (
        float(max(positive) / positive_total)
        if positive_total > 0
        else 0.0
    )

    cents = np.asarray([_minor(value) for value in values], dtype=np.int64)
    sample_counts = _bootstrap_day_counts(len(cents), samples, seed)
    sampled_cents = sample_counts @ cents

    return DailyStabilityAssessment(
        day_totals=day_totals,
        observed_net_eur=observed,
        worst_day_eur=min(values),
        best_day_eur=max(values),
        positive_days=sum(value > 0 for value in values),
        losing_days=sum(value < 0 for value in values),
        leave_one_day_out_worst_eur=min(leave_one_out),
        leave_one_day_out_positive_ratio=(
            sum(value > 0 for value in leave_one_out) / len(leave_one_out)
        ),
        largest_positive_day_share=concentration,
        bootstrap_samples=samples,
        bootstrap_probability_positive=float(np.mean(sampled_cents > 0)),
        bootstrap_p05_eur=_quantile_money(sampled_cents, 0.05),
        bootstrap_median_eur=_quantile_money(sampled_cents, 0.50),
        bootstrap_p95_eur=_quantile_money(sampled_cents, 0.95),
        blockers=(),
    )


def _blocked(day_totals, samples, blockers):
    return DailyStabilityAssessment(
        day_totals=tuple(day_totals),
        observed_net_eur=None,
        worst_day_eur=None,
        best_day_eur=None,
        positive_days=0,
        losing_days=0,
        leave_one_day_out_worst_eur=None,
        leave_one_day_out_positive_ratio=None,
        largest_positive_day_share=None,
        bootstrap_samples=samples,
        bootstrap_probability_positive=None,
        bootstrap_p05_eur=None,
        bootstrap_median_eur=None,
        bootstrap_p95_eur=None,
        blockers=tuple(dict.fromkeys(blockers)),
    )


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _minor(value: Decimal) -> int:
    return int(
        (value * Decimal("100")).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )


def _quantile_money(values: np.ndarray, quantile: float) -> Decimal:
    cents = Decimal(str(float(np.quantile(values, quantile))))
    rounded = cents.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return (rounded / Decimal("100")).quantize(Decimal("0.01"))


@lru_cache(maxsize=128)
def _bootstrap_day_counts(
    day_count: int,
    samples: int,
    seed: int,
) -> np.ndarray:
    """Reuse identical whole-day bootstrap draws across strategy candidates."""

    if day_count <= 0 or samples <= 0:
        raise ValueError("day_count and samples must be positive")
    rng = np.random.default_rng(seed)
    indices = rng.integers(
        0,
        day_count,
        size=(samples, day_count),
        dtype=np.int64,
    )
    counts = np.zeros((samples, day_count), dtype=np.int16)
    sample_rows = np.arange(samples)
    for draw in range(day_count):
        np.add.at(counts, (sample_rows, indices[:, draw]), 1)
    counts.setflags(write=False)
    return counts
