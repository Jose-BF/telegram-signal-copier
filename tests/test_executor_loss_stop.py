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


@pytest.mark.parametrize(
    ("direction", "positions", "expected"),
    [
        (
            "BUY",
            [
                {"entry": 4200.0, "volume": 0.01, "symbol": "XAUUSD"},
                {"entry": 4196.0, "volume": 0.04, "symbol": "XAUUSD"},
            ],
            4191.25,
        ),
        (
            "SELL",
            [
                {"entry": 4200.0, "volume": 0.01, "symbol": "XAUUSD"},
                {"entry": 4204.0, "volume": 0.04, "symbol": "XAUUSD"},
            ],
            4208.75,
        ),
    ],
)
def test_basket_loss_stop_price_caps_the_combined_mt5_loss(
    monkeypatch,
    direction,
    positions,
    expected,
):
    monkeypatch.setattr(
        executor.mt5,
        "symbol_info",
        lambda _symbol: SimpleNamespace(digits=2, point=0.01),
    )
    monkeypatch.setattr(executor.mt5, "order_calc_profit", _profit)

    stop = executor.basket_loss_stop_price(
        direction,
        positions,
        loss_budget=25.0,
    )

    assert stop == expected
    order_type = (
        executor.mt5.ORDER_TYPE_BUY
        if direction == "BUY"
        else executor.mt5.ORDER_TYPE_SELL
    )
    total = sum(
        _profit(
            order_type,
            row["symbol"],
            row["volume"],
            row["entry"],
            stop,
        )
        for row in positions
    )
    assert -25.01 <= total <= -24.95


def test_basket_loss_stop_price_rejects_mixed_symbols(monkeypatch):
    monkeypatch.setattr(
        executor.mt5,
        "symbol_info",
        lambda _symbol: SimpleNamespace(digits=2, point=0.01),
    )

    with pytest.raises(ValueError, match="same symbol"):
        executor.basket_loss_stop_price(
            "BUY",
            [
                {"entry": 4200.0, "volume": 0.01, "symbol": "XAUUSD"},
                {"entry": 4196.0, "volume": 0.04, "symbol": "EURUSD"},
            ],
            loss_budget=25.0,
        )
