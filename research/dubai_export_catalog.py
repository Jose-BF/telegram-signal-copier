"""Offline directional-message catalog, deliberately not an engine dataset."""

from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re

from research import telegram_export


CHAT_ID = 1642806869
SCHEMA = "dubai_export_catalog_v1"


def _bytes(value):
    return telegram_export._json_bytes(value)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _origin(raw):
    return {key: raw[key] for key in ("forwarded_from", "forwarded_from_id", "saved_from")
            if raw.get(key) not in (None, "")}


def _media(source, raw, cache):
    name = raw.get("file")
    root = Path(source["path"]).resolve().parent
    if not isinstance(name, str) or not name:
        return {"path": None, "sha256": None, "issue": "media_path_missing"}
    windows = PureWindowsPath(name)
    if windows.drive or windows.root or ".." in windows.parts:
        return {"path": name, "sha256": None, "issue": "media_path_unsafe"}
    path = (root / name.replace("\\", "/")).resolve()
    if not path.is_relative_to(root):
        return {"path": name, "sha256": None, "issue": "media_path_unsafe"}
    key = str(path)
    if key not in cache:
        cache[key] = {"path": key, "sha256": _sha(path.read_bytes()) if path.is_file() else None,
                      "issue": None if path.is_file() else "media_file_missing"}
    return cache[key]


def build_catalog(admission_dir, labels_path, *, companion_seconds=300):
    """Retain every in-period identity; proximity links are review proposals only.

    Media labels bind verified bytes, not filenames or Telegram emoji. Known
    snapshots remain separate even when the old text adapter merged their media
    descriptors. No edit time, forward date or companion can backdate a trigger.
    """
    if type(companion_seconds) is not int or not 1 <= companion_seconds <= 3600:
        raise ValueError("companion_seconds must be an integer from 1 to 3600")
    admission_dir, labels_path = Path(admission_dir), Path(labels_path)
    bundle = telegram_export.load_admission(admission_dir)
    labels_bytes = labels_path.read_bytes()
    registry = json.loads(labels_bytes, object_pairs_hook=telegram_export._unique_object)
    if registry.get("schema_version") != "reviewed_sticker_labels_v1":
        raise ValueError("unsupported sticker labels")
    labels = {}
    for label in registry["labels"]:
        digest = label.get("sha256", "")
        if (not re.fullmatch("[a-f0-9]{64}", digest) or digest in labels
                or label.get("direction") not in ("BUY", "SELL")
                or label.get("symbol") != "XAUUSD" or not label.get("evidence")):
            raise ValueError("invalid or duplicate reviewed sticker label")
        labels[digest] = label
    sources = {row["source_id"]: row for row in bundle["sources"]}
    if {row["chat_id"] for row in sources.values()} != {CHAT_ID}:
        raise ValueError("catalog requires only the declared Dubai chat")
    for source in sources.values():
        if _sha(Path(source["path"]).read_bytes()) != source["sha256"]:
            raise ValueError("export source hash mismatch")
    occurrences = {row["occurrence_id"]: row for row in bundle["occurrences"]}
    media_cache, messages = {}, []
    for identity in bundle["identities"]:
        if identity["period"] == "outside":
            continue
        evidence, issues = [], set(identity["integrity_issues"])
        for revision in identity["revisions"]:
            for occurrence_id in revision["occurrence_ids"]:
                occurrence = occurrences[occurrence_id]
                raw = occurrence["raw"] if isinstance(occurrence["raw"], dict) else {}
                media = _media(sources[occurrence["source_id"]], raw, media_cache) if raw.get("media_type") == "sticker" else None
                label = labels.get(media["sha256"]) if media and raw.get("type") == "message" else None
                direction = label["direction"] if label else revision["direction"]
                issues.update(revision["evidence_issues"])
                if media and media["issue"]:
                    issues.add(media["issue"])
                evidence.append({"occurrence_id": occurrence_id, "source_id": occurrence["source_id"],
                    "revision_id": revision["revision_id"], "raw_snapshot_sha256": occurrence["raw_snapshot_sha256"],
                    "published_utc": revision["published_utc"], "edited_utc": revision["edited_utc"],
                    "has_edit_marker": revision["has_edit_marker"], "direction": direction,
                    "classification": revision["classification"], "text": revision["text"],
                    "media": media, "is_sticker": raw.get("media_type") == "sticker",
                    "is_gold_sticker": label is not None, "forward_origin": _origin(raw),
                    "reply_to_message_id": raw.get("reply_to_message_id")})
        evidence.sort(key=lambda row: row["occurrence_id"])
        directions = {row["direction"] for row in evidence if row["direction"]}
        publications = {row["published_utc"] for row in evidence}
        origins = {_bytes(row["forward_origin"]) for row in evidence}
        if len(directions) > 1:
            issues.add("direction_conflict")
        if len(origins) > 1:
            issues.add("forward_origin_conflict")
        if identity["message_id"] is None:
            issues.add("message_identity_missing")
        if identity["period"] != "inside":
            issues.add("period_unresolved")
        kind = ("gold_sticker" if any(row["is_gold_sticker"] for row in evidence) else
                "unmapped_sticker" if any(row["is_sticker"] for row in evidence) else
                "text_now_candidate" if identity["has_recognized_text_trigger"] else
                "directional_text_review" if any(row["classification"] in ("ambiguous_direction", "unresolved_directional_text", "zone_plan_not_immediate") for row in evidence) else "other")
        unedited = [row for row in evidence if row["direction"] and not row["has_edit_marker"]]
        row = {"signal_id": identity["identity"], "chat_id": CHAT_ID,
            "message_id": identity["message_id"], "kind": kind,
            "direction": next(iter(directions)) if len(directions) == 1 else None,
            "symbol": "XAUUSD" if directions else None,
            "published_utc": next(iter(publications)) if len(publications) == 1 else None,
            "received_utc": None, "trigger_utc": None, "engine_admitted": False,
            "has_unedited_directional_snapshot": bool(unedited),
            "has_edited_snapshot": any(item["has_edit_marker"] for item in evidence),
            "timing_status": "unresolved" if issues else "unedited_export_snapshot" if unedited else "edited_snapshot_only" if directions else "not_directional",
            "forwarded": any(item["forward_origin"] for item in evidence),
            "forward_origins": [json.loads(value) for value in sorted(origins)],
            "issues": sorted(issues), "snapshots": evidence,
            "nearby_sticker": None, "companion_proposal": None,
            "companion_text_ids": [], "review_reasons": []}
        messages.append(row)
    messages.sort(key=lambda row: (row["published_utc"] or "9999", row["message_id"] or 0, row["signal_id"]))

    # This is retrospective annotation, not an online entry/deduplication rule.
    last_sticker, by_id = None, {row["signal_id"]: row for row in messages}
    for row in messages:
        if row["kind"] in ("gold_sticker", "unmapped_sticker"):
            last_sticker = row
        elif row["kind"] == "text_now_candidate":
            texts = [item["text"] or "" for item in row["snapshots"]]
            if any(re.search(r"\bAGAIN\b", value, re.I) for value in texts):
                row["review_reasons"].append("explicit_again_possible_new_entry")
            if any(not re.match(r"^(?:LET['\u2019]?S\s+)?(?:GOLD|XAU\s*USD|BUY|SELL)\b", value.strip(), re.I) for value in texts):
                row["review_reasons"].append("now_phrase_in_context_not_entry_header")
            if last_sticker and row["published_utc"] and last_sticker["published_utc"]:
                delta = int((datetime.fromisoformat(row["published_utc"])-datetime.fromisoformat(last_sticker["published_utc"])).total_seconds())
                row["nearby_sticker"] = {"signal_id": last_sticker["signal_id"], "seconds_after": delta,
                    "same_direction": row["direction"] is not None and row["direction"] == last_sticker["direction"],
                    "same_origin": row["forward_origins"] == last_sticker["forward_origins"]}
                near = row["nearby_sticker"]
                if 0 <= delta <= companion_seconds:
                    if delta == 0:
                        row["review_reasons"].append("same_second_order_unresolved")
                    if not near["same_direction"]:
                        row["review_reasons"].append("nearby_sticker_direction_mismatch")
                    if not near["same_origin"]:
                        row["review_reasons"].append("nearby_sticker_origin_mismatch")
                    if (last_sticker["kind"] == "gold_sticker" and near["same_direction"] and near["same_origin"]
                            and not row["issues"] and not last_sticker["issues"] and not row["review_reasons"]):
                        row["companion_proposal"] = {"signal_id": last_sticker["signal_id"],
                            "basis": "same_direction_origin_nearby_publication_review_only", "confirmed": False}
                        last_sticker["companion_text_ids"].append(row["signal_id"])
            if row["companion_proposal"] is None:
                row["review_reasons"].append("text_entry_relationship_unresolved")
    for row in messages:
        if len(row["companion_text_ids"]) > 1:
            row["review_reasons"].append("multiple_texts_for_sticker")
            for text_id in row["companion_text_ids"]:
                by_id[text_id]["review_reasons"].append("multiple_texts_for_sticker")
        if row["kind"] == "gold_sticker" and not row["companion_text_ids"]:
            row["review_reasons"].append("no_unambiguous_companion_proposal")
    stickers = [row for row in messages if row["kind"] == "gold_sticker"]
    texts = [row for row in messages if row["kind"] == "text_now_candidate"]
    monthly = defaultdict(Counter)
    for row in messages:
        month = (row["published_utc"] or "unknown")[:7]
        monthly[month]["messages"] += 1
        if row["kind"] == "gold_sticker":
            monthly[month][row["direction"] or "conflict"] += 1
            monthly[month]["forwarded"] += row["forwarded"]
            monthly[month]["unedited_snapshot"] += row["has_unedited_directional_snapshot"]
    summary = {"messages": len(messages), "kind_counts": dict(sorted(Counter(row["kind"] for row in messages).items())),
        "gold_sticker_candidates": len(stickers), "forwarded_gold_stickers": sum(row["forwarded"] for row in stickers),
        "gold_with_unedited_snapshot": sum(row["has_unedited_directional_snapshot"] for row in stickers),
        "text_now_candidates": len(texts), "text_companion_proposals": sum(row["companion_proposal"] is not None for row in texts),
        "text_relationships_unresolved": sum(row["companion_proposal"] is None for row in texts),
        "messages_with_integrity_issues": sum(bool(row["issues"]) for row in messages),
        "monthly": {key: dict(value) for key, value in sorted(monthly.items())},
        "engine_admitted": 0, "confirmed_unique_entries": None}
    return {"contract": {"schema_version": SCHEMA, "start_utc": bundle["contract"]["start_utc"],
            "end_exclusive_utc": bundle["contract"]["end_exclusive_utc"], "timezone": "UTC", "precision": "seconds",
            "companion_seconds": companion_seconds, "companion_proposals_are_causal_evidence": False,
            "engine_dataset_ready": False, "temporal_split": None, "proposed_validation": "rolling_windows_not_yet_frozen",
            "provider_management_used": False, "entry_text_inclusion_decision": "pending_user_confirmation",
            "open_gates": ["entry_universe_and_relationship_review", "edits_and_scenario_clock", "historical_bid_ask_and_conversion", "rolling_window_protocol"]},
        "inputs": {"admission_dir": str(admission_dir.resolve()),
            "admission_manifest_sha256": _sha((admission_dir / "manifest.json").read_bytes()),
            "labels_path": str(labels_path.resolve()), "labels_sha256": _sha(labels_bytes),
            "source_exports": [{key: row[key] for key in ("path", "sha256")} for row in sources.values()],
            "media": [media_cache[key] for key in sorted(media_cache)]},
        "implementation": {path.name: _sha(path.read_bytes()) for path in (Path(__file__), Path(telegram_export.__file__))},
        "summary": summary, "messages": messages, "sticker_signals": stickers, "text_entries": texts}


def write_catalog(catalog, output_dir):
    output = Path(output_dir).resolve()
    payloads = {"summary.json": _bytes(catalog["summary"])}
    for key in ("messages", "sticker_signals", "text_entries"):
        payloads[f"{key}.jsonl"] = b"".join(_bytes(row) for row in catalog[key])
    manifest = {key: catalog[key] for key in ("contract", "inputs", "implementation")}
    manifest["artifacts"] = {name: {"sha256": _sha(data), "bytes": len(data)} for name, data in payloads.items()}
    manifest["catalog_identity_sha256"] = _sha(_bytes(manifest))
    payloads["manifest.json"] = _bytes(manifest)
    protected = {Path(row["path"]).resolve() for row in catalog["inputs"]["source_exports"]}
    protected.update(Path(row["path"]).resolve() for row in catalog["inputs"]["media"])
    protected.add(Path(catalog["inputs"]["labels_path"]).resolve())
    admission = Path(catalog["inputs"]["admission_dir"]).resolve()
    for name, data in payloads.items():
        target = output / name
        if target.resolve() in protected or target.is_relative_to(admission):
            raise ValueError("output would overwrite input evidence")
        if target.exists() and target.read_bytes() != data:
            raise ValueError("immutable catalog conflict")
    output.mkdir(parents=True, exist_ok=True)
    for name, data in payloads.items():
        if not (output / name).exists():
            with (output / name).open("xb") as stream:
                stream.write(data)
    return manifest
