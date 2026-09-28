"""Explicit, falsifiable signal-finalization timing hypotheses for replay.

The projection uses only simulated fills, exits and a frozen policy. Runtime
signal_closed events are outcome-side controls and must not enter this module.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation
import math
import re

from research.causal_replay import CausalSignal, time_ns, utc
from research.dubai_iterative.client_contract import ClientEvent
from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.market_contract import MarketEvent


EXPLICIT_TERMINALS = frozenset({
    "basket_stop", "profit_lock", "time_exit", "provider_close",
    "basket_target", "fixed_move_target", "runner_target",
})
AUTOMATIC_FLAT_EXITS = frozenset({
    "per_leg_target", "provider_tp", "native_tp", "native_sl",
    "hard_stop_per_leg", "break_even", "trailing_stop",
})
SOURCE = "counterfactual_policy_clock_v1"
PREFIX_SOURCE = "counterfactual_policy_prefix_v1"


@dataclass(frozen=True)
class LifecycleTiming:
    finalization_delay_s: float
    flat_grace_s: float = 30.0

    def __post_init__(self):
        for name, maximum in (("finalization_delay_s", 3600), ("flat_grace_s", 300)):
            value = getattr(self, name)
            if (type(value) not in (int, float) or not math.isfinite(value)
                    or not 0 <= value <= maximum):
                raise ValueError(f"invalid {name} lifecycle timing")


def _field(value, name):
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def _blocked(reason):
    return {"status": reason, "signal_closed_at": None, "lifecycle_source": None}


def _execution_events_settled(result, at):
    """Prove no request remains pending and return the latest settled clock."""
    market_events = _field(result, "market_events")
    client_events = _field(result, "client_events")
    if market_events is None and client_events is None:
        return True, None
    if (market_events is None or client_events is None
            or not isinstance(market_events, (tuple, list))
            or not isinstance(client_events, (tuple, list))):
        return False, None
    boundary = time_ns(at)
    requests = {}
    previous_ns = -1
    for event in market_events:
        if (not isinstance(event, MarketEvent)
                or type(event.timestamp_ns) is not int
                or not previous_ns <= event.timestamp_ns < boundary
                or type(event.request_id) is not int or event.request_id <= 0
                or not isinstance(event.ticket, str) or not event.ticket):
            return False, None
        previous_ns = event.timestamp_ns
        kind = event.kind
        if kind in {"entry_requested", "close_requested"}:
            if event.request_id in requests:
                return False, None
            requests[event.request_id] = (kind.split("_")[0], event.ticket, "requested")
            continue
        previous = requests.get(event.request_id)
        if previous is None or previous[1] != event.ticket:
            return False, None
        operation, ticket, stage = previous
        if kind in {operation + "_filled", operation + "_rejected"} and stage == "requested":
            requests[event.request_id] = (operation, ticket, "processed")
        elif kind in {operation + "_acknowledged", operation + "_reconciled"} and stage == "processed":
            requests[event.request_id] = (operation, ticket, "settled")
        else:
            return False, None
    if any(stage != "settled" for _, _, stage in requests.values()):
        return False, None

    actions = {}
    previous_ns = -1
    terminal_requested = False
    for event in client_events:
        if (not isinstance(event, ClientEvent)
                or type(event.time_ns) is not int
                or not previous_ns <= event.time_ns < boundary
                or type(event.decision_ns) is not int
                or event.decision_ns > event.time_ns
                or event.operation not in {"entry", "modify", "close"}
                or not isinstance(event.ticket, str) or not event.ticket):
            return False, None
        previous_ns = event.time_ns
        if event.kind == "terminal_requested":
            if (terminal_requested or event.operation != "close"
                    or event.ticket != "basket"):
                return False, None
            terminal_requested = True
            continue
        key = (event.operation, event.ticket)
        stage = actions.get(key)
        if event.kind == "queued" and stage is None:
            actions[key] = "queued"
        elif event.kind == "intent_updated" and stage == "queued":
            continue
        elif event.kind == "started" and stage == "queued":
            actions[key] = "started"
        elif event.kind == "released" and stage == "started":
            del actions[key]
        elif event.kind == "cancelled" and stage == "queued":
            del actions[key]
        else:
            return False, None
    if actions:
        return False, None
    last_market = market_events[-1].timestamp_ns if market_events else None
    last_client = client_events[-1].time_ns if client_events else None
    return True, max((value for value in (last_market, last_client)
                      if value is not None), default=None)


def project_signal_closure(signal, genome, result, timing):
    """Project a closure hypothesis only for a fully closed simulated path."""
    if (not isinstance(signal, CausalSignal) or not isinstance(genome, StrategyGenome)
            or not isinstance(timing, LifecycleTiming)):
        raise ValueError("typed signal, policy and lifecycle timing required")
    if (_field(result, "signal_id") != signal.signal_id
            or _field(result, "strategy_fingerprint") != genome.fingerprint):
        return _blocked("blocked_identity")
    entries, exits, blockers = (_field(result, name)
                                for name in ("entries", "exits", "blockers"))
    if entries is None or exits is None or blockers is None or blockers:
        return _blocked("blocked_simulation_path")
    if not entries:
        return _blocked("blocked_no_fill")
    if not exits:
        return _blocked("blocked_open_path")
    if any(_field(row, "reason") == "data_end" for row in exits):
        return _blocked("blocked_censored_exit")
    try:
        opened, closed, times, filled_indexes = {}, {}, [], set()
        for row in entries:
            ticket = _field(row, "ticket")
            volume = Decimal(str(_field(row, "volume")))
            stamp = utc(_field(row, "opened_at"))
            if (not isinstance(ticket, str) or ticket in opened
                    or not volume.is_finite() or volume <= 0):
                return _blocked("blocked_invalid_path")
            match = re.fullmatch(r"sim_(\d+)|sim_ladder_(\d+)", ticket)
            index = int(next(group for group in match.groups() if group)) - 1 if match else -1
            if (index < 0 or index >= genome.leg_count or index in filled_indexes
                    or volume != Decimal(str(genome.volume_weights[index]))):
                return _blocked("blocked_entry_plan_identity")
            filled_indexes.add(index)
            opened[ticket] = (stamp, volume)
        for row in exits:
            ticket = _field(row, "ticket")
            volume = Decimal(str(_field(row, "volume")))
            stamp = utc(_field(row, "closed_at"))
            reason = _field(row, "reason")
            if (ticket not in opened or not volume.is_finite() or volume <= 0
                    or stamp < opened[ticket][0]):
                return _blocked("blocked_invalid_path")
            if reason not in EXPLICIT_TERMINALS | AUTOMATIC_FLAT_EXITS:
                return _blocked("blocked_unknown_terminal_cause")
            closed[ticket] = closed.get(ticket, Decimal(0)) + volume
            if closed[ticket] > opened[ticket][1]:
                return _blocked("blocked_invalid_path")
            times.append((stamp, reason))
        if any(closed.get(ticket, Decimal(0)) != volume
               for ticket, (_, volume) in opened.items()):
            return _blocked("blocked_open_path")
    except (TypeError, ValueError, InvalidOperation):
        return _blocked("blocked_invalid_path")

    last_exit = max(stamp for stamp, _ in times)
    last_reasons = {reason for stamp, reason in times if stamp == last_exit}
    if last_reasons <= EXPLICIT_TERMINALS:
        base = last_exit
        terminal_kind = "explicit_terminal"
    elif last_reasons <= AUTOMATIC_FLAT_EXITS and not any(
            reason in EXPLICIT_TERMINALS for _, reason in times):
        first_entry = min(stamp for stamp, _ in opened.values())
        base = max(last_exit, first_entry + timedelta(seconds=timing.flat_grace_s))
        if genome.pending_entry_policy == "until_expiry":
            if len(filled_indexes) < genome.leg_count:
                anchor = signal.published_at if signal.channel == "canal2" else signal.observed_at
                base = max(base, utc(anchor) + timedelta(minutes=genome.entry_expiry_min))
                terminal_kind = "automatic_flat_after_expiry"
            else:
                terminal_kind = "automatic_flat_all_entries_settled"
        elif genome.pending_entry_policy == "none":
            terminal_kind = "automatic_flat_no_pending"
        else:
            return _blocked("blocked_unknown_pending_policy")
    else:
        return _blocked("blocked_mixed_terminal_causes")
    closed_at = base + timedelta(seconds=timing.finalization_delay_s)
    return {"status": "modeled_lifecycle_hypothesis",
            "signal_closed_at": closed_at.isoformat(),
            "lifecycle_source": SOURCE,
            "terminal_kind": terminal_kind,
            "finalization_delay_s": timing.finalization_delay_s,
            "flat_grace_s": timing.flat_grace_s}


def signal_state_at(signal, genome, result, at, timing):
    """Resolve one causal prefix; future fills/exits cannot prove an admission."""
    if (not isinstance(signal, CausalSignal) or not isinstance(genome, StrategyGenome)
            or not isinstance(timing, LifecycleTiming)):
        raise ValueError("typed signal, policy and lifecycle timing required")
    at = utc(at)

    def state(status, reason):
        return {"status": status, "reason": reason,
                "observed_at": at.isoformat(), "lifecycle_source": PREFIX_SOURCE}

    if (_field(result, "signal_id") != signal.signal_id
            or _field(result, "strategy_fingerprint") != genome.fingerprint):
        return state("blocked", "identity_mismatch")
    entries, exits, blockers = (_field(result, name)
                                for name in ("entries", "exits", "blockers"))
    if entries is None or exits is None or blockers is None or blockers:
        return state("blocked", "simulation_path_incomplete")
    try:
        before_entries, before_exits, future_entries = [], [], []
        for row in entries:
            stamp = utc(_field(row, "opened_at"))
            if stamp == at:
                return state("blocked", "same_time_entry_order_unknown")
            (before_entries if stamp < at else future_entries).append(row)
        for row in exits:
            stamp = utc(_field(row, "closed_at"))
            if stamp == at:
                return state("blocked", "same_time_exit_order_unknown")
            if stamp < at:
                before_exits.append(row)
        if not before_entries:
            if at <= signal.observed_at:
                return state("blocked", "signal_registration_order_unknown")
            if before_exits:
                return state("blocked", "exit_without_prior_fill")
            settled, _ = _execution_events_settled(result, at)
            if not settled:
                return state("blocked", "pending_execution_requires_event_replay")
            anchor = signal.published_at if signal.channel == "canal2" else signal.observed_at
            expiry_at = utc(anchor) + timedelta(minutes=genome.entry_expiry_min)
            if at < expiry_at:
                return state("open", "registered_without_fill_before_expiry")
            return state("blocked", "unfilled_finalization_unknown")
        opened = {_field(row, "ticket"): (
            utc(_field(row, "opened_at")), Decimal(str(_field(row, "volume"))))
                  for row in before_entries}
        if len(opened) != len(before_entries) or any(
                not volume.is_finite() or volume <= 0 for _, volume in opened.values()):
            return state("blocked", "invalid_prefix_entries")
        remaining_by_ticket = {ticket: volume for ticket, (_, volume) in opened.items()}
        for row in before_exits:
            ticket = _field(row, "ticket")
            volume = Decimal(str(_field(row, "volume")))
            stamp = utc(_field(row, "closed_at"))
            if (ticket not in opened or not volume.is_finite() or volume <= 0
                    or stamp < opened[ticket][0]
                    or _field(row, "reason") == "data_end"
                    or volume > remaining_by_ticket[ticket]):
                return state("blocked", "invalid_prefix_exits")
            remaining_by_ticket[ticket] -= volume
        remaining = sum(remaining_by_ticket.values(), Decimal(0))
        if remaining > 0:
            return state("open", "simulated_position_open")
    except (TypeError, ValueError, InvalidOperation):
        return state("blocked", "invalid_prefix_path")

    settled, settled_ns = _execution_events_settled(result, at)
    if not settled:
        return state("blocked", "pending_execution_requires_event_replay")
    prefix = {"signal_id": signal.signal_id,
              "strategy_fingerprint": genome.fingerprint,
              "entries": before_entries, "exits": before_exits, "blockers": ()}
    closure = project_signal_closure(signal, genome, prefix, timing)
    if closure["status"] != "modeled_lifecycle_hypothesis":
        return state("blocked", closure["status"])
    closed_ns = time_ns(utc(closure["signal_closed_at"]))
    if settled_ns is not None:
        closed_ns = max(closed_ns, settled_ns + int(timing.finalization_delay_s * 1_000_000_000))
    at_ns = time_ns(at)
    if closed_ns == at_ns:
        return state("blocked", "same_time_finalization_order_unknown")
    if closed_ns < at_ns and future_entries:
        return state("blocked", "future_entry_conflicts_with_projected_closure")
    return state("closed" if closed_ns < at_ns else "open", closure["terminal_kind"])
