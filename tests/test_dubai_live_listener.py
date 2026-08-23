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
