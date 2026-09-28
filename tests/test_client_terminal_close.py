from dataclasses import asdict, replace
from datetime import timedelta

import pytest

from research.dubai_iterative.client import ClientProfile
from research.dubai_iterative.dataset import ProviderEvent
from research.dubai_iterative.engine import ExecutionAssumptions, simulate
from research.dubai_iterative.shared_replay import simulate_shared
from research.execution_profile import execution_from_mapping
from tests.test_iterative_market import market
from tests.test_iterative_protection import BASE, BASE_NS, path, policy, profile
from tests.test_shared_policy_replay import PROFILE, basket, spec


def execution(**changes):
    return ExecutionAssumptions(
        protection=profile(), market=market(),
        client=ClientProfile(name="single_basket_terminal_v2"), **changes)


def tape(quotes, *, direction="BUY", close_at=1, offsets=None):
    return replace(path(quotes, direction=direction, offsets=offsets), provider_events=(
        ProviderEvent(BASE + timedelta(seconds=close_at), "CLOSE_ALL", {}),))


def strategy(**changes):
    return policy(target_mode="none", target_steps=(), trailing_distance=None,
                  provider_management_mode="explicit_close_only", **changes)


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("shared", [False, True])
def test_terminal_close_remembers_arrival_while_first_entry_is_committed(direction, shared):
    data = tape([100.] * 12, direction=direction)
    rules, assumptions = strategy(), execution(entry_fill_latency_ms=3000)
    if shared:
        item = spec("canal1_late", [], tape=data, strategy=rules, execution=assumptions)
        report = simulate_shared([item], profile=PROFILE)
        result = basket(report, item.path.signal_id)
        assert not report.blockers
    else:
        result = simulate(data, rules, execution=assumptions)
    assert not result.blockers
    assert len(result.entries) == len(result.exits) == 1
    assert result.entries[0].tick_index == 3
    queued = [row for row in result.client_events if row.operation == "close" and row.kind == "queued"]
    assert len(queued) == 1
    assert queued[0].decision_index == 1
    assert queued[0].decision_ns == BASE_NS + 1_000_000_000
    assert queued[0].tick_index >= result.entries[0].tick_index
    started = next(row for row in result.client_events if row.operation == "close" and row.kind == "started")
    assert started.time_ns >= result.entries[0].acknowledged_ns
    assert result.exits[0].reason == "provider_close"


def test_terminal_close_cancels_unsent_batch_before_entry_ack():
    rules = strategy(leg_count=3, volume_weights=(.04,) * 3, entry_ladder_mode="adverse", entry_ladder_step=1.5)
    data = tape([100.] * 5 + [96.] * 15, close_at=6)
    result = simulate(data, rules, execution=execution())
    assert not result.blockers
    assert [entry.ticket for entry in result.entries] == ["sim_1", "sim_ladder_2"]
    cancelled = [row for row in result.client_events if row.kind == "cancelled"]
    assert [(row.ticket, row.tick_index) for row in cancelled] == [("sim_ladder_3", 6)]
    assert len(result.exits) == 2
    assert all(row.decision_index == 6 for row in result.client_events if row.operation == "close" and row.kind == "queued")


def test_terminal_profile_is_explicit_and_roundtrips():
    assumptions = execution()
    assert execution_from_mapping(asdict(assumptions)) == assumptions
    assert ClientProfile().name == "single_basket_serial_v1"


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_native_stop_wins_without_double_exit_during_entry_ack_wait(direction):
    quotes = [100., 100.] + [89. if direction == "BUY" else 111.] * 10
    data = tape(quotes, direction=direction)
    assumptions = replace(execution(), market=market(entry_acknowledgement_delay_ms=5000))
    result = simulate(data, strategy(stop_mode="fixed_move", stop_value=10.), execution=assumptions)
    assert not result.blockers
    assert len(result.exits) == 1
    assert result.exits[0].tick_index == 2
    assert result.exits[0].reason == "initial_sl"
    closes = [row for row in result.market_events if row.kind == "close_requested"]
    rejected = [row for row in result.market_events if row.kind == "close_rejected"]
    assert len(closes) == len(rejected) == 1
    assert rejected[0].reason == "position_already_closed"
    assert not [row for row in result.market_events if row.kind == "close_filled"]


def test_terminal_close_survives_busy_modify_and_retains_its_clock():
    data = tape([100.] * 15, close_at=2)
    assumptions = replace(execution(),
        protection=profile(processing_delay_ms=4000, acknowledgement_delay_ms=3000),
        market=market(entry_acknowledgement_delay_ms=0))
    rules = policy(target_steps=(20.,), provider_management_mode="explicit_close_only")
    result = simulate(data, rules, execution=assumptions)
    assert not result.blockers
    queued = next(row for row in result.client_events if row.kind == "queued" and row.operation == "close")
    started = next(row for row in result.client_events if row.kind == "started" and row.operation == "close")
    assert queued.tick_index == queued.decision_index == 2
    assert started.tick_index == 7
    assert started.decision_index == 2
    assert len(result.exits) == 1 and result.exits[0].reason == "provider_close"


@pytest.mark.parametrize("length, fill_ms, close_ack_ms, missing", [
    (2, 5000, 0, "entry_fill_quote_missing"),
    (7, 3000, 5000, "market_lifecycle_incomplete_at_data_end"),
])
def test_cutoff_preserves_terminal_intent_and_unknown_completion(length, fill_ms, close_ack_ms, missing):
    assumptions = replace(execution(entry_fill_latency_ms=fill_ms),
                          market=market(close_acknowledgement_delay_ms=close_ack_ms))
    result = simulate(tape([100.] * length), strategy(), execution=assumptions)
    assert missing in result.blockers
    assert result.pnl_eur is None
    intent = [row for row in result.client_events if row.kind == "terminal_requested"]
    assert len(intent) == 1 and intent[0].decision_index == 1
    assert "client_lifecycle_incomplete_at_data_end" in result.blockers


@pytest.mark.parametrize("limit", range(1, 7))
def test_terminal_trace_budget_never_reports_success_after_losing_evidence(limit):
    assumptions = replace(execution(entry_fill_latency_ms=3000),
                          client=ClientProfile(name="single_basket_terminal_v2", max_events=limit))
    result = simulate(tape([100.] * 12), strategy(), execution=assumptions)
    assert "client_event_budget_exhausted" in result.blockers
    assert result.pnl_eur is None
    assert len(result.client_events) <= limit
    assert len(result.entries) <= 1 and len(result.exits) <= 1


def test_terminal_close_obeys_message_availability_and_duplicate_ordinals():
    data = tape([100.] * 10, close_at=1, offsets=[0, 1, 2, 3, 3, 4, 5, 6, 7, 8])
    result = simulate(data, strategy(), execution=execution(latency_ms=2000))
    assert not result.blockers
    intents = [row for row in result.client_events if row.kind == "terminal_requested"]
    assert len(intents) == 1
    assert intents[0].tick_index == intents[0].decision_index == 3
    assert intents[0].decision_ns == BASE_NS + 3_000_000_000
    assert len(result.exits) == 1


def test_repeated_provider_messages_do_not_reset_terminal_clock():
    data = tape([100.] * 15)
    data = replace(data, provider_events=data.provider_events + (
        ProviderEvent(BASE + timedelta(seconds=2), "CLOSE_ALL", {}),
        ProviderEvent(BASE + timedelta(seconds=4), "CLOSE_ALL", {})))
    result = simulate(data, strategy(), execution=execution(entry_fill_latency_ms=5000))
    assert not result.blockers
    assert len(result.entries) == len(result.exits) == 1
    assert len([row for row in result.client_events if row.kind == "terminal_requested"]) == 1
    assert all(row.decision_index == 1 for row in result.client_events if row.operation == "close")


def test_future_prices_do_not_rewrite_terminal_decision_or_pending_entry():
    assumptions = execution(entry_fill_latency_ms=3000)
    left = simulate(tape([100.] * 12), strategy(), execution=assumptions)
    right = simulate(tape([100., 100., 100.] + [110.] * 9), strategy(), execution=assumptions)
    prefix = lambda result: [row for row in result.client_events if row.tick_index <= 2]
    assert prefix(left) == prefix(right)
    assert left.entries[0].entry_price != right.entries[0].entry_price


def test_ignored_provider_message_does_not_latch_terminal_intent():
    result = simulate(tape([100.] * 12), strategy().with_change(provider_management_mode="ignore"),
                      execution=execution())
    assert not [row for row in result.client_events if row.kind == "terminal_requested"]
    assert result.exits == ()
    assert "path_ended_before_strategy_exit" in result.blockers


def test_close_before_first_entry_cancels_without_synthetic_order():
    result = simulate(tape([100.] * 12, close_at=0), strategy(), execution=execution())
    assert not result.blockers
    assert result.entries == result.exits == result.market_events == ()
    assert [row.kind for row in result.client_events] == ["terminal_requested"]


def test_shared_terminal_withdraws_unsent_entry_before_connection_release():
    quotes = [100.] * 12
    busy = spec("canal2_busy", quotes, strategy=strategy(), execution=replace(execution(),
        market=market(entry_acknowledgement_delay_ms=6000)))
    cancelled = spec("canal1_cancelled", [], tape=tape(quotes, close_at=2),
        strategy=strategy(entry_mode="delay", entry_value=1.), execution=execution())
    report = simulate_shared([busy, cancelled], profile=PROFILE)
    result = basket(report, cancelled.path.signal_id)
    assert not result.blockers
    assert result.entries == result.exits == ()
    row = next(row for row in report.transport["rows"] if row["signal_id"] == cancelled.path.signal_id)
    assert row["status"] == "cancelled_unsent"
    assert row["started_ns"] is None
    assert next(event for event in result.client_events if event.kind == "terminal_requested").tick_index == 2


def test_terminal_profile_does_not_admit_unvalidated_monetary_guard():
    from research.dubai_current_comparison import current_genome

    assumptions = replace(execution(), protection=profile(policy_extension="basket_guard_v1"))
    result = simulate(tape([100.] * 12), current_genome()[0], execution=assumptions)
    assert "basket_guard_requires_hypothetical_market" in result.blockers
    assert result.entries == ()


@pytest.mark.parametrize("mode", ["exact", "close_only", "explicit_close_only", "ignore"])
@pytest.mark.parametrize("action", ["CLOSE_ALL", "EXIT", "CERRAR", "CLOSE_PARTIAL", "CLOSE_FIRST", "CLOSE_PROFIT_OR_BE", "MOVE_SL_TO_BE"])
def test_terminal_action_vocabulary_preserves_each_declared_policy(mode, action):
    data = replace(tape([100.] * 12), provider_events=(ProviderEvent(BASE + timedelta(seconds=1), action, {}),))
    result = simulate(data, strategy().with_change(provider_management_mode=mode),
                      execution=execution(entry_fill_latency_ms=3000))
    expected = (action in {"CLOSE_ALL", "EXIT", "CERRAR"} if mode == "explicit_close_only"
                else action != "MOVE_SL_TO_BE" if mode in {"exact", "close_only"} else False)
    if expected:
        assert not result.blockers
        assert len(result.exits) == 1 and result.exits[0].reason == "provider_close"
        assert all(row.decision_index == 1 for row in result.client_events if row.operation == "close")
    else:
        assert result.exits == ()
        assert not [row for row in result.client_events if row.kind == "terminal_requested"]


@pytest.mark.parametrize("action", ["CLOSE_ALL", "EXIT", "CERRAR", "CLOSE_PARTIAL", "CLOSE_FIRST", "CLOSE_PROFIT_OR_BE", "MOVE_SL_TO_BE"])
def test_terminal_interpretation_matches_local_dubai_and_gold_live_helpers(action):
    from dubai_live_candidate import DubaiLivePolicy, is_provider_close_action as dubai_close
    from gold_555_live_candidate import Gold555Policy, is_provider_close_action as gold_close

    data = replace(tape([100.] * 12), provider_events=(ProviderEvent(BASE + timedelta(seconds=1), action, {}),))
    for live_policy, should_close in ((DubaiLivePolicy(), dubai_close), (Gold555Policy(), gold_close)):
        rules = strategy().with_change(provider_management_mode=live_policy.provider_management_mode)
        result = simulate(data, rules, execution=execution(entry_fill_latency_ms=3000))
        assert bool(result.exits) == should_close(action)
        assert bool([row for row in result.client_events if row.kind == "terminal_requested"]) == should_close(action)


@pytest.mark.parametrize("mode, decision", [("exact", 1), ("close_only", 1), ("explicit_close_only", 3)])
def test_terminal_clock_uses_first_message_eligible_for_its_policy(mode, decision):
    data = replace(tape([100.] * 15), provider_events=(
        ProviderEvent(BASE + timedelta(seconds=1), "CLOSE_PARTIAL", {}),
        ProviderEvent(BASE + timedelta(seconds=2), "MOVE_SL_TO_BE", {}),
        ProviderEvent(BASE + timedelta(seconds=3), "CLOSE_ALL", {})))
    result = simulate(data, strategy().with_change(provider_management_mode=mode),
                      execution=execution(entry_fill_latency_ms=5000))
    assert not result.blockers
    assert len(result.exits) == 1
    intent = [row for row in result.client_events if row.kind == "terminal_requested"]
    assert len(intent) == 1 and intent[0].decision_index == decision
    assert all(row.decision_index == decision for row in result.client_events if row.operation == "close")


@pytest.mark.parametrize("action", ["CLOSE_PARTIAL", "CLOSE_FIRST", "MOVE_SL_TO_BE", "CLOSE_PROFIT_OR_BE"])
def test_nonterminal_provider_action_does_not_become_close_all(action):
    data = replace(tape([100.] * 12), provider_events=(ProviderEvent(BASE + timedelta(seconds=1), action, {}),))
    result = simulate(data, strategy(), execution=execution())
    assert not [row for row in result.client_events if row.kind == "terminal_requested"]
    assert result.exits == ()


def test_invalid_quote_does_not_postpone_receiving_terminal_message():
    data = tape([100., float("nan")] + [100.] * 10)
    result = simulate(data, strategy(), execution=execution(entry_fill_latency_ms=3000))
    assert not result.blockers
    intent = next(row for row in result.client_events if row.kind == "terminal_requested")
    assert intent.tick_index == intent.decision_index == 1
    assert len(result.entries) == len(result.exits) == 1


def test_close_on_ladder_ack_ordinal_never_starts_next_batch_entry():
    rules = strategy(leg_count=3, volume_weights=(.04,) * 3, entry_ladder_mode="adverse", entry_ladder_step=1.5)
    result = simulate(tape([100.] * 5 + [96.] * 15, close_at=7), rules, execution=execution())
    assert not result.blockers
    assert len(result.entries) == len(result.exits) == 2
    assert not [row for row in result.market_events if row.ticket == "sim_ladder_3"]


def test_ladder_committed_before_close_fills_after_it_and_is_closed_once():
    rules = strategy(leg_count=3, volume_weights=(.04,) * 3, entry_ladder_mode="adverse", entry_ladder_step=1.5)
    assumptions = execution(entry_fill_latency_ms=3000)
    result = simulate(tape([100.] * 6 + [96.] * 20, close_at=7), rules, execution=assumptions)
    assert not result.blockers
    assert [(row.ticket, row.tick_index) for row in result.entries] == [("sim_1", 3), ("sim_ladder_2", 9)]
    assert {row.ticket for row in result.exits} == {"sim_1", "sim_ladder_2"}
    assert len(result.exits) == 2
    assert all(row.reason == "provider_close" for row in result.exits)
    closes = [row for row in result.client_events if row.operation == "close" and row.kind == "queued"]
    assert len(closes) == 2 and all(row.decision_index == 7 for row in closes)


def test_no_provider_close_keeps_v1_full_result():
    data = path([100., 100., 100., 101.] + [101.] * 12)
    rules = policy(provider_management_mode="ignore")
    assumptions = execution()
    legacy = replace(assumptions, client=ClientProfile())
    assert simulate(data, rules, execution=assumptions) == simulate(data, rules, execution=legacy)


@pytest.mark.parametrize("native_stop", [False, True])
def test_shared_provider_close_keeps_arrival_clock_while_other_basket_modifies(native_stop):
    quotes = [100.] * 4 + ([89.] * 12 if native_stop else [100.] * 12)
    immediate = market(entry_acknowledgement_delay_ms=0, close_acknowledgement_delay_ms=0)
    closing = spec("canal1_closing", [], tape=tape(quotes, close_at=2),
        strategy=strategy(stop_mode="fixed_move", stop_value=10.),
        execution=replace(execution(), market=immediate))
    busy = spec("canal2_modify", [], tape=tape(quotes, close_at=10),
        strategy=policy(target_steps=(20.,), provider_management_mode="explicit_close_only"),
        execution=replace(execution(), market=immediate,
            protection=profile(processing_delay_ms=5000, acknowledgement_delay_ms=3000)))
    report = simulate_shared([closing, busy], profile=PROFILE)
    result = basket(report, closing.path.signal_id)
    assert not report.blockers
    assert len(result.exits) == 1
    queued = next(row for row in result.client_events if row.kind == "queued" and row.operation == "close")
    started = next(row for row in result.client_events if row.kind == "started" and row.operation == "close")
    assert queued.tick_index == queued.decision_index == 2
    assert started.tick_index == 8 and started.decision_index == 2
    assert result.exits[0].reason == ("initial_sl" if native_stop else "provider_close")
    assert result.exits[0].tick_index == (4 if native_stop else 9)
    assert not report.full_live_parity_verified


def test_zero_delay_close_needs_later_ordinal_even_with_equal_timestamp():
    assumptions = replace(execution(), market=market(entry_acknowledgement_delay_ms=0,
        close_processing_delay_ms=0, close_acknowledgement_delay_ms=0))
    data = tape([100.] * 6, offsets=[0, 1, 1, 1, 2, 3])
    result = simulate(data, strategy(), execution=assumptions)
    assert not result.blockers
    assert result.exits[0].tick_index == 2
    requested = next(row for row in result.market_events if row.kind == "close_requested")
    filled = next(row for row in result.market_events if row.kind == "close_filled")
    assert requested.tick_index == 1
    assert requested.timestamp_ns == filled.timestamp_ns


@pytest.mark.parametrize("fill_seconds", [0, 1, 3, 5])
@pytest.mark.parametrize("ack_seconds", [0, 2, 5])
@pytest.mark.parametrize("close_seconds", [1, 3, 6])
@pytest.mark.parametrize("shared", [False, True])
def test_clock_matrix_matches_independent_serial_close_schedule(fill_seconds, ack_seconds, close_seconds, shared):
    data = tape([100.] * 20, close_at=close_seconds)
    assumptions = replace(execution(entry_fill_latency_ms=fill_seconds * 1000),
        market=market(entry_acknowledgement_delay_ms=ack_seconds * 1000))
    rules = strategy()
    if shared:
        item = spec("canal1_matrix", [], tape=data, strategy=rules, execution=assumptions)
        report = simulate_shared([item], profile=PROFILE)
        assert not report.blockers
        result = basket(report, item.path.signal_id)
    else:
        result = simulate(data, rules, execution=assumptions)
    assert not result.blockers
    assert len(result.entries) == len(result.exits) == 1
    assert result.entries[0].tick_index == fill_seconds
    requested = next(row for row in result.market_events if row.kind == "close_requested")
    assert requested.tick_index == max(close_seconds, fill_seconds + ack_seconds)
    assert result.exits[0].tick_index == requested.tick_index + 1
    assert all(row.decision_index == close_seconds for row in result.client_events if row.operation == "close")
