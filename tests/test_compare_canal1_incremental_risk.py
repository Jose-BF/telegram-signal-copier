from tools.compare_canal1_incremental_risk import compare_rows


def test_comparison_keeps_same_net_different_drawdown_and_unmatched_scopes():
    model = {
        "start_utc": "2026-09-17T13:45:00+00:00",
        "cutoff_utc": "2026-09-17T14:55:00+00:00",
        "clock_offset_seconds_hypothesis": 10_800,
        "basket_rows": [
            {"channel": "canal2", "signal_id": "canal2_3102",
             "entries": [{"ticket": "a", "opened_at": "2026-09-17T13:46:00+00:00",
                          "volume": 0.15}],
             "exits": [{"ticket": "a", "closed_at": "2026-09-17T14:49:12+00:00"}],
             "blockers": []},
            {"channel": "canal1", "signal_id": "canal1_22733",
             "entries": [], "exits": [], "blockers": []},
        ],
        "basket_risk": {
            "canal2_3102": {"channel": "canal2", "blockers": [], "final_total_minor_model": 2002,
                            "max_drawdown_minor_model": 25655,
                            "minimum_from_origin_minor_model": -25000,
                            "maximum_from_origin_minor_model": 2002,
                            "max_gross_volume_model": "0.15",
                            "path_sha256": "a" * 64},
            "canal1_22733": {"channel": "canal1", "blockers": [], "final_total_minor_model": 0,
                             "max_drawdown_minor_model": 0,
                             "minimum_from_origin_minor_model": 0,
                             "maximum_from_origin_minor_model": 0,
                             "max_gross_volume_model": "0",
                             "path_sha256": "b" * 64},
        },
    }
    native = {"baskets": [
        {"channel": "canal2", "signal_id": "canal2_3102",
         "status": "retrospective_complete", "strict_causal_fx_path_complete": True,
         "position_count": 1,
         "path": {"metrics": {"final_at": "2026-09-17T14:43:13+00:00",
                              "final_net": "20.02", "max_drawdown": "178.72",
                              "minimum_from_origin": "-175.00",
                              "maximum_from_origin": "20.02",
                              "max_gross_volume": "0.15"}}},
        {"channel": "canal1", "signal_id": "canal1_missing",
         "status": "retrospective_complete", "strict_causal_fx_path_complete": True,
         "position_count": 1,
         "path": {"metrics": {"final_at": "2026-09-17T14:20:00+00:00"}}},
        {"channel": "canal1", "signal_id": "canal1_later",
         "status": "retrospective_complete", "strict_causal_fx_path_complete": True,
         "position_count": 1,
         "path": {"metrics": {"final_at": "2026-09-17T15:00:00+00:00"}}},
    ]}

    positions = [{"signal_id": "canal2_3102", "position_id": 1,
                  "first_native_msc": 1_000_000, "last_native_msc": 2_000_000,
                  "entry_volume": "0.15"}]
    rows, counts = compare_rows(model, native, positions)
    assert counts == {"matched": 1, "model_no_entry": 1,
                      "model_entry_without_native": 0, "native_without_model": 1,
                      "blocked_comparison": 0, "native_time_unknown": 0,
                      "different_leg_volume_profile": 0,
                      "concurrent_exposure_mismatch": 0,
                      "path_discrepant": 1}
    match = next(row for row in rows if row["signal_id"] == "canal2_3102")
    assert match["status"] == "matched_path_discrepant"
    assert match["leg_volume_profile_match"] is True
    assert match["max_concurrent_volume_match"] is True
    assert match["net_delta_minor"] == 0
    assert match["drawdown_delta_minor"] == 7783
    assert match["exit_delta_ms"] == 359_000
    assert match["trajectory_parity_verified"] is False
    assert next(row for row in rows if row["signal_id"] == "canal1_22733")["status"] == "model_no_entry"
    assert next(row for row in rows if row["signal_id"] == "canal1_missing")["status"] == "native_without_model"
    assert "canal1_later" not in {row["signal_id"] for row in rows}
