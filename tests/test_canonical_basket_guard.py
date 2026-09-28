from dataclasses import asdict, replace
from datetime import datetime, timedelta

import pytest
import numpy as np

import dubai_live_candidate as dubai
import gold_555_live_candidate as gold
import position_lifecycle_monitor as monitor
from state import Signal
from basket_management import guard_observation
from research.canonical_guard import CanonicalGuardComponent
from research.dubai_iterative.shared_replay import simulate_shared
from research.management_observation import ReadCycleProfile
from tests.test_client_terminal_close import tape, strategy
from tests.test_shared_management_reads import reading_spec, PROFILE
from tests.test_shared_policy_replay import basket


@pytest.mark.parametrize("module,channel,apply", [
    (dubai, "canal1", monitor._apply_candidate_basket_guard_unrecorded),
    (gold, "canal2", monitor._apply_gold_555_basket_guard_unrecorded),
])
@pytest.mark.parametrize("summary, elapsed, expected", [
    ({"pl": 31., "total_pl": 31.}, 240, "arm"),
    ({"pl": 31., "total_pl": None, "realized_complete": False}, 240, "evidence_incomplete"),
    ({"floating_pl": 1., "realized_pl": 35., "total_pl": None}, 0, "arm"),
    ({"pl": 2., "floating_pl": 1., "realized_pl": 35., "total_pl": None}, 0, "none"),
])
def test_runtime_summary_fallback_and_arm_priority_before_extraction(monkeypatch, module, channel, apply,
                                                                    summary, elapsed, expected):
    monkeypatch.setattr(monitor, "_journal_event", lambda *a, **k: None)
    monkeypatch.setattr(monitor.pending_actions, "enqueue_close_position", lambda *a, **k: None)
    monkeypatch.setattr(monitor.pending_actions, "enqueue_cancel_pending", lambda *a, **k: None)
    now = datetime(2026, 9, 21, 12)
    signal = Signal(channel=channel, message_id=1, direction="BUY", market_ticket=1,
                    live_strategy_id=module.CANDIDATE_ID, live_strategy_fingerprint=module.CANDIDATE_FINGERPRINT,
                    timestamp=now - timedelta(minutes=elapsed), candidate_first_fill_at=now - timedelta(minutes=elapsed))
    result = apply(signal, {"n_open": 1, "open_tickets": [1], **summary}, now=now)
    assert result.action == expected
    assert signal.basket_guard_peak_pl == result.state.peak_pl
    assert signal.basket_guard_armed == result.state.armed
    if expected == "evidence_incomplete":
        assert result.state.peak_pl is None and not result.state.armed


@pytest.mark.parametrize("strategy_id, value, elapsed, action, reason", [
    (dubai.CANDIDATE_ID, -25., 0, "close", "basket_stop"),
    (dubai.CANDIDATE_ID, -24.999, 0, "none", None),
    (dubai.CANDIDATE_ID, 9.999, 0, "none", None),
    (dubai.CANDIDATE_ID, 10., 100, "arm", "profit_arm"),
    (dubai.CANDIDATE_ID, 0., 40, "close", "loss_time_exit"),
    (dubai.CANDIDATE_ID, .001, 40, "none", None),
    (gold.CANDIDATE_ID, -40., 0, "none", None),
    (gold.CANDIDATE_ID, -.001, 180, "none", None),
    (gold.CANDIDATE_ID, 0., 180, "close", "non_negative_time_exit"),
    (gold.CANDIDATE_ID, 29.999, 0, "none", None),
    (gold.CANDIDATE_ID, 30., 180, "arm", "profit_arm"),
])
def test_canonical_priorities_and_unrounded_money(strategy_id, value, elapsed, action, reason):
    component = CanonicalGuardComponent(strategy_id)
    decision = component.evaluate(component.initial_state(), {"pl": value, "n_open": 1}, elapsed)
    assert (decision.action, decision.reason) == (action, reason)
    assert decision.observed_pl == value


@pytest.mark.parametrize("strategy_id", [dubai.CANDIDATE_ID, gold.CANDIDATE_ID])
def test_recovery_precedes_incomplete_money_and_preserves_original_reason(strategy_id):
    component = CanonicalGuardComponent(strategy_id)
    state = replace(component.initial_state(), triggered=True, recovery_pending=True, trigger_reason="profit_lock")
    summary = {"floating_pl": -2., "realized_complete": False, "total_pl": None, "n_open": 1}
    result = component.evaluate(state, summary, 1)
    assert result.action == "close" and result.reason == "recovery"
    assert result.state.trigger_reason == "profit_lock" and not result.state.recovery_pending
    assert component.evaluate(result.state, summary, 2).action == "none"


@pytest.mark.parametrize("strategy_id", [dubai.CANDIDATE_ID, gold.CANDIDATE_ID])
def test_missing_history_does_not_arm_or_change_peak_until_recovered(strategy_id):
    component = CanonicalGuardComponent(strategy_id)
    state = component.initial_state()
    degraded = component.evaluate(state, {"pl": 100., "n_open": 1, "realized_complete": False}, 0)
    assert degraded.action == "evidence_incomplete" and degraded.state == state
    recovered = component.evaluate(degraded.state, {"pl": 50., "n_open": 1}, 0)
    assert recovered.action == "arm" and recovered.state.peak_pl == 50.


def component_spec(strategy_id, data, **changes):
    component = CanonicalGuardComponent(strategy_id)
    item = reading_spec(data, reads=ReadCycleProfile())
    return replace(item, channel=component.channel,
                   path=replace(item.path, signal_id=component.channel + "_canonical"),
                   guard_component=component, **changes)


@pytest.mark.parametrize("strategy_id, reason", [
    (dubai.CANDIDATE_ID, "basket_stop"), (gold.CANDIDATE_ID, "provider_close"),
])
def test_received_summary_drives_channel_specific_guard_exit(strategy_id, reason):
    item = component_spec(strategy_id, tape([100., 100.] + [90.] * 12, close_at=10))
    report = simulate_shared([item], profile=PROFILE)
    result = basket(report, item.path.signal_id)
    assert not report.blockers
    assert len(result.exits) == 1 and result.exits[0].reason == reason
    metadata = report.management_profiles[0]
    assert metadata["reads"] == asdict(item.management_reads)
    assert metadata["guard_component"] == item.guard_component.descriptor()
    assert not metadata["guard_component"]["complete_strategy_admitted"]
    assert not report.full_live_parity_verified
    for cycle in report.read_cycles:
        if "policy_decision" in cycle:
            decision = cycle["policy_decision"]
            assert decision["guard_strategy_id"] == strategy_id
            assert "state_before" in decision and "state_after" in decision


def test_gold_canonical_guard_does_not_use_generic_ten_euro_arm():
    item = component_spec(gold.CANDIDATE_ID,
        tape([100., 104., 104., 100.] + [100.] * 10, close_at=10))
    report = simulate_shared([item], profile=PROFILE)
    assert not report.blockers
    assert basket(report, item.path.signal_id).exits[0].reason == "provider_close"
    decisions = [row["policy_decision"] for row in report.read_cycles if "policy_decision" in row]
    assert max(row["observed_pl"] for row in decisions) > 10
    assert all(not row["state_after"]["armed"] for row in decisions)


def test_gold_arm_at_time_boundary_does_not_close_in_same_observation():
    item = component_spec(gold.CANDIDATE_ID,
        tape([100.] + [108.] * 6, offsets=[0, 10800, 10801, 10802, 10803, 10804, 10805], close_at=10803),
        read_interval_ns=10_800_000_000_000)
    report = simulate_shared([item], profile=PROFILE)
    assert not report.blockers
    decisions = [row["policy_decision"] for row in report.read_cycles]
    assert decisions[-1]["elapsed_min"] == 180
    assert decisions[-1]["action"] == "arm"
    assert basket(report, item.path.signal_id).exits[0].reason == "provider_close"


def test_explicit_null_pl_is_not_replaced_with_realized_sum():
    row = guard_observation({"pl": None, "floating_pl": 2., "realized_pl": 40.})
    assert row.total_pl is None and row.observed_pl == 2.


def test_guard_component_cannot_be_attached_to_wrong_channel_or_without_reads():
    item = component_spec(dubai.CANDIDATE_ID, tape([100.] * 8))
    with pytest.raises(ValueError, match="matching channel"):
        replace(item, guard_component=CanonicalGuardComponent(gold.CANDIDATE_ID))
    with pytest.raises(ValueError, match="declared management reads"):
        replace(item, management_reads=None)


@pytest.mark.parametrize("limit", [3, 4])
def test_failed_close_keeps_decision_and_separate_application_failure(limit):
    item = component_spec(dubai.CANDIDATE_ID, tape([100.] + [90.] * 8, close_at=7))
    item = replace(item, execution=replace(item.execution,
        client=replace(item.execution.client, max_events=limit)))
    report = simulate_shared([item], profile=PROFILE)
    assert any("client_event_budget_exhausted" in row for row in report.blockers)
    cycle = next(row for row in report.read_cycles if row["summary"] and row["summary"]["total_pl"] < -25)
    decision = cycle["policy_decision"]
    assert decision["action"] == "close" and decision["reason"] == "basket_stop"
    assert not decision["state_before"]["triggered"] and decision["state_after"]["triggered"]
    applied = cycle["policy_application"]
    assert applied["status"] == "blocked"
    assert applied["blocker"] == "client_event_budget_exhausted"
    assert applied["state_after_application"]["recovery_pending"]
    assert applied["state_after_application"]["trigger_reason"] == "basket_stop"
    assert not report.full_live_parity_verified


@pytest.mark.parametrize("strategy_id", [dubai.CANDIDATE_ID, gold.CANDIDATE_ID])
def test_unknown_position_money_retains_blocked_observation_and_scope(strategy_id):
    data = tape([100.] * 10, close_at=8)
    valid = np.ones(10, dtype=bool)
    valid[1] = False
    data = replace(data, conversion_orientation="account_base_profit_quote", fx_valid=valid)
    item = component_spec(strategy_id, data)
    report = simulate_shared([item], profile=PROFILE)
    assert "canonical_guard_observation_invalid" in report.blockers
    assert len(report.baskets) == 1
    cycle = report.read_cycles[-1]
    assert cycle["completed"] and not cycle["summary"]["positions_complete"]
    assert cycle["policy_decision"]["action"] == "blocked"
    assert cycle["policy_application"]["status"] == "blocked"
    assert cycle["policy_decision"]["state_before"] == cycle["policy_decision"]["state_after"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_invalid_money_never_changes_canonical_state(value):
    component = CanonicalGuardComponent(gold.CANDIDATE_ID)
    state = component.initial_state()
    with pytest.raises(ValueError, match="finite"):
        component.evaluate(state, {"pl": value, "n_open": 1}, 0)
    assert not state.armed and state.peak_pl is None


def test_guard_components_keep_independent_state_on_shared_connection():
    data = tape([100., 100., 104., 100.] + [100.] * 12, close_at=12)
    items = [component_spec(name, data) for name in (dubai.CANDIDATE_ID, gold.CANDIDATE_ID)]
    report = simulate_shared(items, profile=PROFILE)
    assert not report.blockers
    assert basket(report, items[0].path.signal_id).exits[0].reason == "profit_lock"
    assert basket(report, items[1].path.signal_id).exits[0].reason == "provider_close"
    golden = [row["policy_decision"] for row in report.read_cycles
              if row["channel"] == "canal2" and "policy_decision" in row]
    assert golden and all(not row["state_after"]["armed"] for row in golden)
    assert len(report.management_profiles) == 2


def test_partial_enqueue_failure_preserves_queued_and_not_submitted_tickets():
    item = component_spec(dubai.CANDIDATE_ID, tape([100., 95.] + [90.] * 10, close_at=8))
    item = replace(item, genome=strategy(leg_count=2, volume_weights=(.04, .04),
        entry_ladder_mode="adverse", entry_ladder_step=1.5),
        execution=replace(item.execution, client=replace(item.execution.client, max_events=8)))
    report = simulate_shared([item], profile=PROFILE)
    assert "client_event_budget_exhausted" in report.blockers
    row = report.read_cycles[-1]
    assert row["policy_decision"]["action"] == "close"
    assert row["policy_application"]["status"] == "blocked"
    assert row["policy_application"]["queued_close_tickets"] == ["sim_1"]
    assert row["policy_application"]["submitted_close_tickets"] == []
    assert row["summary"]["open_tickets"] == [1, 2]
