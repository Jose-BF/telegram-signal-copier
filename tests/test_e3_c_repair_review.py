"""Independent regression controls for the E3-C repair review."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import position_lifecycle_monitor as monitor
from durable_entry_execution import DurableEntryExecutor, EntryDispatchResult, EntryDispatchState
from durable_execution import DurableExecutionService
from execution_intents import IntentConflictError, IntentStore
from mt5_protocol import LookupState
from mt5_read_protocol import ReadOperation, ReadRequest
from mt5_runtime import MAX_RUNTIME_SNAPSHOTS, MT5Runtime


class PrepareOnlyClient:
    config = SimpleNamespace(expected_server="demo", expected_login=7)

    def __init__(self, path):
        self.store = IntentStore(path)
        self.requests = []

    async def execute(self, request, **kwargs):
        self.requests.append(request)
        return self.store.prepare_reserved(
            request,
            reservation_key=kwargs["reservation_key"],
            policy_revision=kwargs["policy_revision"],
            expires_utc=kwargs.get("expires_utc"),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [
    {"direction": "SELL"},
    {"volume": 0.01},
    {"magic": 222},
    {"protection_policy": "deferred_explicit"},
])
async def test_retry_rejects_changed_order_semantics(tmp_path, changed):
    client = PrepareOnlyClient(tmp_path / "review.sqlite3")
    adapter = DurableEntryExecutor(DurableExecutionService(client), symbol="XAUUSD")
    original = dict(
        channel="canal1", signal_root="canal1_review", generation=0,
        leg="entry-1", revision=0, direction="BUY", volume=0.04,
        sl=2490.0, tp=None, loss_budget=None, protection_policy="required",
        magic=111, comment="c1_review", action_id="review-action",
    )
    assert (await adapter.open_market(**original)).state is EntryDispatchState.NOT_SENT
    # A quote-derived stop may be frozen; direction/size/ownership cannot silently change.
    with pytest.raises(IntentConflictError):
        await adapter.open_market(**(original | changed))


@pytest.mark.parametrize("params", [
    {"date_from": 1_700_000_000, "date_to": 1_700_000_001, "date_from_msc": 0},
    {"date_from": 1_700_000_000, "date_to": 1_700_000_001, "date_to_msc": 0},
])
def test_tick_range_rejects_partial_second_clock(params):
    with pytest.raises(ValueError):
        ReadRequest(ReadOperation.TICKS_RANGE, {"symbol": "XAUUSD", "flags": -1, **params})


def test_runtime_snapshot_retention_is_bounded_and_keeps_full_positions():
    runtime = MT5Runtime(SimpleNamespace(store=object()))

    def response(value):
        return SimpleNamespace(
            operation=ReadOperation.POSITIONS,
            state=LookupState.FOUND,
            error=None,
            native_error=None,
            worker_session_id="review",
            worker_pid=1,
            completed_monotonic=1.0,
            completed_utc_ns=1,
            value=value,
        )

    runtime._remember(response([{"ticket": 1}]), {})
    for ticket in range(1, MAX_RUNTIME_SNAPSHOTS * 4):
        runtime._remember(response([{"ticket": ticket}]), {"ticket": ticket})

    assert len(runtime._snapshots) <= MAX_RUNTIME_SNAPSHOTS
    assert runtime.snapshot(ReadOperation.POSITIONS)["value"] == [{"ticket": 1}]


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["canal1", "canal2"])
@pytest.mark.parametrize("after_timeout", ["rebound", "expired"])
async def test_late_leg_is_managed_without_new_entry_trigger(
    monkeypatch, channel, after_timeout,
):
    from tests.test_dubai_live_ladder import _candidate_signal
    from tests.test_gold_555_monitor import _signal

    signal = _candidate_signal("BUY")[0] if channel == "canal1" else _signal()
    now = datetime.utcnow()
    signal.candidate_entry_expires_at = now + timedelta(minutes=1)
    level = float(monitor._candidate_entry_plan(signal)[1]["trigger_price"])
    calls = []

    async def durable_open(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return EntryDispatchResult(EntryDispatchState.RECONCILE, "late-leg")
        return EntryDispatchResult(
            EntryDispatchState.CONFIRMED, "late-leg", ticket=777, fill_price=level,
        )

    async def provisional(*args, **kwargs):
        return level - 30.0

    monkeypatch.setattr(monitor, "_durable_entry_executor", SimpleNamespace(open_market=durable_open))
    monkeypatch.setattr(monitor, "_dubai_provisional_basket_stop", provisional)
    monkeypatch.setattr(monitor.executor, "account_evidence", lambda: {
        "trade_mode": 0, "trade_mode_name": "demo", "currency": "EUR",
    })
    monkeypatch.setattr(monitor, "_journal_event", lambda *a, **k: None)
    monkeypatch.setattr(monitor, "_journal_anomaly", lambda *a, **k: None)
    if channel == "canal1":
        async def exact_basket_stop(*_args, **_kwargs):
            return 1

        monkeypatch.setattr(
            "listener._ensure_dubai_candidate_hard_stops",
            exact_basket_stop,
        )
    trigger = SimpleNamespace(bid=level - 0.2, ask=level - 0.1, time_msc=1)
    assert await monitor._process_candidate_entry_tick(signal, trigger, now=now) == 0
    assert len(calls) == 1

    # The durable result becomes available after the wait. It is an existing
    # broker effect, so neither a new price trigger nor entry expiry may hide it.
    signal.candidate_entry_retry_not_before = 0.0
    quote = level + 0.5 if after_timeout == "rebound" else level - 0.1
    next_time = now + timedelta(seconds=2 if after_timeout == "rebound" else 61)
    tick = SimpleNamespace(bid=quote - 0.1, ask=quote, time_msc=2)
    await monitor._process_candidate_entry_tick(signal, tick, now=next_time)
    assert 777 in signal.dca_tickets, (channel, after_timeout, len(calls), signal.dca_tickets)
