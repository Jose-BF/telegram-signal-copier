"""Canonical meanings for normalized provider management actions."""

from __future__ import annotations


FULL_CLOSE_ACTIONS = frozenset({"CLOSE_ALL", "EXIT", "CERRAR"})


def is_full_close_action(action: object) -> bool:
    """Return true only for an instruction to close the whole basket now."""

    return str(action or "").strip().upper() in FULL_CLOSE_ACTIONS


def is_strategy_close_action(
    action: object,
    provider_management_mode: str,
) -> bool:
    """Apply the close vocabulary declared by a strategy contract.

    ``explicit_close_only`` is deliberately strict: partial or targeted
    provider actions cannot become a whole-basket close.  The legacy exact and
    close-only modes retain their historical broad close behavior so research
    remains aligned with the deployed Dubai candidate.
    """

    normalized = str(action or "").strip().upper()
    if provider_management_mode == "explicit_close_only":
        return is_full_close_action(normalized)
    if provider_management_mode in {"exact", "close_only"}:
        return "CLOSE" in normalized or normalized in {"EXIT", "CERRAR"}
    return False
