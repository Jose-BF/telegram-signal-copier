"""Pure validation of reconciled MT5 positions, fills and account-currency costs.

Consumers must fail closed on blockers. Known identities remain in the mapping
even when incomplete; unavailable facts are None, never inferred zero values.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping


_CENT = Decimal("0.01")
_COST_FIELDS = ("profit", "swap", "commission", "fee")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _number(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _money(value: object) -> Decimal | None:
    parsed = _number(value)
    try:
        return None if parsed is None else parsed.quantize(_CENT, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None


def _integer(value: object) -> int | None:
    parsed = _number(value)
    if parsed is None or parsed != parsed.to_integral_value():
        return None
    return int(parsed)


def _identity(value: object) -> str | None:
    parsed = _integer(value)
    return str(parsed) if parsed is not None and parsed > 0 else None


def _utc(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _deal_time(deal: Mapping[str, Any], offset: int) -> datetime | None:
    seconds = _integer(deal.get("time"))
    millis = _integer(deal.get("time_msc"))
    if seconds is None or seconds < 0:
        return None
    if deal.get("time_msc") is not None and (millis is None or millis // 1000 != seconds):
        return None
    try:
        raw = _EPOCH + timedelta(seconds=seconds)
        if _utc(deal.get("time_utc")) != raw:
            return None
        precise = raw if millis is None else _EPOCH + timedelta(milliseconds=millis)
        return precise - timedelta(seconds=offset)
    except OverflowError:
        return None


def ledger_ticket_evidence(
    actual: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], tuple[str, ...]]:
    """Bind position summaries to their complete deal accounting, without I/O.

    Keys use position_id, not the opening deal's ticket (the replay adapter's
    identity contract). Multi-entry/reversal positions cannot represent one
    immutable SignalLeg and remain blocked. Close-by and partial exits are valid.
    Callers must establish EUR from the account snapshot before supplying these
    ledger records. An explicit contradictory currency is rejected here; absence
    is not independent evidence of the account currency.
    """
    evidence: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []
    if "account_currency" in actual and actual["account_currency"] != "EUR":
        blockers.append("ledger_account_currency_not_eur")
    positions = actual.get("positions")
    if not isinstance(positions, (list, tuple)):
        return {}, ("ledger_positions_missing_or_invalid",)
    count = _integer(actual.get("n_positions"))
    if count is None or count < 0 or count != len(positions):
        blockers.append("ledger_position_count_mismatch")
    if positions and actual.get("reconciled_ok") is not True:
        blockers.append("ledger_reconciliation_incomplete")
    seen_deals: set[str] = set()
    position_nets: list[Decimal | None] = []
    for index, position in enumerate(positions):
        if not isinstance(position, Mapping):
            blockers.append(f"ledger_position_invalid:{index}")
            position_nets.append(None)
            continue
        ticket = _identity(position.get("position_id"))
        if ticket is None:
            blockers.append(f"ledger_position_identity_missing:{index}")
            position_nets.append(None)
            continue
        if ticket in evidence:
            blockers.append(f"ledger_duplicate_position:{ticket}")
            evidence[ticket]["source_position_indices"].append(index)
            position_nets.append(None)
            continue
        issues: list[str] = []
        def block(reason: str) -> None:
            issues.append(f"ledger_{reason}:{ticket}")

        volume = _number(position.get("volume"))
        price = _number(position.get("open_price"))
        net = _money(position.get("pnl_net"))
        position_nets.append(net)
        opened_summary = _utc(position.get("open_dt_utc"))
        closed_summary = _utc(position.get("close_dt_utc"))
        if volume is None or volume <= 0 or price is None or price <= 0 or opened_summary is None:
            block("entry_facts_invalid")
        if position.get("is_closed") is not True or closed_summary is None:
            block("position_not_closed")
        if net is None:
            block("net_invalid")
        position_offset = position.get("mt5_time_offset_s")
        basket_offset = actual.get("mt5_time_offset_s")
        offset_value = position_offset if position_offset is not None else basket_offset
        offset = _integer(offset_value)
        if offset is None:
            block("time_offset_missing_or_invalid")
        if position_offset is not None and basket_offset is not None and _integer(position_offset) != _integer(basket_offset):
            block("time_offset_sources_disagree")
        deals = position.get("deals")
        if not isinstance(deals, (list, tuple)) or not deals:
            block("deals_missing")
            deals = ()
        totals = {field: Decimal("0.00") for field in _COST_FIELDS}
        costs_complete = bool(deals)
        incoming: list[tuple[Mapping[str, Any], datetime | None]] = []
        outgoing: list[tuple[Mapping[str, Any], datetime | None]] = []
        closed_volume = Decimal(0)
        volume_complete = bool(deals)
        for deal in deals:
            if not isinstance(deal, Mapping):
                block("deal_invalid")
                costs_complete = volume_complete = False
                continue
            deal_id = _identity(deal.get("ticket"))
            if deal_id is None:
                block("deal_identity_missing")
            elif deal_id in seen_deals:
                block(f"duplicate_deal:{deal_id}")
            else:
                seen_deals.add(deal_id)
            if _identity(deal.get("position_id")) != ticket:
                block("deal_position_mismatch")
            when = _deal_time(deal, offset) if offset is not None else None
            if when is None:
                block("deal_time_invalid")
            entry = _integer(deal.get("entry"))
            amount = _number(deal.get("volume"))
            if amount is None or amount <= 0:
                block("deal_volume_invalid")
                volume_complete = False
            if entry == 0:
                incoming.append((deal, when))
            elif entry in (1, 3):
                outgoing.append((deal, when))
                if amount is not None:
                    closed_volume += amount
            else:
                block("deal_entry_unsupported")
            if _number(deal.get("price")) is None or _number(deal.get("price")) <= 0:
                block("deal_price_invalid")
            for field in _COST_FIELDS:
                cost = _money(deal.get(field))
                if cost is None:
                    block(f"deal_cost_missing:{field}")
                    costs_complete = False
                else:
                    totals[field] += cost
        opened_at = None
        closed_at = None
        direction = None
        symbol = None
        if len(incoming) != 1:
            block("opening_deal_count_unsupported")
        else:
            opening, opened_at = incoming[0]
            side = _integer(opening.get("type"))
            direction = {0: "BUY", 1: "SELL"}.get(side)
            symbol = str(opening.get("symbol") or "").strip().upper() or None
            if direction is None or actual.get("direction") != direction:
                block("direction_mismatch_or_missing")
            if position.get("direction") is not None and position.get("direction") != direction:
                block("position_direction_mismatch")
            if symbol is None:
                block("symbol_missing")
            for source in (actual, position):
                if source.get("symbol") is not None and str(source["symbol"]).upper() != symbol:
                    block("symbol_summary_mismatch")
            for deal, _ in incoming + outgoing:
                if str(deal.get("symbol") or "").strip().upper() != symbol:
                    block("deal_symbol_mismatch")
                expected_side = side if _integer(deal.get("entry")) == 0 else (1 - side if side in (0, 1) else None)
                if expected_side is None or _integer(deal.get("type")) != expected_side:
                    block("deal_direction_mismatch")
            if (
                _identity(position.get("ticket")) != _identity(opening.get("ticket"))
                or position.get("open_deal") != opening
            ):
                block("opening_deal_summary_mismatch")
            if (
                _number(opening.get("price")) != price
                or _number(opening.get("volume")) != volume
                or opened_at is None
                or opened_at.replace(microsecond=0) != opened_summary
            ):
                block("opening_facts_summary_mismatch")
        if not outgoing:
            block("closing_deals_missing")
        else:
            timed_exits = [(d, t) for d, t in outgoing if t is not None]
            if len(timed_exits) == len(outgoing):
                timed_exits.sort(key=lambda item: item[1])
                first_close, first_time = timed_exits[0]
                closed_at = timed_exits[-1][1]
                # The reconciler stores the first exit, not the final partial.
                if (
                    position.get("close_deal") != first_close
                    or _number(position.get("close_price")) != _number(first_close.get("price"))
                    or closed_summary != first_time.replace(microsecond=0)
                ):
                    block("closing_deal_summary_mismatch")
                if opened_at is None or first_time < opened_at:
                    block("deal_chronology_invalid")
        if not volume_complete or closed_volume != volume:
            block("closed_volume_mismatch")
        components = position.get("pnl_components")
        calculated_net = sum(totals.values(), Decimal(0)) if costs_complete else None
        if not isinstance(components, Mapping):
            block("cost_components_missing")
        else:
            for field in _COST_FIELDS:
                if _money(components.get(field)) is None or not costs_complete or _money(components.get(field)) != totals[field]:
                    block(f"cost_component_mismatch:{field}")
            if _money(components.get("net")) is None or _money(components.get("net")) != calculated_net:
                block("component_net_mismatch")
        if calculated_net is None or net != calculated_net:
            block("deal_net_mismatch")
        evidence[ticket] = {
            "ticket": ticket, "opened_at": opened_at, "open_price": price,
            "volume": volume, "closed_at": closed_at, "net_eur": net,
            "direction": direction, "symbol": symbol,
            "closed_volume": closed_volume if volume_complete else None,
            "deal_count": len(deals), "costs": totals if costs_complete else None,
            "source_position_indices": [index], "blockers": tuple(dict.fromkeys(issues)),
            "opening_net_eur": sum(
                (_money(deal.get(field)) for deal, _ in incoming for field in _COST_FIELDS),
                Decimal(0),
            ) if costs_complete else None,
            "exit_deals": tuple({
                "deal_ticket": _identity(deal.get("ticket")),
                "order_ticket": _identity(deal.get("order")),
                "position_id": _identity(deal.get("position_id")),
                "expected_position_id": ticket, "closed_at": when,
                "millisecond_time_verified": when is not None and _integer(deal.get("time_msc")) is not None,
                "entry_kind": _integer(deal.get("entry")),
                "broker_reason": _integer(deal.get("reason")),
                "entry_price": price, "exit_price": _number(deal.get("price")),
                "volume": _number(deal.get("volume")),
                "net_eur": sum(
                    (_money(deal.get(field)) for field in _COST_FIELDS), Decimal(0),
                ) if costs_complete else None,
            } for deal, when in outgoing),
        }
        blockers.extend(issues)
    declared = _money(actual.get("pnl_real_mt5"))
    if declared is None or any(net is None for net in position_nets):
        blockers.append("ledger_basket_money_unverified")
    elif sum(position_nets, Decimal(0)) != declared:
        blockers.append("ledger_position_money_sum_mismatch")
    return evidence, tuple(dict.fromkeys(blockers))
