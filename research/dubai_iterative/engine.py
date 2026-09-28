"""Deterministic causal replay engine for Dubai strategy research."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import math
import re
from typing import Callable, Generator, Iterable

import numpy as np

from mt5_read_protocol import MAX_HISTORY_SECONDS, MAX_RECORDS
from provider_action_semantics import is_strategy_close_action

from .contracts import StrategyGenome
from .dataset import DubaiLeg, DubaiPath, LevelEvent, ProviderEvent
from .protection_contract import ProtectionEvent, ProtectionProfile
from .protection import ProtectionBlocked, ProtectionBook, profile_blockers
from .market_contract import MarketEvent, MarketProfile
from .market import MarketBook, market_blockers
from .client import ClientAction, ClientBook, ClientEvent, ClientProfile, client_blockers
from .latency_model import LatencyModel


@dataclass(frozen=True)
class ExecutionAssumptions:
    entry_slippage: float = 0.0
    exit_slippage: float = 0.0
    spread_addition: float = 0.0
    latency_ms: int = 0
    entry_fill_latency_ms: int = 0
    protection: ProtectionProfile | None = None
    market: MarketProfile | None = None
    client: ClientProfile | None = None
    latency: LatencyModel | None = None
    # How stale the quote the client reads is when it first sees a signal
    # (terminal feed lag). Reference-based entries (adverse_reversal,
    # pullback, momentum) anchor on the last tick at or before
    # observed + latency_ms - quote_view_lag_ms. 0 keeps the old anchor.
    quote_view_lag_ms: int = 0

    def __post_init__(self) -> None:
        if self.client is not None and not isinstance(self.client, ClientProfile):
            raise ValueError("client must be a ClientProfile")
        if self.latency is not None and not isinstance(self.latency, LatencyModel):
            raise ValueError("latency must be a LatencyModel")
        if self.protection is not None and not isinstance(self.protection, ProtectionProfile):
            raise ValueError("protection must be a ProtectionProfile")
        if self.market is not None and not isinstance(self.market, MarketProfile):
            raise ValueError("market must be a MarketProfile")
        for name in ("entry_slippage", "exit_slippage", "spread_addition"):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(float(value)) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        for name in ("latency_ms", "entry_fill_latency_ms", "quote_view_lag_ms"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")


@dataclass(frozen=True)
class EntryRecord:
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
class ExitRecord:
    ticket: str
    tick_index: int
    closed_at: datetime
    entry_price: float
    exit_price: float
    volume: float
    pnl_eur: Decimal | None
    reason: str


@dataclass(frozen=True)
class SimulationResult:
    signal_id: str
    strategy_fingerprint: str
    confidence_layer: str
    entries: tuple[EntryRecord, ...]
    exits: tuple[ExitRecord, ...]
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
    behavior_digest: str | None = None
    protection_events: tuple[ProtectionEvent, ...] = ()
    market_events: tuple[MarketEvent, ...] = ()
    client_events: tuple[ClientEvent, ...] = ()


@dataclass
class _Position:
    ticket: str
    role: str
    leg_index: int
    volume: float
    entry_price: float
    opened_ns: int
    opened_index: int
    tp_events: tuple[LevelEvent, ...]
    sl_events: tuple[LevelEvent, ...]
    be_stop: float | None = None
    be_reason: str = "break_even"
    trailing_stop: float | None = None
    accrued_swap_minor: int = 0


@dataclass(frozen=True)
class _ScheduledEntry:
    tick_index: int
    position: _Position
    source: str
    requested_ns: int | None = None
    acknowledged_index: int | None = None
    request_index: int | None = None
    decision_index: int | None = None
    price_tick_index: int | None = None


@dataclass
class _PendingClientEntry:
    action: ClientAction
    request_index: int
    requested_ns: int
    source: str
    volume: float
    price_tick_index: int | None = None


@dataclass(frozen=True)
class _ReplayBoundary:
    tick_index: int
    time_ns: int


@dataclass(frozen=True)
class _ReplayRiskSnapshot:
    phase: str
    realized_minor: int | None
    floating_minor: int | None
    positions: tuple[tuple[str, float, float], ...]


@dataclass(frozen=True)
class _ReplaySettlement:
    tick_index: int
    time_ns: int
    continuing: bool
    realized_minor: int | None
    floating_minor: int | None
    positions: tuple[tuple[str, float, float], ...]
    snapshots: tuple[_ReplayRiskSnapshot, ...] = ()
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ReplayRepeat:
    tick_index: int


@dataclass(frozen=True)
class _ReplayBrokerAccess:
    read: Callable
    known_tickets: Callable
    advance_responses: Callable
    next_response_ns: Callable
    monitor_ready: Callable
    observe_summary: Callable
    apply_summary_decision: Callable
    terminal_requested: Callable
    resume_monitor: Callable
    apply_stop_plan: Callable
    runtime_control: object = None


class _ClientEntryTrigger:
    """Schema-2 entry observation state with no access to a future tape."""

    MODES = frozenset({"signal_market", "delay", "pullback", "momentum", "adverse_reversal", "no_entry"})

    def __init__(self, genome, direction, available_ns, expiry_ns, stale_reference=None):
        if genome.schema_version != 2 or genome.entry_mode not in self.MODES:
            raise ValueError("unsupported incremental client entry")
        self.genome = genome
        self.direction = direction
        self.available_ns = available_ns
        self.expiry_ns = expiry_ns
        self.done = genome.entry_mode == "no_entry"
        self.reference = None
        self.extreme = None
        self.armed = False
        # Quote the client already holds when the signal arrives (feed lag);
        # None keeps the first-available-quote anchor.
        self.stale_reference = stale_reference

    def observe(self, now, bid, ask):
        if self.done or now < self.available_ns:
            return False
        if now >= self.expiry_ns:
            self.done = True
            return False
        mode = self.genome.entry_mode
        usable = math.isfinite(bid) and math.isfinite(ask) and bid > 0 and ask >= bid
        if not usable:
            # These two rules require the first available quote as reference;
            # reversal/market/delay instead wait for a usable observation.
            if mode in {"pullback", "momentum"} and self.reference is None:
                self.done = True
            return False
        quote = ask if self.direction == "BUY" else bid
        sign = 1 if self.direction == "BUY" else -1
        if mode == "signal_market":
            matched = True
        elif mode == "delay":
            matched = now >= self.available_ns + int(float(self.genome.entry_value) * 1_000_000_000)
        else:
            if self.reference is None:
                self.reference = quote if self.stale_reference is None else self.stale_reference
                self.extreme = self.reference
            distance = float(self.genome.entry_value)
            if mode == "adverse_reversal":
                if not self.armed:
                    crossed = quote <= self.reference - distance if sign == 1 else quote >= self.reference + distance
                    if crossed:
                        self.armed = True
                        self.extreme = quote
                    return False
                self.extreme = min(self.extreme, quote) if sign == 1 else max(self.extreme, quote)
                reversal = float(self.genome.entry_confirmation_value)
                matched = quote >= self.extreme + reversal if sign == 1 else quote <= self.extreme - reversal
            elif mode == "pullback":
                matched = quote <= self.reference - distance if sign == 1 else quote >= self.reference + distance
            else:
                matched = quote >= self.reference + distance if sign == 1 else quote <= self.reference - distance
        self.done = bool(matched)
        return self.done


def simulate(
    path: DubaiPath,
    genome: StrategyGenome,
    *,
    execution: ExecutionAssumptions | None = None,
) -> SimulationResult:
    """Consume the scalar replay, preserving its existing public result."""
    steps = _simulation_steps(path, genome, execution=execution)
    while True:
        try:
            next(steps)
        except StopIteration as finished:
            return finished.value


def _simulation_steps(
    path: DubaiPath,
    genome: StrategyGenome,
    *,
    execution: ExecutionAssumptions | None = None,
    _transport=None,
) -> Generator[_ReplayBoundary | _ReplaySettlement, _ReplayRepeat | None, SimulationResult]:
    """Suspend before each quote; policy state stays in this private frame.

    This boundary alone does not model inter-basket transport. Non-client
    entries retain their bulk preparation; this is not a parity certificate.
    """

    execution = execution or ExecutionAssumptions()
    observation_latency_ns = execution.latency_ms * 1_000_000
    blockers = list(genome.validation_errors())
    path_blockers = _path_contract_blockers(path)
    blockers.extend(path_blockers)
    blockers.extend(profile_blockers(path, genome, execution.protection))
    blockers.extend(market_blockers(path, genome, execution))
    blockers.extend(client_blockers(genome, execution))
    if execution.latency is not None and execution.client is None:
        blockers.append("latency_model_requires_client_mode")
    if blockers:
        return _empty_result(path, genome, blockers=blockers)

    interquote = bool(_transport is not None and getattr(_transport, "interquote", False))
    runtime_identity = getattr(_transport, "runtime_identity", None) if interquote else None
    external_entries = getattr(runtime_identity, "entry_owner", None) == "external_runtime"
    pending_request_times: list[int] = []
    pending_request_indices: list[int] = []
    first_client_index = None
    initial_trigger = None
    if external_entries:
        scheduled, confidence_layer, entry_blockers = [], "external_runtime_entry", []
    elif execution.client:
        if genome.entry_mode not in _ClientEntryTrigger.MODES:
            return _empty_result(path, genome, blockers=("client_incremental_entry_mode_unsupported",))
        available_ns = _datetime_ns(path.signal_observed_at) + observation_latency_ns
        stale_reference = None
        if execution.quote_view_lag_ms:
            first_available = int(np.searchsorted(path.times_ns, available_ns, side="left"))
            stale_index = _reference_quote_index(path, available_ns, -1, execution)
            if 0 <= stale_index < first_available:
                stale_reference = _entry_quote(path, stale_index)
        initial_trigger = _ClientEntryTrigger(
            genome, path.direction, available_ns,
            _entry_expiry_anchor_ns(path) + genome.entry_expiry_min * 60_000_000_000,
            stale_reference=stale_reference,
        )
        scheduled, confidence_layer, entry_blockers = [], "counterfactual_entry", []
    else:
        scheduled, confidence_layer, entry_blockers = _prepare_entries(
            path, genome, execution, pending_request_times=pending_request_times,
            pending_request_indices=pending_request_indices,
        )
    blockers.extend(entry_blockers)
    if blockers:
        return _empty_result(
            path,
            genome,
            blockers=blockers,
            confidence_layer=confidence_layer,
        )
    if not external_entries and not execution.client and not scheduled and not (execution.market and pending_request_times):
        return _empty_result(
            path,
            genome,
            blockers=(),
            confidence_layer=confidence_layer,
            unfilled=True,
        )

    positions: list[_Position] = []
    entries: list[EntryRecord] = []
    exits: list[ExitRecord] = []
    schedule_cursor = 0
    provider_feed = getattr(_transport, "provider_event_feed", None) if _transport is not None else None
    if provider_feed is not None and path.provider_events:
        raise ValueError("dynamic and static provider events cannot be mixed")
    provider_events = (provider_feed.events if provider_feed is not None else tuple(
        sorted(path.provider_events, key=lambda event: _datetime_ns(event.observed_at))))
    provider_cursor = 0
    direct_be_ns = min((_datetime_ns(event.observed_at) + observation_latency_ns
        for event in provider_events if event.action == "MOVE_SL_TO_BE"
        and event.payload.get("modality") == "direct" and event.observed_at >= path.signal_observed_at),
        default=None) if genome.stop_mode == "fixed_level" and genome.be_mode == "provider" else None
    rollover_events = tuple(
        sorted(
            path.rollover_events,
            key=lambda event: _datetime_ns(event.observed_at),
        )
    )
    rollover_cursor = 0
    rollover_blocked = False
    realized_minor = 0
    money_unknown = False
    equity_incomplete = False
    partial_taken = False
    lock_armed = False
    max_total_minor: int | None = None
    min_total_minor: int | None = None
    max_drawdown_minor = 0
    max_favourable_move = 0.0
    max_adverse_move = 0.0
    last_tick_index = -1
    exit_reason = "not_closed"
    first_open_ns: int | None = None
    protection = ProtectionBook(execution.protection, path.direction) if execution.protection else None
    market = MarketBook(execution.market) if execution.market else None
    client = (_transport.client_book(execution.client) if _transport is not None else ClientBook(execution.client)) if execution.client else None
    pending_client_entry: _PendingClientEntry | None = None
    next_client_leg = 0
    client_expiry_ns = _entry_expiry_anchor_ns(path) + genome.entry_expiry_min * 60 * 1_000_000_000
    if client:
        protection.drain_closed_responses = True
    protection_blocked = False
    cancelled_entries = False
    market_close_reason = None
    terminal_close = None
    terminal_client = bool(client and execution.client.name in {
        "single_basket_terminal_v2", "single_basket_terminal_guard_v1"})
    read_management = bool(interquote and _transport.spec.management_reads is not None)
    broker_quote_index = -1
    observed_peak_minor = None
    guard_component = _transport.spec.guard_component if read_management else None
    guard_state = guard_component.initial_state() if guard_component is not None else None
    stop_component = _transport.spec.stop_component if read_management else None
    if runtime_identity is not None:
        from .runtime_control import RuntimeBrokerIdentity

        if (not isinstance(runtime_identity, RuntimeBrokerIdentity) or stop_component is not None
                or not read_management or genome.stop_mode != "none" or genome.target_mode != "none"
                or genome.trailing_distance is not None or genome.be_mode != "none"
                or genome.hard_stop_eur_per_leg is not None):
            raise ValueError("runtime control requires exclusive external protection ownership")
        if external_entries:
            if (not client or not market or guard_component is not None
                    or genome.provider_management_mode != "ignore"
                    or type(getattr(_transport, "external_entry_open", None)) is not bool):
                raise ValueError("external entry control requires exclusive explicit ownership")
            next_client_leg = genome.leg_count
    runtime_tickets, runtime_receipts, runtime_risk = {}, {}, []
    runtime_comments, runtime_deals, runtime_orders = {}, [], []
    runtime_close_receipts, runtime_exit_comments = {}, {}
    runtime_exit_cursor = 0
    sync_runtime_history = None
    runtime_mutations_allowed = False
    stop_requested_levels, stop_requested_at, stop_desired = {}, {}, {}
    last_client_observation_index = -1
    known_max_total_minor = None
    provider_close_ns = _provider_entry_cancellation_ns(path, genome, execution) if market else None

    def client_ladder_pending(now):
        return bool(client and not cancelled_entries and next_client_leg < genome.leg_count
                    and now < client_expiry_ns and genome.pending_entry_policy != "none")

    def release_client(index, now):
        if client is None or client.active is None:
            return
        action = client.active
        if action.operation == "modify":
            done = protection.states[action.ticket].pending is None
        else:
            requests = market.entries if action.operation == "entry" else market.closes
            done = action.ticket in requests and requests[action.ticket].acknowledged
        if done:
            client.release(index, now)

    def dispatch_client(index, now, *, allow_entry):
        nonlocal pending_client_entry
        if client is None:
            return
        while client.active is None and client.queue:
            if not allow_entry and client.queue[0].operation == "entry":
                return
            action = client.start(index, now)
            if action is None:
                return
            if action.operation == "entry":
                leg_index, = action.payload
                source = f"causal_{genome.entry_mode}" if leg_index == 0 else f"counterfactual_{genome.entry_ladder_mode}_ladder"
                volume = float(genome.volume_weights[leg_index])
                market.request(action.ticket, index, now, _entry_quote(path, index), volume, source, entry=True)
                pending_client_entry = _PendingClientEntry(action, index, now, source, volume)
            elif action.operation == "modify":
                state = protection.states[action.ticket]
                if not state.closed:
                    payload = action.payload
                    if len(payload) == 5 and payload[-1] == "preserve_tp":
                        payload = (payload[0], state.tp, payload[2], state.tp_reason)
                    protection.request(action.ticket, index, now, *payload)
                if state.pending is None:
                    client.release(index, now)
            else:
                volume, reason = action.payload
                market.request(action.ticket, index, now, float(path.exit_quotes[index]), volume, reason, entry=False)

    def observe_client_fill(index, now):
        nonlocal pending_client_entry
        if external_entries:
            return
        pending = pending_client_entry
        if pending is None or not _tick_is_usable(path, index):
            return
        leg_index, = pending.action.payload
        fill_delay = (execution.entry_fill_latency_ms if execution.latency is None
                      else execution.latency.entry_fill_delay_ms(path.signal_id, leg_index))
        price_delay = execution.client.entry_price_delay_ms
        if price_delay is None:
            price_delay = fill_delay
        if pending.price_tick_index is None and now >= pending.requested_ns + price_delay * 1_000_000:
            pending.price_tick_index = index
        if now < pending.requested_ns + fill_delay * 1_000_000:
            return
        price_index = pending.price_tick_index
        # The client contract rejects a price clock later than the fill clock.
        if price_index is None:
            raise ProtectionBlocked("client_price_quote_missing_at_fill")
        price = _adverse_entry_price(path.direction, _entry_quote(path, price_index), execution)
        template = path.legs[min(leg_index, len(path.legs) - 1)]
        scheduled.append(_ScheduledEntry(
            index, _Position(pending.action.ticket, template.role, leg_index, pending.volume, price,
                now, index, template.tp_events, template.sl_events,
                trailing_stop=_initial_trailing_stop(path.direction, price, genome.trailing_distance)),
            pending.source, requested_ns=pending.requested_ns, request_index=pending.request_index,
            decision_index=pending.action.decision_index, price_tick_index=price_index))
        pending_client_entry = None

    def observe_client_entries(index, now, *, new_quote=True):
        nonlocal next_client_leg, cancelled_entries, last_client_observation_index
        if external_entries:
            return
        if not new_quote or client.pending or cancelled_entries or read_management and _transport.monitor_busy:
            return
        if read_management:
            if index <= last_client_observation_index:
                return
            last_client_observation_index = index
        if provider_close_ns is not None and provider_close_ns <= now:
            cancelled_entries = True
            return
        if next_client_leg == 0:
            if index != first_client_index:
                return
            count = 1
        else:
            if next_client_leg >= genome.leg_count or now >= client_expiry_ns:
                return
            if not entries or entries[0].acknowledged_ns is None:
                return
            if not positions and genome.pending_entry_policy == "none":
                return
            sign = _direction_sign(path.direction) * (-1 if genome.entry_ladder_mode == "adverse" else 1)
            distance = sign * (_entry_quote(path, index) - entries[0].entry_price)
            count = 0
            for leg_index in range(next_client_leg, genome.leg_count):
                if distance < float(genome.entry_ladder_step) * leg_index:
                    break
                count += 1
                if execution.client.ladder_decision_mode == "fresh_quote":
                    break
        for leg_index in range(next_client_leg, next_client_leg + count):
            ticket = "sim_1" if leg_index == 0 else f"sim_ladder_{leg_index + 1}"
            client.enqueue(ClientAction("entry", ticket, index, now, (leg_index,)), index, now)
        next_client_leg += count

    def acknowledge_market(index, now):
        try:
            market.acknowledge(index, now)
        finally:
            # A later response can exhaust the trace after earlier acks succeed.
            for entry_index, entry in enumerate(entries):
                if (entry.acknowledged_ns is None
                        and market.entries[entry.ticket].acknowledged
                        and market.entries[entry.ticket].acknowledgement_ns is not None):
                    entries[entry_index] = replace(entry, acknowledged_ns=now)

    def latch_terminal_close(index, now, reason):
        nonlocal terminal_close, cancelled_entries, market_close_reason
        if terminal_close is None:
            intent = ClientAction("close", "basket", index, now, (reason,))
            client.emit("terminal_requested", intent, index, now)
            terminal_close = intent
        cancelled_entries = True
        market_close_reason = terminal_close.payload[0]
        client.cancel_entries(index, now)

    def queue_terminal_closes(index, now):
        # The basket decision survives fills that were not known at decision time.
        # A submitted close (including its delayed response) must not be repeated.
        if terminal_close is None:
            return
        for position in positions:
            if position.ticket not in market.closes:
                client.enqueue(ClientAction("close", position.ticket,
                    terminal_close.decision_index, terminal_close.decision_ns,
                    (position.volume, terminal_close.payload[0])), index, now)

    def request_market_close(selected, index, now, reason, *, basket):
        nonlocal cancelled_entries, market_close_reason, protection_blocked
        try:
            if terminal_client and basket:
                latch_terminal_close(index, now, reason)
                queue_terminal_closes(index, now)
                dispatch_client(index, now, allow_entry=False)
                return
            if any(request.processed_ns is None for request in market.entries.values()):
                raise ProtectionBlocked("market_close_with_entry_in_flight_unsupported")
            if basket:
                cancelled_entries = True
                market_close_reason = reason
                if client:
                    client.cancel_entries(index, now)
            for position in selected:
                if client:
                    client.enqueue(ClientAction("close", position.ticket, index, now,
                                                (position.volume, reason)), index, now)
                else:
                    market.request(position.ticket, index, now, float(path.exit_quotes[index]),
                                   position.volume, reason, entry=False)
            if client:
                dispatch_client(index, now, allow_entry=False)
        except ProtectionBlocked as exc:
            blockers.append(str(exc))
            protection_blocked = True

    risk_samples = []

    def risk_snapshot(index, phase):
        floating, exact = _basket_minor(path, positions, index, execution) if positions and _tick_is_usable(path, index) else (0, not positions)
        return _ReplayRiskSnapshot(
            phase, None if money_unknown else realized_minor, floating if exact else None,
            tuple((position.ticket, position.volume, position.entry_price) for position in positions),
        )

    def capture_risk(index, now, phase):
        if _transport is None:
            return
        _transport.reserve_risk()
        risk_samples.append(risk_snapshot(index, phase))

    def register_fill(item, index):
        nonlocal first_open_ns
        positions.append(item.position)
        first_open_ns = (item.position.opened_ns if first_open_ns is None
                         else min(first_open_ns, item.position.opened_ns))
        entries.append(EntryRecord(ticket=item.position.ticket, tick_index=index,
            opened_at=_ns_datetime(item.position.opened_ns), entry_price=item.position.entry_price,
            volume=item.position.volume, source=item.source, requested_ns=item.requested_ns,
            price_tick_index=item.price_tick_index))

    def observe_equity(index, now_ns):
        nonlocal max_favourable_move, max_adverse_move, equity_incomplete
        nonlocal max_total_minor, min_total_minor, max_drawdown_minor
        raw_exit = float(path.exit_quotes[index])
        for position in positions:
            directional_move = _direction_sign(path.direction) * (raw_exit - position.entry_price)
            max_favourable_move = max(max_favourable_move, directional_move)
            max_adverse_move = min(max_adverse_move, directional_move)
            if not protection:
                _update_custom_be(position, genome, directional_move, now_ns)
        floating_minor, floating_exact = _basket_minor(path, positions, index, execution)
        total_minor = realized_minor + floating_minor
        if not floating_exact:
            if not equity_incomplete:
                blockers.append("incomplete_equity_conversion")
            equity_incomplete = True
        if floating_exact and not money_unknown:
            max_total_minor = total_minor if max_total_minor is None else max(max_total_minor, total_minor)
            min_total_minor = total_minor if min_total_minor is None else min(min_total_minor, total_minor)
            max_drawdown_minor = max(max_drawdown_minor, max_total_minor - total_minor)
        return raw_exit, floating_minor, floating_exact, total_minor

    def process_quote(index, now_ns, *, new_quote=True):
        nonlocal first_client_index, last_tick_index, cancelled_entries, schedule_cursor
        nonlocal protection_blocked, first_open_ns, rollover_cursor, money_unknown, rollover_blocked
        nonlocal realized_minor, exit_reason, max_favourable_move, max_adverse_move, equity_incomplete
        nonlocal max_total_minor, min_total_minor, max_drawdown_minor, known_max_total_minor
        nonlocal provider_cursor, partial_taken, lock_armed
        nonlocal broker_quote_index
        nonlocal provider_close_ns, direct_be_ns
        if _transport is not None and not getattr(_transport, "admitted", True):
            broker_quote_index = index
            risk_samples.clear()
            capture_risk(index, now_ns, "quote_open" if new_quote else "grant_resume")
            return True
        if provider_feed is not None:
            provider_close_ns = provider_feed.close_ns
            direct_be_ns = provider_feed.direct_be_ns
        broker_quote_index = index
        risk_samples.clear()
        capture_risk(index, now_ns, "quote_open" if new_quote else "grant_resume")
        if initial_trigger and initial_trigger.observe(now_ns, float(path.bid[index]), float(path.ask[index])):
            if _context_allows(path, genome, index, execution):
                first_client_index = index
        if market:
            try:
                if terminal_client and provider_close_ns is not None and provider_close_ns <= now_ns:
                    latch_terminal_close(index, now_ns, "provider_close")
                if market.pending and _tick_is_usable(path, index):
                    last_tick_index = index
                    acknowledge_market(index, now_ns)
                if client and _tick_is_usable(path, index):
                    if client.pending:
                        last_tick_index = index
                    protection.acknowledge(index, now_ns)
                    release_client(index, now_ns)
                    if _transport is not None and provider_close_ns is not None and provider_close_ns <= now_ns:
                        cancelled_entries = True
                    if cancelled_entries:
                        client.cancel_entries(index, now_ns)
                    observe_client_entries(index, now_ns, new_quote=new_quote)
                    dispatch_client(index, now_ns, allow_entry=True)
                    observe_client_fill(index, now_ns)
                if not client and provider_close_ns is not None and provider_close_ns <= now_ns:
                    cancelled_entries = True
                    if _tick_is_usable(path, index) and any(
                        request.processed_ns is None and request.requested_ns < provider_close_ns
                        and (provider_close_ns < now_ns or now_ns < request.requested_ns + execution.entry_fill_latency_ms * 1_000_000)
                        for request in market.entries.values()
                    ):
                        raise ProtectionBlocked("market_close_with_entry_in_flight_unsupported")
                if not client and not cancelled_entries:
                    for item in scheduled[schedule_cursor:]:
                        if item.request_index == index:
                            market.request(item.position.ticket, index, now_ns,
                                           _entry_quote(path, index), item.position.volume,
                                           item.source, entry=True)
                            last_tick_index = index
                    if pending_request_indices and index == pending_request_indices[0]:
                        first = not scheduled
                        count = genome.leg_count if first and genome.entry_ladder_mode == "simultaneous" else 1
                        for leg_index in range(len(scheduled), len(scheduled) + count):
                            ticket = f"sim_{leg_index + 1}" if first else f"sim_ladder_{leg_index + 1}"
                            source = f"causal_{genome.entry_mode}" if first else f"counterfactual_{genome.entry_ladder_mode}_ladder"
                            market.request(ticket, index, now_ns, _entry_quote(path, index),
                                           float(genome.volume_weights[leg_index]), source, entry=True)
                        last_tick_index = index
            except ProtectionBlocked as exc:
                blockers.append(str(exc))
                protection_blocked = True
        if pending_request_indices and index >= pending_request_indices[0]:
            last_tick_index = index
        while (
            not protection_blocked
            and schedule_cursor < len(scheduled)
            and scheduled[schedule_cursor].tick_index == index
        ):
            item = scheduled[schedule_cursor]
            if cancelled_entries and (not market or item.position.ticket not in market.entries):
                schedule_cursor += 1
                continue
            if protection:
                try:
                    if market:
                        market.require_event_capacity()
                    if market and not market.valid_volume(item.position.volume):
                        market.finish(item.position.ticket, index, now_ns, None, entry=True, rejection="invalid_volume")
                        cancelled_entries = True
                        schedule_cursor += 1
                        exit_reason = "entry_rejected" if not positions else exit_reason
                        continue
                    _open_protection(protection, path, genome, execution, item, index, now_ns)
                except ProtectionBlocked as exc:
                    if market and str(exc).startswith("initial_protection_rejected_unmodeled:"):
                        try:
                            market.finish(item.position.ticket, index, now_ns, None, entry=True, rejection="invalid_initial_protection")
                        except ProtectionBlocked as budget_error:
                            blockers.append(str(budget_error))
                            protection_blocked = True
                            last_tick_index = index
                            break
                        cancelled_entries = True
                        schedule_cursor += 1
                        exit_reason = "entry_rejected" if not positions else exit_reason
                        continue
                    blockers.append(str(exc))
                    protection_blocked = True
                    last_tick_index = index
                    break
            register_fill(item, index)
            if market:
                try:
                    market.finish(item.position.ticket, index, now_ns, item.position.entry_price, entry=True)
                except ProtectionBlocked as exc:
                    blockers.append(str(exc))
                    protection_blocked = True
                    last_tick_index = index
                    break
            schedule_cursor += 1
            capture_risk(index, now_ns, "entry_fill")

        if terminal_close is not None and not protection_blocked and _tick_is_usable(path, index):
            try:
                queue_terminal_closes(index, now_ns)
                dispatch_client(index, now_ns, allow_entry=False)
            except ProtectionBlocked as exc:
                blockers.append(str(exc))
                protection_blocked = True

        while (
            rollover_cursor < len(rollover_events)
            and _datetime_ns(
                rollover_events[rollover_cursor].observed_at
            ) <= now_ns
        ):
            event = rollover_events[rollover_cursor]
            rollover_cursor += 1
            event_ns = _datetime_ns(event.observed_at)
            rollover_positions = [
                position
                for position in positions
                if position.opened_ns < event_ns
            ]
            if not rollover_positions:
                continue
            if event.blocker:
                blockers.append(event.blocker)
                money_unknown = True
                rollover_blocked = True
                last_tick_index = index
                break
            for position in rollover_positions:
                units = _volume_units(position.volume)
                if units is None or units >= len(event.minor_by_volume_unit):
                    blockers.append("swap_volume_unsupported")
                    money_unknown = True
                    rollover_blocked = True
                    last_tick_index = index
                    break
                position.accrued_swap_minor += int(
                    event.minor_by_volume_unit[units]
                )
                capture_risk(index, now_ns, "swap_accrual")
            if rollover_blocked:
                break
        if rollover_blocked:
            return False

        if not positions:
            if protection_blocked:
                return False
            if market:
                if entries and genome.pending_entry_policy == "none":
                    cancelled_entries = True
                if not _tick_is_usable(path, index):
                    last_tick_index = index
                    return True
                try:
                    acknowledge_market(index, now_ns)
                except ProtectionBlocked as exc:
                    blockers.append(str(exc))
                    protection_blocked = True
                    return False
                if market.pending:
                    last_tick_index = index
                    # A passive exit may precede a still-pending market close.
                    try:
                        for request in market.due_closes(index, now_ns):
                            market.finish(request.ticket, index, now_ns, None, entry=False,
                                          rejection="position_already_closed")
                        acknowledge_market(index, now_ns)
                    except ProtectionBlocked as exc:
                        blockers.append(str(exc))
                        protection_blocked = True
                        return False
                    if market.pending:
                        return True
                if client and client.pending:
                    last_tick_index = index
                    try:
                        protection.process(index, now_ns, float(path.exit_quotes[index]))
                        release_client(index, now_ns)
                        dispatch_client(index, now_ns, allow_entry=False)
                    except ProtectionBlocked as exc:
                        blockers.append(str(exc))
                        protection_blocked = True
                        return False
                    if client.pending:
                        return True
                if read_management and _transport.monitor_busy:
                    return True
                if cancelled_entries or entries and schedule_cursor >= len(scheduled) and not client_ladder_pending(now_ns):
                    return False
            if schedule_cursor >= len(scheduled) and entries and not pending_request_times and not client_ladder_pending(now_ns):
                return False
            return True

        if not _tick_is_usable(path, index):
            blockers.append(f"invalid_tick_at_index:{index}")
            last_tick_index = index
            return False
        last_tick_index = index

        raw_exit, floating_minor, floating_exact, total_minor = observe_equity(index, now_ns)

        if protection:
            try:
                for position in list(positions):
                    hit = protection.hit(position.ticket, raw_exit,
                                         index=index, now=now_ns)
                    if hit is None:
                        continue
                    stop_reason, target_price = hit
                    protection.close(position.ticket, index, now_ns, stop_reason)
                    realized_minor, money_unknown = _close_position(
                        path, position, position.volume, positions, exits, index,
                        stop_reason, execution, realized_minor, money_unknown, blockers,
                        exit_price=(None if target_price is None or
                                    execution.protection.passive_fill_scenario is not None and
                                    execution.protection.passive_fill_scenario.price_mode == "executable_quote"
                                    else _target_exit_price(path.direction, target_price, execution)),
                    )
                    if _transport is not None:
                        _transport.passive(position.ticket, now_ns, target=target_price is not None)
                    capture_risk(index, now_ns, "native_exit")
                    exit_reason = stop_reason
                # A rejected new opening cannot erase an older installed stop
                # reached on the same quote; stop after preserving those exits.
                if protection_blocked:
                    return False
                if market:
                    for request in market.due_closes(index, now_ns):
                        position = next((row for row in positions if row.ticket == request.ticket), None)
                        if position is None:
                            market.finish(request.ticket, index, now_ns, None, entry=False,
                                          rejection="position_already_closed")
                            continue
                        market.require_event_capacity()
                        if request.volume >= position.volume:
                            protection.close(position.ticket, index, now_ns, request.reason)
                        realized_minor, money_unknown = _close_position(
                            path, position, request.volume, positions, exits, index,
                            request.reason, execution, realized_minor, money_unknown, blockers,
                        )
                        market.finish(request.ticket, index, now_ns, exits[-1].exit_price, entry=False)
                        capture_risk(index, now_ns, "market_exit")
                        exit_reason = request.reason
                    acknowledge_market(index, now_ns)
                if not positions:
                    if market and genome.pending_entry_policy == "none":
                        cancelled_entries = True
                    if market and market.pending:
                        return True
                    if client and client.pending:
                        return True
                    if read_management and _transport.monitor_busy:
                        return True
                    if cancelled_entries:
                        return False
                    if client_ladder_pending(now_ns) or _pending_entries_remain(genome, schedule_cursor, len(scheduled) + len(pending_request_times)):
                        return True
                    return False
                protection.process(index, now_ns, raw_exit)
                if client:
                    release_client(index, now_ns)
                    dispatch_client(index, now_ns, allow_entry=False)
                    if client.pending:
                        return True
                if market and (market.entry_waiting or market_close_reason is not None):
                    return True
                if market and any(r.reason == "partial_target" and not r.acknowledged for r in market.closes.values()):
                    return True
                for position in positions:
                    if genome.be_mode == "price":
                        move = _direction_sign(path.direction) * (Decimal(str(raw_exit)) - Decimal(str(position.entry_price)))
                        if move >= Decimal(str(genome.be_trigger)):
                            position.be_stop = position.entry_price
                    elif direct_be_ns is not None and now_ns >= direct_be_ns:
                        position.be_stop = position.entry_price
            except ProtectionBlocked as exc:
                blockers.append(str(exc))
                protection_blocked = True
                return False

        if market:
            floating_minor, floating_exact = _basket_minor(path, positions, index, execution)
            total_minor = realized_minor + floating_minor
            if floating_exact and not money_unknown:
                known_max_total_minor = total_minor if known_max_total_minor is None else max(known_max_total_minor, total_minor)

        # 1. Emergency basket loss always wins over every profit rule.
        if genome.stop_mode == "basket_money":
            threshold = _money_value_to_minor(
                float(genome.stop_value),
                path.currency_digits,
            )
            if floating_exact and total_minor <= -threshold:
                if market:
                    request_market_close(positions, index, now_ns, "basket_stop", basket=True)
                    if protection_blocked:
                        return False
                    return True
                realized_minor, money_unknown = _close_all(
                    path,
                    positions,
                    exits,
                    index,
                    "basket_stop",
                    execution,
                    realized_minor,
                    money_unknown,
                    blockers,
                )
                exit_reason = "basket_stop"
                return False
            if not floating_exact:
                blockers.append("stale_conversion_at_basket_stop")
                money_unknown = True

        # A broker-side per-leg loss cap remains independent of basket rules.
        if genome.hard_stop_eur_per_leg is not None:
            hard_threshold = _money_value_to_minor(
                float(genome.hard_stop_eur_per_leg),
                path.currency_digits,
            )
            for position in list(positions):
                position_minor, exact = _position_minor(
                    path,
                    position,
                    index,
                    execution,
                )
                if exact and position_minor <= -hard_threshold:
                    if market:
                        request_market_close((position,), index, now_ns, "hard_stop_per_leg", basket=False)
                        continue
                    if protection:
                        blockers.append("protection_market_close_latency_unmodeled")
                        protection_blocked = True
                        break
                    realized_minor, money_unknown = _close_position(
                        path,
                        position,
                        position.volume,
                        positions,
                        exits,
                        index,
                        "hard_stop_per_leg",
                        execution,
                        realized_minor,
                        money_unknown,
                        blockers,
                    )
                    exit_reason = "hard_stop_per_leg"
                elif not exact:
                    blockers.append(
                        f"stale_conversion_at_hard_stop:{position.ticket}"
                    )
                    money_unknown = True
            if protection_blocked:
                return False
            if not positions:
                return False

        # 2. Explicit provider close instructions.
        provider_due: list[ProviderEvent] = []
        while (
            provider_cursor < len(provider_events)
            and _datetime_ns(provider_events[provider_cursor].observed_at)
            + observation_latency_ns
            <= now_ns
        ):
            provider_due.append(provider_events[provider_cursor])
            provider_cursor += 1
        if genome.be_mode == "provider" and genome.stop_mode != "fixed_level":
            _apply_provider_protection(positions, provider_due)
        if genome.provider_management_mode in {
            "exact",
            "close_only",
            "explicit_close_only",
        }:
            close_event = next(
                (
                    event
                    for event in provider_due
                    if _is_provider_close(
                        event.action,
                        genome.provider_management_mode,
                    )
                ),
                None,
            )
            if close_event is not None:
                if market:
                    request_market_close(positions, index, now_ns, "provider_close", basket=True)
                    if protection_blocked:
                        return False
                    return True
                if protection:
                    blockers.append("protection_market_close_latency_unmodeled")
                    protection_blocked = True
                    return False
                realized_minor, money_unknown = _close_all(
                    path,
                    positions,
                    exits,
                    index,
                    "provider_close",
                    execution,
                    realized_minor,
                    money_unknown,
                    blockers,
                )
                exit_reason = "provider_close"
                return False

        # 3. Effective SL, including custom BE.
        for position in ([] if protection else list(positions)):
            stop_level, stop_reason = _effective_stop(
                position,
                genome,
                now_ns,
                path.direction,
                observation_latency_ns,
            )
            if stop_level is None or not _level_hit(
                path.direction,
                raw_exit,
                stop_level,
                kind="stop",
            ):
                continue
            realized_minor, money_unknown = _close_position(
                path,
                position,
                position.volume,
                positions,
                exits,
                index,
                stop_reason,
                execution,
                realized_minor,
                money_unknown,
                blockers,
            )
            exit_reason = stop_reason
        if not positions:
            return False

        floating_minor, floating_exact = _basket_minor(
            path,
            positions,
            index,
            execution,
        )
        total_minor = realized_minor + floating_minor

        # 4. Targets and partial exits.
        if genome.target_mode == "provider_per_leg":
            for position in list(positions):
                target = _latest_level(
                    position.tp_events,
                    now_ns,
                    include_be=True,
                    observation_latency_ns=observation_latency_ns,
                )
                if target is None or not _level_hit(
                    path.direction,
                    raw_exit,
                    target,
                    kind="target",
                ):
                    continue
                realized_minor, money_unknown = _close_position(
                    path,
                    position,
                    position.volume,
                    positions,
                    exits,
                    index,
                    "provider_tp",
                    execution,
                    realized_minor,
                    money_unknown,
                    blockers,
                    exit_price=_target_exit_price(
                        path.direction, target, execution
                    ),
                )
                exit_reason = "provider_tp"
        elif genome.target_mode == "per_leg_steps" and not protection:
            direction = _direction_sign(path.direction)
            for position in list(positions):
                target = position.entry_price + direction * float(
                    genome.target_steps[position.leg_index]
                )
                if not _level_hit(
                    path.direction,
                    raw_exit,
                    target,
                    kind="target",
                ):
                    continue
                realized_minor, money_unknown = _close_position(
                    path,
                    position,
                    position.volume,
                    positions,
                    exits,
                    index,
                    "per_leg_target",
                    execution,
                    realized_minor,
                    money_unknown,
                    blockers,
                    exit_price=_target_exit_price(
                        path.direction, target, execution
                    ),
                )
                exit_reason = "per_leg_target"
        elif genome.target_mode == "provider_target_all":
            target = _selected_provider_target(
                path,
                genome,
                now_ns,
                observation_latency_ns,
            )
            if target is not None and _level_hit(
                path.direction,
                raw_exit,
                target,
                kind="target",
            ):
                realized_minor, money_unknown = _close_all(
                    path,
                    positions,
                    exits,
                    index,
                    "provider_target_all",
                    execution,
                    realized_minor,
                    money_unknown,
                    blockers,
                    exit_price=_target_exit_price(
                        path.direction, target, execution
                    ),
                )
                exit_reason = "provider_target_all"
        elif genome.target_mode == "fixed_basket":
            threshold = _money_value_to_minor(
                float(genome.target_value),
                path.currency_digits,
            )
            if floating_exact and total_minor >= threshold:
                realized_minor, money_unknown = _close_all(
                    path,
                    positions,
                    exits,
                    index,
                    "basket_target",
                    execution,
                    realized_minor,
                    money_unknown,
                    blockers,
                )
                exit_reason = "basket_target"
            elif not floating_exact:
                blockers.append("stale_conversion_at_basket_target")
                money_unknown = True
        elif genome.target_mode == "fixed_move":
            if _fixed_move_target_reached(
                path.direction,
                raw_exit,
                positions,
                float(genome.target_value),
            ):
                realized_minor, money_unknown = _close_all(
                    path,
                    positions,
                    exits,
                    index,
                    "fixed_move_target",
                    execution,
                    realized_minor,
                    money_unknown,
                    blockers,
                )
                exit_reason = "fixed_move_target"
        elif genome.target_mode == "partial_runner":
            if not floating_exact:
                blockers.append("stale_conversion_at_basket_target")
                money_unknown = True
            first_target = _money_value_to_minor(
                float(genome.target_value),
                path.currency_digits,
            )
            runner_target = _money_value_to_minor(
                float(genome.runner_target),
                path.currency_digits,
            )
            if not partial_taken and floating_exact and total_minor >= first_target:
                if market:
                    try:
                        for position in positions:
                            volume = float(Decimal(str(position.volume)) * Decimal(str(genome.partial_fraction)))
                            market.request(position.ticket, index, now_ns, raw_exit, volume, "partial_target", entry=False)
                        partial_taken = True
                    except ProtectionBlocked as exc:
                        blockers.append(str(exc))
                        protection_blocked = True
                        return False
                    return True
                for position in list(positions):
                    close_volume = _clean_volume(
                        position.volume * float(genome.partial_fraction)
                    )
                    if close_volume <= 0:
                        continue
                    realized_minor, money_unknown = _close_position(
                        path,
                        position,
                        close_volume,
                        positions,
                        exits,
                        index,
                        "partial_target",
                        execution,
                        realized_minor,
                        money_unknown,
                        blockers,
                    )
                partial_taken = True
            if positions:
                floating_minor, floating_exact = _basket_minor(
                    path,
                    positions,
                    index,
                    execution,
                )
                total_minor = realized_minor + floating_minor
                if partial_taken and floating_exact and total_minor >= runner_target:
                    if market:
                        request_market_close(positions, index, now_ns, "runner_target", basket=True)
                        if protection_blocked:
                            return False
                        return True
                    realized_minor, money_unknown = _close_all(
                        path,
                        positions,
                        exits,
                        index,
                        "runner_target",
                        execution,
                        realized_minor,
                        money_unknown,
                        blockers,
                    )
                    exit_reason = "runner_target"
        if not positions:
            if client_ladder_pending(now_ns) or _pending_entries_remain(
                genome,
                schedule_cursor,
                len(scheduled) + len(pending_request_times),
            ):
                return True
            return False

        # 5. Trailing giveback protection.
        if genome.profit_lock_arm is not None and not read_management:
            arm = _money_value_to_minor(
                float(genome.profit_lock_arm),
                path.currency_digits,
            )
            giveback = _money_value_to_minor(
                float(genome.profit_lock_giveback),
                path.currency_digits,
            )
            policy_max_total = known_max_total_minor if market else max_total_minor
            if floating_exact and policy_max_total is not None:
                lock_armed = lock_armed or policy_max_total >= arm
                if lock_armed and total_minor <= policy_max_total - giveback:
                    if market:
                        request_market_close(positions, index, now_ns, "profit_lock", basket=True)
                        if protection_blocked:
                            return False
                        return True
                    if protection:
                        blockers.append("protection_market_close_latency_unmodeled")
                        protection_blocked = True
                        return False
                    realized_minor, money_unknown = _close_all(
                        path,
                        positions,
                        exits,
                        index,
                        "profit_lock",
                        execution,
                        realized_minor,
                        money_unknown,
                        blockers,
                    )
                    exit_reason = "profit_lock"
                    return False
            elif not floating_exact:
                blockers.append("stale_conversion_during_profit_lock")
                money_unknown = True

        # 6. Conditional time exit.
        time_due = (
            positions and not read_management
            and first_open_ns is not None
            and now_ns - first_open_ns >= genome.time_exit_min * 60 * 1_000_000_000
        )
        if time_due and genome.schema_version != 1 and genome.time_exit_mode in {"loss_only", "profit_only", "non_negative"} and not floating_exact:
            blockers.append("stale_conversion_at_time_exit")
            money_unknown = True
        if time_due and _time_exit_applies(genome, total_minor, floating_exact):
            if market:
                request_market_close(positions, index, now_ns, "time_exit", basket=True)
                if protection_blocked:
                    return False
                return True
            if protection:
                blockers.append("protection_market_close_latency_unmodeled")
                protection_blocked = True
                return False
            realized_minor, money_unknown = _close_all(
                path,
                positions,
                exits,
                index,
                "time_exit",
                execution,
                realized_minor,
                money_unknown,
                blockers,
            )
            exit_reason = "time_exit"
            return False

        # Broker stop modifications observed on this tick become effective for
        # the next tick, matching the live asynchronous modify request.
        if positions and genome.trailing_distance is not None:
            _tighten_trailing_stops(
                positions,
                path.direction,
                raw_exit,
                float(genome.trailing_distance),
            )

        if protection and stop_component is None and runtime_identity is None:
            try:
                for position in positions:
                    if market and position.ticket in market.closes:
                        continue
                    sl, sl_reason = _effective_stop(position, genome, now_ns, path.direction, observation_latency_ns)
                    tp = (float(genome.target_steps[position.leg_index]) if genome.target_mode == "per_leg_levels" else
                          position.entry_price + _direction_sign(path.direction) * float(genome.target_steps[position.leg_index])
                          if genome.target_mode == "per_leg_steps" else None)
                    if client:
                        state = protection.states[position.ticket]
                        if state.sl != protection.rounded(sl) or state.tp != protection.rounded(tp):
                            client.enqueue(ClientAction("modify", position.ticket, index, now_ns,
                                                        (sl, tp, sl_reason, "per_leg_target")), index, now_ns)
                    else:
                        protection.request(position.ticket, index, now_ns, sl, tp, sl_reason, "per_leg_target")
                if client:
                    dispatch_client(index, now_ns, allow_entry=False)
            except ProtectionBlocked as exc:
                blockers.append(str(exc))
                protection_blocked = True
                return False
        return True

    if interquote:
        def ticket_maps():
            return {entry.ticket: offset + 1 for offset, entry in enumerate(entries)}

        def read_broker(request, now):
            from mt5_read_protocol import ReadOperation

            index = broker_quote_index
            if index < 0 or int(path.times_ns[index]) > now:
                raise ProtectionBlocked("read_without_causal_broker_quote")
            aliases = ticket_maps()
            source = int(path.times_ns[index])
            if request.operation is ReadOperation.TICK:
                data = ({"bid": float(path.bid[index]), "ask": float(path.ask[index])}
                        if _tick_is_usable(path, index) else None)
            elif request.operation is ReadOperation.POSITIONS:
                if not _tick_is_usable(path, index):
                    return source, index, None
                data = []
                for position in positions:
                    value, exact = _money_minor(path, position.entry_price, float(path.exit_quotes[index]),
                                                position.volume, index)
                    if not exact:
                        data = None
                        break
                    data.append({"ticket": aliases[position.ticket], "volume": position.volume,
                                 "price_open": position.entry_price,
                                 "profit": float(_minor_decimal(value, path.currency_digits)),
                                 "symbol": _transport.profile.read_symbol,
                                 "sl": protection.states[position.ticket].sl or 0.,
                                 "tp": protection.states[position.ticket].tp or 0.})
            elif request.operation is ReadOperation.SYMBOL:
                if stop_component is None:
                    raise ProtectionBlocked("undeclared_symbol_valuation_profile")
                data = ({"point": protection.profile.point, "digits": protection.profile.digits}
                        if request.params["symbol"] == _transport.profile.read_symbol else None)
            elif request.operation is ReadOperation.PROFIT:
                if stop_component is None:
                    raise ProtectionBlocked("undeclared_profit_valuation_profile")
                from research.broker_valuation import linear_profit_value

                data = (linear_profit_value(path, request.params, index)
                        if request.params["symbol"] == _transport.profile.read_symbol else None)
            elif request.operation is ReadOperation.DEALS_POSITION:
                selected = next((entry for entry in entries
                                 if aliases[entry.ticket] == request.params["position"]), None)
                data = []
                if selected is not None:
                    data.append({"entry": 0, "volume": selected.volume, "profit": 0.,
                                 "commission": 0., "swap": 0., "fee": 0.})
                    for exit in exits:
                        if exit.ticket != selected.ticket:
                            continue
                        gross, exact = _money_minor(path, exit.entry_price, exit.exit_price,
                                                    exit.volume, exit.tick_index)
                        if not exact or exit.pnl_eur is None:
                            data = None
                            break
                        gross_money = _minor_decimal(gross, path.currency_digits)
                        data.append({"entry": 1, "volume": exit.volume, "profit": float(gross_money),
                                     "commission": 0., "swap": float(exit.pnl_eur - gross_money), "fee": 0.})
            else:
                raise ProtectionBlocked("unsupported_replay_broker_read")
            return source, index, data

        def known_tickets():
            aliases = ticket_maps()
            return [aliases[entry.ticket] for entry in entries
                    if market.entries[entry.ticket].acknowledged]

        def advance_responses(now):
            if broker_quote_index < 0:
                return
            acknowledge_market(broker_quote_index, now)
            protection.acknowledge(broker_quote_index, now)
            release_client(broker_quote_index, now)
            if _tick_is_usable(path, broker_quote_index):
                dispatch_client(broker_quote_index, now, allow_entry=True)

        def next_response_ns():
            times = [request.acknowledgement_ns for request in market.requests
                     if not request.acknowledged and request.acknowledgement_ns is not None]
            times.extend(state.ack_ns for state in protection.states.values()
                         if state.pending is not None and state.ack_ns is not None)
            return min(times) if times else None

        def monitor_ready():
            return bool(positions and known_tickets() and not client.pending and terminal_close is None
                        and not protection_blocked)

        def resume_monitor(now):
            # A quote received during a blocking read still needs one entry
            # observation before the next read cycle. Do not replay broker effects.
            if read_management and not protection_blocked and _tick_is_usable(path, broker_quote_index):
                observe_client_entries(broker_quote_index, now)
                dispatch_client(broker_quote_index, now, allow_entry=True)

        def observe_summary(summary, now):
            nonlocal observed_peak_minor, lock_armed, guard_state
            decision = {"time_ns": now, "quote_index": broker_quote_index, "action": "none", "reason": None}
            if terminal_close is not None:
                return {**decision, "reason": "terminal_already_requested"}
            if guard_component is not None:
                before = asdict(guard_state)
                elapsed = max(0., (now - (first_open_ns if first_open_ns is not None else
                                          _datetime_ns(path.signal_observed_at))) / 60_000_000_000)
                try:
                    outcome = guard_component.evaluate(guard_state, summary, elapsed)
                except (RuntimeError, TypeError, ValueError):
                    return {**decision, "action": "blocked", "blocker": "canonical_guard_observation_invalid",
                            "guard_strategy_id": guard_component.strategy_id,
                            "state_before": before, "state_after": before}
                guard_state = outcome.state
                decision.update(action=outcome.action, reason=outcome.reason,
                                observed_pl=outcome.observed_pl, elapsed_min=elapsed,
                                guard_strategy_id=guard_component.strategy_id,
                                state_before=before, state_after=asdict(guard_state))
                return decision
            if not summary.get("realized_complete") or summary.get("total_pl") is None:
                return {**decision, "reason": "money_evidence_incomplete"}
            if not summary["n_open"]:
                return decision
            total = _money_value_to_minor(summary["total_pl"], path.currency_digits)
            observed_peak_minor = total if observed_peak_minor is None else max(observed_peak_minor, total)
            reason = None
            if genome.profit_lock_arm is not None:
                arm = _money_value_to_minor(genome.profit_lock_arm, path.currency_digits)
                giveback = _money_value_to_minor(genome.profit_lock_giveback, path.currency_digits)
                lock_armed = lock_armed or observed_peak_minor >= arm
                if lock_armed and total <= observed_peak_minor - giveback:
                    reason = "profit_lock"
            if (reason is None and first_open_ns is not None
                    and now - first_open_ns >= genome.time_exit_min * 60_000_000_000
                    and _time_exit_applies(genome, total, True)):
                reason = "time_exit"
            decision.update(observed_minor=total, peak_minor=observed_peak_minor, armed=lock_armed)
            if reason is not None:
                decision.update(action="close", reason=reason)
            return decision

        def apply_summary_decision(summary, decision, now):
            nonlocal guard_state, protection_blocked
            application = {"status": "not_requested", "blocker": decision.get("blocker")}
            try:
                if application["blocker"]:
                    raise ProtectionBlocked(application["blocker"])
                if decision["action"] == "close":
                    queue_observed_close(summary, now, decision["reason"])
                    application["status"] = "queued"
            except ProtectionBlocked as exc:
                application.update(status="blocked", blocker=str(exc))
                if guard_state is not None and guard_state.triggered:
                    guard_state = replace(guard_state, recovery_pending=True)
                blockers.append(str(exc))
                protection_blocked = True
            if guard_state is not None:
                application["state_after_application"] = asdict(guard_state)
            if decision["action"] == "close":
                application["queued_close_tickets"] = [action.ticket for action in client.queue
                                                         if action.operation == "close"]
                application["submitted_close_tickets"] = list(market.closes)
            return application

        def queue_observed_close(summary, now, reason):
            request_market_close(positions, broker_quote_index, now, reason, basket=True)
            if protection_blocked:
                raise ProtectionBlocked(blockers[-1])
            aliases = ticket_maps()
            for entry in entries:
                if aliases[entry.ticket] in summary["open_tickets"] and entry.ticket not in market.closes:
                    client.enqueue(ClientAction("close", entry.ticket, broker_quote_index, now,
                                                (entry.volume, reason)), broker_quote_index, now)
            dispatch_client(broker_quote_index, now, allow_entry=False)

        def apply_stop_plan(plan, now):
            from basket_stop import basket_stop_requirement

            application = {"status": "not_requested", "time_ns": now, "tickets": [],
                           "protection_installed": False, "blocker": None}
            if terminal_close is not None:
                return {**application, "status": "terminal_already_requested"}
            if plan["positions_complete"] and not plan["positions"]:
                return {**application, "status": "no_open_positions"}
            if not plan["positions_complete"] or plan["stop_price"] is None:
                return {**application, "status": "unavailable"}
            by_alias = {alias: ticket for ticket, alias in ticket_maps().items()}
            strongest = max if path.direction == "BUY" else min
            try:
                for alias, spec in sorted(plan["positions"].items()):
                    ticket = by_alias[alias]
                    required = plan["stop_price"]
                    status = basket_stop_requirement(path.direction, spec, required,
                        last_level=stop_requested_levels.get(ticket),
                        last_request=stop_requested_at.get(ticket, 0.), now=now / 1e9)
                    row = {"ticket": ticket, "observed_sl": spec["sl"],
                           "required_stop": required, "status": status}
                    application["tickets"].append(row)
                    if status == "observed_protected":
                        stop_desired[ticket] = strongest(required, spec["sl"],
                                                        stop_desired.get(ticket, required))
                    elif status == "request":
                        actions = [*client.queue, *([client.active] if client.active else [])]
                        pending = [a.payload[0] for a in actions if a.operation == "modify"
                                   and a.ticket == ticket and a.payload[0] not in (None, 0.)]
                        level = strongest([required, *pending])
                        client.enqueue(ClientAction("modify", ticket, broker_quote_index, now,
                            (level, None, "dubai_basket_stop", "preserve_tp", "preserve_tp")),
                            broker_quote_index, now)
                        stop_requested_levels[ticket] = level
                        stop_requested_at[ticket] = now / 1e9
                        stop_desired[ticket] = strongest(level, stop_desired.get(ticket, level))
                        row.update(status="queued", requested_stop=level)
                    row["desired_stop"] = stop_desired.get(ticket)
                # The live helper enqueues and returns; it does not wait for ACK.
                dispatch_client(broker_quote_index, now, allow_entry=False)
                application["status"] = "evaluated"
            except ProtectionBlocked as exc:
                application.update(status="blocked", blocker=str(exc))
            return application

        runtime_control = None
        if runtime_identity is not None:
            from .runtime_control import (
                NativeEntryCommand, NativeExecutionReceipt, NativeEntryResponse,
                NativeEntryReconciliation,
                NativeCloseCommand, NativeCloseResponse,
                NativeProtectionEffect, RuntimeBrokerAccess,
            )

            def runtime_ticket(ticket):
                if ticket in runtime_tickets:
                    return runtime_tickets[ticket]
                return runtime_identity.ticket_base + ticket_maps()[ticket]

            def runtime_profit(position, index):
                amount, exact = _money_minor(path, position.entry_price,
                    float(path.exit_quotes[index]), position.volume, index)
                return float(_minor_decimal(amount, path.currency_digits)) if exact else None

            def runtime_snapshot():
                index = broker_quote_index
                if index < 0 or not _tick_is_usable(path, index):
                    raise ProtectionBlocked("runtime_control_without_usable_quote")
                return {"quote_index": index, "time_ns": int(path.times_ns[index]),
                    "tick": {"bid": float(path.bid[index]), "ask": float(path.ask[index]),
                             "time_msc": int(path.times_ns[index]) // 1_000_000},
                    "symbol": {"name": runtime_identity.symbol, "point": protection.profile.point,
                               "digits": protection.profile.digits,
                               "trade_stops_level": protection.profile.stops_level_points,
                               "trade_freeze_level": protection.profile.freeze_level_points},
                    "positions": [{"ticket": runtime_ticket(p.ticket),
                                   "model_ticket": p.ticket, "symbol": runtime_identity.symbol,
                                   "magic": runtime_identity.magic, "type": 0 if path.direction == "BUY" else 1,
                                   "volume": p.volume, "price_open": p.entry_price,
                                   "comment": runtime_comments.get(p.ticket, ""),
                                   "profit": runtime_profit(p, index),
                                   "swap": float(_minor_decimal(p.accrued_swap_minor, path.currency_digits)),
                                   "sl": protection.states[p.ticket].sl or 0.,
                                   "tp": protection.states[p.ticket].tp or 0.}
                                  for p in positions],
                    "entries": [{**asdict(entry), "runtime_ticket": runtime_ticket(entry.ticket)}
                                for entry in entries]}

            def runtime_calc_profit(action, symbol, volume, price_open, price_close):
                """Declared linear hypothesis at the last processed quote, without book effects."""
                from research.broker_valuation import linear_profit_value

                if type(action) is not int or action not in (0, 1):
                    raise ValueError("profit action must be BUY=0 or SELL=1")
                if not isinstance(symbol, str) or not symbol or len(symbol) > 128:
                    raise ValueError("explicit profit symbol required")
                for value in (volume, price_open, price_close):
                    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                        raise ValueError("profit volume/prices must be finite and positive")
                index = broker_quote_index
                if symbol != runtime_identity.symbol or index < 0 or not _tick_is_usable(path, index):
                    return None
                if not math.isfinite(path.contract_size) or path.contract_size <= 0:
                    return None
                if path.conversion_orientation != "identity":
                    bid, ask = float(path.fx_bid[index]), float(path.fx_ask[index])
                    if not math.isfinite(bid) or not math.isfinite(ask) or bid <= 0 or ask < bid:
                        return None
                return linear_profit_value(path, {"action": action, "symbol": symbol,
                    "volume": volume, "price_open": price_open, "price_close": price_close}, index)

            history_enabled = runtime_identity.history_cost_profile is not None

            def record_runtime_deal(ticket, index, entry, price, volume, profit, swap, reason, *,
                                    order=None, comment=None):
                native_ticket = runtime_ticket(ticket)
                deal_id = len(runtime_deals) + 1
                runtime_deals.append({"ticket": deal_id, "order": native_ticket if order is None else order,
                    "position_id": native_ticket, "time_msc": int(path.times_ns[index]) // 1_000_000,
                    "symbol": runtime_identity.symbol, "magic": runtime_identity.magic,
                    "type": (0 if path.direction == "BUY" else 1) if entry == 0 else (1 if path.direction == "BUY" else 0),
                    "entry": entry, "volume": volume, "price": price, "profit": profit,
                    "commission": 0., "fee": 0., "swap": swap, "reason": reason,
                    "comment": runtime_comments.get(ticket, "") if comment is None else comment})
                return deal_id

            def sync_runtime_history():
                nonlocal runtime_exit_cursor
                if not history_enabled:
                    return
                for ordinal, row in enumerate(exits[runtime_exit_cursor:], runtime_exit_cursor):
                    gross, exact = _money_minor(path, row.entry_price, row.exit_price,
                                                row.volume, row.tick_index)
                    gross_money = _minor_decimal(gross, path.currency_digits) if exact else None
                    reason = (5 if row.reason in {"runtime_initial_tp", "runtime_native_tp"}
                              else 4 if row.reason in {"runtime_provisional_sl", "runtime_native_sl"} else 3)
                    record_runtime_deal(row.ticket, row.tick_index, 1, row.exit_price, row.volume,
                        float(gross_money) if gross_money is not None else None,
                        float(row.pnl_eur - gross_money) if row.pnl_eur is not None and gross_money is not None else None,
                        reason, order=2**31 + ordinal + 1, comment=runtime_exit_comments.get(ordinal))
                    stamp = int(path.times_ns[row.tick_index]) // 1_000_000
                    runtime_orders.append({"ticket": 2**31 + ordinal + 1,
                        "position_id": runtime_ticket(row.ticket),
                        "time_setup_msc": stamp, "time_done_msc": stamp,
                        "type": 1 if path.direction == "BUY" else 0, "state": 4,
                        "symbol": runtime_identity.symbol, "magic": runtime_identity.magic,
                        "comment": runtime_exit_comments.get(ordinal, ""),
                        "volume_initial": row.volume, "volume_current": 0.})
                runtime_exit_cursor = len(exits)

            def runtime_history(position):
                if type(position) is not int or position < 1:
                    raise ValueError("positive native position identity required")
                rows = [dict(row) for row in runtime_deals if row["position_id"] == position]
                if any(row["profit"] is None or row["swap"] is None for row in rows):
                    return None
                return rows

            def runtime_history_range(kind, date_from, date_to, group=None):
                if kind not in {"deals", "orders"} or group not in (None, runtime_identity.symbol):
                    raise ValueError("unsupported runtime history scope")

                def seconds(value):
                    if isinstance(value, datetime):
                        if value.tzinfo is None:
                            raise ValueError("runtime history requires an aware clock")
                        return int(value.astimezone(timezone.utc).timestamp())
                    if type(value) is not int:
                        raise ValueError("runtime history requires integer seconds")
                    return value

                lower, upper = seconds(date_from), seconds(date_to)
                if broker_quote_index < 0:
                    raise ValueError("runtime history requires an observed quote")
                current_upper = (int(path.times_ns[broker_quote_index]) // 1_000_000 + 999) // 1000
                if (lower < 0 or upper < lower or upper > 2**53 - 1
                        or upper - lower > MAX_HISTORY_SECONDS or upper > current_upper):
                    raise ValueError("runtime history window limit")
                source = runtime_deals if kind == "deals" else runtime_orders
                field = "time_msc" if kind == "deals" else "time_done_msc"
                rows = [dict(row) for row in source if lower * 1000 <= row[field] <= upper * 1000 + 999]
                if len(rows) > MAX_RECORDS:
                    raise ValueError("runtime history record limit")
                if kind == "deals" and any(row["profit"] is None or row["swap"] is None for row in rows):
                    return None
                return rows

            def apply_runtime_protection(effect):
                if not isinstance(effect, NativeProtectionEffect):
                    raise ValueError("typed native protection effect required")
                if not runtime_mutations_allowed:
                    raise ProtectionBlocked("runtime_control_not_active")
                index = broker_quote_index
                if (index < 0 or effect.quote_index != index or effect.time_ns != int(path.times_ns[index])
                        or not _tick_is_usable(path, index)):
                    raise ProtectionBlocked("runtime_protection_causal_clock_mismatch")
                position = next((p for p in positions
                    if runtime_ticket(p.ticket) == effect.ticket), None)
                if position is None:
                    return {"retcode": 10036, "comment": "position_already_closed"}
                sl = protection.rounded(effect.sl) if effect.sl else None
                tp = protection.rounded(effect.tp) if effect.tp else None
                accepted = protection.valid(float(path.exit_quotes[index]), sl, tp)
                request_id = protection.sequence + 1
                protection.emit(position.ticket, index, effect.time_ns, request_id,
                                "requested", sl, tp, "external_runtime_control")
                protection.sequence = request_id
                protection.emit(position.ticket, index, effect.time_ns, request_id,
                                "installed" if accepted else "rejected", sl, tp,
                                "accepted" if accepted else "invalid_stops")
                if accepted:
                    state = protection.states[position.ticket]
                    state.sl, state.tp = sl, tp
                    state.sl_reason, state.tp_reason = "runtime_native_sl", "runtime_native_tp"
                return {"retcode": 10009 if accepted else 10016,
                        "comment": "accepted" if accepted else "invalid_stops"}

            def require_runtime_clock(index, now):
                if not runtime_mutations_allowed:
                    raise ProtectionBlocked("runtime_control_not_active")
                if (broker_quote_index < 0 or index != broker_quote_index
                        or now != int(path.times_ns[index]) or not _tick_is_usable(path, index)):
                    raise ProtectionBlocked("runtime_entry_causal_clock_mismatch")

            def apply_runtime_entry(command):
                nonlocal last_tick_index, protection_blocked
                if not external_entries or not isinstance(command, NativeEntryCommand):
                    raise ValueError("typed command and external entry ownership required")
                require_runtime_clock(command.quote_index, command.time_ns)
                if not _transport.external_entry_open:
                    raise ProtectionBlocked("runtime_entry_control_closed")
                index, now = command.quote_index, command.time_ns
                sl = protection.rounded(command.sl) if command.sl else None
                tp = protection.rounded(command.tp) if command.tp else None
                rejection, retcode = None, 10009
                if (command.symbol != runtime_identity.symbol or command.magic != runtime_identity.magic
                        or command.direction != path.direction):
                    rejection, retcode = "entry_identity_mismatch", 10013
                elif not market.valid_volume(command.volume):
                    rejection, retcode = "invalid_volume", 10014
                elif not protection.valid(float(path.exit_quotes[index]), sl, tp):
                    rejection, retcode = "invalid_initial_protection", 10016
                try:
                    if len(market.events) + 2 > market.profile.max_events:
                        raise ProtectionBlocked("market_event_budget_exhausted")
                    if rejection is None:
                        if len(protection.events) + 1 > protection.profile.max_events:
                            raise ProtectionBlocked("protection_event_budget_exhausted")
                        # Reserve both fill and settlement samples before creating exposure.
                        _transport.reserve_risk(2)
                except ProtectionBlocked as exc:
                    blockers.append(str(exc))
                    protection_blocked = True
                    raise
                execution_id = len(runtime_receipts) + 1
                ticket = f"runtime_{execution_id}"
                native_ticket = runtime_identity.ticket_base + execution_id
                market.request(ticket, index, now, command.requested_price, command.volume,
                               "external_runtime_entry", entry=True, response_owner="external_runtime")
                last_tick_index = index
                if rejection is not None:
                    market.finish(ticket, index, now, None, entry=True, rejection=rejection)
                    receipt = NativeExecutionReceipt(execution_id, command.request_id, retcode, rejection)
                else:
                    price = _adverse_entry_price(path.direction, _entry_quote(path, index), execution)
                    protection.open(ticket, index, now, sl, tp, "external_runtime_entry")
                    state = protection.states[ticket]
                    state.sl_reason, state.tp_reason = "runtime_provisional_sl", "runtime_initial_tp"
                    item = _ScheduledEntry(index, _Position(ticket, "market", len(entries),
                        command.volume, price, now, index, (), ()), "external_runtime_entry",
                        requested_ns=now, request_index=index, price_tick_index=index)
                    register_fill(item, index)
                    runtime_tickets[ticket] = native_ticket
                    runtime_comments[ticket] = command.comment
                    market.finish(ticket, index, now, price, entry=True)
                    observe_equity(index, now)
                    for phase in ("entry_fill", "settled"):
                        runtime_risk.append({**asdict(risk_snapshot(index, phase)),
                                             "tick_index": index, "time_ns": now})
                    deal_id = (record_runtime_deal(ticket, index, 0, price, command.volume, 0., 0., 3)
                               if history_enabled else 0)
                    if history_enabled:
                        stamp = now // 1_000_000
                        runtime_orders.append({"ticket": native_ticket,
                            "position_id": native_ticket,
                            "time_setup_msc": stamp, "time_done_msc": stamp,
                            "type": 0 if command.direction == "BUY" else 1, "state": 4,
                            "symbol": runtime_identity.symbol, "magic": runtime_identity.magic,
                            "comment": command.comment, "volume_initial": command.volume,
                            "volume_current": 0.})
                    receipt = NativeExecutionReceipt(execution_id, command.request_id, retcode, "accepted",
                                                 native_ticket, price, command.volume, deal_id)
                runtime_receipts[execution_id] = (ticket, receipt)
                return receipt

            def observe_entry_response(response):
                if not external_entries or not isinstance(response, NativeEntryResponse):
                    raise ValueError("typed external entry response required")
                require_runtime_clock(response.quote_index, response.time_ns)
                if response.execution_id not in runtime_receipts:
                    raise ValueError("unknown external entry response")
                ticket, receipt = runtime_receipts[response.execution_id]
                observed = market.acknowledge_external(ticket, response.quote_index, response.time_ns)
                if observed and receipt.retcode == 10009:
                    for offset, entry in enumerate(entries):
                        if entry.ticket == ticket:
                            entries[offset] = replace(entry, acknowledged_ns=response.time_ns)
                            break
                return observed

            def observe_entry_reconciliation(observation):
                if not external_entries or not isinstance(observation, NativeEntryReconciliation):
                    raise ValueError("typed historical entry observation required")
                require_runtime_clock(observation.quote_index, observation.time_ns)
                matches = [(ticket, receipt) for ticket, receipt in runtime_receipts.values()
                           if receipt.request_id == observation.request_id]
                if len(matches) != 1:
                    raise ValueError("historical entry request must identify one native effect")
                ticket, receipt = matches[0]
                if (receipt.retcode != 10009 or receipt.ticket != observation.order
                        or receipt.deal_id != observation.deal
                        or not math.isclose(receipt.volume, observation.volume, rel_tol=0., abs_tol=1e-9)
                        or not math.isclose(receipt.price, observation.price, rel_tol=0., abs_tol=1e-9)
                        or observation.symbol != runtime_identity.symbol
                        or observation.magic != runtime_identity.magic
                        or observation.direction != path.direction
                        or observation.comment != runtime_comments[ticket]):
                    raise ValueError("historical entry does not match the native effect")
                return market.reconcile_external(ticket, observation.quote_index, observation.time_ns)

            def drain_runtime_risk():
                result = tuple(runtime_risk)
                runtime_risk.clear()
                return result

            def apply_runtime_close(command):
                nonlocal realized_minor, money_unknown, last_tick_index, protection_blocked
                if not external_entries or not isinstance(command, NativeCloseCommand):
                    raise ValueError("typed command and external execution ownership required")
                require_runtime_clock(command.quote_index, command.time_ns)
                index, now = command.quote_index, command.time_ns
                position = next((p for p in positions if runtime_ticket(p.ticket) == command.position), None)
                reason, retcode = None, 10009
                if command.symbol != runtime_identity.symbol or command.magic != runtime_identity.magic:
                    reason, retcode = "close_identity_mismatch", 10013
                elif command.direction == path.direction:
                    reason, retcode = "close_direction_mismatch", 10013
                elif position is None:
                    reason, retcode = "position_already_closed", 10036
                elif not market.valid_volume(command.volume) or command.volume > position.volume + 1e-12:
                    reason, retcode = "invalid_close_volume", 10038
                whole = position is not None and math.isclose(command.volume, position.volume, abs_tol=1e-12)
                try:
                    if len(market.events) + 2 > market.profile.max_events:
                        raise ProtectionBlocked("market_event_budget_exhausted")
                    if reason is None:
                        if whole and len(protection.events) + 1 > protection.profile.max_events:
                            raise ProtectionBlocked("protection_event_budget_exhausted")
                        _transport.reserve_risk(2)
                except ProtectionBlocked as exc:
                    blockers.append(str(exc))
                    protection_blocked = True
                    raise
                execution_id = len(runtime_close_receipts) + 1
                ticket = position.ticket if position is not None else f"missing_native_{command.position}"
                request_id = market.request_external_close(ticket, index, now, command.requested_price,
                                                           command.volume, "external_runtime_close")
                last_tick_index = index
                if reason is not None:
                    market.finish_external_close(request_id, index, now, None, rejection=reason)
                    receipt = NativeExecutionReceipt(execution_id, command.request_id, retcode, reason)
                else:
                    price = _adverse_exit_price(path, index, execution)
                    ordinal = len(exits)
                    if whole:
                        protection.close(ticket, index, now, "runtime_market_close")
                    realized_minor, money_unknown = _close_position(path, position, command.volume,
                        positions, exits, index, "runtime_market_close", execution,
                        realized_minor, money_unknown, blockers, exit_price=price)
                    runtime_exit_comments[ordinal] = command.comment
                    sync_runtime_history()
                    market.finish_external_close(request_id, index, now, price)
                    observe_equity(index, now)
                    for phase in ("market_exit", "settled"):
                        runtime_risk.append({**asdict(risk_snapshot(index, phase)),
                                             "tick_index": index, "time_ns": now})
                    receipt = NativeExecutionReceipt(execution_id, command.request_id, retcode, "accepted",
                        2**31 + ordinal + 1, price, command.volume,
                        runtime_deals[-1]["ticket"] if history_enabled else 0)
                runtime_close_receipts[execution_id] = (request_id, receipt)
                return receipt

            def observe_close_response(response):
                if not external_entries or not isinstance(response, NativeCloseResponse):
                    raise ValueError("typed external close response required")
                require_runtime_clock(response.quote_index, response.time_ns)
                if response.execution_id not in runtime_close_receipts:
                    raise ValueError("unknown external close response")
                request_id, _ = runtime_close_receipts[response.execution_id]
                return market.acknowledge_external_close(request_id, response.quote_index, response.time_ns)

            runtime_control = RuntimeBrokerAccess(runtime_snapshot, apply_runtime_protection,
                apply_runtime_entry, observe_entry_response, drain_runtime_risk,
                runtime_history if history_enabled else None, apply_runtime_close, observe_close_response,
                runtime_calc_profit if runtime_identity.valuation_profile is not None else None,
                runtime_history_range if history_enabled else None,
                observe_entry_reconciliation)

        _transport.bind_broker(_ReplayBrokerAccess(read_broker, known_tickets, advance_responses,
            next_response_ns, monitor_ready, observe_summary, apply_summary_decision,
            lambda: terminal_close is not None,
            resume_monitor, apply_stop_plan, runtime_control))

    if _transport is not None and callable(getattr(_transport, "bind_prefix", None)):
        _transport.bind_prefix(lambda: {
            "signal_id": path.signal_id,
            "strategy_fingerprint": genome.fingerprint,
            "entries": tuple(entries), "exits": tuple(exits),
            "blockers": tuple(blockers), "last_tick_index": last_tick_index,
            "market_events": tuple(market.events) if market else (),
            "client_events": tuple(client.events) if client else (),
        })

    for index in range(len(path.times_ns)):
        now_ns = int(path.times_ns[index])
        yield _ReplayBoundary(index, now_ns)
        new_quote = True
        while True:
            try:
                continuing = process_quote(index, now_ns, new_quote=new_quote)
                if external_entries and _transport.external_entry_open and not blockers:
                    continuing = True
                if _transport is not None:
                    capture_risk(index, now_ns, "settled")
            except ProtectionBlocked as exc:
                if _transport is None:
                    raise
                blockers.append(str(exc))
                protection_blocked = True
                continuing = False
            if sync_runtime_history is not None:
                sync_runtime_history()
            if _transport is None:
                break
            last = risk_samples[-1] if risk_samples else None
            runtime_mutations_allowed = True
            try:
                command = yield _ReplaySettlement(
                    index, now_ns, continuing, last.realized_minor if last else None,
                    last.floating_minor if last else None, last.positions if last else (),
                    tuple(risk_samples), tuple(dict.fromkeys(blockers)),
                )
            finally:
                runtime_mutations_allowed = False
            if command is None:
                break
            if not isinstance(command, _ReplayRepeat) or command.tick_index != index or not continuing:
                raise ValueError("invalid shared replay continuation")
            new_quote = False
        if not continuing:
            break

    if positions and not rollover_blocked and not protection_blocked:
        if protection:
            money_unknown = True
            exit_reason = "not_closed"
        elif last_tick_index >= 0 and _tick_is_usable(path, last_tick_index):
            realized_minor, money_unknown = _close_all(
                path,
                positions,
                exits,
                last_tick_index,
                "data_end",
                execution,
                realized_minor,
                money_unknown,
                blockers,
            )
            exit_reason = "data_end"
        blockers.append("path_ended_before_strategy_exit")

    if market:
        for request in market.entries.values():
            if request.processed_ns is not None:
                continue
            if client:
                blockers.append("entry_fill_quote_missing" if last_tick_index == len(path.times_ns) - 1
                                else "entry_request_in_flight_at_strategy_exit")
                continue
            fill_index = _entry_fill_index(path, request.index, execution)
            if fill_index is None:
                blockers.append("entry_fill_quote_missing")
            elif last_tick_index < fill_index:
                blockers.append("entry_request_in_flight_at_strategy_exit")
    else:
        if _entry_request_in_flight(scheduled, schedule_cursor, path, last_tick_index):
            blockers.append("entry_request_in_flight_at_strategy_exit")
        if pending_request_indices and last_tick_index >= pending_request_indices[0]:
            blockers.append("entry_fill_quote_missing")
    if market and market.pending and not protection_blocked:
        blockers.append("market_lifecycle_incomplete_at_data_end")
        money_unknown = True
    if client and client.pending:
        blockers.append("client_lifecycle_incomplete_at_data_end")
        money_unknown = True
    if read_management and _transport.monitor_busy:
        blockers.append("management_read_incomplete_at_data_end")
        money_unknown = True
    if external_entries and _transport.external_entry_open:
        blockers.append("runtime_entry_control_open_at_data_end")
        money_unknown = True
    blockers = list(dict.fromkeys(blockers))
    if protection_blocked:
        money_unknown = True
    if market:
        if positions:
            exit_reason = "not_closed"
        elif not entries and blockers:
            exit_reason = "blocked"
    pnl = None if money_unknown else _minor_decimal(
        realized_minor,
        path.currency_digits,
    )
    if client and not external_entries and not entries and not client.events and not blockers:
        return _empty_result(path, genome, blockers=(), confidence_layer=confidence_layer, unfilled=True)
    return SimulationResult(
        signal_id=path.signal_id,
        strategy_fingerprint=genome.fingerprint,
        confidence_layer=confidence_layer,
        entries=tuple(entries),
        exits=tuple(exits),
        pnl_eur=pnl,
        exit_reason=exit_reason,
        max_favourable_eur=(
            None
            if equity_incomplete or max_total_minor is None
            else _minor_decimal(max_total_minor, path.currency_digits)
        ),
        max_adverse_eur=(
            None
            if equity_incomplete or min_total_minor is None
            else _minor_decimal(min_total_minor, path.currency_digits)
        ),
        max_floating_drawdown_eur=(
            None
            if equity_incomplete or max_total_minor is None
            else _minor_decimal(max_drawdown_minor, path.currency_digits)
        ),
        max_favourable_move=_clean_price(max_favourable_move),
        max_adverse_move=_clean_price(max_adverse_move),
        blockers=tuple(blockers),
        last_tick_index=last_tick_index,
        unfilled=bool(market and not entries and not blockers),
        filled_volume=_clean_volume(sum(item.volume for item in entries)),
        protection_events=tuple(protection.events) if protection else (),
        market_events=tuple(market.events) if market else (),
        client_events=tuple(client.events) if client else (),
    )


def _open_protection(book, path, genome, execution, item, index, now_ns):
    position = item.position
    if genome.entry_mode == "actual_mt5":
        initial = next(row for row in book.profile.initial_protections if row.ticket == position.ticket)
        sl, tp, source = initial.sl, initial.tp, initial.source
        if sl != book.rounded(sl) or tp != book.rounded(tp):
            raise ProtectionBlocked(f"initial_protection_precision:{position.ticket}")
    else:
        if book.profile.request_quote_binding == "timestamp_and_ordinal":
            request_index = item.request_index
            if (request_index is None or not 0 <= request_index < len(path.times_ns)
                    or int(path.times_ns[request_index]) != item.requested_ns):
                raise ProtectionBlocked(f"protection_request_quote_ambiguous:{position.ticket}")
        else:
            request_index = (item.decision_index if item.decision_index is not None else
                             int(np.searchsorted(path.times_ns, item.requested_ns, side="left")))
            if item.decision_index is None and int(np.searchsorted(path.times_ns, item.requested_ns, side="right")) - request_index != 1:
                raise ProtectionBlocked(f"protection_request_quote_ambiguous:{position.ticket}")
        request_price = _entry_quote(path, request_index) + _direction_sign(path.direction) * execution.spread_addition / 2
        candidates = []
        if genome.trailing_distance is not None:
            candidates.append(request_price - _direction_sign(path.direction) * float(genome.trailing_distance))
        if genome.stop_mode == "fixed_move":
            candidates.append(request_price - _direction_sign(path.direction) * float(genome.stop_value))
        sl = book.rounded((max(candidates) if path.direction == "BUY" else min(candidates)) if candidates else None)
        tp, source = None, "hypothetical_open_request"
        if genome.stop_mode == "fixed_level":
            sl = book.rounded(float(genome.stop_value))
            tp = book.rounded(float(genome.target_steps[position.leg_index]))
        if not book.valid(float(path.exit_quotes[index]), sl, tp):
            raise ProtectionBlocked(f"initial_protection_rejected_unmodeled:{position.ticket}")
    book.open(position.ticket, index, now_ns, sl, tp, source)


def _prepare_entries(
    path: DubaiPath,
    genome: StrategyGenome,
    execution: ExecutionAssumptions,
    *,
    pending_request_times: list[int],
    pending_request_indices: list[int] | None = None,
) -> tuple[list[_ScheduledEntry], str, list[str]]:
    if execution.entry_fill_latency_ms:
        if genome.entry_mode == "actual_mt5":
            return [], "counterfactual_entry", ["entry_fill_latency_requires_hypothetical_entries"]
        if genome.schema_version < 2 or genome.stop_mode == "provider":
            return [], "counterfactual_entry", ["entry_fill_latency_contract_unsupported"]
    if (
        genome.entry_mode == "actual_mt5"
        and path.entry_evidence_kind != "actual_mt5"
    ):
        return [], "counterfactual_entry", [
            "actual_entry_evidence_missing"
        ]
    if genome.entry_ladder_mode != "simultaneous":
        return _prepare_ladder_entries(path, genome, execution, pending_request_times=pending_request_times,
                                      pending_request_indices=pending_request_indices)

    if genome.entry_mode == "actual_mt5":
        first_opened_ns = min(_datetime_ns(leg.opened_at) for leg in path.legs)
        context_index = int(np.searchsorted(
            path.times_ns,
            first_opened_ns,
            side="left",
        ))
        if context_index >= len(path.times_ns):
            return [], "counterfactual_entry", ["missing_tick_for_context_filter"]
        if not _context_allows(path, genome, context_index, execution):
            return [], "counterfactual_entry", []
        scheduled: list[_ScheduledEntry] = []
        exact_shape = genome.leg_count == len(path.legs)
        exact_volume = exact_shape and all(
            math.isclose(weight, leg.volume, abs_tol=1e-12)
            for weight, leg in zip(genome.volume_weights, path.legs, strict=True)
        )
        for index in range(genome.leg_count):
            template = path.legs[min(index, len(path.legs) - 1)]
            opened_ns = _datetime_ns(template.opened_at)
            tick_index = int(np.searchsorted(path.times_ns, opened_ns, side="left"))
            if tick_index >= len(path.times_ns):
                return [], "counterfactual_entry", [
                    f"missing_tick_for_entry:{template.ticket}"
                ]
            if index < len(path.legs):
                base_price = template.open_price
                source = "observed_mt5_fill"
                ticket = template.ticket
            else:
                if not _tick_is_usable(path, tick_index):
                    return [], "counterfactual_entry", [
                        f"invalid_tick_for_extra_entry:{tick_index}"
                    ]
                base_price = _entry_quote(path, tick_index)
                source = "counterfactual_extra_leg"
                ticket = f"sim_extra_{index + 1}"
            entry_price = _adverse_entry_price(
                path.direction,
                base_price,
                execution,
            )
            scheduled.append(_ScheduledEntry(
                tick_index=tick_index,
                source=source,
                position=_Position(
                    ticket=ticket,
                    role=template.role,
                    leg_index=index,
                    volume=float(genome.volume_weights[index]),
                    entry_price=entry_price,
                    opened_ns=opened_ns,
                    opened_index=tick_index,
                    tp_events=template.tp_events,
                    sl_events=template.sl_events,
                    trailing_stop=_initial_trailing_stop(
                        path.direction,
                        entry_price,
                        genome.trailing_distance,
                    ),
                ),
            ))
        return (
            sorted(scheduled, key=lambda item: (item.tick_index, item.position.ticket)),
            "observed_entry_management" if exact_volume else "counterfactual_entry",
            [],
        )

    entry_index = _causal_entry_index(path, genome, execution)
    if entry_index is None:
        return [], "counterfactual_entry", []
    if not _context_allows(path, genome, entry_index, execution):
        return [], "counterfactual_entry", []
    requested_ns = int(path.times_ns[entry_index])
    request_index = entry_index
    provider_close_ns = _provider_entry_cancellation_ns(path, genome, execution)
    if provider_close_ns is not None and provider_close_ns <= requested_ns:
        return [], "counterfactual_entry", []
    entry_index = _entry_fill_index(path, entry_index, execution)
    if entry_index is None:
        if execution.market:
            pending_request_times.append(requested_ns)
            if pending_request_indices is not None:
                pending_request_indices.append(request_index)
            return [], "counterfactual_entry", []
        return [], "counterfactual_entry", ["entry_fill_quote_missing"]
    entry_ns = int(path.times_ns[entry_index])
    if not execution.market and provider_close_ns is not None and requested_ns < provider_close_ns < entry_ns:
        return [], "counterfactual_entry", ["provider_close_during_first_entry_execution_unmodeled"]
    base_price = _entry_quote(path, entry_index)
    entry_price = _adverse_entry_price(path.direction, base_price, execution)
    if (
        genome.stop_mode == "provider"
        and _provider_stop_invalidated_before_entry(
            path,
            path.legs[0].sl_events,
            entry_index,
            execution.latency_ms * 1_000_000,
        )
    ):
        return [], "counterfactual_entry", []
    scheduled = []
    for index, volume in enumerate(genome.volume_weights):
        template = path.legs[min(index, len(path.legs) - 1)]
        scheduled.append(_ScheduledEntry(
            tick_index=entry_index,
            source=f"causal_{genome.entry_mode}",
            requested_ns=requested_ns,
            request_index=request_index,
            acknowledged_index=_entry_ack_index(path, entry_index, execution),
            position=_Position(
                ticket=f"sim_{index + 1}",
                role=template.role,
                leg_index=index,
                volume=float(volume),
                entry_price=entry_price,
                opened_ns=entry_ns,
                opened_index=entry_index,
                tp_events=template.tp_events,
                sl_events=template.sl_events,
                trailing_stop=_initial_trailing_stop(
                    path.direction,
                    entry_price,
                    genome.trailing_distance,
                ),
            ),
        ))
    return scheduled, "counterfactual_entry", []


def _prepare_ladder_entries(
    path: DubaiPath,
    genome: StrategyGenome,
    execution: ExecutionAssumptions,
    *,
    pending_request_times: list[int],
    pending_request_indices: list[int] | None = None,
) -> tuple[list[_ScheduledEntry], str, list[str]]:
    requested_ns = None
    request_index = None
    provider_close_ns = _provider_entry_cancellation_ns(path, genome, execution)
    if genome.entry_mode == "actual_mt5":
        template = path.legs[0]
        base_index = int(np.searchsorted(
            path.times_ns,
            _datetime_ns(template.opened_at),
            side="left",
        ))
        if base_index >= len(path.times_ns):
            return [], "counterfactual_entry", [
                f"missing_tick_for_entry:{template.ticket}"
            ]
        if not _context_allows(path, genome, base_index, execution):
            return [], "counterfactual_entry", []
        base_ns = _datetime_ns(template.opened_at)
        reference_price = template.open_price
        first_price = _adverse_entry_price(
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
        base_index = _causal_entry_index(path, genome, execution)
        if base_index is None or not _context_allows(
            path,
            genome,
            base_index,
            execution,
        ):
            return [], "counterfactual_entry", []
        requested_ns = int(path.times_ns[base_index])
        request_index = base_index
        if provider_close_ns is not None and provider_close_ns <= requested_ns:
            return [], "counterfactual_entry", []
        base_index = _entry_fill_index(path, base_index, execution)
        if base_index is None:
            if execution.market:
                pending_request_times.append(requested_ns)
                if pending_request_indices is not None:
                    pending_request_indices.append(request_index)
                return [], "counterfactual_entry", []
            return [], "counterfactual_entry", ["entry_fill_quote_missing"]
        base_ns = int(path.times_ns[base_index])
        if not execution.market and provider_close_ns is not None and requested_ns < provider_close_ns < base_ns:
            return [], "counterfactual_entry", ["provider_close_during_first_entry_execution_unmodeled"]
        reference_price = _entry_quote(path, base_index)
        first_price = _adverse_entry_price(
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
            return [], "counterfactual_entry", []

    first_template = path.legs[0]
    scheduled = [_ScheduledEntry(
        tick_index=base_index,
        source=first_source,
        requested_ns=requested_ns,
        request_index=request_index,
        acknowledged_index=_entry_ack_index(path, base_index, execution),
        position=_Position(
            ticket=first_ticket,
            role=first_template.role,
            leg_index=0,
            volume=float(genome.volume_weights[0]),
            entry_price=first_price,
            opened_ns=base_ns,
            opened_index=base_index,
            tp_events=first_template.tp_events,
            sl_events=first_template.sl_events,
            trailing_stop=_initial_trailing_stop(
                path.direction,
                first_price,
                genome.trailing_distance,
            ),
        ),
    )]
    direction = _direction_sign(path.direction)
    ladder_sign = -1.0 if genome.entry_ladder_mode == "adverse" else 1.0
    step = float(genome.entry_ladder_step or 0)
    cursor = base_index
    if execution.market:
        cursor = _entry_ack_index(path, base_index, execution)
        if cursor is None:
            return scheduled, "counterfactual_entry", []
        cursor += 1
    end_index = int(np.searchsorted(
        path.times_ns,
        expiry_ns,
        side="left" if genome.schema_version >= 2 else "right",
    ))
    if genome.stop_mode == "fixed_level":
        end_index = min(end_index, _absolute_cancel_index(path, genome))
    quotes = path.ask if path.direction == "BUY" else path.bid
    for leg_index in range(1, genome.leg_count):
        usable = _usable_tick_mask(path, cursor, end_index)
        distance = direction * (
            quotes[cursor:end_index] - reference_price
        ) * ladder_sign
        if genome.entry_ladder_mode == "range_levels":
            lo, hi = float(genome.entry_value), float(genome.entry_confirmation_value)
            level = (lo + hi) / 2 if leg_index == 1 else lo if direction == 1 else hi
            matches = np.flatnonzero(usable & (direction * (quotes[cursor:end_index] - level) <= 0))
        else:
            matches = np.flatnonzero(usable & (distance >= step * leg_index))
        if not len(matches):
            break
        matched_index = cursor + int(matches[0])
        requested_ns = int(path.times_ns[matched_index])
        request_index = matched_index
        if provider_close_ns is not None and provider_close_ns <= requested_ns:
            break
        matched_index = _entry_fill_index(path, matched_index, execution)
        if matched_index is None:
            # Diagnose this request only if replay actually reaches it.
            pending_request_times.append(requested_ns)
            if pending_request_indices is not None:
                pending_request_indices.append(request_index)
            break
        next_cursor = _entry_ack_index(path, matched_index, execution) if execution.market else matched_index
        cursor = len(path.times_ns) if next_cursor is None else next_cursor + int(execution.market is not None)
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
        opened_ns = int(path.times_ns[matched_index])
        scheduled.append(_ScheduledEntry(
            tick_index=matched_index,
            source=f"counterfactual_{genome.entry_ladder_mode}_ladder",
            requested_ns=requested_ns,
            request_index=request_index,
            acknowledged_index=_entry_ack_index(path, matched_index, execution),
            position=_Position(
                ticket=f"sim_ladder_{leg_index + 1}",
                role=template.role,
                leg_index=leg_index,
                volume=float(genome.volume_weights[leg_index]),
                entry_price=(entry_price := _adverse_entry_price(
                    path.direction,
                    _entry_quote(path, matched_index),
                    execution,
                )),
                opened_ns=opened_ns,
                opened_index=matched_index,
                tp_events=template.tp_events,
                sl_events=template.sl_events,
                trailing_stop=_initial_trailing_stop(
                    path.direction,
                    entry_price,
                    genome.trailing_distance,
                ),
            ),
        ))
    return scheduled, "counterfactual_entry", []


def _entry_ack_index(path, filled_index, execution):
    if execution.market is None:
        return None
    earliest = int(path.times_ns[filled_index]) + execution.market.entry_acknowledgement_delay_ms * 1_000_000
    start = max(filled_index, int(np.searchsorted(path.times_ns, earliest, side="left")))
    for index in range(start, len(path.times_ns)):
        if _tick_is_usable(path, index):
            return index
    return None


def _entry_fill_index(path, requested_index, execution):
    """A hypothesis: first executable tape quote after order processing delay.

    Observation delay and fill delay are separate. This does not claim broker
    acceptance, polling frequency or an empirically calibrated fill model.
    """
    if not execution.entry_fill_latency_ms:
        return requested_index
    earliest = int(path.times_ns[requested_index]) + execution.entry_fill_latency_ms * 1_000_000
    start = int(np.searchsorted(path.times_ns, earliest, side="left"))
    for index in range(start, len(path.times_ns)):
        if _tick_is_usable(path, index):
            return index
    return None


def _provider_entry_cancellation_ns(path, genome, execution):
    times = [_datetime_ns(event.observed_at) + execution.latency_ms * 1_000_000
             for event in path.provider_events
             if _is_provider_close(event.action, genome.provider_management_mode)]
    return min(times) if times else None


def _entry_request_in_flight(scheduled, filled_count, path, last_index):
    if last_index < 0:
        return False
    now_ns = int(path.times_ns[last_index])
    return any(item.requested_ns is not None and item.requested_ns <= now_ns < item.position.opened_ns
               for item in scheduled[filled_count:])


def _absolute_cancel_index(path, genome):
    sign = _direction_sign(path.direction)
    target = min(genome.target_steps) if sign == 1 else max(genome.target_steps)
    hits = np.flatnonzero((sign * (path.exit_quotes - target) >= 0)
                         | (sign * (path.exit_quotes - float(genome.stop_value)) <= 0))
    return int(hits[0]) if len(hits) else len(path.times_ns)


def _causal_entry_index(
    path: DubaiPath,
    genome: StrategyGenome,
    execution: ExecutionAssumptions,
) -> int | None:
    signal_ns = _datetime_ns(path.signal_observed_at)
    latency_ns = execution.latency_ms * 1_000_000
    start_ns = signal_ns + latency_ns
    start_index = int(np.searchsorted(path.times_ns, start_ns, side="left"))
    if start_index >= len(path.times_ns):
        return None
    expiry_ns = (
        _entry_expiry_anchor_ns(path)
        + genome.entry_expiry_min * 60 * 1_000_000_000
    )

    if genome.entry_mode == "no_entry":
        return None

    if genome.stop_mode == "fixed_level":
        end_index = min(int(np.searchsorted(path.times_ns, expiry_ns, side="left")), _absolute_cancel_index(path, genome))
        usable = _usable_tick_mask(path, start_index, end_index)
        quotes = path.ask[start_index:end_index] if path.direction == "BUY" else path.bid[start_index:end_index]
        if genome.entry_mode == "published_range":
            usable = usable & (quotes >= float(genome.entry_value)) & (quotes <= float(genome.entry_confirmation_value))
        elif genome.entry_mode == "published_limit":
            usable = usable & (_direction_sign(path.direction) * (quotes - float(genome.entry_value)) <= 0)
        candidates = np.flatnonzero(usable)
        return start_index + int(candidates[0]) if len(candidates) else None

    if genome.entry_mode == "signal_market":
        end_index = int(np.searchsorted(path.times_ns, expiry_ns, side="left"))
        usable = _usable_tick_mask(path, start_index, end_index)
        candidates = np.flatnonzero(usable)
        return start_index + int(candidates[0]) if len(candidates) else None

    if genome.entry_mode == "adverse_reversal":
        end_index = int(np.searchsorted(path.times_ns, expiry_ns, side="left"))
        usable = _usable_tick_mask(path, start_index, end_index)
        candidates = np.flatnonzero(usable)
        if not len(candidates):
            return None
        reference_index = start_index + int(candidates[0])
        reference = _entry_quote(path, _reference_quote_index(path, start_ns, reference_index, execution))
        adverse = float(genome.entry_value)
        reversal = float(genome.entry_confirmation_value)
        armed = False
        extreme = reference
        for index in range(reference_index, end_index):
            if not _tick_is_usable(path, index):
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

    if genome.entry_mode == "delay":
        target_ns = start_ns + int(float(genome.entry_value) * 1_000_000_000)
        index = int(np.searchsorted(path.times_ns, target_ns, side="left"))
        if genome.schema_version >= 2:
            end = int(np.searchsorted(path.times_ns, expiry_ns, side="left"))
            matches = np.flatnonzero(_usable_tick_mask(path, index, end))
            return index + int(matches[0]) if len(matches) else None
        return index if index < len(path.times_ns) and path.times_ns[index] <= expiry_ns else None

    if not _tick_is_usable(path, start_index):
        return None
    reference = _entry_quote(path, _reference_quote_index(path, start_ns, start_index, execution))
    distance = float(genome.entry_value)
    end_index = int(np.searchsorted(path.times_ns, expiry_ns,
                                   side="left" if genome.schema_version >= 2 else "right"))
    quotes = path.ask[start_index:end_index] if path.direction == "BUY" else path.bid[start_index:end_index]
    usable = _usable_tick_mask(path, start_index, end_index)
    if genome.entry_mode == "pullback":
        matched = quotes <= reference - distance if path.direction == "BUY" else quotes >= reference + distance
    elif genome.entry_mode == "momentum":
        matched = quotes >= reference + distance if path.direction == "BUY" else quotes <= reference - distance
    else:
        return None
    candidates = np.flatnonzero(usable & matched)
    return start_index + int(candidates[0]) if len(candidates) else None


def _reference_quote_index(path: DubaiPath, start_ns: int, default_index: int,
                           execution: ExecutionAssumptions) -> int:
    """Tick whose quote the client holds when it first handles the signal."""
    lag_ns = execution.quote_view_lag_ms * 1_000_000
    if not lag_ns:
        return default_index
    index = int(np.searchsorted(path.times_ns, start_ns - lag_ns, side="right")) - 1
    while index >= 0 and not _tick_is_usable(path, index):
        index -= 1
    return index if index >= 0 else default_index


def _usable_tick_mask(path: DubaiPath, start: int, end: int) -> np.ndarray:
    bid = path.bid[start:end]
    ask = path.ask[start:end]
    return np.isfinite(bid) & np.isfinite(ask) & (bid > 0) & (ask >= bid)


def _context_allows(
    path: DubaiPath,
    genome: StrategyGenome,
    index: int,
    execution: ExecutionAssumptions,
) -> bool:
    mode = genome.context_filter_mode
    if mode == "none":
        return True
    value = float(genome.context_filter_value)
    if mode == "max_spread":
        return float(path.ask[index] - path.bid[index]) <= value
    if mode == "time_window":
        moment = _ns_datetime(int(path.times_ns[index]))
        hour = moment.hour + moment.minute / 60 + moment.second / 3600
        return hour <= value
    if mode == "max_volatility":
        lookback_ns = int(path.times_ns[index]) - 5 * 60 * 1_000_000_000
        start = int(np.searchsorted(path.times_ns, lookback_ns, side="left"))
        midpoint = (path.bid[start:index + 1] + path.ask[start:index + 1]) / 2
        return bool(len(midpoint)) and float(np.max(midpoint) - np.min(midpoint)) <= value
    if mode == "min_reward_risk":
        leg = path.legs[0]
        now_ns = int(path.times_ns[index])
        observation_latency_ns = execution.latency_ms * 1_000_000
        target = _latest_level(
            leg.tp_events,
            now_ns,
            include_be=True,
            observation_latency_ns=observation_latency_ns,
        )
        stop = _latest_level(
            leg.sl_events,
            now_ns,
            include_be=False,
            observation_latency_ns=observation_latency_ns,
        )
        if target is None or stop is None:
            return False
        entry = _entry_quote(path, index)
        reward = _direction_sign(path.direction) * (target - entry)
        risk = -_direction_sign(path.direction) * (stop - entry)
        return risk > 0 and reward / risk >= value
    return False


def _update_custom_be(
    position: _Position,
    genome: StrategyGenome,
    directional_move: float,
    now_ns: int,
) -> None:
    if genome.be_mode == "price" and directional_move >= float(genome.be_trigger):
        position.be_stop = position.entry_price
    elif genome.be_mode == "delayed":
        delay_ns = int(float(genome.be_trigger) * 60 * 1_000_000_000)
        if now_ns - position.opened_ns >= delay_ns:
            position.be_stop = position.entry_price
    elif (
        genome.be_mode == "partial"
        and directional_move >= float(genome.be_trigger)
        and position.role != "market_a"
    ):
        position.be_stop = position.entry_price


def _effective_stop(
    position: _Position,
    genome: StrategyGenome,
    now_ns: int,
    direction: str,
    observation_latency_ns: int,
) -> tuple[float | None, str]:
    base: float | None = None
    reason = "provider_sl"
    if genome.stop_mode == "provider":
        base = _latest_level(
            position.sl_events,
            now_ns,
            include_be=genome.be_mode == "provider",
            observation_latency_ns=observation_latency_ns,
        )
    elif genome.stop_mode == "fixed_move":
        base = position.entry_price - _direction_sign(direction) * float(
            genome.stop_value
        )
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
    if position.be_stop is not None:
        if base is None:
            return position.be_stop, position.be_reason
        tighter = (
            max(base, position.be_stop)
            if direction == "BUY"
            else min(base, position.be_stop)
        )
        if math.isclose(tighter, position.be_stop, abs_tol=1e-12):
            return tighter, position.be_reason
        return tighter, reason
    return base, reason


def _latest_level(
    events: Iterable[LevelEvent],
    now_ns: int,
    *,
    include_be: bool,
    observation_latency_ns: int = 0,
) -> float | None:
    latest: LevelEvent | None = None
    for event in events:
        if event.status not in {"confirmed", "snapshot"}:
            continue
        if not include_be and _looks_like_be(event.source):
            continue
        if _datetime_ns(event.observed_at) + observation_latency_ns <= now_ns:
            latest = event
        else:
            break
    return None if latest is None else latest.level


def _provider_stop_invalidated_before_entry(
    path: DubaiPath,
    events: Iterable[LevelEvent],
    entry_index: int,
    observation_latency_ns: int = 0,
) -> bool:
    """Return true when the provider thesis stopped out before a delayed fill."""

    entry_ns = int(path.times_ns[entry_index])
    signal_ns = _datetime_ns(path.signal_observed_at) + observation_latency_ns
    eligible = tuple(
        event
        for event in events
        if event.status in {"confirmed", "snapshot"}
        and not _looks_like_be(event.source)
        and _datetime_ns(event.observed_at) + observation_latency_ns <= entry_ns
    )
    for offset, event in enumerate(eligible):
        event_ns = _datetime_ns(event.observed_at) + observation_latency_ns
        active_from = max(signal_ns, event_ns)
        active_until = (
            min(
                entry_ns,
                _datetime_ns(eligible[offset + 1].observed_at)
                + observation_latency_ns,
            )
            if offset + 1 < len(eligible)
            else entry_ns
        )
        start = int(np.searchsorted(path.times_ns, active_from, side="left"))
        end = int(np.searchsorted(path.times_ns, active_until, side="left"))
        if offset + 1 >= len(eligible):
            end = entry_index + 1
        end = min(end, entry_index + 1)
        if start >= end:
            continue
        quotes = path.exit_quotes[start:end]
        if path.direction == "BUY":
            touched = bool(np.any(quotes <= float(event.level)))
        else:
            touched = bool(np.any(quotes >= float(event.level)))
        if touched:
            return True
    return False


def _selected_provider_target(
    path: DubaiPath,
    genome: StrategyGenome,
    now_ns: int,
    observation_latency_ns: int = 0,
) -> float | None:
    targets = [
        value
        for leg in path.legs
        if (
            value := _latest_level(
                leg.tp_events,
                now_ns,
                include_be=True,
                observation_latency_ns=observation_latency_ns,
            )
        )
        is not None
    ]
    if not targets:
        return None
    targets = sorted(set(targets), reverse=path.direction == "SELL")
    selected = max(1, int(round(float(genome.target_value)))) - 1
    return targets[min(selected, len(targets) - 1)]


def _apply_provider_protection(
    positions: Iterable[_Position],
    events: Iterable[ProviderEvent],
) -> None:
    for event in events:
        action = event.action.upper()
        if action == "MOVE_SL_TO_BE":
            for position in positions:
                position.be_stop = position.entry_price
                position.be_reason = "break_even"
        elif action == "MOVE_SL_TO_PRICE":
            level = _provider_announced_price(event)
            if level is None:
                continue
            for position in positions:
                position.be_stop = level
                position.be_reason = "provider_sl_move"


def _provider_announced_price(event: ProviderEvent) -> float | None:
    for key in ("price", "sl", "stop", "target_price"):
        value = event.payload.get(key)
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number) and number > 0:
            return number
    raw_text = str(event.payload.get("raw_text") or "")
    match = re.search(
        r"(?:MOVE\s+)?(?:SL|STOP(?:\s+LOSS)?)\s*(?:TO|AT|@)?\s*[:=]?\s*(\d+(?:[.,]\d+)?)",
        raw_text,
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
    path: DubaiPath,
    positions: list[_Position],
    exits: list[ExitRecord],
    index: int,
    reason: str,
    execution: ExecutionAssumptions,
    realized_minor: int,
    money_unknown: bool,
    blockers: list[str],
    *,
    exit_price: float | None = None,
) -> tuple[int, bool]:
    for position in list(positions):
        realized_minor, money_unknown = _close_position(
            path,
            position,
            position.volume,
            positions,
            exits,
            index,
            reason,
            execution,
            realized_minor,
            money_unknown,
            blockers,
            exit_price=exit_price,
        )
    return realized_minor, money_unknown


def _close_position(
    path: DubaiPath,
    position: _Position,
    volume: float,
    positions: list[_Position],
    exits: list[ExitRecord],
    index: int,
    reason: str,
    execution: ExecutionAssumptions,
    realized_minor: int,
    money_unknown: bool,
    blockers: list[str],
    *,
    exit_price: float | None = None,
) -> tuple[int, bool]:
    volume = min(position.volume, _clean_volume(volume))
    if exit_price is None:
        exit_price = _adverse_exit_price(path, index, execution)
    pnl_minor, exact = _money_minor(
        path,
        position.entry_price,
        exit_price,
        volume,
        index,
    )
    if not exact:
        blockers.append(f"stale_conversion_at_exit:{index}")
        money_unknown = True
    else:
        swap_minor = _allocate_position_swap(position, volume)
        pnl_minor += swap_minor
        realized_minor += pnl_minor
    exits.append(ExitRecord(
        ticket=position.ticket,
        tick_index=index,
        closed_at=_ns_datetime(int(path.times_ns[index])),
        entry_price=position.entry_price,
        exit_price=exit_price,
        volume=volume,
        pnl_eur=(
            _minor_decimal(pnl_minor, path.currency_digits) if exact else None
        ),
        reason=reason,
    ))
    position.volume = _clean_volume(position.volume - volume)
    if position.volume <= 1e-12:
        positions.remove(position)
    return realized_minor, money_unknown


def _basket_minor(
    path: DubaiPath,
    positions: Iterable[_Position],
    index: int,
    execution: ExecutionAssumptions,
) -> tuple[int, bool]:
    exit_price = _adverse_exit_price(path, index, execution)
    total = 0
    exact = True
    for position in positions:
        value, value_exact = _money_minor(
            path,
            position.entry_price,
            exit_price,
            position.volume,
            index,
        )
        total += value + position.accrued_swap_minor
        exact = exact and value_exact
    return total, exact


def _position_minor(
    path: DubaiPath,
    position: _Position,
    index: int,
    execution: ExecutionAssumptions,
) -> tuple[int, bool]:
    value, exact = _money_minor(
        path,
        position.entry_price,
        _adverse_exit_price(path, index, execution),
        position.volume,
        index,
    )
    return value + position.accrued_swap_minor, exact


def _volume_units(volume: float) -> int | None:
    scaled = Decimal(str(volume)) * Decimal(100)
    integral = scaled.to_integral_value(rounding=ROUND_HALF_UP)
    if abs(scaled - integral) > Decimal("0.00000001"):
        return None
    units = int(integral)
    return units if units >= 0 else None


def _allocate_position_swap(position: _Position, volume: float) -> int:
    if position.accrued_swap_minor == 0:
        return 0
    if math.isclose(volume, position.volume, abs_tol=1e-12):
        allocated = position.accrued_swap_minor
    else:
        allocated = _round_minor(
            Decimal(position.accrued_swap_minor)
            * Decimal(str(volume))
            / Decimal(str(position.volume)),
            0,
        )
    position.accrued_swap_minor -= allocated
    return allocated


def _money_minor(
    path: DubaiPath,
    entry_price: float,
    exit_price: float,
    volume: float,
    index: int,
) -> tuple[int, bool]:
    raw = Decimal(_direction_sign(path.direction)) * (
        Decimal(str(exit_price)) - Decimal(str(entry_price))
    )
    raw *= Decimal(str(path.contract_size)) * Decimal(str(volume))
    orientation = path.conversion_orientation
    exact = orientation == "identity" or bool(path.fx_valid[index])
    if orientation == "account_base_profit_quote":
        quote = Decimal(str(
            path.fx_ask[index] if raw >= 0 else path.fx_bid[index]
        ))
        raw = raw / quote
    elif orientation == "profit_base_account_quote":
        quote = Decimal(str(
            path.fx_bid[index] if raw >= 0 else path.fx_ask[index]
        ))
        raw = raw * quote
    elif orientation != "identity":
        return 0, False
    return _round_minor(raw, path.currency_digits), exact


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
    move_numerator = Decimal(_direction_sign(direction)) * (
        Decimal(str(raw_exit)) * total_volume - weighted_entry
    )
    return move_numerator >= Decimal(str(target)) * total_volume


def _initial_trailing_stop(
    direction: str,
    entry_price: float,
    trailing_distance: float | None,
) -> float | None:
    if trailing_distance is None:
        return None
    return _clean_price(
        entry_price - _direction_sign(direction) * float(trailing_distance)
    )


def _tighten_trailing_stops(
    positions: Iterable[_Position],
    direction: str,
    executable_exit: float,
    trailing_distance: float,
) -> None:
    candidate = _clean_price(
        executable_exit - _direction_sign(direction) * trailing_distance
    )
    for position in positions:
        if position.trailing_stop is None:
            position.trailing_stop = candidate
        elif direction == "BUY":
            position.trailing_stop = max(position.trailing_stop, candidate)
        else:
            position.trailing_stop = min(position.trailing_stop, candidate)


def _pending_entries_remain(
    genome: StrategyGenome,
    schedule_cursor: int,
    schedule_count: int,
) -> bool:
    return (
        genome.pending_entry_policy == "until_expiry"
        and schedule_cursor < schedule_count
    )


def _time_exit_applies(
    genome: StrategyGenome,
    total_minor: int,
    money_exact: bool,
) -> bool:
    if genome.schema_version == 1 or genome.time_exit_mode == "always":
        return True
    if not money_exact:
        return False
    if genome.time_exit_mode == "loss_only":
        return total_minor <= 0
    if genome.time_exit_mode == "profit_only":
        return total_minor > 0
    if genome.time_exit_mode == "non_negative":
        return total_minor >= 0
    return False


def _round_minor(value: float | Decimal, digits: int) -> int:
    scaled = Decimal(str(value)) * (Decimal(10) ** digits)
    return int(scaled.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _money_value_to_minor(value: float, digits: int) -> int:
    return _round_minor(value, digits)


def _minor_decimal(value: int, digits: int) -> Decimal:
    quantum = Decimal(1).scaleb(-digits)
    return Decimal(value).scaleb(-digits).quantize(quantum)


def _level_hit(direction: str, price: float, level: float, *, kind: str) -> bool:
    if kind == "target":
        return price >= level if direction == "BUY" else price <= level
    return price <= level if direction == "BUY" else price >= level


def _entry_quote(path: DubaiPath, index: int) -> float:
    return float(path.ask[index] if path.direction == "BUY" else path.bid[index])


def _entry_expiry_anchor_ns(path: DubaiPath) -> int:
    anchor = path.entry_expiry_anchor_at or path.signal_observed_at
    return _datetime_ns(anchor)


def _adverse_entry_price(
    direction: str,
    price: float,
    execution: ExecutionAssumptions,
) -> float:
    cost = execution.entry_slippage + execution.spread_addition
    return _clean_price(price + cost if direction == "BUY" else price - cost)


def _adverse_exit_price(
    path: DubaiPath,
    index: int,
    execution: ExecutionAssumptions,
) -> float:
    price = float(path.exit_quotes[index])
    cost = execution.exit_slippage + execution.spread_addition
    return _clean_price(price - cost if path.direction == "BUY" else price + cost)


def _target_exit_price(
    direction: str,
    target: float,
    execution: ExecutionAssumptions,
) -> float:
    cost = execution.exit_slippage + execution.spread_addition
    return _clean_price(
        float(target) - _direction_sign(direction) * cost
    )


def _direction_sign(direction: str) -> int:
    return 1 if direction == "BUY" else -1


def _is_provider_close(action: str, provider_management_mode: str = "exact") -> bool:
    return is_strategy_close_action(action, provider_management_mode)


def _looks_like_be(source: str) -> bool:
    normalized = str(source).upper()
    return "BE" in normalized or "BREAK EVEN" in normalized or "BREAKEVEN" in normalized


def _path_contract_blockers(path: DubaiPath) -> list[str]:
    lengths = {
        len(path.times_ns),
        len(path.bid),
        len(path.ask),
        len(path.exit_quotes),
        len(path.fx_bid),
        len(path.fx_ask),
        len(path.fx_age_ms),
        len(path.fx_valid),
    }
    blockers: list[str] = []
    if len(lengths) != 1 or not path.times_ns.size:
        blockers.append("invalid_path_lengths")
    elif np.any(np.diff(path.times_ns) < 0):
        blockers.append("non_monotonic_path_time")
    if path.direction not in {"BUY", "SELL"}:
        blockers.append("invalid_path_direction")
    if path.contract_size <= 0 or path.currency_digits < 0:
        blockers.append("invalid_path_money_contract")
    if not path.legs:
        blockers.append("path_without_entry_evidence")
    return blockers


def _tick_is_usable(path: DubaiPath, index: int) -> bool:
    bid = float(path.bid[index])
    ask = float(path.ask[index])
    return (
        math.isfinite(bid)
        and math.isfinite(ask)
        and bid > 0
        and ask >= bid
    )


def _empty_result(
    path: DubaiPath,
    genome: StrategyGenome,
    *,
    blockers: Iterable[str],
    confidence_layer: str = "unclassified",
    unfilled: bool = False,
) -> SimulationResult:
    zero = _minor_decimal(0, max(0, path.currency_digits))
    return SimulationResult(
        signal_id=path.signal_id,
        strategy_fingerprint=genome.fingerprint,
        confidence_layer=confidence_layer,
        entries=(),
        exits=(),
        pnl_eur=zero if not blockers else None,
        exit_reason="not_filled" if unfilled else "blocked",
        max_favourable_eur=zero if not blockers else None,
        max_adverse_eur=zero if not blockers else None,
        max_floating_drawdown_eur=zero if not blockers else None,
        max_favourable_move=0.0,
        max_adverse_move=0.0,
        blockers=tuple(dict.fromkeys(blockers)),
        last_tick_index=-1,
        unfilled=unfilled,
        filled_volume=0.0,
    )


def _datetime_ns(value: datetime) -> int:
    delta = value.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    # Float epoch seconds can move an exactly available event past its tick.
    return ((delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds) * 1000


def _ns_datetime(value: int) -> datetime:
    return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=int(value) // 1000)


def _clean_price(value: float) -> float:
    return round(float(value), 10)


def _clean_volume(value: float) -> float:
    return round(float(value), 10)
