"""Daily readiness report for exact future tick replay."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from broker_market_sessions import MARKET_SESSION_CONTRACT
import runtime_paths
from tools import ensure_replay_tick_cache


DATA_DIR = runtime_paths.active_data_dir()
DEFAULT_REPLAY_FILE = DATA_DIR / "replay_trades.jsonl"
DEFAULT_AUDIT_FILE = DATA_DIR / "accounting_replay_audit.jsonl"
DEFAULT_OBSERVED_AUDIT_FILE = DATA_DIR / "observed_tick_replay_audit.jsonl"
DEFAULT_TICK_CACHE_DIR = DATA_DIR / "ticks_cache"
DEFAULT_OUTPUT = DATA_DIR / "replay_readiness_report.json"
SCHEMA_VERSION = 2
CAUSAL_PATH_CONTRACT = "causal_path_v3"
FILL_PRICE_AUTHORITY = "mt5_deals"


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


def _is_pending(trade: dict) -> bool:
    status = str(trade.get("status") or "").lower()
    if status in {"open", "active", "pending"}:
        return True
    return (
        status not in {"closed", "no_position"}
        and _has_value(trade.get("open_dt_utc"))
        and not _has_value(trade.get("close_dt_utc"))
    )


def _pending_ticket_blockers(trade: dict) -> list[str]:
    blockers: list[str] = []
    tickets = trade.get("tickets") or []
    if not tickets:
        return ["missing_tickets"]
    required = {
        "ticket": "missing_ticket_id",
        "open_dt_utc": "missing_ticket_open_dt",
        "open_price": "missing_ticket_open_price",
        "open_deal": "missing_ticket_open_deal",
    }
    for ticket in tickets:
        label = _ticket_label(ticket)
        for field, code in required.items():
            if not _has_value(ticket.get(field)):
                blockers.append(f"{code}:{label}")
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
        }
        for field, code in required.items():
            if not _has_value(ticket.get(field)):
                blockers.append(f"{code}:{label}")
        if not _has_value(ticket.get("close_deal")) and not _has_value(
            ticket.get("close_event")
        ):
            blockers.append(f"missing_ticket_close_deal:{label}")
        if not ticket.get("sl_history"):
            blockers.append(f"missing_ticket_sl_history:{label}")
        if not ticket.get("tp_history"):
            blockers.append(f"missing_ticket_tp_history:{label}")
    return blockers


def _ticket_warnings(trade: dict) -> list[str]:
    warnings: list[str] = []
    for ticket in trade.get("tickets") or []:
        label = _ticket_label(ticket)
        if not _has_value(ticket.get("close_deal")) and _has_value(
            ticket.get("close_event")
        ):
            warnings.append(f"ticket_close_deal_reconstructed:{label}")
    return warnings


def _audit_blockers_and_warnings(
    audit: dict | None,
) -> tuple[list[str], list[str], bool]:
    if audit is None:
        return ["missing_accounting_audit"], [], False

    status = audit.get("status")
    blockers: list[str] = []
    warnings: list[str] = []
    if status in ("blocked", "mismatch", "estimated"):
        blockers.append(f"accounting_status:{status}")
    elif status == "reconstructed":
        warnings.append("accounting_reconstructed")

    try:
        diff = float(audit.get("diff"))
    except (TypeError, ValueError):
        diff = None
        blockers.append("missing_accounting_diff")
    if diff is not None and round(diff, 2) != 0.0:
        blockers.append(f"accounting_diff:{diff:+.2f}")

    for assumption in audit.get("assumptions") or []:
        warnings.append(f"accounting_assumption:{assumption}")
    money_exact = (
        status in {"exact", "reconstructed"}
        and diff is not None
        and round(diff, 2) == 0.0
        and not blockers
    )
    return blockers, warnings, money_exact


def _observed_blockers_and_warnings(
    observed: dict | None,
) -> tuple[list[str], list[str]]:
    if observed is None:
        return ["missing_observed_tick_audit"], []
    blockers: list[str] = []
    warnings: list[str] = []
    status = str(observed.get("status") or "missing").lower()
    if status != "exact":
        blockers.append(f"observed_path_status:{status}")
    if observed.get("validation_contract") != CAUSAL_PATH_CONTRACT:
        blockers.append("observed_path_contract_unverified")
    if observed.get("fill_price_authority") != FILL_PRICE_AUTHORITY:
        blockers.append("observed_fill_authority_unverified")
    if observed.get("market_session_contract") != MARKET_SESSION_CONTRACT:
        blockers.append("observed_market_session_contract_unverified")
    blockers.extend(
        f"observed:{item}" for item in observed.get("blockers") or [])
    warnings.extend(
        f"observed:{item}" for item in observed.get("warnings") or [])
    for ticket in observed.get("tickets") or []:
        warnings.extend(
            f"observed:{item}" for item in ticket.get("warnings") or [])
    return blockers, warnings


def _tick_days(trade: dict, pad_minutes: int) -> list[str]:
    return [
        day.isoformat()
        for day in ensure_replay_tick_cache.required_dates(
            [trade],
            pad_minutes=pad_minutes,
        )
    ]


def _tick_blockers(trade: dict, *, cache_dir: Path, pad_minutes: int) -> list[str]:
    blockers: list[str] = []
    day_windows = ensure_replay_tick_cache.required_day_windows(
        [trade],
        pad_minutes=pad_minutes,
    )
    for day_date, (required_from, required_through) in day_windows.items():
        day = day_date.isoformat()
        if not (cache_dir / f"{day}.parquet").is_file():
            blockers.append(f"missing_tick_cache:{day}")
            continue
        contract = ensure_replay_tick_cache.load_valid_day_contract(
            cache_dir,
            date.fromisoformat(day),
            expected_symbol="XAUUSD",
        )
        if contract is None:
            blockers.append(f"invalid_tick_cache_contract:{day}")
            continue
        if not ensure_replay_tick_cache.coverage_satisfies_window(
            contract,
            required_from,
            required_through,
        ):
            blockers.append(f"incomplete_tick_cache_coverage:{day}")
    return blockers


def assess_trade(
    trade: dict,
    audit: dict | None,
    observed: dict | None,
    *,
    cache_dir: Path,
    pad_minutes: int = 5,
) -> dict:
    pending = _is_pending(trade)
    blockers: list[str] = []
    blockers.extend(_core_blockers(trade))
    if pending:
        blockers = [
            blocker
            for blocker in blockers
            if blocker != "missing_close_dt_utc"
        ]
        blockers.extend(_pending_ticket_blockers(trade))
        blockers = list(dict.fromkeys(blockers))
        status = "blocked" if blockers else "pending"
        return {
            "sig_id": trade.get("sig_id"),
            "channel": trade.get("channel"),
            "direction": trade.get("direction"),
            "signal_dt_utc": trade.get("signal_dt_utc"),
            "open_dt_utc": trade.get("open_dt_utc"),
            "close_dt_utc": trade.get("close_dt_utc"),
            "ready": False,
            "status": status,
            "accounting_status": None,
            "accounting_money_exact": False,
            "observed_path_status": None,
            "tick_days": _tick_days(trade, pad_minutes),
            "ticket_count": len(trade.get("tickets") or []),
            "blockers": blockers,
            "warnings": ["trade_still_open"] if not blockers else [],
        }

    audit_blockers, warnings, money_exact = (
        _audit_blockers_and_warnings(audit)
    )
    observed_blockers, observed_warnings = (
        _observed_blockers_and_warnings(observed)
    )
    blockers.extend(_ticket_blockers(trade))
    blockers.extend(audit_blockers)
    if not money_exact:
        blockers.append("accounting_money_not_exact")
    blockers.extend(_tick_blockers(
        trade,
        cache_dir=cache_dir,
        pad_minutes=pad_minutes,
    ))
    blockers.extend(observed_blockers)
    warnings.extend(observed_warnings)
    warnings.extend(_ticket_warnings(trade))
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
        "accounting_money_exact": money_exact,
        "observed_path_status": observed.get("status") if observed else None,
        "tick_days": _tick_days(trade, pad_minutes),
        "ticket_count": len(trade.get("tickets") or []),
        "blockers": blockers,
        "warnings": warnings,
    }


def build_report(
    replay_rows: list[dict],
    audit_rows: list[dict],
    observed_rows: list[dict],
    *,
    cache_dir: Path,
    pad_minutes: int = 5,
    since: date | None = None,
    until: date | None = None,
) -> dict:
    audits_by_sig = {row.get("sig_id"): row for row in audit_rows}
    observed_by_sig = {row.get("sig_id"): row for row in observed_rows}
    selected_rows = [
        trade for trade in replay_rows
        if _trade_in_scope(trade, since=since, until=until)
    ]
    trade_rows = [
        assess_trade(
            trade,
            audits_by_sig.get(trade.get("sig_id")),
            observed_by_sig.get(trade.get("sig_id")),
            cache_dir=cache_dir,
            pad_minutes=pad_minutes,
        )
        for trade in selected_rows
    ]
    counts = Counter(row["status"] for row in trade_rows)
    blocker_counts = Counter(
        blocker
        for row in trade_rows
        for blocker in row["blockers"]
    )
    day_rows = []
    for day in sorted({_trade_day(row) for row in trade_rows if _trade_day(row)}):
        rows = [row for row in trade_rows if _trade_day(row) == day]
        day_counts = Counter(row["status"] for row in rows)
        day_status = (
            "blocked" if day_counts.get("blocked", 0)
            else "pending" if day_counts.get("pending", 0)
            else "ready"
        )
        day_rows.append({
            "date": day,
            "status": day_status,
            "total": len(rows),
            "ready": day_counts.get("ready", 0),
            "pending": day_counts.get("pending", 0),
            "blocked": day_counts.get("blocked", 0),
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tick_cache_dir": str(cache_dir),
        "pad_minutes": pad_minutes,
        "scope": {
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
            "input_trades": len(replay_rows),
            "selected_trades": len(selected_rows),
        },
        "summary": {
            "total": len(trade_rows),
            "ready": counts.get("ready", 0),
            "pending": counts.get("pending", 0),
            "blocked": counts.get("blocked", 0),
            "top_blockers": blocker_counts.most_common(20),
        },
        "days": day_rows,
        "trades": trade_rows,
    }


def _trade_day(trade: dict) -> str | None:
    value = trade.get("signal_dt_utc") or trade.get("open_dt_utc")
    day = str(value or "")[:10]
    try:
        return date.fromisoformat(day).isoformat()
    except ValueError:
        return None


def _trade_in_scope(
    trade: dict,
    *,
    since: date | None,
    until: date | None,
) -> bool:
    raw_day = _trade_day(trade)
    if raw_day is None:
        return since is None and until is None
    trade_day = date.fromisoformat(raw_day)
    if since and trade_day < since:
        return False
    if until and trade_day > until:
        return False
    return True


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
    parser.add_argument(
        "--observed-audit",
        type=Path,
        default=DEFAULT_OBSERVED_AUDIT_FILE,
    )
    parser.add_argument("--tick-cache-dir", type=Path, default=DEFAULT_TICK_CACHE_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--pad-minutes", type=int, default=5)
    parser.add_argument("--since", type=date.fromisoformat)
    parser.add_argument("--until", type=date.fromisoformat)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    report = build_report(
        load_jsonl(args.replay),
        load_jsonl(args.audit),
        load_jsonl(args.observed_audit),
        cache_dir=args.tick_cache_dir,
        pad_minutes=args.pad_minutes,
        since=args.since,
        until=args.until,
    )
    write_report(report, args.output)

    if not args.quiet:
        summary = report["summary"]
        print(f"Replay readiness report: {summary['total']} trades")
        print(f"Ready: {summary['ready']}")
        print(f"Pending: {summary['pending']}")
        print(f"Blocked: {summary['blocked']}")
        print(f"Output: {args.output}")
    return 0 if (
        report["summary"]["blocked"] == 0
        and report["summary"]["pending"] == 0
    ) else 1


if __name__ == "__main__":
    sys.exit(main())
