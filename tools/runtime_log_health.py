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
    exists = target.is_file()
    size = target.stat().st_size if exists else 0
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
        "append_only": True,
        "action": "none",
    }
