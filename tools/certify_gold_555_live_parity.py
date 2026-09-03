"""Certify Gold 555 management against reconciled MT5 fills."""

from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.dubai_iterative.dataset import (  # noqa: E402
    VerifiedParquetTickSource,
    load_strategy_dataset,
)
from research.gold_iterative.contracts import gold_555_genome  # noqa: E402
from research.gold_iterative.live_parity import (  # noqa: E402
    certify_live_logic_mirror,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--market-ticks", type=Path, required=True)
    parser.add_argument("--conversion-ticks", type=Path, required=True)
    parser.add_argument("--money-contract", type=Path, required=True)
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--to-date", required=True)
    parser.add_argument("--max-hold-minutes", type=int, default=240)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    start = date.fromisoformat(args.from_date)
    end = date.fromisoformat(args.to_date)
    if end < start:
        raise SystemExit("--to-date must be on or after --from-date")

    genome = gold_555_genome()
    expected_live_fingerprint = (
        genome.source_strategy_fingerprint or genome.fingerprint
    )
    event_evidence = _live_555_event_evidence(
        args.events,
        expected_fingerprint=expected_live_fingerprint,
    )
    ledger_rows = tuple(
        _with_event_evidence(row, event_evidence.get(str(row.get("sig_id") or "")))
        for row in _jsonl(args.ledger)
        if _selected_555_row(
            row,
            event_evidence=event_evidence,
            start=start,
            end=end,
        )
    )
    selected_ids = {str(row.get("sig_id") or "") for row in ledger_rows}
    audit_rows = tuple(
        row for row in _jsonl(args.audit)
        if str(row.get("sig_id") or "") in selected_ids
    )
    money_contract = json.loads(args.money_contract.read_text(encoding="utf-8"))
    dataset = load_strategy_dataset(
        replay_path=args.replay,
        audit_path=args.audit,
        market_ticks=VerifiedParquetTickSource(
            args.market_ticks,
            expected_symbol="XAUUSD",
        ),
        conversion_ticks=VerifiedParquetTickSource(
            args.conversion_ticks,
            expected_symbol="EURUSD",
        ),
        money_contract=money_contract,
        channel="canal2",
        from_date=args.from_date,
        to_date=args.to_date,
        max_hold_minutes=args.max_hold_minutes,
        audit_reason_prefix="tick_replay_",
    )
    paths = tuple(path for path in dataset.paths if path.signal_id in selected_ids)
    report = certify_live_logic_mirror(
        paths=paths,
        actual_rows=ledger_rows,
        audit_rows=audit_rows,
        genome=genome,
    )
    report["scope"] = {
        "channel": "canal2",
        "live_strategy_id": "gold_now_555_v1",
        "from_date": args.from_date,
        "to_date": args.to_date,
    }
    report["sources"] = {
        "replay_sha256": _sha256(args.replay),
        "ledger_sha256": _sha256(args.ledger),
        "events_sha256": _sha256(args.events),
        "audit_sha256": _sha256(args.audit),
        "money_contract_sha256": _sha256(args.money_contract),
        "dataset_source_hashes": dict(dataset.source_hashes),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        "Gold 555 live parity: "
        f"{report['parity']['status']} | "
        f"MT5={report['actual_mt5']['net_eur']} EUR | "
        f"mirror={report['live_logic_mirror']['net_eur']} EUR | "
        f"signals={report['actual_mt5']['signals']}"
    )
    print(f"Output: {args.output.resolve()}")
    return 0 if report["management_replay_allowed"] else 2


def _selected_555_row(
    row: Mapping[str, Any],
    *,
    event_evidence: Mapping[str, Mapping[str, Any]],
    start: date,
    end: date,
) -> bool:
    if row.get("channel") != "canal2":
        return False
    signal_id = str(row.get("sig_id") or "")
    snapshot = row.get("strategy_snapshot") or {}
    is_555 = (
        isinstance(snapshot, Mapping)
        and snapshot.get("live_strategy_id") == "gold_now_555_v1"
    ) or signal_id in event_evidence
    if not is_555:
        return False
    day_text = str(row.get("signal_dt_utc") or row.get("day") or "")[:10]
    try:
        day = date.fromisoformat(day_text)
    except ValueError:
        return False
    return start <= day <= end


def _live_555_event_evidence(
    path: Path,
    *,
    expected_fingerprint: str,
) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    relevant = {
        "signal_received": "signal_received",
        "gold_555_entry_watch_started": "watch_started",
        "gold_555_entry_watch_expired": "watch_expired",
    }
    for row in _jsonl(path):
        marker = relevant.get(str(row.get("ev") or ""))
        if marker is None:
            continue
        strategy_id = str(
            row.get("live_strategy_id") or row.get("strategy_id") or ""
        )
        fingerprint = str(
            row.get("live_strategy_fingerprint")
            or row.get("strategy_fingerprint")
            or ""
        )
        if (
            strategy_id != "gold_now_555_v1"
            or fingerprint != expected_fingerprint
        ):
            continue
        signal_id = str(row.get("sig") or "")
        if not signal_id:
            continue
        item = evidence.setdefault(signal_id, {
            "signal_received": False,
            "watch_started": False,
            "watch_expired": False,
            "event_ids": [],
        })
        item[marker] = True
        event_id = str(row.get("event_id") or "")
        if event_id:
            item["event_ids"].append(event_id)
    for item in evidence.values():
        item["event_ids"] = sorted(set(item["event_ids"]))
        item["no_position_outcome_verified"] = all(
            item[name]
            for name in ("signal_received", "watch_started", "watch_expired")
        )
    return evidence


def _with_event_evidence(
    row: Mapping[str, Any],
    evidence: Mapping[str, Any] | None,
) -> dict[str, Any]:
    enriched = dict(row)
    if evidence is None:
        return enriched
    if not isinstance(enriched.get("strategy_snapshot"), Mapping):
        enriched["strategy_snapshot"] = {
            "live_strategy_id": "gold_now_555_v1",
            "live_strategy_fingerprint": (
                gold_555_genome().source_strategy_fingerprint
                or gold_555_genome().fingerprint
            ),
            "identity_source": "live_event_lineage",
        }
    enriched["no_position_outcome_verified"] = bool(
        evidence.get("no_position_outcome_verified")
    )
    enriched["live_event_evidence"] = dict(evidence)
    return enriched


def _jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"non-object JSONL row at {path}:{line_number}")
            yield value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
