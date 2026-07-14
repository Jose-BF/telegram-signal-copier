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
SCHEMA_VERSION = 3
RECORD_TYPES = {
    "formal_signal",
    "context_setup",
    "daily_summary",
    "management_only",
    "unknown_candidate",
}
RECORD_TYPE_PRIORITY = {
    "unknown_candidate": 0,
    "management_only": 1,
    "context_setup": 2,
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
    r"(?:\s+\w+){0,3}?\s+(?:TO|AT)\s+(\d{3,5}(?:\.\d+)?)\b",
    re.IGNORECASE,
)
CLOSE_TP_RE = re.compile(r"\bCLOSE\s+TP\s*(\d+)\b", re.IGNORECASE)
DAILY_SUMMARY_RE = re.compile(
    r"\b(?:WEEKLY|DAILY|MONDAY|TUESDAY|WEDNESDAY|THURSDAY|FRIDAY)\s+SUMMARY\b|"
    r"\b(?:SIGNALS?|TRADES?)\s+SENT\b.*\b(?:WINS?|WINNING|LOSSES?|STOP\s*LOSS)\b",
    re.IGNORECASE | re.DOTALL,
)
CONTEXT_SETUP_RE = re.compile(
    r"\b(?:SUPPORT|RESISTANCE|4\s*H(?:R|OUR)|ANALYSIS|LEVELS?|ZONE)\b",
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
    for key in ("price", "target_tp_index", "levels"):
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
    if has_break_even and "CLOSE" in upper and re.search(r"\bOR\b", upper):
        return {
            "action": "MANAGEMENT_CHOICE",
            "modality": "optional",
            "execution_options": [
                {"action": "CLOSE_ALL"},
                {"action": "MOVE_SL_TO_BE"},
            ],
        }
    if has_break_even:
        result = {
            "action": "MOVE_SL_TO_BE",
            "modality": _management_modality(text, actionable=True),
        }
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

    if re.search(r"\b(?:TAKE|CLOSE|SECURE)\s+(?:SOME\s+)?PARTIALS?\b", upper):
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
            "modality": _management_modality(text, actionable=True),
        }
        result["execution_options"] = _execution_options(result)
        return result
    return None


def _record_type_for_root(row: dict, *, formal: bool) -> tuple[str, str]:
    if formal:
        return "formal_signal", "entry_or_execution_evidence"
    text = str(row.get("text") or "")
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
        "revisions": [],
        "entry_zone_timeline": [],
        "level_timeline": [],
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
        "execution_count": 0,
        "duplicate_execution": False,
        "semantic_status": "incomplete",
        "semantic_gaps": [],
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
    for field in ("price", "target_tp_index", "levels"):
        if semantics.get(field) is not None:
            event[field] = semantics[field]
    signal["management_events"].append(event)
    signal["_management_last_by_message"][state_key] = {
        "text": normalized_text,
        "event": event,
    }


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
    signal["execution_count"] = len(signal["execution_sig_ids"])
    signal["duplicate_execution"] = signal["execution_count"] > 1

    if signal["execution_count"]:
        _promote_record_type(
            signal, "formal_signal", "linked_execution_evidence")

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

    gaps: list[str] = []
    root_seen = signal.pop("_root_message_seen")
    if signal["record_type"] == "formal_signal":
        if not root_seen:
            gaps.append("missing_root_message")
        if not signal.get("direction"):
            gaps.append("missing_direction")
        if not signal.get("effective_range"):
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
    if trigger is None:
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

    for trade in replay_trades:
        parsed_sig = _message_id_from_sig(trade.get("sig_id"))
        if not parsed_sig:
            continue
        channel, message_id = parsed_sig
        if channel not in ("canal1", "canal2"):
            continue
        root_keys.add((channel, canal1_text_roots.get(message_id, message_id)))

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
            root_id = canal1_text_roots.get(int(reply_to), int(reply_to))
            key = (channel, root_id)
            if key in signals or _looks_like_management(str(row.get("text") or "")):
                signal = ensure(
                    channel,
                    root_id,
                    "management_only" if key not in signals else None,
                    "reply_to_missing_root" if key not in signals else None,
                )
                _append_management(signal, row)
            continue

        root_id = canal1_text_roots.get(message_id, message_id)
        formal = (channel, root_id) in root_keys
        record_type, reason = _record_type_for_root(row, formal=formal)
        signal = ensure(channel, root_id, record_type, reason)
        if message_id == root_id:
            signal["_root_message_seen"] = True
        _append_revision(signal, row)
        if signal["record_type"] == "management_only":
            _append_management(signal, row)

    for (channel, message_id), candidates in understood_directions_by_key.items():
        root_id = canal1_text_roots.get(message_id, message_id)
        if (channel, root_id) not in signals:
            continue
        signal = signals[(channel, root_id)]
        signal["_understood_direction_candidates"].extend(candidates)

    for trade in replay_trades:
        parsed_sig = _message_id_from_sig(trade.get("sig_id"))
        if not parsed_sig:
            continue
        channel, message_id = parsed_sig
        if channel not in ("canal1", "canal2"):
            continue
        root_id = canal1_text_roots.get(message_id, message_id)
        signal = ensure(
            channel,
            root_id,
            "formal_signal",
            "linked_execution_evidence",
        )
        sig_id = str(trade.get("sig_id"))
        if sig_id not in signal["execution_sig_ids"]:
            signal["execution_sig_ids"].append(sig_id)

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

    finalized = [_finalize(signal) for signal in signals.values()]
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
