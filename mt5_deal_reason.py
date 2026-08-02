"""Interpret MT5 deal close reasons without opening another MT5 session."""

from __future__ import annotations


DEAL_REASON_NAMES = {
    0: "manual",
    1: "mobile",
    2: "web",
    3: "expert",
    4: "sl",
    5: "tp",
    6: "stop_out",
    7: "rollover",
    8: "variation_margin",
    9: "split",
}


def close_reason_from_comment(comment: object) -> str:
    cleaned = str(comment or "").strip().lower()
    if cleaned.startswith("[sl"):
        return "sl"
    if cleaned.startswith("[tp"):
        return "tp"
    if cleaned.startswith("[be"):
        return "be"
    if "bot_close" in cleaned:
        return "bot_close"
    return "other"


def close_reason_from_deal(deal) -> str:
    """Return the broker-authoritative reason, with comments as fallback."""
    if deal is None:
        return "other"

    comment_reason = close_reason_from_comment(getattr(deal, "comment", ""))
    raw_reason = getattr(deal, "reason", None)
    try:
        reason = int(raw_reason)
    except (TypeError, ValueError):
        reason = None

    if reason == 5:
        return "tp"
    if reason == 4:
        return "be" if comment_reason == "be" else "sl"
    if comment_reason != "other":
        return comment_reason
    return DEAL_REASON_NAMES.get(reason, "other")
