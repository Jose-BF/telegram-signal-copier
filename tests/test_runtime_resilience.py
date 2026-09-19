from queue import Queue
import errno
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import journal
import main
from tools import run_bot_watch as watch
from tools import start_logged_bot
import runtime_storage


@pytest.mark.asyncio
async def test_network_cable_disconnect_reconnects_without_stopping_monitors(monkeypatch):
    client = SimpleNamespace(
        run_until_disconnected=AsyncMock(side_effect=[ConnectionError("Connection to Telegram failed 5 time(s)"), None]),
        is_connected=Mock(return_value=False), connect=AsyncMock(),
    )
    monkeypatch.setattr(main, "client", client)
    monkeypatch.setattr(main.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(main.journal, "event", Mock())
    await main._run_until_disconnected_with_backoff()
    client.connect.assert_awaited_once()
    assert client.run_until_disconnected.await_count == 2


@pytest.mark.asyncio
async def test_authentication_failure_is_not_retried(monkeypatch):
    client = SimpleNamespace(run_until_disconnected=AsyncMock(side_effect=ValueError("invalid session")))
    monkeypatch.setattr(main, "client", client)
    with pytest.raises(ValueError):
        await main._run_until_disconnected_with_backoff()


def test_long_network_outage_has_bounded_backoff():
    assert main._telegram_run_backoff_seconds(100000) == main._TELEGRAM_RUN_BACKOFF_MAX_S


def test_network_route_error_is_recoverable_but_disk_full_is_not():
    assert main._recoverable_connection_error(OSError(errno.ENETUNREACH, "network unreachable"))
    assert not main._recoverable_connection_error(OSError(errno.ENOSPC, "disk full"))


def test_full_journal_queue_fails_receipt_without_blocking(monkeypatch):
    assert journal.flush_events(2)
    queue = Queue(maxsize=1)
    queue.put(object())
    monkeypatch.setattr(journal, "_event_queue", queue)
    monkeypatch.setattr(journal, "_ensure_event_writer", lambda: None)
    monkeypatch.setattr(journal, "_record_event_write_failure", Mock())
    receipt = journal.event("bot", "test_disk_stall")
    assert receipt.ready.is_set()
    assert not receipt.succeeded
    assert queue.qsize() == 1


def test_full_journal_queue_flush_respects_timeout(monkeypatch):
    assert journal.flush_events(2)
    queue = Queue(maxsize=1)
    queue.put(object())
    monkeypatch.setattr(journal, "_event_queue", queue)
    monkeypatch.setattr(journal, "_ensure_event_writer", lambda: None)
    assert journal.flush_events(timeout=0.01) is False


def test_disk_reserve_preserves_originals_and_does_not_stop_bot(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime_storage.shutil, "disk_usage", lambda path: SimpleNamespace(free=1024))
    monkeypatch.setattr(watch, "RUNTIME_DATA_DIR", tmp_path)
    monkeypatch.setattr(watch, "_local_head", lambda: "verified")
    monkeypatch.setattr(watch, "_remote_head", lambda: "verified")
    monkeypatch.setattr(watch, "_current_branch", lambda: "main")
    checkpoint = Mock(side_effect=AssertionError("must not allocate chunks"))
    monkeypatch.setattr(watch.runtime_telemetry, "checkpoint_runtime", checkpoint)
    monkeypatch.setattr(watch, "_telemetry_publish_process", None)
    source = tmp_path / "trade_events.jsonl"
    source.write_bytes(b"original evidence\n")
    assert watch._checkpoint_runtime_data().ok
    assert watch._trigger_telemetry_publication() is False
    assert source.read_bytes() == b"original evidence\n"
    checkpoint.assert_not_called()


@pytest.mark.parametrize("code", [76, 78, 79, 1])
def test_launcher_does_not_override_critical_startup_block(code):
    assert start_logged_bot.restart_delay(code, 10) is None


def test_launcher_reloads_supervisor_and_bounds_offline_retries():
    assert start_logged_bot.restart_delay(75, 1) == 10
    assert start_logged_bot.restart_delay(77, 1) == 15
    assert start_logged_bot.restart_delay(77, 100000) == 300


def test_launcher_keeps_running_across_reload_and_offline_attempts(monkeypatch, tmp_path):
    monkeypatch.setattr(start_logged_bot, "ROOT", tmp_path)
    spawn = Mock(side_effect=[75, 77, 79])
    sleep = Mock()
    monkeypatch.setattr(start_logged_bot.subprocess, "call", spawn)
    monkeypatch.setattr(start_logged_bot.time, "sleep", sleep)
    assert start_logged_bot.main() == 79
    assert spawn.call_count == 3
    assert sleep.call_count == 2
    assert len(list((tmp_path / "runtime_data").glob("watcher_*.out.log"))) == 3
