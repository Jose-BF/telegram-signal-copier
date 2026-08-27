"""
test_executor_resync.py — Regresión del parser de comments para el resync.

executor._parse_signal_id_from_comment reconoce el comment de una posición
MT5 y devuelve (channel, message_id). Es la pieza con la que el resync
on-startup reconstruye las señales tras un reinicio del bot.

Crítico: el modo scale_out (2026-05-17) abre varias posiciones market por
señal con comments c2_<id>_B1 .. _B4. Si el regex no las reconoce, el
resync las ignora y quedan como posiciones huérfanas. Estos tests fijan
ese contrato.
"""

from types import SimpleNamespace

import executor
from executor import _parse_signal_id_from_comment


class TestParseSignalIdFromComment:
    # ── Market inicial (sin sufijo) ──
    def test_market_canal2(self):
        assert _parse_signal_id_from_comment("c2_12015") == ("canal2", 12015)

    def test_market_canal1(self):
        assert _parse_signal_id_from_comment("c1_19236") == ("canal1", 19236)

    def test_dubai_candidate_market_marker(self):
        assert _parse_signal_id_from_comment("c1_19236_dv1") == (
            "canal1", 19236,
        )

    def test_gold_candidate_market_marker(self):
        assert _parse_signal_id_from_comment("c2_2054_gv1") == (
            "canal2", 2054,
        )

    def test_gold_candidate_scale_out_marker(self):
        assert _parse_signal_id_from_comment("c2_2054_B4_gv1") == (
            "canal2", 2054,
        )

    def test_gold_555_market_marker(self):
        assert _parse_signal_id_from_comment("c2_380_g55") == (
            "canal2", 380,
        )

    def test_gold_555_ladder_marker(self):
        assert _parse_signal_id_from_comment("c2_380_B4_g55") == (
            "canal2", 380,
        )

    # ── Doble market legacy ──
    def test_market_b_doble_market(self):
        assert _parse_signal_id_from_comment("c2_12015_B") == ("canal2", 12015)

    # ── Legs del modo scale_out (el caso NUEVO) ──
    def test_scale_out_leg_b1(self):
        assert _parse_signal_id_from_comment("c2_12015_B1") == ("canal2", 12015)

    def test_scale_out_leg_b4(self):
        assert _parse_signal_id_from_comment("c2_12015_B4") == ("canal2", 12015)

    def test_scale_out_leg_canal1(self):
        assert _parse_signal_id_from_comment("c1_19569_B3") == ("canal1", 19569)

    def test_all_scale_out_legs_recognized(self):
        # Una señal canal2 en scale_out abre el market + B1..B4: las 5 deben
        # resolver al MISMO signal_id para que el resync las agrupe juntas.
        comments = ["c2_12015", "c2_12015_B1", "c2_12015_B2",
                    "c2_12015_B3", "c2_12015_B4"]
        for c in comments:
            assert _parse_signal_id_from_comment(c) == ("canal2", 12015), (
                f"REGRESION: la leg {c!r} del scale_out no se reconoce — "
                f"el resync la ignoraria y quedaria huerfana."
            )

    # ── Rescue market ──
    def test_rescue_market(self):
        assert _parse_signal_id_from_comment("c2_12015_rescue") == ("canal2", 12015)

    # ── DCA nuevo (con signal_id) ──
    def test_dca_new_format(self):
        assert _parse_signal_id_from_comment("DCA_c2_12015_4700.5") == ("canal2", 12015)

    # ── No reconocidos ──
    def test_dca_old_format_not_parsed(self):
        # DCA viejo sin signal_id → None (se agrupa por proximidad temporal)
        assert _parse_signal_id_from_comment("DCA_4700.5") is None

    def test_garbage_comment(self):
        assert _parse_signal_id_from_comment("bot_close") is None
        assert _parse_signal_id_from_comment("c2_12015_X") is None

    def test_empty_comment(self):
        assert _parse_signal_id_from_comment("") is None
        assert _parse_signal_id_from_comment(None) is None


def test_resync_reconstructs_signal_from_surviving_scale_out_legs(monkeypatch):
    """A restart must adopt B legs even when the original leg already closed."""
    magic = executor.config.MT5_MAGIC_CANAL2
    positions = (
        SimpleNamespace(
            ticket=2002, comment="c2_3379_B2", magic=magic,
            type=executor.mt5.ORDER_TYPE_BUY, price_open=4031.25,
            sl=4024.0, tp=4040.0, time=1002,
        ),
        SimpleNamespace(
            ticket=2004, comment="c2_3379_B4", magic=magic,
            type=executor.mt5.ORDER_TYPE_BUY, price_open=4031.30,
            sl=4024.0, tp=4048.0, time=1004,
        ),
    )
    monkeypatch.setattr(executor.mt5, "positions_get", lambda: positions)

    grouped = executor.list_open_positions_grouped()

    signal = grouped["canal2_3379"]
    assert signal["market_ticket"] == 2002
    assert signal["market_price"] == 4031.25
    assert signal["market_open_time"] == 1002
    assert signal["extra_market_tickets"] == [2004]
    assert signal["double_market_tickets"] == []
    assert signal["scale_out_leg_indexes"] == {2002: 2, 2004: 4}
    assert signal["resync_anchor_role"] == "surviving_scale_out_leg"


def test_resync_orders_scale_out_legs_by_original_leg_number(monkeypatch):
    magic = executor.config.MT5_MAGIC_CANAL2
    positions = (
        SimpleNamespace(
            ticket=3000, comment="c2_3380", magic=magic,
            type=executor.mt5.ORDER_TYPE_SELL, price_open=4040.0,
            sl=4050.0, tp=4037.0, time=1000,
        ),
        SimpleNamespace(
            ticket=3004, comment="c2_3380_B4", magic=magic,
            type=executor.mt5.ORDER_TYPE_SELL, price_open=4040.4,
            sl=4050.0, tp=4028.0, time=1004,
        ),
        SimpleNamespace(
            ticket=3002, comment="c2_3380_B2", magic=magic,
            type=executor.mt5.ORDER_TYPE_SELL, price_open=4040.2,
            sl=4050.0, tp=4033.0, time=1002,
        ),
        SimpleNamespace(
            ticket=3001, comment="c2_3380_B1", magic=magic,
            type=executor.mt5.ORDER_TYPE_SELL, price_open=4040.1,
            sl=4050.0, tp=4035.0, time=1001,
        ),
    )
    monkeypatch.setattr(executor.mt5, "positions_get", lambda: positions)

    signal = executor.list_open_positions_grouped()["canal2_3380"]

    assert signal["extra_market_tickets"] == [3001, 3002, 3004]
    assert signal["double_market_tickets"] == []
    assert signal["scale_out_leg_indexes"] == {
        3001: 1,
        3002: 2,
        3004: 4,
    }


def test_resync_distinguishes_legacy_market_b_from_scale_out_legs(monkeypatch):
    magic = executor.config.MT5_MAGIC_CANAL2
    positions = (
        SimpleNamespace(
            ticket=4000, comment="c2_3381", magic=magic,
            type=executor.mt5.ORDER_TYPE_BUY, price_open=4050.0,
            sl=4040.0, tp=4053.0, time=1000,
        ),
        SimpleNamespace(
            ticket=4001, comment="c2_3381_B", magic=magic,
            type=executor.mt5.ORDER_TYPE_BUY, price_open=4050.1,
            sl=4040.0, tp=4057.0, time=1001,
        ),
        SimpleNamespace(
            ticket=4002, comment="c2_3381_B1", magic=magic,
            type=executor.mt5.ORDER_TYPE_BUY, price_open=4050.2,
            sl=4040.0, tp=4055.0, time=1002,
        ),
    )
    monkeypatch.setattr(executor.mt5, "positions_get", lambda: positions)

    signal = executor.list_open_positions_grouped()["canal2_3381"]

    assert signal["extra_market_tickets"] == [4001, 4002]
    assert signal["double_market_tickets"] == [4001]
    assert signal["scale_out_leg_indexes"] == {4002: 1}


def test_resync_preserves_candidate_marker_and_dca_leg_indexes(monkeypatch):
    magic = executor.config.MT5_MAGIC_CANAL1
    positions = (
        SimpleNamespace(
            ticket=5000, comment="c1_26001_dv1", magic=magic,
            type=executor.mt5.ORDER_TYPE_BUY, price_open=4200.0,
            sl=0.0, tp=0.0, time=1000,
        ),
        SimpleNamespace(
            ticket=5001, comment="DCA_c1_26001_D1", magic=magic,
            type=executor.mt5.ORDER_TYPE_BUY, price_open=4196.0,
            sl=0.0, tp=0.0, time=1001,
        ),
    )
    monkeypatch.setattr(executor.mt5, "positions_get", lambda: positions)

    signal = executor.list_open_positions_grouped()["canal1_26001"]

    assert signal["live_strategy_marker"] == "dubai_balanced_v1"
    assert signal["dca_leg_indexes"] == {5001: 1}


def test_resync_recovers_candidate_when_only_ladder_legs_survive(monkeypatch):
    magic = executor.config.MT5_MAGIC_CANAL1
    positions = (
        SimpleNamespace(
            ticket=5101, comment="DCA_c1_26002_D1", magic=magic,
            type=executor.mt5.ORDER_TYPE_SELL, price_open=4204.1,
            sl=0.0, tp=0.0, time=1001,
        ),
        SimpleNamespace(
            ticket=5102, comment="DCA_c1_26002_D2", magic=magic,
            type=executor.mt5.ORDER_TYPE_SELL, price_open=4208.2,
            sl=0.0, tp=0.0, time=1002,
        ),
    )
    monkeypatch.setattr(executor.mt5, "positions_get", lambda: positions)

    signal = executor.list_open_positions_grouped()["canal1_26002"]

    assert signal["market_ticket"] == 5101
    assert signal["dca_tickets"] == [5102]
    assert signal["dca_leg_indexes"] == {5101: 1, 5102: 2}
    assert signal["resync_anchor_role"] == "surviving_candidate_leg"
    assert signal["live_strategy_marker"] == "dubai_balanced_v1"


def test_resync_preserves_gold_marker_and_real_entry_prices(monkeypatch):
    magic = executor.config.MT5_MAGIC_CANAL2
    positions = (
        SimpleNamespace(
            ticket=5200, comment="c2_2054_gv1", magic=magic,
            type=executor.mt5.ORDER_TYPE_SELL, price_open=4620.0,
            volume=0.01, sl=4642.0, tp=0.0, time=1000,
        ),
        SimpleNamespace(
            ticket=5201, comment="c2_2054_B1_gv1", magic=magic,
            type=executor.mt5.ORDER_TYPE_SELL, price_open=4620.2,
            volume=0.01, sl=4642.2, tp=0.0, time=1001,
        ),
    )
    monkeypatch.setattr(executor.mt5, "positions_get", lambda: positions)

    signal = executor.list_open_positions_grouped()["canal2_2054"]

    assert signal["live_strategy_marker"] == "gold_now_c490_v1"
    assert signal["market_ticket"] == 5200
    assert signal["extra_market_tickets"] == [5201]
    assert signal["position_entries"] == {5200: 4620.0, 5201: 4620.2}
    assert signal["position_volumes"] == {5200: 0.01, 5201: 0.01}


def test_resync_recovers_gold_when_only_extra_legs_survive(monkeypatch):
    magic = executor.config.MT5_MAGIC_CANAL2
    positions = (
        SimpleNamespace(
            ticket=5302, comment="c2_2055_B2_gv1", magic=magic,
            type=executor.mt5.ORDER_TYPE_BUY, price_open=4200.2,
            volume=0.01, sl=4180.2, tp=0.0, time=1002,
        ),
        SimpleNamespace(
            ticket=5304, comment="c2_2055_B4_gv1", magic=magic,
            type=executor.mt5.ORDER_TYPE_BUY, price_open=4200.4,
            volume=0.01, sl=4180.4, tp=0.0, time=1004,
        ),
    )
    monkeypatch.setattr(executor.mt5, "positions_get", lambda: positions)

    signal = executor.list_open_positions_grouped()["canal2_2055"]

    assert signal["live_strategy_marker"] == "gold_now_c490_v1"
    assert signal["resync_anchor_role"] == "surviving_scale_out_leg"
    assert signal["market_ticket"] == 5302
    assert signal["extra_market_tickets"] == [5304]


def test_resync_preserves_gold_555_marker_and_broker_levels(monkeypatch):
    magic = executor.config.MT5_MAGIC_CANAL2
    positions = (
        SimpleNamespace(
            ticket=5400, comment="c2_380_g55", magic=magic,
            type=executor.mt5.ORDER_TYPE_BUY, price_open=4056.53,
            volume=0.04, sl=4026.53, tp=4057.03, time=1000,
        ),
        SimpleNamespace(
            ticket=5402, comment="c2_380_B2_g55", magic=magic,
            type=executor.mt5.ORDER_TYPE_BUY, price_open=4053.50,
            volume=0.03, sl=4023.50, tp=4055.00, time=1002,
        ),
    )
    monkeypatch.setattr(executor.mt5, "positions_get", lambda: positions)

    signal = executor.list_open_positions_grouped()["canal2_380"]

    assert signal["live_strategy_marker"] == "gold_now_555_v1"
    assert signal["market_ticket"] == 5400
    assert signal["extra_market_tickets"] == [5402]
    assert signal["scale_out_leg_indexes"] == {5402: 2}
    assert signal["position_entries"] == {5400: 4056.53, 5402: 4053.50}
    assert signal["position_volumes"] == {5400: 0.04, 5402: 0.03}
    assert signal["position_stops"] == {5400: 4026.53, 5402: 4023.50}
    assert signal["position_targets"] == {5400: 4057.03, 5402: 4055.00}


def test_resync_recovers_gold_555_when_only_later_legs_survive(monkeypatch):
    magic = executor.config.MT5_MAGIC_CANAL2
    positions = (
        SimpleNamespace(
            ticket=5503, comment="c2_381_B3_g55", magic=magic,
            type=executor.mt5.ORDER_TYPE_SELL, price_open=4061.0,
            volume=0.03, sl=4091.0, tp=4059.0, time=1003,
        ),
        SimpleNamespace(
            ticket=5504, comment="c2_381_B4_g55", magic=magic,
            type=executor.mt5.ORDER_TYPE_SELL, price_open=4062.5,
            volume=0.03, sl=4092.5, tp=4060.0, time=1004,
        ),
    )
    monkeypatch.setattr(executor.mt5, "positions_get", lambda: positions)

    signal = executor.list_open_positions_grouped()["canal2_381"]

    assert signal["live_strategy_marker"] == "gold_now_555_v1"
    assert signal["resync_anchor_role"] == "surviving_scale_out_leg"
    assert signal["market_ticket"] == 5503
    assert signal["extra_market_tickets"] == [5504]
    assert signal["scale_out_leg_indexes"] == {5503: 3, 5504: 4}
