"""Run or verify the bounded offline monthly Canal 1 engine probe."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    for option in ("stream", "coverage", "raw-audit", "output"):
        run.add_argument("--" + option, type=Path, required=True)
    run.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    verify = commands.add_parser("verify")
    verify.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from research.dubai_entry_probe import BUDGET, run_probe, verify_probe

    try:
        if args.command == "verify":
            result = verify_probe(args.output)
        elif args.worker:
            report = run_probe(args.stream, args.coverage, args.raw_audit, args.output)
            result = {key: report[key] for key in ("status", "summary", "engine_evaluations", "search_candidates")}
        else:
            command = [sys.executable, str(Path(__file__).resolve()), "run", "--worker"]
            for option in ("stream", "coverage", "raw-audit", "output"):
                command.extend(["--" + option, str(getattr(args, option.replace("-", "_")).resolve())])
            completed = subprocess.run(command, timeout=BUDGET["max_wall_seconds"],
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return completed.returncode
        print(json.dumps(result, sort_keys=True), flush=True)
        return 0
    except (ValueError, OSError, KeyError, TypeError, TimeoutError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "failed_closed", "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
