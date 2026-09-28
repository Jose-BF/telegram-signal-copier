from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path

import pytest

from research import dubai_shared_lab as lab
from research.dubai_annual_dataset import AnnualInputs
from research.dubai_iterative.contracts import StrategyGenome
from research.strategy_study import _digest, _encode, _sha
from tests.test_dubai_annual_dataset import annual_case
from tests.test_dubai_entry_probe import probe_case
from tests.test_dubai_family_catalog import reference
from tests.test_strategy_study import case


def row(key, stamp):
    return {"trigger_id": key, "trigger_utc": stamp, "scenario": "revision_time"}


def genome():
    return StrategyGenome.from_dict(reference()).with_change(volume_weights=(.02,))


def test_capacity_is_shared_between_canal1_signals_and_boundary_is_conservative():
    rows = [row("a", "2026-01-05T12:00:00Z"), row("b", "2026-01-05T12:01:00Z"),
            row("c", "2026-01-05T12:02:00Z"), row("equal", "2026-01-05T12:20:00Z"),
            row("after", "2026-01-05T12:20:00.001Z")]
    result = lab.reserve_schedule(rows, genome(), 1200)
    assert [r["admitted"] for r in result] == [True, True, False, False, True]
    assert result[2]["reason"] == "atomic_batch_exceeds_exposure"
    assert all(r["reserved_lots_after"] <= .04 for r in result)


def test_same_timestamp_batch_is_atomic_not_arbitrary_id_priority():
    rows = [row(key, "2026-01-05T12:00:00Z") for key in ("c", "b", "a")]
    result = lab.reserve_schedule(rows, genome(), 1200)
    assert not any(r["admitted"] for r in result)
    assert lab.reserve_schedule(rows[::-1], genome(), 1200) == result


def test_future_prices_outcomes_and_close_times_cannot_change_admission():
    rows = [row("a", "2026-01-05T12:00:00Z"), row("b", "2026-01-05T12:01:00Z")]
    before = lab.reserve_schedule(rows, genome(), 1200)
    rows[0].update(pnl_eur=-500, quote_coverage_pass=False, closed_at="2026-01-05T12:00:01Z")
    assert lab.reserve_schedule(rows, genome(), 1200) == before
    prefix = lab.reserve_schedule(rows[:1], genome(), 1200)
    assert before[:1] == prefix


@pytest.mark.parametrize("policy", [lab.ReservationPolicy(max_baskets=1),
                                  lab.ReservationPolicy(max_lots=.02),
                                  lab.ReservationPolicy(max_configured_loss_usd=20)])
def test_each_reservation_limit_is_enforced(policy):
    rows = [row("a", "2026-01-05T12:00:00Z"), row("b", "2026-01-05T12:01:00Z")]
    assert [r["admitted"] for r in lab.reserve_schedule(rows, genome(), 1200, policy)] == [True, False]


def test_unsafe_unbounded_or_mixed_inputs_are_rejected():
    with pytest.raises(ValueError):
        lab.reserve_schedule([row("a", "2026-01-05T12:00:00Z")], genome().with_change(stop_mode="none", stop_value=None), 1200)
    rows = [row("a", "2026-01-05T12:00:00Z"), row("a", "2026-01-05T12:01:00Z")]
    with pytest.raises(ValueError):
        lab.reserve_schedule(rows, genome(), 1200)
    rows[1]["trigger_id"] = "b"
    rows[1]["scenario"] = "publication_initial"
    with pytest.raises(ValueError):
        lab.reserve_schedule(rows, genome(), 1200)


@pytest.fixture
def lab_case(annual_case):
    def build(**kwargs):
        args = annual_case(**kwargs)
        coverage_file = args[1] / "protocol.json"
        coverage = json.loads(coverage_file.read_text())
        config_file = Path(coverage["threshold_reference"]["path"])
        config = json.loads(config_file.read_text())
        config["strategy"] = reference()
        config_file.write_bytes(_encode(config))
        coverage["threshold_reference"]["sha256"] = _digest(config_file)
        coverage_file.write_bytes(_encode(coverage))
        manifest_file = args[1] / "manifest.json"
        manifest = json.loads(manifest_file.read_text())
        manifest["artifacts"]["protocol.json"]["sha256"] = _digest(coverage_file)
        manifest["audit_identity_sha256"] = _sha({k: v for k, v in manifest.items() if k != "audit_identity_sha256"})
        manifest_file.write_bytes(_encode(manifest))
        stream_file = args[0] / "manifest.json"
        manifest = json.loads(stream_file.read_text())
        manifest["inputs"]["coverage_protocol_sha256"] = _digest(coverage_file)
        manifest["stream_identity_sha256"] = _sha({k: v for k, v in manifest.items() if k != "stream_identity_sha256"})
        stream_file.write_bytes(_encode(manifest))
        return args
    return build


def test_all_family_controls_raw_engines_shared_portfolio_and_archive(lab_case, monkeypatch):
    args = lab_case()
    original = lab.AnnualInputs.load_day

    def load(self, *values, **kwargs):
        assert (args[-1] / "protocol.json").exists()
        return original(self, *values, **kwargs)

    monkeypatch.setattr(lab.AnnualInputs, "load_day", load)
    result = lab.run_lab(*args)
    assert result["control_count"] == 16 and result["execution_profile_count"] == 2
    assert len(result["cells"]) == 64
    assert result["summary"]["cell_status_counts"] == {"diagnostic_complete": 56, "capability_blocked": 8}
    blocked = [c for c in result["cells"] if c["status"] == "capability_blocked"]
    assert {c["family"] for c in blocked} == {"break_even", "partial_runner"}
    assert all(not c["rows"][0]["engines"] for c in blocked)
    assert result["search_candidates"] == 0 and result["selection"]["selected_policy"] is None
    assert not result["ready_for_massive_search"] and not result["money_contract_verified"]
    assert result["annual_portfolio"] is None and result["sample_aggregate_profit"] is None
    assert all(c["portfolio"]["max_concurrent_volume"] <= .02 for c in result["cells"] if c["portfolio"])
    market = next(c for c in result["cells"] if c["family"] == "market" and c["execution_profile"] == "reference")
    assert market["rows"][0]["engines"]["scalar"]["filled_volume"] == .02
    assert lab.verify_lab(args[-1])["engine_evaluations"] == result["engine_evaluations"]
    with pytest.raises(ValueError, match="already exists"):
        lab.run_lab(*args)
    before = (args[-1] / "results.json").read_bytes()
    (args[-1] / "results.json").write_bytes(b"{}")
    with pytest.raises(ValueError, match="hash"):
        lab.verify_lab(args[-1])
    (args[-1] / "results.json").write_bytes(before)
    (args[0] / "triggers.jsonl").write_bytes(b"{}")
    with pytest.raises(ValueError, match="source"):
        lab.verify_lab(args[-1])


def test_incomplete_day_blocks_all_cases_not_just_the_bad_case(lab_case, monkeypatch):
    inputs = AnnualInputs(*lab_case(blocked=True)[:3])
    day = inputs.load_day("revision_time", "2026-01-05", 1200)
    control = lab.catalog(inputs.reference["strategy"])["fixed_controls"][0]
    monkeypatch.setattr(lab, "_evaluate", lambda *_: pytest.fail("incomplete day cannot execute"))
    budget = lab.RunBudget()
    result = lab.run_day(day, control, "reference", inputs.reference["execution"], budget)
    assert len(result["rows"]) == 2
    assert result["status"] == "data_blocked" and result["portfolio"] is None
    assert {r["status"] for r in result["rows"]} == {"day_data_blocked"}
    assert budget.evaluations == 0


def test_engine_disagreement_is_preserved_and_prevents_portfolio(lab_case, monkeypatch):
    inputs = AnnualInputs(*lab_case()[:3])
    day = inputs.load_day("revision_time", "2026-01-05", 1200)
    control = lab.catalog(inputs.reference["strategy"])["fixed_controls"][0]
    original = lab.oracle_simulate

    def divergent(*args, **kwargs):
        result = original(*args, **kwargs)
        return replace(result, pnl_eur=result.pnl_eur + Decimal(".01"))

    monkeypatch.setattr(lab, "oracle_simulate", divergent)
    result = lab.run_day(day, control, "reference", inputs.reference["execution"], lab.RunBudget())
    assert result["status"] == "engine_incident" and result["portfolio"] is None
    assert result["rows"][0]["mismatches"]["scalar"] == ["pnl_eur"]


def test_stress_profiles_are_explicit_and_do_not_mutate_reference(lab_case):
    inputs = AnnualInputs(*lab_case()[:3])
    before = deepcopy(inputs.reference["execution"])
    profiles = lab.execution_profiles(before)
    assert inputs.reference["execution"] == before == profiles["reference"]
    assert profiles["adverse_execution"]["entry_fill_latency_ms"] == 1000
    assert profiles["adverse_execution"]["entry_slippage"] == .1
    assert profiles["adverse_execution"]["exit_slippage"] == .1


def test_budget_rejection_is_before_any_source_decode_or_engine(lab_case, monkeypatch):
    args = lab_case()
    monkeypatch.setattr(lab, "BUDGET", dict(lab.BUDGET, max_evaluations=1))
    monkeypatch.setattr(lab.AnnualInputs, "load_day", lambda *_: pytest.fail("unexpected source decode"))
    with pytest.raises(ValueError, match="budget"):
        lab.run_lab(*args)
    assert not args[-1].exists()


def test_profile_capabilities_are_checked_without_loading_prices(lab_case, monkeypatch):
    inputs = AnnualInputs(*lab_case()[:3])
    monkeypatch.setattr(inputs.raw, "window", lambda *_: pytest.fail("preflight must not read price paths"))
    families = lab.profile_catalog(lab.catalog(inputs.reference["strategy"]), lab.execution_profiles(inputs.reference["execution"]))
    for family in ("break_even", "partial_runner"):
        assert all(r["blockers"] == ["protection_policy_unsupported"] for r in families["profile_admission"][family].values())
    assert all(not r["blockers"] for r in families["profile_admission"]["market"].values())


def test_extended_lab_is_explicit_and_all_sixteen_fixed_controls_integrate(lab_case):
    args = lab_case()
    result = lab.run_lab(*args, policy_extension="own_rule_be_partial_v1")
    assert result["summary"]["cell_status_counts"] == {"diagnostic_complete": 64}
    protocol = json.loads((args[-1] / "protocol.json").read_text())
    assert all(value["protection"]["policy_extension"] == "own_rule_be_partial_v1"
               for value in protocol["execution_profiles"].values())
    families = json.loads((args[-1] / "catalog.json").read_text())
    assert families["extensions"]["integrated_break_even_and_partial_exits"]["status"] == "fixed_control_capability_available_not_all_variants_certified"
    assert not result["ready_for_massive_search"] and result["selection"]["selected_policy"] is None
    assert lab.verify_lab(args[-1])["engine_evaluations"] == result["engine_evaluations"]


def test_partial_catalog_units_match_existing_monetary_engine_contract():
    units = lab.catalog(reference())["units"]
    assert units["runner_target"] == "EUR total basket equity for partial_runner"
    assert "partial_runner" in units["target_value"] and "EUR" in units["target_value"]
