from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal

import numpy as np
import pytest

from research.causal_comparison import SequenceEvent
from research.causal_replay import CausalSignal, make_path, time_ns
from research.conditioned_management import condition_entries, replay_management
from research.dubai_iterative.dataset import ProviderEvent
from tests.test_causal_replay import BASE
from tools.run_causal_controls import policies


def inputs(channel="canal2", direction="BUY", count=1):
    genome = policies()[channel]
    signal = CausalSignal(channel + "_1", channel, direction, BASE, BASE, "revision")
    times = np.array([time_ns(BASE + timedelta(seconds=i)) for i in range(5)])
    prices = np.full(5, 100.0)
    path = make_path(signal, genome, market=(times, prices, prices + .2),
                     conversion=(times, np.ones(5), np.ones(5)),
                     cutoff=BASE + timedelta(seconds=4), contract_size=100,
                     currency_digits=2, max_fx_age_ms=5000,
                     market_sha256="a" * 64, conversion_sha256="b" * 64)
    entries = tuple(SequenceEvent(i + 1, "entry", BASE + timedelta(seconds=i), direction,
                                 Decimal("100.2") if direction == "BUY" else Decimal("100"),
                                 Decimal(str(volume)), Decimal(0), "market")
                    for i, volume in enumerate(genome.volume_weights[:count]))
    return path, genome, entries


@pytest.mark.parametrize("channel", ["canal1", "canal2"])
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("count", [1, 3])
def test_same_confirmed_entries_are_retained_by_all_three_engines(channel, direction, count):
    path, genome, entries = inputs(channel, direction, count)
    path = replace(path, provider_events=(ProviderEvent(BASE + timedelta(seconds=3), "CLOSE_ALL", {}),))
    report, events = replay_management(path, genome, entries)
    assert report["engine_mismatches"] == {"scalar": [], "fast": []}
    assert report["status"] == "evaluated_conditioned_management"
    assert [e for e in events if e.kind == "entry"] == list(entries)
    assert len([e for e in events if e.kind == "exit"]) == count
    assert report["full_live_parity_verified"] is False
    assert report["entry_decisions_verified"] is False


def test_future_realized_outcomes_and_protection_are_not_carried_into_conditioned_path():
    path, genome, entries = inputs()
    dirty = replace(path, actual_pnl_eur=Decimal(99999))
    conditioned, mirror = condition_entries(dirty, genome, entries)
    assert conditioned.actual_pnl_eur is None
    assert conditioned.entry_evidence_kind == "actual_mt5"
    assert mirror.entry_mode == "actual_mt5"
    for leg in conditioned.legs:
        assert (leg.closed_at, leg.close_price, leg.close_reason) == (None, None, None)
        assert leg.actual_pnl_eur == 0
        assert leg.tp_events == leg.sl_events == ()
    assert genome.entry_mode != "actual_mt5"
    assert path.entry_evidence_kind == "provider_signal"


@pytest.mark.parametrize("mutation,reason", [
    (lambda e: replace(e, kind="exit"), "entries only"),
    (lambda e: replace(e, slot=2), "contiguous"),
    (lambda e: replace(e, direction="SELL"), "direction"),
    (lambda e: replace(e, volume=Decimal(".5")), "volume"),
    (lambda e: replace(e, money=Decimal("-.1")), "opening costs"),
    (lambda e: replace(e, at=BASE - timedelta(seconds=1)), "window"),
    (lambda e: replace(e, at=BASE + timedelta(seconds=5)), "window"),
])
def test_unsupported_conditioning_cannot_silently_change_policy(mutation, reason):
    path, genome, entries = inputs()
    with pytest.raises(ValueError, match=reason):
        condition_entries(path, genome, (mutation(entries[0]),))


def test_empty_and_reordered_slots_are_not_invented():
    path, genome, entries = inputs(count=3)
    with pytest.raises(ValueError, match="nonempty"):
        condition_entries(path, genome, ())
    with pytest.raises(ValueError, match="contiguous"):
        condition_entries(path, genome, entries[::-1])


def test_real_exit_is_not_an_input_and_simulated_exit_can_differ():
    path, genome, entries = inputs()
    path = replace(path, bid=np.array([100., 101., 101., 101., 101.]),
                   ask=np.array([100.2, 101.2, 101.2, 101.2, 101.2]),
                   exit_quotes=np.array([100., 101., 101., 101., 101.]))
    report, events = replay_management(path, genome, entries)
    assert report["status"] == "evaluated_conditioned_management"
    exit_event = next(e for e in events if e.kind == "exit")
    assert exit_event.at == BASE + timedelta(seconds=1)
    assert exit_event.money == Decimal("2.00")


def test_missing_fx_remains_a_blocker_not_zero_profit():
    path, genome, entries = inputs()
    path = replace(path, fx_valid=np.zeros(5, dtype=bool))
    report, events = replay_management(path, genome, entries)
    assert report["status"] == "blocked"
    assert report["blockers"]
    assert events is None
