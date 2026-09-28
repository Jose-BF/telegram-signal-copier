from dataclasses import replace

import pytest

from basket_observation import basket_summary_reads
from dubai_live_candidate import DubaiGuardState, DubaiLivePolicy, evaluate_guard
from research.management_observation import BasketReadCycle, ReadCycleProfile, ReadValue
from research.shared_transport import TransportJob, TransportSession


def position(ticket=11, profit=4.):
    return {"ticket": ticket, "profit": profit, "volume": .01, "price_open": 100., "swap": -99.}


def history(profit=2., volume=.01):
    return [{"entry": 0, "volume": .01, "profit": 0., "commission": -.1},
            {"entry": 1, "volume": volume, "profit": profit, "commission": -.1, "swap": -.05, "fee": -.01}]


def cycle(*, sample=None, profile=None, tickets=None, cache=None, session=None, cycle_id=0):
    session = session or TransportSession(cutoff_ns=100)
    tickets = [11, 12] if tickets is None else tickets
    cache = {} if cache is None else cache
    default = {"symbol_info_tick": {"bid": 105., "ask": 105.2},
               "positions_get": [position()], "history_deals_get_position": history()}
    flow = basket_summary_reads("XAUUSD", "BUY", lambda: tickets, lambda: [], cache)
    return BasketReadCycle(flow, session, channel="canal1", signal_id="canal1_test", cycle_id=cycle_id,
        profile=profile or ReadCycleProfile(sample_delay_ns=1, response_delay_ns=2, timeout_ns=50),
        sample=sample or (lambda request, now: ReadValue(now, {"data": default[request.operation.value]})))


def drain(item, *, on_time=None, max_steps=1000):
    session = item.session
    now = session.now_ns
    item.start(now)
    for _ in range(max_steps):
        if on_time:
            on_time(now, item)
        session.advance(now)
        item.advance(now)
        session.advance(now)
        item.advance(now)
        if session.failure_reasons:
            return
        session.advance(now)
        grant = session.dispatch()
        if session.failure_reasons:
            item.advance(session.now_ns)
            return
        if grant is not None:
            if grant["request_id"] == item.request_id:
                item.accept_grant(grant, now)
                item.advance(now)
            continue
        if item.completed or item.stopped and not item.pending:
            return
        pending = [t for t in (item.next_event_ns, session.next_event_ns) if t is not None]
        if not pending:
            session.advance(session.cutoff_ns)
            item.advance(session.cutoff_ns)
            return
        now = min(pending)
    pytest.fail("read cycle failed to converge")


def test_separate_read_stages_sample_their_own_state_and_complete_between_quotes():
    seen = []
    def sample(request, now):
        seen.append((request.operation.value, now))
        if request.operation.value == "symbol_info_tick":
            return ReadValue(0, {"data": {"bid": 105., "ask": 105.2}})
        if request.operation.value == "positions_get":
            return ReadValue(4, {"data": [position(profit=-3.)]})
        return ReadValue(7, {"data": history(8.)})
    item = cycle(sample=sample)
    drain(item)
    assert seen == [("symbol_info_tick", 1), ("positions_get", 4), ("history_deals_get_position", 7)]
    assert [row["delivery_ns"] for row in item.records] == [3, 6, 9]
    assert item.summary["current_price"] == 105.
    assert item.summary["total_pl"] == pytest.approx(4.74)
    assert item.completed and not item.report()["blockers"]


def test_other_basket_trade_delays_actual_read_sampling_not_only_final_delivery():
    session = TransportSession(cutoff_ns=100)
    session.submit([TransportJob("entry", "canal2", "canal2_busy", "OPEN_MARKET", 0, 0, 90, 5)])
    item = cycle(session=session)
    drain(item)
    assert [(row["started_ns"], row["sample_ns"], row["delivery_ns"]) for row in item.records] == [
        (5, 6, 8), (8, 9, 11), (11, 12, 14)]
    rows = session.report()["rows"]
    assert rows[0]["released_ns"] == 5
    assert all(row["released_ns"] is not None for row in rows)


def test_profit_peak_is_not_available_to_guard_before_positions_response():
    def sample(request, now):
        data = {"bid": 100., "ask": 100.2} if request.operation.value == "symbol_info_tick" else [position(profit=12.)]
        return ReadValue(now, {"data": data})
    item = cycle(sample=sample, tickets=[11])
    observations = []
    drain(item, on_time=lambda now, item: observations.append((now, item.summary)))
    assert all(value is None for now, value in observations if now < 6)
    assert item.records[-1]["delivery_ns"] == 6
    decision = evaluate_guard(policy=DubaiLivePolicy(), state=DubaiGuardState(),
        total_pl=item.summary["total_pl"], n_open=item.summary["n_open"], elapsed_min=0.,
        money_evidence_complete=item.summary["realized_complete"])
    assert decision.action == "arm"


@pytest.mark.parametrize("data", [None, [], history(volume=.005)])
def test_missing_or_partial_history_stays_unknown_and_never_enters_cache(data):
    cache = {}
    def sample(request, now):
        value = ({"bid": 100., "ask": 100.2} if request.operation.value == "symbol_info_tick"
                 else [position()] if request.operation.value == "positions_get" else data)
        return ReadValue(now, {"data": value})
    item = cycle(sample=sample, cache=cache)
    drain(item)
    assert item.summary["total_pl"] is None
    assert item.summary["missing_realized_tickets"] == [12]
    assert cache == {}


def test_confirmed_history_cache_reduces_next_cycle_without_recounting_costs():
    cache = {}
    first = cycle(cache=cache)
    drain(first)
    second = cycle(cache=cache, session=first.session, cycle_id=1)
    drain(second)
    assert len(first.records) == 3 and len(second.records) == 2
    assert first.summary == second.summary
    assert cache == {12: pytest.approx(1.74)}


def test_active_timeout_keeps_transport_until_late_response_and_discards_data():
    item = cycle(profile=ReadCycleProfile(sample_delay_ns=2, response_delay_ns=5, timeout_ns=3))
    drain(item)
    assert item.summary is None and not item.completed
    assert item.reason == "read_timeout"
    assert item.records[0]["disposition"] == "discarded"
    assert item.records[0]["delivery_ns"] == 7
    row = item.session.report()["rows"][0]
    assert row["status"] == "released_late" and row["released_ns"] == 7
    assert len(item.records) == 1


def test_unsent_timeout_never_samples_or_starts_next_stage():
    session = TransportSession(cutoff_ns=100)
    session.submit([TransportJob("busy", "canal2", "canal2_busy", "OPEN_MARKET", 0, 0, 90, 10)])
    item = cycle(session=session, profile=ReadCycleProfile(timeout_ns=3))
    drain(item)
    assert item.records == [] and item.summary is None
    assert item.reason == "read_expired_unsent"


@pytest.mark.parametrize("active", [False, True])
def test_cancel_withdraws_only_unsent_read_and_drains_active(active):
    item = cycle()
    session = item.session
    item.start(0)
    session.advance(0)
    if active:
        item.accept_grant(session.dispatch(), 0)
    item.cancel(0)
    if active:
        for now in (1, 3):
            session.advance(now)
            item.advance(now)
        session.advance(3)
        item.advance(3)
    assert item.summary is None and not item.pending
    assert item.reason == "read_cycle_cancelled"
    assert len(item.records) == int(active)
    assert session.report()["rows"][0]["status"] == ("released" if active else "cancelled_unsent")


def test_skipping_due_read_event_rejects_later_broker_state():
    item = cycle()
    item.start(0)
    item.session.advance(0)
    item.accept_grant(item.session.dispatch(), 0)
    item.session.advance(2)
    with pytest.raises(ValueError, match="missed read event"):
        item.advance(2)


def test_future_source_is_rejected_before_response_delivery():
    item = cycle(sample=lambda request, now: ReadValue(now + 1, {"data": None}))
    with pytest.raises(ValueError, match="future source"):
        drain(item)
    assert item.summary is None


def test_read_budget_preserves_prefix_without_fabricating_complete_summary():
    item = cycle(profile=ReadCycleProfile(max_reads=2))
    drain(item)
    assert len(item.records) == 2 and item.summary is None
    assert item.reason == "read_cycle_budget_exhausted"


def test_response_beyond_cutoff_remains_pending():
    item = cycle(session=TransportSession(cutoff_ns=2))
    drain(item)
    assert item.summary is None and item.pending
    assert "read_cycle_incomplete" in item.report()["blockers"]


@pytest.mark.parametrize("change", [{"sample_delay_ns": -1}, {"response_delay_ns": True},
    {"timeout_ns": 0}, {"max_reads": 0}, {"max_reads": 10001}])
def test_profile_is_bounded(change):
    with pytest.raises(ValueError):
        replace(ReadCycleProfile(), **change)


def test_cancel_after_transport_grant_before_native_start_releases_without_sample():
    item = cycle()
    item.start(0)
    item.session.advance(0)
    grant = item.session.dispatch()
    item.cancel(0)
    item.session.advance(0)
    assert not item.pending and item.records == []
    assert item.session.request_state(grant["request_id"])["released_ns"] == 0


def test_cancel_after_unsent_expiry_does_not_revoke_terminal_transport_state():
    session = TransportSession(cutoff_ns=100)
    session.submit([TransportJob("busy", "canal2", "canal2_busy", "OPEN_MARKET", 0, 0, 90, 10)])
    item = cycle(profile=ReadCycleProfile(timeout_ns=1), session=session)
    item.start(0)
    identity = item.request_id
    item.session.advance(0)
    assert item.session.dispatch()["request_id"] == "busy"
    item.session.advance(1)
    item.cancel(1)
    assert item.session.request_state(identity)["status"] == "expired_unsent"
    assert not item.pending


def test_invalid_delivered_records_fail_cycle_but_release_transport():
    item = cycle(sample=lambda request, now: ReadValue(now, {"data": 42}))
    drain(item)
    assert not item.pending
    assert item.summary is None and item.reason == "invalid_read_payload"
    assert item.session.report()["rows"][0]["status"] == "released"


def test_payload_budget_discards_large_result_but_drains_call():
    item = cycle(profile=ReadCycleProfile(sample_delay_ns=1, response_delay_ns=2, max_payload_bytes=1))
    drain(item)
    assert item.reason == "read_payload_budget_exhausted"
    assert item.summary is None and not item.pending
    assert item.records[0]["payload_omitted"] is True
    assert item.records[0]["payload"] is None
    assert item.records[0]["delivery_ns"] == 3
    assert item.payload_bytes == 0


def test_payload_is_copied_at_sampling_not_modified_before_delivery():
    payload = {"data": [position()]}
    value = ReadValue(1, payload)
    payload["data"][0]["profit"] = 500.
    assert value.native_value()[0].profit == 4.
    value.native_value()[0].profit = 700.
    assert value.native_value()[0].profit == 4.


def test_unknown_reply_can_preserve_unknown_source_clock():
    value = ReadValue(None, {"data": None})
    assert value.source_ns is None and value.native_value() is None


def test_known_fill_arriving_before_positions_response_is_selected_at_response():
    tickets = [11]
    def sample(request, now):
        data = ({"bid": 100., "ask": 100.2} if request.operation.value == "symbol_info_tick"
                else [position(), position(12, 3.)])
        return ReadValue(now, {"data": data})
    item = cycle(tickets=tickets, sample=sample)
    def update(now, item):
        if now == 6 and 12 not in tickets:
            tickets.append(12)
    drain(item, on_time=update)
    assert item.summary["open_tickets"] == [11, 12]
    assert item.summary["floating_pl"] == 7.


def test_known_fill_absent_from_older_positions_requires_history_not_fabricated_exposure():
    tickets = [11]
    def sample(request, now):
        data = ({"bid": 100., "ask": 100.2} if request.operation.value == "symbol_info_tick"
                else [position()] if request.operation.value == "positions_get" else history(volume=0.))
        return ReadValue(now, {"data": data})
    item = cycle(tickets=tickets, sample=sample)
    def update(now, item):
        if now >= 6 and 12 not in tickets:
            tickets.append(12)
    drain(item, on_time=update)
    assert item.summary["open_tickets"] == [11]
    assert item.summary["missing_realized_tickets"] == [12]
    assert item.summary["total_pl"] is None


def test_zero_duration_responses_continue_without_a_future_market_quote():
    item = cycle(profile=ReadCycleProfile())
    drain(item)
    assert item.completed
    assert [row["delivery_ns"] for row in item.records] == [0, 0, 0]
    assert item.summary["total_pl"] == pytest.approx(5.74)


def test_completed_history_retains_its_query_identity_in_detached_report():
    item = cycle(profile=ReadCycleProfile(timeout_ns=50))
    drain(item)
    assert item.report()["requests"][-1]["params"] == {"position": 12}
    detached = item.report()
    detached["requests"][-1]["params"]["position"] = 999
    assert item.report()["requests"][-1]["params"] == {"position": 12}


def test_transport_budget_failure_before_final_response_cannot_publish_summary():
    cache = {}
    item = cycle(cache=cache, session=TransportSession(cutoff_ns=100, max_events=11))
    drain(item)
    assert not item.completed and item.summary is None
    assert cache == {}
    assert "transport_event_budget_exhausted" in item.report()["blockers"]


@pytest.mark.parametrize("budget", range(1, 14))
def test_transport_evidence_budget_bounds_every_stage_without_false_completion(budget):
    cache = {}
    item = cycle(cache=cache, session=TransportSession(cutoff_ns=100, max_events=budget))
    drain(item)
    if item.session.failure_reasons:
        assert item.summary is None and not item.completed
        assert cache == {}
    else:
        assert item.completed and item.summary["total_pl"] == pytest.approx(5.74)


def test_response_is_not_consumed_until_transport_event_has_been_observed():
    item = cycle(tickets=[11], profile=ReadCycleProfile())
    item.start(0)
    for stage in range(2):
        item.session.advance(0)
        item.accept_grant(item.session.dispatch(), 0)
        item.advance(0)
        assert item.summary is None and not item.completed
        assert item.pending
        item.session.advance(0)
        item.advance(0)
    assert item.completed and item.summary["floating_pl"] == 4.
