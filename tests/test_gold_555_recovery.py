from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

import config
import gold_555_live_candidate
import main
import position_lifecycle_monitor
import state as state_module
from state import StateManager
from state import Signal


def _enable_only_gold_555(monkeypatch) -> None:
    monkeypatch.setattr(config, "STRATEGY_C1_BALANCED_V1_ENABLED", False)
    monkeypatch.setattr(config, "STRATEGY_C2_GOLD_NOW_C490_ENABLED", False)
    monkeypatch.setattr(config, "STRATEGY_C2_GOLD_NOW_555_ENABLED", True)
    monkeypatch.setattr(config, "GOLD_NOW_LIVE_POLICY", "555")
    monkeypatch.setattr(
        config,
        "GOLD_555_MAX_PLANNED_LOTS_PER_SIGNAL",
        0.16,
    )


def test_gold_555_startup_gate_rejects_real_account(monkeypatch) -> None:
    _enable_only_gold_555(monkeypatch)

    with pytest.raises(ValueError, match="demo"):
        main._assert_dubai_candidate_demo_account({
            "trade_mode": 2,
            "trade_mode_name": "real",
            "currency": "EUR",
        })


def test_gold_555_startup_gate_requires_broker_volume_contract(
    monkeypatch,
) -> None:
    _enable_only_gold_555(monkeypatch)

    main._assert_dubai_candidate_broker_volume(SimpleNamespace(
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
    ))
    with pytest.raises(ValueError, match="volumen"):
        main._assert_dubai_candidate_broker_volume(SimpleNamespace(
            volume_min=0.04,
            volume_max=100.0,
            volume_step=0.01,
        ))


def test_live_contract_exposes_exact_gold_555_policy(monkeypatch) -> None:
    _enable_only_gold_555(monkeypatch)
    monkeypatch.setattr(config, "LOT_SIZE", 0.01)
    monkeypatch.setattr(
        config,
        "STRATEGY_MAX_PLANNED_LOTS_PER_SIGNAL",
        0.05,
    )

    contract = main._live_strategy_contract()

    gold = contract["gold"]
    assert gold["strategy_id"] == gold_555_live_candidate.CANDIDATE_ID
    assert gold["strategy_fingerprint"] == (
        gold_555_live_candidate.CANDIDATE_FINGERPRINT
    )
    assert gold["scope"] == "telegram_now_only"
    assert gold["entry"] == {
        "mode": "adverse_then_reversal",
        "entry_adverse": 1.0,
        "entry_reversal": 1.5,
        "expiry_min": 30,
        "volumes": [0.04, 0.03, 0.03, 0.03, 0.03],
        "ladder_step": 1.5,
    }
    assert gold["target_steps"] == [0.5, 1.0, 1.5, 2.0, 2.5]
    assert gold["broker_sl"] == {
        "mode": "trailing_price_distance",
        "distance": 30.0,
        "per_leg_from_real_fill": True,
        "persistent_retry": True,
    }
    assert gold["basket_guard"] == {
        "enabled": True,
        "profit_arm": 30.0,
        "profit_giveback": 1.0,
        "non_negative_exit_min": 180,
        "poll_mode": "every_new_tick",
        "money_source": "realized_plus_floating_account_currency",
    }
    assert gold["provider_management_mode"] == "explicit_close_only"
    assert contract["risk"]["max_planned_lots_per_signal"] == 0.16
    assert contract["risk"]["legacy_max_planned_lots_per_signal"] == 0.05
    assert contract["risk"]["gold_555_max_planned_lots_per_signal"] == 0.16


def test_gold_555_contract_rejects_missing_dedicated_exposure_gate(
    monkeypatch,
) -> None:
    _enable_only_gold_555(monkeypatch)
    monkeypatch.setattr(
        config,
        "STRATEGY_MAX_PLANNED_LOTS_PER_SIGNAL",
        0.05,
    )
    monkeypatch.setattr(
        config,
        "GOLD_555_MAX_PLANNED_LOTS_PER_SIGNAL",
        0.05,
    )

    with pytest.raises(
        ValueError,
        match="GOLD_555_MAX_PLANNED_LOTS_PER_SIGNAL",
    ):
        main._live_strategy_contract()


def test_startup_status_names_gold_555_trial(monkeypatch) -> None:
    _enable_only_gold_555(monkeypatch)

    text = main._startup_status_message({
        "git_commit": "abc1234",
        "git_branch": "main",
        "git_dirty": False,
        "git_synced": True,
    })

    assert "Gold estrategia: 555 v1 (solo demo)" in text


def test_gold_555_runtime_hooks_restore_watch_and_start_tick_loop(
    monkeypatch,
    tmp_path,
) -> None:
    _enable_only_gold_555(monkeypatch)
    restored = []
    path = tmp_path / "events.jsonl"
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        main,
        "restore_gold_555_entry_watches_from_journal",
        lambda source: restored.append(source) or 2,
    )

    count = main._restore_live_candidate_runtime(path)
    loops = main._candidate_background_loops()

    assert count == 2
    assert restored == [path]
    assert loops == [main.gold_555_entry_watch_loop]


def test_restart_restores_flat_gold_555_with_remaining_entry_window(
    monkeypatch,
    tmp_path,
) -> None:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=20)
    events_file = tmp_path / "trade_events.jsonl"
    rows = [
        {
            "sig": "canal2_380",
            "ev": "signal_received",
            "channel": "canal2",
            "direction": "BUY",
            "tg_ts": (now - timedelta(minutes=10)).isoformat(),
            "telegram_entry_command_key": "BUY GOLD NOW",
            "entry_source_kind": "telegram_now",
            "live_strategy_id": gold_555_live_candidate.CANDIDATE_ID,
            "live_strategy_fingerprint": (
                gold_555_live_candidate.CANDIDATE_FINGERPRINT
            ),
            "message_revision_id": "msgrev_origin",
            "decision_id": "decision_origin",
        },
        {
            "sig": "canal2_380",
            "ev": "gold_555_first_leg_filled",
            "strategy_id": gold_555_live_candidate.CANDIDATE_ID,
            "strategy_fingerprint": (
                gold_555_live_candidate.CANDIDATE_FINGERPRINT
            ),
            "ticket": 1771000001,
            "fill_price": 4300.0,
            "exact_sl": 4270.0,
            "exact_tp": 4300.5,
            "entry_levels": [4300.0, 4298.5, 4297.0, 4295.5, 4294.0],
            "expires_at": expires_at.isoformat(),
            "ts": (now - timedelta(minutes=9)).isoformat(),
        },
    ]
    events_file.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    runtime_state = StateManager()
    started = []
    emitted = []
    monkeypatch.setattr(state_module, "state", runtime_state)
    monkeypatch.setattr(
        position_lifecycle_monitor,
        "start",
        lambda signal, levels: started.append((signal, levels)),
    )
    monkeypatch.setattr(
        main.journal,
        "event",
        lambda sig, ev, **fields: emitted.append((sig, ev, fields)),
    )

    restored = main._restore_flat_gold_555_entry_plans(
        events_file,
        now=now,
    )

    assert restored == 1
    signal = runtime_state.get("canal2", 380)
    assert signal is not None
    assert signal.status == "open"
    assert signal.market_ticket == 1771000001
    assert signal.market_fill_price == 4300.0
    assert signal.candidate_filled_leg_indexes == []
    assert signal.candidate_entry_expires_at == expires_at.replace(tzinfo=None)
    assert signal.candidate_entry_prices_by_ticket == {1771000001: 4300.0}
    assert signal.candidate_hard_stops == {1771000001: 4270.0}
    assert signal.tp_by_ticket == {1771000001: 4300.5}
    assert signal.source_message_revision_id == "msgrev_origin"
    assert signal.source_decision_id == "decision_origin"
    assert started == [(signal, [4298.5, 4297.0, 4295.5, 4294.0])]
    assert any(ev == "gold_555_flat_entry_plan_restored" for _, ev, _ in emitted)


def test_restart_does_not_restore_expired_or_closed_flat_gold_555(
    monkeypatch,
    tmp_path,
) -> None:
    now = datetime.now(timezone.utc)
    events_file = tmp_path / "trade_events.jsonl"
    rows = []
    for message_id, terminal in ((380, False), (381, True)):
        rows.extend([
            {
                "sig": f"canal2_{message_id}",
                "ev": "signal_received",
                "channel": "canal2",
                "direction": "SELL",
                "live_strategy_id": gold_555_live_candidate.CANDIDATE_ID,
                "live_strategy_fingerprint": (
                    gold_555_live_candidate.CANDIDATE_FINGERPRINT
                ),
            },
            {
                "sig": f"canal2_{message_id}",
                "ev": "gold_555_first_leg_filled",
                "strategy_id": gold_555_live_candidate.CANDIDATE_ID,
                "strategy_fingerprint": (
                    gold_555_live_candidate.CANDIDATE_FINGERPRINT
                ),
                "ticket": 1771000000 + message_id,
                "fill_price": 4300.0,
                "entry_levels": [4300.0, 4301.5, 4303.0, 4304.5, 4306.0],
                "expires_at": (
                    now + timedelta(minutes=20)
                    if terminal else now - timedelta(seconds=1)
                ).isoformat(),
            },
        ])
        if terminal:
            rows.append({
                "sig": f"canal2_{message_id}",
                "ev": "signal_closed",
            })
    events_file.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    runtime_state = StateManager()
    monkeypatch.setattr(state_module, "state", runtime_state)

    assert main._restore_flat_gold_555_entry_plans(
        events_file,
        now=now,
    ) == 0
    assert runtime_state.open_signals("canal2") == []


def test_orphan_finalizer_recognizes_restored_flat_plan_as_active(
    monkeypatch,
) -> None:
    runtime_state = StateManager()
    runtime_state.add(Signal(
        channel="canal2",
        message_id=380,
        direction="BUY",
        status="open",
        live_strategy_id=gold_555_live_candidate.CANDIDATE_ID,
    ))
    monkeypatch.setattr(state_module, "state", runtime_state)

    assert main._runtime_signal_is_open("canal2_380") is True
    assert main._runtime_signal_is_open("canal2_381") is False


def test_generic_naked_watchdog_cannot_overwrite_gold_555_protection(
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "STRATEGY_NAKED_PROTECTIVE_SL_ENABLED", True)
    signal = Signal(
        channel="canal2",
        message_id=380,
        direction="BUY",
        market_ticket=1000,
        market_fill_price=4300.0,
        live_strategy_id=gold_555_live_candidate.CANDIDATE_ID,
        live_strategy_fingerprint=(
            gold_555_live_candidate.CANDIDATE_FINGERPRINT
        ),
    )
    signal.candidate_hard_stops = {1000: 4270.0}

    assert main._should_apply_naked_protective_sl(signal) is False
    assert main._is_naked_watchdog_candidate(signal) is False


def test_resync_restores_exact_gold_555_ladder_without_reopening_closed_legs(
    monkeypatch,
    tmp_path,
) -> None:
    now = datetime.now(timezone.utc)
    opened_at = int(now.timestamp()) - 5
    expires_at = now + timedelta(minutes=25)
    levels = [4300.6, 4299.1, 4297.6, 4296.1, 4294.6]
    events_file = tmp_path / "trade_events.jsonl"
    rows = [
        {
            "sig": "canal2_380",
            "ev": "gold_555_first_leg_filled",
            "strategy_id": gold_555_live_candidate.CANDIDATE_ID,
            "strategy_fingerprint": (
                gold_555_live_candidate.CANDIDATE_FINGERPRINT
            ),
            "ticket": 1771000001,
            "fill_price": 4300.6,
            "entry_levels": levels,
            "expires_at": expires_at.isoformat(),
        },
        {
            "sig": "canal2_380",
            "ev": "dca_filled",
            "strategy_id": gold_555_live_candidate.CANDIDATE_ID,
            "candidate_leg_index": 1,
            "ticket": 1771000002,
            "fill_price": 4299.0,
        },
        {
            "sig": "canal2_380",
            "ev": "dca_filled",
            "strategy_id": gold_555_live_candidate.CANDIDATE_ID,
            "candidate_leg_index": 2,
            "ticket": 1771000003,
            "fill_price": 4297.5,
        },
        {
            "sig": "canal2_380",
            "ev": "gold_555_prolonged_exposure_alerted",
            "strategy_id": gold_555_live_candidate.CANDIDATE_ID,
            "strategy_fingerprint": (
                gold_555_live_candidate.CANDIDATE_FINGERPRINT
            ),
        },
    ]
    events_file.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    groups = {
        "canal2_380": {
            "channel": "canal2",
            "message_id": 380,
            "direction": "BUY",
            "market_ticket": 1771000001,
            "market_price": 4300.6,
            "market_sl": 4272.0,
            "market_tp": 4301.1,
            "market_open_time": opened_at,
            "extra_market_tickets": [1771000003],
            "double_market_tickets": [],
            "scale_out_leg_indexes": {1771000003: 2},
            "dca_tickets": [],
            "dca_leg_indexes": {},
            "live_strategy_marker": (
                gold_555_live_candidate.CANDIDATE_ID
            ),
            "position_entries": {
                1771000001: 4300.6,
                1771000003: 4297.5,
            },
            "position_volumes": {
                1771000001: 0.04,
                1771000003: 0.03,
            },
            "position_stops": {
                1771000001: 4272.0,
                1771000003: 4269.0,
            },
            "position_targets": {
                1771000001: 4301.1,
                1771000003: 4299.0,
            },
        }
    }
    runtime_state = StateManager()
    started = []

    monkeypatch.setattr(
        main.executor,
        "list_open_positions_grouped",
        lambda: groups,
    )
    monkeypatch.setattr(
        main.causal_trace,
        "load_signal_origin_index",
        lambda _path: ({}, {}, []),
    )
    monkeypatch.setattr(main.journal, "EVENTS_FILE", events_file)
    monkeypatch.setattr(main.journal, "event", lambda *args, **kwargs: None)
    monkeypatch.setattr(main.journal, "anomaly", lambda *args, **kwargs: None)
    monkeypatch.setattr(state_module, "state", runtime_state)
    monkeypatch.setattr(
        position_lifecycle_monitor,
        "start",
        lambda signal, levels_arg: started.append((signal, levels_arg)),
    )
    monkeypatch.setattr(
        main,
        "_assert_dubai_candidate_demo_account",
        lambda evidence=None, **kwargs: None,
    )
    monkeypatch.setattr(
        main.executor,
        "mt5",
        SimpleNamespace(symbol_info_tick=lambda _symbol: None),
    )

    main._resync_orphan_positions()

    signal = runtime_state.get("canal2", 380)
    assert signal is not None
    assert signal.live_strategy_id == gold_555_live_candidate.CANDIDATE_ID
    assert signal.live_strategy_fingerprint == (
        gold_555_live_candidate.CANDIDATE_FINGERPRINT
    )
    assert signal.entry_mode == "adverse_ladder"
    assert signal.candidate_entry_anchor == 4300.6
    assert signal.candidate_entry_expires_at == expires_at.replace(tzinfo=None)
    assert signal.candidate_entry_legs == [
        {
            "index": index,
            "volume": (0.04, 0.03, 0.03, 0.03, 0.03)[index],
            "trigger_price": levels[index],
            "target_step": (0.5, 1.0, 1.5, 2.0, 2.5)[index],
        }
        for index in range(5)
    ]
    assert signal.candidate_filled_leg_indexes == [1, 2]
    assert signal.candidate_entry_prices_by_ticket == {
        1771000001: 4300.6,
        1771000003: 4297.5,
    }
    assert signal.candidate_hard_stops == {
        1771000001: 4272.0,
        1771000003: 4269.0,
    }
    assert signal.sl_by_ticket == {
        1771000001: 4272.0,
        1771000003: 4269.0,
    }
    assert signal.tp_by_ticket == {
        1771000001: 4301.1,
        1771000003: 4299.0,
    }
    assert signal.candidate_prolonged_exposure_alerted is True
    assert signal.target_tp_index is None
    assert signal.be_at_tp_index is None
    assert signal.time_stop_at is None
    assert started == [(signal, [])]


def test_startup_requeues_missing_provider_close_after_resync(
    monkeypatch,
) -> None:
    runtime_state = StateManager()
    signal = Signal(
        channel="canal2",
        message_id=380,
        direction="BUY",
        market_ticket=1771000001,
        extra_market_tickets=[1771000002],
        status="open",
        live_strategy_id=gold_555_live_candidate.CANDIDATE_ID,
        live_strategy_fingerprint=(
            gold_555_live_candidate.CANDIDATE_FINGERPRINT
        ),
        requested_close_reason="PROVIDER_CLOSE",
    )
    runtime_state.add(signal)
    queued = []
    events = []
    monkeypatch.setattr(state_module, "state", runtime_state)
    monkeypatch.setattr(
        main.pending_actions,
        "snapshot",
        lambda: [{
            "sig_id": "canal2_380",
            "kind": "CLOSE_POSITION",
            "ticket": 1771000001,
            "state": "retrying",
        }],
    )
    monkeypatch.setattr(
        main.pending_actions,
        "enqueue_close_position",
        lambda sig, ticket, **kwargs: queued.append(
            (sig, ticket, kwargs)
        ),
    )
    monkeypatch.setattr(
        main.journal,
        "event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )

    recovered = main._recover_requested_candidate_closes()

    assert recovered == 1
    assert queued == [(
        signal,
        1771000002,
        {
            "label": "RECOVER_PROVIDER_CLOSE #1771000002",
            "persist_until_signal_close": True,
        },
    )]
    assert events == [(
        "canal2_380",
        "provider_close_requeued_after_restart",
        {
            "strategy_id": gold_555_live_candidate.CANDIDATE_ID,
            "strategy_fingerprint": (
                gold_555_live_candidate.CANDIDATE_FINGERPRINT
            ),
            "requested_close_reason": "PROVIDER_CLOSE",
            "missing_close_tickets": [1771000002],
            "already_queued_tickets": [1771000001],
        },
    )]


def test_retraction_is_recovered_as_durable_close_request(tmp_path) -> None:
    events_file = tmp_path / "trade_events.jsonl"
    rows = [
        {
            "sig": "canal2_380",
            "ev": "gold_555_first_leg_filled",
            "strategy_id": gold_555_live_candidate.CANDIDATE_ID,
            "strategy_fingerprint": (
                gold_555_live_candidate.CANDIDATE_FINGERPRINT
            ),
            "fill_price": 4300.0,
            "entry_levels": [4300.0, 4298.5, 4297.0, 4295.5, 4294.0],
            "expires_at": "2026-08-27T09:30:00+00:00",
        },
        {
            "sig": "canal2_380",
            "ev": "gold_555_provider_retraction_close_requested",
            "strategy_id": gold_555_live_candidate.CANDIDATE_ID,
            "strategy_fingerprint": (
                gold_555_live_candidate.CANDIDATE_FINGERPRINT
            ),
        },
    ]
    events_file.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    metadata = main._load_gold_555_candidate_metadata(
        events_file,
        ["canal2_380"],
    )

    assert metadata["canal2_380"]["provider_close_requested"] is True
    assert metadata["canal2_380"]["requested_close_reason"] == (
        "PROVIDER_RETRACTED"
    )


def test_close_racing_unjournaled_fill_is_recovered_from_mt5_identity(
    tmp_path,
) -> None:
    events_file = tmp_path / "trade_events.jsonl"
    events_file.write_text(
        json.dumps({
            "sig": "canal2_380",
            "ev": "gold_555_provider_close_during_open",
            "strategy_id": gold_555_live_candidate.CANDIDATE_ID,
            "strategy_fingerprint": (
                gold_555_live_candidate.CANDIDATE_FINGERPRINT
            ),
            "classified_action": "CLOSE_ALL",
        }) + "\n",
        encoding="utf-8",
    )

    metadata = main._load_gold_555_candidate_metadata(
        events_file,
        ["canal2_380"],
    )

    assert metadata["canal2_380"]["provider_close_requested"] is True
    assert metadata["canal2_380"]["requested_close_reason"] == (
        "PROVIDER_CLOSE"
    )
