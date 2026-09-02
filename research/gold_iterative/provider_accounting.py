"""Diagnostic provider-pip hypotheses kept separate from account money."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Mapping, Sequence

from research.dubai_iterative.evolution import CandidateEvaluation

from .reporting import ProviderPipHypothesis


_PIP_SCALE = Decimal("100")


def build_candidate_pip_hypotheses(
    evaluations: Sequence[CandidateEvaluation],
    *,
    paths: Sequence[object],
    provider_scorecard: Mapping[str, object],
) -> tuple[ProviderPipHypothesis, ...]:
    """Express plausible pip accounting models without asserting any as true."""

    direction_by_signal: dict[str, Decimal] = {}
    for path in paths:
        signal_id = str(getattr(path, "signal_id", "") or "")
        if not signal_id or signal_id in direction_by_signal:
            raise ValueError("provider path signal identities must be unique")
        direction = str(getattr(path, "direction", "") or "").upper()
        if direction == "BUY":
            direction_by_signal[signal_id] = Decimal("1")
        elif direction == "SELL":
            direction_by_signal[signal_id] = Decimal("-1")
        else:
            raise ValueError(f"unsupported provider direction: {signal_id}")

    periods = _claim_periods(provider_scorecard)
    hypotheses = []
    for evaluation in evaluations:
        if evaluation.blockers:
            continue
        metrics_by_day: dict[str, dict[str, Decimal]] = {}
        for day, result in evaluation.results:
            direction = direction_by_signal.get(result.signal_id)
            if direction is None:
                raise ValueError(
                    f"provider path missing for result: {result.signal_id}"
                )
            exit_moves = [
                direction
                * (Decimal(str(item.exit_price)) - Decimal(str(item.entry_price)))
                for item in result.exits
            ]
            exit_volumes = [Decimal(str(item.volume)) for item in result.exits]
            total_volume = sum(exit_volumes, start=Decimal("0"))
            weighted_move = (
                sum(
                    (
                        move * volume
                        for move, volume in zip(
                            exit_moves,
                            exit_volumes,
                            strict=True,
                        )
                    ),
                    start=Decimal("0"),
                )
                / total_volume
                if total_volume > 0
                else Decimal("0")
            )
            values = metrics_by_day.setdefault(str(day), {
                "sum_exit_leg_moves_x100": Decimal("0"),
                "volume_weighted_signal_move_x100": Decimal("0"),
                "best_exit_per_signal_x100": Decimal("0"),
                "max_favourable_per_signal_x100": Decimal("0"),
            })
            values["sum_exit_leg_moves_x100"] += (
                sum(exit_moves, start=Decimal("0")) * _PIP_SCALE
            )
            values["volume_weighted_signal_move_x100"] += (
                weighted_move * _PIP_SCALE
            )
            values["best_exit_per_signal_x100"] += (
                max(exit_moves, default=Decimal("0")) * _PIP_SCALE
            )
            values["max_favourable_per_signal_x100"] += (
                Decimal(str(result.max_favourable_move)) * _PIP_SCALE
            )

        descriptions = {
            "sum_exit_leg_moves_x100": (
                "Sum every simulated leg's directional exit move; 1.00 XAUUSD "
                "price unit is represented as 100 candidate pips."
            ),
            "volume_weighted_signal_move_x100": (
                "Count one volume-weighted realized move per provider signal; "
                "1.00 XAUUSD price unit is 100 candidate pips."
            ),
            "best_exit_per_signal_x100": (
                "Count only the best simulated exit move per provider signal; "
                "1.00 XAUUSD price unit is 100 candidate pips."
            ),
            "max_favourable_per_signal_x100": (
                "Count each simulated signal's maximum favourable excursion; "
                "1.00 XAUUSD price unit is 100 candidate pips."
            ),
        }
        for metric, description in descriptions.items():
            totals = {
                period_key: sum(
                    (
                        metrics_by_day.get(day, {}).get(metric, Decimal("0"))
                        for day in period_days
                    ),
                    start=Decimal("0"),
                )
                for period_key, period_days in periods
            }
            hypotheses.append(ProviderPipHypothesis(
                hypothesis_id=f"{evaluation.genome.fingerprint}:{metric}",
                description=description,
                period_totals=totals,
                verified=False,
            ))
    return tuple(hypotheses)


def _claim_periods(
    scorecard: Mapping[str, object],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    periods: set[tuple[str, str]] = set()
    for summary in scorecard.get("summaries") or ():
        if not isinstance(summary, Mapping):
            continue
        claim = summary.get("claim")
        if not isinstance(claim, Mapping) or claim.get("pips_gained") is None:
            continue
        start = str(claim.get("period_start") or "")
        end = str(claim.get("period_end") or "")
        if start and end:
            periods.add((start, end))

    rows = []
    for start, end in sorted(periods):
        first = date.fromisoformat(start)
        last = date.fromisoformat(end)
        if first > last:
            raise ValueError(f"invalid provider claim period: {start}:{end}")
        span = (last - first).days
        days = tuple(
            (first.fromordinal(first.toordinal() + offset)).isoformat()
            for offset in range(span + 1)
        )
        rows.append((f"{start}:{end}", days))
    return tuple(rows)
