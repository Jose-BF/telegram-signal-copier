"""Run a bounded, capital-aware Dubai strategy exploration.

This is an offline research command.  It never imports or changes live bot
modules and it never deploys a candidate.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from decimal import Decimal
import json
from pathlib import Path
import sys
import time

import pandas as pd

REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from research.dubai_iterative.contracts import SearchSpace, StrategyGenome
from research.dubai_iterative.dataset import (
    VerifiedParquetTickSource,
    load_dubai_dataset,
)
from research.dubai_iterative.engine import ExecutionAssumptions
from research.dubai_iterative.evolution import (
    CandidateEvaluation,
    crossover,
    deduplicate,
    sample_diverse_population,
    seed_population,
)
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.refinement import parameter_neighborhood
from research.dubai_iterative.risk import (
    assess_capital_risk,
    build_capital_risk_context,
)
from research.dubai_iterative.robustness import (
    ScenarioEvaluation,
    assess_execution_robustness,
    assess_robust_daily_stability,
    group_observationally_equivalent,
    rank_observational_groups,
)
from research.dubai_iterative.search import DEFAULT_DUBAI_FOLDS


WORLDS = (
    ("zero", ExecutionAssumptions()),
    ("lat250", ExecutionAssumptions(latency_ms=250)),
    (
        "measured",
        ExecutionAssumptions(
            latency_ms=500,
            entry_slippage=0.05,
            exit_slippage=0.05,
            spread_addition=0.05,
        ),
    ),
    ("lat1000", ExecutionAssumptions(latency_ms=1_000)),
    ("lat2000", ExecutionAssumptions(latency_ms=2_000)),
    (
        "adverse",
        ExecutionAssumptions(
            latency_ms=500,
            entry_slippage=0.10,
            exit_slippage=0.10,
            spread_addition=0.10,
        ),
    ),
)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    dataset = _dataset(args)
    if not dataset.coverage_complete:
        raise RuntimeError("the exact Dubai dataset is incomplete")
    context = build_capital_risk_context(dataset.paths)
    capital = Decimal(str(args.capital))
    risk_fraction = Decimal(str(args.risk_fraction))
    risk_limit = (capital * risk_fraction).quantize(Decimal("0.01"))
    space = SearchSpace(
        min_total_volume=0.01,
        max_total_volume=args.max_total_volume,
        max_legs=args.max_legs,
        volume_step=0.01,
        max_entry_expiry_min=240,
        max_time_exit_min=240,
        max_path_horizon_min=240,
    )
    parents = _load_parents(args.parent_parquet, args.parent_limit)
    generated, local_count, crossover_count = _candidate_pool(
        search_space=space,
        parents=parents,
        scout_count=args.candidates,
        seed=args.seed,
    )
    bounded = tuple(
        genome
        for genome in generated
        if not space.validation_errors(genome)
    )
    print(
        f"Generated {len(generated):,}; research envelope kept "
        f"{len(bounded):,}. Capital risk is classified after simulation "
        f"(deployment limit EUR {risk_limit})",
        flush=True,
    )

    measured = dict(WORLDS)["measured"]
    primary = _evaluate_population(
        dataset.paths,
        bounded,
        execution=measured,
        workers=args.workers,
        progress_every=args.progress_every,
        label="primary",
    )
    primary_survivors = []
    for evaluation in primary:
        if not _primary_allows(
            evaluation,
            minimum_challenge_ratio=args.primary_challenge_ratio,
        ):
            continue
        primary_survivors.append(evaluation)
    print(
        f"Primary causal gate kept {len(primary_survivors):,}",
        flush=True,
    )
    _write_primary(output / "primary_survivors.parquet", primary_survivors)
    # The following worlds only need survivors; release rejected tick tapes now.
    del primary

    evaluations_by_world = {
        "measured": {
            item.genome.fingerprint: item for item in primary_survivors
        }
    }
    genomes = tuple(item.genome for item in primary_survivors)
    for name, execution in WORLDS:
        if name == "measured":
            continue
        rows = _evaluate_population(
            dataset.paths,
            genomes,
            execution=execution,
            workers=args.workers,
            progress_every=args.progress_every,
            label=name,
        )
        evaluations_by_world[name] = {
            item.genome.fingerprint: item for item in rows
        }

    robust_rows = []
    assessments = []
    capital_eligible_by_fingerprint = {}
    for genome in genomes:
        scenarios = tuple(
            ScenarioEvaluation(
                name,
                evaluations_by_world[name][genome.fingerprint],
            )
            for name, _execution in WORLDS
        )
        assessment = assess_execution_robustness(
            scenarios,
            minimum_participation=args.minimum_participation,
            folds=DEFAULT_DUBAI_FOLDS,
            minimum_positive_challenge_ratio=1.0,
        )
        scenario_risks = tuple(
            assess_capital_risk(
                dataset.paths,
                tuple(result for _day, result in scenario.evaluation.results),
                genome,
                initial_capital_eur=capital,
                maximum_loss_fraction=risk_fraction,
                maximum_concurrent_signals=args.max_concurrent_signals,
                risk_context=context,
                observation_latency_ms=dict(WORLDS)[scenario.name].latency_ms,
            )
            for scenario in scenarios
        )
        prospective_ok = all(item.risk_eligible for item in scenario_risks)
        capital_eligible_by_fingerprint[genome.fingerprint] = prospective_ok
        if assessment.robustness_eligible:
            assessments.append(assessment)
        robust_rows.append({
            "fingerprint": genome.fingerprint,
            "genome_json": json.dumps(
                genome.to_dict(), sort_keys=True, separators=(",", ":")
            ),
            "rule_eligible": assessment.robustness_eligible,
            "capital_eligible": prospective_ok,
            "eligible": assessment.robustness_eligible and prospective_ok,
            "worst_net_eur": _float(assessment.worst_net_eur),
            "maximum_drawdown_eur": _float(assessment.maximum_drawdown_eur),
            "worst_challenge_net_eur": _float(
                assessment.worst_challenge_net_eur
            ),
            "positive_challenges": assessment.positive_challenges,
            "challenge_count": assessment.challenge_count,
            "minimum_participation": assessment.minimum_participation,
            "worst_return_over_drawdown": _float(
                assessment.worst_return_over_drawdown
            ),
            "prospective_worst_loss_eur": max(
                (_float(item.worst_loss_eur) or 0.0 for item in scenario_risks),
                default=None,
            ),
            **{
                f"{scenario.name}_net": _float(scenario.evaluation.net_eur)
                for scenario in scenarios
            },
        })
    behavior_frontier = _build_behavior_frontier(
        assessments,
        samples=args.daily_bootstrap_samples,
        seed=args.seed,
    )
    behavior_by_fingerprint = {
        fingerprint: group.behavior_id
        for group, _stability in behavior_frontier
        for fingerprint in group.member_fingerprints
    }
    for row in robust_rows:
        row["behavior_id"] = behavior_by_fingerprint.get(row["fingerprint"])
    pd.DataFrame(robust_rows).to_parquet(
        output / "robust_candidates.parquet", index=False
    )
    equivalence_groups = [
        {
            "behavior_id": group.behavior_id,
            "representative_fingerprint": (
                group.representative.genome.fingerprint
            ),
            "equivalent_variant_count": group.member_count,
            "member_fingerprints": list(group.member_fingerprints),
            "capital_eligible_member_fingerprints": [
                fingerprint
                for fingerprint in group.member_fingerprints
                if capital_eligible_by_fingerprint.get(fingerprint, False)
            ],
        }
        for group, _stability in behavior_frontier
    ]
    (output / "equivalence_groups.json").write_text(
        json.dumps(equivalence_groups, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    finalists = [
        {
            "rank": index,
            "behavior_id": group.behavior_id,
            "fingerprint": group.representative.genome.fingerprint,
            "genome": group.representative.genome.to_dict(),
            "equivalent_variant_count": group.member_count,
            "member_fingerprints": list(group.member_fingerprints),
            "capital_eligible_at_exact_size": (
                capital_eligible_by_fingerprint.get(
                    group.representative.genome.fingerprint,
                    False,
                )
            ),
            "capital_eligible_member_fingerprints": [
                fingerprint
                for fingerprint in group.member_fingerprints
                if capital_eligible_by_fingerprint.get(fingerprint, False)
            ],
            "worst_net_eur": str(group.representative.worst_net_eur),
            "maximum_drawdown_eur": str(
                group.representative.maximum_drawdown_eur
            ),
            "worst_challenge_net_eur": str(
                group.representative.worst_challenge_net_eur
            ),
            "positive_challenges": group.representative.positive_challenges,
            "challenge_count": group.representative.challenge_count,
            "minimum_participation": group.representative.minimum_participation,
            "daily_stability": _daily_stability_payload(stability),
        }
        for index, (group, stability) in enumerate(
            behavior_frontier,
            start=1,
        )
    ]
    (output / "finalists.json").write_text(
        json.dumps(finalists, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    deployable_finalists = [
        item for item in finalists
        if item["capital_eligible_member_fingerprints"]
    ]
    (output / "capital_eligible_finalists.json").write_text(
        json.dumps(deployable_finalists, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    run_card = {
        "schema_version": 1,
        "purpose": "offline_retrospective_demo_research",
        "from": args.from_date,
        "to": args.to_date,
        "exact_signals": len(dataset.paths),
        "excluded_signals": sum(len(items) for items in dataset.exclusions.values()),
        "source_hashes": dict(sorted(dataset.source_hashes.items())),
        "seed": args.seed,
        "requested_scouts": args.candidates,
        "parent_candidates": [item.fingerprint for item in parents],
        "local_candidates": local_count,
        "crossover_candidates": crossover_count,
        "generated_candidates": len(generated),
        "research_envelope_candidates": len(bounded),
        "primary_survivors": len(primary_survivors),
        "robust_rule_variants": len(assessments),
        "capital_eligible_rule_variants": sum(
            capital_eligible_by_fingerprint.get(
                item.genome.fingerprint,
                False,
            )
            for item in assessments
        ),
        "observational_equivalence_groups": len(finalists),
        "equivalent_variants_collapsed": len(assessments) - len(finalists),
        "robust_finalists": len(finalists),
        "capital_eligible_behavior_groups": len(deployable_finalists),
        "daily_stability": {
            "bootstrap_samples": args.daily_bootstrap_samples,
            "ranking_role": "retrospective_stability_not_oos_proof",
        },
        "account": {
            "initial_capital_eur": str(capital),
            "maximum_loss_fraction": str(risk_fraction),
            "risk_limit_eur": str(risk_limit),
            "maximum_concurrent_signals": args.max_concurrent_signals,
            "continuous_market_risk_only": True,
            "margin_contract_verified": False,
        },
        "worlds": [
            {"name": name, **asdict(execution)} for name, execution in WORLDS
        ],
        "confidence": "retrospective_only_requires_fresh_forward_data",
    }
    (output / "run_card.json").write_text(
        json.dumps(run_card, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(
        f"Robust behaviors: {len(finalists)}; capital-eligible groups: "
        f"{len(deployable_finalists)}",
        flush=True,
    )
    print(f"Output: {output.resolve()}", flush=True)
    return 0


def _build_behavior_frontier(assessments, *, samples, seed):
    groups = group_observationally_equivalent(tuple(assessments))
    daily = {
        group.representative.genome.fingerprint: assess_robust_daily_stability(
            group.representative,
            samples=samples,
            seed=seed,
        )
        for group in groups
    }
    ranked = rank_observational_groups(groups, daily)
    return tuple(
        (group, daily[group.representative.genome.fingerprint])
        for group in ranked
    )


def _candidate_pool(
    *,
    search_space,
    parents,
    scout_count,
    seed,
    seed_factory=seed_population,
    scout_factory=sample_diverse_population,
    neighborhood_factory=parameter_neighborhood,
    crossover_factory=crossover,
):
    parents = tuple(parents)
    local = tuple(
        child
        for parent in parents
        for child in neighborhood_factory(parent, search_space)
    )
    crossed = []
    for left_index, left in enumerate(parents):
        for right_index, right in enumerate(
            parents[left_index + 1:], start=left_index + 1
        ):
            crossed.extend(crossover_factory(
                left,
                right,
                search_space=search_space,
                seed=seed + left_index * 1_000 + right_index,
            ))
    generated = deduplicate((
        *parents,
        *seed_factory(search_space, seed=seed),
        *scout_factory(
            search_space,
            seed=seed + 91_337,
            count=scout_count,
        ),
        *local,
        *crossed,
    ))
    return generated, len(local), len(crossed)


def _daily_stability_payload(assessment):
    return {
        "evidence_complete": assessment.evidence_complete,
        "scenario_count": assessment.scenario_count,
        "minimum_bootstrap_probability_positive": (
            assessment.minimum_bootstrap_probability_positive
        ),
        "worst_bootstrap_p05_eur": (
            str(assessment.worst_bootstrap_p05_eur)
            if assessment.worst_bootstrap_p05_eur is not None
            else None
        ),
        "worst_normalized_bootstrap_p05_per_001": (
            str(assessment.worst_normalized_bootstrap_p05_per_001)
            if assessment.worst_normalized_bootstrap_p05_per_001 is not None
            else None
        ),
        "minimum_leave_one_day_out_positive_ratio": (
            assessment.minimum_leave_one_day_out_positive_ratio
        ),
        "maximum_positive_day_concentration": (
            assessment.maximum_positive_day_concentration
        ),
        "blockers": list(assessment.blockers),
    }


def _evaluate_population(
    paths,
    genomes,
    *,
    execution,
    workers,
    progress_every,
    label,
):
    evaluator = FastEvaluator(execution=execution)
    paths = tuple(paths)
    genomes = tuple(genomes)
    started = time.monotonic()

    def evaluate(genome):
        return CandidateEvaluation.from_results(
            genome,
            ((str(path.day), evaluator(path, genome)) for path in paths),
        )

    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for index, evaluation in enumerate(pool.map(evaluate, genomes), start=1):
            results.append(evaluation)
            if index % progress_every == 0 or index == len(genomes):
                elapsed = time.monotonic() - started
                print(
                    f"[{label}] {index:,}/{len(genomes):,} "
                    f"({elapsed:.1f}s)",
                    flush=True,
                )
    return tuple(results)


def _primary_allows(evaluation, *, minimum_challenge_ratio):
    if (
        evaluation.blockers
        or evaluation.net_eur is None
        or evaluation.net_eur <= 0
        or evaluation.max_drawdown_eur is None
        or evaluation.participation_rate < 0.50
    ):
        return False
    positive = 0
    total = 0
    for fold in DEFAULT_DUBAI_FOLDS:
        rows = tuple(
            (day, result)
            for day, result in evaluation.results
            if fold.challenge_contains(day)
        )
        challenged = CandidateEvaluation.from_results(evaluation.genome, rows)
        if challenged.blockers or challenged.net_eur is None:
            return False
        total += 1
        positive += int(challenged.net_eur > 0)
    return bool(total) and positive / total >= minimum_challenge_ratio


def _write_primary(path, evaluations):
    rows = ({
        "fingerprint": item.genome.fingerprint,
        "genome_json": json.dumps(
            item.genome.to_dict(), sort_keys=True, separators=(",", ":")
        ),
        "net_eur": _float(item.net_eur),
        "max_drawdown_eur": _float(item.max_drawdown_eur),
        "participation": item.participation_rate,
    } for item in evaluations)
    pd.DataFrame(rows).to_parquet(path, index=False)


def _load_parents(path_text, limit):
    if not path_text:
        return ()
    frame = pd.read_parquet(Path(path_text))
    required = {"genome_json", "positive_challenges", "worst_net_eur"}
    if not required.issubset(frame.columns):
        raise ValueError("parent parquet does not contain robust candidate columns")
    if "rule_eligible" in frame.columns:
        frame = frame[frame["rule_eligible"].fillna(False).astype(bool)]
    elif "eligible" in frame.columns:
        frame = frame[frame["eligible"].fillna(False).astype(bool)]
    ranked = frame.sort_values(
        ["positive_challenges", "worst_net_eur"],
        ascending=False,
        na_position="last",
    ).head(limit)
    return tuple(
        StrategyGenome.from_dict(json.loads(payload))
        for payload in ranked["genome_json"]
    )


def _float(value):
    return None if value is None else float(value)


def _dataset(args):
    root = Path(args.runtime_root)
    money_contract = json.loads(
        (root / "broker_money_contract.json").read_text(encoding="utf-8")
    )
    conversion = money_contract.get("conversion") or {}
    conversion_source = None
    if conversion.get("orientation") != "identity":
        conversion_source = VerifiedParquetTickSource(
            root / "money_ticks_cache",
            expected_symbol=str(conversion.get("symbol") or "EURUSD"),
        )
    return load_dubai_dataset(
        replay_path=root / "replay_trades.jsonl",
        audit_path=root / "observed_tick_replay_audit.jsonl",
        market_ticks=VerifiedParquetTickSource(
            root / "ticks_cache", expected_symbol="XAUUSD"
        ),
        conversion_ticks=conversion_source,
        money_contract=money_contract,
        from_date=args.from_date,
        to_date=args.to_date,
        max_hold_minutes=240,
    )


def _parser():
    parser = argparse.ArgumentParser(
        description="Bounded capital-aware Dubai strategy search"
    )
    parser.add_argument("--runtime-root", default="runtime_data")
    parser.add_argument("--from", dest="from_date", default="2026-07-27")
    parser.add_argument("--to", dest="to_date", default="2026-08-14")
    parser.add_argument(
        "--output", default="runtime_data/dubai_capital_search_v1"
    )
    parser.add_argument("--candidates", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=20260821)
    parser.add_argument("--capital", type=float, default=500.0)
    parser.add_argument("--risk-fraction", type=float, default=0.25)
    parser.add_argument("--max-concurrent-signals", type=int, default=3)
    parser.add_argument("--max-total-volume", type=float, default=1.0)
    parser.add_argument("--max-legs", type=int, default=12)
    parser.add_argument("--parent-parquet", default=None)
    parser.add_argument("--parent-limit", type=int, default=12)
    parser.add_argument("--minimum-participation", type=float, default=0.50)
    parser.add_argument("--primary-challenge-ratio", type=float, default=0.50)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--progress-every", type=int, default=250)
    parser.add_argument("--daily-bootstrap-samples", type=int, default=10_000)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
