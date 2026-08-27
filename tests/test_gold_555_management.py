from __future__ import annotations

import pytest

import gold_555_live_candidate
import listener
from state import Signal


def _signal() -> Signal:
    return Signal(
        channel="canal2",
        message_id=380,
        direction="BUY",
        market_ticket=1000,
        extra_market_tickets=[1001],
        live_strategy_id=gold_555_live_candidate.CANDIDATE_ID,
        live_strategy_fingerprint=gold_555_live_candidate.CANDIDATE_FINGERPRINT,
    )


@pytest.mark.asyncio
async def test_explicit_provider_close_closes_complete_555_basket(monkeypatch) -> None:
    signal = _signal()
    closes = []
    events = []
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_close_position",
        lambda sig, ticket, **kwargs: closes.append((ticket, kwargs)),
    )
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )

    result = await listener._execute_one_action(
        signal,
        {"action": "CLOSE_FIRST", "confidence": 0.99},
        raw_text="Close first entries now",
    )

    assert result == "closed"
    assert signal.requested_close_reason == "PROVIDER_CLOSE"
    assert [row[0] for row in closes] == [1000, 1001]
    assert all(row[1]["persist_until_signal_close"] for row in closes)
    assert any(row[1] == "gold_555_provider_close_requested" for row in events)


@pytest.mark.asyncio
async def test_provider_be_and_level_changes_are_observed_not_applied(monkeypatch) -> None:
    signal = _signal()
    actions = []
    events = []
    monkeypatch.setattr(
        listener.pending_actions.queue,
        "add",
        actions.append,
    )
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )

    result = await listener._execute_one_action(
        signal,
        {"action": "MOVE_SL_TO_BE", "confidence": 0.99},
        raw_text="Move SL to BE",
    )

    assert result == "ignored"
    assert actions == []
    assert events[-1][1] == "gold_555_provider_action_observed_not_applied"


@pytest.mark.asyncio
async def test_provider_levels_are_recorded_without_mutating_555_orders(
    monkeypatch,
) -> None:
    signal = _signal()
    signal.provider_tps = [4310.0, 4320.0]
    signal.sl = 4280.0
    signal.provider_sl_received = True
    signal.candidate_hard_stops = {1000: 4270.0, 1001: 4271.0}
    events = []
    monkeypatch.setattr(
        listener.pending_actions.queue,
        "add",
        lambda _action: pytest.fail("provider levels must not touch MT5"),
    )
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )

    await listener._apply_sl_tp(signal)

    assert events[-1][1] == "gold_555_provider_levels_observed_not_applied"
    assert events[-1][2]["provider_tps"] == [4310.0, 4320.0]
    assert events[-1][2]["effective_stops_by_ticket"] == {
        1000: 4270.0,
        1001: 4271.0,
    }


@pytest.mark.asyncio
async def test_legacy_scale_out_cannot_open_immediate_legs_for_gold_555(
    monkeypatch,
) -> None:
    signal = _signal()
    monkeypatch.setattr(
        listener.executor,
        "open_market_with_fill",
        lambda *_args, **_kwargs: pytest.fail(
            "Gold 555 must use only its delayed adverse ladder"
        ),
    )

    await listener._open_extra_legs(signal, signal.message_id)

    assert signal.extra_market_tickets == [1001]


@pytest.mark.asyncio
async def test_provider_range_cannot_trigger_legacy_rescue_for_gold_555(
    monkeypatch,
) -> None:
    signal = _signal()
    events = []
    monkeypatch.setattr(
        listener.executor,
        "entry_price",
        lambda *_args, **_kwargs: pytest.fail(
            "Gold 555 provider range must remain evidence only"
        ),
    )
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )

    closed = await listener._handle_range_arrival_safety(
        signal,
        4290.0,
        4300.0,
    )

    assert closed is False
    assert signal.range_safety_applied is True
    assert events[-1][1] == "gold_555_provider_range_observed_not_applied"
