from pathlib import Path
import re

from tools import parse_export
from tools import setup_env


ROOT = Path(__file__).resolve().parents[1]


def _read_repo_file(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_env_example_does_not_ship_sensitive_values():
    text = _read_repo_file(".env.example")
    values = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value

    sensitive_keys = {
        "TELEGRAM_API_ID",
        "TELEGRAM_API_HASH",
        "TELEGRAM_PHONE",
        "CANAL_1_ID",
        "CANAL_2_ID",
        "GOOGLE_API_KEY",
        "MT5_LOGIN",
        "MT5_PASSWORD",
        "MT5_SERVER",
        "TELEGRAM_BOT_TOKEN",
    }
    assert sensitive_keys <= values.keys()
    assert all(values[key] == "" for key in sensitive_keys)

    assert "AIza" not in text
    assert not re.search(r"\+[1-9]\d{7,14}", text)
    assert not re.search(r"-100\d{8,}", text)


def test_parse_export_does_not_hardcode_channel_ids():
    text = _read_repo_file("tools/parse_export.py")

    assert not re.search(r"CANAL_[12]_TELEGRAM_ID\s*=\s*\d", text)
    assert not re.search(r"env CANAL_[12]_ID\s*=\s*-100\d+", text)


def test_parse_export_derives_export_chat_id_from_env_channel_id():
    assert parse_export.telegram_export_id_from_env("-1001234567890") == 1234567890
    assert parse_export.telegram_export_id_from_env("1234567890") == 1234567890


def test_setup_env_renders_env_file_without_template_secrets():
    answers = {
        "TELEGRAM_API_ID": "12345",
        "TELEGRAM_API_HASH": "hash_value",
        "TELEGRAM_PHONE": "test_telegram_phone",
        "CANAL_1_ID": "-1001234567890",
        "CANAL_2_ID": "-1009876543210",
        "GOOGLE_API_KEY": "google_key",
        "MT5_LOGIN": "67890",
        "MT5_PASSWORD": "mt5_password",
        "MT5_SERVER": "Broker-Demo",
        "LOT_SIZE": "0.01",
        "NOTIFY_CHAT_ID": "me",
    }

    rendered = setup_env.render_env(answers)

    assert "TELEGRAM_API_ID=12345" in rendered
    assert "MT5_PASSWORD=mt5_password" in rendered
    assert "GOOGLE_API_KEY=google_key" in rendered
    assert rendered.endswith("\n")


def test_strategy_shadow_modules_cannot_reach_live_order_execution():
    shadow_modules = (
        "strategy_shadow_contracts.py",
        "strategy_shadow_catalog.py",
        "strategy_shadow_engine.py",
        "strategy_shadow_runtime.py",
        "strategy_shadow_report.py",
    )
    forbidden_import = re.compile(
        r"(?m)^\s*(?:from|import)\s+(?:executor|pending_actions|MetaTrader5)\b"
    )

    for path in shadow_modules:
        text = _read_repo_file(path)
        assert forbidden_import.search(text) is None, path
        assert "order_send" not in text, path
