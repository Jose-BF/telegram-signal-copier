"""Finite economic comparison of causally available Canal 1 price levels."""

from collections import Counter, defaultdict
from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal, ROUND_FLOOR
import json
from pathlib import Path
import time

import numpy as np

from research.causal_replay import make_path, utc
from research.dubai_annual_coverage import coverage_metrics, msc
from research.dubai_clock_audit import _write
from research.dubai_entry_probe import compare_results
from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.engine import simulate
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.oracle import oracle_simulate
from research.dubai_receipt_controls import receipt_trigger
from research.dubai_signal_anatomy import BoundedSource, verify_anatomy
from research.execution_profile import execution_from_mapping, execution_to_scenario
from research.strategy_study import ROOT, _check_current, _digest, _encode, _read, _sha, _verify_sources, _watch, current_identity, load_config


SCHEMA = "dubai_simple_range_study_v1"
RISK_USD = Decimal("200")
EXTRA_COST_EUR_PER_LOT = Decimal("10")
BUDGET = {"max_policies": 24, "max_signals": 300, "max_evaluations": 20_000,
          "max_source_quotes": 150_000_000, "max_engine_quote_visits": 12_000_000_000, "max_wall_seconds": 14_400}
SOURCES = ("research/dubai_range_study.py", "tools/run_dubai_range_study.py", "research/dubai_signal_anatomy.py",
           "research/dubai_receipt_controls.py", "research/dubai_annual_coverage.py", "research/causal_replay.py")
LIMITATIONS = [
    "Retrospective discovery on reused receipts; no fresh OOS, production parity or automatic promotion.",
    "200 USD nominal loss budget per isolated signal is a research scale, not user capital or a guaranteed loss cap.",
    "Lot sizes rounded down to 0.01; unattainable allocations remain risk-blocked, never rounded up.",
    "Initial receipt and first priced message retain evidence tiers and inferred associations; no synthetic canonical revision.",
    "First priced levels are frozen; later edits and management ignored. No hindsight typo correction.",
    "Absolute SL and TP sent with each order; real quote/slippage and rejection at processing remain modeled hypotheses.",
    "No break-even move in this first comparison; split targets do not imply risk-free runners.",
    "All no-fills and blocked rows remain visible. Missing data and blocked execution are not zero P/L.",
    "Historical spread/FX and declared broker clock; additional 10 EUR per filled lot round-trip.",
    "Intraday-only money coverage, no inferred overnight swaps, margin, stop-out or account capital adequacy.",
    "Sums and closed-day drawdown are not concurrent account equity; overlapping isolated signals may share market moves.",
    "Fast full matrix plus predeclared first-per-month/direction/type scalar and independent-oracle checks.",
]


def policies():
    return [{"id": f"{kind}:{entry}:{exit_mode}:{minutes}", "kind": kind, "entry": entry,
             "exit": exit_mode, "hold_minutes": minutes}
        for kind, entries in (("range", ("on_levels", "zone", "midpoint", "ladder")),
                              ("single_price", ("on_levels", "limit")))
        for entry in entries for exit_mode in ("tp1", "split") for minutes in (60, 240)]


def build_genome(row, rule, initial_quote, contract_size=100):
    msg = row["first_priced"]
    p, sign = msg["parsed"], 1 if row["direction"] == "BUY" else -1
    lo, hi = p["range"] if p.get("range") else (p["entry_price"], p["entry_price"])
    near, far, mid = (hi if sign == 1 else lo), (lo if sign == 1 else hi), (lo + hi) / 2
    stop, targets = p["sl"], p["tps"]
    if rule["exit"] == "split" and (len(targets) < 2 or sign * (targets[1] - targets[0]) <= 0):
        raise ValueError("second_target_unavailable_or_inconsistent")
    ladder = rule["entry"] == "ladder"
    if ladder:
        references, fractions = [near, mid, far], [Decimal(".60"), Decimal(".25"), Decimal(".15")]
        take_profits = [targets[0], targets[0], targets[1] if rule["exit"] == "split" else targets[0]]
    else:
        reference = initial_quote if rule["entry"] == "on_levels" else mid if rule["entry"] == "midpoint" else near
        references = [reference] * (2 if rule["exit"] == "split" else 1)
        fractions = [Decimal(".75"), Decimal(".25")] if len(references) == 2 else [Decimal(1)]
        take_profits = targets[:2] if len(references) == 2 else targets[:1]
    volumes, nominal = [], Decimal(0)
    for price, fraction in zip(references, fractions, strict=True):
        distance = Decimal(sign) * (Decimal(str(price)) - Decimal(str(stop)))
        if distance <= 0:
            raise ValueError("initial_price_beyond_stop")
        units = (RISK_USD * fraction / (distance * Decimal(str(contract_size))) / Decimal(".01")).to_integral_value(rounding=ROUND_FLOOR)
        volume = min(units * Decimal(".01"), Decimal("1.00"))
        if volume < Decimal(".01"):
            raise ValueError("risk_allocation_below_minimum_lot")
        volumes.append(float(volume))
        nominal += distance * Decimal(str(contract_size)) * volume
    entry_mode = "signal_market" if rule["entry"] == "on_levels" else "published_range" if rule["entry"] in {"zone", "ladder"} else "published_limit"
    genome = StrategyGenome(schema_version=2, entry_mode=entry_mode,
        entry_value=None if entry_mode == "signal_market" else lo if entry_mode == "published_range" else mid if rule["entry"] == "midpoint" else near,
        entry_confirmation_value=hi if entry_mode == "published_range" else None,
        entry_expiry_min=5, entry_ladder_mode="range_levels" if ladder else "simultaneous",
        leg_count=len(volumes), volume_weights=tuple(volumes), target_mode="per_leg_levels", target_steps=tuple(take_profits),
        stop_mode="fixed_level", stop_value=stop, be_mode="none", provider_management_mode="ignore",
        pending_entry_policy="none", time_exit_mode="always", time_exit_min=rule["hold_minutes"])
    if genome.validation_errors() or nominal > RISK_USD:
        raise ValueError("invalid range strategy or risk envelope")
    return genome, float(nominal)


def sample_ids(rows):
    groups = {}
    for row in rows:
        if row["initial_receipt_supported"] and row["first_priced"]:
            key = (row["received_utc"][:7], row["direction"], row["first_priced"]["kind"])
            groups.setdefault(key, row["signal_id"])
    return sorted(groups.values())


def stats(rows):
    known = [r for r in rows if r["status"] in {"simulated", "unfilled"}]
    values = [Decimal(r["net_after_cost_eur"]) for r in known]
    daily = defaultdict(Decimal)
    for row, value in zip(known, values, strict=True):
        daily[row["received_utc"][:10]] += value
    balance = high = dd = Decimal(0)
    for day, value in sorted(daily.items()):
        balance += value
        high = max(high, balance)
        dd = max(dd, high - balance)
    gains, losses = sum((v for v in values if v > 0), Decimal(0)), -sum((v for v in values if v < 0), Decimal(0))
    filled = [r for r in known if r["status"] == "simulated"]
    floating = [Decimal(r["result"]["max_floating_drawdown_eur"]) for r in filled
                if r["result"]["max_floating_drawdown_eur"] is not None]
    floating_max = str(max(floating)) if floating else "0" if known and not filled else None
    return {"rows": len(rows), "status_counts": dict(Counter(r["status"] for r in rows)), "known": len(known),
        "filled_signals": sum(r["status"] == "simulated" for r in known),
        "net_after_cost_eur": str(sum(values, Decimal(0))) if values else None,
        "worst_signal_eur": str(min(values)) if values else None,
        "closed_day_drawdown_eur": str(dd) if values else None,
        "max_single_signal_floating_drawdown_eur": floating_max,
        "filled_signals_missing_floating_drawdown": len(filled) - len(floating),
        "profit_factor": float(gains / losses) if losses else None,
        "positive_signals": sum(v > 0 for v in values), "negative_signals": sum(v < 0 for v in values),
        "mean_nominal_risk_usd": float(np.mean([r["nominal_risk_usd"] for r in known])) if known else None,
        "monthly": {month: str(sum((v for day, v in daily.items() if day.startswith(month)), Decimal(0))) for month in sorted({d[:7] for d in daily})},
        "daily": {d: str(v) for d,v in sorted(daily.items())}}


def summarize(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["policy_id"], row["profile"])].append(row)
    groups = {f"{rule}|{profile}": stats(values) for (rule, profile), values in sorted(grouped.items())}
    # Matched comparisons also remove strategy-specific minimum-lot differences.
    shared = {}
    for kind in ("range", "single_price"):
        sets = [{r["signal_id"] for r in values if r["status"] in {"simulated", "unfilled"}}
                for (rule, _), values in grouped.items() if rule.startswith(kind + ":")]
        ids = set.intersection(*sets) if sets else set()
        shared[kind] = {"signal_ids": sorted(ids), "groups": {f"{rule}|{profile}": stats([r for r in values if r["signal_id"] in ids])
            for (rule, profile), values in sorted(grouped.items()) if rule.startswith(kind + ":")}}
    return {"schema_version": SCHEMA, "groups": groups, "matched_all_policies_and_horizons": shared,
            "selected_policy": None, "money_contract_verified": False, "account_currency_money_verified": False,
            "orders_sent": 0, "limitations": LIMITATIONS}


def retained_parity(record, prior):
    fields = ("signal_id", "received_utc", "priced_received_utc", "direction", "evidence_tier",
        "priced_source_sha256", "policy_id", "profile", "coverage", "genome", "strategy_fingerprint",
        "nominal_risk_usd", "status", "reasons", "result")
    changed = [k for k in fields if _encode(record[k]) != _encode(prior[k])]
    if changed or not prior["parity"]:
        raise ValueError("retained parity inputs or newly computed fast result changed: " + ",".join(changed))
    pair = prior["parity"]["engines"]
    if pair["fast"] != record["result"] or compare_results(pair)[0] == "engine_disagreement":
        raise ValueError("retained parity mismatch")
    return pair


def run_study(anatomy_dir, old_controls_dir, coverage_path, raw_audit, output_dir, progress=lambda value: None, *, parity_from=None):
    started, watched = time.monotonic(), {}
    anatomy, old_controls, coverage_path, raw_audit, output = map(lambda p: Path(p).resolve(),
        (anatomy_dir, old_controls_dir, coverage_path, raw_audit, output_dir))
    if output.exists():
        raise ValueError("immutable range study already exists")

    def check():
        if time.monotonic() - started > BUDGET["max_wall_seconds"]:
            raise TimeoutError("range study wall budget exhausted")

    def watch(path, expected=None):
        check()
        path = Path(path).resolve()
        if path.is_relative_to(output) or output.is_relative_to(path):
            raise ValueError("output overlaps protected input")
        _watch(path, expected or _digest(path), watched)

    implementation = current_identity()
    _check_current(implementation)
    for name in SOURCES:
        watch(ROOT / name)
    proof = verify_anatomy(anatomy)
    for name in ("manifest.json", "signals.json", "messages.json"):
        watch(anatomy / name)
    rows = _read(anatomy / "signals.json")
    if not 0 < len(rows) <= BUDGET["max_signals"]:
        raise ValueError("signal budget exceeded")
    old_manifest = _read(old_controls / "manifest.json")
    watch(old_controls / "manifest.json")
    watch(old_controls / "protocol.json", old_manifest["artifacts"]["protocol.json"]["sha256"])
    profiles = _read(old_controls / "protocol.json")["execution_profiles"]
    for value in profiles.values():
        value["protection"]["policy_extension"] = "absolute_levels_v1"
    executions = {name: execution_from_mapping(p) for name,p in profiles.items()}
    fast = {name: FastEvaluator(execution=e) for name,e in executions.items()}
    watch(coverage_path)
    coverage = _read(coverage_path)
    watch(raw_audit, coverage["raw_audit_sha256"])
    ref = coverage["threshold_reference"]
    watch(ref["path"], ref["sha256"])
    reference = load_config(ref["path"])
    if any(coverage[k] != reference[k] for k in ("max_market_gap_ms", "max_fx_age_ms", "max_fx_interval_ms")):
        raise ValueError("coverage thresholds changed")
    source = BoundedSource(raw_audit, coverage["broker_clock"]["segments"], watch)
    rules, sampled = policies(), sample_ids(rows)
    protocol = {"schema_version": SCHEMA, "implementation": implementation, "budget": BUDGET,
        "anatomy_proof": proof, "policies": rules, "parity_signal_ids": sampled,
        "risk_budget_usd": str(RISK_USD), "execution_profiles": profiles,
        "money": reference["money"], "coverage": coverage, "extra_cost_eur_per_lot": str(EXTRA_COST_EUR_PER_LOT),
        "price_levels": "first priced message frozen; no subsequent edits or discretionary management",
        "entry_expiry": "five minutes from priced-message receipt; cancel unrequested legs at first TP1/SL observation",
        "exit_split": "75/25 nominal risk for simultaneous legs at TP1/TP2; ladder 60/25/15 with last leg to TP2",
        "path_seconds": "holding minutes * 60 + 300 entry wait + 10 settlement seconds, newly checked per message",
        "orders": "market requests at qualifying executable quotes, not resting broker limit orders",
        "retained_identities": len(rows), "limitations": LIMITATIONS}
    prior_rows, reused = {}, 0
    if parity_from is not None:
        previous = Path(parity_from).resolve()
        for name in ("protocol.json", "results.jsonl", "runner_at_failure.py"):
            watch(previous / name)
        old_protocol = _read(previous / "protocol.json")
        for key in protocol:
            if key != "limitations" and protocol[key] != old_protocol.get(key):
                raise ValueError("retained parity protocol changed: " + key)
        previous_rows = [json.loads(line) for line in (previous / "results.jsonl").read_text(encoding="utf-8").splitlines()]
        prior_rows = {(r["signal_id"], r["policy_id"], r["profile"]): r for r in previous_rows}
        expected = {(r["signal_id"], p["id"], name) for r in rows for p in rules for name in profiles}
        if set(prior_rows) != expected or len(prior_rows) != len(previous_rows):
            raise ValueError("retained parity matrix incomplete")
        protocol["retained_parity"] = {"archive": str(previous), "results_sha256": _digest(previous / "results.jsonl"),
            "protocol_sha256": _digest(previous / "protocol.json"), "original_runner_sha256": _digest(previous / "runner_at_failure.py"),
            "scope": "summary-only recovery; all paths, coverage, genomes and fast results rebuilt; matching scalar/oracle records reused"}
    output.mkdir(parents=True)
    _write(output / "protocol.json", protocol)
    results, evaluations, quote_visits, parity_count = [], 0, 0, 0

    def evaluate(engine, path, genome, execution):
        nonlocal evaluations, quote_visits
        check()
        evaluations += 1
        quote_visits += len(path.times_ns)
        if evaluations > BUDGET["max_evaluations"] or quote_visits > BUDGET["max_engine_quote_visits"]:
            raise ValueError("range study evaluation budget exhausted")
        value = engine(path, genome) if isinstance(engine, FastEvaluator) else engine(path, genome, execution=execution)
        return json.loads(_encode(asdict(value)))

    with (output / "results.jsonl").open("xb") as stream:
        for index, row in enumerate(rows):
            check()
            message = row["first_priced"]
            kind = ("range" if message["kind"] == "operational_range" else "single_price") if message else None
            loaded = {}
            for minutes in (60, 240):
                reasons, path, coverage_row = [], None, None
                if not row["initial_receipt_supported"]:
                    reasons = row["entry_reasons"]
                elif message is None:
                    reasons = ["no_associated_priced_message"]
                elif message["barrier_reasons"]:
                    reasons = message["barrier_reasons"]
                else:
                    initial = receipt_trigger(row)
                    trigger = replace(initial, signal_id="priced:" + row["signal_id"], observed_at=utc(message["received_utc"]),
                        published_at=utc(message["published_utc"]), source_row_sha256=message["source_row_sha256"], source_evidence_tier=message["evidence_tier"])
                    stamp, horizon = msc(message["received_utc"]), minutes * 60 + 310
                    market, ids, missing = source.window("XAUUSD", stamp, stamp + horizon * 1000)
                    fx, fxids, fxmissing = source.window("EURUSD", stamp, stamp + horizon * 1000)
                    coverage_row = coverage_metrics(stamp, horizon, market, fx, coverage)
                    reasons = list(coverage_row["coverage_reasons"])
                    if missing or fxmissing:
                        reasons.append("raw_source_day_missing")
                    if not reasons:
                        template = StrategyGenome(schema_version=2, entry_mode="signal_market", leg_count=1, volume_weights=(.01,),
                            target_mode="none", stop_mode="none", be_mode="none", provider_management_mode="ignore")
                        path = make_path(trigger, template, market=market, conversion=fx,
                            cutoff=trigger.observed_at + timedelta(seconds=horizon), contract_size=reference["money"]["contract_size"], currency_digits=2,
                            max_fx_age_ms=coverage["max_fx_age_ms"], max_fx_interval_ms=coverage["max_fx_interval_ms"],
                            market_sha256=_sha({"audit": coverage["raw_audit_sha256"], "days": ids}),
                            conversion_sha256=_sha({"audit": coverage["raw_audit_sha256"], "days": fxids}))
                loaded[minutes] = (path, reasons, coverage_row)
            for rule in rules:
                path, reasons, coverage_row = loaded[rule["hold_minutes"]]
                genome, nominal, status = None, None, "data_blocked"
                if kind is not None and rule["kind"] != kind:
                    status, reasons = "not_applicable", ["different_entry_format"]
                elif path is not None:
                    initial_quote = float(path.ask[0] if row["direction"] == "BUY" else path.bid[0])
                    try:
                        genome, nominal = build_genome(row, rule, initial_quote, path.contract_size)
                        status = "ready"
                    except ValueError as exc:
                        status, reasons = "risk_or_levels_blocked", [str(exc)]
                for name, execution in executions.items():
                    record = {"signal_id": row["signal_id"], "received_utc": row["received_utc"],
                        "priced_received_utc": message["received_utc"] if message else None,
                        "direction": row["direction"], "evidence_tier": row["source_evidence_tier"],
                        "priced_source_sha256": message["source_row_sha256"] if message else None,
                        "policy_id": rule["id"], "profile": name, "status": status, "reasons": reasons,
                        "coverage": coverage_row, "genome": asdict(genome) if genome else None,
                        "strategy_fingerprint": genome.fingerprint if genome else None,
                        "nominal_risk_usd": nominal, "result": None, "net_after_cost_eur": None, "parity": None}
                    if status == "ready":
                        result = evaluate(fast[name], path, genome, execution)
                        record["result"] = result
                        record["status"] = "engine_blocked" if result["blockers"] else "unfilled" if result["unfilled"] else "simulated"
                        record["reasons"] = result["blockers"]
                        if row["signal_id"] in sampled:
                            if prior_rows:
                                pair = retained_parity(record, prior_rows[(row["signal_id"], rule["id"], name)])
                                reused += 1
                            else:
                                pair = {"fast": result, "scalar": evaluate(simulate, path, genome, execution),
                                        "oracle": evaluate(oracle_simulate, path, genome, execution_to_scenario(execution))}
                            outcome, blockers, differences = compare_results(pair)
                            record["parity"] = {"status": outcome, "mismatches": differences, "engines": pair}
                            parity_count += 1
                            if outcome == "engine_disagreement":
                                _write(output / "incident.json", record)
                                raise ValueError("range study engine disagreement; incident retained")
                        if record["status"] in {"simulated", "unfilled"}:
                            record["net_after_cost_eur"] = str(Decimal(result["pnl_eur"]) - Decimal(str(result["filled_volume"])) * EXTRA_COST_EUR_PER_LOT)
                    results.append(record)
                    stream.write(_encode(record))
            for evaluator in fast.values():
                evaluator.clear_cache()
            if (index + 1) % 10 == 0:
                stream.flush()
                progress({"signals": index + 1, "total": len(rows), "evaluations": evaluations, "parity_cases": parity_count,
                          "elapsed_seconds": round(time.monotonic() - started, 1)})
    if len(results) != len(rows) * len(rules) * len(profiles):
        raise ValueError("incomplete range matrix")
    summary = summarize(results)
    _write(output / "summary.json", summary)
    _check_current(implementation)
    _verify_sources(watched)
    manifest = {"schema_version": SCHEMA, "status": "complete_retrospective_hypotheses_only",
        "artifacts": {name: {"sha256": _digest(output / name)} for name in ("protocol.json", "results.jsonl", "summary.json")},
        "inputs": {"watched_files": watched}, "resources": {"evaluations": evaluations, "engine_quote_visits": quote_visits,
            "source_quotes": source.decoded_rows, "parity_cases": parity_count, "parity_cases_reused": reused,
            "wall_seconds": time.monotonic() - started}}
    manifest["study_identity_sha256"] = _sha(manifest)
    _write(output / "manifest.json", manifest)
    return manifest


def verify_study(output_dir):
    output = Path(output_dir)
    manifest = _read(output / "manifest.json")
    if _sha({k:v for k,v in manifest.items() if k != "study_identity_sha256"}) != manifest["study_identity_sha256"]:
        raise ValueError("range manifest mismatch")
    for name, proof in manifest["artifacts"].items():
        if Path(name).name != name or _digest(output / name) != proof["sha256"]:
            raise ValueError("range artifact mismatch")
    _verify_sources(manifest["inputs"]["watched_files"])
    rows = [json.loads(line) for line in (output / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    protocol = _read(output / "protocol.json")
    expected = {(r["signal_id"], p["id"], name) for r in rows for p in protocol["policies"] for name in protocol["execution_profiles"]}
    actual = {(r["signal_id"], r["policy_id"], r["profile"]) for r in rows}
    if expected != actual or len(actual) != len(rows) or summarize(rows) != _read(output / "summary.json"):
        raise ValueError("range matrix or summary mismatch")
    for row in rows:
        if row["parity"]:
            outcome, _, _ = compare_results(row["parity"]["engines"])
            if outcome != row["parity"]["status"] or outcome == "engine_disagreement":
                raise ValueError("range parity mismatch")
    _check_current(protocol["implementation"])
    return {"status": "artifacts_sources_matrix_summary_and_recorded_parity_verified", "identity": manifest["study_identity_sha256"],
            "engines_rerun": False, "rows": len(rows)}
