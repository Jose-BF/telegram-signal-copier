"""
test_executor_anomalies.py — Helpers PUROS de los detectores Batch D (executor).

Batch D instrumenta executor.py para emitir anomaly en superficies que antes
eran print-only:
  - order_send → None (IPC failure)
  - phantom fill (DONE pero positions_get vacio)
  - safe_sl con adjusted=None (trade se abriria SIN STOP LOSS)
  - magic mismatch (near-miss safety)
  - position_pnls: None (MT5 down) vs [] (sin posiciones)
  - slippage alta entre tick read y fill price

La mayor parte de los fix son emisiones puntuales de anomaly. Aqui
cubrimos los pocos helpers PUROS con logica de decision:
  - _slippage_severity(slip_pts, deviation_pts): None | "warning"
  - _classify_position_pnls_query(raw): "ok" | "empty" | "mt5_down"
"""
import pytest
from types import SimpleNamespace

from executor import (
    _slippage_severity,
    _classify_position_pnls_query,
    _sig_id_from_order_comment,
)
import executor


class TestSlippageSeverity:
    """Decide si el slippage entre el price snapshot y el res.price del fill
    merece anomaly. Umbral: > deviation_pts / 2 = warning (la mitad del
    deviation permitido es ya un fill muy degradado).
    """

    def test_zero_slip(self):
        assert _slippage_severity(slip_pts=0.0, deviation_pts=30) is None

    def test_slip_pequeno_no_anomaly(self):
        # 5pts con deviation=30 → ratio 0.16 → OK
        assert _slippage_severity(slip_pts=5.0, deviation_pts=30) is None

    def test_slip_mitad_deviation_warning(self):
        # >50% del deviation permitido = warning
        assert _slippage_severity(slip_pts=16.0, deviation_pts=30) == "warning"

    def test_slip_negativo_se_trata_como_absoluto(self):
        # res.price < tick.price es slippage favorable, pero el detector
        # debe medir MAGNITUD (anomalia es por ruido del mercado, no por
        # direccion del slip).
        assert _slippage_severity(slip_pts=-16.0, deviation_pts=30) == "warning"

    def test_slip_deviation_0_no_anomaly(self):
        # Caso degenerado: deviation=0 → cualquier slip seria warning, pero
        # mejor no alertar en ese caso (probablemente bug del config).
        assert _slippage_severity(slip_pts=5.0, deviation_pts=0) is None

    def test_open_market_converts_price_slippage_to_symbol_points(
        self,
        monkeypatch,
    ):
        events = []
        anomalies = []
        tick = SimpleNamespace(
            bid=4438.59,
            ask=4438.81,
            time=1787155018,
            time_msc=1787155018328,
            last=0.0,
            volume=0,
            flags=4,
            volume_real=0.0,
        )
        result = SimpleNamespace(
            retcode=executor.mt5.TRADE_RETCODE_DONE,
            comment="Request executed",
            order=1798915563,
            deal=1484846572,
            volume=0.01,
            price=4441.06,
            bid=0.0,
            ask=0.0,
            request_id=3285623718,
            retcode_external=0,
        )
        position = SimpleNamespace(ticket=result.order, price_open=result.price)

        monkeypatch.setattr(executor.mt5, "symbol_info_tick", lambda symbol: tick)
        monkeypatch.setattr(executor, "_symbol_point_cache", 0.01)
        monkeypatch.setattr(executor.mt5, "order_send", lambda request: result)
        monkeypatch.setattr(
            executor.mt5,
            "positions_get",
            lambda ticket=None: [position] if ticket == result.order else [],
        )
        monkeypatch.setattr(
            executor,
            "_emit_event",
            lambda sig, ev, **fields: events.append((sig, ev, fields)),
        )
        monkeypatch.setattr(
            executor,
            "_emit_anomaly",
            lambda sig, category, severity, detail, **fields: anomalies.append(
                (sig, category, severity, detail, fields)
            ),
        )

        opened = executor.open_market_with_fill(
            "SELL",
            0.01,
            comment="c2_1716",
            magic=20260422,
        )

        assert opened == (1798915563, 4441.06)
        assert len(anomalies) == 1
        sig, category, severity, _, fields = anomalies[0]
        assert (sig, category, severity) == ("canal2_1716", "fill", "warning")
        assert fields["slip_price"] == pytest.approx(2.47)
        assert fields["slip_points"] == pytest.approx(247.0)


class TestClassifyPositionPnlsQuery:
    """positions_get() puede devolver None (MT5 IPC down), [] (sin posiciones
    abiertas), o lista con posiciones. Hoy tratamos None como [] silenciosamente
    → CLOSE_FIRST puede pensar que no hay nada abierto cuando MT5 no responde.
    """

    def test_lista_no_vacia(self):
        # Una posicion ficticia (solo importa que sea iterable no vacio)
        assert _classify_position_pnls_query([1, 2, 3]) == "ok"

    def test_lista_vacia(self):
        assert _classify_position_pnls_query([]) == "empty"

    def test_none_es_mt5_down(self):
        # CRITICO: None significa que mt5.positions_get FALLO, no que
        # no haya posiciones. Tratarlo como "empty" sin alertar es el bug.
        assert _classify_position_pnls_query(None) == "mt5_down"

    def test_tuple_vacia_tambien_empty(self):
        # MT5 puede devolver tuple a veces
        assert _classify_position_pnls_query(()) == "empty"


class TestOpenEntryPrices:
    def test_returns_only_tickets_that_are_still_open(self, monkeypatch):
        positions = (
            SimpleNamespace(ticket=101, price_open=4030.25),
            SimpleNamespace(ticket=999, price_open=4100.0),
        )
        monkeypatch.setattr(executor.mt5, "positions_get", lambda: positions)

        assert executor.open_entry_prices([101, 102]) == {101: 4030.25}

    def test_returns_none_when_mt5_query_failed(self, monkeypatch):
        monkeypatch.setattr(executor.mt5, "positions_get", lambda: None)

        assert executor.open_entry_prices([101]) is None


class TestSigIdFromOrderComment:
    def test_market_comment(self):
        assert _sig_id_from_order_comment("c2_12747") == "canal2_12747"

    def test_short_message_id_from_new_channel(self):
        assert _sig_id_from_order_comment("c2_278_B4") == "canal2_278"

    def test_scale_out_leg_comment(self):
        assert _sig_id_from_order_comment("c1_19822_B3") == "canal1_19822"

    def test_rescue_comment(self):
        assert _sig_id_from_order_comment("c2_12161_rescue") == "canal2_12161"

    def test_new_dca_comment(self):
        assert _sig_id_from_order_comment("DCA_c1_19569_4593.5") == "canal1_19569"

    def test_close_comment_has_no_signal_id(self):
        assert _sig_id_from_order_comment("bot_close") is None


class TestModifySLTPPreflight:
    def test_magic_mismatch_exposes_the_observed_mt5_owner(
        self,
        monkeypatch,
    ):
        position = SimpleNamespace(
            ticket=101,
            magic=20260421,
        )
        monkeypatch.setattr(
            executor.mt5,
            "positions_get",
            lambda ticket: [position],
        )

        decision = executor.preflight_modify_sltp(
            101,
            new_sl=4059.61,
            expected_magic=20260422,
        )

        assert decision.status == "invalid_magic"
        assert decision.reason == "magic_mismatch"
        assert decision.observed_ticket == 101
        assert decision.observed_magic == 20260421
        assert decision.observed_kind == "position"

    def test_sell_tp_update_preserves_valid_sl_while_tighter_sl_waits(self):
        position = SimpleNamespace(
            type=executor.mt5.ORDER_TYPE_SELL,
            sl=4060.95,
            tp=4055.0,
            price_open=4059.61,
        )
        tick = SimpleNamespace(bid=4059.98, ask=4060.20)
        symbol_info = SimpleNamespace(
            point=0.01,
            trade_stops_level=0,
            trade_freeze_level=0,
        )

        decision = executor.evaluate_position_sltp(
            position,
            tick,
            symbol_info,
            new_sl=4059.61,
            new_tp=4052.0,
        )

        assert decision.status == "apply_tp_defer_sl"
        assert decision.effective_sl == 4060.95
        assert decision.effective_tp == 4052.0
        assert decision.deferred_sl == 4059.61
        assert decision.reason == "requested_sl_waits_for_market"

    def test_invalid_sl_without_tp_waits_and_keeps_existing_protection(self):
        position = SimpleNamespace(
            type=executor.mt5.ORDER_TYPE_BUY,
            sl=4055.0,
            tp=4070.0,
            price_open=4060.0,
        )
        tick = SimpleNamespace(bid=4059.0, ask=4059.2)
        symbol_info = SimpleNamespace(
            point=0.01,
            trade_stops_level=10,
            trade_freeze_level=0,
        )

        decision = executor.evaluate_position_sltp(
            position,
            tick,
            symbol_info,
            new_sl=4060.0,
            new_tp=None,
        )

        assert decision.status == "wait_market"
        assert decision.effective_sl == 4055.0
        assert decision.deferred_sl == 4060.0

    def test_non_finite_stop_is_permanently_invalid(self):
        decision = executor.evaluate_position_sltp(
            SimpleNamespace(
                type=executor.mt5.ORDER_TYPE_BUY,
                sl=4055.0,
                tp=4070.0,
                price_open=4060.0,
            ),
            SimpleNamespace(bid=4061.0, ask=4061.2),
            SimpleNamespace(
                point=0.01,
                trade_stops_level=0,
                trade_freeze_level=0,
            ),
            new_sl=float("nan"),
            new_tp=None,
        )

        assert decision.status == "invalid_request"
        assert decision.reason == "invalid_sl_value"

    def test_full_modify_defers_to_queue_when_only_tp_is_currently_legal(
        self,
        monkeypatch,
    ):
        sent = []
        position = SimpleNamespace(
            ticket=101,
            type=executor.mt5.ORDER_TYPE_SELL,
            sl=4060.95,
            tp=4055.0,
            price_open=4059.61,
            magic=20260422,
        )
        monkeypatch.setattr(executor.mt5, "positions_get", lambda ticket: [position])
        monkeypatch.setattr(
            executor.mt5,
            "symbol_info_tick",
            lambda symbol: SimpleNamespace(bid=4059.98, ask=4060.20),
        )
        monkeypatch.setattr(
            executor.mt5,
            "symbol_info",
            lambda symbol: SimpleNamespace(
                point=0.01,
                trade_stops_level=0,
                trade_freeze_level=0,
            ),
        )

        def fake_send(request, label, *, evidence=None):
            sent.append(request)
            return SimpleNamespace(retcode=10009, comment="done")

        monkeypatch.setattr(executor, "_send_safe", fake_send)

        retcode = executor.modify_sltp_rc(
            101,
            new_sl=4059.61,
            new_tp=4052.0,
            expected_magic=20260422,
        )

        assert retcode == executor.mt5.TRADE_RETCODE_INVALID_STOPS
        assert sent == []


class TestOpenMarketAmbiguousResult:
    def test_recovers_timeout_result_when_mt5_position_exists(
            self, monkeypatch):
        sent = []
        events = []
        anomalies = []

        monkeypatch.setattr(executor.config,
                            "STRATEGY_MARKET_OPEN_ORDER_PROBE_ENABLED",
                            True, raising=False)
        monkeypatch.setattr(executor.config,
                            "STRATEGY_MARKET_OPEN_ORDER_PROBE_ATTEMPTS",
                            1, raising=False)
        monkeypatch.setattr(executor.config,
                            "STRATEGY_MARKET_OPEN_ORDER_PROBE_SLEEP_S",
                            0.0, raising=False)
        monkeypatch.setattr(executor, "_emit_event",
                            lambda sig, ev, **kw: events.append((sig, ev, kw)))
        monkeypatch.setattr(executor, "_emit_anomaly",
                            lambda sig, category, severity, detail, **kw:
                            anomalies.append((sig, category, severity, detail, kw)))

        tick = SimpleNamespace(bid=4517.16, ask=4517.36)
        monkeypatch.setattr(executor.mt5, "symbol_info_tick",
                            lambda symbol: tick)
        monkeypatch.setattr(executor.mt5, "symbol_select",
                            lambda symbol, enable: True)

        timeout_res = SimpleNamespace(
            retcode=10012,
            comment="Request timeout",
            order=1348595935,
            deal=0,
            volume=0,
            price=0,
            bid=0,
            ask=0,
            request_id=1039404759,
        )

        def fake_order_send(req):
            sent.append(req)
            return timeout_res

        position = SimpleNamespace(ticket=1348595935, price_open=4519.02)
        monkeypatch.setattr(executor.mt5, "order_send", fake_order_send)
        monkeypatch.setattr(executor.mt5, "positions_get",
                            lambda ticket=None: [position]
                            if ticket == 1348595935 else [])
        monkeypatch.setattr(executor.mt5, "history_deals_get",
                            lambda position=None: None)

        result = executor.open_market_with_fill(
            "SELL", 0.01, comment="c2_12887", magic=20260422)

        assert result == (1348595935, 4519.02)
        assert len(sent) == 1
        assert any(ev == "market_fill_recovered_from_non_done"
                   for _, ev, _ in events)
        assert any(category == "fill" and severity == "warning"
                   for _, category, severity, _, _ in anomalies)
