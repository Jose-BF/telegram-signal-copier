"""Safely update one Telegram channel ID in the local .env file."""

from __future__ import annotations

import argparse
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from uuid import uuid4


CHANNEL_KEYS = {
    "canal1": "CANAL_1_ID",
    "canal2": "CANAL_2_ID",
}
CHANNEL_ID_RE = re.compile(r"^-100\d{7,}$")


def validate_channel_id(value: str | int) -> str:
    normalized = str(value).strip()
    if not CHANNEL_ID_RE.fullmatch(normalized):
        raise ValueError(
            "Telegram channel ID must use the full -100... format"
        )
    return normalized


def update_channel_id(
    env_file: Path,
    *,
    channel: str,
    channel_id: str | int,
    now: datetime | None = None,
) -> Path | None:
    if channel not in CHANNEL_KEYS:
        raise ValueError(f"unknown channel: {channel}")
    value = validate_channel_id(channel_id)
    key = CHANNEL_KEYS[channel]
    original = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    lines = original.splitlines()
    indexes = [
        index
        for index, line in enumerate(lines)
        if re.match(rf"^\s*{re.escape(key)}\s*=", line)
    ]
    if len(indexes) > 1:
        raise ValueError(f"duplicate {key} entries in {env_file}")
    replacement = f"{key}={value}"
    if indexes:
        lines[indexes[0]] = replacement
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(replacement)
    rendered = "\n".join(lines) + "\n"

    backup = None
    if env_file.exists():
        stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
        backup = env_file.with_name(f"{env_file.name}.backup-{stamp}")
        shutil.copy2(env_file, backup)

    env_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = env_file.with_name(f"{env_file.name}.tmp-{uuid4().hex}")
    try:
        temporary.write_text(rendered, encoding="utf-8")
        os.replace(temporary, env_file)
    finally:
        if temporary.exists():
            temporary.unlink()
    return backup


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", choices=sorted(CHANNEL_KEYS), required=True)
    parser.add_argument("--id", dest="channel_id", required=True)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(__file__).resolve().parent.parent / ".env",
    )
    args = parser.parse_args(argv)
    try:
        backup = update_channel_id(
            args.env_file,
            channel=args.channel,
            channel_id=args.channel_id,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    key = CHANNEL_KEYS[args.channel]
    print(f"Updated {key} in {args.env_file}")
    if backup is not None:
        print(f"Backup: {backup}")
    print("Restart the bot to activate the new channel.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
