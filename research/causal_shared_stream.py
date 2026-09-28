"""Bounded diagnostic bridge from causal messages to shared market replay."""

from __future__ import annotations

from dataclasses import replace
from time import monotonic
from typing import Mapping

import numpy as np

from research.causal_canal1_stream import (
    CausalCanal1Stream, route_compiled_canal1_stream,
)
from research.causal_lifecycle import LifecycleTiming, signal_state_at
from research.causal_replay import CausalSignal, make_shared_paths, time_ns, utc
from research.causal_text_admission import CausalRouteSession
from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.engine import ExecutionAssumptions
from research.dubai_iterative.shared_replay import (
    BasketReplaySpec, SharedReplayProfile, simulate_shared,
)


def run_shared_canal1_stream(
    stream: CausalCanal1Stream, *, genomes: Mapping[str, StrategyGenome],
    executions: Mapping[str, ExecutionAssumptions], profile: SharedReplayProfile,
    timing: LifecycleTiming, market, conversion, start, cutoff,
    contract_size: float, currency_digits: int, max_fx_age_ms: int,
    market_sha256: str, conversion_sha256: str,
    other_signals: tuple[CausalSignal, ...] = (),
    initial_universe_complete: bool = False, max_replays: int = 128,
    max_wall_seconds: float = 1800,
):
    """Replay each accepted raw prefix on one shared transport and quote tape.

    This deliberately recomputes the world after every accepted message. It is
    bounded research, not a streaming production engine or live-parity proof.
    """
    if (not isinstance(stream, CausalCanal1Stream)
            or not isinstance(profile, SharedReplayProfile)
            or not isinstance(timing, LifecycleTiming)
            or type(initial_universe_complete) is not bool
            or type(max_replays) is not int or not 1 <= max_replays <= 256
            or type(max_wall_seconds) not in (int, float)
            or not 0 < max_wall_seconds <= 3600
            or any(signal.channel != "canal2" for signal in other_signals)
            or any(channel not in genomes or channel not in executions
                   for channel in {"canal1"} | {signal.channel for signal in other_signals})):
        raise ValueError("invalid shared causal stream contract")
    start, cutoff = utc(start), utc(cutoff)
    if not start < cutoff:
        raise ValueError("invalid shared causal stream window")
    quote_times = np.asarray(market[0])
    if quote_times.ndim != 1 or quote_times.dtype.kind not in "iu":
        raise ValueError("shared causal quote clock must be integer nanoseconds")
    for item in stream.timeline:
        stamp = time_ns(item.observed_at)
        index = int(np.searchsorted(quote_times, stamp))
        if index < len(quote_times) and int(quote_times[index]) == stamp:
            raise ValueError("same-clock message and quote order unresolved")
    admitted: dict[str, CausalSignal] = {}
    last_report = None
    latest = {}
    refresh_count = 0
    began = monotonic()

    def refresh(event_at=None):
        nonlocal last_report, latest, refresh_count
        if refresh_count >= max_replays or monotonic() - began > max_wall_seconds:
            raise TimeoutError("shared causal stream replay budget exhausted")
        signals = (*other_signals, *admitted.values())
        if not signals:
            latest, last_report = {}, None
            return
        paths = make_shared_paths(
            signals, genomes, start=start, market=market, conversion=conversion,
            cutoff=cutoff, contract_size=contract_size,
            currency_digits=currency_digits, max_fx_age_ms=max_fx_age_ms,
            market_sha256=market_sha256, conversion_sha256=conversion_sha256)
        specs = tuple(BasketReplaySpec(
            signal.channel, path, genomes[signal.channel], executions[signal.channel])
            for signal, path in zip(signals, paths))
        updated = simulate_shared(specs, profile=profile)
        if last_report is not None and event_at is not None:
            previous_ids = {signal_id for _, signal_id, _ in last_report.baskets}
            boundary = time_ns(event_at)
            previous_prefix = tuple(point for point in last_report.risk
                                    if point.time_ns < boundary)
            updated_prefix = tuple(point for point in updated.risk
                                   if point.signal_id in previous_ids
                                   and point.time_ns < boundary)
            if previous_prefix != updated_prefix:
                raise ValueError("noncausal shared replay risk prefix mutation")
        last_report = updated
        latest = {signal_id: result for _, signal_id, result in last_report.baskets}
        refresh_count += 1

    def replay_entry(signal):
        if signal.signal_id in admitted:
            raise ValueError("duplicate causal stream admission")
        admitted[signal.signal_id] = signal
        refresh(signal.observed_at)
        return latest.get(signal.signal_id)

    def apply_event(target_id, event):
        signal = admitted.get(target_id)
        if signal is None:
            return None
        admitted[target_id] = replace(
            signal, provider_events=signal.provider_events + (event,))
        refresh(event.observed_at)
        return latest.get(target_id)

    def apply_text_update(signal, prior):
        if len(signal.provider_events) != 1:
            return None
        return apply_event(prior.signal_id, signal.provider_events[0])

    def apply_management(message, prior):
        return apply_event(prior.signal_id, message.event)

    def resolve_state(signal_id, result, at):
        return signal_state_at(admitted[signal_id], genomes["canal1"], result, at, timing)

    def refresh_world():
        if (last_report is None or last_report.blockers
                or len(latest) != len(other_signals) + len(admitted)
                or any(result is None for result in latest.values())):
            return None
        return {signal_id: latest[signal_id] for signal_id in admitted}

    routed = route_compiled_canal1_stream(
        stream, replay_entry=replay_entry, apply_text_update=apply_text_update,
        apply_management=apply_management, resolve_state=resolve_state,
        refresh_world=refresh_world,
        initial_universe_complete=initial_universe_complete)
    if last_report is None and other_signals:
        refresh()
    return {"decisions": routed["decisions"], "shared": last_report,
            "refresh_count": refresh_count,
            "prior_universe_complete": routed["prior_universe_complete"],
            "status": ("diagnostic_only" if routed["prior_universe_complete"]
                       and last_report is not None and not last_report.blockers
                       else "blocked"),
            "full_live_parity_verified": False}


def run_incremental_shared_canal1_stream(
    stream: CausalCanal1Stream, *, genomes: Mapping[str, StrategyGenome],
    executions: Mapping[str, ExecutionAssumptions], profile: SharedReplayProfile,
    timing: LifecycleTiming, market, conversion, start, cutoff,
    contract_size: float, currency_digits: int, max_fx_age_ms: int,
    market_sha256: str, conversion_sha256: str,
    other_signals: tuple[CausalSignal, ...] = (),
    initial_universe_complete: bool = False, max_messages: int = 128,
    max_wall_seconds: float = 1800, idle_unresolved_ignored: bool = False,
):
    """Route messages into dormant baskets during one shared quote traversal."""
    if (not isinstance(stream, CausalCanal1Stream)
            or not isinstance(profile, SharedReplayProfile)
            or not isinstance(timing, LifecycleTiming)
            or type(initial_universe_complete) is not bool
            or type(max_messages) is not int or not 1 <= max_messages <= 256
            or type(max_wall_seconds) not in (int, float)
            or not 0 < max_wall_seconds <= 3600
            or len(stream.timeline) > max_messages
            or any(signal.channel != "canal2" for signal in other_signals)
            or any(channel not in genomes or channel not in executions
                   for channel in {"canal1"} | {signal.channel for signal in other_signals})):
        raise ValueError("invalid incremental shared causal stream contract")
    start, cutoff = utc(start), utc(cutoff)
    if not start < cutoff:
        raise ValueError("invalid shared causal stream window")
    quote_times = np.asarray(market[0])
    if quote_times.ndim != 1 or quote_times.dtype.kind not in "iu":
        raise ValueError("shared causal quote clock must be integer nanoseconds")
    for item in stream.timeline:
        stamp = time_ns(item.observed_at)
        index = int(np.searchsorted(quote_times, stamp))
        if index < len(quote_times) and int(quote_times[index]) == stamp:
            raise ValueError("same-clock message and quote order unresolved")
    candidates = (*other_signals, *stream.stickers, *stream.texts)
    other_events = tuple(sorted(
        ((signal.signal_id, event) for signal in other_signals
         for event in signal.provider_events),
        key=lambda row: time_ns(row[1].observed_at)))
    for _, event in other_events:
        stamp = time_ns(event.observed_at)
        index = int(np.searchsorted(quote_times, stamp))
        if (not start <= utc(event.observed_at) < cutoff
                or index < len(quote_times) and int(quote_times[index]) == stamp):
            raise ValueError("other-channel event outside causal shared quote window")
    message_times = (time_ns(item.observed_at) for item in stream.timeline)
    provider_times = (time_ns(event.observed_at) for _, event in other_events)
    source_quote_indices = tuple(sorted({
        int(np.searchsorted(quote_times, stamp, side="right"))
        for stamp in (*message_times, *provider_times)}))
    if source_quote_indices and source_quote_indices[-1] >= len(quote_times):
        raise ValueError("causal messages remain beyond shared quote tape")
    if not candidates:
        raise ValueError("incremental shared replay requires known candidate scopes")
    paths = make_shared_paths(
        tuple(replace(signal, provider_events=()) for signal in candidates),
        genomes, start=start, market=market, conversion=conversion,
        cutoff=cutoff, contract_size=contract_size,
        currency_digits=currency_digits, max_fx_age_ms=max_fx_age_ms,
        market_sha256=market_sha256, conversion_sha256=conversion_sha256)
    specs = tuple(BasketReplaySpec(
        signal.channel, path, genomes[signal.channel], executions[signal.channel])
        for signal, path in zip(candidates, paths))

    current_prefixes, admissions, deliveries = {}, [], []
    admitted_signals = {}
    router = None

    def replay_entry(signal):
        prefix = current_prefixes.get(signal.signal_id)
        if prefix is None:
            return None
        admissions.append(signal.signal_id)
        admitted_signals[signal.signal_id] = signal
        deliveries.extend((signal.signal_id, event) for event in signal.provider_events)
        return prefix

    def apply_event(target_id, event, prior):
        deliveries.append((target_id, event))
        admitted_signals[target_id] = replace(
            admitted_signals[target_id],
            provider_events=admitted_signals[target_id].provider_events + (event,))
        return prior

    def resolve_state(signal_id, result, at):
        return signal_state_at(admitted_signals[signal_id], genomes["canal1"],
                               result, at, timing)

    router = CausalRouteSession(
        replay_entry=replay_entry,
        apply_text_update=lambda signal, prior: apply_event(
            prior.signal_id, signal.provider_events[0], prior)
        if len(signal.provider_events) == 1 else None,
        apply_management=lambda message, prior: apply_event(
            prior.signal_id, message.event, prior),
        resolve_state=resolve_state,
        refresh_world=lambda: dict(router.prior_results),
        initial_universe_complete=initial_universe_complete,
        idle_unresolved_ignored=idle_unresolved_ignored)
    cursor = other_cursor = 0
    began = monotonic()

    def admit_at_quote(index, quote_ns, prior):
        nonlocal cursor, other_cursor
        if index % 4096 == 0 and monotonic() - began > max_wall_seconds:
            raise TimeoutError("incremental shared replay wall budget exhausted")
        current_prefixes.clear()
        current_prefixes.update(prior)
        previous_ns = int(quote_times[index - 1]) if index else None
        while other_cursor < len(other_events):
            signal_id, event = other_events[other_cursor]
            observed_ns = time_ns(event.observed_at)
            if observed_ns >= quote_ns:
                break
            if previous_ns is not None and observed_ns <= previous_ns:
                raise ValueError("late other-channel provider delivery")
            deliveries.append((signal_id, event))
            other_cursor += 1
        while cursor < len(stream.timeline):
            item = stream.timeline[cursor]
            observed_ns = time_ns(item.observed_at)
            if observed_ns >= quote_ns:
                break
            if previous_ns is not None and observed_ns <= previous_ns:
                raise ValueError("late channel-one message delivery")
            if monotonic() - began > max_wall_seconds:
                raise TimeoutError("incremental shared replay wall budget exhausted")
            prior_world = {signal_id: current_prefixes.get(signal_id)
                           for signal_id in router.prior_results}
            kind = "text" if item.kind == "text_candidate" else item.kind
            router.step(kind, item.value, prior_world=prior_world)
            cursor += 1
        result = tuple(admissions)
        admissions.clear()
        return result

    def provider_at_quote(_index, _quote_ns, _prior):
        result = tuple(deliveries)
        deliveries.clear()
        return result

    shared = simulate_shared(
        specs, profile=profile, basket_admission_source=admit_at_quote,
        initially_admitted=tuple(signal.signal_id for signal in other_signals),
        provider_event_source=provider_at_quote,
        source_quote_indices=source_quote_indices)
    if cursor != len(stream.timeline) or other_cursor != len(other_events):
        raise ValueError("causal messages remain beyond shared quote tape")
    routed = router.report()
    return {"decisions": routed["decisions"], "shared": shared,
            "refresh_count": 1,
            "prior_universe_complete": routed["prior_universe_complete"],
            "status": ("diagnostic_only" if routed["prior_universe_complete"]
                       and not shared.blockers else "blocked"),
            "full_live_parity_verified": False}
