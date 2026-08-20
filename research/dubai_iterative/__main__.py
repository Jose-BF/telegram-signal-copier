"""Command-line entry point for bounded Dubai strategy research."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Sequence

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .contracts import SearchBudget, SearchSpace, StrategyGenome
from .certification import certify_finalists
from .dataset import VerifiedParquetTickSource, load_dubai_dataset
from .engine import ExecutionAssumptions, SimulationResult
from .fast_engine import FastEvaluator
from .oracle import ExecutionScenario
from .portfolio import build_portfolio_tape
from .reporting import ResearchArtifacts, publish_run
from .search import (
    ChronologicalFold,
    ChronologicalSearchReport,
    DEFAULT_DUBAI_FOLDS,
    GenerationProgress,
    classify_retrospective,
    cross_validate_frontier_candidates,
    run_chronological_search,
    run_search,
)
from .statistics import assess_daily_stability


@dataclass(frozen=True)
class _TinyPath:
    signal_id: str
    day: str


@dataclass(frozen=True)
class _TinyDataset:
    paths: tuple[_TinyPath, ...]
    eligible_signal_ids: tuple[str, ...]
    source_hashes: dict[str, str]
    exclusions: dict[str, tuple[str, ...]]
    actual_pnl_eur: Decimal
    max_hold_minutes: int = 240


class _CandidateSpool:
    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            prefix="dubai-candidates-",
            suffix=".parquet",
            dir=directory,
            delete=False,
        )
        handle.close()
        self.path = Path(handle.name)
        self.path.unlink(missing_ok=True)
        self.writer: pq.ParquetWriter | None = None

    def append(self, fold, generation, evaluations) -> None:
        rows = []
        for item in evaluations:
            rows.append({
                "fold": fold.name,
                "generation": generation,
                "fingerprint": item.genome.fingerprint,
                "genome_json": json.dumps(item.genome.to_dict(), sort_keys=True, separators=(",", ":")),
                "net_eur": _number(item.net_eur),
                "max_drawdown_eur": _number(item.max_drawdown_eur),
                "worst_day_eur": _number(item.worst_day_eur),
                "gross_profit_eur": _number(item.gross_profit_eur),
                "gross_loss_eur": _number(item.gross_loss_eur),
                "profit_factor": item.profit_factor,
                "positive_day_concentration": item.positive_day_concentration,
                "normalized_net_per_001": _number(item.normalized_net_per_001),
                "normalized_max_drawdown_per_001": _number(item.normalized_max_drawdown_per_001),
                "normalized_worst_day_per_001": _number(item.normalized_worst_day_per_001),
                "max_signal_exposure": item.max_signal_exposure,
                "total_signal_count": item.total_signal_count,
                "filled_signal_count": item.filled_signal_count,
                "participation_rate": item.participation_rate,
                "complexity": item.complexity,
                "blockers_json": json.dumps(item.blockers, separators=(",", ":")),
            })
        if not rows:
            return
        table = pa.Table.from_pandas(pd.DataFrame(rows), preserve_index=False)
        if self.writer is None:
            self.writer = pq.ParquetWriter(
                self.path,
                table.schema,
                compression="zstd",
            )
        self.writer.write_table(table)

    def close(self) -> Path:
        if self.writer is not None:
            self.writer.close()
            self.writer = None
        elif not self.path.exists():
            pd.DataFrame(columns=(
                "fold", "generation", "fingerprint", "genome_json", "net_eur",
                "max_drawdown_eur", "worst_day_eur", "gross_profit_eur",
                "gross_loss_eur", "profit_factor", "positive_day_concentration",
                "normalized_net_per_001", "normalized_max_drawdown_per_001",
                "normalized_worst_day_per_001", "max_signal_exposure", "complexity",
                "total_signal_count", "filled_signal_count", "participation_rate",
                "blockers_json",
            )).to_parquet(self.path, index=False)
        return self.path


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    budget = SearchBudget(
        max_generations=args.max_generations,
        max_evaluations=args.max_evaluations,
        max_wall_seconds=args.max_wall_seconds,
        patience_generations=args.patience_generations,
        max_lineage_depth=args.max_lineage_depth,
    )
    search_space = SearchSpace(
        min_total_volume=args.min_total_volume,
        max_total_volume=args.max_total_volume,
        max_legs=args.max_legs,
        volume_step=args.volume_step,
        max_entry_expiry_min=args.max_entry_expiry_minutes,
        max_time_exit_min=args.max_time_exit_minutes,
        max_path_horizon_min=args.max_hold_minutes,
    )
    initial_genomes = (
        ()
        if args.fixture is not None
        else _load_parent_genomes(args.parent_parquet, args.parent_limit)
    )
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    spool = _CandidateSpool(output_root)
    search_execution = ExecutionAssumptions(
        latency_ms=args.search_latency_ms,
        entry_slippage=args.search_entry_slippage,
        exit_slippage=args.search_exit_slippage,
        spread_addition=args.search_spread_addition,
    )
    search_scenario = ExecutionScenario(
        "search_execution",
        latency_ms=args.search_latency_ms,
        entry_slippage=args.search_entry_slippage,
        exit_slippage=args.search_exit_slippage,
        spread_addition=args.search_spread_addition,
    )
    experiment_context = {
        "engine": "numba_fixed_point_v2",
        "grammar_version": 2,
        "execution": asdict(search_execution),
    }

    def progress(update: GenerationProgress) -> None:
        if not args.progress:
            return
        print(
            f"[{update.fold}] Generacion {update.generation}/{update.max_generations} | "
            f"evaluadas {update.evaluated}/{update.max_evaluations} | "
            f"frente {update.frontier_size} | sin mejora {update.stale_generations}",
            flush=True,
        )

    try:
        certifications = {}
        capital_risk_rejections = {}
        certification_candidates_considered = 0
        cross_fold_validation = None
        if args.fixture == "tiny":
            dataset = _tiny_dataset()
            fold = ChronologicalFold(
                "tiny",
                "2026-07-27",
                "2026-07-28",
                "2026-07-29",
                "2026-07-29",
            )
            one = run_search(
                dataset,
                fold=fold,
                budget=budget,
                search_space=search_space,
                output_dir=output_root / ".checkpoints" / fold.name,
                evaluator=_tiny_evaluator,
                seed=args.seed,
                population_size=args.population_size,
                progress_callback=progress,
                evaluation_callback=spool.append,
                experiment_context=experiment_context,
                workers=args.workers,
                initial_genomes=initial_genomes,
            )
            search_report = ChronologicalSearchReport((one,))
        else:
            dataset = _real_dataset(args)
            evaluator = FastEvaluator(execution=search_execution)
            search_report = run_chronological_search(
                dataset,
                folds=DEFAULT_DUBAI_FOLDS,
                budget=budget,
                search_space=search_space,
                output_dir=output_root / ".checkpoints",
                evaluator=evaluator,
                seed=args.seed,
                population_size=args.population_size,
                progress_callback=progress,
                evaluation_callback=spool.append,
                experiment_context=experiment_context,
                workers=args.workers,
                initial_genomes=initial_genomes,
            )
            cross_fold_validation = cross_validate_frontier_candidates(
                dataset,
                search_report,
                evaluator=evaluator,
                workers=args.workers,
                progress_callback=(
                    lambda completed, total: print(
                        "[Cross-fold] Verificadas "
                        f"{completed}/{total} candidatas",
                        flush=True,
                    )
                    if args.progress
                    and (completed == total or completed % 25 == 0)
                    else None
                ),
            )
            if args.progress:
                print(
                    "[Cross-fold] Candidatas "
                    f"{cross_fold_validation.considered_count} | aptas "
                    f"{len(cross_fold_validation.eligible)} | rechazadas "
                    f"{len(cross_fold_validation.rejected)}",
                    flush=True,
                )
            certification_worlds = ()
            portfolio_tape = None
            if cross_fold_validation.eligible and args.oracle_finalists > 0:
                if args.progress:
                    print(
                        "[Portfolio] Construyendo cinta canonica verificada...",
                        flush=True,
                    )
                portfolio_tape = _verified_portfolio_tape(args, dataset)
                certification_worlds = _certification_worlds(
                    search_execution,
                    search_scenario,
                )
                if args.progress:
                    print(
                        "[Portfolio] Cinta lista: "
                        f"{len(portfolio_tape.times_ns)} ticks | "
                        f"bloqueos {len(portfolio_tape.blockers)}",
                        flush=True,
                    )
            certification_batch = certify_finalists(
                dataset,
                search_report,
                evaluator=evaluator,
                limit=args.oracle_finalists,
                execution_scenario=search_scenario,
                initial_capital_eur=Decimal(str(args.capital_eur)),
                maximum_loss_fraction=Decimal(str(
                    args.maximum_loss_fraction
                )),
                maximum_concurrent_signals=(
                    args.maximum_concurrent_signals
                ),
                ranked_genomes=tuple(
                    item.genome
                    for item in cross_fold_validation.eligible
                ),
                certification_worlds=certification_worlds,
                world_evaluator_factory=(
                    lambda execution: FastEvaluator(execution=execution)
                ),
                portfolio_tape=portfolio_tape,
                progress_callback=(
                    lambda index, total, genome: print(
                        f"[Oracle] Candidata {index}/{total}: "
                        f"{genome.fingerprint[:12]}",
                        flush=True,
                    )
                    if args.progress
                    else None
                ),
            )
            certifications = certification_batch
            capital_risk_rejections = (
                certification_batch.risk_rejections
            )
            certification_candidates_considered = (
                certification_batch.considered_count
            )
        candidate_path = spool.close()
        artifacts = _build_artifacts(
            dataset,
            search_report,
            budget=budget,
            search_space=search_space,
            seed=args.seed,
            candidate_path=candidate_path,
            certifications=certifications,
            capital_risk_rejections=capital_risk_rejections,
            certification_candidates_considered=(
                certification_candidates_considered
            ),
            fixture=args.fixture is not None,
            search_execution=search_execution,
            workers=args.workers,
            initial_capital_eur=args.capital_eur,
            maximum_loss_fraction=args.maximum_loss_fraction,
            maximum_concurrent_signals=args.maximum_concurrent_signals,
            oracle_finalists_requested=args.oracle_finalists,
            cross_fold_validation=cross_fold_validation,
            initial_genomes=initial_genomes,
        )
        published = publish_run(artifacts, output_root)
    finally:
        candidate_path = spool.close()
        candidate_path.unlink(missing_ok=True)

    reasons = [item.stop_reason for item in search_report.fold_reports]
    print(f"Parada: {', '.join(reasons)}", flush=True)
    print(
        f"Estrategias evaluadas: {search_report.total_evaluations}",
        flush=True,
    )
    print(f"Resultado: {published.run_dir}", flush=True)
    return 130 if "user_interrupt" in reasons else 0


def _real_dataset(args):
    money_contract = json.loads(Path(args.money_contract).read_text(encoding="utf-8"))
    conversion = money_contract.get("conversion") or {}
    orientation = conversion.get("orientation")
    conversion_source = None
    if orientation != "identity":
        conversion_source = VerifiedParquetTickSource(
            Path(args.conversion_tick_cache),
            expected_symbol=str(conversion.get("symbol") or "EURUSD"),
        )
    return load_dubai_dataset(
        replay_path=Path(args.replay_path),
        audit_path=Path(args.audit_path),
        market_ticks=VerifiedParquetTickSource(
            Path(args.market_tick_cache),
            expected_symbol="XAUUSD",
        ),
        conversion_ticks=conversion_source,
        money_contract=money_contract,
        from_date=args.from_date,
        to_date=args.to_date,
        max_hold_minutes=args.max_hold_minutes,
    )


def _verified_portfolio_tape(args, dataset):
    contract = json.loads(
        Path(args.money_contract).read_text(encoding="utf-8")
    )
    conversion = contract.get("conversion") or {}
    orientation = conversion.get("orientation")
    conversion_source = None
    if orientation != "identity":
        conversion_source = VerifiedParquetTickSource(
            Path(args.conversion_tick_cache),
            expected_symbol=str(conversion.get("symbol") or "EURUSD"),
        )
    return build_portfolio_tape(
        dataset.paths,
        market_tick_source=VerifiedParquetTickSource(
            Path(args.market_tick_cache),
            expected_symbol="XAUUSD",
        ),
        conversion_tick_source=conversion_source,
        max_conversion_age_ms=int(
            conversion.get("max_quote_age_ms") or 5_000
        ),
        max_conversion_interval_ms=int(
            conversion.get("max_quote_interval_ms")
            or conversion.get("max_quote_age_ms")
            or 5_000
        ),
    )


def _certification_worlds(search_execution, search_scenario):
    return (
        (
            "zero_cost_zero_latency",
            ExecutionAssumptions(),
            ExecutionScenario("zero_cost_zero_latency"),
        ),
        (
            "latency_250ms",
            ExecutionAssumptions(latency_ms=250),
            ExecutionScenario("latency_250ms", latency_ms=250),
        ),
        ("search_execution", search_execution, search_scenario),
        (
            "latency_1s",
            ExecutionAssumptions(latency_ms=1_000),
            ExecutionScenario("latency_1s", latency_ms=1_000),
        ),
        (
            "latency_2s",
            ExecutionAssumptions(latency_ms=2_000),
            ExecutionScenario("latency_2s", latency_ms=2_000),
        ),
        (
            "adverse_costs",
            ExecutionAssumptions(
                latency_ms=500,
                entry_slippage=0.10,
                exit_slippage=0.10,
                spread_addition=0.10,
            ),
            ExecutionScenario(
                "adverse_costs",
                latency_ms=500,
                entry_slippage=0.10,
                exit_slippage=0.10,
                spread_addition=0.10,
            ),
        ),
    )


def _build_artifacts(
    dataset,
    search_report,
    *,
    budget,
    search_space,
    seed,
    candidate_path,
    certifications=None,
    capital_risk_rejections=None,
    certification_candidates_considered=0,
    fixture=False,
    search_execution=None,
    workers=1,
    initial_capital_eur=500.0,
    maximum_loss_fraction=0.25,
    maximum_concurrent_signals=3,
    oracle_finalists_requested=1,
    cross_fold_validation=None,
    initial_genomes=(),
):
    if certifications is None:
        certifications = {}
    capital_risk_rejections = capital_risk_rejections or {}
    loaded_signal_ids = tuple(path.signal_id for path in dataset.paths)
    eligible_signal_ids = tuple(
        getattr(dataset, "eligible_signal_ids", loaded_signal_ids)
    )
    coverage_complete = bool(getattr(
        dataset,
        "coverage_complete",
        loaded_signal_ids == eligible_signal_ids,
    ))
    generation_rows = tuple(
        asdict(summary)
        for report in search_report.fold_reports
        for summary in report.generation_summaries
    )
    frontier_rows = []
    signal_rows = []
    confidences = []
    for report in search_report.fold_reports:
        challenge_by_fingerprint = {
            item.genome.fingerprint: item
            for item in report.challenge_evaluations
        }
        for evaluation in report.frontier:
            challenge = challenge_by_fingerprint.get(evaluation.genome.fingerprint)
            development_stability = _daily_stability_summary(
                evaluation,
                seed=seed,
            )
            challenge_stability = (
                None
                if challenge is None
                else _daily_stability_summary(challenge, seed=seed)
            )
            challenge_net = None if challenge is None else challenge.net_eur
            assessment = classify_retrospective(
                train_net=evaluation.net_eur or Decimal("0"),
                challenge_net=challenge_net or Decimal("0"),
            )
            certified = certifications.get(evaluation.genome.fingerprint)
            risk_rejection = capital_risk_rejections.get(
                evaluation.genome.fingerprint
            )
            capital_risk = (
                certified.capital_risk
                if certified is not None
                else risk_rejection
            )
            certification_status = (
                "fixture"
                if fixture
                else (
                    "capital_risk_rejected"
                    if risk_rejection is not None
                    else (
                        "not_selected"
                        if certified is None
                        else certified.certificate.status
                    )
                )
            )
            if certified is not None and not coverage_complete:
                certification_status = "blocked_dataset_coverage"
            stress_summary = (
                {"status": "fixture"}
                if fixture
                else (
                    {"status": "not_selected"}
                    if certified is None
                    else _stress_summary(certified.stress_report)
                )
            )
            certified_evidence_complete = bool(
                certified is not None
                and certified.evidence_complete
                and coverage_complete
            )
            robustness_eligible = bool(
                certified_evidence_complete
                and certified is not None
                and certified.robustness_eligible
            )
            confidence = assessment.confidence
            if not fixture and risk_rejection is not None:
                confidence = "retrospective_capital_risk_failed"
            elif not fixture and confidence == "demo_candidate":
                confidence = (
                    "retrospective_positive_replay_certified_"
                    + ("stress_passed" if robustness_eligible else "stress_failed")
                    if certified_evidence_complete
                    else "retrospective_positive_uncertified"
                )
            elif not fixture and certified_evidence_complete:
                confidence = (
                    f"{confidence}_replay_certified_"
                    + ("stress_passed" if robustness_eligible else "stress_failed")
                )
            confidences.append(confidence)
            frontier_rows.append({
                "fold": report.fold.name,
                "fingerprint": evaluation.genome.fingerprint,
                "plain_strategy": _plain_strategy(evaluation.genome),
                "genome": evaluation.genome.to_dict(),
                "development_net_eur": _number(evaluation.net_eur),
                "challenge_net_eur": _number(challenge_net),
                "max_drawdown_eur": _number(evaluation.max_drawdown_eur),
                "worst_day_eur": _number(evaluation.worst_day_eur),
                "normalized_net_per_001": _number(evaluation.normalized_net_per_001),
                "normalized_max_drawdown_per_001": _number(evaluation.normalized_max_drawdown_per_001),
                "normalized_worst_day_per_001": _number(evaluation.normalized_worst_day_per_001),
                "profit_factor": _number(evaluation.profit_factor),
                "profit_factor_infinite": evaluation.profit_factor is not None and math.isinf(evaluation.profit_factor),
                "max_signal_exposure": evaluation.max_signal_exposure,
                "total_signal_count": evaluation.total_signal_count,
                "filled_signal_count": evaluation.filled_signal_count,
                "participation_rate": evaluation.participation_rate,
                "complexity": evaluation.complexity,
                "development_daily_stability": development_stability,
                "challenge_daily_stability": challenge_stability,
                "confidence": confidence,
                "oracle_status": certification_status,
                "oracle_mismatch_count": (
                    0
                    if certified is None
                    else len(certified.certificate.mismatches)
                ),
                "execution_stress": stress_summary,
                "capital_risk": _capital_risk_summary(capital_risk),
                "evidence_complete": bool(
                    certified_evidence_complete
                ),
                "promotion_eligible": False,
                "robustness_eligible": robustness_eligible,
            })
            signal_rows.extend(_signal_rows(report.fold.name, "development", evaluation))
            if challenge is not None:
                signal_rows.extend(_signal_rows(report.fold.name, "challenge", challenge))
    confidence = _overall_strategy_confidence(
        search_report,
        certifications,
        coverage_complete=coverage_complete,
        fixture=fixture,
        fixture_row_confidences=tuple(confidences),
        capital_risk_rejections=capital_risk_rejections,
        cross_fold_validation=cross_fold_validation,
    )
    exclusions = {
        key: list(values)
        for key, values in getattr(dataset, "exclusions", {}).items()
    }
    run_card = {
        "schema_version": 1,
        "source_hashes": dict(sorted(dataset.source_hashes.items())),
        "signal_ids": list(loaded_signal_ids),
        "eligible_signal_ids": list(eligible_signal_ids),
        "signal_coverage": {
            "eligible": len(eligible_signal_ids),
            "loaded": len(loaded_signal_ids),
            "complete": coverage_complete,
        },
        "exclusions": exclusions,
        "seed": seed,
        "folds": [asdict(item.fold) for item in search_report.fold_reports],
        "budget": asdict(budget),
        "search_space": asdict(search_space),
        "imported_parent_fingerprints": [
            item.fingerprint for item in initial_genomes
        ],
        "imported_parent_role": (
            "research_seed_only_full_sample_origin_not_oos"
            if initial_genomes
            else "not_used"
        ),
        "search_execution": asdict(
            search_execution or ExecutionAssumptions()
        ),
        "workers": workers,
        "max_hold_minutes": getattr(dataset, "max_hold_minutes", None),
        "grammar_version": 2,
        "selection_objective": (
            "positive_full_window_and_every_chronological_challenge_"
            "then_normalized_rule_quality"
        ),
        "cross_fold_validation": _cross_fold_summary(
            cross_fold_validation
        ),
        "account_risk": {
            "enabled": not fixture,
            "initial_capital_eur": initial_capital_eur,
            "maximum_loss_fraction": maximum_loss_fraction,
            "maximum_loss_eur": _number(
                Decimal(str(initial_capital_eur))
                * Decimal(str(maximum_loss_fraction))
            ),
            "maximum_concurrent_signals": maximum_concurrent_signals,
            "continuous_market_only": True,
            "margin_contract_verified": False,
        },
        "daily_stability_method": {
            "bootstrap_samples": 10_000,
            "resampling_unit": "complete_trading_day",
            "seed": seed,
            "validation_role": (
                "retrospective_stability_only_not_untouched_oos"
            ),
        },
        "confidence": confidence,
        "actual_pnl_eur": _number(getattr(dataset, "actual_pnl_eur", None)),
        "loaded_actual_pnl_eur": _number(getattr(
            dataset,
            "loaded_actual_pnl_eur",
            getattr(dataset, "actual_pnl_eur", None),
        )),
        "stop_reasons": [item.stop_reason for item in search_report.fold_reports],
        "total_evaluations": search_report.total_evaluations,
        "engine": "numba_fixed_point_v2",
        "oracle": "independent_scalar_v1",
        "oracle_finalists_requested": (
            0 if fixture else oracle_finalists_requested
        ),
        "oracle_finalists_evaluated": len(certifications),
        "oracle_finalists_certified": sum(
            bool(item.evidence_complete)
            for item in certifications.values()
        ),
        "certification_candidates_considered": (
            certification_candidates_considered
        ),
        "capital_risk_rejections": {
            fingerprint: _capital_risk_summary(item)
            for fingerprint, item in sorted(
                capital_risk_rejections.items()
            )
        },
        "oracle_certificates": {
            fingerprint: {
                "status": item.certificate.status,
                "mismatch_count": len(item.certificate.mismatches),
                "evidence_complete": bool(
                    item.evidence_complete and coverage_complete
                ),
                "robustness_eligible": bool(
                    item.robustness_eligible and coverage_complete
                ),
                "elapsed_seconds": round(item.elapsed_seconds, 6),
                "certified_execution": _certified_execution_summary(
                    item,
                    search_execution or ExecutionAssumptions(),
                ),
                "execution_stress": _stress_summary(item.stress_report),
                "world_certification": _world_certification_summary(
                    item.world_certification
                ),
                "capital_risk": _capital_risk_summary(item.capital_risk),
            }
            for fingerprint, item in sorted(certifications.items())
        },
        "live_code_changed": False,
        "automatic_deployment": False,
    }
    return ResearchArtifacts(
        run_card=run_card,
        frontier=tuple(frontier_rows),
        generation_rows=generation_rows,
        candidate_rows=candidate_path,
        signal_rows=tuple(signal_rows),
    )


def _overall_strategy_confidence(
    search_report,
    certifications,
    *,
    coverage_complete: bool,
    fixture: bool,
    fixture_row_confidences=(),
    capital_risk_rejections=None,
    cross_fold_validation=None,
) -> str:
    if fixture:
        return (
            "demo_candidate"
            if fixture_row_confidences
            and all(item == "demo_candidate" for item in fixture_row_confidences)
            else "retrospective_unstable"
        )
    if not coverage_complete:
        return "evidence_incomplete"

    capital_risk_rejections = capital_risk_rejections or {}
    cross_fold_eligible: set[str] | None = None
    if cross_fold_validation is not None:
        cross_fold_eligible = {
            item.genome.fingerprint
            for item in cross_fold_validation.eligible
        }
        if not cross_fold_eligible:
            return "retrospective_cross_fold_failed"

    replay_certified = {
        fingerprint
        for fingerprint, item in certifications.items()
        if item.evidence_complete
    }
    if not replay_certified:
        return (
            "retrospective_capital_risk_failed"
            if capital_risk_rejections
            else "retrospective_uncertified"
        )

    stress_passed = {
        fingerprint
        for fingerprint in replay_certified
        if bool(getattr(certifications[fingerprint], "robustness_eligible", False))
    }
    if cross_fold_eligible is not None:
        consistent = replay_certified & cross_fold_eligible
    else:
        consistent: set[str] = set()
        for fingerprint in replay_certified:
            survived_every_fold = True
            for fold in search_report.fold_reports:
                development = next(
                    (
                        item
                        for item in fold.frontier
                        if item.genome.fingerprint == fingerprint
                    ),
                    None,
                )
                challenge = next(
                    (
                        item
                        for item in fold.challenge_evaluations
                        if item.genome.fingerprint == fingerprint
                    ),
                    None,
                )
                if (
                    development is None
                    or challenge is None
                    or development.net_eur is None
                    or challenge.net_eur is None
                    or development.net_eur <= 0
                    or challenge.net_eur <= 0
                ):
                    survived_every_fold = False
                    break
            if survived_every_fold:
                consistent.add(fingerprint)
    if consistent & stress_passed:
        return "retrospective_consistent_replay_certified_stress_passed"
    if consistent:
        return "retrospective_consistent_replay_certified_stress_failed"
    if stress_passed:
        return "retrospective_unstable_replay_certified_stress_passed"
    return "retrospective_unstable_replay_certified_stress_failed"


def _cross_fold_summary(validation):
    validation_role = (
        "retrospective_segment_robustness_not_untouched_oos"
    )
    if validation is None:
        return {
            "status": "not_run",
            "validation_role": validation_role,
            "considered": 0,
            "eligible_count": 0,
            "rejected_count": 0,
            "eligible": [],
        }

    eligible = tuple(validation.eligible)
    return {
        "status": "passed" if eligible else "no_eligible_candidate",
        "validation_role": validation_role,
        "considered": validation.considered_count,
        "eligible_count": len(eligible),
        "rejected_count": len(validation.rejected),
        "minimum_participation_required": 0.50,
        "positive_challenge_ratio_required": 1.0,
        "eligible": [
            {
                "fingerprint": item.genome.fingerprint,
                "worst_net_eur": _number(item.worst_net_eur),
                "maximum_drawdown_eur": _number(item.maximum_drawdown_eur),
                "worst_challenge_net_eur": _number(
                    item.worst_challenge_net_eur
                ),
                "positive_challenges": item.positive_challenges,
                "challenge_count": item.challenge_count,
                "positive_challenge_ratio": item.positive_challenge_ratio,
                "minimum_participation": item.minimum_participation,
            }
            for item in eligible
        ],
    }


def _signal_rows(fold, phase, evaluation):
    return [
        {
            "fold": fold,
            "phase": phase,
            "fingerprint": evaluation.genome.fingerprint,
            "day": day,
            "signal_id": result.signal_id,
            "pnl_eur": _number(result.pnl_eur),
            "exit_reason": result.exit_reason,
            "max_favourable_eur": _number(result.max_favourable_eur),
            "max_adverse_eur": _number(result.max_adverse_eur),
            "blockers": json.dumps(result.blockers, separators=(",", ":")),
            "unfilled": result.unfilled,
            "filled_volume": result.filled_volume,
            "entry_count": len(result.entries),
            "exit_count": len(result.exits),
            "confidence_layer": result.confidence_layer,
        }
        for day, result in evaluation.results
    ]


def _plain_strategy(genome: StrategyGenome) -> str:
    exposure = sum(genome.volume_weights)
    return (
        f"Entrada {genome.entry_mode}; {genome.leg_count} posiciones, "
        f"{exposure:.2f} lotes totales; salida {genome.target_mode}; "
        f"stop {genome.stop_mode}; BE {genome.be_mode}; "
        f"cierre maximo {genome.time_exit_min} min"
    )


def _stress_summary(report) -> dict[str, object]:
    return {
        "status": "passed" if report.promotion_eligible else "failed",
        "base_world": {
            "name": "zero_cost_zero_latency",
            "latency_ms": 0,
            "entry_slippage": 0.0,
            "exit_slippage": 0.0,
            "spread_addition": 0.0,
        },
        "base_net_eur": _number(report.base_net_eur),
        "base_blockers": list(report.base_blockers),
        "scenarios": [
            {
                "name": item.scenario.name,
                "latency_ms": item.scenario.latency_ms,
                "entry_slippage": item.scenario.entry_slippage,
                "exit_slippage": item.scenario.exit_slippage,
                "spread_addition": item.scenario.spread_addition,
                "net_eur": _number(item.net_eur),
                "blockers": list(item.blockers),
            }
            for item in report.scenarios
        ],
    }


def _certified_execution_summary(certification, execution):
    values = tuple(
        getattr(result, "pnl_eur", None)
        for result in certification.fast_results
    )
    complete = all(value is not None for value in values) and all(
        not tuple(getattr(result, "blockers", ()) or ())
        for result in certification.fast_results
    )
    net = (
        sum(values, start=Decimal("0"))
        if complete
        else None
    )
    return {
        "name": "search_execution",
        "latency_ms": execution.latency_ms,
        "entry_slippage": execution.entry_slippage,
        "exit_slippage": execution.exit_slippage,
        "spread_addition": execution.spread_addition,
        "net_eur": _number(net),
        "evidence_complete": complete,
    }


def _world_certification_summary(report):
    if report is None:
        return {"status": "not_run", "worlds": []}
    return {
        "status": report.status,
        "certified_worlds": report.certified_worlds,
        "world_count": report.world_count,
        "worlds": [
            {
                "name": item.name,
                "latency_ms": item.oracle_scenario.latency_ms,
                "entry_slippage": item.oracle_scenario.entry_slippage,
                "exit_slippage": item.oracle_scenario.exit_slippage,
                "spread_addition": item.oracle_scenario.spread_addition,
                "oracle_status": item.certificate.status,
                "oracle_mismatch_count": len(item.certificate.mismatches),
                "net_eur": _number(item.net_eur),
                "blockers": list(item.blockers),
                "portfolio": _portfolio_summary(item.portfolio),
            }
            for item in report.worlds
        ],
    }


def _portfolio_summary(assessment):
    if assessment is None:
        return {"status": "not_run"}
    return {
        "status": (
            "passed" if assessment.evidence_complete else "blocked"
        ),
        "net_eur": _number(assessment.net_eur),
        "peak_equity_eur": _number(assessment.peak_equity_eur),
        "minimum_equity_eur": _number(assessment.minimum_equity_eur),
        "max_drawdown_eur": _number(assessment.max_drawdown_eur),
        "max_concurrent_volume": assessment.max_concurrent_volume,
        "max_concurrent_signals": assessment.max_concurrent_signals,
        "timeline_points": assessment.timeline_points,
        "blockers": list(assessment.blockers),
    }


def _daily_stability_summary(evaluation, *, seed):
    assessment = assess_daily_stability(
        (
            (day, result.pnl_eur)
            for day, result in evaluation.results
        ),
        samples=10_000,
        seed=seed,
    )
    return {
        "evidence_complete": assessment.evidence_complete,
        "day_count": len(assessment.day_totals),
        "observed_net_eur": _number(assessment.observed_net_eur),
        "worst_day_eur": _number(assessment.worst_day_eur),
        "best_day_eur": _number(assessment.best_day_eur),
        "positive_days": assessment.positive_days,
        "losing_days": assessment.losing_days,
        "leave_one_day_out_worst_eur": _number(
            assessment.leave_one_day_out_worst_eur
        ),
        "leave_one_day_out_positive_ratio": (
            assessment.leave_one_day_out_positive_ratio
        ),
        "largest_positive_day_share": assessment.largest_positive_day_share,
        "bootstrap_samples": assessment.bootstrap_samples,
        "bootstrap_probability_positive": (
            assessment.bootstrap_probability_positive
        ),
        "bootstrap_p05_eur": _number(assessment.bootstrap_p05_eur),
        "bootstrap_median_eur": _number(assessment.bootstrap_median_eur),
        "bootstrap_p95_eur": _number(assessment.bootstrap_p95_eur),
        "blockers": list(assessment.blockers),
    }


def _tiny_dataset():
    paths = (
            _TinyPath("train_1", "2026-07-27"),
            _TinyPath("train_2", "2026-07-28"),
            _TinyPath("challenge_1", "2026-07-29"),
        )
    return _TinyDataset(
        paths=paths,
        eligible_signal_ids=tuple(path.signal_id for path in paths),
        source_hashes={"fixture": "dubai-iterative-tiny-v1"},
        exclusions={},
        actual_pnl_eur=Decimal("1.50"),
    )


def _tiny_evaluator(path, genome):
    exposure = Decimal(str(sum(genome.volume_weights)))
    pnl = (Decimal("1.00") + exposure).quantize(Decimal("0.01"))
    return SimulationResult(
        signal_id=path.signal_id,
        strategy_fingerprint=genome.fingerprint,
        confidence_layer="fixture",
        entries=(),
        exits=(),
        pnl_eur=pnl,
        exit_reason="fixture_close",
        max_favourable_eur=pnl,
        max_adverse_eur=Decimal("0.00"),
        max_floating_drawdown_eur=Decimal("0.00"),
        max_favourable_move=1.0,
        max_adverse_move=0.0,
        blockers=(),
        last_tick_index=1,
        unfilled=False,
        filled_volume=float(exposure),
    )


def _number(value):
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _capital_risk_summary(assessment):
    if assessment is None:
        return {"status": "not_assessed"}
    return {
        "status": (
            "passed" if assessment.risk_eligible else "failed"
        ),
        "initial_capital_eur": _number(
            assessment.initial_capital_eur
        ),
        "risk_limit_eur": _number(assessment.risk_limit_eur),
        "planned_volume": assessment.planned_volume,
        "aggregate_planned_volume": (
            assessment.aggregate_planned_volume
        ),
        "maximum_concurrent_signals": (
            assessment.maximum_concurrent_signals
        ),
        "configured_maximum_concurrent_signals": (
            assessment.configured_maximum_concurrent_signals
        ),
        "observed_maximum_concurrent_signals": (
            assessment.observed_maximum_concurrent_signals
        ),
        "loss_basis": assessment.loss_basis,
        "single_signal_worst_loss_eur": _number(
            assessment.single_signal_worst_loss_eur
        ),
        "worst_loss_eur": _number(assessment.worst_loss_eur),
        "worst_loss_fraction": _number(
            assessment.worst_loss_fraction
        ),
        "continuous_market_only": assessment.continuous_market_only,
        "risk_eligible": assessment.risk_eligible,
        "blockers": list(assessment.blockers),
    }


def _load_parent_genomes(path_text, limit):
    if not path_text:
        return ()
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("parent_limit must be a positive integer")

    frame = pd.read_parquet(Path(path_text))
    required = {"genome_json", "positive_challenges", "worst_net_eur"}
    if not required.issubset(frame.columns):
        missing = ",".join(sorted(required - set(frame.columns)))
        raise ValueError(f"parent parquet is missing columns: {missing}")
    if "rule_eligible" in frame.columns:
        frame = frame[frame["rule_eligible"].fillna(False).astype(bool)]
    elif "eligible" in frame.columns:
        frame = frame[frame["eligible"].fillna(False).astype(bool)]
    if "capital_eligible" in frame.columns:
        frame = frame[frame["capital_eligible"].fillna(False).astype(bool)]

    ranking = ["positive_challenges"]
    if "worst_return_over_drawdown" in frame.columns:
        ranking.append("worst_return_over_drawdown")
    ranking.append("worst_net_eur")
    frame = frame.sort_values(
        ranking,
        ascending=[False] * len(ranking),
        na_position="last",
    )
    if "behavior_id" in frame.columns:
        frame = frame.drop_duplicates("behavior_id", keep="first")

    genomes = tuple(
        StrategyGenome.from_dict(json.loads(payload))
        for payload in frame.head(limit)["genome_json"]
    )
    return tuple(dict.fromkeys(genomes))


def _parser():
    parser = argparse.ArgumentParser(description="Bounded Dubai strategy research")
    parser.add_argument("--fixture", choices=("tiny",), default=None)
    parser.add_argument("--from", dest="from_date", default="2026-07-27")
    parser.add_argument("--to", dest="to_date", default="2026-08-14")
    parser.add_argument("--replay-path", default="runtime_data/replay_trades.jsonl")
    parser.add_argument("--audit-path", default="runtime_data/observed_tick_replay_audit.jsonl")
    parser.add_argument("--money-contract", default="runtime_data/broker_money_contract.json")
    parser.add_argument("--market-tick-cache", default="runtime_data/ticks_cache")
    parser.add_argument("--conversion-tick-cache", default="runtime_data/money_ticks_cache")
    parser.add_argument("--output-root", default="runtime_data/dubai_strategy_runs")
    parser.add_argument("--max-hold-minutes", type=int, default=240)
    parser.add_argument("--max-generations", type=int, default=50)
    parser.add_argument("--max-evaluations", type=int, default=1_000_000)
    parser.add_argument("--max-wall-seconds", type=int, default=7_200)
    parser.add_argument("--patience-generations", type=int, default=8)
    parser.add_argument("--max-lineage-depth", type=int, default=12)
    parser.add_argument("--population-size", type=int, default=64)
    parser.add_argument("--seed", type=int, default=20260817)
    parser.add_argument("--min-total-volume", type=float, default=0.01)
    parser.add_argument("--max-total-volume", type=float, default=1.0)
    parser.add_argument("--max-legs", type=int, default=12)
    parser.add_argument("--volume-step", type=float, default=0.01)
    parser.add_argument("--max-entry-expiry-minutes", type=int, default=240)
    parser.add_argument("--max-time-exit-minutes", type=int, default=240)
    parser.add_argument("--parent-parquet", default=None)
    parser.add_argument("--parent-limit", type=int, default=12)
    parser.add_argument("--progress", action="store_true")
    parser.add_argument("--oracle-finalists", type=int, default=1)
    parser.add_argument("--capital-eur", type=float, default=500.0)
    parser.add_argument(
        "--maximum-loss-fraction", type=float, default=0.25
    )
    parser.add_argument(
        "--maximum-concurrent-signals", type=int, default=3
    )
    parser.add_argument("--search-latency-ms", type=int, default=0)
    parser.add_argument("--search-entry-slippage", type=float, default=0.0)
    parser.add_argument("--search-exit-slippage", type=float, default=0.0)
    parser.add_argument("--search-spread-addition", type=float, default=0.0)
    parser.add_argument(
        "--workers",
        type=int,
        default=max(1, min(8, (os.cpu_count() or 2) - 1)),
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
