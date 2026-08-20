"""Fail-closed finalist selection and independent replay certification."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from decimal import Decimal
import time
from typing import Callable, Sequence

from .contracts import StrategyGenome
from .engine import SimulationResult
from .oracle import (
    ExecutionScenario,
    OracleCertificate,
    StressReport,
    certify_candidate,
    stress_candidate,
)
from .risk import (
    CapitalRiskAssessment,
    assess_capital_risk,
    build_capital_risk_context,
)
from .search import ChronologicalSearchReport


@dataclass(frozen=True)
class FinalistCertification:
    genome: StrategyGenome
    certificate: OracleCertificate
    stress_report: StressReport
    fast_results: tuple[SimulationResult, ...]
    elapsed_seconds: float
    capital_risk: CapitalRiskAssessment | None = None
    world_certification: GenomeWorldCertification | None = None

    @property
    def evidence_complete(self) -> bool:
        return (
            self.certificate.status == "pass"
            and all(
                result.pnl_eur is not None and not result.blockers
                for result in self.fast_results
            )
            and (
                self.world_certification is None
                or self.world_certification.status == "pass"
            )
        )

    @property
    def robustness_eligible(self) -> bool:
        return (
            self.evidence_complete
            and self.stress_report.promotion_eligible
            and (
                self.capital_risk is None
                or self.capital_risk.risk_eligible
            )
        )


@dataclass(frozen=True)
class FinalistCertificationBatch(Mapping[str, FinalistCertification]):
    certifications: Mapping[str, FinalistCertification]
    risk_rejections: Mapping[str, CapitalRiskAssessment]
    considered_count: int

    def __getitem__(self, key: str) -> FinalistCertification:
        return self.certifications[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.certifications)

    def __len__(self) -> int:
        return len(self.certifications)


@dataclass(frozen=True)
class WorldCertification:
    name: str
    oracle_scenario: ExecutionScenario
    certificate: OracleCertificate
    fast_results: tuple[object, ...]
    net_eur: Decimal | None
    blockers: tuple[str, ...]
    portfolio: object | None = None


@dataclass(frozen=True)
class GenomeWorldCertification:
    genome: StrategyGenome
    worlds: tuple[WorldCertification, ...]
    status: str
    certified_worlds: int
    world_count: int

    @property
    def evidence_complete(self) -> bool:
        return self.status == "pass"


CertificationProgress = Callable[[int, int, StrategyGenome], None]


def certify_genome_worlds(
    paths: Sequence[object],
    genome: StrategyGenome,
    *,
    worlds: Sequence[tuple[str, object, ExecutionScenario]],
    evaluator_factory,
    certifier=certify_candidate,
    portfolio_tape: object | None = None,
    portfolio_reconstructor=None,
) -> GenomeWorldCertification:
    """Recalculate every fixed execution world with the scalar oracle."""

    paths = tuple(paths)
    worlds = tuple(worlds)
    if not worlds:
        raise ValueError("at least one certification world is required")
    names = [str(name) for name, _fast, _oracle in worlds]
    if any(not name for name in names) or len(set(names)) != len(names):
        raise ValueError("certification world names must be non-empty and unique")
    if portfolio_tape is None and portfolio_reconstructor is not None:
        raise ValueError("portfolio_reconstructor requires a portfolio_tape")
    if portfolio_tape is not None and portfolio_reconstructor is None:
        from .portfolio import reconstruct_portfolio

        portfolio_reconstructor = reconstruct_portfolio

    reports = []
    for name, fast_execution, oracle_scenario in worlds:
        evaluator = evaluator_factory(fast_execution)
        fast_results = tuple(evaluator(path, genome) for path in paths)
        certificate = certifier(
            paths,
            genome,
            fast_results,
            execution=oracle_scenario,
        )
        result_blockers = tuple(dict.fromkeys(
            blocker
            for result in fast_results
            for blocker in tuple(getattr(result, "blockers", ()) or ())
        ))
        portfolio = None
        portfolio_blockers: tuple[str, ...] = ()
        if portfolio_tape is not None:
            portfolio = portfolio_reconstructor(
                paths,
                fast_results,
                execution=fast_execution,
                portfolio_tape=portfolio_tape,
            )
            portfolio_blockers = tuple(
                f"portfolio:{blocker}"
                for blocker in tuple(getattr(portfolio, "blockers", ()) or ())
            )
        blockers = tuple(dict.fromkeys((
            *result_blockers,
            *portfolio_blockers,
        )))
        values = tuple(getattr(result, "pnl_eur", None) for result in fast_results)
        net = (
            sum(values, start=Decimal("0"))
            if all(value is not None for value in values)
            else None
        )
        reports.append(WorldCertification(
            name=str(name),
            oracle_scenario=oracle_scenario,
            certificate=certificate,
            fast_results=fast_results,
            net_eur=net,
            blockers=blockers,
            portfolio=portfolio,
        ))
    certified = sum(
        item.certificate.status == "pass"
        and not item.blockers
        and (
            item.portfolio is None
            or bool(getattr(item.portfolio, "evidence_complete", False))
        )
        for item in reports
    )
    return GenomeWorldCertification(
        genome=genome,
        worlds=tuple(reports),
        status="pass" if certified == len(reports) else "blocked",
        certified_worlds=certified,
        world_count=len(reports),
    )


def select_finalist_genomes(
    report: ChronologicalSearchReport,
    *,
    limit: int,
) -> tuple[StrategyGenome, ...]:
    """Prefer repeated chronological survival, never a training-only spike."""

    if limit < 0:
        raise ValueError("limit must be non-negative")
    if limit == 0:
        return ()
    return _rank_finalist_genomes(report)[:limit]


def _rank_finalist_genomes(
    report: ChronologicalSearchReport,
) -> tuple[StrategyGenome, ...]:
    observations: dict[str, list[tuple[object, object]]] = defaultdict(list)
    genomes: dict[str, StrategyGenome] = {}
    for fold in report.fold_reports:
        challenge = {
            item.genome.fingerprint: item
            for item in fold.challenge_evaluations
        }
        for development in fold.frontier:
            fingerprint = development.genome.fingerprint
            challenged = challenge.get(fingerprint)
            if challenged is None:
                continue
            if (
                development.blockers
                or challenged.blockers
                or development.net_eur is None
                or challenged.net_eur is None
                or development.max_drawdown_eur is None
                or challenged.max_drawdown_eur is None
                or development.normalized_net_per_001 is None
                or challenged.normalized_net_per_001 is None
                or development.normalized_max_drawdown_per_001 is None
                or challenged.normalized_max_drawdown_per_001 is None
            ):
                continue
            observations[fingerprint].append((development, challenged))
            genomes[fingerprint] = development.genome

    ranked: list[tuple[tuple[object, ...], str]] = []
    for fingerprint, rows in observations.items():
        challenge_values = [
            challenge.normalized_net_per_001
            for _development, challenge in rows
        ]
        development_values = [
            development.normalized_net_per_001
            for development, _challenge in rows
        ]
        drawdowns = [
            max(
                development.normalized_max_drawdown_per_001,
                challenge.normalized_max_drawdown_per_001,
            )
            for development, challenge in rows
        ]
        exposures = [
            max(development.max_signal_exposure, challenge.max_signal_exposure)
            for development, challenge in rows
        ]
        challenge_participation = [
            challenge.participation_rate
            for _development, challenge in rows
        ]
        filled_observations = sum(
            development.filled_signal_count + challenge.filled_signal_count
            for development, challenge in rows
        )
        score = (
            sum(value > 0 for value in challenge_values),
            len(rows),
            min(challenge_participation),
            filled_observations,
            min(challenge_values),
            sum(challenge_values, start=Decimal("0")),
            min(development_values),
            -max(drawdowns),
            -max(exposures),
        )
        ranked.append((score, fingerprint))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return tuple(genomes[fingerprint] for _score, fingerprint in ranked)


def certify_finalists(
    dataset,
    report: ChronologicalSearchReport,
    *,
    evaluator,
    limit: int = 1,
    progress_callback: CertificationProgress | None = None,
    certifier=certify_candidate,
    stresser=stress_candidate,
    execution_scenario: ExecutionScenario | None = None,
    initial_capital_eur: Decimal | None = None,
    maximum_loss_fraction: Decimal | None = None,
    maximum_concurrent_signals: int = 1,
    risk_context_builder=build_capital_risk_context,
    risk_assessor=assess_capital_risk,
    ranked_genomes: Sequence[StrategyGenome] | None = None,
    certification_worlds: Sequence[tuple[str, object, ExecutionScenario]] = (),
    world_evaluator_factory=None,
    portfolio_tape: object | None = None,
    world_certifier=certify_genome_worlds,
) -> FinalistCertificationBatch:
    if limit < 0:
        raise ValueError("limit must be non-negative")
    capital_enabled = (
        initial_capital_eur is not None
        or maximum_loss_fraction is not None
    )
    if capital_enabled and (
        initial_capital_eur is None
        or maximum_loss_fraction is None
    ):
        raise ValueError(
            "capital risk arguments must be supplied together"
        )
    certification_worlds = tuple(certification_worlds)
    if certification_worlds and world_evaluator_factory is None:
        raise ValueError(
            "certification_worlds require world_evaluator_factory"
        )
    if not certification_worlds and portfolio_tape is not None:
        raise ValueError("portfolio_tape requires certification_worlds")
    ranked = (
        _rank_finalist_genomes(report)
        if ranked_genomes is None
        else tuple(dict.fromkeys(ranked_genomes))
    )
    if limit == 0:
        return FinalistCertificationBatch({}, {}, 0)
    certifications: dict[str, FinalistCertification] = {}
    risk_rejections: dict[str, CapitalRiskAssessment] = {}
    paths = tuple(dataset.paths)
    risk_context = (
        risk_context_builder(paths)
        if capital_enabled
        else None
    )
    considered = 0
    for genome in ranked:
        if len(certifications) >= limit:
            break
        considered += 1
        if progress_callback is not None:
            progress_callback(
                considered,
                len(ranked),
                genome,
            )
        started = time.monotonic()
        fast_results = tuple(evaluator(path, genome) for path in paths)
        capital_risk = None
        if capital_enabled:
            capital_risk = risk_assessor(
                paths,
                fast_results,
                genome,
                initial_capital_eur=initial_capital_eur,
                maximum_loss_fraction=maximum_loss_fraction,
                maximum_concurrent_signals=maximum_concurrent_signals,
                risk_context=risk_context,
                observation_latency_ms=(
                    execution_scenario.latency_ms
                    if execution_scenario is not None
                    else 0
                ),
            )
            if not capital_risk.risk_eligible:
                risk_rejections[genome.fingerprint] = capital_risk
                continue
        if execution_scenario is None:
            certificate = certifier(paths, genome, fast_results)
        else:
            certificate = certifier(
                paths,
                genome,
                fast_results,
                execution=execution_scenario,
            )
        stress_report = stresser(paths, genome)
        world_certification = (
            world_certifier(
                paths,
                genome,
                worlds=certification_worlds,
                evaluator_factory=world_evaluator_factory,
                portfolio_tape=portfolio_tape,
            )
            if certification_worlds
            else None
        )
        certifications[genome.fingerprint] = FinalistCertification(
            genome=genome,
            certificate=certificate,
            stress_report=stress_report,
            fast_results=fast_results,
            elapsed_seconds=time.monotonic() - started,
            capital_risk=capital_risk,
            world_certification=world_certification,
        )
    return FinalistCertificationBatch(
        certifications=certifications,
        risk_rejections=risk_rejections,
        considered_count=considered,
    )
