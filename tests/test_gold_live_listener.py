from datetime import datetime

import pytest

import config
import gold_live_candidate
import journal
import listener
from state import Signal


def _gold_signal(source_kind="telegram_now"):
    return Signal(
        channel="canal2",
        message_id=2054,
        direction="SELL",
        market_ticket=7001,
        market_fill_price=4620.0,
        entry_source_kind=source_kind,
    )


def test_gold_candidate_attaches_only_to_formal_now_entries(monkeypatch):
    monkeypatch.setattr(config, "STRATEGY_C2_GOLD_NOW_C490_ENABLED", True)
    now_signal = _gold_signal("telegram_now")
    zone_signal = _gold_signal("zone_explicit_activation")

    assert listener._attach_gold_live_candidate(
        now_signal,
        datetime(2026, 8, 26, 15, 0, 0),
        provisional_sl=4642.0,
    ) is True
    assert listener._attach_gold_live_candidate(
        zone_signal,
        datetime(2026, 8, 26, 15, 0, 0),
        provisional_sl=4642.0,
    ) is False
    assert now_signal.live_strategy_id == gold_live_candidate.CANDIDATE_ID
    assert now_signal.live_strategy_fingerprint == (
        gold_live_candidate.CANDIDATE_FINGERPRINT
    )
    assert now_signal.target_tp_index is None
    assert now_signal.be_at_tp_index is None
    assert now_signal.time_stop_at is None
    assert now_signal.candidate_provisional_sl == 4642.0
    assert zone_signal.live_strategy_id is None


def test_gold_candidate_comments_survive_first_leg_and_extra_leg_restarts():
    assert gold_live_candidate.market_comment(2054) == "c2_2054_gv1"
    assert gold_live_candidate.market_comment(2054, 3) == "c2_2054_B3_gv1"


@pytest.mark.asyncio
async def test_gold_provider_levels_are_evidence_only(monkeypatch):
    signal = _gold_signal()
    listener._attach_gold_live_candidate(
        signal,
        datetime(2026, 8, 26, 15, 0, 0),
        provisional_sl=4642.0,
    )
    signal.provider_tps = [4618.0, 4615.0]
    signal.tps = list(signal.provider_tps)
    signal.sl = 4631.0
    events = []
    monkeypatch.setattr(
        journal,
        "event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_modify_sltp",
        lambda *_args, **_kwargs: pytest.fail("provider TP/SL must not be applied"),
    )

    await listener._apply_sl_tp(signal)

    assert events[-1][1] == "gold_provider_levels_observed_not_applied"
    assert events[-1][2]["provider_tps"] == [4618.0, 4615.0]


@pytest.mark.asyncio
async def test_gold_provider_management_is_recorded_but_never_executed(monkeypatch):
    signal = _gold_signal()
    listener._attach_gold_live_candidate(
        signal,
        datetime(2026, 8, 26, 15, 0, 0),
        provisional_sl=4642.0,
    )
    events = []
    monkeypatch.setattr(
        journal,
        "event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_close_position",
        lambda *_args, **_kwargs: pytest.fail("provider management is ignored"),
    )

    outcome = await listener._execute_one_action(
        signal,
        {"action": "CLOSE_ALL", "confidence": 0.99},
        raw_text="Close all now",
    )

    assert outcome == "ignored"
    assert events[-1][1] == "gold_provider_action_observed_not_applied"


@pytest.mark.asyncio
async def test_gold_hard_stop_requests_are_persistent(monkeypatch):
    signal = _gold_signal()
    signal.extra_market_tickets = [7002]
    listener._attach_gold_live_candidate(
        signal,
        datetime(2026, 8, 26, 15, 0, 0),
        provisional_sl=4642.0,
    )
    specs = {
        7001: {"entry": 4620.0, "volume": 0.01, "sl": 0.0, "point": 0.01},
        7002: {"entry": 4620.2, "volume": 0.01, "sl": 0.0, "point": 0.01},
    }
    requests = []

    async def fake_run(function, *args, **kwargs):
        if function is listener.executor.open_position_specs:
            return specs
        if function is listener.executor.loss_stop_price:
            return round(float(args[2]) + 22.0, 2)
        raise AssertionError(function)

    monkeypatch.setattr(listener, "_run", fake_run)
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_modify_sl",
        lambda signal, ticket, new_sl, label="", **kwargs: requests.append(
            (ticket, new_sl, kwargs.get("persist_until_signal_close"))
        ),
    )

    requested = await listener._ensure_gold_candidate_hard_stops(
        signal,
        force=True,
    )

    assert requested == 2
    assert requests == [(7001, 4642.0, True), (7002, 4642.2, True)]
    assert signal.candidate_hard_stops == {7001: 4642.0, 7002: 4642.2}


@pytest.mark.asyncio
async def test_gold_be_is_reasserted_after_hard_stop_is_confirmed(monkeypatch):
    signal = _gold_signal()
    signal.candidate_be_tickets = [7001]
    listener._attach_gold_live_candidate(
        signal,
        datetime(2026, 8, 26, 15, 0, 0),
        provisional_sl=4642.0,
    )
    requests = []

    async def fake_run(function, *args, **kwargs):
        if function is listener.executor.open_position_specs:
            return {
                7001: {
                    "entry": 4620.0,
                    "volume": 0.01,
                    "sl": 4642.0,
                    "point": 0.01,
                },
            }
        if function is listener.executor.loss_stop_price:
            return 4642.0
        raise AssertionError(function)

    monkeypatch.setattr(listener, "_run", fake_run)
    monkeypatch.setattr(journal, "event", lambda *args, **kwargs: None)
    monkeypatch.setattr(journal, "anomaly", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_modify_sl",
        lambda signal, ticket, new_sl, label="", **kwargs: requests.append(
            (ticket, new_sl, label, kwargs.get("persist_until_signal_close"))
        ),
    )

    requested = await listener._ensure_gold_candidate_hard_stops(
        signal,
        force=True,
    )

    assert requested == 1
    assert requests == [(7001, 4620.0, "GOLD BE #7001 -> 4620.00", True)]


@pytest.mark.asyncio
async def test_gold_be_pending_is_logged_only_when_a_retry_is_enqueued(
        monkeypatch):
    signal = _gold_signal()
    signal.candidate_be_tickets = [7001]
    listener._attach_gold_live_candidate(
        signal,
        datetime(2026, 8, 26, 15, 0, 0),
        provisional_sl=4642.0,
    )
    signal.candidate_hard_stop_requested_at[7001] = listener.time.time()
    signal.candidate_sl_requested_levels[7001] = 4620.0
    events = []

    async def fake_run(function, *args, **kwargs):
        if function is listener.executor.open_position_specs:
            return {
                7001: {
                    "entry": 4620.0,
                    "volume": 0.01,
                    "sl": 4642.0,
                    "point": 0.01,
                },
            }
        if function is listener.executor.loss_stop_price:
            return 4642.0
        raise AssertionError(function)

    monkeypatch.setattr(listener, "_run", fake_run)
    monkeypatch.setattr(
        journal,
        "event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )
    monkeypatch.setattr(journal, "anomaly", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_modify_sl",
        lambda *_args, **_kwargs: pytest.fail(
            "the existing retry is still inside its throttle window"
        ),
    )

    requested = await listener._ensure_gold_candidate_hard_stops(signal)

    assert requested == 0
    assert not any(ev == "gold_be_pending" for _sig, ev, _fields in events)


@pytest.mark.asyncio
async def test_gold_missing_sl_is_reenqueued_on_the_next_five_second_check(
        monkeypatch):
    signal = _gold_signal()
    listener._attach_gold_live_candidate(
        signal,
        datetime(2026, 8, 26, 15, 0, 0),
        provisional_sl=4642.0,
    )
    signal.candidate_hard_stop_requested_at[7001] = 94.9
    signal.candidate_sl_requested_levels[7001] = 4642.0
    requests = []

    async def fake_run(function, *args, **kwargs):
        if function is listener.executor.open_position_specs:
            return {
                7001: {
                    "entry": 4620.0,
                    "volume": 0.01,
                    "sl": 0.0,
                    "point": 0.01,
                },
            }
        if function is listener.executor.loss_stop_price:
            return 4642.0
        raise AssertionError(function)

    monkeypatch.setattr(listener, "_run", fake_run)
    monkeypatch.setattr(listener.time, "time", lambda: 100.0)
    monkeypatch.setattr(journal, "event", lambda *args, **kwargs: None)
    monkeypatch.setattr(journal, "anomaly", lambda *args, **kwargs: None)
    monkeypatch.setattr(listener, "notify", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(listener, "_schedule_detached", lambda *_args: None)
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_modify_sl",
        lambda signal, ticket, new_sl, label="", **kwargs: requests.append(
            (ticket, new_sl, kwargs.get("persist_until_signal_close"))
        ),
    )

    requested = await listener._ensure_gold_candidate_hard_stops(signal)

    assert requested == 1
    assert requests == [(7001, 4642.0, True)]


@pytest.mark.asyncio
async def test_gold_missing_sl_installs_hard_stop_before_retrying_be(monkeypatch):
    signal = _gold_signal()
    signal.candidate_be_tickets = [7001]
    listener._attach_gold_live_candidate(
        signal,
        datetime(2026, 8, 26, 15, 0, 0),
        provisional_sl=4642.0,
    )
    requests = []

    async def fake_run(function, *args, **kwargs):
        if function is listener.executor.open_position_specs:
            return {
                7001: {
                    "entry": 4620.0,
                    "volume": 0.01,
                    "sl": 0.0,
                    "point": 0.01,
                },
            }
        if function is listener.executor.loss_stop_price:
            return 4642.0
        raise AssertionError(function)

    monkeypatch.setattr(listener, "_run", fake_run)
    monkeypatch.setattr(journal, "event", lambda *args, **kwargs: None)
    monkeypatch.setattr(journal, "anomaly", lambda *args, **kwargs: None)
    monkeypatch.setattr(listener, "notify", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(listener, "_schedule_detached", lambda *_args: None)
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_modify_sl",
        lambda signal, ticket, new_sl, label="", **kwargs: requests.append(
            (ticket, new_sl, label, kwargs.get("persist_until_signal_close"))
        ),
    )

    requested = await listener._ensure_gold_candidate_hard_stops(
        signal,
        force=True,
    )

    assert requested == 1
    assert requests == [
        (7001, 4642.0, "GOLD HARD SL #7001 -> 4642.00", True),
    ]


@pytest.mark.asyncio
async def test_gold_scale_out_opens_four_extra_legs_with_provisional_sl(
        monkeypatch):
    signal = _gold_signal()
    listener._attach_gold_live_candidate(
        signal,
        datetime(2026, 8, 26, 15, 0, 0),
        provisional_sl=4642.0,
    )
    calls = []

    async def fake_run(function, *args, **kwargs):
        assert function is listener.executor.open_market_with_fill
        calls.append(args)
        return 7100 + len(calls), 4620.0 + len(calls) / 10

    monkeypatch.setattr(listener, "_run", fake_run)
    # The frozen candidate owns its five-leg entry contract. A legacy Canal 2
    # mode must not be able to disable it silently.
    monkeypatch.setattr(config, "STRATEGY_C2_ENTRY_MODE", "market_only")
    monkeypatch.setattr(
        config,
        "STRATEGY_MAX_PLANNED_LOTS_PER_SIGNAL",
        0.05,
    )
    monkeypatch.setattr(journal, "event", lambda *args, **kwargs: None)
    monkeypatch.setattr(journal, "anomaly", lambda *args, **kwargs: None)

    await listener._open_extra_legs(signal, signal.message_id)

    assert len(calls) == 4
    assert [call[0] for call in calls] == ["SELL"] * 4
    assert [call[1] for call in calls] == [0.01] * 4
    assert [call[2] for call in calls] == [4642.0] * 4
    assert [call[3] for call in calls] == [None] * 4
    assert [call[4] for call in calls] == [
        "c2_2054_B1_gv1",
        "c2_2054_B2_gv1",
        "c2_2054_B3_gv1",
        "c2_2054_B4_gv1",
    ]
    assert len(signal.all_filled_tickets) == 5


@pytest.mark.asyncio
async def test_gold_monitor_start_failure_remains_retryable(monkeypatch):
    signal = _gold_signal()
    listener._attach_gold_live_candidate(
        signal,
        datetime(2026, 8, 26, 15, 0, 0),
        provisional_sl=4642.0,
    )
    monkeypatch.setattr(
        listener.position_lifecycle_monitor,
        "start",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("monitor start failed")
        ),
    )

    with pytest.raises(RuntimeError, match="monitor start failed"):
        await listener._place_dca(signal)

    assert signal.dca_placed is False

    starts = []
    monkeypatch.setattr(
        listener.position_lifecycle_monitor,
        "start",
        lambda sig, levels: starts.append((sig, levels)),
    )

    await listener._place_dca(signal)

    assert signal.dca_placed is True
    assert starts == [(signal, [])]
