from dataclasses import replace
from datetime import timedelta

import numpy as np
import pytest

from research.dubai_iterative.dataset import RolloverEvent
from research.dubai_iterative.shared_replay import SharedReplayProfile, simulate_shared
from research.management_observation import ReadCycleProfile
from tests.test_client_terminal_close import execution, strategy, tape
from tests.test_iterative_market import market
from tests.test_iterative_protection import BASE, BASE_NS
from tests.test_shared_policy_replay import basket, spec


PROFILE = SharedReplayProfile(account_currency="EUR", name="shared_interquote_reads_v2")


def reading_spec(data, *, rules=None, reads=None):
    return replace(spec("canal1_observed", [], tape=data,
        strategy=rules or strategy(profit_lock_arm=10., profit_lock_giveback=2.),
        execution=replace(execution(), market=market(entry_acknowledgement_delay_ms=0,
            close_acknowledgement_delay_ms=0))),
        management_reads=reads or ReadCycleProfile(sample_delay_ns=500_000_000,
            response_delay_ns=500_000_000))


def test_unobserved_profit_peak_cannot_arm_guard_despite_diagnostic_peak():
    data = tape([100., 104.] + [100.] * 12,
        offsets=[0, .5, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], close_at=8)
    item = reading_spec(data)
    report = simulate_shared([item], profile=PROFILE)
    result = basket(report, item.path.signal_id)
    assert result.exits[0].reason == "provider_close"
    assert result.max_favourable_eur >= 10
    assert report.read_cycles
    assert all(row["summary"]["total_pl"] < 10 for row in report.read_cycles if row["summary"] is not None)


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("delay", [0, 250_000_000, 500_000_000])
def test_observed_peak_and_giveback_use_delivered_snapshot(direction, delay):
    quotes = [100., 104., 104., 104., 104.] + [100.] * 11
    if direction == "SELL":
        quotes = [200 - value for value in quotes]
    item = reading_spec(tape(quotes, direction=direction, close_at=12),
        reads=ReadCycleProfile(sample_delay_ns=delay, response_delay_ns=delay))
    report = simulate_shared([item], profile=PROFILE)
    result = basket(report, item.path.signal_id)
    assert not report.blockers
    decisions = [row["policy_decision"] for row in report.read_cycles if row["completed"]]
    closed = [row for row in decisions if row["action"] == "close"]
    assert len(closed) == 1 and closed[0]["reason"] == "profit_lock"
    close = closed[0]
    assert close["armed"] and close["observed_minor"] < close["peak_minor"]
    assert any(row["armed"] and row["action"] == "none" for row in decisions)
    queued = next(row for row in result.client_events if row.kind == "queued" and row.operation == "close")
    assert queued.decision_ns == close["time_ns"]
    assert queued.decision_index == close["quote_index"]
    assert len(result.exits) == 1 and result.exits[0].reason == "profit_lock"
    assert result.exits[0].tick_index > queued.decision_index
    for cycle in report.read_cycles:
        assert cycle["policy_decision"]["time_ns"] == BASE_NS + cycle["records"][-1]["delivery_ns"]
        for record in cycle["records"]:
            assert record["source_ns"] <= record["sample_ns"] <= record["delivery_ns"]
            assert int(item.path.times_ns[record["source_ordinal"]]) == BASE_NS + record["source_ns"]


def test_entry_ack_between_quotes_starts_reads_without_creating_a_quote():
    item = reading_spec(tape([100.] * 7, offsets=[0, 3, 6, 9, 12, 15, 18], close_at=12),
                        reads=ReadCycleProfile(sample_delay_ns=100_000_000, response_delay_ns=100_000_000))
    item = replace(item, execution=replace(item.execution, market=market(
        entry_acknowledgement_delay_ms=750, close_acknowledgement_delay_ms=0)))
    report = simulate_shared([item], profile=PROFILE)
    result = basket(report, item.path.signal_id)
    assert not report.blockers
    assert len(result.entries) == 1
    assert result.entries[0].acknowledged_ns == BASE_NS + 750_000_000
    first = report.read_cycles[0]
    assert first["requests"][0]["requested_ns"] == 750_000_000
    assert first["records"][0]["source_ordinal"] == 0
    assert first["policy_decision"]["time_ns"] == BASE_NS + 1_150_000_000


@pytest.mark.parametrize("close_at", [1, 2, 3])
def test_provider_close_cancels_pending_read_without_losing_close(close_at):
    item = reading_spec(tape([100.] * 15, close_at=close_at),
        reads=ReadCycleProfile(sample_delay_ns=1_000_000_000, response_delay_ns=1_000_000_000))
    report = simulate_shared([item], profile=PROFILE)
    result = basket(report, item.path.signal_id)
    assert not report.blockers
    assert len(result.exits) == 1 and result.exits[0].reason == "provider_close"
    assert len(report.read_cycles) == 1
    cycle = report.read_cycles[0]
    assert not cycle["completed"] and cycle["summary"] is None
    assert cycle["request_id"] is None
    assert cycle["blockers"] == ["read_cycle_cancelled"]
    assert cycle["records"][-1]["disposition"] == "discarded"
    assert not any(row.get("policy_decision", {}).get("action") == "close" for row in report.read_cycles)


@pytest.mark.parametrize("profile_change, reason", [
    ({"max_read_cycles": 1}, "shared_read_cycle_budget_exhausted"),
    ({"max_read_payload_bytes": 1}, "shared_read_payload_budget_exhausted"),
    ({"max_events": 12}, "budget"),
])
def test_global_budget_does_not_report_complete_risk(profile_change, reason):
    from research.dubai_iterative.shared_replay import summarize_shared_risk

    item = reading_spec(tape([100.] * 15, close_at=12))
    report = simulate_shared([item], profile=replace(PROFILE, **profile_change))
    assert len(report.baskets) == 1
    assert any(reason in blocker for blocker in report.blockers)
    assert summarize_shared_risk(report)["metrics"] is None
    assert not report.full_live_parity_verified


def test_cutoff_retains_incomplete_read_and_unknown_result():
    item = reading_spec(tape([100.] * 2, close_at=10),
        reads=ReadCycleProfile(sample_delay_ns=5_000_000_000))
    report = simulate_shared([item], profile=PROFILE)
    result = basket(report, item.path.signal_id)
    assert result.pnl_eur is None
    assert "management_read_incomplete_at_data_end" in result.blockers
    assert report.read_cycles[0]["summary"] is None
    assert "read_cycle_incomplete" in report.read_cycles[0]["blockers"]


def test_read_mode_requires_explicit_supported_contract():
    item = reading_spec(tape([100.] * 10))
    with pytest.raises(ValueError, match="explicit interquote"):
        simulate_shared([item], profile=replace(PROFILE, name="shared_quote_rounds_v1"))
    with pytest.raises(ValueError, match="declared read consumer"):
        simulate_shared([replace(item, management_reads=None)], profile=PROFILE)
    with pytest.raises(ValueError, match="supported money rules"):
        simulate_shared([replace(item, genome=strategy(stop_mode="basket_money", stop_value=80.))], profile=PROFILE)


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_stale_position_snapshot_after_native_stop_cannot_double_realize(direction):
    quotes = [100.] * 3 + [89.] * 12
    if direction == "SELL":
        quotes = [200 - value for value in quotes]
    item = reading_spec(tape(quotes, direction=direction, close_at=100),
        rules=strategy(stop_mode="fixed_move", stop_value=10., time_exit_min=.04, time_exit_mode="always"),
        reads=ReadCycleProfile(sample_delay_ns=500_000_000, response_delay_ns=1_000_000_000))
    report = simulate_shared([item], profile=PROFILE)
    result = basket(report, item.path.signal_id)
    assert not report.blockers
    assert len(result.exits) == 1 and result.exits[0].reason == "initial_sl"
    assert result.exits[0].tick_index == 3
    cycle = report.read_cycles[0]
    assert cycle["summary"]["n_open"] == 1
    assert cycle["records"][-1]["source_ordinal"] == 2
    assert cycle["policy_decision"]["reason"] == "time_exit"
    rejected = [row for row in result.market_events if row.kind == "close_rejected"]
    assert len(rejected) == 1 and rejected[0].reason == "position_already_closed"
    assert not [row for row in result.market_events if row.kind == "close_filled"]
    assert result.pnl_eur == result.exits[0].pnl_eur


@pytest.mark.parametrize("swap_minor", [0, -125, 75])
def test_history_uses_exit_fx_and_swap_not_later_quote(swap_minor):
    data = tape([100.] * 3 + [89.] * 13, close_at=100)
    rates = np.array([1.] * 4 + [2.] * 12)
    data = replace(data, conversion_orientation="account_base_profit_quote", fx_bid=rates, fx_ask=rates,
        rollover_events=(RolloverEvent(BASE + timedelta(seconds=1), np.array([0, 0, 0, 0, swap_minor])),))
    item = reading_spec(data, rules=strategy(stop_mode="fixed_move", stop_value=10.),
        reads=ReadCycleProfile(sample_delay_ns=1_500_000_000, response_delay_ns=500_000_000))
    report = simulate_shared([item], profile=PROFILE)
    result = basket(report, item.path.signal_id)
    assert not report.blockers
    assert len(report.read_cycles) == 1
    cycle = report.read_cycles[0]
    history = cycle["records"][-1]
    assert history["operation"] == "history_deals_get_position"
    assert history["source_ordinal"] > result.exits[0].tick_index
    exit_deal = history["payload"]["data"][-1]
    assert exit_deal["profit"] == -44.8
    assert exit_deal["swap"] == swap_minor / 100
    assert cycle["summary"]["total_pl"] == pytest.approx(float(result.pnl_eur))
    assert cycle["summary"]["realized_complete"]


def test_open_position_profit_excludes_accrued_swap_and_exit_slippage():
    data = replace(tape([100.] * 15, close_at=12), rollover_events=(
        RolloverEvent(BASE + timedelta(seconds=1), np.array([0, 0, 0, 0, -125])),))
    item = reading_spec(data)
    item = replace(item, execution=replace(item.execution, exit_slippage=.5))
    report = simulate_shared([item], profile=PROFILE)
    assert not report.blockers
    completed = [row for row in report.read_cycles if row["completed"]]
    assert completed
    assert all(row["summary"]["floating_pl"] == -.8 for row in completed)
    result = basket(report, item.path.signal_id)
    assert float(result.pnl_eur) == pytest.approx(-.8 - 2. - 1.25)


def test_other_basket_delays_read_without_changing_snapshot_ownership():
    item = reading_spec(tape([100.] * 20, close_at=12))
    other = spec("canal2_busy", [], tape=item.path,
        strategy=strategy(entry_mode="delay", entry_value=1.),
        execution=replace(execution(), market=market(entry_acknowledgement_delay_ms=1750,
                                                     close_acknowledgement_delay_ms=0)))
    report = simulate_shared([item, other], profile=PROFILE)
    assert not report.blockers
    assert all(len(result.entries) == len(result.exits) == 1 for _, _, result in report.baskets)
    cycle = report.read_cycles[0]
    tick, positions = cycle["records"]
    assert tick["delivery_ns"] == 1_000_000_000
    assert positions["requested_ns"] == 1_000_000_000
    assert positions["started_ns"] == 2_750_000_000
    assert positions["source_ordinal"] == 3
    assert cycle["summary"]["lots_total"] == .04
    assert cycle["policy_decision"]["time_ns"] == BASE_NS + 3_750_000_000
    assert all(row["status"] == "released" for row in report.transport["rows"])


def test_timeout_drains_without_delivering_money_and_preserves_provider_exit():
    item = reading_spec(tape([100.] * 16, close_at=10),
        reads=ReadCycleProfile(sample_delay_ns=1_000_000_000, response_delay_ns=1_000_000_000,
                              timeout_ns=500_000_000))
    report = simulate_shared([item], profile=PROFILE)
    result = basket(report, item.path.signal_id)
    assert report.blockers == ("canal1_observed:read_timeout",)
    assert len(result.exits) == 1 and result.exits[0].reason == "provider_close"
    assert all(row["summary"] is None and not row["completed"] for row in report.read_cycles)
    assert all(row["records"][0]["disposition"] == "discarded" for row in report.read_cycles)
    assert all(row["request_id"] is None for row in report.read_cycles)


def test_duplicate_timestamp_records_exact_source_ordinal():
    data = tape([100., 101., 104.] + [100.] * 12,
                offsets=[0, 1, 1] + list(range(2, 14)), close_at=10)
    item = reading_spec(data, reads=ReadCycleProfile(sample_delay_ns=0, response_delay_ns=500_000_000))
    report = simulate_shared([item], profile=PROFILE)
    assert not report.blockers
    records = [record for cycle in report.read_cycles for record in cycle["records"]]
    assert any(record["source_ordinal"] == 1 and record["source_ns"] == 1_000_000_000 for record in records)
    assert any(record["source_ordinal"] == 2 and record["source_ns"] == 1_000_000_000 for record in records)


@pytest.mark.parametrize("delay", [500_000_000, 600_000_000])
@pytest.mark.parametrize("ladder_mode", ["fresh_quote", "snapshot_batch"])
def test_back_to_back_reads_do_not_starve_unobserved_ladder_decisions(delay, ladder_mode):
    item = reading_spec(tape([100.] * 3 + [96.] * 17, close_at=15),
        rules=strategy(leg_count=3, volume_weights=(.04,) * 3,
                       entry_ladder_mode="adverse", entry_ladder_step=1.5),
        reads=ReadCycleProfile(sample_delay_ns=delay, response_delay_ns=delay))
    item = replace(item, execution=replace(item.execution,
        client=replace(item.execution.client, ladder_decision_mode=ladder_mode)))
    report = simulate_shared([item], profile=PROFILE)
    result = basket(report, item.path.signal_id)
    assert not report.blockers
    assert len(result.entries) == len(result.exits) == 3
    added = [row for row in result.client_events if row.kind == "queued" and row.operation == "entry"][1:]
    assert len({row.decision_index for row in added}) == (2 if ladder_mode == "fresh_quote" else 1)
    for entry in added:
        assert entry.decision_index >= 3
        assert entry.decision_ns >= int(item.path.times_ns[entry.decision_index])
    assert result.filled_volume == .12


@pytest.mark.parametrize("delay", [0, 500_000_000])
@pytest.mark.parametrize("bad_index", [1, 2])
def test_invalid_quote_returns_partial_report_without_post_stop_decisions(delay, bad_index):
    quotes = [100.] * 12
    quotes[bad_index] = float("nan")
    item = reading_spec(tape(quotes, close_at=8),
        rules=strategy(stop_mode="fixed_move", stop_value=10.),
        reads=ReadCycleProfile(sample_delay_ns=delay, response_delay_ns=delay))
    report = simulate_shared([item], profile=PROFILE)
    assert len(report.baskets) == 1
    assert any(f"invalid_tick_at_index:{bad_index}" in reason for reason in report.blockers)
    assert basket(report, item.path.signal_id).pnl_eur is None
    for cycle in report.read_cycles:
        assert cycle["requests"][0]["requested_ns"] < bad_index * 1_000_000_000
        if "policy_decision" in cycle:
            assert cycle["policy_decision"]["time_ns"] < BASE_NS + bad_index * 1_000_000_000
        for row in cycle["records"]:
            if row["source_ordinal"] == bad_index:
                assert row["disposition"] == "discarded"
                assert row["payload"]["data"] is None
