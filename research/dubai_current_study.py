"""Bounded, offline comparison of Dubai's basket contract and retained R05."""

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal, ROUND_FLOOR
import json
from pathlib import Path
import time

from provider_action_semantics import is_strategy_close_action
from research.causal_replay import make_path, utc
from research.dubai_annual_coverage import coverage_metrics, msc
from research.dubai_break_even_study import BUDGET, summary as be_summary
from research.dubai_clock_audit import _write
from research.dubai_current_comparison import current_genome
from research.dubai_entry_probe import compare_results
from research.dubai_iterative.dataset import ProviderEvent
from research.dubai_iterative.engine import simulate
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.oracle import oracle_simulate
from research.dubai_range_entry_quality import decision, overlay_row
from research.dubai_range_study import build_genome
from research.dubai_receipt_controls import receipt_trigger
from research.dubai_signal_anatomy import BoundedSource, verify_anatomy
from research.execution_profile import execution_from_mapping, execution_to_scenario
from research.strategy_study import ROOT, _check_current, _digest, _encode, _read, _sha, _verify_sources, _watch, current_identity


KNOWN = {"simulated", "unfilled"}
BASES = ("dubai_current", "range_native", "range_equal25")
POLICIES = BASES + ("r05_native", "r05_equal25")
SOURCES = ("research/dubai_current_study.py", "tools/run_dubai_current_study.py",
    "research/dubai_current_comparison.py", "research/dubai_break_even_study.py",
    "research/dubai_range_entry_quality.py", "research/dubai_range_study.py",
    "research/dubai_signal_anatomy.py", "research/dubai_annual_coverage.py",
    "research/dubai_receipt_controls.py", "dubai_live_candidate.py",
    "strategy_runtime_contract.py", "strategy_shadow_contracts.py", "provider_action_semantics.py",
    "docs/development/2026-09-14-canal1-basket-current-run-protocol.md")


def frozen_be(directory, snapshot, watch):
    directory, snapshot = Path(directory).resolve(), Path(snapshot).resolve()
    manifest, copies = _read(directory / "manifest.json"), _read(snapshot / "manifest.json")
    if _sha({k: v for k, v in manifest.items() if k != "identity"}) != manifest["identity"]:
        raise ValueError("frozen BE manifest mismatch")
    if copies["be_identity"] != manifest["identity"]:
        raise ValueError("wrong basket source snapshot")
    watch(directory / "manifest.json")
    watch(snapshot / "manifest.json")
    mapped = {r["original_path"]: r for r in copies["sources"]}
    if len(mapped) != len(copies["sources"]):
        raise ValueError("duplicate frozen source")
    for name, digest in manifest["artifacts"].items():
        if Path(name).name != name:
            raise ValueError("invalid BE artifact name")
        watch(directory / name, digest)
    protocol = _read(directory / "protocol.json")
    expected = dict(manifest["inputs"])
    expected.update({str(ROOT / k): v for k, v in protocol["implementation"]["iterative"]["source_sha256"].items()})
    expected.update({str(ROOT / k): v for k, v in protocol["implementation"]["runner_sources"].items()})
    for name, digest in expected.items():
        if name in mapped:
            row = mapped[name]
            copy = Path(row["snapshot_path"]).resolve()
            if not copy.is_relative_to(snapshot) or row["sha256"] != digest:
                raise ValueError("frozen source path or hash mismatch")
            watch(copy, digest)
        else:
            watch(name, digest)
    rows = [json.loads(line) for line in (directory / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    if be_summary(rows) != _read(directory / "summary.json"):
        raise ValueError("frozen BE summary mismatch")
    return protocol, rows, manifest["identity"]


def provider_closes(row, messages, cutoff):
    start = utc(row["received_utc"])
    selected = [m for m in messages if m["signal_id"] == row["signal_id"]
        and m["kind"] == "management" and not m["barrier_reasons"]
        and m["parsed"].get("modality") == "direct"
        and is_strategy_close_action(m["parsed"].get("action"), "exact")
        and start <= utc(m["received_utc"]) <= cutoff]
    unique = {}
    for message in sorted(selected, key=lambda m: (utc(m["received_utc"]), m["source_line_1based"])):
        unique.setdefault(message["source_row_sha256"], message)
    return list(unique.values())


def resize_range(genome, quote, direction, fx_bid, contract_size=100):
    fx = Decimal(str(fx_bid))
    if not fx.is_finite() or fx <= 0:
        raise ValueError("invalid causal FX for nominal risk")
    distance = Decimal(1 if direction == "BUY" else -1) * (Decimal(str(quote)) - Decimal(str(genome.stop_value)))
    if distance <= 0 or genome.leg_count != 1 or genome.stop_mode != "fixed_level":
        raise ValueError("unsupported equal-risk range")
    unit_loss = distance * Decimal(str(contract_size))
    volume = min(Decimal(1), (Decimal(25) * fx / unit_loss / Decimal(".01")).to_integral_value(rounding=ROUND_FLOOR) * Decimal(".01"))
    if volume < Decimal(".01"):
        return None, 0, "0"
    result = replace(genome, volume_weights=(float(volume),))
    if result.validation_errors():
        raise ValueError("invalid resized range genome")
    return result, float(unit_loss * volume), str(unit_loss * volume / fx)


def aggregate(rows):
    known = [r for r in rows if r["status"] in KNOWN]
    if any(r["net_after_cost_eur"] is None for r in known):
        raise ValueError("known row without money")
    values = [Decimal(r["net_after_cost_eur"]) for r in known]
    daily = defaultdict(Decimal)
    for row, value in zip(known, values, strict=True):
        daily[row["received_utc"][:10]] += value
    gains = sum((v for v in values if v > 0), Decimal(0))
    losses = -sum((v for v in values if v < 0), Decimal(0))
    filled = [r for r in known if r["status"] == "simulated"]
    floating = [Decimal(r["result"]["max_floating_drawdown_eur"]) for r in filled
                if r["result"]["max_floating_drawdown_eur"] is not None]
    balance = high = drawdown = Decimal(0)
    for _, value in sorted(daily.items()):
        balance += value
        high = max(high, balance)
        drawdown = max(drawdown, high - balance)
    return {"rows": len(rows), "status_counts": dict(sorted(Counter(r["status"] for r in rows).items())),
        "known": len(known), "filled_signals": len(filled),
        "unknown_ids": sorted(r["signal_id"] for r in rows if r["status"] not in KNOWN),
        "net_after_cost_eur": str(sum(values, Decimal(0))) if values else None,
        "net_without_best_day_eur": str(sum(values, Decimal(0)) - max(daily.values())) if values else None,
        "worst_signal_eur": str(min(values)) if values else None,
        "profit_factor": float(gains / losses) if losses else None,
        "positive_signals": sum(v > 0 for v in values), "negative_signals": sum(v < 0 for v in values),
        "closed_day_drawdown_eur": str(drawdown) if values else None,
        "max_single_signal_floating_drawdown_eur": str(max(floating)) if floating else None,
        "monthly": {month: str(sum((v for day, v in daily.items() if day.startswith(month)), Decimal(0)))
                    for month in sorted({d[:7] for d in daily})},
        "daily": {d: str(v) for d, v in sorted(daily.items())}}


def summarize(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["policy_id"], row["profile"])].append(row)
    groups = {p + "|" + e: aggregate(values) for (p, e), values in sorted(grouped.items())}
    paired, horizons, selected_diagnostic = {}, {}, {}
    for profile in sorted({r["profile"] for r in rows}):
        dubai = {r["signal_id"]: r for r in grouped[("dubai_current", profile)]}
        for horizon in (14400, 3910):
            horizons[str(horizon) + "|" + profile] = aggregate([r for r in dubai.values() if r.get("horizon_seconds") == horizon])
        for policy in POLICIES[1:]:
            challenger = {r["signal_id"]: r for r in grouped[(policy, profile)]}
            ids = sorted(sid for sid in dubai.keys() & challenger.keys()
                         if dubai[sid]["status"] in KNOWN and challenger[sid]["status"] in KNOWN)
            paired[policy + "|" + profile] = {"ids": ids, "dubai_current": aggregate([dubai[sid] for sid in ids]),
                "challenger": aggregate([challenger[sid] for sid in ids])}
        retained = [r for r in grouped[("r05_native", profile)] if r["status"] == "simulated"]
        selected_diagnostic[profile] = [{"signal_id": r["signal_id"], "received_utc": r["received_utc"],
            "r05_native_net_eur": r["net_after_cost_eur"], "dubai_status": dubai[r["signal_id"]]["status"],
            "dubai_net_eur": dubai[r["signal_id"]]["net_after_cost_eur"]} for r in retained]
    return {"groups": groups, "paired": paired, "dubai_by_horizon": horizons,
        "retrospective_r05_fills_diagnostic": selected_diagnostic,
        "selected_policy": None, "money_contract_verified": False, "account_currency_money_verified": False,
        "current_vm_verified_today": False, "orders_sent": 0,
        "limitations": ["Reused discovery, not untouched OOS or live admission.",
            "Local Dubai strategy contract with deterministic direct-close interpretation, not historical runtime decisions.",
            "Isolated signals; aggregate money and closed-day drawdown are not concurrent account equity.",
            "Broker clock, hypothetical fills, intraday FX and extra costs remain declared research assumptions.",
            "Missing and still-open outcomes are unknown; known-subset sums can have censoring bias.",
            "25 EUR is nominal request-time risk or a basket trigger, not a guaranteed loss ceiling.",
            "Own-rule receipts and inferred associations retain original evidence tiers."]}


def run(history, output, progress=lambda value: None):
    started, watched = time.monotonic(), {}
    history, output = Path(history).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("immutable current study output exists")
    source = None
    def check():
        if time.monotonic() - started > BUDGET["max_wall_seconds"]:
            raise TimeoutError("current study wall budget exhausted")
        if source is not None and source.decoded_rows > BUDGET["max_source_quotes"]:
            raise ValueError("source quote budget exhausted")
    def watch(path, expected=None):
        check()
        path = Path(path).resolve()
        if path.is_relative_to(output) or output.is_relative_to(path):
            raise ValueError("current output overlaps input")
        _watch(path, expected or _digest(path), watched)
    implementation = current_identity()
    _check_current(implementation)
    for name in SOURCES:
        watch(ROOT / name)
    previous, old_rows, old_identity = frozen_be(history / "break_even_study_v2", history / "sources_before_basket_guard_v1", watch)
    anatomy = history / "signal_anatomy_v2"
    anatomy_proof = verify_anatomy(anatomy)
    if anatomy_proof != previous["anatomy"]:
        raise ValueError("anatomy changed")
    for name in ("manifest.json", "signals.json", "messages.json"):
        watch(anatomy / name)
    signals, messages = _read(anatomy / "signals.json"), _read(anatomy / "messages.json")
    if not 0 < len(signals) <= BUDGET["max_signals"]:
        raise ValueError("signal budget exceeded")
    old = {(r["signal_id"], r["profile"]): r for r in old_rows if r["management"] == "no_be"}
    if len(old) != len(signals) * 2 or set(old) != {(r["signal_id"], p) for r in signals for p in previous["profiles"]}:
        raise ValueError("incomplete old controls")
    coverage, money = previous["coverage"], previous["money"]
    if money["conversion_orientation"] != "account_base_profit_quote" or money["contract_size"] != 100 or money["currency_digits"] != 2:
        raise ValueError("unsupported frozen money contract")
    raw_audit = history / "raw_mt5_v2_audit.json"
    watch(raw_audit, coverage["raw_audit_sha256"])
    source = BoundedSource(raw_audit, coverage["broker_clock"]["segments"], watch)
    dubai_genome, contract = current_genome()
    profiles = {"range": deepcopy(previous["profiles"]), "dubai": deepcopy(previous["profiles"])}
    for profile in profiles["dubai"].values():
        profile["protection"]["policy_extension"] = "basket_guard_v1"
    executions = {(family, name): execution_from_mapping(value) for family, entries in profiles.items() for name, value in entries.items()}
    fast = {key: FastEvaluator(execution=value) for key, value in executions.items()}
    protocol = {"schema_version": "dubai_current_study_v1", "implementation": implementation,
        "frozen_be_identity": old_identity, "anatomy": anatomy_proof,
        "retained_signal_ids": [r["signal_id"] for r in signals], "base_policies": BASES, "policies": POLICIES,
        "profiles": profiles, "coverage": coverage, "money": money,
        "dubai_genome": asdict(dubai_genome), "strategy_fingerprint": contract.strategy_fingerprint,
        "execution_fingerprint": contract.execution_fingerprint, "dubai_horizons_seconds": [14400, 3910],
        "range_horizon_seconds": 3910, "risk_equal_eur": "25", "extra_cost_eur_per_filled_lot": "10",
        "design": SOURCES[-1], "budget": BUDGET, "orders_sent": 0}
    output.mkdir(parents=True)
    _write(output / "protocol.json", protocol)
    rows, evaluations, quote_visits, parity_cases, baseline_controls = [], 0, 0, 0, 0

    def path_for(trigger, genome, horizon):
        stamp, end = msc(trigger.observed_at.isoformat()), trigger.observed_at + timedelta(seconds=horizon)
        market, days, missing = source.window("XAUUSD", stamp, stamp + horizon * 1000)
        fx, fxdays, fxmissing = source.window("EURUSD", stamp, stamp + horizon * 1000)
        check()
        cov = coverage_metrics(stamp, horizon, market, fx, coverage)
        reasons = sorted(set(cov["coverage_reasons"] + (["missing_raw_day"] if missing or fxmissing else [])))
        if reasons:
            return None, cov, reasons
        return make_path(trigger, genome, market=market, conversion=fx, cutoff=end,
            contract_size=money["contract_size"], currency_digits=money["currency_digits"],
            max_fx_age_ms=coverage["max_fx_age_ms"], max_fx_interval_ms=coverage["max_fx_interval_ms"],
            market_sha256=_sha({"audit": coverage["raw_audit_sha256"], "days": days}),
            conversion_sha256=_sha({"audit": coverage["raw_audit_sha256"], "days": fxdays})), cov, []

    def evaluate(engine, path, genome, execution):
        nonlocal evaluations, quote_visits
        check()
        evaluations += 1
        quote_visits += len(path.times_ns)
        if evaluations > BUDGET["max_evaluations"] or quote_visits > BUDGET["max_engine_quote_visits"]:
            raise ValueError("current study evaluation budget exhausted")
        result = engine(path, genome) if isinstance(engine, FastEvaluator) else engine(path, genome, execution=execution)
        return json.loads(_encode(asdict(result)))

    with (output / "results.jsonl").open("xb") as stream:
        for index, row in enumerate(signals):
            check()
            prior_ref = old[(row["signal_id"], "reference")]
            range_path = range_genome = equal_genome = None
            nominal = equal_usd = 0
            equal_eur = "0"
            range_cov = prior_ref.get("coverage")
            if prior_ref["status"] in KNOWN | {"engine_blocked"}:
                message = row["first_priced"]
                trigger = replace(receipt_trigger(row), signal_id="priced:" + row["signal_id"],
                    observed_at=utc(message["received_utc"]), published_at=utc(message["published_utc"]),
                    source_evidence_tier=message["evidence_tier"], source_row_sha256=message["source_row_sha256"])
                range_path, range_cov, reasons = path_for(trigger, dubai_genome, 3910)
                if range_path is None or range_cov != prior_ref["coverage"]:
                    raise ValueError("old covered range window changed")
                quote = float(range_path.ask[0] if row["direction"] == "BUY" else range_path.bid[0])
                range_genome, nominal = build_genome(row, {"entry": "on_levels", "exit": "tp1", "hold_minutes": 60}, quote)
                if _encode(asdict(range_genome)) != _encode(prior_ref["genome"]) or nominal != prior_ref["nominal_risk_usd"]:
                    raise ValueError("old native genome or nominal risk changed")
                if not range_path.fx_valid[0]:
                    raise ValueError("equal risk has no causal FX")
                equal_genome, equal_usd, equal_eur = resize_range(range_genome, quote, row["direction"], range_path.fx_bid[0])
            dubai_path, dubai_cov, dubai_reasons, full_reasons, closes, horizon = None, None, [], [], [], None
            if row["initial_receipt_supported"]:
                trigger = receipt_trigger(row)
                closes = provider_closes(row, messages, trigger.observed_at + timedelta(seconds=14400))
                trigger = replace(trigger, provider_events=tuple(ProviderEvent(utc(m["received_utc"]), m["parsed"]["action"],
                    {"modality": "direct", "source_message": m}) for m in closes))
                for horizon in (14400, 3910):
                    dubai_path, dubai_cov, dubai_reasons = path_for(trigger, dubai_genome, horizon)
                    if dubai_path is not None:
                        break
                    if horizon == 14400:
                        full_reasons = dubai_reasons
            else:
                dubai_reasons = row["entry_reasons"] or ["unsupported_initial_receipt"]
            for policy in BASES:
                is_dubai = policy == "dubai_current"
                path = dubai_path if is_dubai else range_path
                genome = dubai_genome if is_dubai else range_genome if policy == "range_native" else equal_genome
                for profile in previous["profiles"]:
                    prior = old[(row["signal_id"], profile)]
                    record = {"signal_id": row["signal_id"], "received_utc": row["received_utc"],
                        "priced_received_utc": prior["priced_received_utc"], "direction": row["direction"],
                        "evidence_tier": row["source_evidence_tier"] if is_dubai else prior["evidence_tier"],
                        "source_row_sha256": row["first_source_row_sha256"] if is_dubai else prior.get("priced_source_sha256"),
                        "policy_id": policy, "profile": profile,
                        "status": "data_blocked" if is_dubai else prior["status"],
                        "reasons": dubai_reasons if is_dubai else prior["reasons"],
                        "coverage": dubai_cov if is_dubai else range_cov,
                        "horizon_seconds": horizon if is_dubai else 3910,
                        "full_horizon_coverage_reasons": full_reasons if is_dubai else [],
                        "provider_close_messages": closes if is_dubai else [],
                        "genome": asdict(genome) if genome else None,
                        "strategy_fingerprint": genome.fingerprint if genome else None,
                        "nominal_risk_usd": None if is_dubai else nominal if policy == "range_native" else equal_usd,
                        "nominal_risk_eur": "25" if is_dubai else equal_eur if policy == "range_equal25" else None,
                        "sizing_fx_bid": None if is_dubai or range_path is None else float(range_path.fx_bid[0]),
                        "result": None, "net_after_cost_eur": None, "parity": None}
                    if path is not None and genome is None:
                        record.update(status="unfilled", reasons=["risk_allocation_below_minimum_lot"],
                            net_after_cost_eur="0", result={"market_events": []})
                    elif path is not None:
                        key = ("dubai" if is_dubai else "range", profile)
                        execution = executions[key]
                        pair = {"fast": evaluate(fast[key], path, genome, execution),
                            "scalar": evaluate(simulate, path, genome, execution),
                            "oracle": evaluate(oracle_simulate, path, genome, execution_to_scenario(execution))}
                        status, blockers, differences = compare_results(pair)
                        parity_cases += 1
                        record.update(result=pair["fast"], status=status, reasons=blockers,
                            parity={"status": status, "mismatches": differences, "engines": pair})
                        same_base = policy != "range_native" or all(v == prior["result"].get(k)
                            for k, v in pair["fast"].items() if k != "behavior_digest")
                        if status == "engine_disagreement" or not same_base:
                            record["unchanged_base_control"] = same_base
                            _write(output / "incident.json", record)
                            raise ValueError("current engine or baseline disagreement; incident retained")
                        baseline_controls += policy == "range_native"
                        if status in KNOWN:
                            record["net_after_cost_eur"] = str(Decimal(pair["fast"]["pnl_eur"]) - Decimal(str(pair["fast"]["filled_volume"])) * Decimal(10))
                    rows.append(record)
                    stream.write(_encode(record))
                    if not is_dubai:
                        outcome = decision(record, ".50")
                        overlay = overlay_row(record, outcome)
                        overlay.update(policy_id="r05_native" if policy == "range_native" else "r05_equal25",
                                       r05_decision=outcome, parity=None, base_policy_id=policy)
                        if outcome["decision"] == "abstain":
                            overlay["nominal_risk_eur"] = "0"
                        rows.append(overlay)
                        stream.write(_encode(overlay))
            for evaluator in fast.values():
                evaluator.clear_cache()
            if (index + 1) % 10 == 0:
                stream.flush()
                progress({"signals": index + 1, "total": len(signals), "evaluations": evaluations,
                    "parity_cases": parity_cases, "elapsed_seconds": round(time.monotonic() - started, 1)})
    if len(rows) != len(signals) * len(POLICIES) * 2:
        raise ValueError("incomplete current matrix")
    _write(output / "summary.json", summarize(rows))
    _check_current(implementation)
    _verify_sources(watched)
    manifest = {"schema_version": protocol["schema_version"], "status": "complete_retrospective_only",
        "inputs": watched, "artifacts": {name: _digest(output / name) for name in ("protocol.json", "results.jsonl", "summary.json")},
        "resources": {"evaluations": evaluations, "parity_cases": parity_cases, "native_baseline_controls": baseline_controls,
            "source_quotes": source.decoded_rows, "engine_quote_visits": quote_visits, "wall_seconds": time.monotonic() - started}}
    manifest["identity"] = _sha(manifest)
    _write(output / "manifest.json", manifest)
    return manifest


def verify(directory):
    directory = Path(directory)
    manifest, protocol = _read(directory / "manifest.json"), _read(directory / "protocol.json")
    if _sha({k: v for k, v in manifest.items() if k != "identity"}) != manifest["identity"]:
        raise ValueError("current study manifest mismatch")
    for name, digest in manifest["artifacts"].items():
        if Path(name).name != name or _digest(directory / name) != digest:
            raise ValueError("current study artifact mismatch")
    _verify_sources(manifest["inputs"])
    _check_current(protocol["implementation"])
    rows = [json.loads(line) for line in (directory / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    expected = {(sid, policy, profile) for sid in protocol["retained_signal_ids"]
                for policy in protocol["policies"] for profile in protocol["profiles"]["range"]}
    if len(rows) != len(expected) or {(r["signal_id"], r["policy_id"], r["profile"]) for r in rows} != expected:
        raise ValueError("current study matrix mismatch")
    if summarize(rows) != _read(directory / "summary.json"):
        raise ValueError("current study summary mismatch")
    bases = {(r["signal_id"], r["policy_id"], r["profile"]): r for r in rows if r["policy_id"] in BASES}
    for row in rows:
        if row["parity"] and (compare_results(row["parity"]["engines"])[0] != row["status"] or row["status"] == "engine_disagreement"):
            raise ValueError("current study retained parity mismatch")
        if row["policy_id"].startswith("r05_"):
            base = bases[(row["signal_id"], row["base_policy_id"], row["profile"])]
            outcome = decision(base, ".50")
            adapted = overlay_row(base, outcome)
            if outcome != row["r05_decision"] or any(adapted[k] != row[k] for k in ("status", "net_after_cost_eur", "nominal_risk_usd", "result")):
                raise ValueError("current study R05 overlay mismatch")
    return {"status": "sources_artifacts_matrix_summary_and_recorded_parity_verified", "identity": manifest["identity"],
            "rows": len(rows), "engines_rerun": False}
