import json

import pytest

from telegram_notifications import (
    NotificationTransportError,
    send_photo_with_caption,
)


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_photo_upload_then_unicode_caption_uses_json():
    requests = []
    responses = iter([
        _Response({"ok": True, "result": {"message_id": 737}}),
        _Response({"ok": True, "result": {
            "message_id": 737,
            "caption": "🧪 Operación · revisión",
        }}),
    ])

    def opener(request, timeout):
        requests.append(request)
        return next(responses)

    message_id = send_photo_with_caption(
        "token", -100123, b"\x89PNG\r\n\x1a\nimage",
        "🧪 Operación · revisión",
        opener=opener,
        boundary="test-boundary",
    )

    assert message_id == 737
    assert requests[0].full_url.endswith("/sendPhoto")
    assert b'name="caption"' not in requests[0].data
    assert requests[1].full_url.endswith("/editMessageCaption")
    payload = json.loads(requests[1].data.decode("utf-8"))
    assert payload["caption"] == "🧪 Operación · revisión"


def test_upload_error_reports_stage_without_leaking_token():
    calls = []

    def opener(request, timeout):
        calls.append(request)
        raise OSError("offline")

    with pytest.raises(NotificationTransportError) as exc:
        send_photo_with_caption(
            "secret-token", 123, b"\x89PNG\r\n\x1a\nimage", "alerta",
            opener=opener,
        )

    assert "sendPhoto" in str(exc.value)
    assert "secret-token" not in str(exc.value)
    assert len(calls) == 1


def test_caption_error_deletes_orphan_photo_before_fallback():
    requests = []
    responses = iter([
        _Response({"ok": True, "result": {"message_id": 901}}),
        _Response({"ok": False, "description": "caption rejected"}),
        _Response({"ok": True, "result": True}),
    ])

    def opener(request, timeout):
        requests.append(request)
        return next(responses)

    with pytest.raises(NotificationTransportError, match="editMessageCaption"):
        send_photo_with_caption(
            "token", 123, b"\x89PNG\r\n\x1a\nimage", "alerta",
            opener=opener,
        )

    assert requests[-1].full_url.endswith("/deleteMessage")
