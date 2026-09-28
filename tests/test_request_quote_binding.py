from dataclasses import asdict, replace

import numpy as np
import pytest

from research.execution_profile import execution_from_mapping
from tests.test_own_rule_protection_extension import compare, execution, partial, profile, strategy, tape
from research.dubai_iterative.market_contract import MarketProfile


def ordinal_execution(**changes):
    return execution(protection=profile(policy_extension="own_rule_be_partial_v1",
        request_quote_binding="timestamp_and_ordinal"), **changes)


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("second", [False, True])
def test_default_binding_keeps_ambiguity_barrier_in_all_three_engines(direction, second):
    ticks = tape([100, 101, 102, 103, 103, 103], direction, offsets=[0, 0, 1, 2, 3, 4])
    changes = {"entry_mode": "momentum", "entry_value": .5} if second else {}
    result = compare(ticks, strategy(**changes), execution(entry_fill_latency_ms=250))
    assert result.entries == ()
    assert result.blockers == ("protection_request_quote_ambiguous:sim_1",)
    assert result.market_events[0].tick_index == int(second)


def test_binding_is_explicit_roundtripped_and_unknown_values_are_rejected():
    payload = asdict(execution())
    payload["protection"]["request_quote_binding"] = "timestamp_and_ordinal"
    bound = execution_from_mapping(payload)
    assert bound.protection.request_quote_binding == "timestamp_and_ordinal"
    assert execution().protection.request_quote_binding == "timestamp_only"
    payload["protection"]["request_quote_binding"] = "pick_a_price"
    with pytest.raises(ValueError):
        execution_from_mapping(payload)


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("second", [False, True])
@pytest.mark.parametrize("fill_ms", [0, 250, 1000])
def test_explicit_ordinal_uses_the_requested_event_not_another_same_ms_price(direction, second, fill_ms):
    ticks = tape([100, 101, 102, 102, 89, 89, 89], direction, offsets=[0, 0, 1, 2, 3, 4, 5])
    changes = {"entry_mode": "momentum", "entry_value": .5} if second else {}
    result = compare(ticks, strategy(**changes), ordinal_execution(entry_fill_latency_ms=fill_ms))
    assert not result.blockers
    request = result.market_events[0]
    assert request.tick_index == int(second)
    assert request.price == (100 + int(second) if direction == "BUY" else 100 - int(second))
    initial = next(e for e in result.protection_events if e.kind == "open")
    assert initial.sl == request.price + (-10 if direction == "BUY" else 10)
    assert len(result.entries) == len(result.exits) == 1


@pytest.mark.parametrize("fill_ms", [0, 1000])
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_partial_be_and_identical_repeated_timestamps_keep_order_and_volume(direction, fill_ms):
    ticks = tape([100, 100, 101, 102, 103, 104, 104, 104, 104, 104], direction,
                 offsets=[0, 0, 1, 2, 3, 4, 5, 6, 7, 8])
    result = compare(ticks, partial(be_mode="price", be_trigger=2.), ordinal_execution(entry_fill_latency_ms=fill_ms))
    assert not result.blockers
    assert [e.reason for e in result.exits] == ["partial_target", "runner_target"]
    assert sum(e.volume for e in result.exits) == result.filled_volume


@pytest.mark.parametrize("binding", ["timestamp_only", "timestamp_and_ordinal"])
def test_later_ladder_request_retains_its_actual_quote_ordinal(binding):
    ticks = tape([100, 100, 99, 98, 98, 98, 80, 80, 80], offsets=[0, 1, 2, 2, 3, 4, 5, 6, 7])
    assumptions = execution(protection=profile(policy_extension="own_rule_be_partial_v1", request_quote_binding=binding),
        market=MarketProfile(0, 1000, 1000, .01, 1., .01), entry_fill_latency_ms=1000)
    genome = strategy(leg_count=2, volume_weights=(.04, .04), entry_ladder_mode="adverse", entry_ladder_step=1.5)
    result = compare(ticks, genome, assumptions)
    requests = [e for e in result.market_events if e.kind == "entry_requested"]
    assert requests[1].tick_index == 3 and requests[1].price == 98
    if binding == "timestamp_only":
        assert "protection_request_quote_ambiguous:sim_ladder_2" in result.blockers
    else:
        assert not result.blockers and len(result.entries) == 2


@pytest.mark.parametrize("binding", ["timestamp_only", "timestamp_and_ordinal"])
def test_ordinal_binding_does_not_invent_a_missing_fill(binding):
    assumptions = execution(protection=profile(policy_extension="own_rule_be_partial_v1", request_quote_binding=binding),
                            entry_fill_latency_ms=1000)
    result = compare(tape([100, 101], offsets=[0, 0]), strategy(entry_mode="momentum", entry_value=.5),
                     assumptions)
    assert result.entries == ()
    assert "entry_fill_quote_missing" in result.blockers and result.pnl_eur is None
    assert result.market_events[0].tick_index == 1


@pytest.mark.parametrize("kind", ["without_market", "provider"])
@pytest.mark.parametrize("extension", ["none", "own_rule_be_partial_v1"])
def test_ordinal_binding_does_not_enable_unrepresented_runtime_contracts(kind, extension):
    assumptions = execution(protection=profile(policy_extension=extension, request_quote_binding="timestamp_and_ordinal"))
    genome = strategy()
    if kind == "without_market":
        assumptions = replace(assumptions, market=None)
    else:
        genome = genome.with_change(provider_management_mode="explicit_close_only")
    result = compare(tape([100, 100, 100]), genome, assumptions)
    assert "protection_extension_requires_own_rule_market" in result.blockers


@pytest.mark.parametrize("binding", ["timestamp_only", "timestamp_and_ordinal"])
def test_invalid_volume_remains_a_rejection_before_ambiguous_quote_check(binding):
    assumptions = execution(protection=profile(policy_extension="own_rule_be_partial_v1", request_quote_binding=binding),
                            market=MarketProfile(1000, 1000, 1000, .05, 1., .01), entry_fill_latency_ms=250)
    result = compare(tape([100, 101, 102, 102, 102], offsets=[0, 0, 1, 2, 3]), strategy(), assumptions)
    assert result.entries == () and not result.blockers
    assert result.market_events[1].kind == "entry_rejected"
    assert result.market_events[1].reason == "invalid_volume"


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("mode", ["signal_market", "delay", "pullback", "momentum", "adverse_reversal"])
def test_repeated_timestamp_interactions_have_three_engine_parity(direction, mode):
    for seed in range(3):
        prices = 100 + np.cumsum(np.random.default_rng(411 + seed).choice([-3., -1., 0., 1., 3.], size=30))
        prices[0] = 100
        prices = np.concatenate((prices, np.full(16, prices[-1])))
        offsets = [i // 2 for i in range(30)] + list(range(60, 76))
        changes = {"entry_mode": mode, "entry_value": None if mode == "signal_market" else 5. if mode == "delay" else 1.,
                   "entry_confirmation_value": .5 if mode == "adverse_reversal" else None,
                   "time_exit_mode": "always", "time_exit_min": 1}
        genome = partial(be_mode="price", be_trigger=2., **changes)
        for fill_ms in (0, 250, 1000):
            compare(tape(prices, direction, offsets=offsets), genome, ordinal_execution(entry_fill_latency_ms=fill_ms))
