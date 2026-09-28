"""Fixed acceptance cases for the initial own-rule execution domain."""

from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal

import pytest

from research.dubai_iterative.dataset import ProviderEvent
from research.dubai_iterative.engine import simulate
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.market_contract import MarketProfile
from research.dubai_iterative.oracle import certify_candidate
from research.execution_profile import execution_from_mapping, execution_to_scenario
from tests.test_iterative_protection import BASE, path, policy, profile


def execution():
    return execution_from_mapping({
        "entry_fill_latency_ms": 1000,
        "protection": asdict(profile()),
        "market": asdict(MarketProfile(1000, 1000, 1000, .01, 1., .01)),
    })


def check_engines(tape, strategy):
    assumptions = execution()
    scalar = simulate(tape, strategy, execution=assumptions)
    fast = FastEvaluator(execution=assumptions)(tape, strategy)
    certificate = certify_candidate((tape,), strategy, (scalar,),
                                    execution=execution_to_scenario(assumptions))
    assert certificate.mismatches == ()
    assert fast == scalar
    assert certificate.promotion_eligible is False
    return scalar


def own_policy(**changes):
    return policy(stop_mode="fixed_move", stop_value=10., trailing_distance=None,
                  pending_entry_policy="none", **changes)


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("mode,value,confirmation,quotes,request_index", [
    ("signal_market", None, None, [100., 100., 99., 99., 100.5, 100.5, 100.5, 102., 102., 102.], 0),
    ("delay", 2., None, [100., 100., 99., 99., 99., 99., 99., 102., 102., 102.], 2),
    ("pullback", 1., None, [100., 100., 99., 99., 99., 99., 99., 102., 102., 102.], 2),
    ("momentum", 1., None, [100., 100., 101., 101., 101., 101., 101., 103., 103., 103.], 2),
    ("adverse_reversal", 1., 1.5, [100., 100., 99., 99., 100.5, 100.5, 100.5, 100.5, 100.5, 102., 102., 102.], 4),
])
def test_own_entry_family_uses_delayed_fill_and_fixed_target(direction, mode, value, confirmation, quotes, request_index):
    quotes = quotes if direction == "BUY" else [200. - quote for quote in quotes]
    tape = path(quotes, direction=direction)
    strategy = own_policy(entry_mode=mode, entry_value=value, entry_confirmation_value=confirmation)
    result = check_engines(tape, strategy)
    assert result.blockers == () and not result.unfilled
    assert len(result.entries) == len(result.exits) == 1
    assert result.entries[0].requested_ns == int(tape.times_ns[request_index])
    assert result.entries[0].tick_index == request_index + 1
    assert result.entries[0].entry_price == quotes[request_index + 1]
    assert result.pnl_eur == Decimal("2.00")

    with_provider = replace(tape, provider_events=(
        ProviderEvent(BASE + timedelta(seconds=1), "CLOSE_ALL", {}),
        ProviderEvent(BASE + timedelta(seconds=2), "MOVE_SL_TO_BE", {}),
    ))
    assert check_engines(with_provider, strategy) == result


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("mode,move,closes", [
    ("loss_only", -.2, True), ("loss_only", 0., True), ("loss_only", .2, False),
    ("profit_only", -.2, False), ("profit_only", 0., False), ("profit_only", .2, True),
    ("non_negative", -.2, False), ("non_negative", 0., True), ("non_negative", .2, True),
    ("none", -.2, False), ("none", 0., False), ("none", .2, False),
])
def test_time_exit_sign_boundary_keeps_processing_and_ack_separate(direction, mode, move, closes):
    quotes = [100., 100.] + [100.2 + move] * 8
    if direction == "SELL":
        quotes = [200. - quote for quote in quotes]
    tape = path(quotes, direction=direction, offsets=[0, 1, 2, 60, 61, 62, 63, 64, 65, 66])
    strategy = own_policy(target_mode="none", target_steps=(), time_exit_mode=mode, time_exit_min=1)
    result = check_engines(tape, strategy)
    if not closes:
        assert result.exits == () and result.pnl_eur is None
        assert "path_ended_before_strategy_exit" in result.blockers
        return
    assert result.blockers == ()
    assert result.exits[0].tick_index == 5
    assert result.pnl_eur == Decimal(str(round(move * 4., 2)))
    events = [event for event in result.market_events if event.kind.startswith("close_")]
    assert any(event.kind == "close_requested" and event.tick_index == 4 for event in events)
    assert any(event.kind == "close_filled" and event.tick_index == 5 for event in events)
    assert any(event.kind == "close_acknowledged" and event.tick_index == 6 for event in events)


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("mode,value,admits", [
    ("none", None, True), ("max_spread", .1, False), ("max_spread", .3, True),
    ("time_window", 9.99, False), ("time_window", 10., True),
])
def test_entry_filter_preserves_rejected_opportunities(direction, mode, value, admits):
    quotes = [100., 100., 100., 100., 102., 102., 102.]
    if direction == "SELL":
        quotes = [200. - quote for quote in quotes]
    result = check_engines(path(quotes, direction=direction),
                           own_policy(context_filter_mode=mode, context_filter_value=value))
    assert result.blockers == ()
    if admits:
        assert len(result.entries) == len(result.exits) == 1
        assert result.pnl_eur == Decimal("2.00")
    else:
        assert result.entries == result.exits == ()
        assert result.unfilled and result.pnl_eur == Decimal("0.00")


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_crossed_target_before_installation_does_not_create_an_own_rule_profit(direction):
    quotes = [100., 100., 99., 99., 100.5, 100.5, 100.5, 102., 102., 102.]
    if direction == "SELL":
        quotes = [200. - quote for quote in quotes]
    result = check_engines(path(quotes, direction=direction), own_policy(entry_mode="delay", entry_value=2.))
    assert len(result.entries) == 1 and result.entries[0].tick_index == 3
    assert result.exits == () and result.pnl_eur is None
    assert result.blockers == ("path_ended_before_strategy_exit",)
    rejected = [event for event in result.protection_events if event.kind == "rejected"]
    assert [event.tick_index for event in rejected] == [5, 8]
    assert all(event.reason == "invalid_stops" for event in rejected)
