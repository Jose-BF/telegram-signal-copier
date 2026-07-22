"""The active replay pipeline must share the runtime data boundary."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


MODULE_PATHS = (
    ("reconcile_mt5_ledger", ("DATA_DIR", "JOURNAL_FILE", "LEDGER_FILE")),
    (
        "build_replay_trades",
        ("DATA_DIR", "DEFAULT_LEDGER_FILE", "DEFAULT_EVENTS_FILE", "DEFAULT_REPLAY_FILE"),
    ),
    (
        "accounting_replay_validator",
        ("DATA_DIR", "DEFAULT_REPLAY_FILE", "DEFAULT_AUDIT_FILE"),
    ),
    (
        "observed_tick_replay_validator",
        (
            "DATA_DIR",
            "DEFAULT_REPLAY_FILE",
            "DEFAULT_TICK_CACHE_DIR",
            "DEFAULT_OUTPUT",
            "DEFAULT_STATUS",
        ),
    ),
    (
        "replay_readiness_report",
        (
            "DATA_DIR",
            "DEFAULT_REPLAY_FILE",
            "DEFAULT_AUDIT_FILE",
            "DEFAULT_OBSERVED_AUDIT_FILE",
            "DEFAULT_TICK_CACHE_DIR",
            "DEFAULT_OUTPUT",
        ),
    ),
    (
        "provider_signal_catalog",
        ("DATA_DIR", "DEFAULT_EVENTS", "DEFAULT_REPLAY", "DEFAULT_OUTPUT"),
    ),
    (
        "recursive_log_learning",
        (
            "DATA_DIR",
            "DEFAULT_EVENTS",
            "DEFAULT_REPLAY",
            "DEFAULT_ACCOUNTING",
            "DEFAULT_OBSERVED",
            "DEFAULT_PROVIDER",
            "DEFAULT_STRATEGY_FARM",
            "DEFAULT_REVIEWS",
            "DEFAULT_REPORT",
            "DEFAULT_REGISTRY",
        ),
    ),
    (
        "strategy_farm",
        (
            "DATA_DIR",
            "DEFAULT_REPLAY",
            "DEFAULT_BASELINE",
            "DEFAULT_CATALOG",
            "DEFAULT_TICK_CACHE",
            "DEFAULT_MONEY_CONTRACT",
            "DEFAULT_MONEY_TICK_CACHE",
            "DEFAULT_MONEY_TICK_STATUS",
            "DEFAULT_OUTPUT",
            "DEFAULT_RUN_ARCHIVE",
        ),
    ),
    (
        "strategy_simulator",
        (
            "DATA_DIR",
            "DEFAULT_REPLAY_FILE",
            "DEFAULT_BASELINE_AUDIT_FILE",
            "DEFAULT_TICK_CACHE_DIR",
            "DEFAULT_OUTPUT",
        ),
    ),
    ("mt5_tick_cache", ("DATA_DIR", "TICKS_CACHE")),
    (
        "tools.ensure_replay_tick_cache",
        ("DEFAULT_INPUT", "DEFAULT_CACHE_DIR", "DEFAULT_STATUS"),
    ),
    (
        "tools.ensure_money_tick_cache",
        (
            "DEFAULT_INPUT",
            "DEFAULT_CACHE_DIR",
            "DEFAULT_REFERENCE_CACHE",
            "DEFAULT_STATUS",
        ),
    ),
    ("tools.capture_broker_money_contract", ("DEFAULT_OUTPUT",)),
    ("tools.analyze_new_logs", ("DEFAULT_EVENTS",)),
    ("ledger_report", ("LEDGER_FILE",)),
    (
        "analysis.daily_report",
        ("DATA_DIR", "DEFAULT_LEDGER", "DEFAULT_ACCOUNTING", "DEFAULT_EVENTS"),
    ),
    ("analysis.patterns", ("EVENTS_FILE",)),
)


@pytest.mark.parametrize(("module_name", "attributes"), MODULE_PATHS)
def test_active_analysis_defaults_follow_runtime_store(
    tmp_path: Path,
    module_name: str,
    attributes: tuple[str, ...],
) -> None:
    runtime_dir = (tmp_path / "runtime").resolve()
    environment = os.environ.copy()
    environment["BOT_RUNTIME_DATA_DIR"] = str(runtime_dir)
    code = (
        "import importlib,json; "
        f"module=importlib.import_module({module_name!r}); "
        f"names={attributes!r}; "
        "print(json.dumps([str(getattr(module,name)) for name in names]))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    paths = [Path(value).resolve() for value in json.loads(completed.stdout)]
    assert paths
    assert all(path.is_relative_to(runtime_dir) for path in paths)
