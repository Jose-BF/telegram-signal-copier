import asyncio
from datetime import datetime

import pytest

import config
import listener
from state import StateManager


@pytest.fixture(autouse=True)
def _reset_entry_gate():
    listener._entry_execution_gate.reset()
    listener._canal2_opening_msg_ids.clear()
    yield
    listener._entry_execution_gate.reset()
    listener._canal2_opening_msg_ids.clear()


def _intent(message_id, source_kind, trigger=None):
    return listener._Canal2EntryIntent(
        message_id=message_id,
        direction="BUY",
        parsed={
            "direction": "BUY",
            "range": (4053.0, 4058.0),
            "tps": [4060.0, 4062.0],
            "sl": 4050.0,
        },
        raw_text="Gold Buy Zone",
        entry_timestamp=datetime(2026, 8, 5, 9, 0, 0),
        telegram_timestamp=datetime(2026, 8, 5, 8, 59, 59),
        source_kind=source_kind,
        trigger=trigger or {},
        lot_multiplier=1.0,
        max_tp_index=None,
    )


def _patch_opening_runtime(monkeypatch, *, pause_first_await=False):
    state = StateManager()
    orders = []
    events = []
    entered_context = asyncio.Event()
    release_context = asyncio.Event()

    async def fake_run(fn, *args):
        if pause_first_await and fn is listener.compute_market_context:
            entered_context.set()
            await release_context.wait()
        return fn(*args)

    def fake_open(direction, lot, sl, tp, comment, magic):
        ticket = 900000 + len(orders)
        orders.append({
            "direction": direction,
            "lot": lot,
            "sl": sl,
            "tp": tp,
            "comment": comment,
            "magic": magic,
        })
        return ticket, 4056.25

    async def fake_apply(sig, parsed, channel, reference_price=None,
                         tg_ts=None):
        sig.range_low, sig.range_high = parsed.get("range", (None, None))
        sig.tps = list(parsed.get("tps") or [])
        sig.sl = parsed.get("sl")
        return parsed

    monkeypatch.setattr(listener, "state", state)
    monkeypatch.setattr(
        config,
        "STRATEGY_C2_GOLD_NOW_C490_ENABLED",
        False,
    )
    monkeypatch.setattr(listener, "_run", fake_run)
    monkeypatch.setattr(listener, "compute_market_context", lambda _symbol: None)
    monkeypatch.setattr(
        listener.executor,
        "current_tick_safe",
        lambda: {
            "bid": 4056.10,
            "ask": 4056.30,
            "mid": 4056.20,
            "spread": 0.20,
            "time": 1785920400,
            "time_msc": 1785920400123,
        },
    )
    monkeypatch.setattr(
        listener.executor,
        "account_evidence",
        lambda: {
            "trade_mode": 0,
            "trade_mode_name": "demo",
            "currency": "EUR",
        },
    )
    monkeypatch.setattr(
        listener.executor,
        "open_market_with_fill",
        fake_open,
    )
    monkeypatch.setattr(listener, "_open_extra_legs", lambda *a: _noop())
    monkeypatch.setattr(listener, "_apply_interpreted_entry_levels", fake_apply)
    monkeypatch.setattr(listener, "_emit_same_direction_overlap_anomaly",
                        lambda _sig: None)
    monkeypatch.setattr(listener, "_log_strategy_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(listener.logger, "log_signal", lambda *a, **k: None)
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig, ev, **kw: events.append((sig, ev, kw)),
    )
    monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **kw: None)
    monkeypatch.setattr(listener.journal, "begin_trade", lambda *a, **kw: None)
    return state, orders, events, entered_context, release_context


async def _noop():
    return None


@pytest.mark.asyncio
async def test_now_and_zone_intents_share_one_market_opening_path(monkeypatch):
    state, orders, events, _, _ = _patch_opening_runtime(monkeypatch)

    now_signal = await listener._open_canal2_intent(
        _intent(801, "telegram_now"),
        label="Canal2",
    )
    zone_signal = await listener._open_canal2_intent(
        _intent(
            802,
            "zone_first_touch",
            trigger={
                "trigger": "first_touch",
                "side": "ask",
                "price": 4056.30,
                "time_msc": 1785920400123,
                "zone": [4053.0, 4058.0],
            },
        ),
        label="Canal2_zone",
    )

    assert now_signal is state.get("canal2", 801)
    assert zone_signal is state.get("canal2", 802)
    assert [order["comment"] for order in orders] == ["c2_801", "c2_802"]
    assert all(order["lot"] == config.LOT_SIZE for order in orders)
    assert all(order["magic"] == config.magic_for("canal2") for order in orders)
    assert orders[0]["sl"] == orders[1]["sl"] == 4050.0
    assert orders[0]["tp"] == orders[1]["tp"] == 4060.0
    assert zone_signal.entry_source_kind == "zone_first_touch"
    assert zone_signal.zone_trigger_price == 4056.30
    zone_received = [
        payload for sig, ev, payload in events
        if sig == "canal2_802" and ev == "signal_received"
    ]
    assert zone_received[0]["entry_source_kind"] == "zone_first_touch"
    assert zone_received[0]["zone_trigger_time_msc"] == 1785920400123


@pytest.mark.asyncio
async def test_same_intent_identity_cannot_open_twice_concurrently(monkeypatch):
    state, orders, events, entered, release = _patch_opening_runtime(
        monkeypatch,
        pause_first_await=True,
    )
    intent = _intent(803, "zone_first_touch")

    first = asyncio.create_task(
        listener._open_canal2_intent(intent, label="first")
    )
    await entered.wait()
    second = asyncio.create_task(
        listener._open_canal2_intent(intent, label="second")
    )
    await asyncio.sleep(0)
    release.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert sum(result is not None for result in (first_result, second_result)) == 1
    assert len(orders) == 1
    assert state.get("canal2", 803) is not None
    assert any(
        ev == "canal2_entry_open_already_claimed"
        for _, ev, _ in events
    )


@pytest.mark.asyncio
async def test_gold_now_candidate_opens_with_broker_sl_and_no_tp(monkeypatch):
    state, orders, events, _, _ = _patch_opening_runtime(monkeypatch)
    monkeypatch.setattr(
        config,
        "STRATEGY_C2_GOLD_NOW_C490_ENABLED",
        True,
    )
    hard_stop_checks = []
    monitor_starts = []

    monkeypatch.setattr(
        listener.executor,
        "loss_stop_price",
        lambda direction, volume, entry, budget, symbol=None: 4036.3,
    )

    async def fake_ensure(signal, *, force=False):
        hard_stop_checks.append((signal, force))
        return 1

    async def fake_place_monitor(signal):
        monitor_starts.append(signal)

    monkeypatch.setattr(
        listener,
        "_ensure_gold_candidate_hard_stops",
        fake_ensure,
    )
    monkeypatch.setattr(listener, "_place_dca", fake_place_monitor)

    signal = await listener._open_canal2_intent(
        _intent(804, "telegram_now"),
        label="Canal2",
    )

    assert signal is state.get("canal2", 804)
    assert signal.live_strategy_id == "gold_now_c490_v1"
    assert signal.candidate_provisional_sl == 4036.3
    assert orders == [{
        "direction": "BUY",
        "lot": 0.01,
        "sl": 4036.3,
        "tp": None,
        "comment": "c2_804_gv1",
        "magic": config.magic_for("canal2"),
    }]
    assert hard_stop_checks == [(signal, True)]
    assert monitor_starts == [signal]
    received = [
        payload for sig, ev, payload in events
        if sig == "canal2_804" and ev == "signal_received"
    ][0]
    assert received["effective_lot"] == 0.01


@pytest.mark.asyncio
async def test_gold_now_refuses_account_drift_before_submitting_order(
        monkeypatch):
    _state, orders, _events, _, _ = _patch_opening_runtime(monkeypatch)
    monkeypatch.setattr(
        config,
        "STRATEGY_C2_GOLD_NOW_C490_ENABLED",
        True,
    )
    monkeypatch.setattr(
        listener.executor,
        "account_evidence",
        lambda: {
            "trade_mode": 2,
            "trade_mode_name": "real",
            "currency": "EUR",
        },
    )

    with pytest.raises(RuntimeError, match="demo EUR"):
        await listener._open_canal2_intent(
            _intent(805, "telegram_now"),
            label="Canal2",
        )

    assert orders == []


@pytest.mark.asyncio
async def test_gold_monitor_starts_before_extra_leg_setup_can_fail(monkeypatch):
    state, _orders, _events, _, _ = _patch_opening_runtime(monkeypatch)
    monkeypatch.setattr(
        config,
        "STRATEGY_C2_GOLD_NOW_C490_ENABLED",
        True,
    )
    monkeypatch.setattr(
        listener.executor,
        "loss_stop_price",
        lambda direction, volume, entry, budget, symbol=None: 4036.3,
    )
    monitor_starts = []

    async def fake_place_monitor(signal):
        monitor_starts.append(signal)

    async def fail_extra_legs(*_args, **_kwargs):
        raise RuntimeError("extra-leg setup failed")

    monkeypatch.setattr(listener, "_place_dca", fake_place_monitor)
    monkeypatch.setattr(listener, "_open_extra_legs", fail_extra_legs)

    with pytest.raises(RuntimeError, match="extra-leg setup failed"):
        await listener._open_canal2_intent(
            _intent(806, "telegram_now"),
            label="Canal2",
        )

    signal = state.get("canal2", 806)
    assert signal is not None
    assert monitor_starts == [signal]


@pytest.mark.asyncio
async def test_gold_post_fill_tick_telemetry_failure_keeps_protection_alive(
        monkeypatch):
    state, _orders, events, _, _ = _patch_opening_runtime(monkeypatch)
    monkeypatch.setattr(
        config,
        "STRATEGY_C2_GOLD_NOW_C490_ENABLED",
        True,
    )
    monkeypatch.setattr(
        listener.executor,
        "loss_stop_price",
        lambda direction, volume, entry, budget, symbol=None: 4036.3,
    )
    tick_calls = 0

    def flaky_tick():
        nonlocal tick_calls
        tick_calls += 1
        if tick_calls == 1:
            return {
                "bid": 4056.10,
                "ask": 4056.30,
                "mid": 4056.20,
                "spread": 0.20,
            }
        raise RuntimeError("post-fill tick unavailable")

    monitor_starts = []

    async def fake_place_monitor(signal):
        monitor_starts.append(signal)

    async def fake_ensure(_signal, *, force=False):
        return 0

    monkeypatch.setattr(listener.executor, "current_tick_safe", flaky_tick)
    monkeypatch.setattr(listener, "_place_dca", fake_place_monitor)
    monkeypatch.setattr(
        listener,
        "_ensure_gold_candidate_hard_stops",
        fake_ensure,
    )

    signal = await listener._open_canal2_intent(
        _intent(807, "telegram_now"),
        label="Canal2",
    )

    assert signal is state.get("canal2", 807)
    assert monitor_starts == [signal]
    assert any(
        ev == "post_fill_tick_unavailable"
        for _sig, ev, _fields in events
    )
