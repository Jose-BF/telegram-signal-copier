from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import gold_555_live_candidate
import pending_actions
import position_lifecycle_monitor as monitor
from state import Signal


@pytest.fixture(autouse=True)
def _isolate_journal(monkeypatch):
    monkeypatch.setattr(monitor, "_journal_event", lambda *a, **k: None)
    monkeypatch.setattr(
        monitor.executor,
        "account_evidence",
        lambda: {
            "trade_mode": 0,
            "trade_mode_name": "demo",
            "currency": "EUR",
        },
    )
    monkeypatch.setattr(
        monitor.executor.mt5,
        "ACCOUNT_TRADE_MODE_DEMO",
        0,
        raising=False,
    )


def _signal(direction: str = "BUY") -> Signal:
    policy = gold_555_live_candidate.Gold555Policy()
    anchor = 4300.0
    levels = policy.entry_levels(direction, anchor)
    signal = Signal(
        channel="canal2",
        message_id=380,
        direction=direction,
        timestamp=datetime.utcnow(),
        market_ticket=1000,
        market_fill_price=anchor,
        entry_mode="adverse_ladder",
        live_strategy_id=gold_555_live_candidate.CANDIDATE_ID,
        live_strategy_fingerprint=gold_555_live_candidate.CANDIDATE_FINGERPRINT,
        candidate_entry_anchor=anchor,
        candidate_first_fill_at=datetime.utcnow(),
        candidate_entry_expires_at=datetime.utcnow() + timedelta(minutes=25),
        candidate_entry_legs=[
            {
                "index": index,
                "volume": policy.entry_volumes[index],
                "trigger_price": levels[index],
                "target_step": policy.target_steps[index],
            }
            for index in range(5)
        ],
    )
    signal.candidate_entry_prices_by_ticket[1000] = anchor
    signal.candidate_hard_stops[1000] = policy.initial_stop(direction, anchor)
    return signal


def test_555_entry_plan_is_recognized_and_frozen() -> None:
    signal = _signal()

    plan = monitor._candidate_entry_plan(signal)

    assert len(plan) == 5
    assert plan[1] == {
        "index": 1,
        "volume": 0.03,
        "trigger_price": 4298.5,
        "target_step": 1.0,
    }


def test_flat_555_waits_for_unfilled_legs_before_entry_expiry() -> None:
    signal = _signal()
    now = signal.candidate_entry_expires_at - timedelta(seconds=1)

    assert monitor._should_auto_finalize_signal(
        signal,
        {
            "positions_complete": True,
            "n_open": 0,
        },
        monitor_started_monotonic=100.0,
        now_monotonic=131.0,
        now=now,
    ) is False


@pytest.mark.parametrize(
    ("mutate", "now_offset_s"),
    [
        ("expired", 1),
        ("all_legs_filled", -1),
        ("provider_close", -1),
    ],
)
def test_flat_555_finalizes_only_when_entry_plan_is_over(
    mutate,
    now_offset_s,
) -> None:
    signal = _signal()
    if mutate == "all_legs_filled":
        signal.candidate_filled_leg_indexes = [1, 2, 3, 4]
    elif mutate == "provider_close":
        signal.requested_close_reason = "PROVIDER_CLOSE"
    now = signal.candidate_entry_expires_at + timedelta(seconds=now_offset_s)

    assert monitor._should_auto_finalize_signal(
        signal,
        {
            "positions_complete": True,
            "n_open": 0,
        },
        monitor_started_monotonic=100.0,
        now_monotonic=131.0,
        now=now,
    ) is True


@pytest.mark.asyncio
async def test_flat_555_can_fill_a_later_adverse_leg(monkeypatch) -> None:
    signal = _signal()
    fills = []

    async def fake_open(sig, leg, observed_price):
        fills.append((leg["index"], observed_price))
        return 1001, 4298.4

    monkeypatch.setattr(monitor, "_open_candidate_leg", fake_open)
    monkeypatch.setattr(
        monitor,
        "_queue_gold_555_leg_protection",
        lambda *args, **kwargs: (4268.4, 4299.4),
    )

    opened = await monitor._process_candidate_entry_tick(
        signal,
        SimpleNamespace(bid=4298.3, ask=4298.5, time_msc=123),
        now=signal.candidate_entry_expires_at - timedelta(seconds=1),
    )

    assert opened == 1
    assert fills == [(1, 4298.5)]
    assert signal.status == "open"
    assert signal.dca_tickets == [1001]


@pytest.mark.asyncio
async def test_delayed_leg_provisional_levels_use_current_executable_quote(
    monkeypatch,
) -> None:
    signal = _signal()
    requested = []

    def fake_open(direction, lot, sl, tp, comment, magic):
        requested.append((direction, lot, sl, tp, comment, magic))
        return 1001, 4296.75

    monkeypatch.setattr(
        monitor.executor,
        "open_market_with_fill",
        fake_open,
    )

    result = await monitor._open_candidate_leg(
        signal,
        signal.candidate_entry_legs[1],
        observed_price=4296.80,
    )

    assert result == (1001, 4296.75)
    assert requested == [(
        "BUY",
        0.03,
        4266.80,
        4297.80,
        "c2_380_B1_g55",
        signal.magic,
    )]


@pytest.mark.asyncio
async def test_delayed_leg_revalidates_demo_eur_before_mt5(monkeypatch) -> None:
    signal = _signal()
    orders = []
    monkeypatch.setattr(
        monitor.executor,
        "account_evidence",
        lambda: {
            "trade_mode": 2,
            "trade_mode_name": "real",
            "currency": "EUR",
        },
    )
    monkeypatch.setattr(
        monitor.executor,
        "open_market_with_fill",
        lambda *args, **kwargs: orders.append((args, kwargs)),
    )

    with pytest.raises(
        gold_555_live_candidate.Gold555AccountError,
        match="demo EUR",
    ):
        await monitor._open_candidate_leg(
            signal,
            signal.candidate_entry_legs[1],
            observed_price=4298.5,
        )

    assert orders == []


@pytest.mark.asyncio
async def test_crossed_tick_opens_every_new_leg_in_sequence(monkeypatch) -> None:
    signal = _signal()
    fills = iter([(1001, 4298.4), (1002, 4296.9)])
    opened_indexes = []
    sl_requests = []
    tp_requests = []

    async def fake_open(sig, leg, observed_price):
        opened_indexes.append((leg["index"], observed_price))
        return next(fills)

    monkeypatch.setattr(monitor, "_open_candidate_leg", fake_open)
    monkeypatch.setattr(
        monitor.pending_actions,
        "enqueue_modify_sl",
        lambda sig, ticket, price, **kwargs: sl_requests.append(
            (ticket, price, kwargs)
        ),
    )
    monkeypatch.setattr(
        monitor.pending_actions,
        "enqueue_modify_tp",
        lambda sig, ticket, price, **kwargs: tp_requests.append(
            (ticket, price, kwargs)
        ),
    )

    opened = await monitor._process_candidate_entry_tick(
        signal,
        SimpleNamespace(bid=4296.6, ask=4296.8, time_msc=123),
    )

    assert opened == 2
    assert opened_indexes == [(1, 4296.8), (2, 4296.8)]
    assert signal.dca_tickets == [1001, 1002]
    assert signal.candidate_filled_leg_indexes == [1, 2]
    assert signal.candidate_entry_prices_by_ticket == {
        1000: 4300.0,
        1001: 4298.4,
        1002: 4296.9,
    }
    assert sl_requests[0][0:2] == (1001, 4268.4)
    assert sl_requests[1][0:2] == (1002, 4266.9)
    assert all(row[2]["persist_until_signal_close"] for row in sl_requests)
    assert tp_requests[0][0:2] == (1001, 4299.4)
    assert tp_requests[1][0:2] == (1002, 4298.4)
    assert all(row[2]["persist_until_signal_close"] for row in tp_requests)


@pytest.mark.asyncio
async def test_555_entry_plan_stops_opening_after_expiry(monkeypatch) -> None:
    signal = _signal()
    signal.candidate_entry_expires_at = datetime.utcnow() - timedelta(seconds=1)
    opened = []
    monkeypatch.setattr(
        monitor,
        "_open_candidate_leg",
        lambda *args, **kwargs: opened.append((args, kwargs)),
    )
    monkeypatch.setattr(monitor, "_journal_event", lambda *a, **k: None)

    result = await monitor._process_candidate_entry_tick(
        signal,
        SimpleNamespace(bid=4290.0, ask=4290.2, time_msc=123),
    )

    assert result == 0
    assert opened == []
    assert signal.candidate_entry_expiry_logged is True


@pytest.mark.asyncio
async def test_trailing_stop_tightens_each_leg_without_loosening(monkeypatch) -> None:
    signal = _signal()
    signal.dca_tickets = [1001]
    signal.candidate_filled_leg_indexes = [1]
    signal.candidate_entry_prices_by_ticket[1001] = 4298.5
    signal.candidate_hard_stops[1001] = 4268.5
    requests = []
    monkeypatch.setattr(
        monitor.pending_actions,
        "enqueue_modify_sl",
        lambda sig, ticket, price, **kwargs: requests.append(
            (ticket, price, kwargs)
        ),
    )

    tightened = await monitor._apply_gold_555_trailing_stops(
        signal,
        SimpleNamespace(bid=4310.0, ask=4310.2, time_msc=1),
    )
    unchanged = await monitor._apply_gold_555_trailing_stops(
        signal,
        SimpleNamespace(bid=4309.0, ask=4309.2, time_msc=2),
    )

    assert tightened == 2
    assert unchanged == 0
    assert [(row[0], row[1]) for row in requests] == [
        (1000, 4280.0),
        (1001, 4280.0),
    ]
    assert signal.candidate_hard_stops == {1000: 4280.0, 1001: 4280.0}


@pytest.mark.asyncio
async def test_trailing_only_targets_tickets_still_open_in_mt5(monkeypatch) -> None:
    signal = _signal()
    signal.dca_tickets = [1001]
    signal.candidate_filled_leg_indexes = [1]
    signal.candidate_entry_prices_by_ticket[1001] = 4298.5
    signal.candidate_hard_stops[1001] = 4268.5
    requests = []
    monkeypatch.setattr(
        monitor.pending_actions,
        "enqueue_modify_sl",
        lambda sig, ticket, price, **kwargs: requests.append(ticket),
    )

    tightened = await monitor._apply_gold_555_trailing_stops(
        signal,
        SimpleNamespace(bid=4310.0, ask=4310.2, time_msc=1),
        open_tickets={1001},
    )

    assert tightened == 1
    assert requests == [1001]
    assert signal.candidate_hard_stops[1000] == 4270.0
    assert signal.candidate_hard_stops[1001] == 4280.0


def test_555_basket_guard_uses_its_own_profit_contract(monkeypatch) -> None:
    signal = _signal()
    events = []
    closes = []
    monkeypatch.setattr(
        monitor,
        "_journal_event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )
    monkeypatch.setattr(
        monitor.pending_actions,
        "enqueue_close_position",
        lambda sig, ticket, **kwargs: closes.append((ticket, kwargs)),
    )

    armed = monitor._apply_live_basket_guard(
        signal,
        {
            "positions_complete": True,
            "floating_pl": 30.0,
            "realized_pl": 0.0,
            "realized_complete": True,
            "total_pl": 30.0,
            "n_open": 1,
            "open_tickets": [1000],
        },
        now=signal.candidate_first_fill_at + timedelta(minutes=10),
    )
    closed = monitor._apply_live_basket_guard(
        signal,
        {
            "positions_complete": True,
            "floating_pl": 29.0,
            "realized_pl": 0.0,
            "realized_complete": True,
            "total_pl": 29.0,
            "n_open": 1,
            "open_tickets": [1000],
        },
        now=signal.candidate_first_fill_at + timedelta(minutes=11),
    )

    assert armed.action == "arm"
    assert closed.action == "close"
    assert closed.reason == "profit_lock"
    assert closes[0][0] == 1000
    assert closes[0][1]["persist_until_signal_close"] is True


def test_target_retry_can_persist_until_signal_close(monkeypatch) -> None:
    signal = _signal()
    queued = []
    monkeypatch.setattr(pending_actions.queue, "add", queued.append)

    pending_actions.enqueue_modify_tp(
        signal,
        1000,
        4300.5,
        persist_until_signal_close=True,
    )

    assert len(queued) == 1
    assert queued[0].new_tp == 4300.5
    assert queued[0].persist_until_signal_close is True
