from datetime import datetime, timedelta

import pytest

import journal
import listener
import position_lifecycle_monitor as monitor
from state import Signal


def _candidate_signal():
    opened_at = datetime(2026, 8, 23, 9, 30, 0)
    signal = Signal(
        channel="canal1",
        message_id=24001,
        direction="BUY",
        timestamp=opened_at,
        market_ticket=7001,
        market_fill_price=4200.0,
    )
    listener._attach_dubai_live_candidate(signal, opened_at)
    return signal, opened_at


def _summary(total_pl, tickets=(7001,), *, complete=True):
    return {
        "positions_complete": True,
        "floating_pl": total_pl,
        "realized_pl": 0.0,
        "realized_complete": complete,
        "total_pl": total_pl if complete else None,
        "pl": total_pl,
        "n_open": len(tickets),
        "open_tickets": list(tickets),
    }


def test_candidate_guard_tracks_dynamic_peak_and_closes_after_two_euro_giveback(
    monkeypatch,
):
    signal, opened_at = _candidate_signal()
    events = []
    closes = []
    monkeypatch.setattr(
        journal,
        "event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )
    monkeypatch.setattr(
        monitor.pending_actions,
        "enqueue_close_position",
        lambda sig, ticket, label, **kwargs: closes.append(
            (ticket, label, kwargs.get("persist_until_signal_close"))
        ),
    )

    armed = monitor._apply_live_basket_guard(
        signal, _summary(10.0), now=opened_at + timedelta(minutes=5),
    )
    advanced = monitor._apply_live_basket_guard(
        signal, _summary(17.25), now=opened_at + timedelta(minutes=10),
    )
    closed = monitor._apply_live_basket_guard(
        signal, _summary(15.24), now=opened_at + timedelta(minutes=11),
    )

    assert armed.action == "arm"
    assert advanced.action == "none"
    assert signal.basket_guard_peak_pl == 17.25
    assert closed.action == "close"
    assert closed.reason == "profit_lock"
    assert closes == [(
        7001,
        "BASKET_GUARD_PROFIT_LOCK #7001",
        True,
    )]
    assert [ev for _, ev, _ in events] == [
        "basket_guard_armed",
        "basket_guard_peak_advanced",
        "basket_guard_triggered",
    ]


def test_candidate_guard_closes_at_minus_twenty_five(monkeypatch):
    signal, opened_at = _candidate_signal()
    closes = []
    monkeypatch.setattr(journal, "event", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        monitor.pending_actions,
        "enqueue_close_position",
        lambda sig, ticket, label, **kwargs: closes.append(
            (ticket, kwargs.get("persist_until_signal_close"))
        ),
    )

    decision = monitor._apply_live_basket_guard(
        signal, _summary(-25.01), now=opened_at + timedelta(minutes=2),
    )

    assert decision.reason == "basket_stop"
    assert closes == [(7001, True)]


def test_candidate_time_exit_only_closes_non_positive_basket(monkeypatch):
    losing, opened_at = _candidate_signal()
    positive, _ = _candidate_signal()
    positive.message_id += 1
    closes = []
    monkeypatch.setattr(journal, "event", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        monitor.pending_actions,
        "enqueue_close_position",
        lambda sig, ticket, label, **kwargs: closes.append(
            (
                sig.message_id,
                ticket,
                kwargs.get("persist_until_signal_close"),
            )
        ),
    )

    loss_decision = monitor._apply_live_basket_guard(
        losing, _summary(-0.01), now=opened_at + timedelta(minutes=40),
    )
    positive_decision = monitor._apply_live_basket_guard(
        positive, _summary(0.01), now=opened_at + timedelta(minutes=40),
    )

    assert loss_decision.reason == "loss_time_exit"
    assert positive_decision.action == "none"
    assert closes == [(24001, 7001, True)]


def test_candidate_does_not_make_profit_decisions_on_incomplete_money(
    monkeypatch,
):
    signal, opened_at = _candidate_signal()
    events = []
    monkeypatch.setattr(
        journal,
        "event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )

    decision = monitor._apply_live_basket_guard(
        signal,
        _summary(12.0, complete=False),
        now=opened_at + timedelta(minutes=45),
    )

    assert decision.action == "evidence_incomplete"
    assert signal.basket_guard_armed is False
    assert events[0][1] == "basket_guard_total_pl_degraded"


def test_candidate_guard_is_sampled_on_every_new_tick():
    assert monitor._basket_guard_sample_due(
        candidate_active=True,
        now_monotonic=10.001,
        last_sample_monotonic=10.0,
        interval_s=0.1,
    ) is True
    assert monitor._basket_guard_sample_due(
        candidate_active=False,
        now_monotonic=10.001,
        last_sample_monotonic=10.0,
        interval_s=0.1,
    ) is False
