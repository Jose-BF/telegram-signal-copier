from dataclasses import replace
from types import SimpleNamespace

import pytest

from basket_observation import drive_reads
from basket_stop import basket_loss_stop_reads, basket_stop_plan_reads
from mt5_read_protocol import ReadOperation
from research.management_observation import BasketReadCycle, BasketStopReadCycle, BasketStopPlanReadCycle, ReadCycleProfile, ReadValue
from research.shared_transport import TransportJob, TransportSession
from tests.test_management_observation import drain


def flow(direction="BUY", positions=None):
    return basket_loss_stop_reads(direction, positions or [
        {"entry": 4200., "volume": .01},
        {"entry": 4196. if direction == "BUY" else 4204., "volume": .04}],
        25., "XAUUSD", default_symbol="XAUUSD")


def stop_cycle(direction="BUY", *, profile=None, sample=None, session=None):
    return BasketStopReadCycle(flow(direction), session or TransportSession(cutoff_ns=10_000),
        channel="canal1", signal_id="canal1_stop", cycle_id=0,
        profile=profile or ReadCycleProfile(sample_delay_ns=1, response_delay_ns=2, max_reads=1024),
        sample=sample or valuation)


def valuation(request, now, rate=.9):
    p = request.params
    if request.operation is ReadOperation.SYMBOL:
        data = {"point": .01, "digits": 2}
    else:
        data = (1 if p["action"] == 0 else -1) * (p["price_close"] - p["price_open"]) * p["volume"] * 100 * rate
    return ReadValue(now, {"data": data})


@pytest.mark.parametrize("direction, expected", [("BUY", 4191.25), ("SELL", 4208.75)])
def test_full_stop_calculation_is_sequential_and_delivered_only_after_final_response(direction, expected):
    item = stop_cycle(direction)
    observed = []
    drain(item, on_time=lambda now, cycle: observed.append((now, cycle.stop_price)))
    assert item.completed and item.stop_price == expected
    assert not item.report()["blockers"]
    assert len(item.records) == 173
    assert all(value is None for _, value in observed)
    for prior, current in zip(item.records, item.records[1:]):
        assert current["requested_ns"] == prior["delivery_ns"]
    assert item.records[-1]["delivery_ns"] == 519
    assert item.report()["stop_available"] and not item.report()["protection_installed"]


def test_contention_changes_valuation_sampling_clock_and_not_only_delivery():
    session = TransportSession(cutoff_ns=10_000)
    session.submit([TransportJob("entry", "canal2", "canal2_busy", "OPEN_MARKET", 0, 0, 900, 30)])
    samples = []
    def sample(request, now):
        samples.append(now)
        return valuation(request, now, .9 if now < 30 else 1.1)
    item = stop_cycle(session=session, sample=sample)
    drain(item)
    assert item.stop_price == 4192.26
    assert samples[0] == 31
    assert item.records[0]["started_ns"] == 30
    assert all(row["released_ns"] is not None for row in session.report()["rows"])


@pytest.mark.parametrize("cap, completed", [(172, False), (173, True)])
def test_exact_read_budget_or_one_short_never_returns_a_partial_level(cap, completed):
    item = stop_cycle(profile=ReadCycleProfile(max_reads=cap))
    drain(item)
    assert item.completed is completed
    assert (item.stop_price is not None) is completed
    if not completed:
        assert item.report()["blockers"] == ["read_cycle_budget_exhausted"]
        assert not item.report()["stop_available"]


@pytest.mark.parametrize("unknown_at", [1, 2, 10, 173])
def test_unknown_native_result_stops_without_using_last_trial_price(unknown_at):
    count = 0
    def sample(request, now):
        nonlocal count
        count += 1
        return ReadValue(now, {"data": None}) if count == unknown_at else valuation(request, now)
    item = stop_cycle(sample=sample)
    drain(item)
    assert item.completed and item.stop_price is None
    assert count == unknown_at
    assert not item.report()["stop_available"]


@pytest.mark.parametrize("payload", [True, "0", [], {}, [1]])
def test_profit_payload_must_be_a_scalar_not_truthy_or_record(payload):
    item = stop_cycle(sample=lambda request, now: valuation(request, now)
        if request.operation is ReadOperation.SYMBOL else ReadValue(now, {"data": payload}))
    drain(item)
    assert not item.completed and item.stop_price is None
    assert item.report()["blockers"] == ["invalid_read_payload"]


def test_plain_basket_cycle_does_not_gain_stop_operations():
    item = BasketReadCycle(flow(), TransportSession(cutoff_ns=100), channel="canal1",
        signal_id="canal1_plain", cycle_id=0, profile=ReadCycleProfile(), sample=valuation)
    drain(item)
    assert item.report()["blockers"] == ["unsupported_basket_read"]


def test_nonpositive_trial_price_obeys_existing_protocol_boundary():
    requests = []
    def read(request):
        requests.append(request)
        return SimpleNamespace(point=.01, digits=2) if request.operation is ReadOperation.SYMBOL else 0.
    trial = basket_loss_stop_reads("BUY", [{"volume": .01, "entry": .5}], .2,
                                  default_symbol="XAUUSD")
    with pytest.raises(ValueError):
        drive_reads(trial, read)
    assert len(requests) == 2
    assert requests[-1].params["price_close"] == .5


def test_timeout_discards_committed_profit_and_drains_connection():
    session = TransportSession(cutoff_ns=10_000)
    session.submit([TransportJob("entry", "canal2", "canal2_busy", "OPEN_MARKET", 1, 1, 900, 3)])
    item = stop_cycle(profile=ReadCycleProfile(sample_delay_ns=1, response_delay_ns=2,
                                               timeout_ns=4, max_reads=1024), session=session)
    drain(item)
    assert item.report()["blockers"] == ["read_timeout"]
    assert item.stop_price is None and not item.pending
    assert item.records[-1]["disposition"] == "discarded"
    assert item.records[-1]["operation"] == "order_calc_profit"
    assert item.records[-1]["started_ns"] == 6
    assert item.records[-1]["delivery_ns"] == 9


def test_repeated_prices_are_revalued_and_final_correction_has_a_budget():
    calls = []
    def sample(request, now):
        calls.append(request)
        if request.operation is ReadOperation.SYMBOL:
            return valuation(request, now)
        # Normal bisection, then permanently adverse values during final checks.
        if len(calls) > 171:
            return ReadValue(now, {"data": -1000.})
        return valuation(request, now)
    item = stop_cycle(profile=ReadCycleProfile(max_reads=190), sample=sample)
    drain(item)
    assert not item.completed and item.stop_price is None
    assert item.report()["blockers"] == ["read_cycle_budget_exhausted"]
    prices = [row.params["price_close"] for row in calls if row.operation is ReadOperation.PROFIT]
    assert len(set(prices)) < len(prices)
    assert len(calls) == 190


def test_three_legs_use_explicit_larger_budget_and_preserve_zero_and_negative_profit():
    positions = [{"entry": 4200., "volume": .01}, {"entry": 4196., "volume": .04},
                 {"entry": 4192., "volume": .04}]
    item = stop_cycle(profile=ReadCycleProfile(max_reads=1024))
    item.flow = flow(positions=positions)
    drain(item, max_steps=5000)
    assert item.completed and item.stop_price is not None
    profits = [row["payload"]["data"] for row in item.records if row["operation"] == "order_calc_profit"]
    assert any(value < 0 for value in profits) and any(value > 0 for value in profits)
    assert ReadValue(0, {"data": 0.}).native_value(ReadOperation.PROFIT) == 0.
    assert len(item.records) > 250


def test_cancellation_discards_in_flight_profit_without_releasing_early():
    cancelled = False
    def cancel(now, item):
        nonlocal cancelled
        if not cancelled and item.request and item.request.operation is ReadOperation.PROFIT and item.started_ns is not None:
            item.cancel(item.session.now_ns)
            cancelled = True
    item = stop_cycle()
    drain(item, on_time=cancel)
    assert cancelled and item.report()["blockers"] == ["read_cycle_cancelled"]
    assert not item.pending and item.stop_price is None
    assert item.records[-1]["operation"] == "order_calc_profit"
    assert item.records[-1]["disposition"] == "discarded"
    assert item.records[-1]["delivery_ns"] == item.records[-1]["started_ns"] + 3


def plan_cycle(sample):
    return BasketStopPlanReadCycle(basket_stop_plan_reads("BUY", [1, 2], 25., default_symbol="XAUUSD"),
        TransportSession(cutoff_ns=10_000), channel="canal1", signal_id="canal1_plan", cycle_id=0,
        profile=ReadCycleProfile(sample_delay_ns=1, response_delay_ns=2, max_reads=1024), sample=sample)


def test_stop_plan_keeps_sampled_positions_during_later_valuation():
    positions = [{"ticket": 1, "symbol": "XAUUSD", "price_open": 4200., "volume": .01, "sl": 4100.},
                 {"ticket": 2, "symbol": "XAUUSD", "price_open": 4196., "volume": .04, "sl": 4100.},
                 {"ticket": 3, "symbol": "XAUUSD", "price_open": 4000., "volume": 1.}]
    def sample(request, now):
        if request.operation is ReadOperation.POSITIONS:
            return ReadValue(now, {"data": positions})
        positions[0]["volume"] = 5.
        positions[1]["sl"] = 4300.
        return valuation(request, now)
    item = plan_cycle(sample)
    drain(item, max_steps=5000)
    report = item.report()
    assert report["stop_available"] and not report["protection_installed"]
    assert report["plan"]["stop_price"] == 4191.25
    assert report["plan"]["positions"][1]["volume"] == .01
    assert report["plan"]["positions"][2]["sl"] == 4100.
    assert set(report["plan"]["positions"]) == {1, 2}
    assert [row["operation"] for row in item.records[:3]] == ["positions_get", "symbol_info", "symbol_info"]
    assert len(item.records) == 175


@pytest.mark.parametrize("positions", [None, []])
def test_unknown_positions_and_known_empty_plan_remain_distinct(positions):
    item = plan_cycle(lambda request, now: ReadValue(now, {"data": positions}))
    drain(item)
    assert item.completed and len(item.records) == 1
    plan = item.report()["plan"]
    assert plan["positions_complete"] is (positions is not None)
    assert plan["positions"] == (None if positions is None else {})
    assert plan["stop_price"] is None and not item.report()["stop_available"]


def test_missing_symbol_does_not_turn_default_spec_metadata_into_a_valued_stop():
    positions = [{"ticket": 1, "price_open": 4200., "volume": .01}]
    item = plan_cycle(lambda request, now: ReadValue(now, {"data": positions
        if request.operation is ReadOperation.POSITIONS else None}))
    drain(item)
    assert item.completed and not item.report()["stop_available"]
    assert item.report()["plan"]["positions"][1]["point"] == .01
    assert len(item.records) == 3
