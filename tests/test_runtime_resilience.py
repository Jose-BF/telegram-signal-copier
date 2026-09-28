from queue import Queue
from datetime import datetime, timedelta, timezone
from pathlib import Path
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


@pytest.mark.parametrize("backwards", [False, True])
def test_launcher_log_identity_does_not_depend_on_clock_progress(monkeypatch, tmp_path, backwards):
    fixed = datetime(2026, 9, 21, tzinfo=timezone.utc)
    clock = Mock()
    clock.now.side_effect = [fixed, fixed - timedelta(seconds=10) if backwards else fixed, fixed]
    monkeypatch.setattr(start_logged_bot, "ROOT", tmp_path)
    monkeypatch.setattr(start_logged_bot, "datetime", clock)
    monkeypatch.setattr(start_logged_bot.time, "sleep", Mock())
    calls = []

    def launch(*args, **kwargs):
        number = len(calls)
        kwargs["stdout"].write(f"output-{number}".encode())
        kwargs["stderr"].write(f"error-{number}".encode())
        calls.append(kwargs)
        return [75, 77, 79][number]

    monkeypatch.setattr(start_logged_bot.subprocess, "call", launch)
    assert start_logged_bot.main() == 79
    runtime = tmp_path / "runtime_data"
    outputs = list(runtime.glob("watcher_*.out.log"))
    assert len(outputs) == 3
    assert {path.read_bytes() for path in outputs} == {b"output-0", b"output-1", b"output-2"}
    for path in outputs:
        paired = path.with_name(path.name.removesuffix(".out.log") + ".err.log")
        assert paired.read_bytes().removeprefix(b"error-") == path.read_bytes().removeprefix(b"output-")


def test_separate_launcher_runs_preserve_existing_logs_at_same_timestamp(monkeypatch, tmp_path):
    fixed = datetime(2026, 9, 21, tzinfo=timezone.utc)
    clock = Mock()
    clock.now.return_value = fixed
    monkeypatch.setattr(start_logged_bot, "ROOT", tmp_path)
    monkeypatch.setattr(start_logged_bot, "datetime", clock)
    spawn = Mock(return_value=79)
    monkeypatch.setattr(start_logged_bot.subprocess, "call", spawn)
    runtime = tmp_path / "runtime_data"
    runtime.mkdir()
    original = runtime / ("watcher_" + fixed.strftime("%Y%m%dT%H%M%S%fZ") + ".out.log")
    original.write_bytes(b"preserved evidence")
    assert start_logged_bot.main() == 79
    assert start_logged_bot.main() == 79
    assert original.read_bytes() == b"preserved evidence"
    assert len(list(runtime.glob("watcher_*.out.log"))) == 3
    assert len(list(runtime.glob("watcher_*.err.log"))) == 2
    assert spawn.call_count == 2


@pytest.mark.parametrize("suffix", [".out.log", ".err.log"])
def test_launcher_retries_log_collision_without_overwriting_or_extra_launches(monkeypatch, tmp_path, suffix):
    fixed = datetime(2026, 9, 21, tzinfo=timezone.utc)
    clock = Mock()
    clock.now.return_value = fixed
    monkeypatch.setattr(start_logged_bot, "ROOT", tmp_path)
    monkeypatch.setattr(start_logged_bot, "datetime", clock)
    identifiers = Mock(side_effect=[SimpleNamespace(hex="collision"), SimpleNamespace(hex="fresh")])
    monkeypatch.setattr(start_logged_bot, "uuid4", identifiers)
    spawn = Mock(return_value=79)
    monkeypatch.setattr(start_logged_bot.subprocess, "call", spawn)
    runtime = tmp_path / "runtime_data"
    runtime.mkdir()
    prefix = "watcher_" + fixed.strftime("%Y%m%dT%H%M%S%fZ")
    original = runtime / (prefix + "_collision" + suffix)
    original.write_bytes(b"preserved evidence")
    assert start_logged_bot.main() == 79
    assert original.read_bytes() == b"preserved evidence"
    assert identifiers.call_count == 2
    assert spawn.call_count == 1
    for suffix in (".out.log", ".err.log"):
        assert (runtime / (prefix + "_fresh" + suffix)).exists()
    assert spawn.call_args.kwargs["stdout"].closed
    assert spawn.call_args.kwargs["stderr"].closed


def test_log_collision_retries_are_bounded(monkeypatch, tmp_path):
    fixed = datetime(2026, 9, 21, tzinfo=timezone.utc)
    clock = Mock()
    clock.now.return_value = fixed
    monkeypatch.setattr(start_logged_bot, "ROOT", tmp_path)
    monkeypatch.setattr(start_logged_bot, "datetime", clock)
    identifiers = Mock(return_value=SimpleNamespace(hex="collision"))
    monkeypatch.setattr(start_logged_bot, "uuid4", identifiers)
    spawn = Mock()
    monkeypatch.setattr(start_logged_bot.subprocess, "call", spawn)
    runtime = tmp_path / "runtime_data"
    runtime.mkdir()
    original = runtime / ("watcher_" + fixed.strftime("%Y%m%dT%H%M%S%fZ") + "_collision.out.log")
    original.write_bytes(b"preserved")
    with pytest.raises(FileExistsError, match="budget exhausted"):
        start_logged_bot.main()
    assert identifiers.call_count == start_logged_bot._LOG_NAME_ATTEMPTS
    assert original.read_bytes() == b"preserved"
    spawn.assert_not_called()


def test_log_permission_error_is_not_retried_and_closes_first_file(monkeypatch, tmp_path):
    monkeypatch.setattr(start_logged_bot, "ROOT", tmp_path)
    opened = []
    real_open = Path.open

    def open_log(path, *args, **kwargs):
        if path.name.endswith(".err.log"):
            raise PermissionError("denied")
        stream = real_open(path, *args, **kwargs)
        opened.append(stream)
        return stream

    monkeypatch.setattr(Path, "open", open_log)
    spawn = Mock()
    monkeypatch.setattr(start_logged_bot.subprocess, "call", spawn)
    with pytest.raises(PermissionError):
        start_logged_bot.main()
    assert len(opened) == 1 and opened[0].closed
    spawn.assert_not_called()


def test_child_launch_file_error_is_not_treated_as_log_collision(monkeypatch, tmp_path):
    monkeypatch.setattr(start_logged_bot, "ROOT", tmp_path)
    spawn = Mock(side_effect=FileExistsError("child launch failed"))
    monkeypatch.setattr(start_logged_bot.subprocess, "call", spawn)
    with pytest.raises(FileExistsError, match="child launch failed"):
        start_logged_bot.main()
    assert spawn.call_count == 1
    assert spawn.call_args.kwargs["stdout"].closed
    assert spawn.call_args.kwargs["stderr"].closed
