from pathlib import Path


def test_final_watcher_exit_backup_regenerates_and_tracks_ledger():
    text = Path("run_bot.bat").read_text(encoding="utf-8")

    assert "_regenerate_ledger()" in text
    assert "_clear_mutable_offline_outputs()" in text
    assert "_regenerate_provider_signal_catalog()" in text
    assert "_regenerate_strategy_farm()" in text
    assert r"data\ledger.jsonl" in text
    assert r"data\reconcile_status.json" in text
    assert r"git add -f data\provider_signal_catalog.json" in text
    assert r"data\strategy_farm.json" in text
    assert r"data\simulation_runs" in text
