"""Known-outcome tests for the independent serialized market lifecycle."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from random import Random

import pytest

from research.dubai_iterative.dataset import ProviderEvent
from research.dubai_iterative.engine import ExecutionAssumptions, simulate
from research.dubai_iterative.market_contract import MarketProfile
from research.dubai_iterative.oracle import ExecutionScenario, certify_candidate, oracle_simulate
from research.dubai_iterative.protection_contract import ProtectionProfile
from tests.test_iterative_entry_fill_latency import BASE, BASE_NS, genome, path


def market_profile(**changes):
    return replace(MarketProfile(1000, 1000, 1000, 0.01, 1.0, 0.01), **changes)


def scenario(**changes):
    return replace(ExecutionScenario(
        protection=ProtectionProfile(0.01, 2, 20, 0, 1000, 1000, 1000),
        market=market_profile(),
    ), **changes)


def policy(**changes):
    return genome(
        entry_mode="signal_market", entry_value=None, entry_confirmation_value=None,
        leg_count=1, volume_weights=(0.04,), entry_ladder_mode="simultaneous",
        entry_ladder_step=None, target_mode="none", trailing_distance=30.0,
    ).with_change(**changes)


def run(quotes, *, direction="BUY", offsets=None, strategy=None, execution=None):
    return oracle_simulate(
        path(quotes, direction=direction, offsets=offsets), strategy or policy(),
        execution=execution or scenario(),
    )


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_entry_ack_then_delayed_market_close_and_ack(direction):
    quotes = [100.0, 100.0, 99.0, 98.0, 98.0]
    if direction == "SELL":
        quotes = [200 - price for price in quotes]
    result = run(quotes, direction=direction, strategy=policy(hard_stop_eur_per_leg=1.0))
    assert [(row.kind, row.tick_index) for row in result.market_events] == [
        ("entry_requested", 0), ("entry_filled", 0), ("entry_acknowledged", 1),
        ("close_requested", 2), ("close_filled", 3), ("close_acknowledged", 4),
    ]
    assert result.entries[0].acknowledged_ns == BASE_NS + 1_000_000_000
    assert result.exits[0].tick_index == 3
    assert result.exits[0].reason == "hard_stop_per_leg"
    assert result.pnl_eur == Decimal("-8.80")
    assert result.blockers == ()


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_initial_stop_still_executes_while_entry_ack_is_pending(direction):
    quotes = [100.0, 98.0, 98.0]
    if direction == "SELL":
        quotes = [200 - price for price in quotes]
    result = run(
        quotes, direction=direction, offsets=[0, 1, 3],
        strategy=policy(trailing_distance=1.0),
        execution=scenario(market=market_profile(entry_acknowledgement_delay_ms=3000)),
    )
    assert result.exits[0].tick_index == 1
    assert result.exits[0].reason == "initial_sl"
    assert result.entries[0].acknowledged_ns == BASE_NS + 3_000_000_000
    assert result.market_events[-1].kind == "entry_acknowledged"
    assert result.last_tick_index == 2
    assert result.blockers == ()
    assert not any(row.kind == "requested" for row in result.protection_events)


def test_installed_stop_beats_pending_close_without_double_realization():
    result = run(
        [100.0, 99.5, 98.0, 98.0],
        strategy=policy(trailing_distance=1.0, hard_stop_eur_per_leg=1.0),
        execution=scenario(market=market_profile(entry_acknowledgement_delay_ms=0)),
    )
    assert len(result.exits) == 1
    assert result.exits[0].tick_index == 2 and result.exits[0].reason == "initial_sl"
    assert [row.kind for row in result.market_events][-3:] == [
        "close_requested", "close_rejected", "close_acknowledged",
    ]
    assert result.market_events[-2].price is None
    assert result.market_events[-2].reason == "position_already_closed"
    assert result.pnl_eur == Decimal("-8.80")


@pytest.mark.parametrize("invalid", ["volume", "initial_protection"])
def test_rejected_entry_has_request_and_ack_but_no_fill(invalid):
    execution = scenario()
    strategy = policy()
    if invalid == "volume":
        execution = scenario(market=market_profile(volume_min=0.05))
    else:
        strategy = policy(trailing_distance=0.1)
    result = run([100.0, 100.0], execution=execution, strategy=strategy)
    assert result.entries == result.exits == result.protection_events == ()
    assert [row.kind for row in result.market_events] == [
        "entry_requested", "entry_rejected", "entry_acknowledged",
    ]
    assert result.market_events[1].reason == f"invalid_{invalid}"
    assert result.market_events[1].price is None
    assert result.market_events[-1].price is None


def test_missing_entry_ack_retains_fill_and_passive_exit_prefix():
    result = run(
        [100.0, 98.0], strategy=policy(trailing_distance=1.0),
        execution=scenario(market=market_profile(entry_acknowledgement_delay_ms=10_000)),
    )
    assert len(result.entries) == len(result.exits) == 1
    assert result.entries[0].acknowledged_ns is None
    assert result.exits[0].pnl_eur == Decimal("-8.80")
    assert result.pnl_eur is None and result.blockers
    assert not any(row.kind == "entry_acknowledged" for row in result.market_events)


def test_missing_close_ack_keeps_completed_fill_and_open_remainder_is_not_synthesized():
    result = run(
        [100.0, 99.0, 98.0], strategy=policy(hard_stop_eur_per_leg=1.0),
        execution=scenario(market=market_profile(
            entry_acknowledgement_delay_ms=0, close_acknowledgement_delay_ms=10_000,
        )),
    )
    assert len(result.exits) == 1 and result.exits[0].tick_index == 2
    assert result.market_events[-1].kind == "close_filled"
    assert result.pnl_eur is None and result.blockers
    pending = run([100.0, 99.0], strategy=policy(hard_stop_eur_per_leg=1.0))
    assert pending.entries and pending.exits == ()
    assert pending.pnl_eur is None


def test_ladder_scanning_waits_for_previous_entry_ack():
    result = run(
        [100.0, 98.0, 100.0, 98.0, 98.0, 98.0],
        strategy=policy(
            leg_count=2, volume_weights=(0.04, 0.03),
            entry_ladder_mode="adverse", entry_ladder_step=1.5,
        ),
        execution=scenario(market=market_profile(entry_acknowledgement_delay_ms=2000)),
    )
    requested = [row for row in result.market_events if row.kind == "entry_requested"]
    assert [row.tick_index for row in requested] == [0, 3]
    assert [row.tick_index for row in result.entries] == [0, 3]
    assert [row.acknowledged_ns for row in result.entries] == [
        BASE_NS + 2_000_000_000, BASE_NS + 5_000_000_000,
    ]


def test_provider_close_waits_for_entry_ack_and_cancels_remaining_plan():
    tape = path([100.0] * 6)
    tape = replace(tape, provider_events=(ProviderEvent(BASE + timedelta(seconds=1), "CLOSE_ALL", {}),))
    result = oracle_simulate(
        tape, policy(provider_management_mode="explicit_close_only"),
        execution=scenario(market=market_profile(entry_acknowledgement_delay_ms=3000)),
    )
    assert [row.tick_index for row in result.market_events if row.kind == "close_requested"] == [3]
    assert result.exits[0].tick_index == 4
    assert result.market_events[-1].tick_index == 5


def test_market_trace_budget_never_injects_unrecorded_fill():
    result = run([100.0, 100.0], execution=scenario(market=market_profile(max_events=1)))
    assert [row.kind for row in result.market_events] == ["entry_requested"]
    assert result.entries == result.exits == result.protection_events == ()
    assert "market_event_budget_exhausted" in result.blockers
    assert result.pnl_eur is None


def test_market_requires_explicit_protection_and_hypothetical_schema2():
    missing = run([100.0], execution=scenario(protection=None))
    observed = run([100.0], strategy=policy(entry_mode="actual_mt5"))
    assert missing.blockers and missing.entries == ()
    assert observed.blockers and observed.entries == ()
    assert_scalar_parity(path([100.0]), policy(), scenario(protection=None))
    assert_scalar_parity(path([100.0]), policy(entry_mode="actual_mt5"), scenario())
    assert_scalar_parity(path([100.0]), policy(schema_version=1), scenario())


def test_legacy_scenario_leaves_market_trace_and_acknowledgement_empty():
    result = oracle_simulate(path([100.0, 101.0]), policy())
    assert result.market_events == ()
    assert result.entries[0].acknowledged_ns is None
    assert result.exits[-1].reason == "data_end"


def assert_scalar_parity(tape, strategy, execution):
    scalar = simulate(tape, strategy, execution=ExecutionAssumptions(
        entry_slippage=execution.entry_slippage, exit_slippage=execution.exit_slippage,
        spread_addition=execution.spread_addition, latency_ms=execution.latency_ms,
        entry_fill_latency_ms=execution.entry_fill_latency_ms,
        protection=execution.protection, market=execution.market,
    ))
    certificate = certify_candidate((tape,), strategy, (scalar,), execution=execution)
    assert certificate.mismatches == ()
    assert certificate.promotion_eligible is False
    return certificate


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("case", ["target", "stop_ack", "rejected", "missing_fill", "ladder", "close", "close_stop", "batch"])
def test_known_market_paths_match_scalar_exactly(direction, case):
    quotes = [100.0, 100.0, 100.0, 100.0, 101.0]
    strategy = policy(target_mode="per_leg_steps", target_steps=(0.5,))
    execution = scenario(market=market_profile(entry_acknowledgement_delay_ms=2000))
    provider = False
    if case == "stop_ack":
        quotes = [100.0, 69.0, 69.0, 69.0]
    elif case == "rejected":
        quotes = [100.0, 60.0, 60.0, 60.0]
        execution = replace(execution, entry_fill_latency_ms=1000)
    elif case == "missing_fill":
        quotes = [100.0]
        execution = replace(execution, entry_fill_latency_ms=1000)
    elif case == "ladder":
        quotes = [100.0, 98.0, 100.0, 98.0, 98.0, 98.0]
        strategy = policy(
            leg_count=2, volume_weights=(0.04, 0.03), entry_ladder_mode="adverse", entry_ladder_step=1.5,
        )
    elif case in {"close", "close_stop"}:
        quotes = [100.0, 100.0, 100.0, 99.0 if case == "close" else 69.0, 69.0]
        strategy = policy(provider_management_mode="explicit_close_only")
        execution = scenario(market=market_profile(entry_acknowledgement_delay_ms=0))
        provider = True
    elif case == "batch":
        strategy = policy(leg_count=2, volume_weights=(0.04, 0.03), target_mode="per_leg_steps", target_steps=(0.5, 0.5))
    if direction == "SELL":
        quotes = [200 - price for price in quotes]
    tape = path(quotes, direction=direction)
    if provider:
        tape = replace(tape, provider_events=(ProviderEvent(BASE + timedelta(seconds=2), "CLOSE_ALL", {}),))
    assert_scalar_parity(tape, strategy, execution)


def test_profit_lock_cannot_arm_from_peak_during_entry_ack_wait():
    tape = path([100.0, 110.0, 100.0, 100.0])
    strategy = policy(profit_lock_arm=30.0, profit_lock_giveback=1.0)
    execution = scenario(market=market_profile(entry_acknowledgement_delay_ms=2000))
    result = oracle_simulate(tape, strategy, execution=execution)
    assert result.max_favourable_eur == Decimal("39.20")
    assert not any(row.kind == "close_requested" for row in result.market_events)
    assert_scalar_parity(tape, strategy, execution)


@pytest.mark.parametrize("field,value", [
    ("ticket", "changed"), ("tick_index", 99), ("timestamp_ns", BASE_NS + 1),
    ("request_id", 2), ("kind", "entry_rejected"), ("price", 100.0000000001),
    ("volume", 0.0400000001), ("reason", "changed"),
])
def test_market_certificate_checks_all_trace_fields_without_tolerance(field, value):
    tape = path([100.0, 100.0, 100.0, 101.0])
    strategy = policy(target_mode="per_leg_steps", target_steps=(0.5,))
    execution = scenario()
    result = oracle_simulate(tape, strategy, execution=execution)
    changed = replace(result, market_events=(
        replace(result.market_events[0], **{field: value}), *result.market_events[1:],
    ))
    certificate = certify_candidate((tape,), strategy, (changed,), execution=execution)
    assert [row.field for row in certificate.mismatches] == [f"market_events[0].{field}"]
    assert certificate.status == "blocked" and certificate.promotion_eligible is False


def test_market_certificate_compares_actual_ack_and_never_promotes_profile():
    tape = path([100.0, 100.0, 100.0, 101.0])
    strategy = policy(target_mode="per_leg_steps", target_steps=(0.5,))
    execution = scenario()
    result = oracle_simulate(tape, strategy, execution=execution)
    clean = certify_candidate((tape,), strategy, (result,), execution=execution)
    assert clean.status == "pass" and clean.promotion_eligible is False
    changed = replace(result, entries=(replace(result.entries[0], acknowledged_ns=BASE_NS + 1),))
    mismatch = certify_candidate((tape,), strategy, (changed,), execution=execution)
    assert [row.field for row in mismatch.mismatches] == ["entries[0].acknowledged_ns"]


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_older_installed_tp_executes_while_later_entry_waits_for_ack(direction):
    quotes = [100.0, 100.0, 100.0, 100.0, 100.0, 98.0, 101.0, 100.0, 98.0, 98.0, 99.0]
    if direction == "SELL":
        quotes = [200 - price for price in quotes]
    tape = path(quotes, direction=direction)
    strategy = policy(
        leg_count=2, volume_weights=(0.04, 0.03), entry_ladder_mode="adverse", entry_ladder_step=1.5,
        target_mode="per_leg_steps", target_steps=(0.5, 0.5),
    )
    execution = scenario(market=market_profile(entry_acknowledgement_delay_ms=3000))
    result = oracle_simulate(tape, strategy, execution=execution)
    assert [row.tick_index for row in result.entries] == [0, 5]
    assert result.entries[1].acknowledged_ns == BASE_NS + 8_000_000_000
    assert result.exits[0].ticket == "sim_1" and result.exits[0].tick_index == 6
    assert not any(
        row.ticket == "sim_ladder_2" and row.kind == "requested" and row.tick_index < 8
        for row in result.protection_events
    )
    assert_scalar_parity(tape, strategy, execution)


def test_rejected_batch_leg_does_not_cancel_an_already_requested_leg():
    tape = path([100.0, 100.0, 100.0, 101.0])
    strategy = policy(leg_count=2, volume_weights=(0.02, 0.04), target_mode="per_leg_steps", target_steps=(0.5, 0.5))
    execution = scenario(market=market_profile(volume_min=0.03))
    result = oracle_simulate(tape, strategy, execution=execution)
    assert [row.ticket for row in result.entries] == ["sim_2"]
    assert [row.ticket for row in result.market_events if row.kind == "entry_requested"] == ["sim_1", "sim_2"]
    assert [row.ticket for row in result.market_events if row.kind == "entry_rejected"] == ["sim_1"]
    assert result.pnl_eur == Decimal("2.00") and result.blockers == ()
    assert_scalar_parity(tape, strategy, execution)


@pytest.mark.parametrize("rule", ["profit_lock", "time_exit"])
def test_market_basket_rules_request_then_fill_then_ack(rule):
    execution = scenario(market=market_profile(entry_acknowledgement_delay_ms=0))
    if rule == "profit_lock":
        tape = path([100.0, 101.0, 100.5, 100.5, 100.5])
        strategy = policy(profit_lock_arm=3.0, profit_lock_giveback=1.0)
        request_index, fill_index = 2, 3
    else:
        tape = path([100.0, 100.0, 99.0, 99.0], offsets=[0, 60, 62, 63])
        strategy = policy(time_exit_min=1, time_exit_mode="loss_only")
        request_index, fill_index = 1, 2
    result = oracle_simulate(tape, strategy, execution=execution)
    assert [(row.reason, row.tick_index) for row in result.market_events if row.kind == "close_requested"] == [(rule, request_index)]
    assert [(row.reason, row.tick_index) for row in result.exits] == [(rule, fill_index)]
    assert result.market_events[-1].kind == "close_acknowledged"
    assert_scalar_parity(tape, strategy, execution)


@pytest.mark.parametrize("quotes,entry_blocker", [
    ([100.0, 100.0, 100.0], "entry_request_in_flight_at_strategy_exit"),
    ([100.0, 100.0], "entry_fill_quote_missing"),
])
def test_provider_intent_during_unfilled_entry_blocks_without_later_fill(quotes, entry_blocker):
    tape = replace(path(quotes), provider_events=(
        ProviderEvent(BASE + timedelta(seconds=1), "CLOSE_ALL", {}),
    ))
    strategy = policy(provider_management_mode="explicit_close_only")
    execution = scenario(entry_fill_latency_ms=2000)
    result = oracle_simulate(tape, strategy, execution=execution)
    assert result.entries == result.exits == ()
    assert [row.kind for row in result.market_events] == ["entry_requested"]
    assert "market_close_with_entry_in_flight_unsupported" in result.blockers
    assert entry_blocker in result.blockers
    assert result.pnl_eur is None
    assert_scalar_parity(tape, strategy, execution)


def test_zero_delay_close_requires_a_later_quote_ordinal():
    tape = path([100.0, 99.0, 98.0, 98.0], offsets=[0, 1, 1, 2])
    strategy = policy(hard_stop_eur_per_leg=1.0)
    execution = scenario(market=market_profile(
        entry_acknowledgement_delay_ms=0, close_processing_delay_ms=0,
        close_acknowledgement_delay_ms=0,
    ))
    result = oracle_simulate(tape, strategy, execution=execution)
    close = [row for row in result.market_events if row.kind.startswith("close_")]
    assert [row.tick_index for row in close] == [1, 2, 2]
    assert close[0].timestamp_ns == close[1].timestamp_ns
    assert len(result.exits) == 1
    assert_scalar_parity(tape, strategy, execution)


def test_zero_delay_ladder_rejection_cancels_unrequested_third_leg():
    strategy = policy(
        leg_count=3, volume_weights=(0.04, 0.01, 0.04),
        entry_ladder_mode="adverse", entry_ladder_step=1.5,
        target_mode="per_leg_steps", target_steps=(0.5, 1.0, 1.5),
    )
    execution = scenario(market=market_profile(entry_acknowledgement_delay_ms=0, volume_min=0.02))
    result = run([100.0, 96.0, 65.0, 65.0], strategy=strategy, execution=execution)
    assert len(result.entries) == 1
    assert [row.ticket for row in result.market_events if row.kind == "entry_requested"] == ["sim_1", "sim_ladder_2"]
    assert not any(row.ticket == "sim_ladder_3" for row in result.market_events)
    assert_scalar_parity(path([100.0, 96.0, 65.0, 65.0]), strategy, execution)


def test_passive_exit_cancels_none_policy_even_while_entry_ack_is_pending():
    strategy = policy(
        leg_count=2, volume_weights=(0.04, 0.04), entry_ladder_mode="adverse", entry_ladder_step=1.5,
        pending_entry_policy="none",
    )
    execution = scenario(market=market_profile(entry_acknowledgement_delay_ms=2000))
    result = run([100.0, 69.0, 69.0, 69.0, 38.0, 38.0], strategy=strategy, execution=execution)
    assert len(result.entries) == len(result.exits) == 1
    assert result.exits[0].tick_index == 1 and result.entries[0].acknowledged_ns == BASE_NS + 2_000_000_000
    assert len([row for row in result.market_events if row.kind == "entry_requested"]) == 1
    assert_scalar_parity(path([100.0, 69.0, 69.0, 69.0, 38.0, 38.0]), strategy, execution)


def test_invalid_quote_while_flat_cannot_supply_entry_ack():
    result = run(
        [100.0, 69.0, float("nan")],
        execution=scenario(market=market_profile(entry_acknowledgement_delay_ms=2000)),
    )
    assert len(result.exits) == 1
    assert result.entries[0].acknowledged_ns is None
    assert not any(row.kind == "entry_acknowledged" for row in result.market_events)
    assert "market_lifecycle_incomplete_at_data_end" in result.blockers
    assert result.pnl_eur is None
    assert_scalar_parity(
        path([100.0, 69.0, float("nan")]), policy(),
        scenario(market=market_profile(entry_acknowledgement_delay_ms=2000)),
    )


def test_ack_budget_exhaustion_preserves_installed_sl_on_same_quote():
    result = run(
        [100.0, 100.0, 69.0],
        execution=scenario(market=market_profile(entry_acknowledgement_delay_ms=2000, max_events=2)),
    )
    assert len(result.entries) == len(result.exits) == 1
    assert result.exits[0].tick_index == 2 and result.exits[0].reason == "initial_sl"
    assert result.entries[0].acknowledged_ns is None
    assert "market_event_budget_exhausted" in result.blockers
    assert result.pnl_eur is None
    assert_scalar_parity(
        path([100.0, 100.0, 69.0]), policy(),
        scenario(market=market_profile(entry_acknowledgement_delay_ms=2000, max_events=2)),
    )


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("seed", range(12))
def test_bounded_multi_entry_market_parity(seed, direction):
    rng = Random(seed)
    price = 10000
    quotes = [price / 100]
    for _ in range(23):
        price += rng.choice((-200, -150, -50, 0, 50, 100, 200))
        quotes.append(price / 100)
    if direction == "SELL":
        quotes = [200 - value for value in quotes]
    tape = path(quotes, direction=direction)
    strategy = policy(
        leg_count=3, volume_weights=(0.04, 0.03, 0.02),
        entry_ladder_mode="adverse", entry_ladder_step=1.5,
        target_mode="per_leg_steps", target_steps=(0.5, 1.0, 1.5),
        trailing_distance=5.0,
    )
    execution = scenario(
        entry_fill_latency_ms=(0, 1000)[seed % 2],
        market=market_profile(
            entry_acknowledgement_delay_ms=(0, 1000, 2000)[seed % 3],
            close_processing_delay_ms=(0, 2000)[seed % 2],
            close_acknowledgement_delay_ms=(0, 1000)[seed % 2],
        ),
    )
    assert_scalar_parity(tape, strategy, execution)


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_ladder_request_is_strictly_after_prior_ack_quote(direction):
    quotes = [100.0, 98.0, 98.0, 98.0, 98.0, 98.0]
    if direction == "SELL":
        quotes = [200 - price for price in quotes]
    tape = path(quotes, direction=direction)
    strategy = policy(leg_count=2, volume_weights=(0.04, 0.03), entry_ladder_mode="adverse", entry_ladder_step=1.5)
    execution = scenario(market=market_profile(entry_acknowledgement_delay_ms=2000))
    result = oracle_simulate(tape, strategy, execution=execution)
    requests = [row for row in result.market_events if row.kind == "entry_requested"]
    assert [row.tick_index for row in requests] == [0, 3]
    assert result.entries[0].acknowledged_ns == BASE_NS + 2_000_000_000
    assert_scalar_parity(tape, strategy, execution)


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_requested_stop_and_own_fill_keep_distinct_price_cost_conventions(direction):
    quotes = [100.0, 100.5, 100.5, 100.5, 102.0]
    if direction == "SELL":
        quotes = [200 - price for price in quotes]
    tape = path(quotes, direction=direction)
    strategy = policy(target_mode="per_leg_steps", target_steps=(0.5,))
    execution = scenario(entry_slippage=0.1, exit_slippage=0.05, spread_addition=0.1, entry_fill_latency_ms=1000)
    result = oracle_simulate(tape, strategy, execution=execution)
    assert result.market_events[0].price == 100.0
    assert result.entries[0].entry_price == (100.7 if direction == "BUY" else 99.3)
    assert result.protection_events[0].sl == (70.05 if direction == "BUY" else 129.95)
    assert_scalar_parity(tape, strategy, execution)


def test_oracle_market_lifecycle_never_imports_scalar_execution_implementations():
    import ast
    import inspect
    from research.dubai_iterative import oracle

    tree = ast.parse(inspect.getsource(oracle))
    forbidden = {"engine", "fast_engine", "market", "protection", "broker_execution"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not forbidden.intersection((node.module or "").split("."))
        elif isinstance(node, ast.Import):
            assert not any(forbidden.intersection(alias.name.split(".")) for alias in node.names)


def test_market_close_processes_while_later_entry_waits_and_both_acks_survive():
    tape = path([100.0, 100.0, 99.0, 98.5, 98.5, 98.5, 99.0])
    strategy = policy(
        leg_count=2, volume_weights=(0.04, 0.04), entry_ladder_mode="adverse", entry_ladder_step=1.5,
        hard_stop_eur_per_leg=1.0,
    )
    execution = scenario(market=market_profile(
        entry_acknowledgement_delay_ms=2000, close_acknowledgement_delay_ms=2000,
    ))
    result = oracle_simulate(tape, strategy, execution=execution)
    assert [(row.request_id, row.kind) for row in result.market_events if row.tick_index == 5] == [
        (2, "close_acknowledged"), (3, "entry_acknowledged"),
    ]
    assert result.exits[0].ticket == "sim_1" and result.exits[0].tick_index == 3
    assert result.entries[1].acknowledged_ns == BASE_NS + 5_000_000_000
    assert_scalar_parity(tape, strategy, execution)


def test_missing_fill_preserves_equal_timestamp_request_ordinal_and_price():
    strategy = policy(entry_mode="momentum", entry_value=0.5)
    execution = scenario(entry_fill_latency_ms=1000)
    short_tape = path([100.0, 101.0], offsets=[0, 0])
    extended_tape = path([100.0, 101.0, 101.0], offsets=[0, 0, 1])
    short = oracle_simulate(short_tape, strategy, execution=execution)
    extended = oracle_simulate(extended_tape, strategy, execution=execution)
    assert short.market_events[0] == extended.market_events[0]
    assert short.market_events[0].tick_index == 1
    assert short.market_events[0].price == 101.0
    assert extended.entries == ()
    assert extended.blockers == ("protection_request_quote_ambiguous:sim_1",)
    assert_scalar_parity(short_tape, strategy, execution)
    assert_scalar_parity(extended_tape, strategy, execution)
    ordinal_execution = replace(execution, protection=replace(execution.protection,
                                request_quote_binding="timestamp_and_ordinal"))
    ordinal = oracle_simulate(extended_tape, strategy, execution=ordinal_execution)
    assert ordinal.market_events[0] == short.market_events[0]
    assert ordinal.entries[0].entry_price == 101.0
    assert ordinal.protection_events[0].sl == 71.0
    assert_scalar_parity(extended_tape, strategy, ordinal_execution)


def test_missing_fill_request_never_moves_to_invalid_equal_timestamp_quote():
    tape = path([float("nan"), 100.0], offsets=[0, 0])
    execution = scenario(entry_fill_latency_ms=1000)
    result = oracle_simulate(tape, policy(), execution=execution)
    assert result.market_events[0].tick_index == 1
    assert result.market_events[0].price == 100.0
    assert_scalar_parity(tape, policy(), execution)


@pytest.mark.parametrize("quotes", [
    [100.0, 100.0, 100.0, 102.0, 101.0, 98.0, 98.0, 98.0],
    [100.0, 100.0, 100.0, 102.0, 101.0, 98.0],
])
def test_cancelled_unrequested_ladder_never_becomes_an_inflight_diagnostic(quotes):
    tape = path(quotes)
    strategy = policy(
        leg_count=2, volume_weights=(0.04, 0.04), entry_ladder_mode="adverse", entry_ladder_step=1.5,
        profit_lock_arm=4.0, profit_lock_giveback=1.0,
    )
    execution = scenario(
        entry_fill_latency_ms=2000,
        market=market_profile(
            entry_acknowledgement_delay_ms=0, close_processing_delay_ms=0,
            close_acknowledgement_delay_ms=0,
        ),
        protection=replace(scenario().protection, processing_delay_ms=10_000),
    )
    result = oracle_simulate(tape, strategy, execution=execution)
    assert len(result.entries) == len(result.exits) == 1
    assert result.exits[0].reason == "profit_lock" and result.exits[0].tick_index == 5
    assert not any(row.ticket == "sim_ladder_2" for row in result.market_events)
    assert result.blockers == ()
    assert_scalar_parity(tape, strategy, execution)
