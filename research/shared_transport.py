"""Bounded cross-basket transport simulation, not a broker or policy engine.

Durations are declared hypotheses or observed phase durations, not fill prices.
Preparation occupies admission but not transport. A timeout cannot release an
active call: its late completion or an explicit external recovery is required.
"""

from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass
import heapq

from mt5_read_protocol import ReadOperation
from mt5_scheduling import POLICY_ID, TRANSPORT_KINDS, admission_available, next_transport_kind
from mt5_trade_protocol import ENTRY_OPERATIONS, TRADE_OPERATIONS


MAX_JOBS = 10_000
MAX_TIME_NS = 2**63 - 1
READS = frozenset(row.value for row in ReadOperation if row not in {ReadOperation.INITIALIZE, ReadOperation.SHUTDOWN})


def _clock(value, name):
    if type(value) is not int or not 0 <= value <= MAX_TIME_NS:
        raise ValueError(f"invalid {name}: integer session-relative nanoseconds required")


def _identity(value, name):
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ValueError(f"invalid {name}")


@dataclass(frozen=True)
class TransportJob:
    request_id: str
    channel: str
    signal_id: str | None
    operation: str
    admitted_ns: int
    ready_ns: int | None
    deadline_ns: int
    service_ns: int | None

    def __post_init__(self):
        _identity(self.request_id, "request identity")
        if self.channel not in {"canal1", "canal2", "system"}:
            raise ValueError("unsupported channel")
        if self.operation not in TRADE_OPERATIONS | READS:
            raise ValueError("unsupported operation")
        if self.channel == "system":
            if self.operation not in READS or self.signal_id is not None:
                raise ValueError("system scope only supports unowned reads")
        else:
            _identity(self.signal_id, "signal identity")
            if not self.signal_id.startswith(self.channel + "_"):
                raise ValueError("signal/channel identity mismatch")
        for name in ("admitted_ns", "deadline_ns"):
            _clock(getattr(self, name), name)
        if self.deadline_ns <= self.admitted_ns:
            raise ValueError("deadline must follow admission")
        if self.ready_ns is not None:
            _clock(self.ready_ns, "ready_ns")
            if self.ready_ns < self.admitted_ns:
                raise ValueError("readiness precedes admission")
        if self.service_ns is not None:
            _clock(self.service_ns, "service_ns")

    @property
    def kind(self):
        return "read" if self.operation in READS else "trade" if self.operation in ENTRY_OPERATIONS else "management"


@dataclass(frozen=True)
class PassiveEvent:
    event_id: str
    channel: str
    signal_id: str
    at_ns: int
    kind: str

    def __post_init__(self):
        for name in ("event_id", "signal_id", "kind"):
            _identity(getattr(self, name), name)
        _clock(self.at_ns, "passive clock")
        if self.channel not in {"canal1", "canal2"} or not self.signal_id.startswith(self.channel + "_"):
            raise ValueError("passive signal/channel identity mismatch")
        if self.kind not in {"native_tp", "native_sl", "native_fill", "native_partial_close"}:
            raise ValueError("unsupported passive event")


class TransportSession:
    """Incremental transport, with an observation boundary before dispatch.

    Submit newly generated jobs, advance to the next event (or an earlier
    external observation), inspect the effects, then dispatch at most one call.
    A zero-duration response is another observation, not an implicit drain.
    Input ordinal is the declared same-class tie hypothesis. This session does
    not run a strategy, authorize a native call, or produce a broker fill.
    """

    def __init__(self, *, cutoff_ns, capacity=8, max_events=100_000):
        _clock(cutoff_ns, "cutoff_ns")
        if type(capacity) is not int or not 1 <= capacity <= 128:
            raise ValueError("capacity must be between 1 and 128")
        if type(max_events) is not int or not 1 <= max_events <= 1_000_000:
            raise ValueError("invalid event budget")
        self.cutoff_ns, self.capacity, self.max_events = cutoff_ns, capacity, max_events
        self.now_ns = 0
        self._jobs, self._passive, self._states = [], [], []
        self._job_ids, self._passive_ids = set(), set()
        self._job_index = {}
        self._heap, self._events, self._blockers = [], [], []
        self._pending, self._waiting = set(), set()
        self._active, self._streak, self._peak = None, 0, 0

    @property
    def next_event_ns(self):
        return self._heap[0][0] if self._heap and not self._blockers else None

    @property
    def failure_reasons(self):
        return tuple(self._blockers)

    @property
    def dispatch_pending(self):
        return not self._blockers and self._active is None and bool(self._waiting)

    @property
    def remaining_job_slots(self):
        return max(0, MAX_JOBS - len(self._jobs))

    @property
    def remaining_passive_slots(self):
        return max(0, MAX_JOBS - len(self._passive))

    def submit(self, jobs=(), *, passive_events=()):
        """Validate the entire batch before adding any identities or events."""
        if self._blockers:
            raise RuntimeError("transport_session_blocked")
        jobs, passive_events = tuple(jobs), tuple(passive_events)
        if len(self._jobs) + len(jobs) > MAX_JOBS or len(self._passive) + len(passive_events) > MAX_JOBS:
            raise ValueError("input budget exceeded")
        if any(not isinstance(job, TransportJob) for job in jobs) or any(not isinstance(event, PassiveEvent) for event in passive_events):
            raise ValueError("typed scheduling inputs required")
        job_ids, passive_ids = {job.request_id for job in jobs}, {event.event_id for event in passive_events}
        if len(job_ids) != len(jobs) or job_ids & self._job_ids:
            raise ValueError("duplicate request identity; retries need distinct transport identities")
        if len(passive_ids) != len(passive_events) or passive_ids & self._passive_ids:
            raise ValueError("duplicate passive identity")
        times = [job.admitted_ns for job in jobs] + [event.at_ns for event in passive_events]
        if any(at > self.cutoff_ns for at in times):
            raise ValueError("selected input outside cutoff")
        if any(at < self.now_ns for at in times):
            raise ValueError("cannot submit an input in the past")
        self._job_ids.update(job_ids)
        self._passive_ids.update(passive_ids)
        for job in jobs:
            index = len(self._jobs)
            self._jobs.append(job)
            self._job_index[job.request_id] = index
            self._states.append({
                "request_id": job.request_id, "channel": job.channel, "signal_id": job.signal_id,
                "operation": job.operation, "kind": job.kind, "status": "not_admitted",
                "started_ns": None, "released_ns": None, "timed_out": False,
                "queue_ns": None, "preparation_ns": None,
            })
            self._push(job.admitted_ns, 3, index, "admit")
            self._push(job.deadline_ns, 0, index, "deadline")
            if job.ready_ns is not None:
                self._push(job.ready_ns, 4, index, "ready")
        for event in passive_events:
            index = len(self._passive)
            self._passive.append(event)
            self._push(event.at_ns, 2, index, "passive")

    def advance(self, now_ns):
        """Observe due events, without silently skipping a decision boundary."""
        _clock(now_ns, "now_ns")
        if now_ns < self.now_ns:
            raise ValueError("cannot move transport clock backwards")
        if now_ns > self.cutoff_ns:
            raise ValueError("selected time outside cutoff")
        if self._blockers:
            return ()
        if self.next_event_ns is not None and self.next_event_ns < now_ns:
            raise ValueError("advance would skip the next event")
        # Once eligible work is known, callers must dispatch before moving on.
        if now_ns > self.now_ns and self._active is None and self._waiting:
            raise ValueError("dispatch required before advancing eligible work")
        self.now_ns = now_ns
        start = len(self._events)
        self._observe_due()
        return tuple(deepcopy(self._events[start:]))

    def _current_input(self, request_id, now_ns):
        _clock(now_ns, "now_ns")
        if self._blockers:
            raise RuntimeError("transport_session_blocked")
        if now_ns != self.now_ns:
            raise ValueError("external input requires current observed clock")
        if request_id not in self._job_index:
            raise ValueError("unknown request identity")
        return self._job_index[request_id]

    def complete(self, request_id, now_ns):
        """Queue an observed book response; advance observes it before reuse."""
        index = self._current_input(request_id, now_ns)
        if self._active != index:
            raise ValueError("response requires active request")
        if self._jobs[index].service_ns is not None:
            raise ValueError("declared service already owns response clock")
        if any(item == index and kind == "response" for _, _, item, kind in self._heap):
            raise ValueError("response already scheduled")
        self._push(now_ns, 1, index, "response")

    def request_state(self, request_id):
        if request_id not in self._job_index:
            raise ValueError("unknown request identity")
        return deepcopy(self._states[self._job_index[request_id]])

    def cancel_unsent(self, request_id, now_ns):
        """Withdraw an unsent intent, retaining any preparation still running."""
        index = self._current_input(request_id, now_ns)
        state = self._states[index]
        if state["status"] not in {"not_admitted", "preparing", "queued"}:
            raise ValueError("cancellation requires unsent request")
        try:
            self._emit("cancelled_before_dispatch", index)
        except RuntimeError as exc:
            self._blockers.append(str(exc))
            return
        if state["status"] == "preparing":
            state["status"] = "cancelled_preparing"
        else:
            state["status"] = "cancelled_unsent"
            self._waiting.discard(index)
            self._pending.discard(index)
            self._heap = [(at, rank, item, kind) for at, rank, item, kind in self._heap
                          if not (item == index and kind != "passive")]
            heapq.heapify(self._heap)

    def _observe_due(self):
        try:
            while self._heap and self._heap[0][0] == self.now_ns:
                _, _, index, kind = heapq.heappop(self._heap)
                self._observe(index, kind)
        except RuntimeError as exc:
            self._blockers.append(str(exc))

    def _observe(self, index, kind):
        now = self.now_ns
        if kind == "passive":
            event = self._passive[index]
            self._emit("passive", event_id=event.event_id, channel=event.channel,
                       signal_id=event.signal_id, mechanism=event.kind,
                       active_request_id=self._jobs[self._active].request_id if self._active is not None else None)
            return
        job, state = self._jobs[index], self._states[index]
        if kind == "admit":
            accepted = admission_available(len(self._pending), self.capacity, management=job.kind == "management")
            self._emit("admitted" if accepted else "rejected_capacity", index)
            state["status"] = "preparing" if accepted else "rejected_capacity"
            if accepted:
                self._pending.add(index)
                self._peak = max(self._peak, len(self._pending))
        elif kind == "ready":
            if state["status"] in {"expired_preparing", "cancelled_preparing"}:
                self._emit("preparation_drained", index)
                self._pending.remove(index)
                state["status"] = "expired_unsent" if state["status"] == "expired_preparing" else "cancelled_unsent"
            elif state["status"] == "preparing":
                self._emit("queued", index)
                state["status"] = "queued"
                state["preparation_ns"] = now - job.admitted_ns
                self._waiting.add(index)
        elif kind == "deadline":
            if state["status"] in {"preparing", "queued", "in_flight"}:
                self._emit("timeout_in_flight" if index == self._active else "expired_before_dispatch", index)
                state["timed_out"] = True
                if index == self._active:
                    state["status"] = "timed_out_in_flight"
                elif index in self._waiting:
                    state["status"] = "expired_unsent"
                    self._waiting.remove(index)
                    self._pending.remove(index)
                else:
                    state["status"] = "expired_preparing"
        elif kind == "response":
            if self._active != index:
                raise RuntimeError("response_without_active_request")
            self._emit("late_response" if state["timed_out"] else "response", index)
            state["status"] = "released_late" if state["timed_out"] else "released"
            state["released_ns"] = now
            self._pending.remove(index)
            self._active = None
            self._streak = 0 if job.kind == "read" else self._streak + 1

    def dispatch(self):
        """Admit newly submitted current-time jobs, then grant at most one call.

        Responses must be observed explicitly via advance(), so a caller can
        react before another waiting request gets the resource.
        """
        if self._blockers or self._active is not None:
            return None
        if any(at == self.now_ns and kind in {"response", "deadline", "passive"}
               for at, _, _, kind in self._heap):
            raise ValueError("observe due events before dispatch")
        self._observe_due()
        if self._blockers or not self._waiting:
            return None
        counts = {kind: sum(self._jobs[index].kind == kind for index in self._waiting) for kind in TRANSPORT_KINDS}
        turn = next_transport_kind(counts, self._streak)
        eligible = sorted((index for index in self._waiting if self._jobs[index].kind == turn),
                          key=lambda index: (self._jobs[index].ready_ns, index))
        index = eligible[0]
        try:
            event = self._emit("started", index, transport_kind=turn,
                               eligible_request_ids=[self._jobs[item].request_id for item in eligible],
                               tie_hypothesis_used=len(eligible) > 1)
            self._waiting.remove(index)
            self._active = index
            self._states[index].update(status="in_flight", started_ns=self.now_ns,
                                       queue_ns=self.now_ns - self._jobs[index].ready_ns)
            duration = self._jobs[index].service_ns
            if duration is not None:
                if self.now_ns + duration > MAX_TIME_NS:
                    raise RuntimeError("response_clock_overflow")
                self._push(self.now_ns + duration, 1, index, "response")
            return deepcopy(event)
        except RuntimeError as exc:
            self._blockers.append(str(exc))
            return None

    def _emit(self, kind, index=None, **extra):
        if len(self._events) >= self.max_events:
            raise RuntimeError("transport_event_budget_exhausted")
        job = self._jobs[index] if index is not None else None
        event = {"kind": kind, "at_ns": self.now_ns,
                 "request_id": job.request_id if job else None,
                 "channel": job.channel if job else extra.pop("channel", None),
                 "signal_id": job.signal_id if job else extra.pop("signal_id", None), **extra}
        self._events.append(event)
        return event

    def _push(self, at, rank, index, kind):
        if at <= self.cutoff_ns:
            heapq.heappush(self._heap, (at, rank, index, kind))

    def report(self):
        """Detached evidence snapshot; only an observed cutoff can finish it."""
        blockers = list(self._blockers)
        if self._pending:
            blockers.append("transport_lifecycle_incomplete_at_cutoff")
        if self.now_ns < self.cutoff_ns and not self._blockers:
            blockers.append("transport_session_not_at_cutoff")
        if self.now_ns == self.cutoff_ns and self._heap and not self._blockers:
            blockers.append("transport_events_unobserved_at_cutoff")
        return deepcopy({
            "contract": "shared_transport_diagnostic_v1", "scheduling_policy": POLICY_ID,
            "status": "blocked" if blockers else "diagnostic_complete",
            "full_live_parity_verified": False, "policy_decisions_verified": False,
            "broker_fills_simulated": False, "portfolio_admitted": False,
            "cutoff_ns": self.cutoff_ns, "capacity": self.capacity, "reserved_management_slots": 1,
            "tie_break": "fifo_ready_hypothesis_not_asyncio_order_proof",
            "same_clock_order": ["deadline", "response", "passive", "admit", "ready", "dispatch"],
            "jobs": [asdict(job) for job in self._jobs], "passive_inputs": [asdict(event) for event in self._passive],
            "rows": self._states, "events": self._events, "blockers": blockers,
            "statuses": dict(Counter(row["status"] for row in self._states)), "peak_admitted": self._peak,
            "pending_request_ids": [self._jobs[index].request_id for index in sorted(self._pending)],
            "active_request_id": self._jobs[self._active].request_id if self._active is not None else None,
        })


def simulate_transport(jobs, *, cutoff_ns, capacity=8, passive_events=(), max_events=100_000):
    """Drain the incremental session using the declared bulk tie convention."""
    session = TransportSession(cutoff_ns=cutoff_ns, capacity=capacity, max_events=max_events)
    session.submit(jobs, passive_events=passive_events)
    while session.next_event_ns is not None:
        session.advance(session.next_event_ns)
        session.dispatch()
    session.advance(cutoff_ns)
    return session.report()
