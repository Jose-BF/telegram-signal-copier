"""Bind Gold shadow leg indexes to native deals via strict broker comments.

Broker comments establish logical leg identity, not the live decision,
order-request or receipt chain. Those remain separate evidence gates.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from decimal import Decimal
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gold_555_live_candidate import CANDIDATE_FINGERPRINT
from tools.audit_native_money_anchor import digest, read
from tools.audit_week_shadow_control_path import controls_and_states, shadow_rows


COMMENT = re.compile(r"^c2_(\d+)(?:_B([1-4]))?_g55$")
OFFSET_SECONDS = 10_800


def _bound(report, path):
    matches = [sha for name, sha in report.get("inputs_sha256", {}).items()
               if Path(name).resolve() == Path(path).resolve()]
    if matches != [digest(path)]:
        raise ValueError(f"source hash binding missing or changed: {path}")


def bind_signal(comparison, positions, entry_deals, shadow_states):
    signal = comparison["signal_id"]
    pairing = comparison.get("entry_pairing", {})
    if (comparison.get("channel") != "canal2"
            or comparison.get("control_candidate") != "gold_now_555_v1"
            or pairing.get("status") not in {
                "verified_journal_entry_binding", "diagnostic_ordinal_pairing"}):
        raise ValueError("Gold live-control entry pairing required")
    pairs = {row["leg_index"]: row for row in pairing["pairs"]}
    if (len(pairs) != len(pairing["pairs"]) or not pairs
            or set(pairs) != set(range(len(pairs)))
            or len(positions) != len(pairs)):
        raise ValueError("native/shadow leg denominator mismatch")
    fills = [row for row in shadow_states if row.get("ev") == "strategy_shadow_transition"
             and row.get("transition") == "virtual_fill"]
    fills_by_leg = {row["transition_details"]["leg_index"]: row for row in fills}
    if len(fills) != len(pairs) or set(fills_by_leg) != set(pairs):
        raise ValueError("shadow virtual-fill leg identity incomplete")
    final = shadow_states[-1]["state"]
    if (final["signal_id"] != signal or final["candidate_id"] != "gold_now_555_v1"
            or final["strategy_fingerprint"] != CANDIDATE_FINGERPRINT):
        raise ValueError("shadow strategy identity mismatch")
    positions_by_id = {row["position_id"]: row for row in positions}
    deals_by_id = {row["position_id"]: row for row in entry_deals}
    if (len(positions_by_id) != len(positions) or len(deals_by_id) != len(entry_deals)
            or set(positions_by_id) != set(deals_by_id)):
        raise ValueError("native position/deal identity incomplete")
    rows = []
    for index, pair in sorted(pairs.items()):
        pid = pair["native_position_id"]
        position, deal, shadow = positions_by_id.get(pid), deals_by_id.get(pid), fills_by_leg[index]
        if position is None or deal is None:
            raise ValueError("paired native ticket missing from broker source")
        comment = COMMENT.fullmatch(str(deal.get("comment") or ""))
        if (comment is None or signal != f"canal2_{comment.group(1)}"
                or index != int(comment.group(2) or 0)
                or deal.get("entry") != 0 or deal.get("symbol") != "XAUUSD"
                or deal.get("magic") != 20260422
                or deal.get("order") != pid or deal.get("ticket") not in position["deal_tickets"]
                or deal.get("time_msc") != position["entry_msc"]
                or deal.get("type") != (0 if final["direction"] == "BUY" else 1)
                or Decimal(str(deal["volume"])) != Decimal(str(position["volume"]))
                or Decimal(str(deal["price"])) != Decimal(str(position["entry_price"]))
                or Decimal(str(position["volume"])) != Decimal(pair["volume"])
                or Decimal(str(position["entry_price"])) != Decimal(pair["native_entry_price"])):
            raise ValueError("broker comment or native entry contradicts logical leg")
        details = shadow["transition_details"]
        if (Decimal(str(details["volume"])) != Decimal(pair["volume"])
                or Decimal(str(details["entry_price"])) != Decimal(pair["shadow_entry_price"])
                or position["entry_msc"] - OFFSET_SECONDS * 1000
                - shadow["transition_tick_msc"] != pair["native_minus_shadow_ms"]):
            raise ValueError("shadow virtual fill contradicts paired logical leg")
        rows.append({"leg_index": index, "native_position_id": pid,
                     "native_entry_deal_ticket": deal["ticket"],
                     "broker_comment": deal["comment"],
                     "native_entry_source_msc": position["entry_msc"],
                     "native_entry_price": pair["native_entry_price"],
                     "shadow_entry_tick_utc_msc": shadow["transition_tick_msc"],
                     "shadow_entry_price": pair["shadow_entry_price"],
                     "volume": pair["volume"]})
    return {"signal_id": signal, "channel": "canal2",
            "status": "verified_broker_deal_comment_leg_binding",
            "prior_pairing_status": pairing["status"],
            "leg_count": len(rows), "legs": rows,
            "native_leg_identity_verified": True,
            "live_order_request_chain_verified": pairing["status"] == "verified_journal_entry_binding"}


def audit(control_path, money_path, deals_path, shadow_path, manifest_path):
    paths = tuple(map(Path, (control_path, money_path, deals_path, shadow_path, manifest_path)))
    control_path, money_path, deals_path, shadow_path, manifest_path = paths
    control, money, native = map(read, (control_path, money_path, deals_path))
    if (control.get("contract") != "week_shadow_live_control_path_diagnostic_v1"
            or money.get("contract") != "native_closed_money_anchor_v2"
            or money.get("account_currency") != native.get("currency")):
        raise ValueError("broker-comment source contract mismatch")
    _bound(control, shadow_path)
    _bound(money, deals_path)
    raw = shadow_rows(shadow_path, manifest_path)
    controls, states, registered, manifest_hash = controls_and_states(raw)
    if (control["control_manifest_hash"] != manifest_hash
            or controls.get("canal2") != "gold_now_555_v1"):
        raise ValueError("shadow live-control manifest mismatch")
    comparisons = [row for row in control["comparisons"] if row["channel"] == "canal2"]
    if len(comparisons) != 7:
        raise ValueError("unexpected Gold control comparison denominator")
    native_positions = defaultdict(list)
    for row in money["positions"]:
        native_positions[row["signal_id"]].append(row)
    native_entry_deals = defaultdict(list)
    for row in native["deals"]:
        if row.get("entry") != 0:
            continue
        match = COMMENT.fullmatch(str(row.get("comment") or ""))
        if match:
            native_entry_deals[f"canal2_{match.group(1)}"].append(row)
    results = []
    for row in comparisons:
        signal = row["signal_id"]
        if (signal, "gold_now_555_v1") not in registered or signal not in states:
            raise ValueError("Gold comparison lacks shadow state chain")
        results.append(bind_signal(row, native_positions[signal], native_entry_deals[signal],
                                   states[signal]))
    return {"contract": "gold_broker_comment_leg_binding_diagnostic_v1",
            "status": "diagnostic_only",
            "gold_native_basket_denominator": sum(
                signal.startswith("canal2_") for signal in native_positions
            ),
            "gold_control_comparison_count": len(comparisons),
            "comment_bound_count": len(results),
            "prior_pairing_statuses": dict(sorted(Counter(row["prior_pairing_status"] for row in results).items())),
            "signals": results,
            "inputs_sha256": {str(path): digest(path) for path in (*paths, Path(__file__))},
            "limitations": [
                "Broker deal comments bind logical legs but do not prove the live decision/request/receipt chain.",
                "This does not verify virtual exits, money path or independent policy replay.",
                "Signals without a shadow live control remain outside the compared subset."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("control", "money", "deals", "shadow", "manifest", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = audit(args.control, args.money, args.deals, args.shadow, args.manifest)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"comment_bound": result["comment_bound_count"],
                      "prior_statuses": result["prior_pairing_statuses"]}))


if __name__ == "__main__":
    main()
