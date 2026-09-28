"""Prefix-causal, hypothetical entry grouping over retained export snapshots.

This is not the live listener or a reconstruction of unretained revisions.
Retrospective catalog classifications and manual relationships never drive it.
"""

from collections import Counter, defaultdict
from copy import deepcopy
from itertools import groupby
from pathlib import Path
import re

from research.telegram_export import _classify, _hash, _iso, _json_bytes, _utc
from research.dubai_annual_universe import rolling_cohorts


SCENARIOS = ("revision_time", "publication_initial")
HEADER = re.compile(
    r"^(?:SCALP(?:\s*TRADE)?\s+)?(?:LET['\u2019]?S\s+)?(?:"
    r"(?:GOLD|XAU(?:\s*USD)?)\s+(?:BUY|SELL)|"
    r"(?:BUY|SELL)\s+(?:GOLD|XAU(?:\s*USD)?))"
    r"(?:\s+AGAIN)?\s+NOW\b", re.I)
PRODUCT = re.compile(r"\b(?:GOLD|XAU(?:\s*USD)?)\b", re.I)


def classify_snapshot(snapshot, labels):
    if snapshot.get("classification") == "service_or_unknown_message_type":
        return "context", None, "service_or_unknown_message_type"
    if snapshot["is_sticker"]:
        if snapshot["is_gold_sticker"] and snapshot["direction"] in ("BUY", "SELL"):
            return "sticker", snapshot["direction"], "verified_media_bytes"
        return "barrier", None, "unknown_sticker_symbol_or_direction"
    text = snapshot["text"] or ""
    label = labels.get(snapshot["raw_snapshot_sha256"])
    if label:
        return "text", label["direction"], "snapshot_only_semantic_label"
    direction, kind = _classify({"type": "message"}, text)
    if kind == "explicit_text_now" and HEADER.match(text.strip()) and PRODUCT.search(text):
        return "text", direction, "explicit_gold_now_header"
    return "context", None, "outside_self_contained_entry_grammar"


def make_events(messages, protocol, scenario):
    if scenario not in SCENARIOS:
        raise ValueError("explicit supported message-clock scenario required")
    labels = {row["raw_snapshot_sha256"]: row for row in protocol["semantic_labels"]}
    if len(labels) != len(protocol["semantic_labels"]) or any(row["direction"] not in ("BUY", "SELL") for row in labels.values()):
        raise ValueError("invalid snapshot labels")
    events, unresolved, provenance = {}, [], defaultdict(list)
    for message in messages:
        if message["chat_id"] != 1642806869 or type(message["message_id"]) is not int:
            raise ValueError("invalid message identity")
        for snapshot in message["snapshots"]:
            # Export filenames and raw JSON formatting are not new message versions.
            content = {name: snapshot[name] for name in ("published_utc", "edited_utc", "has_edit_marker",
                "text", "is_sticker", "forward_origin", "reply_to_message_id")}
            content["media_sha256"] = (snapshot.get("media") or {}).get("sha256")
            content["sticker_direction"] = snapshot["direction"] if snapshot["is_gold_sticker"] else None
            content["service_message"] = snapshot.get("classification") == "service_or_unknown_message_type"
            content_hash = _hash(content)
            key = f"{message['chat_id']}:{message['message_id']}:{content_hash}"
            provenance[key].append({name: snapshot[name] for name in
                ("occurrence_id", "source_id", "revision_id", "raw_snapshot_sha256")})
            kind, direction, basis = classify_snapshot(snapshot, labels)
            published = snapshot["published_utc"]
            available = snapshot["edited_utc"] if snapshot["has_edit_marker"] else published
            reason = None
            if scenario == "publication_initial" and snapshot["has_edit_marker"]:
                reason = "edited_snapshot_unavailable_in_initial_scenario"
            elif not published or not available:
                reason = "snapshot_clock_missing"
            elif _utc(available) < _utc(published):
                reason = "revision_precedes_publication"
            event = {"event_id": key, "chat_id": message["chat_id"], "message_id": message["message_id"],
                "content_sha256": content_hash, "kind": kind, "direction": direction,
                "classification_basis": basis, "published_utc": published, "available_utc": available,
                "is_edit": snapshot["has_edit_marker"], "origin": snapshot["forward_origin"],
                "reply_to_message_id": snapshot["reply_to_message_id"], "clock_block_reason": reason}
            if key in events and events[key] != event:
                raise ValueError("same snapshot identity has contradictory content")
            events[key] = event
    timed = []
    for event in events.values():
        if event["clock_block_reason"]:
            unresolved.append(event)
        elif not _utc(protocol["period"]["start"]) <= _utc(event["available_utc"]) < _utc(protocol["period"]["end_exclusive"]):
            unresolved.append(dict(event, clock_block_reason="availability_outside_period"))
        else:
            timed.append(event)
    timed.sort(key=lambda event: (_utc(event["available_utc"]), event["message_id"], event["event_id"]))
    return timed, sorted(unresolved, key=lambda e: e["event_id"]), dict(provenance)


def replay_events(events, *, scenario, pair_seconds=300):
    """Consume full timestamp batches; future batches cannot rewrite decisions."""
    if scenario not in SCENARIOS or type(pair_seconds) is not int or not 0 < pair_seconds <= 3600:
        raise ValueError("invalid stream protocol")
    ordered = sorted(events, key=lambda e: (_utc(e["available_utc"]), e["message_id"], e["event_id"]))
    consumed, seen_events, triggers, decisions = {}, set(), [], []
    pending = None
    for _, batch_iter in groupby(ordered, key=lambda e: _utc(e["available_utc"])):
        batch = list(batch_iter)
        if any(e["event_id"] in seen_events for e in batch) or len({e["event_id"] for e in batch}) != len(batch):
            raise ValueError("redeliveries must be canonicalized before replay")
        seen_events.update(e["event_id"] for e in batch)
        fresh = [e for e in batch if e["kind"] in ("sticker", "text") and e["message_id"] not in consumed]
        same_identity = Counter(e["message_id"] for e in batch)
        barrier = any(e["kind"] == "barrier" and e["message_id"] not in consumed for e in batch)
        simultaneous = len(fresh) > 1
        if barrier or simultaneous:
            pending = None
        for event in batch:
            mid = event["message_id"]
            decision = {"event_id": event["event_id"], "message_id": mid, "scenario": scenario,
                "available_utc": event["available_utc"], "direction": event["direction"], "trigger_id": None,
                "action": "context", "reason": event["classification_basis"],
                "same_timestamp_entry_batch": simultaneous}
            if mid in consumed:
                existing = consumed[mid]
                changed = (event["direction"] is not None and event["direction"] != existing["direction"])
                decision.update(action="revision_no_retrigger", trigger_id=existing["trigger_id"],
                    reason="direction_change_after_trigger" if changed else "message_already_assigned")
                if changed:
                    pending = None
            elif same_identity[mid] > 1:
                decision.update(action="blocked", reason="conflicting_snapshots_same_message_same_time")
            elif event["kind"] == "barrier":
                decision.update(action="pairing_barrier")
            elif event["kind"] in ("sticker", "text"):
                pair = False
                if pending is not None:
                    delay = (_utc(event["available_utc"]) - _utc(pending["available_utc"])).total_seconds()
                    publication_distance = abs((_utc(event["published_utc"]) - _utc(pending["published_utc"])).total_seconds())
                    pair = (event["kind"] != pending["kind"] and event["direction"] == pending["direction"]
                            and event["origin"] == pending["origin"] and 0 < delay <= pair_seconds
                            and publication_distance <= pair_seconds)
                if pair:
                    trigger_id = pending["trigger_id"]
                    decision.update(action="pair_complement", reason="opposite_form_same_direction_origin_within_window", trigger_id=trigger_id)
                    pending = None
                else:
                    trigger_id = f"dubai_stream:{scenario}:{mid}"
                    trigger = {"trigger_id": trigger_id, "entry_id": trigger_id, "scenario": scenario,
                        "chat_id": event["chat_id"], "channel": "canal1", "symbol": "XAUUSD",
                        "direction": event["direction"], "trigger_message_id": mid, "trigger_event_id": event["event_id"],
                        "trigger_utc": event["available_utc"], "published_utc": event["published_utc"],
                        "source_content_sha256": event["content_sha256"], "source_kind": event["kind"],
                        "source_is_edit": event["is_edit"], "forward_origin": event["origin"],
                        "publication_age_seconds": (_utc(event["available_utc"]) - _utc(event["published_utc"])).total_seconds(),
                        "received_utc": None, "clock_is_hypothesis": True,
                        "causal_rule_applied": True, "engine_admitted": False, "provider_events": [],
                        "requires_own_position_policy": True, "same_timestamp_entry_batch": simultaneous}
                    triggers.append(trigger)
                    decision.update(action="new_entry", trigger_id=trigger_id,
                        reason="simultaneous_entries_not_paired" if simultaneous else "no_eligible_pending_complement")
                    pending = {**event, "trigger_id": trigger_id} if not simultaneous and not barrier else None
                consumed[mid] = {"trigger_id": trigger_id, "direction": event["direction"]}
            decisions.append(decision)
    return triggers, decisions


def compare_legacy(legacy, triggers, decisions):
    assignments = {r["message_id"]: r["trigger_id"] for r in decisions if r["action"] in ("new_entry", "pair_complement")}
    groups = defaultdict(set)
    for mid, trigger in assignments.items():
        groups[trigger].add(mid)
    by_trigger = {r["trigger_id"]: r for r in triggers}
    legacy_owner = {mid: r["entry_id"] for r in legacy for mid in r["directional_message_ids"]}
    rows = []
    for entry in legacy:
        members = set(entry["directional_message_ids"])
        targets = sorted({assignments[mid] for mid in members if mid in assignments})
        merged = sorted({legacy_owner.get(mid, "outside_legacy_entry_scope") for target in targets
                         for mid in groups[target] if mid not in members})
        status = "no_trigger" if not targets else "split" if len(targets) > 1 else "merged" if merged else "one_trigger"
        rows.append({"legacy_entry_id": entry["entry_id"], "legacy_message_ids": sorted(members),
            "trigger_ids": targets, "status": status, "other_legacy_entries_merged": merged,
            "messages_without_assignment": sorted(members - assignments.keys()),
            "same_directional_membership": len(targets) == 1 and groups[targets[0]] == members,
            "first_trigger_utc": min((by_trigger[t]["trigger_utc"] for t in targets), default=None),
            "legacy_known_revision_or_initial_utc": entry["known_revision_or_initial_component_utc"],
            "legacy_flags_annotation_only": entry["review_flags"]})
    return rows


def prefix_checks(events, protocol, scenario):
    full_triggers, full_decisions = replay_events(events, scenario=scenario, pair_seconds=protocol["pair_seconds"])
    boundaries = sorted({e["available_utc"][:7] + "-01T00:00:00Z" for e in events})
    checks = []
    for cutoff in boundaries:
        prefix = [e for e in events if _utc(e["available_utc"]) < _utc(cutoff)]
        triggers, decisions = replay_events(prefix, scenario=scenario, pair_seconds=protocol["pair_seconds"])
        expected_triggers = [t for t in full_triggers if _utc(t["trigger_utc"]) < _utc(cutoff)]
        expected_decisions = [d for d in full_decisions if _utc(d["available_utc"]) < _utc(cutoff)]
        valid = triggers == expected_triggers and decisions == expected_decisions
        checks.append({"scenario": scenario, "cutoff_exclusive_utc": cutoff, "events": len(prefix), "valid": valid})
        if not valid:
            raise ValueError("future events changed prefix decisions")
    return checks


def to_causal_signals(triggers, *, scenario):
    """Existing signal type; observed_at is explicitly the hypothetical clock."""
    from research.causal_replay import CausalSignal
    if scenario not in SCENARIOS or any(t["scenario"] != scenario or t["received_utc"] is not None
        or t["chat_id"] != 1642806869 or not t["clock_is_hypothesis"] or not t["causal_rule_applied"]
        or t["engine_admitted"] or t["provider_events"] for t in triggers):
        raise ValueError("unsupported hypothetical trigger contract")
    if len({t["trigger_id"] for t in triggers}) != len(triggers):
        raise ValueError("duplicate trigger identity")
    return tuple(CausalSignal(t["trigger_id"], "canal1", t["direction"], _utc(t["trigger_utc"]),
        _utc(t["published_utc"]), t["trigger_event_id"], ()) for t in triggers)


def build_stream(messages, legacy, protocol):
    if (protocol["schema_version"] != "dubai_entry_stream_protocol_v1" or protocol["search_candidates"] != 0
            or protocol["engine_dataset_ready"] is not False):
        raise ValueError("unsupported stream protocol")
    result = {key: [] for key in ("triggers", "decisions", "unavailable_snapshots", "legacy_lineage",
                                  "message_dispositions", "rolling_folds", "entry_fold_usage", "prefix_checks")}
    summary = {}
    provenance = {}
    for scenario in SCENARIOS:
        events, unavailable, proofs = make_events(messages, protocol, scenario)
        provenance.update(proofs)
        triggers, decisions = replay_events(events, scenario=scenario, pair_seconds=protocol["pair_seconds"])
        checks = prefix_checks(events, protocol, scenario)
        lineage = compare_legacy(legacy, triggers, decisions)
        folds, usage = rolling_cohorts([dict(t, publication_reference_utc=t["trigger_utc"]) for t in triggers],
                                      **protocol["rolling_protocol"])
        result["triggers"].extend(triggers)
        result["decisions"].extend(decisions)
        for name, rows in (("unavailable_snapshots", unavailable), ("legacy_lineage", lineage),
                           ("rolling_folds", folds), ("entry_fold_usage", usage), ("prefix_checks", checks)):
            result[name].extend(dict(r, scenario=scenario) for r in rows)
        by_mid = defaultdict(list)
        unavailable_by_mid = defaultdict(list)
        for decision in decisions:
            by_mid[decision["message_id"]].append(decision)
        for event in unavailable:
            unavailable_by_mid[event["message_id"]].append(event["clock_block_reason"])
        for message in messages:
            rows = by_mid[message["message_id"]]
            result["message_dispositions"].append({"message_id": message["message_id"], "scenario": scenario,
                "actions": dict(Counter(r["action"] for r in rows)),
                "trigger_ids": sorted({r["trigger_id"] for r in rows if r["trigger_id"]}),
                "clock_block_reasons": sorted(set(unavailable_by_mid[message["message_id"]]))})
        bridged = to_causal_signals(triggers, scenario=scenario)
        if len(bridged) != len(triggers):
            raise ValueError("bridge changed trigger denominator")
        summary[scenario] = {"source_messages": len(messages), "available_snapshot_events": len(events),
            "unavailable_snapshot_events": len(unavailable), "triggers": len(triggers),
            "BUY": sum(t["direction"] == "BUY" for t in triggers), "SELL": sum(t["direction"] == "SELL" for t in triggers),
            "edited_first_component": sum(t["source_is_edit"] for t in triggers),
            "publication_age_over_pair_window": sum(t["publication_age_seconds"] > protocol["pair_seconds"] for t in triggers),
            "actions": dict(Counter(r["action"] for r in decisions)),
            "legacy_lineage": dict(Counter(r["status"] for r in lineage)),
            "same_legacy_membership": sum(r["same_directional_membership"] for r in lineage),
            "monthly_triggers": dict(sorted(Counter(t["trigger_utc"][:7] for t in triggers).items())),
            "bridge_signals": len(bridged), "engine_evaluations": 0, "engine_admitted": 0,
            "warmup": sum(r["warmup_only"] for r in usage), "check_entries": sum(bool(r["check_folds"]) for r in usage),
            "prefix_checks": len(checks), "prefix_checks_passed": all(r["valid"] for r in checks)}
    result.update(summary=summary, protocol=deepcopy(protocol), provenance=provenance)
    return result


def write_stream(result, output):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("immutable stream output already exists")
    roots = [Path(p).resolve() for p in result["inputs"]["protected_archive_dirs"]]
    if any(output.is_relative_to(root) or root.is_relative_to(output) for root in roots):
        raise ValueError("output overlaps source archive")
    payloads = {f"{name}.json": _json_bytes(result[name]) for name in ("summary", "protocol", "provenance")}
    for name, rows in result.items():
        if isinstance(rows, list):
            payloads[f"{name}.jsonl"] = b"".join(_json_bytes(row) for row in rows)
    import hashlib
    manifest = {"schema_version": "dubai_entry_stream_v1", "engine_dataset_ready": False,
        "inputs": result["inputs"], "environment": result["environment"],
        "artifacts": {name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)} for name, data in payloads.items()}}
    manifest["stream_identity_sha256"] = _hash(manifest)
    payloads["manifest.json"] = _json_bytes(manifest)
    output.mkdir(parents=True)
    for name, data in payloads.items():
        with (output / name).open("xb") as stream:
            stream.write(data)
    return manifest
