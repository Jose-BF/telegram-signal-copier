from dataclasses import replace

import numpy as np
import pytest

from basket_stop import basket_stop_requirement
from mt5_read_protocol import ReadOperation
from research.broker_valuation import linear_profit_value
from research.canonical_guard import CanonicalGuardComponent
from research.canonical_stop import DubaiBasketStopComponent
from research.dubai_iterative.shared_replay import simulate_shared, summarize_shared_risk
from research.dubai_iterative.protection import ProtectionBook
from research.management_observation import ReadCycleProfile
from tests.test_client_terminal_close import strategy, tape
from tests.test_iterative_protection import BASE_NS, profile
from tests.test_iterative_market import market
from tests.test_shared_management_reads import PROFILE, reading_spec
from tests.test_shared_policy_replay import basket, spec


STOP_READS = ReadCycleProfile(sample_delay_ns=1_000_000, response_delay_ns=1_000_000,
                             max_reads=1024)


def stop_spec(quotes, *, direction="BUY", close_at=25, reads=STOP_READS, rules=None, **changes):
    item = reading_spec(tape(quotes, direction=direction, close_at=close_at),
                        rules=rules or strategy(), reads=ReadCycleProfile())
    return replace(item, stop_component=DubaiBasketStopComponent(reads), **changes)


def plans(report):
    return [row for row in report.read_cycles if row["contract"] == "sequential_basket_stop_plan_v1"]


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_stop_plan_installs_then_confirms_without_generic_removal(direction):
    item = stop_spec([100.] * 35, direction=direction)
    report = simulate_shared([item], profile=PROFILE)
    assert not report.blockers
    result = basket(report, item.path.signal_id)
    stop_plans = plans(report)
    assert len(stop_plans) >= 2
    first = stop_plans[0]
    level = first["plan"]["stop_price"]
    assert first["stop_available"] and not first["protection_installed"]
    assert first["stop_application"]["tickets"][0]["status"] == "queued"
    events = result.protection_events
    sent = next(row for row in events if row.kind == "requested")
    installed = next(row for row in events if row.kind == "installed")
    acknowledged = next(row for row in events if row.kind == "acknowledged")
    assert sent.sl == installed.sl == level
    assert sent.timestamp_ns < installed.timestamp_ns < acknowledged.timestamp_ns
    assert any(row["stop_application"]["tickets"][0]["status"] == "observed_protected"
               for row in stop_plans[1:] if row["completed"])
    assert all(row.sl == level for row in events if row.kind in {"requested", "installed"})
    money = next(row for row in report.read_cycles if row["contract"] == "sequential_basket_reads_v1")
    assert money["requests"][0]["requested_ns"] >= first["records"][-1]["delivery_ns"]
    assert money["requests"][0]["requested_ns"] < acknowledged.timestamp_ns - BASE_NS
    assert not report.full_live_parity_verified


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_installed_stop_can_execute_before_its_ack(direction):
    quotes = [100.] * 3 + [90. if direction == "BUY" else 110.] * 18
    item = stop_spec(quotes, direction=direction, close_at=100)
    item = replace(item, execution=replace(item.execution,
        protection=profile(acknowledgement_delay_ms=10_000)))
    report = simulate_shared([item], profile=PROFILE)
    result = basket(report, item.path.signal_id)
    assert not report.blockers
    assert len(result.exits) == 1 and result.exits[0].reason == "dubai_basket_stop"
    assert result.exits[0].tick_index == 3
    ack = next(row for row in result.protection_events if row.kind == "acknowledged")
    assert ack.timestamp_ns > int(item.path.times_ns[3])
    assert not [row for row in result.market_events if row.kind == "close_filled"]
    # Crossing a stop in a gap is not a fill at the planned EUR loss budget.
    assert float(result.pnl_eur) == pytest.approx(-40.8)
    risk = summarize_shared_risk(report)
    assert risk["metrics"]["max_drawdown"] == 4080
    assert risk["metrics"]["final_net"] == -4080


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_rejected_stop_retries_without_claiming_installation_or_forcing_close(direction):
    quotes = [100.] + [90.] * 8 + [100.] * 26
    if direction == "SELL":
        quotes = [200 - value for value in quotes]
    item = stop_spec(quotes, direction=direction)
    report = simulate_shared([item], profile=PROFILE)
    assert not report.blockers
    result = basket(report, item.path.signal_id)
    assert result.exits[0].reason == "provider_close"
    events = result.protection_events
    rejected = [row for row in events if row.kind == "rejected"]
    installed = [row for row in events if row.kind == "installed"]
    assert rejected and installed and rejected[-1].timestamp_ns < installed[0].timestamp_ns
    assert len([row for row in events if row.kind == "requested"]) >= 3
    early = [row for row in plans(report) if row["completed"]
             and row["stop_application"]["time_ns"] < installed[0].timestamp_ns]
    assert all(row["stop_application"]["tickets"][0]["observed_sl"] == 0 for row in early)


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_later_dca_fill_enters_next_plan_not_prior_snapshot(direction):
    quotes = [100., 98.] + [96.] * 43
    if direction == "SELL":
        quotes = [200 - value for value in quotes]
    item = stop_spec(quotes, direction=direction, close_at=35,
        rules=strategy(leg_count=3, volume_weights=(.01, .04, .04),
                       entry_ladder_mode="adverse", entry_ladder_step=1.5))
    report = simulate_shared([item], profile=PROFILE)
    assert not report.blockers
    result = basket(report, item.path.signal_id)
    assert len(result.entries) == 3
    complete = [row for row in plans(report) if row["completed"]]
    assert len(complete[0]["plan"]["positions"]) == 1
    assert any(len(row["plan"]["positions"]) == 3 for row in complete)
    for row in complete:
        sample = row["records"][0]["sample_ns"] + BASE_NS
        acknowledged = {i + 1 for i, entry in enumerate(result.entries)
                        if entry.acknowledged_ns is not None and entry.acknowledged_ns <= sample}
        assert set(row["plan"]["positions"]) <= acknowledged


def test_provider_terminal_during_profit_read_cancels_unapplied_plan():
    item = stop_spec([100.] * 20, close_at=1,
        reads=replace(STOP_READS, sample_delay_ns=50_000_000, response_delay_ns=50_000_000))
    report = simulate_shared([item], profile=PROFILE)
    assert not report.blockers
    first = plans(report)[0]
    assert not first["completed"] and first["plan"] is None
    assert first["blockers"] == ["read_cycle_cancelled"]
    assert any(row["operation"] == ReadOperation.PROFIT.value for row in first["records"])
    assert "stop_application" not in first
    result = basket(report, item.path.signal_id)
    assert not [row for row in result.protection_events if row.kind == "requested"]
    assert result.exits[0].reason == "provider_close"


@pytest.mark.parametrize("orientation", ["account_base_profit_quote", "profit_base_account_quote"])
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_each_profit_sample_uses_current_fx_and_requested_side_without_cent_rounding(orientation, direction):
    item = stop_spec([100.] * 35, direction=direction,
        reads=replace(STOP_READS, sample_delay_ns=20_000_000, response_delay_ns=20_000_000))
    size = len(item.path.times_ns)
    fx_bid, fx_ask = np.full(size, 1.2), np.full(size, 1.3)
    fx_bid[1:], fx_ask[1:] = .7, .8
    item = replace(item, path=replace(item.path, conversion_orientation=orientation,
                                     fx_bid=fx_bid, fx_ask=fx_ask))
    report = simulate_shared([item], profile=PROFILE)
    assert not report.blockers
    records = [row for row in plans(report)[0]["records"] if row["operation"] == ReadOperation.PROFIT.value]
    assert len({row["source_ordinal"] for row in records}) > 1
    for row in records:
        p, i = row["params"], row["source_ordinal"]
        raw = (1 if p["action"] == 0 else -1) * (p["price_close"] - p["price_open"]) * p["volume"] * 100
        expected = (raw / (fx_ask[i] if raw >= 0 else fx_bid[i])
                    if orientation == "account_base_profit_quote"
                    else raw * (fx_bid[i] if raw >= 0 else fx_ask[i]))
        assert row["payload"]["data"] == pytest.approx(expected, abs=1e-10)
    assert any(abs(row["payload"]["data"] - round(row["payload"]["data"], 2)) > 1e-6 for row in records)


def test_unknown_conversion_mid_calculation_does_not_install_a_partial_price():
    item = stop_spec([100.] * 35,
        reads=replace(STOP_READS, sample_delay_ns=20_000_000, response_delay_ns=20_000_000))
    valid = np.ones(len(item.path.times_ns), dtype=bool)
    valid[1:3] = False
    item = replace(item, path=replace(item.path, conversion_orientation="account_base_profit_quote", fx_valid=valid))
    report = simulate_shared([item], profile=PROFILE)
    first = plans(report)[0]
    assert first["completed"] and first["plan"]["stop_price"] is None
    assert first["stop_application"]["status"] == "unavailable"
    assert first["records"][-1]["payload"]["data"] is None
    result = basket(report, item.path.signal_id)
    sent = next(row for row in result.protection_events if row.kind == "requested")
    assert sent.timestamp_ns > first["stop_application"]["time_ns"]
    assert any(row["stop_available"] for row in plans(report)[1:])


@pytest.mark.parametrize("change, reason", [
    ({"reads": replace(STOP_READS, max_reads=10)}, "read_cycle_budget_exhausted"),
    ({"profile": replace(PROFILE, max_rounds_per_quote=10), "reads": ReadCycleProfile(max_reads=1024)}, "shared_round_budget_exhausted"),
    ({"profile": replace(PROFILE, max_read_payload_bytes=1)}, "shared_read_payload_budget_exhausted"),
])
def test_incomplete_stop_plan_is_retained_and_not_applied(change, reason):
    item = stop_spec([100.] * 35, reads=change.get("reads", STOP_READS))
    report = simulate_shared([item], profile=change.get("profile", PROFILE))
    assert any(reason in value for value in report.blockers)
    first = plans(report)[0]
    assert first["plan"] is None and not first["stop_available"]
    assert "stop_application" not in first
    assert len(report.baskets) == 1


@pytest.mark.parametrize("direction, installed, required", [("BUY", 95., 94.), ("SELL", 105., 106.)])
def test_requirement_protects_stronger_observation_and_exact_retry_boundary(direction, installed, required):
    assert basket_stop_requirement(direction, {"sl": installed, "point": .01}, required, now=10.) == "observed_protected"
    for elapsed, force, expected in [(4.999, False, "retry_deferred"), (5., False, "request"), (0., True, "request")]:
        assert basket_stop_requirement(direction, {"sl": 0, "point": .01}, required,
            last_level=required, last_request=1., now=1. + elapsed, force=force) == expected


@pytest.mark.parametrize("orientation", ["account_base_profit_quote", "profit_base_account_quote"])
@pytest.mark.parametrize("raw_sign", [-1, 1])
def test_profit_model_checks_both_conversion_sides(orientation, raw_sign):
    data = tape([100.] * 2)
    data = replace(data, conversion_orientation=orientation, fx_bid=np.array([1.1, 1.1]), fx_ask=np.array([1.3, 1.3]))
    params = {"action": 0, "price_open": 100., "price_close": 100. + raw_sign * .12345, "volume": .04}
    raw = raw_sign * .12345 * 4
    expected = (raw / (1.3 if raw_sign > 0 else 1.1) if orientation == "account_base_profit_quote"
                else raw * (1.1 if raw_sign > 0 else 1.3))
    assert linear_profit_value(data, params, 0) == pytest.approx(expected)


def test_stop_component_requires_explicit_exclusive_contract():
    item = stop_spec([100.] * 35)
    with pytest.raises(ValueError, match="exclusive"):
        simulate_shared([replace(item, genome=strategy(stop_mode="fixed_move", stop_value=10.))], profile=PROFILE)
    with pytest.raises(ValueError, match="canal1"):
        replace(item, channel="canal2", path=replace(item.path, signal_id="canal2_other"))
    with pytest.raises(ValueError, match="valuation"):
        DubaiBasketStopComponent(STOP_READS, valuation="native_verified")


def test_drained_stop_timeout_does_not_starve_money_guard():
    item = stop_spec([100.] * 2 + [90.] * 38, close_at=30,
        reads=ReadCycleProfile(sample_delay_ns=3_000_000_000, response_delay_ns=3_000_000_000,
                              timeout_ns=1_000_000_000, max_reads=1024),
        guard_component=CanonicalGuardComponent("dubai_balanced_v1"))
    report = simulate_shared([item], profile=PROFILE)
    assert any("read_timeout" in reason for reason in report.blockers)
    money = [row for row in report.read_cycles if "policy_decision" in row]
    assert money and money[0]["policy_decision"]["action"] == "close"
    assert money[0]["policy_decision"]["reason"] == "basket_stop"
    assert money[0]["policy_decision"]["time_ns"] == BASE_NS + 6_000_000_000
    assert basket(report, item.path.signal_id).exits[0].reason == "basket_stop"


def test_stop_and_money_poll_deadlines_remain_independent():
    item = stop_spec([100.] * 40, close_at=30, read_interval_ns=10_000_000_000)
    report = simulate_shared([item], profile=PROFILE)
    assert not report.blockers
    starts = [row["requests"][0]["requested_ns"] for row in plans(report)]
    assert starts[:4] == [0, 5_000_000_000, 10_000_000_000, 15_000_000_000]
    money = [row for row in report.read_cycles if "policy_decision" in row]
    money_starts = [row["requests"][0]["requested_ns"] for row in money]
    assert all(b - a >= item.read_interval_ns for a, b in zip(money_starts, money_starts[1:]))


@pytest.mark.parametrize("direction, existing", [("BUY", 97.), ("SELL", 103.)])
def test_stronger_installed_stop_is_observed_not_replaced(monkeypatch, direction, existing):
    original = ProtectionBook.open

    def open_with_broker_protection(self, ticket, *args):
        original(self, ticket, *args)
        self.states[ticket].sl = existing

    monkeypatch.setattr(ProtectionBook, "open", open_with_broker_protection)
    item = stop_spec([100.] * 35, direction=direction)
    report = simulate_shared([item], profile=PROFILE)
    assert not report.blockers
    first = plans(report)[0]
    assert first["plan"]["positions"][1]["sl"] == existing
    assert first["stop_application"]["tickets"][0]["status"] == "observed_protected"
    result = basket(report, item.path.signal_id)
    assert not [row for row in result.protection_events if row.kind == "requested"]


def test_sl_only_request_preserves_tp_at_dispatch_not_at_old_snapshot(monkeypatch):
    original = ProtectionBook.process

    def external_tp_update(self, index, now, quote):
        if index == 1:
            for state in self.states.values():
                state.tp, state.tp_reason = 110., "external_tp"
        return original(self, index, now, quote)

    monkeypatch.setattr(ProtectionBook, "process", external_tp_update)
    item = stop_spec([100.] * 35,
        reads=replace(STOP_READS, sample_delay_ns=20_000_000, response_delay_ns=20_000_000))
    report = simulate_shared([item], profile=PROFILE)
    assert not report.blockers
    assert plans(report)[0]["plan"]["positions"][1]["tp"] == 0
    events = basket(report, item.path.signal_id).protection_events
    assert all(row.tp == 110. for row in events if row.kind in {"requested", "installed"})
    assert any(row.kind == "installed" for row in events)


def test_native_close_during_valuation_cannot_resurrect_position(monkeypatch):
    original = ProtectionBook.open

    def open_with_broker_protection(self, ticket, *args):
        original(self, ticket, *args)
        self.states[ticket].sl = 95.

    monkeypatch.setattr(ProtectionBook, "open", open_with_broker_protection)
    item = stop_spec([100.] + [90.] * 34, close_at=100,
        reads=replace(STOP_READS, sample_delay_ns=20_000_000, response_delay_ns=20_000_000))
    report = simulate_shared([item], profile=PROFILE)
    assert not report.blockers
    result = basket(report, item.path.signal_id)
    assert len(result.exits) == 1 and result.exits[0].reason == "initial_sl"
    assert result.exits[0].tick_index == 1
    assert not [row for row in result.protection_events if row.kind in {"requested", "installed"}]
    assert plans(report)[0]["plan"]["positions"][1]["sl"] == 95.
    assert plans(report)[0]["records"][-1]["source_ordinal"] > 1


@pytest.mark.parametrize("max_events", [3, 4])
def test_failed_stop_enqueue_or_later_dispatch_retains_plan_and_actual_phase(max_events):
    item = stop_spec([100.] * 35)
    item = replace(item, execution=replace(item.execution,
        client=replace(item.execution.client, max_events=max_events)))
    report = simulate_shared([item], profile=PROFILE)
    assert any("client_event_budget_exhausted" in reason for reason in report.blockers)
    first = plans(report)[0]
    assert first["completed"] and first["stop_available"]
    if max_events == 3:
        assert first["stop_application"]["status"] == "blocked"
        assert first["stop_application"]["blocker"] == "client_event_budget_exhausted"
    else:
        assert first["stop_application"]["status"] == "evaluated"
        assert first["stop_application"]["tickets"][0]["status"] == "queued"
    assert not first["stop_application"]["protection_installed"]


def test_report_identifies_stop_inputs_and_stays_unadmitted():
    item = stop_spec([100.] * 35)
    report = simulate_shared([item], profile=PROFILE)
    descriptor = report.management_profiles[0]["stop_component"]
    assert descriptor["valuation"] == "linear_contract_fx_unrounded_v1"
    assert descriptor["symbol_metadata"] == "declared_protection_profile"
    assert descriptor["loss_budget"] == 25.
    assert descriptor["reads"]["max_reads"] == 1024
    assert not descriptor["complete_strategy_admitted"]
    assert not descriptor["native_valuation_verified"]
    assert not report.full_live_parity_verified and not report.portfolio_admitted


def test_other_channel_contends_for_transport_without_joining_stop_basket():
    item = stop_spec([100.] * 40, close_at=30,
        reads=replace(STOP_READS, sample_delay_ns=20_000_000, response_delay_ns=20_000_000))
    other = spec("canal2_busy", [], tape=item.path,
        strategy=strategy(entry_mode="delay", entry_value=1.),
        execution=replace(item.execution, market=market(entry_acknowledgement_delay_ms=1750,
                                                       close_acknowledgement_delay_ms=0)))
    report = simulate_shared([item, other], profile=PROFILE)
    assert not report.blockers
    first = plans(report)[0]
    assert len(first["plan"]["positions"]) == 1
    assert any(row["started_ns"] - row["requested_ns"] >= 1_500_000_000 for row in first["records"])
    second = basket(report, other.path.signal_id)
    assert not [row for row in second.protection_events if row.kind == "requested"]
    assert all(row["signal_id"] == item.path.signal_id for row in plans(report))
    assert all(row["status"] == "released" for row in report.transport["rows"])


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("legs", [2, 3])
def test_zero_delay_multileg_stop_uses_explicit_causal_round_budget(direction, legs):
    quotes = [100., 98.] + [96.] * 43
    if direction == "SELL":
        quotes = [200 - value for value in quotes]
    item = stop_spec(quotes, direction=direction, close_at=35,
        reads=ReadCycleProfile(max_reads=1024),
        rules=strategy(leg_count=legs, volume_weights=(.01, .04, .04)[:legs],
                       entry_ladder_mode="adverse", entry_ladder_step=1.5))
    report = simulate_shared([item], profile=replace(PROFILE, max_rounds_per_quote=1024))
    assert not report.blockers
    assert len(basket(report, item.path.signal_id).entries) == legs
    assert any(len(row["plan"]["positions"]) == legs for row in plans(report) if row["completed"])
