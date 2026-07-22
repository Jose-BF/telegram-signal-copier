import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_runtime_modules_share_the_explicit_runtime_directory(tmp_path):
    runtime = (tmp_path / "runtime evidence").resolve()
    env = os.environ.copy()
    env.update({
        "BOT_RUNTIME_DATA_DIR": str(runtime),
        "TELEGRAM_API_ID": "1",
        "TELEGRAM_API_HASH": "test_hash",
        "TELEGRAM_PHONE": "test_phone",
        "CANAL_1_ID": "-1001000000001",
        "CANAL_2_ID": "-1001000000002",
        "GOOGLE_API_KEY": "test_key",
        "MT5_LOGIN": "1",
        "MT5_PASSWORD": "test_password",
        "MT5_SERVER": "TestServer-Demo",
    })
    script = """
import json
import config
import journal
import pending_actions
import runtime_control
print(json.dumps({
    "events": str(journal.EVENTS_FILE),
    "journal": str(journal.JOURNAL_FILE),
    "test_events": str(journal.EVENTS_TEST_FILE),
    "test_journal": str(journal.JOURNAL_TEST_FILE),
    "heartbeat": str(config.BOT_RUNTIME_HEARTBEAT_FILE),
    "pending": str(pending_actions.PENDING_SPOOL_FILE),
    "pause": str(runtime_control.PAUSE_FILE),
    "activity": str(runtime_control.ACTIVITY_FILE),
}))
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    paths = json.loads(result.stdout.strip().splitlines()[-1])
    assert paths == {
        "events": str(runtime / "trade_events.jsonl"),
        "journal": str(runtime / "trade_journal.csv"),
        "test_events": str(runtime / "trade_events_TEST.jsonl"),
        "test_journal": str(runtime / "trade_journal_TEST.csv"),
        "heartbeat": str(runtime / "runtime_heartbeat.json"),
        "pending": str(runtime / "runtime_pending_actions.json"),
        "pause": str(runtime / "runtime_pause.json"),
        "activity": str(runtime / "runtime_handler_activity.json"),
    }


def test_orphan_finalizer_uses_journal_authoritative_path():
    source = (ROOT / "main.py").read_text(encoding="utf-8")

    assert "events_file = journal.EVENTS_FILE" in source
    assert 'Path(__file__).parent / "data" / "trade_events.jsonl"' not in source
