from types import SimpleNamespace

import pytest

import executor


def _profit(order_type, _symbol, volume, entry, exit_price):
    direction = 1.0 if order_type == executor.mt5.ORDER_TYPE_BUY else -1.0
    return direction * (exit_price - entry) * volume * 100.0 * 0.9


@pytest.mark.parametrize(
    ("direction", "expected"),
    [("BUY", 3977.78), ("SELL", 4022.22)],
)
def test_loss_stop_price_respects_an_eur_budget(monkeypatch, direction, expected):
    monkeypatch.setattr(
        executor.mt5,
        "symbol_info",
        lambda _symbol: SimpleNamespace(digits=2, point=0.01),
    )
    monkeypatch.setattr(executor.mt5, "order_calc_profit", _profit)

    stop = executor.loss_stop_price(
        direction,
        volume=0.01,
        entry_price=4000.0,
        loss_budget=20.0,
    )

    assert stop == expected
    order_type = (
        executor.mt5.ORDER_TYPE_BUY
        if direction == "BUY"
        else executor.mt5.ORDER_TYPE_SELL
    )
    assert _profit(order_type, "XAUUSD", 0.01, 4000.0, stop) >= -20.01


def test_loss_stop_price_fails_closed_when_mt5_cannot_value_money(monkeypatch):
    monkeypatch.setattr(
        executor.mt5,
        "symbol_info",
        lambda _symbol: SimpleNamespace(digits=2, point=0.01),
    )
    monkeypatch.setattr(
        executor.mt5,
        "order_calc_profit",
        lambda *_args: None,
    )

    assert executor.loss_stop_price(
        "BUY",
        volume=0.01,
        entry_price=4000.0,
        loss_budget=20.0,
    ) is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"direction": "SIDEWAYS", "volume": 0.01, "entry_price": 4000.0, "loss_budget": 20.0},
        {"direction": "BUY", "volume": 0.0, "entry_price": 4000.0, "loss_budget": 20.0},
        {"direction": "BUY", "volume": 0.01, "entry_price": 0.0, "loss_budget": 20.0},
        {"direction": "BUY", "volume": 0.01, "entry_price": 4000.0, "loss_budget": 0.0},
    ],
)
def test_loss_stop_price_rejects_invalid_contracts(kwargs):
    with pytest.raises(ValueError):
        executor.loss_stop_price(**kwargs)
