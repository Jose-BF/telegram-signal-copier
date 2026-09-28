"""Fixed retrospective source-only regression, never a future cohort or search."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research.causal_replay import compile_signals, make_path, utc
from research.dubai_iterative.engine import ExecutionAssumptions, simulate
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.oracle import ExecutionScenario, oracle_simulate
from research.dubai_iterative.market_contract import MarketProfile
from research.dubai_iterative.protection_contract import ProtectionProfile
from research.gold_iterative.contracts import gold_555_genome
from tools import run_protection_controls as p


def execution_from_metadata(metadata):
    info = metadata["XAUUSD"]
    return ExecutionAssumptions(
        entry_fill_latency_ms=250,
        protection=ProtectionProfile(point=info["point"], digits=info["digits"],
            stops_level_points=info["trade_stops_level"], freeze_level_points=info["trade_freeze_level"],
            processing_delay_ms=250, acknowledgement_delay_ms=250, retry_delay_ms=1000),
        market=MarketProfile(entry_acknowledgement_delay_ms=250, close_processing_delay_ms=250,
            close_acknowledgement_delay_ms=250, volume_min=info["volume_min"],
            volume_max=info["volume_max"], volume_step=info["volume_step"]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--capture", required=True, type=Path)
    args = parser.parse_args()
    out = p._workspace_path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    started, identity = time.monotonic(), p.identity()
    watched = {}
    def read(path):
        value, sha = p.read_frozen(p._workspace_path(path))
        watched[str(Path(path).resolve())] = sha
        return value, sha
    source = ROOT / "runtime_data/simulation_foundation_20260909/independent_v3"
    protocol, protocol_sha = read(source / "protocol.json")
    messages, messages_sha = read(source / "raw_messages.json")
    if messages_sha != protocol["raw_messages_sha256"]:
        raise ValueError("source messages changed")
    metadata, metadata_sha = read(args.capture / "symbol_metadata.json")
    capture, capture_sha = read(args.capture / "manifest.json")
    if metadata_sha != capture["files"]["symbol_metadata.json"]["sha256"]:
        raise ValueError("native symbol metadata hash mismatch")
    execution, genome = execution_from_metadata(metadata), gold_555_genome()
    scenario = ExecutionScenario(**vars(execution))
    signals, diagnostics = compile_signals(messages, start=utc(protocol["start_utc"]),
        cutoff=utc(protocol["cutoff_utc"]), sticker_directions=protocol["sticker_directions"])
    selected = [row for row in signals if row.channel == "canal2"]
    expected = [sid for sid in protocol["expected_signal_ids"] if sid.startswith("canal2_")]
    if len(selected) != 11 or [row.signal_id for row in selected] != expected:
        raise ValueError("historical control denominator changed")
    for proof in protocol["tapes"].values():
        watched[str(p._workspace_path(proof["path"]))] = proof["sha256"]
    watched[str(Path(__file__).resolve())] = p.digest(__file__)
    freeze = {"contract": "fixed_market_regression_v2", "status": "retrospective_not_oos",
        "frozen_at_utc": datetime.now(timezone.utc), "implementation": identity,
        "execution": asdict(execution), "genome": asdict(genome), "expected_signal_ids": expected,
        "sources": watched, "source_protocol_sha256": protocol_sha, "capture_sha256": capture_sha,
        "max_engine_evaluations": 33, "max_wall_seconds": 600, "search_candidates": 0,
        "independent_of_observed_outcomes": True, "full_live_parity_verified": False}
    frozen_protocol_sha = p.save(out / "protocol.json", freeze)
    market, conversion = [p.load_tape(protocol["tapes"][symbol], protocol["broker_epoch_offset_seconds"])
                          for symbol in ("XAUUSD", "EURUSD")]
    results = []
    for signal in selected:
        if time.monotonic() - started > 600:
            raise ValueError("fixed regression budget exhausted")
        path = make_path(signal, genome, market=market, conversion=conversion,
            cutoff=utc(protocol["cutoff_utc"]), contract_size=100., currency_digits=2,
            max_fx_age_ms=5000, market_sha256=protocol["tapes"]["XAUUSD"]["sha256"],
            conversion_sha256=protocol["tapes"]["EURUSD"]["sha256"])
        scalar = asdict(simulate(path, genome, execution=execution))
        fast = asdict(FastEvaluator(execution=execution)(path, genome))
        oracle = asdict(oracle_simulate(path, genome, execution=scenario))
        mismatches = {name: p.engine_mismatch_fields(value, oracle)
                      for name, value in (("scalar", scalar), ("fast", fast))}
        results.append({"signal_id": signal.signal_id, "scalar": scalar, "fast": fast,
                        "oracle": oracle, "mismatches": mismatches})
        print(p.encode({"signal_id": signal.signal_id, "mismatches": mismatches,
                        "blockers": scalar["blockers"]}).decode(), flush=True)
    for path, sha in watched.items():
        p.verify_frozen(path, sha)
    if p.identity() != identity or any(name in sys.modules for name in p.FORBIDDEN_IMPORTS):
        raise ValueError("implementation or offline boundary changed")
    mismatches = [row["signal_id"] for row in results if any(row["mismatches"].values())]
    blockers = [{"signal_id": row["signal_id"], "blockers": row["scalar"]["blockers"]}
                for row in results if row["scalar"]["blockers"]]
    p.verify_frozen(out / "protocol.json", frozen_protocol_sha)
    result = {"protocol_sha256": frozen_protocol_sha, "status": "engine_disagreement" if mismatches else "three_engine_regression_agrees",
        "results": results, "denominator": len(selected), "engine_evaluations": len(results) * 3,
        "mismatched_signal_ids": mismatches, "engine_blockers": blockers,
        "source_admission_blockers": protocol["admission_blockers"], "diagnostics": diagnostics,
        "completed_at_utc": datetime.now(timezone.utc), "elapsed_seconds": time.monotonic() - started,
        "implementation": identity, "full_live_parity_verified": False, "search_candidates": 0}
    p.save(out / "results.json", result)
    print(p.encode({key: value for key, value in result.items() if key not in
                   {"results", "implementation", "diagnostics"}}).decode(), flush=True)
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
