"""Install and verify the read-only MT5 broker-money snapshot service."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

import broker_money
import runtime_paths
from tools import capture_broker_money_contract


DEFAULT_SOURCE = (
    REPO_DIR
    / "mql5"
    / "Services"
    / "BrokerMoneySnapshotService.mq5"
)
SERVICE_FOLDER = Path("MQL5") / "Services" / "TelegramSignalCopier"
COMPILE_LOG_NAME = "broker-money-snapshot-install.log"
MANIFEST_NAME = "BrokerMoneySnapshotService.install.json"
COMPILE_SUCCESS = re.compile(
    r"Result:\s*0 errors,\s*0 warnings",
    flags=re.IGNORECASE,
)
RUNTIME_HEARTBEAT_MAX_AGE_SEC = 60.0


def _active_bot_runtime(
    heartbeat_path: Path | None = None,
    *,
    now: float | None = None,
) -> dict | None:
    """Return a fresh production heartbeat, if the main bot is active."""
    path = Path(
        heartbeat_path
        or os.getenv("BOT_RUNTIME_HEARTBEAT_FILE", "")
        or runtime_paths.data_path(
            "runtime_heartbeat.json", repo=REPO_DIR)
    )
    try:
        age = (time.time() if now is None else now) - path.stat().st_mtime
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if age < 0 or age > RUNTIME_HEARTBEAT_MAX_AGE_SEC:
        return None
    if payload.get("schema_version") != 2 or not payload.get("pid"):
        return None
    return payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read_compile_log(path: Path) -> str:
    raw = Path(path).read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    return raw.decode("utf-8", errors="replace")


def install_service(
    *,
    source: Path,
    terminal_data_path: Path,
    metaeditor: Path,
    runner=subprocess.run,
) -> dict:
    source = Path(source).resolve()
    terminal_data_path = Path(terminal_data_path).resolve()
    metaeditor = Path(metaeditor).resolve()
    if not source.is_file():
        raise RuntimeError(f"missing service source: {source}")
    if not metaeditor.is_file():
        raise RuntimeError(f"missing MetaEditor: {metaeditor}")

    target_dir = terminal_data_path / SERVICE_FOLDER
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name
    compiled = target.with_suffix(".ex5")
    manifest_path = target_dir / MANIFEST_NAME
    staging = target.with_name(
        f".{target.stem}.installing-{os.getpid()}{target.suffix}"
    )
    staging_compiled = staging.with_suffix(".ex5")
    staging.unlink(missing_ok=True)
    staging_compiled.unlink(missing_ok=True)
    staging.write_bytes(source.read_bytes())
    log = terminal_data_path / "MQL5" / "Logs" / COMPILE_LOG_NAME
    log.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(metaeditor),
        f"/compile:{staging}",
        f"/log:{log}",
    ]
    completed = runner(
        command,
        timeout=90,
        check=False,
        capture_output=True,
    )
    if not log.is_file():
        raise RuntimeError(
            f"MQL5 compile log missing (exit={completed.returncode})"
        )
    compile_text = _read_compile_log(log)
    if (
        completed.returncode not in (0, 1)
        or not COMPILE_SUCCESS.search(compile_text)
        or not staging_compiled.is_file()
    ):
        tail = " | ".join(compile_text.splitlines()[-4:])
        staging.unlink(missing_ok=True)
        staging_compiled.unlink(missing_ok=True)
        raise RuntimeError(
            f"MQL5 compile failed (exit={completed.returncode}): {tail}"
        )

    previous_source = target.read_bytes() if target.is_file() else None
    previous_compiled = (
        compiled.read_bytes() if compiled.is_file() else None
    )
    previous_manifest = (
        manifest_path.read_bytes() if manifest_path.is_file() else None
    )
    manifest = {
        "schema_version": 1,
        "source_sha256": _sha256(staging),
        "compiled_sha256": _sha256(staging_compiled),
    }
    staging_manifest = manifest_path.with_suffix(".json.tmp")
    staging_manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    try:
        staging.replace(target)
        staging_compiled.replace(compiled)
        staging_manifest.replace(manifest_path)
    except Exception:
        if previous_source is None:
            target.unlink(missing_ok=True)
        else:
            target.write_bytes(previous_source)
        if previous_compiled is None:
            compiled.unlink(missing_ok=True)
        else:
            compiled.write_bytes(previous_compiled)
        if previous_manifest is None:
            manifest_path.unlink(missing_ok=True)
        else:
            manifest_path.write_bytes(previous_manifest)
        raise
    finally:
        staging.unlink(missing_ok=True)
        staging_compiled.unlink(missing_ok=True)
        staging_manifest.unlink(missing_ok=True)
    return {
        "status": "compiled",
        "source_path": str(target),
        "compiled_path": str(compiled),
        "compile_log": str(log),
        "source_sha256": _sha256(target),
        "compiled_sha256": _sha256(compiled),
        "manifest_path": str(manifest_path),
        "metaeditor_exit_code": completed.returncode,
    }


def _terminal_paths(mt5) -> tuple[Path, Path]:
    terminal = mt5.terminal_info()
    if terminal is None:
        raise RuntimeError("MT5 terminal_info unavailable")
    data_path = Path(str(terminal.data_path))
    metaeditor = Path(str(terminal.path)) / "metaeditor64.exe"
    return data_path, metaeditor


def verify_active_service(mt5, *, source: Path = DEFAULT_SOURCE) -> dict:
    data_path, _metaeditor = _terminal_paths(mt5)
    installed_source = data_path / SERVICE_FOLDER / Path(source).name
    compiled = installed_source.with_suffix(".ex5")
    manifest_path = installed_source.parent / MANIFEST_NAME
    if not installed_source.is_file() or not compiled.is_file():
        raise RuntimeError("broker-money service is not installed")
    if not manifest_path.is_file():
        raise RuntimeError("broker-money service manifest is missing")
    if _sha256(installed_source) != _sha256(source):
        raise RuntimeError("installed broker-money service source is outdated")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("broker-money service manifest is invalid") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 1
        or manifest.get("source_sha256") != _sha256(installed_source)
    ):
        raise RuntimeError("installed broker-money source manifest mismatch")
    if manifest.get("compiled_sha256") != _sha256(compiled):
        raise RuntimeError("installed broker-money EX5 hash mismatch")
    contract = capture_broker_money_contract.build_contract(mt5)
    blockers = broker_money.validate_contract_metadata(contract)
    if blockers:
        raise RuntimeError(
            "invalid broker money contract: " + ",".join(blockers)
        )
    latest = contract["swap_snapshots"][-1]
    return {
        "status": "active",
        "source_sha256": _sha256(installed_source),
        "evidence_source": latest["time_evidence"]["source"],
        "evidence_age_seconds": latest["time_evidence"][
            "evidence_age_seconds"
        ],
        "instrument_symbol": latest["instrument_symbol"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install or verify the read-only MT5 money snapshot service"
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)

    active_runtime = _active_bot_runtime()
    if active_runtime is not None:
        print("Broker-money service: BLOCKED")
        print(
            "The production bot is running "
            f"(pid={active_runtime.get('pid')}, "
            f"exposure={active_runtime.get('exposure_state', 'unknown')})."
        )
        print(
            "Stop run_bot.bat first. This tool opens a separate MT5 Python "
            "session and could disconnect the live bot."
        )
        return 2

    import MetaTrader5 as mt5

    if not mt5.initialize():
        print(f"MT5 initialize failed: {mt5.last_error()}")
        return 1
    try:
        data_path, metaeditor = _terminal_paths(mt5)
        if args.verify_only:
            try:
                result = verify_active_service(mt5, source=args.source)
            except Exception as exc:
                print("Broker-money service: INACTIVE")
                print(f"Reason: {exc}")
                return 1
            print("Broker-money service: ACTIVE")
            print(f"Evidence: {result['evidence_source']}")
            print(
                "Evidence age: "
                f"{result['evidence_age_seconds']:.1f} seconds"
            )
            return 0
    finally:
        mt5.shutdown()

    try:
        result = install_service(
            source=args.source,
            terminal_data_path=data_path,
            metaeditor=metaeditor,
        )
    except Exception as exc:
        print(f"Broker-money service install failed: {exc}")
        return 1

    print("Broker-money service: COMPILED")
    print(f"Installed: {result['compiled_path']}")
    print(
        "Visible one-time step: MT5 > Navigator > Services > "
        "TelegramSignalCopier > BrokerMoneySnapshotService > Add."
    )
    print("Leave algorithmic trading permission disabled for this service.")
    print(
        "Then verify with: python "
        "tools\\install_broker_money_snapshot_service.py --verify-only"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
