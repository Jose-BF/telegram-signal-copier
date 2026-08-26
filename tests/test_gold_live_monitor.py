from datetime import datetime
from types import SimpleNamespace

import pytest

import gold_live_candidate
import position_lifecycle_monitor
from state import Signal


def _signal():
    return Signal(
        channel="canal2",
        message_id=2055,
        direction="BUY",
        market_ticket=8101,
        market_fill_price=4200.0,
        live_strategy_id=gold_live_candidate.CANDIDATE_ID,
        live_strategy_fingerprint=gold_live_candidate.CANDIDATE_FINGERPRINT,
        candidate_first_fill_at=datetime(2026, 8, 26, 10, 0, 0),
    )


def test_gold_candidate_enables_the_every_tick_basket_guard():
    assert position_lifecycle_monitor._basket_guard_enabled_for(_signal()) is True


def test_extremes_use_realized_plus_floating_after_partial_closes():
    observed_pl, basis = position_lifecycle_monitor._extremes_observation(
        {
            "pl": 7.5,
            "floating_pl": 7.5,
            "realized_pl": 12.25,
            "realized_complete": True,
            "total_pl": 19.75,
        }
    )

    assert observed_pl == 19.75
    assert basis == "realized_plus_floating_account_currency"


def test_extremes_fail_closed_to_floating_when_realized_money_is_incomplete():
    observed_pl, basis = position_lifecycle_monitor._extremes_observation(
        {
            "pl": -4.5,
            "floating_pl": -4.5,
            "realized_pl": 8.0,
            "realized_complete": False,
            "total_pl": None,
        }
    )

    assert observed_pl == -4.5
    assert basis == "open_floating_account_currency_degraded"


def test_gold_basket_guard_queues_every_open_ticket(monkeypatch):
    signal = _signal()
    signal.extra_market_tickets = [8102]
    queued = []
    monkeypatch.setattr(
        position_lifecycle_monitor.pending_actions,
        "enqueue_close_position",
        lambda signal, ticket, label="", **kwargs: queued.append(
            (ticket, kwargs.get("persist_until_signal_close"))
        ),
    )

    decision = position_lifecycle_monitor._apply_live_basket_guard(
        signal,
        {
            "pl": -100.01,
            "floating_pl": -100.01,
            "realized_pl": 0.0,
            "realized_complete": True,
            "total_pl": -100.01,
            "n_open": 2,
            "open_tickets": [8101, 8102],
            "positions_complete": True,
        },
        now=datetime(2026, 8, 26, 10, 1, 0),
    )

    assert decision.reason == "basket_stop"
    assert queued == [(8101, True), (8102, True)]


@pytest.mark.asyncio
async def test_gold_price_be_uses_each_real_entry_and_persists(monkeypatch):
    signal = _signal()
    signal.extra_market_tickets = [8102]
    requests = []
    monkeypatch.setattr(
        position_lifecycle_monitor.executor,
        "open_entry_prices",
        lambda _tickets: {8101: 4200.0, 8102: 4201.0},
    )
    monkeypatch.setattr(
        position_lifecycle_monitor.pending_actions,
        "enqueue_modify_sl",
        lambda signal, ticket, new_sl, label="", **kwargs: requests.append(
            (ticket, new_sl, kwargs.get("persist_until_signal_close"))
        ),
    )

    moved = await position_lifecycle_monitor._apply_gold_price_be(
        signal,
        SimpleNamespace(bid=4213.1, ask=4213.3),
    )

    assert moved == 2
    assert requests == [(8101, 4200.0, True), (8102, 4201.0, True)]
    assert signal.candidate_be_tickets == [8101, 8102]


@pytest.mark.asyncio
async def test_gold_price_be_waits_until_the_full_twelve_dollar_move(monkeypatch):
    signal = _signal()
    monkeypatch.setattr(
        position_lifecycle_monitor.executor,
        "open_entry_prices",
        lambda _tickets: {8101: 4200.0},
    )
    monkeypatch.setattr(
        position_lifecycle_monitor.pending_actions,
        "enqueue_modify_sl",
        lambda *_args, **_kwargs: pytest.fail("BE must remain unarmed"),
    )

    moved = await position_lifecycle_monitor._apply_gold_price_be(
        signal,
        SimpleNamespace(bid=4211.99, ask=4212.1),
    )

    assert moved == 0
    assert signal.candidate_be_tickets == []
