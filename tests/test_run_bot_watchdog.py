import time

from tools import run_bot_watch


def test_runtime_heartbeat_missing_is_stale_after_startup_grace():
    assert run_bot_watch._runtime_heartbeat_is_stale(
        heartbeat_age_s=None,
        process_uptime_s=181.0,
        timeout_s=180.0,
    )


def test_runtime_heartbeat_fresh_is_not_stale():
    assert not run_bot_watch._runtime_heartbeat_is_stale(
        heartbeat_age_s=20.0,
        process_uptime_s=600.0,
        timeout_s=180.0,
    )


def test_runtime_heartbeat_old_is_stale():
    assert run_bot_watch._runtime_heartbeat_is_stale(
        heartbeat_age_s=240.0,
        process_uptime_s=600.0,
        timeout_s=180.0,
    )
