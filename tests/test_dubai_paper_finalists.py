from copy import deepcopy

import pytest

from research import dubai_paper_campaign as campaign
from research import dubai_paper_finalists as finalists
from research.dubai_family_catalog import fixed_controls
from research.dubai_shared_lab import run_day
from research.execution_profile import execution_from_mapping
from tests.test_dubai_family_catalog import reference
from tests.test_dubai_shared_lab import lab_case
from tests.test_dubai_annual_dataset import annual_case
from tests.test_dubai_entry_probe import probe_case
from tests.test_strategy_study import case


@pytest.mark.parametrize("blocked", [False, True])
def test_full_replay_matches_frozen_screen_and_preserves_blocked_rows(lab_case, blocked):
    args = lab_case(blocked=blocked)
    inputs = campaign.AnnualInputs(*args[:3])
    day = inputs.load_day("revision_time", "2026-01-05", 1200)
    candidate = fixed_controls(reference())[0]
    profile = campaign.execution_profiles(inputs.reference["execution"], policy_extension="own_rule_be_partial_v1")["reference"]
    fast = campaign.FastEvaluator(execution=execution_from_mapping(profile))
    budget = campaign.ScreenBudget({"elapsed_seconds": 0., "evaluations": 0, "quote_visits": 0})
    screened = campaign.screen_day(day, [candidate], {"reference": fast}, budget)
    full = run_day(day, candidate, "reference", profile, finalists.FinalistBudget())
    finalists.compare_screen_cell(full, screened["rows"])
    if not blocked:
        changed = deepcopy(full)
        changed["rows"][0]["engines"]["fast"]["entries"][0]["entry_price"] += .01
        with pytest.raises(ValueError, match="screen"):
            finalists.compare_screen_cell(changed, screened["rows"])


def test_prospective_manifest_is_observation_only_and_not_runtime_compatible():
    candidate = fixed_controls(reference())[0]
    manifest = finalists.observation_manifest(candidate, "screen_sha", "finalist_sha")
    assert manifest["strategy_fingerprint"] == candidate["fingerprint"]
    assert manifest["execution_mode"] == "observation_only"
    assert manifest["broker_orders_allowed"] is False
    assert manifest["production_activation_allowed"] is False
    assert manifest["current_dubai_runtime_compatible"] is False
    assert not manifest["fresh_forward_verified"]


def test_observation_manifest_rejects_strategy_fingerprint_substitution():
    candidate = fixed_controls(reference())[0]
    candidate["fingerprint"] = "wrong"
    with pytest.raises(ValueError, match="fingerprint"):
        finalists.observation_manifest(candidate, "screen_sha", "finalist_sha")


def test_finalist_budget_is_finite():
    budget = finalists.FinalistBudget()
    budget.evaluations = finalists.BUDGET["max_evaluations"]
    with pytest.raises(ValueError, match="budget"):
        budget.charge(1)


def test_synthetic_nominee_full_archive_and_fresh_verifier(lab_case, monkeypatch):
    args = lab_case()
    controls = fixed_controls(reference())[:1]
    monkeypatch.setattr(campaign, "candidate_grid", lambda ref: controls)
    original = campaign.screen_report
    # This fixture exercises replay/archive plumbing; statistical qualification
    # is independently tested without this nomination override.
    def nominate(*values, **kwargs):
        report = original(*values, **kwargs)
        report["paper_finalists"] = [controls[0]["fingerprint"]]
        return report
    monkeypatch.setattr(campaign, "screen_report", nominate)
    campaign.run_campaign(*args)
    output = args[-1].parent / "finalists"
    report = finalists.run_finalists(args[-1], *args[:3], output)
    assert len(report["paper_candidates"]) == 1
    assert report["production_activation_allowed"] is False
    checked = finalists.verify_finalists(output)
    assert checked["status"] == "verified_current_paper_finalists_only"
    assert checked["evaluations"] == 12
    with pytest.raises(ValueError, match="exists"):
        finalists.run_finalists(args[-1], *args[:3], output)
    (output / "finalists.json").write_text("{}")
    with pytest.raises(ValueError, match="hash"):
        finalists.verify_finalists(output)
