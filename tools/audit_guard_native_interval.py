"""Pair captured MT5 guard money with strict-causal native tick intervals."""

from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.audit_native_money_anchor import cents, digest, read
from tools.audit_native_week_risk_path import (native_events, normalized_utc, quote_grid,
                                               summarize_path)
from tools.audit_native_equity_snapshots import _tape
from research.risk_trajectory import RiskSpec, reconstruct_risk
from tools.probe_vm_signal_lifecycle import utc


MAX_GUARDS = 25_000
MAX_MARKET_AGE_MS = 5_000


def compare_guard_intervals(guards, samples, deal_times):
    if len(guards) > MAX_GUARDS or not samples:
        raise ValueError("guard or model sample budget invalid")
    ordered = sorted(samples, key=lambda row: (row["at"], row["ordinal"]))
    if ordered != samples:
        raise ValueError("native model samples out of order")
    times = [row["at"] for row in samples]
    rows = []
    for guard in guards:
        row = {"decision_id": guard.get("decision_id"), "status": "blocked_guard_input"}
        try:
            if guard.get("status") != "observed":
                raise ValueError("guard sample not observed")
            tick_msc = guard["source_tick_time_msc"]
            if type(tick_msc) is not int or tick_msc <= 0:
                raise ValueError("guard source tick missing")
            lower, upper = normalized_utc(tick_msc), utc(guard["guard_evaluated_at_utc"])
            observed = cents(guard["observed_total_eur"])
            tickets = sorted(guard["open_tickets"])
            if not lower <= upper or len(tickets) != len(set(tickets)):
                raise ValueError("guard clock or ticket identity invalid")
        except (KeyError, TypeError, ValueError):
            rows.append(row)
            continue
        row.update(tick_utc=lower.isoformat(), guard_utc=upper.isoformat(),
                   observed_total_eur=str(observed), interval_ms=(upper - lower).total_seconds() * 1000)
        if row["interval_ms"] > MAX_MARKET_AGE_MS:
            row["status"] = "blocked_interval_over_market_age_budget"
        elif any(lower <= deal_at <= upper for deal_at in deal_times):
            row["status"] = "blocked_native_deal_within_interval"
        else:
            first, last = bisect_left(times, lower), bisect_right(times, upper)
            interval = samples[first:last]
            if not interval or interval[0]["at"] != lower:
                row["status"] = "blocked_source_tick_not_in_native_tape"
            elif any(mark["total"] is None or mark["blockers"] for mark in interval):
                row["status"] = "blocked_model_mark_unknown"
            elif any(sorted(int(pid) for pid, state in mark["positions"].items()
                            if state["volume"] > 0) != tickets for mark in interval):
                row["status"] = "blocked_native_position_set_mismatch"
            else:
                minimum = min(mark["total"] for mark in interval)
                maximum = max(mark["total"] for mark in interval)
                row.update(model_min_eur=str(minimum), model_max_eur=str(maximum),
                           model_mark_count=len(interval))
                if minimum <= observed <= maximum:
                    row["status"] = "within_modeled_interval"
                else:
                    row["status"] = "outside_modeled_interval"
                    row["outside_gap_eur"] = str(minimum - observed if observed < minimum
                                                 else observed - maximum)
                    row["model_marks"] = [
                        {"at": mark["at"].isoformat(), "total_eur": str(mark["total"])}
                        for mark in interval[:64]]
                    row["model_marks_truncated"] = len(interval) > 64
                    if first > 0 and samples[first - 1]["total"] is not None:
                        prior = samples[first - 1]
                        row.update(prior_model_at=prior["at"].isoformat(),
                                   prior_model_total_eur=str(prior["total"]),
                                   prior_model_age_ms=(lower - prior["at"]).total_seconds() * 1000)
                    prior_start = bisect_left(times, lower - timedelta(seconds=1))
                    prior_marks = samples[prior_start:first]
                    row["prior_model_marks"] = [
                        {"at": mark["at"].isoformat(),
                         "total_eur": str(mark["total"]) if mark["total"] is not None else None}
                        for mark in prior_marks[-32:]]
                    row["prior_model_marks_truncated"] = len(prior_marks) > 32
        rows.append(row)
    return {"contract": "guard_native_strict_interval_diagnostic_v1", "status": "diagnostic_only",
            "guard_sample_count": len(rows), "statuses": dict(sorted(Counter(row["status"] for row in rows).items())),
            "max_outside_gap_eur": str(max((Decimal(row["outside_gap_eur"]) for row in rows
                                            if "outside_gap_eur" in row), default=Decimal(0))),
            "rows": rows, "full_live_path_parity_verified": False,
            "limitations": ["A source tick and guard evaluation bound an unknown MT5 position read time.",
                            "Only retained market ticks with strictly causal FX are comparable.",
                            "Modeled money uses native fills, not an independent MT5 continuous account path."]}


def _bound(path, hashes):
    matching = [sha for name, sha in hashes.items() if Path(name).resolve() == path.resolve()]
    if len(matching) != 1 or digest(path) != matching[0]:
        raise ValueError(f"risk source not bound or changed: {path}")


def _assert_unchanged(hashes):
    for path, sha in hashes.items():
        if digest(path) != sha:
            raise ValueError(f"audit input changed during reconstruction: {path}")


def audit(*, signal, guard_path, risk_path, raw_dir, native_path, baskets_path,
          money_path, anchor_path, contract_path):
    paths = [Path(value) for value in (guard_path, risk_path, native_path, baskets_path,
                                       money_path, anchor_path, contract_path)]
    guard_path, risk_path, native_path, baskets_path, money_path, anchor_path, contract_path = paths
    raw_dir = Path(raw_dir)
    input_hashes = {path: digest(path) for path in paths + [Path(__file__)]}
    guard, risk, native, baskets, money, anchor, contract = map(read, paths)
    if (guard.get("signal_id") != signal or guard.get("status") != "diagnostic_only"
            or guard.get("management_capture", {}).get("status") != "paired"
            or guard.get("statuses") != {"matches_pure_guard": guard.get("guard_pair_count")}
            or guard.get("guard_pair_count") != guard.get("observed_guard_risk", {}).get("evaluation_count")
            or risk.get("contract") != "native_week_full_tick_risk_diagnostic_v1"
            or anchor.get("contract") != "native_tick_anchor_diagnostic_v1"
            or money.get("contract") != "native_closed_money_anchor_v2"
            or native.get("currency") != "EUR"):
        raise ValueError("guard or native risk prerequisite missing")
    guard_source_hashes = {Path(name): sha for name, sha in guard.get("input_sha256", {}).items()}
    for path, sha in guard_source_hashes.items():
        if digest(path) != sha:
            raise ValueError("captured guard segment changed")
    if not guard_source_hashes:
        raise ValueError("captured guard segment proof missing")
    for path in (native_path, baskets_path, money_path, anchor_path, contract_path,
                 Path(__file__).with_name("audit_native_week_risk_path.py"),
                 Path(__file__).with_name("audit_native_equity_snapshots.py"),
                 Path(__file__).resolve().parents[1] / "research" / "risk_trajectory.py"):
        _bound(path, risk["inputs_sha256"])
    if baskets.get("source_sha256") != digest(native_path):
        raise ValueError("native basket source changed")
    risk_rows = [row for row in risk["baskets"] if row["signal_id"] == signal]
    basket_rows = [row for row in baskets["baskets"] if row["signal_id"] == signal]
    if (len(risk_rows) != 1 or len(basket_rows) != 1
            or risk_rows[0]["status"] != "retrospective_complete"
            or risk_rows[0]["direct_clock_anchor_for_all_event_days"] is not True):
        raise ValueError("native basket clock or risk path not admitted")
    positions = [row for row in money["positions"] if row["signal_id"] == signal]
    if len(positions) != basket_rows[0]["positions"]:
        raise ValueError("native position denominator differs")
    by_position = native_events(native["deals"], positions)
    events = [event for rows in by_position.values() for event in rows]
    if not events:
        raise ValueError("native basket event sequence missing")
    day_start = datetime.combine(min(event.at for event in events).date(), datetime.min.time(), timezone.utc)
    day_end = datetime.combine(max(event.at for event in events).date() + timedelta(days=1),
                               datetime.min.time(), timezone.utc)
    watched = {}
    market = _tape(raw_dir, anchor, "XAUUSD", day_start, day_end, watched)
    conversion = _tape(raw_dir, anchor, "EURUSD", day_start, day_end, watched)
    spec = RiskSpec(money["account_currency"], contract["account"]["currency_digits"],
                    Decimal(str(contract["instrument"]["contract_size"])),
                    contract["conversion"]["orientation"],
                    contract["conversion"]["max_quote_age_ms"], MAX_MARKET_AGE_MS)
    quotes = quote_grid(market, conversion, events)
    retrospective = reconstruct_risk(events, quotes, spec=spec,
        retrospective_fx_interval_ms=contract["conversion"]["max_quote_interval_ms"])
    if summarize_path(retrospective)["sample_stream_sha256"] != risk_rows[0]["path"]["sample_stream_sha256"]:
        raise ValueError("native sample stream differs from frozen risk report")
    strict = reconstruct_risk(events, quotes, spec=spec)
    if (len(strict["samples"]) != len(retrospective["samples"])
            or any((a["at"], a["ordinal"]) != (b["at"], b["ordinal"])
                   for a, b in zip(strict["samples"], retrospective["samples"], strict=True))):
        raise ValueError("strict and retrospective risk grids differ")
    result = compare_guard_intervals(guard["observed_guard_risk"]["rows"],
                                     strict["samples"], [event.at for event in events])
    _assert_unchanged(input_hashes | guard_source_hashes | {Path(path): sha for path, sha in watched.items()})
    result.update(signal_id=signal, direct_native_clock_anchor=True,
                  strict_model_sample_count=len(strict["samples"]),
                  frozen_retrospective_model_sha256=risk_rows[0]["path"]["sample_stream_sha256"],
                  input_sha256={str(path): sha for path, sha in input_hashes.items()} | watched)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signal", required=True)
    parser.add_argument("--guard", type=Path, required=True)
    parser.add_argument("--risk", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--native-deals", type=Path, required=True)
    parser.add_argument("--native-baskets", type=Path, required=True)
    parser.add_argument("--native-money", type=Path, required=True)
    parser.add_argument("--native-anchor", type=Path, required=True)
    parser.add_argument("--broker-contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = audit(signal=args.signal, guard_path=args.guard, risk_path=args.risk,
                   raw_dir=args.raw_dir, native_path=args.native_deals,
                   baskets_path=args.native_baskets, money_path=args.native_money,
                   anchor_path=args.native_anchor, contract_path=args.broker_contract)
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(result, target, indent=2, ensure_ascii=True, allow_nan=False)
        target.write("\n")
    print(json.dumps({"signal_id": args.signal, "guard_samples": result["guard_sample_count"],
                      "statuses": result["statuses"]}))


if __name__ == "__main__":
    main()
