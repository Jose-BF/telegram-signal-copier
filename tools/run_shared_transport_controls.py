"""Run immutable bounded transport hypotheses without importing the live client."""

import argparse
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.shared_transport import PassiveEvent, TransportJob, simulate_transport
from tools.run_causal_controls import digest, read_frozen, save, verify_frozen


SOURCES = ("tools/run_shared_transport_controls.py", "research/shared_transport.py",
           "mt5_scheduling.py", "mt5_read_protocol.py", "mt5_trade_protocol.py",
           "mt5_protocol.py", "tools/run_causal_controls.py")


def run(input_path, output_path):
    input_path, output_path = Path(input_path), Path(output_path)
    if output_path.exists():
        raise ValueError("immutable output already exists")
    if input_path.stat().st_size > 8_000_000:
        raise ValueError("input byte budget exceeded")
    protocol, sha = read_frozen(input_path)
    if (set(protocol) != {"contract", "clock", "cases"}
            or protocol["contract"] != "shared_transport_hypotheses_v1"
            or protocol["clock"] != "session_relative_nanoseconds"):
        raise ValueError("explicit transport hypothesis and clock contract required")
    cases = protocol["cases"]
    if not isinstance(cases, list) or not 1 <= len(cases) <= 100:
        raise ValueError("case budget requires 1 to 100 cases")
    identifiers, prepared, count = set(), [], 0
    for case in cases:
        if set(case) != {"case_id", "capacity", "cutoff_ns", "jobs", "passive_events"}:
            raise ValueError("invalid case fields")
        case_id = case["case_id"]
        if not isinstance(case_id, str) or not case_id or len(case_id) > 128 or case_id in identifiers:
            raise ValueError("case identity missing or duplicated")
        identifiers.add(case_id)
        if not isinstance(case["jobs"], list) or not isinstance(case["passive_events"], list):
            raise ValueError("case inputs must be arrays")
        count += len(case["jobs"]) + len(case["passive_events"])
        if count > 10_000:
            raise ValueError("total input budget exceeded")
        prepared.append((case, [TransportJob(**row) for row in case["jobs"]],
                         [PassiveEvent(**row) for row in case["passive_events"]]))
    sources = {name: digest(ROOT / name) for name in SOURCES}
    started, rows = time.monotonic(), []
    for case, jobs, passive in prepared:
        if time.monotonic() - started > 600:
            raise TimeoutError("control wall budget exceeded")
        result = simulate_transport(jobs, cutoff_ns=case["cutoff_ns"], capacity=case["capacity"],
                                    passive_events=passive, max_events=100_000)
        rows.append({"case_id": case["case_id"], "result": result})
    verify_frozen(input_path, sha)
    if any(digest(ROOT / name) != expected for name, expected in sources.items()):
        raise ValueError("transport control sources changed during execution")
    report = {"contract": "shared_transport_control_report_v1", "status": "diagnostic_only",
              "expected_cases": len(cases), "cases": rows, "sources": sources,
              "input": {"path": str(input_path.resolve()), "sha256": sha},
              "elapsed_seconds": time.monotonic() - started, "strategy_search_performed": False,
              "full_live_parity_verified": False, "portfolio_admitted": False,
              "limitations": ["Class priority and admission share pure runtime rules; within-class ordering is a hypothesis.",
                              "Declared preparation/service durations, not measured or calibrated broker latency.",
                              "No fill prices, strategy feedback, risk, restart or native protective execution model.",
                              "Passive events are supplied facts/scenarios, not predicted broker outcomes.",
                              "Does not lift the existing single-basket engine or portfolio capability gates."]}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    save(output_path, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.input, args.output)
    print({"output": str(args.output), "cases": report["expected_cases"],
           "pending_cases": sum(bool(row["result"]["blockers"]) for row in report["cases"])})


if __name__ == "__main__":
    main()
