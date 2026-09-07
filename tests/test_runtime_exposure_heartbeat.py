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
