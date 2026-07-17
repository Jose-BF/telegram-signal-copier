"""Tests for the human-readable production startup confirmation."""

import subprocess

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


def test_startup_status_message_warns_about_unverified_git_state():
    text = main._startup_status_message({
        "git_commit": None,
        "git_branch": "HEAD",
        "git_dirty": True,
        "git_synced": False,
    })

    assert "Version: desconocida" in text
    assert "Rama: HEAD" in text
    assert "Codigo: estado local sin verificar" in text


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
        ("git", "rev-parse", "--abbrev-ref", "HEAD"): "main",
        ("git", "status", "--porcelain"): "",
    }

    def fake_check_output(args, **_kwargs):
        return outputs[tuple(args)]

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)

    info = main._git_info()

    assert info["git_remote_commit"] == "0457a0e"
    assert info["git_synced"] is True