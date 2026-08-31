from pathlib import Path

from tools.runtime_log_health import inspect_runtime_log


def test_runtime_log_health_warns_without_mutating_file(tmp_path):
    path = tmp_path / "bot_runtime.log"
    original = b"append-only evidence"
    path.write_bytes(original)

    health = inspect_runtime_log(path, warn_bytes=10)

    assert health["exists"] is True
    assert health["size_bytes"] == len(original)
    assert health["warning"] == "runtime_log_size_threshold_exceeded"
    assert health["action"] == "none"
    assert path.read_bytes() == original


def test_runtime_log_health_reports_missing_file_without_creating_it(tmp_path):
    path = tmp_path / "bot_runtime.log"

    health = inspect_runtime_log(path, warn_bytes=10)

    assert health["exists"] is False
    assert health["warning"] is None
    assert health["action"] == "none"
    assert path.exists() is False


def test_runtime_log_health_access_error_is_informational(monkeypatch, tmp_path):
    path = tmp_path / "bot_runtime.log"

    def denied(_path):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "is_file", denied)

    health = inspect_runtime_log(path, warn_bytes=10)

    assert health["warning"] == "runtime_log_inspection_failed"
    assert health["action"] == "none"
    assert health["error"] == "PermissionError: denied"
