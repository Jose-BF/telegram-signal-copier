"""Durable, bounded-memory event trace for shared replay risk."""

from __future__ import annotations

from dataclasses import asdict
import gzip
import hashlib
import json
from pathlib import Path

from research.dubai_iterative.shared_replay import (
    SharedReplayResult, SharedRiskFrame, SharedRiskPoint,
)


TRACE_NAME = "risk.jsonl.gz"
MANIFEST_NAME = "manifest.json"


def _digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def _encode(kind: str, value) -> bytes:
    return (json.dumps({"kind": kind, "value": asdict(value)},
                       sort_keys=True, separators=(",", ":"), allow_nan=False)
            + "\n").encode("utf-8")


class SharedRiskTraceWriter:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=False, exist_ok=False)
        self._stream = gzip.open(self.directory / TRACE_NAME, "xb", compresslevel=1)
        self.point_count = 0
        self.frame_count = 0
        self.finished = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        if not self._stream.closed:
            self._stream.close()

    def record_point(self, point: SharedRiskPoint):
        if self._stream.closed or self.finished or not isinstance(point, SharedRiskPoint):
            raise ValueError("open typed shared risk trace required")
        self._stream.write(_encode("point", point))
        self.point_count += 1

    def record_frame(self, frame: SharedRiskFrame):
        if self._stream.closed or self.finished or not isinstance(frame, SharedRiskFrame):
            raise ValueError("open typed shared risk trace required")
        self._stream.write(_encode("frame", frame))
        self.frame_count += 1

    def finish(self, report: SharedReplayResult) -> dict:
        if (self._stream.closed or self.finished
                or not isinstance(report, SharedReplayResult) or not report.risk_streamed
                or report.risk or report.risk_grid
                or report.risk_point_count != self.point_count
                or report.risk_grid_count != self.frame_count):
            raise ValueError("trace and streamed replay report differ")
        self.close()
        path = self.directory / TRACE_NAME
        manifest = {
            "schema": "shared_risk_trace_v1",
            "risk_sha256": _digest(path), "risk_bytes": path.stat().st_size,
            "risk_point_count": self.point_count,
            "risk_grid_count": self.frame_count,
            "expected_quote_count": report.expected_quote_count,
            "scopes": [[channel, identity] for channel, identity, _ in report.baskets],
            "currency": report.profile.account_currency,
            "currency_digits": report.currency_digits,
            "blockers": list(report.blockers),
            "source_binding": "external_required_for_historical_claims",
        }
        with (self.directory / MANIFEST_NAME).open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
        self.finished = True
        return manifest


def _point(value: dict) -> SharedRiskPoint:
    return SharedRiskPoint(**{**value, "positions": tuple(
        tuple(position) for position in value["positions"])})


def _frame(value: dict) -> SharedRiskFrame:
    return SharedRiskFrame(
        tick_index=value["tick_index"], time_ns=value["time_ns"],
        states=tuple((channel, identity, _point(point) if point is not None else None)
                     for channel, identity, point in value["states"]),
        blockers=tuple(value["blockers"]),
    )


def iter_shared_risk_trace(directory: Path):
    """Verify artifact bytes first; consume fully to verify record counts."""
    directory = Path(directory)
    manifest = json.loads((directory / MANIFEST_NAME).read_text(encoding="utf-8"))
    path = directory / TRACE_NAME
    if (manifest.get("schema") != "shared_risk_trace_v1"
            or path.stat().st_size != manifest.get("risk_bytes")
            or _digest(path) != manifest.get("risk_sha256")):
        raise ValueError("shared risk trace integrity mismatch")
    counts = {"point": 0, "frame": 0}
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            record = json.loads(line)
            kind = record["kind"]
            if kind == "point":
                value = _point(record["value"])
            elif kind == "frame":
                value = _frame(record["value"])
            else:
                raise ValueError("unknown shared risk trace record")
            counts[kind] += 1
            yield kind, value
    if (counts["point"] != manifest["risk_point_count"]
            or counts["frame"] != manifest["risk_grid_count"]):
        raise ValueError("shared risk trace count mismatch")
