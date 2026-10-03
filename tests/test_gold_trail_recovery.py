from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import config
import executor
import gold_trail_live_candidate as gt
import main
from state import Signal


def _enable_only_gold_trail(monkeypatch, mode: str = "candidata") -> None:
    monkeypatch.setattr(config, "STRATEGY_C1_BALANCED_V1_ENABLED", False)
    monkeypatch.setattr(config, "STRATEGY_C2_GOLD_NOW_C490_ENABLED", False)
    monkeypatch.setattr(config, "STRATEGY_C2_GOLD_NOW_555_ENABLED", False)
    monkeypatch.setattr(config, "STRATEGY_C2_GOLD_NOW_TRAIL_ENABLED", True)
    monkeypatch.setattr(config, "GOLD_NOW_LIVE_POLICY", mode)
    monkeypatch.setattr(config, "GOLD_TRAIL_MODE", mode)
    monkeypatch.setattr(config, "STRATEGY_MAX_PLANNED_LOTS_PER_SIGNAL", 0.05)
    monkeypatch.setattr(config, "LOT_SIZE", 0.01)


def _group(stop: float | None = 4205.0) -> dict:
    return {
        "market_price": 4200.0,
        "position_entries": {777001: 4200.0},
        "position_stops": {777001: stop},
        "position_volumes": {777001: 0.05},
    }


def test_restore_rebuilds_trail_signal_from_fill_record() -> None:
    sig = Signal(channel="canal2", message_id=3850, direction="BUY", market_ticket=777001, market_fill_price=4200.0)
    opened = datetime(2026, 10, 5, 9, 0, 3)
    identity = {"strategy_fingerprint": gt.GoldTrailPolicy(mode="candidata").fingerprint, "side": "reverse",
                "provider_direction": "SELL", "time_exit_at": "2026-10-05T15:00:03"}
    errors = main._restore_gold_trail_signal(sig, _group(), identity, opened)
    assert errors == []
    assert sig.live_strategy_id == gt.CANDIDATE_ID
    assert sig.candidate_trail_side == "reverse" and sig.candidate_provider_direction == "SELL"
    assert sig.candidate_time_exit_at == datetime(2026, 10, 5, 15, 0, 3)
    assert sig.candidate_hard_stops == {777001: 4205.0}
    assert sig.candidate_best_price == 4208.0           # stop 4205 locks profit: best >= stop + 3
    assert sig.time_stop_at is None and sig.be_at_tp_index is None
    assert sig.effective_lot == 0.05


def test_restore_without_fill_record_keeps_managing_with_defaults(monkeypatch) -> None:
    _enable_only_gold_trail(monkeypatch, "desarrollada")
    sig = Signal(channel="canal2", message_id=3850, direction="SELL", market_ticket=777001, market_fill_price=4200.0)
    opened = datetime(2026, 10, 5, 9, 0, 3)
    errors = main._restore_gold_trail_signal(sig, _group(stop=4220.0), {}, opened)
    assert "gold_trail_fill_record_missing_or_fingerprint_mismatch" in errors
    assert sig.live_strategy_fingerprint == gt.GoldTrailPolicy(mode="desarrollada").fingerprint
    assert sig.candidate_time_exit_at == opened + timedelta(hours=6)
    assert sig.candidate_best_price is None              # initial stop: nothing locked yet


def test_trail_owns_its_protection_so_naked_watchdog_stays_away(monkeypatch) -> None:
    monkeypatch.setattr(config, "STRATEGY_NAKED_PROTECTIVE_SL_ENABLED", True)
    sig = Signal(channel="canal2", message_id=3850, direction="BUY", market_ticket=777001, market_fill_price=4200.0)
    sig.live_strategy_id = gt.CANDIDATE_ID
    sig.live_strategy_fingerprint = gt.GoldTrailPolicy().fingerprint
    assert main._candidate_owns_protection(sig) is True
    assert main._should_apply_naked_protective_sl(sig) is False
    assert main._is_naked_watchdog_candidate(sig) is False
    sig.live_strategy_fingerprint = "otro"
    assert main._candidate_owns_protection(sig) is False


def test_live_contract_and_startup_message(monkeypatch) -> None:
    _enable_only_gold_trail(monkeypatch)
    contract = main._live_strategy_contract()
    gold = contract["gold"]
    assert gold["strategy_id"] == gt.CANDIDATE_ID
    assert gold["strategy_fingerprint"] == gt.GoldTrailPolicy(mode="candidata").fingerprint
    assert gold["filters"]["reverse_enabled"] is True and gold["filters"]["skip_round_minutes"] is True
    assert gold["broker_sl"]["initial_distance"] == 20.0 and gold["broker_sl"]["following_step"] == 0.25
    assert gold["entry"] == {"mode": "market_now", "volume": 0.05}
    assert gold["provider_management_mode"] == "ignore"
    text = main._startup_status_message({"git_commit": "abc1234", "git_branch": "main", "git_dirty": False,
                                         "git_synced": True})
    assert "Gold estrategia: candidata" in text


def test_contract_refuses_volume_above_cap(monkeypatch) -> None:
    _enable_only_gold_trail(monkeypatch)
    monkeypatch.setattr(config, "STRATEGY_MAX_PLANNED_LOTS_PER_SIGNAL", 0.03)
    with pytest.raises(ValueError, match="Gold trail"):
        main._live_strategy_contract()


def test_startup_gates_cover_trail(monkeypatch) -> None:
    _enable_only_gold_trail(monkeypatch)
    with pytest.raises(ValueError, match="demo"):
        main._assert_dubai_candidate_demo_account({"trade_mode": 2, "trade_mode_name": "real", "currency": "EUR"})
    main._assert_dubai_candidate_broker_volume(SimpleNamespace(volume_min=0.01, volume_max=100.0, volume_step=0.01))
    with pytest.raises(ValueError, match="volumen"):
        main._assert_dubai_candidate_broker_volume(SimpleNamespace(volume_min=0.1, volume_max=100.0, volume_step=0.01))


def test_resync_grouping_marks_trail_positions(monkeypatch) -> None:
    position = SimpleNamespace(ticket=777001, magic=config.MT5_MAGIC_CANAL2, comment="c2_3850_gtr",
                               type=executor.mt5.ORDER_TYPE_BUY, price_open=4200.0, sl=4180.0, tp=0.0,
                               volume=0.05, time=1790000000)
    monkeypatch.setattr(executor.mt5, "positions_get", lambda: [position])
    groups = executor.list_open_positions_grouped()
    assert groups["canal2_3850"]["live_strategy_marker"] == gt.CANDIDATE_ID
    assert groups["canal2_3850"]["market_ticket"] == 777001
