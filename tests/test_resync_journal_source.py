"""The orphan resync must not scan a huge journal (29/09/2026 restart loop)."""
import json

import main


def _write(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_small_journal_is_read_as_is(tmp_path, monkeypatch):
    j = tmp_path / "trade_events.jsonl"
    _write(j, [{"sig": "canal1_1", "ev": "x"}])
    monkeypatch.setattr(main, "STARTUP_JOURNAL_RESTORE_MAX_SCAN_BYTES", 10_000)
    assert main._resync_journal_source(j, ["canal1_1"]) == j


def test_large_journal_uses_filtered_recent_tail(tmp_path, monkeypatch):
    j = tmp_path / "trade_events.jsonl"
    old = [{"sig": "canal1_7", "ev": "old", "pad": "x" * 200}]
    filler = [{"sig": "canal2_99", "ev": "noise", "pad": "y" * 200} for _ in range(200)]
    recent = [{"sig": "canal1_7", "ev": "recent"}, {"sig": "canal2_5", "ev": "recent5"}]
    _write(j, old + filler + recent + filler[:3])
    monkeypatch.setattr(main, "STARTUP_JOURNAL_RESTORE_MAX_SCAN_BYTES", 1000)
    monkeypatch.setattr(main, "RESYNC_JOURNAL_TAIL_BYTES", 5000)
    src = main._resync_journal_source(j, ["canal1_7", "canal2_5"])
    assert src != j and src.exists()
    evs = [json.loads(l)["ev"] for l in src.read_text(encoding="utf-8").splitlines()]
    assert evs == ["recent", "recent5"]          # old line is outside the tail, noise filtered out


def test_large_journal_without_targets_skips_metadata(tmp_path, monkeypatch):
    j = tmp_path / "trade_events.jsonl"
    _write(j, [{"sig": "canal2_99", "pad": "y" * 500}] * 10)
    monkeypatch.setattr(main, "STARTUP_JOURNAL_RESTORE_MAX_SCAN_BYTES", 100)
    assert main._resync_journal_source(j, []) is None
