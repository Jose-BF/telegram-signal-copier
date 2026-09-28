import pytest

import main
import strategy_shadow_runtime
from strategy_shadow_runtime import ShadowRuntime
from tests.test_strategy_shadow_runtime import JournalCapture, register_dubai, register_gold


@pytest.mark.asyncio
@pytest.mark.parametrize("register", [register_dubai, register_gold])
async def test_storage_pause_is_not_later_claimed_as_complete_shadow_evidence(monkeypatch, register):
    capture = JournalCapture()
    runtime = ShadowRuntime(journal_sink=capture)
    original = await register(runtime)
    monkeypatch.setattr(strategy_shadow_runtime, "installed_runtime", lambda: runtime)
    monkeypatch.setattr(main.config, "STRATEGY_SHADOW_ENABLED", True)
    monkeypatch.setattr(main.journal, "optional_telemetry_allowed", lambda: False)
    monkeypatch.setattr(main.journal, "event", capture)

    async def stop_after_pause(delay):
        monkeypatch.setattr(main.config, "STRATEGY_SHADOW_ENABLED", False)

    def no_read():
        pytest.fail("heavy shadow reads must stop while storage is unavailable")

    monkeypatch.setattr(main.asyncio, "sleep", stop_after_pause)
    monkeypatch.setattr(main, "_shadow_tick_snapshot", no_read)
    await main._strategy_shadow_loop()
    states = runtime.states_for_signal(original[0].signal_id)
    assert len(states) == len(original)
    assert all(not state.complete for state in states)
    assert all("storage_capacity_unavailable" in state.evidence_blockers for state in states)
    assert runtime.active_tick_cursor() is None

    def never_reconstruct(cursor):
        pytest.fail("a storage gap must not be silently upgraded after restart")

    restored = await ShadowRuntime().recover(capture.records, history_reader=never_reconstruct)
    assert len(restored) == len(original)
    assert all(not state.complete for state in restored)
