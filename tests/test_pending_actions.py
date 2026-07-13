"""
test_pending_actions.py — Tests minimos de la cola de acciones pendientes.

NO testeamos el ciclo completo (requiere mock asyncio + MT5 + ticks).
Solo cubrimos las INVARIANTES de gestion del task del runner, que es donde
estaba el bug C4.

Cubre:
  - PendingAction.expired (timeout default y custom)
  - PendingQueue._ensure_runner (no duplica si ya hay task vivo)
  - REGRESION C4: _log_failure NO resetea self._task (race con runner activo)
"""

import asyncio
import time
from types import SimpleNamespace

import pytest

from pending_actions import (
    PendingQueue,
    PendingAction,
    DEFAULT_TIMEOUT_S,
)
from state import Signal


def _make_action(label="test", new_sl=None, new_tp=None, kind="MODIFY_SLTP"):
    sig = Signal(channel="canal2", message_id=1, direction="BUY")
    return PendingAction(
        kind=kind,
        ticket=12345,
        signal=sig,
        new_sl=new_sl,
        new_tp=new_tp,
        label=label,
    )


# ─── PendingAction.expired ──────────────────────────────────────────────────

class TestPendingActionExpired:
    def test_fresh_not_expired(self):
        act = _make_action()
        assert act.expired() is False

    def test_default_timeout_is_one_hour(self):
        assert DEFAULT_TIMEOUT_S == 3600

    def test_old_action_expired(self):
        act = _make_action()
        # Forzamos created_at a hace 2h
        act.created_at = time.time() - 7200
        assert act.expired() is True

    def test_custom_timeout(self):
        act = _make_action()
        act.timeout_s = 60
        act.created_at = time.time() - 120  # hace 2min, timeout 1min
        assert act.expired() is True


# ─── PendingQueue._ensure_runner ────────────────────────────────────────────

class TestEnsureRunner:
    """No debe arrancar un segundo runner si ya hay uno vivo."""

    def test_no_runner_initially(self):
        q = PendingQueue()
        assert q._task is None

    @pytest.mark.asyncio
    async def test_ensure_runner_creates_task_when_none(self):
        # Setup: parchamos _run con uno trivial que termina inmediato
        # (no podemos usar el real porque llama MT5).
        q = PendingQueue()
        run_called = []

        async def trivial_run():
            run_called.append(True)

        q._run = trivial_run  # type: ignore
        q._ensure_runner()
        assert q._task is not None
        await asyncio.wait_for(q._task, timeout=2.0)
        assert run_called == [True]

    @pytest.mark.asyncio
    async def test_ensure_runner_does_not_create_second_if_alive(self):
        q = PendingQueue()
        # Encolamos una accion que el runner intentara procesar — pero como
        # no hay MT5, _try_once fallaria. Para evitarlo, monkey-parchamos
        # _run con uno que duerme (simula runner activo procesando).
        async def fake_run():
            await asyncio.sleep(0.5)
        q._run = fake_run  # type: ignore
        q._ensure_runner()
        first_task = q._task
        assert first_task is not None
        # Llamamos otra vez mientras el runner sigue corriendo
        q._ensure_runner()
        # No debe haberse reemplazado por uno nuevo
        assert q._task is first_task
        # Esperamos que termine
        await asyncio.wait_for(q._task, timeout=2.0)


# ─── REGRESION C4 ───────────────────────────────────────────────────────────

class TestC4LogFailureDoesNotResetTask:
    """Bug C4: _log_failure reseteaba self._task = None mientras _run seguia
    activo. Race: la siguiente add() veia None y arrancaba SEGUNDO runner."""

    @pytest.mark.asyncio
    async def test_log_failure_preserves_task_reference(self, tmp_path, monkeypatch):
        import journal
        monkeypatch.setattr(
            journal,
            "EVENTS_TEST_FILE",
            tmp_path / "trade_events_TEST.jsonl",
        )

        q = PendingQueue()
        # Setup minimo: simulamos un runner activo con un task dummy
        async def dummy_run():
            await asyncio.sleep(1.0)
        q._task = asyncio.create_task(dummy_run())
        original_task = q._task

        # Simulamos una accion que va a fallar — encolamos y llamamos
        # _log_failure manualmente con cualquier reason
        act = _make_action()
        token = journal._test_context.set(True)
        try:
            q._log_failure(act, reason="test_regression_c4")
        finally:
            journal._test_context.reset(token)

        # CRITICAL: _log_failure NO debe haber tocado self._task
        assert q._task is original_task, (
            "REGRESION C4: _log_failure reseteo self._task, lo que crea "
            "race condition cuando el runner sigue activo y add() arranca "
            "un segundo runner coexistiendo."
        )

        # Limpieza: cancelamos el dummy task
        original_task.cancel()
        try:
            await original_task
        except asyncio.CancelledError:
            pass


class TestModifyPreconditions:
    @pytest.mark.asyncio
    async def test_invalid_stop_waits_without_mt5_submission(self, monkeypatch):
        q = PendingQueue()
        act = _make_action(new_sl=4060.0)
        submitted = []
        monkeypatch.setattr(q, "_log_waiting_precondition", lambda *args: None)
        monkeypatch.setattr(
            "pending_actions.executor.preflight_modify_sltp",
            lambda *args, **kwargs: SimpleNamespace(
                status="wait_market",
                effective_sl=4055.0,
                effective_tp=4070.0,
                deferred_sl=4060.0,
                reason="requested_sl_waits_for_market",
            ),
        )
        monkeypatch.setattr(
            "pending_actions.executor.modify_sltp_rc",
            lambda *args, **kwargs: submitted.append((args, kwargs)),
        )

        result = await q._try_once(act)

        assert result == "WAIT_PRECONDITION"
        assert act.attempts == 0
        assert act.last_retcode is None
        assert submitted == []

    @pytest.mark.asyncio
    async def test_compatible_tp_applies_once_while_sl_stays_deferred(
        self,
        monkeypatch,
    ):
        q = PendingQueue()
        act = _make_action(new_sl=4059.61, new_tp=4052.0)
        submitted = []
        monkeypatch.setattr(q, "_log_waiting_precondition", lambda *args: None)
        monkeypatch.setattr(
            "pending_actions.executor.preflight_modify_sltp",
            lambda *args, **kwargs: SimpleNamespace(
                status="apply_tp_defer_sl",
                effective_sl=4060.95,
                effective_tp=4052.0,
                deferred_sl=4059.61,
                reason="requested_sl_waits_for_market",
            ),
        )

        def fake_modify(ticket, new_sl, new_tp, expected_magic=None):
            submitted.append((ticket, new_sl, new_tp, expected_magic))
            return 10009

        monkeypatch.setattr(
            "pending_actions.executor.modify_sltp_rc",
            fake_modify,
        )
        monkeypatch.setattr(q, "_log_partial_modify", lambda *args: None)

        result = await q._try_once(act)

        assert result == "WAIT_PRECONDITION"
        assert submitted == [(12345, None, 4052.0, act.signal.magic)]
        assert act.attempts == 1
        assert act.applied_tp == 4052.0
        assert act.new_tp is None
        assert act.new_sl == 4059.61

    @pytest.mark.asyncio
    async def test_unexpected_broker_invalid_stops_drops_after_one_submission(
        self,
        monkeypatch,
    ):
        q = PendingQueue()
        act = _make_action(new_sl=4059.61)
        monkeypatch.setattr(
            "pending_actions.executor.preflight_modify_sltp",
            lambda *args, **kwargs: SimpleNamespace(
                status="ready",
                effective_sl=4059.61,
                effective_tp=4052.0,
                deferred_sl=None,
                reason=None,
            ),
        )
        monkeypatch.setattr(
            "pending_actions.executor.modify_sltp_rc",
            lambda *args, **kwargs: 10016,
        )

        result = await q._try_once(act)

        assert result == "DROP_STOPS_STRUCTURAL"
        assert act.attempts == 1
        assert act.last_retcode == 10016

    def test_equivalent_modify_actions_share_one_queue_slot(self, monkeypatch):
        q = PendingQueue()
        monkeypatch.setattr(q, "_ensure_runner", lambda: None)
        monkeypatch.setattr(q, "_log_request", lambda *args: None)
        monkeypatch.setattr(q, "_log_coalesced", lambda *args, **kwargs: None)
        first = _make_action(new_sl=4059.61, new_tp=4052.0)
        repeated = _make_action(new_sl=4059.61, new_tp=4052.0)

        q.add(first)
        q.add(repeated)

        assert len(q._actions) == 1
        assert q._actions[0].new_sl == 4059.61
        assert q._actions[0].new_tp == 4052.0

    def test_structural_incident_message_aggregates_tickets(self):
        actions = [
            _make_action(new_sl=4059.61, new_tp=4052.0)
            for _ in range(5)
        ]
        for index, action in enumerate(actions, start=1):
            action.ticket = 100 + index
            action.attempts = 1
            action.last_retcode = 10016

        text = PendingQueue._format_structural_notification(actions)

        assert "Canal 2" in text
        assert "BUY" in text
        assert "5 posiciones" in text
        assert "101, 102, 103, 104, 105" in text
        assert "1 intento MT5 por posicion" in text


class TestForensicLifecycleLogging:
    def test_log_request_records_modify_payload(self, monkeypatch):
        events = []
        import journal
        monkeypatch.setattr(
            journal, "event",
            lambda sig, ev, **fields: events.append((sig, ev, fields)))

        q = PendingQueue()
        act = _make_action(label="BE #12345", new_sl=4700.0)
        q._log_request(act)

        assert events == [(
            "canal2_1",
            "mt5_modify_requested",
            {
                "ticket": 12345,
                "new_sl": 4700.0,
                "new_tp": None,
                "label": "BE #12345",
            },
        )]

    def test_log_done_records_close_result(self, monkeypatch):
        events = []
        import journal
        import pending_actions
        monkeypatch.setattr(
            journal, "event",
            lambda sig, ev, **fields: events.append((sig, ev, fields)))
        monkeypatch.setattr(
            pending_actions.mt5, "positions_get",
            lambda ticket: [])

        q = PendingQueue()
        act = _make_action(label="close bad leg", kind="CLOSE_POSITION")
        act.attempts = 3
        act.last_retcode = 10009
        q._log_done(act)

        assert events == [(
            "canal2_1",
            "mt5_close_result",
            {
                "ticket": 12345,
                "attempts": 3,
                "retcode": 10009,
                "label": "close bad leg",
            },
        ), (
            "canal2_1",
            "mt5_position_snapshot",
            {
                "ticket": 12345,
                "after_action": "CLOSE_POSITION",
                "retcode": 10009,
                "label": "close bad leg",
                "requested_sl": None,
                "requested_tp": None,
                "position_exists": False,
            },
        )]

    def test_log_done_records_post_modify_position_snapshot(self, monkeypatch):
        events = []
        import journal
        import pending_actions
        monkeypatch.setattr(
            journal, "event",
            lambda sig, ev, **fields: events.append((sig, ev, fields)))
        monkeypatch.setattr(
            pending_actions.mt5, "positions_get",
            lambda ticket: [SimpleNamespace(
                ticket=ticket,
                symbol="XAUUSD",
                magic=20260422,
                type=0,
                volume=0.01,
                price_open=4700.0,
                price_current=4701.25,
                sl=4700.0,
                tp=4708.5,
                profit=1.25,
                comment="c2_1",
            )])

        q = PendingQueue()
        act = _make_action(label="BE #12345", new_sl=4700.0, new_tp=4708.5)
        act.attempts = 2
        act.last_retcode = 10009
        q._log_done(act)

        assert events[0][1] == "mt5_modify_confirmed"
        snapshot = events[1]
        assert snapshot[0] == "canal2_1"
        assert snapshot[1] == "mt5_position_snapshot"
        assert snapshot[2]["after_action"] == "MODIFY_SLTP"
        assert snapshot[2]["position_exists"] is True
        assert snapshot[2]["sl"] == 4700.0
        assert snapshot[2]["tp"] == 4708.5
        assert snapshot[2]["price_current"] == 4701.25

    def test_log_done_records_modify_position_gone(self, monkeypatch):
        events = []
        import journal
        monkeypatch.setattr(
            journal, "event",
            lambda sig, ev, **fields: events.append((sig, ev, fields)))

        q = PendingQueue()
        act = _make_action(label="BE #12345", new_sl=4700.0)
        act.attempts = 1
        act.last_retcode = 10036
        q._log_done(act)

        assert events == [(
            "canal2_1",
            "mt5_modify_skipped_position_gone",
            {
                "ticket": 12345,
                "attempts": 1,
                "retcode": 10036,
                "label": "BE #12345",
                "new_sl": 4700.0,
                "new_tp": None,
            },
        )]
