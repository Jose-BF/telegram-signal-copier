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
DEFAULT_INPUT = REPO_DIR / "data" / "replay_trades.jsonl"
DEFAULT_CACHE_DIR = REPO_DIR / "data" / "ticks_cache"
DEFAULT_STATUS = REPO_DIR / "data" / "replay_tick_cache_status.json"
TICK_TIME_CONTRACT = "mt5_server_epoch_utc_v3"
SOURCE_TIME_BASIS = "mt5_server_epoch"
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
            fill_dt = _parse_dt(
                fill_event.get("ts") or ticket_row.get("open_dt_utc"))
            raw_price = fill_event.get("price")
            if raw_price is None:
                raw_price = ticket_row.get("open_price")
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


def _window_intersects(
    window: tuple[datetime, datetime],
    since: datetime | None,
    until: datetime | None,
) -> bool:
    start, end = window
    if since is not None and end < since:
        return False
    if until is not None and start > until:
        return False
    return True


def required_dates(
    trades: list[dict],
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    pad_minutes: int = 5,
) -> list[date]:
    days: set[date] = set()
    for trade in trades:
        window = _trade_window(trade, pad_minutes)
        if window is None or not _window_intersects(window, since, until):
            continue
        start, end = window
        days.update(_iter_days(start.date(), end.date()))
    return sorted(days)


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


def write_day_contract(
    cache_dir: Path,
    day: date,
    *,
    time_evidence: dict,
    semantic_validation: dict,
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
    contract_path = _day_contract_file(cache_dir, day)
    contract_path.write_text(
        json.dumps({
            "tick_time_contract": TICK_TIME_CONTRACT,
            "time_basis": "UTC",
            "source_time_basis": time_evidence["source_time_basis"],
            "utc_offset_seconds": int(time_evidence["utc_offset_seconds"]),
            "offset_detection_method": time_evidence["offset_detection_method"],
            "offset_reference": time_evidence["offset_reference"],
            "semantic_time_valid": validation["valid"],
            "anchor_validation": validation,
            "parquet_sha256": _file_sha256(parquet_path),
        }, indent=2) + "\n",
        encoding="utf-8",
    )
    return contract_path


def load_valid_day_contract(cache_dir: Path, day: date) -> dict | None:
    parquet_path = _day_file(cache_dir, day)
    contract_path = _day_contract_file(cache_dir, day)
    if not parquet_path.is_file() or not contract_path.is_file():
        return None
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    digest = _file_sha256(parquet_path)
    anchor_validation = contract.get("anchor_validation")
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
        and contract.get("parquet_sha256") == digest
    ):
        return None
    return {
        **contract,
        "day": day.isoformat(),
        "parquet_sha256": digest,
        "size_bytes": parquet_path.stat().st_size,
    }


def day_contract_valid(cache_dir: Path, day: date) -> bool:
    return load_valid_day_contract(cache_dir, day) is not None


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
) -> dict:
    days = required_dates(
        trades,
        since=since,
        until=until,
        pad_minutes=pad_minutes,
    )
    present = [day for day in days if _day_file(cache_dir, day).is_file()]
    cached = [day for day in present if day_contract_valid(cache_dir, day)]
    invalid = [day for day in present if day not in set(cached)]
    missing = [day for day in days if day not in set(present)]
    refresh_requested_days = refresh_requested_days or []
    refresh_removed_days = refresh_removed_days or []
    refresh_pending = bool(dry_run and refresh_requested_days)
    return {
        "ok": (
            not missing
            and not invalid
            and error is None
            and not refresh_pending
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tick_time_contract": TICK_TIME_CONTRACT,
        "time_basis": "UTC",
        "dry_run": dry_run,
        "ensure_attempted": ensure_attempted,
        "pad_minutes": pad_minutes,
        "cache_dir": _portable_path(cache_dir),
        "n_trades": len(trades),
        "required_days": [day.isoformat() for day in days],
        "cached_days": [day.isoformat() for day in cached],
        "invalid_days": [day.isoformat() for day in invalid],
        "missing_days": [day.isoformat() for day in missing],
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
    ):
        import MetaTrader5 as mt5
        import pandas as pd

        self.mt5 = mt5
        self.pd = pd
        self.symbol = symbol
        self.anchors_by_day = anchors_by_day or {}
        self.offset_candidates_seconds = tuple(
            offset_candidates_seconds or DEFAULT_OFFSET_CANDIDATES_SECONDS)
        self._time_evidence_by_day: dict[date, dict] = {}
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
            & (df["time_utc"] <= self.pd.Timestamp(t_to_utc))
        )
        return df.loc[mask].sort_values("time_utc").reset_index(drop=True)

    def shutdown(self) -> None:
        self.mt5.shutdown()


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
    source = MT5TickSource(symbol, anchors_by_day=anchors_by_day)
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
            day_contracts[day.isoformat()] = {
                "time_evidence": source.time_evidence_for_day(day),
                "semantic_validation": semantic_validation,
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
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--pad-minutes", type=int, default=5)
    parser.add_argument("--symbol", default="XAUUSD")
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
    args.cache_dir.mkdir(parents=True, exist_ok=True)
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
    )

    if args.ensure and not args.dry_run and (
        status["missing_days"] or status["invalid_days"]
    ):
        missing_days = [date.fromisoformat(day) for day in status["missing_days"]]
        invalid_days = [date.fromisoformat(day) for day in status["invalid_days"]]
        automatically_removed = refresh_cache_days(
            invalid_days,
            cache_dir=args.cache_dir,
        )
        refresh_removed_days = sorted(set(
            refresh_removed_days + automatically_removed
        ))
        ensure_days = sorted(set(missing_days + invalid_days))
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
            )

    write_status(status, args.status)
    if not args.quiet:
        print(f"Tick cache required days: {len(status['required_days'])}")
        print(f"Cached: {len(status['cached_days'])}")
        print(f"Invalid: {len(status['invalid_days'])}")
        print(f"Missing: {len(status['missing_days'])}")
        print(f"Output: {args.status}")
    return 0 if status["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
