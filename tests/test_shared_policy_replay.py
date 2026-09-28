from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace

import pytest
import numpy as np

from research.dubai_iterative.client import ClientBook, ClientProfile
from research.dubai_iterative.dataset import ProviderEvent, RolloverEvent
from research.dubai_iterative.engine import ExecutionAssumptions, simulate
from research.dubai_iterative.shared_replay import BasketReplaySpec, SharedReplayProfile, simulate_shared
from tests.test_iterative_market import market
from tests.test_iterative_protection import BASE, BASE_NS, path, policy, profile
from tests.test_iterative_resume import CASES


PROFILE = SharedReplayProfile(account_currency="EUR")


def spec(name, quotes, *, strategy=None, execution=None, tape=None):
    channel = name.split("_", 1)[0]
    return BasketReplaySpec(channel, replace(tape or path(quotes), signal_id=name), strategy or policy(),
                            execution or ExecutionAssumptions(protection=profile(), market=market(), client=ClientProfile()))


def basket(report, identity):
    return next(result for _, signal_id, result in report.baskets if signal_id == identity)


@pytest.mark.parametrize("name", [name for name in CASES if "-client-" in name])
def test_single_basket_preserves_entire_scalar_result(name):
    tape, strategy, execution = CASES[name]
    item = spec("canal1_control", [], tape=tape, strategy=strategy, execution=execution)
    result = simulate_shared([item], profile=PROFILE)
    assert basket(result, item.path.signal_id) == simulate(item.path, strategy, execution=execution)


def test_busy_other_basket_changes_actual_generated_entry_price_and_risk():
    quotes = [100., 100., 99., 98., 97., 96., 95., 95., 95., 95., 95., 95.]
    first = spec("canal2_first", quotes, execution=ExecutionAssumptions(
        protection=profile(), market=market(entry_acknowledgement_delay_ms=5000), client=ClientProfile()))
    second = spec("canal1_second", quotes, strategy=policy(entry_mode="delay", entry_value=1., target_steps=(20.,)))
    alone = simulate(second.path, second.genome, execution=second.execution)
    report = simulate_shared([first, second], profile=PROFILE)
    shared = basket(report, second.path.signal_id)
    assert alone.entries[0].tick_index == 1
    assert shared.entries[0].tick_index > 1
    assert shared.entries[0].entry_price != alone.entries[0].entry_price
    assert shared.max_adverse_eur != alone.max_adverse_eur
    waiting = [point for point in report.risk if point.signal_id == second.path.signal_id and point.tick_index < shared.entries[0].tick_index]
    assert waiting and all(not point.positions for point in waiting)
    assert report.full_live_parity_verified is report.portfolio_admitted is False


def test_installed_native_stop_runs_while_other_basket_owns_connection():
    quotes = [100., 100., 100., 100., 69.] + [69.] * 12
    first = spec("canal1_first", quotes, execution=ExecutionAssumptions(
        protection=profile(), market=market(entry_acknowledgement_delay_ms=0), client=ClientProfile()))
    second = spec("canal2_busy", quotes, strategy=policy(entry_mode="delay", entry_value=2., target_steps=(20.,)),
                  execution=ExecutionAssumptions(protection=profile(), market=market(entry_acknowledgement_delay_ms=8000), client=ClientProfile()))
    report = simulate_shared([first, second], profile=PROFILE)
    result = basket(report, first.path.signal_id)
    assert result.exits[0].tick_index == 4
    assert result.exits[0].reason == "trailing_stop"
    assert any(row.kind == "installed" and row.tick_index < 4 for row in result.protection_events)
    busy = [row for row in report.transport["rows"] if row["signal_id"] == second.path.signal_id and row["operation"] == "OPEN_MARKET"][0]
    assert busy["started_ns"] <= 4_000_000_000 < busy["released_ns"]


def test_provider_close_withdraws_queued_entry_before_other_basket_releases():
    quotes = [100.] * 10
    first = spec("canal2_busy", quotes, execution=ExecutionAssumptions(
        protection=profile(), market=market(entry_acknowledgement_delay_ms=5000), client=ClientProfile()))
    tape = replace(path(quotes), provider_events=(ProviderEvent(BASE + timedelta(seconds=2), "CLOSE_ALL", {}),))
    second = spec("canal1_cancelled", [], tape=tape,
                  strategy=policy(entry_mode="delay", entry_value=1., provider_management_mode="explicit_close_only"))
    report = simulate_shared([first, second], profile=PROFILE)
    assert basket(report, second.path.signal_id).entries == ()
    row = next(row for row in report.transport["rows"] if row["signal_id"] == second.path.signal_id)
    assert row["status"] == "cancelled_unsent"
    assert row["started_ns"] is None


def test_last_quote_grant_is_consumed_without_fake_next_quote():
    first = spec("canal1_last", [100.], execution=ExecutionAssumptions(
        protection=profile(), market=market(entry_acknowledgement_delay_ms=0), client=ClientProfile()))
    report = simulate_shared([first], profile=PROFILE)
    result = basket(report, first.path.signal_id)
    assert result.entries[0].tick_index == 0
    assert result.entries[0].requested_ns == BASE_NS
    assert "path_ended_before_strategy_exit" in result.blockers
    assert all(point.tick_index == 0 for point in report.risk)


@pytest.mark.parametrize("mode", ["fresh_quote", "snapshot_batch"])
def test_zero_ack_rounds_are_not_new_ladder_quotes(mode):
    strategy = policy(leg_count=3, volume_weights=(.04,) * 3, target_steps=(20.,) * 3,
                      entry_ladder_mode="adverse", entry_ladder_step=1.5)
    execution = ExecutionAssumptions(protection=profile(processing_delay_ms=0, acknowledgement_delay_ms=0),
                                     market=market(entry_acknowledgement_delay_ms=0),
                                     client=ClientProfile(ladder_decision_mode=mode))
    item = spec("canal1_batch", [100., 100., 96., 96., 96., 96.], strategy=strategy, execution=execution)
    report = simulate_shared([item], profile=PROFILE)
    result = basket(report, item.path.signal_id)
    assert result == simulate(item.path, strategy, execution=execution)
    assert len({row.tick_index for row in result.entries}) == len(result.entries)


def test_unequal_tape_or_duplicate_identity_is_not_silently_aligned():
    first = spec("canal1_a", [100., 100.])
    with pytest.raises(ValueError, match="identical"):
        simulate_shared([first, spec("canal2_b", [100., 101.])], profile=PROFILE)
    with pytest.raises(ValueError, match="duplicate"):
        simulate_shared([first, first], profile=PROFILE)


def test_incomplete_and_budget_limited_baskets_remain_in_denominator():
    first = spec("canal1_a", [100., 100.])
    second = spec("canal2_b", [100., 100.])
    report = simulate_shared([first, second], profile=replace(PROFILE, max_events=1))
    assert len(report.baskets) == 2
    assert report.blockers
    assert not report.full_live_parity_verified


def test_one_round_budget_does_not_fail_when_no_second_grant_is_needed():
    item = spec("canal1_one", [100., 100.], strategy=policy(target_mode="none", target_steps=(), trailing_distance=None))
    report = simulate_shared([item], profile=replace(PROFILE, max_rounds_per_quote=1))
    assert "shared_round_budget_exhausted" not in report.blockers


def test_provider_cancel_at_deadline_does_not_try_to_revoke_expired_work():
    quotes = [100.] * 5
    busy = spec("canal2_busy", quotes, execution=ExecutionAssumptions(
        protection=profile(), market=market(entry_acknowledgement_delay_ms=10_000), client=ClientProfile()))
    tape = replace(path(quotes), provider_events=(ProviderEvent(BASE + timedelta(seconds=2), "CLOSE_ALL", {}),))
    cancelled = spec("canal1_cancelled", [], tape=tape,
                     strategy=policy(entry_mode="delay", entry_value=1., provider_management_mode="explicit_close_only"))
    report = simulate_shared([busy, cancelled], profile=replace(PROFILE, request_timeout_ms=1000))
    assert basket(report, cancelled.path.signal_id).entries == ()
    assert next(row for row in report.transport["rows"] if row["signal_id"] == cancelled.path.signal_id)["status"] == "expired_unsent"


def contested_management(quotes=None, direction="BUY"):
    quotes = quotes or [100., 101., 100., 99.] + [89.] * 9
    if direction == "SELL":
        quotes = [200. - value for value in quotes]
    common = path(quotes, direction=direction)
    immediate = market(entry_acknowledgement_delay_ms=0, close_acknowledgement_delay_ms=0)
    dubai = spec("canal1_guard", [], tape=common,
                 strategy=policy(target_mode="none", target_steps=(), trailing_distance=None,
                                 stop_mode="fixed_move", stop_value=10., profit_lock_arm=1., profit_lock_giveback=1.),
                 execution=ExecutionAssumptions(protection=profile(), market=immediate, client=ClientProfile()))
    tape = replace(common, provider_events=(ProviderEvent(BASE + timedelta(seconds=10), "CLOSE_ALL", {}),))
    gold = spec("canal2_modify", [], tape=tape,
                strategy=policy(target_steps=(20.,), provider_management_mode="explicit_close_only"),
                execution=ExecutionAssumptions(protection=profile(processing_delay_ms=5000, acknowledgement_delay_ms=3000),
                                              market=immediate, client=ClientProfile()))
    return dubai, gold


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_actual_modify_blocks_other_policy_close_but_not_native_stop(direction):
    dubai, gold = contested_management(direction=direction)
    report = simulate_shared([dubai, gold], profile=PROFILE)
    guarded = basket(report, dubai.path.signal_id)
    alone = simulate(dubai.path, dubai.genome, execution=dubai.execution)
    assert report.blockers == ()
    assert alone.exits[0].reason == "profit_lock" and alone.exits[0].tick_index == 3
    assert guarded.exits[0].reason == "initial_sl" and guarded.exits[0].tick_index == 4
    assert guarded.max_floating_drawdown_eur > alone.max_floating_drawdown_eur
    rows = report.transport["rows"]
    modify = next(row for row in rows if row["operation"] == "MODIFY_SLTP")
    close = next(row for row in rows if row["operation"] == "CLOSE_POSITION" and row["signal_id"] == dubai.path.signal_id)
    assert modify["started_ns"] == 0 and modify["released_ns"] == 8_000_000_000
    assert close["started_ns"] == 8_000_000_000 and close["queue_ns"] == 6_000_000_000
    native = next(row for row in report.transport["events"] if row["kind"] == "passive" and row["signal_id"] == dubai.path.signal_id)
    assert native["mechanism"] == "native_sl" and native["at_ns"] == 4_000_000_000
    assert native["active_request_id"] == modify["request_id"]
    rejected = next(event for event in guarded.market_events if event.kind == "close_rejected")
    assert rejected.reason == "position_already_closed"
    at_stop = [point for point in report.risk if point.signal_id == dubai.path.signal_id and point.tick_index == 4]
    assert any(point.positions for point in at_stop if point.phase == "quote_open")
    assert all(not point.positions and point.realized_minor < 0 for point in at_stop if point.phase in {"native_exit", "settled"})
    assert len({job["request_id"] for job in report.transport["jobs"]}) == len(report.transport["jobs"])

    for point in report.risk:
        item = dubai if point.signal_id == dubai.path.signal_id else gold
        sign = Decimal(1 if item.path.direction == "BUY" else -1)
        quote = Decimal(str(item.path.exit_quotes[point.tick_index]))
        expected = sum((sign * (quote - Decimal(str(price))) * Decimal(str(volume)) * Decimal(100)
                        for _, volume, price in point.positions), Decimal(0))
        expected_minor = int((expected * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        assert point.floating_minor == expected_minor
        assert point.equity_minor == point.realized_minor + expected_minor


def test_unseen_suffix_cannot_change_connected_decisions_or_risk_prefix():
    first = simulate_shared(contested_management(), profile=PROFILE)
    second = simulate_shared(contested_management([100., 101., 100., 99.] + [89.] * 5 + [110.] * 4), profile=PROFILE)
    assert [point for point in first.risk if point.tick_index < 9] == [point for point in second.risk if point.tick_index < 9]
    assert [event for event in first.transport["events"] if event["at_ns"] < 9_000_000_000] == [
        event for event in second.transport["events"] if event["at_ns"] < 9_000_000_000]


def test_modify_that_became_noop_still_releases_its_global_grant_once():
    quotes = [100., 101.] + [89.] * 8
    first = spec("canal1_closed", quotes, strategy=policy(target_mode="none", target_steps=(), trailing_distance=10.),
                 execution=ExecutionAssumptions(protection=profile(), market=market(entry_acknowledgement_delay_ms=0), client=ClientProfile()))
    second = spec("canal2_busy", quotes, strategy=policy(target_mode="none", target_steps=(), trailing_distance=None),
                  execution=ExecutionAssumptions(protection=profile(), market=market(entry_acknowledgement_delay_ms=5000), client=ClientProfile()))
    report = simulate_shared([first, second], profile=PROFILE)
    result = basket(report, first.path.signal_id)
    assert result.exits[0].tick_index == 2
    assert not any(event.kind == "requested" for event in result.protection_events)
    modify = next(row for row in report.transport["rows"] if row["operation"] == "MODIFY_SLTP")
    assert modify["started_ns"] == modify["released_ns"] == 5_000_000_000
    assert modify["status"] == "released"
    events = [row["kind"] for row in report.transport["events"] if row["request_id"] == modify["request_id"]]
    assert events.count("started") == events.count("response") == 1


def test_closing_at_settlement_neither_replays_nor_finalizes(monkeypatch):
    from research.dubai_iterative import engine
    item = spec("canal1_close", [100., 101.])
    port = SimpleNamespace(client_book=ClientBook, reserve_risk=lambda: None)
    stream = engine._simulation_steps(item.path, item.genome, execution=item.execution, _transport=port)
    assert isinstance(next(stream), engine._ReplayBoundary)
    assert isinstance(next(stream), engine._ReplaySettlement)

    def forbidden(*args, **kwargs):
        pytest.fail("abandoning a settled frame cannot execute or fabricate a result")

    monkeypatch.setattr(engine, "SimulationResult", forbidden)
    monkeypatch.setattr(engine, "_basket_minor", forbidden)
    stream.close()


def test_retry_window_noop_does_not_create_same_quote_modify_storm():
    item = spec("canal1_retry", [100., 100., 101., 102., 103., 104., 105., 106., 107., 108.],
                strategy=policy(target_steps=(20.,)))
    report = simulate_shared([item], profile=PROFILE)
    assert "shared_round_budget_exhausted" not in report.blockers
    assert basket(report, item.path.signal_id) == simulate(item.path, item.genome, execution=item.execution)
    assert sum(row["operation"] == "MODIFY_SLTP" and row["started_ns"] == 4_000_000_000 for row in report.transport["rows"]) == 1


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_exit_prices_must_match_the_executable_side_of_shared_tape(direction):
    tape = path([100., 100.], direction=direction)
    changed = tape.exit_quotes.copy()
    changed[1] = 69.
    item = spec("canal1_invalid", [], tape=replace(tape, exit_quotes=changed))
    with pytest.raises(ValueError, match="executable"):
        simulate_shared([item], profile=PROFILE)


def test_request_budget_exhaustion_returns_partial_evidence(monkeypatch):
    import research.shared_transport as transport
    monkeypatch.setattr(transport, "MAX_JOBS", 1)
    report = simulate_shared([spec("canal1_a", [100., 100.]), spec("canal2_b", [100., 100.])], profile=PROFILE)
    assert len(report.baskets) == 2
    assert any("shared_request_budget_exhausted" in reason for reason in report.blockers)
    assert len(report.transport["jobs"]) == 1
    assert report.risk


def test_risk_preserves_both_sides_of_a_broker_transition_on_one_quote():
    item = spec("canal1_gap", [100., 100., 100., 105., 105.],
                execution=ExecutionAssumptions(protection=profile(), market=market(entry_acknowledgement_delay_ms=0), client=ClientProfile()))
    report = simulate_shared([item], profile=PROFILE)
    result = basket(report, item.path.signal_id)
    assert result.exits[0].tick_index == 3
    at_exit = [point for point in report.risk if point.tick_index == 3]
    assert any(point.positions for point in at_exit)
    assert any(not point.positions and point.realized_minor == 200 for point in at_exit)
    assert max(point.equity_minor for point in report.risk) == 1920
    assert at_exit[0].phase == "quote_open"
    assert any(point.phase == "native_exit" for point in at_exit)


def test_risk_budget_bounds_snapshot_creation_before_dense_rollovers(monkeypatch):
    from research.dubai_iterative import engine
    made = []
    original = engine._ReplayRiskSnapshot

    def counted(*args, **kwargs):
        made.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(engine, "_ReplayRiskSnapshot", counted)
    tape = replace(path([100., 100.]), rollover_events=tuple(
        RolloverEvent(BASE + timedelta(milliseconds=index), np.zeros(5, dtype=np.int64)) for index in range(1, 101)))
    item = spec("canal1_dense", [], tape=tape, strategy=policy(target_mode="none", target_steps=(), trailing_distance=None),
                execution=ExecutionAssumptions(protection=profile(), market=market(entry_acknowledgement_delay_ms=0), client=ClientProfile()))
    report = simulate_shared([item], profile=replace(PROFILE, max_events=10))
    assert len(made) <= 10
    assert len(report.risk) == len(made)
    assert "shared_risk_event_budget_exhausted" in report.blockers
