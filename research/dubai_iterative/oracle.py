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
    volume: float
    entry_price: float
    opened_ns: int
    tp_events: tuple[LevelEvent, ...]
    sl_events: tuple[LevelEvent, ...]
    be_stop: float | None = None
    be_reason: str = "break_even"


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

        due: list[ProviderEvent] = []
        while provider_cursor < len(provider_events) and _to_ns(provider_events[provider_cursor].observed_at) <= now_ns:
            due.append(provider_events[provider_cursor])
            provider_cursor += 1
        if genome.be_mode == "provider":
            _provider_protection(active, due)
        if genome.provider_management_mode in {"exact", "close_only"} and any(_provider_close(item.action) for item in due):
            realized_minor, unknown_money = _close_all(
                path, active, exits, index, "provider_close", execution,
                realized_minor, unknown_money, blockers,
            )
            reason = "provider_close"
            break

        for position in tuple(active):
            level, stop_reason = _stop_level(position, genome, now_ns, path.direction)
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
                target = _latest_level(position.tp_events, now_ns, include_be=True)
                if target is None or not _hit(path.direction, raw_exit, target, target=True):
                    continue
                realized_minor, unknown_money = _close_one(
                    path, position, position.volume, active, exits, index,
                    "provider_tp", execution, realized_minor, unknown_money, blockers,
                )
                reason = "provider_tp"
        elif genome.target_mode == "provider_target_all":
            target = _provider_target(path, genome, now_ns)
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

        if active and first_open_ns is not None and now_ns - first_open_ns >= genome.time_exit_min * 60 * 1_000_000_000:
            realized_minor, unknown_money = _close_all(
                path, active, exits, index, "time_exit", execution,
                realized_minor, unknown_money, blockers,
            )
            reason = "time_exit"
            break

    if active:
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
) -> OracleCertificate:
    fast_by_signal = {str(item.signal_id): item for item in fast_results}
    independent = tuple(oracle_simulate(path, genome) for path in paths)
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
    status = "pass" if not mismatches else "blocked"
    return OracleCertificate(
        status=status,
        mismatches=tuple(mismatches),
        oracle_results=independent,
        promotion_eligible=not mismatches,
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
    times = [int(item) for item in path.times_ns]
    if genome.entry_mode == "actual_mt5":
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
                    volume=float(genome.volume_weights[index]),
                    entry_price=_entry_with_cost(path.direction, base, execution),
                    opened_ns=opened_ns,
                    tp_events=template.tp_events,
                    sl_events=template.sl_events,
                ),
            ))
        scheduled.sort(key=lambda item: (item.index, item.position.ticket))
        confidence = "observed_entry_management" if exact_volume else "counterfactual_entry"
        return tuple(scheduled), confidence, ()

    entry_index = _causal_index(path, genome, execution)
    if entry_index is None or not _context_allowed(path, genome, entry_index):
        return (), "counterfactual_entry", ()
    entry_ns = int(path.times_ns[entry_index])
    price = _entry_with_cost(path.direction, _entry_quote(path, entry_index), execution)
    scheduled = tuple(
        _Scheduled(
            entry_index,
            f"causal_{genome.entry_mode}",
            _Position(
                ticket=f"sim_{index + 1}",
                role=path.legs[min(index, len(path.legs) - 1)].role,
                volume=float(volume),
                entry_price=price,
                opened_ns=entry_ns,
                tp_events=path.legs[min(index, len(path.legs) - 1)].tp_events,
                sl_events=path.legs[min(index, len(path.legs) - 1)].sl_events,
            ),
        )
        for index, volume in enumerate(genome.volume_weights)
    )
    return scheduled, "counterfactual_entry", ()


def _causal_index(path: DubaiPath, genome: StrategyGenome, execution: ExecutionScenario) -> int | None:
    times = [int(item) for item in path.times_ns]
    signal_ns = _to_ns(path.signal_observed_at)
    start_ns = signal_ns + execution.latency_ms * 1_000_000
    start_index = bisect_left(times, start_ns)
    if start_index >= len(times):
        return None
    expiry_ns = signal_ns + genome.entry_expiry_min * 60 * 1_000_000_000
    if genome.entry_mode == "delay":
        target_ns = start_ns + int(float(genome.entry_value) * 1_000_000_000)
        index = bisect_left(times, target_ns)
        return index if index < len(times) and times[index] <= expiry_ns else None
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


def _context_allowed(path: DubaiPath, genome: StrategyGenome, index: int) -> bool:
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
        target = _latest_level(path.legs[0].tp_events, now_ns, include_be=True)
        stop = _latest_level(path.legs[0].sl_events, now_ns, include_be=False)
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


def _stop_level(position: _Position, genome: StrategyGenome, now_ns: int, direction: str) -> tuple[float | None, str]:
    base = None
    reason = "provider_sl"
    if genome.stop_mode == "provider":
        base = _latest_level(position.sl_events, now_ns, include_be=genome.be_mode == "provider")
    elif genome.stop_mode == "fixed_move":
        base = position.entry_price - _sign(direction) * float(genome.stop_value)
        reason = "fixed_sl"
    if position.be_stop is None:
        return base, reason
    if base is None:
        return position.be_stop, position.be_reason
    tighter = max(base, position.be_stop) if direction == "BUY" else min(base, position.be_stop)
    return (tighter, position.be_reason) if math.isclose(tighter, position.be_stop, abs_tol=1e-12) else (tighter, reason)


def _latest_level(events: Iterable[LevelEvent], now_ns: int, *, include_be: bool) -> float | None:
    latest = None
    for event in events:
        if event.status not in {"confirmed", "snapshot"}:
            continue
        if not include_be and _be_source(event.source):
            continue
        if _to_ns(event.observed_at) <= now_ns:
            latest = event.level
        else:
            break
    return latest


def _provider_target(path: DubaiPath, genome: StrategyGenome, now_ns: int) -> float | None:
    targets = []
    for leg in path.legs:
        value = _latest_level(leg.tp_events, now_ns, include_be=True)
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
        total += value
        exact = exact and current_exact
    return total, exact


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
