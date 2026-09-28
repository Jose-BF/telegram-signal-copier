import json
from types import SimpleNamespace

import config
import main
import pytest
from state import Signal, StateManager


@pytest.fixture(autouse=True)
def _isolate_pending_entries(monkeypatch):
    monkeypatch.setattr(main, "pending_entry_count", lambda: 0, raising=False)


def test_runtime_exposure_is_open_when_mt5_has_bot_positions():
    mt5_positions = [
        SimpleNamespace(ticket=10, magic=config.magic_for("canal2")),
        SimpleNamespace(ticket=11, magic=0),
    ]

    snapshot = main._runtime_exposure_snapshot(
        StateManager(), positions_get=lambda: mt5_positions)

    assert snapshot == {
        "exposure_state": "open",
        "bot_position_count": 1,
        "open_signal_count": 0,
        "pending_entry_count": 0,
    }


def test_runtime_exposure_is_flat_only_with_confirmed_empty_mt5_and_state():
    snapshot = main._runtime_exposure_snapshot(
        StateManager(), positions_get=lambda: [])

    assert snapshot["exposure_state"] == "flat"
    assert snapshot["bot_position_count"] == 0
    assert snapshot["open_signal_count"] == 0


def test_runtime_exposure_fails_closed_when_mt5_state_is_unknown():
    snapshot = main._runtime_exposure_snapshot(
        StateManager(), positions_get=lambda: None)

    assert snapshot["exposure_state"] == "unknown"
    assert snapshot["bot_position_count"] is None


def test_runtime_exposure_uses_fresh_cached_positions_without_native_wait():
    class Runtime:
        def snapshot(self, operation):
            assert operation == "positions_get"
            return {
                "state": "FOUND",
                "age_seconds": 0.5,
                "value": [
                    {"ticket": 10, "magic": config.magic_for("canal1")},
                    {"ticket": 11, "magic": 0},
                ],
            }

    snapshot = main._runtime_exposure_snapshot(
        StateManager(),
        runtime=Runtime(),
        snapshot_max_age_s=5.0,
    )

    assert snapshot["exposure_state"] == "open"
    assert snapshot["bot_position_count"] == 1


def test_runtime_exposure_treats_stale_cached_positions_as_unknown():
    class Runtime:
        def snapshot(self, _operation, _params=None):
            return {
                "state": "EMPTY",
                "age_seconds": 5.1,
                "value": [],
            }

    snapshot = main._runtime_exposure_snapshot(
        StateManager(),
        runtime=Runtime(),
        snapshot_max_age_s=5.0,
    )

    assert snapshot["exposure_state"] == "unknown"
    assert snapshot["bot_position_count"] is None


def test_runtime_exposure_remains_open_when_memory_knows_a_signal():
    state = StateManager()
    state.add(Signal(channel="canal1", message_id=20700,
                     direction="SELL"))

    snapshot = main._runtime_exposure_snapshot(
        state, positions_get=lambda: None)

    assert snapshot["exposure_state"] == "open"
    assert snapshot["open_signal_count"] == 1


def test_pending_entry_without_signal_or_position_blocks_restart(monkeypatch):
    monkeypatch.setattr(main, "pending_entry_count", lambda: 1, raising=False)

    snapshot = main._runtime_exposure_snapshot(
        StateManager(), positions_get=lambda: [])

    assert snapshot["exposure_state"] == "open"
    assert snapshot["pending_entry_count"] == 1


def test_unknown_pending_entry_state_never_authorizes_restart(monkeypatch):
    def unavailable():
        raise RuntimeError("pending plan snapshot unavailable")

    monkeypatch.setattr(main, "pending_entry_count", unavailable, raising=False)
    snapshot = main._runtime_exposure_snapshot(
        StateManager(), positions_get=lambda: [])

    assert snapshot["exposure_state"] == "unknown"
    assert snapshot["pending_entry_count"] is None


def test_runtime_heartbeat_publishes_versioned_exposure_contract(
        tmp_path, monkeypatch):
    path = tmp_path / "runtime_heartbeat.json"
    monkeypatch.setattr(
        main,
        "_runtime_exposure_snapshot",
        lambda: {
            "exposure_state": "open",
            "bot_position_count": 5,
            "open_signal_count": 1,
            "pending_entry_count": 2,
        },
    )

    main._write_runtime_heartbeat(path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 3
    assert payload["exposure_state"] == "open"
    assert payload["bot_position_count"] == 5
    assert payload["open_signal_count"] == 1
    assert payload["pending_entry_count"] == 2


def test_runtime_heartbeat_includes_mt5_owner_identity_and_freshness(
        tmp_path, monkeypatch):
    path = tmp_path / "runtime_heartbeat.json"

    class Runtime:
        def snapshot(self, _operation, _params=None):
            return {
                "state": "FOUND",
                "error": None,
                "native_error": None,
                "worker_session_id": "session-1",
                "worker_pid": 4567,
                "completed_monotonic": 10.0,
                "completed_utc_ns": 123456789,
                "age_seconds": 0.25,
            }

        client = SimpleNamespace(
            worker_pid=4567,
            session_id="session-1",
            is_alive=True,
            retained_trade_outcome_count=0,
            transport_snapshot=lambda: {
                "active": False,
                "active_kind": None,
                "trade_waiters": 0,
                "read_waiters": 0,
                "consecutive_trades": 0,
                "pending_count": 0,
                "capacity": 32,
            },
        )

    monkeypatch.setattr(main, "_active_mt5_owner", Runtime())
    monkeypatch.setattr(
        main,
        "_runtime_exposure_snapshot",
        lambda: {
            "exposure_state": "flat",
            "bot_position_count": 0,
            "open_signal_count": 0,
            "pending_entry_count": 0,
        },
    )

    main._write_runtime_heartbeat(path)

    owner = json.loads(path.read_text(encoding="utf-8"))["mt5_owner"]
    assert owner["installed"] is True
    assert owner["worker_alive"] is True
    assert owner["worker_pid"] == 4567
    assert owner["worker_session_id"] == "session-1"
    assert owner["reads"]["positions_get"]["age_seconds"] == 0.25
    assert owner["reads"]["symbol_info_tick"]["completed_utc_ns"] == 123456789
    assert owner["transport"]["capacity"] == 32
