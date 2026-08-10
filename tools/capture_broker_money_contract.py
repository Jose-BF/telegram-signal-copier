"""Capture a versioned MT5 account/symbol money contract without private IDs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from math import isfinite
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

import runtime_paths


DEFAULT_OUTPUT = (
    runtime_paths.active_data_dir(REPO_DIR) / "broker_money_contract.json"
)
DEFAULT_EVENTS = (
    runtime_paths.active_data_dir(REPO_DIR) / "trade_events.jsonl"
)
SCHEMA_VERSION = 2
LIVE_VALUE_TOLERANCE = 1e-8
SWAP_MODEL_POINTS = "mt5_points_rollover_v1"
SWAP_BRACKET_MAX_SECONDS = 15 * 60
ZERO_MULTIPLIER_BRACKET_MAX_SECONDS = 72 * 3600
MQL_EVIDENCE_MAX_AGE_SECONDS = 180
MQL_EVIDENCE_RELATIVE_PATH = (
    Path("TelegramSignalCopier") / "broker_swap_evidence.csv"
)
WEEKDAYS = (
    "sunday",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
)
MQL_EVIDENCE_FIELDS = (
    "schema_version",
    "captured_server_epoch",
    "captured_gmt_epoch",
    "last_server_tick_epoch",
    "server_utc_offset_seconds",
    "server_tick_lag_seconds",
    "terminal_build",
    "account_server",
    "instrument_symbol",
    "swap_mode",
    "swap_long",
    "swap_short",
    "swap_rollover3days",
    "point",
    "contract_size",
    "currency_profit",
    *(f"swap_{weekday}" for weekday in WEEKDAYS),
)
EVENT_METADATA_FIELDS = {
    "schema_version",
    "event_id",
    "session_id",
    "ts",
    "monotonic_ns",
    "code_commit",
    "payload_sha256",
}


def _canonical_sha256(payload: dict) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def specification_sha256(specification: dict) -> str:
    return _canonical_sha256(specification)


def account_identity_sha256(*, server: str, login: object) -> str:
    return _canonical_sha256({
        "login": str(login),
        "server": str(server),
    })


def _event_payload_sha256(event: dict) -> str:
    semantic = {
        key: value
        for key, value in event.items()
        if key not in EVENT_METADATA_FIELDS
    }
    return _canonical_sha256(semantic)


def _finite_number(value, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"invalid {label}") from exc
    if not isfinite(parsed):
        raise RuntimeError(f"invalid {label}")
    return parsed


def _default_mql_evidence_path(mt5) -> Path:
    terminal = mt5.terminal_info()
    data_path = (
        None if terminal is None else getattr(terminal, "data_path", None)
    )
    if not data_path:
        raise RuntimeError("MT5 terminal data path unavailable")
    return (
        Path(str(data_path))
        / "MQL5"
        / "Files"
        / MQL_EVIDENCE_RELATIVE_PATH
    )


def _read_mql_evidence(path: Path) -> tuple[dict, str]:
    source = Path(path)
    if not source.is_file():
        raise RuntimeError("missing verified MQL5 broker swap evidence")
    raw = source.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
        rows = list(csv.DictReader(text.splitlines()))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise RuntimeError("invalid MQL5 broker swap evidence") from exc
    if len(rows) != 1:
        raise RuntimeError("invalid MQL5 broker swap evidence")
    row = rows[0]
    if set(row) != set(MQL_EVIDENCE_FIELDS):
        raise RuntimeError("invalid MQL5 broker swap evidence schema")
    return row, hashlib.sha256(raw).hexdigest()


def _integer(value, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"invalid MQL5 broker evidence {label}") from exc


def _evidence_number(value, label: str) -> float:
    try:
        return _finite_number(value, f"MQL5 broker evidence {label}")
    except RuntimeError as exc:
        raise RuntimeError(str(exc)) from exc


def _validated_mql_evidence(
    mt5,
    instrument,
    *,
    account_server: str,
    captured_at: datetime,
    path: Path | None,
) -> tuple[dict, dict]:
    evidence_path = path or _default_mql_evidence_path(mt5)
    row, evidence_sha256 = _read_mql_evidence(evidence_path)
    if _integer(row["schema_version"], "schema_version") != 1:
        raise RuntimeError("unsupported MQL5 broker swap evidence schema")
    if row["account_server"] != account_server:
        raise RuntimeError("MQL5 broker swap evidence account mismatch")
    if row["instrument_symbol"] != str(instrument.name):
        raise RuntimeError("MQL5 broker swap evidence symbol mismatch")

    captured_gmt = _integer(
        row["captured_gmt_epoch"],
        "captured_gmt_epoch",
    )
    age_seconds = captured_at.timestamp() - captured_gmt
    if (
        age_seconds < -5
        or age_seconds > MQL_EVIDENCE_MAX_AGE_SECONDS
    ):
        raise RuntimeError("stale MQL5 broker swap evidence")

    captured_server = _integer(
        row["captured_server_epoch"],
        "captured_server_epoch",
    )
    last_server_tick = _integer(
        row["last_server_tick_epoch"],
        "last_server_tick_epoch",
    )
    raw_offset = _integer(
        row["server_utc_offset_seconds"],
        "server_utc_offset_seconds",
    )
    if captured_server - captured_gmt != raw_offset:
        raise RuntimeError("inconsistent MQL5 broker UTC offset evidence")
    rounded_offset = int(round(raw_offset / 900.0) * 900)
    if (
        abs(raw_offset - rounded_offset) > 5
        or abs(rounded_offset) > 14 * 3600
    ):
        raise RuntimeError("invalid MQL5 broker UTC offset evidence")

    tick_lag = _integer(
        row["server_tick_lag_seconds"],
        "server_tick_lag_seconds",
    )
    if (
        tick_lag != captured_server - last_server_tick
        or tick_lag < 0
    ):
        raise RuntimeError("inconsistent MQL5 broker server-time evidence")
    mql_tick_fresh = tick_lag <= 300
    instrument_tick = mt5.symbol_info_tick(str(instrument.name))
    if instrument_tick is None:
        raise RuntimeError("missing Python server tick evidence")
    python_tick_epoch = _integer(
        getattr(instrument_tick, "time", None),
        "python_server_tick_epoch",
    )
    tick_advances = {
        "utc_epoch": python_tick_epoch - captured_gmt,
        "broker_server_epoch": python_tick_epoch - captured_server,
    }
    valid_tick_bases = [
        (basis, advance)
        for basis, advance in tick_advances.items()
        if -2 <= advance <= age_seconds + 5
    ]
    if not valid_tick_bases:
        raise RuntimeError("MQL5/Python server tick time mismatch")
    python_tick_basis, python_tick_advance = valid_tick_bases[0]

    cross_checks = {
        "swap_mode": (
            _integer(row["swap_mode"], "swap_mode"),
            int(getattr(instrument, "swap_mode")),
        ),
        "swap_rollover3days": (
            _integer(
                row["swap_rollover3days"],
                "swap_rollover3days",
            ),
            int(getattr(instrument, "swap_rollover3days")),
        ),
        "swap_long": (
            _evidence_number(row["swap_long"], "swap_long"),
            _finite_number(getattr(instrument, "swap_long"), "swap_long"),
        ),
        "swap_short": (
            _evidence_number(row["swap_short"], "swap_short"),
            _finite_number(getattr(instrument, "swap_short"), "swap_short"),
        ),
        "point": (
            _evidence_number(row["point"], "point"),
            _finite_number(getattr(instrument, "point"), "point"),
        ),
        "contract_size": (
            _evidence_number(row["contract_size"], "contract_size"),
            _finite_number(
                getattr(instrument, "trade_contract_size"),
                "trade_contract_size",
            ),
        ),
        "currency_profit": (
            row["currency_profit"],
            str(instrument.currency_profit),
        ),
    }
    for label, (native_value, python_value) in cross_checks.items():
        if isinstance(native_value, float):
            matches = abs(native_value - python_value) <= LIVE_VALUE_TOLERANCE
        else:
            matches = native_value == python_value
        if not matches:
            raise RuntimeError(
                f"MQL5/Python swap evidence mismatch: {label}"
            )

    specification = {
        "swap_mode": cross_checks["swap_mode"][0],
        "swap_long": cross_checks["swap_long"][0],
        "swap_short": cross_checks["swap_short"][0],
        "swap_rollover3days": cross_checks["swap_rollover3days"][0],
        "point": cross_checks["point"][0],
        "contract_size": cross_checks["contract_size"][0],
        "currency_profit": cross_checks["currency_profit"][0],
        "weekday_multipliers": {
            weekday: _evidence_number(
                row[f"swap_{weekday}"],
                f"swap_{weekday}",
            )
            for weekday in WEEKDAYS
        },
    }
    if specification["point"] <= 0:
        raise RuntimeError("invalid point")
    if specification["contract_size"] <= 0:
        raise RuntimeError("invalid trade_contract_size")
    time_evidence = {
        "source": "mql5_service_v1",
        "evidence_sha256": evidence_sha256,
        "terminal_build": _integer(row["terminal_build"], "terminal_build"),
        "captured_server_epoch": captured_server,
        "captured_gmt_epoch": captured_gmt,
        "last_server_tick_epoch": last_server_tick,
        "server_tick_lag_seconds": tick_lag,
        "mql_tick_fresh": mql_tick_fresh,
        "python_tick_epoch": python_tick_epoch,
        "python_tick_time_basis": python_tick_basis,
        "python_tick_advance_seconds": python_tick_advance,
        "evidence_age_seconds": round(age_seconds, 3),
        "utc_offset_seconds": rounded_offset,
    }
    return specification, time_evidence


def _swap_specification(
    mt5,
    instrument,
    *,
    account_server: str,
    captured_at: datetime,
    mql_evidence_path: Path | None,
) -> tuple[dict, dict]:
    specification, time_evidence = _validated_mql_evidence(
        mt5,
        instrument,
        account_server=account_server,
        captured_at=captured_at,
        path=mql_evidence_path,
    )
    return specification, time_evidence


def capture_swap_snapshot(
    mt5,
    instrument,
    *,
    account_server: str,
    account_fingerprint: str | None = None,
    captured_at: datetime,
    mql_evidence_path: Path | None = None,
) -> dict:
    specification, time_evidence = _swap_specification(
        mt5,
        instrument,
        account_server=account_server,
        captured_at=captured_at,
        mql_evidence_path=mql_evidence_path,
    )
    native_captured_at = datetime.fromtimestamp(
        int(time_evidence["captured_gmt_epoch"]),
        tz=timezone.utc,
    )
    return {
        "captured_at_utc": native_captured_at.isoformat(),
        "account_server": str(account_server),
        "account_fingerprint": account_fingerprint,
        "instrument_symbol": str(instrument.name),
        "time_evidence": time_evidence,
        "specification": specification,
        "specification_sha256": specification_sha256(specification),
    }


def snapshot_record_reason(
    current: dict,
    previous: dict | None,
    *,
    rollover_window_seconds: int = SWAP_BRACKET_MAX_SECONDS,
) -> str | None:
    if previous is None:
        return "startup"
    if (
        current.get("specification_sha256")
        != previous.get("specification_sha256")
    ):
        return "specification_changed"
    current_offset = (current.get("time_evidence") or {}).get(
        "utc_offset_seconds"
    )
    previous_offset = (previous.get("time_evidence") or {}).get(
        "utc_offset_seconds"
    )
    if current_offset != previous_offset:
        return "utc_offset_changed"
    captured = datetime.fromisoformat(
        str(current["captured_at_utc"]).replace("Z", "+00:00")
    ).astimezone(timezone.utc)
    server_time = captured + timedelta(seconds=int(current_offset))
    seconds = (
        server_time.hour * 3600
        + server_time.minute * 60
        + server_time.second
    )
    if (
        seconds <= rollover_window_seconds
        or seconds >= 24 * 3600 - rollover_window_seconds
    ):
        return "rollover_window"
    return None


def merge_swap_snapshots(*groups) -> list[dict]:
    unique: dict[tuple[str, str, str, str, int | None], dict] = {}
    for group in groups:
        for snapshot in group or []:
            if not isinstance(snapshot, dict):
                continue
            key = (
                str(snapshot.get("captured_at_utc") or ""),
                str(snapshot.get("specification_sha256") or ""),
                str(snapshot.get("account_fingerprint") or ""),
                str(snapshot.get("instrument_symbol") or ""),
                (snapshot.get("time_evidence") or {}).get(
                    "utc_offset_seconds"
                ),
            )
            if key[0] and key[1]:
                unique[key] = snapshot
    return sorted(
        unique.values(),
        key=lambda row: str(row["captured_at_utc"]),
    )


def load_event_snapshots(
    path: Path,
    *,
    account_server: str,
    account_fingerprint: str,
    instrument_symbol: str,
) -> list[dict]:
    source = Path(path)
    if not source.is_file():
        return []
    snapshots: list[dict] = []
    with source.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid broker snapshot event line {line_number}"
                ) from exc
            if event.get("ev") != "broker_money_contract_snapshot":
                continue
            snapshot = event.get("snapshot")
            if (
                isinstance(snapshot, dict)
                and not snapshot.get("account_fingerprint")
            ):
                # Contracts captured before schema-2 identity binding are not
                # safe to reuse, but they must not prevent a clean migration.
                continue
            if event.get("payload_sha256") != _event_payload_sha256(event):
                raise ValueError(
                    f"invalid broker snapshot event payload line {line_number}"
                )
            if not isinstance(snapshot, dict):
                raise ValueError(
                    f"invalid broker snapshot event line {line_number}"
                )
            specification = snapshot.get("specification")
            if (
                not isinstance(specification, dict)
                or snapshot.get("specification_sha256")
                != specification_sha256(specification)
            ):
                raise ValueError(
                    f"invalid broker snapshot hash line {line_number}"
                )
            if (
                snapshot.get("account_server") != account_server
                or snapshot.get("account_fingerprint")
                != account_fingerprint
            ):
                raise ValueError(
                    f"broker snapshot account mismatch line {line_number}"
                )
            if snapshot.get("instrument_symbol") != instrument_symbol:
                raise ValueError(
                    f"broker snapshot symbol mismatch line {line_number}"
                )
            time_evidence = snapshot.get("time_evidence") or {}
            evidence_hash = str(time_evidence.get("evidence_sha256") or "")
            if (
                time_evidence.get("source") != "mql5_service_v1"
                or len(evidence_hash) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in evidence_hash.lower()
                )
            ):
                raise ValueError(
                    f"invalid native broker evidence line {line_number}"
                )
            snapshots.append(snapshot)
    return merge_swap_snapshots(snapshots)


def _conversion_route(mt5, account_currency: str, profit_currency: str) -> dict:
    if account_currency == profit_currency:
        return {
            "symbol": None,
            "orientation": "identity",
            "positive_profit_side": "identity",
            "negative_profit_side": "identity",
            "max_quote_age_ms": 5000,
            "max_quote_interval_ms": 60000,
        }

    direct = f"{account_currency}{profit_currency}"
    if mt5.symbol_info(direct) is not None:
        if not mt5.symbol_select(direct, True):
            raise RuntimeError(f"cannot select conversion symbol {direct}")
        return {
            "symbol": direct,
            "orientation": "account_base_profit_quote",
            "positive_profit_side": "ask",
            "negative_profit_side": "bid",
            "max_quote_age_ms": 5000,
            "max_quote_interval_ms": 60000,
        }

    inverse = f"{profit_currency}{account_currency}"
    if mt5.symbol_info(inverse) is not None:
        if not mt5.symbol_select(inverse, True):
            raise RuntimeError(f"cannot select conversion symbol {inverse}")
        return {
            "symbol": inverse,
            "orientation": "profit_base_account_quote",
            "positive_profit_side": "bid",
            "negative_profit_side": "ask",
            "max_quote_age_ms": 5000,
            "max_quote_interval_ms": 60000,
        }
    raise RuntimeError(
        f"no conversion symbol for {profit_currency}->{account_currency}"
    )


def _live_tick_value_validation(mt5, instrument, conversion: dict) -> dict:
    profit_per_tick = (
        float(instrument.trade_contract_size)
        * float(instrument.trade_tick_size)
    )
    orientation = conversion["orientation"]
    if orientation == "identity":
        expected_profit = profit_per_tick
        expected_loss = profit_per_tick
        quote = None
    else:
        quote = mt5.symbol_info_tick(conversion["symbol"])
        if quote is None:
            raise RuntimeError(
                f"missing live tick for {conversion['symbol']}"
            )
        bid = float(quote.bid)
        ask = float(quote.ask)
        if not all(isfinite(value) and value > 0 for value in (bid, ask)):
            raise RuntimeError("invalid live conversion quote")
        if orientation == "account_base_profit_quote":
            expected_profit = profit_per_tick / ask
            expected_loss = profit_per_tick / bid
        else:
            expected_profit = profit_per_tick * bid
            expected_loss = profit_per_tick * ask

    actual_profit = float(instrument.trade_tick_value_profit)
    actual_loss = float(instrument.trade_tick_value_loss)
    profit_delta = actual_profit - expected_profit
    loss_delta = actual_loss - expected_loss
    return {
        "valid": (
            abs(profit_delta) <= LIVE_VALUE_TOLERANCE
            and abs(loss_delta) <= LIVE_VALUE_TOLERANCE
        ),
        "expected_tick_value_profit": expected_profit,
        "actual_tick_value_profit": actual_profit,
        "tick_value_profit_delta": profit_delta,
        "expected_tick_value_loss": expected_loss,
        "actual_tick_value_loss": actual_loss,
        "tick_value_loss_delta": loss_delta,
        "conversion_bid": None if quote is None else float(quote.bid),
        "conversion_ask": None if quote is None else float(quote.ask),
    }


def build_contract(
    mt5,
    *,
    instrument_symbol: str = "XAUUSD",
    captured_at: datetime | None = None,
    historical_snapshots=None,
    mql_evidence_path: Path | None = None,
) -> dict:
    account = mt5.account_info()
    if account is None:
        raise RuntimeError("MT5 account_info unavailable")
    instrument = mt5.symbol_info(instrument_symbol)
    if instrument is None:
        raise RuntimeError(f"MT5 symbol unavailable: {instrument_symbol}")
    if not mt5.symbol_select(instrument_symbol, True):
        raise RuntimeError(f"cannot select instrument {instrument_symbol}")

    account_currency = str(account.currency)
    account_server = str(account.server)
    account_fingerprint = account_identity_sha256(
        server=account_server,
        login=account.login,
    )
    profit_currency = str(instrument.currency_profit)
    conversion = _conversion_route(
        mt5,
        account_currency,
        profit_currency,
    )
    validation = _live_tick_value_validation(mt5, instrument, conversion)
    captured_at = (captured_at or datetime.now(timezone.utc)).astimezone(
        timezone.utc
    )
    swap_snapshot = capture_swap_snapshot(
        mt5,
        instrument,
        account_server=account_server,
        account_fingerprint=account_fingerprint,
        captured_at=captured_at,
        mql_evidence_path=mql_evidence_path,
    )
    swap_mode = swap_snapshot["specification"]["swap_mode"]
    return {
        "schema_version": SCHEMA_VERSION,
        "captured_at_utc": captured_at.isoformat(),
        "account": {
            "server": account_server,
            "fingerprint": account_fingerprint,
            "currency": account_currency,
            "currency_digits": int(account.currency_digits),
        },
        "instrument": {
            "symbol": str(instrument.name),
            "trade_calc_mode": int(instrument.trade_calc_mode),
            "contract_size": float(instrument.trade_contract_size),
            "tick_size": float(instrument.trade_tick_size),
            "currency_profit": profit_currency,
        },
        "conversion": conversion,
        "costs": {
            "commission_model": "observed_zero_intraday",
            "fee_model": "observed_zero_intraday",
            "swap_model": (
                SWAP_MODEL_POINTS
                if swap_mode == 1
                else f"unsupported_mt5_swap_mode_{swap_mode}"
            ),
            "rollover_clock": "broker_midnight",
            "snapshot_bracket_max_seconds": SWAP_BRACKET_MAX_SECONDS,
            "zero_multiplier_bracket_max_seconds": (
                ZERO_MULTIPLIER_BRACKET_MAX_SECONDS
            ),
        },
        "swap_snapshots": merge_swap_snapshots(
            historical_snapshots,
            [swap_snapshot],
        ),
        "live_validation": validation,
    }


def write_contract(contract: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(contract, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Capture the current MT5 money conversion contract"
    )
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    import MetaTrader5 as mt5

    if not mt5.initialize():
        if not args.quiet:
            print(f"MT5 initialize failed: {mt5.last_error()}")
        return 1
    try:
        contract = build_contract(
            mt5,
            instrument_symbol=args.symbol,
        )
        account = contract["account"]
        historical_snapshots = load_event_snapshots(
            args.events,
            account_server=account["server"],
            account_fingerprint=account["fingerprint"],
            instrument_symbol=contract["instrument"]["symbol"],
        )
        contract["swap_snapshots"] = merge_swap_snapshots(
            historical_snapshots,
            contract["swap_snapshots"],
        )
        write_contract(contract, args.output)
    except Exception as exc:
        if not args.quiet:
            print(f"Money contract capture failed: {exc}")
        return 1
    finally:
        mt5.shutdown()

    if not args.quiet:
        status = "verified" if contract["live_validation"]["valid"] else "invalid"
        print(f"Broker money contract: {status}")
        print(f"Output: {args.output}")
    return 0 if contract["live_validation"]["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

