"""Task Scheduler entry point; retry transport failures and watcher reloads."""
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent


def restart_delay(code, failures):
    if code in {0, 75}:
        return 10
    if code == 77:
        return min(300, 15 * 2 ** min(5, max(0, failures - 1)))
    return None


def main():
    runtime = ROOT / "runtime_data"
    runtime.mkdir(exist_ok=True)
    failures = 0
    while True:
        prefix = runtime / ("watcher_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
        env = dict(os.environ, BOT_RUNTIME_DATA_DIR=str(runtime))
        with Path(str(prefix) + ".out.log").open("xb") as output:
            with Path(str(prefix) + ".err.log").open("xb") as errors:
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
