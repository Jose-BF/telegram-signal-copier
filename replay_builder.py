"""Build clean per-trade replay records from ledger + raw journal events.

`trade_events.jsonl` is the black-box log, and `ledger.jsonl` is the MT5-
verified economic truth. This module produces a thinner, deterministic artifact
for future tick replay: one JSON object per signal, with tickets, levels,
management messages and explicit readiness gaps.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Iterable


DATA_DIR = Path(__file__).parent / "data"
DEFAULT_LEDGER_FILE = DATA_DIR / "ledger.jsonl"
DEFAULT_EVENTS_FILE = DATA_DIR / "trade_events.jsonl"
DEFAULT_REPLAY_FILE = DATA_DIR / "replay_trades.jsonl"
SCHEMA_VERSION = 1

FILL_EVENTS = {
    "market_filled",
    "market_b_filled",
    "dca_filled",
    "scale_out_leg_filled",
}


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def events_by_signal(events: Iterable[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        sig_id = event.get("sig")
        if not sig_id:
            continue
        grouped[str(sig_id)].append(event)
    return dict(grouped)


def _ticket_key(ticket) -> str | None:
    if ticket is None:
        return None
    try:
        return str(int(ticket))
    except (TypeError, ValueError):
        return str(ticket)


def _events_by_ticket(events: Iterable[dict], event_names: set[str]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        if event.get("ev") not in event_names:
            continue
        key = _ticket_key(event.get("ticket"))
        if key is None:
            continue
        grouped[key].append(event)
    return dict(grouped)


def _append_level(history: dict, event: dict, status: str) -> None:
    key = _ticket_key(event.get("ticket"))
    if key is None:
        return
    bucket = history.setdefault(key, {"sl_history": [], "tp_history": []})
    base = {
        "ts": event.get("ts"),
        "source": event.get("label") or event.get("ev"),
        "status": status,
    }
    retcode = event.get("retcode", event.get("last_retcode"))
    if retcode is not None:
        base["retcode"] = retcode
    if event.get("attempts") is not None:
        base["attempts"] = event.get("attempts")

    sl_value = event.get("new_sl") if "new_sl" in event else event.get("sl")
    tp_value = event.get("new_tp") if "new_tp" in event else event.get("tp")
    if sl_value is not None:
        bucket["sl_history"].append({**base, "sl": sl_value})
    if tp_value is not None:
        bucket["tp_history"].append({**base, "tp": tp_value})


def _level_history_from_order_lifecycle(events: Iterable[dict]) -> dict[str, dict]:
    history: dict[str, dict] = {}
    status_by_event = {
        "mt5_modify_requested": "requested",
        "mt5_modify_confirmed": "confirmed",
        "mt5_position_snapshot": "snapshot",
        "mt5_action_failed": "failed",
        "mt5_modify_skipped_position_gone": "skipped_position_gone",
    }
    for event in events:
        status = status_by_event.get(event.get("ev"))
        if status:
            _append_level(history, event, status)
    return history


def _clean_event(event: dict | None) -> dict | None:
    if event is None:
        return None
    return dict(event)


def _normalise_ticket(
    position: dict,
    fill_events_by_ticket: dict[str, list[dict]],
    level_history_by_ticket: dict[str, dict],
) -> dict:
    deal_ticket = position.get("ticket")
    position_ticket = position.get("position_id") or deal_ticket
    key = _ticket_key(position_ticket)
    fills = fill_events_by_ticket.get(key or "", [])
    recovered_levels = level_history_by_ticket.get(key or "", {})
    sl_history = list(position.get("sl_history") or [])
    tp_history = list(position.get("tp_history") or [])
    if not sl_history:
        sl_history = list(recovered_levels.get("sl_history") or [])
    if not tp_history:
        tp_history = list(recovered_levels.get("tp_history") or [])
    return {
        "ticket": position_ticket,
        "position_ticket": position_ticket,
        "deal_ticket": deal_ticket,
        "position_id": position.get("position_id"),
        "role": position.get("role"),
        "volume": position.get("volume"),
        "open_dt_utc": position.get("open_dt_utc"),
        "open_price": position.get("open_price"),
        "close_dt_utc": position.get("close_dt_utc"),
        "close_price": position.get("close_price"),
        "close_reason": position.get("close_reason"),
        "is_closed": bool(position.get("is_closed")),
        "pnl_net": position.get("pnl_net"),
        "pnl_components": position.get("pnl_components"),
        "open_deal": position.get("open_deal"),
        "close_deal": position.get("close_deal"),
        "deals": list(position.get("deals") or []),
        "sl_history": sl_history,
        "tp_history": tp_history,
        "fill_event": _clean_event(fills[0] if fills else None),
        "fill_events": [_clean_event(e) for e in fills],
    }


def _levels_from_row(row: dict) -> dict:
    effective_tps = row.get("effective_tps") or row.get("tps") or []
    effective_sl = row.get("effective_sl")
    if effective_sl is None:
        effective_sl = row.get("sl")
    return {
        "range": row.get("range"),
        "provider_tps": row.get("tps"),
        "provider_sl": row.get("sl"),
        "effective_tps": effective_tps,
        "effective_sl": effective_sl,
        "effective_levels_source": row.get("effective_levels_source") or {},
        "max_tp_idx_touched": row.get("max_tp_idx_touched"),
    }


def _readiness(row: dict, tickets: list[dict], levels: dict) -> dict:
    simulation_blockers: list[str] = []
    audit_blockers: list[str] = []
    gaps: list[str] = []

    def add_gap(code: str, *, simulation: bool = False, audit: bool = False):
        if code not in gaps:
            gaps.append(code)
        if simulation and code not in simulation_blockers:
            simulation_blockers.append(code)
        if audit and code not in audit_blockers:
            audit_blockers.append(code)

    if not row.get("sig_id"):
        add_gap("missing_sig_id", simulation=True, audit=True)
    if not row.get("signal_dt_utc"):
        add_gap("missing_signal_time", simulation=True, audit=True)
    if not row.get("direction"):
        add_gap("missing_direction", simulation=True, audit=True)
    if row.get("analysis_excluded"):
        add_gap("analysis_excluded", simulation=True, audit=True)
    if row.get("pnl_mt5_complete") is False:
        add_gap("pnl_mt5_incomplete", simulation=True, audit=True)

    status = row.get("status")
    if status in ("open", "partial") or int(row.get("n_open") or 0) > 0:
        add_gap("open_positions", simulation=True, audit=True)
    if row.get("journal_has_signal_closed") and int(row.get("n_open") or 0) > 0:
        add_gap("journal_closed_but_mt5_open", simulation=True, audit=True)
    if not row.get("journal_has_signal_closed") and status == "closed":
        add_gap("missing_signal_closed", audit=True)

    if status != "no_position" and not tickets:
        add_gap("missing_positions", simulation=True, audit=True)

    for ticket in tickets:
        label = _ticket_key(ticket.get("ticket")) or "unknown"
        if ticket.get("ticket") is None:
            add_gap("missing_ticket_id", simulation=True, audit=True)
        if ticket.get("open_dt_utc") is None or ticket.get("open_price") is None:
            add_gap(f"missing_ticket_open:{label}", simulation=True, audit=True)
        if ticket.get("is_closed"):
            if ticket.get("close_dt_utc") is None or ticket.get("close_price") is None:
                add_gap(f"missing_ticket_close:{label}",
                        simulation=True, audit=True)
        else:
            add_gap(f"ticket_still_open:{label}", simulation=True, audit=True)

    has_ticket_sl = any(t.get("sl_history") for t in tickets)
    has_ticket_tp = any(t.get("tp_history") for t in tickets)

    if levels.get("effective_sl") is None and not has_ticket_sl:
        add_gap("missing_effective_sl", simulation=True, audit=True)
    if not levels.get("effective_tps") and not has_ticket_tp:
        add_gap("missing_effective_tps", simulation=True, audit=True)

    return {
        "simulation_ready": not simulation_blockers,
        "replay_ready": not simulation_blockers and not audit_blockers,
        "gaps": gaps,
        "simulation_blockers": simulation_blockers,
        "audit_blockers": audit_blockers,
    }


def build_replay_trade(ledger_row: dict, events: Iterable[dict] | None = None) -> dict:
    events = list(events or [])
    sig_id = ledger_row.get("sig_id") or ledger_row.get("sig")
    fill_events_by_ticket = _events_by_ticket(events, FILL_EVENTS)
    level_history_by_ticket = _level_history_from_order_lifecycle(
        ledger_row.get("order_lifecycle") or [])
    tickets = [
        _normalise_ticket(position, fill_events_by_ticket, level_history_by_ticket)
        for position in ledger_row.get("positions") or []
    ]
    levels = _levels_from_row(ledger_row)
    readiness = _readiness(ledger_row, tickets, levels)

    return {
        "schema_version": SCHEMA_VERSION,
        "sig_id": sig_id,
        "channel": ledger_row.get("channel"),
        "direction": ledger_row.get("direction"),
        "signal_dt_utc": ledger_row.get("signal_dt_utc"),
        "open_dt_utc": ledger_row.get("open_dt_utc"),
        "close_dt_utc": ledger_row.get("close_dt_utc"),
        "status": ledger_row.get("status"),
        "duration_min": ledger_row.get("duration_min"),
        "pnl_real_mt5": ledger_row.get("pnl_real_mt5"),
        "pnl_journal": ledger_row.get("pnl_journal"),
        "pnl_discrepancy": ledger_row.get("pnl_discrepancy"),
        "reconciled_ok": ledger_row.get("reconciled_ok"),
        "pnl_mt5_complete": ledger_row.get("pnl_mt5_complete"),
        "journal_has_signal_closed": ledger_row.get("journal_has_signal_closed"),
        "health": ledger_row.get("health"),
        "flags": list(ledger_row.get("flags") or []),
        "anomalies": list(ledger_row.get("anomalies") or []),
        "analysis_excluded": bool(ledger_row.get("analysis_excluded")),
        "analysis_exclusions": list(ledger_row.get("analysis_exclusions") or []),
        "signal_text": ledger_row.get("signal_text"),
        "levels": levels,
        "tickets": tickets,
        "management": list(ledger_row.get("management") or []),
        "decisions": {
            "entry_quality": ledger_row.get("entry_quality"),
            "strategy_snapshot": ledger_row.get("strategy_snapshot"),
            "post_time_stop_outcome": ledger_row.get("post_time_stop_outcome"),
        },
        "timeline": list(ledger_row.get("timeline") or []),
        "order_lifecycle": list(ledger_row.get("order_lifecycle") or []),
        "raw_event_count": len(events),
        **readiness,
    }


def build_replay_trades(
    ledger_rows: Iterable[dict],
    grouped_events: dict[str, list[dict]] | None = None,
) -> list[dict]:
    grouped_events = grouped_events or {}
    trades = []
    for row in ledger_rows:
        sig_id = row.get("sig_id") or row.get("sig")
        trades.append(build_replay_trade(row, grouped_events.get(sig_id, [])))
    return trades


def write_replay_trades(
    ledger_rows: Iterable[dict],
    grouped_events: dict[str, list[dict]] | None,
    output_path: Path,
) -> list[dict]:
    trades = build_replay_trades(ledger_rows, grouped_events)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for trade in trades:
            handle.write(json.dumps(trade, ensure_ascii=False) + "\n")
    return trades


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build data/replay_trades.jsonl from ledger + journal events")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER_FILE)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS_FILE)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPLAY_FILE)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    ledger_rows = load_jsonl(args.ledger)
    grouped_events = events_by_signal(load_jsonl(args.events))
    trades = write_replay_trades(ledger_rows, grouped_events, args.output)

    if not args.quiet:
        replay_ready = sum(1 for t in trades if t["replay_ready"])
        simulation_ready = sum(1 for t in trades if t["simulation_ready"])
        print(f"Replay trades: {len(trades)}")
        print(f"Simulation ready: {simulation_ready}")
        print(f"Replay ready: {replay_ready}")
        print(f"Output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
