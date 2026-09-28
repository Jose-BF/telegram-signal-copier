from dataclasses import replace

from research.dubai_iterative.client import ClientProfile
from research.dubai_iterative.engine import simulate
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.oracle import oracle_simulate
from research.dubai_iterative.shared_replay import SharedReplayProfile, simulate_shared
from research.execution_profile import execution_to_scenario
from tests.test_basket_guard_execution import execution, policy
from tests.test_iterative_market import market
from tests.test_iterative_protection import policy as price_policy, profile
from tests.test_shared_policy_replay import basket, spec


def guard_execution(**changes):
    return replace(
        execution(), client=ClientProfile(name="single_basket_terminal_guard_v1"),
        **changes)


def test_opt_in_terminal_guard_preserves_single_basket_execution_and_risk():
    item = spec(
        "canal1_guard", [100., 100., 98., 97., 97., 97., 97.],
        strategy=policy(leg_count=1, volume_weights=(.04,), stop_value=5.),
        execution=guard_execution())
    scalar = simulate(item.path, item.genome, execution=item.execution)
    shared = simulate_shared([item], profile=SharedReplayProfile(account_currency="EUR"))
    result = basket(shared, item.path.signal_id)

    assert not scalar.blockers and not shared.blockers
    assert result == scalar
    assert result.exits[0].reason == "basket_stop"
    requested = [event for event in result.market_events if event.kind == "close_requested"]
    filled = [event for event in result.market_events if event.kind == "close_filled"]
    assert len(requested) == len(filled) == 1
    assert requested[0].tick_index < filled[0].tick_index
    assert any(point.positions and point.floating_minor < 0
               for point in shared.risk if point.phase == "settled")


def test_guard_close_waits_for_other_channel_without_erasing_floating_loss():
    quotes = [100., 100., 98., 97., 97., 97., 97., 97., 97., 101., 101., 101.]
    dubai = spec(
        "canal1_guard", quotes,
        strategy=policy(leg_count=1, volume_weights=(.04,), stop_value=5.),
        execution=guard_execution())
    busy = spec(
        "canal2_busy", quotes,
        strategy=price_policy(entry_mode="delay", entry_value=1.),
        execution=replace(execution(), protection=profile(),
                          market=market(entry_acknowledgement_delay_ms=6000),
                          client=ClientProfile()))
    report = simulate_shared(
        [dubai, busy], profile=SharedReplayProfile(account_currency="EUR", capacity=1))
    result = basket(report, dubai.path.signal_id)
    rows = report.transport["rows"]
    busy_entry = next(row for row in rows if row["signal_id"] == busy.path.signal_id
                      and row["operation"] == "OPEN_MARKET")
    dubai_close = next(row for row in rows if row["signal_id"] == dubai.path.signal_id
                       and row["operation"] == "CLOSE_POSITION")

    assert not result.blockers
    assert result.exits[0].reason == "basket_stop"
    assert busy_entry["started_ns"] < dubai_close["started_ns"]
    assert dubai_close["started_ns"] >= busy_entry["released_ns"]
    assert any(point.tick_index in {3, 4, 5, 6} and point.positions
               and point.floating_minor < -500
               for point in report.risk if point.signal_id == dubai.path.signal_id)
    assert result.exits[0].tick_index > 6


def test_guard_client_requires_opt_in_and_keeps_other_engines_uncertified():
    item = spec(
        "canal1_guard", [100., 100., 98., 97., 97.],
        strategy=policy(leg_count=1, volume_weights=(.04,), stop_value=5.),
        execution=guard_execution())
    unbound = replace(item.execution, protection=replace(
        item.execution.protection, request_quote_binding="timestamp_only"))
    assert "basket_guard_requires_hypothetical_market" in simulate(
        item.path, item.genome, execution=unbound).blockers
    assert "client_model_not_validated_in_fast" in FastEvaluator(
        execution=item.execution)(item.path, item.genome).blockers
    assert "basket_guard_requires_hypothetical_market" in oracle_simulate(
        item.path, item.genome,
        execution=execution_to_scenario(item.execution)).blockers
