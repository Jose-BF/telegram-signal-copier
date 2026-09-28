from dataclasses import replace

import pytest

from research.dubai_iterative.engine import ExecutionAssumptions, simulate
from research.dubai_iterative.oracle import ExecutionScenario, certify_candidate, oracle_simulate
from research.dubai_iterative.protection_contract import InitialProtection, ProtectionProfile
from tests.test_iterative_entry_fill_latency import BASE_NS, genome, path


def profile(**changes):
    base = ProtectionProfile(0.01, 2, 20, 0, 1000, 1000, 1000)
    return replace(base, **changes)


def policy(**changes):
    base = genome(
        entry_mode="signal_market",
        entry_value=None,
        entry_confirmation_value=None,
        leg_count=1,
        volume_weights=(0.04,),
        entry_ladder_mode="simultaneous",
        entry_ladder_step=None,
        target_mode="per_leg_steps",
        target_steps=(0.5,),
        trailing_distance=30.0,
    )
    return base.with_change(**changes)


def run_oracle(tape, *, protection=None, strategy=None):
    return oracle_simulate(
        tape,
        strategy or policy(),
        execution=ExecutionScenario(protection=protection or profile()),
    )


def run_scalar(tape, *, protection=None, strategy=None):
    return simulate(
        tape,
        strategy or policy(),
        execution=ExecutionAssumptions(protection=protection or profile()),
    )


@pytest.mark.parametrize(
    ("direction", "quotes"),
    [("BUY", [100.0, 100.0, 101.0]), ("SELL", [100.0, 100.0, 99.0])],
)
def test_oracle_buy_and_sell_install_then_close_match_scalar(direction, quotes):
    tape = path(quotes, direction=direction)
    slow_ack = profile(acknowledgement_delay_ms=10_000)
    scalar = run_scalar(tape, protection=slow_ack)
    oracle = run_oracle(tape, protection=slow_ack)

    assert oracle.protection_events == scalar.protection_events
    assert [event.kind for event in oracle.protection_events] == [
        "open", "requested", "installed", "closed",
    ]
    assert oracle.exits[0].reason == "per_leg_target"


def test_duplicate_timestamp_open_request_is_blocked_as_ambiguous():
    result = run_oracle(path([100.0, 100.0], offsets=[0, 0]))

    assert result.entries == ()
    assert result.protection_events == ()
    assert result.pnl_eur is None
    assert result.blockers == ("protection_request_quote_ambiguous:sim_1",)


def test_management_can_process_on_later_tick_with_same_timestamp():
    tape = path([100.0, 100.0, 101.0], offsets=[0, 0, 0])
    tape = replace(
        tape,
        legs=(replace(tape.legs[0], open_price=100.0),),
        entry_evidence_kind="actual_mt5",
    )
    observed = profile(
        processing_delay_ms=0,
        acknowledgement_delay_ms=0,
        retry_delay_ms=0,
        initial_protections=(InitialProtection("template", 70.0, None, "mt5_open"),),
    )
    strategy = policy(entry_mode="actual_mt5")
    result = run_oracle(tape, protection=observed, strategy=strategy)

    assert [event.kind for event in result.protection_events] == [
        "open", "requested", "installed", "acknowledged", "closed",
    ]
    assert result.protection_events[1].tick_index == 0
    assert result.protection_events[2].tick_index == 1
    assert result.protection_events[1].timestamp_ns == result.protection_events[2].timestamp_ns


def test_rejected_pair_retains_old_sl_atomically():
    tape = path([100.0, 110.0, 80.0])
    tape = replace(
        tape,
        legs=(replace(tape.legs[0], open_price=100.0),),
        entry_evidence_kind="actual_mt5",
    )
    observed = profile(
        acknowledgement_delay_ms=10_000,
        initial_protections=(InitialProtection("template", 70.0, None, "mt5_open"),),
    )
    strategy = policy(entry_mode="actual_mt5", stop_mode="fixed_move", stop_value=10.0)
    result = run_oracle(tape, protection=observed, strategy=strategy)

    assert [event.kind for event in result.protection_events] == ["open", "requested", "rejected"]
    assert result.protection_events[-1].sl == 90.0
    assert result.exits == ()
    assert result.pnl_eur is None
    assert result.exit_reason == "not_closed"
    assert "path_ended_before_strategy_exit" in result.blockers


def test_rejected_request_retries_latest_intent_after_ack_quote_delay():
    retrying = profile(
        processing_delay_ms=0,
        acknowledgement_delay_ms=0,
        retry_delay_ms=1000,
    )
    tape = path([100.0, 101.0, 100.0, 100.0, 101.0])
    result = run_oracle(tape, protection=retrying)

    assert [(event.request_id, event.kind, event.tick_index) for event in result.protection_events] == [
        (0, "open", 0),
        (1, "requested", 0),
        (1, "rejected", 1),
        (1, "acknowledged", 1),
        (2, "requested", 2),
        (2, "installed", 3),
        (2, "acknowledged", 3),
        (0, "closed", 4),
    ]


def test_installed_protection_is_active_before_late_ack():
    result = run_oracle(
        path([100.0, 100.0, 101.0]),
        protection=profile(acknowledgement_delay_ms=10_000),
    )

    assert [event.kind for event in result.protection_events] == [
        "open", "requested", "installed", "closed",
    ]
    assert not any(event.kind == "acknowledged" for event in result.protection_events)


@pytest.mark.parametrize("market_rule", ["hard_stop", "profit_lock", "time_exit"])
def test_market_close_rule_blocks_at_trigger_without_fake_exit(market_rule):
    changes = {"target_mode": "none", "target_steps": ()}
    quotes = [100.0, 100.0]
    offsets = None
    if market_rule == "hard_stop":
        changes["hard_stop_eur_per_leg"] = 1.0
        quotes = [100.0, 99.0]
    elif market_rule == "profit_lock":
        changes.update(profit_lock_arm=3.0, profit_lock_giveback=1.0)
        quotes = [100.0, 101.0, 100.5]
    else:
        changes.update(time_exit_min=1, time_exit_mode="loss_only")
        offsets = [0, 60]

    result = run_oracle(path(quotes, offsets=offsets), strategy=policy(**changes))

    assert result.entries
    assert result.exits == ()
    assert result.exit_reason == "not_closed"
    assert result.pnl_eur is None
    assert "protection_market_close_latency_unmodeled" in result.blockers


def test_basket_money_remains_unsupported_upfront():
    result = run_oracle(
        path([100.0, 99.0]),
        strategy=policy(
            target_mode="none",
            target_steps=(),
            stop_mode="basket_money",
            stop_value=1.0,
        ),
    )

    assert result.entries == ()
    assert result.protection_events == ()
    assert "protection_policy_unsupported" in result.blockers


def test_blocked_later_opening_cannot_hide_installed_stop_on_same_quote():
    tape = path([100.0, 100.0, 98.5, 60.0])
    strategy = genome(
        entry_mode="signal_market",
        entry_value=None,
        entry_confirmation_value=None,
        leg_count=2,
        volume_weights=(0.04, 0.03),
        entry_ladder_mode="adverse",
        entry_ladder_step=1.5,
        target_mode="none",
        stop_mode="none",
        be_mode="none",
        trailing_distance=30.0,
        time_exit_min=180,
        provider_management_mode="ignore",
        pending_entry_policy="until_expiry",
    )
    execution = ExecutionScenario(
        entry_fill_latency_ms=1000,
        protection=profile(processing_delay_ms=10_000),
    )
    result = oracle_simulate(tape, strategy, execution=execution)
    scalar = simulate(
        tape,
        strategy,
        execution=ExecutionAssumptions(
            entry_fill_latency_ms=1000,
            protection=profile(processing_delay_ms=10_000),
        ),
    )
    certificate = certify_candidate((tape,), strategy, (scalar,), execution=execution)

    assert len(result.entries) == 1
    assert len(result.exits) == 1
    assert result.exits[0].tick_index == 3
    assert result.exits[0].reason == "initial_sl"
    assert [event.kind for event in result.protection_events] == ["open", "closed"]
    assert "initial_protection_rejected_unmodeled:sim_ladder_2" in result.blockers
    assert result.pnl_eur is None
    assert certificate.mismatches == ()


def test_actual_mt5_protection_rejects_non_simultaneous_schedule_upfront():
    tape = replace(path([100.0, 100.0]), entry_evidence_kind="actual_mt5")
    observed = profile(
        initial_protections=(InitialProtection("template", 70.0, None, "mt5_open"),),
    )
    strategy = genome(
        entry_mode="actual_mt5",
        entry_value=None,
        entry_confirmation_value=None,
        leg_count=1,
        volume_weights=(0.04,),
        entry_ladder_mode="adverse",
        entry_ladder_step=1.5,
        target_mode="none",
        stop_mode="none",
        be_mode="none",
        trailing_distance=30.0,
    )
    result = run_oracle(tape, protection=observed, strategy=strategy)

    assert result.entries == ()
    assert result.protection_events == ()
    assert "protection_actual_entries_require_simultaneous_schedule" in result.blockers


def test_trace_budget_and_data_end_preserve_prefix_without_synthetic_close():
    budgeted = run_oracle(path([100.0, 100.0]), protection=profile(max_events=1))
    data_end = run_oracle(path([100.0, 100.0]))

    assert [event.kind for event in budgeted.protection_events] == ["open"]
    assert len(budgeted.entries) == 1
    assert budgeted.exits == ()
    assert budgeted.pnl_eur is None
    assert "protection_event_budget_exhausted" in budgeted.blockers
    assert data_end.entries and data_end.exits == ()
    assert not any(event.kind == "closed" for event in data_end.protection_events)
    assert data_end.pnl_eur is None
    assert data_end.exit_reason == "not_closed"


def test_certificate_compares_every_protection_trace_field_exactly():
    tape = path([100.0, 100.0, 101.0])
    scalar = run_scalar(tape)
    clean = certify_candidate(
        (tape,),
        policy(),
        (scalar,),
        execution=ExecutionScenario(protection=profile()),
    )
    changed_event = replace(scalar.protection_events[1], reason="changed")
    changed = replace(
        scalar,
        protection_events=(scalar.protection_events[0], changed_event, *scalar.protection_events[2:]),
    )

    certificate = certify_candidate(
        (tape,),
        policy(),
        (changed,),
        execution=ExecutionScenario(protection=profile()),
    )

    assert clean.status == "pass"
    assert clean.promotion_eligible is False
    assert certificate.status == "blocked"
    assert [row.field for row in certificate.mismatches] == [
        "protection_events[1].reason",
    ]
