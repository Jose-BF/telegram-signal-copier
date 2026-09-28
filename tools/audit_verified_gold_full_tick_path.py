"""Compare identity-verified Gold native and recorded-shadow paths on all ticks.

This reconstructs already-recorded virtual decisions, not an independent
Telegram-to-order replay or an independently measured MT5 account equity path.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.causal_comparison import SequenceEvent
from research.risk_trajectory import RiskSpec, compare_risk, reconstruct_risk
from tools.audit_native_equity_snapshots import _tape
from tools.audit_native_money_anchor import cents, digest, read
from tools.audit_native_week_risk_path import (
    MARKET_MAX_AGE_MS, native_events, quote_grid, summarize_path,
)
from tools.audit_week_shadow_control_path import controls_and_states, shadow_rows


MAX_SIGNALS = 10
MAX_SECONDS = 120


def _bound(report, path):
    values = [sha for name, sha in report.get("inputs_sha256", {}).items()
              if Path(name).resolve() == Path(path).resolve()]
    if values != [digest(path)]:
        raise ValueError(f"input hash binding missing or changed: {path}")


def _close_quote_supports_fill(position, observed_quote, direction):
    settled = Decimal(str(position["close_price"]))
    quote = Decimal(str(observed_quote))
    reason = position.get("close_reason")
    if reason == "target":
        target = position.get("target_price")
        return (target is not None and settled == Decimal(str(target))
                and (quote >= settled if direction == "BUY" else quote <= settled))
    if reason == "stop":
        stop = position.get("stop_price")
        return (stop is not None and settled == Decimal(str(stop))
                and (quote <= settled if direction == "BUY" else quote >= settled))
    return quote == settled


def _verified_entry_binding(signal, pairing, broker_binding=None):
    if broker_binding is None:
        if (pairing.get("status") == "verified_journal_entry_binding"
                and pairing.get("full_entry_identity_verified") is True):
            return pairing["status"]
        raise ValueError("verified entry identities required")
    journal_verified = (pairing.get("status") == "verified_journal_entry_binding"
                        and pairing.get("full_entry_identity_verified") is True)
    if (broker_binding.get("signal_id") != signal
            or broker_binding.get("status") != "verified_broker_deal_comment_leg_binding"
            or broker_binding.get("native_leg_identity_verified") is not True
            or broker_binding.get("live_order_request_chain_verified") is not journal_verified
            or broker_binding.get("prior_pairing_status") != pairing.get("status")):
        raise ValueError("verified broker-comment entry identities required")
    legs = {row["leg_index"]: row for row in broker_binding["legs"]}
    pairs = {row["leg_index"]: row for row in pairing["pairs"]}
    if (len(legs) != len(broker_binding["legs"]) or set(legs) != set(pairs)
            or broker_binding.get("leg_count") != len(pairs)):
        raise ValueError("broker-comment leg denominator mismatch")
    for index, pair in pairs.items():
        leg = legs[index]
        if (leg["native_position_id"] != pair["native_position_id"]
                or Decimal(str(leg["native_entry_price"])) != Decimal(pair["native_entry_price"])
                or Decimal(str(leg["shadow_entry_price"])) != Decimal(pair["shadow_entry_price"])
                or Decimal(str(leg["volume"])) != Decimal(pair["volume"])):
            raise ValueError("broker-comment leg contradicts control pairing")
    return pairing["status"] if journal_verified else broker_binding["status"]


def shadow_sequence(signal, pairing, state_rows, *, broker_binding=None):
    _verified_entry_binding(signal, pairing, broker_binding)
    final = state_rows[-1]["state"]
    if (final["signal_id"] != signal or final["candidate_id"] != "gold_now_555_v1"
            or final["status"] != "closed" or final["complete"] is not True
            or final["evidence_blockers"]):
        raise ValueError("complete Gold shadow final state required")
    pairs = {row["leg_index"]: row for row in pairing["pairs"]}
    positions = {row["leg_index"]: row for row in final["positions"]}
    if (len(pairs) != len(pairing["pairs"]) or len(positions) != len(final["positions"])
            or set(pairs) != set(positions) or set(pairs) != set(range(len(pairs)))):
        raise ValueError("shadow leg identity or denominator mismatch")
    fills = [row for row in state_rows if row.get("ev") == "strategy_shadow_transition"
             and row.get("transition") == "virtual_fill"]
    closes = [row for row in state_rows if row.get("ev") == "strategy_shadow_transition"
              and row.get("transition") == "virtual_position_closed"]
    if len(fills) != len(pairs) or len(closes) != len(pairs):
        raise ValueError("shadow entry or full-exit transition missing")
    fill_by_leg = {row["transition_details"]["leg_index"]: row for row in fills}
    close_by_leg = {row["transition_details"]["leg_indexes"][0]: row for row in closes
                    if len(row["transition_details"]["leg_indexes"]) == 1}
    if set(fill_by_leg) != set(pairs) or set(close_by_leg) != set(pairs):
        raise ValueError("shadow fill/close transition identity mismatch")
    events = []
    for leg, pair in sorted(pairs.items()):
        position = positions[leg]
        fill, close = fill_by_leg[leg], close_by_leg[leg]
        if (position["status"] != "closed"
                or Decimal(str(position["entry_price"])) != Decimal(pair["shadow_entry_price"])
                or Decimal(str(position["volume"])) != Decimal(pair["volume"])
                or position["opened_tick_msc"] != fill["transition_tick_msc"]
                or position["closed_tick_msc"] != close["transition_tick_msc"]
                or not _close_quote_supports_fill(
                    position, close["transition_details"]["close_price"], final["direction"])):
            raise ValueError("final shadow leg contradicts transition or verified entry")
        if position["opened_tick_msc"] >= position["closed_tick_msc"]:
            raise ValueError("shadow entry does not precede exit")
        events.extend((
            SequenceEvent(leg + 1, "entry", datetime.fromtimestamp(
                position["opened_tick_msc"] / 1000, timezone.utc), final["direction"],
                Decimal(str(position["entry_price"])), Decimal(str(position["volume"])),
                Decimal("0.00"), "recorded_virtual_fill"),
            SequenceEvent(leg + 1, "exit", datetime.fromtimestamp(
                position["closed_tick_msc"] / 1000, timezone.utc), final["direction"],
                Decimal(str(position["close_price"])), Decimal(str(position["volume"])),
                cents(position["realized_eur"]), "recorded_virtual_close"),
        ))
    if sum((event.money for event in events), Decimal(0)) != cents(final["realized_eur"]):
        raise ValueError("shadow per-leg and basket realized money differ")
    return events


def summarize_comparison(comparison):
    left, right = comparison["observed"], comparison["simulated"]
    n = len(left["samples"])
    if n != len(right["samples"]):
        raise ValueError("common risk grid length mismatch")
    exposure_differences = 0
    floating_differences = 0
    realized_differences = 0
    same_exposure_floating_differences = 0
    same_position_state_floating_differences = 0
    max_volume_delta = Decimal(0)
    for actual, virtual in zip(left["samples"], right["samples"], strict=True):
        av = actual["long_volume"] + actual["short_volume"]
        vv = virtual["long_volume"] + virtual["short_volume"]
        max_volume_delta = max(max_volume_delta, abs(vv - av))
        same_exposure = (actual["long_volume"] == virtual["long_volume"]
                         and actual["short_volume"] == virtual["short_volume"]
                         and actual["open_count"] == virtual["open_count"])
        exposure_differences += not same_exposure
        if actual["floating"] is not None and virtual["floating"] is not None:
            floating_differs = actual["floating"] != virtual["floating"]
            floating_differences += floating_differs
            same_exposure_floating_differences += floating_differs and same_exposure
            if floating_differs and same_exposure and actual["realized"] == virtual["realized"]:
                actual_state = {slot: {key: value for key, value in position.items()
                                       if key not in {"floating", "total"}}
                                for slot, position in actual["positions"].items()}
                virtual_state = {slot: {key: value for key, value in position.items()
                                        if key not in {"floating", "total"}}
                                 for slot, position in virtual["positions"].items()}
                same_position_state_floating_differences += actual_state == virtual_state
        realized_differences += actual["realized"] != virtual["realized"]
    a, b = left["known_sample_metrics"], right["known_sample_metrics"]
    return {"status": comparison["status"], "common_tick_and_event_marks": n,
            "full_risk_path_comparable": not comparison["blockers"],
            "mismatched_marks": comparison["mismatched_pairs"],
            "unknown_marks": comparison["unknown_pairs"],
            "exposure_difference_marks": exposure_differences,
            "floating_difference_marks": floating_differences,
            "same_exposure_floating_difference_marks": same_exposure_floating_differences,
            "same_position_state_floating_difference_marks": same_position_state_floating_differences,
            "realized_difference_marks": realized_differences,
            "max_volume_delta": str(max_volume_delta),
            "max_abs_total_difference_eur": (None if comparison["max_abs_total_difference"] is None
                                             else str(comparison["max_abs_total_difference"])),
            "max_abs_total_difference_scope": "known_marks_only" if comparison["blockers"] else "all_marks",
            "first_divergence_at": (None if comparison["first_divergence"] is None else
                                    comparison["first_divergence"]["at"].isoformat()),
            "native": summarize_path(left), "shadow": summarize_path(right),
            "drawdown_delta_eur": None if comparison["blockers"] or a is None or b is None else str(
                b["max_drawdown"] - a["max_drawdown"]),
            "blockers": comparison["blockers"]}


def audit(raw_dir, native_deals_path, money_path, broker_contract_path, anchor_path,
          native_risk_path, shadow_slice_path, shadow_manifest_path, control_path,
          ladder_path, broker_binding_path=None):
    started = time.monotonic()
    paths = tuple(map(Path, (raw_dir, native_deals_path, money_path,
                             broker_contract_path, anchor_path, native_risk_path,
                             shadow_slice_path, shadow_manifest_path, control_path, ladder_path)))
    (raw_dir, native_deals_path, money_path, broker_contract_path, anchor_path,
     native_risk_path, shadow_slice_path, shadow_manifest_path, control_path, ladder_path) = paths
    broker_binding_path = Path(broker_binding_path) if broker_binding_path else None
    native, money, contract, anchor, native_risk, control, ladder = map(
        read, (native_deals_path, money_path, broker_contract_path, anchor_path,
               native_risk_path, control_path, ladder_path))
    if (money.get("contract") != "native_closed_money_anchor_v2"
            or native_risk.get("contract") != "native_week_full_tick_risk_diagnostic_v1"
            or control.get("contract") != "week_shadow_live_control_path_diagnostic_v1"
            or ladder.get("contract") != "gold_verified_ladder_anchor_diagnostic_v1"
            or contract["account"]["currency"] != money["account_currency"]
            or anchor["scope"]["currency"] != money["account_currency"]):
        raise ValueError("full-tick source contract mismatch")
    for path in (native_deals_path, money_path, broker_contract_path, anchor_path):
        _bound(native_risk, path)
    _bound(ladder, control_path)
    broker_binding = None
    if broker_binding_path:
        broker_binding = read(broker_binding_path)
        if broker_binding.get("contract") != "gold_broker_comment_leg_binding_diagnostic_v1":
            raise ValueError("broker-comment binding contract mismatch")
        for source in (control_path, money_path, native_deals_path, shadow_slice_path,
                       shadow_manifest_path, Path(__file__).with_name("audit_gold_broker_comment_legs.py")):
            _bound(broker_binding, source)
    rows = shadow_rows(shadow_slice_path, shadow_manifest_path)
    controls, states, registered, manifest_hash = controls_and_states(rows)
    if (control["control_manifest_hash"] != manifest_hash
            or controls.get("canal2") != "gold_now_555_v1"):
        raise ValueError("Gold live-control manifest mismatch")
    comparisons = {row["signal_id"]: row for row in control["comparisons"]}
    baseline = {row["signal_id"]: row for row in native_risk["baskets"]}
    if len(baseline) != native_risk["basket_count"]:
        raise ValueError("duplicate native baseline signal")
    signals = ([row["signal_id"] for row in broker_binding["signals"]]
               if broker_binding else [row["signal_id"] for row in ladder["signals"]])
    if (not 0 < len(signals) <= MAX_SIGNALS or len(set(signals)) != len(signals)
            or any(signal not in comparisons or signal not in baseline
                   or (signal, "gold_now_555_v1") not in registered for signal in signals)):
        raise ValueError("verified full-tick signal denominator mismatch")
    by_signal = defaultdict(list)
    for position in money["positions"]:
        by_signal[position["signal_id"]].append(position)
    native_by_position = native_events(native["deals"], money["positions"])
    watched = {str(path): digest(path) for path in paths[1:]}
    start = datetime.fromisoformat(anchor["scope"]["source_epoch_start"])
    end = datetime.fromisoformat(anchor["scope"]["source_epoch_end_exclusive"])
    market = _tape(raw_dir, anchor, "XAUUSD", start, end, watched)
    conversion = _tape(raw_dir, anchor, "EURUSD", start, end, watched)
    spec = RiskSpec(money["account_currency"], contract["account"]["currency_digits"],
                    Decimal(str(contract["instrument"]["contract_size"])),
                    contract["conversion"]["orientation"],
                    contract["conversion"]["max_quote_age_ms"], MARKET_MAX_AGE_MS)
    results = []
    excluded = []
    binding_by_signal = ({row["signal_id"]: row for row in broker_binding["signals"]}
                         if broker_binding else {})
    if broker_binding and (len(binding_by_signal) != len(signals)
                           or broker_binding["comment_bound_count"] != len(signals)
                           or broker_binding["gold_native_basket_denominator"] !=
                           sum(row["channel"] == "canal2" for row in baseline.values())):
        raise ValueError("broker-comment binding coverage mismatch")
    for signal in signals:
        if time.monotonic() - started > MAX_SECONDS:
            raise TimeoutError("full-tick comparison wall budget exceeded")
        pairing = comparisons[signal]["entry_pairing"]
        positions = by_signal[signal]
        binding_status = _verified_entry_binding(signal, pairing, binding_by_signal.get(signal))
        if (len(positions) != len(pairing["pairs"])
                or baseline[signal]["status"] != "retrospective_complete"):
            raise ValueError("native baseline or causal quote path unavailable")
        if baseline[signal]["direct_clock_anchor_for_all_event_days"] is not True:
            if not broker_binding:
                raise ValueError("native baseline or causal quote path unavailable")
            excluded.append({"signal_id": signal, "entry_binding": binding_status,
                             "reason": "direct_clock_anchor_unavailable"})
            continue
        strict_fx = baseline[signal]["strict_causal_fx_path_complete"] is True
        if not strict_fx and not broker_binding:
            raise ValueError("native baseline or causal quote path unavailable")
        actual = [event for position in positions for event in native_by_position[position["position_id"]]]
        baseline_path = reconstruct_risk(actual, quote_grid(market, conversion, actual), spec=spec,
            retrospective_fx_interval_ms=contract["conversion"]["max_quote_interval_ms"])
        if summarize_path(baseline_path)["sample_stream_sha256"] != baseline[signal]["path"]["sample_stream_sha256"]:
            raise ValueError("native full-tick path differs from frozen baseline")
        slot_by_position = {row["native_position_id"]: row["leg_index"] + 1
                            for row in pairing["pairs"]}
        if set(slot_by_position) != {position["position_id"] for position in positions}:
            raise ValueError("native position-to-shadow leg binding incomplete")
        observed = [replace(event, slot=slot_by_position[event.slot]) for event in actual]
        virtual = shadow_sequence(signal, pairing, states[signal],
                                  broker_binding=binding_by_signal.get(signal))
        if sum((event.money for event in observed), Decimal(0)) != cents(baseline[signal]["native_net_eur"]):
            raise ValueError("native basket money changed")
        comparison = compare_risk(observed, virtual,
                                  quote_grid(market, conversion, (*observed, *virtual)), spec=spec)
        result = summarize_comparison(comparison)
        result.update(signal_id=signal, channel="canal2", entry_binding=binding_status,
                      native_position_count=len(positions), shadow_position_count=len(virtual) // 2,
                      observed_path_baseline_verified=True,
                      native_retrospective_fx_bracketed_marks=
                      baseline[signal]["path"]["retrospective_fx_bracketed_samples"],
                      strict_causal_fx_path_complete=strict_fx)
        if not strict_fx and (result["status"] != "blocked"
                              or "stale_conversion_quote" not in result["blockers"]
                              or result["drawdown_delta_eur"] is not None):
            raise ValueError("noncausal FX path presented as complete")
        results.append(result)
    watched[str(Path(__file__))] = digest(Path(__file__))
    if broker_binding_path:
        watched[str(broker_binding_path)] = digest(broker_binding_path)
    if any(digest(Path(name)) != sha for name, sha in watched.items()):
        raise ValueError("full-tick source changed during comparison")
    return {"contract": ("gold_broker_bound_full_tick_path_diagnostic_v2" if broker_binding
                         else "verified_gold_native_shadow_full_tick_path_diagnostic_v1"),
            "status": "diagnostic_only", "native_basket_denominator": native_risk["basket_count"],
            "gold_native_basket_denominator": sum(row["channel"] == "canal2" for row in baseline.values()),
            "verified_compared_count": sum(row["full_risk_path_comparable"] for row in results),
            "partial_compared_count": sum(not row["full_risk_path_comparable"] for row in results),
            "gold_control_count": len(signals), "excluded_controls": excluded,
            "statuses": dict(sorted(Counter(row["status"] for row in results).items())),
            "rows": results, "inputs_sha256": watched,
            "simulator_certified": False, "independent_account_equity_verified": False,
            "limitations": [
                "Virtual events are reconstructed from recorded shadow final states, not independently replayed from Telegram.",
                "Native floating and drawdown are modeled from actual deals and ticks, not independently sampled MT5 account equity.",
                "Full risk comparisons require identity-verified Gold baskets, direct clock and strict causal FX.",
                "Rows with stale causal FX compare exposure and booked money, but cannot establish full floating or drawdown.",
                "Broker-comment identity does not prove the live order-request/receipt chain.",
                "Differences at retained ticks do not bound unobserved between-tick extrema or hypothetical broker fills."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("raw-dir", "native-deals", "money", "broker-contract", "anchor",
                 "native-risk", "shadow", "shadow-manifest", "control", "ladder", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--broker-binding", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = audit(args.raw_dir, args.native_deals, args.money, args.broker_contract,
                   args.anchor, args.native_risk, args.shadow, args.shadow_manifest,
                   args.control, args.ladder, args.broker_binding)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"full_risk_compared": result["verified_compared_count"],
                      "partial_compared": result["partial_compared_count"],
                      "statuses": result["statuses"]}))


if __name__ == "__main__":
    main()
