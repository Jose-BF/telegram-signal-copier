from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from research.causal_comparison import SequenceEvent
from research.risk_trajectory import RiskQuote, RiskSpec, compare_risk, reconstruct_risk


BASE = datetime(2026, 9, 1, 9, tzinfo=timezone.utc)


def at(seconds):
    return BASE + timedelta(seconds=seconds)


def event(kind, seconds, volume="1", money="0", direction="BUY", slot=1):
    return SequenceEvent(slot, kind, at(seconds), direction, D("100"), D(volume), D(money), "market")


def quote(seconds, bid, ask=None, *, fx_age=0, fx_bid="1", fx_ask="1"):
    return RiskQuote(at(seconds), D(str(bid)), D(str(ask if ask is not None else bid)),
                     at(seconds - fx_age), D(fx_bid), D(fx_ask))


SPEC = RiskSpec("EUR", 2, D(100), "account_base_profit_quote", 5000, 5000)


def test_same_final_profit_does_not_hide_doubled_intra_trade_loss():
    tape = [quote(0, 100), quote(1, 99.2), quote(2, 100.1)]
    left = [event("entry", 0), event("exit", 2, money="10")]
    right = [event("entry", 0, volume="2"), event("exit", 2, volume="2", money="10")]
    report = compare_risk(left, right, tape, spec=SPEC)
    assert report["observed"]["metrics"]["final_net"] == report["simulated"]["metrics"]["final_net"] == D(10)
    assert report["observed"]["metrics"]["minimum_from_origin"] == D(-80)
    assert report["simulated"]["metrics"]["minimum_from_origin"] == D(-160)
    assert report["max_abs_total_difference"] == D(80)
    assert report["first_divergence"]["at"] == at(0)
    assert report["status"] == "mismatch"
    assert report["full_live_parity_verified"] is False


def test_drawdown_is_peak_to_trough_not_minimum_or_floating_only():
    events = [event("entry", 0), event("exit", 2, volume=".5", money="10"),
              event("exit", 4, volume=".5", money="5")]
    tape = [quote(0, 100), quote(1, 101), quote(2, 100.5), quote(3, 99), quote(4, 100)]
    result = reconstruct_risk(events, tape, spec=SPEC)
    assert result["samples"][3]["realized"] == D(10)
    assert result["samples"][3]["floating"] == D(-50)
    assert result["metrics"]["minimum_from_origin"] == D(-40)
    assert result["metrics"]["maximum_from_origin"] == D(100)
    assert result["metrics"]["max_drawdown"] == D(140)
    assert result["metrics"]["final_net"] == D(15)
    assert result["samples"][2]["long_volume"] == D(".5")


@pytest.mark.parametrize("direction,bid,ask,expected", [("BUY", 99, 100, "-101.01"), ("SELL", 99, 101, "-101.01"), ("BUY", 101, 102, "99.01"), ("SELL", 98, 99, "99.01")])
def test_executable_side_and_sign_dependent_fx(direction, bid, ask, expected):
    result = reconstruct_risk([event("entry", 0, direction=direction)],
                              [quote(0, bid, ask, fx_bid=".99", fx_ask="1.01")], spec=SPEC)
    assert result["samples"][0]["floating"] == D(expected)


def test_opening_cost_is_booked_immediately_and_not_spread_over_exits():
    events = [event("entry", 0, money="-2"), event("exit", 2, money="5")]
    result = reconstruct_risk(events, [quote(0, 100), quote(1, 99), quote(2, 100)], spec=SPEC)
    assert result["samples"][0]["total"] == D(-2)
    assert result["metrics"]["minimum_from_origin"] == D(-102)
    assert result["metrics"]["final_net"] == D(3)


def test_exit_between_quotes_is_included_using_prior_quote_and_common_grid():
    events = [event("entry", .5), event("exit", 1, money="7")]
    result = compare_risk(events, [], [quote(0, 100), quote(2, 90)], spec=SPEC)
    assert [row["at"] for row in result["observed"]["samples"]] == [at(0), at(.5), at(1), at(2)]
    assert result["observed"]["samples"][2]["total"] == D(7)
    assert result["observed"]["samples"][2]["open_count"] == 0


def test_stale_fx_retains_unknown_row_and_blocks_full_metrics():
    events = [event("entry", 0)]
    tape = [quote(0, 100), quote(1, 99, fx_age=10), quote(2, 100)]
    result = compare_risk(events, events, tape, spec=SPEC)
    assert result["status"] == "blocked"
    assert len(result["observed"]["samples"]) == 3
    assert result["observed"]["samples"][1]["total"] is None
    assert result["observed"]["metrics"] is None
    assert result["unknown_pairs"] == 1


def test_retrospective_fx_bracket_is_explicit_and_never_changes_causal_default():
    events = [event("entry", 0)]
    tape = [quote(0, 100), RiskQuote(at(10), D(99), D(99), at(0), D(".99"), D("1.01"),
                                     conversion_next_at=at(12))]
    causal = reconstruct_risk(events, tape, spec=RiskSpec("EUR", 2, D(100),
        "account_base_profit_quote", 5000, 15_000))
    assert causal["metrics"] is None
    assert "stale_conversion_quote" in causal["blockers"]
    historical = reconstruct_risk(events, tape, spec=RiskSpec("EUR", 2, D(100),
        "account_base_profit_quote", 5000, 15_000), retrospective_fx_interval_ms=12_000)
    assert historical["metrics"]["minimum_from_origin"] == D("-101.01")
    assert historical["retrospective_fx_bracketed_samples"] == 1
    assert historical["fx_coverage_mode"] == "retrospective_bracketed"
    assert historical["full_live_parity_verified"] is False
    too_short = reconstruct_risk(events, tape, spec=RiskSpec("EUR", 2, D(100),
        "account_base_profit_quote", 5000, 15_000), retrospective_fx_interval_ms=11_000)
    assert too_short["metrics"] is None


def test_missing_tape_cannot_look_like_no_trade_zero_risk():
    result = reconstruct_risk([], [], spec=SPEC)
    assert result["metrics"] is None
    assert "missing_market_quotes" in result["blockers"]


@pytest.mark.parametrize("events", [
    [event("entry", 0), event("entry", 1)],
    [event("exit", 1)],
    [event("entry", 0), event("exit", 1, volume="2")],
    [event("entry", 0), event("exit", 1, direction="SELL")],
])
def test_invalid_position_sequence_is_not_repaired_or_ignored(events):
    with pytest.raises(ValueError):
        reconstruct_risk(events, [quote(0, 100), quote(2, 100)], spec=SPEC)


def test_same_timestamp_quotes_retain_the_whole_price_excursion():
    result = reconstruct_risk([event("entry", 0)],
                              [quote(0, 100), quote(1, 90), quote(1, 110)], spec=SPEC)
    assert len(result["samples"]) == 3
    assert result["metrics"]["minimum_from_origin"] == D(-1000)
    assert result["metrics"]["maximum_from_origin"] == D(1000)


def test_missing_market_interval_is_reported_not_used_as_complete_extrema():
    result = reconstruct_risk([event("entry", 0)], [quote(0, 100), quote(20, 90)], spec=SPEC)
    assert result["metrics"] is None
    assert result["known_sample_metrics"]["minimum_from_origin"] == D(-1000)
    assert "market_quote_gap" in result["blockers"]


def test_quotes_cannot_be_sorted_into_apparent_causal_validity():
    with pytest.raises(ValueError):
        reconstruct_risk([], [quote(1, 100), quote(0, 100)], spec=SPEC)


def test_offsetting_leg_errors_cannot_pass_on_equal_basket_money():
    from dataclasses import replace

    left = [event("entry", 0), replace(event("entry", 0, slot=2), price=D(102))]
    right = [replace(left[0], price=D(102)), replace(left[1], price=D(100))]
    result = compare_risk(left, right, [quote(0, 100)], spec=SPEC)
    assert result["max_abs_total_difference"] == 0
    assert result["status"] == "mismatch"
    assert result["first_divergence"]["differences"] == ["positions"]


@pytest.mark.parametrize("orientation,currency,expected", [
    ("identity", "USD", "100.00"), ("profit_base_account_quote", "EUR", "99.00"),
])
def test_other_supported_conversion_orientations_use_existing_money_rule(orientation, currency, expected):
    from dataclasses import replace

    spec = replace(SPEC, orientation=orientation, currency=currency)
    result = reconstruct_risk([event("entry", 0)], [quote(0, 101, fx_bid=".99", fx_ask="1.01")], spec=spec)
    assert result["metrics"]["final_net"] == D(expected)


def test_future_conversion_is_unknown_not_an_exact_zero_difference():
    result = compare_risk([event("entry", 0)], [event("entry", 0)], [quote(0, 99, fx_age=-1)], spec=SPEC)
    assert result["status"] == "blocked"
    assert result["first_unknown"] == {"at": at(0), "ordinal": 0}
    assert result["max_abs_total_difference"] is None


def test_precision_and_compute_budget_are_enforced(monkeypatch):
    import research.risk_trajectory as risk

    with pytest.raises(ValueError, match="precision"):
        reconstruct_risk([event("entry", 0, money=".001")], [quote(0, 100)], spec=SPEC)
    monkeypatch.setattr(risk, "MAX_POSITION_MARKS", 1)
    with pytest.raises(ValueError, match="budget"):
        reconstruct_risk([event("entry", 0)], [quote(0, 100), quote(1, 100)], spec=SPEC)


def test_inserted_event_quotes_do_not_hide_a_market_tape_gap():
    middle = RiskQuote(at(10), D(100), D(100), at(10), D(1), D(1), market_at=at(0))
    result = reconstruct_risk([event("entry", 0)], [quote(0, 100), middle, quote(20, 100)], spec=SPEC)
    assert "market_quote_gap" in result["blockers"]
    assert "stale_market_quote" in result["samples"][1]["blockers"]
