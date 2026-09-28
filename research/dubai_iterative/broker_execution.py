"""Deterministic offline hypothesis for broker-side SL/TP lifecycle behavior.

The kernel owns position and installed-level state. A modify request becomes
eligible after a fixed processing delay, but is evaluated only when a quote is
received. Acceptance installs both requested levels atomically at that quote's
time. Acknowledgement transport latency is intentionally outside this model;
the processing delay must not be interpreted as a universal claim that a broker
waits for an acknowledgement before making a level effective.

For the supported ``fixed_point_distance`` hypothesis, BUY distances use Bid
and SELL distances use Ask. ``min_stop_distance_points`` and
``freeze_distance_points`` are applied independently to proposed non-null
levels. Existing passive exits have priority over a modification processed at
the same quote. These are explicit research assumptions, not an MT5 emulator or
execution certification. In observed-fill mode, terminal quote touches are
evidence only: an independently observed broker deal closes the position.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import math

from .passive_fill_contract import PassiveFillScenario


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_SUPPORTED_RESTRICTION_HYPOTHESIS = "fixed_point_distance"


@dataclass(frozen=True)
class EventTime:
    """Timezone-aware instant with a sub-microsecond nanosecond remainder."""

    at: datetime
    nanosecond: int = 0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.at, datetime)
            or self.at.tzinfo is None
            or self.at.utcoffset() is None
        ):
            raise ValueError("event time must be a timezone-aware datetime")
        if (
            isinstance(self.nanosecond, bool)
            or not isinstance(self.nanosecond, int)
            or not 0 <= self.nanosecond <= 999
        ):
            raise ValueError("nanosecond must be an integer from 0 to 999")

    @property
    def epoch_ns(self) -> int:
        delta = self.at.astimezone(timezone.utc) - _EPOCH
        microseconds = (
            (delta.days * 86_400 + delta.seconds) * 1_000_000
            + delta.microseconds
        )
        return microseconds * 1_000 + self.nanosecond


@dataclass(frozen=True)
class Quote:
    at: EventTime
    bid: float
    ask: float

    def __post_init__(self) -> None:
        if not isinstance(self.at, EventTime):
            raise ValueError("quote time must be EventTime")
        _validate_price("bid", self.bid)
        _validate_price("ask", self.ask)
        if self.ask < self.bid:
            raise ValueError("ask must be greater than or equal to bid")


@dataclass(frozen=True)
class SymbolConstraints:
    symbol: str
    point: float
    digits: int
    min_stop_distance_points: int | None
    freeze_distance_points: int | None
    restriction_hypothesis: str | None = _SUPPORTED_RESTRICTION_HYPOTHESIS

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        if not _positive_finite(self.point):
            raise ValueError("point must be positive and finite")
        if (
            isinstance(self.digits, bool)
            or not isinstance(self.digits, int)
            or not 0 <= self.digits <= 12
        ):
            raise ValueError("digits must be an integer from 0 to 12")
        expected_point = 10.0 ** -self.digits
        if not math.isclose(
            self.point,
            expected_point,
            rel_tol=0.0,
            abs_tol=max(expected_point * 1e-12, 1e-15),
        ):
            raise ValueError("point must match the declared digits")
        for name in ("min_stop_distance_points", "freeze_distance_points"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer or None")
        if self.restriction_hypothesis not in (
            None,
            _SUPPORTED_RESTRICTION_HYPOTHESIS,
        ):
            raise ValueError(
                f"unsupported restriction hypothesis: {self.restriction_hypothesis}"
            )

    @property
    def restrictions_known(self) -> bool:
        return (
            self.restriction_hypothesis == _SUPPORTED_RESTRICTION_HYPOTHESIS
            and self.min_stop_distance_points is not None
            and self.freeze_distance_points is not None
        )


@dataclass(frozen=True)
class Position:
    position_id: str
    side: str
    opened_at: EventTime
    entry_price: float
    sl: float | None = None
    tp: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.position_id, str) or not self.position_id.strip():
            raise ValueError("position_id must be a non-empty string")
        if self.side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if not isinstance(self.opened_at, EventTime):
            raise ValueError("opened_at must be EventTime")
        _validate_price("entry_price", self.entry_price)
        _validate_optional_price("sl", self.sl)
        _validate_optional_price("tp", self.tp)
        if self.sl is not None and self.tp is not None:
            ordered = self.sl < self.tp if self.side == "BUY" else self.tp < self.sl
            if not ordered:
                raise ValueError("installed SL/TP ordering is invalid for side")


@dataclass(frozen=True)
class PositionState:
    position_id: str
    side: str
    opened_at: EventTime
    entry_price: float
    status: str
    sl: float | None
    tp: float | None
    revision: int
    levels_effective_at: EventTime | None
    closed_at: EventTime | None
    close_price: float | None
    close_reason: str | None


@dataclass(frozen=True)
class ModifySubmission:
    request_id: str
    position_id: str
    requested_at: EventTime
    processing_not_before_ns: int | None
    expected_revision: int
    status: str
    reason: str | None


@dataclass(frozen=True)
class ModifyResult:
    request_id: str
    position_id: str
    requested_at: EventTime
    processing_not_before_ns: int
    processed_at: EventTime
    effective_at: EventTime | None
    requested_revision: int
    effective_revision: int | None
    status: str
    reason: str | None


@dataclass(frozen=True)
class PositionExit:
    position_id: str
    side: str
    reason: str
    installed_level: float
    price: float
    effective_at: EventTime
    first_terminal_touch_at: EventTime | None = None


@dataclass(frozen=True)
class PassiveTouch:
    position_id: str
    side: str
    reason: str
    installed_level: float
    market_price: float
    observed_at: EventTime


@dataclass(frozen=True)
class QuoteUpdate:
    quote: Quote
    exits: tuple[PositionExit, ...]
    modifications: tuple[ModifyResult, ...]
    touches: tuple[PassiveTouch, ...] = ()


@dataclass
class _PositionRecord:
    position_id: str
    side: str
    opened_at: EventTime
    entry_price: float
    sl: float | None
    tp: float | None
    revision: int
    levels_effective_at: EventTime | None
    status: str = "open"
    closed_at: EventTime | None = None
    close_price: float | None = None
    close_reason: str | None = None
    observed_touches: dict[tuple[str, float, int], EventTime] = field(default_factory=dict)
    pending_passive: _PendingPassive | None = None


@dataclass(frozen=True)
class _PendingModify:
    sequence: int
    request_id: str
    position_id: str
    requested_at: EventTime
    processing_not_before_ns: int
    expected_revision: int
    sl: float | None
    tp: float | None


@dataclass(frozen=True)
class _PendingPassive:
    reason: str
    installed_level: float
    touched_at: EventTime
    due_ns: int
    revision: int


class BrokerExecutionModel:
    """Own-state broker lifecycle kernel with no external or live dependencies."""

    def __init__(
        self,
        constraints: SymbolConstraints,
        *,
        modify_processing_delay_ns: int,
        passive_exit_mode: str = "immediate_on_quote",
        passive_fill_scenario: PassiveFillScenario | None = None,
    ) -> None:
        if not isinstance(constraints, SymbolConstraints):
            raise ValueError("constraints must be SymbolConstraints")
        if (
            isinstance(modify_processing_delay_ns, bool)
            or not isinstance(modify_processing_delay_ns, int)
            or modify_processing_delay_ns < 0
        ):
            raise ValueError("modify_processing_delay_ns must be non-negative")
        if passive_exit_mode not in {"immediate_on_quote", "observed_fill",
                                     "quote_delay_scenario"}:
            raise ValueError("unsupported passive_exit_mode")
        if (passive_exit_mode == "quote_delay_scenario") != isinstance(
            passive_fill_scenario, PassiveFillScenario
        ):
            raise ValueError("quote_delay_scenario requires a PassiveFillScenario")
        self.constraints = constraints
        self.modify_processing_delay_ns = modify_processing_delay_ns
        self.passive_exit_mode = passive_exit_mode
        self.passive_fill_scenario = passive_fill_scenario
        self._positions: dict[str, _PositionRecord] = {}
        self._pending: dict[str, _PendingModify] = {}
        self._clock_ns: int | None = None
        self._last_quote_ns: int | None = None
        self._next_sequence = 1

    @property
    def inflight_count(self) -> int:
        return len(self._pending)

    @property
    def pending_passive_count(self) -> int:
        return sum(record.pending_passive is not None
                   for record in self._positions.values())

    def add_position(self, position: Position) -> PositionState:
        if not isinstance(position, Position):
            raise ValueError("position must be Position")
        if position.position_id in self._positions:
            raise ValueError(f"duplicate position_id: {position.position_id}")
        self._ensure_not_before_clock(position.opened_at, "position opening")
        self._price_to_ticks(position.entry_price, "entry_price")
        if position.sl is not None:
            self._price_to_ticks(position.sl, "sl")
        if position.tp is not None:
            self._price_to_ticks(position.tp, "tp")
        self._clock_ns = position.opened_at.epoch_ns
        record = _PositionRecord(
            position_id=position.position_id,
            side=position.side,
            opened_at=position.opened_at,
            entry_price=position.entry_price,
            sl=position.sl,
            tp=position.tp,
            revision=0,
            levels_effective_at=(
                position.opened_at
                if position.sl is not None or position.tp is not None
                else None
            ),
        )
        self._positions[position.position_id] = record
        return self._snapshot(record)

    def position(self, position_id: str) -> PositionState:
        return self._snapshot(self._get_position(position_id))

    def confirm_observed_exit(
        self, position_id: str, *, at: EventTime, price: float, reason: str,
    ) -> PositionExit:
        """Apply an independently observed deal, never a predicted quote fill."""
        if self.passive_exit_mode != "observed_fill":
            raise ValueError("observed exit requires observed_fill mode")
        if not isinstance(at, EventTime) or self._clock_ns is None or at.epoch_ns <= self._clock_ns:
            raise ValueError("observed fill must be after model clock")
        _validate_price("observed fill price", price)
        self._price_to_ticks(price, "observed fill price")
        record = self._get_position(position_id)
        if record.status != "open":
            raise ValueError("observed fill position is not open")
        if reason not in {"take_profit", "stop_loss"}:
            raise ValueError("unsupported observed passive exit reason")
        level = record.tp if reason == "take_profit" else record.sl
        if level is None:
            raise ValueError("observed passive exit has no installed level")
        touch = record.observed_touches.get((reason, level, record.revision))
        record.status = "closed"
        record.closed_at = at
        record.close_price = price
        record.close_reason = reason
        self._clock_ns = at.epoch_ns
        return PositionExit(record.position_id, record.side, reason, level, price,
                            at, first_terminal_touch_at=touch)

    def confirm_observed_modify(
        self, request_id: str, *, at: EventTime, status: str,
        reason: str | None = None,
    ) -> ModifyResult:
        """Apply a client-visible modification response without predicting it."""
        if self.passive_exit_mode != "observed_fill":
            raise ValueError("observed modify requires observed_fill mode")
        if not isinstance(at, EventTime) or self._clock_ns is None or at.epoch_ns <= self._clock_ns:
            raise ValueError("observed modify must be after model clock")
        if status not in {"accepted", "rejected"}:
            raise ValueError("unsupported observed modify status")
        if (status == "accepted" and reason is not None
                or status == "rejected" and not reason):
            raise ValueError("observed modify reason differs from status")
        pending = next((row for row in self._pending.values()
                        if row.request_id == request_id), None)
        if pending is None:
            raise ValueError("observed modify has no pending request")
        record = self._positions[pending.position_id]
        if status == "accepted":
            if record.status != "open" or record.revision != pending.expected_revision:
                raise ValueError("observed accepted modify contradicts position state")
            record.sl = pending.sl
            record.tp = pending.tp
            record.revision += 1
            record.levels_effective_at = at
            result = self._modify_result(
                pending, processed_at=at, effective_at=at,
                effective_revision=record.revision, status="accepted", reason=None)
        else:
            result = self._modify_result(
                pending, processed_at=at, status="rejected", reason=reason)
        del self._pending[pending.position_id]
        self._clock_ns = at.epoch_ns
        return result

    def request_modify(
        self,
        position_id: str,
        *,
        at: EventTime,
        sl: float | None,
        tp: float | None,
    ) -> ModifySubmission:
        if not isinstance(at, EventTime):
            raise ValueError("modify time must be EventTime")
        self._ensure_not_before_clock(at, "modify request")
        _validate_optional_price("sl", sl)
        _validate_optional_price("tp", tp)
        if sl is not None:
            self._price_to_ticks(sl, "sl")
        if tp is not None:
            self._price_to_ticks(tp, "tp")

        record = self._get_position(position_id)
        sequence = self._next_sequence
        self._next_sequence += 1
        request_id = f"modify-{sequence}"
        self._clock_ns = at.epoch_ns

        if record.status != "open":
            return ModifySubmission(
                request_id,
                position_id,
                at,
                None,
                record.revision,
                "rejected",
                "position_closed",
            )
        if record.pending_passive is not None:
            return ModifySubmission(
                request_id, position_id, at, None, record.revision,
                "rejected", "passive_fill_pending",
            )
        if position_id in self._pending:
            return ModifySubmission(
                request_id,
                position_id,
                at,
                None,
                record.revision,
                "rejected",
                "modify_in_flight",
            )

        processing_not_before_ns = at.epoch_ns + self.modify_processing_delay_ns
        pending = _PendingModify(
            sequence=sequence,
            request_id=request_id,
            position_id=position_id,
            requested_at=at,
            processing_not_before_ns=processing_not_before_ns,
            expected_revision=record.revision,
            sl=sl,
            tp=tp,
        )
        self._pending[position_id] = pending
        return ModifySubmission(
            request_id,
            position_id,
            at,
            processing_not_before_ns,
            record.revision,
            "queued",
            None,
        )

    def on_quote(self, quote: Quote) -> QuoteUpdate:
        if quote is None:
            raise ValueError("quote is required")
        if not isinstance(quote, Quote):
            raise ValueError("quote must be Quote")
        quote_ns = quote.at.epoch_ns
        if self._last_quote_ns is not None and quote_ns <= self._last_quote_ns:
            raise ValueError("quote times must be strictly increasing")
        if (self.passive_exit_mode == "observed_fill" and self._clock_ns is not None
                and quote_ns <= self._clock_ns):
            raise ValueError("observed-fill quote must be after model clock")
        self._ensure_not_before_clock(quote.at, "quote")
        bid_ticks = self._price_to_ticks(quote.bid, "bid")
        ask_ticks = self._price_to_ticks(quote.ask, "ask")

        self._last_quote_ns = quote_ns
        self._clock_ns = quote_ns
        exits: list[PositionExit] = []
        touches: list[PassiveTouch] = []
        modification_rows: list[tuple[int, ModifyResult]] = []

        # Levels installed before this quote get first priority. A modification
        # accepted on this quote can therefore only trigger on a later quote.
        for record in self._positions.values():
            pending_passive = record.pending_passive
            if pending_passive is not None:
                if quote_ns < pending_passive.due_ns:
                    continue
                if record.revision != pending_passive.revision:
                    raise ValueError("pending passive fill level revision changed")
                assert self.passive_fill_scenario is not None
                price = (pending_passive.installed_level
                         if self.passive_fill_scenario.price_mode == "installed_level"
                         else quote.bid if record.side == "BUY" else quote.ask)
                exits.append(self._settle_passive(
                    record, pending_passive.reason, pending_passive.installed_level,
                    price, quote.at, first_touch=pending_passive.touched_at))
                continue
            passive_exit = self._passive_exit(record, quote, bid_ticks, ask_ticks)
            if passive_exit is None:
                continue
            reason, installed_level, price = passive_exit
            if self.passive_exit_mode == "observed_fill":
                key = (reason, installed_level, record.revision)
                if key not in record.observed_touches:
                    record.observed_touches[key] = quote.at
                    touches.append(PassiveTouch(record.position_id, record.side,
                                                reason, installed_level, price,
                                                quote.at))
                continue
            if self.passive_exit_mode == "quote_delay_scenario":
                assert self.passive_fill_scenario is not None
                touches.append(PassiveTouch(record.position_id, record.side,
                                            reason, installed_level, price,
                                            quote.at))
                if self.passive_fill_scenario.delay_ns:
                    record.pending_passive = _PendingPassive(
                        reason, installed_level, quote.at,
                        quote_ns + self.passive_fill_scenario.delay_ns,
                        record.revision)
                    continue
                if self.passive_fill_scenario.price_mode == "installed_level":
                    price = installed_level
                exits.append(self._settle_passive(
                    record, reason, installed_level, price, quote.at,
                    first_touch=quote.at))
                continue
            exits.append(self._settle_passive(
                record, reason, installed_level, price, quote.at))
            # Closure does not deliver an in-flight request to the server early.
            # Its rejection is recorded when its processing deadline is reached.

        for pending in sorted(self._pending.values(), key=lambda row: row.sequence):
            if self.passive_exit_mode == "observed_fill":
                continue
            if pending.processing_not_before_ns > quote_ns:
                continue
            record = self._positions[pending.position_id]
            if record.status != "open":
                result = self._modify_result(
                    pending,
                    processed_at=quote.at,
                    status="rejected",
                    reason="position_closed",
                )
            elif record.pending_passive is not None:
                result = self._modify_result(
                    pending, processed_at=quote.at, status="rejected",
                    reason="passive_fill_pending",
                )
            elif record.revision != pending.expected_revision:
                result = self._modify_result(
                    pending,
                    processed_at=quote.at,
                    status="rejected",
                    reason="stale_position_revision",
                )
            else:
                reason = self._modify_rejection_reason(
                    record,
                    pending,
                    bid_ticks=bid_ticks,
                    ask_ticks=ask_ticks,
                )
                if reason is None:
                    record.sl = pending.sl
                    record.tp = pending.tp
                    record.revision += 1
                    record.levels_effective_at = quote.at
                    result = self._modify_result(
                        pending,
                        processed_at=quote.at,
                        effective_at=quote.at,
                        effective_revision=record.revision,
                        status="accepted",
                        reason=None,
                    )
                else:
                    result = self._modify_result(
                        pending,
                        processed_at=quote.at,
                        status="rejected",
                        reason=reason,
                    )
            modification_rows.append((pending.sequence, result))
            del self._pending[pending.position_id]

        modification_rows.sort(key=lambda row: row[0])
        return QuoteUpdate(
            quote=quote,
            exits=tuple(exits),
            modifications=tuple(row for _, row in modification_rows),
            touches=tuple(touches),
        )

    def _settle_passive(
        self, record: _PositionRecord, reason: str, installed_level: float,
        price: float, at: EventTime, *, first_touch: EventTime | None = None,
    ) -> PositionExit:
        record.status = "closed"
        record.closed_at = at
        record.close_price = price
        record.close_reason = reason
        record.pending_passive = None
        return PositionExit(record.position_id, record.side, reason,
                            installed_level, price, at,
                            first_terminal_touch_at=first_touch)

    def _passive_exit(
        self,
        record: _PositionRecord,
        quote: Quote,
        bid_ticks: int,
        ask_ticks: int,
    ) -> tuple[str, float, float] | None:
        if record.status != "open":
            return None
        market_ticks = bid_ticks if record.side == "BUY" else ask_ticks
        market_price = quote.bid if record.side == "BUY" else quote.ask
        sl_ticks = (
            self._price_to_ticks(record.sl, "installed sl")
            if record.sl is not None
            else None
        )
        tp_ticks = (
            self._price_to_ticks(record.tp, "installed tp")
            if record.tp is not None
            else None
        )
        sl_hit = (
            sl_ticks is not None
            and (
                market_ticks <= sl_ticks
                if record.side == "BUY"
                else market_ticks >= sl_ticks
            )
        )
        if sl_hit:
            return "stop_loss", record.sl, market_price
        tp_hit = (
            tp_ticks is not None
            and (
                market_ticks >= tp_ticks
                if record.side == "BUY"
                else market_ticks <= tp_ticks
            )
        )
        if tp_hit:
            return "take_profit", record.tp, market_price
        return None

    def _modify_rejection_reason(
        self,
        record: _PositionRecord,
        pending: _PendingModify,
        *,
        bid_ticks: int,
        ask_ticks: int,
    ) -> str | None:
        if not self.constraints.restrictions_known:
            return "symbol_restrictions_unknown"
        min_points = self.constraints.min_stop_distance_points
        freeze_points = self.constraints.freeze_distance_points
        assert min_points is not None and freeze_points is not None
        market_ticks = bid_ticks if record.side == "BUY" else ask_ticks

        for level_name, level in (("sl", pending.sl), ("tp", pending.tp)):
            if level is None:
                continue
            level_ticks = self._price_to_ticks(level, level_name)
            if record.side == "BUY":
                distance = (
                    market_ticks - level_ticks
                    if level_name == "sl"
                    else level_ticks - market_ticks
                )
            else:
                distance = (
                    level_ticks - market_ticks
                    if level_name == "sl"
                    else market_ticks - level_ticks
                )
            if distance <= 0:
                return f"{level_name}_wrong_side"
            if distance < min_points:
                return f"{level_name}_within_min_stop_distance"
            if distance < freeze_points:
                return f"{level_name}_within_freeze_distance"
        return None

    def _modify_result(
        self,
        pending: _PendingModify,
        *,
        processed_at: EventTime,
        status: str,
        reason: str | None,
        effective_at: EventTime | None = None,
        effective_revision: int | None = None,
    ) -> ModifyResult:
        return ModifyResult(
            request_id=pending.request_id,
            position_id=pending.position_id,
            requested_at=pending.requested_at,
            processing_not_before_ns=pending.processing_not_before_ns,
            processed_at=processed_at,
            effective_at=effective_at,
            requested_revision=pending.expected_revision,
            effective_revision=effective_revision,
            status=status,
            reason=reason,
        )

    def _get_position(self, position_id: str) -> _PositionRecord:
        try:
            return self._positions[position_id]
        except KeyError as exc:
            raise KeyError(f"unknown position_id: {position_id}") from exc

    def _snapshot(self, record: _PositionRecord) -> PositionState:
        return PositionState(
            position_id=record.position_id,
            side=record.side,
            opened_at=record.opened_at,
            entry_price=record.entry_price,
            status=record.status,
            sl=record.sl,
            tp=record.tp,
            revision=record.revision,
            levels_effective_at=record.levels_effective_at,
            closed_at=record.closed_at,
            close_price=record.close_price,
            close_reason=record.close_reason,
        )

    def _ensure_not_before_clock(self, at: EventTime, event_name: str) -> None:
        if self._clock_ns is not None and at.epoch_ns < self._clock_ns:
            raise ValueError(f"{event_name} is before model clock")

    def _price_to_ticks(self, price: float, name: str) -> int:
        value = Decimal(str(price))
        point = Decimal(str(self.constraints.point))
        ticks = value / point
        integral = ticks.to_integral_value()
        if ticks != integral:
            raise ValueError(f"{name} must be point-aligned")
        return int(integral)


def _validate_optional_price(name: str, value: float | None) -> None:
    if value is not None:
        _validate_price(name, value)


def _validate_price(name: str, value: float) -> None:
    if not _positive_finite(value):
        raise ValueError(f"{name} must be positive and finite")


def _positive_finite(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and float(value) > 0.0
    )
