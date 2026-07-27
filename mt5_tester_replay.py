"""Deterministic bridge between executed-MT5 replay data and Strategy Tester."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable


SCHEMA_VERSION = 1
FIXTURE_COLUMNS = (
    "schema_version",
    "signal_id",
    "provider",
    "ticket",
    "direction",
    "volume",
    "entry_time_msc",
    "entry_price",
    "observed_close_time_msc",
    "observed_close_price",
    "observed_close_reason",
    "observed_pnl_eur",
    "provider_sl",
    "provider_tp1",
    "provider_tp2",
    "source_sha256",
)
PROVIDER_NAMES = {
    "canal1": "Dubai Investing",
    "canal2": "Gold Signals",
}


class FixtureBlockedError(ValueError):
    """Raised when the requested fixture would require invented evidence."""


def _block(reason: str, detail: object | None = None) -> None:
    suffix = "" if detail is None else f":{detail}"
    raise FixtureBlockedError(f"{reason}{suffix}")


def _decimal(value: object, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        _block(f"invalid_{field}")
    if not result.is_finite():
        _block(f"invalid_{field}")
    return result


def _decimal_text(value: object, field: str) -> str:
    return str(_decimal(value, field))


def _int(value: object, field: str) -> int:
    if isinstance(value, bool):
        _block(f"invalid_{field}")
    try:
        return int(value)
    except (TypeError, ValueError):
        _block(f"invalid_{field}")


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def fixture_sha256(rows: Iterable[dict]) -> str:
    """Fingerprint the complete ordered fixture."""

    return _canonical_sha256(list(rows))


def _trade_day(trade: dict) -> date | None:
    raw = str(trade.get("open_dt_utc") or "")
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _history_by_ticket(observed_history: Iterable[dict]) -> dict[int, dict]:
    result: dict[int, dict] = {}
    for item in observed_history:
        ticket = _int(item.get("ticket"), "history_ticket")
        if ticket in result:
            _block("duplicate_history_ticket", ticket)
        result[ticket] = dict(item)
    return result


def _require_equal(
    *,
    actual: object,
    expected: object,
    reason: str,
    decimal: bool = False,
) -> None:
    if decimal:
        equal = _decimal(actual, reason) == _decimal(expected, reason)
    else:
        equal = actual == expected
    if not equal:
        _block(reason)


def _fixture_row(trade: dict, ticket: dict, history: dict) -> dict:
    signal_id = str(trade.get("sig_id") or "").strip()
    if not signal_id:
        _block("missing_signal_id")
    direction = str(trade.get("direction") or "").upper()
    if direction not in {"BUY", "SELL"}:
        _block("invalid_direction", signal_id)
    channel = str(trade.get("channel") or "").lower()
    provider = PROVIDER_NAMES.get(channel)
    if provider is None:
        _block("unknown_provider", channel)

    levels = trade.get("levels") or {}
    tps = levels.get("provider_tps") or []
    if len(tps) < 2:
        _block("missing_provider_tp2", signal_id)
    provider_sl = levels.get("provider_sl")
    if provider_sl is None:
        _block("missing_provider_sl", signal_id)

    open_deal = ticket.get("open_deal") or {}
    close_deal = ticket.get("close_deal") or {}
    if ticket.get("is_closed") is not True or not close_deal:
        _block("ticket_not_closed", ticket.get("ticket"))

    ticket_id = _int(ticket.get("ticket"), "ticket")
    volume = _decimal_text(ticket.get("volume"), "volume")
    entry_time_msc = _int(open_deal.get("time_msc"), "entry_time_msc")
    entry_price = _decimal_text(ticket.get("open_price"), "entry_price")
    close_time_msc = _int(close_deal.get("time_msc"), "close_time_msc")
    close_price = _decimal_text(ticket.get("close_price"), "close_price")
    close_reason = str(ticket.get("close_reason") or "").lower()
    if not close_reason:
        _block("missing_close_reason", ticket_id)
    observed_pnl = _decimal_text(ticket.get("pnl_net"), "observed_pnl_eur")

    comparisons = (
        ("history_direction_mismatch", history.get("direction"), direction, False),
        ("history_volume_mismatch", history.get("volume"), volume, True),
        (
            "history_entry_time_mismatch",
            _int(history.get("open_time_msc"), "history_open_time_msc"),
            entry_time_msc,
            False,
        ),
        (
            "history_entry_price_mismatch",
            history.get("open_price"),
            entry_price,
            True,
        ),
        (
            "history_close_time_mismatch",
            _int(history.get("close_time_msc"), "history_close_time_msc"),
            close_time_msc,
            False,
        ),
        (
            "history_close_price_mismatch",
            history.get("close_price"),
            close_price,
            True,
        ),
        (
            "history_close_reason_mismatch",
            str(history.get("close_reason") or "").lower(),
            close_reason,
            False,
        ),
        ("history_pnl_mismatch", history.get("pnl_net"), observed_pnl, True),
    )
    for reason, actual, expected, is_decimal in comparisons:
        _require_equal(
            actual=actual,
            expected=expected,
            reason=reason,
            decimal=is_decimal,
        )

    source = {
        "signal_id": signal_id,
        "ticket": ticket,
        "levels": {
            "provider_sl": provider_sl,
            "provider_tps": tps,
        },
        "history": history,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "signal_id": signal_id,
        "provider": provider,
        "ticket": ticket_id,
        "direction": direction,
        "volume": volume,
        "entry_time_msc": entry_time_msc,
        "entry_price": entry_price,
        "observed_close_time_msc": close_time_msc,
        "observed_close_price": close_price,
        "observed_close_reason": close_reason,
        "observed_pnl_eur": observed_pnl,
        "provider_sl": _decimal_text(provider_sl, "provider_sl"),
        "provider_tp1": _decimal_text(tps[0], "provider_tp1"),
        "provider_tp2": _decimal_text(tps[1], "provider_tp2"),
        "source_sha256": _canonical_sha256(source),
    }


def build_fixture(
    *,
    replay_rows: Iterable[dict],
    day: date,
    observed_history: Iterable[dict],
) -> tuple[list[dict], dict]:
    """Build a fail-closed fixture for one executed-MT5 calendar day."""

    history = _history_by_ticket(observed_history)
    rows: list[dict] = []
    seen: set[int] = set()
    signals: set[str] = set()
    for trade in replay_rows:
        if _trade_day(trade) != day:
            continue
        if str(trade.get("status") or "").lower() != "closed":
            _block("trade_not_closed", trade.get("sig_id"))
        for ticket in trade.get("tickets") or []:
            ticket_id = _int(ticket.get("ticket"), "ticket")
            if ticket_id in seen:
                _block("duplicate_ticket", ticket_id)
            seen.add(ticket_id)
            if ticket_id not in history:
                _block("missing_history_ticket", ticket_id)
            row = _fixture_row(trade, ticket, history[ticket_id])
            rows.append(row)
            signals.add(row["signal_id"])

    if not rows:
        _block("empty_fixture", day.isoformat())
    extra_history = sorted(set(history) - seen)
    if extra_history:
        _block("unexpected_history_ticket", extra_history[0])

    rows.sort(key=lambda row: (
        row["entry_time_msc"],
        row["signal_id"],
        row["ticket"],
    ))
    total = sum(
        (_decimal(row["observed_pnl_eur"], "observed_pnl_eur") for row in rows),
        Decimal("0"),
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "day": day.isoformat(),
        "signals": len(signals),
        "tickets": len(rows),
        "observed_pnl_eur": str(total),
        "fixture_sha256": fixture_sha256(rows),
    }
    return rows, manifest


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_fixture(
    rows: Iterable[dict],
    manifest: dict,
    *,
    output_dir: Path,
    stem: str,
) -> tuple[Path, Path]:
    """Write a fixture and manifest atomically."""

    ordered_rows = list(rows)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=FIXTURE_COLUMNS,
        delimiter=";",
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(ordered_rows)
    csv_payload = buffer.getvalue().encode("utf-8")

    written_manifest = dict(manifest)
    written_manifest["csv_sha256"] = hashlib.sha256(csv_payload).hexdigest()
    manifest_payload = (
        json.dumps(
            written_manifest,
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("ascii")

    output_dir = Path(output_dir)
    csv_path = output_dir / f"{stem}.csv"
    manifest_path = output_dir / f"{stem}.manifest.json"
    _atomic_write(csv_path, csv_payload)
    _atomic_write(manifest_path, manifest_payload)
    return csv_path, manifest_path

