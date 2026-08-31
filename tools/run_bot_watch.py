"""
run_bot_watch.py - supervisor de produccion con sincronizacion Git segura.

Uso:
    python tools/run_bot_watch.py

Funcionamiento:
  1. Normaliza Git antes de arrancar y verifica que el codigo sea seguro.
  2. Migra la evidencia viva a un almacen ignorado y deja main solo para codigo.
  3. Cada 60 s consulta origin/main y activa codigo remoto mediante una ruta
     determinista y comprobada.
  4. Antes de reiniciar o cerrar, guarda solo la evidencia cruda de la sesion.
     El pipeline pesado se ejecuta unicamente con --final-backup.
  5. Publica telemetria desde un checkout aislado. Una caida de GitHub no
     detiene la captura; solo codigo inseguro bloquea el arranque.

Detener el wrapper: Ctrl+C (cierra el bot tambien).
"""

import argparse
import os
import json
import math
import signal
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Forzar UTF-8 en stdout/stderr para no crashear con caracteres no-ASCII
# cuando la consola de Windows usa cp1252 (u otro codec estrecho).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

import runtime_paths
import replay_source_contract
from strategy_shadow_contracts import canonical_hash

RUNTIME_DATA_DIR = Path(os.getenv(
    "BOT_RUNTIME_DATA_DIR",
    str(runtime_paths.default_runtime_data_dir(REPO_DIR)),
)).resolve()

import log_learning_publication as learning_publication
import pipeline_progress
import runtime_control
from tools import git_sync
from tools import runtime_recovery
from tools import runtime_log_health
from tools import runtime_telemetry
from tools import set_channel_id

runtime_control.DATA_DIR = RUNTIME_DATA_DIR
runtime_control.PAUSE_FILE = Path(os.getenv(
    "BOT_RUNTIME_PAUSE_FILE",
    str(RUNTIME_DATA_DIR / "runtime_pause.json"),
))
runtime_control.ACTIVITY_FILE = Path(os.getenv(
    "BOT_RUNTIME_ACTIVITY_FILE",
    str(RUNTIME_DATA_DIR / "runtime_handler_activity.json"),
))

MAIN_PY  = REPO_DIR / "main.py"
ACTIVE_CHANNEL_MANIFEST_FILE = REPO_DIR / "active_telegram_channels.json"
ENV_FILE = REPO_DIR / ".env"
RECONCILE_STATUS_FILE = RUNTIME_DATA_DIR / "reconcile_status.json"
REPLAY_STATUS_FILE = RUNTIME_DATA_DIR / "replay_status.json"
ACCOUNTING_REPLAY_AUDIT_STATUS_FILE = RUNTIME_DATA_DIR / "accounting_replay_audit_status.json"
REPLAY_TICK_CACHE_STATUS_FILE = RUNTIME_DATA_DIR / "replay_tick_cache_status.json"
BROKER_MONEY_CONTRACT_FILE = RUNTIME_DATA_DIR / "broker_money_contract.json"
MONEY_TICK_CACHE_STATUS_FILE = RUNTIME_DATA_DIR / "money_tick_cache_status.json"
MONEY_TICK_CACHE_DIR = RUNTIME_DATA_DIR / "money_ticks_cache"
REPLAY_READINESS_REPORT_FILE = RUNTIME_DATA_DIR / "replay_readiness_report.json"
OBSERVED_TICK_REPLAY_AUDIT_FILE = RUNTIME_DATA_DIR / "observed_tick_replay_audit.jsonl"
OBSERVED_TICK_REPLAY_STATUS_FILE = RUNTIME_DATA_DIR / "observed_tick_replay_status.json"
PROVIDER_SIGNAL_CATALOG_FILE = RUNTIME_DATA_DIR / "provider_signal_catalog.json"
PROVIDER_RESULT_SCORECARD_FILE = (
    RUNTIME_DATA_DIR / "provider_result_scorecard.json"
)
STRATEGY_FARM_FILE = RUNTIME_DATA_DIR / "strategy_farm.json"
STRATEGY_SHADOW_REPORT_FILE = (
    RUNTIME_DATA_DIR / "strategy_shadow_report.json"
)
STRATEGY_SHADOW_TICK_CACHE_STATUS_FILE = (
    RUNTIME_DATA_DIR / "strategy_shadow_tick_cache_status.json"
)
LOG_LEARNING_REPORT_FILE = RUNTIME_DATA_DIR / "log_learning_report.json"
LOG_PATTERN_REGISTRY_FILE = RUNTIME_DATA_DIR / "log_pattern_registry.json"
LOG_LEARNING_STATUS_FILE = RUNTIME_DATA_DIR / "log_learning_status.json"
LOG_PATTERN_REVIEWS_FILE = RUNTIME_DATA_DIR / "log_pattern_reviews.json"
STRATEGY_FARM_FROM_DATE = os.getenv("STRATEGY_FARM_FROM_DATE", "2026-07-06")
STRATEGY_SHADOW_FROM_DATE = os.getenv(
    "STRATEGY_SHADOW_FROM_DATE", "2026-08-27",
)
SIMULATION_FROM_DATE = os.getenv("SIMULATION_FROM_DATE")
STRATEGY_FARM_LATENCY_MS = os.getenv("STRATEGY_FARM_LATENCY_MS", "0")
STRATEGY_FARM_VOLUME_PER_LEG = os.getenv(
    "STRATEGY_FARM_VOLUME_PER_LEG", "0.01")
RUNTIME_HEARTBEAT_FILE = Path(os.getenv(
    "BOT_RUNTIME_HEARTBEAT_FILE",
    str(RUNTIME_DATA_DIR / "runtime_heartbeat.json"),
))
RUNTIME_UPDATE_PENDING_FILE = Path(os.getenv(
    "BOT_RUNTIME_UPDATE_PENDING_FILE",
    str(RUNTIME_DATA_DIR / "runtime_update_pending.json"),
))
BOT_RUNTIME_LOG_FILE = RUNTIME_DATA_DIR / "bot_runtime.log"
BOT_RUNTIME_LOG_WARN_BYTES = int(os.getenv(
    "BOT_RUNTIME_LOG_WARN_BYTES", str(512 * 1024 * 1024),
))
POLL_SEC = 60   # cada cuánto comprobar commits nuevos
RESTART_GRACE_SEC = 10  # tiempo para SIGTERM antes de SIGKILL
RELAUNCH_DELAY_SEC = 5  # espera entre fin del bot y relanzamiento
WATCHER_RELOAD_EXIT_CODE = 75
WATCHER_GIT_BLOCKED_EXIT_CODE = 76
WATCHER_GIT_RETRY_EXIT_CODE = 77
WATCHER_DUPLICATE_EXIT_CODE = 78
WATCHER_INSTANCE_PORT = int(os.getenv("BOT_WATCHER_INSTANCE_PORT", "47628"))
RETRYABLE_GIT_ACTIONS = {
    "fetch_failed",
    "post_push_fetch_failed",
    "push_failed",
}
WATCHER_SELF_UPDATE_PATHS = {
    "runtime_paths.py",
    "tools/git_sync.py",
    "tools/run_bot_watch.py",
    "tools/runtime_recovery.py",
    "tools/runtime_log_health.py",
    "tools/runtime_telemetry.py",
    "runtime_control.py",
    "run_bot.bat",
}
WATCHDOG_HEARTBEAT_TIMEOUT_SEC = float(os.getenv(
    "WATCHDOG_HEARTBEAT_TIMEOUT_SEC", "180"))
UPDATE_EXPOSURE_HEARTBEAT_MAX_AGE_SEC = float(os.getenv(
    "BOT_UPDATE_EXPOSURE_HEARTBEAT_MAX_AGE_SEC", "45"))
UPDATE_QUIESCE_CONFIRM_TIMEOUT_SEC = float(os.getenv(
    "BOT_UPDATE_QUIESCE_CONFIRM_TIMEOUT_SEC", "30"))
WATCHDOG_SUPERVISOR_GAP_SEC = float(os.getenv(
    "WATCHDOG_SUPERVISOR_GAP_SEC", "90"))
GIT_TIMEOUT_SEC = float(os.getenv("BOT_GIT_TIMEOUT_SEC", "15"))
WATCHER_QUIESCE_TIMEOUT_SEC = float(os.getenv(
    "BOT_HANDLER_QUIESCE_TIMEOUT_SEC", "30"))
TELEMETRY_PUBLISH_SEC = float(os.getenv(
    "BOT_TELEMETRY_PUBLISH_SEC", "300"))
TELEMETRY_PROCESS_MAX_SEC = float(os.getenv(
    "BOT_TELEMETRY_PROCESS_MAX_SEC", "240"))
TELEMETRY_PROCESS_STOP_TIMEOUT_SEC = 5.0
_telemetry_publish_process = None
_telemetry_publish_started_at = None


@contextmanager
def _runtime_environment():
    previous = os.environ.get("BOT_RUNTIME_DATA_DIR")
    os.environ["BOT_RUNTIME_DATA_DIR"] = str(RUNTIME_DATA_DIR)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("BOT_RUNTIME_DATA_DIR", None)
        else:
            os.environ["BOT_RUNTIME_DATA_DIR"] = previous


class WatcherInstanceGuard:
    """Atomic localhost lock held for the complete watcher lifetime."""

    def __init__(self, port: int = WATCHER_INSTANCE_PORT):
        self.port = port
        self._socket: socket.socket | None = None

    def acquire(self) -> bool:
        if self._socket is not None:
            return True
        owner = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                owner.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            owner.bind(("127.0.0.1", self.port))
            owner.listen(1)
        except OSError:
            owner.close()
            return False
        self._socket = owner
        return True

    def release(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None


def _simulation_from_date() -> str:
    value = str(SIMULATION_FROM_DATE or STRATEGY_FARM_FROM_DATE or "").strip()
    if not value:
        return ""
    datetime.strptime(value, "%Y-%m-%d")
    return value


def _simulation_scope_args() -> list[str]:
    from_date = _simulation_from_date()
    return ["--since", from_date] if from_date else []


def _git(*args: str, capture: bool = True) -> subprocess.CompletedProcess:
    command = ["git", *args]
    try:
        return subprocess.run(
            command,
            cwd=REPO_DIR,
            capture_output=capture,
            text=True,
            check=False,
            timeout=GIT_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            command,
            returncode=124,
            stdout=exc.stdout or "",
            stderr=(
                f"git {' '.join(args)} timed out after "
                f"{GIT_TIMEOUT_SEC:g}s"
            ),
        )


def _print_git_progress(stage: str) -> None:
    labels = {
        "inspect": "comprobando estado local",
        "recover": "protegiendo datos de una sesion interrumpida",
        "fetch": "consultando origin/main",
        "rebase": "integrando datos locales sobre main",
        "push": "subiendo datos de sesion",
        "post_push_fetch": "confirmando la referencia remota",
        "verify": "verificando main limpio y sincronizado",
    }
    print(f"[Watch] Git: {labels.get(stage, stage)}...", flush=True)


def _recover_runtime_worktree(repo_dir: Path):
    migration = runtime_paths.initialize_runtime_store(
        repo_dir,
        runtime_dir=RUNTIME_DATA_DIR,
        code_commit=_local_head() or None,
    )
    if migration.copied or migration.archived_tails:
        print(
            f"[Watch] Runtime: migrados={len(migration.copied)} "
            f"colas_rescatadas={len(migration.archived_tails)}",
            flush=True,
        )
    recovery = runtime_recovery.prepare_runtime_worktree(
        repo_dir,
        runtime_dir=RUNTIME_DATA_DIR,
    )
    if recovery.action != "clean":
        print(
            f"[Watch] Recuperacion local action={recovery.action} "
            f"raw={len(recovery.source_paths)} "
            f"restaurados={len(recovery.restored_paths)} "
            f"archivados={len(recovery.archived_paths)}",
            flush=True,
        )
    return recovery


def _prepare_repository_for_runtime() -> git_sync.SyncResult:
    return git_sync.synchronize_repository(
        REPO_DIR,
        publish_local=False,
        progress_callback=_print_git_progress,
        worktree_recovery=_recover_runtime_worktree,
    )


def _checkpoint_runtime_data() -> git_sync.SyncResult:
    """Create immutable local chunks without touching Git or the network."""
    checkpoint = runtime_telemetry.checkpoint_runtime(
        RUNTIME_DATA_DIR,
        code_commit=_local_head() or None,
    )
    local_head = _local_head()
    remote_head = _remote_head()
    if checkpoint.ok:
        action = (
            "telemetry_checkpointed"
            if checkpoint.chunks
            else "telemetry_checkpoint_clean"
        )
        print(
            f"[Watch] Telemetria local confirmada: "
            f"{len(checkpoint.chunks)} fragmentos nuevos.",
            flush=True,
        )
        error = None
    else:
        action = "telemetry_checkpoint_degraded"
        error = "; ".join(checkpoint.errors)
        print(
            f"[Watch] Telemetria pendiente de revision: {error}. "
            "El bot puede reiniciar porque los archivos originales siguen intactos.",
            flush=True,
        )
    return git_sync.SyncResult(
        ok=True,
        action=action,
        branch=_current_branch(),
        local_head=local_head or None,
        remote_head=remote_head or None,
        error=error,
    )


def _trigger_telemetry_publication(*, now: float | None = None) -> bool:
    """Launch one isolated publication attempt and return immediately."""
    global _telemetry_publish_process, _telemetry_publish_started_at
    current_time = time.time() if now is None else float(now)
    if (
        _telemetry_publish_process is not None
        and _telemetry_publish_process.poll() is None
    ):
        if _telemetry_publish_started_at is None:
            _telemetry_publish_started_at = current_time
            return True
        elapsed = current_time - _telemetry_publish_started_at
        if elapsed <= TELEMETRY_PROCESS_MAX_SEC:
            return True
        print(
            f"[Watch] Publicador de telemetria atascado "
            f"({elapsed:.0f}s). Lo reemplazo sin detener el bot.",
            flush=True,
        )
        runtime_telemetry.terminate_process_tree(
            _telemetry_publish_process,
            timeout_sec=TELEMETRY_PROCESS_STOP_TIMEOUT_SEC,
        )
        _telemetry_publish_process = None
        _telemetry_publish_started_at = None
    command = [
        sys.executable,
        str(REPO_DIR / "tools" / "runtime_telemetry.py"),
        "--publish-once",
        "--runtime-dir",
        str(RUNTIME_DATA_DIR),
        "--timeout",
        str(GIT_TIMEOUT_SEC),
    ]
    environment = os.environ.copy()
    environment["BOT_RUNTIME_DATA_DIR"] = str(RUNTIME_DATA_DIR)
    try:
        _telemetry_publish_process = subprocess.Popen(
            command,
            cwd=REPO_DIR,
            env=environment,
            **runtime_telemetry._process_group_kwargs(),
        )
        _telemetry_publish_started_at = current_time
    except OSError as exc:
        _telemetry_publish_process = None
        _telemetry_publish_started_at = None
        print(
            f"[Watch] Telemetria remota pendiente: {exc}. El bot sigue activo.",
            flush=True,
        )
        return False
    return True


def _offline_runtime_fallback(
    failed: git_sync.SyncResult,
) -> git_sync.SyncResult:
    """Keep known-safe code available when only Git transport is down."""
    if failed.action not in RETRYABLE_GIT_ACTIONS:
        return failed
    if not git_sync.runtime_head_is_safe(REPO_DIR):
        return failed
    print(
        "[Watch] Git remoto no disponible; continuo con el mismo codigo "
        "verificado y reintentare la publicacion en segundo plano.",
        flush=True,
    )
    return git_sync.SyncResult(
        ok=True,
        action="offline_local_verified",
        branch=_current_branch(),
        local_head=_local_head() or None,
        remote_head=_remote_head() or None,
    )


def _previous_verified_runtime_fallback(
    failed: git_sync.SyncResult,
    previous_head: str,
) -> git_sync.SyncResult:
    """Keep the exact previously running build after a failed hot-update."""

    if failed.ok or not git_sync.verified_runtime_head_is_available(
        REPO_DIR,
        previous_head,
    ):
        return failed
    print(
        "[Watch] La actualizacion no pudo activarse; relanzo la version "
        "anterior ya verificada y reintentare el cambio.",
        flush=True,
    )
    return git_sync.SyncResult(
        ok=True,
        action="previous_verified_code",
        branch=_current_branch(),
        local_head=_local_head() or None,
        remote_head=_remote_head() or None,
        error=failed.error,
    )


def _apply_active_channel_manifest() -> bool:
    """Synchronize public Telegram routing before importing `config` in main."""
    if not ENV_FILE.is_file():
        print(
            f"[Watch] Canales: ERROR no existe el .env completo: {ENV_FILE}",
            flush=True,
        )
        return False
    try:
        result = set_channel_id.apply_channel_manifest(
            ACTIVE_CHANNEL_MANIFEST_FILE,
            env_file=ENV_FILE,
        )
    except (OSError, ValueError) as exc:
        print(
            f"[Watch] Canales: ERROR verificando configuracion: {exc}",
            flush=True,
        )
        return False
    active = result["active_ids"]
    if result["changed"]:
        changed = ", ".join(result["changed"])
        print(
            f"[Watch] Canales: configuracion actualizada ({changed}).",
            flush=True,
        )
        if result["backup"] is not None:
            print(
                f"[Watch] Canales: backup local {result['backup'].name}",
                flush=True,
            )
    else:
        print("[Watch] Canales: configuracion verificada.", flush=True)
    for channel, channel_id in active.items():
        print(f"[Watch] Canal activo {channel}={channel_id}", flush=True)
    return True


def _spawn_bot_with_active_channels(verified_head: str | None = None):
    if not _apply_active_channel_manifest():
        return None
    if verified_head is None:
        return _spawn_bot()
    return _spawn_bot(verified_head=verified_head)


def _print_sync_result(result: git_sync.SyncResult) -> None:
    local = (result.local_head or "unknown")[:8]
    remote = (result.remote_head or "unknown")[:8]
    print(
        f"[Watch] Git action={result.action} branch={result.branch or 'DETACHED'} "
        f"local={local} remote={remote}",
        flush=True,
    )
    if result.rescue_branch:
        print(f"[Watch] Rescate Git: {result.rescue_branch}", flush=True)
    if result.error:
        print(f"[Watch] Git ERROR: {result.error}", flush=True)


def _sync_failure_exit_code(result: git_sync.SyncResult) -> int:
    if result.action in RETRYABLE_GIT_ACTIONS:
        return WATCHER_GIT_RETRY_EXIT_CODE
    return WATCHER_GIT_BLOCKED_EXIT_CODE


def _local_head() -> str:
    return _git("rev-parse", "HEAD").stdout.strip()


def _remote_head() -> str:
    return _git("rev-parse", "origin/main").stdout.strip()


def _current_branch() -> str | None:
    result = _git("symbolic-ref", "--quiet", "--short", "HEAD")
    value = (result.stdout or "").strip()
    return value or None


def _remote_update_is_data_only(old_rev: str, new_rev: str) -> bool:
    """True if every commit between refs is a watcher data upload."""
    if old_rev == new_rev:
        return True
    log = _git("log", "--format=%s", f"{old_rev}..{new_rev}")
    if log.returncode != 0:
        return False
    subjects = [line.strip() for line in (log.stdout or "").splitlines()
                if line.strip()]
    if not subjects or not all(
        subject.startswith("data:") for subject in subjects
    ):
        return False
    diff = _git(
        "diff", "--name-only", "--no-renames", f"{old_rev}..{new_rev}"
    )
    if diff.returncode != 0:
        return False
    paths = [
        line.strip().replace("\\", "/")
        for line in (diff.stdout or "").splitlines()
        if line.strip()
    ]
    return bool(paths) and all(
        path == "data" or path.startswith("data/") for path in paths
    )


def _paths_changed_between(old_rev: str, new_rev: str,
                           watched_paths: set[str]) -> bool:
    diff = _git("diff", "--name-only", f"{old_rev}..{new_rev}")
    if diff.returncode != 0:
        return False
    changed = {line.strip().replace("\\", "/")
               for line in (diff.stdout or "").splitlines()
               if line.strip()}
    return bool(changed.intersection(watched_paths))


def _clear_runtime_heartbeat(path: Path = RUNTIME_HEARTBEAT_FILE) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[Watch] no pude limpiar heartbeat runtime: {e}", flush=True)


def _runtime_heartbeat_age_s(path: Path = RUNTIME_HEARTBEAT_FILE,
                             now: float | None = None) -> float | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    now = time.time() if now is None else now
    return max(0.0, now - stat.st_mtime)


def _read_runtime_exposure(
    path: Path = RUNTIME_HEARTBEAT_FILE,
    *,
    now: float | None = None,
    max_age_s: float = UPDATE_EXPOSURE_HEARTBEAT_MAX_AGE_SEC,
) -> dict:
    """Read the bot's exposure contract; uncertainty never means flat."""
    unknown = {
        "exposure_state": "unknown",
        "bot_position_count": None,
        "open_signal_count": None,
    }
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {**unknown, "reason": "heartbeat_missing"}
    except OSError as exc:
        return {
            **unknown,
            "reason": "heartbeat_unreadable",
            "error": str(exc)[:200],
        }

    now = time.time() if now is None else now
    age_s = max(0.0, now - stat.st_mtime)
    if max_age_s > 0 and age_s > max_age_s:
        return {
            **unknown,
            "reason": "heartbeat_stale",
            "heartbeat_age_s": round(age_s, 3),
        }

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            **unknown,
            "reason": "heartbeat_invalid",
            "heartbeat_age_s": round(age_s, 3),
            "error": str(exc)[:200],
        }

    if int(payload.get("schema_version") or 0) < 2:
        return {
            **unknown,
            "reason": "heartbeat_schema_unsupported",
            "heartbeat_age_s": round(age_s, 3),
        }

    exposure_state = str(
        payload.get("exposure_state") or "unknown").lower()
    if exposure_state not in {"open", "flat", "unknown"}:
        return {
            **unknown,
            "reason": "heartbeat_exposure_invalid",
            "heartbeat_age_s": round(age_s, 3),
        }

    bot_position_count = payload.get("bot_position_count")
    open_signal_count = payload.get("open_signal_count")
    if (exposure_state == "flat"
            and (bot_position_count != 0 or open_signal_count != 0)):
        return {
            **unknown,
            "reason": "heartbeat_exposure_inconsistent",
            "heartbeat_age_s": round(age_s, 3),
        }

    return {
        "exposure_state": exposure_state,
        "bot_position_count": bot_position_count,
        "open_signal_count": open_signal_count,
        "reason": f"heartbeat_reported_{exposure_state}",
        "heartbeat_age_s": round(age_s, 3),
        "heartbeat_utc": payload.get("utc"),
        "pid": payload.get("pid"),
    }


def _write_runtime_update_pending(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _record_runtime_update_pending(
    local_revision: str,
    remote_revision: str,
    exposure: dict,
    *,
    pending_path: Path = RUNTIME_UPDATE_PENDING_FILE,
) -> None:
    reason = (
        "open_exposure"
        if exposure.get("exposure_state") == "open"
        else "exposure_unknown"
    )
    exposure_payload = dict(exposure)
    exposure_reason = exposure_payload.pop("reason", None)
    _write_runtime_update_pending(
        pending_path,
        {
            "schema_version": 1,
            "detected_utc": datetime.now(timezone.utc).isoformat(
                timespec="seconds"),
            "local_revision": local_revision,
            "remote_revision": remote_revision,
            **exposure_payload,
            "reason": reason,
            "exposure_reason": exposure_reason,
        },
    )


def _clear_runtime_update_pending(
    path: Path = RUNTIME_UPDATE_PENDING_FILE,
) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        print(
            f"[Watch] No pude limpiar actualizacion pendiente: {exc}",
            flush=True,
        )


def _defer_code_update_if_exposed(
    local_revision: str,
    remote_revision: str,
    *,
    heartbeat_path: Path = RUNTIME_HEARTBEAT_FILE,
    pending_path: Path = RUNTIME_UPDATE_PENDING_FILE,
    now: float | None = None,
    max_age_s: float = UPDATE_EXPOSURE_HEARTBEAT_MAX_AGE_SEC,
) -> tuple[bool, dict]:
    """Defer activation unless a fresh heartbeat proves the bot is flat."""
    exposure = _read_runtime_exposure(
        heartbeat_path, now=now, max_age_s=max_age_s)
    if exposure["exposure_state"] == "flat":
        _clear_runtime_update_pending(pending_path)
        return False, exposure

    _record_runtime_update_pending(
        local_revision,
        remote_revision,
        exposure,
        pending_path=pending_path,
    )
    return True, exposure


def _wait_for_post_quiesce_exposure(
    *,
    child_pid: int,
    heartbeat_path: Path = RUNTIME_HEARTBEAT_FILE,
    not_before: float,
    timeout_s: float = UPDATE_QUIESCE_CONFIRM_TIMEOUT_SEC,
    now_fn=time.time,
    sleep_fn=time.sleep,
) -> dict:
    """Wait for exposure evidence produced after Telegram dispatch paused."""
    deadline = now_fn() + max(0.0, timeout_s)
    last_reason = "post_quiesce_heartbeat_missing"
    while True:
        now = now_fn()
        try:
            heartbeat_mtime = heartbeat_path.stat().st_mtime
        except OSError:
            heartbeat_mtime = None

        if heartbeat_mtime is not None and heartbeat_mtime >= not_before:
            exposure = _read_runtime_exposure(
                heartbeat_path,
                now=now,
                max_age_s=UPDATE_EXPOSURE_HEARTBEAT_MAX_AGE_SEC,
            )
            if exposure.get("pid") != child_pid:
                last_reason = "post_quiesce_heartbeat_pid_mismatch"
            elif exposure.get("exposure_state") in {"flat", "open"}:
                return exposure
            else:
                last_reason = str(
                    exposure.get("reason")
                    or "post_quiesce_exposure_unknown"
                )

        if now >= deadline:
            break
        sleep_fn(min(0.1, max(0.0, deadline - now)))

    return {
        "exposure_state": "unknown",
        "bot_position_count": None,
        "open_signal_count": None,
        "reason": last_reason,
    }


def _quiesce_code_update(
    proc: subprocess.Popen,
    *,
    heartbeat_path: Path = RUNTIME_HEARTBEAT_FILE,
) -> tuple[bool, dict]:
    """Close the flat-heartbeat race before interrupting live code."""
    child_pid = getattr(proc, "pid", None)
    if child_pid is None or proc.poll() is not None:
        return False, {
            "exposure_state": "unknown",
            "bot_position_count": None,
            "open_signal_count": None,
            "reason": "bot_process_not_running",
        }

    runtime_control.request_pause("watcher_code_update")
    deadline = time.time() + WATCHER_QUIESCE_TIMEOUT_SEC
    active = runtime_control.active_handler_count(child_pid)
    while (
        active > 0
        and proc.poll() is None
        and time.time() < deadline
    ):
        time.sleep(0.1)
        active = runtime_control.active_handler_count(child_pid)

    if active > 0 or proc.poll() is not None:
        runtime_control.clear_pause()
        return False, {
            "exposure_state": "unknown",
            "bot_position_count": None,
            "open_signal_count": None,
            "reason": (
                "handler_quiesce_timeout"
                if active > 0 else "bot_process_stopped_during_quiesce"
            ),
        }

    quiesced_at = time.time()
    exposure = _wait_for_post_quiesce_exposure(
        child_pid=child_pid,
        heartbeat_path=heartbeat_path,
        not_before=quiesced_at,
    )
    if exposure["exposure_state"] == "flat":
        return True, exposure

    runtime_control.clear_pause()
    return False, exposure


def _runtime_heartbeat_is_stale(heartbeat_age_s: float | None,
                                process_uptime_s: float,
                                timeout_s: float) -> bool:
    if timeout_s <= 0:
        return False
    if heartbeat_age_s is None:
        return process_uptime_s > timeout_s
    return heartbeat_age_s > timeout_s


def _supervisor_loop_gap_is_stale(
    previous_tick: float,
    current_tick: float,
    timeout_s: float,
) -> bool:
    if timeout_s <= 0:
        return False
    return current_tick - previous_tick > timeout_s


def _spawn_bot(*, verified_head: str | None = None) -> subprocess.Popen | None:
    local_head = _local_head()
    remote_head = _remote_head()
    runtime_safe = (
        git_sync.runtime_head_is_safe(REPO_DIR)
        if verified_head is None
        else git_sync.verified_runtime_head_is_available(
            REPO_DIR,
            verified_head,
        )
    )
    if (
        not local_head
        or (verified_head is not None and local_head != verified_head)
        or not runtime_safe
    ):
        print(
            f"[Watch] Spawn bloqueado: codigo local no verificable "
            f"HEAD={(local_head or 'unknown')[:8]} "
            f"origin/main={(remote_head or 'unknown')[:8]}",
            flush=True,
        )
        return None

    _clear_runtime_heartbeat()
    runtime_control.clear_for_spawn()
    print(f"[Watch] Lanzando bot: python {MAIN_PY}", flush=True)
    # Usamos el mismo intérprete que ejecuta este script.
    # creationflags en Windows para poder mandar Ctrl-Break al subproceso.
    child_env = os.environ.copy()
    child_env["BOT_RUNTIME_DATA_DIR"] = str(RUNTIME_DATA_DIR)
    child_env["BOT_WATCHER_VERIFIED_HEAD"] = local_head
    child_env["BOT_WATCHER_RUNTIME_SAFE"] = "1"
    child_env["BOT_WATCHER_PID"] = str(os.getpid())
    kwargs = {"env": child_env}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen([sys.executable, str(MAIN_PY)], cwd=REPO_DIR, **kwargs)


def _stop_bot(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return  # ya terminó
    child_pid = getattr(proc, "pid", None)
    if child_pid is not None:
        runtime_control.request_pause("watcher_restart")
        deadline = time.time() + WATCHER_QUIESCE_TIMEOUT_SEC
        active = runtime_control.active_handler_count(child_pid)
        if active:
            print(
                f"[Watch] Esperando {active} handler(s) Telegram en curso "
                "antes de reiniciar...",
                flush=True,
            )
        while (
            active > 0
            and proc.poll() is None
            and time.time() < deadline
        ):
            time.sleep(0.1)
            active = runtime_control.active_handler_count(child_pid)
        if active > 0:
            print(
                f"[Watch] Quiesce agotado con {active} handler(s); "
                "la recuperacion los reintentara.",
                flush=True,
            )
    print("[Watch] Parando bot...", flush=True)
    try:
        if sys.platform == "win32":
            # Ctrl-Break es la forma "amable" en Windows para process groups
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            proc.terminate()
    except Exception as e:
        print(f"[Watch] Error mandando señal: {e}", flush=True)

    try:
        proc.wait(timeout=RESTART_GRACE_SEC)
        print("[Watch] Bot terminó limpiamente.", flush=True)
    except subprocess.TimeoutExpired:
        print(f"[Watch] Bot no respondió en {RESTART_GRACE_SEC}s, kill.", flush=True)
        proc.kill()
        proc.wait()


def _write_reconcile_status(status: dict) -> None:
    try:
        RECONCILE_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        RECONCILE_STATUS_FILE.write_text(
            json.dumps(status, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
    except Exception as e:
        print(f"[Watch] no pude escribir reconcile_status.json: {e}",
              flush=True)


def _regenerate_ledger() -> bool:
    """Ejecuta reconcile_mt5_ledger.py para regenerar data/ledger.jsonl.

    El ledger cruza el journal del bot con el historial de MT5 y produce
    la FUENTE DE VERDAD reconciliada (1 fila/trade, P&L verificado). Se
    regenera antes de cada push para que la sesion subida a GitHub lleve
    el ledger ya hecho, listo para auditar sin trabajo manual.

    El bot esta parado cuando esto corre (push_session_data se llama tras
    parar/reiniciar) → MT5 esta libre. Best-effort: si falla, log y sigue.
    """
    started = time.time()
    ledger_file = RUNTIME_DATA_DIR / "ledger.jsonl"
    status = {
        "ok": False,
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "finished_at": None,
        "duration_s": None,
        "returncode": None,
        "stdout": "",
        "stderr": "",
        "ledger_exists": ledger_file.exists(),
        "ledger_size_bytes": ledger_file.stat().st_size if ledger_file.exists() else 0,
        "command": [sys.executable, "reconcile_mt5_ledger.py", "--quiet"],
    }
    try:
        rec = subprocess.run(
            [sys.executable, "reconcile_mt5_ledger.py", "--quiet"],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=180,
        )
        status.update({
            "ok": rec.returncode == 0,
            "returncode": rec.returncode,
            "stdout": rec.stdout or "",
            "stderr": rec.stderr or "",
        })
        if rec.returncode == 0:
            print("[Watch] ledger reconciliado regenerado.", flush=True)
        else:
            print(f"[Watch] reconcile_mt5_ledger.py fallo (rc={rec.returncode}): "
                  f"{(rec.stderr or rec.stdout or '')[:1000]}", flush=True)
    except BaseException as e:
        status.update({
            "ok": False,
            "exception_type": type(e).__name__,
            "stderr": str(e),
        })
        print(f"[Watch] error ejecutando reconcile_mt5_ledger.py: {e}", flush=True)
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
    finally:
        status["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        status["duration_s"] = round(time.time() - started, 2)
        status["ledger_exists"] = ledger_file.exists()
        status["ledger_size_bytes"] = (
            ledger_file.stat().st_size if ledger_file.exists() else 0
        )
        _write_reconcile_status(status)
    return bool(status["ok"])


def _write_replay_status(status: dict) -> None:
    try:
        REPLAY_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        REPLAY_STATUS_FILE.write_text(
            json.dumps(status, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
    except Exception as e:
        print(f"[Watch] no pude escribir replay_status.json: {e}",
              flush=True)


def _write_accounting_replay_audit_status(status: dict) -> None:
    try:
        ACCOUNTING_REPLAY_AUDIT_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        ACCOUNTING_REPLAY_AUDIT_STATUS_FILE.write_text(
            json.dumps(status, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
    except Exception as e:
        print(f"[Watch] no pude escribir accounting_replay_audit_status.json: {e}",
              flush=True)


def _regenerate_replay_trades() -> bool:
    """Regenera data/replay_trades.jsonl desde ledger + journal.

    Es un artefacto derivado: si falla no debe impedir relanzar el bot, pero
    deja status explicito para que sepamos si la sesion quedo simulable.
    """
    started = time.time()
    replay_file = RUNTIME_DATA_DIR / "replay_trades.jsonl"
    manifest_file = replay_source_contract.default_manifest_path(replay_file)
    replay_file.unlink(missing_ok=True)
    manifest_file.unlink(missing_ok=True)
    status = {
        "ok": False,
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "finished_at": None,
        "duration_s": None,
        "returncode": None,
        "stdout": "",
        "stderr": "",
        "replay_exists": replay_file.exists(),
        "replay_size_bytes": replay_file.stat().st_size if replay_file.exists() else 0,
        "manifest_exists": manifest_file.exists(),
        "manifest_size_bytes": (
            manifest_file.stat().st_size if manifest_file.exists() else 0
        ),
        "source_contract_verified": False,
        "source_contract_errors": [],
        "command": [sys.executable, "build_replay_trades.py", "--quiet"],
    }
    try:
        rec = subprocess.run(
            [sys.executable, "build_replay_trades.py", "--quiet"],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=60,
        )
        source_errors = (
            replay_source_contract.validate_manifest(
                replay_path=replay_file,
                ledger_path=RUNTIME_DATA_DIR / "ledger.jsonl",
                events_path=RUNTIME_DATA_DIR / "trade_events.jsonl",
                manifest_path=manifest_file,
            )
            if rec.returncode == 0
            else ["replay_build_failed"]
        )
        source_contract_verified = not source_errors
        status.update({
            "ok": rec.returncode == 0 and source_contract_verified,
            "returncode": rec.returncode,
            "stdout": rec.stdout or "",
            "stderr": rec.stderr or "",
            "source_contract_verified": source_contract_verified,
            "source_contract_errors": source_errors,
        })
        if status["ok"]:
            print("[Watch] replay_trades regenerado.", flush=True)
        elif rec.returncode == 0:
            print(
                "[Watch] replay_trades rechazado: contrato de origen "
                f"invalido ({', '.join(source_errors)}).",
                flush=True,
            )
        else:
            print(f"[Watch] build_replay_trades.py fallo (rc={rec.returncode}): "
                  f"{(rec.stderr or rec.stdout or '')[:1000]}", flush=True)
    except BaseException as e:
        status.update({
            "ok": False,
            "exception_type": type(e).__name__,
            "stderr": str(e),
        })
        print(f"[Watch] error ejecutando build_replay_trades.py: {e}", flush=True)
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
    finally:
        status["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        status["duration_s"] = round(time.time() - started, 2)
        status["replay_exists"] = replay_file.exists()
        status["replay_size_bytes"] = (
            replay_file.stat().st_size if replay_file.exists() else 0
        )
        status["manifest_exists"] = manifest_file.exists()
        status["manifest_size_bytes"] = (
            manifest_file.stat().st_size if manifest_file.exists() else 0
        )
        _write_replay_status(status)
    return bool(status["ok"])


def _regenerate_accounting_replay_audit() -> bool:
    """Regenera data/accounting_replay_audit.jsonl desde replay_trades.jsonl."""
    started = time.time()
    audit_file = RUNTIME_DATA_DIR / "accounting_replay_audit.jsonl"
    status = {
        "ok": False,
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "finished_at": None,
        "duration_s": None,
        "returncode": None,
        "stdout": "",
        "stderr": "",
        "audit_exists": audit_file.exists(),
        "audit_size_bytes": audit_file.stat().st_size if audit_file.exists() else 0,
        "command": [sys.executable, "accounting_replay_validator.py", "--quiet"],
    }
    try:
        rec = subprocess.run(
            [sys.executable, "accounting_replay_validator.py", "--quiet"],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=60,
        )
        status.update({
            "ok": rec.returncode == 0,
            "returncode": rec.returncode,
            "stdout": rec.stdout or "",
            "stderr": rec.stderr or "",
        })
        if rec.returncode == 0:
            print("[Watch] accounting_replay_audit regenerado.", flush=True)
        else:
            print(f"[Watch] accounting_replay_validator.py fallo (rc={rec.returncode}): "
                  f"{(rec.stderr or rec.stdout or '')[:1000]}", flush=True)
    except BaseException as e:
        status.update({
            "ok": False,
            "exception_type": type(e).__name__,
            "stderr": str(e),
        })
        print(f"[Watch] error ejecutando accounting_replay_validator.py: {e}", flush=True)
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
    finally:
        status["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        status["duration_s"] = round(time.time() - started, 2)
        status["audit_exists"] = audit_file.exists()
        status["audit_size_bytes"] = (
            audit_file.stat().st_size if audit_file.exists() else 0
        )
        _write_accounting_replay_audit_status(status)
    return bool(status["ok"])


def _regenerate_replay_tick_cache_status() -> bool:
    """Asegura/cachea ticks necesarios por replay y escribe status JSON."""
    REPLAY_TICK_CACHE_STATUS_FILE.unlink(missing_ok=True)
    try:
        command = [
            sys.executable,
            "tools/ensure_replay_tick_cache.py",
            "--ensure",
            *_simulation_scope_args(),
        ]
        if PROVIDER_SIGNAL_CATALOG_FILE.is_file():
            command.extend([
                "--catalog",
                str(PROVIDER_SIGNAL_CATALOG_FILE),
                "--provider-until",
                _strategy_shadow_until_date(),
            ])
        command.append("--quiet")
        rec = subprocess.run(
            command,
            cwd=REPO_DIR, capture_output=True, text=True, timeout=900,
        )
        if rec.returncode == 0:
            print("[Watch] replay_tick_cache verificado/regenerado.", flush=True)
            return True
        print(f"[Watch] ensure_replay_tick_cache.py aviso/fallo (rc={rec.returncode}): "
              f"{(rec.stderr or rec.stdout or '')[:1000]}", flush=True)
        return False
    except BaseException as e:
        print(f"[Watch] error ejecutando ensure_replay_tick_cache.py: {e}", flush=True)
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        return False


def _regenerate_replay_readiness_report() -> bool:
    """Genera reporte diario de preparacion para replay tick-a-tick.

    replay_readiness_report.py devuelve rc=1 cuando hay trades bloqueados. Eso
    no es crash: el reporte es precisamente la alarma que queremos subir.
    """
    REPLAY_READINESS_REPORT_FILE.unlink(missing_ok=True)
    try:
        command = [
            sys.executable,
            "replay_readiness_report.py",
            *_simulation_scope_args(),
            "--quiet",
        ]
        rec = subprocess.run(
            command,
            cwd=REPO_DIR, capture_output=True, text=True, timeout=60,
        )
        if rec.returncode == 0:
            print("[Watch] replay_readiness_report OK.", flush=True)
            return True
        if REPLAY_READINESS_REPORT_FILE.exists():
            print("[Watch] replay_readiness_report generado con bloqueos.",
                  flush=True)
            return True
        print(f"[Watch] replay_readiness_report.py fallo (rc={rec.returncode}): "
              f"{(rec.stderr or rec.stdout or '')[:1000]}", flush=True)
        return False
    except BaseException as e:
        print(f"[Watch] error ejecutando replay_readiness_report.py: {e}",
              flush=True)
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        return False


def _regenerate_observed_tick_replay_audit() -> bool:
    """Genera auditoria observed tick replay por ticket MT5.

    observed_tick_replay_validator.py devuelve rc=1 cuando hay mismatches o bloqueos.
    Eso no es crash: esos ficheros son el chivato que necesitamos subir para
    saber exactamente que ticket no se pudo reproducir contra bid/ask ticks.
    """
    OBSERVED_TICK_REPLAY_AUDIT_FILE.unlink(missing_ok=True)
    OBSERVED_TICK_REPLAY_STATUS_FILE.unlink(missing_ok=True)
    try:
        command = [
            sys.executable,
            "observed_tick_replay_validator.py",
            *_simulation_scope_args(),
            "--quiet",
        ]
        rec = subprocess.run(
            command,
            cwd=REPO_DIR, capture_output=True, text=True, timeout=120,
        )
        if rec.returncode == 0:
            print("[Watch] observed_tick_replay_audit OK.", flush=True)
            return True
        if OBSERVED_TICK_REPLAY_AUDIT_FILE.exists() and OBSERVED_TICK_REPLAY_STATUS_FILE.exists():
            print("[Watch] observed_tick_replay_audit generado con bloqueos/mismatches.",
                  flush=True)
            return True
        print(f"[Watch] observed_tick_replay_validator.py fallo (rc={rec.returncode}): "
              f"{(rec.stderr or rec.stdout or '')[:1000]}", flush=True)
        return False
    except BaseException as e:
        print(f"[Watch] error ejecutando observed_tick_replay_validator.py: {e}",
              flush=True)
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        return False


def _clear_mutable_offline_outputs() -> None:
    """Prevent skipped/failed builders from leaving reports that look current."""
    PROVIDER_SIGNAL_CATALOG_FILE.unlink(missing_ok=True)
    PROVIDER_RESULT_SCORECARD_FILE.unlink(missing_ok=True)
    STRATEGY_FARM_FILE.unlink(missing_ok=True)
    STRATEGY_SHADOW_REPORT_FILE.unlink(missing_ok=True)
    STRATEGY_SHADOW_TICK_CACHE_STATUS_FILE.unlink(missing_ok=True)
    LOG_LEARNING_REPORT_FILE.unlink(missing_ok=True)
    LOG_PATTERN_REGISTRY_FILE.unlink(missing_ok=True)
    LOG_LEARNING_STATUS_FILE.unlink(missing_ok=True)


def _regenerate_provider_signal_catalog() -> bool:
    """Build the provider timeline independently from MT5 execution."""
    PROVIDER_SIGNAL_CATALOG_FILE.unlink(missing_ok=True)
    try:
        rec = subprocess.run(
            [sys.executable, "provider_signal_catalog.py", "--quiet"],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=60,
        )
        if rec.returncode == 0 and PROVIDER_SIGNAL_CATALOG_FILE.exists():
            print("[Watch] provider_signal_catalog regenerado.", flush=True)
            return True
        print(f"[Watch] provider_signal_catalog.py fallo (rc={rec.returncode}): "
              f"{(rec.stderr or rec.stdout or '')[:1000]}", flush=True)
        return False
    except BaseException as e:
        print(f"[Watch] error ejecutando provider_signal_catalog.py: {e}",
              flush=True)
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        return False


def _strategy_farm_publication_valid(path: Path) -> bool:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(report, dict):
        return False

    scope = report.get("provider_scope")
    executed_scope = report.get("executed_scope")
    executed_contract = report.get("executed_replay_contract")
    validation = report.get("validation")
    provenance = report.get("provenance")
    if not all(isinstance(item, dict) for item in (
        scope,
        executed_scope,
        executed_contract,
        validation,
        provenance,
    )):
        return False
    if (
        report.get("primary_universe") != "executed_mt5"
        or validation.get("primary_universe") != "executed_mt5"
        or validation.get("price_path_mode") != "executed_mt5_entries"
    ):
        return False
    validation_mode = validation.get("mode")
    if validation_mode not in {"diagnostic_only", "verified_simulation"}:
        return False

    count_keys = (
        "formal_signals",
        "policy_count",
        "rows_expected",
        "rows_emitted",
        "simulated_rows",
        "blocked_rows",
    )
    counts = {key: scope.get(key) for key in count_keys}
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in counts.values()
    ):
        return False
    latencies = scope.get("latency_scenarios_ms")
    omitted = scope.get("signals_omitted")
    if (
        not isinstance(latencies, list)
        or not latencies
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in latencies
        )
        or len(set(latencies)) != len(latencies)
        or not isinstance(omitted, list)
        or omitted
    ):
        return False
    expected = (
        counts["formal_signals"]
        * counts["policy_count"]
        * len(latencies)
    )
    if not (
        counts["rows_expected"] == expected
        and counts["rows_emitted"] == expected
        and counts["simulated_rows"] + counts["blocked_rows"] == expected
        and report.get("policy_count") == counts["policy_count"]
    ):
        return False

    executed_count_keys = (
        "executed_trades",
        "policy_count",
        "rows_expected",
        "rows_emitted",
        "blocked_rows",
        "entry_invariant_failures",
    )
    executed_counts = {
        key: executed_scope.get(key)
        for key in executed_count_keys
    }
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in executed_counts.values()
    ):
        return False
    executed_expected = (
        executed_counts["executed_trades"]
        * executed_counts["policy_count"]
    )
    if not (
        executed_counts["rows_expected"] == executed_expected
        and executed_counts["rows_emitted"] == executed_expected
        and report.get("policy_count") == executed_counts["policy_count"]
        and executed_contract.get("universe") == "executed_mt5"
        and executed_contract.get("rows_expected") == executed_expected
        and executed_contract.get("rows_emitted") == executed_expected
        and executed_contract.get("complete")
        is validation.get("executed_contract_complete")
    ):
        return False

    expected_status = (
        "archived"
        if validation_mode == "verified_simulation"
        else "diagnostic_archived"
    )
    if provenance.get("status") != expected_status:
        return False
    run_fingerprint = str(provenance.get("run_fingerprint") or "")
    result_fingerprint = str(provenance.get("result_fingerprint") or "")
    if any(
        len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        for value in (run_fingerprint, result_fingerprint)
    ):
        return False
    card_ref = provenance.get("run_card")
    if not isinstance(card_ref, str) or not card_ref:
        return False
    card_path = (REPO_DIR / card_ref).resolve()
    try:
        card_path.relative_to(REPO_DIR.resolve())
    except ValueError:
        return False
    try:
        card = json.loads(card_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        isinstance(card, dict)
        and card.get("run_fingerprint") == run_fingerprint
        and card.get("result_fingerprint") == result_fingerprint
    )


def _strategy_farm_command() -> list[str]:
    raw_latencies = [
        item.strip()
        for item in STRATEGY_FARM_LATENCY_MS.split(",")
        if item.strip()
    ]
    try:
        latencies = [int(item) for item in raw_latencies]
        volume = float(STRATEGY_FARM_VOLUME_PER_LEG)
    except ValueError as exc:
        raise ValueError("invalid strategy farm execution scenarios") from exc
    if (
        not latencies
        or any(value < 0 for value in latencies)
        or len(set(latencies)) != len(latencies)
        or not math.isfinite(volume)
        or volume <= 0
    ):
        raise ValueError("invalid strategy farm execution scenarios")

    command = [sys.executable, "strategy_farm.py"]
    from_date = _simulation_from_date()
    if from_date:
        command.extend(["--from", from_date])
    for latency_ms in latencies:
        command.extend(["--provider-latency-ms", str(latency_ms)])
    command.extend([
        "--provider-volume-per-leg",
        str(volume),
        "--quiet",
        "--progress",
    ])
    command.extend([
        "--money-contract",
        str(RUNTIME_DATA_DIR / "broker_money_contract.json"),
        "--money-tick-cache-dir",
        str(RUNTIME_DATA_DIR / "money_ticks_cache"),
    ])
    return command


def _regenerate_strategy_farm() -> bool:
    """Run offline strategy diagnostics; never changes live decisions."""
    STRATEGY_FARM_FILE.unlink(missing_ok=True)
    try:
        command = _strategy_farm_command()
        rec = subprocess.run(
            command,
            cwd=REPO_DIR, capture_output=False, text=True, timeout=300,
        )
        if rec.returncode == 0 and STRATEGY_FARM_FILE.exists():
            if _strategy_farm_publication_valid(STRATEGY_FARM_FILE):
                print("[Watch] strategy_farm regenerada.", flush=True)
                return True
            STRATEGY_FARM_FILE.unlink(missing_ok=True)
            print(
                "[Watch] strategy_farm rechazada: cobertura o provenance "
                "incompleta.",
                flush=True,
            )
            return False
        print(f"[Watch] strategy_farm.py fallo (rc={rec.returncode}): "
              f"{(rec.stderr or rec.stdout or '')[:1000]}", flush=True)
        return False
    except BaseException as e:
        print(f"[Watch] error ejecutando strategy_farm.py: {e}", flush=True)
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        return False


def _regenerate_recursive_learning_outputs(
    dependencies: dict[str, bool],
) -> bool:
    """Publish whole-corpus reliability evidence after causal builders.

    A diagnostic-only result is still a successful build: its purpose is to
    name the exact hard gates that remain blocked. Stale outputs are removed
    before every attempt so a failed run can never masquerade as current.
    """
    LOG_LEARNING_REPORT_FILE.unlink(missing_ok=True)
    LOG_PATTERN_REGISTRY_FILE.unlink(missing_ok=True)
    LOG_LEARNING_STATUS_FILE.unlink(missing_ok=True)
    attempted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    returncode = None
    error = None
    status = None
    try:
        rec = subprocess.run(
            [sys.executable, "recursive_log_learning.py", "--quiet"],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=120,
        )
        returncode = rec.returncode
        if (
            rec.returncode == 0
            and LOG_LEARNING_REPORT_FILE.exists()
            and LOG_PATTERN_REGISTRY_FILE.exists()
        ):
            print("[Watch] aprendizaje recursivo actualizado.", flush=True)
        else:
            error = rec.stderr or rec.stdout or "learning artifacts missing"
            LOG_LEARNING_REPORT_FILE.unlink(missing_ok=True)
            LOG_PATTERN_REGISTRY_FILE.unlink(missing_ok=True)
            print(f"[Watch] recursive_log_learning.py fallo (rc={rec.returncode}): "
                  f"{error[:1000]}", flush=True)
    except BaseException as e:
        error = f"{type(e).__name__}: {e}"
        LOG_LEARNING_REPORT_FILE.unlink(missing_ok=True)
        LOG_PATTERN_REGISTRY_FILE.unlink(missing_ok=True)
        print(f"[Watch] error ejecutando recursive_log_learning.py: {e}",
              flush=True)
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
    finally:
        try:
            status = learning_publication.publish_status(
                status_path=LOG_LEARNING_STATUS_FILE,
                report_path=LOG_LEARNING_REPORT_FILE,
                registry_path=LOG_PATTERN_REGISTRY_FILE,
                repo_root=REPO_DIR,
                dependencies=dependencies,
                build_returncode=returncode,
                attempted_at_utc=attempted_at,
                error=error,
            )
        except Exception as publication_error:
            print(
                f"[Watch] no pude publicar log_learning_status.json: "
                f"{publication_error}",
                flush=True,
            )
    if not status:
        return False
    if status["ok"]:
        print("[Watch] estado de aprendizaje vigente.", flush=True)
    else:
        blockers = ", ".join(status["blockers"]) or "unknown"
        print(f"[Watch] aprendizaje no vigente: {blockers}", flush=True)
    return bool(status["ok"])


def _regenerate_broker_money_contract() -> bool:
    """Capture current MT5 money metadata; stale contracts are unsafe."""
    BROKER_MONEY_CONTRACT_FILE.unlink(missing_ok=True)
    try:
        rec = subprocess.run(
            [
                sys.executable,
                "tools/capture_broker_money_contract.py",
                "--output",
                str(BROKER_MONEY_CONTRACT_FILE),
                "--quiet",
            ],
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if rec.returncode == 0 and BROKER_MONEY_CONTRACT_FILE.exists():
            print("[Watch] broker_money_contract verificado.", flush=True)
            return True
        print(
            f"[Watch] contrato monetario no verificado (rc={rec.returncode}): "
            f"{(rec.stderr or rec.stdout or '')[:500]}",
            flush=True,
        )
        return False
    except BaseException as exc:
        print(f"[Watch] error capturando contrato monetario: {exc}", flush=True)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return False


def _regenerate_money_tick_cache_status() -> bool:
    """Build EURUSD conversion ticks using XAUUSD time-contract evidence."""
    MONEY_TICK_CACHE_STATUS_FILE.unlink(missing_ok=True)
    try:
        command = [
            sys.executable,
            "tools/ensure_money_tick_cache.py",
            "--cache-dir",
            str(MONEY_TICK_CACHE_DIR),
            "--reference-cache-dir",
            str(RUNTIME_DATA_DIR / "ticks_cache"),
        ]
        from_date = _simulation_from_date()
        if from_date:
            command.extend(["--since", from_date])
        command.append("--quiet")
        rec = subprocess.run(
            command,
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
            timeout=900,
        )
        if rec.returncode == 0 and MONEY_TICK_CACHE_STATUS_FILE.exists():
            print("[Watch] money_ticks_cache verificado/regenerado.", flush=True)
            return True
        print(
            f"[Watch] money_ticks_cache no verificado (rc={rec.returncode}): "
            f"{(rec.stderr or rec.stdout or '')[:700]}",
            flush=True,
        )
        return False
    except BaseException as exc:
        print(f"[Watch] error generando money_ticks_cache: {exc}", flush=True)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return False


def _provider_result_scorecard_publication_valid(path: Path) -> bool:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(report, dict):
        return False
    summary = report.get("summary")
    summaries = report.get("summaries")
    if (
        report.get("schema_version") != 1
        or report.get("channel") != "canal2"
        or not isinstance(summary, dict)
        or not isinstance(summaries, list)
    ):
        return False
    try:
        records = int(summary["records"])
        ready = int(summary["calibration_ready"])
        blocked = int(summary["blocked"])
    except (KeyError, TypeError, ValueError):
        return False
    return bool(
        min(records, ready, blocked) >= 0
        and records == len(summaries)
        and ready + blocked == records
    )


def _regenerate_provider_result_scorecard() -> bool:
    """Structure provider claims without treating them as verified P&L."""
    PROVIDER_RESULT_SCORECARD_FILE.unlink(missing_ok=True)
    try:
        rec = subprocess.run(
            [
                sys.executable,
                "tools/build_provider_result_scorecard.py",
                "--catalog", str(PROVIDER_SIGNAL_CATALOG_FILE),
                "--output", str(PROVIDER_RESULT_SCORECARD_FILE),
                "--quiet",
            ],
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if (
            rec.returncode == 0
            and _provider_result_scorecard_publication_valid(
                PROVIDER_RESULT_SCORECARD_FILE
            )
        ):
            print("[Watch] resultados publicados estructurados.", flush=True)
            return True
        PROVIDER_RESULT_SCORECARD_FILE.unlink(missing_ok=True)
        print(
            f"[Watch] marcador del proveedor no publicado (rc={rec.returncode}): "
            f"{(rec.stderr or rec.stdout or '')[:1000]}",
            flush=True,
        )
        return False
    except BaseException as exc:
        PROVIDER_RESULT_SCORECARD_FILE.unlink(missing_ok=True)
        print(f"[Watch] error en marcador del proveedor: {exc}", flush=True)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return False


def _strategy_shadow_until_date(now: datetime | None = None) -> str:
    """Return the last UTC day whose complete price path can be certified."""
    observed = now or datetime.now(timezone.utc)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("strategy shadow cutoff must be timezone-aware")
    return (
        observed.astimezone(timezone.utc).date() - timedelta(days=1)
    ).isoformat()


def _regenerate_strategy_shadow_tick_cache_status(
    *,
    since_value: str,
    until_value: str,
) -> bool:
    """Verify only the tick window consumed by the shadow comparison."""
    STRATEGY_SHADOW_TICK_CACHE_STATUS_FILE.unlink(missing_ok=True)
    try:
        since = datetime.strptime(since_value, "%Y-%m-%d").date()
        until = datetime.strptime(until_value, "%Y-%m-%d").date()
        if until < since:
            raise ValueError("strategy shadow tick window ends before it starts")
        if not PROVIDER_SIGNAL_CATALOG_FILE.is_file():
            raise FileNotFoundError("provider signal catalog is missing")
        command = [
            sys.executable,
            "tools/ensure_replay_tick_cache.py",
            "--ensure",
            "--input", str(RUNTIME_DATA_DIR / "replay_trades.jsonl"),
            "--cache-dir", str(RUNTIME_DATA_DIR / "ticks_cache"),
            "--status", str(STRATEGY_SHADOW_TICK_CACHE_STATUS_FILE),
            "--since", since.isoformat(),
            "--until", until.isoformat(),
            "--catalog", str(PROVIDER_SIGNAL_CATALOG_FILE),
            "--provider-since", since.isoformat(),
            "--provider-until", until.isoformat(),
            "--quiet",
        ]
        rec = subprocess.run(
            command,
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
            timeout=900,
        )
        try:
            status = json.loads(
                STRATEGY_SHADOW_TICK_CACHE_STATUS_FILE.read_text(
                    encoding="utf-8",
                )
            )
        except (OSError, UnicodeError, json.JSONDecodeError):
            status = {}
        if rec.returncode == 0 and status.get("ok") is True:
            print("[Watch] ticks de comparativa en sombra verificados.",
                  flush=True)
            return True
        print(
            "[Watch] ticks de comparativa en sombra no verificados "
            f"(rc={rec.returncode}): "
            f"{(rec.stderr or rec.stdout or '')[:1000]}",
            flush=True,
        )
        return False
    except BaseException as exc:
        print(
            f"[Watch] error verificando ticks de comparativa en sombra: {exc}",
            flush=True,
        )
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return False


def _strategy_shadow_publication_valid(
    path: Path,
    *,
    expected_since: str | None = None,
    expected_until: str | None = None,
) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    settlement_hash = payload.get("settlement_hash")
    evidence = {
        key: value
        for key, value in payload.items()
        if key != "settlement_hash"
    }
    if not isinstance(settlement_hash, str):
        return False
    try:
        if settlement_hash != canonical_hash(evidence):
            return False
    except (TypeError, ValueError):
        return False

    candidate_rows = payload.get("candidate_rows")
    actual_rows = payload.get("actual_rows")
    tick_evidence = payload.get("tick_evidence")
    report = payload.get("report")
    if (
        payload.get("schema_version") != 1
        or (
            expected_since is not None
            and payload.get("since") != expected_since
        )
        or (
            expected_until is not None
            and payload.get("until") != expected_until
        )
        or not isinstance(candidate_rows, list)
        or not isinstance(actual_rows, list)
        or not isinstance(tick_evidence, dict)
        or not isinstance(report, dict)
        or not isinstance(report.get("comparison_allowed"), bool)
    ):
        return False
    matrix = report.get("matrix")
    if not isinstance(matrix, dict) or set(matrix) != {"canal1", "canal2"}:
        return False

    observed_by_channel = {"canal1": 0, "canal2": 0}
    for row in candidate_rows:
        if not isinstance(row, dict):
            return False
        channel = str(row.get("channel") or "")
        if channel not in observed_by_channel:
            return False
        observed_by_channel[channel] += 1
    for channel, observed in observed_by_channel.items():
        values = matrix.get(channel)
        if not isinstance(values, dict):
            return False
        try:
            eligible = int(values["eligible_signals"])
            expected = int(values["expected_rows"])
            reported_observed = int(values["observed_rows"])
            settled = int(values["settled_rows"])
            blocked = int(values["blocked_rows"])
            open_rows = int(values["open_rows"])
        except (KeyError, TypeError, ValueError):
            return False
        if (
            min(eligible, expected, reported_observed, settled, blocked, open_rows)
            < 0
            or expected != eligible * 3
            or reported_observed != observed
            or settled + blocked + open_rows != expected
            or values.get("complete") is not (
                observed == expected and settled == expected
            )
        ):
            return False
    if (
        not any(
            int(matrix[channel]["eligible_signals"])
            for channel in ("canal1", "canal2")
        )
        and report.get("comparison_allowed") is not False
    ):
        return False
    return True


def _regenerate_strategy_shadow_report() -> bool:
    """Settle the frozen three-candidate matrix without touching live orders."""
    STRATEGY_SHADOW_REPORT_FILE.unlink(missing_ok=True)
    try:
        since = datetime.strptime(
            STRATEGY_SHADOW_FROM_DATE, "%Y-%m-%d",
        ).date()
        until_value = _strategy_shadow_until_date()
        until = datetime.strptime(until_value, "%Y-%m-%d").date()
        if until < since:
            raise ValueError("strategy shadow window ends before it starts")
        if not _regenerate_strategy_shadow_tick_cache_status(
            since_value=since.isoformat(),
            until_value=until.isoformat(),
        ):
            STRATEGY_SHADOW_REPORT_FILE.unlink(missing_ok=True)
            return False
        command = [
            sys.executable,
            "tools/build_strategy_shadow_report.py",
            "--since", since.isoformat(),
            "--until", until.isoformat(),
            "--events", str(RUNTIME_DATA_DIR / "trade_events.jsonl"),
            "--ledger", str(RUNTIME_DATA_DIR / "ledger.jsonl"),
            "--ticks-cache", str(RUNTIME_DATA_DIR / "ticks_cache"),
            "--money-ticks-cache", str(MONEY_TICK_CACHE_DIR),
            "--money-contract", str(BROKER_MONEY_CONTRACT_FILE),
            "--provider-catalog", str(PROVIDER_SIGNAL_CATALOG_FILE),
            "--output", str(STRATEGY_SHADOW_REPORT_FILE),
        ]
        rec = subprocess.run(
            command,
            cwd=REPO_DIR,
            capture_output=False,
            text=True,
            timeout=900,
        )
        if (
            rec.returncode == 0
            and STRATEGY_SHADOW_REPORT_FILE.is_file()
            and _strategy_shadow_publication_valid(
                STRATEGY_SHADOW_REPORT_FILE,
                expected_since=since.isoformat(),
                expected_until=until.isoformat(),
            )
        ):
            print("[Watch] comparativa en sombra verificada.", flush=True)
            return True
        STRATEGY_SHADOW_REPORT_FILE.unlink(missing_ok=True)
        print(
            f"[Watch] comparativa en sombra no publicada (rc={rec.returncode}).",
            flush=True,
        )
        return False
    except BaseException as exc:
        STRATEGY_SHADOW_REPORT_FILE.unlink(missing_ok=True)
        print(f"[Watch] error en comparativa en sombra: {exc}", flush=True)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return False


def _mutable_offline_output_paths() -> tuple[Path, ...]:
    return (
        PROVIDER_SIGNAL_CATALOG_FILE,
        PROVIDER_RESULT_SCORECARD_FILE,
        STRATEGY_FARM_FILE,
        STRATEGY_SHADOW_REPORT_FILE,
        STRATEGY_SHADOW_TICK_CACHE_STATUS_FILE,
        LOG_LEARNING_REPORT_FILE,
        LOG_PATTERN_REGISTRY_FILE,
        LOG_LEARNING_STATUS_FILE,
    )


@contextmanager
def _offline_output_transaction():
    paths = _mutable_offline_output_paths()
    snapshots = {
        path: path.read_bytes()
        for path in paths
        if path.is_file()
    }
    try:
        yield
    except BaseException:
        for path in paths:
            if path in snapshots:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(snapshots[path])
            else:
                path.unlink(missing_ok=True)
        raise


def _regenerate_session_outputs(
    *,
    progress_reporter: pipeline_progress.ProgressReporter | None = None,
) -> dict[str, bool]:
    _clear_mutable_offline_outputs()
    reporter = progress_reporter or pipeline_progress.ProgressReporter(
        min_interval_s=0.0,
        width=24,
    )
    stage_total = 13
    stage_current = 0
    builder_results = {
        "accounting": False,
        "ledger": False,
        "observed_ticks": False,
        "provider_catalog": False,
        "provider_scorecard": False,
        "readiness": False,
        "replay": False,
        "strategy_farm": False,
        "strategy_shadow": False,
        "tick_cache": False,
        "money_contract": False,
        "money_ticks": False,
    }

    def run_stage(
        key: str | None,
        label: str,
        builder,
        *,
        enabled: bool = True,
    ) -> bool:
        nonlocal stage_current
        if enabled:
            reporter.update(
                stage_current,
                stage_total,
                f"{label}: ejecutando",
                force=True,
            )
            result = bool(builder())
            status = "OK" if result else "FALLO"
        else:
            result = False
            status = "OMITIDA por dependencia"
        stage_current += 1
        reporter.update(
            stage_current,
            stage_total,
            f"{label} {status}",
            force=True,
        )
        if key is not None:
            builder_results[key] = result
        return result

    run_stage("ledger", "Ledger", _regenerate_ledger)
    run_stage(
        "replay",
        "Replay",
        _regenerate_replay_trades,
        enabled=builder_results["ledger"],
    )
    run_stage(
        "accounting",
        "Auditoria contable",
        _regenerate_accounting_replay_audit,
        enabled=builder_results["replay"],
    )
    accounting_ok = builder_results["accounting"]
    run_stage(
        "provider_catalog",
        "Catalogo de senales",
        _regenerate_provider_signal_catalog,
    )
    run_stage(
        "provider_scorecard",
        "Resultados publicados",
        _regenerate_provider_result_scorecard,
        enabled=builder_results["provider_catalog"],
    )
    run_stage(
        "tick_cache",
        "Ticks XAUUSD",
        _regenerate_replay_tick_cache_status,
        enabled=accounting_ok,
    )
    run_stage(
        "money_contract",
        "Contrato monetario",
        _regenerate_broker_money_contract,
        enabled=accounting_ok,
    )
    run_stage(
        "money_ticks",
        "Ticks de conversion",
        _regenerate_money_tick_cache_status,
        enabled=accounting_ok,
    )
    run_stage(
        "strategy_shadow",
        "Comparativa en sombra",
        _regenerate_strategy_shadow_report,
        enabled=(
            builder_results["ledger"]
            and builder_results["provider_catalog"]
            and builder_results["money_contract"]
            and builder_results["money_ticks"]
        ),
    )
    run_stage(
        "observed_ticks",
        "Replay tick a tick",
        _regenerate_observed_tick_replay_audit,
        enabled=accounting_ok,
    )
    run_stage(
        "readiness",
        "Preparacion de replay",
        _regenerate_replay_readiness_report,
        enabled=accounting_ok,
    )
    run_stage(
        "strategy_farm",
        "Granja de estrategias",
        _regenerate_strategy_farm,
        enabled=(
            builder_results["observed_ticks"]
            and builder_results["provider_catalog"]
        ),
    )
    run_stage(
        None,
        "Aprendizaje recursivo",
        lambda: _regenerate_recursive_learning_outputs(builder_results),
    )
    return builder_results


def _push_session_data() -> git_sync.SyncResult:
    """Build offline reports and publish through isolated telemetry only."""
    with _offline_output_transaction():
        _regenerate_session_outputs()
    checkpoint = runtime_telemetry.checkpoint_runtime(
        RUNTIME_DATA_DIR,
        code_commit=_local_head() or None,
    )
    if not checkpoint.ok:
        return git_sync.SyncResult(
            ok=False,
            action="telemetry_checkpoint_failed",
            branch=_current_branch(),
            local_head=_local_head() or None,
            remote_head=_remote_head() or None,
            error="; ".join(checkpoint.errors),
        )
    published = runtime_telemetry.publish_outbox(REPO_DIR, RUNTIME_DATA_DIR)
    if published.ok:
        print(
            f"[Watch] Telemetria publicada desde checkout aislado: "
            f"{published.published_files} archivos.",
            flush=True,
        )
    else:
        print(
            f"[Watch] Telemetria preservada localmente; subida pendiente: "
            f"{published.error}",
            flush=True,
        )
    return git_sync.SyncResult(
        ok=bool(published.ok),
        action=(
            "telemetry_published"
            if published.ok
            else "telemetry_publish_failed"
        ),
        branch=_current_branch(),
        local_head=_local_head() or None,
        remote_head=_remote_head() or None,
        error=published.error,
    )


def _run_main() -> int:
    if not MAIN_PY.exists():
        print(f"[Watch] No encuentro {MAIN_PY}", flush=True)
        return 1

    sync = _prepare_repository_for_runtime()
    if not sync.ok:
        sync = _offline_runtime_fallback(sync)
    _print_sync_result(sync)
    if not sync.ok:
        print("[Watch] Arranque bloqueado: Git no está verificado.", flush=True)
        return _sync_failure_exit_code(sync)
    last_local = str(sync.local_head)
    last_remote = str(sync.remote_head)
    log_health = runtime_log_health.inspect_runtime_log(
        BOT_RUNTIME_LOG_FILE,
        warn_bytes=BOT_RUNTIME_LOG_WARN_BYTES,
    )
    if log_health["exists"]:
        size_mib = log_health["size_bytes"] / (1024 * 1024)
        suffix = (
            " AVISO: supera el umbral configurado."
            if log_health["warning"]
            else ""
        )
        print(
            f"[Watch] Log consola append-only: {size_mib:.1f} MiB; "
            f"sin rotacion automatica.{suffix}",
            flush=True,
        )
    proc = _spawn_bot_with_active_channels()
    if proc is None:
        return WATCHER_GIT_BLOCKED_EXIT_CODE
    bot_started_at = time.time()
    last_check = time.time()
    # El primer intento se lanza ya con el bot activo; los siguientes respetan
    # el intervalo. Siempre ocurre en otro proceso y nunca bloquea Telegram/MT5.
    last_telemetry_publish = 0.0
    last_supervisor_tick = time.time()
    pending_code_remote = None

    try:
        while True:
            supervisor_tick = time.time()
            supervisor_gap_s = supervisor_tick - last_supervisor_tick
            previous_supervisor_tick = last_supervisor_tick
            last_supervisor_tick = supervisor_tick
            if _supervisor_loop_gap_is_stale(
                previous_supervisor_tick,
                supervisor_tick,
                WATCHDOG_SUPERVISOR_GAP_SEC,
            ):
                print(
                    f"[Watch] Pausa del sistema detectada "
                    f"({supervisor_gap_s:.1f}s). Reinicio conexiones.",
                    flush=True,
                )
                _stop_bot(proc)
                session_sync = _checkpoint_runtime_data()
                if not session_sync.ok:
                    return _sync_failure_exit_code(session_sync)
                last_local = str(session_sync.local_head)
                last_remote = str(session_sync.remote_head)
                time.sleep(RELAUNCH_DELAY_SEC)
                proc = _spawn_bot_with_active_channels()
                if proc is None:
                    return WATCHER_GIT_BLOCKED_EXIT_CODE
                bot_started_at = time.time()
                last_check = bot_started_at
                last_supervisor_tick = bot_started_at
                continue

            # Si el bot murió inesperadamente, relanzar
            if proc.poll() is not None:
                print(f"[Watch] Bot terminó con código {proc.returncode}. "
                      f"Relanzo en {RELAUNCH_DELAY_SEC}s.", flush=True)
                session_sync = _checkpoint_runtime_data()
                if not session_sync.ok:
                    return _sync_failure_exit_code(session_sync)
                last_local = str(session_sync.local_head)
                last_remote = str(session_sync.remote_head)
                time.sleep(RELAUNCH_DELAY_SEC)
                proc = _spawn_bot_with_active_channels()
                if proc is None:
                    return WATCHER_GIT_BLOCKED_EXIT_CODE
                bot_started_at = time.time()
                continue

            now = time.time()
            if (
                TELEMETRY_PUBLISH_SEC > 0
                and now - last_telemetry_publish >= TELEMETRY_PUBLISH_SEC
            ):
                last_telemetry_publish = now
                _trigger_telemetry_publication()

            heartbeat_age_s = _runtime_heartbeat_age_s(now=now)
            uptime_s = now - bot_started_at
            if _runtime_heartbeat_is_stale(
                    heartbeat_age_s, uptime_s, WATCHDOG_HEARTBEAT_TIMEOUT_SEC):
                if heartbeat_age_s is None:
                    detail = "no hay heartbeat runtime"
                else:
                    detail = f"heartbeat viejo ({heartbeat_age_s:.1f}s)"
                print(f"[Watch] Bot congelado: {detail}. Reinicio.", flush=True)
                _stop_bot(proc)
                session_sync = _checkpoint_runtime_data()
                if not session_sync.ok:
                    return _sync_failure_exit_code(session_sync)
                last_local = str(session_sync.local_head)
                last_remote = str(session_sync.remote_head)
                time.sleep(RELAUNCH_DELAY_SEC)
                proc = _spawn_bot_with_active_channels()
                if proc is None:
                    return WATCHER_GIT_BLOCKED_EXIT_CODE
                bot_started_at = time.time()
                last_check = bot_started_at
                continue

            # Cada POLL_SEC comprobar commits nuevos
            if now - last_check >= POLL_SEC:
                last_check = now
                fetched = _git("fetch", "origin", "main")
                if fetched.returncode != 0:
                    print(
                        "[Watch] Git remoto no disponible; el bot sigue "
                        f"activo y se reintentara: "
                        f"{(fetched.stderr or fetched.stdout or '').strip()[:300]}",
                        flush=True,
                    )
                    continue
                remote = _remote_head()
                if remote != last_remote:
                    if _remote_update_is_data_only(last_remote, remote):
                        print(f"[Watch] Solo commits de datos: "
                              f"{last_remote[:8]} -> {remote[:8]}. "
                              f"Sin reinicio.", flush=True)
                        last_remote = remote
                        pending_code_remote = None
                        _clear_runtime_update_pending()
                        continue
                    deferred, exposure = _defer_code_update_if_exposed(
                        last_local,
                        remote,
                        now=now,
                    )
                    if deferred:
                        if pending_code_remote != remote:
                            state_label = exposure["exposure_state"]
                            detail = exposure.get("reason") or "sin detalle"
                            print(
                                "[Watch] Codigo nuevo pendiente: el bot sigue "
                                f"activo (exposicion={state_label}, "
                                f"detalle={detail}). Se activara cuando MT5 "
                                "y el estado interno confirmen cero "
                                "posiciones.",
                                flush=True,
                            )
                        pending_code_remote = remote
                        continue
                    watcher_self_update = _paths_changed_between(
                        last_remote, remote, WATCHER_SELF_UPDATE_PATHS)
                    authorized, final_exposure = _quiesce_code_update(proc)
                    if not authorized:
                        _record_runtime_update_pending(
                            last_local,
                            remote,
                            final_exposure,
                        )
                        if pending_code_remote != remote:
                            print(
                                "[Watch] Codigo nuevo aplazado tras pausar "
                                "entradas: no se pudo confirmar una cuenta "
                                "plana sin carreras "
                                f"(exposicion="
                                f"{final_exposure['exposure_state']}, "
                                f"detalle={final_exposure.get('reason')}).",
                                flush=True,
                            )
                        pending_code_remote = remote
                        continue
                    pending_code_remote = None
                    print(f"[Watch] Commit nuevo detectado: {last_remote[:8]} -> "
                          f"{remote[:8]}. Reinicio.", flush=True)
                    _stop_bot(proc)
                    session_sync = _checkpoint_runtime_data()
                    if not session_sync.ok:
                        return _sync_failure_exit_code(session_sync)
                    session_sync = _prepare_repository_for_runtime()
                    _print_sync_result(session_sync)
                    if not session_sync.ok:
                        fallback = _previous_verified_runtime_fallback(
                            session_sync,
                            last_local,
                        )
                        if not fallback.ok:
                            return _sync_failure_exit_code(session_sync)
                        _print_sync_result(fallback)
                        proc = _spawn_bot_with_active_channels(
                            verified_head=last_local,
                        )
                        if proc is None:
                            return WATCHER_GIT_BLOCKED_EXIT_CODE
                        bot_started_at = time.time()
                        last_check = bot_started_at
                        continue
                    last_local = str(session_sync.local_head)
                    last_remote = str(session_sync.remote_head)
                    _clear_runtime_update_pending()
                    if watcher_self_update:
                        print("[Watch] Watcher actualizado. Saliendo para "
                              "que run_bot.bat lo relance.", flush=True)
                        return WATCHER_RELOAD_EXIT_CODE
                    proc = _spawn_bot_with_active_channels()
                    if proc is None:
                        return WATCHER_GIT_BLOCKED_EXIT_CODE
                    bot_started_at = time.time()

            time.sleep(2)
    except KeyboardInterrupt:
        print("\n[Watch] Ctrl+C — cerrando bot.", flush=True)
        _stop_bot(proc)
        session_sync = _checkpoint_runtime_data()
        if not session_sync.ok:
            return _sync_failure_exit_code(session_sync)
        return 0
    finally:
        try:
            child_running = proc.poll() is None
        except BaseException:
            child_running = True
        if child_running:
            print(
                "[Watch] Supervisor interrumpido; cerrando el bot antes de "
                "la recuperacion.",
                flush=True,
            )
            try:
                _stop_bot(proc)
            except Exception as exc:
                print(
                    f"[Watch] No pude confirmar el cierre del bot: {exc}",
                    flush=True,
                )


def main() -> int:
    with _runtime_environment():
        return _run_main()


def _run_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Signal Copier VM watcher")
    parser.add_argument("--final-backup", action="store_true")
    parser.add_argument("--recovery-checkpoint", action="store_true")
    args = parser.parse_args(argv)
    guard = WatcherInstanceGuard()
    if not guard.acquire():
        print(
            "[Watch] Ya existe otro supervisor activo; esta instancia no hara cambios.",
            flush=True,
        )
        return WATCHER_DUPLICATE_EXIT_CODE
    try:
        if args.recovery_checkpoint:
            result = _checkpoint_runtime_data()
            if not result.ok:
                return _sync_failure_exit_code(result)
            return 0
        if args.final_backup:
            result = _push_session_data()
            if not result.ok:
                return _sync_failure_exit_code(result)
            return 0
        return main()
    finally:
        guard.release()


def cli(argv: list[str] | None = None) -> int:
    with _runtime_environment():
        return _run_cli(argv)


if __name__ == "__main__":
    sys.exit(cli())
