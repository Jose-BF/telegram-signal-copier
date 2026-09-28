"""One sequential basket-read cycle on the shared offline transport.

The owner advances broker state and TransportSession before delivering each
event here. Reads have their own owner, never the trading ClientBook's owner.
No quotes are advanced, trades inferred or policy decisions made by this class.
"""

from copy import deepcopy
from dataclasses import dataclass
import math
from types import SimpleNamespace

from mt5_protocol import ImmutableJsonMapping
from mt5_read_protocol import MAX_RECORDS, MAX_RESPONSE_BYTES, ReadOperation, ReadRequest, encode_message
from research.shared_transport import MAX_TIME_NS, TransportJob, TransportSession


@dataclass(frozen=True)
class ReadCycleProfile:
    sample_delay_ns: int = 0
    response_delay_ns: int = 0
    timeout_ns: int = 60_000_000_000
    max_reads: int = 128
    max_payload_bytes: int = 4_194_304

    def __post_init__(self):
        for name in ("sample_delay_ns", "response_delay_ns", "timeout_ns"):
            value = getattr(self, name)
            if type(value) is not int or not (1 if name == "timeout_ns" else 0) <= value <= 86_400_000_000_000:
                raise ValueError(f"invalid {name}")
        if type(self.max_reads) is not int or not 1 <= self.max_reads <= 10_000:
            raise ValueError("invalid max_reads")
        if type(self.max_payload_bytes) is not int or not 1 <= self.max_payload_bytes <= 67_108_864:
            raise ValueError("invalid max_payload_bytes")


@dataclass(frozen=True)
class ReadValue:
    source_ns: int | None
    payload: ImmutableJsonMapping
    source_ordinal: int | None = None

    def __post_init__(self):
        if self.source_ns is not None and (type(self.source_ns) is not int or not 0 <= self.source_ns <= MAX_TIME_NS):
            raise ValueError("invalid read source clock")
        if self.source_ordinal is not None and (type(self.source_ordinal) is not int or not 0 <= self.source_ordinal <= MAX_TIME_NS):
            raise ValueError("invalid read source ordinal")
        if set(self.payload) != {"data"}:
            raise ValueError("read value requires data envelope")
        data = self.payload["data"]
        if isinstance(data, list) and len(data) > MAX_RECORDS:
            raise ValueError("read record budget exhausted")
        encode_message(dict(self.payload), MAX_RESPONSE_BYTES)
        object.__setattr__(self, "payload", ImmutableJsonMapping(dict(self.payload)))

    def native_value(self, operation=None):
        data = self.payload["data"]
        if data is None:
            return None
        if operation is ReadOperation.PROFIT:
            if type(data) not in (int, float) or not math.isfinite(data):
                raise ValueError("profit read requires finite scalar or unknown")
            return data
        if operation is ReadOperation.SYMBOL and not isinstance(data, dict):
            raise ValueError("symbol read requires record or unknown")
        if isinstance(data, list):
            return [SimpleNamespace(**row) for row in data]
        if isinstance(data, dict):
            return SimpleNamespace(**data)
        raise ValueError("basket reads require records or unknown")


class BasketReadCycle:
    """Supply responses to the same generator used by the live monitor."""

    allowed_operations = frozenset({ReadOperation.TICK, ReadOperation.POSITIONS, ReadOperation.DEALS_POSITION})

    def __init__(self, flow, session, *, channel, signal_id, cycle_id, profile, sample):
        if not isinstance(session, TransportSession) or not isinstance(profile, ReadCycleProfile):
            raise ValueError("typed transport and read profile required")
        self.flow, self.session = flow, session
        self.channel, self.signal_id, self.cycle_id = channel, signal_id, cycle_id
        if type(cycle_id) is not int or not 0 <= cycle_id <= 1_000_000:
            raise ValueError("invalid cycle identity")
        self.profile, self.sample = profile, sample
        self.request = self.request_id = None
        self.started_ns = self.sample_ns = self.delivery_ns = None
        self.value = None
        self.payload_bytes = 0
        self.omitted_payload = False
        self.records = []
        self.requests = []
        self.sequence = 0
        self._summary = None
        self.completed = self.stopped = False
        self.reason = None
        self._begun = False
        self._response_queued = False

    @property
    def summary(self):
        return deepcopy(self._summary)

    @property
    def pending(self):
        return self.request_id is not None

    @property
    def next_event_ns(self):
        due = self.delivery_ns if self.value is not None else self.sample_ns
        return due if due is not None and due <= self.session.cutoff_ns else None

    def _clock(self, now):
        if type(now) is not int or now != self.session.now_ns:
            raise ValueError("read cycle requires current transport clock")

    def start(self, now):
        self._clock(now)
        if self._begun:
            raise ValueError("read cycle already started")
        self._begun = True
        self._resume(None, now)

    def _resume(self, response, now):
        try:
            request = self.flow.send(response)
        except StopIteration as complete:
            self._summary = deepcopy(complete.value)
            self.completed = True
            return
        except (AttributeError, KeyError, TypeError, ValueError):
            self._stop("invalid_read_payload")
            if self.records:
                self.records[-1]["disposition"] = "invalid"
            return
        if not isinstance(request, ReadRequest) or request.operation not in self.allowed_operations:
            self._stop("unsupported_basket_read")
            return
        if self.sequence >= self.profile.max_reads or not self.session.remaining_job_slots:
            self._stop("read_cycle_budget_exhausted")
            return
        self.sequence += 1
        identity = f"{self.signal_id}:read:{self.cycle_id}:{self.sequence}"
        self.session.submit([TransportJob(identity, self.channel, self.signal_id, request.operation.value,
            now, now, now + self.profile.timeout_ns, None)])
        self.requests.append({"request_id": identity, "operation": request.operation.value,
                              "params": request.params.to_dict(), "requested_ns": now})
        self.request, self.request_id = request, identity
        self.requested_ns = now

    def accept_grant(self, grant, now):
        self._clock(now)
        if self.request_id is None or grant["request_id"] != self.request_id or self.started_ns is not None:
            raise ValueError("read grant identity mismatch")
        state = self.session.request_state(self.request_id)
        if state["status"] != "in_flight" or state["started_ns"] != now:
            raise ValueError("read grant is not active at this clock")
        self.started_ns = now
        self.sample_ns = now + self.profile.sample_delay_ns
        self.delivery_ns = self.sample_ns + self.profile.response_delay_ns
        if self.delivery_ns > MAX_TIME_NS:
            self._stop("read_clock_overflow")

    def _stop(self, reason):
        self.stopped = True
        self.reason = self.reason or reason
        self.flow.close()

    def cancel(self, now):
        self._clock(now)
        self._stop("read_cycle_cancelled")
        if self.request_id is not None and self.started_ns is None:
            state = self.session.request_state(self.request_id)
            if state["status"] in {"in_flight", "timed_out_in_flight"}:
                # Connection granted, but this consumer has not started a read.
                self.session.complete(self.request_id, now)
            elif state["status"] in {"not_admitted", "preparing", "queued"}:
                self.session.cancel_unsent(self.request_id, now)
            self.request_id = self.request = None

    def advance(self, now):
        self._clock(now)
        if self.request_id is None:
            return
        if self.session.failure_reasons:
            self._stop(self.session.failure_reasons[0])
            return
        state = self.session.request_state(self.request_id)
        if state["status"] in {"expired_unsent", "rejected_capacity", "cancelled_unsent"}:
            self._stop("read_" + state["status"])
            self.request_id = self.request = None
            return
        if state["timed_out"]:
            self._stop("read_timeout")
        if self._response_queued:
            if now > self.delivery_ns:
                raise ValueError("missed read response observation")
            if state["status"] in {"released", "released_late"}:
                self._deliver(now)
            return
        if self.started_ns is None:
            return
        due = self.next_event_ns
        if due is not None and now > due:
            raise ValueError("missed read event; do not sample a later broker state")
        if self.value is None and now == self.sample_ns:
            value = self.sample(self.request, now)
            if not isinstance(value, ReadValue) or value.source_ns is not None and value.source_ns > now:
                raise ValueError("read sample has invalid or future source")
            size = len(encode_message(dict(value.payload), MAX_RESPONSE_BYTES))
            if self.payload_bytes + size > self.profile.max_payload_bytes:
                self._stop("read_payload_budget_exhausted")
                self.omitted_payload = True
                value = ReadValue(value.source_ns, {"data": None}, value.source_ordinal)
            else:
                self.payload_bytes += size
            self.value = value
        if now != self.delivery_ns:
            return
        self.records.append({"request_id": self.request_id, "operation": self.request.operation.value,
            "params": self.request.params.to_dict(), "requested_ns": self.requested_ns,
            "started_ns": self.started_ns, "sample_ns": self.sample_ns,
            "source_ns": self.value.source_ns, "delivery_ns": now,
            "source_ordinal": self.value.source_ordinal,
            "disposition": "awaiting_transport_response",
            "payload_omitted": self.omitted_payload,
            "payload": None if self.omitted_payload else self.value.payload.to_dict()})
        self.session.complete(self.request_id, now)
        self._response_queued = True

    def _deliver(self, now):
        # complete() only queues a transport event. Policy/cache consumption
        # follows its successful observation, not merely the proposed response.
        self.records[-1]["disposition"] = "discarded" if self.stopped else "received"
        try:
            response = self.value.native_value(self.request.operation) if not self.stopped else None
        except (TypeError, ValueError):
            self._stop("invalid_read_payload")
            self.records[-1]["disposition"] = "invalid"
            response = None
        self.request_id = self.request = None
        self.started_ns = self.sample_ns = self.delivery_ns = None
        self.value = None
        self._response_queued = False
        if not self.stopped:
            self._resume(response, now)

    def report(self):
        reasons = [self.reason] if self.reason else []
        if self.pending or not (self.completed or self.stopped):
            reasons.append("read_cycle_incomplete")
        return {"contract": "sequential_basket_reads_v1", "completed": self.completed,
                "summary": self.summary, "records": deepcopy(self.records), "blockers": reasons,
                "requests": deepcopy(self.requests), "request_id": self.request_id,
                "native_parity_verified": False}


class BasketStopReadCycle(BasketReadCycle):
    """Same transport lifecycle for the common-stop valuation generator."""

    allowed_operations = frozenset({ReadOperation.SYMBOL, ReadOperation.PROFIT})

    @property
    def stop_price(self):
        return self.summary

    def report(self):
        report = super().report()
        result = report.pop("summary")
        report.update(contract="sequential_basket_stop_reads_v1", stop_price=result,
                      stop_available=self.completed and result is not None,
                      protection_installed=False)
        return report


class BasketStopPlanReadCycle(BasketReadCycle):
    """Position snapshot followed by the broker-valued common stop."""

    allowed_operations = frozenset({ReadOperation.POSITIONS, ReadOperation.SYMBOL, ReadOperation.PROFIT})

    def report(self):
        report = super().report()
        plan = report.pop("summary")
        report.update(contract="sequential_basket_stop_plan_v1", plan=plan,
                      stop_available=bool(self.completed and plan is not None and plan["stop_price"] is not None),
                      protection_installed=False)
        return report
