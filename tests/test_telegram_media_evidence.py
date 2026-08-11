import base64
import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import telegram_media_evidence


def _photo_message(message_id: int = 501):
    return SimpleNamespace(
        id=message_id,
        chat_id=-1001642806869,
        date=datetime(2026, 8, 11, 9, 30, tzinfo=timezone.utc),
        edit_date=None,
        photo=SimpleNamespace(id=77),
        sticker=None,
        document=None,
    )


def test_pending_capture_requests_exclude_completed_revisions(tmp_path):
    events_path = tmp_path / "trade_events.jsonl"
    rows = [
        {
            "sig": "canal1_501",
            "ev": "telegram_media_capture_requested",
            "channel": "canal1",
            "message_id": 501,
            "update_kind": "new",
            "message_revision_id": "revision_pending",
        },
        {
            "sig": "canal1_502",
            "ev": "telegram_media_capture_requested",
            "channel": "canal1",
            "message_id": 502,
            "update_kind": "edit",
            "message_revision_id": "revision_stored",
        },
        {
            "sig": "canal1_502",
            "ev": "telegram_media_capture_stored",
            "message_revision_id": "revision_stored",
        },
    ]
    events_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    assert telegram_media_evidence.load_pending_capture_requests(
        events_path
    ) == [rows[0]]


def test_cancelled_capture_remains_recoverable(tmp_path):
    events_path = tmp_path / "trade_events.jsonl"
    request = {
        "sig": "canal2_600",
        "ev": "telegram_media_capture_requested",
        "channel": "canal2",
        "message_id": 600,
        "update_kind": "new",
        "message_revision_id": "revision_cancelled",
    }
    rows = [
        request,
        {
            "sig": "canal2_600",
            "ev": "telegram_media_capture_cancelled",
            "message_revision_id": "revision_cancelled",
        },
    ]
    events_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    assert telegram_media_evidence.load_pending_capture_requests(
        events_path
    ) == [request]


@pytest.mark.asyncio
async def test_cancelled_download_records_recoverable_interruption(tmp_path):
    events = []
    started = asyncio.Event()

    class Client:
        async def download_media(self, message, file):
            started.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(
        telegram_media_evidence.capture_message_media(
            Client(),
            _photo_message(),
            channel="canal1",
            update_kind="new",
            message_revision_id="revision_cancelled",
            runtime_dir=tmp_path,
            event_writer=lambda sig, event, **fields: events.append(
                (sig, event, fields)
            ),
        )
    )
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert [event for _, event, _ in events] == [
        "telegram_media_capture_requested",
        "telegram_media_capture_cancelled",
    ]


@pytest.mark.asyncio
async def test_capture_archives_exact_media_and_links_revision(tmp_path):
    payload = b"\x89PNG\r\n\x1a\ntelegram-image"
    events = []

    class Client:
        async def download_media(self, message, file):
            assert message.id == 501
            assert file is bytes
            return payload

    result = await telegram_media_evidence.capture_message_media(
        Client(),
        _photo_message(),
        channel="canal1",
        update_kind="new",
        message_revision_id="msgrev_photo_501",
        runtime_dir=tmp_path,
        event_writer=lambda sig, event, **fields: events.append(
            (sig, event, fields)
        ),
    )

    assert result.status == "stored"
    assert result.sha256
    assert result.size_bytes == len(payload)
    assert result.path.read_bytes() == payload
    assert result.path.parent == tmp_path / "telegram_media" / "canal1"

    records = [
        json.loads(line)
        for line in (tmp_path / "telegram_media.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(records) == 1
    assert records[0]["sha256"] == result.sha256
    assert base64.b64decode(records[0]["payload_base64"]) == payload
    assert [event for _, event, _ in events] == [
        "telegram_media_capture_requested",
        "telegram_media_capture_stored",
    ]
    assert events[-1][2]["message_revision_id"] == "msgrev_photo_501"
    assert events[-1][2]["media_sha256"] == result.sha256


@pytest.mark.asyncio
async def test_capture_failure_is_recorded_and_never_raised(tmp_path):
    events = []

    class Client:
        async def download_media(self, message, file):
            raise TimeoutError("telegram media timeout")

    result = await telegram_media_evidence.capture_message_media(
        Client(),
        _photo_message(),
        channel="canal1",
        update_kind="new",
        message_revision_id="msgrev_photo_501",
        runtime_dir=tmp_path,
        event_writer=lambda sig, event, **fields: events.append(
            (sig, event, fields)
        ),
    )

    assert result.status == "failed"
    assert result.path is None
    assert not (tmp_path / "telegram_media.jsonl").exists()
    assert [event for _, event, _ in events] == [
        "telegram_media_capture_requested",
        "telegram_media_capture_failed",
    ]
    assert events[-1][2]["exception_type"] == "TimeoutError"


@pytest.mark.asyncio
async def test_same_payload_is_archived_once_but_every_revision_is_linked(
    tmp_path,
):
    payload = b"same-photo"
    events = []

    class Client:
        async def download_media(self, message, file):
            return payload

    for message_id in (501, 502):
        await telegram_media_evidence.capture_message_media(
            Client(),
            _photo_message(message_id),
            channel="canal1",
            update_kind="new",
            message_revision_id=f"msgrev_photo_{message_id}",
            runtime_dir=tmp_path,
            event_writer=lambda sig, event, **fields: events.append(
                (sig, event, fields)
            ),
        )

    records = (
        tmp_path / "telegram_media.jsonl"
    ).read_text(encoding="utf-8").splitlines()
    assert len(records) == 1
    stored = [fields for _, event, fields in events
              if event == "telegram_media_capture_stored"]
    assert len(stored) == 2
    assert stored[0]["media_sha256"] == stored[1]["media_sha256"]
    assert stored[0]["archive_appended"] is True
    assert stored[1]["archive_appended"] is False


@pytest.mark.asyncio
async def test_reported_oversized_document_is_skipped_without_download(
    tmp_path,
):
    events = []

    class Client:
        async def download_media(self, message, file):
            raise AssertionError("oversized media must not be downloaded")

    message = SimpleNamespace(
        id=503,
        chat_id=-1001642806869,
        date=datetime(2026, 8, 11, 9, 30, tzinfo=timezone.utc),
        edit_date=None,
        photo=None,
        sticker=None,
        document=SimpleNamespace(
            id=88,
            size=20_000_000,
            mime_type="video/mp4",
            attributes=[],
        ),
    )

    result = await telegram_media_evidence.capture_message_media(
        Client(),
        message,
        channel="canal1",
        update_kind="new",
        message_revision_id="msgrev_document_503",
        runtime_dir=tmp_path,
        max_bytes=8_000_000,
        event_writer=lambda sig, event, **fields: events.append(
            (sig, event, fields)
        ),
    )

    assert result.status == "skipped"
    assert [event for _, event, _ in events] == [
        "telegram_media_capture_requested",
        "telegram_media_capture_skipped",
    ]
    assert events[-1][2]["reason"] == "reported_size_exceeds_limit"
