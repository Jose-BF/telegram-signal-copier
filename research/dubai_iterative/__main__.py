"""Command-line entry point for bounded Dubai strategy research."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from decimal import Decimal
import json
import math
from pathlib import Path
import tempfile
from typing import Sequence

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .contracts import SearchBudget, SearchSpace, StrategyGenome
from .dataset import VerifiedParquetTickSource, load_dubai_dataset
from .engine import SimulationResult
from .reporting import ResearchArtifacts, publish_run
from .search import (
    ChronologicalFold,
    ChronologicalSearchReport,
    DEFAULT_DUBAI_FOLDS,
    GenerationProgress,
    classify_retrospective,
    run_chronological_search,
    run_search,
)


@dataclass(frozen=True)
class _TinyPath:
    signal_id: str
    day: str


@dataclass(frozen=True)
class _TinyDataset:
    paths: tuple[_TinyPath, ...]
    source_hashes: dict[str, str]
    exclusions: dict[str, tuple[str, ...]]
    actual_pnl_eur: Decimal


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
                "max_signal_exposure": item.max_signal_exposure,
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
                "normalized_net_per_001", "max_signal_exposure", "complexity",
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
    )
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    spool = _CandidateSpool(output_root)

    def progress(update: GenerationProgress) -> None:
        if not args.progress:
            return
        print(
            f"[{update.fold}] Generacion {update.generation}/{update.max_generations} | "
            f"evaluadas {update.evaluated}/{update.max_evaluations} | "
            f"frente {update.frontier_size} | sin mejora {update.stale_generations}"
        )

    try:
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
            )
            search_report = ChronologicalSearchReport((one,))
        else:
            dataset = _real_dataset(args)
            search_report = run_chronological_search(
                dataset,
                folds=DEFAULT_DUBAI_FOLDS,
                budget=budget,
                search_space=search_space,
                output_dir=output_root / ".checkpoints",
                seed=args.seed,
                population_size=args.population_size,
                progress_callback=progress,
                evaluation_callback=spool.append,
            )
        candidate_path = spool.close()
        artifacts = _build_artifacts(
            dataset,
            search_report,
            budget=budget,
            search_space=search_space,
            seed=args.seed,
            candidate_path=candidate_path,
        )
        published = publish_run(artifacts, output_root)
    finally:
        candidate_path = spool.close()
        candidate_path.unlink(missing_ok=True)

    reasons = [item.stop_reason for item in search_report.fold_reports]
    print(f"Parada: {', '.join(reasons)}")
    print(f"Estrategias evaluadas: {search_report.total_evaluations}")
    print(f"Resultado: {published.run_dir}")
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


def _build_artifacts(
    dataset,
    search_report,
    *,
    budget,
    search_space,
    seed,
    candidate_path,
):
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
            challenge_net = None if challenge is None else challenge.net_eur
            assessment = classify_retrospective(
                train_net=evaluation.net_eur or Decimal("0"),
                challenge_net=challenge_net or Decimal("0"),
            )
            confidences.append(assessment.confidence)
            frontier_rows.append({
                "fold": report.fold.name,
                "fingerprint": evaluation.genome.fingerprint,
                "plain_strategy": _plain_strategy(evaluation.genome),
                "genome": evaluation.genome.to_dict(),
                "development_net_eur": _number(evaluation.net_eur),
                "challenge_net_eur": _number(challenge_net),
                "max_drawdown_eur": _number(evaluation.max_drawdown_eur),
                "worst_day_eur": _number(evaluation.worst_day_eur),
                "profit_factor": _number(evaluation.profit_factor),
                "profit_factor_infinite": evaluation.profit_factor is not None and math.isinf(evaluation.profit_factor),
                "max_signal_exposure": evaluation.max_signal_exposure,
                "complexity": evaluation.complexity,
                "confidence": assessment.confidence,
                "promotion_eligible": False,
            })
            signal_rows.extend(_signal_rows(report.fold.name, "development", evaluation))
            if challenge is not None:
                signal_rows.extend(_signal_rows(report.fold.name, "challenge", challenge))
    confidence = (
        "demo_candidate"
        if confidences and all(item == "demo_candidate" for item in confidences)
        else "retrospective_unstable"
    )
    exclusions = {
        key: list(values)
        for key, values in getattr(dataset, "exclusions", {}).items()
    }
    run_card = {
        "schema_version": 1,
        "source_hashes": dict(sorted(dataset.source_hashes.items())),
        "signal_ids": [path.signal_id for path in dataset.paths],
        "exclusions": exclusions,
        "seed": seed,
        "folds": [asdict(item.fold) for item in search_report.fold_reports],
        "budget": asdict(budget),
        "search_space": asdict(search_space),
        "grammar_version": 1,
        "confidence": confidence,
        "actual_pnl_eur": _number(getattr(dataset, "actual_pnl_eur", None)),
        "stop_reasons": [item.stop_reason for item in search_report.fold_reports],
        "total_evaluations": search_report.total_evaluations,
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


def _tiny_dataset():
    return _TinyDataset(
        paths=(
            _TinyPath("train_1", "2026-07-27"),
            _TinyPath("train_2", "2026-07-28"),
            _TinyPath("challenge_1", "2026-07-29"),
        ),
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


def _parser():
    parser = argparse.ArgumentParser(description="Bounded Dubai strategy research")
    parser.add_argument("--fixture", choices=("tiny",), default=None)
    parser.add_argument("--from", dest="from_date", default="2026-07-27")
    parser.add_argument("--to", dest="to_date", default="2026-08-14")
    parser.add_argument("--replay-path", default="data/replay_trades.jsonl")
    parser.add_argument("--audit-path", default="data/observed_tick_replay_audit.jsonl")
    parser.add_argument("--money-contract", default="data/broker_money_contract.json")
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
    parser.add_argument("--max-total-volume", type=float, default=0.20)
    parser.add_argument("--max-legs", type=int, default=12)
    parser.add_argument("--volume-step", type=float, default=0.01)
    parser.add_argument("--progress", action="store_true")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
