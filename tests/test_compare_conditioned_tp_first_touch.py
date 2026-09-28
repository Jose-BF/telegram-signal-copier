import pytest

from tools.compare_conditioned_tp_first_touch import compare


def fixtures():
    model = {"contract": "conditioned_fx_interval_path_sensitivity_v1",
             "full_live_parity_verified": False, "cases": []}
    population = {"contract": "native_gold_tp_terminal_touch_population_v1",
                  "full_live_parity_verified": False, "rows": []}
    for number in range(4):
        signal = f"canal2_{number}"
        model["cases"].append({"signal_id": signal,
                               "status": "retrospective_path_compared",
                               "exit_timing": [{"slot": 1,
                                                "native_exit_at": "2026-09-15T12:00:01+00:00",
                                                "model_exit_at": "2026-09-15T12:00:00+00:00",
                                                "native_exit_price": "101",
                                                "model_exit_price": "101",
                                                "model_mechanism": "per_leg_target",
                                                "model_minus_native_ms": -1000}]})
        population["rows"].append({"signal_id": signal, "position_id": number,
                                   "native_entry_utc": "2026-09-15T11:59:00+00:00",
                                   "native_exit_utc": "2026-09-15T12:00:01+00:00",
                                   "native_exit_reason": 5, "status": "retained_tp_side_touch",
                                   "target": "101", "receipt_status": "missing_client_tp_receipt_trace",
                                   "terminal_corridor": {
                                       "first_qualifying_side_update_utc": "2026-09-15T12:00:00+00:00",
                                       "first_touch_to_native_exit_ms": 1000,
                                       "qualifying_side_update_count": 2}})
    return model, population


def test_comparison_binds_all_four_logical_exits_to_first_side_touch():
    model, population = fixtures()
    rows = compare(model, population)
    assert len(rows) == 4
    assert all(row["status"] == "model_at_first_terminal_touch" for row in rows)
    assert all(row["first_touch_to_native_exit_ms"] == 1000 for row in rows)


def test_comparison_rejects_native_target_mismatch():
    model, population = fixtures()
    population["rows"][0]["target"] = "102"
    with pytest.raises(ValueError, match="identity differs"):
        compare(model, population)
