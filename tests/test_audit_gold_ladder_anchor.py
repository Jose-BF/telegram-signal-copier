import pytest

from tools.audit_gold_ladder_anchor import analyze_ladder, coverage_summary


def comparison(first_native="4381.79", first_shadow="4381.35"):
    return {"signal_id": "canal2_3148", "channel": "canal2",
            "control_candidate": "gold_now_555_v1",
            "entry_pairing": {"status": "verified_journal_entry_binding",
                              "full_entry_identity_verified": True,
                              "pairs": [
                                  {"leg_index": 0, "native_position_id": 101, "volume": "0.04",
                                   "native_entry_price": first_native,
                                   "shadow_entry_price": first_shadow,
                                   "native_minus_shadow_ms": 1375},
                                  {"leg_index": 1, "native_position_id": 102, "volume": "0.03",
                                   "native_entry_price": "4380.17",
                                   "shadow_entry_price": "4379.84",
                                   "native_minus_shadow_ms": -1060},
                                  {"leg_index": 2, "native_position_id": 103, "volume": "0.03",
                                   "native_entry_price": "4378.75",
                                   "shadow_entry_price": "4378.18",
                                   "native_minus_shadow_ms": -13140}]}}


def events():
    rows = []
    for index, (position, lot, requested, filled) in enumerate((
            (101, 0.04, 4381.35, 4381.79),
            (102, 0.03, 4380.25, 4380.17),
            (103, 0.03, 4378.74, 4378.75))):
        action_id = f"action_{index}"
        rows.extend((
            {"ev": "mt5_order_requested", "action_id": action_id,
             "ts": f"2026-09-18T11:05:{41 + index:02d}+00:00", "order_kind": "market",
             "direction": "BUY", "lot": lot, "requested_price": requested},
            {"ev": "mt5_order_result", "action_id": action_id,
             "order": position, "retcode": 10009, "direction": "BUY",
             "volume": lot, "price": filled}))
    return rows


def test_anchor_difference_separates_later_gold_triggers():
    report = analyze_ladder(comparison(), events())
    assert report["first_fill_anchor_delta"] == "0.44"
    assert [row["classification"] for row in report["rows"]] == [
        "first_fill_anchor", "anchor_separates_triggers", "anchor_separates_triggers"]
    assert [(row["native_trigger"], row["shadow_trigger_at_same_quote"])
            for row in report["rows"][1:]] == [
                ("4380.29", "4379.85"), ("4378.79", "4378.35")]


def test_same_first_fill_is_negative_control_for_anchor_separation():
    report = analyze_ladder(comparison("4381.79", "4381.79"), events())
    assert report["first_fill_anchor_delta"] == "0.00"
    assert all(row["classification"] == "both_triggers_crossed_at_native_quote"
               for row in report["rows"][1:])


def test_unverified_or_contradictory_entry_is_rejected():
    unverified = comparison()
    unverified["entry_pairing"]["status"] = "diagnostic_ordinal_pairing"
    with pytest.raises(ValueError, match="verified Gold"):
        analyze_ladder(unverified, events())
    contradictory = events()
    contradictory[3]["price"] = 4380.18
    with pytest.raises(ValueError, match="contradict verified fill"):
        analyze_ladder(comparison(), contradictory)
    mixed = events()
    mixed[2]["direction"] = "SELL"
    with pytest.raises(ValueError, match="direction mismatch"):
        analyze_ladder(comparison(), mixed)


def test_native_quote_must_reach_its_own_level():
    rows = events()
    rows[4]["requested_price"] = 4378.80
    with pytest.raises(ValueError, match="does not cross"):
        analyze_ladder(comparison(), rows)


def test_coverage_keeps_unverified_and_uncompared_gold_baskets_visible():
    verified = comparison()
    unverified = comparison()
    unverified["signal_id"] = "canal2_2"
    unverified["entry_pairing"]["status"] = "diagnostic_ordinal_pairing"
    report = {"coverage": [
        {"signal_id": signal, "native_basket_in_extract": True}
        for signal in ("canal2_3148", "canal2_2", "canal2_3")],
        "comparisons": [verified, unverified]}
    result = coverage_summary(report, ["canal2_3148"])
    assert result["native_gold_basket_count"] == 3
    assert result["gold_analyzed_count"] == 1
    assert result["native_gold_without_comparison"] == ["canal2_3"]
    assert result["gold_comparison_without_verified_entry_binding"] == [
        {"signal_id": "canal2_2", "status": "diagnostic_ordinal_pairing"}]
    with pytest.raises(ValueError, match="all identity-verified"):
        coverage_summary(report, [])
