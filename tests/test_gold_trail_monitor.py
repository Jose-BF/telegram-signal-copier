from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import gold_trail_live_candidate as gt
import position_lifecycle_monitor as plm
from state import Signal


def _signal(direction: str = "BUY", entry: float = 4200.0, mode: str = "candidata") -> Signal:
    sig = Signal(channel="canal2", message_id=3850, direction=direction, timestamp=datetime(2026, 10, 5, 9, 0))
    sig.market_ticket = 777001
    sig.market_fill_price = entry
    sig.live_strategy_id = gt.CANDIDATE_ID
    sig.live_strategy_fingerprint = gt.GoldTrailPolicy(mode=mode).fingerprint
    sig.candidate_entry_prices_by_ticket = {777001: entry}
    policy = gt.GoldTrailPolicy(mode=mode)
    sig.candidate_hard_stops = {777001: policy.initial_stop(direction, entry)}
    sig.candidate_time_exit_at = datetime(2026, 10, 5, 15, 0)
    sig.candidate_trail_side = "follow"
    return sig


def _tick(bid: float, ask: float | None = None, msc: int = 1):
    return SimpleNamespace(bid=bid, ask=ask if ask is not None else bid + 0.2, time_msc=msc)


@pytest.fixture
def recorder(monkeypatch):
    calls = {"sl": [], "close": [], "events": []}
    monkeypatch.setattr(plm.pending_actions, "enqueue_modify_sl",
                        lambda signal, ticket, price, **kw: calls["sl"].append((ticket, price, kw)))
    monkeypatch.setattr(plm.pending_actions, "enqueue_close_position",
                        lambda signal, ticket, **kw: calls["close"].append((ticket, kw)))
    monkeypatch.setattr(plm, "_journal_event", lambda sig, ev, **f: calls["events"].append((ev, f)))
    monkeypatch.setattr(plm, "_gold_trail_stops_level", lambda: 0.20)
    return calls


NOW = datetime(2026, 10, 5, 10, 0)


def test_stop_follows_after_plus_five_in_steps_and_throttled(recorder) -> None:
    sig = _signal()
    assert plm._apply_gold_trail(sig, _tick(4204.9), now_utc=NOW, now_monotonic=10.0) == "none"
    assert sig.candidate_best_price == 4204.9
    assert plm._apply_gold_trail(sig, _tick(4205.0), now_utc=NOW, now_monotonic=11.0) == "stop"
    assert recorder["sl"][-1][:2] == (777001, 4202.0)
    assert recorder["sl"][-1][2]["persist_until_signal_close"] is True
    # within the same second: best is tracked but no new request
    assert plm._apply_gold_trail(sig, _tick(4207.0), now_utc=NOW, now_monotonic=11.5) == "none"
    assert sig.candidate_best_price == 4207.0
    # next second: uses the best seen meanwhile even if price came back
    assert plm._apply_gold_trail(sig, _tick(4206.0), now_utc=NOW, now_monotonic=12.1) == "stop"
    assert recorder["sl"][-1][1] == 4204.0
    # an improvement below 0.25 $ is not sent
    assert plm._apply_gold_trail(sig, _tick(4207.2), now_utc=NOW, now_monotonic=14.0) == "none"
    assert len(recorder["sl"]) == 2


def test_sell_mirror(recorder) -> None:
    sig = _signal(direction="SELL", entry=4200.0)
    assert plm._apply_gold_trail(sig, _tick(4194.6, 4194.8), now_utc=NOW, now_monotonic=5.0) == "stop"
    assert recorder["sl"][-1][1] == 4197.8


def test_time_exit_closes_every_ticket_once(recorder) -> None:
    sig = _signal()
    late = datetime(2026, 10, 5, 15, 0, 1)
    assert plm._apply_gold_trail(sig, _tick(4201.0), now_utc=late, now_monotonic=1.0) == "time_exit"
    assert recorder["close"] == [(777001, {"label": "GOLD_TRAIL_TIME_EXIT #777001", "persist_until_signal_close": True})]
    assert sig.requested_close_reason == "GOLD_TRAIL_TIME_EXIT"
    assert plm._apply_gold_trail(sig, _tick(4230.0), now_utc=late + timedelta(seconds=5), now_monotonic=9.0) == "none"
    assert len(recorder["close"]) == 1 and recorder["sl"] == []


def test_other_strategies_and_bad_fingerprint(recorder) -> None:
    other = _signal()
    other.live_strategy_id = "gold_now_555_v1"
    assert plm._apply_gold_trail(other, _tick(4210.0), now_utc=NOW, now_monotonic=1.0) == "none"
    broken = _signal()
    broken.live_strategy_fingerprint = "otro"
    with pytest.raises(ValueError):
        plm._apply_gold_trail(broken, _tick(4210.0), now_utc=NOW, now_monotonic=1.0)


def test_monitor_does_not_skip_trail_signals() -> None:
    sig = _signal()
    assert not plm._basket_guard_enabled_for(sig)
    # run() would return immediately without levels, time stop, BE or basket guard; the trail policy must keep it alive
    import inspect
    source = inspect.getsource(plm.run)
    assert "signal.live_strategy_id != gold_trail_live_candidate.CANDIDATE_ID" in source
