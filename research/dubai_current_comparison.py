"""Offline admission report for comparing the retained study to Dubai's contract.

This freezes comparison inputs, not an economic result or a live-state claim.
No execution gate is relaxed to fit the configured basket policy.
"""

from collections import Counter
from dataclasses import asdict
from pathlib import Path
import json

from dubai_live_candidate import DubaiLivePolicy
from strategy_runtime_contract import strategy_contract_by_id
from research.causal_replay import utc
from research.dubai_break_even_study import verify as verify_be
from research.dubai_clock_audit import _write
from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.market import market_blockers
from research.dubai_iterative.protection import profile_blockers
from research.dubai_signal_anatomy import verify_anatomy
from research.execution_profile import execution_from_mapping
from research.strategy_study import ROOT, _digest, _read, _sha, _verify_sources


SOURCES = (
    "research/dubai_current_comparison.py", "dubai_live_candidate.py",
    "strategy_runtime_contract.py", "strategy_shadow_contracts.py",
    "provider_action_semantics.py",
    "docs/development/2026-09-14-canal1-current-comparison.md",
)


def current_genome():
    policy = DubaiLivePolicy()
    contract = strategy_contract_by_id("dubai_balanced_v1")
    if (contract.strategy_fingerprint != policy.fingerprint
            or contract.entry.volumes != policy.volume_weights
            or contract.entry.ladder_step != policy.entry_ladder_step
            or contract.entry.expiry_minutes != policy.entry_expiry_min
            or contract.protection.basket_stop_eur != policy.stop_value
            or contract.protection.profit_arm_eur != policy.profit_lock_arm
            or contract.protection.profit_giveback_eur != policy.profit_lock_giveback
            or contract.protection.time_exit_minutes != policy.time_exit_min
            or contract.protection.time_exit_mode != policy.time_exit_mode
            or contract.terminal.provider_management_mode != policy.provider_management_mode):
        raise ValueError("local policy and canonical contract disagree")
    payload = policy.research_payload()
    payload.update(schema_version=2, pending_entry_policy=contract.terminal.pending_entry_policy)
    genome = StrategyGenome.from_dict(payload)
    if genome.validation_errors():
        raise ValueError("current contract is outside the research grammar")
    return genome, contract


def dates(values):
    values = [utc(value) for value in values if value is not None]
    return {"first_utc": min(values).isoformat() if values else None,
            "last_utc": max(values).isoformat() if values else None}


def inventory(signals, rows):
    ids = {s["signal_id"] for s in signals}
    if len(ids) != len(signals):
        raise ValueError("duplicate source signals")
    controls = [r for r in rows if r["management"] == "no_be" and r["profile"] == "adverse_execution"]
    if len(controls) != len(ids) or {r["signal_id"] for r in controls} != ids:
        raise ValueError("incomplete or duplicate comparison cohort")
    covered = [r for r in controls if r["status"] in ("simulated", "unfilled")]
    chosen = [r for r in covered if r["status"] == "simulated" and r["r05_decision"]["decision"] == "retain"]
    return {
        "source_signal_count": len(signals), "source_receipts": dates(s["received_utc"] for s in signals),
        "covered_range_count": len(covered),
        "covered_range_receipts": dates(r["priced_received_utc"] for r in covered),
        "covered_range_month_counts": dict(sorted(Counter(r["priced_received_utc"][:7] for r in covered).items())),
        "r05_filled_count": len(chosen), "r05_filled_receipts": dates(r["priced_received_utc"] for r in chosen),
        "r05_ids": [r["signal_id"] for r in chosen],
        "baseline_status_counts": dict(sorted(Counter(r["status"] for r in controls).items())),
        "cohort": [{"signal_id": r["signal_id"], "initial_received_utc": r["received_utc"],
                    "priced_received_utc": r["priced_received_utc"], "base_status": r["status"],
                    "base_reasons": r["reasons"], "r05_decision": r["r05_decision"],
                    "dubai_outcome": "not_evaluated", "dubai_net_eur": None}
                   for r in controls],
    }


def run(history, output):
    history, output = Path(history).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("immutable comparison admission already exists")
    anatomy, be = history / "signal_anatomy_v2", history / "break_even_study_v2"
    proofs = {"anatomy": verify_anatomy(anatomy), "break_even": verify_be(be)}
    signals = _read(anatomy / "signals.json")
    rows = [json.loads(line) for line in (be / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    protocol = _read(be / "protocol.json")
    genome, contract = current_genome()
    capabilities = {}
    for name, value in protocol["profiles"].items():
        execution = execution_from_mapping(value)
        blockers = profile_blockers(None, genome, execution.protection) + market_blockers(None, genome, execution)
        capabilities[name] = {"status": "blocked" if blockers else "requires_full_engine_verification",
                              "blockers": sorted(set(blockers))}
    report = {"schema_version": "dubai_current_comparison_admission_v1", "input_proofs": proofs,
        "inventory": inventory(signals, rows), "local_policy": DubaiLivePolicy().research_payload(),
        "strategy_fingerprint": contract.strategy_fingerprint,
        "execution_fingerprint": contract.execution_fingerprint,
        "canonical_execution": contract.to_shadow_policy(role="candidate").execution_payload(),
        "research_genome": genome.to_dict(), "capabilities_under_previous_profiles": capabilities,
        "economic_comparison_status": "not_run", "current_vm_verified_today": False,
        "selected_policy": None, "orders_sent": 0,
        "next_required_work": [
            "Explicit three-engine support for basket-money stop with market-close request/processing/acknowledgement.",
            "Preserve source versions before any shared engine change; keep old profile gates unchanged.",
            "Bind causal management association and modality; do not relabel deterministic interpretation as actual runtime interpretation.",
            "Recheck coverage at each policy's own decision clock; preserve naturally open positions without forced 60-minute Dubai exits.",
            "Compare native sizes separately from a frozen 25-EUR nominal request-time risk budget; rerun quantized volumes, never scale P/L.",
        ]}
    inputs = {str(ROOT / name): _digest(ROOT / name) for name in SOURCES}
    for directory, names in ((anatomy, ("manifest.json", "signals.json")),
                             (be, ("manifest.json", "protocol.json", "results.jsonl"))):
        inputs.update({str(directory / name): _digest(directory / name) for name in names})
    output.mkdir(parents=True)
    _write(output / "report.json", report)
    manifest = {"schema_version": report["schema_version"], "inputs": inputs,
                "artifacts": {"report.json": _digest(output / "report.json")}}
    manifest["identity"] = _sha(manifest)
    _verify_sources(inputs)
    _write(output / "manifest.json", manifest)
    return {"status": "comparison_admission_complete_economics_not_run", "identity": manifest["identity"],
            "signal_count": len(signals), "capabilities": capabilities}


def verify(output):
    output = Path(output)
    manifest = _read(output / "manifest.json")
    if _sha({k: v for k, v in manifest.items() if k != "identity"}) != manifest["identity"]:
        raise ValueError("comparison admission manifest mismatch")
    _verify_sources(manifest["inputs"])
    if _digest(output / "report.json") != manifest["artifacts"]["report.json"]:
        raise ValueError("comparison admission report changed")
    return {"status": "sources_and_admission_artifact_verified", "identity": manifest["identity"]}
