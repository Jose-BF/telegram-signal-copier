"""Compiled exact-money evaluator used only by the offline strategy search.

The scalar engine remains the readable reference implementation.  This module
prepares the same causal inputs as that engine and executes the per-tick state
machine with Numba.  Finalists must still pass the independent scalar oracle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import math
from threading import Lock
from typing import Iterable

import numpy as np
from numba import njit

from .contracts import StrategyGenome
from .dataset import DubaiPath, LevelEvent, ProviderEvent
from .engine import (
    EntryRecord,
    ExecutionAssumptions,
    ExitRecord,
    SimulationResult,
    _datetime_ns,
    _empty_result,
    _is_provider_close,
    _looks_like_be,
    _path_contract_blockers,
    _prepare_entries,
)


PRICE_SCALE = 100
FX_SCALE = 100_000
VOLUME_SCALE = 100
MAX_EXITS_PER_POSITION = 2

ORIENTATION_IDENTITY = 0
ORIENTATION_ACCOUNT_BASE = 1
ORIENTATION_PROFIT_BASE = 2

TARGET_PROVIDER_LEG = 0
TARGET_PROVIDER_ALL = 1
TARGET_FIXED_BASKET = 2
TARGET_PARTIAL_RUNNER = 3
TARGET_NONE = 4
TARGET_FIXED_MOVE = 5

BE_PROVIDER = 0
BE_NONE = 1
BE_PRICE = 2
BE_DELAYED = 3
BE_PARTIAL = 4

STOP_PROVIDER = 0
STOP_FIXED_MOVE = 1
STOP_BASKET = 2
STOP_NONE = 3

MANAGEMENT_EXACT = 0
MANAGEMENT_CLOSE_ONLY = 1
MANAGEMENT_IGNORE = 2

PROVIDER_OTHER = 0
PROVIDER_MOVE_BE = 1
PROVIDER_MOVE_PRICE = 2
PROVIDER_CLOSE = 3

REASON_NOT_CLOSED = 0
REASON_BASKET_STOP = 1
REASON_PROVIDER_CLOSE = 2
REASON_PROVIDER_SL = 3
REASON_FIXED_SL = 4
REASON_BREAK_EVEN = 5
REASON_PROVIDER_SL_MOVE = 6
REASON_PROVIDER_TP = 7
REASON_PROVIDER_TARGET_ALL = 8
REASON_BASKET_TARGET = 9
REASON_PARTIAL_TARGET = 10
REASON_RUNNER_TARGET = 11
REASON_PROFIT_LOCK = 12
REASON_TIME_EXIT = 13
REASON_DATA_END = 14
REASON_FIXED_MOVE_TARGET = 15

BLOCK_INVALID_TICK = 1
BLOCK_STALE_BASKET_STOP = 2
BLOCK_STALE_BASKET_TARGET = 4
BLOCK_STALE_PROFIT_LOCK = 8
BLOCK_STALE_EXIT = 16
BLOCK_PATH_ENDED = 32


_TARGET_CODES = {
    "provider_per_leg": TARGET_PROVIDER_LEG,
    "provider_target_all": TARGET_PROVIDER_ALL,
    "fixed_basket": TARGET_FIXED_BASKET,
    "fixed_move": TARGET_FIXED_MOVE,
    "partial_runner": TARGET_PARTIAL_RUNNER,
    "none": TARGET_NONE,
}
_BE_CODES = {
    "provider": BE_PROVIDER,
    "none": BE_NONE,
    "price": BE_PRICE,
    "delayed": BE_DELAYED,
    "partial": BE_PARTIAL,
}
_STOP_CODES = {
    "provider": STOP_PROVIDER,
    "fixed_move": STOP_FIXED_MOVE,
    "basket_money": STOP_BASKET,
    "none": STOP_NONE,
}
_MANAGEMENT_CODES = {
    "exact": MANAGEMENT_EXACT,
    "close_only": MANAGEMENT_CLOSE_ONLY,
    "ignore": MANAGEMENT_IGNORE,
}
_REASON_NAMES = {
    REASON_NOT_CLOSED: "not_closed",
    REASON_BASKET_STOP: "basket_stop",
    REASON_PROVIDER_CLOSE: "provider_close",
    REASON_PROVIDER_SL: "provider_sl",
    REASON_FIXED_SL: "fixed_sl",
    REASON_BREAK_EVEN: "break_even",
    REASON_PROVIDER_SL_MOVE: "provider_sl_move",
    REASON_PROVIDER_TP: "provider_tp",
    REASON_PROVIDER_TARGET_ALL: "provider_target_all",
    REASON_BASKET_TARGET: "basket_target",
    REASON_PARTIAL_TARGET: "partial_target",
    REASON_RUNNER_TARGET: "runner_target",
    REASON_PROFIT_LOCK: "profit_lock",
    REASON_TIME_EXIT: "time_exit",
    REASON_DATA_END: "data_end",
    REASON_FIXED_MOVE_TARGET: "fixed_move_target",
}


class FastPathUnsupported(ValueError):
    """Raised when fixed-point research cannot represent a path exactly."""


@dataclass(frozen=True)
class _CompiledPath:
    direction: int
    orientation: int
    times_ns: np.ndarray
    bid: np.ndarray
    ask: np.ndarray
    exit_quote: np.ndarray
    fx_bid: np.ndarray
    fx_ask: np.ndarray
    fx_valid: np.ndarray
    contract_size: int
    provider_indices: np.ndarray
    provider_actions: np.ndarray
    provider_prices: np.ndarray
    all_tp_indices: np.ndarray
    all_tp_levels: np.ndarray
    all_tp_counts: np.ndarray


class FastEvaluator:
    """Cache fixed-point paths and evaluate genomes through the JIT kernel."""

    def __init__(
        self,
        *,
        execution: ExecutionAssumptions | None = None,
    ) -> None:
        self._cache: dict[tuple[int, int], _CompiledPath] = {}
        self._cache_lock = Lock()
        self.execution = execution or ExecutionAssumptions()

    def __call__(self, path: DubaiPath, genome: StrategyGenome) -> SimulationResult:
        blockers = tuple(genome.validation_errors()) + tuple(_path_contract_blockers(path))
        if blockers:
            return _empty_result(path, genome, blockers=blockers)
        try:
            cache_key = (id(path), self.execution.latency_ms)
            compiled = self._cache.get(cache_key)
            if compiled is None:
                with self._cache_lock:
                    compiled = self._cache.get(cache_key)
                    if compiled is None:
                        compiled = _compile_path(
                            path,
                            observation_latency_ns=(
                                self.execution.latency_ms * 1_000_000
                            ),
                        )
                        self._cache[cache_key] = compiled
            return _simulate_compiled(path, compiled, genome, self.execution)
        except FastPathUnsupported as exc:
            return _empty_result(
                path,
                genome,
                blockers=(f"fast_path_unsupported:{exc}",),
            )


def _compile_path(
    path: DubaiPath,
    *,
    observation_latency_ns: int = 0,
) -> _CompiledPath:
    if path.currency_digits != 2:
        raise FastPathUnsupported("currency_digits")
    contract_size = _exact_integer(path.contract_size, "contract_size")
    orientation = {
        "identity": ORIENTATION_IDENTITY,
        "account_base_profit_quote": ORIENTATION_ACCOUNT_BASE,
        "profit_base_account_quote": ORIENTATION_PROFIT_BASE,
    }.get(path.conversion_orientation)
    if orientation is None:
        raise FastPathUnsupported("conversion_orientation")
    bid = _fixed_array(path.bid, PRICE_SCALE, "bid")
    ask = _fixed_array(path.ask, PRICE_SCALE, "ask")
    exit_quote = bid if path.direction == "BUY" else ask
    if orientation == ORIENTATION_IDENTITY:
        fx_bid = np.full(len(path.times_ns), FX_SCALE, dtype=np.int64)
        fx_ask = fx_bid.copy()
    else:
        fx_bid = _fixed_array(path.fx_bid, FX_SCALE, "fx_bid", allow_stale=True)
        fx_ask = _fixed_array(path.fx_ask, FX_SCALE, "fx_ask", allow_stale=True)
    provider_indices, provider_actions, provider_prices = _provider_arrays(
        path.provider_events,
        path.times_ns,
        observation_latency_ns=observation_latency_ns,
    )
    tp_indices, tp_levels, tp_counts, _ = _level_matrices(
        tuple(leg.tp_events for leg in path.legs),
        path.times_ns,
        observation_latency_ns=observation_latency_ns,
    )
    return _CompiledPath(
        direction=1 if path.direction == "BUY" else -1,
        orientation=orientation,
        times_ns=np.ascontiguousarray(path.times_ns, dtype=np.int64),
        bid=bid,
        ask=ask,
        exit_quote=exit_quote,
        fx_bid=fx_bid,
        fx_ask=fx_ask,
        fx_valid=np.ascontiguousarray(path.fx_valid, dtype=np.bool_),
        contract_size=contract_size,
        provider_indices=provider_indices,
        provider_actions=provider_actions,
        provider_prices=provider_prices,
        all_tp_indices=tp_indices,
        all_tp_levels=tp_levels,
        all_tp_counts=tp_counts,
    )


def _simulate_compiled(
    path: DubaiPath,
    compiled: _CompiledPath,
    genome: StrategyGenome,
    execution: ExecutionAssumptions,
) -> SimulationResult:
    scheduled, confidence, entry_blockers = _prepare_entries(
        path,
        genome,
        execution,
    )
    if entry_blockers:
        return _empty_result(
            path,
            genome,
            blockers=entry_blockers,
            confidence_layer=confidence,
        )
    if not scheduled:
        return _empty_result(
            path,
            genome,
            blockers=(),
            confidence_layer=confidence,
            unfilled=True,
        )

    count = len(scheduled)
    schedule_indices = np.asarray([item.tick_index for item in scheduled], dtype=np.int64)
    entry_prices = _fixed_array(
        np.asarray([item.position.entry_price for item in scheduled]),
        PRICE_SCALE,
        "entry_price",
    )
    volume_units = _fixed_array(
        np.asarray([item.position.volume for item in scheduled]),
        VOLUME_SCALE,
        "volume",
    )
    opened_ns = np.asarray([item.position.opened_ns for item in scheduled], dtype=np.int64)
    role_codes = np.asarray(
        [0 if item.position.role == "market_a" else 1 for item in scheduled],
        dtype=np.int8,
    )
    tp_indices, tp_levels, tp_counts, _ = _level_matrices(
        tuple(item.position.tp_events for item in scheduled),
        compiled.times_ns,
        observation_latency_ns=execution.latency_ms * 1_000_000,
    )
    sl_indices, sl_levels, sl_counts, sl_be = _level_matrices(
        tuple(item.position.sl_events for item in scheduled),
        compiled.times_ns,
        observation_latency_ns=execution.latency_ms * 1_000_000,
    )
    output = _kernel(
        compiled.direction,
        compiled.orientation,
        compiled.contract_size,
        compiled.times_ns,
        compiled.bid,
        compiled.ask,
        compiled.exit_quote,
        _price_points(execution.exit_slippage + execution.spread_addition),
        compiled.fx_bid,
        compiled.fx_ask,
        compiled.fx_valid,
        schedule_indices,
        entry_prices,
        volume_units,
        opened_ns,
        role_codes,
        tp_indices,
        tp_levels,
        tp_counts,
        sl_indices,
        sl_levels,
        sl_counts,
        sl_be,
        compiled.all_tp_indices,
        compiled.all_tp_levels,
        compiled.all_tp_counts,
        compiled.provider_indices,
        compiled.provider_actions,
        compiled.provider_prices,
        _TARGET_CODES[genome.target_mode],
        _minor(genome.target_value),
        _price_points(genome.target_value)
        if genome.target_mode == "fixed_move"
        else 0,
        _minor(genome.runner_target),
        float(genome.partial_fraction),
        _BE_CODES[genome.be_mode],
        _price_points(genome.be_trigger)
        if genome.be_mode in {"price", "partial"}
        else 0,
        _minutes_ns(genome.be_trigger) if genome.be_mode == "delayed" else 0,
        _STOP_CODES[genome.stop_mode],
        _price_points(genome.stop_value) if genome.stop_mode == "fixed_move" else 0,
        _minor(genome.stop_value) if genome.stop_mode == "basket_money" else 0,
        _minor(genome.profit_lock_arm),
        _minor(genome.profit_lock_giveback),
        int(genome.time_exit_min) * 60 * 1_000_000_000,
        _MANAGEMENT_CODES[genome.provider_management_mode],
        max(1, int(round(float(genome.target_value or 1.0)))) - 1,
    )
    return _result_from_kernel(path, genome, scheduled, confidence, output)


def _result_from_kernel(path, genome, scheduled, confidence, output) -> SimulationResult:
    (
        pnl_minor,
        exit_reason_code,
        max_total_minor,
        min_total_minor,
        drawdown_minor,
        max_favourable_points,
        max_adverse_points,
        blocker_mask,
        blocker_index,
        last_tick_index,
        unfilled,
        entries_seen,
        filled_units,
        exit_count,
        exit_slots,
        exit_indices,
        exit_entry_prices,
        exit_prices,
        exit_volumes,
        exit_pnls,
        exit_exact,
        exit_reasons,
    ) = output
    blockers = _blockers(int(blocker_mask), int(blocker_index))
    entries = tuple(
        EntryRecord(
            ticket=item.position.ticket,
            tick_index=int(item.tick_index),
            opened_at=datetime.fromtimestamp(
                item.position.opened_ns / 1_000_000_000,
                tz=timezone.utc,
            ),
            entry_price=float(item.position.entry_price),
            volume=float(item.position.volume),
            source=item.source,
        )
        for item in scheduled[:int(entries_seen)]
    )
    exits: list[ExitRecord] = []
    for offset in range(int(exit_count)):
        slot = int(exit_slots[offset])
        index = int(exit_indices[offset])
        exits.append(ExitRecord(
            ticket=scheduled[slot].position.ticket,
            tick_index=index,
            closed_at=datetime.fromtimestamp(
                int(path.times_ns[index]) / 1_000_000_000,
                tz=timezone.utc,
            ),
            entry_price=float(exit_entry_prices[offset]) / PRICE_SCALE,
            exit_price=float(exit_prices[offset]) / PRICE_SCALE,
            volume=float(exit_volumes[offset]) / VOLUME_SCALE,
            pnl_eur=(
                _decimal_minor(int(exit_pnls[offset]), path.currency_digits)
                if bool(exit_exact[offset])
                else None
            ),
            reason=_REASON_NAMES[int(exit_reasons[offset])],
        ))
    money_unknown = bool(blocker_mask & (
        BLOCK_STALE_BASKET_STOP
        | BLOCK_STALE_BASKET_TARGET
        | BLOCK_STALE_PROFIT_LOCK
        | BLOCK_STALE_EXIT
    ))
    return SimulationResult(
        signal_id=path.signal_id,
        strategy_fingerprint=genome.fingerprint,
        confidence_layer=confidence,
        entries=entries,
        exits=tuple(exits),
        pnl_eur=None if money_unknown else _decimal_minor(int(pnl_minor), path.currency_digits),
        exit_reason=_REASON_NAMES[int(exit_reason_code)],
        max_favourable_eur=(
            None if max_total_minor == np.iinfo(np.int64).min
            else _decimal_minor(int(max_total_minor), path.currency_digits)
        ),
        max_adverse_eur=(
            None if min_total_minor == np.iinfo(np.int64).max
            else _decimal_minor(int(min_total_minor), path.currency_digits)
        ),
        max_floating_drawdown_eur=(
            None if max_total_minor == np.iinfo(np.int64).min
            else _decimal_minor(int(drawdown_minor), path.currency_digits)
        ),
        max_favourable_move=round(float(max_favourable_points) / PRICE_SCALE, 10),
        max_adverse_move=round(float(max_adverse_points) / PRICE_SCALE, 10),
        blockers=blockers,
        last_tick_index=int(last_tick_index),
        unfilled=bool(unfilled),
        filled_volume=round(float(filled_units) / VOLUME_SCALE, 10),
    )


def _provider_arrays(
    events: Iterable[ProviderEvent],
    times: np.ndarray,
    *,
    observation_latency_ns: int = 0,
):
    rows: list[tuple[int, int, int]] = []
    for event in sorted(events, key=lambda item: _datetime_ns(item.observed_at)):
        action = event.action.upper()
        if action == "MOVE_SL_TO_BE":
            code = PROVIDER_MOVE_BE
        elif action == "MOVE_SL_TO_PRICE":
            code = PROVIDER_MOVE_PRICE
        elif _is_provider_close(action):
            code = PROVIDER_CLOSE
        else:
            code = PROVIDER_OTHER
        price = 0
        if code == PROVIDER_MOVE_PRICE:
            price = _provider_price(event)
        index = int(np.searchsorted(
            times,
            _datetime_ns(event.observed_at) + observation_latency_ns,
            side="left",
        ))
        rows.append((index, code, price))
    return (
        np.asarray([row[0] for row in rows], dtype=np.int64),
        np.asarray([row[1] for row in rows], dtype=np.int8),
        np.asarray([row[2] for row in rows], dtype=np.int64),
    )


def _provider_price(event: ProviderEvent) -> int:
    from .engine import _provider_announced_price

    value = _provider_announced_price(event)
    return 0 if value is None else _fixed_scalar(value, PRICE_SCALE, "provider_price")


def _level_matrices(groups, times, *, observation_latency_ns: int = 0):
    width = max((sum(1 for item in group if item.status in {"confirmed", "snapshot"}) for group in groups), default=0)
    width = max(1, width)
    indices = np.full((len(groups), width), np.iinfo(np.int64).max, dtype=np.int64)
    levels = np.zeros((len(groups), width), dtype=np.int64)
    is_be = np.zeros((len(groups), width), dtype=np.bool_)
    counts = np.zeros(len(groups), dtype=np.int64)
    for row, group in enumerate(groups):
        valid = [item for item in group if item.status in {"confirmed", "snapshot"}]
        valid.sort(key=lambda item: (item.observed_at, item.level))
        counts[row] = len(valid)
        for column, event in enumerate(valid):
            indices[row, column] = int(np.searchsorted(
                times,
                _datetime_ns(event.observed_at) + observation_latency_ns,
                side="left",
            ))
            levels[row, column] = _fixed_scalar(event.level, PRICE_SCALE, "level")
            is_be[row, column] = _looks_like_be(event.source)
    return indices, levels, counts, is_be


def _fixed_array(values, scale, name, *, allow_stale=False):
    source = np.asarray(values, dtype=np.float64)
    finite = np.isfinite(source)
    if not allow_stale and not finite.all():
        raise FastPathUnsupported(name)
    safe = np.where(finite, source, 0.0)
    scaled = np.rint(safe * scale)
    if np.any(np.abs(safe * scale - scaled) > 1e-7):
        raise FastPathUnsupported(f"{name}_precision")
    if np.any(np.abs(scaled) > np.iinfo(np.int64).max):
        raise FastPathUnsupported(f"{name}_range")
    return np.ascontiguousarray(scaled, dtype=np.int64)


def _fixed_scalar(value, scale, name):
    number = float(value)
    scaled = round(number * scale)
    if not math.isfinite(number) or abs(number * scale - scaled) > 1e-7:
        raise FastPathUnsupported(f"{name}_precision")
    return int(scaled)


def _exact_integer(value, name):
    number = float(value)
    rounded = round(number)
    if not math.isfinite(number) or not math.isclose(number, rounded, abs_tol=1e-12):
        raise FastPathUnsupported(name)
    return int(rounded)


def _price_points(value):
    if value is None:
        return 0
    return int(
        (Decimal(str(value)) * PRICE_SCALE).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )


def _minor(value):
    if value is None:
        return 0
    return int(
        (Decimal(str(value)) * Decimal(100)).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )


def _minutes_ns(value):
    if value is None:
        return 0
    return int(Decimal(str(value)) * Decimal(60_000_000_000))


def _decimal_minor(value: int, digits: int) -> Decimal:
    quantum = Decimal(1).scaleb(-digits)
    return Decimal(value).scaleb(-digits).quantize(quantum)


def _blockers(mask: int, index: int) -> tuple[str, ...]:
    rows: list[str] = []
    if mask & BLOCK_INVALID_TICK:
        rows.append(f"invalid_tick_at_index:{index}")
    if mask & BLOCK_STALE_BASKET_STOP:
        rows.append("stale_conversion_at_basket_stop")
    if mask & BLOCK_STALE_BASKET_TARGET:
        rows.append("stale_conversion_at_basket_target")
    if mask & BLOCK_STALE_PROFIT_LOCK:
        rows.append("stale_conversion_during_profit_lock")
    if mask & BLOCK_STALE_EXIT:
        rows.append(f"stale_conversion_at_exit:{index}")
    if mask & BLOCK_PATH_ENDED:
        rows.append("path_ended_before_strategy_exit")
    return tuple(rows)


@njit(cache=True)
def _round_ratio(num: int, den: int) -> int:
    if den <= 0:
        return 0
    absolute = num if num >= 0 else -num
    rounded = (absolute * 2 + den) // (2 * den)
    return rounded if num >= 0 else -rounded


@njit(cache=True)
def _money_minor_fixed(direction, orientation, contract_size, entry, exit_price, volume, fx_bid, fx_ask, fx_valid):
    raw_sign = direction * (exit_price - entry)
    if orientation == ORIENTATION_IDENTITY:
        numerator = raw_sign * contract_size * volume * 100
        denominator = PRICE_SCALE * VOLUME_SCALE
        return _round_ratio(numerator, denominator), True
    quote = fx_ask if raw_sign >= 0 else fx_bid
    if quote <= 0:
        return 0, False
    if orientation == ORIENTATION_ACCOUNT_BASE:
        numerator = raw_sign * contract_size * volume * FX_SCALE * 100
        denominator = PRICE_SCALE * VOLUME_SCALE * quote
    else:
        quote = fx_bid if raw_sign >= 0 else fx_ask
        numerator = raw_sign * contract_size * volume * quote * 100
        denominator = PRICE_SCALE * VOLUME_SCALE * FX_SCALE
    return _round_ratio(numerator, denominator), bool(fx_valid)


@njit(cache=True, nogil=True)
def _kernel(
    direction,
    orientation,
    contract_size,
    times,
    bid,
    ask,
    exit_quote,
    exit_cost_points,
    fx_bid,
    fx_ask,
    fx_valid,
    schedule_indices,
    entry_prices,
    initial_volumes,
    opened_ns,
    role_codes,
    tp_indices,
    tp_levels,
    tp_counts,
    sl_indices,
    sl_levels,
    sl_counts,
    sl_be,
    all_tp_indices,
    all_tp_levels,
    all_tp_counts,
    provider_indices,
    provider_actions,
    provider_prices,
    target_mode,
    target_minor,
    target_points,
    runner_minor,
    partial_fraction,
    be_mode,
    be_trigger_points,
    be_delay_ns,
    stop_mode,
    stop_points,
    stop_minor,
    lock_arm_minor,
    lock_giveback_minor,
    time_exit_ns,
    management_mode,
    provider_target_index,
):
    position_count = len(schedule_indices)
    active = np.zeros(position_count, dtype=np.bool_)
    volumes = initial_volumes.copy()
    be_stops = np.zeros(position_count, dtype=np.int64)
    be_reasons = np.full(position_count, REASON_BREAK_EVEN, dtype=np.int8)
    tp_cursor = np.zeros(position_count, dtype=np.int64)
    sl_cursor = np.zeros(position_count, dtype=np.int64)
    current_tp = np.zeros(position_count, dtype=np.int64)
    current_sl_all = np.zeros(position_count, dtype=np.int64)
    current_sl_nonbe = np.zeros(position_count, dtype=np.int64)
    all_tp_cursor = np.zeros(len(all_tp_counts), dtype=np.int64)
    current_all_tp = np.zeros(len(all_tp_counts), dtype=np.int64)

    max_exits = max(1, position_count * MAX_EXITS_PER_POSITION)
    exit_slots = np.zeros(max_exits, dtype=np.int64)
    exit_indices = np.zeros(max_exits, dtype=np.int64)
    exit_entry_prices = np.zeros(max_exits, dtype=np.int64)
    exit_prices = np.zeros(max_exits, dtype=np.int64)
    exit_volumes = np.zeros(max_exits, dtype=np.int64)
    exit_pnls = np.zeros(max_exits, dtype=np.int64)
    exit_exact = np.zeros(max_exits, dtype=np.bool_)
    exit_reasons = np.zeros(max_exits, dtype=np.int8)
    exit_count = 0

    schedule_cursor = 0
    provider_cursor = 0
    realized = 0
    money_unknown = False
    partial_taken = False
    lock_armed = False
    max_total = np.iinfo(np.int64).min
    min_total = np.iinfo(np.int64).max
    max_drawdown = 0
    max_favourable_points = 0
    max_adverse_points = 0
    last_index = -1
    exit_reason = REASON_NOT_CLOSED
    first_open_ns = np.iinfo(np.int64).max
    blocker_mask = 0
    blocker_index = -1
    entries_seen = 0
    active_count = 0

    for index in range(len(times)):
        now = times[index]
        while schedule_cursor < position_count and schedule_indices[schedule_cursor] == index:
            active[schedule_cursor] = True
            active_count += 1
            entries_seen += 1
            if opened_ns[schedule_cursor] < first_open_ns:
                first_open_ns = opened_ns[schedule_cursor]
            schedule_cursor += 1
        if active_count == 0:
            if schedule_cursor >= position_count and entries_seen > 0:
                break
            continue
        if bid[index] <= 0 or ask[index] < bid[index]:
            blocker_mask |= BLOCK_INVALID_TICK
            blocker_index = index
            last_index = index
            break
        last_index = index

        for slot in range(position_count):
            while tp_cursor[slot] < tp_counts[slot] and tp_indices[slot, tp_cursor[slot]] <= index:
                current_tp[slot] = tp_levels[slot, tp_cursor[slot]]
                tp_cursor[slot] += 1
            while sl_cursor[slot] < sl_counts[slot] and sl_indices[slot, sl_cursor[slot]] <= index:
                current_sl_all[slot] = sl_levels[slot, sl_cursor[slot]]
                if not sl_be[slot, sl_cursor[slot]]:
                    current_sl_nonbe[slot] = sl_levels[slot, sl_cursor[slot]]
                sl_cursor[slot] += 1
        for slot in range(len(all_tp_counts)):
            while all_tp_cursor[slot] < all_tp_counts[slot] and all_tp_indices[slot, all_tp_cursor[slot]] <= index:
                current_all_tp[slot] = all_tp_levels[slot, all_tp_cursor[slot]]
                all_tp_cursor[slot] += 1

        raw_exit = exit_quote[index]
        money_exit = raw_exit - direction * exit_cost_points
        for slot in range(position_count):
            if not active[slot]:
                continue
            move = direction * (raw_exit - entry_prices[slot])
            if move > max_favourable_points:
                max_favourable_points = move
            if move < max_adverse_points:
                max_adverse_points = move
            if be_mode == BE_PRICE and move >= be_trigger_points:
                be_stops[slot] = entry_prices[slot]
                be_reasons[slot] = REASON_BREAK_EVEN
            elif be_mode == BE_DELAYED and now - opened_ns[slot] >= be_delay_ns:
                be_stops[slot] = entry_prices[slot]
                be_reasons[slot] = REASON_BREAK_EVEN
            elif be_mode == BE_PARTIAL and move >= be_trigger_points and role_codes[slot] != 0:
                be_stops[slot] = entry_prices[slot]
                be_reasons[slot] = REASON_BREAK_EVEN

        floating = 0
        floating_exact = True
        for slot in range(position_count):
            if not active[slot]:
                continue
            value, exact = _money_minor_fixed(
                direction, orientation, contract_size, entry_prices[slot], money_exit,
                volumes[slot], fx_bid[index], fx_ask[index], fx_valid[index]
            )
            floating += value
            floating_exact = floating_exact and exact
        total = realized + floating
        if floating_exact and not money_unknown:
            if total > max_total:
                max_total = total
            if total < min_total:
                min_total = total
            drawdown = max_total - total
            if drawdown > max_drawdown:
                max_drawdown = drawdown

        if stop_mode == STOP_BASKET and total <= -stop_minor:
            if floating_exact:
                for slot in range(position_count):
                    if active[slot]:
                        value, exact = _money_minor_fixed(direction, orientation, contract_size, entry_prices[slot], money_exit, volumes[slot], fx_bid[index], fx_ask[index], fx_valid[index])
                        exit_slots[exit_count] = slot; exit_indices[exit_count] = index
                        exit_entry_prices[exit_count] = entry_prices[slot]; exit_prices[exit_count] = money_exit
                        exit_volumes[exit_count] = volumes[slot]; exit_pnls[exit_count] = value
                        exit_exact[exit_count] = exact; exit_reasons[exit_count] = REASON_BASKET_STOP
                        exit_count += 1; realized += value; active[slot] = False; active_count -= 1
                exit_reason = REASON_BASKET_STOP
                break
            blocker_mask |= BLOCK_STALE_BASKET_STOP
            money_unknown = True

        close_due = False
        while provider_cursor < len(provider_indices) and provider_indices[provider_cursor] <= index:
            action = provider_actions[provider_cursor]
            if be_mode == BE_PROVIDER and action == PROVIDER_MOVE_BE:
                for slot in range(position_count):
                    if active[slot]:
                        be_stops[slot] = entry_prices[slot]
                        be_reasons[slot] = REASON_BREAK_EVEN
            elif be_mode == BE_PROVIDER and action == PROVIDER_MOVE_PRICE and provider_prices[provider_cursor] > 0:
                for slot in range(position_count):
                    if active[slot]:
                        be_stops[slot] = provider_prices[provider_cursor]
                        be_reasons[slot] = REASON_PROVIDER_SL_MOVE
            if action == PROVIDER_CLOSE:
                close_due = True
            provider_cursor += 1
        if close_due and (management_mode == MANAGEMENT_EXACT or management_mode == MANAGEMENT_CLOSE_ONLY):
            for slot in range(position_count):
                if active[slot]:
                    value, exact = _money_minor_fixed(direction, orientation, contract_size, entry_prices[slot], money_exit, volumes[slot], fx_bid[index], fx_ask[index], fx_valid[index])
                    exit_slots[exit_count] = slot; exit_indices[exit_count] = index
                    exit_entry_prices[exit_count] = entry_prices[slot]; exit_prices[exit_count] = money_exit
                    exit_volumes[exit_count] = volumes[slot]; exit_pnls[exit_count] = value
                    exit_exact[exit_count] = exact; exit_reasons[exit_count] = REASON_PROVIDER_CLOSE
                    exit_count += 1
                    if exact: realized += value
                    else: blocker_mask |= BLOCK_STALE_EXIT; blocker_index = index; money_unknown = True
                    active[slot] = False; active_count -= 1
            exit_reason = REASON_PROVIDER_CLOSE
            break

        for slot in range(position_count):
            if not active[slot]:
                continue
            level = 0
            reason = REASON_PROVIDER_SL
            if stop_mode == STOP_PROVIDER:
                level = current_sl_all[slot] if be_mode == BE_PROVIDER else current_sl_nonbe[slot]
            elif stop_mode == STOP_FIXED_MOVE:
                level = entry_prices[slot] - direction * stop_points
                reason = REASON_FIXED_SL
            if be_stops[slot] != 0:
                if level == 0 or (direction == 1 and be_stops[slot] >= level) or (direction == -1 and be_stops[slot] <= level):
                    level = be_stops[slot]
                    reason = be_reasons[slot]
            hit = level != 0 and ((direction == 1 and raw_exit <= level) or (direction == -1 and raw_exit >= level))
            if hit:
                value, exact = _money_minor_fixed(direction, orientation, contract_size, entry_prices[slot], money_exit, volumes[slot], fx_bid[index], fx_ask[index], fx_valid[index])
                exit_slots[exit_count] = slot; exit_indices[exit_count] = index
                exit_entry_prices[exit_count] = entry_prices[slot]; exit_prices[exit_count] = money_exit
                exit_volumes[exit_count] = volumes[slot]; exit_pnls[exit_count] = value
                exit_exact[exit_count] = exact; exit_reasons[exit_count] = reason
                exit_count += 1
                if exact: realized += value
                else: blocker_mask |= BLOCK_STALE_EXIT; blocker_index = index; money_unknown = True
                active[slot] = False; active_count -= 1; exit_reason = reason
        if active_count == 0:
            break

        floating = 0
        floating_exact = True
        for slot in range(position_count):
            if active[slot]:
                value, exact = _money_minor_fixed(direction, orientation, contract_size, entry_prices[slot], money_exit, volumes[slot], fx_bid[index], fx_ask[index], fx_valid[index])
                floating += value; floating_exact = floating_exact and exact
        total = realized + floating

        if target_mode == TARGET_PROVIDER_LEG:
            for slot in range(position_count):
                level = current_tp[slot]
                hit = active[slot] and level != 0 and ((direction == 1 and raw_exit >= level) or (direction == -1 and raw_exit <= level))
                if hit:
                    value, exact = _money_minor_fixed(direction, orientation, contract_size, entry_prices[slot], money_exit, volumes[slot], fx_bid[index], fx_ask[index], fx_valid[index])
                    exit_slots[exit_count] = slot; exit_indices[exit_count] = index
                    exit_entry_prices[exit_count] = entry_prices[slot]; exit_prices[exit_count] = money_exit
                    exit_volumes[exit_count] = volumes[slot]; exit_pnls[exit_count] = value
                    exit_exact[exit_count] = exact; exit_reasons[exit_count] = REASON_PROVIDER_TP
                    exit_count += 1
                    if exact: realized += value
                    else: blocker_mask |= BLOCK_STALE_EXIT; blocker_index = index; money_unknown = True
                    active[slot] = False; active_count -= 1; exit_reason = REASON_PROVIDER_TP
        elif target_mode == TARGET_PROVIDER_ALL:
            available = np.empty(len(current_all_tp), dtype=np.int64)
            available_count = 0
            for slot in range(len(current_all_tp)):
                value = current_all_tp[slot]
                if value == 0:
                    continue
                duplicate = False
                for prior in range(available_count):
                    if available[prior] == value:
                        duplicate = True
                        break
                if not duplicate:
                    available[available_count] = value; available_count += 1
            if available_count > 0:
                ordered = np.sort(available[:available_count])
                selected = provider_target_index
                if selected >= available_count: selected = available_count - 1
                level = ordered[selected] if direction == 1 else ordered[available_count - 1 - selected]
                hit = (direction == 1 and raw_exit >= level) or (direction == -1 and raw_exit <= level)
                if hit:
                    for slot in range(position_count):
                        if active[slot]:
                            value, exact = _money_minor_fixed(direction, orientation, contract_size, entry_prices[slot], money_exit, volumes[slot], fx_bid[index], fx_ask[index], fx_valid[index])
                            exit_slots[exit_count] = slot; exit_indices[exit_count] = index
                            exit_entry_prices[exit_count] = entry_prices[slot]; exit_prices[exit_count] = money_exit
                            exit_volumes[exit_count] = volumes[slot]; exit_pnls[exit_count] = value
                            exit_exact[exit_count] = exact; exit_reasons[exit_count] = REASON_PROVIDER_TARGET_ALL
                            exit_count += 1
                            if exact: realized += value
                            else: blocker_mask |= BLOCK_STALE_EXIT; blocker_index = index; money_unknown = True
                            active[slot] = False; active_count -= 1
                    exit_reason = REASON_PROVIDER_TARGET_ALL
        elif target_mode == TARGET_FIXED_BASKET and total >= target_minor:
            if floating_exact:
                for slot in range(position_count):
                    if active[slot]:
                        value, exact = _money_minor_fixed(direction, orientation, contract_size, entry_prices[slot], money_exit, volumes[slot], fx_bid[index], fx_ask[index], fx_valid[index])
                        exit_slots[exit_count] = slot; exit_indices[exit_count] = index
                        exit_entry_prices[exit_count] = entry_prices[slot]; exit_prices[exit_count] = money_exit
                        exit_volumes[exit_count] = volumes[slot]; exit_pnls[exit_count] = value
                        exit_exact[exit_count] = exact; exit_reasons[exit_count] = REASON_BASKET_TARGET
                        exit_count += 1; realized += value; active[slot] = False; active_count -= 1
                exit_reason = REASON_BASKET_TARGET
            else:
                blocker_mask |= BLOCK_STALE_BASKET_TARGET; money_unknown = True
        elif target_mode == TARGET_FIXED_MOVE:
            active_volume = 0
            weighted_entry = 0
            for slot in range(position_count):
                if active[slot]:
                    active_volume += volumes[slot]
                    weighted_entry += entry_prices[slot] * volumes[slot]
            move_numerator = direction * (
                raw_exit * active_volume - weighted_entry
            )
            if active_volume > 0 and move_numerator >= target_points * active_volume:
                for slot in range(position_count):
                    if active[slot]:
                        value, exact = _money_minor_fixed(direction, orientation, contract_size, entry_prices[slot], money_exit, volumes[slot], fx_bid[index], fx_ask[index], fx_valid[index])
                        exit_slots[exit_count] = slot; exit_indices[exit_count] = index
                        exit_entry_prices[exit_count] = entry_prices[slot]; exit_prices[exit_count] = money_exit
                        exit_volumes[exit_count] = volumes[slot]; exit_pnls[exit_count] = value
                        exit_exact[exit_count] = exact; exit_reasons[exit_count] = REASON_FIXED_MOVE_TARGET
                        exit_count += 1
                        if exact: realized += value
                        else: blocker_mask |= BLOCK_STALE_EXIT; blocker_index = index; money_unknown = True
                        active[slot] = False; active_count -= 1
                exit_reason = REASON_FIXED_MOVE_TARGET
        elif target_mode == TARGET_PARTIAL_RUNNER:
            if not partial_taken and floating_exact and total >= target_minor:
                for slot in range(position_count):
                    if active[slot]:
                        close_volume = int(math.floor(volumes[slot] * partial_fraction + 0.5))
                        if close_volume <= 0: continue
                        value, exact = _money_minor_fixed(direction, orientation, contract_size, entry_prices[slot], money_exit, close_volume, fx_bid[index], fx_ask[index], fx_valid[index])
                        exit_slots[exit_count] = slot; exit_indices[exit_count] = index
                        exit_entry_prices[exit_count] = entry_prices[slot]; exit_prices[exit_count] = money_exit
                        exit_volumes[exit_count] = close_volume; exit_pnls[exit_count] = value
                        exit_exact[exit_count] = exact; exit_reasons[exit_count] = REASON_PARTIAL_TARGET
                        exit_count += 1; realized += value; volumes[slot] -= close_volume
                        if volumes[slot] <= 0: active[slot] = False; active_count -= 1
                partial_taken = True
            if active_count > 0:
                floating = 0; floating_exact = True
                for slot in range(position_count):
                    if active[slot]:
                        value, exact = _money_minor_fixed(direction, orientation, contract_size, entry_prices[slot], money_exit, volumes[slot], fx_bid[index], fx_ask[index], fx_valid[index])
                        floating += value; floating_exact = floating_exact and exact
                total = realized + floating
                if partial_taken and floating_exact and total >= runner_minor:
                    for slot in range(position_count):
                        if active[slot]:
                            value, exact = _money_minor_fixed(direction, orientation, contract_size, entry_prices[slot], money_exit, volumes[slot], fx_bid[index], fx_ask[index], fx_valid[index])
                            exit_slots[exit_count] = slot; exit_indices[exit_count] = index
                            exit_entry_prices[exit_count] = entry_prices[slot]; exit_prices[exit_count] = money_exit
                            exit_volumes[exit_count] = volumes[slot]; exit_pnls[exit_count] = value
                            exit_exact[exit_count] = exact; exit_reasons[exit_count] = REASON_RUNNER_TARGET
                            exit_count += 1; realized += value; active[slot] = False; active_count -= 1
                    exit_reason = REASON_RUNNER_TARGET
        if active_count == 0:
            break

        if lock_arm_minor > 0:
            if floating_exact and max_total != np.iinfo(np.int64).min:
                lock_armed = lock_armed or max_total >= lock_arm_minor
                if lock_armed and total <= max_total - lock_giveback_minor:
                    for slot in range(position_count):
                        if active[slot]:
                            value, exact = _money_minor_fixed(direction, orientation, contract_size, entry_prices[slot], money_exit, volumes[slot], fx_bid[index], fx_ask[index], fx_valid[index])
                            exit_slots[exit_count] = slot; exit_indices[exit_count] = index
                            exit_entry_prices[exit_count] = entry_prices[slot]; exit_prices[exit_count] = money_exit
                            exit_volumes[exit_count] = volumes[slot]; exit_pnls[exit_count] = value
                            exit_exact[exit_count] = exact; exit_reasons[exit_count] = REASON_PROFIT_LOCK
                            exit_count += 1; realized += value; active[slot] = False; active_count -= 1
                    exit_reason = REASON_PROFIT_LOCK
                    break
            elif not floating_exact and lock_armed:
                blocker_mask |= BLOCK_STALE_PROFIT_LOCK; money_unknown = True

        if active_count > 0 and first_open_ns != np.iinfo(np.int64).max and now - first_open_ns >= time_exit_ns:
            for slot in range(position_count):
                if active[slot]:
                    value, exact = _money_minor_fixed(direction, orientation, contract_size, entry_prices[slot], money_exit, volumes[slot], fx_bid[index], fx_ask[index], fx_valid[index])
                    exit_slots[exit_count] = slot; exit_indices[exit_count] = index
                    exit_entry_prices[exit_count] = entry_prices[slot]; exit_prices[exit_count] = money_exit
                    exit_volumes[exit_count] = volumes[slot]; exit_pnls[exit_count] = value
                    exit_exact[exit_count] = exact; exit_reasons[exit_count] = REASON_TIME_EXIT
                    exit_count += 1
                    if exact: realized += value
                    else: blocker_mask |= BLOCK_STALE_EXIT; blocker_index = index; money_unknown = True
                    active[slot] = False; active_count -= 1
            exit_reason = REASON_TIME_EXIT
            break

    if active_count > 0:
        if last_index >= 0:
            raw_exit = exit_quote[last_index]
            money_exit = raw_exit - direction * exit_cost_points
            for slot in range(position_count):
                if active[slot]:
                    value, exact = _money_minor_fixed(direction, orientation, contract_size, entry_prices[slot], money_exit, volumes[slot], fx_bid[last_index], fx_ask[last_index], fx_valid[last_index])
                    exit_slots[exit_count] = slot; exit_indices[exit_count] = last_index
                    exit_entry_prices[exit_count] = entry_prices[slot]; exit_prices[exit_count] = money_exit
                    exit_volumes[exit_count] = volumes[slot]; exit_pnls[exit_count] = value
                    exit_exact[exit_count] = exact; exit_reasons[exit_count] = REASON_DATA_END
                    exit_count += 1
                    if exact: realized += value
                    else: blocker_mask |= BLOCK_STALE_EXIT; blocker_index = last_index; money_unknown = True
                    active[slot] = False
            exit_reason = REASON_DATA_END
        blocker_mask |= BLOCK_PATH_ENDED

    return (
        realized, exit_reason, max_total, min_total, max_drawdown,
        max_favourable_points, max_adverse_points, blocker_mask, blocker_index,
        last_index, False, entries_seen, np.sum(initial_volumes[:entries_seen]), exit_count,
        exit_slots, exit_indices, exit_entry_prices, exit_prices, exit_volumes,
        exit_pnls, exit_exact, exit_reasons,
    )
