from datetime import datetime
from types import SimpleNamespace

import pytest

import causal_trace
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
        chat_id=-1003828356530,
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
    assert raw[2]["chat_id"] == -1003828356530
    assert raw[2]["update_kind"] == "new"
    assert raw[2]["revision_token"] == "new"
    assert raw[2]["text"] == "BUY NOW\nTP1 4505\nSL 4490"
    assert raw[2]["has_text"] is True
    assert raw[2]["is_reply"] is True
    assert raw[2]["reply_to_msg_id"] == 111
    assert raw[2]["text_sha1"]
    assert raw[2]["message_revision_id"].startswith("msgrev_")


def test_edit_handler_diagnostic_uses_edit_time_and_revision_identity(
        monkeypatch):
    events = []
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig_id, ev, **kw: events.append((sig_id, ev, kw)),
    )
    msg = SimpleNamespace(
        id=12345,
        chat_id=-1003908582492,
        text="BUY NOW\nSL 4490",
        message="BUY NOW\nSL 4490",
        date=datetime(2026, 6, 4, 9, 0, 0),
        edit_date=datetime(2026, 6, 4, 9, 5, 0),
        sticker=None,
        photo=None,
        document=None,
        reply_to=None,
    )

    listener._msg_diag(msg, "canal2", "poll_edit")

    raw = next(row for row in events if row[1] == "telegram_raw")
    handler = next(row for row in events if row[1] == "handler_entry")
    assert handler[2]["tg_ts"] == "2026-06-04T09:05:00"
    assert handler[2]["message_revision_id"] == (
        raw[2]["message_revision_id"]
    )
    assert raw[2]["revision_token"] == "2026-06-04T09:05:00+00:00"


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


def test_telegram_revision_identity_is_stable_and_changes_on_edit():
    base = SimpleNamespace(
        id=12346,
        chat_id=-1003908582492,
        text="SELL NOW",
        message="SELL NOW",
        date=datetime(2026, 6, 4, 9, 0, 0),
        edit_date=None,
        sticker=None,
        photo=None,
        document=None,
        reply_to=None,
    )

    first = listener._telegram_raw_payload(base, "canal2", "new")
    duplicate = listener._telegram_raw_payload(base, "canal2", "poll_new")
    edited = SimpleNamespace(
        **{
            **base.__dict__,
            "text": "SELL NOW\nSL 4585",
            "message": "SELL NOW\nSL 4585",
            "edit_date": datetime(2026, 6, 4, 9, 0, 5),
        }
    )
    edited_payload = listener._telegram_raw_payload(
        edited, "canal2", "poll_edit")

    assert first["message_revision_id"] == duplicate["message_revision_id"]
    assert edited_payload["message_revision_id"] != (
        first["message_revision_id"]
    )


def test_media_only_message_has_revision_identity():
    msg = SimpleNamespace(
        id=12347,
        chat_id=-1001642806869,
        text="",
        message="",
        date=datetime(2026, 6, 4, 9, 0, 0),
        edit_date=None,
        sticker=None,
        photo=SimpleNamespace(id=77),
        document=None,
        reply_to=None,
    )

    payload = listener._telegram_raw_payload(msg, "canal1", "new")

    assert payload["has_media"] is True
    assert payload["media_sha256"] is None
    assert payload["message_revision_id"].startswith("msgrev_")
    assert causal_trace.message_revision_id(
        chat_id=msg.chat_id,
        message_id=msg.id,
        revision_token="new",
        text_sha1=None,
        media_sha256=None,
    ) == payload["message_revision_id"]


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
            "provider_stated_be_price": 4030.0,
            "confidence": 0.95,
            "_reason": "regex_move_sl_be",
        }],
        raw_text="Move SL to BE at 4030",
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
    assert executed[0][1]["price"] is None
    assert executed[0][1]["provider_stated_be_price"] == 4030.0
    firewall = [
        row for row in events if row[1] == "interpretation_firewall_decision"
    ][0]
    assert firewall[2]["price"] is None
    assert firewall[2]["provider_stated_be_price"] == 4030.0
    applied = [row for row in events if row[1] == "mgmt_msg"][-1]
    assert applied[2]["provider_stated_be_price"] == 4030.0


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


@pytest.mark.asyncio
async def test_historical_sl_comment_does_not_close_live_mt5_positions(
        monkeypatch):
    events = []
    finalized = []
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig_id, ev, **kw: events.append((sig_id, ev, kw)),
    )
    monkeypatch.setattr(listener.journal, "append_mgmt", lambda *a, **kw: None)
    monkeypatch.setattr(
        listener,
        "_open_mt5_positions_for_signal",
        lambda signal: [{"ticket": signal.market_ticket}],
    )

    async def fake_finalize(signal, closed_by, notes=""):
        finalized.append((signal, closed_by, notes))

    monkeypatch.setattr(listener, "_finalize_signal", fake_finalize)
    listener._seen_management_actions.clear()
    sig = Signal(
        channel="canal1",
        message_id=20700,
        direction="SELL",
        market_ticket=9001,
    )

    await listener._execute_actions(
        sig,
        [{"action": "INFORMATIONAL", "confidence": 0.95}],
        raw_text="SL was HIT. New York wicked it then dumped.",
    )

    assert sig.status == "open"
    assert finalized == []
    deferred = [row for row in events if row[1] == "sl_hit_message_deferred"]
    assert len(deferred) == 1
    assert deferred[0][2]["reason"] == "mt5_positions_still_open"


@pytest.mark.asyncio
async def test_extra_leg_opening_exposes_transient_audit_guard(monkeypatch):
    observed_flags = []
    events = []
    sig = Signal(channel="canal2", message_id=380, direction="BUY")
    sig.market_ticket = 1000

    async def fake_run(fn, *args, **kwargs):
        observed_flags.append(getattr(sig, "opening_extra_legs", False))
        return (1001, 4056.50)

    monkeypatch.setattr(listener, "_run", fake_run)
    monkeypatch.setattr(listener.config, "STRATEGY_C2_ENTRY_MODE", "scale_out")
    monkeypatch.setattr(listener.config, "STRATEGY_C2_NUM_ENTRIES", 2)
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig_id, ev, **kw: events.append((sig_id, ev, kw)),
    )
    monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **kw: None)

    await listener._open_extra_legs(sig, 380)

    assert observed_flags == [True]
    assert sig.opening_extra_legs is False
    assert sig.extra_market_tickets == [1001]


@pytest.mark.asyncio
async def test_scale_out_never_opens_beyond_signal_exposure_cap(monkeypatch):
    sig = Signal(channel="canal2", message_id=932, direction="BUY")
    sig.market_ticket = 1000
    calls = []

    async def fake_run(fn, *args, **kwargs):
        ticket = 1001 + len(calls)
        calls.append(args)
        return (ticket, 4056.50)

    monkeypatch.setattr(listener, "_run", fake_run)
    monkeypatch.setattr(listener.config, "LOT_SIZE", 0.01)
    monkeypatch.setattr(listener.config, "STRATEGY_C2_ENTRY_MODE", "scale_out")
    monkeypatch.setattr(listener.config, "STRATEGY_C2_NUM_ENTRIES", 6)
    monkeypatch.setattr(
        listener.config, "STRATEGY_MAX_PLANNED_LOTS_PER_SIGNAL", 0.05,
    )
    monkeypatch.setattr(listener.journal, "event", lambda *a, **kw: None)
    monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **kw: None)

    await listener._open_extra_legs(sig, 932)

    assert len(calls) == 4
    assert len(sig.all_filled_tickets) == 5
    assert len(sig.all_filled_tickets) * sig.effective_lot == 0.05


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
    assert "Dubai Investing" in first_lines
    assert "SELL" in first_lines
    assert "+4.20" in text
    assert "2/4 posiciones abiertas" in text
    assert "4321.50" in text
    assert "4336.00" in text
    assert "Reenter now SL to 4336.00" in text
    assert "no ejecutó cambios" in text.lower()
    assert "abrir una entrada adicional" in text.lower()
    assert "REENTRY_SIGNAL" not in text
    assert "conf 0.91" not in text
    assert "n/a" not in text
    assert "Opciones" not in text
    assert len(text.splitlines()) <= 10


def test_confirmed_extra_entry_alert_uses_price_context_not_generic_advice():
    ctx = TradeContext(
        channel="canal2",
        signal_id="canal2_380",
        direction="SELL",
        entry_price=4060.0,
        tps=[4052.0],
        sl=4068.0,
        n_initial=5,
        n_open=5,
        open_tickets_pnl=[],
        floating_pnl_total=7.5,
        elapsed_min=11.0,
        current_price=4057.2,
        be_armed=False,
    )
    classification = {
        "action": "REENTRY_SIGNAL",
        "confidence": 0.98,
        "entry_direction": "SELL",
        "price": 4055.0,
        "_reason": "provider_confirmed_additional_entry",
    }

    text = listener.format_review_notification(
        ctx, classification, "I put more sell on 4055.00")

    assert "Gold Signals" in text
    assert "SELL" in text
    assert "4055.00" in text
    assert "4057.20" in text
    assert "2.20" in text
    assert "no abri" in text.lower()
    assert "SL propuesto" not in text
    assert "abrir una entrada adicional o ignorar" not in text


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
    assert sent[0].startswith(
        "⚠️ REVISIÓN NECESARIA\nGold Signals · BUY"
    )
    assert "canal2_33001" not in "\n".join(sent[0].splitlines()[:3])
    assert "Opciones" not in sent[0]
    assert "interpretar el mensaje" in sent[0].lower()
    assert events[0][1] == "ambiguous_decision_notified"


@pytest.mark.asyncio
async def test_confirmed_extra_entry_review_is_structured_for_learning(
        monkeypatch):
    events = []
    ctx = TradeContext(
        channel="canal2", signal_id="canal2_380", direction="SELL",
        entry_price=4060.0, tps=[4052.0], sl=4068.0,
        n_initial=5, n_open=5, open_tickets_pnl=[],
        floating_pnl_total=7.5, elapsed_min=11.0,
        current_price=4057.2, be_armed=False,
    )
    sig = Signal(channel="canal2", message_id=380, direction="SELL")
    monkeypatch.setattr(sig, "build_context", lambda: ctx)
    monkeypatch.setattr(listener, "notify_review_graph",
                        lambda *a, **kw: _async_result(True))
    monkeypatch.setattr(
        listener.journal, "event",
        lambda sig_id, ev, **kw: events.append((sig_id, ev, kw)))

    await listener.notify_ambiguous_decision(
        sig,
        {
            "action": "REENTRY_SIGNAL",
            "confidence": 0.98,
            "entry_direction": "SELL",
            "price": 4055.0,
            "_reason": "provider_confirmed_additional_entry",
        },
        "I put more sell on 4055.00",
    )

    event = next(row for row in events
                 if row[1] == "explicit_additional_entry_review")
    assert event[2]["provider_price"] == 4055.0
    assert event[2]["current_price"] == 4057.2
    assert event[2]["market_delta"] == 2.2
    assert event[2]["behavior"] == "notify_only"


@pytest.mark.asyncio
async def test_notify_ambiguous_decision_prefers_graph(monkeypatch):
    sent_graphs = []
    sent_texts = []
    ctx = TradeContext(
        channel="canal2", signal_id="canal2_33002", direction="BUY",
        entry_price=4310.0, tps=[4318.0], sl=4302.0,
        n_initial=5, n_open=4, open_tickets_pnl=[],
        floating_pnl_total=9.2, elapsed_min=7.2,
        current_price=4312.3, be_armed=False,
    )
    sig = Signal(channel="canal2", message_id=33002, direction="BUY")
    monkeypatch.setattr(sig, "build_context", lambda: ctx)
    monkeypatch.setattr(
        listener, "notify_review_graph",
        lambda *args, **kwargs: _async_result(sent_graphs.append(args) or True),
    )
    monkeypatch.setattr(
        listener, "notify",
        lambda text: _async_result(sent_texts.append(text)),
    )
    monkeypatch.setattr(listener.journal, "event", lambda *a, **kw: None)

    await listener.notify_ambiguous_decision(
        sig, {"action": "UNKNOWN", "confidence": 0.0}, "Protect now",
    )

    assert len(sent_graphs) == 1
    assert sent_texts == []


@pytest.mark.asyncio
async def test_notify_ambiguous_decision_falls_back_once(monkeypatch):
    sent_texts = []
    ctx = TradeContext(
        channel="canal1", signal_id="canal1_20999", direction="SELL",
        entry_price=4315.0, tps=[4310.0], sl=4322.0,
        n_initial=4, n_open=2, open_tickets_pnl=[],
        floating_pnl_total=-2.0, elapsed_min=3.0,
        current_price=4316.0, be_armed=False,
    )
    sig = Signal(channel="canal1", message_id=20999, direction="SELL")
    monkeypatch.setattr(sig, "build_context", lambda: ctx)
    monkeypatch.setattr(
        listener, "notify_review_graph",
        lambda *args, **kwargs: _async_result(False),
    )
    monkeypatch.setattr(
        listener, "notify",
        lambda text: _async_result(sent_texts.append(text)),
    )
    monkeypatch.setattr(listener.journal, "event", lambda *a, **kw: None)

    await listener.notify_ambiguous_decision(
        sig, {"action": "UNKNOWN", "confidence": 0.0}, "Unclear",
    )

    assert len(sent_texts) == 1


@pytest.mark.asyncio
async def test_notify_review_graph_uses_png_and_compact_caption(monkeypatch):
    sent = []
    events = []
    ctx = TradeContext(
        channel="canal2", signal_id="canal2_33100", direction="BUY",
        entry_price=4310.0, tps=[4318.0], sl=4302.0,
        n_initial=5, n_open=4, open_tickets_pnl=[],
        floating_pnl_total=9.2, elapsed_min=7.2,
        current_price=4312.3, be_armed=False,
    )
    sig = Signal(channel="canal2", message_id=33100, direction="BUY")
    monkeypatch.setattr(listener.config, "REVIEW_ALERT_GRAPH_ENABLED", True)
    monkeypatch.setattr(listener.config, "TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(listener.config, "REVIEW_ALERT_GRAPH_SEND_TIMEOUT_S", 8.0)
    monkeypatch.setattr(
        listener.alert_graphics, "build_live_review_image",
        lambda *args: b"\x89PNG\r\n\x1a\nimage",
    )

    async def resolve_chat():
        return 123

    def send_photo(token, chat_id, png, caption, **kwargs):
        sent.append((token, chat_id, png, caption, kwargs))
        return 88

    monkeypatch.setattr(listener, "_resolve_notify_chat_id", resolve_chat)
    monkeypatch.setattr(
        listener.telegram_notifications, "send_photo_with_caption", send_photo,
    )
    monkeypatch.setattr(
        listener.journal, "event",
        lambda sig_id, event, **kw: events.append((sig_id, event, kw)),
    )

    result = await listener.notify_review_graph(
        sig, ctx, {"action": "UNKNOWN"}, "Protect now",
    )

    assert result is True
    assert sent[0][1] == 123
    assert "Gold Signals · BUY" in sent[0][3]
    assert "canal2_33100" not in sent[0][3]
    assert sent[0][4]["timeout_s"] == 4.0
    assert events[-1][1] == "notify_graph_sent"


@pytest.mark.asyncio
async def test_notify_review_graph_returns_false_on_render_error(monkeypatch):
    events = []
    ctx = TradeContext(
        channel="canal1", signal_id="canal1_21000", direction="SELL",
        entry_price=4315.0, tps=[4310.0], sl=4322.0,
        n_initial=4, n_open=2, open_tickets_pnl=[],
        floating_pnl_total=-2.0, elapsed_min=3.0,
        current_price=4316.0, be_armed=False,
    )
    sig = Signal(channel="canal1", message_id=21000, direction="SELL")
    monkeypatch.setattr(listener.config, "REVIEW_ALERT_GRAPH_ENABLED", True)
    monkeypatch.setattr(listener.config, "TELEGRAM_BOT_TOKEN", "token")

    def fail(*args):
        raise RuntimeError("Pillow missing")

    monkeypatch.setattr(
        listener.alert_graphics, "build_live_review_image", fail,
    )
    monkeypatch.setattr(
        listener.journal, "event",
        lambda sig_id, event, **kw: events.append((sig_id, event, kw)),
    )

    result = await listener.notify_review_graph(
        sig, ctx, {"action": "UNKNOWN"}, "Unclear",
    )

    assert result is False
    assert events[-1][1] == "notify_graph_failed"


async def _async_result(value):
    return value


@pytest.mark.asyncio
async def test_execute_actions_sends_one_review_for_one_source_message(
        monkeypatch):
    notified = []
    events = []
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig_id, ev, **kw: events.append((sig_id, ev, kw)),
    )
    monkeypatch.setattr(listener.journal, "append_mgmt", lambda *a, **kw: None)

    async def fake_notify(signal, classification, raw_text):
        notified.append(classification["action"])

    monkeypatch.setattr(listener, "notify_ambiguous_decision", fake_notify)
    listener._seen_management_actions.clear()
    sig = Signal(channel="canal1", message_id=20945, direction="SELL")

    await listener._execute_actions(
        sig,
        [
            {"action": "TP_HIT_ANNOUNCEMENT", "confidence": 0.95,
             "is_optional": True, "requires_review": True},
            {"action": "PROGRESS_UPDATE", "confidence": 0.95,
             "is_optional": True, "requires_review": True},
            {"action": "OPTIONAL_SUGGESTION", "confidence": 0.90,
             "is_optional": True, "requires_review": True},
            {"action": "MARKET_COMMENTARY", "confidence": 0.95,
             "is_optional": True, "requires_review": True},
        ],
        raw_text="TP hit. You can protect or close if you prefer.",
    )

    assert notified == ["OPTIONAL_SUGGESTION"]
