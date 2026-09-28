import asyncio
from dataclasses import replace

import pytest

from mt5_client import MT5ReadClient
from mt5_worker import WorkerConfig
from research.shared_transport import PassiveEvent, TransportJob, simulate_transport
from tests.mt5_load_fakes import OrderedTradeMT5


def job(name, operation="OPEN_MARKET", *, channel="canal1", at=0, ready=None, deadline=1000, service=10):
    return TransportJob(name, channel, None if channel == "system" else channel + "_1",
                        operation, at, at if ready is None else ready, deadline, service)


def started(report):
    return [(row["request_id"], row["at_ns"]) for row in report["events"] if row["kind"] == "started"]


def test_two_channels_share_one_connection_and_management_overtakes_entry():
    report = simulate_transport([
        job("active", "symbol_info_tick", channel="system"),
        job("entry", "PLACE_LIMIT", at=1),
        job("close", "CLOSE_POSITION", channel="canal2", at=2),
    ], cutoff_ns=100)
    assert started(report) == [("active", 0), ("close", 10), ("entry", 20)]
    assert report["status"] == "diagnostic_complete"
    assert report["full_live_parity_verified"] is report["portfolio_admitted"] is False
    assert report["rows"][1]["queue_ns"] == 19


def test_preparation_holds_capacity_but_does_not_hold_transport():
    report = simulate_transport([
        job("preparing", ready=50), job("close", "CLOSE_POSITION", at=1),
    ], cutoff_ns=100, capacity=1)
    assert started(report) == [("close", 1), ("preparing", 50)]
    assert report["peak_admitted"] == 2


@pytest.mark.parametrize("operation", ["OPEN_MARKET", "PLACE_LIMIT", "symbol_info_tick"])
def test_only_management_gets_the_reserved_slot(operation):
    report = simulate_transport([
        job("preparing", ready=50), job("regular", operation, at=1),
        job("close", "CLOSE_POSITION", at=2), job("modify", "MODIFY_SLTP", at=3),
    ], cutoff_ns=100, capacity=1)
    assert report["rows"][1]["status"] == "rejected_capacity"
    assert report["rows"][2]["status"] == "released"
    assert report["rows"][3]["status"] == "rejected_capacity"


def test_inflight_timeout_does_not_release_transport_or_retry():
    report = simulate_transport([
        job("entry", deadline=5, service=20),
        job("close", "CLOSE_POSITION", at=1, channel="canal2"),
    ], cutoff_ns=50)
    assert started(report) == [("entry", 0), ("close", 20)]
    assert report["rows"][0]["status"] == "released_late"
    assert report["rows"][0]["timed_out"]
    assert [e["kind"] for e in report["events"] if e["request_id"] == "entry"] == [
        "admitted", "queued", "started", "timeout_in_flight", "late_response"]


def test_unavailable_response_retains_unknown_and_blocks_following_calls():
    report = simulate_transport([
        job("hung", deadline=5, service=None), job("close", "CLOSE_POSITION", at=1, deadline=30),
    ], cutoff_ns=40)
    assert started(report) == [("hung", 0)]
    assert report["rows"][0]["status"] == "timed_out_in_flight"
    assert report["rows"][1]["status"] == "expired_unsent"
    assert report["active_request_id"] == "hung"
    assert report["blockers"] == ["transport_lifecycle_incomplete_at_cutoff"]


def test_expired_preparation_retains_capacity_until_its_completion():
    report = simulate_transport([
        job("slow_storage", ready=50, deadline=5), job("blocked", at=10), job("after_drain", at=60),
    ], cutoff_ns=100, capacity=1)
    assert started(report) == [("after_drain", 60)]
    assert [r["status"] for r in report["rows"]] == ["expired_unsent", "rejected_capacity", "released"]
    assert next(e for e in report["events"] if e["kind"] == "preparation_drained")["at_ns"] == 50


def test_passive_sl_can_happen_while_transport_is_blocked_without_releasing_it():
    event = PassiveEvent("sl-1", "canal2", "canal2_1", 6, "native_sl")
    report = simulate_transport([job("modify", "MODIFY_SLTP", deadline=5, service=20)],
                                cutoff_ns=30, passive_events=[event])
    passive = next(e for e in report["events"] if e["kind"] == "passive")
    assert passive["at_ns"] == 6 and passive["active_request_id"] == "modify"
    assert report["rows"][0]["released_ns"] == 20
    assert report["broker_fills_simulated"] is False


def test_equal_deadline_and_response_has_declared_unknown_then_late_convention():
    report = simulate_transport([job("same_time", deadline=10, service=10)], cutoff_ns=10)
    assert report["rows"][0]["status"] == "released_late"
    assert [event["kind"] for event in report["events"]][-2:] == ["timeout_in_flight", "late_response"]


def test_zero_duration_and_ties_remain_finite_and_are_not_claimed_as_native_order():
    report = simulate_transport([job("a", service=0), job("b", service=0)], cutoff_ns=10)
    assert started(report) == [("a", 0), ("b", 0)]
    first = next(event for event in report["events"] if event["kind"] == "started")
    assert first["tie_hypothesis_used"] is True
    assert first["eligible_request_ids"] == ["a", "b"]
    assert "hypothesis" in report["tie_break"]


def test_event_budget_preserves_partial_evidence_and_never_claims_completion():
    report = simulate_transport([job("a")], cutoff_ns=100, max_events=2)
    assert report["status"] == "blocked"
    assert report["blockers"][0] == "transport_event_budget_exhausted"
    assert len(report["events"]) == 2
    assert report["rows"][0]["status"] == "queued"


def test_future_requests_cannot_change_earlier_decisions():
    base = [job("a", service=20), job("b", at=1)]
    left = simulate_transport(base, cutoff_ns=100)
    right = simulate_transport(base + [job("later", "CLOSE_POSITION", at=25)], cutoff_ns=100)
    prefix = lambda report: [row for row in report["events"] if row["at_ns"] < 25]
    assert prefix(left) == prefix(right)


@pytest.mark.parametrize("change", [
    {"ready_ns": -1}, {"admitted_ns": True}, {"ready_ns": .5}, {"deadline_ns": 0},
    {"service_ns": -1}, {"operation": "invalid"}, {"signal_id": "canal2_1"},
    {"channel": "system"}, {"request_id": ""},
])
def test_invalid_phase_or_identity_is_rejected(change):
    with pytest.raises(ValueError):
        replace(job("a"), **change)


def test_duplicates_and_events_outside_selected_window_are_not_dropped():
    with pytest.raises(ValueError, match="duplicate"):
        simulate_transport([job("a"), job("a")], cutoff_ns=100)
    with pytest.raises(ValueError, match="outside cutoff"):
        simulate_transport([job("future", at=101)], cutoff_ns=100)


@pytest.mark.asyncio
async def test_arbitration_order_matches_actual_client_across_both_channels():
    client = MT5ReadClient(WorkerConfig(7, "demo", ("XAUUSD",)), backend_factory=OrderedTradeMT5)
    operations = ["OPEN_MARKET", *(["CLOSE_POSITION"] * 5), "symbol_info_tick"]
    jobs = [job("initial", "symbol_info_tick", channel="system", service=10)]
    jobs += [job(str(index), operation, channel="canal1" if index % 2 else "canal2", at=1, service=1)
             for index, operation in enumerate(operations)]
    report = simulate_transport(jobs, cutoff_ns=100)
    deadline = asyncio.get_running_loop().time() + 3
    actual, tasks = [], []

    async def acquire(item):
        assert await client._acquire_transport(item.kind, deadline)
        actual.append(item.request_id)
        await asyncio.sleep(0)
        await client._release_transport(item.kind)

    try:
        assert await client._acquire_transport("read", deadline)
        tasks = [asyncio.create_task(acquire(item)) for item in jobs[1:]]
        while sum(client._transport_waiters.values()) != len(tasks):
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0)
        await client._release_transport("read")
        await asyncio.gather(*tasks)
        assert actual == ["1", "2", "3", "4", "6", "5", "0"]
        assert actual == [name for name, _ in started(report)][1:]
        assert client.worker_pid is None
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await client.close()
