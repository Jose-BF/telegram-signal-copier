"""Read-only health information for the append-only console log."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def inspect_runtime_log(
    path: Path,
    *,
    warn_bytes: int,
) -> dict[str, Any]:
    """Inspect size only; never create, rotate, truncate, or rename the log."""
    target = Path(path)
    try:
        exists = target.is_file()
        size = target.stat().st_size if exists else 0
    except OSError as exc:
        return {
            "schema_version": 1,
            "path": str(target),
            "exists": False,
            "size_bytes": 0,
            "warn_bytes": int(warn_bytes),
            "warning": "runtime_log_inspection_failed",
            "error": f"{type(exc).__name__}: {exc}",
            "append_only": True,
            "action": "none",
        }
    warning = (
        "runtime_log_size_threshold_exceeded"
        if exists and size >= int(warn_bytes)
        else None
    )
    return {
        "schema_version": 1,
        "path": str(target),
        "exists": exists,
        "size_bytes": size,
        "warn_bytes": int(warn_bytes),
        "warning": warning,
        "error": None,
        "append_only": True,
        "action": "none",
    }
