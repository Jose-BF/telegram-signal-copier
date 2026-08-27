from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import listener
import strategy_shadow_runtime
from strategy_shadow_contracts import ShadowManagementEvent


class FakeRuntime:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.registrations = []
        self.management = []

    async def register_signal(self, **fields):
        if self.fail:
            raise RuntimeError("injected registration failure")
        self.registrations.append(fields)
        return (SimpleNamespace(candidate_id="candidate"),)

    async def process_management(self, event):
        if self.fail:
            raise RuntimeError("injected management failure")
        self.management.append(event)
        return ()


@pytest.fixture(autouse=True)
def clear_shadow_runtime():
    strategy_shadow_runtime.install_runtime(None)
    yield
    strategy_shadow_runtime.install_runtime(None)


@pytest.mark.asyncio
async def test_listener_registers_dubai_entry_with_causal_cursor(monkeypatch):
    runtime = FakeRuntime()
    strategy_shadow_runtime.install_runtime(runtime)
    monkeypatch.setattr(listener.config, "STRATEGY_SHADOW_ENABLED", True)
    observed = datetime(2026, 8, 27, 8, 0, tzinfo=timezone.utc)

    await listener._shadow_register_accepted_entry(
        channel="canal1",
        message_id=20700,
        direction="BUY",
        observed_at=observed,
        source_kind="telegram_signal",
        tick=None,
    )

    assert len(runtime.registrations) == 1
    registration = runtime.registrations[0]
    assert registration["signal_id"] == "canal1_20700"
    assert registration["registered_tick_msc"] == 1787817600000
    assert registration["registered_at_utc"] == observed.isoformat()


@pytest.mark.asyncio
async def test_listener_registers_gold_now_but_excludes_zone_plans(monkeypatch):
    runtime = FakeRuntime()
    strategy_shadow_runtime.install_runtime(runtime)
    monkeypatch.setattr(listener.config, "STRATEGY_SHADOW_ENABLED", True)
    observed = datetime(2026, 8, 27, 8, 0, tzinfo=timezone.utc)
    broker_tick = {"time_msc": 1234, "bid": 4300.0, "ask": 4300.2}

    excluded = await listener._shadow_register_accepted_entry(
        channel="canal2",
        message_id=379,
        direction="BUY",
        observed_at=observed,
        source_kind="zone_first_touch",
        tick=broker_tick,
        reference_price=4300.2,
    )
    included = await listener._shadow_register_accepted_entry(
        channel="canal2",
        message_id=380,
        direction="BUY",
        observed_at=observed,
        source_kind="telegram_now",
        tick=broker_tick,
        reference_price=4300.2,
    )

    assert excluded == ()
    assert included
    assert [row["source_message_id"] for row in runtime.registrations] == [380]
    assert runtime.registrations[0]["registered_tick_msc"] == 1234


@pytest.mark.asyncio
async def test_listener_shadow_failure_never_escapes_live_path(monkeypatch):
    runtime = FakeRuntime(fail=True)
    strategy_shadow_runtime.install_runtime(runtime)
    monkeypatch.setattr(listener.config, "STRATEGY_SHADOW_ENABLED", True)
    events = []
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda signal_id, event, **fields: events.append(
            (signal_id, event, fields)
        ),
    )

    result = await listener._shadow_register_accepted_entry(
        channel="canal1",
        message_id=20700,
        direction="BUY",
        observed_at=datetime.now(timezone.utc),
        source_kind="telegram_signal",
        tick=None,
    )

    assert result == ()
    assert events[0][1] == "strategy_shadow_bridge_error"


@pytest.mark.asyncio
async def test_resolved_management_is_fanned_out_with_stable_identity(monkeypatch):
    runtime = FakeRuntime()
    strategy_shadow_runtime.install_runtime(runtime)
    monkeypatch.setattr(listener.config, "STRATEGY_SHADOW_ENABLED", True)
    signal = SimpleNamespace(channel="canal1", message_id=20700)
    classification = {
        "action": "MOVE_SL_TO_PRICE",
        "price": 4298.5,
        "confidence": 0.95,
    }

    await listener._shadow_observe_resolved_management(
        signal,
        classification,
        raw_text="Move SL to 4298.5",
        tg_ts="2026-08-27T08:01:00+00:00",
    )
    await listener._shadow_observe_resolved_management(
        signal,
        classification,
        raw_text="Move SL to 4298.5",
        tg_ts="2026-08-27T08:01:00+00:00",
    )

    assert len(runtime.management) == 2
    first, second = runtime.management
    assert isinstance(first, ShadowManagementEvent)
    assert first.signal_id == "canal1_20700"
    assert first.event_id == second.event_id
    assert first.price == 4298.5


@pytest.mark.asyncio
async def test_unresolved_live_reply_still_reaches_existing_shadow(monkeypatch):
    runtime = FakeRuntime()
    strategy_shadow_runtime.install_runtime(runtime)
    monkeypatch.setattr(listener.config, "STRATEGY_SHADOW_ENABLED", True)

    await listener._shadow_observe_unresolved_management(
        channel="canal2",
        reply_id=380,
        classifications=[
            {"action": "CLOSE_ALL", "confidence": 0.95},
            {"action": "MOVE_SL_TO_BE", "confidence": 0.40},
        ],
        raw_text="Close all now",
        tg_ts="2026-08-27T08:01:00+00:00",
    )

    assert len(runtime.management) == 1
    assert runtime.management[0].signal_id == "canal2_380"
    assert runtime.management[0].action == "CLOSE_ALL"


def test_shadow_modules_have_no_live_execution_dependency():
    root = Path(__file__).resolve().parents[1]
    modules = (
        "strategy_shadow_contracts.py",
        "strategy_shadow_catalog.py",
        "strategy_shadow_engine.py",
        "strategy_shadow_runtime.py",
    )
    forbidden_modules = {"executor", "pending_actions", "MetaTrader5"}
    for name in modules:
        source = (root / name).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=name)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert not imported.intersection(forbidden_modules), name
        assert "order_send" not in source, name
