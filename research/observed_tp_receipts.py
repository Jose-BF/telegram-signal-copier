"""Causal client-side TP state from recorded requests and responses.

This does not assert the broker's installation timestamp or position state.
Only a response strictly earlier than the queried millisecond is known.
"""

from __future__ import annotations

from decimal import Decimal


def tp_receipt_state(leg, at_utc_msc):
    if type(at_utc_msc) is not int:
        raise ValueError("TP state requires an integer UTC millisecond")
    target = Decimal(str(leg["target_level"]))
    initial = leg["initial_order_requested_tp"]
    confirmed = Decimal(str(initial)) if initial is not None else None
    attempts = leg["attempts"]
    if len({row["attempt_id"] for row in attempts}) != len(attempts):
        raise ValueError("duplicate TP attempt identity")
    for row in attempts:
        start, response = row["started_utc_msc"], row["responded_utc_msc"]
        if (type(start) is not int or type(response) is not int or response < start
                or type(row["retcode"]) is not int or not row["attempt_id"]):
            raise ValueError("invalid TP attempt chronology")
    if any(at_utc_msc in (row["started_utc_msc"], row["responded_utc_msc"])
           for row in attempts):
        return {"at_utc_msc": at_utc_msc, "target_status": "clock_tie_blocked",
                "confirmed_tp": None, "inflight_attempt_ids": []}
    completed = sorted((row for row in attempts if row["responded_utc_msc"] < at_utc_msc),
                       key=lambda row: (row["responded_utc_msc"], row["attempt_id"]))
    for row in completed:
        if row["retcode"] == 10009:
            confirmed = Decimal(str(row["request_tp"]))
    inflight = sorted((row for row in attempts
                       if row["started_utc_msc"] < at_utc_msc < row["responded_utc_msc"]),
                      key=lambda row: (row["started_utc_msc"], row["attempt_id"]))
    pending_levels = {Decimal(str(row["request_tp"])) for row in inflight}
    if confirmed == target:
        status = ("client_accepted_target_pending_change" if pending_levels - {target}
                  else "client_accepted_target_redundant_pending" if inflight
                  else "client_accepted_target_broker_unobserved")
    elif target in pending_levels:
        status = "pending_target_outcome_unknown"
    else:
        status = "target_not_confirmed_in_client_trace"
    return {"at_utc_msc": at_utc_msc, "target_status": status,
            "confirmed_tp": str(confirmed) if confirmed is not None else None,
            "inflight_attempt_ids": [row["attempt_id"] for row in inflight],
            "completed_accepted_count": sum(row["retcode"] == 10009 for row in completed),
            "completed_rejected_count": sum(row["retcode"] != 10009 for row in completed)}
