from datetime import datetime, timedelta, timezone
from dataclasses import replace

import pytest

from research.causal_lifecycle import (
    LifecycleTiming, project_signal_closure, signal_state_at,
)
from research.causal_replay import CausalSignal
from research.dubai_iterative.client_contract import ClientEvent
from research.dubai_iterative.market_contract import MarketEvent
from tools.run_week_causal_controls import policies


BASE = datetime(2026, 9, 17, 14, tzinfo=timezone.utc)
SIGNAL = CausalSignal("canal1_1", "canal1", "BUY", BASE, BASE, "rev-1")


def result(reason, *, blockers=(), exit_second=20):
    return {
        "signal_id": SIGNAL.signal_id,
        "strategy_fingerprint": policies()["canal1"].fingerprint,
        "entries": [{"ticket": "sim_1", "opened_at": BASE.isoformat(), "volume": 0.01}],
        "exits": [{"ticket": "sim_1", "closed_at":
                   (BASE + timedelta(seconds=exit_second)).isoformat(),
                   "volume": 0.01, "reason": reason}],
        "blockers": list(blockers),
    }


def test_explicit_terminal_closure_uses_simulated_exit_not_live_event():
    timing = LifecycleTiming(finalization_delay_s=5)
    projection = project_signal_closure(
        SIGNAL, policies()["canal1"], result("basket_stop"), timing)
    assert projection["status"] == "modeled_lifecycle_hypothesis"
    assert projection["signal_closed_at"] == (BASE + timedelta(seconds=25)).isoformat()
    assert projection["lifecycle_source"] == "counterfactual_policy_clock_v1"


def test_natural_flat_waits_for_pending_entry_expiry():
    projection = project_signal_closure(
        SIGNAL, policies()["canal1"], result("native_tp"),
        LifecycleTiming(finalization_delay_s=5))
    assert projection["signal_closed_at"] == (BASE + timedelta(minutes=15, seconds=5)).isoformat()
    assert projection["terminal_kind"] == "automatic_flat_after_expiry"


def test_natural_flat_after_all_planned_legs_does_not_wait_for_expiry():
    completed = result("native_tp")
    for index, volume in ((2, 0.04), (3, 0.04)):
        ticket = f"sim_ladder_{index}"
        completed["entries"].append({"ticket": ticket, "opened_at": BASE.isoformat(),
                                     "volume": volume})
        completed["exits"].append({"ticket": ticket,
                                   "closed_at": (BASE + timedelta(seconds=20)).isoformat(),
                                   "volume": volume, "reason": "native_tp"})
    projection = project_signal_closure(
        SIGNAL, policies()["canal1"], completed,
        LifecycleTiming(finalization_delay_s=5))
    assert projection["signal_closed_at"] == (BASE + timedelta(seconds=35)).isoformat()
    assert projection["terminal_kind"] == "automatic_flat_all_entries_settled"


def test_censored_or_unfilled_path_cannot_invent_signal_closure():
    timing = LifecycleTiming(finalization_delay_s=5)
    assert project_signal_closure(
        SIGNAL, policies()["canal1"], result("data_end"), timing)["status"] == (
        "blocked_censored_exit")
    assert project_signal_closure(
        SIGNAL, policies()["canal1"], result("basket_stop", blockers=("gap",)),
        timing)["status"] == "blocked_simulation_path"
    empty = result("basket_stop")
    empty["entries"] = empty["exits"] = []
    assert project_signal_closure(
        SIGNAL, policies()["canal1"], empty, timing)["status"] == "blocked_no_fill"
    wrong_plan = result("native_tp")
    wrong_plan["entries"][0]["volume"] = 0.04
    assert project_signal_closure(
        SIGNAL, policies()["canal1"], wrong_plan, timing)["status"] == (
        "blocked_entry_plan_identity")


def test_prefix_state_uses_only_actions_available_at_candidate_time():
    policy = policies()["canal1"]
    timing = LifecycleTiming(finalization_delay_s=5)
    candidate = BASE + timedelta(seconds=30)
    assert signal_state_at(SIGNAL, policy, result("basket_stop"), candidate,
                           timing)["status"] == "closed"
    assert signal_state_at(SIGNAL, policy, result("native_tp"), candidate,
                           timing)["status"] == "open"
    assert signal_state_at(SIGNAL, policy, result("native_tp"),
                           BASE + timedelta(minutes=15, seconds=6), timing)["status"] == (
        "closed")
    still_open = result("basket_stop", exit_second=40)
    assert signal_state_at(SIGNAL, policy, still_open, candidate,
                           timing)["status"] == "open"


def test_prefix_state_keeps_registered_unfilled_signal_pending_until_expiry():
    policy = policies()["canal1"]
    timing = LifecycleTiming(finalization_delay_s=5)
    candidate = BASE + timedelta(seconds=30)
    late = result("basket_stop", exit_second=60)
    late["entries"][0]["opened_at"] = (BASE + timedelta(seconds=40)).isoformat()
    assert signal_state_at(SIGNAL, policy, late, candidate,
                           timing)["status"] == "open"
    no_fill = {"signal_id": SIGNAL.signal_id,
               "strategy_fingerprint": policy.fingerprint,
               "entries": [], "exits": [], "blockers": []}
    assert signal_state_at(SIGNAL, policy, no_fill, candidate,
                           timing)["status"] == "open"
    assert signal_state_at(SIGNAL, policy, no_fill,
                           BASE + timedelta(minutes=15, seconds=6), timing)["status"] == (
        "blocked")
    impossible_late = result("basket_stop", exit_second=1000)
    impossible_late["entries"][0]["opened_at"] = (
        BASE + timedelta(minutes=16)).isoformat()
    assert signal_state_at(SIGNAL, policy, impossible_late,
                           BASE + timedelta(minutes=15, seconds=6), timing)["status"] == (
        "blocked")


def test_prefix_state_rejects_censored_or_in_flight_path():
    policy = policies()["canal1"]
    timing = LifecycleTiming(finalization_delay_s=5)
    candidate = BASE + timedelta(seconds=30)
    assert signal_state_at(SIGNAL, policy, result("data_end"), candidate,
                           timing)["status"] == "blocked"
    in_flight = result("native_tp")
    in_flight["market_events"] = [{"kind": "entry_requested"}]
    assert signal_state_at(SIGNAL, policy, in_flight, candidate,
                           timing)["status"] == "blocked"
    impossible = result("basket_stop", exit_second=40)
    impossible["entries"].append({"ticket": "sim_ladder_2",
                                  "opened_at": BASE.isoformat(), "volume": 0.04})
    impossible["exits"].append({"ticket": "sim_1",
                                "closed_at": (BASE + timedelta(seconds=20)).isoformat(),
                                "volume": 0.02, "reason": "basket_stop"})
    assert signal_state_at(SIGNAL, policy, impossible, candidate,
                           timing)["status"] == "blocked"


def settled_execution_events():
    def stamp(second):
        return int((BASE + timedelta(seconds=second)).timestamp() * 1_000_000_000)

    market = (
        MarketEvent("sim_1", 0, stamp(0), 1, "entry_requested", 100., 0.01, "signal"),
        MarketEvent("sim_1", 0, stamp(0), 1, "entry_filled", 100., 0.01, "signal"),
        MarketEvent("sim_1", 1, stamp(1), 1, "entry_acknowledged", 100., 0.01, "accepted"),
        MarketEvent("sim_1", 19, stamp(19), 2, "close_requested", 99., 0.01, "basket_stop"),
        MarketEvent("sim_1", 20, stamp(20), 2, "close_filled", 99., 0.01, "basket_stop"),
        MarketEvent("sim_1", 21, stamp(21), 2, "close_acknowledged", 99., 0.01, "accepted"),
    )
    client = (
        ClientEvent("queued", "entry", "sim_1", 0, stamp(0), 0, stamp(0)),
        ClientEvent("started", "entry", "sim_1", 0, stamp(0), 0, stamp(0)),
        ClientEvent("released", "entry", "sim_1", 1, stamp(1), 0, stamp(0)),
        ClientEvent("queued", "close", "sim_1", 19, stamp(19), 19, stamp(19)),
        ClientEvent("started", "close", "sim_1", 19, stamp(19), 19, stamp(19)),
        ClientEvent("released", "close", "sim_1", 21, stamp(21), 19, stamp(19)),
    )
    return market, client


def test_prefix_closure_requires_terminal_market_and_client_events():
    market, client = settled_execution_events()
    candidate = BASE + timedelta(seconds=30)
    path = result("basket_stop")
    path.update(market_events=market, client_events=client)

    assert signal_state_at(SIGNAL, policies()["canal1"], path, candidate,
                           LifecycleTiming(finalization_delay_s=5))["status"] == "closed"


def test_prefix_closure_waits_for_late_execution_settlement():
    market, client = settled_execution_events()
    late_ns = int((BASE + timedelta(seconds=29)).timestamp() * 1_000_000_000)
    path = result("basket_stop")
    path.update(market_events=(*market[:-1], replace(market[-1], timestamp_ns=late_ns)),
                client_events=(*client[:-1], replace(client[-1], time_ns=late_ns)))
    timing = LifecycleTiming(finalization_delay_s=5)

    assert signal_state_at(SIGNAL, policies()["canal1"], path,
                           BASE + timedelta(seconds=30), timing)["status"] == "open"
    assert signal_state_at(SIGNAL, policies()["canal1"], path,
                           BASE + timedelta(seconds=35), timing)["status"] == "closed"


def test_terminal_decision_marker_does_not_masquerade_as_pending_close():
    market, client = settled_execution_events()
    marker = ClientEvent("terminal_requested", "close", "basket", 19,
                         client[3].time_ns, 19, client[3].decision_ns)
    path = result("basket_stop")
    path.update(market_events=market,
                client_events=(*client[:3], marker, *client[3:]))
    at = BASE + timedelta(seconds=30)

    assert signal_state_at(SIGNAL, policies()["canal1"], path, at,
                           LifecycleTiming(finalization_delay_s=5))["status"] == "closed"
    path["client_events"] = (*client[:3], marker, marker, *client[3:])
    assert signal_state_at(SIGNAL, policies()["canal1"], path, at,
                           LifecycleTiming(finalization_delay_s=5))["status"] == "blocked"


@pytest.mark.parametrize("damage", [
    "market_ack", "client_release", "future_event", "unknown_kind",
    "wrong_ticket", "market_order", "client_order"])
def test_prefix_closure_fails_closed_on_incomplete_or_future_execution(damage):
    market, client = settled_execution_events()
    if damage == "market_ack":
        market = market[:-1]
    elif damage == "client_release":
        client = client[:-1]
    elif damage == "future_event":
        market = (*market, replace(market[-1], timestamp_ns=int(
            (BASE + timedelta(seconds=31)).timestamp() * 1_000_000_000)))
    elif damage == "unknown_kind":
        market = (*market, replace(market[-1], kind="unrecognized"))
    elif damage == "wrong_ticket":
        market = (*market[:-1], replace(market[-1], ticket="other"))
    elif damage == "market_order":
        market = (market[1], market[0], *market[2:])
    else:
        client = (client[1], client[0], *client[2:])
    path = result("basket_stop")
    path.update(market_events=market, client_events=client)

    assert signal_state_at(
        SIGNAL, policies()["canal1"], path, BASE + timedelta(seconds=30),
        LifecycleTiming(finalization_delay_s=5))["status"] == "blocked"
