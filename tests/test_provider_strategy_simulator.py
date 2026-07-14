import warnings
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

import provider_strategy_simulator as provider_simulator
from provider_strategy_simulator import (
    VirtualEntry,
    prepare_replay_ticks,
    select_entry_tick,
    simulate_provider_policy,
)
from provider_trade_spec import ProviderTradeSpec
from strategy_policies import default_policy_catalog, policy_by_id


TRIGGER = datetime(2026, 7, 8, 11, 0, tzinfo=timezone.utc)


def _spec(
    *,
    direction="BUY",
    trigger=TRIGGER,
    latency_ms=0,
    blockers=(),
    volume_per_leg=0.01,
    provider_tps=(4110.0,),
    provider_sl=4090.0,
    level_timeline=(),
    management_events=(),
    execution_sig_ids=(),
    policy_evidence_gaps=(),
    leg_count=None,
):
    provider_tps = tuple(provider_tps)
    return ProviderTradeSpec(
        provider_signal_id="canal2_3200",
        channel="canal2",
        direction=direction,
        trigger_observed_utc=trigger,
        latency_ms=latency_ms,
        volume_per_leg=volume_per_leg,
        leg_count=(len(provider_tps) or 1) if leg_count is None else leg_count,
        provider_tps=provider_tps,
        provider_sl=provider_sl,
        level_timeline=tuple(level_timeline),
        management_events=tuple(management_events),
        execution_sig_ids=tuple(execution_sig_ids),
        entry_blockers=tuple(blockers),
        policy_evidence_gaps=tuple(policy_evidence_gaps),
    )


def _ticks(rows):
    return pd.DataFrame(rows, columns=["time_utc", "bid", "ask"])


def _level_event(observed, *, tps=(105.0,), sl=95.0):
    return {
        "observed_ts_utc": observed.isoformat(),
        "telegram_ts_utc": (observed - timedelta(seconds=1)).isoformat(),
        "tps": list(tps),
        "sl": sl,
    }


def _management_event(observed, action="MOVE_SL_TO_BE"):
    return {
        "observed_ts_utc": observed.isoformat(),
        "telegram_ts_utc": (observed - timedelta(seconds=1)).isoformat(),
        "classified_action": action,
    }


EXPECTED_POLICY_ROW_KEYS = {
    "provider_signal_id",
    "channel",
    "policy_id",
    "status",
    "result_unit",
    "money_status",
    "strategy_value",
    "strategy_pnl",
    "entry",
    "blockers",
    "assumptions",
    "legs",
}


def _assert_policy_row_shape(result):
    assert set(result) == EXPECTED_POLICY_ROW_KEYS
    assert result["result_unit"] == "xauusd_price_units"
    assert result["money_status"] == "unverified"
    assert result["strategy_pnl"] is None
    assert "actual_pnl" not in result


@pytest.mark.parametrize(
    ("direction", "expected_side", "expected_price"),
    [
        ("BUY", "ask", 4100.25),
        ("SELL", "bid", 4100.00),
    ],
)
def test_entry_uses_directional_quote_side(
    direction,
    expected_side,
    expected_price,
):
    ticks = _ticks(
        [
            (TRIGGER - timedelta(milliseconds=1), 4099.00, 4099.25),
            (TRIGGER, 4100.00, 4100.25),
            (TRIGGER + timedelta(milliseconds=1), 4101.00, 4101.25),
        ]
    )

    result = select_entry_tick(_spec(direction=direction), ticks)

    assert result == VirtualEntry(
        status="entered",
        time_utc=TRIGGER,
        price=expected_price,
        side=expected_side,
        latency_ms=0,
        blockers=(),
    )


def test_latency_selects_first_tick_strictly_after_threshold_when_needed():
    ticks = _ticks(
        [
            (TRIGGER + timedelta(milliseconds=249), 4100.00, 4100.25),
            (TRIGGER + timedelta(milliseconds=251), 4101.00, 4101.25),
            (TRIGGER + timedelta(milliseconds=300), 4102.00, 4102.25),
        ]
    )

    result = select_entry_tick(_spec(latency_ms=250), ticks)

    assert result.time_utc == TRIGGER + timedelta(milliseconds=251)
    assert result.price == 4101.25
    assert result.latency_ms == 250


def test_tick_exactly_at_latency_threshold_is_eligible():
    threshold = TRIGGER + timedelta(milliseconds=250)
    ticks = _ticks(
        [
            (threshold - timedelta(microseconds=1), 4100.00, 4100.25),
            (threshold, 4101.00, 4101.25),
            (threshold + timedelta(microseconds=1), 4102.00, 4102.25),
        ]
    )

    result = select_entry_tick(_spec(latency_ms=250), ticks)

    assert result.time_utc == threshold
    assert result.price == 4101.25


def test_extreme_latency_blocks_when_entry_threshold_overflows():
    ticks = _ticks([(TRIGGER, 4100.00, 4100.25)])

    result = select_entry_tick(_spec(latency_ms=10**30), ticks)

    assert result.status == "blocked"
    assert result.blockers == ("entry_threshold_out_of_range",)


def test_year_2300_blocks_when_threshold_has_no_nanosecond_representation():
    trigger = datetime(2300, 1, 1, tzinfo=timezone.utc)
    ticks = _ticks([("2300-01-01T00:00:00+00:00", 4100.00, 4100.25)])

    result = select_entry_tick(_spec(trigger=trigger), ticks)

    assert result.status == "blocked"
    assert result.blockers == ("entry_threshold_out_of_range",)


def test_unsorted_duplicate_timestamps_keep_stable_source_order():
    offsets_ms = np.array([100, 200, 100, 0, 0])
    ask_prices = np.array([4100.25, 4200.25, 4102.25, 4003.25, 4004.25])
    ticks = _ticks(
        [
            (
                TRIGGER + timedelta(milliseconds=int(offset)),
                ask - 0.25,
                ask,
            )
            for offset, ask in zip(offsets_ms, ask_prices, strict=True)
        ]
    )

    result = select_entry_tick(_spec(latency_ms=100), ticks)

    unstable_order = np.argsort(offsets_ms, kind="quicksort")
    unstable_start = np.searchsorted(
        offsets_ms[unstable_order],
        100,
        side="left",
    )
    assert unstable_order[unstable_start] == 2
    assert ask_prices[unstable_order[unstable_start]] == 4102.25
    assert result.time_utc == TRIGGER + timedelta(milliseconds=100)
    assert result.price == 4100.25


@pytest.mark.parametrize(
    ("direction", "bad_side", "expected_side", "expected_price"),
    [
        ("BUY", "ask", "ask", 4105.25),
        ("SELL", "bid", "bid", 4105.00),
    ],
)
def test_nonpositive_and_nonfinite_side_prices_are_skipped(
    direction,
    bad_side,
    expected_side,
    expected_price,
):
    invalid_prices = [np.nan, 0.0, -1.0, np.inf, -np.inf]
    rows = []
    for index, invalid_price in enumerate(invalid_prices):
        bid, ask = 4200.0 + index, 4200.25 + index
        if bad_side == "bid":
            bid = invalid_price
        else:
            ask = invalid_price
        rows.append((TRIGGER + timedelta(milliseconds=index), bid, ask))
    rows.append((TRIGGER + timedelta(milliseconds=5), 4105.00, 4105.25))

    result = select_entry_tick(_spec(direction=direction), _ticks(rows))

    assert result.status == "entered"
    assert result.side == expected_side
    assert result.price == expected_price
    assert result.time_utc == TRIGGER + timedelta(milliseconds=5)


@pytest.mark.parametrize(
    "unsafe_quote",
    [True, "4100.25", {"price": 4100.25}],
    ids=["bool", "numeric-string", "object"],
)
def test_unsafe_quote_values_do_not_become_entry_prices(unsafe_quote):
    ticks = _ticks([(TRIGGER, 4100.00, unsafe_quote)])

    result = select_entry_tick(_spec(direction="BUY"), ticks)

    assert result.blockers == ("no_tradable_entry_tick",)


def test_complex_quote_is_rejected_without_emitting_a_warning():
    ticks = _ticks([(TRIGGER, 4100.00, 4100.25 + 2j)])

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        result = select_entry_tick(_spec(direction="BUY"), ticks)

    assert result.blockers == ("no_tradable_entry_tick",)


def test_object_quote_column_skips_unsafe_values_until_first_real_number():
    ticks = _ticks(
        [
            (TRIGGER, 4100.00, 0.0),
            (TRIGGER + timedelta(milliseconds=1), 4101.00, 0.0),
            (TRIGGER + timedelta(milliseconds=2), 4102.00, 0.0),
        ]
    )
    ticks["ask"] = pd.Series(["4100.25", False, 4102.25], dtype=object)

    result = select_entry_tick(_spec(direction="BUY"), ticks)

    assert result.time_utc == TRIGGER + timedelta(milliseconds=2)
    assert result.price == 4102.25


@pytest.mark.parametrize(
    ("dtype", "expected_price"),
    [("float64", 4100.25), ("int64", 4100.0)],
)
def test_real_numeric_quote_dtypes_remain_tradable(dtype, expected_price):
    ticks = _ticks([(TRIGGER, 4099.00, 0.0)])
    ticks["ask"] = pd.Series([expected_price], dtype=dtype)

    result = select_entry_tick(_spec(direction="BUY"), ticks)

    assert result.price == expected_price


@pytest.mark.parametrize(
    "ticks",
    [
        None,
        pd.DataFrame(columns=["time_utc", "bid", "ask"]),
    ],
)
def test_missing_or_empty_ticks_block_entry(ticks):
    result = select_entry_tick(_spec(), ticks)

    assert result.status == "blocked"
    assert result.time_utc is None
    assert result.price is None
    assert result.side is None
    assert result.blockers == ("missing_ticks",)


@pytest.mark.parametrize(
    ("ticks", "expected_blocker"),
    [
        (
            pd.DataFrame({"other": [1]}),
            "missing_tick_columns:time_utc,bid,ask",
        ),
        (
            pd.DataFrame({"bid": [4100.0]}),
            "missing_tick_columns:time_utc,ask",
        ),
        (
            pd.DataFrame(
                {
                    "ask": [4100.25],
                    "time_utc": [TRIGGER],
                }
            ),
            "missing_tick_columns:bid",
        ),
    ],
)
def test_missing_tick_columns_are_reported_in_required_order(
    ticks,
    expected_blocker,
):
    result = select_entry_tick(_spec(), ticks)

    assert result.blockers == (expected_blocker,)


def test_all_invalid_tick_times_block_entry():
    ticks = _ticks(
        [
            (None, 4100.00, 4100.25),
            ("not-a-time", 4101.00, 4101.25),
        ]
    )

    result = select_entry_tick(_spec(), ticks)

    assert result.blockers == ("invalid_tick_times",)


def test_ticks_only_before_entry_trigger_block_entry():
    ticks = _ticks(
        [
            (TRIGGER - timedelta(milliseconds=2), 4100.00, 4100.25),
            (TRIGGER - timedelta(milliseconds=1), 4101.00, 4101.25),
        ]
    )

    result = select_entry_tick(_spec(), ticks)

    assert result.blockers == ("missing_ticks_after_entry_trigger",)


def test_post_trigger_ticks_without_tradable_side_block_entry():
    ticks = _ticks(
        [
            (TRIGGER, 4200.00, np.nan),
            (TRIGGER + timedelta(milliseconds=1), 4201.00, 0.0),
            (TRIGGER + timedelta(milliseconds=2), 4202.00, np.inf),
        ]
    )

    result = select_entry_tick(_spec(direction="BUY"), ticks)

    assert result.blockers == ("no_tradable_entry_tick",)


def test_any_invalid_time_row_blocks_even_when_valid_entry_tick_exists():
    ticks = _ticks(
        [
            ("invalid", 4000.00, 4000.25),
            (TRIGGER, 4100.00, 4100.25),
        ]
    )

    result = select_entry_tick(_spec(), ticks)

    assert result.status == "blocked"
    assert result.price is None
    assert result.blockers == ("invalid_tick_times",)


@pytest.mark.parametrize(
    "invalid_time",
    [
        1_783_508_400,
        1_783_508_400.0,
        "July 8, 2026 11:00 UTC",
    ],
)
def test_epoch_numbers_and_non_iso_strings_are_invalid_tick_times(invalid_time):
    ticks = _ticks(
        [
            (invalid_time, 4000.00, 4000.25),
            (TRIGGER, 4100.00, 4100.25),
        ]
    )

    result = select_entry_tick(_spec(), ticks)

    assert result.blockers == ("invalid_tick_times",)


def test_blocked_spec_short_circuits_and_inherits_only_spec_blockers():
    spec = _spec(
        direction="HOLD",
        trigger=None,
        latency_ms=250,
        blockers=("manual_review", "missing_direction"),
    )

    result = select_entry_tick(spec, pd.DataFrame())

    assert result == VirtualEntry(
        status="blocked",
        time_utc=None,
        price=None,
        side=None,
        latency_ms=250,
        blockers=("manual_review", "missing_direction"),
    )


@pytest.mark.parametrize(
    ("direction", "expected_blocker"),
    [
        (None, "missing_direction"),
        ("HOLD", "invalid_direction"),
        ("buy", "invalid_direction"),
    ],
)
def test_invalid_direction_on_inconsistent_ready_spec_blocks_entry(
    direction,
    expected_blocker,
):
    result = select_entry_tick(
        _spec(direction=direction),
        _ticks([(TRIGGER, 4100.00, 4100.25)]),
    )

    assert result.blockers == (expected_blocker,)


def test_selector_does_not_mutate_spec_or_ticks():
    spec = _spec(latency_ms=100)
    ticks = _ticks(
        [
            (TRIGGER + timedelta(milliseconds=200), 4101.00, 4101.25),
            (TRIGGER + timedelta(milliseconds=100), 4100.00, 4100.25),
        ]
    )
    ticks.index = pd.Index([8, 3], name="source_row")
    ticks.attrs["cache_contract"] = {"version": 3}
    original_spec = spec
    original_ticks = ticks.copy(deep=True)

    select_entry_tick(spec, ticks)

    assert spec == original_spec
    assert_frame_equal(ticks, original_ticks)
    assert ticks.attrs == original_ticks.attrs


def test_offset_timestamp_input_is_normalized_to_utc_aware_datetime():
    ticks = _ticks(
        [
            ("2026-07-08T13:00:00.250+02:00", 4100.00, 4100.25),
        ]
    )

    result = select_entry_tick(_spec(latency_ms=250), ticks)

    assert result.time_utc == datetime(
        2026,
        7,
        8,
        11,
        0,
        0,
        250000,
        tzinfo=timezone.utc,
    )
    assert result.time_utc.tzinfo is timezone.utc


@pytest.mark.parametrize("latency_ms", [0, 1, 250, 1000])
def test_entry_boundary_never_selects_a_pre_threshold_tick(latency_ms):
    threshold = TRIGGER + timedelta(milliseconds=latency_ms)
    ticks = _ticks(
        [
            (threshold - timedelta(microseconds=1), 4999.00, 4999.25),
            (threshold + timedelta(microseconds=1), 4100.00, 4100.25),
        ]
    )

    result = select_entry_tick(_spec(latency_ms=latency_ms), ticks)

    assert result.time_utc >= threshold
    assert result.price == 4100.25


def test_virtual_entry_is_frozen():
    result = select_entry_tick(
        _spec(),
        _ticks([(TRIGGER, 4100.00, 4100.25)]),
    )

    with pytest.raises(FrozenInstanceError):
        result.price = 1.0


def test_provider_levels_activate_only_when_observed_and_ignore_prior_touch():
    level_time = TRIGGER + timedelta(seconds=2)
    ticks = _ticks(
        [
            (TRIGGER, 99.80, 100.00),
            (TRIGGER + timedelta(seconds=1), 106.00, 106.20),
            (level_time, 100.00, 100.20),
            (TRIGGER + timedelta(seconds=3), 105.20, 105.40),
        ]
    )
    spec = _spec(
        provider_tps=(105.0,),
        provider_sl=95.0,
        level_timeline=[_level_event(level_time)],
    )

    result = simulate_provider_policy(spec, ticks, policy_by_id("no_be"))

    _assert_policy_row_shape(result)
    assert result["status"] == "simulated_price_path"
    assert result["strategy_value"] == 5.0
    assert result["blockers"] == []
    assert result["entry"] == {
        "status": "entered",
        "time_utc": TRIGGER.isoformat(),
        "price": 100.0,
        "side": "ask",
        "latency_ms": 0,
        "blockers": [],
    }
    assert result["legs"][0]["action"] == "follow_provider"
    assert result["legs"][0]["close_reason"] == "tp"
    assert result["legs"][0]["close_time_utc"] == (
        TRIGGER + timedelta(seconds=3)
    ).isoformat()
    assert result["legs"][0]["touch_side"] == "bid"


@pytest.mark.parametrize(
    ("final_bid", "expected_reason", "expected_value"),
    [
        (105.0, "tp", 5.0),
        (95.0, "sl", -5.0),
    ],
)
def test_no_be_ignores_later_be_and_remains_runner_to_tp_or_sl(
    final_bid,
    expected_reason,
    expected_value,
):
    management_time = TRIGGER + timedelta(seconds=1)
    ticks = _ticks(
        [
            (TRIGGER, 99.80, 100.00),
            (management_time, 101.00, 101.20),
            (TRIGGER + timedelta(seconds=2), 99.00, 99.20),
            (TRIGGER + timedelta(seconds=3), final_bid, final_bid + 0.20),
        ]
    )
    spec = _spec(
        provider_tps=(105.0,),
        provider_sl=95.0,
        level_timeline=[
            _level_event(TRIGGER, tps=(105.0,), sl=95.0),
            _level_event(management_time, tps=(105.0,), sl=100.0),
        ],
        management_events=[_management_event(management_time)],
    )

    result = simulate_provider_policy(spec, ticks, policy_by_id("no_be"))

    assert result["status"] == "simulated_price_path"
    assert result["strategy_value"] == expected_value
    assert result["legs"][0]["action"] == "runner"
    assert result["legs"][0]["close_reason"] == expected_reason
    assert result["legs"][0]["close_time_utc"] == (
        TRIGGER + timedelta(seconds=3)
    ).isoformat()


def test_close_2_be_1_runner_2_orders_nearest_causal_tps_first():
    targets = (120.0, 105.0, 115.0, 110.0, 125.0)
    management_time = TRIGGER + timedelta(seconds=2)
    ticks = _ticks(
        [
            (TRIGGER, 99.80, 100.00),
            (TRIGGER + timedelta(seconds=1), 101.00, 101.20),
            (management_time, 102.00, 102.20),
            (TRIGGER + timedelta(seconds=3), 100.00, 100.20),
            (TRIGGER + timedelta(seconds=4), 120.00, 120.20),
            (TRIGGER + timedelta(seconds=5), 125.00, 125.20),
        ]
    )
    spec = _spec(
        volume_per_leg=0.03,
        provider_tps=targets,
        provider_sl=90.0,
        level_timeline=[_level_event(TRIGGER, tps=targets, sl=90.0)],
        management_events=[_management_event(management_time)],
    )

    result = simulate_provider_policy(
        spec,
        ticks,
        policy_by_id("close_2_be_1_runner_2"),
    )

    assert result["status"] == "simulated_price_path"
    assert [leg["ticket"] for leg in result["legs"]] == [
        f"virtual:canal2_3200:{index}" for index in range(5)
    ]
    assert [leg["action"] for leg in result["legs"]] == [
        "runner",
        "close_now",
        "move_to_be",
        "close_now",
        "runner",
    ]
    assert [leg["close_reason"] for leg in result["legs"]] == [
        "tp",
        "management_close",
        "sl",
        "management_close",
        "tp",
    ]
    assert [leg["volume"] for leg in result["legs"]] == [0.03] * 5
    assert [leg["strategy_value"] for leg in result["legs"]] == [
        20.0,
        2.0,
        0.0,
        2.0,
        25.0,
    ]
    assert result["strategy_value"] == 49.0


def test_without_management_trigger_all_legs_follow_provider_sl_and_tp():
    targets = (105.0, 110.0, 115.0, 120.0, 125.0)
    ticks = _ticks(
        [(TRIGGER, 99.80, 100.00)]
        + [
            (
                TRIGGER + timedelta(seconds=index),
                target,
                target + 0.20,
            )
            for index, target in enumerate(targets, start=1)
        ]
    )
    spec = _spec(
        provider_tps=targets,
        provider_sl=90.0,
        level_timeline=[_level_event(TRIGGER, tps=targets, sl=90.0)],
    )

    result = simulate_provider_policy(
        spec,
        ticks,
        policy_by_id("close_2_be_1_runner_2"),
    )

    assert result["status"] == "simulated_price_path"
    assert [leg["action"] for leg in result["legs"]] == [
        "follow_provider"
    ] * 5
    assert [leg["close_reason"] for leg in result["legs"]] == ["tp"] * 5
    assert result["strategy_value"] == 75.0


def test_unexecuted_and_executed_evidence_links_simulate_identically():
    level_timeline = [_level_event(TRIGGER, tps=(105.0,), sl=95.0)]
    ticks = _ticks(
        [
            (TRIGGER, 99.80, 100.00),
            (TRIGGER + timedelta(seconds=1), 105.00, 105.20),
        ]
    )
    unexecuted = _spec(
        provider_tps=(105.0,),
        provider_sl=95.0,
        level_timeline=level_timeline,
        execution_sig_ids=(),
    )
    executed = _spec(
        provider_tps=(105.0,),
        provider_sl=95.0,
        level_timeline=deepcopy(level_timeline),
        execution_sig_ids=("sig_mt5_123", "sig_mt5_456"),
    )
    policy = policy_by_id("no_be")

    assert simulate_provider_policy(unexecuted, ticks, policy) == (
        simulate_provider_policy(executed, ticks, policy)
    )


def test_missing_tp_blocks_runner_policy_and_keeps_visible_leg():
    ticks = _ticks(
        [
            (TRIGGER, 99.80, 100.00),
            (TRIGGER + timedelta(seconds=1), 101.00, 101.20),
        ]
    )
    spec = _spec(
        provider_tps=(),
        provider_sl=95.0,
        level_timeline=[_level_event(TRIGGER, tps=(), sl=95.0)],
        policy_evidence_gaps=("missing_provider_tps",),
    )

    result = simulate_provider_policy(spec, ticks, policy_by_id("no_be"))

    _assert_policy_row_shape(result)
    assert result["status"] == "blocked"
    assert result["strategy_value"] is None
    assert len(result["legs"]) == 1
    assert result["legs"][0]["status"] == "blocked"
    assert result["blockers"] == ["missing_provider_tps"]


def test_close_only_policy_does_not_require_tp_when_sl_and_trigger_are_causal():
    management_time = TRIGGER + timedelta(seconds=1)
    ticks = _ticks(
        [
            (TRIGGER, 99.80, 100.00),
            (management_time, 101.00, 101.20),
        ]
    )
    spec = _spec(
        provider_tps=(),
        provider_sl=95.0,
        level_timeline=[_level_event(TRIGGER, tps=(), sl=95.0)],
        management_events=[_management_event(management_time)],
        policy_evidence_gaps=("missing_provider_tps",),
    )

    result = simulate_provider_policy(
        spec,
        ticks,
        policy_by_id("close_5_be_0_runner_0"),
    )

    assert result["status"] == "simulated_price_path"
    assert result["strategy_value"] == 1.0
    assert result["blockers"] == []
    assert result["legs"][0]["action"] == "close_now"
    assert result["legs"][0]["close_reason"] == "management_close"
    assert not any("tp" in blocker.lower() for blocker in result["blockers"])


def test_close_only_without_causal_sl_still_blocks():
    management_time = TRIGGER + timedelta(seconds=1)
    ticks = _ticks(
        [
            (TRIGGER, 99.80, 100.00),
            (management_time, 101.00, 101.20),
        ]
    )
    spec = _spec(
        provider_tps=(),
        provider_sl=None,
        level_timeline=[_level_event(TRIGGER, tps=(), sl=None)],
        management_events=[_management_event(management_time)],
        policy_evidence_gaps=("missing_provider_tps", "missing_provider_sl"),
    )

    result = simulate_provider_policy(
        spec,
        ticks,
        policy_by_id("close_5_be_0_runner_0"),
    )

    assert result["status"] == "blocked"
    assert result["strategy_value"] is None
    assert any("sl" in blocker.lower() for blocker in result["blockers"])


def test_blocked_entry_returns_one_complete_blocked_row():
    spec = _spec(blockers=("manual_review", "manual_review"))

    result = simulate_provider_policy(
        spec,
        _ticks([(TRIGGER, 99.80, 100.00)]),
        policy_by_id("no_be"),
    )

    _assert_policy_row_shape(result)
    assert result["provider_signal_id"] == "canal2_3200"
    assert result["channel"] == "canal2"
    assert result["policy_id"] == "no_be"
    assert result["status"] == "blocked"
    assert result["strategy_value"] is None
    assert result["blockers"] == ["manual_review"]
    assert result["entry"]["status"] == "blocked"
    assert result["legs"] == []


@pytest.mark.parametrize(
    "unsafe_quote",
    [True, "99.80", 99.80 + 1j, np.nan, np.inf, 0.0, -1.0],
    ids=["bool", "string", "complex", "nan", "inf", "zero", "negative"],
)
def test_any_unsafe_replay_quote_blocks_the_whole_row(unsafe_quote):
    ticks = _ticks(
        [
            (TRIGGER, unsafe_quote, 100.00),
            (TRIGGER + timedelta(seconds=1), 105.00, 105.20),
        ]
    )
    spec = _spec(
        provider_tps=(105.0,),
        provider_sl=95.0,
        level_timeline=[_level_event(TRIGGER)],
    )

    result = simulate_provider_policy(spec, ticks, policy_by_id("no_be"))

    _assert_policy_row_shape(result)
    assert result["status"] == "blocked"
    assert result["strategy_value"] is None
    assert result["blockers"] == ["invalid_replay_quotes"]
    assert result["entry"]["blockers"] == ["invalid_replay_quotes"]


def test_replay_requires_valid_timezone_aware_tick_times():
    ticks = _ticks([(datetime(2026, 7, 8, 11, 0), 99.80, 100.00)])

    result = simulate_provider_policy(
        _spec(),
        ticks,
        policy_by_id("no_be"),
    )

    assert result["status"] == "blocked"
    assert result["blockers"] == ["invalid_replay_tick_times"]


def test_sell_closes_on_ask_and_uses_directional_price_delta():
    ticks = _ticks(
        [
            (TRIGGER, 100.00, 100.20),
            (TRIGGER + timedelta(seconds=1), 94.80, 95.00),
        ]
    )
    spec = _spec(
        direction="SELL",
        provider_tps=(95.0,),
        provider_sl=105.0,
        level_timeline=[_level_event(TRIGGER, tps=(95.0,), sl=105.0)],
    )

    result = simulate_provider_policy(spec, ticks, policy_by_id("no_be"))

    assert result["entry"]["side"] == "bid"
    assert result["entry"]["price"] == 100.0
    assert result["legs"][0]["touch_side"] == "ask"
    assert result["legs"][0]["touch_side_price"] == 95.0
    assert result["legs"][0]["close_price"] == 95.0
    assert result["legs"][0]["strategy_value"] == 5.0
    assert result["strategy_value"] == 5.0


def _all_mapping_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _all_mapping_keys(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _all_mapping_keys(item)


def test_price_path_result_keeps_money_fields_honest_without_calibration():
    ticks = _ticks(
        [
            (TRIGGER, 99.80, 100.00),
            (TRIGGER + timedelta(seconds=1), 105.00, 105.20),
        ]
    )
    spec = _spec(
        provider_tps=(105.0,),
        provider_sl=95.0,
        level_timeline=[_level_event(TRIGGER)],
        volume_per_leg=9.99,
    )

    result = simulate_provider_policy(spec, ticks, policy_by_id("no_be"))

    _assert_policy_row_shape(result)
    assert result["strategy_value"] == 5.0
    assert result["strategy_pnl"] is None
    assert result["money_status"] == "unverified"
    keys = set(_all_mapping_keys(result))
    assert "actual_pnl" not in keys
    assert "unit_value" not in keys
    assert "pnl_source" not in keys
    assert not any("calibrat" in key.lower() for key in keys)


def test_management_trigger_before_entry_blocks_without_partial_ranking():
    management_time = TRIGGER - timedelta(seconds=1)
    spec = _spec(
        provider_tps=(105.0,),
        provider_sl=95.0,
        level_timeline=[
            _level_event(TRIGGER - timedelta(seconds=2), tps=(105.0,), sl=95.0)
        ],
        management_events=[_management_event(management_time)],
    )

    result = simulate_provider_policy(
        spec,
        _ticks([(TRIGGER, 99.80, 100.00)]),
        policy_by_id("no_be"),
    )

    assert result["status"] == "blocked"
    assert result["strategy_value"] is None
    assert result["blockers"] == ["management_trigger_before_entry"]
    assert len(result["legs"]) == 1
    assert result["legs"][0]["ticket"] == "virtual:canal2_3200:0"


def test_follow_actual_is_unsupported_for_virtual_provider_trade():
    result = simulate_provider_policy(
        _spec(),
        _ticks([(TRIGGER, 99.80, 100.00)]),
        policy_by_id("follow_actual"),
    )

    _assert_policy_row_shape(result)
    assert result["status"] == "blocked"
    assert result["strategy_value"] is None
    assert result["blockers"] == [
        "unsupported_virtual_policy:follow_actual"
    ]
    assert result["entry"]["status"] == "entered"


def test_eod_horizon_is_taken_from_policy_and_recorded_as_assumption():
    final_tick = TRIGGER.replace(hour=22)
    ticks = _ticks(
        [
            (TRIGGER, 99.80, 100.00),
            (final_tick, 103.00, 103.20),
        ]
    )
    spec = _spec(
        provider_tps=(110.0,),
        provider_sl=90.0,
        level_timeline=[_level_event(TRIGGER, tps=(110.0,), sl=90.0)],
    )

    result = simulate_provider_policy(spec, ticks, policy_by_id("no_be"))

    assert result["status"] == "simulated_price_path"
    assert result["legs"][0]["close_reason"] == "horizon_close"
    assert result["legs"][0]["close_time_utc"] == final_tick.isoformat()
    assert result["legs"][0]["assumptions"] == ["horizon_close:eod"]
    assert result["assumptions"] == ["horizon_close:eod"]


def test_policy_replay_does_not_mutate_inputs_and_is_deterministic():
    level_timeline = [_level_event(TRIGGER, tps=(105.0,), sl=95.0)]
    spec = _spec(
        provider_tps=(105.0,),
        provider_sl=95.0,
        level_timeline=level_timeline,
    )
    ticks = _ticks(
        [
            (TRIGGER + timedelta(seconds=1), 105.50, 105.70),
            (TRIGGER, 99.80, 100.00),
            (TRIGGER + timedelta(seconds=1), 106.00, 106.20),
        ]
    )
    ticks.index = pd.Index([8, 3, 5], name="source_row")
    ticks.attrs["cache_contract"] = {"version": 3}
    original_spec = deepcopy(spec)
    original_ticks = ticks.copy(deep=True)
    policy = policy_by_id("no_be")

    first = simulate_provider_policy(spec, ticks, policy)
    second = simulate_provider_policy(spec, ticks, policy)

    assert first == second
    assert first["legs"][0]["touch_side_price"] == 105.5
    assert spec == original_spec
    assert_frame_equal(ticks, original_ticks)
    assert ticks.attrs == original_ticks.attrs


@pytest.mark.parametrize(
    "gap",
    [
        "invalid_level_timeline_observed_ts:2",
        "invalid_management_event_observed_ts:1",
    ],
)
def test_invalid_causal_evidence_gap_blocks_exactly_and_keeps_legs(gap):
    ticks = _ticks(
        [
            (TRIGGER, 99.80, 100.00),
            (TRIGGER + timedelta(seconds=1), 105.00, 105.20),
        ]
    )
    spec = _spec(
        provider_tps=(105.0,),
        provider_sl=95.0,
        level_timeline=[_level_event(TRIGGER, tps=(105.0,), sl=95.0)],
        policy_evidence_gaps=(gap,),
    )

    result = simulate_provider_policy(spec, ticks, policy_by_id("no_be"))

    assert result["entry"]["status"] == "entered"
    assert result["status"] == "blocked"
    assert result["strategy_value"] is None
    assert result["blockers"] == [gap]
    assert len(result["legs"]) == 1
    assert result["legs"][0]["status"] == "blocked"
    assert result["legs"][0]["blockers"] == [gap]


def test_invalid_tp_gap_is_ignored_only_for_triggered_close_only_policy():
    management_time = TRIGGER + timedelta(seconds=1)
    ticks = _ticks(
        [
            (TRIGGER, 99.80, 100.00),
            (management_time, 101.00, 101.20),
        ]
    )
    spec = _spec(
        provider_tps=(),
        provider_sl=95.0,
        level_timeline=[_level_event(TRIGGER, tps=(), sl=95.0)],
        management_events=[_management_event(management_time)],
        policy_evidence_gaps=(
            "invalid_provider_tp:0",
            "missing_provider_tps",
        ),
    )

    result = simulate_provider_policy(
        spec,
        ticks,
        policy_by_id("close_5_be_0_runner_0"),
    )

    assert result["status"] == "simulated_price_path"
    assert result["strategy_value"] == 1.0
    assert result["blockers"] == []


def test_invalid_sl_gap_still_blocks_close_only_policy_contextually():
    management_time = TRIGGER + timedelta(seconds=1)
    ticks = _ticks(
        [
            (TRIGGER, 99.80, 100.00),
            (management_time, 101.00, 101.20),
        ]
    )
    spec = _spec(
        provider_tps=(),
        provider_sl=95.0,
        level_timeline=[_level_event(TRIGGER, tps=(), sl=95.0)],
        management_events=[_management_event(management_time)],
        policy_evidence_gaps=("invalid_provider_sl",),
    )

    result = simulate_provider_policy(
        spec,
        ticks,
        policy_by_id("close_5_be_0_runner_0"),
    )

    assert result["status"] == "blocked"
    assert result["strategy_value"] is None
    assert result["blockers"] == ["invalid_provider_sl"]
    assert len(result["legs"]) == 1


def test_tp_and_sl_before_management_need_no_post_trigger_tick():
    management_time = TRIGGER + timedelta(seconds=3)
    ticks = _ticks(
        [
            (TRIGGER, 99.80, 100.00),
            (TRIGGER + timedelta(seconds=1), 102.00, 102.20),
            (TRIGGER + timedelta(seconds=2), 95.00, 95.20),
        ]
    )
    targets = (102.0, 110.0)
    spec = _spec(
        provider_tps=targets,
        provider_sl=95.0,
        level_timeline=[_level_event(TRIGGER, tps=targets, sl=95.0)],
        management_events=[_management_event(management_time)],
    )

    result = simulate_provider_policy(
        spec,
        ticks,
        policy_by_id("close_2_be_1_runner_2"),
    )

    assert result["status"] == "simulated_price_path"
    assert result["strategy_value"] == -3.0
    assert result["blockers"] == []
    assert [leg["action"] for leg in result["legs"]] == [
        "closed_before_management",
        "closed_before_management",
    ]
    assert [leg["close_reason"] for leg in result["legs"]] == ["tp", "sl"]
    assert not any(
        "missing_ticks_after_management" in blocker
        for blocker in result["blockers"]
    )


def _three_survivor_repro():
    management_time = TRIGGER + timedelta(seconds=3)
    targets = (102.0, 104.0, 110.0, 130.0, 140.0)
    ticks = _ticks(
        [
            (TRIGGER, 99.80, 100.00),
            (TRIGGER + timedelta(seconds=1), 102.00, 102.20),
            (TRIGGER + timedelta(seconds=2), 104.00, 104.20),
            (management_time, 106.00, 106.20),
            (TRIGGER + timedelta(seconds=4), 100.00, 100.20),
            (TRIGGER + timedelta(seconds=5), 140.00, 140.20),
        ]
    )
    spec = _spec(
        provider_tps=targets,
        provider_sl=90.0,
        level_timeline=[_level_event(TRIGGER, tps=targets, sl=90.0)],
        management_events=[_management_event(management_time)],
    )
    return spec, ticks


def test_preclosed_tp1_tp2_leave_three_survivors_with_original_tp_indexes():
    spec, ticks = _three_survivor_repro()

    result = simulate_provider_policy(
        spec,
        ticks,
        policy_by_id("close_2_be_1_runner_2"),
    )

    assert result["status"] == "simulated_price_path"
    assert [leg["action"] for leg in result["legs"]] == [
        "closed_before_management",
        "closed_before_management",
        "close_now",
        "move_to_be",
        "runner",
    ]
    assert [leg["strategy_value"] for leg in result["legs"]] == [
        2.0,
        4.0,
        6.0,
        0.0,
        40.0,
    ]
    assert result["strategy_value"] == 52.0
    assert result["blockers"] == []
    assert all(
        not any("no_touch_before_horizon" in item for item in leg["blockers"])
        for leg in result["legs"]
    )


def test_all_legs_closed_before_trigger_produce_valid_result_without_later_tick():
    management_time = TRIGGER + timedelta(seconds=4)
    targets = (102.0, 104.0, 106.0)
    ticks = _ticks(
        [
            (TRIGGER, 99.80, 100.00),
            (TRIGGER + timedelta(seconds=1), 102.00, 102.20),
            (TRIGGER + timedelta(seconds=2), 104.00, 104.20),
            (TRIGGER + timedelta(seconds=3), 106.00, 106.20),
        ]
    )
    spec = _spec(
        provider_tps=targets,
        provider_sl=90.0,
        level_timeline=[_level_event(TRIGGER, tps=targets, sl=90.0)],
        management_events=[_management_event(management_time)],
    )

    result = simulate_provider_policy(
        spec,
        ticks,
        policy_by_id("close_2_be_1_runner_2"),
    )

    assert result["status"] == "simulated_price_path"
    assert result["strategy_value"] == 12.0
    assert [leg["action"] for leg in result["legs"]] == [
        "closed_before_management"
    ] * 3
    assert result["blockers"] == []


def test_sl_touch_before_observation_is_ignored_then_applies_after_activation():
    level_time = TRIGGER + timedelta(seconds=2)
    close_time = TRIGGER + timedelta(seconds=3)
    ticks = _ticks(
        [
            (TRIGGER, 99.80, 100.00),
            (TRIGGER + timedelta(seconds=1), 94.00, 94.20),
            (level_time, 100.00, 100.20),
            (close_time, 95.00, 95.20),
        ]
    )
    spec = _spec(
        provider_tps=(110.0,),
        provider_sl=95.0,
        level_timeline=[_level_event(level_time, tps=(110.0,), sl=95.0)],
    )

    result = simulate_provider_policy(spec, ticks, policy_by_id("no_be"))

    assert result["status"] == "simulated_price_path"
    assert result["legs"][0]["close_reason"] == "sl"
    assert result["legs"][0]["close_time_utc"] == close_time.isoformat()
    assert result["legs"][0]["strategy_value"] == -5.0


def test_complete_policy_catalog_returns_22_honest_rows_without_drop():
    management_time = TRIGGER + timedelta(seconds=1)
    targets = (110.0, 115.0, 120.0, 125.0, 130.0)
    ticks = _ticks(
        [
            (TRIGGER, 99.80, 100.00),
            (management_time, 101.00, 101.20),
            (TRIGGER + timedelta(seconds=2), 100.00, 100.20),
            (TRIGGER + timedelta(seconds=3), 130.00, 130.20),
        ]
    )
    spec = _spec(
        provider_tps=targets,
        provider_sl=90.0,
        level_timeline=[_level_event(TRIGGER, tps=targets, sl=90.0)],
        management_events=[_management_event(management_time)],
    )
    policies = default_policy_catalog()

    results = [
        simulate_provider_policy(spec, ticks, policy)
        for policy in policies
    ]

    assert len(policies) == 22
    assert len(results) == 22
    assert [row["policy_id"] for row in results] == [
        policy.policy_id for policy in policies
    ]
    assert len({row["policy_id"] for row in results}) == 22
    for row in results:
        _assert_policy_row_shape(row)
        assert row["strategy_pnl"] is None
        assert row["money_status"] == "unverified"
        assert "actual_pnl" not in set(_all_mapping_keys(row))


def test_survivor_classification_is_deterministic_and_does_not_mutate_inputs():
    spec, ticks = _three_survivor_repro()
    ticks = ticks.iloc[[5, 0, 2, 1, 4, 3]].copy()
    ticks.index = pd.Index([9, 2, 8, 4, 7, 5], name="source_row")
    ticks.attrs["cache_contract"] = {"version": 3}
    original_spec = deepcopy(spec)
    original_ticks = ticks.copy(deep=True)
    policy = policy_by_id("close_2_be_1_runner_2")

    first = simulate_provider_policy(spec, ticks, policy)
    second = simulate_provider_policy(spec, ticks, policy)

    assert first == second
    assert first["strategy_value"] == 52.0
    assert spec == original_spec
    assert_frame_equal(ticks, original_ticks)
    assert ticks.attrs == original_ticks.attrs


def test_prepared_replay_ticks_are_validated_once_and_reusable():
    ticks = _ticks([
        (TRIGGER + timedelta(seconds=1), 101.0, 101.2),
        (TRIGGER, 99.8, 100.0),
    ])
    original = ticks.copy(deep=True)

    prepared, blocker = prepare_replay_ticks(ticks)
    reused, reused_blocker = prepare_replay_ticks(prepared)

    assert blocker is None
    assert reused_blocker is None
    assert reused is prepared
    assert prepared.attrs["provider_replay_ticks_contract"] == (
        "strict_bid_ask_utc_v1"
    )
    assert prepared["time_utc"].is_monotonic_increasing
    assert "_time_ns" in prepared.columns
    assert_frame_equal(ticks, original)
    assert "_time_ns" not in ticks.columns


def test_shared_result_cache_reuses_identical_leg_price_paths(monkeypatch):
    ticks = _ticks([
        (TRIGGER, 99.8, 100.0),
        (TRIGGER + timedelta(seconds=1), 105.0, 105.2),
    ])
    spec = _spec(
        provider_tps=(105.0,),
        provider_sl=95.0,
        level_timeline=[_level_event(TRIGGER, tps=(105.0,), sl=95.0)],
    )
    prepared, blocker = prepare_replay_ticks(ticks)
    assert blocker is None
    calls = []
    original = provider_simulator._simulate_virtual_leg

    def counted(*args, **kwargs):
        calls.append((args[2], kwargs["action"]))
        return original(*args, **kwargs)

    monkeypatch.setattr(provider_simulator, "_simulate_virtual_leg", counted)
    cache = {}

    first = simulate_provider_policy(
        spec,
        prepared,
        policy_by_id("no_be"),
        result_cache=cache,
    )
    second = simulate_provider_policy(
        spec,
        prepared,
        policy_by_id("close_0_be_1_runner_4"),
        result_cache=cache,
    )

    assert first["strategy_value"] == second["strategy_value"] == 5.0
    assert first["policy_id"] == "no_be"
    assert second["policy_id"] == "close_0_be_1_runner_4"
    assert calls == [(0, "follow_provider")]
