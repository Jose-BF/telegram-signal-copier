"""Tests for production startup confirmation and orphan recovery."""

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import listener
import main
import position_lifecycle_monitor
import state as state_module
from state import StateManager


def test_startup_status_message_confirms_active_production_version():
    text = main._startup_status_message({
        "git_commit": "0457a0e",
        "git_branch": "main",
        "git_dirty": False,
        "git_synced": True,
    }, money_capture_ready=True)

    assert "BOT ACTIVO" in text
    assert "Version: 0457a0e" in text
    assert "Rama: main" in text
    assert "Codigo: limpio y sincronizado" in text
    assert "MT5: conectado" in text
    assert "Telegram: canales 1 y 2 activos" in text
    assert "Dubai Investing:" in text
    assert "Gold Signals:" in text
    assert "Registro simulacion: activo" in text


def test_startup_status_message_exposes_incomplete_simulation_capture():
    text = main._startup_status_message({
        "git_commit": "0457a0e",
        "git_branch": "main",
        "git_dirty": False,
        "git_synced": True,
    }, money_capture_ready=False)

    assert "Registro simulacion: INCOMPLETO" in text
    assert "El bot sigue operando" in text


def test_startup_status_message_warns_about_unverified_git_state():
    text = main._startup_status_message({
        "git_commit": None,
        "git_branch": "HEAD",
        "git_dirty": True,
        "git_synced": False,
        "git_verification_error": "sin atestacion del supervisor",
    })

    assert "Version: desconocida" in text
    assert "Rama: HEAD" in text
    assert "Codigo: estado local sin verificar" in text
    assert "Motivo: sin atestacion del supervisor" in text


def test_startup_status_message_does_not_infer_sync_from_clean_main():
    text = main._startup_status_message({
        "git_commit": "0457a0e",
        "git_branch": "main",
        "git_dirty": False,
        "git_synced": False,
    })

    assert "Codigo: estado local sin verificar" in text


def test_publish_live_strategy_contract_records_exact_runtime_policy(
        monkeypatch):
    events = []
    monkeypatch.setattr(main.config, "LOT_SIZE", 0.01)
    monkeypatch.setattr(
        main.config, "STRATEGY_MAX_PLANNED_LOTS_PER_SIGNAL", 0.05,
    )
    monkeypatch.setattr(main.config, "STRATEGY_C1_NUM_ENTRIES", 4)
    monkeypatch.setattr(main.config, "STRATEGY_C2_NUM_ENTRIES", 5)
    monkeypatch.setattr(
        main.config,
        "STRATEGY_C1_BASKET_GUARD_ENABLED",
        True,
    )
    monkeypatch.setattr(main.config, "STRATEGY_C1_BASKET_LOSS_CAP", -50.0)
    monkeypatch.setattr(main.config, "STRATEGY_C1_BASKET_PROFIT_ARM", 30.0)
    monkeypatch.setattr(main.config, "STRATEGY_C1_BASKET_PROFIT_LOCK", 20.0)
    monkeypatch.setattr(main.config, "STRATEGY_C1_BASKET_GUARD_POLL_S", 0.5)
    monkeypatch.setattr(
        main.config,
        "STRATEGY_C2_ZONE_FIRST_TOUCH_EXECUTION_ENABLED",
        False,
    )
    monkeypatch.setattr(
        main.journal,
        "event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )

    contract = main._publish_live_strategy_contract()

    assert events == [("bot", "live_strategy_contract", contract)]
    assert contract["dubai"]["basket_guard"] == {
        "enabled": True,
        "loss_cap": -50.0,
        "profit_arm": 30.0,
        "profit_lock": 20.0,
        "poll_seconds": 0.1,
        "money_source": "realized_plus_floating_account_currency",
    }
    assert contract["gold"]["zone_first_touch_execution"] is False
    assert contract["gold"]["zone_explicit_activation"] is True
    assert contract["risk"]["max_planned_lots_per_signal"] == 0.05
    assert contract["risk"]["exposure_cap_enforced"] is True
    assert contract["contract_schema_version"] == 1
    assert "schema_version" not in contract
    assert contract["evidence_status"] == "forward_trial"


def test_live_strategy_contract_reports_effective_guard_poll_interval(
        monkeypatch):
    monkeypatch.setattr(main.config, "STRATEGY_C1_BASKET_GUARD_POLL_S", 0.01)

    contract = main._live_strategy_contract()

    assert contract["dubai"]["basket_guard"]["poll_seconds"] == 0.01


@pytest.mark.parametrize("max_lots", [0.0, float("nan"), float("inf")])
def test_live_strategy_contract_rejects_invalid_exposure_cap(
        monkeypatch, max_lots):
    monkeypatch.setattr(
        main.config, "STRATEGY_MAX_PLANNED_LOTS_PER_SIGNAL", max_lots,
    )

    with pytest.raises(ValueError, match="STRATEGY_MAX_PLANNED_LOTS"):
        main._live_strategy_contract()


def test_git_info_compares_head_with_origin_main(monkeypatch):
    outputs = {
        ("git", "rev-parse", "--short", "HEAD"): "0457a0e",
        ("git", "rev-parse", "--short", "origin/main"): "0457a0e",
        ("git", "rev-parse", "HEAD"): "0457a0e" + "1" * 33,
        ("git", "rev-parse", "origin/main"): "0457a0e" + "1" * 33,
        ("git", "rev-parse", "--abbrev-ref", "HEAD"): "main",
        ("git", "status", "--porcelain"): "",
    }

    def fake_check_output(args, **kwargs):
        assert kwargs["timeout"] == 10
        return outputs[tuple(args)]

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)

    info = main._git_info()

    assert info["git_remote_commit"] == "0457a0e"
    assert info["git_synced"] is True


def test_watcher_attestation_accepts_the_exact_verified_head():
    full_head = "a" * 40
    info = {
        "git_commit_full": full_head,
        "git_remote_commit_full": full_head,
        "git_branch": "main",
        "git_dirty": False,
    }

    assert main._watcher_attestation_error(info, full_head) is None


def test_watcher_attestation_rejects_a_direct_main_launch():
    full_head = "a" * 40
    info = {
        "git_commit_full": full_head,
        "git_remote_commit_full": full_head,
        "git_branch": "main",
        "git_dirty": False,
    }

    assert (
        main._watcher_attestation_error(info, None)
        == "sin atestacion del supervisor"
    )


def test_watcher_attestation_rejects_head_before_its_push_finishes():
    info = {
        "git_commit_full": "b" * 40,
        "git_remote_commit_full": "a" * 40,
        "git_branch": "main",
        "git_dirty": False,
    }

    reason = main._watcher_attestation_error(info, "b" * 40)

    assert "origin/main" in reason
    assert "aaaaaaaa" in reason


def test_watcher_attestation_accepts_data_only_remote_mismatch_when_authorized():
    info = {
        "git_commit_full": "b" * 40,
        "git_remote_commit_full": "a" * 40,
        "git_branch": "main",
        "git_dirty": False,
    }

    assert main._watcher_attestation_error(
        info,
        "b" * 40,
        allow_remote_mismatch=True,
    ) is None


def test_startup_status_names_verified_local_checkpoint_without_false_alarm():
    text = main._startup_status_message({
        "git_commit": "bbbbbbb",
        "git_branch": "main",
        "git_dirty": False,
        "git_synced": False,
        "git_runtime_verified": True,
    })

    assert "Codigo: verificado; datos pendientes de subir" in text
    assert "estado local sin verificar" not in text


def test_unattested_main_terminates_only_a_legacy_watcher_parent():
    class LegacyWatcher:
        def __init__(self):
            self.terminated = False

        def name(self):
            return "python.exe"

        def cmdline(self):
            return ["python.exe", "-u", r"tools\run_bot_watch.py"]

        def terminate(self):
            self.terminated = True

    parent = LegacyWatcher()

    assert main._terminate_legacy_watcher_parent(parent) is True
    assert parent.terminated is True


def test_unattested_main_does_not_terminate_a_normal_shell_parent():
    class NormalShell:
        def __init__(self):
            self.terminated = False

        def name(self):
            return "cmd.exe"

        def cmdline(self):
            return ["cmd.exe", "/c", "python main.py"]

        def terminate(self):
            self.terminated = True

    parent = NormalShell()

    assert main._terminate_legacy_watcher_parent(parent) is False
    assert parent.terminated is False


def test_orphan_history_query_end_covers_positive_broker_offset():
    now = datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc)

    query_end = main._orphan_history_query_end(now)

    assert query_end == now + timedelta(days=1)
    assert query_end > now + timedelta(hours=3)


def test_orphan_history_waits_for_expected_position_close():
    opening = SimpleNamespace(position_id=101, entry=0)
    closing = SimpleNamespace(position_id=101, entry=1)
    responses = [
        (opening,),
        (opening, closing),
        (opening, closing),
    ]
    calls = []

    def history_get(_start, _end):
        calls.append(True)
        return responses.pop(0)

    deals = main._fetch_orphan_deals_synced(
        datetime(2026, 7, 20, tzinfo=timezone.utc),
        datetime(2026, 7, 22, tzinfo=timezone.utc),
        {101},
        history_get=history_get,
        sleep_fn=lambda _seconds: None,
        retries=4,
        pause_s=0,
    )

    assert deals == (opening, closing)
    assert len(calls) == 3


def test_orphan_history_default_uses_metatrader5_module(monkeypatch):
    closing = SimpleNamespace(
        ticket=501,
        position_id=101,
        entry=1,
        time_msc=1_000,
    )
    calls = []

    def history_get(_start, _end):
        calls.append(True)
        return (closing,)

    monkeypatch.setitem(
        sys.modules,
        "MetaTrader5",
        SimpleNamespace(history_deals_get=history_get),
    )

    deals = main._fetch_orphan_deals_synced(
        datetime(2026, 7, 20, tzinfo=timezone.utc),
        datetime(2026, 7, 22, tzinfo=timezone.utc),
        {101},
        sleep_fn=lambda _seconds: None,
        retries=2,
        pause_s=0,
    )

    assert deals == (closing,)
    assert len(calls) == 2


def test_resync_restores_canal2_reply_entry_identity(monkeypatch, tmp_path):
    opened_at = int(datetime.now(timezone.utc).timestamp()) - 5
    telegram_ts = datetime.fromtimestamp(opened_at, tz=timezone.utc)
    events_file = tmp_path / "trade_events.jsonl"
    events_file.write_text(
        json.dumps({
            "ts": (telegram_ts + timedelta(milliseconds=100)).isoformat(),
            "sig": "canal2_585",
            "ev": "telegram_raw",
            "channel": "canal2",
            "message_id": 585,
            "text": "Sell Gold Now",
            "is_reply": True,
            "reply_to_msg_id": 580,
            "date_utc": telegram_ts.isoformat(),
        }) + "\n" + json.dumps({
            "ts": (telegram_ts + timedelta(seconds=8)).isoformat(),
            "sig": "canal2_585",
            "ev": "canal2_duplicate_alias_registered",
            "alias_message_id": 586,
        }) + "\n",
        encoding="utf-8",
    )
    st = StateManager()
    groups = {
        "canal2_585": {
            "channel": "canal2",
            "message_id": 585,
            "direction": "SELL",
            "market_ticket": 1671689001,
            "market_price": 4002.8,
            "market_sl": 4010.0,
            "market_tp": 3998.0,
            "market_open_time": opened_at,
            "extra_market_tickets": [],
            "dca_tickets": [],
        }
    }

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
    monkeypatch.setattr(state_module, "state", st)
    monkeypatch.setattr(
        position_lifecycle_monitor,
        "start",
        lambda _signal, _levels: None,
    )
    tick_calls = []
    monkeypatch.setattr(
        main.executor,
        "mt5",
        SimpleNamespace(
            symbol_info_tick=lambda symbol: tick_calls.append(symbol) or None
        ),
    )

    main._resync_orphan_positions()

    signal = st.get("canal2", 585)
    assert signal is not None
    assert signal.telegram_entry_command_key == "SELL GOLD NOW"
    assert signal.telegram_entry_was_reply is True
    assert signal.telegram_entry_reply_to_message_id == 580
    assert signal.telegram_entry_timestamp == telegram_ts.replace(tzinfo=None)
    assert st.get("canal2", 586) is signal
    assert tick_calls == [main.config.MT5_SYMBOL]

    duplicate = listener._canal2_duplicate_alias_candidate(
        586,
        "SELL",
        telegram_ts.replace(tzinfo=None) + timedelta(seconds=8),
        {},
        [signal],
        10.0,
        raw_text="Sell Gold Now",
        is_reply=False,
    )
    assert duplicate is signal


def test_resync_restores_armed_dubai_basket_guard(monkeypatch, tmp_path):
    opened_at = int(datetime.now(timezone.utc).timestamp()) - 5
    events_file = tmp_path / "trade_events.jsonl"
    events_file.write_text(
        json.dumps({
            "sig": "canal1_21190",
            "ev": "basket_guard_armed",
            "observed_pl": 31.0,
            "peak_pl": 34.5,
        }) + "\n" + json.dumps({
            "sig": "canal1_21190",
            "ev": "market_filled",
            "ticket": 1671689009,
        }) + "\n" + json.dumps({
            "sig": "canal1_21190",
            "ev": "basket_guard_realized_ticket_confirmed",
            "ticket": 1671689009,
            "realized_pl": 7.30,
        }) + "\n",
        encoding="utf-8",
    )
    st = StateManager()
    groups = {
        "canal1_21190": {
            "channel": "canal1",
            "message_id": 21190,
            "direction": "BUY",
            "market_ticket": 1671689010,
            "market_price": 4040.0,
            "market_sl": 4030.0,
            "market_tp": 4050.0,
            "market_open_time": opened_at,
            "extra_market_tickets": [],
            "dca_tickets": [],
        }
    }
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
    monkeypatch.setattr(state_module, "state", st)
    monkeypatch.setattr(
        position_lifecycle_monitor,
        "start",
        lambda signal, levels: started.append((signal, levels)),
    )
    monkeypatch.setattr(
        main.executor,
        "mt5",
        SimpleNamespace(symbol_info_tick=lambda _symbol: None),
    )

    main._resync_orphan_positions()

    signal = st.get("canal1", 21190)
    assert signal is not None
    assert signal.basket_guard_armed is True
    assert signal.basket_guard_triggered is False
    assert signal.basket_guard_peak_pl == 34.5
    assert signal.basket_guard_known_tickets == [1671689009]
    assert signal.basket_guard_realized_by_ticket == {1671689009: 7.30}
    assert started == [(signal, [])]
