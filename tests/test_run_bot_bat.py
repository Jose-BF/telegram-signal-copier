from pathlib import Path


def test_final_watcher_exit_backup_regenerates_and_tracks_ledger():
    text = Path("run_bot.bat").read_text(encoding="utf-8")

    assert "_regenerate_ledger()" in text
    assert r"data\ledger.jsonl" in text
    assert r"data\reconcile_status.json" in text
