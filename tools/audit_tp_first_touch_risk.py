"""Risk cost of mechanically closing at the first post-response TP touch.

This is a conditioned counterfactual with actual entries, target exits and
money held fixed. It is not a prospective broker-fill model or live equity.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.risk_trajectory import RiskSpec, compare_risk
from tools.audit_native_equity_snapshots import _tape
from tools.audit_native_money_anchor import cents, digest, read
from tools.audit_native_week_risk_path import native_events, quote_grid, _jsonable


MAX_SIGNALS = 20
MARKET_MAX_AGE_MS = 5_000


def _utc_msc(value):
    seconds, millis = divmod(value, 1000)
    return datetime.fromtimestamp(seconds, timezone.utc) + timedelta(milliseconds=millis)


def early_exit_events(by_position, touch_rows):
    result = []
    seen = set()
    for row in touch_rows:
        ticket = row["native_position_id"]
        if ticket in seen or ticket not in by_position:
            raise ValueError("duplicate or unbound TP ticket")
        seen.add(ticket)
        pair = by_position[ticket]
        if len(pair) != 2 or pair[0].kind != "entry" or pair[1].kind != "exit":
            raise ValueError("native entry/exit pair unavailable")
        first = row["first_target_touch_utc_msc"]
        if type(first) is not int or row["status"] != "retained_touch_precedes_native_tp_exit":
            raise ValueError("first retained TP touch missing")
        at = _utc_msc(first)
        if (not pair[0].at < at < pair[1].at
                or pair[1].price != Decimal(str(row["target_level"]))):
            raise ValueError("first TP touch or native target chronology invalid")
        result.extend((pair[0], replace(pair[1], at=at,
                                        mechanism="hypothetical_first_post_response_touch")))
    return result


def audit(postack_path, money_path, anchor_path, native_path, baskets_path,
          contract_path, baseline_path, raw_dir):
    postack_path, money_path, anchor_path, native_path, baskets_path, contract_path, baseline_path, raw_dir = map(
        Path, (postack_path, money_path, anchor_path, native_path, baskets_path,
               contract_path, baseline_path, raw_dir))
    postack, money, anchor, native, baskets, contract, baseline = map(
        read, (postack_path, money_path, anchor_path, native_path,
               baskets_path, contract_path, baseline_path))
    if (postack.get("contract") != "tp_response_to_native_exit_diagnostic_v1"
            or money.get("contract") != "native_closed_money_anchor_v2"
            or anchor.get("contract") != "native_tick_anchor_diagnostic_v1"
            or baskets.get("source_sha256") != digest(native_path)
            or baseline.get("contract") != "native_week_full_tick_risk_diagnostic_v1"
            or len(postack.get("rows", [])) != postack.get("leg_count")
            or len({row["signal_id"] for row in postack["rows"]}) > MAX_SIGNALS):
        raise ValueError("TP risk inputs or bounded cohort mismatch")
    watched = {str(path): digest(path) for path in
               (postack_path, money_path, anchor_path, native_path, baskets_path,
                contract_path, baseline_path, Path(__file__), Path(sys.modules[RiskSpec.__module__].__file__),
                Path(sys.modules[quote_grid.__module__].__file__))}
    for name, expected in postack["inputs_sha256"].items():
        if digest(Path(name)) != expected:
            raise ValueError(f"post-response source changed: {name}")
    for path in (native_path, baskets_path, contract_path, anchor_path):
        bound = [sha for name, sha in money["inputs_sha256"].items()
                 if Path(name).resolve() == path.resolve()]
        if bound != [watched[str(path)]]:
            raise ValueError(f"native risk input not bound: {path}")
    for name, expected in baseline["inputs_sha256"].items():
        if digest(Path(name)) != expected:
            raise ValueError(f"native risk baseline source changed: {name}")
    start = datetime.fromisoformat(anchor["scope"]["source_epoch_start"])
    end = datetime.fromisoformat(anchor["scope"]["source_epoch_end_exclusive"])
    market = _tape(raw_dir, anchor, "XAUUSD", start, end, watched)
    conversion = _tape(raw_dir, anchor, "EURUSD", start, end, watched)
    spec = RiskSpec(money["account_currency"], contract["account"]["currency_digits"],
                    Decimal(str(contract["instrument"]["contract_size"])),
                    contract["conversion"]["orientation"],
                    contract["conversion"]["max_quote_age_ms"], MARKET_MAX_AGE_MS)
    if spec.currency != "EUR" or spec.orientation != "account_base_profit_quote":
        raise ValueError("unexpected account money contract")
    positions_by_signal = defaultdict(list)
    for position in money["positions"]:
        positions_by_signal[position["signal_id"]].append(position)
    touches_by_signal = defaultdict(list)
    for row in postack["rows"]:
        touches_by_signal[row["signal_id"]].append(row)
    native_by_position = native_events(native["deals"], money["positions"])
    basket_by_signal = {row["signal_id"]: row for row in baskets["baskets"]}
    baseline_by_signal = {row["signal_id"]: row for row in baseline["baskets"]}
    if len(basket_by_signal) != len(baskets["baskets"]):
        raise ValueError("duplicate native basket")
    if len(baseline_by_signal) != len(baseline["baskets"]):
        raise ValueError("duplicate native risk baseline basket")
    reports = []
    for signal, touch_rows in sorted(touches_by_signal.items()):
        positions = positions_by_signal[signal]
        if (signal not in basket_by_signal or len(positions) != len(touch_rows)
                or {row["position_id"] for row in positions}
                != {row["native_position_id"] for row in touch_rows}):
            raise ValueError("basket and TP touch leg sets differ")
        observed = [event for position in positions for event in native_by_position[position["position_id"]]]
        early = early_exit_events(native_by_position, touch_rows)
        if (sum((event.money for event in observed), Decimal(0))
                != cents(basket_by_signal[signal]["net_eur"])):
            raise ValueError("native basket money not reconciled")
        quotes = quote_grid(market, conversion, observed)
        compared = compare_risk(observed, early, quotes, spec=spec)
        actual = compared["observed"]["metrics"]
        hypothetical = compared["simulated"]["metrics"]
        frozen = baseline_by_signal.get(signal)
        if (frozen is None or frozen.get("status") != "retrospective_complete"
                or frozen.get("strict_causal_fx_path_complete") is not True
                or _jsonable(actual) != frozen["path"]["metrics"]):
            raise ValueError("actual modeled risk differs from frozen weekly baseline")
        if (actual is not None and hypothetical is not None
                and actual["final_net"] != hypothetical["final_net"]):
            raise ValueError("mechanical early close changed final net")
        first = compared["first_divergence"]
        reports.append({"signal_id": signal, "position_count": len(positions),
                        "status": compared["status"], "blockers": compared["blockers"],
                        "first_divergence_at": first["at"] if first else None,
                        "first_divergence_fields": first["differences"] if first else [],
                        "mismatched_grid_pairs": compared["mismatched_pairs"],
                        "unknown_grid_pairs": compared["unknown_pairs"],
                        "max_abs_total_difference_eur": compared["max_abs_total_difference"],
                        "actual_metrics": actual, "mechanical_early_close_metrics": hypothetical,
                        "actual_metrics_match_frozen_weekly_baseline": True})
    if any(digest(Path(name)) != sha for name, sha in watched.items()):
        raise ValueError("TP risk sources changed during reconstruction")
    return _jsonable({"contract": "tp_first_post_response_touch_risk_counterfactual_v1",
                     "status": "diagnostic_only", "basket_count": len(reports),
                     "baskets": reports, "inputs_sha256": watched,
                     "limitations": ["Actual fills, target price and booked money are held fixed; only exit time changes.",
                                     "The first retained target touch is not a verified broker fill opportunity.",
                                     "This is per-basket modeled EUR risk, not independent MT5 account equity.",
                                     "Quote gaps or stale FX block a comparison rather than being interpolated."]})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("postack", "money", "anchor", "native", "baskets", "contract",
                 "baseline-risk", "raw-dir", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = audit(args.postack, args.money, args.anchor, args.native,
                   args.baskets, args.contract, args.baseline_risk, args.raw_dir)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"baskets": result["basket_count"],
                      "statuses": {row["signal_id"]: row["status"] for row in result["baskets"]}}), flush=True)


if __name__ == "__main__":
    main()
