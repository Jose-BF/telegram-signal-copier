"""
test_listener_helpers.py — Tests para helpers PUROS extraídos de listener.py.

Tests del listener entero requieren mockear Telethon + MT5 (módulo Dios,
~2000 líneas, deferred a refactor posterior). Aquí cubrimos las funciones
PURAS extraídas, que son las que tienen lógica de decisión sin side effects.

Cubre:
  - _should_accept_canal1_text (fix C6 — cutoff 5min tras restart)
"""

import pytest
from datetime import datetime, timedelta
from types import SimpleNamespace

import listener
from listener import (
    _be_close_negative_decision,
    _breakeven_close_guard_applies,
    _canal1_text_applied_summary,
    _same_direction_overlap_candidate,
    _should_accept_canal1_text,
    _standalone_mgmt_route,
    _unresolved_management_severity,
)
from state import Signal


class TestShouldAcceptCanal1Text:
    """Decide si el texto canal1 debe asociarse al sticker abierto.

    Antes del fix C6, la condición rechazaba si timestamp < cutoff(5min)
    INDEPENDIENTEMENTE de si la señal tenía TPs ya. Tras un restart, el
    resync recrea Signal con timestamp = position.time de MT5 (de hace
    horas), y el texto siguiente quedaba rechazado → posición canal1 sin
    TPs/SL (caso real canal1_19439 sesión 2026-05-06).

    Fix: aceptar siempre si sin TPs aún, aplicar cutoff solo si ya tiene TPs.
    """

    def test_sig_none_rejected(self):
        # Sin señal abierta → no hay nada con qué asociar
        assert _should_accept_canal1_text(None) is False

    def test_no_tps_recent_accepted(self):
        # Señal nueva sin TPs aún (caso normal canal1: sticker → texto en ~2min)
        sig = Signal(channel="canal1", message_id=1, direction="BUY",
                     timestamp=datetime.utcnow() - timedelta(minutes=2))
        assert _should_accept_canal1_text(sig) is True

    def test_no_tps_old_accepted_after_restart(self):
        # FIX C6: signal recreada por resync tiene timestamp viejo pero
        # sin TPs (porque MT5 no tenía SL/TP guardados). Debe aceptar texto.
        sig = Signal(channel="canal1", message_id=1, direction="BUY",
                     timestamp=datetime.utcnow() - timedelta(hours=2),
                     tps=[])
        assert _should_accept_canal1_text(sig) is True, (
            "REGRESION C6: señal sin TPs y timestamp viejo (post-resync) "
            "debe aceptar el texto que llega después del restart, sino "
            "queda naked indefinidamente."
        )

    def test_no_tps_canal1_text_late_accepted(self):
        # Canal 1 que tarda 10min en mandar el texto (no es bug del bot,
        # es del canal). Aceptar — mejor tarde que nunca.
        sig = Signal(channel="canal1", message_id=1, direction="BUY",
                     timestamp=datetime.utcnow() - timedelta(minutes=10))
        assert _should_accept_canal1_text(sig) is True

    def test_tps_recent_accepted(self):
        # Señal con TPs ya aplicados pero sticker reciente: aceptamos texto
        # adicional (puede ser edit del canal con TPs nuevos). _update_signal_
        # from_parsed se encarga de actualizar/no-op según diferencia.
        sig = Signal(channel="canal1", message_id=1, direction="BUY",
                     timestamp=datetime.utcnow() - timedelta(minutes=2),
                     tps=[2010.0, 2015.0])
        assert _should_accept_canal1_text(sig) is True

    def test_tps_old_rejected(self):
        # Señal con TPs ya aplicados y sticker viejo (>5min): rechazar texto.
        # Probablemente texto canal1 antiguo asociado erróneamente a sticker
        # nuevo (ej. al recoger histórico en scan inicial del poller).
        sig = Signal(channel="canal1", message_id=1, direction="BUY",
                     timestamp=datetime.utcnow() - timedelta(minutes=10),
                     tps=[2010.0, 2015.0])
        assert _should_accept_canal1_text(sig) is False

    def test_tps_exactly_5min_boundary(self):
        # Edge case: timestamp exactamente en el cutoff. Aceptar (>=).
        now = datetime(2026, 5, 7, 14, 0, 0)
        sig = Signal(channel="canal1", message_id=1, direction="BUY",
                     timestamp=now - timedelta(minutes=5),
                     tps=[2010.0])
        assert _should_accept_canal1_text(sig, now=now) is True

    def test_tps_just_over_5min_rejected(self):
        # 5min y 1s vieja con TPs → rechazar (cutoff estricto)
        now = datetime(2026, 5, 7, 14, 0, 0)
        sig = Signal(channel="canal1", message_id=1, direction="BUY",
                     timestamp=now - timedelta(minutes=5, seconds=1),
                     tps=[2010.0])
        assert _should_accept_canal1_text(sig, now=now) is False

    def test_now_param_optional_uses_utcnow(self):
        # Si no se pasa `now`, usa datetime.utcnow() — verificamos que la
        # función se ejecuta sin crashear bajo este path.
        sig = Signal(channel="canal1", message_id=1, direction="BUY")
        # Sin TPs → True sin importar tiempo
        assert _should_accept_canal1_text(sig) is True


class TestStandaloneMgmtRoute:
    """Regla del destino de un mensaje de gestión canal1 SIN reply.

    Un mensaje suelto no dice a qué señal va. Solo se EJECUTA la acción si el
    destino es inequívoco (exactamente 1 señal canal1 abierta); con varias →
    notificar al usuario; sin acción accionable (chatter) → solo registrar.
    """

    def test_no_actionable_always_logs(self):
        # Chatter / informativo → log, da igual cuántas señales abiertas.
        assert _standalone_mgmt_route(0, has_actionable=False) == "log"
        assert _standalone_mgmt_route(1, has_actionable=False) == "log"
        assert _standalone_mgmt_route(3, has_actionable=False) == "log"

    def test_actionable_one_open_applies(self):
        # Destino inequívoco → aplicar.
        assert _standalone_mgmt_route(1, has_actionable=True) == "apply"

    def test_actionable_multiple_open_notifies(self):
        # Ambiguo → notificar, NO actuar.
        assert _standalone_mgmt_route(2, has_actionable=True) == "notify"
        assert _standalone_mgmt_route(5, has_actionable=True) == "notify"

    def test_actionable_zero_open_logs(self):
        # Sin señal abierta no hay nada que gestionar.
        assert _standalone_mgmt_route(0, has_actionable=True) == "log"


class TestRealizedPl:
    def test_manual_close_deal_with_zero_magic_is_included(self, monkeypatch):
        sig = Signal(channel="canal1", message_id=19885, direction="SELL")
        sig.market_ticket = 111

        deals = [
            SimpleNamespace(magic=sig.magic, profit=0.0,
                            commission=0.0, swap=0.0),
            SimpleNamespace(magic=0, profit=-4.62,
                            commission=0.0, swap=0.0),
        ]

        monkeypatch.setattr(
            "MetaTrader5.history_deals_get",
            lambda position: deals if position == 111 else [],
        )

        assert listener._realized_pl(sig) == -4.62


class TestDuplicateSignalObservability:
    def test_detects_recent_same_direction_open_signal(self):
        now = datetime.utcnow()
        existing = Signal(channel="canal2", message_id=12828,
                          direction="BUY", timestamp=now)
        new = Signal(channel="canal2", message_id=12829,
                     direction="BUY", timestamp=now + timedelta(seconds=1))

        match = _same_direction_overlap_candidate(
            new, [existing, new], window_s=2.0)

        assert match is existing

    def test_ignores_opposite_direction_or_old_signal(self):
        now = datetime.utcnow()
        old_same = Signal(channel="canal2", message_id=12820,
                          direction="BUY",
                          timestamp=now - timedelta(seconds=10))
        opposite = Signal(channel="canal2", message_id=12821,
                          direction="SELL", timestamp=now)
        new = Signal(channel="canal2", message_id=12829,
                     direction="BUY", timestamp=now)

        assert _same_direction_overlap_candidate(
            new, [old_same, opposite, new], window_s=2.0) is None


class TestCanal1TextAppliedSummary:
    def test_marks_levels_without_range_explicitly(self):
        sig = Signal(channel="canal1", message_id=19880, direction="SELL")
        sig.entry_mode = "market_only"
        parsed = {"tps": [4560.5, 4557.0], "sl": 4571.61}

        summary = _canal1_text_applied_summary(sig, parsed)

        assert summary["has_range"] is False
        assert summary["has_tps"] is True
        assert summary["has_sl"] is True
        assert summary["levels_without_range"] is True
        assert summary["entry_mode"] == "market_only"


class TestBreakevenCloseGuard:
    def test_applies_to_close_this_trade_at_breakeven_when_not_hard_close(self):
        classification = {"action": "CLOSE_ALL", "confidence": 0.90}
        text = "Due to bank holiday volume is thin. Let's close this trade overall breakeven now or make it risk free"

        assert _breakeven_close_guard_applies(classification, text) is True

    def test_does_not_apply_to_explicit_close_all(self):
        classification = {"action": "CLOSE_ALL", "confidence": 0.95}

        assert _breakeven_close_guard_applies(
            classification, "Close all now") is False

    def test_rescues_only_beyond_negative_tolerance(self):
        assert _be_close_negative_decision(-6.35, tolerance_usd=2.0) == "rescue_tp_be"
        assert _be_close_negative_decision(-1.25, tolerance_usd=2.0) == "allow_close"
        assert _be_close_negative_decision(0.10, tolerance_usd=2.0) == "allow_close"


class TestRuntimeTradeMonitor:
    @pytest.mark.asyncio
    async def test_place_dca_starts_monitor_without_legacy_levels(self, monkeypatch):
        sig = Signal(channel="canal2", message_id=12888, direction="BUY")
        sig.market_ticket = 123
        sig.entry_mode = "intra_dca"
        sig.range_low = 4795.0
        sig.range_high = 4799.0
        sig.be_at_tp_index = 0

        started = []
        monkeypatch.setattr(
            listener.position_lifecycle_monitor,
            "start",
            lambda signal, levels: started.append((signal, levels)),
        )
        monkeypatch.setattr(
            listener.executor,
            "entry_price",
            lambda ticket: pytest.fail("legacy DCA must not read entry_price"),
        )

        await listener._place_dca(sig)

        assert sig.dca_placed is True
        assert started == [(sig, [])]

    @pytest.mark.asyncio
    async def test_place_dca_skips_monitor_without_be_or_time_stop(self, monkeypatch):
        sig = Signal(channel="canal2", message_id=12889, direction="SELL")
        sig.market_ticket = 456
        sig.entry_mode = "intra_dca"
        sig.range_low = 4795.0
        sig.range_high = 4799.0

        monkeypatch.setattr(
            listener.position_lifecycle_monitor,
            "start",
            lambda signal, levels: pytest.fail("monitor should be skipped"),
        )
        monkeypatch.setattr(
            listener.executor,
            "entry_price",
            lambda ticket: pytest.fail("legacy DCA must not read entry_price"),
        )

        await listener._place_dca(sig)

        assert sig.dca_placed is True


class TestPollerTelegramBackoff:
    @pytest.mark.asyncio
    async def test_transient_get_history_error_backs_off_next_poll(
            self, monkeypatch):
        for attr in ("_poller_history_backoff_until",
                     "_poller_history_failures"):
            if hasattr(listener, attr):
                getattr(listener, attr).clear()

        class FakeClient:
            def __init__(self):
                self.calls = 0

            async def get_messages(self, channel_id, limit):
                self.calls += 1
                raise RuntimeError(
                    "Telegram is having internal issues ServerError: "
                    "RPCError -500: No workers running "
                    "(caused by GetHistoryRequest)"
                )

        fake_client = FakeClient()
        events = []
        monkeypatch.setattr(listener, "client", fake_client)
        monkeypatch.setattr(
            listener, "_poller_now_monotonic", lambda: 100.0,
            raising=False,
        )
        monkeypatch.setattr(
            listener.journal,
            "event",
            lambda sig, ev, **fields: events.append((sig, ev, fields)),
        )

        await listener._poll_channel(123, "canal2")
        await listener._poll_channel(123, "canal2")

        assert fake_client.calls == 1
        assert events == [
            ("bot", "poller_telegram_history_backoff", {
                "channel": "canal2",
                "phase": "active_poll",
                "failures": 1,
                "cooldown_s": 15.0,
                "error": (
                    "Telegram is having internal issues ServerError: "
                    "RPCError -500: No workers running "
                    "(caused by GetHistoryRequest)"
                ),
            })
        ]


class TestUnresolvedManagementSeverity:
    def test_closed_target_is_forensic_only_even_if_text_looks_actionable(self):
        assert _unresolved_management_severity(
            "target_signal_closed",
            actionable=True,
        ) == "info"

    def test_unknown_actionable_target_remains_critical(self):
        assert _unresolved_management_severity(
            "unknown_reply_target",
            actionable=True,
        ) == "critical"

    @pytest.mark.parametrize("text", [
        "BE hit, we protected the trade and locked in the profit",
        "SL HIT - out of this trade",
        "TP2 hit, secure result recorded",
    ])
    def test_result_announcements_are_not_actionable_orders(self, text):
        assert listener._looks_actionable_management_text(text) is False

    def test_direct_management_instruction_remains_actionable(self):
        assert listener._looks_actionable_management_text(
            "Move SL to BE now and protect the remaining entries"
        ) is True


@pytest.mark.asyncio
async def test_move_sl_to_be_always_queues_each_exact_entry(monkeypatch):
    sig = Signal(
        channel="canal2",
        message_id=3331,
        direction="BUY",
        market_ticket=101,
        market_fill_price=4000.0,
        sl=3980.0,
    )
    queued = []

    def fake_entry_price(ticket):
        assert ticket == 101
        return 4000.0

    def fake_open_entry_prices(tickets):
        assert tickets == [101]
        return {101: 4000.0}

    async def fake_run(fn, *args):
        if fn is fake_entry_price:
            return fn(*args)
        name = getattr(fn, "__name__", "")
        if name == "symbol_info_tick":
            return SimpleNamespace(bid=3990.0, ask=3990.2)
        if name == "symbol_info":
            return SimpleNamespace(point=0.01, trade_stops_level=30)
        return fn(*args)

    monkeypatch.setattr(listener.executor, "entry_price", fake_entry_price)
    monkeypatch.setattr(
        listener.executor, "open_entry_prices", fake_open_entry_prices,
    )
    monkeypatch.setattr(listener, "_run", fake_run)
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_modify_sl",
        lambda signal, ticket, new_sl, **kwargs: queued.append({
            "ticket": ticket,
            "new_sl": new_sl,
            **kwargs,
        }),
    )
    monkeypatch.setattr(listener.journal, "event", lambda *args, **kwargs: None)
    monkeypatch.setattr(listener.journal, "anomaly", lambda *args, **kwargs: None)
    monkeypatch.setattr(listener.logger, "log_action", lambda *args, **kwargs: None)

    await listener._execute_one_action(
        sig,
        {"action": "MOVE_SL_TO_BE", "confidence": 0.99},
    )

    assert queued == [{
        "ticket": 101,
        "new_sl": 4000.0,
        "label": "BE #101 -> 4000.00",
        "persist_until_signal_close": True,
    }]


@pytest.mark.asyncio
async def test_move_sl_to_be_skips_already_closed_tickets_without_alert(
        monkeypatch):
    sig = Signal(
        channel="canal1",
        message_id=20945,
        direction="SELL",
        market_ticket=101,
        extra_market_tickets=[102],
        market_fill_price=4030.0,
        sl=4040.0,
    )
    queued = []
    anomalies = []
    events = []

    def fake_open_entry_prices(tickets):
        assert tickets == [101, 102]
        return {101: 4030.0}

    def fake_entry_price(ticket):
        return {101: 4030.0}.get(ticket)

    async def fake_run(fn, *args):
        return fn(*args)

    monkeypatch.setattr(
        listener.executor, "open_entry_prices", fake_open_entry_prices,
        raising=False,
    )
    monkeypatch.setattr(listener.executor, "entry_price", fake_entry_price)
    monkeypatch.setattr(listener, "_run", fake_run)
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_modify_sl",
        lambda signal, ticket, new_sl, **kwargs: queued.append(ticket),
    )
    monkeypatch.setattr(
        listener.journal, "event",
        lambda sig_id, event, **fields: events.append((event, fields)),
    )
    monkeypatch.setattr(
        listener.journal, "anomaly",
        lambda *args, **kwargs: anomalies.append((args, kwargs)),
    )
    monkeypatch.setattr(listener.logger, "log_action", lambda *args, **kwargs: None)

    await listener._execute_one_action(
        sig,
        {"action": "MOVE_SL_TO_BE", "confidence": 0.99},
    )

    assert queued == [101]
    assert anomalies == []
    armed = next(fields for event, fields in events
                 if event == "be_armed_classifier")
    assert armed["closed_tickets_skipped"] == [102]
