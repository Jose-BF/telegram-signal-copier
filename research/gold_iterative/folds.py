"""Complete-day chronological contracts for Gold Signals research."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Mapping, Protocol, Sequence

from research.dubai_iterative.search import ChronologicalFold


class GoldFoldDataset(Protocol):
    paths: Sequence[object]
    eligible_signal_ids: Sequence[str]
    eligible_signal_days: Mapping[str, str]
    exclusions: Mapping[str, Sequence[str]]


@dataclass(frozen=True)
class GoldDayCoverage:
    day: str
    eligible_signal_ids: tuple[str, ...]
    loaded_signal_ids: tuple[str, ...]
    missing_signal_ids: tuple[str, ...]
    exclusion_reasons: tuple[tuple[str, tuple[str, ...]], ...]

    @property
    def complete(self) -> bool:
        return not self.missing_signal_ids


@dataclass(frozen=True)
class GoldFoldPlan:
    day_coverage: tuple[GoldDayCoverage, ...]
    folds: tuple[ChronologicalFold, ...]

    @property
    def complete_days(self) -> tuple[str, ...]:
        return tuple(item.day for item in self.day_coverage if item.complete)

    @property
    def incomplete_days(self) -> tuple[str, ...]:
        return tuple(item.day for item in self.day_coverage if not item.complete)


def build_gold_fold_plan(
    dataset: GoldFoldDataset,
    *,
    minimum_development_days: int = 2,
    challenge_days_per_fold: int = 1,
) -> GoldFoldPlan:
    """Build expanding folds only from fully accounted formal NOW days."""

    if minimum_development_days < 2:
        raise ValueError("minimum_development_days must be at least 2")
    if challenge_days_per_fold <= 0:
        raise ValueError("challenge_days_per_fold must be positive")

    eligible_ids = tuple(str(value) for value in dataset.eligible_signal_ids)
    if len(set(eligible_ids)) != len(eligible_ids):
        raise ValueError("eligible signal identities must be unique")
    eligible_set = set(eligible_ids)
    signal_days = {
        str(signal_id): str(day)
        for signal_id, day in dataset.eligible_signal_days.items()
    }
    missing_day_ids = eligible_set.difference(signal_days)
    extra_day_ids = set(signal_days).difference(eligible_set)
    if missing_day_ids or extra_day_ids:
        raise ValueError(
            "eligible signal day map must exactly cover eligible signal identities"
        )
    for day_value in signal_days.values():
        try:
            date.fromisoformat(day_value)
        except ValueError as exc:
            raise ValueError(f"invalid eligible trading day: {day_value}") from exc

    loaded_days: dict[str, str] = {}
    for path in dataset.paths:
        signal_id = str(path.signal_id)
        path_day = str(path.day)
        if signal_id not in eligible_set:
            raise ValueError(f"loaded path is not eligible: {signal_id}")
        if signal_id in loaded_days:
            raise ValueError(f"duplicate loaded signal path: {signal_id}")
        if signal_days[signal_id] != path_day:
            raise ValueError(f"loaded path day disagrees with scope: {signal_id}")
        loaded_days[signal_id] = path_day

    excluded_by_signal: dict[str, list[str]] = {}
    for reason, signal_ids in dataset.exclusions.items():
        for signal_id in signal_ids:
            normalized = str(signal_id)
            if normalized in eligible_set:
                excluded_by_signal.setdefault(normalized, []).append(str(reason))

    ordered_days = sorted(set(signal_days.values()))
    day_coverage: list[GoldDayCoverage] = []
    for day_value in ordered_days:
        day_ids = tuple(
            signal_id
            for signal_id in eligible_ids
            if signal_days[signal_id] == day_value
        )
        loaded = tuple(signal_id for signal_id in day_ids if signal_id in loaded_days)
        missing = tuple(signal_id for signal_id in day_ids if signal_id not in loaded_days)
        reasons: dict[str, list[str]] = {}
        for signal_id in missing:
            labels = excluded_by_signal.get(signal_id) or ["unaccounted_signal"]
            for reason in labels:
                reasons.setdefault(reason, []).append(signal_id)
        day_coverage.append(GoldDayCoverage(
            day=day_value,
            eligible_signal_ids=day_ids,
            loaded_signal_ids=loaded,
            missing_signal_ids=missing,
            exclusion_reasons=tuple(
                (reason, tuple(signal_ids))
                for reason, signal_ids in sorted(reasons.items())
            ),
        ))

    complete_days = tuple(item.day for item in day_coverage if item.complete)
    required = minimum_development_days + challenge_days_per_fold
    if len(complete_days) < required:
        raise ValueError(
            f"at least {required} complete trading days are required "
            f"({minimum_development_days} development and "
            f"{challenge_days_per_fold} later challenge)"
        )

    folds: list[ChronologicalFold] = []
    challenge_start = minimum_development_days
    while challenge_start + challenge_days_per_fold <= len(complete_days):
        development_days = complete_days[:challenge_start]
        challenge_days = complete_days[
            challenge_start:challenge_start + challenge_days_per_fold
        ]
        folds.append(ChronologicalFold(
            name=f"gold_fold_{len(folds) + 1:02d}",
            development_from=development_days[0],
            development_to=development_days[-1],
            challenge_from=challenge_days[0],
            challenge_to=challenge_days[-1],
            development_days=development_days,
            challenge_days=challenge_days,
        ))
        challenge_start += challenge_days_per_fold

    return GoldFoldPlan(
        day_coverage=tuple(day_coverage),
        folds=tuple(folds),
    )
