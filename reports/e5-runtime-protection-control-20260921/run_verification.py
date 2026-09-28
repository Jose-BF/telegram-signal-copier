"""Reproducible local verification; no runtime connections or live credentials."""

from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(os.environ.get("COPIER_VERIFY_OUTPUT", str(Path(__file__).resolve().parent))).resolve()
sys.path.insert(0, str(ROOT))

from research.iterative_provenance import implementation_identity


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(name, value):
    with (OUT / name).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def sources():
    names = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
    ).decode("utf-8").split("\0")
    return {name: digest(ROOT / name) for name in sorted(set(names))
            if name and (ROOT / name).is_file()
            and (name.endswith((".py", ".ini", ".toml")) or name == "requirements.txt")}


def run(label, args, env):
    command = [sys.executable, "-m", "pytest", "-q", *args,
               f"--junitxml={OUT / (label + '.xml')}"]
    started = time.monotonic()
    print(f"Starting {label}", flush=True)
    with (OUT / (label + "-output.txt")).open("xb") as stream:
        result = subprocess.run(command, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT)
    xml = OUT / (label + ".xml")
    xml_error = None
    try:
        suites = ET.parse(xml).getroot().findall(".//testsuite") if xml.exists() else []
    except ET.ParseError as exc:
        suites, xml_error = [], str(exc)
    counts = {name: sum(int(suite.get(name, 0)) for suite in suites)
              for name in ("tests", "failures", "errors", "skipped")}
    report = {"command": command, "exit_code": result.returncode, "counts": counts if suites else None,
              "junit_error": xml_error,
              "elapsed_seconds": time.monotonic() - started,
              "junit_sha256": digest(xml) if xml.exists() else None}
    save(label + "-verification.json", report)
    print(json.dumps({"phase": label, **report}), flush=True)
    return report


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    runtime = Path(tempfile.mkdtemp(prefix="copier-runtime-protection-"))
    if shutil.disk_usage(runtime).free < 4 * 1024**3:
        raise RuntimeError("insufficient scratch space for verification")
    env["BOT_RUNTIME_DATA_DIR"] = str(runtime / "runtime")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["NUMBA_CACHE_DIR"] = str(runtime / "numba-cache")
    before, implementation = sources(), implementation_identity()
    save("sources.json", {"sources": before, "implementation": implementation,
                          "python": sys.executable, "runtime_dir": env["BOT_RUNTIME_DATA_DIR"],
                          "runner_sha256": digest(Path(__file__)),
                          "temp_dir": str(runtime), "numba_cache_dir": env["NUMBA_CACHE_DIR"]})
    focus = run("focused", ["tests/test_runtime_replay_protection.py",
        "tests/test_runtime_replay_mailbox.py", "tests/test_shared_basket_stop.py",
        "tests/test_e4_pending_revision.py", "tests/test_e4_pending_spool.py",
        "tests/test_iterative_provenance.py", f"--basetemp={runtime / 'focused'}",
        "-o", f"cache_dir={runtime / 'pytest-cache'}"], env)
    controls = OUT / "controls"
    controls.mkdir()
    hashes = {}
    for source in sorted((runtime / "focused").rglob("runtime-control.json")):
        target = controls / (source.parent.name + ".json")
        shutil.copy2(source, target)
        hashes[target.name] = digest(target)
    save("control-manifest.json", {"controls": hashes, "synthetic": True,
        "native_broker_verified": False, "full_live_parity_verified": False})
    full = run("full", [f"--basetemp={runtime / 'full'}", "-o",
                       f"cache_dir={runtime / 'pytest-cache'}"], env) if focus["exit_code"] == 0 else None
    after = sources()
    verification = {"focused": focus, "full": full, "sources_unchanged": before == after,
        "changed_sources": sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name)),
        "implementation_unchanged": implementation == implementation_identity(),
        "control_count": len(hashes), "native_broker_verified": False,
        "complete_strategy_admitted": False, "full_live_parity_verified": False,
        "goal_complete": False, "published": False}
    save("verification.json", verification)
    print(json.dumps(verification), flush=True)
    sys.exit(0 if full and full["exit_code"] == 0 and full["counts"] and before == after else 1)
