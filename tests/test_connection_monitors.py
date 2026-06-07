"""
test_connection_monitors.py — Decisión pura de los monitores de conexión.

Los monitores de Telegram y MT5 detectan disconnect sostenido y emiten
anomaly crítica. La lógica de "¿debo alertar ya?" la encapsulamos en una
función pura para testearla sin async loops ni mocks de Telethon/MT5.
"""
import pytest

import main
from state import Signal, StateManager


class TestShouldAlertSustainedDisconnect:
    """Reglas del alerter:
       True solo si — estoy desconectado AHORA, lo estaba también la
       observación anterior (sustained, no transición), llevo >= threshold
       en este estado, y aún no he alertado para este disconnect."""

    def test_alert_cuando_desconectado_largo_sin_alertar(self):
        assert main._should_alert_sustained_disconnect(
            connected=False, last_state=False, age_s=400,
            already_alerted=False, threshold_s=300) is True

    def test_no_alert_si_conectado(self):
        assert main._should_alert_sustained_disconnect(
            connected=True, last_state=True, age_s=400,
            already_alerted=False, threshold_s=300) is False

    def test_no_alert_si_recien_desconectado(self):
        # Aún por debajo del umbral
        assert main._should_alert_sustained_disconnect(
            connected=False, last_state=False, age_s=100,
            already_alerted=False, threshold_s=300) is False

    def test_no_alert_si_ya_alertado(self):
        assert main._should_alert_sustained_disconnect(
            connected=False, last_state=False, age_s=400,
            already_alerted=True, threshold_s=300) is False

    def test_no_alert_durante_la_transicion(self):
        # Estaba CONECTADO y ahora aparece desconectado: es la primera
        # detección, no es sustained — last_state debe ser False también.
        assert main._should_alert_sustained_disconnect(
            connected=False, last_state=True, age_s=400,
            already_alerted=False, threshold_s=300) is False

    def test_no_alert_si_last_state_none(self):
        # Primer ciclo del loop: last_state es None hasta la primera lectura.
        assert main._should_alert_sustained_disconnect(
            connected=False, last_state=None, age_s=400,
            already_alerted=False, threshold_s=300) is False

    def test_alert_exacto_en_threshold(self):
        # >= threshold → True
        assert main._should_alert_sustained_disconnect(
            connected=False, last_state=False, age_s=300.0,
            already_alerted=False, threshold_s=300.0) is True


class TestHeartbeatOpenSignalCount:
    def test_count_open_signals_deduplicates_canal1_aliases(self):
        st = StateManager()
        sig = Signal(channel="canal1", message_id=19868, direction="SELL",
                     status="open")
        st.add(sig)
        st.alias(sig, 19869)
        st.add(Signal(channel="canal2", message_id=12780, direction="BUY",
                      status="open"))

        assert main._count_open_signals_unique(st) == 2


class TestTelegramRunBackoff:
    @pytest.mark.asyncio
    async def test_retries_transient_history_error_without_exiting(
            self, monkeypatch):
        class FakeClient:
            def __init__(self):
                self.attempts = 0
                self.connects = 0

            async def run_until_disconnected(self):
                self.attempts += 1
                if self.attempts == 1:
                    raise RuntimeError(
                        "Telegram is having internal issues ServerError: "
                        "RPCError -500: No workers running "
                        "(caused by GetHistoryRequest)"
                    )

            def is_connected(self):
                return True

            async def connect(self):
                self.connects += 1

        fake_client = FakeClient()
        sleeps = []
        events = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        monkeypatch.setattr(main, "client", fake_client)
        monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
        monkeypatch.setattr(
            main.journal,
            "event",
            lambda sig, ev, **fields: events.append((sig, ev, fields)),
        )

        await main._run_until_disconnected_with_backoff()

        assert fake_client.attempts == 2
        assert fake_client.connects == 0
        assert sleeps == [15.0]
        assert events == [
            ("bot", "telegram_run_until_disconnected_backoff", {
                "failures": 1,
                "cooldown_s": 15.0,
                "error": (
                    "Telegram is having internal issues ServerError: "
                    "RPCError -500: No workers running "
                    "(caused by GetHistoryRequest)"
                ),
            })
        ]


class TestFreezeTracebackWatchdog:
    def test_traceback_watchdog_disabled_when_timeout_zero(self):
        assert main._freeze_traceback_enabled(0) is False

    def test_arm_traceback_dump_reschedules_faulthandler(self, monkeypatch,
                                                         tmp_path):
        calls = []

        monkeypatch.setattr(main.faulthandler,
                            "cancel_dump_traceback_later",
                            lambda: calls.append(("cancel",)))
        monkeypatch.setattr(main.faulthandler,
                            "dump_traceback_later",
                            lambda timeout, repeat, file, exit:
                            calls.append((timeout, repeat, file, exit)))
        main._freeze_traceback_file_handle = None

        armed = main._arm_freeze_traceback_dump(
            timeout_sec=123.0,
            path=tmp_path / "freeze_traceback.log",
        )

        assert armed is True
        assert calls[0] == ("cancel",)
        assert calls[1][0] == 123.0
        assert calls[1][1] is False
        assert calls[1][3] is False
        assert not calls[1][2].closed
        main._freeze_traceback_file_handle.close()


class TestMt5ReconnectAuditDecision:
    def test_audits_only_reconnect_with_open_signals(self):
        assert main._should_audit_mt5_reconnect(
            connected=True, previous_state=False, open_signals=2) is True

    def test_no_audit_without_open_signals(self):
        assert main._should_audit_mt5_reconnect(
            connected=True, previous_state=False, open_signals=0) is False

    def test_no_audit_on_disconnect_transition(self):
        assert main._should_audit_mt5_reconnect(
            connected=False, previous_state=True, open_signals=2) is False


class TestMt5TradePermissionAlert:
    def test_alerts_when_terminal_connected_but_autotrading_disabled(self):
        assert main._should_alert_mt5_trade_disabled(
            connected=True,
            trade_allowed=False,
            tradeapi_disabled=False,
            already_alerted=False,
        ) is True

    def test_alerts_when_python_trade_api_disabled(self):
        assert main._should_alert_mt5_trade_disabled(
            connected=True,
            trade_allowed=True,
            tradeapi_disabled=True,
            already_alerted=False,
        ) is True

    def test_no_alert_when_already_alerted(self):
        assert main._should_alert_mt5_trade_disabled(
            connected=True,
            trade_allowed=False,
            tradeapi_disabled=False,
            already_alerted=True,
        ) is False

    def test_no_alert_when_disconnected_or_status_unknown(self):
        assert main._should_alert_mt5_trade_disabled(
            connected=False,
            trade_allowed=False,
            tradeapi_disabled=False,
            already_alerted=False,
        ) is False
        assert main._should_alert_mt5_trade_disabled(
            connected=True,
            trade_allowed=None,
            tradeapi_disabled=None,
            already_alerted=False,
        ) is False


class TestNakedProtectiveSl:
    def test_should_apply_only_when_open_naked_and_has_entry(self):
        sig = Signal(channel="canal2", message_id=12780, direction="BUY",
                     market_ticket=100, market_fill_price=4529.89)
        assert main._should_apply_naked_protective_sl(sig) is True

        sig.sl = 4519.89
        assert main._should_apply_naked_protective_sl(sig) is False

        sig.sl = None
        sig.market_ticket = None
        assert main._should_apply_naked_protective_sl(sig) is False

    @pytest.mark.asyncio
    async def test_apply_naked_protective_sl_to_all_open_tickets(self,
                                                                monkeypatch):
        sig = Signal(channel="canal2", message_id=12780, direction="BUY",
                     market_ticket=100,
                     extra_market_tickets=[101, 102],
                     market_fill_price=4529.89)
        modifies = []
        events = []

        monkeypatch.setattr(main.pending_actions, "enqueue_modify_sl",
                            lambda signal, ticket, new_sl, label="":
                            modifies.append((signal, ticket, new_sl, label)))
        monkeypatch.setattr(main.journal, "event",
                            lambda sig_id, ev, **kw:
                            events.append((sig_id, ev, kw)))
        monkeypatch.setattr(main.journal, "anomaly",
                            lambda *args, **kwargs: None)

        await main._apply_naked_protective_sl(sig, elapsed_s=130.0)

        assert sig.sl == 4519.89
        assert sig.levels_predicted is True
        assert getattr(sig, "_naked_protective_sl_applied") is True
        assert [ticket for _, ticket, _, _ in modifies] == [100, 101, 102]
        assert all(new_sl == 4519.89 for _, _, new_sl, _ in modifies)
        assert any(ev == "naked_protective_sl_applied"
                   for _, ev, _ in events)
