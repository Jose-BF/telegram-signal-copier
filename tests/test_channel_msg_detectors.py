"""
test_channel_msg_detectors.py — Helpers PUROS de los detectores Batch B.

Batch B cubre tres fallos silenciosos relacionados con mensajes del canal:

  B1. MessageDeleted: el canal borra un mensaje que ya procesamos.
      Si era una señal con posición abierta → riesgo alto (el proveedor
      retracta el trade pero nosotros lo tenemos vivo). Si era gestión ya
      aplicada o señal cerrada → solo informativo.

  B2. canal1 MessageEdited: canal 1 NO tenía handler de edits. Si tras el
      texto inicial el proveedor edita TPs/SL, lo perdíamos y la posición
      seguía con los niveles viejos. Comparar parsed-nuevo vs Signal vivo
      y emitir anomaly si algo material cambió.

  B3. parser silent-failure: tras tightening de is_canal1_signal_text para
      cerrar el bug canal1_19778, queremos visibilidad de la diferencia
      entre el filtro LOOSE (anterior) y el STRICT (actual) — si un día
      la versión strict rechaza una señal real, lo veremos en logs.
"""
import asyncio

import pytest
from datetime import datetime, timedelta
from types import SimpleNamespace

import config
import listener
from listener import (
    _canal2_open_in_progress,
    _canal2_open_finished,
    _canal2_open_started,
    _classify_deleted_msg_impact,
    _handle_canal1_sticker,
    _open_canal1_from_text,
    _pop_deferred_canal2_entry_edit,
    _diff_canal1_edit,
    _process_canal1_edit,
    _process_canal2_edit,
    _process_canal2_new,
    _should_skip_stale_entry_signal,
    _strict_vs_loose_canal1_filter,
)
from state import Signal, StateManager


@pytest.fixture(autouse=True)
def _reset_entry_execution_gate():
    listener._entry_execution_gate.reset()
    listener._canal2_opening_msg_ids.clear()
    yield
    listener._entry_execution_gate.reset()
    listener._canal2_opening_msg_ids.clear()


# ────────────────────────── B1 — MessageDeleted ──────────────────────────

class TestClassifyDeletedMsgImpact:
    """Clasifica el impacto de un mensaje borrado:
       - signal_open: era una señal con posición ABIERTA. Crítico.
       - signal_closed: era una señal ya cerrada. Informativo.
       - management: era un alias de canal1 (mensaje de texto/gestión).
       - unknown: no estaba en state — chatter o pre-bot.
    """

    def _state_con_signal(self, channel, msg_id, status="open",
                          alias_id=None):
        st = StateManager()
        sig = Signal(channel=channel, message_id=msg_id,
                     direction="BUY", status=status)
        st.add(sig)
        if alias_id:
            st.alias(sig, alias_id)
        return st

    def test_signal_open_es_critico(self):
        st = self._state_con_signal("canal2", 12345, status="open")
        result = _classify_deleted_msg_impact("canal2", 12345, st)
        assert result["kind"] == "signal_open"
        assert result["sig_id"] == "canal2_12345"

    def test_signal_closed_es_informativo(self):
        st = self._state_con_signal("canal1", 9999, status="closed")
        result = _classify_deleted_msg_impact("canal1", 9999, st)
        assert result["kind"] == "signal_closed"
        assert result["sig_id"] == "canal1_9999"

    def test_management_alias_canal1(self):
        # canal1 abre con sticker (msg=10) y luego texto (msg=11) que
        # registra alias. Si el canal borra el msg=11, NO es la señal
        # principal sino el alias de gestión.
        st = self._state_con_signal("canal1", 10, alias_id=11)
        result = _classify_deleted_msg_impact("canal1", 11, st)
        # El alias 11 apunta al mismo Signal (msg_id=10). Lo clasificamos
        # como "management" para distinguirlo de la señal principal.
        assert result["kind"] == "management"
        # El sig_id que devolvemos es el del SIGNAL real (msg_id principal)
        # para que el log pueda correlacionar con la señal.
        assert result["sig_id"] == "canal1_10"

    def test_unknown_msg(self):
        st = StateManager()
        result = _classify_deleted_msg_impact("canal1", 77777, st)
        assert result["kind"] == "unknown"
        assert result["sig_id"] is None


# ────────────────────── B2 — canal1 MessageEdited diff ─────────────────────

class TestDiffCanal1Edit:
    """Compara el parsed nuevo vs lo que ya tiene la Signal.

    Si algo material cambió (TPs, SL, range, direction), el bot debe alertar
    porque la posición vive en MT5 con los valores VIEJOS. El operador tiene
    que decidir si reajustar manualmente.
    """

    def _sig(self, tps=None, sl=None, range_low=None, range_high=None,
             direction="BUY"):
        return Signal(channel="canal1", message_id=1, direction=direction,
                      tps=tps or [], sl=sl,
                      range_low=range_low, range_high=range_high)

    def test_nada_cambia(self):
        sig = self._sig(tps=[4700, 4710, 4720], sl=4680)
        parsed = {"tps": [4700, 4710, 4720], "sl": 4680, "direction": "BUY"}
        diff = _diff_canal1_edit(sig, parsed)
        assert diff["material_change"] is False

    def test_sl_cambia(self):
        sig = self._sig(tps=[4700, 4710], sl=4680)
        parsed = {"tps": [4700, 4710], "sl": 4670, "direction": "BUY"}
        diff = _diff_canal1_edit(sig, parsed)
        assert diff["material_change"] is True
        assert diff["sl_changed"] is True
        assert diff["previous"]["sl"] == 4680
        assert diff["new"]["sl"] == 4670

    def test_tps_cambia(self):
        sig = self._sig(tps=[4700, 4710], sl=4680)
        parsed = {"tps": [4700, 4715, 4725], "sl": 4680, "direction": "BUY"}
        diff = _diff_canal1_edit(sig, parsed)
        assert diff["material_change"] is True
        assert diff["tps_changed"] is True
        assert diff["new"]["tps"] == [4700, 4715, 4725]

    def test_direction_cambia_es_critico(self):
        # Cambiar BUY → SELL es un error fatal del proveedor que la posicion
        # MT5 está en la dirección incorrecta. Marcado especial.
        sig = self._sig(tps=[4700], sl=4680, direction="BUY")
        parsed = {"tps": [4700], "sl": 4680, "direction": "SELL"}
        diff = _diff_canal1_edit(sig, parsed)
        assert diff["material_change"] is True
        assert diff["direction_changed"] is True

    def test_parsed_sin_tps_no_cuenta_como_cambio(self):
        # Si el parser nuevo no extrajo TPs, no debemos asumir que han
        # cambiado a [] — el edit puede traer solo SL o un texto distinto
        # del que el parser saca menos info.
        sig = self._sig(tps=[4700, 4710], sl=4680)
        parsed = {"sl": 4680, "direction": "BUY"}   # sin "tps"
        diff = _diff_canal1_edit(sig, parsed)
        assert diff["tps_changed"] is False

    def test_parsed_sin_sl_no_cuenta_como_cambio(self):
        sig = self._sig(tps=[4700, 4710], sl=4680)
        parsed = {"tps": [4700, 4710], "direction": "BUY"}   # sin "sl"
        diff = _diff_canal1_edit(sig, parsed)
        assert diff["sl_changed"] is False

    def test_solo_rango_cambia_no_es_material(self):
        sig = self._sig(
            tps=[4151.0, 4146.0, 4142.0, 4138.0],
            sl=4175.0,
            range_low=4152.62,
            range_high=4157.62,
            direction="SELL",
        )
        parsed = {
            "direction": "SELL",
            "tps": [4151.0, 4146.0, 4142.0, 4138.0],
            "sl": 4175.0,
            "range": [4155.0, 4160.0],
        }

        diff = _diff_canal1_edit(sig, parsed)

        assert diff["range_changed"] is True
        assert diff["material_change"] is False
        assert diff["sl_changed"] is False
        assert diff["tps_changed"] is False
        assert diff["direction_changed"] is False


# ─────────── B3 — strict vs loose canal1 signal-text filter ───────────

class TestManagementReplyEdits:
    """Edits de mensajes de gestion reply->senal deben re-procesarse."""

    def _state_with_open_signal(self, channel="canal1"):
        st = StateManager()
        sig = Signal(channel=channel, message_id=100, direction="SELL",
                     status="open", tps=[99.0], sl=110.0)
        st.add(sig)
        st.alias(sig, 101)
        return st, sig

    @pytest.mark.asyncio
    async def test_canal1_management_reply_edit_executes_again(self, monkeypatch):
        st, sig = self._state_with_open_signal("canal1")
        calls = []

        async def fake_classify(text, signal=None):
            calls.append(("classify", text, signal))
            return [{"action": "MOVE_SL_TO_BE", "price": None, "confidence": 0.95}]

        async def fake_execute(signal, cl, raw_text="", tg_ts=None):
            calls.append(("execute", signal, cl, raw_text, tg_ts))

        monkeypatch.setattr("listener.state", st)
        monkeypatch.setattr("listener.classify_async", fake_classify)
        monkeypatch.setattr("listener._execute_action", fake_execute)

        msg = SimpleNamespace(
            id=102,
            text="Move SL to BE for 0% risk",
            message="Move SL to BE for 0% risk",
            date=datetime.utcnow(),
            edit_date=datetime.utcnow(),
            reply_to=SimpleNamespace(reply_to_msg_id=101),
        )

        await _process_canal1_edit(msg)

        assert calls[0] == ("classify", "Move SL to BE for 0% risk", sig)
        assert calls[1][0] == "execute"
        assert calls[1][1] is sig


    @pytest.mark.asyncio
    async def test_canal2_management_reply_edit_executes_again(self, monkeypatch):
        st, sig = self._state_with_open_signal("canal2")
        calls = []

        async def fake_classify(text, signal=None):
            calls.append(("classify", text, signal))
            return [{"action": "CLOSE_ALL", "price": None, "confidence": 0.95}]

        async def fake_execute(signal, cl, raw_text="", tg_ts=None):
            calls.append(("execute", signal, cl, raw_text, tg_ts))

        monkeypatch.setattr("listener.state", st)
        monkeypatch.setattr("listener.classify_async", fake_classify)
        monkeypatch.setattr("listener._execute_action", fake_execute)

        msg = SimpleNamespace(
            id=202,
            text="Close all now",
            message="Close all now",
            date=datetime.utcnow(),
            edit_date=datetime.utcnow(),
            reply_to=SimpleNamespace(reply_to_msg_id=101),
        )

        await _process_canal2_edit(msg)

        assert calls[0] == ("classify", "Close all now", sig)
        assert calls[1][0] == "execute"
        assert calls[1][1] is sig

    @pytest.mark.asyncio
    async def test_unknown_reply_routes_to_single_open_signal_after_resync(self, monkeypatch):
        st = StateManager()
        sig = Signal(channel="canal1", message_id=20801, direction="SELL",
                     status="open", tps=[4118.0], sl=4140.0)
        st.add(sig)
        calls = []
        events = []

        async def fake_classify(text, signal=None):
            calls.append(("classify", text, signal))
            return [{"action": "MOVE_SL_TO_BE", "price": None, "confidence": 0.95}]

        async def fake_execute(signal, cl, raw_text="", tg_ts=None):
            calls.append(("execute", signal, cl, raw_text, tg_ts))

        monkeypatch.setattr("listener.state", st)
        monkeypatch.setattr("listener.classify_async", fake_classify)
        monkeypatch.setattr("listener._execute_action", fake_execute)
        monkeypatch.setattr(listener.journal, "event",
                            lambda sig_id, ev, **kw: events.append((sig_id, ev, kw)))

        msg = SimpleNamespace(
            id=20803,
            text="Move SL to BE for 0% risk",
            message="Move SL to BE for 0% risk",
            date=datetime.utcnow(),
            edit_date=datetime.utcnow(),
            reply_to=SimpleNamespace(reply_to_msg_id=20802),
        )

        handled = await listener._process_management_reply_edit(
            msg, "canal1", "Canal1")

        assert handled is True
        assert calls[0] == ("classify", "Move SL to BE for 0% risk", sig)
        assert calls[1][0] == "execute"
        routed = [row for row in events
                  if row[1] == "management_reply_routed_by_open_signal"]
        assert routed
        assert routed[0][0] == "canal1_20801"
        assert routed[0][2]["reply_to_msg_id"] == 20802

    @pytest.mark.asyncio
    async def test_unknown_reply_with_no_unique_target_logs_tripwire(self, monkeypatch):
        st = StateManager()
        events = []
        anomalies = []

        monkeypatch.setattr("listener.state", st)
        monkeypatch.setattr(listener.journal, "event",
                            lambda sig_id, ev, **kw: events.append((sig_id, ev, kw)))
        monkeypatch.setattr(listener.journal, "anomaly",
                            lambda sig_id, category, severity, detail, **kw:
                            anomalies.append((sig_id, category, severity, detail, kw)))

        msg = SimpleNamespace(
            id=3027,
            text="make sure you are risk free",
            message="make sure you are risk free",
            date=datetime.utcnow(),
            edit_date=datetime.utcnow(),
            reply_to=SimpleNamespace(reply_to_msg_id=3023),
        )

        handled = await listener._process_management_reply_edit(
            msg, "canal2", "Canal2")

        assert handled is True
        unresolved = [row for row in events
                      if row[1] == "management_reply_unresolved"]
        assert unresolved
        assert unresolved[0][0] == "canal2_3027"
        assert unresolved[0][2]["reply_to_msg_id"] == 3023
        assert anomalies
        assert anomalies[0][0] == "canal2_3027"
        assert anomalies[0][2] == "critical"


class TestManagementActionDedup:
    def test_same_action_and_text_inside_window_is_duplicate(self, monkeypatch):
        listener._seen_management_actions.clear()
        first = datetime(2026, 6, 2, 12, 0, 0)
        second = datetime(2026, 6, 2, 12, 0, 5)

        assert listener._management_action_already_seen(
            "canal2_13228", "MOVE_SL_TO_BE",
            "+70 pips\nClose your first entries", None, now=first) is False
        assert listener._management_action_already_seen(
            "canal2_13228", "MOVE_SL_TO_BE",
            "+70 pips Close your first entries", None, now=second) is True

    def test_different_action_same_text_is_not_duplicate(self):
        listener._seen_management_actions.clear()
        now = datetime(2026, 6, 2, 12, 0, 0)

        assert listener._management_action_already_seen(
            "canal2_13228", "MOVE_SL_TO_BE",
            "Close your first entries", None, now=now) is False
        assert listener._management_action_already_seen(
            "canal2_13228", "CLOSE_FIRST",
            "Close your first entries", None, now=now) is False

    def test_same_action_after_window_is_processed_again(self):
        listener._seen_management_actions.clear()
        first = datetime(2026, 6, 2, 12, 0, 0)
        later = datetime(2026, 6, 2, 12, 1, 0)

        assert listener._management_action_already_seen(
            "canal2_13228", "MOVE_SL_TO_PRICE",
            "Move SL to 4533", 4533.0, now=first) is False
        assert listener._management_action_already_seen(
            "canal2_13228", "MOVE_SL_TO_PRICE",
            "Move SL to 4533", 4533.0, now=later) is False

    @pytest.mark.asyncio
    async def test_execute_actions_logs_and_skips_duplicate(self, monkeypatch):
        listener._seen_management_actions.clear()
        sig = Signal(channel="canal2", message_id=13228, direction="BUY",
                     status="open")
        executed = []
        events = []
        mgmt = []

        async def fake_execute_one(signal, classification, raw_text=""):
            executed.append((signal, classification, raw_text))

        monkeypatch.setattr(listener, "_execute_one_action", fake_execute_one)
        monkeypatch.setattr(listener.journal, "append_mgmt",
                            lambda *a, **kw: mgmt.append((a, kw)))
        monkeypatch.setattr(listener.journal, "event",
                            lambda sig_id, ev, **kw:
                            events.append((sig_id, ev, kw)))

        classification = {
            "action": "MOVE_SL_TO_BE",
            "price": None,
            "confidence": 0.95,
            "_reason": "regex",
        }
        raw_text = "Move your SL to BE"

        await listener._execute_actions(sig, [classification], raw_text=raw_text,
                                        tg_ts="2026-06-02T12:00:00")
        await listener._execute_actions(sig, [classification], raw_text=raw_text,
                                        tg_ts="2026-06-02T12:00:05")

        assert len(executed) == 1
        assert len(mgmt) == 1
        assert [ev for _, ev, _ in events].count("mgmt_msg") == 1
        duplicates = [row for row in events
                      if row[1] == "mgmt_msg_duplicate_skipped"]
        assert len(duplicates) == 1
        assert duplicates[0][2]["action"] == "MOVE_SL_TO_BE"


class TestCanal1SignalTextEdits:
    @pytest.mark.asyncio
    async def test_material_tp_edit_reapplies_levels_to_mt5(self, monkeypatch):
        st = StateManager()
        sig = Signal(channel="canal1", message_id=19935, direction="BUY",
                     status="open", tps=[4433.0, 4435.0, 4437.0, 4439.0],
                     sl=4420.0, market_ticket=1354050001)
        st.add(sig)
        st.alias(sig, 19936)
        parsed = {
            "direction": "BUY",
            "tps": [4435.0, 4440.0, 4445.0, 4450.0],
            "sl": 4420.0,
        }
        updates = []
        events = []
        anomalies = []

        async def fake_update(signal, parsed_arg, tg_ts=None):
            updates.append((signal, parsed_arg, tg_ts))

        monkeypatch.setattr(listener, "state", st)
        monkeypatch.setattr(listener, "parse_canal1_text", lambda _text: parsed)
        monkeypatch.setattr(listener, "_update_signal_from_parsed", fake_update)
        monkeypatch.setattr(listener.journal, "event",
                            lambda sig_id, ev, **kw:
                            events.append((sig_id, ev, kw)))
        monkeypatch.setattr(listener.journal, "anomaly",
                            lambda sig_id, category, severity, detail, **kw:
                            anomalies.append((sig_id, category, severity,
                                              detail, kw)))
        listener._seen_edits.clear()
        listener._seen_edits_order.clear()

        edit_ts = datetime.utcnow()
        msg = SimpleNamespace(
            id=19936,
            text="BUY GOLD NOW edited levels",
            message="BUY GOLD NOW edited levels",
            date=edit_ts,
            edit_date=edit_ts,
            reply_to=None,
        )

        await _process_canal1_edit(msg)

        assert updates == [(sig, parsed, edit_ts.isoformat(timespec="seconds"))]
        assert any(ev == "canal1_text_edit_auto_applied"
                   for _, ev, _ in events)
        assert anomalies == []


class TestCanal2DuplicateAlias:
    @pytest.mark.asyncio
    async def test_near_duplicate_new_message_aliases_existing_naked_signal(
            self, monkeypatch):
        st = StateManager()
        existing = Signal(channel="canal2", message_id=12828,
                          direction="BUY",
                          timestamp=datetime.utcnow() - timedelta(seconds=1),
                          market_ticket=1342891209,
                          market_fill_price=4563.06)
        st.add(existing)
        events = []
        anomalies = []

        async def fail_run(*args, **kwargs):
            raise AssertionError("duplicate alias must not open MT5 order")

        monkeypatch.setattr(listener, "state", st)
        monkeypatch.setattr(listener, "_run", fail_run)
        monkeypatch.setattr(listener.journal, "event",
                            lambda sig, ev, **kw: events.append((sig, ev, kw)))
        monkeypatch.setattr(listener.journal, "anomaly",
                            lambda sig, category, severity, detail, **kw:
                            anomalies.append((sig, category, severity, detail, kw)))
        listener._seen_new_msg_ids.clear()
        listener._seen_new_msgs_order.clear()

        msg = SimpleNamespace(
            id=12829,
            text="XAU USD BUY NOW",
            date=datetime.utcnow(),
            reply_to=None,
        )

        await _process_canal2_new(msg)

        assert st.get("canal2", 12829) is existing
        assert any(ev == "canal2_duplicate_alias_registered"
                   for _, ev, _ in events)
        assert any(category == "channel_msg" and severity == "warning"
                   for _, category, severity, _, _ in anomalies)


class TestCanal2OrphanEditRecovery:
    @pytest.mark.asyncio
    async def test_recovered_edit_then_new_delivery_opens_only_once(
            self, monkeypatch):
        """One Telegram message must create at most one exposure block.

        Telethon can deliver an edited entry before the new-message poller.
        The recovered edit opens the trade; the later new delivery must not
        create a second five-position block for the same message.
        """
        st = StateManager()
        orders = []
        events = []

        async def fake_run(fn, *args):
            return fn(*args)

        async def fake_open_extra_legs(sig, msg_id):
            return None

        async def fake_update(sig, parsed, tg_ts=None):
            return None

        def fake_open_market_with_fill(*args, **kwargs):
            orders.append((args, kwargs))
            return (1600000000 + len(orders), 4122.0)

        monkeypatch.setattr(listener, "state", st)
        monkeypatch.setattr(listener, "_run", fake_run)
        monkeypatch.setattr(listener, "compute_market_context",
                            lambda symbol: None)
        monkeypatch.setattr(listener.executor, "open_market_with_fill",
                            fake_open_market_with_fill)
        monkeypatch.setattr(listener.executor, "current_tick_safe",
                            lambda: {"bid": 4121.9, "ask": 4122.1,
                                     "spread": 0.2})
        monkeypatch.setattr(listener, "_open_extra_legs",
                            fake_open_extra_legs)
        monkeypatch.setattr(listener, "_update_signal_from_parsed",
                            fake_update)
        monkeypatch.setattr(listener, "_emit_same_direction_overlap_anomaly",
                            lambda sig: None)
        monkeypatch.setattr(listener, "_log_strategy_snapshot",
                            lambda *args, **kwargs: None)
        monkeypatch.setattr(listener.logger, "log_signal",
                            lambda sig, parsed: None)
        monkeypatch.setattr(
            listener.journal, "event",
            lambda sig, ev, **kw: events.append((sig, ev, kw)))
        monkeypatch.setattr(listener.journal, "begin_trade",
                            lambda *args, **kwargs: None)
        listener._seen_new_msg_ids.clear()
        listener._seen_new_msgs_order.clear()
        listener._seen_edits.clear()
        listener._seen_edits_order.clear()
        listener._canal2_opening_msg_ids.clear()
        listener._deferred_canal2_entry_edits.clear()

        now = datetime.utcnow()
        msg = SimpleNamespace(
            id=266,
            text=("XAU USD SELL NOW\n\n4122 - 4126\n\n"
                  "TP1 4119\nTP2 4117\nTP3 4115\n"
                  "TP4 4113\nTP5 4111\nSL 4130"),
            date=now - timedelta(seconds=2),
            edit_date=now,
            reply_to=None,
        )

        await _process_canal2_edit(msg, label="Canal2_poll")
        await _process_canal2_new(msg, label="Canal2_poll")

        assert len(orders) == 1
        assert st.get("canal2", 266) is not None
        assert any(ev == "canal2_entry_open_already_claimed"
                   for _, ev, _ in events)

    @pytest.mark.asyncio
    async def test_fresh_entry_edit_without_state_recovers_as_new_signal(
            self, monkeypatch):
        st = StateManager()
        recovered = []
        events = []

        async def fake_process_new(msg, label="Canal2", dedup=True):
            recovered.append((msg.id, label, dedup))

        monkeypatch.setattr(listener, "state", st)
        monkeypatch.setattr(listener, "_process_canal2_new", fake_process_new)
        monkeypatch.setattr(listener.journal, "event",
                            lambda sig, ev, **kw: events.append((sig, ev, kw)))
        monkeypatch.setattr(config, "STRATEGY_C2_ORPHAN_EDIT_MAX_AGE_S", 180.0,
                            raising=False)

        msg = SimpleNamespace(
            id=12879,
            text="XAU USD BUY NOW\n\n4509.5 - 4505.5",
            date=datetime.utcnow() - timedelta(seconds=18),
            edit_date=datetime.utcnow(),
            reply_to=None,
        )

        await _process_canal2_edit(msg)

        assert recovered == [(12879, "Canal2_recover", False)]
        assert any(ev == "canal2_orphan_entry_edit_recovered"
                   for _, ev, _ in events)

    @pytest.mark.asyncio
    async def test_entry_edit_during_market_open_is_deferred_not_recovered(
            self, monkeypatch):
        st = StateManager()
        recovered = []
        events = []

        async def fake_process_new(msg, label="Canal2", dedup=True):
            recovered.append((msg.id, label, dedup))

        monkeypatch.setattr(listener, "state", st)
        monkeypatch.setattr(listener, "_process_canal2_new", fake_process_new)
        monkeypatch.setattr(listener.journal, "event",
                            lambda sig, ev, **kw: events.append((sig, ev, kw)))
        listener._seen_edits.clear()
        listener._seen_edits_order.clear()
        listener._deferred_canal2_entry_edits.clear()
        _canal2_open_started(12887)

        msg = SimpleNamespace(
            id=12887,
            text=("XAU USD SELL NOW\n\n4517 - 4522\n\n"
                  "TP1 4514\nTP2 4511\nSL 4524"),
            date=datetime.utcnow() - timedelta(seconds=15),
            edit_date=datetime.utcnow(),
            reply_to=None,
        )

        try:
            await _process_canal2_edit(msg)
        finally:
            _canal2_open_finished(12887)

        deferred = _pop_deferred_canal2_entry_edit(12887)
        assert recovered == []
        assert deferred is not None
        assert "TP1 4514" in deferred["text"]
        assert any(ev == "canal2_orphan_entry_edit_deferred"
                   for _, ev, _ in events)

    @pytest.mark.asyncio
    async def test_successful_new_signal_applies_deferred_entry_edit(
            self, monkeypatch):
        st = StateManager()
        parsed_updates = []
        events = []

        async def fake_run(fn, *args):
            return fn(*args)

        async def fake_update(sig, parsed, tg_ts=None):
            parsed_updates.append((parsed, tg_ts))

        async def fake_open_extra_legs(sig, msg_id):
            return None

        monkeypatch.setattr(listener, "state", st)
        monkeypatch.setattr(listener, "_run", fake_run)
        monkeypatch.setattr(listener, "compute_market_context",
                            lambda symbol: None)
        monkeypatch.setattr(listener.executor, "open_market_with_fill",
                            lambda *args, **kwargs: (1348595935, 4519.02))
        monkeypatch.setattr(listener.executor, "current_tick_safe",
                            lambda: {"bid": 4518.9, "ask": 4519.1,
                                     "spread": 0.2})
        monkeypatch.setattr(listener, "_open_extra_legs", fake_open_extra_legs)
        monkeypatch.setattr(listener, "_update_signal_from_parsed",
                            fake_update)
        monkeypatch.setattr(listener, "_emit_same_direction_overlap_anomaly",
                            lambda sig: None)
        monkeypatch.setattr(listener, "_log_strategy_snapshot",
                            lambda *args, **kwargs: None)
        monkeypatch.setattr(listener.logger, "log_signal",
                            lambda sig, parsed: None)
        monkeypatch.setattr(listener.journal, "event",
                            lambda sig, ev, **kw: events.append((sig, ev, kw)))
        monkeypatch.setattr(listener.journal, "begin_trade",
                            lambda *args, **kwargs: None)
        listener._seen_new_msg_ids.clear()
        listener._seen_new_msgs_order.clear()
        listener._deferred_canal2_entry_edits.clear()

        edit_msg = SimpleNamespace(
            id=12887,
            text=("XAU USD SELL NOW\n\n4517 - 4522\n\n"
                  "TP1 4514\nTP2 4511\nSL 4524"),
            date=datetime.utcnow(),
            edit_date=datetime.utcnow(),
            reply_to=None,
        )
        listener._defer_canal2_entry_edit(edit_msg, edit_msg.text)

        msg = SimpleNamespace(
            id=12887,
            text="XAU USD SELL NOW",
            date=datetime.utcnow(),
            reply_to=None,
        )

        await _process_canal2_new(msg)

        assert st.get("canal2", 12887) is not None
        assert _pop_deferred_canal2_entry_edit(12887) is None
        assert any("tps" in parsed and parsed.get("sl") == 4524.0
                   for parsed, _ in parsed_updates)
        assert any(ev == "canal2_deferred_entry_edit_applied"
                   for _, ev, _ in events)

    @pytest.mark.asyncio
    async def test_invalid_initial_sl_opens_with_interpreted_sl(
            self, monkeypatch):
        st = StateManager()
        events = []
        orders = []
        parsed_updates = []

        async def fake_run(fn, *args):
            return fn(*args)

        async def fake_update(sig, parsed, tg_ts=None):
            parsed_updates.append((parsed, tg_ts))

        async def fake_open_extra_legs(sig, msg_id):
            return None

        def fake_open_market_with_fill(direction, lot, sl, tp, comment, magic):
            orders.append({
                "direction": direction, "lot": lot, "sl": sl,
                "tp": tp, "comment": comment, "magic": magic,
            })
            return (2571001, 4030.7)

        monkeypatch.setattr(listener, "state", st)
        monkeypatch.setattr(listener, "_run", fake_run)
        monkeypatch.setattr(listener, "compute_market_context",
                            lambda symbol: None)
        monkeypatch.setattr(listener.executor, "open_market_with_fill",
                            fake_open_market_with_fill)
        monkeypatch.setattr(listener.executor, "current_tick_safe",
                            lambda: {"bid": 4030.6, "ask": 4030.8,
                                     "spread": 0.2})
        monkeypatch.setattr(listener, "_open_extra_legs", fake_open_extra_legs)
        monkeypatch.setattr(listener, "_update_signal_from_parsed",
                            fake_update)
        monkeypatch.setattr(listener, "_emit_same_direction_overlap_anomaly",
                            lambda sig: None)
        monkeypatch.setattr(listener, "_log_strategy_snapshot",
                            lambda *args, **kwargs: None)
        monkeypatch.setattr(listener.logger, "log_signal",
                            lambda sig, parsed: None)
        monkeypatch.setattr(listener.journal, "event",
                            lambda sig, ev, **kw: events.append((sig, ev, kw)))
        monkeypatch.setattr(listener.journal, "begin_trade",
                            lambda *args, **kwargs: None)
        listener._seen_new_msg_ids.clear()
        listener._seen_new_msgs_order.clear()

        msg = SimpleNamespace(
            id=2571,
            text=("XAU USD SELL NOW\n\n4030.5 - 4035.5\n\n"
                  "TP1 4027.5\nTP2 4025.5\nTP3 4023\n"
                  "TP4 4021\nTP5 4017\nTP6 4000\nSL 4022"),
            date=datetime.utcnow(),
            reply_to=None,
        )

        await _process_canal2_new(msg)

        assert st.get("canal2", 2571) is not None
        assert orders == [{
            "direction": "SELL", "lot": config.LOT_SIZE, "sl": 4039.5,
            "tp": 4027.5, "comment": "c2_2571",
            "magic": config.magic_for("canal2"),
        }]
        assert any(parsed.get("sl") == 4039.5
                   for parsed, _ in parsed_updates)
        assert any(ev == "entry_levels_interpreted"
                   and kw["corrections"][0]["field"] == "sl"
                   for _, ev, kw in events)

    @pytest.mark.asyncio
    async def test_new_canal2_gold_format_opens_with_targets_block(
            self, monkeypatch):
        st = StateManager()
        events = []
        orders = []
        parsed_updates = []

        async def fake_run(fn, *args):
            return fn(*args)

        async def fake_update(sig, parsed, tg_ts=None):
            parsed_updates.append((parsed, tg_ts))

        async def fake_open_extra_legs(sig, msg_id):
            return None

        def fake_open_market_with_fill(direction, lot, sl, tp, comment, magic):
            orders.append({
                "direction": direction, "lot": lot, "sl": sl,
                "tp": tp, "comment": comment, "magic": magic,
            })
            return (2926001, 4067.1)

        monkeypatch.setattr(listener, "state", st)
        monkeypatch.setattr(listener, "_run", fake_run)
        monkeypatch.setattr(listener, "compute_market_context",
                            lambda symbol: None)
        monkeypatch.setattr(listener.executor, "open_market_with_fill",
                            fake_open_market_with_fill)
        monkeypatch.setattr(listener.executor, "current_tick_safe",
                            lambda: {"bid": 4067.0, "ask": 4067.2,
                                     "spread": 0.2})
        monkeypatch.setattr(listener, "_open_extra_legs", fake_open_extra_legs)
        monkeypatch.setattr(listener, "_update_signal_from_parsed",
                            fake_update)
        monkeypatch.setattr(listener, "_emit_same_direction_overlap_anomaly",
                            lambda sig: None)
        monkeypatch.setattr(listener, "_log_strategy_snapshot",
                            lambda *args, **kwargs: None)
        monkeypatch.setattr(listener.logger, "log_signal",
                            lambda sig, parsed: None)
        monkeypatch.setattr(listener.journal, "event",
                            lambda sig, ev, **kw: events.append((sig, ev, kw)))
        monkeypatch.setattr(listener.journal, "begin_trade",
                            lambda *args, **kwargs: None)
        listener._seen_new_msg_ids.clear()
        listener._seen_new_msgs_order.clear()

        msg = SimpleNamespace(
            id=2926,
            text=("Buy Gold Now\n\n4067.5 - 4062.5\n\n"
                  "Targets\n4069.5\n4071.5\n4073\nOpen\n\n"
                  "SL/ invalid 4059"),
            date=datetime.utcnow(),
            reply_to=None,
        )

        await _process_canal2_new(msg)

        assert st.get("canal2", 2926) is not None
        assert orders == [{
            "direction": "BUY", "lot": config.LOT_SIZE, "sl": 4059.0,
            "tp": 4069.5, "comment": "c2_2926",
            "magic": config.magic_for("canal2"),
        }]

    @pytest.mark.asyncio
    async def test_invalid_sl_reply_updates_provisional_levels(
            self, monkeypatch):
        st = StateManager()
        events = []
        orders = []
        parsed_updates = []

        async def fake_run(fn, *args):
            return fn(*args)

        async def fake_update(sig, parsed, tg_ts=None):
            parsed_updates.append((parsed, tg_ts))

        async def fake_open_extra_legs(sig, msg_id):
            return None

        def fake_open_market_with_fill(direction, lot, sl, tp, comment, magic):
            orders.append({
                "direction": direction, "lot": lot, "sl": sl,
                "tp": tp, "comment": comment, "magic": magic,
            })
            return (2571001, 4030.7)

        monkeypatch.setattr(listener, "state", st)
        monkeypatch.setattr(listener, "_run", fake_run)
        monkeypatch.setattr(listener, "compute_market_context",
                            lambda symbol: None)
        monkeypatch.setattr(listener.executor, "open_market_with_fill",
                            fake_open_market_with_fill)
        monkeypatch.setattr(listener.executor, "current_tick_safe",
                            lambda: {"bid": 4030.6, "ask": 4030.8,
                                     "spread": 0.2})
        monkeypatch.setattr(listener, "_open_extra_legs", fake_open_extra_legs)
        monkeypatch.setattr(listener, "_update_signal_from_parsed",
                            fake_update)
        monkeypatch.setattr(listener, "_emit_same_direction_overlap_anomaly",
                            lambda sig: None)
        monkeypatch.setattr(listener, "_log_strategy_snapshot",
                            lambda *args, **kwargs: None)
        monkeypatch.setattr(listener.logger, "log_signal",
                            lambda sig, parsed: None)
        monkeypatch.setattr(listener.journal, "event",
                            lambda sig, ev, **kw: events.append((sig, ev, kw)))
        monkeypatch.setattr(listener.journal, "begin_trade",
                            lambda *args, **kwargs: None)
        listener._seen_new_msg_ids.clear()
        listener._seen_new_msgs_order.clear()

        msg = SimpleNamespace(
            id=2571,
            text=("XAU USD SELL NOW\n\n4030.5 - 4035.5\n\n"
                  "TP1 4027.5\nTP2 4025.5\nTP3 4023\n"
                  "TP4 4021\nTP5 4017\nTP6 4000\nSL 4022"),
            date=datetime.utcnow(),
            reply_to=None,
        )
        await _process_canal2_new(msg)

        assert st.get("canal2", 2571) is not None

        reply = SimpleNamespace(
            id=2572,
            text="TP1 4027.5 SL 4038.5",
            date=datetime.utcnow() + timedelta(seconds=20),
            reply_to=SimpleNamespace(reply_to_msg_id=2571),
        )
        await _process_canal2_new(reply)

        assert st.get("canal2", 2571) is not None
        assert orders == [{
            "direction": "SELL", "lot": config.LOT_SIZE, "sl": 4039.5,
            "tp": 4027.5, "comment": "c2_2571",
            "magic": config.magic_for("canal2"),
        }]
        assert any(parsed.get("sl") == 4039.5 and len(parsed.get("tps", [])) == 6
                   for parsed, _ in parsed_updates)
        assert any(parsed.get("sl") == 4038.5 and parsed.get("tps") == [4027.5]
                   for parsed, _ in parsed_updates)
        assert any(ev == "entry_levels_interpreted"
                   for _, ev, _ in events)

    @pytest.mark.asyncio
    async def test_new_signal_marks_open_in_progress_before_first_await(
            self, monkeypatch):
        st = StateManager()
        marker_seen_during_context = []

        def fake_market_context(_symbol):
            marker_seen_during_context.append(
                _canal2_open_in_progress(12914))
            return None

        async def fake_run(fn, *args):
            return fn(*args)

        async def fake_open_extra_legs(sig, msg_id):
            return None

        async def fake_update(sig, parsed, tg_ts=None):
            return None

        monkeypatch.setattr(listener, "state", st)
        monkeypatch.setattr(listener, "_run", fake_run)
        monkeypatch.setattr(listener, "compute_market_context",
                            fake_market_context)
        monkeypatch.setattr(listener.executor, "open_market_with_fill",
                            lambda *args, **kwargs: (1352996249, 4494.45))
        monkeypatch.setattr(listener.executor, "current_tick_safe",
                            lambda: {"bid": 4494.4, "ask": 4494.6,
                                     "spread": 0.2})
        monkeypatch.setattr(listener, "_open_extra_legs", fake_open_extra_legs)
        monkeypatch.setattr(listener, "_update_signal_from_parsed",
                            fake_update)
        monkeypatch.setattr(listener, "_emit_same_direction_overlap_anomaly",
                            lambda sig: None)
        monkeypatch.setattr(listener, "_log_strategy_snapshot",
                            lambda *args, **kwargs: None)
        monkeypatch.setattr(listener.logger, "log_signal",
                            lambda sig, parsed: None)
        monkeypatch.setattr(listener.journal, "event", lambda *a, **kw: None)
        monkeypatch.setattr(listener.journal, "begin_trade",
                            lambda *args, **kwargs: None)
        listener._seen_new_msg_ids.clear()
        listener._seen_new_msgs_order.clear()
        listener._canal2_opening_msg_ids.clear()

        msg = SimpleNamespace(
            id=12914,
            text=("XAU USD SELL NOW\n\n4490 - 4495\n\n"
                  "TP1 4487\nTP2 4485\nTP3 4483\nSL 4499"),
            date=datetime.utcnow(),
            reply_to=None,
        )

        await _process_canal2_new(msg)

        assert marker_seen_during_context == [True]
        assert not _canal2_open_in_progress(12914)

    @pytest.mark.asyncio
    async def test_pre_fill_failure_releases_entry_claim(
            self, monkeypatch):
        st = StateManager()

        async def fake_run(fn, *args):
            return fn(*args)

        monkeypatch.setattr(listener, "state", st)
        monkeypatch.setattr(listener, "_run", fake_run)
        monkeypatch.setattr(listener, "compute_market_context",
                            lambda _symbol: None)
        monkeypatch.setattr(
            listener.executor,
            "current_tick_safe",
            lambda: (_ for _ in ()).throw(RuntimeError("tick unavailable")),
        )
        monkeypatch.setattr(listener.journal, "event", lambda *a, **kw: None)

        msg = SimpleNamespace(
            id=12915,
            text=("XAU USD SELL NOW\n\n4490 - 4495\n\n"
                  "TP1 4487\nTP2 4485\nTP3 4483\nSL 4499"),
            date=datetime.utcnow(),
            reply_to=None,
        )

        with pytest.raises(RuntimeError, match="tick unavailable"):
            await _process_canal2_new(msg, dedup=False)

        assert not _canal2_open_in_progress(12915)

    @pytest.mark.asyncio
    async def test_resynced_signal_identity_cannot_reopen_after_restart(
            self, monkeypatch):
        st = StateManager()
        existing = Signal(
            channel="canal2",
            message_id=12914,
            direction="BUY",
            timestamp=datetime.utcnow() - timedelta(minutes=10),
            market_ticket=2200001,
            market_fill_price=4056.5,
            status="open",
        )
        st.add(existing)
        events = []

        async def fail_run(*args, **kwargs):
            raise AssertionError(
                "a resynced Telegram identity must not reach MT5")

        monkeypatch.setattr(listener, "state", st)
        monkeypatch.setattr(listener, "_run", fail_run)
        monkeypatch.setattr(
            listener.journal,
            "event",
            lambda sig, ev, **kw: events.append((sig, ev, kw)),
        )
        listener._seen_new_msg_ids.clear()
        listener._seen_new_msgs_order.clear()

        msg = SimpleNamespace(
            id=12914,
            text=(
                "XAU USD BUY NOW\n\n4051.5 - 4056.5\n\n"
                "TP1 4059.5\nTP2 4061.5\nSL 4047.5"
            ),
            date=datetime.utcnow(),
            reply_to=None,
        )

        await _process_canal2_new(msg)

        assert st.get("canal2", 12914) is existing
        assert listener._canal2_open_already_committed(12914)
        assert any(
            ev == "canal2_entry_open_already_claimed"
            and payload.get("reason") == "state_already_contains_signal"
            for _, ev, payload in events
        )

    @pytest.mark.asyncio
    async def test_stale_entry_edit_without_state_is_not_recovered(
            self, monkeypatch):
        st = StateManager()
        recovered = []
        anomalies = []

        async def fake_process_new(msg, label="Canal2", dedup=True):
            recovered.append(msg.id)

        monkeypatch.setattr(listener, "state", st)
        monkeypatch.setattr(listener, "_process_canal2_new", fake_process_new)
        monkeypatch.setattr(listener.journal, "anomaly",
                            lambda sig, category, severity, detail, **kw:
                            anomalies.append((sig, category, severity, detail, kw)))
        monkeypatch.setattr(config, "STRATEGY_C2_ORPHAN_EDIT_MAX_AGE_S", 180.0,
                            raising=False)

        msg = SimpleNamespace(
            id=12000,
            text="XAU USD SELL NOW\n\n4515 - 4519",
            date=datetime.utcnow() - timedelta(minutes=20),
            edit_date=datetime.utcnow(),
            reply_to=None,
        )

        await _process_canal2_edit(msg)

        assert recovered == []
        assert any(category == "channel_msg" and severity == "warning"
                   for _, category, severity, _, _ in anomalies)


class TestGlobalEntryLevelInterpretation:
    @pytest.mark.asyncio
    async def test_resynced_canal1_sticker_cannot_reopen_after_restart(
            self, monkeypatch):
        st = StateManager()
        existing = Signal(
            channel="canal1",
            message_id=2100,
            direction="BUY",
            timestamp=datetime.utcnow() - timedelta(minutes=10),
            market_ticket=2100001,
            market_fill_price=4018.7,
            status="open",
        )
        st.add(existing)
        events = []

        async def fail_run(*args, **kwargs):
            raise AssertionError(
                "a resynced sticker identity must not reach MT5")

        monkeypatch.setattr(listener, "state", st)
        monkeypatch.setattr(config, "CANAL1_BUY_STICKER_ID", 999)
        monkeypatch.setattr(listener, "_run", fail_run)
        monkeypatch.setattr(
            listener.journal,
            "event",
            lambda sig, ev, **kw: events.append((sig, ev, kw)),
        )

        msg = SimpleNamespace(
            id=2100,
            sticker=SimpleNamespace(id=999),
            date=datetime.utcnow(),
            reply_to=None,
        )

        await _handle_canal1_sticker(msg)

        assert st.get("canal1", 2100) is existing
        assert listener._entry_execution_gate.committed("canal1", 2100)
        assert any(
            ev == "canal1_entry_open_already_claimed"
            and payload.get("reason") == "state_already_contains_signal"
            for _, ev, payload in events
        )

    @pytest.mark.asyncio
    async def test_canal1_sticker_applies_inferred_levels_after_fill(
            self, monkeypatch):
        st = StateManager()
        events = []
        parsed_updates = []

        async def fake_run(fn, *args):
            return fn(*args)

        async def fake_update(sig, parsed, tg_ts=None):
            parsed_updates.append((parsed, tg_ts))

        async def fake_open_extra_legs(sig, msg_id):
            return None

        monkeypatch.setattr(listener, "state", st)
        monkeypatch.setattr(config, "CANAL1_BUY_STICKER_ID", 999)
        monkeypatch.setattr(config, "STRATEGY_C1_ENTRY_MODE", "scale_out")
        monkeypatch.setattr(listener, "_run", fake_run)
        monkeypatch.setattr(listener, "compute_market_context",
                            lambda symbol: None)
        monkeypatch.setattr(listener.executor, "open_market_with_fill",
                            lambda *args, **kwargs: (2100001, 4018.7))
        monkeypatch.setattr(listener.executor, "current_tick_safe",
                            lambda: {"bid": 4018.5, "ask": 4018.7,
                                     "spread": 0.2})
        monkeypatch.setattr(listener, "_open_extra_legs", fake_open_extra_legs)
        monkeypatch.setattr(listener, "_update_signal_from_parsed",
                            fake_update)
        monkeypatch.setattr(listener, "_log_strategy_snapshot",
                            lambda *args, **kwargs: None)
        monkeypatch.setattr(listener.journal, "event",
                            lambda sig, ev, **kw: events.append((sig, ev, kw)))
        monkeypatch.setattr(listener.journal, "begin_trade",
                            lambda *args, **kwargs: None)

        msg = SimpleNamespace(
            id=2100,
            sticker=SimpleNamespace(id=999),
            date=datetime.utcnow(),
            reply_to=None,
        )

        await _handle_canal1_sticker(msg)

        signal = st.get("canal1", 2100)
        assert signal is not None
        assert signal.entry_mode == "scale_out"
        assert any(parsed.get("range") == (4013.7, 4018.7)
                   and parsed.get("sl") == 4009.7
                   for parsed, _ in parsed_updates)
        assert any(ev == "entry_levels_interpreted"
                   for _, ev, _ in events)

    @pytest.mark.asyncio
    async def test_canal1_exact_price_text_preserves_executed_scale_out_mode(
            self, monkeypatch):
        st = StateManager()
        signal = Signal(
            channel="canal1",
            message_id=2101,
            direction="BUY",
            market_ticket=2101001,
            market_fill_price=4810.0,
            extra_market_tickets=[2101002, 2101003, 2101004],
            extra_market_fill_prices=[4810.1, 4810.2, 4810.3],
            entry_mode="scale_out",
        )
        st.add(signal)
        applied_events = []

        async def fake_apply(sig, parsed, channel, **kwargs):
            sig.tps = list(parsed["tps"])
            sig.sl = parsed["sl"]
            return parsed

        monkeypatch.setattr(listener, "state", st)
        monkeypatch.setattr(config, "STRATEGY_C1_ENTRY_MODE", "scale_out")
        monkeypatch.setattr(listener, "_log_telegram_understood",
                            lambda *args, **kwargs: None)
        monkeypatch.setattr(listener, "_log_strategy_snapshot",
                            lambda *args, **kwargs: None)
        monkeypatch.setattr(listener, "_apply_interpreted_entry_levels",
                            fake_apply)
        monkeypatch.setattr(listener.logger, "log_signal",
                            lambda *args, **kwargs: None)
        monkeypatch.setattr(
            listener.journal,
            "event",
            lambda sig_id, event, **payload:
                applied_events.append((sig_id, event, payload)),
        )

        msg = SimpleNamespace(id=2102, date=datetime.utcnow())
        text = (
            "BUY GOLD NOW @4810\n"
            "TP1: 4815\nTP2: 4820\nTP3: 4825\nTP4: 4830\n"
            "SL: 4800"
        )

        await listener._handle_canal1_text(msg, text)

        assert signal.entry_mode == "scale_out"
        assert any(
            event == "canal1_text_applied"
            and payload["entry_mode"] == "scale_out"
            for _, event, payload in applied_events
        )


class TestStaleEntryGuard:
    def test_entry_signal_older_than_cutoff_is_stale(self):
        msg = SimpleNamespace(
            id=13083,
            date=datetime.utcnow() - timedelta(seconds=420),
            edit_date=None,
            reply_to=None,
        )

        skip, age_s = _should_skip_stale_entry_signal(msg, max_age_s=120.0)

        assert skip is True
        assert age_s >= 419

    def test_entry_signal_without_timestamp_is_not_stale(self):
        msg = SimpleNamespace(id=13083, date=None, edit_date=None,
                              reply_to=None)

        skip, age_s = _should_skip_stale_entry_signal(msg, max_age_s=120.0)

        assert skip is False
        assert age_s is None

    @pytest.mark.asyncio
    async def test_stale_canal2_new_signal_does_not_open_market(
            self, monkeypatch):
        st = StateManager()
        events = []
        anomalies = []

        async def fail_run(*args, **kwargs):
            raise AssertionError("stale entry must not call MT5")

        monkeypatch.setattr(listener, "state", st)
        monkeypatch.setattr(listener, "_run", fail_run)
        monkeypatch.setattr(config, "STRATEGY_ENTRY_MAX_TG_DELAY_S",
                            120.0, raising=False)
        monkeypatch.setattr(listener.journal, "event",
                            lambda sig, ev, **kw: events.append((sig, ev, kw)))
        monkeypatch.setattr(listener.journal, "anomaly",
                            lambda sig, category, severity, detail, **kw:
                            anomalies.append((sig, category, severity, detail, kw)))
        listener._seen_new_msg_ids.clear()
        listener._seen_new_msgs_order.clear()

        msg = SimpleNamespace(
            id=13083,
            text="XAU USD BUY NOW\n\n4515 - 4511\n\nTP1 4518\nSL 4510",
            date=datetime.utcnow() - timedelta(minutes=70),
            reply_to=None,
        )

        await _process_canal2_new(msg)

        assert st.get("canal2", 13083) is None
        assert any(ev == "signal_skipped" and
                   kw.get("reason") == "stale_entry_signal"
                   for _, ev, kw in events)
        assert any(category == "channel_msg" and severity == "critical"
                   for _, category, severity, _, _ in anomalies)

    @pytest.mark.asyncio
    async def test_stale_canal1_text_only_signal_does_not_open_market(
            self, monkeypatch):
        events = []
        anomalies = []

        async def fail_run(*args, **kwargs):
            raise AssertionError("stale text-only signal must not call MT5")

        monkeypatch.setattr(listener, "_run", fail_run)
        monkeypatch.setattr(config, "STRATEGY_ENTRY_MAX_TG_DELAY_S",
                            120.0, raising=False)
        monkeypatch.setattr(listener.journal, "event",
                            lambda sig, ev, **kw: events.append((sig, ev, kw)))
        monkeypatch.setattr(listener.journal, "anomaly",
                            lambda sig, category, severity, detail, **kw:
                            anomalies.append((sig, category, severity, detail, kw)))

        msg = SimpleNamespace(
            id=19998,
            text="BUY GOLD NOW 4518-24\nTP1 4529\nSL 4505",
            date=datetime.utcnow() - timedelta(minutes=50),
            reply_to=None,
        )

        result = await _open_canal1_from_text(msg, {
            "direction": "BUY",
            "range": (4518.0, 4524.0),
            "tps": [4529.0],
            "sl": 4505.0,
        })

        assert result is None
        assert any(ev == "signal_skipped" and
                   kw.get("trigger") == "text_only"
                   for _, ev, kw in events)
        assert any(category == "channel_msg" and severity == "critical"
                   for _, category, severity, _, _ in anomalies)

    @pytest.mark.asyncio
    async def test_resynced_canal1_text_identity_cannot_reopen_after_restart(
            self, monkeypatch):
        st = StateManager()
        existing = Signal(
            channel="canal1",
            message_id=19998,
            direction="BUY",
            timestamp=datetime.utcnow() - timedelta(minutes=10),
            market_ticket=2199981,
            market_fill_price=4518.0,
            status="closed",
        )
        st.add(existing)
        events = []

        async def fail_run(*args, **kwargs):
            raise AssertionError(
                "a resynced text identity must not reach MT5")

        monkeypatch.setattr(listener, "state", st)
        monkeypatch.setattr(listener, "_run", fail_run)
        monkeypatch.setattr(
            listener.journal,
            "event",
            lambda sig, ev, **kw: events.append((sig, ev, kw)),
        )
        msg = SimpleNamespace(
            id=19998,
            text="BUY GOLD NOW 4518-24\nTP1 4529\nSL 4505",
            date=datetime.utcnow(),
            reply_to=None,
        )

        result = await _open_canal1_from_text(msg, {
            "direction": "BUY",
            "range": (4518.0, 4524.0),
            "tps": [4529.0],
            "sl": 4505.0,
        })

        assert result is None
        assert listener._entry_execution_gate.committed("canal1", 19998)
        assert any(
            ev == "canal1_entry_open_already_claimed"
            and payload.get("reason") == "state_already_contains_signal"
            for _, ev, payload in events
        )


class TestCanal1DuplicateSticker:
    @pytest.mark.asyncio
    async def test_sticker_and_text_entry_paths_are_serialized(
            self, monkeypatch):
        sticker_started = asyncio.Event()
        release_sticker = asyncio.Event()
        text_started = asyncio.Event()

        async def slow_sticker(_msg):
            sticker_started.set()
            await release_sticker.wait()

        async def observe_text(_msg, _text):
            text_started.set()

        monkeypatch.setattr(listener, "_handle_canal1_sticker", slow_sticker)
        monkeypatch.setattr(listener, "_handle_canal1_text", observe_text)
        listener._seen_new_msg_ids.clear()
        listener._seen_new_msgs_order.clear()

        sticker = SimpleNamespace(
            id=3001,
            sticker=SimpleNamespace(id=999),
            text=None,
            date=datetime.utcnow(),
            reply_to=None,
        )
        text = SimpleNamespace(
            id=3002,
            sticker=None,
            text="BUY GOLD NOW 4518-24\nTP1 4529\nSL 4505",
            date=datetime.utcnow(),
            reply_to=None,
        )

        sticker_task = asyncio.create_task(
            listener._process_canal1_new(sticker))
        await sticker_started.wait()
        text_task = asyncio.create_task(listener._process_canal1_new(text))
        await asyncio.sleep(0)
        overlapped = text_started.is_set()

        release_sticker.set()
        await asyncio.gather(sticker_task, text_task)

        assert overlapped is False
        assert text_started.is_set() is True

    @pytest.mark.asyncio
    async def test_duplicate_sticker_aliases_existing_signal_without_new_order(
            self, monkeypatch):
        st = StateManager()
        orders = []
        events = []
        buy_sticker_id = 777

        async def fake_run(fn, *args):
            return fn(*args)

        def fake_open_market_with_fill(direction, lot, sl, tp, comment, magic):
            orders.append(comment)
            return (1353614545, 4448.11)

        async def fake_open_extra_legs(sig, msg_id):
            return None

        monkeypatch.setattr(listener, "state", st)
        monkeypatch.setattr(config, "CANAL1_BUY_STICKER_ID", buy_sticker_id,
                            raising=False)
        monkeypatch.setattr(config, "CANAL1_SELL_STICKER_ID", 778,
                            raising=False)
        monkeypatch.setattr(config, "STRATEGY_C1_DUPLICATE_STICKER_WINDOW_S",
                            5.0, raising=False)
        monkeypatch.setattr(listener, "_run", fake_run)
        monkeypatch.setattr(listener, "compute_market_context",
                            lambda _symbol: None)
        monkeypatch.setattr(listener.executor, "open_market_with_fill",
                            fake_open_market_with_fill)
        monkeypatch.setattr(listener.executor, "current_tick_safe",
                            lambda: {"bid": 4448.0, "ask": 4448.2,
                                     "spread": 0.2})
        monkeypatch.setattr(listener, "_open_extra_legs", fake_open_extra_legs)
        monkeypatch.setattr(listener, "_log_strategy_snapshot",
                            lambda *args, **kwargs: None)
        monkeypatch.setattr(listener.journal, "event",
                            lambda sig, ev, **kw: events.append((sig, ev, kw)))
        monkeypatch.setattr(listener.journal, "begin_trade",
                            lambda *args, **kwargs: None)

        msg1 = SimpleNamespace(
            id=19920,
            sticker=SimpleNamespace(id=buy_sticker_id),
            date=datetime.utcnow(),
        )
        msg2 = SimpleNamespace(
            id=19921,
            sticker=SimpleNamespace(id=buy_sticker_id),
            date=datetime.utcnow() + timedelta(seconds=2),
        )

        await _handle_canal1_sticker(msg1)
        await _handle_canal1_sticker(msg2)

        assert orders == ["c1_19920"]
        assert st.get("canal1", 19921) is st.get("canal1", 19920)
        assert any(ev == "canal1_duplicate_sticker_alias_registered"
                   for _, ev, _ in events)


class TestStrictVsLooseCanal1Filter:
    """Tras tightening de is_canal1_signal_text (commit d4bf1a6 — bug
    canal1_19778), tenemos un filtro STRICT que requiere TP con nivel
    numérico. Loggear cuando un texto pasaría el LOOSE pero falla el STRICT
    nos da visibilidad de:
      a) cuántos chatter textos rechaza el strict que el loose pasaba (bien).
      b) si algún día rechaza una señal real (mal — habría que ajustar).
    """

    def test_chatter_caso_real_canal1_19778(self):
        # El texto exacto que abrió 4 posiciones naked y se perdieron $129
        # antes del fix d4bf1a6. STRICT lo rechaza, LOOSE lo aceptaba.
        text = ("GOLD UPDATE\n"
                "After the earlier TP1 hit we expect a buy continuation "
                "soon. Stay tuned for the next trade.")
        result = _strict_vs_loose_canal1_filter(text)
        assert result["strict"] is False
        assert result["loose"] is True
        assert result["strict_blocked_loose_signal"] is True

    def test_real_signal_pasa_ambos(self):
        # Señal real con TPs numéricos pasa los 2 filtros, no genera anomaly.
        text = ("BUY GOLD @ 4700\n"
                "TP1 4710  TP2 4720\n"
                "SL 4685")
        result = _strict_vs_loose_canal1_filter(text)
        assert result["strict"] is True
        assert result["loose"] is True
        assert result["strict_blocked_loose_signal"] is False

    def test_texto_ajeno_falla_ambos(self):
        # Mensaje sin dirección ni gold → ambos rechazan, no es interesante.
        text = "Good morning everyone."
        result = _strict_vs_loose_canal1_filter(text)
        assert result["strict"] is False
        assert result["loose"] is False
        assert result["strict_blocked_loose_signal"] is False
