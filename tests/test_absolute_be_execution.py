from dataclasses import replace
from datetime import timedelta

import numpy as np
import pytest

from research.dubai_iterative.dataset import ProviderEvent
from tests.test_absolute_level_execution import execution, parity, policy
from tests.test_iterative_protection import path, profile


def assumptions(**changes):
    return execution(protection=profile(policy_extension="absolute_levels_be_v1",
        request_quote_binding="timestamp_and_ordinal", **changes))


def provider(tape, at, modality="direct"):
    return replace(tape, provider_events=(ProviderEvent(tape.signal_observed_at + timedelta(seconds=at),
        "MOVE_SL_TO_BE", {"modality": modality, "source_row_sha256": "a" * 64}),))


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("mode", ["price", "provider"])
def test_absolute_be_is_active_only_after_installation(direction, mode):
    q = np.array([100.,100.,103.,103.,99.,99.])
    tape = provider(path(q if direction == "BUY" else 200-q, direction=direction), 2)
    result = parity(tape, policy(direction, be_mode=mode, be_trigger=2 if mode == "price" else None), assumptions())
    assert not result.blockers
    assert result.exits[0].tick_index == 4 and result.exits[0].reason == "break_even"
    assert next(e.tick_index for e in result.protection_events if e.kind == "installed") == 3


def test_old_absolute_profile_does_not_silently_enable_be():
    result = parity(path([100.,100.,103.,106.,106.]), policy(be_mode="price", be_trigger=2), execution())
    assert "absolute_level_policy_contract" in result.blockers


def test_original_stop_wins_while_be_request_is_pending():
    tape = provider(path([100.,100.,103.,89.,89.]), 2)
    result = parity(tape, policy(be_mode="provider"), assumptions(processing_delay_ms=10_000))
    assert result.exits[0].reason == "initial_sl"
    assert not any(e.kind == "installed" for e in result.protection_events)


@pytest.mark.parametrize("at,modality", [(-1,"direct"), (5,"direct"), (2,"conditional"), (2,"optional")])
def test_stale_late_and_non_direct_announcements_do_not_arm_early_be(at, modality):
    tape = provider(path([100.,100.,103.,103.,89.,89.]), at, modality)
    result = parity(tape, policy(be_mode="provider"), assumptions())
    assert result.exits[0].reason == "initial_sl"


def test_invalid_be_request_keeps_initial_stop_then_retries():
    tape = provider(path([100.,100.,99.,99.,99.,102.,102.,102.,99.,99.]), 2)
    result = parity(tape, policy(be_mode="provider"), assumptions())
    assert not result.blockers
    assert any(e.kind == "rejected" for e in result.protection_events)
    assert any(e.kind == "installed" for e in result.protection_events)
    assert result.exits[0].reason == "break_even"


@pytest.mark.parametrize("seed", range(8))
def test_absolute_be_lifecycle_randomized_parity(seed):
    rng = np.random.default_rng(seed + 80)
    q = np.round(100 + np.cumsum(rng.normal(0, 1.1, 90)), 2)
    tape = provider(path(q), 4 + seed)
    parity(tape, policy(be_mode="price" if seed % 2 else "provider", be_trigger=1.5 if seed % 2 else None), assumptions())
