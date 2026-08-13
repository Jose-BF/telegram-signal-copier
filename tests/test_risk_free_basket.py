import pytest

from risk_free_basket import BasketLeg, plan_risk_free_basket


def _leg(ticket, current, stop, target):
    return BasketLeg(
        ticket=ticket,
        current_pnl=current,
        stop_pnl=stop,
        target_distance=target,
    )


def test_already_secured_basket_does_not_close_anything():
    plan = plan_risk_free_basket(
        [
            _leg(1, current=5.0, stop=-4.0, target=2.0),
            _leg(2, current=5.0, stop=-4.0, target=8.0),
        ],
        realized_pnl=10.0,
        safety_buffer=1.0,
    )

    assert plan.status == "already_secured"
    assert plan.close_tickets == ()
    assert plan.keep_tickets == (1, 2)
    assert plan.projected_floor == pytest.approx(1.0)


def test_closes_minimum_near_targets_and_keeps_farthest_runner():
    plan = plan_risk_free_basket(
        [
            _leg(1, current=4.0, stop=-10.0, target=1.0),
            _leg(2, current=4.0, stop=-10.0, target=2.0),
            _leg(3, current=4.0, stop=-10.0, target=3.0),
            _leg(4, current=4.0, stop=-10.0, target=4.0),
            _leg(5, current=4.0, stop=-10.0, target=9.0),
        ],
        realized_pnl=0.0,
        safety_buffer=1.0,
    )

    assert plan.status == "secure"
    assert plan.close_tickets == (1, 2, 3, 4)
    assert plan.keep_tickets == (5,)
    assert plan.projected_floor == pytest.approx(5.0)


def test_prior_realized_profit_reduces_required_partial_closes():
    plan = plan_risk_free_basket(
        [
            _leg(10, current=4.0, stop=-4.0, target=1.0),
            _leg(11, current=4.0, stop=-4.0, target=4.0),
            _leg(12, current=4.0, stop=-4.0, target=8.0),
        ],
        realized_pnl=8.0,
        safety_buffer=1.0,
    )

    assert plan.status == "secure"
    assert plan.close_tickets == (10,)
    assert plan.keep_tickets == (11, 12)
    assert plan.projected_floor == pytest.approx(3.0)


def test_insufficient_profit_never_pretends_the_basket_is_risk_free():
    plan = plan_risk_free_basket(
        [
            _leg(1, current=1.0, stop=-10.0, target=1.0),
            _leg(2, current=1.0, stop=-10.0, target=2.0),
            _leg(3, current=1.0, stop=-10.0, target=3.0),
        ],
        realized_pnl=0.0,
        safety_buffer=1.0,
    )

    assert plan.status == "infeasible"
    assert plan.close_tickets == ()
    assert plan.keep_tickets == (1, 2, 3)


def test_missing_stop_evidence_fails_closed():
    plan = plan_risk_free_basket(
        [
            _leg(1, current=8.0, stop=None, target=1.0),
            _leg(2, current=8.0, stop=None, target=8.0),
        ],
        realized_pnl=0.0,
        safety_buffer=1.0,
    )

    assert plan.status == "incomplete_evidence"
    assert plan.close_tickets == ()


def test_leg_without_stop_can_be_closed_when_remaining_floor_is_proved():
    plan = plan_risk_free_basket(
        [
            _leg(1, current=10.0, stop=None, target=1.0),
            _leg(2, current=2.0, stop=-4.0, target=8.0),
        ],
        realized_pnl=0.0,
        safety_buffer=1.0,
    )

    assert plan.status == "secure"
    assert plan.close_tickets == (1,)
    assert plan.keep_tickets == (2,)
    assert plan.projected_floor == pytest.approx(5.0)


def test_missing_realized_profit_evidence_fails_closed():
    plan = plan_risk_free_basket(
        [_leg(1, current=10.0, stop=-1.0, target=8.0)],
        realized_pnl=None,
        safety_buffer=1.0,
    )

    assert plan.status == "incomplete_evidence"
    assert plan.reason == "missing_realized_pnl"
