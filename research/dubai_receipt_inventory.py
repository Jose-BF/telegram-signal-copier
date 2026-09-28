"""Receipt evidence tiers without inventing modern identities for old logs."""

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import time

from research.causal_replay import compile_signals, raw_message, utc
from research.dubai_clock_audit import _signal_record, _write, build_crosswalk, verify_audit
from research.dubai_entry_probe import SCENARIOS, _archive, _rows
from research.strategy_study import (
    ROOT, _check_current, _digest, _encode, _invalid, _read, _sha, _unique,
    _verify_sources, _watch, current_identity,
)
from tools.audit_causal_lineage import _has_raw_message_evidence, _semantic_is_edit


SCHEMA = "dubai_receipt_evidence_inventory_v2"
BUDGET = {"max_raw_bytes": 300_000_000, "max_raw_lines": 1_000_000,
          "max_directional_identities": 300, "max_wall_seconds": 3600}
PERIOD = {"start_utc": "2026-06-05T00:00:00Z", "canonical_start_utc": "2026-07-27T00:00:00Z",
          "end_exclusive_utc": "2026-09-08T00:00:00Z"}
SOURCES = ("research/dubai_receipt_inventory.py", "tools/audit_dubai_receipts.py",
           "research/dubai_clock_audit.py", "research/dubai_entry_probe.py", "tools/audit_causal_lineage.py")
_IMPORTED = {name: _digest(ROOT / name) for name in SOURCES if (ROOT / name).is_file()}
ARTIFACTS = {"protocol.json", "inventory.json", "canonical_signals.json", "summary.json"}
LEGACY_BINDING = {"legacy_channel_tag_only": "declared_channel_tag_no_observed_numeric_chat_or_revision",
                  "legacy_observed_chat_without_revision": "observed_numeric_chat_no_observed_canonical_revision"}
LIMITATIONS = [
    "Canonical receipts retain numeric chat and canonical revision; early legacy keeps only a channel tag, intermediate legacy keeps numeric chat without revision.",
    "Intermediate legacy is restricted to receipts before the canonical epoch and absence of every modern revision-schema marker; malformed canonical claims never fall back.",
    "No absent chat or message_revision_id is synthesized, inferred as observed, or passed through the canonical compiler.",
    "Legacy receipt support is a separate research evidence tier, not canonical telemetry certification.",
    "Only predeclared sticker IDs determine direction. Text, provider performance and later export edits cannot rewrite a trigger.",
    "Export correspondence is diagnostic, not a causal eligibility filter. Missing/changed later exports cannot remove initial receipt evidence.",
    "Freshness within five seconds is a predeclared descriptive subset, not an automatically activated trading filter.",
    "Every directional identity, invalid receipt, unknown sticker and incomplete original revision stays visible.",
    "Receipt inventory only: no quote admission, strategy evaluations, account money, new OOS or live policy selection.",
]


def _stamp(value):
    try:
        return utc(value)
    except (ValueError, TypeError, OverflowError):
        return None


def classify_receipt(row):
    reasons = []
    markers = {"message_revision_id", "revision_token", "schema_version", "media_sha256"} & row.keys()
    observed = _stamp(row.get("ts"))
    historical_chat_only = (not markers and type(row.get("chat_id")) is int and row["chat_id"] == -1001642806869
                            and observed is not None and observed < utc(PERIOD["canonical_start_utc"]))
    modern = bool(markers) or ("chat_id" in row and not historical_chat_only)
    if row.get("ev") != "telegram_raw" or row.get("channel") != "canal1":
        reasons.append("wrong_receipt_scope")
    if type(row.get("message_id")) is not int or row["message_id"] <= 0:
        reasons.append("invalid_message_id")
    if _stamp(row.get("ts")) is None or _stamp(row.get("date_utc")) is None:
        reasons.append("missing_or_invalid_receipt_publication_clock")
    if row.get("edit_date_utc") is not None and _stamp(row["edit_date_utc"]) is None:
        reasons.append("invalid_edit_clock")
    sticker = row.get("sticker_id")
    if sticker is not None and (type(sticker) not in (int, str) or not str(sticker).isdigit() or int(sticker) <= 0):
        reasons.append("invalid_sticker_id")
    if modern:
        if not _has_raw_message_evidence(row) or row.get("chat_id") != -1001642806869:
            reasons.append("incomplete_or_invalid_canonical_evidence")
    else:
        required = {"ev", "channel", "message_id", "update_kind", "date_utc", "edit_date_utc", "ts",
                    "is_edit", "is_reply", "reply_to_msg_id", "has_text", "text", "text_len", "text_sha1", "has_media", "sticker_id"}
        if not required <= row.keys():
            reasons.append("legacy_required_fields_missing")
        if (type(row.get("is_edit")) is not bool or type(row.get("update_kind")) is not str
                or not row.get("update_kind") or row["is_edit"] != _semantic_is_edit(row["update_kind"])):
            reasons.append("legacy_edit_transport_invalid")
        text = row.get("text")
        if (type(text) is not str or type(row.get("has_text")) is not bool or row["has_text"] != bool(text)
                or type(row.get("text_len")) is not int or row["text_len"] != len(text or "")):
            reasons.append("legacy_text_shape_invalid")
        elif row.get("text_sha1") != (hashlib.sha1(text.encode("utf-8")).hexdigest() if text else None):
            reasons.append("legacy_text_hash_mismatch")
        if type(row.get("has_media")) is not bool or (sticker is not None and row.get("has_media") is not True):
            reasons.append("legacy_media_shape_invalid")
        if type(row.get("is_reply")) is not bool or (row.get("reply_to_msg_id") is not None
                and (type(row["reply_to_msg_id"]) is not int or row["reply_to_msg_id"] <= 0)):
            reasons.append("legacy_reply_shape_invalid")
    return {"tier": "invalid" if reasons else "canonical_chat_revision" if modern else
                    "legacy_observed_chat_without_revision" if historical_chat_only else "legacy_channel_tag_only",
            "reasons": reasons, "numeric_chat_observed": type(row.get("chat_id")) is int and row["chat_id"] == -1001642806869,
            "canonical_revision_observed": modern and _has_raw_message_evidence(row)}


def build_inventory(receipts, *, start, end, sticker_directions, triggers, decisions):
    start, end = utc(start), utc(end)
    if end <= start or any(v not in {"BUY", "SELL"} for v in sticker_directions.values()):
        raise ValueError("invalid receipt period or frozen sticker directions")
    receipts = deepcopy(receipts)
    lines, groups, invalid, unknown = set(), defaultdict(list), [], defaultdict(list)
    tiers, outside, context = Counter(), 0, 0
    for line, row in receipts:
        if type(line) is not int or line <= 0 or line in lines:
            raise ValueError("duplicate or invalid source line")
        lines.add(line)
        observed = _stamp(row.get("ts"))
        if observed is not None and not start <= observed < end:
            outside += 1
            continue
        evidence = classify_receipt(row)
        tiers[evidence["tier"]] += 1
        proof = {"source_line_1based": line, "source_row_sha256": _sha(row), "evidence": evidence}
        if evidence["reasons"]:
            invalid.append(dict(proof, message_id=row.get("message_id"), received_utc=row.get("ts")))
        mid = row.get("message_id")
        if type(mid) is not int or mid <= 0 or observed is None:
            continue
        direction = sticker_directions.get(str(row.get("sticker_id")))
        groups[mid].append({"row": row, "observed": observed, "direction": direction, "proof": proof})
        if row.get("sticker_id") is not None and direction is None:
            unknown[mid].append(dict(proof, sticker_id=row["sticker_id"]))
        if direction is None:
            context += 1
    entries, originals = [], []
    for mid, history in groups.items():
        history.sort(key=lambda r: (r["observed"], r["proof"]["source_line_1based"]))
        directional = [r for r in history if r["direction"] is not None]
        if not directional:
            continue
        first = directional[0]
        row, proof, observed = first["row"], first["proof"], first["observed"]
        published, edited = _stamp(row.get("date_utc")), _stamp(row.get("edit_date_utc"))
        is_edit = bool(row.get("is_edit") or row.get("edit_date_utc"))
        reasons = list(proof["evidence"]["reasons"])
        if is_edit:
            reasons.append("first_directional_revision_is_edit")
        if published is not None and published > observed:
            reasons.append("publication_after_receipt")
        if edited is not None and edited > observed:
            reasons.append("edit_clock_after_receipt")
        if history[0]["observed"] < observed:
            reasons.append("earlier_nondirectional_receipt")
        initial = [r for r in history if r["observed"] == observed]
        semantic = {(_stamp(r["row"].get("date_utc")), _stamp(r["row"].get("edit_date_utc")),
                     str(r["row"].get("sticker_id")), r["row"].get("text")) for r in initial}
        if len(semantic) != 1:
            reasons.append("ambiguous_first_directional_receipt")
        lag = (observed - published).total_seconds() if published is not None else None
        diagnostics = []
        if any(r["direction"] != first["direction"] for r in directional[1:]):
            diagnostics.append("later_direction_differs")
        if any(_stamp(r["row"].get("date_utc")) != published for r in history):
            diagnostics.append("publication_identity_conflict")
        evidence = proof["evidence"]
        entry = {"signal_id": f"canal1_{mid}", "message_id": mid, "direction": first["direction"],
            "published_utc": published.isoformat() if published is not None else None,
            "received_utc": observed.isoformat(), "raw_chat_id": row.get("chat_id"),
            "raw_message_revision_id": row.get("message_revision_id"), "sticker_id": row.get("sticker_id"),
            "first_source_line_1based": proof["source_line_1based"], "first_source_row_sha256": proof["source_row_sha256"],
            "source_evidence_tier": evidence["tier"], "numeric_chat_observed": evidence["numeric_chat_observed"],
            "canonical_revision_observed": evidence["canonical_revision_observed"],
            "retained_revision_is_edit": is_edit, "receipt_minus_publication_seconds": lag,
            "initial_receipt_supported": not reasons, "fresh_initial_receipt_within_5s": not reasons and 0 <= lag <= 5,
            "entry_reasons": reasons, "history_diagnostics": diagnostics,
            "receipt_count": len(history), "receipt_proofs": [r["proof"] for r in history],
            "export_crosswalk": [], "legacy_channel_binding": "declared_channel_tag_not_observed_numeric_chat"
                if evidence["tier"] == "legacy_channel_tag_only" else "observed_numeric_chat_no_canonical_revision"
                if evidence["tier"] == "legacy_observed_chat_without_revision" else None}
        entries.append(entry)
        if published is not None:
            originals.append({k: entry[k] for k in ("signal_id", "message_id", "direction", "published_utc",
                "received_utc", "retained_revision_is_edit")})
    if len(entries) > BUDGET["max_directional_identities"]:
        raise ValueError("receipt identity budget exceeded")
    crosswalk = build_crosswalk(originals, triggers, decisions)
    by_id = {r["signal_id"]: r for r in entries}
    for row in crosswalk["pairs"]:
        entry = by_id[row["raw_original"]["signal_id"]]
        evidence_reasons = ["receipt_evidence:" + r for r in entry["entry_reasons"]]
        if evidence_reasons:
            row["comparison_status"] = "not_comparable"
            row["reasons"] = sorted(set(row["reasons"] + evidence_reasons))
        entry["export_crosswalk"].append({k: v for k, v in row.items() if k != "raw_original"})
    entries.sort(key=lambda r: (utc(r["received_utc"]), r["message_id"]))
    return {"schema_version": SCHEMA, "entries": entries, "invalid_receipts": invalid,
        "unknown_sticker_messages": [{"message_id": mid, "receipts": rows} for mid, rows in sorted(unknown.items())],
        "export_inventory": crosswalk["export_inventory"], "receipt_tiers": dict(tiers),
        "outside_period_receipts": outside, "nondirectional_receipts": context,
        "raw_input_receipts": len(receipts), "engine_dataset_ready": False, "money_contract_verified": False,
        "account_currency_money_verified": False, "strategy_evaluations": 0, "selected_policy": None,
        "orders_sent": 0, "limitations": LIMITATIONS}


def summarize(inventory, canonical):
    groups = {}
    for tier in ("canonical_chat_revision", "legacy_channel_tag_only", "legacy_observed_chat_without_revision", "invalid"):
        rows = [r for r in inventory["entries"] if r["source_evidence_tier"] == tier]
        groups[tier] = {"directional_ids": len(rows), "initial_receipt_supported": sum(r["initial_receipt_supported"] for r in rows),
            "fresh_initial_receipt_within_5s": sum(r["fresh_initial_receipt_within_5s"] for r in rows),
            "receipt_months": dict(Counter(r["received_utc"][:7] for r in rows)),
            "entry_reasons": dict(Counter(reason for r in rows for reason in r["entry_reasons"])),
            "history_diagnostics": dict(Counter(reason for r in rows for reason in r["history_diagnostics"])),
            "export_comparison": {scenario: dict(Counter(c["comparison_status"] for r in rows
                for c in r["export_crosswalk"] if c["scenario"] == scenario)) for scenario in SCENARIOS}}
    return {"schema_version": SCHEMA, "groups": groups, "receipt_tiers": inventory["receipt_tiers"],
        "invalid_receipts": len(inventory["invalid_receipts"]),
        "unknown_sticker_message_ids": len(inventory["unknown_sticker_messages"]),
        "directional_identities": len(inventory["entries"]), "canonical_compiled_signals": len(canonical["signals"]),
        "canonical_added_signal_ids": canonical["added_signal_ids"],
        "canonical_diagnostics": dict(Counter(r["reason"] for r in canonical["diagnostics"])),
        "canonical_prefix_matches_prior": canonical["prior_prefix_verified"],
        "engine_dataset_ready": False, "money_contract_verified": False, "strategy_evaluations": 0,
        "orders_sent": 0, "selected_policy": None, "limitations": LIMITATIONS}


def _current(identity):
    _check_current(identity)
    if any(_digest(ROOT / name) != value for name, value in _IMPORTED.items()):
        raise ValueError("loaded receipt inventory implementation changed")


def _compute(protocol, watch, deadline):
    proof, source = protocol["raw_source"], Path(protocol["raw_source"]["path"])
    if proof["bytes"] != source.stat().st_size or proof["bytes"] > BUDGET["max_raw_bytes"]:
        raise ValueError("raw source byte budget or size mismatch")
    watch(source, proof["sha256"])
    receipts, canonical_rows = [], []
    start, end, canonical_start = (utc(protocol["period"][name]) for name in
                                  ("start_utc", "end_exclusive_utc", "canonical_start_utc"))
    with source.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if number > BUDGET["max_raw_lines"]:
                raise ValueError("raw source line budget exceeded")
            if number % 4096 == 0:
                deadline()
            row = json.loads(line, object_pairs_hook=_unique, parse_constant=_invalid)
            if row.get("ev") != "telegram_raw" or row.get("channel") != "canal1":
                continue
            stamp = _stamp(row.get("ts"))
            if stamp is not None and not start <= stamp < end:
                continue
            receipts.append((number, row))
            if stamp is not None and canonical_start <= stamp < end:
                if not _has_raw_message_evidence(row) or row.get("chat_id") != -1001642806869:
                    raise ValueError("canonical extension contains invalid original evidence; no legacy fallback")
                canonical_rows.append(raw_message(row))
    signals, diagnostics = compile_signals(canonical_rows, start=canonical_start, cutoff=end,
                                           sticker_directions=protocol["sticker_directions"])
    signals = [_signal_record(asdict(s)) for s in signals]
    prior = _read(Path(protocol["prior_audit_dir"]) / "originals.json")
    by_id = {s["signal_id"]: s for s in signals}
    for original in prior:
        current = by_id.get(original["signal_id"])
        if current is None or any(current[field] != original[key] for field, key in (
                ("direction", "direction"), ("observed_at", "received_utc"), ("published_at", "published_utc"),
                ("message_revision_id", "message_revision_id"))):
            raise ValueError("canonical extension changed a prior trigger")
        old_events = original["provider_events"]
        if current["provider_events"][:len(old_events)] != old_events:
            raise ValueError("canonical extension changed the original provider-event prefix")
    old_ids = {r["signal_id"] for r in prior}
    canonical = {"signals": signals, "diagnostics": list(diagnostics), "prior_prefix_verified": True,
                 "added_signal_ids": [r["signal_id"] for r in signals if r["signal_id"] not in old_ids]}
    directory = Path(protocol["stream_dir"])
    inventory = build_inventory(receipts, start=start, end=end, sticker_directions=protocol["sticker_directions"],
                                triggers=_rows(directory / "triggers.jsonl"), decisions=_rows(directory / "decisions.jsonl"))
    return inventory, canonical, summarize(inventory, canonical)


def run_inventory(prior_audit_dir, output_dir):
    started, watched = time.monotonic(), {}
    prior_dir, output = Path(prior_audit_dir).resolve(), Path(output_dir).resolve()
    if output.exists():
        raise ValueError("immutable receipt inventory output already exists")

    def deadline():
        if time.monotonic() - started >= BUDGET["max_wall_seconds"]:
            raise TimeoutError("receipt inventory wall budget exceeded")

    def watch(path, expected=None):
        deadline()
        if Path(path).resolve().is_relative_to(output):
            raise ValueError("output contains a protected input")
        _watch(path, _digest(path) if expected is None else expected, watched)

    proof = verify_audit(prior_dir)
    _archive(prior_dir, "audit_identity_sha256", watch)
    prior_protocol = _read(prior_dir / "protocol.json")
    source_protocol = _read(Path(prior_protocol["raw_inputs"]) / "data_protocol.json")
    stream = Path(prior_protocol["stream_dir"])
    stream_manifest = _archive(stream, "stream_identity_sha256", watch)
    for root in (prior_dir, stream, Path(prior_protocol["raw_inputs"]),
                 *map(Path, stream_manifest["inputs"].get("protected_archive_dirs", []))):
        if output.is_relative_to(root.resolve()) or root.resolve().is_relative_to(output):
            raise ValueError("output overlaps a protected archive")
    identity = current_identity()
    _current(identity)
    for name in SOURCES:
        watch(ROOT / name)
    protocol = {"schema_version": SCHEMA, "budget": BUDGET, "period": PERIOD,
        "prior_audit_dir": str(prior_dir), "prior_audit_proof": proof, "stream_dir": str(stream),
        "implementation": identity, "raw_source": source_protocol["raw_source"],
        "sticker_directions": source_protocol["sticker_directions"], "freshness_reference_seconds": 5,
        "legacy_channel_binding": LEGACY_BINDING,
        "export_correspondence_used_as_eligibility_filter": False,
        "data_use": "retrospective_receipt_inventory_only", "limitations": LIMITATIONS}
    output.mkdir(parents=True, exist_ok=False)
    _write(output / "protocol.json", protocol)
    inventory, canonical, summary = _compute(protocol, watch, deadline)
    _current(identity)
    _verify_sources(watched)
    deadline()
    for name, value in (("inventory.json", inventory), ("canonical_signals.json", canonical), ("summary.json", summary)):
        _write(output / name, value)
    manifest = {"schema_version": SCHEMA, "status": "complete_receipt_inventory_only",
        "inputs": {"watched_files": watched},
        "artifacts": {name: {"sha256": _digest(output / name)} for name in sorted(ARTIFACTS)}}
    manifest["receipt_inventory_identity_sha256"] = _sha(manifest)
    _write(output / "manifest.json", manifest)
    return summary


def verify_inventory(output_dir):
    started, watched, output = time.monotonic(), {}, Path(output_dir).resolve()
    if {p.name for p in output.iterdir()} != ARTIFACTS | {"manifest.json"}:
        raise ValueError("incomplete or mixed receipt inventory archive")

    def deadline():
        if time.monotonic() - started >= BUDGET["max_wall_seconds"]:
            raise TimeoutError("receipt inventory verify budget exhausted")

    def watch(path, expected=None):
        deadline()
        _watch(path, _digest(path) if expected is None else expected, watched)

    manifest = _archive(output, "receipt_inventory_identity_sha256", watch)
    if manifest["schema_version"] != SCHEMA or set(manifest["artifacts"]) != ARTIFACTS:
        raise ValueError("unsupported receipt inventory manifest")
    protocol = _read(output / "protocol.json")
    _current(protocol["implementation"])
    if (protocol["schema_version"] != SCHEMA or protocol["budget"] != BUDGET or protocol["period"] != PERIOD
            or protocol["export_correspondence_used_as_eligibility_filter"] is not False
            or protocol["freshness_reference_seconds"] != 5 or protocol["limitations"] != LIMITATIONS
            or protocol["legacy_channel_binding"] != LEGACY_BINDING):
        raise ValueError("receipt inventory protocol differs")
    if verify_audit(protocol["prior_audit_dir"]) != protocol["prior_audit_proof"]:
        raise ValueError("prior original audit identity differs")
    inventory, canonical, summary = _compute(protocol, watch, deadline)
    if (inventory != _read(output / "inventory.json") or canonical != _read(output / "canonical_signals.json")
            or summary != _read(output / "summary.json")):
        raise ValueError("receipt inventory source recomputation differs")
    _current(protocol["implementation"])
    _verify_sources(watched)
    return {"status": "verified_receipt_inventory_only",
        "receipt_inventory_identity_sha256": manifest["receipt_inventory_identity_sha256"],
        "directional_identities": summary["directional_identities"],
        "canonical_compiled_signals": summary["canonical_compiled_signals"],
        "canonical_added_signals": len(summary["canonical_added_signal_ids"])}
