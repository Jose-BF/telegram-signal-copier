from dataclasses import replace
from datetime import timedelta

import pytest

from research.dubai_iterative.client import ClientProfile
from research.dubai_iterative.dataset import ProviderEvent
from research.dubai_iterative.engine import ExecutionAssumptions
from research.dubai_iterative.shared_replay import simulate_shared
from research.causal_lifecycle import LifecycleTiming, signal_state_at
from research.causal_replay import CausalSignal
from tests.test_iterative_market import market
from tests.test_iterative_protection import BASE, path, policy, profile
from tests.test_absolute_be_execution import assumptions as be_assumptions
from tests.test_absolute_level_execution import policy as absolute_policy
from tests.test_shared_policy_replay import PROFILE, basket, spec


def test_causal_provider_delivery_matches_static_full_risk_path():
    event = ProviderEvent(BASE + timedelta(milliseconds=1500), "CLOSE_ALL", {})
    item = spec("canal1_dynamic", [100.] * 7,
                tape=replace(path([100.] * 7), provider_events=(event,)),
                strategy=policy(target_mode="none", target_steps=(), trailing_distance=None,
                                provider_management_mode="explicit_close_only"),
                execution=ExecutionAssumptions(
                    protection=profile(), market=market(entry_acknowledgement_delay_ms=0,
                                                        close_acknowledgement_delay_ms=0),
                    client=ClientProfile()))
    expected = simulate_shared([item], profile=PROFILE)
    dynamic_item = replace(item, path=replace(item.path, provider_events=()))
    deliveries = []

    def source(index, time_ns, prior):
        if index != 2:
            return ()
        assert prior[0][0] == item.path.signal_id
        assert prior[0][1].risk_point.tick_index == 1
        deliveries.append((index, time_ns))
        return ((item.path.signal_id, event),)

    actual = simulate_shared([dynamic_item], profile=PROFILE,
                             provider_event_source=source)
    assert len(deliveries) == 1
    assert basket(actual, item.path.signal_id) == basket(expected, item.path.signal_id)
    assert actual.risk == expected.risk
    assert actual.risk_grid == expected.risk_grid
    assert actual.transport == expected.transport
    assert actual.blockers == expected.blockers
    assert actual.dynamic_provider_events == ((item.path.signal_id, (event,)),)
    assert any("dynamic basket admission" in reason for reason in actual.limitations)


def test_provider_source_sees_only_settled_prefix_not_future_exit():
    item = spec("canal1_prefix", [100., 100., 100., 105., 105.],
                execution=ExecutionAssumptions(
                    protection=profile(), market=market(entry_acknowledgement_delay_ms=0),
                    client=ClientProfile()))
    observed = []
    signal = CausalSignal(item.path.signal_id, item.channel, item.path.direction,
                          item.path.signal_observed_at, item.path.signal_observed_at,
                          "rev-prefix")

    def source(index, _time, prior):
        if index == 0:
            assert prior[0][1] is None
        if index == 2:
            prefix = prior[0][1]
            observed.append(prefix)
            assert prefix.signal_id == item.path.signal_id
            assert prefix.strategy_fingerprint == item.genome.fingerprint
            assert prefix.risk_point.tick_index == 1
            assert len(prefix.entries) == 1
            assert prefix.exits == ()
            assert prefix.last_tick_index <= 1
            state = signal_state_at(signal, item.genome, prefix,
                                    BASE + timedelta(milliseconds=1500), LifecycleTiming(0))
            assert state["status"] == "open"
        return ()

    report = simulate_shared([item], profile=PROFILE, provider_event_source=source)
    assert len(observed) == 1
    assert basket(report, item.path.signal_id).exits[0].tick_index == 3


def test_causal_provider_close_cancels_queued_entry_under_shared_contention():
    quotes = [100.] * 10
    busy = spec("canal2_busy", quotes, execution=ExecutionAssumptions(
        protection=profile(), market=market(entry_acknowledgement_delay_ms=5000),
        client=ClientProfile()))
    event = ProviderEvent(BASE + timedelta(milliseconds=1500), "CLOSE_ALL", {})
    tape = replace(path(quotes), provider_events=(event,))
    cancelled = spec("canal1_cancelled", [], tape=tape,
                     strategy=policy(entry_mode="delay", entry_value=1.,
                                     provider_management_mode="explicit_close_only"))
    expected = simulate_shared([busy, cancelled], profile=PROFILE)
    actual = simulate_shared(
        [busy, replace(cancelled, path=replace(cancelled.path, provider_events=()))],
        profile=PROFILE,
        provider_event_source=lambda index, _time, _prior: (
            ((cancelled.path.signal_id, event),) if index == 2 else ()))
    assert actual.baskets == expected.baskets
    assert actual.risk == expected.risk
    assert actual.risk_grid == expected.risk_grid
    assert actual.transport == expected.transport
    assert actual.blockers == expected.blockers


def test_provider_be_with_shared_client_remains_blocked_until_supported():
    event = ProviderEvent(BASE + timedelta(milliseconds=1500), "MOVE_SL_TO_BE",
                          {"modality": "direct"})
    tape = replace(path([100., 100., 103., 103., 99., 99.]), provider_events=(event,))
    item = spec("canal1_provider_be", [], tape=tape,
                strategy=absolute_policy(be_mode="provider", provider_management_mode="exact"),
                execution=replace(be_assumptions(), client=ClientProfile()))
    static = simulate_shared([item], profile=PROFILE)
    assert any("absolute_levels_require_explicit_market_profile" in reason
               for reason in static.blockers)
    with pytest.raises(ValueError, match="finished basket"):
        simulate_shared(
            [replace(item, path=replace(item.path, provider_events=()))], profile=PROFILE,
            provider_event_source=lambda index, _time, _prior: (
                ((item.path.signal_id, event),) if index == 2 else ()))


@pytest.mark.parametrize("delivery_index,event_ms", [(2, 2000), (3, 1500)])
def test_causal_provider_delivery_rejects_tie_or_late_message(delivery_index, event_ms):
    item = spec("canal1_clock", [100.] * 5)
    event = ProviderEvent(BASE + timedelta(milliseconds=event_ms), "CLOSE_ALL", {})
    with pytest.raises(ValueError, match="interquote"):
        simulate_shared([item], profile=PROFILE,
                        provider_event_source=lambda index, _time, _prior: (
                            ((item.path.signal_id, event),) if index == delivery_index else ()))


def test_causal_provider_delivery_rejects_static_events_and_unknown_scope():
    item = spec("canal1_scope", [100.] * 5)
    event = ProviderEvent(BASE + timedelta(milliseconds=1500), "CLOSE_ALL", {})
    with pytest.raises(ValueError, match="static provider events"):
        simulate_shared([replace(item, path=replace(item.path, provider_events=(event,)))],
                        profile=PROFILE, provider_event_source=lambda *_: ())
    with pytest.raises(ValueError, match="unknown provider scope"):
        simulate_shared([item], profile=PROFILE,
                        provider_event_source=lambda index, _time, _prior: (
                            (("canal1_missing", event),) if index == 2 else ()))


def test_causal_provider_delivery_has_bounded_event_budget():
    item = spec("canal1_budget", [100.] * 5)
    events = tuple(ProviderEvent(BASE + timedelta(milliseconds=offset), "CLOSE_ALL", {})
                   for offset in range(100, 650, 50))
    with pytest.raises(ValueError, match="delivery budget"):
        simulate_shared([item], profile=replace(PROFILE, max_events=10),
                        provider_event_source=lambda index, _time, _prior: (
                            tuple((item.path.signal_id, event) for event in events)
                            if index == 1 else ()))


def test_causal_basket_admission_matches_predeclared_full_risk_path():
    quotes = [100.] * 7
    first = spec("canal2_initial", quotes)
    observed = BASE + timedelta(milliseconds=1500)
    later = spec("canal1_later", [], tape=replace(
        path(quotes), signal_observed_at=observed, opened_at=observed))
    expected = simulate_shared([first, later], profile=PROFILE)
    observed_prefixes = []

    def admit(index, _time, prior):
        if index == 2:
            observed_prefixes.append(prior)
            return (later.path.signal_id,)
        return ()

    actual = simulate_shared(
        [first, later], profile=PROFILE, basket_admission_source=admit,
        initially_admitted=(first.path.signal_id,))
    assert len(observed_prefixes) == 1
    assert observed_prefixes[0][1][1].entries == ()
    assert actual.baskets == expected.baskets
    assert actual.risk == expected.risk
    assert actual.risk_grid == expected.risk_grid
    assert actual.transport == expected.transport
    assert actual.blockers == expected.blockers
    assert actual.dynamic_basket_admissions == ((later.path.signal_id, 2),)


def test_nonadmitted_basket_cannot_open_or_receive_provider_event():
    quotes = [100.] * 6
    first = spec("canal2_initial", quotes)
    observed = BASE + timedelta(milliseconds=1500)
    rejected = spec("canal1_rejected", [], tape=replace(
        path(quotes), signal_observed_at=observed, opened_at=observed))
    actual = simulate_shared(
        [first, rejected], profile=PROFILE,
        basket_admission_source=lambda *_: (),
        initially_admitted=(first.path.signal_id,))
    control = simulate_shared([first], profile=PROFILE)
    assert basket(actual, rejected.path.signal_id).entries == ()
    assert all(not point.positions for point in actual.risk
               if point.signal_id == rejected.path.signal_id)
    assert tuple(point for point in actual.risk if point.signal_id == first.path.signal_id) == control.risk
    assert actual.transport == control.transport
    assert actual.blockers == control.blockers
    assert all(not frame.blockers for frame in actual.risk_grid)
    event = ProviderEvent(BASE + timedelta(milliseconds=2500), "CLOSE_ALL", {})
    with pytest.raises(ValueError, match="not admitted"):
        simulate_shared(
            [first, rejected], profile=PROFILE,
            basket_admission_source=lambda *_: (),
            initially_admitted=(first.path.signal_id,),
            provider_event_source=lambda index, _time, _prior: (
                ((rejected.path.signal_id, event),) if index == 3 else ()))


@pytest.mark.parametrize("delivery_index", [1, 3])
def test_causal_basket_admission_rejects_early_or_late_delivery(delivery_index):
    quotes = [100.] * 5
    observed = BASE + timedelta(milliseconds=1500)
    item = spec("canal1_clock", [], tape=replace(
        path(quotes), signal_observed_at=observed, opened_at=observed))
    with pytest.raises(ValueError, match="outside causal interquote window"):
        simulate_shared(
            [item], profile=PROFILE,
            basket_admission_source=lambda index, _time, _prior: (
                (item.path.signal_id,) if index == delivery_index else ()))


def test_message_quote_schedule_skips_empty_prefix_snapshots():
    item = spec("canal1_scheduled", [100.] * 6)
    seen = []

    def source(index, _time, prior):
        seen.append((index, prior[0][0]))
        return ()

    expected = simulate_shared([item], profile=PROFILE)
    actual = simulate_shared(
        [item], profile=PROFILE, provider_event_source=source,
        source_quote_indices=(2, 4))
    assert seen == [(2, item.path.signal_id), (4, item.path.signal_id)]
    assert actual.baskets == expected.baskets
    assert actual.risk == expected.risk
    assert actual.risk_grid == expected.risk_grid
    assert actual.transport == expected.transport
