"""Common-tick account path diagnostic for observed and hypothetical events.

The observed side is reconstructed from deals, not sampled MT5 account equity.
Unknown quote marks prevent a complete monetary drawdown claim.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
import hashlib

import numpy as np
from numba import njit

from research.causal_comparison import SequenceEvent
from research.causal_replay import time_ns
from research.risk_trajectory import RiskSpec
from research.dubai_iterative.portfolio import _money_minor_fixed


MAX_MARKS = 5_000_000
MAX_SLICES = 1_000
MAX_EVENTS = 4_000


def _fixed(value, scale):
    amount = Decimal(str(value)) * scale
    if not amount.is_finite() or amount != amount.to_integral_value():
        raise ValueError("account path input exceeds fixed-point precision")
    return int(amount)


def _quotes(tape, scale):
    if len(tape) != 3:
        raise ValueError("market and FX tapes require time, Bid and Ask")
    times = np.asarray(tape[0], dtype=np.int64)
    bid, ask = (np.asarray(tape[index], dtype=float) for index in (1, 2))
    if (not len(times) or len(times) != len(bid) or len(times) != len(ask)
            or np.any(np.diff(times) < 0) or not np.isfinite(bid).all()
            or not np.isfinite(ask).all() or np.any(bid <= 0)
            or np.any(ask < bid)):
        raise ValueError("invalid ordered account quote tape")
    raw_bid, raw_ask = bid * scale, ask * scale
    fixed_bid, fixed_ask = np.rint(raw_bid), np.rint(raw_ask)
    if (np.max(np.abs(raw_bid - fixed_bid)) > 1e-5
            or np.max(np.abs(raw_ask - fixed_ask)) > 1e-5):
        raise ValueError("quote tape exceeds fixed-point precision")
    return times, fixed_bid.astype(np.int64), fixed_ask.astype(np.int64)


def _slices(events_by_signal):
    if not isinstance(events_by_signal, dict) or not events_by_signal:
        raise ValueError("account cohort requires explicit signals")
    positions, volume_events, money_events, times = [], [], [], []
    for signal_id, events in events_by_signal.items():
        if not isinstance(signal_id, str) or not signal_id:
            raise ValueError("account event signal identity missing")
        if len(events) > MAX_EVENTS:
            raise ValueError("account event budget exceeded")
        grouped = defaultdict(list)
        for event in events:
            if not isinstance(event, SequenceEvent):
                raise ValueError("typed account events required")
            grouped[event.slot].append(event)
            at = time_ns(event.at)
            times.append(at)
            money_events.append((at, _fixed(event.money, 100)))
        for slot, rows in grouped.items():
            entries = [row for row in rows if row.kind == "entry"]
            exits = [row for row in rows if row.kind == "exit"]
            if len(entries) != 1 or not exits:
                raise ValueError(f"account position incomplete: {signal_id}:{slot}")
            entry = entries[0]
            if (sum((row.volume for row in exits), Decimal(0)) != entry.volume
                    or any(row.at < entry.at or row.direction != entry.direction for row in exits)):
                raise ValueError(f"account position chronology or volume invalid: {signal_id}:{slot}")
            direction = 1 if entry.direction == "BUY" else -1
            position_index = len(positions)
            positions.append((time_ns(entry.at), max(time_ns(row.at) for row in exits),
                              direction, _fixed(entry.price, 100)))
            volume_events.append((time_ns(entry.at), position_index,
                                  _fixed(entry.volume, 100)))
            for exit_row in exits:
                volume_events.append((time_ns(exit_row.at), position_index,
                                      -_fixed(exit_row.volume, 100)))
    if len(positions) > MAX_SLICES or len(money_events) > MAX_EVENTS:
        raise ValueError("account position or event budget exceeded")
    return positions, volume_events, money_events, times


def _grid(market, conversion, event_times, spec, retrospective_fx_interval_ms):
    market_times, market_bid, market_ask = _quotes(market, 100)
    fx_times, fx_bid, fx_ask = _quotes(conversion, 100_000)
    if not event_times:
        raise ValueError("account comparison has no event timestamps")
    lower, upper = min(event_times), max(event_times)
    first = int(np.searchsorted(market_times, lower, side="left"))
    last = int(np.searchsorted(market_times, upper, side="right"))
    selected = market_times[first:last]
    unique_events = np.unique(np.asarray(event_times, dtype=np.int64))
    indices = np.searchsorted(selected, unique_events, side="left")
    present = (indices < len(selected)) & (selected[np.minimum(indices, max(0, len(selected) - 1))]
                                            == unique_events) if len(selected) else np.zeros(len(unique_events), dtype=bool)
    boundaries = unique_events[~present]
    merged = np.concatenate((selected, boundaries))
    order = np.argsort(merged, kind="stable")
    times = merged[order]
    if not len(times) or len(times) > MAX_MARKS:
        raise ValueError("account common quote-grid budget exceeded")
    prior_market = np.searchsorted(market_times, times, side="right") - 1
    # Source ticks retain their own Bid/Ask, including duplicate timestamps.
    selected_bid = market_bid[first:last]
    selected_ask = market_ask[first:last]
    prior_boundary = np.searchsorted(market_times, boundaries, side="right") - 1
    safe_boundary = np.maximum(prior_boundary, 0)
    bids = np.concatenate((selected_bid, market_bid[safe_boundary]))[order]
    asks = np.concatenate((selected_ask, market_ask[safe_boundary]))[order]
    quote_at = np.concatenate((selected, market_times[safe_boundary]))[order]
    market_valid = ((prior_market >= 0) & ((times - quote_at) <= spec.max_market_gap_ms * 1_000_000))
    market_valid &= quote_at <= times
    prior_fx = np.searchsorted(fx_times, times, side="right") - 1
    safe_fx = np.maximum(prior_fx, 0)
    fx_valid = ((prior_fx >= 0)
                & ((times - fx_times[safe_fx]) <= spec.max_fx_age_ms * 1_000_000))
    bracketed = np.zeros(len(times), dtype=bool)
    if retrospective_fx_interval_ms is not None:
        next_index = prior_fx + 1
        has_next = (prior_fx >= 0) & (next_index < len(fx_times))
        safe_next = np.minimum(next_index, len(fx_times) - 1)
        bracketed = (has_next & ~fx_valid & (times < fx_times[safe_next])
                     & ((fx_times[safe_next] - fx_times[safe_fx])
                        <= retrospective_fx_interval_ms * 1_000_000))
        fx_valid |= bracketed
    return (times, bids, asks, fx_bid[safe_fx], fx_ask[safe_fx],
            market_valid, fx_valid, market_times, bracketed)


@njit(cache=True, nogil=True)
def _mark_kernel(bid, ask, fx_bid, fx_ask, market_valid, fx_valid,
                 event_indices, event_positions, event_deltas,
                 directions, entries, realized_delta):
    count, length = len(directions), len(bid)
    remaining = np.zeros(count, dtype=np.int64)
    event_cursor = realized = 0
    equity = np.zeros(length, dtype=np.int64)
    floating = np.zeros(length, dtype=np.int64)
    booked = np.zeros(length, dtype=np.int64)
    long_volume = np.zeros(length, dtype=np.int64)
    short_volume = np.zeros(length, dtype=np.int64)
    known = np.ones(length, dtype=np.bool_)
    for index in range(length):
        while event_cursor < len(event_indices) and event_indices[event_cursor] == index:
            remaining[event_positions[event_cursor]] += event_deltas[event_cursor]
            event_cursor += 1
        realized += realized_delta[index]
        value, longs, shorts, valid = 0, 0, 0, True
        for slot in range(count):
            volume = remaining[slot]
            if not volume:
                continue
            direction = directions[slot]
            if direction == 1:
                longs += volume
            else:
                shorts += volume
            if not market_valid[index]:
                valid = False
                continue
            exit_price = bid[index] if direction == 1 else ask[index]
            if exit_price == entries[slot]:
                continue
            item, exact = _money_minor_fixed(
                direction, 1, 100, entries[slot], exit_price, volume,
                fx_bid[index], fx_ask[index], fx_valid[index])
            if not exact:
                valid = False
            else:
                value += item
        booked[index] = realized
        long_volume[index] = longs
        short_volume[index] = shorts
        known[index] = valid
        if valid:
            floating[index] = value
            equity[index] = realized + value
    return equity, floating, booked, long_volume, short_volume, known


def _mark(events_by_signal, grid, spec):
    positions, volume_events, money_events, _ = _slices(events_by_signal)
    times, bid, ask, fx_bid, fx_ask, market_valid, fx_valid, market_times, _ = grid
    indexed = sorted((int(np.searchsorted(times, at, side="left")), slot, delta)
                     for at, slot, delta in volume_events)
    if any(index >= len(times) for index, _, _ in indexed):
        raise ValueError("account event not on common quote grid")
    event_indices = np.asarray([row[0] for row in indexed], dtype=np.int64)
    event_positions = np.asarray([row[1] for row in indexed], dtype=np.int64)
    event_deltas = np.asarray([row[2] for row in indexed], dtype=np.int64)
    directions = np.asarray([row[2] for row in positions], dtype=np.int64)
    entries = np.asarray([row[3] for row in positions], dtype=np.int64)
    realized_delta = np.zeros(len(times), dtype=np.int64)
    for at, minor in money_events:
        realized_delta[np.searchsorted(times, at, side="left")] += minor
    arrays = _mark_kernel(bid, ask, fx_bid, fx_ask, market_valid, fx_valid,
                          event_indices, event_positions, event_deltas,
                          directions, entries, realized_delta)
    gap = False
    gaps = np.flatnonzero(np.diff(market_times) > spec.max_market_gap_ms * 1_000_000)
    for left_index in gaps:
        left, right = int(market_times[left_index]), int(market_times[left_index + 1])
        if any(start < right and end > left for start, end, *_ in positions):
            gap = True
            break
    return arrays, gap


def _summary(times, arrays, market_gap):
    equity, floating, booked, longs, shorts, known = arrays
    digestor = hashlib.sha256()
    for array in (times, equity, floating, booked, longs, shorts, known):
        digestor.update(np.ascontiguousarray(array).tobytes())
    values = equity[known]
    peaks = np.maximum.accumulate(np.maximum(values, 0)) if len(values) else values
    maximum_dd = int(np.max(peaks - values)) if len(values) else 0
    blockers = []
    if not known.all():
        blockers.append("unknown_market_or_conversion_marks")
    if market_gap:
        blockers.append("market_quote_gap_while_position_open")
    money = lambda minor: str(Decimal(int(minor)).scaleb(-2).quantize(Decimal("0.01")))
    return {"sample_count": len(times), "known_sample_count": int(known.sum()),
            "sample_stream_sha256": digestor.hexdigest(),
            "booked_net_eur": money(booked[-1]),
            "max_gross_volume": float(np.max(longs + shorts) / 100),
            "max_drawdown_eur": None if blockers else money(maximum_dd),
            "known_only_drawdown_eur": money(maximum_dd),
            "blockers": blockers,
            "scope": "modeled_account_from_events_and_retained_ticks_not_mt5_equity"}


def compare_account_paths(observed, simulated, market, conversion, *, spec,
                          retrospective_fx_interval_ms=None):
    if (not isinstance(spec, RiskSpec) or spec.currency != "EUR" or spec.currency_digits != 2
            or spec.contract_size != 100 or spec.orientation != "account_base_profit_quote"):
        raise ValueError("unsupported account path money contract")
    if (retrospective_fx_interval_ms is not None
            and (type(retrospective_fx_interval_ms) is not int or retrospective_fx_interval_ms <= 0)):
        raise ValueError("invalid retrospective FX interval")
    _, _, _, left_times = _slices(observed)
    _, _, _, right_times = _slices(simulated)
    if not left_times and not right_times:
        flat = {"sample_count": 0, "known_sample_count": 0,
                "sample_stream_sha256": hashlib.sha256(b"").hexdigest(),
                "booked_net_eur": "0.00", "max_gross_volume": 0.0,
                "max_drawdown_eur": "0.00", "known_only_drawdown_eur": "0.00",
                "blockers": [],
                "scope": "modeled_account_from_events_and_retained_ticks_not_mt5_equity"}
        return {"status": "exact_sampled_path_only", "observed": flat,
                "simulated": flat.copy(), "common_marks": 0,
                "unknown_money_marks": 0, "exposure_difference_marks": 0,
                "booked_difference_marks": 0,
                "money_difference_marks": 0, "first_divergence": None,
                "max_abs_equity_difference_eur": "0.00", "blockers": [],
                "fx_coverage_mode": "strict_causal" if retrospective_fx_interval_ms is None
                                    else "retrospective_bracketed",
                "retrospective_fx_bracketed_open_marks": 0,
                "full_live_parity_verified": False,
                "observed_account_equity_compared": False}
    grid = _grid(market, conversion, left_times + right_times, spec,
                 retrospective_fx_interval_ms)
    actual, actual_gap = _mark(observed, grid, spec)
    virtual, virtual_gap = _mark(simulated, grid, spec)
    times = grid[0]
    both_known = actual[5] & virtual[5]
    exposure_difference = ((actual[3] != virtual[3]) | (actual[4] != virtual[4]))
    booked_difference = actual[2] != virtual[2]
    money_difference = booked_difference | (both_known & ((actual[0] != virtual[0])
                                                     | (actual[1] != virtual[1])))
    any_difference = exposure_difference | money_difference
    first = int(np.flatnonzero(any_difference)[0]) if any_difference.any() else None
    unknown = int((~both_known).sum())
    actual_summary = _summary(times, actual, actual_gap)
    virtual_summary = _summary(times, virtual, virtual_gap)
    blockers = sorted(set(actual_summary["blockers"] + virtual_summary["blockers"]))
    maximum = int(np.max(np.abs(virtual[0][both_known] - actual[0][both_known]))) if both_known.any() else None
    bracketed_open = grid[8] & ((actual[3] + actual[4] > 0)
                                | (virtual[3] + virtual[4] > 0))
    money = lambda minor: str(Decimal(int(minor)).scaleb(-2).quantize(Decimal("0.01")))
    return {"status": "blocked" if blockers else "mismatch" if any_difference.any() else "exact_sampled_path_only",
            "observed": actual_summary,
            "simulated": virtual_summary,
            "common_marks": len(times), "unknown_money_marks": unknown,
            "exposure_difference_marks": int(exposure_difference.sum()),
            "booked_difference_marks": int(booked_difference.sum()),
            "money_difference_marks": int(money_difference.sum()),
            "first_divergence": None if first is None else {"at_ns": int(times[first]),
                "observed_equity_eur": money(actual[0][first]) if actual[5][first] else None,
                "simulated_equity_eur": money(virtual[0][first]) if virtual[5][first] else None,
                "observed_booked_eur": money(actual[2][first]),
                "simulated_booked_eur": money(virtual[2][first]),
                "observed_gross_volume": float((actual[3][first] + actual[4][first]) / 100),
                "simulated_gross_volume": float((virtual[3][first] + virtual[4][first]) / 100)},
            "max_abs_equity_difference_eur": None if maximum is None else money(maximum),
            "blockers": blockers,
            "fx_coverage_mode": "strict_causal" if retrospective_fx_interval_ms is None
                                else "retrospective_bracketed",
            "retrospective_fx_bracketed_open_marks": int(bracketed_open.sum()),
            "full_live_parity_verified": False,
            "observed_account_equity_compared": False}
