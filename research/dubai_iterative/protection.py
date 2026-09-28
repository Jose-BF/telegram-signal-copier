"""Scalar quote-clock protection lifecycle, separate from policy intent."""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from .protection_contract import ProtectionEvent, ProtectionProfile


class ProtectionBlocked(ValueError):
    pass


@dataclass
class _Installed:
    sl: float | None
    tp: float | None
    sl_reason: str = "initial_sl"
    tp_reason: str = "initial_tp"
    pending: tuple | None = None
    ack_ns: int | None = None
    retry_ns: int = 0
    closed: bool = False
    pending_passive: tuple[int, float, str] | None = None


class ProtectionBook:
    def __init__(self, profile: ProtectionProfile, direction: str):
        self.profile = profile
        self.sign = 1 if direction == "BUY" else -1
        self.states: dict[str, _Installed] = {}
        self.events: list[ProtectionEvent] = []
        self.sequence = 0
        self.drain_closed_responses = False

    def emit(self, ticket, index, now, request_id, kind, sl, tp, reason):
        if len(self.events) >= self.profile.max_events:
            raise ProtectionBlocked("protection_event_budget_exhausted")
        self.events.append(ProtectionEvent(ticket, index, now, request_id, kind, sl, tp, reason))

    def rounded(self, value):
        if value is None:
            return None
        return float(Decimal(str(value)).quantize(Decimal(str(self.profile.point)), rounding=ROUND_HALF_UP))

    def valid(self, quote, sl, tp):
        quote_d = Decimal(str(quote))
        minimum = Decimal(str(self.profile.point)) * self.profile.stops_level_points
        for level, sign in ((sl, self.sign), (tp, -self.sign)):
            if level is None:
                continue
            distance = sign * (quote_d - Decimal(str(level)))
            if level <= 0 or distance <= 0 or distance < minimum:
                return False
        return True

    def open(self, ticket, index, now, sl, tp, source):
        self.emit(ticket, index, now, 0, "open", sl, tp, source)
        self.states[ticket] = _Installed(sl, tp)

    def hit(self, ticket, quote, *, index=None, now=None):
        state = self.states[ticket]
        scenario = self.profile.passive_fill_scenario
        if scenario is not None and (index is None or now is None):
            raise ValueError("passive scenario requires quote index and timestamp")
        if state.pending_passive is not None:
            # This stress world treats the first terminal touch as committed.
            due_ns, level, reason = state.pending_passive
            return (reason, level) if now is not None and now >= due_ns else None
        if state.sl is not None and self.sign * (quote - state.sl) <= 0:
            return state.sl_reason, None
        if state.tp is not None and self.sign * (quote - state.tp) >= 0:
            if scenario is not None:
                self.emit(ticket, index, now, 0, "touched", state.sl, state.tp,
                          state.tp_reason)
                if scenario.delay_ns:
                    state.pending_passive = (now + scenario.delay_ns,
                                             state.tp, state.tp_reason)
                    return None
            return state.tp_reason, state.tp
        return None

    def close(self, ticket, index, now, reason):
        state = self.states[ticket]
        self.emit(ticket, index, now, 0, "closed", state.sl, state.tp, reason)
        state.closed = True
        state.pending_passive = None

    def process(self, index, now, quote):
        for ticket, state in self.states.items():
            if state.pending is None or state.closed and not self.drain_closed_responses:
                continue
            request_id, sent_index, sent_ns, sl, tp, sl_reason, tp_reason = state.pending
            if state.ack_ns is None and index > sent_index and now >= sent_ns + self.profile.processing_delay_ms * 1_000_000:
                accepted = (not state.closed and state.pending_passive is None
                            and self.valid(quote, sl, tp))
                self.emit(ticket, index, now, request_id, "installed" if accepted else "rejected",
                          sl, tp, "accepted" if accepted else
                          "position_already_closed" if state.closed else
                          "passive_fill_pending" if state.pending_passive is not None
                          else "invalid_stops")
                if accepted:
                    state.sl, state.tp = sl, tp
                    state.sl_reason, state.tp_reason = sl_reason, tp_reason
                state.ack_ns = now + self.profile.acknowledgement_delay_ms * 1_000_000
            self._acknowledge_state(ticket, state, index, now)

    def acknowledge(self, index, now):
        for ticket, state in self.states.items():
            self._acknowledge_state(ticket, state, index, now)

    def _acknowledge_state(self, ticket, state, index, now):
        if state.closed and not self.drain_closed_responses:
            return
        if state.pending is not None and state.ack_ns is not None and now >= state.ack_ns:
            request_id = state.pending[0]
            self.emit(ticket, index, now, request_id, "acknowledged", state.sl, state.tp, "response_available")
            state.pending = None
            state.ack_ns = None
            state.retry_ns = now + self.profile.retry_delay_ms * 1_000_000

    def request(self, ticket, index, now, sl, tp, sl_reason, tp_reason):
        state = self.states[ticket]
        sl, tp = self.rounded(sl), self.rounded(tp)
        if state.pending is not None or now < state.retry_ns or (state.sl == sl and state.tp == tp):
            return
        request_id = self.sequence + 1
        self.emit(ticket, index, now, request_id, "requested", sl, tp, "policy_intent")
        self.sequence = request_id
        state.pending = (request_id, index, now, sl, tp, sl_reason, tp_reason)


def profile_blockers(path, genome, profile):
    if profile is None:
        return []
    errors = []
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
    # basket_money here = client-side basket loss cap closed with market orders
    # (per-leg broker SL/TP stay as usual); the fast engine requires a market profile.
    elif (genome.stop_mode not in {"none", "fixed_move", "basket_money"}
            or genome.target_mode not in ({"none", "per_leg_steps", "partial_runner"} if extended else {"none", "per_leg_steps"})
            or genome.be_mode not in ({"none", "price"} if extended else {"none"})):
        errors.append("protection_policy_unsupported")
    if genome.entry_mode != "actual_mt5" and profile.initial_protections:
        errors.append("observed_protection_requires_actual_entries")
    if genome.entry_mode == "actual_mt5":
        if genome.entry_ladder_mode != "simultaneous":
            errors.append("protection_actual_entries_require_simultaneous_schedule")
        initial = {row.ticket: row for row in profile.initial_protections}
        for leg in path.legs[:genome.leg_count]:
            if leg.ticket not in initial:
                errors.append(f"initial_protection_missing:{leg.ticket}")
        if genome.leg_count != len(path.legs):
            errors.append("protection_actual_entry_shape_required")
    return errors
