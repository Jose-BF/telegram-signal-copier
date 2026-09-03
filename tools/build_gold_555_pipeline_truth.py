"""Build one unambiguous Gold 555 actual-versus-simulation report."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.gold_iterative.pipeline_truth import (  # noqa: E402
    build_pipeline_truth_report,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--management-parity", type=Path, required=True)
    parser.add_argument("--entry-watch-parity", type=Path, required=True)
    parser.add_argument("--prospective", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument(
        "--variant",
        default="deterministic_flat_cancel",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    management = _json(args.management_parity)
    entry_watch = _json(args.entry_watch_parity)
    prospective = _json(args.prospective)
    signal_ids = {
        str(row.get("signal_id") or "")
        for row in management.get("rows") or ()
        if str(row.get("signal_id") or "")
    }
    ledger = tuple(
        row for row in _jsonl(args.ledger)
        if str(row.get("sig_id") or "") in signal_ids
    )
    events = tuple(
        row for row in _jsonl(args.events)
        if str(row.get("sig") or "") in signal_ids
        and row.get("ev") == "market_fill_failed"
    )
    report = build_pipeline_truth_report(
        management_report=management,
        entry_watch_report=entry_watch,
        prospective_report=prospective,
        variant_name=args.variant,
        ledger_rows=ledger,
        event_rows=events,
    )
    report["sources"] = {
        "management_parity_sha256": _sha256(args.management_parity),
        "entry_watch_parity_sha256": _sha256(args.entry_watch_parity),
        "prospective_sha256": _sha256(args.prospective),
        "ledger_sha256": _sha256(args.ledger),
        "events_sha256": _sha256(args.events),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        "Gold 555 pipeline truth: "
        f"MT5={report['observed_mt5']['net_eur']} EUR | "
        "conditioned="
        f"{report['retrospective_management_replay']['net_eur']} EUR | "
        "prospective="
        f"{report['prospective_simulation']['net_eur']} EUR | "
        f"exact={report['actual_vs_prospective']['exact_signals']}/"
        f"{report['actual_vs_prospective']['signals']}"
    )
    print(f"Output: {args.output.resolve()}")
    return 0 if report["end_to_end_historical_extension_allowed"] else 2


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
            if isinstance(value, dict):
                yield value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
