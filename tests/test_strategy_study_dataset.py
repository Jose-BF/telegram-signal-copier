"""Admission/search bridge tests: input facts, not selected outcomes."""

from decimal import Decimal
import sys

import pandas as pd
import pytest

from research import strategy_study as fixed
from research import strategy_study_dataset as bridge
from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.engine import simulate
from research.execution_profile import execution_from_mapping
from tests.test_strategy_study import BASE, case, control_case, digest, write_json


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_export_bridge_has_same_path_money_as_fixed_runner_without_observed_fills(case, direction):
    config, output, payload = case(direction)
    bundle = bridge.load_study_dataset(config)
    bundle.verify_sources()
    assert bundle.dataset.coverage_complete
    assert len(bundle.dataset.eligible_signal_ids) == len(bundle.dataset.paths) == 1
    assert bundle.dataset.actual_evidence_signal_ids == ()
    assert bundle.dataset.paths[0].actual_pnl_eur is None
    assert bundle.dataset.max_hold_minutes == 2
    assert bundle.dataset.paths[0].provider_events == ()
    assert bundle.config == fixed.load_config(config)
    result = simulate(bundle.dataset.paths[0], StrategyGenome.from_dict(bundle.config["strategy"]),
                      execution=execution_from_mapping(bundle.config["execution"]))
    assert result.pnl_eur == Decimal("1.00")
    assert not output.exists(), "Loading input must not execute the fixed diagnostic or search"
    assert bundle.identity["config_sha256"] == digest(config)
    assert bundle.identity["money_contract_verified"] is False
    assert bundle.identity["account_currency_money_verified"] is False
    assert bundle.identity["selection_allowed"] is False
    assert bundle.max_fx_age_ms == payload["max_fx_age_ms"]
    frame, _, blockers = bundle.market_tick_source.load_day(BASE.date())
    assert len(frame) == 121 and not blockers


def test_loader_does_not_execute_engines_or_consume_observed_results(case, monkeypatch):
    config, _, _ = case()
    def forbidden(*args, **kwargs):
        raise AssertionError("No engine should be executed by an input loader")
    monkeypatch.setattr(fixed, "simulate", forbidden)
    monkeypatch.setattr(fixed, "FastEvaluator", forbidden)
    monkeypatch.setattr(fixed, "oracle_simulate", forbidden)
    assert bridge.load_study_dataset(config).dataset.coverage_complete


@pytest.mark.parametrize("hole", ["market", "conversion"])
def test_missing_input_is_retained_in_eligible_denominator_and_inventory(case, hole):
    config, _, payload = case(extra=[
        {"id": 2, "type": "message", "date_unixtime": str(int(BASE.timestamp())), "text": "Market commentary"},
    ])
    proof = payload["sources"][hole][0]
    pd.read_parquet(proof["path"]).drop(index=range(20, 100)).to_parquet(proof["path"])
    proof["sha256"] = digest(proof["path"])
    write_json(config, payload)
    bundle = bridge.load_study_dataset(config)
    assert not bundle.dataset.paths
    assert len(bundle.dataset.eligible_signal_ids) == 1
    assert not bundle.dataset.coverage_complete
    assert set(next(iter(bundle.dataset.exclusions.values()))) == set(bundle.dataset.eligible_signal_ids)
    assert bundle.inventory["denominator"]["all_archive_identities"] == 2
    assert bundle.inventory["denominator"]["status_counts"] == {"admission_blocked": 1, "data_blocked": 1}
    assert bundle.inventory["search_candidates"] == 0
    assert len(bundle.inventory["rows"]) == 2


def test_budget_excess_preserves_whole_cohort_without_loading_first_n(case):
    config, _, payload = case(extra=[
        {"id": 2, "type": "message", "date_unixtime": str(int(BASE.timestamp())), "text": "BUY GOLD NOW"},
    ])
    payload["budget"]["max_signals"] = 1
    write_json(config, payload)
    bundle = bridge.load_study_dataset(config)
    assert not bundle.dataset.paths
    assert len(bundle.dataset.eligible_signal_ids) == 2
    assert bundle.inventory["denominator"]["status_counts"] == {"budget_blocked": 2}
    assert not bundle.dataset.coverage_complete


def test_control_input_preserves_channels_and_discards_provider_management(control_case):
    config, *_ = control_case()
    bundle = bridge.load_study_dataset(config)
    assert len(bundle.dataset.paths) == 1
    assert bundle.dataset.paths[0].provider_events == ()
    assert bundle.dataset.paths[0].actual_pnl_eur is None
    assert bundle.inventory["input_kind"] == "causal_control"
    assert len(bundle.inventory["rows"]) > len(bundle.dataset.paths)
    assert all(path.signal_id.startswith("causal_control:") for path in bundle.dataset.paths)


@pytest.mark.parametrize("module", ["MetaTrader5", "listener", "executor", "telethon", "classifier"])
def test_data_bridge_allows_offline_search_imports_but_never_live_modules(case, monkeypatch, module):
    config, _, _ = case()
    monkeypatch.setitem(sys.modules, "research.dubai_iterative.search", object())
    monkeypatch.setitem(sys.modules, "research.gold_iterative.search", object())
    bundle = bridge.load_study_dataset(config)
    bundle.verify_sources()
    monkeypatch.setitem(sys.modules, module, object())
    with pytest.raises(ValueError, match="offline|live"):
        bundle.verify_sources()


def test_source_change_after_loading_invalidates_search_bundle(case):
    config, _, payload = case()
    bundle = bridge.load_study_dataset(config)
    payload["horizon_seconds"] = 119
    write_json(config, payload)
    with pytest.raises(ValueError, match="source|changed|hash"):
        bundle.verify_sources()


@pytest.mark.parametrize("part", ["config", "inventory", "identity"])
def test_in_memory_contract_mutation_is_not_an_unbound_search_input(case, part):
    config, _, _ = case()
    bundle = bridge.load_study_dataset(config)
    getattr(bundle, part)["unbound_field"] = True
    with pytest.raises(ValueError, match="changed|contract|identity"):
        bundle.verify_sources()


def test_no_automatic_admission_or_missing_observed_profit_is_invented(case):
    config, _, _ = case()
    bundle = bridge.load_study_dataset(config)
    report = bundle.inventory
    assert report["status"] == "diagnostic_input_only"
    assert report["money_contract_verified"] is False
    assert report["account_currency_money_verified"] is False
    assert report["observed_accounting_available"] is False
    assert report["automatic_admission"] is False
    assert all(not row["engines"] for row in report["rows"])


@pytest.mark.parametrize("role", ["market", "conversion"])
def test_canonical_frame_evidence_and_loaded_day_are_detached_copies(case, role):
    config, _, _ = case()
    bundle = bridge.load_study_dataset(config)
    source = getattr(bundle, f"{role}_tick_source")
    original, evidence, _ = source.load_day(BASE.date())
    exposed = source.frame
    exposed.loc[1, ["bid", "ask"]] = [90., 90.2]
    exposed_evidence = source.evidence
    exposed_evidence[0]["sha256"] = "0" * 64
    loaded, loaded_evidence, _ = source.load_day(BASE.date())
    loaded.loc[1, ["bid", "ask"]] = [80., 80.2]
    loaded_evidence[0]["sha256"] = "1" * 64
    bundle.verify_sources()
    unchanged, unchanged_evidence, _ = source.load_day(BASE.date())
    pd.testing.assert_frame_equal(original, unchanged)
    assert unchanged_evidence == evidence


@pytest.mark.parametrize("role", ["market", "conversion"])
@pytest.mark.parametrize("part", ["frame", "evidence"])
def test_internal_canonical_mutation_fails_before_bundle_or_tape_use(case, role, part):
    config, _, _ = case()
    bundle = bridge.load_study_dataset(config)
    source = getattr(bundle, f"{role}_tick_source")
    if part == "frame":
        source._frame.loc[1, ["bid", "ask"]] = [90., 90.2]
    else:
        source._evidence[0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="canonical.*changed"):
        bundle.verify_sources()
    with pytest.raises(ValueError, match="canonical.*changed"):
        source.load_day(BASE.date())
