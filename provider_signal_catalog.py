"""Build canonical provider signals from immutable Telegram perception events."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from parser import (
    canal2_entry_command_key,
    is_canal1_signal_text,
    is_canal2_entry,
    parse_canal2,
    parse_canal2_zone_plan,
)
from interpretation_firewall import extract_provider_stated_be_price
from interpretation_firewall import normalize_xauusd_management_price
import runtime_paths


DATA_DIR = runtime_paths.active_data_dir()
DEFAULT_EVENTS = DATA_DIR / "trade_events.jsonl"
DEFAULT_REPLAY = DATA_DIR / "replay_trades.jsonl"
DEFAULT_OUTPUT = DATA_DIR / "provider_signal_catalog.json"
SCHEMA_VERSION = 7
EXECUTION_BOUNDARY_EVENT = "signal_received"
EXECUTION_PRIMARY_FILL_EVENT = "market_filled"
EXECUTION_FILL_EVENTS = {
    EXECUTION_PRIMARY_FILL_EVENT,
    "market_b_filled",
    "scale_out_leg_filled",
}
RECORD_TYPES = {
    "formal_signal",
    "zone_plan",
    "context_setup",
    "daily_summary",
    "management_only",
    "unknown_candidate",
}
RECORD_TYPE_PRIORITY = {
    "unknown_candidate": 0,
    "management_only": 1,
    "context_setup": 2,
    "zone_plan": 2,
    "daily_summary": 3,
    "formal_signal": 4,
}
SINGLE_ENTRY_RE = re.compile(
    r"\b(?:BUY|SELL)\s+(?:GOLD|XAUUSD)?\s*(?:NOW|LIMIT)?\s*"
    r"(?:@|AT)?\s*(\d{3,5}(?:\.\d+)?)\b",
    re.IGNORECASE,
)
MOVE_SL_PRICE_RE = re.compile(
    r"\b(?:MOVE|MOVING|CHANGE|CHANGING|ADJUST|ADJUSTING|SET|SETTING|PUT)"
    r"\s+(?:MY\s+|YOUR\s+|THE\s+)?(?:SL|STOP\s*LOSS)"
    r"(?:\s+\w+){0,3}?\s+(?:TO|AT)\s+(\d{1,5}(?:\.\d+)?)\b",
    re.IGNORECASE,
)
CLOSE_TP_RE = re.compile(r"\bCLOSE\s+TP\s*(\d+)\b", re.IGNORECASE)
DAILY_SUMMARY_RE = re.compile(
    r"\b(?:WEEKLY|DAILY|MONDAY|TUESDAY|WEDNESDAY|THURSDAY|FRIDAY)\s+SUMMARY\b|"
    r"\b(?:SIGNALS?|TRADES?)\s+SENT\b.*\b(?:WINS?|WINNING|LOSSES?|STOP\s*LOSS)\b",
    re.IGNORECASE | re.DOTALL,
)
CONTEXT_SETUP_RE = re.compile(
    r"\b(?:SUPPORT|RESISTANCE|4\s*H(?:R|OUR)|ANALYSIS|LEVELS?|ZONES?)\b",
    re.IGNORECASE,
)


def _parse_zone_plan(text: str) -> dict | None:
    return parse_canal2_zone_plan(text)


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


def _execution_ticket_id(row: dict) -> int | None:
    value = row.get("ticket")
    if value is None:
        value = row.get("position_ticket")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _execution_entry_provenance(row: dict | None) -> dict:
    if row is None:
        return {}
    field_map = {
        "entry_source_kind": "source_kind",
        "zone_plan_message_id": "zone_plan_message_id",
        "zone_thread_root_message_id": "zone_thread_root_message_id",
        "zone_entry_generation": "zone_entry_generation",
        "zone_trigger_kind": "zone_trigger_kind",
        "zone_trigger_side": "zone_trigger_side",
        "zone_trigger_price": "zone_trigger_price",
        "zone_trigger_range": "zone_trigger_range",
        "zone_trigger_time": "zone_trigger_time",
        "zone_trigger_time_msc": "zone_trigger_time_msc",
        "zone_trigger_observed_utc": "zone_trigger_observed_utc",
        "zone_trigger_normalized_utc": "zone_trigger_normalized_utc",
        "zone_trigger_broker_utc_offset_s": (
            "zone_trigger_broker_utc_offset_s"
        ),
        "zone_trigger_clock_basis": "zone_trigger_clock_basis",
    }
    provenance: dict = {}
    for source, target in field_map.items():
        value = row.get(source)
        if value is None:
            continue
        provenance[target] = list(value) if isinstance(value, list) else value
    return provenance


def _execution_batches(
    events: list[dict],
    replay_trades: list[dict],
) -> dict[str, list[dict]]:
    """Build immutable observed execution blocks per runtime signal ID."""
    relevant_by_sig: dict[str, list[dict]] = {}
    relevant_events = EXECUTION_FILL_EVENTS | {EXECUTION_BOUNDARY_EVENT}
    for row in events:
        sig_id = str(row.get("sig") or "")
        if not _message_id_from_sig(sig_id):
            continue
        if row.get("ev") not in relevant_events:
            continue
        relevant_by_sig.setdefault(sig_id, []).append(row)

    batches_by_sig: dict[str, list[dict]] = {}
    for sig_id, rows in relevant_by_sig.items():
        batches: list[dict] = []
        current: dict | None = None

        def start(boundary: dict | None = None) -> dict:
            return {
                "sig_id": sig_id,
                "source": "journal",
                "signal_received_utc": (
                    boundary.get("ts") if boundary is not None else None
                ),
                "entry_provenance": _execution_entry_provenance(boundary),
                "first_fill_utc": None,
                "last_fill_utc": None,
                "ticket_ids": [],
                "fills": [],
                "_has_primary": False,
            }

        def finish(batch: dict | None) -> None:
            if batch is None or not batch["fills"]:
                return
            completed = dict(batch)
            completed.pop("_has_primary", None)
            batches.append(completed)

        for row in rows:
            event = str(row.get("ev") or "")
            if event == EXECUTION_BOUNDARY_EVENT:
                finish(current)
                current = start(row)
                continue

            if current is None:
                current = start()
            elif (event == EXECUTION_PRIMARY_FILL_EVENT
                  and current["_has_primary"]):
                finish(current)
                current = start()

            ticket = _execution_ticket_id(row)
            if ticket is None:
                continue
            observed_utc = row.get("ts")
            fill = {
                "ticket": ticket,
                "event": event,
                "price": row.get("price"),
                "observed_utc": observed_utc,
            }
            if row.get("leg") is not None:
                fill["leg"] = row.get("leg")
            if ticket not in current["ticket_ids"]:
                current["ticket_ids"].append(ticket)
                current["fills"].append(fill)
            if current["first_fill_utc"] is None:
                current["first_fill_utc"] = observed_utc
            current["last_fill_utc"] = observed_utc
            if event == EXECUTION_PRIMARY_FILL_EVENT:
                current["_has_primary"] = True

        finish(current)
        if batches:
            batches_by_sig[sig_id] = batches

    # Older evidence can lack signal_received/fill journal events. A replay
    # trade still proves one observed execution block.
    for trade in replay_trades:
        sig_id = str(trade.get("sig_id") or "")
        if not _message_id_from_sig(sig_id):
            continue
        if batches_by_sig.get(sig_id):
            continue
        ticket_ids = []
        fills = []
        for ticket_row in trade.get("tickets") or []:
            ticket = _execution_ticket_id(ticket_row)
            if ticket is None or ticket in ticket_ids:
                continue
            ticket_ids.append(ticket)
            fills.append({
                "ticket": ticket,
                "event": "replay_ticket",
                "price": ticket_row.get("open_price"),
                "observed_utc": ticket_row.get("open_time_utc"),
            })
        batches_by_sig.setdefault(sig_id, []).append({
            "sig_id": sig_id,
            "source": "replay_trade_inferred",
            "signal_received_utc": None,
            "first_fill_utc": (
                fills[0]["observed_utc"] if fills else None
            ),
            "last_fill_utc": (
                fills[-1]["observed_utc"] if fills else None
            ),
            "ticket_ids": ticket_ids,
            "fills": fills,
            "entry_provenance": dict(trade.get("entry_provenance") or {}),
        })

    for sig_id, batches in batches_by_sig.items():
        for index, batch in enumerate(batches, start=1):
            batch["execution_batch_id"] = f"{sig_id}#exec{index}"
    return batches_by_sig


def _is_edit_update_kind(value: str | None) -> bool:
    update_kind = str(value or "").strip().lower()
    return update_kind == "edit" or update_kind.endswith("_edit")


def _telegram_ts(row: dict) -> str | None:
    if (
        row.get("is_edit") or _is_edit_update_kind(row.get("update_kind"))
    ) and row.get("edit_date_utc"):
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


def _timestamp_sort_key(value: str | None) -> tuple[bool, datetime]:
    observed_dt = _parse_dt(value)
    return (
        observed_dt is None,
        observed_dt or datetime.max.replace(tzinfo=timezone.utc),
    )


def _causal_row_sort_key(
    row: dict,
    *,
    message_id_key: str = "message_id",
) -> tuple[bool, datetime, int, int]:
    return (
        *_timestamp_sort_key(row.get("observed_ts_utc")),
        int(row.get(message_id_key) or 0),
        int(row.get("_source_order") or 0),
    )


def _strip_private_fields(value: object) -> None:
    if isinstance(value, dict):
        for key in list(value):
            if str(key).startswith("_"):
                value.pop(key)
            else:
                _strip_private_fields(value[key])
    elif isinstance(value, list):
        for item in value:
            _strip_private_fields(item)


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


def _management_modality(text: str, *, actionable: bool) -> str:
    if not actionable:
        return "informational"
    upper = text.upper()
    if re.search(
        r"\b(?:WHEN\s+HAPPY|WHEN\s+COMFORTABLE|IF\s+YOU\s+WANT|"
        r"IF\s+YOU\s+WISH|FEEL\s+FREE|OPTIONAL(?:LY)?)\b",
        upper,
    ):
        return "optional"
    if re.search(r"\b(?:IF|ONCE|UNLESS|WHEN)\b", upper):
        return "conditional"
    return "direct"


def _execution_options(semantics: dict) -> list[dict]:
    modality = semantics.get("modality")
    if modality == "informational":
        return []
    primary = {"action": semantics["action"]}
    for key in (
        "price",
        "provider_stated_be_price",
        "target_tp_index",
        "levels",
    ):
        if semantics.get(key) is not None:
            primary[key] = semantics[key]
    if modality == "optional":
        return [primary, {"action": "HOLD"}]
    if modality == "conditional":
        return [primary, {"action": "WAIT_FOR_CONDITION"}]
    return [primary]


def _deterministic_management_semantics(text: str) -> dict | None:
    upper = text.upper()
    has_break_even = (
        re.search(r"\bBE\b|BREAK\s*EVEN|BREAKEVEN", upper)
        or "RISK FREE" in upper
        or "0% RISK" in upper
    )
    provider_stated_be_price = extract_provider_stated_be_price(text)
    if has_break_even and "CLOSE" in upper and re.search(r"\bOR\b", upper):
        be_option = {"action": "MOVE_SL_TO_BE"}
        if provider_stated_be_price is not None:
            be_option["provider_stated_be_price"] = provider_stated_be_price
        return {
            "action": "MANAGEMENT_CHOICE",
            "modality": "optional",
            "execution_options": [
                {"action": "CLOSE_ALL"},
                be_option,
            ],
            **({"provider_stated_be_price": provider_stated_be_price}
               if provider_stated_be_price is not None else {}),
        }
    if has_break_even:
        result = {
            "action": "MOVE_SL_TO_BE",
            "modality": _management_modality(text, actionable=True),
        }
        if provider_stated_be_price is not None:
            result["provider_stated_be_price"] = provider_stated_be_price
        result["execution_options"] = _execution_options(result)
        return result

    move_match = MOVE_SL_PRICE_RE.search(text)
    if move_match:
        result = {
            "action": "MOVE_SL_TO_PRICE",
            "price": float(move_match.group(1)),
            "modality": _management_modality(text, actionable=True),
        }
        result["execution_options"] = _execution_options(result)
        return result

    close_tp = CLOSE_TP_RE.search(text)
    if close_tp:
        result = {
            "action": "CLOSE_AT_TP",
            "target_tp_index": int(close_tp.group(1)),
            "modality": _management_modality(text, actionable=True),
        }
        result["execution_options"] = _execution_options(result)
        return result

    if re.search(r"\bCLOSE\s+(?:ALL|EVERYTHING|THE\s+TRADE)\b", upper):
        result = {
            "action": "CLOSE_ALL",
            "modality": _management_modality(text, actionable=True),
        }
        result["execution_options"] = _execution_options(result)
        return result

    if (
        re.search(
            r"\b(?:TAKE|CLOSE|SECURE)\s+(?:SOME\s+)?PARTIALS?\b",
            upper,
        )
        or re.search(
            r"\bTAKE\s+PROFITS?\s+FROM\s+(?:THE\s+)?LAYERS?\b",
            upper,
        )
    ):
        result = {
            "action": "CLOSE_PARTIAL",
            "modality": _management_modality(text, actionable=True),
        }
        result["execution_options"] = _execution_options(result)
        return result

    if re.search(r"\bSL\s+(?:WAS\s+|GOT\s+)?(?:HIT|DONE|REACHED)\b", upper):
        return {
            "action": "SL_HIT_ANNOUNCEMENT",
            "modality": "informational",
            "execution_options": [],
        }

    tp_subject = r"(?:TP\s*\d+|TARGET\s*\d+|FULL\s+TARGET)"
    tp_result = r"(?:HIT|DONE|REACHED|SMASHED|KISSED|TAPPED|HOT|\u2705)"
    if re.search(rf"\b{tp_subject}\b.*\b{tp_result}\b", upper) or re.search(
        rf"\b{tp_subject}\b\s*\u2705", upper
    ):
        return {
            "action": "TP_HIT_ANNOUNCEMENT",
            "modality": "informational",
            "execution_options": [],
        }

    if re.search(r"\bZONE\s+(?:FAILED|INVALID|BROKEN)\b", upper):
        return {
            "action": "ZONE_INVALIDATED",
            "modality": "informational",
            "execution_options": [],
        }

    if re.search(r"\bTPS?\s+(?:EDITED|CORRECTED|UPDATED)\b", upper):
        return {
            "action": "LEVEL_CORRECTION",
            "modality": "informational",
            "execution_options": [],
        }

    if re.search(
        r"(?:\+\s*\d+|\d+\s*\+)\s*PIPS?\b|"
        r"\bPIPS?\s+(?:RUNNING|PROFIT)|"
        r"\b(?:ALL\s+)?ENTR(?:Y|IES)\s+(?:ARE\s+|BACK\s+)?IN\s+PROFIT|"
        r"\bRUNNING\s+(?:OVERALL\s+)?PROFIT",
        upper,
    ):
        return {
            "action": "PROGRESS_UPDATE",
            "modality": "informational",
            "execution_options": [],
        }

    parsed = _normalise_parsed(parse_canal2(text)) if text else {}
    if parsed.get("tps") or parsed.get("sl") is not None:
        result = {
            "action": "LEVEL_UPDATE",
            "levels": {
                "tps": parsed.get("tps") or [],
                "sl": parsed.get("sl"),
            },
            "tp_updates": _indexed_tp_updates(text, parsed.get("tps") or []),
            "modality": _management_modality(text, actionable=True),
        }
        result["execution_options"] = _execution_options(result)
        return result
    return None


def _record_type_for_root(row: dict, *, formal: bool) -> tuple[str, str]:
    if formal:
        return "formal_signal", "entry_or_execution_evidence"
    if row.get("sticker_id") is not None:
        return "unknown_candidate", "unknown_sticker"
    text = str(row.get("text") or "")
    if _parse_zone_plan(text) is not None:
        return "zone_plan", "provider_multi_zone_plan"
    if DAILY_SUMMARY_RE.search(text):
        return "daily_summary", "provider_session_summary"
    if _looks_like_management(text):
        return "management_only", "standalone_management_message"
    has_media = bool(row.get("has_photo") or row.get("has_document"))
    if has_media or CONTEXT_SETUP_RE.search(text):
        return "context_setup", "media_or_market_context"
    return "unknown_candidate", "unclassified_provider_message"


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


def _indexed_tp_updates(
    text: str,
    fallback_tps: Iterable[float] = (),
) -> dict[str, float]:
    updates: dict[str, float] = {}
    pattern = (
        r"TPs*(d+)s*(?::|=|s)s*"
        r"(d{3,5}(?:.d{1,3})?)"
    )
    for match in re.finditer(pattern, text or "", re.IGNORECASE):
        updates[str(int(match.group(1)))] = float(match.group(2))
    if updates:
        return updates
    return {
        str(index): float(value)
        for index, value in enumerate(fallback_tps, start=1)
    }

def _empty_signal(
    channel: str,
    message_id: int,
    record_type: str = "unknown_candidate",
    record_type_reason: str = "unclassified_provider_message",
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "provider_signal_id": f"{channel}_{message_id}",
        "record_type": record_type,
        "record_type_reason": record_type_reason,
        "channel": channel,
        "root_message_id": message_id,
        "source_message_ids": [],
        "signal_ts_utc": None,
        "first_observed_utc": None,
        "direction": None,
        "_direction_source": None,
        "_direction_observed_utc": None,
        "risk_label": "standard",
        "effective_range": None,
        "effective_tps": [],
        "effective_sl": None,
        "entry_zones": [],
        "zone_target": None,
        "zone_plan_timeline": [],
        "revisions": [],
        "entry_zone_timeline": [],
        "level_timeline": [],
        "runtime_level_timeline": [],
        "management_events": [],
        "media": {
            "availability": "none",
            "has_photo": False,
            "has_document": False,
            "sha256": None,
            "path": None,
            "extraction_status": "not_applicable",
        },
        "execution_sig_ids": [],
        "execution_batches": [],
        "execution_range_assessments": [],
        "execution_count": 0,
        "canonical_execution_batch_ids": [],
        "canonical_execution_count": 0,
        "canonical_corrections": [],
        "duplicate_execution": False,
        "semantic_status": "incomplete",
        "semantic_gaps": [],
        "canonicalization_issues": [],
        "_root_message_seen": False,
        "_last_revision_by_message": {},
        "_management_last_by_message": {},
        "_understood_direction_candidates": [],
    }


def _promote_record_type(
    signal: dict,
    record_type: str,
    reason: str,
) -> None:
    if record_type not in RECORD_TYPES:
        raise ValueError(f"unknown record type: {record_type}")
    current = str(signal.get("record_type") or "unknown_candidate")
    if RECORD_TYPE_PRIORITY[record_type] >= RECORD_TYPE_PRIORITY[current]:
        signal["record_type"] = record_type
        signal["record_type_reason"] = reason


def _revision_state(row: dict) -> tuple:
    return (
        str(row.get("text") or "").strip(),
        row.get("sticker_id"),
        bool(row.get("has_photo")),
        bool(row.get("has_document")),
        row.get("media_sha256") or row.get("file_sha256"),
        row.get("media_path") or row.get("file_path"),
        row.get("media_extraction_status"),
    )


def _append_revision(signal: dict, row: dict) -> None:
    message_id = int(row.get("message_id"))
    state = _revision_state(row)
    update_kind = str(row.get("update_kind") or "unknown")
    previous = signal["_last_revision_by_message"].get(message_id)
    if previous is not None and previous["state"] == state:
        revision = previous["revision"]
        if update_kind not in revision["update_kinds"]:
            revision["update_kinds"].append(update_kind)
        return

    text = str(row.get("text") or "")
    parsed = _normalise_parsed(parse_canal2(text)) if text else {}
    if text and parsed.get("direction") and not parsed.get("range"):
        single_range = _single_entry_range(text)
        if single_range is not None:
            parsed["range"] = single_range
    revision = {
        "message_id": message_id,
        "observed_ts_utc": row.get("ts"),
        "telegram_ts_utc": _telegram_ts(row),
        "_source_order": int(row.get("_source_order") or 0),
        "update_kinds": [update_kind],
        "text": text,
        "sticker_id": row.get("sticker_id"),
        "has_photo": bool(row.get("has_photo")),
        "has_document": bool(row.get("has_document")),
        "media_sha256": row.get("media_sha256") or row.get("file_sha256"),
        "media_path": row.get("media_path") or row.get("file_path"),
        "media_extraction_status": row.get("media_extraction_status"),
        "parsed": parsed,
    }
    signal["revisions"].append(revision)
    signal["_last_revision_by_message"][message_id] = {
        "state": state,
        "revision": revision,
    }
    if message_id not in signal["source_message_ids"]:
        signal["source_message_ids"].append(message_id)
    if signal["signal_ts_utc"] is None:
        signal["signal_ts_utc"] = revision["telegram_ts_utc"]
    if signal["first_observed_utc"] is None:
        signal["first_observed_utc"] = revision["observed_ts_utc"]

    if revision["has_photo"] or revision["has_document"]:
        has_bytes = bool(revision["media_sha256"] and revision["media_path"])
        signal["media"] = {
            "availability": "captured" if has_bytes else "metadata_only",
            "has_photo": revision["has_photo"],
            "has_document": revision["has_document"],
            "sha256": revision["media_sha256"],
            "path": revision["media_path"],
            "extraction_status": (
                revision["media_extraction_status"]
                or ("not_extracted" if not has_bytes else "pending")
            ),
        }

    upper = text.upper()
    if "HIGH RISK" in upper:
        signal["risk_label"] = "high_risk"
    if parsed.get("direction"):
        signal["direction"] = parsed["direction"]
        signal["_direction_source"] = f"revision_parser:{message_id}"
        signal["_direction_observed_utc"] = revision["observed_ts_utc"]
    if parsed.get("range"):
        signal["effective_range"] = parsed["range"]
        signal["entry_zone_timeline"].append({
            "telegram_ts_utc": revision["telegram_ts_utc"],
            "observed_ts_utc": revision["observed_ts_utc"],
            "range": parsed["range"],
            "source_message_id": message_id,
            "_source_order": revision["_source_order"],
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
            "_source_order": revision["_source_order"],
        })


def _append_zone_plan(signal: dict, row: dict) -> bool:
    parsed = _parse_zone_plan(str(row.get("text") or ""))
    if parsed is None:
        return False
    signal["direction"] = parsed["direction"]
    signal["_direction_source"] = f"zone_plan:{int(row['message_id'])}"
    signal["_direction_observed_utc"] = row.get("ts")
    if parsed["target"] is not None:
        signal["zone_target"] = parsed["target"]
    if parsed["zones"]:
        signal["entry_zones"] = parsed["zones"]
    timeline_row = {
        "message_id": int(row["message_id"]),
        "observed_ts_utc": row.get("ts"),
        "telegram_ts_utc": _telegram_ts(row),
        "direction": parsed["direction"],
        "zones": parsed["zones"],
        "target": parsed["target"],
        "_source_order": int(row.get("_source_order") or 0),
    }
    previous = signal["zone_plan_timeline"][-1] if signal["zone_plan_timeline"] else None
    if previous is None or any(
        previous.get(key) != timeline_row.get(key)
        for key in ("message_id", "zones", "target")
    ):
        signal["zone_plan_timeline"].append(timeline_row)
    return True


def _append_management(signal: dict, row: dict) -> None:
    state_key = (
        row.get("message_id"),
        row.get("reply_to_msg_id"),
    )
    text = str(row.get("text") or row.get("raw_text") or "")
    normalized_text = text.strip()
    previous = signal["_management_last_by_message"].get(state_key)
    if previous is not None and previous["text"] == normalized_text:
        previous["event"]["raw_versions"] += 1
        update_kind = str(row.get("update_kind") or "unknown")
        if update_kind not in previous["event"]["update_kinds"]:
            previous["event"]["update_kinds"].append(update_kind)
        return
    telegram_ts = _telegram_ts(row)
    observed_ts = row.get("ts")
    if signal["signal_ts_utc"] is None:
        signal["signal_ts_utc"] = telegram_ts
    if signal["first_observed_utc"] is None:
        signal["first_observed_utc"] = observed_ts
    if not text.strip() and (row.get("has_photo") or row.get("has_document")):
        deterministic = {
            "action": "MEDIA_COMPANION",
            "modality": "informational",
            "execution_options": [],
        }
    else:
        deterministic = _deterministic_management_semantics(text)
    classifier_action = row.get("action") or row.get("classified")
    if deterministic is not None:
        semantics = deterministic
        semantic_source = "deterministic_parser"
    elif classifier_action:
        semantics = {
            "action": str(classifier_action),
            "modality": (
                "optional" if row.get("is_optional")
                else "conditional" if row.get("is_conditional")
                else "informational" if str(classifier_action).upper() == "INFORMATIONAL"
                else "direct"
            ),
        }
        semantics["execution_options"] = _execution_options(semantics)
        semantic_source = "classifier"
    else:
        semantics = {
            "action": None,
            "modality": "informational",
            "execution_options": [],
        }
        semantic_source = "unclassified"
    event = {
        "message_id": row.get("message_id"),
        "reply_to_msg_id": row.get("reply_to_msg_id"),
        "observed_ts_utc": observed_ts,
        "telegram_ts_utc": telegram_ts,
        "_source_order": int(row.get("_source_order") or 0),
        "text": text,
        "classified_action": semantics["action"],
        "classifier_action": classifier_action,
        "modality": semantics["modality"],
        "semantic_source": semantic_source,
        "execution_options": semantics["execution_options"],
        "raw_versions": 1,
        "update_kinds": [str(row.get("update_kind") or "unknown")],
        "source": "telegram_raw" if row.get("ev") == "telegram_raw" else row.get("ev"),
    }
    for field in (
        "price",
        "provider_stated_be_price",
        "target_tp_index",
        "levels",
        "tp_updates",
    ):
        if semantics.get(field) is not None:
            event[field] = semantics[field]
    signal["management_events"].append(event)
    signal["_management_last_by_message"][state_key] = {
        "text": normalized_text,
        "event": event,
    }



def _append_runtime_level(signal: dict, row: dict) -> None:
    interpreted = row.get("interpreted")
    if not isinstance(interpreted, dict):
        return
    entry_range = interpreted.get("range")
    interpreted_zones = interpreted.get("zones") or []
    if entry_range is None and len(interpreted_zones) == 1:
        entry_range = interpreted_zones[0]
    tps = interpreted.get("tps") or []
    sl = interpreted.get("sl")
    if not entry_range and not tps and sl is None:
        return
    corrections = [
        dict(item)
        for item in (row.get("corrections") or [])
        if isinstance(item, dict)
    ]
    signal["runtime_level_timeline"].append({
        "observed_ts_utc": row.get("ts"),
        "telegram_ts_utc": row.get("tg_ts"),
        "range": list(entry_range) if entry_range else None,
        "tps": list(tps),
        "sl": sl,
        "provisional": bool(row.get("provisional")),
        "corrections": corrections,
        "source_kind": "runtime_entry_interpreter",
        "source_event": "entry_levels_interpreted",
        "_source_order": int(row.get("_source_order") or 0),
    })

_ENTRY_ZONE_MAX_WIDTH = 25.0
_SL_MAX_DISTANCE_FROM_ENTRY = 50.0
_TP_MAX_DISTANCE_FROM_ENTRY = 250.0


def _prefix_price_variants(
    raw: float,
    references: Iterable[float],
) -> list[float]:
    raw = float(raw)
    suffix = raw - int(raw // 100) * 100
    variants: set[float] = set()
    for reference in references:
        reference = float(reference)
        base = int(reference // 100) * 100
        for shift in (-100, 0, 100):
            candidate = round(base + shift + suffix, 3)
            if 1_000 <= candidate <= 9_999:
                variants.add(candidate)
    return sorted(variants)


def _range_compatible(
    candidate: list[float],
    direction: str | None,
    tps: Iterable[float],
    sl: float | None,
) -> bool:
    low, high = candidate
    if high - low > _ENTRY_ZONE_MAX_WIDTH:
        return False
    tps = [float(value) for value in tps]
    if direction == "BUY":
        if tps and any(value <= high for value in tps):
            return False
        if sl is not None and float(sl) >= low:
            return False
    elif direction == "SELL":
        if tps and any(value >= low for value in tps):
            return False
        if sl is not None and float(sl) <= high:
            return False
    return True


def _canonical_range_candidate(
    raw_range: Iterable[float],
    *,
    direction: str | None,
    tps: Iterable[float],
    sl: float | None,
) -> tuple[list[float] | None, str | None]:
    raw_values = [float(value) for value in raw_range]
    if len(raw_values) != 2:
        return None, None
    raw_normalized = sorted(raw_values)
    references = [*raw_values, *[float(value) for value in tps]]
    if sl is not None:
        references.append(float(sl))

    choices = []
    for raw in raw_values:
        choices.append(sorted({
            raw,
            *_prefix_price_variants(raw, references),
        }))

    candidates: dict[tuple[float, float], int] = {}
    for left in choices[0]:
        for right in choices[1]:
            normalized = tuple(sorted((left, right)))
            if not _range_compatible(
                list(normalized), direction, tps, sl,
            ):
                continue
            edits = int(left != raw_values[0]) + int(right != raw_values[1])
            candidates[normalized] = min(
                edits,
                candidates.get(normalized, edits),
            )
    if not candidates:
        return None, None
    minimum_edits = min(candidates.values())
    best = sorted(
        candidate
        for candidate, edits in candidates.items()
        if edits == minimum_edits
    )
    if len(best) != 1:
        return None, None
    canonical = list(best[0])
    decision = (
        "accepted" if canonical == raw_normalized
        else "repaired_prefix_typo"
    )
    return canonical, decision


def _entry_boundary(
    direction: str | None,
    entry_range: list[float] | None,
    tps: Iterable[float],
) -> float | None:
    if entry_range:
        return (
            float(entry_range[0])
            if direction == "BUY"
            else float(entry_range[1])
            if direction == "SELL"
            else None
        )
    values = [float(value) for value in tps]
    if not values:
        return None
    if direction == "BUY":
        return min(values)
    if direction == "SELL":
        return max(values)
    return None


def _sl_compatible(
    value: float,
    *,
    direction: str | None,
    entry_range: list[float] | None,
    tps: Iterable[float],
) -> bool:
    boundary = _entry_boundary(direction, entry_range, tps)
    if boundary is None:
        return True
    value = float(value)
    if direction == "BUY":
        distance = boundary - value
    elif direction == "SELL":
        distance = value - boundary
    else:
        return True
    maximum = (
        _SL_MAX_DISTANCE_FROM_ENTRY if entry_range is not None else 100.0
    )
    return 0 < distance <= maximum


def _canonical_sl_candidate(
    raw: float,
    *,
    current: float | None,
    direction: str | None,
    entry_range: list[float] | None,
    tps: Iterable[float],
) -> tuple[float | None, str]:
    raw = float(raw)
    if _sl_compatible(
        raw,
        direction=direction,
        entry_range=entry_range,
        tps=tps,
    ):
        return raw, "accepted"

    references = [
        *([] if entry_range is None else entry_range),
        *[float(value) for value in tps],
    ]
    if current is not None:
        references.append(float(current))
    repaired = [
        value
        for value in _prefix_price_variants(raw, references)
        if value != raw and _sl_compatible(
            value,
            direction=direction,
            entry_range=entry_range,
            tps=tps,
        )
    ]
    repaired = sorted(set(repaired))
    if len(repaired) == 1:
        candidate = repaired[0]
        if current is not None:
            return float(current), "rejected_keep_previous"
        return candidate, "repaired_prefix_typo"
    if current is not None:
        return float(current), "rejected_keep_previous"
    return None, "rejected_invalid"


def _tp_compatible(
    value: float,
    *,
    direction: str | None,
    entry_range: list[float] | None,
) -> bool:
    if not entry_range or direction not in {"BUY", "SELL"}:
        return True
    value = float(value)
    if direction == "BUY":
        distance = value - float(entry_range[1])
    else:
        distance = float(entry_range[0]) - value
    return 0 < distance <= _TP_MAX_DISTANCE_FROM_ENTRY


def _canonical_tp_candidate(
    raw: float,
    *,
    current: float | None,
    direction: str | None,
    entry_range: list[float] | None,
    references: Iterable[float],
) -> tuple[float | None, str]:
    raw = float(raw)
    if _tp_compatible(
        raw,
        direction=direction,
        entry_range=entry_range,
    ):
        return raw, "accepted"
    repaired = [
        value
        for value in _prefix_price_variants(raw, references)
        if value != raw and _tp_compatible(
            value,
            direction=direction,
            entry_range=entry_range,
        )
    ]
    repaired = sorted(set(repaired))
    if len(repaired) == 1:
        if current is not None:
            return float(current), "rejected_keep_previous"
        return repaired[0], "repaired_prefix_typo"
    if current is not None:
        return float(current), "rejected_keep_previous"
    return None, "rejected_invalid"


def _canonical_management_sl_candidate(
    raw: float,
    *,
    current: float | None,
    entry_range: list[float] | None,
    tps: Iterable[float],
) -> tuple[float | None, str]:
    references = [
        *([] if entry_range is None else entry_range),
        *([] if current is None else [current]),
        *list(tps),
    ]
    reference = next(
        (value for value in references if 1000 <= float(value) <= 9999),
        None,
    )
    canonical = normalize_xauusd_management_price(raw, reference)
    if canonical is None:
        if current is not None:
            return float(current), "rejected_keep_previous"
        return None, "rejected_invalid"
    if canonical != float(raw):
        return canonical, "expanded_short_price"
    return canonical, "accepted"


def _canonical_issue(
    observation: dict,
    *,
    field: str,
    raw,
    canonical,
    decision: str,
) -> dict:
    return {
        "field": field,
        "raw": raw,
        "canonical": canonical,
        "decision": decision,
        "observed_ts_utc": observation.get("observed_ts_utc"),
        "source_kind": observation.get("source_kind"),
        "source_message_id": observation.get("source_message_id"),
    }


def _canonical_observations(signal: dict) -> list[dict]:
    observations: list[dict] = []
    for revision in signal.get("revisions") or []:
        parsed = revision.get("parsed") or {}
        observations.append({
            "observed_ts_utc": revision.get("observed_ts_utc"),
            "telegram_ts_utc": revision.get("telegram_ts_utc"),
            "source_kind": "revision",
            "source_message_id": revision.get("message_id"),
            "_source_order": int(revision.get("_source_order") or 0),
            "raw_range": parsed.get("range"),
            "raw_tps": parsed.get("tps") or [],
            "raw_sl": parsed.get("sl"),
            "full_tp_snapshot": bool(parsed.get("tps")),
            "tp_updates": {
                str(index): float(value)
                for index, value in enumerate(
                    parsed.get("tps") or [], start=1,
                )
            },
        })
    for event in signal.get("management_events") or []:
        action = event.get("classified_action")
        if action == "MOVE_SL_TO_PRICE":
            price = event.get("price")
            if price is not None:
                observations.append({
                    "observed_ts_utc": event.get("observed_ts_utc"),
                    "telegram_ts_utc": event.get("telegram_ts_utc"),
                    "source_kind": "management_sl_move",
                    "source_message_id": event.get("message_id"),
                    "_source_order": int(event.get("_source_order") or 0),
                    "raw_range": None,
                    "raw_tps": [],
                    "raw_sl": float(price),
                    "full_tp_snapshot": False,
                    "tp_updates": {},
                    "explicit_sl_move": True,
                })
            continue
        if event.get("classified_action") != "LEVEL_UPDATE":
            continue
        levels = event.get("levels") or {}
        observations.append({
            "observed_ts_utc": event.get("observed_ts_utc"),
            "telegram_ts_utc": event.get("telegram_ts_utc"),
            "source_kind": "management_level_update",
            "source_message_id": event.get("message_id"),
            "_source_order": int(event.get("_source_order") or 0),
            "raw_range": None,
            "raw_tps": levels.get("tps") or [],
            "raw_sl": levels.get("sl"),
            "full_tp_snapshot": False,
            "tp_updates": (
                event.get("tp_updates")
                or {
                    str(index): float(value)
                    for index, value in enumerate(
                        levels.get("tps") or [], start=1,
                    )
                }
            ),
        })
    for runtime in signal.get("runtime_level_timeline") or []:
        corrections = runtime.get("corrections") or []
        market_context_shift = next(
            (
                correction
                for correction in corrections
                if correction.get("kind") == "market_context_shift"
            ),
            None,
        )
        if market_context_shift is None:
            continue
        runtime_tps = runtime.get("tps") or []
        observations.append({
            "observed_ts_utc": runtime.get("observed_ts_utc"),
            "telegram_ts_utc": runtime.get("telegram_ts_utc"),
            "source_kind": "runtime_market_context_repair",
            "source_message_id": signal.get("root_message_id"),
            "_source_order": int(runtime.get("_source_order") or 0),
            "raw_range": runtime.get("range"),
            "raw_tps": runtime_tps,
            "raw_sl": runtime.get("sl"),
            "full_tp_snapshot": bool(runtime_tps),
            "tp_updates": {
                str(index): float(value)
                for index, value in enumerate(runtime_tps, start=1)
            },
            "canonical_decision": "market_context_prefix_repair",
            "canonical_correction": market_context_shift,
        })
    observations.sort(
        key=lambda row: _causal_row_sort_key(
            row,
            message_id_key="source_message_id",
        )
    )
    return observations


def _rebuild_canonical_timeline(signal: dict) -> None:
    direction = signal.get("direction")
    current_range: list[float] | None = None
    current_sl: float | None = None
    tp_state: dict[int, float] = {}
    pending_range: dict | None = None
    entry_timeline: list[dict] = []
    level_timeline: list[dict] = []
    issues: list[dict] = []
    missing_level = object()

    def current_tps() -> list[float]:
        return [tp_state[index] for index in sorted(tp_state)]

    def apply_range(
        raw_observation: dict,
        knowledge_observation: dict,
        *,
        validation_tps: list[float] | None = None,
        validation_sl=missing_level,
    ) -> bool:
        nonlocal current_range
        candidate, decision = _canonical_range_candidate(
            raw_observation["raw_range"],
            direction=direction,
            tps=(
                current_tps()
                if validation_tps is None
                else validation_tps
            ),
            sl=current_sl if validation_sl is missing_level else validation_sl,
        )
        if candidate is None:
            return False
        current_range = candidate
        entry_timeline.append({
            "telegram_ts_utc": raw_observation.get("telegram_ts_utc"),
            "observed_ts_utc": knowledge_observation.get("observed_ts_utc"),
            "range": candidate,
            "raw_range": list(raw_observation["raw_range"]),
            "source_message_id": raw_observation.get("source_message_id"),
            "source_kind": raw_observation.get("source_kind"),
            "canonicalized_at_source_message_id": (
                knowledge_observation.get("source_message_id")
            ),
            "_source_order": int(
                knowledge_observation.get("_source_order") or 0
            ),
        })
        if decision != "accepted":
            issues.append(_canonical_issue(
                knowledge_observation,
                field="entry_range",
                raw=list(raw_observation["raw_range"]),
                canonical=candidate,
                decision=str(decision),
            ))
        return True

    for observation in _canonical_observations(signal):
        raw_tps = [float(value) for value in observation.get("raw_tps") or []]
        raw_sl = observation.get("raw_sl")
        raw_range = observation.get("raw_range")
        if raw_range:
            normalized_raw_range = sorted(float(value) for value in raw_range)
            complete_bundle_is_coherent = (
                len(normalized_raw_range) == 2
                and bool(raw_tps)
                and raw_sl is not None
                and _range_compatible(
                    normalized_raw_range,
                    direction,
                    raw_tps,
                    float(raw_sl),
                )
            )
            if apply_range(
                observation,
                observation,
                validation_tps=(raw_tps if complete_bundle_is_coherent else None),
                validation_sl=(
                    float(raw_sl)
                    if complete_bundle_is_coherent
                    else missing_level
                ),
            ):
                pending_range = None
            else:
                pending_range = observation

        canonical_decision = observation.get("canonical_decision")
        if canonical_decision:
            issues.append(_canonical_issue(
                observation,
                field="price_bundle",
                raw=observation.get("canonical_correction"),
                canonical={
                    "range": list(raw_range) if raw_range else None,
                    "tps": raw_tps,
                    "sl": raw_sl,
                },
                decision=str(canonical_decision),
            ))
        has_level_update = bool(raw_tps or raw_sl is not None)

        updates = {
            int(index): float(value)
            for index, value in (observation.get("tp_updates") or {}).items()
        }
        if updates:
            references = [
                *([] if current_range is None else current_range),
                *current_tps(),
                *updates.values(),
            ]
            if observation.get("full_tp_snapshot"):
                next_tp_state: dict[int, float] = {}
            else:
                next_tp_state = dict(tp_state)
            for index in sorted(updates):
                raw = updates[index]
                current = tp_state.get(index)
                canonical, decision = _canonical_tp_candidate(
                    raw,
                    current=current,
                    direction=direction,
                    entry_range=current_range,
                    references=references,
                )
                if canonical is not None:
                    next_tp_state[index] = canonical
                if decision != "accepted":
                    issues.append(_canonical_issue(
                        observation,
                        field=f"tp{index}",
                        raw=raw,
                        canonical=canonical,
                        decision=decision,
                    ))
            tp_state = next_tp_state

        if raw_sl is not None:
            previous_sl = current_sl
            if observation.get("explicit_sl_move"):
                canonical_sl, decision = _canonical_management_sl_candidate(
                    float(raw_sl),
                    current=current_sl,
                    entry_range=current_range,
                    tps=current_tps(),
                )
            else:
                canonical_sl, decision = _canonical_sl_candidate(
                    float(raw_sl),
                    current=current_sl,
                    direction=direction,
                    entry_range=current_range,
                    tps=current_tps(),
                )
            current_sl = canonical_sl
            if decision != "accepted":
                issues.append(_canonical_issue(
                    observation,
                    field="sl",
                    raw=float(raw_sl),
                    canonical=current_sl,
                    decision=decision,
                ))
            elif previous_sl != current_sl:
                current_sl = canonical_sl

        if pending_range is not None and apply_range(
            pending_range, observation,
        ):
            pending_range = None

        if has_level_update:
            level_timeline.append({
                "telegram_ts_utc": observation.get("telegram_ts_utc"),
                "observed_ts_utc": observation.get("observed_ts_utc"),
                "tps": current_tps(),
                "sl": current_sl,
                "source_message_id": observation.get("source_message_id"),
                "source_kind": observation.get("source_kind"),
                "raw_tps": raw_tps,
                "raw_sl": raw_sl,
                "_source_order": int(observation.get("_source_order") or 0),
            })

    if pending_range is not None:
        issues.append(_canonical_issue(
            pending_range,
            field="entry_range",
            raw=list(pending_range["raw_range"]),
            canonical=current_range,
            decision="rejected_unresolved",
        ))

    signal["entry_zone_timeline"] = entry_timeline
    signal["level_timeline"] = level_timeline
    signal["effective_range"] = current_range
    signal["effective_tps"] = current_tps()
    signal["effective_sl"] = current_sl
    signal["canonicalization_issues"] = issues

def _finalize(signal: dict) -> dict:
    signal["revisions"].sort(key=_causal_row_sort_key)
    signal["entry_zone_timeline"].sort(
        key=lambda row: _causal_row_sort_key(
            row,
            message_id_key="source_message_id",
        )
    )
    signal["level_timeline"].sort(
        key=lambda row: _causal_row_sort_key(
            row,
            message_id_key="source_message_id",
        )
    )
    signal["runtime_level_timeline"].sort(key=_causal_row_sort_key)
    signal["zone_plan_timeline"].sort(key=_causal_row_sort_key)
    for revision in signal["revisions"]:
        parsed = revision.get("parsed", {})
        if parsed.get("range"):
            signal["effective_range"] = parsed["range"]
        if parsed.get("tps"):
            signal["effective_tps"] = parsed["tps"]
        if parsed.get("sl") is not None:
            signal["effective_sl"] = parsed["sl"]
    signal["management_events"].sort(key=_causal_row_sort_key)
    signal["source_message_ids"].sort()
    signal["execution_sig_ids"].sort()
    signal["execution_batches"].sort(key=lambda row: (
        *_timestamp_sort_key(
            row.get("signal_received_utc") or row.get("first_fill_utc")),
        row["execution_batch_id"],
    ))
    signal["execution_count"] = len(signal["execution_batches"])
    canonical_batches = signal["execution_batches"][:1]
    signal["canonical_execution_batch_ids"] = [
        batch["execution_batch_id"] for batch in canonical_batches
    ]
    signal["canonical_execution_count"] = len(canonical_batches)
    canonical_sig_id = (
        canonical_batches[0]["sig_id"] if canonical_batches else None
    )
    signal["canonical_corrections"] = [
        {
            "type": "exclude_execution_batch",
            "reason": (
                "duplicate_delivery_execution"
                if batch["sig_id"] == canonical_sig_id
                else "duplicate_provider_identity_execution"
            ),
            "execution_batch_id": batch["execution_batch_id"],
            "sig_id": batch["sig_id"],
            "ticket_ids": list(batch["ticket_ids"]),
            "preserved_in_observed_replay": True,
        }
        for batch in signal["execution_batches"][1:]
    ]
    signal["duplicate_execution"] = (
        signal["execution_count"] > signal["canonical_execution_count"]
    )

    direction_evidence: list[dict] = []
    entry_candidates: list[dict] = []
    for revision in signal["revisions"]:
        parsed_direction = revision.get("parsed", {}).get("direction")
        observed_dt = _parse_dt(revision.get("observed_ts_utc"))
        if not parsed_direction or observed_dt is None:
            continue
        direction_source = f"revision_parser:{revision['message_id']}"
        direction_evidence.append({
            "observed_dt": observed_dt,
            "observed_ts_utc": revision.get("observed_ts_utc"),
            "telegram_ts_utc": revision.get("telegram_ts_utc"),
            "message_id": revision["message_id"],
            "direction": parsed_direction,
            "direction_source": direction_source,
            "_source_order": revision["_source_order"],
        })
        if signal["channel"] == "canal2":
            is_actionable_entry = is_canal2_entry(revision.get("text") or "")
        else:
            is_actionable_entry = (
                revision.get("sticker_id") is not None
                or is_canal1_signal_text(revision.get("text") or "")
            )
        if not is_actionable_entry:
            continue
        if _is_edit_update_kind((revision.get("update_kinds") or [None])[0]):
            trigger_kind = "edit"
        elif revision.get("sticker_id") is not None:
            trigger_kind = "sticker"
        else:
            trigger_kind = "text"
        entry_candidates.append({
            "observed_dt": observed_dt,
            "observed_ts_utc": revision.get("observed_ts_utc"),
            "telegram_ts_utc": revision.get("telegram_ts_utc"),
            "message_id": revision["message_id"],
            "trigger_kind": trigger_kind,
            "direction": parsed_direction,
            "direction_source": direction_source,
            "_source_order": revision["_source_order"],
        })

    root_sticker = next(
        (
            revision
            for revision in signal["revisions"]
            if revision["message_id"] == signal["root_message_id"]
            and revision.get("sticker_id") is not None
        ),
        None,
    )
    for understood in signal.get("_understood_direction_candidates", []):
        understood_dt = _parse_dt(understood.get("observed_ts_utc"))
        if understood_dt is None:
            continue
        direction_evidence.append({
            **understood,
            "observed_dt": understood_dt,
        })
        if (
            root_sticker is None
            or understood["message_id"] != signal["root_message_id"]
        ):
            continue
        sticker_dt = _parse_dt(root_sticker.get("observed_ts_utc"))
        if sticker_dt is None:
            continue
        if understood_dt >= sticker_dt:
            causal_event = understood
            causal_dt = understood_dt
        else:
            causal_event = root_sticker
            causal_dt = sticker_dt
        entry_candidates.append({
            "observed_dt": causal_dt,
            "observed_ts_utc": causal_event.get("observed_ts_utc"),
            "telegram_ts_utc": (
                understood.get("telegram_ts_utc")
                or root_sticker.get("telegram_ts_utc")
            ),
            "message_id": understood["message_id"],
            "trigger_kind": "sticker",
            "direction": understood["direction"],
            "direction_source": "telegram_understood",
            "_source_order": causal_event["_source_order"],
        })

    if direction_evidence:
        effective_direction = max(direction_evidence, key=lambda candidate: (
            candidate["observed_dt"],
            candidate["message_id"],
            candidate["_source_order"],
        ))
        signal["direction"] = effective_direction["direction"]
        signal["_direction_source"] = effective_direction["direction_source"]
        signal["_direction_observed_utc"] = effective_direction["observed_ts_utc"]
    _rebuild_canonical_timeline(signal)

    gaps: list[str] = []
    root_seen = signal.pop("_root_message_seen")
    immediate_market_entry = (
        signal["channel"] == "canal2"
        and any(
            is_canal2_entry(revision.get("text") or "")
            for revision in signal["revisions"]
        )
    )
    if signal["record_type"] == "formal_signal":
        if not root_seen:
            gaps.append("missing_root_message")
        if not signal.get("direction"):
            gaps.append("missing_direction")
        if not signal.get("effective_range") and not immediate_market_entry:
            gaps.append("missing_entry_range")
        if not signal.get("effective_tps"):
            gaps.append("missing_tps")
        if signal.get("effective_sl") is None:
            gaps.append("missing_sl")
        semantic_status = "complete" if not gaps else "incomplete"
    elif signal["record_type"] == "unknown_candidate":
        gaps.append("unclassified_record_type")
        semantic_status = "needs_review"
    else:
        semantic_status = "classified"
    signal["semantic_gaps"] = gaps
    signal["semantic_status"] = semantic_status

    entry_candidates.sort(key=lambda candidate: (
        candidate["observed_dt"],
        candidate["message_id"],
        candidate["_source_order"],
    ))
    trigger = entry_candidates[0] if entry_candidates else None
    if signal["record_type"] != "formal_signal":
        trigger = None

    contract_direction = (
        trigger["direction"] if trigger is not None else signal.get("direction")
    )
    contract_direction_source = (
        trigger["direction_source"]
        if trigger is not None
        else signal.get("_direction_source")
    )
    blockers: list[str] = []
    if not contract_direction:
        blockers.append("missing_direction")
    if signal["record_type"] == "zone_plan":
        blockers.append("provider_zone_plan_not_live_trigger")
    elif signal["record_type"] != "formal_signal":
        blockers.append("provider_record_not_formal_signal")
    elif trigger is None:
        blockers.append("missing_actionable_entry_trigger")

    signal["entry_contract"] = {
        "status": "ready" if not blockers else "blocked",
        "trigger_observed_utc": (
            trigger.get("observed_ts_utc") if trigger is not None else None
        ),
        "trigger_telegram_utc": (
            trigger.get("telegram_ts_utc") if trigger is not None else None
        ),
        "trigger_message_id": (
            trigger.get("message_id") if trigger is not None else None
        ),
        "trigger_kind": (
            trigger.get("trigger_kind") if trigger is not None else None
        ),
        "direction": contract_direction,
        "direction_source": contract_direction_source,
        "blockers": blockers,
    }
    _strip_private_fields(signal)
    return signal


def _summary(signals: list[dict]) -> dict:
    channels: dict[str, dict] = {}
    for channel in sorted({row["channel"] for row in signals}):
        records = [row for row in signals if row["channel"] == channel]
        selected = [row for row in records if row["record_type"] == "formal_signal"]
        channels[channel] = {
            "records": len(records),
            "provider_signals": len(selected),
            "complete_signals": sum(
                row["semantic_status"] == "complete" for row in selected),
            "incomplete_signals": sum(
                row["semantic_status"] != "complete" for row in selected),
            "executed_signals": sum(row["execution_count"] > 0 for row in selected),
            "unexecuted_signals": sum(row["execution_count"] == 0 for row in selected),
            "duplicate_execution_signals": sum(
                row["duplicate_execution"] for row in selected),
            "record_types": {
                record_type: sum(
                    row["record_type"] == record_type for row in records)
                for record_type in sorted({row["record_type"] for row in records})
            },
        }
    formal = [row for row in signals if row["record_type"] == "formal_signal"]
    return {
        "records": len(signals),
        "provider_signals": len(formal),
        "formal_signals": len(formal),
        "complete_signals": sum(
            row["semantic_status"] == "complete" for row in formal),
        "incomplete_signals": sum(
            row["semantic_status"] != "complete" for row in formal),
        "executed_signals": sum(row["execution_count"] > 0 for row in formal),
        "unexecuted_signals": sum(row["execution_count"] == 0 for row in formal),
        "duplicate_execution_signals": sum(
            row["duplicate_execution"] for row in formal),
        "management_events": sum(len(row["management_events"]) for row in signals),
        "record_types": {
            record_type: sum(row["record_type"] == record_type for row in signals)
            for record_type in sorted({row["record_type"] for row in signals})
        },
        "channels": channels,
    }


def _attach_execution_range_assessments(
    signal: dict,
    replay_by_sig: dict[str, dict],
) -> None:
    entry_range = signal.get("effective_range")
    if not entry_range or len(entry_range) != 2:
        signal["execution_range_assessments"] = []
        return
    low, high = sorted(float(value) for value in entry_range)
    assessments = []
    for sig_id in signal.get("execution_sig_ids") or []:
        trade = replay_by_sig.get(str(sig_id))
        if trade is None:
            continue
        tickets = []
        for ticket in trade.get("tickets") or []:
            try:
                open_price = float(ticket.get("open_price"))
            except (TypeError, ValueError):
                continue
            inside = low <= open_price <= high
            if open_price < low:
                distance = low - open_price
            elif open_price > high:
                distance = open_price - high
            else:
                distance = 0.0
            tickets.append({
                "ticket": ticket.get("ticket") or ticket.get("position_ticket"),
                "open_price": open_price,
                "inside_final_provider_range": inside,
                "distance_outside": round(distance, 3),
            })
        runtime_quality = (
            (trade.get("decisions") or {}).get("entry_quality")
        )
        assessments.append({
            "sig_id": str(sig_id),
            "canonical_range": [low, high],
            "runtime_entry_quality": (
                dict(runtime_quality)
                if isinstance(runtime_quality, dict) else None
            ),
            "tickets": tickets,
            "all_entries_inside": bool(tickets) and all(
                row["inside_final_provider_range"] for row in tickets
            ),
            "max_distance_outside": max(
                (row["distance_outside"] for row in tickets),
                default=None,
            ),
        })
    signal["execution_range_assessments"] = assessments


def build_catalog_report(events: Iterable[dict], replay_trades: Iterable[dict]) -> dict:
    events = [
        {**row, "_source_order": source_order}
        for source_order, row in enumerate(events)
    ]
    events.sort(key=lambda row: (
        *_timestamp_sort_key(row.get("ts")),
        row["_source_order"],
    ))
    replay_trades = list(replay_trades)
    execution_batches_by_sig = _execution_batches(events, replay_trades)
    signals: dict[tuple[str, int], dict] = {}

    def ensure(
        channel: str,
        message_id: int,
        record_type: str | None = None,
        reason: str | None = None,
    ) -> dict:
        key = (channel, int(message_id))
        if key not in signals:
            signals[key] = _empty_signal(
                *key,
                record_type=record_type or "unknown_candidate",
                record_type_reason=reason or "unclassified_provider_message",
            )
        elif record_type is not None:
            _promote_record_type(
                signals[key], record_type, reason or "additional_evidence")
        return signals[key]

    raw_events = [row for row in events if row.get("ev") == "telegram_raw"]
    reply_parent_by_key: dict[tuple[str, int], int] = {}
    for row in raw_events:
        channel = str(row.get("channel") or "")
        message_id = row.get("message_id")
        reply_to = row.get("reply_to_msg_id")
        if channel in ("canal1", "canal2") and message_id is not None and reply_to is not None:
            reply_parent_by_key[(channel, int(message_id))] = int(reply_to)

    canal2_entry_evidence: dict[int, dict] = {}
    for row in raw_events:
        if row.get("channel") != "canal2":
            continue
        message_id = row.get("message_id")
        text = str(row.get("text") or "")
        if message_id is None or not is_canal2_entry(text):
            continue
        message_id = int(message_id)
        observed_dt = _parse_dt(_telegram_ts(row))
        if observed_dt is None:
            continue
        parsed = _normalise_parsed(parse_canal2(text))
        candidate = {
            "message_id": message_id,
            "observed_dt": observed_dt,
            "direction": parsed.get("direction"),
            "plain": not any(
                key in parsed for key in ("range", "tps", "sl")
            ),
            "normalized_text": canal2_entry_command_key(text),
            "is_reply": bool(
                row.get("is_reply") or row.get("reply_to_msg_id") is not None
            ),
        }
        previous = canal2_entry_evidence.get(message_id)
        if previous is None or observed_dt < previous["observed_dt"]:
            canal2_entry_evidence[message_id] = candidate

    canal2_alias_roots: dict[int, int] = {}
    canal2_identity_links: dict[int, list[dict]] = {}

    def register_canal2_alias(alias_id: int, root_id: int, link: dict) -> None:
        alias_id = int(alias_id)
        root_id = int(root_id)
        if alias_id == root_id:
            return
        existing_root = canal2_alias_roots.get(alias_id)
        if existing_root is None:
            canal2_alias_roots[alias_id] = root_id
        elif existing_root != root_id:
            return
        links = canal2_identity_links.setdefault(root_id, [])
        if link not in links:
            links.append(link)

    for row in events:
        if row.get("ev") != "canal2_duplicate_alias_registered":
            continue
        parsed_root = _message_id_from_sig(row.get("sig"))
        alias_id = row.get("alias_message_id")
        if (
            parsed_root is None
            or parsed_root[0] != "canal2"
            or alias_id is None
        ):
            continue
        register_canal2_alias(
            int(alias_id),
            parsed_root[1],
            {
                "source": "runtime_duplicate_alias",
                "root_message_id": parsed_root[1],
                "companion_message_id": int(alias_id),
                "telegram_gap_ms": None,
            },
        )

    ordered_canal2_entries = sorted(
        canal2_entry_evidence.values(),
        key=lambda row: (row["observed_dt"], row["message_id"]),
    )
    for previous, current in zip(
        ordered_canal2_entries,
        ordered_canal2_entries[1:],
    ):
        gap_ms = int(
            (current["observed_dt"] - previous["observed_dt"])
            / timedelta(milliseconds=1)
        )
        is_repeated_reply_command = (
            0 <= gap_ms <= 10_000
            and previous["is_reply"]
            and not current["is_reply"]
            and previous["plain"]
            and current["plain"]
            and previous["direction"] == current["direction"]
            and previous["normalized_text"] == current["normalized_text"]
        )
        if not is_repeated_reply_command:
            continue
        register_canal2_alias(
            current["message_id"],
            previous["message_id"],
            {
                "source": "near_duplicate_immediate_command",
                "root_message_id": previous["message_id"],
                "companion_message_id": current["message_id"],
                "telegram_gap_ms": gap_ms,
            },
        )

    def canal2_root(message_id: int) -> int:
        current = int(message_id)
        seen = set()
        while current in canal2_alias_roots and current not in seen:
            seen.add(current)
            current = canal2_alias_roots[current]
        return current

    def thread_root(channel: str, message_id: int) -> int:
        current = int(message_id)
        seen = set()
        while current not in seen:
            if channel == "canal2" and current in canal2_entry_evidence:
                return canal2_root(current)
            seen.add(current)
            parent = reply_parent_by_key.get((channel, current))
            if parent is None:
                break
            current = parent
        return canal2_root(current) if channel == "canal2" else current

    sticker_evidence: dict[int, datetime] = {}
    text_evidence: dict[int, datetime] = {}
    for row in raw_events:
        if row.get("channel") != "canal1" or row.get("is_reply"):
            continue
        message_id = row.get("message_id")
        observed_dt = _parse_dt(row.get("ts"))
        if message_id is None or observed_dt is None:
            continue
        message_id = int(message_id)
        if row.get("sticker_id") is not None:
            sticker_evidence.setdefault(message_id, observed_dt)
        if is_canal1_signal_text(str(row.get("text") or "")):
            text_evidence.setdefault(message_id, observed_dt)

    processing_roots_by_text: dict[int, set[int]] = {}
    for row in events:
        if row.get("ev") != "canal1_text_processing":
            continue
        parsed_sig = _message_id_from_sig(row.get("sig"))
        source_message_id = row.get("source_msg_id")
        if (
            parsed_sig is None
            or parsed_sig[0] != "canal1"
            or source_message_id is None
        ):
            continue
        processing_roots_by_text.setdefault(
            int(source_message_id),
            set(),
        ).add(parsed_sig[1])

    def is_preceding_sticker(sticker_id: int, text_id: int) -> bool:
        sticker = sticker_evidence.get(sticker_id)
        text = text_evidence.get(text_id)
        if sticker is None or text is None:
            return False
        return (sticker, sticker_id) < (text, text_id)

    def identity_link(
        sticker_id: int,
        text_id: int,
        source: str,
    ) -> dict:
        observed_gap = text_evidence[text_id] - sticker_evidence[sticker_id]
        return {
            "source": source,
            "root_message_id": sticker_id,
            "companion_message_id": text_id,
            "observed_gap_ms": (
                observed_gap // timedelta(milliseconds=1)
            ),
        }

    canal1_text_roots: dict[int, int] = {}
    canal1_identity_links: dict[int, list[dict]] = {}
    raw_paired_stickers: set[int] = set()
    unmatched_text_ids: list[int] = []
    ordered_texts = sorted(
        text_evidence.items(),
        key=lambda item: (item[1], item[0]),
    )
    for text_id, text_dt in ordered_texts:
        candidates = [
            sticker_id
            for sticker_id in sticker_evidence
            if sticker_id not in raw_paired_stickers
            and is_preceding_sticker(sticker_id, text_id)
            and text_dt - sticker_evidence[sticker_id] <= timedelta(minutes=3)
        ]
        if not candidates:
            unmatched_text_ids.append(text_id)
            continue
        sticker_id = max(
            candidates,
            key=lambda candidate: (sticker_evidence[candidate], candidate),
        )
        canal1_text_roots[text_id] = sticker_id
        raw_paired_stickers.add(sticker_id)
        canal1_identity_links.setdefault(sticker_id, []).append(
            identity_link(sticker_id, text_id, "raw_nearest")
        )

    fallback_paired_stickers: set[int] = set()
    for text_id in unmatched_text_ids:
        candidates = [
            sticker_id
            for sticker_id in processing_roots_by_text.get(text_id, set())
            if sticker_id not in fallback_paired_stickers
            and is_preceding_sticker(sticker_id, text_id)
        ]
        if not candidates:
            continue
        sticker_id = max(
            candidates,
            key=lambda candidate: (sticker_evidence[candidate], candidate),
        )
        canal1_text_roots[text_id] = sticker_id
        fallback_paired_stickers.add(sticker_id)
        canal1_identity_links.setdefault(sticker_id, []).append(
            identity_link(sticker_id, text_id, "processing_fallback")
        )

    understood_directions_by_key: dict[tuple[str, int], list[dict]] = {}
    for row in events:
        if row.get("ev") != "telegram_understood" or not row.get("direction"):
            continue
        channel = row.get("channel")
        message_id = row.get("message_id")
        if channel and message_id is not None:
            key = (str(channel), int(message_id))
            understood_directions_by_key.setdefault(key, []).append({
                "observed_ts_utc": row.get("ts"),
                "telegram_ts_utc": (
                    str(row["tg_ts"]) if row.get("tg_ts") else None
                ),
                "message_id": int(message_id),
                "direction": str(row["direction"]),
                "direction_source": "telegram_understood",
                "_source_order": row["_source_order"],
                "provenance": {
                    "event": "telegram_understood",
                    "sig": row.get("sig"),
                    "kind": row.get("kind"),
                    "parser": row.get("parser"),
                },
            })
    for candidates in understood_directions_by_key.values():
        candidates.sort(key=_causal_row_sort_key)

    unknown_sticker_keys = {
        parsed
        for row in events
        if row.get("ev") == "sticker_unknown"
        and (parsed := _message_id_from_sig(row.get("sig"))) is not None
        and parsed[0] == "canal1"
    }
    paired_canal1_sticker_roots = set(canal1_text_roots.values())
    root_keys: set[tuple[str, int]] = set()
    for row in raw_events:
        channel = str(row.get("channel") or "")
        message_id = row.get("message_id")
        if channel not in ("canal1", "canal2") or message_id is None:
            continue
        message_id = int(message_id)
        text = str(row.get("text") or "")
        canal2_entry = channel == "canal2" and is_canal2_entry(text)
        if (
            row.get("is_reply") or row.get("reply_to_msg_id") is not None
        ) and not canal2_entry:
            continue
        if canal2_entry:
            root_keys.add((channel, canal2_root(message_id)))
        elif channel == "canal1":
            if message_id in canal1_text_roots and is_canal1_signal_text(text):
                root_keys.add((channel, canal1_text_roots[message_id]))
            elif row.get("sticker_id") is not None:
                key = (channel, message_id)
                if (
                    key not in unknown_sticker_keys
                    or message_id in paired_canal1_sticker_roots
                ):
                    root_keys.add(key)
            elif is_canal1_signal_text(text):
                root_keys.add((channel, message_id))

    for channel, message_id in root_keys:
        ensure(
            channel,
            message_id,
            "formal_signal",
            "entry_or_execution_evidence",
        )

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
            if (
                channel == "canal2"
                and message_id in canal2_entry_evidence
            ):
                root_id = canal2_root(message_id)
                signal = ensure(
                    channel,
                    root_id,
                    "formal_signal",
                    "immediate_entry_reply",
                )
                if message_id == root_id:
                    signal["_root_message_seen"] = True
                _append_revision(signal, row)
                _append_zone_plan(signal, row)
                continue
            resolved_root = thread_root(channel, message_id)
            root_id = canal1_text_roots.get(resolved_root, resolved_root)
            if channel == "canal2":
                root_id = canal2_root(root_id)
            key = (channel, root_id)
            zone_plan = _parse_zone_plan(str(row.get("text") or ""))
            if zone_plan is not None:
                signal = ensure(
                    channel,
                    root_id,
                    "zone_plan",
                    "provider_multi_zone_plan",
                )
                _append_revision(signal, row)
                _append_zone_plan(signal, row)
            elif key in signals or _looks_like_management(str(row.get("text") or "")):
                signal = ensure(
                    channel,
                    root_id,
                    "management_only" if key not in signals else None,
                    "reply_to_missing_root" if key not in signals else None,
                )
                _append_management(signal, row)
            continue

        root_id = canal1_text_roots.get(message_id, message_id)
        if channel == "canal2":
            root_id = canal2_root(root_id)
        formal = (channel, root_id) in root_keys
        record_type, reason = _record_type_for_root(row, formal=formal)
        signal = ensure(channel, root_id, record_type, reason)
        if message_id == root_id:
            signal["_root_message_seen"] = True
        _append_revision(signal, row)
        _append_zone_plan(signal, row)
        if signal["record_type"] == "management_only":
            _append_management(signal, row)

    for (channel, message_id), candidates in understood_directions_by_key.items():
        root_id = canal1_text_roots.get(message_id, message_id)
        if channel == "canal2":
            root_id = canal2_root(root_id)
        if (channel, root_id) not in signals:
            continue
        signal = signals[(channel, root_id)]
        signal["_understood_direction_candidates"].extend(candidates)

    for row in events:
        if row.get("ev") != "entry_levels_interpreted":
            continue
        parsed_sig = _message_id_from_sig(row.get("sig"))
        if not parsed_sig:
            continue
        channel, message_id = parsed_sig
        root_id = canal1_text_roots.get(message_id, message_id)
        if channel == "canal2":
            root_id = canal2_root(root_id)
        key = (channel, root_id)
        if key not in signals:
            continue
        _append_runtime_level(signals[key], row)

    for trade in replay_trades:
        parsed_sig = _message_id_from_sig(trade.get("sig_id"))
        if not parsed_sig:
            continue
        channel, message_id = parsed_sig
        if channel not in ("canal1", "canal2"):
            continue
        root_id = canal1_text_roots.get(message_id, message_id)
        if channel == "canal2":
            root_id = canal2_root(root_id)
        key = (channel, root_id)
        if key in signals:
            signal = signals[key]
        else:
            signal = ensure(
                channel,
                root_id,
                "unknown_candidate",
                "linked_execution_evidence_without_raw_root",
            )
        sig_id = str(trade.get("sig_id"))
        if sig_id not in signal["execution_sig_ids"]:
            signal["execution_sig_ids"].append(sig_id)

    for sig_id, batches in execution_batches_by_sig.items():
        parsed_sig = _message_id_from_sig(sig_id)
        if not parsed_sig:
            continue
        channel, message_id = parsed_sig
        if channel not in ("canal1", "canal2"):
            continue
        root_id = canal1_text_roots.get(message_id, message_id)
        if channel == "canal2":
            root_id = canal2_root(root_id)
        key = (channel, root_id)
        if key in signals:
            signal = signals[key]
        else:
            signal = ensure(
                channel,
                root_id,
                "unknown_candidate",
                "linked_execution_evidence_without_raw_root",
            )
        if sig_id not in signal["execution_sig_ids"]:
            signal["execution_sig_ids"].append(sig_id)
        signal["execution_batches"].extend(
            dict(batch) for batch in batches
        )

    for root_id, links in canal1_identity_links.items():
        key = ("canal1", root_id)
        if key not in signals:
            continue
        signals[key]["identity_links"] = sorted(
            links,
            key=lambda link: (
                link["observed_gap_ms"],
                link["companion_message_id"],
                link["source"],
            ),
        )

    for root_id, links in canal2_identity_links.items():
        key = ("canal2", canal2_root(root_id))
        if key not in signals:
            continue
        signals[key]["identity_links"] = sorted(
            links,
            key=lambda link: (
                link["telegram_gap_ms"]
                if link["telegram_gap_ms"] is not None
                else float("inf"),
                link["companion_message_id"],
                link["source"],
            ),
        )

    finalized = [_finalize(signal) for signal in signals.values()]
    replay_by_sig = {
        str(trade.get("sig_id")): trade
        for trade in replay_trades
        if trade.get("sig_id")
    }
    for signal in finalized:
        _attach_execution_range_assessments(signal, replay_by_sig)
    finalized.sort(key=lambda row: (
        *_timestamp_sort_key(row.get("first_observed_utc")),
        row["provider_signal_id"],
    ))
    return {
        "schema_version": SCHEMA_VERSION,
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
