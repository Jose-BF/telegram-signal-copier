"""Honest, unit-safe evidence artifacts for Gold Signals research."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
import json
from pathlib import Path
from typing import Mapping, Sequence

import pandas as pd

from research.dubai_iterative.evolution import CandidateEvaluation
from research.dubai_iterative.reporting import ResearchArtifacts

from .folds import GoldFoldPlan


@dataclass(frozen=True)
class GoldEvidenceGates:
    provider_paths_complete: bool
    tick_paths_complete: bool
    account_currency_money_complete: bool
    oracle_parity_complete: bool
    chronological_challenge_complete: bool
    cross_fold_candidate_eligible: bool
    daily_stability_candidate_eligible: bool
    source_manifest_complete: bool

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be boolean")

    @property
    def blockers(self) -> tuple[str, ...]:
        labels = {
            "provider_paths_complete": "provider_paths_incomplete",
            "tick_paths_complete": "tick_paths_incomplete",
            "account_currency_money_complete": (
                "account_currency_money_incomplete"
            ),
            "oracle_parity_complete": "oracle_parity_incomplete",
            "chronological_challenge_complete": (
                "chronological_challenge_incomplete"
            ),
            "cross_fold_candidate_eligible": "no_cross_fold_candidate",
            "daily_stability_candidate_eligible": (
                "no_daily_stability_candidate"
            ),
            "source_manifest_complete": "source_manifest_incomplete",
        }
        return tuple(
            labels[name]
            for name, value in asdict(self).items()
            if not value
        )


@dataclass(frozen=True)
class ProviderPipHypothesis:
    hypothesis_id: str
    description: str
    period_totals: Mapping[str, Decimal]
    verified: bool = False

    def __post_init__(self) -> None:
        if not self.hypothesis_id.strip():
            raise ValueError("hypothesis_id cannot be empty")
        if not self.description.strip():
            raise ValueError("hypothesis description cannot be empty")
        if not isinstance(self.verified, bool):
            raise ValueError("hypothesis verified flag must be boolean")
        for period, amount in self.period_totals.items():
            if not str(period).strip():
                raise ValueError("hypothesis period cannot be empty")
            value = Decimal(str(amount))
            if not value.is_finite():
                raise ValueError("hypothesis totals must be finite")


def build_gold_research_artifacts(
    dataset,
    *,
    fold_plan: GoldFoldPlan,
    frontier_evaluations: Sequence[CandidateEvaluation],
    generation_rows: Sequence[Mapping[str, object]],
    gates: GoldEvidenceGates,
    provider_scorecard: Mapping[str, object],
    candidate_evaluations: Sequence[CandidateEvaluation] | None = None,
    provider_pip_hypotheses: Sequence[ProviderPipHypothesis] = (),
    run_metadata: Mapping[str, object] | None = None,
    candidate_rows_source: (
        Sequence[Mapping[str, object]] | pd.DataFrame | Path | None
    ) = None,
    candidate_population_size: int | None = None,
) -> ResearchArtifacts:
    """Build immutable evidence without comparing unlike financial units."""

    evaluations = _unique_evaluations(frontier_evaluations)
    candidates = _unique_evaluations(
        candidate_evaluations
        if candidate_evaluations is not None
        else frontier_evaluations
    )
    candidate_fingerprints = {
        item.genome.fingerprint for item in candidates
    }
    missing_frontier = {
        item.genome.fingerprint for item in evaluations
    }.difference(candidate_fingerprints)
    if missing_frontier:
        raise ValueError("frontier evaluations must exist in candidate population")
    if candidate_population_size is not None and candidate_population_size < 0:
        raise ValueError("candidate_population_size must be non-negative")
    population_size = (
        len(candidates)
        if candidate_population_size is None
        else candidate_population_size
    )
    hypotheses = _unique_hypotheses(provider_pip_hypotheses)
    blockers = list(gates.blockers)
    if not evaluations:
        blockers.append("frontier_empty")
    if any(item.blockers or item.net_eur is None for item in evaluations):
        blockers.append("frontier_evidence_incomplete")
    blockers = list(dict.fromkeys(blockers))
    ranking_allowed = not blockers

    ordered = (
        _ranked_evaluations(evaluations)
        if ranking_allowed
        else tuple(sorted(evaluations, key=lambda item: item.genome.fingerprint))
    )
    frontier = []
    for rank, evaluation in enumerate(ordered, start=1):
        row = {
            "strategy_fingerprint": evaluation.genome.fingerprint,
            "simulated_eur": _money_text(
                evaluation.net_eur,
                dataset.currency_digits,
            ),
            "max_drawdown_eur": _money_text(
                evaluation.max_drawdown_eur,
                dataset.currency_digits,
            ),
            "worst_day_eur": _money_text(
                evaluation.worst_day_eur,
                dataset.currency_digits,
            ),
            "participation_rate": round(evaluation.participation_rate, 8),
            "status": (
                "retrospective_only" if ranking_allowed else "diagnostic_only"
            ),
        }
        if ranking_allowed:
            row["retrospective_rank"] = rank
        frontier.append(row)

    complete_days = set(fold_plan.complete_days)
    research_paths = tuple(
        path for path in dataset.paths if str(path.day) in complete_days
    )
    path_by_signal = {str(path.signal_id): path for path in research_paths}
    actual_values = tuple(
        Decimal(str(path.actual_pnl_eur))
        for path in research_paths
        if path.actual_pnl_eur is not None
    )
    actual_known_total = sum(
        actual_values,
        start=Decimal(0),
    )
    actual_complete = len(actual_values) == len(research_paths)
    actual_financial_total = {
        "amount": _money_text(
            actual_known_total if actual_complete else None,
            dataset.currency_digits,
        ),
        "currency": dataset.account_currency,
        "kind": "observed_mt5",
    }
    if not actual_complete:
        actual_financial_total["known_amount"] = _money_text(
            actual_known_total,
            dataset.currency_digits,
        )
    provider_claims = _provider_claim_totals(provider_scorecard)
    distance_rows = _claim_distance_rows(
        provider_scorecard,
        hypotheses,
    )
    selection = {
        "ranking_allowed": ranking_allowed,
        "status": (
            "retrospective_ranked_requires_untouched_forward"
            if ranking_allowed
            else "diagnostic_only"
        ),
        "blockers": blockers,
        "selected_strategy_fingerprint": None,
        "promotion_eligible": False,
    }
    run_card = {
        "schema_version": 1,
        "research_kind": "gold_now_iterative_strategy_farm",
        "channel": "canal2",
        "signal_scope": "formal_telegram_now",
        "source_hashes": dict(sorted(dataset.source_hashes.items())),
        "eligible_signal_ids": list(dataset.eligible_signal_ids),
        "research_signal_ids": [
            str(path.signal_id) for path in research_paths
        ],
        "exclusions": {
            reason: list(signal_ids)
            for reason, signal_ids in sorted(dataset.exclusions.items())
        },
        "complete_days": list(fold_plan.complete_days),
        "incomplete_days": list(fold_plan.incomplete_days),
        "folds": [asdict(fold) for fold in fold_plan.folds],
        "evidence_gates": asdict(gates),
        "selection": selection,
        "actual_mt5_coverage": {
            "known_signal_count": len(actual_values),
            "research_signal_count": len(research_paths),
            "complete": actual_complete,
        },
        "units_contract": {
            "actual_mt5": dataset.account_currency,
            "counterfactual_simulation": dataset.account_currency,
            "provider_claim": "provider_pips",
            "provider_pips_are_not_account_currency": True,
        },
        "financial_totals": {
            "actual_mt5": actual_financial_total,
            "simulated_frontier": [
                {
                    "strategy_fingerprint": evaluation.genome.fingerprint,
                    "amount": _money_text(
                        evaluation.net_eur,
                        dataset.currency_digits,
                    ),
                    "currency": dataset.account_currency,
                    "kind": "counterfactual_simulation",
                }
                for evaluation in ordered
            ],
        },
        "provider_claim_totals": provider_claims,
        "provider_accounting_hypotheses": [
            {
                "hypothesis_id": item.hypothesis_id,
                "description": item.description,
                "verified": item.verified,
            }
            for item in hypotheses
        ],
        "candidate_population_size": population_size,
        "run_metadata": dict(run_metadata or {}),
    }
    return ResearchArtifacts(
        run_card=run_card,
        frontier=tuple(frontier),
        generation_rows=tuple(dict(row) for row in generation_rows),
        candidate_rows=(
            candidate_rows_source
            if candidate_rows_source is not None
            else _candidate_rows(
                candidates,
                currency_digits=dataset.currency_digits,
            )
        ),
        signal_rows=_signal_rows(
            candidates,
            path_by_signal,
            currency_digits=dataset.currency_digits,
        ),
        extra_json_files={
            "provider_claim_scorecard.json": dict(provider_scorecard),
        },
        extra_tables={
            "daily_totals.parquet": _daily_rows(
                candidates,
                research_paths,
                provider_claims,
                currency_digits=dataset.currency_digits,
            ),
            "provider_claim_distance.parquet": distance_rows,
        },
    )


def _unique_evaluations(
    evaluations: Sequence[CandidateEvaluation],
) -> tuple[CandidateEvaluation, ...]:
    by_fingerprint: dict[str, CandidateEvaluation] = {}
    for item in evaluations:
        fingerprint = item.genome.fingerprint
        if fingerprint in by_fingerprint:
            raise ValueError(f"duplicate frontier evaluation: {fingerprint}")
        by_fingerprint[fingerprint] = item
    return tuple(by_fingerprint.values())


def _unique_hypotheses(
    hypotheses: Sequence[ProviderPipHypothesis],
) -> tuple[ProviderPipHypothesis, ...]:
    identifiers: set[str] = set()
    ordered = []
    for item in hypotheses:
        if item.hypothesis_id in identifiers:
            raise ValueError(f"duplicate provider hypothesis: {item.hypothesis_id}")
        identifiers.add(item.hypothesis_id)
        ordered.append(item)
    return tuple(ordered)


def _ranked_evaluations(
    evaluations: Sequence[CandidateEvaluation],
) -> tuple[CandidateEvaluation, ...]:
    return tuple(sorted(
        evaluations,
        key=lambda item: (
            -Decimal(item.net_eur or 0),
            Decimal(item.max_drawdown_eur or 0),
            item.genome.fingerprint,
        ),
    ))


def _candidate_rows(
    evaluations: Sequence[CandidateEvaluation],
    *,
    currency_digits: int,
) -> tuple[dict[str, object], ...]:
    return tuple({
        "strategy_fingerprint": item.genome.fingerprint,
        "net_eur": _money_text(item.net_eur, currency_digits),
        "net_minor": _minor_units(item.net_eur, currency_digits),
        "max_drawdown_eur": _money_text(
            item.max_drawdown_eur,
            currency_digits,
        ),
        "worst_day_eur": _money_text(item.worst_day_eur, currency_digits),
        "participation_rate": item.participation_rate,
        "complexity": item.complexity,
        "blockers_json": json.dumps(item.blockers, separators=(",", ":")),
    } for item in evaluations)


def _signal_rows(
    evaluations: Sequence[CandidateEvaluation],
    path_by_signal: Mapping[str, object],
    *,
    currency_digits: int,
) -> tuple[dict[str, object], ...]:
    rows = []
    for evaluation in evaluations:
        for day, result in evaluation.results:
            path = path_by_signal.get(result.signal_id)
            actual = (
                None
                if path is None or path.actual_pnl_eur is None
                else Decimal(str(path.actual_pnl_eur))
            )
            rows.append({
                "strategy_fingerprint": evaluation.genome.fingerprint,
                "signal_id": result.signal_id,
                "day": day,
                "actual_mt5_eur": _money_text(actual, currency_digits),
                "simulated_eur": _money_text(result.pnl_eur, currency_digits),
                "pnl_eur": _money_text(result.pnl_eur, currency_digits),
                "exit_reason": result.exit_reason,
                "unfilled": result.unfilled,
                "blockers_json": json.dumps(
                    result.blockers,
                    separators=(",", ":"),
                ),
            })
    return tuple(rows)


def _daily_rows(
    evaluations: Sequence[CandidateEvaluation],
    paths: Sequence[object],
    provider_claims: Sequence[Mapping[str, object]],
    *,
    currency_digits: int,
) -> tuple[dict[str, object], ...]:
    actual_by_day: dict[str, Decimal | None] = {}
    for path in paths:
        day = str(path.day)
        if path.actual_pnl_eur is None:
            actual_by_day[day] = None
        elif actual_by_day.get(day, Decimal(0)) is not None:
            actual_by_day[day] = (
                actual_by_day.get(day, Decimal(0))
                + Decimal(str(path.actual_pnl_eur))
            )
    daily_claim = {
        str(item["period_start"]): item["amount"]
        for item in provider_claims
        if item.get("period_start") == item.get("period_end")
    }
    rows = []
    for evaluation in evaluations:
        simulated_by_day: dict[str, Decimal | None] = {}
        for day, result in evaluation.results:
            if result.pnl_eur is None:
                simulated_by_day[day] = None
            elif simulated_by_day.get(day, Decimal(0)) is not None:
                simulated_by_day[day] = (
                    simulated_by_day.get(day, Decimal(0))
                    + result.pnl_eur
                )
        for day in sorted(set(actual_by_day) | set(simulated_by_day)):
            rows.append({
                "strategy_fingerprint": evaluation.genome.fingerprint,
                "day": day,
                "actual_mt5_eur": _money_text(
                    actual_by_day.get(day),
                    currency_digits,
                ),
                "simulated_eur": _money_text(
                    simulated_by_day.get(day),
                    currency_digits,
                ),
                "provider_claim_pips": daily_claim.get(day),
            })
    return tuple(rows)


def _provider_claim_totals(
    scorecard: Mapping[str, object],
) -> tuple[dict[str, object], ...]:
    rows = []
    for summary in scorecard.get("summaries") or ():
        if not isinstance(summary, Mapping):
            continue
        claim = summary.get("claim") or {}
        if not isinstance(claim, Mapping) or claim.get("pips_gained") is None:
            continue
        rows.append({
            "provider_signal_id": str(summary.get("provider_signal_id") or ""),
            "period_start": claim.get("period_start"),
            "period_end": claim.get("period_end"),
            "amount": _decimal_text(claim["pips_gained"]),
            "unit": "provider_pips",
            "calibration_ready": bool(claim.get("calibration_ready")),
            "blockers": list(claim.get("blockers") or ()),
        })
    return tuple(rows)


def _claim_distance_rows(
    scorecard: Mapping[str, object],
    hypotheses: Sequence[ProviderPipHypothesis],
) -> tuple[dict[str, object], ...]:
    rows = []
    for summary in scorecard.get("summaries") or ():
        if not isinstance(summary, Mapping):
            continue
        claim = summary.get("claim") or {}
        if not isinstance(claim, Mapping):
            continue
        start = claim.get("period_start")
        end = claim.get("period_end")
        claimed = claim.get("pips_gained")
        if not start or not end or claimed is None:
            continue
        period = f"{start}:{end}"
        claim_value = Decimal(str(claimed))
        for hypothesis in hypotheses:
            hypothesis_value = hypothesis.period_totals.get(period)
            if hypothesis_value is None:
                continue
            hypothesis_value = Decimal(str(hypothesis_value))
            rows.append({
                "provider_signal_id": str(
                    summary.get("provider_signal_id") or ""
                ),
                "period_start": str(start),
                "period_end": str(end),
                "hypothesis_id": hypothesis.hypothesis_id,
                "provider_claim_pips": _decimal_text(claim_value),
                "hypothesis_pips": _decimal_text(hypothesis_value),
                "distance_pips": _decimal_text(
                    hypothesis_value - claim_value
                ),
                "hypothesis_verified": hypothesis.verified,
                "provider_claim_calibration_ready": bool(
                    claim.get("calibration_ready")
                ),
                "claim_blockers_json": json.dumps(
                    claim.get("blockers") or (),
                    separators=(",", ":"),
                ),
                "usable_for_strategy_selection": False,
            })
    return tuple(rows)


def _money_text(value: Decimal | None, digits: int) -> str | None:
    if value is None:
        return None
    quantum = Decimal(1).scaleb(-digits)
    return format(Decimal(value).quantize(quantum), f".{digits}f")


def _minor_units(value: Decimal | None, digits: int) -> int | None:
    if value is None:
        return None
    return int(Decimal(value).scaleb(digits).quantize(Decimal(1)))


def _decimal_text(value: object) -> str:
    normalized = format(Decimal(str(value)), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"
