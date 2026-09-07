from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import threading
from types import SimpleNamespace

import pytest

import listener
import runtime_control
from canal2_zone_lifecycle import new_plan_record


NOW = datetime(2026, 9, 8, 9, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolated_pending_runtime(tmp_path, monkeypatch):
    listener._gold_555_entry_watches.clear()
    listener._canal2_zone_plans.clear()
    listener._pending_entry_processor_tasks.clear()
    monkeypatch.setattr(runtime_control, "PAUSE_FILE", tmp_path / "pause.json")
    monkeypatch.setattr(
        runtime_control,
        "ACTIVITY_FILE",
        tmp_path / "activity.json",
    )
    monkeypatch.setattr(runtime_control, "_active_handlers", 0)
    monkeypatch.setattr(listener.journal, "event", lambda *args, **kwargs: None)
    monkeypatch.setattr(listener.journal, "anomaly", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        listener.config,
        "STRATEGY_C2_ZONE_FIRST_TOUCH_EXECUTION_ENABLED",
        True,
        raising=False,
    )
    yield
    listener._gold_555_entry_watches.clear()
    listener._canal2_zone_plans.clear()
    assert listener._pending_entry_processor_tasks == set()
    runtime_control.clear_for_spawn()


def _gold_record(
    message_id: int,
    *,
    status: str = "waiting",
    expired: bool = False,
) -> listener._Gold555PendingEntry:
    observed_at = NOW if not expired else NOW - timedelta(days=2)
    watch = listener.gold_555_entry_watch.EntryWatch.new(
        "BUY",
        reference=4300.0,
        observed_at=observed_at,
    )
    if not expired:
        watch.expires_at = datetime(2099, 1, 1, tzinfo=timezone.utc)
    watch.status = status
    if status == "confirmed":
        watch.confirmed_quote = 4300.4
        watch.confirmed_at = NOW
    intent = listener._Canal2EntryIntent(
        message_id=message_id,
        direction="BUY",
        parsed={"direction": "BUY"},
        raw_text="XAUUSD BUY NOW",
        entry_timestamp=NOW.replace(tzinfo=None),
        telegram_timestamp=NOW,
        source_kind="telegram_now",
    )
    return listener._Gold555PendingEntry(intent=intent, watch=watch)


def _zone_plan(message_id: int) -> dict:
    plan = new_plan_record(
        {
            "direction": "BUY",
            "zones": [[4299.0, 4301.0]],
            "tps": [4305.0],
            "sl": 4295.0,
        },
        message_id=message_id,
        root_message_id=message_id,
        raw_text="Gold Buy Zone",
        tg_ts=NOW.isoformat(),
        source_kind="new",
        now_utc=NOW,
    )
    plan["expires_utc"] = "2099-01-01T00:00:00+00:00"
    plan["execution_eligible"] = True
    return plan


def _tick(time_msc: int = 1) -> dict:
    return {
        "bid": 4299.8,
        "ask": 4300.0,
        "time": 1788858000,
        "time_msc": time_msc,
    }


def _install_pending(kind: str, monkeypatch, callback) -> None:
    if kind == "gold":
        listener._gold_555_entry_watches[101] = _gold_record(
            101,
            status="confirmed",
        )
        monkeypatch.setattr(
            listener,
            "_open_gold_555_confirmed_intent",
            callback,
        )
        return

    plan = _zone_plan(201)
    plan["activation_requested"] = True
    listener._canal2_zone_plans[201] = plan
    monkeypatch.setattr(listener, "_trigger_canal2_zone_entry", callback)


async def _run_processor(kind: str) -> int:
    if kind == "gold":
        return await listener.process_gold_555_entry_tick(_tick(), now=NOW)
    return await listener._process_canal2_zone_tick(_tick())


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["gold", "zone"])
async def test_pause_denies_background_entry_at_processor_boundary(
    monkeypatch,
    kind,
):
    opened = []

    async def fake_open(*args, **kwargs):
        opened.append((args, kwargs))
        return object()

    _install_pending(kind, monkeypatch, fake_open)
    runtime_control.request_pause("watcher_restart")

    assert await _run_processor(kind) == 0
    assert opened == []
    assert runtime_control.read_activity() == {}
    assert runtime_control._active_handlers == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["gold", "zone"])
@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
async def test_background_entry_remains_busy_until_await_finishes(
    monkeypatch,
    kind,
    outcome,
):
    entered = asyncio.Event()
    release = asyncio.Event()
    native_release = threading.Event()
    internal_completed = asyncio.Event()

    async def blocking_open(*args, **kwargs):
        if outcome == "cancel":
            loop = asyncio.get_running_loop()

            def native_open():
                loop.call_soon_threadsafe(entered.set)
                native_release.wait(timeout=2)
                return object()

            result = await listener._run(native_open)
            internal_completed.set()
            return result
        entered.set()
        await release.wait()
        if outcome == "error":
            raise RuntimeError("opening failed")
        return object()

    _install_pending(kind, monkeypatch, blocking_open)
    task = asyncio.create_task(_run_processor(kind))
    await asyncio.wait_for(entered.wait(), timeout=1)
    snapshot = runtime_control.read_activity()
    assert runtime_control.active_handler_count(snapshot["pid"]) == 1

    if outcome == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert runtime_control.active_handler_count(snapshot["pid"]) == 1
        native_release.set()
        await asyncio.wait_for(internal_completed.wait(), timeout=1)
        while listener._pending_entry_processor_tasks:
            await asyncio.sleep(0)
    else:
        release.set()
        if outcome == "error":
            with pytest.raises(RuntimeError, match="opening failed"):
                await task
        else:
            assert await task == 1

    assert runtime_control.active_handler_count(snapshot["pid"]) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["gold", "zone"])
async def test_paused_loop_retries_same_tick_after_resume(monkeypatch, kind):
    tick_reads = 0
    opened = asyncio.Event()

    async def stop_after_open(*args, **kwargs):
        opened.set()
        raise asyncio.CancelledError

    _install_pending(kind, monkeypatch, stop_after_open)
    runtime_control.request_pause("watcher_restart")

    if kind == "gold":
        async def fake_to_thread(function, *args, **kwargs):
            nonlocal tick_reads
            tick_reads += 1
            if tick_reads == 2:
                runtime_control.clear_pause()
            return _tick()

        monkeypatch.setattr(listener.asyncio, "to_thread", fake_to_thread)
        loop = listener.gold_555_entry_watch_loop(interval_s=0.01)
    else:
        async def fake_run(function, *args, **kwargs):
            nonlocal tick_reads
            tick_reads += 1
            if tick_reads == 2:
                runtime_control.clear_pause()
            return _tick()

        monkeypatch.setattr(listener, "_run", fake_run)
        loop = listener.canal2_zone_touch_loop(interval_s=0.01)

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(loop, timeout=1)

    assert opened.is_set()
    assert tick_reads == 2
    assert runtime_control._active_handlers == 0


def test_pending_entry_count_matches_gold_watch_execution_capability():
    waiting = _gold_record(101)
    confirmed_expired = _gold_record(102, status="confirmed", expired=True)
    listener._gold_555_entry_watches.update({
        101: waiting,
        1001: waiting,
        102: confirmed_expired,
        1002: confirmed_expired,
        103: _gold_record(103, expired=True),
        104: _gold_record(104, status="expired"),
        105: _gold_record(105, status="cancelled"),
    })

    assert listener.pending_entry_count() == 2


def test_pending_entry_count_matches_zone_execution_capability(monkeypatch):
    first_touch = _zone_plan(201)
    first_touch_alias = 1201
    explicit_activation = _zone_plan(202)
    explicit_activation["activation_requested"] = True
    consumed = _zone_plan(203)
    consumed["consumed"] = True
    ineligible = _zone_plan(204)
    ineligible["activation_requested"] = True
    ineligible["execution_eligible"] = False
    incomplete = _zone_plan(205)
    incomplete["activation_requested"] = True
    incomplete["tps"] = []
    expired = _zone_plan(206)
    expired["activation_requested"] = True
    expired["expires_utc"] = "2020-01-01T00:00:00+00:00"
    claimed = _zone_plan(207)
    claimed["activation_requested"] = True
    claimed["trigger_claim"] = "already-opening"
    listener._canal2_zone_plans.update({
        201: first_touch,
        first_touch_alias: first_touch,
        202: explicit_activation,
        203: consumed,
        204: ineligible,
        205: incomplete,
        206: expired,
        207: claimed,
    })

    assert listener.pending_entry_count() == 2

    monkeypatch.setattr(
        listener.config,
        "STRATEGY_C2_ZONE_FIRST_TOUCH_EXECUTION_ENABLED",
        False,
    )
    assert listener.pending_entry_count() == 1


def test_triggered_but_unconsumed_explicit_activation_still_blocks_restart():
    plan = _zone_plan(208)
    plan.update(status="triggered", consumed=False, activation_requested=True)
    listener._canal2_zone_plans[208] = plan

    assert listener.pending_entry_count() == 1


@pytest.mark.parametrize(
    "malformed",
    [
        SimpleNamespace(watch=SimpleNamespace(status="unknown")),
        {"status": "armed"},
    ],
)
def test_pending_entry_count_raises_for_malformed_runtime_state(malformed):
    if isinstance(malformed, dict):
        listener._canal2_zone_plans[201] = malformed
    else:
        listener._gold_555_entry_watches[101] = malformed

    with pytest.raises((AttributeError, TypeError, ValueError)):
        listener.pending_entry_count()
