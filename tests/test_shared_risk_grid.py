from dataclasses import replace
from decimal import Decimal
from itertools import product

import pytest

from research.dubai_iterative.engine import ExecutionAssumptions
from research.dubai_iterative.client import ClientProfile
from research.dubai_iterative.shared_replay import simulate_shared
from research.dubai_iterative.shared_replay import SharedRiskFrame, SharedRiskPoint
from research.shared_risk_reducer import SharedRiskOnlineReducer
from tests.test_iterative_market import market
from tests.test_iterative_protection import path, policy, profile
from tests.test_shared_policy_replay import PROFILE, spec, contested_management


def gap_spec():
    return spec("canal1_gap", [100., 100., 100., 105., 105.], execution=ExecutionAssumptions(
        protection=profile(), market=market(entry_acknowledgement_delay_ms=0), client=ClientProfile()))


def summary(report):
    from research.dubai_iterative.shared_replay import summarize_shared_risk
    return summarize_shared_risk(report)


def test_post_event_gap_has_origin_zero_and_does_not_use_pre_hit_peak():
    report = simulate_shared([gap_spec()], profile=PROFILE)
    grid = report.risk_grid
    assert [frame.tick_index for frame in grid] == list(range(5))
    assert [frame.states[0][2].equity_minor for frame in grid] == [-80, -80, -80, 200, 200]
    assert grid[-1].states[0][2].tick_index == 3  # Confirmed flat state carried.
    assert max(point.equity_minor for point in report.risk) == 1920
    result = summary(report)
    assert result["metrics"]["max_drawdown"] == 80
    assert result["metrics"]["maximum_from_origin"] == 200
    assert result["metrics"]["final_net"] == 200
    assert result["native_parity_verified"] is False
    assert result["grid_contract"] == "post_event_quote_grid_v1"


def test_streamed_shared_risk_preserves_every_point_and_frame_without_retention():
    class Recorder:
        def __init__(self):
            self.points = []
            self.frames = []

        def record_point(self, point):
            self.points.append(point)

        def record_frame(self, frame):
            self.frames.append(frame)

    item = gap_spec()
    retained = simulate_shared([item], profile=PROFILE)
    recorder = Recorder()
    streamed = simulate_shared([item], profile=PROFILE, risk_sink=recorder,
                               retain_risk=False)

    assert streamed.baskets == retained.baskets
    assert streamed.blockers == retained.blockers
    assert streamed.transport == retained.transport
    assert streamed.risk == streamed.risk_grid == ()
    assert streamed.risk_point_count == len(retained.risk)
    assert streamed.risk_grid_count == len(retained.risk_grid)
    assert tuple(recorder.points) == retained.risk
    assert tuple(recorder.frames) == retained.risk_grid
    with pytest.raises(ValueError, match="streamed risk"):
        summary(streamed)


@pytest.mark.parametrize("unsupported", [False, True])
def test_online_reducer_matches_retained_exact_joint_drawdown(unsupported):
    item = gap_spec()
    specs = [item]
    if unsupported:
        specs.append(replace(item, channel="canal2",
                             path=replace(item.path, signal_id="canal2_unknown"),
                             genome=item.genome.with_change(entry_mode="actual_mt5")))
    retained = simulate_shared(specs, profile=PROFILE)
    online = SharedRiskOnlineReducer()
    streamed = simulate_shared(specs, profile=PROFILE, risk_sink=online,
                               retain_risk=False)

    expected = summary(retained)
    actual = online.finalize(streamed)
    for key in ("expected_scopes", "metrics", "known_sample_metrics",
                "final_total_minor", "blockers"):
        assert actual[key] == expected[key]
    assert actual["sample_count"] == len(expected["samples"])
    assert actual["samples_retained"] is False


def test_online_reducer_rejects_backward_frame_after_a_gap():
    reducer = SharedRiskOnlineReducer()
    point = SharedRiskPoint("canal1", "canal1_gap", 5, 5, 0, 0, 0, (), "settled")
    reducer.record_point(point)
    reducer.record_frame(SharedRiskFrame(5, 5, (("canal1", "canal1_gap", point),)))
    with pytest.raises(ValueError, match="chronology"):
        reducer.record_frame(SharedRiskFrame(4, 6, (("canal1", "canal1_gap", point),)))


def test_online_reducer_rejects_resumed_scope_after_carried_flat_state():
    reducer = SharedRiskOnlineReducer()
    flat = SharedRiskPoint("canal1", "canal1_gap", 0, 0, 0, 100, 0, (), "settled")
    reducer.record_point(flat)
    reducer.record_frame(SharedRiskFrame(0, 0, (("canal1", "canal1_gap", flat),)))
    reducer.record_frame(SharedRiskFrame(1, 1, (("canal1", "canal1_gap", flat),)))
    resumed = SharedRiskPoint("canal1", "canal1_gap", 2, 2, 0, 100, -10,
                              (("sim_1", 0.01, 100.0),), "settled")
    with pytest.raises(ValueError, match="carried flat"):
        reducer.record_point(resumed)


@pytest.mark.parametrize("reverse", [False, True])
def test_compensated_baskets_do_not_create_a_peak_from_mixed_quote_ages(reverse):
    tape = path([100., 110., 90., 100.])
    strategy = policy(target_mode="none", target_steps=(), trailing_distance=None, stop_mode="none")
    execution = ExecutionAssumptions(protection=profile(), market=market(entry_acknowledgement_delay_ms=0), client=ClientProfile())
    buy = spec("canal1_buy", [], tape=tape, strategy=strategy, execution=execution)
    sell = spec("canal2_sell", [], tape=replace(tape, direction="SELL", exit_quotes=tape.ask), strategy=strategy, execution=execution)
    report = simulate_shared([sell, buy] if reverse else [buy, sell], profile=PROFILE)
    assert len(report.risk_grid) == 4
    for frame in report.risk_grid:
        assert all(point.tick_index == frame.tick_index for _, _, point in frame.states)
        assert sum(point.equity_minor for _, _, point in frame.states) == -160
    result = summary(report)
    assert result["known_sample_metrics"]["maximum_from_origin"] == 0
    assert result["known_sample_metrics"]["max_drawdown"] == 160
    assert result["metrics"] is None  # Both baskets are still open at cutoff.


def test_same_timestamp_ordinals_are_retained():
    item = gap_spec()
    times = item.path.times_ns.copy()
    times[2] = times[1]
    report = simulate_shared([replace(item, path=replace(item.path, times_ns=times))], profile=PROFILE)
    assert [frame.tick_index for frame in report.risk_grid] == list(range(5))
    assert report.risk_grid[1].time_ns == report.risk_grid[2].time_ns


def test_interrupted_quote_has_no_fabricated_final_barrier():
    item = gap_spec()
    report = simulate_shared([item], profile=replace(PROFILE, max_events=1))
    assert report.risk_grid == ()
    assert summary(report)["metrics"] is None
    assert summary(report)["expected_scopes"] == [("canal1", "canal1_gap")]


def test_flat_tail_is_bounded_before_allocating_scope_references():
    item = gap_spec()
    tape = path([100., 100., 100., 105.] + [105.] * 300)
    report = simulate_shared([replace(item, path=replace(tape, signal_id=item.path.signal_id))],
                             profile=replace(PROFILE, max_events=20))
    assert len(report.risk_grid) == 20
    assert "shared_risk_grid_budget_exhausted" in report.blockers
    assert summary(report)["metrics"] is None


def test_unsupported_basket_stays_unknown_not_zero():
    item = gap_spec()
    unsupported = replace(item, path=replace(item.path, signal_id="canal2_missing"), channel="canal2",
                          genome=item.genome.with_change(entry_mode="actual_mt5"))
    report = simulate_shared([item, unsupported], profile=PROFILE)
    assert len(report.baskets) == 2
    assert report.risk_grid
    assert all(frame.states[1][2] is None for frame in report.risk_grid)
    result = summary(report)
    assert result["metrics"] is result["known_sample_metrics"] is None
    assert all(row["total"] is None for row in result["samples"])
    assert any(row["contributions"][0]["total"] is not None for row in result["samples"])


def synthetic_report(curves):
    from research.dubai_iterative.shared_replay import SharedRiskFrame, SharedRiskPoint
    report = simulate_shared(contested_management(), profile=PROFILE)
    scopes = [(channel, identity) for channel, identity, _ in report.baskets]
    frames = []
    for index, values in enumerate(zip(*curves)):
        states = tuple((channel, identity, SharedRiskPoint(channel, identity, index, index, 0,
                        value, 0, (), "settled")) for (channel, identity), value in zip(scopes, values))
        frames.append(SharedRiskFrame(index, index, states))
    return replace(report, risk_grid=tuple(frames), expected_quote_count=len(frames),
                   risk=tuple(point for frame in frames for _, _, point in frame.states))


def test_joint_minimum_is_not_sum_of_individual_minima():
    result = summary(synthetic_report([[0, -1000, 0], [0, 0, -2000]]))
    assert result["metrics"]["minimum_from_origin"] == -2000
    assert result["metrics"]["max_drawdown"] == 2000


def test_unknown_component_blocks_complete_metrics_without_erasing_known_contribution():
    result = summary(synthetic_report([[0, None, 100], [0, 900, 200]]))
    assert result["metrics"] is None
    assert result["samples"][1]["total"] is None
    assert result["samples"][1]["contributions"][1]["total"] == 900
    assert result["known_sample_metrics"]["known_samples"] == 2


def test_known_prefix_final_is_not_misreported_as_final_after_unknown_tail():
    result = summary(synthetic_report([[0, 100, None], [0, 200, 300]]))
    assert result["metrics"] is None
    assert result["final_total_minor"] is None
    assert result["known_sample_metrics"]["final_net"] == 300
    assert result["known_sample_metrics"]["final_tick_index"] == 1


def test_cost_and_partial_changes_are_not_counted_again_in_totals():
    from research.dubai_iterative.shared_replay import SharedRiskFrame
    report = synthetic_report([[-20, 180, 180], [0, -10, -10]])
    frames = []
    for frame in report.risk_grid:
        channel, identity, point = frame.states[0]
        point = replace(point, floating_minor=100 if frame.tick_index == 1 else 0,
                        positions=(("sim_1", .02, 100.),) if frame.tick_index == 1 else ())
        frames.append(SharedRiskFrame(frame.tick_index, frame.time_ns, ((channel, identity, point), frame.states[1])))
    result = summary(replace(report, risk_grid=tuple(frames),
                             risk=tuple(point for frame in frames for _, _, point in frame.states)))
    assert [row["total"] for row in result["samples"]] == [-20, 270, 170]
    assert result["metrics"]["max_drawdown"] == 100


def test_missing_barrier_keeps_full_metrics_unknown():
    report = synthetic_report([[0, -1000, 0], [0, 0, -2000]])
    result = summary(replace(report, risk_grid=(report.risk_grid[0], report.risk_grid[2])))
    assert result["metrics"] is None
    assert "incomplete_post_event_grid" in result["blockers"]


@pytest.mark.parametrize("field", ["origin", "value"])
@pytest.mark.parametrize("bad", [True, 1.5, "2", float("nan")])
def test_money_reducer_requires_exact_units(field, bad):
    from research.risk_metrics import money_path_metrics
    with pytest.raises(ValueError, match="finite exact monetary"):
        money_path_metrics([bad] if field == "value" else [1], origin=bad if field == "origin" else 0)


def test_risk_cli_sources_include_the_shared_money_reducer():
    from tools.compare_risk_trajectories import SOURCES as comparison
    from tools.run_conditioned_management import SOURCES as conditioned
    assert "research/risk_metrics.py" in comparison
    assert "research/risk_metrics.py" in conditioned


@pytest.mark.parametrize("mutation", ["wrong_scope", "phase", "future", "stale_open", "clock"])
def test_grid_does_not_certify_incoherent_contributions(mutation):
    report = synthetic_report([[0, 100], [0, 200]])
    frame = report.risk_grid[-1]
    channel, identity, point = frame.states[0]
    changes = {"wrong_scope": {"signal_id": "canal1_other"}, "phase": {"phase": "quote_open"},
               "future": {"tick_index": 2}, "stale_open": {"tick_index": 0, "positions": (("sim_1", .04, 100.),)},
               "clock": {"time_ns": 0}}[mutation]
    bad = replace(frame, states=((channel, identity, replace(point, **changes)), frame.states[1]))
    with pytest.raises(ValueError, match="shared grid state"):
        summary(replace(report, risk_grid=(report.risk_grid[0], bad)))


def test_money_reducer_against_exhaustive_independent_pairwise_extrema():
    from research.risk_metrics import money_path_metrics
    for values in product((None, -200, 0, 100), repeat=5):
        known = [value for value in values if value is not None]
        result = money_path_metrics(values, origin=0)
        if not known:
            assert result is None
            continue
        series = [0] + known
        assert result["max_drawdown"] == max(series[i] - series[j]
                                             for i in range(len(series)) for j in range(i, len(series)))
        assert result["minimum_from_origin"] == min(series)
        assert result["maximum_from_origin"] == max(series)
        assert result["final_net"] == known[-1]
        assert result == money_path_metrics((Decimal(value) if value is not None else None for value in values), origin=Decimal(0))


@pytest.mark.parametrize("ordinal", [0, 4])
def test_earlier_settled_round_cannot_replace_final_or_terminal_state(ordinal):
    report = simulate_shared([gap_spec()], profile=PROFILE)
    preentry = next(point for point in report.risk if point.phase == "settled"
                    and point.tick_index == 0 and point.causal_round == 0)
    frames = list(report.risk_grid)
    channel, identity, _ = frames[ordinal].states[0]
    frames[ordinal] = replace(frames[ordinal], states=((channel, identity, preentry),))
    with pytest.raises(ValueError, match="shared grid state"):
        summary(replace(report, risk_grid=tuple(frames)))
