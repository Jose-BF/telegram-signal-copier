import json
import os
from types import SimpleNamespace

from tools import run_bot_watch as watch


def _write_heartbeat(path, *, state, positions=0, signals=0, mtime=1000.0):
    path.write_text(json.dumps({
        "schema_version": 2,
        "pid": 123,
        "utc": "2026-07-23T15:00:00.000",
        "exposure_state": state,
        "bot_position_count": positions,
        "open_signal_count": signals,
    }), encoding="utf-8")
    os.utime(path, (mtime, mtime))


def test_code_update_is_deferred_while_bot_positions_are_open(tmp_path):
    heartbeat = tmp_path / "heartbeat.json"
    pending = tmp_path / "runtime_update_pending.json"
    _write_heartbeat(
        heartbeat, state="open", positions=5, signals=1, mtime=1000.0)

    deferred, exposure = watch._defer_code_update_if_exposed(
        "old", "new",
        heartbeat_path=heartbeat,
        pending_path=pending,
        now=1010.0,
        max_age_s=30.0,
    )

    assert deferred is True
    assert exposure["exposure_state"] == "open"
    payload = json.loads(pending.read_text(encoding="utf-8"))
    assert payload["local_revision"] == "old"
    assert payload["remote_revision"] == "new"
    assert payload["reason"] == "open_exposure"
    assert payload["bot_position_count"] == 5


def test_code_update_fails_closed_when_exposure_cannot_be_verified(tmp_path):
    pending = tmp_path / "runtime_update_pending.json"

    deferred, exposure = watch._defer_code_update_if_exposed(
        "old", "new",
        heartbeat_path=tmp_path / "missing.json",
        pending_path=pending,
        now=1010.0,
        max_age_s=30.0,
    )

    assert deferred is True
    assert exposure["exposure_state"] == "unknown"
    assert exposure["reason"] == "heartbeat_missing"
    assert json.loads(
        pending.read_text(encoding="utf-8"))["reason"] == "exposure_unknown"


def test_stale_flat_heartbeat_does_not_authorize_restart(tmp_path):
    heartbeat = tmp_path / "heartbeat.json"
    pending = tmp_path / "runtime_update_pending.json"
    _write_heartbeat(
        heartbeat, state="flat", positions=0, signals=0, mtime=900.0)

    deferred, exposure = watch._defer_code_update_if_exposed(
        "old", "new",
        heartbeat_path=heartbeat,
        pending_path=pending,
        now=1010.0,
        max_age_s=30.0,
    )

    assert deferred is True
    assert exposure["exposure_state"] == "unknown"
    assert exposure["reason"] == "heartbeat_stale"


def test_confirmed_flat_state_allows_update_and_clears_pending_marker(
        tmp_path):
    heartbeat = tmp_path / "heartbeat.json"
    pending = tmp_path / "runtime_update_pending.json"
    pending.write_text('{"remote_revision":"old-pending"}',
                       encoding="utf-8")
    _write_heartbeat(
        heartbeat, state="flat", positions=0, signals=0, mtime=1000.0)

    deferred, exposure = watch._defer_code_update_if_exposed(
        "old", "new",
        heartbeat_path=heartbeat,
        pending_path=pending,
        now=1010.0,
        max_age_s=30.0,
    )

    assert deferred is False
    assert exposure["exposure_state"] == "flat"
    assert not pending.exists()


def test_legacy_heartbeat_cannot_be_mistaken_for_flat(tmp_path):
    heartbeat = tmp_path / "heartbeat.json"
    pending = tmp_path / "runtime_update_pending.json"
    heartbeat.write_text(
        '{"pid":123,"utc":"2026-07-23T15:00:00.000"}',
        encoding="utf-8",
    )
    os.utime(heartbeat, (1000.0, 1000.0))

    deferred, exposure = watch._defer_code_update_if_exposed(
        "old", "new",
        heartbeat_path=heartbeat,
        pending_path=pending,
        now=1010.0,
        max_age_s=30.0,
    )

    assert deferred is True
    assert exposure["reason"] == "heartbeat_schema_unsupported"


def test_code_update_quiesce_rechecks_exposure_after_handlers_finish(
        monkeypatch):
    process = SimpleNamespace(pid=123, poll=lambda: None)
    calls = []

    monkeypatch.setattr(
        watch.runtime_control,
        "request_pause",
        lambda reason: calls.append(("pause", reason)),
    )
    monkeypatch.setattr(
        watch.runtime_control,
        "active_handler_count",
        lambda pid: 0,
    )
    monkeypatch.setattr(
        watch.runtime_control,
        "clear_pause",
        lambda: calls.append(("resume", None)),
        raising=False,
    )
    monkeypatch.setattr(
        watch,
        "_wait_for_post_quiesce_exposure",
        lambda *args, **kwargs: {
            "exposure_state": "open",
            "bot_position_count": 5,
            "open_signal_count": 1,
            "reason": "heartbeat_reported_open",
        },
        raising=False,
    )

    authorized, exposure = watch._quiesce_code_update(process)

    assert authorized is False
    assert exposure["exposure_state"] == "open"
    assert calls == [
        ("pause", "watcher_code_update"),
        ("resume", None),
    ]


def test_post_quiesce_confirmation_requires_new_heartbeat_from_child(
        tmp_path, monkeypatch):
    heartbeat = tmp_path / "heartbeat.json"
    _write_heartbeat(
        heartbeat, state="flat", positions=0, signals=0, mtime=1000.0)
    times = iter((1010.0, 1010.1, 1010.2, 1010.3))

    exposure = watch._wait_for_post_quiesce_exposure(
        child_pid=123,
        heartbeat_path=heartbeat,
        not_before=1005.0,
        timeout_s=0.2,
        now_fn=lambda: next(times),
        sleep_fn=lambda _seconds: None,
    )

    assert exposure["exposure_state"] == "unknown"
    assert exposure["reason"] == "post_quiesce_heartbeat_missing"
