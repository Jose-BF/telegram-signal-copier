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


def test_supervisor_loop_gap_detects_machine_resume_even_with_fresh_heartbeat():
    assert run_bot_watch._supervisor_loop_gap_is_stale(
        previous_tick=100.0,
        current_tick=7300.0,
        timeout_s=90.0,
    )


def test_normal_supervisor_loop_delay_does_not_trigger_resume_recovery():
    assert not run_bot_watch._supervisor_loop_gap_is_stale(
        previous_tick=100.0,
        current_tick=102.2,
        timeout_s=90.0,
    )
