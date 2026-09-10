"""Separate Telegram revision content from transport redeliveries.

This diagnostic preserves every observation and cannot admit a frozen cohort
or replace the full lineage, media, capture-window or execution checks.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.audit_causal_lineage import _has_raw_message_evidence, _parse_contract_utc_ts
from tools.audit_management_capture import _reject_constant, _unique_object


CONTENT_FIELDS = (
    "channel", "chat_id", "message_id", "revision_token", "message_revision_id",
    "date_utc", "edit_date_utc", "text", "text_sha1", "has_text", "has_media",
    "sticker_id", "has_photo", "has_document", "reply_to_msg_id", "is_reply", "media_sha256",
)
OBSERVATION_FIELDS = ("ts", "update_kind", "is_edit")


def _digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def audit_rows(rows):
    observations, revisions, conflicts, invalid = [], {}, [], []
    redeliveries = 0
    source_count = 0
    for index, row in enumerate(rows, 1):
        source_count = index
        if not isinstance(row, dict):
            raise ValueError(f"source row {index} is not an object")
        if row.get("ev") != "telegram_raw":
            continue
        missing = sorted(set((*CONTENT_FIELDS, *OBSERVATION_FIELDS)) - row.keys())
        errors = [f"missing:{field}" for field in missing]
        if not _has_raw_message_evidence(row):
            errors.append("invalid_revision_or_transport_evidence")
        observed = _parse_contract_utc_ts(row.get("ts"))
        published = _parse_contract_utc_ts(row.get("date_utc"))
        edited = _parse_contract_utc_ts(row.get("edit_date_utc"))
        if observed is None or published is None or (row.get("edit_date_utc") is not None and edited is None):
            errors.append("invalid_causal_clock")
        elif published > observed or (edited is not None and edited > observed):
            errors.append("revision_not_yet_available")
        content = {key: row.get(key) for key in CONTENT_FIELDS}
        revision = row.get("message_revision_id")
        if not isinstance(revision, str) or not revision:
            errors.append("invalid_revision_identity")
        observation = {
            "source_row": index, "message_revision_id": revision,
            "observed_at_utc": row.get("ts"), "update_kind": row.get("update_kind"),
            "is_edit": row.get("is_edit"), "event_id": row.get("event_id"),
            "observation_sha256": _digest(row), "content_sha256": _digest(content),
            "errors": errors,
        }
        observations.append(observation)
        if errors:
            invalid.append({"source_row": index, "message_revision_id": revision, "reasons": errors})
        if not isinstance(revision, str) or not revision:
            continue
        previous = revisions.get(revision)
        if previous is None:
            revisions[revision] = {
                "message_revision_id": revision, "channel": row.get("channel"),
                "message_id": row.get("message_id"), "content": content,
                "observation_rows": [index], "first_observed_at_utc": row.get("ts"),
                "first_observation_valid": not errors,
            }
            continue
        previous["observation_rows"].append(index)
        differences = [field for field in CONTENT_FIELDS if content[field] != previous["content"][field]]
        if differences:
            conflicts.append({
                "message_revision_id": revision, "first_source_row": previous["observation_rows"][0],
                "conflicting_source_row": index, "changed_content_fields": differences,
            })
        elif not errors and previous["first_observation_valid"]:
            redeliveries += 1
        first = _parse_contract_utc_ts(previous["first_observed_at_utc"])
        if observed is not None and first is not None and observed < first:
            previous["first_observed_at_utc"] = row["ts"]
    return {
        "contract": "telegram_raw_observation_audit_v1",
        "status": "blocked" if conflicts or invalid else "raw_observations_consistent",
        "full_causal_or_prospective_validation": False,
        "summary": {"source_rows": source_count, "raw_observations": len(observations),
                    "unique_revisions": len(revisions), "compatible_redeliveries": redeliveries,
                    "content_conflicts": len(conflicts), "invalid_observations": len(invalid)},
        "content_fields": list(CONTENT_FIELDS), "transport_fields": list(OBSERVATION_FIELDS),
        "observations": observations, "revisions": list(revisions.values()),
        "content_conflicts": conflicts, "invalid_observations": invalid,
    }


def audit_file(path, *, max_bytes=268435456, max_rows=750000):
    path = Path(path)
    if max_bytes <= 0 or max_rows <= 0 or path.stat().st_size > max_bytes:
        raise ValueError("raw observation input budget exceeded")

    def file_hash(source):
        with Path(source).open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    source_hash = file_hash(path)
    root = Path(__file__).resolve().parents[1]
    sources = ("tools/audit_raw_observations.py", "tools/audit_causal_lineage.py", "tools/audit_management_capture.py")
    code_before = {name: file_hash(root / name) for name in sources}
    decompressed = hashlib.sha256()

    def rows():
        total = 0
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rb") as stream:
            index = 0
            while line := stream.readline(max_bytes - total + 1):
                index += 1
                total += len(line)
                if total > max_bytes or index > max_rows:
                    raise ValueError("raw observation input budget exceeded")
                if not line.endswith(b"\n"):
                    raise ValueError("incomplete source row")
                decompressed.update(line)
                yield json.loads(line, parse_constant=_reject_constant, object_pairs_hook=_unique_object)

    report = audit_rows(rows())
    if file_hash(path) != source_hash:
        raise ValueError("source changed during audit")
    if {name: file_hash(root / name) for name in sources} != code_before:
        raise ValueError("audit code changed during execution")
    report["source"] = {"path": str(path.resolve()), "sha256": source_hash,
                        "decompressed_sha256": decompressed.hexdigest()}
    report["code"] = code_before
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise FileExistsError("diagnostic outputs are immutable")
    report = audit_file(args.events)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"status": report["status"], **report["summary"]}))
    return 0 if report["status"] == "raw_observations_consistent" else 2


if __name__ == "__main__":
    raise SystemExit(main())
