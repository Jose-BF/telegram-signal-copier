"""A declared holding limit is a causal close request, not an end-of-data fill."""

from decimal import Decimal

import pytest

from tests.test_iterative_money_coverage import close_policy, evaluate, money_tape


@pytest.mark.parametrize("lifecycle", ["legacy", "market"])
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("end_price", [90., 100., 110.])
def test_unconditional_time_limit_closes_winners_and_losers(evaluate, lifecycle, direction, end_price):
    quotes = [100, 100, end_price, end_price, end_price]
    if direction == "SELL":
        quotes = [200 - value for value in quotes]
    tape = money_tape(quotes, offsets=[0, 1, 61, 62, 63], direction=direction, missing=())
    strategy = close_policy(time_exit_min=1, time_exit_mode="always")
    result = evaluate(tape, strategy, lifecycle=lifecycle)
    assert result.blockers == ()
    assert len(result.entries) == len(result.exits) == 1
    assert result.exits[0].reason == "time_exit"
    assert result.exits[0].tick_index == (3 if lifecycle == "market" else 2)
    assert result.pnl_eur == Decimal(str(4 * (end_price - 100) - .8)).quantize(Decimal(".01"))


def test_time_limit_does_not_need_conversion_to_make_the_close_decision(evaluate):
    tape = money_tape([100, 100, 110, 102, 102], offsets=[0, 1, 61, 62, 63])
    result = evaluate(tape, close_policy(time_exit_min=1, time_exit_mode="always"))
    assert result.blockers == ("incomplete_equity_conversion",)
    assert result.exits[0].reason == "time_exit"
    assert result.exits[0].tick_index == 3
    assert result.pnl_eur == Decimal("7.20")
    assert result.max_floating_drawdown_eur is None


def test_timed_close_cannot_hide_an_installed_stop_during_processing(evaluate):
    tape = money_tape([100, 100, 100, 90, 90], offsets=[0, 1, 61, 62, 63], missing=())
    result = evaluate(tape, close_policy(time_exit_min=1, time_exit_mode="always",
                                        stop_mode="fixed_move", stop_value=5.))
    assert result.blockers == ()
    assert len(result.exits) == 1
    assert result.exits[0].reason == "initial_sl"
    assert any(row.kind == "close_rejected" and row.reason == "position_already_closed"
               for row in result.market_events)


def test_time_limit_without_next_quote_stays_open_and_blocked(evaluate):
    tape = money_tape([100, 100, 100], offsets=[0, 1, 61], missing=())
    result = evaluate(tape, close_policy(time_exit_min=1, time_exit_mode="always"))
    assert len(result.entries) == 1 and result.exits == ()
    assert "path_ended_before_strategy_exit" in result.blockers
    assert result.pnl_eur is None
