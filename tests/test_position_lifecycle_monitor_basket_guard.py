import asyncio
from types import SimpleNamespace

import pytest

from state import Signal

import position_lifecycle_monitor as monitor


def _signal(channel="canal1"):
    return Signal(
        channel=channel,
        message_id=900,
        direction="BUY",
        market_ticket=1001,
        extra_market_tickets=[1002, 1003],
    )


def _enable_guard(monkeypatch):
    monkeypatch.setattr(monitor.config, "STRATEGY_C1_BASKET_GUARD_ENABLED", True, raising=False)
    monkeypatch.setattr(monitor.config, "STRATEGY_C1_BASKET_LOSS_CAP", -50.0, raising=False)
    monkeypatch.setattr(monitor.config, "STRATEGY_C1_BASKET_PROFIT_ARM", 30.0, raising=False)
    monkeypatch.setattr(monitor.config, "STRATEGY_C1_BASKET_PROFIT_LOCK", 20.0, raising=False)


def test_live_guard_queues_each_open_ticket_once_and_journals_decision(
        monkeypatch):
    _enable_guard(monkeypatch)
    signal = _signal()
    closes = []
    events = []
    monkeypatch.setattr(
        monitor.pending_actions,
        "enqueue_close_position",
        lambda sig, ticket, label="": closes.append((ticket, label)),
    )
    monkeypatch.setattr(
        monitor.pending_actions,
        "enqueue_cancel_pending",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        monitor,
        "_journal_event",
        lambda sig_id, event, **fields: events.append((sig_id, event, fields)),
        raising=False,
    )
    summary = {
        "pl": -51.25,
        "n_open": 2,
        "open_tickets": [1001, 1003],
        "avg_entry": 4200.0,
        "current_price": 4190.0,
        "lots_total": 0.02,
    }

    first = monitor._apply_live_basket_guard(signal, summary)
    second = monitor._apply_live_basket_guard(signal, summary)

    assert first.action == "close"
    assert second.action == "none"
    assert [ticket for ticket, _ in closes] == [1001, 1003]
    assert signal.status == "open"
    assert signal.basket_guard_triggered is True
    assert signal.basket_guard_close_tickets == [1001, 1003]
    event = next(row for row in events if row[1] == "basket_guard_triggered")
    assert event[2]["reason"] == "loss_cap"
    assert event[2]["observed_pl"] == -51.25
    assert event[2]["open_tickets"] == [1001, 1003]


def test_live_guard_arms_then_closes_profit_and_ignores_gold(monkeypatch):
    _enable_guard(monkeypatch)
    events = []
    closes = []
    monkeypatch.setattr(
        monitor,
        "_journal_event",
        lambda sig_id, event, **fields: events.append((sig_id, event, fields)),
        raising=False,
    )
    monkeypatch.setattr(
        monitor.pending_actions,
        "enqueue_close_position",
        lambda sig, ticket, label="": closes.append(ticket),
    )
    monkeypatch.setattr(
        monitor.pending_actions,
        "enqueue_cancel_pending",
        lambda *args, **kwargs: None,
    )
    dubai = _signal()
    gold = _signal("canal2")

    armed = monitor._apply_live_basket_guard(
        dubai,
        {"pl": 31.0, "n_open": 2, "open_tickets": [1001, 1002]},
    )
    closed = monitor._apply_live_basket_guard(
        dubai,
        {"pl": 19.0, "n_open": 2, "open_tickets": [1001, 1002]},
    )
    ignored = monitor._apply_live_basket_guard(
        gold,
        {"pl": -100.0, "n_open": 2, "open_tickets": [1001, 1002]},
    )

    assert armed.action == "arm"
    assert closed.reason == "profit_lock"
    assert ignored.action == "none"
    assert closes == [1001, 1002]
    assert [event for _, event, _ in events] == [
        "basket_guard_armed",
        "basket_guard_triggered",
    ]


@pytest.mark.asyncio
async def test_runtime_monitor_samples_live_dubai_basket(monkeypatch):
    _enable_guard(monkeypatch)
    monkeypatch.setattr(
        monitor.config,
        "STRATEGY_C1_BASKET_GUARD_POLL_S",
        0.1,
        raising=False,
    )
    signal = _signal()
    samples = []
    summary = {
        "pl": -12.0,
        "n_open": 3,
        "open_tickets": [1001, 1002, 1003],
    }
    monkeypatch.setattr(
        monitor.mt5,
        "symbol_info_tick",
        lambda _symbol: SimpleNamespace(
            time_msc=1,
            bid=4200.0,
            ask=4200.1,
        ),
    )
    monkeypatch.setattr(
        monitor,
        "_floating_pl_summary",
        lambda _signal: summary,
    )

    def apply_guard(current_signal, current_summary):
        samples.append((current_signal, current_summary))
        current_signal.status = "closed"
        return SimpleNamespace(
            action="none",
            reason=None,
            observed_pl=current_summary["pl"],
        )

    monkeypatch.setattr(monitor, "_apply_live_basket_guard", apply_guard)

    await asyncio.wait_for(monitor.run(signal, []), timeout=0.25)

    assert samples == [(signal, summary)]


def test_guard_retries_after_partial_queue_failure(monkeypatch):
    _enable_guard(monkeypatch)
    signal = _signal()
    summary = {
        "pl": -51.0,
        "n_open": 2,
        "open_tickets": [1001, 1003],
    }

    def fail_first_enqueue(*_args, **_kwargs):
        raise RuntimeError("spool unavailable")

    monkeypatch.setattr(
        monitor.pending_actions,
        "enqueue_close_position",
        fail_first_enqueue,
    )
    monkeypatch.setattr(
        monitor.pending_actions,
        "enqueue_cancel_pending",
        lambda *args, **kwargs: None,
    )

    with pytest.raises(RuntimeError, match="spool unavailable"):
        monitor._apply_live_basket_guard(signal, summary)

    assert signal.basket_guard_triggered is True
    assert signal.basket_guard_recovery_pending is True

    queued = []
    monkeypatch.setattr(
        monitor.pending_actions,
        "enqueue_close_position",
        lambda _signal, ticket, label="": queued.append(ticket),
    )
    monkeypatch.setattr(
        monitor,
        "_journal_event",
        lambda *args, **kwargs: None,
    )

    recovered = monitor._apply_live_basket_guard(signal, summary)

    assert recovered.reason == "recovery"
    assert queued == [1001, 1003]
    assert signal.basket_guard_recovery_pending is False
