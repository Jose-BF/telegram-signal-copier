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
    listener._canal2_zone_plans.clear()
    yield
    listener._entry_execution_gate.reset()
    listener._canal2_opening_msg_ids.clear()
    listener._canal2_zone_plans.clear()


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

    def test_reply_message_ids_are_isolated_by_channel(self, monkeypatch):
        st = StateManager()
        canal1_signal = Signal(
            channel="canal1",
            message_id=100,
            direction="BUY",
            status="open",
        )
        st.add(canal1_signal)
        monkeypatch.setattr(listener, "state", st)

        target, route = listener._resolve_management_reply_target(
            "canal2",
            100,
            allow_single_open_fallback=False,
        )

        assert target is None
        assert route == "unknown_reply_target"

    def test_test_channel_can_explicitly_resolve_either_provider(
            self, monkeypatch):
        st = StateManager()
        canal1_signal = Signal(
            channel="canal1",
            message_id=101,
            direction="SELL",
            status="open",
        )
        st.add(canal1_signal)
        monkeypatch.setattr(listener, "state", st)

        target, route = listener._resolve_management_reply_target(
            "canal2",
            101,
            allow_single_open_fallback=False,
            allow_cross_channel=True,
        )

        assert target is canal1_signal
        assert route == "direct"


class TestManagementReplyAncestry:
    @pytest.mark.asyncio
    async def test_reply_through_media_routes_only_to_its_original_signal(
            self, monkeypatch):
        st = StateManager()
        intended = Signal(
            channel="canal1", message_id=21361, direction="BUY",
            market_ticket=1700001361,
        )
        other = Signal(
            channel="canal1", message_id=21362, direction="SELL",
            market_ticket=1700001362,
        )
        st.add(intended)
        st.add(other)
        events = []

        original = SimpleNamespace(
            id=21361,
            text="BUY GOLD NOW",
            reply_to=None,
        )

        async def media_get_reply():
            return original

        media = SimpleNamespace(
            id=21365,
            text="",
            reply_to=SimpleNamespace(reply_to_msg_id=21361),
            get_reply_message=media_get_reply,
        )

        async def management_get_reply():
            return media

        management = SimpleNamespace(
            id=21366,
            text="Move SL to BE",
            reply_to=SimpleNamespace(reply_to_msg_id=21365),
            get_reply_message=management_get_reply,
        )

        monkeypatch.setattr(listener, "state", st)
        monkeypatch.setattr(
            listener.journal,
            "event",
            lambda sig, ev, **kw: events.append((sig, ev, kw)),
        )

        target, route = await (
            listener._resolve_management_reply_target_with_ancestry(
                management,
                "canal1",
                21365,
                allow_single_open_fallback=True,
            )
        )

        assert target is intended
        assert route == "reply_ancestry_hop_2"
        routed = next(
            payload for _, ev, payload in events
            if ev == "management_reply_routed_by_ancestry"
        )
        assert routed["source_message_id"] == 21366
        assert routed["target_signal_id"] == "canal1_21361"
        assert routed["ancestry_message_ids"] == [21365, 21361]

    @pytest.mark.asyncio
    async def test_ancestry_never_guesses_between_multiple_open_signals(
            self, monkeypatch):
        st = StateManager()
        st.add(Signal("canal2", 730, "SELL", market_ticket=1700000730))
        st.add(Signal("canal2", 731, "BUY", market_ticket=1700000731))

        async def get_reply_message():
            return SimpleNamespace(
                id=729,
                text="Sell Gold Now",
                reply_to=None,
            )

        msg = SimpleNamespace(
            id=732,
            text="Move SL to BE",
            reply_to=SimpleNamespace(reply_to_msg_id=729),
            get_reply_message=get_reply_message,
        )
        monkeypatch.setattr(listener, "state", st)
        monkeypatch.setattr(listener.journal, "event", lambda *a, **kw: None)

        target, route = await (
            listener._resolve_management_reply_target_with_ancestry(
                msg,
                "canal2",
                729,
                allow_single_open_fallback=False,
            )
        )

        assert target is None
        assert route == "reply_root_identity_unproven"


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
        assert mgmt[0][1]["outcome"] == "requested"
        assert [ev for _, ev, _ in events].count("mgmt_msg") == 1
        outcomes = [row for row in events
                    if row[1] == "management_action_outcome"]
        assert len(outcomes) == 1
        assert outcomes[0][2]["outcome"] == "requested"
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

        async def fake_update(signal, parsed_arg, tg_ts=None, **kwargs):
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
    async def test_reply_command_then_same_standalone_command_is_aliased(
            self, monkeypatch):
        st = StateManager()
        entry_ts = datetime.utcnow() - timedelta(seconds=1)
        existing = Signal(channel="canal2", message_id=12828,
                          direction="BUY",
                          timestamp=entry_ts,
                          market_ticket=1342891209,
                          market_fill_price=4563.06,
                          telegram_entry_command_key="BUY GOLD NOW",
                          telegram_entry_was_reply=True,
                          telegram_entry_reply_to_message_id=12820)
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
            text="Buy Gold Now",
            date=entry_ts + timedelta(seconds=1),
            reply_to=None,
        )

        await _process_canal2_new(msg)

        assert st.get("canal2", 12829) is existing
        assert any(ev == "canal2_duplicate_alias_registered"
                   for _, ev, _ in events)
        assert any(category == "channel_msg" and severity == "warning"
                   for _, category, severity, _, _ in anomalies)

    @pytest.mark.asyncio
    async def test_repeated_plain_command_eight_seconds_later_is_alias(
            self, monkeypatch):
        st = StateManager()
        existing = Signal(
            channel="canal2",
            message_id=585,
            direction="SELL",
            timestamp=datetime.utcnow() - timedelta(seconds=8),
            market_ticket=1671689001,
            market_fill_price=4002.8,
            telegram_entry_command_key="SELL GOLD NOW",
            telegram_entry_was_reply=True,
            telegram_entry_reply_to_message_id=580,
        )
        st.add(existing)

        async def fail_run(*args, **kwargs):
            raise AssertionError("repeated provider command must not reopen")

        monkeypatch.setattr(listener, "state", st)
        monkeypatch.setattr(listener, "_run", fail_run)
        monkeypatch.setattr(listener.journal, "event", lambda *a, **kw: None)
        monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **kw: None)
        listener._seen_new_msg_ids.clear()
        listener._seen_new_msgs_order.clear()

        msg = SimpleNamespace(
            id=586,
            text="Sell gold now",
            date=datetime.utcnow(),
            reply_to=None,
        )

        await _process_canal2_new(msg)

        assert st.get("canal2", 586) is existing

    def test_two_independent_standalone_orders_are_not_aliased(self):
        now = datetime.utcnow()
        existing = Signal(
            channel="canal2",
            message_id=900,
            direction="SELL",
            timestamp=now - timedelta(seconds=2),
            telegram_entry_command_key="SELL GOLD NOW",
            telegram_entry_was_reply=False,
        )

        duplicate = listener._canal2_duplicate_alias_candidate(
            901,
            "SELL",
            now,
            {},
            [existing],
            10.0,
            raw_text="Sell Gold Now",
            is_reply=False,
        )

        assert duplicate is None


class TestCanal2SemanticRouting:
    @pytest.mark.asyncio
    async def test_entry_reply_is_routed_as_reentry_before_management(
            self, monkeypatch):
        events = []
        understood = []

        def fail_management_route(*args, **kwargs):
            raise AssertionError("entry reply must not enter management route")

        monkeypatch.setattr(
            listener, "_resolve_management_reply_target",
            fail_management_route,
        )
        monkeypatch.setattr(config, "STRATEGY_ENTRY_MAX_TG_DELAY_S", 1.0)
        monkeypatch.setattr(
            listener.journal,
            "event",
            lambda sig, ev, **kw: events.append((sig, ev, kw)),
        )
        monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **kw: None)
        monkeypatch.setattr(
            listener,
            "_log_telegram_understood",
            lambda sig, **kw: understood.append((sig, kw)),
        )
        listener._seen_new_msg_ids.clear()
        listener._seen_new_msgs_order.clear()

        msg = SimpleNamespace(
            id=585,
            text="Sell Gold Now",
            date=datetime.utcnow() - timedelta(seconds=5),
            reply_to=SimpleNamespace(reply_to_msg_id=580),
        )

        await _process_canal2_new(msg)

        assert any(
            ev == "signal_skipped"
            and payload.get("reason") == "stale_entry_signal"
            for _, ev, payload in events
        )
        assert any(
            ev == "canal2_reply_entry_recognized"
            and payload.get("reply_to_msg_id") == 580
            for _, ev, payload in events
        )
        assert understood[0][0] == "canal2_585"
        assert understood[0][1]["is_reply"] is True
        assert understood[0][1]["reply_to_msg_id"] == 580

    @pytest.mark.asyncio
    async def test_future_zone_is_registered_without_opening_market(
            self, monkeypatch):
        events = []

        async def fail_run(*args, **kwargs):
            raise AssertionError("future zone must not open market")

        monkeypatch.setattr(listener, "_run", fail_run)
        monkeypatch.setattr(
            listener.journal,
            "event",
            lambda sig, ev, **kw: events.append((sig, ev, kw)),
        )
        monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **kw: None)
        monkeypatch.setattr(
            listener,
            "_schedule_detached",
            lambda value: value.close(),
        )
        listener._seen_new_msg_ids.clear()
        listener._seen_new_msgs_order.clear()
        listener._canal2_zone_plans.clear()

        msg = SimpleNamespace(
            id=612,
            text=(
                "Next Sell Zone at 4030\n\n"
                "Bear in mind FOMC at 7\n\n"
                "Look for a quick reaction"
            ),
            date=datetime.utcnow(),
            reply_to=None,
        )

        await _process_canal2_new(msg)

        assert listener._canal2_zone_plans[612]["direction"] == "SELL"
        assert listener._canal2_zone_plans[612]["zones"] == [[4030.0, 4030.0]]
        assert any(
            ev == "canal2_zone_plan_registered"
            and payload["execution_behavior"] == "observe_only"
            for _, ev, payload in events
        )

    @pytest.mark.asyncio
    async def test_real_sell_limit_plan_never_manages_open_trade(
            self, monkeypatch):
        st = StateManager()
        open_signal = Signal(
            channel="canal2",
            message_id=3600,
            direction="BUY",
            market_ticket=1700003600,
        )
        st.add(open_signal)
        events = []

        async def fail_execute(*args, **kwargs):
            raise AssertionError(
                "a future Sell Limit plan must not manage the open BUY"
            )

        monkeypatch.setattr(listener, "state", st)
        monkeypatch.setattr(listener, "_execute_action", fail_execute)
        monkeypatch.setattr(
            listener.journal,
            "event",
            lambda sig, ev, **kw: events.append((sig, ev, kw)),
        )
        monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **kw: None)
        monkeypatch.setattr(
            listener,
            "_schedule_detached",
            lambda value: value.close(),
        )
        listener._seen_new_msg_ids.clear()
        listener._seen_new_msgs_order.clear()

        msg = SimpleNamespace(
            id=3610,
            text=(
                "Good Evening All\n\n"
                "I am still holding my buys risk free\n\n"
                "I am looking at a possible sell around 4121 - 4125 area\n\n"
                "We can expect a reaction at this zone.\n\n"
                "You can consider a Sell Limit with the following parameters\n\n"
                "Sell Limit\n"
                "Entry 4121-4125\n"
                "Taps 4118/4115/4110/4100\n"
                "SL 4131"
            ),
            date=datetime.utcnow(),
            reply_to=None,
        )

        await _process_canal2_new(msg)

        assert listener._canal2_zone_plans[3610]["direction"] == "SELL"
        assert listener._canal2_zone_plans[3610]["zones"] == [
            [4121.0, 4125.0]
        ]
        assert any(
            ev == "canal2_zone_plan_registered"
            for _, ev, _ in events
        )

    @pytest.mark.asyncio
    async def test_zone_reply_is_attached_to_registered_plan(
            self, monkeypatch):
        events = []
        listener._canal2_zone_plans.clear()
        listener._canal2_zone_plans[612] = {
            "direction": "SELL",
            "zones": [[4030.0, 4030.0]],
            "target": None,
        }

        monkeypatch.setattr(
            listener.journal,
            "event",
            lambda sig, ev, **kw: events.append((sig, ev, kw)),
        )
        monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **kw: None)
        listener._seen_new_msg_ids.clear()
        listener._seen_new_msgs_order.clear()

        msg = SimpleNamespace(
            id=615,
            text="Take profit from layers",
            date=datetime.utcnow(),
            reply_to=SimpleNamespace(reply_to_msg_id=612),
        )

        await _process_canal2_new(msg)

        attached = [
            payload for _, ev, payload in events
            if ev == "canal2_zone_plan_management"
        ]
        assert attached
        assert attached[0]["zone_plan_signal_id"] == "canal2_612"
        assert attached[0]["actions"] == ["CLOSE_PARTIAL"]

    @pytest.mark.asyncio
    async def test_zone_failed_invalidates_registered_plan(
            self, monkeypatch):
        events = []
        listener._canal2_zone_plans.clear()
        plan = {
            "direction": "BUY",
            "zones": [[4055.0, 4061.0]],
            "target": None,
        }
        listener._canal2_zone_plans[2944] = plan

        monkeypatch.setattr(
            listener.journal,
            "event",
            lambda sig, ev, **kw: events.append((sig, ev, kw)),
        )
        monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **kw: None)
        monkeypatch.setattr(
            listener,
            "_schedule_detached",
            lambda value: value.close(),
        )
        listener._seen_new_msg_ids.clear()
        listener._seen_new_msgs_order.clear()

        msg = SimpleNamespace(
            id=2947,
            text="Zone failed",
            date=datetime.utcnow(),
            reply_to=SimpleNamespace(reply_to_msg_id=2944),
        )

        await _process_canal2_new(msg)

        assert plan["status"] == "invalidated"
        attached = [
            payload for _, ev, payload in events
            if ev == "canal2_zone_plan_management"
        ]
        assert attached[0]["actions"] == ["ZONE_INVALIDATED"]
        assert attached[0]["actionable"] is False

    @pytest.mark.asyncio
    async def test_zone_with_price_and_failed_invalidates_existing_plan(
            self, monkeypatch):
        plan = {
            "message_id": 2944,
            "direction": "SELL",
            "zones": [[4030.0, 4030.0]],
            "target": None,
            "status": "active",
        }
        listener._canal2_zone_plans[2944] = plan
        events = []

        monkeypatch.setattr(
            listener.journal,
            "event",
            lambda sig, ev, **kw: events.append((sig, ev, kw)),
        )
        monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **kw: None)
        monkeypatch.setattr(
            listener,
            "_schedule_detached",
            lambda value: value.close(),
        )
        listener._seen_new_msg_ids.clear()
        listener._seen_new_msgs_order.clear()

        msg = SimpleNamespace(
            id=2948,
            text="Sell zone at 4030 failed",
            date=datetime.utcnow(),
            reply_to=SimpleNamespace(reply_to_msg_id=2944),
        )

        await _process_canal2_new(msg)

        assert plan["status"] == "invalidated"
        assert listener._canal2_zone_plans[2948] is plan
        assert any(
            ev == "canal2_zone_plan_management"
            and payload["actions"] == ["ZONE_INVALIDATED"]
            for _, ev, payload in events
        )

    def test_legacy_zone_observation_is_not_armed_after_upgrade(
            self, tmp_path):
        path = tmp_path / "trade_events.jsonl"
        path.write_text(
            (
                '{"ts":"2026-07-29T15:06:48+00:00",'
                '"sig":"canal2_599",'
                '"ev":"canal2_zone_plan_registered",'
                '"channel":"canal2","direction":"SELL",'
                '"zones":[[4007.0,4007.0],[4010.0,4010.0]],'
                '"target":null,"source_kind":"reply",'
                '"thread_root_message_id":598,'
                '"tg_ts":"2026-07-29T15:06:46+00:00",'
                '"raw_text":"4007\\n4010\\nSell areas"}\n'
            ),
            encoding="utf-8",
        )

        restored = listener.restore_canal2_zone_plans_from_journal(path)

        assert restored == 0
        assert listener._canal2_zone_plans == {}

    def test_schema_v2_zone_and_reply_alias_are_restored(self, tmp_path):
        path = tmp_path / "trade_events.jsonl"
        rows = [
            {
                "ts": "2026-08-05T09:00:00+00:00",
                "sig": "canal2_700",
                "ev": "canal2_zone_plan_created",
                "lifecycle_schema_version": 2,
                "message_id": 700,
                "thread_root_message_id": 700,
                "direction": "BUY",
                "zones": [[4053.0, 4058.0]],
                "tps": [4060.0, 4062.0],
                "sl": 4050.0,
                "target": None,
                "status": "armed",
                "expires_utc": "2099-08-06T09:00:00+00:00",
                "raw_text": "Gold Buy Zone",
            },
            {
                "ts": "2026-08-05T09:01:00+00:00",
                "sig": "canal2_701",
                "ev": "canal2_zone_plan_alias_registered",
                "lifecycle_schema_version": 2,
                "zone_plan_message_id": 700,
                "alias_message_id": 701,
            },
        ]
        path.write_text(
            "\n".join(__import__("json").dumps(row) for row in rows) + "\n",
            encoding="utf-8",
        )

        restored = listener.restore_canal2_zone_plans_from_journal(path)

        assert restored == 1
        assert listener._canal2_zone_plans[700] is (
            listener._canal2_zone_plans[701]
        )
        assert listener._canal2_zone_plans[700]["status"] == "armed"
        assert listener._canal2_zone_plans[700]["tps"] == [4060.0, 4062.0]

    @pytest.mark.asyncio
    async def test_recursive_zone_replies_keep_original_identity(
            self, monkeypatch):
        events = []
        monkeypatch.setattr(
            listener.journal,
            "event",
            lambda sig, ev, **kw: events.append((sig, ev, kw)),
        )
        monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **kw: None)
        monkeypatch.setattr(
            listener,
            "_schedule_detached",
            lambda value: value.close(),
        )
        listener._seen_new_msg_ids.clear()
        listener._seen_new_msgs_order.clear()

        root = SimpleNamespace(
            id=700,
            text=(
                "Gold Buy Zone\n4058 - 4053\nTargets\n"
                "4060\n4062\nOpen\nSL 4050"
            ),
            date=datetime.utcnow(),
            reply_to=None,
        )
        approaching = SimpleNamespace(
            id=701,
            text="Approaching",
            date=datetime.utcnow(),
            reply_to=SimpleNamespace(reply_to_msg_id=700),
        )
        still_valid = SimpleNamespace(
            id=702,
            text="Still valid if it comes down",
            date=datetime.utcnow(),
            reply_to=SimpleNamespace(reply_to_msg_id=701),
        )

        await _process_canal2_new(root)
        await _process_canal2_new(approaching)
        await _process_canal2_new(still_valid)

        assert listener._canal2_zone_plans[700] is (
            listener._canal2_zone_plans[701]
        )
        assert listener._canal2_zone_plans[701] is (
            listener._canal2_zone_plans[702]
        )
        assert listener._canal2_zone_plans[700]["status"] == "rearmed"
        aliases = [
            payload["alias_message_id"]
            for _, ev, payload in events
            if ev == "canal2_zone_plan_alias_registered"
        ]
        assert 701 in aliases
        assert 702 in aliases

    @pytest.mark.asyncio
    async def test_active_full_plan_merges_into_root_instead_of_new_plan(
            self, monkeypatch):
        events = []
        triggers = []

        async def fake_trigger(plan, trigger, **kwargs):
            triggers.append((plan, trigger, kwargs))
            return None

        monkeypatch.setattr(
            listener,
            "_trigger_canal2_zone_entry",
            fake_trigger,
            raising=False,
        )
        monkeypatch.setattr(
            listener.journal,
            "event",
            lambda sig, ev, **kw: events.append((sig, ev, kw)),
        )
        monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **kw: None)
        monkeypatch.setattr(
            listener,
            "_schedule_detached",
            lambda value: value.close(),
        )
        listener._seen_new_msg_ids.clear()
        listener._seen_new_msgs_order.clear()

        root = SimpleNamespace(
            id=710,
            text="Buy Zones Marked Out\n4058 - 4053",
            date=datetime.utcnow(),
            reply_to=None,
        )
        active = SimpleNamespace(
            id=711,
            text=(
                "Active\nGold Buy Zone\n4058 - 4053\nTargets\n"
                "4060\n4062\nOpen\nSL 4050"
            ),
            date=datetime.utcnow(),
            reply_to=SimpleNamespace(reply_to_msg_id=710),
        )

        await _process_canal2_new(root)
        await _process_canal2_new(active)

        plan = listener._canal2_zone_plans[710]
        assert listener._canal2_zone_plans[711] is plan
        assert plan["message_id"] == 710
        assert plan["tps"] == [4060.0, 4062.0]
        assert plan["sl"] == 4050.0
        assert plan["activation_requested"] is True
        assert plan["status"] == "armed"
        assert len(triggers) == 1
        assert not any(
            ev == "canal2_zone_plan_created"
            and payload.get("message_id") == 711
            for _, ev, payload in events
        )

    @pytest.mark.asyncio
    async def test_zone_plan_reply_to_chart_is_not_routed_to_open_trade(
            self, monkeypatch):
        events = []
        listener._canal2_zone_plans.clear()

        def fail_management_route(*args, **kwargs):
            raise AssertionError(
                "future areas replied to a chart are not trade management"
            )

        monkeypatch.setattr(
            listener,
            "_resolve_management_reply_target",
            fail_management_route,
        )
        monkeypatch.setattr(
            listener.journal,
            "event",
            lambda sig, ev, **kw: events.append((sig, ev, kw)),
        )
        monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **kw: None)
        monkeypatch.setattr(
            listener,
            "_schedule_detached",
            lambda value: value.close(),
        )
        listener._seen_new_msg_ids.clear()
        listener._seen_new_msgs_order.clear()

        msg = SimpleNamespace(
            id=599,
            text=(
                "4007\n4010\n4017\n\n"
                "These are all strong areas we can expect gold to sell from"
            ),
            date=datetime.utcnow(),
            reply_to=SimpleNamespace(reply_to_msg_id=598),
        )

        await _process_canal2_new(msg)

        assert listener._canal2_zone_plans[599]["zones"] == [
            [4007.0, 4007.0],
            [4010.0, 4010.0],
            [4017.0, 4017.0],
        ]
        assert listener._canal2_zone_plans[598] is (
            listener._canal2_zone_plans[599]
        )
        assert any(
            ev == "canal2_zone_plan_registered"
            and payload["thread_root_message_id"] == 598
            for _, ev, payload in events
        )

    @pytest.mark.asyncio
    async def test_zone_reply_recovers_plan_from_telegram_after_restart(
            self, monkeypatch):
        events = []
        listener._canal2_zone_plans.clear()

        def fail_management_route(*args, **kwargs):
            raise AssertionError(
                "a recoverable zone reply must not use the trade route"
            )

        async def get_reply_message():
            return SimpleNamespace(
                id=612,
                text=(
                    "Next Sell Zone at 4030\n\n"
                    "Bear in mind FOMC at 7\n\n"
                    "Look for a quick reaction"
                ),
                date=datetime.utcnow() - timedelta(minutes=5),
            )

        monkeypatch.setattr(
            listener,
            "_resolve_management_reply_target",
            fail_management_route,
        )
        monkeypatch.setattr(
            listener.journal,
            "event",
            lambda sig, ev, **kw: events.append((sig, ev, kw)),
        )
        monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **kw: None)
        listener._seen_new_msg_ids.clear()
        listener._seen_new_msgs_order.clear()

        msg = SimpleNamespace(
            id=613,
            text="Approaching",
            date=datetime.utcnow(),
            reply_to=SimpleNamespace(reply_to_msg_id=612),
            get_reply_message=get_reply_message,
        )

        await _process_canal2_new(msg)

        assert listener._canal2_zone_plans[612]["source_kind"] == (
            "reply_recovery"
        )
        assert any(
            ev == "canal2_zone_plan_management"
            and payload["zone_plan_signal_id"] == "canal2_612"
            for _, ev, payload in events
        )

    @pytest.mark.asyncio
    async def test_edited_zone_reply_recovers_plan_after_restart(
            self, monkeypatch):
        events = []
        listener._canal2_zone_plans.clear()
        listener._seen_edits.clear()
        listener._seen_edits_order.clear()

        async def fail_management_edit(*args, **kwargs):
            raise AssertionError(
                "a recoverable zone edit must not use the trade route"
            )

        async def get_reply_message():
            return SimpleNamespace(
                id=612,
                text="Next Sell Zone at 4030\nLook for a quick reaction",
                date=datetime.utcnow() - timedelta(minutes=5),
            )

        monkeypatch.setattr(
            listener,
            "_process_management_reply_edit",
            fail_management_edit,
        )
        monkeypatch.setattr(
            listener.journal,
            "event",
            lambda sig, ev, **kw: events.append((sig, ev, kw)),
        )
        monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **kw: None)

        msg = SimpleNamespace(
            id=615,
            text="Take profit from layers",
            date=datetime.utcnow(),
            edit_date=datetime.utcnow(),
            reply_to=SimpleNamespace(reply_to_msg_id=612),
            get_reply_message=get_reply_message,
        )

        await _process_canal2_edit(msg)

        assert listener._canal2_zone_plans[612]["source_kind"] == (
            "reply_recovery"
        )
        assert any(
            ev == "canal2_zone_plan_management"
            and payload["actions"] == ["CLOSE_PARTIAL"]
            for _, ev, payload in events
        )

    @pytest.mark.asyncio
    async def test_standalone_management_applies_to_single_open_signal(
            self, monkeypatch):
        st = StateManager()
        signal = Signal(
            channel="canal2",
            message_id=700,
            direction="BUY",
            market_ticket=1700000001,
        )
        st.add(signal)
        executed = []
        events = []

        async def fake_classify(text, signal=None):
            return [{
                "action": "MOVE_SL_TO_BE",
                "price": None,
                "confidence": 0.95,
            }]

        async def fake_execute(target, actions, raw_text="", tg_ts=None):
            executed.append((target, actions, raw_text))

        monkeypatch.setattr(listener, "state", st)
        monkeypatch.setattr(listener, "classify_async", fake_classify)
        monkeypatch.setattr(listener, "_execute_action", fake_execute)
        monkeypatch.setattr(
            listener.journal,
            "event",
            lambda sig, ev, **kw: events.append((sig, ev, kw)),
        )
        listener._seen_new_msg_ids.clear()
        listener._seen_new_msgs_order.clear()

        msg = SimpleNamespace(
            id=701,
            text="Move SL to BE",
            date=datetime.utcnow(),
            reply_to=None,
        )

        await _process_canal2_new(msg)

        assert executed == [(
            signal,
            [{
                "action": "MOVE_SL_TO_BE",
                "price": None,
                "confidence": 0.95,
            }],
            "Move SL to BE",
        )]
        assert any(
            ev == "standalone_mgmt_applied"
            and payload["channel"] == "canal2"
            for _, ev, payload in events
        )

    @pytest.mark.asyncio
    async def test_unknown_photo_reply_never_falls_back_to_open_trade(
            self, monkeypatch):
        st = StateManager()
        signal = Signal(
            channel="canal2",
            message_id=720,
            direction="BUY",
            market_ticket=1700000020,
        )
        st.add(signal)
        events = []

        async def get_reply_message():
            return SimpleNamespace(
                id=710,
                text="",
                message="",
                photo=object(),
                date=datetime.utcnow() - timedelta(minutes=5),
            )

        async def fail_execute(*args, **kwargs):
            raise AssertionError(
                "unknown photo reply must not manage an unrelated trade"
            )

        monkeypatch.setattr(listener, "state", st)
        monkeypatch.setattr(listener, "_execute_action", fail_execute)
        monkeypatch.setattr(
            listener.journal,
            "event",
            lambda sig, ev, **kw: events.append((sig, ev, kw)),
        )
        monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **kw: None)
        listener._seen_new_msg_ids.clear()
        listener._seen_new_msgs_order.clear()
        listener._canal2_zone_plans.clear()

        msg = SimpleNamespace(
            id=721,
            text="Move SL to BE",
            date=datetime.utcnow(),
            reply_to=SimpleNamespace(reply_to_msg_id=710),
            get_reply_message=get_reply_message,
        )

        await _process_canal2_new(msg)

        unresolved = [
            payload for _, ev, payload in events
            if ev == "management_reply_unresolved"
        ]
        assert unresolved
        assert unresolved[0]["reason"] == "reply_root_not_entry"

    @pytest.mark.asyncio
    async def test_historical_entry_reply_does_not_guess_unique_open_trade(
            self, monkeypatch):
        st = StateManager()
        signal = Signal(
            channel="canal2",
            message_id=730,
            direction="SELL",
            market_ticket=1700000030,
        )
        st.add(signal)
        executed = []

        async def get_reply_message():
            return SimpleNamespace(
                id=729,
                text="Sell Gold Now",
                message="Sell Gold Now",
                date=datetime.utcnow() - timedelta(minutes=5),
            )

        async def fake_execute(target, actions, raw_text="", tg_ts=None):
            executed.append((target, actions, raw_text))

        monkeypatch.setattr(listener, "state", st)
        monkeypatch.setattr(listener, "_execute_action", fake_execute)
        events = []
        monkeypatch.setattr(
            listener.journal,
            "event",
            lambda sig, ev, **kw: events.append((sig, ev, kw)),
        )
        monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **kw: None)
        listener._seen_new_msg_ids.clear()
        listener._seen_new_msgs_order.clear()
        listener._canal2_zone_plans.clear()

        msg = SimpleNamespace(
            id=731,
            text="Move SL to BE",
            date=datetime.utcnow(),
            reply_to=SimpleNamespace(reply_to_msg_id=729),
            get_reply_message=get_reply_message,
        )

        await _process_canal2_new(msg)

        assert executed == []
        assert any(
            ev == "management_reply_unresolved"
            and payload["reason"] == "reply_root_identity_unproven"
            for _, ev, payload in events
        )

    @pytest.mark.asyncio
    async def test_known_reply_target_does_not_refetch_telegram_root(
            self, monkeypatch):
        st = StateManager()
        signal = Signal(
            channel="canal2",
            message_id=735,
            direction="BUY",
            market_ticket=1700000035,
        )
        st.add(signal)
        fetched = []
        executed = []

        async def get_reply_message():
            fetched.append(True)
            return None

        async def fake_execute(target, actions, raw_text="", tg_ts=None):
            executed.append(target)

        monkeypatch.setattr(listener, "state", st)
        monkeypatch.setattr(listener, "_execute_action", fake_execute)
        monkeypatch.setattr(listener.journal, "event", lambda *a, **kw: None)
        monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **kw: None)
        listener._seen_new_msg_ids.clear()
        listener._seen_new_msgs_order.clear()
        listener._canal2_zone_plans.clear()

        msg = SimpleNamespace(
            id=736,
            text="Move SL to BE",
            date=datetime.utcnow(),
            reply_to=SimpleNamespace(reply_to_msg_id=735),
            get_reply_message=get_reply_message,
        )

        await _process_canal2_new(msg)

        assert executed == [signal]
        assert fetched == []

    @pytest.mark.parametrize(
        "text",
        [
            "I put more sell on 4055.00",
            "I'm out of this trade",
        ],
    )
    def test_standalone_action_guard_covers_provider_phrases(self, text):
        assert listener._canal2_context_candidate(text) is True

    @pytest.mark.asyncio
    async def test_edit_of_live_reply_entry_updates_its_own_levels(
            self, monkeypatch):
        st = StateManager()
        signal = Signal(
            channel="canal2",
            message_id=585,
            direction="SELL",
            market_ticket=1700000585,
            market_fill_price=4002.8,
        )
        st.add(signal)
        applied = []

        async def fail_management(*args, **kwargs):
            raise AssertionError(
                "edit belongs to the live re-entry, not the older reply root"
            )

        async def fake_apply(target, parsed, channel, **kwargs):
            applied.append((target, parsed, channel))
            return parsed

        monkeypatch.setattr(listener, "state", st)
        monkeypatch.setattr(
            listener, "_process_management_reply_edit", fail_management
        )
        monkeypatch.setattr(
            listener, "_apply_interpreted_entry_levels", fake_apply
        )
        monkeypatch.setattr(
            listener, "_log_telegram_understood", lambda *a, **kw: None
        )
        listener._seen_edits.clear()
        listener._seen_edits_order.clear()

        msg = SimpleNamespace(
            id=585,
            text=(
                "Sell Gold\n4000-4005\n"
                "TP1 3998\nTP2 3996\nSL 4010"
            ),
            message="",
            date=datetime.utcnow() - timedelta(minutes=1),
            edit_date=datetime.utcnow(),
            reply_to=SimpleNamespace(reply_to_msg_id=580),
        )

        await _process_canal2_edit(msg)

        assert applied
        assert applied[0][0] is signal
        assert applied[0][1]["range"] == (4000.0, 4005.0)

    @pytest.mark.asyncio
    async def test_zone_message_edited_into_entry_drops_stale_zone_cache(
            self, monkeypatch):
        plan = {
            "message_id": 740,
            "direction": "BUY",
            "zones": [[4040.0, 4042.0]],
        }
        listener._canal2_zone_plans.clear()
        listener._canal2_zone_plans[740] = plan
        listener._canal2_zone_plans[739] = plan
        listener._seen_edits.clear()
        listener._seen_edits_order.clear()
        routed = []

        async def fake_new(msg, label="Canal2", dedup=True, **kwargs):
            routed.append((msg.id, label, dedup))

        monkeypatch.setattr(listener, "_process_canal2_new", fake_new)
        monkeypatch.setattr(listener.journal, "event", lambda *a, **kw: None)
        monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **kw: None)

        now = datetime.utcnow()
        msg = SimpleNamespace(
            id=740,
            text="Buy Gold Now",
            message="Buy Gold Now",
            date=now,
            edit_date=now,
            reply_to=None,
        )

        await _process_canal2_edit(msg)

        assert routed == [(740, "Canal2_recover", False)]
        assert 740 not in listener._canal2_zone_plans
        assert 739 not in listener._canal2_zone_plans


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

        async def fake_update(sig, parsed, tg_ts=None, **kwargs):
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
    async def test_fresh_reply_reentry_edit_recovers_before_management(
            self, monkeypatch):
        st = StateManager()
        older = Signal(
            channel="canal2",
            message_id=580,
            direction="SELL",
            market_ticket=1700000580,
        )
        st.add(older)
        recovered = []

        async def fail_management(*args, **kwargs):
            raise AssertionError(
                "an immediate reply edit is a re-entry, not management"
            )

        async def fake_process_new(msg, label="Canal2", dedup=True):
            recovered.append((msg.id, label, dedup))

        monkeypatch.setattr(listener, "state", st)
        monkeypatch.setattr(
            listener,
            "_process_management_reply_edit",
            fail_management,
        )
        monkeypatch.setattr(listener, "_process_canal2_new", fake_process_new)
        monkeypatch.setattr(listener.journal, "event", lambda *a, **kw: None)
        monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **kw: None)
        monkeypatch.setattr(
            config,
            "STRATEGY_C2_ORPHAN_EDIT_MAX_AGE_S",
            180.0,
            raising=False,
        )
        listener._seen_edits.clear()
        listener._seen_edits_order.clear()

        msg = SimpleNamespace(
            id=585,
            text="Sell Gold Now\n4000-4005\nTP1 3998\nSL 4010",
            date=datetime.utcnow() - timedelta(seconds=2),
            edit_date=datetime.utcnow(),
            reply_to=SimpleNamespace(reply_to_msg_id=580),
        )

        await _process_canal2_edit(msg)

        assert recovered == [(585, "Canal2_recover", False)]

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
    async def test_reply_reentry_edit_without_now_is_deferred_during_open(
            self, monkeypatch):
        st = StateManager()
        older = Signal(
            channel="canal2",
            message_id=580,
            direction="SELL",
            market_ticket=1700000580,
        )
        st.add(older)
        events = []

        async def fail_management(*args, **kwargs):
            raise AssertionError(
                "the edit belongs to the opening re-entry message"
            )

        monkeypatch.setattr(listener, "state", st)
        monkeypatch.setattr(
            listener, "_process_management_reply_edit", fail_management
        )
        monkeypatch.setattr(
            listener.journal,
            "event",
            lambda sig, ev, **kw: events.append((sig, ev, kw)),
        )
        listener._seen_edits.clear()
        listener._seen_edits_order.clear()
        listener._deferred_canal2_entry_edits.clear()
        _canal2_open_started(585)

        msg = SimpleNamespace(
            id=585,
            text=(
                "Sell Gold\n4000-4005\n"
                "TP1 3998\nTP2 3996\nSL 4010"
            ),
            message="",
            date=datetime.utcnow() - timedelta(seconds=2),
            edit_date=datetime.utcnow(),
            reply_to=SimpleNamespace(reply_to_msg_id=580),
        )

        try:
            await _process_canal2_edit(msg)
        finally:
            _canal2_open_finished(585)

        deferred = _pop_deferred_canal2_entry_edit(585)
        assert deferred is not None
        assert "TP1 3998" in deferred["text"]
        assert any(
            ev == "canal2_orphan_entry_edit_deferred"
            for _, ev, _ in events
        )

    @pytest.mark.asyncio
    async def test_successful_new_signal_applies_deferred_entry_edit(
            self, monkeypatch):
        st = StateManager()
        parsed_updates = []
        events = []

        async def fake_run(fn, *args):
            return fn(*args)

        async def fake_update(sig, parsed, tg_ts=None, **kwargs):
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

        async def fake_update(sig, parsed, tg_ts=None, **kwargs):
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

        async def fake_update(sig, parsed, tg_ts=None, **kwargs):
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

        async def fake_update(sig, parsed, tg_ts=None, **kwargs):
            parsed_updates.append((parsed, tg_ts, kwargs))

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
                   for parsed, _, _ in parsed_updates)
        assert any(
            parsed.get("sl") == 4038.5
            and parsed.get("tps", [None])[0] == 4027.5
            and kwargs.get("provider_values", {}).get("tps") == [4027.5]
            for parsed, _, kwargs in parsed_updates
        )
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

        async def fake_update(sig, parsed, tg_ts=None, **kwargs):
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

        async def fake_update(sig, parsed, tg_ts=None, **kwargs):
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
