"""Regression tests for Gold Signals partial-close management.

When the provider asks to close its first/best layered entries, the bot only
closes part of our basket if our own entries are genuinely in profit. A flat
or losing copied basket is not equivalent to the provider's layers, so the
instruction is recorded and deferred without changing the original trade.

The older BE-rescue helpers remain covered below because the generic BE rescue
still shares their price-safety helper.
"""
import json

import pytest

import causal_trace
import journal
import listener
from listener import (_close_first_decision, _safe_tp_be,
                      _close_all_be_rescue,
                      _close_first_be_timeout, _close_first_be_rescue)
from state import Signal


class TestCloseFirstDecision:
    """Decide la rama de CLOSE_FIRST para canal2:
       'close_half'           → cerrar una parte cuando hay profit real.
       'defer_layer_mismatch' → conservar la cesta si las capas no coinciden.
    """

    def test_profit_significativo_close_half(self):
        # +1.5 pts en profit → la lógica clásica gana (asegurar parciales)
        assert _close_first_decision(price_vs_entry_pts=1.5) == "close_half"

    def test_profit_justo_en_threshold_close_half(self):
        # +0.5 exacto no cubre con holgura spread/deslizamiento.
        assert _close_first_decision(
            price_vs_entry_pts=0.5,
        ) == "defer_layer_mismatch"

    def test_profit_pequeno_set_tp_be(self):
        assert _close_first_decision(
            price_vs_entry_pts=0.3,
        ) == "defer_layer_mismatch"

    def test_be_exacto_set_tp_be(self):
        assert _close_first_decision(
            price_vs_entry_pts=0.0,
        ) == "defer_layer_mismatch"

    def test_loss_set_tp_be(self):
        assert _close_first_decision(
            price_vs_entry_pts=-0.41,
        ) == "defer_layer_mismatch"

    def test_loss_grande_set_tp_be(self):
        assert _close_first_decision(
            price_vs_entry_pts=-3.06,
        ) == "defer_layer_mismatch"

    def test_provider_best_entry_mismatch_is_deferred_without_forced_exit(self):
        assert _close_first_decision(
            price_vs_entry_pts=-1.1,
        ) == "defer_layer_mismatch"


@pytest.mark.asyncio
async def test_close_first_layer_mismatch_preserves_original_trade(
        monkeypatch):
    signal = Signal(
        channel="canal2",
        message_id=1313,
        direction="BUY",
        market_ticket=810,
        extra_market_tickets=[811, 812, 813, 814],
        market_fill_price=4300.0,
        tps=[4303.0, 4305.0, 4307.0, 4309.0],
        sl=4291.0,
    )
    tickets = signal.all_filled_tickets
    closes = []
    tp_changes = []
    rescue_calls = []
    events = []

    monkeypatch.setattr(
        listener.executor,
        "position_pnls",
        lambda requested: [(ticket, -1.0) for ticket in requested],
    )
    monkeypatch.setattr(
        listener.executor,
        "current_tick_safe",
        lambda: {"bid": 4298.9, "ask": 4299.1},
    )
    monkeypatch.setattr(
        listener.executor,
        "entry_price",
        lambda ticket: 4300.0,
    )
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_close_position",
        lambda *args, **kwargs: closes.append((args, kwargs)),
    )
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_modify_tp",
        lambda *args, **kwargs: tp_changes.append((args, kwargs)),
    )

    async def _capture_rescue(*args, **kwargs):
        rescue_calls.append((args, kwargs))

    monkeypatch.setattr(listener, "_close_first_be_rescue", _capture_rescue)
    monkeypatch.setattr(listener.logger, "log_action", lambda *a, **k: None)
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig_id, ev, **kwargs: events.append({
            "sig_id": sig_id,
            "ev": ev,
            **kwargs,
        }),
    )
    monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **k: None)
    monkeypatch.setattr(listener, "_schedule_detached", lambda coro: coro.close())

    await listener._execute_one_action(
        signal,
        {"action": "CLOSE_FIRST", "confidence": 0.99},
        raw_text="Close first entries and make risk free",
    )

    assert signal.all_filled_tickets == tickets
    assert closes == []
    assert tp_changes == []
    assert rescue_calls == []
    deferred = next(
        event for event in events
        if event["ev"] == "close_first_layer_mismatch_deferred"
    )
    assert deferred["price_vs_entry"] == pytest.approx(-1.1)

    def test_threshold_configurable(self):
        # Permitir override del threshold (default 0.5).
        assert _close_first_decision(price_vs_entry_pts=0.7,
                                      profit_threshold_pts=1.0) == (
                                          "defer_layer_mismatch")
        assert _close_first_decision(price_vs_entry_pts=1.5,
                                      profit_threshold_pts=1.0) == "close_half"


class TestSafeTpBe:
    """TP=BE respetando stops_level del broker.

    MT5 rechaza con INVALID_STOPS (10016) si pones TP demasiado cerca del
    precio actual. Necesitamos garantizar que el TP esté FUERA del
    stops_level por el lado correcto. Si entry+padding es legal: usar.
    Si no: usar current_price + stops_level + padding.
    """

    def test_buy_precio_bajo_entry_tp_en_entry_padding(self):
        # BUY @ 4488.85, precio actual 4488.38 (en loss), stops_level=1
        # entry+0.05 = 4488.90 → 0.52 pts por encima del precio ✓
        # min_legal = 4488.38 + 1 + 0.05 = 4489.43 → entry+pad NO cumple → 4489.43
        tp = _safe_tp_be("BUY", entry=4488.85, current_price=4488.38,
                          stops_level_pts=1.0, padding_pts=0.05)
        assert tp == pytest.approx(4489.43, abs=0.01)

    def test_buy_precio_bajo_entry_grande_stops_level_chico(self):
        # BUY @ 4488.85, precio 4488.0 (-0.85), stops_level=0.3
        # entry+0.05 = 4488.90 → 0.90 pts por encima ✓
        # min_legal = 4488.0 + 0.3 + 0.05 = 4488.35
        # entry+pad (4488.90) >= min_legal (4488.35) → usar entry+pad
        tp = _safe_tp_be("BUY", entry=4488.85, current_price=4488.0,
                          stops_level_pts=0.3, padding_pts=0.05)
        assert tp == pytest.approx(4488.90, abs=0.01)

    def test_buy_precio_sobre_entry_tp_min_legal(self):
        # BUY @ 4527.86, precio 4529.01 (en profit pequeno), stops_level=1
        # entry+0.05 = 4527.91 (POR DEBAJO del precio actual!) → MT5 rechazaria
        # min_legal = 4529.01 + 1 + 0.05 = 4530.06 → usar este
        # Pero ojo: si TP > precio, MT5 lo TRATA como TP normal (cierra cuando bid alcanza)
        # En modo BE rescate queremos cerrar RAPIDO → max(entry+pad, min_legal)
        tp = _safe_tp_be("BUY", entry=4527.86, current_price=4529.01,
                          stops_level_pts=1.0, padding_pts=0.05)
        assert tp == pytest.approx(4530.06, abs=0.01)

    def test_sell_precio_sobre_entry_tp_en_entry_padding(self):
        # SELL @ 4500.0, precio 4500.5 (en loss para SELL), stops_level=1
        # entry-0.05 = 4499.95
        # max_legal = 4500.5 - 1 - 0.05 = 4499.45
        # entry-pad (4499.95) > max_legal (4499.45) → entry-pad NO cumple (debe ser <=)
        # → usar max_legal = 4499.45
        tp = _safe_tp_be("SELL", entry=4500.0, current_price=4500.5,
                          stops_level_pts=1.0, padding_pts=0.05)
        assert tp == pytest.approx(4499.45, abs=0.01)

    def test_sell_precio_bajo_entry_tp_min_de_los_dos(self):
        # SELL en profit (precio < entry): TP por debajo del precio actual
        # entry-0.05 = 4499.95
        # max_legal = 4499.0 - 1 - 0.05 = 4497.95
        # min(entry-pad=4499.95, max_legal=4497.95) = 4497.95
        tp = _safe_tp_be("SELL", entry=4500.0, current_price=4499.0,
                          stops_level_pts=1.0, padding_pts=0.05)
        assert tp == pytest.approx(4497.95, abs=0.01)

    def test_default_padding_005(self):
        # Sin padding explícito, usa 0.05
        tp = _safe_tp_be("BUY", entry=4500.0, current_price=4499.0,
                          stops_level_pts=0.5)
        # entry+0.05 = 4500.05; min_legal = 4499.0 + 0.5 + 0.05 = 4499.55
        # max(4500.05, 4499.55) = 4500.05
        assert tp == pytest.approx(4500.05, abs=0.01)

    def test_stops_level_cero(self):
        # Algunos brokers no exigen distancia mínima
        tp = _safe_tp_be("BUY", entry=4500.0, current_price=4499.5,
                          stops_level_pts=0.0)
        # entry+0.05 = 4500.05; min_legal = 4499.5 + 0 + 0.05 = 4499.55
        # max → 4500.05
        assert tp == pytest.approx(4500.05, abs=0.01)


# ───────────────── Time-stop _close_first_be_timeout ──────────────────

@pytest.fixture
def isolated_journal(tmp_path, monkeypatch):
    """Redirige el journal a tmp para no contaminar data/ real."""
    monkeypatch.setattr(journal, "EVENTS_FILE", tmp_path / "events.jsonl")
    monkeypatch.setattr(journal, "JOURNAL_FILE", tmp_path / "journal.csv")
    return tmp_path / "events.jsonl"


@pytest.fixture
def captured_closes(monkeypatch):
    """Captura las llamadas a pending_actions.enqueue_close_position."""
    calls = []

    def _fake_enqueue_close(signal, ticket, label=""):
        calls.append({"ticket": ticket, "label": label})

    monkeypatch.setattr(listener.pending_actions, "enqueue_close_position",
                        _fake_enqueue_close)
    return calls


class TestCloseFirstBeTimeout:
    """El time-stop de la rama RESCATE BE. Tras N segundos cierra a mercado
    las posiciones que el precio no llevó a BE.

    Usamos timeout_s=0 → asyncio.sleep(0) cede el loop pero no espera.
    """

    def _signal(self, status="open", be_armed=True):
        sig = Signal(channel="canal2", message_id=99001, direction="BUY",
                     market_ticket=500,
                     extra_market_tickets=[501, 502, 503, 504])
        sig.status = status
        sig.close_first_be_armed = be_armed
        return sig

    async def test_timeout_cierra_las_que_quedan_abiertas(
            self, isolated_journal, captured_closes, monkeypatch):
        sig = self._signal()
        # 3 de 5 siguen abiertas (el precio no rebotó a BE)
        monkeypatch.setattr(listener.executor, "position_pnls",
                            lambda tickets: [(502, -5.0), (503, -5.0),
                                             (504, -5.1)])
        await _close_first_be_timeout(sig, 0)
        # Las 3 abiertas se cierran a mercado
        assert sorted(c["ticket"] for c in captured_closes) == [502, 503, 504]
        # Se registran en close_first_tickets (para tag correcto)
        assert set(sig.close_first_tickets) == {502, 503, 504}
        # La rama se desarma
        assert sig.close_first_be_armed is False
        assert sig.close_first_be_deadline is None

    async def test_timeout_no_cierra_si_todo_resuelto_por_be(
            self, isolated_journal, captured_closes, monkeypatch):
        sig = self._signal()
        # El precio rebotó: todas cerraron por TP-BE antes del timeout
        monkeypatch.setattr(listener.executor, "position_pnls",
                            lambda tickets: [])
        await _close_first_be_timeout(sig, 0)
        assert captured_closes == []
        assert sig.close_first_be_armed is False

    async def test_timeout_no_actua_si_desarmado(
            self, isolated_journal, captured_closes, monkeypatch):
        # close_first_be_armed=False → el canal mandó CLOSE_ALL mientras
        # dormíamos. No debe tocar nada.
        sig = self._signal(be_armed=False)
        monkeypatch.setattr(listener.executor, "position_pnls",
                            lambda tickets: [(502, -5.0)])
        await _close_first_be_timeout(sig, 0)
        assert captured_closes == []

    async def test_timeout_no_actua_si_senal_cerrada(
            self, isolated_journal, captured_closes, monkeypatch):
        # status != open → la señal ya cerró. Desarmar y salir.
        sig = self._signal(status="closed")
        monkeypatch.setattr(listener.executor, "position_pnls",
                            lambda tickets: [(502, -5.0)])
        await _close_first_be_timeout(sig, 0)
        assert captured_closes == []
        assert sig.close_first_be_armed is False

    async def test_timeout_nunca_lanza_excepcion(
            self, isolated_journal, captured_closes, monkeypatch):
        # Defensivo: si position_pnls crashea, el time-stop no debe
        # propagar la excepción (es fire-and-forget).
        sig = self._signal()

        def _boom(tickets):
            raise RuntimeError("MT5 down")

        monkeypatch.setattr(listener.executor, "position_pnls", _boom)
        # No debe lanzar
        await _close_first_be_timeout(sig, 0)

    async def test_timeout_keeps_management_message_as_causal_parent(
            self, isolated_journal, monkeypatch):
        sig = self._signal()
        sig.source_message_revision_id = "msgrev_entry"
        sig.source_decision_id = "decision_entry"
        monkeypatch.setattr(
            listener.executor,
            "position_pnls",
            lambda tickets: [(502, -5.0)],
        )
        captured = []

        def _capture_enqueue(signal, ticket, label=""):
            captured.append({
                **causal_trace.current_fields(),
                "action_id": causal_trace.new_action_id(),
                "ticket": ticket,
            })

        monkeypatch.setattr(
            listener.pending_actions,
            "enqueue_close_position",
            _capture_enqueue,
        )

        await _close_first_be_timeout(
            sig,
            0,
            source_message_revision_id="msgrev_management",
            parent_decision_id="decision_management",
        )
        assert journal.flush_events(timeout=2.0)

        assert captured[0]["message_revision_id"] == "msgrev_management"
        assert captured[0]["decision_id"].startswith("decision_")
        rows = [
            json.loads(line)
            for line in isolated_journal.read_text(
                encoding="utf-8"
            ).splitlines()
        ]
        decision = next(
            row for row in rows
            if row["ev"] == "bot_internal_decision"
        )
        started = next(
            row for row in rows
            if row["ev"] == "bot_internal_decision_started"
        )
        assert started["decision_id"] == decision["decision_id"]
        assert rows.index(started) < rows.index(decision)
        assert decision["message_revision_id"] == "msgrev_management"
        assert decision["parent_decision_id"] == "decision_management"
        assert decision["declared_action_ids"] == [
            captured[0]["action_id"]
        ]


class TestCloseFirstBeRescue:
    """La función que ARMA el rescate: pone TP=BE en todas las posiciones,
    marca la señal y lanza el time-stop."""

    def _signal(self):
        sig = Signal(channel="canal2", message_id=99002, direction="BUY",
                     market_ticket=600,
                     extra_market_tickets=[601, 602, 603, 604])
        sig.status = "open"
        return sig

    def _pos_info(self):
        return [
            {"ticket": 600, "pnl": -1.0, "entry": 4488.79, "tp": 4491,
             "recorrido": 2.6},
            {"ticket": 601, "pnl": -1.0, "entry": 4488.91, "tp": 4493,
             "recorrido": 4.6},
            {"ticket": 602, "pnl": -1.0, "entry": 4488.91, "tp": 4495,
             "recorrido": 6.6},
            {"ticket": 603, "pnl": -1.0, "entry": 4488.90, "tp": 4497,
             "recorrido": 8.6},
            {"ticket": 604, "pnl": -1.0, "entry": 4489.02, "tp": 4499,
             "recorrido": 10.6},
        ]

    @pytest.fixture
    def captured_modifies(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            listener.pending_actions, "enqueue_modify_tp",
            lambda signal, ticket, new_tp, label="": calls.append(
                {"ticket": ticket, "new_tp": new_tp}))
        return calls

    @pytest.fixture
    def no_side_effects(self, monkeypatch):
        """Neutraliza task spawn + notify para testear _close_first_be_rescue
        sin lanzar el time-stop real ni mandar Telegram."""
        def _fake_ensure_future(coro):
            # cerrar el coroutine evita 'coroutine never awaited'
            try:
                coro.close()
            except Exception:
                pass
            return None
        monkeypatch.setattr(listener.asyncio, "ensure_future",
                            _fake_ensure_future)

        async def _fake_notify(*a, **k):
            return None
        monkeypatch.setattr(listener, "notify", _fake_notify)
        # stops_level del broker → 1.0 fijo para el test
        monkeypatch.setattr(listener.mt5_errors if hasattr(listener, "mt5_errors")
                            else __import__("mt5_errors"),
                            "get_stops_level_pts", lambda: 1.0)

    async def test_rescue_pone_tp_be_en_todas(
            self, isolated_journal, captured_modifies, no_side_effects):
        sig = self._signal()
        await _close_first_be_rescue(sig, self._pos_info(),
                                     cur_price=4488.38, entry_avg=4488.91)
        # TP=BE encolado para las 5 posiciones
        assert sorted(c["ticket"] for c in captured_modifies) == [
            600, 601, 602, 603, 604]
        # Todos al mismo TP=BE (un único nivel común)
        tps = {c["new_tp"] for c in captured_modifies}
        assert len(tps) == 1
        # TP=BE seguro: max(entry+0.05, cur+stops+0.05)
        # = max(4488.96, 4488.38+1+0.05=4489.43) = 4489.43
        assert abs(captured_modifies[0]["new_tp"] - 4489.43) < 0.01

    async def test_rescue_arma_la_senal(
            self, isolated_journal, captured_modifies, no_side_effects):
        sig = self._signal()
        await _close_first_be_rescue(sig, self._pos_info(),
                                     cur_price=4488.38, entry_avg=4488.91)
        assert sig.close_first_be_armed is True
        assert sig.close_first_be_deadline is not None

    async def test_rescue_passes_management_parent_to_timeout(
            self, isolated_journal, captured_modifies, no_side_effects,
            monkeypatch):
        sig = self._signal()
        captured = {}

        def _capture_timeout(signal, timeout_s, **kwargs):
            captured.update(kwargs)

            async def _noop():
                return None

            return _noop()

        monkeypatch.setattr(
            listener,
            "_close_first_be_timeout",
            _capture_timeout,
        )
        with causal_trace.bind_message_revision(
            "msgrev_management",
            decision_id="decision_management",
        ):
            await _close_first_be_rescue(
                sig,
                self._pos_info(),
                cur_price=4488.38,
                entry_avg=4488.91,
            )

        assert captured == {
            "source_message_revision_id": "msgrev_management",
            "parent_decision_id": "decision_management",
        }

    async def test_rescue_idempotente(
            self, isolated_journal, captured_modifies, no_side_effects):
        # Segunda llamada con la rama ya armada → no re-encola nada
        sig = self._signal()
        await _close_first_be_rescue(sig, self._pos_info(),
                                     cur_price=4488.38, entry_avg=4488.91)
        captured_modifies.clear()
        await _close_first_be_rescue(sig, self._pos_info(),
                                     cur_price=4488.38, entry_avg=4488.91)
        assert captured_modifies == []


class TestCloseAllBeRescue:
    """Guard para CLOSE_ALL semantico de BE/risk-free en loss real."""

    def _signal(self):
        sig = Signal(channel="canal2", message_id=12847, direction="BUY",
                     market_ticket=700,
                     extra_market_tickets=[701, 702, 703, 704])
        sig.status = "open"
        return sig

    def _pos_info(self):
        return [
            {"ticket": 700, "pnl": -1.2, "entry": 4566.20, "tp": 4569,
             "recorrido": 3.0},
            {"ticket": 701, "pnl": -1.2, "entry": 4566.24, "tp": 4571,
             "recorrido": 5.0},
        ]

    @pytest.fixture
    def captured_modifies(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            listener.pending_actions, "enqueue_modify_tp",
            lambda signal, ticket, new_tp, label="": calls.append(
                {"ticket": ticket, "new_tp": new_tp, "label": label}))
        return calls

    @pytest.fixture
    def no_side_effects(self, monkeypatch):
        def _fake_ensure_future(coro):
            try:
                coro.close()
            except Exception:
                pass
            return None
        monkeypatch.setattr(listener.asyncio, "ensure_future",
                            _fake_ensure_future)

        async def _fake_notify(*a, **k):
            return None
        monkeypatch.setattr(listener, "notify", _fake_notify)
        monkeypatch.setattr(listener.mt5_errors if hasattr(listener, "mt5_errors")
                            else __import__("mt5_errors"),
                            "get_stops_level_pts", lambda: 1.0)

    async def test_close_all_be_rescue_sets_tp_be_without_market_close(
            self, isolated_journal, captured_modifies, no_side_effects):
        sig = self._signal()

        await _close_all_be_rescue(
            sig, self._pos_info(), cur_price=4565.40,
            entry_avg=4566.22, raw_text="close breakeven or risk free")

        assert sorted(c["ticket"] for c in captured_modifies) == [700, 701]
        assert all("CLOSE_ALL_BE_RESCUE" in c["label"]
                   for c in captured_modifies)
        assert sig.be_rescue_armed is True
        assert sig.be_rescue_deadline is not None
