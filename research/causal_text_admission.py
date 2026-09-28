"""Conservative channel-one text admission from raw receipts and replay state."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
import math

from parser import is_canal1_signal_text, parse_canal1_text
from provider_signal_catalog import _deterministic_management_semantics
from research.causal_lifecycle import PREFIX_SOURCE
from research.causal_replay import CausalSignal, raw_message, utc
from research.dubai_iterative.dataset import ProviderEvent


def _field(value, name):
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def candidate_text_entries(rows, *, start, cutoff, max_entry_age_s):
    """Retain fresh text-only candidates without treating level updates as entries."""
    start, cutoff = utc(start), utc(cutoff)
    if (not start < cutoff or type(max_entry_age_s) not in (int, float)
            or not math.isfinite(max_entry_age_s) or max_entry_age_s <= 0):
        raise ValueError("invalid text admission window or entry age")
    candidates, exclusions, seen = [], [], set()
    for row in sorted((raw_message(item) for item in rows), key=lambda item: utc(item["ts"])):
        if (row["channel"] != "canal1" or row["is_edit"] is True
                or row["sticker_id"] is not None
                or row["reply_to_msg_id"] is not None):
            continue
        observed = utc(row["ts"])
        if not start <= observed < cutoff:
            continue
        message_id = row["message_id"]
        if (type(message_id) is not int or message_id <= 0
                or message_id in seen):
            continue
        text = row["text"] or ""
        if not is_canal1_signal_text(text):
            continue
        parsed = parse_canal1_text(text)
        if (parsed.get("direction") not in {"BUY", "SELL"}
                or not (parsed.get("tps") or parsed.get("range"))):
            continue
        seen.add(message_id)
        signal_id = f"canal1_{message_id}"
        published = utc(row["date_utc"])
        if published > observed:
            exclusions.append({"signal_id": signal_id, "reason": "text_clock_inconsistent"})
        elif (observed - published).total_seconds() > max_entry_age_s:
            exclusions.append({"signal_id": signal_id, "reason": "stale_text_candidate"})
        elif not isinstance(row["message_revision_id"], str) or not row["message_revision_id"]:
            exclusions.append({"signal_id": signal_id, "reason": "text_revision_missing"})
        else:
            semantic = _deterministic_management_semantics(text)
            if (semantic is None or semantic.get("action") != "LEVEL_UPDATE"
                    or semantic.get("modality") != "direct"):
                exclusions.append({"signal_id": signal_id,
                                   "reason": "text_level_update_semantics_unresolved"})
                continue
            event = ProviderEvent(observed, semantic["action"], dict(semantic))
            candidates.append(CausalSignal(signal_id, "canal1", parsed["direction"],
                                           observed, published, row["message_revision_id"],
                                           (event,)))
    return tuple(candidates), exclusions


def open_volume_at(result, at):
    """Return remaining simulated volume, or None when timing/facts are uncertain."""
    at = utc(at)
    entries, exits, blockers = (_field(result, name)
                                for name in ("entries", "exits", "blockers"))
    if entries is None or exits is None or blockers is None or blockers:
        return None
    try:
        opened, remaining = {}, Decimal(0)
        for row in entries:
            ticket, time = _field(row, "ticket"), utc(_field(row, "opened_at"))
            volume = Decimal(str(_field(row, "volume")))
            if (not isinstance(ticket, str) or ticket in opened
                    or not volume.is_finite() or volume <= 0):
                return None
            opened[ticket] = (time, volume)
            if time == at:
                return None
            if time < at:
                remaining += volume
        closed = {}
        for row in exits:
            ticket, time = _field(row, "ticket"), utc(_field(row, "closed_at"))
            volume = Decimal(str(_field(row, "volume")))
            if (ticket not in opened or not volume.is_finite() or volume <= 0
                    or time < opened[ticket][0]):
                return None
            if _field(row, "reason") == "data_end" and time <= at:
                return None
            closed[ticket] = closed.get(ticket, Decimal(0)) + volume
            if closed[ticket] > opened[ticket][1] or time == at:
                return None
            if time < at:
                remaining -= volume
        return remaining if remaining >= 0 else None
    except (TypeError, ValueError, InvalidOperation):
        return None


def _route_prior_at(observed_at, prior_results, *, prior_universe_complete,
                    prior_states):
    if (not isinstance(prior_results, Mapping)
            or type(prior_universe_complete) is not bool
            or prior_states is not None and not isinstance(prior_states, Mapping)):
        raise ValueError("invalid channel-one prior-state inputs")
    observed_at = utc(observed_at)
    if not prior_universe_complete:
        return {"status": "blocked_incomplete_prior_universe", "open_signal_ids": []}
    open_ids, unknown_state, unknown_lifecycle = [], [], []
    for signal_id, result in prior_results.items():
        if not isinstance(signal_id, str) or not signal_id.startswith("canal1_"):
            raise ValueError("text admission prior signal identity invalid")
        volume = open_volume_at(result, observed_at)
        state = prior_states.get(signal_id) if prior_states is not None else None
        if state is not None:
            try:
                valid_state = (isinstance(state, Mapping)
                               and state.get("lifecycle_source") == PREFIX_SOURCE
                               and utc(state.get("observed_at")) == observed_at
                               and state.get("status") in {"open", "closed", "blocked"})
            except (TypeError, ValueError):
                valid_state = False
            if not valid_state:
                unknown_lifecycle.append(signal_id)
                continue
        if volume is None:
            unknown_state.append(signal_id)
        elif volume > 0:
            if state is not None and state["status"] != "open":
                unknown_lifecycle.append(signal_id)
            else:
                open_ids.append(signal_id)
        else:
            if state is None or state["status"] == "blocked":
                unknown_lifecycle.append(signal_id)
            elif state["status"] == "open":
                open_ids.append(signal_id)
    if unknown_state:
        status = "blocked_unknown_state"
    elif unknown_lifecycle:
        status = "blocked_unknown_lifecycle"
    elif len(open_ids) > 1:
        status = "blocked_multiple_open_signals"
    elif open_ids:
        status = "targets_open_signal"
    else:
        status = "eligible_text_fallback"
    return {"status": status, "open_signal_ids": sorted(open_ids)}


def route_text_candidate(candidate, prior_results, *, prior_universe_complete=False,
                         prior_states=None):
    """Resolve against a complete simulated signal history, never observed fills."""
    if candidate.channel != "canal1":
        raise ValueError("invalid channel-one text admission inputs")
    return _route_prior_at(candidate.observed_at, prior_results,
                           prior_universe_complete=prior_universe_complete,
                           prior_states=prior_states)


class CausalRouteSession:
    """Apply one message using only the prior settled basket world."""

    def __init__(self, *, replay_entry, apply_text_update=None,
                 apply_management=None, resolve_state=None, refresh_world=None,
                 initial_universe_complete=False, idle_unresolved_ignored=False):
        if (type(initial_universe_complete) is not bool or not callable(replay_entry)
                or type(idle_unresolved_ignored) is not bool
                or apply_text_update is not None and not callable(apply_text_update)
                or apply_management is not None and not callable(apply_management)
                or resolve_state is not None and not callable(resolve_state)
                or refresh_world is not None and not callable(refresh_world)):
            raise ValueError("invalid channel-one stream callbacks or opening state")
        self.replay_entry = replay_entry
        self.apply_text_update = apply_text_update
        self.apply_management = apply_management
        self.resolve_state = resolve_state
        self.refresh_world = refresh_world
        self.complete = initial_universe_complete
        # Opt-in: an unresolved provider text while no canal1 signal is open
        # (and none is in an unknown state) cannot change any basket; the live
        # bot ignores it. Without this the whole later universe is blocked.
        self.idle_unresolved_ignored = idle_unresolved_ignored
        self.prior_results = {}
        self.decisions = []
        self.message_roots = {}
        self.last_at = None
        self.signal_ids = set()

    def step(self, kind, signal, *, prior_world=None):
        if kind not in {"sticker", "text", "management", "unresolved",
                        "optional_hold", "optional_repeat"}:
            raise ValueError("invalid channel-one stream event kind")
        at = utc(signal.observed_at)
        if self.last_at is not None and at <= self.last_at:
            raise ValueError("channel-one stream out of order")
        if kind in {"sticker", "text"}:
            if (signal.channel != "canal1" or signal.signal_id in self.signal_ids):
                raise ValueError("ambiguous channel-one stream identity")
        elif (type(signal.message_id) is not int or signal.message_id <= 0
              or not isinstance(signal.message_revision_id, str)
              or not signal.message_revision_id):
            raise ValueError("invalid channel-one message identity")
        if prior_world is not None:
            if (not isinstance(prior_world, Mapping)
                    or set(prior_world) != set(self.prior_results)
                    or any(value is not None and _field(value, "signal_id") != signal_id
                           for signal_id, value in prior_world.items())):
                raise ValueError("channel-one prior world incomplete")
            self.prior_results = dict(prior_world)
        if kind in {"sticker", "text"}:
            self.signal_ids.add(signal.signal_id)
        self.last_at = at

        if not self.complete:
            decision = {"status": "blocked_incomplete_prior_universe",
                        "open_signal_ids": []}
        elif kind == "sticker":
            decision = {"status": "sticker_entry", "open_signal_ids": []}
        elif kind == "optional_hold":
            decision = {"status": "optional_management_held", "open_signal_ids": []}
        elif kind == "optional_repeat":
            decision = {"status": "optional_management_repeated", "open_signal_ids": []}
        elif kind == "unresolved":
            decision = {"status": "blocked_unresolved_provider_event",
                        "open_signal_ids": [], "reason": signal.reason}
            if self.idle_unresolved_ignored:
                states = ({signal_id: self.resolve_state(signal_id, result, at)
                           for signal_id, result in self.prior_results.items()}
                          if self.resolve_state is not None else None)
                route = _route_prior_at(at, self.prior_results,
                                        prior_universe_complete=True,
                                        prior_states=states)
                if route["status"] == "eligible_text_fallback":
                    decision = {"status": "unresolved_ignored_no_open_signal",
                                "open_signal_ids": [], "reason": signal.reason}
        else:
            states = ({signal_id: self.resolve_state(signal_id, result, at)
                       for signal_id, result in self.prior_results.items()}
                      if self.resolve_state is not None else None)
            if kind == "text":
                decision = route_text_candidate(
                    signal, self.prior_results, prior_universe_complete=True,
                    prior_states=states)
            else:
                route = _route_prior_at(at, self.prior_results,
                                        prior_universe_complete=True,
                                        prior_states=states)
                reply = signal.reply_to_msg_id
                target = self.message_roots.get(reply) if reply is not None else None
                if reply is not None and target is None:
                    decision = {"status": "blocked_unknown_management_target",
                                "open_signal_ids": route["open_signal_ids"]}
                elif route["status"] in {"blocked_unknown_state", "blocked_unknown_lifecycle"}:
                    decision = route
                elif reply is not None:
                    decision = ({"status": "management_targets_signal",
                                 "open_signal_ids": route["open_signal_ids"],
                                 "target_signal_id": target}
                                if target in route["open_signal_ids"] else
                                {"status": "blocked_management_target_not_open",
                                 "open_signal_ids": route["open_signal_ids"]})
                elif route["status"] == "targets_open_signal":
                    decision = {"status": "management_targets_signal",
                                "open_signal_ids": route["open_signal_ids"],
                                "target_signal_id": route["open_signal_ids"][0]}
                elif route["status"] == "eligible_text_fallback":
                    decision = {"status": "management_no_open_signal",
                                "open_signal_ids": []}
                else:
                    decision = route

        status = decision["status"]
        if status in {"sticker_entry", "eligible_text_fallback"}:
            result = self.replay_entry(signal)
            if result is None:
                decision = {"status": "blocked_entry_replay_missing",
                            "open_signal_ids": []}
            else:
                self.prior_results[signal.signal_id] = result
                self.message_roots[int(signal.signal_id.split("_")[1])] = signal.signal_id
        elif status == "targets_open_signal":
            target = decision["open_signal_ids"][0]
            updated = (self.apply_text_update(signal, self.prior_results[target])
                       if self.apply_text_update is not None else None)
            if updated is None:
                decision = {"status": "blocked_text_update_unmodeled",
                            "open_signal_ids": [target]}
            else:
                self.prior_results[target] = updated
                self.message_roots[int(signal.signal_id.split("_")[1])] = target
        elif status == "management_targets_signal":
            target = decision["target_signal_id"]
            updated = (self.apply_management(signal, self.prior_results[target])
                       if self.apply_management is not None else None)
            if updated is None:
                decision = {"status": "blocked_management_update_unmodeled",
                            "open_signal_ids": decision["open_signal_ids"],
                            "target_signal_id": target}
            else:
                self.prior_results[target] = updated
                self.message_roots[signal.message_id] = target
        if (decision["status"] in {"sticker_entry", "eligible_text_fallback",
                                   "targets_open_signal", "management_targets_signal"}
                and self.refresh_world is not None):
            world = self.refresh_world()
            if (not isinstance(world, Mapping)
                    or set(world) != set(self.prior_results)
                    or any(_field(value, "signal_id") != signal_id
                           for signal_id, value in world.items())):
                decision = {"status": "blocked_shared_world_incomplete",
                            "open_signal_ids": decision["open_signal_ids"]}
            else:
                self.prior_results = dict(world)
        if decision["status"].startswith("blocked_"):
            self.complete = False
        identity = ({"message_id": signal.message_id,
                     "message_revision_id": signal.message_revision_id}
        if kind in {"management", "unresolved", "optional_hold",
                    "optional_repeat"}
                    else {"signal_id": signal.signal_id})
        recorded = {**identity, **decision}
        self.decisions.append(recorded)
        return recorded

    def report(self):
        return {"decisions": list(self.decisions), "results": dict(self.prior_results),
                "prior_universe_complete": self.complete}


def route_canal1_stream(
    stickers, texts, *, replay_entry, management=(), unresolved=(),
    optional_holds=(), optional_repeats=(), apply_text_update=None,
    apply_management=None, resolve_state=None, refresh_world=None,
    initial_universe_complete=False,
):
    """Route one scenario's causal entries and management through callbacks.

    Callbacks must use only the raw prefix and market observations available at
    each event. A missed decision invalidates later text admission in this
    branch; no observed MT5 fill may be used to repair the history.
    """
    if (type(initial_universe_complete) is not bool or not callable(replay_entry)
            or apply_text_update is not None and not callable(apply_text_update)
            or apply_management is not None and not callable(apply_management)
            or resolve_state is not None and not callable(resolve_state)
            or refresh_world is not None and not callable(refresh_world)):
        raise ValueError("invalid channel-one stream callbacks or opening state")
    events = [(row.observed_at, "sticker", row) for row in stickers]
    events.extend((row.observed_at, "text", row) for row in texts)
    events.extend((row.observed_at, "management", row) for row in management)
    events.extend((row.observed_at, "unresolved", row) for row in unresolved)
    events.extend((row.observed_at, "optional_hold", row) for row in optional_holds)
    events.extend((row.observed_at, "optional_repeat", row)
                  for row in optional_repeats)
    events.sort(key=lambda item: utc(item[0]))
    if (any(row.channel != "canal1" for _, kind, row in events
            if kind in {"sticker", "text"})
            or len({row.signal_id for _, kind, row in events
                    if kind in {"sticker", "text"}}) != sum(
                        kind in {"sticker", "text"} for _, kind, _ in events)
            or any(type(row.message_id) is not int or row.message_id <= 0
                   or not isinstance(row.message_revision_id, str)
                   or not row.message_revision_id
                   for _, kind, row in events
                   if kind in {"management", "unresolved", "optional_hold",
                               "optional_repeat"})
            or any(utc(left[0]) == utc(right[0])
                   for left, right in zip(events, events[1:]))):
        raise ValueError("ambiguous channel-one stream identity or event order")

    session = CausalRouteSession(
        replay_entry=replay_entry, apply_text_update=apply_text_update,
        apply_management=apply_management, resolve_state=resolve_state,
        refresh_world=refresh_world,
        initial_universe_complete=initial_universe_complete)
    for _, kind, signal in events:
        session.step(kind, signal)
    return session.report()
