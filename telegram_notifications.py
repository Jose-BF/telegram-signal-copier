"""Low-level Telegram Bot API transports used by rich notifications."""

from __future__ import annotations

import json
import uuid
import urllib.error
import urllib.request


class NotificationTransportError(RuntimeError):
    pass


def _read_json(response, stage: str) -> dict:
    try:
        payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise NotificationTransportError(f"{stage}: respuesta JSON inválida") from exc
    if not payload.get("ok"):
        description = str(payload.get("description") or "error desconocido")[:300]
        raise NotificationTransportError(f"{stage}: {description}")
    return payload


def _open(opener, request, timeout_s: float, stage: str) -> dict:
    try:
        with opener(request, timeout=timeout_s) as response:
            return _read_json(response, stage)
    except NotificationTransportError:
        raise
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            body = f"HTTP {getattr(exc, 'code', '?')}"
        raise NotificationTransportError(f"{stage}: {body}") from exc
    except Exception as exc:
        raise NotificationTransportError(
            f"{stage}: {type(exc).__name__}: {str(exc)[:200]}"
        ) from exc


def _multipart_photo(chat_id, png_bytes: bytes, boundary: str) -> bytes:
    boundary_bytes = boundary.encode("ascii")
    chunks = [
        b"--" + boundary_bytes + b"\r\n",
        b'Content-Disposition: form-data; name="chat_id"\r\n\r\n',
        str(chat_id).encode("ascii"), b"\r\n",
        b"--" + boundary_bytes + b"\r\n",
        b'Content-Disposition: form-data; name="photo"; filename="alert.png"\r\n',
        b"Content-Type: image/png\r\n\r\n",
        bytes(png_bytes), b"\r\n",
        b"--" + boundary_bytes + b"--\r\n",
    ]
    return b"".join(chunks)


def send_photo_with_caption(
    token: str,
    chat_id,
    png_bytes: bytes,
    caption: str,
    *,
    timeout_s: float = 10.0,
    opener=None,
    boundary: str | None = None,
) -> int:
    """Upload PNG first, then add Unicode caption through a JSON request."""
    if not token:
        raise NotificationTransportError("sendPhoto: token no configurado")
    if not png_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        raise NotificationTransportError("sendPhoto: imagen PNG inválida")
    opener = opener or urllib.request.urlopen
    boundary = boundary or f"----tsc-{uuid.uuid4().hex}"
    base = f"https://api.telegram.org/bot{token}"
    upload_request = urllib.request.Request(
        f"{base}/sendPhoto",
        data=_multipart_photo(chat_id, png_bytes, boundary),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    upload = _open(opener, upload_request, timeout_s, "sendPhoto")
    try:
        message_id = int(upload["result"]["message_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise NotificationTransportError(
            "sendPhoto: respuesta sin message_id"
        ) from exc

    caption_request = urllib.request.Request(
        f"{base}/editMessageCaption",
        data=json.dumps({
            "chat_id": chat_id,
            "message_id": message_id,
            "caption": str(caption),
        }, ensure_ascii=True).encode("ascii"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        _open(opener, caption_request, timeout_s, "editMessageCaption")
    except NotificationTransportError:
        delete_request = urllib.request.Request(
            f"{base}/deleteMessage",
            data=json.dumps({
                "chat_id": chat_id,
                "message_id": message_id,
            }).encode("ascii"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            _open(opener, delete_request, timeout_s, "deleteMessage")
        except NotificationTransportError:
            pass
        raise
    return message_id
