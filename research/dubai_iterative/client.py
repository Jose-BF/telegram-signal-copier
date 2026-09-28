"""Opt-in, single-basket client resource and causal decision snapshots."""

from dataclasses import dataclass

from .protection import ProtectionBlocked
from .client_contract import ClientEvent, ClientProfile


@dataclass(frozen=True)
class ClientAction:
    operation: str
    ticket: str
    decision_index: int
    decision_ns: int
    payload: tuple


class ClientBook:
    """FIFO calls share one resource until their response is available.

    Unsent stop intents coalesce without changing FIFO position. A submitted
    operation is immutable; passive broker events do not acquire this resource.
    """

    def __init__(self, profile):
        self.profile = profile
        self.queue: list[ClientAction] = []
        self.active: ClientAction | None = None
        self.events: list[ClientEvent] = []

    def emit(self, kind, action, index, now):
        if len(self.events) >= self.profile.max_events:
            raise ProtectionBlocked("client_event_budget_exhausted")
        self.events.append(ClientEvent(kind, action.operation, action.ticket, index, now,
                                       action.decision_index, action.decision_ns))

    def enqueue(self, action, index, now):
        key = (action.operation, action.ticket)
        if self.active is not None and (self.active.operation, self.active.ticket) == key and action.operation != "modify":
            return
        for offset, previous in enumerate(self.queue):
            if (previous.operation, previous.ticket) == key:
                if action.operation == "modify" and previous != action:
                    self.emit("intent_updated", action, index, now)
                    self.queue[offset] = action
                return
        self.emit("queued", action, index, now)
        self.queue.append(action)

    def start(self, index, now):
        if self.active is not None or not self.queue:
            return None
        action = self.queue[0]
        self.emit("started", action, index, now)
        self.active = self.queue.pop(0)
        return action

    def release(self, index, now):
        if self.active is not None:
            self.emit("released", self.active, index, now)
            self.active = None

    def cancel_entries(self, index, now):
        for action in list(self.queue):
            if action.operation == "entry":
                self.emit("cancelled", action, index, now)
                self.queue.remove(action)

    @property
    def pending(self):
        return self.active is not None or bool(self.queue)


def client_blockers(genome, execution, *, backend="scalar"):
    if execution.client is None:
        return []
    errors = []
    if execution.market is None or execution.protection is None:
        errors.append("client_requires_market_and_protection")
    if genome.schema_version != 2 or genome.entry_mode == "actual_mt5":
        errors.append("client_requires_hypothetical_schema2_entries")
    if genome.leg_count > 1 and genome.entry_ladder_mode == "simultaneous":
        errors.append("client_simultaneous_multileg_unsupported")
    delay = execution.client.entry_price_delay_ms
    if delay is not None and delay > execution.entry_fill_latency_ms:
        errors.append("client_price_clock_after_fill")
    if backend != "scalar":
        errors.append(f"client_model_not_validated_in_{backend}")
    return errors
