# Runtime Control Plan

## Goal

Make the bot runtime easier to control by removing inactive DCA modes from the
live configuration path while preserving the current `scale_out` behavior.

## Scope

- Keep production entry behavior as `scale_out`.
- Keep the trade monitor for BE, time-stop and auto-finalize.
- Prevent deprecated entry modes (`intra_dca`, `extremes`) from being
  reactivated through environment variables.
- Leave historical parser, replay and analysis helpers for a later archive pass.

## Implementation

1. Add `config.normalize_entry_mode()` and use it for channel entry modes.
2. Refactor the legacy `_place_dca()` path into monitor-only behavior.
3. Update runtime comments so they no longer describe DCA limits as active.
4. Add regression tests for config normalization and monitor-only startup.

## Verification

- `python -m pytest -q tests/test_config_runtime.py`
- `python -m pytest -q tests/test_config_runtime.py tests/test_listener_helpers.py tests/test_state.py tests/test_position_lifecycle_monitor.py tests/test_position_lifecycle_monitor_time_stop.py tests/test_pending_actions.py tests/test_reconcile_mt5_ledger.py`
- `python -m pytest -q`
