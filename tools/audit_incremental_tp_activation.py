"""Bounded offline TP timing audit on a source-bound incremental window."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

import numpy as np
import pandas as pd

from research.gold_iterative.contracts import gold_555_genome
from tools.compare_canal1_incremental_risk import verify_sources
from tools.probe_canal1_incremental_window import digest


def _utc_msc(value):
    at = datetime.fromisoformat(value)
    if at.tzinfo is None:
        raise ValueError("model UTC timestamp lacks timezone")
    return int(at.timestamp() * 1000)


def first_model_modify_release(events, ticket, *, entry_ns):
    """First completed client modification after this modeled entry."""
    active = False
    for event in events:
        if event["operation"] != "modify" or event["ticket"] != ticket:
            continue
        when = event["time_ns"]
        if type(when) is not int or when < entry_ns:
            raise ValueError("model modification before entry")
        if event["kind"] == "started":
            if active:
                raise ValueError("overlapping model modifications")
            active = True
        elif event["kind"] == "released":
            if not active:
                raise ValueError("model modification released without start")
            return when
    return None


def first_model_tp_install(events, ticket, *, entry_ns):
    """Return the first effective model TP and its quote-clock timestamp."""
    requests = {}
    for event in events:
        if event["ticket"] != ticket:
            continue
        when = event["timestamp_ns"]
        if type(when) is not int or when < entry_ns:
            raise ValueError("model protection before entry")
        kind = event["kind"]
        if kind == "open" and event["tp"] is not None:
            return when, event["tp"]
        if kind == "requested":
            request_id = event["request_id"]
            if request_id in requests:
                raise ValueError("duplicate model protection request")
            requests[request_id] = (when, event["tp"])
        elif kind == "installed":
            request = requests.get(event["request_id"])
            if request is None:
                raise ValueError("model TP installed without request")
            if when < request[0] or event["tp"] != request[1]:
                raise ValueError("model TP installation differs from request")
            if event["tp"] is not None:
                return when, event["tp"]
    return None


def pre_model_tp_touches(times, bids, asks, *, direction, target,
                         entry_utc_msc, install_utc_msc, offset_seconds):
    """Find retained exit-side quotes before modeled TP installation."""
    if (direction not in {"BUY", "SELL"} or type(offset_seconds) is not int
            or type(entry_utc_msc) is not int or type(install_utc_msc) is not int
            or entry_utc_msc >= install_utc_msc
            or len(times) != len(bids) or len(times) != len(asks)
            or np.any(np.diff(times) < 0)):
        raise ValueError("invalid model TP touch inputs")
    lower = entry_utc_msc + offset_seconds * 1000
    upper = install_utc_msc + offset_seconds * 1000
    first = int(np.searchsorted(times, lower, side="left"))
    last = int(np.searchsorted(times, upper, side="left"))
    target = Decimal(str(target))
    if not target.is_finite() or target <= 0:
        raise ValueError("invalid model TP target")
    values = bids if direction == "BUY" else asks
    touches = [(int(times[index]), Decimal(str(values[index])))
               for index in range(first, last)
               if (Decimal(str(values[index])) >= target if direction == "BUY"
                   else Decimal(str(values[index])) <= target)]
    initial = touches[0] if touches else None
    return {"quote_count_before_install": last - first,
            "touch_count": len(touches),
            "first_touch_utc_msc": (initial[0] - offset_seconds * 1000
                                    if initial else None),
            "first_touch_quote": str(initial[1]) if initial else None}


def audit(model, native, reconciled, deals, anchor, frozen_root, *,
          model_path, native_path, reconciled_path, deals_path, anchor_path):
    frozen_root = Path(frozen_root)
    sources = verify_sources(model, native, model_path=model_path,
                             native_path=native_path, frozen_root=frozen_root)
    if (digest(reconciled_path) != model["native_start_control"]["reconciled_sha256"]
            or digest(deals_path) != model["native_start_control"]["deals_sha256"]
            or reconciled["source_sha256"] != digest(deals_path)
            or model["policy_fingerprints"]["canal2"] != gold_555_genome().fingerprint):
        raise ValueError("TP audit ledger or 555 policy identity changed")
    anchor_bindings = [sha for name, sha in native["inputs_sha256"].items()
                       if Path(name).name == Path(anchor_path).name]
    if anchor_bindings != [digest(anchor_path)]:
        raise ValueError("TP clock anchor not bound to native risk source")
    day = datetime.fromisoformat(model["start_utc"]).date().isoformat()
    clock = anchor["independent_clock_evidence"]["days"].get(day, {})
    offset = model["clock_offset_seconds_hypothesis"]
    if clock.get("status") != "direct_anchor_available" or clock.get("offset_seconds") != offset:
        raise ValueError("TP window lacks direct matching clock anchor")
    tape_path = frozen_root / "XAUUSD" / f"{day}.parquet"
    tape = pd.read_parquet(tape_path, columns=["time_msc", "bid", "ask"])
    times = tape.time_msc.to_numpy(dtype=np.int64)
    bids = tape.bid.to_numpy(dtype=float)
    asks = tape.ask.to_numpy(dtype=float)
    by_position = defaultdict(list)
    for deal in deals["deals"]:
        if deal.get("symbol") == "XAUUSD":
            by_position[deal["position_id"]].append(deal)
    by_signal = defaultdict(list)
    for position in reconciled["positions"]:
        by_signal[position["signal_id"]].append(position)
    native_by_id = {row["signal_id"]: row for row in native["baskets"]}
    genome = gold_555_genome()
    rows = []
    for basket in model["basket_rows"]:
        if basket["channel"] != "canal2" or not basket["entries"]:
            continue
        signal_id = basket["signal_id"]
        observed = native_by_id.get(signal_id)
        positions = sorted(by_signal.get(signal_id, []),
                           key=lambda row: (row["first_native_msc"], row["position_id"]))
        entries = sorted(basket["entries"], key=lambda row: _utc_msc(row["opened_at"]))
        if (observed is None or len(positions) != observed["position_count"]
                or len(entries) != len(positions)
                or [Decimal(str(row["volume"])) for row in entries]
                != [Decimal(str(row["entry_volume"])) for row in positions]):
            rows.append({"signal_id": signal_id, "status": "blocked_leg_identity",
                         "model_entry_count": len(entries),
                         "native_position_count": len(positions)})
            continue
        model_exits = {row["ticket"]: row for row in basket["exits"]}
        for leg_index, (entry, position) in enumerate(zip(entries, positions)):
            pair = by_position[position["position_id"]]
            if (len(pair) != 2 or {row["entry"] for row in pair} != {0, 1}
                    or {row["ticket"] for row in pair} != set(position["deal_tickets"])):
                rows.append({"signal_id": signal_id, "leg_index": leg_index,
                             "status": "blocked_native_deal_identity"})
                continue
            native_entry = next(row for row in pair if row["entry"] == 0)
            native_exit = next(row for row in pair if row["entry"] == 1)
            direction = "BUY" if native_entry["type"] == 0 else "SELL" if native_entry["type"] == 1 else None
            if direction is None or native_exit["type"] != 1 - native_entry["type"]:
                rows.append({"signal_id": signal_id, "leg_index": leg_index,
                             "status": "blocked_native_direction"})
                continue
            install = first_model_tp_install(
                basket["protection_events"], entry["ticket"],
                entry_ns=entry["requested_ns"])
            release_ns = first_model_modify_release(
                basket["client_events"], entry["ticket"],
                entry_ns=entry["requested_ns"])
            model_exit = model_exits.get(entry["ticket"])
            target = (Decimal(str(entry["entry_price"]))
                      + (1 if direction == "BUY" else -1)
                      * Decimal(str(genome.target_steps[leg_index])))
            row = {"signal_id": signal_id, "leg_index": leg_index,
                   "model_ticket": entry["ticket"],
                   "native_position_id": position["position_id"],
                   "direction": direction,
                   "model_entry_utc_msc": _utc_msc(entry["opened_at"]),
                   "model_target": str(target),
                   "native_entry_source_msc": native_entry["time_msc"],
                   "native_entry_price": native_entry["price"],
                   "native_exit_source_msc": native_exit["time_msc"],
                   "native_exit_reason": native_exit["reason"],
                   "native_exit_price": native_exit["price"],
                   "model_exit_utc": model_exit["closed_at"] if model_exit else None,
                   "model_exit_reason": model_exit["reason"] if model_exit else None,
                   "model_first_tp_install_utc_msc": (
                       install[0] // 1_000_000 if install is not None else None),
                   "model_installed_tp": str(install[1]) if install is not None else None,
                   "model_first_modify_release_utc_msc": (
                       release_ns // 1_000_000 if release_ns is not None else None),
                   "ordinal_pairing_not_broker_ticket_identity": True}
            if install is None:
                row["status"] = "blocked_model_tp_install_missing"
            elif Decimal(str(install[1])) != target:
                row["status"] = "blocked_model_tp_target_mismatch"
            else:
                row.update(pre_model_tp_touches(
                    times, bids, asks, direction=direction, target=target,
                    entry_utc_msc=row["model_entry_utc_msc"],
                    install_utc_msc=install[0] // 1_000_000,
                    offset_seconds=offset))
                row["native_tp_before_model_install"] = (
                    native_exit["reason"] == 5
                    and native_exit["time_msc"] - offset * 1000
                    < install[0] // 1_000_000)
                row["status"] = "timed_diagnostic"
            rows.append(row)
    counts = Counter(row["status"] for row in rows)
    return {"contract": "incremental_model_tp_activation_diagnostic_v2",
            "status": "diagnostic_only", "live_tp_install_time_verified": False,
            "model_555_fingerprint": genome.fingerprint,
            "leg_count": len(rows),
            "timed_leg_count": counts["timed_diagnostic"],
            "blocked_leg_count": len(rows) - counts["timed_diagnostic"],
            "pre_model_install_touch_legs": sum(row.get("touch_count", 0) > 0 for row in rows),
            "native_tp_before_model_install_legs": sum(
                row.get("native_tp_before_model_install") is True for row in rows),
            "rows": rows,
            "sources": {**sources, "reconciled_sha256": digest(reconciled_path),
                        "deals_sha256": digest(deals_path),
                        "clock_anchor_sha256": digest(anchor_path),
                        "auditor_sha256": digest(__file__)},
            "limitations": [
                "The model TP install is the first effective modeled protection, not a native TP receipt.",
                "A native TP deal proves execution by that time, not the exact server install time.",
                "Leg pairing is ordinal with equal volumes, not common ticket identity.",
                "A retained quote touch is not by itself a broker fill.",
                "Missing native modification telemetry prevents exact installation parity.",
            ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("model", "native", "native_reconciled", "native_deals", "anchor",
                 "frozen_root", "output"):
        parser.add_argument("--" + name.replace("_", "-"), type=Path, required=True)
    args = parser.parse_args()
    model = json.loads(args.model.read_text(encoding="utf-8"))
    native = json.loads(args.native.read_text(encoding="utf-8"))
    reconciled = json.loads(args.native_reconciled.read_text(encoding="utf-8"))
    deals = json.loads(args.native_deals.read_text(encoding="utf-8"))
    anchor = json.loads(args.anchor.read_text(encoding="utf-8"))
    result = audit(model, native, reconciled, deals, anchor, args.frozen_root,
                   model_path=args.model, native_path=args.native,
                   reconciled_path=args.native_reconciled, deals_path=args.native_deals,
                   anchor_path=args.anchor)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(result, target, indent=2, sort_keys=True, allow_nan=False)
        target.write("\n")
    print(json.dumps({"status": result["status"], "legs": result["leg_count"],
                      "pre_install_touches": result["pre_model_install_touch_legs"],
                      "native_tp_before_install": result["native_tp_before_model_install_legs"]}))


if __name__ == "__main__":
    main()
