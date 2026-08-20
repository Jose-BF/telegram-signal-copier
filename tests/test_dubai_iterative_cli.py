from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd

from research.dubai_iterative.__main__ import (
    _certification_worlds,
    _certified_execution_summary,
    _cross_fold_summary,
    _load_parent_genomes,
    _overall_strategy_confidence,
    _parser,
    _stress_summary,
    _world_certification_summary,
    main,
)
from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.engine import ExecutionAssumptions
from research.dubai_iterative.oracle import (
    ExecutionScenario,
    StressReport,
    StressScenarioResult,
)


def test_adverse_certification_world_combines_latency_and_costs():
    search_execution = ExecutionAssumptions(
        latency_ms=500,
        entry_slippage=0.05,
        exit_slippage=0.05,
        spread_addition=0.05,
    )
    search_scenario = ExecutionScenario(
        "search_execution",
        latency_ms=500,
        entry_slippage=0.05,
        exit_slippage=0.05,
        spread_addition=0.05,
    )

    worlds = _certification_worlds(search_execution, search_scenario)
    name, execution, scenario = worlds[-1]

    assert name == "adverse_costs"
    assert execution.latency_ms == 500
    assert scenario.latency_ms == 500
    assert execution.entry_slippage == 0.10
    assert execution.exit_slippage == 0.10
    assert execution.spread_addition == 0.10


def test_cli_emits_bounded_progress_and_stop_reason(tmp_path, capsys):
    code = main([
        "--fixture",
        "tiny",
        "--max-generations",
        "2",
        "--patience-generations",
        "10",
        "--output-root",
        str(tmp_path),
        "--progress",
    ])
    output = capsys.readouterr().out

    assert code == 0
    assert "Generacion 1/2" in output
    assert "Generacion 2/2" in output
    assert "Parada: max_generations" in output
    assert "Estrategias evaluadas:" in output


def test_cli_fixture_publishes_a_bound_run(tmp_path):
    code = main([
        "--fixture",
        "tiny",
        "--max-generations",
        "1",
        "--output-root",
        str(tmp_path),
    ])

    cards = list(tmp_path.glob("*/run_card.json"))
    assert code == 0
    assert len(cards) == 1
    card = json.loads(cards[0].read_text(encoding="utf-8"))
    frontier = json.loads((cards[0].parent / "frontier.json").read_text(encoding="utf-8"))
    candidates = pd.read_parquet(cards[0].parent / "candidate_matrix.parquet")
    signals = pd.read_parquet(cards[0].parent / "signal_results.parquet")
    assert card["stop_reasons"] == ["max_generations"]
    assert card["live_code_changed"] is False
    assert card["confidence"] in {"retrospective_unstable", "demo_candidate"}
    assert card["selection_objective"] == (
        "positive_full_window_and_every_chronological_challenge_"
        "then_normalized_rule_quality"
    )
    assert card["grammar_version"] == 2
    assert card["imported_parent_role"] == "not_used"
    assert card["search_execution"] == {
        "entry_slippage": 0.0,
        "exit_slippage": 0.0,
        "latency_ms": 0,
        "spread_addition": 0.0,
    }
    assert card["max_hold_minutes"] == 240
    assert card["signal_coverage"]["complete"] is True
    assert card["signal_coverage"]["eligible"] == 3
    assert card["signal_coverage"]["loaded"] == 3
    assert "normalized_max_drawdown_per_001" in candidates.columns
    assert "normalized_worst_day_per_001" in candidates.columns
    assert "filled_signal_count" in candidates.columns
    assert "participation_rate" in candidates.columns
    assert all("normalized_net_per_001" in row for row in frontier)
    assert all("filled_signal_count" in row for row in frontier)
    assert all("development_daily_stability" in row for row in frontier)
    assert all(
        row["development_daily_stability"]["bootstrap_samples"] == 10_000
        for row in frontier
    )
    assert card["daily_stability_method"] == {
        "bootstrap_samples": 10_000,
        "resampling_unit": "complete_trading_day",
        "seed": 20260817,
        "validation_role": "retrospective_stability_only_not_untouched_oos",
    }
    assert {"unfilled", "filled_volume", "entry_count", "exit_count"} <= set(signals.columns)


def test_real_cli_defaults_read_ignored_runtime_evidence():
    args = _parser().parse_args([])

    assert args.replay_path == "runtime_data/replay_trades.jsonl"
    assert args.audit_path == "runtime_data/observed_tick_replay_audit.jsonl"
    assert args.money_contract == "runtime_data/broker_money_contract.json"
    assert args.max_hold_minutes == 240
    assert args.max_hold_minutes >= args.max_time_exit_minutes
    assert args.max_total_volume == 1.0
    assert args.capital_eur == 500.0
    assert args.maximum_loss_fraction == 0.25
    assert args.maximum_concurrent_signals == 3
    assert args.parent_parquet is None
    assert args.parent_limit == 12


def test_parent_loader_keeps_capital_safe_behaviorally_distinct_rules(tmp_path):
    first = StrategyGenome.baseline().with_change(
        leg_count=1,
        volume_weights=(0.10,),
    )
    duplicate_behavior = first.with_change(
        be_mode="none",
    )
    unsafe = first.with_change(
        stop_mode="none",
    )
    path = tmp_path / "parents.parquet"
    pd.DataFrame((
        {
            "genome_json": json.dumps(first.to_dict()),
            "positive_challenges": 4,
            "worst_net_eur": 12.0,
            "worst_return_over_drawdown": 2.0,
            "rule_eligible": True,
            "capital_eligible": True,
            "behavior_id": "same",
        },
        {
            "genome_json": json.dumps(duplicate_behavior.to_dict()),
            "positive_challenges": 4,
            "worst_net_eur": 11.0,
            "worst_return_over_drawdown": 1.5,
            "rule_eligible": True,
            "capital_eligible": True,
            "behavior_id": "same",
        },
        {
            "genome_json": json.dumps(unsafe.to_dict()),
            "positive_challenges": 4,
            "worst_net_eur": 99.0,
            "worst_return_over_drawdown": 9.0,
            "rule_eligible": True,
            "capital_eligible": False,
            "behavior_id": "unsafe",
        },
    )).to_parquet(path, index=False)

    loaded = _load_parent_genomes(path, limit=12)

    assert tuple(item.fingerprint for item in loaded) == (first.fingerprint,)


def test_cli_accepts_explicit_execution_costs_for_the_search_itself():
    args = _parser().parse_args([
        "--search-latency-ms",
        "750",
        "--search-entry-slippage",
        "0.04",
        "--search-exit-slippage",
        "0.06",
        "--search-spread-addition",
        "0.03",
    ])

    assert args.search_latency_ms == 750
    assert args.search_entry_slippage == 0.04
    assert args.search_exit_slippage == 0.06
    assert args.search_spread_addition == 0.03


def test_cli_accepts_parallel_workers_without_changing_research_rules():
    args = _parser().parse_args(["--workers", "6"])

    assert args.workers == 6


def test_replay_math_certificate_does_not_hide_chronological_instability():
    genome = StrategyGenome.baseline()
    development = SimpleNamespace(genome=genome, net_eur=Decimal("10"))
    winning_challenge = SimpleNamespace(genome=genome, net_eur=Decimal("2"))
    losing_challenge = SimpleNamespace(genome=genome, net_eur=Decimal("-1"))
    report = SimpleNamespace(fold_reports=(
        SimpleNamespace(
            frontier=(development,),
            challenge_evaluations=(winning_challenge,),
        ),
        SimpleNamespace(
            frontier=(development,),
            challenge_evaluations=(losing_challenge,),
        ),
    ))
    certifications = {
        genome.fingerprint: SimpleNamespace(
            evidence_complete=True,
            robustness_eligible=False,
        ),
    }

    confidence = _overall_strategy_confidence(
        report,
        certifications,
        coverage_complete=True,
        fixture=False,
    )

    assert confidence == "retrospective_unstable_replay_certified_stress_failed"


def test_only_a_candidate_positive_in_every_fold_is_called_consistent():
    genome = StrategyGenome.baseline()
    development = SimpleNamespace(genome=genome, net_eur=Decimal("10"))
    challenge = SimpleNamespace(genome=genome, net_eur=Decimal("2"))
    report = SimpleNamespace(fold_reports=tuple(
        SimpleNamespace(
            frontier=(development,),
            challenge_evaluations=(challenge,),
        )
        for _index in range(4)
    ))
    certifications = {
        genome.fingerprint: SimpleNamespace(
            evidence_complete=True,
            robustness_eligible=True,
        ),
    }

    confidence = _overall_strategy_confidence(
        report,
        certifications,
        coverage_complete=True,
        fixture=False,
    )

    assert confidence == "retrospective_consistent_replay_certified_stress_passed"


def test_chronological_consistency_does_not_hide_failed_execution_stress():
    genome = StrategyGenome.baseline()
    development = SimpleNamespace(genome=genome, net_eur=Decimal("10"))
    challenge = SimpleNamespace(genome=genome, net_eur=Decimal("2"))
    report = SimpleNamespace(fold_reports=(SimpleNamespace(
        frontier=(development,),
        challenge_evaluations=(challenge,),
    ),))
    certifications = {
        genome.fingerprint: SimpleNamespace(
            evidence_complete=True,
            robustness_eligible=False,
        ),
    }

    confidence = _overall_strategy_confidence(
        report,
        certifications,
        coverage_complete=True,
        fixture=False,
    )

    assert confidence == "retrospective_consistent_replay_certified_stress_failed"


def test_overall_confidence_cannot_hide_that_every_finalist_failed_capital_risk():
    report = SimpleNamespace(fold_reports=())
    rejected = {
        "unsafe": SimpleNamespace(
            risk_eligible=False,
            blockers=("account_loss_limit_exceeded",),
        )
    }

    confidence = _overall_strategy_confidence(
        report,
        {},
        coverage_complete=True,
        fixture=False,
        capital_risk_rejections=rejected,
    )

    assert confidence == "retrospective_capital_risk_failed"


def test_cross_fold_gate_can_certify_a_rule_discovered_in_only_one_frontier():
    genome = StrategyGenome.baseline()
    development = SimpleNamespace(genome=genome, net_eur=Decimal("10"))
    challenge = SimpleNamespace(genome=genome, net_eur=Decimal("2"))
    report = SimpleNamespace(fold_reports=(
        SimpleNamespace(
            frontier=(development,),
            challenge_evaluations=(challenge,),
        ),
        SimpleNamespace(frontier=(), challenge_evaluations=()),
    ))
    assessment = SimpleNamespace(
        genome=genome,
        robustness_eligible=True,
        worst_net_eur=Decimal("8"),
        maximum_drawdown_eur=Decimal("3"),
        worst_challenge_net_eur=Decimal("1"),
        positive_challenges=4,
        challenge_count=4,
        positive_challenge_ratio=1.0,
        minimum_participation=0.75,
    )
    cross_fold = SimpleNamespace(
        assessments=(assessment,),
        eligible=(assessment,),
        rejected=(),
        considered_count=1,
    )
    certifications = {
        genome.fingerprint: SimpleNamespace(
            evidence_complete=True,
            robustness_eligible=True,
        ),
    }

    confidence = _overall_strategy_confidence(
        report,
        certifications,
        coverage_complete=True,
        fixture=False,
        cross_fold_validation=cross_fold,
    )

    assert confidence == "retrospective_consistent_replay_certified_stress_passed"


def test_cross_fold_gate_reports_when_no_discovered_rule_survived_every_period():
    rejected = SimpleNamespace(
        genome=StrategyGenome.baseline(),
        robustness_eligible=False,
        worst_net_eur=Decimal("-2"),
        maximum_drawdown_eur=Decimal("7"),
        worst_challenge_net_eur=Decimal("-1"),
        positive_challenges=3,
        challenge_count=4,
        positive_challenge_ratio=0.75,
        minimum_participation=1.0,
    )
    cross_fold = SimpleNamespace(
        assessments=(rejected,),
        eligible=(),
        rejected=(rejected,),
        considered_count=1,
    )

    confidence = _overall_strategy_confidence(
        SimpleNamespace(fold_reports=()),
        {},
        coverage_complete=True,
        fixture=False,
        cross_fold_validation=cross_fold,
    )
    summary = _cross_fold_summary(cross_fold)

    assert confidence == "retrospective_cross_fold_failed"
    assert summary["status"] == "no_eligible_candidate"
    assert summary["considered"] == 1
    assert summary["eligible_count"] == 0
    assert summary["rejected_count"] == 1
    assert summary["validation_role"] == (
        "retrospective_segment_robustness_not_untouched_oos"
    )


def test_stress_summary_keeps_every_scenario_and_gate_visible():
    report = StressReport(
        base_net_eur=Decimal("12.34"),
        base_blockers=(),
        scenarios=(
            StressScenarioResult(
                scenario=ExecutionScenario("latency_1s", latency_ms=1_000),
                net_eur=Decimal("9.87"),
                blockers=(),
                results=(),
            ),
            StressScenarioResult(
                scenario=ExecutionScenario(
                    "adverse_costs",
                    entry_slippage=0.1,
                    exit_slippage=0.1,
                    spread_addition=0.1,
                ),
                net_eur=Decimal("-1.00"),
                blockers=("stale_fx",),
                results=(),
            ),
        ),
        promotion_eligible=False,
    )

    summary = _stress_summary(report)

    assert summary["status"] == "failed"
    assert summary["base_net_eur"] == 12.34
    assert summary["base_world"] == {
        "name": "zero_cost_zero_latency",
        "latency_ms": 0,
        "entry_slippage": 0.0,
        "exit_slippage": 0.0,
        "spread_addition": 0.0,
    }
    assert summary["scenarios"][0]["name"] == "latency_1s"
    assert summary["scenarios"][1]["blockers"] == ["stale_fx"]


def test_report_separates_measured_execution_from_zero_cost_stress_base():
    execution = ExecutionAssumptions(
        latency_ms=500,
        entry_slippage=0.05,
        exit_slippage=0.05,
        spread_addition=0.05,
    )
    certification = SimpleNamespace(
        fast_results=(
            SimpleNamespace(pnl_eur=Decimal("3.00"), blockers=()),
            SimpleNamespace(pnl_eur=Decimal("-1.25"), blockers=()),
        ),
    )
    portfolio = SimpleNamespace(
        evidence_complete=True,
        net_eur=Decimal("1.75"),
        peak_equity_eur=Decimal("4.00"),
        minimum_equity_eur=Decimal("-2.00"),
        max_drawdown_eur=Decimal("3.50"),
        max_concurrent_volume=0.06,
        max_concurrent_signals=2,
        timeline_points=100,
        blockers=(),
    )
    world = SimpleNamespace(
        name="measured",
        oracle_scenario=ExecutionScenario(
            "measured",
            latency_ms=500,
            entry_slippage=0.05,
            exit_slippage=0.05,
            spread_addition=0.05,
        ),
        certificate=SimpleNamespace(status="pass", mismatches=()),
        net_eur=Decimal("1.75"),
        blockers=(),
        portfolio=portfolio,
    )

    measured = _certified_execution_summary(certification, execution)
    worlds = _world_certification_summary(SimpleNamespace(
        status="pass",
        certified_worlds=1,
        world_count=1,
        worlds=(world,),
    ))

    assert measured["name"] == "search_execution"
    assert measured["net_eur"] == 1.75
    assert measured["latency_ms"] == 500
    assert worlds["worlds"][0]["portfolio"]["max_drawdown_eur"] == 3.5
    assert worlds["worlds"][0]["portfolio"]["max_concurrent_volume"] == 0.06
