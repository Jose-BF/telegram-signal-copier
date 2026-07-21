"""Safely update one Telegram channel ID in the local .env file."""

from __future__ import annotations

import argparse
import json
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


def _render_channel_updates(original: str, updates: dict[str, str]) -> str:
    lines = original.splitlines()
    for channel, value in updates.items():
        if channel not in CHANNEL_KEYS:
            raise ValueError(f"unknown channel: {channel}")
        key = CHANNEL_KEYS[channel]
        indexes = [
            index
            for index, line in enumerate(lines)
            if re.match(rf"^\s*{re.escape(key)}\s*=", line)
        ]
        if len(indexes) > 1:
            raise ValueError(f"duplicate {key} entries")
        replacement = f"{key}={value}"
        if indexes:
            lines[indexes[0]] = replacement
        else:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(replacement)
    return "\n".join(lines) + "\n"


def _current_channel_values(original: str) -> dict[str, str | None]:
    values: dict[str, str | None] = {}
    lines = original.splitlines()
    for channel, key in CHANNEL_KEYS.items():
        matches = [
            line.split("=", 1)[1].strip()
            for line in lines
            if re.match(rf"^\s*{re.escape(key)}\s*=", line)
        ]
        if len(matches) > 1:
            raise ValueError(f"duplicate {key} entries")
        values[channel] = matches[0] if matches else None
    return values


def apply_channel_manifest(
    manifest_file: Path,
    *,
    env_file: Path,
    now: datetime | None = None,
) -> dict:
    """Apply public channel routing from Git without exposing `.env` secrets."""
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"channel manifest not found: {manifest_file}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid channel manifest: {exc}") from exc
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported channel manifest schema_version")
    channels = manifest.get("channels")
    if not isinstance(channels, dict) or not channels:
        raise ValueError("channel manifest must declare at least one channel")

    desired: dict[str, str] = {}
    for channel, payload in channels.items():
        if channel not in CHANNEL_KEYS:
            raise ValueError(f"unknown channel: {channel}")
        if not isinstance(payload, dict) or "id" not in payload:
            raise ValueError(f"missing id for channel: {channel}")
        desired[channel] = validate_channel_id(payload["id"])

    original = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    current = _current_channel_values(original)
    changed = [
        channel for channel, value in desired.items()
        if current.get(channel) != value
    ]
    if not changed:
        return {
            "changed": [],
            "active_ids": desired,
            "backup": None,
        }

    rendered = _render_channel_updates(
        original,
        {channel: desired[channel] for channel in changed},
    )
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
    return {
        "changed": changed,
        "active_ids": desired,
        "backup": backup,
    }


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
