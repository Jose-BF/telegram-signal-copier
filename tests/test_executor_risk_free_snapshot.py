from types import SimpleNamespace

import pytest

import executor


def _deal(entry, volume, *, profit=0.0, commission=0.0, swap=0.0, fee=0.0):
    return SimpleNamespace(
        entry=entry,
        volume=volume,
        profit=profit,
        commission=commission,
        swap=swap,
        fee=fee,
    )


def test_snapshot_combines_open_stop_floors_and_closed_realized_pnl(
        monkeypatch):
    buy = getattr(executor.mt5, "POSITION_TYPE_BUY", 0)
    positions = (
        SimpleNamespace(
            ticket=101,
            type=buy,
            symbol="XAUUSD",
            volume=0.01,
            price_open=4360.0,
            price_current=4364.0,
            sl=4350.0,
            tp=4368.0,
            profit=4.0,
            swap=-0.1,
        ),
        SimpleNamespace(
            ticket=102,
            type=buy,
            symbol="XAUUSD",
            volume=0.01,
            price_open=4360.0,
            price_current=4364.0,
            sl=4350.0,
            tp=4375.0,
            profit=4.0,
            swap=0.0,
        ),
    )
    monkeypatch.setattr(executor.mt5, "positions_get", lambda: positions)
    monkeypatch.setattr(
        executor.mt5,
        "order_calc_profit",
        lambda _kind, _symbol, _volume, _entry, _sl: -10.0,
    )
    entry_in = getattr(executor.mt5, "DEAL_ENTRY_IN", 0)
    entry_out = getattr(executor.mt5, "DEAL_ENTRY_OUT", 1)
    histories = {
        101: (
            _deal(entry_in, 0.01),
        ),
        102: (
            _deal(entry_in, 0.01),
        ),
        103: (
            _deal(entry_in, 0.01, commission=-0.1),
            _deal(entry_out, 0.01, profit=6.0),
        ),
    }
    monkeypatch.setattr(
        executor.mt5,
        "history_deals_get",
        lambda *, position: histories.get(position, ()),
    )
    monkeypatch.setattr(
        executor.mt5,
        "account_info",
        lambda: SimpleNamespace(currency="EUR"),
    )

    snapshot = executor.risk_free_basket_snapshot([101, 102, 103])

    assert snapshot["realized_complete"] is True
    assert snapshot["realized_pnl"] == pytest.approx(5.9)
    assert snapshot["account_currency"] == "EUR"
    assert snapshot["missing_realized_tickets"] == []
    assert snapshot["open_legs"] == [
        {
            "ticket": 101,
            "current_pnl": pytest.approx(3.9),
            "stop_pnl": pytest.approx(-10.1),
            "target_distance": pytest.approx(4.0),
            "sl": 4350.0,
            "tp": 4368.0,
        },
        {
            "ticket": 102,
            "current_pnl": pytest.approx(4.0),
            "stop_pnl": pytest.approx(-10.0),
            "target_distance": pytest.approx(11.0),
            "sl": 4350.0,
            "tp": 4375.0,
        },
    ]


def test_snapshot_fails_closed_when_mt5_positions_query_fails(monkeypatch):
    monkeypatch.setattr(executor.mt5, "positions_get", lambda: None)

    assert executor.risk_free_basket_snapshot([101]) is None


def test_snapshot_marks_absent_ticket_without_history_incomplete(monkeypatch):
    monkeypatch.setattr(executor.mt5, "positions_get", lambda: ())
    monkeypatch.setattr(
        executor.mt5, "history_deals_get", lambda *, position: ()
    )
    monkeypatch.setattr(
        executor.mt5,
        "account_info",
        lambda: SimpleNamespace(currency="EUR"),
    )

    snapshot = executor.risk_free_basket_snapshot([999])

    assert snapshot["realized_complete"] is False
    assert snapshot["realized_pnl"] is None
    assert snapshot["missing_realized_tickets"] == [999]


def test_snapshot_counts_realized_costs_from_still_open_positions(
        monkeypatch):
    buy = getattr(executor.mt5, "POSITION_TYPE_BUY", 0)
    monkeypatch.setattr(
        executor.mt5,
        "positions_get",
        lambda: (
            SimpleNamespace(
                ticket=201,
                type=buy,
                symbol="XAUUSD",
                volume=0.01,
                price_open=4360.0,
                price_current=4364.0,
                sl=4350.0,
                tp=4368.0,
                profit=4.0,
                swap=0.0,
            ),
        ),
    )
    monkeypatch.setattr(
        executor.mt5,
        "order_calc_profit",
        lambda *_args: -10.0,
    )
    entry_in = getattr(executor.mt5, "DEAL_ENTRY_IN", 0)
    monkeypatch.setattr(
        executor.mt5,
        "history_deals_get",
        lambda *, position: (
            _deal(entry_in, 0.01, commission=-0.35),
        ),
    )
    monkeypatch.setattr(
        executor.mt5,
        "account_info",
        lambda: SimpleNamespace(currency="EUR"),
    )

    snapshot = executor.risk_free_basket_snapshot([201])

    assert snapshot["realized_complete"] is True
    assert snapshot["realized_pnl"] == pytest.approx(-0.35)


def test_snapshot_fails_closed_without_history_for_open_position(monkeypatch):
    buy = getattr(executor.mt5, "POSITION_TYPE_BUY", 0)
    monkeypatch.setattr(
        executor.mt5,
        "positions_get",
        lambda: (
            SimpleNamespace(
                ticket=202,
                type=buy,
                symbol="XAUUSD",
                volume=0.01,
                price_open=4360.0,
                price_current=4364.0,
                sl=4350.0,
                tp=4368.0,
                profit=4.0,
                swap=0.0,
            ),
        ),
    )
    monkeypatch.setattr(
        executor.mt5,
        "order_calc_profit",
        lambda *_args: -10.0,
    )
    monkeypatch.setattr(
        executor.mt5,
        "history_deals_get",
        lambda *, position: (),
    )
    monkeypatch.setattr(
        executor.mt5,
        "account_info",
        lambda: SimpleNamespace(currency="EUR"),
    )

    snapshot = executor.risk_free_basket_snapshot([202])

    assert snapshot["realized_complete"] is False
    assert snapshot["realized_pnl"] is None
    assert snapshot["missing_realized_tickets"] == [202]
