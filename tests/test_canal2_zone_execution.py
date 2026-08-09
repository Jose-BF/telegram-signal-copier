from datetime import datetime, timezone
import asyncio
from types import SimpleNamespace
import json

import pytest

import listener
from canal2_zone_lifecycle import new_plan_record
from state import Signal, StateManager


def _plan(message_id=500, *, complete=True):
    parsed = {
        "direction": "BUY",
        "zones": [[4053.0, 4058.0]],
        "target": None,
        "tps": [4060.0, 4062.0] if complete else [],
        "sl": 4050.0 if complete else None,
        "has_open_runner": True,
    }
    record = new_plan_record(
        parsed,
        message_id=message_id,
        root_message_id=message_id,
        raw_text="Gold Buy Zone",
        tg_ts="2026-08-05T09:00:00+00:00",
        source_kind="new",
    )
    record["execution_eligible"] = True
    return record


def _tick(*, bid=4055.0, ask=4055.2, time_msc=1785920400123):
    return {
        "bid": bid,
        "ask": ask,
        "mid": (bid + ask) / 2,
        "spread": ask - bid,
        "time": 1785920400,
        "time_msc": time_msc,
    }


def test_armed_zone_notice_describes_live_waiting_state():
    text = listener._format_canal2_zone_plan_notice(_plan())

    assert "ZONA ARMADA" in text
    assert "primer toque" in text
    assert "simulaci" not in text.lower()


def test_incomplete_zone_notice_explains_why_it_cannot_open():
    text = listener._format_canal2_zone_plan_notice(
        _plan(501, complete=False)
    )

    assert "ZONA REGISTRADA" in text
    assert "faltan" in text.lower()
    assert "no abrira" in text.lower()


@pytest.fixture(autouse=True)
def _reset_zone_runtime(monkeypatch):
    async def fake_notify(_text):
        return None

    listener._canal2_zone_plans.clear()
    listener._entry_execution_gate.reset()
    listener._canal2_opening_msg_ids.clear()
    monkeypatch.setattr(listener, "notify", fake_notify)
    monkeypatch.setattr(listener.journal, "event", lambda *a, **kw: None)
    monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **kw: None)
    yield
    listener._canal2_zone_plans.clear()
    listener._entry_execution_gate.reset()
    listener._canal2_opening_msg_ids.clear()


@pytest.mark.asyncio
async def test_first_touch_opens_once_and_consumes_generation(monkeypatch):
    plan = _plan()
    listener._canal2_zone_plans[500] = plan
    state = StateManager()
    intents = []
    events = []

    async def fake_open(intent, *, label):
        intents.append(intent)
        signal = Signal(
            channel="canal2",
            message_id=intent.message_id,
            direction=intent.direction,
            market_ticket=950001,
            entry_source_kind=intent.source_kind,
        )
        state.add(signal)
        return signal

    monkeypatch.setattr(listener, "state", state)
    monkeypatch.setattr(listener, "_open_canal2_intent", fake_open)
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda signal_id, ev, **kw: events.append((signal_id, ev, kw)),
    )

    assert await listener._process_canal2_zone_tick(_tick()) == 1
    assert await listener._process_canal2_zone_tick(
        _tick(time_msc=1785920400456)
    ) == 0

    assert len(intents) == 1
    assert intents[0].message_id == 500
    assert intents[0].source_kind == "zone_first_touch"
    assert intents[0].trigger["side"] == "ask"
    assert plan["consumed"] is True
    assert plan["status"] == "triggered"
    assert plan["entry_generation"] == 1
    assert state.get("canal2", 500) is not None
    assert any(ev == "canal2_zone_entry_confirmed" for _, ev, _ in events)


@pytest.mark.asyncio
async def test_explicit_activation_opens_outside_zone_and_logs_deviation(
        monkeypatch):
    plan = _plan(510)
    plan["activation_requested"] = True
    listener._canal2_zone_plans[510] = plan
    intents = []

    async def fake_open(intent, *, label):
        intents.append(intent)
        return Signal(
            "canal2",
            intent.message_id,
            intent.direction,
            market_ticket=950010,
        )

    monkeypatch.setattr(listener, "_open_canal2_intent", fake_open)
    monkeypatch.setattr(listener.journal, "event", lambda *a, **kw: None)

    opened = await listener._process_canal2_zone_tick(
        _tick(bid=4063.8, ask=4064.0)
    )

    assert opened == 1
    assert intents[0].source_kind == "zone_explicit_active"
    assert intents[0].trigger["trigger"] == "explicit_active"
    assert intents[0].trigger["outside_zone"] is True
    assert intents[0].trigger["deviation_from_zone"] == pytest.approx(6.0)


@pytest.mark.asyncio
async def test_active_incomplete_waits_then_final_levels_trigger(monkeypatch):
    plan = _plan(520, complete=False)
    listener._canal2_zone_plans[520] = plan
    triggers = []

    async def fake_trigger(plan_arg, trigger, **kwargs):
        triggers.append((plan_arg, trigger, kwargs))
        return None

    monkeypatch.setattr(listener, "_trigger_canal2_zone_entry", fake_trigger)
    monkeypatch.setattr(listener.journal, "event", lambda *a, **kw: None)
    monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **kw: None)

    active = SimpleNamespace(
        id=521,
        text="Active",
        date=datetime.now(timezone.utc),
    )
    await listener._handle_canal2_zone_plan_reply(active, 520, plan)

    assert plan["status"] == "activation_pending"
    assert triggers == []

    levels = SimpleNamespace(
        id=522,
        text=(
            "Gold Buy Zone\n4058 - 4053\nTargets\n"
            "4060\n4062\nOpen\nSL 4050"
        ),
        date=datetime.now(timezone.utc),
    )
    await listener._handle_canal2_zone_plan_reply(levels, 521, plan)

    assert plan["status"] == "armed"
    assert len(triggers) == 1
    assert triggers[0][1]["trigger"] == "explicit_active"


@pytest.mark.asyncio
async def test_incomplete_zone_is_waiting_state_not_runtime_anomaly(
        monkeypatch):
    events = []
    anomalies = []
    msg = SimpleNamespace(
        id=525,
        text="Gold Buy Zone 4058 - 4053",
        date=datetime.now(timezone.utc),
    )

    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda signal_id, ev, **fields:
        events.append((signal_id, ev, fields)),
    )
    monkeypatch.setattr(
        listener.journal,
        "anomaly",
        lambda *args, **fields: anomalies.append((args, fields)),
    )

    await listener._handle_canal2_zone_plan(
        msg,
        msg.text,
        {
            "direction": "BUY",
            "zones": [[4053.0, 4058.0]],
            "target": None,
            "tps": [],
            "sl": None,
            "has_open_runner": True,
        },
    )

    assert anomalies == []
    assert any(
        ev == "canal2_zone_plan_waiting_for_levels"
        for _, ev, _ in events
    )


@pytest.mark.asyncio
async def test_complete_zone_prefix_is_aligned_to_live_market_before_storage(
        monkeypatch):
    events = []
    msg = SimpleNamespace(
        id=526,
        text=(
            "Gold Sell Zone\n4062 - 4067\nTargets\n"
            "4060\n4058\n4047\nSL 4070"
        ),
        date=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(
        listener.executor,
        "current_tick_safe",
        lambda: _tick(bid=4259.8, ask=4260.0),
    )
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda signal_id, ev, **fields:
        events.append((signal_id, ev, fields)),
    )

    await listener._handle_canal2_zone_plan(
        msg,
        msg.text,
        {
            "direction": "SELL",
            "zones": [[4062.0, 4067.0]],
            "target": None,
            "tps": [4060.0, 4058.0, 4047.0],
            "sl": 4070.0,
            "has_open_runner": True,
        },
    )

    stored = listener._canal2_zone_plans[526]
    assert stored["zones"] == [[4262.0, 4267.0]]
    assert stored["tps"] == [4260.0, 4258.0, 4247.0]
    assert stored["sl"] == 4270.0
    correction = next(
        fields
        for _, ev, fields in events
        if ev == "entry_levels_interpreted"
    )
    assert correction["reference_price"] == 4259.8
    assert correction["corrections"][0]["kind"] == "market_context_shift"


@pytest.mark.asyncio
async def test_reentry_uses_new_message_identity(monkeypatch):
    plan = _plan(530)
    plan["consumed"] = True
    plan["status"] = "triggered"
    listener._canal2_zone_plans[530] = plan
    triggers = []

    async def fake_trigger(plan_arg, trigger, **kwargs):
        triggers.append((plan_arg, trigger, kwargs))
        return None

    monkeypatch.setattr(listener, "_trigger_canal2_zone_entry", fake_trigger)
    monkeypatch.setattr(listener.journal, "event", lambda *a, **kw: None)
    monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **kw: None)

    msg = SimpleNamespace(
        id=531,
        text="I am re entering",
        date=datetime.now(timezone.utc),
    )
    await listener._handle_canal2_zone_plan_reply(msg, 530, plan)

    assert len(triggers) == 1
    assert triggers[0][1]["trigger"] == "explicit_reentry"
    assert triggers[0][2]["generation_message_id"] == 531


@pytest.mark.asyncio
async def test_do_not_reenter_blocks_later_reentry(monkeypatch):
    plan = _plan(540)
    plan["consumed"] = True
    plan["status"] = "triggered"
    listener._canal2_zone_plans[540] = plan
    events = []
    triggers = []

    async def fake_trigger(*args, **kwargs):
        triggers.append((args, kwargs))

    monkeypatch.setattr(listener, "_trigger_canal2_zone_entry", fake_trigger)
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig, ev, **kw: events.append((sig, ev, kw)),
    )
    monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **kw: None)

    block = SimpleNamespace(
        id=541,
        text="Do not re-enter",
        date=datetime.now(timezone.utc),
    )
    reentry = SimpleNamespace(
        id=542,
        text="I am re entering",
        date=datetime.now(timezone.utc),
    )
    await listener._handle_canal2_zone_plan_reply(block, 540, plan)
    await listener._handle_canal2_zone_plan_reply(reentry, 541, plan)

    assert triggers == []
    assert plan["no_reentry"] is True
    assert any(ev == "canal2_zone_reentry_blocked" for _, ev, _ in events)


def test_current_tick_safe_includes_broker_clock(monkeypatch):
    tick = SimpleNamespace(
        bid=4055.10,
        ask=4055.30,
        time=1785920400,
        time_msc=1785920400123,
    )
    monkeypatch.setattr(listener.executor.mt5, "symbol_info_tick", lambda _s: tick)

    result = listener.executor.current_tick_safe()

    assert result["time"] == 1785920400
    assert result["time_msc"] == 1785920400123


def test_zone_entry_clock_normalizes_broker_server_offset():
    observed = datetime(2026, 8, 5, 9, 0, 0, 250000, tzinfo=timezone.utc)
    raw_broker = datetime(2026, 8, 5, 12, 0, 0, 200000,
                          tzinfo=timezone.utc)
    trigger = {
        "time": int(raw_broker.timestamp()),
        "time_msc": int(raw_broker.timestamp() * 1000),
        "observed_utc": observed.isoformat(timespec="milliseconds"),
    }

    normalized = listener._zone_entry_timestamp(trigger)

    assert normalized == raw_broker - listener.timedelta(hours=3)
    assert trigger["clock_basis"] == "broker_time_normalized"
    assert trigger["broker_utc_offset_s"] == 10800
    assert trigger["normalized_time_utc"] == normalized.isoformat(
        timespec="milliseconds"
    )


def test_zone_entry_clock_keeps_true_utc_broker_time():
    observed = datetime(2026, 8, 5, 9, 0, 0, 250000, tzinfo=timezone.utc)
    raw_broker = datetime(2026, 8, 5, 9, 0, 0, 200000,
                          tzinfo=timezone.utc)
    trigger = {
        "time_msc": int(raw_broker.timestamp() * 1000),
        "observed_utc": observed.isoformat(timespec="milliseconds"),
    }

    normalized = listener._zone_entry_timestamp(trigger)

    assert normalized == raw_broker
    assert trigger["broker_utc_offset_s"] == 0


def test_zone_activation_notice_is_human_readable():
    plan = _plan(565)
    signal = Signal(
        "canal2",
        565,
        "BUY",
        market_ticket=950065,
        market_fill_price=4056.25,
    )
    signal.extra_market_tickets.append(950066)

    text = listener._format_canal2_zone_activation_notice(
        plan,
        signal,
        {"trigger": "first_touch", "price": 4056.30},
    )

    assert "Gold Signals" in text
    assert "ZONA ACTIVADA" in text
    assert "4056.25" in text
    assert "2 posiciones" in text
    assert "canal2_" not in text


@pytest.mark.asyncio
async def test_management_reply_after_zone_fill_routes_to_live_signal(
        monkeypatch):
    plan = _plan(550)
    plan["consumed"] = True
    plan["status"] = "triggered"
    plan["alias_generation_ids"] = {"550": 550}
    listener._canal2_zone_plans[550] = plan
    runtime_state = StateManager()
    signal = Signal("canal2", 550, "BUY", market_ticket=950050)
    runtime_state.add(signal)
    handled_plans = []
    executions = []

    async def fake_plan_handler(*args, **kwargs):
        handled_plans.append((args, kwargs))

    async def fake_execute(target, classification, **kwargs):
        executions.append((target, classification, kwargs))

    async def fake_classify(_text, *, signal=None):
        return [{"action": "MOVE_SL_TO_BE", "confidence": 1.0}]

    monkeypatch.setattr(listener, "state", runtime_state)
    monkeypatch.setattr(listener, "_handle_canal2_zone_plan_reply", fake_plan_handler)
    monkeypatch.setattr(listener, "_execute_action", fake_execute)
    monkeypatch.setattr(listener, "classify_async", fake_classify)
    monkeypatch.setattr(listener, "_log_telegram_understood", lambda *a, **kw: None)

    msg = SimpleNamespace(
        id=551,
        text="Move SL to BE",
        date=datetime.now(timezone.utc),
        reply_to=SimpleNamespace(reply_to_msg_id=550),
    )
    await listener._process_canal2_new(msg, dedup=False)

    assert handled_plans == []
    assert len(executions) == 1
    assert executions[0][0] is signal


@pytest.mark.asyncio
async def test_live_zone_level_reply_refreshes_reentry_plan_and_alias(
        monkeypatch):
    plan = _plan(555)
    plan["consumed"] = True
    plan["status"] = "triggered"
    plan["alias_generation_ids"] = {"555": 555}
    listener._canal2_zone_plans[555] = plan
    runtime_state = StateManager()
    signal = Signal("canal2", 555, "BUY", market_ticket=950055)
    runtime_state.add(signal)
    events = []
    updates = []

    async def fake_update(target, parsed, **kwargs):
        updates.append((target, parsed, kwargs))

    async def fake_classify(_text, *, signal=None):
        return []

    async def fake_execute(*args, **kwargs):
        return None

    monkeypatch.setattr(listener, "state", runtime_state)
    monkeypatch.setattr(listener, "_update_signal_from_parsed", fake_update)
    monkeypatch.setattr(listener, "classify_async", fake_classify)
    monkeypatch.setattr(listener, "_execute_action", fake_execute)
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig, ev, **kw: events.append((sig, ev, kw)),
    )
    monkeypatch.setattr(listener, "_log_telegram_understood", lambda *a, **kw: None)

    msg = SimpleNamespace(
        id=556,
        text="TP1 4065\nTP2 4067\nSL 4051",
        date=datetime.now(timezone.utc),
        reply_to=SimpleNamespace(reply_to_msg_id=555),
    )
    await listener._process_canal2_new(msg, dedup=False)

    assert plan["tps"] == [4065.0, 4067.0]
    assert plan["sl"] == 4051.0
    assert runtime_state.get("canal2", 556) is signal
    assert updates[0][0] is signal
    assert any(
        ev == "canal2_zone_plan_updated"
        and payload["changed_fields"] == ["sl", "tps", "raw_text", "tg_ts"]
        for _, ev, payload in events
    )


@pytest.mark.asyncio
async def test_live_zone_entry_edit_refreshes_future_reentry_levels(
        monkeypatch):
    plan = _plan(557)
    plan["consumed"] = True
    plan["status"] = "triggered"
    listener._canal2_zone_plans[557] = plan
    runtime_state = StateManager()
    signal = Signal("canal2", 557, "BUY", market_ticket=950057)
    runtime_state.add(signal)
    applied = []

    async def fake_apply(target, parsed, *args, **kwargs):
        applied.append((target, parsed, args, kwargs))

    monkeypatch.setattr(listener, "state", runtime_state)
    monkeypatch.setattr(listener, "_edit_already_seen", lambda *a: False)
    monkeypatch.setattr(
        listener, "_apply_interpreted_entry_levels", fake_apply
    )
    monkeypatch.setattr(listener.journal, "event", lambda *a, **kw: None)
    monkeypatch.setattr(listener, "_log_telegram_understood", lambda *a, **kw: None)

    msg = SimpleNamespace(
        id=557,
        text="TP1 4066\nTP2 4069\nSL 4052",
        date=datetime.now(timezone.utc),
        edit_date=datetime.now(timezone.utc),
        reply_to=None,
    )
    await listener._process_canal2_edit(msg)

    assert plan["tps"] == [4066.0, 4069.0]
    assert plan["sl"] == 4052.0
    assert applied[0][0] is signal


@pytest.mark.asyncio
async def test_lifecycle_reply_after_zone_fill_still_updates_plan(monkeypatch):
    plan = _plan(560)
    plan["consumed"] = True
    plan["status"] = "triggered"
    plan["alias_generation_ids"] = {"560": 560}
    listener._canal2_zone_plans[560] = plan
    runtime_state = StateManager()
    runtime_state.add(Signal("canal2", 560, "BUY", market_ticket=950060))
    handled_plans = []

    async def fake_plan_handler(*args, **kwargs):
        handled_plans.append((args, kwargs))

    monkeypatch.setattr(listener, "state", runtime_state)
    monkeypatch.setattr(listener, "_handle_canal2_zone_plan_reply", fake_plan_handler)

    msg = SimpleNamespace(
        id=561,
        text="Do not re-enter",
        date=datetime.now(timezone.utc),
        reply_to=SimpleNamespace(reply_to_msg_id=560),
    )
    await listener._process_canal2_new(msg, dedup=False)

    assert len(handled_plans) == 1


def test_restart_restores_triggered_plan_and_binds_reply_alias(
        monkeypatch, tmp_path):
    path = tmp_path / "trade_events.jsonl"
    rows = [
        {
            "ts": "2026-08-05T09:00:00+00:00",
            "sig": "canal2_570",
            "ev": "canal2_zone_plan_created",
            "lifecycle_schema_version": 2,
            "message_id": 570,
            "thread_root_message_id": 570,
            "direction": "BUY",
            "zones": [[4053.0, 4058.0]],
            "tps": [4060.0, 4062.0],
            "sl": 4050.0,
            "status": "armed",
            "expires_utc": "2099-08-06T09:00:00+00:00",
        },
        {
            "ts": "2026-08-05T09:01:00+00:00",
            "sig": "canal2_571",
            "ev": "canal2_zone_plan_alias_registered",
            "lifecycle_schema_version": 2,
            "zone_plan_message_id": 570,
            "alias_message_id": 571,
        },
        {
            "ts": "2026-08-05T09:02:00+00:00",
            "sig": "canal2_570",
            "ev": "canal2_zone_entry_confirmed",
            "lifecycle_schema_version": 2,
            "zone_plan_message_id": 570,
            "entry_generation": 1,
            "entry_generation_id": 570,
            "confirmed_generation_ids": [570],
            "alias_generation_ids": {"570": 570, "571": 570},
            "last_trigger": {"trigger": "first_touch"},
        },
    ]
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    runtime_state = StateManager()
    signal = Signal("canal2", 570, "BUY", market_ticket=950070)
    runtime_state.add(signal)
    monkeypatch.setattr(listener, "state", runtime_state)

    restored = listener.restore_canal2_zone_plans_from_journal(path)

    assert restored == 1
    assert listener._canal2_zone_plans[570] is (
        listener._canal2_zone_plans[571]
    )
    assert listener._canal2_zone_plans[570]["status"] == "triggered"
    assert runtime_state.get("canal2", 571) is signal


def test_restart_recovers_zone_fill_when_confirmation_event_was_lost(
        monkeypatch, tmp_path):
    path = tmp_path / "trade_events.jsonl"
    rows = [
        {
            "ts": "2026-08-05T09:00:00+00:00",
            "sig": "canal2_575",
            "ev": "canal2_zone_plan_created",
            "lifecycle_schema_version": 2,
            "message_id": 575,
            "thread_root_message_id": 575,
            "direction": "SELL",
            "zones": [[4181.0, 4187.0]],
            "tps": [4179.0, 4177.0],
            "sl": 4190.0,
            "status": "armed",
            "expires_utc": "2099-08-06T09:00:00+00:00",
        },
        {
            "ts": "2026-08-05T09:01:00+00:00",
            "sig": "canal2_575",
            "ev": "signal_received",
            "lifecycle_schema_version": 2,
            "entry_source_kind": "zone_first_touch",
            "zone_plan_message_id": 575,
            "zone_entry_generation": 1,
            "zone_trigger_kind": "first_touch",
        },
        {
            "ts": "2026-08-05T09:01:01+00:00",
            "sig": "canal2_575",
            "ev": "market_filled",
            "lifecycle_schema_version": 2,
            "entry_source_kind": "zone_first_touch",
            "ticket": 950075,
            "price": 4181.15,
        },
    ]
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    runtime_state = StateManager()
    signal = Signal("canal2", 575, "SELL", market_ticket=950075)
    runtime_state.add(signal)
    monkeypatch.setattr(listener, "state", runtime_state)

    restored = listener.restore_canal2_zone_plans_from_journal(path)

    assert restored == 1
    restored_plan = listener._canal2_zone_plans[575]
    assert restored_plan["status"] == "triggered"
    assert restored_plan["consumed"] is True
    assert restored_plan["confirmed_generation_ids"] == [575]


@pytest.mark.asyncio
async def test_failed_fill_keeps_plan_armed_for_next_fresh_tick(monkeypatch):
    plan = _plan(580)
    listener._canal2_zone_plans[580] = plan
    runtime_state = StateManager()
    attempts = []

    async def fake_open(intent, *, label):
        attempts.append(intent)
        if len(attempts) == 1:
            return None
        signal = Signal("canal2", 580, "BUY", market_ticket=950080)
        runtime_state.add(signal)
        return signal

    monkeypatch.setattr(listener, "state", runtime_state)
    monkeypatch.setattr(listener, "_open_canal2_intent", fake_open)
    monkeypatch.setattr(listener.journal, "event", lambda *a, **kw: None)

    assert await listener._process_canal2_zone_tick(_tick()) == 0
    assert plan["consumed"] is False
    assert plan["trigger_claim"] is None

    assert await listener._process_canal2_zone_tick(
        _tick(time_msc=1785920400456)
    ) == 1
    assert len(attempts) == 2
    assert plan["consumed"] is True


@pytest.mark.asyncio
async def test_expired_or_multi_zone_plan_never_opens(monkeypatch):
    expired = _plan(590)
    expired["expires_utc"] = "2020-01-01T00:00:00+00:00"
    multi = _plan(591)
    multi["zones"] = [[4053.0, 4058.0], [4048.0, 4050.0]]
    listener._canal2_zone_plans[590] = expired
    listener._canal2_zone_plans[591] = multi
    intents = []

    async def fake_open(intent, *, label):
        intents.append(intent)

    monkeypatch.setattr(listener, "_open_canal2_intent", fake_open)
    monkeypatch.setattr(listener.journal, "event", lambda *a, **kw: None)

    assert await listener._process_canal2_zone_tick(_tick()) == 0
    assert intents == []
    assert expired["status"] == "expired"
    assert multi["consumed"] is False


@pytest.mark.asyncio
async def test_concurrent_touch_evaluation_opens_one_generation(monkeypatch):
    plan = _plan(600)
    listener._canal2_zone_plans[600] = plan
    runtime_state = StateManager()
    attempts = []

    async def fake_open(intent, *, label):
        attempts.append(intent)
        await asyncio.sleep(0)
        signal = Signal("canal2", 600, "BUY", market_ticket=950090)
        runtime_state.add(signal)
        return signal

    monkeypatch.setattr(listener, "state", runtime_state)
    monkeypatch.setattr(listener, "_open_canal2_intent", fake_open)
    monkeypatch.setattr(listener.journal, "event", lambda *a, **kw: None)

    results = await asyncio.gather(
        listener._process_canal2_zone_tick(_tick()),
        listener._process_canal2_zone_tick(_tick()),
    )

    assert sum(results) == 1
    assert len(attempts) == 1
