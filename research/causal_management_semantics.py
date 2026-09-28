"""Causal-stream grammar extensions without changing archived catalog semantics."""

import re

from provider_signal_catalog import _deterministic_management_semantics


_NARRATIVE_IF = re.compile(r"\b(?:AND\s+)?LET['\u2019]?S\s+SEE\s+IF\b", re.I)
_LATER_ACTION = re.compile(
    r"\b(?:CLOSE|EXIT|MOVE\s+(?:SL|STOP)|SL\s+TO|BE|"
    r"STOP\s+LOSS|TAKE\s+PROFIT|TP\s*\d+)\b", re.I)
_CLOSE_NOW = re.compile(r"\bCLOSE\s+(?:(?:THIS|IT)\s+NOW|NOW)\b", re.I)
_OPTIONAL = re.compile(
    r"\b(?:IF\s+YOU\s+(?:DON'?T|DO\s+NOT)\s+WANT|"
    r"IF\s+YOU\s+(?:WANT|WISH)|FEEL\s+FREE|OPTIONAL(?:LY)?)\b", re.I)
_CONDITION = re.compile(r"\b(?:IF|ONCE|UNLESS|WHEN)\b", re.I)


def causal_management_semantics(text: str) -> dict | None:
    """Preserve direct instructions followed by speculative market commentary."""
    narrative = _NARRATIVE_IF.search(text)
    instruction = (text[:narrative.start()] if narrative is not None
                   and not _LATER_ACTION.search(text[narrative.end():]) else text)
    known = _deterministic_management_semantics(instruction)
    if known is not None:
        return known
    if not _CLOSE_NOW.search(instruction):
        return None
    modality = ("optional" if _OPTIONAL.search(instruction) else
                "conditional" if _CONDITION.search(instruction) else "direct")
    action = {"action": "CLOSE_ALL"}
    return {**action, "modality": modality,
            "execution_options": ([action, {"action": "HOLD"}] if modality == "optional"
                                  else [action, {"action": "WAIT_FOR_CONDITION"}]
                                  if modality == "conditional" else [action])}
