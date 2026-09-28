from dataclasses import replace

import numpy as np
import pytest

from tests.test_iterative_entry_fill_latency import genome, path, run


@pytest.mark.parametrize("engine", ["scalar", "fast", "oracle"])
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("mode", ["delay", "pullback", "momentum"])
@pytest.mark.parametrize("when,expected_entries", [(59.999, 1), (60., 0), (60.001, 0)])
def test_schema2_entry_window_is_half_open_for_every_trigger(engine, direction, mode, when, expected_entries):
    second = 99. if mode == "pullback" else 101.
    quotes = [100., second]
    if direction == "SELL":
        quotes = [200 - value for value in quotes]
    tape = path(quotes, direction=direction, offsets=[0, when])
    policy = genome(entry_mode=mode, entry_value=when if mode == "delay" else 1.,
                    entry_confirmation_value=None, entry_ladder_mode="simultaneous",
                    entry_ladder_step=None, leg_count=1, volume_weights=(0.04,))
    result = run(engine, tape, policy, delay=0)
    assert len(result.entries) == expected_entries
    assert result.unfilled is (not expected_entries)
    if expected_entries:
        assert result.entries[0].tick_index == 1
        assert result.entries[0].entry_price == quotes[1]


@pytest.mark.parametrize("engine", ["scalar", "fast", "oracle"])
@pytest.mark.parametrize("mode", ["delay", "pullback", "momentum"])
def test_schema1_legacy_inclusive_expiry_is_not_silently_reinterpreted(engine, mode):
    tape = path([100., 99. if mode == "pullback" else 101.], offsets=[0, 60])
    policy = genome(schema_version=1, entry_mode=mode,
                    entry_value=60. if mode == "delay" else 1., entry_confirmation_value=None,
                    entry_ladder_mode="simultaneous", entry_ladder_step=None,
                    leg_count=1, volume_weights=(0.04,), pending_entry_policy="none")
    result = run(engine, tape, policy, delay=0)
    assert len(result.entries) == 1 and result.entries[0].tick_index == 1


@pytest.mark.parametrize("engine", ["scalar", "fast", "oracle"])
def test_delay_never_creates_a_position_on_an_invalid_tick(engine):
    tape = path([100., 99., 101.], offsets=[0, 30, 31])
    broken_bid = tape.bid.copy()
    broken_bid[1] = np.nan
    tape = replace(tape, bid=broken_bid, exit_quotes=broken_bid)
    policy = genome(entry_mode="delay", entry_value=30., entry_confirmation_value=None,
                    entry_ladder_mode="simultaneous", entry_ladder_step=None,
                    leg_count=1, volume_weights=(0.04,))
    result = run(engine, tape, policy, delay=0)
    if engine == "fast":
        assert result.entries == ()
        assert "fast_path_unsupported:bid" in result.blockers
        return
    assert len(result.entries) == 1 and result.entries[0].tick_index == 2
    assert "invalid_tick_at_index:1" not in result.blockers
