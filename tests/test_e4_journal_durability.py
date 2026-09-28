import asyncio
import threading
import time
from queue import Full

import pytest

import journal


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    journal.flush_events()
    monkeypatch.setattr(journal, "EVENTS_FILE", tmp_path / "events.jsonl")
    monkeypatch.setattr(journal, "_event_write_failures", 0)
    monkeypatch.setattr(journal, "_event_acknowledged_failures", 0)
    monkeypatch.setattr(journal, "_storage_observation", None, raising=False)
    monkeypatch.setattr(journal, "_telemetry_suppressed", {})
    monkeypatch.setattr(journal, "_telemetry_gap_started", None)
    monkeypatch.setattr(journal, "_telemetry_gap_last", None)
    yield tmp_path
    journal.flush_events()


def test_confirmed_event_is_synced_to_disk(isolated, monkeypatch):
    calls = []
    original = journal.os.fsync

    def sync(fd):
        calls.append(threading.get_ident())
        original(fd)

    monkeypatch.setattr(journal.os, "fsync", sync)
    receipt = journal.event("canal1_1", "durability_probe")
    assert journal.confirm_event(receipt, timeout=2)
    assert calls, "confirmed evidence must be fsynced, not just closed"
    assert all(thread != threading.get_ident() for thread in calls)


def test_confirmation_reports_fsync_failure(isolated, monkeypatch):
    receipt = journal.event("canal1_2", "durability_probe")
    assert receipt.ready.wait(2)

    def fail_sync(fd):
        raise OSError("simulated disk full during sync")

    with monkeypatch.context() as context:
        context.setattr(journal.os, "fsync", fail_sync)
        assert not journal.confirm_event(receipt, timeout=2)


def test_flush_syncs_unconfirmed_events(isolated, monkeypatch):
    calls = []
    original = journal.os.fsync

    def sync(fd):
        calls.append(fd)
        original(fd)

    monkeypatch.setattr(journal.os, "fsync", sync)
    journal.event("canal1_3", "unconfirmed")
    assert journal.flush_events(timeout=2)
    assert calls


def test_failed_event_cannot_be_confirmed_by_another_success(isolated, monkeypatch):
    def fail_open(*args, **kwargs):
        raise OSError("simulated write failure")

    with monkeypatch.context() as context:
        context.setattr(journal, "open", fail_open, raising=False)
        failed = journal.event("canal1_4", "failed")
        assert failed.ready.wait(2)
    written = journal.event("canal1_5", "success")
    assert journal.confirm_event(written, timeout=2)
    assert not journal.confirm_event(failed, timeout=2)


def test_flushing_does_not_erase_a_failed_write_for_entry_admission(isolated, monkeypatch):
    monkeypatch.setattr(journal, "_event_write_failures", 1)
    assert not journal.flush_events(timeout=2)
    assert journal.flush_events(timeout=2)
    assert not journal.persistence_health()["allow_new_entries"]
    with pytest.raises(journal.EvidenceUnavailable):
        journal.assert_entry_evidence_ready()


def test_busy_writer_is_visible_without_blocking_for_its_file_lock(isolated, monkeypatch):
    monkeypatch.setattr(journal, "_event_active_since", time.monotonic() - 20)
    monkeypatch.setattr(journal, "_event_active_kind", "sync")
    with journal._file_lock:
        health = journal.persistence_health()
        assert not health["allow_new_entries"]
        assert health["active_kind"] == "sync"


@pytest.mark.asyncio
async def test_real_entry_probe_has_exact_request_identity_and_sync(isolated, monkeypatch):
    import json
    from tests.test_mt5_trade_client import trade_request

    synced = []
    original = journal.os.fsync

    def sync(fd):
        original(fd)
        synced.append(fd)

    monkeypatch.setattr(journal.os, "fsync", sync)
    request = trade_request()
    assert await journal.confirm_entry_intent(request, timeout=2)
    record = json.loads(journal.EVENTS_FILE.read_text(encoding="utf-8").splitlines()[-1])
    assert record["ev"] == "entry_admission_evidence"
    assert record["broker_request"] == request.to_dict()
    assert synced


def test_low_space_heartbeat_blocks_entries_but_preserves_essential_events(isolated, monkeypatch):
    import main

    monkeypatch.setattr(main.runtime_storage, "storage_health", lambda path: {
        "free_bytes": 1024, "warning": True, "allow_telemetry": False,
    })
    monkeypatch.setattr(main, "_runtime_exposure_snapshot", lambda: {"flat": True})
    main._write_runtime_heartbeat(isolated / "heartbeat.json")
    assert not journal.persistence_health()["allow_new_entries"]
    optional = journal.event("canal1_1", "audit_snapshot", floating_pl=-1)
    essential = journal.event("canal1_1", "protective_sl_applied", ticket=42)
    assert not journal.confirm_event(optional, timeout=2)
    assert journal.confirm_event(essential, timeout=2)


def test_storage_recovery_keeps_explicit_gap_and_does_not_invent_snapshots(isolated):
    import json

    journal.observe_storage_health({"free_bytes": 0, "allow_telemetry": False})
    first = journal.event("canal1_1", "audit_snapshot", floating_pl=-10)
    assert not journal.confirm_event(first, timeout=1)
    journal.observe_storage_health({"free_bytes": 10 * 1024 ** 3, "allow_telemetry": True})
    second = journal.event("canal1_1", "audit_snapshot", floating_pl=5)
    assert journal.confirm_event(second, timeout=2)
    rows = [json.loads(line) for line in journal.EVENTS_FILE.read_text(encoding="utf-8").splitlines()]
    assert [row["floating_pl"] for row in rows if row["ev"] == "audit_snapshot"] == [5]
    assert [row["ev"] for row in rows if row["ev"].startswith("telemetry_capacity_")] == [
        "telemetry_capacity_gap", "telemetry_capacity_resumed",
    ]
    assert journal.persistence_health()["telemetry_suppressed"] == {"audit_snapshot": 1}


def test_stale_storage_observation_blocks_new_entries(isolated, monkeypatch):
    journal.observe_storage_health({"free_bytes": 10 * 1024 ** 3, "allow_telemetry": True})
    stale = {**journal._storage_observation, "observed_monotonic": time.monotonic() - 100}
    monkeypatch.setattr(journal, "_storage_observation", stale)
    assert journal.persistence_health()["storage_stale"]
    assert not journal.optional_telemetry_allowed()


def test_live_contract_declares_the_storage_admission_policy():
    import main

    contract = main._live_strategy_contract()["storage_admission"]
    assert contract == journal.admission_contract()
    assert contract["entry_checkpoint"] == "request_fsync_before_transport"
    assert contract["telemetry_reserve_bytes"] == 2 * 1024 ** 3


def test_one_sync_covers_all_receipts_already_written(isolated, monkeypatch):
    calls = []
    original = journal.os.fsync

    def sync(fd):
        calls.append(fd)
        original(fd)

    monkeypatch.setattr(journal.os, "fsync", sync)
    receipts = [journal.event("canal1_1", "checkpoint_batch", index=i) for i in range(20)]
    assert receipts[-1].ready.wait(2)
    for receipt in receipts:
        assert journal.confirm_event(receipt, timeout=2)
    assert len(calls) == 1


def test_full_event_queue_rejects_receipt_and_latches_entry_veto(isolated, monkeypatch):
    def full(item):
        raise Full

    with monkeypatch.context() as context:
        context.setattr(journal._event_queue, "put_nowait", full)
        receipt = journal.event("canal1_1", "entry_admission_evidence")
    assert receipt.ready.is_set()
    assert not journal.confirm_event(receipt, timeout=.1)
    assert journal.persistence_health()["write_failures"] == 1
    assert not journal.persistence_health()["allow_new_entries"]


def test_full_sync_queue_cannot_confirm_an_appended_receipt(isolated, monkeypatch):
    receipt = journal.event("canal1_1", "entry_admission_evidence")
    assert receipt.ready.wait(2)

    def full(item, **kwargs):
        raise Full

    with monkeypatch.context() as context:
        context.setattr(journal._event_queue, "put", full)
        assert not journal.confirm_event(receipt, timeout=.1)
    assert journal.confirm_event(receipt, timeout=2)


@pytest.mark.asyncio
async def test_slow_real_sync_times_out_without_stalling_event_loop(isolated, monkeypatch):
    from tests.test_mt5_trade_client import trade_request

    entered, release = threading.Event(), threading.Event()
    original = journal.os.fsync

    def slow_sync(fd):
        entered.set()
        assert release.wait(3), "test must release the writer"
        original(fd)

    with monkeypatch.context() as context:
        context.setattr(journal.os, "fsync", slow_sync)
        probe = asyncio.create_task(journal.confirm_entry_intent(trade_request(), timeout=.2))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            # The sync is still blocked while the event loop handles health and timeout.
            assert journal.persistence_health()["active_kind"] == "sync"
            assert not await asyncio.wait_for(probe, timeout=1)
            assert not release.is_set()
        finally:
            release.set()
            await asyncio.gather(probe, return_exceptions=True)
            await asyncio.to_thread(journal.flush_events, timeout=2)


@pytest.mark.asyncio
async def test_live_owner_installs_both_entry_evidence_guards(monkeypatch, tmp_path):
    import main

    monkeypatch.setattr(main.config, "BOT_EXECUTION_LEDGER_FILE", str(tmp_path / "intents.sqlite3"))
    monkeypatch.setattr(main.config, "MT5_LOGIN", 7)
    monkeypatch.setattr(main.config, "MT5_SERVER", "demo")
    monkeypatch.setattr(main.config, "MT5_PASSWORD", "")
    runtime = main._build_mt5_owner()
    try:
        assert runtime.service.entry_guard is journal.assert_entry_evidence_ready
        assert runtime.service.entry_evidence_probe is journal.confirm_entry_intent
    finally:
        await runtime.client.close()
