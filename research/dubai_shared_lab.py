"""Frozen family controls with causal, bounded Canal 1 exposure reservations."""

from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass
from decimal import Decimal
from itertools import groupby
import json
import math
from pathlib import Path
import time

from research import strategy_study as fixed
from research.causal_replay import time_ns
from research.dubai_annual_dataset import AnnualInputs
from research.dubai_entry_probe import compare_results, select_days, SCENARIOS
from research.dubai_family_catalog import catalog
from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.engine import simulate
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.oracle import oracle_simulate
from research.dubai_iterative.portfolio import reconstruct_portfolio
from research.dubai_iterative.protection import profile_blockers
from research.dubai_iterative.market import market_blockers
from research.execution_profile import execution_from_mapping, execution_to_scenario


SCHEMA = "dubai_shared_family_lab_v1"
BUDGET = {"max_controls": 18, "max_selected_triggers": 64, "max_evaluations": 9000,
          "max_engine_quote_visits": 100_000_000, "max_wall_seconds": 1800}
ARTIFACTS = {"protocol.json", "catalog.json", "annual_inventory.json", "results.json", "manifest.json"}


@dataclass(frozen=True)
class ReservationPolicy:
    max_baskets: int = 2
    max_lots: float = .04
    max_configured_loss_usd: float = 40.

    def __post_init__(self):
        if type(self.max_baskets) is not int or not 1 <= self.max_baskets <= 10:
            raise ValueError("invalid basket reservation limit")
        for value in (self.max_lots, self.max_configured_loss_usd):
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError("invalid exposure reservation limit")


def reserve_schedule(rows, genome, horizon_seconds, policy=ReservationPolicy(), contract_size=100):
    if (genome.validation_errors() or genome.schema_version != 2 or genome.stop_mode != "fixed_move"
            or genome.provider_management_mode != "ignore" or genome.time_exit_mode != "always"
            or genome.entry_mode == "actual_mt5" or type(horizon_seconds) is not int
            or not 0 < horizon_seconds <= 14400 or contract_size != 100
            or 60 * (genome.entry_expiry_min + genome.time_exit_min) >= horizon_seconds):
        raise ValueError("unsupported bounded own-rule reservation contract")
    if (len({r["trigger_id"] for r in rows}) != len(rows)
            or len({r["scenario"] for r in rows}) > 1 or any(r["scenario"] not in SCENARIOS for r in rows)):
        raise ValueError("duplicate or mixed reservation identities")
    lots = sum((Decimal(str(v)) for v in genome.volume_weights), Decimal(0))
    loss = lots * Decimal(str(genome.stop_value)) * Decimal(str(contract_size))
    ordered = sorted(rows, key=lambda r: (time_ns(fixed._utc_explicit(r["trigger_utc"])), r["trigger_id"]))
    active, result = [], []
    for stamp, group in groupby(ordered, key=lambda r: time_ns(fixed._utc_explicit(r["trigger_utc"]))):
        group = list(group)
        # Equality is deliberately still occupied; no event order is invented
        # between a reservation expiry and a signal with the same timestamp.
        active = [expiry for expiry in active if expiry >= stamp]
        total = len(active) + len(group)
        admitted = (total <= policy.max_baskets and lots * total <= Decimal(str(policy.max_lots))
                    and loss * total <= Decimal(str(policy.max_configured_loss_usd)))
        if admitted:
            active.extend([stamp + horizon_seconds * 1_000_000_000] * len(group))
        for row in group:
            result.append({"trigger_id": row["trigger_id"], "trigger_ns": stamp, "admitted": admitted,
                "reason": "horizon_capacity_reserved" if admitted else "atomic_batch_exceeds_exposure",
                "reservation_until_ns": stamp + horizon_seconds * 1_000_000_000 if admitted else None,
                "batch_size": len(group), "reserved_baskets_after": len(active),
                "reserved_lots_after": float(lots * len(active)),
                "configured_loss_usd_reserved_after": str(loss * len(active)),
                "future_prices_outcomes_or_exits_used": False})
    return result


def execution_profiles(reference, *, policy_extension="none"):
    if policy_extension not in {"none", "own_rule_be_partial_v1"}:
        raise ValueError("unknown lab policy extension")
    base, stressed = deepcopy(reference), deepcopy(reference)
    stressed.update(entry_slippage=.1, exit_slippage=.1, spread_addition=.1, entry_fill_latency_ms=1000)
    stressed["market"].update(entry_acknowledgement_delay_ms=500, close_processing_delay_ms=500,
                              close_acknowledgement_delay_ms=500)
    stressed["protection"].update(processing_delay_ms=500, acknowledgement_delay_ms=500)
    for value in (base, stressed):
        if policy_extension != "none":
            value["protection"]["policy_extension"] = policy_extension
        if execution_from_mapping(value).protection.policy_extension != policy_extension:
            raise ValueError("reference policy extension differs from explicit lab selection")
    return {"reference": base, "adverse_execution": stressed}


def capability_blockers(genome, profile):
    if genome.entry_mode == "actual_mt5":
        return ["observed_entries_outside_own_rule_scope"]
    execution = execution_from_mapping(profile)
    return sorted(set(profile_blockers(None, genome, execution.protection) + market_blockers(None, genome, execution)))


def profile_catalog(families, profiles):
    result = deepcopy(families)
    result["profile_admission"] = {row["family"]: {name: {
        "blockers": capability_blockers(StrategyGenome.from_dict(row["strategy"]), profile),
        "status": "capability_blocked" if capability_blockers(StrategyGenome.from_dict(row["strategy"]), profile)
                  else "eligible_for_fixed_integration_control_not_certified"}
        for name, profile in profiles.items()} for row in result["fixed_controls"]}
    capable = all(not entry["blockers"] for family in ("break_even", "partial_runner")
                  for entry in result["profile_admission"][family].values())
    result["extensions"]["integrated_break_even_and_partial_exits"] = {
        "status": "fixed_control_capability_available_not_all_variants_certified" if capable else "blocked_execution_profile_contract",
        "requirement": "Explicit own-rule market extension: price BE, simultaneous partial/residual lot steps; other BE modes, partial ladders and client remain blocked. Verify controls in all three engines."}
    return result


class RunBudget:
    def __init__(self):
        self.started, self.evaluations, self.quote_visits = time.monotonic(), 0, 0

    def check(self):
        if time.monotonic() - self.started >= BUDGET["max_wall_seconds"]:
            raise TimeoutError("lab time budget exhausted; archive remains incomplete")

    def charge(self, quotes):
        self.check()
        if self.evaluations + 1 > BUDGET["max_evaluations"] or self.quote_visits + quotes > BUDGET["max_engine_quote_visits"]:
            raise ValueError("lab engine budget exceeded")
        self.evaluations += 1
        self.quote_visits += quotes


def _evaluate(path, genome, execution, fast, budget):
    results, scalar = {}, None
    for name, engine, profile in (("scalar", simulate, execution), ("fast", fast, execution),
                                  ("oracle", oracle_simulate, execution_to_scenario(execution))):
        budget.charge(len(path.times_ns))
        result = engine(path, genome) if name == "fast" else engine(path, genome, execution=profile)
        budget.check()
        results[name] = json.loads(fixed._encode(asdict(result)))
        if name == "scalar":
            scalar = result
    status, reasons, mismatches = compare_results(results)
    return {"status": status, "reasons": reasons, "mismatches": mismatches, "engines": results}, scalar


def run_day(day, control, profile_name, profile, budget, policy=ReservationPolicy()):
    genome = StrategyGenome.from_dict(control["strategy"])
    schedule = reserve_schedule(day.rows, genome, day.horizon_seconds, policy)
    decisions = {r["trigger_id"]: r for r in schedule}
    rows = [dict(r, reservation=decisions[r["trigger_id"]], engines={}, mismatches={}) for r in day.rows]
    report = {"day": day.day, "scenario": day.scenario, "horizon_seconds": day.horizon_seconds,
        "family": control["family"], "strategy_fingerprint": genome.fingerprint, "execution_profile": profile_name,
        "rows": rows, "portfolio": None, "money_contract_verified": False, "capital_adequacy_assessed": False,
        "reservation_policy": asdict(policy), "status": "pending", "blockers": []}
    unsupported = capability_blockers(genome, profile)
    if unsupported:
        for row in rows:
            row.update(status="capability_blocked", reasons=sorted(set(row["reasons"] + unsupported)))
        report.update(status="capability_blocked", blockers=unsupported)
        return report
    if not day.complete:
        reasons = sorted({reason for r in rows for reason in r["reasons"]})
        for row in rows:
            row.update(status="day_data_blocked", reasons=sorted(set(row["reasons"] + ["complete_day_coverage_required"])))
        report.update(status="data_blocked", blockers=reasons)
        return report
    execution = execution_from_mapping(profile)
    fast = FastEvaluator(execution=execution)
    by_id, accepted_paths, results = {p.signal_id: p for p in day.paths}, [], []
    for row in rows:
        if not row["reservation"]["admitted"]:
            row.update(status="exposure_not_admitted", reasons=[row["reservation"]["reason"]])
            continue
        path = by_id[row["trigger_id"]]
        evaluated, scalar = _evaluate(path, genome, execution, fast, budget)
        row.update(evaluated)
        if row["status"] in {"simulated", "unfilled"}:
            opened = sum((Decimal(str(e.volume)) for e in scalar.entries), Decimal(0))
            closed = sum((Decimal(str(e.volume)) for e in scalar.exits), Decimal(0))
            first = time_ns(path.signal_observed_at)
            deadline = first + day.horizon_seconds * 1_000_000_000
            if (opened != closed or opened > sum(Decimal(str(v)) for v in genome.volume_weights)
                    or any(time_ns(e.opened_at) < first for e in scalar.entries)
                    or any(time_ns(e.closed_at) > deadline for e in scalar.exits)):
                row.update(status="reservation_contract_incident", reasons=["position_outside_reserved_envelope"])
        accepted_paths.append(path)
        results.append(scalar)
    bad = [r for r in rows if r["status"] not in {"simulated", "unfilled", "exposure_not_admitted"}]
    if bad:
        report.update(status="engine_incident", blockers=sorted({reason for r in bad for reason in r["reasons"]}
            | {"engine_results_disagree" for r in bad if r["status"] == "engine_disagreement"}))
    elif results:
        assessment = reconstruct_portfolio(accepted_paths, results, execution=execution, portfolio_tape=day.portfolio_tape)
        report["portfolio"] = json.loads(fixed._encode(asdict(assessment)))
        reasons = list(assessment.blockers)
        if assessment.max_concurrent_volume > policy.max_lots or assessment.max_concurrent_signals > policy.max_baskets:
            reasons.append("actual_exposure_exceeds_reserved_limit")
        report.update(status="portfolio_incident" if reasons else "diagnostic_complete", blockers=reasons)
    else:
        report.update(status="diagnostic_complete_no_admissions")
    budget.check()
    return report


def _publish(output, payloads):
    output.mkdir(parents=True, exist_ok=True)
    for name, payload in payloads.items():
        if name not in ARTIFACTS:
            raise ValueError("unexpected lab artifact")
        data = fixed._encode(payload)
        target = output / name
        if target.exists():
            if target.is_symlink() or target.read_bytes() != data:
                raise ValueError("immutable lab artifact conflict")
        else:
            with target.open("xb") as stream:
                stream.write(data)


def run_lab(stream_dir, coverage_dir, raw_audit_path, output_dir, *, policy_extension="none"):
    budget = RunBudget()
    output = Path(output_dir).resolve()
    if output.exists():
        raise ValueError("immutable lab output already exists")
    inputs = AnnualInputs(stream_dir, coverage_dir, raw_audit_path)
    if any(output.is_relative_to(root.resolve()) or root.resolve().is_relative_to(output) for root in inputs.protected_dirs):
        raise ValueError("output overlaps protected annual inputs")
    if any(Path(path).is_relative_to(output) for path in inputs.watched):
        raise ValueError("output contains a read-only source")
    if inputs.reference["money"]["contract_size"] != 100:
        raise ValueError("reference USD reservation requires contract size 100")
    inventory = inputs.inventory()
    profiles = execution_profiles(inputs.reference["execution"], policy_extension=policy_extension)
    families = profile_catalog(catalog(inputs.reference["strategy"]), profiles)
    days, selected = select_days(inputs.triggers, start_utc=inputs.rolling["start"], end_exclusive_utc=inputs.rolling["end_exclusive"])
    if 1200 not in inputs.horizons or not selected:
        raise ValueError("missing 20-minute annual control cohort")
    controls = families["fixed_controls"]
    planned_evaluations = len(selected) * len(controls) * len(profiles) * 3
    planned_quotes = sum(inputs.coverage[(r["trigger_id"], 1200)]["market_quotes_in_horizon"] for r in selected)
    planned_quotes *= len(controls) * len(profiles) * 3
    if (len(controls) > BUDGET["max_controls"] or len(selected) > BUDGET["max_selected_triggers"]
            or planned_evaluations > BUDGET["max_evaluations"] or planned_quotes > BUDGET["max_engine_quote_visits"]):
        raise ValueError("declared full family control matrix exceeds budget; no truncation")
    protocol = {"schema_version": SCHEMA, "channel": "canal1", "other_channels_included": False,
        "catalog_sha256": fixed._sha(families), "annual_inventory_sha256": fixed._sha(inventory),
        "days": days, "selected_triggers": selected, "horizon_seconds": 1200,
        "execution_profiles": profiles, "money": inputs.reference["money"], "capital_eur": None,
        "policy_extension": policy_extension,
        "capital_status": "not_supplied_user_confirms_canal1_only", "reservation_policy": asdict(ReservationPolicy()),
        "reservation_release": "fixed_horizon_even_if_position_closes_earlier_strictly_after_expiry",
        "same_timestamp_policy": "atomic_batch_all_or_none", "budget": BUDGET,
        "planned_evaluations_upper_bound": planned_evaluations, "planned_engine_quote_visits_upper_bound": planned_quotes,
        "sources": dict(inputs.watched), "implementation": inputs.implementation,
        "data_use": "retrospective_integration_only", "parameter_optimization": False,
        "limits": ["Only Canal 1; each message-clock scenario is a separate account sleeve.",
            "Reservations are configured reference risk, not margin, capital, maximum possible loss or actual open exposure.",
            "Whole days with any blocked input remain blocked for every control; no annual total from the passing subset.",
            "Original receipt and initial edited content are not reconstructed from known revisions.",
            "Base costs optimistic; adverse slippage/latency hypotheses are not measured broker costs.",
            "Commission, swap, leverage, margin, stop-out and capital-dependent policies are not certified.",
            "Independent basket management; no cross-basket netting, dynamic close-release or daily loss-stop.",
            "Coverage of fixed controls is not coverage of all parameters/interactions or permission for massive search."]}
    protocol["identity_sha256"] = fixed._sha(protocol)
    inputs.verify_sources()
    budget.check()
    _publish(output, {"catalog.json": families, "annual_inventory.json": inventory, "protocol.json": protocol})
    print(json.dumps({"event": "lab_frozen_before_prices_and_engines", "controls": len(controls), "days": days,
                      "triggers": len(selected), "max_evaluations": planned_evaluations}), flush=True)
    reports = []
    for day_name in days:
        for scenario in SCENARIOS:
            if not any(r["scenario"] == scenario and r["trigger_utc"].startswith(day_name) for r in selected):
                continue
            budget.check()
            day = inputs.load_day(scenario, day_name, 1200)
            for control in controls:
                for profile_name, profile in profiles.items():
                    reports.append(run_day(day, control, profile_name, profile, budget))
            print(json.dumps({"event": "day_family_matrix_checked", "day": day_name, "scenario": scenario,
                              "complete_data": day.complete, "engine_evaluations": budget.evaluations}), flush=True)
    summary = {"cell_status_counts": dict(Counter(r["status"] for r in reports)),
        "signal_status_counts": dict(Counter(row["status"] for r in reports for row in r["rows"])),
        "families": {control["family"]: dict(Counter(r["status"] for r in reports if r["family"] == control["family"])) for control in controls},
        "scenarios": {scenario: {"selected_triggers": sum(r["scenario"] == scenario for r in selected),
            "cell_status_counts": dict(Counter(r["status"] for r in reports if r["scenario"] == scenario))} for scenario in SCENARIOS}}
    report = {"schema_version": SCHEMA, "status": "hypothetical_family_integration_only",
        "protocol_sha256": fixed._sha(protocol), "summary": summary, "cells": reports,
        "engine_evaluations": budget.evaluations, "engine_quote_visits": budget.quote_visits,
        "control_count": len(controls), "execution_profile_count": len(profiles), "search_candidates": 0,
        "annual_portfolio": None, "sample_aggregate_profit": None, "money_contract_verified": False,
        "account_currency_money_verified": False, "full_live_parity_verified": False,
        "ready_for_massive_search": False, "automatic_admission": False,
        "selection": {"selected_policy": None, "promotion_eligible": False}}
    manifest = {"schema_version": SCHEMA, "protocol_identity_sha256": protocol["identity_sha256"],
        "artifacts": {"protocol.json": fixed._sha(protocol), "catalog.json": fixed._sha(families),
                      "annual_inventory.json": fixed._sha(inventory), "results.json": fixed._sha(report)}}
    inputs.verify_sources()
    budget.check()
    _publish(output, {"results.json": report, "manifest.json": manifest})
    return report


def verify_lab(output_dir):
    output = Path(output_dir).resolve()
    if not output.is_dir() or {p.name for p in output.iterdir()} != ARTIFACTS:
        raise ValueError("incomplete family lab archive")
    manifest = fixed._read(output / "manifest.json")
    if manifest["schema_version"] != SCHEMA or set(manifest["artifacts"]) != ARTIFACTS - {"manifest.json"}:
        raise ValueError("unexpected family lab manifest")
    for name, sha in manifest["artifacts"].items():
        if (output / name).is_symlink() or fixed._digest(output / name) != sha:
            raise ValueError("family lab artifact hash mismatch")
    protocol, report = fixed._read(output / "protocol.json"), fixed._read(output / "results.json")
    identity = protocol["identity_sha256"]
    if (identity != fixed._sha({k: v for k, v in protocol.items() if k != "identity_sha256"})
            or manifest["protocol_identity_sha256"] != identity or report["protocol_sha256"] != fixed._sha(protocol)
            or protocol["catalog_sha256"] != manifest["artifacts"]["catalog.json"]
            or protocol["annual_inventory_sha256"] != manifest["artifacts"]["annual_inventory.json"]):
        raise ValueError("mixed family lab identities")
    fixed._check_current(protocol["implementation"])
    fixed._verify_sources(protocol["sources"])
    expected = {(day, scenario, control["family"], profile)
        for day in protocol["days"] for scenario in SCENARIOS
        if any(r["scenario"] == scenario and r["trigger_utc"].startswith(day) for r in protocol["selected_triggers"])
        for control in fixed._read(output / "catalog.json")["fixed_controls"] for profile in protocol["execution_profiles"]}
    actual = [(r["day"], r["scenario"], r["family"], r["execution_profile"]) for r in report["cells"]]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("incomplete family/day/profile matrix")
    for cell in report["cells"]:
        wanted = {r["trigger_id"] for r in protocol["selected_triggers"] if r["scenario"] == cell["scenario"]
                  and r["trigger_utc"].startswith(cell["day"])}
        if {r["trigger_id"] for r in cell["rows"]} != wanted or len(cell["rows"]) != len(wanted):
            raise ValueError("incomplete family cell trigger denominator")
    return {"status": "verified_current_hypothetical_family_lab", "identity_sha256": identity,
            "summary": report["summary"], "engine_evaluations": report["engine_evaluations"]}
