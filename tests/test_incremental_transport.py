import hashlib
import json
import random

import pytest

import research.shared_transport as transport
from research.shared_transport import PassiveEvent, TransportJob, simulate_transport


def scenarios():
    rng = random.Random(21591)
    for _ in range(64):
        jobs = []
        for index in range(20):
            at = rng.randrange(40)
            channel = rng.choice(["canal1", "canal2"])
            jobs.append(TransportJob(
                str(index), channel, channel + "_1",
                rng.choice(["OPEN_MARKET", "CLOSE_POSITION", "MODIFY_SLTP", "symbol_info_tick"]),
                at, rng.choice([None, at, at + 3, at + 15]),
                at + rng.choice([1, 5, 30, 60]), rng.choice([None, 0, 2, 10]),
            ))
        passive = [PassiveEvent("sl", "canal1", "canal1_1", 22, "native_sl")]
        yield jobs, passive, rng.randrange(1, 9)


def test_frozen_bulk_scheduling_results():
    reports = [simulate_transport(jobs, cutoff_ns=100, capacity=capacity, passive_events=passive)
               for jobs, passive, capacity in scenarios()]
    digest = hashlib.sha256(json.dumps(reports, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert digest == "187aa0c1bc26312a08f93ec0219b476d7679ba8f1c826af9dcf5264d83b45b1d"


def job(name, *, at=0, operation="OPEN_MARKET", service=10, deadline=100):
    return TransportJob(name, "canal1", "canal1_1", operation, at, at, deadline, service)


def session(**kwargs):
    return transport.TransportSession(cutoff_ns=100, **kwargs)


def test_dynamic_response_can_generate_management_before_next_dispatch():
    replay = session()
    replay.submit([job("entry"), job("waiting", at=1)])
    replay.advance(0)
    assert replay.dispatch()["request_id"] == "entry"
    replay.advance(1)
    assert replay.dispatch() is None
    replay.advance(10)
    assert replay.report()["active_request_id"] is None
    replay.submit([job("derived_close", at=10, operation="CLOSE_POSITION")])
    assert replay.dispatch()["request_id"] == "derived_close"
    replay.advance(20)
    assert replay.dispatch()["request_id"] == "waiting"
    replay.advance(30)
    replay.advance(100)
    report = replay.report()
    assert report["status"] == "diagnostic_complete"
    assert [row["started_ns"] for row in report["rows"]] == [0, 20, 10]


def test_cannot_skip_an_observable_event_or_move_time_backwards():
    replay = session()
    replay.submit([job("entry")])
    replay.advance(0)
    replay.dispatch()
    with pytest.raises(ValueError, match="next event"):
        replay.advance(11)
    replay.advance(10)
    with pytest.raises(ValueError, match="backwards"):
        replay.advance(9)
    with pytest.raises(ValueError, match="past"):
        replay.submit([job("past", at=1)])


def test_zero_duration_response_requires_observation_before_next_dispatch():
    replay = session()
    replay.submit([job("zero", service=0), job("waiting")])
    replay.advance(0)
    assert replay.dispatch()["request_id"] == "zero"
    assert replay.dispatch() is None
    assert replay.next_event_ns == 0
    replay.advance(0)
    replay.submit([job("close", operation="CLOSE_POSITION")])
    assert replay.dispatch()["request_id"] == "close"


def test_partial_report_is_detached_and_does_not_poison_later_completion():
    replay = session()
    replay.submit([job("entry")])
    replay.advance(0)
    replay.dispatch()
    report = replay.report()
    assert report["status"] == "blocked"
    report["events"][0]["kind"] = "tampered"
    report["rows"][0]["status"] = "tampered"
    report["blockers"].append("tampered")
    replay.advance(10)
    replay.advance(100)
    final = replay.report()
    assert final["status"] == "diagnostic_complete"
    assert final["events"][0]["kind"] == "admitted"
    assert final["rows"][0]["status"] == "released"


def test_batch_validation_is_atomic_and_identities_remain_unique():
    replay = session()
    with pytest.raises(ValueError, match="duplicate"):
        replay.submit([job("duplicate"), job("duplicate")])
    assert replay.report()["jobs"] == []
    replay.submit([job("a")])
    with pytest.raises(ValueError, match="duplicate"):
        replay.submit([job("b"), job("a")])
    assert len(replay.report()["jobs"]) == 1


def test_event_exhaustion_cannot_be_resumed_into_a_false_success():
    replay = session(max_events=2)
    replay.submit([job("a")])
    replay.advance(0)
    assert replay.dispatch() is None
    assert replay.report()["blockers"][0] == "transport_event_budget_exhausted"
    with pytest.raises(RuntimeError, match="blocked"):
        replay.submit([job("b")])
    assert replay.dispatch() is None
    assert replay.next_event_ns is None


def test_submitted_at_cutoff_is_not_complete_before_observation():
    replay = session()
    replay.advance(100)
    replay.submit([job("last", at=100, deadline=200)])
    assert replay.report()["status"] == "blocked"
    replay.advance(100)
    replay.dispatch()
    assert replay.report()["pending_request_ids"] == ["last"]
    assert replay.report()["status"] == "blocked"


def test_due_passive_event_must_be_observed_before_dispatch():
    replay = session()
    replay.submit([job("entry")], passive_events=[PassiveEvent("sl", "canal2", "canal2_1", 0, "native_sl")])
    with pytest.raises(ValueError, match="observe due"):
        replay.dispatch()
    events = replay.advance(0)
    assert events[0]["kind"] == "passive"
    assert replay.dispatch()["request_id"] == "entry"


def test_waiting_work_cannot_be_silently_delayed_by_driver():
    replay = session()
    replay.submit([job("entry")])
    replay.advance(0)
    with pytest.raises(ValueError, match="dispatch required"):
        replay.advance(1)
    assert replay.now_ns == 0
    assert replay.dispatch()["at_ns"] == 0


def test_timeout_and_passive_execution_do_not_release_occupied_connection():
    replay = session()
    replay.submit([job("busy", deadline=5, service=20)])
    replay.advance(0)
    replay.dispatch()
    replay.advance(5)
    replay.submit([job("close", at=5, operation="CLOSE_POSITION")],
                  passive_events=[PassiveEvent("sl", "canal2", "canal2_1", 6, "native_sl")])
    replay.advance(5)
    assert replay.dispatch() is None
    observed = replay.advance(6)
    assert observed[0]["active_request_id"] == "busy"
    assert replay.dispatch() is None
    replay.advance(20)
    assert replay.dispatch()["request_id"] == "close"
    assert replay.report()["rows"][0]["status"] == "released_late"


def test_incremental_driver_matches_bulk_reports_for_staged_inputs():
    for jobs, passive, capacity in scenarios():
        # Preserve input ordinal as the declared tie convention, not arrival
        # order invented by this driver.
        jobs = sorted(jobs, key=lambda row: row.admitted_ns)
        expected = simulate_transport(jobs, cutoff_ns=100, capacity=capacity, passive_events=passive)
        replay = session(capacity=capacity)
        incoming = sorted({row.admitted_ns for row in jobs} | {row.at_ns for row in passive})
        while incoming or replay.next_event_ns is not None:
            times = incoming[:1] + ([] if replay.next_event_ns is None else [replay.next_event_ns])
            now = min(times)
            if incoming and incoming[0] == now:
                incoming.pop(0)
                replay.submit([row for row in jobs if row.admitted_ns == now],
                              passive_events=[row for row in passive if row.at_ns == now])
            replay.advance(now)
            replay.dispatch()
        replay.advance(100)
        assert replay.report() == expected


def test_external_book_completion_releases_transport_without_second_delay():
    replay = session()
    replay.submit([job("book_owned", service=None), job("waiting", at=1)])
    replay.advance(0)
    replay.dispatch()
    replay.advance(1)
    replay.dispatch()
    replay.advance(7)
    replay.complete("book_owned", 7)
    assert replay.report()["active_request_id"] == "book_owned"
    assert replay.advance(7)[0]["kind"] == "response"
    assert replay.dispatch()["request_id"] == "waiting"
    assert replay.report()["rows"][0]["released_ns"] == 7


def test_external_response_after_timeout_still_drains_once():
    replay = session()
    replay.submit([job("late", service=None, deadline=5)])
    replay.advance(0)
    replay.dispatch()
    replay.advance(5)
    replay.complete("late", 5)
    with pytest.raises(ValueError, match="already scheduled"):
        replay.complete("late", 5)
    assert replay.advance(5)[0]["kind"] == "late_response"
    with pytest.raises(ValueError, match="active"):
        replay.complete("late", 5)


@pytest.mark.parametrize("mode", ["unknown", "queued", "future", "past", "declared_service"])
def test_invalid_external_response_cannot_change_transport(mode):
    replay = session()
    replay.submit([job("active", service=10 if mode == "declared_service" else None), job("queued")])
    replay.advance(0)
    replay.dispatch()
    replay.advance(1)
    identity = "unknown" if mode == "unknown" else "queued" if mode == "queued" else "active"
    at = 2 if mode == "future" else 0 if mode == "past" else 1
    before = replay.report()
    with pytest.raises(ValueError):
        replay.complete(identity, at)
    assert replay.report() == before


@pytest.mark.parametrize("phase", ["not_admitted", "queued", "preparing"])
def test_cancel_unsent_preserves_preparation_ownership(phase):
    replay = session(capacity=1)
    item = job("entry")
    if phase == "preparing":
        from dataclasses import replace
        item = replace(item, ready_ns=10)
    replay.submit([item])
    if phase != "not_admitted":
        replay.advance(0)
    replay.cancel_unsent("entry", 0)
    replay.advance(0)
    assert replay.dispatch() is None
    if phase == "preparing":
        assert replay.report()["pending_request_ids"] == ["entry"]
        replay.advance(10)
    assert replay.report()["pending_request_ids"] == []
    assert replay.report()["rows"][0]["status"] == "cancelled_unsent"


def test_cancel_does_not_revoke_started_call_or_other_identity():
    replay = session()
    replay.submit([job("active", service=None)])
    replay.advance(0)
    replay.dispatch()
    before = replay.report()
    for identity in ("active", "missing"):
        with pytest.raises(ValueError):
            replay.cancel_unsent(identity, 0)
    assert replay.report() == before
