from dataclasses import replace
from datetime import timedelta

import numpy as np
import pytest

from research.dubai_iterative import engine
from research.dubai_iterative.client import ClientProfile
from research.dubai_iterative.dataset import ProviderEvent
from tests.test_iterative_market import market
from tests.test_iterative_protection import BASE, BASE_NS, path, policy, profile


MODES = {
    "signal_market": (None, None), "delay": (.5, None),
    "pullback": (1., None), "momentum": (1., None),
    "adverse_reversal": (1., 1.5), "no_entry": (None, None),
}


def strategy(mode):
    value, confirmation = MODES[mode]
    return policy(entry_mode=mode, entry_value=value, entry_confirmation_value=confirmation,
                  target_steps=(20.,))


def execution(latency_ms=0):
    return engine.ExecutionAssumptions(latency_ms=latency_ms, protection=profile(),
                                       market=market(), client=ClientProfile())


def test_client_trigger_does_not_search_future_tape(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("the client must observe its trigger incrementally")

    monkeypatch.setattr(engine, "_causal_entry_index", forbidden)
    result = engine.simulate(path([100., 98., 99.5, 100., 100., 100.]),
                             strategy("adverse_reversal"), execution=execution())
    assert result.entries[0].tick_index == 2
    assert result.entries[0].entry_price == 99.5


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("latency_ms", [0, 250, 2000])
def test_incremental_trigger_matches_existing_bulk_rule(mode, direction, latency_ms):
    rng = np.random.default_rng(578)
    genome = strategy(mode)
    assumptions = execution(latency_ms)
    for sample in range(30):
        quotes = 100. + rng.integers(-4, 5, size=8).astype(float)
        if sample % 3 == 0:
            quotes[sample % 8] = np.nan
        tape = path(quotes, direction=direction, offsets=[-1, 0, 0, 1, 2, 59, 60, 61])
        if sample % 5 == 0:
            tape = replace(tape, entry_expiry_anchor_at=BASE - timedelta(seconds=30))
        expiry = engine._entry_expiry_anchor_ns(tape) + genome.entry_expiry_min * 60_000_000_000
        trigger = engine._ClientEntryTrigger(genome, direction,
            BASE_NS + latency_ms * 1_000_000, expiry)
        matched = [i for i, now in enumerate(tape.times_ns)
                   if trigger.observe(int(now), float(tape.bid[i]), float(tape.ask[i]))]
        expected = engine._causal_entry_index(tape, genome, assumptions)
        assert matched == ([] if expected is None else [expected]), (mode, direction, sample)


@pytest.mark.parametrize("mode", MODES)
def test_trigger_prefix_is_unchanged_by_unobserved_quotes(mode):
    genome = strategy(mode)
    prefix = [100., 99., 97., 98.]
    outcomes = []
    for suffix in ([200., 201.], [1., 2.]):
        tape = path(prefix + suffix)
        trigger = engine._ClientEntryTrigger(genome, "BUY", BASE_NS, BASE_NS + 60_000_000_000)
        outcomes.append([trigger.observe(int(tape.times_ns[i]), float(tape.bid[i]), float(tape.ask[i]))
                         for i in range(len(prefix))])
    assert outcomes[0] == outcomes[1]


def test_context_rejection_does_not_try_a_later_trigger():
    tape = path([100., 99., 98., 100., 100.])
    bid = tape.bid.copy()
    bid[2:] = tape.ask[2:]
    tape = replace(tape, bid=bid, exit_quotes=bid)
    genome = strategy("pullback").with_change(context_filter_mode="max_spread", context_filter_value=.1)
    result = engine.simulate(tape, genome, execution=execution())
    assert result.unfilled
    assert not result.entries and not result.client_events


def test_provider_cancel_before_trigger_prevents_entry():
    tape = replace(path([100., 100., 98., 99.5, 100.]), provider_events=(
        ProviderEvent(BASE + timedelta(seconds=1), "CLOSE_ALL", {}),))
    genome = strategy("adverse_reversal").with_change(provider_management_mode="explicit_close_only")
    result = engine.simulate(tape, genome, execution=execution())
    assert result.unfilled and not result.client_events


def test_never_triggered_client_reports_zero_exposure_not_missing_fill():
    result = engine.simulate(path([100., 100., 100.]), strategy("adverse_reversal"), execution=execution())
    assert result.unfilled and result.blockers == ()
    assert result.max_floating_drawdown_eur == 0
    assert result.pnl_eur == 0 and not result.entries
