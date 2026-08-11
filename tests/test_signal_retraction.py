from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import listener
import position_lifecycle_monitor
from state import Signal, StateManager


def _signal(message_id, timestamp, *, ticket, tps=None):
    return Signal(
        channel="canal2",
        message_id=message_id,
        direction="BUY",
        timestamp=timestamp,
        market_ticket=ticket,
        market_fill_price=4300.0,
        range_low=4299.0,
        range_high=4301.0,
        tps=tps or [4303.0, 4305.0, 4307.0, 4309.0],
        sl=4292.0,
    )


@pytest.mark.parametrize(
    "text",
    [
        "This is not a new signal",
        "THIS IS NOT A NEW SIGNAL!",
        "This wasn't a new signal.",
    ],
)
def test_detects_only_explicit_duplicate_retraction_phrases(text):
    assert listener._is_explicit_signal_retraction(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "This is a new signal",
        "Is this not a new signal?",
        "Wait for a new signal",
        "Not a new setup yet, keep watching",
    ],
)
def test_does_not_guess_retraction_from_ambiguous_text(text):
    assert listener._is_explicit_signal_retraction(text) is False


def test_selects_only_fresh_newest_material_duplicate():
    now = datetime(2026, 8, 11, 9, 15)
    original = _signal(1358, now - timedelta(minutes=20), ticket=101)
    duplicate = _signal(1359, now - timedelta(seconds=25), ticket=201)

    result = listener._select_explicit_duplicate_retraction(
        [duplicate, original],
        now,
        max_age_s=180,
    )

    assert result["reason"] == "proven_duplicate"
    assert result["candidate"] is duplicate
    assert result["original"] is original


def test_refuses_retraction_when_original_is_ambiguous():
    now = datetime(2026, 8, 11, 9, 15)
    original_a = _signal(1357, now - timedelta(minutes=30), ticket=100)
    original_b = _signal(1358, now - timedelta(minutes=20), ticket=101)
    duplicate = _signal(1359, now - timedelta(seconds=25), ticket=201)

    result = listener._select_explicit_duplicate_retraction(
        [duplicate, original_b, original_a],
        now,
        max_age_s=180,
    )

    assert result["reason"] == "multiple_matching_originals"
    assert result["candidate"] is None


@pytest.mark.asyncio
async def test_explicit_retraction_closes_only_proven_newest_duplicate(monkeypatch):
    now = datetime(2026, 8, 11, 9, 15)
    original = _signal(1358, now - timedelta(minutes=20), ticket=101)
    duplicate = _signal(1359, now - timedelta(seconds=25), ticket=201)
    duplicate.extra_market_tickets = [202]
    duplicate.pending_tickets = [203]
    local_state = StateManager()
    local_state.add(original)
    local_state.add(duplicate)

    closes = []
    cancels = []
    events = []
    monkeypatch.setattr(listener, "state", local_state)
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_close_position",
        lambda signal, ticket, label="": closes.append((signal, ticket)),
    )
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_cancel_pending",
        lambda signal, ticket, label="": cancels.append((signal, ticket)),
    )
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )
    monkeypatch.setattr(listener, "_schedule_detached", lambda awaitable: awaitable.close())

    msg = SimpleNamespace(
        id=1360,
        text="This is not a new signal",
        message="This is not a new signal",
        date=now.replace(tzinfo=timezone.utc),
    )

    assert await listener._handle_explicit_signal_retraction(msg, "canal2") is True
    assert closes == [(duplicate, 201), (duplicate, 202)]
    assert cancels == [(duplicate, 203)]
    assert original.status == "open"
    assert duplicate.status == "open"
    assert duplicate.requested_close_reason == "PROVIDER_RETRACTED"
    applied = next(fields for _, ev, fields in events if ev == "provider_duplicate_retraction_applied")
    assert applied["original_signal_id"] == "canal2_1358"
    assert applied["retracted_signal_id"] == "canal2_1359"


def test_retracted_signal_finalizes_with_provider_reason():
    signal = _signal(
        1359,
        datetime(2026, 8, 11, 9, 14),
        ticket=201,
    )
    signal.requested_close_reason = "PROVIDER_RETRACTED"

    assert position_lifecycle_monitor._dominant_close_tag(
        signal,
        {"MANUAL": 2},
    ) == "PROVIDER_RETRACTED"
