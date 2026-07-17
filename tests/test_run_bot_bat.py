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


def test_hard_git_block_stops_after_failed_recovery_backup():
    text = Path("run_bot.bat").read_text(encoding="utf-8")

    assert 'set BACKUPCODE=%errorlevel%' in text
    assert 'if not "%BACKUPCODE%"=="0" goto backup_failed' in text
    assert ':backup_failed' in text
    assert 'exit /b 76' in text


def test_transient_git_failure_retries_slowly_without_starting_old_code():
    text = Path("run_bot.bat").read_text(encoding="utf-8")

    assert 'if "%EXITCODE%"=="77" goto retry_wait' in text
    assert ':retry_wait' in text
    assert 'timeout /t 60 /nobreak' in text
