"""
test_journal.py — Suite de regresion para journal.anomaly() y health_verdict().

La capa de anomalias estructurada (categoria + severidad) sobre el journal.
Spec: docs/superpowers/specs/2026-05-19-registro-anomalias-design.md

ISOLATION: el fixture redirige EVENTS_FILE/JOURNAL_FILE a tmp_path con
monkeypatch para NO contaminar data/trade_events.jsonl real (problema
visto con test_pending_actions que escribia al journal de produccion).
"""
import json
import csv
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
    journal._reset_critical_notify_rate_limit()
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
    async def test_repeated_critical_keeps_both_logs_but_notifies_once(
        self,
        isolated_journal,
        monkeypatch,
    ):
        calls = []

        async def fake_notify(text):
            calls.append(text)

        monkeypatch.setitem(
            sys.modules,
            "listener",
            types.SimpleNamespace(notify=fake_notify),
        )
        journal.set_notify_loop(None)

        for _ in range(2):
            journal.anomaly(
                "canal1_100",
                "mt5",
                "critical",
                "MT5 no responde",
                ticket=123,
            )
        await asyncio.sleep(0)

        rows = _events(isolated_journal)
        assert len([row for row in rows if row["ev"] == "anomaly"]) == 2
        assert len([
            row for row in rows
            if row["ev"] == "critical_notify_suppressed"
        ]) == 1
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_distinct_tickets_are_not_hidden_by_critical_cooldown(
        self,
        isolated_journal,
        monkeypatch,
    ):
        calls = []

        async def fake_notify(text):
            calls.append(text)

        monkeypatch.setitem(
            sys.modules,
            "listener",
            types.SimpleNamespace(notify=fake_notify),
        )
        journal.set_notify_loop(None)

        for ticket in (123, 456):
            journal.anomaly(
                "canal1_100",
                "mt5",
                "critical",
                "MT5 no responde",
                ticket=ticket,
                retcode=10016,
            )
        await asyncio.sleep(0)

        rows = _events(isolated_journal)
        assert not any(
            row["ev"] == "critical_notify_suppressed" for row in rows
        )
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_price_noise_does_not_bypass_critical_cooldown(
        self,
        isolated_journal,
        monkeypatch,
    ):
        calls = []

        async def fake_notify(text):
            calls.append(text)

        monkeypatch.setitem(
            sys.modules,
            "listener",
            types.SimpleNamespace(notify=fake_notify),
        )
        journal.set_notify_loop(None)

        for price in (4300.0, 4300.2):
            journal.anomaly(
                "canal1_100",
                "mt5",
                "critical",
                "No se pudo proteger la posicion",
                ticket=123,
                retcode=10016,
                price=price,
            )
        await asyncio.sleep(0)

        assert len(calls) == 1

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


def test_event_never_raises_when_a_field_cannot_be_serialized(
        isolated_journal):
    journal.event("canal2_380", "unserializable", value=object())

    assert journal.flush_events(timeout=1.0) is False
    assert journal.flush_events(timeout=1.0) is True
    assert _events(isolated_journal) == []


def test_flush_reports_a_queued_disk_write_failure(
        isolated_journal, monkeypatch):
    def fail_open(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(journal, "open", fail_open, raising=False)
    journal.event("canal2_380", "telegram_raw", message_id=380)

    assert journal.flush_events(timeout=1.0) is False

    monkeypatch.delattr(journal, "open", raising=False)
    assert journal.flush_events(timeout=1.0) is True


def test_event_receipts_confirm_each_write_independently(
        isolated_journal, monkeypatch):
    real_open = open
    calls = 0

    def fail_first_open(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("first write failed")
        return real_open(*args, **kwargs)

    monkeypatch.setattr(journal, "open", fail_first_open, raising=False)
    failed = journal.event(
        "canal2_380",
        "telegram_raw",
        message_id=380,
    )
    written = journal.event(
        "canal2_381",
        "telegram_raw",
        message_id=381,
    )

    assert journal.confirm_event(written, timeout=1.0) is True
    assert journal.confirm_event(failed, timeout=1.0) is False
    rows = [
        json.loads(line)
        for line in isolated_journal.read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert [row["sig"] for row in rows] == ["canal2_381"]
    assert journal.flush_events(timeout=1.0) is False
    assert journal.flush_events(timeout=1.0) is True


def test_payload_hash_serializes_sets_in_stable_order():
    assert journal._serialize({"b", "a"}) == ["a", "b"]


def test_optional_management_suggestion_is_not_tagged_as_ignored_loss():
    row = {
        "closed_by": "SL",
        "total_pnl_usd": -50.50,
        "mfe_usd": 1.0,
        "mae_usd": -50.50,
        "mgmt_msgs_classified": ["CLOSE_ALL_NOTIFY_REVIEW"],
        "mgmt_msgs_applied": [False],
        "mgmt_msgs_required": [False],
    }

    assert journal._auto_tag(row) == "LOSS_CLEAN"


def test_unexecuted_required_management_order_remains_visible_in_loss_tag():
    row = {
        "closed_by": "SL",
        "total_pnl_usd": -20.0,
        "mfe_usd": 6.0,
        "mae_usd": -20.0,
        "mgmt_msgs_classified": ["MOVE_SL_TO_BE_LOWCONF"],
        "mgmt_msgs_applied": [False],
        "mgmt_msgs_required": [True],
    }

    assert journal._auto_tag(row) == "LOSS_MGMT_IGNORED"


def test_required_management_metadata_does_not_change_live_csv_schema(
        isolated_journal):
    signal_id = "canal1_21182_csv"
    journal.begin_trade(
        signal_id, channel="canal1", direction="SELL")
    journal.append_mgmt(
        signal_id, classified="CLOSE_ALL_NOTIFY_REVIEW",
        applied=False, required=False)
    journal.finalize_trade(
        signal_id, closed_by="SL", total_pnl_usd=-50.50,
        closed_at_utc="2026-07-30T15:30:00+00:00",
        duration_sec=60.0)

    with journal.JOURNAL_FILE.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        row = next(reader)

    assert "mgmt_msgs_required" not in reader.fieldnames
    assert None not in row
    assert row["tag"] == "LOSS_CLEAN"
