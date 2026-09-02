from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from research.dubai_iterative.engine import ExitRecord, SimulationResult
from research.dubai_iterative.evolution import CandidateEvaluation
from research.gold_iterative.contracts import gold_555_genome
from research.gold_iterative.provider_accounting import (
    build_candidate_pip_hypotheses,
)


@dataclass(frozen=True)
class PipPath:
    signal_id: str
    direction: str


def test_candidate_pip_hypotheses_keep_distinct_accounting_models():
    genome = gold_555_genome()
    moment = datetime(2026, 8, 24, 12, tzinfo=timezone.utc)
    result = SimulationResult(
        signal_id="gold_1",
        strategy_fingerprint=genome.fingerprint,
        confidence_layer="fixture",
        entries=(),
        exits=(
            ExitRecord("a", 1, moment, 100.0, 102.0, 0.01, Decimal("2"), "tp"),
            ExitRecord("b", 2, moment, 101.0, 104.0, 0.01, Decimal("3"), "tp"),
        ),
        pnl_eur=Decimal("5"),
        exit_reason="tp",
        max_favourable_eur=Decimal("8"),
        max_adverse_eur=Decimal("1"),
        max_floating_drawdown_eur=Decimal("1"),
        max_favourable_move=5.0,
        max_adverse_move=1.0,
        blockers=(),
        last_tick_index=2,
        unfilled=False,
        filled_volume=0.02,
    )
    evaluation = CandidateEvaluation.from_results(
        genome,
        (("2026-08-24", result),),
    )
    scorecard = {
        "summaries": [{
            "claim": {
                "period_start": "2026-08-24",
                "period_end": "2026-08-24",
                "pips_gained": 250,
            },
        }],
    }

    hypotheses = build_candidate_pip_hypotheses(
        (evaluation,),
        paths=(PipPath("gold_1", "BUY"),),
        provider_scorecard=scorecard,
    )
    totals = {
        item.hypothesis_id.rsplit(":", 1)[-1]: item.period_totals[
            "2026-08-24:2026-08-24"
        ]
        for item in hypotheses
    }

    assert totals == {
        "sum_exit_leg_moves_x100": Decimal("500.0"),
        "volume_weighted_signal_move_x100": Decimal("250.00"),
        "best_exit_per_signal_x100": Decimal("300.0"),
        "max_favourable_per_signal_x100": Decimal("500.0"),
    }
    assert all(item.verified is False for item in hypotheses)


def test_candidate_pip_hypotheses_respect_sell_direction():
    genome = gold_555_genome()
    moment = datetime(2026, 8, 24, 12, tzinfo=timezone.utc)
    result = SimulationResult(
        signal_id="gold_sell",
        strategy_fingerprint=genome.fingerprint,
        confidence_layer="fixture",
        entries=(),
        exits=(
            ExitRecord(
                "a", 1, moment, 105.0, 102.0, 0.01, Decimal("3"), "tp"
            ),
        ),
        pnl_eur=Decimal("3"),
        exit_reason="tp",
        max_favourable_eur=Decimal("3"),
        max_adverse_eur=Decimal("1"),
        max_floating_drawdown_eur=Decimal("1"),
        max_favourable_move=3.0,
        max_adverse_move=1.0,
        blockers=(),
        last_tick_index=1,
        unfilled=False,
        filled_volume=0.01,
    )
    evaluation = CandidateEvaluation.from_results(
        genome,
        (("2026-08-24", result),),
    )

    hypotheses = build_candidate_pip_hypotheses(
        (evaluation,),
        paths=(PipPath("gold_sell", "SELL"),),
        provider_scorecard={
            "summaries": [{
                "claim": {
                    "period_start": "2026-08-24",
                    "period_end": "2026-08-24",
                    "pips_gained": 300,
                },
            }],
        },
    )

    assert all(
        item.period_totals["2026-08-24:2026-08-24"] == Decimal("300.0")
        for item in hypotheses
    )
