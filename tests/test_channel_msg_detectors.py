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
    _pop_deferred_canal2_entry_edit,
    _diff_canal1_edit,
    _process_canal1_edit,
    _process_canal2_edit,
    _process_canal2_new,
    _strict_vs_loose_canal1_filter,
)
from state import Signal, StateManager


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
            text="XAU USD SELL NOW",
            date=datetime.utcnow(),
            reply_to=None,
        )

        await _process_canal2_new(msg)

        assert marker_seen_during_context == [True]
        assert not _canal2_open_in_progress(12914)

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


class TestCanal1DuplicateSticker:
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
