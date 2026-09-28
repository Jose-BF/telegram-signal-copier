"""Audit native deal/tick alignment without admitting a UTC clock or simulator."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import statistics

import numpy as np
import pandas as pd


OFFSETS_SECONDS = (0, 3600, 7200, 10800)
MAX_TICK_ROWS_PER_DAY = 2_000_000
MAX_NATIVE_DEALS = 10_000


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path):
    path = Path(path)
    if path.stat().st_size > 16_000_000:
        raise ValueError("JSON input byte budget exceeded")
    return json.loads(path.read_text(encoding="utf-8"))


def _utc_msc(value):
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
        raise ValueError("explicit UTC receipt required")
    return int(stamp.timestamp() * 1000)


def _summary(values):
    if not values:
        return None
    ordered = sorted(values)
    return {"min": ordered[0], "median": statistics.median(ordered),
            "p90": ordered[int((len(ordered) - 1) * .9)], "max": ordered[-1]}


def _clock_evidence(path, *, server, currency, start, end, by_day):
    source = read_json(path)
    account, instrument = source.get("account", {}), source.get("instrument", {})
    if (source.get("schema_version") != 2 or account.get("server") != server
            or account.get("currency") != currency or instrument.get("symbol") != "XAUUSD"
            or len(source.get("swap_snapshots", [])) > 10_000):
        raise ValueError("broker clock evidence identity or budget mismatch")
    days = {}
    day = start
    while day < end:
        days[day.date().isoformat()] = {"status": "no_direct_anchor", "anchors": [], "other_snapshots": []}
        day += timedelta(days=1)
    for snapshot in source["swap_snapshots"]:
        evidence = snapshot.get("time_evidence", {})
        if (snapshot.get("account_server") != server or snapshot.get("instrument_symbol") != "XAUUSD"):
            raise ValueError("broker clock snapshot identity mismatch")
        server_epoch, gmt_epoch, offset = (evidence.get(key) for key in
                                           ("captured_server_epoch", "captured_gmt_epoch", "utc_offset_seconds"))
        if any(type(value) is not int for value in (server_epoch, gmt_epoch, offset)):
            raise ValueError("invalid broker clock snapshot epoch")
        label = datetime.fromtimestamp(server_epoch, timezone.utc).date().isoformat()
        if label not in days:
            continue
        captured = datetime.fromisoformat(snapshot["captured_at_utc"])
        if (captured.tzinfo is None or abs(captured.timestamp() - gmt_epoch) > 1
                or type(evidence.get("evidence_age_seconds")) not in (int, float)
                or not 0 <= evidence["evidence_age_seconds"] <= 300
                or server_epoch - gmt_epoch != offset or abs(offset) > 14 * 3600
                or evidence.get("server_tick_lag_seconds") != server_epoch - evidence.get("last_server_tick_epoch", -1)):
            raise ValueError("contradictory broker clock snapshot")
        row = {"captured_at_utc": captured.astimezone(timezone.utc).isoformat(),
               "evidence_sha256": evidence.get("evidence_sha256"), "offset_seconds": offset,
               "python_tick_time_basis": evidence.get("python_tick_time_basis"),
               "server_tick_lag_seconds": evidence["server_tick_lag_seconds"]}
        if (evidence.get("source") != "mql5_service_v1" or evidence.get("market_session_open") is not True
                or evidence.get("mql_tick_fresh") is not True
                or evidence.get("python_tick_time_basis") != "broker_server_epoch"
                or evidence["server_tick_lag_seconds"] > 2):
            row["reason"] = "not_active_independent_clock_anchor"
            days[label]["other_snapshots"].append(row)
            continue
        python_epoch = evidence.get("python_tick_epoch")
        if type(python_epoch) is not int:
            raise ValueError("invalid Python tick epoch in clock evidence")
        times = by_day[label][0]
        index = int(np.searchsorted(times, python_epoch * 1000 + 999, side="right")) - 1
        row["raw_tick_age_ms_at_python_second_end"] = (
            python_epoch * 1000 + 999 - int(times[index]) if index >= 0 else None)
        if index < 0 or row["raw_tick_age_ms_at_python_second_end"] > 5_000:
            row["reason"] = "no_nearby_historical_tick"
            days[label]["other_snapshots"].append(row)
            continue
        days[label]["status"] = "direct_anchor_available"
        days[label]["anchors"].append(row)
    for label, row in days.items():
        offsets = {item["offset_seconds"] for item in row["anchors"]}
        if len(offsets) > 1:
            raise ValueError(f"conflicting direct clock anchors: {label}")
        row["offset_seconds"] = next(iter(offsets)) if offsets else None
    return {"days": days, "direct_days": sum(row["status"] == "direct_anchor_available" for row in days.values()),
            "unanchored_days": [label for label, row in days.items() if row["status"] != "direct_anchor_available"],
            "scope": "independent_same_server_clock_snapshots_not_full_historical_utc_admission"}


def audit(raw_dir, native_deals_path, native_baskets_path, inventory_path, *, clock_contract_path=None):
    raw_dir, native_deals_path, native_baskets_path, inventory_path = map(
        Path, (raw_dir, native_deals_path, native_baskets_path, inventory_path))
    contract = read_json(raw_dir / "contract.json")
    binding = read_json(raw_dir / "binding.json")
    native = read_json(native_deals_path)
    baskets = read_json(native_baskets_path)
    inventory = read_json(inventory_path)
    if (contract.get("schema_version") != "raw_isolated_mt5_history_v1"
            or contract.get("clock_admitted") is not False
            or contract.get("engine_dataset_ready") is not False
            or binding.get("trade_allowed") is not False
            or binding.get("tradeapi_disabled") is not True
            or contract.get("server") != binding.get("server")
            or native.get("server") != binding.get("server")
            or native.get("account_binding_sha256") != binding.get("account_binding_sha256")
            or native.get("currency") != binding.get("currency")
            or baskets.get("currency") != binding.get("currency")
            or baskets.get("source_sha256") != digest(native_deals_path)
            or inventory.get("scope") is None):
        raise ValueError("raw/native identity or read-only contract mismatch")
    inputs = {str(path): digest(path) for path in
              (raw_dir / "contract.json", raw_dir / "binding.json", native_deals_path,
               native_baskets_path, inventory_path, Path(__file__))}
    start = datetime.fromisoformat(contract["source_epoch_start"])
    end = datetime.fromisoformat(contract["source_epoch_end_exclusive"])
    if (start.tzinfo is None or end.tzinfo is None or start.utcoffset() != timedelta(0)
            or end.utcoffset() != timedelta(0) or not start < end
            or end - start > timedelta(days=14)):
        raise ValueError("bounded source-epoch days required")
    by_day, symbol_days = {}, Counter()
    for symbol in ("XAUUSD", "EURUSD"):
        day = start
        while day < end:
            label = day.date().isoformat()
            meta_path = raw_dir / symbol / f"{label}.json"
            meta = read_json(meta_path)
            if (meta.get("status") != "raw_reads_consistent" or meta.get("reads_consistent") is not True
                    or meta.get("symbol") != symbol or meta.get("server") != binding["server"]
                    or meta.get("clock_admitted") is not False
                    or not 0 < meta.get("rows", 0) <= MAX_TICK_ROWS_PER_DAY
                    or meta.get("artifact") != f"{label}.parquet"):
                raise ValueError(f"unadmitted or incomplete raw day: {symbol}/{label}")
            data_path = meta_path.with_suffix(".parquet")
            if digest(data_path) != meta["sha256"] or data_path.stat().st_size != meta["bytes"]:
                raise ValueError(f"raw day bytes changed: {symbol}/{label}")
            frame = pd.read_parquet(data_path, columns=["time_msc", "bid", "ask"])
            if (len(frame) != meta["rows"] or not pd.api.types.is_integer_dtype(frame.time_msc)
                    or frame.isna().any().any()):
                raise ValueError(f"raw day schema/count mismatch: {symbol}/{label}")
            times = frame.time_msc.to_numpy(dtype=np.int64)
            bid, ask = frame.bid.to_numpy(dtype=float), frame.ask.to_numpy(dtype=float)
            lower, upper = int(day.timestamp() * 1000), int((day + timedelta(days=1)).timestamp() * 1000)
            if (np.any(np.diff(times) < 0) or times[0] < lower or times[-1] >= upper
                    or not np.isfinite(bid).all() or not np.isfinite(ask).all()
                    or np.any(bid <= 0) or np.any(ask < bid)):
                raise ValueError(f"raw quote order/price invalid: {symbol}/{label}")
            if symbol == "XAUUSD":
                by_day[label] = (times, bid, ask)
            symbol_days[symbol] += 1
            inputs[str(meta_path)], inputs[str(data_path)] = digest(meta_path), meta["sha256"]
            day += timedelta(days=1)

    deals = native.get("deals", [])
    if len(deals) > MAX_NATIVE_DEALS:
        raise ValueError("native deal budget exceeded")
    rows, blockers = [], Counter()
    for deal in deals:
        if deal.get("symbol") != "XAUUSD":
            continue
        row = {"ticket": deal.get("ticket"), "position_id": deal.get("position_id"),
               "time_msc": deal.get("time_msc"), "deal_side": deal.get("type"),
               "entry_kind": deal.get("entry"), "reason": deal.get("reason"),
               "fill_price": deal.get("price")}
        msc = row["time_msc"]
        if (type(msc) is not int or type(row["ticket"]) is not int
                or row["deal_side"] not in (0, 1) or row["entry_kind"] not in (0, 1)
                or type(row["fill_price"]) not in (int, float) or row["fill_price"] <= 0):
            row["blocker"] = "invalid_native_deal_schema"
        else:
            label = datetime.fromtimestamp(msc / 1000, timezone.utc).date().isoformat()
            row["source_epoch_day"] = label
            tape = by_day.get(label)
            if tape is None:
                row["blocker"] = "source_epoch_day_not_extracted"
            else:
                times, bid, ask = tape
                index = int(np.searchsorted(times, msc, side="right")) - 1
                if index < 0:
                    row["blocker"] = "no_causal_prior_tick"
                else:
                    price = float(ask[index] if row["deal_side"] == 0 else bid[index])
                    side = ask if row["deal_side"] == 0 else bid
                    minute_start = int(np.searchsorted(times, msc - 60_000, side="left"))
                    minute = side[minute_start:index + 1]
                    row.update(prior_tick_msc=int(times[index]), prior_age_ms=msc - int(times[index]),
                               prior_executable_price=price,
                               causal_minute_low=float(minute.min()),
                               causal_minute_high=float(minute.max()),
                               fill_minus_prior=round(float(row["fill_price"]) - price, 5))
        if "blocker" in row:
            blockers[row["blocker"]] += 1
        rows.append(row)

    catalog = {item["signal_id"]: item for item in inventory.get("signals", [])}
    if len(catalog) != len(inventory.get("signals", [])):
        raise ValueError("duplicate signal identity in inventory")
    receipts, missing = [], []
    for basket in baskets.get("baskets", []):
        signal = catalog.get(basket["signal_id"])
        positions = [row for row in baskets.get("positions", []) if row["signal_id"] == basket["signal_id"]]
        if (signal is None or signal.get("receipt", {}).get("observed") is not True
                or len(positions) != basket["positions"]):
            missing.append({"signal_id": basket["signal_id"], "reason": "receipt_or_native_positions_missing"})
            continue
        native_first = min(row["first_native_msc"] for row in positions)
        receipt_msc = _utc_msc(signal["receipt"]["event_utc"])
        receipts.append({"signal_id": basket["signal_id"], "channel": basket["channel"],
                         "receipt_utc_msc": receipt_msc, "first_native_source_msc": native_first,
                         "raw_difference_ms": native_first - receipt_msc,
                         "residual_ms_by_offset": {str(offset): native_first - offset * 1000 - receipt_msc
                                                  for offset in OFFSETS_SECONDS}})
    offset_checks = {str(offset): {
        "negative_before_receipt": sum(row["residual_ms_by_offset"][str(offset)] < 0 for row in receipts),
        "residual_ms": _summary([row["residual_ms_by_offset"][str(offset)] for row in receipts])}
        for offset in OFFSETS_SECONDS}
    aligned = [row for row in rows if "blocker" not in row]
    report = {"contract": "native_tick_anchor_diagnostic_v1", "status": "diagnostic_only",
              "clock_admitted": False, "engine_dataset_ready": False,
              "native_execution_parity_verified": False, "input_sha256": inputs,
              "scope": {"source_epoch_start": contract["source_epoch_start"],
                        "source_epoch_end_exclusive": contract["source_epoch_end_exclusive"],
                        "server": binding["server"], "currency": binding["currency"],
                        "verified_symbol_days": dict(symbol_days)},
              "deals": rows, "deal_blockers": dict(blockers),
              "deal_summary": {"native_xauusd": len(rows), "causal_prior": len(aligned),
                               "prior_age_ms": _summary([row["prior_age_ms"] for row in aligned]),
                               "absolute_fill_minus_prior": _summary([abs(row["fill_minus_prior"]) for row in aligned])},
              "receipts": receipts, "missing_receipts": missing, "offset_checks": offset_checks,
              "limitations": ["Broker tick/deal source epoch is not independently normalized to UTC.",
                              "Historical quotes are not a proof of exact execution price or latency.",
                              "No strategy decisions, protection path, floating P/L or drawdown are compared."]}
    if clock_contract_path is not None:
        clock_contract_path = Path(clock_contract_path)
        inputs[str(clock_contract_path)] = digest(clock_contract_path)
        report["independent_clock_evidence"] = _clock_evidence(
            clock_contract_path, server=binding["server"], currency=binding["currency"],
            start=start, end=end, by_day=by_day)
        report["limitations"][0] = "Direct clock anchors cover only listed days; the historical UTC clock remains unadmitted."
    for path, sha in inputs.items():
        if digest(path) != sha:
            raise ValueError("audit input changed during run")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--native-deals", type=Path, required=True)
    parser.add_argument("--native-baskets", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--clock-contract", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("immutable audit output already exists")
    report = audit(args.raw_dir, args.native_deals, args.native_baskets, args.inventory,
                   clock_contract_path=args.clock_contract)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"output": str(args.output), "deals": report["deal_summary"],
                      "receipts": len(report["receipts"]), "offsets": report["offset_checks"]}), flush=True)


if __name__ == "__main__":
    main()
