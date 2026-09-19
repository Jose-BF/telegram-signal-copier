"""Constant-cost disk headroom checks; never scan or delete historical data."""
import shutil

WARN_FREE_BYTES = 4 * 1024 ** 3
TELEMETRY_RESERVE_BYTES = 2 * 1024 ** 3


def storage_health(path):
    try:
        free = shutil.disk_usage(path).free
    except OSError:
        return {"free_bytes": None, "warning": True, "allow_telemetry": False}
    return {
        "free_bytes": free,
        "warning": free < WARN_FREE_BYTES,
        "allow_telemetry": free >= TELEMETRY_RESERVE_BYTES,
    }
