"""Auditable strategy simulations over replay trades and cached MT5 ticks.

This module is intentionally conservative:

* A strategy cannot run unless the observed tick replay for that trade is exact.
* A no-op branch must leave the real MT5 result untouched.
* Strategy changes are recorded per ticket, with assumptions and blockers.

The first promoted hypothesis is ``no_be``: ignore SL moves caused by BE
management, while keeping the provider's entries, TP levels and original SL.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

import observed_tick_replay_validator


DATA_DIR = Path(__file__).parent / "data"
DEFAULT_REPLAY_FILE = DATA_DIR / "replay_trades.jsonl"
DEFAULT_BASELINE_AUDIT_FILE = DATA_DIR / "observed_tick_replay_audit.jsonl"
DEFAULT_TICK_CACHE_DIR = DATA_DIR / "ticks_cache"
DEFAULT_OUTPUT = DATA_DIR / "strategy_simulation.json"
SCHEMA_VERSION = 1
PRICE_EPSILON = 0.01
BE_PRICE_EPSILON = 0.05


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _round_money(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


def _ticket_label(ticket: dict) -> str:
    value = ticket.get("ticket") or ticket.get("position_ticket")
    if value is None:
        return "unknown"
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value)


def _direction(trade: dict) -> str:
    return str(trade.get("direction") or "").upper()


def _actual_ticket_pnl(ticket: dict) -> float:
    try:
        return float(ticket.get("pnl_net") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _actual_trade_pnl(trade: dict) -> float:
    try:
        return float(trade.get("pnl_real_mt5"))
    except (TypeError, ValueError):
        return sum(_actual_ticket_pnl(ticket) for ticket in trade.get("tickets") or [])


def _directional_price_delta(direction: str, open_price: float,
                             close_price: float) -> float:
    if direction == "BUY":
        return close_price - open_price
    if direction == "SELL":
        return open_price - close_price
    return 0.0


def _infer_unit_values(trade: dict) -> list[float]:
    direction = _direction(trade)
    values: list[float] = []
    for ticket in trade.get("tickets") or []:
        try:
            open_price = float(ticket.get("open_price"))
            close_price = float(ticket.get("close_price"))
            volume = float(ticket.get("volume") or 1.0)
            pnl = float(ticket.get("pnl_net"))
        except (TypeError, ValueError):
            continue
        denominator = _directional_price_delta(
            direction, open_price, close_price) * volume
        if abs(denominator) <= PRICE_EPSILON or abs(pnl) <= PRICE_EPSILON:
            continue
        values.append(pnl / denominator)
    return values


def _unit_value_for_ticket(
    trade: dict,
    ticket: dict,
    *,
    default_unit_value: float,
    default_unit_source: str = "default_unit_value",
) -> tuple[float, str, list[str]]:
    direction = _direction(trade)
    label = _ticket_label(ticket)
    try:
        open_price = float(ticket.get("open_price"))
        close_price = float(ticket.get("close_price"))
        volume = float(ticket.get("volume") or 1.0)
        pnl = float(ticket.get("pnl_net"))
    except (TypeError, ValueError):
        return (
            float(default_unit_value),
            default_unit_source,
            [f"{default_unit_source}:{label}"],
        )
    denominator = _directional_price_delta(direction, open_price, close_price) * volume
    if abs(denominator) > PRICE_EPSILON and abs(pnl) > PRICE_EPSILON:
        return pnl / denominator, "ticket_mt5_calibrated", []

    trade_values = _infer_unit_values(trade)
    if trade_values:
        return float(np.median(trade_values)), "trade_mt5_calibrated", []

    return (
        float(default_unit_value),
        default_unit_source,
        [f"{default_unit_source}:{label}"],
    )


def _pnl_from_prices(
    trade: dict,
    ticket: dict,
    *,
    close_price: float,
    unit_value: float,
) -> float:
    direction = _direction(trade)
    open_price = float(ticket.get("open_price"))
    volume = float(ticket.get("volume") or 1.0)
    return (
        _directional_price_delta(direction, open_price, close_price)
        * volume
        * unit_value
    )


def _level_events(history: Iterable[dict], key: str) -> list[dict]:
    events: list[dict] = []
    for item in history or []:
        if item.get("status") not in (None, "confirmed", "snapshot"):
            continue
        ts = _parse_dt(item.get("ts"))
        value = item.get(key)
        if ts is None or value is None:
            continue
        try:
            level = float(value)
        except (TypeError, ValueError):
            continue
        if level <= 0:
            continue
        events.append({
            "ts": ts,
            "level": level,
            "source": str(item.get("source") or ""),
            "raw": item,
        })
    return sorted(events, key=lambda event: event["ts"])


def _is_be_sl_event(ticket: dict, event: dict) -> bool:
    source = str(event.get("source") or "").upper()
    if "BE" in source or "BREAKEVEN" in source or "BREAK_EVEN" in source:
        return True
    try:
        open_price = float(ticket.get("open_price"))
        level = float(event.get("level"))
    except (TypeError, ValueError):
        return False
    return abs(level - open_price) <= BE_PRICE_EPSILON


def _filter_no_be_sl_events(ticket: dict) -> tuple[list[dict], bool]:
    events = _level_events(ticket.get("sl_history") or [], "sl")
    filtered = [
        event
        for event in events
        if not _is_be_sl_event(ticket, event)
    ]
    return filtered, len(filtered) != len(events)


def _active_levels(events: list[dict], tick_ns: np.ndarray) -> np.ndarray:
    levels = np.full(len(tick_ns), np.nan, dtype=float)
    if not events or len(tick_ns) == 0:
        return levels
    event_ns = np.array(
        [pd.Timestamp(event["ts"]).value for event in events],
        dtype=np.int64,
    )
    event_levels = np.array(
        [float(event["level"]) for event in events],
        dtype=float,
    )
    indexes = np.searchsorted(event_ns, tick_ns, side="right") - 1
    valid = indexes >= 0
    levels[valid] = event_levels[indexes[valid]]
    return levels


def _normalise_ticks(ticks: pd.DataFrame) -> pd.DataFrame:
    if ticks.empty:
        return ticks.copy()
    frame = ticks.copy()
    frame["time_utc"] = pd.to_datetime(frame["time_utc"], utc=True)
    return frame.sort_values("time_utc").reset_index(drop=True)


def _eod_horizon(opened: datetime) -> datetime:
    return datetime.combine(opened.date(), time(23, 59, 59), tzinfo=timezone.utc)


def _first_strategy_close(
    trade: dict,
    ticket: dict,
    ticks: pd.DataFrame,
    *,
    sl_events: list[dict],
    tp_events: list[dict],
    horizon_policy: str,
) -> tuple[dict | None, list[str], list[str]]:
    blockers: list[str] = []
    assumptions: list[str] = []
    direction = _direction(trade)
    opened = _parse_dt(ticket.get("open_dt_utc"))
    if opened is None or ticket.get("open_price") is None:
        return None, [f"missing_ticket_open:{_ticket_label(ticket)}"], assumptions
    if direction not in ("BUY", "SELL"):
        return None, ["missing_direction"], assumptions
    if not sl_events:
        blockers.append(f"missing_strategy_sl:{_ticket_label(ticket)}")
    if not tp_events:
        blockers.append(f"missing_strategy_tp:{_ticket_label(ticket)}")
    if ticks.empty:
        blockers.append(f"missing_ticks:{_ticket_label(ticket)}")
    if blockers:
        return None, blockers, assumptions

    ticks = _normalise_ticks(ticks)
    horizon = _eod_horizon(opened)
    time_col = pd.to_datetime(ticks["time_utc"], utc=True)
    window = ticks.loc[
        (time_col >= pd.Timestamp(opened))
        & (time_col <= pd.Timestamp(horizon))
    ].copy()
    if window.empty:
        return None, [f"missing_ticks_after_open:{_ticket_label(ticket)}"], assumptions

    tick_times = pd.to_datetime(window["time_utc"], utc=True)
    tick_ns = tick_times.dt.as_unit("ns").astype("int64").to_numpy()
    sl_levels = _active_levels(sl_events, tick_ns)
    tp_levels = _active_levels(tp_events, tick_ns)
    bid = pd.to_numeric(window["bid"], errors="coerce").to_numpy(dtype=float)
    ask = pd.to_numeric(window["ask"], errors="coerce").to_numpy(dtype=float)

    if direction == "BUY":
        side = "bid"
        side_prices = bid
        sl_touch = ~np.isnan(sl_levels) & (bid <= sl_levels)
        tp_touch = ~np.isnan(tp_levels) & (bid >= tp_levels)
    else:
        side = "ask"
        side_prices = ask
        sl_touch = ~np.isnan(sl_levels) & (ask >= sl_levels)
        tp_touch = ~np.isnan(tp_levels) & (ask <= tp_levels)

    touched = np.flatnonzero(sl_touch | tp_touch)
    if len(touched) > 0:
        idx = int(touched[0])
        is_sl = bool(sl_touch[idx])
        level = float(sl_levels[idx] if is_sl else tp_levels[idx])
        return {
            "reason": "sl" if is_sl else "tp",
            "close_price": round(level, 2),
            "side": side,
            "side_price": round(float(side_prices[idx]), 2),
            "time_utc": _iso(pd.Timestamp(tick_times.iloc[idx]).to_pydatetime()),
        }, blockers, assumptions

    if horizon_policy != "eod_close":
        return None, [f"no_touch_before_horizon:{_ticket_label(ticket)}"], assumptions

    last = window.iloc[-1]
    close_price = float(last["bid"] if direction == "BUY" else last["ask"])
    assumptions.append("horizon_close:eod")
    return {
        "reason": "horizon_close",
        "close_price": round(close_price, 2),
        "side": "bid" if direction == "BUY" else "ask",
        "side_price": round(close_price, 2),
        "time_utc": _iso(pd.Timestamp(last["time_utc"]).to_pydatetime()),
    }, blockers, assumptions


def _unchanged_ticket_result(ticket: dict) -> dict:
    pnl = _actual_ticket_pnl(ticket)
    return {
        "ticket": ticket.get("ticket"),
        "status": "unchanged_no_strategy_event",
        "changed_rules": [],
        "actual_pnl": _round_money(pnl),
        "strategy_pnl": _round_money(pnl),
        "delta_pnl": 0.0,
        "close_reason": ticket.get("close_reason"),
        "close_time_utc": _iso(_parse_dt(ticket.get("close_dt_utc"))),
        "close_price": ticket.get("close_price"),
        "pnl_source": "mt5_actual",
        "blockers": [],
        "assumptions": [],
    }


def _simulate_ticket_no_be(
    trade: dict,
    ticket: dict,
    ticks: pd.DataFrame,
    *,
    default_unit_value: float,
    default_unit_source: str,
    horizon_policy: str,
) -> dict:
    sl_events, changed = _filter_no_be_sl_events(ticket)
    if not changed:
        return _unchanged_ticket_result(ticket)

    tp_events = _level_events(ticket.get("tp_history") or [], "tp")
    close, blockers, assumptions = _first_strategy_close(
        trade,
        ticket,
        ticks,
        sl_events=sl_events,
        tp_events=tp_events,
        horizon_policy=horizon_policy,
    )
    if blockers or close is None:
        return {
            "ticket": ticket.get("ticket"),
            "status": "blocked",
            "changed_rules": ["ignored_be_sl"],
            "actual_pnl": _round_money(_actual_ticket_pnl(ticket)),
            "strategy_pnl": None,
            "delta_pnl": None,
            "close_reason": None,
            "close_time_utc": None,
            "close_price": None,
            "pnl_source": None,
            "blockers": list(dict.fromkeys(blockers)),
            "assumptions": list(dict.fromkeys(assumptions)),
        }

    unit_value, pnl_source, pnl_assumptions = _unit_value_for_ticket(
        trade,
        ticket,
        default_unit_value=default_unit_value,
        default_unit_source=default_unit_source,
    )
    strategy_pnl = _pnl_from_prices(
        trade,
        ticket,
        close_price=float(close["close_price"]),
        unit_value=unit_value,
    )
    actual_pnl = _actual_ticket_pnl(ticket)
    assumptions.extend(pnl_assumptions)
    return {
        "ticket": ticket.get("ticket"),
        "status": "simulated",
        "changed_rules": ["ignored_be_sl"],
        "actual_pnl": _round_money(actual_pnl),
        "strategy_pnl": _round_money(strategy_pnl),
        "delta_pnl": _round_money(strategy_pnl - actual_pnl),
        "close_reason": close["reason"],
        "close_time_utc": close["time_utc"],
        "close_price": close["close_price"],
        "touch_side": close["side"],
        "touch_side_price": close["side_price"],
        "pnl_source": pnl_source,
        "blockers": [],
        "assumptions": list(dict.fromkeys(assumptions)),
    }


def _baseline_blockers(baseline_audit: dict | None) -> list[str]:
    if baseline_audit is None:
        return ["missing_baseline_tick_replay"]
    status = baseline_audit.get("status")
    if status == "exact":
        return []
    blockers = [f"baseline_not_exact:{status or 'unknown'}"]
    blockers.extend(baseline_audit.get("blockers") or [])
    return list(dict.fromkeys(blockers))


def simulate_trade(
    trade: dict,
    ticks: pd.DataFrame,
    *,
    strategy_name: str,
    baseline_audit: dict | None,
    default_unit_value: float = 1.0,
    default_unit_source: str = "default_unit_value",
    horizon_policy: str = "eod_close",
) -> dict:
    """Simulate one management strategy for one replay trade."""
    if strategy_name != "no_be":
        return {
            "schema_version": SCHEMA_VERSION,
            "sig_id": trade.get("sig_id"),
            "status": "blocked",
            "strategy": strategy_name,
            "actual_pnl": _round_money(_actual_trade_pnl(trade)),
            "strategy_pnl": None,
            "delta_pnl": None,
            "blockers": [f"unsupported_strategy:{strategy_name}"],
            "assumptions": [],
            "tickets": [],
        }

    baseline_errors = _baseline_blockers(baseline_audit)
    if baseline_errors:
        return {
            "schema_version": SCHEMA_VERSION,
            "sig_id": trade.get("sig_id"),
            "channel": trade.get("channel"),
            "direction": trade.get("direction"),
            "open_dt_utc": trade.get("open_dt_utc"),
            "status": "blocked",
            "strategy": strategy_name,
            "actual_pnl": _round_money(_actual_trade_pnl(trade)),
            "strategy_pnl": None,
            "delta_pnl": None,
            "blockers": baseline_errors,
            "assumptions": [],
            "tickets": [],
        }

    tickets = trade.get("tickets") or []
    ticket_results = [
        _simulate_ticket_no_be(
            trade,
            ticket,
            ticks,
            default_unit_value=default_unit_value,
            default_unit_source=default_unit_source,
            horizon_policy=horizon_policy,
        )
        for ticket in tickets
    ]
    blockers = list(dict.fromkeys(
        blocker
        for result in ticket_results
        for blocker in result.get("blockers") or []
    ))
    assumptions = list(dict.fromkeys(
        assumption
        for result in ticket_results
        for assumption in result.get("assumptions") or []
    ))

    actual_pnl = _actual_trade_pnl(trade)
    if blockers:
        strategy_pnl = None
        status = "blocked"
        delta = None
    else:
        strategy_pnl = sum(
            float(result.get("strategy_pnl") or 0.0)
            for result in ticket_results
        )
        if all(result["status"].startswith("unchanged") for result in ticket_results):
            status = "unchanged"
        else:
            status = "simulated"
        delta = strategy_pnl - actual_pnl

    return {
        "schema_version": SCHEMA_VERSION,
        "sig_id": trade.get("sig_id"),
        "channel": trade.get("channel"),
        "direction": trade.get("direction"),
        "open_dt_utc": trade.get("open_dt_utc"),
        "status": status,
        "strategy": strategy_name,
        "actual_pnl": _round_money(actual_pnl),
        "strategy_pnl": _round_money(strategy_pnl),
        "delta_pnl": _round_money(delta),
        "blockers": blockers,
        "assumptions": assumptions,
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


def _date_in_range(trade: dict, from_date: str | None, to_date: str | None) -> bool:
    opened = _parse_dt(trade.get("open_dt_utc") or trade.get("signal_dt_utc"))
    if opened is None:
        return False
    day = opened.date().isoformat()
    if from_date and day < from_date:
        return False
    if to_date and day > to_date:
        return False
    return True


def _baseline_by_sig(rows: Iterable[dict]) -> dict[str, dict]:
    return {
        str(row.get("sig_id")): row
        for row in rows
        if row.get("sig_id") is not None
    }


def _global_unit_value(
    trades: Iterable[dict],
    explicit_default: float | None,
) -> tuple[float, str]:
    if explicit_default is not None:
        return float(explicit_default), "cli_default_unit_value"
    values: list[float] = []
    for trade in trades:
        values.extend(_infer_unit_values(trade))
    if values:
        return float(np.median(values)), "global_mt5_calibrated"
    return 1.0, "default_unit_value"


def _summary(rows: list[dict]) -> dict:
    counts = Counter(row.get("status") for row in rows)
    actual = sum(
        float(row.get("actual_pnl") or 0.0)
        for row in rows
        if row.get("status") != "blocked"
    )
    strategy_values = [
        float(row.get("strategy_pnl") or 0.0)
        for row in rows
        if row.get("status") != "blocked"
    ]
    strategy = sum(strategy_values)
    blocker_counts = Counter(
        blocker
        for row in rows
        for blocker in row.get("blockers") or []
    )
    assumption_counts = Counter(
        assumption
        for row in rows
        for assumption in row.get("assumptions") or []
    )
    return {
        "total": len(rows),
        "simulated": counts.get("simulated", 0),
        "unchanged": counts.get("unchanged", 0),
        "blocked": counts.get("blocked", 0),
        "actual_pnl": _round_money(actual),
        "strategy_pnl": _round_money(strategy) if counts.get("blocked", 0) == 0 else None,
        "delta_pnl": _round_money(strategy - actual) if counts.get("blocked", 0) == 0 else None,
        "top_blockers": blocker_counts.most_common(20),
        "top_assumptions": assumption_counts.most_common(20),
    }


def build_simulation_report(
    trades: list[dict],
    baseline_rows: list[dict],
    *,
    strategy_name: str,
    tick_cache_dir: Path,
    from_date: str | None = None,
    to_date: str | None = None,
    default_unit_value: float | None = None,
    horizon_policy: str = "eod_close",
) -> dict:
    baselines = _baseline_by_sig(baseline_rows)
    tick_loader = observed_tick_replay_validator.ReplayTickFrameCache(tick_cache_dir)
    selected = [
        trade
        for trade in trades
        if _date_in_range(trade, from_date, to_date)
    ]
    unit_value, unit_source = _global_unit_value(selected, default_unit_value)
    rows: list[dict] = []
    for trade in selected:
        ticks, missing = tick_loader.load_ticks_for_trade(trade, pad_minutes=5)
        baseline = baselines.get(str(trade.get("sig_id")))
        if missing:
            baseline = {
                "status": "blocked",
                "blockers": list(dict.fromkeys(missing)),
            }
        rows.append(simulate_trade(
            trade,
            ticks,
            strategy_name=strategy_name,
            baseline_audit=baseline,
            default_unit_value=unit_value,
            default_unit_source=unit_source,
            horizon_policy=horizon_policy,
        ))
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "strategy": strategy_name,
        "from_date": from_date,
        "to_date": to_date,
        "horizon_policy": horizon_policy,
        "calibration": {
            "unit_value": round(unit_value, 8),
            "source": unit_source,
        },
        "summary": _summary(rows),
        "trades": rows,
    }


def write_report(report: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run auditable management strategy simulations")
    parser.add_argument("--strategy", default="no_be")
    parser.add_argument("--replay", type=Path, default=DEFAULT_REPLAY_FILE)
    parser.add_argument(
        "--baseline-audit",
        type=Path,
        default=DEFAULT_BASELINE_AUDIT_FILE,
    )
    parser.add_argument("--tick-cache-dir", type=Path, default=DEFAULT_TICK_CACHE_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--from", dest="from_date")
    parser.add_argument("--from-date", dest="from_date")
    parser.add_argument("--to", dest="to_date")
    parser.add_argument("--to-date", dest="to_date")
    parser.add_argument("--default-unit-value", type=float, default=None)
    parser.add_argument("--horizon-policy", default="eod_close",
                        choices=["eod_close", "block"])
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    report = build_simulation_report(
        load_jsonl(args.replay),
        load_jsonl(args.baseline_audit),
        strategy_name=args.strategy,
        tick_cache_dir=args.tick_cache_dir,
        from_date=args.from_date,
        to_date=args.to_date,
        default_unit_value=args.default_unit_value,
        horizon_policy=args.horizon_policy,
    )
    write_report(report, args.output)
    summary = report["summary"]
    if not args.quiet:
        print(f"Strategy simulation: {args.strategy}")
        print(f"Trades: {summary['total']}")
        print(f"Simulated: {summary['simulated']}")
        print(f"Unchanged: {summary['unchanged']}")
        print(f"Blocked: {summary['blocked']}")
        print(f"Actual P/L: {summary['actual_pnl']}")
        print(f"Strategy P/L: {summary['strategy_pnl']}")
        print(f"Delta: {summary['delta_pnl']}")
        print(f"Output: {args.output}")
    return 0 if summary["blocked"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
