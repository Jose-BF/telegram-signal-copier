"""Certify Gold 555 entry decisions from observed and complete broker ticks."""

from __future__ import annotations

import argparse
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Iterable

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gold_555_live_candidate import CANDIDATE_FINGERPRINT  # noqa: E402
from research.gold_iterative.entry_watch_parity import (  # noqa: E402
    certify_entry_watch_parity,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--market-ticks", type=Path, required=True)
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--to-date", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    start = date.fromisoformat(args.from_date)
    end = date.fromisoformat(args.to_date)
    if end < start:
        raise SystemExit("--to-date must be on or after --from-date")

    event_rows = tuple(_selected_events(args.events, start=start, end=end))
    cache: dict[str, pd.DataFrame] = {}

    def load_ticks(day: str, reference_msc: int, expires_at: datetime):
        frame = cache.get(day)
        if frame is None:
            path = args.market_ticks / f"{day}.parquet"
            if not path.exists():
                raise ValueError(f"verified market ticks missing for {day}")
            frame = pd.read_parquet(
                path,
                columns=("bid", "ask", "source_time_msc", "time_utc"),
            )
            cache[day] = frame
        selected = frame[
            (frame["source_time_msc"] > int(reference_msc))
            & (frame["time_utc"] < pd.Timestamp(expires_at))
        ]
        return selected.itertuples(index=False)

    report = certify_entry_watch_parity(event_rows, tick_loader=load_ticks)
    report["scope"] = {
        "channel": "canal2",
        "live_strategy_id": "gold_now_555_v1",
        "live_strategy_fingerprint": CANDIDATE_FINGERPRINT,
        "from_date": args.from_date,
        "to_date": args.to_date,
    }
    report["sources"] = {
        "events_sha256": _sha256(args.events),
        "market_tick_files": {
            day: _sha256(args.market_ticks / f"{day}.parquet")
            for day in sorted(cache)
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        "Gold 555 entry parity: "
        f"trace={report['logged_sample_replay']['status']} | "
        f"full_ticks={report['full_tick_replay']['status']} | "
        f"outcomes={report['full_tick_replay']['outcome_matches']}/"
        f"{report['attempts']} | "
        f"triggers={report['full_tick_replay']['trigger_tick_matches']}/"
        f"{report['full_tick_replay']['confirmed_attempts']}"
    )
    print(f"Output: {args.output.resolve()}")
    return 0 if report["prospective_entry_trigger_allowed"] else 2


def _selected_events(
    path: Path,
    *,
    start: date,
    end: date,
) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not any(
                marker in line
                for marker in (
                    "gold_555_entry_watch_",
                    '"gold_555_first_leg_filled"',
                    '"market_fill_failed"',
                )
            ):
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
            if not isinstance(row, dict):
                continue
            event_name = str(row.get("ev") or "")
            fingerprint = str(row.get("strategy_fingerprint") or "")
            strategy_id = str(row.get("strategy_id") or "")
            if (
                event_name.startswith("gold_555_entry_watch_")
                and fingerprint != CANDIDATE_FINGERPRINT
            ):
                continue
            if (
                event_name in {"gold_555_first_leg_filled", "market_fill_failed"}
                and strategy_id != "gold_now_555_v1"
            ):
                continue
            day_text = str(row.get("ts") or "")[:10]
            try:
                day = date.fromisoformat(day_text)
            except ValueError:
                continue
            if start <= day <= end:
                yield row


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
