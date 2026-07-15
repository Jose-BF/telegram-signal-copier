"""Validate observed MT5 closures against cached bid/ask ticks."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from tools import ensure_replay_tick_cache


DATA_DIR = Path(__file__).parent / "data"
DEFAULT_REPLAY_FILE = DATA_DIR / "replay_trades.jsonl"
DEFAULT_TICK_CACHE_DIR = DATA_DIR / "ticks_cache"
DEFAULT_OUTPUT = DATA_DIR / "observed_tick_replay_audit.jsonl"
DEFAULT_STATUS = DATA_DIR / "observed_tick_replay_status.json"
SCHEMA_VERSION = 1
PRICE_EPSILON = 0.01
ALIGNMENT_NEAR_SECONDS = 5
CLOSE_TOUCH_TIME_TOLERANCE_SECONDS = 5
CAUSAL_PATH_CONTRACT = "causal_path_v2"
FILL_PRICE_AUTHORITY = "mt5_deals"
MARKET_CLOSE_REASONS = {"bot_close", "other", "manual_close"}
SUPPORTED_CLOSE_REASONS = {"tp", "sl", "be", *MARKET_CLOSE_REASONS}


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
            level = float(value)
        except (TypeError, ValueError):
            continue
        if level <= 0:
            continue
        events.append((ts, level))
    return sorted(events, key=lambda item: item[0])


def _has_inactive_level_marker(history: Iterable[dict], key: str) -> bool:
    for item in history or []:
        if item.get("status") not in (None, "confirmed", "snapshot"):
            continue
        value = item.get(key)
        if value is None:
            continue
        try:
            if float(value) <= 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


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


def _active_levels(events: list[tuple[datetime, float]],
                   tick_ns: np.ndarray) -> np.ndarray:
    levels = np.full(len(tick_ns), np.nan, dtype=float)
    if not events or len(tick_ns) == 0:
        return levels
    event_ns = np.array(
        [pd.Timestamp(ts).value for ts, _value in events],
        dtype=np.int64,
    )
    event_levels = np.array([float(value) for _ts, value in events], dtype=float)
    indexes = np.searchsorted(event_ns, tick_ns, side="right") - 1
    valid = indexes >= 0
    levels[valid] = event_levels[indexes[valid]]
    return levels


def _first_touch_for_ticks(
    direction: str,
    ticket: dict,
    window_ticks: pd.DataFrame,
    sl_events: list[tuple[datetime, float]],
    tp_events: list[tuple[datetime, float]],
) -> dict | None:
    if window_ticks.empty:
        return None
    tick_times = pd.to_datetime(window_ticks["time_utc"], utc=True)
    tick_ns = tick_times.dt.as_unit("ns").astype("int64").to_numpy()
    sl_levels = _active_levels(sl_events, tick_ns)
    tp_levels = _active_levels(tp_events, tick_ns)
    bid = pd.to_numeric(window_ticks["bid"], errors="coerce").to_numpy(dtype=float)
    ask = pd.to_numeric(window_ticks["ask"], errors="coerce").to_numpy(dtype=float)

    if direction == "BUY":
        side = "bid"
        side_prices = bid
        sl_touch = ~np.isnan(sl_levels) & (bid <= sl_levels)
        tp_touch = ~np.isnan(tp_levels) & (bid >= tp_levels)
    elif direction == "SELL":
        side = "ask"
        side_prices = ask
        sl_touch = ~np.isnan(sl_levels) & (ask >= sl_levels)
        tp_touch = ~np.isnan(tp_levels) & (ask <= tp_levels)
    else:
        return None

    touched = np.flatnonzero(sl_touch | tp_touch)
    if len(touched) == 0:
        return None
    idx = int(touched[0])
    is_sl = bool(sl_touch[idx])
    level = float(sl_levels[idx] if is_sl else tp_levels[idx])
    tick_time = pd.Timestamp(tick_times.iloc[idx]).to_pydatetime().astimezone(
        timezone.utc)
    return {
        "reason": _sl_reason(ticket, level) if is_sl else "tp",
        "level": round(level, 2),
        "side": side,
        "side_price": round(float(side_prices[idx]), 2),
        "time_utc": _iso(tick_time),
    }


def _market_close_for_ticket(
    direction: str,
    ticket: dict,
    ticks: pd.DataFrame,
    *,
    close_reason: str,
    close_grace_s: int = 5,
) -> tuple[dict | None, list[str], list[str]]:
    label = _ticket_label(ticket)
    blockers: list[str] = []
    warnings: list[str] = []
    closed = _parse_dt(ticket.get("close_dt_utc"))
    if closed is None or ticks.empty or "time_utc" not in ticks.columns:
        return None, [f"missing_ticks_near_bot_close:{label}"], warnings
    if direction == "BUY":
        side = "bid"
    elif direction == "SELL":
        side = "ask"
    else:
        return None, ["missing_direction"], warnings
    try:
        close_price = float(ticket.get("close_price"))
    except (TypeError, ValueError):
        return None, [f"missing_ticket_close:{label}"], warnings

    time_col = pd.to_datetime(ticks["time_utc"], utc=True)
    start = closed - timedelta(seconds=close_grace_s)
    end = closed + timedelta(seconds=close_grace_s)
    near = ticks.loc[(time_col >= start) & (time_col <= end)]
    if near.empty:
        return None, [f"missing_ticks_near_bot_close:{label}"], warnings

    near_times = pd.to_datetime(near["time_utc"], utc=True)
    nearest_pos = np.abs(
        near_times.dt.as_unit("ns").astype("int64").to_numpy()
        - pd.Timestamp(closed).value
    ).argmin()
    row = near.iloc[int(nearest_pos)]
    tick_time = pd.Timestamp(row["time_utc"]).to_pydatetime().astimezone(
        timezone.utc)
    side_price = float(row[side])
    price_delta = side_price - close_price
    if abs(price_delta) > PRICE_EPSILON:
        warnings.append(
            f"observed_close_execution_delta:{label}:{price_delta:+.2f}"
        )

    return {
        "reason": (
            "bot_close" if close_reason == "bot_close" else "external_close"
        ),
        "level": round(close_price, 2),
        "side": side,
        "side_price": round(side_price, 2),
        "time_utc": _iso(tick_time),
        "price_delta": round(price_delta, 3),
    }, blockers, warnings


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


def _execution_alignment(
    direction: str,
    ticket: dict,
    ticks: pd.DataFrame,
    *,
    event: str,
) -> dict:
    if event == "open":
        event_dt = _parse_dt(ticket.get("open_dt_utc"))
        reference_price = ticket.get("open_price")
        side = "ask" if direction == "BUY" else "bid"
    else:
        event_dt = _parse_dt(ticket.get("close_dt_utc"))
        reference_price = ticket.get("close_price")
        side = "bid" if direction == "BUY" else "ask"
    if (
        event_dt is None
        or reference_price is None
        or direction not in ("BUY", "SELL")
        or ticks.empty
        or "time_utc" not in ticks.columns
    ):
        return {"status": "unavailable", "side": side}

    time_col = pd.to_datetime(ticks["time_utc"], utc=True)
    near = ticks.loc[
        (time_col >= event_dt - timedelta(seconds=ALIGNMENT_NEAR_SECONDS))
        & (time_col <= event_dt + timedelta(seconds=ALIGNMENT_NEAR_SECONDS))
    ]
    if near.empty:
        return {"status": "unverified", "side": side}

    near_times = pd.to_datetime(near["time_utc"], utc=True)
    deltas_ns = (
        near_times.dt.as_unit("ns").astype("int64").to_numpy()
        - pd.Timestamp(event_dt).value
    )
    nearest_pos = int(np.abs(deltas_ns).argmin())
    row = near.iloc[nearest_pos]
    tick_time = pd.Timestamp(row["time_utc"]).to_pydatetime().astimezone(
        timezone.utc)
    side_price = float(row[side])
    price_delta = side_price - float(reference_price)
    return {
        "status": "verified",
        "side": side,
        "time_utc": _iso(tick_time),
        "time_delta_ms": int(round(deltas_ns[nearest_pos] / 1_000_000)),
        "side_price": round(side_price, 3),
        "reference_price": round(float(reference_price), 3),
        "price_delta": round(price_delta, 3),
    }


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
    if expected_reason not in SUPPORTED_CLOSE_REASONS:
        blockers.append(f"unsupported_close_reason:{label}:{expected_reason or 'unknown'}")

    alignment = {
        "open": _execution_alignment(
            direction, ticket, ticks, event="open"),
        "close": _execution_alignment(
            direction, ticket, ticks, event="close"),
    }
    open_alignment = alignment["open"]
    if open_alignment.get("status") != "verified":
        blockers.append(f"open_tick_alignment_unverified:{label}")
    elif abs(float(open_alignment.get("price_delta") or 0.0)) > PRICE_EPSILON:
        warnings.append(
            f"observed_open_execution_delta:{label}:"
            f"{float(open_alignment['price_delta']):+.2f}"
        )

    base = {
        "ticket": ticket.get("ticket"),
        "status": "blocked",
        "expected_close_reason": expected_reason or None,
        "validation_contract": CAUSAL_PATH_CONTRACT,
        "fill_price_authority": FILL_PRICE_AUTHORITY,
        "first_touch": None,
        "alignment": alignment,
        "blockers": list(dict.fromkeys(blockers)),
        "warnings": warnings,
    }
    if blockers:
        return base

    if expected_reason in MARKET_CLOSE_REASONS:
        first_touch, market_blockers, market_warnings = _market_close_for_ticket(
            direction,
            ticket,
            ticks,
            close_reason=expected_reason,
        )
        warnings.extend(market_warnings)
        status = "exact" if not market_blockers else "mismatch"
        return {
            **base,
            "status": status,
            "first_touch": first_touch,
            "blockers": market_blockers,
            "warnings": warnings,
        }

    sl_history = ticket.get("sl_history") or []
    tp_history = ticket.get("tp_history") or []
    sl_events = _level_events(sl_history, "sl")
    tp_events = _level_events(tp_history, "tp")
    if not sl_events and not _has_inactive_level_marker(sl_history, "sl"):
        blockers.append(f"missing_sl_history:{label}")
    if not tp_events and not _has_inactive_level_marker(tp_history, "tp"):
        blockers.append(f"missing_tp_history:{label}")

    window_ticks = _filter_ticket_ticks(ticket, ticks)
    if window_ticks.empty:
        blockers.append(f"missing_ticks_for_ticket:{label}")
    if blockers:
        return {
            **base,
            "blockers": list(dict.fromkeys(blockers)),
            "warnings": warnings,
        }

    first_touch = _first_touch_for_ticks(
        direction,
        ticket,
        window_ticks,
        sl_events,
        tp_events,
    )

    if first_touch is None:
        return {
            **base,
            "status": "mismatch",
            "blockers": ["no_level_touch_before_close"],
        }

    result_blockers: list[str] = []
    if not _reason_matches(expected_reason, first_touch["reason"]):
        result_blockers.append(
            f"first_touch_reason_mismatch:{first_touch['reason']}!=mt5_{expected_reason}"
        )

    touch_dt = _parse_dt(first_touch.get("time_utc"))
    close_dt = _parse_dt(ticket.get("close_dt_utc"))
    if touch_dt is None or close_dt is None:
        result_blockers.append(f"missing_touch_close_time:{label}")
    else:
        delta_s = (touch_dt - close_dt).total_seconds()
        if abs(delta_s) > CLOSE_TOUCH_TIME_TOLERANCE_SECONDS:
            result_blockers.append(
                f"first_touch_time_mismatch:{label}:{delta_s:+.3f}s"
            )

    close_alignment = alignment["close"]
    if close_alignment.get("status") != "verified":
        result_blockers.append(f"close_tick_alignment_unverified:{label}")
    elif abs(float(close_alignment.get("price_delta") or 0.0)) > PRICE_EPSILON:
        warnings.append(
            f"observed_close_execution_delta:{label}:"
            f"{float(close_alignment['price_delta']):+.2f}"
        )

    try:
        actual_close_price = float(ticket.get("close_price"))
        modeled_close_price = float(
            first_touch["level"]
            if first_touch["reason"] == "tp"
            else first_touch["side_price"]
        )
    except (KeyError, TypeError, ValueError):
        result_blockers.append(f"missing_modeled_close_price:{label}")
    else:
        price_delta = modeled_close_price - actual_close_price
        if abs(price_delta) > PRICE_EPSILON:
            warnings.append(
                f"observed_level_fill_delta:{label}:{price_delta:+.2f}"
            )

    status = "exact" if not result_blockers else "mismatch"
    return {
        **base,
        "status": status,
        "first_touch": first_touch,
        "blockers": result_blockers,
        "warnings": warnings,
    }


def _required_tick_days(trade: dict, pad_minutes: int) -> list[str]:
    return [
        day.isoformat()
        for day in ensure_replay_tick_cache.required_dates(
            [trade],
            pad_minutes=pad_minutes,
        )
    ]


class ReplayTickFrameCache:
    def __init__(self, tick_cache_dir: Path):
        self.tick_cache_dir = Path(tick_cache_dir)
        self._frames: dict[str, pd.DataFrame] = {}
        self._required_days: set[str] = set()
        self._verified_contracts: dict[str, dict] = {}

    @property
    def required_days(self) -> list[str]:
        return sorted(self._required_days)

    @property
    def verified_contracts(self) -> dict[str, dict]:
        return {
            day: dict(contract)
            for day, contract in sorted(self._verified_contracts.items())
        }

    def _load_day(self, day: str) -> tuple[pd.DataFrame | None, str | None]:
        if day in self._frames:
            return self._frames[day], None
        path = self.tick_cache_dir / f"{day}.parquet"
        if not path.exists():
            return None, f"missing_tick_cache:{day}"
        contract = ensure_replay_tick_cache.load_valid_day_contract(
            self.tick_cache_dir,
            datetime.fromisoformat(day).date(),
        )
        if contract is None:
            return None, f"invalid_tick_cache_contract:{day}"
        try:
            frame = pd.read_parquet(path)
        except Exception as exc:
            return None, f"tick_cache_read_failed:{day}:{type(exc).__name__}"
        if not frame.empty:
            frame = frame.copy()
            frame["time_utc"] = pd.to_datetime(frame["time_utc"], utc=True)
            frame = frame.sort_values("time_utc").reset_index(drop=True)
        self._frames[day] = frame
        self._verified_contracts[day] = contract
        return frame, None

    def load_ticks_for_trade(
        self,
        trade: dict,
        *,
        pad_minutes: int = 5,
    ) -> tuple[pd.DataFrame, list[str]]:
        missing: list[str] = []
        frames: list[pd.DataFrame] = []
        for day in _required_tick_days(trade, pad_minutes):
            self._required_days.add(day)
            frame, error = self._load_day(day)
            if error:
                missing.append(error)
                continue
            if frame is not None and not frame.empty:
                frames.append(frame)
        if not frames:
            return pd.DataFrame(), missing
        ticks = pd.concat(frames, ignore_index=True).sort_values("time_utc")
        return ticks.reset_index(drop=True), missing


def load_ticks_for_trade(
    trade: dict,
    *,
    tick_cache_dir: Path,
    pad_minutes: int = 5,
) -> tuple[pd.DataFrame, list[str]]:
    return ReplayTickFrameCache(tick_cache_dir).load_ticks_for_trade(
        trade,
        pad_minutes=pad_minutes,
    )


def validate_trade(
    trade: dict,
    *,
    tick_cache_dir: Path = DEFAULT_TICK_CACHE_DIR,
    pad_minutes: int = 5,
    tick_loader: ReplayTickFrameCache | None = None,
) -> dict:
    if tick_loader is None:
        ticks, missing = load_ticks_for_trade(
            trade,
            tick_cache_dir=tick_cache_dir,
            pad_minutes=pad_minutes,
        )
    else:
        ticks, missing = tick_loader.load_ticks_for_trade(
            trade,
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
        "validation_contract": CAUSAL_PATH_CONTRACT,
        "fill_price_authority": FILL_PRICE_AUTHORITY,
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
        "schema_version": SCHEMA_VERSION,
        "validation_contract": CAUSAL_PATH_CONTRACT,
        "fill_price_authority": FILL_PRICE_AUTHORITY,
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

    tick_loader = ReplayTickFrameCache(args.tick_cache_dir)
    rows = [
        validate_trade(
            trade,
            tick_cache_dir=args.tick_cache_dir,
            pad_minutes=args.pad_minutes,
            tick_loader=tick_loader,
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
