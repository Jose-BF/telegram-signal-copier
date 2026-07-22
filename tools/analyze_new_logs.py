"""Analyze only new trade log records and print a compact report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import log_analysis
import runtime_paths


DEFAULT_EVENTS = runtime_paths.active_data_dir(ROOT) / "trade_events.jsonl"


def _git_private_path(name: str) -> Path:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-path", name],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        path = Path(result.stdout.strip())
        return path if path.is_absolute() else ROOT / path
    except (OSError, subprocess.CalledProcessError):
        return ROOT / ".git" / name


def _load_json(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Resume solo los eventos nuevos de trade_events.jsonl"
    )
    parser.add_argument("--events", type=Path,
                        default=DEFAULT_EVENTS)
    parser.add_argument("--state", type=Path,
                        default=_git_private_path("log_analysis_cursor.json"))
    parser.add_argument("--report", type=Path,
                        default=_git_private_path("log_analysis_latest.json"))
    parser.add_argument("--full", action="store_true",
                        help="ignora el cursor y reconstruye todo")
    parser.add_argument("--no-save", action="store_true",
                        help="no actualiza cursor ni informe local")
    parser.add_argument("--json", action="store_true",
                        help="imprime el informe JSON completo")
    args = parser.parse_args(argv)

    cursor = None if args.full else _load_json(args.state)
    scan = log_analysis.scan_jsonl(
        args.events,
        cursor=cursor,
        force_full=args.full,
    )
    report = log_analysis.summarize_events(scan.events)
    report["scan"] = {
        "mode": scan.mode,
        "start_offset": scan.start_offset,
        "end_offset": scan.end_offset,
        "reset_reason": scan.reset_reason,
        "parse_errors": len(scan.parse_errors),
        "incomplete_tail": scan.incomplete_tail,
    }
    report["status_snapshots"] = log_analysis.load_status_snapshots(
        args.events.parent
    )

    if not args.no_save:
        _write_json(args.state, scan.cursor)
        _write_json(args.report, report)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(log_analysis.render_compact_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
