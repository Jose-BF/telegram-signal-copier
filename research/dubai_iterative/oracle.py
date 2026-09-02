"""Independent scalar replay oracle for Dubai strategy finalists.

This module deliberately does not import the fast engine. It repeats entry,
state-transition and money decisions from immutable paths so shared mistakes
cannot certify themselves.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import math
import re
from typing import Iterable, Sequence

from .contracts import StrategyGenome
from .dataset import DubaiPath, LevelEvent, ProviderEvent


@dataclass(frozen=True)
class ExecutionScenario:
    name: str = "base"
    entry_slippage: float = 0.0
    exit_slippage: float = 0.0
    spread_addition: float = 0.0
    latency_ms: int = 0

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("scenario name cannot be empty")
        for field_name in ("entry_slippage", "exit_slippage", "spread_addition"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not math.isfinite(float(value)) or value < 0:
                raise ValueError(f"{field_name} must be finite and non-negative")
        if isinstance(self.latency_ms, bool) or not isinstance(self.latency_ms, int) or self.latency_ms < 0:
            raise ValueError("latency_ms must be a non-negative integer")


@dataclass(frozen=True)
class OracleEntry:
    ticket: str
    tick_index: int
    opened_at: datetime
    entry_price: float
    volume: float
    source: str


@dataclass(frozen=True)
class OracleExit:
    ticket: str
    tick_index: int
    closed_at: datetime
    entry_price: float
    exit_price: float
    volume: float
    pnl_eur: Decimal | None
    reason: str


@dataclass(frozen=True)
class OracleResult:
    signal_id: str
    strategy_fingerprint: str
    confidence_layer: str
    entries: tuple[OracleEntry, ...]
    exits: tuple[OracleExit, ...]
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


@dataclass(frozen=True)
class OracleMismatch:
    signal_id: str
    field: str
    fast_value: object
    oracle_value: object


@dataclass(frozen=True)
class OracleCertificate:
    status: str
    mismatches: tuple[OracleMismatch, ...]
    oracle_results: tuple[OracleResult, ...]
    promotion_eligible: bool


@dataclass(frozen=True)
class StressScenarioResult:
    scenario: ExecutionScenario
    net_eur: Decimal | None
    blockers: tuple[str, ...]
    results: tuple[OracleResult, ...]


@dataclass(frozen=True)
class StressReport:
    base_net_eur: Decimal | None
    base_blockers: tuple[str, ...]
    scenarios: tuple[StressScenarioResult, ...]
    promotion_eligible: bool


@dataclass
class _Position:
    ticket: str
    role: str
    leg_index: int
    volume: float
    entry_price: float
    opened_ns: int
    tp_events: tuple[LevelEvent, ...]
    sl_events: tuple[LevelEvent, ...]
    be_stop: float | None = None
    be_reason: str = "break_even"
    trailing_stop: float | None = None
    accrued_swap_minor: int = 0


@dataclass(frozen=True)
class _Scheduled:
    index: int
    source: str
    position: _Position


def oracle_simulate(
    path: DubaiPath,
    genome: StrategyGenome,
    *,
    execution: ExecutionScenario | None = None,
) -> OracleResult:
    execution = execution or ExecutionScenario()
    observation_latency_ns = execution.latency_ms * 1_000_000
    blockers = list(genome.validation_errors())
    blockers.extend(_path_errors(path))
    if blockers:
        return _empty(path, genome, blockers)

    scheduled, confidence, entry_errors = _schedule_entries(path, genome, execution)
    blockers.extend(entry_errors)
    if blockers:
        return _empty(path, genome, blockers, confidence=confidence)
    if not scheduled:
        return _empty(path, genome, (), confidence=confidence, unfilled=True)

    active: list[_Position] = []
    entries: list[OracleEntry] = []
    exits: list[OracleExit] = []
    schedule_cursor = 0
    provider_events = tuple(sorted(path.provider_events, key=lambda item: _to_ns(item.observed_at)))
    provider_cursor = 0
    rollover_events = tuple(
        sorted(path.rollover_events, key=lambda item: _to_ns(item.observed_at))
    )
    rollover_cursor = 0
    rollover_blocked = False
    realized_minor = 0
    unknown_money = False
    partial_taken = False
    lock_armed = False
    high_minor: int | None = None
    low_minor: int | None = None
    drawdown_minor = 0
    favourable_move = 0.0
    adverse_move = 0.0
    last_index = -1
    reason = "not_closed"
    first_open_ns: int | None = None

    for index in range(len(path.times_ns)):
        now_ns = int(path.times_ns[index])
        while schedule_cursor < len(scheduled) and scheduled[schedule_cursor].index == index:
            item = scheduled[schedule_cursor]
            active.append(item.position)
            first_open_ns = item.position.opened_ns if first_open_ns is None else min(first_open_ns, item.position.opened_ns)
            entries.append(OracleEntry(
                ticket=item.position.ticket,
                tick_index=index,
                opened_at=_from_ns(item.position.opened_ns),
                entry_price=item.position.entry_price,
                volume=item.position.volume,
                source=item.source,
            ))
            schedule_cursor += 1

        while (
            rollover_cursor < len(rollover_events)
            and _to_ns(rollover_events[rollover_cursor].observed_at) <= now_ns
        ):
            event = rollover_events[rollover_cursor]
            rollover_cursor += 1
            event_ns = _to_ns(event.observed_at)
            rollover_positions = [
                position
                for position in active
                if position.opened_ns < event_ns
            ]
            if not rollover_positions:
                continue
            if event.blocker:
                blockers.append(event.blocker)
                unknown_money = True
                rollover_blocked = True
                last_index = index
                break
            for position in rollover_positions:
                units = _swap_volume_units(position.volume)
                if units is None or units >= len(event.minor_by_volume_unit):
                    blockers.append("swap_volume_unsupported")
                    unknown_money = True
                    rollover_blocked = True
                    last_index = index
                    break
                position.accrued_swap_minor += int(
                    event.minor_by_volume_unit[units]
                )
            if rollover_blocked:
                break
        if rollover_blocked:
            break

        if not active:
            if schedule_cursor >= len(scheduled) and entries:
                break
            continue
        if not _usable_tick(path, index):
            blockers.append(f"invalid_tick_at_index:{index}")
            last_index = index
            break
        last_index = index
        raw_exit = float(path.exit_quotes[index])

        for position in active:
            move = _sign(path.direction) * (raw_exit - position.entry_price)
            favourable_move = max(favourable_move, move)
            adverse_move = min(adverse_move, move)
            _custom_be(position, genome, move, now_ns)

        floating_minor, exact = _basket_money(path, active, index, execution)
        total_minor = realized_minor + floating_minor
        if exact and not unknown_money:
            high_minor = total_minor if high_minor is None else max(high_minor, total_minor)
            low_minor = total_minor if low_minor is None else min(low_minor, total_minor)
            drawdown_minor = max(drawdown_minor, high_minor - total_minor)

        if genome.stop_mode == "basket_money":
            threshold = _amount_to_minor(float(genome.stop_value), path.currency_digits)
            if exact and total_minor <= -threshold:
                realized_minor, unknown_money = _close_all(
                    path, active, exits, index, "basket_stop", execution,
                    realized_minor, unknown_money, blockers,
                )
                reason = "basket_stop"
                break
            if not exact and total_minor <= -threshold:
                blockers.append("stale_conversion_at_basket_stop")
                unknown_money = True

        if genome.hard_stop_eur_per_leg is not None:
            threshold = _amount_to_minor(
                float(genome.hard_stop_eur_per_leg),
                path.currency_digits,
            )
            for position in tuple(active):
                current_minor, current_exact = _position_money(
                    path,
                    position,
                    index,
                    execution,
                )
                if current_exact and current_minor <= -threshold:
                    realized_minor, unknown_money = _close_one(
                        path, position, position.volume, active, exits, index,
                        "hard_stop_per_leg", execution, realized_minor,
                        unknown_money, blockers,
                    )
                    reason = "hard_stop_per_leg"
                elif not current_exact and current_minor <= -threshold:
                    blockers.append(
                        f"stale_conversion_at_hard_stop:{position.ticket}"
                    )
                    unknown_money = True
            if not active:
                break

        due: list[ProviderEvent] = []
        while (
            provider_cursor < len(provider_events)
            and _to_ns(provider_events[provider_cursor].observed_at)
            + observation_latency_ns
            <= now_ns
        ):
            due.append(provider_events[provider_cursor])
            provider_cursor += 1
        if genome.be_mode == "provider":
            _provider_protection(active, due)
        if genome.provider_management_mode in {
            "exact",
            "close_only",
            "explicit_close_only",
        } and any(_provider_close(item.action) for item in due):
            realized_minor, unknown_money = _close_all(
                path, active, exits, index, "provider_close", execution,
                realized_minor, unknown_money, blockers,
            )
            reason = "provider_close"
            break

        for position in tuple(active):
            level, stop_reason = _stop_level(
                position,
                genome,
                now_ns,
                path.direction,
                observation_latency_ns,
            )
            if level is None or not _hit(path.direction, raw_exit, level, target=False):
                continue
            realized_minor, unknown_money = _close_one(
                path, position, position.volume, active, exits, index,
                stop_reason, execution, realized_minor, unknown_money, blockers,
            )
            reason = stop_reason
        if not active:
            break

        floating_minor, exact = _basket_money(path, active, index, execution)
        total_minor = realized_minor + floating_minor

        if genome.target_mode == "provider_per_leg":
            for position in tuple(active):
                target = _latest_level(
                    position.tp_events,
                    now_ns,
                    include_be=True,
                    observation_latency_ns=observation_latency_ns,
                )
                if target is None or not _hit(path.direction, raw_exit, target, target=True):
                    continue
                realized_minor, unknown_money = _close_one(
                    path, position, position.volume, active, exits, index,
                    "provider_tp", execution, realized_minor, unknown_money, blockers,
                )
                reason = "provider_tp"
        elif genome.target_mode == "per_leg_steps":
            direction = _sign(path.direction)
            for position in tuple(active):
                target = position.entry_price + direction * float(
                    genome.target_steps[position.leg_index]
                )
                if not _hit(path.direction, raw_exit, target, target=True):
                    continue
                realized_minor, unknown_money = _close_one(
                    path, position, position.volume, active, exits, index,
                    "per_leg_target", execution, realized_minor,
                    unknown_money, blockers,
                )
                reason = "per_leg_target"
        elif genome.target_mode == "provider_target_all":
            target = _provider_target(
                path,
                genome,
                now_ns,
                observation_latency_ns,
            )
            if target is not None and _hit(path.direction, raw_exit, target, target=True):
                realized_minor, unknown_money = _close_all(
                    path, active, exits, index, "provider_target_all", execution,
                    realized_minor, unknown_money, blockers,
                )
                reason = "provider_target_all"
        elif genome.target_mode == "fixed_basket":
            target_minor = _amount_to_minor(float(genome.target_value), path.currency_digits)
            if exact and total_minor >= target_minor:
                realized_minor, unknown_money = _close_all(
                    path, active, exits, index, "basket_target", execution,
                    realized_minor, unknown_money, blockers,
                )
                reason = "basket_target"
            elif not exact and total_minor >= target_minor:
                blockers.append("stale_conversion_at_basket_target")
                unknown_money = True
        elif genome.target_mode == "fixed_move":
            if _fixed_move_target_reached(
                path.direction,
                raw_exit,
                active,
                float(genome.target_value),
            ):
                realized_minor, unknown_money = _close_all(
                    path, active, exits, index, "fixed_move_target", execution,
                    realized_minor, unknown_money, blockers,
                )
                reason = "fixed_move_target"
        elif genome.target_mode == "partial_runner":
            first_minor = _amount_to_minor(float(genome.target_value), path.currency_digits)
            runner_minor = _amount_to_minor(float(genome.runner_target), path.currency_digits)
            if not partial_taken and exact and total_minor >= first_minor:
                for position in tuple(active):
                    amount = _clean_volume(position.volume * float(genome.partial_fraction))
                    if amount > 0:
                        realized_minor, unknown_money = _close_one(
                            path, position, amount, active, exits, index,
                            "partial_target", execution, realized_minor,
                            unknown_money, blockers,
                        )
                partial_taken = True
            if active:
                floating_minor, exact = _basket_money(path, active, index, execution)
                total_minor = realized_minor + floating_minor
                if partial_taken and exact and total_minor >= runner_minor:
                    realized_minor, unknown_money = _close_all(
                        path, active, exits, index, "runner_target", execution,
                        realized_minor, unknown_money, blockers,
                    )
                    reason = "runner_target"
        if not active:
            if (
                genome.pending_entry_policy == "until_expiry"
                and schedule_cursor < len(scheduled)
            ):
                continue
            break

        if genome.profit_lock_arm is not None:
            arm = _amount_to_minor(float(genome.profit_lock_arm), path.currency_digits)
            giveback = _amount_to_minor(float(genome.profit_lock_giveback), path.currency_digits)
            if exact and high_minor is not None:
                lock_armed = lock_armed or high_minor >= arm
                if lock_armed and total_minor <= high_minor - giveback:
                    realized_minor, unknown_money = _close_all(
                        path, active, exits, index, "profit_lock", execution,
                        realized_minor, unknown_money, blockers,
                    )
                    reason = "profit_lock"
                    break
            elif not exact and lock_armed:
                blockers.append("stale_conversion_during_profit_lock")
                unknown_money = True

        if (
            active
            and first_open_ns is not None
            and now_ns - first_open_ns
            >= genome.time_exit_min * 60 * 1_000_000_000
            and _time_rule_matches(genome, total_minor, exact)
        ):
            realized_minor, unknown_money = _close_all(
                path, active, exits, index, "time_exit", execution,
                realized_minor, unknown_money, blockers,
            )
            reason = "time_exit"
            break

        if active and genome.trailing_distance is not None:
            _advance_trailing(
                active,
                path.direction,
                raw_exit,
                float(genome.trailing_distance),
            )

    if active and not rollover_blocked:
        if last_index >= 0 and _usable_tick(path, last_index):
            realized_minor, unknown_money = _close_all(
                path, active, exits, last_index, "data_end", execution,
                realized_minor, unknown_money, blockers,
            )
            reason = "data_end"
        blockers.append("path_ended_before_strategy_exit")

    blockers = list(dict.fromkeys(blockers))
    return OracleResult(
        signal_id=path.signal_id,
        strategy_fingerprint=genome.fingerprint,
        confidence_layer=confidence,
        entries=tuple(entries),
        exits=tuple(exits),
        pnl_eur=None if unknown_money else _minor_decimal(realized_minor, path.currency_digits),
        exit_reason=reason,
        max_favourable_eur=None if high_minor is None else _minor_decimal(high_minor, path.currency_digits),
        max_adverse_eur=None if low_minor is None else _minor_decimal(low_minor, path.currency_digits),
        max_floating_drawdown_eur=None if high_minor is None else _minor_decimal(drawdown_minor, path.currency_digits),
        max_favourable_move=_clean_price(favourable_move),
        max_adverse_move=_clean_price(adverse_move),
        blockers=tuple(blockers),
        last_tick_index=last_index,
        unfilled=False,
        filled_volume=_clean_volume(sum(item.volume for item in entries)),
    )


def certify_candidate(
    paths: Sequence[DubaiPath],
    genome: StrategyGenome,
    fast_results: Sequence[object],
    *,
    execution: ExecutionScenario | None = None,
) -> OracleCertificate:
    fast_by_signal = {str(item.signal_id): item for item in fast_results}
    independent = tuple(
        oracle_simulate(path, genome, execution=execution)
        for path in paths
    )
    mismatches: list[OracleMismatch] = []
    for result in independent:
        fast = fast_by_signal.get(result.signal_id)
        if fast is None:
            mismatches.append(OracleMismatch(result.signal_id, "missing_fast_result", None, "present"))
            continue
        mismatches.extend(_compare_result(fast, result))
    oracle_ids = {item.signal_id for item in independent}
    for signal_id in sorted(set(fast_by_signal) - oracle_ids):
        mismatches.append(OracleMismatch(signal_id, "missing_oracle_path", "present", None))
    evidence_complete = all(
        result.pnl_eur is not None and not result.blockers
        for result in independent
    )
    if mismatches:
        status = "blocked"
    elif not evidence_complete:
        status = "blocked_evidence"
    else:
        status = "pass"
    return OracleCertificate(
        status=status,
        mismatches=tuple(mismatches),
        oracle_results=independent,
        promotion_eligible=not mismatches and evidence_complete,
    )


def stress_candidate(
    paths: Sequence[DubaiPath],
    genome: StrategyGenome,
    *,
    scenarios: Sequence[ExecutionScenario] | None = None,
) -> StressReport:
    scenarios = tuple(scenarios or (
        ExecutionScenario("latency_250ms", latency_ms=250),
        ExecutionScenario("latency_1s", latency_ms=1_000),
        ExecutionScenario("latency_2s", latency_ms=2_000),
        ExecutionScenario("adverse_costs", entry_slippage=0.10, exit_slippage=0.10, spread_addition=0.10),
    ))
    base_results = tuple(oracle_simulate(path, genome) for path in paths)
    base_net, base_blockers = _aggregate(base_results)
    stressed: list[StressScenarioResult] = []
    for scenario in scenarios:
        results = tuple(oracle_simulate(path, genome, execution=scenario) for path in paths)
        net, blockers = _aggregate(results)
        stressed.append(StressScenarioResult(scenario, net, blockers, results))
    eligible = (
        base_net is not None
        and base_net > 0
        and not base_blockers
        and all(item.net_eur is not None and item.net_eur > 0 and not item.blockers for item in stressed)
    )
    return StressReport(base_net, base_blockers, tuple(stressed), eligible)


def _schedule_entries(
    path: DubaiPath,
    genome: StrategyGenome,
    execution: ExecutionScenario,
) -> tuple[tuple[_Scheduled, ...], str, tuple[str, ...]]:
    if (
        genome.entry_mode == "actual_mt5"
        and path.entry_evidence_kind != "actual_mt5"
    ):
        return (), "counterfactual_entry", (
            "actual_entry_evidence_missing",
        )
    if genome.entry_ladder_mode != "simultaneous":
        return _schedule_ladder_entries(path, genome, execution)

    times = [int(item) for item in path.times_ns]
    if genome.entry_mode == "actual_mt5":
        first_opened_ns = min(_to_ns(leg.opened_at) for leg in path.legs)
        context_index = bisect_left(times, first_opened_ns)
        if context_index >= len(times):
            return (), "counterfactual_entry", ("missing_tick_for_context_filter",)
        if not _context_allowed(path, genome, context_index, execution):
            return (), "counterfactual_entry", ()
        exact_shape = genome.leg_count == len(path.legs)
        exact_volume = exact_shape and all(
            math.isclose(weight, leg.volume, abs_tol=1e-12)
            for weight, leg in zip(genome.volume_weights, path.legs, strict=True)
        )
        scheduled: list[_Scheduled] = []
        for index in range(genome.leg_count):
            template = path.legs[min(index, len(path.legs) - 1)]
            opened_ns = _to_ns(template.opened_at)
            tick_index = bisect_left(times, opened_ns)
            if tick_index >= len(times):
                return (), "counterfactual_entry", (f"missing_tick_for_entry:{template.ticket}",)
            if index < len(path.legs):
                base = template.open_price
                source = "observed_mt5_fill"
                ticket = template.ticket
            else:
                if not _usable_tick(path, tick_index):
                    return (), "counterfactual_entry", (f"invalid_tick_for_extra_entry:{tick_index}",)
                base = _entry_quote(path, tick_index)
                source = "counterfactual_extra_leg"
                ticket = f"sim_extra_{index + 1}"
            scheduled.append(_Scheduled(
                tick_index,
                source,
                _Position(
                    ticket=ticket,
                    role=template.role,
                    leg_index=index,
                    volume=float(genome.volume_weights[index]),
                    entry_price=(entry_price := _entry_with_cost(
                        path.direction,
                        base,
                        execution,
                    )),
                    opened_ns=opened_ns,
                    tp_events=template.tp_events,
                    sl_events=template.sl_events,
                    trailing_stop=_trailing_start(
                        path.direction,
                        entry_price,
                        genome.trailing_distance,
                    ),
                ),
            ))
        scheduled.sort(key=lambda item: (item.index, item.position.ticket))
        confidence = "observed_entry_management" if exact_volume else "counterfactual_entry"
        return tuple(scheduled), confidence, ()

    entry_index = _causal_index(path, genome, execution)
    if entry_index is None or not _context_allowed(
        path,
        genome,
        entry_index,
        execution,
    ):
        return (), "counterfactual_entry", ()
    entry_ns = int(path.times_ns[entry_index])
    price = _entry_with_cost(path.direction, _entry_quote(path, entry_index), execution)
    if (
        genome.stop_mode == "provider"
        and _provider_stop_invalidated_before_entry(
            path,
            path.legs[0].sl_events,
            entry_index,
            execution.latency_ms * 1_000_000,
        )
    ):
        return (), "counterfactual_entry", ()
    scheduled = tuple(
        _Scheduled(
            entry_index,
            f"causal_{genome.entry_mode}",
            _Position(
                ticket=f"sim_{index + 1}",
                role=path.legs[min(index, len(path.legs) - 1)].role,
                leg_index=index,
                volume=float(volume),
                entry_price=price,
                opened_ns=entry_ns,
                tp_events=path.legs[min(index, len(path.legs) - 1)].tp_events,
                sl_events=path.legs[min(index, len(path.legs) - 1)].sl_events,
                trailing_stop=_trailing_start(
                    path.direction,
                    price,
                    genome.trailing_distance,
                ),
            ),
        )
        for index, volume in enumerate(genome.volume_weights)
    )
    return scheduled, "counterfactual_entry", ()


def _schedule_ladder_entries(
    path: DubaiPath,
    genome: StrategyGenome,
    execution: ExecutionScenario,
) -> tuple[tuple[_Scheduled, ...], str, tuple[str, ...]]:
    times = [int(item) for item in path.times_ns]
    if genome.entry_mode == "actual_mt5":
        template = path.legs[0]
        base_ns = _to_ns(template.opened_at)
        base_index = bisect_left(times, base_ns)
        if base_index >= len(times):
            return (), "counterfactual_entry", (
                f"missing_tick_for_entry:{template.ticket}",
            )
        if not _context_allowed(path, genome, base_index, execution):
            return (), "counterfactual_entry", ()
        reference_price = template.open_price
        first_price = _entry_with_cost(
            path.direction,
            template.open_price,
            execution,
        )
        if genome.schema_version >= 2:
            reference_price = first_price
        first_ticket = template.ticket
        first_source = "observed_mt5_fill"
        expiry_anchor_ns = (
            _to_ns(path.signal_observed_at)
            if genome.schema_version >= 2
            else base_ns
        )
        expiry_ns = (
            expiry_anchor_ns
            + genome.entry_expiry_min * 60 * 1_000_000_000
        )
    else:
        base_index = _causal_index(path, genome, execution)
        if base_index is None or not _context_allowed(
            path,
            genome,
            base_index,
            execution,
        ):
            return (), "counterfactual_entry", ()
        base_ns = times[base_index]
        reference_price = _entry_quote(path, base_index)
        first_price = _entry_with_cost(
            path.direction,
            reference_price,
            execution,
        )
        if genome.schema_version >= 2:
            reference_price = first_price
        first_ticket = "sim_1"
        first_source = f"causal_{genome.entry_mode}"
        expiry_ns = (
            _to_ns(path.signal_observed_at)
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
            return (), "counterfactual_entry", ()

    first_template = path.legs[0]
    scheduled = [_Scheduled(
        base_index,
        first_source,
        _Position(
            ticket=first_ticket,
            role=first_template.role,
            leg_index=0,
            volume=float(genome.volume_weights[0]),
            entry_price=first_price,
            opened_ns=base_ns,
            tp_events=first_template.tp_events,
            sl_events=first_template.sl_events,
            trailing_stop=_trailing_start(
                path.direction,
                first_price,
                genome.trailing_distance,
            ),
        ),
    )]
    direction = _sign(path.direction)
    ladder_sign = -1.0 if genome.entry_ladder_mode == "adverse" else 1.0
    step = float(genome.entry_ladder_step)
    cursor = base_index
    for leg_index in range(1, genome.leg_count):
        matched_index = None
        for index in range(cursor, len(times)):
            if (
                times[index] >= expiry_ns
                if genome.schema_version >= 2
                else times[index] > expiry_ns
            ):
                break
            if not _usable_tick(path, index):
                continue
            quote = _entry_quote(path, index)
            if (
                direction
                * (quote - reference_price)
                * ladder_sign
                >= step * leg_index
            ):
                matched_index = index
                break
        if matched_index is None:
            break
        cursor = matched_index
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
        scheduled.append(_Scheduled(
            matched_index,
            f"counterfactual_{genome.entry_ladder_mode}_ladder",
            _Position(
                ticket=f"sim_ladder_{leg_index + 1}",
                role=template.role,
                leg_index=leg_index,
                volume=float(genome.volume_weights[leg_index]),
                entry_price=(entry_price := _entry_with_cost(
                    path.direction,
                    _entry_quote(path, matched_index),
                    execution,
                )),
                opened_ns=times[matched_index],
                tp_events=template.tp_events,
                sl_events=template.sl_events,
                trailing_stop=_trailing_start(
                    path.direction,
                    entry_price,
                    genome.trailing_distance,
                ),
            ),
        ))
    return tuple(scheduled), "counterfactual_entry", ()


def _causal_index(path: DubaiPath, genome: StrategyGenome, execution: ExecutionScenario) -> int | None:
    times = [int(item) for item in path.times_ns]
    signal_ns = _to_ns(path.signal_observed_at)
    start_ns = signal_ns + execution.latency_ms * 1_000_000
    start_index = bisect_left(times, start_ns)
    if start_index >= len(times):
        return None
    expiry_ns = signal_ns + genome.entry_expiry_min * 60 * 1_000_000_000
    if genome.entry_mode == "no_entry":
        return None
    if genome.entry_mode == "delay":
        target_ns = start_ns + int(float(genome.entry_value) * 1_000_000_000)
        index = bisect_left(times, target_ns)
        return index if index < len(times) and times[index] <= expiry_ns else None

    if genome.entry_mode == "signal_market":
        for index in range(start_index, len(times)):
            if times[index] >= expiry_ns:
                break
            if _usable_tick(path, index):
                return index
        return None

    if genome.entry_mode == "adverse_reversal":
        reference_index = None
        for index in range(start_index, len(times)):
            if times[index] >= expiry_ns:
                break
            if _usable_tick(path, index):
                reference_index = index
                break
        if reference_index is None:
            return None
        reference = _entry_quote(path, reference_index)
        adverse = float(genome.entry_value)
        reversal = float(genome.entry_confirmation_value)
        armed = False
        extreme = reference
        for index in range(reference_index, len(times)):
            if times[index] >= expiry_ns:
                break
            if not _usable_tick(path, index):
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

    if not _usable_tick(path, start_index):
        return None
    reference = _entry_quote(path, start_index)
    distance = float(genome.entry_value)
    for index in range(start_index, len(times)):
        if times[index] > expiry_ns:
            break
        if not _usable_tick(path, index):
            continue
        quote = _entry_quote(path, index)
        if genome.entry_mode == "pullback":
            matches = quote <= reference - distance if path.direction == "BUY" else quote >= reference + distance
        elif genome.entry_mode == "momentum":
            matches = quote >= reference + distance if path.direction == "BUY" else quote <= reference - distance
        else:
            return None
        if matches:
            return index
    return None


def _context_allowed(
    path: DubaiPath,
    genome: StrategyGenome,
    index: int,
    execution: ExecutionScenario,
) -> bool:
    mode = genome.context_filter_mode
    if mode == "none":
        return True
    value = float(genome.context_filter_value)
    if mode == "max_spread":
        return float(path.ask[index]) - float(path.bid[index]) <= value
    if mode == "time_window":
        moment = _from_ns(int(path.times_ns[index]))
        hour = moment.hour + moment.minute / 60 + moment.second / 3600
        return hour <= value
    if mode == "max_volatility":
        start_ns = int(path.times_ns[index]) - 5 * 60 * 1_000_000_000
        start = bisect_left([int(item) for item in path.times_ns], start_ns)
        midpoints = [
            (float(path.bid[cursor]) + float(path.ask[cursor])) / 2
            for cursor in range(start, index + 1)
        ]
        return bool(midpoints) and max(midpoints) - min(midpoints) <= value
    if mode == "min_reward_risk":
        now_ns = int(path.times_ns[index])
        observation_latency_ns = execution.latency_ms * 1_000_000
        target = _latest_level(
            path.legs[0].tp_events,
            now_ns,
            include_be=True,
            observation_latency_ns=observation_latency_ns,
        )
        stop = _latest_level(
            path.legs[0].sl_events,
            now_ns,
            include_be=False,
            observation_latency_ns=observation_latency_ns,
        )
        if target is None or stop is None:
            return False
        entry = _entry_quote(path, index)
        reward = _sign(path.direction) * (target - entry)
        risk = -_sign(path.direction) * (stop - entry)
        return risk > 0 and reward / risk >= value
    return False


def _custom_be(position: _Position, genome: StrategyGenome, move: float, now_ns: int) -> None:
    if genome.be_mode == "price" and move >= float(genome.be_trigger):
        position.be_stop = position.entry_price
    elif genome.be_mode == "delayed" and now_ns - position.opened_ns >= int(float(genome.be_trigger) * 60 * 1_000_000_000):
        position.be_stop = position.entry_price
    elif genome.be_mode == "partial" and move >= float(genome.be_trigger) and position.role != "market_a":
        position.be_stop = position.entry_price


def _stop_level(
    position: _Position,
    genome: StrategyGenome,
    now_ns: int,
    direction: str,
    observation_latency_ns: int,
) -> tuple[float | None, str]:
    base = None
    reason = "provider_sl"
    if genome.stop_mode == "provider":
        base = _latest_level(
            position.sl_events,
            now_ns,
            include_be=genome.be_mode == "provider",
            observation_latency_ns=observation_latency_ns,
        )
    elif genome.stop_mode == "fixed_move":
        base = position.entry_price - _sign(direction) * float(genome.stop_value)
        reason = "fixed_sl"
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
    if position.be_stop is None:
        return base, reason
    if base is None:
        return position.be_stop, position.be_reason
    tighter = max(base, position.be_stop) if direction == "BUY" else min(base, position.be_stop)
    return (tighter, position.be_reason) if math.isclose(tighter, position.be_stop, abs_tol=1e-12) else (tighter, reason)


def _latest_level(
    events: Iterable[LevelEvent],
    now_ns: int,
    *,
    include_be: bool,
    observation_latency_ns: int = 0,
) -> float | None:
    latest = None
    for event in events:
        if event.status not in {"confirmed", "snapshot"}:
            continue
        if not include_be and _be_source(event.source):
            continue
        if _to_ns(event.observed_at) + observation_latency_ns <= now_ns:
            latest = event.level
        else:
            break
    return latest


def _provider_stop_invalidated_before_entry(
    path: DubaiPath,
    events: Iterable[LevelEvent],
    entry_index: int,
    observation_latency_ns: int = 0,
) -> bool:
    times = [int(item) for item in path.times_ns]
    entry_ns = times[entry_index]
    signal_ns = _to_ns(path.signal_observed_at) + observation_latency_ns
    eligible = tuple(
        event
        for event in events
        if event.status in {"confirmed", "snapshot"}
        and not _be_source(event.source)
        and _to_ns(event.observed_at) + observation_latency_ns <= entry_ns
    )
    for offset, event in enumerate(eligible):
        event_ns = _to_ns(event.observed_at) + observation_latency_ns
        active_from = max(signal_ns, event_ns)
        active_until = (
            min(
                entry_ns,
                _to_ns(eligible[offset + 1].observed_at)
                + observation_latency_ns,
            )
            if offset + 1 < len(eligible)
            else entry_ns
        )
        start = bisect_left(times, active_from)
        end = bisect_left(times, active_until)
        if offset + 1 >= len(eligible):
            end = entry_index + 1
        end = min(end, entry_index + 1)
        for index in range(start, end):
            quote = float(path.exit_quotes[index])
            if _hit(path.direction, quote, float(event.level), target=False):
                return True
    return False


def _provider_target(
    path: DubaiPath,
    genome: StrategyGenome,
    now_ns: int,
    observation_latency_ns: int = 0,
) -> float | None:
    targets = []
    for leg in path.legs:
        value = _latest_level(
            leg.tp_events,
            now_ns,
            include_be=True,
            observation_latency_ns=observation_latency_ns,
        )
        if value is not None:
            targets.append(value)
    if not targets:
        return None
    ordered = sorted(set(targets), reverse=path.direction == "SELL")
    selected = max(1, int(round(float(genome.target_value)))) - 1
    return ordered[min(selected, len(ordered) - 1)]


def _provider_protection(positions: Iterable[_Position], events: Iterable[ProviderEvent]) -> None:
    for event in events:
        action = event.action.upper()
        if action == "MOVE_SL_TO_BE":
            for position in positions:
                position.be_stop = position.entry_price
                position.be_reason = "break_even"
        elif action == "MOVE_SL_TO_PRICE":
            price = _announced_price(event)
            if price is not None:
                for position in positions:
                    position.be_stop = price
                    position.be_reason = "provider_sl_move"


def _announced_price(event: ProviderEvent) -> float | None:
    for key in ("price", "sl", "stop", "target_price"):
        try:
            value = float(event.payload.get(key))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            return value
    match = re.search(
        r"(?:MOVE\s+)?(?:SL|STOP(?:\s+LOSS)?)\s*(?:TO|AT|@)?\s*[:=]?\s*(\d+(?:[.,]\d+)?)",
        str(event.payload.get("raw_text") or ""),
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    try:
        value = float(match.group(1).replace(",", "."))
    except ValueError:
        return None
    return value if math.isfinite(value) and value > 0 else None


def _close_all(path, active, exits, index, reason, execution, realized, unknown, blockers):
    for position in tuple(active):
        realized, unknown = _close_one(
            path, position, position.volume, active, exits, index, reason,
            execution, realized, unknown, blockers,
        )
    return realized, unknown


def _close_one(path, position, volume, active, exits, index, reason, execution, realized, unknown, blockers):
    volume = min(position.volume, _clean_volume(volume))
    exit_price = _exit_with_cost(path, index, execution)
    pnl_minor, exact = _money_minor(path, position.entry_price, exit_price, volume, index)
    if exact:
        pnl_minor += _allocate_swap(position, volume)
        realized += pnl_minor
    else:
        blockers.append(f"stale_conversion_at_exit:{index}")
        unknown = True
    exits.append(OracleExit(
        ticket=position.ticket,
        tick_index=index,
        closed_at=_from_ns(int(path.times_ns[index])),
        entry_price=position.entry_price,
        exit_price=exit_price,
        volume=volume,
        pnl_eur=_minor_decimal(pnl_minor, path.currency_digits) if exact else None,
        reason=reason,
    ))
    position.volume = _clean_volume(position.volume - volume)
    if position.volume <= 1e-12:
        active.remove(position)
    return realized, unknown


def _basket_money(path, active, index, execution):
    exit_price = _exit_with_cost(path, index, execution)
    total = 0
    exact = True
    for position in active:
        value, current_exact = _money_minor(path, position.entry_price, exit_price, position.volume, index)
        total += value + position.accrued_swap_minor
        exact = exact and current_exact
    return total, exact


def _position_money(path, position, index, execution):
    value, exact = _money_minor(
        path,
        position.entry_price,
        _exit_with_cost(path, index, execution),
        position.volume,
        index,
    )
    return value + position.accrued_swap_minor, exact


def _swap_volume_units(volume: float) -> int | None:
    scaled = Decimal(str(volume)) * Decimal(100)
    integral = scaled.to_integral_value(rounding=ROUND_HALF_UP)
    if abs(scaled - integral) > Decimal("0.00000001"):
        return None
    units = int(integral)
    return units if units >= 0 else None


def _allocate_swap(position: _Position, volume: float) -> int:
    if position.accrued_swap_minor == 0:
        return 0
    if math.isclose(volume, position.volume, abs_tol=1e-12):
        allocated = position.accrued_swap_minor
    else:
        raw = (
            Decimal(position.accrued_swap_minor)
            * Decimal(str(volume))
            / Decimal(str(position.volume))
        )
        allocated = int(raw.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    position.accrued_swap_minor -= allocated
    return allocated


def _money_minor(path: DubaiPath, entry: float, exit_price: float, volume: float, index: int) -> tuple[int, bool]:
    raw = Decimal(_sign(path.direction)) * (Decimal(str(exit_price)) - Decimal(str(entry)))
    raw *= Decimal(str(path.contract_size)) * Decimal(str(volume))
    orientation = path.conversion_orientation
    exact = orientation == "identity" or bool(path.fx_valid[index])
    if orientation == "account_base_profit_quote":
        quote = Decimal(str(path.fx_ask[index] if raw >= 0 else path.fx_bid[index]))
        raw /= quote
    elif orientation == "profit_base_account_quote":
        quote = Decimal(str(path.fx_bid[index] if raw >= 0 else path.fx_ask[index]))
        raw *= quote
    elif orientation != "identity":
        return 0, False
    scale = Decimal(10) ** path.currency_digits
    minor = int((raw * scale).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return minor, exact


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
    move_numerator = Decimal(_sign(direction)) * (
        Decimal(str(raw_exit)) * total_volume - weighted_entry
    )
    return move_numerator >= Decimal(str(target)) * total_volume


def _trailing_start(direction, entry_price, trailing_distance):
    if trailing_distance is None:
        return None
    return _clean_price(
        entry_price - _sign(direction) * float(trailing_distance)
    )


def _advance_trailing(active, direction, executable_exit, distance):
    candidate = _clean_price(
        executable_exit - _sign(direction) * float(distance)
    )
    for position in active:
        if position.trailing_stop is None:
            position.trailing_stop = candidate
        elif direction == "BUY" and candidate > position.trailing_stop:
            position.trailing_stop = candidate
        elif direction == "SELL" and candidate < position.trailing_stop:
            position.trailing_stop = candidate


def _time_rule_matches(genome, total_minor, exact):
    if genome.schema_version == 1:
        return True
    if not exact:
        return False
    if genome.time_exit_mode == "loss_only":
        return total_minor <= 0
    if genome.time_exit_mode == "profit_only":
        return total_minor > 0
    if genome.time_exit_mode == "non_negative":
        return total_minor >= 0
    return False


def _compare_result(fast: object, oracle: OracleResult) -> list[OracleMismatch]:
    mismatches: list[OracleMismatch] = []

    def compare(field: str, left: object, right: object, *, tolerance: float | None = None) -> None:
        equal = math.isclose(float(left), float(right), abs_tol=tolerance, rel_tol=0.0) if tolerance is not None and left is not None and right is not None else left == right
        if not equal:
            mismatches.append(OracleMismatch(oracle.signal_id, field, left, right))

    for field in (
        "strategy_fingerprint", "confidence_layer", "pnl_eur", "exit_reason",
        "max_favourable_eur", "max_adverse_eur", "max_floating_drawdown_eur",
        "blockers", "last_tick_index", "unfilled",
    ):
        compare(field, getattr(fast, field), getattr(oracle, field))
    for field in ("max_favourable_move", "max_adverse_move", "filled_volume"):
        compare(field, getattr(fast, field), getattr(oracle, field), tolerance=1e-9)
    _compare_records(mismatches, oracle.signal_id, "entries", getattr(fast, "entries"), oracle.entries)
    _compare_records(mismatches, oracle.signal_id, "exits", getattr(fast, "exits"), oracle.exits)
    return mismatches


def _compare_records(mismatches, signal_id, prefix, fast_rows, oracle_rows):
    if len(fast_rows) != len(oracle_rows):
        mismatches.append(OracleMismatch(signal_id, f"{prefix}.length", len(fast_rows), len(oracle_rows)))
    fields = ("ticket", "tick_index", "opened_at", "entry_price", "volume", "source") if prefix == "entries" else (
        "ticket", "tick_index", "closed_at", "entry_price", "exit_price", "volume", "pnl_eur", "reason",
    )
    for index, (fast, oracle) in enumerate(zip(fast_rows, oracle_rows)):
        for field in fields:
            left = getattr(fast, field)
            right = getattr(oracle, field)
            if field in {"entry_price", "exit_price", "volume"}:
                equal = math.isclose(float(left), float(right), abs_tol=1e-9, rel_tol=0.0)
            else:
                equal = left == right
            if not equal:
                mismatches.append(OracleMismatch(signal_id, f"{prefix}[{index}].{field}", left, right))


def _aggregate(results: Sequence[OracleResult]) -> tuple[Decimal | None, tuple[str, ...]]:
    blockers = tuple(dict.fromkeys(blocker for result in results for blocker in result.blockers))
    if any(result.pnl_eur is None for result in results):
        return None, blockers
    return sum((result.pnl_eur for result in results), start=Decimal("0")), blockers


def _path_errors(path: DubaiPath) -> tuple[str, ...]:
    lengths = {
        len(path.times_ns), len(path.bid), len(path.ask), len(path.exit_quotes),
        len(path.fx_bid), len(path.fx_ask), len(path.fx_age_ms), len(path.fx_valid),
    }
    errors = []
    if len(lengths) != 1 or not len(path.times_ns):
        errors.append("invalid_path_lengths")
    elif any(int(path.times_ns[index]) < int(path.times_ns[index - 1]) for index in range(1, len(path.times_ns))):
        errors.append("non_monotonic_path_time")
    if path.direction not in {"BUY", "SELL"}:
        errors.append("invalid_path_direction")
    if path.contract_size <= 0 or path.currency_digits < 0:
        errors.append("invalid_path_money_contract")
    if not path.legs:
        errors.append("path_without_entry_evidence")
    return tuple(errors)


def _empty(path, genome, blockers, *, confidence="unclassified", unfilled=False):
    zero = _minor_decimal(0, max(0, path.currency_digits))
    blockers = tuple(dict.fromkeys(blockers))
    return OracleResult(
        path.signal_id, genome.fingerprint, confidence, (), (),
        zero if not blockers else None,
        "not_filled" if unfilled else "blocked",
        zero if not blockers else None,
        zero if not blockers else None,
        zero if not blockers else None,
        0.0, 0.0, blockers, -1, unfilled, 0.0,
    )


def _amount_to_minor(value: float, digits: int) -> int:
    return int((Decimal(str(value)) * (Decimal(10) ** digits)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _minor_decimal(value: int, digits: int) -> Decimal:
    quantum = Decimal(1).scaleb(-digits)
    return Decimal(value).scaleb(-digits).quantize(quantum)


def _entry_quote(path: DubaiPath, index: int) -> float:
    return float(path.ask[index] if path.direction == "BUY" else path.bid[index])


def _entry_with_cost(direction: str, price: float, scenario: ExecutionScenario) -> float:
    cost = scenario.entry_slippage + scenario.spread_addition
    return _clean_price(price + cost if direction == "BUY" else price - cost)


def _exit_with_cost(path: DubaiPath, index: int, scenario: ExecutionScenario) -> float:
    price = float(path.exit_quotes[index])
    cost = scenario.exit_slippage + scenario.spread_addition
    return _clean_price(price - cost if path.direction == "BUY" else price + cost)


def _usable_tick(path: DubaiPath, index: int) -> bool:
    bid = float(path.bid[index])
    ask = float(path.ask[index])
    return math.isfinite(bid) and math.isfinite(ask) and bid > 0 and ask >= bid


def _hit(direction: str, price: float, level: float, *, target: bool) -> bool:
    if target:
        return price >= level if direction == "BUY" else price <= level
    return price <= level if direction == "BUY" else price >= level


def _provider_close(action: str) -> bool:
    normalized = str(action).upper()
    return "CLOSE" in normalized or normalized in {"EXIT", "CERRAR"}


def _be_source(source: str) -> bool:
    normalized = str(source).upper()
    return "BE" in normalized or "BREAK EVEN" in normalized or "BREAKEVEN" in normalized


def _sign(direction: str) -> int:
    return 1 if direction == "BUY" else -1


def _to_ns(value: datetime) -> int:
    return int(value.astimezone(timezone.utc).timestamp() * 1_000_000_000)


def _from_ns(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1_000_000_000, tz=timezone.utc)


def _clean_price(value: float) -> float:
    return round(float(value), 10)


def _clean_volume(value: float) -> float:
    return round(float(value), 10)
