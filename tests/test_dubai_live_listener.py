from datetime import datetime, timedelta

import pytest

import config
import journal
import listener
from dubai_live_candidate import CANDIDATE_FINGERPRINT, CANDIDATE_ID
from state import Signal


def test_candidate_attachment_anchors_price_to_fill_and_expiry_to_signal(
    monkeypatch,
):
    monkeypatch.setattr(config, "STRATEGY_C1_BALANCED_V1_ENABLED", True)
    opened_at = datetime(2026, 8, 23, 9, 30, 0)
    signal = Signal(
        channel="canal1",
        message_id=22001,
        direction="BUY",
        market_ticket=5001,
        market_fill_price=4200.0,
        entry_mode="scale_out",
        time_stop_at=opened_at + timedelta(minutes=60),
    )

    observed_at = opened_at - timedelta(seconds=3)
    attached = listener._attach_dubai_live_candidate(
        signal,
        opened_at,
        observed_at=observed_at,
    )

    assert attached is True
    assert signal.live_strategy_id == CANDIDATE_ID
    assert signal.live_strategy_fingerprint == CANDIDATE_FINGERPRINT
    assert signal.entry_mode == "adverse_ladder"
    assert signal.time_stop_at is None
    assert signal.be_at_tp_index is None
    assert signal.candidate_entry_anchor == 4200.0
    assert signal.candidate_first_fill_at == opened_at
    assert signal.candidate_entry_expires_at == (
        observed_at + timedelta(minutes=15)
    )
    assert signal.candidate_entry_legs == [
        {"index": 0, "volume": 0.01, "trigger_price": None},
        {"index": 1, "volume": 0.04, "trigger_price": 4196.0},
        {"index": 2, "volume": 0.04, "trigger_price": 4192.0},
    ]


def test_candidate_is_never_attached_to_gold_signals(monkeypatch):
    monkeypatch.setattr(config, "STRATEGY_C1_BALANCED_V1_ENABLED", True)
    signal = Signal(
        channel="canal2",
        message_id=380,
        direction="SELL",
        market_fill_price=4200.0,
    )

    assert listener._attach_dubai_live_candidate(
        signal, datetime.utcnow(),
    ) is False
    assert signal.live_strategy_id is None


def test_initial_volume_is_frozen_for_dubai_but_gold_keeps_its_config(monkeypatch):
    monkeypatch.setattr(config, "STRATEGY_C1_BALANCED_V1_ENABLED", True)
    monkeypatch.setattr(config, "LOT_SIZE", 0.03)

    assert listener._initial_market_lot("canal1") == 0.01
    assert listener._initial_market_lot("canal2") == 0.03


def test_candidate_market_comment_survives_a_crash_before_journal_attach(
    monkeypatch,
):
    monkeypatch.setattr(config, "STRATEGY_C1_BALANCED_V1_ENABLED", True)

    assert listener._market_comment("canal1", 22004) == "c1_22004_dv1"
    assert listener._market_comment("canal2", 380) == "c2_380"


@pytest.mark.asyncio
async def test_candidate_skips_the_legacy_immediate_scale_out(monkeypatch):
    signal = Signal(
        channel="canal1",
        message_id=22002,
        direction="SELL",
        market_ticket=5002,
        market_fill_price=4200.0,
        live_strategy_id=CANDIDATE_ID,
        live_strategy_fingerprint=CANDIDATE_FINGERPRINT,
    )

    async def fail_run(*args, **kwargs):
        raise AssertionError("candidate must not open immediate scale-out legs")

    monkeypatch.setattr(listener, "_run", fail_run)

    await listener._open_extra_legs(signal, signal.message_id)

    assert signal.extra_market_tickets == []


@pytest.mark.asyncio
async def test_candidate_monitor_starts_immediately_with_the_frozen_plan(
    monkeypatch,
):
    starts = []
    signal = Signal(
        channel="canal1",
        message_id=22003,
        direction="SELL",
        market_ticket=5003,
        market_fill_price=4200.0,
    )
    listener._attach_dubai_live_candidate(
        signal, datetime(2026, 8, 23, 10, 0, 0),
    )
    monkeypatch.setattr(
        listener.position_lifecycle_monitor,
        "start",
        lambda sig, levels: starts.append((sig, levels)),
    )

    await listener._place_dca(signal)

    assert signal.dca_placed is True
    assert starts == [(signal, [4204.0, 4208.0])]


def test_candidate_strategy_snapshot_reports_effective_rule_not_legacy_config(
    monkeypatch,
):
    signal = Signal(
        channel="canal1",
        message_id=22005,
        direction="BUY",
        market_ticket=5005,
        market_fill_price=4200.0,
    )
    listener._attach_dubai_live_candidate(
        signal, datetime(2026, 8, 23, 10, 0, 0),
    )
    events = []
    monkeypatch.setattr(
        journal,
        "event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )

    listener._log_strategy_snapshot(
        signal,
        num_entries=99,
        time_stop_min=999,
    )

    snapshot = events[0][2]
    assert snapshot["live_strategy_id"] == CANDIDATE_ID
    assert snapshot["num_entries"] == 3
    assert snapshot["volume_weights"] == [0.01, 0.04, 0.04]
    assert snapshot["entry_expiry_min"] == 15
    assert snapshot["time_stop_min"] == 40
    assert snapshot["basket_stop_eur"] == -25.0


def test_canal1_text_routing_cannot_reenable_legacy_candidate_management():
    signal = Signal(
        channel="canal1",
        message_id=22006,
        direction="SELL",
        market_ticket=5006,
        market_fill_price=4200.0,
    )
    listener._attach_dubai_live_candidate(
        signal, datetime(2026, 8, 23, 10, 0, 0),
    )

    listener._configure_canal1_signal_runtime(signal)

    assert signal.entry_mode == "adverse_ladder"
    assert signal.target_tp_index is None
    assert signal.be_at_tp_index is None
    assert signal.time_stop_at is None
    assert listener._should_report_canal1_naked(signal) is False


@pytest.mark.asyncio
async def test_dubai_hard_stop_protects_the_whole_open_basket(monkeypatch):
    signal = Signal(
        channel="canal1",
        message_id=22007,
        direction="BUY",
        market_ticket=5007,
        market_fill_price=4200.0,
        dca_tickets=[5008],
    )
    listener._attach_dubai_live_candidate(
        signal, datetime(2026, 8, 23, 10, 0, 0),
    )
    specs = {
        5007: {
            "entry": 4200.0,
            "volume": 0.01,
            "symbol": "XAUUSD",
            "sl": 0.0,
            "point": 0.01,
        },
        5008: {
            "entry": 4196.0,
            "volume": 0.04,
            "symbol": "XAUUSD",
            "sl": 0.0,
            "point": 0.01,
        },
    }
    requests = []

    async def fake_run(function, *args, **kwargs):
        if function is listener.executor.open_position_specs:
            return specs
        if function is listener.executor.basket_loss_stop_price:
            assert args[0] == "BUY"
            assert args[2] == 25.0
            return 4191.25
        raise AssertionError(function)

    monkeypatch.setattr(listener, "_run", fake_run)
    monkeypatch.setattr(journal, "event", lambda *args, **kwargs: None)
    monkeypatch.setattr(journal, "anomaly", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_modify_sl",
        lambda signal, ticket, new_sl, label="", **kwargs: requests.append(
            (ticket, new_sl, kwargs.get("persist_until_signal_close"))
        ),
    )

    requested = await listener._ensure_dubai_candidate_hard_stops(
        signal,
        force=True,
    )

    assert requested == 2
    assert requests == [(5007, 4191.25, True), (5008, 4191.25, True)]
    assert signal.candidate_hard_stops == {
        5007: 4191.25,
        5008: 4191.25,
    }


@pytest.mark.asyncio
async def test_dubai_hard_stop_does_not_loosen_a_stronger_installed_sl(
    monkeypatch,
):
    signal = Signal(
        channel="canal1",
        message_id=22008,
        direction="SELL",
        market_ticket=5009,
        market_fill_price=4200.0,
    )
    listener._attach_dubai_live_candidate(
        signal, datetime(2026, 8, 23, 10, 0, 0),
    )

    async def fake_run(function, *args, **kwargs):
        if function is listener.executor.open_position_specs:
            return {
                5009: {
                    "entry": 4200.0,
                    "volume": 0.01,
                    "symbol": "XAUUSD",
                    "sl": 4210.0,
                    "point": 0.01,
                },
            }
        if function is listener.executor.basket_loss_stop_price:
            return 4228.0
        raise AssertionError(function)

    monkeypatch.setattr(listener, "_run", fake_run)
    monkeypatch.setattr(journal, "event", lambda *args, **kwargs: None)
    monkeypatch.setattr(journal, "anomaly", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_modify_sl",
        lambda *_args, **_kwargs: pytest.fail("must not loosen broker SL"),
    )

    requested = await listener._ensure_dubai_candidate_hard_stops(
        signal,
        force=True,
    )

    assert requested == 0
    assert signal.candidate_sl_confirmed_tickets == [5009]
    assert signal.sl_by_ticket == {5009: 4210.0}
