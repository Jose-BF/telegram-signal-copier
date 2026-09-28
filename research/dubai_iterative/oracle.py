"""Independent scalar replay oracle for Dubai strategy finalists.

This module deliberately does not import the fast engine. It repeats entry,
state-transition and money decisions from immutable paths so shared mistakes
cannot certify themselves.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import math
import re
from typing import Iterable, Sequence

from provider_action_semantics import is_strategy_close_action

from .contracts import StrategyGenome
from .dataset import DubaiPath, LevelEvent, ProviderEvent
from .market_contract import MarketEvent, MarketProfile
from .protection_contract import ProtectionEvent, ProtectionProfile
from .client_contract import ClientEvent, ClientProfile


@dataclass(frozen=True)
class ExecutionScenario:
    name: str = "base"
    entry_slippage: float = 0.0
    exit_slippage: float = 0.0
    spread_addition: float = 0.0
    latency_ms: int = 0
    entry_fill_latency_ms: int = 0
    protection: ProtectionProfile | None = None
    market: MarketProfile | None = None
    client: ClientProfile | None = None

    def __post_init__(self) -> None:
        if self.client is not None and not isinstance(self.client, ClientProfile):
            raise ValueError("client must be a ClientProfile")
        if not self.name:
            raise ValueError("scenario name cannot be empty")
        if self.protection is not None and not isinstance(self.protection, ProtectionProfile):
            raise ValueError("protection must be a ProtectionProfile")
        if self.market is not None and not isinstance(self.market, MarketProfile):
            raise ValueError("market must be a MarketProfile")
        for field_name in ("entry_slippage", "exit_slippage", "spread_addition"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not math.isfinite(float(value)) or value < 0:
                raise ValueError(f"{field_name} must be finite and non-negative")
        for field_name in ("latency_ms", "entry_fill_latency_ms"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")


@dataclass(frozen=True)
class OracleEntry:
    ticket: str
    tick_index: int
    opened_at: datetime
    entry_price: float
    volume: float
    source: str
    requested_ns: int | None = None
    acknowledged_ns: int | None = None
    price_tick_index: int | None = None


@dataclass(frozen=True)
class OracleExit:
    ticket: str
    tick_index: int
    closed_at: datetime
    entry_price: float
    exit_price: float
    volume: float
    pnl_eur: Decimal | None
    reason: str


@dataclass(frozen=True)
class OracleResult:
    signal_id: str
    strategy_fingerprint: str
    confidence_layer: str
    entries: tuple[OracleEntry, ...]
    exits: tuple[OracleExit, ...]
    pnl_eur: Decimal | None
    exit_reason: str
    max_favourable_eur: Decimal | None
    max_adverse_eur: Decimal | None
    max_floating_drawdown_eur: Decimal | None
    max_favourable_move: float
    max_adverse_move: float
    blockers: tuple[str, ...]
    last_tick_index: int
    unfilled: bool
    filled_volume: float
    protection_events: tuple[ProtectionEvent, ...] = ()
    market_events: tuple[MarketEvent, ...] = ()
    client_events: tuple[ClientEvent, ...] = ()


@dataclass(frozen=True)
class OracleMismatch:
    signal_id: str
    field: str
    fast_value: object
    oracle_value: object


@dataclass(frozen=True)
class OracleCertificate:
    status: str
    mismatches: tuple[OracleMismatch, ...]
    oracle_results: tuple[OracleResult, ...]
    promotion_eligible: bool


@dataclass(frozen=True)
class StressScenarioResult:
    scenario: ExecutionScenario
    net_eur: Decimal | None
    blockers: tuple[str, ...]
    results: tuple[OracleResult, ...]


@dataclass(frozen=True)
class StressReport:
    base_net_eur: Decimal | None
    base_blockers: tuple[str, ...]
    scenarios: tuple[StressScenarioResult, ...]
    promotion_eligible: bool


@dataclass
class _Position:
    ticket: str
    role: str
    leg_index: int
    volume: float
    entry_price: float
    opened_ns: int
    tp_events: tuple[LevelEvent, ...]
    sl_events: tuple[LevelEvent, ...]
    be_stop: float | None = None
    be_reason: str = "break_even"
    trailing_stop: float | None = None
    accrued_swap_minor: int = 0


@dataclass(frozen=True)
class _Scheduled:
    index: int
    source: str
    position: _Position
    requested_ns: int | None = None


class _OracleProtectionBlocked(ValueError):
    pass


@dataclass
class _PendingProtection:
    request_id: int
    sent_index: int
    sent_ns: int
    sl: float | None
    tp: float | None
    sl_reason: str
    tp_reason: str


@dataclass
class _ProtectionState:
    sl: float | None
    tp: float | None
    sl_reason: str = "initial_sl"
    tp_reason: str = "initial_tp"
    pending: _PendingProtection | None = None
    acknowledgement_ns: int | None = None
    retry_ns: int = 0
    closed: bool = False
    pending_passive: tuple[int, float, str] | None = None


class _OracleProtectionLifecycle:
    """Independent quote-clock reconstruction of installed broker protection."""

    def __init__(self, profile: ProtectionProfile, direction: str) -> None:
        self.profile = profile
        self.direction_sign = 1 if direction == "BUY" else -1
        self.states: dict[str, _ProtectionState] = {}
        self.events: list[ProtectionEvent] = []
        self.request_sequence = 0

    def _emit(
        self,
        ticket: str,
        tick_index: int,
        timestamp_ns: int,
        request_id: int,
        kind: str,
        sl: float | None,
        tp: float | None,
        reason: str,
    ) -> None:
        if len(self.events) >= self.profile.max_events:
            raise _OracleProtectionBlocked("protection_event_budget_exhausted")
        self.events.append(ProtectionEvent(
            ticket, tick_index, timestamp_ns, request_id, kind, sl, tp, reason,
        ))

    def rounded(self, value: float | None) -> float | None:
        if value is None:
            return None
        quantum = Decimal(str(self.profile.point))
        return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))

    def pair_is_valid(
        self,
        exit_quote: float,
        sl: float | None,
        tp: float | None,
    ) -> bool:
        quote = Decimal(str(exit_quote))
        minimum = Decimal(str(self.profile.point)) * self.profile.stops_level_points
        for level, side in ((sl, self.direction_sign), (tp, -self.direction_sign)):
            if level is None:
                continue
            distance = side * (quote - Decimal(str(level)))
            if level <= 0 or distance <= 0 or distance < minimum:
                return False
        return True

    def open(
        self,
        ticket: str,
        tick_index: int,
        timestamp_ns: int,
        sl: float | None,
        tp: float | None,
        source: str,
    ) -> None:
        self._emit(ticket, tick_index, timestamp_ns, 0, "open", sl, tp, source)
        self.states[ticket] = _ProtectionState(sl=sl, tp=tp)

    def passive_hit(self, ticket: str, exit_quote: float, tick_index: int,
                    timestamp_ns: int) -> tuple[str, float | None] | None:
        state = self.states[ticket]
        scenario = self.profile.passive_fill_scenario
        if state.pending_passive is not None:
            due_ns, level, reason = state.pending_passive
            return (reason, level) if timestamp_ns >= due_ns else None
        if state.sl is not None and self.direction_sign * (exit_quote - state.sl) <= 0:
            return state.sl_reason, None
        if state.tp is not None and self.direction_sign * (exit_quote - state.tp) >= 0:
            if scenario is not None:
                self._emit(ticket, tick_index, timestamp_ns, 0, "touched",
                           state.sl, state.tp, state.tp_reason)
                if scenario.delay_ns:
                    state.pending_passive = (timestamp_ns + scenario.delay_ns,
                                             state.tp, state.tp_reason)
                    return None
            return state.tp_reason, state.tp
        return None

    def close(self, ticket: str, tick_index: int, timestamp_ns: int, reason: str) -> None:
        state = self.states[ticket]
        self._emit(ticket, tick_index, timestamp_ns, 0, "closed", state.sl, state.tp, reason)
        state.closed = True
        state.pending_passive = None

    def process(self, tick_index: int, timestamp_ns: int, exit_quote: float) -> None:
        processing_ns = self.profile.processing_delay_ms * 1_000_000
        acknowledgement_ns = self.profile.acknowledgement_delay_ms * 1_000_000
        retry_ns = self.profile.retry_delay_ms * 1_000_000
        for ticket, state in self.states.items():
            pending = state.pending
            if pending is None or state.closed:
                continue
            if (
                state.acknowledgement_ns is None
                and tick_index > pending.sent_index
                and timestamp_ns >= pending.sent_ns + processing_ns
            ):
                accepted = (state.pending_passive is None
                            and self.pair_is_valid(exit_quote, pending.sl, pending.tp))
                self._emit(
                    ticket,
                    tick_index,
                    timestamp_ns,
                    pending.request_id,
                    "installed" if accepted else "rejected",
                    pending.sl,
                    pending.tp,
                    "accepted" if accepted else "passive_fill_pending"
                    if state.pending_passive is not None else "invalid_stops",
                )
                if accepted:
                    state.sl = pending.sl
                    state.tp = pending.tp
                    state.sl_reason = pending.sl_reason
                    state.tp_reason = pending.tp_reason
                state.acknowledgement_ns = timestamp_ns + acknowledgement_ns
            if state.acknowledgement_ns is not None and timestamp_ns >= state.acknowledgement_ns:
                self._emit(
                    ticket,
                    tick_index,
                    timestamp_ns,
                    pending.request_id,
                    "acknowledged",
                    state.sl,
                    state.tp,
                    "response_available",
                )
                state.pending = None
                state.acknowledgement_ns = None
                state.retry_ns = timestamp_ns + retry_ns

    def request(
        self,
        ticket: str,
        tick_index: int,
        timestamp_ns: int,
        sl: float | None,
        tp: float | None,
        sl_reason: str,
        tp_reason: str,
    ) -> None:
        state = self.states[ticket]
        sl = self.rounded(sl)
        tp = self.rounded(tp)
        if (
            state.pending is not None
            or timestamp_ns < state.retry_ns
            or (state.sl == sl and state.tp == tp)
        ):
            return
        request_id = self.request_sequence + 1
        self._emit(
            ticket, tick_index, timestamp_ns, request_id,
            "requested", sl, tp, "policy_intent",
        )
        self.request_sequence = request_id
        state.pending = _PendingProtection(
            request_id, tick_index, timestamp_ns, sl, tp, sl_reason, tp_reason,
        )


@dataclass
class _MarketOpening:
    ticket: str
    leg_index: int
    request_id: int
    request_index: int
    requested_ns: int
    volume: float
    source: str
    initial_sl: float | None
    initial_tp: float | None = None
    outcome: str | None = None
    price: float | None = None
    acknowledgement_ns: int | None = None
    acknowledged: bool = False
    acknowledged_index: int | None = None


@dataclass
class _MarketClosing:
    ticket: str
    request_id: int
    request_index: int
    requested_ns: int
    volume: float
    reason: str
    outcome: str | None = None
    price: float | None = None
    acknowledgement_ns: int | None = None
    acknowledged: bool = False


class _OracleMarketBlocked(ValueError):
    pass


class _OracleMarketReplay:
    """Independent serial-client replay; requests never stand in for fills."""

    def __init__(self, path, genome, execution):
        self.path, self.genome, self.execution = path, genome, execution
        self.profile = execution.market
        self.protection = _OracleProtectionLifecycle(execution.protection, path.direction)
        self.active: list[_Position] = []
        self.entries: list[OracleEntry] = []
        self.exits: list[OracleExit] = []
        self.events: list[MarketEvent] = []
        self.openings: list[_MarketOpening] = []
        self.closings: dict[str, _MarketClosing] = {}
        self.sequence = 0
        self.requested_legs = 0
        self.plan_cancelled = False
        self.basket_close_reason: str | None = None
        self.reference_price: float | None = None
        self.first_open_ns: int | None = None
        self.provider_close_ns = _entry_cancel_time(path, genome, execution)
        self.direct_be_ns = None
        if genome.stop_mode == "fixed_level" and genome.be_mode == "provider":
            for event in path.provider_events:
                if event.action != "MOVE_SL_TO_BE" or event.payload.get("modality") != "direct" or event.observed_at < path.signal_observed_at:
                    continue
                available = _to_ns(event.observed_at) + execution.latency_ms * 1_000_000
                if self.direct_be_ns is None or available < self.direct_be_ns:
                    self.direct_be_ns = available
        self.absolute_cancel_index = _absolute_cancel_index(path, genome) if genome.stop_mode == "fixed_level" else len(path.times_ns)
        self.expiry_ns = _entry_expiry_anchor_ns(path) + genome.entry_expiry_min * 60_000_000_000
        self.realized_minor = 0
        self.unknown_money = False
        self.equity_incomplete = False
        self.high_minor: int | None = None
        self.known_high_minor: int | None = None
        self.low_minor: int | None = None
        self.drawdown_minor = 0
        self.favourable_move = 0.0
        self.adverse_move = 0.0
        self.lock_armed = False
        self.partial_requested = False
        self.reason = "not_closed"
        self.blockers: list[str] = []
        self.last_index = -1
        self.rollovers = sorted(path.rollover_events, key=lambda row: _to_ns(row.observed_at))
        self.rollover_cursor = 0

    def _reserve_market_event(self):
        if len(self.events) >= self.profile.max_events:
            raise _OracleMarketBlocked("market_event_budget_exhausted")

    def _reserve_protection_event(self):
        if len(self.protection.events) >= self.protection.profile.max_events:
            raise _OracleProtectionBlocked("protection_event_budget_exhausted")

    def _emit(self, request, index, kind, price, reason):
        self._reserve_market_event()
        self.last_index = index
        self.events.append(MarketEvent(
            request.ticket, index, int(self.path.times_ns[index]), request.request_id,
            kind, price, request.volume, reason,
        ))

    def _client_waiting(self):
        return any(not request.acknowledged for request in self.openings)

    def _market_pending(self):
        return self._client_waiting() or any(not row.acknowledged for row in self.closings.values())

    def _requested_stop(self, index):
        if self.genome.stop_mode == "fixed_level":
            return self.protection.rounded(float(self.genome.stop_value))
        sign = _sign(self.path.direction)
        price = _entry_quote(self.path, index) + sign * self.execution.spread_addition / 2
        candidates = []
        if self.genome.trailing_distance is not None:
            candidates.append(price - sign * float(self.genome.trailing_distance))
        if self.genome.stop_mode == "fixed_move":
            candidates.append(price - sign * float(self.genome.stop_value))
        level = (max(candidates) if sign > 0 else min(candidates)) if candidates else None
        return self.protection.rounded(level)

    def _request_entry(self, index):
        leg_index = self.requested_legs
        simultaneous = self.genome.entry_ladder_mode == "simultaneous"
        source = (
            f"causal_{self.genome.entry_mode}" if simultaneous or leg_index == 0
            else f"counterfactual_{self.genome.entry_ladder_mode}_ladder"
        )
        ticket = f"sim_{leg_index + 1}" if simultaneous or leg_index == 0 else f"sim_ladder_{leg_index + 1}"
        request = _MarketOpening(
            ticket, leg_index, self.sequence + 1, index, int(self.path.times_ns[index]),
            float(self.genome.volume_weights[leg_index]), source, self._requested_stop(index),
            self.protection.rounded(float(self.genome.target_steps[leg_index])) if self.genome.target_mode == "per_leg_levels" else None,
        )
        self._emit(request, index, "entry_requested", _entry_quote(self.path, index), source)
        self.sequence = request.request_id
        self.requested_legs += 1
        self.openings.append(request)

    def _volume_valid(self, volume):
        value = Decimal(str(volume))
        return (
            Decimal(str(self.profile.volume_min)) <= value <= Decimal(str(self.profile.volume_max))
            and value % Decimal(str(self.profile.volume_step)) == 0
        )

    def _process_openings(self, index):
        now = int(self.path.times_ns[index])
        for request in self.openings:
            if request.outcome is not None:
                continue
            if now < request.requested_ns + self.execution.entry_fill_latency_ms * 1_000_000:
                continue
            self._reserve_market_event()
            rejection = None
            if not self._volume_valid(request.volume):
                rejection = "invalid_volume"
            elif (self.protection.profile.request_quote_binding == "timestamp_only"
                  and bisect_right(self.path.times_ns, request.requested_ns)
                  - bisect_left(self.path.times_ns, request.requested_ns) != 1):
                raise _OracleProtectionBlocked(f"protection_request_quote_ambiguous:{request.ticket}")
            elif (self.protection.profile.request_quote_binding == "timestamp_and_ordinal"
                  and (not 0 <= request.request_index < len(self.path.times_ns)
                       or int(self.path.times_ns[request.request_index]) != request.requested_ns)):
                raise _OracleProtectionBlocked(f"protection_request_quote_ambiguous:{request.ticket}")
            elif not self.protection.pair_is_valid(
                float(self.path.exit_quotes[index]), request.initial_sl, request.initial_tp,
            ):
                rejection = "invalid_initial_protection"
            if rejection is not None:
                self._emit(request, index, "entry_rejected", None, rejection)
                request.outcome = rejection
                self.plan_cancelled = True
                if not self.active:
                    self.reason = "entry_rejected"
            else:
                self._reserve_market_event()
                self._reserve_protection_event()
                price = _entry_with_cost(self.path.direction, _entry_quote(self.path, index), self.execution)
                self._emit(request, index, "entry_filled", price, request.source)
                self.protection.open(
                    request.ticket, index, now, request.initial_sl, request.initial_tp, "hypothetical_open_request",
                )
                template = self.path.legs[min(request.leg_index, len(self.path.legs) - 1)]
                self.active.append(_Position(
                    ticket=request.ticket, role=template.role, leg_index=request.leg_index,
                    volume=request.volume, entry_price=price, opened_ns=now,
                    tp_events=template.tp_events, sl_events=template.sl_events,
                    trailing_stop=_trailing_start(self.path.direction, price, self.genome.trailing_distance),
                ))
                self.entries.append(OracleEntry(
                    request.ticket, index, _from_ns(now), price, request.volume,
                    request.source, request.requested_ns,
                ))
                if self.first_open_ns is None:
                    self.first_open_ns = now
                    self.reference_price = price
                request.outcome = "accepted"
                request.price = price
            request.acknowledgement_ns = now + self.profile.entry_acknowledgement_delay_ms * 1_000_000

    def _acknowledge_market(self, index):
        now = int(self.path.times_ns[index])
        requests = sorted((*self.openings, *self.closings.values()), key=lambda row: row.request_id)
        for request in requests:
            if request.acknowledged or request.acknowledgement_ns is None or now < request.acknowledgement_ns:
                continue
            opening = isinstance(request, _MarketOpening)
            kind = "entry_acknowledged" if opening else "close_acknowledged"
            self._emit(request, index, kind, request.price, request.outcome)
            request.acknowledged = True
            if opening:
                request.acknowledged_index = index
                for entry_index, entry in enumerate(self.entries):
                    if entry.ticket == request.ticket:
                        self.entries[entry_index] = replace(entry, acknowledged_ns=now)
                        break
            elif request.reason == "partial_target":
                del self.closings[request.ticket]

    def _ladder_due(self, index):
        if index >= self.absolute_cancel_index:
            self.plan_cancelled = True
            return False
        if self.plan_cancelled or self.requested_legs >= self.genome.leg_count or self._client_waiting():
            return False
        if not self.openings or self.openings[-1].acknowledged_index is None or index <= self.openings[-1].acknowledged_index:
            return False
        if int(self.path.times_ns[index]) >= self.expiry_ns:
            self.plan_cancelled = True
            return False
        if self.reference_price is None or self.genome.entry_ladder_mode == "simultaneous":
            return False
        if self.genome.entry_ladder_mode == "range_levels":
            low, high = float(self.genome.entry_value), float(self.genome.entry_confirmation_value)
            threshold = (low + high) / 2 if self.requested_legs == 1 else low if self.path.direction == "BUY" else high
            return _sign(self.path.direction) * (_entry_quote(self.path, index) - threshold) <= 0
        sign = _sign(self.path.direction) * (-1 if self.genome.entry_ladder_mode == "adverse" else 1)
        return sign * (_entry_quote(self.path, index) - self.reference_price) >= (
            float(self.genome.entry_ladder_step) * self.requested_legs
        )

    def _rollover(self, index):
        now = int(self.path.times_ns[index])
        while self.rollover_cursor < len(self.rollovers):
            event = self.rollovers[self.rollover_cursor]
            event_ns = _to_ns(event.observed_at)
            if event_ns > now:
                break
            self.rollover_cursor += 1
            eligible = [position for position in self.active if position.opened_ns < event_ns]
            if not eligible:
                continue
            if event.blocker:
                raise _OracleMarketBlocked(event.blocker)
            for position in eligible:
                units = _swap_volume_units(position.volume)
                if units is None or units >= len(event.minor_by_volume_unit):
                    raise _OracleMarketBlocked("swap_volume_unsupported")
                position.accrued_swap_minor += int(event.minor_by_volume_unit[units])

    def _update_stats(self, index):
        if not self.active:
            return
        raw_exit = float(self.path.exit_quotes[index])
        for position in self.active:
            move = _sign(self.path.direction) * (raw_exit - position.entry_price)
            self.favourable_move = max(self.favourable_move, move)
            self.adverse_move = min(self.adverse_move, move)
        floating, exact = _basket_money(self.path, self.active, index, self.execution)
        total = self.realized_minor + floating
        # Peaks observed while waiting belong to diagnostics, not policy knowledge.
        if not exact:
            if not self.equity_incomplete:
                self.blockers.append("incomplete_equity_conversion")
            self.equity_incomplete = True
        if exact and not self.unknown_money:
            self.high_minor = total if self.high_minor is None else max(self.high_minor, total)
            self.low_minor = total if self.low_minor is None else min(self.low_minor, total)
            self.drawdown_minor = max(self.drawdown_minor, self.high_minor - total)

    def _close_position(self, position, index, reason, *, price=None, volume=None):
        volume = position.volume if volume is None else volume
        if volume >= position.volume:
            self.protection.close(position.ticket, index, int(self.path.times_ns[index]), reason)
        self.realized_minor, self.unknown_money = _close_one(
            self.path, position, volume, self.active, self.exits, index,
            reason, self.execution, self.realized_minor, self.unknown_money,
            self.blockers, exit_price=price,
        )
        self.reason = reason

    def _passive_exits(self, index):
        for position in tuple(self.active):
            hit = self.protection.passive_hit(
                position.ticket, float(self.path.exit_quotes[index]), index,
                int(self.path.times_ns[index]))
            if hit is not None:
                reason, target = hit
                scenario = self.protection.profile.passive_fill_scenario
                price = (None if target is None or scenario is not None
                         and scenario.price_mode == "executable_quote" else
                         _target_exit_price(self.path.direction, target,
                                            self.execution))
                self._close_position(position, index, reason, price=price)

    def _request_close(self, position, index, reason, *, volume=None):
        if position.ticket in self.closings:
            return
        request = _MarketClosing(
            position.ticket, self.sequence + 1, index, int(self.path.times_ns[index]),
            position.volume if volume is None else volume, reason,
        )
        self._emit(request, index, "close_requested", float(self.path.exit_quotes[index]), reason)
        self.sequence = request.request_id
        self.closings[position.ticket] = request

    def _process_closes(self, index):
        now = int(self.path.times_ns[index])
        for request in self.closings.values():
            if request.outcome is None:
                if index <= request.request_index or now < (
                    request.requested_ns + self.profile.close_processing_delay_ms * 1_000_000
                ):
                    continue
                position = next((row for row in self.active if row.ticket == request.ticket), None)
                if position is None:
                    self._emit(request, index, "close_rejected", None, "position_already_closed")
                    request.outcome = "position_already_closed"
                else:
                    self._reserve_market_event()
                    if request.volume >= position.volume:
                        self._reserve_protection_event()
                    price = _exit_with_cost(self.path, index, self.execution)
                    self._emit(request, index, "close_filled", price, request.reason)
                    self._close_position(position, index, request.reason, price=price, volume=request.volume)
                    request.outcome = "accepted"
                    request.price = price
                request.acknowledgement_ns = now + self.profile.close_acknowledgement_delay_ms * 1_000_000

    def _basket_close(self, index, reason):
        if any(row.outcome is None for row in self.openings):
            raise _OracleMarketBlocked("market_close_with_entry_in_flight_unsupported")
        self.basket_close_reason = reason
        self.plan_cancelled = True
        for position in self.active:
            self._request_close(position, index, reason)

    def _decide(self, index):
        if self._client_waiting() or not self.active:
            return
        now = int(self.path.times_ns[index])
        if self.basket_close_reason is not None:
            return
        if any(r.reason == "partial_target" and not r.acknowledged for r in self.closings.values()):
            return
        if self.genome.stop_mode == "basket_money":
            floating, exact = _basket_money(self.path, self.active, index, self.execution)
            if not exact:
                self.blockers.append("stale_conversion_at_basket_stop")
                self.unknown_money = True
            elif self.realized_minor + floating <= -_amount_to_minor(float(self.genome.stop_value), self.path.currency_digits):
                self._basket_close(index, "basket_stop")
                return
        for position in self.active:
            if self.genome.be_mode == "price":
                move = _sign(self.path.direction) * (Decimal(str(float(self.path.exit_quotes[index]))) - Decimal(str(position.entry_price)))
                if move >= Decimal(str(self.genome.be_trigger)):
                    position.be_stop = position.entry_price
            elif self.direct_be_ns is not None and now >= self.direct_be_ns:
                position.be_stop = position.entry_price
        if self.genome.hard_stop_eur_per_leg is not None:
            threshold = _amount_to_minor(float(self.genome.hard_stop_eur_per_leg), self.path.currency_digits)
            for position in self.active:
                value, exact = _position_money(self.path, position, index, self.execution)
                if not exact:
                    self.blockers.append(f"stale_conversion_at_hard_stop:{position.ticket}")
                    self.unknown_money = True
                elif value <= -threshold:
                    self._request_close(position, index, "hard_stop_per_leg")
        if self.provider_close_ns is not None and self.provider_close_ns <= now:
            self._basket_close(index, "provider_close")
            return
        floating, exact = _basket_money(self.path, self.active, index, self.execution)
        total = self.realized_minor + floating
        if exact and not self.unknown_money:
            self.known_high_minor = total if self.known_high_minor is None else max(self.known_high_minor, total)
        if self.genome.target_mode == "partial_runner":
            if not exact:
                self.blockers.append("stale_conversion_at_basket_target")
                self.unknown_money = True
            elif not self.partial_requested and total >= _amount_to_minor(float(self.genome.target_value), self.path.currency_digits):
                for position in self.active:
                    volume = float(Decimal(str(position.volume)) * Decimal(str(self.genome.partial_fraction)))
                    self._request_close(position, index, "partial_target", volume=volume)
                self.partial_requested = True
                return
            elif self.partial_requested and total >= _amount_to_minor(float(self.genome.runner_target), self.path.currency_digits):
                self._basket_close(index, "runner_target")
                return
        if self.genome.profit_lock_arm is not None:
            arm = _amount_to_minor(float(self.genome.profit_lock_arm), self.path.currency_digits)
            giveback = _amount_to_minor(float(self.genome.profit_lock_giveback), self.path.currency_digits)
            if exact and self.known_high_minor is not None:
                self.lock_armed = self.lock_armed or self.known_high_minor >= arm
                if self.lock_armed and total <= self.known_high_minor - giveback:
                    self._basket_close(index, "profit_lock")
                    return
            elif not exact:
                self.blockers.append("stale_conversion_during_profit_lock")
                self.unknown_money = True
        time_due = (
            self.first_open_ns is not None
            and now - self.first_open_ns >= self.genome.time_exit_min * 60_000_000_000
        )
        if time_due and self.genome.schema_version != 1 and self.genome.time_exit_mode in {"loss_only", "profit_only", "non_negative"} and not exact:
            self.blockers.append("stale_conversion_at_time_exit")
            self.unknown_money = True
        if time_due and _time_rule_matches(self.genome, total, exact):
            self._basket_close(index, "time_exit")
            return
        if self.genome.trailing_distance is not None:
            _advance_trailing(
                self.active, self.path.direction, float(self.path.exit_quotes[index]),
                float(self.genome.trailing_distance),
            )
        for position in self.active:
            if position.ticket in self.closings:
                continue
            sl, reason = _stop_level(
                position, self.genome, now, self.path.direction, self.execution.latency_ms * 1_000_000,
            )
            target = (
                float(self.genome.target_steps[position.leg_index]) if self.genome.target_mode == "per_leg_levels" else
                position.entry_price + _sign(self.path.direction) * float(self.genome.target_steps[position.leg_index])
                if self.genome.target_mode == "per_leg_steps" else None
            )
            self.protection.request(position.ticket, index, now, sl, target, reason, "per_leg_target")

    def run(self):
        first_index = _causal_index(self.path, self.genome, self.execution)
        if first_index is None or not _context_allowed(self.path, self.genome, first_index, self.execution):
            return _empty(self.path, self.genome, (), confidence="counterfactual_entry", unfilled=True)
        if self.provider_close_ns is not None and self.provider_close_ns <= int(self.path.times_ns[first_index]):
            return _empty(self.path, self.genome, (), confidence="counterfactual_entry", unfilled=True)
        interrupted = False
        for index in range(first_index, len(self.path.times_ns)):
            now = int(self.path.times_ns[index])
            if self.active or self._market_pending():
                self.last_index = index
            if not _usable_tick(self.path, index):
                if self.active:
                    self.blockers.append(f"invalid_tick_at_index:{index}")
                    interrupted = True
                    break
                continue
            try:
                phase_error = None
                provider_due = self.provider_close_ns is not None and self.provider_close_ns <= now
                if provider_due:
                    self.plan_cancelled = True
                in_flight_conflict = provider_due and any(
                    row.outcome is None and row.requested_ns < self.provider_close_ns
                    and (self.provider_close_ns < now or now < row.requested_ns + self.execution.entry_fill_latency_ms * 1_000_000)
                    for row in self.openings
                )
                try:
                    self._acknowledge_market(index)
                    if in_flight_conflict:
                        raise _OracleMarketBlocked("market_close_with_entry_in_flight_unsupported")
                    if index == first_index and not self.plan_cancelled:
                        count = self.genome.leg_count if self.genome.entry_ladder_mode == "simultaneous" else 1
                        for _ in range(count):
                            self._request_entry(index)
                    if self._ladder_due(index):
                        self._request_entry(index)
                    self._process_openings(index)
                except (_OracleMarketBlocked, _OracleProtectionBlocked) as exc:
                    phase_error = exc
                self._rollover(index)
                self._update_stats(index)
                self._passive_exits(index)
                # Failed client bookkeeping cannot erase an installed stop's fill.
                if phase_error is not None:
                    raise phase_error
                self._process_closes(index)
                self._acknowledge_market(index)
                if self.active:
                    self.protection.process(index, now, float(self.path.exit_quotes[index]))
                self._decide(index)
                if not self.active and self.entries and self.genome.pending_entry_policy != "until_expiry":
                    self.plan_cancelled = True
                plan_done = self.plan_cancelled or self.requested_legs >= self.genome.leg_count or now >= self.expiry_ns
                if not self.active and not self._market_pending() and plan_done:
                    break
            except (_OracleMarketBlocked, _OracleProtectionBlocked) as exc:
                self.blockers.append(str(exc))
                interrupted = True
                break
        if not interrupted:
            if self.active:
                self.blockers.append("path_ended_before_strategy_exit")
            if self._market_pending():
                if any(request.outcome is None for request in self.openings):
                    self.blockers.append("entry_fill_quote_missing")
                self.blockers.append("market_lifecycle_incomplete_at_data_end")
        elif self.last_index >= 0:
            for request in self.openings:
                if request.outcome is not None:
                    continue
                fill_index = _delayed_execution_index(
                    self.path, request.request_index, self.execution.entry_fill_latency_ms,
                )
                if fill_index is None:
                    self.blockers.append("entry_fill_quote_missing")
                elif int(self.path.times_ns[self.last_index]) < int(self.path.times_ns[fill_index]):
                    self.blockers.append("entry_request_in_flight_at_strategy_exit")
        if self.active or self._market_pending() or interrupted:
            self.unknown_money = True
        if self.active:
            self.reason = "not_closed"
        elif not self.entries:
            self.reason = (
                "blocked" if self.blockers else
                "entry_rejected" if any(row.outcome is not None for row in self.openings) else "not_filled"
            )
        def money(value):
            return None if value is None else _minor_decimal(value, self.path.currency_digits)

        return OracleResult(
            signal_id=self.path.signal_id, strategy_fingerprint=self.genome.fingerprint,
            confidence_layer="counterfactual_entry", entries=tuple(self.entries), exits=tuple(self.exits),
            pnl_eur=None if self.unknown_money else money(self.realized_minor), exit_reason=self.reason,
            max_favourable_eur=None if self.equity_incomplete else money(self.high_minor),
            max_adverse_eur=None if self.equity_incomplete else money(self.low_minor),
            max_floating_drawdown_eur=None if self.equity_incomplete or self.high_minor is None else money(self.drawdown_minor),
            max_favourable_move=_clean_price(self.favourable_move),
            max_adverse_move=_clean_price(self.adverse_move), blockers=tuple(dict.fromkeys(self.blockers)),
            last_tick_index=self.last_index, unfilled=not self.entries and not self.blockers,
            filled_volume=_clean_volume(sum(row.volume for row in self.entries)),
            protection_events=tuple(self.protection.events), market_events=tuple(self.events),
        )


def oracle_simulate(
    path: DubaiPath,
    genome: StrategyGenome,
    *,
    execution: ExecutionScenario | None = None,
) -> OracleResult:
    execution = execution or ExecutionScenario()
    observation_latency_ns = execution.latency_ms * 1_000_000
    blockers = list(genome.validation_errors())
    absolute = genome.stop_mode == "fixed_level" or genome.target_mode == "per_leg_levels"
    if absolute and (execution.protection is None or execution.protection.policy_extension not in {"absolute_levels_v1", "absolute_levels_be_v1"}
            or execution.market is None or execution.client is not None):
        blockers.append("absolute_levels_require_explicit_market_profile")
    blockers.extend(_path_errors(path))
    blockers.extend(_oracle_protection_blockers(path, genome, execution.protection))
    if execution.client is not None:
        blockers.append("client_model_not_validated_in_oracle")
    extended = execution.protection is not None and execution.protection.policy_extension == "own_rule_be_partial_v1"
    ordinal = execution.protection is not None and execution.protection.request_quote_binding == "timestamp_and_ordinal"
    basket = execution.protection is not None and execution.protection.policy_extension == "basket_guard_v1"
    if basket and (execution.market is None or execution.client is not None
                   or genome.schema_version != 2 or genome.entry_mode == "actual_mt5"):
        blockers.append("basket_guard_requires_hypothetical_market")
    if (extended or ordinal and not basket) and (execution.market is None or execution.client is not None
                     or genome.schema_version != 2 or genome.entry_mode == "actual_mt5"
                     or genome.provider_management_mode != "ignore"):
        blockers.append("protection_extension_requires_own_rule_market")
    if execution.market is not None:
        if execution.protection is None:
            blockers.append("market_requires_protection_profile")
        if genome.schema_version != 2 or genome.entry_mode == "actual_mt5":
            blockers.append("market_requires_hypothetical_schema2_entries")
        if extended and genome.target_mode == "partial_runner" and not genome.validation_errors():
            if genome.entry_ladder_mode != "simultaneous":
                blockers.append("market_partial_ladder_unsupported")
            low, high, step = (Decimal(str(getattr(execution.market, "volume_" + key))) for key in ("min", "max", "step"))
            for amount in genome.volume_weights:
                original = Decimal(str(amount))
                portion = original * Decimal(str(genome.partial_fraction))
                if any(v < low or v > high or v % step for v in (portion, original - portion)):
                    blockers.append("market_partial_volume_unsupported")
                    break
    if blockers:
        return _empty(path, genome, blockers)
    if execution.market is not None:
        return _OracleMarketReplay(path, genome, execution).run()

    missing_fill_requests: list[int] = []
    scheduled, confidence, entry_errors = _schedule_entries(path, genome, execution, missing_fill_requests=missing_fill_requests)
    blockers.extend(entry_errors)
    if blockers:
        return _empty(path, genome, blockers, confidence=confidence)
    if not scheduled:
        return _empty(path, genome, (), confidence=confidence, unfilled=True)

    active: list[_Position] = []
    entries: list[OracleEntry] = []
    exits: list[OracleExit] = []
    schedule_cursor = 0
    provider_events = tuple(sorted(path.provider_events, key=lambda item: _to_ns(item.observed_at)))
    provider_cursor = 0
    rollover_events = tuple(
        sorted(path.rollover_events, key=lambda item: _to_ns(item.observed_at))
    )
    rollover_cursor = 0
    rollover_blocked = False
    realized_minor = 0
    unknown_money = False
    equity_incomplete = False
    partial_taken = False
    lock_armed = False
    high_minor: int | None = None
    low_minor: int | None = None
    drawdown_minor = 0
    favourable_move = 0.0
    adverse_move = 0.0
    last_index = -1
    reason = "not_closed"
    first_open_ns: int | None = None
    protection = (
        _OracleProtectionLifecycle(execution.protection, path.direction)
        if execution.protection is not None
        else None
    )
    protection_blocked = False

    for index in range(len(path.times_ns)):
        now_ns = int(path.times_ns[index])
        if any(requested <= now_ns for requested in missing_fill_requests):
            last_index = index
        while schedule_cursor < len(scheduled) and scheduled[schedule_cursor].index == index:
            item = scheduled[schedule_cursor]
            if protection is not None:
                try:
                    _open_oracle_protection(
                        protection, path, genome, execution, item, index, now_ns,
                    )
                except _OracleProtectionBlocked as exc:
                    blockers.append(str(exc))
                    protection_blocked = True
                    last_index = index
                    break
            active.append(item.position)
            first_open_ns = item.position.opened_ns if first_open_ns is None else min(first_open_ns, item.position.opened_ns)
            entries.append(OracleEntry(
                ticket=item.position.ticket,
                tick_index=index,
                opened_at=_from_ns(item.position.opened_ns),
                entry_price=item.position.entry_price,
                volume=item.position.volume,
                source=item.source,
                requested_ns=item.requested_ns,
            ))
            schedule_cursor += 1

        while (
            rollover_cursor < len(rollover_events)
            and _to_ns(rollover_events[rollover_cursor].observed_at) <= now_ns
        ):
            event = rollover_events[rollover_cursor]
            rollover_cursor += 1
            event_ns = _to_ns(event.observed_at)
            rollover_positions = [
                position
                for position in active
                if position.opened_ns < event_ns
            ]
            if not rollover_positions:
                continue
            if event.blocker:
                blockers.append(event.blocker)
                unknown_money = True
                rollover_blocked = True
                last_index = index
                break
            for position in rollover_positions:
                units = _swap_volume_units(position.volume)
                if units is None or units >= len(event.minor_by_volume_unit):
                    blockers.append("swap_volume_unsupported")
                    unknown_money = True
                    rollover_blocked = True
                    last_index = index
                    break
                position.accrued_swap_minor += int(
                    event.minor_by_volume_unit[units]
                )
            if rollover_blocked:
                break
        if rollover_blocked:
            break

        if not active:
            if protection_blocked:
                break
            if schedule_cursor >= len(scheduled) and entries and not missing_fill_requests:
                break
            continue
        if not _usable_tick(path, index):
            blockers.append(f"invalid_tick_at_index:{index}")
            last_index = index
            break
        last_index = index
        raw_exit = float(path.exit_quotes[index])

        for position in active:
            move = _sign(path.direction) * (raw_exit - position.entry_price)
            favourable_move = max(favourable_move, move)
            adverse_move = min(adverse_move, move)
            _custom_be(position, genome, move, now_ns)

        floating_minor, exact = _basket_money(path, active, index, execution)
        total_minor = realized_minor + floating_minor
        if not exact:
            if not equity_incomplete:
                blockers.append("incomplete_equity_conversion")
            equity_incomplete = True
        if exact and not unknown_money:
            high_minor = total_minor if high_minor is None else max(high_minor, total_minor)
            low_minor = total_minor if low_minor is None else min(low_minor, total_minor)
            drawdown_minor = max(drawdown_minor, high_minor - total_minor)

        if protection is not None:
            try:
                for position in tuple(active):
                    hit = protection.passive_hit(position.ticket, raw_exit,
                                                 index, now_ns)
                    if hit is None:
                        continue
                    close_reason, target_price = hit
                    protection.close(position.ticket, index, now_ns, close_reason)
                    realized_minor, unknown_money = _close_one(
                        path,
                        position,
                        position.volume,
                        active,
                        exits,
                        index,
                        close_reason,
                        execution,
                        realized_minor,
                        unknown_money,
                        blockers,
                        exit_price=(None if target_price is None or
                                    protection.profile.passive_fill_scenario is not None and
                                    protection.profile.passive_fill_scenario.price_mode == "executable_quote"
                                    else _target_exit_price(path.direction, target_price, execution)),
                    )
                    reason = close_reason
                if not active:
                    if protection_blocked:
                        break
                    if (
                        genome.pending_entry_policy == "until_expiry"
                        and (schedule_cursor < len(scheduled) or missing_fill_requests)
                    ):
                        continue
                    break
                if protection_blocked:
                    break
                protection.process(index, now_ns, raw_exit)
            except _OracleProtectionBlocked as exc:
                blockers.append(str(exc))
                protection_blocked = True
                break

        if genome.stop_mode == "basket_money":
            threshold = _amount_to_minor(float(genome.stop_value), path.currency_digits)
            if exact and total_minor <= -threshold:
                realized_minor, unknown_money = _close_all(
                    path, active, exits, index, "basket_stop", execution,
                    realized_minor, unknown_money, blockers,
                )
                reason = "basket_stop"
                break
            if not exact:
                blockers.append("stale_conversion_at_basket_stop")
                unknown_money = True

        if genome.hard_stop_eur_per_leg is not None:
            threshold = _amount_to_minor(
                float(genome.hard_stop_eur_per_leg),
                path.currency_digits,
            )
            for position in tuple(active):
                current_minor, current_exact = _position_money(
                    path,
                    position,
                    index,
                    execution,
                )
                if current_exact and current_minor <= -threshold:
                    if protection is not None:
                        blockers.append("protection_market_close_latency_unmodeled")
                        protection_blocked = True
                        break
                    realized_minor, unknown_money = _close_one(
                        path, position, position.volume, active, exits, index,
                        "hard_stop_per_leg", execution, realized_minor,
                        unknown_money, blockers,
                    )
                    reason = "hard_stop_per_leg"
                elif not current_exact:
                    blockers.append(
                        f"stale_conversion_at_hard_stop:{position.ticket}"
                    )
                    unknown_money = True
            if protection_blocked:
                break
            if not active:
                break

        due: list[ProviderEvent] = []
        while (
            provider_cursor < len(provider_events)
            and _to_ns(provider_events[provider_cursor].observed_at)
            + observation_latency_ns
            <= now_ns
        ):
            due.append(provider_events[provider_cursor])
            provider_cursor += 1
        if genome.be_mode == "provider":
            _provider_protection(active, due)
        if genome.provider_management_mode in {
            "exact",
            "close_only",
            "explicit_close_only",
        } and any(
            _provider_close(item.action, genome.provider_management_mode)
            for item in due
        ):
            if protection is not None:
                blockers.append("protection_market_close_latency_unmodeled")
                protection_blocked = True
                break
            realized_minor, unknown_money = _close_all(
                path, active, exits, index, "provider_close", execution,
                realized_minor, unknown_money, blockers,
            )
            reason = "provider_close"
            break

        for position in (() if protection is not None else tuple(active)):
            level, stop_reason = _stop_level(
                position,
                genome,
                now_ns,
                path.direction,
                observation_latency_ns,
            )
            if level is None or not _hit(path.direction, raw_exit, level, target=False):
                continue
            realized_minor, unknown_money = _close_one(
                path, position, position.volume, active, exits, index,
                stop_reason, execution, realized_minor, unknown_money, blockers,
            )
            reason = stop_reason
        if not active:
            break

        floating_minor, exact = _basket_money(path, active, index, execution)
        total_minor = realized_minor + floating_minor

        if genome.target_mode == "provider_per_leg":
            for position in tuple(active):
                target = _latest_level(
                    position.tp_events,
                    now_ns,
                    include_be=True,
                    observation_latency_ns=observation_latency_ns,
                )
                if target is None or not _hit(path.direction, raw_exit, target, target=True):
                    continue
                realized_minor, unknown_money = _close_one(
                    path, position, position.volume, active, exits, index,
                    "provider_tp", execution, realized_minor, unknown_money, blockers,
                    exit_price=_target_exit_price(
                        path.direction, target, execution
                    ),
                )
                reason = "provider_tp"
        elif genome.target_mode == "per_leg_steps" and protection is None:
            direction = _sign(path.direction)
            for position in tuple(active):
                target = position.entry_price + direction * float(
                    genome.target_steps[position.leg_index]
                )
                if not _hit(path.direction, raw_exit, target, target=True):
                    continue
                realized_minor, unknown_money = _close_one(
                    path, position, position.volume, active, exits, index,
                    "per_leg_target", execution, realized_minor,
                    unknown_money, blockers,
                    exit_price=_target_exit_price(
                        path.direction, target, execution
                    ),
                )
                reason = "per_leg_target"
        elif genome.target_mode == "provider_target_all":
            target = _provider_target(
                path,
                genome,
                now_ns,
                observation_latency_ns,
            )
            if target is not None and _hit(path.direction, raw_exit, target, target=True):
                realized_minor, unknown_money = _close_all(
                    path, active, exits, index, "provider_target_all", execution,
                    realized_minor, unknown_money, blockers,
                    exit_price=_target_exit_price(
                        path.direction, target, execution
                    ),
                )
                reason = "provider_target_all"
        elif genome.target_mode == "fixed_basket":
            target_minor = _amount_to_minor(float(genome.target_value), path.currency_digits)
            if exact and total_minor >= target_minor:
                realized_minor, unknown_money = _close_all(
                    path, active, exits, index, "basket_target", execution,
                    realized_minor, unknown_money, blockers,
                )
                reason = "basket_target"
            elif not exact:
                blockers.append("stale_conversion_at_basket_target")
                unknown_money = True
        elif genome.target_mode == "fixed_move":
            if _fixed_move_target_reached(
                path.direction,
                raw_exit,
                active,
                float(genome.target_value),
            ):
                realized_minor, unknown_money = _close_all(
                    path, active, exits, index, "fixed_move_target", execution,
                    realized_minor, unknown_money, blockers,
                )
                reason = "fixed_move_target"
        elif genome.target_mode == "partial_runner":
            if not exact:
                blockers.append("stale_conversion_at_basket_target")
                unknown_money = True
            first_minor = _amount_to_minor(float(genome.target_value), path.currency_digits)
            runner_minor = _amount_to_minor(float(genome.runner_target), path.currency_digits)
            if not partial_taken and exact and total_minor >= first_minor:
                for position in tuple(active):
                    amount = _clean_volume(position.volume * float(genome.partial_fraction))
                    if amount > 0:
                        realized_minor, unknown_money = _close_one(
                            path, position, amount, active, exits, index,
                            "partial_target", execution, realized_minor,
                            unknown_money, blockers,
                        )
                partial_taken = True
            if active:
                floating_minor, exact = _basket_money(path, active, index, execution)
                total_minor = realized_minor + floating_minor
                if partial_taken and exact and total_minor >= runner_minor:
                    realized_minor, unknown_money = _close_all(
                        path, active, exits, index, "runner_target", execution,
                        realized_minor, unknown_money, blockers,
                    )
                    reason = "runner_target"
        if not active:
            if (
                genome.pending_entry_policy == "until_expiry"
                and (schedule_cursor < len(scheduled) or missing_fill_requests)
            ):
                continue
            break

        if genome.profit_lock_arm is not None:
            arm = _amount_to_minor(float(genome.profit_lock_arm), path.currency_digits)
            giveback = _amount_to_minor(float(genome.profit_lock_giveback), path.currency_digits)
            if exact and high_minor is not None:
                lock_armed = lock_armed or high_minor >= arm
                if lock_armed and total_minor <= high_minor - giveback:
                    if protection is not None:
                        blockers.append("protection_market_close_latency_unmodeled")
                        protection_blocked = True
                        break
                    realized_minor, unknown_money = _close_all(
                        path, active, exits, index, "profit_lock", execution,
                        realized_minor, unknown_money, blockers,
                    )
                    reason = "profit_lock"
                    break
            elif not exact:
                blockers.append("stale_conversion_during_profit_lock")
                unknown_money = True

        time_due = (
            active
            and first_open_ns is not None
            and now_ns - first_open_ns
            >= genome.time_exit_min * 60 * 1_000_000_000
        )
        if time_due and genome.schema_version != 1 and genome.time_exit_mode in {"loss_only", "profit_only", "non_negative"} and not exact:
            blockers.append("stale_conversion_at_time_exit")
            unknown_money = True
        if time_due and _time_rule_matches(genome, total_minor, exact):
            if protection is not None:
                blockers.append("protection_market_close_latency_unmodeled")
                protection_blocked = True
                break
            realized_minor, unknown_money = _close_all(
                path, active, exits, index, "time_exit", execution,
                realized_minor, unknown_money, blockers,
            )
            reason = "time_exit"
            break

        if active and genome.trailing_distance is not None:
            _advance_trailing(
                active,
                path.direction,
                raw_exit,
                float(genome.trailing_distance),
            )

        if protection is not None:
            try:
                for position in active:
                    sl, sl_reason = _stop_level(
                        position,
                        genome,
                        now_ns,
                        path.direction,
                        observation_latency_ns,
                    )
                    tp = (
                        position.entry_price
                        + _sign(path.direction) * float(genome.target_steps[position.leg_index])
                        if genome.target_mode == "per_leg_steps"
                        else None
                    )
                    protection.request(
                        position.ticket,
                        index,
                        now_ns,
                        sl,
                        tp,
                        sl_reason,
                        "per_leg_target",
                    )
            except _OracleProtectionBlocked as exc:
                blockers.append(str(exc))
                protection_blocked = True
                break

    if active and not rollover_blocked and not protection_blocked:
        if protection is None:
            if last_index >= 0 and _usable_tick(path, last_index):
                realized_minor, unknown_money = _close_all(
                    path, active, exits, last_index, "data_end", execution,
                    realized_minor, unknown_money, blockers,
                )
                reason = "data_end"
        else:
            unknown_money = True
            reason = "not_closed"
        blockers.append("path_ended_before_strategy_exit")

    if last_index >= 0:
        final_time = int(path.times_ns[last_index])
        if any(requested <= final_time for requested in missing_fill_requests):
            blockers.append("entry_fill_quote_missing")
        for remaining in scheduled[schedule_cursor:]:
            if remaining.requested_ns is not None and remaining.requested_ns <= final_time < remaining.position.opened_ns:
                blockers.append("entry_request_in_flight_at_strategy_exit")
                break
    blockers = list(dict.fromkeys(blockers))
    if protection_blocked:
        unknown_money = True
    return OracleResult(
        signal_id=path.signal_id,
        strategy_fingerprint=genome.fingerprint,
        confidence_layer=confidence,
        entries=tuple(entries),
        exits=tuple(exits),
        pnl_eur=None if unknown_money else _minor_decimal(realized_minor, path.currency_digits),
        exit_reason=reason,
        max_favourable_eur=None if equity_incomplete or high_minor is None else _minor_decimal(high_minor, path.currency_digits),
        max_adverse_eur=None if equity_incomplete or low_minor is None else _minor_decimal(low_minor, path.currency_digits),
        max_floating_drawdown_eur=None if equity_incomplete or high_minor is None else _minor_decimal(drawdown_minor, path.currency_digits),
        max_favourable_move=_clean_price(favourable_move),
        max_adverse_move=_clean_price(adverse_move),
        blockers=tuple(blockers),
        last_tick_index=last_index,
        unfilled=False,
        filled_volume=_clean_volume(sum(item.volume for item in entries)),
        protection_events=tuple(protection.events) if protection is not None else (),
    )


def certify_candidate(
    paths: Sequence[DubaiPath],
    genome: StrategyGenome,
    fast_results: Sequence[object],
    *,
    execution: ExecutionScenario | None = None,
) -> OracleCertificate:
    fast_by_signal = {str(item.signal_id): item for item in fast_results}
    independent = tuple(
        oracle_simulate(path, genome, execution=execution)
        for path in paths
    )
    mismatches: list[OracleMismatch] = []
    for result in independent:
        fast = fast_by_signal.get(result.signal_id)
        if fast is None:
            mismatches.append(OracleMismatch(result.signal_id, "missing_fast_result", None, "present"))
            continue
        mismatches.extend(_compare_result(fast, result))
    oracle_ids = {item.signal_id for item in independent}
    for signal_id in sorted(set(fast_by_signal) - oracle_ids):
        mismatches.append(OracleMismatch(signal_id, "missing_oracle_path", "present", None))
    evidence_complete = all(
        result.pnl_eur is not None and not result.blockers
        for result in independent
    )
    if mismatches:
        status = "blocked"
    elif not evidence_complete:
        status = "blocked_evidence"
    else:
        status = "pass"
    return OracleCertificate(
        status=status,
        mismatches=tuple(mismatches),
        oracle_results=independent,
        promotion_eligible=(
            not mismatches
            and evidence_complete
            and not (
                execution is not None
                and (execution.protection is not None or execution.market is not None)
            )
        ),
    )


def stress_candidate(
    paths: Sequence[DubaiPath],
    genome: StrategyGenome,
    *,
    scenarios: Sequence[ExecutionScenario] | None = None,
) -> StressReport:
    scenarios = tuple(scenarios or (
        ExecutionScenario("latency_250ms", latency_ms=250),
        ExecutionScenario("latency_1s", latency_ms=1_000),
        ExecutionScenario("latency_2s", latency_ms=2_000),
        ExecutionScenario("adverse_costs", entry_slippage=0.10, exit_slippage=0.10, spread_addition=0.10),
    ))
    base_results = tuple(oracle_simulate(path, genome) for path in paths)
    base_net, base_blockers = _aggregate(base_results)
    stressed: list[StressScenarioResult] = []
    for scenario in scenarios:
        results = tuple(oracle_simulate(path, genome, execution=scenario) for path in paths)
        net, blockers = _aggregate(results)
        stressed.append(StressScenarioResult(scenario, net, blockers, results))
    eligible = (
        base_net is not None
        and base_net > 0
        and not base_blockers
        and all(item.net_eur is not None and item.net_eur > 0 and not item.blockers for item in stressed)
    )
    return StressReport(base_net, base_blockers, tuple(stressed), eligible)


def _oracle_protection_blockers(path, genome, profile):
    if profile is None:
        return ()
    errors: list[str] = []
    if profile.freeze_level_points:
        errors.append("protection_freeze_semantics_unsupported")
    extended = profile.policy_extension == "own_rule_be_partial_v1"
    absolute = profile.policy_extension in {"absolute_levels_v1", "absolute_levels_be_v1"}
    if profile.policy_extension == "basket_guard_v1":
        if (genome.stop_mode != "basket_money" or genome.target_mode != "none"
                or genome.be_mode != "none" or genome.trailing_distance is not None
                or genome.hard_stop_eur_per_leg is not None):
            errors.append("basket_guard_policy_contract")
    elif absolute:
        be_modes = {"none", "price", "provider"} if profile.policy_extension == "absolute_levels_be_v1" else {"none"}
        if genome.stop_mode != "fixed_level" or genome.target_mode != "per_leg_levels" or genome.be_mode not in be_modes:
            errors.append("absolute_level_policy_contract")
    elif (
        genome.stop_mode not in {"none", "fixed_move"}
        or genome.target_mode not in ({"none", "per_leg_steps", "partial_runner"} if extended else {"none", "per_leg_steps"})
        or genome.be_mode not in ({"none", "price"} if extended else {"none"})
    ):
        errors.append("protection_policy_unsupported")
    if genome.entry_mode != "actual_mt5" and profile.initial_protections:
        errors.append("observed_protection_requires_actual_entries")
    if genome.entry_mode == "actual_mt5":
        if genome.entry_ladder_mode != "simultaneous":
            errors.append("protection_actual_entries_require_simultaneous_schedule")
        initial_by_ticket = {row.ticket: row for row in profile.initial_protections}
        for leg in path.legs[:genome.leg_count]:
            if leg.ticket not in initial_by_ticket:
                errors.append(f"initial_protection_missing:{leg.ticket}")
        if genome.leg_count != len(path.legs):
            errors.append("protection_actual_entry_shape_required")
    return tuple(errors)


def _open_oracle_protection(
    lifecycle,
    path,
    genome,
    execution,
    scheduled,
    tick_index,
    timestamp_ns,
):
    position = scheduled.position
    if genome.entry_mode == "actual_mt5":
        initial = next(
            row
            for row in lifecycle.profile.initial_protections
            if row.ticket == position.ticket
        )
        sl, tp, source = initial.sl, initial.tp, initial.source
        if sl != lifecycle.rounded(sl) or tp != lifecycle.rounded(tp):
            raise _OracleProtectionBlocked(
                f"initial_protection_precision:{position.ticket}"
            )
    else:
        request_ns = scheduled.requested_ns
        times = [int(value) for value in path.times_ns]
        if request_ns is None or times.count(request_ns) != 1:
            raise _OracleProtectionBlocked(
                f"protection_request_quote_ambiguous:{position.ticket}"
            )
        request_index = bisect_left(times, request_ns)
        request_price = (
            _entry_quote(path, request_index)
            + _sign(path.direction) * execution.spread_addition / 2
        )
        candidates: list[float] = []
        if genome.trailing_distance is not None:
            candidates.append(
                request_price - _sign(path.direction) * float(genome.trailing_distance)
            )
        if genome.stop_mode == "fixed_move":
            candidates.append(
                request_price - _sign(path.direction) * float(genome.stop_value)
            )
        desired = (
            max(candidates) if path.direction == "BUY" else min(candidates)
        ) if candidates else None
        sl = lifecycle.rounded(desired)
        tp = None
        source = "hypothetical_open_request"
        if not lifecycle.pair_is_valid(float(path.exit_quotes[tick_index]), sl, tp):
            raise _OracleProtectionBlocked(
                f"initial_protection_rejected_unmodeled:{position.ticket}"
            )
    lifecycle.open(position.ticket, tick_index, timestamp_ns, sl, tp, source)


def _schedule_entries(
    path: DubaiPath,
    genome: StrategyGenome,
    execution: ExecutionScenario,
    *,
    missing_fill_requests: list[int],
) -> tuple[tuple[_Scheduled, ...], str, tuple[str, ...]]:
    if execution.entry_fill_latency_ms:
        if genome.entry_mode == "actual_mt5":
            return (), "counterfactual_entry", ("entry_fill_latency_requires_hypothetical_entries",)
        if genome.schema_version < 2 or genome.stop_mode == "provider":
            return (), "counterfactual_entry", ("entry_fill_latency_contract_unsupported",)
    if (
        genome.entry_mode == "actual_mt5"
        and path.entry_evidence_kind != "actual_mt5"
    ):
        return (), "counterfactual_entry", (
            "actual_entry_evidence_missing",
        )
    if genome.entry_ladder_mode != "simultaneous":
        return _schedule_ladder_entries(path, genome, execution, missing_fill_requests=missing_fill_requests)

    times = [int(item) for item in path.times_ns]
    if genome.entry_mode == "actual_mt5":
        first_opened_ns = min(_to_ns(leg.opened_at) for leg in path.legs)
        context_index = bisect_left(times, first_opened_ns)
        if context_index >= len(times):
            return (), "counterfactual_entry", ("missing_tick_for_context_filter",)
        if not _context_allowed(path, genome, context_index, execution):
            return (), "counterfactual_entry", ()
        exact_shape = genome.leg_count == len(path.legs)
        exact_volume = exact_shape and all(
            math.isclose(weight, leg.volume, abs_tol=1e-12)
            for weight, leg in zip(genome.volume_weights, path.legs, strict=True)
        )
        scheduled: list[_Scheduled] = []
        for index in range(genome.leg_count):
            template = path.legs[min(index, len(path.legs) - 1)]
            opened_ns = _to_ns(template.opened_at)
            tick_index = bisect_left(times, opened_ns)
            if tick_index >= len(times):
                return (), "counterfactual_entry", (f"missing_tick_for_entry:{template.ticket}",)
            if index < len(path.legs):
                base = template.open_price
                source = "observed_mt5_fill"
                ticket = template.ticket
            else:
                if not _usable_tick(path, tick_index):
                    return (), "counterfactual_entry", (f"invalid_tick_for_extra_entry:{tick_index}",)
                base = _entry_quote(path, tick_index)
                source = "counterfactual_extra_leg"
                ticket = f"sim_extra_{index + 1}"
            scheduled.append(_Scheduled(
                tick_index,
                source,
                _Position(
                    ticket=ticket,
                    role=template.role,
                    leg_index=index,
                    volume=float(genome.volume_weights[index]),
                    entry_price=(entry_price := _entry_with_cost(
                        path.direction,
                        base,
                        execution,
                    )),
                    opened_ns=opened_ns,
                    tp_events=template.tp_events,
                    sl_events=template.sl_events,
                    trailing_stop=_trailing_start(
                        path.direction,
                        entry_price,
                        genome.trailing_distance,
                    ),
                ),
            ))
        scheduled.sort(key=lambda item: (item.index, item.position.ticket))
        confidence = "observed_entry_management" if exact_volume else "counterfactual_entry"
        return tuple(scheduled), confidence, ()

    entry_index = _causal_index(path, genome, execution)
    if entry_index is None or not _context_allowed(
        path,
        genome,
        entry_index,
        execution,
    ):
        return (), "counterfactual_entry", ()
    requested_ns = int(path.times_ns[entry_index])
    close_time = _entry_cancel_time(path, genome, execution)
    if close_time is not None and close_time <= requested_ns:
        return (), "counterfactual_entry", ()
    entry_index = _delayed_execution_index(path, entry_index, execution.entry_fill_latency_ms)
    if entry_index is None:
        return (), "counterfactual_entry", ("entry_fill_quote_missing",)
    entry_ns = int(path.times_ns[entry_index])
    if close_time is not None and requested_ns < close_time < entry_ns:
        return (), "counterfactual_entry", ("provider_close_during_first_entry_execution_unmodeled",)
    price = _entry_with_cost(path.direction, _entry_quote(path, entry_index), execution)
    if (
        genome.stop_mode == "provider"
        and _provider_stop_invalidated_before_entry(
            path,
            path.legs[0].sl_events,
            entry_index,
            execution.latency_ms * 1_000_000,
        )
    ):
        return (), "counterfactual_entry", ()
    scheduled = tuple(
        _Scheduled(
            entry_index,
            f"causal_{genome.entry_mode}",
            _Position(
                ticket=f"sim_{index + 1}",
                role=path.legs[min(index, len(path.legs) - 1)].role,
                leg_index=index,
                volume=float(volume),
                entry_price=price,
                opened_ns=entry_ns,
                tp_events=path.legs[min(index, len(path.legs) - 1)].tp_events,
                sl_events=path.legs[min(index, len(path.legs) - 1)].sl_events,
                trailing_stop=_trailing_start(
                    path.direction,
                    price,
                    genome.trailing_distance,
                ),
            ),
            requested_ns=requested_ns,
        )
        for index, volume in enumerate(genome.volume_weights)
    )
    return scheduled, "counterfactual_entry", ()


def _schedule_ladder_entries(
    path: DubaiPath,
    genome: StrategyGenome,
    execution: ExecutionScenario,
    *,
    missing_fill_requests: list[int],
) -> tuple[tuple[_Scheduled, ...], str, tuple[str, ...]]:
    times = [int(item) for item in path.times_ns]
    requested_ns = None
    close_time = _entry_cancel_time(path, genome, execution)
    if genome.entry_mode == "actual_mt5":
        template = path.legs[0]
        base_ns = _to_ns(template.opened_at)
        base_index = bisect_left(times, base_ns)
        if base_index >= len(times):
            return (), "counterfactual_entry", (
                f"missing_tick_for_entry:{template.ticket}",
            )
        if not _context_allowed(path, genome, base_index, execution):
            return (), "counterfactual_entry", ()
        reference_price = template.open_price
        first_price = _entry_with_cost(
            path.direction,
            template.open_price,
            execution,
        )
        if genome.schema_version >= 2:
            reference_price = first_price
        first_ticket = template.ticket
        first_source = "observed_mt5_fill"
        expiry_anchor_ns = (
            _entry_expiry_anchor_ns(path)
            if genome.schema_version >= 2
            else base_ns
        )
        expiry_ns = (
            expiry_anchor_ns
            + genome.entry_expiry_min * 60 * 1_000_000_000
        )
    else:
        base_index = _causal_index(path, genome, execution)
        if base_index is None or not _context_allowed(
            path,
            genome,
            base_index,
            execution,
        ):
            return (), "counterfactual_entry", ()
        requested_ns = times[base_index]
        if close_time is not None and close_time <= requested_ns:
            return (), "counterfactual_entry", ()
        base_index = _delayed_execution_index(path, base_index, execution.entry_fill_latency_ms)
        if base_index is None:
            return (), "counterfactual_entry", ("entry_fill_quote_missing",)
        base_ns = times[base_index]
        if close_time is not None and requested_ns < close_time < base_ns:
            return (), "counterfactual_entry", ("provider_close_during_first_entry_execution_unmodeled",)
        reference_price = _entry_quote(path, base_index)
        first_price = _entry_with_cost(
            path.direction,
            reference_price,
            execution,
        )
        if genome.schema_version >= 2:
            reference_price = first_price
        first_ticket = "sim_1"
        first_source = f"causal_{genome.entry_mode}"
        expiry_ns = (
            _entry_expiry_anchor_ns(path)
            + genome.entry_expiry_min * 60 * 1_000_000_000
        )
        if (
            genome.stop_mode == "provider"
            and _provider_stop_invalidated_before_entry(
                path,
                path.legs[0].sl_events,
                base_index,
                execution.latency_ms * 1_000_000,
            )
        ):
            return (), "counterfactual_entry", ()

    first_template = path.legs[0]
    scheduled = [_Scheduled(
        base_index,
        first_source,
        _Position(
            ticket=first_ticket,
            role=first_template.role,
            leg_index=0,
            volume=float(genome.volume_weights[0]),
            entry_price=first_price,
            opened_ns=base_ns,
            tp_events=first_template.tp_events,
            sl_events=first_template.sl_events,
            trailing_stop=_trailing_start(
                path.direction,
                first_price,
                genome.trailing_distance,
            ),
        ),
        requested_ns=requested_ns,
    )]
    direction = _sign(path.direction)
    ladder_sign = -1.0 if genome.entry_ladder_mode == "adverse" else 1.0
    step = float(genome.entry_ladder_step)
    cursor = base_index
    for leg_index in range(1, genome.leg_count):
        matched_index = None
        for index in range(cursor, len(times)):
            if (
                times[index] >= expiry_ns
                if genome.schema_version >= 2
                else times[index] > expiry_ns
            ):
                break
            if not _usable_tick(path, index):
                continue
            quote = _entry_quote(path, index)
            if (
                direction
                * (quote - reference_price)
                * ladder_sign
                >= step * leg_index
            ):
                matched_index = index
                break
        if matched_index is None:
            break
        requested_ns = times[matched_index]
        if close_time is not None and close_time <= requested_ns:
            break
        matched_index = _delayed_execution_index(path, matched_index, execution.entry_fill_latency_ms)
        if matched_index is None:
            missing_fill_requests.append(requested_ns)
            break
        cursor = matched_index
        template = path.legs[min(leg_index, len(path.legs) - 1)]
        if (
            genome.stop_mode == "provider"
            and _provider_stop_invalidated_before_entry(
                path,
                template.sl_events,
                matched_index,
                execution.latency_ms * 1_000_000,
            )
        ):
            break
        scheduled.append(_Scheduled(
            matched_index,
            f"counterfactual_{genome.entry_ladder_mode}_ladder",
            _Position(
                ticket=f"sim_ladder_{leg_index + 1}",
                role=template.role,
                leg_index=leg_index,
                volume=float(genome.volume_weights[leg_index]),
                entry_price=(entry_price := _entry_with_cost(
                    path.direction,
                    _entry_quote(path, matched_index),
                    execution,
                )),
                opened_ns=times[matched_index],
                tp_events=template.tp_events,
                sl_events=template.sl_events,
                trailing_stop=_trailing_start(
                    path.direction,
                    entry_price,
                    genome.trailing_distance,
                ),
            ),
            requested_ns=requested_ns,
        ))
    return tuple(scheduled), "counterfactual_entry", ()


def _delayed_execution_index(path, decision_index, delay_ms):
    if delay_ms == 0:
        return decision_index
    deadline = int(path.times_ns[decision_index]) + delay_ms * 1_000_000
    for index in range(decision_index, len(path.times_ns)):
        if int(path.times_ns[index]) >= deadline and _usable_tick(path, index):
            return index
    return None


def _entry_cancel_time(path, genome, execution):
    earliest = None
    for event in path.provider_events:
        if is_strategy_close_action(event.action, genome.provider_management_mode):
            available = _to_ns(event.observed_at) + execution.latency_ms * 1_000_000
            if earliest is None or available < earliest:
                earliest = available
    return earliest


def _absolute_cancel_index(path, genome):
    direction = _sign(path.direction)
    target = min(genome.target_steps) if direction > 0 else max(genome.target_steps)
    for index, price in enumerate(path.exit_quotes):
        if direction * (float(price) - target) >= 0 or direction * (float(price) - float(genome.stop_value)) <= 0:
            return index
    return len(path.times_ns)


def _causal_index(path: DubaiPath, genome: StrategyGenome, execution: ExecutionScenario) -> int | None:
    times = [int(item) for item in path.times_ns]
    signal_ns = _to_ns(path.signal_observed_at)
    start_ns = signal_ns + execution.latency_ms * 1_000_000
    start_index = bisect_left(times, start_ns)
    if start_index >= len(times):
        return None
    expiry_ns = (
        _entry_expiry_anchor_ns(path)
        + genome.entry_expiry_min * 60 * 1_000_000_000
    )
    if genome.entry_mode == "no_entry":
        return None
    if genome.stop_mode == "fixed_level":
        cancelled = _absolute_cancel_index(path, genome)
        for index in range(start_index, min(cancelled, len(times))):
            if times[index] >= expiry_ns:
                break
            if not _usable_tick(path, index):
                continue
            price = _entry_quote(path, index)
            if genome.entry_mode == "published_range" and not float(genome.entry_value) <= price <= float(genome.entry_confirmation_value):
                continue
            if genome.entry_mode == "published_limit" and _sign(path.direction) * (price - float(genome.entry_value)) > 0:
                continue
            return index
        return None
    if genome.entry_mode == "delay":
        target_ns = start_ns + int(float(genome.entry_value) * 1_000_000_000)
        index = bisect_left(times, target_ns)
        if genome.schema_version >= 2:
            for candidate in range(index, len(times)):
                if times[candidate] >= expiry_ns:
                    break
                if _usable_tick(path, candidate):
                    return candidate
            return None
        return index if index < len(times) and times[index] <= expiry_ns else None

    if genome.entry_mode == "signal_market":
        for index in range(start_index, len(times)):
            if times[index] >= expiry_ns:
                break
            if _usable_tick(path, index):
                return index
        return None

    if genome.entry_mode == "adverse_reversal":
        reference_index = None
        for index in range(start_index, len(times)):
            if times[index] >= expiry_ns:
                break
            if _usable_tick(path, index):
                reference_index = index
                break
        if reference_index is None:
            return None
        reference = _entry_quote(path, reference_index)
        adverse = float(genome.entry_value)
        reversal = float(genome.entry_confirmation_value)
        armed = False
        extreme = reference
        for index in range(reference_index, len(times)):
            if times[index] >= expiry_ns:
                break
            if not _usable_tick(path, index):
                continue
            quote = _entry_quote(path, index)
            if not armed:
                crossed = (
                    quote <= reference - adverse
                    if path.direction == "BUY"
                    else quote >= reference + adverse
                )
                if crossed:
                    armed = True
                    extreme = quote
                continue
            if path.direction == "BUY":
                extreme = min(extreme, quote)
                if quote >= extreme + reversal:
                    return index
            else:
                extreme = max(extreme, quote)
                if quote <= extreme - reversal:
                    return index
        return None

    if not _usable_tick(path, start_index):
        return None
    reference = _entry_quote(path, start_index)
    distance = float(genome.entry_value)
    for index in range(start_index, len(times)):
        if times[index] >= expiry_ns if genome.schema_version >= 2 else times[index] > expiry_ns:
            break
        if not _usable_tick(path, index):
            continue
        quote = _entry_quote(path, index)
        if genome.entry_mode == "pullback":
            matches = quote <= reference - distance if path.direction == "BUY" else quote >= reference + distance
        elif genome.entry_mode == "momentum":
            matches = quote >= reference + distance if path.direction == "BUY" else quote <= reference - distance
        else:
            return None
        if matches:
            return index
    return None


def _context_allowed(
    path: DubaiPath,
    genome: StrategyGenome,
    index: int,
    execution: ExecutionScenario,
) -> bool:
    mode = genome.context_filter_mode
    if mode == "none":
        return True
    value = float(genome.context_filter_value)
    if mode == "max_spread":
        return float(path.ask[index]) - float(path.bid[index]) <= value
    if mode == "time_window":
        moment = _from_ns(int(path.times_ns[index]))
        hour = moment.hour + moment.minute / 60 + moment.second / 3600
        return hour <= value
    if mode == "max_volatility":
        start_ns = int(path.times_ns[index]) - 5 * 60 * 1_000_000_000
        start = bisect_left([int(item) for item in path.times_ns], start_ns)
        midpoints = [
            (float(path.bid[cursor]) + float(path.ask[cursor])) / 2
            for cursor in range(start, index + 1)
        ]
        return bool(midpoints) and max(midpoints) - min(midpoints) <= value
    if mode == "min_reward_risk":
        now_ns = int(path.times_ns[index])
        observation_latency_ns = execution.latency_ms * 1_000_000
        target = _latest_level(
            path.legs[0].tp_events,
            now_ns,
            include_be=True,
            observation_latency_ns=observation_latency_ns,
        )
        stop = _latest_level(
            path.legs[0].sl_events,
            now_ns,
            include_be=False,
            observation_latency_ns=observation_latency_ns,
        )
        if target is None or stop is None:
            return False
        entry = _entry_quote(path, index)
        reward = _sign(path.direction) * (target - entry)
        risk = -_sign(path.direction) * (stop - entry)
        return risk > 0 and reward / risk >= value
    return False


def _custom_be(position: _Position, genome: StrategyGenome, move: float, now_ns: int) -> None:
    if genome.be_mode == "price" and move >= float(genome.be_trigger):
        position.be_stop = position.entry_price
    elif genome.be_mode == "delayed" and now_ns - position.opened_ns >= int(float(genome.be_trigger) * 60 * 1_000_000_000):
        position.be_stop = position.entry_price
    elif genome.be_mode == "partial" and move >= float(genome.be_trigger) and position.role != "market_a":
        position.be_stop = position.entry_price


def _stop_level(
    position: _Position,
    genome: StrategyGenome,
    now_ns: int,
    direction: str,
    observation_latency_ns: int,
) -> tuple[float | None, str]:
    base = None
    reason = "provider_sl"
    if genome.stop_mode == "provider":
        base = _latest_level(
            position.sl_events,
            now_ns,
            include_be=genome.be_mode == "provider",
            observation_latency_ns=observation_latency_ns,
        )
    elif genome.stop_mode == "fixed_move":
        base = position.entry_price - _sign(direction) * float(genome.stop_value)
        reason = "fixed_sl"
    elif genome.stop_mode == "fixed_level":
        base, reason = float(genome.stop_value), "fixed_sl"
    if position.trailing_stop is not None:
        if base is None:
            base = position.trailing_stop
            reason = "trailing_stop"
        else:
            tighter = (
                max(base, position.trailing_stop)
                if direction == "BUY"
                else min(base, position.trailing_stop)
            )
            if math.isclose(tighter, position.trailing_stop, abs_tol=1e-12):
                reason = "trailing_stop"
            base = tighter
    if position.be_stop is None:
        return base, reason
    if base is None:
        return position.be_stop, position.be_reason
    tighter = max(base, position.be_stop) if direction == "BUY" else min(base, position.be_stop)
    return (tighter, position.be_reason) if math.isclose(tighter, position.be_stop, abs_tol=1e-12) else (tighter, reason)


def _latest_level(
    events: Iterable[LevelEvent],
    now_ns: int,
    *,
    include_be: bool,
    observation_latency_ns: int = 0,
) -> float | None:
    latest = None
    for event in events:
        if event.status not in {"confirmed", "snapshot"}:
            continue
        if not include_be and _be_source(event.source):
            continue
        if _to_ns(event.observed_at) + observation_latency_ns <= now_ns:
            latest = event.level
        else:
            break
    return latest


def _provider_stop_invalidated_before_entry(
    path: DubaiPath,
    events: Iterable[LevelEvent],
    entry_index: int,
    observation_latency_ns: int = 0,
) -> bool:
    times = [int(item) for item in path.times_ns]
    entry_ns = times[entry_index]
    signal_ns = _to_ns(path.signal_observed_at) + observation_latency_ns
    eligible = tuple(
        event
        for event in events
        if event.status in {"confirmed", "snapshot"}
        and not _be_source(event.source)
        and _to_ns(event.observed_at) + observation_latency_ns <= entry_ns
    )
    for offset, event in enumerate(eligible):
        event_ns = _to_ns(event.observed_at) + observation_latency_ns
        active_from = max(signal_ns, event_ns)
        active_until = (
            min(
                entry_ns,
                _to_ns(eligible[offset + 1].observed_at)
                + observation_latency_ns,
            )
            if offset + 1 < len(eligible)
            else entry_ns
        )
        start = bisect_left(times, active_from)
        end = bisect_left(times, active_until)
        if offset + 1 >= len(eligible):
            end = entry_index + 1
        end = min(end, entry_index + 1)
        for index in range(start, end):
            quote = float(path.exit_quotes[index])
            if _hit(path.direction, quote, float(event.level), target=False):
                return True
    return False


def _provider_target(
    path: DubaiPath,
    genome: StrategyGenome,
    now_ns: int,
    observation_latency_ns: int = 0,
) -> float | None:
    targets = []
    for leg in path.legs:
        value = _latest_level(
            leg.tp_events,
            now_ns,
            include_be=True,
            observation_latency_ns=observation_latency_ns,
        )
        if value is not None:
            targets.append(value)
    if not targets:
        return None
    ordered = sorted(set(targets), reverse=path.direction == "SELL")
    selected = max(1, int(round(float(genome.target_value)))) - 1
    return ordered[min(selected, len(ordered) - 1)]


def _provider_protection(positions: Iterable[_Position], events: Iterable[ProviderEvent]) -> None:
    for event in events:
        action = event.action.upper()
        if action == "MOVE_SL_TO_BE":
            for position in positions:
                position.be_stop = position.entry_price
                position.be_reason = "break_even"
        elif action == "MOVE_SL_TO_PRICE":
            price = _announced_price(event)
            if price is not None:
                for position in positions:
                    position.be_stop = price
                    position.be_reason = "provider_sl_move"


def _announced_price(event: ProviderEvent) -> float | None:
    for key in ("price", "sl", "stop", "target_price"):
        try:
            value = float(event.payload.get(key))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            return value
    match = re.search(
        r"(?:MOVE\s+)?(?:SL|STOP(?:\s+LOSS)?)\s*(?:TO|AT|@)?\s*[:=]?\s*(\d+(?:[.,]\d+)?)",
        str(event.payload.get("raw_text") or ""),
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    try:
        value = float(match.group(1).replace(",", "."))
    except ValueError:
        return None
    return value if math.isfinite(value) and value > 0 else None


def _close_all(
    path,
    active,
    exits,
    index,
    reason,
    execution,
    realized,
    unknown,
    blockers,
    *,
    exit_price=None,
):
    for position in tuple(active):
        realized, unknown = _close_one(
            path, position, position.volume, active, exits, index, reason,
            execution, realized, unknown, blockers,
            exit_price=exit_price,
        )
    return realized, unknown


def _close_one(
    path,
    position,
    volume,
    active,
    exits,
    index,
    reason,
    execution,
    realized,
    unknown,
    blockers,
    *,
    exit_price=None,
):
    volume = min(position.volume, _clean_volume(volume))
    if exit_price is None:
        exit_price = _exit_with_cost(path, index, execution)
    pnl_minor, exact = _money_minor(path, position.entry_price, exit_price, volume, index)
    if exact:
        pnl_minor += _allocate_swap(position, volume)
        realized += pnl_minor
    else:
        blockers.append(f"stale_conversion_at_exit:{index}")
        unknown = True
    exits.append(OracleExit(
        ticket=position.ticket,
        tick_index=index,
        closed_at=_from_ns(int(path.times_ns[index])),
        entry_price=position.entry_price,
        exit_price=exit_price,
        volume=volume,
        pnl_eur=_minor_decimal(pnl_minor, path.currency_digits) if exact else None,
        reason=reason,
    ))
    position.volume = _clean_volume(position.volume - volume)
    if position.volume <= 1e-12:
        active.remove(position)
    return realized, unknown


def _basket_money(path, active, index, execution):
    exit_price = _exit_with_cost(path, index, execution)
    total = 0
    exact = True
    for position in active:
        value, current_exact = _money_minor(path, position.entry_price, exit_price, position.volume, index)
        total += value + position.accrued_swap_minor
        exact = exact and current_exact
    return total, exact


def _position_money(path, position, index, execution):
    value, exact = _money_minor(
        path,
        position.entry_price,
        _exit_with_cost(path, index, execution),
        position.volume,
        index,
    )
    return value + position.accrued_swap_minor, exact


def _swap_volume_units(volume: float) -> int | None:
    scaled = Decimal(str(volume)) * Decimal(100)
    integral = scaled.to_integral_value(rounding=ROUND_HALF_UP)
    if abs(scaled - integral) > Decimal("0.00000001"):
        return None
    units = int(integral)
    return units if units >= 0 else None


def _allocate_swap(position: _Position, volume: float) -> int:
    if position.accrued_swap_minor == 0:
        return 0
    if math.isclose(volume, position.volume, abs_tol=1e-12):
        allocated = position.accrued_swap_minor
    else:
        raw = (
            Decimal(position.accrued_swap_minor)
            * Decimal(str(volume))
            / Decimal(str(position.volume))
        )
        allocated = int(raw.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    position.accrued_swap_minor -= allocated
    return allocated


def _money_minor(path: DubaiPath, entry: float, exit_price: float, volume: float, index: int) -> tuple[int, bool]:
    raw = Decimal(_sign(path.direction)) * (Decimal(str(exit_price)) - Decimal(str(entry)))
    raw *= Decimal(str(path.contract_size)) * Decimal(str(volume))
    orientation = path.conversion_orientation
    exact = orientation == "identity" or bool(path.fx_valid[index])
    if orientation == "account_base_profit_quote":
        quote = Decimal(str(path.fx_ask[index] if raw >= 0 else path.fx_bid[index]))
        raw /= quote
    elif orientation == "profit_base_account_quote":
        quote = Decimal(str(path.fx_bid[index] if raw >= 0 else path.fx_ask[index]))
        raw *= quote
    elif orientation != "identity":
        return 0, False
    scale = Decimal(10) ** path.currency_digits
    minor = int((raw * scale).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return minor, exact


def _fixed_move_target_reached(direction, raw_exit, positions, target):
    total_volume = sum(
        (Decimal(str(position.volume)) for position in positions),
        start=Decimal("0"),
    )
    if total_volume <= 0:
        return False
    weighted_entry = sum(
        (
            Decimal(str(position.entry_price))
            * Decimal(str(position.volume))
            for position in positions
        ),
        start=Decimal("0"),
    )
    move_numerator = Decimal(_sign(direction)) * (
        Decimal(str(raw_exit)) * total_volume - weighted_entry
    )
    return move_numerator >= Decimal(str(target)) * total_volume


def _trailing_start(direction, entry_price, trailing_distance):
    if trailing_distance is None:
        return None
    return _clean_price(
        entry_price - _sign(direction) * float(trailing_distance)
    )


def _advance_trailing(active, direction, executable_exit, distance):
    candidate = _clean_price(
        executable_exit - _sign(direction) * float(distance)
    )
    for position in active:
        if position.trailing_stop is None:
            position.trailing_stop = candidate
        elif direction == "BUY" and candidate > position.trailing_stop:
            position.trailing_stop = candidate
        elif direction == "SELL" and candidate < position.trailing_stop:
            position.trailing_stop = candidate


def _time_rule_matches(genome, total_minor, exact):
    if genome.schema_version == 1 or genome.time_exit_mode == "always":
        return True
    if not exact:
        return False
    if genome.time_exit_mode == "loss_only":
        return total_minor <= 0
    if genome.time_exit_mode == "profit_only":
        return total_minor > 0
    if genome.time_exit_mode == "non_negative":
        return total_minor >= 0
    return False


def _compare_result(fast: object, oracle: OracleResult) -> list[OracleMismatch]:
    mismatches: list[OracleMismatch] = []

    def compare(field: str, left: object, right: object, *, tolerance: float | None = None) -> None:
        equal = math.isclose(float(left), float(right), abs_tol=tolerance, rel_tol=0.0) if tolerance is not None and left is not None and right is not None else left == right
        if not equal:
            mismatches.append(OracleMismatch(oracle.signal_id, field, left, right))

    for field in (
        "strategy_fingerprint", "confidence_layer", "pnl_eur", "exit_reason",
        "max_favourable_eur", "max_adverse_eur", "max_floating_drawdown_eur",
        "blockers", "last_tick_index", "unfilled",
    ):
        compare(field, getattr(fast, field), getattr(oracle, field))
    for field in ("max_favourable_move", "max_adverse_move", "filled_volume"):
        compare(field, getattr(fast, field), getattr(oracle, field), tolerance=1e-9)
    _compare_records(mismatches, oracle.signal_id, "entries", getattr(fast, "entries"), oracle.entries)
    _compare_records(mismatches, oracle.signal_id, "exits", getattr(fast, "exits"), oracle.exits)
    _compare_records(
        mismatches,
        oracle.signal_id,
        "protection_events",
        getattr(fast, "protection_events"),
        oracle.protection_events,
    )
    _compare_records(
        mismatches, oracle.signal_id, "market_events",
        getattr(fast, "market_events", ()), oracle.market_events,
    )
    return mismatches


def _compare_records(mismatches, signal_id, prefix, fast_rows, oracle_rows):
    if len(fast_rows) != len(oracle_rows):
        mismatches.append(OracleMismatch(signal_id, f"{prefix}.length", len(fast_rows), len(oracle_rows)))
    if prefix == "entries":
        fields = (
            "ticket", "tick_index", "opened_at", "entry_price", "volume",
            "source", "requested_ns", "acknowledged_ns",
        )
    elif prefix == "exits":
        fields = (
            "ticket", "tick_index", "closed_at", "entry_price", "exit_price",
            "volume", "pnl_eur", "reason",
        )
    elif prefix == "protection_events":
        fields = (
            "ticket", "tick_index", "timestamp_ns", "request_id", "kind",
            "sl", "tp", "reason",
        )
    else:
        fields = (
            "ticket", "tick_index", "timestamp_ns", "request_id", "kind",
            "price", "volume", "reason",
        )
    for index, (fast, oracle) in enumerate(zip(fast_rows, oracle_rows)):
        for field in fields:
            left = getattr(fast, field, None) if field == "acknowledged_ns" else getattr(fast, field)
            right = getattr(oracle, field)
            if prefix in {"entries", "exits"} and field in {"entry_price", "exit_price", "volume"}:
                equal = math.isclose(float(left), float(right), abs_tol=1e-9, rel_tol=0.0)
            else:
                equal = left == right
            if not equal:
                mismatches.append(OracleMismatch(signal_id, f"{prefix}[{index}].{field}", left, right))


def _aggregate(results: Sequence[OracleResult]) -> tuple[Decimal | None, tuple[str, ...]]:
    blockers = tuple(dict.fromkeys(blocker for result in results for blocker in result.blockers))
    if any(result.pnl_eur is None for result in results):
        return None, blockers
    return sum((result.pnl_eur for result in results), start=Decimal("0")), blockers


def _path_errors(path: DubaiPath) -> tuple[str, ...]:
    lengths = {
        len(path.times_ns), len(path.bid), len(path.ask), len(path.exit_quotes),
        len(path.fx_bid), len(path.fx_ask), len(path.fx_age_ms), len(path.fx_valid),
    }
    errors = []
    if len(lengths) != 1 or not len(path.times_ns):
        errors.append("invalid_path_lengths")
    elif any(int(path.times_ns[index]) < int(path.times_ns[index - 1]) for index in range(1, len(path.times_ns))):
        errors.append("non_monotonic_path_time")
    if path.direction not in {"BUY", "SELL"}:
        errors.append("invalid_path_direction")
    if path.contract_size <= 0 or path.currency_digits < 0:
        errors.append("invalid_path_money_contract")
    if not path.legs:
        errors.append("path_without_entry_evidence")
    return tuple(errors)


def _empty(path, genome, blockers, *, confidence="unclassified", unfilled=False):
    zero = _minor_decimal(0, max(0, path.currency_digits))
    blockers = tuple(dict.fromkeys(blockers))
    return OracleResult(
        path.signal_id, genome.fingerprint, confidence, (), (),
        zero if not blockers else None,
        "not_filled" if unfilled else "blocked",
        zero if not blockers else None,
        zero if not blockers else None,
        zero if not blockers else None,
        0.0, 0.0, blockers, -1, unfilled, 0.0,
    )


def _amount_to_minor(value: float, digits: int) -> int:
    return int((Decimal(str(value)) * (Decimal(10) ** digits)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _minor_decimal(value: int, digits: int) -> Decimal:
    quantum = Decimal(1).scaleb(-digits)
    return Decimal(value).scaleb(-digits).quantize(quantum)


def _entry_quote(path: DubaiPath, index: int) -> float:
    return float(path.ask[index] if path.direction == "BUY" else path.bid[index])


def _entry_expiry_anchor_ns(path: DubaiPath) -> int:
    anchor = path.entry_expiry_anchor_at or path.signal_observed_at
    return _to_ns(anchor)


def _entry_with_cost(direction: str, price: float, scenario: ExecutionScenario) -> float:
    cost = scenario.entry_slippage + scenario.spread_addition
    return _clean_price(price + cost if direction == "BUY" else price - cost)


def _exit_with_cost(path: DubaiPath, index: int, scenario: ExecutionScenario) -> float:
    price = float(path.exit_quotes[index])
    cost = scenario.exit_slippage + scenario.spread_addition
    return _clean_price(price - cost if path.direction == "BUY" else price + cost)


def _target_exit_price(
    direction: str,
    target: float,
    scenario: ExecutionScenario,
) -> float:
    cost = scenario.exit_slippage + scenario.spread_addition
    return _clean_price(float(target) - _sign(direction) * cost)


def _usable_tick(path: DubaiPath, index: int) -> bool:
    bid = float(path.bid[index])
    ask = float(path.ask[index])
    return math.isfinite(bid) and math.isfinite(ask) and bid > 0 and ask >= bid


def _hit(direction: str, price: float, level: float, *, target: bool) -> bool:
    if target:
        return price >= level if direction == "BUY" else price <= level
    return price <= level if direction == "BUY" else price >= level


def _provider_close(action: str, provider_management_mode: str = "exact") -> bool:
    return is_strategy_close_action(action, provider_management_mode)


def _be_source(source: str) -> bool:
    normalized = str(source).upper()
    return "BE" in normalized or "BREAK EVEN" in normalized or "BREAKEVEN" in normalized


def _sign(direction: str) -> int:
    return 1 if direction == "BUY" else -1


def _to_ns(value: datetime) -> int:
    utc = value.astimezone(timezone.utc)
    days = utc.toordinal() - datetime(1970, 1, 1).toordinal()
    seconds = ((days * 24 + utc.hour) * 60 + utc.minute) * 60 + utc.second
    return seconds * 1_000_000_000 + utc.microsecond * 1000


def _from_ns(value: int) -> datetime:
    seconds, nanoseconds = divmod(int(value), 1_000_000_000)
    return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(
        seconds=seconds, microseconds=nanoseconds // 1000,
    )


def _clean_price(value: float) -> float:
    return round(float(value), 10)


def _clean_volume(value: float) -> float:
    return round(float(value), 10)
