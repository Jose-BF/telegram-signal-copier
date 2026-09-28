import pytest

from provider_signal_catalog import _deterministic_management_semantics
from research.causal_management_semantics import causal_management_semantics


@pytest.mark.parametrize("text,action,modality", [
    ("Close this now guys", "CLOSE_ALL", "direct"),
    ("Please close it now", "CLOSE_ALL", "direct"),
    ("SL to BE and let's see if we catch the move", "MOVE_SL_TO_BE", "direct"),
    ("Move SL to BE if price reaches 4370", "MOVE_SL_TO_BE", "conditional"),
    ("Back around entry if you don't want risk close now", "CLOSE_ALL", "optional"),
])
def test_causal_grammar_distinguishes_instruction_from_narrative(text, action, modality):
    parsed = causal_management_semantics(text)
    assert parsed["action"] == action
    assert parsed["modality"] == modality


def test_legacy_catalog_semantics_remain_unchanged():
    assert _deterministic_management_semantics("Close this now guys") is None
    assert (_deterministic_management_semantics("SL to BE and let's see if we catch the move")
            ["modality"] == "conditional")


def test_narrative_tail_with_another_action_is_not_silently_removed():
    parsed = causal_management_semantics(
        "SL to BE and let's see if it falls, then close now")
    assert parsed["modality"] == "conditional"
