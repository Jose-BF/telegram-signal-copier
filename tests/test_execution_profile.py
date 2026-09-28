from copy import deepcopy
from dataclasses import asdict

import pytest

from research.execution_profile import execution_from_mapping, execution_to_scenario, load_execution_profile


def payload():
    return {"latency_ms": 123, "entry_fill_latency_ms": 250,
        "entry_slippage": 0.01, "exit_slippage": 0.02, "spread_addition": 0.03,
        "market": {"entry_acknowledgement_delay_ms": 250, "close_processing_delay_ms": 250,
                   "close_acknowledgement_delay_ms": 500, "volume_min": 0.01,
                   "volume_max": 100, "volume_step": 0.01},
        "protection": {"point": 0.01, "digits": 2, "stops_level_points": 20,
            "freeze_level_points": 0, "processing_delay_ms": 250,
            "acknowledgement_delay_ms": 250, "retry_delay_ms": 1000,
            "initial_protections": []}}


def test_roundtrip_and_oracle_preserve_all_fields_and_nested_objects():
    data = payload()
    original = deepcopy(data)
    execution = execution_from_mapping(data)
    assert data == original
    assert execution_from_mapping(asdict(execution)) == execution
    scenario = execution_to_scenario(execution, "stress")
    assert scenario.market is execution.market
    assert scenario.protection is execution.protection
    engine_only = {"latency": None, "quote_view_lag_ms": 0}
    assert {k: v for k, v in asdict(scenario).items() if k != "name"} == {
        k: v for k, v in asdict(execution).items() if k not in engine_only}
    assert {k: asdict(execution)[k] for k in engine_only} == engine_only
    with pytest.raises(ValueError, match="quote view lag"):
        execution_to_scenario(execution_from_mapping({**data, "quote_view_lag_ms": 400}))


@pytest.mark.parametrize("section", [None, "market", "protection"])
def test_unknown_keys_are_never_silently_ignored(section):
    data = payload()
    (data[section] if section else data)["typo_delay_ms"] = 10
    with pytest.raises(ValueError, match="unknown"):
        execution_from_mapping(data)


@pytest.mark.parametrize("section,key,value", [
    (None, "latency_ms", True), (None, "entry_slippage", "0.01"),
    (None, "entry_fill_latency_ms", -1), ("market", "volume_step", 0),
    ("market", "name", 4), ("protection", "processing_delay_ms", 0.5),
    ("protection", "initial_protections", None), ("protection", "point", float("nan")),
])
def test_invalid_fields_fail_with_value_error(section, key, value):
    data = payload()
    (data[section] if section else data)[key] = value
    with pytest.raises(ValueError):
        execution_from_mapping(data)


def test_market_cannot_implicitly_disable_protection():
    data = payload()
    del data["protection"]
    with pytest.raises(ValueError, match="explicit protection"):
        execution_from_mapping(data)


def test_legacy_profile_remains_explicitly_available():
    execution = execution_from_mapping({"entry_fill_latency_ms": 17})
    assert execution.market is execution.protection is None
    assert execution_to_scenario(execution).entry_fill_latency_ms == 17


def test_initial_protection_records_roundtrip_without_losing_evidence():
    data = payload()
    del data["market"]
    data["protection"]["initial_protections"] = [
        {"ticket": "bound-ticket", "sl": 4300, "tp": None, "source": "declared-initial"}]
    execution = execution_from_mapping(data)
    assert execution.protection.initial_protections[0].source == "declared-initial"
    assert execution_from_mapping(asdict(execution)) == execution


@pytest.mark.parametrize("text", ['{"latency_ms":0,"latency_ms":1}', '{"entry_slippage":NaN}',
                                   '[1,2]', '{"market":{"volume_min":0.01,"volume_min":1}}'])
def test_profile_file_rejects_ambiguous_or_non_json_values(tmp_path, text):
    path = tmp_path / "profile.json"
    path.write_text(text)
    with pytest.raises(ValueError):
        load_execution_profile(path)
