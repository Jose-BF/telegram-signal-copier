"""Bounded retrospective management diagnostic, explicitly conditioned on fills."""

import argparse
from collections import Counter
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.causal_replay import compile_signals, make_path, utc
from research.conditioned_management import replay_management
from research.dubai_iterative.contracts import StrategyGenome
from research.risk_trajectory import RiskSpec, compare_risk
from tools.compare_causal_controls import observed_events
from tools.compare_risk_trajectories import _read, _summary, _tape, paired_quotes
from tools.run_causal_controls import digest, encode, identity, save, verify_frozen


SOURCES = ("tools/run_conditioned_management.py", "research/conditioned_management.py",
           "tools/compare_risk_trajectories.py", "research/risk_trajectory.py",
           "research/risk_metrics.py",
           "tools/compare_causal_controls.py", "research/gold_iterative/live_parity.py",
           "research/gold_iterative/ledger_evidence.py", "research/causal_comparison.py",
           "broker_money.py", "mt5_deal_reason.py")
MAX_SIGNALS = 40
MAX_SECONDS = 600


def run(study, analysis, output, *, max_market_gap_ms):
    study, analysis, output = Path(study), Path(analysis), Path(output)
    if output.exists():
        raise ValueError("immutable output already exists")
    started, watched = time.monotonic(), {}
    implementation = identity()
    sources = {name: digest(ROOT / name) for name in SOURCES}
    protocol, protocol_sha = _read(study / "protocol.json", watched)
    messages, raw_sha = _read(study / "raw_messages.json", watched)
    metadata, metadata_sha = _read(study / "input_diagnostics.json", watched)
    manifest, _ = _read(analysis / "analysis_manifest.json", watched)
    ledger_path = analysis / "observed_ledgers.json"
    ledgers, ledger_sha = _read(ledger_path, watched)
    if (protocol.get("contract") != "raw_message_control_diagnostic_v2"
            or protocol.get("account_currency") != "EUR" or ledgers.get("account_currency") != "EUR"
            or raw_sha != protocol.get("raw_messages_sha256")
            or metadata_sha != protocol.get("input_diagnostics_sha256")
            or manifest.get("source_capture_manifest_sha256") != protocol.get("source_capture_manifest_sha256")):
        raise ValueError("frozen causal/native binding mismatch")
    proofs = [row for row in manifest["artifacts"] if row["name"] == ledger_path.name]
    if len(proofs) != 1 or proofs[0]["sha256"] != ledger_sha or proofs[0]["bytes"] != ledger_path.stat().st_size:
        raise ValueError("native ledger source binding mismatch")
    signals, diagnostics = compile_signals(messages, start=utc(protocol["start_utc"]),
        cutoff=utc(protocol["cutoff_utc"]), sticker_directions=protocol["sticker_directions"])
    if ([row.signal_id for row in signals] != protocol["expected_signal_ids"]
            or encode([asdict(row) for row in signals]) != encode(metadata["signals"])
            or encode(diagnostics) != encode(metadata["diagnostics"])):
        raise ValueError("current causal compilation differs from frozen inputs")
    actual = {row["sig_id"]: row for row in ledgers["signals"]}
    if len(actual) != len(ledgers["signals"]):
        raise ValueError("duplicate native signal")
    expected = {row.signal_id: row for row in signals}
    if len(expected) != len(signals) or len(expected.keys() | actual.keys()) > MAX_SIGNALS:
        raise ValueError("signal identity or bounded case budget invalid")
    genomes = {}
    for channel, raw in protocol["genomes"].items():
        values = dict(raw)
        for key in ("volume_weights", "target_steps", "parent_fingerprints"):
            if key in values:
                values[key] = tuple(values[key])
        genomes[channel] = StrategyGenome(**values)
    spec = RiskSpec("EUR", protocol["currency_digits"], protocol["contract_size"],
                    "account_base_profit_quote", protocol["fx_max_age_ms"], max_market_gap_ms)
    market = _tape(protocol["tapes"]["XAUUSD"], protocol["broker_epoch_offset_seconds"], watched)
    conversion = _tape(protocol["tapes"]["EURUSD"], protocol["broker_epoch_offset_seconds"], watched)
    triples = lambda tape: (tape[0], tape[1][:, 0], tape[1][:, 1])
    rows = []
    for sig in sorted(expected.keys() | actual.keys()):
        if time.monotonic() - started > MAX_SECONDS:
            raise TimeoutError("conditioned diagnostic wall budget exceeded")
        row = {"signal_id": sig, "channel": sig.split("_", 1)[0]}
        try:
            if sig not in expected or sig not in actual:
                raise ValueError("missing_or_unexpected_native_signal")
            signal, ledger = expected[sig], actual[sig]
            if (ledger.get("account_currency") != "EUR" or ledger.get("channel") != signal.channel
                    or ledger.get("direction") != signal.direction):
                raise ValueError("native_scope_mismatch")
            for position in ledger["positions"]:
                for deal in position["deals"]:
                    if type(deal.get("time_msc")) is not int:
                        raise ValueError("native_millisecond_time_missing")
                    try:
                        costs = [Decimal(str(deal[key])) for key in ("commission", "fee", "swap")]
                    except (InvalidOperation, KeyError) as exc:
                        raise ValueError("native_cost_fact_invalid") from exc
                    if any(not cost.is_finite() for cost in costs):
                        raise ValueError("native_cost_fact_invalid")
                    if any(cost != 0 for cost in costs):
                        raise ValueError("nonzero_cost_path_unsupported")
            observed = observed_events(ledger)
            entries = sorted((event for event in observed if event.kind == "entry"), key=lambda event: event.slot)
            path = make_path(signal, genomes[signal.channel], market=triples(market), conversion=triples(conversion),
                cutoff=utc(protocol["cutoff_utc"]), contract_size=protocol["contract_size"],
                currency_digits=protocol["currency_digits"], max_fx_age_ms=protocol["fx_max_age_ms"],
                market_sha256=protocol["tapes"]["XAUUSD"]["sha256"],
                conversion_sha256=protocol["tapes"]["EURUSD"]["sha256"])
            management, simulated = replay_management(path, genomes[signal.channel], entries)
            row["management"] = management
            if simulated is None:
                raise ValueError("conditioned_management_blocked")
            events = observed + simulated
            start, end = min(event.at for event in events), max(event.at for event in events)
            if sum((event.volume if event.kind == "entry" else -event.volume for event in simulated), Decimal(0)):
                end = max(end, utc(protocol["cutoff_utc"]))
            tape = paired_quotes(market, conversion, events, start=start, end=end)
            comparison = compare_risk(observed, simulated, tape, spec=spec)
            for name in ("observed", "simulated"):
                comparison[name] = _summary(comparison[name])
            row["risk"] = comparison
            row["status"] = comparison["status"]
        except (ValueError, KeyError, TypeError) as exc:
            row.update(status="blocked", blockers=[str(exc)])
        rows.append(row)
        print({"signal": sig, "status": row["status"]}, flush=True)
    for path, sha in watched.items():
        verify_frozen(path, sha)
    if implementation != identity() or any(digest(ROOT / name) != sha for name, sha in sources.items()):
        raise ValueError("diagnostic implementation changed during run")
    report = {"contract": "conditioned_management_risk_diagnostic_v1", "status": "diagnostic_only",
              "full_live_parity_verified": False, "entry_decisions_verified": False,
              "client_observation_replay_verified": False, "native_policy_identity_verified": False,
              "strategy_search_performed": False, "protocol_sha256": protocol_sha,
              "expected_signals": len(expected), "rows": rows,
              "statuses": dict(Counter(row["status"] for row in rows)),
              "risk_spec": asdict(spec), "sources": sources, "implementation": implementation,
              "inputs": {str(path): sha for path, sha in watched.items()},
              "elapsed_seconds": time.monotonic() - started,
              "inherited_admission_blockers": protocol["admission_blockers"],
              "limitations": ["Observed entries only; real exits and realized money are not engine inputs.",
                              "Declared archived policy, not proof of actual historical runtime policy identity.",
                              "Retained market tape, not the exact observations consumed by the live client.",
                              "No broker request/ack queue, installed-level timing or shared-account replay.",
                              "Exact conditional risk would not certify hypothetical entry predictions.",
                              "Zero-cost scope; missing/unsupported cases retained, not calibrated or OOS."]}
    output.parent.mkdir(parents=True, exist_ok=True)
    save(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-market-gap-ms", type=int, required=True)
    args = parser.parse_args()
    report = run(args.study, args.analysis, args.output, max_market_gap_ms=args.max_market_gap_ms)
    print({"output": str(args.output), "statuses": report["statuses"]})


if __name__ == "__main__":
    main()
