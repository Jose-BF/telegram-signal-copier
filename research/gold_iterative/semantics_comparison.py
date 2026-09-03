"""Deterministic summaries for explicitly named Gold 555 semantics."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from statistics import median
from typing import Any, Mapping, Sequence


_CENT = Decimal("0.01")


def compare_result_vectors(
    expected: Sequence[object],
    actual: Sequence[object],
) -> tuple[str, ...]:
    """Require one byte-semantic dataclass result per identical signal id."""

    expected_by_id = {str(row.signal_id): row for row in expected}
    actual_by_id = {str(row.signal_id): row for row in actual}
    mismatches: list[str] = []
    for signal_id in sorted(expected_by_id):
        other = actual_by_id.get(signal_id)
        if other is None:
            mismatches.append(f"result_missing:{signal_id}")
        elif expected_by_id[signal_id] != other:
            mismatches.append(f"result_mismatch:{signal_id}")
    for signal_id in sorted(set(actual_by_id).difference(expected_by_id)):
        mismatches.append(f"unexpected_result:{signal_id}")
    return tuple(mismatches)


def summarize_results(
    paths: Sequence[object],
    results: Sequence[object],
    *,
    oracle_status: str,
) -> dict[str, Any]:
    """Summarize a complete result vector without inventing uncertainty ranges."""

    blockers: list[str] = []
    path_ids = [str(path.signal_id) for path in paths]
    if len(path_ids) != len(set(path_ids)):
        blockers.append("duplicate_signal_path")
    result_by_id: dict[str, object] = {}
    for result in results:
        signal_id = str(result.signal_id)
        if signal_id in result_by_id:
            blockers.append(f"duplicate_result:{signal_id}")
            continue
        result_by_id[signal_id] = result
    missing = [signal_id for signal_id in path_ids if signal_id not in result_by_id]
    extra = sorted(set(result_by_id).difference(path_ids))
    blockers.extend(f"missing_result:{signal_id}" for signal_id in missing)
    blockers.extend(f"unexpected_result:{signal_id}" for signal_id in extra)

    daily_values: dict[str, list[Decimal]] = defaultdict(list)
    daily_counts: dict[str, int] = defaultdict(int)
    rows: list[dict[str, Any]] = []
    pnls: list[Decimal] = []
    holding_times_ms: list[int] = []
    filled = wins = losses = flat = 0
    for path in paths:
        signal_id = str(path.signal_id)
        day = str(path.day)
        result = result_by_id.get(signal_id)
        daily_counts[day] += 1
        if result is None:
            rows.append({
                "signal_id": signal_id,
                "day": day,
                "net_eur": None,
                "entry_count": None,
                "exit_reason": None,
                "blockers": ["missing_result"],
            })
            continue
        result_blockers = [str(value) for value in result.blockers if str(value)]
        blockers.extend(result_blockers)
        money = _money(result.pnl_eur)
        if money is None:
            blockers.append(f"money_missing:{signal_id}")
        else:
            pnls.append(money)
            daily_values[day].append(money)
            wins += int(money > 0)
            losses += int(money < 0)
            flat += int(money == 0)
        entry_count = len(result.entries)
        entry_times = [item.opened_at for item in result.entries]
        exit_times = [item.closed_at for item in result.exits]
        first_entry_at = min(entry_times) if entry_times else None
        first_exit_at = min(exit_times) if exit_times else None
        last_exit_at = max(exit_times) if exit_times else None
        holding_ms = (
            round((last_exit_at - first_entry_at).total_seconds() * 1_000)
            if first_entry_at is not None and last_exit_at is not None
            else None
        )
        if holding_ms is not None:
            holding_times_ms.append(holding_ms)
        filled += int(entry_count > 0)
        rows.append({
            "signal_id": signal_id,
            "day": day,
            "net_eur": _text(money),
            "entry_count": entry_count,
            "first_entry_at": (
                first_entry_at.isoformat() if first_entry_at is not None else None
            ),
            "first_entry_price": (
                _number_text(result.entries[0].entry_price)
                if result.entries
                else None
            ),
            "first_exit_at": (
                first_exit_at.isoformat() if first_exit_at is not None else None
            ),
            "last_exit_at": (
                last_exit_at.isoformat() if last_exit_at is not None else None
            ),
            "holding_ms": holding_ms,
            "exit_reason": result.exit_reason,
            "max_favourable_eur": _text(_money(result.max_favourable_eur)),
            "max_adverse_eur": _text(_money(result.max_adverse_eur)),
            "max_floating_drawdown_eur": _text(
                _money(result.max_floating_drawdown_eur)
            ),
            "blockers": result_blockers,
        })
    if oracle_status != "pass":
        blockers.append("oracle_not_passed")
    blockers = list(dict.fromkeys(blockers))
    complete = not blockers and len(pnls) == len(paths) == len(results)

    daily = []
    for day in sorted(daily_counts):
        values = daily_values[day]
        daily_complete = len(values) == daily_counts[day]
        daily.append({
            "day": day,
            "signals": daily_counts[day],
            "net_eur": _text(sum(values, start=Decimal("0.00")))
            if daily_complete
            else None,
        })

    return {
        "status": "certified" if complete else "blocked",
        "signals": len(paths),
        "filled_signals": filled,
        "wins": wins,
        "losses": losses,
        "flat": flat,
        "net_eur": _text(sum(pnls, start=Decimal("0.00")))
        if complete
        else None,
        "basket_equity_max_drawdown_eur": (
            _text(_equity_drawdown(pnls)) if complete else None
        ),
        "holding_time_ms": _duration_summary(holding_times_ms),
        "daily": daily,
        "blockers": blockers,
        "rows": rows,
    }


def _equity_drawdown(values: Sequence[Decimal]) -> Decimal:
    equity = peak = Decimal("0.00")
    maximum = Decimal("0.00")
    for value in values:
        equity += value
        peak = max(peak, equity)
        maximum = max(maximum, peak - equity)
    return maximum.quantize(_CENT)


def _money(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not result.is_finite():
        return None
    return result.quantize(_CENT, rounding=ROUND_HALF_UP)


def _text(value: Decimal | None) -> str | None:
    return None if value is None else format(value.quantize(_CENT), ".2f")


def _number_text(value: object) -> str | None:
    parsed = _money(value)
    return _text(parsed)


def _duration_summary(values: Sequence[int]) -> dict[str, int | None]:
    if not values:
        return {"min": None, "median": None, "p95": None, "max": None}
    ordered = sorted(int(value) for value in values)
    return {
        "min": ordered[0],
        "median": round(float(median(ordered))),
        "p95": ordered[round(0.95 * (len(ordered) - 1))],
        "max": ordered[-1],
    }
