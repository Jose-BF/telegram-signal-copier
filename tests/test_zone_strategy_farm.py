from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from provider_zone_spec import build_zone_trade_spec
from zone_entry_policies import zone_policy_by_id
from zone_strategy_farm import (
    audit_observed_zone_execution,
    build_zone_farm_report,
    calculate_zone_policy_metrics,
    validate_observed_baseline,
)


BASE = datetime(2026, 8, 4, 10, tzinfo=timezone.utc)


def t(seconds):
    return BASE + timedelta(seconds=seconds)


def event(at, **values):
    return {"observed_ts_utc": at.isoformat(), **values}


def management(at, action, text=""):
    return event(at, classified_action=action, text=text)


def zone_record(signal_id="canal2_9000", *, complete=True, execution=()):
    return {
        "provider_signal_id": signal_id,
        "channel": "canal2",
        "record_type": "zone_plan",
        "signal_ts_utc": BASE.isoformat(),
        "zone_plan_timeline": [event(BASE, direction="BUY")],
        "entry_zone_timeline": (
            [event(BASE, range=[100.0, 105.0])] if complete else []
        ),
        "level_timeline": [event(BASE, tps=[110.0], sl=95.0)],
        "runtime_level_timeline": [],
        "management_events": [],
        "execution_batches": list(execution),
    }


class FakeTickSource:
    def __init__(self, ticks, blockers=()):
        self.ticks = ticks
        self.blockers = list(blockers)
        self.evidence_by_day = {
            "2026-08-04": {
                "parquet_sha256": "a" * 64,
                "utc_offset_seconds": 10800,
            }
        }

    def load_day(self, day):
        assert day == date(2026, 8, 4)
        return self.ticks.copy(deep=True), self.evidence_by_day[str(day)], list(
            self.blockers
        )


class FakeMoneyConverter:
    currency = "EUR"
    currency_digits = 2

    def convert_leg(self, **values):
        delta = values["close_price"] - values["open_price"]
        if values["direction"] == "SELL":
            delta = -delta
        pnl = round(delta * 100 * values["volume"], 2)
        return {
            "status": "verified",
            "strategy_pnl": pnl,
            "profit_currency_pnl": pnl,
            "pnl_currency": "EUR",
            "blockers": [],
        }


class CapturingMoneyConverter(FakeMoneyConverter):
    def __init__(self):
        self.offsets = []

    def convert_leg(self, **values):
        self.offsets.append(values.get("verified_utc_offset_seconds"))
        return super().convert_leg(**values)


def test_farm_emits_one_row_per_plan_and_policy_even_when_blocked():
    catalog = {
        "schema_version": 7,
        "signals": [zone_record(), zone_record("canal2_9001", complete=False)],
    }
    original = deepcopy(catalog)
    tick_source = FakeTickSource(pd.DataFrame([
        (t(0), 104.8, 105.0),
        (t(1), 110.0, 110.2),
    ], columns=["time_utc", "bid", "ask"]))
    policies = (
        zone_policy_by_id("one_first_touch"),
        zone_policy_by_id("five_equal_limits"),
    )

    report = build_zone_farm_report(
        catalog,
        tick_source,
        policies=policies,
        money_converter=FakeMoneyConverter(),
        since="2026-08-04",
        until="2026-08-04",
    )

    assert len(report["rows"]) == 4
    assert report["scope"]["zone_plans"] == 2
    assert report["scope"]["complete_zone_plans"] == 1
    assert report["scope"]["incomplete_zone_plans"] == 1
    assert report["scope"]["tick_valid_complete_zone_plans"] == 1
    assert report["scope"]["policy_count"] == 2
    assert report["summary"]["blocked_rows"] == 2
    assert report["audit_summary"]["disagreements"] == 0
    assert catalog == original


def test_invalid_tick_day_blocks_every_policy_without_dropping_plan():
    catalog = {"schema_version": 7, "signals": [zone_record()]}
    source = FakeTickSource(
        pd.DataFrame(columns=["time_utc", "bid", "ask"]),
        blockers=["semantic_tick_time_unverified:2026-08-04"],
    )

    report = build_zone_farm_report(
        catalog,
        source,
        policies=(zone_policy_by_id("one_first_touch"),),
        since="2026-08-04",
        until="2026-08-04",
    )

    assert len(report["rows"]) == 1
    assert report["rows"][0]["status"] == "blocked"
    assert report["rows"][0]["blockers"] == [
        "semantic_tick_time_unverified:2026-08-04"
    ]


def test_farm_passes_verified_tick_clock_to_money_conversion():
    catalog = {"schema_version": 7, "signals": [zone_record()]}
    source = FakeTickSource(pd.DataFrame([
        (t(0), 104.8, 105.0),
        (t(1), 110.0, 110.2),
    ], columns=["time_utc", "bid", "ask"]))
    converter = CapturingMoneyConverter()

    report = build_zone_farm_report(
        catalog,
        source,
        policies=(zone_policy_by_id("one_first_touch"),),
        money_converter=converter,
        since="2026-08-04",
        until="2026-08-04",
    )

    assert report["rows"][0]["money_status"] == "verified"
    assert converter.offsets == [10800]


def test_farm_separates_observed_execution_from_modeled_policy_match():
    trigger_server_msc = int((t(0.05) + timedelta(hours=3)).timestamp() * 1000)
    execution = {
        "execution_batch_id": "canal2_9000#exec1",
        "signal_received_utc": t(0.2).isoformat(),
        "entry_provenance": {
            "source_kind": "zone_first_touch",
            "zone_trigger_kind": "first_touch",
            "zone_trigger_time_msc": trigger_server_msc,
        },
        "fills": [
            {
                "observed_utc": t(0.3 + index * 0.1).isoformat(),
                "price": 104.95 - index * 0.01,
            }
            for index in range(5)
        ],
    }
    catalog = {
        "schema_version": 7,
        "signals": [zone_record(execution=[execution])],
    }
    source = FakeTickSource(pd.DataFrame([
        (t(0), 104.8, 105.0),
        (t(0.3), 104.7, 104.9),
        (t(0.4), 104.69, 104.89),
        (t(0.5), 104.68, 104.88),
        (t(0.6), 104.67, 104.87),
        (t(0.7), 104.66, 104.86),
        (t(1), 110.0, 110.2),
    ], columns=["time_utc", "bid", "ask"]))

    report = build_zone_farm_report(
        catalog,
        source,
        policies=(zone_policy_by_id("one_first_touch"),),
        money_converter=FakeMoneyConverter(),
        since="2026-08-04",
        until="2026-08-04",
    )

    assert report["observed_execution_summary"]["proofs"] == 1
    assert report["observed_execution_summary"]["verified"] == 1
    assert report["modeled_baseline_summary"]["proofs"] == 0


def test_lifecycle_blocker_does_not_erase_observed_execution_proof():
    trigger_server_msc = int((t(0.3) + timedelta(hours=3)).timestamp() * 1000)
    execution = {
        "execution_batch_id": "canal2_9000#exec1",
        "signal_received_utc": t(0.35).isoformat(),
        "entry_provenance": {
            "source_kind": "zone_active",
            "zone_trigger_kind": "explicit_active",
            "zone_trigger_time_msc": trigger_server_msc,
        },
        "fills": [
            {
                "observed_utc": t(0.4 + index * 0.1).isoformat(),
                "price": 104.95 - index * 0.01,
            }
            for index in range(5)
        ],
    }
    record = zone_record(execution=[execution])
    record["management_events"] = [
        management(t(0.1), None, "Left without us"),
        management(t(0.2), None, "Still valid"),
        management(t(0.3), "ACTIVATE_ZONE", "Active"),
    ]
    source = FakeTickSource(pd.DataFrame([
        (t(0), 104.8, 105.0),
        (t(0.4), 104.7, 104.9),
        (t(0.5), 104.69, 104.89),
        (t(0.6), 104.68, 104.88),
        (t(0.7), 104.67, 104.87),
        (t(0.8), 104.66, 104.86),
        (t(1), 110.0, 110.2),
    ], columns=["time_utc", "bid", "ask"]))

    report = build_zone_farm_report(
        {"schema_version": 7, "signals": [record]},
        source,
        policies=(zone_policy_by_id("one_first_touch"),),
        money_converter=FakeMoneyConverter(),
        since="2026-08-04",
        until="2026-08-04",
    )

    assert report["rows"][0]["status"] == "blocked"
    assert "unsupported_rearm_after_terminal" in report["rows"][0]["blockers"]
    assert report["observed_execution_summary"]["proofs"] == 1
    assert report["observed_execution_summary"]["verified"] == 1


def test_farm_keeps_observed_mt5_result_separate_from_modeled_pnl():
    catalog = {"schema_version": 7, "signals": [zone_record()]}
    source = FakeTickSource(pd.DataFrame([
        (t(0), 104.8, 105.0),
        (t(1), 110.0, 110.2),
    ], columns=["time_utc", "bid", "ask"]))

    report = build_zone_farm_report(
        catalog,
        source,
        policies=(zone_policy_by_id("one_first_touch"),),
        money_converter=FakeMoneyConverter(),
        observed_trades=[{
            "sig_id": "canal2_9000",
            "status": "closed",
            "pnl_real_mt5": -3.25,
            "pnl_mt5_complete": True,
            "reconciled_ok": True,
            "analysis_excluded": False,
            "tickets": [{"ticket": 1}],
        }],
        since="2026-08-04",
        until="2026-08-04",
    )

    observed = report["observed_live_result"]
    assert observed["comparison_role"] == "context_only"
    assert observed["trades"] == 1
    assert observed["verified_trades"] == 1
    assert observed["verified_net_pnl"] == -3.25
    assert observed["pnl_currency"] == "EUR"
    assert observed["modeled_common_exit_by_policy"]["one_first_touch"] == {
        "plans": 1,
        "verified_net_pnl": 5.0,
    }


def test_observed_baseline_validation_uses_all_execution_fills():
    simulated = {
        "filled_legs": [
            {"open_time_utc": t(0).isoformat(), "open_price": 105.0},
            {"open_time_utc": t(0.125).isoformat(), "open_price": 104.9},
        ]
    }
    execution = {
        "fills": [
            {"observed_utc": t(0.5).isoformat(), "price": 105.2},
            {"observed_utc": t(0.7).isoformat(), "price": 105.0},
        ]
    }

    proof = validate_observed_baseline(simulated, execution)

    assert proof["actual_fill_count"] == 2
    assert proof["simulated_fill_count"] == 2
    assert proof["time_tolerance_ms"] == 3000
    assert proof["price_tolerance"] == 1.0
    assert proof["max_time_delta_ms"] == 575
    assert proof["max_price_delta"] == 0.2
    assert proof["within_tolerance"] is True


def test_observed_first_touch_execution_is_audited_against_independent_ticks():
    trigger_server_msc = int((t(0.05) + timedelta(hours=3)).timestamp() * 1000)
    execution = {
        "signal_received_utc": t(0.2).isoformat(),
        "entry_provenance": {
            "source_kind": "zone_first_touch",
            "zone_trigger_kind": "first_touch",
            "zone_trigger_time_msc": trigger_server_msc,
        },
        "fills": [
            {"observed_utc": t(0.3).isoformat(), "price": 104.95},
        ],
    }
    frame = pd.DataFrame([
        (t(0), 104.8, 105.0),
        (t(0.3), 104.7, 104.9),
    ], columns=["time_utc", "bid", "ask"])

    proof = audit_observed_zone_execution(
        build_zone_trade_spec(zone_record(execution=[execution])),
        execution,
        frame,
        zone_audit={"first_touch_utc": t(0).isoformat()},
        verified_utc_offset_seconds=10800,
        expected_fill_count=1,
    )

    assert proof["status"] == "verified"
    assert proof["trigger_kind"] == "first_touch"
    assert proof["trigger_delta_ms"] == 50
    assert proof["max_fill_tick_delta_ms"] == 0
    assert proof["max_fill_price_delta"] == 0.05


def test_observed_active_execution_uses_provider_activation_not_zone_touch():
    record = zone_record()
    record["management_events"] = [management(t(1), None, "Active")]
    spec = build_zone_trade_spec(record)
    trigger_server_msc = int((t(0.9) + timedelta(hours=3)).timestamp() * 1000)
    execution = {
        "signal_received_utc": t(1.1).isoformat(),
        "entry_provenance": {
            "source_kind": "zone_explicit_active",
            "zone_trigger_kind": "explicit_active",
            "zone_trigger_time_msc": trigger_server_msc,
        },
        "fills": [
            {"observed_utc": t(1.2).isoformat(), "price": 106.2},
        ],
    }
    frame = pd.DataFrame([
        (t(1.2), 106.0, 106.2),
        (t(2), 104.8, 105.0),
    ], columns=["time_utc", "bid", "ask"])

    proof = audit_observed_zone_execution(
        spec,
        execution,
        frame,
        zone_audit={"first_touch_utc": t(2).isoformat()},
        verified_utc_offset_seconds=10800,
        expected_fill_count=1,
    )

    assert proof["status"] == "verified"
    assert proof["trigger_kind"] == "explicit_active"
    assert proof["trigger_delta_ms"] == 100


def test_observed_execution_audit_never_uses_a_future_tick():
    trigger_server_msc = int((t(0.05) + timedelta(hours=3)).timestamp() * 1000)
    execution = {
        "signal_received_utc": t(0.1).isoformat(),
        "entry_provenance": {
            "source_kind": "zone_first_touch",
            "zone_trigger_kind": "first_touch",
            "zone_trigger_time_msc": trigger_server_msc,
        },
        "fills": [
            {"observed_utc": t(0.18).isoformat(), "price": 105.0},
        ],
    }
    frame = pd.DataFrame([
        (t(0.1), 99.8, 100.0),
        (t(0.2), 104.8, 105.0),
    ], columns=["time_utc", "bid", "ask"])

    proof = audit_observed_zone_execution(
        build_zone_trade_spec(zone_record(execution=[execution])),
        execution,
        frame,
        zone_audit={"first_touch_utc": t(0.05).isoformat()},
        verified_utc_offset_seconds=10800,
        expected_fill_count=1,
        fill_price_tolerance=1.0,
    )

    assert proof["status"] == "blocked"
    assert proof["fills"][0]["tick_utc"] == t(0.1).isoformat()
    assert "execution_fill_price_outside_tolerance:0" in proof["blockers"]


def test_policy_metrics_calculate_money_drawdown_and_profit_factor():
    metrics = calculate_zone_policy_metrics([
        {"status": "filled", "money_status": "verified", "strategy_pnl": 10.0,
         "planned_volume": 0.05, "planned_risk_price_lots": 0.25,
         "risk_reference_price_lots": 0.25,
         "provider_signal_id": "a", "ready_at_utc": t(0).isoformat()},
        {"status": "filled", "money_status": "verified", "strategy_pnl": -15.0,
         "planned_volume": 0.05, "planned_risk_price_lots": 0.25,
         "risk_reference_price_lots": 0.25,
         "provider_signal_id": "b", "ready_at_utc": t(1).isoformat()},
        {"status": "filled", "money_status": "verified", "strategy_pnl": 5.0,
         "planned_volume": 0.05, "planned_risk_price_lots": 0.25,
         "risk_reference_price_lots": 0.25,
         "provider_signal_id": "c", "ready_at_utc": t(2).isoformat()},
    ])

    assert metrics["verified_net_pnl"] == 0.0
    assert metrics["maximum_drawdown"] == 15.0
    assert metrics["worst_basket"] == -15.0
    assert metrics["profit_factor"] == 1.0
    assert metrics["expectancy_per_verified_plan"] == 0.0
    assert metrics["risk_normalized_net_pnl"] == 0.0
    assert metrics["risk_normalized_maximum_drawdown"] == 15.0


def test_policy_metrics_normalize_smaller_policies_to_equal_planned_risk():
    metrics = calculate_zone_policy_metrics([
        {"status": "filled", "money_status": "verified", "strategy_pnl": 2.0,
         "planned_volume": 0.01, "planned_risk_price_lots": 0.1,
         "risk_reference_price_lots": 0.5,
         "provider_signal_id": "a", "ready_at_utc": t(0).isoformat()},
        {"status": "filled", "money_status": "verified", "strategy_pnl": -1.0,
         "planned_volume": 0.01, "planned_risk_price_lots": 0.1,
         "risk_reference_price_lots": 0.5,
         "provider_signal_id": "b", "ready_at_utc": t(1).isoformat()},
    ])

    assert metrics["verified_net_pnl"] == 1.0
    assert metrics["risk_reference_volume"] == 0.05
    assert metrics["policy_planned_volume"] == 0.01
    assert metrics["risk_normalized_net_pnl"] == 5.0
    assert metrics["risk_normalized_maximum_drawdown"] == 5.0


def test_policy_metrics_normalize_equal_volume_by_actual_sl_risk():
    metrics = calculate_zone_policy_metrics([
        {
            "status": "filled",
            "money_status": "verified",
            "strategy_pnl": 4.0,
            "planned_volume": 0.05,
            "planned_risk_price_lots": 0.5,
            "risk_reference_price_lots": 0.5,
            "provider_signal_id": "a",
            "ready_at_utc": t(0).isoformat(),
        },
        {
            "status": "filled",
            "money_status": "verified",
            "strategy_pnl": 4.0,
            "planned_volume": 0.05,
            "planned_risk_price_lots": 0.25,
            "risk_reference_price_lots": 0.5,
            "provider_signal_id": "b",
            "ready_at_utc": t(1).isoformat(),
        },
    ])

    assert metrics["verified_net_pnl"] == 8.0
    assert metrics["risk_normalized_net_pnl"] == 12.0
    assert metrics["risk_reference"] == "current_live_zone_trigger_per_plan"


def test_policy_metrics_publish_reproducible_daily_and_leg_contributions():
    metrics = calculate_zone_policy_metrics([
        {
            "status": "filled",
            "money_status": "verified",
            "strategy_pnl": 3.0,
            "planned_volume": 0.05,
            "planned_risk_price_lots": 0.25,
            "risk_reference_price_lots": 0.25,
            "planned_leg_count": 2,
            "signal_date": "2026-08-04",
            "provider_signal_id": "a",
            "ready_at_utc": t(0).isoformat(),
            "filled_legs": [
                {
                    "leg_index": 0,
                    "depth_fraction": 0.0,
                    "money": {"status": "verified", "strategy_pnl": 5.0},
                },
                {
                    "leg_index": 1,
                    "depth_fraction": 1.0,
                    "money": {"status": "verified", "strategy_pnl": -2.0},
                },
            ],
            "unfilled_legs": [],
        },
        {
            "status": "filled",
            "money_status": "verified",
            "strategy_pnl": -1.0,
            "planned_volume": 0.05,
            "planned_risk_price_lots": 0.25,
            "risk_reference_price_lots": 0.25,
            "planned_leg_count": 2,
            "signal_date": "2026-08-05",
            "provider_signal_id": "b",
            "ready_at_utc": t(1).isoformat(),
            "filled_legs": [
                {
                    "leg_index": 0,
                    "depth_fraction": 0.0,
                    "money": {"status": "verified", "strategy_pnl": -1.0},
                },
            ],
            "unfilled_legs": [
                {"leg_index": 1, "depth_fraction": 1.0},
            ],
        },
    ])

    assert metrics["daily_results"] == [
        {
            "signal_date": "2026-08-04",
            "verified_plans": 1,
            "filled_plans": 1,
            "verified_net_pnl": 3.0,
            "risk_normalized_net_pnl": 3.0,
        },
        {
            "signal_date": "2026-08-05",
            "verified_plans": 1,
            "filled_plans": 1,
            "verified_net_pnl": -1.0,
            "risk_normalized_net_pnl": -1.0,
        },
    ]
    assert metrics["leg_contributions"] == [
        {
            "leg_index": 0,
            "depth_fraction": 0.0,
            "planned_occurrences": 2,
            "filled_occurrences": 2,
            "fill_rate": 1.0,
            "verified_net_pnl": 4.0,
            "risk_normalized_net_pnl": 4.0,
        },
        {
            "leg_index": 1,
            "depth_fraction": 1.0,
            "planned_occurrences": 2,
            "filled_occurrences": 1,
            "fill_rate": 0.5,
            "verified_net_pnl": -2.0,
            "risk_normalized_net_pnl": -2.0,
        },
    ]


def test_farm_is_deterministic_and_contains_no_live_execution_imports():
    catalog = {"schema_version": 7, "signals": [zone_record()]}
    tick_source = FakeTickSource(pd.DataFrame([
        (t(0), 104.8, 105.0),
        (t(1), 110.0, 110.2),
    ], columns=["time_utc", "bid", "ask"]))
    kwargs = {
        "policies": (zone_policy_by_id("one_first_touch"),),
        "money_converter": FakeMoneyConverter(),
        "since": "2026-08-04",
        "until": "2026-08-04",
    }

    first = build_zone_farm_report(catalog, tick_source, **kwargs)
    second = build_zone_farm_report(catalog, tick_source, **kwargs)
    source = Path("zone_strategy_farm.py").read_text(encoding="utf-8")

    assert first == second
    assert "executor" not in source
    assert "MetaTrader5" not in source
    assert "mt5_session" not in source


def test_farm_fingerprints_money_conversion_tick_days_after_pricing():
    catalog = {"schema_version": 7, "signals": [zone_record()]}
    tick_source = FakeTickSource(pd.DataFrame([
        (t(0), 104.8, 105.0),
        (t(1), 110.0, 110.2),
    ], columns=["time_utc", "bid", "ask"]))
    converter = FakeMoneyConverter()
    converter.conversion_tick_evidence = {
        "2026-08-04": {
            "symbol": "EURUSD",
            "parquet_sha256": "b" * 64,
            "contract_sha256": "c" * 64,
        }
    }

    report = build_zone_farm_report(
        catalog,
        tick_source,
        policies=(zone_policy_by_id("one_first_touch"),),
        money_converter=converter,
        since="2026-08-04",
        until="2026-08-04",
    )

    assert report["source_fingerprints"]["money_tick_days"] == (
        converter.conversion_tick_evidence
    )


def test_farm_never_invents_an_end_of_day_close_for_an_open_zone_leg():
    catalog = {"schema_version": 7, "signals": [zone_record()]}
    tick_source = FakeTickSource(pd.DataFrame([
        (t(0), 104.8, 105.0),
        (t(1), 105.8, 106.0),
    ], columns=["time_utc", "bid", "ask"]))

    report = build_zone_farm_report(
        catalog,
        tick_source,
        policies=(zone_policy_by_id("one_first_touch"),),
        money_converter=FakeMoneyConverter(),
        since="2026-08-04",
        until="2026-08-04",
    )

    row = report["rows"][0]
    assert row["status"] == "blocked"
    assert row["blockers"] == ["leg_0:open_at_horizon"]
    assert row["strategy_pnl"] is None
