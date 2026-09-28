from copy import deepcopy
import json

import pytest

from research import dubai_paper_campaign as campaign
from research.dubai_family_catalog import fixed_controls
from research.dubai_iterative.contracts import StrategyGenome
from tests.test_dubai_family_catalog import reference
from tests.test_dubai_shared_lab import lab_case
from tests.test_dubai_annual_dataset import annual_case
from tests.test_dubai_entry_probe import probe_case
from tests.test_strategy_study import case


def result(key, day, net="2.00", status="simulated", volume=.02):
    return {"trigger_id": key, "day": day, "status": status, "pnl_eur": net,
            "filled_volume": volume, "reasons": []}


def test_domain_is_frozen_unique_bounded_and_covers_all_fixed_families():
    rows = campaign.candidate_grid(reference())
    assert 16 < len(rows) <= 128
    assert rows == campaign.candidate_grid(reference())
    assert len({r["fingerprint"] for r in rows}) == len(rows)
    assert {r["family"] for r in rows} == {r["family"] for r in fixed_controls(reference())}
    for row in rows:
        genome = StrategyGenome.from_dict(row["strategy"])
        assert not genome.validation_errors()
        assert genome.stop_value == 10 and sum(genome.volume_weights) == .02
        assert genome.provider_management_mode == "ignore"
        assert genome.context_filter_mode != "max_volatility"
        assert genome.time_exit_mode == "always" and genome.time_exit_min <= 15


def test_statistics_keep_missing_days_separate_from_zero_and_charge_volume():
    rows = [result("a", "2026-01-02"), result("b", "2026-01-02", "-1.00"),
            result("c", "2026-02-02", None, "day_data_blocked", 0),
            result("d", "2026-03-02", "0.00", "exposure_not_admitted", 0)]
    stats = campaign.summarize_rows(rows)
    assert stats["net_after_cost_eur"] == "0.60"
    assert stats["eligible"] == 4 and stats["data_blocked"] == 1
    assert stats["executed"] == 2 and stats["coverage_fraction"] == .75
    assert "2026-02" not in stats["monthly"]
    assert stats["monthly"]["2026-03"] == "0.00"
    assert not stats["full_universe_complete"]


@pytest.mark.parametrize("net", ["NaN", "Infinity", "-Infinity", None])
def test_simulated_money_is_not_fabricated(net):
    with pytest.raises(ValueError):
        campaign.summarize_rows([result("a", "2026-01-02", net)])


def test_duplicate_identity_is_not_double_counted():
    row = result("a", "2026-01-02")
    with pytest.raises(ValueError, match="duplicate"):
        campaign.summarize_rows([row, row])


def test_engine_incident_cannot_rank_as_a_candidate():
    rows = [result("a", "2026-01-02", None, "engine_incident", 0)]
    stats = campaign.summarize_rows(rows)
    assert not stats["engine_complete"] and stats["net_after_cost_eur"] is None


def test_future_check_results_do_not_choose_a_past_window():
    groups = {}
    for candidate, net in (("a", "2.00"), ("b", "1.00")):
        for profile in ("reference", "adverse_execution"):
            groups[(candidate, "revision_time", profile)] = [
                result(str(i), "2026-01-02", net) for i in range(30)] + [
                result("future", "2026-03-01", "-2.00")]
    fold = {"fold_id": "one", "scenario": "revision_time", "complete_calendar_check": True,
            "development_entry_ids": [str(i) for i in range(30)], "check_entry_ids": ["future"]}
    before = campaign.rolling_choices(groups, [fold])
    altered = deepcopy(groups)
    altered[("b", "revision_time", "adverse_execution")][-1]["pnl_eur"] = "99999.00"
    after = campaign.rolling_choices(altered, [fold])
    assert before[0]["candidate"] == after[0]["candidate"] == "a"
    assert before[0]["development"] == after[0]["development"]
    assert before[0]["status"] == "retrospective_not_fresh_oos"


def test_no_trade_or_negative_rule_is_not_a_paper_nominee():
    groups = {(key, scenario, profile): [result("a", "2026-01-02", net, status, volume)]
              for key, net, status, volume in (("none", "0.00", "unfilled", 0), ("loss", "-1.00", "simulated", .02))
              for scenario in campaign.SCENARIOS for profile in campaign.PROFILE_NAMES}
    report = campaign.screen_report(groups, [], [{"fingerprint": k, "family": k} for k in ("none", "loss")])
    assert report["paper_finalists"] == []
    assert report["selection"]["selected_policy"] is None
    assert report["live_activation_allowed"] is False


def test_small_annual_campaign_preserves_rows_and_verifies_archive(lab_case, monkeypatch):
    args = lab_case()
    cleared = []
    clear = campaign.FastEvaluator.clear_cache
    def observe_clear(self):
        cleared.append(len(self._cache))
        clear(self)
    monkeypatch.setattr(campaign.FastEvaluator, "clear_cache", observe_clear)
    monkeypatch.setattr(campaign, "candidate_grid", lambda ref: fixed_controls(ref)[:1])
    report = campaign.run_campaign(*args)
    assert report["paper_finalists"] == []
    assert report["selection"]["selected_policy"] is None
    assert campaign.verify_campaign(args[-1])["status"] == "verified_paper_screen_only"
    state = json.loads((args[-1] / "state.json").read_text())
    assert state["status"] == "complete" and state["evaluations"] > 0
    assert len(cleared) == len(state["days"]) * len(campaign.PROFILE_NAMES)
    with pytest.raises(ValueError, match="exists"):
        campaign.run_campaign(*args)
    assert campaign.run_campaign(*args, resume=True) == report
    output = args[-1] / "screen.json"
    output.write_text("{}")
    with pytest.raises(ValueError, match="hash"):
        campaign.verify_campaign(args[-1])


def test_incomplete_day_does_not_execute_or_erase_good_sibling(lab_case, monkeypatch):
    args = lab_case(blocked=True)
    inputs = campaign.AnnualInputs(*args[:3])
    day = inputs.load_day("revision_time", "2026-01-05", 1200)
    state = {"elapsed_seconds": 0., "evaluations": 0, "quote_visits": 0}
    def unexpected(*_):
        pytest.fail("blocked day reached the engine")
    report = campaign.screen_day(day, fixed_controls(reference())[:1], {"reference": unexpected}, campaign.ScreenBudget(state))
    assert len(report["rows"]) == 2
    assert {r["status"] for r in report["rows"]} == {"day_data_blocked"}
    assert all(r["pnl_eur"] is None for r in report["rows"])
    assert state["evaluations"] == 0


def test_budget_and_carried_runtime_cannot_reset_on_resume():
    state = {"elapsed_seconds": campaign.BUDGET["max_wall_seconds"], "evaluations": 0, "quote_visits": 0}
    with pytest.raises(TimeoutError):
        campaign.ScreenBudget(state).charge(1)
    state = {"elapsed_seconds": 0., "evaluations": campaign.BUDGET["max_evaluations"], "quote_visits": 0}
    with pytest.raises(ValueError, match="budget"):
        campaign.ScreenBudget(state).charge(1)


def test_missing_development_check_overlap_is_rejected():
    fold = {"fold_id": "bad", "scenario": "revision_time", "complete_calendar_check": True,
            "development_entry_ids": ["same"], "check_entry_ids": ["same"]}
    with pytest.raises(ValueError, match="overlap"):
        campaign.rolling_choices({}, [fold])


def positive_groups():
    groups, folds = {}, []
    for scenario in campaign.SCENARIOS:
        for month in range(1, 10):
            folds.append({"fold_id": f"month_{month}", "scenario": scenario, "complete_calendar_check": True,
                          "development_entry_ids": [], "check_entry_ids": [f"{month}_{i}" for i in range(12)]})
        for key, net in (("stronger", "2.00"), ("weaker", "1.00")):
            for profile in campaign.PROFILE_NAMES:
                groups[(key, scenario, profile)] = [result(f"{month}_{i}", f"2026-{month:02}-02", net)
                                                    for month in range(1, 10) for i in range(12)]
    return groups, folds, [{"fingerprint": k, "family": k} for k in ("stronger", "weaker")]


def test_robust_paper_hypotheses_still_cannot_activate_or_select_live_policy():
    groups, folds, candidates = positive_groups()
    report = campaign.screen_report(groups, folds, candidates, period_end="2026-09-13")
    assert report["paper_finalists"] == ["stronger", "weaker"]
    assert report["paper_candidate"] is None
    assert report["finalist_three_engine_verification_required"]
    assert report["selection"] == {"selected_policy": None, "promotion_eligible": False}
    assert not report["live_activation_allowed"] and not report["account_currency_money_verified"]


def test_one_failed_clock_or_execution_profile_rejects_paper_nomination():
    groups, folds, candidates = positive_groups()
    for row in groups[("stronger", "publication_initial", "adverse_execution")]:
        if row["day"] >= "2026-08-01":
            row["pnl_eur"] = "-2.00"
    report = campaign.screen_report(groups, folds, candidates, period_end="2026-09-13")
    assert report["paper_finalists"] == ["weaker"]
    stronger = next(r for r in report["candidate_reports"] if r["fingerprint"] == "stronger")
    assert "publication_initial:adverse_execution:recent_window_not_positive" in stronger["reasons"]
