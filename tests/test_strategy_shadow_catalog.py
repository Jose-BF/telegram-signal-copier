from __future__ import annotations

from dataclasses import replace

import pytest

from strategy_shadow_catalog import build_shadow_catalog, validate_shadow_catalog
from strategy_shadow_contracts import (
    ShadowPolicy,
    ShadowSignalState,
    ShadowTick,
)


DUBAI_IDS = (
    "dubai_balanced_v1",
    "dubai_frontloaded_30m_v1",
    "dubai_frontloaded_40m_v1",
)
GOLD_IDS = (
    "gold_now_555_v1",
    "gold_now_b210_v1",
    "gold_now_c490_v1",
)


def test_catalog_contains_three_frozen_candidates_per_channel():
    catalog = build_shadow_catalog()

    assert tuple(item.candidate_id for item in catalog["canal1"]) == DUBAI_IDS
    assert tuple(item.candidate_id for item in catalog["canal2"]) == GOLD_IDS
    assert catalog["canal1"][0].role == "live_control"
    assert catalog["canal2"][0].role == "live_control"


def test_catalog_matches_approved_strategy_fingerprints_and_parameters():
    catalog = build_shadow_catalog()
    policies = {
        policy.candidate_id: policy
        for channel in catalog.values()
        for policy in channel
    }

    assert policies["dubai_balanced_v1"].strategy_fingerprint == (
        "32cb5c0fe8205ad00a0c655bacd5446c6cc219d1ad7338967212c71781860631"
    )
    assert policies["dubai_frontloaded_30m_v1"].entry_volumes == (
        0.01, 0.05, 0.01, 0.02, 0.01, 0.02,
    )
    assert policies["dubai_frontloaded_40m_v1"].time_exit_minutes == 40
    assert policies["gold_now_555_v1"].entry_mode == "adverse_reversal"
    assert policies["gold_now_b210_v1"].basket_stop_eur == 60.0
    assert policies["gold_now_c490_v1"].hard_stop_eur_per_leg == 20.0


def test_execution_fingerprint_changes_with_live_only_contract():
    policy = build_shadow_catalog()["canal2"][2]

    changed = replace(policy, hard_stop_eur_per_leg=21.0)

    assert changed.execution_fingerprint != policy.execution_fingerprint
    assert changed.strategy_fingerprint == policy.strategy_fingerprint


def test_catalog_rejects_missing_live_control():
    catalog = build_shadow_catalog()
    invalid = dict(catalog)
    invalid["canal1"] = tuple(
        replace(policy, role="candidate")
        for policy in invalid["canal1"]
    )

    with pytest.raises(ValueError, match="one live control"):
        validate_shadow_catalog(invalid)


def test_policy_rejects_non_positive_volume():
    policy = build_shadow_catalog()["canal1"][0]

    with pytest.raises(ValueError, match="entry volumes"):
        replace(policy, entry_volumes=(0.01, 0.0, 0.04))


def test_state_round_trip_preserves_canonical_hash():
    state = ShadowSignalState.new(
        signal_id="canal1_20700",
        source_message_id=20700,
        candidate_id="dubai_balanced_v1",
        channel="canal1",
        direction="BUY",
        registered_at_utc="2026-08-27T08:00:00+00:00",
        registered_tick_msc=100,
    )

    restored = ShadowSignalState.from_dict(state.to_dict())

    assert restored == state
    assert restored.state_hash == state.state_hash


def test_tick_uses_broker_executable_quote_side():
    tick = ShadowTick(
        time_msc=100,
        bid=4300.0,
        ask=4300.2,
        observed_at_utc="2026-08-27T08:00:00+00:00",
        positive_eur_per_move_lot=100.0,
        negative_eur_per_move_lot=100.0,
        money_evidence_id="money-1",
    )

    assert tick.executable_price("BUY", entry=True) == 4300.2
    assert tick.executable_price("BUY", entry=False) == 4300.0
    assert tick.executable_price("SELL", entry=True) == 4300.0
    assert tick.executable_price("SELL", entry=False) == 4300.2


def test_shadow_policy_type_is_immutable():
    policy: ShadowPolicy = build_shadow_catalog()["canal1"][0]

    with pytest.raises(Exception):
        policy.candidate_id = "changed"
