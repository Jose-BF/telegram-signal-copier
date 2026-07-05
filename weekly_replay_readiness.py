"""Daily readiness report for exact future tick replay."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from tools import cache_replay_ticks


DATA_DIR = Path(__file__).parent / "data"
DEFAULT_REPLAY_FILE = DATA_DIR / "replay_trades.jsonl"
DEFAULT_AUDIT_FILE = DATA_DIR / "simulation_audit.jsonl"
DEFAULT_TICK_CACHE_DIR = DATA_DIR / "ticks_cache"
DEFAULT_OUTPUT = DATA_DIR / "weekly_replay_readiness.json"
SCHEMA_VERSION = 1


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _ticket_label(ticket: dict) -> str:
    value = ticket.get("ticket") or ticket.get("position_ticket")
    if value is None:
        return "unknown"
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value)


def _has_value(value) -> bool:
    return value is not None and value != ""


def _core_blockers(trade: dict) -> list[str]:
    blockers: list[str] = []
    for field in ("sig_id", "channel", "direction", "signal_dt_utc"):
        if not _has_value(trade.get(field)):
            blockers.append(f"missing_{field}")
    if trade.get("status") != "no_position":
        if not _has_value(trade.get("open_dt_utc")):
            blockers.append("missing_open_dt_utc")
        if not _has_value(trade.get("close_dt_utc")):
            blockers.append("missing_close_dt_utc")
    return blockers


def _ticket_blockers(trade: dict) -> list[str]:
    blockers: list[str] = []
    tickets = trade.get("tickets") or []
    if trade.get("status") != "no_position" and not tickets:
        return ["missing_tickets"]

    for ticket in tickets:
        label = _ticket_label(ticket)
        required = {
            "ticket": "missing_ticket_id",
            "open_dt_utc": "missing_ticket_open_dt",
            "open_price": "missing_ticket_open_price",
            "close_dt_utc": "missing_ticket_close_dt",
            "close_price": "missing_ticket_close_price",
            "pnl_net": "missing_ticket_pnl",
            "pnl_components": "missing_ticket_pnl_components",
            "open_deal": "missing_ticket_open_deal",
            "close_deal": "missing_ticket_close_deal",
        }
        for field, code in required.items():
            if not _has_value(ticket.get(field)):
                blockers.append(f"{code}:{label}")
        if not ticket.get("sl_history"):
            blockers.append(f"missing_ticket_sl_history:{label}")
        if not ticket.get("tp_history"):
            blockers.append(f"missing_ticket_tp_history:{label}")
    return blockers


def _audit_blockers_and_warnings(audit: dict | None) -> tuple[list[str], list[str]]:
    if audit is None:
        return ["missing_accounting_audit"], []

    status = audit.get("status")
    blockers: list[str] = []
    warnings: list[str] = []
    if status in ("blocked", "mismatch", "estimated"):
        blockers.append(f"accounting_status:{status}")
    elif status == "reconstructed":
        warnings.append("accounting_reconstructed")

    for assumption in audit.get("assumptions") or []:
        warnings.append(f"accounting_assumption:{assumption}")
    return blockers, warnings


def _tick_days(trade: dict, pad_minutes: int) -> list[str]:
    return [
        day.isoformat()
        for day in cache_replay_ticks.required_dates(
            [trade],
            pad_minutes=pad_minutes,
        )
    ]


def _tick_blockers(trade: dict, *, cache_dir: Path, pad_minutes: int) -> list[str]:
    blockers: list[str] = []
    for day in _tick_days(trade, pad_minutes):
        if not (cache_dir / f"{day}.parquet").exists():
            blockers.append(f"missing_tick_cache:{day}")
    return blockers


def assess_trade(
    trade: dict,
    audit: dict | None,
    *,
    cache_dir: Path,
    pad_minutes: int = 5,
) -> dict:
    audit_blockers, warnings = _audit_blockers_and_warnings(audit)
    blockers = []
    blockers.extend(_core_blockers(trade))
    blockers.extend(_ticket_blockers(trade))
    blockers.extend(audit_blockers)
    blockers.extend(_tick_blockers(
        trade,
        cache_dir=cache_dir,
        pad_minutes=pad_minutes,
    ))
    blockers = list(dict.fromkeys(blockers))
    warnings = list(dict.fromkeys(warnings))
    ready = not blockers
    return {
        "sig_id": trade.get("sig_id"),
        "channel": trade.get("channel"),
        "direction": trade.get("direction"),
        "signal_dt_utc": trade.get("signal_dt_utc"),
        "open_dt_utc": trade.get("open_dt_utc"),
        "close_dt_utc": trade.get("close_dt_utc"),
        "ready": ready,
        "status": "ready" if ready else "blocked",
        "accounting_status": audit.get("status") if audit else None,
        "tick_days": _tick_days(trade, pad_minutes),
        "ticket_count": len(trade.get("tickets") or []),
        "blockers": blockers,
        "warnings": warnings,
    }


def build_report(
    replay_rows: list[dict],
    audit_rows: list[dict],
    *,
    cache_dir: Path,
    pad_minutes: int = 5,
) -> dict:
    audits_by_sig = {row.get("sig_id"): row for row in audit_rows}
    trade_rows = [
        assess_trade(
            trade,
            audits_by_sig.get(trade.get("sig_id")),
            cache_dir=cache_dir,
            pad_minutes=pad_minutes,
        )
        for trade in replay_rows
    ]
    counts = Counter(row["status"] for row in trade_rows)
    blocker_counts = Counter(
        blocker
        for row in trade_rows
        for blocker in row["blockers"]
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tick_cache_dir": str(cache_dir),
        "pad_minutes": pad_minutes,
        "summary": {
            "total": len(trade_rows),
            "ready": counts.get("ready", 0),
            "blocked": counts.get("blocked", 0),
            "top_blockers": blocker_counts.most_common(20),
        },
        "trades": trade_rows,
    }


def write_report(report: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report whether replay trades have enough data for full replay")
    parser.add_argument("--replay", type=Path, default=DEFAULT_REPLAY_FILE)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT_FILE)
    parser.add_argument("--tick-cache-dir", type=Path, default=DEFAULT_TICK_CACHE_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--pad-minutes", type=int, default=5)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    report = build_report(
        load_jsonl(args.replay),
        load_jsonl(args.audit),
        cache_dir=args.tick_cache_dir,
        pad_minutes=args.pad_minutes,
    )
    write_report(report, args.output)

    if not args.quiet:
        summary = report["summary"]
        print(f"Weekly replay readiness: {summary['total']} trades")
        print(f"Ready: {summary['ready']}")
        print(f"Blocked: {summary['blocked']}")
        print(f"Output: {args.output}")
    return 0 if report["summary"]["blocked"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
