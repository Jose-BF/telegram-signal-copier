from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from zone_entry_policies import zone_policy_by_id
from zone_strategy_farm import (
    build_zone_farm_report,
    calculate_zone_policy_metrics,
    validate_observed_baseline,
)


BASE = datetime(2026, 8, 4, 10, tzinfo=timezone.utc)


def t(seconds):
    return BASE + timedelta(seconds=seconds)


def event(at, **values):
    return {"observed_ts_utc": at.isoformat(), **values}


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


def test_policy_metrics_calculate_money_drawdown_and_profit_factor():
    metrics = calculate_zone_policy_metrics([
        {"status": "filled", "money_status": "verified", "strategy_pnl": 10.0,
         "planned_volume": 0.05,
         "provider_signal_id": "a", "ready_at_utc": t(0).isoformat()},
        {"status": "filled", "money_status": "verified", "strategy_pnl": -15.0,
         "planned_volume": 0.05,
         "provider_signal_id": "b", "ready_at_utc": t(1).isoformat()},
        {"status": "filled", "money_status": "verified", "strategy_pnl": 5.0,
         "planned_volume": 0.05,
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
         "planned_volume": 0.01,
         "provider_signal_id": "a", "ready_at_utc": t(0).isoformat()},
        {"status": "filled", "money_status": "verified", "strategy_pnl": -1.0,
         "planned_volume": 0.01,
         "provider_signal_id": "b", "ready_at_utc": t(1).isoformat()},
    ])

    assert metrics["verified_net_pnl"] == 1.0
    assert metrics["risk_reference_volume"] == 0.05
    assert metrics["policy_planned_volume"] == 0.01
    assert metrics["risk_normalized_net_pnl"] == 5.0
    assert metrics["risk_normalized_maximum_drawdown"] == 5.0


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
