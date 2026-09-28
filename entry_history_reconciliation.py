"""Pure historical entry matching; this does not establish intent exclusivity.

The caller must supply complete, bounded reads from the declared account and
atomically exclude competing intents. No missing row proves absence of an effect.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from typing import Any

from mt5_protocol import BrokerRequest
from mt5_read_protocol import MAX_RECORDS
from mt5_trade_protocol import TradePreparation, validate_trade_request


# MT5 ENUM_ORDER_STATE / ENUM_DEAL_ENTRY, also defined by the installed package.
_ORDER_STATE_FILLED = 4
_DEAL_ENTRY_IN = 0
_DEAL_ENTRY_INOUT = 2
_DEAL_FIELDS = frozenset({"ticket", "order", "position_id", "time_msc", "entry", "type",
                          "symbol", "magic", "comment", "volume", "price"})
_ORDER_FIELDS = frozenset({"ticket", "position_id", "time_setup_msc", "time_done_msc",
                           "type", "state", "symbol", "magic", "comment",
                           "volume_initial", "volume_current"})


class EntryHistoryBlocked(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class EntryHistoryMatch:
    order: int
    position_id: int
    deal_ids: tuple[int, ...]
    volume: float
    price: float
    first_fill_msc: int
    last_fill_msc: int

    def to_dict(self):
        return {"order": self.order, "position_id": self.position_id,
                "deal_ids": list(self.deal_ids), "volume": self.volume, "price": self.price,
                "first_fill_msc": self.first_fill_msc, "last_fill_msc": self.last_fill_msc}


def _integer(value, reason, *, minimum=1):
    if type(value) is not int or not minimum <= value < 2**63:
        raise EntryHistoryBlocked(reason)
    return value


def _number(value, reason, *, positive=True):
    if type(value) not in (int, float):
        raise EntryHistoryBlocked(reason)
    try:
        valid = math.isfinite(value) and (value > 0 if positive else value >= 0)
    except OverflowError:
        valid = False
    if not valid:
        raise EntryHistoryBlocked(reason)
    return float(value)


def _same_volume(first, second):
    # Only floating representation tolerance, not a broker lot-step allowance.
    return abs(first - second) <= 4 * max(math.ulp(first), math.ulp(second))


def _records(value, *, kind):
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise EntryHistoryBlocked(f"{kind}_unavailable_or_invalid")
    if len(value) > MAX_RECORDS:
        raise EntryHistoryBlocked(f"{kind}_record_budget_exhausted")
    fields = _DEAL_FIELDS if kind == "deals" else _ORDER_FIELDS
    result, seen = [], set()
    for row in value:
        if not isinstance(row, Mapping) or not fields <= row.keys():
            raise EntryHistoryBlocked(f"{kind}_record_incomplete")
        row = dict(row)
        ticket = _integer(row["ticket"], f"{kind}_invalid_ticket")
        if ticket in seen:
            raise EntryHistoryBlocked(f"{kind}_duplicate_ticket")
        seen.add(ticket)
        for name in ("symbol", "comment"):
            if not isinstance(row[name], str):
                raise EntryHistoryBlocked(f"{kind}_invalid_{name}")
        for name in ("position_id", "magic", "type"):
            _integer(row[name], f"{kind}_invalid_{name}", minimum=0)
        if kind == "deals":
            for name in ("order", "time_msc", "entry"):
                _integer(row[name], f"{kind}_invalid_{name}", minimum=0)
            if row["entry"] not in (0, 1, 2, 3):
                raise EntryHistoryBlocked("deals_invalid_entry")
            numbers = ("volume", "price")
        else:
            for name in ("time_setup_msc", "time_done_msc", "state"):
                _integer(row[name], f"{kind}_invalid_{name}", minimum=0)
            numbers = ("volume_initial", "volume_current")
        # Non-trade account records may have zero quantities and no position.
        for name in numbers:
            _number(row[name], f"{kind}_invalid_{name}", positive=False)
        result.append(row)
    return result


def _owned(row, native):
    return all(row[name] == native[name] for name in ("symbol", "magic", "comment"))


def match_market_entry(
    request: BrokerRequest,
    preparation: TradePreparation,
    *,
    deals: Sequence[Mapping[str, Any]],
    orders: Sequence[Mapping[str, Any]],
    date_from_msc: int,
    date_to_msc: int,
    account_fingerprint: str,
) -> EntryHistoryMatch:
    """Match a full opening group, never an outcome, rejection or final money.

Bounds are inclusive broker-clock milliseconds. Matching owned rows outside
the causal interval block rather than being ranked or silently discarded.
"""
    if not isinstance(request, BrokerRequest):
        raise EntryHistoryBlocked("invalid_request")
    try:
        if request.intent_key.operation != "OPEN_MARKET":
            raise EntryHistoryBlocked("unsupported_operation")
        validate_trade_request(request)
    except EntryHistoryBlocked:
        raise
    except (AttributeError, ValueError, TypeError, OverflowError) as exc:
        raise EntryHistoryBlocked("invalid_request") from exc
    if account_fingerprint != request.intent_key.account_fingerprint:
        raise EntryHistoryBlocked("account_mismatch")
    if not isinstance(preparation, TradePreparation) or preparation.error is not None:
        raise EntryHistoryBlocked("successful_preparation_required")
    if any(getattr(request, name) != getattr(preparation, name)
           for name in ("request_id", "intent_id", "attempt_id", "action_id")):
        raise EntryHistoryBlocked("preparation_identity_mismatch")
    native = preparation.native_request
    side = 0 if request.payload["direction"] == "BUY" else 1
    if not isinstance(native, Mapping) or "position" in native or "position_by" in native:
        raise EntryHistoryBlocked("native_open_required")
    if (_integer(native.get("action"), "native_open_required") != 1
            or _integer(native.get("type"), "native_direction_mismatch", minimum=0) != side):
        raise EntryHistoryBlocked("native_open_required")
    _integer(native.get("magic"), "native_magic_invalid", minimum=0)
    if any(native.get(name) != request.payload[name] for name in ("symbol", "magic", "comment")):
        raise EntryHistoryBlocked("native_ownership_mismatch")
    volume = _number(native.get("volume"), "native_volume_invalid")
    if not _same_volume(volume, request.payload["volume"]):
        raise EntryHistoryBlocked("native_volume_mismatch")
    _number(native.get("price"), "native_price_invalid")
    lower = _integer(date_from_msc, "invalid_history_window", minimum=0)
    upper = _integer(date_to_msc, "invalid_history_window")
    tick = preparation.evidence.get("source_tick")
    if not isinstance(tick, Mapping):
        raise EntryHistoryBlocked("source_tick_missing")
    source_time = _integer(tick.get("time_msc"), "source_tick_clock_invalid")
    if not lower <= source_time <= upper:
        raise EntryHistoryBlocked("source_tick_outside_window")

    deal_rows = _records(deals, kind="deals")
    order_rows = _records(orders, kind="orders")
    groups = {}
    for row in deal_rows:
        if not _owned(row, native):
            continue
        if row["entry"] == _DEAL_ENTRY_INOUT:
            raise EntryHistoryBlocked("reversal_not_supported")
        if row["entry"] != _DEAL_ENTRY_IN:
            continue
        if row["type"] != side:
            raise EntryHistoryBlocked("deal_direction_mismatch")
        order_id = _integer(row["order"], "deal_order_invalid")
        position_id = _integer(row["position_id"], "deal_position_invalid")
        _number(row["volume"], "deal_volume_invalid")
        _number(row["price"], "deal_price_invalid")
        if not source_time <= row["time_msc"] <= upper:
            raise EntryHistoryBlocked("deal_outside_causal_window")
        groups.setdefault((order_id, position_id), []).append(row)
    if not groups:
        raise EntryHistoryBlocked("entry_not_found")
    if len(groups) != 1:
        raise EntryHistoryBlocked("multiple_entry_groups")
    (order_id, position_id), fills = next(iter(groups.items()))
    owned_orders = [row for row in order_rows if _owned(row, native) and row["type"] == side]
    if len(owned_orders) != 1 or owned_orders[0]["ticket"] != order_id:
        raise EntryHistoryBlocked("historical_order_missing_or_ambiguous")
    order = owned_orders[0]
    if order["position_id"] != position_id:
        raise EntryHistoryBlocked("historical_order_position_mismatch")
    if order["state"] != _ORDER_STATE_FILLED or order["volume_current"] != 0:
        raise EntryHistoryBlocked("historical_order_not_fully_filled")
    if not _same_volume(order["volume_initial"], volume):
        raise EntryHistoryBlocked("historical_order_volume_mismatch")
    if not source_time <= order["time_setup_msc"] <= order["time_done_msc"] <= upper:
        raise EntryHistoryBlocked("historical_order_clock_incoherent")
    # A row sharing this order may not disappear through the ownership filter.
    if any(row["order"] == order_id and (
        not _owned(row, native) or row["entry"] != _DEAL_ENTRY_IN
        or row["type"] != side or row["position_id"] != position_id
    ) for row in deal_rows):
        raise EntryHistoryBlocked("entry_order_contains_incompatible_deals")
    fills.sort(key=lambda row: (row["time_msc"], row["ticket"]))
    if not order["time_setup_msc"] <= fills[0]["time_msc"] <= fills[-1]["time_msc"] <= order["time_done_msc"]:
        raise EntryHistoryBlocked("fill_clock_outside_order")
    try:
        filled_volume = math.fsum(row["volume"] for row in fills)
        if not _same_volume(filled_volume, volume):
            raise EntryHistoryBlocked("entry_volume_incomplete_or_excessive")
        price = math.fsum(row["price"] * (row["volume"] / filled_volume) for row in fills)
    except OverflowError as exc:
        raise EntryHistoryBlocked("entry_aggregate_overflow") from exc
    _number(price, "entry_price_invalid")
    return EntryHistoryMatch(order_id, position_id, tuple(row["ticket"] for row in fills),
                             filled_volume, price, fills[0]["time_msc"], fills[-1]["time_msc"])
