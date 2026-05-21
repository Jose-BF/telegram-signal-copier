"""
test_market_context.py — Tests del snapshot de mercado al entrar.

compute_market_context(symbol) captura {atr_m5_14, recent_5m_range,
current_price_at_signal} desde MT5. Defensivo: si MT5 falla devuelve None
(el listener lo loguea como null sin bloquear la apertura).
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import market_context


@pytest.fixture
def mt5_mock(monkeypatch):
    """Sustituye market_context.mt5 por un MagicMock — sin tocar MT5 real."""
    mt5 = MagicMock()
    monkeypatch.setattr(market_context, "mt5", mt5)
    return mt5


def _bar(high, low, close=None):
    """Barra MT5-style: solo necesitamos .high y .low para el ATR."""
    return SimpleNamespace(high=high, low=low,
                           close=close if close is not None else (high + low) / 2)


class TestComputeMarketContext:

    def test_happy_path(self, mt5_mock):
        # 14 barras M5 con (high-low) = 1.0 cada una → ATR = 1.0
        bars = [_bar(2001.0, 2000.0) for _ in range(14)]
        mt5_mock.copy_rates_from_pos.return_value = bars
        mt5_mock.symbol_info_tick.return_value = SimpleNamespace(
            bid=2000.5, ask=2000.7)
        ctx = market_context.compute_market_context("XAUUSD")
        assert ctx is not None
        assert abs(ctx["atr_m5_14"] - 1.0) < 0.01
        assert ctx["recent_5m_range"] == [2000.0, 2001.0]
        assert ctx["current_price_at_signal"] == 2000.6  # mid bid/ask

    def test_variable_atr(self, mt5_mock):
        # Mix de rangos: [2.0, 1.0, 0.5, ...] → ATR = media
        bars = [_bar(100.0 + r, 100.0) for r in [2.0, 1.0, 0.5, 1.5, 2.5,
                                                    1.0, 0.5, 1.0, 1.5, 2.0,
                                                    0.8, 1.2, 1.0, 1.0]]
        mt5_mock.copy_rates_from_pos.return_value = bars
        mt5_mock.symbol_info_tick.return_value = SimpleNamespace(
            bid=100.0, ask=100.0)
        ctx = market_context.compute_market_context("XAUUSD")
        expected_atr = sum([2.0, 1.0, 0.5, 1.5, 2.5, 1.0, 0.5, 1.0, 1.5,
                            2.0, 0.8, 1.2, 1.0, 1.0]) / 14
        assert abs(ctx["atr_m5_14"] - expected_atr) < 0.01

    def test_mt5_returns_none_returns_none(self, mt5_mock):
        mt5_mock.copy_rates_from_pos.return_value = None
        assert market_context.compute_market_context("XAUUSD") is None

    def test_empty_bars_returns_none(self, mt5_mock):
        mt5_mock.copy_rates_from_pos.return_value = []
        assert market_context.compute_market_context("XAUUSD") is None

    def test_exception_returns_none(self, mt5_mock):
        mt5_mock.copy_rates_from_pos.side_effect = RuntimeError("MT5 down")
        assert market_context.compute_market_context("XAUUSD") is None

    def test_tick_failure_still_returns_atr(self, mt5_mock):
        """Si el tick falla pero las barras llegan, devuelve ATR + range
        con current_price=None — info parcial mejor que ninguna."""
        bars = [_bar(2001.0, 2000.0) for _ in range(14)]
        mt5_mock.copy_rates_from_pos.return_value = bars
        mt5_mock.symbol_info_tick.return_value = None
        ctx = market_context.compute_market_context("XAUUSD")
        assert ctx is not None
        assert ctx["atr_m5_14"] == 1.0
        assert ctx["current_price_at_signal"] is None
