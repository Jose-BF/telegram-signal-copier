import numpy as np
import pytest

from tools.audit_native_position_profit_interval import direct_same_day_offset, match_snapshot


POSITION = {"position_id": 101, "signal_id": "canal2_1", "direction": "BUY",
            "entry_msc": 11_000, "exit_msc": 18_000,
            "entry_price": "100.0", "volume": "0.04"}
SNAPSHOT = {"sig": "canal2_1", "event_id": "snapshot", "ticket": 101,
            "ts": "1970-01-01T00:00:05+00:00", "profit": 0.04, "volume": 0.04}
MARKET = (np.array([12_000, 14_000, 15_000, 16_000]),
          np.array([100.01, 100.01, 100.02, 100.03]),
          np.array([100.03, 100.03, 100.04, 100.05]))
FX = (np.array([11_000, 14_500]), np.array([1.0, 1.0]), np.array([1.0, 1.0]))


def test_native_profit_matches_only_prior_causal_quotes():
    row = match_snapshot(SNAPSHOT, POSITION, MARKET, FX,
                         offset_seconds=11, contract_size=100, max_fx_age_ms=5000)
    assert row["status"] == "native_profit_matches_prior_causal_quote"
    assert row["native_profit_eur"] == "0.04"
    assert row["latest_matching_quote_age_ms"] == 2000
    assert row["candidate_quote_count"] == 4


def test_native_profit_preserves_missing_and_nonmatching_rows():
    missing = match_snapshot({**SNAPSHOT, "profit": None}, POSITION, MARKET, FX,
                             offset_seconds=11, contract_size=100, max_fx_age_ms=5000)
    assert missing["status"] == "blocked_missing_native_profit"
    mismatch = match_snapshot({**SNAPSHOT, "profit": 1.0}, POSITION, MARKET, FX,
                              offset_seconds=11, contract_size=100, max_fx_age_ms=5000)
    assert mismatch["status"] == "native_profit_not_reproduced_in_prior_window"
    assert mismatch["nearest_abs_difference_eur"] == "0.88"


def test_native_profit_rejects_mixed_ticket_or_volume():
    with pytest.raises(ValueError, match="identity"):
        match_snapshot({**SNAPSHOT, "ticket": 102}, POSITION, MARKET, FX,
                       offset_seconds=11, contract_size=100, max_fx_age_ms=5000)
    with pytest.raises(ValueError, match="volume"):
        match_snapshot({**SNAPSHOT, "volume": 0.05}, POSITION, MARKET, FX,
                       offset_seconds=11, contract_size=100, max_fx_age_ms=5000)


def test_native_profit_sell_uses_ask_and_sign_specific_fx_side():
    position = {**POSITION, "direction": "SELL"}
    market = (np.array([15_000]), np.array([99.97]), np.array([99.98]))
    conversion = (np.array([14_000]), np.array([1.10]), np.array([1.20]))
    positive = match_snapshot({**SNAPSHOT, "profit": 0.07}, position,
                              market, conversion, offset_seconds=11,
                              contract_size=100, max_fx_age_ms=5000)
    assert positive["status"] == "native_profit_matches_prior_causal_quote"
    assert positive["candidate_value_min_eur"] == "0.07"
    loss_market = (np.array([15_000]), np.array([100.02]), np.array([100.03]))
    negative = match_snapshot({**SNAPSHOT, "profit": -0.11}, position,
                              loss_market, conversion, offset_seconds=11,
                              contract_size=100, max_fx_age_ms=5000)
    assert negative["status"] == "native_profit_matches_prior_causal_quote"
    assert negative["candidate_value_min_eur"] == "-0.11"


def test_native_profit_blocks_stale_fx_instead_of_interpolating():
    stale = (np.array([1_000]), np.array([1.0]), np.array([1.0]))
    row = match_snapshot(SNAPSHOT, POSITION, MARKET, stale,
                         offset_seconds=11, contract_size=100, max_fx_age_ms=5000)
    assert row["status"] == "blocked_no_causal_quote_in_prior_window"


def test_native_profit_requires_direct_same_day_broker_clock():
    days = {"2026-09-18": {"status": "direct_anchor_available", "offset_seconds": 10_800}}
    assert direct_same_day_offset("2026-09-18T12:00:00+00:00", days) == 10_800
    with pytest.raises(ValueError, match="crosses"):
        direct_same_day_offset("2026-09-18T22:00:00+00:00", days)
    days["2026-09-18"]["status"] = "no_direct_anchor"
    with pytest.raises(ValueError, match="direct clock"):
        direct_same_day_offset("2026-09-18T12:00:00+00:00", days)


def test_native_snapshot_mark_and_profit_must_match_same_prior_quote():
    detailed = {**SNAPSHOT, "position_exists": True, "symbol": "XAUUSD", "magic": 555,
                "position_type": 0, "price_open": 100.0, "price_current": 100.01}
    row = match_snapshot(detailed, POSITION, MARKET, FX, offset_seconds=11,
                         contract_size=100, max_fx_age_ms=5000, symbol="XAUUSD")
    assert row["native_mark_status"] == "native_mark_profit_matches_same_prior_quote"
    assert row["native_mark_quote_count"] == 2
    assert row["native_mark_profit_match_count"] == 2
    assert row["native_mark_latest_matching_quote_age_ms"] == 2000
    wrong_mark = match_snapshot({**detailed, "price_current": 100.03}, POSITION,
                                MARKET, FX, offset_seconds=11,
                                contract_size=100, max_fx_age_ms=5000, symbol="XAUUSD")
    assert wrong_mark["status"] == "native_profit_matches_prior_causal_quote"
    assert wrong_mark["native_mark_status"] == "native_mark_profit_not_reproduced"


def test_native_snapshot_details_reject_wrong_entry_direction_or_partial_projection():
    detailed = {**SNAPSHOT, "position_exists": True, "symbol": "XAUUSD", "magic": 555,
                "position_type": 0, "price_open": 100.0, "price_current": 100.01}
    for changes in ({"price_open": 100.02}, {"position_type": 1}, {"symbol": "EURUSD"}):
        with pytest.raises(ValueError, match="native position detail"):
            match_snapshot({**detailed, **changes}, POSITION, MARKET, FX,
                           offset_seconds=11, contract_size=100, max_fx_age_ms=5000,
                           symbol="XAUUSD")
    partial = {**detailed}
    del partial["price_current"]
    with pytest.raises(ValueError, match="native position detail"):
        match_snapshot(partial, POSITION, MARKET, FX, offset_seconds=11,
                       contract_size=100, max_fx_age_ms=5000, symbol="XAUUSD")


def test_native_mark_without_fresh_fx_is_blocked_not_a_mismatch():
    detailed = {**SNAPSHOT, "position_exists": True, "symbol": "XAUUSD", "magic": 555,
                "position_type": 0, "price_open": 100.0, "price_current": 100.01}
    stale = (np.array([1_000]), np.array([1.0]), np.array([1.0]))
    row = match_snapshot(detailed, POSITION, MARKET, stale, offset_seconds=11,
                         contract_size=100, max_fx_age_ms=5000, symbol="XAUUSD")
    assert row["status"] == "blocked_no_causal_quote_in_prior_window"
    assert row["native_mark_status"] == "blocked_no_causal_quote_in_prior_window"
