"""Check or backfill tick cache days required by replay trades."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from broker_market_sessions import broker_session_close_utc
import runtime_paths


DATA_DIR = runtime_paths.active_data_dir(REPO_DIR)
DEFAULT_INPUT = DATA_DIR / "replay_trades.jsonl"
DEFAULT_CACHE_DIR = DATA_DIR / "ticks_cache"
DEFAULT_STATUS = DATA_DIR / "replay_tick_cache_status.json"
TICK_TIME_CONTRACT = "mt5_server_epoch_utc_v3"
SOURCE_TIME_BASIS = "mt5_server_epoch"
TICK_SOURCE_VERIFICATION = "full_day_vs_two_half_days_v1"
TICK_CONTENT_DIGEST = "time_bid_ask_sequence_sha256_v1"
ANCHOR_SEARCH_WINDOW_S = 3
ANCHOR_TIME_TOLERANCE_MS = 2_500
ANCHOR_PRICE_TOLERANCE = 0.10
DEFAULT_OFFSET_CANDIDATES_SECONDS = tuple(
    hours * 3600
    for hours in (0, 2, 3, 1, 4, -1, -2, -3, -4, 5, 6, 7, 8, 9, 10,
                  11, 12, 13, 14, -5, -6, -7, -8, -9, -10, -11, -12)
)


@dataclass(frozen=True)
class FillAnchor:
    signal_id: str
    ticket: int | None
    time_utc: datetime
    price: float
    quote_side: str


def extract_fill_anchors(trades: Iterable[dict]) -> dict[date, list[FillAnchor]]:
    """Build broker-clock anchors from the canonical MT5 opening fills."""
    by_day: dict[date, list[FillAnchor]] = {}
    seen: set[tuple] = set()
    for trade in trades:
        direction = str(trade.get("direction") or "").upper()
        if direction not in {"BUY", "SELL"}:
            continue
        quote_side = "ask" if direction == "BUY" else "bid"
        signal_id = str(trade.get("sig_id") or "unknown")
        for ticket_row in trade.get("tickets") or []:
            fill_event = ticket_row.get("fill_event") or {}
            # La hora canonica del deal viene del historial MT5. El timestamp
            # del evento es cuando termino order_send y puede llegar segundos
            # despues en sesiones lentas.
            fill_dt = _parse_dt(
                ticket_row.get("open_dt_utc") or fill_event.get("ts"))
            raw_price = ticket_row.get("open_price")
            if raw_price is None:
                raw_price = fill_event.get("price")
            try:
                price = float(raw_price)
            except (TypeError, ValueError):
                continue
            if fill_dt is None or price <= 0:
                continue
            raw_ticket = ticket_row.get("ticket")
            try:
                ticket = int(raw_ticket) if raw_ticket is not None else None
            except (TypeError, ValueError):
                ticket = None
            key = (signal_id, ticket, fill_dt.isoformat(), price, quote_side)
            if key in seen:
                continue
            seen.add(key)
            by_day.setdefault(fill_dt.date(), []).append(FillAnchor(
                signal_id=signal_id,
                ticket=ticket,
                time_utc=fill_dt,
                price=price,
                quote_side=quote_side,
            ))
    for anchors in by_day.values():
        anchors.sort(key=lambda row: (row.time_utc, row.ticket or 0))
    return dict(sorted(by_day.items()))


def validate_cached_day_anchors(
    ticks,
    anchors: Iterable[FillAnchor],
    *,
    max_time_delta_ms: int = ANCHOR_TIME_TOLERANCE_MS,
    max_price_delta: float = ANCHOR_PRICE_TOLERANCE,
) -> dict:
    """Verify that normalized UTC ticks reproduce known MT5 opening fills."""
    import pandas as pd

    anchors = list(anchors)
    result = {
        "valid": True,
        "anchors_checked": len(anchors),
        "anchors_matched": 0,
        "max_time_delta_ms": None,
        "max_price_delta": None,
        "errors": [],
    }
    if not anchors:
        return result
    if ticks is None or len(ticks) == 0 or "time_utc" not in ticks.columns:
        result["valid"] = False
        result["errors"] = ["fill_anchor_outside_tolerance"]
        return result

    frame = ticks.copy()
    frame["time_utc"] = pd.to_datetime(frame["time_utc"], utc=True)
    observed_time_deltas: list[int] = []
    observed_price_deltas: list[float] = []
    for anchor in anchors:
        if anchor.quote_side not in frame.columns:
            continue
        anchor_ts = pd.Timestamp(anchor.time_utc)
        deltas_ms = (
            (frame["time_utc"] - anchor_ts).abs().dt.total_seconds() * 1000
        )
        near = frame[deltas_ms <= max_time_delta_ms]
        if near.empty:
            continue
        price_deltas = (near[anchor.quote_side].astype(float) - anchor.price).abs()
        best_index = price_deltas.idxmin()
        best_price_delta = float(price_deltas.loc[best_index])
        best_time_delta = int(round(float(deltas_ms.loc[best_index])))
        if best_price_delta > max_price_delta:
            continue
        result["anchors_matched"] += 1
        observed_time_deltas.append(best_time_delta)
        observed_price_deltas.append(best_price_delta)

    if observed_time_deltas:
        result["max_time_delta_ms"] = max(observed_time_deltas)
        result["max_price_delta"] = round(max(observed_price_deltas), 6)
    if result["anchors_matched"] != result["anchors_checked"]:
        result["valid"] = False
        result["errors"] = ["fill_anchor_outside_tolerance"]
    return result


def ensure_repo_import_path() -> None:
    repo = str(REPO_DIR)
    if repo not in sys.path:
        sys.path.insert(0, repo)


ensure_repo_import_path()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iter_days(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _trade_window(trade: dict, pad_minutes: int) -> tuple[datetime, datetime] | None:
    opened = _parse_dt(trade.get("open_dt_utc") or trade.get("signal_dt_utc"))
    closed = _parse_dt(trade.get("close_dt_utc")) or opened
    if opened is None or closed is None:
        return None
    if closed < opened:
        opened, closed = closed, opened
    pad = timedelta(minutes=max(0, pad_minutes))
    return opened - pad, closed + pad


def required_dates(
    trades: list[dict],
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    pad_minutes: int = 5,
) -> list[date]:
    days: set[date] = set()
    for trade in selected_trades(
        trades,
        since=since,
        until=until,
        pad_minutes=pad_minutes,
    ):
        window = _trade_window(trade, pad_minutes)
        if window is None:
            continue
        start, end = window
        days.update(_iter_days(start.date(), end.date()))
    return sorted(days)


def required_provider_dates(
    catalog: dict,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    latency_scenarios_ms: Iterable[int] = (0,),
    offset_candidates_seconds: Iterable[int] | None = None,
) -> list[date]:
    """Return every UTC day a provider-first replay can request.

    The strategy farm asks for the trigger-day contract before it can derive
    the broker-session horizon. Consider every supported broker UTC offset so
    Sunday-to-Monday sessions and large latency scenarios cannot appear as
    late cache surprises.
    """
    import provider_trade_spec

    latencies = tuple(latency_scenarios_ms)
    if not latencies:
        raise ValueError("provider latency scenarios cannot be empty")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in latencies
    ):
        raise ValueError(
            "provider latency scenarios must be non-negative integers"
        )
    offsets = tuple(
        DEFAULT_OFFSET_CANDIDATES_SECONDS
        if offset_candidates_seconds is None
        else offset_candidates_seconds
    )
    if not offsets or any(
        isinstance(value, bool)
        or not isinstance(value, int)
        or abs(value) > 14 * 3600
        for value in offsets
    ):
        raise ValueError("provider UTC offset candidates are invalid")

    days: set[date] = set()
    for raw_signal in catalog.get("signals") or []:
        if not isinstance(raw_signal, dict):
            raise ValueError("provider catalog signals must be mappings")
        if raw_signal.get("record_type", "formal_signal") != "formal_signal":
            continue
        cohort = _parse_dt(
            raw_signal.get("first_observed_utc")
            or raw_signal.get("signal_ts_utc")
        )
        if cohort is None:
            continue
        if since is not None and cohort.date() < since.date():
            continue
        if until is not None and cohort.date() > until.date():
            continue

        signal = (
            raw_signal
            if raw_signal.get("record_type") == "formal_signal"
            else {**raw_signal, "record_type": "formal_signal"}
        )
        for latency_ms in latencies:
            spec = provider_trade_spec.build_trade_spec(
                signal,
                latency_ms=latency_ms,
                volume_per_leg=0.01,
            )
            trigger = spec.trigger_observed_utc
            if not spec.entry_ready or trigger is None:
                continue
            days.add(trigger.date())
            try:
                threshold = trigger + timedelta(milliseconds=latency_ms)
            except OverflowError:
                raise ValueError("provider latency threshold is out of range")
            for offset_seconds in offsets:
                horizon = broker_session_close_utc(
                    threshold,
                    utc_offset_seconds=offset_seconds,
                )
                if horizon is None or threshold >= horizon:
                    continue
                days.update(_iter_days(threshold.date(), horizon.date()))
    return sorted(days)


def selected_trades(
    trades: list[dict],
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    pad_minutes: int = 5,
) -> list[dict]:
    selected = []
    for trade in trades:
        window = _trade_window(trade, pad_minutes)
        if window is None:
            continue
        if since is not None or until is not None:
            cohort = _parse_dt(
                trade.get("signal_dt_utc") or trade.get("open_dt_utc")
            )
            if cohort is None:
                continue
            if since is not None and cohort.date() < since.date():
                continue
            if until is not None and cohort.date() > until.date():
                continue
        selected.append(trade)
    return selected


def required_day_windows(
    trades: list[dict],
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    pad_minutes: int = 5,
) -> dict[date, tuple[datetime, datetime]]:
    """Return the exact replay interval that must be covered for each UTC day."""
    windows: dict[date, tuple[datetime, datetime]] = {}
    for trade in selected_trades(
        trades,
        since=since,
        until=until,
        pad_minutes=pad_minutes,
    ):
        trade_window = _trade_window(trade, pad_minutes)
        if trade_window is None:
            continue
        trade_start, trade_end = trade_window
        for day in _iter_days(trade_start.date(), trade_end.date()):
            day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
            day_end = day_start + timedelta(days=1)
            required_start = max(trade_start, day_start)
            required_end = min(trade_end, day_end)
            current = windows.get(day)
            if current is None:
                windows[day] = (required_start, required_end)
            else:
                windows[day] = (
                    min(current[0], required_start),
                    max(current[1], required_end),
                )
    return dict(sorted(windows.items()))


def _day_file(cache_dir: Path, day: date) -> Path:
    return cache_dir / f"{day.isoformat()}.parquet"


def _day_contract_file(cache_dir: Path, day: date) -> Path:
    return cache_dir / f"{day.isoformat()}.parquet.meta.json"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tick_content_sha256(ticks) -> str:
    """Fingerprint the exact ordered quote stream used by the replay."""
    import numpy as np
    import pandas as pd

    required = ("time_utc", "bid", "ask")
    missing = [column for column in required if column not in ticks.columns]
    if missing:
        raise ValueError(
            "tick content missing columns: " + ",".join(missing)
        )
    frame = ticks.loc[:, required].copy()
    frame["time_utc"] = pd.to_datetime(
        frame["time_utc"],
        utc=True,
        errors="coerce",
    )
    frame["bid"] = pd.to_numeric(frame["bid"], errors="coerce")
    frame["ask"] = pd.to_numeric(frame["ask"], errors="coerce")
    if (
        frame["time_utc"].isna().any()
        or frame["bid"].isna().any()
        or frame["ask"].isna().any()
    ):
        raise ValueError("tick content contains invalid values")

    digest = hashlib.sha256()
    digest.update(TICK_CONTENT_DIGEST.encode("ascii") + b"\0")
    digest.update(str(len(frame)).encode("ascii") + b"\0")
    arrays = (
        frame["time_utc"].astype("int64").to_numpy(dtype="<i8", copy=False),
        frame["bid"].to_numpy(dtype="<f8", copy=False),
        frame["ask"].to_numpy(dtype="<f8", copy=False),
    )
    for values in arrays:
        digest.update(np.ascontiguousarray(values).tobytes())
    return digest.hexdigest()


def verify_day_source_acquisition(
    source,
    day: date,
    primary_frame,
) -> dict:
    """Refetch a day in independent halves and compare its ordered quotes."""
    import pandas as pd

    day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    midpoint = day_start + timedelta(hours=12)
    day_end = day_start + timedelta(days=1)
    windows = ((day_start, midpoint), (midpoint, day_end))
    verification_frames = []
    for window_start, window_end in windows:
        frame = source.fetch_ticks(window_start, window_end)
        if frame is not None and not frame.empty:
            verification_frames.append(frame)
    if verification_frames:
        verification_frame = pd.concat(
            verification_frames,
            ignore_index=True,
        )
    else:
        verification_frame = pd.DataFrame(
            columns=["time_utc", "bid", "ask"],
        )

    errors: list[str] = []
    try:
        primary_digest = tick_content_sha256(primary_frame)
    except (TypeError, ValueError) as exc:
        primary_digest = None
        errors.append(f"invalid_primary_tick_content:{type(exc).__name__}")
    try:
        verification_digest = tick_content_sha256(verification_frame)
    except (TypeError, ValueError) as exc:
        verification_digest = None
        errors.append(
            f"invalid_verification_tick_content:{type(exc).__name__}"
        )
    primary_rows = int(len(primary_frame))
    verification_rows = int(len(verification_frame))
    if primary_rows != verification_rows:
        errors.append("source_row_count_mismatch")
    elif (
        primary_digest is not None
        and verification_digest is not None
        and primary_digest != verification_digest
    ):
        errors.append("source_content_mismatch")

    errors = list(dict.fromkeys(errors))
    return {
        "verified": not errors,
        "method": TICK_SOURCE_VERIFICATION,
        "content_digest": TICK_CONTENT_DIGEST,
        "symbol": str(source.symbol),
        "verified_at_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        "primary_query": {
            "from_utc": _utc_iso(day_start),
            "to_utc_exclusive": _utc_iso(day_end),
        },
        "verification_queries": [
            {
                "from_utc": _utc_iso(window_start),
                "to_utc_exclusive": _utc_iso(window_end),
            }
            for window_start, window_end in windows
        ],
        "primary_row_count": primary_rows,
        "verification_row_count": verification_rows,
        "primary_content_sha256": primary_digest,
        "verification_content_sha256": verification_digest,
        "errors": errors,
    }


def _is_sha256(value: object) -> bool:
    text = str(value or "")
    return bool(
        len(text) == 64
        and all(character in "0123456789abcdef" for character in text)
    )


def _normalized_source_verification(
    source_verification: dict | None,
) -> dict | None:
    if not isinstance(source_verification, dict):
        return None
    try:
        primary_rows = int(source_verification["primary_row_count"])
        verification_rows = int(
            source_verification["verification_row_count"]
        )
    except (KeyError, TypeError, ValueError):
        return None
    primary_digest = source_verification.get("primary_content_sha256")
    verification_digest = source_verification.get(
        "verification_content_sha256"
    )
    if (
        source_verification.get("method") != TICK_SOURCE_VERIFICATION
        or source_verification.get("content_digest") not in (
            None,
            TICK_CONTENT_DIGEST,
        )
        or not str(source_verification.get("symbol") or "")
        or primary_rows < 0
        or verification_rows < 0
        or not _is_sha256(primary_digest)
        or not _is_sha256(verification_digest)
    ):
        return None
    errors = [str(item) for item in source_verification.get("errors") or []]
    verified = source_verification.get("verified") is True
    if verified and (
        errors
        or primary_rows != verification_rows
        or primary_digest != verification_digest
    ):
        return None
    normalized = dict(source_verification)
    normalized.update({
        "verified": verified,
        "method": TICK_SOURCE_VERIFICATION,
        "content_digest": TICK_CONTENT_DIGEST,
        "symbol": str(source_verification["symbol"]),
        "primary_row_count": primary_rows,
        "verification_row_count": verification_rows,
        "primary_content_sha256": str(primary_digest),
        "verification_content_sha256": str(verification_digest),
        "errors": errors,
    })
    return normalized


def _utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def build_tick_coverage(
    ticks,
    day: date,
    *,
    captured_at: datetime | None = None,
    utc_offset_seconds: int | None = None,
) -> dict:
    """Describe the part of a requested UTC day proven by one MT5 download."""
    import pandas as pd

    day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)
    captured_at = (captured_at or datetime.now(timezone.utc)).astimezone(
        timezone.utc
    )
    first_tick = None
    last_tick = None
    row_count = int(len(ticks)) if ticks is not None else 0
    if row_count and "time_utc" in ticks.columns:
        times = pd.to_datetime(
            ticks["time_utc"], utc=True, errors="coerce",
        ).dropna()
        if not times.empty:
            first_tick = times.min().to_pydatetime()
            last_tick = times.max().to_pydatetime()

    complete_through = day_end if captured_at >= day_end else last_tick
    if (
        complete_through != day_end
        and utc_offset_seconds is not None
        and last_tick is not None
    ):
        session_close = broker_session_close_utc(
            day_start + timedelta(hours=12),
            utc_offset_seconds=utc_offset_seconds,
        )
        gap = (
            session_close - last_tick
            if session_close is not None
            else None
        )
        if (
            session_close is not None
            and day_start <= session_close <= day_end
            and captured_at >= session_close
            and gap is not None
            and timedelta(0) <= gap <= timedelta(minutes=5)
        ):
            complete_through = session_close

    return {
        "source_query_start_utc": _utc_iso(day_start),
        "source_query_end_utc": _utc_iso(day_end),
        "captured_at_utc": _utc_iso(captured_at),
        "first_tick_utc": _utc_iso(first_tick),
        "last_tick_utc": _utc_iso(last_tick),
        "complete_from_utc": _utc_iso(day_start),
        "complete_through_utc": _utc_iso(complete_through),
        "row_count": row_count,
    }

def _legacy_tick_coverage(parquet_path: Path, day: date) -> dict | None:
    """Safely infer old contracts from tick bounds without claiming future data."""
    try:
        import pandas as pd

        frame = pd.read_parquet(parquet_path, columns=["time_utc"])
    except Exception:
        return None
    day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)
    times = pd.to_datetime(frame.get("time_utc"), utc=True, errors="coerce").dropna()
    first_tick = times.min().to_pydatetime() if not times.empty else None
    last_tick = times.max().to_pydatetime() if not times.empty else None
    return {
        "source_query_start_utc": _utc_iso(day_start),
        "source_query_end_utc": _utc_iso(day_end),
        "captured_at_utc": _utc_iso(last_tick or day_start),
        "first_tick_utc": _utc_iso(first_tick),
        "last_tick_utc": _utc_iso(last_tick),
        "complete_from_utc": _utc_iso(day_start),
        "complete_through_utc": _utc_iso(last_tick),
        "row_count": int(len(frame)),
        "coverage_source": "legacy_parquet_bounds",
    }


def _normalized_coverage(coverage: dict | None, day: date) -> dict | None:
    if not isinstance(coverage, dict):
        return None
    day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)
    query_start = _parse_dt(coverage.get("source_query_start_utc"))
    query_end = _parse_dt(coverage.get("source_query_end_utc"))
    captured_at = _parse_dt(coverage.get("captured_at_utc"))
    complete_from = _parse_dt(coverage.get("complete_from_utc"))
    complete_through = _parse_dt(coverage.get("complete_through_utc"))
    first_tick = _parse_dt(coverage.get("first_tick_utc"))
    last_tick = _parse_dt(coverage.get("last_tick_utc"))
    try:
        row_count = int(coverage.get("row_count"))
    except (TypeError, ValueError):
        return None
    if (
        query_start is None
        or query_end is None
        or captured_at is None
        or complete_from is None
        or query_start > day_start
        or query_end < day_end
        or row_count < 0
        or (complete_through is not None and complete_through < complete_from)
    ):
        return None
    normalized = {
        "source_query_start_utc": _utc_iso(query_start),
        "source_query_end_utc": _utc_iso(query_end),
        "captured_at_utc": _utc_iso(captured_at),
        "first_tick_utc": _utc_iso(first_tick),
        "last_tick_utc": _utc_iso(last_tick),
        "complete_from_utc": _utc_iso(complete_from),
        "complete_through_utc": _utc_iso(complete_through),
        "row_count": row_count,
    }
    if coverage.get("coverage_source"):
        normalized["coverage_source"] = coverage["coverage_source"]
    return normalized


def coverage_satisfies_window(
    contract: dict | None,
    required_from: datetime,
    required_through: datetime,
) -> bool:
    coverage = (contract or {}).get("coverage")
    if not isinstance(coverage, dict):
        return False
    complete_from = _parse_dt(coverage.get("complete_from_utc"))
    complete_through = _parse_dt(coverage.get("complete_through_utc"))
    if (
        complete_from is not None
        and complete_through is not None
        and complete_from <= required_from
        and complete_through >= required_through
    ):
        return True

    captured_at = _parse_dt(coverage.get("captured_at_utc"))
    last_tick = _parse_dt(coverage.get("last_tick_utc"))
    utc_offset_seconds = (contract or {}).get("utc_offset_seconds")
    try:
        session_close = broker_session_close_utc(
            required_from,
            utc_offset_seconds=utc_offset_seconds,
        )
    except ValueError:
        return False
    if (
        complete_from is None
        or captured_at is None
        or last_tick is None
        or session_close is None
    ):
        return False
    gap = session_close - last_tick
    return bool(
        complete_from <= required_from
        and required_through <= session_close
        and captured_at >= session_close
        and timedelta(0) <= gap <= timedelta(minutes=5)
    )

def write_day_contract(
    cache_dir: Path,
    day: date,
    *,
    time_evidence: dict,
    semantic_validation: dict,
    coverage: dict | None = None,
    source_verification: dict | None = None,
    symbol: str | None = None,
) -> Path:
    parquet_path = _day_file(cache_dir, day)
    if not parquet_path.is_file():
        raise FileNotFoundError(parquet_path)
    required_evidence = {
        "source_time_basis",
        "utc_offset_seconds",
        "offset_detection_method",
        "offset_reference",
    }
    missing = sorted(required_evidence - set(time_evidence))
    if missing:
        raise ValueError(f"incomplete time evidence: {','.join(missing)}")
    validation = {
        "valid": bool(semantic_validation.get("valid")),
        "anchors_checked": int(semantic_validation.get("anchors_checked") or 0),
        "anchors_matched": int(semantic_validation.get("anchors_matched") or 0),
        "max_time_delta_ms": semantic_validation.get("max_time_delta_ms"),
        "max_price_delta": semantic_validation.get("max_price_delta"),
        "errors": list(semantic_validation.get("errors") or []),
    }
    if coverage is None:
        try:
            import pandas as pd

            coverage = build_tick_coverage(
                pd.read_parquet(parquet_path),
                day,
                utc_offset_seconds=time_evidence["utc_offset_seconds"],
            )
        except Exception as exc:
            raise ValueError("tick coverage evidence unavailable") from exc
    normalized_coverage = _normalized_coverage(coverage, day)
    if normalized_coverage is None:
        raise ValueError("invalid tick coverage evidence")
    contract_path = _day_contract_file(cache_dir, day)
    payload = {
            "tick_time_contract": TICK_TIME_CONTRACT,
            "time_basis": "UTC",
            "source_time_basis": time_evidence["source_time_basis"],
            "utc_offset_seconds": int(time_evidence["utc_offset_seconds"]),
            "offset_detection_method": time_evidence["offset_detection_method"],
            "offset_reference": time_evidence["offset_reference"],
            "semantic_time_valid": validation["valid"],
            "anchor_validation": validation,
            "coverage": normalized_coverage,
            "parquet_sha256": _file_sha256(parquet_path),
        }
    normalized_source_verification = _normalized_source_verification(
        source_verification
    )
    if normalized_source_verification is not None:
        payload["source_verification"] = normalized_source_verification
    if symbol:
        payload["symbol"] = str(symbol)
    contract_path.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    return contract_path


def load_valid_day_contract(
    cache_dir: Path,
    day: date,
    *,
    expected_symbol: str | None = None,
) -> dict | None:
    parquet_path = _day_file(cache_dir, day)
    contract_path = _day_contract_file(cache_dir, day)
    if not parquet_path.is_file() or not contract_path.is_file():
        return None
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if expected_symbol is not None and contract.get("symbol") != expected_symbol:
        return None
    digest = _file_sha256(parquet_path)
    anchor_validation = contract.get("anchor_validation")
    source_verification = _normalized_source_verification(
        contract.get("source_verification")
    )
    if not (
        contract.get("tick_time_contract") == TICK_TIME_CONTRACT
        and contract.get("time_basis") == "UTC"
        and contract.get("source_time_basis") == SOURCE_TIME_BASIS
        and isinstance(contract.get("utc_offset_seconds"), int)
        and abs(contract["utc_offset_seconds"]) <= 14 * 3600
        and bool(contract.get("offset_detection_method"))
        and isinstance(contract.get("offset_reference"), dict)
        and contract.get("semantic_time_valid") is True
        and isinstance(anchor_validation, dict)
        and anchor_validation.get("valid") is True
        and source_verification is not None
        and source_verification.get("verified") is True
        and source_verification.get("symbol") == contract.get("symbol")
        and contract.get("parquet_sha256") == digest
    ):
        return None
    coverage = _normalized_coverage(contract.get("coverage"), day)
    if coverage is None:
        coverage = _normalized_coverage(
            _legacy_tick_coverage(parquet_path, day),
            day,
        )
    return {
        **contract,
        "day": day.isoformat(),
        "coverage": coverage,
        "source_verification": source_verification,
        "parquet_sha256": digest,
        "contract_sha256": _file_sha256(contract_path),
        "size_bytes": parquet_path.stat().st_size,
    }


def day_contract_valid(
    cache_dir: Path,
    day: date,
    *,
    expected_symbol: str | None = None,
) -> bool:
    return load_valid_day_contract(
        cache_dir,
        day,
        expected_symbol=expected_symbol,
    ) is not None


def verified_cache_offset_candidates(
    cache_dir: Path,
    *,
    expected_symbol: str,
) -> list[int]:
    """Return only offsets backed by complete immutable day contracts."""
    offsets: set[int] = set()
    for parquet_path in sorted(cache_dir.glob("????-??-??.parquet")):
        try:
            day = date.fromisoformat(parquet_path.stem)
        except ValueError:
            continue
        contract = load_valid_day_contract(
            cache_dir,
            day,
            expected_symbol=expected_symbol,
        )
        if not isinstance(contract, dict):
            continue
        offset = contract.get("utc_offset_seconds")
        if (
            isinstance(offset, int)
            and not isinstance(offset, bool)
            and abs(offset) <= 14 * 3600
        ):
            offsets.add(offset)
    return sorted(offsets)


def refresh_cache_days(days: list[date], *, cache_dir: Path) -> list[date]:
    """Invalidate only the explicitly requested daily cache files."""
    removed: list[date] = []
    for day in sorted(set(days)):
        path = _day_file(cache_dir, day)
        contract_path = _day_contract_file(cache_dir, day)
        existed = path.is_file() or contract_path.is_file()
        if path.is_file():
            path.unlink()
        if contract_path.is_file():
            contract_path.unlink()
        if existed:
            removed.append(day)
    return removed


def _portable_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_DIR.resolve()).as_posix()
    except ValueError:
        return str(path)


def build_status(
    trades: list[dict],
    *,
    cache_dir: Path,
    since: datetime | None = None,
    until: datetime | None = None,
    pad_minutes: int = 5,
    dry_run: bool = False,
    ensure_attempted: bool = False,
    ensure_stats: dict | None = None,
    refresh_requested_days: list[date] | None = None,
    refresh_removed_days: list[date] | None = None,
    error: str | None = None,
    expected_symbol: str | None = None,
    additional_required_days: Iterable[date] = (),
) -> dict:
    scoped_trades = selected_trades(
        trades,
        since=since,
        until=until,
        pad_minutes=pad_minutes,
    )
    day_windows = required_day_windows(
        trades,
        since=since,
        until=until,
        pad_minutes=pad_minutes,
    )
    additional_days = sorted(set(additional_required_days))
    for day in additional_days:
        day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)
        current = day_windows.get(day)
        if current is None:
            day_windows[day] = (day_start, day_end)
        else:
            day_windows[day] = (
                min(current[0], day_start),
                max(current[1], day_end),
            )
    day_windows = dict(sorted(day_windows.items()))
    days = list(day_windows)
    present = [day for day in days if _day_file(cache_dir, day).is_file()]
    contracts = {
        day: load_valid_day_contract(
            cache_dir,
            day,
            expected_symbol=expected_symbol,
        )
        for day in present
    }
    invalid = [day for day in present if contracts[day] is None]
    structurally_valid = [day for day in present if contracts[day] is not None]
    cached = [
        day for day in structurally_valid
        if coverage_satisfies_window(
            contracts[day],
            day_windows[day][0],
            day_windows[day][1],
        )
    ]
    incomplete = [day for day in structurally_valid if day not in set(cached)]
    missing = [day for day in days if day not in set(present)]
    coverage_by_day = {}
    for day in days:
        required_from, required_through = day_windows[day]
        contract = contracts.get(day)
        coverage = (contract or {}).get("coverage") or {}
        if day in missing:
            coverage_status = "missing"
        elif day in invalid:
            coverage_status = "invalid"
        elif day in incomplete:
            coverage_status = "incomplete"
        else:
            coverage_status = "complete"
        coverage_by_day[day.isoformat()] = {
            "status": coverage_status,
            "required_from_utc": _utc_iso(required_from),
            "required_through_utc": _utc_iso(required_through),
            "complete_from_utc": coverage.get("complete_from_utc"),
            "complete_through_utc": coverage.get("complete_through_utc"),
        }
    refresh_requested_days = refresh_requested_days or []
    refresh_removed_days = refresh_removed_days or []
    refresh_pending = bool(dry_run and refresh_requested_days)
    return {
        "ok": (
            not missing
            and not invalid
            and not incomplete
            and error is None
            and not refresh_pending
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tick_time_contract": TICK_TIME_CONTRACT,
        "time_basis": "UTC",
        "expected_symbol": expected_symbol,
        "dry_run": dry_run,
        "ensure_attempted": ensure_attempted,
        "pad_minutes": pad_minutes,
        "cache_dir": _portable_path(cache_dir),
        "n_trades": len(scoped_trades),
        "scope": {
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
            "input_trades": len(trades),
            "selected_trades": len(scoped_trades),
            **(
                {
                    "additional_required_days": [
                        day.isoformat() for day in additional_days
                    ]
                }
                if additional_days
                else {}
            ),
        },
        "required_days": [day.isoformat() for day in days],
        "cached_days": [day.isoformat() for day in cached],
        "invalid_days": [day.isoformat() for day in invalid],
        "incomplete_days": [day.isoformat() for day in incomplete],
        "missing_days": [day.isoformat() for day in missing],
        "coverage_by_day": coverage_by_day,
        "ensure_stats": ensure_stats or {},
        "refresh_requested_days": [
            day.isoformat() for day in refresh_requested_days
        ],
        "refresh_removed_days": [
            day.isoformat() for day in refresh_removed_days
        ],
        "error": error,
    }


class MT5TickSource:
    def __init__(
        self,
        symbol: str,
        *,
        anchors_by_day: dict[date, list[FillAnchor]] | None = None,
        offset_candidates_seconds: Iterable[int] | None = None,
        preloaded_time_evidence_by_day: dict[date, dict] | None = None,
    ):
        import MetaTrader5 as mt5
        import pandas as pd

        self.mt5 = mt5
        self.pd = pd
        self.symbol = symbol
        self.anchors_by_day = anchors_by_day or {}
        self.offset_candidates_seconds = tuple(
            offset_candidates_seconds or DEFAULT_OFFSET_CANDIDATES_SECONDS)
        self._time_evidence_by_day: dict[date, dict] = dict(
            preloaded_time_evidence_by_day or {}
        )
        if not mt5.initialize():
            raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"MT5 symbol_select failed for {symbol}: {mt5.last_error()}")

    def _copy_raw(self, date_from: datetime, date_to: datetime):
        return self.mt5.copy_ticks_range(
            self.symbol,
            date_from,
            date_to,
            self.mt5.COPY_TICKS_ALL,
        )

    def _probe_anchor(self, anchor: FillAnchor, offset_seconds: int) -> dict | None:
        shift = timedelta(seconds=offset_seconds)
        window = timedelta(seconds=ANCHOR_SEARCH_WINDOW_S)
        raw = self._copy_raw(
            anchor.time_utc + shift - window,
            anchor.time_utc + shift + window,
        )
        if raw is None or len(raw) == 0:
            return None
        frame = self.pd.DataFrame(raw)
        if anchor.quote_side not in frame.columns or "time_msc" not in frame.columns:
            return None
        normalized_time = (
            self.pd.to_datetime(frame["time_msc"], unit="ms", utc=True)
            - self.pd.Timedelta(seconds=offset_seconds)
        )
        time_delta_ms = (
            (normalized_time - self.pd.Timestamp(anchor.time_utc))
            .abs()
            .dt.total_seconds()
            * 1000
        )
        price_delta = (frame[anchor.quote_side].astype(float) - anchor.price).abs()
        eligible = (
            (time_delta_ms <= ANCHOR_TIME_TOLERANCE_MS)
            & (price_delta <= ANCHOR_PRICE_TOLERANCE)
        )
        if not eligible.any():
            return None
        indexes = frame.index[eligible]
        best_index = min(
            indexes,
            key=lambda idx: (float(price_delta.loc[idx]), float(time_delta_ms.loc[idx])),
        )
        return {
            "signal_id": anchor.signal_id,
            "ticket": anchor.ticket,
            "anchor_time_utc": anchor.time_utc.isoformat(),
            "raw_time_msc": int(frame.loc[best_index, "time_msc"]),
            "quote_side": anchor.quote_side,
            "fill_price": anchor.price,
            "price_delta": round(float(price_delta.loc[best_index]), 6),
            "time_delta_ms": int(round(float(time_delta_ms.loc[best_index]))),
        }

    def _detect_offset_from_anchors(self, day: date) -> dict | None:
        anchors = list(self.anchors_by_day.get(day) or [])[:5]
        if not anchors:
            return None
        candidates: list[tuple[int, float, int, dict]] = []
        for offset_seconds in self.offset_candidates_seconds:
            matches = [
                self._probe_anchor(anchor, int(offset_seconds))
                for anchor in anchors
            ]
            matches = [match for match in matches if match is not None]
            if len(matches) != len(anchors):
                continue
            score = sum(
                float(match["price_delta"])
                + float(match["time_delta_ms"]) / 1000.0
                for match in matches
            )
            candidates.append((len(matches), score, int(offset_seconds), matches[0]))
        if not candidates:
            return None
        _matched, _score, offset_seconds, reference = min(
            candidates,
            key=lambda row: (-row[0], row[1], abs(row[2])),
        )
        return {
            "source_time_basis": SOURCE_TIME_BASIS,
            "utc_offset_seconds": offset_seconds,
            "offset_detection_method": "fill_anchor",
            "offset_reference": reference,
        }

    def _detect_offset_from_recent_tick(self, day: date) -> dict | None:
        # A live tick proves today's broker clock only. Applying that offset
        # to an arbitrary historical day could cross a broker DST change and
        # silently shift every replay tick by one hour.
        if day != datetime.now(timezone.utc).date():
            return None
        tick_getter = getattr(self.mt5, "symbol_info_tick", None)
        if tick_getter is None:
            return None
        tick = tick_getter(self.symbol)
        if tick is None:
            return None
        raw_seconds = getattr(tick, "time", None)
        if raw_seconds is None:
            raw_msc = getattr(tick, "time_msc", None)
            raw_seconds = float(raw_msc) / 1000 if raw_msc is not None else None
        if raw_seconds is None:
            return None
        observed = datetime.now(timezone.utc)
        server_as_utc = datetime.fromtimestamp(float(raw_seconds), tz=timezone.utc)
        offset_seconds = int(round(
            (server_as_utc - observed).total_seconds() / 3600.0
        ) * 3600)
        if offset_seconds not in self.offset_candidates_seconds:
            return None
        normalized = server_as_utc - timedelta(seconds=offset_seconds)
        age_seconds = abs((observed - normalized).total_seconds())
        if age_seconds > 300:
            return None
        return {
            "source_time_basis": SOURCE_TIME_BASIS,
            "utc_offset_seconds": offset_seconds,
            "offset_detection_method": "recent_live_tick",
            "offset_reference": {
                "observed_utc": observed.isoformat(),
                "raw_server_epoch": int(raw_seconds),
                "normalized_tick_utc": normalized.isoformat(),
                "age_seconds": round(age_seconds, 3),
            },
        }

    def _inherit_adjacent_evidence(self, day: date) -> dict | None:
        if not self._time_evidence_by_day:
            return None
        nearest_day = min(
            self._time_evidence_by_day,
            key=lambda known: abs((known - day).days),
        )
        distance_days = abs((nearest_day - day).days)
        if distance_days > 1:
            return None
        inherited = dict(self._time_evidence_by_day[nearest_day])
        inherited["offset_detection_method"] = "adjacent_verified_day"
        inherited["offset_reference"] = {
            "inherited_from_day": nearest_day.isoformat(),
            "distance_days": distance_days,
            "source_reference": inherited.get("offset_reference"),
        }
        return inherited

    def _resolve_time_evidence(self, day: date) -> dict:
        if day in self._time_evidence_by_day:
            return self._time_evidence_by_day[day]
        evidence = self._detect_offset_from_anchors(day)
        if evidence is None:
            evidence = self._inherit_adjacent_evidence(day)
        if evidence is None:
            evidence = self._detect_offset_from_recent_tick(day)
        if evidence is None:
            raise RuntimeError(
                f"cannot prove MT5 server offset for {day.isoformat()}"
            )
        self._time_evidence_by_day[day] = evidence
        return evidence

    def prime_offsets(self, days: Iterable[date]) -> None:
        ordered = sorted(set(days))
        for day in ordered:
            if self.anchors_by_day.get(day):
                self._resolve_time_evidence(day)
        for day in ordered:
            self._resolve_time_evidence(day)

    def time_evidence_for_day(self, day: date) -> dict:
        return dict(self._resolve_time_evidence(day))

    def fetch_ticks(self, t_from_utc: datetime, t_to_utc: datetime):
        if t_from_utc.tzinfo is None or t_from_utc.utcoffset() is None:
            raise ValueError("t_from_utc must be timezone-aware")
        if t_to_utc.tzinfo is None or t_to_utc.utcoffset() is None:
            raise ValueError("t_to_utc must be timezone-aware")
        t_from_utc = t_from_utc.astimezone(timezone.utc)
        t_to_utc = t_to_utc.astimezone(timezone.utc)
        day = t_from_utc.date()
        evidence = self._resolve_time_evidence(day)
        offset_seconds = int(evidence["utc_offset_seconds"])
        shift = timedelta(seconds=offset_seconds)
        raw = self._copy_raw(t_from_utc + shift, t_to_utc + shift)
        if raw is None or len(raw) == 0:
            return self.pd.DataFrame()
        df = self.pd.DataFrame(raw)
        df["source_time_msc"] = df["time_msc"].astype("int64")
        df["time_utc"] = self.pd.to_datetime(
            df["source_time_msc"], unit="ms", utc=True
        ) - self.pd.Timedelta(seconds=offset_seconds)
        df["time_msc"] = (
            df["time_utc"].astype("int64") // 1_000_000
        ).astype("int64")
        mask = (
            (df["time_utc"] >= self.pd.Timestamp(t_from_utc))
            & (df["time_utc"] < self.pd.Timestamp(t_to_utc))
        )
        return (
            df.loc[mask]
            .sort_values("time_utc", kind="stable")
            .reset_index(drop=True)
        )

    def shutdown(self) -> None:
        self.mt5.shutdown()


def load_adjacent_time_evidence(
    cache_dir: Path,
    days: Iterable[date],
    *,
    expected_symbol: str,
) -> dict[date, dict]:
    """Seed offset resolution from immutable neighbouring day contracts."""
    target_days = sorted(set(days))
    candidates = {
        adjacent
        for day in target_days
        for adjacent in (day - timedelta(days=1), day + timedelta(days=1))
        if adjacent not in target_days
    }
    evidence: dict[date, dict] = {}
    fields = (
        "source_time_basis",
        "utc_offset_seconds",
        "offset_detection_method",
        "offset_reference",
    )
    for candidate in sorted(candidates):
        contract = load_valid_day_contract(
            cache_dir,
            candidate,
            expected_symbol=expected_symbol,
        )
        if not isinstance(contract, dict):
            continue
        if any(field not in contract for field in fields):
            continue
        evidence[candidate] = {
            field: contract[field]
            for field in fields
        }
    return evidence


def ensure_missing_days(
    days: list[date],
    *,
    cache_dir: Path,
    symbol: str,
    verbose: bool,
    trades: list[dict],
) -> dict:
    from mt5_tick_cache import TickCache

    import pandas as pd

    anchors_by_day = extract_fill_anchors(trades)
    source = MT5TickSource(
        symbol,
        anchors_by_day=anchors_by_day,
        preloaded_time_evidence_by_day=load_adjacent_time_evidence(
            cache_dir,
            days,
            expected_symbol=symbol,
        ),
    )
    try:
        source.prime_offsets(days)
        cache = TickCache(source, cache_dir=cache_dir)
        anchored_days = [day for day in days if anchors_by_day.get(day)]
        unanchored_days = [day for day in days if day not in set(anchored_days)]
        stats = cache.bulk_ensure(
            anchored_days + unanchored_days,
            verbose=verbose,
        )
        day_contracts = {}
        for day in days:
            frame = pd.read_parquet(_day_file(cache_dir, day))
            semantic_validation = validate_cached_day_anchors(
                frame,
                anchors_by_day.get(day) or [],
            )
            time_evidence = source.time_evidence_for_day(day)
            day_contracts[day.isoformat()] = {
                "time_evidence": time_evidence,
                "semantic_validation": semantic_validation,
                "coverage": build_tick_coverage(
                    frame,
                    day,
                    utc_offset_seconds=time_evidence["utc_offset_seconds"],
                ),
                "source_verification": verify_day_source_acquisition(
                    source,
                    day,
                    frame,
                ),
            }
        return {**stats, "day_contracts": day_contracts}
    finally:
        source.shutdown()


def write_status(status: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check or download tick-cache days needed by replay trades")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument(
        "--catalog",
        type=Path,
        help=(
            "Optional provider catalog; includes unexecuted formal signals "
            "in the required-day preflight"
        ),
    )
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--pad-minutes", type=int, default=5)
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument(
        "--provider-latency-ms",
        action="append",
        type=int,
        dest="provider_latency_scenarios_ms",
        help="Repeat to cover every provider-entry latency scenario",
    )
    parser.add_argument("--ensure", action="store_true")
    parser.add_argument(
        "--refresh-day",
        action="append",
        default=[],
        type=date.fromisoformat,
        help="Invalidate one cached UTC day before --ensure (repeatable)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    trades = load_jsonl(args.input)
    since = _parse_dt(args.since)
    until = _parse_dt(args.until)
    latency_scenarios_ms = tuple(args.provider_latency_scenarios_ms or [0])
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    catalog = {}
    if args.catalog is not None:
        if not args.catalog.is_file():
            parser.error(f"provider catalog does not exist: {args.catalog}")
        catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
        if not isinstance(catalog, dict):
            parser.error("provider catalog root must be an object")
    verified_offsets = verified_cache_offset_candidates(
        args.cache_dir,
        expected_symbol=args.symbol,
    )
    additional_required_days = required_provider_dates(
        catalog,
        since=since,
        until=until,
        latency_scenarios_ms=latency_scenarios_ms,
        offset_candidates_seconds=(
            verified_offsets or DEFAULT_OFFSET_CANDIDATES_SECONDS
        ),
    )
    refresh_requested_days = sorted(set(args.refresh_day))
    refresh_removed_days = []
    if refresh_requested_days and not args.dry_run:
        refresh_removed_days = refresh_cache_days(
            refresh_requested_days,
            cache_dir=args.cache_dir,
        )

    status = build_status(
        trades,
        cache_dir=args.cache_dir,
        since=since,
        until=until,
        pad_minutes=args.pad_minutes,
        dry_run=args.dry_run,
        ensure_attempted=False,
        refresh_requested_days=refresh_requested_days,
        refresh_removed_days=refresh_removed_days,
        expected_symbol=args.symbol,
        additional_required_days=additional_required_days,
    )

    if args.ensure and not args.dry_run and (
        status["missing_days"]
        or status["invalid_days"]
        or status["incomplete_days"]
    ):
        missing_days = [date.fromisoformat(day) for day in status["missing_days"]]
        invalid_days = [date.fromisoformat(day) for day in status["invalid_days"]]
        incomplete_days = [
            date.fromisoformat(day) for day in status["incomplete_days"]
        ]
        automatically_removed = refresh_cache_days(
            invalid_days + incomplete_days,
            cache_dir=args.cache_dir,
        )
        refresh_removed_days = sorted(set(
            refresh_removed_days + automatically_removed
        ))
        ensure_days = sorted(set(missing_days + invalid_days + incomplete_days))
        try:
            stats = ensure_missing_days(
                ensure_days,
                cache_dir=args.cache_dir,
                symbol=args.symbol,
                verbose=not args.quiet,
                trades=trades,
            )
            for day in ensure_days:
                day_contract = (stats.get("day_contracts") or {}).get(
                    day.isoformat())
                if not day_contract:
                    raise RuntimeError(
                        f"missing UTC-v3 evidence for {day.isoformat()}"
                    )
                write_day_contract(
                    args.cache_dir,
                    day,
                    time_evidence=day_contract["time_evidence"],
                    semantic_validation=day_contract["semantic_validation"],
                    coverage=day_contract["coverage"],
                    source_verification=day_contract[
                        "source_verification"
                    ],
                    symbol=args.symbol,
                )
            status = build_status(
                trades,
                cache_dir=args.cache_dir,
                since=since,
                until=until,
                pad_minutes=args.pad_minutes,
                dry_run=False,
                ensure_attempted=True,
                ensure_stats=stats,
                refresh_requested_days=refresh_requested_days,
                refresh_removed_days=refresh_removed_days,
                expected_symbol=args.symbol,
                additional_required_days=additional_required_days,
            )
        except Exception as exc:
            status = build_status(
                trades,
                cache_dir=args.cache_dir,
                since=since,
                until=until,
                pad_minutes=args.pad_minutes,
                dry_run=False,
                ensure_attempted=True,
                refresh_requested_days=refresh_requested_days,
                refresh_removed_days=refresh_removed_days,
                error=f"{type(exc).__name__}: {str(exc)[:300]}",
                expected_symbol=args.symbol,
                additional_required_days=additional_required_days,
            )

    write_status(status, args.status)
    if not args.quiet:
        print(f"Tick cache required days: {len(status['required_days'])}")
        print(f"Cached: {len(status['cached_days'])}")
        print(f"Invalid: {len(status['invalid_days'])}")
        print(f"Incomplete: {len(status['incomplete_days'])}")
        print(f"Missing: {len(status['missing_days'])}")
        print(f"Output: {args.status}")
    return 0 if status["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
