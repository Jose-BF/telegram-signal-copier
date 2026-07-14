from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from provider_strategy_simulator import VirtualEntry, select_entry_tick
from provider_trade_spec import ProviderTradeSpec


TRIGGER = datetime(2026, 7, 8, 11, 0, tzinfo=timezone.utc)


def _spec(
    *,
    direction="BUY",
    trigger=TRIGGER,
    latency_ms=0,
    blockers=(),
):
    return ProviderTradeSpec(
        provider_signal_id="canal2_3200",
        channel="canal2",
        direction=direction,
        trigger_observed_utc=trigger,
        latency_ms=latency_ms,
        volume_per_leg=0.01,
        leg_count=1,
        provider_tps=(4110.0,),
        provider_sl=4090.0,
        level_timeline=(),
        management_events=(),
        execution_sig_ids=(),
        entry_blockers=tuple(blockers),
        policy_evidence_gaps=(),
    )


def _ticks(rows):
    return pd.DataFrame(rows, columns=["time_utc", "bid", "ask"])


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


def test_unsorted_duplicate_timestamps_keep_stable_source_order():
    duplicate_time = TRIGGER + timedelta(milliseconds=100)
    ticks = _ticks(
        [
            (TRIGGER + timedelta(milliseconds=200), 4102.00, 4102.25),
            (duplicate_time, 4100.00, 4100.25),
            (TRIGGER - timedelta(milliseconds=1), 4099.00, 4099.25),
            (duplicate_time, 4101.00, 4101.25),
        ]
    )

    result = select_entry_tick(_spec(latency_ms=100), ticks)

    assert result.time_utc == duplicate_time
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


def test_invalid_time_rows_are_ignored_when_a_valid_entry_tick_exists():
    ticks = _ticks(
        [
            ("invalid", 4000.00, 4000.25),
            (TRIGGER, 4100.00, 4100.25),
        ]
    )

    result = select_entry_tick(_spec(), ticks)

    assert result.status == "entered"
    assert result.price == 4100.25
    assert result.blockers == ()


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
