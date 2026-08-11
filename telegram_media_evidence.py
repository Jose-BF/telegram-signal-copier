"""Asynchronous, replay-safe capture of Telegram media evidence."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import config
import journal
import runtime_paths


MEDIA_ARCHIVE_STREAM = "telegram_media.jsonl"
MEDIA_DIRECTORY = "telegram_media"
_PERSIST_LOCK = threading.Lock()


@dataclass(frozen=True)
class MediaDescriptor:
    media_type: str
    media_id: int | str | None
    mime_type: str | None
    reported_size: int | None
    file_name: str | None


@dataclass(frozen=True)
class CaptureResult:
    status: str
    sha256: str | None = None
    size_bytes: int | None = None
    path: Path | None = None
    archive_appended: bool = False
    error: str | None = None


def _document_file_name(document) -> str | None:
    for attribute in getattr(document, "attributes", None) or ():
        value = getattr(attribute, "file_name", None)
        if value:
            return str(value)
    return None


def describe_message_media(msg) -> MediaDescriptor | None:
    sticker = getattr(msg, "sticker", None)
    photo = getattr(msg, "photo", None)
    document = getattr(msg, "document", None)
    if sticker is not None:
        source = document or sticker
        return MediaDescriptor(
            media_type="sticker",
            media_id=getattr(source, "id", None),
            mime_type=getattr(source, "mime_type", None),
            reported_size=getattr(source, "size", None),
            file_name=_document_file_name(source),
        )
    if photo is not None:
        return MediaDescriptor(
            media_type="photo",
            media_id=getattr(photo, "id", None),
            mime_type="image/jpeg",
            reported_size=None,
            file_name=None,
        )
    if document is not None:
        return MediaDescriptor(
            media_type="document",
            media_id=getattr(document, "id", None),
            mime_type=getattr(document, "mime_type", None),
            reported_size=getattr(document, "size", None),
            file_name=_document_file_name(document),
        )
    return None


def has_capture_candidate(msg) -> bool:
    return describe_message_media(msg) is not None


def _safe_extension(descriptor: MediaDescriptor, payload: bytes) -> str:
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if payload.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if payload.startswith(b"RIFF") and payload[8:12] == b"WEBP":
        return ".webp"
    by_mime = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "application/x-tgsticker": ".tgs",
        "video/webm": ".webm",
        "video/mp4": ".mp4",
    }
    mime_extension = by_mime.get(str(descriptor.mime_type or "").lower())
    if mime_extension:
        return mime_extension
    suffix = Path(descriptor.file_name or "").suffix.lower()
    if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
        return suffix
    return ".bin"


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _append_archive_record(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    with path.open("ab") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _persist_media(
    runtime_dir: Path,
    channel: str,
    descriptor: MediaDescriptor,
    payload: bytes,
    *,
    message_id: int,
    update_kind: str,
    message_revision_id: str,
) -> tuple[Path, str, bool]:
    digest = hashlib.sha256(payload).hexdigest()
    extension = _safe_extension(descriptor, payload)
    target = runtime_dir / MEDIA_DIRECTORY / channel / f"{digest}{extension}"
    archive = runtime_dir / MEDIA_ARCHIVE_STREAM

    with _PERSIST_LOCK:
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ValueError(f"media evidence hash mismatch: {target}")
            return target, digest, False

        archive_record = {
            "schema_version": 1,
            "captured_at_utc": datetime.now(timezone.utc).isoformat(
                timespec="milliseconds"
            ),
            "channel": channel,
            "message_id": int(message_id),
            "update_kind": update_kind,
            "message_revision_id": message_revision_id,
            "media_type": descriptor.media_type,
            "media_id": descriptor.media_id,
            "mime_type": descriptor.mime_type,
            "file_name": descriptor.file_name,
            "sha256": digest,
            "size_bytes": len(payload),
            "extension": extension,
            "payload_encoding": "base64",
            "payload_base64": base64.b64encode(payload).decode("ascii"),
        }
        # Archive first: a crash can leave a duplicate record, never a blob
        # that exists only on the production VM.
        _append_archive_record(archive, archive_record)
        _atomic_write(target, payload)
        return target, digest, True


def _emit(event_writer: Callable, sig_id: str, event: str, **fields) -> None:
    try:
        event_writer(sig_id, event, **fields)
    except Exception as exc:
        print(
            f"[Media] No se pudo registrar {event}: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )


async def capture_message_media(
    client,
    msg,
    *,
    channel: str,
    update_kind: str,
    message_revision_id: str,
    runtime_dir: Path | None = None,
    max_bytes: int | None = None,
    timeout_s: float | None = None,
    event_writer: Callable = journal.event,
) -> CaptureResult | None:
    """Download and archive media without propagating operational failures."""

    descriptor = describe_message_media(msg)
    if descriptor is None:
        return None

    runtime_dir = Path(runtime_dir or runtime_paths.active_data_dir()).resolve()
    max_bytes = int(
        max_bytes
        if max_bytes is not None
        else getattr(config, "TELEGRAM_MEDIA_MAX_BYTES", 8 * 1024 * 1024)
    )
    timeout_s = float(
        timeout_s
        if timeout_s is not None
        else getattr(config, "TELEGRAM_MEDIA_DOWNLOAD_TIMEOUT_S", 30.0)
    )
    message_id = int(getattr(msg, "id"))
    sig_id = f"{channel}_{message_id}"
    common = {
        "channel": channel,
        "message_id": message_id,
        "update_kind": update_kind,
        "message_revision_id": message_revision_id,
        "media_type": descriptor.media_type,
        "media_id": descriptor.media_id,
        "mime_type": descriptor.mime_type,
        "file_name": descriptor.file_name,
        "reported_size": descriptor.reported_size,
        "max_bytes": max_bytes,
    }
    _emit(
        event_writer,
        sig_id,
        "telegram_media_capture_requested",
        **common,
    )

    if (
        descriptor.reported_size is not None
        and int(descriptor.reported_size) > max_bytes
    ):
        _emit(
            event_writer,
            sig_id,
            "telegram_media_capture_skipped",
            **common,
            reason="reported_size_exceeds_limit",
        )
        return CaptureResult(status="skipped")

    attempts = max(
        1,
        int(getattr(config, "TELEGRAM_MEDIA_DOWNLOAD_ATTEMPTS", 2)),
    )
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            download = client.download_media(msg, file=bytes)
            payload = (
                await asyncio.wait_for(download, timeout=timeout_s)
                if timeout_s > 0
                else await download
            )
            if not isinstance(payload, (bytes, bytearray, memoryview)):
                raise TypeError("Telegram media download did not return bytes")
            payload = bytes(payload)
            if not payload:
                raise ValueError("Telegram media download returned no bytes")
            if len(payload) > max_bytes:
                _emit(
                    event_writer,
                    sig_id,
                    "telegram_media_capture_skipped",
                    **common,
                    actual_size=len(payload),
                    reason="downloaded_size_exceeds_limit",
                )
                return CaptureResult(status="skipped", size_bytes=len(payload))

            path, digest, archive_appended = await asyncio.to_thread(
                _persist_media,
                runtime_dir,
                channel,
                descriptor,
                payload,
                message_id=message_id,
                update_kind=update_kind,
                message_revision_id=message_revision_id,
            )
            relative_path = path.relative_to(runtime_dir).as_posix()
            _emit(
                event_writer,
                sig_id,
                "telegram_media_capture_stored",
                **common,
                media_sha256=digest,
                size_bytes=len(payload),
                storage_path=relative_path,
                archive_stream=MEDIA_ARCHIVE_STREAM,
                archive_appended=archive_appended,
                attempts=attempt,
            )
            return CaptureResult(
                status="stored",
                sha256=digest,
                size_bytes=len(payload),
                path=path,
                archive_appended=archive_appended,
            )
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                await asyncio.sleep(0.25 * attempt)

    assert last_error is not None
    _emit(
        event_writer,
        sig_id,
        "telegram_media_capture_failed",
        **common,
        attempts=attempts,
        exception_type=type(last_error).__name__,
        exception_message=str(last_error)[:500],
    )
    return CaptureResult(
        status="failed",
        error=f"{type(last_error).__name__}: {last_error}",
    )
