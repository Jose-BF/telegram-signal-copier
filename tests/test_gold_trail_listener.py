from __future__ import annotations

from datetime import date, datetime, timezone
import json

import pytest

import config
import gold_trail_live_candidate as gt
import listener
from state import Signal, StateManager


PUB = datetime(2026, 10, 5, 9, 17, 23, tzinfo=timezone.utc)


def _intent(message_id: int = 3850, direction: str = "BUY", published: datetime = PUB) -> listener._Canal2EntryIntent:
    return listener._Canal2EntryIntent(
        message_id=message_id,
        direction=direction,
        parsed={"direction": direction},
        raw_text=f"XAUUSD {direction} NOW",
        entry_timestamp=published.replace(tzinfo=None),
        telegram_timestamp=published,
        source_kind="telegram_now",
        command_key=f"{direction}_NOW",
    )


@pytest.fixture(autouse=True)
def _reset_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(listener, "_gold_trail_state_path", lambda: tmp_path / "gold_trail_state.json")
    listener._entry_execution_gate.reset()
    listener._canal2_opening_msg_ids.clear()
    listener._gold_trail_decided.clear()
    listener._gold_trail_day_losses.clear()
    listener._gold_trail_open_trades.clear()
    yield
    listener._entry_execution_gate.reset()
    listener._canal2_opening_msg_ids.clear()
    listener._gold_trail_decided.clear()
    listener._gold_trail_day_losses.clear()


def _patch(monkeypatch, *, mode: str, momentum: gt.Momentum):
    events, orders, sl_requests = [], [], []

    async def fake_run(fn, *args):
        return fn(*args)

    async def no_monitor(_signal):
        return None

    monkeypatch.setattr(listener, "state", StateManager())
    monkeypatch.setattr(config, "STRATEGY_C2_GOLD_NOW_555_ENABLED", False)
    monkeypatch.setattr(config, "STRATEGY_C2_GOLD_NOW_C490_ENABLED", False)
    monkeypatch.setattr(config, "STRATEGY_C2_GOLD_NOW_TRAIL_ENABLED", True)
    monkeypatch.setattr(config, "GOLD_TRAIL_MODE", mode)
    monkeypatch.setattr(config, "STRATEGY_MAX_PLANNED_LOTS_PER_SIGNAL", 0.05)
    monkeypatch.setattr(config, "LOT_SIZE", 0.01)
    monkeypatch.setattr(listener, "_run", fake_run)
    monkeypatch.setattr(listener, "_gold_trail_utc_offset", lambda tick: 10800)
    monkeypatch.setattr(listener.gold_trail_market, "momentum_now", lambda *a: momentum)
    monkeypatch.setattr(listener.executor, "account_evidence", lambda: {
        "trade_mode": 0, "trade_mode_name": "demo", "currency": "EUR", "login": 1, "server": "Vantage-Demo"})
    monkeypatch.setattr(listener.executor, "current_tick_safe", lambda: {
        "bid": 4199.8, "ask": 4200.0, "time": 1790000000, "time_msc": 1790000000123})

    def fake_open(direction, volume, sl, tp, comment, magic):
        orders.append({"direction": direction, "volume": volume, "sl": sl, "tp": tp, "comment": comment, "magic": magic})
        return (777001, 4200.1 if direction == "BUY" else 4199.7)

    monkeypatch.setattr(listener.executor, "open_market_with_fill", fake_open)
    monkeypatch.setattr(listener.pending_actions, "enqueue_modify_sl",
                        lambda signal, ticket, price, **kw: sl_requests.append((ticket, price, kw)))
    monkeypatch.setattr(listener, "_place_dca", no_monitor)
    monkeypatch.setattr(listener.journal, "begin_trade", lambda *a, **k: None)
    monkeypatch.setattr(listener.journal, "event", lambda sig, ev, **f: events.append((sig, ev, f)))
    monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **k: None)
    monkeypatch.setattr(listener.logger, "log_signal", lambda *a, **k: None)
    return events, orders, sl_requests


@pytest.mark.asyncio
async def test_desarrollada_follows_with_momentum_and_installs_stop(monkeypatch) -> None:
    events, orders, sl_requests = _patch(monkeypatch, mode="desarrollada", momentum=gt.Momentum(status="ok", mi=3, mi_rev=0))
    sig = await listener._open_canal2_intent(_intent())
    assert sig is not None
    assert orders == [{"direction": "BUY", "volume": 0.05, "sl": 4180.0, "tp": None, "comment": "c2_3850_gtr",
                       "magic": config.magic_for("canal2")}]
    assert sig.live_strategy_id == gt.CANDIDATE_ID
    assert sig.live_strategy_fingerprint == gt.GoldTrailPolicy(mode="desarrollada").fingerprint
    assert sig.direction == "BUY" and sig.candidate_trail_side == "follow"
    assert sig.candidate_hard_stops == {777001: 4180.1}
    assert sl_requests[0][:2] == (777001, 4180.1) and sl_requests[0][2]["persist_until_signal_close"] is True
    assert sig.effective_lot == 0.05
    assert sig.time_stop_at is None and sig.be_at_tp_index is None
    names = [e[1] for e in events]
    assert "gold_trail_decision" in names and "gold_trail_filled" in names
    assert 3850 in listener._gold_trail_decided


@pytest.mark.asyncio
async def test_candidata_reverses_signal_against_momentum(monkeypatch) -> None:
    events, orders, _ = _patch(monkeypatch, mode="candidata", momentum=gt.Momentum(status="ok", mi=1, mi_rev=2))
    sig = await listener._open_canal2_intent(_intent(direction="BUY"))
    assert orders[0]["direction"] == "SELL"
    assert orders[0]["sl"] == 4219.8
    assert sig.direction == "SELL" and sig.candidate_trail_side == "reverse"
    assert sig.candidate_provider_direction == "BUY"
    assert sig.candidate_hard_stops == {777001: 4219.7}


@pytest.mark.asyncio
async def test_round_minute_signal_is_skipped_once_and_never_reopened(monkeypatch) -> None:
    events, orders, _ = _patch(monkeypatch, mode="candidata", momentum=gt.Momentum(status="ok", mi=4, mi_rev=0))
    published = PUB.replace(minute=15, second=2)
    assert await listener._open_canal2_intent(_intent(published=published)) is None
    assert await listener._open_canal2_intent(_intent(published=published)) is None
    assert orders == []
    decisions = [e for e in events if e[1] == "gold_trail_decision"]
    assert len(decisions) == 1 and decisions[0][2]["decision"]["reason"] == "round_minute"
    assert not listener._canal2_open_in_progress(3850)


@pytest.mark.asyncio
async def test_day_rule_loss_blocks_that_side(monkeypatch) -> None:
    events, orders, _ = _patch(monkeypatch, mode="candidata", momentum=gt.Momentum(status="ok", mi=3, mi_rev=0))
    listener._gold_trail_day_losses.add((date(2026, 10, 5), "follow"))
    assert await listener._open_canal2_intent(_intent()) is None
    assert orders == []
    decision = [e for e in events if e[1] == "gold_trail_decision"][0][2]["decision"]
    assert decision["reason"] == "day_rule_loss_today"


@pytest.mark.asyncio
async def test_momentum_failure_never_opens(monkeypatch) -> None:
    _, orders, _ = _patch(monkeypatch, mode="candidata", momentum=gt.Momentum(status="ok", mi=3, mi_rev=0))

    def boom(*args):
        raise RuntimeError("mt5 caido")

    monkeypatch.setattr(listener.gold_trail_market, "momentum_now", boom)
    assert await listener._open_canal2_intent(_intent()) is None
    assert orders == []


@pytest.mark.asyncio
async def test_real_account_is_refused(monkeypatch) -> None:
    _, orders, _ = _patch(monkeypatch, mode="candidata", momentum=gt.Momentum(status="ok", mi=3, mi_rev=0))
    monkeypatch.setattr(listener.executor, "account_evidence", lambda: {
        "trade_mode": 2, "trade_mode_name": "real", "currency": "EUR"})
    with pytest.raises(gt.GoldTrailAccountError):
        await listener._open_canal2_intent(_intent())
    assert orders == []
    assert not listener._canal2_open_in_progress(3850)


@pytest.mark.asyncio
async def test_provider_actions_are_ignored(monkeypatch) -> None:
    events, _, _ = _patch(monkeypatch, mode="candidata", momentum=gt.Momentum(status="ok", mi=3, mi_rev=0))
    sig = await listener._open_canal2_intent(_intent())
    closes = []
    monkeypatch.setattr(listener.pending_actions, "enqueue_close_position", lambda *a, **k: closes.append(a))
    for action in ("CLOSE_ALL", "MOVE_SL_TO_BE", "CLOSE_PARTIAL"):
        result = await listener._execute_one_action(sig, {"action": action, "confidence": 0.99}, raw_text="close now")
        assert result == "ignored"
    assert closes == []
    assert any(e[1] == "gold_trail_provider_action_observed_not_applied" for e in events)


def test_record_close_state_file_and_restart(tmp_path, monkeypatch) -> None:
    rows = []
    monkeypatch.setattr(listener.journal, "event", lambda sig, ev, **f: rows.append({"sig": sig, "ev": ev, **f}))
    monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **k: None)
    sig = Signal(channel="canal2", message_id=3851, direction="SELL", timestamp=PUB.replace(tzinfo=None))
    sig.live_strategy_id = gt.CANDIDATE_ID
    sig.candidate_trail_side = "reverse"
    sig.telegram_entry_timestamp = PUB
    sig.market_ticket = 900001
    listener._gold_trail_open_trades[900001] = {"sig_id": "canal2_3851", "side": "reverse", "loss_day": "2026-10-05"}
    listener._gold_trail_decided.add(3851)
    listener._gold_trail_record_close(sig, -18.5)
    assert (date(2026, 10, 5), "reverse") in listener._gold_trail_day_losses
    assert 900001 not in listener._gold_trail_open_trades
    listener._gold_trail_record_close(sig, 4.2)
    assert len([r for r in rows if r["ev"] == "gold_trail_day_loss"]) == 1
    # a restart the same day restores decided messages and the loss; the next day the loss is gone
    listener._gold_trail_decided.clear()
    listener._gold_trail_day_losses.clear()
    decided, losses = listener.restore_gold_trail_state(now=datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc))
    assert (decided, losses) == (1, 1) and 3851 in listener._gold_trail_decided
    decided, losses = listener.restore_gold_trail_state(now=datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc))
    assert losses == 0


def test_trade_closed_while_bot_was_down_feeds_day_rule(monkeypatch) -> None:
    monkeypatch.setattr(listener.journal, "event", lambda *a, **k: None)
    listener._gold_trail_open_trades.update({
        1: {"sig_id": "canal2_1", "side": "follow", "loss_day": "2026-10-05"},
        2: {"sig_id": "canal2_2", "side": "reverse", "loss_day": "2026-10-05"},
        3: {"sig_id": "canal2_3", "side": "reverse", "loss_day": "2026-10-05"},
    })
    profits = {1: -20.3, 2: 5.1}
    n = listener.reconcile_gold_trail_closed_while_down({3}, lambda t: profits.get(t))
    assert n == 2
    assert (date(2026, 10, 5), "follow") in listener._gold_trail_day_losses
    assert (date(2026, 10, 5), "reverse") not in listener._gold_trail_day_losses
    assert set(listener._gold_trail_open_trades) == {3}


@pytest.mark.asyncio
async def test_non_now_entries_are_refused_under_trail(monkeypatch) -> None:
    events, orders, _ = _patch(monkeypatch, mode="candidata", momentum=gt.Momentum(status="ok", mi=3, mi_rev=0))
    from dataclasses import replace
    intent = replace(_intent(), source_kind="zone_first_touch")
    assert await listener._open_canal2_intent(intent) is None
    assert orders == []
    assert any(e[1] == "gold_trail_non_now_entry_refused" for e in events)
