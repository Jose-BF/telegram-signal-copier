"""Bind captured close fills to native broker identities, without live imports.

This is execution attribution, not an audit of journal authenticity, full causal
manifests, strategy decisions or whether an alternative fill was realizable.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from mt5_deal_reason import DEAL_REASON_NAMES

from .ledger_evidence import _identity, _integer, _number, _utc


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _broker_time(row, offset, *, order=False):
    seconds = _integer(row.get("time_done" if order else "time"))
    millis = _integer(row.get("time_done_msc" if order else "time_msc"))
    if seconds is None or millis is None or millis // 1000 != seconds or millis < 0:
        return None
    try:
        return _EPOCH + timedelta(milliseconds=millis, seconds=-offset)
    except OverflowError:
        return None


def bind_close_attempts_to_deals(
    *, events: Iterable[Mapping[str, Any]],
    broker_deals: Iterable[Mapping[str, Any]],
    broker_orders: Iterable[Mapping[str, Any]],
    position_signal: Mapping[str, str],
    mt5_time_offset_s: int | None,
) -> dict[str, Any]:
    """Attribute single-deal close orders only after checking all retained facts.

    Failed/redundant attempts stay visible without inventing a fill. A TP/SL
    on the same position is not attributed to an unsuccessful close request.
    Multi-deal orders and unbound expert exits require a stronger contract.
    """
    blockers: list[str] = []
    input_counts: dict[str, int | None] = {}
    invalid_sources: list[str] = []
    invalid_rows: list[dict[str, Any]] = []

    def invalid_row(source, index, reason):
        invalid_rows.append({"source": source, "row_index": index, "reason": reason})
        blockers.append(f"{source}:{index}:{reason}")

    def source_rows(values, source):
        try:
            if values is None or isinstance(values, (str, bytes, Mapping)):
                raise TypeError
            retained = tuple(values)
        except TypeError:
            input_counts[source] = None
            invalid_sources.append(source)
            blockers.append(f"{source}_source_invalid")
            return []
        input_counts[source] = len(retained)
        valid = []
        for index, row in enumerate(retained):
            if not isinstance(row, Mapping):
                invalid_row(source, index, "row_not_mapping")
            else:
                valid.append((index, row))
        return valid

    events = source_rows(events, "events")
    broker_deals = source_rows(broker_deals, "broker_deals")
    broker_orders = source_rows(broker_orders, "broker_orders")
    selected = {}
    if not isinstance(position_signal, Mapping):
        input_counts["position_signal"] = None
        invalid_sources.append("position_signal")
        blockers.append("position_signal_source_invalid")
    else:
        input_counts["position_signal"] = len(position_signal)
        for index, (key, value) in enumerate(position_signal.items()):
            identity = _identity(key) if _nonempty_string(key) else None
            if identity is None or not _nonempty_string(value):
                invalid_row("position_signal", index, "identity_or_signal_invalid")
            elif identity in selected:
                invalid_row("position_signal", index, "position_identity_duplicate")
            else:
                selected[identity] = value
    if not selected:
        blockers.append("selected_position_identity_invalid_or_empty")
    clock_valid = type(mt5_time_offset_s) is int and abs(mt5_time_offset_s) <= 86400
    if not clock_valid:
        blockers.append("broker_clock_offset_unverified")

    def index_rows(rows, label, source):
        indexed = {}
        for index, row in rows:
            key = _identity(row.get("ticket"))
            if key is None or key in indexed:
                invalid_row(source, index, f"{label}_identity_missing_or_duplicate")
            else:
                indexed[key] = row
        return indexed

    deals = index_rows(broker_deals, "broker_deal", "broker_deals")
    orders = index_rows(broker_orders, "broker_order", "broker_orders")
    closing = {
        key: deal for key, deal in deals.items()
        if _identity(deal.get("position_id")) in selected
        and _integer(deal.get("entry")) in (1, 3)
    }
    for position in sorted(set(selected) - {_identity(deal.get("position_id")) for deal in closing.values()}):
        blockers.append(f"selected_position_exit_missing:{position}")
    by_order = defaultdict(list)
    # Count every retained deal of an order, including entries, other positions
    # and duplicate/malformed tickets; filtering first can hide a multi-deal fill.
    for _, deal in broker_deals:
        order_id = _identity(deal.get("order"))
        if order_id is not None:
            by_order[order_id].append(deal)
    requests = defaultdict(list)
    confirmations = defaultdict(list)
    event_issues = defaultdict(list)
    for index, row in events:
        kind = row.get("ev")
        is_attempt = kind == "mt5_action_attempt" and row.get("operation") == "CLOSE_POSITION"
        if kind not in ("mt5_close_requested", "mt5_close_result") and not is_attempt:
            continue
        fields = ("action_id", "decision_id", "sig")
        if kind != "mt5_close_requested":
            fields += ("attempt_id",)
        for field in fields:
            if not _nonempty_string(row.get(field)):
                event_issues[index].append(f"{field}_invalid")
                invalid_row("events", index, f"{field}_invalid")
        if _identity(row.get("ticket")) is None:
            event_issues[index].append("position_identity_invalid")
            invalid_row("events", index, "position_identity_invalid")
        if event_issues[index]:
            continue
        if row.get("ev") == "mt5_close_requested":
            requests[row.get("action_id")].append(row)
        elif row.get("ev") == "mt5_close_result":
            confirmations[row.get("attempt_id")].append(row)
    attempts = []
    seen_attempts = set()
    attributed = {}
    for index, event in events:
        if event.get("ev") != "mt5_action_attempt" or event.get("operation") != "CLOSE_POSITION":
            continue
        if _identity(event.get("ticket")) not in selected:
            continue
        attempt_id = event.get("attempt_id")
        issues = list(event_issues[index])
        if not _nonempty_string(attempt_id) or attempt_id in seen_attempts:
            issues.append("attempt_identity_missing_or_duplicate")
        if _nonempty_string(attempt_id):
            seen_attempts.add(attempt_id)
        result = event.get("result") if isinstance(event.get("result"), Mapping) else {}
        retcode = result.get("retcode")
        if type(retcode) is not int:
            issues.append("retcode_invalid")
        if retcode == 10010:
            issues.append("partial_return_code_contract_unverified")
        deal_id = _identity(result.get("deal"))
        row = {
            "event_index": index, "signal_id": event.get("sig"), "attempt_id": attempt_id,
            "action_id": event.get("action_id"), "position_id": str(event.get("ticket")),
            "retcode": retcode, "deal_ticket": deal_id,
            "broker_request_sent": event.get("broker_request_sent"),
            "status": "no_fill_bound", "blockers": issues,
        }
        if deal_id is None:
            if retcode in (10009, 10010):
                issues.append("successful_retcode_without_bound_deal")
        else:
            request = event.get("request") if isinstance(event.get("request"), Mapping) else {}
            before = event.get("position_before") if isinstance(event.get("position_before"), Mapping) else {}
            target = _identity(request.get("position"))
            deal = closing.get(deal_id, {})
            order_id = _identity(result.get("order"))
            order = orders.get(order_id, {})
            action_id = event.get("action_id")
            roots = requests.get(action_id, ()) if _nonempty_string(action_id) else ()
            ends = confirmations.get(attempt_id, ()) if _nonempty_string(attempt_id) else ()
            if event.get("broker_request_sent") is not True:
                issues.append("fill_claim_without_sent_request")
            if (
                _integer(request.get("action")) != 1 or not target or target != _identity(event.get("ticket"))
                or _identity(before.get("ticket")) != target or selected.get(target) != event.get("sig")
                or _identity(deal.get("position_id")) != target
                or _identity(order.get("position_id")) != target
                or _identity(deal.get("order")) != order_id or not order_id
            ):
                issues.append("broker_fill_identity_mismatch")
            order_deals = by_order.get(order_id, ())
            if not deal or len(order_deals) != 1 or _identity(order_deals[0].get("ticket")) != deal_id:
                issues.append("single_deal_close_order_unverified")
            if _integer(deal.get("entry")) != 1 or deal.get("reason") != 3 or order.get("reason") != 3:
                issues.append("broker_deal_is_not_this_expert_close")
            for field in ("symbol", "magic"):
                value = request.get(field)
                valid = isinstance(value, str) and bool(value) if field == "symbol" else _identity(value) is not None
                if not valid or any(source.get(field) != value for source in (before, deal, order)):
                    issues.append(f"broker_fill_{field}_mismatch")
            side = _integer(before.get("type"))
            if side not in (0, 1) or any(_integer(source.get("type")) != 1 - side for source in (request, deal, order)):
                issues.append("broker_fill_direction_mismatch")
            volume = _number(deal.get("volume"))
            available = _number(before.get("volume"))
            if (
                volume is None or volume <= 0 or available is None or available < volume
                or any(_number(source.get("volume")) != volume for source in (request, result))
                or _number(order.get("volume_initial")) != volume
                or _number(order.get("volume_current")) != 0 or order.get("state") != 4
            ):
                issues.append("broker_fill_volume_or_order_state_mismatch")
            price = _number(deal.get("price"))
            if price is None or price <= 0 or _number(result.get("price")) != price:
                issues.append("broker_fill_price_mismatch")
            start = _utc(event.get("broker_request_started_utc"))
            finish = _utc(event.get("broker_response_received_utc"))
            logged = _utc(event.get("ts"))
            fill_time = _broker_time(deal, mt5_time_offset_s) if clock_valid else None
            order_time = _broker_time(order, mt5_time_offset_s, order=True) if clock_valid else None
            if None in (start, finish, logged, fill_time, order_time) or not (
                start <= fill_time <= order_time <= finish <= logged
            ):
                issues.append("broker_fill_time_outside_attempt")
            if len(roots) != 1 or len(ends) != 1:
                issues.append("close_request_or_confirmation_missing_or_duplicate")
            else:
                root, end = roots[0], ends[0]
                for source in (root, end):
                    if (
                        type(event.get("action_revision")) is not int or event["action_revision"] < 0
                        or _identity(source.get("ticket")) != target
                        or any(not event.get(field) or source.get(field) != event.get(field)
                               for field in ("action_id", "decision_id", "sig"))
                        or source.get("action_revision") != event.get("action_revision")
                    ):
                        issues.append("close_request_or_confirmation_identity_mismatch")
                root_time, end_time = _utc(root.get("ts")), _utc(end.get("ts"))
                if (
                    None in (root_time, end_time, start, logged)
                    or not root_time <= start <= logged <= end_time
                    or type(end.get("retcode")) is not int or end.get("retcode") != retcode
                ):
                    issues.append("close_confirmation_time_or_result_mismatch")
            if deal_id in attributed:
                issues.append("deal_claimed_by_multiple_attempts")
            if not issues:
                row["status"] = "fill_bound" if retcode == 10009 else "fill_bound_with_non_done_retcode"
                row["closed_at"] = fill_time.isoformat()
                row["order_ticket"] = order_id
                attributed[deal_id] = attempt_id
        if issues:
            row["status"] = "blocked"
            blockers.extend(f"close_attempt:{attempt_id}:{issue}" for issue in issues)
        attempts.append(row)

    exit_rows = []
    for key, deal in sorted(closing.items()):
        order_id = _identity(deal.get("order"))
        order = orders.get(order_id, {})
        deal_time = _broker_time(deal, mt5_time_offset_s) if clock_valid else None
        order_time = _broker_time(order, mt5_time_offset_s, order=True) if clock_valid else None
        volume = _number(deal.get("volume"))
        order_deals = by_order.get(order_id, ())
        order_volumes = [_number(item.get("volume")) for item in order_deals]
        complete_volume = sum(order_volumes) if all(value is not None and value > 0 for value in order_volumes) else None
        exit_issues = []
        price = _number(deal.get("price"))
        if price is None or price <= 0:
            exit_issues.append(f"observed_exit_price_invalid:{key}")
        if _integer(deal.get("entry")) != 1:
            exit_issues.append(f"observed_exit_entry_contract_unverified:{key}")
        if (
            not order_id or not order or _identity(order.get("position_id")) != _identity(deal.get("position_id"))
            or _integer(deal.get("type")) not in (0, 1)
            or _integer(order.get("type")) != _integer(deal.get("type"))
            or not isinstance(deal.get("symbol"), str) or not deal.get("symbol")
            or order.get("symbol") != deal.get("symbol")
            or _identity(deal.get("magic")) is None
            or order.get("magic") != deal.get("magic")
            or _integer(order.get("reason")) != _integer(deal.get("reason"))
            or deal_time is None or order_time is None or order_time < deal_time
            or volume is None or volume <= 0 or complete_volume is None
            or any(_identity(item.get("position_id")) != _identity(deal.get("position_id"))
                   or _integer(item.get("entry")) != 1 for item in order_deals)
            or complete_volume != _number(order.get("volume_initial"))
            or _number(order.get("volume_current")) != 0 or _integer(order.get("state")) != 4
        ):
            exit_issues.append(f"observed_exit_order_or_clock_unverified:{key}")
        mechanism = DEAL_REASON_NAMES.get(_integer(deal.get("reason")), "unknown")
        status = "bound_expert_close" if key in attributed else "passive_broker_exit" if mechanism in ("tp", "sl") else "unattributed_exit"
        if status == "unattributed_exit":
            exit_issues.append(f"observed_exit_cause_unbound:{key}")
        blockers.extend(exit_issues)
        exit_rows.append({
            "deal_ticket": key, "order_ticket": _identity(deal.get("order")),
            "position_id": _identity(deal.get("position_id")), "mechanism": mechanism,
            "status": "blocked" if exit_issues else status,
            "attempt_id": attributed.get(key), "blockers": exit_issues,
        })
    return {
        "schema_version": 1, "comparison_contract": "close_attempt_native_deal_binding_v1",
        "selected_positions": len(selected), "observed_exit_deals": len(closing),
        "close_attempts": len(attempts), "bound_close_deals": len(attributed),
        "status": "blocked" if blockers else "attribution_checked",
        "full_policy_and_causal_sequence_verified": False,
        "input_counts": input_counts, "invalid_sources": invalid_sources,
        "invalid_rows": invalid_rows,
        "blockers": list(dict.fromkeys(blockers)), "attempts": attempts, "exit_rows": exit_rows,
    }
