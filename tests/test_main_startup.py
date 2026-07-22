"""Tests for production startup confirmation and orphan recovery."""

import subprocess
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import main


def test_startup_status_message_confirms_active_production_version():
    text = main._startup_status_message({
        "git_commit": "0457a0e",
        "git_branch": "main",
        "git_dirty": False,
        "git_synced": True,
    })

    assert "BOT ACTIVO" in text
    assert "Version: 0457a0e" in text
    assert "Rama: main" in text
    assert "Codigo: limpio y sincronizado" in text
    assert "MT5: conectado" in text
    assert "Telegram: canales 1 y 2 activos" in text
    assert "Dubai Investing:" in text
    assert "Gold Signals:" in text


def test_startup_status_message_warns_about_unverified_git_state():
    text = main._startup_status_message({
        "git_commit": None,
        "git_branch": "HEAD",
        "git_dirty": True,
        "git_synced": False,
        "git_verification_error": "sin atestacion del supervisor",
    })

    assert "Version: desconocida" in text
    assert "Rama: HEAD" in text
    assert "Codigo: estado local sin verificar" in text
    assert "Motivo: sin atestacion del supervisor" in text


def test_startup_status_message_does_not_infer_sync_from_clean_main():
    text = main._startup_status_message({
        "git_commit": "0457a0e",
        "git_branch": "main",
        "git_dirty": False,
        "git_synced": False,
    })

    assert "Codigo: estado local sin verificar" in text


def test_git_info_compares_head_with_origin_main(monkeypatch):
    outputs = {
        ("git", "rev-parse", "--short", "HEAD"): "0457a0e",
        ("git", "rev-parse", "--short", "origin/main"): "0457a0e",
        ("git", "rev-parse", "HEAD"): "0457a0e" + "1" * 33,
        ("git", "rev-parse", "origin/main"): "0457a0e" + "1" * 33,
        ("git", "rev-parse", "--abbrev-ref", "HEAD"): "main",
        ("git", "status", "--porcelain"): "",
    }

    def fake_check_output(args, **kwargs):
        assert kwargs["timeout"] == 10
        return outputs[tuple(args)]

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)

    info = main._git_info()

    assert info["git_remote_commit"] == "0457a0e"
    assert info["git_synced"] is True


def test_watcher_attestation_accepts_the_exact_verified_head():
    full_head = "a" * 40
    info = {
        "git_commit_full": full_head,
        "git_remote_commit_full": full_head,
        "git_branch": "main",
        "git_dirty": False,
    }

    assert main._watcher_attestation_error(info, full_head) is None


def test_watcher_attestation_rejects_a_direct_main_launch():
    full_head = "a" * 40
    info = {
        "git_commit_full": full_head,
        "git_remote_commit_full": full_head,
        "git_branch": "main",
        "git_dirty": False,
    }

    assert (
        main._watcher_attestation_error(info, None)
        == "sin atestacion del supervisor"
    )


def test_watcher_attestation_rejects_head_before_its_push_finishes():
    info = {
        "git_commit_full": "b" * 40,
        "git_remote_commit_full": "a" * 40,
        "git_branch": "main",
        "git_dirty": False,
    }

    reason = main._watcher_attestation_error(info, "b" * 40)

    assert "origin/main" in reason
    assert "aaaaaaaa" in reason


def test_watcher_attestation_accepts_data_only_remote_mismatch_when_authorized():
    info = {
        "git_commit_full": "b" * 40,
        "git_remote_commit_full": "a" * 40,
        "git_branch": "main",
        "git_dirty": False,
    }

    assert main._watcher_attestation_error(
        info,
        "b" * 40,
        allow_remote_mismatch=True,
    ) is None


def test_startup_status_names_verified_local_checkpoint_without_false_alarm():
    text = main._startup_status_message({
        "git_commit": "bbbbbbb",
        "git_branch": "main",
        "git_dirty": False,
        "git_synced": False,
        "git_runtime_verified": True,
    })

    assert "Codigo: verificado; datos pendientes de subir" in text
    assert "estado local sin verificar" not in text


def test_unattested_main_terminates_only_a_legacy_watcher_parent():
    class LegacyWatcher:
        def __init__(self):
            self.terminated = False

        def name(self):
            return "python.exe"

        def cmdline(self):
            return ["python.exe", "-u", r"tools\run_bot_watch.py"]

        def terminate(self):
            self.terminated = True

    parent = LegacyWatcher()

    assert main._terminate_legacy_watcher_parent(parent) is True
    assert parent.terminated is True


def test_unattested_main_does_not_terminate_a_normal_shell_parent():
    class NormalShell:
        def __init__(self):
            self.terminated = False

        def name(self):
            return "cmd.exe"

        def cmdline(self):
            return ["cmd.exe", "/c", "python main.py"]

        def terminate(self):
            self.terminated = True

    parent = NormalShell()

    assert main._terminate_legacy_watcher_parent(parent) is False
    assert parent.terminated is False


def test_orphan_history_query_end_covers_positive_broker_offset():
    now = datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc)

    query_end = main._orphan_history_query_end(now)

    assert query_end == now + timedelta(days=1)
    assert query_end > now + timedelta(hours=3)


def test_orphan_history_waits_for_expected_position_close():
    opening = SimpleNamespace(position_id=101, entry=0)
    closing = SimpleNamespace(position_id=101, entry=1)
    responses = [
        (opening,),
        (opening, closing),
        (opening, closing),
    ]
    calls = []

    def history_get(_start, _end):
        calls.append(True)
        return responses.pop(0)

    deals = main._fetch_orphan_deals_synced(
        datetime(2026, 7, 20, tzinfo=timezone.utc),
        datetime(2026, 7, 22, tzinfo=timezone.utc),
        {101},
        history_get=history_get,
        sleep_fn=lambda _seconds: None,
        retries=4,
        pause_s=0,
    )

    assert deals == (opening, closing)
    assert len(calls) == 3


def test_orphan_history_default_uses_metatrader5_module(monkeypatch):
    closing = SimpleNamespace(
        ticket=501,
        position_id=101,
        entry=1,
        time_msc=1_000,
    )
    calls = []

    def history_get(_start, _end):
        calls.append(True)
        return (closing,)

    monkeypatch.setitem(
        sys.modules,
        "MetaTrader5",
        SimpleNamespace(history_deals_get=history_get),
    )

    deals = main._fetch_orphan_deals_synced(
        datetime(2026, 7, 20, tzinfo=timezone.utc),
        datetime(2026, 7, 22, tzinfo=timezone.utc),
        {101},
        sleep_fn=lambda _seconds: None,
        retries=2,
        pause_s=0,
    )

    assert deals == (closing,)
    assert len(calls) == 2
