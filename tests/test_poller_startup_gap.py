from datetime import datetime, timedelta, timezone
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import listener


@pytest.fixture
def gap_scan(monkeypatch):
    now = [100.0]
    cutoff = datetime(2026, 9, 9, tzinfo=timezone.utc)
    messages = [
        SimpleNamespace(id=msg_id, date=cutoff + timedelta(minutes=msg_id),
                        edit_date=None)
        for msg_id in (3, 2)
    ]
    client = SimpleNamespace(get_messages=AsyncMock(return_value=messages))
    notify = AsyncMock()
    anomaly = Mock()
    coverage = Mock()
    for name in (
        "_poller_history_backoff_until", "_poller_history_failures",
        "_poller_access_backoff_until", "_poller_access_failures",
        "_poller_startup_gap_retry_after", "_poller_msg_state",
    ):
        monkeypatch.setattr(listener, name, {}, raising=False)
    monkeypatch.setattr(listener, "_poller_initialized_channels", set())
    monkeypatch.setattr(listener, "_poller_now_monotonic", lambda: now[0])
    monkeypatch.setattr(listener, "_POLL_STARTUP_SCAN_LIMIT", 2)
    monkeypatch.setattr(listener, "_POLL_STARTUP_MAX_MESSAGES", 2)
    monkeypatch.setattr(listener, "client", client)
    monkeypatch.setattr(listener, "notify", notify)
    monkeypatch.setattr(listener.journal, "anomaly", anomaly)
    monkeypatch.setattr(listener.journal, "event", Mock())
    monkeypatch.setattr(listener, "_poller_record_coverage", coverage)
    monkeypatch.setattr(listener, "_poller_dispatch_message", AsyncMock())
    monkeypatch.setattr(listener, "_load_poller_startup_history", lambda *args: {
        "has_channel_history": True, "coverage_cutoff": cutoff,
        "processing_contract_utc": cutoff, "message_versions": {},
    })
    return SimpleNamespace(now=now, client=client, notify=notify,
                           anomaly=anomaly, coverage=coverage)


@pytest.mark.asyncio
async def test_incomplete_startup_uses_only_rate_limited_alert(gap_scan):
    assert await listener._poller_initial_scan_channel(2, "canal2") is False

    gap_scan.notify.assert_not_awaited()
    gap_scan.anomaly.assert_called_once()
    assert gap_scan.anomaly.call_args.args[1:3] == ("channel_msg", "critical")
    assert gap_scan.anomaly.call_args.kwargs["channel"] == "canal2"
    gap_scan.coverage.assert_not_called()
    listener._poller_dispatch_message.assert_not_awaited()
    assert "canal2" not in listener._poller_initialized_channels


@pytest.mark.asyncio
async def test_incomplete_startup_does_not_repeat_full_scan_each_cycle(gap_scan):
    assert await listener._poller_initial_scan_channel(2, "canal2") is False
    for elapsed in (0.5, 20.0, 30.0, 299.9):
        gap_scan.now[0] = 100.0 + elapsed
        assert await listener._poller_initial_scan_channel(2, "canal2") is False
    assert gap_scan.client.get_messages.await_count == 1
    assert gap_scan.anomaly.call_count == 1

    gap_scan.now[0] = 400.0
    assert await listener._poller_initial_scan_channel(2, "canal2") is False
    assert gap_scan.client.get_messages.await_count == 2
    assert gap_scan.anomaly.call_count == 2
    gap_scan.coverage.assert_not_called()


@pytest.mark.asyncio
async def test_gap_backoff_is_channel_local_and_clears_on_recovery(gap_scan):
    assert await listener._poller_initial_scan_channel(2, "canal2") is False
    gap_scan.client.get_messages.return_value = []
    assert await listener._poller_initial_scan_channel(1, "canal1") is True
    assert "canal2" not in listener._poller_initialized_channels

    gap_scan.now[0] = 400.0
    assert await listener._poller_initial_scan_channel(2, "canal2") is True
    assert "canal2" in listener._poller_initialized_channels
    assert "canal2" not in listener._poller_startup_gap_retry_after
    assert gap_scan.coverage.call_count == 2


@pytest.mark.asyncio
async def test_startup_history_does_not_block_the_telegram_event_loop(gap_scan, monkeypatch):
    event_loop_thread = threading.get_ident()
    observed_threads = []

    def load_history(*args):
        observed_threads.append(threading.get_ident())
        return {"has_channel_history": False}

    monkeypatch.setattr(listener, "_load_poller_startup_history", load_history)
    gap_scan.client.get_messages.return_value = []
    assert await listener._poller_initial_scan_channel(2, "canal2") is True
    assert observed_threads and observed_threads[0] != event_loop_thread


@pytest.mark.parametrize("equivalent", [True, False])
def test_processed_equivalent_edit_does_not_pin_recovery_to_old_revision(
    tmp_path, equivalent,
):
    old_date = "2026-07-29T20:49:33+00:00"
    edit_date = "2026-07-29T20:49:48+00:00"
    current_cutoff = "2026-09-09T07:00:00+00:00"
    common = dict(channel="canal2", chat_id=2, message_id=642)
    raw = dict(
        **common, ev="telegram_raw", date_utc=old_date,
        edit_date_utc=None, text="Close first entries",
        has_media=False, has_photo=False, has_document=False,
        sticker_id=None, is_reply=True, reply_to_msg_id=641,
    )
    rows = [
        dict(ev="telegram_processing_contract", channel="canal2", channel_id=2,
             activated_utc="2026-07-22T00:00:00+00:00"),
        dict(raw, ts=old_date),
        dict(raw, ts=edit_date, edit_date_utc=edit_date,
             text=raw["text"] if equivalent else "Do not close"),
        dict(**common, ev="telegram_processed", ts=edit_date,
             revision_token=edit_date),
        dict(ev="telegram_poll_coverage", channel="canal2", channel_id=2,
             covered_through_utc=current_cutoff),
    ]
    path = tmp_path / "events.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    history = listener._load_poller_startup_history("canal2", 2, path=path)

    assert (642, "new") in history["unprocessed_revisions"]
    assert (642, "new") not in history["processed_revisions"]
    expected = (datetime.fromisoformat(current_cutoff) if equivalent else
                datetime.fromisoformat(old_date) - timedelta(seconds=120))
    assert history["coverage_cutoff"] == expected
    if equivalent:
        assert history["equivalent_processed_revisions"] == {(642, "new"): (642, edit_date)}


@pytest.mark.parametrize("variant", ["media", "reply", "missing_text", "unconfirmed", "older_edit", "conflicting_raw"])
def test_unproven_revision_equivalence_keeps_original_gap(tmp_path, variant):
    old_date = "2026-07-29T20:49:33+00:00"
    first_edit = "2026-07-29T20:49:40+00:00"
    later_edit = "2026-07-29T20:49:48+00:00"
    common = dict(channel="canal2", chat_id=2, message_id=642)
    raw = dict(**common, ev="telegram_raw", ts=first_edit, date_utc=old_date,
               edit_date_utc=first_edit, text="Close first entries", has_media=False,
               has_photo=False, has_document=False, sticker_id=None,
               is_reply=True, reply_to_msg_id=641)
    newer = dict(raw, ts=later_edit, edit_date_utc=later_edit)
    if variant == "media":
        raw["has_media"] = newer["has_media"] = True
    elif variant == "reply":
        newer["reply_to_msg_id"] = 640
    elif variant == "missing_text":
        raw.pop("text")
    elif variant == "older_edit":
        newer["edit_date_utc"] = old_date
    rows = [
        dict(ev="telegram_processing_contract", channel="canal2", channel_id=2,
             activated_utc="2026-07-22T00:00:00+00:00"),
        raw, newer,
        dict(ev="telegram_poll_coverage", channel="canal2", channel_id=2,
             covered_through_utc="2026-09-09T07:00:00+00:00"),
    ]
    if variant != "unconfirmed":
        rows.append(dict(**common, ev="telegram_processed", ts=later_edit,
                         revision_token=newer["edit_date_utc"]))
    if variant == "conflicting_raw":
        rows.append(dict(raw, text="Different recorded contents"))
    path = tmp_path / "events.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    history = listener._load_poller_startup_history("canal2", 2, path=path)
    assert history["coverage_cutoff"] == datetime.fromisoformat(old_date) - timedelta(seconds=120)
