"""
test_position_lifecycle_monitor.py — Suite de regresion para position_lifecycle_monitor._decide_close_tag.

_decide_close_tag decide el motivo de cierre de UNA posicion (TP / SL /
LOSS_BE / MANUAL) cruzando el precio de salida con los niveles. Alimenta el
`closed_by_tag` de positions_closed_by_mt5 y el `tag` de signal_closed.

REGRESION CLAVE (bug 2026-05-18): cuando un mensaje del canal movia el SL,
`signal.sl` quedaba obsoleto y _classify_closures comparaba el cierre contra
el SL viejo del proveedor → etiquetaba "MANUAL" cierres que en realidad
fueron por SL (canal1_19754 y canal1_19759). El fix registra el SL real por
ticket en `signal.sl_by_ticket` y lo pasa como `effective_sl`. Estos tests
usan los numeros REALES de esas dos operaciones.
"""

import pytest

from position_lifecycle_monitor import _decide_close_tag


class TestDecideCloseTag:

    # ─── TP / SL normales ──────────────────────────────────────────────────
    def test_buy_cierra_en_tp1(self):
        tag, dist = _decide_close_tag(
            exit_price=4565.0, ticket_entry=4563.35, direction="BUY",
            tps=[4565.0, 4570.0, 4575.0, 4580.0], effective_sl=4545.0,
            be_armed=False)
        assert tag == "TP1"
        assert dist == 0.0

    def test_sell_cierra_en_tp2(self):
        tag, _ = _decide_close_tag(
            exit_price=4561.0, ticket_entry=4571.16, direction="SELL",
            tps=[4566.0, 4561.0, 4556.0, 4551.0], effective_sl=4594.0,
            be_armed=False)
        assert tag == "TP2"

    def test_buy_cierra_en_sl_del_proveedor(self):
        # Sin movimiento de gestion: effective_sl = SL del proveedor.
        tag, _ = _decide_close_tag(
            exit_price=4530.0, ticket_entry=4546.0, direction="BUY",
            tps=[4551.0, 4556.0], effective_sl=4530.0, be_armed=False)
        assert tag == "SL"

    def test_sell_cierre_por_encima_del_tp_no_es_tp(self):
        # SELL: un cierre 0.8 POR ENCIMA de TP1 NO cruzo el TP (direccional).
        tag, _ = _decide_close_tag(
            exit_price=4534.80, ticket_entry=4540.0, direction="SELL",
            tps=[4534.0, 4530.0, 4525.0, 4520.0], effective_sl=4555.0,
            be_armed=False)
        assert tag != "TP1"
        assert tag == "MANUAL"

    # ─── BE: cierre en breakeven ───────────────────────────────────────────
    def test_be_cierre_en_entry_es_loss_be(self):
        # canal1_19762: BE movio el SL al entry; el leg cerro ~en el entry.
        tag, _ = _decide_close_tag(
            exit_price=4542.39, ticket_entry=4542.40, direction="BUY",
            tps=[4548.0, 4553.0, 4561.0, 4566.0], effective_sl=4542.40,
            be_armed=True)
        assert tag == "LOSS_BE"

    def test_fallback_loss_be_sin_match_pero_be_armado(self):
        # Ni TP ni SL cruzado, pero BE armado y cierre ~en el entry → LOSS_BE.
        tag, dist = _decide_close_tag(
            exit_price=4545.5, ticket_entry=4546.0, direction="BUY",
            tps=[4551.0, 4556.0], effective_sl=4530.0, be_armed=True)
        assert tag == "LOSS_BE"
        assert dist is None

    def test_fallback_manual_sin_match_sin_be(self):
        tag, dist = _decide_close_tag(
            exit_price=4540.0, ticket_entry=4546.0, direction="BUY",
            tps=[4551.0, 4556.0], effective_sl=4530.0, be_armed=False)
        assert tag == "MANUAL"
        assert dist is None

    # ─── REGRESION del bug 2026-05-18 ──────────────────────────────────────
    def test_canal1_19754_sl_movido_por_gestion_no_es_manual(self):
        """canal1_19754: el canal mando 'move SL to 4544'. Las 4 legs
        cerraron en 4544. Con el SL EFECTIVO (4544) el tag es 'SL', no
        'MANUAL'. Numeros reales del leg market_a (entry 4545.42)."""
        tag, _ = _decide_close_tag(
            exit_price=4544.0, ticket_entry=4545.42, direction="BUY",
            tps=[4551.0, 4556.0, 4561.0, 4566.0], effective_sl=4544.0,
            be_armed=False)
        assert tag == "SL"

    def test_canal1_19759_be_trailing_no_es_manual(self):
        """canal1_19759: BE imposible (precio ya bajo el entry) → el bot
        movio el SL a un trailing 4543.81. Las 3 legs cerraron ahi. Con el
        SL EFECTIVO (4543.81) el tag es 'SL', no 'MANUAL'."""
        tag, _ = _decide_close_tag(
            exit_price=4543.81, ticket_entry=4546.97, direction="BUY",
            tps=[4549.0, 4554.0, 4561.0, 4566.0], effective_sl=4543.81,
            be_armed=True)
        assert tag == "SL"

    def test_bug_con_sl_obsoleto_habria_dado_manual(self):
        """Documenta el bug: MISMOS numeros que canal1_19754 pero pasando el
        SL OBSOLETO del proveedor (4530) en vez del efectivo (4544) → el
        cierre se etiqueta 'MANUAL'. Es lo que pasaba antes del fix; ahora
        _classify_closures pasa signal.sl_by_ticket como effective_sl."""
        tag, _ = _decide_close_tag(
            exit_price=4544.0, ticket_entry=4545.42, direction="BUY",
            tps=[4551.0, 4556.0, 4561.0, 4566.0], effective_sl=4530.0,
            be_armed=False)
        assert tag == "MANUAL"   # el bug, reproducido con el input viejo
