from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from alert_graphics import (
    build_chart_model,
    chart_level_specs,
    collect_recent_prices,
    render_review_alert_png,
)


def _context(**overrides):
    values = {
        "channel": "canal2",
        "direction": "BUY",
        "entry_price": 4310.0,
        "tps": [4318.0, 4324.0],
        "sl": 4302.0,
        "effective_sls": [4310.0],
        "n_initial": 5,
        "n_open": 4,
        "floating_pnl_total": 9.2,
        "elapsed_min": 7.2,
        "current_price": 4312.3,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_chart_model_uses_public_provider_and_effective_mt5_stop():
    model = build_chart_model(_context(), [4309.8, 4310.4, 4312.3])

    assert model.provider == "Gold Signals"
    assert model.direction == "BUY"
    assert model.target == 4318.0
    assert model.stop_levels == (4310.0,)
    assert model.stop_source == "actual"


def test_chart_model_does_not_invent_trajectory_without_ticks():
    model = build_chart_model(_context(effective_sls=[]), [])

    assert model.prices == ()
    assert model.stop_levels == (4302.0,)
    assert model.stop_source == "proveedor"


def test_sell_chart_selects_next_target_below_market():
    model = build_chart_model(
        _context(
            channel="canal1",
            direction="SELL",
            current_price=4312.0,
            entry_price=4315.0,
            tps=[4310.0, 4305.0, 4320.0],
            effective_sls=[4318.0],
        ),
        [4315.0, 4313.0, 4312.0],
    )

    assert model.provider == "Dubai Investing"
    assert model.target == 4310.0


def test_renderer_returns_real_png_even_without_tick_line():
    png = render_review_alert_png(
        build_chart_model(_context(), []),
    )

    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(BytesIO(png)) as image:
        assert image.format == "PNG"
        assert image.width >= 900
        assert image.height >= 500


def test_entry_and_effective_be_stop_share_one_label():
    model = build_chart_model(_context(effective_sls=[4310.0]), [])

    specs = chart_level_specs(model)
    matching = [label for label, value, _kind in specs
                if abs(value - 4310.0) < 0.01]

    assert matching == ["ENTRADA / BE / SL ACTUAL"]


class _FakeMt5:
    COPY_TICKS_INFO = 1

    def copy_ticks_range(self, symbol, since, until, flags):
        assert symbol == "XAUUSD"
        assert flags == self.COPY_TICKS_INFO
        return [
            {"bid": 4300.0 + idx, "ask": 4300.3 + idx}
            for idx in range(20)
        ]


def test_tick_collection_uses_closing_side_and_downsamples():
    buy = collect_recent_prices(
        "XAUUSD", "BUY", mt5_module=_FakeMt5(), max_points=5,
    )
    sell = collect_recent_prices(
        "XAUUSD", "SELL", mt5_module=_FakeMt5(), max_points=5,
    )

    assert len(buy) <= 5
    assert len(sell) <= 5
    assert buy[0] == 4300.0
    assert buy[-1] == 4319.0
    assert sell[0] == 4300.3
    assert sell[-1] == 4319.3
