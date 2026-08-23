from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import config
import journal
import listener
import position_lifecycle_monitor as monitor
from state import Signal


def _candidate_signal(direction="BUY", anchor=4200.0):
    observed_at = datetime(2026, 8, 23, 9, 30, 0)
    signal = Signal(
        channel="canal1",
        message_id=23001,
        direction=direction,
        timestamp=observed_at,
        market_ticket=6001,
        market_fill_price=anchor,
    )
    listener._attach_dubai_live_candidate(signal, observed_at)
    return signal, observed_at


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("direction", "bid", "ask", "expected_level"),
    [
        ("BUY", 4195.8, 4196.0, 4196.0),
        ("SELL", 4204.0, 4204.2, 4204.0),
    ],
)
async def test_candidate_uses_ask_for_buy_and_bid_for_sell(
    monkeypatch,
    direction,
    bid,
    ask,
    expected_level,
):
    signal, observed_at = _candidate_signal(direction)
    calls = []

    async def fake_open(sig, leg, observed_price):
        calls.append((leg, observed_price))
        return (6101, expected_level - 0.1 if direction == "BUY" else expected_level + 0.1)

    monkeypatch.setattr(monitor, "_open_candidate_leg", fake_open)
    monkeypatch.setattr(journal, "event", lambda *args, **kwargs: None)
    monkeypatch.setattr(journal, "increment_dca_filled", lambda *args: None)

    opened = await monitor._process_candidate_entry_tick(
        signal,
        SimpleNamespace(bid=bid, ask=ask, time_msc=1000),
        now=observed_at + timedelta(minutes=1),
    )

    assert opened == 1
    assert calls[0][0]["volume"] == 0.04
    assert calls[0][0]["trigger_price"] == expected_level
    assert calls[0][1] == expected_level
    assert signal.dca_tickets == [6101]


@pytest.mark.asyncio
async def test_one_fast_tick_can_fill_both_crossed_levels_once(monkeypatch):
    signal, observed_at = _candidate_signal("BUY")
    fills = iter([(6101, 4191.9), (6102, 4191.8)])
    calls = []

    async def fake_open(sig, leg, observed_price):
        calls.append((leg["index"], leg["volume"], observed_price))
        return next(fills)

    monkeypatch.setattr(monitor, "_open_candidate_leg", fake_open)
    monkeypatch.setattr(journal, "event", lambda *args, **kwargs: None)
    monkeypatch.setattr(journal, "increment_dca_filled", lambda *args: None)
    tick = SimpleNamespace(bid=4191.7, ask=4191.9, time_msc=2000)

    first = await monitor._process_candidate_entry_tick(
        signal, tick, now=observed_at + timedelta(minutes=2),
    )
    second = await monitor._process_candidate_entry_tick(
        signal, tick, now=observed_at + timedelta(minutes=2, seconds=1),
    )

    assert first == 2
    assert second == 0
    assert calls == [(1, 0.04, 4191.9), (2, 0.04, 4191.9)]
    assert signal.dca_tickets == [6101, 6102]


@pytest.mark.asyncio
async def test_recovered_filled_leg_index_cannot_be_opened_twice(monkeypatch):
    signal, observed_at = _candidate_signal("BUY")
    signal.candidate_filled_leg_indexes = [1]
    calls = []

    async def fake_open(sig, leg, observed_price):
        calls.append(leg["index"])
        return (6102, 4191.8)

    monkeypatch.setattr(monitor, "_open_candidate_leg", fake_open)
    monkeypatch.setattr(journal, "event", lambda *args, **kwargs: None)
    monkeypatch.setattr(journal, "increment_dca_filled", lambda *args: None)

    opened = await monitor._process_candidate_entry_tick(
        signal,
        SimpleNamespace(bid=4191.7, ask=4191.9, time_msc=2100),
        now=observed_at + timedelta(minutes=2),
    )

    assert opened == 1
    assert calls == [2]
    assert signal.candidate_filled_leg_indexes == [1, 2]


@pytest.mark.asyncio
async def test_failed_fill_stays_pending_until_a_later_tick(monkeypatch):
    signal, observed_at = _candidate_signal("SELL")
    results = iter([None, (6201, 4204.3)])

    async def fake_open(sig, leg, observed_price):
        return next(results)

    monkeypatch.setattr(monitor, "_open_candidate_leg", fake_open)
    monkeypatch.setattr(journal, "event", lambda *args, **kwargs: None)
    monkeypatch.setattr(journal, "increment_dca_filled", lambda *args: None)
    tick = SimpleNamespace(bid=4204.2, ask=4204.4, time_msc=3000)

    failed = await monitor._process_candidate_entry_tick(
        signal, tick, now=observed_at + timedelta(minutes=1),
    )
    retried = await monitor._process_candidate_entry_tick(
        signal, tick, now=observed_at + timedelta(minutes=1, seconds=1),
    )

    assert failed == 0
    assert retried == 1
    assert signal.dca_tickets == [6201]


@pytest.mark.asyncio
async def test_expired_ladder_never_opens_and_logs_only_once(monkeypatch):
    signal, observed_at = _candidate_signal("BUY")
    events = []

    async def fail_open(*args, **kwargs):
        raise AssertionError("expired entry plan must not reach MT5")

    monkeypatch.setattr(monitor, "_open_candidate_leg", fail_open)
    monkeypatch.setattr(
        journal,
        "event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )
    tick = SimpleNamespace(bid=4189.8, ask=4190.0, time_msc=4000)

    for seconds in (1, 2):
        opened = await monitor._process_candidate_entry_tick(
            signal,
            tick,
            now=observed_at + timedelta(minutes=15, seconds=seconds),
        )
        assert opened == 0

    assert [ev for _, ev, _ in events] == ["dubai_entry_plan_expired"]


@pytest.mark.asyncio
async def test_candidate_leg_uses_exact_executor_fill_without_sl_or_tp(
    monkeypatch,
):
    signal, _ = _candidate_signal("BUY")
    calls = []

    def fake_open(*args, **kwargs):
        calls.append((args, kwargs))
        return (6301, 4195.73)

    async def immediate(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(monitor.executor, "open_market_with_fill", fake_open)
    monkeypatch.setattr(monitor.asyncio, "to_thread", immediate)
    monkeypatch.setattr(journal, "event", lambda *args, **kwargs: None)

    result = await monitor._open_candidate_leg(
        signal,
        {"index": 1, "volume": 0.04, "trigger_price": 4196.0},
        4195.9,
    )

    assert result == (6301, 4195.73)
    args, kwargs = calls[0]
    assert args[:2] == ("BUY", 0.04)
    assert kwargs["sl"] is None
    assert kwargs["tp"] is None
    assert kwargs["magic"] == config.magic_for("canal1")


@pytest.mark.asyncio
async def test_mutated_or_over_cap_plan_is_rejected_before_mt5(monkeypatch):
    signal, observed_at = _candidate_signal("BUY")
    signal.candidate_entry_legs[1]["volume"] = 0.05

    async def fail_open(*args, **kwargs):
        raise AssertionError("invalid frozen plan must not reach MT5")

    monkeypatch.setattr(monitor, "_open_candidate_leg", fail_open)

    with pytest.raises(RuntimeError, match="frozen entry plan"):
        await monitor._process_candidate_entry_tick(
            signal,
            SimpleNamespace(bid=4195.8, ask=4196.0, time_msc=5000),
            now=observed_at + timedelta(minutes=1),
        )


@pytest.mark.asyncio
async def test_triggered_basket_exit_blocks_every_later_ladder_fill(
    monkeypatch,
):
    signal, observed_at = _candidate_signal("BUY")
    signal.basket_guard_triggered = True

    async def fail_open(*args, **kwargs):
        raise AssertionError("a closing basket must never add exposure")

    monkeypatch.setattr(monitor, "_open_candidate_leg", fail_open)

    opened = await monitor._process_candidate_entry_tick(
        signal,
        SimpleNamespace(bid=4191.7, ask=4191.9, time_msc=6000),
        now=observed_at + timedelta(minutes=2),
    )

    assert opened == 0
