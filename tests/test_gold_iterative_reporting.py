from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json

import pandas as pd
import pytest

from research.dubai_iterative.engine import SimulationResult
from research.dubai_iterative.evolution import CandidateEvaluation
from research.dubai_iterative.reporting import (
    ProvenanceConflictError,
    publish_run,
)
from research.gold_iterative.contracts import gold_555_genome, gold_c490_genome
from research.gold_iterative.folds import build_gold_fold_plan
from research.gold_iterative.reporting import (
    GoldEvidenceGates,
    ProviderPipHypothesis,
    build_gold_research_artifacts,
)


@dataclass(frozen=True)
class TinyPath:
    signal_id: str
    day: str
    actual_pnl_eur: Decimal


@dataclass(frozen=True)
class TinyDataset:
    paths: tuple[TinyPath, ...]
    eligible_signal_ids: tuple[str, ...]
    eligible_signal_days: dict[str, str]
    exclusions: dict[str, tuple[str, ...]]
    source_hashes: dict[str, str]
    account_currency: str = "EUR"
    currency_digits: int = 2


def _dataset() -> TinyDataset:
    paths = (
        TinyPath("gold_1", "2026-08-24", Decimal("1.25")),
        TinyPath("gold_2", "2026-08-25", Decimal("-2.25")),
        TinyPath("gold_3", "2026-08-26", Decimal("3.00")),
    )
    return TinyDataset(
        paths=paths,
        eligible_signal_ids=tuple(path.signal_id for path in paths),
        eligible_signal_days={path.signal_id: path.day for path in paths},
        exclusions={},
        source_hashes={"fixture": "reporting-evidence"},
    )


def _result(path: TinyPath, pnl: str, genome=None) -> SimulationResult:
    genome = genome or gold_555_genome()
    return SimulationResult(
        signal_id=path.signal_id,
        strategy_fingerprint=genome.fingerprint,
        confidence_layer="fixture",
        entries=(),
        exits=(),
        pnl_eur=Decimal(pnl),
        exit_reason="fixture",
        max_favourable_eur=Decimal("5.00"),
        max_adverse_eur=Decimal("1.00"),
        max_floating_drawdown_eur=Decimal("1.00"),
        max_favourable_move=1.0,
        max_adverse_move=0.2,
        blockers=(),
        last_tick_index=1,
        unfilled=False,
        filled_volume=0.05,
    )


def _evaluation(genome=None, pnl_values=("5.00", "-1.00", "2.00")) -> CandidateEvaluation:
    dataset = _dataset()
    genome = genome or gold_555_genome()
    return CandidateEvaluation.from_results(
        genome,
        (
            (path.day, _result(path, pnl, genome))
            for path, pnl in zip(
                dataset.paths,
                pnl_values,
                strict=True,
            )
        ),
    )


def _scorecard():
    return {
        "schema_version": 1,
        "provider": "Gold Signals",
        "channel": "canal2",
        "summaries": [
            {
                "provider_signal_id": "summary_week",
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
                "observed_signal_ids": ["gold_1", "gold_2", "gold_3"],
            }
        ],
    }


def _hypothesis():
    return ProviderPipHypothesis(
        hypothesis_id="sum_exit_moves_x100",
        description="Sum directional exit moves using an assumed 0.01 pip",
        period_totals={"2026-08-24:2026-08-26": Decimal("385")},
        verified=False,
    )


def _gates(**changes) -> GoldEvidenceGates:
    values = {
        "actual_mt5_complete": True,
        "tick_paths_complete": True,
        "account_currency_money_complete": True,
        "oracle_parity_complete": True,
        "chronological_challenge_complete": True,
        "source_manifest_complete": True,
    }
    values.update(changes)
    return GoldEvidenceGates(**values)


def _artifacts(gates: GoldEvidenceGates):
    dataset = _dataset()
    return build_gold_research_artifacts(
        dataset,
        fold_plan=build_gold_fold_plan(dataset),
        frontier_evaluations=(_evaluation(),),
        candidate_evaluations=(
            _evaluation(),
            _evaluation(gold_c490_genome(), ("4.00", "0.00", "1.00")),
        ),
        generation_rows=(
            {"fold": "gold_fold_01", "generation": 1, "evaluated": 37},
        ),
        gates=gates,
        provider_scorecard=_scorecard(),
        provider_pip_hypotheses=(_hypothesis(),),
    )


def test_gold_report_never_mixes_mt5_eur_simulated_eur_and_provider_pips():
    artifacts = _artifacts(_gates(oracle_parity_complete=False))
    card = artifacts.run_card

    assert card["financial_totals"]["actual_mt5"] == {
        "amount": "2.00",
        "currency": "EUR",
        "kind": "observed_mt5",
    }
    assert card["financial_totals"]["simulated_frontier"][0] == {
        "strategy_fingerprint": gold_555_genome().fingerprint,
        "amount": "6.00",
        "currency": "EUR",
        "kind": "counterfactual_simulation",
    }
    assert card["provider_claim_totals"][0]["amount"] == "400"
    assert card["provider_claim_totals"][0]["unit"] == "provider_pips"
    assert card["selection"]["ranking_allowed"] is False
    assert card["selection"]["status"] == "diagnostic_only"
    assert card["selection"]["selected_strategy_fingerprint"] is None
    assert "oracle_parity_incomplete" in card["selection"]["blockers"]
    assert "retrospective_rank" not in artifacts.frontier[0]

    distances = pd.DataFrame(artifacts.extra_tables["provider_claim_distance.parquet"])
    row = distances.iloc[0].to_dict()
    assert row["provider_claim_pips"] == "400"
    assert row["hypothesis_pips"] == "385"
    assert row["distance_pips"] == "-15"
    assert row["hypothesis_verified"] == False
    assert row["usable_for_strategy_selection"] == False


def test_all_hard_gates_allow_only_retrospective_ranking_not_a_live_winner():
    artifacts = _artifacts(_gates())

    assert artifacts.run_card["selection"] == {
        "ranking_allowed": True,
        "status": "retrospective_ranked_requires_untouched_forward",
        "blockers": [],
        "selected_strategy_fingerprint": None,
        "promotion_eligible": False,
    }
    assert artifacts.frontier[0]["retrospective_rank"] == 1


def test_gold_publication_keeps_daily_and_claim_evidence_immutable(tmp_path):
    artifacts = _artifacts(_gates())
    published = publish_run(artifacts, tmp_path)

    assert (published.run_dir / "daily_totals.parquet").is_file()
    assert (published.run_dir / "provider_claim_distance.parquet").is_file()
    assert json.loads(
        (published.run_dir / "provider_claim_scorecard.json").read_text(
            encoding="utf-8"
        )
    )["provider"] == "Gold Signals"
    assert len(pd.read_parquet(published.run_dir / "candidate_matrix.parquet")) == 2
    assert len(pd.read_parquet(published.run_dir / "daily_totals.parquet")) == 6

    (published.run_dir / "daily_totals.parquet").write_bytes(b"corrupt")
    with pytest.raises(ProvenanceConflictError, match="daily_totals.parquet"):
        publish_run(artifacts, tmp_path)
