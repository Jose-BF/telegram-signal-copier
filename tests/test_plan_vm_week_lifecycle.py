import pytest

from tools.plan_vm_week_lifecycle import build_plan


def fixture_sources():
    base = 1789732800000  # 2026-09-18 12:00 UTC.
    calls = {"rows": []}
    baskets = {"baskets": [], "positions": []}
    native = {"deals": []}
    anchor = {"independent_clock_evidence": {"days": {
        "2026-09-18": {"status": "direct_anchor_available"}}}}
    for index in range(40):
        signal = f"canal2_{index}"
        calls["rows"].append({"signal_id": signal, "receipt_utc": "2026-09-18T12:00:00+00:00",
                              "direct_clock_anchor": True})
        baskets["baskets"].append({"signal_id": signal, "channel": "canal2"})
        baskets["positions"].append({"signal_id": signal, "deal_tickets": [index * 2 + 1, index * 2 + 2]})
        native["deals"].extend([
            {"ticket": index * 2 + 1, "time_msc": base + 10_800_000 + 60_000,
             "entry": 0, "symbol": "XAUUSD"},
            {"ticket": index * 2 + 2, "time_msc": base + 10_800_000 + (11 if index == 0 else 2) * 60_000,
             "entry": 1, "symbol": "XAUUSD"},
        ])
    return calls, baskets, native, anchor


def test_week_plan_keeps_full_denominator_and_segments_long_basket():
    result = build_plan(*fixture_sources())
    assert result["native_basket_count"] == 40
    assert result["position_count"] == 40
    assert result["deal_count"] == 80
    assert result["total_segments"] == 42
    assert result["clock_statuses"] == {"direct_for_all_native_days": 40}
    assert len(result["rows"][0]["segments"]) == 3
    assert all(row["entry_deal_count"] == row["exit_deal_count"] == 1 for row in result["rows"])


def test_week_plan_rejects_denominator_or_clock_contradiction():
    calls, baskets, native, anchor = fixture_sources()
    calls["rows"].pop()
    with pytest.raises(ValueError, match="denominator"):
        build_plan(calls, baskets, native, anchor)
    calls, baskets, native, anchor = fixture_sources()
    calls["rows"][0]["direct_clock_anchor"] = False
    with pytest.raises(ValueError, match="clock anchor disagree"):
        build_plan(calls, baskets, native, anchor)
