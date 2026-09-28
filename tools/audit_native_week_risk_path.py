"""Reconstruct full retained-tick basket risk from actual MT5 fills, offline only."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
import time

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.causal_comparison import SequenceEvent
from research.risk_trajectory import RiskQuote, RiskSpec, reconstruct_risk
from tools.audit_native_equity_snapshots import _tape
from tools.audit_native_money_anchor import cents, digest, read


MAX_BASKETS = 200
MAX_SECONDS = 600
OFFSET_SECONDS = 10_800
MARKET_MAX_AGE_MS = 5_000


def normalized_utc(source_msc):
    seconds, millis = divmod(int(source_msc), 1000)
    return datetime.fromtimestamp(seconds - OFFSET_SECONDS, timezone.utc) + timedelta(milliseconds=millis)


def source_msc(stamp):
    delta = stamp - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86_400 + delta.seconds + OFFSET_SECONDS) * 1000 + delta.microseconds // 1000


def native_events(deals, position_rows):
    by_position = defaultdict(list)
    for deal in deals:
        if deal.get("symbol") == "XAUUSD":
            by_position[deal["position_id"]].append(deal)
    events = {}
    for position in position_rows:
        pid = position["position_id"]
        rows = by_position.get(pid, [])
        if (len(rows) != 2 or {row["ticket"] for row in rows} != set(position["deal_tickets"])
                or {row["entry"] for row in rows} != {0, 1}):
            raise ValueError(f"native position shape or identity unavailable: {pid}")
        entry = next(row for row in rows if row["entry"] == 0)
        exit_deal = next(row for row in rows if row["entry"] == 1)
        if (entry["type"] not in (0, 1) or exit_deal["type"] != 1 - entry["type"]
                or entry["time_msc"] > exit_deal["time_msc"]
                or Decimal(str(entry["volume"])) != Decimal(str(exit_deal["volume"]))
                or any(cents(row["swap"]) for row in rows)):
            raise ValueError(f"native position direction, volume or swap unsupported: {pid}")
        direction = "BUY" if entry["type"] == 0 else "SELL"
        pair = []
        for row, kind in ((entry, "entry"), (exit_deal, "exit")):
            money = sum((cents(row[key]) for key in ("profit", "commission", "swap", "fee")), Decimal(0))
            pair.append(SequenceEvent(pid, kind, normalized_utc(row["time_msc"]), direction,
                                      Decimal(str(row["price"])), Decimal(str(row["volume"])), money,
                                      f"native_reason_{row['reason']}"))
        if sum((row.money for row in pair), Decimal(0)) != cents(position["actual_net_eur"]):
            raise ValueError(f"native money not reconciled for risk path: {pid}")
        events[pid] = pair
    return events


def quote_grid(market, conversion, events):
    """Keep every XAU tick plus fill boundaries; FX is prior at each mark."""
    times, bid, ask = market
    fx_times, fx_bid, fx_ask = conversion
    earliest = min(row.at for row in events)
    latest = max(row.at for row in events)
    first_msc = source_msc(earliest)
    last_msc = source_msc(latest)
    first = max(0, int(np.searchsorted(times, first_msc, side="left")) - 1)
    last = int(np.searchsorted(times, last_msc, side="right"))
    marks = [(int(times[index]), index) for index in range(first, last)]
    present = {stamp for stamp, _ in marks}
    for row in events:
        stamp = source_msc(row.at)
        if stamp not in present:
            index = int(np.searchsorted(times, stamp, side="right")) - 1
            if index >= 0:
                marks.append((stamp, index))
            present.add(stamp)
    marks.sort(key=lambda item: item[0])
    quotes = []
    for stamp, index in marks:
        fx_index = int(np.searchsorted(fx_times, stamp, side="right")) - 1
        prior_fx = normalized_utc(fx_times[fx_index]) if fx_index >= 0 else None
        next_fx = normalized_utc(fx_times[fx_index + 1]) if 0 <= fx_index < len(fx_times) - 1 else None
        quotes.append(RiskQuote(normalized_utc(stamp), bid[index], ask[index], prior_fx,
                                fx_bid[fx_index] if fx_index >= 0 else None,
                                fx_ask[fx_index] if fx_index >= 0 else None,
                                market_at=normalized_utc(times[index]), conversion_next_at=next_fx))
    return quotes


def _jsonable(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def summarize_path(path):
    digestor = hashlib.sha256()
    peak, peak_at, worst, worst_at, maximum_dd = Decimal(0), None, Decimal(0), None, Decimal(0)
    dd_peak_at, dd_trough_at, known = None, None, 0
    for sample in path["samples"]:
        digestor.update(json.dumps(_jsonable(sample), sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")
        total = sample["total"]
        if total is None:
            continue
        known += 1
        if total < worst:
            worst, worst_at = total, sample["at"]
        if total > peak:
            peak, peak_at = total, sample["at"]
        if peak - total > maximum_dd:
            maximum_dd, dd_peak_at, dd_trough_at = peak - total, peak_at, sample["at"]
    metrics = path["metrics"] or path["known_sample_metrics"]
    if metrics is not None and (metrics["minimum_from_origin"] != worst
                               or metrics["max_drawdown"] != maximum_dd
                               or metrics["known_samples"] != known):
        raise ValueError("path reduction differs from shared risk metrics")
    return {"sample_count": len(path["samples"]), "known_samples": known,
            "sample_stream_sha256": digestor.hexdigest(),
            "sample_stream_encoding": "sorted compact JSON per sample, newline-delimited; Decimal string; datetime ISO",
            "metrics": _jsonable(path["metrics"]),
            "known_sample_metrics": _jsonable(path["known_sample_metrics"]),
            "minimum_at": worst_at.isoformat() if worst_at else None,
            "drawdown_peak_at": dd_peak_at.isoformat() if dd_peak_at else None,
            "drawdown_trough_at": dd_trough_at.isoformat() if dd_trough_at else None,
            "blockers": path["blockers"], "fx_coverage_mode": path["fx_coverage_mode"],
            "retrospective_fx_bracketed_samples": path["retrospective_fx_bracketed_samples"]}


def audit(raw_dir, native_deals_path, native_baskets_path, broker_contract_path,
          anchor_path, money_path):
    started = time.monotonic()
    raw_dir, native_deals_path, native_baskets_path, broker_contract_path, anchor_path, money_path = map(
        Path, (raw_dir, native_deals_path, native_baskets_path, broker_contract_path, anchor_path, money_path))
    anchor, money, native, baskets, contract = map(read, (anchor_path, money_path,
        native_deals_path, native_baskets_path, broker_contract_path))
    if (anchor.get("contract") != "native_tick_anchor_diagnostic_v1"
            or money.get("contract") != "native_closed_money_anchor_v2"
            or anchor["scope"]["currency"] != money["account_currency"]
            or contract["account"]["server"] != anchor["scope"]["server"]
            or baskets["source_sha256"] != digest(native_deals_path)
            or len(baskets["baskets"]) > MAX_BASKETS):
        raise ValueError("risk source identity or basket budget mismatch")
    for path in (native_deals_path, native_baskets_path, broker_contract_path, anchor_path):
        matching = [sha for name, sha in money["inputs_sha256"].items()
                    if Path(name).resolve() == path.resolve()]
        if len(matching) != 1 or digest(path) != matching[0]:
            raise ValueError(f"risk input not bound to money audit: {path}")
    watched = {str(path): digest(path) for path in (native_deals_path, native_baskets_path,
               broker_contract_path, anchor_path, money_path, Path(__file__),
               Path(sys.modules[RiskQuote.__module__].__file__),
               Path(sys.modules[_tape.__module__].__file__))}
    start = datetime.fromisoformat(anchor["scope"]["source_epoch_start"])
    end = datetime.fromisoformat(anchor["scope"]["source_epoch_end_exclusive"])
    market = _tape(raw_dir, anchor, "XAUUSD", start, end, watched)
    conversion = _tape(raw_dir, anchor, "EURUSD", start, end, watched)
    spec = RiskSpec(money["account_currency"], contract["account"]["currency_digits"],
                    Decimal(str(contract["instrument"]["contract_size"])),
                    contract["conversion"]["orientation"],
                    contract["conversion"]["max_quote_age_ms"], MARKET_MAX_AGE_MS)
    if spec.currency != "EUR" or spec.orientation != "account_base_profit_quote":
        raise ValueError("unexpected account conversion contract")
    by_signal = defaultdict(list)
    for position in money["positions"]:
        by_signal[position["signal_id"]].append(position)
    if (sum(map(len, by_signal.values())) != len(baskets["positions"])
            or {row["signal_id"] for row in baskets["baskets"]} != set(by_signal)):
        raise ValueError("native money positions or basket denominator changed")
    all_events = native_events(native["deals"], money["positions"])
    reports = []
    for basket in baskets["baskets"]:
        if time.monotonic() - started > MAX_SECONDS:
            raise TimeoutError("risk reconstruction wall budget exceeded")
        signal = basket["signal_id"]
        positions = by_signal[signal]
        row = {"signal_id": signal, "channel": basket["channel"],
               "position_count": len(positions), "native_net_eur": basket["net_eur"]}
        try:
            events = [event for position in positions for event in all_events[position["position_id"]]]
            if sum((event.money for event in events), Decimal(0)) != cents(basket["net_eur"]):
                raise ValueError("basket native money differs from event sequence")
            days = sorted({datetime.fromtimestamp(int(value) / 1000, timezone.utc).date().isoformat()
                           for position in positions for value in (position["entry_msc"], position["exit_msc"])})
            clock = anchor["independent_clock_evidence"]["days"]
            row["source_epoch_days"] = days
            row["direct_clock_anchor_for_all_event_days"] = all(
                clock.get(day, {}).get("status") == "direct_anchor_available" for day in days)
            quotes = quote_grid(market, conversion, events)
            reconstructed = reconstruct_risk(events, quotes, spec=spec,
                retrospective_fx_interval_ms=contract["conversion"]["max_quote_interval_ms"])
            row["path"] = summarize_path(reconstructed)
            row["status"] = "retrospective_complete" if not reconstructed["blockers"] else "blocked"
            row["strict_causal_fx_path_complete"] = (
                row["status"] == "retrospective_complete"
                and reconstructed["retrospective_fx_bracketed_samples"] == 0)
            if reconstructed["metrics"] is not None and reconstructed["metrics"]["final_net"] != cents(basket["net_eur"]):
                raise ValueError("risk final net differs from reconciled native basket")
        except (ValueError, KeyError) as exc:
            row.update(status="blocked", blocker=str(exc))
        reports.append(row)
    for path, sha in watched.items():
        if digest(path) != sha:
            raise ValueError("risk source changed during reconstruction")
    return {"contract": "native_week_full_tick_risk_diagnostic_v1", "status": "diagnostic_only",
            "source_clock_offset_seconds_hypothesis": OFFSET_SECONDS, "clock_admitted": False,
            "simulator_path_parity_verified": False, "basket_count": len(reports),
            "statuses": dict(Counter(row["status"] for row in reports)),
            "strict_causal_fx_complete_baskets": sum(row.get("strict_causal_fx_path_complete") is True for row in reports),
            "baskets": reports, "inputs_sha256": watched,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "limitations": ["Every retained XAU tick is marked; unobserved between-tick extrema remain unknown.",
                            "FX intervals over five seconds are admitted only retrospectively using a later timestamp, never for strategy decisions.",
                            "Broker epoch to UTC remains a hypothesis outside directly anchored days.",
                            "Modeled floating and drawdown from actual fills are not native account equity or independent live path parity.",
                            "Per-basket risk excludes shared-account equity, credit, margin and other positions."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--native-deals", type=Path, required=True)
    parser.add_argument("--native-baskets", type=Path, required=True)
    parser.add_argument("--broker-contract", type=Path, required=True)
    parser.add_argument("--native-anchor", type=Path, required=True)
    parser.add_argument("--native-money", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("immutable risk path output already exists")
    result = audit(args.raw_dir, args.native_deals, args.native_baskets,
                   args.broker_contract, args.native_anchor, args.native_money)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"output": str(args.output), "baskets": result["basket_count"],
                      "statuses": result["statuses"],
                      "strict_causal_fx_complete": result["strict_causal_fx_complete_baskets"]}), flush=True)


if __name__ == "__main__":
    main()
