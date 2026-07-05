from datetime import datetime
from types import SimpleNamespace

import pytest

import listener
from state import Signal


def test_msg_diag_emits_telegram_raw_event(monkeypatch):
    events = []
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig_id, ev, **kw: events.append((sig_id, ev, kw)),
    )
    msg = SimpleNamespace(
        id=12345,
        text="BUY NOW\nTP1 4505\nSL 4490",
        message="BUY NOW\nTP1 4505\nSL 4490",
        date=datetime(2026, 6, 4, 9, 0, 0),
        edit_date=None,
        sticker=None,
        photo=None,
        document=None,
        reply_to=SimpleNamespace(reply_to_msg_id=111),
    )

    listener._msg_diag(msg, "canal2", "new")

    raw = [row for row in events if row[1] == "telegram_raw"][0]
    assert raw[0] == "canal2_12345"
    assert raw[2]["channel"] == "canal2"
    assert raw[2]["message_id"] == 12345
    assert raw[2]["update_kind"] == "new"
    assert raw[2]["text"] == "BUY NOW\nTP1 4505\nSL 4490"
    assert raw[2]["has_text"] is True
    assert raw[2]["is_reply"] is True
    assert raw[2]["reply_to_msg_id"] == 111
    assert raw[2]["text_sha1"]


def test_log_telegram_understood_records_parsed_levels(monkeypatch):
    events = []
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig_id, ev, **kw: events.append((sig_id, ev, kw)),
    )

    listener._log_telegram_understood(
        "canal2_12345",
        channel="canal2",
        message_id=12345,
        kind="levels_update",
        parser="parse_canal2",
        raw_text="SELL NOW\n4575 - 4579\nTP1 4572\nSL 4585",
        parsed={
            "direction": "SELL",
            "range": (4575.0, 4579.0),
            "tps": [4572.0],
            "sl": 4585.0,
        },
        is_edit=True,
        tg_ts="2026-06-04T09:00:05",
    )

    row = events[0]
    assert row[0] == "canal2_12345"
    assert row[1] == "telegram_understood"
    payload = row[2]
    assert payload["kind"] == "levels_update"
    assert payload["parser"] == "parse_canal2"
    assert payload["direction"] == "SELL"
    assert payload["range_low"] == 4575.0
    assert payload["range_high"] == 4579.0
    assert payload["tps"] == [4572.0]
    assert payload["n_tps"] == 1
    assert payload["sl"] == 4585.0
    assert payload["is_edit"] is True
    assert payload["raw_text_sha1"]


@pytest.mark.asyncio
async def test_execute_actions_emits_management_understanding(monkeypatch):
    events = []
    executed = []
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig_id, ev, **kw: events.append((sig_id, ev, kw)),
    )
    monkeypatch.setattr(listener.journal, "append_mgmt", lambda *a, **kw: None)

    async def fake_execute_one(signal, classification, raw_text=""):
        executed.append((signal, classification, raw_text))

    monkeypatch.setattr(listener, "_execute_one_action", fake_execute_one)
    listener._seen_management_actions.clear()
    sig = Signal(channel="canal2", message_id=222, direction="BUY")

    await listener._execute_actions(
        sig,
        [{
            "action": "MOVE_SL_TO_BE",
            "price": None,
            "confidence": 0.95,
            "_reason": "regex_move_sl_be",
        }],
        raw_text="Move SL to BE",
        tg_ts="2026-06-04T09:01:00",
    )

    understood = [row for row in events if row[1] == "telegram_understood"][0]
    assert understood[0] == "canal2_222"
    payload = understood[2]
    assert payload["kind"] == "management"
    assert payload["parser"] == "classifier"
    assert payload["actions"] == ["MOVE_SL_TO_BE"]
    assert payload["parser_sources"] == ["regex"]
    assert payload["confidence_min"] == 0.95
    assert payload["requires_review"] is False
    assert len(executed) == 1


@pytest.mark.asyncio
async def test_execute_actions_firewall_logs_conditional_plan_without_execution(monkeypatch):
    events = []
    executed = []
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig_id, ev, **kw: events.append((sig_id, ev, kw)),
    )
    monkeypatch.setattr(listener.journal, "append_mgmt", lambda *a, **kw: None)

    async def fake_execute_one(signal, classification, raw_text=""):
        executed.append((signal, classification, raw_text))

    monkeypatch.setattr(listener, "_execute_one_action", fake_execute_one)
    listener._seen_management_actions.clear()
    sig = Signal(channel="canal1", message_id=20708, direction="BUY")

    await listener._execute_actions(
        sig,
        [{
            "action": "CONDITIONAL_PLAN",
            "confidence": 0.95,
            "is_conditional": True,
            "message_role": "conditional_plan",
        }],
        raw_text="If M5 closes below 4325 we close this trade.",
        tg_ts="2026-07-03T18:40:00",
    )

    assert executed == []
    firewall = [row for row in events
                if row[1] == "interpretation_firewall_decision"][0]
    assert firewall[2]["action"] == "CONDITIONAL_PLAN"
    assert firewall[2]["policy"] == "log_only"
    assert firewall[2]["will_execute"] is False
    assert firewall[2]["reason"] == "conditional_plan"


@pytest.mark.asyncio
async def test_execute_actions_firewall_notify_review_for_reentry(monkeypatch):
    events = []
    executed = []
    notified = []
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig_id, ev, **kw: events.append((sig_id, ev, kw)),
    )
    monkeypatch.setattr(listener.journal, "append_mgmt", lambda *a, **kw: None)

    async def fake_execute_one(signal, classification, raw_text=""):
        executed.append((signal, classification, raw_text))

    async def fake_notify_ambiguous(signal, classification, raw_text):
        notified.append((signal, classification, raw_text))

    monkeypatch.setattr(listener, "_execute_one_action", fake_execute_one)
    monkeypatch.setattr(listener, "notify_ambiguous_decision", fake_notify_ambiguous)
    listener._seen_management_actions.clear()
    sig = Signal(channel="canal1", message_id=20124, direction="SELL")

    await listener._execute_actions(
        sig,
        [{
            "action": "REENTRY_SIGNAL",
            "confidence": 0.91,
            "message_role": "direct_order",
            "evidence": "Reenter now SL to 4336.00",
        }],
        raw_text="Reenter now SL to 4336.00",
        tg_ts="2026-06-08T15:05:06",
    )

    assert executed == []
    assert len(notified) == 1
    firewall = [row for row in events
                if row[1] == "interpretation_firewall_decision"][0]
    assert firewall[2]["action"] == "REENTRY_SIGNAL"
    assert firewall[2]["policy"] == "notify_review"
    assert firewall[2]["will_execute"] is False
    assert firewall[2]["requires_review"] is True


def test_management_understanding_flags_uncovered_close_fragment(monkeypatch):
    events = []
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig_id, ev, **kw: events.append((sig_id, ev, kw)),
    )

    listener._log_telegram_understood(
        "canal1_20090",
        channel="canal1",
        message_id=20092,
        kind="management",
        parser="classifier",
        raw_text=(
            "Back in profits move sl to be on time if you are happy "
            "for today close for now in profit"
        ),
        classifications=[{
            "action": "MOVE_SL_TO_BE",
            "price": None,
            "confidence": 0.95,
            "reasoning": "Imperative instruction to move SL to BE.",
        }],
        target_signal_id="canal1_20090",
        is_reply=True,
        reply_to_msg_id=20091,
    )

    payload = events[0][2]
    assert payload["actions"] == ["MOVE_SL_TO_BE"]
    assert payload["coverage_status"] == "partial"
    assert payload["unhandled_text_fragments"] == ["close for now in profit"]
    assert payload["requires_review"] is True


def test_management_understanding_flags_new_review_intents(monkeypatch):
    events = []
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig_id, ev, **kw: events.append((sig_id, ev, kw)),
    )

    listener._log_telegram_understood(
        "canal1_20124",
        channel="canal1",
        message_id=20127,
        kind="management",
        parser="classifier",
        raw_text="Reenter now SL to 4336.00",
        classifications=[{
            "action": "REENTRY_SIGNAL",
            "confidence": 0.91,
            "message_role": "direct_order",
        }],
        target_signal_id="canal1_20124",
        is_reply=True,
        reply_to_msg_id=20124,
    )

    payload = events[0][2]
    assert payload["actions"] == ["REENTRY_SIGNAL"]
    assert payload["requires_review"] is True
