"""Compiled exact-money evaluator used only by the offline strategy search.

The scalar engine remains the readable reference implementation.  This module
prepares the same causal inputs as that engine and executes the per-tick state
machine with Numba.  Finalists must still pass the independent scalar oracle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import math
from threading import Lock
from typing import Iterable

import numpy as np
from numba import njit

from provider_action_semantics import is_full_close_action

from .contracts import StrategyGenome
from .dataset import DubaiPath, LevelEvent, ProviderEvent
from .engine import (
    EntryRecord,
    ExecutionAssumptions,
    ExitRecord,
    SimulationResult,
    _datetime_ns,
    _empty_result,
    _entry_request_in_flight,
    _is_provider_close,
    _looks_like_be,
    _path_contract_blockers,
    _prepare_entries,
)
from .protection_contract import ProtectionEvent, ProtectionProfile
from .market_contract import MarketEvent


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
TARGET_PER_LEG_STEPS = 6

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
MANAGEMENT_EXPLICIT_CLOSE_ONLY = 3

TIME_UNCONDITIONAL = 0
TIME_DISABLED = 1
TIME_LOSS_ONLY = 2
TIME_PROFIT_ONLY = 3
TIME_NON_NEGATIVE = 4

PROVIDER_OTHER = 0
PROVIDER_MOVE_BE = 1
PROVIDER_MOVE_PRICE = 2
PROVIDER_CLOSE = 3
PROVIDER_LEGACY_CLOSE = 4

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
REASON_PER_LEG_TARGET = 16
REASON_TRAILING_STOP = 17
REASON_HARD_STOP_PER_LEG = 18
REASON_INITIAL_SL = 19
REASON_INITIAL_TP = 20
REASON_ENTRY_REJECTED = 21

PROTECTION_EVENT_OPEN = 1
PROTECTION_EVENT_REQUESTED = 2
PROTECTION_EVENT_INSTALLED = 3
PROTECTION_EVENT_REJECTED = 4
PROTECTION_EVENT_ACKNOWLEDGED = 5
PROTECTION_EVENT_CLOSED = 6
PROTECTION_EVENT_TOUCHED = 7
PROTECTION_REJECT_PASSIVE_PENDING = 100

PROTECTION_BLOCK_NONE = 0
PROTECTION_BLOCK_EVENT_BUDGET = 1
PROTECTION_BLOCK_INITIAL_PRECISION = 2
PROTECTION_BLOCK_REQUEST_AMBIGUOUS = 3
PROTECTION_BLOCK_INITIAL_INVALID = 4
PROTECTION_BLOCK_MARKET_CLOSE = 5

PROTECTION_NONE = np.iinfo(np.int64).min

MARKET_ENTRY_REQUESTED = 1
MARKET_ENTRY_FILLED = 2
MARKET_ENTRY_REJECTED = 3
MARKET_ENTRY_ACKNOWLEDGED = 4
MARKET_CLOSE_REQUESTED = 5
MARKET_CLOSE_FILLED = 6
MARKET_CLOSE_REJECTED = 7
MARKET_CLOSE_ACKNOWLEDGED = 8
MARKET_INVALID_VOLUME = 100
MARKET_INVALID_PROTECTION = 101
MARKET_ALREADY_CLOSED = 102
MARKET_ACCEPTED = 103

_MARKET_KIND_NAMES = {
    MARKET_ENTRY_REQUESTED: "entry_requested",
    MARKET_ENTRY_FILLED: "entry_filled",
    MARKET_ENTRY_REJECTED: "entry_rejected",
    MARKET_ENTRY_ACKNOWLEDGED: "entry_acknowledged",
    MARKET_CLOSE_REQUESTED: "close_requested",
    MARKET_CLOSE_FILLED: "close_filled",
    MARKET_CLOSE_REJECTED: "close_rejected",
    MARKET_CLOSE_ACKNOWLEDGED: "close_acknowledged",
}
_MARKET_REASON_NAMES = {
    MARKET_INVALID_VOLUME: "invalid_volume",
    MARKET_INVALID_PROTECTION: "invalid_initial_protection",
    MARKET_ALREADY_CLOSED: "position_already_closed",
    MARKET_ACCEPTED: "accepted",
}

BLOCK_INVALID_TICK = 1
BLOCK_STALE_BASKET_STOP = 2
BLOCK_STALE_BASKET_TARGET = 4
BLOCK_STALE_PROFIT_LOCK = 8
BLOCK_STALE_EXIT = 16
BLOCK_PATH_ENDED = 32
BLOCK_STALE_HARD_STOP = 64
BLOCK_SWAP_ROLLOVER = 128
BLOCK_SWAP_VOLUME = 256
BLOCK_STALE_EQUITY = 512
BLOCK_STALE_TIME_EXIT = 1024


_TARGET_CODES = {
    "provider_per_leg": TARGET_PROVIDER_LEG,
    "per_leg_levels": TARGET_PER_LEG_STEPS,
    "provider_target_all": TARGET_PROVIDER_ALL,
    "fixed_basket": TARGET_FIXED_BASKET,
    "fixed_move": TARGET_FIXED_MOVE,
    "partial_runner": TARGET_PARTIAL_RUNNER,
    "per_leg_steps": TARGET_PER_LEG_STEPS,
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
    "fixed_level": STOP_FIXED_MOVE,
    "fixed_move": STOP_FIXED_MOVE,
    "basket_money": STOP_BASKET,
    "none": STOP_NONE,
}
_MANAGEMENT_CODES = {
    "exact": MANAGEMENT_EXACT,
    "close_only": MANAGEMENT_CLOSE_ONLY,
    "explicit_close_only": MANAGEMENT_EXPLICIT_CLOSE_ONLY,
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
    REASON_PER_LEG_TARGET: "per_leg_target",
    REASON_TRAILING_STOP: "trailing_stop",
    REASON_HARD_STOP_PER_LEG: "hard_stop_per_leg",
    REASON_INITIAL_SL: "initial_sl",
    REASON_INITIAL_TP: "initial_tp",
    REASON_ENTRY_REJECTED: "entry_rejected",
}
_PROTECTION_KIND_NAMES = {
    PROTECTION_EVENT_OPEN: "open",
    PROTECTION_EVENT_REQUESTED: "requested",
    PROTECTION_EVENT_INSTALLED: "installed",
    PROTECTION_EVENT_REJECTED: "rejected",
    PROTECTION_EVENT_ACKNOWLEDGED: "acknowledged",
    PROTECTION_EVENT_CLOSED: "closed",
    PROTECTION_EVENT_TOUCHED: "touched",
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
    rollover_indices: np.ndarray
    rollover_times_ns: np.ndarray
    rollover_costs: np.ndarray
    rollover_valid: np.ndarray
    rollover_blockers: tuple[str | None, ...]


class FastEvaluator:
    """Cache fixed-point paths and evaluate genomes through the JIT kernel."""

    path_bounded_cache = True

    def __init__(
        self,
        *,
        execution: ExecutionAssumptions | None = None,
    ) -> None:
        self._cache: dict[
            tuple[int, int],
            tuple[DubaiPath, _CompiledPath],
        ] = {}
        self._cache_lock = Lock()
        self.execution = execution or ExecutionAssumptions()

    def __call__(self, path: DubaiPath, genome: StrategyGenome) -> SimulationResult:
        if self.execution.client is not None:
            return _empty_result(path, genome, blockers=("client_model_not_validated_in_fast",))
        blockers = (
            tuple(genome.validation_errors())
            + tuple(_path_contract_blockers(path))
            + tuple(_protection_profile_blockers(path, genome, self.execution.protection))
            + tuple(_market_profile_blockers(genome, self.execution))
        )
        if blockers:
            return _empty_result(path, genome, blockers=blockers)
        if self.execution.protection is not None and self.execution.protection.digits != 2:
            return _empty_result(
                path,
                genome,
                blockers=("fast_path_unsupported:protection_digits",),
            )
        try:
            cache_key = (id(path), self.execution.latency_ms)
            cached = self._cache.get(cache_key)
            compiled = cached[1] if cached is not None and cached[0] is path else None
            if compiled is None:
                with self._cache_lock:
                    cached = self._cache.get(cache_key)
                    compiled = (
                        cached[1]
                        if cached is not None and cached[0] is path
                        else None
                    )
                    if compiled is None:
                        compiled = _compile_path(
                            path,
                            observation_latency_ns=(
                                self.execution.latency_ms * 1_000_000
                            ),
                            market_mode=getattr(self.execution, "market", None) is not None,
                        )
                        # Keep a strong reference to the path so CPython cannot
                        # recycle its id and bind another signal to these ticks.
                        self._cache[cache_key] = (path, compiled)
            return _simulate_compiled(path, compiled, genome, self.execution)
        except FastPathUnsupported as exc:
            return _empty_result(
                path,
                genome,
                blockers=(f"fast_path_unsupported:{exc}",),
            )

    def clear_cache(self) -> None:
        """Release compiled tick arrays between bounded validation worlds."""

        with self._cache_lock:
            self._cache.clear()


def _protection_profile_blockers(
    path: DubaiPath,
    genome: StrategyGenome,
    profile: ProtectionProfile | None,
) -> list[str]:
    if profile is None:
        return []
    errors: list[str] = []
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
    # (per-leg broker SL/TP stay as usual); requires a market profile (see below).
    elif (
        genome.stop_mode not in {"none", "fixed_move", "basket_money"}
        or genome.target_mode not in ({"none", "per_leg_steps", "partial_runner"} if extended else {"none", "per_leg_steps"})
        or genome.be_mode not in ({"none", "price"} if extended else {"none"})
    ):
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


def _market_profile_blockers(genome, execution):
    errors = []
    absolute = genome.stop_mode == "fixed_level" or genome.target_mode == "per_leg_levels"
    if absolute and (execution.protection is None or execution.protection.policy_extension not in {"absolute_levels_v1", "absolute_levels_be_v1"}
            or execution.market is None or execution.client is not None):
        errors.append("absolute_levels_require_explicit_market_profile")
    extended = execution.protection is not None and execution.protection.policy_extension == "own_rule_be_partial_v1"
    ordinal = execution.protection is not None and execution.protection.request_quote_binding == "timestamp_and_ordinal"
    basket = execution.protection is not None and execution.protection.policy_extension == "basket_guard_v1"
    if basket and (execution.market is None or execution.client is not None
                   or genome.schema_version != 2 or genome.entry_mode == "actual_mt5"):
        errors.append("basket_guard_requires_hypothetical_market")
    if (extended or ordinal and not basket) and (execution.market is None or execution.client is not None
                     or genome.schema_version != 2 or genome.entry_mode == "actual_mt5"
                     or genome.provider_management_mode != "ignore"):
        errors.append("protection_extension_requires_own_rule_market")
    if (genome.stop_mode == "basket_money" and not basket and execution.protection is not None
            and getattr(execution, "market", None) is None):
        errors.append("basket_money_cap_requires_market_profile")
    if getattr(execution, "market", None) is None:
        return errors
    if execution.protection is None:
        errors.append("market_requires_protection_profile")
    if genome.schema_version != 2 or genome.entry_mode == "actual_mt5":
        errors.append("market_requires_hypothetical_schema2_entries")
    if extended and genome.target_mode == "partial_runner" and not genome.validation_errors():
        if genome.entry_ladder_mode != "simultaneous":
            errors.append("market_partial_ladder_unsupported")
        low, high, step = (Decimal(str(getattr(execution.market, "volume_" + key))) for key in ("min", "max", "step"))
        for amount in genome.volume_weights:
            whole = Decimal(str(amount))
            part = whole * Decimal(str(genome.partial_fraction))
            if any(value < low or value > high or value % step for value in (part, whole - part)):
                errors.append("market_partial_volume_unsupported")
                break
    return errors


def _compile_path(
    path: DubaiPath,
    *,
    observation_latency_ns: int = 0,
    market_mode: bool = False,
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
    bid = _fixed_array(path.bid, PRICE_SCALE, "bid", allow_stale=market_mode)
    ask = _fixed_array(path.ask, PRICE_SCALE, "ask", allow_stale=market_mode)
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
    rollover_rows = sorted(
        path.rollover_events,
        key=lambda event: _datetime_ns(event.observed_at),
    )
    rollover_width = max(
        (len(event.minor_by_volume_unit) for event in rollover_rows),
        default=1,
    )
    rollover_costs = np.zeros(
        (len(rollover_rows), rollover_width),
        dtype=np.int64,
    )
    for row, event in enumerate(rollover_rows):
        values = np.asarray(event.minor_by_volume_unit)
        if values.ndim != 1 or values.dtype.kind not in {"i", "u"}:
            raise FastPathUnsupported("rollover_cost_lookup")
        rollover_costs[row, :len(values)] = values.astype(
            np.int64,
            copy=False,
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
        rollover_indices=np.asarray(
            [
                int(np.searchsorted(
                    path.times_ns,
                    _datetime_ns(event.observed_at),
                    side="left",
                ))
                for event in rollover_rows
            ],
            dtype=np.int64,
        ),
        rollover_times_ns=np.asarray(
            [_datetime_ns(event.observed_at) for event in rollover_rows],
            dtype=np.int64,
        ),
        rollover_costs=rollover_costs,
        rollover_valid=np.asarray(
            [event.blocker is None for event in rollover_rows],
            dtype=np.bool_,
        ),
        rollover_blockers=tuple(event.blocker for event in rollover_rows),
    )


def _simulate_compiled(
    path: DubaiPath,
    compiled: _CompiledPath,
    genome: StrategyGenome,
    execution: ExecutionAssumptions,
) -> SimulationResult:
    pending_request_times: list[int] = []
    pending_request_indices: list[int] = []
    scheduled, confidence, entry_blockers = _prepare_entries(
        path,
        genome,
        execution,
        pending_request_times=pending_request_times,
        pending_request_indices=pending_request_indices,
    )
    if entry_blockers:
        return _empty_result(
            path,
            genome,
            blockers=entry_blockers,
            confidence_layer=confidence,
        )
    if getattr(execution, "market", None) is not None and (scheduled or pending_request_times):
        return _simulate_market_compiled(
            path, compiled, genome, execution, scheduled, confidence,
            pending_request_times, pending_request_indices,
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
    leg_target_points = np.asarray(
        [
            _price_points(genome.target_steps[item.position.leg_index])
            if genome.target_mode == "per_leg_steps"
            else 0
            for item in scheduled
        ],
        dtype=np.int64,
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
    if execution.protection is not None:
        profile = execution.protection
        initial_sl, initial_tp, open_block_codes, open_sources = (
            _prepare_opening_protection(
                path,
                scheduled,
                genome,
                execution,
                profile,
            )
        )
        processing_delay_ns = _protection_delay_ns(
            profile.processing_delay_ms,
            "processing",
            compiled.times_ns,
        )
        acknowledgement_delay_ns = _protection_delay_ns(
            profile.acknowledgement_delay_ms,
            "acknowledgement",
            compiled.times_ns,
        )
        retry_delay_ns = _protection_delay_ns(
            profile.retry_delay_ms,
            "retry",
            compiled.times_ns,
        )
        passive_enabled, passive_delay_ns, passive_quote_price = (
            _passive_fill_config(profile, compiled.times_ns))
        event_capacity = min(
            profile.max_events,
            count * (3 * len(compiled.times_ns) + 1),
        )
        protected_output = _protection_kernel(
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
            compiled.provider_indices,
            compiled.provider_actions,
            compiled.rollover_indices,
            compiled.rollover_times_ns,
            compiled.rollover_costs,
            compiled.rollover_valid,
            initial_sl,
            initial_tp,
            open_block_codes,
            leg_target_points,
            _TARGET_CODES[genome.target_mode],
            _STOP_CODES[genome.stop_mode],
            _price_points(genome.stop_value)
            if genome.stop_mode == "fixed_move"
            else 0,
            _minor(genome.hard_stop_eur_per_leg),
            _price_points(genome.trailing_distance),
            _minor(genome.profit_lock_arm),
            _minor(genome.profit_lock_giveback),
            int(genome.time_exit_min) * 60 * 1_000_000_000,
            _time_exit_code(genome),
            _MANAGEMENT_CODES[genome.provider_management_mode],
            genome.pending_entry_policy == "until_expiry",
            int(np.searchsorted(path.times_ns, pending_request_times[0]))
            if pending_request_times
            else -1,
            profile.stops_level_points,
            processing_delay_ns,
            acknowledgement_delay_ns,
            retry_delay_ns,
            passive_enabled,
            passive_delay_ns,
            passive_quote_price,
            event_capacity,
            genome.entry_mode != "actual_mt5",
        )
        output = protected_output[:23]
        protection_events = _protection_events_from_kernel(
            scheduled,
            open_sources,
            protected_output,
        )
        protection_blocker = _protection_blocker_from_kernel(
            scheduled,
            protected_output,
        )
        return _result_from_kernel(
            path,
            genome,
            scheduled,
            confidence,
            compiled,
            output,
            pending_request_times,
            protection_events=protection_events,
            protection_blocker=protection_blocker,
            protection_money_unknown=bool(protected_output[34]),
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
        compiled.rollover_indices,
        compiled.rollover_times_ns,
        compiled.rollover_costs,
        compiled.rollover_valid,
        _TARGET_CODES[genome.target_mode],
        leg_target_points,
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
        _minor(genome.hard_stop_eur_per_leg),
        _price_points(genome.trailing_distance),
        _minor(genome.profit_lock_arm),
        _minor(genome.profit_lock_giveback),
        int(genome.time_exit_min) * 60 * 1_000_000_000,
        _time_exit_code(genome),
        _MANAGEMENT_CODES[genome.provider_management_mode],
        max(1, int(round(float(genome.target_value or 1.0)))) - 1,
        genome.pending_entry_policy == "until_expiry",
        int(np.searchsorted(path.times_ns, pending_request_times[0])) if pending_request_times else -1,
    )
    return _result_from_kernel(
        path,
        genome,
        scheduled,
        confidence,
        compiled,
        output,
        pending_request_times,
    )


def _result_from_kernel(
    path,
    genome,
    scheduled,
    confidence,
    compiled,
    output,
    pending_request_times,
    *,
    protection_events=(),
    protection_blocker=None,
    protection_money_unknown=False,
) -> SimulationResult:
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
        rollover_blocker_event,
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
    blockers = list(_blockers(int(blocker_mask), int(blocker_index)))
    if protection_blocker is not None:
        blockers.append(protection_blocker)
    if _entry_request_in_flight(scheduled, int(entries_seen), path, int(last_tick_index)):
        blockers.append("entry_request_in_flight_at_strategy_exit")
    if pending_request_times and last_tick_index >= 0 and int(path.times_ns[last_tick_index]) >= pending_request_times[0]:
        blockers.append("entry_fill_quote_missing")
    if int(rollover_blocker_event) >= 0:
        blocker = compiled.rollover_blockers[
            int(rollover_blocker_event)
        ]
        blockers.append(blocker or "swap_volume_unsupported")
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
            requested_ns=item.requested_ns,
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
    money_unknown = protection_money_unknown or bool(blocker_mask & (
        BLOCK_STALE_BASKET_STOP
        | BLOCK_STALE_BASKET_TARGET
        | BLOCK_STALE_PROFIT_LOCK
        | BLOCK_STALE_EXIT
        | BLOCK_STALE_HARD_STOP
        | BLOCK_SWAP_ROLLOVER
        | BLOCK_SWAP_VOLUME
        | BLOCK_STALE_TIME_EXIT
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
            None if blocker_mask & BLOCK_STALE_EQUITY or max_total_minor == np.iinfo(np.int64).min
            else _decimal_minor(int(max_total_minor), path.currency_digits)
        ),
        max_adverse_eur=(
            None if blocker_mask & BLOCK_STALE_EQUITY or min_total_minor == np.iinfo(np.int64).max
            else _decimal_minor(int(min_total_minor), path.currency_digits)
        ),
        max_floating_drawdown_eur=(
            None if blocker_mask & BLOCK_STALE_EQUITY or max_total_minor == np.iinfo(np.int64).min
            else _decimal_minor(int(drawdown_minor), path.currency_digits)
        ),
        max_favourable_move=round(float(max_favourable_points) / PRICE_SCALE, 10),
        max_adverse_move=round(float(max_adverse_points) / PRICE_SCALE, 10),
        blockers=tuple(dict.fromkeys(blockers)),
        last_tick_index=int(last_tick_index),
        unfilled=bool(unfilled),
        filled_volume=round(float(filled_units) / VOLUME_SCALE, 10),
        protection_events=tuple(protection_events),
    )


@dataclass(frozen=True)
class _PendingMarketPosition:
    ticket: str
    entry_price: float
    volume: float
    opened_ns: int
    leg_index: int


@dataclass(frozen=True)
class _PendingMarketEntry:
    position: _PendingMarketPosition
    tick_index: int
    request_index: int
    source: str
    requested_ns: int


def _simulate_market_compiled(
    path, compiled, genome, execution, scheduled, confidence, pending_request_times,
    pending_request_indices,
):
    profile = execution.market
    protection = execution.protection
    scheduled = list(scheduled)
    if pending_request_times:
        first_missing = len(scheduled)
        missing_legs = (
            range(genome.leg_count)
            if not scheduled and genome.entry_ladder_mode == "simultaneous"
            else (first_missing,)
        )
        for leg_index in missing_legs:
            first = first_missing == 0
            scheduled.append(_PendingMarketEntry(
                position=_PendingMarketPosition(
                    ticket=f"sim_{leg_index + 1}" if first else f"sim_ladder_{leg_index + 1}",
                    entry_price=0.0, volume=float(genome.volume_weights[leg_index]),
                    opened_ns=int(path.times_ns[-1]), leg_index=leg_index,
                ),
                tick_index=len(path.times_ns),
                request_index=pending_request_indices[0],
                source=f"causal_{genome.entry_mode}" if first else f"counterfactual_{genome.entry_ladder_mode}_ladder",
                requested_ns=pending_request_times[0],
            ))
    count = len(scheduled)
    requests = np.asarray([
        item.request_index if getattr(item, "request_index", None) is not None
        else np.searchsorted(path.times_ns, item.requested_ns, side="left")
        for item in scheduled
    ], dtype=np.int64)
    fills = np.asarray([item.tick_index for item in scheduled], dtype=np.int64)
    request_times = path.times_ns[requests]
    request_ambiguous = (
        np.searchsorted(path.times_ns, request_times, side="right")
        - np.searchsorted(path.times_ns, request_times, side="left")
    ) != 1
    if protection.request_quote_binding == "timestamp_and_ordinal":
        request_ambiguous = np.asarray([
            getattr(item, "request_index", None) is None or int(path.times_ns[requests[slot]]) != item.requested_ns
            for slot, item in enumerate(scheduled)
        ], dtype=np.bool_)
    prices = _fixed_array(
        [item.position.entry_price for item in scheduled], PRICE_SCALE, "entry_price",
    )
    volumes = _fixed_array(
        [item.position.volume for item in scheduled], VOLUME_SCALE, "volume",
    )
    opened = np.asarray([item.position.opened_ns for item in scheduled], dtype=np.int64)
    initial_sl = np.full(count, PROTECTION_NONE, dtype=np.int64)
    initial_tp = np.full(count, PROTECTION_NONE, dtype=np.int64)
    stop_points = (
        _fixed_scalar(genome.stop_value, PRICE_SCALE, "market_stop")
        if genome.stop_mode in {"fixed_move", "fixed_level"} else 0
    )
    trailing_points = (
        _fixed_scalar(genome.trailing_distance, PRICE_SCALE, "market_trailing")
        if genome.trailing_distance is not None else 0
    )
    target_points = np.asarray([
        _fixed_scalar(genome.target_steps[item.position.leg_index], PRICE_SCALE, "market_target")
        if genome.target_mode in {"per_leg_steps", "per_leg_levels"} else 0
        for item in scheduled
    ], dtype=np.int64)
    for slot, request_index in enumerate(requests):
        if genome.stop_mode == "fixed_level":
            initial_sl[slot] = stop_points
            initial_tp[slot] = target_points[slot]
            continue
        quote = Decimal(str(
            path.ask[request_index] if compiled.direction == 1 else path.bid[request_index]
        ))
        quote += Decimal(compiled.direction) * Decimal(str(execution.spread_addition)) / 2
        distances = [value for value in (stop_points, trailing_points) if value > 0]
        if distances:
            level = quote - Decimal(compiled.direction * min(distances)) / PRICE_SCALE
            initial_sl[slot] = _fixed_scalar(
                _rounded_profile_price(level, protection), PRICE_SCALE, "market_initial_sl",
            )
    protection_config = np.asarray([
        protection.stops_level_points,
        _protection_delay_ns(protection.processing_delay_ms, "processing", compiled.times_ns),
        _protection_delay_ns(protection.acknowledgement_delay_ms, "acknowledgement", compiled.times_ns),
        _protection_delay_ns(protection.retry_delay_ms, "retry", compiled.times_ns),
        min(protection.max_events, count * (3 * len(path.times_ns) + 1)),
        *_passive_fill_config(protection, compiled.times_ns),
    ], dtype=np.int64)
    market_config = np.asarray([
        _protection_delay_ns(profile.entry_acknowledgement_delay_ms, "entry_ack", compiled.times_ns),
        _protection_delay_ns(profile.close_processing_delay_ms, "close_processing", compiled.times_ns),
        _protection_delay_ns(profile.close_acknowledgement_delay_ms, "close_ack", compiled.times_ns),
        _fixed_scalar(profile.volume_min, VOLUME_SCALE, "market_volume_min"),
        _fixed_scalar(profile.volume_max, VOLUME_SCALE, "market_volume_max"),
        _fixed_scalar(profile.volume_step, VOLUME_SCALE, "market_volume_step"),
        min(profile.max_events, count * (9 if genome.target_mode == "partial_runner" else 6)),
        min((
            _datetime_ns(event.observed_at) + execution.latency_ms * 1_000_000
            for event in path.provider_events
            if _is_provider_close(event.action, genome.provider_management_mode)
        ), default=np.iinfo(np.int64).max),
        execution.entry_fill_latency_ms * 1_000_000,
    ], dtype=np.int64)
    if market_config[5] <= 0:
        raise FastPathUnsupported("market_volume_step_precision")
    policy = np.asarray([
        stop_points, trailing_points, _minor(genome.hard_stop_eur_per_leg),
        _minor(genome.profit_lock_arm), _minor(genome.profit_lock_giveback),
        int(genome.time_exit_min) * 60_000_000_000, _time_exit_code(genome),
        _MANAGEMENT_CODES[genome.provider_management_mode],
        genome.pending_entry_policy == "until_expiry",
        genome.hard_stop_eur_per_leg is not None,
        genome.profit_lock_arm is not None,
        1 if genome.be_mode == "price" else 2 if genome.be_mode == "provider" else 0,
        _fixed_scalar(genome.be_trigger, PRICE_SCALE, "market_be") if genome.be_mode == "price" else 0,
        genome.target_mode == "partial_runner",
        _minor(genome.target_value) if genome.target_mode == "partial_runner" else 0,
        _minor(genome.runner_target) if genome.target_mode == "partial_runner" else 0,
        genome.stop_mode == "fixed_level",
        min((_datetime_ns(event.observed_at) + execution.latency_ms * 1_000_000
            for event in path.provider_events if event.action == "MOVE_SL_TO_BE"
            and event.payload.get("modality") == "direct" and event.observed_at >= path.signal_observed_at),
            default=np.iinfo(np.int64).max) if genome.be_mode == "provider" else np.iinfo(np.int64).max,
        _minor(genome.stop_value) if genome.stop_mode == "basket_money" else 0,
    ], dtype=np.int64)
    partial_volumes = np.asarray([
        _fixed_scalar(float(Decimal(str(item.position.volume)) * Decimal(str(genome.partial_fraction))), VOLUME_SCALE, "market_partial")
        if genome.target_mode == "partial_runner" else 0 for item in scheduled
    ], dtype=np.int64)
    output = _market_kernel(
        compiled.direction, compiled.orientation, compiled.contract_size,
        compiled.times_ns, compiled.bid, compiled.ask, compiled.exit_quote,
        _price_points(execution.exit_slippage + execution.spread_addition),
        compiled.fx_bid, compiled.fx_ask, compiled.fx_valid,
        requests, fills, prices, volumes, opened, initial_sl, initial_tp, request_ambiguous,
        target_points, compiled.provider_indices, compiled.provider_actions,
        compiled.rollover_indices, compiled.rollover_times_ns,
        compiled.rollover_costs, compiled.rollover_valid,
        protection_config, market_config, policy, partial_volumes,
    )
    (
        stats, accepted, acknowledged, exits, protection_trace, market_trace,
        counts, error, error_slot, rollover_error,
    ) = output
    blockers = list(_blockers(int(stats[7]), int(stats[8])))
    market_errors = {
        1: "market_event_budget_exhausted",
        6: "market_close_with_entry_in_flight_unsupported",
        7: "protection_event_budget_exhausted",
    }
    if error in market_errors:
        blockers.append(market_errors[int(error)])
    if error == 8:
        blockers.append(f"protection_request_quote_ambiguous:{scheduled[int(error_slot)].position.ticket}")
    if rollover_error >= 0:
        blockers.append(compiled.rollover_blockers[int(rollover_error)] or "swap_volume_unsupported")
    if stats[11]:
        blockers.append("entry_request_in_flight_at_strategy_exit")
    if stats[12]:
        blockers.append("entry_fill_quote_missing")
    if error in (2, 3, 4, 5):
        blockers.append("market_lifecycle_incomplete_at_data_end")
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    entries = tuple(EntryRecord(
        ticket=item.position.ticket, tick_index=int(fills[slot]),
        opened_at=epoch + timedelta(microseconds=int(opened[slot]) // 1000),
        entry_price=float(prices[slot]) / PRICE_SCALE,
        volume=float(volumes[slot]) / VOLUME_SCALE, source=item.source,
        requested_ns=item.requested_ns,
        acknowledged_ns=None if acknowledged[slot] == PROTECTION_NONE else int(acknowledged[slot]),
    ) for slot, item in enumerate(scheduled) if accepted[slot])
    exit_records = tuple(ExitRecord(
        ticket=scheduled[int(row[0])].position.ticket, tick_index=int(row[1]),
        closed_at=epoch + timedelta(microseconds=int(path.times_ns[int(row[1])]) // 1000),
        entry_price=float(row[2]) / PRICE_SCALE, exit_price=float(row[3]) / PRICE_SCALE,
        volume=float(row[4]) / VOLUME_SCALE,
        pnl_eur=_decimal_minor(int(row[5]), path.currency_digits) if row[6] else None,
        reason=_REASON_NAMES[int(row[7])],
    ) for row in exits[:int(counts[2])])
    protection_events = []
    for row in protection_trace[:int(counts[0])]:
        slot, index, now, request_id, kind, sl, tp, reason = map(int, row)
        reasons = {
            PROTECTION_EVENT_OPEN: "hypothetical_open_request",
            PROTECTION_EVENT_REQUESTED: "policy_intent",
            PROTECTION_EVENT_INSTALLED: "accepted",
            PROTECTION_EVENT_ACKNOWLEDGED: "response_available",
        }
        event_reason = ("passive_fill_pending" if kind == PROTECTION_EVENT_REJECTED
                        and reason == PROTECTION_REJECT_PASSIVE_PENDING else
                        "invalid_stops" if kind == PROTECTION_EVENT_REJECTED else
                        reasons.get(kind, _REASON_NAMES.get(reason, "")))
        protection_events.append(ProtectionEvent(
            scheduled[slot].position.ticket, index, now, request_id,
            _PROTECTION_KIND_NAMES[kind], _protection_level_from_fixed(sl),
            _protection_level_from_fixed(tp), event_reason,
        ))
    market_events = []
    for row in market_trace[:int(counts[1])]:
        slot, index, now, request_id, kind, price, volume, reason = map(int, row)
        source = scheduled[slot].source
        event_price = _protection_level_from_fixed(price)
        if kind == MARKET_ENTRY_REQUESTED:
            event_price = float(path.ask[index] if compiled.direction == 1 else path.bid[index])
        elif kind == MARKET_CLOSE_REQUESTED:
            event_price = float(path.exit_quotes[index])
        market_events.append(MarketEvent(
            scheduled[slot].position.ticket, index, now, request_id,
            _MARKET_KIND_NAMES[kind], event_price,
            float(volume) / VOLUME_SCALE,
            source if reason == 0 else _MARKET_REASON_NAMES.get(reason, _REASON_NAMES.get(reason, "")),
        ))
    money_unknown = bool(stats[10]) or bool(error) or "entry_fill_quote_missing" in blockers
    equity_incomplete = bool(stats[7] & BLOCK_STALE_EQUITY)
    return SimulationResult(
        signal_id=path.signal_id, strategy_fingerprint=genome.fingerprint,
        confidence_layer=confidence, entries=entries, exits=exit_records,
        pnl_eur=None if money_unknown else _decimal_minor(int(stats[0]), path.currency_digits),
        exit_reason="blocked" if not entries and blockers else _REASON_NAMES[int(stats[1])],
        max_favourable_eur=None if equity_incomplete or stats[2] == PROTECTION_NONE else _decimal_minor(int(stats[2]), path.currency_digits),
        max_adverse_eur=None if equity_incomplete or stats[3] == np.iinfo(np.int64).max else _decimal_minor(int(stats[3]), path.currency_digits),
        max_floating_drawdown_eur=None if equity_incomplete or stats[2] == PROTECTION_NONE else _decimal_minor(int(stats[4]), path.currency_digits),
        max_favourable_move=float(stats[5]) / PRICE_SCALE,
        max_adverse_move=float(stats[6]) / PRICE_SCALE,
        blockers=tuple(dict.fromkeys(blockers)), last_tick_index=int(stats[9]),
        unfilled=not entries and not blockers,
        filled_volume=round(sum(row.volume for row in entries), 10),
        protection_events=tuple(protection_events), market_events=tuple(market_events),
    )


def _prepare_opening_protection(
    path: DubaiPath,
    scheduled,
    genome: StrategyGenome,
    execution: ExecutionAssumptions,
    profile: ProtectionProfile,
):
    count = len(scheduled)
    initial_sl = np.full(count, PROTECTION_NONE, dtype=np.int64)
    initial_tp = np.full(count, PROTECTION_NONE, dtype=np.int64)
    open_block_codes = np.zeros(count, dtype=np.int8)
    open_sources: list[str] = []
    initial_by_ticket = {row.ticket: row for row in profile.initial_protections}
    direction = 1 if path.direction == "BUY" else -1

    for slot, item in enumerate(scheduled):
        ticket = item.position.ticket
        if genome.entry_mode == "actual_mt5":
            initial = initial_by_ticket[ticket]
            open_sources.append(initial.source)
            for value, output in ((initial.sl, initial_sl), (initial.tp, initial_tp)):
                if value is None:
                    continue
                rounded = _rounded_profile_price(value, profile)
                if float(value) != rounded:
                    open_block_codes[slot] = PROTECTION_BLOCK_INITIAL_PRECISION
                    continue
                output[slot] = _fixed_scalar(rounded, PRICE_SCALE, "initial_protection")
            continue

        open_sources.append("hypothetical_open_request")
        requested_ns = item.requested_ns
        if requested_ns is None:
            open_block_codes[slot] = PROTECTION_BLOCK_REQUEST_AMBIGUOUS
            continue
        request_index = int(np.searchsorted(path.times_ns, requested_ns, side="left"))
        request_end = int(np.searchsorted(path.times_ns, requested_ns, side="right"))
        if request_end - request_index != 1:
            open_block_codes[slot] = PROTECTION_BLOCK_REQUEST_AMBIGUOUS
            continue
        request_quote = Decimal(str(
            path.ask[request_index] if direction == 1 else path.bid[request_index]
        ))
        request_price = request_quote + (
            Decimal(direction) * Decimal(str(execution.spread_addition)) / Decimal(2)
        )
        candidates: list[Decimal] = []
        if genome.trailing_distance is not None:
            candidates.append(
                request_price
                - Decimal(direction) * Decimal(str(genome.trailing_distance))
            )
        if genome.stop_mode == "fixed_move":
            candidates.append(
                request_price - Decimal(direction) * Decimal(str(genome.stop_value))
            )
        if candidates:
            selected = max(candidates) if direction == 1 else min(candidates)
            rounded = _rounded_profile_price(selected, profile)
            initial_sl[slot] = _fixed_scalar(
                rounded,
                PRICE_SCALE,
                "hypothetical_initial_protection",
            )
    return (
        np.ascontiguousarray(initial_sl),
        np.ascontiguousarray(initial_tp),
        np.ascontiguousarray(open_block_codes),
        tuple(open_sources),
    )


def _rounded_profile_price(value, profile: ProtectionProfile) -> float:
    return float(
        Decimal(str(value)).quantize(
            Decimal(str(profile.point)),
            rounding=ROUND_HALF_UP,
        )
    )


def _protection_delay_ns(value_ms: int, name: str, times: np.ndarray) -> int:
    value = value_ms * 1_000_000
    maximum = int(times[-1])
    if (
        value > np.iinfo(np.int64).max
        or maximum > np.iinfo(np.int64).max - value
    ):
        raise FastPathUnsupported(f"protection_{name}_delay_range")
    return value


def _passive_fill_config(profile: ProtectionProfile, times: np.ndarray) -> tuple[int, int, int]:
    scenario = profile.passive_fill_scenario
    if scenario is None:
        return 0, 0, 0
    if scenario.delay_ns > np.iinfo(np.int64).max - int(times[-1]):
        raise FastPathUnsupported("passive_fill_delay_range")
    return 1, scenario.delay_ns, int(scenario.price_mode == "executable_quote")


def _protection_events_from_kernel(scheduled, open_sources, output):
    event_count = int(output[23])
    event_slots = output[24]
    event_indices = output[25]
    event_times = output[26]
    event_request_ids = output[27]
    event_kinds = output[28]
    event_sl = output[29]
    event_tp = output[30]
    event_reasons = output[31]
    events: list[ProtectionEvent] = []
    for offset in range(event_count):
        slot = int(event_slots[offset])
        kind_code = int(event_kinds[offset])
        if kind_code == PROTECTION_EVENT_OPEN:
            reason = open_sources[slot]
        elif kind_code == PROTECTION_EVENT_REQUESTED:
            reason = "policy_intent"
        elif kind_code == PROTECTION_EVENT_INSTALLED:
            reason = "accepted"
        elif kind_code == PROTECTION_EVENT_REJECTED:
            reason = ("passive_fill_pending"
                      if int(event_reasons[offset]) == PROTECTION_REJECT_PASSIVE_PENDING
                      else "invalid_stops")
        elif kind_code == PROTECTION_EVENT_ACKNOWLEDGED:
            reason = "response_available"
        else:
            reason = _REASON_NAMES[int(event_reasons[offset])]
        events.append(ProtectionEvent(
            ticket=scheduled[slot].position.ticket,
            tick_index=int(event_indices[offset]),
            timestamp_ns=int(event_times[offset]),
            request_id=int(event_request_ids[offset]),
            kind=_PROTECTION_KIND_NAMES[kind_code],
            sl=_protection_level_from_fixed(int(event_sl[offset])),
            tp=_protection_level_from_fixed(int(event_tp[offset])),
            reason=reason,
        ))
    return tuple(events)


def _protection_level_from_fixed(value: int) -> float | None:
    if value == PROTECTION_NONE:
        return None
    return float(value) / PRICE_SCALE


def _protection_blocker_from_kernel(scheduled, output) -> str | None:
    code = int(output[32])
    slot = int(output[33])
    if code == PROTECTION_BLOCK_NONE:
        return None
    if code == PROTECTION_BLOCK_EVENT_BUDGET:
        return "protection_event_budget_exhausted"
    if code == PROTECTION_BLOCK_MARKET_CLOSE:
        return "protection_market_close_latency_unmodeled"
    ticket = scheduled[slot].position.ticket
    if code == PROTECTION_BLOCK_INITIAL_PRECISION:
        return f"initial_protection_precision:{ticket}"
    if code == PROTECTION_BLOCK_REQUEST_AMBIGUOUS:
        return f"protection_request_quote_ambiguous:{ticket}"
    if code == PROTECTION_BLOCK_INITIAL_INVALID:
        return f"initial_protection_rejected_unmodeled:{ticket}"
    raise AssertionError(f"unknown protection blocker code: {code}")


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
        elif is_full_close_action(action):
            code = PROVIDER_CLOSE
        elif _is_provider_close(action, "exact"):
            code = PROVIDER_LEGACY_CLOSE
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


def _time_exit_code(genome: StrategyGenome) -> int:
    if genome.schema_version == 1:
        return TIME_UNCONDITIONAL
    return {
        "none": TIME_DISABLED,
        "always": TIME_UNCONDITIONAL,
        "loss_only": TIME_LOSS_ONLY,
        "profit_only": TIME_PROFIT_ONLY,
        "non_negative": TIME_NON_NEGATIVE,
    }[genome.time_exit_mode]


def _decimal_minor(value: int, digits: int) -> Decimal:
    quantum = Decimal(1).scaleb(-digits)
    return Decimal(value).scaleb(-digits).quantize(quantum)


def _blockers(mask: int, index: int) -> tuple[str, ...]:
    rows: list[str] = []
    if mask & BLOCK_STALE_EQUITY:
        rows.append("incomplete_equity_conversion")
    if mask & BLOCK_INVALID_TICK:
        rows.append(f"invalid_tick_at_index:{index}")
    if mask & BLOCK_STALE_BASKET_STOP:
        rows.append("stale_conversion_at_basket_stop")
    if mask & BLOCK_STALE_BASKET_TARGET:
        rows.append("stale_conversion_at_basket_target")
    if mask & BLOCK_STALE_PROFIT_LOCK:
        rows.append("stale_conversion_during_profit_lock")
    if mask & BLOCK_STALE_TIME_EXIT:
        rows.append("stale_conversion_at_time_exit")
    if mask & BLOCK_STALE_EXIT:
        rows.append(f"stale_conversion_at_exit:{index}")
    if mask & BLOCK_PATH_ENDED:
        rows.append("path_ended_before_strategy_exit")
    if mask & BLOCK_STALE_HARD_STOP:
        rows.append(f"stale_conversion_at_hard_stop_index:{index}")
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


@njit(cache=True)
def _allocate_swap_fixed(accrued, close_volume, current_volume):
    if accrued == 0 or close_volume <= 0 or current_volume <= 0:
        return 0
    if close_volume >= current_volume:
        return accrued
    return _round_ratio(accrued * close_volume, current_volume)


@njit(cache=True, inline="always")
def _protection_pair_valid(direction, quote, sl, tp, minimum_distance):
    if sl != PROTECTION_NONE:
        distance = direction * (quote - sl)
        if sl <= 0 or distance <= 0 or distance < minimum_distance:
            return False
    if tp != PROTECTION_NONE:
        distance = direction * (tp - quote)
        if tp <= 0 or distance <= 0 or distance < minimum_distance:
            return False
    return True


@njit(cache=True, inline="always")
def _emit_protection_event(
    event_count,
    event_slots,
    event_indices,
    event_times,
    event_request_ids,
    event_kinds,
    event_sl,
    event_tp,
    event_reasons,
    slot,
    index,
    now,
    request_id,
    kind,
    sl,
    tp,
    reason,
):
    if event_count >= len(event_slots):
        return event_count, False
    event_slots[event_count] = slot
    event_indices[event_count] = index
    event_times[event_count] = now
    event_request_ids[event_count] = request_id
    event_kinds[event_count] = kind
    event_sl[event_count] = sl
    event_tp[event_count] = tp
    event_reasons[event_count] = reason
    return event_count + 1, True


@njit(cache=True, inline="always")
def _market_trace_emit(rows, counts, stream, slot, index, now, request_id, kind, first, second, reason):
    offset = counts[stream]
    if offset >= len(rows):
        return False
    rows[offset, 0] = slot
    rows[offset, 1] = index
    rows[offset, 2] = now
    rows[offset, 3] = request_id
    rows[offset, 4] = kind
    rows[offset, 5] = first
    rows[offset, 6] = second
    rows[offset, 7] = reason
    counts[stream] += 1
    return True


@njit(cache=True, inline="always")
def _market_realize(
    exits, counts, active, accrued, remaining, slot, index, entry, price, volume, reason,
    direction, orientation, contract_size, fx_bid, fx_ask, fx_valid,
):
    value, exact = _money_minor_fixed(
        direction, orientation, contract_size, entry, price, volume,
        fx_bid, fx_ask, fx_valid,
    )
    swap = _allocate_swap_fixed(accrued[slot], volume, remaining[slot])
    value += swap
    accrued[slot] -= swap
    remaining[slot] -= volume
    active[slot] = remaining[slot] > 0
    offset = counts[2]
    exits[offset, 0] = slot
    exits[offset, 1] = index
    exits[offset, 2] = entry
    exits[offset, 3] = price
    exits[offset, 4] = volume
    exits[offset, 5] = value
    exits[offset, 6] = int(exact)
    exits[offset, 7] = reason
    counts[2] += 1
    return value, exact


@njit(cache=True)
def _market_acknowledge(
    trace, counts, index, now, sequence, entry_state, entry_ids, entry_ack_due,
    entry_outcome, prices, accepted, acknowledged, close_state, close_ids,
    close_ack_due, close_outcome, close_prices, volumes, close_volumes, close_reasons,
):
    for request_id in range(1, sequence + 1):
        for slot in range(len(entry_state)):
            if entry_ids[slot] == request_id and entry_state[slot] == 2 and now >= entry_ack_due[slot]:
                price = prices[slot] if accepted[slot] else PROTECTION_NONE
                if not _market_trace_emit(
                    trace, counts, 1, slot, index, now, request_id,
                    MARKET_ENTRY_ACKNOWLEDGED, price, volumes[slot], entry_outcome[slot],
                ):
                    return False
                entry_state[slot] = 3
                if accepted[slot]:
                    acknowledged[slot] = now
            if close_ids[slot] == request_id and close_state[slot] == 2 and now >= close_ack_due[slot]:
                if not _market_trace_emit(
                    trace, counts, 1, slot, index, now, request_id,
                    MARKET_CLOSE_ACKNOWLEDGED, close_prices[slot], close_volumes[slot], close_outcome[slot],
                ):
                    return False
                close_state[slot] = 0 if close_reasons[slot] == REASON_PARTIAL_TARGET else 3
    return True


@njit(cache=True, nogil=True)
def _market_kernel(
    direction, orientation, contract_size, times, bid, ask, exit_quote, exit_cost,
    fx_bid, fx_ask, fx_valid, requests, fills, prices, volumes, opened,
    initial_sl, initial_tp, request_ambiguous, target_points, provider_indices, provider_actions,
    rollover_indices, rollover_times, rollover_costs, rollover_valid,
    protection_config, market_config, policy, partial_volumes,
):
    """Replay market requests with two bounded integer event streams.

    Protection config: stop distance, processing/ack/retry delays, capacity.
    Market config: entry ack, close processing/ack, volume min/max/step, capacity.
    Policy: fixed/trailing distances, hard/lock amounts, time/mode, provider mode,
    pending-entry policy, hard/lock enabled, BE enabled/distance, partial enabled/targets.
    """
    count = len(requests)
    entry_volumes = volumes
    volumes = volumes.copy()
    active = np.zeros(count, dtype=np.bool_)
    accepted = np.zeros(count, dtype=np.bool_)
    entry_state = np.zeros(count, dtype=np.int8)
    entry_ids = np.zeros(count, dtype=np.int64)
    entry_outcome = np.zeros(count, dtype=np.int64)
    entry_ack_due = np.full(count, PROTECTION_NONE, dtype=np.int64)
    acknowledged = np.full(count, PROTECTION_NONE, dtype=np.int64)
    close_state = np.zeros(count, dtype=np.int8)
    close_ids = np.zeros(count, dtype=np.int64)
    close_indices = np.full(count, -1, dtype=np.int64)
    close_times = np.zeros(count, dtype=np.int64)
    close_prices = np.full(count, PROTECTION_NONE, dtype=np.int64)
    close_reasons = np.zeros(count, dtype=np.int64)
    close_outcome = np.zeros(count, dtype=np.int64)
    close_ack_due = np.full(count, PROTECTION_NONE, dtype=np.int64)
    close_volumes = np.zeros(count, dtype=np.int64)
    be_armed = np.zeros(count, dtype=np.bool_)
    sl = initial_sl.copy()
    tp = initial_tp.copy()
    sl_reasons = np.full(count, REASON_INITIAL_SL, dtype=np.int64)
    tp_reasons = np.full(count, REASON_INITIAL_TP, dtype=np.int64)
    passive_due = np.full(count, PROTECTION_NONE, dtype=np.int64)
    passive_level = np.full(count, PROTECTION_NONE, dtype=np.int64)
    passive_reason = np.zeros(count, dtype=np.int64)
    trailing = np.full(count, PROTECTION_NONE, dtype=np.int64)
    if policy[1] > 0:
        trailing = prices - direction * policy[1]
    modify_ids = np.zeros(count, dtype=np.int64)
    modify_indices = np.full(count, -1, dtype=np.int64)
    modify_times = np.zeros(count, dtype=np.int64)
    modify_sl = np.full(count, PROTECTION_NONE, dtype=np.int64)
    modify_tp = np.full(count, PROTECTION_NONE, dtype=np.int64)
    modify_reasons = np.zeros(count, dtype=np.int64)
    modify_ack_due = np.full(count, PROTECTION_NONE, dtype=np.int64)
    modify_retry = np.zeros(count, dtype=np.int64)
    accrued = np.zeros(count, dtype=np.int64)
    protection_trace = np.zeros((protection_config[4], 8), dtype=np.int64)
    market_trace = np.zeros((market_config[6], 8), dtype=np.int64)
    exits = np.zeros((count * (2 if policy[13] else 1), 8), dtype=np.int64)
    counts = np.zeros(3, dtype=np.int64)
    # realized, reason, high, low, drawdown, favourable, adverse, mask, bad tick, last, unknown, in-flight, missing-fill
    stats = np.zeros(13, dtype=np.int64)
    stats[2] = PROTECTION_NONE
    stats[3] = np.iinfo(np.int64).max
    stats[8] = -1
    stats[9] = -1
    error = 0
    error_slot = -1
    rollover_error = -1
    rollover_cursor = 0
    provider_cursor = 0
    market_sequence = 0
    modify_sequence = 0
    cancelled = False
    basket_reason = REASON_NOT_CLOSED
    first_open = np.iinfo(np.int64).max
    lock_armed = False
    known_max_total = PROTECTION_NONE
    ever_requested = False
    partial_taken = False

    for index in range(len(times)):
        now = times[index]
        usable = bid[index] > 0 and ask[index] >= bid[index]
        if np.any((entry_state == 1) | (entry_state == 2) | (close_state == 1) | (close_state == 2)) and usable:
            stats[9] = index
            if not _market_acknowledge(
                market_trace, counts, index, now, market_sequence, entry_state,
                entry_ids, entry_ack_due, entry_outcome, prices, accepted, acknowledged,
                close_state, close_ids, close_ack_due, close_outcome, close_prices, entry_volumes, close_volumes, close_reasons,
            ):
                error = 1
        if not error and now >= market_config[7]:
            cancelled = True
            for slot in range(count):
                requested_ns = times[requests[slot]]
                if (
                    usable and entry_state[slot] == 1 and requested_ns < market_config[7]
                    and (market_config[7] < now or now < requested_ns + market_config[8])
                ):
                    error = 6
                    stats[9] = index
                    break
        for slot in range(count):
            if error or cancelled or entry_state[slot] != 0 or requests[slot] != index:
                continue
            market_sequence += 1
            request_price = ask[index] if direction == 1 else bid[index]
            if not _market_trace_emit(
                market_trace, counts, 1, slot, index, now, market_sequence,
                MARKET_ENTRY_REQUESTED, request_price, volumes[slot], 0,
            ):
                error = 1
                break
            entry_ids[slot] = market_sequence
            entry_state[slot] = 1
            ever_requested = True
            stats[9] = index
        if error and not np.any(active):
            break

        for slot in range(count):
            if error or entry_state[slot] != 1 or fills[slot] != index or not usable:
                continue
            if counts[1] >= market_trace.shape[0]:
                error = 1
                stats[9] = index
                break
            reject = 0
            if (
                volumes[slot] < market_config[3] or volumes[slot] > market_config[4]
                or volumes[slot] % market_config[5] != 0
            ):
                reject = MARKET_INVALID_VOLUME
            elif request_ambiguous[slot]:
                error = 8
                error_slot = slot
                stats[9] = index
                break
            elif not _protection_pair_valid(direction, exit_quote[index], sl[slot], tp[slot], protection_config[0]):
                reject = MARKET_INVALID_PROTECTION
            kind = MARKET_ENTRY_REJECTED if reject else MARKET_ENTRY_FILLED
            price = PROTECTION_NONE if reject else prices[slot]
            if not reject:
                if not _market_trace_emit(
                    protection_trace, counts, 0, slot, index, now, 0,
                    PROTECTION_EVENT_OPEN, sl[slot], tp[slot], 0,
                ):
                    error = 7
                    break
                accepted[slot] = True
                active[slot] = True
                first_open = min(first_open, opened[slot])
            if not _market_trace_emit(
                market_trace, counts, 1, slot, index, now, entry_ids[slot], kind,
                price, volumes[slot], reject,
            ):
                error = 1
                break
            entry_state[slot] = 2
            entry_outcome[slot] = reject if reject else MARKET_ACCEPTED
            entry_ack_due[slot] = now + market_config[0]
            stats[9] = index
            if reject:
                cancelled = True
                if not np.any(active):
                    stats[1] = REASON_ENTRY_REJECTED
                continue
        if error and not np.any(active):
            break

        while rollover_cursor < len(rollover_indices) and rollover_indices[rollover_cursor] <= index:
            event = rollover_cursor
            rollover_cursor += 1
            for slot in range(count):
                if not active[slot] or opened[slot] >= rollover_times[event]:
                    continue
                if not rollover_valid[event] or volumes[slot] >= rollover_costs.shape[1]:
                    rollover_error = event
                    stats[10] = 1
                    break
                accrued[slot] += rollover_costs[event, volumes[slot]]
            if rollover_error >= 0:
                break
        if rollover_error >= 0:
            stats[9] = index
            break

        if not usable:
            if np.any((entry_state == 1) | (entry_state == 2) | (close_state == 1) | (close_state == 2)):
                stats[9] = index
            if np.any(active):
                stats[7] |= BLOCK_INVALID_TICK
                stats[8] = index
                stats[9] = index
                break
            continue
        raw_exit = exit_quote[index]
        money_exit = raw_exit - direction * exit_cost
        if np.any(active):
            stats[9] = index
            floating = 0
            exact_total = True
            for slot in range(count):
                if not active[slot]:
                    continue
                move = direction * (raw_exit - prices[slot])
                stats[5] = max(stats[5], move)
                stats[6] = min(stats[6], move)
                value, exact = _money_minor_fixed(
                    direction, orientation, contract_size, prices[slot], money_exit,
                    volumes[slot], fx_bid[index], fx_ask[index], fx_valid[index],
                )
                floating += value + accrued[slot]
                exact_total = exact_total and exact
            total = stats[0] + floating
            if not exact_total:
                stats[7] |= BLOCK_STALE_EQUITY
            if exact_total and not stats[10]:
                stats[2] = max(stats[2], total)
                stats[3] = min(stats[3], total)
                stats[4] = max(stats[4], stats[2] - total)

        # Installed broker orders win over queued closes and modifications.
        for slot in range(count):
            if not active[slot]:
                continue
            reason = REASON_NOT_CLOSED
            price = money_exit
            if passive_due[slot] != PROTECTION_NONE:
                if now < passive_due[slot]:
                    continue
                reason = passive_reason[slot]
                if not protection_config[7]:
                    price = passive_level[slot] - direction * exit_cost
            elif sl[slot] != PROTECTION_NONE and direction * (raw_exit - sl[slot]) <= 0:
                reason = sl_reasons[slot]
            elif tp[slot] != PROTECTION_NONE and direction * (raw_exit - tp[slot]) >= 0:
                reason = tp_reasons[slot]
                if protection_config[5]:
                    if not _market_trace_emit(
                        protection_trace, counts, 0, slot, index, now, 0,
                        PROTECTION_EVENT_TOUCHED, sl[slot], tp[slot], reason,
                    ):
                        error = 7
                        break
                    if protection_config[6]:
                        passive_due[slot] = now + protection_config[6]
                        passive_level[slot] = tp[slot]
                        passive_reason[slot] = reason
                        continue
                if not protection_config[5] or not protection_config[7]:
                    price = tp[slot] - direction * exit_cost
            if reason == REASON_NOT_CLOSED:
                continue
            if not _market_trace_emit(
                protection_trace, counts, 0, slot, index, now, 0,
                PROTECTION_EVENT_CLOSED, sl[slot], tp[slot], reason,
            ):
                error = 7
                break
            value, exact = _market_realize(
                exits, counts, active, accrued, volumes, slot, index, prices[slot], price,
                volumes[slot], reason, direction, orientation, contract_size,
                fx_bid[index], fx_ask[index], fx_valid[index],
            )
            stats[1] = reason
            if exact:
                stats[0] += value
            else:
                stats[7] |= BLOCK_STALE_EXIT
                stats[8] = index
                stats[10] = 1
        if counts[2] > 0 and not np.any(active) and not policy[8]:
            cancelled = True
        if error:
            break

        for slot in np.argsort(close_ids):
            if close_state[slot] == 1 and index > close_indices[slot] and now >= close_times[slot] + market_config[1]:
                reject = not active[slot]
                kind = MARKET_CLOSE_REJECTED if reject else MARKET_CLOSE_FILLED
                price = PROTECTION_NONE if reject else money_exit
                reason = MARKET_ALREADY_CLOSED if reject else close_reasons[slot]
                if counts[1] >= market_trace.shape[0]:
                    error = 1
                    break
                if not reject:
                    if close_volumes[slot] >= volumes[slot] and not _market_trace_emit(
                        protection_trace, counts, 0, slot, index, now, 0,
                        PROTECTION_EVENT_CLOSED, sl[slot], tp[slot], reason,
                    ):
                        error = 7
                        break
                    value, exact = _market_realize(
                        exits, counts, active, accrued, volumes, slot, index, prices[slot], price,
                        close_volumes[slot], reason, direction, orientation, contract_size,
                        fx_bid[index], fx_ask[index], fx_valid[index],
                    )
                    stats[1] = reason
                    if exact:
                        stats[0] += value
                    else:
                        stats[7] |= BLOCK_STALE_EXIT
                        stats[8] = index
                        stats[10] = 1
                if not _market_trace_emit(
                    market_trace, counts, 1, slot, index, now, close_ids[slot],
                    kind, price, close_volumes[slot], reason,
                ):
                    error = 1
                    break
                close_state[slot] = 2
                close_ack_due[slot] = now + market_config[2]
                close_prices[slot] = price
                close_outcome[slot] = MARKET_ALREADY_CLOSED if reject else MARKET_ACCEPTED
                stats[9] = index
        if error:
            break

        if not _market_acknowledge(
            market_trace, counts, index, now, market_sequence, entry_state,
            entry_ids, entry_ack_due, entry_outcome, prices, accepted, acknowledged,
            close_state, close_ids, close_ack_due, close_outcome, close_prices, entry_volumes, close_volumes, close_reasons,
        ):
            error = 1
            break

        for slot in range(count):
            if not active[slot] or modify_ids[slot] == 0:
                continue
            if modify_ack_due[slot] == PROTECTION_NONE and index > modify_indices[slot] and now >= modify_times[slot] + protection_config[1]:
                valid = (passive_due[slot] == PROTECTION_NONE
                         and _protection_pair_valid(direction, raw_exit,
                                                    modify_sl[slot], modify_tp[slot],
                                                    protection_config[0]))
                kind = PROTECTION_EVENT_INSTALLED if valid else PROTECTION_EVENT_REJECTED
                if not _market_trace_emit(
                    protection_trace, counts, 0, slot, index, now, modify_ids[slot],
                    kind, modify_sl[slot], modify_tp[slot],
                    PROTECTION_REJECT_PASSIVE_PENDING
                    if passive_due[slot] != PROTECTION_NONE else 0,
                ):
                    error = 7
                    break
                if valid:
                    sl[slot] = modify_sl[slot]
                    tp[slot] = modify_tp[slot]
                    sl_reasons[slot] = modify_reasons[slot]
                    tp_reasons[slot] = REASON_PER_LEG_TARGET
                modify_ack_due[slot] = now + protection_config[2]
            if modify_ack_due[slot] != PROTECTION_NONE and now >= modify_ack_due[slot]:
                if not _market_trace_emit(
                    protection_trace, counts, 0, slot, index, now, modify_ids[slot],
                    PROTECTION_EVENT_ACKNOWLEDGED, sl[slot], tp[slot], 0,
                ):
                    error = 7
                    break
                modify_ids[slot] = 0
                modify_ack_due[slot] = PROTECTION_NONE
                modify_retry[slot] = now + protection_config[3]
        if error:
            break

        in_flight = np.any(entry_state == 1)
        entry_busy = in_flight or np.any(entry_state == 2)
        market_busy = np.any((close_state == 1) | (close_state == 2))
        if not np.any(active):
            future = not cancelled and np.any((entry_state == 0) & (requests > index))
            if ever_requested and not entry_busy and not market_busy and not (policy[8] and future):
                break
            continue
        if entry_busy or basket_reason != REASON_NOT_CLOSED:
            continue
        if np.any(((close_state == 1) | (close_state == 2)) & (close_reasons == REASON_PARTIAL_TARGET)):
            continue
        if policy[11]:
            for slot in range(count):
                if active[slot] and ((policy[11] == 1 and direction * (raw_exit - prices[slot]) >= policy[12])
                                    or (policy[11] == 2 and now >= policy[17])):
                    be_armed[slot] = True

        wanted = np.zeros(count, dtype=np.int64)
        if policy[9]:
            for slot in range(count):
                if not active[slot] or close_state[slot] != 0:
                    continue
                value, exact = _money_minor_fixed(
                    direction, orientation, contract_size, prices[slot], money_exit,
                    volumes[slot], fx_bid[index], fx_ask[index], fx_valid[index],
                )
                value += accrued[slot]
                if exact and value <= -policy[2]:
                    wanted[slot] = REASON_HARD_STOP_PER_LEG
                elif not exact:
                    stats[7] |= BLOCK_STALE_HARD_STOP
                    stats[8] = index
                    stats[10] = 1
        provider_close = False
        while provider_cursor < len(provider_indices) and provider_indices[provider_cursor] <= index:
            action = provider_actions[provider_cursor]
            if action == PROVIDER_CLOSE or (action == PROVIDER_LEGACY_CLOSE and policy[7] != MANAGEMENT_EXPLICIT_CLOSE_ONLY):
                provider_close = True
            provider_cursor += 1
        if provider_close and policy[7] != MANAGEMENT_IGNORE and basket_reason == REASON_NOT_CLOSED:
            basket_reason = REASON_PROVIDER_CLOSE
        floating = 0
        exact_total = True
        for slot in range(count):
            if active[slot]:
                value, exact = _money_minor_fixed(
                    direction, orientation, contract_size, prices[slot], money_exit,
                    volumes[slot], fx_bid[index], fx_ask[index], fx_valid[index],
                )
                floating += value + accrued[slot]
                exact_total = exact_total and exact
        total = stats[0] + floating
        if exact_total and not stats[10]:
            known_max_total = max(known_max_total, total)
        if policy[18] > 0:
            if not exact_total:
                stats[7] |= BLOCK_STALE_BASKET_STOP
                stats[10] = 1
            elif total <= -policy[18]:
                basket_reason = REASON_BASKET_STOP
        partial_now = False
        if policy[13]:
            if not exact_total:
                stats[7] |= BLOCK_STALE_BASKET_TARGET
                stats[10] = 1
            elif not partial_taken and total >= policy[14]:
                partial_taken = True
                partial_now = True
                for slot in range(count):
                    if active[slot] and wanted[slot] == REASON_NOT_CLOSED:
                        wanted[slot] = REASON_PARTIAL_TARGET
            elif partial_taken and total >= policy[15] and basket_reason == REASON_NOT_CLOSED:
                basket_reason = REASON_RUNNER_TARGET
        if policy[10] and not partial_now:
            if exact_total and known_max_total != PROTECTION_NONE:
                lock_armed = lock_armed or known_max_total >= policy[3]
                if lock_armed and total <= known_max_total - policy[4] and basket_reason == REASON_NOT_CLOSED:
                    basket_reason = REASON_PROFIT_LOCK
            elif not exact_total:
                stats[7] |= BLOCK_STALE_PROFIT_LOCK
                stats[10] = 1
        if not partial_now and now - first_open >= policy[5] and policy[6] > TIME_DISABLED and not exact_total:
            stats[7] |= BLOCK_STALE_TIME_EXIT
            stats[10] = 1
        time_matches = policy[6] == TIME_UNCONDITIONAL or (
            exact_total and (
                (policy[6] == TIME_LOSS_ONLY and total <= 0)
                or (policy[6] == TIME_PROFIT_ONLY and total > 0)
                or (policy[6] == TIME_NON_NEGATIVE and total >= 0)
            )
        )
        if not partial_now and now - first_open >= policy[5] and time_matches and basket_reason == REASON_NOT_CLOSED:
            basket_reason = REASON_TIME_EXIT
        if basket_reason != REASON_NOT_CLOSED:
            cancelled = True
            if in_flight:
                error = 6
                break
            for slot in range(count):
                if active[slot] and wanted[slot] == REASON_NOT_CLOSED:
                    wanted[slot] = basket_reason
        request_order = np.argsort(
            np.where(wanted == REASON_HARD_STOP_PER_LEG, 0, 1), kind="mergesort",
        )
        for slot in request_order:
            if not active[slot] or close_state[slot] != 0 or wanted[slot] == REASON_NOT_CLOSED:
                continue
            market_sequence += 1
            requested_volume = partial_volumes[slot] if wanted[slot] == REASON_PARTIAL_TARGET else volumes[slot]
            if not _market_trace_emit(
                market_trace, counts, 1, slot, index, now, market_sequence,
                MARKET_CLOSE_REQUESTED, raw_exit, requested_volume, wanted[slot],
            ):
                error = 1
                break
            close_state[slot] = 1
            close_ids[slot] = market_sequence
            close_indices[slot] = index
            close_times[slot] = now
            close_reasons[slot] = wanted[slot]
            close_volumes[slot] = requested_volume
        if error:
            break
        if partial_now:
            continue

        for slot in range(count):
            if not active[slot] or close_state[slot] != 0:
                continue
            if policy[1] > 0:
                candidate = raw_exit - direction * policy[1]
                if trailing[slot] == PROTECTION_NONE or direction * (candidate - trailing[slot]) > 0:
                    trailing[slot] = candidate
            desired_sl = PROTECTION_NONE
            reason = REASON_FIXED_SL
            if policy[0] > 0:
                desired_sl = policy[0] if policy[16] else prices[slot] - direction * policy[0]
            if trailing[slot] != PROTECTION_NONE:
                if desired_sl == PROTECTION_NONE or direction * (trailing[slot] - desired_sl) >= 0:
                    desired_sl = trailing[slot]
                    reason = REASON_TRAILING_STOP
            if be_armed[slot] and (desired_sl == PROTECTION_NONE or direction * (prices[slot] - desired_sl) >= 0):
                desired_sl = prices[slot]
                reason = REASON_BREAK_EVEN
            desired_tp = (target_points[slot] if policy[16] else prices[slot] + direction * target_points[slot]) if target_points[slot] else PROTECTION_NONE
            if modify_ids[slot] or now < modify_retry[slot] or (sl[slot] == desired_sl and tp[slot] == desired_tp):
                continue
            modify_sequence += 1
            if not _market_trace_emit(
                protection_trace, counts, 0, slot, index, now, modify_sequence,
                PROTECTION_EVENT_REQUESTED, desired_sl, desired_tp, 0,
            ):
                error = 7
                break
            modify_ids[slot] = modify_sequence
            modify_indices[slot] = index
            modify_times[slot] = now
            modify_sl[slot] = desired_sl
            modify_tp[slot] = desired_tp
            modify_reasons[slot] = reason
        if error:
            break

    if not error:
        if np.any(entry_state == 1):
            error = 2
        elif np.any(entry_state == 2):
            error = 3
        elif np.any(close_state == 1):
            error = 4
        elif np.any(close_state == 2):
            error = 5
    if np.any(active):
        stats[1] = REASON_NOT_CLOSED
        stats[10] = 1
        if error in (0, 2, 3, 4, 5) and rollover_error < 0:
            stats[7] |= BLOCK_PATH_ENDED
    if stats[9] >= 0:
        stats[11] = np.any(
            (entry_state == 1) & (fills < len(times)) & (requests <= stats[9]) & (fills > stats[9])
        )
        stats[12] = np.any((entry_state == 1) & (fills >= len(times)))
    return stats, accepted, acknowledged, exits, protection_trace, market_trace, counts, error, error_slot, rollover_error


@njit(cache=True, nogil=True)
def _protection_kernel(
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
    provider_indices,
    provider_actions,
    rollover_indices,
    rollover_times_ns,
    rollover_costs,
    rollover_valid,
    initial_sl,
    initial_tp,
    open_block_codes,
    leg_target_points,
    target_mode,
    stop_mode,
    stop_points,
    hard_stop_minor,
    trailing_points,
    lock_arm_minor,
    lock_giveback_minor,
    time_exit_ns,
    time_exit_mode,
    management_mode,
    keep_pending_entries,
    pending_request_index,
    minimum_distance,
    processing_delay_ns,
    acknowledgement_delay_ns,
    retry_delay_ns,
    passive_enabled,
    passive_delay_ns,
    passive_quote_price,
    event_budget,
    hypothetical_opening=True,
):
    position_count = len(schedule_indices)
    active = np.zeros(position_count, dtype=np.bool_)
    volumes = initial_volumes.copy()
    accrued_swap = np.zeros(position_count, dtype=np.int64)
    trailing_stops = np.full(position_count, PROTECTION_NONE, dtype=np.int64)
    if trailing_points > 0:
        for slot in range(position_count):
            trailing_stops[slot] = entry_prices[slot] - direction * trailing_points

    installed_sl = initial_sl.copy()
    installed_tp = initial_tp.copy()
    installed_sl_reason = np.full(position_count, REASON_INITIAL_SL, dtype=np.int8)
    installed_tp_reason = np.full(position_count, REASON_INITIAL_TP, dtype=np.int8)
    passive_due = np.full(position_count, PROTECTION_NONE, dtype=np.int64)
    passive_level = np.full(position_count, PROTECTION_NONE, dtype=np.int64)
    passive_reason = np.zeros(position_count, dtype=np.int8)
    pending = np.zeros(position_count, dtype=np.bool_)
    pending_request_id = np.zeros(position_count, dtype=np.int64)
    pending_sent_index = np.full(position_count, -1, dtype=np.int64)
    pending_sent_ns = np.zeros(position_count, dtype=np.int64)
    pending_sl = np.full(position_count, PROTECTION_NONE, dtype=np.int64)
    pending_tp = np.full(position_count, PROTECTION_NONE, dtype=np.int64)
    pending_sl_reason = np.full(position_count, REASON_FIXED_SL, dtype=np.int8)
    pending_tp_reason = np.full(position_count, REASON_PER_LEG_TARGET, dtype=np.int8)
    acknowledgement_ns = np.full(position_count, PROTECTION_NONE, dtype=np.int64)
    retry_ns = np.zeros(position_count, dtype=np.int64)
    request_sequence = 0

    event_slots = np.zeros(event_budget, dtype=np.int64)
    event_indices = np.zeros(event_budget, dtype=np.int64)
    event_times = np.zeros(event_budget, dtype=np.int64)
    event_request_ids = np.zeros(event_budget, dtype=np.int64)
    event_kinds = np.zeros(event_budget, dtype=np.int8)
    event_sl = np.full(event_budget, PROTECTION_NONE, dtype=np.int64)
    event_tp = np.full(event_budget, PROTECTION_NONE, dtype=np.int64)
    event_reasons = np.zeros(event_budget, dtype=np.int8)
    event_count = 0

    max_exits = max(1, position_count)
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
    rollover_cursor = 0
    rollover_blocker_event = -1
    rollover_blocked = False
    realized = 0
    money_unknown = False
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
    protection_block_code = PROTECTION_BLOCK_NONE
    protection_block_slot = -1
    protection_money_unknown = False
    entries_seen = 0
    active_count = 0

    for index in range(len(times)):
        now = times[index]
        if pending_request_index >= 0 and index >= pending_request_index:
            last_index = index

        failed_opening = False
        while schedule_cursor < position_count and schedule_indices[schedule_cursor] == index:
            slot = schedule_cursor
            opening_block = open_block_codes[slot]
            if opening_block == PROTECTION_BLOCK_NONE and hypothetical_opening:
                if not _protection_pair_valid(
                    direction,
                    exit_quote[index],
                    initial_sl[slot],
                    initial_tp[slot],
                    minimum_distance,
                ):
                    opening_block = PROTECTION_BLOCK_INITIAL_INVALID
            if opening_block != PROTECTION_BLOCK_NONE:
                protection_block_code = opening_block
                protection_block_slot = slot
                protection_money_unknown = True
                failed_opening = True
                last_index = index
                break
            event_count, emitted = _emit_protection_event(
                event_count,
                event_slots,
                event_indices,
                event_times,
                event_request_ids,
                event_kinds,
                event_sl,
                event_tp,
                event_reasons,
                slot,
                index,
                now,
                0,
                PROTECTION_EVENT_OPEN,
                installed_sl[slot],
                installed_tp[slot],
                0,
            )
            if not emitted:
                protection_block_code = PROTECTION_BLOCK_EVENT_BUDGET
                protection_block_slot = slot
                protection_money_unknown = True
                failed_opening = True
                last_index = index
                break
            active[slot] = True
            active_count += 1
            entries_seen += 1
            if opened_ns[slot] < first_open_ns:
                first_open_ns = opened_ns[slot]
            schedule_cursor += 1

        if failed_opening and active_count == 0:
            break

        while (
            rollover_cursor < len(rollover_indices)
            and rollover_indices[rollover_cursor] <= index
        ):
            event_index = rollover_cursor
            rollover_cursor += 1
            eligible_count = 0
            for slot in range(position_count):
                if active[slot] and opened_ns[slot] < rollover_times_ns[event_index]:
                    eligible_count += 1
            if eligible_count == 0:
                continue
            if not rollover_valid[event_index]:
                blocker_mask |= BLOCK_SWAP_ROLLOVER
                blocker_index = index
                rollover_blocker_event = event_index
                money_unknown = True
                rollover_blocked = True
                break
            for slot in range(position_count):
                if not active[slot] or opened_ns[slot] >= rollover_times_ns[event_index]:
                    continue
                units = volumes[slot]
                if units < 0 or units >= rollover_costs.shape[1]:
                    blocker_mask |= BLOCK_SWAP_VOLUME
                    blocker_index = index
                    rollover_blocker_event = event_index
                    money_unknown = True
                    rollover_blocked = True
                    break
                accrued_swap[slot] += rollover_costs[event_index, units]
            if rollover_blocked:
                break
        if rollover_blocked:
            last_index = index
            break

        if active_count == 0:
            if failed_opening:
                break
            if schedule_cursor >= position_count and entries_seen > 0 and pending_request_index < 0:
                break
            continue
        if bid[index] <= 0 or ask[index] < bid[index]:
            blocker_mask |= BLOCK_INVALID_TICK
            blocker_index = index
            last_index = index
            break
        last_index = index

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

        floating = 0
        floating_exact = True
        for slot in range(position_count):
            if not active[slot]:
                continue
            value, exact = _money_minor_fixed(
                direction,
                orientation,
                contract_size,
                entry_prices[slot],
                money_exit,
                volumes[slot],
                fx_bid[index],
                fx_ask[index],
                fx_valid[index],
            )
            floating += value + accrued_swap[slot]
            floating_exact = floating_exact and exact
        total = realized + floating
        if not floating_exact:
            blocker_mask |= BLOCK_STALE_EQUITY
        if floating_exact and not money_unknown:
            if total > max_total:
                max_total = total
            if total < min_total:
                min_total = total
            drawdown = max_total - total
            if drawdown > max_drawdown:
                max_drawdown = drawdown

        for slot in range(position_count):
            if not active[slot]:
                continue
            reason = REASON_NOT_CLOSED
            target_exit = money_exit
            if passive_due[slot] != PROTECTION_NONE:
                if now < passive_due[slot]:
                    continue
                reason = passive_reason[slot]
                if not passive_quote_price:
                    target_exit = passive_level[slot] - direction * exit_cost_points
            elif (
                installed_sl[slot] != PROTECTION_NONE
                and direction * (raw_exit - installed_sl[slot]) <= 0
            ):
                reason = installed_sl_reason[slot]
            elif (
                installed_tp[slot] != PROTECTION_NONE
                and direction * (raw_exit - installed_tp[slot]) >= 0
            ):
                reason = installed_tp_reason[slot]
                if passive_enabled:
                    event_count, emitted = _emit_protection_event(
                        event_count, event_slots, event_indices, event_times,
                        event_request_ids, event_kinds, event_sl, event_tp,
                        event_reasons, slot, index, now, 0,
                        PROTECTION_EVENT_TOUCHED, installed_sl[slot],
                        installed_tp[slot], reason,
                    )
                    if not emitted:
                        protection_block_code = PROTECTION_BLOCK_EVENT_BUDGET
                        protection_block_slot = slot
                        protection_money_unknown = True
                        break
                    if passive_delay_ns:
                        passive_due[slot] = now + passive_delay_ns
                        passive_level[slot] = installed_tp[slot]
                        passive_reason[slot] = reason
                        continue
                if not passive_enabled or not passive_quote_price:
                    target_exit = installed_tp[slot] - direction * exit_cost_points
            if reason == REASON_NOT_CLOSED:
                continue
            event_count, emitted = _emit_protection_event(
                event_count,
                event_slots,
                event_indices,
                event_times,
                event_request_ids,
                event_kinds,
                event_sl,
                event_tp,
                event_reasons,
                slot,
                index,
                now,
                0,
                PROTECTION_EVENT_CLOSED,
                installed_sl[slot],
                installed_tp[slot],
                reason,
            )
            if not emitted:
                protection_block_code = PROTECTION_BLOCK_EVENT_BUDGET
                protection_block_slot = slot
                protection_money_unknown = True
                break
            value, exact = _money_minor_fixed(
                direction,
                orientation,
                contract_size,
                entry_prices[slot],
                target_exit,
                volumes[slot],
                fx_bid[index],
                fx_ask[index],
                fx_valid[index],
            )
            value += accrued_swap[slot]
            accrued_swap[slot] = 0
            exit_slots[exit_count] = slot
            exit_indices[exit_count] = index
            exit_entry_prices[exit_count] = entry_prices[slot]
            exit_prices[exit_count] = target_exit
            exit_volumes[exit_count] = volumes[slot]
            exit_pnls[exit_count] = value
            exit_exact[exit_count] = exact
            exit_reasons[exit_count] = reason
            exit_count += 1
            if exact:
                realized += value
            else:
                blocker_mask |= BLOCK_STALE_EXIT
                blocker_index = index
                money_unknown = True
            active[slot] = False
            active_count -= 1
            exit_reason = reason

        if protection_block_code != PROTECTION_BLOCK_NONE:
            break
        if active_count == 0:
            if keep_pending_entries and (
                schedule_cursor < position_count or pending_request_index >= 0
            ):
                continue
            break
        if failed_opening:
            break

        for slot in range(position_count):
            if not active[slot] or not pending[slot]:
                continue
            if (
                acknowledgement_ns[slot] == PROTECTION_NONE
                and index > pending_sent_index[slot]
                and now >= pending_sent_ns[slot] + processing_delay_ns
            ):
                accepted = (passive_due[slot] == PROTECTION_NONE
                            and _protection_pair_valid(
                                direction, raw_exit, pending_sl[slot],
                                pending_tp[slot], minimum_distance))
                kind = (
                    PROTECTION_EVENT_INSTALLED
                    if accepted
                    else PROTECTION_EVENT_REJECTED
                )
                event_count, emitted = _emit_protection_event(
                    event_count,
                    event_slots,
                    event_indices,
                    event_times,
                    event_request_ids,
                    event_kinds,
                    event_sl,
                    event_tp,
                    event_reasons,
                    slot,
                    index,
                    now,
                    pending_request_id[slot],
                    kind,
                    pending_sl[slot],
                    pending_tp[slot],
                    PROTECTION_REJECT_PASSIVE_PENDING
                    if passive_due[slot] != PROTECTION_NONE else 0,
                )
                if not emitted:
                    protection_block_code = PROTECTION_BLOCK_EVENT_BUDGET
                    protection_block_slot = slot
                    protection_money_unknown = True
                    break
                if accepted:
                    installed_sl[slot] = pending_sl[slot]
                    installed_tp[slot] = pending_tp[slot]
                    installed_sl_reason[slot] = pending_sl_reason[slot]
                    installed_tp_reason[slot] = pending_tp_reason[slot]
                acknowledgement_ns[slot] = now + acknowledgement_delay_ns
            if (
                acknowledgement_ns[slot] != PROTECTION_NONE
                and now >= acknowledgement_ns[slot]
            ):
                event_count, emitted = _emit_protection_event(
                    event_count,
                    event_slots,
                    event_indices,
                    event_times,
                    event_request_ids,
                    event_kinds,
                    event_sl,
                    event_tp,
                    event_reasons,
                    slot,
                    index,
                    now,
                    pending_request_id[slot],
                    PROTECTION_EVENT_ACKNOWLEDGED,
                    installed_sl[slot],
                    installed_tp[slot],
                    0,
                )
                if not emitted:
                    protection_block_code = PROTECTION_BLOCK_EVENT_BUDGET
                    protection_block_slot = slot
                    protection_money_unknown = True
                    break
                pending[slot] = False
                acknowledgement_ns[slot] = PROTECTION_NONE
                retry_ns[slot] = now + retry_delay_ns
        if protection_block_code != PROTECTION_BLOCK_NONE:
            break

        if hard_stop_minor > 0:
            for slot in range(position_count):
                if not active[slot]:
                    continue
                value, exact = _money_minor_fixed(
                    direction,
                    orientation,
                    contract_size,
                    entry_prices[slot],
                    money_exit,
                    volumes[slot],
                    fx_bid[index],
                    fx_ask[index],
                    fx_valid[index],
                )
                value += accrued_swap[slot]
                if exact and value <= -hard_stop_minor:
                    protection_block_code = PROTECTION_BLOCK_MARKET_CLOSE
                    protection_block_slot = slot
                    protection_money_unknown = True
                    break
                if not exact:
                    blocker_mask |= BLOCK_STALE_HARD_STOP
                    blocker_index = index
                    money_unknown = True
            if protection_block_code != PROTECTION_BLOCK_NONE:
                break

        close_due = False
        while provider_cursor < len(provider_indices) and provider_indices[provider_cursor] <= index:
            action = provider_actions[provider_cursor]
            if (
                action == PROVIDER_CLOSE
                or (
                    action == PROVIDER_LEGACY_CLOSE
                    and management_mode != MANAGEMENT_EXPLICIT_CLOSE_ONLY
                )
            ):
                close_due = True
            provider_cursor += 1
        if close_due and management_mode != MANAGEMENT_IGNORE:
            protection_block_code = PROTECTION_BLOCK_MARKET_CLOSE
            protection_money_unknown = True
            break

        floating = 0
        floating_exact = True
        for slot in range(position_count):
            if active[slot]:
                value, exact = _money_minor_fixed(
                    direction,
                    orientation,
                    contract_size,
                    entry_prices[slot],
                    money_exit,
                    volumes[slot],
                    fx_bid[index],
                    fx_ask[index],
                    fx_valid[index],
                )
                floating += value + accrued_swap[slot]
                floating_exact = floating_exact and exact
        total = realized + floating

        if lock_arm_minor > 0:
            if floating_exact and max_total != np.iinfo(np.int64).min:
                lock_armed = lock_armed or max_total >= lock_arm_minor
                if lock_armed and total <= max_total - lock_giveback_minor:
                    protection_block_code = PROTECTION_BLOCK_MARKET_CLOSE
                    protection_money_unknown = True
                    break
            elif not floating_exact:
                blocker_mask |= BLOCK_STALE_PROFIT_LOCK
                money_unknown = True

        time_due = (
            active_count > 0
            and first_open_ns != np.iinfo(np.int64).max
            and now - first_open_ns >= time_exit_ns
        )
        if time_due and time_exit_mode > TIME_DISABLED and not floating_exact:
            blocker_mask |= BLOCK_STALE_TIME_EXIT
            money_unknown = True
        time_matches = (
            time_exit_mode == TIME_UNCONDITIONAL
            or (
                floating_exact
                and (
                    (time_exit_mode == TIME_LOSS_ONLY and total <= 0)
                    or (time_exit_mode == TIME_PROFIT_ONLY and total > 0)
                    or (time_exit_mode == TIME_NON_NEGATIVE and total >= 0)
                )
            )
        )
        if time_due and time_matches:
            protection_block_code = PROTECTION_BLOCK_MARKET_CLOSE
            protection_money_unknown = True
            break

        if trailing_points > 0:
            candidate = raw_exit - direction * trailing_points
            for slot in range(position_count):
                if not active[slot]:
                    continue
                if (
                    trailing_stops[slot] == PROTECTION_NONE
                    or (direction == 1 and candidate > trailing_stops[slot])
                    or (direction == -1 and candidate < trailing_stops[slot])
                ):
                    trailing_stops[slot] = candidate

        for slot in range(position_count):
            if not active[slot]:
                continue
            desired_sl = PROTECTION_NONE
            desired_sl_reason = REASON_FIXED_SL
            if stop_mode == STOP_FIXED_MOVE:
                desired_sl = entry_prices[slot] - direction * stop_points
            if trailing_stops[slot] != PROTECTION_NONE:
                if (
                    desired_sl == PROTECTION_NONE
                    or (direction == 1 and trailing_stops[slot] >= desired_sl)
                    or (direction == -1 and trailing_stops[slot] <= desired_sl)
                ):
                    desired_sl = trailing_stops[slot]
                    desired_sl_reason = REASON_TRAILING_STOP
            desired_tp = PROTECTION_NONE
            if target_mode == TARGET_PER_LEG_STEPS:
                desired_tp = entry_prices[slot] + direction * leg_target_points[slot]
            if (
                pending[slot]
                or now < retry_ns[slot]
                or (
                    installed_sl[slot] == desired_sl
                    and installed_tp[slot] == desired_tp
                )
            ):
                continue
            next_request_id = request_sequence + 1
            event_count, emitted = _emit_protection_event(
                event_count,
                event_slots,
                event_indices,
                event_times,
                event_request_ids,
                event_kinds,
                event_sl,
                event_tp,
                event_reasons,
                slot,
                index,
                now,
                next_request_id,
                PROTECTION_EVENT_REQUESTED,
                desired_sl,
                desired_tp,
                0,
            )
            if not emitted:
                protection_block_code = PROTECTION_BLOCK_EVENT_BUDGET
                protection_block_slot = slot
                protection_money_unknown = True
                break
            request_sequence = next_request_id
            pending[slot] = True
            pending_request_id[slot] = next_request_id
            pending_sent_index[slot] = index
            pending_sent_ns[slot] = now
            pending_sl[slot] = desired_sl
            pending_tp[slot] = desired_tp
            pending_sl_reason[slot] = desired_sl_reason
            pending_tp_reason[slot] = REASON_PER_LEG_TARGET
            acknowledgement_ns[slot] = PROTECTION_NONE
        if protection_block_code != PROTECTION_BLOCK_NONE:
            break

    if (
        active_count > 0
        and not rollover_blocked
        and protection_block_code == PROTECTION_BLOCK_NONE
    ):
        blocker_mask |= BLOCK_PATH_ENDED
        protection_money_unknown = True
        exit_reason = REASON_NOT_CLOSED

    if protection_block_code != PROTECTION_BLOCK_NONE:
        protection_money_unknown = True

    return (
        realized,
        exit_reason,
        max_total,
        min_total,
        max_drawdown,
        max_favourable_points,
        max_adverse_points,
        blocker_mask,
        blocker_index,
        rollover_blocker_event,
        last_index,
        False,
        entries_seen,
        np.sum(initial_volumes[:entries_seen]),
        exit_count,
        exit_slots,
        exit_indices,
        exit_entry_prices,
        exit_prices,
        exit_volumes,
        exit_pnls,
        exit_exact,
        exit_reasons,
        event_count,
        event_slots,
        event_indices,
        event_times,
        event_request_ids,
        event_kinds,
        event_sl,
        event_tp,
        event_reasons,
        protection_block_code,
        protection_block_slot,
        protection_money_unknown,
    )


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
    rollover_indices,
    rollover_times_ns,
    rollover_costs,
    rollover_valid,
    target_mode,
    leg_target_points,
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
    hard_stop_minor,
    trailing_points,
    lock_arm_minor,
    lock_giveback_minor,
    time_exit_ns,
    time_exit_mode,
    management_mode,
    provider_target_index,
    keep_pending_entries,
    pending_request_index,
):
    position_count = len(schedule_indices)
    active = np.zeros(position_count, dtype=np.bool_)
    volumes = initial_volumes.copy()
    accrued_swap = np.zeros(position_count, dtype=np.int64)
    be_stops = np.zeros(position_count, dtype=np.int64)
    be_reasons = np.full(position_count, REASON_BREAK_EVEN, dtype=np.int8)
    trailing_stops = np.zeros(position_count, dtype=np.int64)
    if trailing_points > 0:
        for slot in range(position_count):
            trailing_stops[slot] = (
                entry_prices[slot] - direction * trailing_points
            )
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
    rollover_cursor = 0
    rollover_blocker_event = -1
    rollover_blocked = False
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
        if pending_request_index >= 0 and index >= pending_request_index:
            last_index = index
        while schedule_cursor < position_count and schedule_indices[schedule_cursor] == index:
            active[schedule_cursor] = True
            active_count += 1
            entries_seen += 1
            if opened_ns[schedule_cursor] < first_open_ns:
                first_open_ns = opened_ns[schedule_cursor]
            schedule_cursor += 1
        while (
            rollover_cursor < len(rollover_indices)
            and rollover_indices[rollover_cursor] <= index
        ):
            event_index = rollover_cursor
            rollover_cursor += 1
            eligible_count = 0
            for slot in range(position_count):
                if active[slot] and opened_ns[slot] < rollover_times_ns[event_index]:
                    eligible_count += 1
            if eligible_count == 0:
                continue
            if not rollover_valid[event_index]:
                blocker_mask |= BLOCK_SWAP_ROLLOVER
                blocker_index = index
                rollover_blocker_event = event_index
                money_unknown = True
                rollover_blocked = True
                break
            for slot in range(position_count):
                if (
                    not active[slot]
                    or opened_ns[slot] >= rollover_times_ns[event_index]
                ):
                    continue
                units = volumes[slot]
                if units < 0 or units >= rollover_costs.shape[1]:
                    blocker_mask |= BLOCK_SWAP_VOLUME
                    blocker_index = index
                    rollover_blocker_event = event_index
                    money_unknown = True
                    rollover_blocked = True
                    break
                accrued_swap[slot] += rollover_costs[event_index, units]
            if rollover_blocked:
                break
        if rollover_blocked:
            last_index = index
            break
        if active_count == 0:
            if schedule_cursor >= position_count and entries_seen > 0 and pending_request_index < 0:
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
            floating += value + accrued_swap[slot]
            floating_exact = floating_exact and exact
        total = realized + floating
        if not floating_exact:
            blocker_mask |= BLOCK_STALE_EQUITY
        if floating_exact and not money_unknown:
            if total > max_total:
                max_total = total
            if total < min_total:
                min_total = total
            drawdown = max_total - total
            if drawdown > max_drawdown:
                max_drawdown = drawdown

        if stop_mode == STOP_BASKET and not floating_exact:
            blocker_mask |= BLOCK_STALE_BASKET_STOP
            money_unknown = True
        if stop_mode == STOP_BASKET and total <= -stop_minor:
            if floating_exact:
                for slot in range(position_count):
                    if active[slot]:
                        value, exact = _money_minor_fixed(direction, orientation, contract_size, entry_prices[slot], money_exit, volumes[slot], fx_bid[index], fx_ask[index], fx_valid[index])
                        value += accrued_swap[slot]
                        accrued_swap[slot] = 0
                        exit_slots[exit_count] = slot; exit_indices[exit_count] = index
                        exit_entry_prices[exit_count] = entry_prices[slot]; exit_prices[exit_count] = money_exit
                        exit_volumes[exit_count] = volumes[slot]; exit_pnls[exit_count] = value
                        exit_exact[exit_count] = exact; exit_reasons[exit_count] = REASON_BASKET_STOP
                        exit_count += 1; realized += value; active[slot] = False; active_count -= 1
                exit_reason = REASON_BASKET_STOP
                break
            blocker_mask |= BLOCK_STALE_BASKET_STOP
            money_unknown = True

        if hard_stop_minor > 0:
            for slot in range(position_count):
                if not active[slot]:
                    continue
                value, current_exact = _money_minor_fixed(
                    direction, orientation, contract_size,
                    entry_prices[slot], money_exit, volumes[slot],
                    fx_bid[index], fx_ask[index], fx_valid[index],
                )
                value += accrued_swap[slot]
                if current_exact and value <= -hard_stop_minor:
                    exit_slots[exit_count] = slot
                    exit_indices[exit_count] = index
                    exit_entry_prices[exit_count] = entry_prices[slot]
                    exit_prices[exit_count] = money_exit
                    exit_volumes[exit_count] = volumes[slot]
                    exit_pnls[exit_count] = value
                    exit_exact[exit_count] = current_exact
                    exit_reasons[exit_count] = REASON_HARD_STOP_PER_LEG
                    exit_count += 1
                    realized += value
                    accrued_swap[slot] = 0
                    active[slot] = False
                    active_count -= 1
                    exit_reason = REASON_HARD_STOP_PER_LEG
                elif not current_exact:
                    blocker_mask |= BLOCK_STALE_HARD_STOP
                    blocker_index = index
                    money_unknown = True
            if active_count == 0:
                break

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
            if (
                action == PROVIDER_CLOSE
                or (
                    action == PROVIDER_LEGACY_CLOSE
                    and management_mode != MANAGEMENT_EXPLICIT_CLOSE_ONLY
                )
            ):
                close_due = True
            provider_cursor += 1
        if close_due and management_mode != MANAGEMENT_IGNORE:
            for slot in range(position_count):
                if active[slot]:
                    value, exact = _money_minor_fixed(direction, orientation, contract_size, entry_prices[slot], money_exit, volumes[slot], fx_bid[index], fx_ask[index], fx_valid[index])
                    value += accrued_swap[slot]
                    accrued_swap[slot] = 0
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
            if trailing_stops[slot] != 0:
                if (
                    level == 0
                    or (direction == 1 and trailing_stops[slot] >= level)
                    or (direction == -1 and trailing_stops[slot] <= level)
                ):
                    level = trailing_stops[slot]
                    reason = REASON_TRAILING_STOP
            if be_stops[slot] != 0:
                if level == 0 or (direction == 1 and be_stops[slot] >= level) or (direction == -1 and be_stops[slot] <= level):
                    level = be_stops[slot]
                    reason = be_reasons[slot]
            hit = level != 0 and ((direction == 1 and raw_exit <= level) or (direction == -1 and raw_exit >= level))
            if hit:
                value, exact = _money_minor_fixed(direction, orientation, contract_size, entry_prices[slot], money_exit, volumes[slot], fx_bid[index], fx_ask[index], fx_valid[index])
                value += accrued_swap[slot]
                accrued_swap[slot] = 0
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
                floating += value + accrued_swap[slot]; floating_exact = floating_exact and exact
        total = realized + floating

        if target_mode == TARGET_PROVIDER_LEG:
            for slot in range(position_count):
                level = current_tp[slot]
                hit = active[slot] and level != 0 and ((direction == 1 and raw_exit >= level) or (direction == -1 and raw_exit <= level))
                if hit:
                    target_exit = level - direction * exit_cost_points
                    value, exact = _money_minor_fixed(direction, orientation, contract_size, entry_prices[slot], target_exit, volumes[slot], fx_bid[index], fx_ask[index], fx_valid[index])
                    value += accrued_swap[slot]
                    accrued_swap[slot] = 0
                    exit_slots[exit_count] = slot; exit_indices[exit_count] = index
                    exit_entry_prices[exit_count] = entry_prices[slot]; exit_prices[exit_count] = target_exit
                    exit_volumes[exit_count] = volumes[slot]; exit_pnls[exit_count] = value
                    exit_exact[exit_count] = exact; exit_reasons[exit_count] = REASON_PROVIDER_TP
                    exit_count += 1
                    if exact: realized += value
                    else: blocker_mask |= BLOCK_STALE_EXIT; blocker_index = index; money_unknown = True
                    active[slot] = False; active_count -= 1; exit_reason = REASON_PROVIDER_TP
        elif target_mode == TARGET_PER_LEG_STEPS:
            for slot in range(position_count):
                level = entry_prices[slot] + direction * leg_target_points[slot]
                hit = active[slot] and (
                    (direction == 1 and raw_exit >= level)
                    or (direction == -1 and raw_exit <= level)
                )
                if hit:
                    target_exit = level - direction * exit_cost_points
                    value, current_exact = _money_minor_fixed(
                        direction, orientation, contract_size,
                        entry_prices[slot], target_exit, volumes[slot],
                        fx_bid[index], fx_ask[index], fx_valid[index],
                    )
                    value += accrued_swap[slot]
                    accrued_swap[slot] = 0
                    exit_slots[exit_count] = slot
                    exit_indices[exit_count] = index
                    exit_entry_prices[exit_count] = entry_prices[slot]
                    exit_prices[exit_count] = target_exit
                    exit_volumes[exit_count] = volumes[slot]
                    exit_pnls[exit_count] = value
                    exit_exact[exit_count] = current_exact
                    exit_reasons[exit_count] = REASON_PER_LEG_TARGET
                    exit_count += 1
                    if current_exact:
                        realized += value
                    else:
                        blocker_mask |= BLOCK_STALE_EXIT
                        blocker_index = index
                        money_unknown = True
                    active[slot] = False
                    active_count -= 1
                    exit_reason = REASON_PER_LEG_TARGET
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
                    target_exit = level - direction * exit_cost_points
                    for slot in range(position_count):
                        if active[slot]:
                            value, exact = _money_minor_fixed(direction, orientation, contract_size, entry_prices[slot], target_exit, volumes[slot], fx_bid[index], fx_ask[index], fx_valid[index])
                            value += accrued_swap[slot]
                            accrued_swap[slot] = 0
                            exit_slots[exit_count] = slot; exit_indices[exit_count] = index
                            exit_entry_prices[exit_count] = entry_prices[slot]; exit_prices[exit_count] = target_exit
                            exit_volumes[exit_count] = volumes[slot]; exit_pnls[exit_count] = value
                            exit_exact[exit_count] = exact; exit_reasons[exit_count] = REASON_PROVIDER_TARGET_ALL
                            exit_count += 1
                            if exact: realized += value
                            else: blocker_mask |= BLOCK_STALE_EXIT; blocker_index = index; money_unknown = True
                            active[slot] = False; active_count -= 1
                    exit_reason = REASON_PROVIDER_TARGET_ALL
        elif target_mode == TARGET_FIXED_BASKET and (not floating_exact or total >= target_minor):
            if floating_exact:
                for slot in range(position_count):
                    if active[slot]:
                        value, exact = _money_minor_fixed(direction, orientation, contract_size, entry_prices[slot], money_exit, volumes[slot], fx_bid[index], fx_ask[index], fx_valid[index])
                        value += accrued_swap[slot]
                        accrued_swap[slot] = 0
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
                        value += accrued_swap[slot]
                        accrued_swap[slot] = 0
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
            if not floating_exact:
                blocker_mask |= BLOCK_STALE_BASKET_TARGET
                money_unknown = True
            if not partial_taken and floating_exact and total >= target_minor:
                for slot in range(position_count):
                    if active[slot]:
                        close_volume = int(math.floor(volumes[slot] * partial_fraction + 0.5))
                        if close_volume <= 0: continue
                        value, exact = _money_minor_fixed(direction, orientation, contract_size, entry_prices[slot], money_exit, close_volume, fx_bid[index], fx_ask[index], fx_valid[index])
                        allocated_swap = _allocate_swap_fixed(
                            accrued_swap[slot],
                            close_volume,
                            volumes[slot],
                        )
                        value += allocated_swap
                        accrued_swap[slot] -= allocated_swap
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
                        floating += value + accrued_swap[slot]; floating_exact = floating_exact and exact
                total = realized + floating
                if partial_taken and floating_exact and total >= runner_minor:
                    for slot in range(position_count):
                        if active[slot]:
                            value, exact = _money_minor_fixed(direction, orientation, contract_size, entry_prices[slot], money_exit, volumes[slot], fx_bid[index], fx_ask[index], fx_valid[index])
                            value += accrued_swap[slot]
                            accrued_swap[slot] = 0
                            exit_slots[exit_count] = slot; exit_indices[exit_count] = index
                            exit_entry_prices[exit_count] = entry_prices[slot]; exit_prices[exit_count] = money_exit
                            exit_volumes[exit_count] = volumes[slot]; exit_pnls[exit_count] = value
                            exit_exact[exit_count] = exact; exit_reasons[exit_count] = REASON_RUNNER_TARGET
                            exit_count += 1; realized += value; active[slot] = False; active_count -= 1
                    exit_reason = REASON_RUNNER_TARGET
        if active_count == 0:
            if keep_pending_entries and (schedule_cursor < position_count or pending_request_index >= 0):
                continue
            break

        if lock_arm_minor > 0:
            if floating_exact and max_total != np.iinfo(np.int64).min:
                lock_armed = lock_armed or max_total >= lock_arm_minor
                if lock_armed and total <= max_total - lock_giveback_minor:
                    for slot in range(position_count):
                        if active[slot]:
                            value, exact = _money_minor_fixed(direction, orientation, contract_size, entry_prices[slot], money_exit, volumes[slot], fx_bid[index], fx_ask[index], fx_valid[index])
                            value += accrued_swap[slot]
                            accrued_swap[slot] = 0
                            exit_slots[exit_count] = slot; exit_indices[exit_count] = index
                            exit_entry_prices[exit_count] = entry_prices[slot]; exit_prices[exit_count] = money_exit
                            exit_volumes[exit_count] = volumes[slot]; exit_pnls[exit_count] = value
                            exit_exact[exit_count] = exact; exit_reasons[exit_count] = REASON_PROFIT_LOCK
                            exit_count += 1; realized += value; active[slot] = False; active_count -= 1
                    exit_reason = REASON_PROFIT_LOCK
                    break
            elif not floating_exact:
                blocker_mask |= BLOCK_STALE_PROFIT_LOCK; money_unknown = True

        time_due = (
            active_count > 0
            and first_open_ns != np.iinfo(np.int64).max
            and now - first_open_ns >= time_exit_ns
        )
        if time_due and time_exit_mode > TIME_DISABLED and not floating_exact:
            blocker_mask |= BLOCK_STALE_TIME_EXIT
            money_unknown = True
        time_matches = (
            time_exit_mode == TIME_UNCONDITIONAL
            or (
                floating_exact
                and (
                    (time_exit_mode == TIME_LOSS_ONLY and total <= 0)
                    or (time_exit_mode == TIME_PROFIT_ONLY and total > 0)
                    or (time_exit_mode == TIME_NON_NEGATIVE and total >= 0)
                )
            )
        )
        if time_due and time_matches:
            for slot in range(position_count):
                if active[slot]:
                    value, exact = _money_minor_fixed(direction, orientation, contract_size, entry_prices[slot], money_exit, volumes[slot], fx_bid[index], fx_ask[index], fx_valid[index])
                    value += accrued_swap[slot]
                    accrued_swap[slot] = 0
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

        if active_count > 0 and trailing_points > 0:
            candidate = raw_exit - direction * trailing_points
            for slot in range(position_count):
                if not active[slot]:
                    continue
                if (
                    trailing_stops[slot] == 0
                    or (direction == 1 and candidate > trailing_stops[slot])
                    or (direction == -1 and candidate < trailing_stops[slot])
                ):
                    trailing_stops[slot] = candidate

    if active_count > 0 and not rollover_blocked:
        if last_index >= 0:
            raw_exit = exit_quote[last_index]
            money_exit = raw_exit - direction * exit_cost_points
            for slot in range(position_count):
                if active[slot]:
                    value, exact = _money_minor_fixed(direction, orientation, contract_size, entry_prices[slot], money_exit, volumes[slot], fx_bid[last_index], fx_ask[last_index], fx_valid[last_index])
                    value += accrued_swap[slot]
                    accrued_swap[slot] = 0
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
        rollover_blocker_event, last_index, False, entries_seen,
        np.sum(initial_volumes[:entries_seen]), exit_count,
        exit_slots, exit_indices, exit_entry_prices, exit_prices, exit_volumes,
        exit_pnls, exit_exact, exit_reasons,
    )
