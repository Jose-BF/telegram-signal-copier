"""Read-only completeness audit for causal Telegram-to-MT5 evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional


TELEGRAM_RAW_EVENTS = {"telegram_raw"}
TELEGRAM_MEDIA_EVIDENCE_EVENTS = {"telegram_media_capture_stored"}
TELEGRAM_DECISION_START_EVENTS = {"telegram_decision_started"}
TELEGRAM_PROCESSED_EVENTS = {"telegram_processed"}
TELEGRAM_FAILED_DECISION_EVENTS = {"telegram_processing_failed"}
TELEGRAM_DECISION_EVENTS = (
    TELEGRAM_DECISION_START_EVENTS
    | TELEGRAM_PROCESSED_EVENTS
    | TELEGRAM_FAILED_DECISION_EVENTS
)
INTERNAL_DECISION_START_EVENTS = {"bot_internal_decision_started"}
INTERNAL_DECISION_EVENTS = {"bot_internal_decision"}
DECISION_START_EVENTS = (
    TELEGRAM_DECISION_START_EVENTS | INTERNAL_DECISION_START_EVENTS
)
DECISION_ROOT_EVENTS = (
    TELEGRAM_PROCESSED_EVENTS
    | TELEGRAM_FAILED_DECISION_EVENTS
    | INTERNAL_DECISION_EVENTS
)
ACTION_ROOT_EVENTS = {
    "mt5_order_requested",
    "mt5_modify_requested",
    "mt5_close_requested",
    "mt5_cancel_requested",
    "mt5_pending_action_restored",
}
ACTION_RELATION_EVENTS = {
    "mt5_action_coalesced",
}
ACTION_EVENTS = ACTION_ROOT_EVENTS | ACTION_RELATION_EVENTS
ATTEMPT_EVENTS = {"mt5_action_attempt"}
BROKER_RESULT_EVENTS = {
    "mt5_order_result",
    "mt5_modify_confirmed",
    "mt5_close_result",
    "mt5_cancel_result",
    "mt5_modify_attempt_superseded",
    "market_fill_recovered_from_non_done",
}
ACTION_LIFECYCLE_EVENTS = {
    "mt5_action_failed",
    "mt5_modify_skipped_position_gone",
    "mt5_modify_waiting_precondition",
    "mt5_modify_precondition_satisfied",
    "mt5_position_snapshot",
    "mt5_structural_incident",
}
ACTION_TERMINAL_EVENTS = {
    "mt5_action_failed",
    "mt5_modify_skipped_position_gone",
}
RELEVANT_EVENTS = (
    TELEGRAM_RAW_EVENTS
    | TELEGRAM_MEDIA_EVIDENCE_EVENTS
    | DECISION_START_EVENTS
    | DECISION_ROOT_EVENTS
    | ACTION_EVENTS
    | ATTEMPT_EVENTS
    | BROKER_RESULT_EVENTS
    | ACTION_LIFECYCLE_EVENTS
)
ENVELOPE_FIELDS = {
    "schema_version",
    "event_id",
    "session_id",
    "ts",
    "monotonic_ns",
    "code_commit",
    "payload_sha256",
}
_HEX_DIGITS = frozenset("0123456789abcdef")
_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
_MT5_COMMENT_SIGNAL_RE = re.compile(
    r"^(?:DCA_)?c([12])_([1-9]\d*)(?:_[A-Za-z0-9.]+)?"
)
_SIGNAL_ID_RE = re.compile(r"^(canal[12])_([1-9]\d*)$")
_EXPECTED_MAGIC_BY_CHANNEL = {
    "canal1": 20260421,
    "canal2": 20260422,
}
_ATTEMPT_OPERATIONS = {
    "OPEN_MARKET",
    "PLACE_LIMIT",
    "MODIFY_SLTP",
    "CLOSE_POSITION",
    "CANCEL_PENDING",
}
_LOOKUP_STATES = {
    "not_queried",
    "found",
    "empty",
    "unavailable",
}
_BROKER_RESULT_FIELDS = (
    "retcode",
    "comment",
    "order",
    "deal",
    "volume",
    "price",
    "bid",
    "ask",
    "request_id",
    "retcode_external",
)
_PENDING_KIND_OPERATIONS = {
    "MODIFY_SLTP": "MODIFY_SLTP",
    "CLOSE_POSITION": "CLOSE_POSITION",
    "CANCEL_PENDING": "CANCEL_PENDING",
}
_ENTRY_REQUEST_CONTRACTS = {
    "OPEN_MARKET": {
        "action": 1,
        "types": {"BUY": 0, "SELL": 1},
    },
    "PLACE_LIMIT": {
        "action": 5,
        "types": {"BUY": 2, "SELL": 3},
    },
}


def _schema_version(row: dict) -> int:
    try:
        return int(row.get("schema_version") or 0)
    except (TypeError, ValueError):
        return 0


def _valid_runtime_id(value, prefix: str) -> bool:
    return (
        isinstance(value, str)
        and value.startswith(prefix)
        and len(value) > len(prefix)
    )


def _has_complete_envelope(row: dict) -> bool:
    payload_hash = row.get("payload_sha256")
    monotonic_ns = row.get("monotonic_ns")
    code_commit = row.get("code_commit")
    return (
        _schema_version(row) >= 2
        and _valid_runtime_id(row.get("event_id"), "event_")
        and _valid_runtime_id(row.get("session_id"), "session_")
        and isinstance(monotonic_ns, int)
        and not isinstance(monotonic_ns, bool)
        and monotonic_ns >= 0
        and isinstance(code_commit, str)
        and _COMMIT_RE.fullmatch(code_commit) is not None
        and isinstance(payload_hash, str)
        and len(payload_hash) == 64
        and set(payload_hash) <= _HEX_DIGITS
        and bool(row.get("sig"))
    )


def _valid_sha256(value) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and set(value) <= _HEX_DIGITS
    )


def _valid_sha1(value) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and set(value) <= _HEX_DIGITS
    )


def _contains_non_finite(value) -> bool:
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, dict):
        return any(_contains_non_finite(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_non_finite(item) for item in value)
    return False


def _reject_non_finite_json_constant(token: str):
    raise ValueError(f"non-finite JSON constant: {token}")


def _valid_positive_number(value) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value > 0
    )


def _valid_finite_number(value) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def _valid_nonnegative_number(value) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value >= 0
    )


def _expected_magic_for_signal(signal_id) -> Optional[int]:
    if not isinstance(signal_id, str):
        return None
    match = _SIGNAL_ID_RE.fullmatch(signal_id)
    if match is None:
        return None
    return _EXPECTED_MAGIC_BY_CHANNEL.get(match.group(1))


def _same_level(left, right) -> bool:
    """Treat MT5's 0.0 and the journal's None as the same unset level."""
    if isinstance(left, bool) or isinstance(right, bool):
        return False
    left_unset = left is None or left == 0
    right_unset = right is None or right == 0
    if left_unset or right_unset:
        return left_unset and right_unset
    return (
        _valid_nonnegative_number(left)
        and _valid_nonnegative_number(right)
        and left == right
    )


def _valid_optional_evidence_level(row: dict, field: str) -> bool:
    return (
        field in row
        and (
            row.get(field) is None
            or _valid_nonnegative_number(row.get(field))
        )
    )


def _valid_attempt_preflight_contract(row: dict) -> bool:
    operation = row.get("operation")
    preflight_fields = (
        "preflight_status",
        "preflight_reason",
        "preflight_effective_sl",
        "preflight_effective_tp",
        "preflight_deferred_sl",
    )
    expected_magic = _expected_magic_for_signal(row.get("sig"))

    if operation in {"OPEN_MARKET", "PLACE_LIMIT"}:
        return (
            row.get("expected_magic") is None
            and all(row.get(field) is None for field in preflight_fields)
        )

    if (
        expected_magic is None
        or row.get("expected_magic") != expected_magic
    ):
        return False
    if operation != "MODIFY_SLTP":
        return all(row.get(field) is None for field in preflight_fields)

    if not all(
        _valid_optional_evidence_level(row, field)
        for field in (
            "preflight_effective_sl",
            "preflight_effective_tp",
            "preflight_deferred_sl",
        )
    ):
        return False
    status = row.get("preflight_status")
    if status == "ready":
        return (
            row.get("preflight_reason") is None
            and row.get("preflight_deferred_sl") is None
        )
    if status == "apply_tp_defer_sl":
        reason = row.get("preflight_reason")
        return (
            isinstance(reason, str)
            and bool(reason.strip())
            and _valid_positive_number(
                row.get("preflight_effective_tp")
            )
            and _valid_positive_number(
                row.get("preflight_deferred_sl")
            )
        )
    return False


def _valid_tick_evidence(value) -> bool:
    if not isinstance(value, dict):
        return False
    time_msc = value.get("time_msc")
    bid = value.get("bid")
    ask = value.get("ask")
    return (
        not isinstance(time_msc, bool)
        and isinstance(time_msc, int)
        and time_msc > 0
        and _valid_positive_number(bid)
        and _valid_positive_number(ask)
        and ask >= bid
    )


def _valid_position_evidence(value) -> bool:
    if not isinstance(value, dict):
        return False
    ticket = value.get("ticket")
    magic = value.get("magic")
    position_type = value.get("type")
    return (
        not isinstance(ticket, bool)
        and isinstance(ticket, int)
        and ticket > 0
        and isinstance(value.get("symbol"), str)
        and bool(value.get("symbol"))
        and not isinstance(magic, bool)
        and isinstance(magic, int)
        and not isinstance(position_type, bool)
        and position_type in {0, 1}
        and _valid_positive_number(value.get("volume"))
        and _valid_positive_number(value.get("price_open"))
        and _valid_positive_number(value.get("price_current"))
        and _valid_nonnegative_number(value.get("sl"))
        and _valid_nonnegative_number(value.get("tp"))
        and _valid_finite_number(value.get("profit"))
        and isinstance(value.get("comment"), str)
    )


def _valid_order_evidence(value) -> bool:
    if not isinstance(value, dict):
        return False
    ticket = value.get("ticket")
    magic = value.get("magic")
    order_type = value.get("type")
    return (
        not isinstance(ticket, bool)
        and isinstance(ticket, int)
        and ticket > 0
        and isinstance(value.get("symbol"), str)
        and bool(value.get("symbol"))
        and not isinstance(magic, bool)
        and isinstance(magic, int)
        and not isinstance(order_type, bool)
        and isinstance(order_type, int)
        and 2 <= order_type <= 7
        and _valid_positive_number(value.get("volume_initial"))
        and _valid_positive_number(value.get("volume_current"))
        and _valid_positive_number(value.get("price_open"))
        and _valid_positive_number(value.get("price_current"))
        and _valid_nonnegative_number(value.get("sl"))
        and _valid_nonnegative_number(value.get("tp"))
        and isinstance(value.get("comment"), str)
    )


def _valid_symbol_contract(value) -> bool:
    if not isinstance(value, dict):
        return False
    digits = value.get("digits")
    return (
        _valid_positive_number(value.get("point"))
        and not isinstance(digits, bool)
        and isinstance(digits, int)
        and digits >= 0
        and _valid_nonnegative_number(value.get("trade_stops_level"))
        and _valid_nonnegative_number(value.get("trade_freeze_level"))
    )


def _valid_ticket(value) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int)
        and value > 0
    )


def _valid_optional_level(request: dict, field: str) -> bool:
    return (
        field not in request
        or request.get(field) is None
        or _valid_nonnegative_number(request.get(field))
    )


def _valid_request_evidence(operation: str, request) -> bool:
    if not isinstance(request, dict):
        return False
    if operation in {"OPEN_MARKET", "PLACE_LIMIT", "CLOSE_POSITION"}:
        expected_action = 5 if operation == "PLACE_LIMIT" else 1
        allowed_types = {2, 3} if operation == "PLACE_LIMIT" else {0, 1}
        if not (
            request.get("action") == expected_action
            and isinstance(request.get("symbol"), str)
            and bool(request.get("symbol"))
            and _valid_positive_number(request.get("volume"))
            and request.get("type") in allowed_types
            and not isinstance(request.get("type"), bool)
            and _valid_positive_number(request.get("price"))
            and _valid_nonnegative_number(request.get("deviation"))
            and isinstance(request.get("magic"), int)
            and not isinstance(request.get("magic"), bool)
            and isinstance(request.get("comment"), str)
            and isinstance(request.get("type_time"), int)
            and not isinstance(request.get("type_time"), bool)
            and isinstance(request.get("type_filling"), int)
            and not isinstance(request.get("type_filling"), bool)
            and _valid_optional_level(request, "sl")
            and _valid_optional_level(request, "tp")
        ):
            return False
        return (
            operation != "CLOSE_POSITION"
            or _valid_ticket(request.get("position"))
        )
    if operation == "MODIFY_SLTP":
        position = request.get("position")
        order = request.get("order")
        if _valid_ticket(position) and order is None:
            return (
                request.get("action") == 6
                and _valid_nonnegative_number(request.get("sl"))
                and _valid_nonnegative_number(request.get("tp"))
            )
        if _valid_ticket(order) and position is None:
            return (
                request.get("action") == 7
                and _valid_positive_number(request.get("price"))
                and _valid_nonnegative_number(request.get("sl"))
                and _valid_nonnegative_number(request.get("tp"))
            )
        return False
    if operation == "CANCEL_PENDING":
        return (
            request.get("action") == 8
            and _valid_ticket(request.get("order"))
        )
    return False


def _has_complete_broker_result(result: dict) -> bool:
    return all(field in result for field in _BROKER_RESULT_FIELDS)


def _semantic_is_edit(update_kind) -> Optional[bool]:
    normalized = str(update_kind or "").strip().lower()
    if not normalized:
        return None
    return normalized == "edit" or normalized.endswith("_edit")


def _telegram_identity(row: dict) -> tuple:
    # ``update_kind`` is delivery transport, not immutable message identity.
    # The same Telegram revision can legitimately arrive as live ``edit`` and
    # later as ``poll_new``; each raw row validates its own is_edit flag.
    return (
        row.get("channel"),
        row.get("chat_id"),
        row.get("message_id"),
        row.get("revision_token"),
    )


def _has_stored_media_evidence(row: dict) -> bool:
    size_bytes = row.get("size_bytes")
    attempts = row.get("attempts")
    return (
        row.get("ev") in TELEGRAM_MEDIA_EVIDENCE_EVENTS
        and isinstance(row.get("message_revision_id"), str)
        and bool(row.get("message_revision_id"))
        and _valid_sha256(row.get("media_sha256"))
        and isinstance(size_bytes, int)
        and not isinstance(size_bytes, bool)
        and size_bytes > 0
        and isinstance(row.get("storage_path"), str)
        and bool(row.get("storage_path"))
        and isinstance(row.get("archive_stream"), str)
        and bool(row.get("archive_stream"))
        and isinstance(attempts, int)
        and not isinstance(attempts, bool)
        and attempts > 0
    )


def _has_raw_message_evidence(row: dict) -> bool:
    if not {
        "channel",
        "chat_id",
        "message_id",
        "update_kind",
        "is_edit",
        "revision_token",
        "text",
        "text_sha1",
        "has_text",
        "has_media",
        "media_sha256",
        "message_revision_id",
    }.issubset(row):
        return False
    channel = row.get("channel")
    chat_id = row.get("chat_id")
    message_id = row.get("message_id")
    update_kind = row.get("update_kind")
    is_edit = row.get("is_edit")
    revision_token = row.get("revision_token")
    text = row.get("text")
    text_sha1 = row.get("text_sha1")
    has_text = row.get("has_text")
    has_media = row.get("has_media")
    media_sha256 = row.get("media_sha256")
    if (
        not isinstance(channel, str)
        or not channel
        or not isinstance(update_kind, str)
        or not update_kind
        or not isinstance(is_edit, bool)
        or is_edit != _semantic_is_edit(update_kind)
        or isinstance(chat_id, bool)
        or not isinstance(chat_id, int)
        or isinstance(message_id, bool)
        or not isinstance(message_id, int)
        or message_id <= 0
        or not isinstance(revision_token, str)
        or not revision_token
        or not isinstance(text, str)
        or not isinstance(has_text, bool)
        or has_text != bool(text)
        or not isinstance(has_media, bool)
    ):
        return False
    if has_text:
        expected_text_sha1 = hashlib.sha1(
            text.encode("utf-8")
        ).hexdigest()
        if (
            not _valid_sha1(text_sha1)
            or text_sha1 != expected_text_sha1
        ):
            return False
    elif text_sha1 is not None:
        return False
    if media_sha256 is not None and not _valid_sha256(media_sha256):
        return False
    if not has_media and media_sha256 is not None:
        return False

    canonical = json.dumps(
        {
            "chat_id": chat_id,
            "media_sha256": media_sha256,
            "message_id": message_id,
            "revision_token": revision_token,
            "text_sha1": text_sha1,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    expected_revision_id = (
        f"msgrev_{hashlib.sha256(canonical).hexdigest()}"
    )
    return row.get("message_revision_id") == expected_revision_id


def _declared_action_manifest(row: dict) -> Optional[list[str]]:
    raw_ids = row.get("declared_action_ids")
    count = row.get("declared_action_count")
    if (
        not isinstance(raw_ids, list)
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count != len(raw_ids)
    ):
        return None
    normalized = []
    seen = set()
    for value in raw_ids:
        if not isinstance(value, str) or not value.startswith("action_"):
            return None
        if value in seen:
            return None
        seen.add(value)
        normalized.append(value)
    return normalized


def _has_attempt_evidence(row: dict) -> bool:
    if not {
        "operation",
        "ticket",
        "attempt_started_utc",
        "attempt_finished_utc",
        "attempt_started_monotonic_ns",
        "attempt_finished_monotonic_ns",
        "duration_ns",
        "broker_request_sent",
        "request",
        "result",
        "last_error",
        "exception",
        "source_tick",
        "validation_tick",
        "position_before",
        "order_before",
        "symbol_contract",
        "source_tick_lookup_state",
        "validation_tick_lookup_state",
        "position_lookup_state",
        "order_lookup_state",
        "symbol_info_lookup_state",
        "terminal_state",
        "account_state",
        "expected_magic",
        "preflight_status",
        "preflight_reason",
        "preflight_effective_sl",
        "preflight_effective_tp",
        "preflight_deferred_sl",
    }.issubset(row):
        return False
    if row.get("operation") not in _ATTEMPT_OPERATIONS:
        return False
    if not _valid_attempt_preflight_contract(row):
        return False
    started_utc = _parse_contract_utc_ts(
        row.get("attempt_started_utc")
    )
    finished_utc = _parse_contract_utc_ts(
        row.get("attempt_finished_utc")
    )
    if (
        started_utc is None
        or finished_utc is None
        or finished_utc < started_utc
    ):
        return False
    started_ns = row.get("attempt_started_monotonic_ns")
    finished_ns = row.get("attempt_finished_monotonic_ns")
    if (
        isinstance(started_ns, bool)
        or not isinstance(started_ns, int)
        or isinstance(finished_ns, bool)
        or not isinstance(finished_ns, int)
        or started_ns < 0
        or finished_ns < started_ns
    ):
        return False
    duration_ns = row.get("duration_ns")
    if (
        isinstance(duration_ns, bool)
        or not isinstance(duration_ns, int)
        or duration_ns != finished_ns - started_ns
    ):
        return False
    sent = row.get("broker_request_sent")
    if not isinstance(sent, bool):
        return False
    request = row.get("request")
    if sent and (not isinstance(request, dict) or not request):
        return False
    if not sent and request is not None:
        return False
    if sent and not _valid_request_evidence(row.get("operation"), request):
        return False
    result = row.get("result")
    if not isinstance(result, dict):
        return False
    lookup_payloads = {
        "source_tick_lookup_state": "source_tick",
        "validation_tick_lookup_state": "validation_tick",
        "position_lookup_state": "position_before",
        "order_lookup_state": "order_before",
        "symbol_info_lookup_state": "symbol_contract",
    }
    for state_field, payload_field in lookup_payloads.items():
        state = row.get(state_field)
        if state not in _LOOKUP_STATES:
            return False
        payload = row.get(payload_field)
        if state == "found" and not isinstance(payload, dict):
            return False
        if state != "found" and payload is not None:
            return False
    for state_field, payload_field in (
        ("source_tick_lookup_state", "source_tick"),
        ("validation_tick_lookup_state", "validation_tick"),
    ):
        if (
            row.get(state_field) == "found"
            and not _valid_tick_evidence(row.get(payload_field))
        ):
            return False
    for state_field, payload_field, validator in (
        (
            "position_lookup_state",
            "position_before",
            _valid_position_evidence,
        ),
        ("order_lookup_state", "order_before", _valid_order_evidence),
        (
            "symbol_info_lookup_state",
            "symbol_contract",
            _valid_symbol_contract,
        ),
    ):
        if (
            row.get(state_field) == "found"
            and not validator(row.get(payload_field))
        ):
            return False
    if (
        sent
        and row.get("operation") in {"OPEN_MARKET", "CLOSE_POSITION"}
        and row.get("source_tick_lookup_state") != "found"
    ):
        return False
    has_retcode = result.get("retcode") is not None
    has_last_error = row.get("last_error") not in (None, "", "None")
    exception = row.get("exception")
    has_exception = (
        isinstance(exception, dict)
        and bool(exception.get("type"))
    )
    if (
        sent
        and has_retcode
        and not has_last_error
        and not has_exception
        and not _has_complete_broker_result(result)
    ):
        return False
    return has_retcode or has_last_error or has_exception


def _broker_result_matches_attempt(
    row: dict,
    attempt_row: dict,
) -> bool:
    attempt_result = attempt_row.get("result")
    if not isinstance(attempt_result, dict):
        return False
    compared = False
    for field in _BROKER_RESULT_FIELDS:
        if field not in row:
            continue
        compared = True
        if field not in attempt_result:
            return False
        if row.get(field) != attempt_result.get(field):
            return False
    return compared


def _event_precedes_or_equals(earlier: dict, later: dict) -> bool:
    earlier_ts = _parse_contract_utc_ts(earlier.get("ts"))
    later_ts = _parse_contract_utc_ts(later.get("ts"))
    if earlier_ts is None or later_ts is None or earlier_ts > later_ts:
        return False
    if earlier.get("session_id") != later.get("session_id"):
        return True
    earlier_ns = earlier.get("monotonic_ns")
    later_ns = later.get("monotonic_ns")
    return (
        isinstance(earlier_ns, int)
        and not isinstance(earlier_ns, bool)
        and isinstance(later_ns, int)
        and not isinstance(later_ns, bool)
        and earlier_ns <= later_ns
    )


def _action_root_operation(row: dict) -> Optional[str]:
    event_name = row.get("ev")
    if event_name == "mt5_order_requested":
        return {
            "market": "OPEN_MARKET",
            "pending_limit": "PLACE_LIMIT",
        }.get(row.get("order_kind"))
    if event_name == "mt5_modify_requested":
        return "MODIFY_SLTP"
    if event_name == "mt5_close_requested":
        return "CLOSE_POSITION"
    if event_name == "mt5_cancel_requested":
        return "CANCEL_PENDING"
    if event_name == "mt5_pending_action_restored":
        return _PENDING_KIND_OPERATIONS.get(row.get("kind"))
    return None


def _has_action_root_evidence(row: dict) -> bool:
    operation = _action_root_operation(row)
    revision = row.get("action_revision")
    expected_magic = _expected_magic_for_signal(row.get("sig"))
    if (
        operation is None
        or expected_magic is None
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 0
    ):
        return False

    if operation in {"OPEN_MARKET", "PLACE_LIMIT"}:
        required = {
            "attempt_id",
            "direction",
            "lot",
            "requested_price",
            "sl",
            "tp",
            "magic",
            "comment",
            "deviation",
        }
        if not required.issubset(row):
            return False
        if (
            not _valid_runtime_id(row.get("attempt_id"), "attempt_")
            or row.get("direction") not in {"BUY", "SELL"}
            or not _valid_positive_number(row.get("lot"))
            or not (
                row.get("sl") is None
                or _valid_nonnegative_number(row.get("sl"))
            )
            or not (
                row.get("tp") is None
                or _valid_nonnegative_number(row.get("tp"))
            )
            or isinstance(row.get("magic"), bool)
            or not isinstance(row.get("magic"), int)
            or row.get("magic") != expected_magic
            or not isinstance(row.get("comment"), str)
            or not _valid_nonnegative_number(row.get("deviation"))
        ):
            return False
        requested_price = row.get("requested_price")
        if _valid_positive_number(requested_price):
            return True
        return (
            operation == "OPEN_MARKET"
            and requested_price is None
            and row.get("preflight_status") in {
                "source_tick_unavailable",
                "source_tick_error",
            }
        )

    if (
        not _valid_ticket(row.get("ticket"))
        or "expected_magic" not in row
        or row.get("expected_magic") != expected_magic
    ):
        return False
    if operation == "MODIFY_SLTP":
        if not {"new_sl", "new_tp"}.issubset(row):
            return False
        new_sl = row.get("new_sl")
        new_tp = row.get("new_tp")
        return (
            (new_sl is not None or new_tp is not None)
            and (
                new_sl is None
                or _valid_nonnegative_number(new_sl)
            )
            and (
                new_tp is None
                or _valid_nonnegative_number(new_tp)
            )
        )
    return True


def _signal_id_from_mt5_comment(comment) -> Optional[str]:
    if not isinstance(comment, str):
        return None
    match = _MT5_COMMENT_SIGNAL_RE.match(comment)
    if match is None:
        return None
    return f"canal{match.group(1)}_{match.group(2)}"


def _attempt_owner_matches_action_root(
    attempt: dict,
    action_root: dict,
    operation: str,
) -> bool:
    evidence_fields = []
    if operation in {"MODIFY_SLTP", "CLOSE_POSITION"}:
        evidence_fields.append("position_before")
    if operation in {"MODIFY_SLTP", "CANCEL_PENDING"}:
        evidence_fields.append("order_before")

    action_signal = action_root.get("sig")
    expected_magic = action_root.get("expected_magic")
    expected_ticket = action_root.get("ticket")
    for field in evidence_fields:
        evidence = attempt.get(field)
        if not isinstance(evidence, dict):
            continue
        if (
            evidence.get("ticket") != expected_ticket
            or evidence.get("magic") != expected_magic
        ):
            return False
        observed_signal = _signal_id_from_mt5_comment(
            evidence.get("comment")
        )
        if observed_signal is not None and observed_signal != action_signal:
            return False
    return True


def _terminal_action_matches_root(
    terminal: dict,
    action_root: dict,
    *,
    attempt_ids: set[str],
    attempt_actions: dict[str, set[str]],
) -> bool:
    operation = _action_root_operation(action_root)
    if operation not in _PENDING_KIND_OPERATIONS:
        return False
    if not _event_precedes_or_equals(action_root, terminal):
        return False
    if (
        terminal.get("action_revision")
        != action_root.get("action_revision")
    ):
        return False
    if (
        terminal.get("ticket") != action_root.get("ticket")
        or not _valid_ticket(terminal.get("ticket"))
    ):
        return False
    expected_magic = _expected_magic_for_signal(
        action_root.get("sig")
    )
    if (
        expected_magic is None
        or action_root.get("expected_magic") != expected_magic
        or terminal.get("expected_magic") != expected_magic
    ):
        return False
    if operation == "MODIFY_SLTP" and (
        "new_sl" not in terminal
        or "new_tp" not in terminal
        or not _same_level(
            terminal.get("new_sl"),
            action_root.get("new_sl"),
        )
        or not _same_level(
            terminal.get("new_tp"),
            action_root.get("new_tp"),
        )
    ):
        return False

    event_name = terminal.get("ev")
    attempts = terminal.get("attempts")
    if (
        isinstance(attempts, bool)
        or not isinstance(attempts, int)
        or attempts < 0
    ):
        return False

    if event_name == "mt5_modify_skipped_position_gone":
        if (
            operation != "MODIFY_SLTP"
            or terminal.get("retcode") != 10036
            or not {
                "preflight_status",
                "preflight_reason",
                "preflight_effective_sl",
                "preflight_effective_tp",
                "preflight_deferred_sl",
            }.issubset(terminal)
            or terminal.get("preflight_status") != "position_gone"
            or not isinstance(terminal.get("preflight_reason"), str)
            or not terminal.get("preflight_reason").strip()
            or terminal.get("preflight_effective_sl") is not None
            or terminal.get("preflight_effective_tp") is not None
            or terminal.get("preflight_deferred_sl") is not None
        ):
            return False
    elif event_name == "mt5_action_failed":
        if (
            terminal.get("kind") != operation
            or not isinstance(terminal.get("reason"), str)
            or not terminal.get("reason").strip()
            or not _valid_nonnegative_number(
                terminal.get("age_seconds")
            )
        ):
            return False
        last_retcode = terminal.get("last_retcode")
        if (
            last_retcode is not None
            and (
                isinstance(last_retcode, bool)
                or not isinstance(last_retcode, int)
            )
        ):
            return False
        if attempts == 0 and last_retcode == 10013:
            observed_magic = terminal.get("preflight_observed_magic")
            if (
                operation != "MODIFY_SLTP"
                or terminal.get("preflight_status") != "invalid_magic"
                or terminal.get("preflight_reason") != "magic_mismatch"
                or terminal.get("preflight_effective_sl") is not None
                or terminal.get("preflight_effective_tp") is not None
                or terminal.get("preflight_deferred_sl") is not None
                or terminal.get("preflight_observed_ticket")
                != action_root.get("ticket")
                or isinstance(observed_magic, bool)
                or not isinstance(observed_magic, int)
                or observed_magic == expected_magic
                or terminal.get("preflight_observed_kind")
                not in {"position", "order"}
            ):
                return False
    else:
        return False

    attempt_id = terminal.get("attempt_id")
    if attempts == 0:
        if attempt_id not in (None, ""):
            return False
        if event_name == "mt5_modify_skipped_position_gone":
            return True
        if (
            event_name == "mt5_action_failed"
            and terminal.get("last_retcode") == 10013
            and terminal.get("preflight_status") == "invalid_magic"
        ):
            return True
        return terminal.get("last_retcode") is None
    return (
        _valid_runtime_id(attempt_id, "attempt_")
        and str(attempt_id) in attempt_ids
        and attempt_actions.get(str(attempt_id), set())
        == {str(terminal.get("action_id"))}
    )


def _action_relation_matches_roots(
    relation: dict,
    source_roots: list[dict],
    target_roots: list[dict],
) -> bool:
    coalesced_target = relation.get("coalesced_into_action_id")
    superseded_target = relation.get("supersedes_action_id")
    if bool(coalesced_target) == bool(superseded_target):
        return False
    if relation.get("queue_slots") != 1:
        return False
    for field in (
        "payload_changed",
        "label_changed",
        "persistence_changed",
    ):
        if not isinstance(relation.get(field), bool):
            return False
    changed = any(
        relation[field]
        for field in (
            "payload_changed",
            "label_changed",
            "persistence_changed",
        )
    )
    if coalesced_target and changed:
        return False
    if superseded_target and not changed:
        return False

    for source_root in source_roots:
        source_operation = _action_root_operation(source_root)
        expected_magic = _expected_magic_for_signal(
            source_root.get("sig")
        )
        if (
            source_operation != "MODIFY_SLTP"
            or expected_magic is None
            or source_root.get("expected_magic") != expected_magic
            or relation.get("expected_magic") != expected_magic
            or "new_sl" not in relation
            or "new_tp" not in relation
        ):
            continue
        if (
            relation.get("kind") != source_operation
            or relation.get("ticket") != source_root.get("ticket")
            or relation.get("sig") != source_root.get("sig")
            or relation.get("action_revision")
            != source_root.get("action_revision")
            or not _event_precedes_or_equals(source_root, relation)
        ):
            continue
        for target_root in target_roots:
            if (
                _action_root_operation(target_root) != source_operation
                or target_root.get("ticket") != source_root.get("ticket")
                or target_root.get("sig") != source_root.get("sig")
                or target_root.get("expected_magic") != expected_magic
                or not _event_precedes_or_equals(target_root, relation)
            ):
                continue
            source_revision = source_root.get("action_revision")
            target_revision = target_root.get("action_revision")
            if (
                isinstance(source_revision, bool)
                or not isinstance(source_revision, int)
                or isinstance(target_revision, bool)
                or not isinstance(target_revision, int)
            ):
                continue
            source_sl = source_root.get("new_sl")
            source_tp = source_root.get("new_tp")
            target_sl = target_root.get("new_sl")
            target_tp = target_root.get("new_tp")
            relation_sl = relation.get("new_sl")
            relation_tp = relation.get("new_tp")
            if coalesced_target and source_revision == target_revision:
                return (
                    _same_level(source_sl, target_sl)
                    and _same_level(source_tp, target_tp)
                    and _same_level(relation_sl, source_sl)
                    and _same_level(relation_tp, source_tp)
                    and relation.get("payload_changed") is False
                )
            if (
                superseded_target
                and source_revision == target_revision + 1
            ):
                merged_sl = (
                    source_sl if source_sl is not None else target_sl
                )
                merged_tp = (
                    source_tp if source_tp is not None else target_tp
                )
                payload_changed = (
                    not _same_level(merged_sl, target_sl)
                    or not _same_level(merged_tp, target_tp)
                )
                return (
                    relation.get("payload_changed")
                    is payload_changed
                    and _same_level(relation_sl, merged_sl)
                    and _same_level(relation_tp, merged_tp)
                )
    return False


def _broker_result_operation(row: dict) -> Optional[str]:
    event_name = row.get("ev")
    if event_name == "mt5_order_result":
        return {
            "market": "OPEN_MARKET",
            "pending_limit": "PLACE_LIMIT",
        }.get(row.get("order_kind"))
    return {
        "market_fill_recovered_from_non_done": "OPEN_MARKET",
        "mt5_modify_confirmed": "MODIFY_SLTP",
        "mt5_modify_attempt_superseded": "MODIFY_SLTP",
        "mt5_close_result": "CLOSE_POSITION",
        "mt5_cancel_result": "CANCEL_PENDING",
    }.get(event_name)


def _broker_result_matches_action_root(
    row: dict,
    action_root: dict,
    attempt: dict,
) -> bool:
    operation = _action_root_operation(action_root)
    if (
        operation is None
        or row.get("action_revision")
        != action_root.get("action_revision")
    ):
        return False
    event_name = row.get("ev")
    if operation in {"OPEN_MARKET", "PLACE_LIMIT"}:
        return event_name in {
            "mt5_order_result",
            "market_fill_recovered_from_non_done",
        }
    if row.get("ticket") != action_root.get("ticket"):
        return False
    if operation == "MODIFY_SLTP":
        expected_sl = action_root.get("new_sl")
        expected_tp = action_root.get("new_tp")
        if attempt.get("preflight_status") == "apply_tp_defer_sl":
            expected_sl = None
        if event_name == "mt5_modify_confirmed":
            return (
                "new_sl" in row
                and "new_tp" in row
                and _same_level(row.get("new_sl"), expected_sl)
                and _same_level(row.get("new_tp"), expected_tp)
            )
        if event_name == "mt5_modify_attempt_superseded":
            current_revision = row.get("current_revision")
            return (
                "attempted_sl" in row
                and "attempted_tp" in row
                and _same_level(
                    row.get("attempted_sl"),
                    expected_sl,
                )
                and _same_level(
                    row.get("attempted_tp"),
                    expected_tp,
                )
                and row.get("attempted_revision")
                == action_root.get("action_revision")
                and not isinstance(current_revision, bool)
                and isinstance(current_revision, int)
                and current_revision
                > action_root.get("action_revision")
            )
        return False
    return (
        (operation == "CLOSE_POSITION"
         and event_name == "mt5_close_result")
        or (
            operation == "CANCEL_PENDING"
            and event_name == "mt5_cancel_result"
        )
    )


def _action_intent_signature(row: dict) -> tuple:
    operation = _action_root_operation(row)
    common = (
        operation,
        row.get("action_revision"),
    )
    if operation in {"OPEN_MARKET", "PLACE_LIMIT"}:
        return common + (
            row.get("direction"),
            row.get("lot"),
            row.get("requested_price"),
            row.get("sl"),
            row.get("tp"),
            row.get("magic"),
            row.get("comment"),
            row.get("deviation"),
        )
    return common + (
        row.get("ticket"),
        row.get("new_sl"),
        row.get("new_tp"),
    )


def _request_matches_operation(
    operation: str,
    request: dict,
    action_root: dict,
    attempt: dict,
) -> bool:
    if operation in _ENTRY_REQUEST_CONTRACTS:
        contract = _ENTRY_REQUEST_CONTRACTS[operation]
        expected_type = contract["types"].get(action_root.get("direction"))
        return (
            request.get("action") == contract["action"]
            and expected_type is not None
            and request.get("type") == expected_type
        )
    if operation == "MODIFY_SLTP":
        has_position = request.get("position") is not None
        has_order = request.get("order") is not None
        return (
            has_position != has_order
            and (
                (has_position and request.get("action") == 6)
                or (has_order and request.get("action") == 7)
            )
        )
    if operation == "CLOSE_POSITION":
        position_before = attempt.get("position_before")
        expected_type = None
        if isinstance(position_before, dict):
            position_type = position_before.get("type")
            if position_type in {0, 1}:
                expected_type = 1 - position_type
        return (
            request.get("action") == 1
            and request.get("position") == action_root.get("ticket")
            and request.get("magic") == action_root.get("expected_magic")
            and (
                expected_type is None
                or request.get("type") == expected_type
            )
        )
    if operation == "CANCEL_PENDING":
        return (
            request.get("action") == 8
            and request.get("order") == action_root.get("ticket")
        )
    return False


def _modify_preflight_matches_action(
    attempt: dict,
    action_root: dict,
) -> bool:
    status = attempt.get("preflight_status")
    effective_sl = attempt.get("preflight_effective_sl")
    effective_tp = attempt.get("preflight_effective_tp")
    deferred_sl = attempt.get("preflight_deferred_sl")
    root_sl = action_root.get("new_sl")
    root_tp = action_root.get("new_tp")

    if status == "ready":
        if (
            deferred_sl is not None
            or (
                root_sl is not None
                and not _same_level(effective_sl, root_sl)
            )
            or (
                root_tp is not None
                and not _same_level(effective_tp, root_tp)
            )
        ):
            return False
    elif status == "apply_tp_defer_sl":
        position_before = attempt.get("position_before")
        if (
            root_sl is None
            or root_tp is None
            or not _same_level(deferred_sl, root_sl)
            or not _same_level(effective_tp, root_tp)
            or not isinstance(position_before, dict)
        ):
            return False
    else:
        return False

    if attempt.get("broker_request_sent") is not True:
        return True
    request = attempt.get("request")
    request_sl = effective_sl
    if status == "apply_tp_defer_sl":
        # The executor deliberately re-reads the position before sending.
        # Preserve that latest observed SL if it changed after queue preflight.
        request_sl = attempt["position_before"].get("sl")
    return (
        isinstance(request, dict)
        and _same_level(request.get("sl"), request_sl)
        and _same_level(request.get("tp"), effective_tp)
    )


def _attempt_matches_action_root(
    attempt: dict,
    action_root: dict,
) -> bool:
    operation = _action_root_operation(action_root)
    if operation is None or attempt.get("operation") != operation:
        return False
    if (
        attempt.get("action_revision")
        != action_root.get("action_revision")
    ):
        return False
    if operation in {
        "MODIFY_SLTP",
        "CLOSE_POSITION",
        "CANCEL_PENDING",
    }:
        if (
            attempt.get("ticket") != action_root.get("ticket")
            or attempt.get("expected_magic")
            != action_root.get("expected_magic")
        ):
            return False
    if not _attempt_owner_matches_action_root(
        attempt,
        action_root,
        operation,
    ):
        return False
    if (
        operation == "MODIFY_SLTP"
        and not _modify_preflight_matches_action(
            attempt,
            action_root,
        )
    ):
        return False

    if attempt.get("broker_request_sent") is not True:
        return True
    request = attempt.get("request")
    if not isinstance(request, dict):
        return False
    if not _request_matches_operation(
        operation,
        request,
        action_root,
        attempt,
    ):
        return False
    if operation in {"OPEN_MARKET", "PLACE_LIMIT"}:
        root_to_request = (
            ("lot", "volume"),
            ("requested_price", "price"),
            ("sl", "sl"),
            ("tp", "tp"),
            ("magic", "magic"),
            ("comment", "comment"),
            ("deviation", "deviation"),
        )
        return all(
            action_root.get(root_field) == request.get(request_field)
            for root_field, request_field in root_to_request
        )
    if operation == "CLOSE_POSITION":
        return request.get("position") == action_root.get("ticket")
    if operation == "CANCEL_PENDING":
        return request.get("order") == action_root.get("ticket")

    ticket = action_root.get("ticket")
    if request.get("position") != ticket and request.get("order") != ticket:
        return False
    return True


def _attempt_has_compatible_operation_contract(
    attempt: dict,
    action_root: dict,
) -> bool:
    operation = _action_root_operation(action_root)
    if operation is None or attempt.get("operation") != operation:
        return False
    request = attempt.get("request")
    if (
        attempt.get("broker_request_sent") is True
        and isinstance(request, dict)
        and request
    ):
        return _request_matches_operation(
            operation,
            request,
            action_root,
            attempt,
        )
    return True


def _semantic_payload_sha256(row: dict) -> str:
    semantic = {
        key: value
        for key, value in row.items()
        if key not in ENVELOPE_FIELDS
    }
    canonical = json.dumps(
        semantic,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _parse_ts(value) -> Optional[datetime]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_contract_utc_ts(value) -> Optional[datetime]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        return None
    return parsed.astimezone(timezone.utc)


def _date_boundary(value: Optional[str], *, end: bool) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if len(text) == 10:
        parsed = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        return parsed + timedelta(days=1) if end else parsed
    parsed = _parse_ts(text)
    if parsed is None:
        raise ValueError(f"invalid date boundary: {value}")
    return parsed


def _selected(
    row: dict,
    *,
    since: Optional[datetime],
    until_exclusive: Optional[datetime],
) -> bool:
    row_ts = _parse_ts(row.get("ts"))
    if row_ts is None:
        return True
    if since is not None and row_ts < since:
        return False
    if until_exclusive is not None and row_ts >= until_exclusive:
        return False
    return True


def _contract_activation(rows: list[tuple[int, dict]]) -> Optional[datetime]:
    candidates = []
    for _, row in rows:
        if (
            _schema_version(row) >= 2
            and row.get("event_id")
            and row.get("session_id")
        ):
            row_ts = _parse_ts(row.get("ts"))
            if row_ts is not None:
                candidates.append(row_ts)
    return min(candidates) if candidates else None


def _canonical_fingerprint(payload: dict) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _lineage_dependency_keys(row: dict) -> set[tuple[str, str]]:
    keys = set()
    for field, kind in (
        ("message_revision_id", "message"),
        ("decision_id", "decision"),
        ("action_id", "action"),
        ("attempt_id", "attempt"),
        ("coalesced_into_action_id", "action"),
        ("supersedes_action_id", "action"),
    ):
        value = row.get(field)
        if value:
            keys.add((kind, str(value)))
    for action_id in row.get("declared_action_ids") or []:
        if action_id:
            keys.add(("action", str(action_id)))
    return keys


def _find_parent_cycles(
    parent_ids: dict[str, set[str]],
) -> set[str]:
    cyclic = set()
    resolved = set()
    for start in sorted(parent_ids):
        if start in resolved:
            continue
        chain = []
        positions = {}
        current = start
        while (
            current in parent_ids
            and len(parent_ids[current]) == 1
            and current not in resolved
        ):
            if current in positions:
                cyclic.update(chain[positions[current]:])
                break
            positions[current] = len(chain)
            chain.append(current)
            current = next(iter(parent_ids[current]))
        resolved.update(chain)
    return cyclic


def audit_rows(
    rows: Iterable[dict],
    *,
    source_sha256: str,
    source_path: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    source_errors: Optional[Iterable[dict]] = None,
    source_line_count: Optional[int] = None,
) -> dict:
    all_rows = [dict(row) for row in rows]
    normalized_source_errors = sorted(
        (dict(error) for error in (source_errors or [])),
        key=lambda error: (
            int(error.get("line_number") or 0),
            str(error.get("reason") or ""),
        ),
    )
    since_dt = _date_boundary(since, end=False)
    until_dt = _date_boundary(until, end=True)
    selected = [
        (index, row)
        for index, row in enumerate(all_rows)
        if _selected(row, since=since_dt, until_exclusive=until_dt)
    ]
    relevant = [
        (index, row)
        for index, row in selected
        if row.get("ev") in RELEVANT_EVENTS
    ]
    all_indexed_rows = list(enumerate(all_rows))
    all_relevant = [
        (index, row)
        for index, row in all_indexed_rows
        if row.get("ev") in RELEVANT_EVENTS
    ]
    activation = _contract_activation(all_indexed_rows)

    event_id_counts = Counter(
        row.get("event_id")
        for _, row in all_indexed_rows
        if row.get("event_id")
    )
    duplicate_event_ids = {
        event_id
        for event_id, count in event_id_counts.items()
        if count > 1
    }

    action_decisions = defaultdict(set)
    action_message_revisions = defaultdict(set)
    decision_signals = defaultdict(set)
    action_signals = defaultdict(set)
    attempt_signals = defaultdict(set)
    decision_message_revisions = defaultdict(set)
    decision_parent_ids = defaultdict(set)
    decision_reasons = defaultdict(set)
    decision_manifest_variants = defaultdict(set)
    declared_action_owners = defaultdict(set)
    actions_declared_by_decision = defaultdict(set)
    attempt_actions = defaultdict(set)
    action_attempts = defaultdict(set)
    attempt_evidence_rows = defaultdict(list)
    action_root_rows = defaultdict(list)
    decision_start_rows = defaultdict(list)
    decision_final_rows = defaultdict(list)
    raw_message_identities = defaultdict(set)
    raw_message_rows = defaultdict(list)
    stored_media_rows = defaultdict(list)
    raw_message_revisions = set()
    processed_message_revisions = set()
    processed_decisions = set()
    decision_roots = set()
    decision_root_ids = set()
    action_roots = set()
    action_terminal_rows = defaultdict(list)
    action_relation_rows = defaultdict(list)
    attempt_ids = set()
    ticket_signal_owners = defaultdict(set)
    session_code_commits = defaultdict(set)
    for _, row in all_relevant:
        event_name = row.get("ev")
        message_revision_id = row.get("message_revision_id")
        action_id = row.get("action_id")
        decision_id = row.get("decision_id")
        attempt_id = row.get("attempt_id")
        session_id = row.get("session_id")
        code_commit = row.get("code_commit")
        if (
            _valid_runtime_id(session_id, "session_")
            and isinstance(code_commit, str)
            and _COMMIT_RE.fullmatch(code_commit) is not None
        ):
            session_code_commits[str(session_id)].add(
                code_commit.lower()
            )
        if (
            event_name in TELEGRAM_RAW_EVENTS
            and message_revision_id
        ):
            raw_message_revisions.add(str(message_revision_id))
            raw_message_identities[str(message_revision_id)].add(
                _telegram_identity(row)
            )
            raw_message_rows[str(message_revision_id)].append(row)
        if (
            event_name in TELEGRAM_MEDIA_EVIDENCE_EVENTS
            and message_revision_id
            and _has_stored_media_evidence(row)
        ):
            stored_media_rows[str(message_revision_id)].append(row)
        if (
            event_name in (DECISION_START_EVENTS | DECISION_ROOT_EVENTS)
            and decision_id
            and message_revision_id
        ):
            decision_message_revisions[str(decision_id)].add(
                str(message_revision_id)
            )
        if (
            event_name in (
                INTERNAL_DECISION_START_EVENTS
                | INTERNAL_DECISION_EVENTS
            )
            and decision_id
        ):
            parent_decision_id = row.get("parent_decision_id")
            decision_reason = row.get("decision_reason")
            if parent_decision_id:
                decision_parent_ids[str(decision_id)].add(
                    str(parent_decision_id)
                )
            if decision_reason:
                decision_reasons[str(decision_id)].add(
                    str(decision_reason)
                )
        if event_name in DECISION_START_EVENTS and decision_id:
            decision_start_rows[str(decision_id)].append(row)
        if event_name in DECISION_ROOT_EVENTS and decision_id:
            decision_final_rows[str(decision_id)].append(row)
            decision_root_ids.add(str(decision_id))
            if message_revision_id:
                decision_roots.add(
                    (str(message_revision_id), str(decision_id))
                )
            manifest = _declared_action_manifest(row)
            if manifest is not None:
                decision_manifest_variants[str(decision_id)].add(
                    tuple(manifest)
                )
                for declared_action_id in manifest:
                    actions_declared_by_decision[str(decision_id)].add(
                        declared_action_id
                    )
                    declared_action_owners[declared_action_id].add(
                        str(decision_id)
                    )
        if (
            event_name in (
                TELEGRAM_PROCESSED_EVENTS
                | TELEGRAM_FAILED_DECISION_EVENTS
            )
            and message_revision_id
            and decision_id
        ):
            processed_message_revisions.add(str(message_revision_id))
            processed_decisions.add((
                str(message_revision_id),
                str(decision_id),
            ))
            decision_message_revisions[str(decision_id)].add(
                str(message_revision_id)
            )
        if action_id and decision_id:
            action_decisions[str(action_id)].add(str(decision_id))
        if action_id and message_revision_id:
            action_message_revisions[str(action_id)].add(
                str(message_revision_id)
            )
        if attempt_id and action_id:
            attempt_actions[str(attempt_id)].add(str(action_id))
            if event_name in ATTEMPT_EVENTS:
                action_attempts[str(action_id)].add(str(attempt_id))
        sig_id = row.get("sig")
        if (
            sig_id
            and decision_id
            and event_name in (DECISION_START_EVENTS | DECISION_ROOT_EVENTS)
        ):
            decision_signals[str(decision_id)].add(str(sig_id))
        if sig_id and action_id:
            action_signals[str(action_id)].add(str(sig_id))
        if sig_id and attempt_id:
            attempt_signals[str(attempt_id)].add(str(sig_id))
        if event_name in ATTEMPT_EVENTS:
            for evidence_field in ("position_before", "order_before"):
                evidence = row.get(evidence_field)
                if not isinstance(evidence, dict):
                    continue
                observed_signal = _signal_id_from_mt5_comment(
                    evidence.get("comment")
                )
                ticket = evidence.get("ticket")
                if observed_signal and _valid_ticket(ticket):
                    ticket_signal_owners[int(ticket)].add(
                        observed_signal
                    )
            if (
                sig_id
                and row.get("operation") in {
                    "OPEN_MARKET",
                    "PLACE_LIMIT",
                }
            ):
                result = row.get("result")
                ticket = (
                    result.get("order")
                    if isinstance(result, dict)
                    else None
                )
                if _valid_ticket(ticket):
                    ticket_signal_owners[int(ticket)].add(str(sig_id))
        if (
            event_name == "market_fill_recovered_from_non_done"
            and sig_id
            and _valid_ticket(row.get("ticket"))
        ):
            ticket_signal_owners[int(row["ticket"])].add(str(sig_id))
        if event_name in ACTION_ROOT_EVENTS and action_id:
            action_roots.add(str(action_id))
            action_root_rows[str(action_id)].append(row)
        if event_name in ATTEMPT_EVENTS and attempt_id:
            attempt_ids.add(str(attempt_id))
            attempt_evidence_rows[str(attempt_id)].append(row)
        if event_name in ACTION_TERMINAL_EVENTS and action_id:
            action_terminal_rows[str(action_id)].append(row)
        if event_name in ACTION_RELATION_EVENTS and action_id:
            action_relation_rows[str(action_id)].append(row)

    relation_parent_ids = defaultdict(set)
    coalescence_mismatch_action_ids = set()
    for action_id, relation_rows in action_relation_rows.items():
        if len(relation_rows) != 1:
            coalescence_mismatch_action_ids.add(action_id)
            continue
        relation = relation_rows[0]
        targets = {
            str(target)
            for target in (
                relation.get("coalesced_into_action_id"),
                relation.get("supersedes_action_id"),
            )
            if target
        }
        if len(targets) != 1 or action_id in targets:
            continue
        target_id = next(iter(targets))
        relation_parent_ids[action_id].add(target_id)

    cyclic_action_relation_ids = _find_parent_cycles(
        relation_parent_ids
    )
    execution_action_root_rows = {
        action_id: list(root_rows)
        for action_id, root_rows in action_root_rows.items()
    }
    resolved_relation_ids = set()
    resolving_relation_ids = set()

    def resolve_relation_roots(action_id: str) -> list[dict]:
        if action_id in resolved_relation_ids:
            return execution_action_root_rows.get(action_id, [])
        if (
            action_id in cyclic_action_relation_ids
            or action_id in resolving_relation_ids
        ):
            return execution_action_root_rows.get(action_id, [])

        resolving_relation_ids.add(action_id)
        relation_rows = action_relation_rows.get(action_id, [])
        if (
            action_id in coalescence_mismatch_action_ids
            or len(relation_rows) != 1
        ):
            resolving_relation_ids.discard(action_id)
            resolved_relation_ids.add(action_id)
            return execution_action_root_rows.get(action_id, [])

        relation = relation_rows[0]
        targets = {
            str(target)
            for target in (
                relation.get("coalesced_into_action_id"),
                relation.get("supersedes_action_id"),
            )
            if target
        }
        if len(targets) != 1 or action_id in targets:
            resolving_relation_ids.discard(action_id)
            resolved_relation_ids.add(action_id)
            return execution_action_root_rows.get(action_id, [])

        target_id = next(iter(targets))
        target_roots = resolve_relation_roots(target_id)
        source_roots = action_root_rows.get(action_id, [])
        relation_valid = (
            bool(source_roots)
            and bool(target_roots)
            and _action_relation_matches_roots(
                relation,
                source_roots,
                target_roots,
            )
        )
        if source_roots and target_roots and not relation_valid:
            coalescence_mismatch_action_ids.add(action_id)

        effective_roots = []
        if relation_valid and relation.get("supersedes_action_id"):
            candidate_roots = source_roots
        else:
            candidate_roots = []
        for root_row in candidate_roots:
            if (
                _action_root_operation(root_row) != "MODIFY_SLTP"
                or root_row.get("ticket") != relation.get("ticket")
                or root_row.get("sig") != relation.get("sig")
                or root_row.get("action_revision")
                != relation.get("action_revision")
                or root_row.get("expected_magic")
                != relation.get("expected_magic")
            ):
                continue
            effective_root = dict(root_row)
            effective_root["new_sl"] = relation.get("new_sl")
            effective_root["new_tp"] = relation.get("new_tp")
            effective_roots.append(effective_root)
        if effective_roots:
            execution_action_root_rows[action_id] = effective_roots
        resolving_relation_ids.discard(action_id)
        resolved_relation_ids.add(action_id)
        return execution_action_root_rows.get(action_id, [])

    for relation_action_id in action_relation_rows:
        resolve_relation_roots(relation_action_id)

    valid_terminal_action_ids = set()
    invalid_terminal_action_ids = set()
    for action_id, terminal_rows in action_terminal_rows.items():
        root_rows = execution_action_root_rows.get(action_id, [])
        valid = (
            len(terminal_rows) == 1
            and bool(root_rows)
            and any(
                _terminal_action_matches_root(
                    terminal_rows[0],
                    root_row,
                    attempt_ids=attempt_ids,
                    attempt_actions=attempt_actions,
                )
                for root_row in root_rows
            )
        )
        if valid:
            valid_terminal_action_ids.add(action_id)
        else:
            invalid_terminal_action_ids.add(action_id)

    relation_terminal_actions = set()
    for action_id, relation_rows in action_relation_rows.items():
        if (
            action_id in coalescence_mismatch_action_ids
            or action_id in cyclic_action_relation_ids
            or len(relation_rows) != 1
        ):
            continue
        relation = relation_rows[0]
        if relation.get("coalesced_into_action_id"):
            relation_terminal_actions.add(action_id)
        if relation.get("supersedes_action_id"):
            relation_terminal_actions.add(
                str(relation["supersedes_action_id"])
            )
    action_terminals = (
        valid_terminal_action_ids | relation_terminal_actions
    )

    telegram_identity_mismatch_decisions = set()
    for _, row in all_relevant:
        if row.get("ev") not in TELEGRAM_DECISION_EVENTS:
            continue
        message_revision_id = row.get("message_revision_id")
        decision_id = row.get("decision_id")
        if not message_revision_id or not decision_id:
            continue
        raw_identities = raw_message_identities[
            str(message_revision_id)
        ]
        if raw_identities and (
            len(raw_identities) != 1
            or _telegram_identity(row) not in raw_identities
        ):
            telegram_identity_mismatch_decisions.add(str(decision_id))

    contradictory_message_revision_ids = sorted(
        message_revision_id
        for message_revision_id, identities
        in raw_message_identities.items()
        if len(identities) > 1
    )
    contradictory_session_ids = sorted(
        session_id
        for session_id, code_commits in session_code_commits.items()
        if len(code_commits) > 1
    )
    temporal_mismatch_decision_ids = set()
    for decision_id in (
        set(decision_start_rows) | set(decision_final_rows)
    ):
        starts = decision_start_rows.get(decision_id, [])
        finals = decision_final_rows.get(decision_id, [])
        if len(starts) > 1 or len(finals) > 1:
            temporal_mismatch_decision_ids.add(decision_id)
            continue
        if starts:
            start = starts[0]
            raw_rows = raw_message_rows.get(
                str(start.get("message_revision_id") or ""),
                [],
            )
            if raw_rows and not any(
                _event_precedes_or_equals(raw_row, start)
                for raw_row in raw_rows
            ):
                temporal_mismatch_decision_ids.add(decision_id)
        if starts and finals and not _event_precedes_or_equals(
            starts[0],
            finals[0],
        ):
            temporal_mismatch_decision_ids.add(decision_id)
        parents = decision_parent_ids.get(decision_id, set())
        if starts and len(parents) == 1:
            parent_id = next(iter(parents))
            parent_finals = decision_final_rows.get(parent_id, [])
            if parent_finals and not any(
                _event_precedes_or_equals(parent_final, starts[0])
                for parent_final in parent_finals
            ):
                temporal_mismatch_decision_ids.add(decision_id)

    parent_mismatch_decision_ids = {
        decision_id
        for decision_id in (
            set(decision_parent_ids) | set(decision_reasons)
        )
        if (
            len(decision_parent_ids[decision_id]) > 1
            or decision_id in decision_parent_ids[decision_id]
            or len(decision_reasons[decision_id]) > 1
        )
    }
    cyclic_decision_ids = _find_parent_cycles(decision_parent_ids)

    temporal_mismatch_action_ids = set()
    temporal_mismatch_attempt_ids = set()
    for action_id, root_rows in action_root_rows.items():
        for root_row in root_rows:
            decision_id = str(root_row.get("decision_id") or "")
            starts = decision_start_rows.get(decision_id, [])
            if starts and not any(
                _event_precedes_or_equals(start, root_row)
                for start in starts
            ):
                temporal_mismatch_action_ids.add(action_id)
                if decision_id:
                    temporal_mismatch_decision_ids.add(decision_id)
            if root_row.get("ev") == "mt5_pending_action_restored":
                continue
            finals = decision_final_rows.get(decision_id, [])
            if finals and not any(
                _event_precedes_or_equals(root_row, final)
                for final in finals
            ):
                temporal_mismatch_action_ids.add(action_id)
                if decision_id:
                    temporal_mismatch_decision_ids.add(decision_id)

    for attempt_id, evidence_rows in attempt_evidence_rows.items():
        if len(evidence_rows) != 1:
            continue
        attempt_row = evidence_rows[0]
        action_id = str(attempt_row.get("action_id") or "")
        roots = action_root_rows.get(action_id, [])
        if roots and not any(
            _event_precedes_or_equals(root, attempt_row)
            for root in roots
        ):
            temporal_mismatch_action_ids.add(action_id)
            temporal_mismatch_attempt_ids.add(attempt_id)

    action_intent_mismatch_ids = {
        action_id
        for action_id, root_rows in action_root_rows.items()
        if (
            any(
                _action_root_operation(root_row) is None
                for root_row in root_rows
            )
            or len({
                _action_intent_signature(root_row)
                for root_row in root_rows
            }) > 1
        )
    }
    ticket_owner_mismatch_action_ids = set()
    for action_id, root_rows in action_root_rows.items():
        for root_row in root_rows:
            if _action_root_operation(root_row) not in {
                "MODIFY_SLTP",
                "CLOSE_POSITION",
                "CANCEL_PENDING",
            }:
                continue
            ticket = root_row.get("ticket")
            if not _valid_ticket(ticket):
                continue
            known_owners = ticket_signal_owners.get(int(ticket), set())
            expected_owner = str(root_row.get("sig") or "")
            if known_owners and known_owners != {expected_owner}:
                ticket_owner_mismatch_action_ids.add(action_id)
    attempt_action_mismatch_ids = set()
    operation_mismatch_attempt_ids = set()
    for attempt_id, evidence_rows in attempt_evidence_rows.items():
        if len(evidence_rows) != 1:
            continue
        attempt_row = evidence_rows[0]
        action_id = str(attempt_row.get("action_id") or "")
        root_rows = execution_action_root_rows.get(action_id, [])
        if not root_rows:
            continue
        if not any(
            _attempt_has_compatible_operation_contract(
                attempt_row,
                root_row,
            )
            for root_row in root_rows
        ):
            attempt_action_mismatch_ids.add(action_id)
            operation_mismatch_attempt_ids.add(attempt_id)
            continue
        if not _has_attempt_evidence(attempt_row):
            continue
        if not any(
            _attempt_matches_action_root(attempt_row, root_row)
            for root_row in root_rows
        ):
            attempt_action_mismatch_ids.add(action_id)
            operation_mismatch_attempt_ids.add(attempt_id)

    broker_operation_mismatch_attempt_ids = set()
    for _, row in all_relevant:
        if row.get("ev") not in BROKER_RESULT_EVENTS:
            continue
        attempt_id = str(row.get("attempt_id") or "")
        evidence_rows = attempt_evidence_rows.get(attempt_id, [])
        if len(evidence_rows) != 1:
            continue
        action_id = str(row.get("action_id") or "")
        if not execution_action_root_rows.get(action_id):
            continue
        if not _has_attempt_evidence(evidence_rows[0]):
            continue
        expected_operation = _broker_result_operation(row)
        if (
            expected_operation is None
            or evidence_rows[0].get("operation") != expected_operation
            or not any(
                _broker_result_matches_action_root(
                    row,
                    action_root,
                    evidence_rows[0],
                )
                for action_root in execution_action_root_rows[action_id]
            )
            or not _event_precedes_or_equals(evidence_rows[0], row)
        ):
            broker_operation_mismatch_attempt_ids.add(attempt_id)
            if action_id:
                attempt_action_mismatch_ids.add(action_id)

    contradictory_decision_ids = sorted(
        decision_id
        for decision_id in (
            set(decision_message_revisions)
            | set(decision_manifest_variants)
            | telegram_identity_mismatch_decisions
            | set(decision_signals)
            | temporal_mismatch_decision_ids
            | parent_mismatch_decision_ids
            | cyclic_decision_ids
        )
        if (
            len(decision_message_revisions[decision_id]) > 1
            or len(decision_manifest_variants[decision_id]) > 1
            or decision_id in telegram_identity_mismatch_decisions
            or len(decision_signals[decision_id]) > 1
            or decision_id in temporal_mismatch_decision_ids
            or decision_id in parent_mismatch_decision_ids
            or decision_id in cyclic_decision_ids
        )
    )
    contradictory_action_ids = sorted(
        action_id
        for action_id in (
            set(action_decisions)
            | set(action_message_revisions)
            | set(declared_action_owners)
            | action_intent_mismatch_ids
            | ticket_owner_mismatch_action_ids
            | coalescence_mismatch_action_ids
            | cyclic_action_relation_ids
            | attempt_action_mismatch_ids
            | set(action_signals)
            | temporal_mismatch_action_ids
        )
        if (
            len(action_decisions[action_id]) > 1
            or len(action_message_revisions[action_id]) > 1
            or len(declared_action_owners[action_id]) > 1
            or action_id in action_intent_mismatch_ids
            or action_id in ticket_owner_mismatch_action_ids
            or action_id in coalescence_mismatch_action_ids
            or action_id in cyclic_action_relation_ids
            or action_id in attempt_action_mismatch_ids
            or len(action_signals[action_id]) > 1
            or action_id in temporal_mismatch_action_ids
        )
    )
    contradictory_attempt_ids = sorted(
        attempt_id
        for attempt_id in (
            set(attempt_actions) | set(attempt_evidence_rows)
            | operation_mismatch_attempt_ids
            | broker_operation_mismatch_attempt_ids
            | set(attempt_signals)
            | temporal_mismatch_attempt_ids
        )
        if (
            len(attempt_actions[attempt_id]) > 1
            or len(attempt_evidence_rows[attempt_id]) > 1
            or attempt_id in operation_mismatch_attempt_ids
            or attempt_id in broker_operation_mismatch_attempt_ids
            or len(attempt_signals[attempt_id]) > 1
            or attempt_id in temporal_mismatch_attempt_ids
        )
    )
    contradictory_actions = set(contradictory_action_ids)
    contradictory_attempts = set(contradictory_attempt_ids)
    contradictory_decisions = set(contradictory_decision_ids)
    contradictory_sessions = set(contradictory_session_ids)

    audited_rows = []
    status_counts = Counter()
    affected = defaultdict(set)
    all_audited_rows = []
    for index, row in all_relevant:
        event_name = str(row.get("ev") or "")
        event_id = row.get("event_id")
        message_revision_id = row.get("message_revision_id")
        decision_id = row.get("decision_id")
        action_id = row.get("action_id")
        attempt_id = row.get("attempt_id")
        row_ts = _parse_ts(row.get("ts"))
        reasons = []
        relation_targets = [
            str(target)
            for target in (
                row.get("coalesced_into_action_id"),
                row.get("supersedes_action_id"),
            )
            if target
        ]

        is_legacy = (
            _schema_version(row) < 2
            and (
                activation is None
                or (
                    row_ts is not None
                    and row_ts < activation
                )
            )
        )
        action_related = event_name in (
            ACTION_EVENTS
            | ATTEMPT_EVENTS
            | BROKER_RESULT_EVENTS
            | ACTION_LIFECYCLE_EVENTS
        )
        manifest = (
            _declared_action_manifest(row)
            if event_name in DECISION_ROOT_EVENTS
            else None
        )
        if is_legacy:
            status = "legacy_before_contract"
        elif (
            row_ts is None
            or (
                _schema_version(row) >= 2
                and _parse_contract_utc_ts(row.get("ts")) is None
            )
        ):
            status = "missing_timestamp"
        elif not _has_complete_envelope(row):
            status = "missing_envelope"
        elif event_id in duplicate_event_ids:
            status = "duplicate_id"
        elif _contains_non_finite(row):
            status = "invalid_numeric_evidence"
        elif row.get("payload_sha256") != _semantic_payload_sha256(row):
            status = "payload_hash_mismatch"
        elif str(row.get("session_id")) in contradictory_sessions:
            status = "contradictory_link"
        elif (
            str(decision_id) in contradictory_decisions
            or
            str(action_id) in contradictory_actions
            or str(attempt_id) in contradictory_attempts
        ):
            status = "contradictory_link"
        elif (
            action_related
            and not _valid_runtime_id(action_id, "action_")
        ):
            status = "missing_action"
        elif (
            event_name in ACTION_ROOT_EVENTS
            and not _has_action_root_evidence(row)
        ):
            status = "missing_execution_evidence"
        elif (
            event_name in ATTEMPT_EVENTS
            and str(action_id) not in action_roots
        ):
            status = "orphan_attempt"
        elif (
            event_name in (
                BROKER_RESULT_EVENTS
                | ACTION_LIFECYCLE_EVENTS
                | ACTION_RELATION_EVENTS
            )
            and str(action_id) not in action_roots
        ):
            status = "missing_action"
        elif (
            event_name in (ATTEMPT_EVENTS | BROKER_RESULT_EVENTS)
            and not _valid_runtime_id(attempt_id, "attempt_")
        ):
            status = "missing_attempt"
        elif (
            event_name in (
                TELEGRAM_RAW_EVENTS
                | DECISION_START_EVENTS
                | DECISION_ROOT_EVENTS
            )
            or action_related
        ) and not message_revision_id:
            status = "missing_message_revision"
        elif (
            event_name in DECISION_ROOT_EVENTS
            or event_name in DECISION_START_EVENTS
            or action_related
        ) and not _valid_runtime_id(decision_id, "decision_"):
            status = "missing_decision"
        elif (
            event_name in DECISION_START_EVENTS
            and str(message_revision_id) not in raw_message_revisions
        ):
            status = "missing_message_revision"
        elif (
            event_name in TELEGRAM_RAW_EVENTS
            and not _has_raw_message_evidence(row)
        ):
            status = "invalid_message_evidence"
        elif (
            event_name in TELEGRAM_RAW_EVENTS
            and row.get("has_media") is True
            and not _valid_sha256(row.get("media_sha256"))
            and not stored_media_rows.get(str(message_revision_id))
        ):
            status = "missing_media_evidence"
        elif (
            event_name in TELEGRAM_MEDIA_EVIDENCE_EVENTS
            and not _has_stored_media_evidence(row)
        ):
            status = "missing_media_evidence"
        elif (
            event_name in TELEGRAM_RAW_EVENTS
            and str(message_revision_id) not in processed_message_revisions
        ):
            status = "missing_decision"
        elif (
            event_name in DECISION_ROOT_EVENTS
            and not decision_start_rows.get(str(decision_id))
        ):
            status = "missing_decision_start"
        elif (
            event_name in DECISION_ROOT_EVENTS
            and manifest is None
        ):
            status = "missing_action_manifest"
        elif (
            event_name in DECISION_ROOT_EVENTS
            and str(message_revision_id) not in raw_message_revisions
        ):
            status = "missing_message_revision"
        elif (
            event_name in TELEGRAM_FAILED_DECISION_EVENTS
            and (
                not isinstance(row.get("exception_type"), str)
                or not row.get("exception_type")
                or not isinstance(row.get("exception_message"), str)
            )
        ):
            status = "missing_decision_evidence"
        elif (
            event_name in INTERNAL_DECISION_START_EVENTS
            and (
                not isinstance(row.get("decision_reason"), str)
                or not row.get("decision_reason")
            )
        ):
            status = "missing_decision_evidence"
        elif (
            event_name in INTERNAL_DECISION_START_EVENTS
            and (
                not row.get("parent_decision_id")
                or str(row.get("parent_decision_id"))
                not in decision_root_ids
            )
        ):
            status = "missing_decision"
        elif (
            event_name in INTERNAL_DECISION_START_EVENTS
            and str(message_revision_id) not in (
                decision_message_revisions[
                    str(row.get("parent_decision_id"))
                ]
            )
        ):
            status = "contradictory_link"
        elif (
            event_name in INTERNAL_DECISION_EVENTS
            and (
                not isinstance(row.get("decision_reason"), str)
                or not row.get("decision_reason")
            )
        ):
            status = "missing_decision_evidence"
        elif (
            event_name in INTERNAL_DECISION_EVENTS
            and (
                not row.get("parent_decision_id")
                or str(row.get("parent_decision_id"))
                not in decision_root_ids
            )
        ):
            status = "missing_decision"
        elif (
            event_name in INTERNAL_DECISION_EVENTS
            and str(message_revision_id) not in (
                decision_message_revisions[
                    str(row.get("parent_decision_id"))
                ]
            )
        ):
            status = "contradictory_link"
        elif (
            event_name in DECISION_ROOT_EVENTS
            and any(
                declared_action_id not in action_roots
                for declared_action_id in (manifest or [])
            )
        ):
            status = "missing_action"
        elif (
            action_related
            and str(message_revision_id) not in raw_message_revisions
        ):
            status = "missing_message_revision"
        elif (
            action_related
            and (
                str(message_revision_id),
                str(decision_id),
            ) not in decision_roots
        ):
            status = "missing_decision"
        elif (
            event_name in ACTION_RELATION_EVENTS
            and not relation_targets
        ):
            status = "missing_action"
        elif (
            event_name in ACTION_RELATION_EVENTS
            and (
                len(relation_targets) != 1
                or relation_targets[0] == str(action_id)
            )
        ):
            status = "contradictory_link"
        elif (
            event_name in ACTION_RELATION_EVENTS
            and relation_targets[0] not in action_roots
        ):
            status = "missing_action"
        elif (
            action_related
            and str(action_id) not in (
                actions_declared_by_decision[str(decision_id)]
            )
        ):
            status = "missing_action_manifest"
        elif (
            event_name in ACTION_ROOT_EVENTS
            and attempt_id
            and str(attempt_id) not in attempt_ids
        ):
            status = "missing_attempt"
        elif (
            event_name in ACTION_ROOT_EVENTS
            and not action_attempts[str(action_id)]
            and str(action_id) not in action_terminals
        ):
            status = "missing_execution_evidence"
        elif (
            event_name in BROKER_RESULT_EVENTS
            and str(attempt_id) not in attempt_ids
        ):
            status = "missing_attempt"
        elif (
            event_name in BROKER_RESULT_EVENTS
            and not _broker_result_matches_attempt(
                row,
                attempt_evidence_rows[str(attempt_id)][0],
            )
        ):
            status = "contradictory_result"
        elif (
            event_name in ATTEMPT_EVENTS
            and not _has_attempt_evidence(row)
        ):
            status = "missing_execution_evidence"
        elif (
            event_name in ACTION_TERMINAL_EVENTS
            and str(action_id) in invalid_terminal_action_ids
        ):
            status = "missing_execution_evidence"
        else:
            status = "complete"

        if status != "complete":
            reasons.append(status)
        all_audited_rows.append({
            "row_index": index,
            "event_id": event_id,
            "ts": row.get("ts"),
            "sig": row.get("sig"),
            "ev": event_name,
            "message_revision_id": row.get("message_revision_id"),
            "decision_id": row.get("decision_id"),
            "action_id": action_id,
            "attempt_id": attempt_id,
            "coalesced_into_action_id": row.get(
                "coalesced_into_action_id"
            ),
            "supersedes_action_id": row.get("supersedes_action_id"),
            "status": status,
            "reasons": reasons,
        })

    source_rows_by_index = {
        index: row for index, row in all_relevant
    }
    blocked_dependency_keys = set()
    for audited in all_audited_rows:
        if audited["status"] != "complete":
            blocked_dependency_keys.update(
                _lineage_dependency_keys(
                    source_rows_by_index[audited["row_index"]]
                )
            )
    changed = True
    while changed:
        changed = False
        for audited in all_audited_rows:
            if audited["status"] != "complete":
                continue
            source_row = source_rows_by_index[audited["row_index"]]
            keys = _lineage_dependency_keys(source_row)
            if keys.isdisjoint(blocked_dependency_keys):
                continue
            audited["status"] = "invalid_dependency"
            audited["reasons"] = ["invalid_dependency"]
            blocked_dependency_keys.update(keys)
            changed = True

    selected_relevant_indexes = {index for index, _ in relevant}
    audited_rows = [
        audited
        for audited in all_audited_rows
        if audited["row_index"] in selected_relevant_indexes
    ]
    status_counts = Counter(
        audited["status"] for audited in audited_rows
    )
    affected = defaultdict(set)
    for audited in audited_rows:
        if audited["status"] != "complete":
            affected[audited["status"]].add(
                str(audited.get("sig") or "unknown")
            )

    timestamps = [
        _parse_ts(row.get("ts"))
        for _, row in selected
        if _parse_ts(row.get("ts")) is not None
    ]
    empty_selection = int(not audited_rows)
    blocked = len(normalized_source_errors) + empty_selection + sum(
        count
        for status, count in status_counts.items()
        if status != "complete"
    )
    summary = {
        status: status_counts.get(status, 0)
        for status in (
            "complete",
            "legacy_before_contract",
            "missing_timestamp",
            "missing_envelope",
            "payload_hash_mismatch",
            "missing_message_revision",
            "missing_decision",
            "missing_decision_start",
            "missing_action",
            "missing_action_manifest",
            "missing_decision_evidence",
            "missing_attempt",
            "missing_execution_evidence",
            "missing_media_evidence",
            "invalid_message_evidence",
            "invalid_numeric_evidence",
            "invalid_dependency",
            "orphan_attempt",
            "duplicate_id",
            "contradictory_link",
            "contradictory_result",
            "invalid_source_line",
            "empty_selection",
        )
    }
    summary["invalid_source_line"] = len(normalized_source_errors)
    summary["empty_selection"] = empty_selection
    summary["blocked"] = blocked

    report = {
        "schema_version": 1,
        "source": {
            "path": source_path,
            "sha256": source_sha256,
        },
        "selection": {
            "since": since,
            "until": until,
            "selected_rows": len(selected),
            "relevant_rows": len(relevant),
            "source_lines": (
                int(source_line_count)
                if source_line_count is not None
                else len(all_rows) + len(normalized_source_errors)
            ),
            "parsed_rows": len(all_rows),
            "first_ts": min(timestamps).isoformat() if timestamps else None,
            "last_ts": max(timestamps).isoformat() if timestamps else None,
        },
        "source_integrity": {
            "invalid_lines": normalized_source_errors,
        },
        "contract": {
            "inferred_activation_utc": (
                activation.isoformat() if activation else None
            ),
            "inference": (
                "first schema_version>=2 event with event_id and session_id"
            ),
        },
        "summary": summary,
        "relations": {
            "duplicate_event_ids": sorted(duplicate_event_ids),
            "contradictory_session_ids": contradictory_session_ids,
            "contradictory_message_revision_ids": (
                contradictory_message_revision_ids
            ),
            "telegram_identity_mismatch_decision_ids": sorted(
                telegram_identity_mismatch_decisions
            ),
            "action_intent_mismatch_ids": sorted(
                action_intent_mismatch_ids
            ),
            "ticket_owner_mismatch_action_ids": sorted(
                ticket_owner_mismatch_action_ids
            ),
            "coalescence_mismatch_action_ids": sorted(
                coalescence_mismatch_action_ids
            ),
            "cyclic_action_relation_ids": sorted(
                cyclic_action_relation_ids
            ),
            "attempt_action_mismatch_ids": sorted(
                attempt_action_mismatch_ids
            ),
            "broker_operation_mismatch_attempt_ids": sorted(
                broker_operation_mismatch_attempt_ids
            ),
            "temporal_mismatch_decision_ids": sorted(
                temporal_mismatch_decision_ids
            ),
            "parent_mismatch_decision_ids": sorted(
                parent_mismatch_decision_ids
            ),
            "cyclic_decision_ids": sorted(cyclic_decision_ids),
            "temporal_mismatch_action_ids": sorted(
                temporal_mismatch_action_ids
            ),
            "temporal_mismatch_attempt_ids": sorted(
                temporal_mismatch_attempt_ids
            ),
            "contradictory_decision_ids": contradictory_decision_ids,
            "contradictory_action_ids": contradictory_action_ids,
            "contradictory_attempt_ids": contradictory_attempt_ids,
        },
        "affected_signals": {
            status: sorted(signals)
            for status, signals in sorted(affected.items())
        },
        "rows": audited_rows,
    }
    fingerprint_payload = {
        **report,
        "source": {
            "sha256": report["source"]["sha256"],
        },
    }
    report["fingerprint"] = _canonical_fingerprint(fingerprint_payload)
    return report


def _read_jsonl(path: Path) -> tuple[list[dict], list[dict], int]:
    rows = []
    errors = []
    raw_lines = path.read_bytes().splitlines()
    for line_number, raw_line in enumerate(raw_lines, start=1):
        if not raw_line.strip():
            continue
        try:
            stripped = raw_line.decode("utf-8").strip()
        except UnicodeDecodeError:
            errors.append({
                "line_number": line_number,
                "reason": "invalid_utf8",
            })
            continue
        try:
            value = json.loads(
                stripped,
                parse_constant=_reject_non_finite_json_constant,
            )
        except (json.JSONDecodeError, ValueError):
            errors.append({
                "line_number": line_number,
                "reason": "invalid_json",
            })
            continue
        if not isinstance(value, dict):
            errors.append({
                "line_number": line_number,
                "reason": "non_object_json",
            })
            continue
        rows.append(value)
    return rows, errors, len(raw_lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit causal Telegram-to-MT5 lineage.",
    )
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/causal_lineage_audit.json"),
    )
    parser.add_argument("--since")
    parser.add_argument("--until")
    return parser


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    source_bytes = args.events.read_bytes()
    rows, source_errors, source_line_count = _read_jsonl(args.events)
    report = audit_rows(
        rows,
        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
        source_path=str(args.events.resolve()),
        since=args.since,
        until=args.until,
        source_errors=source_errors,
        source_line_count=source_line_count,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    print(
        "Causal lineage audit: "
        f"{report['selection']['relevant_rows']} relevant rows, "
        f"{report['summary']['complete']} complete, "
        f"{report['summary']['blocked']} blocked"
    )
    print(f"Output: {args.output.resolve()}")
    return 0 if report["summary"]["blocked"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
