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
