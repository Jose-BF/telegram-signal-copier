from datetime import datetime, timezone
from types import SimpleNamespace

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


@pytest.fixture(autouse=True)
def _reset_zone_runtime():
    listener._canal2_zone_plans.clear()
    listener._entry_execution_gate.reset()
    listener._canal2_opening_msg_ids.clear()
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
        lambda sig, ev, **kw: events.append((sig, ev, kw)),
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
