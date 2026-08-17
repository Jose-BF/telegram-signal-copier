"""Deterministic causal replay engine for Dubai strategy research."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import math
import re
from typing import Iterable

import numpy as np

from .contracts import StrategyGenome
from .dataset import DubaiLeg, DubaiPath, LevelEvent, ProviderEvent


@dataclass(frozen=True)
class ExecutionAssumptions:
    entry_slippage: float = 0.0
    exit_slippage: float = 0.0
    spread_addition: float = 0.0
    latency_ms: int = 0

    def __post_init__(self) -> None:
        for name in ("entry_slippage", "exit_slippage", "spread_addition"):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(float(value)) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if (
            isinstance(self.latency_ms, bool)
            or not isinstance(self.latency_ms, int)
            or self.latency_ms < 0
        ):
            raise ValueError("latency_ms must be a non-negative integer")


@dataclass(frozen=True)
class EntryRecord:
    ticket: str
    tick_index: int
    opened_at: datetime
    entry_price: float
    volume: float
    source: str


@dataclass(frozen=True)
class ExitRecord:
    ticket: str
    tick_index: int
    closed_at: datetime
    entry_price: float
    exit_price: float
    volume: float
    pnl_eur: Decimal | None
    reason: str


@dataclass(frozen=True)
class SimulationResult:
    signal_id: str
    strategy_fingerprint: str
    confidence_layer: str
    entries: tuple[EntryRecord, ...]
    exits: tuple[ExitRecord, ...]
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


@dataclass
class _Position:
    ticket: str
    role: str
    volume: float
    entry_price: float
    opened_ns: int
    opened_index: int
    tp_events: tuple[LevelEvent, ...]
    sl_events: tuple[LevelEvent, ...]
    be_stop: float | None = None
    be_reason: str = "break_even"


@dataclass(frozen=True)
class _ScheduledEntry:
    tick_index: int
    position: _Position
    source: str


def simulate(
    path: DubaiPath,
    genome: StrategyGenome,
    *,
    execution: ExecutionAssumptions | None = None,
) -> SimulationResult:
    """Replay one signal without reading any tick after the final decision."""

    execution = execution or ExecutionAssumptions()
    blockers = list(genome.validation_errors())
    path_blockers = _path_contract_blockers(path)
    blockers.extend(path_blockers)
    if blockers:
        return _empty_result(path, genome, blockers=blockers)

    scheduled, confidence_layer, entry_blockers = _prepare_entries(
        path,
        genome,
        execution,
    )
    blockers.extend(entry_blockers)
    if blockers:
        return _empty_result(
            path,
            genome,
            blockers=blockers,
            confidence_layer=confidence_layer,
        )
    if not scheduled:
        return _empty_result(
            path,
            genome,
            blockers=(),
            confidence_layer=confidence_layer,
            unfilled=True,
        )

    positions: list[_Position] = []
    entries: list[EntryRecord] = []
    exits: list[ExitRecord] = []
    schedule_cursor = 0
    provider_events = tuple(
        sorted(path.provider_events, key=lambda event: _datetime_ns(event.observed_at))
    )
    provider_cursor = 0
    realized_minor = 0
    money_unknown = False
    partial_taken = False
    lock_armed = False
    max_total_minor: int | None = None
    min_total_minor: int | None = None
    max_drawdown_minor = 0
    max_favourable_move = 0.0
    max_adverse_move = 0.0
    last_tick_index = -1
    exit_reason = "not_closed"
    first_open_ns: int | None = None

    for index in range(len(path.times_ns)):
        now_ns = int(path.times_ns[index])
        while (
            schedule_cursor < len(scheduled)
            and scheduled[schedule_cursor].tick_index == index
        ):
            item = scheduled[schedule_cursor]
            positions.append(item.position)
            first_open_ns = (
                item.position.opened_ns
                if first_open_ns is None
                else min(first_open_ns, item.position.opened_ns)
            )
            entries.append(EntryRecord(
                ticket=item.position.ticket,
                tick_index=index,
                opened_at=_ns_datetime(item.position.opened_ns),
                entry_price=item.position.entry_price,
                volume=item.position.volume,
                source=item.source,
            ))
            schedule_cursor += 1

        if not positions:
            if schedule_cursor >= len(scheduled) and entries:
                break
            continue

        if not _tick_is_usable(path, index):
            blockers.append(f"invalid_tick_at_index:{index}")
            last_tick_index = index
            break
        last_tick_index = index

        raw_exit = float(path.exit_quotes[index])
        for position in positions:
            directional_move = _direction_sign(path.direction) * (
                raw_exit - position.entry_price
            )
            max_favourable_move = max(max_favourable_move, directional_move)
            max_adverse_move = min(max_adverse_move, directional_move)
            _update_custom_be(position, genome, directional_move, now_ns)

        floating_minor, floating_exact = _basket_minor(
            path,
            positions,
            index,
            execution,
        )
        total_minor = realized_minor + floating_minor
        if floating_exact and not money_unknown:
            max_total_minor = (
                total_minor
                if max_total_minor is None
                else max(max_total_minor, total_minor)
            )
            min_total_minor = (
                total_minor
                if min_total_minor is None
                else min(min_total_minor, total_minor)
            )
            max_drawdown_minor = max(
                max_drawdown_minor,
                max_total_minor - total_minor,
            )

        # 1. Emergency basket loss always wins over every profit rule.
        if genome.stop_mode == "basket_money":
            threshold = _money_value_to_minor(
                float(genome.stop_value),
                path.currency_digits,
            )
            if floating_exact and total_minor <= -threshold:
                realized_minor, money_unknown = _close_all(
                    path,
                    positions,
                    exits,
                    index,
                    "basket_stop",
                    execution,
                    realized_minor,
                    money_unknown,
                    blockers,
                )
                exit_reason = "basket_stop"
                break
            if not floating_exact and total_minor <= -threshold:
                blockers.append("stale_conversion_at_basket_stop")
                money_unknown = True

        # 2. Explicit provider close instructions.
        provider_due: list[ProviderEvent] = []
        while (
            provider_cursor < len(provider_events)
            and _datetime_ns(provider_events[provider_cursor].observed_at) <= now_ns
        ):
            provider_due.append(provider_events[provider_cursor])
            provider_cursor += 1
        if genome.be_mode == "provider":
            _apply_provider_protection(positions, provider_due)
        if genome.provider_management_mode in {"exact", "close_only"}:
            close_event = next(
                (event for event in provider_due if _is_provider_close(event.action)),
                None,
            )
            if close_event is not None:
                realized_minor, money_unknown = _close_all(
                    path,
                    positions,
                    exits,
                    index,
                    "provider_close",
                    execution,
                    realized_minor,
                    money_unknown,
                    blockers,
                )
                exit_reason = "provider_close"
                break

        # 3. Effective SL, including custom BE.
        for position in list(positions):
            stop_level, stop_reason = _effective_stop(
                position,
                genome,
                now_ns,
                path.direction,
            )
            if stop_level is None or not _level_hit(
                path.direction,
                raw_exit,
                stop_level,
                kind="stop",
            ):
                continue
            realized_minor, money_unknown = _close_position(
                path,
                position,
                position.volume,
                positions,
                exits,
                index,
                stop_reason,
                execution,
                realized_minor,
                money_unknown,
                blockers,
            )
            exit_reason = stop_reason
        if not positions:
            break

        floating_minor, floating_exact = _basket_minor(
            path,
            positions,
            index,
            execution,
        )
        total_minor = realized_minor + floating_minor

        # 4. Targets and partial exits.
        if genome.target_mode == "provider_per_leg":
            for position in list(positions):
                target = _latest_level(position.tp_events, now_ns, include_be=True)
                if target is None or not _level_hit(
                    path.direction,
                    raw_exit,
                    target,
                    kind="target",
                ):
                    continue
                realized_minor, money_unknown = _close_position(
                    path,
                    position,
                    position.volume,
                    positions,
                    exits,
                    index,
                    "provider_tp",
                    execution,
                    realized_minor,
                    money_unknown,
                    blockers,
                )
                exit_reason = "provider_tp"
        elif genome.target_mode == "provider_target_all":
            target = _selected_provider_target(path, genome, now_ns)
            if target is not None and _level_hit(
                path.direction,
                raw_exit,
                target,
                kind="target",
            ):
                realized_minor, money_unknown = _close_all(
                    path,
                    positions,
                    exits,
                    index,
                    "provider_target_all",
                    execution,
                    realized_minor,
                    money_unknown,
                    blockers,
                )
                exit_reason = "provider_target_all"
        elif genome.target_mode == "fixed_basket":
            threshold = _money_value_to_minor(
                float(genome.target_value),
                path.currency_digits,
            )
            if floating_exact and total_minor >= threshold:
                realized_minor, money_unknown = _close_all(
                    path,
                    positions,
                    exits,
                    index,
                    "basket_target",
                    execution,
                    realized_minor,
                    money_unknown,
                    blockers,
                )
                exit_reason = "basket_target"
            elif not floating_exact and total_minor >= threshold:
                blockers.append("stale_conversion_at_basket_target")
                money_unknown = True
        elif genome.target_mode == "partial_runner":
            first_target = _money_value_to_minor(
                float(genome.target_value),
                path.currency_digits,
            )
            runner_target = _money_value_to_minor(
                float(genome.runner_target),
                path.currency_digits,
            )
            if not partial_taken and floating_exact and total_minor >= first_target:
                for position in list(positions):
                    close_volume = _clean_volume(
                        position.volume * float(genome.partial_fraction)
                    )
                    if close_volume <= 0:
                        continue
                    realized_minor, money_unknown = _close_position(
                        path,
                        position,
                        close_volume,
                        positions,
                        exits,
                        index,
                        "partial_target",
                        execution,
                        realized_minor,
                        money_unknown,
                        blockers,
                    )
                partial_taken = True
            if positions:
                floating_minor, floating_exact = _basket_minor(
                    path,
                    positions,
                    index,
                    execution,
                )
                total_minor = realized_minor + floating_minor
                if partial_taken and floating_exact and total_minor >= runner_target:
                    realized_minor, money_unknown = _close_all(
                        path,
                        positions,
                        exits,
                        index,
                        "runner_target",
                        execution,
                        realized_minor,
                        money_unknown,
                        blockers,
                    )
                    exit_reason = "runner_target"
        if not positions:
            break

        # 5. Trailing giveback protection.
        if genome.profit_lock_arm is not None:
            arm = _money_value_to_minor(
                float(genome.profit_lock_arm),
                path.currency_digits,
            )
            giveback = _money_value_to_minor(
                float(genome.profit_lock_giveback),
                path.currency_digits,
            )
            if floating_exact and max_total_minor is not None:
                lock_armed = lock_armed or max_total_minor >= arm
                if lock_armed and total_minor <= max_total_minor - giveback:
                    realized_minor, money_unknown = _close_all(
                        path,
                        positions,
                        exits,
                        index,
                        "profit_lock",
                        execution,
                        realized_minor,
                        money_unknown,
                        blockers,
                    )
                    exit_reason = "profit_lock"
                    break
            elif not floating_exact and lock_armed:
                blockers.append("stale_conversion_during_profit_lock")
                money_unknown = True

        # 6. Absolute time exit.
        if (
            positions
            and first_open_ns is not None
            and now_ns - first_open_ns >= genome.time_exit_min * 60 * 1_000_000_000
        ):
            realized_minor, money_unknown = _close_all(
                path,
                positions,
                exits,
                index,
                "time_exit",
                execution,
                realized_minor,
                money_unknown,
                blockers,
            )
            exit_reason = "time_exit"
            break

    if positions:
        if last_tick_index >= 0 and _tick_is_usable(path, last_tick_index):
            realized_minor, money_unknown = _close_all(
                path,
                positions,
                exits,
                last_tick_index,
                "data_end",
                execution,
                realized_minor,
                money_unknown,
                blockers,
            )
            exit_reason = "data_end"
        blockers.append("path_ended_before_strategy_exit")

    blockers = list(dict.fromkeys(blockers))
    pnl = None if money_unknown else _minor_decimal(
        realized_minor,
        path.currency_digits,
    )
    return SimulationResult(
        signal_id=path.signal_id,
        strategy_fingerprint=genome.fingerprint,
        confidence_layer=confidence_layer,
        entries=tuple(entries),
        exits=tuple(exits),
        pnl_eur=pnl,
        exit_reason=exit_reason,
        max_favourable_eur=(
            None
            if max_total_minor is None
            else _minor_decimal(max_total_minor, path.currency_digits)
        ),
        max_adverse_eur=(
            None
            if min_total_minor is None
            else _minor_decimal(min_total_minor, path.currency_digits)
        ),
        max_floating_drawdown_eur=(
            None
            if max_total_minor is None
            else _minor_decimal(max_drawdown_minor, path.currency_digits)
        ),
        max_favourable_move=_clean_price(max_favourable_move),
        max_adverse_move=_clean_price(max_adverse_move),
        blockers=tuple(blockers),
        last_tick_index=last_tick_index,
        unfilled=False,
        filled_volume=_clean_volume(sum(item.volume for item in entries)),
    )


def _prepare_entries(
    path: DubaiPath,
    genome: StrategyGenome,
    execution: ExecutionAssumptions,
) -> tuple[list[_ScheduledEntry], str, list[str]]:
    if genome.entry_mode == "actual_mt5":
        scheduled: list[_ScheduledEntry] = []
        exact_shape = genome.leg_count == len(path.legs)
        exact_volume = exact_shape and all(
            math.isclose(weight, leg.volume, abs_tol=1e-12)
            for weight, leg in zip(genome.volume_weights, path.legs, strict=True)
        )
        for index in range(genome.leg_count):
            template = path.legs[min(index, len(path.legs) - 1)]
            opened_ns = _datetime_ns(template.opened_at)
            tick_index = int(np.searchsorted(path.times_ns, opened_ns, side="left"))
            if tick_index >= len(path.times_ns):
                return [], "counterfactual_entry", [
                    f"missing_tick_for_entry:{template.ticket}"
                ]
            if index < len(path.legs):
                base_price = template.open_price
                source = "observed_mt5_fill"
                ticket = template.ticket
            else:
                if not _tick_is_usable(path, tick_index):
                    return [], "counterfactual_entry", [
                        f"invalid_tick_for_extra_entry:{tick_index}"
                    ]
                base_price = _entry_quote(path, tick_index)
                source = "counterfactual_extra_leg"
                ticket = f"sim_extra_{index + 1}"
            entry_price = _adverse_entry_price(
                path.direction,
                base_price,
                execution,
            )
            scheduled.append(_ScheduledEntry(
                tick_index=tick_index,
                source=source,
                position=_Position(
                    ticket=ticket,
                    role=template.role,
                    volume=float(genome.volume_weights[index]),
                    entry_price=entry_price,
                    opened_ns=opened_ns,
                    opened_index=tick_index,
                    tp_events=template.tp_events,
                    sl_events=template.sl_events,
                ),
            ))
        return (
            sorted(scheduled, key=lambda item: (item.tick_index, item.position.ticket)),
            "observed_entry_management" if exact_volume else "counterfactual_entry",
            [],
        )

    entry_index = _causal_entry_index(path, genome, execution)
    if entry_index is None:
        return [], "counterfactual_entry", []
    if not _context_allows(path, genome, entry_index):
        return [], "counterfactual_entry", []
    entry_ns = int(path.times_ns[entry_index])
    base_price = _entry_quote(path, entry_index)
    entry_price = _adverse_entry_price(path.direction, base_price, execution)
    scheduled = []
    for index, volume in enumerate(genome.volume_weights):
        template = path.legs[min(index, len(path.legs) - 1)]
        scheduled.append(_ScheduledEntry(
            tick_index=entry_index,
            source=f"causal_{genome.entry_mode}",
            position=_Position(
                ticket=f"sim_{index + 1}",
                role=template.role,
                volume=float(volume),
                entry_price=entry_price,
                opened_ns=entry_ns,
                opened_index=entry_index,
                tp_events=template.tp_events,
                sl_events=template.sl_events,
            ),
        ))
    return scheduled, "counterfactual_entry", []


def _causal_entry_index(
    path: DubaiPath,
    genome: StrategyGenome,
    execution: ExecutionAssumptions,
) -> int | None:
    signal_ns = _datetime_ns(path.signal_observed_at)
    latency_ns = execution.latency_ms * 1_000_000
    start_ns = signal_ns + latency_ns
    start_index = int(np.searchsorted(path.times_ns, start_ns, side="left"))
    if start_index >= len(path.times_ns):
        return None
    expiry_ns = signal_ns + genome.entry_expiry_min * 60 * 1_000_000_000

    if genome.entry_mode == "delay":
        target_ns = start_ns + int(float(genome.entry_value) * 1_000_000_000)
        index = int(np.searchsorted(path.times_ns, target_ns, side="left"))
        return index if index < len(path.times_ns) and path.times_ns[index] <= expiry_ns else None

    if not _tick_is_usable(path, start_index):
        return None
    reference = _entry_quote(path, start_index)
    distance = float(genome.entry_value)
    for index in range(start_index, len(path.times_ns)):
        if int(path.times_ns[index]) > expiry_ns:
            break
        if not _tick_is_usable(path, index):
            continue
        quote = _entry_quote(path, index)
        if genome.entry_mode == "pullback":
            matched = (
                quote <= reference - distance
                if path.direction == "BUY"
                else quote >= reference + distance
            )
        elif genome.entry_mode == "momentum":
            matched = (
                quote >= reference + distance
                if path.direction == "BUY"
                else quote <= reference - distance
            )
        else:
            return None
        if matched:
            return index
    return None


def _context_allows(path: DubaiPath, genome: StrategyGenome, index: int) -> bool:
    mode = genome.context_filter_mode
    if mode == "none":
        return True
    value = float(genome.context_filter_value)
    if mode == "max_spread":
        return float(path.ask[index] - path.bid[index]) <= value
    if mode == "time_window":
        moment = _ns_datetime(int(path.times_ns[index]))
        hour = moment.hour + moment.minute / 60 + moment.second / 3600
        return hour <= value
    if mode == "max_volatility":
        lookback_ns = int(path.times_ns[index]) - 5 * 60 * 1_000_000_000
        start = int(np.searchsorted(path.times_ns, lookback_ns, side="left"))
        midpoint = (path.bid[start:index + 1] + path.ask[start:index + 1]) / 2
        return bool(len(midpoint)) and float(np.max(midpoint) - np.min(midpoint)) <= value
    if mode == "min_reward_risk":
        leg = path.legs[0]
        now_ns = int(path.times_ns[index])
        target = _latest_level(leg.tp_events, now_ns, include_be=True)
        stop = _latest_level(leg.sl_events, now_ns, include_be=False)
        if target is None or stop is None:
            return False
        entry = _entry_quote(path, index)
        reward = _direction_sign(path.direction) * (target - entry)
        risk = -_direction_sign(path.direction) * (stop - entry)
        return risk > 0 and reward / risk >= value
    return False


def _update_custom_be(
    position: _Position,
    genome: StrategyGenome,
    directional_move: float,
    now_ns: int,
) -> None:
    if genome.be_mode == "price" and directional_move >= float(genome.be_trigger):
        position.be_stop = position.entry_price
    elif genome.be_mode == "delayed":
        delay_ns = int(float(genome.be_trigger) * 60 * 1_000_000_000)
        if now_ns - position.opened_ns >= delay_ns:
            position.be_stop = position.entry_price
    elif (
        genome.be_mode == "partial"
        and directional_move >= float(genome.be_trigger)
        and position.role != "market_a"
    ):
        position.be_stop = position.entry_price


def _effective_stop(
    position: _Position,
    genome: StrategyGenome,
    now_ns: int,
    direction: str,
) -> tuple[float | None, str]:
    base: float | None = None
    reason = "provider_sl"
    if genome.stop_mode == "provider":
        base = _latest_level(
            position.sl_events,
            now_ns,
            include_be=genome.be_mode == "provider",
        )
    elif genome.stop_mode == "fixed_move":
        base = position.entry_price - _direction_sign(direction) * float(
            genome.stop_value
        )
        reason = "fixed_sl"
    if position.be_stop is not None:
        if base is None:
            return position.be_stop, position.be_reason
        tighter = (
            max(base, position.be_stop)
            if direction == "BUY"
            else min(base, position.be_stop)
        )
        if math.isclose(tighter, position.be_stop, abs_tol=1e-12):
            return tighter, position.be_reason
        return tighter, reason
    return base, reason


def _latest_level(
    events: Iterable[LevelEvent],
    now_ns: int,
    *,
    include_be: bool,
) -> float | None:
    latest: LevelEvent | None = None
    for event in events:
        if event.status not in {"confirmed", "snapshot"}:
            continue
        if not include_be and _looks_like_be(event.source):
            continue
        if _datetime_ns(event.observed_at) <= now_ns:
            latest = event
        else:
            break
    return None if latest is None else latest.level


def _selected_provider_target(
    path: DubaiPath,
    genome: StrategyGenome,
    now_ns: int,
) -> float | None:
    targets = [
        value
        for leg in path.legs
        if (value := _latest_level(leg.tp_events, now_ns, include_be=True))
        is not None
    ]
    if not targets:
        return None
    targets = sorted(set(targets), reverse=path.direction == "SELL")
    selected = max(1, int(round(float(genome.target_value)))) - 1
    return targets[min(selected, len(targets) - 1)]


def _apply_provider_protection(
    positions: Iterable[_Position],
    events: Iterable[ProviderEvent],
) -> None:
    for event in events:
        action = event.action.upper()
        if action == "MOVE_SL_TO_BE":
            for position in positions:
                position.be_stop = position.entry_price
                position.be_reason = "break_even"
        elif action == "MOVE_SL_TO_PRICE":
            level = _provider_announced_price(event)
            if level is None:
                continue
            for position in positions:
                position.be_stop = level
                position.be_reason = "provider_sl_move"


def _provider_announced_price(event: ProviderEvent) -> float | None:
    for key in ("price", "sl", "stop", "target_price"):
        value = event.payload.get(key)
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number) and number > 0:
            return number
    raw_text = str(event.payload.get("raw_text") or "")
    match = re.search(
        r"(?:MOVE\s+)?(?:SL|STOP(?:\s+LOSS)?)\s*(?:TO|AT|@)?\s*[:=]?\s*(\d+(?:[.,]\d+)?)",
        raw_text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    try:
        value = float(match.group(1).replace(",", "."))
    except ValueError:
        return None
    return value if math.isfinite(value) and value > 0 else None


def _close_all(
    path: DubaiPath,
    positions: list[_Position],
    exits: list[ExitRecord],
    index: int,
    reason: str,
    execution: ExecutionAssumptions,
    realized_minor: int,
    money_unknown: bool,
    blockers: list[str],
) -> tuple[int, bool]:
    for position in list(positions):
        realized_minor, money_unknown = _close_position(
            path,
            position,
            position.volume,
            positions,
            exits,
            index,
            reason,
            execution,
            realized_minor,
            money_unknown,
            blockers,
        )
    return realized_minor, money_unknown


def _close_position(
    path: DubaiPath,
    position: _Position,
    volume: float,
    positions: list[_Position],
    exits: list[ExitRecord],
    index: int,
    reason: str,
    execution: ExecutionAssumptions,
    realized_minor: int,
    money_unknown: bool,
    blockers: list[str],
) -> tuple[int, bool]:
    volume = min(position.volume, _clean_volume(volume))
    exit_price = _adverse_exit_price(path, index, execution)
    pnl_minor, exact = _money_minor(
        path,
        position.entry_price,
        exit_price,
        volume,
        index,
    )
    if not exact:
        blockers.append(f"stale_conversion_at_exit:{index}")
        money_unknown = True
    else:
        realized_minor += pnl_minor
    exits.append(ExitRecord(
        ticket=position.ticket,
        tick_index=index,
        closed_at=_ns_datetime(int(path.times_ns[index])),
        entry_price=position.entry_price,
        exit_price=exit_price,
        volume=volume,
        pnl_eur=(
            _minor_decimal(pnl_minor, path.currency_digits) if exact else None
        ),
        reason=reason,
    ))
    position.volume = _clean_volume(position.volume - volume)
    if position.volume <= 1e-12:
        positions.remove(position)
    return realized_minor, money_unknown


def _basket_minor(
    path: DubaiPath,
    positions: Iterable[_Position],
    index: int,
    execution: ExecutionAssumptions,
) -> tuple[int, bool]:
    exit_price = _adverse_exit_price(path, index, execution)
    total = 0
    exact = True
    for position in positions:
        value, value_exact = _money_minor(
            path,
            position.entry_price,
            exit_price,
            position.volume,
            index,
        )
        total += value
        exact = exact and value_exact
    return total, exact


def _money_minor(
    path: DubaiPath,
    entry_price: float,
    exit_price: float,
    volume: float,
    index: int,
) -> tuple[int, bool]:
    raw = (
        _direction_sign(path.direction)
        * (exit_price - entry_price)
        * path.contract_size
        * volume
    )
    orientation = path.conversion_orientation
    exact = orientation == "identity" or bool(path.fx_valid[index])
    if orientation == "account_base_profit_quote":
        quote = float(path.fx_ask[index] if raw >= 0 else path.fx_bid[index])
        raw = raw / quote
    elif orientation == "profit_base_account_quote":
        quote = float(path.fx_bid[index] if raw >= 0 else path.fx_ask[index])
        raw = raw * quote
    elif orientation != "identity":
        return 0, False
    return _round_minor(raw, path.currency_digits), exact


def _round_minor(value: float, digits: int) -> int:
    scale = 10 ** digits
    scaled = value * scale
    return math.floor(scaled + 0.5) if scaled >= 0 else math.ceil(scaled - 0.5)


def _money_value_to_minor(value: float, digits: int) -> int:
    return _round_minor(value, digits)


def _minor_decimal(value: int, digits: int) -> Decimal:
    quantum = Decimal(1).scaleb(-digits)
    return Decimal(value).scaleb(-digits).quantize(quantum)


def _level_hit(direction: str, price: float, level: float, *, kind: str) -> bool:
    if kind == "target":
        return price >= level if direction == "BUY" else price <= level
    return price <= level if direction == "BUY" else price >= level


def _entry_quote(path: DubaiPath, index: int) -> float:
    return float(path.ask[index] if path.direction == "BUY" else path.bid[index])


def _adverse_entry_price(
    direction: str,
    price: float,
    execution: ExecutionAssumptions,
) -> float:
    cost = execution.entry_slippage + execution.spread_addition
    return _clean_price(price + cost if direction == "BUY" else price - cost)


def _adverse_exit_price(
    path: DubaiPath,
    index: int,
    execution: ExecutionAssumptions,
) -> float:
    price = float(path.exit_quotes[index])
    cost = execution.exit_slippage + execution.spread_addition
    return _clean_price(price - cost if path.direction == "BUY" else price + cost)


def _direction_sign(direction: str) -> int:
    return 1 if direction == "BUY" else -1


def _is_provider_close(action: str) -> bool:
    normalized = str(action).upper()
    return "CLOSE" in normalized or normalized in {"EXIT", "CERRAR"}


def _looks_like_be(source: str) -> bool:
    normalized = str(source).upper()
    return "BE" in normalized or "BREAK EVEN" in normalized or "BREAKEVEN" in normalized


def _path_contract_blockers(path: DubaiPath) -> list[str]:
    lengths = {
        len(path.times_ns),
        len(path.bid),
        len(path.ask),
        len(path.exit_quotes),
        len(path.fx_bid),
        len(path.fx_ask),
        len(path.fx_age_ms),
        len(path.fx_valid),
    }
    blockers: list[str] = []
    if len(lengths) != 1 or not path.times_ns.size:
        blockers.append("invalid_path_lengths")
    elif np.any(np.diff(path.times_ns) < 0):
        blockers.append("non_monotonic_path_time")
    if path.direction not in {"BUY", "SELL"}:
        blockers.append("invalid_path_direction")
    if path.contract_size <= 0 or path.currency_digits < 0:
        blockers.append("invalid_path_money_contract")
    if not path.legs:
        blockers.append("path_without_entry_evidence")
    return blockers


def _tick_is_usable(path: DubaiPath, index: int) -> bool:
    bid = float(path.bid[index])
    ask = float(path.ask[index])
    return (
        math.isfinite(bid)
        and math.isfinite(ask)
        and bid > 0
        and ask >= bid
    )


def _empty_result(
    path: DubaiPath,
    genome: StrategyGenome,
    *,
    blockers: Iterable[str],
    confidence_layer: str = "unclassified",
    unfilled: bool = False,
) -> SimulationResult:
    zero = _minor_decimal(0, max(0, path.currency_digits))
    return SimulationResult(
        signal_id=path.signal_id,
        strategy_fingerprint=genome.fingerprint,
        confidence_layer=confidence_layer,
        entries=(),
        exits=(),
        pnl_eur=zero if not blockers else None,
        exit_reason="not_filled" if unfilled else "blocked",
        max_favourable_eur=zero if not blockers else None,
        max_adverse_eur=zero if not blockers else None,
        max_floating_drawdown_eur=zero if not blockers else None,
        max_favourable_move=0.0,
        max_adverse_move=0.0,
        blockers=tuple(dict.fromkeys(blockers)),
        last_tick_index=-1,
        unfilled=unfilled,
        filled_volume=0.0,
    )


def _datetime_ns(value: datetime) -> int:
    utc = value.astimezone(timezone.utc)
    return int(utc.timestamp() * 1_000_000_000)


def _ns_datetime(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1_000_000_000, tz=timezone.utc)


def _clean_price(value: float) -> float:
    return round(float(value), 10)


def _clean_volume(value: float) -> float:
    return round(float(value), 10)
