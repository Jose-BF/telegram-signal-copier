"""Preregister a bounded future Gold control; validate local evidence in stages.

No collector or live code is executed here. The sidecar run hook and later-stage
checks execute the three local engines independently of observed outcomes.
A parent-supplied execution profile and code-bound verification precede freeze. Successful checks only
admit evidence for review; they never certify MT5 realism or authorize search.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, fields
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sys
import time

import numpy as np
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.causal_replay import RAW_FIELDS, compile_signals, make_path, time_ns
from tools import run_causal_controls as causal
from tools import run_protection_controls as protection

# Historical v1 cohort only. Explicit windows belong to protocol v2.
START = datetime(2026, 9, 9, 6, 30, tzinfo=timezone.utc)
END = datetime(2026, 9, 9, 8, 30, tzinfo=timezone.utc)
CAPABILITIES = ("market_open_request_fill_ack", "sltp_request_accept_install", "market_close", "money")
EXACT_NATIVE_CHECKS = ("native_accounting", "request_native_binding")
LIFECYCLE_CHECKS = ("requests", "accepted_or_rejected", "installed_protection", "native_deals", *EXACT_NATIVE_CHECKS)
CAPABILITY_SCOPE = "local_bounded_declared_profile"
RUNNER_SOURCE = "tools/run_simulator_forward.py"
BUDGET = {"min_signals": 2, "max_signals": 11, "max_engine_evaluations": 132,
          "max_wall_seconds": 600, "max_raw_messages": 50_000, "max_tape_rows": 2_000_000,
          "max_json_bytes": 16_000_000}
DATA_RULES = {"channel": "canal2", "selection": "all_causal_gold_now_in_half_open_window",
              "symbol": "XAUUSD", "conversion_symbol": "EURUSD", "account_currency": "EUR",
              "contract_size": 100.0, "currency_digits": 2, "fx_max_age_ms": 5000,
              "broker_epoch_offset_seconds": 10800, "missing_cases": "retain_and_block",
              "max_window_hours": 2, "approved_tolerances": None,
              "commission": "hypothetical_zero", "swap": "no_rollover_admitted"}
MARKET_SOURCES = ("research/dubai_iterative/market_contract.py", "research/dubai_iterative/market.py")
EXTRA_SOURCES = ("tools/prepare_simulator_forward.py", "tools/run_causal_controls.py", *MARKET_SOURCES)


def _now():
    return datetime.now(timezone.utc)


def utc(value):
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be explicitly normalized UTC (Z or +00:00)")
    return parsed.astimezone(timezone.utc)


def _validate_window(start, end):
    start, end = utc(start), utc(end)
    if not start < end or end - start > timedelta(hours=DATA_RULES["max_window_hours"]):
        raise ValueError("forward window must be positive and at most two hours")
    return start, end


def _protocol_window(protocol):
    contract = protocol.get("contract")
    if contract not in ("simulator_forward_protocol_v1", "simulator_forward_protocol_v2"):
        raise ValueError("unsupported forward protocol contract")
    start, end = _validate_window(protocol["start_utc"], protocol["end_utc"])
    if contract == "simulator_forward_protocol_v1" and (start != START or end != END):
        raise ValueError("historical v1 forward window cannot be relabeled")
    return start, end


def _same(left, right):
    return protection.encode(left) == protection.encode(right)


def identity():
    return {"controls": protection.identity(),
            "forward_sources": {name: protection.digest(ROOT / name) for name in EXTRA_SOURCES}}


def _path(path):
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(ROOT.resolve()):
        raise ValueError("forward evidence must remain inside the project workspace")
    return resolved


def _read(path, watched):
    path = _path(path)
    if path.stat().st_size > BUDGET["max_json_bytes"]:
        raise ValueError(f"bounded JSON input exceeded: {path.name}")
    value, sha = protection.read_frozen(path)
    if path in watched and watched[path] != sha:
        raise ValueError(f"frozen input changed during execution: {path.name}")
    watched[path] = sha
    return value, sha


def _proof(proof, watched):
    path = _path(proof["path"])
    actual = protection.digest(path)
    if actual != proof["sha256"]:
        raise ValueError(f"evidence hash mismatch: {path.name}")
    if path in watched and watched[path] != actual:
        raise ValueError(f"frozen input changed during execution: {path.name}")
    watched[path] = actual
    return path


def _finish(watched, expected_identity, started):
    for path, sha in watched.items():
        protection.verify_frozen(path, sha)
    if not _same(identity(), expected_identity):
        raise ValueError("implementation changed during forward validation")
    if any(name in sys.modules for name in protection.FORBIDDEN_IMPORTS):
        raise ValueError("live or observed replay module imported across the offline boundary")
    if time.monotonic() - started > BUDGET["max_wall_seconds"]:
        raise ValueError("forward validation wall budget exhausted")


def _producer(proof, protocol, watched):
    path = _proof(proof, watched)
    additional = protocol["profile_payload"].get("additional_sources", [])
    sources = protocol["implementation"]["controls"].get("control_sources", {})
    admitted = {str((ROOT / name).resolve()): sha for name, sha in sources.items()}
    admitted.update({str(_path(row["path"])): row["sha256"] for row in additional})
    if admitted.get(str(path)) != proof["sha256"]:
        raise ValueError("evidence producer was not frozen before outcomes")


def _execution(payload, *, objects=False):
    from research.dubai_iterative.engine import ExecutionAssumptions
    from research.dubai_iterative.oracle import ExecutionScenario
    from research.dubai_iterative.market_contract import MarketProfile
    from research.dubai_iterative.protection_contract import ProtectionProfile
    values = dict(payload)
    spec = values.get("protection")
    if not isinstance(spec, dict) or spec.get("initial_protections", []):
        raise ValueError("forward independent profile needs protection without actual initial states")
    spec = dict(spec, initial_protections=())
    values["protection"] = ProtectionProfile(**spec)
    market = values.get("market")
    if market is not None:
        if not isinstance(market, dict):
            raise ValueError("forward market profile must be a MarketProfile dictionary")
        values["market"] = MarketProfile(**market)
    execution = ExecutionAssumptions(**values)
    # Both public execution contracts must accept the final parent profile.
    ExecutionScenario(**values)
    return execution if objects else asdict(execution)


def _readiness(profile_path, watched, current_identity, now, *, start):
    profile, profile_sha = _read(profile_path, watched)
    if profile.get("contract") != "simulator_forward_profile_v1":
        raise ValueError("unsupported final execution profile contract")
    execution = _execution(profile["execution"])
    verification_path = _proof(profile["verification"], watched)
    verification, _ = _read(verification_path, watched)
    blockers = []
    if execution["market"] is None:
        blockers.append("market_profile_missing")
    expected = current_identity["controls"]["iterative"]["sha256"]
    verified_sources = current_identity["controls"]["iterative"].get("source_sha256", {})
    if any(verified_sources.get(name) != current_identity["forward_sources"][name] for name in MARKET_SOURCES):
        blockers.append("engine_verification_identity_missing_market_sources")
    suite = verification.get("suite", {})
    if (verification.get("status") != "local_suite_verified"
            or verification.get("implementation_sha256") != expected
            or verification.get("exit_code") != 0
            or verification.get("same_implementation") is not True
            or verification.get("changed_sources") != []
            or verification.get("changed_wrappers") != []
            or type(suite.get("tests")) is not int or suite["tests"] < 1
            or any(type(suite.get(key)) is not int or suite[key] != 0
                   for key in ("failures", "errors", "skipped"))):
        blockers.append("current_engine_verification_missing_or_failed")
    completed = utc(verification["completed_at_utc"])
    if completed > now or completed >= start:
        blockers.append("verification_not_completed_before_forward_window")
    capabilities = profile.get("capabilities", {})
    for name in CAPABILITIES:
        if capabilities.get(name) != "verified":
            blockers.append(f"capability_unverified:{name}")
    if now >= start:
        blockers.append("forward_freeze_deadline_missed")
    if execution["protection"]["freeze_level_points"] != 0:
        blockers.append("nonzero_freeze_not_admitted")
    if any(execution["protection"][key] != value for key, value in (
        ("point", 0.01), ("digits", 2), ("stops_level_points", 20)
    )):
        blockers.append("symbol_constraints_not_admitted")
    for proof in profile.get("additional_sources", []):
        if _proof(proof, watched).suffix != ".py":
            raise ValueError("additional producer sources must be local Python source files")
    runner_count = sum(_path(row["path"]) == (ROOT / RUNNER_SOURCE).resolve()
                       for row in profile.get("additional_sources", []))
    if runner_count == 0:
        blockers.append("forward_runner_source_missing")
    elif runner_count != 1:
        blockers.append("forward_runner_source_duplicated")
    return profile, profile_sha, execution, blockers


def prepare(profile_path, *, out=None, now=None, start_utc=None, end_utc=None):
    """Readiness is read-only. Freeze publishes only a complete immutable directory."""
    if (start_utc is None) != (end_utc is None):
        raise ValueError("start_utc and end_utc must be supplied together")
    explicit_window = start_utc is not None
    start, end = _validate_window(start_utc, end_utc) if explicit_window else (START, END)
    now = utc(_now() if now is None else now)
    admission_now = now if out is None else min(now, utc(_now()))
    started, watched, implementation = time.monotonic(), {}, identity()
    profile, profile_sha, execution, blockers = _readiness(
        profile_path, watched, implementation, admission_now, start=start)
    if now >= start and "forward_freeze_deadline_missed" not in blockers:
        blockers.append("forward_freeze_deadline_missed")
    from research.gold_iterative.contracts import gold_555_genome
    genome = gold_555_genome()
    report = {"status": "blocked" if blockers else "ready_to_freeze",
              "engine_state": ("engine_incomplete"
                               if any(reason != "forward_freeze_deadline_missed" for reason in blockers)
                               else "admitted_for_bounded_control"),
              "blockers": blockers, "start_utc": start, "end_utc": end,
              "capability_scope": CAPABILITY_SCOPE, "quantitative_agreement_admitted": False,
              "full_live_parity_verified": False, "mass_search_authorized": False, "search_candidates": 0}
    _finish(watched, implementation, started)
    if out is None or blockers:
        return report
    frozen_at = utc(_now())
    if frozen_at >= start:
        raise ValueError("forward freeze deadline reached during preparation")
    if frozen_at < admission_now:
        raise ValueError("clock moved backwards during forward preparation")
    out = _path(out)
    if out.exists():
        raise ValueError("forward protocol directory already exists; never overwrite")
    protocol = {
        "contract": "simulator_forward_protocol_v2" if explicit_window else "simulator_forward_protocol_v1",
        "frozen_at_utc": frozen_at,
        "start_utc": start, "end_utc": end, "budget": BUDGET, "dataset_rules": DATA_RULES,
        "search_candidate_budget": 0, "mass_search_authorized": False,
        "full_live_parity_verified": False, "implementation": implementation,
        "engine_state": report["engine_state"],
        "capability_scope": CAPABILITY_SCOPE, "quantitative_agreement_admitted": False,
        "profile": {"path": str(_path(profile_path)), "sha256": profile_sha},
        "profile_payload": profile, "execution": execution,
        "genome": asdict(genome), "genome_fingerprint": genome.fingerprint,
        "engines": ["scalar", "fast", "oracle"],
        "sticker_directions": causal.STICKERS, "required_lifecycle_checks": LIFECYCLE_CHECKS,
        "installed_protection_evidence_scope": "observed_state",
        "server_installation_time_required": False,
        "stages": ["inputs", "independent", "observed"],
        "limitations": ["first_two_hours_do_not_certify_all_engine_capabilities",
                        "observed_server_processing_quotes_are_not_inferred",
                        "portfolio_margin_and_strategy_selection_out_of_scope"],
    }
    protocol["fingerprint"] = hashlib.sha256(protection.encode(protocol)).hexdigest()
    _finish(watched, implementation, started)
    completed_at = utc(_now())
    if completed_at >= start:
        raise ValueError("forward freeze deadline reached during final verification")
    if completed_at < frozen_at:
        raise ValueError("clock moved backwards during forward verification")
    out.mkdir(parents=True, exist_ok=False)
    sha = protection.save(out / "protocol.json", protocol)
    return report | {"status": "frozen_waiting_for_cohort", "protocol_sha256": sha,
                     "fingerprint": protocol["fingerprint"], "protocol": str(out / "protocol.json")}


def _frozen_protocol(path, watched, implementation):
    from research.gold_iterative.contracts import gold_555_genome
    protocol, sha = _read(path, watched)
    payload = {key: value for key, value in protocol.items() if key != "fingerprint"}
    if protocol.get("fingerprint") != hashlib.sha256(protection.encode(payload)).hexdigest():
        raise ValueError("forward protocol fingerprint mismatch")
    start, _ = _protocol_window(protocol)
    if (not _same(protocol.get("implementation"), implementation)
            or utc(protocol["frozen_at_utc"]) >= start
            or not _same(protocol.get("budget"), BUDGET)
            or not _same(protocol.get("dataset_rules"), DATA_RULES)
            or protocol.get("mass_search_authorized") is not False
            or protocol.get("engine_state") != "admitted_for_bounded_control"
            or protocol.get("capability_scope") != CAPABILITY_SCOPE
            or protocol.get("quantitative_agreement_admitted") is not False
            or protocol.get("full_live_parity_verified") is not False
            or protocol.get("engines") != ["scalar", "fast", "oracle"]
            or not _same(protocol.get("sticker_directions"), causal.STICKERS)
            or not _same(protocol.get("required_lifecycle_checks"), LIFECYCLE_CHECKS)
            or protocol.get("installed_protection_evidence_scope") != "observed_state"
            or protocol.get("server_installation_time_required") is not False
            or not _same(protocol.get("genome"), asdict(gold_555_genome()))
            or protocol.get("genome_fingerprint") != gold_555_genome().fingerprint
            or protocol.get("search_candidate_budget") != 0):
        raise ValueError("frozen implementation or forward protocol changed")
    _proof(protocol["profile"], watched)
    profile, _, execution, blockers = _readiness(
        protocol["profile"]["path"], watched, implementation, utc(protocol["frozen_at_utc"]), start=start)
    if (blockers or not _same(profile, protocol["profile_payload"])
            or not _same(execution, protocol["execution"])):
        raise ValueError("frozen engine profile admission changed")
    return protocol, sha


def _dataset_evidence(path, dataset, protocol, watched, now, *, start, cutoff):
    from tools import run_simulator_forward as runner

    evidence = dataset.get("source_evidence")
    if not isinstance(evidence, dict):
        raise ValueError("retained clock and metadata evidence required")
    captured_inputs = None
    if "capture_manifest" in evidence:
        capture_path = _proof(evidence["capture_manifest"], watched)
        captured_inputs = runner._capture_inputs(capture_path, cutoff, now, protocol, watched)
    for kind in ("clock", "metadata"):
        proof = evidence.get(kind)
        required = {"path", "sha256", "source_path", "source_sha256"}
        if not isinstance(proof, dict) or set(proof) != required:
            raise ValueError(f"retained {kind} evidence proof incomplete")
        retained = _proof(proof, watched)
        if retained != _path(path) / f"{kind}_evidence.json":
            raise ValueError(f"retained {kind} evidence belongs to another dataset")
        source_proof = {"path": proof["source_path"], "sha256": proof["source_sha256"]}
        source_path = _proof(source_proof, watched)
        value, _ = runner._fresh_evidence(
            retained, f"simulator_forward_{kind}_evidence_v1", cutoff, now, watched, start=start)
        if kind == "clock":
            runner._validate_clock(value)
        else:
            runner._validate_metadata(value, protocol["execution"])
        if captured_inputs is None:
            source, _ = _read(source_path, watched)
            if "event_cutoff_utc" in value:
                raise ValueError(f"retained {kind} capture source missing")
        else:
            if not _same(source_proof, captured_inputs[f"{kind}_proof"]):
                raise ValueError(f"retained {kind} source binding mismatch")
            source = captured_inputs[kind]
        if not _same(source, value):
            raise ValueError(f"retained {kind} evidence differs from its source projection")


def _dataset(path, protocol, protocol_sha, watched, now):
    start, end = _protocol_window(protocol)
    dataset, dataset_sha = _read(_path(path) / "protocol.json", watched)
    if (dataset.get("forward_protocol_sha256") != protocol_sha
            or dataset.get("universe") != "independent"
            or dataset.get("search_candidate_budget") != 0
            or dataset.get("mass_search_authorized") is not False
            or not _same(dataset.get("execution"), protocol["execution"])
            or dataset.get("genome_fingerprint") != protocol["genome_fingerprint"]):
        raise ValueError("dataset does not bind the frozen independent experiment")
    if any(dataset.get(key) for key in ("openings_sha256", "actual_preparation_sources", "conditioned_genomes")):
        raise ValueError("observed input leaked into independent dataset")
    _producer(dataset["producer"], protocol, watched)
    cutoff = utc(dataset["cutoff_utc"])
    if utc(dataset["start_utc"]) != start or not start < cutoff <= min(end, now):
        raise ValueError("dataset outside admissible future window")
    for key in ("contract_size", "currency_digits", "fx_max_age_ms", "broker_epoch_offset_seconds"):
        if dataset.get(key) != DATA_RULES[key]:
            raise ValueError(f"frozen dataset assumption changed: {key}")
    _dataset_evidence(path, dataset, protocol, watched, now, start=start, cutoff=cutoff)
    messages, messages_sha = _read(_path(path) / "raw_messages.json", watched)
    if messages_sha != dataset.get("raw_messages_sha256") or len(messages) > BUDGET["max_raw_messages"]:
        raise ValueError("raw message identity or budget mismatch")
    for row in messages:
        if set(row) - set(RAW_FIELDS) or row.get("ev") != "telegram_raw":
            raise ValueError("independent input must contain only raw message fields")
        if not start <= utc(row["ts"]) <= cutoff or utc(row["ts"]) >= end:
            raise ValueError("message outside admissible future window")
    signals, diagnostics = compile_signals(messages, start=start, cutoff=cutoff,
                                         sticker_directions=protocol["sticker_directions"])
    selected = [row.signal_id for row in signals if row.channel == "canal2"]
    if len(selected) > BUDGET["max_signals"] or dataset.get("expected_signal_ids") != selected:
        raise ValueError("signal denominator or budget mismatch; no truncation")
    blockers = [f"parser:{row['reason']}" for row in diagnostics if row.get("channel") == DATA_RULES["channel"]]
    excluded_diagnostics = [row for row in diagnostics if row.get("channel") != DATA_RULES["channel"]]
    incomplete = []
    for symbol in ("XAUUSD", "EURUSD"):
        proof = dataset["tapes"][symbol]
        tape_path = _proof(proof, watched)
        if pq.read_metadata(tape_path).num_rows > BUDGET["max_tape_rows"]:
            raise ValueError("quote tape exceeds forward row budget")
        times, bid, ask = protection.load_tape(proof, DATA_RULES["broker_epoch_offset_seconds"])
        if (not len(times) or np.any(np.diff(times) < 0) or not np.isfinite(bid).all()
                or not np.isfinite(ask).all() or np.any(bid <= 0) or np.any(ask < bid)):
            raise ValueError("invalid forward Bid/Ask tape")
        lookback = DATA_RULES["fx_max_age_ms"] * 1_000_000 if symbol == "EURUSD" else 0
        if times[0] < time_ns(start) - lookback or times[-1] > time_ns(cutoff):
            raise ValueError("quote tape outside future dataset boundary")
        if time_ns(cutoff) - times[-1] > DATA_RULES["fx_max_age_ms"] * 1_000_000:
            blockers.append(f"quote_tail_incomplete:{symbol}")
        if symbol == "XAUUSD":
            for signal in signals:
                if signal.channel == DATA_RULES["channel"] and times[-1] < time_ns(signal.observed_at):
                    incomplete.append(f"signal_without_market_quote:{signal.signal_id}")
    if cutoff < end:
        incomplete.append("observation_window_incomplete")
    if len(selected) < BUDGET["min_signals"]:
        incomplete.append("insufficient_natural_signals")
    dataset = dataset | {"messages_path": str(_path(path) / "raw_messages.json"),
                         "excluded_input_diagnostics": excluded_diagnostics}
    return dataset, dataset_sha, selected, blockers, incomplete, [
        {"signal_id": row.signal_id, "reason": "unsupported_protection_profile"}
        for row in signals if row.channel != "canal2"]


def _blocked_preparation(signal_id, genome, reason):
    from research.dubai_iterative.engine import SimulationResult
    from research.dubai_iterative.oracle import OracleResult
    values = dict(signal_id=signal_id, strategy_fingerprint=genome.fingerprint,
                  confidence_layer="counterfactual_entry", entries=(), exits=(), pnl_eur=None,
                  exit_reason="blocked", max_favourable_eur=None, max_adverse_eur=None,
                  max_floating_drawdown_eur=None, max_favourable_move=0., max_adverse_move=0.,
                  blockers=(reason,), last_tick_index=-1, unfilled=False, filled_volume=0.)
    return {"signal_id": signal_id, "preparation_blockers": [reason],
            "scalar": asdict(SimulationResult(**values)), "fast": asdict(SimulationResult(**values)),
            "oracle": asdict(OracleResult(**values))}


def _evaluation_count(rows):
    return sum(0 if row.get("preparation_blockers") else 3 for row in rows)


def _simulate(protocol, dataset, selected, watched, started):
    from research.dubai_iterative.contracts import StrategyGenome
    from research.dubai_iterative.engine import simulate
    from research.dubai_iterative.fast_engine import FastEvaluator
    from research.dubai_iterative.oracle import ExecutionScenario, oracle_simulate
    start, _ = _protocol_window(protocol)
    execution = _execution(protocol["execution"], objects=True)
    scenario = ExecutionScenario(**vars(execution))
    genome = StrategyGenome.from_dict(protocol["genome"])
    if genome.fingerprint != protocol["genome_fingerprint"]:
        raise ValueError("frozen genome fingerprint mismatch")
    messages, _ = _read(dataset["messages_path"], watched)
    signals, _ = compile_signals(messages, start=start, cutoff=utc(dataset["cutoff_utc"]),
                                 sticker_directions=protocol["sticker_directions"])
    signals = [row for row in signals if row.channel == "canal2"]
    if [row.signal_id for row in signals] != selected:
        raise ValueError("independent execution denominator changed")
    market, conversion = (protection.load_tape(dataset["tapes"][symbol], DATA_RULES["broker_epoch_offset_seconds"])
                          for symbol in ("XAUUSD", "EURUSD"))
    rows = []
    for signal in signals:
        _finish(watched, protocol["implementation"], started)
        try:
            path = make_path(signal, genome, market=market, conversion=conversion,
                             cutoff=utc(dataset["cutoff_utc"]), contract_size=DATA_RULES["contract_size"],
                             currency_digits=DATA_RULES["currency_digits"], max_fx_age_ms=DATA_RULES["fx_max_age_ms"],
                             market_sha256=dataset["tapes"]["XAUUSD"]["sha256"],
                             conversion_sha256=dataset["tapes"]["EURUSD"]["sha256"])
        except ValueError as exc:
            rows.append(_blocked_preparation(signal.signal_id, genome, f"path_preparation_blocked:{exc}"))
            continue
        row = {"signal_id": signal.signal_id}
        engines = (("scalar", lambda: simulate(path, genome, execution=execution)),
                   ("fast", lambda: FastEvaluator(execution=execution)(path, genome)),
                   ("oracle", lambda: oracle_simulate(path, genome, execution=scenario)))
        for name, evaluate in engines:
            if time.monotonic() - started > BUDGET["max_wall_seconds"]:
                raise ValueError("independent engine wall budget exhausted")
            row[name] = asdict(evaluate())
        rows.append(row)
    _finish(watched, protocol["implementation"], started)
    return rows


def _runner(protocol):
    path = (ROOT / RUNNER_SOURCE).resolve()
    proofs = [row for row in protocol["profile_payload"].get("additional_sources", [])
              if _path(row["path"]) == path]
    if len(proofs) != 1:
        raise ValueError("forward runner must be frozen exactly once in additional_sources")
    return {"path": str(path), "sha256": proofs[0]["sha256"]}


def _independent(path, protocol, protocol_sha, dataset_sha, selected, watched, expected_rows):
    from research.dubai_iterative.engine import SimulationResult
    from research.dubai_iterative.oracle import OracleResult
    result, sha = _read(path, watched)
    if (result.get("forward_protocol_sha256") != protocol_sha
            or result.get("protocol_sha256") != dataset_sha
            or result.get("universe") != "independent"
            or result.get("contract") != "simulator_forward_run_v1"
            or not _same(result.get("runner"), _runner(protocol))
            or not _same(result.get("implementation"), protocol["implementation"])
            or not _same(result.get("execution"), protocol["execution"])
            or result.get("search_candidates") != 0
            or result.get("mass_search_authorized") is not False):
        raise ValueError("independent results are not bound to the frozen inputs")
    rows = result["results"]
    if [row["signal_id"] for row in rows] != selected:
        raise ValueError("independent results dropped or duplicated a signal")
    if result.get("engine_evaluations") != _evaluation_count(expected_rows) or _evaluation_count(expected_rows) > BUDGET["max_engine_evaluations"]:
        raise ValueError("independent engine evaluation count mismatch")
    blockers = []
    if not _same(rows, expected_rows):
        blockers.append("independent_results_differ_from_local_replay")
    entries = exits = 0
    for row in rows:
        for name in protocol["engines"]:
            record = row[name]
            schema = OracleResult if name == "oracle" else SimulationResult
            if set(record) != {field.name for field in fields(schema)}:
                raise ValueError("engine result is not the complete dataclass record")
            if (record.get("signal_id") != row["signal_id"]
                    or record.get("strategy_fingerprint") != protocol["genome_fingerprint"]):
                raise ValueError("engine result signal or genome identity mismatch")
            blockers.extend(f"{row['signal_id']}:{name}:{reason}" for reason in record["blockers"])
            if any(entry.get("source") == "observed_mt5_fill" for entry in record["entries"]):
                raise ValueError("observed fill leaked into independent result")
        for name in ("scalar", "fast"):
            if protection.engine_mismatch_fields(row[name], row["oracle"]):
                blockers.append(f"{row['signal_id']}:engine_disagreement:{name}")
        entries += len(row["scalar"]["entries"])
        exits += len(row["scalar"]["exits"])
    return sha, blockers, {"simulated_entries": entries, "simulated_exits": exits}


def run(protocol_path, dataset_path, *, out, now=None):
    """Execute the fixed causal control, publishing no observed input or result."""
    now = utc(_now() if now is None else now)
    started, watched, implementation = time.monotonic(), {}, identity()
    protocol, protocol_sha = _frozen_protocol(protocol_path, watched, implementation)
    dataset, dataset_sha, selected, blockers, incomplete, excluded = _dataset(
        dataset_path, protocol, protocol_sha, watched, now)
    if blockers:
        _finish(watched, implementation, started)
        return {"status": "blocked", "blockers": blockers, "incomplete": incomplete,
                "engine_state": protocol["engine_state"], "capability_scope": CAPABILITY_SCOPE,
                "quantitative_agreement_admitted": False,
                "full_live_parity_verified": False, "mass_search_authorized": False, "search_candidates": 0}
    out = _path(out)
    if out.exists():
        raise ValueError("independent output directory already exists; never overwrite")
    rows = _simulate(protocol, dataset, selected, watched, started)
    payload = {"contract": "simulator_forward_run_v1", "runner": _runner(protocol),
               "implementation": implementation, "execution": protocol["execution"],
               "forward_protocol_sha256": protocol_sha, "protocol_sha256": dataset_sha,
               "universe": "independent", "search_candidates": 0, "mass_search_authorized": False,
               "full_live_parity_verified": False, "engine_evaluations": _evaluation_count(rows),
               "capability_scope": CAPABILITY_SCOPE, "quantitative_agreement_admitted": False,
               "results": rows, "completed_at_utc": _now(), "elapsed_seconds": time.monotonic() - started}
    if len(protection.encode(payload)) > BUDGET["max_json_bytes"]:
        raise ValueError("independent results exceed frozen JSON budget")
    _finish(watched, implementation, started)
    out.mkdir(parents=True, exist_ok=False)
    results = out / "independent_results.json"
    protection.save(results, payload)
    result_sha, blockers, counts = _independent(
        results, protocol, protocol_sha, dataset_sha, selected, watched, rows)
    if counts["simulated_exits"] < BUDGET["min_signals"]:
        incomplete.append("insufficient_completed_simulated_trades")
    _finish(watched, implementation, started)
    return {"status": "blocked" if blockers else "incomplete" if incomplete else "evidence_ready_for_review",
            "stage": "independent", "results": str(results), "results_sha256": result_sha,
            "engine_state": protocol["engine_state"], "engine_evaluations": _evaluation_count(rows),
            "capability_scope": CAPABILITY_SCOPE, "quantitative_agreement_admitted": False,
            "eligible_signal_ids": selected, "excluded_signals": excluded, "denominator": len(selected),
            "excluded_input_diagnostics": dataset["excluded_input_diagnostics"],
            "blockers": blockers, "incomplete": incomplete, "counts": counts,
            "full_live_parity_verified": False, "mass_search_authorized": False, "search_candidates": 0}


def _observed(path, protocol, protocol_sha, independent_sha, selected, watched):
    report, _ = _read(path, watched)
    if (report.get("forward_protocol_sha256") != protocol_sha
            or report.get("independent_results_sha256") != independent_sha
            or report.get("search_candidates") != 0
            or report.get("mass_search_authorized") is not False):
        raise ValueError("observed comparison does not bind the frozen independent results")
    _producer(report["producer"], protocol, watched)
    if [row["signal_id"] for row in report["rows"]] != selected:
        raise ValueError("observed comparison dropped or duplicated a signal")
    blockers = []
    coverage_gaps = []
    counts = {"observed_entries": 0, "observed_exits": 0}
    for row in report["rows"]:
        blockers.extend(row["blockers"])
        for name in counts:
            count = row.get(name)
            if type(count) is not int or count < 0:
                blockers.append(f"{row['signal_id']}:{name}_count_missing")
            else:
                counts[name] += count
        if (row.get("status") not in {"exact", "reviewed", "differences_for_review"}
                or not isinstance(row.get("differences"), list)):
            blockers.append(f"{row['signal_id']}:execution_hypothesis_review_missing")
        for name in LIFECYCLE_CHECKS:
            check = row.get("lifecycle_checks", {}).get(name, {})
            if name == "installed_protection" and check.get("status") in {"verified", "not_observed"}:
                if (check.get("scope") != "observed_state" or
                        "server_install_time" not in check or check["server_install_time"] is not None):
                    blockers.append(f"{row['signal_id']}:installed_protection_scope_or_time_unproven")
                    continue
                if check.get("status") == "not_observed" and check.get("evidence"):
                    for proof in check["evidence"]:
                        _proof(proof, watched)
                    coverage_gaps.append({"signal_id": row["signal_id"], "capability": name,
                                          "reason": "native_state_snapshot_not_observed"})
                    continue
            required_status = "exact" if name in EXACT_NATIVE_CHECKS else "verified"
            if check.get("status") != required_status or not check.get("evidence"):
                blockers.append(f"{row['signal_id']}:lifecycle_unverified:{name}")
            else:
                for proof in check["evidence"]:
                    _proof(proof, watched)
    return blockers, counts, [{"signal_id": row["signal_id"], "status": row.get("status"),
                              "differences": row.get("differences")} for row in report["rows"]], coverage_gaps


def check(protocol_path, dataset_path, *, stage="inputs", results=None, comparison=None, now=None):
    now = utc(_now() if now is None else now)
    if stage not in ("inputs", "independent", "observed"):
        raise ValueError("unsupported check stage")
    if (stage == "inputs" and (results is not None or comparison is not None)
            or stage == "independent" and comparison is not None):
        raise ValueError("stage boundary forbids observing later-phase evidence")
    started, watched, implementation = time.monotonic(), {}, identity()
    protocol, protocol_sha = _frozen_protocol(protocol_path, watched, implementation)
    dataset, dataset_sha, selected, blockers, incomplete, excluded = _dataset(
        dataset_path, protocol, protocol_sha, watched, now)
    counts = {}
    hypothesis_reviews = []
    coverage_gaps = []
    if stage in ("independent", "observed") and not blockers:
        if results is None:
            incomplete.append("independent_results_missing")
        else:
            expected_rows = _simulate(protocol, dataset, selected, watched, started)
            results_sha, issues, counts = _independent(
                results, protocol, protocol_sha, dataset_sha, selected, watched, expected_rows)
            blockers.extend(issues)
            if counts["simulated_exits"] < BUDGET["min_signals"]:
                incomplete.append("insufficient_completed_simulated_trades")
            if stage == "observed" and blockers:
                incomplete.append("observed_comparison_deferred_independent_blocked")
            elif stage == "observed":
                if comparison is None:
                    incomplete.append("observed_comparison_missing")
                else:
                    issues, observed_counts, hypothesis_reviews, coverage_gaps = _observed(
                        comparison, protocol, protocol_sha, results_sha, selected, watched)
                    blockers.extend(issues)
                    counts.update(observed_counts)
                    if coverage_gaps:
                        incomplete.append("observed_capability_coverage_incomplete")
                    if counts["observed_exits"] < BUDGET["min_signals"]:
                        incomplete.append("insufficient_completed_observed_trades")
    _finish(watched, implementation, started)
    return {"status": "blocked" if blockers else "incomplete" if incomplete else "evidence_ready_for_review",
            "stage": stage, "protocol_sha256": protocol_sha,
            "eligible_signal_ids": selected, "excluded_signals": excluded,
            "excluded_input_diagnostics": dataset["excluded_input_diagnostics"],
            "denominator": len(selected), "counts": counts, "blockers": sorted(set(blockers)),
            "incomplete": incomplete, "full_live_parity_verified": False,
            "engine_state": protocol["engine_state"],
            "capability_scope": CAPABILITY_SCOPE, "quantitative_agreement_admitted": False,
            "observed_hypothesis_reviews": hypothesis_reviews,
            "observed_coverage_gaps": coverage_gaps,
            "mass_search_authorized": False, "search_candidates": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    for mode in ("ready", "freeze"):
        command = modes.add_parser(mode)
        command.add_argument("--profile", type=Path, required=True)
        command.add_argument("--start-utc")
        command.add_argument("--end-utc")
        if mode == "freeze":
            command.add_argument("--out", type=Path, required=True)
    command = modes.add_parser("check")
    command.add_argument("--protocol", type=Path, required=True)
    command.add_argument("--dataset", type=Path, required=True)
    command.add_argument("--stage", choices=("inputs", "independent", "observed"), default="inputs")
    command.add_argument("--results", type=Path)
    command.add_argument("--comparison", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.mode == "check":
            result = check(args.protocol, args.dataset, stage=args.stage, results=args.results, comparison=args.comparison)
        else:
            result = prepare(args.profile, out=getattr(args, "out", None),
                             start_utc=args.start_utc, end_utc=args.end_utc)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        result = {"status": "blocked", "blockers": [str(exc)], "full_live_parity_verified": False,
                  "engine_state": "engine_incomplete",
                  "capability_scope": CAPABILITY_SCOPE, "quantitative_agreement_admitted": False,
                  "mass_search_authorized": False, "search_candidates": 0}
    sys.stdout.write(protection.encode(result).decode())
    sys.stdout.flush()
    return 0 if result["status"] in ("ready_to_freeze", "frozen_waiting_for_cohort", "evidence_ready_for_review") else 2


if __name__ == "__main__":
    raise SystemExit(main())
