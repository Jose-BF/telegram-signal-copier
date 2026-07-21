from datetime import datetime

import pytest

from tools import set_channel_id


def test_updates_only_requested_channel_and_creates_backup(tmp_path):
    env_file = tmp_path / ".env"
    original = (
        "TELEGRAM_API_HASH=keep-secret\n"
        "CANAL_1_ID=-1001111111111\n"
        "CANAL_2_ID=-1003828356530\n"
        "MT5_PASSWORD=also-keep-secret\n"
    )
    env_file.write_text(original, encoding="utf-8")

    backup = set_channel_id.update_channel_id(
        env_file,
        channel="canal2",
        channel_id="-1003908582492",
        now=datetime(2026, 7, 21, 22, 30, 0),
    )

    assert backup is not None
    assert backup.read_text(encoding="utf-8") == original
    updated = env_file.read_text(encoding="utf-8")
    assert "CANAL_1_ID=-1001111111111" in updated
    assert "CANAL_2_ID=-1003908582492" in updated
    assert "TELEGRAM_API_HASH=keep-secret" in updated
    assert "MT5_PASSWORD=also-keep-secret" in updated
    assert "-1003828356530" not in updated


@pytest.mark.parametrize("value", ["3908582492", "-3908582492", "abc", "-100"])
def test_rejects_non_channel_ids(value):
    with pytest.raises(ValueError):
        set_channel_id.validate_channel_id(value)


def test_applies_versioned_manifest_without_touching_other_settings(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "CANAL_1_ID=-1001111111111\n"
        "CANAL_2_ID=-1003828356530\n"
        "MT5_PASSWORD=keep-secret\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "active_telegram_channels.json"
    manifest.write_text(
        '{"schema_version": 1, "channels": '
        '{"canal2": {"id": -1003908582492, "name": "Gold Signals"}}}\n',
        encoding="utf-8",
    )

    result = set_channel_id.apply_channel_manifest(
        manifest,
        env_file=env_file,
        now=datetime(2026, 7, 21, 23, 0, 0),
    )

    assert result["changed"] == ["canal2"]
    assert result["active_ids"] == {"canal2": "-1003908582492"}
    assert result["backup"] is not None
    rendered = env_file.read_text(encoding="utf-8")
    assert "CANAL_1_ID=-1001111111111" in rendered
    assert "CANAL_2_ID=-1003908582492" in rendered
    assert "MT5_PASSWORD=keep-secret" in rendered


def test_manifest_application_is_idempotent_and_does_not_create_backup(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "CANAL_2_ID=-1003908582492\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "active_telegram_channels.json"
    manifest.write_text(
        '{"schema_version": 1, "channels": '
        '{"canal2": {"id": -1003908582492}}}\n',
        encoding="utf-8",
    )

    result = set_channel_id.apply_channel_manifest(
        manifest,
        env_file=env_file,
    )

    assert result["changed"] == []
    assert result["backup"] is None
    assert list(tmp_path.glob(".env.backup-*")) == []


def test_manifest_rejects_unknown_channels(tmp_path):
    manifest = tmp_path / "active_telegram_channels.json"
    manifest.write_text(
        '{"schema_version": 1, "channels": '
        '{"canal3": {"id": -1003908582492}}}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown channel"):
        set_channel_id.apply_channel_manifest(
            manifest,
            env_file=tmp_path / ".env",
        )
