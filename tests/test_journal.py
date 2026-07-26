"""
test_journal.py — Suite de regresion para journal.anomaly() y health_verdict().

La capa de anomalias estructurada (categoria + severidad) sobre el journal.
Spec: docs/superpowers/specs/2026-05-19-registro-anomalias-design.md

ISOLATION: el fixture redirige EVENTS_FILE/JOURNAL_FILE a tmp_path con
monkeypatch para NO contaminar data/trade_events.jsonl real (problema
visto con test_pending_actions que escribia al journal de produccion).
"""
import json
import sys
import types
import asyncio
import threading
import time

import pytest

import causal_trace
import journal


@pytest.fixture
def isolated_journal(tmp_path, monkeypatch):
    """Redirige EVENTS_FILE/JOURNAL_FILE a tmp_path."""
    monkeypatch.setattr(journal, "EVENTS_FILE", tmp_path / "events.jsonl")
    monkeypatch.setattr(journal, "JOURNAL_FILE", tmp_path / "journal.csv")
    return tmp_path / "events.jsonl"


def _events(path):
    assert journal.flush_events(timeout=1.0) is True
    if not path.exists():
        return []
    return [json.loads(l) for l in
            path.read_text(encoding="utf-8").splitlines() if l.strip()]


# ─── anomaly() ───────────────────────────────────────────────────────────────

class TestAnomaly:

    def test_critical_notification_uses_provider_name(self):
        text = journal.format_critical_notification(
            "canal1_20955",
            "sl_be",
            "No se pudo aplicar BE",
            {"direction": "SELL", "ticket": 123},
        )

        assert "Dubai Investing" in text
        assert "Canal 1" not in text

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

    @pytest.mark.asyncio
    async def test_notify_critical_uses_running_loop(self, isolated_journal,
                                                     monkeypatch):
        calls = []

        async def fake_notify(text):
            calls.append(text)

        monkeypatch.setitem(sys.modules, "listener",
                            types.SimpleNamespace(notify=fake_notify))
        journal.set_notify_loop(None)

        journal._notify_critical("s1", "mt5", "critical issue", {"ticket": 1})
        await asyncio.sleep(0)

        assert len(calls) == 1
        assert calls[0].startswith("🚨 MT5 NECESITA ATENCIÓN")
        assert "Ticket: 1" in calls[0]
        assert "s1" not in calls[0]

    @pytest.mark.asyncio
    async def test_notify_critical_from_worker_thread_uses_registered_loop(
            self, isolated_journal, monkeypatch):
        calls = []

        async def fake_notify(text):
            calls.append(text)

        monkeypatch.setitem(sys.modules, "listener",
                            types.SimpleNamespace(notify=fake_notify))
        journal.set_notify_loop(asyncio.get_running_loop())

        await asyncio.to_thread(
            journal._notify_critical,
            "s2", "naked", "thread issue", {"ticket": 2})
        await asyncio.sleep(0.05)

        assert len(calls) == 1
        assert calls[0].startswith("🚨 OPERACIÓN SIN PROTECCIÓN")
        assert "Ticket: 2" in calls[0]
        journal.set_notify_loop(None)


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


def test_event_enqueue_does_not_wait_for_disk_lock(isolated_journal):
    acquired = threading.Event()
    release = threading.Event()

    def hold_disk_lock():
        with journal._file_lock:
            acquired.set()
            release.wait(timeout=2.0)

    holder = threading.Thread(target=hold_disk_lock)
    holder.start()
    assert acquired.wait(timeout=1.0)
    timer = threading.Timer(0.25, release.set)
    timer.start()

    started = time.perf_counter()
    journal.event("canal2_3331", "telegram_raw", message_id=3331)
    elapsed = time.perf_counter() - started

    release.set()
    holder.join(timeout=1.0)
    timer.cancel()
    assert elapsed < 0.05
    assert journal.flush_events(timeout=1.0) is True
    assert _events(isolated_journal)[0]["message_id"] == 3331


def test_event_adds_immutable_process_envelope(
        isolated_journal, monkeypatch):
    monkeypatch.setenv("BOT_WATCHER_VERIFIED_HEAD", "a" * 40)

    journal.event("canal2_380", "telegram_raw", message_id=380)
    journal.event("canal2_380", "telegram_raw", message_id=380)

    first, second = _events(isolated_journal)
    for row in (first, second):
        assert row["schema_version"] == 2
        assert row["event_id"].startswith("event_")
        assert row["session_id"].startswith("session_")
        assert isinstance(row["monotonic_ns"], int)
        assert row["monotonic_ns"] > 0
        assert row["code_commit"] == "a" * 40
        assert len(row["payload_sha256"]) == 64
        assert set(row["payload_sha256"]) <= set("0123456789abcdef")
        assert row["sig"] == "canal2_380"
        assert row["ev"] == "telegram_raw"
        assert "ts" in row

    assert first["event_id"] != second["event_id"]
    assert first["session_id"] == second["session_id"]
    assert first["monotonic_ns"] <= second["monotonic_ns"]
    assert first["payload_sha256"] == second["payload_sha256"]


def test_payload_hash_changes_when_semantic_payload_changes(
        isolated_journal):
    journal.event("canal2_380", "telegram_raw", message_id=380)
    journal.event("canal2_380", "telegram_raw", message_id=381)

    first, second = _events(isolated_journal)
    assert first["payload_sha256"] != second["payload_sha256"]


def test_event_inherits_bound_causal_context_without_overwriting_explicit(
        isolated_journal):
    with causal_trace.bind_message_revision(
        "msgrev_bound",
        decision_id="decision_bound",
    ):
        journal.event("canal2_380", "classification")
        journal.event(
            "canal2_380",
            "manual_correction",
            message_revision_id="msgrev_explicit",
            decision_id="decision_explicit",
        )

    inherited, explicit = _events(isolated_journal)
    assert inherited["message_revision_id"] == "msgrev_bound"
    assert inherited["decision_id"] == "decision_bound"
    assert explicit["message_revision_id"] == "msgrev_explicit"
    assert explicit["decision_id"] == "decision_explicit"
