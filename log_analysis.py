"""Fast, read-only summaries for append-only trade event logs."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any


CURSOR_VERSION = 1
_HASH_CHUNK_SIZE = 1024 * 1024
_PARTIAL_PROFIT_RE = re.compile(
    r"\b(?:TAKE|CLOSE|CLOSING|BOOK|SECURE)\s+(?:SOME\s+)?"
    r"PARTIAL(?:S|\s+PROFITS?)?\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LogScan:
    mode: str
    events: list[dict]
    cursor: dict
    start_offset: int
    end_offset: int
    reset_reason: str | None
    incomplete_tail: bool
    parse_errors: list[dict]


def _hash_prefix(path: Path, length: int):
    digest = hashlib.sha256()
    remaining = max(0, int(length))
    with path.open("rb") as handle:
        while remaining:
            chunk = handle.read(min(_HASH_CHUNK_SIZE, remaining))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
    return digest, remaining == 0


def scan_jsonl(path: str | Path, cursor: dict | None = None,
               force_full: bool = False) -> LogScan:
    """Read only unseen complete JSONL records and return a durable cursor."""
    path = Path(path)
    file_size = path.stat().st_size
    start_offset = 0
    reset_reason = None
    mode = "full"
    digest = hashlib.sha256()

    if cursor and not force_full:
        requested = int(cursor.get("offset") or 0)
        expected_hash = str(cursor.get("prefix_sha256") or "")
        if requested > file_size:
            mode = "full_rebuild"
            reset_reason = "file_truncated"
        elif cursor.get("version") != CURSOR_VERSION:
            mode = "full_rebuild"
            reset_reason = "cursor_version_changed"
        else:
            prefix_digest, complete = _hash_prefix(path, requested)
            if complete and prefix_digest.hexdigest() == expected_hash:
                mode = "incremental"
                start_offset = requested
                digest = prefix_digest
            else:
                mode = "full_rebuild"
                reset_reason = "prefix_changed"
    elif force_full and cursor:
        mode = "full_rebuild"
        reset_reason = "forced"

    events: list[dict] = []
    parse_errors: list[dict] = []
    incomplete_tail = False
    end_offset = start_offset

    with path.open("rb") as handle:
        handle.seek(start_offset)
        while True:
            line_offset = handle.tell()
            raw = handle.readline()
            if not raw:
                break
            if not raw.endswith(b"\n"):
                incomplete_tail = True
                break
            try:
                decoded = json.loads(raw.decode("utf-8"))
                if not isinstance(decoded, dict):
                    raise ValueError("JSONL record is not an object")
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                parse_errors.append({
                    "offset": line_offset,
                    "error": f"{type(exc).__name__}: {exc}",
                })
            else:
                events.append(decoded)
            digest.update(raw)
            end_offset = handle.tell()

    next_cursor = {
        "version": CURSOR_VERSION,
        "offset": end_offset,
        "prefix_sha256": digest.hexdigest(),
    }
    return LogScan(
        mode=mode,
        events=events,
        cursor=next_cursor,
        start_offset=start_offset,
        end_offset=end_offset,
        reset_reason=reset_reason,
        incomplete_tail=incomplete_tail,
        parse_errors=parse_errors,
    )


def _channel(event: dict) -> str:
    channel = str(event.get("channel") or "").lower()
    if channel in {"canal1", "canal2"}:
        return channel
    signal_id = str(event.get("sig") or "").lower()
    if signal_id.startswith("canal1_"):
        return "canal1"
    if signal_id.startswith("canal2_"):
        return "canal2"
    return "other"


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    value = ordered[lower] * (1 - weight) + ordered[upper] * weight
    return round(value, 2)


def _actions(event: dict) -> list[str]:
    actions = []
    direct = event.get("action")
    if direct:
        actions.append(str(direct).upper())
    for item in event.get("classifications") or []:
        if isinstance(item, dict):
            action = item.get("action") or item.get("type")
            if action:
                actions.append(str(action).upper())
    return actions


def _partial_evidence_count(events: list[dict]) -> int:
    """Count unique provider messages that communicate a partial close."""
    raw_messages = set()
    raw_fingerprints = set()
    for event in events:
        if event.get("ev") != "telegram_raw":
            continue
        text = str(event.get("text") or "")
        if not _PARTIAL_PROFIT_RE.search(text):
            continue
        fingerprint = event.get("text_sha1")
        if not fingerprint and text:
            fingerprint = hashlib.sha1(text.encode("utf-8")).hexdigest()
        if fingerprint:
            raw_fingerprints.add(str(fingerprint))
        raw_messages.add((
            _channel(event),
            str(event.get("message_id") or event.get("sig") or "unknown"),
        ))

    classified_only = set()
    for event in events:
        if "CLOSE_PARTIAL" not in _actions(event):
            continue
        fingerprint = event.get("raw_text_sha1") or event.get("text_sha1")
        if fingerprint and str(fingerprint) in raw_fingerprints:
            continue
        classified_only.add(
            ("text", str(fingerprint)) if fingerprint else (
                "event",
                _channel(event),
                str(event.get("message_id") or event.get("sig") or "unknown"),
                str(event.get("ts") or ""),
            )
        )
    return len(raw_messages) + len(classified_only)


def summarize_events(events: list[dict]) -> dict:
    """Build a compact operational summary without retaining raw messages."""
    event_counts = Counter(str(event.get("ev") or "unknown") for event in events)
    timestamps = [str(event["ts"]) for event in events if event.get("ts")]
    received = [event for event in events if event.get("ev") == "signal_received"]
    closed = [event for event in events if event.get("ev") == "signal_closed"]

    received_by_channel = Counter(_channel(event) for event in received)
    closed_by_channel = Counter(_channel(event) for event in closed)
    pnl_by_channel: defaultdict[str, float] = defaultdict(float)
    pnl_total = 0.0
    pnl_rows = 0
    for event in closed:
        raw_pnl = event.get("total_pnl_usd")
        if raw_pnl is None:
            raw_pnl = event.get("total_pnl")
        if raw_pnl is None:
            raw_pnl = event.get("total_pl")
        try:
            pnl = float(raw_pnl)
        except (TypeError, ValueError):
            continue
        pnl_total += pnl
        pnl_by_channel[_channel(event)] += pnl
        pnl_rows += 1

    anomaly_counter: Counter[tuple] = Counter()
    anomaly_rows = []
    for event in events:
        if event.get("ev") != "anomaly":
            continue
        key = (
            str(event.get("sig") or "bot"),
            str(event.get("category") or "unknown"),
            str(event.get("severity") or "unknown"),
            str(event.get("detail") or ""),
            str(event.get("code") or ""),
            str(event.get("ticket") or ""),
        )
        anomaly_counter[key] += 1
        anomaly_rows.append(event)

    severity_rank = {"critical": 0, "warning": 1, "info": 2}
    top_anomalies = []
    for key, count in sorted(
        anomaly_counter.items(),
        key=lambda item: (
            severity_rank.get(item[0][2], 9), -item[1], item[0][0]
        ),
    )[:12]:
        signal_id, category, severity, detail, code, ticket = key
        row = {
            "signal": signal_id,
            "category": category,
            "severity": severity,
            "detail": detail,
            "count": count,
        }
        if code:
            row["code"] = code
        if ticket:
            row["ticket"] = ticket
        top_anomalies.append(row)

    latency_by_message: defaultdict[str, dict[str, float]] = defaultdict(dict)
    for event in events:
        if event.get("ev") != "handler_entry":
            continue
        kind = str(event.get("kind") or "").lower()
        if kind and kind not in {"new", "poll_new"}:
            continue
        raw_delay = event.get("telegram_to_handler_ms")
        if raw_delay is None:
            raw_delay = event.get("delay_ms")
        try:
            delay_ms = float(raw_delay)
        except (TypeError, ValueError):
            continue
        channel = _channel(event)
        message_key = str(event.get("message_id") or event.get("sig") or event.get("ts"))
        previous = latency_by_message[channel].get(message_key)
        if previous is None or delay_ms < previous:
            latency_by_message[channel][message_key] = delay_ms
    latency_report = {
        channel: {
            "count": len(values),
            "p50": _percentile(values, 0.50),
            "p95": _percentile(values, 0.95),
            "max": round(max(values), 2),
        }
        for channel, rows in sorted(latency_by_message.items())
        if (values := list(rows.values()))
    }

    close_partial_evidence = _partial_evidence_count(events)
    gemini_failures = sum(
        1 for event in events
        if event.get("gemini_failed")
        or any(bool(item.get("_gemini_failed"))
               for item in event.get("classifications") or []
               if isinstance(item, dict))
    )
    unresolved_rows = [
        event for event in events
        if event.get("ev") == "management_reply_unresolved"
    ]
    unresolved_unique = {
        (
            str(event.get("sig") or ""),
            str(event.get("reply_to_msg_id") or ""),
            str(event.get("reason") or ""),
            str(event.get("text_preview") or ""),
        )
        for event in unresolved_rows
    }

    return {
        "window": {
            "events": len(events),
            "first_ts": min(timestamps) if timestamps else None,
            "last_ts": max(timestamps) if timestamps else None,
            "sessions_started": event_counts["session_started"],
            "sessions_closed": event_counts["session_closed"],
        },
        "operations": {
            "signals_received": len(received),
            "signals_closed": len(closed),
            "received_by_channel": dict(sorted(received_by_channel.items())),
            "closed_by_channel": dict(sorted(closed_by_channel.items())),
            "recorded_pnl": round(pnl_total, 2) if pnl_rows else None,
            "recorded_pnl_by_channel": {
                key: round(value, 2)
                for key, value in sorted(pnl_by_channel.items())
            },
            "signal_ids": sorted({
                str(event.get("sig")) for event in received
                if event.get("sig")
            }),
        },
        "execution": {
            "orders_requested": event_counts["mt5_order_requested"],
            "orders_result": event_counts["mt5_order_result"],
            "mt5_action_failed": event_counts["mt5_action_failed"],
            "mt5_structural_incident": event_counts["mt5_structural_incident"],
            "disconnects": sum(
                1 for event in events
                if event.get("ev") in {
                    "mt5_connection_change", "telegram_connection_change"
                } and event.get("connected") is False
            ),
        },
        "interpretation": {
            "telegram_understood": event_counts["telegram_understood"],
            "human_reviews": event_counts["ambiguous_decision_notified"],
            "unresolved_management": len(unresolved_unique),
            "unresolved_management_events": len(unresolved_rows),
            "gemini_failures": gemini_failures,
            "close_partial_evidence": close_partial_evidence,
        },
        "anomalies": {
            "total": len(anomaly_rows),
            "critical": sum(
                1 for event in anomaly_rows if event.get("severity") == "critical"
            ),
            "warning": sum(
                1 for event in anomaly_rows if event.get("severity") == "warning"
            ),
            "info": sum(
                1 for event in anomaly_rows if event.get("severity") == "info"
            ),
            "unique": len(anomaly_counter),
            "top": top_anomalies,
        },
        "latency_ms": latency_report,
        "event_counts": dict(sorted(event_counts.items())),
    }


def load_status_snapshots(data_dir: str | Path) -> dict:
    data_dir = Path(data_dir)
    specs = {
        "observed_replay": "observed_tick_replay_status.json",
        "replay_readiness": "replay_readiness_report.json",
        "reconcile": "reconcile_status.json",
        "accounting": "accounting_replay_audit_status.json",
    }
    snapshots = {}
    for key, filename in specs.items():
        path = data_dir / filename
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        snapshots[key] = {
            field: payload[field]
            for field in ("generated_at", "scope", "summary", "ok", "duration_s")
            if field in payload
        }
    return snapshots


def render_compact_report(report: dict) -> str:
    scan = report.get("scan") or {}
    window = report["window"]
    operations = report["operations"]
    execution = report["execution"]
    interpretation = report["interpretation"]
    anomalies = report["anomalies"]
    lines = [
        (f"Logs: {scan.get('mode', 'n/a')} | {window['events']} eventos | "
         f"{window['first_ts'] or 'sin inicio'} -> {window['last_ts'] or 'sin fin'}"),
        (f"Operaciones: {operations['signals_received']} senales, "
         f"{operations['signals_closed']} cierres, "
         f"P&L registrado={operations['recorded_pnl']}"),
        (f"MT5: {execution['orders_requested']} ordenes, "
         f"{execution['mt5_action_failed']} fallos, "
         f"{execution['mt5_structural_incident']} incidentes estructurales"),
        (f"Interpretacion: {interpretation['telegram_understood']} entendidos, "
         f"{interpretation['human_reviews']} revisiones, "
         f"{interpretation['gemini_failures']} fallos Gemini, "
         f"{interpretation['close_partial_evidence']} parciales detectados"),
        (f"Anomalias: {anomalies['critical']} criticas, "
         f"{anomalies['warning']} avisos, {anomalies['unique']} unicas"),
    ]
    latency_parts = []
    for channel, label in (("canal1", "Canal 1"), ("canal2", "Canal 2")):
        values = (report.get("latency_ms") or {}).get(channel)
        if not values:
            continue
        latency_parts.append(
            f"{label} p95={values['p95']:.0f}ms "
            f"(max={values['max']:.0f}ms, n={values['count']})"
        )
    if latency_parts:
        lines.append("Latencia: " + " | ".join(latency_parts))
    readiness = (report.get("status_snapshots") or {}).get("replay_readiness", {})
    if readiness.get("summary"):
        summary = readiness["summary"]
        lines.append(
            f"Replay: {summary.get('ready', 0)}/{summary.get('total', 0)} ready, "
            f"{summary.get('blocked', 0)} bloqueadas"
        )
    if anomalies["top"]:
        lines.append("Problemas principales:")
        for item in anomalies["top"][:8]:
            lines.append(
                f"- [{item['severity']}] {item['signal']} {item['category']}: "
                f"{item['detail']} (x{item['count']})"
            )
    if scan.get("parse_errors"):
        lines.append(f"Aviso: {scan['parse_errors']} lineas JSON invalidas")
    if scan.get("incomplete_tail"):
        lines.append("Aviso: ultima linea aun en escritura; se leera en la proxima pasada")
    return "\n".join(lines)
