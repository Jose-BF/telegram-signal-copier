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

from executor import _parse_signal_id_from_comment


class TestParseSignalIdFromComment:
    # ── Market inicial (sin sufijo) ──
    def test_market_canal2(self):
        assert _parse_signal_id_from_comment("c2_12015") == ("canal2", 12015)

    def test_market_canal1(self):
        assert _parse_signal_id_from_comment("c1_19236") == ("canal1", 19236)

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
