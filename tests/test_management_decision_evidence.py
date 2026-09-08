from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import causal_trace
import gold_555_live_candidate
import dubai_live_candidate
import journal
import pending_actions
import position_lifecycle_monitor as monitor
from state import Signal


NOW = datetime(2026, 9, 8, 7)


def _signal(direction="BUY"):
    signal = Signal(
        channel="canal2", message_id=98765, direction=direction,
        timestamp=NOW, market_ticket=101, market_fill_price=4300.0,
        source_message_revision_id="msgrev_original", source_decision_id="decision_original",
        live_strategy_id=gold_555_live_candidate.CANDIDATE_ID,
        live_strategy_fingerprint=gold_555_live_candidate.CANDIDATE_FINGERPRINT,
        candidate_first_fill_at=NOW,
    )
    signal.candidate_hard_stops[101] = 4270.0 if direction == "BUY" else 4330.0
    signal.candidate_entry_prices_by_ticket[101] = 4300.0
    return signal


@pytest.fixture
def captured(monkeypatch, tmp_path):
    events = []
    def event(sig, ev, **fields):
        events.append({"sig": sig, "ev": ev, **causal_trace.current_fields(), **fields})
    monkeypatch.setattr(journal, "event", event)
    queue = pending_actions.PendingQueue(spool_path=tmp_path / "pending.json")
    monkeypatch.setattr(queue, "_ensure_runner", lambda: None)
    monkeypatch.setattr(pending_actions, "queue", queue)
    return events, queue


def _pairs(events, kind):
    starts = [row for row in events if row["ev"] == "bot_internal_decision_started"
              and row.get("management_kind") == kind]
    ends = [row for row in events if row["ev"] == "bot_internal_decision"
            and row.get("management_kind") == kind]
    assert len(starts) == len(ends) > 0
    for start, end in zip(starts, ends):
        assert start["decision_id"] == end["decision_id"]
        assert start["management_contract"] == end["management_contract"] == "management_decision_inputs_v1"
        assert start["parent_decision_id"] == "decision_original"
        requests = [row["action_id"] for row in events if row["ev"] in (
            "mt5_modify_requested", "mt5_close_requested", "mt5_cancel_requested",
        ) and row["decision_id"] == start["decision_id"]]
        assert end["declared_action_ids"] == requests
        assert end["declared_action_count"] == len(requests)
    return starts, ends


@pytest.mark.asyncio
@pytest.mark.parametrize("direction,price,next_price", [("BUY", 4310.0, 4309.0), ("SELL", 4290.0, 4291.0)])
async def test_trailing_records_consumed_inputs_state_actions_and_noop(captured, direction, price, next_price):
    events, queue = captured
    signal = _signal(direction)
    tick = SimpleNamespace(bid=price, ask=price, time_msc=1788850800123)
    assert await monitor._apply_gold_555_trailing_stops(signal, tick, open_tickets={101}) == 1
    tick.bid = tick.ask = next_price
    assert await monitor._apply_gold_555_trailing_stops(signal, tick, open_tickets={101}) == 0
    starts, ends = _pairs(events, "gold_555_trailing")
    assert len(starts) == 2
    assert starts[0]["decision_inputs"]["bid"] == price
    assert starts[0]["decision_inputs"]["open_tickets"] == [101]
    assert starts[0]["state_before"]["candidate_hard_stops"][101] != signal.candidate_hard_stops[101]
    assert ends[0]["state_after"]["candidate_hard_stops"][101] == signal.candidate_hard_stops[101]
    assert ends[0]["decision_result"] == 1 and ends[1]["decision_result"] == 0
    assert ends[1]["declared_action_ids"] == []
    semantic = next(row for row in events if row["ev"] == "gold_555_trailing_stop_requested")
    assert semantic["decision_id"] == starts[0]["decision_id"]
    assert len(queue._actions) == 1
    assert causal_trace.current_decision_id() is None


def test_leg_protection_keeps_two_requests_and_coalescence_in_one_decision(captured):
    events, queue = captured
    signal = _signal()
    assert monitor._queue_gold_555_leg_protection(signal, ticket=102, fill_price=4298.5, leg_index=1) == (4268.5, 4299.5)
    starts, ends = _pairs(events, "gold_555_leg_protection")
    assert len(starts) == 1 and ends[0]["declared_action_count"] == 2
    assert starts[0]["decision_inputs"] == {"ticket": 102, "fill_price": 4298.5, "leg_index": 1}
    assert len(queue._actions) == 1
    coalesced = next(row for row in events if row["ev"] == "mt5_action_coalesced")
    assert coalesced["action_id"] == ends[0]["declared_action_ids"][1]
    assert coalesced["supersedes_action_id"] == ends[0]["declared_action_ids"][0]


def test_guard_records_unrounded_inputs_prior_peak_and_close(captured):
    events, queue = captured
    signal = _signal()
    summary = {"positions_complete": True, "realized_complete": True,
               "floating_pl": 30.004, "realized_pl": 0.0, "total_pl": 30.004,
               "n_open": 1, "open_tickets": [101], "source_tick_time_msc": 1788850800123}
    assert monitor._apply_gold_555_basket_guard(signal, summary, now=NOW + timedelta(minutes=1)).action == "arm"
    summary.update(floating_pl=29.003, total_pl=29.003)
    assert monitor._apply_gold_555_basket_guard(signal, summary, now=NOW + timedelta(minutes=2)).action == "close"
    starts, ends = _pairs(events, "gold_555_basket_guard")
    assert len(starts) == 2
    assert starts[0]["decision_inputs"]["summary"]["total_pl"] == 30.004
    assert starts[0]["state_before"]["basket_guard_peak_pl"] is None
    assert starts[1]["state_before"]["basket_guard_peak_pl"] == 30.004
    assert ends[0]["decision_result"]["action"] == "arm"
    assert ends[1]["declared_action_count"] == 1
    assert len(queue._actions) == 1 and queue._actions[0].kind == "CLOSE_POSITION"


@pytest.mark.asyncio
async def test_queue_failure_is_recorded_and_propagated_without_false_completed(captured, monkeypatch):
    events, _ = captured
    def fail(*args, **kwargs):
        raise OSError("synthetic queue failure")
    monkeypatch.setattr(pending_actions, "enqueue_modify_sl", fail)
    with pytest.raises(OSError, match="synthetic queue failure"):
        await monitor._apply_gold_555_trailing_stops(_signal(), SimpleNamespace(bid=4310, ask=4310.2, time_msc=1))
    _, ends = _pairs(events, "gold_555_trailing")
    assert ends[0]["decision_status"] == "error"
    assert ends[0]["error_type"] == "OSError"
    assert ends[0]["declared_action_ids"] == []
    assert causal_trace.current_decision_id() is None


def test_dubai_guard_keeps_its_result_and_records_complete_inputs(captured):
    events, _ = captured
    signal = _signal()
    signal.channel = "canal1"
    signal.live_strategy_id = dubai_live_candidate.CANDIDATE_ID
    signal.live_strategy_fingerprint = dubai_live_candidate.DubaiLivePolicy().fingerprint
    summary = {"positions_complete": True, "realized_complete": True,
               "floating_pl": 0.25, "realized_pl": 0.0, "total_pl": 0.25,
               "n_open": 1, "open_tickets": [101]}
    result = monitor._apply_candidate_basket_guard(signal, summary, now=NOW)
    starts, ends = _pairs(events, "dubai_basket_guard")
    assert starts[0]["decision_inputs"]["summary"] == summary
    assert starts[0]["decision_inputs"]["now_utc"] == "2026-09-08T07:00:00+00:00"
    assert ends[0]["decision_result"]["action"] == result.action
    assert ends[0]["declared_action_count"] == 0


@pytest.mark.asyncio
async def test_capture_writer_failure_does_not_change_requested_trailing(captured, monkeypatch):
    _, queue = captured
    original = journal.event
    def event(sig, ev, **fields):
        if fields.get("management_contract"):
            raise OSError("synthetic capture writer failure")
        return original(sig, ev, **fields)
    monkeypatch.setattr(journal, "event", event)
    assert await monitor._apply_gold_555_trailing_stops(_signal(), SimpleNamespace(bid=4310, ask=4310.2, time_msc=1)) == 1
    assert len(queue._actions) == 1 and queue._actions[0].new_sl == 4280.0
    assert causal_trace.current_decision_id() is None


@pytest.mark.asyncio
async def test_nested_provider_context_remains_bound_after_internal_capture(captured):
    events, _ = captured
    with causal_trace.bind_message_revision("msgrev_later", decision_id="decision_provider"):
        assert await monitor._apply_gold_555_trailing_stops(_signal(), SimpleNamespace(bid=4310, ask=4310.2, time_msc=1)) == 1
        assert causal_trace.current_decision_id() == "decision_provider"
        assert causal_trace.declared_action_ids() == []
    starts = [r for r in events if r.get("management_kind") == "gold_555_trailing" and r["ev"].endswith("_started")]
    assert starts[0]["message_revision_id"] == "msgrev_later"
    assert starts[0]["parent_decision_id"] == "decision_provider"


def test_startup_discloses_capture_contract_without_altering_policy():
    import main
    contract = main._live_strategy_contract()
    capture = contract["management_capture"]
    assert capture["contract"] == "management_decision_inputs_v1"
    assert capture["includes_no_action_evaluations"] is True
    assert "gold_555_trailing" in capture["supported_kinds"]


def test_snapshots_serialize_without_later_state_mutation(captured):
    import json
    import management_decision_evidence as capture
    events, _ = captured
    signal = _signal()
    with capture.capture_management_decision(signal, kind="gold_555_trailing", inputs={"bid": 4300.0}) as output:
        signal.candidate_hard_stops[101] = 4280.0
        output["result"] = 0
    before, after = events[-2:]
    assert before["state_before"]["candidate_hard_stops"][101] == 4270.0
    assert after["state_after"]["candidate_hard_stops"][101] == 4280.0
    # The journal serializer must accept every retained state field.
    encoded = json.dumps(events, default=journal._serialize)
    assert json.loads(encoded)[-1]["state_after"]["candidate_hard_stops"]["101"] == 4280.0


@pytest.mark.parametrize("fail_on", [1, 2])
def test_snapshot_preparation_failure_preserves_management_result(captured, monkeypatch, fail_on):
    import management_decision_evidence as capture
    original = capture.signal_state
    calls = 0
    def snapshot(signal):
        nonlocal calls
        calls += 1
        if calls == fail_on:
            raise ValueError("synthetic snapshot failure")
        return original(signal)
    monkeypatch.setattr(capture, "signal_state", snapshot)
    with capture.capture_management_decision(_signal(), kind="gold_555_trailing", inputs={}) as output:
        output["result"] = 17
    assert output["result"] == 17
    assert causal_trace.current_decision_id() is None


def test_snapshot_failure_does_not_mask_the_original_management_error(captured, monkeypatch):
    import management_decision_evidence as capture
    monkeypatch.setattr(capture, "signal_state", lambda signal: (_ for _ in ()).throw(ValueError("snapshot")))
    with pytest.raises(OSError, match="management"):
        with capture.capture_management_decision(_signal(), kind="gold_555_trailing", inputs={}):
            raise OSError("management")
    assert causal_trace.current_decision_id() is None


def test_serialized_state_keeps_microseconds_at_the_time_exit_boundary(captured):
    import json
    events, _ = captured
    signal = _signal()
    signal.candidate_first_fill_at = NOW + timedelta(microseconds=500)
    summary = {"positions_complete": True, "realized_complete": True,
               "floating_pl": 0.25, "realized_pl": 0.0, "total_pl": 0.25,
               "n_open": 1, "open_tickets": [101]}
    at = NOW + timedelta(minutes=180, microseconds=499)
    original = monitor._apply_gold_555_basket_guard(signal, summary, now=at)
    assert original.action == "none"
    start = next(r for r in json.loads(json.dumps(events, default=journal._serialize))
                 if r.get("management_kind") == "gold_555_basket_guard" and r["ev"].endswith("_started"))
    first_fill = datetime.fromisoformat(start["state_before"]["candidate_first_fill_at"])
    now = datetime.fromisoformat(start["decision_inputs"]["now_utc"])
    if first_fill.tzinfo is None:
        first_fill = first_fill.replace(tzinfo=timezone.utc)
    replay = gold_555_live_candidate.evaluate_guard(
        policy=gold_555_live_candidate.Gold555Policy(),
        state=gold_555_live_candidate.Gold555GuardState(), total_pl=0.25,
        n_open=1, elapsed_min=(now - first_fill).total_seconds() / 60,
        money_evidence_complete=True,
    )
    assert first_fill.microsecond == 500
    assert replay == original
