from datetime import datetime

import pytest

import journal
import listener
from state import Signal


def _candidate_signal():
    signal = Signal(
        channel="canal1",
        message_id=25001,
        direction="SELL",
        market_ticket=8001,
        market_fill_price=4200.0,
        dca_tickets=[8002, 8003],
        tps=[4196.0, 4192.0],
        sl=4208.0,
        provider_tps=[4196.0, 4192.0],
        provider_sl_received=True,
    )
    listener._attach_dubai_live_candidate(
        signal, datetime(2026, 8, 23, 9, 30, 0),
    )
    return signal


@pytest.mark.asyncio
async def test_provider_levels_are_recorded_but_never_installed(monkeypatch):
    signal = _candidate_signal()
    events = []

    async def fail_run(*args, **kwargs):
        raise AssertionError("candidate provider levels must not reach MT5")

    monkeypatch.setattr(listener, "_run", fail_run)
    monkeypatch.setattr(
        journal,
        "event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )

    await listener._apply_sl_tp(signal)

    assert signal.provider_tps == [4196.0, 4192.0]
    assert signal.sl == 4208.0
    assert [ev for _, ev, _ in events] == [
        "dubai_provider_levels_observed_not_applied"
    ]


@pytest.mark.asyncio
async def test_provider_range_cannot_activate_legacy_rescue_for_candidate(
    monkeypatch,
):
    signal = _candidate_signal()
    signal.direction = "BUY"
    signal.market_fill_price = 4200.0
    signal.adverse_action = "rescue_market"
    events = []

    async def fail_run(*args, **kwargs):
        raise AssertionError("candidate range must not execute legacy rescue")

    monkeypatch.setattr(listener, "_run", fail_run)
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_close_position",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("candidate range must not close the basket")
        ),
    )
    monkeypatch.setattr(
        journal,
        "event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )

    closed = await listener._handle_range_arrival_safety(
        signal, 4210.0, 4212.0,
    )

    assert closed is False
    assert signal.entry_mode == "adverse_ladder"
    assert [ev for _, ev, _ in events] == [
        "dubai_provider_range_observed_not_applied"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["MOVE_SL_TO_BE", "MOVE_SL_TO_PRICE"])
async def test_provider_protection_is_evidence_only_for_candidate(
    monkeypatch,
    action,
):
    signal = _candidate_signal()
    events = []

    def fail_pending(*args, **kwargs):
        raise AssertionError("candidate protection must not mutate MT5")

    monkeypatch.setattr(
        listener.pending_actions, "enqueue_modify_sl", fail_pending,
    )
    monkeypatch.setattr(
        journal,
        "event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )

    outcome = await listener._execute_one_action(
        signal,
        {"action": action, "price": 4198.0, "confidence": 0.99},
        raw_text="Move SL now",
    )

    assert outcome == "ignored"
    ignored = next(
        fields for _, ev, fields in events
        if ev == "dubai_provider_action_observed_not_applied"
    )
    assert ignored["action"] == action


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    ["CLOSE_ALL", "CLOSE_FIRST", "CLOSE_AT_TP", "CLOSE_PROFIT_OR_BE"],
)
async def test_any_explicit_provider_close_closes_the_complete_candidate_basket(
    monkeypatch,
    action,
):
    signal = _candidate_signal()
    closes = []
    events = []
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_close_position",
        lambda sig, ticket, label: closes.append((ticket, label)),
    )
    monkeypatch.setattr(
        journal,
        "event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )

    outcome = await listener._execute_one_action(
        signal,
        {"action": action, "price": 1, "confidence": 0.99},
        raw_text="Close the trade",
    )

    assert outcome == "requested"
    assert [ticket for ticket, _ in closes] == [8001, 8002, 8003]
    assert signal.requested_close_reason == "PROVIDER_CLOSE"
    assert signal.status == "open"
    applied = next(
        fields for _, ev, fields in events
        if ev == "dubai_provider_close_requested"
    )
    assert applied["classified_action"] == action
