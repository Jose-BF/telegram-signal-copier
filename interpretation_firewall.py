"""Normalize and gate Telegram management interpretations before MT5.

Gemini may describe message intent with richer labels than the legacy
classifier actions. This module keeps the external contract small:
normalize every classifier output to a list of action dicts, then decide
whether each action may execute automatically, only be logged, notify for
review, or be rejected.
"""

from dataclasses import dataclass
from math import isfinite
import re
from typing import Any


EXECUTABLE_ACTIONS = {
    "CLOSE_ALL",
    "CLOSE_PROFIT_OR_BE",
    "CLOSE_FIRST",
    "CLOSE_AT_TP",
    "SECURE_BASKET",
    "MOVE_SL_TO_BE",
    "MOVE_SL_TO_PRICE",
}

NOTIFY_REVIEW_ACTIONS = {
    "REENTRY_SIGNAL",
    "ENTRY_UPDATE",
    "SIGNAL_RETRACTED",
    "AMBIGUOUS",
    "UNKNOWN",
    "PROTECT_AND_NOTIFY",
    "SIGNAL_UPDATED",
}

LOG_ONLY_ACTIONS = {
    "INFORMATIONAL",
    "TP_HIT_ANNOUNCEMENT",
    "SL_HIT_ANNOUNCEMENT",
    "BE_ANNOUNCEMENT",
    "PROGRESS_UPDATE",
    "CONDITIONAL_PLAN",
    "OPTIONAL_SUGGESTION",
    "DAILY_SUMMARY",
    "WEEKLY_SUMMARY",
    "MARKET_COMMENTARY",
    "MEDIA_COMPANION",
    "HIGH_RISK_WARNING",
    "CLOSE_PARTIAL",
}

# These are not direct MT5 actions. They should be handled by parser/level
# update paths when concrete levels are present, not executed as generic
# management commands.
LEVEL_ONLY_ACTIONS = {
    "LEVEL_UPDATE",
    "LEVEL_CORRECTION",
    "NEW_SIGNAL_CANDIDATE",
}

KNOWN_ACTIONS = (
    EXECUTABLE_ACTIONS
    | NOTIFY_REVIEW_ACTIONS
    | LOG_ONLY_ACTIONS
    | LEVEL_ONLY_ACTIONS
)

ROLE_DEFAULT_ACTION = {
    "conditional_plan": "CONDITIONAL_PLAN",
    "optional_suggestion": "OPTIONAL_SUGGESTION",
    "daily_summary": "DAILY_SUMMARY",
    "weekly_summary": "WEEKLY_SUMMARY",
    "progress_update": "PROGRESS_UPDATE",
    "market_commentary": "MARKET_COMMENTARY",
    "media_companion": "MEDIA_COMPANION",
    "unknown": "UNKNOWN",
}


_PROVIDER_BE_PRICE_RE = re.compile(
    r"(?:\bB\s*/\s*E\b|\bBE\b|\bBREAK\s*EVEN\b|\bBREAKEVEN\b|\bENTRY\b)"
    r"\s*(?:(?:AT|AROUND|NEAR|ABOUT)\s*)?[*_`~]*"
    r"(\d{3,5}(?:\.\d{1,3})?)\b",
    re.IGNORECASE,
)


def extract_provider_stated_be_price(text: str | None) -> float | None:
    """Extract the provider's stated entry near a BE instruction as evidence."""
    match = _PROVIDER_BE_PRICE_RE.search(str(text or ""))
    return float(match.group(1)) if match else None


def normalize_xauusd_management_price(price, reference) -> float | None:
    """Expand a provider shorthand SL against an absolute XAUUSD reference."""
    if isinstance(price, bool) or isinstance(reference, bool):
        return None
    try:
        price_f = float(price)
    except (TypeError, ValueError):
        return None
    if not isfinite(price_f):
        return None
    try:
        reference_f = float(reference)
    except (TypeError, ValueError):
        return price_f if 1000 <= price_f <= 9999 else None
    if not isfinite(reference_f) or not 1000 <= reference_f <= 9999:
        return price_f if 1000 <= price_f <= 9999 else None

    if 1000 <= price_f <= 9999:
        # Providers occasionally drop the hundreds digit while editing a
        # live XAUUSD level (for example 4050 while price is near 4346). Only
        # repair a gross displacement when the local-hundreds candidate is
        # both close to the live reference and at least $100 more plausible.
        direct_distance = abs(price_f - reference_f)
        local_candidate = (
            int(reference_f / 100) * 100 + (price_f % 100)
        )
        candidate_distance = abs(local_candidate - reference_f)
        if (
            direct_distance >= 150
            and candidate_distance <= 50
            and direct_distance - candidate_distance >= 100
        ):
            return round(local_candidate, 3)
        return price_f

    if not 0 <= price_f < 100:
        return None

    base = int(reference_f / 100) * 100
    normalized = base + price_f
    if abs(normalized - reference_f) > 50:
        normalized += 100 if normalized < reference_f else -100
    return round(normalized, 3)


@dataclass(frozen=True)
class FirewallDecision:
    policy: str
    will_execute: bool
    reason: str
    requires_review: bool = False


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _normalize_action_type(value: Any) -> str:
    raw = str(value or "").strip().upper()
    if not raw:
        return "UNKNOWN"
    aliases = {
        "PARTIAL_CLOSE": "CLOSE_PARTIAL",
        "MOVE_SL_BREAKEVEN": "MOVE_SL_TO_BE",
        "MOVE_SL_ENTRY": "MOVE_SL_TO_BE",
        "SL_TO_BE": "MOVE_SL_TO_BE",
        "SL_TO_PRICE": "MOVE_SL_TO_PRICE",
        "TP_HIT": "TP_HIT_ANNOUNCEMENT",
        "SL_HIT": "SL_HIT_ANNOUNCEMENT",
        "BE_HIT": "BE_ANNOUNCEMENT",
        "SUMMARY_DAILY": "DAILY_SUMMARY",
        "SUMMARY_WEEKLY": "WEEKLY_SUMMARY",
    }
    return aliases.get(raw, raw)


def _role_default(role: Any) -> str | None:
    key = str(role or "").strip().lower()
    return ROLE_DEFAULT_ACTION.get(key)


def _normalize_one_action(action: dict, envelope: dict | None = None) -> dict:
    envelope = envelope or {}
    action_type = _normalize_action_type(
        action.get("action") or action.get("type") or action.get("intent")
    )
    confidence = action.get("confidence", envelope.get("confidence"))
    if confidence is None:
        confidence = 1.0 if action_type in LOG_ONLY_ACTIONS else 0.0

    price = action.get("price")
    provider_stated_be_price = action.get(
        "provider_stated_be_price",
        envelope.get("provider_stated_be_price"),
    )
    if action_type == "MOVE_SL_TO_BE":
        if provider_stated_be_price is None and price is not None:
            provider_stated_be_price = price
        price = None

    normalized = {
        "action": action_type,
        "price": price,
        "confidence": float(confidence),
        "message_role": envelope.get("message_role") or action.get("message_role"),
        "execution_policy": (
            action.get("execution_policy") or envelope.get("execution_policy")
        ),
        "is_conditional": bool(
            action.get("is_conditional", envelope.get("is_conditional", False))
        ),
        "is_optional": bool(
            action.get("is_optional", envelope.get("is_optional", False))
        ),
        "requires_review": bool(
            action.get("requires_review", envelope.get("requires_review", False))
        ),
        "evidence": action.get("evidence") or envelope.get("evidence"),
        "reasoning": action.get("reasoning") or envelope.get("reasoning"),
    }
    if provider_stated_be_price is not None:
        try:
            normalized["provider_stated_be_price"] = float(
                provider_stated_be_price
            )
        except (TypeError, ValueError):
            pass
    for key in ("target", "summary", "levels", "field", "_reason",
                "_gemini_failed", "_last_error", "is_plural"):
        if key in action:
            normalized[key] = action[key]
        elif key in envelope:
            normalized[key] = envelope[key]
    return normalized


def normalize_classifier_outputs(raw_output) -> list[dict]:
    """Return a list of legacy-compatible action dicts.

    Accepted inputs:
      - legacy action dict: {"action": "MOVE_SL_TO_BE", ...}
      - legacy action list: [{"action": ...}, ...]
      - rich Gemini contract: {"message_role": ..., "actions": [...]}
    """
    normalized: list[dict] = []
    for item in _as_list(raw_output):
        if not isinstance(item, dict):
            continue

        if "actions" in item and isinstance(item.get("actions"), list):
            envelope = item
            actions = item.get("actions") or []
            if not actions:
                default_action = _role_default(item.get("message_role"))
                if default_action:
                    actions = [{"type": default_action}]
                else:
                    actions = [{"type": "UNKNOWN"}]
            for action in actions:
                if isinstance(action, dict):
                    normalized.append(_normalize_one_action(action, envelope))
            continue

        if "action" in item or "type" in item or "intent" in item:
            normalized.append(_normalize_one_action(item))
            continue

        default_action = _role_default(item.get("message_role"))
        if default_action:
            normalized.append(_normalize_one_action(
                {"type": default_action}, item))

    return normalized


def firewall_decision(signal, classification: dict,
                      raw_text: str = "") -> FirewallDecision:
    action = _normalize_action_type(classification.get("action"))
    text = " ".join((raw_text or "").lower().split())
    policy = str(classification.get("execution_policy") or "").lower()

    if classification.get("_gemini_failed"):
        return FirewallDecision("notify_review", False, "gemini_failed", True)

    if classification.get("is_conditional") or action == "CONDITIONAL_PLAN":
        return FirewallDecision("log_only", False, "conditional_plan", False)

    if classification.get("is_optional") or action == "OPTIONAL_SUGGESTION":
        return FirewallDecision("notify_review", False, "optional_suggestion", True)

    if policy in {"log_only", "reject", "notify_review"}:
        return FirewallDecision(
            policy, False, f"explicit_policy_{policy}",
            policy == "notify_review")

    if action in LEVEL_ONLY_ACTIONS:
        return FirewallDecision("log_only", False, "level_parser_path", False)

    if action in LOG_ONLY_ACTIONS:
        return FirewallDecision("log_only", False, "non_executable_intent", False)

    if action in NOTIFY_REVIEW_ACTIONS:
        return FirewallDecision("notify_review", False, "requires_review_intent", True)

    if action not in EXECUTABLE_ACTIONS:
        return FirewallDecision("notify_review", False, "unknown_action", True)

    if action == "CLOSE_ALL" and _looks_non_holder_scope(text):
        return FirewallDecision("log_only", False, "non_holder_scope", False)

    confidence = float(classification.get("confidence") or 0.0)
    if confidence < 0.5 and not classification.get("_reason"):
        return FirewallDecision("notify_review", False, "low_confidence", True)

    if _looks_optional_close(text) and action.startswith("CLOSE_"):
        return FirewallDecision("notify_review", False, "optional_close_text", True)

    if _looks_conditional(text) and action.startswith("CLOSE_"):
        return FirewallDecision("log_only", False, "conditional_close_text", False)

    return FirewallDecision("auto_execute", True, "direct_executable", False)


def _looks_conditional(text: str) -> bool:
    if not text:
        return False
    return any(marker in text for marker in (
        " if ", "if ", " when ", " once ", " unless ", "watch "
    ))


def _looks_optional_close(text: str) -> bool:
    if not text:
        return False
    optional_markers = (
        "you can close",
        "can close",
        "if you want",
        "if you don't want",
        "if you dont want",
        "feel free to close",
        "for those",
        "anyone who",
        "members who",
    )
    return any(marker in text for marker in optional_markers)


def _looks_non_holder_scope(text: str) -> bool:
    if not text:
        return False
    return any(marker in text for marker in (
        "for who is out",
        "for who are out",
        "for those who are out",
        "for those out",
        "who is out of the trade",
        "who are out of the trade",
        "if you are out of the trade",
        "if you're out of the trade",
    ))
