"""Run or verify one local fixed diagnostic; no search or live dependencies."""

import argparse
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--config", required=True, type=Path)
    run.add_argument("--output-dir", required=True, type=Path)
    run.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    verify = commands.add_parser("verify")
    verify.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    from research.strategy_study import load_config, run_study, verify_study

    try:
        if args.command == "verify":
            summary = verify_study(args.output_dir)
        elif args.worker:
            report = run_study(args.config, args.output_dir)
            summary = {key: report[key] for key in ("status", "denominator", "engine_evaluations", "search_candidates")}
        else:
            config = load_config(args.config)
            completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), "run", "--worker",
                "--config", str(args.config.resolve()), "--output-dir", str(args.output_dir.resolve())],
                timeout=config["budget"]["max_wall_seconds"],
                capture_output=True, text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            print(completed.stdout, end="")
            print(completed.stderr, end="", file=sys.stderr)
            return completed.returncode
        print(json.dumps(summary, sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, TypeError, TimeoutError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "failed_closed", "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
