"""
run_bot_watch.py — Wrapper que lanza el bot y lo reinicia al detectar
commits nuevos en `origin/main`.

Uso:
    python tools/run_bot_watch.py

Funcionamiento:
  1. Lanza `python main.py` como subproceso.
  2. Cada 60s hace `git fetch` y compara HEAD local vs origin/main.
  3. Si hay commits nuevos:
       - Para el bot (SIGTERM, espera 10s, SIGKILL si no responde).
       - Hace `git pull --ff-only origin main`.
       - Vuelve a lanzar el bot.
  4. Si el bot termina inesperadamente, lo relanza tras 5s.

Por qué: la sesión 2026-05-06 dejó al bot corriendo todo el día con
código pre-f985140 porque nadie lo reinició tras el pull. Estos commits
incluían fixes de bloqueo del event loop. Resultado: 115min congelado +
13 señales perdidas + 1 posición huérfana sin SL/TP.

Detener el wrapper: Ctrl+C (cierra el bot también).
"""

import os
import json
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Forzar UTF-8 en stdout/stderr para no crashear con caracteres no-ASCII
# cuando la consola de Windows usa cp1252 (u otro codec estrecho).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_DIR = Path(__file__).resolve().parent.parent
MAIN_PY  = REPO_DIR / "main.py"
RECONCILE_STATUS_FILE = REPO_DIR / "data" / "reconcile_status.json"
POLL_SEC = 60   # cada cuánto comprobar commits nuevos
RESTART_GRACE_SEC = 10  # tiempo para SIGTERM antes de SIGKILL
RELAUNCH_DELAY_SEC = 5  # espera entre fin del bot y relanzamiento


def _git(*args: str, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=REPO_DIR, capture_output=capture, text=True, check=False
    )


def _local_head() -> str:
    return _git("rev-parse", "HEAD").stdout.strip()


def _remote_head() -> str:
    return _git("rev-parse", "origin/main").stdout.strip()


def _pull_main_ff(capture: bool = True) -> subprocess.CompletedProcess:
    return _git("pull", "--ff-only", "origin", "main", capture=capture)


def _pull_main_and_refresh_heads() -> tuple[subprocess.CompletedProcess, str, str]:
    pull = _pull_main_ff()
    return pull, _local_head(), _remote_head()


def _refresh_heads_after_session_data_push() -> tuple[str, str]:
    """Refresh refs after this watcher may have pushed a data commit."""
    _git("fetch", "origin", "main")
    local = _local_head()
    remote = _remote_head()
    if local == remote:
        return local, remote

    pull, local, remote = _pull_main_and_refresh_heads()
    if pull.returncode != 0:
        print(f"[Watch] git pull tras subir datos fallo:\n{pull.stderr}",
              flush=True)
    return local, remote


def _spawn_bot() -> subprocess.Popen:
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
    """Ejecuta reconcile.py para regenerar data/ledger.jsonl.

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
        "command": [sys.executable, "reconcile.py", "--quiet"],
    }
    try:
        rec = subprocess.run(
            [sys.executable, "reconcile.py", "--quiet"],
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
            print(f"[Watch] reconcile.py fallo (rc={rec.returncode}): "
                  f"{(rec.stderr or rec.stdout or '')[:1000]}", flush=True)
    except Exception as e:
        status.update({
            "ok": False,
            "exception_type": type(e).__name__,
            "stderr": str(e),
        })
        print(f"[Watch] error ejecutando reconcile.py: {e}", flush=True)
    finally:
        status["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        status["duration_s"] = round(time.time() - started, 2)
        status["ledger_exists"] = ledger_file.exists()
        status["ledger_size_bytes"] = (
            ledger_file.stat().st_size if ledger_file.exists() else 0
        )
        _write_reconcile_status(status)
    return bool(status["ok"])


def _push_session_data() -> None:
    """Sube data/trade_events.jsonl + ledger + journal a GitHub si hay cambios.

    Replica el comportamiento del run_bot.bat original (que solo se ejecutaba
    al cerrar el bot). Con el watcher el bot puede vivir días sin parar, así
    que sin esto los logs no se subirían y no podríamos analizar la sesión.
    Llamamos a esta función después de cada parada/reinicio del bot.

    Antes de subir, regenera el ledger reconciliado (reconcile.py).
    """
    _regenerate_ledger()
    files = [
        "data/trade_events.jsonl",
        "data/ledger.jsonl",
        "data/reconcile_status.json",
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

        from datetime import datetime
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        msg = _git("commit", "-m", f"data: sesion {ts}")
        if msg.returncode != 0:
            print(f"[Watch] git commit falló: {msg.stderr}", flush=True)
            return
        # Pull --rebase antes del push: si el repo remoto avanzó (e.g. fix
        # nuevo), rebasamos los datos sobre el código nuevo en vez de
        # rechazar con non-fast-forward.
        _git("pull", "--rebase", "origin", "main")
        push = _git("push", "origin", "main")
        if push.returncode != 0:
            print(f"[Watch] git push falló: {push.stderr}", flush=True)
        else:
            print(f"[Watch] datos de sesión subidos a GitHub.", flush=True)
    except Exception as e:
        print(f"[Watch] _push_session_data error: {e}", flush=True)


def main() -> int:
    if not MAIN_PY.exists():
        print(f"[Watch] No encuentro {MAIN_PY}", flush=True)
        return 1

    # Fetch inicial para tener referencia clara del remoto
    _git("fetch", "origin", "main")
    last_local  = _local_head()
    last_remote = _remote_head()
    print(f"[Watch] HEAD local={last_local[:8]} remote={last_remote[:8]}", flush=True)
    if last_local != last_remote:
        print("[Watch] El local está desfasado — pull antes de arrancar.", flush=True)
        _pull_main_ff(capture=False)
        last_local = _local_head()

    proc = _spawn_bot()
    last_check = time.time()

    try:
        while True:
            # Si el bot murió inesperadamente, relanzar
            if proc.poll() is not None:
                print(f"[Watch] Bot terminó con código {proc.returncode}. "
                      f"Relanzo en {RELAUNCH_DELAY_SEC}s.", flush=True)
                _push_session_data()  # sube datos antes de relanzar
                last_local, last_remote = _refresh_heads_after_session_data_push()
                time.sleep(RELAUNCH_DELAY_SEC)
                proc = _spawn_bot()
                continue

            # Cada POLL_SEC comprobar commits nuevos
            now = time.time()
            if now - last_check >= POLL_SEC:
                last_check = now
                _git("fetch", "origin", "main")
                remote = _remote_head()
                if remote != last_remote:
                    print(f"[Watch] Commit nuevo detectado: {last_remote[:8]} -> "
                          f"{remote[:8]}. Reinicio.", flush=True)
                    _stop_bot(proc)
                    _push_session_data()  # sube datos antes del pull
                    pull, last_local, last_remote = _pull_main_and_refresh_heads()
                    print(pull.stdout, end="", flush=True)
                    if pull.returncode != 0:
                        print(f"[Watch] git pull falló:\n{pull.stderr}", flush=True)
                        # Aun así relanzamos con el código que haya
                    proc = _spawn_bot()

            time.sleep(2)
    except KeyboardInterrupt:
        print("\n[Watch] Ctrl+C — cerrando bot.", flush=True)
        _stop_bot(proc)
        _push_session_data()
        return 0


if __name__ == "__main__":
    sys.exit(main())
