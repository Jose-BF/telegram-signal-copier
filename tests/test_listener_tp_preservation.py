from types import SimpleNamespace

import pytest

import executor
import listener
from state import Signal


def test_open_position_levels_reads_current_mt5_targets(monkeypatch):
    positions = [
        SimpleNamespace(
            ticket=101,
            sl=4380.0,
            tp=4395.0,
            symbol="XAUUSD",
        ),
        SimpleNamespace(
            ticket=202,
            sl=4375.0,
            tp=4401.5,
            symbol="XAUUSD",
        ),
    ]
    monkeypatch.setattr(executor.mt5, "positions_get", lambda: positions)
    monkeypatch.setattr(
        executor.mt5,
        "symbol_info",
        lambda symbol: SimpleNamespace(digits=2, point=0.01),
    )

    assert executor.open_position_levels([101]) == {
        101: {"sl": 4380.0, "tp": 4395.0, "digits": 2, "point": 0.01}
    }


@pytest.mark.asyncio
async def test_apply_sl_tp_preserves_target_already_installed_in_mt5(monkeypatch):
    ticket = 1644451051
    signal = Signal(
        channel="canal2",
        message_id=1359,
        direction="BUY",
        market_ticket=ticket,
        market_fill_price=4390.0,
        tps=[4395.0, 4401.5, 4405.0, 4410.0],
        sl=4385.0,
    )

    monkeypatch.setattr(
        listener.executor,
        "open_position_levels",
        lambda tickets: {
            ticket: {"sl": 4380.0, "tp": 4395.0, "digits": 2, "point": 0.01}
        },
        raising=False,
    )

    async def fake_run(function, *args):
        if function is listener.executor.open_position_levels:
            return function(*args)
        if function.__name__ == "symbol_info_tick":
            return SimpleNamespace(bid=4394.84, ask=4395.04)
        if function.__name__ == "symbol_info":
            return SimpleNamespace(trade_stops_level=30, point=0.01)
        return function(*args)

    queued = []
    events = []
    monkeypatch.setattr(listener, "_run", fake_run)
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_modify_sl",
        lambda sig, item, sl, label="": queued.append(("sl", item, sl)),
    )
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_modify_tp",
        lambda sig, item, tp, label="": queued.append(("tp", item, tp)),
    )
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_modify_sltp",
        lambda sig, item, sl, tp, label="": queued.append(
            ("sltp", item, sl, tp)
        ),
    )
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_close_position",
        lambda *args, **kwargs: queued.append(("close",)),
    )
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig, ev, **fields: events.append((ev, fields)),
    )

    await listener._apply_sl_tp(signal)

    assert queued == [("sl", ticket, 4385.0)]
    assert not any(ev == "tp_chase_advanced" for ev, _ in events)
    assert any(ev == "tp_preserved_installed" for ev, _ in events)


@pytest.mark.asyncio
async def test_apply_sl_tp_keeps_chasing_a_genuinely_missing_target(monkeypatch):
    ticket = 1644451052
    signal = Signal(
        channel="canal2",
        message_id=1360,
        direction="BUY",
        market_ticket=ticket,
        market_fill_price=4390.0,
        tps=[4395.0, 4401.5, 4405.0, 4410.0],
        sl=4385.0,
    )
    monkeypatch.setattr(
        listener.executor,
        "open_position_levels",
        lambda tickets: {
            ticket: {"sl": 4380.0, "tp": 0.0, "digits": 2, "point": 0.01}
        },
    )

    async def fake_run(function, *args):
        if function is listener.executor.open_position_levels:
            return function(*args)
        if function.__name__ == "symbol_info_tick":
            return SimpleNamespace(bid=4394.84, ask=4395.04)
        if function.__name__ == "symbol_info":
            return SimpleNamespace(trade_stops_level=30, point=0.01)
        return function(*args)

    queued = []
    events = []
    monkeypatch.setattr(listener, "_run", fake_run)
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_modify_sltp",
        lambda sig, item, sl, tp, label="": queued.append(
            ("sltp", item, sl, tp)
        ),
    )
    monkeypatch.setattr(listener.journal, "event", lambda sig, ev, **fields: events.append(ev))

    await listener._apply_sl_tp(signal)

    assert queued == [("sltp", ticket, 4385.0, 4410.0)]
    assert "tp_chase_advanced" in events


@pytest.mark.asyncio
async def test_apply_sl_tp_never_chases_or_closes_when_mt5_levels_are_unreadable(
    monkeypatch,
):
    ticket = 1644451053
    signal = Signal(
        channel="canal2",
        message_id=1361,
        direction="BUY",
        market_ticket=ticket,
        market_fill_price=4390.0,
        tps=[4395.0, 4401.5, 4405.0, 4410.0],
        sl=4385.0,
    )

    monkeypatch.setattr(
        listener.executor,
        "open_position_levels",
        lambda tickets: None,
    )

    async def fake_run(function, *args):
        if function is listener.executor.open_position_levels:
            return None
        if function.__name__ == "symbol_info_tick":
            return SimpleNamespace(bid=4412.0, ask=4412.2)
        if function.__name__ == "symbol_info":
            return SimpleNamespace(trade_stops_level=30, point=0.01)
        return function(*args)

    queued = []
    events = []
    monkeypatch.setattr(listener, "_run", fake_run)
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_modify_sl",
        lambda sig, item, sl, label="": queued.append(("sl", item, sl)),
    )
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_modify_tp",
        lambda *args, **kwargs: queued.append(("tp",)),
    )
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_modify_sltp",
        lambda *args, **kwargs: queued.append(("sltp",)),
    )
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_close_position",
        lambda *args, **kwargs: queued.append(("close",)),
    )
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig, ev, **fields: events.append((ev, fields)),
    )

    await listener._apply_sl_tp(signal)

    assert queued == [("sl", ticket, 4385.0)]
    assert not any(ev.startswith("tp_chase_") for ev, _ in events)
    assert any(ev == "position_levels_unavailable_tp_preserved" for ev, _ in events)


@pytest.mark.asyncio
async def test_apply_sl_tp_skips_ticket_that_mt5_reports_as_already_closed(
    monkeypatch,
):
    closed_ticket = 1798506133
    open_ticket = 1798506231
    signal = Signal(
        channel="canal2",
        message_id=1704,
        direction="BUY",
        market_ticket=closed_ticket,
        market_fill_price=4382.42,
        tps=[4383.5, 4386.0, 4389.0, 4410.0],
        sl=4371.0,
    )
    signal.extra_market_tickets.append(open_ticket)
    signal.extra_market_fill_prices.append(4382.36)

    monkeypatch.setattr(
        listener.executor,
        "open_position_levels",
        lambda tickets: {
            open_ticket: {
                "sl": 4371.0,
                "tp": 4386.0,
                "digits": 2,
                "point": 0.01,
            }
        },
    )

    async def fake_run(function, *args):
        if function is listener.executor.open_position_levels:
            return function(*args)
        if function.__name__ == "symbol_info_tick":
            return SimpleNamespace(bid=4383.61, ask=4383.81)
        if function.__name__ == "symbol_info":
            return SimpleNamespace(trade_stops_level=30, point=0.01)
        return function(*args)

    queued = []
    events = []
    monkeypatch.setattr(listener, "_run", fake_run)
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_modify_sl",
        lambda sig, item, sl, label="": queued.append(("sl", item, sl)),
    )
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_modify_tp",
        lambda sig, item, tp, label="": queued.append(("tp", item, tp)),
    )
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_modify_sltp",
        lambda sig, item, sl, tp, label="": queued.append(
            ("sltp", item, sl, tp)
        ),
    )
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_close_position",
        lambda *args, **kwargs: queued.append(("close",)),
    )
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig, ev, **fields: events.append((ev, fields)),
    )

    await listener._apply_sl_tp(signal)

    assert queued == [("sl", open_ticket, 4371.0)]
    assert not any(ev.startswith("tp_chase_") for ev, _ in events)
    assert any(
        ev == "closed_ticket_level_update_skipped"
        and fields["ticket"] == closed_ticket
        for ev, fields in events
    )


@pytest.mark.asyncio
async def test_apply_sl_tp_clears_only_explicit_open_runner_target(monkeypatch):
    tickets = [301, 302, 303, 304, 305]
    signal = Signal(
        channel="canal2",
        message_id=1781,
        direction="BUY",
        market_ticket=tickets[0],
        market_fill_price=4495.0,
        extra_market_tickets=tickets[1:],
        extra_market_fill_prices=[4495.1, 4495.2, 4495.3, 4495.4],
        tps=[4503.0, 4506.0, 4509.0],
        provider_tps=[4503.0, 4506.0, 4509.0],
        has_open_runner=True,
        sl=4488.0,
    )
    monkeypatch.setattr(
        listener.executor,
        "open_position_levels",
        lambda requested: {
            ticket: {
                "sl": 4488.0,
                "tp": (4503.0, 4506.0, 4509.0, 4509.0, 4509.0)[idx],
                "digits": 2,
                "point": 0.01,
            }
            for idx, ticket in enumerate(tickets)
        },
    )

    async def fake_run(function, *args):
        if function is listener.executor.open_position_levels:
            return function(*args)
        if function.__name__ == "symbol_info_tick":
            return SimpleNamespace(bid=4497.0, ask=4497.2)
        if function.__name__ == "symbol_info":
            return SimpleNamespace(trade_stops_level=20, point=0.01)
        return function(*args)

    queued = []
    events = []
    monkeypatch.setattr(listener, "_run", fake_run)
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_modify_sltp",
        lambda sig, ticket, sl, tp, label="": queued.append(
            ("sltp", ticket, sl, tp)
        ),
    )
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_modify_tp",
        lambda sig, ticket, tp, label="": queued.append(("tp", ticket, tp)),
    )
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_modify_sl",
        lambda sig, ticket, sl, label="": queued.append(("sl", ticket, sl)),
    )
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig, ev, **fields: events.append((ev, fields)),
    )

    await listener._apply_sl_tp(signal)

    assert queued == [("sltp", tickets[-1], 4488.0, 0.0)]
    allocation = next(fields for ev, fields in events if ev == "tp_allocation_decided")
    assert allocation["allocations"][-2]["reason"] == "overflow_last_tp"
    assert allocation["allocations"][-1] == {
        "position_index": 4,
        "ticket": tickets[-1],
        "tp": None,
        "reason": "open_runner",
    }
