"""Settle every frozen shadow strategy over complete cached broker ticks."""

from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path
import subprocess
import sys


REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

import runtime_paths
from strategy_shadow_settlement import (
    ParquetShadowTickReader,
    actual_rows_from_ledger,
    eligible_signal_ids,
    reconstruct_registration_records,
    settle_shadow_records,
)
from strategy_shadow_contracts import canonical_hash


RUNTIME_DIR = runtime_paths.active_data_dir(REPO_DIR)
SHADOW_CONTRACT_PATHS = (
    "strategy_shadow_catalog.py",
    "strategy_shadow_contracts.py",
    "strategy_shadow_engine.py",
)


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc


def _default_money_contract() -> Path:
    candidates = (
        RUNTIME_DIR / "broker_money_contract.json",
        REPO_DIR / "data" / "broker_money_contract.json",
    )
    return next((path for path in candidates if path.is_file()), candidates[0])


def _load_shadow_records(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if (
                "strategy_shadow_" not in line
                and '"signal_received"' not in line
                and '"gold_555_entry_watch_started"' not in line
            ):
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            event = str(record.get("ev") or "")
            if (
                event.startswith("strategy_shadow_")
                or event == "signal_received"
                or event == "gold_555_entry_watch_started"
            ):
                records.append(record)
    return records


def _git_blob_id(ref: str, path: str) -> str | None:
    command = (
        ["git", "hash-object", "--", path]
        if ref == "WORKTREE"
        else ["git", "rev-parse", f"{ref}:{path}"]
    )
    completed = subprocess.run(
        command,
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    value = completed.stdout.strip()
    if completed.returncode != 0 or len(value) != 40:
        return None
    return value


def _trusted_source_commits(
    records: list[dict],
) -> dict[str, str]:
    current = {
        path: _git_blob_id("WORKTREE", path)
        for path in SHADOW_CONTRACT_PATHS
    }
    if any(value is None for value in current.values()):
        return {}
    trusted: dict[str, str] = {}
    commits = sorted({
        str(record.get("code_commit") or "")
        for record in records
        if len(str(record.get("code_commit") or "")) == 40
    })
    for commit in commits:
        historical = {
            path: _git_blob_id(commit, path)
            for path in SHADOW_CONTRACT_PATHS
        }
        if historical != current:
            continue
        trusted[commit] = canonical_hash({
            "schema_version": 1,
            "commit": commit,
            "contract_blobs": historical,
        })
    return trusted


def _load_ledger(path: Path, signal_ids: set[str]) -> list[dict]:
    if not path.is_file():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not any(signal_id in line for signal_id in signal_ids):
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("sig_id") or row.get("signal_id") or "") in signal_ids:
                rows.append(row)
    return rows


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _print_summary(result: dict, output: Path) -> None:
    report = result["report"]
    print("Shadow settlement complete")
    for channel, label in (("canal1", "Dubai Investing"), ("canal2", "Gold Signals")):
        matrix = report["matrix"][channel]
        print(
            f"  {label}: {matrix['eligible_signals']} signals, "
            f"{matrix['settled_rows']}/{matrix['expected_rows']} "
            f"strategy results settled, {matrix['blocked_rows']} blocked, "
            f"{matrix['open_rows']} open"
        )
    if report["comparison_allowed"]:
        leader = report["shadow_leader"] or {}
        print(
            "  Comparison: READY | "
            f"Dubai={leader.get('canal1')} | Gold={leader.get('canal2')}"
        )
    else:
        print(
            "  Comparison: BLOCKED | "
            + ", ".join(report["comparison_blockers"])
        )
    print(f"  Settlement hash: {result['settlement_hash']}")
    print(f"  Output: {output}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True, type=_date)
    parser.add_argument("--until", required=True, type=_date)
    parser.add_argument(
        "--events",
        type=Path,
        default=RUNTIME_DIR / "trade_events.jsonl",
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=RUNTIME_DIR / "ledger.jsonl",
    )
    parser.add_argument(
        "--ticks-cache",
        type=Path,
        default=RUNTIME_DIR / "ticks_cache",
    )
    parser.add_argument(
        "--money-ticks-cache",
        type=Path,
        default=RUNTIME_DIR / "money_ticks_cache",
    )
    parser.add_argument(
        "--money-contract",
        type=Path,
        default=_default_money_contract(),
    )
    parser.add_argument(
        "--provider-catalog",
        type=Path,
        default=RUNTIME_DIR / "provider_signal_catalog.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=RUNTIME_DIR / "strategy_shadow_report.json",
    )
    args = parser.parse_args(argv)

    if args.until < args.since:
        parser.error("--until cannot precede --since")
    for path, label in (
        (args.events, "events"),
        (args.money_contract, "money contract"),
        (args.provider_catalog, "provider catalog"),
    ):
        if not path.is_file():
            parser.error(f"{label} file does not exist: {path}")

    print("[1/4] Reading causal shadow registrations and management...")
    records = _load_shadow_records(args.events)
    provider_catalog = json.loads(
        args.provider_catalog.read_text(encoding="utf-8")
    )
    trusted_commits = _trusted_source_commits(records)
    signal_ids = eligible_signal_ids(
        records,
        since=args.since,
        until=args.until,
    )
    print(f"[2/4] Reading MT5 calibration for {len(signal_ids)} signals...")
    ledger = _load_ledger(args.ledger, signal_ids)
    contract = json.loads(args.money_contract.read_text(encoding="utf-8"))
    reader = ParquetShadowTickReader(
        ticks_cache_dir=args.ticks_cache,
        money_ticks_cache_dir=args.money_ticks_cache,
        money_contract=contract,
    )
    reconstructed, _reconstruction_audit = reconstruct_registration_records(
        records,
        provider_catalog=provider_catalog,
        tick_reader=reader,
        trusted_source_commits=trusted_commits,
        since=args.since,
        until=args.until,
    )
    actual = actual_rows_from_ledger(
        ledger,
        [*records, *reconstructed],
        since=args.since,
        until=args.until,
    )
    print("[3/4] Replaying all frozen candidates over verified ticks...")
    result = settle_shadow_records(
        records,
        tick_reader=reader,
        since=args.since,
        until=args.until,
        actual_rows=actual,
        provider_catalog=provider_catalog,
        trusted_source_commits=trusted_commits,
    )
    print("[4/4] Writing deterministic comparison report...")
    _atomic_json(args.output, result)
    _print_summary(result, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
