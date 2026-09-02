"""Command line interface for bounded Gold Signals NOW research."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Mapping, Sequence

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from provider_result_scorecard import (
    build_scorecard,
    load_hash_bound_media_summaries,
)
from research.dubai_iterative.contracts import SearchBudget, SearchSpace
from research.dubai_iterative.certification import certify_genome_worlds
from research.dubai_iterative.dataset import (
    LevelEvent,
    ProviderEvent,
    SignalLeg,
    SignalPath,
    StrategyDataset,
    VerifiedParquetTickSource,
)
from research.dubai_iterative.engine import ExecutionAssumptions
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.oracle import ExecutionScenario
from research.dubai_iterative.reporting import (
    ProvenanceConflictError,
    publish_run,
    verify_published_run,
)
from research.dubai_iterative.search import (
    GenerationProgress,
    cross_validate_frontier_candidates,
)

from .dataset import load_gold_direct_dataset, load_gold_now_dataset
from .folds import build_gold_fold_plan
from .provider_accounting import build_candidate_pip_hypotheses
from .reporting import (
    GoldEvidenceGates,
    ProviderPipHypothesis,
    build_gold_research_artifacts,
)
from .search import run_gold_chronological_search
from .validation import (
    GoldStabilityPolicy,
    validate_gold_candidates,
)


_CANDIDATE_SCHEMA = pa.schema((
    ("fold", pa.string()),
    ("generation", pa.int32()),
    ("strategy_fingerprint", pa.string()),
    ("genome_json", pa.string()),
    ("net_eur", pa.string()),
    ("max_drawdown_eur", pa.string()),
    ("worst_day_eur", pa.string()),
    ("normalized_net_per_001", pa.string()),
    ("normalized_max_drawdown_per_001", pa.string()),
    ("participation_rate", pa.float64()),
    ("complexity", pa.int32()),
    ("blockers_json", pa.string()),
))


@dataclass(frozen=True)
class _CompleteDatasetView:
    paths: tuple[SignalPath, ...]
    source_hashes: Mapping[str, str]


class _CandidateFragmentSpool:
    """Persist one deterministic Parquet fragment per completed generation."""

    def __init__(self, history_dir: Path, output_root: Path) -> None:
        self.history_dir = Path(history_dir)
        self.output_root = Path(output_root)
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self.output_root.mkdir(parents=True, exist_ok=True)

    def append(self, fold, generation, evaluations) -> None:
        rows = [
            {
                "fold": fold.name,
                "generation": int(generation),
                "strategy_fingerprint": item.genome.fingerprint,
                "genome_json": json.dumps(
                    item.genome.to_dict(),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "net_eur": _decimal_text(item.net_eur),
                "max_drawdown_eur": _decimal_text(item.max_drawdown_eur),
                "worst_day_eur": _decimal_text(item.worst_day_eur),
                "normalized_net_per_001": _decimal_text(
                    item.normalized_net_per_001
                ),
                "normalized_max_drawdown_per_001": _decimal_text(
                    item.normalized_max_drawdown_per_001
                ),
                "participation_rate": float(item.participation_rate),
                "complexity": int(item.complexity),
                "blockers_json": json.dumps(
                    item.blockers,
                    separators=(",", ":"),
                ),
            }
            for item in evaluations
        ]
        table = pa.Table.from_pylist(rows, schema=_CANDIDATE_SCHEMA)
        fold_dir = self.history_dir / fold.name
        fold_dir.mkdir(parents=True, exist_ok=True)
        target = fold_dir / f"generation-{int(generation):06d}.parquet"
        temporary = target.with_suffix(".parquet.tmp")
        pq.write_table(table, temporary, compression="zstd")
        if target.exists():
            if _sha256_file(target) != _sha256_file(temporary):
                temporary.unlink(missing_ok=True)
                raise ValueError(
                    f"candidate generation conflicts with prior evidence: {target}"
                )
            temporary.unlink(missing_ok=True)
            return
        os.replace(temporary, target)

    def materialize(self) -> tuple[Path, int]:
        handle = tempfile.NamedTemporaryFile(
            prefix="gold-candidates-",
            suffix=".parquet",
            dir=self.output_root,
            delete=False,
        )
        handle.close()
        output = Path(handle.name)
        output.unlink(missing_ok=True)
        writer = pq.ParquetWriter(output, _CANDIDATE_SCHEMA, compression="zstd")
        row_count = 0
        try:
            for fragment in sorted(self.history_dir.glob("*/generation-*.parquet")):
                table = pq.read_table(fragment, schema=_CANDIDATE_SCHEMA)
                writer.write_table(table)
                row_count += table.num_rows
        finally:
            writer.close()
        return output, row_count


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "inspect":
            return _inspect(args)
        if args.command in {"search", "resume"}:
            return _search(args, resume=args.command == "resume")
        if args.command == "verify":
            published = verify_published_run(Path(args.run_dir))
            _emit(f"VERIFICADO: {published.run_id}")
            return 0
        if args.command == "compare-provider-claims":
            return _compare_provider_claims(Path(args.run_dir))
        raise ValueError(f"unsupported command: {args.command}")
    except (
        FileNotFoundError,
        json.JSONDecodeError,
        ProvenanceConflictError,
        ValueError,
    ) as exc:
        _emit(f"ERROR: {exc}")
        return 2


def _inspect(args) -> int:
    dataset = _load_dataset(args)
    plan = build_gold_fold_plan(dataset)
    payload = {
        "eligible_signals": len(dataset.eligible_signal_ids),
        "loaded_paths": len(dataset.paths),
        "complete_days": list(plan.complete_days),
        "incomplete_days": list(plan.incomplete_days),
        "folds": len(plan.folds),
        "exclusions": {
            reason: list(signal_ids)
            for reason, signal_ids in sorted(dataset.exclusions.items())
        },
        "source_hashes": dict(sorted(dataset.source_hashes.items())),
    }
    _emit(json.dumps(payload, sort_keys=True, indent=2))
    return 0


def _search(args, *, resume: bool) -> int:
    dataset = _load_dataset(args)
    fold_plan = build_gold_fold_plan(dataset)
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
    execution = ExecutionAssumptions(
        latency_ms=args.search_latency_ms,
        entry_slippage=args.search_entry_slippage,
        exit_slippage=args.search_exit_slippage,
        spread_addition=args.search_spread_addition,
    )
    validation_worlds = _execution_validation_worlds(execution)
    stability_policy = GoldStabilityPolicy(
        bootstrap_samples=args.bootstrap_samples,
        seed=args.seed,
        minimum_bootstrap_probability_positive=(
            args.minimum_bootstrap_probability_positive
        ),
        minimum_leave_one_day_out_positive_ratio=(
            args.minimum_leave_one_day_out_positive_ratio
        ),
    )
    minimum_future_challenge_folds = (
        1 if args.fixture == "tiny"
        else args.minimum_future_challenge_folds
    )
    minimum_future_challenge_signals = (
        1 if args.fixture == "tiny"
        else args.minimum_future_challenge_signals
    )
    minimum_future_filled_signals = (
        1 if args.fixture == "tiny"
        else args.minimum_future_filled_signals
    )
    output_root = Path(args.output_root)
    checkpoint_base = output_root / ".checkpoints"
    signal_scope = _signal_scope_label(args.signal_scope)
    context = {
        "engine": "numba_fixed_point_gold_v2",
        "execution": asdict(execution),
        "signal_scope": signal_scope,
    }
    experiment_key = _experiment_key(
        dataset.source_hashes,
        search_space,
        execution,
        args.seed,
    )
    checkpoint_root = checkpoint_base / experiment_key
    legacy_checkpoint_root = checkpoint_base
    if (
        resume
        and not any(checkpoint_root.glob("gold_fold_*/checkpoint.json"))
        and args.signal_scope == "now"
        and any(legacy_checkpoint_root.glob("gold_fold_*/checkpoint.json"))
    ):
        checkpoint_root = legacy_checkpoint_root
    spool = _CandidateFragmentSpool(
        checkpoint_base / ".candidate_fragments" / experiment_key,
        output_root,
    )
    if resume and not any(checkpoint_root.glob("gold_fold_*/checkpoint.json")):
        raise ValueError("no Gold checkpoint exists to resume")
    if resume:
        _emit("Reanudando busqueda Gold desde checkpoints verificados...")

    def progress(update: GenerationProgress) -> None:
        if not args.progress:
            return
        rate = (
            update.evaluated / update.elapsed_seconds
            if update.elapsed_seconds > 0
            else 0.0
        )
        remaining = max(0, update.max_evaluations - update.evaluated)
        eta = remaining / rate if rate > 0 else 0.0
        _emit(
            f"[{update.fold}] Generacion "
            f"{update.generation}/{update.max_generations} | "
            f"evaluadas {update.evaluated}/{update.max_evaluations} | "
            f"frente {update.frontier_size} | "
            f"tiempo {update.elapsed_seconds:.1f}s | ETA {_duration(eta)}",
        )

    evaluator = FastEvaluator(execution=execution)
    candidate_path = None
    try:
        report = run_gold_chronological_search(
            dataset,
            fold_plan=fold_plan,
            budget=budget,
            search_space=search_space,
            output_dir=checkpoint_root,
            evaluator=evaluator,
            seed=args.seed,
            population_size=args.population_size,
            progress_callback=progress,
            evaluation_callback=spool.append,
            experiment_context=context,
            signal_scope=signal_scope,
            workers=args.workers,
            resume_from_root=checkpoint_root if resume else None,
            retain_result_rows=False,
        )
        candidate_path, candidate_count = spool.materialize()
        complete_days = set(fold_plan.complete_days)
        complete_paths = tuple(
            path for path in dataset.paths if path.day in complete_days
        )
        complete_dataset = _CompleteDatasetView(
            paths=complete_paths,
            source_hashes=dataset.source_hashes,
        )
        validation_progress_state = {"bucket": -1}

        def validation_progress(completed: int, total: int) -> None:
            if not args.progress or total <= 0:
                return
            percent = min(100, int(completed * 100 / total))
            bucket = percent // 5
            if bucket <= validation_progress_state["bucket"]:
                return
            validation_progress_state["bucket"] = bucket
            _emit(
                f"[Validacion cruzada] {completed}/{total} "
                f"({percent}%; candidatos inviables se podan pronto)",
            )

        cross_fold = cross_validate_frontier_candidates(
            complete_dataset,
            report.search,
            evaluator=evaluator,
            additional_execution_scenarios=tuple(
                (name, FastEvaluator(execution=world_execution))
                for name, world_execution, _oracle_scenario
                in validation_worlds[1:]
            ),
            minimum_participation=args.minimum_participation,
            minimum_positive_challenge_ratio=(
                args.minimum_positive_challenge_ratio
            ),
            minimum_future_challenge_folds=(
                minimum_future_challenge_folds
            ),
            minimum_future_challenge_signals=(
                minimum_future_challenge_signals
            ),
            minimum_future_filled_signals=(
                minimum_future_filled_signals
            ),
            workers=args.workers,
            progress_callback=validation_progress,
        )
        stability_candidates = (
            cross_fold.eligible
            or cross_fold.assessments[:max(12, args.oracle_finalists)]
        )
        candidate_validation = validate_gold_candidates(
            stability_candidates,
            policy=stability_policy,
        )
        selection_pool = (
            candidate_validation.eligible
            or candidate_validation.candidates
        )
        selected_validations = selection_pool[: args.oracle_finalists]
        selected_assessments = tuple(
            item.assessment for item in selected_validations
        )
        frontier_evaluations = tuple(
            item.scenarios[0].evaluation for item in selected_assessments
        )
        world_certifications = tuple(
            certify_genome_worlds(
                complete_paths,
                item.genome,
                worlds=validation_worlds,
                evaluator_factory=lambda active_execution: FastEvaluator(
                    execution=active_execution
                ),
            )
            for item in selected_validations
        )
        provider_scorecard = _provider_scorecard(args)
        chronological_diagnostics = _chronological_diagnostics(report.search)
        gates = GoldEvidenceGates(
            provider_paths_complete=(
                bool(complete_paths)
                and all(
                    not coverage.missing_signal_ids
                    for coverage in fold_plan.day_coverage
                    if coverage.day in complete_days
                )
            ),
            tick_paths_complete=(
                bool(complete_paths)
                and all(path.market_evidence for path in complete_paths)
            ),
            account_currency_money_complete=(
                bool(selected_assessments)
                and all(
                    item.evidence_complete
                    for item in selected_assessments
                )
            ),
            oracle_parity_complete=(
                bool(world_certifications)
                and all(
                    item.status == "pass"
                    for item in world_certifications
                )
            ),
            chronological_challenge_complete=bool(
                chronological_diagnostics["complete"]
            ),
            cross_fold_candidate_eligible=bool(cross_fold.eligible),
            daily_stability_candidate_eligible=bool(
                candidate_validation.eligible
            ),
            source_manifest_complete=all(dataset.source_hashes.values()),
        )
        generation_rows = tuple(
            {
                "fold": update.fold,
                "generation": update.generation,
                "evaluated": update.evaluated,
                "frontier_size": update.frontier_size,
                "stale_generations": update.stale_generations,
            }
            for fold_report in report.search.fold_reports
            for update in fold_report.generation_summaries
        )
        run_metadata = {
            "seed": args.seed,
            "budget": asdict(budget),
            "search_space": asdict(search_space),
            "execution": asdict(execution),
            "execution_validation_worlds": [
                {
                    "name": name,
                    **asdict(world_execution),
                }
                for name, world_execution, _oracle_scenario
                in validation_worlds
            ],
            "stability_policy": asdict(stability_policy),
            "minimum_participation": args.minimum_participation,
            "minimum_positive_challenge_ratio": (
                args.minimum_positive_challenge_ratio
            ),
            "future_evidence_policy": {
                "scope": "post_first_discovery_only",
                "minimum_future_challenge_folds": (
                    minimum_future_challenge_folds
                ),
                "minimum_future_challenge_signals": (
                    minimum_future_challenge_signals
                ),
                "minimum_future_filled_signals": (
                    minimum_future_filled_signals
                ),
            },
            "engine": "numba_fixed_point_gold_v2",
            "signal_scope": signal_scope,
            "oracle": "independent_scalar_gold_v2",
            "oracle_statuses": [
                item.status for item in world_certifications
            ],
            "oracle_world_certification": [
                _world_certification_summary(item)
                for item in world_certifications
            ],
            "stop_reasons": [
                item.stop_reason for item in report.search.fold_reports
            ],
            "generations_completed": [
                item.generations_completed for item in report.search.fold_reports
            ],
            "total_evaluations": report.search.total_evaluations,
            "cross_fold_validation": {
                "considered_count": cross_fold.considered_count,
                "eligible_count": len(cross_fold.eligible),
                "rejected_count": len(cross_fold.rejected),
                "stable_count": len(candidate_validation.eligible),
                "stability_rejected_count": len(
                    candidate_validation.rejected
                ),
                "stability_considered_count": len(
                    candidate_validation.candidates
                ),
                "stability_assessments": [
                    _candidate_validation_summary(item)
                    for item in candidate_validation.candidates
                ],
                "fully_world_tested_count": sum(
                    item.scenario_count == len(validation_worlds)
                    for item in cross_fold.assessments
                ),
                "selected": [
                    _candidate_validation_summary(item)
                    for item in selected_validations
                ],
            },
            "chronological_challenge": chronological_diagnostics,
            "fixture": args.fixture is not None,
            "live_code_changed": False,
            "automatic_deployment": False,
        }
        artifacts = build_gold_research_artifacts(
            dataset,
            fold_plan=fold_plan,
            frontier_evaluations=frontier_evaluations,
            candidate_evaluations=frontier_evaluations,
            candidate_rows_source=candidate_path,
            candidate_population_size=candidate_count,
            generation_rows=generation_rows,
            gates=gates,
            provider_scorecard=provider_scorecard,
            signal_scope=signal_scope,
            provider_pip_hypotheses=_provider_hypotheses(
                args,
                evaluations=frontier_evaluations,
                paths=complete_paths,
                provider_scorecard=provider_scorecard,
            ),
            run_metadata=run_metadata,
        )
        published = publish_run(artifacts, output_root)
    finally:
        if candidate_path is not None:
            candidate_path.unlink(missing_ok=True)

    reasons = [item.stop_reason for item in report.search.fold_reports]
    _emit(f"Parada: {', '.join(reasons)}")
    _emit(f"Estrategias evaluadas: {report.search.total_evaluations}")
    _emit(f"Resultado: {published.run_dir}")
    return 130 if "user_interrupt" in reasons else 0


def _compare_provider_claims(run_dir: Path) -> int:
    published = verify_published_run(run_dir)
    frame = pd.read_parquet(
        published.run_dir / "provider_claim_distance.parquet"
    )
    if frame.empty:
        _emit("Sin hipotesis comparables para los periodos publicados.")
        return 0
    for row in frame.to_dict("records"):
        status = (
            "VERIFICADA" if bool(row["hypothesis_verified"])
            else "NO VERIFICADA"
        )
        _emit(
            f"{row['period_start']}..{row['period_end']} | "
            f"provider_pips={row['provider_claim_pips']} | "
            f"{row['hypothesis_id']}={row['hypothesis_pips']} | "
            f"distancia={row['distance_pips']} | {status}",
        )
    _emit("Esta comparacion es diagnostica y no selecciona estrategias.")
    return 0


def _load_dataset(args) -> StrategyDataset:
    if args.fixture == "tiny":
        return _tiny_dataset()
    contract = json.loads(Path(args.money_contract).read_text(encoding="utf-8"))
    conversion = contract.get("conversion") or {}
    conversion_source = None
    if conversion.get("orientation") != "identity":
        conversion_source = VerifiedParquetTickSource(
            Path(args.conversion_tick_cache),
            expected_symbol=str(conversion.get("symbol") or "EURUSD"),
        )
    loader = (
        load_gold_now_dataset
        if args.signal_scope == "now"
        else load_gold_direct_dataset
    )
    return loader(
        replay_path=Path(args.replay_path),
        audit_path=Path(args.audit_path),
        provider_catalog_path=Path(args.provider_catalog_path),
        raw_events_path=Path(args.raw_events_path),
        market_ticks=VerifiedParquetTickSource(
            Path(args.market_tick_cache),
            expected_symbol="XAUUSD",
        ),
        conversion_ticks=conversion_source,
        money_contract=contract,
        from_date=args.from_date,
        to_date=args.to_date,
        max_hold_minutes=args.max_hold_minutes,
    )


def _provider_scorecard(args) -> Mapping[str, object]:
    if args.fixture == "tiny":
        return _tiny_scorecard()
    catalog = json.loads(
        Path(args.provider_catalog_path).read_text(encoding="utf-8")
    )
    annotations_path = Path(args.provider_media_annotations)
    media_evidence_path = Path(args.provider_media_evidence)
    supplemental = load_hash_bound_media_summaries(
        annotations_path,
        media_evidence_path,
    )
    scorecard = build_scorecard(
        catalog,
        supplemental_records=supplemental,
    )
    return {
        **scorecard,
        "source_hashes": {
            "provider_catalog": _sha256_file(Path(args.provider_catalog_path)),
            "provider_media_annotations": _sha256_file(annotations_path),
            "provider_media_evidence": _sha256_file(media_evidence_path),
        },
    }


def _provider_hypotheses(
    args,
    *,
    evaluations: Sequence[object] = (),
    paths: Sequence[object] = (),
    provider_scorecard: Mapping[str, object] | None = None,
) -> tuple[ProviderPipHypothesis, ...]:
    if args.fixture == "tiny":
        return (ProviderPipHypothesis(
            hypothesis_id="fixture_sum_exit_moves_x100",
            description="Synthetic provider accounting hypothesis",
            period_totals={"2026-08-24:2026-08-26": Decimal("385")},
            verified=False,
        ),)
    return build_candidate_pip_hypotheses(
        evaluations,
        paths=paths,
        provider_scorecard=provider_scorecard or {},
    )


def _chronological_complete(report) -> bool:
    return bool(_chronological_diagnostics(report)["complete"])


def _chronological_diagnostics(report) -> dict[str, object]:
    """Require usable challenge evidence per fold, not perfection per genome."""

    folds = []
    for fold_report in report.fold_reports:
        reasons: dict[str, int] = {}
        complete_count = 0
        evaluations = tuple(fold_report.challenge_evaluations)
        for item in evaluations:
            blockers = tuple(item.blockers)
            if item.net_eur is not None and not blockers:
                complete_count += 1
                continue
            if item.net_eur is None:
                reasons["missing_net_eur"] = reasons.get("missing_net_eur", 0) + 1
            for blocker in blockers:
                key = str(blocker)
                reasons[key] = reasons.get(key, 0) + 1
        folds.append({
            "fold": str(fold_report.fold.name),
            "candidate_count": len(evaluations),
            "complete_candidate_count": complete_count,
            "rejected_candidate_count": len(evaluations) - complete_count,
            "rejection_reasons": dict(sorted(reasons.items())),
        })
    return {
        "complete": bool(folds) and all(
            item["complete_candidate_count"] > 0 for item in folds
        ),
        "folds": folds,
    }


def _execution_validation_worlds(search_execution):
    worlds = (
        ("full_window", search_execution),
        ("latency_250ms", ExecutionAssumptions(latency_ms=250)),
        ("latency_1s", ExecutionAssumptions(latency_ms=1_000)),
        ("latency_2s", ExecutionAssumptions(latency_ms=2_000)),
        (
            "mild_costs",
            ExecutionAssumptions(
                latency_ms=250,
                entry_slippage=0.03,
                exit_slippage=0.03,
                spread_addition=0.02,
            ),
        ),
        (
            "adverse_costs",
            ExecutionAssumptions(
                latency_ms=500,
                entry_slippage=0.10,
                exit_slippage=0.10,
                spread_addition=0.10,
            ),
        ),
    )
    return tuple(
        (
            name,
            world_execution,
            ExecutionScenario(name, **asdict(world_execution)),
        )
        for name, world_execution in worlds
    )


def _candidate_validation_summary(item) -> Mapping[str, object]:
    assessment = item.assessment
    stability = item.stability
    return {
        "strategy_fingerprint": item.genome.fingerprint,
        "behavior_id": item.group.behavior_id,
        "equivalent_genome_count": item.group.member_count,
        "equivalent_genome_fingerprints": list(
            item.group.member_fingerprints
        ),
        "validation_eligible": item.eligible,
        "validation_blockers": list(item.blockers),
        "robustness_eligible": assessment.robustness_eligible,
        "discovery_fold_name": assessment.discovery_fold_name,
        "validation_fold_names": list(
            assessment.validation_fold_names
        ),
        "validation_days": list(assessment.validation_days),
        "validation_signal_count": assessment.validation_signal_count,
        "validation_filled_signal_count": (
            assessment.validation_filled_signal_count
        ),
        "validation_participation_rate": (
            assessment.validation_participation_rate
        ),
        "selection_blockers": list(assessment.selection_blockers),
        "selection_scope": "post_first_discovery_only",
        "worst_net_eur": _decimal_text(assessment.worst_net_eur),
        "worst_challenge_net_eur": _decimal_text(
            assessment.worst_challenge_net_eur
        ),
        "positive_challenge_ratio": assessment.positive_challenge_ratio,
        "minimum_participation": assessment.minimum_participation,
        "maximum_drawdown_eur": _decimal_text(
            assessment.maximum_drawdown_eur
        ),
        "execution_scenarios": [
            {
                "name": scenario.name,
                "net_eur": _decimal_text(scenario.evaluation.net_eur),
                "max_drawdown_eur": _decimal_text(
                    scenario.evaluation.max_drawdown_eur
                ),
                "participation_rate": (
                    scenario.evaluation.participation_rate
                ),
                "blockers": list(scenario.evaluation.blockers),
            }
            for scenario in assessment.scenarios
        ],
        "daily_stability": {
            "evidence_complete": stability.evidence_complete,
            "minimum_bootstrap_probability_positive": (
                stability.minimum_bootstrap_probability_positive
            ),
            "worst_bootstrap_p05_eur": _decimal_text(
                stability.worst_bootstrap_p05_eur
            ),
            "minimum_leave_one_day_out_positive_ratio": (
                stability.minimum_leave_one_day_out_positive_ratio
            ),
            "maximum_positive_day_concentration": (
                stability.maximum_positive_day_concentration
            ),
            "blockers": list(stability.blockers),
        },
    }


def _world_certification_summary(report) -> Mapping[str, object]:
    return {
        "strategy_fingerprint": report.genome.fingerprint,
        "status": report.status,
        "certified_worlds": report.certified_worlds,
        "world_count": report.world_count,
        "worlds": [
            {
                "name": item.name,
                "oracle_status": item.certificate.status,
                "oracle_mismatch_count": len(item.certificate.mismatches),
                "net_eur": _decimal_text(item.net_eur),
                "blockers": list(item.blockers),
            }
            for item in report.worlds
        ],
    }


def _experiment_key(source_hashes, search_space, execution, seed) -> str:
    payload = {
        "source_hashes": dict(sorted(source_hashes.items())),
        "search_space": asdict(search_space),
        "execution": asdict(execution),
        "seed": int(seed),
        "operators": "gold_iterative_v1",
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def _tiny_dataset() -> StrategyDataset:
    paths = tuple(
        _tiny_path(day, direction)
        for day, direction in (
            ("2026-08-24", "BUY"),
            ("2026-08-25", "SELL"),
            ("2026-08-26", "BUY"),
        )
    )
    return StrategyDataset(
        paths=paths,
        eligible_signal_ids=tuple(path.signal_id for path in paths),
        eligible_signal_days={path.signal_id: path.day for path in paths},
        eligible_actual_pnl_eur=Decimal("3.00"),
        exclusions={},
        source_hashes={
            "fixture": hashlib.sha256(b"gold-cli-tiny-v1").hexdigest(),
        },
        account_currency="EUR",
        currency_digits=2,
        max_hold_minutes=240,
    )


def _tiny_path(day: str, direction: str) -> SignalPath:
    base = datetime.fromisoformat(f"{day}T09:00:00+00:00")
    offsets = (0, 1, 2, 3, 4, 5, 90, 240)
    if direction == "BUY":
        bid_values = (100.0, 99.0, 98.5, 100.0, 100.8, 102.0, 101.0, 100.5)
    else:
        bid_values = (100.0, 101.0, 101.5, 100.0, 99.2, 98.0, 99.0, 99.5)
    ask_values = tuple(value + 0.2 for value in bid_values)
    bid = _readonly(bid_values)
    ask = _readonly(ask_values)
    entry = 100.2 if direction == "BUY" else 100.0
    sign = 1.0 if direction == "BUY" else -1.0
    legs = tuple(
        SignalLeg(
            ticket=f"{day}-{index}",
            role="market_a" if index == 0 else "scale_out_leg",
            volume=0.01,
            opened_at=base,
            open_price=entry,
            closed_at=base + timedelta(minutes=5),
            close_price=entry + sign * 0.5,
            close_reason="fixture",
            actual_pnl_eur=Decimal("0.20"),
            tp_events=(LevelEvent(
                base,
                entry + sign * (index + 1) * 0.5,
                "confirmed",
                "fixture",
            ),),
            sl_events=(LevelEvent(
                base,
                entry - sign * 5.0,
                "confirmed",
                "fixture",
            ),),
        )
        for index in range(5)
    )
    times = _readonly(
        tuple(
            int((base + timedelta(minutes=offset)).timestamp() * 1_000_000_000)
            for offset in offsets
        ),
        dtype=np.int64,
    )
    return SignalPath(
        signal_id=f"canal2_fixture_{day}",
        day=day,
        direction=direction,
        signal_observed_at=base,
        opened_at=base,
        actual_pnl_eur=Decimal("1.00"),
        legs=legs,
        provider_events=(ProviderEvent(
            base + timedelta(minutes=240),
            "CLOSE_ALL",
            {"raw_text": "fixture close"},
        ),),
        times_ns=times,
        bid=bid,
        ask=ask,
        exit_quotes=bid if direction == "BUY" else ask,
        fx_bid=_readonly((1.0,) * len(offsets)),
        fx_ask=_readonly((1.0,) * len(offsets)),
        fx_age_ms=_readonly((0.0,) * len(offsets)),
        fx_valid=_readonly((True,) * len(offsets), dtype=bool),
        contract_size=100.0,
        conversion_orientation="identity",
        currency_digits=2,
        market_evidence=({"verified": True, "day": day},),
        conversion_evidence=(),
    )


def _tiny_scorecard() -> Mapping[str, object]:
    return {
        "schema_version": 1,
        "provider": "Gold Signals",
        "channel": "canal2",
        "summaries": ({
            "provider_signal_id": "fixture_week",
            "claim": {
                "period_kind": "weekly",
                "period_start": "2026-08-24",
                "period_end": "2026-08-26",
                "signals_sent": 3,
                "wins": 2,
                "losses": 1,
                "breakeven": 0,
                "pips_gained": 400,
                "calibration_ready": True,
                "blockers": [],
            },
            "observed_signal_ids": [
                "canal2_fixture_2026-08-24",
                "canal2_fixture_2026-08-25",
                "canal2_fixture_2026-08-26",
            ],
        },),
    }


def _readonly(values, *, dtype=float):
    array = np.asarray(values, dtype=dtype)
    array.setflags(write=False)
    return array


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _emit(message: str) -> None:
    """Write stable machine-readable CLI output outside live print hooks."""

    sys.stdout.write(f"{message}\n")
    sys.stdout.flush()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _add_dataset_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--fixture", choices=("tiny",), default=None)
    parser.add_argument(
        "--signal-scope",
        choices=("now", "direct"),
        default="now",
        help="NOW only, or NOW plus explicit priced direct entries",
    )
    parser.add_argument("--from", dest="from_date", default="2026-07-27")
    parser.add_argument("--to", dest="to_date", default=None)
    parser.add_argument("--replay-path", default="runtime_data/replay_trades.jsonl")
    parser.add_argument(
        "--audit-path",
        default="runtime_data/observed_tick_replay_audit.jsonl",
    )
    parser.add_argument(
        "--provider-catalog-path",
        default="runtime_data/provider_signal_catalog.json",
    )
    parser.add_argument(
        "--provider-media-annotations",
        default="research/gold_iterative/provider_claim_annotations.json",
    )
    parser.add_argument(
        "--provider-media-evidence",
        default="runtime_data/telemetry_latest/telegram_media.jsonl",
    )
    parser.add_argument(
        "--raw-events-path",
        default="runtime_data/trade_events.jsonl",
    )
    parser.add_argument(
        "--money-contract",
        default="runtime_data/broker_money_contract.json",
    )
    parser.add_argument("--market-tick-cache", default="runtime_data/ticks_cache")
    parser.add_argument(
        "--conversion-tick-cache",
        default="runtime_data/money_ticks_cache",
    )
    parser.add_argument("--max-hold-minutes", type=int, default=240)


def _add_search_arguments(parser: argparse.ArgumentParser) -> None:
    _add_dataset_arguments(parser)
    parser.add_argument("--output-root", default="runtime_data/gold_strategy_runs")
    parser.add_argument("--max-generations", type=int, default=50)
    parser.add_argument("--max-evaluations", type=int, default=1_000_000)
    parser.add_argument("--max-wall-seconds", type=int, default=7_200)
    parser.add_argument("--patience-generations", type=int, default=8)
    parser.add_argument("--max-lineage-depth", type=int, default=12)
    parser.add_argument("--population-size", type=int, default=64)
    parser.add_argument("--seed", type=int, default=20260902)
    parser.add_argument("--min-total-volume", type=float, default=0.01)
    parser.add_argument("--max-total-volume", type=float, default=1.0)
    parser.add_argument("--max-legs", type=int, default=12)
    parser.add_argument("--volume-step", type=float, default=0.01)
    parser.add_argument("--max-entry-expiry-minutes", type=int, default=240)
    parser.add_argument("--max-time-exit-minutes", type=int, default=240)
    parser.add_argument("--oracle-finalists", type=int, default=3)
    parser.add_argument("--minimum-participation", type=float, default=0.50)
    parser.add_argument(
        "--minimum-positive-challenge-ratio",
        type=float,
        default=0.60,
    )
    parser.add_argument(
        "--minimum-future-challenge-folds",
        type=int,
        default=12,
    )
    parser.add_argument(
        "--minimum-future-challenge-signals",
        type=int,
        default=100,
    )
    parser.add_argument(
        "--minimum-future-filled-signals",
        type=int,
        default=100,
    )
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument(
        "--minimum-bootstrap-probability-positive",
        type=float,
        default=0.95,
    )
    parser.add_argument(
        "--minimum-leave-one-day-out-positive-ratio",
        type=float,
        default=1.0,
    )
    parser.add_argument("--search-latency-ms", type=int, default=0)
    parser.add_argument("--search-entry-slippage", type=float, default=0.0)
    parser.add_argument("--search-exit-slippage", type=float, default=0.0)
    parser.add_argument("--search-spread-addition", type=float, default=0.0)
    parser.add_argument("--progress", action="store_true")
    parser.add_argument(
        "--workers",
        type=int,
        default=max(1, min(8, (os.cpu_count() or 2) - 1)),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Gold Signals NOW iterative strategy research"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="audit dataset coverage")
    _add_dataset_arguments(inspect)
    search = commands.add_parser("search", help="start a bounded search")
    _add_search_arguments(search)
    resume = commands.add_parser("resume", help="continue verified checkpoints")
    _add_search_arguments(resume)
    verify = commands.add_parser("verify", help="verify immutable run bytes")
    verify.add_argument("--run-dir", required=True)
    compare = commands.add_parser(
        "compare-provider-claims",
        help="show provider-pip accounting distances",
    )
    compare.add_argument("--run-dir", required=True)
    return parser


def _signal_scope_label(value: str) -> str:
    labels = {
        "now": "formal_telegram_now",
        "direct": "formal_telegram_direct",
    }
    try:
        return labels[value]
    except KeyError as exc:
        raise ValueError(f"unsupported Gold signal scope: {value}") from exc


if __name__ == "__main__":
    raise SystemExit(main())
