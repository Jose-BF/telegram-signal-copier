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
import json
import threading
import time
from types import SimpleNamespace

import pytest

import causal_trace
import pending_actions
from pending_actions import (
    PendingQueue,
    PendingAction,
    DEFAULT_TIMEOUT_S,
    _record_confirmed_levels,
    _should_alert_waiting_exact_be,
    _should_alert_stuck_stops,
    snapshot,
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


def test_pending_action_spool_survives_process_restart(tmp_path, monkeypatch):
    monkeypatch.setattr("journal.event", lambda *args, **kwargs: None)
    spool = tmp_path / "runtime_pending_actions.json"
    signal = Signal(channel="canal2", message_id=278, direction="SELL")
    first = PendingQueue(spool_path=spool)
    first._ensure_runner = lambda: None
    first.add(PendingAction(
        kind="MODIFY_SLTP",
        ticket=1634685403,
        signal=signal,
        new_sl=4122.10,
        new_tp=4119.0,
        label="BE #1634685403",
        persist_until_signal_close=True,
    ))

    payload = json.loads(spool.read_text(encoding="utf-8"))
    assert payload["version"] == 2
    assert payload["actions"][0]["message_id"] == 278
    assert payload["actions"][0]["new_sl"] == 4122.10
    action_id = payload["actions"][0]["action_id"]
    decision_id = payload["actions"][0]["decision_id"]

    restored_signal = Signal(
        channel="canal2",
        message_id=278,
        direction="SELL",
    )
    state_manager = SimpleNamespace(
        get=lambda channel, message_id: (
            restored_signal
            if (channel, message_id) == ("canal2", 278)
            else None
        )
    )
    second = PendingQueue(spool_path=spool)
    second._ensure_runner = lambda: None

    restored = second.restore_from_spool(state_manager)

    assert restored == 1
    assert len(second._actions) == 1
    assert second._actions[0].ticket == 1634685403
    assert second._actions[0].signal is restored_signal
    assert second._actions[0].persist_until_signal_close is True
    assert second._actions[0].action_id == action_id
    assert second._actions[0].decision_id == decision_id


def test_pending_action_captures_bound_causal_context():
    with causal_trace.bind_message_revision(
        "msgrev_source",
        decision_id="decision_source",
    ):
        action = _make_action(new_sl=4100.0)

    assert action.action_id.startswith("action_")
    assert action.decision_id == "decision_source"
    assert action.message_revision_id == "msgrev_source"


def test_legacy_spool_restores_with_explicit_recovered_lineage(
        tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(
        "journal.event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )
    spool = tmp_path / "runtime_pending_actions.json"
    spool.write_text(json.dumps({
        "version": 1,
        "saved_at": 1.0,
        "actions": [{
            "kind": "MODIFY_SLTP",
            "ticket": 1634685403,
            "channel": "canal2",
            "message_id": 278,
            "direction": "SELL",
            "new_sl": 4122.10,
            "new_tp": 4119.0,
            "created_at": 1.0,
            "timeout_s": 3600,
            "label": "legacy",
            "persist_until_signal_close": True,
            "revision": 0,
        }],
    }), encoding="utf-8")
    queue = PendingQueue(spool_path=spool)
    queue._ensure_runner = lambda: None
    state_manager = SimpleNamespace(get=lambda channel, message_id: None)

    assert queue.restore_from_spool(state_manager) == 1

    action = queue._actions[0]
    assert action.action_id.startswith("action_")
    assert action.decision_id.startswith("decision_")
    restored = next(
        row for row in events if row[1] == "mt5_pending_action_restored")
    assert restored[2]["lineage_recovered_from_legacy_spool"] is True
    assert restored[2]["action_id"] == action.action_id


def test_pending_spool_is_rewritten_when_action_leaves_queue(tmp_path, monkeypatch):
    monkeypatch.setattr("journal.event", lambda *args, **kwargs: None)
    spool = tmp_path / "runtime_pending_actions.json"
    queue = PendingQueue(spool_path=spool)
    queue._ensure_runner = lambda: None
    queue.add(_make_action(new_sl=4100.0))

    queue._actions = []
    queue._persist_spool()

    payload = json.loads(spool.read_text(encoding="utf-8"))
    assert payload["actions"] == []


@pytest.mark.asyncio
async def test_closed_signal_cancels_pending_modify_without_false_failure(
    monkeypatch,
):
    events = []
    anomalies = []
    signal = Signal(
        channel="canal2",
        message_id=2110,
        direction="BUY",
        status="closed",
    )
    action = PendingAction(
        kind="MODIFY_SLTP",
        ticket=1644451068,
        signal=signal,
        new_sl=4580.0,
        new_tp=4585.0,
        label="TP ya alcanzado",
    )
    queue = PendingQueue()
    queue._actions.append(action)

    monkeypatch.setattr(
        pending_actions.mt5,
        "symbol_info_tick",
        lambda _symbol: SimpleNamespace(time_msc=1),
    )
    monkeypatch.setattr(
        "journal.event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )
    monkeypatch.setattr(
        "journal.anomaly",
        lambda *args, **fields: anomalies.append((args, fields)),
    )

    await queue._run()

    assert queue._actions == []
    assert [event for _, event, _ in events] == [
        "mt5_modify_cancelled_signal_closed"
    ]
    assert events[0][2]["reason"] == "signal_closed_during_retry"
    assert anomalies == []


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
    async def test_runner_does_not_retain_telegram_decision_context(self):
        q = PendingQueue()
        observed = []

        async def trivial_run():
            observed.append(causal_trace.current_fields())

        q._run = trivial_run  # type: ignore
        with causal_trace.bind_message_revision(
            "msgrev_source",
            decision_id="decision_source",
        ):
            q._ensure_runner()
        await asyncio.wait_for(q._task, timeout=2.0)

        assert observed == [{}]

    @pytest.mark.asyncio
    async def test_runner_does_not_retain_test_channel_context(self):
        import journal

        q = PendingQueue()
        observed = []

        async def trivial_run():
            observed.append(journal.is_test_mode())

        q._run = trivial_run  # type: ignore
        token = journal._test_context.set(True)
        try:
            q._ensure_runner()
        finally:
            journal._test_context.reset(token)
        await asyncio.wait_for(q._task, timeout=2.0)

        assert observed == [False]

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
    def test_confirmed_modify_records_real_sl_and_tp_per_ticket(self):
        act = _make_action(new_sl=4700.0, new_tp=4708.5)
        act.last_retcode = 10009

        recorded = _record_confirmed_levels(act)

        assert recorded is True
        assert act.signal.sl_by_ticket == {12345: 4700.0}
        assert act.signal.tp_by_ticket == {12345: 4708.5}

    def test_unconfirmed_modify_does_not_change_real_level_state(self):
        act = _make_action(new_sl=4700.0, new_tp=4708.5)
        act.last_retcode = 10016

        recorded = _record_confirmed_levels(act)

        assert recorded is False
        assert act.signal.sl_by_ticket == {}
        assert act.signal.tp_by_ticket == {}

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
    async def test_position_gone_preserves_preflight_evidence(
        self,
        monkeypatch,
    ):
        q = PendingQueue()
        act = _make_action(new_sl=4060.0)
        monkeypatch.setattr(
            "pending_actions.executor.preflight_modify_sltp",
            lambda *args, **kwargs: SimpleNamespace(
                status="position_gone",
                effective_sl=None,
                effective_tp=None,
                deferred_sl=None,
                reason="ticket_not_found",
            ),
        )

        result = await q._try_once(act)

        assert result == "DONE"
        assert act.attempts == 0
        assert act.last_attempt_id is None
        assert act.last_preflight_status == "position_gone"
        assert act.last_preflight_reason == "ticket_not_found"

    @pytest.mark.asyncio
    async def test_invalid_magic_preserves_the_observed_mt5_owner(
        self,
        monkeypatch,
    ):
        q = PendingQueue()
        act = _make_action(new_sl=4060.0)
        monkeypatch.setattr(
            "pending_actions.executor.preflight_modify_sltp",
            lambda *args, **kwargs: SimpleNamespace(
                status="invalid_magic",
                effective_sl=None,
                effective_tp=None,
                deferred_sl=None,
                reason="magic_mismatch",
                observed_ticket=12345,
                observed_magic=20260421,
                observed_kind="position",
            ),
        )

        result = await q._try_once(act)

        assert result == "DROP"
        assert act.attempts == 0
        assert act.last_retcode == 10013
        assert act.last_preflight_status == "invalid_magic"
        assert act.last_preflight_observed_ticket == 12345
        assert act.last_preflight_observed_magic == 20260421
        assert act.last_preflight_observed_kind == "position"

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

        def fake_modify(
            ticket,
            new_sl,
            new_tp,
            expected_magic=None,
            *,
            trace=None,
        ):
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
        assert act.signal.sl_by_ticket == {}
        assert act.signal.tp_by_ticket == {12345: 4052.0}

    @pytest.mark.asyncio
    async def test_unexpected_broker_invalid_stops_rechecks_after_cooldown(
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

        assert result == "RETRY"
        assert act.attempts == 1
        assert act.last_retcode == 10016
        assert act.retry_not_before > time.time()

    @pytest.mark.asyncio
    async def test_retries_share_action_and_use_distinct_attempt_ids(
        self,
        monkeypatch,
    ):
        q = PendingQueue()
        act = _make_action(new_sl=4059.61)
        traces = []
        retcodes = iter((10016, 10009))
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

        def modify(
            ticket,
            new_sl,
            new_tp,
            expected_magic=None,
            *,
            trace=None,
        ):
            traces.append(trace)
            return next(retcodes)

        monkeypatch.setattr(
            "pending_actions.executor.modify_sltp_rc",
            modify,
        )

        assert await q._try_once(act) == "RETRY"
        act.retry_not_before = 0.0
        assert await q._try_once(act) == "DONE"

        assert len(traces) == 2
        assert {row["action_id"] for row in traces} == {act.action_id}
        assert traces[0]["attempt_id"] != traces[1]["attempt_id"]
        assert act.last_attempt_id == traces[1]["attempt_id"]

    @pytest.mark.asyncio
    async def test_retry_cooldown_does_not_resubmit_same_modify(
        self,
        monkeypatch,
    ):
        q = PendingQueue()
        act = _make_action(new_sl=4059.61)
        act.retry_not_before = time.time() + 1.0
        submitted = []
        monkeypatch.setattr(
            "pending_actions.executor.preflight_modify_sltp",
            lambda *args, **kwargs: submitted.append("preflight"),
        )

        result = await q._try_once(act)

        assert result == "WAIT_RETRY_COOLDOWN"
        assert submitted == []

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

    def test_delayed_action_gets_internal_decision_linked_to_signal_origin(
        self,
    ):
        signal = Signal(
            channel="canal2",
            message_id=380,
            direction="BUY",
        )
        signal.source_message_revision_id = "msgrev_origin"
        signal.source_decision_id = "decision_origin"

        action = PendingAction(
            kind="MODIFY_SLTP",
            ticket=12345,
            signal=signal,
            new_sl=4056.53,
        )

        assert action.message_revision_id == "msgrev_origin"
        assert action.decision_id != "decision_origin"
        assert action.decision_id.startswith("decision_")
        assert action.parent_decision_id == "decision_origin"
        assert action.decision_origin == "internal"

    def test_unbound_pending_action_emits_internal_decision_manifest(
        self,
        monkeypatch,
    ):
        events = []
        monkeypatch.setattr(
            "journal.event",
            lambda sig, ev, **fields: events.append((sig, ev, fields)),
        )
        signal = Signal(
            channel="canal2",
            message_id=380,
            direction="BUY",
            source_message_revision_id="msgrev_origin",
            source_decision_id="decision_origin",
        )
        action = PendingAction(
            kind="MODIFY_SLTP",
            ticket=12345,
            signal=signal,
            new_sl=4056.53,
            internal_reason="position_lifecycle_be",
        )
        q = PendingQueue()
        monkeypatch.setattr(q, "_ensure_runner", lambda: None)

        q.add(action)

        assert [ev for _, ev, _ in events] == [
            "bot_internal_decision_started",
            "mt5_modify_requested",
            "bot_internal_decision",
        ]
        started = events[0][2]
        decision = next(
            fields for _, ev, fields in events
            if ev == "bot_internal_decision"
        )
        assert started["decision_id"] == decision["decision_id"]
        assert decision["decision_id"] == action.decision_id
        assert decision["parent_decision_id"] == "decision_origin"
        assert decision["message_revision_id"] == "msgrev_origin"
        assert decision["decision_reason"] == "position_lifecycle_be"
        assert decision["declared_action_ids"] == [action.action_id]
        assert decision["declared_action_count"] == 1

    def test_bound_telegram_action_does_not_emit_internal_decision(
        self,
        monkeypatch,
    ):
        events = []
        monkeypatch.setattr(
            "journal.event",
            lambda sig, ev, **fields: events.append((sig, ev, fields)),
        )
        q = PendingQueue()
        monkeypatch.setattr(q, "_ensure_runner", lambda: None)
        with causal_trace.bind_message_revision(
            "msgrev_edit",
            decision_id="decision_edit",
        ):
            action = _make_action(new_sl=4057.0)
            q.add(action)

        assert action.decision_origin == "telegram"
        assert not any(
            ev in {
                "bot_internal_decision_started",
                "bot_internal_decision",
            }
            for _, ev, _ in events
        )

    def test_existing_internal_action_does_not_leak_into_unrelated_manifest(
        self,
        monkeypatch,
    ):
        monkeypatch.setattr("journal.event", lambda *args, **kwargs: None)
        signal = Signal(
            channel="canal2",
            message_id=380,
            direction="BUY",
            source_message_revision_id="msgrev_origin",
            source_decision_id="decision_origin",
        )
        action = PendingAction(
            kind="MODIFY_SLTP",
            ticket=12345,
            signal=signal,
            new_sl=4056.53,
        )
        q = PendingQueue()
        monkeypatch.setattr(q, "_ensure_runner", lambda: None)

        with causal_trace.bind_message_revision(
            "msgrev_unrelated",
            decision_id="decision_unrelated",
        ):
            q.add(action)
            declared = causal_trace.declared_action_ids()

        assert declared == []

    def test_signal_captures_origin_when_created_in_message_context(self):
        with causal_trace.bind_message_revision(
            "msgrev_origin",
            decision_id="decision_origin",
        ):
            signal = Signal(
                channel="canal2",
                message_id=380,
                direction="BUY",
            )

        assert signal.source_message_revision_id == "msgrev_origin"
        assert signal.source_decision_id == "decision_origin"

    def test_current_management_revision_overrides_signal_origin(self):
        signal = Signal(
            channel="canal2",
            message_id=380,
            direction="BUY",
        )
        signal.source_message_revision_id = "msgrev_origin"
        signal.source_decision_id = "decision_origin"

        with causal_trace.bind_message_revision(
            "msgrev_edit",
            decision_id="decision_edit",
        ):
            action = PendingAction(
                kind="MODIFY_SLTP",
                ticket=12345,
                signal=signal,
                new_sl=4057.0,
            )

        assert action.message_revision_id == "msgrev_edit"
        assert action.decision_id == "decision_edit"

    def test_equivalent_coalesced_action_keeps_distinct_causal_sources(
        self,
        monkeypatch,
    ):
        events = []
        monkeypatch.setattr(
            "journal.event",
            lambda sig, ev, **fields: events.append((sig, ev, fields)),
        )
        q = PendingQueue()
        monkeypatch.setattr(q, "_ensure_runner", lambda: None)

        with causal_trace.bind_message_revision(
            "msgrev_first",
            decision_id="decision_first",
        ):
            first = _make_action(new_sl=4059.61, new_tp=4052.0)
        with causal_trace.bind_message_revision(
            "msgrev_second",
            decision_id="decision_second",
        ):
            repeated = _make_action(new_sl=4059.61, new_tp=4052.0)

        q.add(first)
        q.add(repeated)

        assert len(q._actions) == 1
        queued = q._actions[0]
        assert queued.action_id == first.action_id
        assert queued.message_revision_id == "msgrev_first"
        assert queued.decision_id == "decision_first"
        requests = [
            fields for _, ev, fields in events
            if ev == "mt5_modify_requested"
        ]
        assert [row["action_id"] for row in requests] == [
            first.action_id,
            repeated.action_id,
        ]
        coalesced = next(
            fields for _, ev, fields in events
            if ev == "mt5_action_coalesced"
        )
        assert coalesced["action_id"] == repeated.action_id
        assert coalesced["message_revision_id"] == "msgrev_second"
        assert coalesced["coalesced_into_action_id"] == first.action_id

    def test_changed_coalesced_action_supersedes_queue_identity(
            self,
            monkeypatch,
    ):
        events = []
        monkeypatch.setattr(
            "journal.event",
            lambda sig, ev, **fields: events.append((sig, ev, fields)),
        )
        q = PendingQueue()
        monkeypatch.setattr(q, "_ensure_runner", lambda: None)
        first = _make_action(new_sl=4059.61, new_tp=4052.0)
        first.last_attempt_id = "attempt_old"
        first.attempts = 2
        first.parent_decision_id = "decision_parent_old"
        first.decision_origin = "internal"
        first.internal_reason = "old_reason"
        changed = _make_action(new_sl=4060.25, new_tp=4050.0)
        changed.parent_decision_id = "decision_parent_new"
        changed.decision_origin = "telegram"
        changed.internal_reason = "new_reason"
        first_action_id = first.action_id

        q.add(first)
        q.add(changed)

        queued = q._actions[0]
        assert queued.action_id == changed.action_id
        assert queued.last_attempt_id is None
        assert queued.attempts == 0
        assert queued.parent_decision_id == "decision_parent_new"
        assert queued.decision_origin == "telegram"
        assert queued.internal_reason == "new_reason"
        coalesced = next(
            fields for _, ev, fields in events
            if ev == "mt5_action_coalesced"
        )
        assert coalesced["action_id"] == changed.action_id
        assert coalesced["supersedes_action_id"] == first_action_id

    @pytest.mark.asyncio
    async def test_completed_action_cannot_absorb_update_while_next_leg_runs(
            self, monkeypatch):
        q = PendingQueue()
        first = _make_action(new_sl=4059.61, new_tp=4052.0)
        second = _make_action(new_sl=4059.61, new_tp=4050.0)
        second.ticket = first.ticket + 1
        replacement = _make_action(new_sl=4060.25, new_tp=4051.0)
        old_first_action_id = first.action_id
        replacement_action_id = replacement.action_id
        seen = []
        injected = False
        tick_msc = 100

        async def fake_try_once(action):
            nonlocal injected
            seen.append(action.action_id)
            action.last_retcode = 10009
            if action is second and not injected:
                injected = True
                q.add(replacement)
            return "DONE"

        def fake_tick(_symbol):
            nonlocal tick_msc
            tick_msc += 1
            return SimpleNamespace(time_msc=tick_msc)

        q._actions.extend([first, second])
        monkeypatch.setattr(q, "_try_once", fake_try_once)
        monkeypatch.setattr(q, "_ensure_runner", lambda: None)
        monkeypatch.setattr(q, "_persist_spool", lambda: None)
        monkeypatch.setattr(q, "_log_done", lambda action: None)
        monkeypatch.setattr(
            pending_actions, "_record_confirmed_levels", lambda action: None
        )
        monkeypatch.setattr(
            pending_actions.mt5, "symbol_info_tick", fake_tick
        )

        await q._run()

        assert seen[0] == old_first_action_id
        assert seen[-1] == replacement_action_id
        assert seen.count(replacement_action_id) == 1
        assert q._actions == []

    def test_last_attempt_does_not_change_spool_fingerprint(self):
        q = PendingQueue()
        action = _make_action(new_sl=4059.61)
        q._actions.append(action)
        before = q._spool_fingerprint()

        action.last_attempt_id = "attempt_runtime_only"

        assert q._spool_fingerprint() == before
        assert "last_attempt_id" not in q._spool_payload(action)

    def test_superseded_attempt_event_keeps_completed_attempt_id(
        self,
        monkeypatch,
    ):
        events = []
        monkeypatch.setattr(
            "journal.event",
            lambda sig, ev, **fields: events.append((sig, ev, fields)),
        )
        q = PendingQueue()
        current = _make_action(new_sl=4058.0)
        completed = _make_action(new_sl=4059.0)
        completed.last_retcode = 10016
        completed.last_attempt_id = "attempt_completed"

        assert q._finish_superseded_attempt(current, completed) == "RETRY"

        row = next(
            fields for _, ev, fields in events
            if ev == "mt5_modify_attempt_superseded"
        )
        assert row["attempt_id"] == "attempt_completed"

    @pytest.mark.asyncio
    async def test_coalesced_modify_during_mt5_call_keeps_latest_payload(
        self,
        monkeypatch,
    ):
        """A newer SL/TP must not be attributed to an older MT5 request."""
        q = PendingQueue()
        monkeypatch.setattr(q, "_ensure_runner", lambda: None)
        monkeypatch.setattr(q, "_log_request", lambda *args: None)
        monkeypatch.setattr(q, "_log_coalesced", lambda *args, **kwargs: None)
        completed = []
        monkeypatch.setattr(
            q,
            "_log_done",
            lambda action: completed.append((
                action.new_sl,
                action.new_tp,
                action.label,
            )),
        )
        monkeypatch.setattr(
            "pending_actions.executor.preflight_modify_sltp",
            lambda *args, **kwargs: SimpleNamespace(
                status="ready",
                effective_sl=4044.49,
                effective_tp=4060.49,
                deferred_sl=None,
                reason=None,
            ),
        )

        entered_mt5 = threading.Event()
        release_mt5 = threading.Event()
        submitted = []

        def slow_modify(
            ticket,
            new_sl,
            new_tp,
            expected_magic=None,
            *,
            trace=None,
        ):
            submitted.append((ticket, new_sl, new_tp, expected_magic))
            entered_mt5.set()
            assert release_mt5.wait(timeout=2.0)
            return 10009

        monkeypatch.setattr(
            "pending_actions.executor.modify_sltp_rc",
            slow_modify,
        )

        first = _make_action(
            label="old levels",
            new_sl=4044.49,
            new_tp=4060.49,
        )
        q.add(first)
        in_flight = asyncio.create_task(q._try_once(first))
        assert await asyncio.to_thread(entered_mt5.wait, 1.0)

        q.add(_make_action(
            label="latest levels",
            new_sl=4044.35,
            new_tp=4060.35,
        ))
        release_mt5.set()
        result = await asyncio.wait_for(in_flight, timeout=2.0)

        assert result == "RETRY"
        assert submitted == [(12345, 4044.49, 4060.49, first.signal.magic)]
        assert completed == [(4044.49, 4060.49, "old levels")]
        assert first.new_sl == 4044.35
        assert first.new_tp == 4060.35
        assert first.label == "latest levels"
        assert first.signal.sl_by_ticket == {12345: 4044.49}
        assert first.signal.tp_by_ticket == {12345: 4060.49}

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

        assert "Gold Signals" in text
        assert "BUY" in text
        assert "5 posiciones" in text
        assert "101, 102, 103, 104, 105" in text
        assert "1 intento MT5 por posicion" in text
        assert "continua reintentando" in text.lower()

    def test_structural_incident_groups_cent_level_be_differences(self):
        actions = [
            _make_action(new_sl=value, new_tp=4065.0)
            for value in (4059.61, 4059.63, 4059.65)
        ]
        for index, action in enumerate(actions, start=1):
            action.ticket = 200 + index
            action.attempts = 2
            action.last_retcode = 10016
            action.last_preflight_status = "wait_market"
            action.last_preflight_reason = "sl_wrong_side"

        keys = {
            PendingQueue._structural_incident_key(action)
            for action in actions
        }
        text = PendingQueue._format_structural_notification(actions)

        assert len(keys) == 1
        assert "3 posiciones" in text
        assert "4059.61 a 4059.65" in text


def test_stuck_stops_alerts_once_after_threshold():
    assert _should_alert_stuck_stops(10016, 30.0, 30.0, False) is True
    assert _should_alert_stuck_stops(10016, 29.9, 30.0, False) is False
    assert _should_alert_stuck_stops(10016, 60.0, 30.0, True) is False
    assert _should_alert_stuck_stops(10029, 60.0, 30.0, False) is False


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

        assert len(events) == 1
        sig_id, event_name, fields = events[0]
        assert sig_id == "canal2_1"
        assert event_name == "mt5_modify_requested"
        assert fields["ticket"] == 12345
        assert fields["new_sl"] == 4700.0
        assert fields["new_tp"] is None
        assert fields["label"] == "BE #12345"
        assert fields["action_id"] == act.action_id
        assert fields["decision_id"] == act.decision_id
        assert "message_revision_id" in fields
        assert fields["action_revision"] == 0

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
        act.last_attempt_id = "attempt_close"
        q._log_done(act)

        assert [event_name for _, event_name, _ in events] == [
            "mt5_close_result",
            "mt5_position_snapshot",
        ]
        for _, _, fields in events:
            assert fields["action_id"] == act.action_id
            assert fields["decision_id"] == act.decision_id
            assert fields["action_revision"] == 0
        assert events[0][2]["ticket"] == 12345
        assert events[0][2]["attempts"] == 3
        assert events[0][2]["attempt_id"] == "attempt_close"
        assert events[1][2]["position_exists"] is False

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
        assert snapshot[2]["action_id"] == act.action_id

    def test_final_sl_confirmation_retains_the_tp_applied_earlier(
            self, monkeypatch):
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
                type=1,
                volume=0.01,
                price_open=4059.61,
                price_current=4058.0,
                sl=4059.61,
                tp=4052.0,
                profit=1.61,
                comment="c2_1",
            )])

        q = PendingQueue()
        act = _make_action(
            label="BE #12345",
            new_sl=4059.61,
            new_tp=None,
        )
        act.applied_tp = 4052.0
        act.attempts = 2
        act.last_retcode = 10009
        q._log_done(act)

        confirmed = events[0]
        assert confirmed[1] == "mt5_modify_confirmed"
        assert confirmed[2]["new_sl"] == 4059.61
        assert confirmed[2]["new_tp"] == 4052.0

    def test_confirmed_modify_retains_actual_ticket_levels_for_auditor(
            self, monkeypatch):
        import journal
        import pending_actions

        monkeypatch.setattr(journal, "event", lambda *a, **kw: None)
        monkeypatch.setattr(pending_actions.time, "time", lambda: 1000.0)
        monkeypatch.setattr(
            pending_actions.mt5,
            "positions_get",
            lambda ticket: [SimpleNamespace(
                ticket=ticket,
                symbol="XAUUSD",
                magic=20260422,
                type=1,
                volume=0.01,
                price_open=4701.0,
                price_current=4699.5,
                sl=4700.25,
                tp=4688.75,
                profit=1.5,
                comment="c2_1",
            )],
        )

        q = PendingQueue()
        act = _make_action(
            label="BE #12345", new_sl=4700.0, new_tp=None)
        act.last_retcode = 10009

        q._log_done(act)

        assert act.signal.sl_by_ticket == {12345: 4700.25}
        assert act.signal.tp_by_ticket == {12345: 4688.75}
        assert snapshot(q, now=1005.0) == [{
            "sig_id": "canal2_1",
            "kind": "MODIFY_SLTP",
            "ticket": 12345,
            "action_id": act.action_id,
            "decision_id": act.decision_id,
            "message_revision_id": act.message_revision_id,
            "action_revision": 0,
            "new_sl": 4700.25,
            "new_tp": 4688.75,
            "age_s": 5.0,
            "attempts": 0,
            "last_retcode": 10009,
            "state": "confirmed_recent",
            "waiting_reason": None,
            "applied_tp": 4688.75,
            "label": "BE #12345",
        }]

        assert snapshot(q, now=1121.0) == []

    def test_log_done_records_modify_position_gone(self, monkeypatch):
        events = []
        import journal
        monkeypatch.setattr(
            journal, "event",
            lambda sig, ev, **fields: events.append((sig, ev, fields)))

        q = PendingQueue()
        act = _make_action(label="BE #12345", new_sl=4700.0)
        act.attempts = 0
        act.last_retcode = 10036
        act.last_preflight_status = "position_gone"
        act.last_preflight_reason = "ticket_not_found"
        q._log_done(act)

        assert len(events) == 1
        assert events[0][0] == "canal2_1"
        assert events[0][1] == "mt5_modify_skipped_position_gone"
        assert events[0][2]["ticket"] == 12345
        assert events[0][2]["action_id"] == act.action_id
        assert events[0][2]["preflight_status"] == "position_gone"
        assert events[0][2]["preflight_reason"] == "ticket_not_found"
        assert events[0][2]["expected_magic"] == act.signal.magic


def test_persistent_provider_instruction_does_not_expire_before_signal_closes():
    action = _make_action(new_sl=4000.0)
    action.timeout_s = 0
    action.persist_until_signal_close = True
    action.created_at = time.time() - 10_000

    assert action.signal.status == "open"
    assert action.expired() is False


def test_exact_be_wait_alerts_once_after_threshold():
    action = _make_action(label="BE #12345 -> 4056.50", new_sl=4056.50)
    action.persist_until_signal_close = True
    action.waiting_reason = "requested_sl_waits_for_market"

    assert _should_alert_waiting_exact_be(
        action, age_s=29.9, threshold_s=30.0) is False
    assert _should_alert_waiting_exact_be(
        action, age_s=30.0, threshold_s=30.0) is True

    action.stops_alerted = True
    assert _should_alert_waiting_exact_be(
        action, age_s=60.0, threshold_s=30.0) is False
