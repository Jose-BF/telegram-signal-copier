"""Independent parity audit for the Gold 555 pre-entry state machine."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from statistics import median
from typing import Any

from gold_555_entry_watch import EntryWatch
from gold_555_live_candidate import Gold555Policy


TickLoader = Callable[[str, int, datetime], Iterable[object]]
_TERMINAL_STATUS = {
    "gold_555_entry_watch_confirmed": "confirmed",
    "gold_555_entry_watch_expired": "expired",
    "gold_555_entry_watch_cancelled": "cancelled",
    "gold_555_entry_watch_aborted": "aborted",
}


def certify_entry_watch_parity(
    event_rows: Iterable[Mapping[str, Any]],
    *,
    tick_loader: TickLoader,
    policy: Gold555Policy | None = None,
) -> dict[str, Any]:
    """Replay logged samples and the complete broker tick stream separately.

    Logged samples prove that the live state machine implemented its own rule.
    Complete ticks test whether the same rule predicts the same entry outcome
    without using the future MT5 fill.  Neither check proves broker fills or
    terminal lifecycle behavior, so this function never grants end-to-end
    historical extension on its own.
    """

    active_policy = policy or Gold555Policy()
    attempts, global_blockers = _collect_attempts(event_rows)
    rows = [
        _audit_attempt(attempt, tick_loader=tick_loader, policy=active_policy)
        for attempt in attempts
    ]

    logged_exact = sum(not row["logged_blockers"] for row in rows)
    comparable = sum(row["full_tick_outcome"] is not None for row in rows)
    outcome_matches = sum(row["full_tick_outcome_match"] is True for row in rows)
    confirmed = sum(row["actual_outcome"] == "confirmed" for row in rows)
    quote_matches = sum(row["confirmation_quote_match"] is True for row in rows)
    wall_clock_deltas = [
        abs(int(row["confirmation_wall_clock_delta_ms"]))
        for row in rows
        if row["confirmation_wall_clock_delta_ms"] is not None
    ]
    price_deltas = [
        abs(Decimal(str(row["confirmation_quote_delta"])))
        for row in rows
        if row["confirmation_quote_delta"] is not None
    ]
    confirmed_rows = [row for row in rows if row["actual_outcome"] == "confirmed"]
    trigger_tick_matches = sum(
        row["confirmation_tick_match"] is True for row in confirmed_rows
    )
    filled_rows = [
        row for row in confirmed_rows if row["broker_order_outcome"] == "filled"
    ]
    failed_rows = [
        row for row in confirmed_rows if row["broker_order_outcome"] == "failed"
    ]
    missing_order_rows = [
        row for row in confirmed_rows if row["broker_order_outcome"] is None
    ]
    result_event_delays = [
        int(row["broker_result_event_delay_ms"])
        for row in filled_rows
        if row["broker_result_event_delay_ms"] is not None
    ]
    slippages = [
        Decimal(str(row["broker_unfavourable_slippage"]))
        for row in filled_rows
        if row["broker_unfavourable_slippage"] is not None
    ]
    exact_quote_fills = sum(value == Decimal("0") for value in slippages)
    broker_exact = (
        bool(confirmed_rows)
        and len(filled_rows) == len(confirmed_rows)
        and not failed_rows
        and not missing_order_rows
        and all(value == 0 for value in result_event_delays)
        and exact_quote_fills == len(filled_rows)
    )

    trace_exact = (
        bool(rows)
        and not global_blockers
        and logged_exact == len(rows)
    )
    outcome_exact = (
        bool(rows)
        and not global_blockers
        and comparable == len(rows)
        and outcome_matches == len(rows)
    )
    trigger_exact = outcome_exact and all(
        row["actual_outcome"] != "confirmed"
        or (
            row["confirmation_tick_match"] is True
            and row["confirmation_quote_match"] is True
        )
        for row in rows
    )
    return {
        "schema_version": 1,
        "evidence_role": "prospective_entry_decision_parity",
        "attempts": len(rows),
        "signals": len({row["signal_id"] for row in rows}),
        "logged_sample_replay": {
            "status": "exact" if trace_exact else "mismatch",
            "exact_attempts": logged_exact,
            "mismatched_attempts": len(rows) - logged_exact,
        },
        "full_tick_replay": {
            "status": (
                "exact"
                if trigger_exact
                else "outcome_only"
                if outcome_exact
                else "mismatch"
            ),
            "comparable_attempts": comparable,
            "outcome_matches": outcome_matches,
            "confirmed_attempts": confirmed,
            "trigger_tick_matches": trigger_tick_matches,
            "quote_matches": quote_matches,
            "max_abs_confirmation_wall_clock_delta_ms": max(
                wall_clock_deltas,
                default=None,
            ),
            "max_abs_confirmation_quote_delta": _price_text(
                max(price_deltas, default=None)
            ),
        },
        "prospective_entry_outcome_allowed": trace_exact and outcome_exact,
        "prospective_entry_trigger_allowed": trace_exact and trigger_exact,
        "broker_execution": {
            "status": "exact" if broker_exact else "observed_variance",
            "confirmed_attempts": len(confirmed_rows),
            "filled_attempts": len(filled_rows),
            "failed_attempts": len(failed_rows),
            "missing_outcomes": len(missing_order_rows),
            "exact_quote_fills": exact_quote_fills,
            "median_result_event_delay_ms": _median_int(result_event_delays),
            "max_result_event_delay_ms": max(result_event_delays, default=None),
            "median_unfavourable_slippage": _price_text(
                _median_decimal(slippages)
            ),
            "min_unfavourable_slippage": _price_text(
                min(slippages, default=None)
            ),
            "max_unfavourable_slippage": _price_text(
                max(slippages, default=None)
            ),
        },
        "prospective_fill_model_allowed": broker_exact,
        "end_to_end_historical_extension_allowed": False,
        "remaining_end_to_end_gates": [
            "prospective_entry_trigger_parity",
            "broker_fill_parity",
            "deterministic_terminal_lifecycle_parity",
        ],
        "blockers": list(dict.fromkeys(global_blockers)),
        "rows": rows,
    }


def _collect_attempts(
    event_rows: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    attempts: list[dict[str, Any]] = []
    active: dict[str, dict[str, Any]] = {}
    pending_order: dict[str, dict[str, Any]] = {}
    ordinals: dict[str, int] = {}
    blockers: list[str] = []

    for event in event_rows:
        name = str(event.get("ev") or "")
        signal_id = str(event.get("sig") or "")
        if not signal_id:
            continue
        if name == "gold_555_entry_watch_started":
            previous_order = pending_order.pop(signal_id, None)
            if previous_order is not None:
                previous_order["collection_blockers"].append(
                    "next_watch_started_before_order_outcome"
                )
            previous = active.get(signal_id)
            if previous is not None:
                previous["collection_blockers"].append(
                    "next_watch_started_before_terminal"
                )
            ordinal = ordinals.get(signal_id, 0) + 1
            ordinals[signal_id] = ordinal
            attempt = {
                "attempt_id": f"{signal_id}#{ordinal}",
                "signal_id": signal_id,
                "start": dict(event),
                "samples": [],
                "terminal": None,
                "order_outcome": None,
                "order_event": None,
                "collection_blockers": [],
            }
            attempts.append(attempt)
            active[signal_id] = attempt
            continue
        if name in {"gold_555_first_leg_filled", "market_fill_failed"}:
            order_attempt = pending_order.pop(signal_id, None)
            if order_attempt is None:
                blockers.append(f"orphan_order_outcome:{signal_id}:{name}")
                continue
            order_attempt["order_outcome"] = (
                "filled" if name == "gold_555_first_leg_filled" else "failed"
            )
            order_attempt["order_event"] = dict(event)
            continue
        attempt = active.get(signal_id)
        if attempt is None:
            if name in _TERMINAL_STATUS or name == "gold_555_entry_watch_state":
                blockers.append(f"orphan_watch_event:{signal_id}:{name}")
            continue
        if name == "gold_555_entry_watch_state":
            attempt["samples"].append(dict(event))
        elif name in _TERMINAL_STATUS:
            attempt["terminal"] = dict(event)
            active.pop(signal_id, None)
            if name == "gold_555_entry_watch_confirmed":
                pending_order[signal_id] = attempt

    for signal_id, attempt in active.items():
        attempt["collection_blockers"].append("watch_terminal_event_missing")
        blockers.append(f"open_watch_attempt:{signal_id}")
    for signal_id, attempt in pending_order.items():
        attempt["collection_blockers"].append("broker_order_outcome_missing")
        blockers.append(f"open_broker_order_attempt:{signal_id}")
    if not attempts:
        blockers.append("no_watch_attempts")
    return attempts, blockers


def _audit_attempt(
    attempt: Mapping[str, Any],
    *,
    tick_loader: TickLoader,
    policy: Gold555Policy,
) -> dict[str, Any]:
    start = attempt["start"]
    terminal = attempt.get("terminal")
    logged_blockers = list(attempt.get("collection_blockers") or ())
    full_tick_blockers: list[str] = []
    actual_outcome = (
        _TERMINAL_STATUS.get(str(terminal.get("ev") or ""))
        if isinstance(terminal, Mapping)
        else None
    )
    if actual_outcome is None:
        logged_blockers.append("actual_terminal_outcome_missing")

    try:
        logged_watch = EntryWatch.from_dict(dict(start["watch"]))
    except (KeyError, TypeError, ValueError):
        logged_watch = None
        logged_blockers.append("watch_start_invalid")

    if logged_watch is not None:
        for sample in attempt.get("samples") or ():
            try:
                sample_watch = sample.get("watch")
                sample_now = _datetime(sample["ts"])
                if (
                    str(sample.get("action") or "") == "confirm"
                    and isinstance(sample_watch, Mapping)
                    and sample_watch.get("confirmed_at")
                ):
                    sample_now = _datetime(sample_watch["confirmed_at"])
                decision = logged_watch.on_quote(
                    bid=float(sample["bid"]),
                    ask=float(sample["ask"]),
                    now=sample_now,
                    tick_msc=int(sample["tick_time_msc"]),
                    policy=policy,
                )
            except (KeyError, TypeError, ValueError):
                logged_blockers.append("logged_sample_invalid")
                break
            if decision.action != str(sample.get("action") or ""):
                logged_blockers.append("logged_action_mismatch")
            if isinstance(sample_watch, Mapping) and not _watch_equal(
                logged_watch.to_dict(), sample_watch
            ):
                logged_blockers.append("logged_state_mismatch")
        if actual_outcome in {"confirmed", "expired"}:
            if logged_watch.status != actual_outcome:
                logged_blockers.append("logged_terminal_status_mismatch")
            terminal_watch = terminal.get("watch") if terminal else None
            if isinstance(terminal_watch, Mapping) and not _watch_equal(
                logged_watch.to_dict(), terminal_watch
            ):
                logged_blockers.append("logged_terminal_state_mismatch")

    full_watch: EntryWatch | None = None
    if logged_watch is not None:
        try:
            full_watch = EntryWatch.from_dict(dict(start["watch"]))
            reference_msc = int(start["reference_tick_time_msc"])
            day = str(start.get("ts") or full_watch.observed_at.isoformat())[:10]
            last_bid = float(start["reference_bid"])
            last_ask = float(start["reference_ask"])
            for tick in tick_loader(day, reference_msc, full_watch.expires_at):
                tick_msc = int(_value(tick, "source_time_msc"))
                if tick_msc <= reference_msc:
                    continue
                tick_time = _datetime(_value(tick, "time_utc"))
                last_bid = float(_value(tick, "bid"))
                last_ask = float(_value(tick, "ask"))
                decision = full_watch.on_quote(
                    bid=last_bid,
                    ask=last_ask,
                    now=tick_time,
                    tick_msc=tick_msc,
                    policy=policy,
                )
                if decision.action in {"confirm", "expire"}:
                    break
            if full_watch.status == "waiting":
                full_watch.on_quote(
                    bid=last_bid,
                    ask=last_ask,
                    now=full_watch.expires_at,
                    tick_msc=None,
                    policy=policy,
                )
        except (KeyError, TypeError, ValueError) as exc:
            full_tick_blockers.append(
                f"full_tick_replay_invalid:{type(exc).__name__}"
            )
            full_watch = None

    full_outcome = full_watch.status if full_watch is not None else None
    comparable_outcomes = {"confirmed", "expired"}
    outcome_match: bool | None = None
    if actual_outcome in comparable_outcomes and full_outcome in comparable_outcomes:
        outcome_match = actual_outcome == full_outcome
        if not outcome_match:
            full_tick_blockers.append("full_tick_outcome_mismatch")
    else:
        full_tick_blockers.append("full_tick_outcome_not_comparable")

    actual_watch = terminal.get("watch") if isinstance(terminal, Mapping) else None
    actual_at = _optional_datetime(
        actual_watch.get("confirmed_at")
        if isinstance(actual_watch, Mapping)
        else None
    )
    actual_quote = _optional_decimal(
        actual_watch.get("confirmed_quote")
        if isinstance(actual_watch, Mapping)
        else None
    )
    replay_at = full_watch.confirmed_at if full_watch is not None else None
    replay_quote = _optional_decimal(
        full_watch.confirmed_quote if full_watch is not None else None
    )
    confirmation_wall_clock_delta_ms = (
        round((replay_at - actual_at).total_seconds() * 1_000)
        if replay_at is not None and actual_at is not None
        else None
    )
    actual_tick_msc = _optional_int(
        actual_watch.get("last_tick_msc")
        if isinstance(actual_watch, Mapping)
        else None
    )
    replay_tick_msc = (
        _optional_int(full_watch.last_tick_msc)
        if full_watch is not None
        else None
    )
    tick_match = (
        actual_tick_msc == replay_tick_msc
        if actual_tick_msc is not None and replay_tick_msc is not None
        else None
    )
    quote_delta = (
        replay_quote - actual_quote
        if replay_quote is not None and actual_quote is not None
        else None
    )
    quote_match = quote_delta == Decimal("0") if quote_delta is not None else None

    order_outcome = attempt.get("order_outcome")
    order_event = attempt.get("order_event")
    broker_fill_price = _optional_decimal(
        order_event.get("fill_price")
        if isinstance(order_event, Mapping) and order_outcome == "filled"
        else None
    )
    order_at = _optional_datetime(
        order_event.get("ts") if isinstance(order_event, Mapping) else None
    )
    broker_result_event_delay_ms = (
        round((order_at - actual_at).total_seconds() * 1_000)
        if order_outcome == "filled" and order_at is not None and actual_at is not None
        else None
    )
    direction = str((start.get("watch") or {}).get("direction") or "").upper()
    broker_slippage = None
    if broker_fill_price is not None and actual_quote is not None:
        broker_slippage = (
            broker_fill_price - actual_quote
            if direction == "BUY"
            else actual_quote - broker_fill_price
        )

    logged_blockers = list(dict.fromkeys(logged_blockers))
    full_tick_blockers = list(dict.fromkeys(full_tick_blockers))
    return {
        "attempt_id": str(attempt["attempt_id"]),
        "signal_id": str(attempt["signal_id"]),
        "started_at": str(start.get("ts") or ""),
        "actual_outcome": actual_outcome,
        "full_tick_outcome": full_outcome,
        "full_tick_outcome_match": outcome_match,
        "actual_confirmation_at": _iso(actual_at),
        "full_tick_confirmation_at": _iso(replay_at),
        "confirmation_wall_clock_delta_ms": confirmation_wall_clock_delta_ms,
        "actual_confirmation_tick_msc": actual_tick_msc,
        "full_tick_confirmation_tick_msc": replay_tick_msc,
        "confirmation_tick_match": tick_match,
        "actual_confirmation_quote": _price_text(actual_quote),
        "full_tick_confirmation_quote": _price_text(replay_quote),
        "confirmation_quote_delta": _price_text(quote_delta),
        "confirmation_quote_match": quote_match,
        "broker_order_outcome": order_outcome,
        "broker_failure_reason": (
            str(order_event.get("reason") or "") or None
            if isinstance(order_event, Mapping) and order_outcome == "failed"
            else None
        ),
        "broker_result_event_at": (
            _iso(order_at) if order_outcome == "filled" else None
        ),
        "broker_result_event_delay_ms": broker_result_event_delay_ms,
        "broker_fill_price": _price_text(broker_fill_price),
        "broker_unfavourable_slippage": _price_text(broker_slippage),
        "logged_samples": len(attempt.get("samples") or ()),
        "logged_blockers": logged_blockers,
        "full_tick_blockers": full_tick_blockers,
        "blockers": logged_blockers + full_tick_blockers,
    }


def _watch_equal(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> bool:
    keys = (
        "direction",
        "reference",
        "observed_at",
        "expires_at",
        "adverse_extreme",
        "armed",
        "status",
        "confirmed_quote",
        "confirmed_at",
    )
    return all(expected.get(key) == actual.get(key) for key in keys)


def _value(row: object, name: str) -> object:
    if isinstance(row, Mapping):
        return row[name]
    return getattr(row, name)


def _datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif hasattr(value, "to_pydatetime"):
        parsed = value.to_pydatetime()
    else:
        parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _optional_datetime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    return _datetime(value)


def _optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _price_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(rounded, ".2f")


def _median_int(values: list[int]) -> int | None:
    if not values:
        return None
    return round(float(median(values)))


def _median_decimal(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / Decimal("2")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
