"""Bounded independent diagnostics; never a strategy search or certification.

Prepare strips runtime outcomes. Run reads only that frozen message file and
the two quote tapes. Compare is a separate invocation allowed to read ledgers.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
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

from research.causal_replay import compile_signals, make_path, raw_message, utc
from research.dubai_iterative.contracts import StrategyGenome
from research.iterative_provenance import implementation_identity


EXTRA_SOURCES = (
    "tools/run_causal_controls.py", "research/causal_replay.py", "parser.py",
    "provider_signal_catalog.py", "interpretation_firewall.py",
)
STICKERS = {"6255969549976339155": "BUY", "6256057072819896994": "SELL"}
MAX_SIGNALS = 40
LATENCIES = (0, 250, 1000)
SCENARIOS = tuple(
    {"latency_ms": observation, "entry_fill_latency_ms": fill}
    for observation, fill in ((0, 0), (250, 0), (1000, 0), (0, 250), (0, 1000))
)
MAX_WALL_SECONDS = 600


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


def default(value):
    if isinstance(value, (datetime, Decimal, Path)):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(type(value).__name__)


def encode(value):
    return (json.dumps(value, default=default, sort_keys=True, indent=2,
                       allow_nan=False, ensure_ascii=True) + "\n").encode()


def save(path, value):
    with Path(path).open("xb") as stream:
        stream.write(encode(value))
    return digest(path)


def identity():
    return {"iterative": implementation_identity(),
            "control_sources": {name: digest(ROOT / name) for name in EXTRA_SOURCES}}


def policies():
    from research.gold_iterative.contracts import gold_555_genome
    return {
        "canal1": StrategyGenome(
            schema_version=2, entry_mode="signal_market", entry_expiry_min=15,
            entry_ladder_mode="adverse", entry_ladder_step=4.0, leg_count=3,
            volume_weights=(0.01, 0.04, 0.04), target_mode="none", be_mode="none",
            stop_mode="basket_money", stop_value=25.0, profit_lock_arm=10.0,
            profit_lock_giveback=2.0, time_exit_min=40, time_exit_mode="loss_only",
            provider_management_mode="exact",
            source_strategy_fingerprint="32cb5c0fe8205ad00a0c655bacd5446c6cc219d1ad7338967212c71781860631",
        ),
        "canal2": gold_555_genome(),
    }


def prepare(args):
    if args.study.exists():
        raise ValueError("study already exists; outputs are immutable")
    manifest = read(args.capture / "manifest.json")
    if manifest["contract"] != "incremental_live_day_capture_v1":
        raise ValueError("unsupported capture contract")
    source = manifest["event_evidence"]
    delta = args.capture / "event_delta.jsonl.gz"
    if digest(args.prefix) != source["base_prefix_sha256"] or args.prefix.stat().st_size != source["base_prefix_bytes"]:
        raise ValueError("source prefix identity mismatch")
    if digest(delta) != source["delta_gzip_sha256"]:
        raise ValueError("compressed source identity mismatch")
    tapes = {}
    for symbol in ("XAUUSD", "EURUSD"):
        path = args.capture / f"{symbol}.parquet"
        proof = manifest["files"][path.name]
        if digest(path) != proof["sha256"] or path.stat().st_size != proof["bytes"]:
            raise ValueError(f"quote identity mismatch: {symbol}")
        tapes[symbol] = {"path": str(path.resolve()), "sha256": proof["sha256"]}
    complete_hash, byte_count, rows = hashlib.sha256(), 0, []
    for path, opener in ((args.prefix, open), (delta, gzip.open)):
        with opener(path, "rb") as stream:
            for line in stream:
                complete_hash.update(line)
                byte_count += len(line)
                row = json.loads(line)
                if row.get("ev") == "telegram_raw":
                    rows.append(raw_message(row))
    if complete_hash.hexdigest() != source["reconstructed_prefix_sha256"] or byte_count != source["reconstructed_prefix_bytes"]:
        raise ValueError("reconstructed causal source identity mismatch")
    start = utc(manifest["management_capture_activation_utc"])
    cutoff = utc(manifest["event_cutoff_utc"])
    rows = [row for row in rows if start <= utc(row["ts"]) <= cutoff]
    signals, diagnostics = compile_signals(rows, start=start, cutoff=cutoff, sticker_directions=STICKERS)
    if len(signals) > MAX_SIGNALS:
        raise ValueError("fixed control budget exceeded; no truncation permitted")
    args.study.mkdir(parents=True)
    raw_hash = save(args.study / "raw_messages.json", rows)
    diagnostics_hash = save(args.study / "input_diagnostics.json", {
        "signals": [asdict(row) for row in signals], "diagnostics": diagnostics, "raw_rows": len(rows)})
    save(args.study / "protocol.json", {
        "contract": "raw_message_control_diagnostic_v2", "status": "retrospective_not_certified_not_oos",
        "search_candidate_budget": 0, "mass_search_authorized": False,
        "start_utc": start, "cutoff_utc": cutoff, "max_signals": MAX_SIGNALS,
        "raw_messages_sha256": raw_hash, "tapes": tapes,
        "input_diagnostics_sha256": diagnostics_hash,
        "source_capture_manifest_sha256": digest(args.capture / "manifest.json"),
        "reconstructed_event_sha256": complete_hash.hexdigest(),
        "broker_epoch_offset_seconds": manifest["raw_server_epoch_minus_utc_seconds"],
        "latencies_ms": LATENCIES, "execution_scenarios": SCENARIOS,
        "max_wall_seconds": MAX_WALL_SECONDS, "sticker_directions": STICKERS,
        "genomes": {channel: asdict(value) for channel, value in policies().items()},
        "implementation": identity(), "expected_signal_ids": [row.signal_id for row in signals],
        "admission_blockers": [
            "tick_archive_not_complete_live_tape", "broker_order_lifecycle_not_in_tick_engine",
            "execution_latency_scenarios_not_calibrated_tolerances", "portfolio_margin_not_simulated",
            "hypothetical_zero_commission_and_no_rollover_contract", "fresh_heldout_validation_missing",
        ],
        "account_currency": "EUR", "contract_size": 100.0, "currency_digits": 2,
        "fx_max_age_ms": 5000, "independence": "own entries and continuous state; no live outcome or state inputs",
        "out_of_scope": "raw parsing supports Gold NOW and declared Dubai stickers; unresolved commands retained",
    })
    print(json.dumps({"prepared_signals": len(signals), "raw_messages": len(rows),
                      "maximum_engine_runs": len(signals) * len(SCENARIOS) * 3,
                      "search_candidates": 0}), flush=True)


def load_tape(proof, offset):
    path = Path(proof["path"])
    if digest(path) != proof["sha256"]:
        raise ValueError("quote tape changed after preparation")
    frame = pd.read_parquet(path)
    ns = pd.to_datetime(frame["time_utc"], utc=True).astype("datetime64[ns, UTC]").astype("int64").to_numpy()
    if not np.array_equal(ns, (frame["source_time_msc"].to_numpy(dtype=np.int64) - offset * 1000) * 1_000_000):
        raise ValueError("quote clock contract mismatch")
    return ns, frame["bid"].to_numpy(), frame["ask"].to_numpy()


def run(args):
    from research.dubai_iterative.engine import ExecutionAssumptions, simulate
    from research.dubai_iterative.fast_engine import FastEvaluator
    from research.dubai_iterative.oracle import ExecutionScenario, oracle_simulate
    protocol_path = args.study / "protocol.json"
    protocol, protocol_hash = read_frozen(protocol_path)
    messages, messages_hash = read_frozen(args.study / "raw_messages.json")
    if protocol["implementation"] != identity() or protocol["raw_messages_sha256"] != messages_hash:
        raise ValueError("frozen implementation or messages changed")
    if (protocol["search_candidate_budget"] != 0 or protocol["latencies_ms"] != list(LATENCIES)
            or protocol.get("execution_scenarios") != list(SCENARIOS)
            or protocol.get("max_wall_seconds") != MAX_WALL_SECONDS):
        raise ValueError("not the fixed diagnostic protocol")
    signals, diagnostics = compile_signals(messages,
        start=utc(protocol["start_utc"]), cutoff=utc(protocol["cutoff_utc"]),
        sticker_directions=protocol["sticker_directions"])
    if [row.signal_id for row in signals] != protocol["expected_signal_ids"] or len(signals) > MAX_SIGNALS:
        raise ValueError("cohort or budget changed")
    genomes = policies()
    if encode(protocol["genomes"]) != encode({channel: asdict(genome) for channel, genome in genomes.items()}):
        raise ValueError("fixed policy changed")
    market, conversion = (load_tape(protocol["tapes"][symbol], protocol["broker_epoch_offset_seconds"])
                          for symbol in ("XAUUSD", "EURUSD"))
    results, started = [], time.monotonic()
    for signal in signals:
        genome = genomes[signal.channel]
        path = make_path(signal, genome, market=market, conversion=conversion,
            cutoff=utc(protocol["cutoff_utc"]), contract_size=protocol["contract_size"],
            currency_digits=protocol["currency_digits"], max_fx_age_ms=protocol["fx_max_age_ms"],
            market_sha256=protocol["tapes"]["XAUUSD"]["sha256"],
            conversion_sha256=protocol["tapes"]["EURUSD"]["sha256"])
        for scenario in SCENARIOS:
            if time.monotonic() - started > MAX_WALL_SECONDS:
                save(args.study / "blocked_run.json", {
                    "protocol_sha256": protocol_hash, "completed_controls": results,
                    "blockers": ["fixed_control_wall_budget_exhausted"],
                    "full_live_parity_verified": False, "search_candidates": 0})
                raise ValueError("fixed control wall budget exhausted between controls")
            begin = time.monotonic()
            scalar = asdict(simulate(path, genome, execution=ExecutionAssumptions(**scenario)))
            fast = asdict(FastEvaluator(execution=ExecutionAssumptions(**scenario))(path, genome))
            oracle = asdict(oracle_simulate(path, genome, execution=ExecutionScenario(**scenario)))
            mismatches = {"fast": [key for key in oracle if fast[key] != oracle[key]],
                          "scalar": [key for key in oracle if scalar[key] != oracle[key]]}
            row = {"signal_id": signal.signal_id, **scenario,
                   "result": scalar, "fast": fast, "oracle": oracle, "engine_mismatches": mismatches,
                   "elapsed_seconds": time.monotonic() - begin, "invalid_fx_ticks": int((~path.fx_valid).sum())}
            results.append(row)
            print(json.dumps({"signal": signal.signal_id, **scenario,
                "entries": len(scalar["entries"]), "exit": scalar["exit_reason"],
                "mismatches": mismatches, "blockers": scalar["blockers"]}), flush=True)
    forbidden = [name for name in sys.modules if name in {
        "MetaTrader5", "listener", "executor", "gold_555_entry_watch", "gold_555_live_candidate",
        "dubai_live_candidate", "research.gold_iterative.live_parity", "research.gold_iterative.ledger_evidence"}]
    if forbidden or protocol["implementation"] != identity():
        raise ValueError(f"independence or frozen code boundary violated: {forbidden}")
    verify_frozen(protocol_path, protocol_hash)
    verify_frozen(args.study / "raw_messages.json", messages_hash)
    for proof in protocol["tapes"].values():
        verify_frozen(proof["path"], proof["sha256"])
    sha = save(args.study / "independent_results.json", {
        "protocol_sha256": protocol_hash, "results": results,
        "diagnostics": diagnostics, "forbidden_imports": forbidden,
        "engine_runs": len(results) * 3, "search_candidates": 0,
        "elapsed_seconds": time.monotonic() - started, "full_live_parity_verified": False,
    })
    print(json.dumps({"results_sha256": sha, "engine_runs": len(results) * 3}), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    prep = modes.add_parser("prepare")
    prep.add_argument("--capture", type=Path, required=True)
    prep.add_argument("--prefix", type=Path, required=True)
    prep.add_argument("--study", type=Path, required=True)
    execute = modes.add_parser("run")
    execute.add_argument("--study", type=Path, required=True)
    args = parser.parse_args(argv)
    {"prepare": prepare, "run": run}[args.mode](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
