"""Replay frozen paper finalists; no MT5, bot activation or order execution."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    for option in ("screen", "stream", "coverage", "raw-audit", "output"):
        run.add_argument("--" + option, type=Path, required=True)
    run.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    verify = commands.add_parser("verify")
    verify.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from research.dubai_paper_finalists import BUDGET, run_finalists, verify_finalists
    try:
        if args.command == "verify":
            report = verify_finalists(args.output)
        elif args.worker:
            result = run_finalists(args.screen, args.stream, args.coverage, args.raw_audit, args.output)
            report = {"status": result["status"], "paper_candidates": [r["candidate_id"] for r in result["paper_candidates"]],
                      "production_activation_allowed": False}
        else:
            command = [sys.executable, str(Path(__file__).resolve()), "run", "--worker"]
            for option in ("screen", "stream", "coverage", "raw-audit", "output"):
                command.extend(["--" + option, str(getattr(args, option.replace("-", "_")).resolve())])
            expired = threading.Event()
            with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as process:
                def timeout():
                    expired.set()
                    process.kill()
                timer = threading.Timer(BUDGET["max_wall_seconds"], timeout)
                timer.daemon = True
                timer.start()
                try:
                    for line in process.stdout:
                        print(line, end="", flush=True)
                    code = process.wait()
                finally:
                    timer.cancel()
                    if process.poll() is None:
                        process.kill()
                        process.wait()
                if expired.is_set():
                    raise TimeoutError("paper finalist process deadline exhausted")
                return code
        print(json.dumps(report, sort_keys=True), flush=True)
        return 0
    except (ValueError, OSError, KeyError, TypeError, TimeoutError) as exc:
        print(json.dumps({"status": "failed_closed", "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
