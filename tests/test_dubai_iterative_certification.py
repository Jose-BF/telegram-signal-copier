from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from research.dubai_iterative.certification import (
    certify_genome_worlds,
    certify_finalists,
    select_finalist_genomes,
)
from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.evolution import CandidateEvaluation
from research.dubai_iterative.oracle import (
    ExecutionScenario,
    OracleCertificate,
    StressReport,
)
from research.dubai_iterative.search import (
    ChronologicalFold,
    ChronologicalSearchReport,
    SearchReport,
)


def _evaluation(genome, *, net, drawdown=5, normalized=None, blockers=()):
    normalized = net if normalized is None else normalized
    return CandidateEvaluation(
        genome=genome,
        results=(),
        net_eur=Decimal(str(net)),
        max_drawdown_eur=Decimal(str(drawdown)),
        worst_day_eur=Decimal(str(-drawdown)),
        gross_profit_eur=Decimal(str(max(net, 0))),
        gross_loss_eur=Decimal(str(max(-net, 0))),
        profit_factor=2.0,
        positive_day_concentration=0.4,
        normalized_net_per_001=Decimal(str(normalized)),
        normalized_max_drawdown_per_001=Decimal(str(drawdown)),
        normalized_worst_day_per_001=Decimal(str(-drawdown)),
        max_signal_exposure=sum(genome.volume_weights),
        complexity=1,
        blockers=tuple(blockers),
    )


def _fold_report(name, development, challenge):
    fold = ChronologicalFold(
        name,
        "2026-07-27",
        "2026-07-28",
        "2026-07-29",
        "2026-07-30",
    )
    return SearchReport(
        fold=fold,
        stop_reason="max_generations",
        generations_completed=1,
        evaluations=len(development),
        elapsed_seconds=1.0,
        frontier=tuple(development),
        challenge_evaluations=tuple(challenge),
        checkpoint_path=Path("checkpoint.json"),
        stale_generations=0,
        generation_summaries=(),
    )


def test_finalist_selection_prefers_repeatable_challenge_to_training_spike():
    spike = StrategyGenome.baseline().with_change(time_exit_min=3)
    stable = StrategyGenome.baseline().with_change(time_exit_min=30)
    report = ChronologicalSearchReport((
        _fold_report(
            "fold_1",
            (_evaluation(spike, net=100), _evaluation(stable, net=20)),
            (_evaluation(spike, net=-80), _evaluation(stable, net=8)),
        ),
        _fold_report(
            "fold_2",
            (_evaluation(stable, net=25),),
            (_evaluation(stable, net=7),),
        ),
    ))

    selected = select_finalist_genomes(report, limit=1)

    assert selected == (stable,)


def test_finalist_selection_excludes_incomplete_candidates():
    blocked = StrategyGenome.baseline().with_change(time_exit_min=5)
    clean = StrategyGenome.baseline().with_change(time_exit_min=10)
    report = ChronologicalSearchReport((
        _fold_report(
            "fold_1",
            (
                _evaluation(blocked, net=200, blockers=("missing_fx",)),
                _evaluation(clean, net=3),
            ),
            (
                _evaluation(blocked, net=100, blockers=("missing_fx",)),
                _evaluation(clean, net=2),
            ),
        ),
    ))

    selected = select_finalist_genomes(report, limit=2)

    assert selected == (clean,)


def test_finalist_selection_does_not_reward_profit_created_only_by_more_lotage():
    small = StrategyGenome.baseline().with_change(
        leg_count=1,
        volume_weights=(0.01,),
    )
    large = StrategyGenome.baseline().with_change(
        leg_count=1,
        volume_weights=(0.10,),
    )
    report = ChronologicalSearchReport((
        _fold_report(
            "fold_1",
            (
                _evaluation(small, net=2, drawdown=1, normalized=2),
                _evaluation(large, net=20, drawdown=10, normalized=2),
            ),
            (
                _evaluation(small, net=1, drawdown=1, normalized=1),
                _evaluation(large, net=10, drawdown=10, normalized=1),
            ),
        ),
    ))

    selected = select_finalist_genomes(report, limit=1)

    assert selected == (small,)


def test_finalist_selection_prefers_normalized_edge_over_larger_raw_profit():
    better_rule = StrategyGenome.baseline().with_change(
        leg_count=1,
        volume_weights=(0.01,),
        time_exit_min=30,
    )
    larger_bet = StrategyGenome.baseline().with_change(
        leg_count=1,
        volume_weights=(0.10,),
        time_exit_min=60,
    )
    report = ChronologicalSearchReport((
        _fold_report(
            "fold_1",
            (
                _evaluation(better_rule, net=3, drawdown=1, normalized=3),
                _evaluation(larger_bet, net=20, drawdown=10, normalized=2),
            ),
            (
                _evaluation(better_rule, net=2, drawdown=1, normalized=2),
                _evaluation(larger_bet, net=10, drawdown=10, normalized=1),
            ),
        ),
    ))

    selected = select_finalist_genomes(report, limit=1)

    assert selected == (better_rule,)


def test_finalist_selection_does_not_prefer_one_lucky_fill_to_broad_evidence():
    selective = StrategyGenome.baseline().with_change(time_exit_min=15)
    broad = StrategyGenome.baseline().with_change(time_exit_min=30)

    def participation(row, filled):
        return replace(
            row,
            results=tuple(
                (
                    "2026-07-27",
                    SimpleNamespace(unfilled=index >= filled),
                )
                for index in range(10)
            ),
        )

    report = ChronologicalSearchReport((
        _fold_report(
            "fold_1",
            (
                participation(_evaluation(selective, net=100, normalized=100), 1),
                participation(_evaluation(broad, net=10, normalized=10), 8),
            ),
            (
                participation(_evaluation(selective, net=100, normalized=100), 1),
                participation(_evaluation(broad, net=10, normalized=10), 8),
            ),
        ),
    ))

    selected = select_finalist_genomes(report, limit=1)

    assert selected == (broad,)


def test_finalist_certification_runs_and_records_execution_stress():
    genome = StrategyGenome.baseline()
    report = ChronologicalSearchReport((
        _fold_report(
            "fold_1",
            (_evaluation(genome, net=5),),
            (_evaluation(genome, net=2),),
        ),
    ))
    fast_result = SimpleNamespace(pnl_eur=Decimal("3.00"), blockers=())
    certificate = OracleCertificate(
        status="pass",
        mismatches=(),
        oracle_results=(),
        promotion_eligible=True,
    )
    stress = StressReport(
        base_net_eur=Decimal("3.00"),
        base_blockers=(),
        scenarios=(),
        promotion_eligible=True,
    )

    certifications = certify_finalists(
        SimpleNamespace(paths=("path",)),
        report,
        evaluator=lambda _path, _genome: fast_result,
        certifier=lambda _paths, _genome, _results: certificate,
        stresser=lambda _paths, _genome: stress,
    )

    result = certifications[genome.fingerprint]
    assert result.stress_report is stress
    assert result.robustness_eligible is True


def test_finalist_oracle_certifies_the_same_execution_used_by_search():
    genome = StrategyGenome.baseline()
    report = ChronologicalSearchReport((
        _fold_report(
            "fold_1",
            (_evaluation(genome, net=5),),
            (_evaluation(genome, net=2),),
        ),
    ))
    fast_result = SimpleNamespace(pnl_eur=Decimal("3.00"), blockers=())
    certificate = OracleCertificate("pass", (), (), True)
    stress = StressReport(Decimal("3.00"), (), (), True)
    scenario = ExecutionScenario(
        "search_costs",
        entry_slippage=0.05,
        exit_slippage=0.05,
    )
    received = []

    def certifier(_paths, _genome, _results, *, execution=None):
        received.append(execution)
        return certificate

    certify_finalists(
        SimpleNamespace(paths=("path",)),
        report,
        evaluator=lambda _path, _genome: fast_result,
        certifier=certifier,
        stresser=lambda _paths, _genome: stress,
        execution_scenario=scenario,
    )

    assert received == [scenario]


def test_finalist_certification_uses_the_cross_fold_ranked_pool():
    early_spike = StrategyGenome.baseline().with_change(time_exit_min=5)
    cross_fold_safe = StrategyGenome.baseline().with_change(time_exit_min=30)
    report = ChronologicalSearchReport((
        _fold_report(
            "fold_1",
            (
                _evaluation(early_spike, net=100, normalized=100),
                _evaluation(cross_fold_safe, net=5, normalized=5),
            ),
            (
                _evaluation(early_spike, net=50, normalized=50),
                _evaluation(cross_fold_safe, net=2, normalized=2),
            ),
        ),
    ))
    fast_result = SimpleNamespace(pnl_eur=Decimal("3.00"), blockers=())

    batch = certify_finalists(
        SimpleNamespace(paths=("path",)),
        report,
        evaluator=lambda _path, _genome: fast_result,
        ranked_genomes=(cross_fold_safe,),
        certifier=lambda *_args, **_kwargs: OracleCertificate(
            "pass", (), (), True
        ),
        stresser=lambda *_args, **_kwargs: StressReport(
            Decimal("3.00"), (), (), True
        ),
    )

    assert tuple(batch) == (cross_fold_safe.fingerprint,)
    assert batch.considered_count == 1


def test_capital_risk_receives_the_search_observation_latency():
    genome = StrategyGenome.baseline()
    report = ChronologicalSearchReport((
        _fold_report(
            "fold_1",
            (_evaluation(genome, net=5),),
            (_evaluation(genome, net=2),),
        ),
    ))
    received = []

    def assess(_paths, _results, _genome, **kwargs):
        received.append(kwargs["observation_latency_ms"])
        return SimpleNamespace(risk_eligible=True, blockers=())

    certify_finalists(
        SimpleNamespace(paths=("path",)),
        report,
        evaluator=lambda _path, _genome: SimpleNamespace(
            pnl_eur=Decimal("1.00"), blockers=()
        ),
        execution_scenario=ExecutionScenario("measured", latency_ms=500),
        initial_capital_eur=Decimal("500"),
        maximum_loss_fraction=Decimal("0.25"),
        risk_context_builder=lambda _paths: "context",
        risk_assessor=assess,
        certifier=lambda *_args, **_kwargs: OracleCertificate(
            "pass", (), (), True
        ),
        stresser=lambda *_args, **_kwargs: StressReport(
            Decimal("1.00"), (), (), True
        ),
    )

    assert received == [500]


def test_capital_aware_certification_skips_unsafe_candidate_and_keeps_searching():
    unsafe = StrategyGenome.baseline().with_change(
        leg_count=1,
        volume_weights=(0.90,),
        time_exit_min=15,
    )
    safe = StrategyGenome.baseline().with_change(
        leg_count=1,
        volume_weights=(0.02,),
        time_exit_min=30,
    )
    report = ChronologicalSearchReport((
        _fold_report(
            "fold_1",
            (
                _evaluation(unsafe, net=100, normalized=100),
                _evaluation(safe, net=20, normalized=20),
            ),
            (
                _evaluation(unsafe, net=50, normalized=50),
                _evaluation(safe, net=10, normalized=10),
            ),
        ),
    ))
    fast_result = SimpleNamespace(pnl_eur=Decimal("3.00"), blockers=())
    certificate = OracleCertificate("pass", (), (), True)
    stress = StressReport(Decimal("3.00"), (), (), True)
    calls = []

    def risk_assessor(_paths, _results, genome, **kwargs):
        calls.append((genome.fingerprint, kwargs))
        return SimpleNamespace(
            risk_eligible=genome.fingerprint == safe.fingerprint,
            blockers=(
                ()
                if genome.fingerprint == safe.fingerprint
                else ("account_loss_limit_exceeded",)
            ),
        )

    batch = certify_finalists(
        SimpleNamespace(paths=("path",)),
        report,
        evaluator=lambda _path, _genome: fast_result,
        limit=1,
        certifier=lambda _paths, _genome, _results: certificate,
        stresser=lambda _paths, _genome: stress,
        initial_capital_eur=Decimal("500"),
        maximum_loss_fraction=Decimal("0.25"),
        maximum_concurrent_signals=2,
        risk_context_builder=lambda _paths: "verified-context",
        risk_assessor=risk_assessor,
    )

    assert tuple(batch) == (safe.fingerprint,)
    assert tuple(batch.risk_rejections) == (unsafe.fingerprint,)
    assert batch.considered_count == 2
    assert len(calls) == 2
    assert all(
        call[1]["maximum_concurrent_signals"] == 2
        and call[1]["risk_context"] == "verified-context"
        for call in calls
    )


def test_capital_aware_certification_requires_a_complete_envelope():
    genome = StrategyGenome.baseline()
    report = ChronologicalSearchReport((
        _fold_report(
            "fold_1",
            (_evaluation(genome, net=5),),
            (_evaluation(genome, net=2),),
        ),
    ))

    try:
        certify_finalists(
            SimpleNamespace(paths=("path",)),
            report,
            evaluator=lambda _path, _genome: SimpleNamespace(
                pnl_eur=Decimal("1.00"), blockers=()
            ),
            initial_capital_eur=Decimal("500"),
        )
    except ValueError as exc:
        assert "capital risk arguments" in str(exc)
    else:
        raise AssertionError("an incomplete capital envelope was accepted")


def test_world_certification_requires_every_execution_world_to_match_oracle():
    genome = StrategyGenome.baseline()
    paths = (SimpleNamespace(signal_id="signal_1"),)
    fast_result = SimpleNamespace(
        signal_id="signal_1",
        pnl_eur=Decimal("1.00"),
        blockers=(),
    )
    worlds = (
        ("measured", "fast-measured", ExecutionScenario("measured")),
        ("adverse", "fast-adverse", ExecutionScenario("adverse")),
    )
    received = []

    def evaluator_factory(execution):
        received.append(("fast", execution))
        return lambda _path, _genome: fast_result

    def certifier(_paths, _genome, _results, *, execution):
        received.append(("oracle", execution.name))
        if execution.name == "adverse":
            return OracleCertificate(
                "blocked",
                (SimpleNamespace(field="pnl_eur"),),
                (),
                False,
            )
        return OracleCertificate("pass", (), (), True)

    report = certify_genome_worlds(
        paths,
        genome,
        worlds=worlds,
        evaluator_factory=evaluator_factory,
        certifier=certifier,
    )

    assert report.status == "blocked"
    assert report.certified_worlds == 1
    assert report.world_count == 2
    assert [item.name for item in report.worlds] == ["measured", "adverse"]
    assert received == [
        ("fast", "fast-measured"),
        ("oracle", "measured"),
        ("fast", "fast-adverse"),
        ("oracle", "adverse"),
    ]


def test_world_certification_requires_complete_portfolio_reconstruction():
    genome = StrategyGenome.baseline()
    paths = (SimpleNamespace(signal_id="signal_1"),)
    fast_result = SimpleNamespace(
        signal_id="signal_1",
        pnl_eur=Decimal("1.00"),
        blockers=(),
    )
    worlds = (
        ("measured", "fast-measured", ExecutionScenario("measured")),
        ("adverse", "fast-adverse", ExecutionScenario("adverse")),
    )
    calls = []

    def reconstruct(_paths, _results, *, execution, portfolio_tape):
        calls.append((execution, portfolio_tape))
        complete = execution == "fast-measured"
        return SimpleNamespace(
            evidence_complete=complete,
            blockers=() if complete else ("stale_conversion",),
        )

    report = certify_genome_worlds(
        paths,
        genome,
        worlds=worlds,
        evaluator_factory=lambda _execution: (
            lambda _path, _genome: fast_result
        ),
        certifier=lambda *_args, **_kwargs: OracleCertificate(
            "pass", (), (), True
        ),
        portfolio_tape="verified-tape",
        portfolio_reconstructor=reconstruct,
    )

    assert report.status == "blocked"
    assert report.certified_worlds == 1
    assert report.worlds[0].portfolio.evidence_complete is True
    assert report.worlds[1].portfolio.blockers == ("stale_conversion",)
    assert report.worlds[1].blockers == ("portfolio:stale_conversion",)
    assert calls == [
        ("fast-measured", "verified-tape"),
        ("fast-adverse", "verified-tape"),
    ]


def test_finalist_is_not_evidence_complete_when_any_world_portfolio_is_blocked():
    genome = StrategyGenome.baseline()
    report = ChronologicalSearchReport((
        _fold_report(
            "fold_1",
            (_evaluation(genome, net=5),),
            (_evaluation(genome, net=2),),
        ),
    ))
    fast_result = SimpleNamespace(pnl_eur=Decimal("3.00"), blockers=())
    world_report = SimpleNamespace(status="blocked", worlds=())
    received = []

    def world_certifier(paths, selected, **kwargs):
        received.append((paths, selected, kwargs))
        return world_report

    batch = certify_finalists(
        SimpleNamespace(paths=("path",)),
        report,
        evaluator=lambda _path, _genome: fast_result,
        certifier=lambda *_args, **_kwargs: OracleCertificate(
            "pass", (), (), True
        ),
        stresser=lambda *_args, **_kwargs: StressReport(
            Decimal("3.00"), (), (), True
        ),
        certification_worlds=((
            "measured",
            "fast-measured",
            ExecutionScenario("measured"),
        ),),
        world_evaluator_factory=lambda _execution: (
            lambda _path, _genome: fast_result
        ),
        portfolio_tape="canonical-tape",
        world_certifier=world_certifier,
    )

    finalist = batch[genome.fingerprint]
    assert finalist.world_certification is world_report
    assert finalist.evidence_complete is False
    assert finalist.robustness_eligible is False
    assert received[0][2]["portfolio_tape"] == "canonical-tape"
