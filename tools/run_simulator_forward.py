"""Build and run the frozen source-only simulator forward cohort.

The producer projects collector events onto RAW_FIELDS and accepts only frozen
messages, quote tapes, clock evidence and symbol metadata. It never reads MT5
outcomes. Execution is delegated to the preregistered three-engine control and
the scalar records are then joined on the same tapes for bounded portfolio risk.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import tempfile
import time

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.causal_replay import RAW_FIELDS, compile_signals, make_path, time_ns
from research.dubai_iterative.portfolio import reconstruct_portfolio
from tools import prepare_simulator_forward as forward
from tools.prepare_simulator_forward import utc
from tools import run_protection_controls as protection


SOURCE_NAME = "tools/run_simulator_forward.py"
COLLECTOR_SOURCE = (
    "runtime_data/causal_capture_today_20260909/operations/collect_morning_window.py"
)
DATASET_CONTRACT = "simulator_forward_dataset_v1"
CAPTURE_CONTRACT = "morning_causal_capture_v1"
MAX_EVIDENCE_LAG_SECONDS = 120
TIME_PRECISION_SECONDS = 1
TAPE_FIELDS = ("time_utc", "source_time_msc", "bid", "ask")
NATIVE_SYMBOL_FIELDS = (
    "volume_min",
    "volume_max",
    "volume_step",
    "point",
    "digits",
    "trade_stops_level",
    "trade_freeze_level",
    "trade_contract_size",
    "currency_base",
    "currency_profit",
    "currency_margin",
    "filling_mode",
    "order_mode",
)


def _now():
    return forward._now()


def _path(value):
    resolved = Path(value).resolve()
    if not resolved.is_relative_to(ROOT.resolve()):
        raise ValueError("forward input and output must remain inside the workspace")
    return resolved


def _fresh_evidence(path, contract, cutoff, now, watched, *, start):
    value, sha = forward._read(path, watched)
    if value.get("contract") != contract:
        raise ValueError(f"unsupported {contract} input")
    captured = utc(value["captured_at_utc"])
    if (
        captured < start
        or captured > cutoff + pd.Timedelta(seconds=MAX_EVIDENCE_LAG_SECONDS)
        or captured > now + pd.Timedelta(seconds=TIME_PRECISION_SECONDS)
    ):
        label = "metadata" if "metadata" in contract else "clock"
        raise ValueError(f"{label} evidence outside forward window")
    return value, sha


def _validate_clock(value):
    base_fields = {
        "contract",
        "captured_at_utc",
        "clock_domain",
        "broker_epoch_offset_seconds",
        "monotonic_order_verified",
    }
    capture_fields = {
        "event_cutoff_utc",
        "maximum_capture_lag_seconds",
        "samples",
        "source_capture_sha256",
    }
    if (
        set(value) not in (base_fields, base_fields | capture_fields)
        or value.get("clock_domain") != "UTC"
        or value.get("broker_epoch_offset_seconds")
        != forward.DATA_RULES["broker_epoch_offset_seconds"]
        or value.get("monotonic_order_verified") is not True
    ):
        raise ValueError("forward clock evidence is incomplete or inconsistent")


def _validate_metadata(value, execution):
    symbols = value.get("symbols", {})
    xau = symbols.get("XAUUSD", {})
    eurusd = symbols.get("EURUSD", {})
    protection_profile = execution["protection"]
    market_profile = execution["market"]
    expected_xau = {
        "point": protection_profile["point"],
        "digits": protection_profile["digits"],
        "contract_size": forward.DATA_RULES["contract_size"],
        "stops_level_points": protection_profile["stops_level_points"],
        "freeze_level_points": protection_profile["freeze_level_points"],
        "volume_min": market_profile["volume_min"],
        "volume_max": market_profile["volume_max"],
        "volume_step": market_profile["volume_step"],
    }
    base_fields = {
            "contract",
            "captured_at_utc",
            "account_currency",
            "symbols",
        }
    capture_fields = {
        "event_cutoff_utc",
        "source_capture_sha256",
        "source_symbol_metadata_sha256",
    }
    if (
        set(value) not in (base_fields, base_fields | capture_fields)
        or set(symbols) != {"XAUUSD", "EURUSD"}
        or set(xau) != set(expected_xau)
        or set(eurusd) != {"point", "digits"}
        or value.get("account_currency") != forward.DATA_RULES["account_currency"]
        or any(xau.get(key) != expected for key, expected in expected_xau.items())
        or eurusd.get("point") != 0.00001
        or eurusd.get("digits") != 5
    ):
        raise ValueError("forward symbol metadata does not match the frozen profile")


def _raw_messages(events, cutoff, watched, *, start, end):
    path = _path(events)
    if path.stat().st_size > forward.BUDGET["max_json_bytes"]:
        raise ValueError("bounded collector event input exceeded")
    source_sha = protection.digest(path)
    watched[path] = source_sha
    messages = []
    with path.open("r", encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid collector JSON at line {line_number}") from exc
            if not isinstance(row, dict) or row.get("ev") != "telegram_raw":
                continue
            projected = {name: row.get(name) for name in RAW_FIELDS}
            observed = utc(projected["ts"])
            if start <= observed <= cutoff and observed < end:
                messages.append(projected)
                if len(messages) > forward.BUDGET["max_raw_messages"]:
                    raise ValueError("raw message budget exceeded")
    messages.sort(key=lambda row: utc(row["ts"]))
    return messages, {"path": str(path), "sha256": source_sha}


def _manifest_file(manifest, directory, name, watched):
    proof = manifest.get("files", {}).get(name)
    if (
        not isinstance(proof, dict)
        or set(proof) != {"bytes", "sha256"}
        or type(proof.get("bytes")) is not int
        or proof["bytes"] < 0
        or not isinstance(proof.get("sha256"), str)
    ):
        raise ValueError(f"capture file proof missing or invalid: {name}")
    path = _path(directory / name)
    if path.stat().st_size != proof["bytes"]:
        raise ValueError(f"capture file size mismatch: {name}")
    actual = protection.digest(path)
    if actual != proof["sha256"]:
        raise ValueError(f"capture file hash mismatch: {name}")
    if path in watched and watched[path] != actual:
        raise ValueError(f"frozen input changed during execution: {name}")
    watched[path] = actual
    return path, actual


def _captured_messages(path, expected_sha, cutoff, watched, *, start, end):
    rows, actual = forward._read(path, watched)
    if actual != expected_sha or not isinstance(rows, list):
        raise ValueError("captured raw message identity or shape mismatch")
    messages = []
    for index, row in enumerate(rows):
        if (
            not isinstance(row, dict)
            or set(row) != set(RAW_FIELDS)
            or row.get("ev") != "telegram_raw"
        ):
            raise ValueError(f"captured raw message is invalid: row {index + 1}")
        projected = {name: row.get(name) for name in RAW_FIELDS}
        observed = utc(projected["ts"])
        if start <= observed <= cutoff and observed < end:
            messages.append(projected)
            if len(messages) > forward.BUDGET["max_raw_messages"]:
                raise ValueError("raw message budget exceeded")
    messages.sort(key=lambda row: utc(row["ts"]))
    return messages, len(rows)


def _evidence_time(value, label, cutoff, now, *, floor=None):
    stamp = utc(value)
    lower = cutoff if floor is None else floor
    if (
        stamp < lower
        or stamp > cutoff + pd.Timedelta(seconds=MAX_EVIDENCE_LAG_SECONDS)
        or stamp > now + pd.Timedelta(seconds=TIME_PRECISION_SECONDS)
    ):
        raise ValueError(f"{label} outside capture evidence boundary")
    return stamp


def _capture_inputs(capture, requested_cutoff, now, protocol, watched):
    start, end = forward._protocol_window(protocol)
    capture_path = _path(capture)
    manifest_path = capture_path / "manifest.json" if capture_path.is_dir() else capture_path
    if manifest_path.name != "manifest.json":
        raise ValueError("capture must name a directory or manifest.json")
    manifest, manifest_sha = forward._read(manifest_path, watched)
    capture_contract = protocol["profile_payload"].get("capture_contract")
    required_binding = {
        "collector_sha256",
        "wrapper_sha256",
        "expected_live_commit",
        "expected_account_identity_sha256",
    }
    explicit_source = isinstance(capture_contract, dict) and "collector_source" in capture_contract
    collector_source = capture_contract.get("collector_source") if explicit_source else None
    if explicit_source and (
        not isinstance(collector_source, dict)
        or set(collector_source) != {"path", "sha256"}
        or not isinstance(collector_source.get("path"), str)
        or collector_source.get("sha256") != capture_contract.get("collector_sha256")
    ):
        raise ValueError("explicit collector source must be a frozen path/SHA-256 proof")
    collector_path = _path(collector_source["path"]) if explicit_source else (ROOT / COLLECTOR_SOURCE).resolve()
    frozen_collectors = [
        row
        for row in protocol["profile_payload"].get("additional_sources", [])
        if _path(row["path"]) == collector_path
    ]
    if (
        not isinstance(capture_contract, dict)
        or set(capture_contract) != required_binding | ({"collector_source"} if explicit_source else set())
        or len(frozen_collectors) != 1
        or frozen_collectors[0].get("sha256")
        != capture_contract.get("collector_sha256")
    ):
        raise ValueError("native capture was not bound into the frozen profile")
    for name in required_binding:
        value = capture_contract[name]
        length = 40 if name == "expected_live_commit" else 64
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{%d}" % length, value) is None:
            raise ValueError(f"native capture binding requires a typed hexadecimal identity: {name}")
    if explicit_source:
        import hashlib

        if collector_path.suffix != ".py":
            raise ValueError("explicit collector source must be Python")
        forward._proof(collector_source, watched)
        expected_protocol = {
            "sha256": hashlib.sha256(protection.encode(protocol)).hexdigest(),
            "contract": protocol["contract"], "fingerprint": protocol["fingerprint"],
        }
        if (
            protocol["contract"] != "simulator_forward_protocol_v2"
            or manifest.get("forward_protocol") != expected_protocol
            or manifest.get("collector_source") != collector_source
            or manifest.get("source_hashes_verified_before_after") is not True
            or manifest.get("phase") not in {"checkpoint", "final"}
            or (manifest["phase"] == "checkpoint" and not start <= utc(manifest["event_cutoff_utc"]) < end)
            or (manifest["phase"] == "final" and utc(manifest["event_cutoff_utc"]) < end)
        ):
            raise ValueError("capture differs from its frozen protocol or producer proof")
    live_code = manifest.get("live_code", {})
    account = manifest.get("account", {})
    if not isinstance(live_code, dict) or not isinstance(account, dict):
        raise ValueError("capture live code and account must be identity objects")
    if (
        live_code.get("collector_sha256")
        != capture_contract["collector_sha256"]
        or live_code.get("wrapper_sha256")
        != capture_contract["wrapper_sha256"]
        or live_code.get("commit")
        != capture_contract["expected_live_commit"]
        or live_code.get("clean") is not True
        or account.get("account_identity_sha256")
        != capture_contract["expected_account_identity_sha256"]
    ):
        raise ValueError("capture helper, live commit, or account differs from preregistration")
    source_cutoff = utc(manifest["event_cutoff_utc"])
    cutoff = min(source_cutoff, end)
    blockers = manifest.get("blockers")
    expected_status = "captured" if blockers == [] else "captured_with_blockers"
    if (
        manifest.get("contract") != ("simulator_causal_capture_v2" if explicit_source else CAPTURE_CONTRACT)
        or manifest.get("status") != expected_status
        or not isinstance(blockers, list)
        or any(not isinstance(item, str) or not item for item in blockers)
        or utc(manifest.get("window_start_utc")) != start
        or utc(manifest.get("window_end_exclusive_utc")) != end
        or not start < cutoff <= min(end, now)
        or source_cutoff > cutoff + pd.Timedelta(seconds=MAX_EVIDENCE_LAG_SECONDS)
        or source_cutoff > now + pd.Timedelta(seconds=TIME_PRECISION_SECONDS)
        or manifest.get("read_only") is not True
        or manifest.get("normal_operations_only") is not True
        or manifest.get("orders_sent_by_collector") != 0
    ):
        raise ValueError("capture manifest is not an admissible source-only window")
    if requested_cutoff is not None and utc(requested_cutoff) != cutoff:
        raise ValueError("requested cutoff differs from the bounded capture cutoff")
    completed = _evidence_time(
        manifest["completed_at_utc"], "capture completion", cutoff, now,
        floor=source_cutoff,
    )

    directory = manifest_path.parent
    raw_path, raw_sha = _manifest_file(
        manifest, directory, "raw_messages_window.json", watched
    )
    event_evidence = manifest.get("event_evidence", {})
    if (
        event_evidence.get("raw_messages_file") != raw_path.name
        or event_evidence.get("raw_messages_sha256") != raw_sha
    ):
        raise ValueError("capture raw message proof differs from event evidence")
    messages, raw_rows = _captured_messages(raw_path, raw_sha, cutoff, watched, start=start, end=end)
    availability = event_evidence.get("raw_message_causal_availability")
    if (
        not isinstance(availability, dict)
        or availability.get("window_rows_cumulative") != raw_rows
        or not isinstance(availability.get("missing_fields"), dict)
        or not isinstance(availability.get("revision_conflicts"), list)
        or not isinstance(event_evidence.get("invalid_delta_rows"), list)
    ):
        raise ValueError("capture raw message denominator evidence is incomplete")
    derived_blockers = []
    if event_evidence["invalid_delta_rows"]:
        derived_blockers.append("native_capture_invalid_delta_rows")
    if availability["missing_fields"]:
        derived_blockers.append("native_capture_raw_fields_incomplete")
    if availability["revision_conflicts"]:
        derived_blockers.append("native_capture_revision_conflicts")
    blockers = list(dict.fromkeys([*blockers, *derived_blockers]))

    native_metadata_path, native_metadata_sha = _manifest_file(
        manifest, directory, "symbol_metadata.json", watched
    )
    native_metadata, actual_metadata_sha = forward._read(
        native_metadata_path, watched
    )
    native_contracts = manifest.get("symbol_contract", {})
    if actual_metadata_sha != native_metadata_sha:
        raise ValueError("capture symbol metadata identity mismatch")
    for symbol in ("XAUUSD", "EURUSD"):
        source = native_metadata.get(symbol, {})
        contract = native_contracts.get(symbol, {})
        if (
            not isinstance(source, dict)
            or not isinstance(contract, dict)
            or any(
                field not in source
                or field not in contract
                or source[field] != contract[field]
                for field in NATIVE_SYMBOL_FIELDS
            )
        ):
            raise ValueError(f"capture native symbol contract mismatch: {symbol}")
    if (
        account.get("currency") != forward.DATA_RULES["account_currency"]
        or account.get("terminal_connected") is not True
    ):
        raise ValueError("capture account currency or connection evidence mismatch")

    xau = native_contracts["XAUUSD"]
    eurusd = native_contracts["EURUSD"]
    metadata = {
        "contract": "simulator_forward_metadata_evidence_v1",
        "captured_at_utc": completed,
        "account_currency": account["currency"],
        "symbols": {
            "XAUUSD": {
                "point": xau["point"],
                "digits": xau["digits"],
                "contract_size": xau["trade_contract_size"],
                "stops_level_points": xau["trade_stops_level"],
                "freeze_level_points": xau["trade_freeze_level"],
                "volume_min": xau["volume_min"],
                "volume_max": xau["volume_max"],
                "volume_step": xau["volume_step"],
            },
            "EURUSD": {"point": eurusd["point"], "digits": eurusd["digits"]},
        },
        "event_cutoff_utc": source_cutoff,
        "source_capture_sha256": manifest_sha,
        "source_symbol_metadata_sha256": native_metadata_sha,
    }
    _validate_metadata(metadata, protocol["execution"])

    offset = manifest.get("raw_server_epoch_minus_utc_seconds")
    samples = manifest.get("clock_samples")
    if (
        offset != forward.DATA_RULES["broker_epoch_offset_seconds"]
        or not isinstance(samples, list)
        or len(samples) != 6
    ):
        raise ValueError("capture clock anchors are incomplete or inconsistent")
    expected_pairs = {(index, symbol) for index in range(3) for symbol in ("XAUUSD", "EURUSD")}
    actual_pairs = set()
    captured_at = source_cutoff
    previous_observed = None
    for sample in samples:
        if not isinstance(sample, dict):
            raise ValueError("capture clock anchor is not an object")
        observed = _evidence_time(
            sample.get("observed_utc"), "clock anchor", cutoff, now,
            floor=source_cutoff,
        )
        pair = (sample.get("sample"), sample.get("symbol"))
        actual_pairs.add(pair)
        raw_time = sample.get("raw_time_msc")
        residual = sample.get("residual_seconds")
        bid, ask = sample.get("bid"), sample.get("ask")
        if (
            sample.get("offset_seconds") != offset
            or type(raw_time) is not int
            or raw_time <= 0
            or not isinstance(residual, (int, float))
            or not np.isfinite(residual)
            or abs(residual) > 15
            or abs((raw_time / 1000 - observed.timestamp() - offset) - residual) > 0.002
            or not isinstance(bid, (int, float))
            or not isinstance(ask, (int, float))
            or not np.isfinite(bid)
            or not np.isfinite(ask)
            or bid <= 0
            or ask < bid
            or (previous_observed is not None and observed < previous_observed)
        ):
            raise ValueError("capture clock anchor is invalid")
        captured_at = max(captured_at, observed)
        previous_observed = observed
    if actual_pairs != expected_pairs:
        raise ValueError("capture clock anchors do not cover both symbols three times")
    clock = {
        "contract": "simulator_forward_clock_evidence_v1",
        "captured_at_utc": captured_at,
        "clock_domain": "UTC",
        "broker_epoch_offset_seconds": offset,
        "monotonic_order_verified": True,
        "event_cutoff_utc": source_cutoff,
        "maximum_capture_lag_seconds": MAX_EVIDENCE_LAG_SECONDS,
        "samples": samples,
        "source_capture_sha256": manifest_sha,
    }
    _validate_clock(clock)

    tapes = {}
    tick_evidence = manifest.get("tick_evidence", {})
    for symbol in ("XAUUSD", "EURUSD"):
        path, sha = _manifest_file(
            manifest, directory, f"{symbol}.parquet", watched
        )
        tick = tick_evidence.get(symbol, {})
        if (
            tick.get("status") != "captured"
            or tick.get("parquet_sha256") != sha
            or tick.get("rows") != pq.read_metadata(path).num_rows
            or utc(tick.get("window_end_exclusive_utc")) != cutoff
        ):
            raise ValueError(f"capture tick evidence mismatch: {symbol}")
        tapes[symbol] = path

    return {
        "cutoff": cutoff,
        "messages": messages,
        "event_proof": {"path": str(raw_path), "sha256": raw_sha},
        "clock": clock,
        "clock_proof": {"path": str(manifest_path), "sha256": manifest_sha},
        "metadata": metadata,
        "metadata_proof": {
            "path": str(native_metadata_path),
            "sha256": native_metadata_sha,
        },
        "tapes": tapes,
        "allow_extra_columns": True,
        "capture": {
            "path": str(manifest_path),
            "sha256": manifest_sha,
            "status": manifest["status"],
            "blockers": list(blockers),
            "event_cutoff_utc": source_cutoff,
            "completed_at_utc": completed,
        },
    }


def _validate_tape(path, symbol, cutoff, watched, *, start, allow_extra_columns=False):
    path = _path(path)
    sha = protection.digest(path)
    if path in watched and watched[path] != sha:
        raise ValueError(f"frozen input changed during execution: {path.name}")
    watched[path] = sha
    metadata = pq.read_metadata(path)
    columns = set(metadata.schema.names)
    if not set(TAPE_FIELDS).issubset(columns) or (
        not allow_extra_columns and columns != set(TAPE_FIELDS)
    ):
        raise ValueError(f"{symbol} input is not a source-only quote tape")
    if metadata.num_rows > forward.BUDGET["max_tape_rows"]:
        raise ValueError(f"{symbol} quote tape exceeds row budget")
    proof = {"path": str(path), "sha256": sha}
    times, bid, ask = protection.load_tape(
        proof, forward.DATA_RULES["broker_epoch_offset_seconds"]
    )
    if (
        not len(times)
        or np.any(np.diff(times) < 0)
        or not np.isfinite(bid).all()
        or not np.isfinite(ask).all()
        or np.any(bid <= 0)
        or np.any(ask < bid)
    ):
        raise ValueError(f"invalid {symbol} forward Bid/Ask tape")
    lookback_ns = (
        forward.DATA_RULES["fx_max_age_ms"] * 1_000_000
        if symbol == "EURUSD"
        else 0
    )
    if times[0] < time_ns(start) - lookback_ns or times[-1] > time_ns(cutoff):
        raise ValueError(f"{symbol} quote tape outside forward boundary")
    if time_ns(cutoff) - times[-1] > forward.DATA_RULES["fx_max_age_ms"] * 1_000_000:
        raise ValueError(f"{symbol} quote tape tail is incomplete")
    frame = pd.read_parquet(path, columns=list(TAPE_FIELDS))
    return path, frame, {
        "rows": len(times),
        "first_ns": int(times[0]),
        "last_ns": int(times[-1]),
        "source_path": str(path),
        "source_sha256": sha,
    }


def produce(
    protocol_path,
    *,
    events=None,
    market_tape=None,
    conversion_tape=None,
    clock_evidence=None,
    metadata_evidence=None,
    capture=None,
    cutoff=None,
    out,
    now=None,
):
    """Publish an immutable source-only dataset matching forward._dataset."""

    now = utc(_now() if now is None else now)
    started, watched, implementation = time.monotonic(), {}, forward.identity()
    protocol, protocol_sha = forward._frozen_protocol(
        protocol_path, watched, implementation
    )
    start, end = forward._protocol_window(protocol)
    out = _path(out)
    if out.exists():
        raise ValueError("forward dataset directory already exists; never overwrite")
    if not out.parent.exists():
        raise ValueError("forward dataset parent directory does not exist")

    explicit = (
        events,
        market_tape,
        conversion_tape,
        clock_evidence,
        metadata_evidence,
    )
    if capture is not None:
        if any(value is not None for value in explicit):
            raise ValueError("capture and explicit forward inputs are mutually exclusive")
        inputs = _capture_inputs(
            capture, cutoff, now, protocol, watched
        )
        cutoff = inputs["cutoff"]
        messages = inputs["messages"]
        event_proof = inputs["event_proof"]
        clock, clock_source = inputs["clock"], inputs["clock_proof"]
        metadata, metadata_source = (
            inputs["metadata"], inputs["metadata_proof"]
        )
        tape_paths = inputs["tapes"]
        allow_extra_columns = inputs["allow_extra_columns"]
        capture_proof = inputs["capture"]
    else:
        if cutoff is None or any(value is None for value in explicit):
            raise ValueError("explicit production requires all six forward inputs")
        cutoff = utc(cutoff)
        if not start < cutoff <= min(end, now):
            raise ValueError("dataset cutoff outside bounded forward window")
        messages, event_proof = _raw_messages(events, cutoff, watched, start=start, end=end)
        clock, clock_sha = _fresh_evidence(
            clock_evidence,
            "simulator_forward_clock_evidence_v1",
            cutoff,
            now,
            watched,
            start=start,
        )
        metadata, metadata_sha = _fresh_evidence(
            metadata_evidence,
            "simulator_forward_metadata_evidence_v1",
            cutoff,
            now,
            watched,
            start=start,
        )
        clock_source = {
            "path": str(_path(clock_evidence)),
            "sha256": clock_sha,
        }
        metadata_source = {
            "path": str(_path(metadata_evidence)),
            "sha256": metadata_sha,
        }
        tape_paths = {"XAUUSD": market_tape, "EURUSD": conversion_tape}
        allow_extra_columns = False
        capture_proof = None

    signals, diagnostics = compile_signals(
        messages,
        start=start,
        cutoff=cutoff,
        sticker_directions=protocol["sticker_directions"],
    )
    selected = [row.signal_id for row in signals if row.channel == "canal2"]
    if len(selected) > forward.BUDGET["max_signals"]:
        raise ValueError("natural signal count exceeds frozen budget; no truncation")

    _validate_clock(clock)
    _validate_metadata(metadata, protocol["execution"])
    source_tapes = {
        symbol: _validate_tape(
            tape_paths[symbol],
            symbol,
            cutoff,
            watched,
            start=start,
            allow_extra_columns=allow_extra_columns,
        )
        for symbol in ("XAUUSD", "EURUSD")
    }
    producer = forward._runner(protocol)
    forward._finish(watched, implementation, started)

    with tempfile.TemporaryDirectory(prefix="forward-dataset-", dir=out.parent) as temporary:
        stage = Path(temporary) / "dataset"
        stage.mkdir()
        messages_sha = protection.save(stage / "raw_messages.json", messages)
        diagnostics_sha = protection.save(
            stage / "input_diagnostics.json", list(diagnostics)
        )
        retained_clock_sha = protection.save(stage / "clock_evidence.json", clock)
        retained_metadata_sha = protection.save(
            stage / "metadata_evidence.json", metadata
        )
        tapes = {}
        quote_proofs = {}
        for symbol, (source, frame, details) in source_tapes.items():
            retained = stage / f"{symbol}.parquet"
            frame.to_parquet(retained, index=False, compression="zstd")
            retained_sha = protection.digest(retained)
            tapes[symbol] = {"path": str(out / retained.name), "sha256": retained_sha}
            quote_proofs[symbol] = details | {
                "path": str(out / retained.name),
                "sha256": retained_sha,
            }
        evidence = {
            "collector_events": event_proof,
            "clock": {
                "source_path": clock_source["path"],
                "source_sha256": clock_source["sha256"],
                "path": str(out / "clock_evidence.json"),
                "sha256": retained_clock_sha,
            },
            "metadata": {
                "source_path": metadata_source["path"],
                "source_sha256": metadata_source["sha256"],
                "path": str(out / "metadata_evidence.json"),
                "sha256": retained_metadata_sha,
            },
            "quotes": quote_proofs,
        }
        if capture_proof is not None:
            evidence["capture_manifest"] = capture_proof
            evidence["raw_messages_source"] = event_proof
        payload = {
            "contract": DATASET_CONTRACT,
            "forward_protocol_sha256": protocol_sha,
            "universe": "independent",
            "search_candidate_budget": 0,
            "mass_search_authorized": False,
            "execution": protocol["execution"],
            "genome_fingerprint": protocol["genome_fingerprint"],
            "producer": producer,
            "start_utc": start,
            "cutoff_utc": cutoff,
            "contract_size": forward.DATA_RULES["contract_size"],
            "currency_digits": forward.DATA_RULES["currency_digits"],
            "fx_max_age_ms": forward.DATA_RULES["fx_max_age_ms"],
            "broker_epoch_offset_seconds": forward.DATA_RULES[
                "broker_epoch_offset_seconds"
            ],
            "raw_messages_sha256": messages_sha,
            "input_diagnostics_sha256": diagnostics_sha,
            "expected_signal_ids": selected,
            "tapes": tapes,
            "source_evidence": evidence,
            "capture_blockers": (
                None if capture_proof is None else capture_proof["blockers"]
            ),
            "openings_sha256": None,
            "actual_preparation_sources": None,
            "conditioned_genomes": None,
        }
        if len(protection.encode(payload)) > forward.BUDGET["max_json_bytes"]:
            raise ValueError("forward dataset protocol exceeds JSON budget")
        dataset_sha = protection.save(stage / "protocol.json", payload)
        forward._finish(watched, implementation, started)
        os.replace(stage, out)

    protection.verify_frozen(out / "protocol.json", dataset_sha)
    protection.verify_frozen(out / "raw_messages.json", messages_sha)
    protection.verify_frozen(out / "input_diagnostics.json", diagnostics_sha)
    protection.verify_frozen(out / "clock_evidence.json", retained_clock_sha)
    protection.verify_frozen(out / "metadata_evidence.json", retained_metadata_sha)
    for proof in tapes.values():
        protection.verify_frozen(proof["path"], proof["sha256"])
    forward._finish(watched, implementation, started)

    return {
        "status": "dataset_ready",
        "dataset": str(out),
        "protocol_sha256": dataset_sha,
        "eligible_signal_ids": selected,
        "denominator": len(selected),
        "parser_diagnostics": len(diagnostics),
        "cutoff_utc": cutoff,
        "capture_blockers": (
            None if capture_proof is None else capture_proof["blockers"]
        ),
        "mass_search_authorized": False,
        "search_candidates": 0,
    }


def _paths(protocol, dataset, included):
    start, _ = forward._protocol_window(protocol)
    messages = protection.read(dataset["messages_path"])
    signals, _ = compile_signals(
        messages,
        start=start,
        cutoff=utc(dataset["cutoff_utc"]),
        sticker_directions=protocol["sticker_directions"],
    )
    signals = [row for row in signals if row.channel == "canal2"]
    by_id = {row.signal_id: row for row in signals}
    if len(by_id) != len(signals) or any(signal_id not in by_id for signal_id in included):
        raise ValueError("portfolio signal denominator differs from engine run")
    from research.dubai_iterative.contracts import StrategyGenome

    genome = StrategyGenome.from_dict(protocol["genome"])
    market, conversion = (
        protection.load_tape(
            dataset["tapes"][symbol],
            forward.DATA_RULES["broker_epoch_offset_seconds"],
        )
        for symbol in ("XAUUSD", "EURUSD")
    )
    paths = []
    prepared_ids = []
    failures = {}
    for signal_id in included:
        try:
            path = make_path(
                by_id[signal_id],
                genome,
                market=market,
                conversion=conversion,
                cutoff=utc(dataset["cutoff_utc"]),
                contract_size=forward.DATA_RULES["contract_size"],
                currency_digits=forward.DATA_RULES["currency_digits"],
                max_fx_age_ms=forward.DATA_RULES["fx_max_age_ms"],
                market_sha256=dataset["tapes"]["XAUUSD"]["sha256"],
                conversion_sha256=dataset["tapes"]["EURUSD"]["sha256"],
            )
        except ValueError as exc:
            failures[signal_id] = (str(exc),)
            continue
        paths.append(path)
        prepared_ids.append(signal_id)
    return tuple(paths), prepared_ids, failures


def _result_object(payload):
    entries = tuple(
        SimpleNamespace(**(row | {"opened_at": utc(row["opened_at"])}))
        for row in payload["entries"]
    )
    exits = tuple(
        SimpleNamespace(
            **(
                row
                | {
                    "closed_at": utc(row["closed_at"]),
                    "pnl_eur": (
                        None if row["pnl_eur"] is None else Decimal(str(row["pnl_eur"]))
                    ),
                }
            )
        )
        for row in payload["exits"]
    )
    events = tuple(SimpleNamespace(**row) for row in payload["protection_events"])
    return SimpleNamespace(
        **(
            payload
            | {
                "entries": entries,
                "exits": exits,
                "protection_events": events,
                "pnl_eur": (
                    None
                    if payload["pnl_eur"] is None
                    else Decimal(str(payload["pnl_eur"]))
                ),
            }
        )
    )


def _portfolio(protocol, dataset, selected, result_payload, run_report):
    rows = result_payload["results"]
    if [row["signal_id"] for row in rows] != selected:
        raise ValueError("portfolio results denominator differs from engine run")
    preparation_failures = {
        row["signal_id"]: tuple(row.get("preparation_blockers", ()) or ())
        for row in rows
        if row.get("preparation_blockers")
    }
    candidate_ids = [
        row["signal_id"]
        for row in rows
        if row["signal_id"] not in preparation_failures
    ]
    assessment = None
    blockers = []
    paths = ()
    ready_ids = []
    if candidate_ids:
        paths, ready_ids, local_failures = _paths(protocol, dataset, candidate_ids)
        preparation_failures.update(local_failures)
    ready_by_id = {row["signal_id"]: row for row in rows}
    ready_rows = [ready_by_id[signal_id] for signal_id in ready_ids]
    results = tuple(_result_object(row["scalar"]) for row in ready_rows)
    if ready_rows:
        execution = forward._execution(protocol["execution"], objects=True)
        assessment = reconstruct_portfolio(paths, results, execution=execution)
        blockers.extend(assessment.blockers)
    for signal_id, reasons in preparation_failures.items():
        blockers.extend(
            f"path_preparation_failed:{signal_id}:{reason}" for reason in reasons
        )
    blockers.extend(f"forward:{item}" for item in run_report.get("blockers", []))
    blockers.extend(f"forward_incomplete:{item}" for item in run_report.get("incomplete", []))
    for signal_id, result in zip(ready_ids, results, strict=True):
        if (
            result.blockers
            or result.unfilled
            or result.pnl_eur is None
            or not result.entries
            or not result.exits
        ):
            blockers.append(f"signal_incomplete:{signal_id}")
    blockers = list(dict.fromkeys(blockers))
    verified = bool(assessment is not None and not blockers and assessment.evidence_complete)
    values = asdict(assessment) if assessment is not None else {
        "net_eur": None,
        "peak_equity_eur": None,
        "minimum_equity_eur": None,
        "max_drawdown_eur": None,
        "max_concurrent_volume": None,
        "max_concurrent_signals": None,
        "timeline_points": None,
        "blockers": (),
    }
    if not verified:
        for key in (
            "net_eur",
            "peak_equity_eur",
            "minimum_equity_eur",
            "max_drawdown_eur",
            "max_concurrent_volume",
            "max_concurrent_signals",
            "timeline_points",
        ):
            values[key] = None
    values["blockers"] = blockers
    values["verified"] = verified
    values["verification_scope"] = "frozen_source_only_execution_assumptions"
    values["signal_ids"] = selected
    values["swap_model"] = "no_rollover_admitted"
    values["margin_model"] = "out_of_scope"
    return values


def execute(protocol_path, dataset_path, *, out, now=None):
    """Run all three engines, then reconstruct only verified portfolio values."""

    now = utc(_now() if now is None else now)
    out = _path(out)
    report = forward.run(protocol_path, dataset_path, out=out, now=now)
    if not out.exists():
        return report | {
            "portfolio": {"verified": False, "blockers": ["engine_run_not_published"]}
        }

    started, watched, implementation = time.monotonic(), {}, forward.identity()
    protocol, protocol_sha = forward._frozen_protocol(
        protocol_path, watched, implementation
    )
    dataset, dataset_sha, selected, blockers, incomplete, _ = forward._dataset(
        dataset_path, protocol, protocol_sha, watched, now
    )
    if blockers:
        raise ValueError("dataset changed between engine and portfolio execution")
    result_payload, result_sha = forward._read(
        out / "independent_results.json", watched
    )
    if result_sha != report["results_sha256"]:
        raise ValueError("engine result hash changed before portfolio reconstruction")
    capture_blockers = [
        f"capture:{item}" for item in dataset.get("capture_blockers") or []
    ]
    if capture_blockers:
        report = report | {
            "blockers": list(dict.fromkeys([
                *report.get("blockers", []),
                *capture_blockers,
            ])),
        }
    portfolio = _portfolio(
        protocol,
        dataset,
        selected,
        result_payload,
        report | {"incomplete": list(report.get("incomplete", [])) + incomplete},
    )
    portfolio_payload = {
        "contract": "simulator_forward_portfolio_v1",
        "forward_protocol_sha256": protocol_sha,
        "dataset_protocol_sha256": dataset_sha,
        "independent_results_sha256": result_sha,
        "portfolio": portfolio,
        "mass_search_authorized": False,
        "search_candidates": 0,
    }
    portfolio_sha = protection.save(out / "portfolio.json", portfolio_payload)
    status = report["status"]
    if (portfolio["blockers"] or capture_blockers) and status == "evidence_ready_for_review":
        status = "blocked"
    combined = report | {
        "status": status,
        "portfolio": portfolio,
        "portfolio_path": str(out / "portfolio.json"),
        "portfolio_sha256": portfolio_sha,
    }
    report_sha = protection.save(out / "run_report.json", combined)
    protection.verify_frozen(out / "portfolio.json", portfolio_sha)
    protection.verify_frozen(out / "run_report.json", report_sha)
    forward._finish(watched, implementation, started)
    return combined | {"run_report_sha256": report_sha}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    produce_parser = modes.add_parser("produce")
    produce_parser.add_argument("--protocol", type=Path, required=True)
    produce_parser.add_argument("--capture", type=Path)
    produce_parser.add_argument("--events", type=Path)
    produce_parser.add_argument("--market-tape", type=Path)
    produce_parser.add_argument("--conversion-tape", type=Path)
    produce_parser.add_argument("--clock-evidence", type=Path)
    produce_parser.add_argument("--metadata-evidence", type=Path)
    produce_parser.add_argument("--cutoff")
    produce_parser.add_argument("--out", type=Path, required=True)
    run_parser = modes.add_parser("run")
    run_parser.add_argument("--protocol", type=Path, required=True)
    run_parser.add_argument("--dataset", type=Path, required=True)
    run_parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.mode == "produce":
            result = produce(
                args.protocol,
                events=args.events,
                market_tape=args.market_tape,
                conversion_tape=args.conversion_tape,
                clock_evidence=args.clock_evidence,
                metadata_evidence=args.metadata_evidence,
                capture=args.capture,
                cutoff=args.cutoff,
                out=args.out,
            )
        else:
            result = execute(args.protocol, args.dataset, out=args.out)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        result = {
            "status": "blocked",
            "blockers": [str(exc)],
            "mass_search_authorized": False,
            "search_candidates": 0,
        }
    sys.stdout.write(protection.encode(result).decode())
    sys.stdout.flush()
    return 0 if result["status"] in {"dataset_ready", "evidence_ready_for_review"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
