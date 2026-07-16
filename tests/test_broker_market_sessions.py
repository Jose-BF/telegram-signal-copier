import pandas as pd

from broker_market_sessions import (
    MARKET_SESSION_CONTRACT,
    filter_tradable_ticks,
)


def _ticks(*values):
    return pd.DataFrame({
        "time_utc": pd.to_datetime(values, utc=True, format="mixed"),
        "bid": range(len(values)),
        "ask": range(len(values)),
    })


def test_vantage_xauusd_filters_quote_only_ticks_at_weekly_reopen():
    ticks = _ticks(
        "2026-07-12T22:00:00+00:00",
        "2026-07-12T22:00:59.999+00:00",
        "2026-07-12T22:01:00+00:00",
        "2026-07-12T22:01:00.001+00:00",
    )

    filtered, removed = filter_tradable_ticks(
        ticks,
        utc_offset_seconds=10_800,
    )

    assert MARKET_SESSION_CONTRACT == "vantage_xauusd_standard_v1"
    assert removed == 2
    assert filtered["time_utc"].dt.strftime(
        "%Y-%m-%dT%H:%M:%S.%f%z"
    ).tolist() == [
        "2026-07-12T22:01:00.000000+0000",
        "2026-07-12T22:01:00.001000+0000",
    ]


def test_vantage_xauusd_uses_distinct_weekday_and_friday_closes():
    ticks = _ticks(
        "2026-07-06T20:57:59.999+00:00",  # Monday 23:57:59.999 server
        "2026-07-06T20:58:00+00:00",      # Monday 23:58 server
        "2026-07-10T20:56:59.999+00:00",  # Friday 23:56:59.999 server
        "2026-07-10T20:57:00+00:00",      # Friday 23:57 server
        "2026-07-11T10:00:00+00:00",      # Saturday
    )

    filtered, removed = filter_tradable_ticks(
        ticks,
        utc_offset_seconds=10_800,
    )

    assert removed == 3
    assert filtered.index.tolist() == [0, 2]


def test_filter_keeps_input_unchanged_and_rejects_missing_offset():
    ticks = _ticks("2026-07-06T10:00:00+00:00")
    original = ticks.copy(deep=True)

    try:
        filter_tradable_ticks(ticks, utc_offset_seconds=None)
    except ValueError as exc:
        assert str(exc) == "missing broker UTC offset evidence"
    else:
        raise AssertionError("missing UTC offset must fail closed")

    pd.testing.assert_frame_equal(ticks, original)
