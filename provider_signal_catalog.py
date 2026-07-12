"""Build canonical provider signals from immutable Telegram perception events."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from parser import is_canal1_signal_text, is_canal2_entry, parse_canal2


DATA_DIR = Path(__file__).parent / "data"
DEFAULT_EVENTS = DATA_DIR / "trade_events.jsonl"
DEFAULT_REPLAY = DATA_DIR / "replay_trades.jsonl"
DEFAULT_OUTPUT = DATA_DIR / "provider_signal_catalog.json"
SCHEMA_VERSION = 1
SINGLE_ENTRY_RE = re.compile(
    r"\b(?:BUY|SELL)\s+(?:GOLD|XAUUSD)?\s*(?:NOW|LIMIT)?\s*"
    r"(?:@|AT)?\s*(\d{3,5}(?:\.\d+)?)\b",
    re.IGNORECASE,
)


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _message_id_from_sig(sig_id: str | None) -> tuple[str, int] | None:
    if not sig_id or "_" not in str(sig_id):
        return None
    channel, raw_id = str(sig_id).rsplit("_", 1)
    try:
        return channel, int(raw_id)
    except ValueError:
        return None


def _telegram_ts(row: dict) -> str | None:
    if row.get("is_edit") and row.get("edit_date_utc"):
        return str(row["edit_date_utc"])
    for key in ("date_utc", "tg_ts", "ts"):
        if row.get(key):
            return str(row[key])
    return None


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


def _looks_like_management(text: str) -> bool:
    upper = text.upper()
    return bool(
        re.search(
            r"\b(?:TP\d*|TARGET\d*|SL|BREAKEVEN|BREAK\s*EVEN|"
            r"RISK\s*FREE|CLOSE|PROFIT|PIPS?|ZONE\s*FAILED|"
            r"ALL\s*ENTRIES|LOCK|SECURE|MOVE\s+SL)\b",
            upper,
        )
        or re.search(r"\bBE\b", text)
    )


def _deterministic_management_action(text: str) -> str | None:
    upper = text.upper()
    if (
        re.search(r"\bBE\b|BREAK\s*EVEN|BREAKEVEN", upper)
        or "RISK FREE" in upper
        or "0% RISK" in upper
    ):
        return "MOVE_SL_TO_BE"
    return None


def _normalise_parsed(parsed: dict) -> dict:
    result = dict(parsed)
    if result.get("range") is not None:
        result["range"] = [float(value) for value in result["range"]]
    if result.get("tps") is not None:
        result["tps"] = [float(value) for value in result["tps"]]
    if result.get("sl") is not None:
        result["sl"] = float(result["sl"])
    return result


def _single_entry_range(text: str) -> list[float] | None:
    match = SINGLE_ENTRY_RE.search(text)
    if not match:
        return None
    try:
        value = float(match.group(1))
    except (TypeError, ValueError):
        return None
    return [value, value]


def _empty_signal(channel: str, message_id: int) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "provider_signal_id": f"{channel}_{message_id}",
        "channel": channel,
        "root_message_id": message_id,
        "source_message_ids": [],
        "signal_ts_utc": None,
        "first_observed_utc": None,
        "direction": None,
        "risk_label": "standard",
        "effective_range": None,
        "effective_tps": [],
        "effective_sl": None,
        "revisions": [],
        "entry_zone_timeline": [],
        "level_timeline": [],
        "management_events": [],
        "execution_sig_ids": [],
        "execution_count": 0,
        "duplicate_execution": False,
        "semantic_status": "incomplete",
        "semantic_gaps": [],
        "_root_message_seen": False,
        "_revision_keys": {},
        "_management_keys": set(),
    }


def _revision_key(row: dict) -> tuple:
    return (
        int(row.get("message_id") or 0),
        str(row.get("text") or "").strip(),
        row.get("sticker_id"),
        bool(row.get("has_photo")),
        bool(row.get("has_document")),
        _telegram_ts(row),
    )


def _append_revision(signal: dict, row: dict) -> None:
    key = _revision_key(row)
    update_kind = str(row.get("update_kind") or "unknown")
    existing = signal["_revision_keys"].get(key)
    if existing is not None:
        if update_kind not in existing["update_kinds"]:
            existing["update_kinds"].append(update_kind)
        return

    text = str(row.get("text") or "")
    parsed = _normalise_parsed(parse_canal2(text)) if text else {}
    if text and parsed.get("direction") and not parsed.get("range"):
        single_range = _single_entry_range(text)
        if single_range is not None:
            parsed["range"] = single_range
    revision = {
        "message_id": int(row.get("message_id")),
        "observed_ts_utc": row.get("ts"),
        "telegram_ts_utc": _telegram_ts(row),
        "update_kinds": [update_kind],
        "text": text,
        "sticker_id": row.get("sticker_id"),
        "has_photo": bool(row.get("has_photo")),
        "has_document": bool(row.get("has_document")),
        "parsed": parsed,
    }
    signal["revisions"].append(revision)
    signal["_revision_keys"][key] = revision
    message_id = revision["message_id"]
    if message_id not in signal["source_message_ids"]:
        signal["source_message_ids"].append(message_id)
    if signal["signal_ts_utc"] is None:
        signal["signal_ts_utc"] = revision["telegram_ts_utc"]
    if signal["first_observed_utc"] is None:
        signal["first_observed_utc"] = revision["observed_ts_utc"]

    upper = text.upper()
    if "HIGH RISK" in upper:
        signal["risk_label"] = "high_risk"
    if parsed.get("direction"):
        signal["direction"] = parsed["direction"]
    if parsed.get("range"):
        signal["effective_range"] = parsed["range"]
        signal["entry_zone_timeline"].append({
            "telegram_ts_utc": revision["telegram_ts_utc"],
            "observed_ts_utc": revision["observed_ts_utc"],
            "range": parsed["range"],
            "source_message_id": message_id,
        })
    if parsed.get("tps"):
        signal["effective_tps"] = parsed["tps"]
    if parsed.get("sl") is not None:
        signal["effective_sl"] = parsed["sl"]
    if parsed.get("tps") or parsed.get("sl") is not None:
        signal["level_timeline"].append({
            "telegram_ts_utc": revision["telegram_ts_utc"],
            "observed_ts_utc": revision["observed_ts_utc"],
            "tps": parsed.get("tps") or [],
            "sl": parsed.get("sl"),
            "source_message_id": message_id,
        })


def _append_management(signal: dict, row: dict) -> None:
    key = (
        row.get("message_id"),
        str(row.get("text") or row.get("raw_text") or "").strip(),
        _telegram_ts(row),
    )
    if key in signal["_management_keys"]:
        return
    signal["_management_keys"].add(key)
    telegram_ts = _telegram_ts(row)
    observed_ts = row.get("ts")
    if signal["signal_ts_utc"] is None:
        signal["signal_ts_utc"] = telegram_ts
    if signal["first_observed_utc"] is None:
        signal["first_observed_utc"] = observed_ts
    text = str(row.get("text") or row.get("raw_text") or "")
    signal["management_events"].append({
        "message_id": row.get("message_id"),
        "reply_to_msg_id": row.get("reply_to_msg_id"),
        "observed_ts_utc": observed_ts,
        "telegram_ts_utc": telegram_ts,
        "text": text,
        "classified_action": (
            row.get("action")
            or row.get("classified")
            or _deterministic_management_action(text)
        ),
        "source": "telegram_raw" if row.get("ev") == "telegram_raw" else row.get("ev"),
    })


def _finalize(signal: dict) -> dict:
    signal["revisions"].sort(
        key=lambda row: (row.get("telegram_ts_utc") or "", row["message_id"]))
    signal["management_events"].sort(
        key=lambda row: (row.get("telegram_ts_utc") or "", row.get("message_id") or 0))
    signal["source_message_ids"].sort()
    signal["execution_sig_ids"].sort()
    signal["execution_count"] = len(signal["execution_sig_ids"])
    signal["duplicate_execution"] = signal["execution_count"] > 1

    gaps: list[str] = []
    if not signal.pop("_root_message_seen"):
        gaps.append("missing_root_message")
    if not signal.get("direction"):
        gaps.append("missing_direction")
    if not signal.get("effective_range"):
        gaps.append("missing_entry_range")
    if not signal.get("effective_tps"):
        gaps.append("missing_tps")
    if signal.get("effective_sl") is None:
        gaps.append("missing_sl")
    signal["semantic_gaps"] = gaps
    signal["semantic_status"] = "complete" if not gaps else "incomplete"
    signal.pop("_revision_keys", None)
    signal.pop("_management_keys", None)
    return signal


def _summary(signals: list[dict]) -> dict:
    channels: dict[str, dict] = {}
    for channel in sorted({row["channel"] for row in signals}):
        selected = [row for row in signals if row["channel"] == channel]
        channels[channel] = {
            "provider_signals": len(selected),
            "complete_signals": sum(
                row["semantic_status"] == "complete" for row in selected),
            "incomplete_signals": sum(
                row["semantic_status"] != "complete" for row in selected),
            "executed_signals": sum(row["execution_count"] > 0 for row in selected),
            "unexecuted_signals": sum(row["execution_count"] == 0 for row in selected),
            "duplicate_execution_signals": sum(
                row["duplicate_execution"] for row in selected),
        }
    return {
        "provider_signals": len(signals),
        "complete_signals": sum(
            row["semantic_status"] == "complete" for row in signals),
        "incomplete_signals": sum(
            row["semantic_status"] != "complete" for row in signals),
        "executed_signals": sum(row["execution_count"] > 0 for row in signals),
        "unexecuted_signals": sum(row["execution_count"] == 0 for row in signals),
        "duplicate_execution_signals": sum(
            row["duplicate_execution"] for row in signals),
        "management_events": sum(len(row["management_events"]) for row in signals),
        "channels": channels,
    }


def build_catalog_report(events: Iterable[dict], replay_trades: Iterable[dict]) -> dict:
    events = sorted(list(events), key=lambda row: str(row.get("ts") or ""))
    signals: dict[tuple[str, int], dict] = {}

    def ensure(channel: str, message_id: int) -> dict:
        key = (channel, int(message_id))
        if key not in signals:
            signals[key] = _empty_signal(*key)
        return signals[key]

    canal1_text_roots: dict[int, int] = {}
    for row in events:
        if row.get("ev") != "canal1_text_processing":
            continue
        parsed_sig = _message_id_from_sig(row.get("sig"))
        source_msg_id = row.get("source_msg_id")
        if parsed_sig and parsed_sig[0] == "canal1" and source_msg_id is not None:
            canal1_text_roots[int(source_msg_id)] = parsed_sig[1]

    direction_by_key: dict[tuple[str, int], str] = {}
    for row in events:
        if row.get("ev") != "telegram_understood" or not row.get("direction"):
            continue
        channel = row.get("channel")
        message_id = row.get("message_id")
        if channel and message_id is not None:
            direction_by_key[(str(channel), int(message_id))] = str(row["direction"])

    raw_events = [row for row in events if row.get("ev") == "telegram_raw"]
    sticker_roots: dict[int, tuple[datetime, str | None]] = {}
    text_candidates: dict[int, tuple[datetime, str | None]] = {}
    for row in raw_events:
        if row.get("channel") != "canal1" or row.get("is_reply"):
            continue
        message_id = row.get("message_id")
        event_dt = _parse_dt(_telegram_ts(row))
        if message_id is None or event_dt is None:
            continue
        message_id = int(message_id)
        if row.get("sticker_id") is not None:
            sticker_roots.setdefault(
                message_id,
                (event_dt, direction_by_key.get(("canal1", message_id))),
            )
        text = str(row.get("text") or "")
        if is_canal1_signal_text(text):
            text_candidates.setdefault(
                message_id,
                (event_dt, parse_canal2(text).get("direction")),
            )

    paired_stickers = {
        root_id
        for text_id, root_id in canal1_text_roots.items()
        if text_id != root_id
    }
    for text_id, (text_dt, direction) in sorted(
        text_candidates.items(), key=lambda item: item[1][0]
    ):
        explicit_root = canal1_text_roots.get(text_id)
        if explicit_root is not None and explicit_root != text_id:
            continue
        candidates = []
        for sticker_id, (sticker_dt, sticker_direction) in sticker_roots.items():
            age = text_dt - sticker_dt
            if sticker_id in paired_stickers or not timedelta(0) <= age <= timedelta(minutes=3):
                continue
            if direction and sticker_direction and direction != sticker_direction:
                continue
            candidates.append((sticker_dt, sticker_id))
        if candidates:
            _sticker_dt, sticker_id = max(candidates)
            canal1_text_roots[text_id] = sticker_id
            paired_stickers.add(sticker_id)

    root_keys: set[tuple[str, int]] = set()
    for row in raw_events:
        channel = str(row.get("channel") or "")
        message_id = row.get("message_id")
        if channel not in ("canal1", "canal2") or message_id is None:
            continue
        message_id = int(message_id)
        if row.get("is_reply") or row.get("reply_to_msg_id") is not None:
            continue
        text = str(row.get("text") or "")
        if channel == "canal2" and is_canal2_entry(text):
            root_keys.add((channel, message_id))
        elif channel == "canal1" and (
            row.get("sticker_id") is not None
            or (
                message_id not in canal1_text_roots
                and is_canal1_signal_text(text)
            )
        ):
            root_keys.add((channel, message_id))

    for channel, message_id in root_keys:
        ensure(channel, message_id)

    for row in raw_events:
        channel = str(row.get("channel") or "")
        message_id = row.get("message_id")
        if channel not in ("canal1", "canal2") or message_id is None:
            continue
        message_id = int(message_id)
        reply_to = row.get("reply_to_msg_id")
        if row.get("is_reply") or reply_to is not None:
            if reply_to is None:
                continue
            root_id = canal1_text_roots.get(int(reply_to), int(reply_to))
            key = (channel, root_id)
            if key in signals or _looks_like_management(str(row.get("text") or "")):
                _append_management(ensure(channel, root_id), row)
            continue

        root_id = canal1_text_roots.get(message_id, message_id)
        key = (channel, root_id)
        if key not in signals:
            continue
        signal = ensure(channel, root_id)
        if message_id == root_id:
            signal["_root_message_seen"] = True
        _append_revision(signal, row)

    for (channel, message_id), direction in direction_by_key.items():
        root_id = canal1_text_roots.get(message_id, message_id)
        if (channel, root_id) in signals and not signals[(channel, root_id)]["direction"]:
            signals[(channel, root_id)]["direction"] = direction

    for trade in replay_trades:
        parsed_sig = _message_id_from_sig(trade.get("sig_id"))
        if not parsed_sig:
            continue
        channel, message_id = parsed_sig
        if channel not in ("canal1", "canal2"):
            continue
        root_id = canal1_text_roots.get(message_id, message_id)
        signal = ensure(channel, root_id)
        sig_id = str(trade.get("sig_id"))
        if sig_id not in signal["execution_sig_ids"]:
            signal["execution_sig_ids"].append(sig_id)

    finalized = [_finalize(signal) for signal in signals.values()]
    finalized.sort(key=lambda row: (
        row.get("signal_ts_utc") or row.get("first_observed_utc") or "",
        row["provider_signal_id"],
    ))
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": _summary(finalized),
        "signals": finalized,
    }


def write_report(report: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build canonical provider signals from Telegram events")
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--replay", type=Path, default=DEFAULT_REPLAY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    report = build_catalog_report(
        load_jsonl(args.events),
        load_jsonl(args.replay),
    )
    write_report(report, args.output)
    if not args.quiet:
        summary = report["summary"]
        print(f"Provider signals: {summary['provider_signals']}")
        print(f"Complete: {summary['complete_signals']}")
        print(f"Incomplete: {summary['incomplete_signals']}")
        print(f"Executed: {summary['executed_signals']}")
        print(f"Unexecuted: {summary['unexecuted_signals']}")
        print(f"Output: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
