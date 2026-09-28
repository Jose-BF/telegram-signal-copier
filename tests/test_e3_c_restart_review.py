"""Offline review regression: durable Gold fill survives its producer process."""

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import gold_555_live_candidate as gold
import listener
import main
import pending_actions
import position_lifecycle_monitor as monitor
import state as state_module
from durable_entry_execution import (
    DurableEntryExecutor,
    EntryDispatchState,
    EntryRecoveryRecord,
)
from durable_execution import DurableExecutionService
from execution_intents import IntentStore
from tests.test_dubai_live_ladder import _candidate_signal
from tests.test_gold_555_monitor import _signal


async def test_restart_applies_gold_done_protection_after_orphan_resync(
    tmp_path, monkeypatch,
):
    now = datetime.now(timezone.utc)
    observed = now - timedelta(seconds=10)
    policy = gold.Gold555Policy()
    message_id, ticket, fill = 384, 701, 2500.25
    ledger = tmp_path / "ledger.sqlite3"
    payload = {
        "symbol": "XAUUSD", "direction": "BUY", "volume": 0.04,
        "sl": 2470.4, "tp": None, "loss_budget": None,
        "protection_policy": "required", "deviation": 30,
        "magic": main.config.magic_for("canal2"),
        "comment": gold.market_comment(message_id),
    }
    # The producer exits after durable DONE, before any Signal or SL/TP spool.
    subprocess.run(
        [sys.executable, "-B", "-c", """
import json, sys
from execution_intents import IntentStore
from mt5_protocol import BrokerRequest, BrokerOutcome, IntentKey, IntentState
store = IntentStore(sys.argv[1])
request = BrokerRequest.create(
    IntentKey('demo/7', 'canal2', 'canal2_384', 0, 'entry-0', 'OPEN_MARKET', 0),
    json.loads(sys.argv[2]),
)
store.prepare_reserved(request, reservation_key='demo/7/canal2/canal2_384/g0/entry-0', policy_revision=0)
store.begin_dispatch(request)
store.record_outcome(request, BrokerOutcome(IntentState.DONE, retcode=10009,
    order=701, price=2500.25, filled_volume=0.04), projection_key='signal:canal2_384')
""", str(ledger), json.dumps(payload)],
        cwd=Path(__file__).resolve().parents[1], check=True,
        capture_output=True, text=True,
    )
    store = IntentStore(ledger)

    async def execute(request, **_kwargs):
        return store.get(request.intent_id)

    service = DurableExecutionService(SimpleNamespace(
        store=store, execute=execute,
        config=SimpleNamespace(expected_server="demo", expected_login=7),
    ))
    adapter = DurableEntryExecutor(service, symbol="XAUUSD")
    recovered = adapter.reconstruct()
    assert len(recovered) == 1
    assert recovered[0].state is EntryDispatchState.CONFIRMED
    assert recovered[0].ticket == ticket

    intent = listener._Canal2EntryIntent(
        message_id=message_id, direction="BUY", parsed={"direction": "BUY"},
        raw_text="XAUUSD BUY NOW", entry_timestamp=observed.replace(tzinfo=None),
        telegram_timestamp=observed, source_kind="telegram_now", command_key="BUY_NOW",
    )
    watch = listener.gold_555_entry_watch.EntryWatch.new(
        "BUY", reference=2500.0, observed_at=observed, policy=policy,
    )
    watch.status = "confirmed"
    watch.confirmed_quote = 2500.4
    watch.confirmed_at = observed
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps({
        "sig": "canal2_384", "ev": "gold_555_entry_watch_confirmed",
        "intent": listener._gold_555_intent_payload(intent), "watch": watch.to_dict(),
    }) + "\n", encoding="utf-8")

    fresh = state_module.StateManager()
    for module in (state_module, listener, main):
        monkeypatch.setattr(module, "state", fresh)
    monkeypatch.setattr(listener, "_gold_555_entry_watches", {})
    monkeypatch.setattr(listener, "_canal2_opening_msg_ids", set())
    monkeypatch.setattr(listener, "_durable_entry_executor", adapter)
    monkeypatch.setattr(listener.executor, "account_evidence", lambda: {
        "trade_mode": 0,
        "trade_mode_name": "demo",
        "currency": "EUR",
        "login": 7,
        "server": "demo",
    })
    monkeypatch.setattr(
        listener.executor.mt5,
        "positions_get",
        lambda **_kwargs: [SimpleNamespace(ticket=ticket)],
    )
    monkeypatch.setattr(main.config, "STRATEGY_C2_GOLD_NOW_555_ENABLED", True)
    monkeypatch.setattr(main.config, "GOLD_555_MAX_PLANNED_LOTS_PER_SIGNAL", 0.16)
    monkeypatch.setattr(main.journal, "EVENTS_FILE", events)
    monkeypatch.setattr(main.journal, "event", lambda *a, **k: None)
    monkeypatch.setattr(main.journal, "anomaly", lambda *a, **k: None)
    monkeypatch.setattr(main, "_assert_dubai_candidate_demo_account", lambda *a, **k: None)
    monkeypatch.setattr(monitor, "start", lambda *a, **k: None)
    monkeypatch.setattr(pending_actions, "queue", pending_actions.PendingQueue(
        spool_path=tmp_path / "empty-spool.json",
    ))
    sl_requests, tp_requests = [], []
    monkeypatch.setattr(pending_actions, "enqueue_modify_sl", lambda s, t, p, **k: sl_requests.append((t, p)))
    monkeypatch.setattr(pending_actions, "enqueue_modify_tp", lambda s, t, p, **k: tp_requests.append((t, p)))
    groups = {"canal2_384": {
        "channel": "canal2", "message_id": message_id, "direction": "BUY",
        "market_ticket": ticket, "market_price": fill, "market_sl": 2470.4,
        "market_tp": None, "market_open_time": int(observed.timestamp()),
        "extra_market_tickets": [], "double_market_tickets": [],
        "scale_out_leg_indexes": {}, "dca_tickets": [],
        "live_strategy_marker": gold.CANDIDATE_ID,
        "position_entries": {ticket: fill}, "position_volumes": {ticket: 0.04},
        "position_stops": {ticket: 2470.4}, "position_targets": {ticket: 0.0},
    }}
    tick = SimpleNamespace(time=int(now.timestamp()), time_msc=int(now.timestamp()*1000), bid=2500.2, ask=2500.4)
    # Preserve production startup ordering: positions, spool, then watches.
    main._resync_orphan_positions(groups=groups, server_tick=tick)
    pending_actions.queue.restore_from_spool(fresh)
    main._restore_live_candidate_runtime(events)
    await listener.process_gold_555_entry_tick(tick, now=now)
    signal = fresh.get("canal2", message_id)
    assert signal is not None and signal.market_ticket == ticket
    assert tp_requests == [(ticket, policy.target_price("BUY", fill, 0))], (
        "Durable DONE survived process exit, but orphan resync suppressed the "
        f"confirmed watch: TP requests={tp_requests}, SL requests={sl_requests}, "
        f"watches={listener._gold_555_entry_watches}, actual TP={signal.tp_by_ticket}"
    )


async def test_restart_recovers_confirmed_delayed_entries_for_both_channels(
    monkeypatch,
):
    runtime_state = state_module.StateManager()
    dubai = _candidate_signal("BUY")[0]
    gold_signal = _signal()
    runtime_state.add(dubai)
    runtime_state.add(gold_signal)
    rows = []
    for signal, ticket, fill in (
        (dubai, 7701, 4195.8),
        (gold_signal, 7702, 4298.4),
    ):
        rows.append(EntryRecoveryRecord(
            intent_id=f"intent-{signal.channel}",
            outcome_revision=1,
            state=EntryDispatchState.CONFIRMED,
            channel=signal.channel,
            signal_root=f"{signal.channel}_{signal.message_id}",
            generation=0,
            leg="candidate-entry-1",
            policy_revision=0,
            payload={
                "symbol": "XAUUSD",
                "direction": "BUY",
                "volume": monitor._candidate_entry_plan(signal)[1]["volume"],
                "magic": signal.magic,
                "protection_policy": "required",
                "comment": (
                    gold.market_comment(signal.message_id, 1)
                    if signal.channel == "canal2"
                    else f"DCA_c1_{signal.message_id}_D1"
                ),
            },
            ticket=ticket,
            fill_price=fill,
            retcode=10009,
        ))
    monkeypatch.setattr(
        monitor,
        "_durable_entry_executor",
        SimpleNamespace(reconstruct=lambda: rows),
    )
    monkeypatch.setattr(
        monitor.executor.mt5,
        "positions_get",
        lambda **kwargs: [SimpleNamespace(ticket=kwargs["ticket"])],
    )
    monkeypatch.setattr(monitor, "_journal_event", lambda *a, **k: None)
    monkeypatch.setattr(monitor, "_journal_anomaly", lambda *a, **k: None)
    gold_sl, gold_tp, dubai_stops = [], [], []
    monkeypatch.setattr(
        monitor.pending_actions,
        "enqueue_modify_sl",
        lambda _s, ticket, price, **_k: gold_sl.append((ticket, price)),
    )
    monkeypatch.setattr(
        monitor.pending_actions,
        "enqueue_modify_tp",
        lambda _s, ticket, price, **_k: gold_tp.append((ticket, price)),
    )

    async def ensure_stops(signal, **kwargs):
        dubai_stops.append((signal, kwargs))

    monkeypatch.setattr(listener, "_ensure_dubai_candidate_hard_stops", ensure_stops)

    assert await monitor.recover_durable_candidate_entries(runtime_state) == 2
    assert dubai.dca_tickets == [7701]
    assert gold_signal.dca_tickets == [7702]
    assert dubai.candidate_filled_leg_indexes == [1]
    assert gold_signal.candidate_filled_leg_indexes == [1]
    assert dubai_stops[0][0] is dubai
    assert gold_sl == [(7702, 4268.4)]
    assert gold_tp == [(7702, 4299.4)]
