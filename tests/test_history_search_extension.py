import json
from pathlib import Path

from research.history_search import execution_for

CAL = json.loads(Path("runtime_data/execution_calibration_v2/C_p50_q450.json").read_text())


def test_default_extensions_unchanged():
    assert execution_for(CAL, "canal1").protection.policy_extension == "basket_guard_v1"
    assert execution_for(CAL, "canal2").protection.policy_extension == "none"


def test_per_channel_override_only_touches_that_channel():
    cal = {**CAL, "policy_extension": {"canal2": "own_rule_be_partial_v1"}}
    assert execution_for(cal, "canal2").protection.policy_extension == "own_rule_be_partial_v1"
    assert execution_for(cal, "canal1").protection.policy_extension == "basket_guard_v1"
    # delays are not affected by the override
    assert execution_for(cal, "canal2").market == execution_for(CAL, "canal2").market
