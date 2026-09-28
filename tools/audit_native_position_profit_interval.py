"""Compare native position.profit with causal quote valuations before its journal event.

The MT5 positions_get acquisition time was not logged. A match inside a bounded
prior-quote interval supports the valuation formula, not exact-time parity.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from broker_money import convert_profit_amount
from tools.audit_native_equity_snapshots import _tape
from tools.audit_native_money_anchor import cents, digest, read


MAX_PROBES = 10
MAX_SNAPSHOTS = 1_000
PRIOR_WINDOW_MS = 5_000
NATIVE_DETAIL_FIELDS = {"symbol", "magic", "position_type", "price_open", "price_current"}


def _utc_ms(value):
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
        raise ValueError("native snapshot requires explicit UTC")
    delta = stamp - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86_400 + delta.seconds) * 1000 + delta.microseconds // 1000


def direct_same_day_offset(event_utc, anchor_days):
    utc_ms = _utc_ms(event_utc)
    day = datetime.fromtimestamp(utc_ms // 1000, timezone.utc).date().isoformat()
    clock = anchor_days.get(day, {})
    offset = clock.get("offset_seconds")
    if clock.get("status") != "direct_anchor_available" or type(offset) is not int:
        raise ValueError("native snapshot lacks a direct clock anchor")
    source_day = datetime.fromtimestamp(utc_ms // 1000 + offset, timezone.utc).date().isoformat()
    if source_day != day:
        raise ValueError("native snapshot crosses a clock-anchor day")
    return offset


def match_snapshot(snapshot, position, market, conversion, *, offset_seconds,
                   contract_size, max_fx_age_ms, symbol=None, window_ms=PRIOR_WINDOW_MS):
    if (snapshot.get("ticket") != position["position_id"]
            or snapshot.get("sig") != position["signal_id"]):
        raise ValueError("snapshot and native position identity mismatch")
    if snapshot.get("volume") is not None and Decimal(str(snapshot["volume"])) != Decimal(str(position["volume"])):
        raise ValueError("snapshot and native position volume mismatch")
    if position["direction"] not in ("BUY", "SELL") or type(offset_seconds) is not int:
        raise ValueError("invalid native direction or clock offset")
    has_detail = bool(NATIVE_DETAIL_FIELDS & snapshot.keys())
    if has_detail:
        if (not NATIVE_DETAIL_FIELDS <= snapshot.keys()
                or snapshot.get("position_exists") is not True
                or snapshot["symbol"] != symbol
                or type(snapshot["magic"]) is not int
                or type(snapshot["position_type"]) is not int
                or snapshot["position_type"] != (0 if position["direction"] == "BUY" else 1)
                or Decimal(str(snapshot["price_open"])) != Decimal(str(position["entry_price"]))
                or Decimal(str(snapshot["price_current"])) <= 0):
            raise ValueError("native position detail identity or entry mismatch")
    at = _utc_ms(snapshot["ts"]) + offset_seconds * 1000
    result = {"signal_id": snapshot["sig"], "event_id": snapshot["event_id"],
              "ticket": snapshot["ticket"], "event_utc": snapshot["ts"],
              "read_time_observed": False, "prior_window_ms": window_ms}
    if snapshot.get("profit") is None:
        return {**result, "status": "blocked_missing_native_profit"}
    if (type(window_ms) is not int or not 0 < window_ms <= 10_000
            or type(max_fx_age_ms) is not int or max_fx_age_ms <= 0):
        raise ValueError("invalid quote age or prior-window contract")
    native_profit = cents(snapshot["profit"])
    times, bid, ask = market
    fx_times, fx_bid, fx_ask = conversion
    begin = max(position["entry_msc"], at - window_ms)
    end = min(at, position["exit_msc"] - 1)
    values, ages, mark_ages, mark_profit_ages = [], [], [], []
    if begin <= end:
        for index in range(int(np.searchsorted(times, begin, side="left")),
                           int(np.searchsorted(times, end, side="right"))):
            fx_index = int(np.searchsorted(fx_times, times[index], side="right")) - 1
            if fx_index < 0 or times[index] - fx_times[fx_index] > max_fx_age_ms:
                continue
            side = bid[index] if position["direction"] == "BUY" else ask[index]
            raw = ((Decimal(str(side)) - Decimal(str(position["entry_price"])))
                   * (1 if position["direction"] == "BUY" else -1)
                   * Decimal(str(position["volume"])) * Decimal(str(contract_size)))
            rate = Decimal(str(fx_ask[fx_index] if raw >= 0 else fx_bid[fx_index]))
            value = convert_profit_amount(raw, rate, orientation="account_base_profit_quote",
                                          currency_digits=2)
            values.append(value)
            if value == native_profit:
                ages.append(at - int(times[index]))
            if has_detail and Decimal(str(side)) == Decimal(str(snapshot["price_current"])):
                mark_ages.append(at - int(times[index]))
                if value == native_profit:
                    mark_profit_ages.append(at - int(times[index]))
    mark_result = ({"native_mark_quote_count": len(mark_ages),
                    "native_mark_profit_match_count": len(mark_profit_ages),
                    "native_mark_latest_matching_quote_age_ms": min(mark_profit_ages) if mark_profit_ages else None,
                    "native_mark_status": ("native_mark_profit_matches_same_prior_quote"
                                           if mark_profit_ages else "native_mark_profit_not_reproduced")}
                   if has_detail else {})
    if not values:
        return {**result, "native_profit_eur": str(native_profit),
                "status": "blocked_no_causal_quote_in_prior_window", "candidate_quote_count": 0,
                **({**mark_result, "native_mark_status": "blocked_no_causal_quote_in_prior_window"}
                   if has_detail else {})}
    return {**result, "native_profit_eur": str(native_profit),
            "candidate_quote_count": len(values), "candidate_value_min_eur": str(min(values)),
            "candidate_value_max_eur": str(max(values)),
            "exact_match_quote_count": len(ages),
            "latest_matching_quote_age_ms": min(ages) if ages else None,
            "nearest_abs_difference_eur": str(min(abs(native_profit - value) for value in values)),
            **mark_result,
            "status": ("native_profit_matches_prior_causal_quote"
                       if ages else "native_profit_not_reproduced_in_prior_window")}


def audit(probe_paths, money_path, anchor_path, contract_path, raw_dir):
    probe_paths = tuple(map(Path, probe_paths))
    money_path, anchor_path, contract_path, raw_dir = map(
        Path, (money_path, anchor_path, contract_path, raw_dir))
    money, anchor, contract = map(read, (money_path, anchor_path, contract_path))
    if (not probe_paths or len(probe_paths) > MAX_PROBES
            or money.get("contract") != "native_closed_money_anchor_v2"
            or anchor.get("contract") != "native_tick_anchor_diagnostic_v1"
            or contract["account"]["currency"] != "EUR"
            or contract["account"]["currency_digits"] != 2
            or contract["conversion"]["orientation"] != "account_base_profit_quote"):
        raise ValueError("native position profit source contract mismatch")
    watched = {str(path): digest(path) for path in
               (*probe_paths, money_path, anchor_path, contract_path, Path(__file__))}
    for path in (anchor_path, contract_path):
        bound = [sha for name, sha in money["inputs_sha256"].items()
                 if Path(name).resolve() == path.resolve()]
        if bound != [watched[str(path)]]:
            raise ValueError("native money source binding changed")
    start = datetime.fromisoformat(anchor["scope"]["source_epoch_start"])
    end = datetime.fromisoformat(anchor["scope"]["source_epoch_end_exclusive"])
    market = _tape(raw_dir, anchor, "XAUUSD", start, end, watched)
    conversion = _tape(raw_dir, anchor, "EURUSD", start, end, watched)
    positions = {row["position_id"]: row for row in money["positions"]}
    if len(positions) != len(money["positions"]):
        raise ValueError("duplicate native position")
    rows, seen = [], set()
    for path in probe_paths:
        probe = read(path)
        if (probe.get("contract") != "bounded_vm_signal_lifecycle_probe_v1"
                or probe.get("status") != "diagnostic_only"):
            raise ValueError("bounded native snapshot probe contract mismatch")
        selected = [row for row in probe["events"] if row.get("ev") == "mt5_position_snapshot"]
        if len(selected) != probe["event_counts"].get("mt5_position_snapshot", 0):
            raise ValueError("native snapshot probe count truncated")
        for snapshot in selected:
            if snapshot["event_id"] in seen or snapshot["sig"] != probe["window"]["signal_id"]:
                raise ValueError("duplicate or mixed native snapshot")
            seen.add(snapshot["event_id"])
            ticket = snapshot.get("ticket")
            if ticket not in positions:
                raise ValueError("native snapshot ticket is unbound")
            offset = direct_same_day_offset(snapshot["ts"], anchor["independent_clock_evidence"]["days"])
            rows.append(match_snapshot(snapshot, positions[ticket], market, conversion,
                                       offset_seconds=offset,
                                       contract_size=contract["instrument"]["contract_size"],
                                       max_fx_age_ms=contract["conversion"]["max_quote_age_ms"],
                                       symbol=contract["instrument"]["symbol"]))
            if len(rows) > MAX_SNAPSHOTS:
                raise ValueError("native snapshot budget exceeded")
    if any(digest(Path(name)) != sha for name, sha in watched.items()):
        raise ValueError("native position profit source changed during audit")
    return {"contract": "native_position_profit_prior_quote_diagnostic_v1",
            "status": "diagnostic_only", "snapshot_count": len(rows),
            "statuses": dict(sorted(Counter(row["status"] for row in rows).items())),
            "native_mark_statuses": dict(sorted(Counter(row["native_mark_status"] for row in rows
                                                       if "native_mark_status" in row).items())),
            "rows": rows, "inputs_sha256": watched,
            "full_floating_path_parity_verified": False,
            "limitations": ["Snapshot journal time follows positions_get; exact acquisition time was not logged.",
                            "A matching prior quote is a bounded consistency check, not proof of the actual quote used by MT5.",
                            "Native entry price substitutes for omitted snapshot price_open in old probes.",
                            "This is per-position profit, not independent continuous account equity or drawdown."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=Path, action="append", required=True)
    parser.add_argument("--money", type=Path, required=True)
    parser.add_argument("--anchor", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = audit(args.probe, args.money, args.anchor, args.contract, args.raw_dir)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"snapshots": result["snapshot_count"],
                      "statuses": result["statuses"]}), flush=True)


if __name__ == "__main__":
    main()
