"""Observed exit facts, separate from policy decisions and broker action lineage."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from mt5_deal_reason import DEAL_REASON_NAMES

from .ledger_evidence import _identity, _money, _number, _utc, ledger_ticket_evidence


EXIT_DEAL_CONTRACT = "observed_exit_deal_facts_v1"
_PASSIVE_REASONS = {
    "per_leg_target": "tp", "provider_tp": "tp", "provider_target_all": "tp",
    "provider_sl": "sl", "fixed_sl": "sl", "trailing_stop": "sl",
    "break_even": "sl", "provider_be": "sl",
}
_LAST = datetime.max.replace(tzinfo=timezone.utc)


def mirror_exit_rows(records: Iterable[Any]) -> list[dict[str, Any]]:
    """Retain the engine's native exit order and facts; never substitute fills."""
    return [{
        "ticket": str(item.ticket), "closed_at": item.closed_at.isoformat(),
        "entry_price": str(item.entry_price), "exit_price": str(item.exit_price),
        "volume": str(item.volume), "pnl_eur": None if item.pnl_eur is None else str(item.pnl_eur),
        "reason": item.reason,
    } for item in records]


def _text(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if isinstance(value, Decimal) else value


def _sort_key(row: Mapping[str, Any]) -> tuple:
    # No cross-position order is inferred within a millisecond. Same-time
    # partial deals compare as a multiset, retaining multiplicity and identity.
    return (row.get("closed_at") or _LAST,) + tuple(
        (row.get(name) is None, row.get(name) or Decimal(0))
        for name in ("exit_price", "volume", "net_eur")
    ) + (str(row.get("mechanism")),)


def compare_exit_deals(
    actual: Mapping[str, Any],
    simulated: Iterable[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """Compare immutable entry/exit facts, not equality of policy decision traces.

    Native TP/SL establish a broker mechanism, not how its level was decided.
    Expert/manual/unknown exits need a separate causal decision contract. Entry
    costs cannot be allocated among exits implicitly to manufacture equality.
    """
    evidence, ledger_blockers = ledger_ticket_evidence(actual)
    blockers = list(ledger_blockers)
    mismatches: list[str] = []
    if simulated is None or isinstance(simulated, (str, bytes, Mapping)):
        blockers.append("mirror_exit_rows_missing_or_invalid")
        supplied = ()
    else:
        try:
            supplied = tuple(simulated)
        except TypeError:
            supplied = ()
            blockers.append("mirror_exit_rows_missing_or_invalid")
    mirror: dict[str, list[dict[str, Any]]] = defaultdict(list)
    previous_time = None
    for index, raw in enumerate(supplied):
        if not isinstance(raw, Mapping):
            blockers.append(f"mirror_exit_row_invalid:{index}")
            continue
        ticket = _identity(raw.get("ticket")) or str(raw.get("ticket") or "")
        row = {
            "source_index": index, "position_id": ticket,
            "closed_at": _utc(raw.get("closed_at")),
            "entry_price": _number(raw.get("entry_price")),
            "exit_price": _number(raw.get("exit_price")),
            "volume": _number(raw.get("volume")),
            "net_eur": _money(raw.get("pnl_eur")),
            "reason": raw.get("reason"),
            "mechanism": _PASSIVE_REASONS.get(str(raw.get("reason") or "")),
        }
        if _identity(ticket) is None:
            blockers.append(f"mirror_exit_position_invalid:{index}")
        if row["closed_at"] is None:
            blockers.append(f"mirror_exit_time_invalid:{index}")
        elif previous_time is not None and row["closed_at"] < previous_time:
            blockers.append(f"mirror_exit_order_invalid:{index}")
        if row["closed_at"] is not None:
            previous_time = row["closed_at"]
        for field in ("entry_price", "exit_price", "volume"):
            if row[field] is None or row[field] <= 0:
                blockers.append(f"mirror_exit_{field}_invalid:{index}")
        if row["net_eur"] is None:
            blockers.append(f"mirror_exit_money_invalid:{index}")
        if row["mechanism"] is None:
            blockers.append(f"mirror_exit_cause_requires_decision_trace:{index}")
        mirror[ticket].append(row)
    if set(evidence) != set(mirror):
        mismatches.append("exit_position_coverage_mismatch")
    if not evidence and actual.get("no_position_outcome_verified") is not True:
        blockers.append("no_position_outcome_unverified")

    rows = []
    for ticket in sorted(evidence.keys() | mirror.keys()):
        position = evidence.get(ticket)
        observed = []
        if position is not None:
            if position["opening_net_eur"] != 0:
                blockers.append(f"opening_cost_allocation_unverified:{ticket}")
            for deal in position["exit_deals"]:
                row = dict(deal, mechanism=DEAL_REASON_NAMES.get(deal["broker_reason"]))
                observed.append(row)
                label = f"{ticket}:{deal['deal_ticket']}"
                if not deal["millisecond_time_verified"]:
                    blockers.append(f"observed_exit_millisecond_time_missing:{label}")
                if deal["order_ticket"] is None:
                    blockers.append(f"observed_exit_order_identity_missing:{label}")
                if deal["entry_kind"] != 1:
                    blockers.append(f"observed_close_by_contract_unverified:{label}")
                if row["mechanism"] not in ("sl", "tp"):
                    blockers.append(f"observed_exit_cause_requires_decision_trace:{label}")
        observed.sort(key=_sort_key)
        calculated = sorted(mirror.get(ticket, ()), key=_sort_key)
        ticket_mismatches = []
        if len(observed) != len(calculated):
            ticket_mismatches.append(f"exit_deal_count_mismatch:{ticket}")
        comparisons = []
        for index in range(max(len(observed), len(calculated))):
            left = observed[index] if index < len(observed) else None
            right = calculated[index] if index < len(calculated) else None
            differences = []
            if left is None or right is None:
                differences.append("exit_missing")
            else:
                for field in ("closed_at", "entry_price", "exit_price", "volume", "net_eur", "mechanism"):
                    if left[field] is None or right[field] is None or left[field] != right[field]:
                        differences.append(field)
            if differences:
                ticket_mismatches.append(f"exit_deal_facts_mismatch:{ticket}:{index}")
            comparisons.append({
                "observed": None if left is None else {
                    key: _text(value) for key, value in left.items()
                },
                "mirror": None if right is None else {
                    key: _text(value) for key, value in right.items()
                },
                "differences": differences,
            })
        mismatches.extend(ticket_mismatches)
        rows.append({
            "ticket": ticket, "observed_exits": len(observed),
            "mirror_exits": len(calculated), "comparisons": comparisons,
            "mismatches": ticket_mismatches,
        })
    return {
        "schema_version": 1, "comparison_contract": EXIT_DEAL_CONTRACT,
        "status": "blocked" if blockers else "mismatch" if mismatches else "exact",
        "exit_deal_facts_verified": not blockers and not mismatches,
        "policy_decision_sequence_verified": False,
        "broker_request_attempt_confirmation_sequence_verified": False,
        "observed_positions": len(evidence),
        "observed_exit_deals": sum(row["observed_exits"] for row in rows),
        "mirror_exit_deals": len(supplied),
        "blockers": list(dict.fromkeys(blockers)),
        "mismatches": list(dict.fromkeys(mismatches)), "rows": rows,
    }
