"""Scalar request/response state for the opt-in quote-clock market model."""

from dataclasses import dataclass
from decimal import Decimal

from .market_contract import MarketEvent, MarketProfile
from .protection import ProtectionBlocked


@dataclass
class _Request:
    ticket: str
    request_id: int
    index: int
    requested_ns: int
    volume: float
    reason: str
    entry: bool
    price: float | None
    processed_ns: int | None = None
    acknowledgement_ns: int | None = None
    outcome_reason: str = ""
    acknowledged: bool = False
    response_owner: str = "quote_clock"


class MarketBook:
    def __init__(self, profile: MarketProfile):
        self.profile = profile
        self.events: list[MarketEvent] = []
        self.requests: list[_Request] = []
        self.entries: dict[str, _Request] = {}
        self.closes: dict[str, _Request] = {}

    def require_event_capacity(self):
        if len(self.events) >= self.profile.max_events:
            raise ProtectionBlocked("market_event_budget_exhausted")

    def emit(self, request, index, now, kind, price, reason):
        self.require_event_capacity()
        self.events.append(MarketEvent(request.ticket, index, now, request.request_id,
                                       kind, price, request.volume, reason))

    def valid_volume(self, volume):
        value, step = Decimal(str(volume)), Decimal(str(self.profile.volume_step))
        return (Decimal(str(self.profile.volume_min)) <= value <= Decimal(str(self.profile.volume_max))
                and value % step == 0)

    def request(self, ticket, index, now, price, volume, reason, *, entry,
                response_owner="quote_clock"):
        if response_owner not in ("quote_clock", "external_runtime"):
            raise ValueError("unsupported market response owner")
        if response_owner == "external_runtime" and not entry:
            raise ValueError("external responses currently require an entry")
        target = self.entries if entry else self.closes
        if ticket in target:
            if target[ticket].response_owner != response_owner:
                raise ValueError("market response owner mismatch")
            return
        request = _Request(ticket, len(self.requests) + 1, index, now, volume, reason, entry, price,
                           response_owner=response_owner)
        self.emit(request, index, now, "entry_requested" if entry else "close_requested", price, reason)
        self.requests.append(request)
        target[ticket] = request

    def finish(self, ticket, index, now, price, *, entry, rejection=None):
        request = (self.entries if entry else self.closes)[ticket]
        self._finish(request, index, now, price, rejection=rejection)

    def request_external_close(self, ticket, index, now, price, volume, reason):
        request = _Request(ticket, len(self.requests) + 1, index, now, volume, reason,
                           False, price, response_owner="external_runtime")
        self.emit(request, index, now, "close_requested", price, reason)
        # Native sends are distinct effects, never quote-clock scheduled work.
        self.requests.append(request)
        return request.request_id

    def _external_close(self, request_id):
        if type(request_id) is not int or not 1 <= request_id <= len(self.requests):
            raise ValueError("unknown external close request")
        request = self.requests[request_id - 1]
        if request.entry or request.response_owner != "external_runtime":
            raise ValueError("market request is not an external close")
        return request

    def finish_external_close(self, request_id, index, now, price, *, rejection=None):
        request = self._external_close(request_id)
        if request.processed_ns is not None:
            raise ValueError("external close is already processed")
        if now < request.requested_ns:
            raise ValueError("external close processing precedes request")
        self._finish(request, index, now, price, rejection=rejection)

    def _finish(self, request, index, now, price, *, rejection=None):
        prefix = "entry" if request.entry else "close"
        self.emit(request, index, now, prefix + ("_rejected" if rejection else "_filled"),
                  None if rejection else price, rejection or request.reason)
        request.processed_ns = now
        request.price = None if rejection else price
        delay = self.profile.entry_acknowledgement_delay_ms if request.entry else self.profile.close_acknowledgement_delay_ms
        request.acknowledgement_ns = (now + delay * 1_000_000
                                      if request.response_owner == "quote_clock" else None)
        request.outcome_reason = rejection or "accepted"

    def acknowledge(self, index, now):
        entries = {}
        for request in self.requests:
            if request.response_owner != "quote_clock":
                continue
            if request.acknowledged or request.acknowledgement_ns is None or now < request.acknowledgement_ns:
                continue
            self.emit(request, index, now, "entry_acknowledged" if request.entry else "close_acknowledged",
                      request.price, request.outcome_reason)
            request.acknowledged = True
            if request.entry and request.outcome_reason == "accepted":
                entries[request.ticket] = now
            elif not request.entry and request.reason == "partial_target":
                del self.closes[request.ticket]
        return entries

    def acknowledge_external(self, ticket, index, now, *, entry=True):
        if not entry:
            raise ValueError("external responses currently require an entry")
        request = self.entries[ticket]
        if request.response_owner != "external_runtime":
            raise ValueError("market response is not externally owned")
        return self._acknowledge_external(request, index, now)

    def reconcile_external(self, ticket, index, now):
        request = self.entries[ticket]
        if (request.response_owner != "external_runtime" or request.processed_ns is None
                or request.outcome_reason != "accepted" or now < request.processed_ns):
            raise ValueError("historical entry observation requires an accepted external fill")
        if request.acknowledged:
            return False
        self.emit(request, index, now, "entry_reconciled", request.price, "history_attribution")
        request.acknowledged = True
        return True

    def acknowledge_external_close(self, request_id, index, now):
        return self._acknowledge_external(self._external_close(request_id), index, now)

    def _acknowledge_external(self, request, index, now):
        if request.processed_ns is None:
            raise ValueError("external response cannot precede processing")
        if now < request.processed_ns:
            raise ValueError("external response clock precedes processing")
        if request.acknowledged:
            return False
        self.emit(request, index, now, "entry_acknowledged" if request.entry else "close_acknowledged",
                  request.price, request.outcome_reason)
        request.acknowledgement_ns = now
        request.acknowledged = True
        return True

    def due_closes(self, index, now):
        return tuple(request for request in self.closes.values()
                     if request.processed_ns is None and index > request.index
                     and now >= request.requested_ns + self.profile.close_processing_delay_ms * 1_000_000)

    @property
    def entry_waiting(self):
        return any(not request.acknowledged for request in self.entries.values())

    @property
    def pending(self):
        return any(not request.acknowledged for request in self.requests)


def market_blockers(path, genome, execution):
    errors = []
    absolute = genome.stop_mode == "fixed_level" or genome.target_mode == "per_leg_levels"
    if absolute and (execution.protection is None or execution.protection.policy_extension not in {"absolute_levels_v1", "absolute_levels_be_v1"}
            or execution.market is None or execution.client is not None):
        errors.append("absolute_levels_require_explicit_market_profile")
    extended = execution.protection is not None and execution.protection.policy_extension == "own_rule_be_partial_v1"
    ordinal = execution.protection is not None and execution.protection.request_quote_binding == "timestamp_and_ordinal"
    basket = execution.protection is not None and execution.protection.policy_extension == "basket_guard_v1"
    guarded_terminal = (execution.client is not None
                        and execution.client.name == "single_basket_terminal_guard_v1"
                        and execution.protection.request_quote_binding == "timestamp_and_ordinal")
    if basket and (execution.market is None
                   or execution.client is not None and not guarded_terminal
                   or genome.schema_version != 2 or genome.entry_mode == "actual_mt5"):
        errors.append("basket_guard_requires_hypothetical_market")
    if (extended or ordinal and not basket) and (execution.market is None or execution.client is not None
                     or genome.schema_version != 2 or genome.entry_mode == "actual_mt5"
                     or genome.provider_management_mode != "ignore"):
        errors.append("protection_extension_requires_own_rule_market")
    if execution.market is None:
        return errors
    if execution.protection is None:
        errors.append("market_requires_protection_profile")
    if genome.schema_version != 2 or genome.entry_mode == "actual_mt5":
        errors.append("market_requires_hypothetical_schema2_entries")
    if extended and genome.target_mode == "partial_runner" and not genome.validation_errors():
        if genome.entry_ladder_mode != "simultaneous":
            errors.append("market_partial_ladder_unsupported")
        minimum, maximum, step = (Decimal(str(getattr(execution.market, "volume_" + name))) for name in ("min", "max", "step"))
        for volume in genome.volume_weights:
            whole = Decimal(str(volume))
            part = whole * Decimal(str(genome.partial_fraction))
            if any(value < minimum or value > maximum or value % step != 0 for value in (part, whole - part)):
                errors.append("market_partial_volume_unsupported")
                break
    return errors
