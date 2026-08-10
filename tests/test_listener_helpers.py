"""
test_listener_helpers.py — Tests para helpers PUROS extraídos de listener.py.

Tests del listener entero requieren mockear Telethon + MT5 (módulo Dios,
~2000 líneas, deferred a refactor posterior). Aquí cubrimos las funciones
PURAS extraídas, que son las que tienen lógica de decisión sin side effects.

Cubre:
  - _should_accept_canal1_text (fix C6 — cutoff 5min tras restart)
"""

import asyncio
import json

import pytest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import causal_trace
import listener
from listener import (
    _be_close_negative_decision,
    _breakeven_close_guard_applies,
    _canal1_text_applied_summary,
    _management_requires_execution,
    _rescue_market_capacity,
    _same_direction_overlap_candidate,
    _should_accept_canal1_text,
    _standalone_mgmt_route,
    _unresolved_management_severity,
)
from interpretation_firewall import firewall_decision
from state import Signal


def test_optional_provider_suggestion_does_not_count_as_required_execution():
    signal = Signal(channel="canal1", message_id=21182, direction="SELL")
    classification = {
        "action": "CLOSE_ALL",
        "confidence": 0.95,
        "is_optional": True,
    }
    decision = firewall_decision(
        signal, classification,
        raw_text="You can close around entry if you prefer",
    )

    assert _management_requires_execution(
        signal, classification, decision) is False


def test_direct_provider_order_counts_as_required_execution():
    signal = Signal(channel="canal2", message_id=380, direction="BUY")
    classification = {
        "action": "MOVE_SL_TO_BE",
        "confidence": 0.95,
        "is_optional": False,
        "is_conditional": False,
    }
    decision = firewall_decision(
        signal, classification, raw_text="Move SL to BE")

    assert _management_requires_execution(
        signal, classification, decision) is True


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


def test_only_commands_or_review_intents_require_a_standalone_target():
    classifications = [
        {"action": "MARKET_COMMENTARY"},
        {"action": "TP_HIT_ANNOUNCEMENT"},
        {"action": "PROGRESS_UPDATE"},
        {"action": "REENTRY_SIGNAL"},
    ]

    assert listener._target_requiring_actions(classifications) == [
        {"action": "REENTRY_SIGNAL"},
    ]


def test_tp_announcement_targets_unique_recent_observed_hit():
    now = datetime(2026, 8, 10, 14, 51, 14)
    older = Signal(channel="canal1", message_id=21321, direction="SELL")
    newer = Signal(channel="canal1", message_id=21325, direction="SELL")
    newer.observed_tp_hits = {0: now - timedelta(seconds=40)}

    target = listener._recent_tp_announcement_target(
        [older, newer],
        "TP1 HIT! 50+ PIPS secured!",
        observed_at=now,
    )

    assert target is newer


def test_tp_announcement_does_not_guess_when_two_recent_hits_match():
    now = datetime(2026, 8, 10, 14, 51, 14)
    first = Signal(channel="canal1", message_id=21321, direction="SELL")
    second = Signal(channel="canal1", message_id=21325, direction="SELL")
    first.observed_tp_hits = {0: now - timedelta(seconds=30)}
    second.observed_tp_hits = {0: now - timedelta(seconds=40)}

    assert listener._recent_tp_announcement_target(
        [first, second],
        "TP1 HIT",
        observed_at=now,
    ) is None


@pytest.mark.asyncio
async def test_canal1_commentary_with_two_open_signals_does_not_alert(
        monkeypatch):
    first = Signal(channel="canal1", message_id=21321, direction="SELL")
    second = Signal(channel="canal1", message_id=21325, direction="SELL")
    events = []
    anomalies = []
    notifications = []

    monkeypatch.setattr(
        listener.state,
        "open_signals",
        lambda channel=None: [first, second],
    )

    async def _classify(*args, **kwargs):
        return [{"action": "MARKET_COMMENTARY", "confidence": 0.99}]

    monkeypatch.setattr(listener, "classify_async", _classify)
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig_id, ev, **kwargs: events.append({"ev": ev, **kwargs}),
    )
    monkeypatch.setattr(
        listener.journal,
        "anomaly",
        lambda *args, **kwargs: anomalies.append((args, kwargs)),
    )
    monkeypatch.setattr(
        listener.asyncio,
        "create_task",
        lambda coro: (notifications.append(True), coro.close()),
    )

    await listener._handle_canal1_standalone(
        SimpleNamespace(),
        "Lets try again",
        "canal1_21328",
    )

    assert anomalies == []
    assert notifications == []
    assert any(event["ev"] == "standalone_context_observed"
               for event in events)


@pytest.mark.asyncio
async def test_canal1_tp_announcement_records_unique_recent_target(
        monkeypatch):
    now = datetime.utcnow()
    older = Signal(channel="canal1", message_id=21321, direction="SELL")
    newer = Signal(channel="canal1", message_id=21325, direction="SELL")
    newer.observed_tp_hits = {0: now - timedelta(seconds=40)}
    events = []

    monkeypatch.setattr(
        listener.state,
        "open_signals",
        lambda channel=None: [older, newer],
    )

    async def _classify(*args, **kwargs):
        return [{"action": "TP_HIT_ANNOUNCEMENT", "confidence": 0.99}]

    monkeypatch.setattr(listener, "classify_async", _classify)
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig_id, ev, **kwargs: events.append({"ev": ev, **kwargs}),
    )
    monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **k: None)
    monkeypatch.setattr(
        listener.asyncio,
        "create_task",
        lambda coro: coro.close(),
    )

    await listener._handle_canal1_standalone(
        SimpleNamespace(),
        "TP1 HIT! 50+ PIPS secured!",
        "canal1_21326",
    )

    attributed = next(
        event for event in events
        if event["ev"] == "standalone_context_attributed"
    )
    assert attributed["target"] == "canal1_21325"
    assert attributed["attribution"] == "recent_observed_tp_hit"


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

    @pytest.mark.asyncio
    async def test_place_dca_starts_dubai_basket_guard_without_be_or_time_stop(
            self, monkeypatch):
        sig = Signal(channel="canal1", message_id=21190, direction="BUY")
        sig.market_ticket = 789

        started = []
        monkeypatch.setattr(
            listener.config,
            "STRATEGY_C1_BASKET_GUARD_ENABLED",
            True,
        )
        monkeypatch.setattr(
            listener.position_lifecycle_monitor,
            "start",
            lambda signal, levels: started.append((signal, levels)),
        )

        await listener._place_dca(sig)

        assert sig.dca_placed is True
        assert started == [(sig, [])]


class TestRescueMarketExposureCap:
    def test_four_positions_can_add_one_rescue_leg(self, monkeypatch):
        monkeypatch.setattr(
            listener.config,
            "STRATEGY_MAX_PLANNED_LOTS_PER_SIGNAL",
            0.05,
        )
        sig = Signal(
            channel="canal1",
            message_id=21290,
            direction="BUY",
            market_ticket=101,
            extra_market_tickets=[102, 103, 104],
        )

        capacity = _rescue_market_capacity(sig)

        assert capacity == {
            "allowed": True,
            "current_positions": 4,
            "current_lots": 0.04,
            "projected_lots": 0.05,
            "max_lots": 0.05,
        }

    def test_five_positions_cannot_add_a_sixth_leg(self, monkeypatch):
        monkeypatch.setattr(
            listener.config,
            "STRATEGY_MAX_PLANNED_LOTS_PER_SIGNAL",
            0.05,
        )
        sig = Signal(
            channel="canal2",
            message_id=930,
            direction="SELL",
            market_ticket=201,
            extra_market_tickets=[202, 203, 204, 205],
        )

        capacity = _rescue_market_capacity(sig)

        assert capacity == {
            "allowed": False,
            "current_positions": 5,
            "current_lots": 0.05,
            "projected_lots": 0.06,
            "max_lots": 0.05,
        }

    @pytest.mark.asyncio
    async def test_adverse_range_skips_rescue_before_sending_mt5_order(
            self, monkeypatch):
        monkeypatch.setattr(
            listener.config,
            "STRATEGY_MAX_PLANNED_LOTS_PER_SIGNAL",
            0.05,
        )
        sig = Signal(
            channel="canal2",
            message_id=931,
            direction="SELL",
            market_ticket=201,
            extra_market_tickets=[202, 203, 204, 205],
            adverse_action="rescue_market",
        )
        events = []

        def fake_entry_price(ticket):
            assert ticket == 201
            return 4100.0

        async def fake_run(fn, *args):
            if fn is listener.executor.current_tick:
                pytest.fail("exposure cap must stop before reading rescue tick")
            if fn is listener.executor.open_market:
                pytest.fail("exposure cap must stop before sending an MT5 order")
            return fn(*args)

        monkeypatch.setattr(listener.executor, "entry_price", fake_entry_price)
        monkeypatch.setattr(listener, "_run", fake_run)
        monkeypatch.setattr(
            listener.journal,
            "event",
            lambda sig_id, event, **fields: events.append(
                (sig_id, event, fields)
            ),
        )
        monkeypatch.setattr(
            listener.journal, "update_trade", lambda *args, **kwargs: None,
        )

        closed = await listener._handle_range_arrival_safety(
            sig, 4090.0, 4095.0,
        )

        assert closed is False
        assert sig.entry_mode == "market_only"
        skipped = next(
            fields for _, event, fields in events
            if event == "rescue_market_skipped_exposure_cap"
        )
        assert skipped["current_positions"] == 5
        assert skipped["projected_lots"] == 0.06


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


class TestPollerStartupCatchup:
    @staticmethod
    def _reset_poller_state():
        listener._poller_msg_state.clear()
        listener._poller_initialized_channels.clear()
        listener._poller_last_coverage_log.clear()
        listener._seen_new_msg_ids.clear()
        listener._seen_new_msgs_order.clear()
        listener._seen_edits.clear()
        listener._seen_edits_order.clear()
        listener._dispatch_inflight_revisions.clear()
        listener._dispatch_completed_revisions.clear()
        listener._dispatch_completed_order.clear()

    def test_unseen_message_during_downtime_is_dispatched_as_new(self):
        history = {
            "has_channel_history": True,
            "coverage_cutoff": datetime(
                2026, 7, 22, 11, 59, 58, tzinfo=timezone.utc
            ),
            "message_versions": {278: "2026-07-22T11:59:57+00:00"},
        }
        message = SimpleNamespace(
            id=279,
            date=datetime(2026, 7, 22, 12, 1, tzinfo=timezone.utc),
            edit_date=None,
        )

        assert listener._poller_startup_action(message, history) == "new"

    def test_seen_message_is_not_replayed_but_new_edit_is(self):
        history = {
            "has_channel_history": True,
            "coverage_cutoff": datetime(
                2026, 7, 22, 11, 59, 58, tzinfo=timezone.utc
            ),
            "message_versions": {278: "2026-07-22T11:59:57+00:00"},
        }
        unchanged = SimpleNamespace(
            id=278,
            date=datetime(2026, 7, 22, 11, 59, 35, tzinfo=timezone.utc),
            edit_date=datetime(
                2026, 7, 22, 11, 59, 57, tzinfo=timezone.utc
            ),
        )
        edited = SimpleNamespace(
            id=278,
            date=unchanged.date,
            edit_date=datetime(2026, 7, 22, 12, 3, tzinfo=timezone.utc),
        )

        assert listener._poller_startup_action(unchanged, history) == "seen"
        assert listener._poller_startup_action(edited, history) == "edit"

    def test_unknown_new_channel_establishes_baseline_without_replaying_history(
        self,
    ):
        history = {
            "has_channel_history": False,
            "coverage_cutoff": None,
            "message_versions": {},
        }
        message = SimpleNamespace(
            id=1,
            date=datetime(2026, 7, 22, 12, 1, tzinfo=timezone.utc),
            edit_date=None,
        )

        assert listener._poller_startup_action(message, history) == "baseline"

    def test_legacy_history_cutoff_adds_safe_overlap_before_startup(
        self,
        tmp_path,
    ):
        path = tmp_path / "trade_events.jsonl"
        rows = [
            {
                "ts": "2026-07-22T11:59:58+00:00",
                "sig": "canal2_278",
                "ev": "telegram_raw",
                "channel": "canal2",
                "chat_id": -1003908582492,
                "message_id": 278,
                "edit_date_utc": "2026-07-22T11:59:57+00:00",
            },
            {
                "ts": "2026-07-22T11:59:59+00:00",
                "sig": "bot",
                "ev": "heartbeat",
            },
            {
                "ts": "2026-07-22T14:05:13+00:00",
                "sig": "bot",
                "ev": "session_started",
            },
            {
                "ts": "2026-07-22T14:05:14+00:00",
                "sig": "canal2_279",
                "ev": "telegram_raw",
                "channel": "canal2",
                "chat_id": -1003908582492,
                "message_id": 279,
                "edit_date_utc": None,
            },
        ]
        path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )

        history = listener._load_poller_startup_history(
            "canal2",
            -1003908582492,
            path=path,
        )

        assert history["coverage_cutoff"] == datetime(
            2026, 7, 21, 11, 59, 59, tzinfo=timezone.utc
        )
        assert history["message_versions"] == {
            278: "2026-07-22T11:59:57+00:00",
            279: None,
        }

    def test_explicit_channel_coverage_survives_a_later_failed_startup(
        self,
        tmp_path,
    ):
        path = tmp_path / "trade_events.jsonl"
        rows = [
            {
                "ts": "2026-07-22T11:55:00+00:00",
                "sig": "bot",
                "ev": "telegram_poll_coverage",
                "channel": "canal2",
                "channel_id": -1003908582492,
                "covered_through_utc": "2026-07-22T11:53:00+00:00",
            },
            {
                "ts": "2026-07-22T12:05:00+00:00",
                "sig": "bot",
                "ev": "session_started",
            },
            {
                "ts": "2026-07-22T12:05:01+00:00",
                "sig": "bot",
                "ev": "telegram_history_backoff",
            },
            {
                "ts": "2026-07-22T12:06:00+00:00",
                "sig": "bot",
                "ev": "session_started",
            },
        ]
        path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )

        history = listener._load_poller_startup_history(
            "canal2",
            -1003908582492,
            path=path,
        )
        missed = SimpleNamespace(
            id=279,
            date=datetime(2026, 7, 22, 11, 58, tzinfo=timezone.utc),
            edit_date=None,
        )

        assert history["coverage_cutoff"] == datetime(
            2026, 7, 22, 11, 53, tzinfo=timezone.utc
        )
        assert listener._poller_startup_action(missed, history) == "new"

    def test_raw_capture_after_processing_contract_is_not_treated_as_done(
        self,
        tmp_path,
    ):
        path = tmp_path / "trade_events.jsonl"
        rows = [
            {
                "ts": "2026-07-22T12:00:00+00:00",
                "sig": "bot",
                "ev": "telegram_processing_contract",
                "channel": "canal2",
                "channel_id": -1003908582492,
                "activated_utc": "2026-07-22T12:00:00+00:00",
            },
            {
                "ts": "2026-07-22T12:01:00+00:00",
                "sig": "canal2_279",
                "ev": "telegram_raw",
                "channel": "canal2",
                "chat_id": -1003908582492,
                "message_id": 279,
                "edit_date_utc": None,
            },
        ]
        path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )
        message = SimpleNamespace(
            id=279,
            date=datetime(2026, 7, 22, 12, 1, tzinfo=timezone.utc),
            edit_date=None,
        )

        history = listener._load_poller_startup_history(
            "canal2",
            -1003908582492,
            path=path,
        )

        assert listener._poller_startup_action(message, history) == "new"

        rows.append({
            "ts": "2026-07-22T12:01:01+00:00",
            "sig": "canal2_279",
            "ev": "telegram_processed",
            "channel": "canal2",
            "chat_id": -1003908582492,
            "message_id": 279,
            "revision_token": "new",
        })
        path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )
        history = listener._load_poller_startup_history(
            "canal2",
            -1003908582492,
            path=path,
        )

        assert listener._poller_startup_action(message, history) == "seen"

    def test_unprocessed_raw_revision_moves_recovery_cutoff_back(
        self,
        tmp_path,
    ):
        path = tmp_path / "trade_events.jsonl"
        rows = [
            {
                "ts": "2026-07-22T12:00:00+00:00",
                "sig": "bot",
                "ev": "telegram_processing_contract",
                "channel": "canal2",
                "channel_id": -1003908582492,
                "activated_utc": "2026-07-22T12:00:00+00:00",
            },
            {
                "ts": "2026-07-22T14:00:00+00:00",
                "sig": "bot",
                "ev": "telegram_poll_coverage",
                "channel": "canal2",
                "channel_id": -1003908582492,
                "covered_through_utc": "2026-07-22T13:58:00+00:00",
            },
            {
                "ts": "2026-07-22T14:00:01+00:00",
                "sig": "canal2_279",
                "ev": "telegram_raw",
                "channel": "canal2",
                "chat_id": -1003908582492,
                "message_id": 279,
                "date_utc": "2026-07-22T12:30:00+00:00",
                "edit_date_utc": None,
            },
        ]
        path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )

        history = listener._load_poller_startup_history(
            "canal2",
            -1003908582492,
            path=path,
        )

        assert history["coverage_cutoff"] == datetime(
            2026, 7, 22, 12, 28, tzinfo=timezone.utc
        )

    @pytest.mark.asyncio
    async def test_dispatch_marks_processed_only_after_handler_completes(
        self,
        monkeypatch,
    ):
        self._reset_poller_state()
        message = SimpleNamespace(
            id=279,
            chat_id=-1003908582492,
            date=datetime(2026, 7, 22, 12, 1, tzinfo=timezone.utc),
            edit_date=None,
        )
        events = []
        monkeypatch.setattr(
            listener.journal,
            "event",
            lambda sig, ev, **fields: events.append((sig, ev, fields)),
        )
        flushes = []
        monkeypatch.setattr(
            listener.journal,
            "flush_events",
            lambda timeout=10.0: flushes.append(timeout) or True,
        )

        async def successful(msg, label="Canal2", dedup=True):
            assert not any(ev == "telegram_processed" for _, ev, _ in events)
            listener._new_msg_already_seen("canal2", msg.id)

        monkeypatch.setattr(listener, "_process_canal2_new", successful)

        await listener._dispatch_telegram_message(
            message,
            "canal2",
            "new",
            label="Canal2_catchup",
        )

        assert [ev for _, ev, _ in events] == [
            "telegram_decision_started",
            "telegram_processed",
        ]
        assert flushes == []

        async def failed(msg, label="Canal2", dedup=True):
            raise RuntimeError("interrupted")

        failed_message = SimpleNamespace(
            id=280,
            chat_id=-1003908582492,
            date=message.date,
            edit_date=None,
        )
        monkeypatch.setattr(listener, "_process_canal2_new", failed)
        with pytest.raises(RuntimeError, match="interrupted"):
            await listener._dispatch_telegram_message(
                failed_message,
                "canal2",
                "new",
                label="Canal2_catchup",
            )

        assert [ev for _, ev, _ in events] == [
            "telegram_decision_started",
            "telegram_processed",
            "telegram_decision_started",
            "telegram_processing_failed",
        ]
        assert flushes == []

    @pytest.mark.asyncio
    async def test_dispatch_processes_even_when_raw_receipt_failed(
        self,
        monkeypatch,
    ):
        self._reset_poller_state()
        message = SimpleNamespace(
            id=280,
            chat_id=-1003908582492,
            date=datetime(2026, 7, 22, 12, 1, tzinfo=timezone.utc),
            edit_date=None,
        )
        failed_receipt = object()
        processed = []
        monkeypatch.setattr(
            listener.journal,
            "confirm_event",
            lambda receipt, timeout=10.0: receipt is not failed_receipt,
        )

        async def process(msg, label="Canal2", dedup=True):
            processed.append(msg.id)

        monkeypatch.setattr(listener, "_process_canal2_new", process)

        dispatched = await listener._dispatch_telegram_message(
            message,
            "canal2",
            "new",
            raw_receipt=failed_receipt,
        )

        assert dispatched is True
        assert processed == [280]

    @pytest.mark.asyncio
    async def test_failed_handler_releases_early_dedup_claim_for_retry(
        self,
        monkeypatch,
    ):
        self._reset_poller_state()
        message = SimpleNamespace(
            id=282,
            chat_id=-1003908582492,
            date=datetime(2026, 7, 22, 12, 1, tzinfo=timezone.utc),
            edit_date=None,
        )
        monkeypatch.setattr(
            listener.journal,
            "flush_events",
            lambda timeout=10.0: True,
        )
        events = []

        def capture(sig, ev, **fields):
            events.append((sig, ev, fields))

        monkeypatch.setattr(listener.journal, "event", capture)

        async def fail_after_claim(msg, label="Canal2", dedup=True):
            listener._new_msg_already_seen("canal2", msg.id)
            causal_trace.new_action_id()
            raise RuntimeError("handler failed")

        monkeypatch.setattr(
            listener,
            "_process_canal2_new",
            fail_after_claim,
        )
        with pytest.raises(RuntimeError, match="handler failed"):
            await listener._dispatch_telegram_message(
                message,
                "canal2",
                "new",
            )

        assert ("canal2", 282) not in listener._seen_new_msg_ids
        failed = next(
            fields
            for _, ev, fields in events
            if ev == "telegram_processing_failed"
        )
        assert failed["message_revision_id"].startswith("msgrev_")
        assert failed["decision_id"].startswith("decision_")
        assert len(failed["declared_action_ids"]) == 1
        assert failed["declared_action_count"] == 1
        assert failed["exception_type"] == "RuntimeError"
        assert failed["exception_message"] == "handler failed"

        processed = []

        async def succeed(msg, label="Canal2", dedup=True):
            assert listener._new_msg_already_seen(
                "canal2",
                msg.id,
            ) is False
            processed.append(msg.id)

        monkeypatch.setattr(listener, "_process_canal2_new", succeed)
        assert await listener._dispatch_telegram_message(
            message,
            "canal2",
            "new",
        ) is True
        assert processed == [282]

    @pytest.mark.asyncio
    async def test_cancelled_handler_records_failure_without_waiting_on_disk(
        self,
        monkeypatch,
    ):
        self._reset_poller_state()
        message = SimpleNamespace(
            id=284,
            chat_id=-1003908582492,
            date=datetime(2026, 7, 22, 12, 1, tzinfo=timezone.utc),
            edit_date=None,
        )
        raw_receipt = object()
        failed_receipt = object()
        events = []
        confirmed = []

        def capture(sig, ev, **fields):
            events.append((sig, ev, fields))
            return failed_receipt

        def confirm(receipt, timeout=10.0):
            confirmed.append((receipt, timeout))
            return True

        monkeypatch.setattr(listener.journal, "event", capture)
        monkeypatch.setattr(listener.journal, "confirm_event", confirm)

        async def cancelled(msg, label="Canal2", dedup=True):
            listener._new_msg_already_seen("canal2", msg.id)
            raise asyncio.CancelledError

        monkeypatch.setattr(listener, "_process_canal2_new", cancelled)

        with pytest.raises(asyncio.CancelledError):
            await listener._dispatch_telegram_message(
                message,
                "canal2",
                "new",
                raw_receipt=raw_receipt,
            )

        assert confirmed == []
        assert [ev for _, ev, _ in events] == [
            "telegram_decision_started",
            "telegram_processing_failed",
        ]
        assert ("canal2", 284) not in listener._seen_new_msg_ids

    @pytest.mark.asyncio
    async def test_journal_enqueue_result_never_repeats_actions(
        self,
        monkeypatch,
    ):
        self._reset_poller_state()
        message = SimpleNamespace(
            id=283,
            chat_id=-1003908582492,
            date=datetime(2026, 7, 22, 12, 1, tzinfo=timezone.utc),
            edit_date=None,
        )
        failed_receipt = object()
        successful_receipt = object()
        processed_events = []

        def capture(sig, ev, **fields):
            if ev != "telegram_processed":
                return successful_receipt
            processed_events.append(fields)
            return (
                failed_receipt
                if len(processed_events) == 1
                else successful_receipt
            )

        monkeypatch.setattr(listener.journal, "event", capture)
        monkeypatch.setattr(
            listener.journal,
            "confirm_event",
            lambda receipt, timeout=10.0: receipt is not failed_receipt,
        )
        actions = []

        async def process(msg, label="Canal2", dedup=True):
            listener._new_msg_already_seen("canal2", msg.id)
            actions.append(msg.id)
            causal_trace.new_action_id()

        monkeypatch.setattr(listener, "_process_canal2_new", process)

        assert await listener._dispatch_telegram_message(
            message,
            "canal2",
            "new",
            raw_receipt=successful_receipt,
        ) is True
        assert await listener._dispatch_telegram_message(
            message,
            "canal2",
            "new",
            raw_receipt=successful_receipt,
        ) is True

        assert actions == [283]
        assert len(processed_events) == 1

    @pytest.mark.asyncio
    async def test_dispatch_binds_one_revision_and_decision_then_resets(
        self,
        monkeypatch,
    ):
        self._reset_poller_state()
        message = SimpleNamespace(
            id=281,
            chat_id=-1003908582492,
            text="BUY NOW",
            message="BUY NOW",
            date=datetime(2026, 7, 22, 12, 1, tzinfo=timezone.utc),
            edit_date=None,
            sticker=None,
            photo=None,
            document=None,
            reply_to=None,
        )
        events = []

        def capture(sig, ev, **fields):
            inherited = causal_trace.current_fields()
            inherited.update(fields)
            events.append((sig, ev, inherited))

        monkeypatch.setattr(listener.journal, "event", capture)
        monkeypatch.setattr(
            listener.journal,
            "flush_events",
            lambda timeout=10.0: True,
        )

        async def successful(msg, label="Canal2", dedup=True):
            listener.journal.event(
                f"canal2_{msg.id}",
                "processing_probe",
            )
            action_id = causal_trace.new_action_id()
            listener.journal.event(
                f"canal2_{msg.id}",
                "action_probe",
                action_id=action_id,
            )
            listener._new_msg_already_seen("canal2", msg.id)

        monkeypatch.setattr(listener, "_process_canal2_new", successful)

        listener._msg_diag(message, "canal2", "new")
        assert await listener._dispatch_telegram_message(
            message,
            "canal2",
            "new",
            label="Canal2_catchup",
        ) is True

        raw = next(row for row in events if row[1] == "telegram_raw")
        probe = next(row for row in events if row[1] == "processing_probe")
        processed = next(
            row for row in events if row[1] == "telegram_processed")
        started = next(
            row for row in events
            if row[1] == "telegram_decision_started"
        )
        revision_id = raw[2]["message_revision_id"]

        assert probe[2]["message_revision_id"] == revision_id
        assert processed[2]["message_revision_id"] == revision_id
        assert started[2]["message_revision_id"] == revision_id
        assert started[2]["decision_id"] == processed[2]["decision_id"]
        assert probe[2]["decision_id"] == processed[2]["decision_id"]
        action = next(row for row in events if row[1] == "action_probe")
        assert processed[2]["declared_action_ids"] == [
            action[2]["action_id"],
        ]
        assert processed[2]["declared_action_count"] == 1
        assert causal_trace.current_fields() == {}

    @pytest.mark.asyncio
    async def test_mt5_worker_keeps_bound_causal_context(self):
        with causal_trace.bind_message_revision(
            "msgrev_worker",
            decision_id="decision_worker",
        ):
            worker_fields = await listener._run(
                causal_trace.current_fields
            )

        assert worker_fields == {
            "message_revision_id": "msgrev_worker",
            "decision_id": "decision_worker",
        }

    @pytest.mark.asyncio
    async def test_canal1_edit_missed_during_downtime_is_dispatched(
        self,
        monkeypatch,
    ):
        self._reset_poller_state()
        edited = SimpleNamespace(
            id=20700,
            chat_id=-1001642806869,
            date=datetime(2026, 7, 22, 11, 59, tzinfo=timezone.utc),
            edit_date=datetime(2026, 7, 22, 12, 3, tzinfo=timezone.utc),
        )

        class FakeClient:
            async def get_messages(self, channel_id, limit):
                return [edited]

        processed = []
        monkeypatch.setattr(listener, "client", FakeClient())
        monkeypatch.setattr(
            listener,
            "_load_poller_startup_history",
            lambda channel, channel_id: {
                "has_channel_history": True,
                "coverage_cutoff": datetime(
                    2026, 7, 22, 11, 58, tzinfo=timezone.utc
                ),
                "message_versions": {
                    20700: "2026-07-22T11:59:30+00:00"
                },
            },
        )
        monkeypatch.setattr(listener, "_msg_diag", lambda *args: None)
        monkeypatch.setattr(listener.journal, "event", lambda *args, **kwargs: None)

        async def process_edit(msg):
            processed.append(msg.id)

        monkeypatch.setattr(listener, "_process_canal1_edit", process_edit)

        assert await listener._poller_initial_scan_channel(
            -1001642806869,
            "canal1",
        ) is True
        assert processed == [20700]

    @pytest.mark.asyncio
    async def test_initial_scan_dispatches_only_downtime_messages(
        self,
        monkeypatch,
    ):
        self._reset_poller_state()
        old = SimpleNamespace(
            id=278,
            date=datetime(2026, 7, 22, 11, 59, 35, tzinfo=timezone.utc),
            edit_date=datetime(
                2026, 7, 22, 11, 59, 57, tzinfo=timezone.utc
            ),
        )
        missed = SimpleNamespace(
            id=279,
            date=datetime(2026, 7, 22, 12, 1, tzinfo=timezone.utc),
            edit_date=None,
        )

        class FakeClient:
            async def get_messages(self, channel_id, limit):
                assert channel_id == -1003908582492
                assert limit == listener._POLL_STARTUP_SCAN_LIMIT
                return [missed, old]

        processed = []
        monkeypatch.setattr(listener, "client", FakeClient())
        monkeypatch.setattr(
            listener,
            "_load_poller_startup_history",
            lambda channel, channel_id: {
                "has_channel_history": True,
                "coverage_cutoff": datetime(
                    2026, 7, 22, 11, 59, 58, tzinfo=timezone.utc
                ),
                "message_versions": {
                    278: "2026-07-22T11:59:57+00:00"
                },
            },
        )
        monkeypatch.setattr(listener, "_msg_diag", lambda *args: None)

        async def process_new(msg, label):
            processed.append((msg.id, label))

        monkeypatch.setattr(listener, "_process_canal2_new", process_new)
        monkeypatch.setattr(
            listener,
            "_process_canal2_edit",
            lambda *args, **kwargs: pytest.fail("unchanged message was edited"),
        )
        monkeypatch.setattr(listener.journal, "event", lambda *args, **kwargs: None)

        assert await listener._poller_initial_scan_channel(
            -1003908582492,
            "canal2",
        ) is True
        assert processed == [(279, "Canal2_catchup")]
        assert "canal2" in listener._poller_initialized_channels

    @pytest.mark.asyncio
    async def test_initial_scan_dispatches_revision_missed_during_downtime(
        self,
        monkeypatch,
    ):
        self._reset_poller_state()
        edited = SimpleNamespace(
            id=278,
            date=datetime(2026, 7, 22, 11, 59, 35, tzinfo=timezone.utc),
            edit_date=datetime(2026, 7, 22, 12, 3, tzinfo=timezone.utc),
        )

        class FakeClient:
            async def get_messages(self, channel_id, limit):
                return [edited]

        processed = []
        monkeypatch.setattr(listener, "client", FakeClient())
        monkeypatch.setattr(
            listener,
            "_load_poller_startup_history",
            lambda channel, channel_id: {
                "has_channel_history": True,
                "coverage_cutoff": datetime(
                    2026, 7, 22, 11, 59, 58, tzinfo=timezone.utc
                ),
                "message_versions": {
                    278: "2026-07-22T11:59:57+00:00"
                },
            },
        )
        monkeypatch.setattr(listener, "_msg_diag", lambda *args: None)
        monkeypatch.setattr(
            listener,
            "_process_canal2_new",
            lambda *args, **kwargs: pytest.fail("edit was treated as new"),
        )

        async def process_edit(msg, label):
            processed.append((msg.id, label))

        monkeypatch.setattr(listener, "_process_canal2_edit", process_edit)
        monkeypatch.setattr(listener.journal, "event", lambda *args, **kwargs: None)

        assert await listener._poller_initial_scan_channel(
            -1003908582492,
            "canal2",
        ) is True
        assert processed == [(278, "Canal2_catchup")]

    @pytest.mark.asyncio
    async def test_startup_fetch_paginates_until_previous_coverage(
        self,
        monkeypatch,
    ):
        cutoff = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
        messages = {
            4: SimpleNamespace(
                id=4,
                date=cutoff + timedelta(minutes=4),
                edit_date=None,
            ),
            3: SimpleNamespace(
                id=3,
                date=cutoff + timedelta(minutes=3),
                edit_date=None,
            ),
            2: SimpleNamespace(
                id=2,
                date=cutoff + timedelta(minutes=2),
                edit_date=None,
            ),
            1: SimpleNamespace(
                id=1,
                date=cutoff - timedelta(minutes=1),
                edit_date=None,
            ),
        }

        class FakeClient:
            def __init__(self):
                self.offsets = []

            async def get_messages(self, channel_id, limit, **kwargs):
                self.offsets.append(kwargs.get("offset_id"))
                if kwargs.get("offset_id") is None:
                    return [messages[4], messages[3]]
                return [messages[2], messages[1]]

        fake_client = FakeClient()
        monkeypatch.setattr(listener, "client", fake_client)
        monkeypatch.setattr(listener, "_POLL_STARTUP_SCAN_LIMIT", 2)
        history = {
            "has_channel_history": True,
            "coverage_cutoff": cutoff,
            "message_versions": {},
        }

        fetched, complete = await listener._poller_fetch_startup_messages(
            -1003908582492,
            "canal2",
            history,
        )

        assert complete is True
        assert [message.id for message in fetched] == [4, 3, 2, 1]
        assert fake_client.offsets == [None, 3]

    @pytest.mark.asyncio
    async def test_active_poll_paginates_until_a_known_message(
        self,
        monkeypatch,
    ):
        self._reset_poller_state()
        now = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
        messages = {
            msg_id: SimpleNamespace(
                id=msg_id,
                chat_id=-1003908582492,
                date=now + timedelta(seconds=msg_id),
                edit_date=None,
            )
            for msg_id in (1, 2, 3, 4)
        }
        listener._poller_msg_state[("canal2", 1)] = None

        class FakeClient:
            def __init__(self):
                self.offsets = []

            async def get_messages(self, channel_id, limit, **kwargs):
                self.offsets.append(kwargs.get("offset_id"))
                if kwargs.get("offset_id") is None:
                    return [messages[4], messages[3]]
                return [messages[2], messages[1]]

        fake_client = FakeClient()
        processed = []
        monkeypatch.setattr(listener, "client", fake_client)
        monkeypatch.setattr(listener, "_POLL_MSG_LIMIT", 2)
        monkeypatch.setattr(listener, "_msg_diag", lambda *args: None)
        monkeypatch.setattr(listener, "_poller_record_coverage", lambda *args, **kwargs: None)

        async def dispatch(msg, channel, kind, **kwargs):
            processed.append((msg.id, channel, kind))

        monkeypatch.setattr(listener, "_dispatch_telegram_message", dispatch)

        await listener._poll_channel(-1003908582492, "canal2")

        assert processed == [
            (2, "canal2", "new"),
            (3, "canal2", "new"),
            (4, "canal2", "new"),
        ]
        assert fake_client.offsets == [None, 3]


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


@pytest.mark.asyncio
async def test_delayed_bot_task_does_not_inherit_telegram_decision():
    observed = []

    async def probe():
        observed.append(causal_trace.current_fields())

    with causal_trace.bind_message_revision(
        "msgrev_origin",
        decision_id="decision_origin",
    ):
        task = listener._schedule_detached(probe())

    await task

    assert observed == [{}]
