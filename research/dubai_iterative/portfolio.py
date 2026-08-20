"""Account-level reconstruction for independently simulated Dubai signals.

Signal replays are intentionally isolated while strategies are searched.  This
module joins their immutable entries and exits back onto one market tape so a
finalist cannot hide risk created by overlapping signals.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import math
from typing import Sequence

import numpy as np
import pandas as pd
from numba import njit

from .engine import ExecutionAssumptions


PRICE_SCALE = 100
FX_SCALE = 100_000
VOLUME_SCALE = 100

ORIENTATION_IDENTITY = 0
ORIENTATION_ACCOUNT_BASE = 1
ORIENTATION_PROFIT_BASE = 2


@dataclass(frozen=True)
class PortfolioAssessment:
    net_eur: Decimal | None
    peak_equity_eur: Decimal | None
    minimum_equity_eur: Decimal | None
    max_drawdown_eur: Decimal | None
    max_concurrent_volume: float
    max_concurrent_signals: int
    timeline_points: int
    blockers: tuple[str, ...]

    @property
    def evidence_complete(self) -> bool:
        return self.net_eur is not None and not self.blockers


@dataclass(frozen=True)
class PortfolioTape:
    times_ns: np.ndarray
    bid_points: np.ndarray
    ask_points: np.ndarray
    fx_bid_points: np.ndarray
    fx_ask_points: np.ndarray
    fx_valid: np.ndarray
    max_conversion_age_ms: int
    max_conversion_interval_ms: int
    valuation_mode: str
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class _PositionSlice:
    signal_id: str
    ticket: str
    opened_ns: int
    closed_ns: int
    direction: int
    entry_points: int
    volume_units: int
    realized_minor: int
    opened_rank: int
    closed_rank: int
    opened_bid_points: int
    opened_ask_points: int
    closed_bid_points: int
    closed_ask_points: int


def reconstruct_portfolio(
    paths: Sequence[object],
    results: Sequence[object],
    *,
    execution: ExecutionAssumptions | None = None,
    market_tick_source: object | None = None,
    conversion_tick_source: object | None = None,
    max_conversion_age_ms: int = 5_000,
    max_conversion_interval_ms: int | None = None,
    portfolio_tape: PortfolioTape | None = None,
) -> PortfolioAssessment:
    """Rebuild one account equity path and fail closed on inconsistent input."""

    execution = execution or ExecutionAssumptions()
    blockers: list[str] = []
    paths_by_signal = _unique_by_signal(paths, "path", blockers)
    results_by_signal = _unique_by_signal(results, "result", blockers)
    for signal_id in sorted(set(paths_by_signal) - set(results_by_signal)):
        blockers.append(f"missing_result:{signal_id}")
    for signal_id in sorted(set(results_by_signal) - set(paths_by_signal)):
        blockers.append(f"missing_path:{signal_id}")
    if blockers:
        return _blocked(blockers)

    contract = _portfolio_contract(tuple(paths_by_signal.values()), blockers)
    if contract is None:
        return _blocked(blockers)
    contract_size, orientation, currency_digits = contract

    slices: list[_PositionSlice] = []
    for signal_id in sorted(paths_by_signal):
        path = paths_by_signal[signal_id]
        result = results_by_signal[signal_id]
        result_blockers = tuple(getattr(result, "blockers", ()) or ())
        blockers.extend(
            f"simulation_blocked:{signal_id}:{item}"
            for item in result_blockers
        )
        if getattr(result, "pnl_eur", None) is None:
            blockers.append(f"missing_result_money:{signal_id}")
            continue
        slices.extend(_result_slices(
            path,
            result,
            execution=execution,
            orientation=orientation,
            contract_size=contract_size,
            currency_digits=currency_digits,
            blockers=blockers,
        ))
    if blockers:
        return _blocked(blockers)

    expected_net = sum(
        (Decimal(str(result.pnl_eur)) for result in results_by_signal.values()),
        start=Decimal("0"),
    ).quantize(Decimal(1).scaleb(-currency_digits))
    if not slices:
        return PortfolioAssessment(
            net_eur=expected_net,
            peak_equity_eur=Decimal("0").quantize(
                Decimal(1).scaleb(-currency_digits)
            ),
            minimum_equity_eur=Decimal("0").quantize(
                Decimal(1).scaleb(-currency_digits)
            ),
            max_drawdown_eur=Decimal("0").quantize(
                Decimal(1).scaleb(-currency_digits)
            ),
            max_concurrent_volume=0.0,
            max_concurrent_signals=0,
            timeline_points=0,
            blockers=(),
        )

    if portfolio_tape is not None:
        if market_tick_source is not None or conversion_tick_source is not None:
            return _blocked(("ambiguous_portfolio_tape_source",))
        if portfolio_tape.blockers:
            return _blocked(portfolio_tape.blockers)
        tape = (
            portfolio_tape.times_ns,
            portfolio_tape.bid_points,
            portfolio_tape.ask_points,
            portfolio_tape.fx_bid_points,
            portfolio_tape.fx_ask_points,
            portfolio_tape.fx_valid,
        )
    elif market_tick_source is not None:
        prepared = build_portfolio_tape(
            tuple(paths_by_signal.values()),
            market_tick_source=market_tick_source,
            conversion_tick_source=conversion_tick_source,
            max_conversion_age_ms=max_conversion_age_ms,
            max_conversion_interval_ms=max_conversion_interval_ms,
        )
        if prepared.blockers:
            return _blocked(prepared.blockers)
        tape = (
            prepared.times_ns,
            prepared.bid_points,
            prepared.ask_points,
            prepared.fx_bid_points,
            prepared.fx_ask_points,
            prepared.fx_valid,
        )
    else:
        tape = _combined_tape(tuple(paths_by_signal.values()), blockers)
    if tape is None:
        return _blocked(blockers)
    times, bid, ask, fx_bid, fx_ask, fx_valid = tape
    arrays = _slice_arrays(slices, times, blockers)
    if arrays is None:
        return _blocked(blockers)
    starts, ends, directions, entries, volumes, realized = arrays

    exit_cost_points = _fixed_scalar(
        execution.exit_slippage + execution.spread_addition,
        PRICE_SCALE,
    )
    kernel = _portfolio_kernel(
        bid,
        ask,
        fx_bid,
        fx_ask,
        fx_valid,
        starts,
        ends,
        directions,
        entries,
        volumes,
        realized,
        contract_size,
        orientation,
        exit_cost_points,
    )
    peak_minor, minimum_minor, drawdown_minor, final_minor, stale_index = kernel
    if stale_index >= 0:
        return _blocked((f"stale_conversion_at_portfolio_tick:{stale_index}",))
    final = _minor_decimal(int(final_minor), currency_digits)
    if final != expected_net:
        return _blocked((
            f"portfolio_net_mismatch:{final}:{expected_net}",
        ))

    max_volume_units = _max_inclusive_exposure(starts, ends, volumes, len(times))
    max_signals = _max_concurrent_signals(slices, times)
    return PortfolioAssessment(
        net_eur=final,
        peak_equity_eur=_minor_decimal(int(peak_minor), currency_digits),
        minimum_equity_eur=_minor_decimal(int(minimum_minor), currency_digits),
        max_drawdown_eur=_minor_decimal(int(drawdown_minor), currency_digits),
        max_concurrent_volume=round(max_volume_units / VOLUME_SCALE, 10),
        max_concurrent_signals=max_signals,
        timeline_points=len(times),
        blockers=(),
    )


def build_portfolio_tape(
    paths: Sequence[object],
    *,
    market_tick_source: object,
    conversion_tick_source: object | None = None,
    max_conversion_age_ms: int = 5_000,
    max_conversion_interval_ms: int | None = None,
) -> PortfolioTape:
    """Load and verify one reusable canonical tape for finalist scenarios."""

    if max_conversion_age_ms <= 0:
        raise ValueError("max_conversion_age_ms must be positive")
    if max_conversion_interval_ms is None:
        max_conversion_interval_ms = max_conversion_age_ms
    if max_conversion_interval_ms < max_conversion_age_ms:
        raise ValueError(
            "max_conversion_interval_ms must be at least max_conversion_age_ms"
        )
    blockers: list[str] = []
    paths = tuple(paths)
    contract = _portfolio_contract(paths, blockers)
    if contract is None:
        return _blocked_tape(
            max_conversion_age_ms,
            max_conversion_interval_ms,
            blockers,
        )
    _contract_size, orientation, _digits = contract
    populated = [path for path in paths if len(path.times_ns)]
    if not populated:
        return _blocked_tape(
            max_conversion_age_ms,
            max_conversion_interval_ms,
            ("empty_portfolio_paths",),
        )
    first_ns = min(int(path.times_ns[0]) for path in populated)
    last_ns = max(int(path.times_ns[-1]) for path in populated)
    days: set[date] = set()
    for path in populated:
        current = _from_ns(int(path.times_ns[0])).date()
        final = _from_ns(int(path.times_ns[-1])).date()
        while current <= final:
            days.add(current)
            current += timedelta(days=1)
    loaded = _load_source_tape(
        days,
        first_ns,
        last_ns,
        market_tick_source,
        conversion_tick_source=conversion_tick_source,
        orientation=orientation,
        max_conversion_age_ms=max_conversion_age_ms,
        max_conversion_interval_ms=max_conversion_interval_ms,
        blockers=blockers,
    )
    if loaded is None:
        return _blocked_tape(
            max_conversion_age_ms,
            max_conversion_interval_ms,
            blockers,
        )
    times, bid, ask, fx_bid, fx_ask, fx_valid = loaded
    arrays = tuple(_read_only(array) for array in (
        times,
        bid,
        ask,
        fx_bid,
        fx_ask,
        fx_valid,
    ))
    times, bid, ask, fx_bid, fx_ask, fx_valid = arrays
    return PortfolioTape(
        times_ns=times,
        bid_points=bid,
        ask_points=ask,
        fx_bid_points=fx_bid,
        fx_ask_points=fx_ask,
        fx_valid=fx_valid,
        max_conversion_age_ms=max_conversion_age_ms,
        max_conversion_interval_ms=max_conversion_interval_ms,
        valuation_mode=(
            "identity_exact"
            if orientation == ORIENTATION_IDENTITY
            else "verified_asof_or_bracketed_conversion"
        ),
        blockers=(),
    )


def _unique_by_signal(rows, kind, blockers):
    indexed = {}
    for row in rows:
        signal_id = str(getattr(row, "signal_id", "") or "")
        if not signal_id:
            blockers.append(f"{kind}_without_signal_id")
        elif signal_id in indexed:
            blockers.append(f"duplicate_{kind}:{signal_id}")
        else:
            indexed[signal_id] = row
    return indexed


def _portfolio_contract(paths, blockers):
    if not paths:
        blockers.append("empty_portfolio_paths")
        return None
    contracts = {
        (
            float(path.contract_size),
            str(path.conversion_orientation),
            int(path.currency_digits),
        )
        for path in paths
    }
    if len(contracts) != 1:
        blockers.append("mixed_portfolio_money_contract")
        return None
    contract_size_value, orientation_name, digits = contracts.pop()
    if not float(contract_size_value).is_integer() or contract_size_value <= 0:
        blockers.append("unsupported_portfolio_contract_size")
        return None
    orientation = {
        "identity": ORIENTATION_IDENTITY,
        "account_base_profit_quote": ORIENTATION_ACCOUNT_BASE,
        "profit_base_account_quote": ORIENTATION_PROFIT_BASE,
    }.get(orientation_name)
    if orientation is None or digits != 2:
        blockers.append("unsupported_portfolio_money_contract")
        return None
    return int(contract_size_value), orientation, digits


def _result_slices(
    path,
    result,
    *,
    execution,
    orientation,
    contract_size,
    currency_digits,
    blockers,
):
    signal_id = str(path.signal_id)
    initial_blocker_count = len(blockers)
    entries = tuple(getattr(result, "entries", ()) or ())
    exits = tuple(getattr(result, "exits", ()) or ())
    exits_by_ticket: dict[str, list[object]] = {}
    for item in exits:
        exits_by_ticket.setdefault(str(item.ticket), []).append(item)
    rows: list[_PositionSlice] = []
    summed_minor = 0
    for entry in entries:
        ticket = str(entry.ticket)
        ticket_exits = exits_by_ticket.pop(ticket, [])
        if not ticket_exits:
            blockers.append(f"open_position:{signal_id}:{ticket}")
            continue
        entry_units = _fixed_scalar(float(entry.volume), VOLUME_SCALE)
        closed_units = 0
        opened_ns = _to_ns(entry.opened_at)
        entry_index = int(entry.tick_index)
        if (
            entry_index < 0
            or entry_index >= len(path.times_ns)
            or int(path.times_ns[entry_index]) != opened_ns
        ):
            blockers.append(f"entry_tick_mismatch:{signal_id}:{ticket}")
            continue
        for item in sorted(ticket_exits, key=lambda row: (_to_ns(row.closed_at), row.volume)):
            closed_ns = _to_ns(item.closed_at)
            volume_units = _fixed_scalar(float(item.volume), VOLUME_SCALE)
            closed_units += volume_units
            if closed_ns < opened_ns:
                blockers.append(f"exit_before_entry:{signal_id}:{ticket}")
                continue
            index = int(item.tick_index)
            if (
                index < 0
                or index >= len(path.times_ns)
                or int(path.times_ns[index]) != closed_ns
            ):
                blockers.append(f"exit_tick_mismatch:{signal_id}:{ticket}")
                continue
            raw_exit = float(path.bid[index] if path.direction == "BUY" else path.ask[index])
            cost = execution.exit_slippage + execution.spread_addition
            expected_exit = raw_exit - cost if path.direction == "BUY" else raw_exit + cost
            if _fixed_scalar(expected_exit, PRICE_SCALE) != _fixed_scalar(
                float(item.exit_price), PRICE_SCALE
            ):
                blockers.append(f"execution_scenario_mismatch:{signal_id}:{ticket}")
                continue
            pnl = getattr(item, "pnl_eur", None)
            if pnl is None:
                blockers.append(f"missing_exit_money:{signal_id}:{ticket}")
                continue
            pnl_minor = _amount_minor(Decimal(str(pnl)), currency_digits)
            expected_minor, exact = _money_minor_fixed_python(
                1 if path.direction == "BUY" else -1,
                orientation,
                contract_size,
                _fixed_scalar(float(item.entry_price), PRICE_SCALE),
                _fixed_scalar(float(item.exit_price), PRICE_SCALE),
                volume_units,
                _fixed_scalar(float(path.fx_bid[index]), FX_SCALE),
                _fixed_scalar(float(path.fx_ask[index]), FX_SCALE),
                bool(path.fx_valid[index]),
            )
            if not exact:
                blockers.append(f"stale_conversion_at_exit:{signal_id}:{ticket}")
            elif expected_minor != pnl_minor:
                blockers.append(f"exit_money_mismatch:{signal_id}:{ticket}")
            rows.append(_PositionSlice(
                signal_id=signal_id,
                ticket=ticket,
                opened_ns=opened_ns,
                closed_ns=closed_ns,
                direction=1 if path.direction == "BUY" else -1,
                entry_points=_fixed_scalar(float(entry.entry_price), PRICE_SCALE),
                volume_units=volume_units,
                realized_minor=pnl_minor,
                opened_rank=_timestamp_rank(path.times_ns, entry_index),
                closed_rank=_timestamp_rank(path.times_ns, index),
                opened_bid_points=_fixed_scalar(
                    float(path.bid[entry_index]), PRICE_SCALE
                ),
                opened_ask_points=_fixed_scalar(
                    float(path.ask[entry_index]), PRICE_SCALE
                ),
                closed_bid_points=_fixed_scalar(float(path.bid[index]), PRICE_SCALE),
                closed_ask_points=_fixed_scalar(float(path.ask[index]), PRICE_SCALE),
            ))
            summed_minor += pnl_minor
        if closed_units != entry_units:
            blockers.append(f"exit_volume_mismatch:{signal_id}:{ticket}")
    for ticket in sorted(exits_by_ticket):
        blockers.append(f"exit_without_entry:{signal_id}:{ticket}")
    expected_result = _amount_minor(Decimal(str(result.pnl_eur)), currency_digits)
    if len(blockers) == initial_blocker_count and summed_minor != expected_result:
        blockers.append(f"result_money_mismatch:{signal_id}")
    return rows


def _combined_tape(paths, blockers):
    times = np.concatenate([np.asarray(path.times_ns, dtype=np.int64) for path in paths])
    bid = np.concatenate([np.asarray(path.bid, dtype=float) for path in paths])
    ask = np.concatenate([np.asarray(path.ask, dtype=float) for path in paths])
    fx_bid = np.concatenate([np.asarray(path.fx_bid, dtype=float) for path in paths])
    fx_ask = np.concatenate([np.asarray(path.fx_ask, dtype=float) for path in paths])
    fx_valid = np.concatenate([np.asarray(path.fx_valid, dtype=bool) for path in paths])
    order = np.argsort(times, kind="stable")
    times = times[order]
    bid = bid[order]
    ask = ask[order]
    fx_bid = fx_bid[order]
    fx_ask = fx_ask[order]
    fx_valid = fx_valid[order]
    duplicate = times[1:] == times[:-1]
    if duplicate.any():
        disagreement = duplicate & (
            (np.abs(bid[1:] - bid[:-1]) > 1e-9)
            | (np.abs(ask[1:] - ask[:-1]) > 1e-9)
            | (fx_valid[1:] != fx_valid[:-1])
            | (
                fx_valid[1:]
                & (
                    (np.abs(fx_bid[1:] - fx_bid[:-1]) > 1e-9)
                    | (np.abs(fx_ask[1:] - fx_ask[:-1]) > 1e-9)
                )
            )
        )
        if disagreement.any():
            blockers.append("inconsistent_portfolio_market_tape")
            return None
    keep = np.concatenate((np.asarray([True]), times[1:] != times[:-1]))
    return (
        np.ascontiguousarray(times[keep], dtype=np.int64),
        _fixed_array(bid[keep], PRICE_SCALE),
        _fixed_array(ask[keep], PRICE_SCALE),
        _fixed_array(fx_bid[keep], FX_SCALE),
        _fixed_array(fx_ask[keep], FX_SCALE),
        np.ascontiguousarray(fx_valid[keep], dtype=np.bool_),
    )


def _source_tape(
    slices,
    market_source,
    *,
    conversion_tick_source,
    orientation,
    max_conversion_age_ms,
    max_conversion_interval_ms,
    blockers,
):
    days: set[date] = set()
    for item in slices:
        current = _from_ns(item.opened_ns).date()
        final = _from_ns(item.closed_ns).date()
        while current <= final:
            days.add(current)
            current += timedelta(days=1)
    first_ns = min(item.opened_ns for item in slices)
    last_ns = max(item.closed_ns for item in slices)
    return _load_source_tape(
        days,
        first_ns,
        last_ns,
        market_source,
        conversion_tick_source=conversion_tick_source,
        orientation=orientation,
        max_conversion_age_ms=max_conversion_age_ms,
        max_conversion_interval_ms=max_conversion_interval_ms,
        blockers=blockers,
    )


def _load_source_tape(
    days,
    first_ns,
    last_ns,
    market_source,
    *,
    conversion_tick_source,
    orientation,
    max_conversion_age_ms,
    max_conversion_interval_ms,
    blockers,
):
    market_frames = []
    for day in sorted(days):
        frame, _evidence, errors = market_source.load_day(day)
        if errors:
            blockers.extend(f"portfolio_market:{error}" for error in errors)
            continue
        normalized = _normalize_source_frame(frame)
        if normalized is None:
            blockers.append(f"invalid_portfolio_market_tape:{day}")
            continue
        market_frames.append(normalized)
    if blockers or not market_frames:
        if not blockers:
            blockers.append("empty_portfolio_market_tape")
        return None
    market = pd.concat(market_frames, ignore_index=True).sort_values(
        "time_utc", kind="stable"
    )
    market_ns = market["time_utc"].array.as_unit("ns").asi8
    selected = (market_ns >= first_ns) & (market_ns <= last_ns)
    market = market.loc[selected].reset_index(drop=True)
    market_ns = market["time_utc"].array.as_unit("ns").asi8.copy()
    if not len(market):
        blockers.append("empty_portfolio_market_interval")
        return None

    if orientation == ORIENTATION_IDENTITY:
        fx_bid = np.ones(len(market), dtype=float)
        fx_ask = np.ones(len(market), dtype=float)
        fx_valid = np.ones(len(market), dtype=bool)
    else:
        if conversion_tick_source is None:
            blockers.append("missing_portfolio_conversion_source")
            return None
        conversion_frames = []
        for day in sorted(days):
            frame, _evidence, errors = conversion_tick_source.load_day(day)
            if errors:
                blockers.extend(f"portfolio_conversion:{error}" for error in errors)
                continue
            normalized = _normalize_source_frame(frame)
            if normalized is None:
                blockers.append(f"invalid_portfolio_conversion_tape:{day}")
                continue
            conversion_frames.append(normalized)
        if blockers or not conversion_frames:
            if not blockers:
                blockers.append("empty_portfolio_conversion_tape")
            return None
        conversion = pd.concat(conversion_frames, ignore_index=True).sort_values(
            "time_utc", kind="stable"
        )
        conversion_ns = conversion["time_utc"].array.as_unit("ns").asi8
        indices = np.searchsorted(conversion_ns, market_ns, side="right") - 1
        prior = indices >= 0
        safe = np.maximum(indices, 0)
        ages_ms = np.full(len(market_ns), np.inf, dtype=float)
        ages_ms[prior] = (
            market_ns[prior] - conversion_ns[safe[prior]]
        ) / 1_000_000
        next_indices = indices + 1
        has_next = prior & (next_indices < len(conversion_ns))
        safe_next = np.minimum(next_indices, len(conversion_ns) - 1)
        intervals_ms = np.full(len(market_ns), np.inf, dtype=float)
        intervals_ms[has_next] = (
            conversion_ns[safe_next[has_next]]
            - conversion_ns[safe[has_next]]
        ) / 1_000_000
        bracketed = (
            has_next
            & (intervals_ms > 0)
            & (intervals_ms <= max_conversion_interval_ms)
            & (market_ns < conversion_ns[safe_next])
        )
        fx_valid = prior & (
            (ages_ms <= max_conversion_age_ms) | bracketed
        )
        fx_bid = np.full(len(market_ns), np.nan, dtype=float)
        fx_ask = np.full(len(market_ns), np.nan, dtype=float)
        source_bid = conversion["bid"].to_numpy(dtype=float, copy=False)
        source_ask = conversion["ask"].to_numpy(dtype=float, copy=False)
        fx_bid[prior] = source_bid[safe[prior]]
        fx_ask[prior] = source_ask[safe[prior]]

    return (
        np.ascontiguousarray(market_ns, dtype=np.int64),
        _fixed_array(market["bid"].to_numpy(dtype=float), PRICE_SCALE),
        _fixed_array(market["ask"].to_numpy(dtype=float), PRICE_SCALE),
        _fixed_array(fx_bid, FX_SCALE, allow_nan=True),
        _fixed_array(fx_ask, FX_SCALE, allow_nan=True),
        np.ascontiguousarray(fx_valid, dtype=np.bool_),
    )


def _normalize_source_frame(frame):
    if frame is None or frame.empty or not {"time_utc", "bid", "ask"}.issubset(frame):
        return None
    result = frame.loc[:, ["time_utc", "bid", "ask"]].copy()
    result["time_utc"] = pd.to_datetime(result["time_utc"], utc=True, errors="coerce")
    result["bid"] = pd.to_numeric(result["bid"], errors="coerce")
    result["ask"] = pd.to_numeric(result["ask"], errors="coerce")
    values = result[["bid", "ask"]].to_numpy(dtype=float)
    if (
        result["time_utc"].isna().any()
        or not np.isfinite(values).all()
        or (values <= 0).any()
        or (result["ask"] < result["bid"]).any()
        or not result["time_utc"].is_monotonic_increasing
    ):
        return None
    return result.reset_index(drop=True)


def _slice_arrays(slices, times, blockers):
    starts = []
    ends = []
    for item in slices:
        start = _locate_tick(
            times,
            item.opened_ns,
            item.opened_rank,
        )
        end = _locate_tick(
            times,
            item.closed_ns,
            item.closed_rank,
        )
        starts.append(start)
        ends.append(end)
        if start is None or end is None:
            blockers.append(f"portfolio_tick_missing:{item.signal_id}:{item.ticket}")
    if blockers:
        return None
    return tuple(np.ascontiguousarray(values, dtype=dtype) for values, dtype in (
        (starts, np.int64),
        (ends, np.int64),
        ([item.direction for item in slices], np.int8),
        ([item.entry_points for item in slices], np.int64),
        ([item.volume_units for item in slices], np.int64),
        ([item.realized_minor for item in slices], np.int64),
    ))


def _locate_tick(times, timestamp_ns, rank):
    first = int(np.searchsorted(times, timestamp_ns, side="left"))
    last = int(np.searchsorted(times, timestamp_ns, side="right"))
    index = first + int(rank)
    return index if first < last and index < last else None


def _timestamp_rank(times, index):
    timestamp = int(times[index])
    first = int(np.searchsorted(times, timestamp, side="left"))
    return int(index) - first


@njit(cache=True, nogil=True)
def _portfolio_kernel(
    bid,
    ask,
    fx_bid,
    fx_ask,
    fx_valid,
    starts,
    ends,
    directions,
    entries,
    volumes,
    realized_values,
    contract_size,
    orientation,
    exit_cost_points,
):
    count = len(starts)
    active = np.zeros(count, dtype=np.bool_)
    starts_order = np.argsort(starts)
    ends_order = np.argsort(ends)
    start_cursor = 0
    end_cursor = 0
    realized = 0
    peak = 0
    minimum = 0
    drawdown = 0
    stale_index = -1
    for index in range(len(bid)):
        while end_cursor < count and ends[ends_order[end_cursor]] == index:
            slot = ends_order[end_cursor]
            active[slot] = False
            realized += realized_values[slot]
            end_cursor += 1
        while start_cursor < count and starts[starts_order[start_cursor]] == index:
            slot = starts_order[start_cursor]
            if ends[slot] > index:
                active[slot] = True
            start_cursor += 1
        floating = 0
        for slot in range(count):
            if not active[slot]:
                continue
            direction = directions[slot]
            raw_exit = bid[index] if direction == 1 else ask[index]
            money_exit = raw_exit - direction * exit_cost_points
            value, exact = _money_minor_fixed(
                direction,
                orientation,
                contract_size,
                entries[slot],
                money_exit,
                volumes[slot],
                fx_bid[index],
                fx_ask[index],
                fx_valid[index],
            )
            if not exact:
                stale_index = index
                return peak, minimum, drawdown, realized, stale_index
            floating += value
        equity = realized + floating
        if equity > peak:
            peak = equity
        if equity < minimum:
            minimum = equity
        current_drawdown = peak - equity
        if current_drawdown > drawdown:
            drawdown = current_drawdown
    return peak, minimum, drawdown, realized, stale_index


@njit(cache=True, nogil=True)
def _money_minor_fixed(
    direction,
    orientation,
    contract_size,
    entry,
    exit_price,
    volume,
    fx_bid,
    fx_ask,
    fx_valid,
):
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
def _round_ratio(num, den):
    if den <= 0:
        return 0
    absolute = num if num >= 0 else -num
    rounded = (absolute * 2 + den) // (2 * den)
    return rounded if num >= 0 else -rounded


def _money_minor_fixed_python(*args):
    return tuple(int(value) if index == 0 else bool(value) for index, value in enumerate(
        _money_minor_fixed(*args)
    ))


def _max_inclusive_exposure(starts, ends, volumes, length):
    changes = np.zeros(length + 1, dtype=np.int64)
    for start, end, volume in zip(starts, ends, volumes, strict=True):
        changes[int(start)] += int(volume)
        if int(end) + 1 < len(changes):
            changes[int(end) + 1] -= int(volume)
    return int(np.max(np.cumsum(changes[:-1]), initial=0))


def _max_concurrent_signals(slices, times):
    grouped: dict[str, list[tuple[int, int]]] = {}
    for item in slices:
        start = int(np.searchsorted(times, item.opened_ns, side="left"))
        end = int(np.searchsorted(times, item.closed_ns, side="left"))
        grouped.setdefault(item.signal_id, []).append((start, end))
    changes = np.zeros(len(times) + 1, dtype=np.int64)
    for intervals in grouped.values():
        merged: list[list[int]] = []
        for start, end in sorted(intervals):
            if merged and start <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        for start, end in merged:
            changes[start] += 1
            if end + 1 < len(changes):
                changes[end + 1] -= 1
    return int(np.max(np.cumsum(changes[:-1]), initial=0))


def _fixed_array(values, scale, *, allow_nan=False):
    return np.ascontiguousarray(
        [
            0 if allow_nan and not math.isfinite(float(value))
            else _fixed_scalar(value, scale)
            for value in values
        ],
        dtype=np.int64,
    )


def _fixed_scalar(value, scale):
    number = Decimal(str(float(value))) * Decimal(scale)
    return int(number.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _read_only(array):
    result = np.ascontiguousarray(array)
    result.flags.writeable = False
    return result


def _amount_minor(value, digits):
    return int(
        (value * (Decimal(10) ** digits)).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )


def _minor_decimal(value, digits):
    quantum = Decimal(1).scaleb(-digits)
    return Decimal(value).scaleb(-digits).quantize(quantum)


def _to_ns(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("portfolio timestamps must be timezone-aware datetimes")
    utc = value.astimezone(timezone.utc)
    delta = utc - datetime(1970, 1, 1, tzinfo=timezone.utc)
    microseconds = (
        (delta.days * 86_400 + delta.seconds) * 1_000_000
        + delta.microseconds
    )
    return microseconds * 1_000


def _from_ns(value):
    return datetime.fromtimestamp(value / 1_000_000_000, tz=timezone.utc)


def _blocked(blockers):
    return PortfolioAssessment(
        net_eur=None,
        peak_equity_eur=None,
        minimum_equity_eur=None,
        max_drawdown_eur=None,
        max_concurrent_volume=0.0,
        max_concurrent_signals=0,
        timeline_points=0,
        blockers=tuple(dict.fromkeys(blockers)),
    )


def _blocked_tape(
    max_conversion_age_ms,
    max_conversion_interval_ms,
    blockers,
):
    empty_int = _read_only(np.asarray([], dtype=np.int64))
    empty_bool = _read_only(np.asarray([], dtype=bool))
    return PortfolioTape(
        times_ns=empty_int,
        bid_points=empty_int,
        ask_points=empty_int,
        fx_bid_points=empty_int,
        fx_ask_points=empty_int,
        fx_valid=empty_bool,
        max_conversion_age_ms=max_conversion_age_ms,
        max_conversion_interval_ms=max_conversion_interval_ms,
        valuation_mode="blocked",
        blockers=tuple(dict.fromkeys(blockers)),
    )
