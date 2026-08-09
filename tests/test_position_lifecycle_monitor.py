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

from types import SimpleNamespace

import pytest

import causal_trace
import journal
import position_lifecycle_monitor
from position_lifecycle_monitor import (
    _classify_closures,
    _decide_close_tag,
    _should_emit_periodic_snapshot,
)
from state import Signal


def test_periodic_snapshot_sampling_respects_interval():
    assert _should_emit_periodic_snapshot(
        now_ts=100.0, last_ts=0.0, interval_s=120.0) is False
    assert _should_emit_periodic_snapshot(
        now_ts=120.0, last_ts=0.0, interval_s=120.0) is True
    assert _should_emit_periodic_snapshot(
        now_ts=121.0, last_ts=0.0, interval_s=120.0) is True


def test_periodic_snapshot_sampling_can_be_disabled_for_tests():
    assert _should_emit_periodic_snapshot(
        now_ts=100.0, last_ts=99.9, interval_s=0.0) is True


def test_auto_finalize_grace_uses_monitor_clock_not_signal_timestamp():
    assert position_lifecycle_monitor._auto_finalize_grace_elapsed(
        monitor_started_monotonic=100.0,
        now_monotonic=129.9,
    ) is False
    assert position_lifecycle_monitor._auto_finalize_grace_elapsed(
        monitor_started_monotonic=100.0,
        now_monotonic=130.0,
    ) is True


@pytest.mark.asyncio
async def test_delayed_market_open_emits_internal_decision_manifest(
    monkeypatch,
):
    events = []
    signal = Signal(
        channel="canal2",
        message_id=380,
        direction="BUY",
        source_message_revision_id="msgrev_origin",
        source_decision_id="decision_origin",
    )

    def open_market(*args, **kwargs):
        causal_trace.new_action_id()
        return 12345

    monkeypatch.setattr(
        position_lifecycle_monitor.executor,
        "open_market",
        open_market,
    )
    monkeypatch.setattr(
        journal,
        "event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )

    ticket = await position_lifecycle_monitor._open_market_internal(
        signal,
        level=4051.0,
        lot=0.01,
        sl=4047.0,
        tp=4059.0,
        comment="DCA_c2_380_4051.0",
    )

    assert ticket == 12345
    assert [ev for _, ev, _ in events] == [
        "bot_internal_decision_started",
        "bot_internal_decision",
    ]
    started = events[0][2]
    decision = next(
        fields for _, ev, fields in events
        if ev == "bot_internal_decision"
    )
    assert started["decision_id"] == decision["decision_id"]
    assert decision["message_revision_id"] == "msgrev_origin"
    assert decision["parent_decision_id"] == "decision_origin"
    assert decision["decision_reason"] == "position_lifecycle_dca"
    assert decision["declared_action_count"] == 1
    assert len(decision["declared_action_ids"]) == 1


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

    def test_broker_manual_reason_cannot_be_mislabeled_as_tp(self):
        tag, dist = _decide_close_tag(
            exit_price=4533.5, ticket_entry=4540.0, direction="SELL",
            tps=[4534.0], effective_sl=4555.0, be_armed=False,
            broker_close_reason="manual")
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


def test_mt5_tp_reason_uses_confirmed_ticket_tp_not_later_provider_levels(
        monkeypatch):
    """A TP hit before the final provider levels is still a real TP."""
    ticket = 1688925705
    signal = Signal(channel="canal1", message_id=21182, direction="SELL")
    signal.market_ticket = ticket
    signal.tps = [4039.0, 4037.0, 4035.0, 4033.0]
    signal.sl = 4052.0
    signal.tp_by_ticket[ticket] = 4040.68

    deals = [
        SimpleNamespace(
            ticket=1, time_msc=1, price=4043.68, profit=0.0,
            commission=0.0, swap=0.0, reason=3, comment="c1_21182",
        ),
        SimpleNamespace(
            ticket=2, time_msc=2, price=4040.68, profit=2.95,
            commission=0.0, swap=0.0, fee=-0.05, reason=5, comment="",
        ),
    ]
    monkeypatch.setattr(
        position_lifecycle_monitor.mt5,
        "history_deals_get",
        lambda **_kwargs: deals,
    )

    closures = _classify_closures(signal)

    assert closures == [{
        "ticket": ticket,
        "exit_price": 4040.68,
        "pnl": 2.90,
        "closed_by_tag": "TP_PROVISIONAL",
        "distance_to_tag": 0.0,
        "broker_close_reason": "tp",
        "broker_deal_reason": 5,
        "effective_tp": 4040.68,
        "effective_sl": 4052.0,
        "classification_source": "broker_reason_and_effective_level",
    }]


def test_mt5_sl_reason_wins_when_provider_level_changed_after_close(
        monkeypatch):
    ticket = 1689000001
    signal = Signal(channel="canal1", message_id=21183, direction="SELL")
    signal.market_ticket = ticket
    signal.tps = [4039.0]
    signal.sl = 4055.0
    signal.sl_by_ticket[ticket] = 4090.0

    deals = [
        SimpleNamespace(
            ticket=10, time_msc=1, price=4044.0, profit=0.0,
            commission=0.0, swap=0.0, reason=3, comment="c1_21183",
        ),
        SimpleNamespace(
            ticket=11, time_msc=2, price=4090.2, profit=-50.5,
            commission=0.0, swap=0.0, reason=4, comment="",
        ),
    ]
    monkeypatch.setattr(
        position_lifecycle_monitor.mt5,
        "history_deals_get",
        lambda **_kwargs: deals,
    )

    closure = _classify_closures(signal)[0]

    assert closure["closed_by_tag"] == "SL"
    assert closure["broker_close_reason"] == "sl"
    assert closure["classification_source"] == "broker_reason_and_effective_level"


def test_basket_guard_close_uses_bot_state_before_price_classification(
        monkeypatch):
    ticket = 1689000002
    signal = Signal(channel="canal1", message_id=21184, direction="BUY")
    signal.market_ticket = ticket
    signal.tps = [4050.0]
    signal.sl = 4030.0
    signal.basket_guard_close_tickets = [ticket]
    signal.basket_guard_trigger_reason = "loss_cap"

    deals = [
        SimpleNamespace(
            ticket=20, time_msc=1, price=4040.0, profit=0.0,
            commission=0.0, swap=0.0, fee=0.0, reason=3, comment="c1_21184",
        ),
        SimpleNamespace(
            ticket=21, time_msc=2, price=4035.0, profit=-5.0,
            commission=0.0, swap=0.0, fee=-0.05, reason=1, comment="",
        ),
    ]
    monkeypatch.setattr(
        position_lifecycle_monitor.mt5,
        "history_deals_get",
        lambda **_kwargs: deals,
    )

    closure = _classify_closures(signal)[0]

    assert closure["closed_by_tag"] == "BASKET_GUARD_LOSS_CAP"
    assert closure["classification_source"] == "bot_state"
