"""
run_bot_watch.py - supervisor de produccion con sincronizacion Git segura.

Uso:
    python tools/run_bot_watch.py

Funcionamiento:
  1. Normaliza Git antes de arrancar y verifica main == origin/main.
  2. Solo lanza main.py cuando no hay rebase pendiente ni HEAD separado.
  3. Cada 60 s consulta origin/main; publica commits data: locales o activa
     la nueva version remota mediante una ruta determinista y comprobada.
  4. Antes de reiniciar o cerrar, regenera los artefactos de sesion dentro
     de una transaccion y publica los datos por esa misma ruta Git.
  5. Si no puede garantizar el estado, preserva un rescate, no lanza el bot
     y devuelve el codigo 76 para dejar el problema visible.

Detener el wrapper: Ctrl+C (cierra el bot tambien).
"""

import argparse
import os
import json
import math
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
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

import log_learning_publication as learning_publication
import pipeline_progress
from tools import git_sync

MAIN_PY  = REPO_DIR / "main.py"
RECONCILE_STATUS_FILE = REPO_DIR / "data" / "reconcile_status.json"
REPLAY_STATUS_FILE = REPO_DIR / "data" / "replay_status.json"
ACCOUNTING_REPLAY_AUDIT_STATUS_FILE = REPO_DIR / "data" / "accounting_replay_audit_status.json"
REPLAY_TICK_CACHE_STATUS_FILE = REPO_DIR / "data" / "replay_tick_cache_status.json"
BROKER_MONEY_CONTRACT_FILE = REPO_DIR / "data" / "broker_money_contract.json"
MONEY_TICK_CACHE_STATUS_FILE = REPO_DIR / "data" / "money_tick_cache_status.json"
MONEY_TICK_CACHE_DIR = REPO_DIR / "data" / "money_ticks_cache"
REPLAY_READINESS_REPORT_FILE = REPO_DIR / "data" / "replay_readiness_report.json"
OBSERVED_TICK_REPLAY_AUDIT_FILE = REPO_DIR / "data" / "observed_tick_replay_audit.jsonl"
OBSERVED_TICK_REPLAY_STATUS_FILE = REPO_DIR / "data" / "observed_tick_replay_status.json"
PROVIDER_SIGNAL_CATALOG_FILE = REPO_DIR / "data" / "provider_signal_catalog.json"
STRATEGY_FARM_FILE = REPO_DIR / "data" / "strategy_farm.json"
LOG_LEARNING_REPORT_FILE = REPO_DIR / "data" / "log_learning_report.json"
LOG_PATTERN_REGISTRY_FILE = REPO_DIR / "data" / "log_pattern_registry.json"
LOG_LEARNING_STATUS_FILE = REPO_DIR / "data" / "log_learning_status.json"
LOG_PATTERN_REVIEWS_FILE = REPO_DIR / "data" / "log_pattern_reviews.json"
STRATEGY_FARM_FROM_DATE = os.getenv("STRATEGY_FARM_FROM_DATE", "2026-07-06")
SIMULATION_FROM_DATE = os.getenv("SIMULATION_FROM_DATE")
STRATEGY_FARM_LATENCY_MS = os.getenv("STRATEGY_FARM_LATENCY_MS", "0")
STRATEGY_FARM_VOLUME_PER_LEG = os.getenv(
    "STRATEGY_FARM_VOLUME_PER_LEG", "0.01")
RUNTIME_HEARTBEAT_FILE = Path(os.getenv(
    "BOT_RUNTIME_HEARTBEAT_FILE",
    str(REPO_DIR / "data" / "runtime_heartbeat.json"),
))
POLL_SEC = 60   # cada cuánto comprobar commits nuevos
RESTART_GRACE_SEC = 10  # tiempo para SIGTERM antes de SIGKILL
RELAUNCH_DELAY_SEC = 5  # espera entre fin del bot y relanzamiento
WATCHER_RELOAD_EXIT_CODE = 75
WATCHER_GIT_BLOCKED_EXIT_CODE = 76
WATCHER_GIT_RETRY_EXIT_CODE = 77
RETRYABLE_GIT_ACTIONS = {
    "fetch_failed",
    "post_push_fetch_failed",
    "push_failed",
}
WATCHER_SELF_UPDATE_PATHS = {"tools/run_bot_watch.py", "run_bot.bat"}
WATCHDOG_HEARTBEAT_TIMEOUT_SEC = float(os.getenv(
    "WATCHDOG_HEARTBEAT_TIMEOUT_SEC", "180"))


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
    return subprocess.run(
        ["git", *args], cwd=REPO_DIR, capture_output=capture, text=True, check=False
    )


def _print_git_progress(stage: str) -> None:
    labels = {
        "inspect": "comprobando estado local",
        "fetch": "consultando origin/main",
        "rebase": "integrando datos locales sobre main",
        "push": "subiendo datos de sesion",
        "post_push_fetch": "confirmando la referencia remota",
        "verify": "verificando main limpio y sincronizado",
    }
    print(f"[Watch] Git: {labels.get(stage, stage)}...", flush=True)


def _prepare_repository_for_runtime() -> git_sync.SyncResult:
    return git_sync.synchronize_repository(
        REPO_DIR,
        publish_local=True,
        progress_callback=_print_git_progress,
    )


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


def _refresh_heads_after_session_data_push() -> git_sync.SyncResult:
    """Return one verified repository state after a session-data attempt."""
    result = _prepare_repository_for_runtime()
    _print_sync_result(result)
    return result


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


def _runtime_heartbeat_is_stale(heartbeat_age_s: float | None,
                                process_uptime_s: float,
                                timeout_s: float) -> bool:
    if timeout_s <= 0:
        return False
    if heartbeat_age_s is None:
        return process_uptime_s > timeout_s
    return heartbeat_age_s > timeout_s


def _spawn_bot() -> subprocess.Popen:
    _clear_runtime_heartbeat()
    print(f"[Watch] Lanzando bot: python {MAIN_PY}", flush=True)
    # Usamos el mismo intérprete que ejecuta este script.
    # creationflags en Windows para poder mandar Ctrl-Break al subproceso.
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen([sys.executable, str(MAIN_PY)], cwd=REPO_DIR, **kwargs)


def _stop_bot(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return  # ya terminó
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
    ledger_file = REPO_DIR / "data" / "ledger.jsonl"
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
    replay_file = REPO_DIR / "data" / "replay_trades.jsonl"
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
        "command": [sys.executable, "build_replay_trades.py", "--quiet"],
    }
    try:
        rec = subprocess.run(
            [sys.executable, "build_replay_trades.py", "--quiet"],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=60,
        )
        status.update({
            "ok": rec.returncode == 0,
            "returncode": rec.returncode,
            "stdout": rec.stdout or "",
            "stderr": rec.stderr or "",
        })
        if rec.returncode == 0:
            print("[Watch] replay_trades regenerado.", flush=True)
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
        _write_replay_status(status)
    return bool(status["ok"])


def _regenerate_accounting_replay_audit() -> bool:
    """Regenera data/accounting_replay_audit.jsonl desde replay_trades.jsonl."""
    started = time.time()
    audit_file = REPO_DIR / "data" / "accounting_replay_audit.jsonl"
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
            "--quiet",
        ]
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
    STRATEGY_FARM_FILE.unlink(missing_ok=True)
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
    validation = report.get("validation")
    provenance = report.get("provenance")
    if not all(isinstance(item, dict) for item in (
        scope,
        validation,
        provenance,
    )):
        return False
    if validation.get("price_path_mode") != "provider_first":
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
        str(REPO_DIR / "data" / "broker_money_contract.json"),
        "--money-tick-cache-dir",
        str(REPO_DIR / "data" / "money_ticks_cache"),
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
            str(REPO_DIR / "data" / "ticks_cache"),
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


def _mutable_offline_output_paths() -> tuple[Path, ...]:
    return (
        PROVIDER_SIGNAL_CATALOG_FILE,
        STRATEGY_FARM_FILE,
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
    stage_total = 11
    stage_current = 0
    builder_results = {
        "accounting": False,
        "ledger": False,
        "observed_ticks": False,
        "provider_catalog": False,
        "readiness": False,
        "replay": False,
        "strategy_farm": False,
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
        "provider_catalog",
        "Catalogo de senales",
        _regenerate_provider_signal_catalog,
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


def _format_byte_size(size_bytes: int) -> str:
    value = float(max(0, size_bytes))
    units = ("B", "KB", "MB", "GB")
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{int(size_bytes)} B"


def _staged_payload_summary() -> tuple[int, int]:
    result = _git("diff", "--cached", "--name-only", "-z")
    if result.returncode != 0:
        return 0, 0
    paths = [path for path in (result.stdout or "").split("\0") if path]
    total_bytes = 0
    root = REPO_DIR.resolve()
    for raw_path in paths:
        path = (REPO_DIR / raw_path).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            continue
        if path.is_file():
            total_bytes += path.stat().st_size
    return len(paths), total_bytes


def _push_session_data() -> git_sync.SyncResult | None:
    """Sube data/trade_events.jsonl + ledger + journal a GitHub si hay cambios.

    Replica el comportamiento del run_bot.bat original (que solo se ejecutaba
    al cerrar el bot). Con el watcher el bot puede vivir días sin parar, así
    que sin esto los logs no se subirían y no podríamos analizar la sesión.
    Llamamos a esta función después de cada parada/reinicio del bot.

    Antes de subir, regenera el ledger reconciliado (reconcile_mt5_ledger.py).
    """
    with _offline_output_transaction():
        _regenerate_session_outputs()
    files = [
        "data/trade_events.jsonl",
        "data/ledger.jsonl",
        "data/reconcile_status.json",
        "data/replay_trades.jsonl",
        "data/replay_status.json",
        "data/accounting_replay_audit.jsonl",
        "data/accounting_replay_audit_status.json",
        "data/replay_tick_cache_status.json",
        "data/broker_money_contract.json",
        "data/money_tick_cache_status.json",
        "data/replay_readiness_report.json",
        "data/observed_tick_replay_audit.jsonl",
        "data/observed_tick_replay_status.json",
        "data/provider_signal_catalog.json",
        "data/strategy_farm.json",
        "data/log_learning_report.json",
        "data/log_pattern_registry.json",
        "data/log_learning_status.json",
        "data/log_pattern_reviews.json",
        "data/simulation_runs",
        "data/trade_events_TEST.jsonl",
        "data/trade_journal.csv",
        "data/trade_journal_TEST.csv",
    ]
    try:
        for f in files:
            _git("add", "-f", f)  # -f por si data/ esta en .gitignore
        # Nada que commitear?
        diff = _git("diff", "--cached", "--quiet")
        if diff.returncode == 0:
            return  # sin cambios

        staged_files, staged_bytes = _staged_payload_summary()
        print(
            "[Watch] Publicacion preparada: "
            f"{staged_files} archivos, {_format_byte_size(staged_bytes)}.",
            flush=True,
        )

        from datetime import datetime
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        msg = _git("commit", "-m", f"data: sesion {ts}")
        if msg.returncode != 0:
            print(f"[Watch] git commit falló: {msg.stderr}", flush=True)
            return
        sync = _prepare_repository_for_runtime()
        _print_sync_result(sync)
        if sync.ok:
            print("[Watch] datos de sesión subidos a GitHub.", flush=True)
        else:
            print(
                f"[Watch] datos preservados pero no publicados: "
                f"{sync.action}: {sync.error or 'estado Git inseguro'}",
                flush=True,
            )
        return sync
    except Exception as e:
        print(f"[Watch] _push_session_data error: {e}", flush=True)


def main() -> int:
    if not MAIN_PY.exists():
        print(f"[Watch] No encuentro {MAIN_PY}", flush=True)
        return 1

    sync = _prepare_repository_for_runtime()
    _print_sync_result(sync)
    if not sync.ok:
        print("[Watch] Arranque bloqueado: Git no está verificado.", flush=True)
        return _sync_failure_exit_code(sync)
    last_local = str(sync.local_head)
    last_remote = str(sync.remote_head)
    proc = _spawn_bot()
    bot_started_at = time.time()
    last_check = time.time()

    try:
        while True:
            # Si el bot murió inesperadamente, relanzar
            if proc.poll() is not None:
                print(f"[Watch] Bot terminó con código {proc.returncode}. "
                      f"Relanzo en {RELAUNCH_DELAY_SEC}s.", flush=True)
                session_sync = (
                    _push_session_data()
                    or _refresh_heads_after_session_data_push()
                )
                if not session_sync.ok:
                    return _sync_failure_exit_code(session_sync)
                last_local = str(session_sync.local_head)
                last_remote = str(session_sync.remote_head)
                time.sleep(RELAUNCH_DELAY_SEC)
                proc = _spawn_bot()
                bot_started_at = time.time()
                continue

            now = time.time()
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
                session_sync = (
                    _push_session_data()
                    or _refresh_heads_after_session_data_push()
                )
                if not session_sync.ok:
                    return _sync_failure_exit_code(session_sync)
                last_local = str(session_sync.local_head)
                last_remote = str(session_sync.remote_head)
                time.sleep(RELAUNCH_DELAY_SEC)
                proc = _spawn_bot()
                bot_started_at = time.time()
                last_check = bot_started_at
                continue

            # Cada POLL_SEC comprobar commits nuevos
            if now - last_check >= POLL_SEC:
                last_check = now
                _git("fetch", "origin", "main")
                remote = _remote_head()
                if remote != last_remote:
                    if _remote_update_is_data_only(last_remote, remote):
                        print(f"[Watch] Solo commits de datos: "
                              f"{last_remote[:8]} -> {remote[:8]}. "
                              f"Sin reinicio.", flush=True)
                        last_remote = remote
                        continue
                    watcher_self_update = _paths_changed_between(
                        last_remote, remote, WATCHER_SELF_UPDATE_PATHS)
                    print(f"[Watch] Commit nuevo detectado: {last_remote[:8]} -> "
                          f"{remote[:8]}. Reinicio.", flush=True)
                    _stop_bot(proc)
                    session_sync = (
                        _push_session_data()
                        or _refresh_heads_after_session_data_push()
                    )
                    if not session_sync.ok:
                        return _sync_failure_exit_code(session_sync)
                    last_local = str(session_sync.local_head)
                    last_remote = str(session_sync.remote_head)
                    if watcher_self_update:
                        print("[Watch] Watcher actualizado. Saliendo para "
                              "que run_bot.bat lo relance.", flush=True)
                        return WATCHER_RELOAD_EXIT_CODE
                    proc = _spawn_bot()
                    bot_started_at = time.time()

            time.sleep(2)
    except KeyboardInterrupt:
        print("\n[Watch] Ctrl+C — cerrando bot.", flush=True)
        _stop_bot(proc)
        session_sync = (
            _push_session_data()
            or _refresh_heads_after_session_data_push()
        )
        if not session_sync.ok:
            return _sync_failure_exit_code(session_sync)
        return 0


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Signal Copier VM watcher")
    parser.add_argument("--final-backup", action="store_true")
    args = parser.parse_args(argv)
    if args.final_backup:
        result = _push_session_data()
        if result is None:
            result = _refresh_heads_after_session_data_push()
        if not result.ok:
            return _sync_failure_exit_code(result)
        return 0
    return main()


if __name__ == "__main__":
    sys.exit(cli())
