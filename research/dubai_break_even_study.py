"""Three frozen break-even managements on the retained causal range entries."""

from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal, ROUND_CEILING
import json
from pathlib import Path
import time

from research.causal_replay import make_path, utc
from research.dubai_annual_coverage import coverage_metrics, msc
from research.dubai_clock_audit import _write
from research.dubai_entry_probe import compare_results
from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.dataset import ProviderEvent
from research.dubai_iterative.engine import simulate
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.oracle import oracle_simulate
from research.dubai_range_entry_quality import decision, overlay_row
from research.dubai_range_study import build_genome, stats, summarize as summarize_base
from research.dubai_receipt_controls import receipt_trigger
from research.dubai_signal_anatomy import BoundedSource, verify_anatomy
from research.execution_profile import execution_from_mapping, execution_to_scenario
from research.strategy_study import ROOT, _check_current, _digest, _encode, _read, _sha, _verify_sources, _watch, current_identity, load_config


MANAGEMENTS = ("no_be", "provider_direct_be", "half_tp1_be")
BUDGET = {"max_signals": 300, "max_evaluations": 20000, "max_source_quotes": 150000000,
          "max_engine_quote_visits": 12000000000, "max_wall_seconds": 14400}
SOURCES = ("research/dubai_break_even_study.py", "tools/run_dubai_break_even_study.py",
    "research/dubai_range_study.py", "research/dubai_range_entry_quality.py",
    "research/dubai_signal_anatomy.py", "research/dubai_annual_coverage.py",
    "research/dubai_receipt_controls.py", "docs/development/2026-09-14-canal1-break-even-protocol.md")


def frozen_base(directory, snapshot, watch):
    directory, snapshot = Path(directory).resolve(), Path(snapshot).resolve()
    manifest, copies = _read(directory / "manifest.json"), _read(snapshot / "manifest.json")
    if _sha({k:v for k,v in manifest.items() if k != "study_identity_sha256"}) != manifest["study_identity_sha256"]:
        raise ValueError("base manifest mismatch")
    if copies["base_identity"] != manifest["study_identity_sha256"]:
        raise ValueError("wrong source snapshot")
    watch(directory / "manifest.json")
    watch(snapshot / "manifest.json")
    mapped = {r["original_path"]: r for r in copies["sources"]}
    if len(mapped) != len(copies["sources"]):
        raise ValueError("duplicate frozen source")
    for name, item in manifest["artifacts"].items():
        if Path(name).name != name:
            raise ValueError("invalid base artifact name")
        watch(directory / name, item["sha256"])
    protocol = _read(directory / "protocol.json")
    expected = dict(manifest["inputs"]["watched_files"])
    expected.update({str(ROOT / k): v for k,v in protocol["implementation"]["iterative"]["source_sha256"].items()})
    expected.update({str(ROOT / k): v for k,v in protocol["implementation"]["runner_sources"].items()})
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
    if summarize_base(rows) != _read(directory / "summary.json"):
        raise ValueError("frozen base summary mismatch")
    return protocol, rows, manifest["study_identity_sha256"]


def first_direct_be(row, messages):
    if not row["first_priced"]:
        return None
    start = utc(row["first_priced"]["received_utc"])
    eligible = [m for m in messages if m["signal_id"] == row["signal_id"] and m["kind"] == "management"
        and m["parsed"].get("action") == "MOVE_SL_TO_BE" and m["parsed"].get("modality") == "direct"
        and utc(m["received_utc"]) >= start]
    return min(eligible, key=lambda m: (utc(m["received_utc"]), m["source_line_1based"])) if eligible else None


def managed_genome(genome, name, initial_quote, direction):
    if name == "no_be":
        return genome
    if name == "provider_direct_be":
        return replace(genome, be_mode="provider", be_trigger=None)
    if name != "half_tp1_be":
        raise ValueError("unknown frozen management")
    sign = Decimal(1 if direction == "BUY" else -1)
    half = sign * (Decimal(str(genome.target_steps[0])) - Decimal(str(initial_quote))) / Decimal(2)
    threshold = max(Decimal(".01"), half.quantize(Decimal(".01"), rounding=ROUND_CEILING))
    return replace(genome, be_mode="price", be_trigger=float(threshold))


def summary(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["management"], row["profile"])].append(row)
    result = {"broad": {}, "r05": {}}
    for (management, profile), selected in sorted(grouped.items()):
        for population in result:
            values = [overlay_row(r, r["r05_decision"]) if population == "r05" and r["status"] in {"simulated", "unfilled"} else r for r in selected]
            value = stats(values)
            if value["net_after_cost_eur"] is not None:
                value["net_without_best_day_eur"] = str(Decimal(value["net_after_cost_eur"]) - max(map(Decimal,value["daily"].values())))
            result[population][management + "|" + profile] = value
    return {"populations": result, "selected_policy": None, "money_contract_verified": False,
        "account_currency_money_verified": False, "orders_sent": 0,
        "limitations": ["Retrospective discovery, not fresh OOS or live parity.",
            "200 USD per isolated signal is a research scale; no portfolio, margin or guaranteed risk cap.",
            "BE requests are not installed until modeled acceptance and may reject or retry.",
            "BE at entry price is not zero net after slippage and costs.",
            "First direct BE uses actual receipt and retains association/evidence limitations.",
            "Unknown data/outcomes remain blocked; no synthetic overnight costs.",
            "R05 abstention is separate from the broad cohort; no outcome-dependent signal filter."]}


def run(anatomy_dir, base_dir, snapshot_dir, raw_audit, output_dir, progress=lambda value: None):
    started, watched = time.monotonic(), {}
    anatomy, output, raw_audit = map(lambda p: Path(p).resolve(), (anatomy_dir, output_dir, raw_audit))
    if output.exists():
        raise ValueError("immutable BE output exists")
    def check():
        if time.monotonic() - started > BUDGET["max_wall_seconds"]:
            raise TimeoutError("BE wall budget exhausted")
    def watch(path, expected=None):
        check()
        path = Path(path).resolve()
        if path.is_relative_to(output) or output.is_relative_to(path):
            raise ValueError("BE output overlaps input")
        _watch(path, expected or _digest(path), watched)
    implementation = current_identity()
    _check_current(implementation)
    for name in SOURCES:
        watch(ROOT / name)
    old_protocol, old_rows, old_identity = frozen_base(base_dir, snapshot_dir, watch)
    anatomy_proof = verify_anatomy(anatomy)
    if anatomy_proof != old_protocol["anatomy_proof"]:
        raise ValueError("anatomy changed")
    for name in ("manifest.json", "signals.json", "messages.json"):
        watch(anatomy / name)
    signals, messages = _read(anatomy / "signals.json"), _read(anatomy / "messages.json")
    if not 0 < len(signals) <= BUDGET["max_signals"]:
        raise ValueError("signal budget exceeded")
    old = {(r["signal_id"], r["profile"]): r for r in old_rows if r["policy_id"] == "range:on_levels:tp1:60"}
    if set(old) != {(r["signal_id"], p) for r in signals for p in old_protocol["execution_profiles"]}:
        raise ValueError("baseline identities incomplete")
    coverage = old_protocol["coverage"]
    watch(raw_audit, coverage["raw_audit_sha256"])
    ref = coverage["threshold_reference"]
    watch(ref["path"], ref["sha256"])
    reference = load_config(ref["path"])
    source = BoundedSource(raw_audit, coverage["broker_clock"]["segments"], watch)
    profiles = deepcopy(old_protocol["execution_profiles"])
    for profile in profiles.values():
        profile["protection"]["policy_extension"] = "absolute_levels_be_v1"
    executions = {name: execution_from_mapping(value) for name,value in profiles.items()}
    fast = {name: FastEvaluator(execution=value) for name,value in executions.items()}
    protocol = {"schema_version": "dubai_break_even_study_v1", "implementation": implementation,
        "base_identity": old_identity, "anatomy": anatomy_proof, "managements": MANAGEMENTS,
        "retained_signal_ids": [r["signal_id"] for r in signals], "profiles": profiles,
        "coverage": coverage, "money": reference["money"], "horizon_seconds": 3910,
        "entry": "first priced range, immediate, same lots, absolute SL and TP1, 60 minutes",
        "be_clock": "first post-level direct MOVE_SL_TO_BE receipt; all other provider actions ignored",
        "price_be": "half initial quoted TP1 distance rounded up to 0.01, min 0.01, measured from scenario fill",
        "risk_usd": "200", "extra_cost_eur_per_filled_lot": "10", "r05_threshold": "0.50",
        "parity": "all calculable rows, fresh scalar/fast/oracle; no-be lifecycle compared to frozen base",
        "budget": BUDGET, "orders_sent": 0}
    output.mkdir(parents=True)
    _write(output / "protocol.json", protocol)
    rows, evaluations, quote_visits, parity_cases = [], 0, 0, 0
    def evaluate(engine, path, genome, execution):
        nonlocal evaluations, quote_visits
        check()
        evaluations += 1
        quote_visits += len(path.times_ns)
        if evaluations > BUDGET["max_evaluations"] or quote_visits > BUDGET["max_engine_quote_visits"]:
            raise ValueError("BE evaluation budget exhausted")
        result = engine(path, genome) if isinstance(engine, FastEvaluator) else engine(path, genome, execution=execution)
        return json.loads(_encode(asdict(result)))
    with (output / "results.jsonl").open("xb") as stream:
        for index, row in enumerate(signals):
            check()
            message, path, cov, initial_quote, base_genome = row["first_priced"], None, None, None, None
            direct = first_direct_be(row, messages)
            old_base = old[(row["signal_id"], "reference")]
            if old_base["status"] in {"simulated", "unfilled", "engine_blocked"}:
                trigger = replace(receipt_trigger(row), signal_id="priced:" + row["signal_id"],
                    observed_at=utc(message["received_utc"]), published_at=utc(message["published_utc"]),
                    source_evidence_tier=message["evidence_tier"], source_row_sha256=message["source_row_sha256"])
                end = trigger.observed_at + timedelta(seconds=3910)
                if direct and utc(direct["received_utc"]) <= end:
                    trigger = replace(trigger, provider_events=(ProviderEvent(utc(direct["received_utc"]), "MOVE_SL_TO_BE",
                        {"modality": "direct", "source_message": direct}),))
                stamp = msc(message["received_utc"])
                market, days, missing = source.window("XAUUSD", stamp, stamp + 3910000)
                fx, fxdays, fxmissing = source.window("EURUSD", stamp, stamp + 3910000)
                cov = coverage_metrics(stamp, 3910, market, fx, coverage)
                if missing or fxmissing or cov["coverage_reasons"] or cov != old_base["coverage"]:
                    raise ValueError("previously covered base window changed")
                template = StrategyGenome(schema_version=2, entry_mode="signal_market", leg_count=1, volume_weights=(.01,),
                    target_mode="none", stop_mode="none", be_mode="none", provider_management_mode="ignore")
                path = make_path(trigger, template, market=market, conversion=fx, cutoff=end,
                    contract_size=reference["money"]["contract_size"], currency_digits=2,
                    max_fx_age_ms=coverage["max_fx_age_ms"], max_fx_interval_ms=coverage["max_fx_interval_ms"],
                    market_sha256=_sha({"audit": coverage["raw_audit_sha256"], "days": days}),
                    conversion_sha256=_sha({"audit": coverage["raw_audit_sha256"], "days": fxdays}))
                initial_quote = float(path.ask[0] if row["direction"] == "BUY" else path.bid[0])
                base_genome, nominal = build_genome(row, {"entry": "on_levels", "exit": "tp1", "hold_minutes": 60}, initial_quote, path.contract_size)
                if _encode(asdict(base_genome)) != _encode(old_base["genome"]) or nominal != old_base["nominal_risk_usd"]:
                    raise ValueError("baseline genome or risk changed")
            for name in MANAGEMENTS:
                genome = managed_genome(base_genome, name, initial_quote, row["direction"]) if base_genome else None
                for profile, execution in executions.items():
                    prior = old[(row["signal_id"], profile)]
                    record = deepcopy(prior)
                    record.update(management=name, policy_id=name, provider_be_message=direct,
                        r05_decision=decision(prior, ".50"), parity=None,
                        genome=asdict(genome) if genome else None,
                        strategy_fingerprint=genome.fingerprint if genome else None)
                    if path is not None:
                        pair = {"fast": evaluate(fast[profile], path, genome, execution),
                            "scalar": evaluate(simulate, path, genome, execution),
                            "oracle": evaluate(oracle_simulate, path, genome, execution_to_scenario(execution))}
                        status, blockers, differences = compare_results(pair)
                        parity_cases += 1
                        record.update(result=pair["fast"], status=status, reasons=blockers,
                            parity={"status": status, "mismatches": differences, "engines": pair}, net_after_cost_eur=None)
                        same_base = name != "no_be" or all(v == prior["result"].get(k) for k,v in pair["fast"].items() if k != "behavior_digest")
                        if status == "engine_disagreement" or not same_base:
                            record["unchanged_base_control"] = same_base
                            _write(output / "incident.json", record)
                            raise ValueError("BE engine or baseline disagreement; incident retained")
                        if status in {"simulated", "unfilled"}:
                            record["net_after_cost_eur"] = str(Decimal(pair["fast"]["pnl_eur"]) - Decimal(str(pair["fast"]["filled_volume"])) * Decimal(10))
                    rows.append(record)
                    stream.write(_encode(record))
            for evaluator in fast.values():
                evaluator.clear_cache()
            if (index + 1) % 10 == 0:
                stream.flush()
                progress({"signals": index + 1, "total": len(signals), "evaluations": evaluations,
                    "parity_cases": parity_cases, "elapsed_seconds": round(time.monotonic() - started, 1)})
    if len(rows) != len(signals) * 3 * 2:
        raise ValueError("incomplete BE matrix")
    _write(output / "summary.json", summary(rows))
    _check_current(implementation)
    _verify_sources(watched)
    manifest = {"schema_version": "dubai_break_even_study_v1", "status": "complete_retrospective_only",
        "inputs": watched, "artifacts": {name: _digest(output / name) for name in ("protocol.json", "results.jsonl", "summary.json")},
        "resources": {"evaluations": evaluations, "parity_cases": parity_cases, "source_quotes": source.decoded_rows,
            "engine_quote_visits": quote_visits, "wall_seconds": time.monotonic() - started}}
    manifest["identity"] = _sha(manifest)
    _write(output / "manifest.json", manifest)
    return manifest


def verify(directory):
    directory = Path(directory)
    manifest, protocol = _read(directory / "manifest.json"), _read(directory / "protocol.json")
    if _sha({k:v for k,v in manifest.items() if k != "identity"}) != manifest["identity"]:
        raise ValueError("BE manifest mismatch")
    for name, digest in manifest["artifacts"].items():
        if Path(name).name != name or _digest(directory / name) != digest:
            raise ValueError("BE artifact mismatch")
    _verify_sources(manifest["inputs"])
    _check_current(protocol["implementation"])
    rows = [json.loads(line) for line in (directory / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    expected = {(sid, name, profile) for sid in protocol["retained_signal_ids"] for name in protocol["managements"] for profile in protocol["profiles"]}
    if len(rows) != len(expected) or {(r["signal_id"],r["management"],r["profile"]) for r in rows} != expected:
        raise ValueError("BE matrix mismatch")
    if summary(rows) != _read(directory / "summary.json"):
        raise ValueError("BE summary mismatch")
    for row in rows:
        if row["parity"] and (compare_results(row["parity"]["engines"])[0] != row["status"] or row["status"] == "engine_disagreement"):
            raise ValueError("BE retained parity mismatch")
    return {"status": "sources_artifacts_matrix_summary_and_recorded_parity_verified", "identity": manifest["identity"],
        "rows": len(rows), "engines_rerun": False}
