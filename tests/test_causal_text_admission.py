from datetime import datetime, timedelta, timezone
from decimal import Decimal

from research.causal_text_admission import (
    CausalRouteSession,
    candidate_text_entries,
    open_volume_at,
    route_canal1_stream,
    route_text_candidate,
)
from research.causal_replay import CausalSignal
from research.causal_canal1_stream import CausalManagementMessage
from research.causal_lifecycle import LifecycleTiming, PREFIX_SOURCE, signal_state_at
from research.dubai_iterative.dataset import ProviderEvent
from tools.run_week_causal_controls import policies


BASE = datetime(2026, 9, 17, 14, tzinfo=timezone.utc)


def raw(message_id, text, *, seconds=0, published_seconds=None, reply=None, revision=1):
    observed = BASE + timedelta(seconds=seconds)
    published = BASE + timedelta(seconds=seconds if published_seconds is None
                                 else published_seconds)
    return {"ev": "telegram_raw", "channel": "canal1", "message_id": message_id,
            "message_revision_id": f"rev-{message_id}-{revision}",
            "date_utc": published.isoformat(), "ts": observed.isoformat(),
            "text": text, "is_edit": revision > 1, "edit_date_utc": None,
            "reply_to_msg_id": reply, "sticker_id": None}


def result(*, opened=0, exits=(), blockers=()):
    return {"entries": [{"ticket": "sim_1", "opened_at": BASE.isoformat(),
                         "volume": opened}] if opened else [],
            "exits": [{"ticket": "sim_1", "closed_at":
                       (BASE + timedelta(seconds=seconds)).isoformat(),
                       "volume": volume} for seconds, volume in exits],
            "blockers": list(blockers)}


def prefix_state(status, *, seconds=30, source=PREFIX_SOURCE):
    return {"status": status, "observed_at":
            (BASE + timedelta(seconds=seconds)).isoformat(),
            "lifecycle_source": source}


def management(message_id, seconds, *, reply=None):
    at = BASE + timedelta(seconds=seconds)
    return CausalManagementMessage(
        message_id, f"rev-{message_id}", at, at, reply,
        ProviderEvent(at, "MOVE_SL_TO_BE", {"action": "MOVE_SL_TO_BE",
                                                "modality": "direct"}))


def test_fresh_text_without_reply_is_candidate_once_not_stale_backfill():
    text = "BUY GOLD NOW 4366 TP1: 4370 SL: 4355"
    rows = [raw(1, text), raw(1, text, seconds=2, revision=2),
            raw(2, text, seconds=200, published_seconds=0),
            raw(3, text, seconds=3, reply=1),
            raw(4, "Gold update, TP1 hit", seconds=4),
            raw(5, text, seconds=5, revision=2)]

    candidates, exclusions = candidate_text_entries(
        rows, start=BASE, cutoff=BASE + timedelta(minutes=5), max_entry_age_s=120)

    assert [row.signal_id for row in candidates] == ["canal1_1"]
    assert candidates[0].direction == "BUY"
    assert len(candidates[0].provider_events) == 1
    assert candidates[0].provider_events[0].action == "LEVEL_UPDATE"
    assert candidates[0].provider_events[0].observed_at == BASE
    assert exclusions == [{"signal_id": "canal1_2", "reason": "stale_text_candidate"}]


def test_partial_close_retains_open_volume_and_same_time_is_unknown():
    path = result(opened=0.04, exits=((10, 0.01), (20, 0.03)))
    assert open_volume_at(path, BASE + timedelta(seconds=15)) == Decimal("0.03")
    assert open_volume_at(path, BASE + timedelta(seconds=20)) is None
    assert open_volume_at(path, BASE + timedelta(seconds=21)) == Decimal("0")
    assert open_volume_at(result(opened=0.04, blockers=("data_gap",)),
                          BASE + timedelta(seconds=15)) is None


def test_route_changes_by_scenario_and_never_uses_observed_fill():
    candidate = candidate_text_entries(
        [raw(2, "BUY GOLD NOW 4366 TP1: 4370 SL: 4355", seconds=30)],
        start=BASE, cutoff=BASE + timedelta(minutes=5), max_entry_age_s=120)[0][0]
    still_open = {"canal1_1": result(opened=0.04, exits=((40, 0.04),))}
    already_flat = {"canal1_1": result(opened=0.04, exits=((20, 0.04),))}

    assert route_text_candidate(candidate, still_open, prior_universe_complete=True) == {
        "status": "targets_open_signal", "open_signal_ids": ["canal1_1"]}
    assert route_text_candidate(candidate, already_flat, prior_universe_complete=True,
                                prior_states={"canal1_1": prefix_state("closed")}) == {
        "status": "eligible_text_fallback", "open_signal_ids": []}
    assert route_text_candidate(candidate, {"canal1_1": result(
        opened=0.04, exits=((20, 0.04),))},
        prior_universe_complete=True)["status"] == "blocked_unknown_lifecycle"
    assert route_text_candidate(candidate, {"canal1_1": result(
        opened=0.04, blockers=("missing_quote",))},
        prior_universe_complete=True)["status"] == "blocked_unknown_state"


def test_flat_but_modeled_still_open_targets_prior_signal():
    candidate = candidate_text_entries(
        [raw(2, "BUY GOLD NOW 4366 TP1: 4370 SL: 4355", seconds=30)],
        start=BASE, cutoff=BASE + timedelta(minutes=5), max_entry_age_s=120)[0][0]
    pending = result(opened=0.04, exits=((20, 0.04),))
    assert route_text_candidate(candidate, {"canal1_1": pending},
                                prior_universe_complete=True,
                                prior_states={"canal1_1": prefix_state("open")})["status"] == (
        "targets_open_signal")
    assert route_text_candidate(candidate, {"canal1_1": pending},
                                prior_universe_complete=True,
                                prior_states={"canal1_1": prefix_state(
                                    "open", source="observed_journal")})["status"] == (
        "blocked_unknown_lifecycle")
    not_filled_yet = result(opened=0.04)
    not_filled_yet["entries"][0]["opened_at"] = (
        BASE + timedelta(seconds=40)).isoformat()
    assert route_text_candidate(candidate, {"canal1_1": not_filled_yet},
                                prior_universe_complete=True,
                                prior_states={"canal1_1": prefix_state("open")})["status"] == (
        "targets_open_signal")


def test_registered_unfilled_signal_targets_text_until_modeled_expiry():
    policy = policies()["canal1"]
    prior = CausalSignal("canal1_1", "canal1", "BUY", BASE, BASE, "rev-1")
    no_fill = {"signal_id": prior.signal_id,
               "strategy_fingerprint": policy.fingerprint,
               "entries": [], "exits": [], "blockers": []}
    timing = LifecycleTiming(finalization_delay_s=5)
    for seconds, expected in ((30, "targets_open_signal"),
                              (15 * 60 + 6, "blocked_unknown_lifecycle")):
        candidate = candidate_text_entries(
            [raw(2, "BUY GOLD NOW 4366 TP1: 4370 SL: 4355", seconds=seconds)],
            start=BASE, cutoff=BASE + timedelta(minutes=20),
            max_entry_age_s=120)[0][0]
        state = signal_state_at(prior, policy, no_fill, candidate.observed_at, timing)
        assert route_text_candidate(
            candidate, {prior.signal_id: no_fill}, prior_universe_complete=True,
            prior_states={prior.signal_id: state})["status"] == expected


def test_management_reply_follows_scenario_text_root_not_static_sticker():
    sticker = CausalSignal("canal1_1", "canal1", "BUY", BASE, BASE, "rev-1")
    text = candidate_text_entries(
        [raw(2, "BUY GOLD NOW 4366 TP1: 4370 SL: 4355", seconds=30)],
        start=BASE, cutoff=BASE + timedelta(minutes=5), max_entry_age_s=120)[0][0]
    applied = []

    def apply(event, prior):
        applied.append((event.message_id, prior["signal_id"]))
        return prior

    report = route_canal1_stream(
        [sticker], [text], management=[management(3, 40, reply=2)],
        replay_entry=lambda signal: {"signal_id": signal.signal_id,
                                     **result(opened=0.04)},
        apply_text_update=lambda signal, prior: prior,
        apply_management=apply,
        resolve_state=lambda signal_id, prior, at: {
            "status": "open", "observed_at": at.isoformat(),
            "lifecycle_source": PREFIX_SOURCE},
        initial_universe_complete=True)

    assert [row["status"] for row in report["decisions"]] == [
        "sticker_entry", "targets_open_signal", "management_targets_signal"]
    assert applied == [(3, "canal1_1")]
    assert report["decisions"][2]["target_signal_id"] == "canal1_1"


def test_explicit_management_reply_disambiguates_two_open_baskets():
    stickers = [CausalSignal(f"canal1_{index}", "canal1", "BUY",
                             BASE + timedelta(seconds=index - 1), BASE,
                             f"rev-{index}") for index in (1, 2)]
    report = route_canal1_stream(
        stickers, [], management=[management(3, 30, reply=1)],
        replay_entry=lambda signal: {"signal_id": signal.signal_id,
                                     **result(opened=0.04)},
        apply_management=lambda event, prior: prior,
        resolve_state=lambda signal_id, prior, at: {
            "status": "open", "observed_at": at.isoformat(),
            "lifecycle_source": PREFIX_SOURCE},
        initial_universe_complete=True)

    assert report["decisions"][2]["status"] == "management_targets_signal"
    assert report["decisions"][2]["target_signal_id"] == "canal1_1"


def test_stream_refreshes_all_prior_baskets_after_shared_world_changes():
    stickers = [CausalSignal(f"canal1_{index}", "canal1", "BUY",
                             BASE + timedelta(seconds=10 * (index - 1)), BASE,
                             f"rev-{index}") for index in (1, 2)]
    candidate = candidate_text_entries(
        [raw(3, "BUY GOLD NOW 4366 TP1: 4370 SL: 4355", seconds=30)],
        start=BASE, cutoff=BASE + timedelta(minutes=5), max_entry_age_s=120)[0][0]
    admitted, refreshed = [], []

    def replay(signal):
        admitted.append(signal.signal_id)
        return result(opened=0.04)

    def refresh():
        refreshed.append(tuple(admitted))
        if len(admitted) == 1:
            return {admitted[0]: {"signal_id": admitted[0], **result(opened=0.04)}}
        world = {signal_id: {"signal_id": signal_id,
                             **result(opened=0.04, exits=((20, 0.04),))}
                 for signal_id in admitted[:2]}
        if len(admitted) == 3:
            world[admitted[2]] = {"signal_id": admitted[2], **result(opened=0.04)}
        return world

    report = route_canal1_stream(
        stickers, [candidate], replay_entry=replay, refresh_world=refresh,
        resolve_state=lambda signal_id, prior, at: {
            "status": "closed" if prior["exits"] else "open",
            "observed_at": at.isoformat(), "lifecycle_source": PREFIX_SOURCE},
        initial_universe_complete=True)

    assert [row["status"] for row in report["decisions"]] == [
        "sticker_entry", "sticker_entry", "eligible_text_fallback"]
    assert refreshed == [("canal1_1",), ("canal1_1", "canal1_2"),
                         ("canal1_1", "canal1_2", "canal1_3")]


def test_incomplete_shared_refresh_blocks_the_remaining_message_stream():
    sticker = CausalSignal("canal1_1", "canal1", "BUY", BASE, BASE, "rev-1")
    candidate = candidate_text_entries(
        [raw(2, "BUY GOLD NOW 4366 TP1: 4370 SL: 4355", seconds=30)],
        start=BASE, cutoff=BASE + timedelta(minutes=5), max_entry_age_s=120)[0][0]
    report = route_canal1_stream(
        [sticker], [candidate], replay_entry=lambda signal: {
            "signal_id": signal.signal_id, **result(opened=0.04)},
        refresh_world=lambda: {}, initial_universe_complete=True)

    assert [row["status"] for row in report["decisions"]] == [
        "blocked_shared_world_incomplete", "blocked_incomplete_prior_universe"]


def test_censored_or_inconsistent_prior_path_cannot_certify_text_route():
    candidate = candidate_text_entries(
        [raw(2, "BUY GOLD NOW 4366 TP1: 4370 SL: 4355", seconds=30)],
        start=BASE, cutoff=BASE + timedelta(minutes=5), max_entry_age_s=120)[0][0]
    censored = result(opened=0.04, exits=((20, 0.04),))
    censored["exits"][0]["reason"] = "data_end"
    assert open_volume_at(censored, candidate.observed_at) is None
    assert route_text_candidate(candidate, {"canal1_1": censored},
                                prior_universe_complete=True)["status"] == (
        "blocked_unknown_state")
    impossible = result(opened=0.04)
    assert route_text_candidate(candidate, {"canal1_1": impossible},
                                prior_universe_complete=True,
                                prior_states={"canal1_1": prefix_state("closed")})["status"] == (
        "blocked_unknown_lifecycle")


def test_text_fallback_requires_complete_prior_universe_and_consistent_closure():
    candidate = candidate_text_entries(
        [raw(2, "BUY GOLD NOW 4366 TP1: 4370 SL: 4355", seconds=30)],
        start=BASE, cutoff=BASE + timedelta(minutes=5), max_entry_age_s=120)[0][0]
    flat = result(opened=0.04, exits=((20, 0.04),))
    assert route_text_candidate(candidate, {"canal1_1": flat})["status"] == (
        "blocked_incomplete_prior_universe")
    assert route_text_candidate(candidate, {}, prior_universe_complete=True)["status"] == (
        "eligible_text_fallback")
    premature = result(opened=0.04, exits=((20, 0.04),))
    assert route_text_candidate(candidate, {"canal1_1": premature},
                                prior_universe_complete=True,
                                prior_states={"canal1_1": prefix_state("closed", seconds=29)})["status"] == (
        "blocked_unknown_lifecycle")


def test_stream_routes_the_same_text_differently_by_scenario():
    sticker = CausalSignal("canal1_1", "canal1", "BUY", BASE, BASE, "sticker-1")
    text = candidate_text_entries(
        [raw(2, "BUY GOLD NOW 4366 TP1: 4370 SL: 4355", seconds=30)],
        start=BASE, cutoff=BASE + timedelta(minutes=5), max_entry_age_s=120)[0][0]

    def replay_with(previous, status):
        calls = []

        def entry(signal):
            calls.append(signal.signal_id)
            return previous if signal.signal_id == "canal1_1" else result(opened=0.04)

        report = route_canal1_stream(
            (sticker,), (text,), replay_entry=entry,
            resolve_state=lambda _id, _result, _at: prefix_state(status),
            apply_text_update=lambda _signal, prior: prior,
            initial_universe_complete=True)
        return report, calls

    open_report, open_calls = replay_with(result(opened=0.04, exits=((40, 0.04),)), "open")
    assert open_report["decisions"][-1] == {
        "signal_id": "canal1_2", "status": "targets_open_signal",
        "open_signal_ids": ["canal1_1"]}
    assert open_calls == ["canal1_1"]

    closed_report, closed_calls = replay_with(result(
        opened=0.04, exits=((20, 0.04),)), "closed")
    assert closed_report["decisions"][-1]["status"] == "eligible_text_fallback"
    assert closed_calls == ["canal1_1", "canal1_2"]
    assert set(closed_report["results"]) == {"canal1_1", "canal1_2"}


def test_stream_propagates_unknown_lifecycle_and_unmodeled_updates():
    sticker = CausalSignal("canal1_1", "canal1", "BUY", BASE, BASE, "sticker-1")
    texts = candidate_text_entries(
        [raw(2, "BUY GOLD NOW 4366 TP1: 4370 SL: 4355", seconds=30),
         raw(3, "BUY GOLD NOW 4366 TP1: 4370 SL: 4355", seconds=60)],
        start=BASE, cutoff=BASE + timedelta(minutes=5), max_entry_age_s=120)[0]
    unknown = route_canal1_stream(
        (sticker,), texts, replay_entry=lambda _signal: result(
            opened=0.04, exits=((20, 0.04),)), initial_universe_complete=True)
    assert [row["status"] for row in unknown["decisions"]] == [
        "sticker_entry", "blocked_unknown_lifecycle", "blocked_incomplete_prior_universe"]
    assert unknown["prior_universe_complete"] is False

    unmodeled = route_canal1_stream(
        (sticker,), texts, replay_entry=lambda _signal: result(opened=0.04),
        initial_universe_complete=True)
    assert [row["status"] for row in unmodeled["decisions"]] == [
        "sticker_entry", "blocked_text_update_unmodeled", "blocked_incomplete_prior_universe"]


def test_stream_distinguishes_terminal_stop_from_pending_flat_without_live_fills():
    sticker = CausalSignal("canal1_1", "canal1", "BUY", BASE, BASE, "sticker-1")
    candidate = candidate_text_entries(
        [raw(2, "BUY GOLD NOW 4366 TP1: 4370 SL: 4355", seconds=30)],
        start=BASE, cutoff=BASE + timedelta(minutes=5), max_entry_age_s=120)[0][0]

    def scenario(reason):
        simulated = result(opened=0.01, exits=((20, 0.01),))
        simulated["signal_id"] = sticker.signal_id
        simulated["strategy_fingerprint"] = policies()["canal1"].fingerprint
        simulated["exits"][0]["reason"] = reason
        return route_canal1_stream(
            (sticker,), (candidate,), replay_entry=lambda signal: (
                simulated if signal.signal_id == sticker.signal_id
                else result(opened=0.01)),
            apply_text_update=lambda _signal, prior: prior,
            resolve_state=lambda _id, prior, at: signal_state_at(
                sticker, policies()["canal1"], prior, at,
                LifecycleTiming(finalization_delay_s=5)),
            initial_universe_complete=True)

    assert scenario("basket_stop")["decisions"][-1]["status"] == (
        "eligible_text_fallback")
    assert scenario("native_tp")["decisions"][-1]["status"] == (
        "targets_open_signal")


def test_incremental_router_uses_new_settled_world_at_each_message():
    sticker = CausalSignal("canal1_1", "canal1", "BUY", BASE, BASE, "rev-1")
    text = candidate_text_entries(
        [raw(2, "BUY GOLD NOW 4366 TP1: 4370 SL: 4355", seconds=30)],
        start=BASE, cutoff=BASE + timedelta(minutes=5), max_entry_age_s=120)[0][0]
    first = {"signal_id": sticker.signal_id, **result(opened=0.04)}
    flat = {"signal_id": sticker.signal_id,
            **result(opened=0.04, exits=((20, 0.04),))}
    route = CausalRouteSession(
        replay_entry=lambda signal: first if signal.signal_id == sticker.signal_id
        else {"signal_id": signal.signal_id, **result(opened=0.04)},
        resolve_state=lambda _id, prior, at: {
            "status": "closed" if prior["exits"] else "open",
            "observed_at": at.isoformat(), "lifecycle_source": PREFIX_SOURCE},
        initial_universe_complete=True)

    assert route.step("sticker", sticker)["status"] == "sticker_entry"
    assert route.step("text", text, prior_world={sticker.signal_id: flat})["status"] == (
        "eligible_text_fallback")
    assert set(route.report()["results"]) == {"canal1_1", "canal1_2"}


def test_incremental_router_rejects_missing_or_out_of_order_prefix():
    import pytest

    sticker = CausalSignal("canal1_1", "canal1", "BUY", BASE, BASE, "rev-1")
    later = CausalSignal("canal1_2", "canal1", "BUY",
                         BASE + timedelta(seconds=30), BASE, "rev-2")
    route = CausalRouteSession(
        replay_entry=lambda signal: {"signal_id": signal.signal_id,
                                     **result(opened=0.04)},
        initial_universe_complete=True)
    route.step("sticker", sticker)
    with pytest.raises(ValueError, match="prior world incomplete"):
        route.step("sticker", later, prior_world={})
    with pytest.raises(ValueError, match="out of order"):
        route.step("sticker", sticker)


def test_incremental_router_records_unknown_prior_basket_state():
    sticker = CausalSignal("canal1_1", "canal1", "BUY", BASE, BASE, "rev-1")
    text = candidate_text_entries(
        [raw(2, "BUY GOLD NOW 4366 TP1: 4370 SL: 4355", seconds=30)],
        start=BASE, cutoff=BASE + timedelta(minutes=5), max_entry_age_s=120)[0][0]
    route = CausalRouteSession(
        replay_entry=lambda signal: {"signal_id": signal.signal_id,
                                     **result(opened=0.04)},
        initial_universe_complete=True)
    route.step("sticker", sticker)
    assert route.step("text", text, prior_world={sticker.signal_id: None})["status"] == (
        "blocked_unknown_state")
    assert route.report()["prior_universe_complete"] is False
