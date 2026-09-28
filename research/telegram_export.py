"""Lossless offline inventory and explicit temporal experiments for exports.

Admission here means a textual trigger, not a certified engine dataset. No
receipt is synthesized. The engine bridge uses an explicitly selected scenario
clock in its observed_at slot, never an asserted historical receipt.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import re

import parser as signal_parser


SCHEMA_VERSION = "telegram_export_admission_v1"
CHATS = {1642806869: ("dubai_historical", "canal1"),
         3828356530: ("gold_historical", "canal2")}
SCENARIOS = {
    "receipt": "Observed reception required; exports do not contain this evidence.",
    "publication_initial": "Hypothesis: a snapshot without an edit marker is available at publication; zero transport latency.",
    "revision_time": "Hypothesis: exported content is available at its last edit timestamp, or publication if unedited; zero transport latency. This is NOT the initial entry replay.",
}


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False,
                       separators=(",", ":")) + "\n").encode("utf-8")


def _hash(value):
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _utc(value):
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("period timestamps must include an explicit UTC offset")
    return parsed.astimezone(timezone.utc)


def _iso(value):
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def _epoch(value):
    # Telegram's naive display dates do not identify a timezone. Only the
    # structured Unix field establishes UTC; filenames and mtimes are not clocks.
    try:
        if type(value) is int:
            number = value
        elif isinstance(value, str) and re.fullmatch(r"-?\d+", value):
            number = int(value)
        else:
            return None
        return datetime.fromtimestamp(number, timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def _period(timestamp, start, end):
    if timestamp is None:
        return "unknown"
    return "inside" if start <= timestamp < end else "outside"


def _text(value):
    if isinstance(value, str):
        return value, None
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            else:
                return None, "malformed_text"
        return "".join(parts), None
    return None, "malformed_text"


def _classify(raw, text):
    if raw.get("type") != "message":
        return None, "service_or_unknown_message_type"
    if text is None:
        return None, "malformed_text"
    directions = set(re.findall(r"\b(?:BUY|SELL)\b", text.upper()))
    if len(directions) > 1:
        return None, "ambiguous_direction"
    if raw.get("media_type") == "sticker":
        return None, "unknown_sticker_direction"
    if signal_parser.is_canal2_entry(text):
        return signal_parser.parse_canal2(text).get("direction"), "explicit_text_now"
    if re.search(r"\bZONE\b", text, re.I) and directions:
        return None, "zone_plan_not_immediate"
    if directions:
        return None, "unresolved_directional_text"
    if not text.strip() and any(raw.get(key) for key in ("media_type", "file", "photo")):
        return None, "media_without_text"
    return None, "no_explicit_entry"


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f"non-JSON numeric constant: {value}")


def _counts(values):
    return dict(sorted(Counter(values).items()))


def _summarize(identities):
    scenarios = {}
    for scenario in SCENARIOS:
        admitted = sum(item["admission"][scenario]["status"] == "admitted" for item in identities)
        candidates = [item for item in identities if item["has_recognized_text_trigger"]]
        scenarios[scenario] = {
            "admitted": admitted, "blocked": len(identities) - admitted,
            "recognized_candidate_identities": len(candidates),
            "blocked_recognized_candidates": sum(item["admission"][scenario]["status"] != "admitted" for item in candidates),
            "reason_counts": _counts(reason for item in identities for reason in item["admission"][scenario]["reasons"]),
            "by_period": {period: {
                "admitted": sum(item["period"] == period and item["admission"][scenario]["status"] == "admitted" for item in identities),
                "blocked": sum(item["period"] == period and item["admission"][scenario]["status"] == "blocked" for item in identities),
                "recognized_candidates": sum(item["period"] == period for item in candidates),
            } for period in ("inside", "outside", "unknown")},
        }
    return {"identities": len(identities), "period_counts": {
        key: sum(item["period"] == key for item in identities)
        for key in ("inside", "outside", "unknown")},
        "recognized_candidate_identities": sum(item["has_recognized_text_trigger"] for item in identities),
        "scenarios": scenarios}


def _admit(identity, start, end):
    revisions = identity["revisions"]
    shared = set(reason for revision in revisions for reason in revision["evidence_issues"])
    if identity["chat_id"] not in CHATS:
        shared.add("unsupported_chat_id")
    if identity["message_id"] is None:
        shared.add("message_identity_missing")
    publications = {revision["published_utc"] for revision in revisions}
    if len(publications) > 1:
        shared.add("publication_time_conflict")
    clocks = defaultdict(set)
    for revision in revisions:
        clocks[(revision["published_utc"], revision["edited_utc"], revision["has_edit_marker"])].add(revision["content_sha256"])
    if any(len(contents) > 1 for contents in clocks.values()):
        shared.add("revision_content_conflict")
    identity["integrity_issues"] = sorted(shared)
    identity["admission"] = {}
    triggers = {}
    for scenario in SCENARIOS:
        reasons = set(shared)
        eligible = [row for row in revisions if row["direction"] in ("BUY", "SELL")]
        if not eligible:
            reasons.update(row["classification"] for row in revisions)
        if scenario == "receipt":
            reasons.add("receipt_unknown")
        elif scenario == "publication_initial":
            eligible = [row for row in eligible if not row["has_edit_marker"]]
            if not any(not row["has_edit_marker"] for row in revisions):
                reasons.add("initial_version_missing")
            elif not eligible:
                reasons.add("no_initial_text_trigger")
        eligible.sort(key=lambda row: (row["revision_clock_utc"] or "", row["revision_id"]))
        selected = eligible[0] if eligible else None
        if identity["period"] == "outside":
            reasons.add("publication_outside_period")
        if selected and selected["revision_clock_utc"]:
            trigger = _utc(selected["revision_clock_utc"])
            if not start <= trigger < end:
                reasons.add("trigger_outside_period")
        elif selected:
            reasons.add("trigger_time_unknown")
        admitted = selected is not None and not reasons
        identity["admission"][scenario] = {"status": "admitted" if admitted else "blocked",
            "reasons": sorted(reasons), "selected_revision_id": selected["revision_id"] if selected else None}
        if admitted:
            triggers[scenario] = {
                "signal_id": identity["identity"], "chat_id": identity["chat_id"],
                "cohort": identity["cohort"], "channel": CHATS[identity["chat_id"]][1],
                "message_id": identity["message_id"], "direction": selected["direction"],
                "published_utc": selected["published_utc"],
                "trigger_utc": selected["revision_clock_utc"], "received_utc": None,
                "scenario": scenario, "clock_is_hypothesis": True,
                "initial_content_known": not selected["has_edit_marker"],
                "message_revision_id": selected["revision_id"],
                "content_sha256": selected["content_sha256"],
                "occurrence_ids": selected["occurrence_ids"],
                "source_ids": selected["source_ids"], "provider_events": [],
            }
    return triggers


def prepare_exports(sources, *, start, end):
    """Inventory every source occurrence, identity and known export revision.

    The half-open period filters admission, never inventory. Source list order
    is diagnostic and preserved; duplicate identities are channel-bound. Media
    descriptors are not media-byte evidence and never identify BUY/SELL.
    """
    start, end = _utc(start), _utc(end)
    if start >= end:
        raise ValueError("end must be after start")
    paths = [Path(path).resolve() for path in sources]
    if not paths or len(set(paths)) != len(paths):
        raise ValueError("provide nonempty, distinct source paths")
    inventories, occurrences, identities = [], [], {}
    for source_index, path in enumerate(paths):
        data = path.read_bytes()
        payload = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_unique_object,
                             parse_constant=_invalid_constant)
        if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
            raise ValueError(f"export must contain a messages list: {path}")
        chat_id = payload.get("id")
        if type(chat_id) is not int or chat_id <= 0:
            raise ValueError(f"export must identify its chat with a positive integer: {path}")
        cohort = CHATS.get(chat_id, ("unsupported_chat", None))[0]
        digest = hashlib.sha256(data).hexdigest()
        source_id = f"source_{source_index}:{digest}"
        inventory = {"source_id": source_id, "path": str(path), "sha256": digest,
            "bytes": len(data), "chat_id": chat_id, "cohort": cohort,
            "header": {key: value for key, value in payload.items() if key != "messages"},
            "exported_at_utc": None, "message_ids": [], "occurrence_ids": [],
            "period_counts": {"inside": 0, "outside": 0, "unknown": 0}}
        published_times = []
        for index, raw_value in enumerate(payload["messages"]):
            raw = raw_value if isinstance(raw_value, dict) else {}
            occurrence_id = f"{source_id}:{index}"
            message_id = raw.get("id")
            valid_id = type(message_id) is int and message_id > 0
            identity_key = f"telegram_export:{chat_id}:{message_id}" if valid_id else f"missing_id:{occurrence_id}"
            published = _epoch(raw.get("date_unixtime"))
            has_edit = any(raw.get(key) not in (None, "") for key in ("edited", "edited_unixtime"))
            edited = _epoch(raw.get("edited_unixtime")) if has_edit else None
            text, text_issue = _text(raw.get("text"))
            direction, classification = _classify(raw, text)
            issues = []
            if published is None:
                issues.append("publication_time_unknown")
            if has_edit and edited is None:
                issues.append("edit_time_unknown")
            if edited is not None and published is not None and edited < published:
                issues.append("edit_before_publication")
            if text_issue:
                issues.append(text_issue)
            if not isinstance(raw_value, dict):
                issues.append("malformed_message")
            period = _period(published, start, end)
            inventory["period_counts"][period] += 1
            inventory["message_ids"].append(raw.get("id"))
            inventory["occurrence_ids"].append(occurrence_id)
            if published:
                published_times.append(_iso(published))
            # Export-local file names/reaction counters do not establish a new
            # textual revision. Full raw snapshots below still retain them.
            content = {"text": text, "type": raw.get("type"),
                "media": {key: raw.get(key) for key in ("media_type", "mime_type", "file_size", "photo_file_size", "sticker_emoji")},
                "reply_to_message_id": raw.get("reply_to_message_id")}
            content_hash = _hash(content)
            revision_hash = _hash({"identity": identity_key, "content_sha256": content_hash,
                "published_utc": _iso(published), "edited_utc": _iso(edited), "has_edit_marker": has_edit})
            revision_id = f"export_revision:{revision_hash}"
            snapshot_hash = _hash(raw_value)
            occurrence = {"occurrence_id": occurrence_id, "source_id": source_id,
                "source_index": index, "identity": identity_key, "chat_id": chat_id,
                "message_id": raw.get("id"), "revision_id": revision_id,
                "raw_snapshot_sha256": snapshot_hash, "period": period,
                "raw": raw_value}
            occurrences.append(occurrence)
            identity = identities.setdefault(identity_key, {"identity": identity_key,
                "chat_id": chat_id, "cohort": cohort, "message_id": message_id if valid_id else None,
                "source_ids": [], "occurrence_ids": [], "raw_snapshot_hashes": [], "_revisions": {}})
            identity["source_ids"].append(source_id)
            identity["occurrence_ids"].append(occurrence_id)
            identity["raw_snapshot_hashes"].append(snapshot_hash)
            revision = identity["_revisions"].setdefault(revision_id, {
                "revision_id": revision_id, "content_sha256": content_hash,
                "text_sha256": _hash(text), "text": text, "classification": classification,
                "direction": direction, "published_utc": _iso(published), "edited_utc": _iso(edited),
                "has_edit_marker": has_edit, "revision_clock_utc": _iso(edited if has_edit else published),
                "received_utc": None, "observed_content_at_utc": None,
                "period": period, "evidence_issues": [], "occurrence_ids": [], "source_ids": []})
            revision["evidence_issues"] = sorted(set(revision["evidence_issues"] + issues))
            revision["occurrence_ids"].append(occurrence_id)
            revision["source_ids"] = sorted(set(revision["source_ids"] + [source_id]))
        inventory.update({"messages": len(payload["messages"]),
            "unique_valid_message_ids": len({value for value in inventory["message_ids"] if type(value) is int and value > 0}),
            "published_min_utc": min(published_times, default=None),
            "published_max_utc": max(published_times, default=None)})
        inventories.append(inventory)
    rows = list(identities.values())
    triggers = {scenario: [] for scenario in SCENARIOS}
    for identity in rows:
        identity["revisions"] = sorted(identity.pop("_revisions").values(),
            key=lambda row: (row["revision_clock_utc"] or "", row["revision_id"]))
        for key in ("source_ids", "raw_snapshot_hashes"):
            identity[key] = sorted(set(identity[key]))
        periods = {revision["period"] for revision in identity["revisions"]}
        identity["period"] = next(iter(periods)) if len(periods) == 1 else "unknown"
        identity["has_recognized_text_trigger"] = any(row["direction"] in ("BUY", "SELL") for row in identity["revisions"])
        identity["distinct_text_contents"] = len({row["text_sha256"] for row in identity["revisions"]})
        for scenario, trigger in _admit(identity, start, end).items():
            triggers[scenario].append(trigger)
    for trigger_rows in triggers.values():
        trigger_rows.sort(key=lambda row: (row["trigger_utc"], row["signal_id"]))
    text_groups = defaultdict(set)
    for row in rows:
        for revision in row["revisions"]:
            if revision["text"] and revision["text"].strip():
                text_groups[(row["chat_id"], revision["text_sha256"])].add(row["identity"])
    text_duplicate_groups = [{"chat_id": chat, "text_sha256": digest, "identity_ids": sorted(group)}
        for (chat, digest), group in sorted(text_groups.items()) if len(group) > 1]
    summary = _summarize(rows)
    summary.update({"source_occurrences": len(occurrences), "sources": len(inventories),
        "duplicate_identity_occurrences": len(occurrences) - len(rows),
        "duplicate_revision_occurrences": len(occurrences) - sum(len(row["revisions"]) for row in rows),
        "duplicate_raw_snapshot_occurrences": len(occurrences) - sum(len(row["raw_snapshot_hashes"]) for row in rows),
        "text_duplicate_groups_across_distinct_ids": len(text_duplicate_groups),
        "known_export_revisions": sum(len(row["revisions"]) for row in rows),
        "identities_with_multiple_revisions": sum(len(row["revisions"]) > 1 for row in rows),
        "identities_with_multiple_text_contents": sum(row["distinct_text_contents"] > 1 for row in rows),
        "classification_counts_by_revision": _counts(revision["classification"] for row in rows for revision in row["revisions"]),
        "by_chat": {str(chat): _summarize([row for row in rows if row["chat_id"] == chat])
                    for chat in sorted({row["chat_id"] for row in rows})}})
    for inventory in inventories:
        inventory["union_identity_admission"] = _summarize([row for row in rows if inventory["source_id"] in row["source_ids"]])
        inventory["selected_trigger_counts"] = {scenario: sum(inventory["source_id"] in row["source_ids"] for row in trigger_rows)
                                                 for scenario, trigger_rows in triggers.items()}
    contract = {"schema_version": SCHEMA_VERSION, "start_utc": _iso(start), "end_exclusive_utc": _iso(end),
        "timezone": "UTC", "timestamp_precision": "seconds", "scenarios": SCENARIOS, "trigger_grammar": "existing parser.is_canal2_entry, both confirmed text cohorts; conflicting direction tokens blocked",
        "provider_management_used": False, "sticker_directions_inferred": False,
        "receipt_known": False, "export_time_known": False, "ticks_certified": False,
        "strategy_certified": False, "untouched_oos_established": False,
        "engine_dataset_ready": False,
        "open_gates": ["scenario_choice", "historical_bid_ask_and_coverage", "execution_assumptions", "money_and_cost_contract", "study_cohorts_and_prior_use"],
        "revision_semantics": "Known export snapshots, not a complete edit history. Raw snapshot hashes differ from normalized text/media-descriptor revision hashes. Media bytes are not verified.",
        "denominator": "All source rows and all channel-bound identities retained, including service, unresolved, malformed, outside period and blocked. Reasons overlap.",
        "conflict_policy": "Retrospective fail-closed audit of every known revision; not an online classifier using future text."}
    implementation = {"python": platform.python_version(), "files": {str(path.name): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (Path(__file__), Path(signal_parser.__file__))}}
    return {"contract": contract, "implementation": implementation, "sources": inventories,
            "occurrences": occurrences, "identities": rows, "triggers": triggers, "summary": summary,
            "text_duplicate_groups": text_duplicate_groups}


def write_admission(bundle, output_dir):
    """Write a deterministic archive; identical reruns are allowed, conflicts fail."""
    output = Path(output_dir).resolve()
    payloads = {"summary.json": _json_bytes(bundle["summary"]),
                "sources.json": _json_bytes(bundle["sources"])}
    for key in ("occurrences", "identities", "text_duplicate_groups"):
        payloads[f"{key}.jsonl"] = b"".join(_json_bytes(row) for row in bundle[key])
    for scenario, rows in bundle["triggers"].items():
        payloads[f"triggers_{scenario}.jsonl"] = b"".join(_json_bytes(row) for row in rows)
    manifest = {"contract": bundle["contract"], "implementation": bundle["implementation"],
        "sources": [{key: source[key] for key in ("source_id", "path", "sha256", "bytes", "chat_id")}
                    for source in bundle["sources"]],
        "artifacts": {name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
                      for name, data in payloads.items()}}
    manifest["archive_identity_sha256"] = _hash(manifest)
    payloads["manifest.json"] = _json_bytes(manifest)
    source_paths = {Path(source["path"]).resolve() for source in bundle["sources"]}
    for name, data in payloads.items():
        target = output / name
        if target.resolve() in source_paths:
            raise ValueError("output would overwrite a read-only source")
        if target.exists() and target.read_bytes() != data:
            raise ValueError(f"immutable archive conflict: {target}")
    output.mkdir(parents=True, exist_ok=True)
    # Manifest is written last, so an interrupted partial archive is not complete.
    for name, data in payloads.items():
        target = output / name
        if not target.exists():
            with target.open("xb") as stream:
                stream.write(data)
    return manifest


def to_causal_signals(bundle, *, scenario, chat_id):
    """Bridge one named historical cohort to make_path's existing signal type.

    observed_at is the scenario clock, NOT an observed receipt. Keep the bundle
    manifest with any engine run. Market, FX and money gates are still external.
    """
    if scenario not in SCENARIOS or chat_id not in CHATS:
        raise ValueError("select an explicit supported scenario and historical chat")
    if bundle["contract"]["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported admission schema")
    from research.causal_replay import CausalSignal

    return tuple(CausalSignal(row["signal_id"], row["channel"], row["direction"],
        _utc(row["trigger_utc"]), _utc(row["published_utc"]), row["message_revision_id"], ())
        for row in bundle["triggers"][scenario] if row["chat_id"] == chat_id)


def load_admission(output_dir):
    """Verify retained artifact bytes before restoring a preparation bundle.

    Source hashes refer to bytes read at preparation, not a new verification of
    the user's read-only export directory. The complete raw row inventory stays
    in this archive even when the original directory is unavailable later.
    """
    output = Path(output_dir)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"),
                          object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    if not isinstance(manifest, dict) or manifest.get("contract", {}).get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported admission manifest")
    identity = manifest.get("archive_identity_sha256")
    if identity != _hash({key: value for key, value in manifest.items() if key != "archive_identity_sha256"}):
        raise ValueError("manifest identity hash mismatch")
    expected_names = {"summary.json", "sources.json", "occurrences.jsonl", "identities.jsonl", "text_duplicate_groups.jsonl"}
    expected_names.update(f"triggers_{scenario}.jsonl" for scenario in SCENARIOS)
    if set(manifest.get("artifacts", {})) != expected_names:
        raise ValueError("manifest artifact set mismatch")
    restored = {}
    for name, expected in manifest["artifacts"].items():
        data = (output / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected["sha256"] or len(data) != expected["bytes"]:
            raise ValueError(f"artifact hash mismatch: {name}")
        texts = data.decode("utf-8").splitlines() if name.endswith(".jsonl") else [data.decode("utf-8")]
        decoded = [json.loads(text, object_pairs_hook=_unique_object, parse_constant=_invalid_constant) for text in texts]
        restored[name] = decoded if name.endswith(".jsonl") else decoded[0]
    return {"contract": manifest["contract"], "implementation": manifest["implementation"],
        "sources": restored["sources.json"], "summary": restored["summary.json"],
        "occurrences": restored["occurrences.jsonl"], "identities": restored["identities.jsonl"],
        "text_duplicate_groups": restored["text_duplicate_groups.jsonl"],
        "triggers": {scenario: restored[f"triggers_{scenario}.jsonl"] for scenario in SCENARIOS}}
