"""Fail-closed invariants for counterfactuals anchored to executed MT5 trades."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from math import isclose
from typing import Iterable, Mapping

import strategy_policies


SCHEMA_VERSION = 1
ENTRY_AUTHORITY = "mt5_deals"
ENTRY_POLICY = "actual_mt5"
ALLOWED_CHANGED_RULES = {
    "closed_at_management_trigger",
    "policy_be",
    "ignored_be_sl",
}


def _trade_id(trade: Mapping) -> str:
    return str(trade.get("sig_id") or "")


def _ticket_id(ticket: Mapping) -> str:
    return str(ticket.get("ticket") or ticket.get("position_id") or "")


def _normalise_time(value: object) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _same_number(left: object, right: object) -> bool:
    try:
        return isclose(
            float(left),
            float(right),
            rel_tol=0.0,
            abs_tol=1e-9,
        )
    except (TypeError, ValueError):
        return left is None and right is None


def _policy_id(policy: strategy_policies.StrategyPolicy) -> str:
    return str(policy.policy_id)


def _policy_entry_policy(
    policy: strategy_policies.StrategyPolicy,
    row: Mapping,
) -> str:
    snapshot = row.get("policy")
    if isinstance(snapshot, Mapping) and snapshot.get("entry_policy"):
        return str(snapshot["entry_policy"])
    return str(policy.entry_policy)


def _entry_blockers(
    *,
    sig_id: str,
    policy_id: str,
    trade: Mapping,
    row: Mapping,
) -> list[str]:
    blockers: list[str] = []
    if row.get("entry_authority") != ENTRY_AUTHORITY:
        blockers.append(
            f"entry_authority_mismatch:{sig_id}:{policy_id}"
        )

    expected_tickets = {
        _ticket_id(ticket): ticket
        for ticket in trade.get("tickets") or []
        if _ticket_id(ticket)
    }
    emitted_tickets = {
        _ticket_id(ticket): ticket
        for ticket in row.get("tickets") or []
        if _ticket_id(ticket)
    }
    if set(emitted_tickets) != set(expected_tickets):
        blockers.append(f"ticket_set_mismatch:{sig_id}:{policy_id}")
        return blockers

    field_pairs = (
        ("open_dt_utc", "open_time_utc", _normalise_time),
        ("open_price", "open_price", lambda value: value),
        ("volume", "volume", lambda value: value),
    )
    for ticket_id, expected in expected_tickets.items():
        emitted = emitted_tickets[ticket_id]
        for expected_key, emitted_key, normaliser in field_pairs:
            expected_value = normaliser(expected.get(expected_key))
            emitted_value = normaliser(emitted.get(emitted_key))
            same = (
                expected_value == emitted_value
                if expected_key == "open_dt_utc"
                else _same_number(expected_value, emitted_value)
            )
            if not same:
                blockers.append(
                    f"entry_mismatch:{sig_id}:{policy_id}:"
                    f"{ticket_id}:{expected_key}"
                )

        changed_rules = emitted.get("changed_rules") or []
        unsupported = sorted(
            str(rule)
            for rule in changed_rules
            if str(rule) not in ALLOWED_CHANGED_RULES
        )
        blockers.extend(
            f"entry_or_unknown_rule:{sig_id}:{policy_id}:"
            f"{ticket_id}:{rule}"
            for rule in unsupported
        )
    return blockers


def validate_contract(
    trades: Iterable[dict],
    policies: Iterable[strategy_policies.StrategyPolicy],
    rows_by_policy: Mapping[str, Iterable[dict]],
) -> dict:
    """Validate row accounting and immutable MT5 entry facts."""
    trades = list(trades)
    policies = list(policies)
    policy_ids = [_policy_id(policy) for policy in policies]
    trade_ids = [_trade_id(trade) for trade in trades]
    blockers: list[str] = []
    if not trades:
        blockers.append("no_executed_trades")
    if not policies:
        blockers.append("no_policies")

    duplicate_trade_ids = sorted(
        sig_id
        for sig_id, count in Counter(trade_ids).items()
        if not sig_id or count > 1
    )
    blockers.extend(
        f"duplicate_or_missing_trade_id:{sig_id or '<empty>'}"
        for sig_id in duplicate_trade_ids
    )
    duplicate_policy_ids = sorted(
        policy_id
        for policy_id, count in Counter(policy_ids).items()
        if not policy_id or count > 1
    )
    blockers.extend(
        f"duplicate_or_missing_policy_id:{policy_id or '<empty>'}"
        for policy_id in duplicate_policy_ids
    )

    policy_by_id = {
        _policy_id(policy): policy
        for policy in policies
        if _policy_id(policy)
    }
    trade_by_id = {
        _trade_id(trade): trade
        for trade in trades
        if _trade_id(trade)
    }
    emitted: dict[tuple[str, str], list[dict]] = {}
    rows_emitted = 0
    blocked_rows = 0
    entry_failures: set[tuple[str, str]] = set()

    for container_policy_id, rows in rows_by_policy.items():
        for row in rows:
            rows_emitted += 1
            sig_id = str(row.get("sig_id") or "")
            row_policy_id = str(
                row.get("strategy")
                or (row.get("policy") or {}).get("policy_id")
                or container_policy_id
            )
            emitted.setdefault((sig_id, row_policy_id), []).append(row)
            if row.get("status") == "blocked":
                blocked_rows += 1

    expected_pairs = {
        (sig_id, policy_id)
        for sig_id in trade_by_id
        for policy_id in policy_by_id
    }
    emitted_pairs = set(emitted)
    for sig_id, policy_id in sorted(expected_pairs - emitted_pairs):
        blockers.append(f"missing_row:{sig_id}:{policy_id}")
    for sig_id, policy_id in sorted(emitted_pairs - expected_pairs):
        blockers.append(
            f"unexpected_row:{sig_id or '<empty>'}:{policy_id or '<empty>'}"
        )
    for pair, rows in sorted(emitted.items()):
        if len(rows) > 1:
            blockers.append(f"duplicate_row:{pair[0]}:{pair[1]}")

    for sig_id, policy_id in sorted(expected_pairs & emitted_pairs):
        rows = emitted[(sig_id, policy_id)]
        if len(rows) != 1:
            continue
        row = rows[0]
        policy = policy_by_id[policy_id]
        if _policy_entry_policy(policy, row) != ENTRY_POLICY:
            blockers.append(f"non_mt5_entry_policy:{policy_id}")
        if row.get("status") == "blocked":
            blockers.append(f"blocked_row:{sig_id}:{policy_id}")
            continue
        row_entry_blockers = _entry_blockers(
            sig_id=sig_id,
            policy_id=policy_id,
            trade=trade_by_id[sig_id],
            row=row,
        )
        if row_entry_blockers:
            entry_failures.add((sig_id, policy_id))
            blockers.extend(row_entry_blockers)
        if policy.mode == "follow_actual" and not _same_number(
            row.get("strategy_pnl"),
            row.get("actual_pnl"),
        ):
            blockers.append(f"actual_baseline_money_mismatch:{sig_id}")

    blockers = list(dict.fromkeys(blockers))
    rows_expected = len(expected_pairs)
    return {
        "schema_version": SCHEMA_VERSION,
        "universe": "executed_mt5",
        "trades_expected": len(trade_by_id),
        "policies_expected": len(policy_by_id),
        "rows_expected": rows_expected,
        "rows_emitted": rows_emitted,
        "blocked_rows": blocked_rows,
        "entry_invariant_failures": len(entry_failures),
        "complete": not blockers and rows_emitted == rows_expected,
        "blockers": blockers,
    }
