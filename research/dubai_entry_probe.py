"""Bounded, isolated engine checks of the frozen hypothetical Canal 1 stream."""

from collections import Counter
from dataclasses import asdict
from datetime import timedelta
import json
from pathlib import Path
import time

from research.causal_replay import make_path
from research.dubai_annual_coverage import RawSource, coverage_metrics, msc
from research.dubai_entry_stream import to_causal_signals
from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.engine import simulate
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.oracle import oracle_simulate
from research.execution_profile import execution_from_mapping, execution_to_scenario
from research.strategy_study import (
    ROOT, _check_current, _digest, _encode, _publish, _read, _sha, _utc_explicit,
    _verify_sources, _watch, current_identity, load_config,
)


SCHEMA = "dubai_isolated_entry_probe_v1"
SCENARIOS = ("revision_time", "publication_initial")
BUDGET = {"max_signals": 64, "max_evaluations": 192, "max_path_quotes": 1_000_000,
          "max_source_quotes": 20_000_000, "max_wall_seconds": 600}
EXTRA_SOURCES = ("research/dubai_entry_probe.py", "tools/run_dubai_entry_probe.py",
                 "research/dubai_entry_stream.py", "research/dubai_annual_coverage.py",
                 "research/dubai_annual_universe.py", "research/dubai_export_catalog.py")
_IMPORTED = {name: _digest(ROOT / name) for name in EXTRA_SOURCES if (ROOT / name).is_file()}
LIMITATIONS = [
    "Known-version and unedited clocks are separate hypotheses, never observed receipt or original-entry replay.",
    "First complete UTC day with revision_time entries per month; all cases on those days, no coverage/outcome substitution.",
    "The unedited contrast uses the same calendar days, not matched trades; empty months remain empty.",
    "Each trigger starts a fresh isolated position state; no shared account, concurrency admission or annual portfolio.",
    "Reference volume is not investment capital; no margin, stop-out, capital adequacy or unlimited-capital claim.",
    "Reference strategy/execution/money are copied unchanged from the previously frozen control, not optimized here.",
    "Actual historical Bid/Ask spread; zero extra slippage, commission and swap are optimistic declared hypotheses.",
    "Declared EUR/USD conversion and broker clock, not verified real accounting or broker execution realism.",
    "Provider SL, TP, management and claimed outcomes are ignored; no fitting to provider results.",
    "All engine result fields except implementation-specific behavior_digest are compared to the independent oracle.",
    "Retrospective integration checks only; not a representative performance sample, untouched OOS or strategy selection.",
]


def select_days(triggers, *, start_utc, end_exclusive_utc):
    start, end = _utc_explicit(start_utc), _utc_explicit(end_exclusive_utc)
    if end <= start or len({r["trigger_id"] for r in triggers}) != len(triggers):
        raise ValueError("invalid period or duplicate trigger identity")
    days, stamps = {}, {}
    for row in triggers:
        stamp = _utc_explicit(row["trigger_utc"])
        if row["scenario"] not in SCENARIOS or not start <= stamp < end:
            raise ValueError("mixed scenario or trigger outside frozen period")
        stamps[row["trigger_id"]] = stamp
        midnight = stamp.replace(hour=0, minute=0, second=0, microsecond=0)
        if row["scenario"] == "revision_time" and start <= midnight and midnight + timedelta(days=1) <= end:
            month, day = stamp.strftime("%Y-%m"), stamp.strftime("%Y-%m-%d")
            days[month] = min(day, days.get(month, day))
    selected_days = sorted(days.values())
    selected = [r for r in triggers if stamps[r["trigger_id"]].strftime("%Y-%m-%d") in selected_days]
    selected.sort(key=lambda r: (stamps[r["trigger_id"]], r["scenario"], r["trigger_id"]))
    return selected_days, selected


def compare_results(results):
    if set(results) != {"scalar", "fast", "oracle"}:
        raise ValueError("parity requires all three engines")
    reference = results["oracle"]
    mismatches = {name: sorted(key for key in set(value) | set(reference)
        if key != "behavior_digest" and (key not in value or key not in reference or value[key] != reference[key]))
        for name, value in results.items() if name != "oracle"}
    blockers = sorted({reason for value in results.values() for reason in value["blockers"]})
    status = ("engine_disagreement" if any(mismatches.values()) else "engine_blocked" if blockers
              else "unfilled" if reference["unfilled"] else "simulated")
    return status, blockers, mismatches


def budget_reasons(case_count, path_quotes, source_quotes, budget):
    checks = ((case_count, "max_signals", "signal_budget_exceeded"),
              (3 * case_count, "max_evaluations", "evaluation_budget_exceeded"),
              (path_quotes, "max_path_quotes", "path_quote_budget_exceeded"),
              (source_quotes, "max_source_quotes", "source_quote_budget_exceeded"))
    return [reason for count, key, reason in checks if count > budget[key]]


class ProbeRawSource(RawSource):
    """Count every decoded source row, including a reload after cache eviction."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.decoded_rows = 0

    def day(self, symbol, day):
        key = (symbol, day)
        if key not in self.cache:
            self.decoded_rows += self.records[key]["rows"]
            if self.decoded_rows > BUDGET["max_source_quotes"]:
                raise ValueError("source decode budget exhausted before engines")
        return super().day(symbol, day)


def _archive(directory, identity_key, watch):
    directory = Path(directory).resolve()
    manifest = _read(directory / "manifest.json")
    watch(directory / "manifest.json")
    if _sha({k: v for k, v in manifest.items() if k != identity_key}) != manifest[identity_key]:
        raise ValueError("source archive identity mismatch")
    for name, proof in manifest["artifacts"].items():
        if Path(name).name != name:
            raise ValueError("unsafe source artifact path")
        watch(directory / name, proof["sha256"])
    for path, sha in manifest["inputs"]["watched_files"].items():
        watch(path, sha)
    return manifest


def _rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]


def _current(identity):
    _check_current(identity)
    if any(_digest(ROOT / name) != sha for name, sha in _IMPORTED.items()):
        raise ValueError("loaded probe implementation changed; use a fresh interpreter")


def evaluate_path(path, genome, execution, fast, deadline):
    results = {}
    for name, engine, profile in (("scalar", simulate, execution), ("fast", fast, execution),
                                  ("oracle", oracle_simulate, execution_to_scenario(execution))):
        deadline()
        result = engine(path, genome) if name == "fast" else engine(path, genome, execution=profile)
        results[name] = json.loads(_encode(asdict(result)))
        deadline()
    status, reasons, mismatches = compare_results(results)
    return {"status": status, "reasons": reasons, "mismatches": mismatches, "engines": results}


def run_probe(stream_dir, coverage_dir, raw_audit_path, output_dir):
    started, watched = time.monotonic(), {}
    stream_dir, coverage_dir, raw_audit_path, output = map(
        lambda p: Path(p).resolve(), (stream_dir, coverage_dir, raw_audit_path, output_dir))
    if output.exists():
        raise ValueError("immutable output already exists; verify it or use a new archive")

    def deadline():
        if time.monotonic() - started >= BUDGET["max_wall_seconds"]:
            raise TimeoutError("probe wall-time budget exhausted; no complete results")

    def watch(path, expected=None):
        deadline()
        sha = _digest(path) if expected is None else expected
        _watch(path, sha, watched)
        return sha

    identity = current_identity()
    _current(identity)
    for relative in EXTRA_SOURCES:
        watch(ROOT / relative)
    stream = _archive(stream_dir, "stream_identity_sha256", watch)
    coverage_archive = _archive(coverage_dir, "audit_identity_sha256", watch)
    if stream["schema_version"] != "dubai_entry_stream_v1" or stream["engine_dataset_ready"] is not False:
        raise ValueError("only the unadmitted hypothetical entry stream is supported")
    coverage_protocol = _read(coverage_dir / "protocol.json")
    watch(coverage_dir / "protocol.json", stream["inputs"]["coverage_protocol_sha256"])
    watch(raw_audit_path, coverage_protocol["raw_audit_sha256"])
    if coverage_protocol["broker_clock"]["status"] != "declared_hypothesis":
        raise ValueError("explicit hypothetical clock required")
    reference = coverage_protocol["threshold_reference"]
    watch(reference["path"], reference["sha256"])
    control = load_config(reference["path"])
    if control["horizon_seconds"] != 1200 or control["money"]["rollover_hour_server"] != 0:
        raise ValueError("probe requires the fixed 20-minute intraday control")
    for key in ("max_market_gap_ms", "max_fx_age_ms", "max_fx_interval_ms"):
        if control[key] != coverage_protocol[key]:
            raise ValueError("coverage thresholds differ from the frozen control")
    rolling = _read(stream_dir / "protocol.json")["rolling_protocol"]
    triggers = _rows(stream_dir / "triggers.jsonl")
    days, selected = select_days(triggers, start_utc=rolling["start"], end_exclusive_utc=rolling["end_exclusive"])
    if not selected:
        raise ValueError("no complete monthly trigger days")
    signals = {s.signal_id: s for scenario in SCENARIOS for s in to_causal_signals(
        [r for r in selected if r["scenario"] == scenario], scenario=scenario)}
    prior = {}
    for row in _rows(stream_dir / "coverage.jsonl"):
        if row["horizon_seconds"] == control["horizon_seconds"]:
            if row["trigger_id"] in prior:
                raise ValueError("duplicate trigger coverage identity")
            prior[row["trigger_id"]] = row
    if set(prior) != {r["trigger_id"] for r in triggers}:
        raise ValueError("incomplete or mixed stream coverage denominator")
    source = ProbeRawSource(raw_audit_path, coverage_protocol["broker_clock"]["segments"], watch)
    roots = [stream_dir, coverage_dir, source.directory,
             *map(Path, stream["inputs"]["protected_archive_dirs"])]
    if any(output.is_relative_to(root.resolve()) or root.resolve().is_relative_to(output) for root in roots):
        raise ValueError("output overlaps a protected archive")
    if any(Path(path).is_relative_to(output) for path in watched):
        raise ValueError("output contains a read-only input")
    source_days = sorted({day for r in selected for day in prior[r["trigger_id"]]["raw_source_days"]})
    source_quotes = sum(source.records[tuple(day.split(":"))]["rows"] for day in source_days
                        if tuple(day.split(":")) in source.records)
    planned_quotes = sum(prior[r["trigger_id"]]["market_quotes_in_horizon"] for r in selected)
    protocol = {"schema_version": SCHEMA, "data_use": "retrospective_integration_only", "budget": BUDGET,
        "selection_rule": "first_complete_UTC_day_per_month_with_revision_time_triggers_no_substitution",
        "days": days, "selected_triggers": selected, "full_stream_triggers": len(triggers),
        "outside_selected_days": len(triggers) - len(selected), "period": rolling,
        "exposure_policy": "isolated_trigger_reset_no_shared_account_no_portfolio_aggregation",
        "baseline_reference": reference, "strategy": control["strategy"], "execution": control["execution"],
        "money": control["money"], "horizon_seconds": control["horizon_seconds"],
        "coverage_protocol": coverage_protocol, "planned_path_quotes": planned_quotes,
        "planned_source_quotes": source_quotes, "source_days": source_days,
        "stream_identity_sha256": stream["stream_identity_sha256"],
        "coverage_identity_sha256": coverage_archive["audit_identity_sha256"],
        "implementation": identity, "sources": watched, "candidate_search_count": 0,
        "engine_dataset_ready": False, "limitations": LIMITATIONS}
    protocol["identity_sha256"] = _sha(protocol)
    _verify_sources(watched)
    _current(identity)
    _publish(output, {"protocol.json": protocol})
    print(json.dumps({"event": "protocol_frozen_before_quote_decode_and_engines", "days": days,
                      "cases": len(selected), "identity": protocol["identity_sha256"]}), flush=True)
    genome = StrategyGenome.from_dict(control["strategy"])
    execution = execution_from_mapping(control["execution"])
    rows = [dict(r, status="pending", reasons=[], engines={}, mismatches={}) for r in selected]
    over_budget = budget_reasons(len(rows), planned_quotes, source_quotes, BUDGET)
    paths, path_quotes = {}, 0
    if not over_budget:
        for row in rows:
            deadline()
            key, start = row["trigger_id"], msc(row["trigger_utc"])
            horizon = control["horizon_seconds"]
            market, market_ids, market_missing = source.window("XAUUSD", start, start + horizon * 1000)
            fx, fx_ids, fx_missing = source.window("EURUSD", start, start + horizon * 1000)
            metrics = coverage_metrics(start, horizon, market, fx, coverage_protocol)
            metrics.update(raw_source_days=market_ids + fx_ids, missing_raw_source_days=market_missing + fx_missing)
            if market_missing or fx_missing:
                metrics["coverage_reasons"] = sorted(set(metrics["coverage_reasons"] + ["raw_source_day_missing"]))
                metrics["quote_coverage_pass"] = False
            if (prior[key]["trigger_utc"] != row["trigger_utc"] or prior[key]["scenario"] != row["scenario"]
                    or any(prior[key][name] != value for name, value in metrics.items())):
                raise ValueError(f"coverage revalidation incident: {key}; no engine run")
            row["coverage"] = metrics
            path_quotes += metrics["market_quotes_in_horizon"]
            if not metrics["quote_coverage_pass"]:
                row.update(status="data_blocked", reasons=metrics["coverage_reasons"])
                continue
            proofs = {role: {"clock": coverage_protocol["broker_clock"], "raw_days": [source.records[tuple(day.split(":"))]
                            for day in ids]} for role, ids in (("market", market_ids), ("conversion", fx_ids))}
            try:
                paths[key] = make_path(signals[key], genome, market=market, conversion=fx,
                    cutoff=signals[key].observed_at + timedelta(seconds=horizon),
                    contract_size=control["money"]["contract_size"], currency_digits=control["money"]["currency_digits"],
                    max_fx_age_ms=control["max_fx_age_ms"], max_fx_interval_ms=control["max_fx_interval_ms"],
                    market_sha256=_sha(proofs["market"]), conversion_sha256=_sha(proofs["conversion"]))
            except ValueError as exc:
                row.update(status="data_blocked", reasons=[str(exc)])
        over_budget = budget_reasons(len(rows), path_quotes, source.decoded_rows, BUDGET)
    evaluations = 0
    _verify_sources(watched)
    _current(identity)
    if over_budget:
        for row in rows:
            row.update(status="budget_blocked", reasons=sorted(set(row["reasons"] + over_budget)))
    else:
        fast = FastEvaluator(execution=execution)
        for row in rows:
            if row["status"] == "pending":
                row.update(evaluate_path(paths[row["trigger_id"]], genome, execution, fast, deadline))
                evaluations += 3
            print(json.dumps({"event": "case_checked", "id": row["trigger_id"], "status": row["status"]}), flush=True)
    summary = {scenario: {"selected": sum(r["scenario"] == scenario for r in rows),
        "status_counts": dict(Counter(r["status"] for r in rows if r["scenario"] == scenario)),
        "days": {day: dict(Counter(r["status"] for r in rows if r["scenario"] == scenario
                                  and r["trigger_utc"].startswith(day))) for day in days}} for scenario in SCENARIOS}
    report = {"status": "hypothetical_only_diagnostic", "schema_version": SCHEMA,
        "protocol_sha256": _sha(protocol), "rows": rows, "summary": summary,
        "engine_evaluations": evaluations, "path_quotes_checked": path_quotes, "source_rows_decoded": source.decoded_rows,
        "money_contract_verified": False, "account_currency_money_verified": False,
        "full_live_parity_verified": False, "engine_dataset_ready": False, "automatic_admission": False,
        "portfolio": None, "aggregate_profit": None, "capital_adequacy_assessed": False,
        "search_candidates": 0, "selection": {"selected_policy": None, "promotion_eligible": False},
        "limitations": LIMITATIONS}
    manifest = {"schema_version": SCHEMA, "protocol_identity_sha256": protocol["identity_sha256"],
        "artifacts": {"protocol.json": _sha(protocol), "results.json": _sha(report)}}
    _verify_sources(watched)
    _current(identity)
    deadline()
    _publish(output, {"protocol.json": protocol, "results.json": report, "manifest.json": manifest})
    return report


def verify_probe(output_dir):
    output = Path(output_dir).resolve()
    if not output.is_dir() or {p.name for p in output.iterdir()} != {"protocol.json", "results.json", "manifest.json"}:
        raise ValueError("incomplete probe archive")
    manifest, protocol, report = (_read(output / name) for name in ("manifest.json", "protocol.json", "results.json"))
    if manifest["schema_version"] != SCHEMA or set(manifest["artifacts"]) != {"protocol.json", "results.json"}:
        raise ValueError("unexpected probe manifest")
    for name, sha in manifest["artifacts"].items():
        if (output / name).is_symlink() or _digest(output / name) != sha:
            raise ValueError("probe artifact hash mismatch")
    identity = protocol["identity_sha256"]
    if (identity != _sha({k: v for k, v in protocol.items() if k != "identity_sha256"})
            or manifest["protocol_identity_sha256"] != identity or report["protocol_sha256"] != _sha(protocol)):
        raise ValueError("mixed probe identities")
    _current(protocol["implementation"])
    _verify_sources(protocol["sources"])
    if [r["trigger_id"] for r in report["rows"]] != [r["trigger_id"] for r in protocol["selected_triggers"]]:
        raise ValueError("changed result denominator")
    return {"status": "verified_current_hypothetical_probe", "identity_sha256": identity,
            "summary": report["summary"], "engine_evaluations": report["engine_evaluations"]}
