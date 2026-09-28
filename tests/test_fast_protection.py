from dataclasses import replace
from datetime import timedelta

import pytest

from research.dubai_iterative.dataset import ProviderEvent
from research.dubai_iterative.engine import ExecutionAssumptions, simulate
from research.dubai_iterative.fast_engine import FastEvaluator, _protection_kernel
from research.dubai_iterative.protection_contract import (
    InitialProtection,
    ProtectionProfile,
)
from tests.test_iterative_entry_fill_latency import BASE, genome, path


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


def fast_run(tape, *, protection=None, strategy=None, **execution_changes):
    execution = ExecutionAssumptions(
        protection=protection or profile(),
        **execution_changes,
    )
    return FastEvaluator(execution=execution)(tape, strategy or policy())


def assert_fast_matches_scalar(tape, strategy, execution):
    fast = FastEvaluator(execution=execution)(tape, strategy)
    scalar = simulate(tape, strategy, execution=execution)
    assert fast == scalar
    return fast


def test_compiled_lifecycle_rejects_crossed_pair_and_keeps_position_open_at_data_end():
    result = fast_run(path([100.0, 101.0, 100.0, 101.0]))

    assert result.exits == ()
    assert result.pnl_eur is None
    assert result.exit_reason == "not_closed"
    assert "path_ended_before_strategy_exit" in result.blockers
    assert [event.kind for event in result.protection_events] == [
        "open",
        "requested",
        "rejected",
        "acknowledged",
        "requested",
    ]
    rejected = result.protection_events[2]
    assert (rejected.tick_index, rejected.request_id, rejected.reason) == (
        1,
        1,
        "invalid_stops",
    )
    assert _protection_kernel.nopython_signatures


def test_installed_target_is_active_before_acknowledgement():
    result = fast_run(
        path([100.0, 100.0, 101.0]),
        protection=profile(acknowledgement_delay_ms=10_000),
    )

    assert result.exits[0].tick_index == 2
    assert result.exits[0].reason == "per_leg_target"
    assert [event.kind for event in result.protection_events] == [
        "open",
        "requested",
        "installed",
        "closed",
    ]
    assert result.protection_events[-1].request_id == 0


def test_old_initial_sl_wins_while_modify_is_pending():
    result = fast_run(
        path([100.0, 99.0, 69.0]),
        protection=profile(processing_delay_ms=10_000),
    )

    assert result.exits[0].tick_index == 2
    assert result.exits[0].reason == "initial_sl"
    assert [event.kind for event in result.protection_events] == [
        "open",
        "requested",
        "closed",
    ]


def test_rejected_pair_retains_old_sl_and_tp_atomically():
    result = fast_run(
        path([100.0, 110.0, 105.0, 79.0]),
        protection=profile(acknowledgement_delay_ms=10_000),
    )

    assert result.exits == ()
    assert any(event.kind == "rejected" for event in result.protection_events)


def test_hypothetical_open_uses_its_own_request_quote_not_fill_price():
    execution = ExecutionAssumptions(
        entry_fill_latency_ms=1000,
        protection=profile(),
    )
    strategy = policy(
        target_mode="none",
        target_steps=(),
        trailing_distance=5.0,
    )
    tape = path([100.0, 110.0])

    result = assert_fast_matches_scalar(tape, strategy, execution)

    assert result.entries[0].entry_price == 110.0
    assert result.protection_events[0].kind == "open"
    assert result.protection_events[0].sl == 95.0
    assert result.protection_events[0].tp is None


def test_invalid_hypothetical_opening_sl_blocks_without_entry_or_fake_close():
    result = fast_run(
        path([100.0, 98.0]),
        strategy=policy(
            target_mode="none",
            target_steps=(),
            trailing_distance=None,
            stop_mode="fixed_move",
            stop_value=1.0,
        ),
        entry_fill_latency_ms=1000,
    )

    assert result.entries == result.exits == result.protection_events == ()
    assert result.pnl_eur is None
    assert result.exit_reason == "not_closed"
    assert "initial_protection_rejected_unmodeled:sim_1" in result.blockers


def test_duplicate_request_timestamp_blocks_ambiguous_hypothetical_open():
    result = fast_run(
        path([100.0, 100.0], offsets=[0, 0]),
        strategy=policy(target_mode="none", target_steps=()),
    )

    assert result.entries == result.exits == result.protection_events == ()
    assert result.pnl_eur is None
    assert "protection_request_quote_ambiguous:sim_1" in result.blockers


def test_actual_fill_uses_explicit_initial_pair_and_source():
    tape = replace(path([100.0, 99.0, 98.0]), entry_evidence_kind="actual_mt5")
    strategy = policy(
        entry_mode="actual_mt5",
        target_mode="none",
        target_steps=(),
        trailing_distance=None,
    )
    execution = ExecutionAssumptions(
        protection=profile(
            initial_protections=(
                InitialProtection("template", 99.5, 105.0, "observed_mt5"),
            ),
        ),
    )

    result = assert_fast_matches_scalar(tape, strategy, execution)

    opened = result.protection_events[0]
    assert (opened.ticket, opened.sl, opened.tp, opened.reason) == (
        "template",
        99.5,
        105.0,
        "observed_mt5",
    )
    assert result.exits[0].reason == "initial_sl"


def test_management_requests_use_global_ids_and_strictly_later_tick_indices():
    strategy = policy(
        leg_count=2,
        volume_weights=(0.04, 0.03),
        target_steps=(5.0, 6.0),
        trailing_distance=None,
    )
    execution = ExecutionAssumptions(
        protection=profile(processing_delay_ms=0, acknowledgement_delay_ms=0),
    )
    tape = path([100.0, 100.0], offsets=[0, 0])

    # A duplicated opening-request timestamp is ambiguous for hypothetical fills.
    blocked = FastEvaluator(execution=execution)(tape, strategy)
    assert "protection_request_quote_ambiguous:sim_1" in blocked.blockers

    tape = path([100.0, 100.0], offsets=[0, 1])
    result = assert_fast_matches_scalar(tape, strategy, execution)
    requested = [event for event in result.protection_events if event.kind == "requested"]
    installed = [event for event in result.protection_events if event.kind == "installed"]
    assert [event.request_id for event in requested] == [1, 2]
    assert [event.request_id for event in installed] == [1, 2]
    assert all(event.tick_index == 1 for event in installed)


def test_event_budget_keeps_trace_prefix_and_never_synthesizes_an_exit():
    result = fast_run(path([100.0, 100.0, 101.0]), protection=profile(max_events=1))

    assert len(result.entries) == 1
    assert result.exits == ()
    assert [event.kind for event in result.protection_events] == ["open"]
    assert result.pnl_eur is None
    assert "protection_event_budget_exhausted" in result.blockers


def test_blocked_later_opening_cannot_hide_installed_stop_on_same_quote():
    strategy = policy(
        leg_count=2,
        volume_weights=(0.04, 0.04),
        entry_ladder_mode="adverse",
        entry_ladder_step=1.5,
        target_mode="none",
        target_steps=(),
    )
    execution = ExecutionAssumptions(
        entry_fill_latency_ms=1000,
        protection=profile(processing_delay_ms=10_000),
    )

    result = assert_fast_matches_scalar(
        path([100.0, 100.0, 98.5, 60.0]),
        strategy,
        execution,
    )

    assert len(result.entries) == 1
    assert result.exits[0].tick_index == 3
    assert result.exits[0].reason == "initial_sl"
    assert result.pnl_eur is None
    assert "initial_protection_rejected_unmodeled:sim_ladder_2" in result.blockers


def test_actual_entries_with_non_simultaneous_schedule_fail_upfront():
    tape = replace(path([100.0, 100.0]), entry_evidence_kind="actual_mt5")
    strategy = policy(
        entry_mode="actual_mt5",
        leg_count=2,
        volume_weights=(0.04, 0.04),
        entry_ladder_mode="adverse",
        entry_ladder_step=1.5,
        target_mode="none",
        target_steps=(),
    )
    result = fast_run(
        tape,
        strategy=strategy,
        protection=profile(
            initial_protections=(
                InitialProtection("template", None, None, "observed_mt5"),
            ),
        ),
    )

    assert result.entries == ()
    assert "protection_actual_entries_require_simultaneous_schedule" in result.blockers


def test_open_leg_at_data_end_overrides_an_earlier_passive_exit_reason():
    strategy = policy(
        leg_count=2,
        volume_weights=(0.04, 0.04),
        entry_ladder_mode="adverse",
        entry_ladder_step=1.5,
        target_steps=(0.5, 0.5),
        trailing_distance=None,
    )
    execution = ExecutionAssumptions(protection=profile())

    result = assert_fast_matches_scalar(
        path([100.0, 100.0, 101.0, 98.5, 98.5]),
        strategy,
        execution,
    )

    assert len(result.entries) == 2
    assert len(result.exits) == 1
    assert result.exits[0].tick_index == 2
    assert result.exit_reason == "not_closed"
    assert result.pnl_eur is None


@pytest.mark.parametrize(
    ("strategy", "tape"),
    [
        (
            policy(hard_stop_eur_per_leg=1.0),
            path([100.0, 99.0]),
        ),
        (
            policy(profit_lock_arm=2.0, profit_lock_giveback=1.0),
            path([100.0, 101.0, 100.0]),
        ),
        (
            policy(time_exit_min=1, time_exit_mode="loss_only"),
            path([100.0, 99.0], offsets=[0, 60]),
        ),
        (
            policy(provider_management_mode="exact"),
            replace(
                path([100.0, 100.0]),
                provider_events=(
                    ProviderEvent(BASE + timedelta(seconds=1), "CLOSE_ALL", {}),
                ),
            ),
        ),
    ],
    ids=["hard-stop", "profit-lock", "time-exit", "provider-close"],
)
def test_market_close_trigger_blocks_without_closing(strategy, tape):
    result = fast_run(tape, strategy=strategy)

    assert result.entries
    assert result.exits == ()
    assert result.pnl_eur is None
    assert result.exit_reason == "not_closed"
    assert result.blockers[-1] == "protection_market_close_latency_unmodeled"


def test_unsupported_profile_families_fail_closed_before_entry():
    result = fast_run(
        path([100.0, 99.0]),
        strategy=policy(
            target_mode="none",
            target_steps=(),
            stop_mode="basket_money",
            stop_value=1.0,
        ),
    )

    assert result.entries == result.exits == ()
    # a basket money cap is only simulated as client-side market closes
    assert result.blockers == ("basket_money_cap_requires_market_profile",)


def test_profile_requires_two_digit_fixed_point_prices():
    result = fast_run(
        path([100.0, 100.0]),
        protection=ProtectionProfile(0.001, 3, 20, 0, 1000, 1000, 1000),
    )

    assert result.entries == ()
    assert result.blockers == ("fast_path_unsupported:protection_digits",)


def test_legacy_default_result_is_unchanged_when_protection_is_absent():
    tape = path([100.0, 100.0, 101.0])
    strategy = policy()
    execution = ExecutionAssumptions()

    assert FastEvaluator(execution=execution)(tape, strategy) == simulate(
        tape,
        strategy,
        execution=execution,
    )
