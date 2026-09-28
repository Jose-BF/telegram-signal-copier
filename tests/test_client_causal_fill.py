from dataclasses import replace

import pytest

from research.dubai_iterative import engine
from research.dubai_iterative.client import ClientProfile
from tests.test_iterative_market import market
from tests.test_iterative_protection import path, policy, profile
from tests.test_iterative_resume import drain


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("price_ms", [None, 0, 1000, 2000])
def test_client_prices_only_quotes_at_or_before_processed_boundary(monkeypatch, direction, price_ms):
    quotes = [100., 99., 98., 98., 98., 98.]
    if direction == "SELL":
        quotes = [200. - quote for quote in quotes]
    tape = path(quotes, direction=direction)
    execution = engine.ExecutionAssumptions(
        entry_fill_latency_ms=2000, protection=profile(), market=market(),
        client=ClientProfile(entry_price_delay_ms=price_ms))
    reads = []
    original = engine._entry_quote

    def quote(tape, index):
        reads.append(index)
        return original(tape, index)

    monkeypatch.setattr(engine, "_entry_quote", quote)
    steps = engine._simulation_steps(tape, policy(target_steps=(20.,)), execution=execution)
    current = next(steps)
    while True:
        reads.clear()
        try:
            following = next(steps)
        except StopIteration as finished:
            result = finished.value
            assert all(index <= current.tick_index for index in reads)
            break
        assert all(index <= current.tick_index for index in reads), (current, reads)
        current = following
    assert result.entries[0].tick_index == 2
    expected_index = 2 if price_ms is None else price_ms // 1000
    assert result.entries[0].price_tick_index == expected_index
    assert result.entries[0].entry_price == quotes[expected_index]


def test_client_missing_fill_keeps_intent_without_forward_search(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("a client request must wait for quotes, not search its future fill")

    monkeypatch.setattr(engine, "_entry_fill_index", forbidden)
    result = engine.simulate(path([100.]), policy(), execution=engine.ExecutionAssumptions(
        entry_fill_latency_ms=1000, protection=profile(), market=market(), client=ClientProfile()))
    assert not result.entries
    assert [event.kind for event in result.market_events] == ["entry_requested"]
    assert "entry_fill_quote_missing" in result.blockers
    assert "client_lifecycle_incomplete_at_data_end" in result.blockers


@pytest.mark.parametrize("price_ms", [0, 500, 1000, None])
def test_unusable_quote_is_not_used_as_delayed_price_or_fill(price_ms):
    tape = path([100., 99., 98., 97., 97., 97., 97.])
    ask = tape.ask.copy()
    ask[1] = float("nan")
    tape = replace(tape, ask=ask)
    result = engine.simulate(tape, policy(target_steps=(20.,)), execution=engine.ExecutionAssumptions(
        entry_fill_latency_ms=1000, protection=profile(), market=market(),
        client=ClientProfile(entry_price_delay_ms=price_ms)))
    assert result.entries[0].tick_index == 2
    assert result.entries[0].price_tick_index == (0 if price_ms == 0 else 2)


def test_duplicate_timestamp_does_not_move_zero_delay_fill_to_previous_ordinal():
    tape = path([100., 100., 100., 100., 100., 96., 99., 100., 100., 100.],
                offsets=[0, 1, 2, 3, 4, 5, 5, 7, 8, 9])
    strategy = policy(leg_count=3, volume_weights=(.04,) * 3, target_steps=(20.,) * 3,
                      entry_ladder_mode="adverse", entry_ladder_step=1.5)
    execution = engine.ExecutionAssumptions(protection=profile(), market=market(), client=ClientProfile())
    boundaries, result = drain(engine._simulation_steps(tape, strategy, execution=execution))
    assert [boundary.tick_index for boundary in boundaries] == list(range(len(boundaries)))
    assert result.entries[1].tick_index == 5
    assert result.entries[1].price_tick_index == 5
    assert result.entries[1].entry_price == 96.
