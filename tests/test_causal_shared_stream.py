from dataclasses import replace
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

import research.causal_shared_stream as bridge
from research.causal_canal1_stream import compile_canal1_stream
from research.causal_lifecycle import LifecycleTiming
from research.causal_replay import CausalSignal
from research.causal_shared_stream import (
    run_incremental_shared_canal1_stream, run_shared_canal1_stream,
)
from research.dubai_iterative.client import ClientProfile
from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.engine import ExecutionAssumptions
from research.dubai_iterative.dataset import ProviderEvent
from research.dubai_iterative.shared_replay import SharedReplayProfile
from tests.test_iterative_market import market as market_profile
from tests.test_iterative_protection import profile as protection_profile


BASE = datetime(2026, 9, 17, 14, tzinfo=timezone.utc)
BASE_NS = int(BASE.timestamp()) * 1_000_000_000


def raw(message_id, seconds, text=None, *, sticker=None, reply=None):
    at = (BASE + timedelta(seconds=seconds)).isoformat()
    return {"ev": "telegram_raw", "channel": "canal1", "message_id": message_id,
            "message_revision_id": f"rev-{message_id}", "date_utc": at,
            "ts": at, "text": text, "is_edit": False, "edit_date_utc": None,
            "reply_to_msg_id": reply, "sticker_id": sticker}


def case():
    stream = compile_canal1_stream(
        [raw(1, 0.5, sticker="buy"),
         raw(2, 30.5, "BUY GOLD NOW 4366 TP1: 4370 SL: 4355"),
         raw(3, 40.5, "Move SL to BE", reply=2)],
        start=BASE, cutoff=BASE + timedelta(seconds=100),
        sticker_directions={"buy": "BUY"}, max_entry_age_s=120)
    times = BASE_NS + np.arange(101, dtype=np.int64) * 1_000_000_000
    market = (times, np.full(101, 100.0), np.full(101, 100.2))
    conversion = (times, np.ones(101), np.ones(101))
    genome = StrategyGenome(schema_version=2, entry_mode="signal_market",
                            leg_count=1, volume_weights=(0.01,),
                            target_mode="none", stop_mode="none", be_mode="none",
                            time_exit_mode="always", time_exit_min=1,
                            provider_management_mode="exact")
    execution = ExecutionAssumptions(protection=protection_profile(),
                                     market=market_profile(), client=ClientProfile())
    other_at = BASE + timedelta(seconds=5, milliseconds=500)
    other = CausalSignal("canal2_20", "canal2", "SELL", other_at, other_at,
                         "rev-canal2-20")

    return stream, dict(
        genomes={"canal1": genome, "canal2": genome},
        executions={"canal1": execution, "canal2": execution},
        profile=SharedReplayProfile(account_currency="EUR"),
        timing=LifecycleTiming(finalization_delay_s=5),
        market=market, conversion=conversion, start=BASE,
        cutoff=BASE + timedelta(seconds=100), contract_size=100.0,
        currency_digits=2, max_fx_age_ms=5000,
        market_sha256="a" * 64, conversion_sha256="b" * 64,
        other_signals=(other,), initial_universe_complete=True)


def test_shared_stream_routes_text_and_management_from_joint_engine_prefix():
    stream, options = case()
    report = run_shared_canal1_stream(stream, **options)

    assert [row["status"] for row in report["decisions"]] == [
        "sticker_entry", "targets_open_signal", "management_targets_signal"]
    assert report["refresh_count"] == 3
    assert [row[1] for row in report["shared"].baskets] == ["canal2_20", "canal1_1"]
    assert any(point.positions for point in report["shared"].risk)
    assert report["shared"].risk_grid
    assert any(sum(bool(point.positions) for _, _, point in frame.states if point) == 2
               for frame in report["shared"].risk_grid)
    assert report["full_live_parity_verified"] is False


def test_shared_stream_rejects_a_recomputed_past_risk_mark(monkeypatch):
    stream, options = case()
    original = bridge.simulate_shared
    calls = 0

    def corrupted(*args, **kwargs):
        nonlocal calls
        report = original(*args, **kwargs)
        calls += 1
        if calls != 2:
            return report
        first = report.risk[0]
        changed = replace(first, realized_minor=(first.realized_minor or 0) + 1)
        return replace(report, risk=(changed, *report.risk[1:]))

    monkeypatch.setattr(bridge, "simulate_shared", corrupted)
    with pytest.raises(ValueError, match="noncausal shared replay risk prefix mutation"):
        run_shared_canal1_stream(stream, **options)


def test_shared_stream_rejects_unknown_same_clock_order():
    stream, options = case()
    times, bid, ask = options["market"]
    tied = times.copy()
    tied[1] = BASE_NS + 500_000_000
    options["market"] = (tied, bid, ask)

    with pytest.raises(ValueError, match="same-clock message and quote order unresolved"):
        run_shared_canal1_stream(stream, **options)


def test_shared_stream_has_a_finite_replay_budget():
    stream, options = case()
    options["max_replays"] = 1

    with pytest.raises(TimeoutError, match="replay budget exhausted"):
        run_shared_canal1_stream(stream, **options)


@pytest.mark.parametrize("text_second,expected_text", [
    (30.5, "targets_open_signal"), (70.5, "eligible_text_fallback")])
def test_incremental_shared_stream_preserves_admitted_risk_and_transport(
        text_second, expected_text):
    _, options = case()
    stream = compile_canal1_stream(
        [raw(1, 0.5, sticker="buy"),
         raw(2, text_second, "BUY GOLD NOW 4366 TP1: 4370 SL: 4355"),
         raw(3, text_second + 10, "Move SL to BE", reply=2)],
        start=BASE, cutoff=BASE + timedelta(seconds=100),
        sticker_directions={"buy": "BUY"}, max_entry_age_s=120)
    control = run_shared_canal1_stream(stream, **options)
    actual = run_incremental_shared_canal1_stream(stream, **options)
    admitted = {signal_id for _, signal_id, _ in control["shared"].baskets}

    assert actual["decisions"][1]["status"] == expected_text
    assert actual["refresh_count"] == 1
    assert actual["shared"].dynamic_basket_admissions
    if expected_text == "targets_open_signal":
        assert actual["decisions"] == control["decisions"]
        assert [(channel, signal_id, result) for channel, signal_id, result
                in actual["shared"].baskets if signal_id in admitted] == list(
                    control["shared"].baskets)
        assert tuple(point for point in actual["shared"].risk
                     if point.signal_id in admitted) == control["shared"].risk
        assert tuple(replace(frame, states=tuple(state for state in frame.states
                                           if state[1] in admitted))
                     for frame in actual["shared"].risk_grid) == control["shared"].risk_grid
        assert actual["shared"].transport == control["shared"].transport
        assert actual["shared"].blockers == control["shared"].blockers
    else:
        boundary = BASE_NS + int(text_second * 1_000_000_000)
        assert control["decisions"][1]["status"] == "blocked_shared_world_incomplete"
        assert actual["decisions"][2]["status"] == "management_targets_signal"
        assert actual["shared"].risk_grid[:70] == control["shared"].risk_grid[:70]
        assert tuple(point for point in actual["shared"].risk if point.time_ns < boundary) == (
            tuple(point for point in control["shared"].risk if point.time_ns < boundary))
        assert actual["status"] == "blocked"
    delivered = dict(actual["shared"].dynamic_provider_events)
    if expected_text == "targets_open_signal":
        assert [event.action for event in delivered["canal1_1"]] == [
            "LEVEL_UPDATE", "MOVE_SL_TO_BE"]
        assert "canal1_2" not in admitted


def test_incremental_shared_stream_keeps_unverified_universe_dormant():
    stream, options = case()
    options["initial_universe_complete"] = False
    report = run_incremental_shared_canal1_stream(stream, **options)

    assert all(row["status"] == "blocked_incomplete_prior_universe"
               for row in report["decisions"])
    assert report["shared"].dynamic_basket_admissions == ()
    assert all(result.entries == () for channel, _, result
               in report["shared"].baskets if channel == "canal1")
    assert report["status"] == "blocked"


def test_incremental_shared_stream_delivers_other_channel_management():
    stream, options = case()
    event = ProviderEvent(BASE + timedelta(seconds=15, milliseconds=500),
                          "CLOSE_ALL", {})
    other = options["other_signals"][0]
    options["other_signals"] = (replace(other, provider_events=(event,)),)
    control = run_shared_canal1_stream(stream, **options)
    actual = run_incremental_shared_canal1_stream(stream, **options)

    assert actual["decisions"] == control["decisions"]
    admitted = {signal_id for _, signal_id, _ in control["shared"].baskets}
    assert tuple(row for row in actual["shared"].baskets if row[1] in admitted) == (
        control["shared"].baskets)
    assert tuple(point for point in actual["shared"].risk
                 if point.signal_id != "canal1_2") == control["shared"].risk
    assert actual["shared"].transport == control["shared"].transport
    assert dict(actual["shared"].dynamic_provider_events)[other.signal_id] == (event,)
