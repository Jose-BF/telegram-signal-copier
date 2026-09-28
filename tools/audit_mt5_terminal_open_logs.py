"""Cross-check native first-order calls against immutable MT5 terminal logs."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.audit_vm_entry_roundtrip import digest, utc_ms


LOCAL_ZONE = ZoneInfo("Europe/Madrid")
DONE = re.compile(r"order #(?P<order>\d+).* done in (?P<duration>\d+(?:\.\d+)?) ms")
DEAL = re.compile(r"deal #(?P<deal>\d+) .*\(based on order #(?P<order>\d+)\)")
MARKET = re.compile(r"^(?P<accepted>accepted )?market (?P<direction>buy|sell) (?P<volume>\d+(?:\.\d+)?) XAUUSD(?: |$)")


def read_log(path):
    date = datetime.strptime(Path(path).stem, "%Y%m%d").date()
    rows = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-16").splitlines(), 1):
        fields = line.split("\t", 4)
        if len(fields) != 5 or fields[3] != "Trades":
            continue
        try:
            local = datetime.fromisoformat(f"{date.isoformat()}T{fields[2]}").replace(tzinfo=LOCAL_ZONE)
        except ValueError:
            continue
        message = fields[4].split(": ", 1)[-1]
        done = DONE.search(message)
        deal = DEAL.search(message)
        market = MARKET.search(message)
        kind = "done" if done else "deal" if deal else "accepted" if market and market["accepted"] else "market" if market else None
        if kind:
            rows.append({"line": number, "utc_ms": utc_ms(local.astimezone(timezone.utc).isoformat()),
                         "local_clock": fields[2], "kind": kind,
                         "order": int(done["order"]) if done else int(deal["order"]) if deal else None,
                         "deal": int(deal["deal"]) if deal else None,
                         "done_ms": done["duration"] if done else None,
                         "direction": market["direction"].upper() if market else None,
                         "volume": market["volume"] if market else None})
    return rows


def bind_terminal(call, window, log_rows, direction, native_order):
    signal = call["signal_id"]
    results = [event for event in window["events"] if event["ev"] == "mt5_order_result"
               and event.get("deal") == call["native_entry_deal"]]
    if len(results) != 1 or results[0].get("action_id") != call["action_id"]:
        raise ValueError("bounded VM result identity mismatch")
    order = results[0]["order"]
    if order != native_order:
        raise ValueError("terminal order conflicts with native deal")
    deal = [row for row in log_rows if row["kind"] == "deal" and row["deal"] == call["native_entry_deal"]
            and row["order"] == order]
    done = [row for row in log_rows if row["kind"] == "done" and row["order"] == order]
    if len(deal) != 1 or len(done) != 1:
        raise ValueError("terminal deal/order completion identity missing or ambiguous")
    deal, done = deal[0], done[0]
    started = utc_ms(call["call_started_utc"])
    response = utc_ms(call["call_response_utc"])
    candidates = [row for row in log_rows if row["kind"] in {"market", "accepted"}
                  and row["direction"] == direction
                  and Decimal(str(row["volume"])) == Decimal(str(call["native_entry_volume"]))
                  and started <= row["utc_ms"] <= done["utc_ms"]]
    market = [row for row in candidates if row["kind"] == "market"]
    accepted = [row for row in candidates if row["kind"] == "accepted"]
    terminal_ms = Decimal(done["done_ms"])
    python_ms = Decimal(call["mt5_order_send_roundtrip_ms"])
    result = {"signal_id": signal, "status": "terminal_deal_and_done_bound",
              "native_entry_deal": call["native_entry_deal"], "terminal_order": order,
              "terminal_deal_line": deal["line"], "terminal_done_line": done["line"],
              "terminal_deal_local_clock": deal["local_clock"],
              "terminal_done_local_clock": done["local_clock"],
              "terminal_done_minus_python_response_ms": done["utc_ms"] - response,
              "terminal_deal_minus_python_start_ms": deal["utc_ms"] - started,
              "call_clock_order_consistent": started <= deal["utc_ms"] <= done["utc_ms"] <= response,
              "terminal_order_done_ms": str(terminal_ms),
              "python_mt5_call_ms": str(python_ms),
              "terminal_minus_python_duration_ms": str(terminal_ms - python_ms),
              "market_candidate_count": len(market), "accepted_candidate_count": len(accepted)}
    if len(market) == 1:
        result["market_line"] = market[0]["line"]
        result["market_local_clock"] = market[0]["local_clock"]
        result["terminal_market_minus_python_call_ms"] = market[0]["utc_ms"] - started
    if len(accepted) == 1:
        result["accepted_line"] = accepted[0]["line"]
        result["accepted_local_clock"] = accepted[0]["local_clock"]
        result["accepted_minus_python_call_ms"] = accepted[0]["utc_ms"] - started
    if len(market) == len(accepted) == 1:
        result["market_to_accepted_ms"] = accepted[0]["utc_ms"] - market[0]["utc_ms"]
        result["accepted_to_deal_ms"] = deal["utc_ms"] - accepted[0]["utc_ms"]
        result["terminal_stage_order_consistent"] = (
            started <= market[0]["utc_ms"] <= accepted[0]["utc_ms"] <= deal["utc_ms"] <= done["utc_ms"])
    return result


def deal_coverage(native, baskets, logs):
    by_ticket = {row["ticket"]: row for row in native["deals"]}
    if len(by_ticket) != len(native["deals"]):
        raise ValueError("duplicate native deal ticket")
    owners = {}
    for position in baskets["positions"]:
        for ticket in position["deal_tickets"]:
            if ticket in owners:
                raise ValueError("native deal assigned to multiple positions")
            owners[ticket] = position["signal_id"]
    terminal = {}
    for rows in logs.values():
        for row in rows:
            if row["kind"] == "deal":
                terminal.setdefault(row["deal"], []).append(row)
    statuses = Counter()
    exceptions = []
    for ticket, signal in sorted(owners.items()):
        deal = by_ticket[ticket]
        matches = terminal.get(ticket, [])
        status = ("bound_exact_deal_order" if len(matches) == 1 and matches[0]["order"] == deal["order"]
                  else "absent_from_terminal_trades" if not matches
                  else "ambiguous_or_order_mismatch")
        statuses[status] += 1
        if status != "bound_exact_deal_order":
            exceptions.append({"signal_id": signal, "native_deal": ticket, "native_order": deal["order"],
                               "native_entry": deal["entry"], "native_reason": deal["reason"],
                               "terminal_match_count": len(matches), "status": status})
    if len(owners) != sum(statuses.values()):
        raise ValueError("native deal coverage denominator changed")
    return {"basket_linked_native_deals": len(owners), "statuses": dict(statuses), "exceptions": exceptions,
            "unlinked_source_deals": len(by_ticket) - len(owners)}


def audit(calls_path, batches, deals_path, baskets_path, log_dir):
    calls_path, deals_path, baskets_path, log_dir = map(Path, (calls_path, deals_path, baskets_path, log_dir))
    batches = [Path(path) for path in batches]
    calls = json.loads(calls_path.read_text(encoding="utf-8"))
    native = json.loads(deals_path.read_text(encoding="utf-8"))
    baskets = json.loads(baskets_path.read_text(encoding="utf-8"))
    if calls.get("contract") != "native_week_first_entry_mt5_call_v1" or calls["bound_count"] != 40:
        raise ValueError("unexpected first-call source contract/coverage")
    if (calls["inputs_sha256"].get(str(deals_path)) != digest(deals_path)
            or calls["inputs_sha256"].get(str(baskets_path)) != digest(baskets_path)
            or baskets.get("source_sha256") != digest(deals_path)):
        raise ValueError("native first-call and ledger source mismatch")
    windows = {}
    for path in batches:
        batch = json.loads(path.read_text(encoding="utf-8"))
        if calls["inputs_sha256"].get(str(path)) != digest(path):
            raise ValueError("terminal comparison batch changed")
        for window in batch["windows"]:
            if window["signal_id"] in windows:
                raise ValueError("duplicate bounded signal window")
            windows[window["signal_id"]] = window
    deals = {row["ticket"]: row for row in native["deals"]}
    logs = {path.stem: read_log(path) for path in sorted(log_dir.glob("2026091*.log"))}
    if len(logs) != 5 or len(windows) != len(calls["rows"]):
        raise ValueError("terminal log days or native window denominator incomplete")
    reports = []
    for call in calls["rows"]:
        signal = call["signal_id"]
        day = call["receipt_utc"][:10].replace("-", "")
        deal = deals[call["native_entry_deal"]]
        direction = "BUY" if deal["type"] == 0 else "SELL" if deal["type"] == 1 else None
        try:
            if direction is None:
                raise ValueError("native entry direction invalid")
            reports.append(bind_terminal(call, windows[signal], logs[day], direction, deal["order"]))
        except (KeyError, TypeError, ValueError) as exc:
            reports.append({"signal_id": signal, "status": "blocked", "reason": str(exc)})
    paths = [calls_path, *batches, deals_path, baskets_path, *sorted(log_dir.glob("2026091*.log")), Path(__file__)]
    return {"contract": "native_week_mt5_terminal_open_log_v1", "status": "diagnostic_only",
            "signal_count": len(reports), "statuses": dict(Counter(row["status"] for row in reports)),
            "rows": reports, "all_deal_coverage": deal_coverage(native, baskets, logs),
            "inputs_sha256": {str(path): digest(path) for path in paths},
            "mt5_internal_cause_isolated": False,
            "limitations": ["Terminal log timestamps use the VM's Romance Standard Time (Madrid); cross-clock subsecond ordering is diagnostic.",
                            "Market and accepted lines lack order IDs; stage intervals are reported only with unique direction/volume candidates.",
                            "Cross-clock order anomalies are retained without a millisecond tolerance; identity binding uses deal and order IDs.",
                            "Terminal logging alone cannot separate network transit from trade-server processing."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calls", type=Path, required=True)
    parser.add_argument("--batch", type=Path, action="append", required=True)
    parser.add_argument("--deals", type=Path, required=True)
    parser.add_argument("--baskets", type=Path, required=True)
    parser.add_argument("--terminal-log-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = audit(args.calls, args.batch, args.deals, args.baskets, args.terminal_log_dir)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2, ensure_ascii=True)
        output.write("\n")
    print(json.dumps({"signal_count": result["signal_count"], "statuses": result["statuses"]}))


if __name__ == "__main__":
    main()
