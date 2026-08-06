from copy import deepcopy
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from provider_zone_simulator import simulate_zone_policy
from provider_zone_spec import ProviderZoneSpec, ZoneState
from zone_entry_policies import zone_policy_by_id


BASE = datetime(2026, 8, 4, 10, tzinfo=timezone.utc)


def t(seconds: float) -> datetime:
    return BASE + timedelta(seconds=seconds)


def ticks(rows) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["time_utc", "bid", "ask"])


def state(
    at,
    *,
    zone,
    direction="BUY",
    tps=(110.0, 115.0, 120.0, 125.0, 130.0),
    sl=95.0,
) -> ZoneState:
    return ZoneState(
        observed_utc=at,
        direction=direction,
        zone=tuple(zone),
        tps=tuple(tps),
        sl=sl,
    )


def management(at, action, text=""):
    return {
        "observed_ts_utc": at.isoformat(),
        "classified_action": action,
        "text": text,
    }


def buy_spec(*, zone, later_states=(), management_events=()):
    states = (state(BASE, zone=zone), *later_states)
    return ProviderZoneSpec(
        provider_signal_id="canal2_9000",
        channel="canal2",
        ready_at_utc=BASE,
        ready_states=states,
        management_events=tuple(management_events),
        execution_batches=(),
        blockers=(),
        warnings=(),
        source_sha256="0" * 64,
    )


def sell_spec(*, zone, later_states=(), management_events=()):
    states = (
        state(
            BASE,
            zone=zone,
            direction="SELL",
            tps=(95.0, 90.0, 85.0, 80.0, 75.0),
            sl=110.0,
        ),
        *later_states,
    )
    return ProviderZoneSpec(
        provider_signal_id="canal2_9001",
        channel="canal2",
        ready_at_utc=BASE,
        ready_states=states,
        management_events=tuple(management_events),
        execution_batches=(),
        blockers=(),
        warnings=(),
        source_sha256="1" * 64,
    )


def test_buy_limits_fill_from_ask_at_declared_depths():
    frame = ticks([
        (t(0), 105.1, 105.3),
        (t(1), 104.7, 104.9),
        (t(2), 103.5, 103.7),
        (t(3), 102.2, 102.4),
        (t(4), 101.0, 101.2),
        (t(5), 99.7, 99.9),
    ])

    result = simulate_zone_policy(
        buy_spec(zone=(100, 105)),
        frame,
        zone_policy_by_id("five_equal_limits"),
        horizon_at=t(5),
    )

    assert result["status"] == "filled"
    assert [leg["depth_fraction"] for leg in result["filled_legs"]] == [
        0.0,
        0.25,
        0.5,
        0.75,
        1.0,
    ]
    assert [leg["open_price"] for leg in result["filled_legs"]] == [
        105.0,
        103.75,
        102.5,
        101.25,
        100.0,
    ]
    assert all(leg["touch_side"] == "ask" for leg in result["filled_legs"])
    assert result["zone_diagnostics"]["maximum_penetration_pct"] == 100.0


def test_sell_limits_fill_from_bid():
    frame = ticks([
        (t(0), 99.8, 100.0),
        (t(1), 100.1, 100.3),
        (t(2), 101.3, 101.5),
        (t(3), 102.6, 102.8),
        (t(4), 103.8, 104.0),
        (t(5), 105.0, 105.2),
    ])

    result = simulate_zone_policy(
        sell_spec(zone=(100, 105)),
        frame,
        zone_policy_by_id("five_equal_limits"),
        horizon_at=t(5),
    )

    assert [leg["planned_level"] for leg in result["filled_legs"]] == [
        100.0,
        101.25,
        102.5,
        103.75,
        105.0,
    ]
    assert all(leg["touch_side"] == "bid" for leg in result["filled_legs"])


def test_market_legs_use_first_touch_then_declared_spacing():
    frame = ticks([
        (t(0), 105.2, 105.4),
        (t(1), 104.8, 105.0),
        (t(1.125), 104.7, 104.9),
        (t(1.250), 104.6, 104.8),
        (t(1.375), 104.5, 104.7),
        (t(1.500), 104.4, 104.6),
    ])

    result = simulate_zone_policy(
        buy_spec(zone=(100, 105)),
        frame,
        zone_policy_by_id("all_first_touch_live"),
        horizon_at=t(2),
    )

    assert [leg["open_time_utc"] for leg in result["filled_legs"]] == [
        t(1).isoformat(),
        t(1.125).isoformat(),
        t(1.250).isoformat(),
        t(1.375).isoformat(),
        t(1.500).isoformat(),
    ]
    assert [leg["open_price"] for leg in result["filled_legs"]] == [
        105.0,
        104.9,
        104.8,
        104.7,
        104.6,
    ]


def test_provider_progress_cancels_only_unfilled_future_entries():
    spec = buy_spec(
        zone=(100, 105),
        management_events=[management(t(2), "PROGRESS_UPDATE")],
    )
    frame = ticks([
        (t(0), 105.2, 105.4),
        (t(1), 104.8, 105.0),
        (t(3), 99.8, 100.0),
    ])

    result = simulate_zone_policy(
        spec,
        frame,
        zone_policy_by_id("one_plus_four_equal"),
        horizon_at=t(4),
    )

    assert result["fill_cutoff_reason"] == "provider_progress"
    assert result["fill_cutoff_utc"] == t(2).isoformat()
    assert result["filled_leg_count"] == 1
    assert len(result["unfilled_legs"]) == 4


def test_live_baseline_keeps_waiting_after_provider_progress():
    spec = buy_spec(
        zone=(100, 105),
        management_events=[management(t(2), "PROGRESS_UPDATE")],
    )
    frame = ticks([
        (t(0), 105.2, 105.4),
        (t(3), 104.8, 105.0),
        (t(3.125), 104.7, 104.9),
        (t(3.250), 104.6, 104.8),
        (t(3.375), 104.5, 104.7),
        (t(3.500), 104.4, 104.6),
    ])

    result = simulate_zone_policy(
        spec,
        frame,
        zone_policy_by_id("all_first_touch_live"),
        horizon_at=t(4),
    )

    assert result["fill_cutoff_reason"] == "session_end"
    assert result["filled_leg_count"] == 5


def test_unfilled_levels_reprice_after_causal_zone_revision():
    revised = state(t(2), zone=(98, 103), tps=(110,), sl=95)
    frame = ticks([
        (t(0), 105.3, 105.5),
        (t(1), 105.1, 105.3),
        (t(3), 104.0, 104.2),
    ])

    result = simulate_zone_policy(
        buy_spec(zone=(100, 105), later_states=[revised]),
        frame,
        zone_policy_by_id("five_equal_limits"),
        horizon_at=t(4),
    )

    assert result["filled_leg_count"] == 0
    assert [leg["planned_level"] for leg in result["unfilled_legs"]] == [
        103.0,
        101.75,
        100.5,
        99.25,
        98.0,
    ]


def test_zero_width_zone_blocks_layered_depth_policy():
    result = simulate_zone_policy(
        buy_spec(zone=(100, 100)),
        ticks([(t(0), 99.8, 100.0)]),
        zone_policy_by_id("five_equal_limits"),
        horizon_at=t(1),
    )

    assert result["status"] == "blocked"
    assert result["blockers"] == ["zero_width_zone_for_layered_policy"]


def test_invalid_tick_quote_blocks_the_whole_row():
    result = simulate_zone_policy(
        buy_spec(zone=(100, 105)),
        ticks([(t(0), 100.0, np.nan)]),
        zone_policy_by_id("one_first_touch"),
        horizon_at=t(1),
    )

    assert result["status"] == "blocked"
    assert result["blockers"] == ["invalid_quote:0"]


def test_simulation_is_deterministic_and_does_not_mutate_inputs():
    spec = buy_spec(zone=(100, 105))
    frame = ticks([
        (t(1), 104.8, 105.0),
        (t(0), 105.2, 105.4),
    ])
    frame.index = pd.Index([8, 3], name="source_row")
    original_spec = deepcopy(spec)
    original_frame = frame.copy(deep=True)
    policy = zone_policy_by_id("one_first_touch")

    first = simulate_zone_policy(spec, frame, policy, horizon_at=t(2))
    second = simulate_zone_policy(spec, frame, policy, horizon_at=t(2))

    assert first == second
    assert spec == original_spec
    assert_frame_equal(frame, original_frame)
