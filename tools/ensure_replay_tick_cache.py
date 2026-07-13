"""Check or backfill tick cache days required by replay trades."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = REPO_DIR / "data" / "replay_trades.jsonl"
DEFAULT_CACHE_DIR = REPO_DIR / "data" / "ticks_cache"
DEFAULT_STATUS = REPO_DIR / "data" / "replay_tick_cache_status.json"
TICK_TIME_CONTRACT = "mt5_utc_v2"


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


def write_day_contract(cache_dir: Path, day: date) -> Path:
    parquet_path = _day_file(cache_dir, day)
    if not parquet_path.is_file():
        raise FileNotFoundError(parquet_path)
    contract_path = _day_contract_file(cache_dir, day)
    contract_path.write_text(
        json.dumps({
            "tick_time_contract": TICK_TIME_CONTRACT,
            "time_basis": "UTC",
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
    if not (
        contract.get("tick_time_contract") == TICK_TIME_CONTRACT
        and contract.get("time_basis") == "UTC"
        and contract.get("parquet_sha256") == digest
    ):
        return None
    return {
        "day": day.isoformat(),
        "tick_time_contract": TICK_TIME_CONTRACT,
        "time_basis": "UTC",
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
    def __init__(self, symbol: str):
        import MetaTrader5 as mt5
        import pandas as pd

        self.mt5 = mt5
        self.pd = pd
        self.symbol = symbol
        if not mt5.initialize():
            raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"MT5 symbol_select failed for {symbol}: {mt5.last_error()}")

    def fetch_ticks(self, t_from_utc: datetime, t_to_utc: datetime):
        if t_from_utc.tzinfo is None or t_from_utc.utcoffset() is None:
            raise ValueError("t_from_utc must be timezone-aware")
        if t_to_utc.tzinfo is None or t_to_utc.utcoffset() is None:
            raise ValueError("t_to_utc must be timezone-aware")
        t_from_utc = t_from_utc.astimezone(timezone.utc)
        t_to_utc = t_to_utc.astimezone(timezone.utc)
        raw = self.mt5.copy_ticks_range(
            self.symbol,
            t_from_utc,
            t_to_utc,
            self.mt5.COPY_TICKS_ALL,
        )
        if raw is None or len(raw) == 0:
            return self.pd.DataFrame()
        df = self.pd.DataFrame(raw)
        df["time_utc"] = self.pd.to_datetime(
            df["time_msc"], unit="ms", utc=True)
        return df.sort_values("time_msc").reset_index(drop=True)

    def shutdown(self) -> None:
        self.mt5.shutdown()


def ensure_missing_days(
    days: list[date],
    *,
    cache_dir: Path,
    symbol: str,
    verbose: bool,
) -> dict:
    from mt5_tick_cache import TickCache

    source = MT5TickSource(symbol)
    try:
        cache = TickCache(source, cache_dir=cache_dir)
        return cache.bulk_ensure(days, verbose=verbose)
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
            )
            for day in ensure_days:
                write_day_contract(args.cache_dir, day)
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
