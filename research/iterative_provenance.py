"""Read-only, bounded implementation identity for offline iterative research.

Hash source bytes, not Git state, paths, mtimes or live configuration. This is
resume provenance, not proof that an archived simulation is correct today.
"""

from __future__ import annotations

from decimal import getcontext
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import sys


SOURCE_ROOT = Path(__file__).resolve().parents[1]
# Explicit dependency boundary: shared search/execution, Gold and offline
# monetary/time/action helpers. Never discover files by scanning the workspace.
IMPLEMENTATION_FILES = (
    "research/__init__.py",
    "research/iterative_provenance.py",
    "research/execution_profile.py",
    "research/strategy_study_dataset.py",
    "research/strategy_study.py",
    "research/telegram_export.py",
    "research/causal_replay.py",
    "parser.py",
    "provider_signal_catalog.py",
    "interpretation_firewall.py",
    "research/dubai_iterative/__main__.py",
    "research/dubai_iterative/__init__.py",
    "research/dubai_iterative/contracts.py",
    "research/dubai_iterative/dataset.py",
    "research/dubai_iterative/engine.py",
    "research/dubai_iterative/runtime_control.py",
    "research/dubai_iterative/protection_contract.py",
    "research/dubai_iterative/passive_fill_contract.py",
    "research/dubai_iterative/protection.py",
    "research/dubai_iterative/market_contract.py",
    "research/dubai_iterative/market.py",
    "research/dubai_iterative/client_contract.py",
    "research/dubai_iterative/client.py",
    "research/dubai_iterative/shared_replay.py",
    "research/shared_transport.py",
    "research/management_observation.py",
    "basket_observation.py",
    "basket_management.py",
    "basket_stop.py",
    "dubai_live_candidate.py",
    "gold_555_live_candidate.py",
    "research/canonical_guard.py",
    "research/canonical_stop.py",
    "research/broker_valuation.py",
    "research/risk_metrics.py",
    "mt5_scheduling.py",
    "mt5_protocol.py",
    "mt5_read_protocol.py",
    "mt5_trade_protocol.py",
    "research/dubai_iterative/fast_engine.py",
    "research/dubai_iterative/oracle.py",
    "research/dubai_iterative/evolution.py",
    "research/dubai_iterative/search.py",
    "research/dubai_iterative/recursive.py",
    "research/dubai_iterative/refinement.py",
    "research/dubai_iterative/robustness.py",
    "research/dubai_iterative/statistics.py",
    "research/dubai_iterative/risk.py",
    "research/dubai_iterative/portfolio.py",
    "research/dubai_iterative/certification.py",
    "research/dubai_iterative/reporting.py",
    "research/gold_iterative/__init__.py",
    "research/gold_iterative/__main__.py",
    "research/gold_iterative/contracts.py",
    "research/gold_iterative/dataset.py",
    "research/gold_iterative/folds.py",
    "research/gold_iterative/search.py",
    "research/gold_iterative/seeds.py",
    "research/gold_iterative/validation.py",
    "research/gold_iterative/provider_accounting.py",
    "research/gold_iterative/reporting.py",
    "broker_money.py",
    "broker_market_sessions.py",
    "broker_tick_clock.py",
    "provider_action_semantics.py",
    "provider_trade_spec.py",
    "provider_result_scorecard.py",
    "tools/ensure_replay_tick_cache.py",
    "mt5_tick_cache.py",
    "runtime_paths.py",
)
RUNTIME_PACKAGES = ("numpy", "pandas", "pyarrow", "numba", "llvmlite", "matplotlib")
RUNTIME_ENVIRONMENT = (
    "NUMBA_DISABLE_JIT", "NUMBA_CPU_NAME", "NUMBA_CPU_FEATURES",
    "NUMBA_ENABLE_AVX", "NUMBA_OPT", "NUMBA_BOUNDSCHECK",
    "NUMBA_NUM_THREADS", "NUMBA_THREADING_LAYER",
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def runtime_identity() -> dict[str, object]:
    packages = {}
    for name in RUNTIME_PACKAGES:
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    decimal = getcontext()
    return {
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "python_cache_tag": sys.implementation.cache_tag,
        "system": platform.system(),
        "machine": platform.machine(),
        "byteorder": sys.byteorder,
        "packages": packages,
        "decimal": {
            name: getattr(decimal, name)
            for name in ("prec", "rounding", "Emin", "Emax", "capitals", "clamp")
        } | {"traps": sorted(
            signal.__name__ for signal, enabled in decimal.traps.items() if enabled
        )},
        "numba_environment": {
            name: os.environ.get(name) for name in RUNTIME_ENVIRONMENT
        },
    }


def implementation_identity() -> dict[str, object]:
    """Compute fresh identity; missing dependencies fail closed, without writes."""
    sources = {}
    for relative in IMPLEMENTATION_FILES:
        try:
            sources[relative] = hashlib.sha256(
                (SOURCE_ROOT / relative).read_bytes()
            ).hexdigest()
        except OSError as exc:
            raise ValueError(
                f"cannot identify iterative implementation file: {relative}"
            ) from exc
    payload = {
        "schema_version": 1,
        "source_sha256": sources,
        "runtime": runtime_identity(),
    }
    return {**payload, "sha256": hashlib.sha256(_canonical_bytes(payload)).hexdigest()}
