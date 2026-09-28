"""Four predeclared isolated controls on paired raw/export identities."""

from collections import Counter
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
import time

from research.causal_replay import CausalSignal, make_path, utc
from research.dubai_annual_coverage import coverage_metrics, msc
from research.dubai_annual_dataset import AnnualInputs
from research.dubai_clock_audit import _write, verify_audit
from research.dubai_entry_probe import SCENARIOS, _archive, compare_results
from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_shared_lab import _evaluate, execution_profiles
from research.execution_profile import execution_from_mapping
from research.strategy_study import ROOT, _check_current, _digest, _read, _sha, _verify_sources, _watch


SCHEMA = "dubai_paired_clock_controls_v1"
BUDGET = {"max_evaluations": 12_000, "max_engine_quote_visits": 150_000_000,
          "max_wall_seconds": 3600, "max_cases": 250, "max_path_quotes": 3_000_000}
HORIZON = 1200
EXTRA_SOURCES = ("research/dubai_paired_clocks.py", "tools/run_dubai_paired_clocks.py",
                 "research/dubai_clock_audit.py", "tools/audit_dubai_signal_clocks.py")
_IMPORTED = {name: _digest(ROOT / name) for name in EXTRA_SOURCES if (ROOT / name).is_file()}
STRATA = ("all_paired", "direct_message", "group_member", "direct_receipt_within_5s",
          "direct_receipt_5_to_60s", "direct_receipt_60_to_300s", "direct_receipt_over_300s")
LIMITATIONS = [
    "All source identities retained; paired monetary sums include only the same identities with both clocks evaluable.",
    "Four fixed own-rule controls at 0.01 lots, isolated position state for each trigger; not strategy discovery or an account.",
    "Unfilled entries count as zero, missing/blocked halves never do; incomplete pairs and original exclusions remain visible.",
    "Group-member contrasts also change grouping; only direct-message rows isolate the two retained clocks for one message.",
    "Raw receipts can be late retrievals; export timestamps can be edits. Neither clock is presumed to be the executable original for all cases.",
    "Historical Bid/Ask and causal prior EURUSD conversion with unchanged coverage gates; declared broker clock and zero commission/swap.",
    "Reference and adverse execution are hypotheses. Three-engine equality does not establish real fills or verified account money.",
    "No capital, margin, concurrent-account drawdown, untouched OOS, candidate selection or live activation claim.",
    "Opportunity extrema are hindsight diagnostics within 20 minutes, not fills or achievable profit.",
]


def fixed_controls():
    base = StrategyGenome(schema_version=2, entry_mode="signal_market", entry_expiry_min=3,
        leg_count=1, volume_weights=(.01,), target_mode="per_leg_steps", target_steps=(5.,),
        be_mode="none", stop_mode="fixed_move", stop_value=10., time_exit_min=15,
        time_exit_mode="always", provider_management_mode="ignore")
    controls = {"market": base,
        "delay90": base.with_change(entry_mode="delay", entry_value=90., stop_value=8., target_steps=(12.,), time_exit_min=5),
        "recovery2_1": base.with_change(entry_mode="adverse_reversal", entry_value=2., entry_confirmation_value=1.,
                                      stop_value=15., target_steps=(8.,)),
        "momentum5": base.with_change(entry_mode="momentum", entry_value=5., target_steps=(10.,))}
    if any(g.validation_errors() for g in controls.values()):
        raise ValueError("fixed control grammar changed")
    return {name: g.to_dict() for name, g in controls.items()}


def _pair_keys(pair):
    return "receipt:" + pair["raw_original"]["signal_id"], pair["export_trigger"]["trigger_id"]


def clock_cases(crosswalk):
    cases = {}
    for pair in crosswalk["pairs"]:
        if pair["comparison_status"] != "paired_clock_control":
            continue
        raw, exported = pair["raw_original"], pair["export_trigger"]
        raw_id, export_id = _pair_keys(pair)
        for row in ({"case_id": raw_id, "raw_signal_id": raw["signal_id"], "clock_kind": "raw_receipt",
                     "direction": raw["direction"], "trigger_utc": raw["received_utc"],
                     "published_utc": raw["published_utc"], "revision_id": raw["message_revision_id"]
                         if "message_revision_id" in raw else raw["signal_id"], "provider_events": []},
                    {"case_id": export_id, "raw_signal_id": raw["signal_id"], "clock_kind": pair["scenario"],
                     "direction": exported["direction"], "trigger_utc": exported["trigger_utc"],
                     "published_utc": exported["published_utc"], "revision_id": exported.get("trigger_event_id", export_id),
                     "provider_events": []}):
            if cases.setdefault(row["case_id"], row) != row:
                raise ValueError("paired clock identity maps to conflicting cases")
    return sorted(cases.values(), key=lambda r: (utc(r["trigger_utc"]), r["case_id"]))


def _strata(pair):
    result = {"all_paired", pair["match_kind"] if pair["match_kind"] == "group_member" else "direct_message"}
    if pair["match_kind"] == "direct_trigger":
        raw = pair["raw_original"]
        lag = (utc(raw["received_utc"]) - utc(raw["published_utc"])).total_seconds()
        result.add("direct_receipt_within_5s" if lag <= 5 else "direct_receipt_5_to_60s" if lag <= 60
                   else "direct_receipt_60_to_300s" if lag <= 300 else "direct_receipt_over_300s")
    return result


def paired_summary(crosswalk, cases, rows, *, controls, profiles):
    expected = {(c["case_id"], rule, profile) for c in cases for rule in controls for profile in profiles}
    index = {(r["case_id"], r["control"], r["profile"]): r for r in rows}
    if len(index) != len(rows) or set(index) != expected:
        raise ValueError("missing, duplicate or mixed paired evaluation matrix")
    comparisons = []
    for scenario in SCENARIOS:
        eligible = [p for p in crosswalk["pairs"] if p["scenario"] == scenario
                    and p["comparison_status"] == "paired_clock_control"]
        for stratum in STRATA:
            pairs = [p for p in eligible if stratum in _strata(p)]
            for rule in sorted(controls):
                for profile in sorted(profiles):
                    complete, missing, values = [], [], []
                    fills = [0, 0]
                    for pair in pairs:
                        keys = _pair_keys(pair)
                        halves = [index[(key, rule, profile)] for key in keys]
                        if any(r["status"] not in {"simulated", "unfilled"} for r in halves):
                            missing.append({"raw_signal_id": pair["raw_original"]["signal_id"],
                                "clock_statuses": {key: r["status"] for key, r in zip(keys, halves)}})
                            continue
                        pnl = []
                        for i, r in enumerate(halves):
                            value = r["engines"]["oracle"]["pnl_eur"]
                            if value is None or not Decimal(value).is_finite():
                                raise ValueError("evaluable row lacks finite money")
                            if r["status"] == "unfilled" and Decimal(value) != 0:
                                raise ValueError("unfilled row has nonzero money")
                            fills[i] += r["status"] == "simulated"
                            pnl.append(Decimal(value))
                        complete.append(pair["raw_original"]["signal_id"])
                        values.append(pnl)
                    totals = [sum((r[i] for r in values), Decimal("0.00")) for i in (0, 1)]
                    comparisons.append({"scenario": scenario, "stratum": stratum, "control": rule, "profile": profile,
                        "eligible_pairs": len(pairs), "complete_pairs": len(complete), "incomplete_pairs": len(missing),
                        "raw_filled": fills[0], "export_filled": fills[1],
                        "raw_pnl_eur": format(totals[0], ".2f") if values else None,
                        "export_pnl_eur": format(totals[1], ".2f") if values else None,
                        "raw_minus_export_eur": format(totals[0] - totals[1], ".2f") if values else None,
                        "raw_better_pairs": sum(a > b for a, b in values),
                        "export_better_pairs": sum(b > a for a, b in values),
                        "equal_pairs": sum(a == b for a, b in values),
                        "complete_raw_signal_ids": complete, "incomplete_details": missing})
    return {"schema_version": SCHEMA, "comparisons": comparisons, "evaluation_rows": len(rows),
        "status_counts": dict(Counter(r["status"] for r in rows)),
        "original_pair_statuses": {s: dict(Counter(r["comparison_status"] for r in crosswalk["pairs"]
            if r["scenario"] == s)) for s in SCENARIOS}, "selected_policy": None,
        "money_contract_verified": False, "account_currency_money_verified": False,
        "live_activation_allowed": False, "limitations": LIMITATIONS}


class RunBudget:
    def __init__(self):
        self.started, self.evaluations, self.quote_visits = time.monotonic(), 0, 0

    def check(self):
        if time.monotonic() - self.started >= BUDGET["max_wall_seconds"]:
            raise TimeoutError("paired controls time budget exhausted")

    def charge(self, quotes):
        self.check()
        if self.evaluations + 1 > BUDGET["max_evaluations"] or self.quote_visits + quotes > BUDGET["max_engine_quote_visits"]:
            raise ValueError("paired controls evaluation/quote budget exhausted")
        self.evaluations += 1
        self.quote_visits += quotes


def _current(implementation):
    _check_current(implementation)
    if any(_digest(ROOT / name) != value for name, value in _IMPORTED.items()):
        raise ValueError("loaded paired-control code changed")


def _load_path(case, inputs, base):
    stamp = msc(case["trigger_utc"])
    market, market_ids, market_missing = inputs.raw.window("XAUUSD", stamp, stamp + HORIZON * 1000)
    fx, fx_ids, fx_missing = inputs.raw.window("EURUSD", stamp, stamp + HORIZON * 1000)
    metrics = coverage_metrics(stamp, HORIZON, market, fx, inputs.protocol)
    metrics.update(raw_source_days=market_ids + fx_ids, missing_raw_source_days=market_missing + fx_missing)
    if market_missing or fx_missing:
        metrics["coverage_reasons"] = sorted(set(metrics["coverage_reasons"] + ["raw_source_day_missing"]))
        metrics["quote_coverage_pass"] = False
    if case["clock_kind"] != "raw_receipt":
        prior = inputs.coverage[(case["case_id"], HORIZON)]
        if any(prior[key] != value for key, value in metrics.items()):
            raise ValueError("paired export coverage differs from frozen annual source")
    if metrics["market_quotes_in_horizon"] > BUDGET["max_path_quotes"]:
        raise ValueError("paired path quote budget exhausted")
    reasons, path, opportunity = list(metrics["coverage_reasons"]), None, None
    if not reasons:
        signal = CausalSignal(case["case_id"], "canal1", case["direction"], utc(case["trigger_utc"]),
                              utc(case["published_utc"]), case["revision_id"])
        try:
            path = make_path(signal, base, market=market, conversion=fx,
                cutoff=signal.observed_at + timedelta(seconds=HORIZON), contract_size=inputs.reference["money"]["contract_size"],
                currency_digits=2, max_fx_age_ms=inputs.protocol["max_fx_age_ms"],
                max_fx_interval_ms=inputs.protocol["max_fx_interval_ms"],
                market_sha256=_sha({"raw": inputs.protocol["raw_audit_sha256"], "days": market_ids}),
                conversion_sha256=_sha({"raw": inputs.protocol["raw_audit_sha256"], "days": fx_ids}))
            entry = float(path.ask[0] if case["direction"] == "BUY" else path.bid[0])
            movement = path.bid - entry if case["direction"] == "BUY" else entry - path.ask
            opportunity = {"hypothetical_first_quote_entry": entry, "first_spread": float(path.ask[0] - path.bid[0]),
                "max_exit_side_move": float(movement.max()), "min_exit_side_move": float(movement.min()),
                "units": "XAUUSD_price_not_money", "hindsight_not_executable_profit": True}
        except ValueError as exc:
            reasons.append(str(exc))
    return path, {**case, "status": "data_blocked" if reasons else "data_ready", "reasons": reasons,
                  "coverage": metrics, "opportunity": opportunity}


def run_controls(audit_dir, coverage_dir, raw_audit_path, output_dir, *, progress=None):
    budget = RunBudget()
    audit_dir, output = Path(audit_dir).resolve(), Path(output_dir).resolve()
    if output.exists():
        raise ValueError("immutable output already exists")
    audit_proof = verify_audit(audit_dir)
    audit_protocol = _read(audit_dir / "protocol.json")
    inputs = AnnualInputs(audit_protocol["stream_dir"], coverage_dir, raw_audit_path)
    protected = [audit_dir, Path(audit_protocol["raw_inputs"]), *inputs.protected_dirs]
    if any(output.is_relative_to(p.resolve()) or p.resolve().is_relative_to(output) for p in protected):
        raise ValueError("paired output overlaps a protected archive")
    _archive(audit_dir, "audit_identity_sha256", inputs.watch)
    for name in EXTRA_SOURCES:
        inputs.watch(ROOT / name)
    if any(Path(p).is_relative_to(output) for p in inputs.watched):
        raise ValueError("paired output contains a protected file")
    _current(inputs.implementation)
    crosswalk = _read(audit_dir / "crosswalk.json")
    cases, controls = clock_cases(crosswalk), fixed_controls()
    profiles = execution_profiles(inputs.reference["execution"])
    for profile in profiles.values():
        profile["protection"]["request_quote_binding"] = "timestamp_and_ordinal"
    if inputs.reference["money"]["contract_size"] != 100:
        raise ValueError("fixed reference contract size changed")
    if len(cases) > BUDGET["max_cases"] or len(cases) * len(controls) * len(profiles) * 3 > BUDGET["max_evaluations"]:
        raise ValueError("full paired matrix exceeds budget")
    protocol = {"schema_version": SCHEMA, "budget": BUDGET, "horizon_seconds": HORIZON,
        "implementation": inputs.implementation, "audit_dir": str(audit_dir), "audit_proof": audit_proof,
        "cases": cases, "controls": controls, "execution_profiles": profiles, "strata": STRATA,
        "coverage_dir": str(Path(coverage_dir).resolve()), "raw_audit_path": str(Path(raw_audit_path).resolve()),
        "money": inputs.reference["money"], "coverage_protocol": inputs.protocol,
        "parameter_optimization": False, "data_use": "retrospective_paired_diagnostic_only", "limitations": LIMITATIONS}
    output.mkdir(parents=True, exist_ok=False)
    _write(output / "protocol.json", protocol)
    engines = {name: (execution_from_mapping(p), FastEvaluator(execution=execution_from_mapping(p))) for name, p in profiles.items()}
    base = StrategyGenome.from_dict(controls["market"])
    rows, inventory = [], []
    with (output / "results.jsonl").open("xb") as receipt_file:
        from research.strategy_study import _encode
        for i, case in enumerate(cases, 1):
            budget.check()
            path, coverage = _load_path(case, inputs, base)
            inventory.append(coverage)
            for name, strategy in controls.items():
                genome = StrategyGenome.from_dict(strategy)
                for profile, (execution, fast) in engines.items():
                    row = {"case_id": case["case_id"], "control": name, "profile": profile,
                        "strategy_fingerprint": genome.fingerprint, "status": "data_blocked",
                        "reasons": coverage["reasons"], "engines": {}, "mismatches": {}}
                    if path is not None:
                        result, scalar = _evaluate(path, genome, execution, fast, budget)
                        row.update(result)
                        if row["status"] in {"simulated", "unfilled"}:
                            opened = sum((Decimal(str(e.volume)) for e in scalar.entries), Decimal(0))
                            closed = sum((Decimal(str(e.volume)) for e in scalar.exits), Decimal(0))
                            if opened != closed or opened > Decimal("0.01"):
                                row.update(status="position_envelope_incident", reasons=["unclosed_or_excess_position"])
                    rows.append(row)
                    receipt_file.write(_encode(row))
                    if row["status"] not in {"simulated", "unfilled", "data_blocked"}:
                        receipt_file.flush()
                        _write(output / "incident.json", row)
                        raise ValueError("paired engine incident; archive preserved incomplete")
            receipt_file.flush()
            if progress and (i % 10 == 0 or i == len(cases)):
                progress({"completed_cases": i, "total_cases": len(cases), "evaluations": budget.evaluations,
                          "quote_visits": budget.quote_visits})
    summary = paired_summary(crosswalk, cases, rows, controls=controls, profiles=profiles)
    summary["usage"] = {"evaluations": budget.evaluations, "engine_quote_visits": budget.quote_visits,
                        "elapsed_seconds": time.monotonic() - budget.started}
    inputs.verify_sources()
    _current(inputs.implementation)
    budget.check()
    _write(output / "inventory.json", inventory)
    _write(output / "summary.json", summary)
    manifest = {"schema_version": SCHEMA, "status": "complete_fixed_paired_controls_only",
        "inputs": {"watched_files": inputs.watched},
        "artifacts": {name: {"sha256": _digest(output / name)} for name in
                      ("protocol.json", "inventory.json", "results.jsonl", "summary.json")}}
    manifest["paired_identity_sha256"] = _sha(manifest)
    _write(output / "manifest.json", manifest)
    return summary


def verify_controls(output_dir):
    from research.dubai_entry_probe import _rows
    output, watched = Path(output_dir).resolve(), {}
    artifacts = {"protocol.json", "inventory.json", "results.jsonl", "summary.json"}
    if {p.name for p in output.iterdir()} != artifacts | {"manifest.json"}:
        raise ValueError("incomplete or mixed paired archive")

    def watch(path, expected=None):
        _watch(path, _digest(path) if expected is None else expected, watched)

    manifest = _archive(output, "paired_identity_sha256", watch)
    if manifest["schema_version"] != SCHEMA or set(manifest["artifacts"]) != artifacts:
        raise ValueError("unsupported paired archive")
    protocol, summary = _read(output / "protocol.json"), _read(output / "summary.json")
    _current(protocol["implementation"])
    if (protocol["controls"] != fixed_controls() or protocol["budget"] != BUDGET
            or protocol["strata"] != list(STRATA) or protocol["limitations"] != LIMITATIONS):
        raise ValueError("paired frozen protocol differs")
    if verify_audit(protocol["audit_dir"]) != protocol["audit_proof"]:
        raise ValueError("paired audit identity differs")
    crosswalk = _read(Path(protocol["audit_dir"]) / "crosswalk.json")
    cases = clock_cases(crosswalk)
    if cases != protocol["cases"]:
        raise ValueError("paired clock denominator differs")
    rows = _rows(output / "results.jsonl")
    inventory = _read(output / "inventory.json")
    if len(inventory) != len(cases) or {r["case_id"] for r in inventory} != {c["case_id"] for c in cases}:
        raise ValueError("paired coverage denominator differs")
    quotes = {r["case_id"]: r["coverage"]["market_quotes_in_horizon"] for r in inventory}
    evaluations = visits = 0
    for row in rows:
        if row["status"] == "data_blocked":
            if row["engines"] or not row["reasons"]:
                raise ValueError("invalid data-blocked result")
            continue
        status, reasons, mismatches = compare_results(row["engines"])
        if (row["status"], row["reasons"], row["mismatches"]) != (status, reasons, mismatches):
            raise ValueError("engine parity verdict differs")
        if status not in {"simulated", "unfilled"}:
            raise ValueError("non-evaluable engine result in completed archive")
        evaluations += 3
        visits += 3 * quotes[row["case_id"]]
    expected = paired_summary(crosswalk, cases, rows, controls=protocol["controls"], profiles=protocol["execution_profiles"])
    if {k: v for k, v in summary.items() if k != "usage"} != expected:
        raise ValueError("paired aggregation differs")
    if (summary["usage"]["evaluations"] != evaluations or summary["usage"]["engine_quote_visits"] != visits
            or evaluations > BUDGET["max_evaluations"] or visits > BUDGET["max_engine_quote_visits"]):
        raise ValueError("paired budget usage differs")
    _verify_sources(watched)
    _current(protocol["implementation"])
    return {"status": "verified_fixed_paired_controls_only", "paired_identity_sha256": manifest["paired_identity_sha256"],
            "cases": len(cases), "evaluation_rows": len(rows), "engine_evaluations": evaluations}
