"""Build clean per-trade replay records from ledger + raw journal events.

`trade_events.jsonl` is the black-box log, and `ledger.jsonl` is the MT5-
verified economic truth. This module produces a thinner, deterministic artifact
for future tick replay: one JSON object per signal, with tickets, levels,
management messages and explicit readiness gaps.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import replay_source_contract
import runtime_paths

DATA_DIR = runtime_paths.active_data_dir()
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


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_seconds(value: str | None) -> str | None:
    parsed = _parse_dt(value)
    if parsed is None:
        return value
    return parsed.isoformat(timespec="seconds")




def _runtime_discontinuities(events: Iterable[dict]) -> list[dict]:
    ordered = sorted(
        (
            (_parse_dt(event.get("ts")), event)
            for event in events
            if _parse_dt(event.get("ts")) is not None
        ),
        key=lambda item: item[0],
    )
    discontinuities: list[dict] = []
    for index, (restart_at, event) in enumerate(ordered):
        if event.get("ev") != "session_started":
            continue
        previous = next(
            (
                event_at
                for event_at, _previous_event in reversed(ordered[:index])
                if event_at < restart_at
            ),
            None,
        )
        if previous is None:
            continue
        restored = next(
            (
                event_at
                for event_at, later_event in ordered[index + 1:]
                if (
                    later_event.get("ev") == "mt5_connection_change"
                    and later_event.get("connected") is True
                )
            ),
            None,
        )
        if restored is None:
            restored = next(
                (
                    event_at
                    for event_at, later_event in ordered[index + 1:]
                    if later_event.get("ev") == "poller_started"
                ),
                restart_at,
            )
        discontinuities.append({
            "kind": "session_restart_overlap",
            "unobserved_from_utc": previous.isoformat(timespec="seconds"),
            "restart_observed_utc": restart_at.isoformat(timespec="seconds"),
            "observability_restored_utc": restored.isoformat(timespec="seconds"),
        })
    return discontinuities


def _operational_context_for_trade(
    row: dict,
    discontinuities: Iterable[dict],
) -> dict:
    opened = _parse_dt(row.get("open_dt_utc") or row.get("signal_dt_utc"))
    closed = _parse_dt(row.get("close_dt_utc"))
    if opened is None or closed is None:
        return {"runtime_discontinuities": []}
    overlapping = []
    for item in discontinuities:
        window_start = _parse_dt(item.get("unobserved_from_utc"))
        window_end = _parse_dt(item.get("observability_restored_utc"))
        if (
            window_start is not None
            and window_end is not None
            and window_start <= closed
            and window_end >= opened
        ):
            overlapping.append(dict(item))
    return {"runtime_discontinuities": overlapping}

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


def _role_from_fill_event(event: dict) -> str | None:
    ev = event.get("ev")
    if ev == "market_filled":
        return "market_a"
    if ev in {"market_b_filled", "scale_out_leg_filled"}:
        return "scale_out_leg"
    if ev == "dca_filled":
        return "dca"
    return None


def _positive_volume(value) -> float | None:
    try:
        volume = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(volume) or volume <= 0:
        return None
    return round(volume, 8)


def _snapshot_volume_consensus(events: Iterable[dict]) -> dict[str, float]:
    observed: dict[str, set[float]] = defaultdict(set)
    invalid: set[str] = set()
    for event in events:
        if (
            event.get("ev") != "mt5_position_snapshot"
            or event.get("position_exists") is not True
        ):
            continue
        key = _ticket_key(event.get("ticket"))
        if key is None:
            continue
        volume = _positive_volume(event.get("volume"))
        if volume is None:
            invalid.add(key)
        else:
            observed[key].add(volume)
    return {
        key: next(iter(volumes))
        for key, volumes in observed.items()
        if key not in invalid and len(volumes) == 1
    }


def _positions_from_journal_events(events: Iterable[dict]) -> list[dict]:
    """Rebuild minimal positions from journal events when MT5 history is absent.

    This is a replay fallback, not broker truth. It is only used when the
    ledger has no MT5 positions but the black-box journal contains fills.
    """
    events = list(events)
    snapshot_volumes = _snapshot_volume_consensus(events)
    positions: list[dict] = []
    seen: set[str] = set()
    for event in events:
        if event.get("ev") not in FILL_EVENTS:
            continue
        key = _ticket_key(event.get("ticket"))
        if key is None or key in seen:
            continue
        seen.add(key)
        volume = _positive_volume(event.get("volume"))
        volume_source = "fill_event" if volume is not None else None
        if volume is None and key in snapshot_volumes:
            volume = snapshot_volumes[key]
            volume_source = "mt5_position_snapshot_consensus"
        positions.append({
            "ticket": event.get("ticket"),
            "position_id": event.get("ticket"),
            "role": _role_from_fill_event(event),
            "volume": volume,
            "volume_source": volume_source,
            "open_dt_utc": _iso_seconds(event.get("ts")),
            "open_price": event.get("price"),
            "close_dt_utc": None,
            "close_price": None,
            "close_reason": None,
            "is_closed": False,
            "pnl_net": None,
            "pnl_components": None,
            "open_deal": None,
            "close_deal": None,
            "deals": [],
            "sl_history": [],
            "tp_history": [],
        })
    return positions


def _close_reason_from_tag(tag: str | None) -> str | None:
    cleaned = str(tag or "").strip().upper()
    if not cleaned:
        return None
    if cleaned.startswith("TP"):
        return "tp"
    if cleaned.startswith("SL"):
        return "sl"
    if cleaned in {"BE", "BREAKEVEN", "BREAK_EVEN"}:
        return "be"
    if cleaned in {"BOT_CLOSE", "MANUAL_CLOSE", "CLOSE"}:
        return "bot_close"
    return cleaned.lower()


def _closure_events_by_ticket(events: Iterable[dict]) -> dict[str, dict]:
    events = list(events)
    requested = {
        key
        for event in events
        if event.get("ev") == "mt5_close_requested"
        for key in [_ticket_key(event.get("ticket"))]
        if key is not None
    }
    succeeded = {
        key
        for event in events
        if (
            event.get("ev") == "mt5_close_result"
            and event.get("retcode") in {10009, 10010}
        )
        for key in [_ticket_key(event.get("ticket"))]
        if key is not None
    }
    disappeared = {
        key
        for event in events
        if (
            event.get("ev") == "mt5_position_snapshot"
            and event.get("after_action") == "CLOSE_POSITION"
            and event.get("position_exists") is False
        )
        for key in [_ticket_key(event.get("ticket"))]
        if key is not None
    }
    closures: dict[str, dict] = {}
    for event in events:
        if event.get("ev") != "positions_closed_by_mt5":
            continue
        event_ts = _iso_seconds(event.get("ts"))
        for closure in event.get("closures") or []:
            key = _ticket_key(closure.get("ticket"))
            if key is None:
                continue
            closures[key] = {
                "ev": "positions_closed_by_mt5",
                "ts": event_ts,
                "ticket": closure.get("ticket"),
                "exit_price": closure.get("exit_price"),
                "pnl": closure.get("pnl"),
                "closed_by_tag": closure.get("closed_by_tag"),
                "distance_to_tag": closure.get("distance_to_tag"),
                "confirmed_bot_close": (
                    key in requested
                    and key in succeeded
                    and key in disappeared
                ),
            }
    return closures


def _same_price(left, right) -> bool:
    try:
        return abs(float(left) - float(right)) <= 1e-6
    except (TypeError, ValueError):
        return False


def _has_confirmed_entry_level_stop(ticket: dict) -> bool:
    if not _same_price(ticket.get("open_price"), ticket.get("close_price")):
        return False
    try:
        if abs(float(ticket.get("pnl_net"))) > 0.01:
            return False
    except (TypeError, ValueError):
        return False
    close_at = _parse_dt(ticket.get("close_dt_utc"))
    for level in ticket.get("sl_history") or []:
        if (
            level.get("status") != "confirmed"
            or not _same_price(level.get("sl"), ticket.get("open_price"))
        ):
            continue
        level_at = _parse_dt(level.get("ts"))
        if close_at is None or level_at is None or level_at <= close_at:
            return True
    return False


def _normalise_observed_close_reason(
    ticket: dict,
    closure: dict | None,
) -> None:
    reason = ticket.get("close_reason")
    if (
        reason == "close_first"
        and closure is not None
        and closure.get("confirmed_bot_close")
    ):
        ticket["close_reason"] = "bot_close"
        ticket["close_reason_evidence"] = "confirmed_bot_close_chain"
    elif reason == "loss_be" and _has_confirmed_entry_level_stop(ticket):
        ticket["close_reason"] = "be"
        ticket["close_reason_evidence"] = "confirmed_entry_level_stop"


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

    if status == "observed_unattributed":
        for field in (
            "observed_interval_start_utc",
            "observed_interval_end_utc",
            "previous",
            "current",
        ):
            if event.get(field) is not None:
                base[field] = event.get(field)

    current = event.get("current") if isinstance(event.get("current"), dict) else {}
    sl_value = (
        event.get("new_sl") if "new_sl" in event
        else event.get("sl", current.get("sl"))
    )
    tp_value = (
        event.get("new_tp") if "new_tp" in event
        else event.get("tp", current.get("tp"))
    )
    if status == "observed_unattributed":
        changed_fields = set(event.get("changed_fields") or [])
        if changed_fields:
            if "sl" not in changed_fields:
                sl_value = None
            if "tp" not in changed_fields:
                tp_value = None
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
        "mt5_level_change_unattributed": "observed_unattributed",
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
    closure_events_by_ticket: dict[str, dict],
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
    ticket = {
        "ticket": position_ticket,
        "position_ticket": position_ticket,
        "deal_ticket": deal_ticket,
        "position_id": position.get("position_id"),
        "role": position.get("role"),
        "volume": position.get("volume"),
        "volume_source": position.get("volume_source"),
        "mt5_time_offset_s": position.get("mt5_time_offset_s"),
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
    closure = closure_events_by_ticket.get(key or "")
    if closure and not ticket["is_closed"]:
        ticket["close_dt_utc"] = closure.get("ts")
        ticket["close_price"] = closure.get("exit_price")
        ticket["close_reason"] = _close_reason_from_tag(closure.get("closed_by_tag"))
        ticket["is_closed"] = True
        ticket["pnl_net"] = closure.get("pnl")
        ticket["pnl_components"] = {
            "profit": closure.get("pnl"),
            "swap": 0.0,
            "commission": 0.0,
            "fee": 0.0,
            "net": closure.get("pnl"),
        }
        ticket["close_event"] = closure
    _normalise_observed_close_reason(ticket, closure)
    return ticket


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


def _all_tickets_closed(tickets: list[dict]) -> bool:
    return bool(tickets) and all(ticket.get("is_closed") for ticket in tickets)


def _latest_close_dt(tickets: list[dict]) -> str | None:
    candidates = [
        _parse_dt(ticket.get("close_dt_utc"))
        for ticket in tickets
        if ticket.get("close_dt_utc")
    ]
    candidates = [candidate for candidate in candidates if candidate is not None]
    if not candidates:
        return None
    return max(candidates).isoformat(timespec="seconds")


def _earliest_open_dt(tickets: list[dict]) -> str | None:
    candidates = [
        _parse_dt(ticket.get("open_dt_utc"))
        for ticket in tickets
        if ticket.get("open_dt_utc")
    ]
    candidates = [candidate for candidate in candidates if candidate is not None]
    if not candidates:
        return None
    return min(candidates).isoformat(timespec="seconds")


def _duration_min(open_dt: str | None, close_dt: str | None) -> float | None:
    opened = _parse_dt(open_dt)
    closed = _parse_dt(close_dt)
    if opened is None or closed is None:
        return None
    return round((closed - opened).total_seconds() / 60, 1)


def _sum_ticket_pnl(tickets: list[dict]) -> float | None:
    total = 0.0
    for ticket in tickets:
        pnl = ticket.get("pnl_net")
        if pnl is None:
            return None
        try:
            total += float(pnl)
        except (TypeError, ValueError):
            return None
    return round(total, 2)


def _with_reconstructed_closure_state(row: dict, tickets: list[dict]) -> dict:
    if not any(ticket.get("close_event") for ticket in tickets):
        return dict(row)
    reconstructed = dict(row)
    if not reconstructed.get("open_dt_utc"):
        reconstructed["open_dt_utc"] = _earliest_open_dt(tickets)
    if _all_tickets_closed(tickets):
        reconstructed["status"] = "closed"
        reconstructed["n_closed"] = len(tickets)
        reconstructed["n_open"] = 0
        reconstructed["n_positions"] = len(tickets)
        reconstructed["pnl_mt5_complete"] = True
        reconstructed["close_dt_utc"] = _latest_close_dt(tickets)
        reconstructed["duration_min"] = _duration_min(
            reconstructed.get("open_dt_utc"),
            reconstructed.get("close_dt_utc"),
        )
        pnl = _sum_ticket_pnl(tickets)
        if pnl is not None:
            reconstructed["pnl_real_mt5"] = pnl
            if reconstructed.get("pnl_journal") is not None:
                try:
                    discrepancy = pnl - float(reconstructed.get("pnl_journal"))
                except (TypeError, ValueError):
                    discrepancy = None
                if discrepancy is not None:
                    reconstructed["pnl_discrepancy"] = round(discrepancy, 2)
                    reconstructed["reconciled_ok"] = (
                        reconstructed["pnl_discrepancy"] == 0
                    )
    return reconstructed


def build_replay_trade(
    ledger_row: dict,
    events: Iterable[dict] | None = None,
    *,
    operational_context: dict | None = None,
) -> dict:
    events = list(events or [])
    sig_id = ledger_row.get("sig_id") or ledger_row.get("sig")
    fill_events_by_ticket = _events_by_ticket(events, FILL_EVENTS)
    closure_events_by_ticket = _closure_events_by_ticket(events)
    level_history_by_ticket = _level_history_from_order_lifecycle(
        ledger_row.get("order_lifecycle") or [])
    source_positions = list(ledger_row.get("positions") or [])
    if not source_positions and closure_events_by_ticket:
        source_positions = _positions_from_journal_events(events)
    tickets = [
        _normalise_ticket(
            position,
            fill_events_by_ticket,
            level_history_by_ticket,
            closure_events_by_ticket,
        )
        for position in source_positions
    ]
    replay_row = _with_reconstructed_closure_state(ledger_row, tickets)
    levels = _levels_from_row(replay_row)
    readiness = _readiness(replay_row, tickets, levels)
    pnl_source = (
        "positions_closed_by_mt5"
        if any(ticket.get("close_event") for ticket in tickets)
        else "ledger"
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "sig_id": sig_id,
        "channel": replay_row.get("channel"),
        "direction": replay_row.get("direction"),
        "signal_dt_utc": replay_row.get("signal_dt_utc"),
        "open_dt_utc": replay_row.get("open_dt_utc"),
        "close_dt_utc": replay_row.get("close_dt_utc"),
        "status": replay_row.get("status"),
        "duration_min": replay_row.get("duration_min"),
        "mt5_time_offset_s": replay_row.get("mt5_time_offset_s"),
        "pnl_real_mt5": replay_row.get("pnl_real_mt5"),
        "pnl_real_mt5_source": pnl_source,
        "pnl_journal": replay_row.get("pnl_journal"),
        "pnl_discrepancy": replay_row.get("pnl_discrepancy"),
        "reconciled_ok": replay_row.get("reconciled_ok"),
        "pnl_mt5_complete": replay_row.get("pnl_mt5_complete"),
        "journal_has_signal_closed": replay_row.get("journal_has_signal_closed"),
        "health": replay_row.get("health"),
        "flags": list(replay_row.get("flags") or []),
        "anomalies": list(replay_row.get("anomalies") or []),
        "analysis_excluded": bool(replay_row.get("analysis_excluded")),
        "analysis_exclusions": list(replay_row.get("analysis_exclusions") or []),
        "signal_text": replay_row.get("signal_text"),
        "entry_provenance": replay_row.get("entry_provenance"),
        "levels": levels,
        "tickets": tickets,
        "management": list(replay_row.get("management") or []),
        "decisions": {
            "entry_quality": replay_row.get("entry_quality"),
            "strategy_snapshot": replay_row.get("strategy_snapshot"),
            "post_time_stop_outcome": replay_row.get("post_time_stop_outcome"),
        },
        "timeline": list(replay_row.get("timeline") or []),
        "order_lifecycle": list(replay_row.get("order_lifecycle") or []),
        "raw_event_count": len(events),
        "operational_context": operational_context or {"runtime_discontinuities": []},
        **readiness,
    }


def build_replay_trades(
    ledger_rows: Iterable[dict],
    grouped_events: dict[str, list[dict]] | None = None,
    *,
    operational_events: Iterable[dict] | None = None,
) -> list[dict]:
    grouped_events = grouped_events or {}
    discontinuities = _runtime_discontinuities(operational_events or [])
    trades = []
    for row in ledger_rows:
        sig_id = row.get("sig_id") or row.get("sig")
        trades.append(build_replay_trade(
            row,
            grouped_events.get(sig_id, []),
            operational_context=_operational_context_for_trade(
                row,
                discontinuities,
            ),
        ))
    return trades


def write_replay_trades(
    ledger_rows: Iterable[dict],
    grouped_events: dict[str, list[dict]] | None,
    output_path: Path,
    *,
    operational_events: Iterable[dict] | None = None,
) -> list[dict]:
    trades = build_replay_trades(
        ledger_rows,
        grouped_events,
        operational_events=operational_events,
    )
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
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    manifest_path = (
        args.manifest
        or replay_source_contract.default_manifest_path(args.output)
    )
    args.output.unlink(missing_ok=True)
    manifest_path.unlink(missing_ok=True)
    try:
        source_hashes = {
            "ledger": replay_source_contract.sha256_file(args.ledger),
            "trade_events": replay_source_contract.sha256_file(args.events),
        }
        ledger_rows = load_jsonl(args.ledger)
        all_events = load_jsonl(args.events)
        grouped_events = events_by_signal(all_events)
        trades = write_replay_trades(
            ledger_rows,
            grouped_events,
            args.output,
            operational_events=all_events,
        )
        manifest_path = replay_source_contract.write_manifest(
            replay_path=args.output,
            ledger_path=args.ledger,
            events_path=args.events,
            row_count=len(trades),
            manifest_path=manifest_path,
        )
        final_source_hashes = {
            "ledger": replay_source_contract.sha256_file(args.ledger),
            "trade_events": replay_source_contract.sha256_file(args.events),
        }
        if final_source_hashes != source_hashes:
            raise RuntimeError("replay sources changed during build")
    except BaseException:
        args.output.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
        raise

    if not args.quiet:
        replay_ready = sum(1 for t in trades if t["replay_ready"])
        simulation_ready = sum(1 for t in trades if t["simulation_ready"])
        print(f"Replay trades: {len(trades)}")
        print(f"Simulation ready: {simulation_ready}")
        print(f"Replay ready: {replay_ready}")
        print(f"Output: {args.output}")
        print(f"Source manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
