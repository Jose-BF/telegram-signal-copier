import dataclasses

import pytest

from research.account_sim import Rules
from research.prop_rules import FTMO_2STEP, PRESETS, RULE_FIELDS


def test_every_preset_loads_and_every_field_is_sourced_or_flagged():
    for key, preset in PRESETS.items():
        assert preset.validation_errors() == [], key
        for name in RULE_FIELDS:
            assert (name in preset.sources) != (name in preset.unverified), (key, name)


def test_value_without_source_is_rejected():
    sources = dict(FTMO_2STEP.sources)
    del sources["daily_pct"]
    with pytest.raises(ValueError, match="daily_pct: missing official source"):
        dataclasses.replace(FTMO_2STEP, sources=sources)


def test_non_https_source_is_rejected():
    sources = {**FTMO_2STEP.sources, "max_pct": "somewhere on a forum"}
    with pytest.raises(ValueError, match="max_pct"):
        dataclasses.replace(FTMO_2STEP, sources=sources)


def test_conversion_to_account_rules_keeps_limits_and_bounds_unlimited_time():
    r = FTMO_2STEP.to_account_rules(sim_horizon_days=200)
    assert isinstance(r, Rules)
    assert (r.daily_pct, r.max_pct, r.target1_pct, r.target2_pct, r.min_days, r.max_days) == (5.0, 10.0, 10.0, 5.0, 4, 200)
