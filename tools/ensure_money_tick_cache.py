"""Ensure a verified conversion-symbol tick cache for account-currency replay."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from tools import ensure_replay_tick_cache as base
import runtime_paths

DATA_DIR = runtime_paths.active_data_dir(REPO_DIR)
DEFAULT_INPUT = DATA_DIR / "replay_trades.jsonl"
DEFAULT_CACHE_DIR = DATA_DIR / "money_ticks_cache"
DEFAULT_REFERENCE_CACHE = DATA_DIR / "ticks_cache"
DEFAULT_STATUS = DATA_DIR / "money_tick_cache_status.json"
DEFAULT_SYMBOL = "EURUSD"


def _classify_cache_days(
    day_windows: dict[date, tuple],
    *,
    cache_dir: Path,
    symbol: str,
) -> dict[str, list[date]]:
    """Classify structural validity and exact intraday coverage."""
    days = list(day_windows)
    present = [
        day for day in days
        if base._day_file(cache_dir, day).is_file()
    ]
    contracts = {
        day: base.load_valid_day_contract(
            cache_dir,
            day,
            expected_symbol=symbol,
        )
        for day in present
    }
    missing = [day for day in days if day not in set(present)]
    invalid = [day for day in present if contracts[day] is None]
    structurally_valid = [
        day for day in present if contracts[day] is not None
    ]
    cached = [
        day
        for day in structurally_valid
        if base.coverage_satisfies_window(
            contracts[day],
            day_windows[day][0],
            day_windows[day][1],
        )
    ]
    incomplete = [
        day for day in structurally_valid if day not in set(cached)
    ]
    refresh = sorted({*missing, *invalid, *incomplete})
    return {
        "cached": cached,
        "missing": missing,
        "invalid": invalid,
        "incomplete": incomplete,
        "refresh": refresh,
    }


def _reference_evidence(reference_dir: Path, days: list[date]) -> dict[date, dict]:
    evidence: dict[date, dict] = {}
    for day in days:
        contract = base.load_valid_day_contract(reference_dir, day)
        if contract is None:
            continue
        evidence[day] = {
            "source_time_basis": base.SOURCE_TIME_BASIS,
            "utc_offset_seconds": contract["utc_offset_seconds"],
            "offset_detection_method": "reference_xauusd_tick_contract",
            "offset_reference": {
                "reference_cache": base._portable_path(reference_dir),
                "reference_day": day.isoformat(),
                "reference_contract_sha256": contract["parquet_sha256"],
            },
        }
    return evidence


def ensure_money_cache(
    trades: list[dict],
    *,
    cache_dir: Path,
    reference_cache_dir: Path,
    symbol: str = DEFAULT_SYMBOL,
    since=None,
    until=None,
    verbose: bool = True,
) -> dict:
    day_windows = base.required_day_windows(
        trades,
        since=since,
        until=until,
        pad_minutes=5,
    )
    days = list(day_windows)
    cache_dir.mkdir(parents=True, exist_ok=True)
    initial = _classify_cache_days(
        day_windows,
        cache_dir=cache_dir,
        symbol=symbol,
    )
    refresh_days = initial["refresh"]
    removed = base.refresh_cache_days(refresh_days, cache_dir=cache_dir)
    if not refresh_days:
        return {
            "ok": True,
            "symbol": symbol,
            "required_days": [day.isoformat() for day in days],
            "cached_days": [day.isoformat() for day in initial["cached"]],
            "missing_days": [],
            "invalid_days": [],
            "incomplete_days": [],
            "initial_invalid_days": [],
            "initial_incomplete_days": [],
            "refresh_requested_days": [],
            "refresh_removed_days": [],
            "reference_evidence_days": [],
            "ensure_stats": {},
        }

    preloaded = _reference_evidence(reference_cache_dir, refresh_days)
    source = base.MT5TickSource(
        symbol,
        preloaded_time_evidence_by_day=preloaded,
    )
    try:
        source.prime_offsets(refresh_days)
        from mt5_tick_cache import TickCache
        cache = TickCache(source, cache_dir=cache_dir)
        stats = cache.bulk_ensure(refresh_days, verbose=verbose)
        for day in refresh_days:
            import pandas as pd
            frame = pd.read_parquet(
                base._day_file(cache_dir, day)
            )
            evidence = source.time_evidence_for_day(day)
            base.write_day_contract(
                cache_dir,
                day,
                time_evidence=evidence,
                semantic_validation={
                    "valid": True,
                    "anchors_checked": 0,
                    "anchors_matched": 0,
                    "errors": [],
                },
                coverage=base.build_tick_coverage(
                    frame,
                    day,
                    utc_offset_seconds=evidence["utc_offset_seconds"],
                ),
                symbol=symbol,
            )
    finally:
        source.shutdown()

    final = _classify_cache_days(
        day_windows,
        cache_dir=cache_dir,
        symbol=symbol,
    )
    return {
        "ok": not (
            final["missing"]
            or final["invalid"]
            or final["incomplete"]
        ),
        "symbol": symbol,
        "required_days": [day.isoformat() for day in days],
        "cached_days": [day.isoformat() for day in final["cached"]],
        "missing_days": [day.isoformat() for day in final["missing"]],
        "invalid_days": [day.isoformat() for day in final["invalid"]],
        "incomplete_days": [day.isoformat() for day in final["incomplete"]],
        "initial_invalid_days": [
            day.isoformat() for day in initial["invalid"]
        ],
        "initial_incomplete_days": [
            day.isoformat() for day in initial["incomplete"]
        ],
        "reference_evidence_days": [day.isoformat() for day in preloaded],
        "refresh_requested_days": [
            day.isoformat() for day in refresh_days
        ],
        "refresh_removed_days": [day.isoformat() for day in removed],
        "ensure_stats": stats,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument(
        "--reference-cache-dir",
        type=Path,
        default=DEFAULT_REFERENCE_CACHE,
    )
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    trades = base.load_jsonl(args.input)
    result = ensure_money_cache(
        trades,
        cache_dir=args.cache_dir,
        reference_cache_dir=args.reference_cache_dir,
        symbol=args.symbol,
        since=base._parse_dt(args.since),
        until=base._parse_dt(args.until),
        verbose=not args.quiet,
    )
    args.status.parent.mkdir(parents=True, exist_ok=True)
    args.status.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if not args.quiet:
        print(f"Money tick cache required days: {len(result['required_days'])}")
        print(f"Cached: {len(result['cached_days'])}")
        print(f"Invalid: {len(result['invalid_days'])}")
        print(f"Incomplete: {len(result['incomplete_days'])}")
        print(f"Missing: {len(result['missing_days'])}")
        print(f"Output: {args.status}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
