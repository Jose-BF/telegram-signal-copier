"""Compare frozen retrospective market controls with separately verified MT5 facts.

This consumes the bound fixed_market_regression_v2 contract, not a prospective study.
No observed outcome is supplied to an engine and no tolerance is fitted here.
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.causal_comparison import compare_sequences
from tools.compare_causal_controls import observed_events, simulated_events
from tools import run_protection_controls as p


def validate_control(protocol, results, current_identity, protocol_sha256):
    if results.get("protocol_sha256") != protocol_sha256:
        raise ValueError("result protocol binding missing or changed; preserve and rerun legacy controls")
    if (protocol.get("contract") != "fixed_market_regression_v2"
            or protocol.get("status") != "retrospective_not_oos"
            or protocol.get("independent_of_observed_outcomes") is not True
            or protocol.get("full_live_parity_verified") is not False
            or protocol.get("search_candidates") != 0 or results.get("search_candidates") != 0):
        raise ValueError("not an independent, retrospective, zero-search market control")
    if protocol.get("implementation") != results.get("implementation"):
        raise ValueError("result implementation differs from frozen protocol")
    if protocol["implementation"] != current_identity:
        raise ValueError("implementation changed; rerun a separately frozen control")
    expected = protocol.get("expected_signal_ids", [])
    if not expected or len(set(expected)) != len(expected) or any(
            not sid.startswith("canal2_") for sid in expected):
        raise ValueError("invalid Gold control universe")
    rows = results.get("results", [])
    if [row["signal_id"] for row in rows] != expected:
        raise ValueError("missing, duplicate, reordered or unexpected market control")
    if (results.get("denominator") != len(expected)
            or results.get("engine_evaluations") != len(expected) * 3
            or protocol.get("max_engine_evaluations") != len(expected) * 3):
        raise ValueError("market control denominator or evaluation count changed")
    for row in rows:
        for engine in ("scalar", "fast", "oracle"):
            if row[engine].get("signal_id") != row["signal_id"]:
                raise ValueError("embedded engine signal identity mismatch")


def _blocked(reason, status="blocked"):
    return {"status": status, "blockers": [reason], "full_live_parity_verified": False}


def compare_rows(protocol, results, actual, directions):
    rows = []
    for control in results["results"]:
        sid = control["signal_id"]
        mismatches = {name: p.engine_mismatch_fields(control[name], control["oracle"])
                      for name in ("scalar", "fast")}
        row = {"signal_id": sid, "engine_mismatches": mismatches,
               "recorded_engine_mismatches": control["mismatches"],
               "observed_entries": actual.get(sid, {}).get("n_positions"),
               "simulated_entries": len(control["scalar"]["entries"])}
        try:
            if any(mismatches.values()) or any(control["mismatches"].values()):
                raise ValueError("independent engine disagreement")
            if sid not in directions:
                raise ValueError("frozen causal direction missing")
            if sid not in actual:
                raise ValueError("observed signal missing; no-trade outcome not inferred")
            row["comparison"] = compare_sequences(
                observed_events(actual[sid]), simulated_events(control["scalar"], directions[sid]))
        except ValueError as exc:
            row["comparison"] = _blocked(str(exc))
        rows.append(row)
    for sid in sorted(actual.keys() - set(protocol["expected_signal_ids"])):
        outside = sid.startswith("canal1_")
        rows.append({"signal_id": sid, "observed_entries": actual[sid]["n_positions"],
            "simulated_entries": None, "comparison": _blocked(
                "Dubai is outside this frozen Gold-only control" if outside else
                "observed Gold signal missing from frozen controls",
                "out_of_scope" if outside else "blocked")})
    return rows


def compare(args):
    watched = {}

    def read(path):
        path = p._workspace_path(path)
        value, sha = p.read_frozen(path)
        watched[str(path)] = sha
        return value, sha

    protocol, protocol_sha = read(args.study / "protocol.json")
    results, results_sha = read(args.study / "results.json")
    current_identity = p.identity()
    validate_control(protocol, results, current_identity, protocol_sha)
    for path, sha in protocol["sources"].items():
        path = p._workspace_path(path)
        p.verify_frozen(path, sha)
        watched[str(path)] = sha
    source, source_sha = read(args.source_study / "protocol.json")
    if protocol["source_protocol_sha256"] != source_sha:
        raise ValueError("source protocol identity mismatch")
    raw_path = p._workspace_path(args.source_study / "raw_messages.json")
    if (protocol["sources"].get(str(raw_path)) != source["raw_messages_sha256"]
            or p.digest(raw_path) != source["raw_messages_sha256"]):
        raise ValueError("raw source identity mismatch")
    metadata, metadata_sha = read(args.source_study / "input_diagnostics.json")
    if metadata_sha != source["input_diagnostics_sha256"]:
        raise ValueError("frozen causal metadata identity mismatch")
    directions = {row["signal_id"]: row["direction"] for row in metadata["signals"]}
    if (len(directions) != len(metadata["signals"])
            or list(directions) != source["expected_signal_ids"]
            or [sid for sid in directions if sid.startswith("canal2_")] != protocol["expected_signal_ids"]):
        raise ValueError("frozen causal universe mismatch")
    observed = p._load_actual_sources(args.analysis, args.measurement, source,
                                      args.event_prefix, args.event_delta)
    manifest = p.read(observed["paths"]["analysis_manifest"])
    if manifest["source_capture_manifest_sha256"] != source["source_capture_manifest_sha256"]:
        raise ValueError("observed analysis belongs to another capture")
    for name, path in observed["paths"].items():
        watched[str(path)] = observed["hashes"][name + "_sha256"]
    actual = {row["sig_id"]: row for row in observed["ledger"]["signals"]}
    if len(actual) != len(observed["ledger"]["signals"]):
        raise ValueError("duplicate observed signal identity")
    rows = compare_rows(protocol, results, actual, directions)
    selected = [row for row in rows if row["signal_id"] in protocol["expected_signal_ids"]]
    report = {"contract": "market_control_factual_comparison_v1", "status": "diagnostic_only",
        "full_live_parity_verified": False, "search_candidates": 0, "mass_search_authorized": False,
        "validation_cohort": "retrospective_reused_not_oos",
        "inputs": {"protocol_sha256": protocol_sha, "results_sha256": results_sha,
                   "source_protocol_sha256": source_sha, "observed": observed["hashes"]},
        "implementation": current_identity,
        "comparator_sources": {name: p.digest(ROOT / name) for name in (
            "tools/compare_market_controls.py", "tools/compare_causal_controls.py",
            "tools/run_protection_controls.py", "research/causal_comparison.py",
            "research/causal_replay.py", "research/gold_iterative/ledger_evidence.py",
            "research/gold_iterative/exit_deals.py", "mt5_deal_reason.py")},
        "rows": rows, "statuses": dict(Counter(row["comparison"]["status"] for row in rows)),
        "denominators": {"observed_signals": len(actual), "observed_positions": observed["ledger"]["positions"],
            "frozen_gold_controls": len(selected),
            "gold_observed_entries": sum(row["observed_entries"] or 0 for row in selected),
            "gold_simulated_entries": sum(row["simulated_entries"] for row in selected),
            "out_of_scope_signals": sum(row["comparison"]["status"] == "out_of_scope" for row in rows)},
        "inherited_source_limitations_not_reassessed": results["source_admission_blockers"],
        "unverified": ["decision_sequence", "modify_reject_confirm_sequence",
                       "alternative_fill_realizability", "execution_profile_calibration"],
    }
    for path, sha in watched.items():
        p.verify_frozen(path, sha)
    for name, sha in report["comparator_sources"].items():
        p.verify_frozen(ROOT / name, sha)
    if p.identity() != current_identity:
        raise ValueError("implementation changed during comparison")
    out = p._workspace_path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    report_sha = p.save(out, report)
    print(p.encode({"statuses": report["statuses"], "denominators": report["denominators"],
                    "report_sha256": report_sha}).decode())
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("study", "source-study", "analysis", "measurement", "event-prefix", "event-delta", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    compare(parser.parse_args(argv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
