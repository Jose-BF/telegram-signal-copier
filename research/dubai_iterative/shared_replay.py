"""Bounded scalar policy feedback through one quote-clock transport resource.

This diagnostic requires one common Bid/Ask tape. Preparation is immediate;
broker books own processing and response delays. Same-time causal rounds and
scope order are declared hypotheses, not a calibrated live execution model.
"""

from dataclasses import asdict, dataclass, replace

import numpy as np

from research.shared_transport import PassiveEvent, TransportJob, TransportSession
from research.risk_metrics import money_path_metrics
from research.management_observation import BasketReadCycle, BasketStopPlanReadCycle, ReadCycleProfile, ReadValue
from research.canonical_guard import CanonicalGuardComponent
from research.canonical_stop import DubaiBasketStopComponent
from basket_observation import basket_summary_reads
from basket_stop import basket_stop_plan_reads
from mt5_read_protocol import MAX_RESPONSE_BYTES, encode_message
from .client import ClientBook
from .contracts import StrategyGenome
from .dataset import ProviderEvent, SignalPath
from .engine import (
    ExecutionAssumptions, SimulationResult, _ReplayBoundary, _ReplayRepeat,
    _ReplaySettlement, _datetime_ns, _is_provider_close, _simulation_steps,
)
from .protection import ProtectionBlocked


@dataclass(frozen=True)
class SharedReplayProfile:
    account_currency: str
    name: str = "shared_quote_rounds_v1"
    capacity: int = 8
    request_timeout_ms: int = 60_000
    max_rounds_per_quote: int = 256
    max_events: int = 100_000
    max_quotes: int = 1_000_000
    max_read_cycles: int = 1000
    max_read_payload_bytes: int = 16_777_216
    read_symbol: str = "XAUUSD"

    def __post_init__(self):
        if self.name not in {"shared_quote_rounds_v1", "shared_interquote_reads_v2"} or self.account_currency != "EUR":
            raise ValueError("unsupported shared replay or account currency")
        for name, maximum in (("capacity", 128), ("request_timeout_ms", 86_400_000),
                              ("max_rounds_per_quote", 10_000), ("max_events", 1_000_000),
                              ("max_quotes", 1_000_000), ("max_read_cycles", 10_000),
                              ("max_read_payload_bytes", 268_435_456)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"invalid shared replay {name}")
        if not isinstance(self.read_symbol, str) or not 1 <= len(self.read_symbol) <= 128 or any(c in self.read_symbol for c in "*,!"):
            raise ValueError("invalid read symbol")


@dataclass(frozen=True)
class BasketReplaySpec:
    channel: str
    path: SignalPath
    genome: StrategyGenome
    execution: ExecutionAssumptions
    management_reads: ReadCycleProfile | None = None
    read_interval_ns: int = 1_000_000_000
    guard_component: CanonicalGuardComponent | None = None
    stop_component: DubaiBasketStopComponent | None = None

    def __post_init__(self):
        if self.channel not in {"canal1", "canal2"} or not isinstance(self.path, SignalPath):
            raise ValueError("typed channel/path required")
        if not self.path.signal_id.startswith(self.channel + "_"):
            raise ValueError("signal/channel identity mismatch")
        if not isinstance(self.genome, StrategyGenome) or not isinstance(self.execution, ExecutionAssumptions):
            raise ValueError("typed policy/execution required")
        if self.execution.client is None:
            raise ValueError("shared replay requires explicit client assumptions")
        if self.management_reads is not None and not isinstance(self.management_reads, ReadCycleProfile):
            raise ValueError("typed management read profile required")
        if type(self.read_interval_ns) is not int or not 1 <= self.read_interval_ns <= 86_400_000_000_000:
            raise ValueError("invalid management read interval")
        if self.guard_component is not None and (
                not isinstance(self.guard_component, CanonicalGuardComponent)
                or self.guard_component.channel != self.channel or self.management_reads is None):
            raise ValueError("canonical guard requires matching channel and declared management reads")
        if self.stop_component is not None and (
                not isinstance(self.stop_component, DubaiBasketStopComponent)
                or self.channel != "canal1" or self.management_reads is None
                or self.execution.protection is None):
            raise ValueError("Dubai stop requires canal1, protection and management read profiles")


@dataclass(frozen=True)
class SharedRiskPoint:
    channel: str
    signal_id: str
    tick_index: int
    time_ns: int
    causal_round: int
    realized_minor: int | None
    floating_minor: int | None
    positions: tuple[tuple[str, float, float], ...]
    phase: str = "settled"

    @property
    def equity_minor(self):
        if self.realized_minor is None or self.floating_minor is None:
            return None
        return self.realized_minor + self.floating_minor


@dataclass(frozen=True)
class SharedRiskFrame:
    tick_index: int
    time_ns: int
    states: tuple[tuple[str, str, SharedRiskPoint | None], ...]
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class SharedBasketPrefix:
    signal_id: str
    strategy_fingerprint: str
    entries: tuple
    exits: tuple
    blockers: tuple[str, ...]
    last_tick_index: int
    market_events: tuple
    client_events: tuple
    risk_point: SharedRiskPoint | None


@dataclass(frozen=True)
class SharedReplayResult:
    profile: SharedReplayProfile
    baskets: tuple[tuple[str, str, SimulationResult | None], ...]
    risk: tuple[SharedRiskPoint, ...]
    transport: dict
    blockers: tuple[str, ...]
    full_live_parity_verified: bool = False
    portfolio_admitted: bool = False
    risk_parity_verified: bool = False
    risk_grid: tuple[SharedRiskFrame, ...] = ()
    expected_quote_count: int = 0
    currency_digits: int = 2
    policies: tuple[tuple[str, StrategyGenome, ExecutionAssumptions], ...] = ()
    read_cycles: tuple[dict, ...] = ()
    management_profiles: tuple[dict, ...] = ()
    risk_streamed: bool = False
    risk_point_count: int = 0
    risk_grid_count: int = 0
    dynamic_provider_events: tuple[tuple[str, tuple[ProviderEvent, ...]], ...] = ()
    dynamic_basket_admissions: tuple[tuple[str, int], ...] = ()
    limitations: tuple[str, ...] = (
        "Common supplied quotes; shared read requests are not modeled.",
        "Preparation is immediate; native authorization is not modeled.",
        "Same-quote causal rounds and input scope order are explicit hypotheses.",
        "Caller continuation after a transport timeout is not live-validated.",
        "Risk phases are model states, not observed broker micro-timestamps.",
        "Legacy scalar summaries do not certify the new pre/post broker risk grid.",
        "Post-event grid measures selected initially-flat model scopes, not account equity.",
        "No portfolio, margin, strategy promotion or full live-parity certification.",
    )


@dataclass
class _RiskBudget:
    remaining: int

    def reserve(self):
        if self.remaining <= 0:
            raise ProtectionBlocked("shared_risk_event_budget_exhausted")
        self.remaining -= 1


class _ProviderEventFeed:
    def __init__(self, spec):
        self.spec = spec
        self.events = []
        self.close_ns = None
        self.direct_be_ns = None

    def admit(self, event, previous_ns, quote_ns):
        if not isinstance(event, ProviderEvent):
            raise ValueError("typed provider event required")
        observed_ns = _datetime_ns(event.observed_at)
        if (observed_ns >= quote_ns
                or previous_ns is not None and observed_ns <= previous_ns
                or observed_ns < _datetime_ns(self.spec.path.signal_observed_at)
                or self.events and observed_ns < _datetime_ns(self.events[-1].observed_at)):
            raise ValueError("provider event outside causal interquote window")
        self.events.append(event)
        effective_ns = observed_ns + self.spec.execution.latency_ms * 1_000_000
        if _is_provider_close(event.action, self.spec.genome.provider_management_mode):
            self.close_ns = effective_ns if self.close_ns is None else min(self.close_ns, effective_ns)
        if (self.spec.genome.stop_mode == "fixed_level"
                and self.spec.genome.be_mode == "provider"
                and event.action == "MOVE_SL_TO_BE"
                and event.payload.get("modality") == "direct"):
            self.direct_be_ns = (effective_ns if self.direct_be_ns is None
                                 else min(self.direct_be_ns, effective_ns))


class _SharedClientBook(ClientBook):
    def __init__(self, profile, port):
        super().__init__(profile)
        self.port = port

    def enqueue(self, action, index, now):
        if action.operation == "modify" and self.port.last_attempts.get((action.operation, action.ticket)) == (index, action.payload):
            return
        super().enqueue(action, index, now)

    def start(self, index, now):
        if self.active is not None or not self.queue:
            return None
        if not self.port.claim(self.queue[0], index, now):
            return None
        return super().start(index, now)

    def release(self, index, now):
        if self.active is not None:
            super().release(index, now)
            self.port.release(now)

    def cancel_entries(self, index, now):
        for action in list(self.queue):
            if action.operation == "entry":
                self.emit("cancelled", action, index, now)
                self.port.withdraw(action, now)
                self.queue.remove(action)


class _Port:
    def __init__(self, session, spec, ordinal, origin_ns, profile, owners, risk_budget,
                 provider_event_feed=None, admitted=True):
        self.session, self.spec, self.ordinal = session, spec, ordinal
        self.origin_ns, self.profile, self.owners = origin_ns, profile, owners
        self.client = None
        self.offered = {}
        self.sequence = 0
        self.passive_sequence = 0
        self.granted = None
        self.active = None
        self.last_entry_index = None
        self.last_attempts = {}
        self.risk_budget = risk_budget
        self.provider_event_feed = provider_event_feed
        self.admitted = admitted
        self.prefix_source = None
        self.interquote = profile.name == "shared_interquote_reads_v2"
        self.monitor_busy = False
        self.broker = None

    def bind_broker(self, access):
        self.broker = access

    def bind_prefix(self, source):
        if not callable(source) or self.prefix_source is not None:
            raise ValueError("unique shared prefix source required")
        self.prefix_source = source

    def snapshot_prefix(self, risk_point):
        if self.prefix_source is None or risk_point is None:
            return None
        return SharedBasketPrefix(**self.prefix_source(), risk_point=risk_point)

    def reserve_risk(self):
        self.risk_budget.reserve()

    def client_book(self, profile):
        self.client = _SharedClientBook(profile, self)
        return self.client

    def claim(self, action, index, now):
        self._require_transport()
        if action.operation == "entry" and self.last_entry_index == index:
            return False
        key = (action.operation, action.ticket)
        request_id = self.offered.get(key)
        if request_id is None:
            if not self.session.remaining_job_slots:
                raise ProtectionBlocked("shared_request_budget_exhausted")
            self.sequence += 1
            request_id = f"{self.spec.channel}:{self.ordinal}:{self.sequence}:{action.operation}:{action.ticket}"
            at = now - self.origin_ns
            operation = {"entry": "OPEN_MARKET", "modify": "MODIFY_SLTP", "close": "CLOSE_POSITION"}[action.operation]
            self.session.submit([TransportJob(
                request_id, self.spec.channel, self.spec.path.signal_id, operation,
                at, at, at + self.profile.request_timeout_ms * 1_000_000, None,
            )])
            self.offered[key] = request_id
            self.owners[request_id] = self.ordinal
        state = self.session.request_state(request_id)
        if state["status"] in {"expired_unsent", "rejected_capacity", "cancelled_unsent"}:
            raise ProtectionBlocked("shared_transport_" + state["status"])
        if self.granted != request_id:
            return False
        self.granted = None
        self.active = (key, request_id)
        self.last_attempts[key] = (index, action.payload)
        if action.operation == "entry":
            self.last_entry_index = index
        return True

    def release(self, now):
        self._require_transport()
        if self.active is None:
            raise RuntimeError("shared_release_without_claim")
        key, request_id = self.active
        self.session.complete(request_id, now - self.origin_ns)
        del self.offered[key]
        self.active = None

    def withdraw(self, action, now):
        self._require_transport()
        key = (action.operation, action.ticket)
        request_id = self.offered.get(key)
        if request_id is None:
            return
        if request_id == self.granted:
            # Transport acquired, but the strategy has not consumed its grant.
            self.session.complete(request_id, now - self.origin_ns)
            self.granted = None
        elif self.session.request_state(request_id)["status"] in {"not_admitted", "preparing", "queued"}:
            self.session.cancel_unsent(request_id, now - self.origin_ns)
        del self.offered[key]

    def _require_transport(self):
        if self.session.failure_reasons:
            raise ProtectionBlocked(self.session.failure_reasons[0])

    def passive(self, ticket, now, *, target):
        self._require_transport()
        if not self.session.remaining_passive_slots:
            raise ProtectionBlocked("shared_passive_budget_exhausted")
        self.passive_sequence += 1
        self.session.submit(passive_events=[PassiveEvent(
            f"{self.ordinal}:{self.passive_sequence}:{ticket}", self.spec.channel,
            self.spec.path.signal_id, now - self.origin_ns, "native_tp" if target else "native_sl",
        )])


def _validate_common_tape(specs, profile):
    if not isinstance(profile, SharedReplayProfile):
        raise ValueError("explicit typed shared profile required")
    if not 1 <= len(specs) <= 128 or any(not isinstance(spec, BasketReplaySpec) for spec in specs):
        raise ValueError("one to 128 typed baskets required")
    if len({spec.path.signal_id for spec in specs}) != len(specs):
        raise ValueError("duplicate basket identity")
    reading = [spec for spec in specs if spec.management_reads is not None]
    if reading and profile.name != "shared_interquote_reads_v2":
        raise ValueError("management reads require explicit interquote profile")
    if profile.name == "shared_interquote_reads_v2" and not reading:
        raise ValueError("interquote profile requires a declared read consumer")
    for spec in reading:
        genome = spec.genome
        if (spec.execution.client.name != "single_basket_terminal_v2"
                or genome.stop_mode not in {"none", "fixed_move"} or genome.target_mode != "none"
                or genome.trailing_distance is not None or genome.be_mode != "none"
                or genome.hard_stop_eur_per_leg is not None):
            raise ValueError("read-driven policy requires terminal client and explicit supported money rules")
        if spec.stop_component is not None and genome.stop_mode != "none":
            raise ValueError("Dubai stop component requires exclusive native stop ownership")
    if any(spec.stop_component is not None for spec in specs):
        metadata = {(spec.execution.protection.point, spec.execution.protection.digits)
                    for spec in specs if spec.execution.protection is not None}
        if len(metadata) != 1:
            raise ValueError("shared symbol metadata must agree")
    first = specs[0].path
    if not 1 <= len(first.times_ns) <= profile.max_quotes:
        raise ValueError("common tape quote budget exceeded")
    if np.any(first.times_ns[1:] < first.times_ns[:-1]):
        raise ValueError("common tape clock must be monotonic")
    for spec in specs:
        tape = spec.path
        if tape.direction in {"BUY", "SELL"} and not np.array_equal(
                tape.exit_quotes, tape.bid if tape.direction == "BUY" else tape.ask, equal_nan=True):
            raise ValueError("exit quotes must match executable Bid/Ask side")
        for name in ("times_ns", "bid", "ask", "fx_bid", "fx_ask", "fx_age_ms", "fx_valid"):
            if not np.array_equal(getattr(first, name), getattr(tape, name), equal_nan=True):
                raise ValueError("shared replay requires identical causal market and FX tape")
        if (first.contract_size, first.conversion_orientation, first.currency_digits) != (
                tape.contract_size, tape.conversion_orientation, tape.currency_digits):
            raise ValueError("shared broker money contract mismatch")


def simulate_shared(specs, *, profile, risk_sink=None, retain_risk=True,
                    provider_event_source=None, basket_admission_source=None,
                    initially_admitted=(), source_quote_indices=None):
    """Run real engine decisions, never requests inferred from completed results.

    At each quote, all baskets first observe broker effects and generate their
    eligible requests. Grants then resume one basket at the same ordinal; they
    cannot masquerade as a new quote for another ladder-entry decision.
    """
    specs = tuple(specs)
    if (type(retain_risk) is not bool
            or risk_sink is not None and (
                not callable(getattr(risk_sink, "record_point", None))
                or not callable(getattr(risk_sink, "record_frame", None)))
            or not retain_risk and risk_sink is None):
        raise ValueError("streamed risk requires point and frame sinks")
    _validate_common_tape(specs, profile)
    if provider_event_source is not None and (
            not callable(provider_event_source)
            or profile.name != "shared_quote_rounds_v1"
            or any(spec.path.provider_events for spec in specs)):
        raise ValueError("dynamic provider source requires quote profile without static provider events")
    if (type(initially_admitted) is not tuple
            or len(set(initially_admitted)) != len(initially_admitted)
            or any(signal_id not in {spec.path.signal_id for spec in specs}
                   for signal_id in initially_admitted)
            or basket_admission_source is None and initially_admitted):
        raise ValueError("invalid initially admitted basket scopes")
    if basket_admission_source is not None and (
            not callable(basket_admission_source)
            or profile.name != "shared_quote_rounds_v1"
            or any(spec.path.provider_events for spec in specs)):
        raise ValueError("dynamic basket admission requires quote profile without static provider events")
    times = specs[0].path.times_ns
    if source_quote_indices is not None and (
            type(source_quote_indices) is not tuple
            or any(type(index) is not int or not 0 <= index < len(times)
                   for index in source_quote_indices)
            or tuple(sorted(set(source_quote_indices))) != source_quote_indices
            or provider_event_source is None and basket_admission_source is None):
        raise ValueError("invalid causal source quote schedule")
    source_quotes = set(source_quote_indices) if source_quote_indices is not None else None
    origin_ns = int(times[0])
    session = TransportSession(cutoff_ns=int(times[-1]) - origin_ns,
                               capacity=profile.capacity, max_events=profile.max_events)
    owners, results, current, streams, ports = {}, {}, {}, {}, {}
    risk, blockers, risk_grid = [], [], []
    risk_count = grid_count = 0
    last_settled = {}
    risk_budget = _RiskBudget(profile.max_events)
    interquote = profile.name == "shared_interquote_reads_v2"
    cycles, read_owners, read_cache, known_tickets, next_poll = {}, {}, {}, {}, {}
    next_stop_poll = {}
    read_reports = []
    read_count = read_bytes = 0
    feeds = ({spec.path.signal_id: _ProviderEventFeed(spec) for spec in specs}
             if provider_event_source is not None else {})
    owners_by_id = {spec.path.signal_id: owner for owner, spec in enumerate(specs)}
    delivered_event_count = 0
    dynamic_admissions = []
    dropped_finished_events = 0

    def advance_transport(at):
        session.advance(at)
        if session.failure_reasons:
            raise ProtectionBlocked(session.failure_reasons[0])

    def advance_stream(owner, command=None):
        try:
            current[owner] = streams[owner].send(command)
        except StopIteration as finished:
            results[owner] = finished.value
            current.pop(owner, None)

    def observe(owner, causal_round):
        nonlocal risk_count
        value = current[owner]
        if not isinstance(value, _ReplaySettlement):
            raise RuntimeError("shared_quote_did_not_settle")
        spec = specs[owner]
        for point in value.snapshots:
            if risk_count >= profile.max_events:
                raise ProtectionBlocked("shared_risk_event_budget_exhausted")
            observation = SharedRiskPoint(
                spec.channel, spec.path.signal_id, value.tick_index,
                value.time_ns, causal_round, point.realized_minor,
                point.floating_minor, point.positions, point.phase)
            if risk_sink is not None:
                risk_sink.record_point(observation)
            if retain_risk:
                risk.append(observation)
            risk_count += 1
            if point.phase == "settled":
                last_settled[owner] = observation
        if "shared_risk_event_budget_exhausted" in value.blockers:
            if not value.continuing:
                advance_stream(owner)
            raise ProtectionBlocked("shared_risk_event_budget_exhausted")

    def settle_grid(index, time_ns):
        nonlocal grid_count
        # A failed intra-quote step has no complete global post-event state.
        if any(value.blockers for value in current.values()):
            return
        if (grid_count + 1) * len(specs) > profile.max_events:
            raise ProtectionBlocked("shared_risk_grid_budget_exhausted")
        states, issues = [], []
        for owner, spec in enumerate(specs):
            point = last_settled.get(owner)
            if owner in current:
                valid = point is not None and point.tick_index == index
            else:
                finished = results.get(owner)
                valid = (point is not None and not point.positions and point.floating_minor == 0
                         and point.realized_minor is not None and finished is not None and not finished.blockers)
            if not valid:
                point = None
                issues.append(f"unresolved_scope:{spec.path.signal_id}")
            states.append((spec.channel, spec.path.signal_id, point))
        frame = SharedRiskFrame(index, time_ns, tuple(states), tuple(issues))
        if risk_sink is not None:
            risk_sink.record_frame(frame)
        if retain_risk:
            risk_grid.append(frame)
        grid_count += 1

    def register_read(owner):
        request_id = cycles[owner].request_id
        if request_id is not None:
            read_owners[request_id] = owner

    def scope_running(owner):
        state = current.get(owner)
        return state is not None and (not isinstance(state, _ReplaySettlement) or state.continuing)

    def start_read(owner, at, *, stage=None):
        nonlocal read_count
        if read_count >= profile.max_read_cycles:
            raise ProtectionBlocked("shared_read_cycle_budget_exhausted")
        spec, port = specs[owner], ports[owner]
        cache = read_cache.setdefault(owner, {})
        known_tickets.setdefault(owner, [])

        def retain_known(known):
            known_tickets[owner] = known

        def sample(request, now):
            nonlocal read_bytes
            source, ordinal, data = port.broker.read(request, origin_ns + now)
            value = ReadValue(None if source is None else source - origin_ns, {"data": data}, ordinal)
            size = len(encode_message(dict(value.payload), MAX_RESPONSE_BYTES))
            if read_bytes + size > profile.max_read_payload_bytes:
                raise ProtectionBlocked("shared_read_payload_budget_exhausted")
            read_bytes += size
            return value

        stop_due = (stage != "money" and spec.stop_component is not None
                    and at >= next_stop_poll.get(owner, 0))
        if stop_due:
            flow = basket_stop_plan_reads(spec.path.direction, port.broker.known_tickets(),
                spec.stop_component.loss_budget, default_symbol=profile.read_symbol)
            cycle_type, read_profile = BasketStopPlanReadCycle, spec.stop_component.reads
            next_stop_poll[owner] = at + spec.stop_component.interval_ns
        else:
            flow = basket_summary_reads(profile.read_symbol, spec.path.direction,
                port.broker.known_tickets, lambda: known_tickets[owner], cache, on_known=retain_known)
            cycle_type, read_profile = BasketReadCycle, spec.management_reads
            next_poll[owner] = at + spec.read_interval_ns
        cycle = cycle_type(flow, session, channel=spec.channel, signal_id=spec.path.signal_id,
            cycle_id=read_count, profile=read_profile, sample=sample)
        read_count += 1
        cycles[owner] = cycle
        port.monitor_busy = True
        cycle.start(at)
        register_read(owner)

    def advance_reads(at):
        for owner, cycle in tuple(cycles.items()):
            port = ports[owner]
            if (not scope_running(owner) or port.broker.terminal_requested()) and not cycle.stopped:
                cycle.cancel(at)
            cycle.advance(at)
            register_read(owner)
            if cycle.completed or cycle.stopped and not cycle.pending:
                row = cycle.report()
                row.update(channel=specs[owner].channel, signal_id=specs[owner].path.signal_id,
                           cycle_id=cycle.cycle_id)
                stop_stage = isinstance(cycle, BasketStopPlanReadCycle)
                if cycle.completed and scope_running(owner):
                    if stop_stage:
                        row["stop_application"] = port.broker.apply_stop_plan(cycle.summary, origin_ns + at)
                    else:
                        row["policy_decision"] = port.broker.observe_summary(cycle.summary, origin_ns + at)
                        row["policy_application"] = port.broker.apply_summary_decision(
                            cycle.summary, row["policy_decision"], origin_ns + at)
                for reason in row["blockers"]:
                    if reason != "read_cycle_cancelled":
                        blockers.append(f"{specs[owner].path.signal_id}:{reason}")
                read_reports.append(row)
                port.monitor_busy = False
                del cycles[owner]
                failure = row.get("stop_application", row.get("policy_application", {})).get("blocker")
                if failure:
                    raise ProtectionBlocked(failure)
                if scope_running(owner):
                    if (stop_stage and not port.broker.terminal_requested()
                            and at >= next_poll.get(owner, 0)):
                        # Continue this monitor turn without a DCA observation in between.
                        # A drained failed stop read must not starve the money guard.
                        start_read(owner, at, stage="money")
                    else:
                        port.broker.resume_monitor(origin_ns + at)

    def next_monitor_poll(owner):
        money = next_poll.get(owner, 0)
        return min(money, next_stop_poll.get(owner, 0)) if specs[owner].stop_component else money

    def pump_interquote(at, quote_index=None):
        for causal_round in range(1, profile.max_rounds_per_quote + 1):
            advance_transport(at)
            for owner in tuple(current):
                if scope_running(owner) and ports[owner].broker is not None:
                    ports[owner].broker.advance_responses(origin_ns + at)
            advance_reads(at)
            advance_transport(at)
            advance_reads(at)
            for owner in tuple(current):
                port = ports[owner]
                if (scope_running(owner) and specs[owner].management_reads is not None
                        and owner not in cycles and port.broker is not None
                        and at >= next_monitor_poll(owner)):
                    port.broker.resume_monitor(origin_ns + at)
                    if port.broker.monitor_ready():
                        start_read(owner, at)
            advance_transport(at)
            grant = session.dispatch()
            if grant is None:
                return
            if grant["request_id"] in read_owners:
                owner = read_owners[grant["request_id"]]
                cycles[owner].accept_grant(grant, at)
                cycles[owner].advance(at)
                register_read(owner)
            else:
                owner = owners[grant["request_id"]]
                if owner not in current:
                    raise ProtectionBlocked("shared_grant_to_terminal_basket")
                ports[owner].granted = grant["request_id"]
                if quote_index is None:
                    ports[owner].broker.advance_responses(origin_ns + at)
                else:
                    if not current[owner].continuing:
                        raise ProtectionBlocked("shared_grant_to_terminal_basket")
                    advance_stream(owner, _ReplayRepeat(quote_index))
                    observe(owner, causal_round)
            advance_transport(at)
        if (session.dispatch_pending or session.next_event_ns == at
                or any(cycle.next_event_ns == at for cycle in cycles.values())):
            raise ProtectionBlocked("shared_round_budget_exhausted")

    def next_interquote_event():
        events = [session.next_event_ns]
        events.extend(cycle.next_event_ns for cycle in cycles.values())
        for owner in tuple(current):
            port = ports[owner]
            if port.broker is None or not scope_running(owner):
                continue
            response = port.broker.next_response_ns()
            if response is not None:
                events.append(response - origin_ns)
            if (specs[owner].management_reads is not None and owner not in cycles
                    and port.broker.monitor_ready()):
                events.append(max(session.now_ns, next_monitor_poll(owner)))
        return min((at for at in events if at is not None), default=None)

    try:
        for owner, spec in enumerate(specs):
            ports[owner] = _Port(session, spec, owner, origin_ns, profile, owners, risk_budget,
                                 feeds.get(spec.path.signal_id),
                                 basket_admission_source is None
                                 or spec.path.signal_id in initially_admitted)
            streams[owner] = _simulation_steps(spec.path, spec.genome, execution=spec.execution,
                                              _transport=ports[owner])
            advance_stream(owner)
        for index, time in enumerate(times):
            if (basket_admission_source is not None
                    and (source_quotes is None or index in source_quotes)):
                prior = tuple((spec.path.signal_id,
                               ports[owner].snapshot_prefix(last_settled.get(owner)))
                              for owner, spec in enumerate(specs))
                admissions = tuple(basket_admission_source(index, int(time), prior))
                if len(dynamic_admissions) + len(admissions) > len(specs):
                    raise ValueError("basket admission budget exceeded")
                previous_ns = int(times[index - 1]) if index else None
                for signal_id in admissions:
                    owner = owners_by_id.get(signal_id)
                    if owner is None or ports[owner].admitted or owner not in current:
                        raise ValueError("unknown, duplicate or finished basket admission")
                    observed_ns = _datetime_ns(specs[owner].path.signal_observed_at)
                    if (observed_ns >= int(time)
                            or previous_ns is not None and observed_ns <= previous_ns):
                        raise ValueError("basket admission outside causal interquote window")
                    ports[owner].admitted = True
                    dynamic_admissions.append((signal_id, index))
            if (provider_event_source is not None
                    and (source_quotes is None or index in source_quotes)):
                prior = tuple((spec.path.signal_id,
                               ports[owner].snapshot_prefix(last_settled.get(owner)))
                              for owner, spec in enumerate(specs))
                deliveries = tuple(provider_event_source(index, int(time), prior))
                if delivered_event_count + len(deliveries) > profile.max_events:
                    raise ValueError("provider event delivery budget exceeded")
                previous_ns = int(times[index - 1]) if index else None
                for signal_id, event in deliveries:
                    owner = owners_by_id.get(signal_id)
                    if owner is not None and owner not in current and owner in results:
                        # The basket already finished in this replay (e.g. a
                        # sampled slower/faster fill); the live bot ignores
                        # management for a closed signal. Counted, never hidden.
                        dropped_finished_events += 1
                        continue
                    if owner is None or owner not in current:
                        raise ValueError("unknown provider scope or finished basket")
                    if not ports[owner].admitted:
                        raise ValueError("provider target basket not admitted")
                    feeds[signal_id].admit(event, previous_ns, int(time))
                    delivered_event_count += 1
            at = int(time) - origin_ns
            if interquote:
                due = next_interquote_event()
                while due is not None and due < at:
                    pump_interquote(due)
                    next_due = next_interquote_event()
                    if next_due is not None and next_due <= due:
                        raise ProtectionBlocked("shared_interquote_event_not_drained")
                    due = next_due
            else:
                while session.next_event_ns is not None and session.next_event_ns < at:
                    advance_transport(session.next_event_ns)
            advance_transport(at)
            for owner in tuple(current):
                boundary = current[owner]
                if not isinstance(boundary, _ReplayBoundary) or boundary.tick_index != index:
                    raise RuntimeError("shared_quote_boundary_mismatch")
                advance_stream(owner)
                observe(owner, 0)
            advance_transport(at)
            if interquote:
                pump_interquote(at, index)
            for causal_round in (() if interquote else range(1, profile.max_rounds_per_quote + 1)):
                grant = session.dispatch()
                if grant is None:
                    break
                owner = owners[grant["request_id"]]
                if owner not in current or not current[owner].continuing:
                    raise ProtectionBlocked("shared_grant_to_terminal_basket")
                ports[owner].granted = grant["request_id"]
                advance_stream(owner, _ReplayRepeat(index))
                observe(owner, causal_round)
                advance_transport(at)
            else:
                if not interquote and session.dispatch_pending:
                    raise ProtectionBlocked("shared_round_budget_exhausted")
            if session.failure_reasons:
                raise ProtectionBlocked(session.failure_reasons[0])
            settle_grid(index, int(time))
            for owner in tuple(current):
                advance_stream(owner)
        while session.next_event_ns is not None:
            advance_transport(session.next_event_ns)
        advance_transport(session.cutoff_ns)
    except ProtectionBlocked as exc:
        blockers.append(str(exc))
    finally:
        for stream in streams.values():
            stream.close()
    for owner, spec in enumerate(specs):
        if owner not in results:
            blockers.append(f"shared_basket_incomplete:{spec.path.signal_id}")
        elif results[owner].blockers:
            blockers.extend(f"{spec.path.signal_id}:{reason}" for reason in results[owner].blockers)
    transport = session.report()
    blockers.extend(transport["blockers"])
    for owner, cycle in cycles.items():
        row = cycle.report()
        row.update(channel=specs[owner].channel, signal_id=specs[owner].path.signal_id, cycle_id=cycle.cycle_id)
        read_reports.append(row)
        blockers.extend(f"{specs[owner].path.signal_id}:{reason}" for reason in row["blockers"])
        cycle.flow.close()
    report = SharedReplayResult(profile, tuple((spec.channel, spec.path.signal_id, results.get(owner))
                                             for owner, spec in enumerate(specs)),
                              tuple(risk), transport, tuple(dict.fromkeys(blockers)),
                              risk_grid=tuple(risk_grid), expected_quote_count=len(times),
                              currency_digits=specs[0].path.currency_digits,
                              policies=tuple((spec.path.signal_id, spec.genome, spec.execution) for spec in specs),
                              read_cycles=tuple(read_reports),
                              risk_streamed=not retain_risk,
                              risk_point_count=risk_count,
                              risk_grid_count=grid_count,
                              dynamic_provider_events=tuple(
                                  (spec.path.signal_id, tuple(feeds[spec.path.signal_id].events))
                                  for spec in specs) if feeds else (),
                              dynamic_basket_admissions=tuple(dynamic_admissions),
                              management_profiles=tuple({"signal_id": spec.path.signal_id,
                                  "channel": spec.channel, "reads": asdict(spec.management_reads),
                                  "read_interval_ns": spec.read_interval_ns,
                                  "guard_component": spec.guard_component.descriptor() if spec.guard_component else None,
                                  "stop_component": spec.stop_component.descriptor() if spec.stop_component else None}
                                  for spec in specs if spec.management_reads is not None))
    if interquote:
        report = replace(report, limitations=(
            "Common supplied quote states; sequential reads use declared hypothetical clocks.",
            "Trade fills and protection processing remain quote-clock hypotheses; acknowledgements may arrive between quotes.",
            "Read-driven money rules are not full Dubai/Gold555 policy admission.",
            "An explicit canonical guard replaces generic profit-lock/time-exit rules only; entry and native protection remain separate hypotheses.",
            "Optional Dubai stop plans use declared unrounded linear FX valuation, not certified native order_calc_profit.",
            "Stop retries use monitor cadence and the declared protection cooldown, not the durable live pending-action worker.",
            "Provisional entry stops, forced post-fill checks and complete monitor ordering remain outside the stop component.",
        ) + report.limitations[1:])
    if dropped_finished_events:
        report = replace(report, limitations=report.limitations + (
            f"{dropped_finished_events} provider event(s) arrived after their basket finished and were ignored.",))
    if provider_event_source is not None:
        report = replace(report, limitations=report.limitations + (
            "Provider updates arrive at the next strictly later quote; "
            + ("dynamic basket admission and " if basket_admission_source is None else "")
            + "interquote reads are not modeled.",
        ))
    if basket_admission_source is not None:
        report = replace(report, limitations=report.limitations + (
            "Predeclared dormant baskets are activated only after causal admission; rejected scopes remain zero-risk model placeholders.",
        ))
    return report


def summarize_shared_risk(report):
    """Summarize synchronized model P/L, not account equity or native extrema."""
    if not isinstance(report, SharedReplayResult):
        raise ValueError("typed shared replay required")
    if report.risk_streamed:
        raise ValueError("streamed risk requires external ordered reduction")
    scopes = [(channel, identity) for channel, identity, _ in report.baskets]
    if not scopes or len(set(scopes)) != len(scopes):
        raise ValueError("unique nonempty scopes required")
    if (len(report.risk_grid) * len(scopes) > report.profile.max_events
            or len(report.risk) > report.profile.max_events):
        raise ValueError("shared risk grid budget exceeded")
    final_round, final_state = {}, {}
    finished = {(channel, identity): result for channel, identity, result in report.baskets}
    for point in report.risk:
        if point.phase == "settled":
            key = (point.channel, point.signal_id)
            final_round[(key, point.tick_index)] = point
            final_state[key] = point
    issues, samples = list(report.blockers), []
    previous_index, previous_time = -1, None
    for frame in report.risk_grid:
        if (frame.tick_index <= previous_index or previous_time is not None and frame.time_ns < previous_time
                or [(channel, identity) for channel, identity, _ in frame.states] != scopes):
            raise ValueError("shared grid identity or chronology mismatch")
        if frame.tick_index != previous_index + 1:
            issues.append("incomplete_post_event_grid")
        previous_index, previous_time = frame.tick_index, frame.time_ns
        issues.extend(frame.blockers)
        contributions = []
        for channel, identity, point in frame.states:
            key = (channel, identity)
            if point is not None and (
                    (point.channel, point.signal_id) != (channel, identity) or point.phase != "settled"
                    or point.tick_index > frame.tick_index or point.time_ns > frame.time_ns
                    or point.tick_index == frame.tick_index and point.time_ns != frame.time_ns
                    or final_round.get((key, point.tick_index)) != point
                    or point.tick_index < frame.tick_index and (
                        point.positions or point.floating_minor != 0 or point.realized_minor is None
                        or final_state.get(key) != point or finished[key] is None
                        or finished[key].blockers or finished[key].pnl_eur is None
                        or finished[key].last_tick_index > point.tick_index
                        or finished[key].pnl_eur * 10 ** report.currency_digits != point.realized_minor)):
                raise ValueError("shared grid state is not a settled or carried-flat contribution")
            total = point.equity_minor if point is not None else None
            if total is None:
                issues.append(f"unknown_risk:{identity}")
            contributions.append({"channel": channel, "signal_id": identity,
                                  "source_tick_index": point.tick_index if point else None,
                                  "realized": point.realized_minor if point else None,
                                  "floating": point.floating_minor if point else None,
                                  "total": total, "positions": point.positions if point else None})
        total = (sum(row["total"] for row in contributions)
                 if all(row["total"] is not None for row in contributions) else None)
        samples.append({"tick_index": frame.tick_index, "time_ns": frame.time_ns,
                        "total": total, "contributions": contributions})
    if not samples or len(samples) != report.expected_quote_count:
        issues.append("incomplete_post_event_grid")
    known = money_path_metrics((row["total"] for row in samples), origin=0)
    if known is not None:
        last = next(row for row in reversed(samples) if row["total"] is not None)
        known = {**known, "final_tick_index": last["tick_index"], "final_time_ns": last["time_ns"]}
    return {"grid_contract": "post_event_quote_grid_v1", "provenance": "modelled",
            "scope": "selected_initially_flat_baskets", "expected_scopes": scopes,
            "currency": report.profile.account_currency, "currency_digits": report.currency_digits,
            "units": "account_currency_minor", "origin": 0,
            "metrics": known if not issues else None, "known_sample_metrics": known,
            "final_total_minor": samples[-1]["total"] if samples and previous_index == report.expected_quote_count - 1 else None,
            "samples": samples, "blockers": tuple(dict.fromkeys(issues)),
            "native_parity_verified": False, "account_equity_verified": False}
