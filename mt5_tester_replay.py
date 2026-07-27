"""Deterministic bridge between executed-MT5 replay data and Strategy Tester."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import shutil
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Iterable, Sequence


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


def certify_result(
    *,
    fixture_rows: Iterable[dict],
    fixture_manifest: dict,
    policy_id: str,
    result_rows: Iterable[dict],
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
    for ticket in sorted(set(fixture_by_ticket) & set(result_by_ticket)):
        fixture = fixture_by_ticket[ticket]
        row = result_by_ticket[ticket]
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

        proofs.append({
            "ticket": ticket,
            "fixture_source_sha256": fixture.get("source_sha256"),
            "result": row,
        })

    result_total: Decimal | None = None
    if not blockers:
        try:
            result_total = sum(
                (_money(row.get("pnl_eur"), "pnl_eur") for row in result_list),
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
    }
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
        "conclusions_allowed": False,
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


def read_observed_history_from_mt5(
    *,
    replay_rows: Iterable[dict],
    day: date,
) -> list[dict]:
    """Read every selected position directly from the connected MT5 history."""

    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise RuntimeError("MetaTrader5 package is not installed") from exc

    tickets = _target_tickets(replay_rows, day)
    if not tickets:
        _block("empty_ticket_universe", day.isoformat())
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    rows: list[dict] = []
    try:
        for ticket in tickets:
            deals = list(mt5.history_deals_get(position=ticket) or ())
            deals.sort(key=lambda deal: int(deal.time_msc))
            opens = [deal for deal in deals if int(deal.entry) == 0]
            closes = [
                deal for deal in deals if int(deal.entry) in (1, 3)
            ]
            if len(opens) != 1:
                _block("mt5_open_deal_count", f"{ticket}:{len(opens)}")
            if len(closes) != 1:
                _block("mt5_close_deal_count", f"{ticket}:{len(closes)}")
            open_deal = opens[0]
            close_deal = closes[0]
            direction = {0: "BUY", 1: "SELL"}.get(int(open_deal.type))
            if direction is None:
                _block("mt5_open_direction", ticket)
            pnl_net = sum(
                (
                    Decimal(str(float(getattr(deal, field, 0.0) or 0.0)))
                    for deal in deals
                    for field in ("profit", "swap", "commission", "fee")
                ),
                Decimal("0"),
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            rows.append({
                "ticket": ticket,
                "direction": direction,
                "volume": str(open_deal.volume),
                "open_time_msc": int(open_deal.time_msc),
                "open_price": str(open_deal.price),
                "close_time_msc": int(close_deal.time_msc),
                "close_price": str(close_deal.price),
                "close_reason": _close_reason_from_comment(
                    close_deal.comment
                ),
                "pnl_net": str(pnl_net),
            })
    finally:
        mt5.shutdown()
    return rows


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


def _ini_text(*, day: date, policy_id: str) -> str:
    profile_name = _profile_name(day, policy_id)
    values = (
        "[Tester]",
        f"Expert={EA_TESTER_PATH.as_posix().replace('/', chr(92))}",
        f"ExpertParameters={profile_name}.set",
        "Symbol=XAUUSD",
        "Period=M1",
        "Model=4",
        "ExecutionMode=0",
        "Optimization=0",
        "OptimizationCriterion=0",
        "FromDate=" + day.strftime("%Y.%m.%d"),
        "ToDate=" + day.strftime("%Y.%m.%d"),
        "ForwardMode=0",
        "Deposit=10000",
        "Currency=EUR",
        "Leverage=1:500",
        "UseLocal=1",
        "UseRemote=0",
        "UseCloud=0",
        "Visual=0",
        "ShutdownTerminal=0",
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
) -> dict:
    """Prepare one frozen tester run without invoking the terminal."""

    compiled_ea = Path(compiled_ea)
    if not compiled_ea.is_file():
        raise FileNotFoundError(f"compiled EA not found: {compiled_ea}")

    rows, manifest = build_fixture(
        replay_rows=replay_rows,
        day=day,
        observed_history=observed_history,
    )
    _validate_expectations(
        manifest,
        expected_signals=expected_signals,
        expected_tickets=expected_tickets,
        expected_pnl_eur=expected_pnl_eur,
    )

    run_dir = Path(run_root) / day.isoformat()
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
            _ini_text(day=day, policy_id=policy_id),
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


def certify_run(run_dir: Path) -> dict:
    """Certify every result currently present for a prepared run."""

    run_dir = Path(run_dir)
    run_card = json.loads(
        (run_dir / "run_card.json").read_text(encoding="utf-8")
    )
    fixture_rows = read_fixture(run_dir / "fixture.csv")
    manifest = json.loads(
        (run_dir / "fixture.manifest.json").read_text(encoding="utf-8")
    )
    certificates: dict[str, dict] = {}
    for policy_id, policy in run_card["policies"].items():
        result_path = Path(policy["result_path"])
        if not result_path.is_file():
            continue
        certificate = certify_result(
            fixture_rows=fixture_rows,
            fixture_manifest=manifest,
            policy_id=policy_id,
            result_rows=read_result(result_path),
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
    replay_rows = _load_jsonl(Path(args.replay_file))
    history_rows = (
        _load_json_rows(Path(args.history_file))
        if args.history_file
        else read_observed_history_from_mt5(
            replay_rows=replay_rows,
            day=selected_day,
        )
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
    )
    print(json.dumps(run_card, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
