"""Bounded Canal 1 paper screening. No MT5 connection, orders or deployment."""

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
    for option in ("stream", "coverage", "raw-audit", "output"):
        run.add_argument("--" + option, type=Path, required=True)
    run.add_argument("--resume", action="store_true")
    run.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    verify = commands.add_parser("verify")
    verify.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from research.dubai_paper_campaign import BUDGET, run_campaign, verify_campaign
    try:
        if args.command == "verify":
            result = verify_campaign(args.output)
        elif args.worker:
            report = run_campaign(args.stream, args.coverage, args.raw_audit, args.output, resume=args.resume)
            result = {k: report[k] for k in ("status", "candidate_count", "paper_finalists", "live_activation_allowed")}
        else:
            command = [sys.executable, str(Path(__file__).resolve()), "run", "--worker"]
            for option in ("stream", "coverage", "raw-audit", "output"):
                command.extend(["--" + option, str(getattr(args, option.replace("-", "_")).resolve())])
            if args.resume:
                command.append("--resume")
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
                    raise TimeoutError("paper screen process deadline exhausted")
                return code
        print(json.dumps(result, sort_keys=True), flush=True)
        return 0
    except (ValueError, OSError, KeyError, TypeError, TimeoutError) as exc:
        print(json.dumps({"status": "failed_closed", "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
