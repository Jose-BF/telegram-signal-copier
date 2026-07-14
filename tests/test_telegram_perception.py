from datetime import datetime
from types import SimpleNamespace

import pytest

import listener
from state import Signal, TradeContext


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


@pytest.mark.parametrize("update_kind", ["poll_edit", "recovery_edit"])
def test_telegram_raw_payload_marks_semantic_edits(update_kind):
    msg = SimpleNamespace(
        id=12346,
        text="SELL NOW",
        message="SELL NOW",
        date=datetime(2026, 6, 4, 9, 0, 0),
        edit_date=datetime(2026, 6, 4, 9, 0, 5),
        sticker=None,
        photo=None,
        document=None,
        reply_to=None,
    )

    payload = listener._telegram_raw_payload(msg, "canal2", update_kind)

    assert payload["update_kind"] == update_kind
    assert payload["is_edit"] is True


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


def test_review_notification_format_is_human_first_and_compact():
    ctx = TradeContext(
        channel="canal1",
        signal_id="canal1_20700",
        direction="SELL",
        entry_price=4328.5,
        tps=[4320.0, 4314.0],
        sl=4336.0,
        n_initial=4,
        n_open=2,
        open_tickets_pnl=[(11, 2.1), (12, 2.1)],
        floating_pnl_total=4.2,
        elapsed_min=18.4,
        current_price=4321.5,
        be_armed=False,
    )

    text = listener.format_review_notification(
        ctx,
        {
            "action": "REENTRY_SIGNAL",
            "confidence": 0.91,
            "reasoning": "Re-entry instruction needs manual handling.",
        },
        "Reenter now SL to 4336.00",
    )

    first_lines = "\n".join(text.splitlines()[:3])
    assert "canal1_20700" not in first_lines
    assert "Canal 1" in first_lines
    assert "SELL" in first_lines
    assert "+4.20" in text
    assert "2/4 abiertas" in text
    assert "4321.50" in text
    assert "4336.00" in text
    assert "Reenter now SL to 4336.00" in text
    assert "No ejecuto automatico" in text
    assert "Opciones" not in text
    assert len(text.splitlines()) <= 14


@pytest.mark.asyncio
async def test_notify_ambiguous_decision_sends_compact_review(monkeypatch):
    sent = []
    events = []
    ctx = TradeContext(
        channel="canal2",
        signal_id="canal2_33001",
        direction="BUY",
        entry_price=4310.0,
        tps=[4318.0],
        sl=4302.0,
        n_initial=5,
        n_open=4,
        open_tickets_pnl=[],
        floating_pnl_total=-3.5,
        elapsed_min=7.2,
        current_price=4308.3,
        be_armed=True,
    )
    sig = Signal(channel="canal2", message_id=33001, direction="BUY")
    async def fake_notify(text):
        sent.append(text)

    monkeypatch.setattr(sig, "build_context", lambda: ctx)
    monkeypatch.setattr(listener, "notify", fake_notify)
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig_id, ev, **kw: events.append((sig_id, ev, kw)),
    )

    await listener.notify_ambiguous_decision(
        sig,
        {"action": "UNKNOWN", "confidence": 0.0, "reasoning": "unclear"},
        "Maybe protect here but wait for confirmation",
    )

    assert len(sent) == 1
    assert sent[0].startswith("REVISION NECESARIA\nCanal 2 | BUY")
    assert "canal2_33001" not in "\n".join(sent[0].splitlines()[:3])
    assert "Opciones" not in sent[0]
    assert "Decision manual: revisar en MT5 si procede." in sent[0]
    assert events[0][1] == "ambiguous_decision_notified"
