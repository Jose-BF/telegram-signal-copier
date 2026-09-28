from dataclasses import replace
from decimal import Decimal
import json

import pytest

from research.dubai_iterative.__main__ import _OwnRulesPlan
from research.dubai_iterative.contracts import SearchSpace, StrategyGenome
from research.dubai_iterative.evolution import CandidateEvaluation
from research.dubai_iterative.recursive import (
    CampaignPolicy, development_metrics, extension_decision, run_recursive_campaign,
)
from research.dubai_iterative.search import ChronologicalFold
from research.dubai_iterative.search import run_search
from research.dubai_iterative.contracts import SearchBudget
from tests.test_dubai_iterative_search import TinyDataset, TinyPath, _flat_evaluator
import research.dubai_iterative.recursive as recursive


def dataset():
    return TinyDataset(tuple(TinyPath(str(i), f"2026-07-{27 + i:02}") for i in range(4)), {"fixture": "abc"})


def fold():
    return ChronologicalFold("campaign", "2026-07-27", "2026-07-29", "2026-07-30", "2026-07-30")


def plan(mode="delay"):
    base = StrategyGenome.baseline().with_change(entry_mode=mode, entry_value=1., time_exit_min=15)
    values = tuple(float(i) for i in range(1, 31))
    return _OwnRulesPlan((base,), (("entry_value", values),), tuple(base.with_change(entry_value=v) for v in values))


def metric(net="10", dd="5", blocks=("3", "3", "4"), **changes):
    return dict(fingerprint="a", complete=True, adjusted_net=net, drawdown=dd,
                worst_day="-2", blocks=list(blocks), participation=1., concentration=.4, **changes)


def test_extension_requires_material_multi_block_gain_without_risk_inflation():
    policy = CampaignPolicy()
    assert extension_decision([metric()], [metric("12", blocks=("4", "4", "4"))], policy)["extend"]
    assert not extension_decision([metric()], [metric("10.01")], policy)["extend"]
    assert not extension_decision([metric()], [metric("12", "8")], policy)["extend"]
    assert not extension_decision([metric()], [metric("15", blocks=("1", "1", "13"))], policy)["extend"]
    assert not extension_decision([], [metric()], policy)["extend"]


def test_unknown_or_partial_money_never_drives_extension():
    strategy = plan().seeds[0]
    rows = [(p.day, _flat_evaluator(p, strategy)) for p in dataset().paths[:3]]
    evaluation = CandidateEvaluation.from_results(strategy, rows)
    blocks = tuple((p.day,) for p in dataset().paths[:3])
    complete = development_metrics(evaluation, ("0", "1", "2"), blocks)
    assert complete["complete"]
    assert not development_metrics(evaluation, ("0", "1", "2", "missing"), blocks)["complete"]
    incomplete = replace(evaluation, net_eur=None)
    assert not development_metrics(incomplete, ("0", "1", "2"), blocks)["complete"]
    assert not extension_decision([metric()], [dict(metric("50"), complete=False)], CampaignPolicy())["extend"]


def run(tmp_path, **kwargs):
    return run_recursive_campaign(dataset(), fold=fold(), eligible_signal_days={str(i): f"2026-07-{27+i:02}" for i in range(4)},
        families={"delay": plan()}, search_space=SearchSpace(), evaluator=kwargs.pop("evaluator", _flat_evaluator),
        output_dir=tmp_path / "campaign", policy=kwargs.pop("policy", CampaignPolicy(initial_per_family=6, step_per_family=6,
        max_evaluations=18, population_size=3)), **kwargs)


def test_campaign_never_evaluates_holdout_and_stops_on_flat_development(tmp_path):
    visited = []
    def evaluate(path, genome):
        visited.append(path.day)
        return _flat_evaluator(path, genome)
    result = run(tmp_path, evaluator=evaluate)
    assert result["total_evaluations"] == 6
    assert result["status"] == "complete"
    assert result["families"]["delay"]["stop_reason"] == "no_material_stable_gain"
    assert "2026-07-30" not in visited
    assert result["selected_policy"] is None
    receipts = list((tmp_path / "campaign" / "delay").glob("generation_*.json"))
    assert sum(len(json.loads(p.read_text())["candidates"]) for p in receipts) == 6


def test_completed_resume_is_read_only_and_identity_change_is_rejected(tmp_path):
    run(tmp_path)
    def forbidden(*args):
        raise AssertionError("completed resume must not evaluate")
    # Evaluator identity is part of the protocol, even when work is complete.
    with pytest.raises(ValueError, match="identity"):
        run(tmp_path, evaluator=forbidden, resume=True)
    assert run(tmp_path, resume=True)["total_evaluations"] == 6
    with pytest.raises(ValueError, match="identity"):
        run(tmp_path, resume=True, policy=CampaignPolicy(initial_per_family=6, step_per_family=6,
            max_evaluations=21, population_size=3))


@pytest.mark.parametrize("change", [{"max_evaluations": 5}, {"population_size": True},
                                   {"initial_per_family": 7}, {"max_wall_seconds": 0}])
def test_invalid_budget_fails_before_any_evaluation(change):
    with pytest.raises(ValueError):
        CampaignPolicy(**dict(dict(initial_per_family=6, step_per_family=6, population_size=3), **change))


def test_duplicate_family_domain_and_missing_denominator_fail_before_write(tmp_path):
    with pytest.raises(ValueError, match="overlap"):
        run_recursive_campaign(dataset(), fold=fold(), eligible_signal_days={str(i): f"2026-07-{27+i:02}" for i in range(4)},
            families={"first": plan(), "second": plan()}, search_space=SearchSpace(), evaluator=_flat_evaluator,
            output_dir=tmp_path / "duplicate")
    with pytest.raises(ValueError, match="denominator"):
        run_recursive_campaign(dataset(), fold=fold(), eligible_signal_days={"0": "2026-07-27"},
            families={"first": plan()}, search_space=SearchSpace(), evaluator=_flat_evaluator,
            output_dir=tmp_path / "missing")
    assert not (tmp_path / "duplicate").exists()
    assert not (tmp_path / "missing").exists()


def test_damaged_checkpoint_or_generation_is_not_silently_resumed(tmp_path):
    run(tmp_path)
    path = next((tmp_path / "campaign" / "delay").glob("generation_*.json"))
    path.write_text("{}")
    with pytest.raises(ValueError, match="artifact"):
        run(tmp_path, resume=True)


def test_expansion_obeys_global_unique_budget_and_clean_resume_matches_full_run(tmp_path, monkeypatch):
    monkeypatch.setattr(recursive, "extension_decision", lambda *args: {"extend": True, "reason": "fixture_gain"})
    def execute(target, **kwargs):
        return run_recursive_campaign(dataset(), fold=fold(),
            eligible_signal_days={str(i): f"2026-07-{27+i:02}" for i in range(4)},
            families={"delay": plan(), "pullback": plan("pullback")}, search_space=SearchSpace(),
            evaluator=_flat_evaluator, output_dir=target, policy=CampaignPolicy(initial_per_family=6,
                step_per_family=6, population_size=3, max_evaluations=24), **kwargs)
    full = execute(tmp_path / "full")
    paused = execute(tmp_path / "resumed", max_stages=1)
    assert paused["status"] == "paused" and paused["total_evaluations"] == 6
    resumed = execute(tmp_path / "resumed", resume=True)
    assert full["total_evaluations"] == resumed["total_evaluations"] == 24
    assert full["stages"] == resumed["stages"]
    assert [s["family"] for s in full["stages"]] == ["delay", "pullback", "delay", "pullback"]
    assert full["families"]["delay"]["snapshot"] == resumed["families"]["delay"]["snapshot"]


def test_missing_signal_is_retained_and_cannot_authorize_expansion(tmp_path):
    evidence = {str(i): f"2026-07-{27+i:02}" for i in range(4)} | {"missing": "2026-07-29"}
    result = run_recursive_campaign(dataset(), fold=fold(), eligible_signal_days=evidence,
        families={"delay": plan()}, search_space=SearchSpace(), evaluator=_flat_evaluator,
        output_dir=tmp_path / "missing", policy=CampaignPolicy(initial_per_family=6,
            step_per_family=6, population_size=3, max_evaluations=12))
    assert result["total_evaluations"] == 6
    assert all(not row["complete"] and row["eligible_signals"] == 4 for row in result["families"]["delay"]["snapshot"])


def test_time_limit_is_global_and_completed_work_is_not_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(recursive, "extension_decision", lambda *args: {"extend": True, "reason": "fixture_gain"})
    timer = [0.]
    def evaluate(path, genome):
        timer[0] += 1
        return _flat_evaluator(path, genome)
    result = run(tmp_path, evaluator=evaluate, clock=lambda: timer[0],
        policy=CampaignPolicy(initial_per_family=6, step_per_family=6, population_size=3,
            max_evaluations=24, max_wall_seconds=15))
    assert result["total_evaluations"] == 5
    assert result["elapsed_seconds"] == 15
    assert result["stop_reason"] == "max_wall_seconds"


def test_interrupted_stage_is_not_automatically_replayed(tmp_path):
    def failure(*args):
        raise RuntimeError("fixture interruption")
    with pytest.raises(RuntimeError, match="interruption"):
        run(tmp_path, evaluator=failure)
    with pytest.raises(ValueError, match="interrupted stage"):
        run(tmp_path, evaluator=failure, resume=True)


def test_partial_population_keeps_every_unevaluated_proposal_pending(tmp_path):
    evaluated = set()
    def receive(fold, generation, rows):
        evaluated.update(item.genome.fingerprint for item in rows)
    result = run_search(dataset(), fold=fold(), budget=SearchBudget(max_evaluations=6),
        search_space=SearchSpace(), output_dir=tmp_path, population_size=3,
        evaluator=_flat_evaluator, evaluation_callback=receive, **plan().search_options())
    checkpoint = json.loads(result.checkpoint_path.read_text())
    pending = {StrategyGenome.from_dict(row).fingerprint for row in checkpoint["next_population"]}
    assert len(evaluated) == 6
    assert set(checkpoint["seen_fingerprints"]) == evaluated | pending
