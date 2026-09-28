"""Closure classification uses native roles and stable deal IDs on clock ties."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

import position_lifecycle_monitor as monitor
from state import Signal
from tests.test_runtime_replay_close import ack, close, command, opening
from tests.test_runtime_replay_history import history_model


def signal_for(ticket=1001, direction="BUY"):
    signal = Signal(channel="canal2", message_id=98766, direction=direction)
    signal.market_ticket = ticket
    signal.sl = 90. if direction == "BUY" else 110.
    signal.tps = [105. if direction == "BUY" else 95.]
    return signal


def classify(monkeypatch, rows, signal=None):
    monkeypatch.setattr(monitor.mt5, "history_deals_get",
                        lambda **kwargs: tuple(SimpleNamespace(**row) for row in rows))
    return monitor._classify_closures(signal or signal_for())


def deal(ticket, entry, *, time_msc=100, price=100., volume=.04, reason=3, profit=0.):
    return dict(ticket=ticket, entry=entry, time_msc=time_msc, price=price,
                volume=volume, reason=reason, profit=profit, commission=0., swap=0., fee=0., comment="")


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("reverse", [False, True])
def test_actual_engine_same_quote_open_close_is_classified(monkeypatch, direction, reverse):
    with history_model(direction) as owner:
        position = opening(owner, direction)
        receipt = close(owner, command(owner, position, direction))
        assert ack(owner, receipt)
        rows = owner.broker.runtime_control.history(position)
        assert len(rows) == 2 and rows[0]["time_msc"] == rows[1]["time_msc"]
        assert rows[0]["ticket"] != rows[1]["ticket"]
        before = deepcopy(rows)
        closures = classify(monkeypatch, list(reversed(rows)) if reverse else rows,
                            signal_for(position, direction))
        assert len(closures) == 1
        assert closures[0]["exit_price"] == pytest.approx(receipt.price)
        assert closures[0]["pnl"] == pytest.approx(-.8)
        assert closures[0]["broker_close_reason"] == "bot_close"
        assert owner.broker.runtime_control.history(position) == before


@pytest.mark.parametrize("order", [(0, 1, 2), (2, 1, 0), (1, 0, 2)])
@pytest.mark.parametrize("exit_role", [1, 3])
def test_last_partial_exit_uses_deal_id_for_equal_timestamps(monkeypatch, order, exit_role):
    rows = [deal(10, 0, time_msc=99),
            deal(20, exit_role, price=105., volume=.02, reason=5, profit=10.),
            deal(30, exit_role, price=90., volume=.02, reason=4, profit=-20.)]
    closure, = classify(monkeypatch, [rows[i] for i in order])
    assert closure["exit_price"] == 90.
    assert closure["closed_by_tag"] == "SL"
    assert closure["broker_deal_reason"] == 4
    assert closure["pnl"] == -10.


@pytest.mark.parametrize("rows", [
    [deal(1, 0)],
    [deal(1, 0), deal(2, 0, time_msc=101)],
    [deal(1, 1), deal(2, 1, time_msc=101)],
    [deal(1, 0), deal(2, 1, time_msc=101, volume=.02)],
    [deal(1, 0), deal(2, 1, time_msc=101, volume=.05)],
    [deal(1, 0), deal(2, 2, time_msc=101)],
    [deal(1, 0), deal(2, None, time_msc=101)],
    [deal(1, 0), deal(2, True, time_msc=101)],
    [deal(1, 0), deal(2, 1, time_msc=99)],
    [deal(1, 0), deal(1, 1, time_msc=101)],
    [deal(1, 0), deal(2, 1, time_msc=101, volume=float("nan"))],
], ids=["one_open", "two_opens", "no_open", "still_partial", "excess_exit", "reversal",
        "unknown_role", "boolean_role", "exit_before_open", "duplicate_id", "unknown_volume"])
def test_incomplete_or_ambiguous_typed_history_is_not_a_closed_position(monkeypatch, rows):
    assert classify(monkeypatch, rows) == []


@pytest.mark.parametrize("field", ["entry", "volume", "ticket", "time_msc"])
def test_partially_typed_or_incomplete_records_cannot_fall_back_to_legacy(monkeypatch, field):
    rows = [deal(1, 0), deal(2, 1, time_msc=101)]
    del rows[1][field]
    assert classify(monkeypatch, rows) == []


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("same_clock", [False, True])
def test_legacy_history_without_entry_preserves_existing_classification(monkeypatch, reverse, same_clock):
    rows = [deal(10, 0), deal(11, 1, time_msc=100 if same_clock else 101,
                            price=105., reason=5, profit=20.)]
    for row in rows:
        del row["entry"]
        del row["volume"]
    closure, = classify(monkeypatch, list(reversed(rows)) if reverse else rows)
    assert closure["exit_price"] == 105. and closure["pnl"] == 20.
    assert closure["broker_close_reason"] == "tp"


def test_ambiguous_ticket_does_not_hide_later_complete_ticket(monkeypatch):
    signal = signal_for()
    signal.dca_tickets = [1002]
    histories = {1001: [deal(1, 0), deal(2, 1, volume=.02)],
                 1002: [deal(3, 0), deal(4, 1, price=105., reason=5, profit=20.)]}
    monkeypatch.setattr(monitor.mt5, "history_deals_get", lambda *, position:
                        tuple(SimpleNamespace(**row) for row in histories[position]))
    closures = monitor._classify_closures(signal)
    assert [row["ticket"] for row in closures] == [1002]
