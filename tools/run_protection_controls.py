"""Finite offline controls for the quote-clock SL/TP protection model.

Prepare freezes one of two universes. Actual-entry preparation may read MT5
results only to emit sanitized openings. Run consumes only its frozen study and
quote tapes; compare is the other phase allowed to read MT5 results. This tool
never searches strategies, imports live code, or trades.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys
import time

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.causal_replay import compile_signals, make_path, utc
from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.dataset import SignalLeg
from research.dubai_iterative.protection_contract import InitialProtection, ProtectionProfile
from research.iterative_provenance import implementation_identity


MAX_SIGNALS = 11
MAX_ENGINE_EVALUATIONS = 132
MAX_WALL_SECONDS = 600
EXECUTION = {"latency_ms": 0, "entry_fill_latency_ms": 0}
PROFILE_SPECS = (
    {
        "name": "processing_0_ack_0_retry_0",
        "point": 0.01,
        "digits": 2,
        "stops_level_points": 20,
        "freeze_level_points": 0,
        "processing_delay_ms": 0,
        "acknowledgement_delay_ms": 0,
        "retry_delay_ms": 0,
        "max_events": 100_000,
    },
    {
        "name": "processing_250_ack_250_retry_1000",
        "point": 0.01,
        "digits": 2,
        "stops_level_points": 20,
        "freeze_level_points": 0,
        "processing_delay_ms": 250,
        "acknowledgement_delay_ms": 250,
        "retry_delay_ms": 1000,
        "max_events": 100_000,
    },
)
CONTROL_SOURCES = (
    "tools/run_protection_controls.py",
    "research/causal_replay.py",
    "research/dubai_iterative/contracts.py",
    "research/dubai_iterative/dataset.py",
    "research/dubai_iterative/engine.py",
    "research/dubai_iterative/fast_engine.py",
    "research/dubai_iterative/oracle.py",
    "research/dubai_iterative/protection.py",
    "research/dubai_iterative/protection_contract.py",
    "research/gold_iterative/contracts.py",
    "parser.py",
    "provider_signal_catalog.py",
    "interpretation_firewall.py",
)
FORBIDDEN_IMPORTS = {
    "MetaTrader5",
    "listener",
    "executor",
    "gold_555_entry_watch",
    "gold_555_live_candidate",
    "dubai_live_candidate",
    "research.gold_iterative.live_parity",
    "research.gold_iterative.ledger_evidence",
}
_COMMENT = re.compile(r"^c2_(\d+)(?:_B([1-9]\d*))?_g55$")


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def read_frozen(path):
    raw = Path(path).read_bytes()
    return json.loads(raw.decode("utf-8-sig")), hashlib.sha256(raw).hexdigest()


def verify_frozen(path, expected_sha256):
    if digest(path) != expected_sha256:
        raise ValueError(f"frozen input changed during execution: {Path(path).name}")


def _default(value):
    if isinstance(value, (datetime, Decimal, Path)):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(type(value).__name__)


def encode(value):
    return (json.dumps(value, default=_default, sort_keys=True, indent=2,
                       allow_nan=False, ensure_ascii=True) + "\n").encode()


def save(path, value):
    with Path(path).open("xb") as stream:
        stream.write(encode(value))
    return digest(path)


def _workspace_path(path):
    resolved = Path(path).resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"control data must remain inside the workspace: {resolved}") from exc
    return resolved


def identity():
    return {
        "iterative": implementation_identity(),
        "control_sources": {name: digest(ROOT / name) for name in CONTROL_SOURCES},
    }


def _profile(spec, initial=()):
    return ProtectionProfile(
        **{key: value for key, value in spec.items() if key != "hypothesis_status"},
        initial_protections=tuple(initial),
    )


def _profile_payloads():
    return [asdict(_profile(spec)) | {
        "initial_protections": [],
        "hypothesis_status": "uncalibrated_not_fitted_not_certified"
    } for spec in PROFILE_SPECS]


def _artifact(manifest, name):
    matches = [row for row in manifest.get("artifacts", []) if row.get("name") == name]
    if len(matches) != 1:
        raise ValueError(f"analysis manifest does not identify exactly one {name}")
    return matches[0]


def verify_attempt_evidence(event_prefix, event_delta, attempts, reconstructed_sha256):
    """Prove every retained attempt is an unchanged row in the causal stream."""
    expected = {}
    for row in attempts:
        attempt_id = row.get("attempt_id")
        if not isinstance(attempt_id, str) or not attempt_id or attempt_id in expected:
            raise ValueError("attempt evidence has a missing or duplicate attempt_id")
        expected[attempt_id] = row
    seen = {}
    reconstructed = hashlib.sha256()
    reconstructed_bytes = 0
    for path, opener in ((Path(event_prefix), open), (Path(event_delta), gzip.open)):
        with opener(path, "rb") as stream:
            for line in stream:
                reconstructed.update(line)
                reconstructed_bytes += len(line)
                source = json.loads(line)
                if source.get("ev") != "mt5_action_attempt":
                    continue
                attempt_id = source.get("attempt_id")
                if attempt_id not in expected:
                    continue
                if attempt_id in seen:
                    raise ValueError(f"duplicate target attempt in causal stream: {attempt_id}")
                if encode(source) != encode(expected[attempt_id]):
                    raise ValueError(f"attempt evidence differs from causal stream: {attempt_id}")
                seen[attempt_id] = source
    actual_sha256 = reconstructed.hexdigest()
    if actual_sha256 != reconstructed_sha256:
        raise ValueError("reconstructed causal event identity mismatch")
    missing = sorted(expected.keys() - seen.keys())
    if missing:
        raise ValueError(f"attempt evidence missing from causal stream: {missing[0]}")
    return {
        "reconstructed_event_sha256": actual_sha256,
        "reconstructed_event_bytes": reconstructed_bytes,
        "causal_attempt_rows_verified": len(seen),
        "event_prefix_sha256": digest(event_prefix),
        "event_delta_gzip_sha256": digest(event_delta),
    }


def _load_actual_sources(analysis_dir, measurement_dir, source_protocol, event_prefix, event_delta):
    analysis_dir = _workspace_path(analysis_dir)
    measurement_dir = _workspace_path(measurement_dir)
    event_prefix = _workspace_path(event_prefix)
    event_delta = _workspace_path(event_delta)
    manifest_path = analysis_dir / "analysis_manifest.json"
    ledger_path = analysis_dir / "observed_ledgers.json"
    binding_path = analysis_dir / "native_open_binding.json"
    measurement_path = measurement_dir / "measurement.json"
    attempts_path = measurement_dir / "attempt_evidence.json"
    manifest, manifest_hash = read_frozen(manifest_path)
    ledger, ledger_hash = read_frozen(ledger_path)
    binding, binding_hash = read_frozen(binding_path)
    measurement, measurement_hash = read_frozen(measurement_path)
    attempts, attempts_hash = read_frozen(attempts_path)
    for path, actual_hash in ((ledger_path, ledger_hash), (binding_path, binding_hash)):
        proof = _artifact(manifest, path.name)
        if proof.get("sha256") != actual_hash or proof.get("bytes") != path.stat().st_size:
            raise ValueError(f"analysis artifact identity mismatch: {path.name}")
    if any((
        binding.get("status") != "exact",
        binding.get("errors") != [],
        binding.get("bound_openings") != 59,
        binding.get("broker_openings") != 59,
        binding.get("journal_successful_results") != 59,
    )):
        raise ValueError("native opening binding is not the exact 59-opening control")
    if measurement.get("ledger_sha256") != ledger_hash:
        raise ValueError("measurement ledger identity mismatch")
    if measurement.get("source_event_sha256") != source_protocol.get("reconstructed_event_sha256"):
        raise ValueError("measurement causal evidence identity mismatch")
    if measurement.get("attempts") != len(attempts) or measurement.get("errors") != []:
        raise ValueError("attempt evidence is incomplete or measurement has errors")
    if sum(row.get("operation") == "OPEN_MARKET" for row in attempts) != 59:
        raise ValueError("attempt evidence does not contain exactly 59 opening requests")
    if ledger.get("positions") != 59 or len(ledger.get("signals", [])) != 18:
        raise ValueError("observed ledger is not the reconciled 59-position, 18-signal cohort")
    if any(not row.get("reconciled_ok") for row in ledger["signals"]):
        raise ValueError("observed ledger contains an unreconciled signal")
    positions = [position for row in ledger["signals"] for position in row.get("positions", [])]
    if len(positions) != 59 or any(
        not position.get("position_id")
        or not position.get("opened_at_precise")
        or not position.get("closed_at_precise")
        or not position.get("open_deal")
        or not position.get("close_deal")
        for position in positions
    ):
        raise ValueError("observed ledger position/deal reconciliation is incomplete")
    causal_proof = verify_attempt_evidence(
        event_prefix,
        event_delta,
        attempts,
        source_protocol.get("reconstructed_event_sha256"),
    )
    return {
        "ledger": ledger,
        "attempts": attempts,
        "paths": {
            "analysis_manifest": manifest_path,
            "observed_ledgers": ledger_path,
            "native_open_binding": binding_path,
            "measurement": measurement_path,
            "attempt_evidence": attempts_path,
            "event_prefix": event_prefix,
            "event_delta": event_delta,
        },
        "hashes": {
            "analysis_manifest_sha256": manifest_hash,
            "observed_ledgers_sha256": ledger_hash,
            "native_open_binding_sha256": binding_hash,
            "measurement_sha256": measurement_hash,
            "attempt_evidence_sha256": attempts_hash,
            "event_prefix_sha256": causal_proof["event_prefix_sha256"],
            "event_delta_sha256": causal_proof["event_delta_gzip_sha256"],
            "reconstructed_event_sha256": causal_proof["reconstructed_event_sha256"],
            "reconstructed_event_bytes": causal_proof["reconstructed_event_bytes"],
            "causal_attempt_rows_verified": causal_proof["causal_attempt_rows_verified"],
        },
    }


def _same_number(left, right):
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except Exception:
        return False


def _slot(signal_id, comment):
    match = _COMMENT.fullmatch(str(comment or ""))
    expected = signal_id.removeprefix("canal2_")
    if not match or match.group(1) != expected:
        raise ValueError(f"opening comment does not bind to {signal_id}: {comment}")
    return 0 if match.group(2) is None else int(match.group(2))


def bind_actual_openings(observed, attempts, directions):
    """Bind successful OPEN results to native entry deals and discard outcomes."""
    ledgers = {row.get("sig_id"): row for row in observed.get("signals", [])}
    if len(ledgers) != len(observed.get("signals", [])):
        raise ValueError("duplicate observed signal identity")
    openings = [row for row in attempts if row.get("operation") == "OPEN_MARKET"]
    by_identity = {}
    for row in openings:
        result = row.get("result") or {}
        key = (str(row.get("sig")), str(result.get("order")), str(result.get("deal")))
        if key in by_identity:
            raise ValueError(f"duplicate opening result identity: {key}")
        by_identity[key] = row
    output = []
    for signal_id, direction in directions.items():
        ledger = ledgers.get(signal_id)
        if ledger is None or ledger.get("channel") != "canal2" or ledger.get("direction") != direction:
            raise ValueError(f"Gold ledger identity/direction missing: {signal_id}")
        positions = sorted(ledger.get("positions", []), key=lambda row: utc(row["opened_at_precise"]))
        if ledger.get("n_positions") != len(positions) or not positions:
            raise ValueError(f"opening count mismatch: {signal_id}")
        bound = []
        slots = []
        for position in positions:
            deal = position.get("open_deal") or {}
            position_id = str(position.get("position_id"))
            key = (signal_id, position_id, str(deal.get("ticket")))
            attempt = by_identity.get(key)
            if attempt is None:
                raise ValueError(f"opening result not bound to native deal: {key}")
            request, result = attempt.get("request") or {}, attempt.get("result") or {}
            slot = _slot(signal_id, request.get("comment"))
            expected_type = 0 if direction == "BUY" else 1
            checks = (
                attempt.get("broker_request_sent") is True,
                result.get("retcode") == 10009,
                str(result.get("order")) == position_id == str(deal.get("order")) == str(deal.get("position_id")),
                str(result.get("deal")) == str(deal.get("ticket")),
                request.get("comment") == deal.get("comment"),
                request.get("symbol") == position.get("symbol") == deal.get("symbol") == "XAUUSD",
                position.get("direction") == direction,
                request.get("type") == deal.get("type") == expected_type,
                deal.get("entry") == 0,
                _same_number(result.get("price"), position.get("open_price")),
                _same_number(result.get("price"), deal.get("price")),
                _same_number(result.get("volume"), position.get("volume")),
                _same_number(result.get("volume"), deal.get("volume")),
            )
            if not all(checks):
                raise ValueError(f"opening result contradicts native entry: {key}")
            initial_sl = request.get("sl")
            initial_tp = request.get("tp")
            initial_sl = None if initial_sl in (None, 0, 0.0) else float(initial_sl)
            initial_tp = None if initial_tp in (None, 0, 0.0) else float(initial_tp)
            bound.append({
                "slot": slot,
                "role": "root" if slot == 0 else f"B{slot}",
                "ticket": position_id,
                "position_id": position_id,
                "open_deal": str(deal.get("ticket")),
                "opening_attempt_id": str(attempt.get("attempt_id")),
                "comment": str(request.get("comment")),
                "symbol": "XAUUSD",
                "direction": direction,
                "opened_at": utc(position["opened_at_precise"]).isoformat(),
                "open_price": float(position["open_price"]),
                "volume": float(position["volume"]),
                "initial_sl": initial_sl,
                "initial_tp": initial_tp,
                "initial_protection_source": "observed_successful_open_request",
            })
            slots.append(slot)
        if slots != list(range(len(slots))):
            raise ValueError(f"opening slots are not chronological root/B1..Bn: {signal_id}")
        output.append({"signal_id": signal_id, "direction": direction, "openings": bound})
    return {"contract": "sanitized_actual_openings_v1", "signals": output}


def _conditioned_genome(original, openings):
    volumes = tuple(row["volume"] for row in openings)
    if len(volumes) > len(original.target_steps):
        raise ValueError("actual Gold leg count exceeds fixed target steps")
    return original.with_change(
        entry_mode="actual_mt5",
        entry_value=None,
        entry_confirmation_value=None,
        entry_ladder_mode="simultaneous",
        entry_ladder_step=None,
        leg_count=len(volumes),
        volume_weights=volumes,
        target_steps=original.target_steps[:len(volumes)],
    )


def _validate_source_study(source_study):
    source_study = _workspace_path(source_study)
    protocol, protocol_hash = read_frozen(source_study / "protocol.json")
    messages, messages_hash = read_frozen(source_study / "raw_messages.json")
    diagnostics, diagnostics_hash = read_frozen(source_study / "input_diagnostics.json")
    if protocol.get("contract") != "raw_message_control_diagnostic_v2":
        raise ValueError("unsupported causal source protocol")
    if protocol.get("raw_messages_sha256") != messages_hash or protocol.get("input_diagnostics_sha256") != diagnostics_hash:
        raise ValueError("causal source study identity mismatch")
    for proof in protocol.get("tapes", {}).values():
        path = _workspace_path(proof["path"])
        if digest(path) != proof.get("sha256"):
            raise ValueError("quote tape identity mismatch")
    signals, parser_diagnostics = compile_signals(
        messages,
        start=utc(protocol["start_utc"]),
        cutoff=utc(protocol["cutoff_utc"]),
        sticker_directions=protocol["sticker_directions"],
    )
    if [row.signal_id for row in signals] != protocol.get("expected_signal_ids"):
        raise ValueError("causal signal cohort changed")
    if [row.get("signal_id") for row in diagnostics.get("signals", [])] != [row.signal_id for row in signals]:
        raise ValueError("causal diagnostics signal cohort changed")
    return protocol, protocol_hash, messages, messages_hash, signals, parser_diagnostics, diagnostics_hash


def prepare(args):
    study = _workspace_path(args.study)
    if study.exists():
        raise ValueError("study already exists; outputs are immutable")
    (source_protocol, source_protocol_hash, messages, source_messages_hash,
     signals, parser_diagnostics, source_diagnostics_hash) = _validate_source_study(args.source_study)
    selected = tuple(row for row in signals if row.channel == "canal2")
    excluded = tuple(row for row in signals if row.channel != "canal2")
    if len(selected) != MAX_SIGNALS or len(excluded) != 7:
        raise ValueError("expected exactly 11 Gold controls and 7 explicitly excluded Dubai signals")
    from research.gold_iterative.contracts import gold_555_genome
    original = gold_555_genome()
    if encode(asdict(original)) != encode(source_protocol.get("genomes", {}).get("canal2")):
        raise ValueError("frozen source Gold genome differs from current Gold 555")
    directions = {row.signal_id: row.direction for row in selected}
    actual_sources = None
    openings = None
    conditioned = {}
    if args.universe == "actual_entries":
        if any(value is None for value in (
            args.analysis_dir, args.measurement_dir, args.event_prefix, args.event_delta
        )):
            raise ValueError("actual_entries preparation requires analysis, measurement, prefix, and delta inputs")
        actual_sources = _load_actual_sources(
            args.analysis_dir, args.measurement_dir, source_protocol, args.event_prefix, args.event_delta
        )
        openings = bind_actual_openings(actual_sources["ledger"], actual_sources["attempts"], directions)
        if sum(len(row["openings"]) for row in openings["signals"]) != 45:
            raise ValueError("conditioned Gold universe does not contain exactly 45 openings")
        for row in openings["signals"]:
            genome = _conditioned_genome(original, row["openings"])
            conditioned[row["signal_id"]] = {"genome": asdict(genome), "fingerprint": genome.fingerprint}
    elif any(value is not None for value in (
        args.analysis_dir, args.measurement_dir, args.event_prefix, args.event_delta
    )):
        raise ValueError("independent preparation must not receive observed or attempt inputs")
    expected_evaluations = len(selected) * len(PROFILE_SPECS) * 3
    if expected_evaluations > MAX_ENGINE_EVALUATIONS:
        raise ValueError("fixed engine evaluation budget exceeded")
    study.mkdir(parents=True, exist_ok=False)
    raw_hash = save(study / "raw_messages.json", messages)
    diagnostics_hash = save(study / "input_diagnostics.json", {
        "selected_signals": [asdict(row) for row in selected],
        "excluded_signals": [{"signal_id": row.signal_id, "channel": row.channel,
                              "reason": "unsupported_protection_profile"} for row in excluded],
        "parser_diagnostics": parser_diagnostics,
        "raw_rows": len(messages),
    })
    openings_hash = save(study / "openings.json", openings) if openings is not None else None
    protocol = {
        "contract": "protection_control_diagnostic_v1",
        "status": "retrospective_diagnostic_only_not_certified_not_oos",
        "universe": args.universe,
        "universe_label": ("independent_own_entries" if args.universe == "independent"
                           else "conditional_on_actual_mt5_openings_not_independent"),
        "search_candidate_budget": 0,
        "mass_search_authorized": False,
        "approved_tolerances": None,
        "max_signals": MAX_SIGNALS,
        "max_engine_evaluations": MAX_ENGINE_EVALUATIONS,
        "expected_engine_evaluations": expected_evaluations,
        "max_wall_seconds": MAX_WALL_SECONDS,
        "execution": EXECUTION,
        "profiles": _profile_payloads(),
        "expected_signal_ids": list(directions),
        "excluded_signals": [{"signal_id": row.signal_id, "channel": row.channel,
                              "reason": "unsupported_protection_profile"} for row in excluded],
        "start_utc": source_protocol["start_utc"],
        "cutoff_utc": source_protocol["cutoff_utc"],
        "sticker_directions": source_protocol["sticker_directions"],
        "tapes": source_protocol["tapes"],
        "broker_epoch_offset_seconds": source_protocol["broker_epoch_offset_seconds"],
        "contract_size": source_protocol["contract_size"],
        "currency_digits": source_protocol["currency_digits"],
        "fx_max_age_ms": source_protocol["fx_max_age_ms"],
        "raw_messages_sha256": raw_hash,
        "input_diagnostics_sha256": diagnostics_hash,
        "openings_sha256": openings_hash,
        "source_study": {
            "protocol_sha256": source_protocol_hash,
            "raw_messages_sha256": source_messages_hash,
            "input_diagnostics_sha256": source_diagnostics_hash,
            "reconstructed_event_sha256": source_protocol["reconstructed_event_sha256"],
        },
        "original_genome": {"genome": asdict(original), "fingerprint": original.fingerprint},
        "conditioned_genomes": conditioned,
        "implementation": identity(),
        "actual_preparation_sources": actual_sources["hashes"] if actual_sources else None,
        "independence": ("no observed ledger, attempt result, live state, or actual exit input"
                         if args.universe == "independent" else
                         "run sees sanitized actual openings only; no actual exits, modifications, retcodes, or live state"),
        "admission_blockers": [
            "profiles_uncalibrated_not_fitted",
            "opening_ack_timing_uncertified",
            "actual_server_processing_quotes_unobserved",
            "actual_tick_tape_has_19_known_holes",
            "nonzero_freeze_level_unsupported",
            "market_close_latency_unmodeled",
            "portfolio_and_margin_unmodeled",
        ],
    }
    protocol_hash = save(study / "protocol.json", protocol)
    print(json.dumps({"study": str(study), "universe": args.universe,
                      "signals": len(selected), "excluded": len(excluded),
                      "maximum_engine_evaluations": expected_evaluations,
                      "protocol_sha256": protocol_hash}), flush=True)


def load_tape(proof, offset):
    path = _workspace_path(proof["path"])
    if digest(path) != proof["sha256"]:
        raise ValueError("quote tape changed after preparation")
    frame = pd.read_parquet(path)
    ns = pd.to_datetime(frame["time_utc"], utc=True).astype("datetime64[ns, UTC]").astype("int64").to_numpy()
    expected = (frame["source_time_msc"].to_numpy(dtype=np.int64) - offset * 1000) * 1_000_000
    if not np.array_equal(ns, expected):
        raise ValueError("quote clock contract mismatch")
    return ns, frame["bid"].to_numpy(), frame["ask"].to_numpy()


def _actual_path(base, opening_row):
    legs = tuple(SignalLeg(
        row["ticket"], row["role"], row["volume"], utc(row["opened_at"]), row["open_price"],
        None, None, None, Decimal(0), (), (),
    ) for row in opening_row["openings"])
    return replace(base, opened_at=min(row.opened_at for row in legs), legs=legs,
                   actual_pnl_eur=None, entry_evidence_kind="actual_mt5")


def engine_mismatch_fields(candidate, oracle):
    candidate = {key: value for key, value in candidate.items() if key != "behavior_digest"}
    oracle = {key: value for key, value in oracle.items() if key != "behavior_digest"}
    return sorted(
        key for key in set(candidate) | set(oracle)
        if key not in candidate or key not in oracle or candidate[key] != oracle[key]
    )


def _load_run_study(study):
    study = _workspace_path(study)
    protocol, protocol_hash = read_frozen(study / "protocol.json")
    messages, messages_hash = read_frozen(study / "raw_messages.json")
    diagnostics, diagnostics_hash = read_frozen(study / "input_diagnostics.json")
    openings = openings_hash = None
    if protocol.get("universe") == "actual_entries":
        openings, openings_hash = read_frozen(study / "openings.json")
    if protocol.get("contract") != "protection_control_diagnostic_v1":
        raise ValueError("unsupported protection control protocol")
    if protocol.get("implementation") != identity():
        raise ValueError("frozen implementation changed; prepare a new study after final code freeze")
    if protocol.get("raw_messages_sha256") != messages_hash or protocol.get("input_diagnostics_sha256") != diagnostics_hash:
        raise ValueError("frozen study input identity mismatch")
    if protocol.get("openings_sha256") != openings_hash:
        raise ValueError("frozen sanitized openings identity mismatch")
    if protocol.get("universe") == "independent":
        if openings is not None or protocol.get("actual_preparation_sources") is not None or protocol.get("conditioned_genomes") != {}:
            raise ValueError("independent protocol contains conditioned MT5 inputs")
    elif protocol.get("universe") == "actual_entries":
        if openings is None or not protocol.get("actual_preparation_sources") or not protocol.get("conditioned_genomes"):
            raise ValueError("actual-entry protocol is missing its sanitized opening contract")
    else:
        raise ValueError("unsupported protection control universe")
    if (protocol.get("search_candidate_budget") != 0 or protocol.get("mass_search_authorized") is not False
            or protocol.get("execution") != EXECUTION or protocol.get("max_wall_seconds") != MAX_WALL_SECONDS
            or protocol.get("max_signals") != MAX_SIGNALS or protocol.get("max_engine_evaluations") != MAX_ENGINE_EVALUATIONS
            or protocol.get("profiles") != _profile_payloads()):
        raise ValueError("fixed protection diagnostic protocol changed")
    return study, protocol, protocol_hash, messages, messages_hash, diagnostics_hash, openings, openings_hash


def run(args):
    from research.dubai_iterative.engine import ExecutionAssumptions, simulate
    from research.dubai_iterative.fast_engine import FastEvaluator
    from research.dubai_iterative.oracle import ExecutionScenario, oracle_simulate
    (study, protocol, protocol_hash, messages, messages_hash, diagnostics_hash,
     openings, openings_hash) = _load_run_study(args.study)
    signals, _ = compile_signals(messages, start=utc(protocol["start_utc"]),
        cutoff=utc(protocol["cutoff_utc"]), sticker_directions=protocol["sticker_directions"])
    selected = tuple(row for row in signals if row.signal_id in protocol["expected_signal_ids"])
    if [row.signal_id for row in selected] != protocol["expected_signal_ids"] or len(selected) > MAX_SIGNALS:
        raise ValueError("frozen Gold cohort changed")
    original = StrategyGenome.from_dict(protocol["original_genome"]["genome"])
    if original.fingerprint != protocol["original_genome"]["fingerprint"]:
        raise ValueError("original genome identity mismatch")
    opening_map = {row["signal_id"]: row for row in (openings or {}).get("signals", [])}
    market, conversion = (load_tape(protocol["tapes"][symbol], protocol["broker_epoch_offset_seconds"])
                          for symbol in ("XAUUSD", "EURUSD"))
    rows, started, evaluations = [], time.monotonic(), 0
    for signal in selected:
        base = make_path(signal, original, market=market, conversion=conversion,
            cutoff=utc(protocol["cutoff_utc"]), contract_size=protocol["contract_size"],
            currency_digits=protocol["currency_digits"], max_fx_age_ms=protocol["fx_max_age_ms"],
            market_sha256=protocol["tapes"]["XAUUSD"]["sha256"],
            conversion_sha256=protocol["tapes"]["EURUSD"]["sha256"])
        genome, path = original, base
        if protocol["universe"] == "actual_entries":
            opening_row = opening_map.get(signal.signal_id)
            if opening_row is None:
                raise ValueError(f"sanitized openings missing: {signal.signal_id}")
            genome = StrategyGenome.from_dict(protocol["conditioned_genomes"][signal.signal_id]["genome"])
            if genome.fingerprint != protocol["conditioned_genomes"][signal.signal_id]["fingerprint"]:
                raise ValueError("conditioned genome identity mismatch")
            path = _actual_path(base, opening_row)
        for spec in PROFILE_SPECS:
            if time.monotonic() - started > MAX_WALL_SECONDS:
                save(study / "blocked_run.json", {"protocol_sha256": protocol_hash,
                    "completed_controls": rows, "blockers": ["fixed_control_wall_budget_exhausted"],
                    "search_candidates": 0, "mass_search_authorized": False})
                raise ValueError("fixed control wall budget exhausted between controls")
            initial = () if openings is None else tuple(InitialProtection(
                row["ticket"], row["initial_sl"], row["initial_tp"], row["initial_protection_source"]
            ) for row in opening_map[signal.signal_id]["openings"])
            profile = _profile(spec, initial)
            scalar = asdict(simulate(path, genome, execution=ExecutionAssumptions(**EXECUTION, protection=profile)))
            fast = asdict(FastEvaluator(execution=ExecutionAssumptions(**EXECUTION, protection=profile))(path, genome))
            oracle = asdict(oracle_simulate(path, genome, execution=ExecutionScenario(
                name=profile.name, **EXECUTION, protection=profile)))
            evaluations += 3
            if evaluations > MAX_ENGINE_EVALUATIONS:
                raise ValueError("fixed engine evaluation budget exceeded")
            mismatches = {"scalar": engine_mismatch_fields(scalar, oracle),
                          "fast": engine_mismatch_fields(fast, oracle)}
            rows.append({"signal_id": signal.signal_id, "profile": profile.name,
                         "genome_fingerprint": genome.fingerprint,
                         "scalar": scalar, "fast": fast, "oracle": oracle,
                         "engine_mismatches": mismatches})
            print(json.dumps({"signal": signal.signal_id, "profile": profile.name,
                              "blockers": scalar["blockers"], "mismatches": mismatches}), flush=True)
    forbidden = sorted(name for name in sys.modules if name in FORBIDDEN_IMPORTS)
    if forbidden or protocol["implementation"] != identity():
        raise ValueError(f"independence or frozen code boundary violated: {forbidden}")
    verify_frozen(study / "protocol.json", protocol_hash)
    verify_frozen(study / "raw_messages.json", messages_hash)
    verify_frozen(study / "input_diagnostics.json", diagnostics_hash)
    if openings_hash:
        verify_frozen(study / "openings.json", openings_hash)
    for proof in protocol["tapes"].values():
        verify_frozen(proof["path"], proof["sha256"])
    result_hash = save(study / "protection_results.json", {
        "protocol_sha256": protocol_hash, "universe": protocol["universe"], "results": rows,
        "engine_evaluations": evaluations, "search_candidates": 0,
        "mass_search_authorized": False, "full_live_parity_verified": False,
        "elapsed_seconds": time.monotonic() - started, "forbidden_imports": forbidden,
    })
    print(json.dumps({"results_sha256": result_hash, "engine_evaluations": evaluations}), flush=True)


def _canonical_time(value):
    return utc(value).isoformat()


def _native_reason(position):
    reason = (position.get("close_deal") or {}).get("reason")
    if reason == 5:
        return "tp"
    if reason == 4:
        return "sl"
    return None


def _simulated_native_reason(reason):
    if reason in {"per_leg_target", "provider_tp", "provider_target_all", "fixed_move_target"}:
        return "tp"
    if reason in {"initial_sl", "fixed_sl", "provider_sl", "provider_sl_move", "trailing_stop", "break_even"}:
        return "sl"
    return None


def _compare_signal(position_rows, simulated, universe):
    observed = sorted(position_rows, key=lambda row: utc(row["opened_at_precise"]))
    entries = list(simulated.get("entries", []))
    exit_rows = list(simulated.get("exits", []))
    entry_ticket_counts = Counter(str(row.get("ticket")) for row in entries)
    exit_ticket_counts = Counter(str(row.get("ticket")) for row in exit_rows)
    blockers = [
        f"duplicate_simulated_entry_ticket:{ticket}"
        for ticket, count in entry_ticket_counts.items() if count > 1
    ] + [
        f"duplicate_simulated_exit_ticket:{ticket}"
        for ticket, count in exit_ticket_counts.items() if count > 1
    ]
    exits = {str(row.get("ticket")): row for row in exit_rows}
    legs, gaps = [], Counter()
    used_entries, used_exits = set(), set()
    by_ticket = {str(row.get("ticket")): (index, row) for index, row in enumerate(entries)}
    for index, position in enumerate(observed):
        position_id = str(position.get("position_id"))
        if universe == "actual_entries":
            pair = by_ticket.get(position_id)
        else:
            pair = (index, entries[index]) if index < len(entries) else None
        if pair is None:
            gaps["missing_simulated_entries"] += 1
            legs.append({"slot": index, "position_id": position_id,
                         "status": "gap", "gaps": ["missing_simulated_entry"]})
            continue
        entry_index, entry = pair
        used_entries.add(entry_index)
        sim_ticket = str(entry.get("ticket"))
        exit_row = exits.get(sim_ticket)
        facts = {
            "entry_price": [position.get("open_price"), entry.get("entry_price")],
            "entry_time": [_canonical_time(position["opened_at_precise"]), _canonical_time(entry["opened_at"])],
            "volume": [position.get("volume"), entry.get("volume")],
        }
        differences = [name for name, pair_values in facts.items()
                       if not (_same_number(*pair_values) if name != "entry_time" else pair_values[0] == pair_values[1])]
        if exit_row is None:
            gaps["missing_simulated_exits"] += 1
        else:
            used_exits.add(sim_ticket)
            native_reason = _native_reason(position)
            if native_reason is None:
                blockers.append(f"observed_exit_not_native_sltp:{position_id}")
            exit_facts = {
                "exit_price": [position.get("close_price"), exit_row.get("exit_price")],
                "exit_time": [_canonical_time(position["closed_at_precise"]), _canonical_time(exit_row["closed_at"])],
                "native_sltp_reason": [native_reason, _simulated_native_reason(exit_row.get("reason"))],
            }
            facts.update(exit_facts)
            differences.extend(name for name, pair_values in exit_facts.items()
                               if not (_same_number(*pair_values) if name == "exit_price" else pair_values[0] == pair_values[1]))
        legs.append({"slot": index, "position_id": position_id, "simulated_ticket": sim_ticket,
                     "status": "different" if differences or exit_row is None else "exact",
                     "facts": facts, "differences": differences,
                     "gaps": ["missing_simulated_exit"] if exit_row is None else []})
    gaps["extra_simulated_entries"] += len(entries) - len(used_entries)
    gaps["extra_simulated_exits"] += len(exit_rows) - len(used_exits)
    nonzero_gaps = {name: count for name, count in gaps.items() if count}
    return {"legs": legs, "gaps": nonzero_gaps, "blockers": list(dict.fromkeys(blockers)),
            "exact": not nonzero_gaps and not blockers and all(row["status"] == "exact" for row in legs)}


def _attempt_diagnostics(attempts, signal_id):
    rows = [row for row in attempts if row.get("sig") == signal_id and row.get("operation") == "MODIFY_SLTP"]
    return {"modify_attempts": len(rows),
            "retcodes": dict(sorted(Counter(str((row.get("result") or {}).get("retcode")) for row in rows).items()))}


def _first_2640_invalid_stop(attempts):
    ordered = sorted(enumerate(attempts), key=lambda pair: (
        str(pair[1].get("broker_request_started_utc") or pair[1].get("attempt_started_utc") or ""), pair[0]))
    for order_index, (_, row) in enumerate(ordered):
        if row.get("sig") != "canal2_2640" or row.get("operation") != "MODIFY_SLTP" or (row.get("result") or {}).get("retcode") != 10016:
            continue
        before, request = row.get("position_before") or {}, row.get("request") or {}
        ticket = str(row.get("ticket") or request.get("position"))
        next_row = next((candidate for _, candidate in ordered[order_index + 1:]
                         if str(candidate.get("ticket") or (candidate.get("request") or {}).get("position")) == ticket
                         and candidate.get("position_before")), None)
        next_before = (next_row or {}).get("position_before") or {}
        old_levels = {"sl": before.get("sl"), "tp": before.get("tp")}
        next_levels = {"sl": next_before.get("sl"), "tp": next_before.get("tp")}
        return {
            "attempt_id": row.get("attempt_id"),
            "ticket": ticket,
            "request_utc": row.get("broker_request_started_utc") or row.get("attempt_started_utc"),
            "old_levels_before": old_levels,
            "requested_levels": {"sl": request.get("sl"), "tp": request.get("tp")},
            "source_quote": {key: (row.get("source_tick") or {}).get(key) for key in ("time_msc", "bid", "ask")},
            "next_position_before": ({"attempt_id": next_row.get("attempt_id"),
                                      "request_utc": next_row.get("broker_request_started_utc") or next_row.get("attempt_started_utc"),
                                      **next_levels} if next_row else None),
            "old_levels_retained_on_next_attempt": next_row is not None and old_levels == next_levels,
            "interpretation": "diagnostic_observation_only_not_server_processing_time",
        }
    return None


def build_comparison_report(protocol, results, observed, attempts):
    actual = {row.get("sig_id"): row for row in observed.get("signals", [])}
    if len(actual) != len(observed.get("signals", [])):
        raise ValueError("duplicate observed signal identity")
    result_map = {}
    for row in results.get("results", []):
        key = (row.get("signal_id"), row.get("profile"))
        if key in result_map:
            raise ValueError(f"duplicate protection control: {key}")
        result_map[key] = row
    profiles = [row["name"] for row in protocol["profiles"]]
    rows, missing = [], []
    for signal_id in protocol["expected_signal_ids"]:
        for profile in profiles:
            control = result_map.get((signal_id, profile))
            ledger = actual.get(signal_id)
            row = {"signal_id": signal_id, "profile": profile,
                   "actual_attempts": _attempt_diagnostics(attempts, signal_id)}
            blockers = []
            if control is None:
                missing.append({"signal_id": signal_id, "profile": profile})
                blockers.append("missing_control_result")
                comparison = {"legs": [], "gaps": {}, "blockers": [], "exact": False}
            elif ledger is None:
                blockers.append("observed_signal_missing")
                comparison = {"legs": [], "gaps": {}, "blockers": [], "exact": False}
            else:
                comparison = _compare_signal(ledger.get("positions", []), control["scalar"], protocol["universe"])
                if any(control.get("engine_mismatches", {}).values()):
                    blockers.append("independent_engine_disagreement")
                blockers.extend(control["scalar"].get("blockers", []))
                blockers.extend(comparison["blockers"])
                row["engine_mismatches"] = control.get("engine_mismatches", {})
                row["hypothesis_trace_counts"] = dict(sorted(Counter(
                    event.get("kind") for event in control["scalar"].get("protection_events", [])
                ).items()))
            row["comparison"] = comparison
            row["blockers"] = list(dict.fromkeys(blockers))
            row["status"] = ("blocked" if row["blockers"] else
                             "exact" if comparison["exact"] else "different")
            rows.append(row)
    unexpected = [{"signal_id": key[0], "profile": key[1]} for key in result_map
                  if key[0] not in protocol["expected_signal_ids"] or key[1] not in profiles]
    return {
        "status": "diagnostic_only",
        "full_live_parity_verified": False,
        "search_candidates": 0,
        "mass_search_authorized": False,
        "universe": protocol["universe"],
        "denominator": {"signals": len(protocol["expected_signal_ids"]),
                        "profiles": len(profiles),
                        "controls": len(protocol["expected_signal_ids"]) * len(profiles)},
        "rows": rows,
        "statuses": dict(sorted(Counter(row["status"] for row in rows).items())),
        "missing_controls": missing,
        "unexpected_controls": unexpected,
        "canal2_2640_first_10016": _first_2640_invalid_stop(attempts),
        "limitations": [
            "actual retcodes are diagnostics and never steer an independent simulation",
            "request/response timing does not identify server processing time",
            "opening acknowledgement timing remains uncertified",
            "19 known live quote holes remain in the archived tape; server processing quotes are unobserved",
            "nonzero freeze levels, market-close latency, portfolio, and margin remain uncertified",
        ],
    }


def compare(args):
    study = _workspace_path(args.study)
    protocol, protocol_hash = read_frozen(study / "protocol.json")
    results, results_hash = read_frozen(study / "protection_results.json")
    diagnostics, diagnostics_hash = read_frozen(study / "input_diagnostics.json")
    if protocol.get("input_diagnostics_sha256") != diagnostics_hash or results.get("protocol_sha256") != protocol_hash:
        raise ValueError("frozen comparison input identity mismatch")
    if protocol.get("implementation") != identity():
        raise ValueError("frozen implementation changed; compare the final frozen run with its code")
    actual = _load_actual_sources(
        args.analysis_dir,
        args.measurement_dir,
        protocol["source_study"],
        args.event_prefix,
        args.event_delta,
    )
    report = build_comparison_report(protocol, results, actual["ledger"], actual["attempts"])
    report["inputs"] = {"protocol_sha256": protocol_hash, "results_sha256": results_hash,
                        "input_diagnostics_sha256": diagnostics_hash, **actual["hashes"]}
    report["comparator_identity"] = identity()
    verify_frozen(study / "protocol.json", protocol_hash)
    verify_frozen(study / "protection_results.json", results_hash)
    verify_frozen(study / "input_diagnostics.json", diagnostics_hash)
    for name, path in actual["paths"].items():
        verify_frozen(path, actual["hashes"][f"{name}_sha256"])
    output_hash = save(study / "comparison.json", report)
    print(json.dumps({"statuses": report["statuses"], "denominator": report["denominator"],
                      "comparison_sha256": output_hash}), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    prep = modes.add_parser("prepare")
    prep.add_argument("--source-study", type=Path, required=True)
    prep.add_argument("--study", type=Path, required=True)
    prep.add_argument("--universe", choices=("independent", "actual_entries"), required=True)
    prep.add_argument("--analysis-dir", type=Path)
    prep.add_argument("--measurement-dir", type=Path)
    prep.add_argument("--event-prefix", type=Path)
    prep.add_argument("--event-delta", type=Path)
    execute = modes.add_parser("run")
    execute.add_argument("--study", type=Path, required=True)
    comparison = modes.add_parser("compare")
    comparison.add_argument("--study", type=Path, required=True)
    comparison.add_argument("--analysis-dir", type=Path, required=True)
    comparison.add_argument("--measurement-dir", type=Path, required=True)
    comparison.add_argument("--event-prefix", type=Path, required=True)
    comparison.add_argument("--event-delta", type=Path, required=True)
    args = parser.parse_args(argv)
    {"prepare": prepare, "run": run, "compare": compare}[args.mode](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
