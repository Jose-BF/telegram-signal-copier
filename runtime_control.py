"""Small cross-process gate for graceful watcher restarts."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import runtime_paths

DATA_DIR = runtime_paths.active_data_dir()
PAUSE_FILE = Path(os.getenv(
    "BOT_RUNTIME_PAUSE_FILE",
    str(DATA_DIR / "runtime_pause.json"),
))
ACTIVITY_FILE = Path(os.getenv(
    "BOT_RUNTIME_ACTIVITY_FILE",
    str(DATA_DIR / "runtime_handler_activity.json"),
))

_lock = threading.Lock()
_active_handlers = 0


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def request_pause(reason: str) -> None:
    _atomic_json(PAUSE_FILE, {
        "reason": str(reason),
        "requested_utc": datetime.now(timezone.utc).isoformat(),
        "watcher_pid": os.getpid(),
    })


def pause_requested() -> bool:
    return PAUSE_FILE.is_file()


def clear_pause() -> None:
    """Resume Telegram dispatch without altering live handler accounting."""
    try:
        PAUSE_FILE.unlink()
    except FileNotFoundError:
        pass


def _write_activity() -> None:
    if _active_handlers <= 0:
        try:
            ACTIVITY_FILE.unlink()
        except FileNotFoundError:
            pass
        return
    _atomic_json(ACTIVITY_FILE, {
        "pid": os.getpid(),
        "active_handlers": _active_handlers,
        "updated_utc": datetime.now(timezone.utc).isoformat(),
    })


def begin_handler() -> bool:
    global _active_handlers
    with _lock:
        _active_handlers += 1
        _write_activity()
        # Register first, then inspect the cross-process pause gate. This
        # ordering closes the race where the watcher saw zero active handlers
        # just before a Telegram callback started doing real work.
        if pause_requested():
            _active_handlers = max(0, _active_handlers - 1)
            _write_activity()
            return False
        return True


def end_handler() -> None:
    global _active_handlers
    with _lock:
        _active_handlers = max(0, _active_handlers - 1)
        _write_activity()


def read_activity() -> dict:
    try:
        value = json.loads(ACTIVITY_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, TypeError, ValueError):
        return {}


def active_handler_count(pid: int | None) -> int:
    snapshot = read_activity()
    try:
        if pid is None or int(snapshot.get("pid")) != int(pid):
            return 0
        return max(0, int(snapshot.get("active_handlers") or 0))
    except (TypeError, ValueError):
        return 0


def clear_for_spawn() -> None:
    global _active_handlers
    with _lock:
        _active_handlers = 0
        for path in (PAUSE_FILE, ACTIVITY_FILE):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
