from pathlib import Path

import runtime_control


def test_pause_gate_and_handler_activity_are_crash_visible(tmp_path, monkeypatch):
    pause = tmp_path / "pause.json"
    activity = tmp_path / "activity.json"
    monkeypatch.setattr(runtime_control, "PAUSE_FILE", pause)
    monkeypatch.setattr(runtime_control, "ACTIVITY_FILE", activity)
    monkeypatch.setattr(runtime_control, "_active_handlers", 0)

    assert runtime_control.begin_handler() is True
    snapshot = runtime_control.read_activity()
    assert snapshot["active_handlers"] == 1

    runtime_control.request_pause("system_resume")
    assert runtime_control.begin_handler() is False

    runtime_control.end_handler()
    assert runtime_control.active_handler_count(snapshot["pid"]) == 0

    runtime_control.clear_for_spawn()
    assert not pause.exists()
    assert not activity.exists()


def test_handler_registers_activity_before_accepting_work(tmp_path, monkeypatch):
    pause = tmp_path / "pause.json"
    activity = tmp_path / "activity.json"
    monkeypatch.setattr(runtime_control, "PAUSE_FILE", pause)
    monkeypatch.setattr(runtime_control, "ACTIVITY_FILE", activity)
    monkeypatch.setattr(runtime_control, "_active_handlers", 0)

    original_write = runtime_control._write_activity

    def pause_during_registration():
        original_write()
        runtime_control.request_pause("watcher_restart")

    monkeypatch.setattr(
        runtime_control,
        "_write_activity",
        pause_during_registration,
    )

    assert runtime_control.begin_handler() is False
    assert runtime_control.active_handler_count(None) == 0


def test_clear_pause_resumes_handlers_without_resetting_activity(
        tmp_path, monkeypatch):
    pause = tmp_path / "pause.json"
    activity = tmp_path / "activity.json"
    monkeypatch.setattr(runtime_control, "PAUSE_FILE", pause)
    monkeypatch.setattr(runtime_control, "ACTIVITY_FILE", activity)
    monkeypatch.setattr(runtime_control, "_active_handlers", 0)

    runtime_control.request_pause("watcher_code_update")
    assert runtime_control.pause_requested() is True

    runtime_control.clear_pause()

    assert runtime_control.pause_requested() is False
    assert runtime_control.begin_handler() is True
    runtime_control.end_handler()
