"""One fail-closed report for actual, retrospective and prospective 555 results."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


_CENT = Decimal("0.01")


def build_pipeline_truth_report(
    *,
    management_report: Mapping[str, Any],
    entry_watch_report: Mapping[str, Any],
    prospective_report: Mapping[str, Any],
    variant_name: str,
    ledger_rows: Iterable[Mapping[str, Any]],
    event_rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare evidence roles without blending them into an uncertainty range."""

    blockers: list[str] = []
    variants = prospective_report.get("variants")
    variant = variants.get(variant_name) if isinstance(variants, Mapping) else None
    if not isinstance(variant, Mapping):
        raise ValueError(f"prospective variant not found: {variant_name}")

    actual_rows, duplicate_actual = _rows_by_id(
        management_report.get("rows") or (), "signal_id"
    )
    predicted_rows, duplicate_predicted = _rows_by_id(
        variant.get("rows") or (), "signal_id"
    )
    ledger_by_id, duplicate_ledger = _rows_by_id(ledger_rows, "sig_id")
    blockers.extend(duplicate_actual + duplicate_predicted + duplicate_ledger)
    if set(actual_rows) != set(predicted_rows):
        for signal_id in sorted(set(actual_rows).difference(predicted_rows)):
            blockers.append(f"prospective_signal_missing:{signal_id}")
        for signal_id in sorted(set(predicted_rows).difference(actual_rows)):
            blockers.append(f"unexpected_prospective_signal:{signal_id}")

    failed_signals = {
        str(row.get("sig") or "")
        for row in event_rows
        if row.get("ev") == "market_fill_failed"
        and row.get("strategy_id") == "gold_now_555_v1"
    }
    rows: list[dict[str, Any]] = []
    causes: Counter[str] = Counter()
    for signal_id in sorted(actual_rows, key=_signal_sort_key):
        actual = actual_rows[signal_id]
        predicted = predicted_rows.get(signal_id)
        actual_money = _money(actual.get("actual_mt5_eur"))
        mirror_money = _money(actual.get("live_logic_mirror_eur"))
        actual_entries = _integer(actual.get("actual_entry_count"))
        predicted_money = (
            _money(predicted.get("net_eur"))
            if predicted is not None
            else None
        )
        predicted_entries = (
            _integer(predicted.get("entry_count"))
            if predicted is not None
            else None
        )
        money_delta = (
            predicted_money - actual_money
            if actual_money is not None and predicted_money is not None
            else None
        )
        exact = (
            actual.get("status") == "exact"
            and actual_money == mirror_money
            and actual_money == predicted_money
            and actual_entries == predicted_entries
        )
        cause = None if exact else _difference_cause(
            signal_id=signal_id,
            actual_entries=actual_entries,
            predicted_entries=predicted_entries,
            actual_money=actual_money,
            predicted_money=predicted_money,
            predicted=predicted,
            ledger=ledger_by_id.get(signal_id),
            failed_signals=failed_signals,
        )
        if cause:
            causes[cause] += 1
        rows.append({
            "signal_id": signal_id,
            "status": "exact" if exact else "mismatch",
            "actual_mt5_eur": _text(actual_money),
            "retrospective_management_replay_eur": _text(mirror_money),
            "prospective_simulation_eur": _text(predicted_money),
            "prospective_minus_actual_eur": _text(money_delta),
            "actual_entry_count": actual_entries,
            "prospective_entry_count": predicted_entries,
            "difference_cause": cause,
        })

    actual_total = _sum(row["actual_mt5_eur"] for row in rows)
    mirror_total = _sum(
        row["retrospective_management_replay_eur"] for row in rows
    )
    predicted_total = _sum(row["prospective_simulation_eur"] for row in rows)
    declared_actual = _money(
        (management_report.get("actual_mt5") or {}).get("net_eur")
    )
    declared_mirror = _money(
        (management_report.get("live_logic_mirror") or {}).get("net_eur")
    )
    declared_predicted = _money(variant.get("net_eur"))
    if actual_total != declared_actual:
        blockers.append("actual_total_disagrees_with_rows")
    if mirror_total != declared_mirror:
        blockers.append("management_total_disagrees_with_rows")
    if predicted_total != declared_predicted:
        blockers.append("prospective_total_disagrees_with_rows")

    management_exact = (
        management_report.get("management_replay_allowed") is True
        and (management_report.get("parity") or {}).get("status") == "exact"
    )
    entry_outcome_exact = (
        entry_watch_report.get("prospective_entry_outcome_allowed") is True
    )
    entry_trigger_exact = (
        entry_watch_report.get("prospective_entry_trigger_allowed") is True
    )
    fill_exact = entry_watch_report.get("prospective_fill_model_allowed") is True
    prospective_certified = variant.get("status") == "certified"
    exact_signals = sum(row["status"] == "exact" for row in rows)
    pipeline_exact = bool(rows) and exact_signals == len(rows)
    lifecycle_exact = not any(
        row["difference_cause"] in {
            "post_flat_reentry_before_finalization",
            "additional_live_entry_unexplained",
        }
        for row in rows
    )
    end_to_end = (
        not blockers
        and management_exact
        and entry_outcome_exact
        and entry_trigger_exact
        and fill_exact
        and prospective_certified
        and pipeline_exact
    )

    return {
        "schema_version": 1,
        "purpose": "gold_555_pipeline_truth_without_blended_ranges",
        "observed_mt5": {
            "evidence_role": "broker_fact",
            "signals": len(rows),
            "net_eur": _text(actual_total),
        },
        "retrospective_management_replay": {
            "evidence_role": "conditioned_on_actual_mt5_fills",
            "status": "exact" if management_exact else "blocked",
            "net_eur": _text(mirror_total),
        },
        "prospective_simulation": {
            "evidence_role": "telegram_and_ticks_without_future_mt5_fills",
            "variant": variant_name,
            "status": str(variant.get("status") or "blocked"),
            "net_eur": _text(predicted_total),
        },
        "actual_vs_prospective": {
            "status": "exact" if pipeline_exact else "mismatch",
            "signals": len(rows),
            "exact_signals": exact_signals,
            "mismatched_signals": len(rows) - exact_signals,
            "net_delta_eur": _text(
                None
                if actual_total is None or predicted_total is None
                else predicted_total - actual_total
            ),
            "difference_causes": dict(sorted(causes.items())),
        },
        "gates": {
            "management_replay": "pass" if management_exact else "fail",
            "entry_outcome": "pass" if entry_outcome_exact else "fail",
            "entry_trigger": "pass" if entry_trigger_exact else "fail",
            "broker_fill_model": "pass" if fill_exact else "fail",
            "deterministic_terminal_lifecycle": (
                "pass" if lifecycle_exact else "fail"
            ),
        },
        "end_to_end_historical_extension_allowed": end_to_end,
        "blockers": list(dict.fromkeys(blockers)),
        "rows": rows,
    }


def _difference_cause(
    *,
    signal_id: str,
    actual_entries: int | None,
    predicted_entries: int | None,
    actual_money: Decimal | None,
    predicted_money: Decimal | None,
    predicted: Mapping[str, Any] | None,
    ledger: Mapping[str, Any] | None,
    failed_signals: set[str],
) -> str:
    if signal_id in failed_signals:
        return "broker_rejection_retry"
    if actual_entries is None or predicted_entries is None:
        return "entry_count_evidence_missing"
    if actual_entries > predicted_entries:
        positions = sorted(
            (
                row for row in (ledger or {}).get("positions") or ()
                if isinstance(row, Mapping)
            ),
            key=lambda row: str(row.get("open_dt_utc") or ""),
        )
        if len(positions) >= actual_entries and predicted_entries > 0:
            extra_open = _optional_datetime(
                positions[predicted_entries].get("open_dt_utc")
            )
            prior_closes = [
                _optional_datetime(row.get("close_dt_utc"))
                for row in positions[:predicted_entries]
            ]
            if (
                extra_open is not None
                and prior_closes
                and all(value is not None and value < extra_open for value in prior_closes)
            ):
                return "post_flat_reentry_before_finalization"
            predicted_entry = _money(
                predicted.get("first_entry_price") if predicted else None
            )
            actual_entry = _money(positions[0].get("open_price"))
            if predicted_entry is not None and actual_entry != predicted_entry:
                return "broker_fill_shift_changed_target_vs_ladder_order"
        return "additional_live_entry_unexplained"
    if actual_entries < predicted_entries:
        return "prospective_model_added_entries_not_seen_live"
    if actual_money != predicted_money:
        return "execution_price_or_exit_timing_difference"
    return "unclassified_pipeline_difference"


def _rows_by_id(
    rows: Iterable[Mapping[str, Any]],
    key: str,
) -> tuple[dict[str, Mapping[str, Any]], list[str]]:
    result: dict[str, Mapping[str, Any]] = {}
    blockers: list[str] = []
    for row in rows:
        signal_id = str(row.get(key) or "")
        if not signal_id:
            blockers.append(f"row_without_{key}")
            continue
        if signal_id in result:
            blockers.append(f"duplicate_{key}:{signal_id}")
            continue
        result[signal_id] = row
    return result, blockers


def _money(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite():
        return None
    return parsed.quantize(_CENT, rounding=ROUND_HALF_UP)


def _sum(values: Iterable[object]) -> Decimal | None:
    parsed = [_money(value) for value in values]
    if any(value is None for value in parsed):
        return None
    return sum((value for value in parsed if value is not None), Decimal("0.00"))


def _text(value: Decimal | None) -> str | None:
    return None if value is None else format(value.quantize(_CENT), ".2f")


def _integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _optional_datetime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _signal_sort_key(signal_id: str) -> tuple[str, int, int | str]:
    head, separator, tail = signal_id.rpartition("_")
    if separator and tail.isdigit():
        return head, 0, int(tail)
    return signal_id, 1, signal_id
