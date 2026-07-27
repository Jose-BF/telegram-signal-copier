"""Deterministic bridge between executed-MT5 replay data and Strategy Tester."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import shutil
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Callable, Iterable, Sequence


SCHEMA_VERSION = 1
FIXTURE_COLUMNS = (
    "schema_version",
    "signal_id",
    "provider",
    "ticket",
    "direction",
    "volume",
    "entry_time_msc",
    "mt5_time_offset_s",
    "entry_time_utc",
    "entry_price",
    "observed_close_time_msc",
    "observed_close_time_utc",
    "observed_close_price",
    "observed_close_reason",
    "observed_pnl_eur",
    "provider_sl",
    "provider_tp1",
    "provider_tp2",
    "source_sha256",
)
RESULT_COLUMNS = (
    "schema_version",
    "policy_id",
    "signal_id",
    "ticket",
    "status",
    "direction",
    "volume",
    "entry_time_msc",
    "entry_price",
    "close_time_msc",
    "close_price",
    "close_reason",
    "pnl_eur",
    "touch_bid",
    "touch_ask",
    "source_sha256",
)
POLICY_IDS = {
    "observed_close",
    "all_tp2_keep_be",
    "all_tp2_no_be",
}
PROVIDER_NAMES = {
    "canal1": "Dubai Investing",
    "canal2": "Gold Signals",
}
BOT_MAGIC_NUMBERS = {20260421, 20260422}
POLICY_ORDER = (
    "observed_close",
    "all_tp2_keep_be",
    "all_tp2_no_be",
)
EA_FILENAME = "TelegramSignalReplayEA.ex5"
EA_TESTER_PATH = Path("Research") / EA_FILENAME
COMMON_RUN_FOLDER = "TelegramSignalReplay"


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


def _parse_datetime(value: object, field: str) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        _block(f"invalid_{field}")
    if parsed.tzinfo is None:
        _block(f"naive_{field}")
    return parsed.astimezone(timezone.utc)


def _mt5_server_msc_to_utc(
    time_msc: object,
    *,
    offset_seconds: object,
    field: str,
) -> datetime:
    try:
        return (
            datetime.fromtimestamp(
                _int(time_msc, field) / 1000,
                tz=timezone.utc,
            )
            - timedelta(
                seconds=_int(offset_seconds, "mt5_time_offset_s")
            )
        )
    except (OSError, OverflowError, ValueError):
        _block(f"invalid_{field}")


def _require_server_time_matches(
    *,
    server_time_utc: datetime,
    recorded_time: object,
    field: str,
) -> None:
    recorded = _parse_datetime(recorded_time, field)
    if abs((server_time_utc - recorded).total_seconds()) > 1.0:
        _block(f"{field.removesuffix('_dt_utc')}_server_time_mismatch")


def select_replay_rows(
    replay_rows: Iterable[dict],
    *,
    day: date,
    cutoff_utc: datetime | None = None,
) -> tuple[list[dict], dict]:
    """Select a day or an explicit, auditable closed intraday prefix."""

    if cutoff_utc is not None:
        if cutoff_utc.tzinfo is None:
            _block("naive_cutoff_utc")
        cutoff_utc = cutoff_utc.astimezone(timezone.utc)
        if cutoff_utc.date() != day:
            _block("cutoff_day_mismatch")
    day_rows = [
        dict(row) for row in replay_rows if _trade_day(row) == day
    ]
    selected: list[dict] = []
    rows_after_cutoff = 0
    rows_opened_after_cutoff = 0
    rows_not_closed_by_cutoff = 0
    for row in day_rows:
        if cutoff_utc is None:
            selected.append(row)
            continue
        opened = _parse_datetime(row.get("open_dt_utc"), "open_dt_utc")
        if opened > cutoff_utc:
            rows_after_cutoff += 1
            rows_opened_after_cutoff += 1
            continue
        raw_closed = row.get("close_dt_utc")
        if not raw_closed:
            rows_after_cutoff += 1
            rows_not_closed_by_cutoff += 1
            continue
        closed = _parse_datetime(raw_closed, "close_dt_utc")
        if closed > cutoff_utc:
            rows_after_cutoff += 1
            rows_not_closed_by_cutoff += 1
            continue
        selected.append(row)
    return selected, {
        "cutoff_utc": (
            None
            if cutoff_utc is None
            else cutoff_utc.isoformat(timespec="seconds")
        ),
        "day_rows_seen": len(day_rows),
        "rows_selected": len(selected),
        "rows_after_cutoff": rows_after_cutoff,
        "rows_opened_after_cutoff": rows_opened_after_cutoff,
        "rows_not_closed_by_cutoff": rows_not_closed_by_cutoff,
    }


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
    trade_offset = trade.get("mt5_time_offset_s")
    ticket_offset = ticket.get("mt5_time_offset_s")
    if trade_offset is not None and ticket_offset is not None:
        if _int(trade_offset, "trade_mt5_time_offset_s") != _int(
            ticket_offset,
            "ticket_mt5_time_offset_s",
        ):
            _block("ticket_mt5_time_offset_mismatch", ticket_id)
    mt5_time_offset_s = _int(
        ticket_offset if ticket_offset is not None else trade_offset,
        "mt5_time_offset_s",
    )
    entry_time_utc = _mt5_server_msc_to_utc(
        entry_time_msc,
        offset_seconds=mt5_time_offset_s,
        field="entry_time_msc",
    )
    _require_server_time_matches(
        server_time_utc=entry_time_utc,
        recorded_time=ticket.get("open_dt_utc"),
        field="entry_dt_utc",
    )
    entry_price = _decimal_text(ticket.get("open_price"), "entry_price")
    close_time_msc = _int(close_deal.get("time_msc"), "close_time_msc")
    close_time_utc = _mt5_server_msc_to_utc(
        close_time_msc,
        offset_seconds=mt5_time_offset_s,
        field="close_time_msc",
    )
    _require_server_time_matches(
        server_time_utc=close_time_utc,
        recorded_time=ticket.get("close_dt_utc"),
        field="close_dt_utc",
    )
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
        "mt5_time_offset_s": mt5_time_offset_s,
        "entry_time_utc": entry_time_utc.isoformat(timespec="microseconds"),
        "entry_price": entry_price,
        "observed_close_time_msc": close_time_msc,
        "observed_close_time_utc": close_time_utc.isoformat(
            timespec="microseconds"
        ),
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


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_copy(source: Path, target: Path) -> None:
    source = Path(source)
    target = Path(target)
    if source.resolve() == target.resolve():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_json(path: Path, value: object) -> None:
    payload = (
        json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    ).encode("ascii")
    _atomic_write(path, payload)


def _atomic_utf16(path: Path, text: str) -> None:
    _atomic_write(path, text.encode("utf-16"))


def _freeze_tick_cache(
    *,
    source_dir: Path,
    target_dir: Path,
    start_day: date,
    end_day: date,
) -> dict[str, dict]:
    target_dir.mkdir(parents=True, exist_ok=True)
    for path in target_dir.glob("*"):
        if path.is_file():
            path.unlink()
    days: dict[str, dict] = {}
    for day_value in _calendar_days(start_day, end_day):
        day_text = day_value.isoformat()
        parquet_source = source_dir / f"{day_text}.parquet"
        contract_source = source_dir / f"{day_text}.parquet.meta.json"
        if not parquet_source.is_file() and not contract_source.is_file():
            continue
        if not parquet_source.is_file() or not contract_source.is_file():
            days[day_text] = {"status": "incomplete_source_pair"}
            continue
        parquet_target = target_dir / parquet_source.name
        contract_target = target_dir / contract_source.name
        _atomic_copy(parquet_source, parquet_target)
        _atomic_copy(contract_source, contract_target)
        days[day_text] = {
            "status": "frozen",
            "parquet_path": str(parquet_target.resolve()),
            "parquet_sha256": _file_sha256(parquet_target),
            "contract_path": str(contract_target.resolve()),
            "contract_sha256": _file_sha256(contract_target),
        }
    return days


def _freeze_independent_evidence(
    *,
    run_dir: Path,
    day: date,
    tester_until: date,
    market_tick_cache_dir: Path | None,
    money_tick_cache_dir: Path | None,
    money_contract_path: Path | None,
) -> dict:
    evidence_root = run_dir / "independent_evidence"
    market_target = evidence_root / "market_ticks"
    money_target = evidence_root / "money_ticks"
    contract_target = evidence_root / "broker_money_contract.json"
    for target in (market_target, money_target):
        target.mkdir(parents=True, exist_ok=True)
        for path in target.glob("*"):
            if path.is_file():
                path.unlink()
    contract_target.unlink(missing_ok=True)

    configured = all(
        path is not None
        for path in (
            market_tick_cache_dir,
            money_tick_cache_dir,
            money_contract_path,
        )
    )
    market_days: dict[str, dict] = {}
    money_days: dict[str, dict] = {}
    contract_sha256 = None
    if configured:
        market_days = _freeze_tick_cache(
            source_dir=Path(market_tick_cache_dir),
            target_dir=market_target,
            start_day=day - timedelta(days=1),
            end_day=tester_until,
        )
        money_days = _freeze_tick_cache(
            source_dir=Path(money_tick_cache_dir),
            target_dir=money_target,
            start_day=day - timedelta(days=1),
            end_day=tester_until,
        )
        source_contract = Path(money_contract_path)
        if source_contract.is_file():
            _atomic_copy(source_contract, contract_target)
            contract_sha256 = _file_sha256(contract_target)

    status = (
        "prepared"
        if configured and contract_sha256 is not None
        else "missing"
    )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "market_tick_cache_path": str(market_target.resolve()),
        "market_days": market_days,
        "money_tick_cache_path": str(money_target.resolve()),
        "money_days": money_days,
        "money_contract_path": str(contract_target.resolve()),
        "money_contract_sha256": contract_sha256,
    }
    payload["evidence_sha256"] = _canonical_sha256(payload)
    return payload


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


def read_result(path: Path) -> list[dict]:
    """Read the fixed tester result schema without coercing money to float."""

    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        if tuple(reader.fieldnames or ()) != RESULT_COLUMNS:
            raise ValueError("invalid_result_columns")
        rows: list[dict] = []
        for raw in reader:
            row = dict(raw)
            for field in (
                "schema_version",
                "ticket",
                "entry_time_msc",
                "close_time_msc",
            ):
                row[field] = _int(row.get(field), f"result_{field}")
            rows.append(row)
    return rows


def _money(value: object, field: str) -> Decimal:
    return _decimal(value, field).quantize(Decimal("0.01"))


def _append_once(blockers: list[str], blocker: str) -> None:
    if blocker not in blockers:
        blockers.append(blocker)


def _mt5_server_calendar_date(time_msc: object, field: str) -> date:
    # MT5 deal/tick epochs in this corpus preserve the broker wall clock.
    try:
        return datetime.fromtimestamp(
            _int(time_msc, field) / 1000,
            tz=timezone.utc,
        ).date()
    except (OSError, OverflowError, ValueError):
        _block(f"invalid_{field}")


def _result_ticket_map(
    rows: Iterable[dict],
    blockers: list[str],
) -> dict[int, dict]:
    result: dict[int, dict] = {}
    for row in rows:
        try:
            ticket = _int(row.get("ticket"), "result_ticket")
        except FixtureBlockedError:
            _append_once(blockers, "invalid_result_ticket")
            continue
        if ticket in result:
            _append_once(blockers, "duplicate_result_ticket")
            continue
        result[ticket] = dict(row)
    return result


def _compare_decimal(
    row: dict,
    fixture: dict,
    *,
    result_field: str,
    fixture_field: str,
    blocker: str,
    blockers: list[str],
) -> None:
    try:
        equal = _decimal(row.get(result_field), result_field) == _decimal(
            fixture.get(fixture_field),
            fixture_field,
        )
    except FixtureBlockedError:
        equal = False
    if not equal:
        _append_once(blockers, blocker)


def _validate_alternative_result(
    *,
    fixture: dict,
    row: dict,
    policy_id: str,
    blockers: list[str],
) -> None:
    allowed_reasons = {
        "all_tp2_no_be": {"tp2", "sl"},
        "all_tp2_keep_be": {"tp2", "sl", "be"},
    }
    reason = str(row.get("close_reason") or "")
    if reason not in allowed_reasons.get(policy_id, set()):
        _append_once(blockers, "invalid_alternative_close_reason")

    try:
        entry_time = _int(row.get("entry_time_msc"), "result_entry_time_msc")
        close_time = _int(row.get("close_time_msc"), "result_close_time_msc")
    except FixtureBlockedError:
        _append_once(blockers, "invalid_result_close_time")
    else:
        if close_time < entry_time:
            _append_once(blockers, "result_close_before_entry")

    try:
        bid = _decimal(row.get("touch_bid"), "touch_bid")
        ask = _decimal(row.get("touch_ask"), "touch_ask")
        close_price = _decimal(row.get("close_price"), "close_price")
    except FixtureBlockedError:
        _append_once(blockers, "invalid_touch_quote")
        return
    if bid <= 0 or ask <= 0 or close_price <= 0:
        _append_once(blockers, "invalid_touch_quote")
        return
    if ask < bid:
        _append_once(blockers, "crossed_touch_quote")
        return

    direction = str(fixture.get("direction") or "")
    side = bid if direction == "BUY" else ask
    if reason == "tp2":
        tp2 = _decimal(fixture.get("provider_tp2"), "provider_tp2")
        if close_price != tp2:
            _append_once(blockers, "tp2_close_price_mismatch")
        touched = side >= tp2 if direction == "BUY" else side <= tp2
        if not touched:
            _append_once(blockers, "tp2_not_touched")
    elif reason in {"sl", "be"}:
        if close_price != side:
            _append_once(blockers, "stop_close_price_mismatch")
        level_field = "entry_price" if reason == "be" else "provider_sl"
        level = _decimal(fixture.get(level_field), level_field)
        touched = side <= level if direction == "BUY" else side >= level
        if not touched:
            _append_once(blockers, f"{reason}_not_touched")


def _compare_alternative_oracle(
    *,
    row: dict,
    expected: dict,
    blockers: list[str],
) -> None:
    for field, blocker in (
        ("close_time_msc", "alternative_close_time_mismatch"),
        ("close_reason", "alternative_close_reason_mismatch"),
    ):
        if row.get(field) != expected.get(field):
            _append_once(blockers, blocker)
    for field, blocker in (
        ("close_price", "alternative_close_price_mismatch"),
        ("touch_bid", "alternative_touch_bid_mismatch"),
        ("touch_ask", "alternative_touch_ask_mismatch"),
    ):
        try:
            equal = _decimal(row.get(field), field) == _decimal(
                expected.get(field),
                f"expected_{field}",
            )
        except FixtureBlockedError:
            equal = False
        if not equal:
            _append_once(blockers, blocker)
    try:
        pnl_equal = _money(row.get("pnl_eur"), "pnl_eur") == _money(
            expected.get("pnl_eur"),
            "expected_pnl_eur",
        )
    except FixtureBlockedError:
        pnl_equal = False
    if not pnl_equal:
        _append_once(blockers, "alternative_pnl_mismatch")


def _calendar_days(start: date, stop: date) -> Iterable[date]:
    current = start
    while current <= stop:
        yield current
        current += timedelta(days=1)


def build_alternative_oracle_rows(
    *,
    fixture_rows: Iterable[dict],
    policy_id: str,
    tester_until: date,
    market_tick_loader: Callable[
        [date],
        tuple[object, dict | None, list[str]],
    ],
    money_converter: object,
) -> tuple[dict[int, dict], list[str], list[dict]]:
    """Independently replay MT5's TP2 policies over verified UTC ticks."""

    if policy_id not in {"all_tp2_keep_be", "all_tp2_no_be"}:
        return {}, ["alternative_oracle_policy_unsupported"], []

    import numpy as np
    import pandas as pd
    import simulation_oracle

    rows = list(fixture_rows)
    blockers: list[str] = []
    frames: dict[date, object | None] = {}
    frame_blockers: dict[date, list[str]] = {}
    evidence_by_day: dict[date, dict] = {}

    def load_day(day_value: date) -> tuple[object | None, list[str]]:
        if day_value in frames:
            return frames[day_value], frame_blockers[day_value]
        frame, evidence, day_blockers = market_tick_loader(day_value)
        if day_blockers:
            frames[day_value] = None
            frame_blockers[day_value] = [
                str(blocker) for blocker in day_blockers
            ]
            return None, frame_blockers[day_value]
        if not isinstance(evidence, dict):
            frames[day_value] = None
            frame_blockers[day_value] = [
                f"missing_market_tick_evidence:{day_value.isoformat()}",
            ]
            return None, frame_blockers[day_value]
        frames[day_value] = frame
        frame_blockers[day_value] = []
        evidence_by_day[day_value] = dict(evidence)
        return frame, []

    expected: dict[int, dict] = {}
    for fixture in rows:
        ticket = _int(fixture.get("ticket"), "fixture_ticket")
        try:
            opened = _parse_datetime(
                fixture.get("entry_time_utc"),
                "entry_time_utc",
            )
            offset = _int(
                fixture.get("mt5_time_offset_s"),
                "mt5_time_offset_s",
            )
        except FixtureBlockedError as exc:
            _append_once(blockers, f"{exc}:{ticket}")
            continue
        horizon = (
            datetime.combine(
                tester_until,
                datetime.min.time(),
                tzinfo=timezone.utc,
            )
            - timedelta(seconds=offset)
        )
        if horizon <= opened:
            _append_once(blockers, f"invalid_alternative_horizon:{ticket}")
            continue

        direction = str(fixture.get("direction") or "")
        entry_price = float(_decimal(
            fixture.get("entry_price"),
            "entry_price",
        ))
        tp1 = float(_decimal(fixture.get("provider_tp1"), "provider_tp1"))
        tp2 = float(_decimal(fixture.get("provider_tp2"), "provider_tp2"))
        provider_sl = float(_decimal(
            fixture.get("provider_sl"),
            "provider_sl",
        ))
        if (
            direction == "BUY"
            and not (provider_sl < entry_price < tp2)
        ) or (
            direction == "SELL"
            and not (tp2 < entry_price < provider_sl)
        ):
            _append_once(blockers, f"invalid_policy_level_geometry:{ticket}")
            continue

        entry_ns = pd.Timestamp(opened).value
        horizon_ns = pd.Timestamp(horizon).value
        be_active = False
        saw_tick = False
        ticket_failed = False
        close_prepared = None
        close_absolute_position: int | None = None
        close_reason: str | None = None
        for day_value in _calendar_days(opened.date(), horizon.date()):
            frame, day_errors = load_day(day_value)
            if day_errors or frame is None:
                for blocker in day_errors or [
                    f"market_tick_evidence_missing:{day_value.isoformat()}"
                ]:
                    _append_once(blockers, f"{blocker}:{ticket}")
                ticket_failed = True
                break
            evidence = evidence_by_day[day_value]
            if day_value == opened.date():
                try:
                    evidence_offset = _int(
                        evidence.get("utc_offset_seconds"),
                        "entry_tick_offset",
                    )
                except FixtureBlockedError:
                    _append_once(
                        blockers,
                        f"invalid_entry_tick_offset:{ticket}",
                    )
                    ticket_failed = True
                    break
                if evidence_offset != offset:
                    _append_once(
                        blockers,
                        f"entry_tick_offset_mismatch:{ticket}",
                    )
                    ticket_failed = True
                    break

            prepared, tick_blockers = (
                simulation_oracle.prepare_tick_window(frame)
            )
            if tick_blockers or prepared is None:
                for blocker in tick_blockers or [
                    "invalid_market_tick_window"
                ]:
                    _append_once(blockers, f"{blocker}:{ticket}")
                ticket_failed = True
                break
            start = int(np.searchsorted(
                prepared.times_ns,
                entry_ns,
                side="left",
            ))
            stop = int(np.searchsorted(
                prepared.times_ns,
                horizon_ns,
                side="left",
            ))
            if start >= stop:
                continue
            saw_tick = True
            side_values = (
                prepared.bid[start:stop]
                if direction == "BUY"
                else prepared.ask[start:stop]
            )
            if direction == "BUY":
                tp2_mask = side_values >= tp2
                provider_sl_mask = side_values <= provider_sl
                tp1_mask = side_values >= tp1
                be_mask = side_values <= entry_price
            else:
                tp2_mask = side_values <= tp2
                provider_sl_mask = side_values >= provider_sl
                tp1_mask = side_values <= tp1
                be_mask = side_values >= entry_price

            close_position: int | None = None
            if policy_id == "all_tp2_keep_be" and be_active:
                exit_positions = np.flatnonzero(tp2_mask | be_mask)
                if len(exit_positions):
                    close_position = int(exit_positions[0])
                    if (
                        tp2_mask[close_position]
                        and be_mask[close_position]
                    ):
                        _append_once(
                            blockers,
                            f"same_tick_tp_be_ambiguity:{ticket}",
                        )
                        ticket_failed = True
                        break
                    close_reason = (
                        "tp2" if tp2_mask[close_position] else "be"
                    )
            else:
                initial_positions = np.flatnonzero(
                    tp2_mask | provider_sl_mask
                )
                initial_exit = (
                    int(initial_positions[0])
                    if len(initial_positions)
                    else None
                )
                close_position = initial_exit
                if initial_exit is not None:
                    if (
                        tp2_mask[initial_exit]
                        and provider_sl_mask[initial_exit]
                    ):
                        _append_once(
                            blockers,
                            f"same_tick_tp_sl_ambiguity:{ticket}",
                        )
                        ticket_failed = True
                        break
                    close_reason = (
                        "tp2" if tp2_mask[initial_exit] else "sl"
                    )
                if policy_id == "all_tp2_keep_be":
                    tp1_positions = np.flatnonzero(tp1_mask)
                    tp1_position = (
                        int(tp1_positions[0])
                        if len(tp1_positions)
                        else None
                    )
                    if (
                        tp1_position is not None
                        and (
                            initial_exit is None
                            or tp1_position < initial_exit
                        )
                    ):
                        post_be_positions = np.flatnonzero(
                            tp2_mask[tp1_position + 1:]
                            | be_mask[tp1_position + 1:]
                        )
                        if len(post_be_positions):
                            close_position = (
                                tp1_position
                                + 1
                                + int(post_be_positions[0])
                            )
                            if (
                                tp2_mask[close_position]
                                and be_mask[close_position]
                            ):
                                _append_once(
                                    blockers,
                                    f"same_tick_tp_be_ambiguity:{ticket}",
                                )
                                ticket_failed = True
                                break
                            close_reason = (
                                "tp2"
                                if tp2_mask[close_position]
                                else "be"
                            )
                        else:
                            be_active = True
                            close_position = None
                            close_reason = None
            if close_position is not None and close_reason is not None:
                close_prepared = prepared
                close_absolute_position = start + close_position
                break

        if ticket_failed:
            continue
        if (
            close_prepared is None
            or close_absolute_position is None
            or close_reason is None
        ):
            if not saw_tick:
                _append_once(blockers, f"missing_ticks_after_entry:{ticket}")
            else:
                _append_once(blockers, f"alternative_horizon_open:{ticket}")
            continue
        prepared = close_prepared
        absolute_position = close_absolute_position
        close_ns = int(prepared.times_ns[absolute_position])
        close_utc = pd.Timestamp(
            close_ns,
            unit="ns",
            tz="UTC",
        ).to_pydatetime()
        close_day_evidence = evidence_by_day.get(close_utc.date())
        if not isinstance(close_day_evidence, dict):
            _append_once(
                blockers,
                f"missing_close_tick_evidence:{ticket}",
            )
            continue
        try:
            close_offset = _int(
                close_day_evidence.get("utc_offset_seconds"),
                "close_mt5_time_offset_s",
            )
        except FixtureBlockedError:
            _append_once(
                blockers,
                f"invalid_close_tick_offset:{ticket}",
            )
            continue
        close_time_msc = (
            close_ns // 1_000_000 + close_offset * 1000
        )
        touch_bid = float(prepared.bid[absolute_position])
        touch_ask = float(prepared.ask[absolute_position])
        close_price = (
            tp2
            if close_reason == "tp2"
            else (touch_bid if direction == "BUY" else touch_ask)
        )
        money = money_converter.convert_leg(
            direction=direction,
            open_price=entry_price,
            close_price=close_price,
            volume=fixture.get("volume"),
            open_time_utc=opened,
            close_time_utc=close_utc,
        )
        if money.get("status") != "verified":
            for blocker in money.get("blockers") or [
                "alternative_money_unverified"
            ]:
                _append_once(blockers, f"{blocker}:{ticket}")
            continue
        expected[ticket] = {
            "close_time_msc": close_time_msc,
            "close_price": _decimal_text(close_price, "close_price"),
            "close_reason": close_reason,
            "pnl_eur": str(_money(
                money.get("strategy_pnl"),
                "strategy_pnl",
            )),
            "touch_bid": _decimal_text(touch_bid, "touch_bid"),
            "touch_ask": _decimal_text(touch_ask, "touch_ask"),
            "close_time_utc": close_utc.isoformat(timespec="microseconds"),
            "money_conversion": money.get("conversion"),
        }

    if len(expected) != len(rows) and not blockers:
        _append_once(blockers, "alternative_oracle_ticket_set_mismatch")
    evidence = [
        evidence_by_day[day_value]
        for day_value in sorted(evidence_by_day)
    ]
    return expected, blockers, evidence


def certify_result(
    *,
    fixture_rows: Iterable[dict],
    fixture_manifest: dict,
    policy_id: str,
    result_rows: Iterable[dict],
    expected_alternative_rows: dict[int, dict] | None = None,
    alternative_oracle_evidence: dict | None = None,
) -> dict:
    """Certify a tester result or return explicit fail-closed blockers."""

    fixture_list = list(fixture_rows)
    result_list = list(result_rows)
    blockers: list[str] = []
    if policy_id not in POLICY_IDS:
        _append_once(blockers, "unsupported_policy")
    if fixture_manifest.get("fixture_sha256") != fixture_sha256(fixture_list):
        _append_once(blockers, "fixture_sha256_mismatch")

    fixture_by_ticket: dict[int, dict] = {}
    for fixture in fixture_list:
        ticket = _int(fixture.get("ticket"), "fixture_ticket")
        if ticket in fixture_by_ticket:
            _append_once(blockers, "duplicate_fixture_ticket")
        fixture_by_ticket[ticket] = fixture
    result_by_ticket = _result_ticket_map(result_list, blockers)
    if set(fixture_by_ticket) != set(result_by_ticket):
        _append_once(blockers, "ticket_set_mismatch")

    proofs: list[dict] = []
    overnight_tickets: list[int] = []
    for ticket in sorted(set(fixture_by_ticket) & set(result_by_ticket)):
        fixture = fixture_by_ticket[ticket]
        row = result_by_ticket[ticket]
        if row.get("schema_version") != SCHEMA_VERSION:
            _append_once(blockers, "unsupported_result_schema")
        if row.get("policy_id") != policy_id:
            _append_once(blockers, "policy_id_mismatch")
        if row.get("status") != "closed":
            _append_once(blockers, "result_ticket_not_closed")
        for result_field, fixture_field, blocker in (
            ("signal_id", "signal_id", "signal_id_mismatch"),
            ("direction", "direction", "direction_mismatch"),
            ("entry_time_msc", "entry_time_msc", "entry_time_mismatch"),
            ("source_sha256", "source_sha256", "source_sha256_mismatch"),
        ):
            if row.get(result_field) != fixture.get(fixture_field):
                _append_once(blockers, blocker)
        _compare_decimal(
            row,
            fixture,
            result_field="volume",
            fixture_field="volume",
            blocker="volume_mismatch",
            blockers=blockers,
        )
        _compare_decimal(
            row,
            fixture,
            result_field="entry_price",
            fixture_field="entry_price",
            blocker="entry_price_mismatch",
            blockers=blockers,
        )

        if policy_id == "observed_close":
            for result_field, fixture_field, blocker in (
                (
                    "close_time_msc",
                    "observed_close_time_msc",
                    "baseline_close_time_mismatch",
                ),
                (
                    "close_reason",
                    "observed_close_reason",
                    "baseline_close_reason_mismatch",
                ),
            ):
                if row.get(result_field) != fixture.get(fixture_field):
                    _append_once(blockers, blocker)
            _compare_decimal(
                row,
                fixture,
                result_field="close_price",
                fixture_field="observed_close_price",
                blocker="baseline_close_price_mismatch",
                blockers=blockers,
            )
            try:
                if _money(row.get("pnl_eur"), "pnl_eur") != _money(
                    fixture.get("observed_pnl_eur"),
                    "observed_pnl_eur",
                ):
                    _append_once(blockers, "baseline_pnl_mismatch")
            except FixtureBlockedError:
                _append_once(blockers, "baseline_pnl_mismatch")
        else:
            _validate_alternative_result(
                fixture=fixture,
                row=row,
                policy_id=policy_id,
                blockers=blockers,
            )
            if expected_alternative_rows is not None:
                expected = expected_alternative_rows.get(ticket)
                if expected is None:
                    _append_once(
                        blockers,
                        "alternative_oracle_ticket_missing",
                    )
                else:
                    _compare_alternative_oracle(
                        row=row,
                        expected=expected,
                        blockers=blockers,
                    )
            try:
                opened_day = _mt5_server_calendar_date(
                    fixture.get("entry_time_msc"),
                    "fixture_entry_time_msc",
                )
                closed_day = _mt5_server_calendar_date(
                    row.get("close_time_msc"),
                    "result_close_time_msc",
                )
            except FixtureBlockedError:
                _append_once(blockers, "invalid_result_close_time")
            else:
                if opened_day != closed_day:
                    overnight_tickets.append(ticket)
                    _append_once(
                        blockers,
                        "overnight_cost_model_unverified",
                    )

        proof = {
            "ticket": ticket,
            "fixture_source_sha256": fixture.get("source_sha256"),
            "result": row,
        }
        if (
            policy_id != "observed_close"
            and expected_alternative_rows is not None
        ):
            proof["oracle_expected"] = expected_alternative_rows.get(ticket)
        proofs.append(proof)

    result_total: Decimal | None = None
    if not blockers:
        try:
            total_rows = (
                result_list
                if expected_alternative_rows is None
                or policy_id == "observed_close"
                else expected_alternative_rows.values()
            )
            result_total = sum(
                (_money(row.get("pnl_eur"), "pnl_eur") for row in total_rows),
                Decimal("0.00"),
            )
        except FixtureBlockedError:
            _append_once(blockers, "invalid_result_pnl")
            result_total = None
    if (
        not blockers
        and policy_id == "observed_close"
        and result_total != _money(
            fixture_manifest.get("observed_pnl_eur"),
            "manifest_observed_pnl_eur",
        )
    ):
        _append_once(blockers, "baseline_total_mismatch")
        result_total = None

    status = "blocked"
    if not blockers:
        status = "certified" if policy_id == "observed_close" else "diagnostic"
    certificate_payload = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": policy_id,
        "fixture_sha256": fixture_manifest.get("fixture_sha256"),
        "proofs": proofs,
        "alternative_oracle_evidence": alternative_oracle_evidence,
    }
    oracle_verified = (
        policy_id != "observed_close"
        and expected_alternative_rows is not None
        and alternative_oracle_evidence is not None
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "policy_id": policy_id,
        "expected_tickets": len(fixture_by_ticket),
        "checked_tickets": len(result_by_ticket),
        "observed_pnl_eur": str(
            _money(
                fixture_manifest.get("observed_pnl_eur"),
                "manifest_observed_pnl_eur",
            )
        ),
        "result_pnl_eur": None if result_total is None else str(result_total),
        "blockers": blockers,
        "overnight_tickets": overnight_tickets,
        "conclusions_allowed": False,
        "oracle_status": (
            "not_applicable"
            if policy_id == "observed_close"
            else ("verified" if oracle_verified else "unverified")
        ),
        "oracle_evidence_sha256": (
            _canonical_sha256(alternative_oracle_evidence)
            if oracle_verified
            else None
        ),
        "certificate_sha256": (
            _canonical_sha256(certificate_payload) if not blockers else None
        ),
    }


def read_fixture(path: Path) -> list[dict]:
    """Read a fixture while restoring the types used by its fingerprint."""

    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        if tuple(reader.fieldnames or ()) != FIXTURE_COLUMNS:
            raise ValueError("invalid_fixture_columns")
        rows: list[dict] = []
        for raw in reader:
            row = dict(raw)
            for field in (
                "schema_version",
                "ticket",
                "entry_time_msc",
                "mt5_time_offset_s",
                "observed_close_time_msc",
            ):
                row[field] = _int(row.get(field), f"fixture_{field}")
            rows.append(row)
    return rows


def _load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with Path(path).open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid_jsonl:{path}:{line_number}"
                ) from exc
            if not isinstance(row, dict):
                raise ValueError(f"invalid_jsonl_row:{path}:{line_number}")
            rows.append(row)
    return rows


def _load_json_rows(path: Path) -> list[dict]:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if isinstance(payload, dict):
        payload = payload.get("rows")
    if not isinstance(payload, list) or not all(
        isinstance(row, dict) for row in payload
    ):
        raise ValueError(f"invalid_json_rows:{path}")
    return payload


def _close_reason_from_comment(comment: object) -> str:
    text = str(comment or "").lower()
    if text.startswith("[sl"):
        return "sl"
    if text.startswith("[tp"):
        return "tp"
    if text.startswith("[be"):
        return "be"
    if "bot_close" in text:
        return "bot_close"
    return "other"


def _target_tickets(replay_rows: Iterable[dict], day: date) -> list[int]:
    tickets: list[int] = []
    for trade in replay_rows:
        if _trade_day(trade) != day:
            continue
        for item in trade.get("tickets") or []:
            tickets.append(_int(item.get("ticket"), "ticket"))
    return sorted(tickets)


def _deal_value(deal: object, field: str, default: object = None) -> object:
    if isinstance(deal, dict):
        return deal.get(field, default)
    return getattr(deal, field, default)


def _replay_mt5_time_offset(
    replay_rows: Iterable[dict],
    day: date,
) -> int:
    offsets: set[int] = set()
    for trade in replay_rows:
        if _trade_day(trade) != day:
            continue
        values = [trade.get("mt5_time_offset_s")]
        values.extend(
            ticket.get("mt5_time_offset_s")
            for ticket in (trade.get("tickets") or [])
        )
        for value in values:
            if value is None:
                continue
            offsets.add(_int(value, "mt5_time_offset_s"))
    if not offsets:
        _block("missing_mt5_time_offset")
    if len(offsets) != 1:
        _block("inconsistent_mt5_time_offset")
    return offsets.pop()


def _ticket_set_sha256(tickets: Iterable[int]) -> str:
    return _canonical_sha256(sorted(int(ticket) for ticket in tickets))


def build_ticket_universe_proof(
    *,
    day: date,
    expected_tickets: Iterable[int],
    observed_tickets: Iterable[int],
    mt5_time_offset_s: int,
    source: str,
    stable_snapshots: int,
) -> dict:
    expected = sorted({_int(ticket, "expected_ticket") for ticket in expected_tickets})
    observed = sorted({_int(ticket, "observed_ticket") for ticket in observed_tickets})
    payload = {
        "schema_version": SCHEMA_VERSION,
        "day": day.isoformat(),
        "source": str(source),
        "symbol": "XAUUSD",
        "magic_numbers": sorted(BOT_MAGIC_NUMBERS),
        "mt5_time_offset_s": _int(
            mt5_time_offset_s,
            "mt5_time_offset_s",
        ),
        "stable_snapshots": _int(stable_snapshots, "stable_snapshots"),
        "expected_tickets": expected,
        "observed_tickets": observed,
        "expected_ticket_set_sha256": _ticket_set_sha256(expected),
        "observed_ticket_set_sha256": _ticket_set_sha256(observed),
    }
    payload["status"] = (
        "verified"
        if expected == observed and payload["stable_snapshots"] >= 2
        else "blocked"
    )
    payload["evidence_sha256"] = _canonical_sha256(payload)
    return payload


def _ticket_universe_blockers(
    proof: object,
    *,
    day: date,
    expected_tickets: Iterable[int],
) -> list[str]:
    if not isinstance(proof, dict):
        return ["ticket_universe_proof_missing"]
    blockers: list[str] = []
    expected = sorted({_int(ticket, "expected_ticket") for ticket in expected_tickets})
    evidence = dict(proof)
    evidence_sha256 = evidence.pop("evidence_sha256", None)
    if evidence_sha256 != _canonical_sha256(evidence):
        _append_once(blockers, "ticket_universe_proof_hash_mismatch")
    if proof.get("schema_version") != SCHEMA_VERSION:
        _append_once(blockers, "ticket_universe_proof_schema_mismatch")
    if proof.get("status") != "verified":
        _append_once(blockers, "ticket_universe_not_verified")
    if proof.get("day") != day.isoformat():
        _append_once(blockers, "ticket_universe_day_mismatch")
    if proof.get("symbol") != "XAUUSD":
        _append_once(blockers, "ticket_universe_symbol_mismatch")
    if proof.get("magic_numbers") != sorted(BOT_MAGIC_NUMBERS):
        _append_once(blockers, "ticket_universe_magic_mismatch")
    try:
        stable_snapshots = _int(
            proof.get("stable_snapshots"),
            "stable_snapshots",
        )
        proof_expected = sorted(
            _int(ticket, "proof_expected_ticket")
            for ticket in (proof.get("expected_tickets") or [])
        )
        proof_observed = sorted(
            _int(ticket, "proof_observed_ticket")
            for ticket in (proof.get("observed_tickets") or [])
        )
    except FixtureBlockedError:
        _append_once(blockers, "ticket_universe_proof_invalid")
    else:
        if stable_snapshots < 2:
            _append_once(blockers, "ticket_universe_history_unstable")
        if proof_expected != expected or proof_observed != expected:
            _append_once(blockers, "mt5_ticket_universe_mismatch")
        if (
            proof.get("expected_ticket_set_sha256")
            != _ticket_set_sha256(proof_expected)
            or proof.get("observed_ticket_set_sha256")
            != _ticket_set_sha256(proof_observed)
        ):
            _append_once(blockers, "ticket_universe_set_hash_mismatch")
    return blockers


def _deal_server_time_utc(
    deal: object,
    *,
    mt5_time_offset_s: int,
) -> datetime:
    time_msc = _int(_deal_value(deal, "time_msc"), "deal_time_msc")
    try:
        return (
            datetime.fromtimestamp(time_msc / 1000, tz=timezone.utc)
            - timedelta(seconds=mt5_time_offset_s)
        )
    except (OSError, OverflowError, ValueError):
        _block("invalid_deal_time_msc")


def build_mt5_history_bundle(
    *,
    replay_rows: Iterable[dict],
    day: date,
    deals: Iterable[object],
    stable_snapshots: int,
) -> tuple[list[dict], dict]:
    """Build history only after proving the full bot-ticket universe."""

    replay_list = list(replay_rows)
    target_tickets = _target_tickets(replay_list, day)
    if not target_tickets:
        _block("empty_ticket_universe", day.isoformat())
    offset = _replay_mt5_time_offset(replay_list, day)
    all_deals = sorted(
        list(deals),
        key=lambda deal: (
            _int(_deal_value(deal, "time_msc"), "deal_time_msc"),
            _int(_deal_value(deal, "ticket"), "deal_ticket"),
        ),
    )
    observed_tickets = sorted({
        _int(_deal_value(deal, "position_id"), "position_id")
        for deal in all_deals
        if _int(_deal_value(deal, "entry"), "deal_entry") == 0
        and str(_deal_value(deal, "symbol") or "") == "XAUUSD"
        and _int(_deal_value(deal, "magic", 0), "deal_magic")
        in BOT_MAGIC_NUMBERS
        and _deal_server_time_utc(
            deal,
            mt5_time_offset_s=offset,
        ).date() == day
    })
    proof = build_ticket_universe_proof(
        day=day,
        expected_tickets=target_tickets,
        observed_tickets=observed_tickets,
        mt5_time_offset_s=offset,
        source="mt5_full_history",
        stable_snapshots=stable_snapshots,
    )
    if proof["status"] != "verified":
        missing = sorted(set(target_tickets) - set(observed_tickets))
        extra = sorted(set(observed_tickets) - set(target_tickets))
        _block(
            "mt5_ticket_universe_mismatch",
            f"missing={missing},extra={extra}",
        )

    by_position: dict[int, list[object]] = {}
    for deal in all_deals:
        position_id = _int(
            _deal_value(deal, "position_id"),
            "position_id",
        )
        if position_id in target_tickets:
            by_position.setdefault(position_id, []).append(deal)

    rows: list[dict] = []
    for ticket in target_tickets:
        ticket_deals = by_position.get(ticket, [])
        opens = [
            deal for deal in ticket_deals
            if _int(_deal_value(deal, "entry"), "deal_entry") == 0
        ]
        closes = [
            deal for deal in ticket_deals
            if _int(_deal_value(deal, "entry"), "deal_entry") in (1, 3)
        ]
        if len(opens) != 1:
            _block("mt5_open_deal_count", f"{ticket}:{len(opens)}")
        if len(closes) != 1:
            _block("mt5_close_deal_count", f"{ticket}:{len(closes)}")
        open_deal = opens[0]
        close_deal = closes[0]
        direction = {
            0: "BUY",
            1: "SELL",
        }.get(_int(_deal_value(open_deal, "type"), "deal_type"))
        if direction is None:
            _block("mt5_open_direction", ticket)
        pnl_net = sum(
            (
                Decimal(str(float(_deal_value(deal, field, 0.0) or 0.0)))
                for deal in ticket_deals
                for field in ("profit", "swap", "commission", "fee")
            ),
            Decimal("0"),
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        rows.append({
            "ticket": ticket,
            "direction": direction,
            "volume": float(_deal_value(open_deal, "volume")),
            "open_time_msc": _int(
                _deal_value(open_deal, "time_msc"),
                "open_time_msc",
            ),
            "open_price": float(_deal_value(open_deal, "price")),
            "close_time_msc": _int(
                _deal_value(close_deal, "time_msc"),
                "close_time_msc",
            ),
            "close_price": float(_deal_value(close_deal, "price")),
            "close_reason": _close_reason_from_comment(
                _deal_value(close_deal, "comment")
            ),
            "pnl_net": float(pnl_net),
        })
    return rows, proof


def _deal_snapshot_sha256(deals: Iterable[object]) -> str:
    rows = [
        {
            field: _deal_value(deal, field)
            for field in (
                "ticket",
                "position_id",
                "entry",
                "type",
                "time_msc",
                "price",
                "volume",
                "profit",
                "swap",
                "commission",
                "fee",
                "magic",
                "symbol",
                "comment",
            )
        }
        for deal in deals
    ]
    return _canonical_sha256(rows)


def read_observed_history_from_mt5(
    *,
    replay_rows: Iterable[dict],
    day: date,
) -> tuple[list[dict], dict]:
    """Read every selected position directly from the connected MT5 history."""

    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise RuntimeError("MetaTrader5 package is not installed") from exc

    replay_list = list(replay_rows)
    tickets = _target_tickets(replay_list, day)
    if not tickets:
        _block("empty_ticket_universe", day.isoformat())
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        from_utc = datetime.combine(
            day - timedelta(days=1),
            datetime.min.time(),
            tzinfo=timezone.utc,
        )
        close_dates = [
            _parse_datetime(row.get("close_dt_utc"), "close_dt_utc")
            for row in replay_list
            if _trade_day(row) == day and row.get("close_dt_utc")
        ]
        through_day = max(
            [day + timedelta(days=2)]
            + [closed.date() + timedelta(days=1) for closed in close_dates]
        )
        until_utc = datetime.combine(
            through_day,
            datetime.min.time(),
            tzinfo=timezone.utc,
        )
        previous_hash: str | None = None
        stable_snapshots = 0
        deals: list[object] = []
        for attempt in range(5):
            snapshot = mt5.history_deals_get(from_utc, until_utc)
            if snapshot is None:
                raise RuntimeError(
                    f"MT5 history query failed: {mt5.last_error()}"
                )
            deals = list(snapshot)
            current_hash = _deal_snapshot_sha256(deals)
            if current_hash == previous_hash:
                stable_snapshots += 1
            else:
                stable_snapshots = 1
                previous_hash = current_hash
            if stable_snapshots >= 2:
                break
            if attempt < 4:
                time.sleep(0.25)
        if stable_snapshots < 2:
            _block("mt5_history_not_stable")
        return build_mt5_history_bundle(
            replay_rows=replay_list,
            day=day,
            deals=deals,
            stable_snapshots=stable_snapshots,
        )
    finally:
        mt5.shutdown()


def _profile_name(day: date, policy_id: str) -> str:
    return f"telegram-replay-{day.isoformat()}-{policy_id}"


def _set_text(
    *,
    day: date,
    policy_id: str,
    fixture_hash: str,
) -> str:
    common_prefix = (
        f"{COMMON_RUN_FOLDER}\\{day.isoformat()}"
    )
    values = (
        f"InpFixtureFile={common_prefix}\\fixture.csv",
        f"InpResultFile={common_prefix}\\{policy_id}.csv",
        f"InpPolicy={policy_id}",
        f"InpFixtureSha256={fixture_hash}",
    )
    return "\r\n".join(values) + "\r\n"


def _tester_until_date(
    *,
    day: date,
    tester_until: date | None,
) -> date:
    resolved = tester_until or (day + timedelta(days=1))
    if resolved <= day:
        _block("invalid_tester_until", resolved.isoformat())
    return resolved


def _ini_text(
    *,
    day: date,
    policy_id: str,
    tester_until: date | None = None,
) -> str:
    resolved_until = _tester_until_date(
        day=day,
        tester_until=tester_until,
    )
    values = (
        "[Tester]",
        f"Expert={EA_TESTER_PATH.as_posix().replace('/', chr(92))}",
        "Symbol=XAUUSD",
        "Period=M1",
        "Optimization=0",
        "Model=4",
        "Dates=1",
        "FromDate=" + day.strftime("%Y.%m.%d"),
        "ToDate=" + resolved_until.strftime("%Y.%m.%d"),
        "ForwardMode=0",
        "Deposit=10000",
        "Currency=EUR",
        "ProfitInPips=0",
        "Leverage=500",
        "ExecutionMode=0",
        "OptimizationCriterion=0",
        "Visual=0",
    )
    return "\r\n".join(values) + "\r\n"


def _validate_expectations(
    manifest: dict,
    *,
    expected_signals: int | None,
    expected_tickets: int | None,
    expected_pnl_eur: Decimal | None,
) -> None:
    expectations = (
        ("signals", expected_signals, manifest.get("signals")),
        ("tickets", expected_tickets, manifest.get("tickets")),
    )
    for label, expected, actual in expectations:
        if expected is not None and actual != expected:
            _block(f"unexpected_{label}", f"{actual}!={expected}")
    if (
        expected_pnl_eur is not None
        and _money(manifest.get("observed_pnl_eur"), "observed_pnl_eur")
        != _money(expected_pnl_eur, "expected_pnl_eur")
    ):
        _block(
            "unexpected_observed_pnl_eur",
            f"{manifest.get('observed_pnl_eur')}!={expected_pnl_eur}",
        )


def prepare_run(
    *,
    day: date,
    replay_rows: Iterable[dict],
    observed_history: Iterable[dict],
    run_root: Path,
    mt5_data_dir: Path,
    common_files_dir: Path,
    compiled_ea: Path,
    expected_signals: int | None = None,
    expected_tickets: int | None = None,
    expected_pnl_eur: Decimal | None = None,
    selection: dict | None = None,
    tester_until: date | None = None,
    universe_proof: dict | None = None,
    market_tick_cache_dir: Path | None = None,
    money_tick_cache_dir: Path | None = None,
    money_contract_path: Path | None = None,
) -> dict:
    """Prepare one frozen tester run without invoking the terminal."""

    compiled_ea = Path(compiled_ea)
    if not compiled_ea.is_file():
        raise FileNotFoundError(f"compiled EA not found: {compiled_ea}")
    resolved_tester_until = _tester_until_date(
        day=day,
        tester_until=tester_until,
    )

    rows, manifest = build_fixture(
        replay_rows=replay_rows,
        day=day,
        observed_history=observed_history,
    )
    universe_blockers = _ticket_universe_blockers(
        universe_proof,
        day=day,
        expected_tickets=(row["ticket"] for row in rows),
    )
    if universe_blockers:
        _block(universe_blockers[0])
    _validate_expectations(
        manifest,
        expected_signals=expected_signals,
        expected_tickets=expected_tickets,
        expected_pnl_eur=expected_pnl_eur,
    )

    run_dir = Path(run_root) / day.isoformat()
    independent_evidence = _freeze_independent_evidence(
        run_dir=run_dir,
        day=day,
        tester_until=resolved_tester_until,
        market_tick_cache_dir=market_tick_cache_dir,
        money_tick_cache_dir=money_tick_cache_dir,
        money_contract_path=money_contract_path,
    )
    common_run_dir = (
        Path(common_files_dir) / COMMON_RUN_FOLDER / day.isoformat()
    )
    run_fixture, run_manifest = write_fixture(
        rows,
        manifest,
        output_dir=run_dir,
        stem="fixture",
    )
    common_fixture, common_manifest = write_fixture(
        rows,
        manifest,
        output_dir=common_run_dir,
        stem="fixture",
    )
    if run_fixture.read_bytes() != common_fixture.read_bytes():
        _block("fixture_copy_mismatch")
    if run_manifest.read_bytes() != common_manifest.read_bytes():
        _block("manifest_copy_mismatch")

    written_manifest = json.loads(
        run_manifest.read_text(encoding="utf-8")
    )
    mt5_data_dir = Path(mt5_data_dir)
    installed_ea = (
        mt5_data_dir / "MQL5" / "Experts" / EA_TESTER_PATH
    )
    _atomic_copy(compiled_ea, installed_ea)

    profile_dir = mt5_data_dir / "MQL5" / "Profiles" / "Tester"
    policies: dict[str, dict] = {}
    for policy_id in POLICY_ORDER:
        profile_name = _profile_name(day, policy_id)
        ini_path = profile_dir / f"{profile_name}.ini"
        set_path = profile_dir / f"{profile_name}.set"
        result_path = common_run_dir / f"{policy_id}.csv"
        result_path.unlink(missing_ok=True)
        _atomic_utf16(
            ini_path,
            _ini_text(
                day=day,
                policy_id=policy_id,
                tester_until=resolved_tester_until,
            ),
        )
        _atomic_utf16(
            set_path,
            _set_text(
                day=day,
                policy_id=policy_id,
                fixture_hash=written_manifest["fixture_sha256"],
            ),
        )
        policies[policy_id] = {
            "ini_path": str(ini_path.resolve()),
            "ini_sha256": _file_sha256(ini_path),
            "set_path": str(set_path.resolve()),
            "set_sha256": _file_sha256(set_path),
            "result_path": str(result_path.resolve()),
        }

    run_card = {
        "schema_version": SCHEMA_VERSION,
        "day": day.isoformat(),
        "signals": written_manifest["signals"],
        "tickets": written_manifest["tickets"],
        "observed_pnl_eur": written_manifest["observed_pnl_eur"],
        "tester_window": {
            "from_date": day.isoformat(),
            "until_exclusive": resolved_tester_until.isoformat(),
        },
        "selection": selection or {
            "cutoff_utc": None,
            "day_rows_seen": written_manifest["signals"],
            "rows_selected": written_manifest["signals"],
            "rows_after_cutoff": 0,
            "rows_opened_after_cutoff": 0,
            "rows_not_closed_by_cutoff": 0,
        },
        "ticket_universe": universe_proof,
        "independent_evidence": independent_evidence,
        "fixture_sha256": written_manifest["fixture_sha256"],
        "fixture_csv_sha256": written_manifest["csv_sha256"],
        "fixture_path": str(run_fixture.resolve()),
        "common_fixture_path": str(common_fixture.resolve()),
        "compiled_ea_path": str(installed_ea.resolve()),
        "compiled_ea_sha256": _file_sha256(installed_ea),
        "tester_model": 4,
        "optimization": False,
        "agents": {
            "local": True,
            "remote": False,
            "cloud": False,
        },
        "policies": policies,
    }
    _atomic_json(run_dir / "run_card.json", run_card)
    return run_card


def _hash_blockers(
    *,
    path_value: object,
    expected_sha256: object,
    missing: str,
    mismatch: str,
) -> list[str]:
    path = Path(str(path_value or ""))
    if not path.is_file():
        return [missing]
    if _file_sha256(path) != str(expected_sha256 or ""):
        return [mismatch]
    return []


def _independent_evidence_blockers(evidence: object) -> list[str]:
    if not isinstance(evidence, dict):
        return ["independent_evidence_missing"]
    blockers: list[str] = []
    unsigned = dict(evidence)
    evidence_sha256 = unsigned.pop("evidence_sha256", None)
    if evidence_sha256 != _canonical_sha256(unsigned):
        _append_once(blockers, "independent_evidence_hash_mismatch")
    if evidence.get("schema_version") != SCHEMA_VERSION:
        _append_once(blockers, "independent_evidence_schema_mismatch")
    if evidence.get("status") != "prepared":
        _append_once(blockers, "independent_evidence_missing")
    for kind in ("market", "money"):
        records = evidence.get(f"{kind}_days")
        if not isinstance(records, dict):
            _append_once(blockers, f"{kind}_tick_evidence_invalid")
            continue
        for day_text, record in records.items():
            if not isinstance(record, dict) or record.get("status") != "frozen":
                _append_once(
                    blockers,
                    f"{kind}_tick_evidence_incomplete:{day_text}",
                )
                continue
            for field, expected_field, missing, mismatch in (
                (
                    "parquet_path",
                    "parquet_sha256",
                    f"{kind}_tick_parquet_missing:{day_text}",
                    f"{kind}_tick_parquet_hash_mismatch:{day_text}",
                ),
                (
                    "contract_path",
                    "contract_sha256",
                    f"{kind}_tick_contract_missing:{day_text}",
                    f"{kind}_tick_contract_hash_mismatch:{day_text}",
                ),
            ):
                for blocker in _hash_blockers(
                    path_value=record.get(field),
                    expected_sha256=record.get(expected_field),
                    missing=missing,
                    mismatch=mismatch,
                ):
                    _append_once(blockers, blocker)
    for blocker in _hash_blockers(
        path_value=evidence.get("money_contract_path"),
        expected_sha256=evidence.get("money_contract_sha256"),
        missing="broker_money_contract_missing",
        mismatch="broker_money_contract_hash_mismatch",
    ):
        _append_once(blockers, blocker)
    return blockers


def _alternative_oracle_for_run(
    *,
    run_card: dict,
    fixture_rows: list[dict],
    policy_id: str,
) -> tuple[dict[int, dict] | None, list[str], dict | None]:
    evidence = run_card.get("independent_evidence")
    blockers = _independent_evidence_blockers(evidence)
    if blockers or not isinstance(evidence, dict):
        return None, blockers, None

    import pandas as pd
    import broker_money
    import simulation_oracle

    market_records = evidence["market_days"]
    money_records = evidence["money_days"]
    market_cache = simulation_oracle.IndependentTickCache(
        Path(evidence["market_tick_cache_path"]),
        expected_symbol="XAUUSD",
        require_market_session=False,
    )
    money_contract = broker_money.load_contract(
        Path(evidence["money_contract_path"])
    )
    conversion_symbol = str(
        (money_contract.get("conversion") or {}).get("symbol") or ""
    )
    money_cache = broker_money.VerifiedConversionTickCache(
        Path(evidence["money_tick_cache_path"]),
        symbol=conversion_symbol,
    )

    def market_loader(day_value: date):
        day_text = day_value.isoformat()
        if day_text not in market_records:
            return (
                pd.DataFrame(),
                None,
                [f"market_tick_day_not_frozen:{day_text}"],
            )
        return market_cache.load_day(day_value)

    def money_loader(day_value: date):
        day_text = day_value.isoformat()
        if day_text not in money_records:
            return (
                pd.DataFrame(),
                f"money_tick_day_not_frozen:{day_text}",
            )
        return money_cache.load_day(day_value)

    try:
        converter = broker_money.BrokerMoneyConverter(
            money_contract,
            quote_loader=money_loader,
        )
        tester_until = date.fromisoformat(
            str(
                (run_card.get("tester_window") or {}).get(
                    "until_exclusive"
                )
            )
        )
    except (OSError, UnicodeError, ValueError) as exc:
        return None, [
            f"independent_money_initialization_failed:{type(exc).__name__}"
        ], None

    expected, oracle_blockers, market_evidence = (
        build_alternative_oracle_rows(
            fixture_rows=fixture_rows,
            policy_id=policy_id,
            tester_until=tester_until,
            market_tick_loader=market_loader,
            money_converter=converter,
        )
    )
    if oracle_blockers:
        return None, oracle_blockers, None
    oracle_evidence = {
        "policy_id": policy_id,
        "frozen_evidence_sha256": evidence["evidence_sha256"],
        "market_tick_evidence": market_evidence,
        "money_contract_sha256": evidence["money_contract_sha256"],
        "expected_rows_sha256": _canonical_sha256(expected),
    }
    return expected, [], oracle_evidence


def _apply_certificate_blockers(
    certificate: dict,
    extra_blockers: Iterable[str],
) -> dict:
    result = dict(certificate)
    blockers = list(result.get("blockers") or [])
    for blocker in extra_blockers:
        _append_once(blockers, blocker)
    result["blockers"] = blockers
    if blockers:
        result["status"] = "blocked"
        result["result_pnl_eur"] = None
        result["certificate_sha256"] = None
        if result.get("oracle_status") != "not_applicable":
            result["oracle_status"] = "blocked"
    return result


def _missing_result_certificate(
    *,
    policy_id: str,
    run_card: dict,
    blocker: str = "result_file_missing",
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "blocked",
        "policy_id": policy_id,
        "expected_tickets": int(run_card["tickets"]),
        "checked_tickets": 0,
        "observed_pnl_eur": str(
            _money(
                run_card["observed_pnl_eur"],
                "run_card_observed_pnl_eur",
            )
        ),
        "result_pnl_eur": None,
        "blockers": [blocker],
        "overnight_tickets": [],
        "conclusions_allowed": False,
        "oracle_status": (
            "not_applicable"
            if policy_id == "observed_close"
            else "unavailable"
        ),
        "oracle_evidence_sha256": None,
        "certificate_sha256": None,
    }


def certify_run(run_dir: Path) -> dict:
    """Certify every result currently present for a prepared run."""

    run_dir = Path(run_dir)
    run_card = json.loads(
        (run_dir / "run_card.json").read_text(encoding="utf-8")
    )
    run_blockers: list[str] = []

    manifest: dict | None = None
    manifest_path = run_dir / "fixture.manifest.json"
    if not manifest_path.is_file():
        _append_once(run_blockers, "fixture_manifest_missing")
    else:
        try:
            loaded_manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, ValueError):
            _append_once(run_blockers, "fixture_manifest_invalid")
        else:
            if isinstance(loaded_manifest, dict):
                manifest = loaded_manifest
            else:
                _append_once(run_blockers, "fixture_manifest_invalid")

    for blocker in _hash_blockers(
        path_value=run_card.get("fixture_path"),
        expected_sha256=run_card.get("fixture_csv_sha256"),
        missing="fixture_file_missing",
        mismatch="fixture_csv_sha256_mismatch",
    ):
        _append_once(run_blockers, blocker)

    fixture_rows: list[dict] | None = None
    fixture_path = Path(str(run_card.get("fixture_path") or ""))
    if fixture_path.is_file():
        try:
            fixture_rows = read_fixture(fixture_path)
        except (OSError, UnicodeError, ValueError, csv.Error):
            _append_once(run_blockers, "fixture_file_invalid")

    if (
        manifest is not None
        and run_card.get("fixture_sha256")
        != manifest.get("fixture_sha256")
    ):
        _append_once(
            run_blockers,
            "run_card_fixture_sha256_mismatch",
        )

    if fixture_rows is not None:
        try:
            proof_day = date.fromisoformat(str(run_card.get("day")))
        except ValueError:
            _append_once(run_blockers, "run_card_day_invalid")
        else:
            for blocker in _ticket_universe_blockers(
                run_card.get("ticket_universe"),
                day=proof_day,
                expected_tickets=(
                    row["ticket"] for row in fixture_rows
                ),
            ):
                _append_once(run_blockers, blocker)

    for blocker in _hash_blockers(
        path_value=run_card.get("common_fixture_path"),
        expected_sha256=run_card.get("fixture_csv_sha256"),
        missing="common_fixture_file_missing",
        mismatch="common_fixture_csv_sha256_mismatch",
    ):
        _append_once(run_blockers, blocker)
    for blocker in _hash_blockers(
        path_value=run_card.get("compiled_ea_path"),
        expected_sha256=run_card.get("compiled_ea_sha256"),
        missing="compiled_ea_missing",
        mismatch="compiled_ea_sha256_mismatch",
    ):
        _append_once(run_blockers, blocker)

    policies = run_card.get("policies")
    if not isinstance(policies, dict):
        policies = {}
    if set(policies) != POLICY_IDS:
        _append_once(run_blockers, "run_card_policy_set_mismatch")

    certificates: dict[str, dict] = {}
    for policy_id in POLICY_ORDER:
        policy = policies.get(policy_id)
        if not isinstance(policy, dict):
            certificate = _apply_certificate_blockers(
                _missing_result_certificate(
                    policy_id=policy_id,
                    run_card=run_card,
                    blocker="policy_run_card_missing",
                ),
                run_blockers,
            )
            certificates[policy_id] = certificate
            _atomic_json(
                run_dir / f"{policy_id}.certificate.json",
                certificate,
            )
            continue

        policy_blockers = list(run_blockers)
        for blocker in _hash_blockers(
            path_value=policy.get("ini_path"),
            expected_sha256=policy.get("ini_sha256"),
            missing="ini_file_missing",
            mismatch="ini_sha256_mismatch",
        ):
            _append_once(policy_blockers, blocker)
        for blocker in _hash_blockers(
            path_value=policy.get("set_path"),
            expected_sha256=policy.get("set_sha256"),
            missing="set_file_missing",
            mismatch="set_sha256_mismatch",
        ):
            _append_once(policy_blockers, blocker)

        result_path = Path(policy["result_path"])
        if not result_path.is_file():
            certificate = _apply_certificate_blockers(
                _missing_result_certificate(
                    policy_id=policy_id,
                    run_card=run_card,
                ),
                policy_blockers,
            )
        else:
            try:
                result_rows = read_result(result_path)
            except (OSError, UnicodeError, ValueError, csv.Error):
                certificate = _apply_certificate_blockers(
                    _missing_result_certificate(
                        policy_id=policy_id,
                        run_card=run_card,
                        blocker="result_file_invalid",
                    ),
                    policy_blockers,
                )
            else:
                if fixture_rows is None or manifest is None:
                    certificate = _apply_certificate_blockers(
                        _missing_result_certificate(
                            policy_id=policy_id,
                            run_card=run_card,
                            blocker="fixture_evidence_unavailable",
                        ),
                        policy_blockers,
                    )
                else:
                    expected_alternative_rows = None
                    alternative_oracle_evidence = None
                    oracle_blockers: list[str] = []
                    if policy_id != "observed_close":
                        (
                            expected_alternative_rows,
                            oracle_blockers,
                            alternative_oracle_evidence,
                        ) = _alternative_oracle_for_run(
                            run_card=run_card,
                            fixture_rows=fixture_rows,
                            policy_id=policy_id,
                        )
                    certificate = certify_result(
                        fixture_rows=fixture_rows,
                        fixture_manifest=manifest,
                        policy_id=policy_id,
                        result_rows=result_rows,
                        expected_alternative_rows=(
                            expected_alternative_rows
                        ),
                        alternative_oracle_evidence=(
                            alternative_oracle_evidence
                        ),
                    )
                    certificate = _apply_certificate_blockers(
                        certificate,
                        [*policy_blockers, *oracle_blockers],
                    )
        certificates[policy_id] = certificate
        _atomic_json(
            run_dir / f"{policy_id}.certificate.json",
            certificate,
        )

    baseline = certificates["observed_close"]
    if baseline.get("status") != "certified":
        for policy_id in POLICY_ORDER:
            if policy_id == "observed_close":
                continue
            certificate = certificates[policy_id]
            if int(certificate.get("checked_tickets") or 0) <= 0:
                continue
            certificate = _apply_certificate_blockers(
                certificate,
                ["observed_baseline_not_certified"],
            )
            certificates[policy_id] = certificate
            _atomic_json(
                run_dir / f"{policy_id}.certificate.json",
                certificate,
            )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "day": run_card["day"],
        "fixture_sha256": run_card["fixture_sha256"],
        "integrity": {
            "status": "verified" if not run_blockers else "blocked",
            "blockers": run_blockers,
        },
        "certificates": certificates,
    }
    _atomic_json(run_dir / "certification_summary.json", summary)
    return summary


def _default_mt5_data_dir() -> Path:
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise RuntimeError("MetaTrader5 package is not installed") from exc
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        info = mt5.terminal_info()
        if info is None or not getattr(info, "data_path", None):
            raise RuntimeError("MT5 terminal data path is unavailable")
        return Path(info.data_path)
    finally:
        mt5.shutdown()


def _default_common_files_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA is unavailable")
    return Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare and certify isolated MT5 signal replays."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--date", required=True)
    prepare.add_argument(
        "--replay-file",
        default=str(Path(__file__).parent / "runtime_data" / "replay_trades.jsonl"),
    )
    prepare.add_argument("--history-file")
    prepare.add_argument("--universe-proof-file")
    prepare.add_argument(
        "--market-tick-cache-dir",
        default=str(Path(__file__).parent / "data" / "ticks_cache"),
    )
    prepare.add_argument(
        "--money-tick-cache-dir",
        default=str(
            Path(__file__).parent / "data" / "money_ticks_cache"
        ),
    )
    prepare.add_argument(
        "--money-contract",
        default=str(
            Path(__file__).parent / "data" / "broker_money_contract.json"
        ),
    )
    prepare.add_argument("--mt5-data-dir")
    prepare.add_argument("--common-files-dir")
    prepare.add_argument("--compiled-ea")
    prepare.add_argument(
        "--run-root",
        default=str(
            Path(__file__).parent
            / "runtime_data"
            / "mt5_tester_runs"
        ),
    )
    prepare.add_argument("--expect-signals", type=int)
    prepare.add_argument("--expect-tickets", type=int)
    prepare.add_argument("--expect-pnl-eur")
    prepare.add_argument("--cutoff-utc")
    prepare.add_argument(
        "--tester-until",
        help="Exclusive Strategy Tester end date (YYYY-MM-DD)",
    )

    certify = subparsers.add_parser("certify")
    certify.add_argument("--run-dir", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "certify":
        summary = certify_run(Path(args.run_dir))
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    selected_day = date.fromisoformat(args.date)
    tester_until = (
        None
        if args.tester_until is None
        else date.fromisoformat(args.tester_until)
    )
    all_replay_rows = _load_jsonl(Path(args.replay_file))
    cutoff_utc = (
        None
        if args.cutoff_utc is None
        else _parse_datetime(args.cutoff_utc, "cutoff_utc")
    )
    replay_rows, selection = select_replay_rows(
        all_replay_rows,
        day=selected_day,
        cutoff_utc=cutoff_utc,
    )
    if args.history_file:
        if not args.universe_proof_file:
            _block("ticket_universe_proof_file_required")
        history_rows = _load_json_rows(Path(args.history_file))
        universe_proof = json.loads(
            Path(args.universe_proof_file).read_text(encoding="utf-8-sig")
        )
    else:
        history_rows, universe_proof = read_observed_history_from_mt5(
            replay_rows=replay_rows,
            day=selected_day,
        )
    mt5_data_dir = (
        Path(args.mt5_data_dir)
        if args.mt5_data_dir
        else _default_mt5_data_dir()
    )
    common_files_dir = (
        Path(args.common_files_dir)
        if args.common_files_dir
        else _default_common_files_dir()
    )
    compiled_ea = (
        Path(args.compiled_ea)
        if args.compiled_ea
        else (
            mt5_data_dir
            / "MQL5"
            / "Experts"
            / EA_TESTER_PATH
        )
    )
    run_card = prepare_run(
        day=selected_day,
        replay_rows=replay_rows,
        observed_history=history_rows,
        run_root=Path(args.run_root),
        mt5_data_dir=mt5_data_dir,
        common_files_dir=common_files_dir,
        compiled_ea=compiled_ea,
        expected_signals=args.expect_signals,
        expected_tickets=args.expect_tickets,
        expected_pnl_eur=(
            None
            if args.expect_pnl_eur is None
            else Decimal(args.expect_pnl_eur)
        ),
        selection=selection,
        tester_until=tester_until,
        universe_proof=universe_proof,
        market_tick_cache_dir=Path(args.market_tick_cache_dir),
        money_tick_cache_dir=Path(args.money_tick_cache_dir),
        money_contract_path=Path(args.money_contract),
    )
    print(json.dumps(run_card, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
