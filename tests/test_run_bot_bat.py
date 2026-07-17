from pathlib import Path


def test_final_backup_delegates_to_watcher_cli_only():
    text = Path("run_bot.bat").read_text(encoding="utf-8")

    assert r"python -u tools\run_bot_watch.py --final-backup" in text
    assert "git pull --rebase" not in text
    assert "git push origin main" not in text
    assert "python -c \"import tools.run_bot_watch" not in text


def test_batch_still_relaunches_after_controlled_watcher_exit():
    text = Path("run_bot.bat").read_text(encoding="utf-8")

    assert "Reiniciando en 10 segundos" in text
    assert "goto restart" in text

def test_restart_banner_keeps_batch_commands_on_separate_lines():
    text = Path("run_bot.bat").read_text(encoding="utf-8").replace("\r\n", "\n")

    assert "echo.echo" not in text
    assert "\necho.\necho Reiniciando en 10 segundos..." in text