from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from research.gold_iterative import pipeline_truth
from research.gold_iterative.pipeline_truth import build_pipeline_truth_report


def _management(*, second_entries: int = 2, second_open="2026-09-01T10:00:20+00:00") -> dict:
    return {
        "schema_version": 4,
        "comparison_contract": "ledger_bound_entry_and_exit_deals_v4",
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
                "engine_agreement": True,
                "ticket_rows": _ticket_rows(101, 1, "1.00"),
                "mirror_exit_rows": _exit_rows(101, 1, "1.00"),
            },
            {
                "signal_id": "canal2_2",
                "status": "exact",
                "actual_mt5_eur": "4.00",
                "live_logic_mirror_eur": "4.00",
                "actual_entry_count": second_entries,
                "engine_agreement": True,
                "ticket_rows": _ticket_rows(201, second_entries, "4.00", second_open=second_open),
                "mirror_exit_rows": _exit_rows(201, second_entries, "4.00", second_open=second_open),
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


def _ticket_rows(first_ticket: int, count: int, total: str, *, second_open="2026-09-01T10:00:20+00:00"):
    amount = Decimal(total) / count
    facts = {
        101: ("100.00", "2026-09-01T09:00:00+00:00"),
        201: ("100.05", "2026-09-01T10:00:00+00:00"),
        202: ("98.50", second_open),
    }
    return [{
        "ticket": str(first_ticket + index), "status": "exact", "blockers": [],
        "actual_mt5_eur": str(amount), "mirror_eur": str(amount), "net_delta_eur": "0.00",
        "actual_volume": "0.04", "mirror_closed_volume": "0.04",
        "mirror_entry_count": 1, "mirror_exit_count": 1,
        "actual_entry_price": facts[first_ticket + index][0],
        "actual_opened_at": facts[first_ticket + index][1],
        "actual_direction": "BUY", "actual_symbol": "XAUUSD",
    } for index in range(count)]


def _exit_rows(first_ticket, count, total, **kwargs):
    closes = {101: "2026-09-01T09:00:10+00:00", 201: "2026-09-01T10:00:15+00:00",
              202: "2026-09-01T10:01:00+00:00"}
    return [{
        "ticket": row["ticket"], "closed_at": closes[int(row["ticket"])],
        "entry_price": row["actual_entry_price"],
        "exit_price": str(Decimal(row["actual_entry_price"]) + Decimal(row["mirror_eur"]) / 4),
        "volume": "0.04", "pnl_eur": row["mirror_eur"], "reason": "per_leg_target",
    } for row in _ticket_rows(first_ticket, count, total, **kwargs)]


def _position(ticket, price, opened_at, closed_at, net):
    close_price = float(Decimal(str(price)) + Decimal(str(net)) / 4)
    deals = []
    for entry, at, fill, profit in ((0, opened_at, price, 0), (1, closed_at, close_price, net)):
        seconds = int(datetime.fromisoformat(at).timestamp())
        deals.append({
            "ticket": ticket * 10 + entry, "position_id": ticket, "entry": entry,
            "order": ticket * 100 + entry, "reason": 3 if entry == 0 else 5,
            "type": entry, "symbol": "XAUUSD", "volume": 0.04, "price": fill,
            "time": seconds, "time_msc": seconds * 1000, "time_utc": at,
            "profit": profit, "commission": 0, "swap": 0, "fee": 0,
        })
    return {
        "position_id": ticket, "ticket": ticket * 10, "volume": 0.04,
        "open_price": price, "open_dt_utc": opened_at, "close_price": close_price,
        "close_dt_utc": closed_at, "is_closed": True, "mt5_time_offset_s": 0,
        "pnl_net": net, "pnl_components": {"profit": net, "commission": 0,
                                              "swap": 0, "fee": 0, "net": net},
        "open_deal": deals[0], "close_deal": deals[-1], "deals": deals,
    }


def _ledger(*, second_open: str = "2026-09-01T10:00:20+00:00", second_entries: int = 2):
    second_net = 4 / second_entries
    second = [_position(201, 100.05, "2026-09-01T10:00:00+00:00",
                        "2026-09-01T10:00:15+00:00", second_net)]
    if second_entries == 2:
        second.append(_position(202, 98.50, second_open, "2026-09-01T10:01:00+00:00", second_net))
    return (
        {
            "sig_id": "canal2_1", "channel": "canal2", "direction": "BUY",
            "n_positions": 1, "pnl_real_mt5": 1, "reconciled_ok": True,
            "positions": [_position(101, 100.00, "2026-09-01T09:00:00+00:00",
                                     "2026-09-01T09:00:10+00:00", 1)],
        },
        {
            "sig_id": "canal2_2", "channel": "canal2", "direction": "BUY",
            "n_positions": second_entries, "pnl_real_mt5": 4, "reconciled_ok": True,
            "positions": second,
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
        management_report=_management(second_open="2026-09-01T10:00:05+00:00"),
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


def test_matching_totals_and_entries_do_not_verify_exit_decision_sequence() -> None:
    report = build_pipeline_truth_report(
        management_report=_management(second_entries=1),
        entry_watch_report=_entry(broker_status="exact"),
        prospective_report=_prospective(second_entries=1, second_money="4.00"),
        variant_name="deterministic_flat_cancel",
        ledger_rows=_ledger(second_entries=1),
        event_rows=(),
    )

    assert report["actual_vs_prospective"]["status"] == "exact"
    assert report["gates"]["management_replay"] == "pass"
    assert report["end_to_end_historical_extension_allowed"] is False
    assert report["gates"]["exit_decision_and_deal_sequence"] == "unverified"
    assert "exit_decision_and_deal_sequence_not_verified" in report["blockers"]


def test_entry_outcome_match_cannot_hide_a_trigger_tick_mismatch() -> None:
    report = build_pipeline_truth_report(
        management_report=_management(second_entries=1),
        entry_watch_report=_entry(broker_status="exact", trigger_exact=False),
        prospective_report=_prospective(second_entries=1, second_money="4.00"),
        variant_name="deterministic_flat_cancel",
        ledger_rows=_ledger(second_entries=1),
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


@pytest.mark.parametrize("schema,contract", [(1, None), (3, "ledger_bound_ticket_money_v3")])
def test_old_management_certificate_requires_reverification(schema, contract):
    management = _management(second_entries=1)
    management["schema_version"] = schema
    management["comparison_contract"] = contract
    report = build_pipeline_truth_report(
        management_report=management,
        entry_watch_report=_entry(broker_status="exact"),
        prospective_report=_prospective(second_entries=1, second_money="4.00"),
        variant_name="deterministic_flat_cancel",
        ledger_rows=_ledger(second_entries=1), event_rows=(),
    )
    assert report["gates"]["management_replay"] == "fail"
    assert "management_ticket_comparison_contract_unverified" in report["blockers"]
    assert report["end_to_end_historical_extension_allowed"] is False


@pytest.mark.parametrize("ticket_rows", [
    None,
    [],
    [{"ticket": "101", "status": "mismatch", "actual_mt5_eur": "1.00",
      "mirror_eur": "9.00", "actual_volume": "0.04", "mirror_closed_volume": "0.03"}],
])
def test_management_label_cannot_replace_ticket_evidence(ticket_rows):
    management = _management(second_entries=1)
    if ticket_rows is None:
        management["rows"][0].pop("ticket_rows")
    else:
        management["rows"][0]["ticket_rows"] = ticket_rows
    report = build_pipeline_truth_report(
        management_report=management, entry_watch_report=_entry(broker_status="exact"),
        prospective_report=_prospective(second_entries=1, second_money="4.00"),
        variant_name="deterministic_flat_cancel", ledger_rows=_ledger(second_entries=1), event_rows=(),
    )
    assert report["gates"]["management_replay"] == "fail"
    assert any("management_ticket_evidence" in reason for reason in report["blockers"])
    assert report["retrospective_management_replay"]["status"] == "blocked"


@pytest.mark.parametrize(("field", "value"), [
    ("status", "mismatch"), ("blockers", ["unexplained"]),
    ("actual_mt5_eur", "2.00"), ("mirror_eur", "9.00"), ("net_delta_eur", "0.01"),
    ("actual_volume", "0.03"), ("mirror_closed_volume", "0.03"),
    ("mirror_entry_count", 2), ("mirror_exit_count", 0), ("mirror_exit_count", 1.5),
    ("mirror_entry_count", float("inf")), ("mirror_eur", "1e1000"),
    ("actual_entry_price", "100.01"), ("actual_entry_price", None),
    ("actual_opened_at", "2026-09-01T09:00:00.001+00:00"),
    ("actual_opened_at", "2026-09-01T09:00:00"),
    ("actual_opened_at", "not a date"), ("actual_opened_at", None),
    ("actual_direction", "SELL"), ("actual_symbol", "EURUSD"),
])
def test_ticket_details_are_recomputed_even_when_every_summary_claims_exact(field, value):
    management = _management(second_entries=1)
    management["rows"][0]["ticket_rows"][0][field] = value
    report = build_pipeline_truth_report(
        management_report=management, entry_watch_report=_entry(broker_status="exact"),
        prospective_report=_prospective(second_entries=1, second_money="4.00"),
        variant_name="deterministic_flat_cancel", ledger_rows=_ledger(second_entries=1), event_rows=(),
    )
    assert report["gates"]["management_replay"] == "fail"
    assert any("management_ticket_evidence:canal2_1:" in reason for reason in report["blockers"])


def test_offsetting_report_ticket_errors_are_bound_to_the_actual_ledger():
    management = _management()
    for row, amount in zip(management["rows"][1]["ticket_rows"], ("1.00", "3.00"), strict=True):
        row["actual_mt5_eur"] = row["mirror_eur"] = amount
    report = build_pipeline_truth_report(
        management_report=management, entry_watch_report=_entry(broker_status="exact"),
        prospective_report=_prospective(second_entries=2, second_money="4.00"),
        variant_name="deterministic_flat_cancel", ledger_rows=_ledger(), event_rows=(),
    )
    assert report["gates"]["management_replay"] == "fail"
    assert "management_ticket_evidence:canal2_2:ticket_money_mismatch:201" in report["blockers"]
    assert "management_ticket_evidence:canal2_2:ticket_money_mismatch:202" in report["blockers"]


def test_duplicate_ticket_rows_do_not_satisfy_coverage():
    management = _management(second_entries=1)
    management["rows"][0]["ticket_rows"] *= 2
    report = build_pipeline_truth_report(
        management_report=management, entry_watch_report=_entry(broker_status="exact"),
        prospective_report=_prospective(second_entries=1, second_money="4.00"),
        variant_name="deterministic_flat_cancel", ledger_rows=_ledger(second_entries=1), event_rows=(),
    )
    assert report["gates"]["management_replay"] == "fail"
    assert "management_ticket_evidence:canal2_1:duplicate_ticket:101" in report["blockers"]


@pytest.mark.parametrize("field,value", [
    ("closed_at", "2026-09-01T09:00:10.001+00:00"),
    ("exit_price", "100.26"), ("reason", "trailing_stop"),
    ("pnl_eur", "9.00"), ("volume", "0.03"),
])
def test_exit_sequence_flags_do_not_replace_actual_exit_facts(field, value):
    management = _management(second_entries=1)
    management["rows"][0]["mirror_exit_rows"][0][field] = value
    management["rows"][0]["exit_deal_comparison"] = {
        "status": "exact", "exit_deal_facts_verified": True, "blockers": [],
    }
    report = build_pipeline_truth_report(
        management_report=management, entry_watch_report=_entry(broker_status="exact"),
        prospective_report=_prospective(second_entries=1, second_money="4.00"),
        variant_name="deterministic_flat_cancel", ledger_rows=_ledger(second_entries=1), event_rows=(),
    )
    assert report["gates"]["management_replay"] == "fail"
    assert report["gates"]["exit_deal_facts"] == "fail"
    assert report["end_to_end_historical_extension_allowed"] is False
