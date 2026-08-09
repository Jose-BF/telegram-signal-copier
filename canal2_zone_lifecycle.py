"""Pure lifecycle rules for Gold Signals zone plans.

This module contains no Telegram or MT5 calls.  It decides whether a provider
plan is complete, which follow-up intent was expressed, and whether a fresh
broker tick touched the executable zone.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any


LIFECYCLE_SCHEMA_VERSION = 2
DEFAULT_VALIDITY_HOURS = 24
_TOUCHABLE_STATUSES = {"armed", "approaching", "rearmed"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def is_executable(plan: dict[str, Any]) -> bool:
    """Return whether a plan has one unambiguous, protected trade setup."""
    direction = str(plan.get("direction") or "").upper()
    zones = plan.get("zones") or []
    tps = plan.get("tps") or []
    sl = plan.get("sl")
    if direction not in {"BUY", "SELL"}:
        return False
    if len(zones) != 1 or len(zones[0]) != 2:
        return False
    try:
        low, high = (float(zones[0][0]), float(zones[0][1]))
        float(sl)
        parsed_tps = [float(value) for value in tps]
    except (TypeError, ValueError):
        return False
    return low <= high and bool(parsed_tps)


def new_plan_record(
    parsed: dict[str, Any],
    *,
    message_id: int,
    root_message_id: int,
    raw_text: str,
    tg_ts: str | None,
    source_kind: str,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Create the durable schema-v2 record for one provider plan thread."""
    now = _as_utc(now_utc) or _utc_now()
    plan = {
        "direction": parsed.get("direction"),
        "zones": [list(zone) for zone in (parsed.get("zones") or [])],
        "target": parsed.get("target"),
        "tps": list(parsed.get("tps") or []),
        "sl": parsed.get("sl"),
        "has_open_runner": bool(parsed.get("has_open_runner")),
        "message_id": int(message_id),
        "thread_root_message_id": int(root_message_id),
        "aliases": [int(message_id)],
        "raw_text": raw_text,
        "tg_ts": tg_ts,
        "source_kind": source_kind,
        "lifecycle_schema_version": LIFECYCLE_SCHEMA_VERSION,
        "status": "draft",
        "registered_utc": _iso_utc(now),
        "updated_utc": _iso_utc(now),
        "expires_utc": _iso_utc(now + timedelta(hours=DEFAULT_VALIDITY_HOURS)),
        "activation_requested": False,
        "no_reentry": False,
        "consumed": False,
        "entry_generation": 0,
        "entry_generation_id": None,
        "trigger_claim": None,
        "confirmed_generation_ids": [],
        "alias_generation_ids": {},
        "last_trigger": {},
        "first_touch_observed": False,
        "first_touch_evidence": {},
    }
    if is_executable(plan):
        plan["status"] = "armed"
    return plan


def merge_plan_record(
    record: dict[str, Any],
    parsed: dict[str, Any],
    *,
    raw_text: str | None = None,
    tg_ts: str | None = None,
    extend_validity_hours: float | None = None,
    now_utc: datetime | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Merge a plan edit without deleting already known provider levels."""
    merged = dict(record)
    changes: list[str] = []

    for key in ("direction", "target", "sl"):
        value = parsed.get(key)
        if value is not None and value != merged.get(key):
            merged[key] = value
            changes.append(key)
    for key in ("zones", "tps"):
        value = parsed.get(key)
        if value and value != merged.get(key):
            merged[key] = [list(item) for item in value] if key == "zones" else list(value)
            changes.append(key)
    if parsed.get("has_open_runner") and not merged.get("has_open_runner"):
        merged["has_open_runner"] = True
        changes.append("has_open_runner")
    if raw_text is not None and raw_text != merged.get("raw_text"):
        merged["raw_text"] = raw_text
        changes.append("raw_text")
    if tg_ts is not None and tg_ts != merged.get("tg_ts"):
        merged["tg_ts"] = tg_ts
        changes.append("tg_ts")

    if extend_validity_hours:
        base = _as_utc(merged.get("expires_utc")) or (_as_utc(now_utc) or _utc_now())
        merged["expires_utc"] = _iso_utc(
            base + timedelta(hours=float(extend_validity_hours))
        )
        changes.append("expires_utc")

    if (
        is_executable(merged)
        and merged.get("status") in {"draft", "activation_pending"}
    ):
        merged["status"] = "armed"
        changes.append("status")

    if changes:
        merged["updated_utc"] = _iso_utc(_as_utc(now_utc) or _utc_now())
    return merged, changes


def classify_followup(text: str) -> list[str]:
    """Classify only explicit zone-lifecycle language, in stable order."""
    normalized = " ".join((text or "").replace("\u2019", "'").split()).lower()
    if not normalized:
        return []

    intents: list[str] = []
    no_reentry = bool(re.search(
        r"\b(?:do\s+not|don't|dont|no)\s+re[- ]?ent(?:er|ry)\b",
        normalized,
    ))
    invalid = bool(re.search(
        r"\b(?:zone\s+)?(?:failed|invalid(?:ated)?|broken)\b|"
        r"\bno\s+longer\s+valid\b",
        normalized,
    ))

    if invalid:
        intents.append("INVALIDATE")
    if no_reentry:
        intents.append("NO_REENTRY")
    if not no_reentry and re.search(
        r"\b(?:i(?:\s+am|'m)?\s+)?re[- ]?enter(?:ing)?\b|\breentry\b",
        normalized,
    ):
        intents.append("REENTRY")
    if re.search(r"\b(?:left|went)\s+without\s+us\b|\bwe\s+missed\b", normalized):
        intents.append("MISSED")
    if re.search(r"\bstill\s+valid\b|\bzone\s+(?:is|remains)\s+valid\b", normalized):
        intents.append("REARM")
    if re.search(r"\bapproach(?:ing|es)?\b|\bgetting\s+close\b", normalized):
        intents.append("APPROACHING")
    if not invalid and re.search(
        r"^(?:the\s+)?(?:buy\s+|sell\s+)?zone\s+is\s+active$|"
        r"^active\b|\byou\s+can\s+enter(?:\s+now)?\b",
        normalized,
    ):
        intents.append("ACTIVATE")
    if re.search(
        r"\b(?:valid|keep|remains?)\b[^.!?]{0,40}\b(?:asia|overnight)\b|"
        r"\b(?:asia|overnight)\b[^.!?]{0,40}\bvalid\b",
        normalized,
    ):
        intents.append("EXTEND_VALIDITY")
    return intents


def touch_decision(plan: dict[str, Any], tick: dict[str, Any]) -> dict[str, Any] | None:
    """Return immutable first-touch evidence for an eligible fresh tick."""
    if (
        plan.get("execution_eligible") is False
        or plan.get("consumed")
        or plan.get("trigger_claim")
    ):
        return None
    if plan.get("status") not in _TOUCHABLE_STATUSES or not is_executable(plan):
        return None
    if is_expired(plan):
        return None

    direction = str(plan["direction"]).upper()
    side = "ask" if direction == "BUY" else "bid"
    try:
        price = float(tick[side])
        low, high = sorted(float(value) for value in plan["zones"][0])
    except (KeyError, TypeError, ValueError):
        return None
    if not low <= price <= high:
        return None
    return {
        "trigger": "first_touch",
        "side": side,
        "price": price,
        "time": tick.get("time"),
        "time_msc": tick.get("time_msc"),
        "zone": [low, high],
    }


def is_expired(plan: dict[str, Any], now_utc: datetime | None = None) -> bool:
    expires = _as_utc(plan.get("expires_utc"))
    if expires is None:
        return False
    now = _as_utc(now_utc) or _utc_now()
    return now >= expires
