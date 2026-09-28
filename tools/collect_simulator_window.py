"""Immutable, read-only capture for an explicitly frozen UTC simulator window.

This helper is intentionally kept outside the live bot. It reads only an
append-only journal suffix and read-only MT5 APIs from the already running
terminal. It never logs in, sends, modifies, or closes an order.

Standalone descendant of the archived Sep 9 collector; the archived module is
never imported or modified. Source changes require a new explicit frozen binding.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import ctypes
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import zipfile
from uuid import uuid4


UTC = timezone.utc
REPO = Path(r"C:\Users\bot\telegram-signal-copier")
CONTROL = Path(r"C:\Users\bot\codex-control")
JOURNAL = REPO / "runtime_data" / "trade_events.jsonl"
HEARTBEAT = REPO / "runtime_data" / "runtime_heartbeat.json"
TERMINAL = Path(r"C:\Program Files\MetaTrader 5\terminal64.exe")
CONTRACT = "simulator_causal_capture_v2"
CHAIN_CONTRACTS = ("morning_causal_capture_v1", CONTRACT)
LABEL_RE = re.compile(r"^(?:pre|check|final)_\d{8}t\d{6}z_[0-9a-f]{8}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
BOUNDARY_BYTES = 1_048_576
TAIL_PROBE_BYTES = 4 * 1024 * 1024
CONTEXT_EVENTS = {
    "session_started",
    "live_strategy_contract",
    "broker_money_contract_snapshot",
    "mt5_account_connected",
    "strategy_shadow_runtime_started",
}
RAW_FIELDS = (
    "ev", "channel", "message_id", "message_revision_id", "date_utc", "ts",
    "text", "is_edit", "edit_date_utc", "reply_to_msg_id", "sticker_id",
)
# audit_raw_observations.CONTENT_FIELDS intersected with the frozen raw schema.
# Transport ts/is_edit are retained, not content; update_kind and full media
# evidence remain in the byte-exact delta. No live-repo import is needed here.
RAW_CONTENT_FIELDS = (
    "channel", "message_id", "message_revision_id", "date_utc", "edit_date_utc",
    "text", "sticker_id", "reply_to_msg_id",
)
RAW_NONNULL = ("channel", "message_id", "message_revision_id", "date_utc", "ts")
SYMBOLS = ("XAUUSD", "EURUSD")
SYMBOL_CONTRACT_FIELDS = (
    "volume_min", "volume_max", "volume_step", "point", "digits",
    "trade_stops_level", "trade_freeze_level", "trade_contract_size",
    "currency_base", "currency_profit", "currency_margin", "filling_mode",
    "order_mode",
)


class CaptureError(RuntimeError):
    pass


def strict_utc(value) -> datetime:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("timestamp_must_be_explicit_UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("timestamp_must_be_explicit_UTC")
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class CaptureWindow:
    start: datetime
    end: datetime

    def __post_init__(self):
        object.__setattr__(self, "start", strict_utc(self.start))
        object.__setattr__(self, "end", strict_utc(self.end))
        if not self.start < self.end or self.end - self.start > timedelta(hours=2):
            raise ValueError("window_must_be_positive_and_at_most_two_hours")


def _now() -> datetime:
    return datetime.now(UTC)


def _budget(started, limit):
    if time.monotonic() - started > limit:
        raise CaptureError("capture_runtime_budget_exceeded")


def _verify_sources(watched):
    for path, expected in watched.items():
        if sha256_file(path) != expected:
            raise CaptureError(f"frozen_source_changed:{path.name}")


def _frozen_capture(args, window, observed):
    path = Path(args.protocol).resolve()
    expected = validate_hash(args.protocol_sha256, "protocol_sha256")
    watched = {path: expected}
    _verify_sources(watched)
    protocol = read_json(path, max_bytes=16_000_000)
    payload = {key: value for key, value in protocol.items() if key != "fingerprint"}
    encoded = (json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True,
                          allow_nan=False) + "\n").encode()
    frozen = strict_utc(protocol["frozen_at_utc"])
    if (protocol.get("contract") != "simulator_forward_protocol_v2"
            or protocol.get("fingerprint") != hashlib.sha256(encoded).hexdigest()
            or CaptureWindow(protocol["start_utc"], protocol["end_utc"]) != window
            or frozen >= window.start or frozen > observed):
        raise CaptureError("protocol_not_frozen_for_exact_future_window")
    profile = protocol["profile_payload"]
    binding = profile.get("capture_contract")
    fields = {"collector_sha256", "wrapper_sha256", "expected_live_commit",
              "expected_account_identity_sha256", "collector_source"}
    if not isinstance(binding, dict) or set(binding) != fields:
        raise CaptureError("explicit_frozen_collector_source_required")
    source = binding["collector_source"]
    if (not isinstance(source, dict) or set(source) != {"path", "sha256"}
            or not isinstance(source["path"], str) or not source["path"].endswith(".py")
            or source["sha256"] != binding["collector_sha256"]
            or sum(row == source for row in profile.get("additional_sources", [])) != 1
            or sum(row.get("path") == source["path"] for row in profile.get("additional_sources", [])) != 1
            or binding["expected_live_commit"] != args.expected_commit
            or binding["expected_account_identity_sha256"] != args.expected_account_identity):
        raise CaptureError("collector_not_bound_into_frozen_profile")
    # The source proof names the preregistration workspace; deployment may be a
    # byte-identical standalone copy. Its actual runtime bytes must still match.
    watched[Path(__file__).resolve()] = validate_hash(binding["collector_sha256"], "collector_sha256")
    watched[Path(args.wrapper_path).resolve()] = validate_hash(binding["wrapper_sha256"], "wrapper_sha256")
    _verify_sources(watched)
    return {
        "forward_protocol": {"sha256": expected, "contract": protocol["contract"],
                             "fingerprint": protocol["fingerprint"]},
        "collector_source": dict(source),
    }, watched


def sha256_file(path: Path, *, start: int = 0, length: int | None = None) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        stream.seek(start)
        remaining = length
        while remaining is None or remaining:
            size = 4 * 1024 * 1024 if remaining is None else min(4 * 1024 * 1024, remaining)
            chunk = stream.read(size)
            if not chunk:
                if remaining:
                    raise CaptureError("short_file_while_hashing")
                break
            result.update(chunk)
            if remaining is not None:
                remaining -= len(chunk)
    return result.hexdigest()


def canonical_sha256(value) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate_json_key:{key}")
        value[key] = item
    return value


def read_json(path: Path, *, max_bytes: int = 32 * 1024 * 1024):
    if path.stat().st_size > max_bytes:
        raise CaptureError(f"json_too_large:{path.name}")
    return json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=lambda item: (_ for _ in ()).throw(ValueError(f"non_finite_json:{item}")),
        object_pairs_hook=unique_object,
    )


def write_json(path: Path, value) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, default=str,
                  ensure_ascii=True, allow_nan=False)
        stream.write("\n")


def utc(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def validate_hash(value: str, label: str) -> str:
    value = value.lower()
    if not HASH_RE.fullmatch(value):
        raise ValueError(f"invalid_{label}")
    return value


def validate_label(value: str) -> str:
    if not value.isascii() or not LABEL_RE.fullmatch(value):
        raise ValueError("invalid_capture_label")
    return value


def last_complete_prefix(path: Path) -> tuple[int, int]:
    extent = path.stat().st_size
    amount = min(extent, TAIL_PROBE_BYTES)
    with path.open("rb") as stream:
        stream.seek(extent - amount)
        tail = stream.read(amount)
    newline = tail.rfind(b"\n")
    if newline < 0:
        raise CaptureError("no_complete_source_line_in_bounded_tail")
    return extent - amount + newline + 1, amount


def _anchor_from_manifest(path: Path, expected_sha256: str, legacy_boundary_sha256: str | None) -> dict:
    expected_sha256 = validate_hash(expected_sha256, "anchor_manifest_sha256")
    if sha256_file(path) != expected_sha256:
        raise CaptureError("anchor_manifest_sha256_mismatch")
    manifest = read_json(path)
    if manifest.get("contract") == "incremental_live_day_capture_v1":
        evidence = manifest.get("event_evidence") or {}
        prefix_bytes = evidence.get("reconstructed_prefix_bytes")
        prefix_sha256 = evidence.get("reconstructed_prefix_sha256")
        if legacy_boundary_sha256 is None:
            raise CaptureError("legacy_anchor_requires_boundary_sha256")
        boundary_sha256 = validate_hash(legacy_boundary_sha256, "legacy_boundary_sha256")
        prior_chain = canonical_sha256({
            "contract": "verified_legacy_whole_prefix_v1",
            "manifest_sha256": expected_sha256,
            "prefix_bytes": prefix_bytes,
            "prefix_sha256": prefix_sha256,
        })
    elif manifest.get("contract") in CHAIN_CONTRACTS:
        evidence = manifest.get("event_evidence") or {}
        prefix_bytes = evidence.get("prefix_end_bytes")
        prefix_sha256 = evidence.get("inherited_whole_prefix_sha256")
        boundary_sha256 = evidence.get("prefix_boundary_sha256")
        prior_chain = evidence.get("source_chain_sha256")
        for value, label in ((boundary_sha256, "anchor_boundary_sha256"),
                             (prior_chain, "anchor_chain_sha256")):
            validate_hash(str(value), label)
    else:
        raise CaptureError("unsupported_anchor_contract")
    if type(prefix_bytes) is not int or prefix_bytes < BOUNDARY_BYTES:
        raise CaptureError("invalid_anchor_prefix_bytes")
    validate_hash(str(prefix_sha256), "anchor_prefix_sha256")
    return {
        "manifest": manifest,
        "manifest_path": path,
        "manifest_sha256": expected_sha256,
        "prefix_bytes": prefix_bytes,
        "inherited_whole_prefix_sha256": prefix_sha256,
        "boundary_sha256": boundary_sha256,
        "source_chain_sha256": prior_chain,
    }


def verify_anchor_boundary(source: Path, anchor: dict) -> None:
    start = anchor["prefix_bytes"] - BOUNDARY_BYTES
    actual = sha256_file(source, start=start, length=BOUNDARY_BYTES)
    if actual != anchor["boundary_sha256"]:
        raise CaptureError("source_anchor_boundary_mismatch")


def _prior_context(args, anchor: dict) -> dict:
    if anchor["manifest"].get("contract") in CHAIN_CONTRACTS:
        evidence = anchor["manifest"]["event_evidence"]
        name, expected = evidence.get("latest_context_file"), evidence.get("latest_context_sha256")
        if not name or Path(name).name != name:
            raise CaptureError("invalid_prior_context_reference")
        path = anchor["manifest_path"].parent / name
        if sha256_file(path) != expected:
            raise CaptureError("prior_context_sha256_mismatch")
        value = read_json(path)
    else:
        if not args.prior_context_json or not args.prior_context_sha256:
            raise CaptureError("legacy_anchor_requires_prior_context")
        path = Path(args.prior_context_json)
        expected = validate_hash(args.prior_context_sha256, "prior_context_sha256")
        if sha256_file(path) != expected:
            raise CaptureError("prior_context_sha256_mismatch")
        value = read_json(path)
    if not isinstance(value, dict):
        raise CaptureError("prior_context_must_be_object")
    return value


def _prior_raw_messages(anchor: dict, window: CaptureWindow, cutoff: datetime) -> list[dict]:
    if anchor["manifest"].get("contract") not in CHAIN_CONTRACTS:
        return []
    evidence = anchor["manifest"]["event_evidence"]
    name, expected = evidence.get("raw_messages_file"), evidence.get("raw_messages_sha256")
    if not name or Path(name).name != name:
        raise CaptureError("invalid_prior_raw_reference")
    path = anchor["manifest_path"].parent / name
    if sha256_file(path) != expected:
        raise CaptureError("prior_raw_messages_sha256_mismatch")
    value = read_json(path, max_bytes=64 * 1024 * 1024)
    if not isinstance(value, list):
        raise CaptureError("prior_raw_messages_must_be_array")
    if any(not isinstance(row, dict) or utc(row.get("ts")) is None for row in value):
        raise CaptureError("invalid_prior_raw_message")
    return [row for row in value if window.start <= utc(row["ts"]) < window.end
            and utc(row["ts"]) <= cutoff]


def _raw_missing_fields(row: dict) -> Counter:
    missing = Counter(f"missing_key:{field}" for field in RAW_FIELDS if field not in row)
    missing.update(f"null:{field}" for field in RAW_NONNULL if row.get(field) is None)
    return missing


def capture_event_delta(
    source: Path, anchor: dict, prefix_end: int, output: Path, *,
    max_rows: int, max_seconds: float, prior_context: dict,
    prior_raw: list[dict], started: float, window: CaptureWindow, cutoff: datetime,
) -> tuple[dict, dict, list[dict]]:
    delta_hash = hashlib.sha256()
    rows = 0
    prior_manifest = anchor.get("manifest", {})
    same_window = (utc(prior_manifest.get("window_start_utc")) == window.start
                   and utc(prior_manifest.get("window_end_exclusive_utc")) == window.end)
    inherited = prior_manifest.get("event_evidence", {}) if same_window else {}
    availability = inherited.get("raw_message_causal_availability", {})
    invalid_rows = list(inherited.get("invalid_delta_rows", []))[:100]
    first_ts = None
    last_ts = None
    counts = Counter()
    per_signal = defaultdict(Counter)
    context = dict(prior_context)
    raw_messages = list(prior_raw)
    raw_missing = Counter(availability.get("missing_fields", {}))
    raw_conflicts = list(availability.get("revision_conflicts", []))[:100]
    if len(raw_messages) > max_rows:
        raise CaptureError("cumulative_raw_message_budget_exceeded")
    revisions = {}

    def observe_revision(row):
        revision = row.get("message_revision_id")
        fingerprint = canonical_sha256({field: row.get(field) for field in RAW_CONTENT_FIELDS})
        previous = revisions.setdefault(revision, fingerprint)
        if previous != fingerprint and revision not in raw_conflicts and len(raw_conflicts) < 100:
            raw_conflicts.append(revision)

    prior_missing = Counter()
    for row in prior_raw:
        _budget(started, max_seconds)
        prior_missing.update(_raw_missing_fields(row))
        observe_revision(row)
    # Retained projections can expose omissions, but inherited counts already
    # include them. Keep the larger count without recounting previous evidence.
    raw_missing |= prior_missing
    with source.open("rb") as stream, output.open("xb") as raw_output:
        stream.seek(anchor["prefix_bytes"])
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_output,
                           compresslevel=6, mtime=0) as compressed:
            while stream.tell() < prefix_end:
                if rows >= max_rows:
                    raise CaptureError("event_row_budget_exceeded")
                if time.monotonic() - started > max_seconds:
                    raise CaptureError("capture_runtime_budget_exceeded")
                line_start = stream.tell()
                line = stream.readline(prefix_end - stream.tell())
                if stream.tell() > prefix_end or not line.endswith(b"\n"):
                    raise CaptureError("event_line_crossed_frozen_prefix")
                compressed.write(line)
                delta_hash.update(line)
                rows += 1
                try:
                    event = json.loads(
                        line, parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)),
                        object_pairs_hook=unique_object,
                    )
                    if not isinstance(event, dict):
                        raise ValueError("event_not_object")
                except (UnicodeError, ValueError, json.JSONDecodeError, RecursionError):
                    if len(invalid_rows) < 100:
                        invalid_rows.append({"row": rows, "source_byte": line_start})
                    continue
                stamp = utc(event.get("ts"))
                if stamp is not None:
                    first_ts = stamp if first_ts is None else min(first_ts, stamp)
                    last_ts = stamp if last_ts is None else max(last_ts, stamp)
                name = str(event.get("ev") or "missing")
                counts[name] += 1
                signal = event.get("sig")
                if isinstance(signal, str) and signal.startswith(("canal1_", "canal2_")):
                    per_signal[signal][name] += 1
                if name in CONTEXT_EVENTS:
                    context[name] = event
                if (name == "telegram_raw" and stamp is not None
                        and window.start <= stamp < window.end and stamp <= cutoff):
                    row = {field: event.get(field) for field in RAW_FIELDS}
                    raw_missing.update(_raw_missing_fields(event))
                    observe_revision(row)
                    raw_messages.append(row)
                    if len(raw_messages) > max_rows:
                        raise CaptureError("cumulative_raw_message_budget_exceeded")
    delta_bytes = prefix_end - anchor["prefix_bytes"]
    delta_sha256 = delta_hash.hexdigest()
    boundary_sha256 = sha256_file(source, start=prefix_end - BOUNDARY_BYTES,
                                  length=BOUNDARY_BYTES)
    chain = canonical_sha256({
        "contract": "append_only_source_chain_v1",
        "prior_chain_sha256": anchor["source_chain_sha256"],
        "anchor_manifest_sha256": anchor["manifest_sha256"],
        "start_byte": anchor["prefix_bytes"],
        "end_byte": prefix_end,
        "delta_sha256": delta_sha256,
        "end_boundary_sha256": boundary_sha256,
    })
    evidence = {
        "anchor_manifest_sha256": anchor["manifest_sha256"],
        "anchor_prefix_bytes": anchor["prefix_bytes"],
        "anchor_boundary_sha256": anchor["boundary_sha256"],
        "inherited_whole_prefix_sha256": anchor["inherited_whole_prefix_sha256"],
        "delta_bytes": delta_bytes,
        "delta_rows": rows,
        "delta_sha256": delta_sha256,
        "delta_gzip_sha256": sha256_file(output),
        "prefix_end_bytes": prefix_end,
        "prefix_boundary_bytes": BOUNDARY_BYTES,
        "prefix_boundary_sha256": boundary_sha256,
        "source_chain_sha256": chain,
        "source_integrity_contract": "prior_verified_whole_hash_plus_boundary_and_delta_chain",
        "full_source_rescan": False,
        "invalid_delta_rows": invalid_rows,
        "first_delta_ts": first_ts,
        "last_delta_ts": last_ts,
        "event_counts": dict(sorted(counts.items())),
        "per_signal_event_counts": {
            key: dict(sorted(value.items())) for key, value in sorted(per_signal.items())
        },
        "raw_message_causal_availability": {
            "window_rows_cumulative": len(raw_messages),
            "new_rows": len(raw_messages) - len(prior_raw),
            "missing_fields": dict(sorted(raw_missing.items())),
            "revision_conflicts": raw_conflicts,
        },
    }
    return evidence, context, raw_messages


def _git(*arguments: str) -> str:
    return subprocess.check_output(["git", "-C", str(REPO), *arguments], text=True, timeout=15).strip()


def _session_id(pid: int) -> int:
    value = ctypes.c_ulong()
    if not ctypes.windll.kernel32.ProcessIdToSessionId(pid, ctypes.byref(value)):
        raise CaptureError(f"session_lookup_failed:{pid}")
    return int(value.value)


def _terminal_inventory() -> list[dict]:
    command = (
        "$p=@(Get-Process terminal64 -ErrorAction SilentlyContinue | "
        "Select-Object Id,SessionId,StartTime,Path);$p|ConvertTo-Json -Compress"
    )
    result = subprocess.check_output(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        text=True, timeout=15,
    ).strip()
    if not result:
        return []
    value = json.loads(result)
    return value if isinstance(value, list) else [value]


def _expected_terminal(pid: int, session_id: int) -> dict:
    values = _terminal_inventory()
    if len(values) != 1:
        raise CaptureError(f"expected_one_existing_terminal:found_{len(values)}")
    value = values[0]
    if int(value["Id"]) != pid or int(value["SessionId"]) != session_id:
        raise CaptureError("existing_terminal_identity_mismatch")
    if Path(value["Path"]).resolve() != TERMINAL.resolve():
        raise CaptureError("unexpected_terminal_path")
    return value


def _runtime_state(now: datetime) -> tuple[dict, list[str]]:
    blockers = []
    try:
        heartbeat = read_json(HEARTBEAT, max_bytes=1024 * 1024)
        stamp = utc(heartbeat.get("utc"))
        if stamp is None and heartbeat.get("schema_version") == 3:
            # The live v3 writer explicitly uses datetime.utcnow without a suffix.
            raw_stamp = heartbeat.get("utc")
            if isinstance(raw_stamp, str):
                parsed = datetime.fromisoformat(raw_stamp)
                if parsed.tzinfo is None:
                    stamp = parsed.replace(tzinfo=UTC)
        age = None if stamp is None else (now - stamp).total_seconds()
        pid = heartbeat.get("pid")
        pid_exists = type(pid) is int and pid > 0
        if pid_exists:
            # Signal zero is not a read-only liveness probe on Windows.
            probe = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 f"@(Get-Process -Id {pid} -ErrorAction SilentlyContinue).Count"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            pid_exists = probe.returncode == 0 and probe.stdout.strip() == "1"
        if age is None or age < -5 or age > 120:
            blockers.append("runtime_heartbeat_stale")
        if not pid_exists:
            blockers.append("runtime_heartbeat_pid_missing")
        return {
            "heartbeat": heartbeat,
            "heartbeat_sha256": sha256_file(HEARTBEAT),
            "heartbeat_age_seconds": age,
            "heartbeat_pid_exists": pid_exists,
        }, blockers
    except Exception as exc:
        blockers.append("runtime_heartbeat_unavailable")
        return {"error_type": type(exc).__name__, "error": str(exc)}, blockers


def _clock_samples(mt5, count: int = 3) -> tuple[list[dict], int]:
    rows = []
    for index in range(count):
        for symbol in SYMBOLS:
            observed = _now()
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                raise CaptureError(f"clock_quote_unavailable:{symbol}:{mt5.last_error()}")
            bid, ask = float(tick.bid), float(tick.ask)
            if not math.isfinite(bid) or not math.isfinite(ask) or bid <= 0 or ask < bid:
                raise CaptureError(f"invalid_live_bid_ask:{symbol}")
            raw_delta = tick.time_msc / 1000 - observed.timestamp()
            offset = round(raw_delta / 3600) * 3600
            residual = raw_delta - offset
            if abs(residual) > 15:
                raise CaptureError(f"stale_or_ambiguous_clock_anchor:{symbol}")
            rows.append({
                "sample": index, "symbol": symbol, "observed_utc": observed,
                "raw_time_msc": int(tick.time_msc), "bid": bid, "ask": ask,
                "offset_seconds": int(offset), "residual_seconds": residual,
            })
        if index + 1 < count:
            time.sleep(0.2)
    offsets = {row["offset_seconds"] for row in rows}
    if len(offsets) != 1:
        raise CaptureError("inconsistent_clock_offsets")
    return rows, offsets.pop()


def _capture_ticks(mt5, pd, symbol: str, offset: int, cutoff: datetime,
                   output: Path, max_rows: int, *, window: CaptureWindow) -> dict:
    end = min(cutoff, window.end)
    if end <= window.start:
        return {"symbol": symbol, "status": "window_not_started", "rows": 0}
    lookback = timedelta(seconds=5) if symbol == "EURUSD" else timedelta(0)
    raw_start = window.start - lookback + timedelta(seconds=offset)
    raw_end = end + timedelta(seconds=offset)
    values = mt5.copy_ticks_range(symbol, raw_start, raw_end, mt5.COPY_TICKS_ALL)
    error = mt5.last_error()
    if values is None:
        raise CaptureError(f"tick_query_failed:{symbol}:{error}")
    if len(values) > max_rows:
        raise CaptureError(f"tick_row_budget_exceeded:{symbol}:{len(values)}")
    frame = pd.DataFrame(values)
    required = {"time_msc", "bid", "ask"}
    if not required.issubset(frame.columns):
        raise CaptureError(f"tick_schema_missing:{symbol}")
    frame["source_time_msc"] = frame["time_msc"].astype("int64")
    frame["time_utc"] = pd.to_datetime(
        frame["source_time_msc"] - offset * 1000, unit="ms", utc=True,
    )
    before = len(frame)
    frame = frame[(frame["time_utc"] >= pd.Timestamp(window.start - lookback)) &
                  (frame["time_utc"] < pd.Timestamp(end))].copy()
    frame.reset_index(drop=True, inplace=True)
    if frame.empty:
        raise CaptureError(f"tick_window_empty:{symbol}")
    frame.to_parquet(output, index=False, compression="zstd")
    return {
        "symbol": symbol, "status": "captured", "rows_before_filter": before,
        "rows": len(frame), "first_utc": frame["time_utc"].iloc[0].isoformat(),
        "last_utc": frame["time_utc"].iloc[-1].isoformat(),
        "duplicate_source_milliseconds": int(frame["source_time_msc"].duplicated().sum()),
        "backward_time_steps": int((frame["source_time_msc"].diff() < 0).sum()),
        "invalid_bid_ask": int((~frame["bid"].map(math.isfinite) |
                                ~frame["ask"].map(math.isfinite) |
                                (frame["bid"] <= 0) | (frame["ask"] < frame["bid"])).sum()),
        "columns": list(frame.columns), "raw_query_start": raw_start,
        "raw_query_end": raw_end, "window_end_exclusive_utc": end,
        "last_error": error, "parquet_bytes": output.stat().st_size,
        "parquet_sha256": sha256_file(output), "continuity_certified": False,
    }


def _normalize_broker_rows(rows, offset: int, fields: tuple[str, ...]) -> list[dict]:
    result = []
    for native in rows:
        row = native._asdict()
        for field in fields:
            value = row.get(field)
            target = field.removesuffix("_msc") + "_utc"
            row[target] = (
                datetime.fromtimestamp((value - offset * 1000) / 1000, tz=UTC).isoformat()
                if type(value) is int and value > 0 else None
            )
        result.append(row)
    return result


def _window_count(rows: list[dict], field: str, cutoff: datetime, window: CaptureWindow) -> int:
    return sum(
        1 for row in rows
        if (stamp := utc(row.get(field))) is not None and window.start <= stamp < min(cutoff, window.end)
    )


def _phase_guard(phase: str, now: datetime, window: CaptureWindow) -> None:
    now = strict_utc(now)
    if phase not in ("pre_window", "checkpoint", "final"):
        raise CaptureError("unknown_capture_phase")
    if phase == "pre_window" and now >= window.start:
        raise CaptureError("pre_window_capture_is_late")
    if phase == "checkpoint" and not window.start <= now < window.end:
        raise CaptureError("checkpoint_outside_window")
    if phase == "final" and now < window.end:
        raise CaptureError("final_capture_before_window_end")


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label")
    parser.add_argument("--start-utc", required=True)
    parser.add_argument("--end-utc", required=True)
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--protocol-sha256", required=True)
    parser.add_argument("--phase", required=True, choices=("pre_window", "checkpoint", "final"))
    parser.add_argument("--anchor-manifest", required=True)
    parser.add_argument("--anchor-manifest-sha256", required=True)
    parser.add_argument("--legacy-boundary-sha256")
    parser.add_argument("--prior-context-json")
    parser.add_argument("--prior-context-sha256")
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-account-identity", required=True)
    parser.add_argument("--expected-terminal-pid", required=True, type=int)
    parser.add_argument("--expected-session-id", type=int, default=2)
    parser.add_argument("--wrapper-path", required=True)
    parser.add_argument("--max-event-bytes", type=int, default=402_653_184)
    parser.add_argument("--max-event-rows", type=int, default=750_000)
    parser.add_argument("--max-tick-rows-per-symbol", type=int, default=2_000_000)
    parser.add_argument("--max-history-rows", type=int, default=100_000)
    parser.add_argument("--max-runtime-seconds", type=int, default=420)
    args = parser.parse_args(argv)
    args.window = CaptureWindow(args.start_utc, args.end_utc)
    validate_hash(args.protocol_sha256, "protocol_sha256")
    if args.label is not None:
        validate_label(args.label)
    expected_label_phase = {"pre_window": "pre", "checkpoint": "check", "final": "final"}[args.phase]
    if args.label is not None and not args.label.startswith(f"{expected_label_phase}_"):
        parser.error("label prefix does not match phase")
    args.expected_commit = args.expected_commit.lower()
    if not COMMIT_RE.fullmatch(args.expected_commit):
        parser.error("expected-commit must be a 40-character Git SHA-1")
    args.expected_account_identity = validate_hash(args.expected_account_identity, "expected_account_identity")
    if args.expected_terminal_pid <= 0 or args.expected_session_id <= 0:
        parser.error("positive existing terminal PID and interactive session are required")
    # A declared pre-window bootstrap may bridge one day of appended telemetry.
    # Normal in-window capture keeps its original budgets; defaults never grow.
    byte_cap = 1_073_741_824 if args.phase == "pre_window" else 536_870_912
    row_cap = 2_000_000 if args.phase == "pre_window" else 1_000_000
    if not 1 <= args.max_event_bytes <= byte_cap:
        parser.error(f"max-event-bytes must be in 1..{byte_cap} for {args.phase}")
    if not 1 <= args.max_event_rows <= row_cap:
        parser.error(f"max-event-rows must be in 1..{row_cap} for {args.phase}")
    if not 1 <= args.max_tick_rows_per_symbol <= 2_000_000:
        parser.error("max-tick-rows-per-symbol must be in 1..2000000")
    if not 1 <= args.max_history_rows <= 100_000:
        parser.error("max-history-rows must be in 1..100000")
    if not 30 <= args.max_runtime_seconds <= 600:
        parser.error("max-runtime-seconds must be in 30..600")
    return args


def capture(args) -> dict:
    started = time.monotonic()
    phase_observed = strict_utc(_now())
    window = CaptureWindow(args.start_utc, args.end_utc)
    _phase_guard(args.phase, phase_observed, window)
    proof, watched = _frozen_capture(args, window, phase_observed)
    phase_label = {"pre_window": "pre", "checkpoint": "check", "final": "final"}[args.phase]
    label_prefix = f"{phase_label}_{phase_observed:%Y%m%dt%H%M%Sz}_"
    label = args.label or label_prefix + uuid4().hex[:8]
    validate_label(label)
    if not label.startswith(label_prefix):
        raise CaptureError("label_does_not_match_observed_capture_time")
    if os.name != "nt":
        raise CaptureError("windows_interactive_session_required")
    if _session_id(os.getpid()) != args.expected_session_id:
        raise CaptureError("collector_not_in_expected_interactive_session")
    terminal_before = _expected_terminal(args.expected_terminal_pid, args.expected_session_id)
    anchor_path = Path(args.anchor_manifest)
    anchor = _anchor_from_manifest(
        anchor_path, args.anchor_manifest_sha256, args.legacy_boundary_sha256,
    )
    watched[anchor_path.resolve()] = anchor["manifest_sha256"]
    anchor_manifest = anchor["manifest"]
    if anchor_manifest.get("contract") in CHAIN_CONTRACTS:
        anchor_cutoff = strict_utc(anchor_manifest["event_cutoff_utc"])
        same_window = (strict_utc(anchor_manifest["window_start_utc"]) == window.start
                       and strict_utc(anchor_manifest["window_end_exclusive_utc"]) == window.end)
        if anchor_cutoff > phase_observed or (not same_window and anchor_cutoff > window.start):
            raise CaptureError("anchor_skips_requested_causal_window")
        evidence = anchor_manifest["event_evidence"]
        for kind in ("latest_context", "raw_messages"):
            name = evidence.get(f"{kind}_file")
            if not isinstance(name, str) or Path(name).name != name:
                raise CaptureError("invalid_anchor_file_reference")
            watched[(anchor_path.parent / name).resolve()] = evidence.get(f"{kind}_sha256")
    elif args.prior_context_json:
        watched[Path(args.prior_context_json).resolve()] = args.prior_context_sha256
    _verify_sources(watched)
    if JOURNAL.stat().st_size < anchor["prefix_bytes"]:
        raise CaptureError("journal_shorter_than_anchor")
    verify_anchor_boundary(JOURNAL, anchor)
    prefix_end, tail_probe = last_complete_prefix(JOURNAL)
    observed = strict_utc(_now())
    _phase_guard(args.phase, observed, window)
    if observed < phase_observed:
        raise CaptureError("capture_clock_moved_backwards")
    delta_bytes = prefix_end - anchor["prefix_bytes"]
    if delta_bytes < 0:
        raise CaptureError("journal_complete_prefix_shorter_than_anchor")
    if delta_bytes > args.max_event_bytes:
        raise CaptureError(f"event_byte_budget_exceeded:{delta_bytes}")
    head, tree = _git("rev-parse", "HEAD"), _git("rev-parse", "HEAD^{tree}")
    status = _git("status", "--porcelain")
    if head != args.expected_commit:
        raise CaptureError(f"unexpected_live_commit:{head}")
    if status:
        raise CaptureError("live_checkout_not_clean")
    wrapper = Path(args.wrapper_path)
    collector = Path(__file__).resolve()
    output = CONTROL / f"capture_simulator_{window.start:%Y%m%dt%H%M%Sz}_{window.end:%Y%m%dt%H%M%Sz}_{label}"
    archive = output.with_suffix(".zip")
    pending_archive = output.with_suffix(".zip.partial")
    if output.exists() or archive.exists() or pending_archive.exists():
        raise FileExistsError("capture_destination_exists")
    context = _prior_context(args, anchor)
    prior_raw = _prior_raw_messages(anchor, window, observed)
    _budget(started, args.max_runtime_seconds)
    output.mkdir()
    args.created_output = output
    write_json(output / "capture_started.json", {
        "contract": CONTRACT, "label": label, "phase": args.phase, **proof,
        "started_at_utc": phase_observed, "event_prefix_frozen_at_utc": observed,
        "window_start_utc": window.start, "window_end_exclusive_utc": window.end,
        "read_only": True, "natural_operations_only": True,
        "collector_sha256": sha256_file(collector), "wrapper_sha256": sha256_file(wrapper),
        "limits": {
            "event_bytes": args.max_event_bytes, "event_rows": args.max_event_rows,
            "tick_rows_per_symbol": args.max_tick_rows_per_symbol,
            "history_rows": args.max_history_rows,
            "runtime_seconds": args.max_runtime_seconds,
        },
    })
    event_evidence, context, raw_messages = capture_event_delta(
        JOURNAL, anchor, prefix_end, output / "event_delta.jsonl.gz",
        max_rows=args.max_event_rows, max_seconds=args.max_runtime_seconds,
        prior_context=context, prior_raw=prior_raw, started=started,
        window=window, cutoff=observed,
    )
    write_json(output / "prior_context.json", context)
    write_json(output / "raw_messages_window.json", raw_messages)
    event_evidence.update({
        "latest_context_file": "prior_context.json",
        "latest_context_sha256": sha256_file(output / "prior_context.json"),
        "raw_messages_file": "raw_messages_window.json",
        "raw_messages_sha256": sha256_file(output / "raw_messages_window.json"),
        "tail_probe_bytes_read": tail_probe,
        "source_bytes_read_for_events": delta_bytes,
    })
    live_contract = context.get("live_strategy_contract")
    blockers = []
    if not isinstance(live_contract, dict):
        blockers.append("live_strategy_contract_unavailable")
        live_contract_sha256 = None
    else:
        live_contract_sha256 = canonical_sha256(live_contract)
    runtime_state, runtime_blockers = _runtime_state(_now())
    blockers.extend(runtime_blockers)
    mt5 = importlib.import_module("MetaTrader5")
    pd = importlib.import_module("pandas")
    _budget(started, args.max_runtime_seconds)
    _verify_sources(watched)
    if _expected_terminal(args.expected_terminal_pid, args.expected_session_id) != terminal_before:
        raise CaptureError("terminal_changed_before_initialize")
    if _git("rev-parse", "HEAD") != head or _git("status", "--porcelain"):
        raise CaptureError("live_code_changed_before_initialize")
    try:
        if not mt5.initialize(str(TERMINAL), timeout=15000):
            raise CaptureError(f"mt5_initialize_failed:{mt5.last_error()}")
        _budget(started, args.max_runtime_seconds)
        if _expected_terminal(args.expected_terminal_pid, args.expected_session_id) != terminal_before:
            raise CaptureError("terminal_changed_during_initialize")
        account, terminal = mt5.account_info(), mt5.terminal_info()
        if not account or not terminal or not terminal.connected:
            raise CaptureError("terminal_or_account_unavailable")
        identity = hashlib.sha256(f"{account.server}:{account.login}".encode()).hexdigest()
        if identity != args.expected_account_identity:
            raise CaptureError("unexpected_capture_account")
        if account.currency != "EUR" or account.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
            raise CaptureError("unexpected_account_contract")
        clock_samples, offset = _clock_samples(mt5)
        metadata = {}
        symbol_contract = {}
        for symbol in SYMBOLS:
            info = mt5.symbol_info(symbol)
            if info is None:
                raise CaptureError(f"symbol_unavailable:{symbol}")
            metadata[symbol] = info._asdict()
            symbol_contract[symbol] = {field: getattr(info, field, None) for field in SYMBOL_CONTRACT_FIELDS}
        write_json(output / "symbol_metadata.json", metadata)
        positions_before = mt5.positions_get()
        pending_before = mt5.orders_get()
        if positions_before is None or pending_before is None:
            raise CaptureError("broker_snapshot_before_unavailable")
        write_json(output / "positions_and_pending_before.json", {
            "observed_at_utc": _now(),
            "positions": [row._asdict() for row in positions_before],
            "pending_orders": [row._asdict() for row in pending_before],
        })
        ticks = {}
        for symbol in SYMBOLS:
            _budget(started, args.max_runtime_seconds)
            path = output / f"{symbol}.parquet"
            ticks[symbol] = _capture_ticks(
                mt5, pd, symbol, offset, observed, path, args.max_tick_rows_per_symbol,
                window=window,
            )
            if ticks[symbol].get("backward_time_steps") or ticks[symbol].get("invalid_bid_ask"):
                blockers.append(f"invalid_captured_tick_order_or_quotes:{symbol}")
        _budget(started, args.max_runtime_seconds)
        effective_end = min(max(observed, window.start), window.end)
        history_from = window.start - timedelta(hours=6)
        history_to = effective_end + timedelta(hours=6)
        native_deals = mt5.history_deals_get(history_from, history_to)
        native_orders = mt5.history_orders_get(history_from, history_to)
        if native_deals is None or native_orders is None:
            raise CaptureError(f"broker_history_unavailable:{mt5.last_error()}")
        if len(native_deals) > args.max_history_rows or len(native_orders) > args.max_history_rows:
            raise CaptureError("broker_history_row_budget_exceeded")
        deals = _normalize_broker_rows(native_deals, offset, ("time_msc",))
        orders = _normalize_broker_rows(native_orders, offset, ("time_setup_msc", "time_done_msc"))
        write_json(output / "broker_deals.json", {
            "query_from": history_from, "query_to": history_to,
            "captured_at_utc": _now(), "rows": deals,
        })
        write_json(output / "broker_orders.json", {
            "query_from": history_from, "query_to": history_to,
            "captured_at_utc": _now(), "rows": orders,
        })
        positions_after = mt5.positions_get()
        pending_after = mt5.orders_get()
        if positions_after is None or pending_after is None:
            raise CaptureError("broker_snapshot_after_unavailable")
        write_json(output / "positions_and_pending_after.json", {
            "observed_at_utc": _now(),
            "positions": [row._asdict() for row in positions_after],
            "pending_orders": [row._asdict() for row in pending_after],
        })
        broker = {
            "query_from": history_from, "query_to": history_to,
            "deal_rows": len(deals), "order_rows": len(orders),
            "in_window_deal_rows": _window_count(deals, "time_utc", observed, window),
            "in_window_order_setup_rows": _window_count(orders, "time_setup_utc", observed, window),
            "in_window_order_done_rows": _window_count(orders, "time_done_utc", observed, window),
            "positions_before": len(positions_before), "positions_after": len(positions_after),
            "pending_before": len(pending_before), "pending_after": len(pending_after),
            "flat_required": False,
        }
        account_evidence = {
            "account_identity_sha256": identity, "currency": account.currency,
            "trade_mode": int(account.trade_mode), "terminal_connected": bool(terminal.connected),
        }
    finally:
        mt5.shutdown()
    terminal_after = _expected_terminal(args.expected_terminal_pid, args.expected_session_id)
    if terminal_after != terminal_before:
        raise CaptureError("terminal_changed_during_capture")
    if _git("rev-parse", "HEAD") != head or _git("status", "--porcelain"):
        raise CaptureError("live_code_changed_during_capture")
    _budget(started, args.max_runtime_seconds)
    _verify_sources(watched)
    verify_anchor_boundary(JOURNAL, anchor)
    if sha256_file(JOURNAL, start=anchor["prefix_bytes"], length=delta_bytes) != event_evidence["delta_sha256"]:
        raise CaptureError("source_delta_changed_during_capture")
    verify_anchor_boundary(JOURNAL, {**anchor, "prefix_bytes": prefix_end,
                                    "boundary_sha256": event_evidence["prefix_boundary_sha256"]})
    files = {}
    for path in sorted(output.iterdir()):
        if path.is_file():
            files[path.name] = {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
    code_contract = {
        "commit": head, "tree": tree, "live_strategy_contract_sha256": live_contract_sha256,
        "collector_sha256": sha256_file(collector), "wrapper_sha256": sha256_file(wrapper),
    }
    completed = strict_utc(_now())
    if completed < observed:
        raise CaptureError("capture_clock_moved_backwards")
    manifest = {
        "contract": CONTRACT, "label": label, "phase": args.phase, **proof,
        "source_hashes_verified_before_after": True,
        "status": "captured_with_blockers" if blockers else "captured",
        "blockers": sorted(set(blockers)), "completed_at_utc": completed,
        "window_start_utc": window.start, "window_end_exclusive_utc": window.end,
        "event_cutoff_utc": observed, "event_evidence": event_evidence,
        "live_code": {"commit": head, "tree": tree, "clean": True,
                      "contract_sha256": canonical_sha256(code_contract), **code_contract},
        "runtime_state": runtime_state, "account": account_evidence,
        "clock_samples": clock_samples, "raw_server_epoch_minus_utc_seconds": offset,
        "symbol_contract": symbol_contract, "tick_evidence": ticks,
        "broker_evidence": broker, "terminal_before": terminal_before,
        "terminal_after": terminal_after, "files": files,
        "read_only": True, "normal_operations_only": True, "orders_sent_by_collector": 0,
        "full_live_parity_verified": False, "tick_continuity_certified": False,
        "source_prefix_rehashed_in_full": False,
        "elapsed_seconds": time.monotonic() - started,
    }
    manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2, default=str,
                                 ensure_ascii=True, allow_nan=False) + "\n").encode()
    with zipfile.ZipFile(pending_archive, "x", compression=zipfile.ZIP_STORED, allowZip64=True) as bundle:
        for path in sorted(output.iterdir()):
            _budget(started, args.max_runtime_seconds)
            if path.is_file():
                bundle.write(path, path.name)
        _verify_sources(watched)
        _budget(started, args.max_runtime_seconds)
        bundle.writestr("manifest.json", manifest_bytes)
    archive_sha = sha256_file(pending_archive)
    _verify_sources(watched)
    _budget(started, args.max_runtime_seconds)
    # A failed collection may retain diagnostics, but never an admissible manifest.
    pending_archive.rename(archive)
    with (output / "manifest.json").open("xb") as stream:
        stream.write(manifest_bytes)
    result = {
        "status": manifest["status"], "blockers": manifest["blockers"],
        "output": str(output), "manifest_sha256": sha256_file(output / "manifest.json"),
        "archive": str(archive), "archive_sha256": archive_sha,
        "event_cutoff_utc": observed.isoformat(), "event_delta_bytes": delta_bytes,
    }
    print(json.dumps(result, sort_keys=True), flush=True)
    return result


def main(argv=None) -> int:
    args = _parse_args(argv)
    try:
        result = capture(args)
        return 0 if result["status"] == "captured" else 2
    except Exception as exc:
        failure = {
            "status": "capture_failed", "failed_at_utc": _now(),
            "type": type(exc).__name__, "message": str(exc),
            "read_only": True, "orders_sent_by_collector": 0,
        }
        output = getattr(args, "created_output", None)
        if output is not None:
            path = output / "failure.json"
            if not path.exists():
                write_json(path, failure)
        print(json.dumps(failure, default=str, sort_keys=True), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
