"""Validate observed MT5 closures against cached bid/ask ticks."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

from tools import cache_replay_ticks


DATA_DIR = Path(__file__).parent / "data"
DEFAULT_REPLAY_FILE = DATA_DIR / "replay_trades.jsonl"
DEFAULT_TICK_CACHE_DIR = DATA_DIR / "ticks_cache"
DEFAULT_OUTPUT = DATA_DIR / "tick_replay_audit.jsonl"
DEFAULT_STATUS = DATA_DIR / "tick_replay_status.json"
SCHEMA_VERSION = 1
PRICE_EPSILON = 0.01


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


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _ticket_label(ticket: dict) -> str:
    value = ticket.get("ticket") or ticket.get("position_ticket")
    if value is None:
        return "unknown"
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value)


def _level_events(history: Iterable[dict], key: str) -> list[tuple[datetime, float]]:
    events: list[tuple[datetime, float]] = []
    for item in history or []:
        if item.get("status") not in (None, "confirmed", "snapshot"):
            continue
        ts = _parse_dt(item.get("ts"))
        value = item.get(key)
        if ts is None or value is None:
            continue
        try:
            events.append((ts, float(value)))
        except (TypeError, ValueError):
            continue
    return sorted(events, key=lambda item: item[0])


def _direction(trade: dict) -> str:
    return str(trade.get("direction") or "").upper()


def _sl_reason(ticket: dict, sl: float) -> str:
    open_price = ticket.get("open_price")
    try:
        if abs(float(open_price) - float(sl)) <= PRICE_EPSILON:
            return "be"
    except (TypeError, ValueError):
        pass
    return "sl"


def _tick_time(row) -> datetime:
    return pd.Timestamp(row["time_utc"]).to_pydatetime().astimezone(timezone.utc)


def _touch_for_tick(direction: str, ticket: dict, row, sl: float | None,
                    tp: float | None) -> dict | None:
    bid = float(row["bid"])
    ask = float(row["ask"])
    tick_time = _tick_time(row)
    if direction == "BUY":
        if sl is not None and bid <= sl:
            return {
                "reason": _sl_reason(ticket, sl),
                "level": round(sl, 2),
                "side": "bid",
                "side_price": round(bid, 2),
                "time_utc": _iso(tick_time),
            }
        if tp is not None and bid >= tp:
            return {
                "reason": "tp",
                "level": round(tp, 2),
                "side": "bid",
                "side_price": round(bid, 2),
                "time_utc": _iso(tick_time),
            }
    elif direction == "SELL":
        if sl is not None and ask >= sl:
            return {
                "reason": _sl_reason(ticket, sl),
                "level": round(sl, 2),
                "side": "ask",
                "side_price": round(ask, 2),
                "time_utc": _iso(tick_time),
            }
        if tp is not None and ask <= tp:
            return {
                "reason": "tp",
                "level": round(tp, 2),
                "side": "ask",
                "side_price": round(ask, 2),
                "time_utc": _iso(tick_time),
            }
    return None


def _reason_matches(expected: str | None, observed: str | None) -> bool:
    expected = (expected or "").lower()
    observed = (observed or "").lower()
    if expected == observed:
        return True
    if expected == "sl" and observed == "be":
        return True
    if expected == "be" and observed in ("sl", "be"):
        return True
    return False


def _filter_ticket_ticks(ticket: dict, ticks: pd.DataFrame,
                         close_grace_s: int = 2) -> pd.DataFrame:
    opened = _parse_dt(ticket.get("open_dt_utc"))
    closed = _parse_dt(ticket.get("close_dt_utc"))
    if opened is None or closed is None or ticks.empty:
        return pd.DataFrame()
    if "time_utc" not in ticks.columns:
        return pd.DataFrame()
    time_col = pd.to_datetime(ticks["time_utc"], utc=True)
    end = closed + timedelta(seconds=close_grace_s)
    mask = (time_col >= opened) & (time_col <= end)
    out = ticks.loc[mask].copy()
    if not out.empty:
        out["time_utc"] = pd.to_datetime(out["time_utc"], utc=True)
        out = out.sort_values("time_utc").reset_index(drop=True)
    return out


def validate_ticket(trade: dict, ticket: dict, ticks: pd.DataFrame) -> dict:
    label = _ticket_label(ticket)
    blockers: list[str] = []
    warnings: list[str] = []
    direction = _direction(trade)
    if direction not in ("BUY", "SELL"):
        blockers.append("missing_direction")
    if not ticket.get("is_closed"):
        blockers.append(f"ticket_not_closed:{label}")
    if not ticket.get("open_dt_utc") or ticket.get("open_price") is None:
        blockers.append(f"missing_ticket_open:{label}")
    if not ticket.get("close_dt_utc") or ticket.get("close_price") is None:
        blockers.append(f"missing_ticket_close:{label}")
    expected_reason = (ticket.get("close_reason") or "").lower()
    if expected_reason not in ("tp", "sl", "be"):
        blockers.append(f"unsupported_close_reason:{label}:{expected_reason or 'unknown'}")

    sl_events = _level_events(ticket.get("sl_history") or [], "sl")
    tp_events = _level_events(ticket.get("tp_history") or [], "tp")
    if not sl_events:
        blockers.append(f"missing_sl_history:{label}")
    if not tp_events:
        blockers.append(f"missing_tp_history:{label}")

    window_ticks = _filter_ticket_ticks(ticket, ticks)
    if window_ticks.empty:
        blockers.append(f"missing_ticks_for_ticket:{label}")

    base = {
        "ticket": ticket.get("ticket"),
        "status": "blocked",
        "expected_close_reason": expected_reason or None,
        "first_touch": None,
        "blockers": list(dict.fromkeys(blockers)),
        "warnings": warnings,
    }
    if blockers:
        return base

    sl_idx = 0
    tp_idx = 0
    active_sl = None
    active_tp = None
    first_touch = None
    for _, row in window_ticks.iterrows():
        tick_time = _tick_time(row)
        while sl_idx < len(sl_events) and sl_events[sl_idx][0] <= tick_time:
            active_sl = sl_events[sl_idx][1]
            sl_idx += 1
        while tp_idx < len(tp_events) and tp_events[tp_idx][0] <= tick_time:
            active_tp = tp_events[tp_idx][1]
            tp_idx += 1
        first_touch = _touch_for_tick(direction, ticket, row, active_sl, active_tp)
        if first_touch is not None:
            break

    if first_touch is None:
        return {
            **base,
            "status": "mismatch",
            "blockers": ["no_level_touch_before_close"],
        }

    status = "exact" if _reason_matches(expected_reason, first_touch["reason"]) else "mismatch"
    result_blockers = [] if status == "exact" else [
        f"first_touch_reason_mismatch:{first_touch['reason']}!=mt5_{expected_reason}"
    ]
    return {
        **base,
        "status": status,
        "first_touch": first_touch,
        "blockers": result_blockers,
    }


def _required_tick_days(trade: dict, pad_minutes: int) -> list[str]:
    return [
        day.isoformat()
        for day in cache_replay_ticks.required_dates(
            [trade],
            pad_minutes=pad_minutes,
        )
    ]


def load_ticks_for_trade(
    trade: dict,
    *,
    tick_cache_dir: Path,
    pad_minutes: int = 5,
) -> tuple[pd.DataFrame, list[str]]:
    missing: list[str] = []
    frames: list[pd.DataFrame] = []
    for day in _required_tick_days(trade, pad_minutes):
        path = tick_cache_dir / f"{day}.parquet"
        if not path.exists():
            missing.append(f"missing_tick_cache:{day}")
            continue
        try:
            frame = pd.read_parquet(path)
        except Exception as exc:
            missing.append(f"tick_cache_read_failed:{day}:{type(exc).__name__}")
            continue
        if not frame.empty:
            frame["time_utc"] = pd.to_datetime(frame["time_utc"], utc=True)
            frames.append(frame)
    if not frames:
        return pd.DataFrame(), missing
    ticks = pd.concat(frames, ignore_index=True).sort_values("time_utc")
    return ticks.reset_index(drop=True), missing


def validate_trade(
    trade: dict,
    *,
    tick_cache_dir: Path = DEFAULT_TICK_CACHE_DIR,
    pad_minutes: int = 5,
) -> dict:
    ticks, missing = load_ticks_for_trade(
        trade,
        tick_cache_dir=tick_cache_dir,
        pad_minutes=pad_minutes,
    )
    tickets = trade.get("tickets") or []
    ticket_results = []
    if missing:
        ticket_results = [
            {
                "ticket": ticket.get("ticket"),
                "status": "blocked",
                "expected_close_reason": ticket.get("close_reason"),
                "first_touch": None,
                "blockers": missing,
                "warnings": [],
            }
            for ticket in tickets
        ]
    else:
        ticket_results = [
            validate_ticket(trade, ticket, ticks)
            for ticket in tickets
        ]

    statuses = Counter(result["status"] for result in ticket_results)
    blockers = list(dict.fromkeys(
        blocker
        for result in ticket_results
        for blocker in result.get("blockers") or []
    ))
    if not tickets and trade.get("status") != "no_position":
        blockers.append("missing_tickets")
    if blockers:
        status = "blocked" if statuses.get("blocked", 0) else "mismatch"
    elif statuses.get("exact", 0) == len(ticket_results):
        status = "exact"
    else:
        status = "mismatch"

    return {
        "schema_version": SCHEMA_VERSION,
        "sig_id": trade.get("sig_id"),
        "channel": trade.get("channel"),
        "direction": trade.get("direction"),
        "stage": "tick_replay_observed",
        "status": status,
        "ticket_count": len(tickets),
        "exact_tickets": statuses.get("exact", 0),
        "mismatch_tickets": statuses.get("mismatch", 0),
        "blocked_tickets": statuses.get("blocked", 0),
        "tick_days": _required_tick_days(trade, pad_minutes),
        "blockers": blockers,
        "tickets": ticket_results,
    }


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_jsonl(rows: Iterable[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def summarize(rows: Iterable[dict]) -> dict:
    rows = list(rows)
    counts = Counter(row.get("status") for row in rows)
    return {
        "total": len(rows),
        "exact": counts.get("exact", 0),
        "mismatch": counts.get("mismatch", 0),
        "blocked": counts.get("blocked", 0),
    }


def write_status(rows: list[dict], path: Path) -> dict:
    status = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": summarize(rows),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate observed MT5 closes against cached bid/ask ticks")
    parser.add_argument("--input", type=Path, default=DEFAULT_REPLAY_FILE)
    parser.add_argument("--tick-cache-dir", type=Path, default=DEFAULT_TICK_CACHE_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--pad-minutes", type=int, default=5)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    rows = [
        validate_trade(
            trade,
            tick_cache_dir=args.tick_cache_dir,
            pad_minutes=args.pad_minutes,
        )
        for trade in load_jsonl(args.input)
    ]
    write_jsonl(rows, args.output)
    status = write_status(rows, args.status)
    if not args.quiet:
        summary = status["summary"]
        print(f"Tick replay audit: {summary['total']} trades")
        print(f"Exact: {summary['exact']}")
        print(f"Mismatch: {summary['mismatch']}")
        print(f"Blocked: {summary['blocked']}")
        print(f"Output: {args.output}")
    return 0 if status["summary"]["mismatch"] == 0 and status["summary"]["blocked"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
