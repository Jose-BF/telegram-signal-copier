from dataclasses import replace

import pytest

from research.dubai_iterative.engine import ExecutionAssumptions, simulate
from research.dubai_iterative.protection_contract import InitialProtection, ProtectionProfile
from tests.test_iterative_entry_fill_latency import BASE, BASE_NS, genome, path


def profile(**changes):
    return replace(ProtectionProfile(0.01, 2, 20, 0, 1000, 1000, 1000), **changes)


def policy(**changes):
    base = genome(entry_mode="signal_market", entry_value=None, entry_confirmation_value=None,
                  leg_count=1, volume_weights=(0.04,), entry_ladder_mode="simultaneous", entry_ladder_step=None,
                  target_mode="per_leg_steps", target_steps=(0.5,), trailing_distance=30.)
    return base.with_change(**changes)


def run(tape, *, execution=None, strategy=None):
    return simulate(tape, strategy or policy(), execution=ExecutionAssumptions(protection=execution or profile()))


def test_target_crossed_before_installation_does_not_close_at_desired_tp():
    result = run(path([100., 101., 100., 101.]))
    assert result.exits == () and result.pnl_eur is None
    assert "path_ended_before_strategy_exit" in result.blockers
    rejected = [event for event in result.protection_events if event.kind == "rejected"]
    assert rejected[0].tick_index == 1 and rejected[0].reason == "invalid_stops"


def test_initial_sl_remains_active_while_modify_is_pending():
    result = run(path([100., 99., 69.]), execution=profile(processing_delay_ms=10_000))
    assert result.exits[0].tick_index == 2 and result.exits[0].reason == "initial_sl"
    assert not any(event.kind == "installed" for event in result.protection_events)


def test_installation_is_active_before_acknowledgement():
    result = run(path([100., 100., 101.]), execution=profile(acknowledgement_delay_ms=10_000))
    assert result.exits[0].tick_index == 2 and result.exits[0].reason == "per_leg_target"
    assert [event.kind for event in result.protection_events] == ["open", "requested", "installed", "closed"]


def test_rejected_pair_does_not_install_tightened_sl():
    tape = path([100., 110., 65.])
    tape = replace(tape, entry_evidence_kind="actual_mt5", legs=(replace(tape.legs[0], open_price=100.),))
    result = run(tape, strategy=policy(entry_mode="actual_mt5"),
                 execution=profile(acknowledgement_delay_ms=10_000,
                                   initial_protections=(InitialProtection("template", 50., None, "native_open_request"),)))
    assert result.exits == () and result.pnl_eur is None
    assert any(event.kind == "rejected" for event in result.protection_events)


def test_observed_fill_requires_explicit_initial_protection():
    tape = replace(path([100., 100.]), entry_evidence_kind="actual_mt5")
    result = run(tape, strategy=policy(entry_mode="actual_mt5"))
    assert result.entries == ()
    assert "initial_protection_missing:template" in result.blockers


def test_hypothetical_fill_cannot_inherit_observed_protection():
    result = run(path([100., 100.]), execution=profile(initial_protections=(InitialProtection("template", 90., None, "mt5"),)))
    assert "observed_protection_requires_actual_entries" in result.blockers


def test_unknown_freeze_semantics_fail_closed():
    result = run(path([100., 100.]), execution=profile(freeze_level_points=1))
    assert "protection_freeze_semantics_unsupported" in result.blockers


@pytest.mark.parametrize("changes", [
    {"point": True}, {"digits": True}, {"max_events": 0}, {"max_events": 1_000_001},
    {"processing_delay_ms": 86_400_001}, {"acknowledgement_delay_ms": -1},
    {"retry_delay_ms": 1.5}, {"point": .1},
])
def test_profile_rejects_ambiguous_or_unbounded_parameters(changes):
    with pytest.raises(ValueError):
        profile(**changes)


def test_trace_budget_keeps_prefix_and_never_synthesizes_exit():
    result = run(path([100., 100., 101.]), execution=profile(max_events=1))
    assert len(result.entries) == 1 and result.exits == ()
    assert [row.kind for row in result.protection_events] == ["open"]
    assert "protection_event_budget_exhausted" in result.blockers


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_retry_waits_for_ack_and_uses_latest_causal_intent(direction):
    quotes = [100., 101., 100., 100., 100., 101.]
    if direction == "SELL":
        quotes = [200 - value for value in quotes]
    result = run(path(quotes, direction=direction))
    assert [row.tick_index for row in result.protection_events if row.kind == "requested"] == [0, 3]
    assert [row.tick_index for row in result.protection_events if row.kind == "installed"] == [4]
    assert result.exits[0].tick_index == 5 and result.exits[0].reason == "per_leg_target"
    requested = [row for row in result.protection_events if row.kind == "requested"]
    assert requested[1].sl == (70.8 if direction == "BUY" else 129.2)


def test_duplicate_management_timestamp_preserves_quote_order():
    tape = path([100., 100., 101.], offsets=[0, .1, .1])
    result = run(tape, execution=profile(processing_delay_ms=0, acknowledgement_delay_ms=0, retry_delay_ms=0))
    assert result.exits[0].tick_index == 2
    assert next(row for row in result.protection_events if row.kind == "installed").tick_index == 1


def test_ambiguous_open_request_quote_is_blocked_not_guessed():
    result = run(path([100., 101., 102.], offsets=[0, 0, 1]))
    assert result.entries == () and result.pnl_eur is None
    assert any("protection_request_quote_ambiguous" in row for row in result.blockers)


def test_passive_installed_stop_wins_over_due_modification():
    result = run(path([100., 69.]), execution=profile(processing_delay_ms=1000))
    assert result.exits[0].reason == "initial_sl"
    assert not any(row.kind in {"installed", "rejected"} for row in result.protection_events)


def test_unresolved_future_trace_budget_cannot_erase_a_closed_prefix():
    prefix = run(path([100., 100., 101.]), execution=profile(max_events=5))
    extended = run(path([100., 100., 101., 50., 40.]), execution=profile(max_events=5))
    assert prefix == extended


def test_installed_target_wins_over_provider_market_close_at_same_quote():
    from datetime import timedelta
    from research.dubai_iterative.dataset import ProviderEvent
    tape = path([100., 100., 101.])
    tape = replace(tape, provider_events=(ProviderEvent(BASE + timedelta(seconds=2), "CLOSE_ALL", {}),))
    result = run(tape, strategy=policy(provider_management_mode="explicit_close_only"))
    assert result.exits[0].reason == "per_leg_target" and result.blockers == ()


def test_market_close_is_blocked_without_inventing_execution():
    from datetime import timedelta
    from research.dubai_iterative.dataset import ProviderEvent
    tape = path([100., 100., 100.])
    tape = replace(tape, provider_events=(ProviderEvent(BASE + timedelta(seconds=2), "CLOSE_ALL", {}),))
    result = run(tape, strategy=policy(provider_management_mode="explicit_close_only"))
    assert result.exits == () and result.pnl_eur is None
    assert "protection_market_close_latency_unmodeled" in result.blockers


def test_mt5_2640_rejection_values_preserve_old_levels_at_later_quote():
    from research.dubai_iterative.protection import ProtectionBook
    # Retained attempt_f1d53cbd03ca472e99d9cc0df5292346 and the following
    # attempt_014fa1c1c30f48d29520713ededabd51. The later client quote is a
    # regression stimulus, not an assertion of the unobserved server quote.
    book = ProtectionBook(profile(processing_delay_ms=0), "SELL")
    book.open("1961398996", 0, BASE_NS, 4423.28, None, "native_open_request")
    assert book.valid(4394.54, 4423.54, 4394.20)
    book.request("1961398996", 0, BASE_NS, 4423.54, 4394.20, "trailing_stop", "per_leg_target")
    book.process(1, BASE_NS + 1_000_000_000, 4393.88)
    assert book.events[-1].kind == "rejected"
    assert book.events[-1].reason == "invalid_stops"
    assert book.states["1961398996"].sl == 4423.28
    assert book.states["1961398996"].tp is None
    assert book.hit("1961398996", 4393.88) is None


def test_blocked_later_opening_cannot_hide_installed_stop_on_same_quote():
    strategy = policy(leg_count=2, volume_weights=(.04, .04), target_steps=(.5, 1.),
                      entry_ladder_mode="adverse", entry_ladder_step=1.5)
    result = simulate(path([100., 100., 98.5, 60.]), strategy,
                      execution=ExecutionAssumptions(entry_fill_latency_ms=1000,
                                                     protection=profile(processing_delay_ms=10_000)))
    assert len(result.entries) == 1 and len(result.exits) == 1
    assert result.exits[0].tick_index == 3 and result.exits[0].reason == "initial_sl"
    assert result.pnl_eur is None
    assert any("initial_protection_rejected_unmodeled" in row for row in result.blockers)


def test_observed_entries_with_counterfactual_ladder_block_before_lookup():
    from datetime import timedelta
    tape = path([100., 98., 98.])
    tape = replace(tape, entry_evidence_kind="actual_mt5", legs=(
        replace(tape.legs[0], open_price=100.),
        replace(tape.legs[0], ticket="second", opened_at=BASE + timedelta(seconds=1), open_price=98.),
    ))
    strategy = policy(entry_mode="actual_mt5", leg_count=2, volume_weights=(.04, .04),
                      target_steps=(.5, 1.), entry_ladder_mode="adverse", entry_ladder_step=1.5)
    result = run(tape, strategy=strategy, execution=profile(initial_protections=(
        InitialProtection("template", 70., None, "mt5"), InitialProtection("second", 68., None, "mt5"))))
    assert result.entries == ()
    assert "protection_actual_entries_require_simultaneous_schedule" in result.blockers
