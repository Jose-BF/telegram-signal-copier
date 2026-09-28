"""Freeze recovery controls alongside both-channel runtime and global checks."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location(
    "control_verification", ROOT / "reports/e5-runtime-protection-control-20260921/run_verification.py")
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


if __name__ == "__main__":
    scratch = Path(tempfile.mkdtemp(prefix="cr-", dir="A:/"))
    if shutil.disk_usage(scratch).free < 4 * 1024**3:
        raise RuntimeError("insufficient scratch space for verification")
    report.OUT = scratch / "e"
    report.OUT.mkdir()
    env = dict(os.environ, TEMP=str(scratch), TMP=str(scratch),
               BOT_RUNTIME_DATA_DIR=str(scratch / "r"), NUMBA_CACHE_DIR=str(scratch / "n"),
               PYTHONDONTWRITEBYTECODE="1")
    before, identity = report.sources(), report.implementation_identity()
    report.save("sources.json", {"sources": before, "implementation": identity,
        "python": sys.executable, "scratch": str(scratch), "environment": {
            name: env[name] for name in ("TEMP", "TMP", "BOT_RUNTIME_DATA_DIR",
                                         "NUMBA_CACHE_DIR", "PYTHONDONTWRITEBYTECODE")}})
    print(json.dumps({"evidence": str(report.OUT), "scratch": str(scratch)}), flush=True)
    focused_tests = sorted({str(path.relative_to(ROOT)) for pattern in
        ("test_runtime_replay_*.py", "test_e3_c*.py") for path in (ROOT / "tests").glob(pattern)})
    focused = report.run("focused", [*focused_tests,
        "tests/test_recovered_closed_entry_evidence.py", "tests/test_late_initial_entry_recovery.py",
        "tests/test_cancelled_candidate_recovery.py", "tests/test_pending_revision_continuity.py",
        "tests/test_e4_pending_revision.py", "tests/test_e4_pending_spool.py", "tests/test_pending_actions.py",
        "tests/test_unresolved_entry_evidence.py", "tests/test_signal_lifecycle.py",
        "tests/test_strategy_runtime_lifecycle_integration.py", "tests/test_native_exit_attribution.py",
        "tests/test_same_clock_closures.py", "tests/test_position_lifecycle_monitor.py",
        "tests/test_gold_555_monitor.py", "tests/test_gold_555_listener.py", "tests/test_dubai_live_listener.py",
        "tests/test_dubai_live_ladder.py", "tests/test_finalizer_money_completeness.py",
        "tests/test_listener_helpers.py", "tests/test_gateway_cancellation_race.py",
        "tests/test_iterative_provenance.py",
        f"--basetemp={scratch / 'f'}", "-o", f"cache_dir={scratch / 'c'}"], env)
    controls = report.OUT / "controls"
    controls.mkdir()
    hashes = {}
    for pattern, prefix in (("entry-controller.json", "gold-"), ("dubai.json", "dubai-")):
        for source in sorted((scratch / "f").rglob(pattern)):
            destination = controls / (prefix + source.parent.name + ".json")
            if destination.exists():
                raise RuntimeError("duplicate control evidence name")
            shutil.copy2(source, destination)
            hashes[destination.name] = report.digest(destination)
    report.save("control-manifest.json", {"controls": hashes, "synthetic": True,
        "native_broker_verified": False, "complete_strategy_admitted": False})
    full = report.run("full", [f"--basetemp={scratch / 't'}", "-o",
                               f"cache_dir={scratch / 'c'}"], env) if focused["exit_code"] == 0 else None
    after = report.sources()
    unchanged = before == after and identity == report.implementation_identity()
    result = {"focused": focused, "full": full, "sources_unchanged": before == after,
        "changed_sources": sorted(key for key in set(before) | set(after)
                                  if before.get(key) != after.get(key)),
        "implementation_unchanged": identity == report.implementation_identity(),
        "control_count": len(hashes), "published": False, "goal_complete": False,
        "full_live_parity_verified": False, "native_broker_verified": False,
        "complete_strategy_admitted": False}
    report.save("verification.json", result)
    print(json.dumps(result), flush=True)
    sys.exit(0 if full and full["exit_code"] == 0 and full["counts"] and unchanged else 1)
