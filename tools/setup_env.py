"""Interactive helper to create a local .env file without committing secrets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import getpass
from pathlib import Path
import shutil


@dataclass(frozen=True)
class EnvField:
    key: str
    prompt: str
    required: bool = True
    default: str = ""
    secret: bool = False


FIELDS: tuple[EnvField, ...] = (
    EnvField("TELEGRAM_API_ID", "Telegram API ID"),
    EnvField("TELEGRAM_API_HASH", "Telegram API hash", secret=True),
    EnvField("TELEGRAM_PHONE", "Telegram phone in E.164 format"),
    EnvField("CANAL_1_ID", "Canal 1 Telegram ID"),
    EnvField("CANAL_2_ID", "Canal 2 Telegram ID"),
    EnvField("GOOGLE_API_KEY", "Google Gemini API key", secret=True),
    EnvField("MT5_LOGIN", "MT5 login"),
    EnvField("MT5_PASSWORD", "MT5 password", secret=True),
    EnvField("MT5_SERVER", "MT5 server"),
    EnvField("LOT_SIZE", "Lot size per position", default="0.01"),
    EnvField("DCA_STEP", "DCA step in USD", default="1.0"),
    EnvField("CANAL1_BUY_STICKER_ID", "Canal 1 BUY sticker ID", required=False),
    EnvField("CANAL1_SELL_STICKER_ID", "Canal 1 SELL sticker ID", required=False),
    EnvField("TELEGRAM_BOT_TOKEN", "Telegram bot token for notifications", required=False, secret=True),
    EnvField("NOTIFY_CHAT_ID", "Notify chat ID", required=False, default="me"),
)


SECTION_BREAKS = {
    "TELEGRAM_API_ID": "Telegram",
    "GOOGLE_API_KEY": "Google Gemini",
    "MT5_LOGIN": "MetaTrader 5",
    "LOT_SIZE": "Trading",
    "CANAL1_BUY_STICKER_ID": "Canal 1 stickers",
    "TELEGRAM_BOT_TOKEN": "Notifications",
}


def render_env(values: dict[str, str]) -> str:
    """Render .env content from collected values."""
    lines: list[str] = []
    for field in FIELDS:
        section = SECTION_BREAKS.get(field.key)
        if section:
            if lines:
                lines.append("")
            lines.append(f"# {section}")
        lines.append(f"{field.key}={values.get(field.key, field.default)}")
    return "\n".join(lines) + "\n"


def prompt_for_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for field in FIELDS:
        while True:
            suffix = f" [{field.default}]" if field.default else ""
            prompt = f"{field.prompt}{suffix}: "
            value = getpass.getpass(prompt) if field.secret else input(prompt)
            if not value and field.default:
                value = field.default
            if value or not field.required:
                values[field.key] = value
                break
            print(f"{field.key} is required.")
    return values


def write_env_file(target: Path, values: dict[str, str]) -> Path | None:
    backup = None
    if target.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = target.with_name(f"{target.name}.backup-{stamp}")
        shutil.copy2(target, backup)
    target.write_text(render_env(values), encoding="utf-8")
    return backup


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    target = root / ".env"

    print("This creates a local .env file. Values are not printed and .env is gitignored.")
    values = prompt_for_values()
    backup = write_env_file(target, values)

    print(f"Wrote {target}")
    if backup:
        print(f"Previous .env backed up to {backup}")


if __name__ == "__main__":
    main()

