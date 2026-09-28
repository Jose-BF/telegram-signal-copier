"""Task Scheduler entry point; retry transport failures and watcher reloads."""
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
_LOG_NAME_ATTEMPTS = 8


def restart_delay(code, failures):
    if code in {0, 75}:
        return 10
    if code == 77:
        return min(300, 15 * 2 ** min(5, max(0, failures - 1)))
    return None


@contextmanager
def _attempt_logs(runtime):
    # Wall time can repeat; exclusive creation still protects prior evidence.
    for _ in range(_LOG_NAME_ATTEMPTS):
        prefix = runtime / ("watcher_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                            + "_" + uuid4().hex)
        with ExitStack() as files:
            try:
                output = files.enter_context(Path(str(prefix) + ".out.log").open("xb"))
                errors = files.enter_context(Path(str(prefix) + ".err.log").open("xb"))
            except FileExistsError:
                continue
            yield output, errors
            return
    raise FileExistsError("watcher log identity collision budget exhausted")


def main():
    runtime = ROOT / "runtime_data"
    runtime.mkdir(exist_ok=True)
    failures = 0
    while True:
        env = dict(os.environ, BOT_RUNTIME_DATA_DIR=str(runtime))
        with _attempt_logs(runtime) as (output, errors):
            started = time.monotonic()
            code = subprocess.call(
                [sys.executable, "-u", str(ROOT / "tools/run_bot_watch.py")],
                cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                stdout=output, stderr=errors,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        failures = 1 if time.monotonic() - started >= 600 else failures + 1
        delay = restart_delay(code, failures)
        if delay is None:
            return code
        time.sleep(delay)


if __name__ == "__main__":
    sys.exit(main())
