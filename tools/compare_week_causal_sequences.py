"""Compare every frozen weekly replay row with reconciled native leg events.

This is a post-run decision/entry/exit diagnostic. It cannot certify shared
account drawdown, broker counterfactual fills, or live strategy parity.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mt5_deal_reason import DEAL_REASON_NAMES
from research.causal_comparison import compare_sequences
from research.causal_replay import utc
from tools import assess_week_causal_portfolio as portfolio
from tools import run_causal_controls as causal
from tools import run_week_causal_controls as weekly
from tools.audit_native_money_anchor import digest, read
from tools.audit_native_week_risk_path import native_events, OFFSET_SECONDS
from tools.compare_causal_controls import observed_slot, simulated_events


CONTRACT = "weekly_raw_causal_sequence_comparison_v1"
SOURCES = (
    "tools/compare_week_causal_sequences.py",
    "tools/compare_causal_controls.py",
    "tools/audit_native_week_risk_path.py",
    "research/causal_comparison.py",
    "mt5_deal_reason.py",
)


def _money_binding(money, path):
    matches = [sha for name, sha in money["inputs_sha256"].items()
               if Path(name).resolve() == Path(path).resolve()]
    if len(matches) != 1 or matches[0] != digest(path):
        raise ValueError(f"native money source is not bound: {path}")


def _observed(signal_id, positions, deals):
    by_ticket = {row["ticket"]: row for row in deals["deals"]}
    if len(by_ticket) != len(deals["deals"]):
        raise ValueError("native deal tickets duplicated")
    native = native_events(deals["deals"], positions)
    events, slots = [], set()
    for position in positions:
        pid = position["position_id"]
        entry_ticket = next(ticket for ticket in position["deal_tickets"]
                            if by_ticket[ticket]["entry"] == 0)
        slot = observed_slot(signal_id, by_ticket[entry_ticket].get("comment", ""))
        if slot in slots:
            raise ValueError("native logical slot duplicated")
        slots.add(slot)
        for event in native[pid]:
            mechanism = "market" if event.kind == "entry" else DEAL_REASON_NAMES.get(
                int(event.mechanism.removeprefix("native_reason_")))
            if mechanism is None or (event.kind == "exit" and mechanism not in {"tp", "sl", "expert"}):
                raise ValueError("native close mechanism unsupported")
            events.append(replace(event, slot=slot, mechanism=mechanism))
    return events


def no_fill_coverage(protocol, deals, money, broker):
    """Prove captured XAU fill absence, never absence of order attempts."""
    reasons = []
    margin = timedelta(seconds=OFFSET_SECONDS)
    try:
        start, end = utc(protocol["start_utc"]), utc(protocol["end_utc"])
        query_start, query_end = utc(deals["query_start"]), utc(deals["query_end"])
        captured = utc(deals["captured_at_utc"])
        if not (query_start <= start - margin and query_end >= end + margin
                and captured >= end + margin):
            reasons.append("native_deal_query_or_capture_does_not_cover_week_with_clock_margin")
    except (KeyError, ValueError, TypeError):
        reasons.append("native_deal_query_clock_unavailable")
    if (deals.get("server") != broker["account"]["server"]
            or deals.get("currency") != broker["account"]["currency"]):
        reasons.append("native_deal_account_binding_mismatch")
    expected = {ticket: position["position_id"] for position in money["positions"]
                for ticket in position["deal_tickets"]}
    tickets = [ticket for position in money["positions"] for ticket in position["deal_tickets"]]
    xau = [row for row in deals["deals"] if row.get("symbol") == "XAUUSD"]
    actual = [row["ticket"] for row in xau]
    if (len(expected) != len(tickets) or len(set(actual)) != len(actual)
            or set(actual) != set(expected)
            or any(row.get("position_id") != expected.get(row["ticket"])
                   for row in xau)):
        reasons.append("native_xau_deal_universe_not_exhaustively_reconciled")
    return {"verified_no_xau_fill_for_absent_signal_ids": not reasons,
            "xau_deal_count": len(xau), "reconciled_position_count": len(money["positions"]),
            "order_attempt_absence_verified": False,
            "blockers": reasons}


def compare_matrix(protocol, assembled, signals, observed):
    ids = protocol["raw_signal_ids"]
    if list(signals) != ids:
        raise ValueError("raw signal order changed")
    controls = {}
    for row in assembled["results"]:
        key = row["signal_id"], weekly._scenario_key(row)
        if key in controls:
            raise ValueError("weekly control duplicated")
        controls[key] = row
    expected = {(signal_id, weekly._scenario_key(scenario))
                for signal_id in ids for scenario in weekly.SCENARIOS}
    if set(controls) != expected:
        raise ValueError("weekly control matrix incomplete or unexpected")
    rows = []
    for signal_id in ids:
        for scenario in weekly.SCENARIOS:
            control = controls[(signal_id, weekly._scenario_key(scenario))]
            result = {"signal_id": signal_id, "channel": signals[signal_id].channel,
                      "scenario": scenario, "control_status": control["status"],
                      "observed_position_count": None,
                      "observed_fill_status": "unknown"}
            try:
                if signal_id not in observed:
                    raise ValueError("no_native_basket_observed; no-trade not independently verified")
                actual = observed[signal_id]
                if isinstance(actual, str):
                    raise ValueError(actual)
                result["observed_position_count"] = sum(event.kind == "entry" for event in actual)
                result["observed_fill_status"] = (
                    "verified_no_xau_fill_in_captured_deals" if not actual else
                    "reconciled_native_fills")
                if control["status"] != "diagnostic_only" or control.get("censored_data_end") is True:
                    raise ValueError(f"weekly control incomplete: {control['status']}")
                if any(control["engine_mismatches"].values()):
                    raise ValueError("independent engines disagree")
                virtual = simulated_events(control["result"], signals[signal_id].direction)
                result["comparison"] = compare_sequences(actual, virtual)
            except (ValueError, KeyError, TypeError) as exc:
                result["comparison"] = {"status": "blocked", "blockers": [str(exc)],
                                        "full_live_parity_verified": False}
            rows.append(result)
    return rows


def compare(protocol_path, assembled_path, native_deals_path, native_baskets_path,
            native_money_path, output_path):
    paths = tuple(map(Path, (protocol_path, assembled_path, native_deals_path,
                              native_baskets_path, native_money_path)))
    protocol_path, assembled_path, native_deals_path, native_baskets_path, native_money_path = paths
    output_path = Path(output_path)
    if output_path.exists():
        raise FileExistsError(output_path)
    hashes = {str(path): digest(path) for path in paths}
    sources = {name: digest(ROOT / name) for name in SOURCES}
    protocol, assembled = weekly._read(protocol_path), weekly._read(assembled_path)
    portfolio._validate(protocol, assembled, hashes[str(protocol_path)])
    if protocol.get("broker_epoch_offset_seconds_hypothesis") != OFFSET_SECONDS:
        raise ValueError("native/replay broker clock hypothesis differs")
    signals = portfolio._raw_signals(protocol)
    money, deals, baskets = map(read, (native_money_path, native_deals_path, native_baskets_path))
    if (money.get("contract") != "native_closed_money_anchor_v2"
            or money.get("account_currency") != "EUR"
            or money.get("position_count") != len(money.get("positions", []))
            or baskets.get("source_sha256") != hashes[str(native_deals_path)]):
        raise ValueError("native week ledger identity mismatch")
    for path in (native_deals_path, native_baskets_path):
        _money_binding(money, path)
    shared = [Path(name) for name in protocol["input_paths_sha256"]
              if Path(name).resolve() in {Path(name).resolve()
                  for name in money["inputs_sha256"]}]
    if len(shared) != 2:
        raise ValueError("native/replay anchor and broker contract binding missing")
    for path in shared:
        _money_binding(money, path)
    broker_candidates = [weekly._read(path) for path in shared
                         if "account" in weekly._read(path)]
    if len(broker_candidates) != 1:
        raise ValueError("weekly broker money contract is not unique")
    no_fill = no_fill_coverage(protocol, deals, money, broker_candidates[0])
    by_signal = defaultdict(list)
    for position in money["positions"]:
        by_signal[position["signal_id"]].append(position)
    if (set(by_signal) != {row["signal_id"] for row in baskets["baskets"]}
            or sum(len(rows) for rows in by_signal.values()) != len(baskets["positions"])):
        raise ValueError("native basket/position denominator mismatch")
    for basket in baskets["baskets"]:
        positions = by_signal[basket["signal_id"]]
        if (len(positions) != len([row for row in baskets["positions"]
                                    if row["signal_id"] == basket["signal_id"]])
                or sum((Decimal(str(row["actual_net_eur"])) for row in positions), Decimal(0))
                   != Decimal(str(basket["net_eur"]))):
            raise ValueError("native basket money or position count differs")
    observed = {}
    for signal_id, positions in by_signal.items():
        try:
            observed[signal_id] = _observed(signal_id, positions, deals)
        except (ValueError, KeyError, TypeError, StopIteration) as exc:
            observed[signal_id] = f"native_leg_binding_blocked: {exc}"
    absent = sorted(set(protocol["raw_signal_ids"]) - set(observed))
    if no_fill["verified_no_xau_fill_for_absent_signal_ids"]:
        observed.update({signal_id: [] for signal_id in absent})
    rows = compare_matrix(protocol, assembled, signals, observed)
    extra = sorted(set(by_signal) - set(protocol["raw_signal_ids"]))
    for signal_id in extra:
        rows.append({"signal_id": signal_id, "channel": signal_id.split("_", 1)[0],
                     "scenario": None, "control_status": "missing_raw_signal",
                     "observed_position_count": len(by_signal[signal_id]),
                     "comparison": {"status": "blocked",
                                    "blockers": ["native_basket_missing_from_raw_replay"],
                                    "full_live_parity_verified": False}})
    portfolio._validate(protocol, assembled, hashes[str(protocol_path)])
    if (any(digest(path) != hashes[str(path)] for path in paths)
            or any(digest(ROOT / name) != sha for name, sha in sources.items())):
        raise ValueError("weekly sequence source changed during comparison")
    report = {"contract": CONTRACT, "status": "diagnostic_only",
              "full_live_parity_verified": False,
              "observed_account_equity_compared": False,
              "shared_account_drawdown_compared": False,
              "source_clock_admitted": protocol["clock_admitted"],
              "raw_semantics_complete": protocol["raw_semantics_complete"],
              "raw_signal_count": len(protocol["raw_signal_ids"]),
              "native_basket_count": len(by_signal),
              "native_baskets_missing_from_raw": extra,
              "raw_signals_without_native_basket": absent,
              "no_fill_coverage": no_fill,
              "verified_no_xau_fill_signal_ids": absent if no_fill[
                  "verified_no_xau_fill_for_absent_signal_ids"] else [],
              "expected_control_rows": len(protocol["raw_signal_ids"]) * len(weekly.SCENARIOS),
              "rows": rows,
              "statuses": dict(sorted(Counter(row["comparison"]["status"] for row in rows).items())),
              "inputs_sha256": hashes,
              "sources_sha256": sources,
              "limitations": [
                  "Closed native MT5 deals are observed; alternative replay fills are hypothetical.",
                  "An absent native basket can prove no XAU fill only under exhaustive captured-deal coverage.",
                  "No filled position does not prove the bot made no order attempt or decision.",
                  "Event sequence comparison does not establish risk-path or shared-account equity parity.",
                  "Unanchored broker days and hypothetical costs remain diagnostic only.",
              ]}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("xb") as stream:
        stream.write(causal.encode(report))
    return {"raw_signals": report["raw_signal_count"], "native_baskets": report["native_basket_count"],
            "statuses": report["statuses"], "output_sha256": digest(output_path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("protocol", "assembled", "native-deals", "native-baskets", "native-money", "output"):
        parser.add_argument(f"--{name}", required=True)
    args = parser.parse_args()
    print(compare(args.protocol, args.assembled, args.native_deals,
                  args.native_baskets, args.native_money, args.output))


if __name__ == "__main__":
    main()
