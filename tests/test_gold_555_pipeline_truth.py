from __future__ import annotations

from research.gold_iterative import pipeline_truth
from research.gold_iterative.pipeline_truth import build_pipeline_truth_report


def _management(*, second_entries: int = 2) -> dict:
    return {
        "management_replay_allowed": True,
        "actual_mt5": {"signals": 2, "entries": 1 + second_entries, "net_eur": "5.00"},
        "live_logic_mirror": {
            "signals": 2,
            "exact_signals": 2,
            "net_eur": "5.00",
        },
        "parity": {"status": "exact", "net_delta_eur": "0.00", "blockers": []},
        "rows": [
            {
                "signal_id": "canal2_1",
                "status": "exact",
                "actual_mt5_eur": "1.00",
                "live_logic_mirror_eur": "1.00",
                "actual_entry_count": 1,
            },
            {
                "signal_id": "canal2_2",
                "status": "exact",
                "actual_mt5_eur": "4.00",
                "live_logic_mirror_eur": "4.00",
                "actual_entry_count": second_entries,
            },
        ],
    }


def _entry(
    *,
    broker_status: str = "observed_variance",
    trigger_exact: bool = True,
) -> dict:
    return {
        "prospective_entry_outcome_allowed": True,
        "prospective_entry_trigger_allowed": trigger_exact,
        "logged_sample_replay": {"status": "exact"},
        "full_tick_replay": {"status": "behavioral_exact", "outcome_matches": 2},
        "broker_execution": {"status": broker_status},
        "prospective_fill_model_allowed": broker_status == "exact",
    }


def _prospective(*, second_entries: int = 1, second_money: str = "1.50") -> dict:
    return {
        "variants": {
            "deterministic_flat_cancel": {
                "status": "certified",
                "signals": 2,
                "net_eur": str(1 + float(second_money)),
                "rows": [
                    {
                        "signal_id": "canal2_1",
                        "net_eur": "1.00",
                        "entry_count": 1,
                        "first_entry_at": "2026-09-01T09:00:00+00:00",
                        "first_entry_price": "100.00",
                        "first_exit_at": "2026-09-01T09:00:10+00:00",
                    },
                    {
                        "signal_id": "canal2_2",
                        "net_eur": second_money,
                        "entry_count": second_entries,
                        "first_entry_at": "2026-09-01T10:00:00+00:00",
                        "first_entry_price": "100.00",
                        "first_exit_at": "2026-09-01T10:00:10+00:00",
                    },
                ],
            }
        }
    }


def _ledger(*, second_open: str = "2026-09-01T10:00:20+00:00"):
    return (
        {
            "sig_id": "canal2_1",
            "positions": [{
                "open_price": 100.00,
                "open_dt_utc": "2026-09-01T09:00:00+00:00",
                "close_dt_utc": "2026-09-01T09:00:10+00:00",
            }],
        },
        {
            "sig_id": "canal2_2",
            "positions": [
                {
                    "open_price": 100.05,
                    "open_dt_utc": "2026-09-01T10:00:00+00:00",
                    "close_dt_utc": "2026-09-01T10:00:15+00:00",
                },
                {
                    "open_price": 98.50,
                    "open_dt_utc": second_open,
                    "close_dt_utc": "2026-09-01T10:01:00+00:00",
                },
            ],
        },
    )


def test_truth_report_never_presents_actual_and_prospective_as_a_range() -> None:
    report = build_pipeline_truth_report(
        management_report=_management(),
        entry_watch_report=_entry(),
        prospective_report=_prospective(),
        variant_name="deterministic_flat_cancel",
        ledger_rows=_ledger(),
        event_rows=(),
    )

    assert report["observed_mt5"]["net_eur"] == "5.00"
    assert report["retrospective_management_replay"]["net_eur"] == "5.00"
    assert report["prospective_simulation"]["net_eur"] == "2.50"
    assert report["actual_vs_prospective"]["net_delta_eur"] == "-2.50"
    assert report["actual_vs_prospective"]["exact_signals"] == 1
    assert report["rows"][1]["difference_cause"] == (
        "post_flat_reentry_before_finalization"
    )
    assert report["end_to_end_historical_extension_allowed"] is False
    assert "range" not in report


def test_rejected_first_order_is_not_mislabeled_as_strategy_behavior() -> None:
    report = build_pipeline_truth_report(
        management_report=_management(),
        entry_watch_report=_entry(),
        prospective_report=_prospective(),
        variant_name="deterministic_flat_cancel",
        ledger_rows=_ledger(second_open="2026-09-01T10:00:05+00:00"),
        event_rows=({
            "sig": "canal2_2",
            "ev": "market_fill_failed",
            "strategy_id": "gold_now_555_v1",
        },),
    )

    assert report["rows"][1]["difference_cause"] == "broker_rejection_retry"
    assert report["gates"]["deterministic_terminal_lifecycle"] == "pass"
    assert report["gates"]["entry_trigger"] == "pass"
    assert report["gates"]["broker_fill_model"] == "fail"


def test_every_layer_must_pass_before_historical_extension_is_allowed() -> None:
    report = build_pipeline_truth_report(
        management_report=_management(second_entries=1),
        entry_watch_report=_entry(broker_status="exact"),
        prospective_report=_prospective(second_entries=1, second_money="4.00"),
        variant_name="deterministic_flat_cancel",
        ledger_rows=_ledger(),
        event_rows=(),
    )

    assert report["actual_vs_prospective"]["status"] == "exact"
    assert report["end_to_end_historical_extension_allowed"] is True


def test_entry_outcome_match_cannot_hide_a_trigger_tick_mismatch() -> None:
    report = build_pipeline_truth_report(
        management_report=_management(second_entries=1),
        entry_watch_report=_entry(broker_status="exact", trigger_exact=False),
        prospective_report=_prospective(second_entries=1, second_money="4.00"),
        variant_name="deterministic_flat_cancel",
        ledger_rows=_ledger(),
        event_rows=(),
    )

    assert report["gates"]["entry_outcome"] == "pass"
    assert report["gates"]["entry_trigger"] == "fail"
    assert report["end_to_end_historical_extension_allowed"] is False


def test_truth_report_sorts_numeric_and_non_numeric_signal_ids_safely() -> None:
    assert sorted(
        ("canal2", "canal2_10", "canal2_2"),
        key=pipeline_truth._signal_sort_key,
    ) == ["canal2_2", "canal2_10", "canal2"]
