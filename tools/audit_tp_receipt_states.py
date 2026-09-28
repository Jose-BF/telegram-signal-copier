"""Bind causal client TP states to retained pre/post-ack target touches."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.observed_tp_receipts import tp_receipt_state
from tools.audit_native_money_anchor import digest, read


def _verify_sources(report):
    for name, expected in report["inputs_sha256"].items():
        if digest(Path(name)) != expected:
            raise ValueError(f"bound TP source changed: {name}")


def _rows_by_ticket(report):
    rows = {row["native_position_id"]: row for row in report["rows"]}
    if len(rows) != len(report["rows"]) or len(rows) != report["leg_count"]:
        raise ValueError("TP leg denominator or ticket identity mismatch")
    return rows


def audit(timeline_path, preack_path, postack_path):
    timeline_path, preack_path, postack_path = map(Path, (timeline_path, preack_path, postack_path))
    timeline, preack, postack = map(read, (timeline_path, preack_path, postack_path))
    if (timeline.get("contract") != "tp_request_timeline_diagnostic_v1"
            or preack.get("contract") != "tp_preack_retained_touch_diagnostic_v1"
            or postack.get("contract") != "tp_response_to_native_exit_diagnostic_v1"):
        raise ValueError("TP receipt input contract mismatch")
    watched = {str(path): digest(path) for path in
               (timeline_path, preack_path, postack_path, Path(__file__),
                Path(__file__).resolve().parents[1] / "research" / "observed_tp_receipts.py")}
    for report in (timeline, preack, postack):
        _verify_sources(report)
    for report, source in ((preack, timeline_path), (postack, preack_path)):
        bound = [sha for name, sha in report["inputs_sha256"].items()
                 if Path(name).resolve() == source.resolve()]
        if bound != [watched[str(source)]]:
            raise ValueError("TP report chain is not bound")
    timeline_by_ticket = _rows_by_ticket(timeline)
    preack_by_ticket = _rows_by_ticket(preack)
    postack_by_ticket = _rows_by_ticket(postack)
    if not timeline_by_ticket.keys() == preack_by_ticket.keys() == postack_by_ticket.keys():
        raise ValueError("TP ticket sets differ across reports")
    rows = []
    for ticket, leg in timeline_by_ticket.items():
        before, after = preack_by_ticket[ticket], postack_by_ticket[ticket]
        identity = ("signal_id", "leg_index", "native_position_id", "target_level")
        if (any(leg[key] != before[key] or leg[key] != after[key] for key in identity)
                or leg["first_target_accepted_response_utc_msc"]
                != before["first_accepted_response_utc_msc"]
                != after["first_accepted_response_utc_msc"]):
            raise ValueError("TP leg level or accepted response differs")
        first_before = before["first_target_touch_utc_msc"]
        first_after = after["first_target_touch_utc_msc"]
        accepted = leg["first_target_accepted_response_utc_msc"]
        if ((first_before is not None and first_before >= accepted)
                or (first_after is not None and first_after < accepted)):
            raise ValueError("TP touch outside bound response interval")
        rows.append({"signal_id": leg["signal_id"], "leg_index": leg["leg_index"],
                     "native_position_id": ticket,
                     "first_preack_touch_state": tp_receipt_state(leg, first_before)
                     if first_before is not None else None,
                     "first_postack_touch_state": tp_receipt_state(leg, first_after)
                     if first_after is not None else None,
                     "first_postack_touch_to_native_exit_ms": after["first_touch_to_native_exit_ms"]})
    if any(digest(Path(name)) != sha for name, sha in watched.items()):
        raise ValueError("TP receipt sources changed during audit")
    return {"contract": "tp_client_receipt_state_at_touch_diagnostic_v1",
            "status": "diagnostic_only", "leg_count": len(rows),
            "preack_state_counts": dict(sorted(Counter(
                row["first_preack_touch_state"]["target_status"] for row in rows
                if row["first_preack_touch_state"] is not None).items())),
            "postack_state_counts": dict(sorted(Counter(
                row["first_postack_touch_state"]["target_status"] for row in rows
                if row["first_postack_touch_state"] is not None).items())),
            "rows": rows, "inputs_sha256": watched,
            "full_protection_path_parity_verified": False,
            "limitations": ["Client request/response state is not continuous broker-position observation.",
                            "Pending requests remain unknown at a tick; later retcodes are not read early.",
                            "A target touch is not an observed server trigger or fill."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeline", type=Path, required=True)
    parser.add_argument("--preack", type=Path, required=True)
    parser.add_argument("--postack", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = audit(args.timeline, args.preack, args.postack)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"legs": result["leg_count"],
                      "preack_states": result["preack_state_counts"],
                      "postack_states": result["postack_state_counts"]}), flush=True)


if __name__ == "__main__":
    main()
