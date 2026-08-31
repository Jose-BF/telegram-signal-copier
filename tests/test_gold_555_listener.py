from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

import config
import listener
from state import StateManager


NOW = datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc)


def _intent(message_id: int = 380) -> listener._Canal2EntryIntent:
    return listener._Canal2EntryIntent(
        message_id=message_id,
        direction="BUY",
        parsed={"direction": "BUY"},
        raw_text="XAUUSD BUY NOW",
        entry_timestamp=NOW.replace(tzinfo=None),
        telegram_timestamp=NOW,
        source_kind="telegram_now",
        command_key="BUY_NOW",
    )


@pytest.fixture(autouse=True)
def _reset_runtime():
    listener._entry_execution_gate.reset()
    listener._canal2_opening_msg_ids.clear()
    getattr(listener, "_gold_555_entry_watches", {}).clear()
    listener._seen_edits.clear()
    listener._seen_edits_order.clear()
    yield
    listener._entry_execution_gate.reset()
    listener._canal2_opening_msg_ids.clear()
    getattr(listener, "_gold_555_entry_watches", {}).clear()
    listener._seen_edits.clear()
    listener._seen_edits_order.clear()


def _patch_registration(monkeypatch):
    events = []

    async def fake_run(fn, *args):
        return fn(*args)

    monkeypatch.setattr(listener, "state", StateManager())
    monkeypatch.setattr(config, "STRATEGY_C2_GOLD_NOW_555_ENABLED", True)
    monkeypatch.setattr(config, "STRATEGY_C2_GOLD_NOW_C490_ENABLED", False)
    monkeypatch.setattr(
        config,
        "STRATEGY_MAX_PLANNED_LOTS_PER_SIGNAL",
        0.05,
    )
    monkeypatch.setattr(
        config,
        "GOLD_555_MAX_PLANNED_LOTS_PER_SIGNAL",
        0.16,
    )
    monkeypatch.setattr(config, "LOT_SIZE", 0.01)
    monkeypatch.setattr(listener, "_run", fake_run)
    monkeypatch.setattr(
        listener.executor,
        "account_evidence",
        lambda: {
            "trade_mode": 0,
            "trade_mode_name": "demo",
            "currency": "EUR",
            "login": 123,
            "server": "Vantage-Demo",
        },
    )
    monkeypatch.setattr(
        listener.executor,
        "current_tick_safe",
        lambda: {
            "bid": 4299.8,
            "ask": 4300.0,
            "time": 1785920400,
            "time_msc": 1785920400123,
        },
    )
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )
    monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **k: None)
    return events


@pytest.mark.asyncio
async def test_555_now_signal_starts_watch_without_opening_mt5(monkeypatch) -> None:
    events = _patch_registration(monkeypatch)
    opened = []
    monkeypatch.setattr(
        listener.executor,
        "open_market_with_fill",
        lambda *args, **kwargs: opened.append((args, kwargs)),
    )

    result = await listener._open_canal2_intent(_intent())

    assert result is None
    assert opened == []
    assert 380 in listener._gold_555_entry_watches
    assert listener._canal2_open_in_progress(380)
    started = [row for row in events if row[1] == "gold_555_entry_watch_started"]
    assert len(started) == 1
    assert started[0][2]["reference_price"] == 4300.0
    assert started[0][2]["strategy_id"] == "gold_now_555_v1"


@pytest.mark.asyncio
async def test_duplicate_signal_cannot_create_second_watch(monkeypatch) -> None:
    events = _patch_registration(monkeypatch)

    await listener._open_canal2_intent(_intent())
    await listener._open_canal2_intent(_intent())

    assert list(listener._gold_555_entry_watches) == [380]
    assert len([
        row for row in events if row[1] == "gold_555_entry_watch_started"
    ]) == 1


@pytest.mark.asyncio
async def test_watch_confirms_once_after_adverse_reversal(monkeypatch) -> None:
    _patch_registration(monkeypatch)
    confirmed = []

    async def fake_open(record):
        confirmed.append(record)
        listener._canal2_open_committed(record.intent.message_id)
        return SimpleNamespace(message_id=record.intent.message_id)

    monkeypatch.setattr(listener, "_open_gold_555_confirmed_intent", fake_open)
    await listener._open_canal2_intent(_intent())

    await listener.process_gold_555_entry_tick(
        SimpleNamespace(bid=4298.7, ask=4298.9, time_msc=2),
        now=NOW + timedelta(seconds=1),
    )
    await listener.process_gold_555_entry_tick(
        SimpleNamespace(bid=4300.2, ask=4300.4, time_msc=3),
        now=NOW + timedelta(seconds=2),
    )
    await listener.process_gold_555_entry_tick(
        SimpleNamespace(bid=4300.3, ask=4300.5, time_msc=4),
        now=NOW + timedelta(seconds=3),
    )

    assert len(confirmed) == 1
    assert confirmed[0].watch.confirmed_quote == 4300.4
    assert 380 not in listener._gold_555_entry_watches


@pytest.mark.asyncio
async def test_expired_watch_releases_entry_claim(monkeypatch) -> None:
    events = _patch_registration(monkeypatch)
    await listener._open_canal2_intent(_intent())

    await listener.process_gold_555_entry_tick(
        SimpleNamespace(bid=4298.0, ask=4298.2, time_msc=2),
        now=NOW + timedelta(minutes=30),
    )

    assert 380 not in listener._gold_555_entry_watches
    assert not listener._canal2_open_in_progress(380)
    assert any(row[1] == "gold_555_entry_watch_expired" for row in events)


def test_provider_close_cancels_a_waiting_watch(monkeypatch) -> None:
    events = _patch_registration(monkeypatch)
    record = listener._Gold555PendingEntry(
        intent=_intent(),
        watch=listener.gold_555_entry_watch.EntryWatch.new(
            "BUY",
            reference=4300.0,
            observed_at=NOW,
        ),
    )
    listener._gold_555_entry_watches[380] = record
    assert listener._canal2_open_claim(380)

    handled = listener._handle_gold_555_pending_management(
        380,
        [{"action": "CLOSE_ALL", "confidence": 0.99}],
        raw_text="Close all now",
        source_message_id=381,
        tg_ts=NOW.isoformat(),
    )

    assert handled is True
    assert record.watch.status == "cancelled"
    assert 380 not in listener._gold_555_entry_watches
    assert not listener._canal2_open_in_progress(380)
    cancelled = [
        row for row in events
        if row[1] == "gold_555_entry_watch_cancelled"
    ]
    assert len(cancelled) == 1
    assert cancelled[0][2]["classified_action"] == "CLOSE_ALL"


def test_provider_close_during_confirmed_order_is_remembered(monkeypatch) -> None:
    events = _patch_registration(monkeypatch)
    record = listener._Gold555PendingEntry(
        intent=_intent(),
        watch=listener.gold_555_entry_watch.EntryWatch.new(
            "BUY",
            reference=4300.0,
            observed_at=NOW,
        ),
    )
    record.watch.status = "confirmed"
    record.order_started = True
    listener._gold_555_entry_watches[380] = record
    assert listener._canal2_open_claim(380)

    handled = listener._handle_gold_555_pending_management(
        380,
        [{"action": "CLOSE_ALL", "confidence": 0.99}],
        raw_text="Close all now",
        source_message_id=381,
        tg_ts=NOW.isoformat(),
    )

    assert handled is True
    assert record.provider_close_requested is True
    assert record.provider_close_action == "CLOSE_ALL"
    assert 380 in listener._gold_555_entry_watches
    assert any(
        row[1] == "gold_555_provider_close_during_open"
        for row in events
    )


def test_pending_555_records_provider_levels_without_changing_entry(
    monkeypatch,
) -> None:
    events = _patch_registration(monkeypatch)
    record = listener._Gold555PendingEntry(
        intent=_intent(),
        watch=listener.gold_555_entry_watch.EntryWatch.new(
            "BUY",
            reference=4300.0,
            observed_at=NOW,
        ),
    )
    listener._gold_555_entry_watches[380] = record

    handled = listener._handle_gold_555_pending_management(
        380,
        [{"action": "TP1_HIT", "confidence": 0.98}],
        raw_text="TP1 4305\nSL 4290",
        source_message_id=381,
        tg_ts=NOW.isoformat(),
    )

    assert handled is True
    assert record.watch.status == "waiting"
    assert 380 in listener._gold_555_entry_watches
    observed = next(
        fields for _, event, fields in events
        if event == "gold_555_pending_provider_context_observed"
    )
    assert observed["source_message_id"] == 381
    assert observed["classified_actions"] == ["TP1_HIT"]
    assert observed["provider_levels"]["tps"] == [4305.0]
    assert observed["provider_levels"]["sl"] == 4290.0
    assert observed["applied_to_live_entry"] is False


@pytest.mark.asyncio
async def test_reply_close_is_routed_to_waiting_watch(monkeypatch) -> None:
    events = _patch_registration(monkeypatch)
    await listener._open_canal2_intent(_intent())
    msg = SimpleNamespace(
        id=381,
        text="Close all now",
        reply_to=SimpleNamespace(reply_to_msg_id=380),
        date=NOW + timedelta(seconds=5),
    )

    async def no_zone(*_args, **_kwargs):
        return None

    async def classify_close(*_args, **_kwargs):
        return [{"action": "CLOSE_ALL", "confidence": 0.99}]

    monkeypatch.setattr(
        listener,
        "_recover_canal2_zone_plan_from_reply",
        no_zone,
    )
    monkeypatch.setattr(listener, "classify_async", classify_close)
    monkeypatch.setattr(
        listener,
        "_resolve_management_reply_target_with_ancestry",
        lambda *_args, **_kwargs: pytest.fail(
            "a pending 555 watch must be resolved before live Signal routing"
        ),
    )

    await listener._process_canal2_new(msg, dedup=False)

    assert 380 not in listener._gold_555_entry_watches
    assert any(
        row[1] == "gold_555_entry_watch_cancelled"
        for row in events
    )


@pytest.mark.asyncio
async def test_edited_reply_close_is_routed_to_waiting_watch(
    monkeypatch,
) -> None:
    events = _patch_registration(monkeypatch)
    await listener._open_canal2_intent(_intent())
    msg = SimpleNamespace(
        id=382,
        text="Close all now",
        message="Close all now",
        reply_to=SimpleNamespace(reply_to_msg_id=380),
        date=NOW + timedelta(seconds=5),
        edit_date=NOW + timedelta(seconds=6),
    )

    async def classify_close(*_args, **_kwargs):
        return [{"action": "CLOSE_ALL", "confidence": 0.99}]

    monkeypatch.setattr(listener, "classify_async", classify_close)
    monkeypatch.setattr(
        listener,
        "_resolve_management_reply_target_with_ancestry",
        lambda *_args, **_kwargs: pytest.fail(
            "an edited close must cancel the pending watch first"
        ),
    )

    handled = await listener._process_management_reply_edit(
        msg,
        "canal2",
        "Canal2_edit",
    )

    assert handled is True
    assert 380 not in listener._gold_555_entry_watches
    assert any(
        row[1] == "gold_555_entry_watch_cancelled"
        for row in events
    )


@pytest.mark.asyncio
async def test_confirmed_watch_opens_first_leg_from_real_fill(monkeypatch) -> None:
    events = _patch_registration(monkeypatch)
    monkeypatch.setattr(
        config,
        "STRATEGY_MAX_PLANNED_LOTS_PER_SIGNAL",
        0.05,
    )
    monkeypatch.setattr(
        config,
        "GOLD_555_MAX_PLANNED_LOTS_PER_SIGNAL",
        0.16,
    )
    orders = []
    sl_requests = []
    tp_requests = []
    monitor_starts = []

    def fake_open(direction, lot, sl, tp, comment, magic):
        orders.append((direction, lot, sl, tp, comment, magic))
        return 1645000001, 4300.6

    async def fake_monitor(signal):
        monitor_starts.append(signal)

    monkeypatch.setattr(listener.executor, "open_market_with_fill", fake_open)
    monkeypatch.setattr(listener.pending_actions, "enqueue_modify_sl", lambda signal, ticket, price, **kwargs: sl_requests.append((ticket, price, kwargs)))
    monkeypatch.setattr(listener.pending_actions, "enqueue_modify_tp", lambda signal, ticket, price, **kwargs: tp_requests.append((ticket, price, kwargs)))
    monkeypatch.setattr(listener, "_place_dca", fake_monitor)
    monkeypatch.setattr(listener.journal, "begin_trade", lambda *a, **k: None)
    monkeypatch.setattr(listener.logger, "log_signal", lambda *a, **k: None)
    monkeypatch.setattr(listener, "_emit_same_direction_overlap_anomaly", lambda *a, **k: None)

    await listener._open_canal2_intent(_intent())
    record = listener._gold_555_entry_watches.pop(380)
    record.watch.on_quote(
        bid=4298.7,
        ask=4298.9,
        now=NOW + timedelta(seconds=1),
        tick_msc=2,
    )
    record.watch.on_quote(
        bid=4300.2,
        ask=4300.4,
        now=NOW + timedelta(seconds=2),
        tick_msc=3,
    )

    signal = await listener._open_gold_555_confirmed_intent(record)

    assert signal is listener.state.get("canal2", 380)
    assert orders == [(
        "BUY",
        0.04,
        4270.4,
        4300.9,
        "c2_380_g55",
        config.magic_for("canal2"),
    )]
    assert signal.market_fill_price == 4300.6
    assert signal.live_strategy_id == "gold_now_555_v1"
    assert signal.candidate_entry_anchor == 4300.6
    assert signal.candidate_entry_legs[4]["trigger_price"] == 4294.6
    assert signal.candidate_filled_leg_indexes == []
    assert sl_requests[0][0:2] == (1645000001, 4270.6)
    assert sl_requests[0][2]["persist_until_signal_close"] is True
    assert tp_requests[0][0:2] == (1645000001, 4301.1)
    assert monitor_starts == [signal]
    assert listener._canal2_open_already_committed(380)
    assert any(row[1] == "gold_555_first_leg_filled" for row in events)


@pytest.mark.asyncio
async def test_fill_is_closed_when_provider_close_raced_market_order(
    monkeypatch,
) -> None:
    events = _patch_registration(monkeypatch)
    closes = []
    monitor_starts = []
    monkeypatch.setattr(
        listener.executor,
        "open_market_with_fill",
        lambda *_args, **_kwargs: (1645000002, 4300.6),
    )
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_close_position",
        lambda sig, ticket, **kwargs: closes.append((ticket, kwargs)),
    )
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_modify_sl",
        lambda *_args, **_kwargs: pytest.fail(
            "a raced provider close must not queue a new level change"
        ),
    )
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_modify_tp",
        lambda *_args, **_kwargs: pytest.fail(
            "a raced provider close must not queue a new level change"
        ),
    )

    async def fake_monitor(signal):
        monitor_starts.append(signal)

    monkeypatch.setattr(listener, "_place_dca", fake_monitor)
    monkeypatch.setattr(listener.journal, "begin_trade", lambda *a, **k: None)
    monkeypatch.setattr(listener.logger, "log_signal", lambda *a, **k: None)
    monkeypatch.setattr(
        listener,
        "_emit_same_direction_overlap_anomaly",
        lambda *a, **k: None,
    )
    record = listener._Gold555PendingEntry(
        intent=_intent(),
        watch=listener.gold_555_entry_watch.EntryWatch.new(
            "BUY",
            reference=4300.0,
            observed_at=NOW,
        ),
        order_started=True,
        provider_close_requested=True,
        provider_close_action="CLOSE_ALL",
    )
    record.watch.status = "confirmed"
    record.watch.confirmed_quote = 4300.4
    record.watch.confirmed_at = NOW + timedelta(seconds=2)

    signal = await listener._open_gold_555_confirmed_intent(record)

    assert signal.requested_close_reason == "PROVIDER_CLOSE"
    assert closes == [(
        1645000002,
        {"label": "GOLD_555_PROVIDER_CLOSE_RACE #1645000002",
         "persist_until_signal_close": True},
    )]
    assert monitor_starts == [signal]
    assert any(
        row[1] == "gold_555_provider_close_requested"
        for row in events
    )


@pytest.mark.asyncio
async def test_provider_close_during_preopen_check_cancels_without_order(
    monkeypatch,
) -> None:
    _patch_registration(monkeypatch)
    record = listener._Gold555PendingEntry(
        intent=_intent(),
        watch=listener.gold_555_entry_watch.EntryWatch.new(
            "BUY",
            reference=4300.0,
            observed_at=NOW,
        ),
    )
    record.watch.status = "confirmed"
    record.watch.confirmed_quote = 4300.4
    record.watch.confirmed_at = NOW + timedelta(seconds=2)
    listener._gold_555_entry_watches[380] = record
    assert listener._canal2_open_claim(380)
    orders = []

    async def close_during_account_check(fn, *args, **kwargs):
        if fn is listener.executor.account_evidence:
            handled = listener._handle_gold_555_pending_management(
                380,
                [{"action": "CLOSE_ALL", "confidence": 0.99}],
                raw_text="Close all now",
                source_message_id=381,
                tg_ts=NOW.isoformat(),
            )
            assert handled is True
            return {
                "trade_mode": 0,
                "trade_mode_name": "demo",
                "currency": "EUR",
                "login": 123,
                "server": "Vantage-Demo",
            }
        orders.append((fn, args, kwargs))
        pytest.fail("provider close before order dispatch must cancel entry")

    monkeypatch.setattr(listener, "_run", close_during_account_check)

    result = await listener._open_gold_555_confirmed_intent(record)

    assert result is None
    assert orders == []
    assert record.watch.status == "cancelled"
    assert 380 not in listener._gold_555_entry_watches
    assert not listener._canal2_open_in_progress(380)


def test_gold_555_cap_does_not_relax_legacy_rescue_capacity(
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "LOT_SIZE", 0.01)
    monkeypatch.setattr(
        config,
        "STRATEGY_MAX_PLANNED_LOTS_PER_SIGNAL",
        0.05,
    )
    monkeypatch.setattr(
        config,
        "GOLD_555_MAX_PLANNED_LOTS_PER_SIGNAL",
        0.16,
    )
    signal = listener.Signal(
        channel="canal2",
        message_id=999,
        direction="BUY",
        timestamp=NOW.replace(tzinfo=None),
        market_ticket=1,
        extra_market_tickets=[2, 3, 4, 5],
    )

    capacity = listener._rescue_market_capacity(signal)

    assert capacity["current_lots"] == 0.05
    assert capacity["projected_lots"] == 0.06
    assert capacity["max_lots"] == 0.05
    assert capacity["allowed"] is False


@pytest.mark.asyncio
async def test_confirmed_watch_rechecks_demo_account_before_order(monkeypatch) -> None:
    _patch_registration(monkeypatch)
    await listener._open_canal2_intent(_intent())
    record = listener._gold_555_entry_watches.pop(380)
    record.watch.on_quote(
        bid=4298.7,
        ask=4298.9,
        now=NOW + timedelta(seconds=1),
        tick_msc=2,
    )
    record.watch.on_quote(
        bid=4300.2,
        ask=4300.4,
        now=NOW + timedelta(seconds=2),
        tick_msc=3,
    )
    orders = []
    monkeypatch.setattr(
        listener.executor,
        "account_evidence",
        lambda: {
            "trade_mode": 2,
            "trade_mode_name": "real",
            "currency": "EUR",
        },
    )
    monkeypatch.setattr(
        listener.executor,
        "open_market_with_fill",
        lambda *args, **kwargs: orders.append((args, kwargs)),
    )

    with pytest.raises(RuntimeError, match="demo EUR"):
        await listener._open_gold_555_confirmed_intent(record)

    assert orders == []


def test_restart_restores_latest_unfilled_watch(tmp_path, monkeypatch) -> None:
    events = _patch_registration(monkeypatch)
    intent = _intent(381)
    watch = listener.gold_555_entry_watch.EntryWatch.new(
        "BUY",
        reference=4300.0,
        observed_at=NOW,
    )
    watch.on_quote(
        bid=4298.7,
        ask=4298.9,
        now=NOW + timedelta(seconds=1),
        tick_msc=2,
    )
    path = tmp_path / "events.jsonl"
    rows = [
        {
            "sig": "canal2_381",
            "ev": "gold_555_entry_watch_started",
            "intent": listener._gold_555_intent_payload(intent),
            "watch": listener.gold_555_entry_watch.EntryWatch.new(
                "BUY", reference=4300.0, observed_at=NOW
            ).to_dict(),
        },
        {
            "sig": "canal2_381",
            "ev": "gold_555_entry_watch_state",
            "intent": listener._gold_555_intent_payload(intent),
            "watch": watch.to_dict(),
        },
    ]
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    restored = listener.restore_gold_555_entry_watches_from_journal(
        path,
        now=NOW + timedelta(minutes=5),
    )

    assert restored == 1
    assert listener._gold_555_entry_watches[381].watch.armed is True
    assert listener._canal2_open_in_progress(381)
    assert any(row[1] == "gold_555_entry_watch_restored" for row in events)


def test_restart_does_not_restore_filled_or_expired_watch(
    tmp_path,
    monkeypatch,
) -> None:
    _patch_registration(monkeypatch)
    filled_intent = _intent(382)
    expired_intent = _intent(383)
    path = tmp_path / "events.jsonl"
    rows = [
        {
            "sig": "canal2_382",
            "ev": "gold_555_entry_watch_started",
            "intent": listener._gold_555_intent_payload(filled_intent),
            "watch": listener.gold_555_entry_watch.EntryWatch.new(
                "BUY", reference=4300.0, observed_at=NOW
            ).to_dict(),
        },
        {"sig": "canal2_382", "ev": "gold_555_first_leg_filled"},
        {
            "sig": "canal2_383",
            "ev": "gold_555_entry_watch_started",
            "intent": listener._gold_555_intent_payload(expired_intent),
            "watch": listener.gold_555_entry_watch.EntryWatch.new(
                "BUY", reference=4300.0, observed_at=NOW
            ).to_dict(),
        },
    ]
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    restored = listener.restore_gold_555_entry_watches_from_journal(
        path,
        now=NOW + timedelta(minutes=31),
    )

    assert restored == 0
    assert listener._gold_555_entry_watches == {}


def test_restart_does_not_reopen_watch_closed_while_order_was_in_flight(
    tmp_path,
    monkeypatch,
) -> None:
    _patch_registration(monkeypatch)
    intent = _intent(385)
    watch = listener.gold_555_entry_watch.EntryWatch.new(
        "BUY",
        reference=4300.0,
        observed_at=NOW,
    )
    watch.status = "confirmed"
    path = tmp_path / "events.jsonl"
    rows = [
        {
            "sig": "canal2_385",
            "ev": "gold_555_entry_watch_confirmed",
            "intent": listener._gold_555_intent_payload(intent),
            "watch": watch.to_dict(),
        },
        {
            "sig": "canal2_385",
            "ev": "gold_555_provider_close_during_open",
        },
    ]
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    restored = listener.restore_gold_555_entry_watches_from_journal(
        path,
        now=NOW + timedelta(minutes=5),
    )

    assert restored == 0
    assert listener._gold_555_entry_watches == {}


@pytest.mark.asyncio
async def test_restart_completes_confirmed_watch_exactly_once(
    tmp_path,
    monkeypatch,
) -> None:
    _patch_registration(monkeypatch)
    intent = _intent(384)
    watch = listener.gold_555_entry_watch.EntryWatch.new(
        "BUY",
        reference=4300.0,
        observed_at=NOW,
    )
    watch.on_quote(
        bid=4298.7,
        ask=4298.9,
        now=NOW + timedelta(seconds=1),
        tick_msc=2,
    )
    watch.on_quote(
        bid=4300.2,
        ask=4300.4,
        now=NOW + timedelta(seconds=2),
        tick_msc=3,
    )
    path = tmp_path / "events.jsonl"
    path.write_text(
        json.dumps({
            "sig": "canal2_384",
            "ev": "gold_555_entry_watch_confirmed",
            "intent": listener._gold_555_intent_payload(intent),
            "watch": watch.to_dict(),
        }) + "\n",
        encoding="utf-8",
    )
    opened = []

    async def fake_open(record):
        opened.append(record.intent.message_id)
        listener._canal2_open_committed(record.intent.message_id)
        return SimpleNamespace(message_id=record.intent.message_id)

    monkeypatch.setattr(
        listener,
        "_open_gold_555_confirmed_intent",
        fake_open,
    )

    assert listener.restore_gold_555_entry_watches_from_journal(
        path,
        now=NOW + timedelta(minutes=5),
    ) == 1

    first = await listener.process_gold_555_entry_tick(
        SimpleNamespace(bid=4300.2, ask=4300.4, time_msc=4),
        now=NOW + timedelta(minutes=5),
    )
    second = await listener.process_gold_555_entry_tick(
        SimpleNamespace(bid=4300.3, ask=4300.5, time_msc=5),
        now=NOW + timedelta(minutes=5, seconds=1),
    )

    assert first == 1
    assert second == 0
    assert opened == [384]
    assert 384 not in listener._gold_555_entry_watches
