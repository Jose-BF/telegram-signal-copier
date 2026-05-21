"""
test_journal.py — Suite de regresion para journal.anomaly() y health_verdict().

La capa de anomalias estructurada (categoria + severidad) sobre el journal.
Spec: docs/superpowers/specs/2026-05-19-registro-anomalias-design.md

ISOLATION: el fixture redirige EVENTS_FILE/JOURNAL_FILE a tmp_path con
monkeypatch para NO contaminar data/trade_events.jsonl real (problema
visto con test_pending_actions que escribia al journal de produccion).
"""
import json

import pytest

import journal


@pytest.fixture
def isolated_journal(tmp_path, monkeypatch):
    """Redirige EVENTS_FILE/JOURNAL_FILE a tmp_path."""
    monkeypatch.setattr(journal, "EVENTS_FILE", tmp_path / "events.jsonl")
    monkeypatch.setattr(journal, "JOURNAL_FILE", tmp_path / "journal.csv")
    return tmp_path / "events.jsonl"


def _events(path):
    if not path.exists():
        return []
    return [json.loads(l) for l in
            path.read_text(encoding="utf-8").splitlines() if l.strip()]


# ─── anomaly() ───────────────────────────────────────────────────────────────

class TestAnomaly:

    def test_writes_schema(self, isolated_journal):
        journal.anomaly("canal1_12345", "naked", "critical",
                        "position opened without SL", ticket=999)
        ev = _events(isolated_journal)[0]
        assert ev["ev"] == "anomaly"
        assert ev["sig"] == "canal1_12345"
        assert ev["category"] == "naked"
        assert ev["severity"] == "critical"
        assert ev["detail"] == "position opened without SL"
        assert ev["ticket"] == 999

    def test_rejects_invalid_severity(self, isolated_journal):
        with pytest.raises(ValueError, match="severity"):
            journal.anomaly("s1", "naked", "OOPS", "x")

    def test_rejects_invalid_category(self, isolated_journal):
        with pytest.raises(ValueError, match="category"):
            journal.anomaly("s1", "OOPS", "info", "x")

    def test_critical_triggers_notify(self, isolated_journal, monkeypatch):
        """severity='critical' debe invocar _notify_critical con (sig, cat, det, ctx)."""
        calls = []
        monkeypatch.setattr(
            journal, "_notify_critical",
            lambda sig, cat, det, ctx: calls.append((sig, cat, det, ctx)))
        journal.anomaly("s1", "naked", "critical", "no SL", ticket=42)
        assert calls == [("s1", "naked", "no SL", {"ticket": 42})]

    def test_warning_does_not_trigger_notify(self, isolated_journal,
                                              monkeypatch):
        calls = []
        monkeypatch.setattr(journal, "_notify_critical",
                            lambda *a: calls.append(a))
        journal.anomaly("s1", "sl_be", "warning", "BE imposible")
        assert calls == []

    def test_info_does_not_trigger_notify(self, isolated_journal,
                                           monkeypatch):
        calls = []
        monkeypatch.setattr(journal, "_notify_critical",
                            lambda *a: calls.append(a))
        journal.anomaly("s1", "channel_msg", "info", "reply a senal cerrada")
        assert calls == []

    def test_outcome_category_is_valid_for_trade_results(self, isolated_journal):
        journal.anomaly("canal1_19822", "outcome", "warning",
                        "time-stop notify-only fired", pl=-41.9)
        ev = _events(isolated_journal)[0]
        assert ev["category"] == "outcome"
        assert ev["severity"] == "warning"
        assert ev["pl"] == -41.9


# ─── health_verdict() ────────────────────────────────────────────────────────

class TestHealthVerdict:

    def test_empty_is_ok(self):
        assert journal.health_verdict([]) == "ok"

    def test_only_info_is_ok(self):
        assert journal.health_verdict([{"severity": "info"}]) == "ok"
        assert journal.health_verdict([
            {"severity": "info"}, {"severity": "info"}]) == "ok"

    def test_warning_is_degraded(self):
        assert journal.health_verdict([{"severity": "warning"}]) == "degraded"
        assert journal.health_verdict([
            {"severity": "info"}, {"severity": "warning"}]) == "degraded"

    def test_critical_is_failed(self):
        assert journal.health_verdict([{"severity": "critical"}]) == "failed"
        assert journal.health_verdict([
            {"severity": "warning"}, {"severity": "critical"}]) == "failed"
        assert journal.health_verdict([
            {"severity": "info"}, {"severity": "warning"},
            {"severity": "critical"}]) == "failed"
