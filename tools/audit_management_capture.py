"""Bounded offline audit of management_decision_inputs_v1 capture evidence.

This checks capture completeness, not policy outputs or broker deal parity.
The complete supplied journal is audited before applying the report cutoff.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


# Direct script execution must not import the bot or create bytecode files.
if __name__ == "__main__":
    sys.dont_write_bytecode = True
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.audit_causal_lineage import audit_rows


CONTRACT = "management_decision_inputs_v1"
KINDS = {
    "gold_555_trailing": {"bid", "ask", "tick_time_msc", "open_tickets"},
    "gold_555_leg_protection": {"ticket", "fill_price", "leg_index"},
    "gold_555_basket_guard": {"summary", "now_utc"},
    "dubai_basket_guard": {"summary", "now_utc"},
}
# Frozen v1 capture bindings, copied from the pure strategy runtime contract.
# A new policy requires an explicit validator contract update, not a guess.
POLICIES = {
    "gold_now_555_v1": "555124a24b534aa2abda53ddaaa2ee35fd3afd07e61d05937eb14c80ad0676f0",
    "dubai_balanced_v1": "32cb5c0fe8205ad00a0c655bacd5446c6cc219d1ad7338967212c71781860631",
    "dubai_frontloaded_30m_v1": "d486f5ce418094e862fe3b58e6ccc14068a136ef7116f8a9a80c347083e6dc1c",
    "dubai_frontloaded_40m_v1": "cdee2bdfc53aff748d0b87e1d57301793eeb620a4287916c4494cb6681a070b0",
}
STATE_FIELDS = {
    "status", "requested_close_reason", "all_filled_tickets", "pending_tickets",
    "candidate_hard_stops", "candidate_entry_prices_by_ticket",
    "basket_guard_armed", "basket_guard_triggered", "basket_guard_peak_pl",
    "basket_guard_trigger_reason", "basket_guard_recovery_pending",
    "basket_guard_close_tickets", "candidate_first_fill_at", "timestamp",
}
START = "bot_internal_decision_started"
FINAL = "bot_internal_decision"
REQUESTS = {"mt5_modify_requested", "mt5_close_requested", "mt5_cancel_requested", "mt5_order_requested"}
COMMON = (
    "management_contract", "management_kind", "strategy_id", "strategy_fingerprint",
    "direction", "decision_id", "message_revision_id", "parent_decision_id",
    "decision_reason", "sig", "session_id", "code_commit",
)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _utc(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0):
            return parsed
    except ValueError:
        pass
    return None


def _id(value, prefix):
    return isinstance(value, str) and value.startswith(prefix) and len(value) > len(prefix)


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _json_value(value):
    if value is None or type(value) in (str, bool, int):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _json_value(item) for key, item in value.items())
    return False


def _safe_row(row):
    if not isinstance(row, dict) or not _json_value(row) or not isinstance(row.get("ev"), str):
        return False
    # The existing lineage auditor indexes these values as scalar identities.
    for key in ("event_id", "session_id", "message_revision_id", "decision_id", "parent_decision_id",
                "action_id", "attempt_id", "sig", "coalesced_into_action_id", "supersedes_action_id"):
        if key in row and row[key] is not None and not isinstance(row[key], str):
            return False
    return True


def _inputs_valid(kind, inputs):
    if not isinstance(inputs, dict) or not KINDS.get(kind, set()).issubset(inputs):
        return False
    if kind == "gold_555_trailing":
        tickets = inputs["open_tickets"]
        return (_positive(inputs["bid"]) and _positive(inputs["ask"])
                and inputs["ask"] >= inputs["bid"] and _integer(inputs["tick_time_msc"], 1)
                and (tickets is None or (isinstance(tickets, list)
                     and all(_integer(ticket, 1) for ticket in tickets) and len(tickets) == len(set(tickets)))))
    if kind == "gold_555_leg_protection":
        return (_integer(inputs["ticket"], 1) and _positive(inputs["fill_price"])
                and _integer(inputs["leg_index"]))
    if kind in ("gold_555_basket_guard", "dubai_basket_guard"):
        return isinstance(inputs["summary"], dict) and _utc(inputs["now_utc"]) is not None
    return False


def _precedes(left, right):
    before, after = _utc(left.get("ts")), _utc(right.get("ts"))
    return (before is not None and after is not None and before <= after
            and left.get("session_id") == right.get("session_id")
            and _integer(left.get("monotonic_ns")) and _integer(right.get("monotonic_ns"))
            and left["monotonic_ns"] <= right["monotonic_ns"])


def _state_valid(state):
    return (isinstance(state, dict) and STATE_FIELDS.issubset(state) and _json_value(state)
            and all(state[field] is None or _utc(state[field]) is not None
                    for field in ("timestamp", "candidate_first_fill_at")))


def audit_capture(rows, *, since=None):
    """Pure audit; since selects whole decisions and never truncates ancestry."""
    cutoff = _utc(since) if since is not None else None
    if since is not None and cutoff is None:
        raise ValueError("since must be an ISO timestamp with an explicit UTC offset")
    all_rows = list(rows)
    valid_rows, malformed = [], []
    for index, row in enumerate(all_rows):
        if _safe_row(row):
            valid_rows.append(row)
        else:
            malformed.append({"line_number": index + 1, "reason": "malformed_row"})
    source_hash = hashlib.sha256(_canonical(valid_rows)).hexdigest()
    try:
        journal = audit_rows(valid_rows, source_sha256=source_hash,
                             source_errors=malformed, source_line_count=len(all_rows))
    except (TypeError, ValueError, KeyError, OverflowError, RecursionError) as exc:
        # Malformed evidence must yield a blocked report, never disappear.
        journal = {"summary": {"blocked": 1}, "audit_error_type": type(exc).__name__}
    journal_bad_decisions = {
        row.get("decision_id") for row in journal.get("rows", []) if row["status"] != "complete"
    }

    candidates = set()
    for index, row in enumerate(all_rows):
        if not isinstance(row, dict) or row.get("ev") not in (START, FINAL):
            continue
        if ("management_contract" in row or "management_kind" in row
                or (isinstance(row.get("decision_reason"), str) and row["decision_reason"] in KINDS)):
            decision = row.get("decision_id")
            candidates.add(decision if _id(decision, "decision_") else f"invalid_row_{index}")
    groups = defaultdict(list)
    for index, row in enumerate(all_rows):
        if not isinstance(row, dict):
            continue
        decision = row.get("decision_id")
        key = decision if _id(decision, "decision_") else f"invalid_row_{index}"
        if key in candidates:
            groups[key].append((index, row))

    decisions = []
    all_declared, all_requested, all_pending = set(), set(), set()
    coalescences = 0
    for decision, indexed in sorted(groups.items()):
        if cutoff is not None and not any(_utc(row.get("ts")) is None or _utc(row["ts"]) >= cutoff for _, row in indexed):
            continue
        records = [row for _, row in indexed]
        starts = [row for row in records if row.get("ev") == START]
        finals = [row for row in records if row.get("ev") == FINAL]
        requests = [row for row in records if isinstance(row.get("ev"), str) and row["ev"] in REQUESTS]
        relations = [row for row in records if row.get("ev") == "mt5_action_coalesced"]
        coalescences += len(relations)
        errors = []
        if decision in journal_bad_decisions:
            errors.append("journal_integrity")
        if len(starts) != 1:
            errors.append("start_count")
        if len(finals) != 1:
            errors.append("completion_count")
        identity = (starts or finals or [{}])[0]
        for record in starts + finals:
            if any(key not in record or record.get(key) != identity.get(key) for key in COMMON):
                errors.append("identity_mismatch")
            kind, policy = record.get("management_kind"), record.get("strategy_id")
            if record.get("management_contract") != CONTRACT or not isinstance(kind, str) or kind not in KINDS:
                errors.append("unsupported_contract_or_kind")
            if (not isinstance(policy, str) or policy not in POLICIES
                    or record.get("strategy_fingerprint") != POLICIES.get(policy)):
                errors.append("policy_binding")
            gold = isinstance(kind, str) and kind.startswith("gold_")
            if ((gold and policy != "gold_now_555_v1")
                    or (kind == "dubai_basket_guard" and (not isinstance(policy, str) or not policy.startswith("dubai_")))
                    or not str(record.get("sig", "")).startswith("canal2_" if gold else "canal1_")):
                errors.append("policy_kind_or_channel")
            if record.get("direction") not in ("BUY", "SELL") or record.get("decision_reason") != kind:
                errors.append("direction_or_reason")
            if any(not _id(record.get(field), prefix) for field, prefix in (
                ("decision_id", "decision_"), ("parent_decision_id", "decision_"), ("message_revision_id", "msgrev_"),
            )):
                errors.append("missing_identity")
        for record in starts:
            kind = record.get("management_kind")
            if not isinstance(kind, str) or not _inputs_valid(kind, record.get("decision_inputs")):
                errors.append("invalid_decision_inputs")
            state = record.get("state_before")
            if not _state_valid(state):
                errors.append("invalid_state_before")
            observed, recorded = _utc(record.get("observed_at_utc")), _utc(record.get("ts"))
            if observed is None or recorded is None or observed.replace(microsecond=observed.microsecond // 1000 * 1000) > recorded:
                errors.append("invalid_observed_time")
        declared = set()
        declared_counts = []
        for record in finals:
            ids, count = record.get("declared_action_ids"), record.get("declared_action_count")
            if isinstance(ids, list):
                declared.update(value for value in ids if _id(value, "action_"))
            if _integer(count):
                declared_counts.append(count)
            if (not isinstance(ids, list) or not all(_id(value, "action_") for value in ids)
                    or len(ids) != len(set(value for value in ids if isinstance(value, str)))
                    or not _integer(count) or count != len(ids)):
                errors.append("invalid_action_manifest")
            if record.get("decision_status") != "completed":
                errors.append("decision_not_completed")
            if "error_type" not in record or record["error_type"] is not None:
                errors.append("decision_error")
            if "decision_result" not in record or not _json_value(record.get("decision_result")):
                errors.append("invalid_decision_result")
            state = record.get("state_after")
            if not _state_valid(state):
                errors.append("invalid_state_after")
        requested = [row.get("action_id") for row in requests if _id(row.get("action_id"), "action_")]
        request_set = set(requested)
        if len(requested) != len(requests) or len(requested) != len(request_set):
            errors.append("invalid_or_duplicate_request_ids")
        if declared != request_set:
            errors.append("declared_request_mismatch")
        for row in requests:
            if any(row.get(key) != identity.get(key) for key in ("decision_id", "message_revision_id", "sig", "session_id", "code_commit")):
                errors.append("request_identity_mismatch")
            if not _integer(row.get("action_revision")) or (row.get("ev") != "mt5_order_requested" and not _integer(row.get("ticket"), 1)):
                errors.append("invalid_request_identity")
        if len(starts) == len(finals) == 1:
            if not _precedes(starts[0], finals[0]):
                errors.append("decision_chronology")
            if any(not _precedes(starts[0], row) or not _precedes(row, finals[0]) for row in requests + relations):
                errors.append("request_chronology")
        pending = declared - request_set
        all_declared.update(declared)
        all_requested.update(request_set)
        all_pending.update(pending)
        decisions.append({
            "decision_id": decision, "management_kind": identity.get("management_kind"),
            "sig": identity.get("sig"), "strategy_id": identity.get("strategy_id"),
            "strategy_fingerprint": identity.get("strategy_fingerprint"),
            "start_count": len(starts), "completion_count": len(finals), "request_count": len(requests),
            "declared_action_ids": sorted(declared), "requested_action_ids": sorted(request_set),
            "pending_declared_action_ids": sorted(pending),
            "action_denominator": max(len(declared | request_set), max(declared_counts, default=0)),
            "row_indexes": [index for index, _ in indexed],
            "errors": sorted(set(errors)), "status": "blocked" if errors else "capture_complete",
        })
    journal_blocked = journal["summary"].get("blocked", 0) - journal["summary"].get("empty_selection", 0)
    blocked = bool(malformed or journal_blocked or any(d["errors"] for d in decisions))
    return {
        "schema_version": 1, "management_contract": CONTRACT,
        "status": "blocked" if blocked else "capture_complete" if decisions else "no_decisions_yet",
        "full_live_parity_verified": False, "policy_motor_verified": False, "broker_deals_verified": False,
        "scope": "capture completeness and global causal journal integrity only",
        "selection": {"since": since, "source_rows": len(all_rows), "global_management_decisions": len(groups)},
        "summary": {
            "decisions": len(decisions), "blocked_decisions": sum(bool(d["errors"]) for d in decisions),
            "noop_decisions": sum(not d["errors"] and d["action_denominator"] == 0 for d in decisions),
            "action_denominator": sum(d["action_denominator"] for d in decisions),
            "unique_declared_actions": len(all_declared), "unique_requested_actions": len(all_requested),
            "pending_declared_actions": len(all_pending), "coalescence_rows": coalescences,
            "malformed_rows": len(malformed),
        },
        "malformed_rows": malformed, "journal_integrity": journal, "decisions": decisions,
    }


def _reject_constant(value):
    raise ValueError(f"non-finite JSON value: {value}")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _decode(source):
    rows = []
    for line in source.splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line.decode("utf-8"), parse_constant=_reject_constant, object_pairs_hook=_unique_object))
        except (UnicodeError, ValueError, RecursionError):
            rows.append(None)
    return rows


def _code_hash():
    root = Path(__file__).resolve().parents[1]
    names = ("tools/audit_management_capture.py", "tests/test_audit_management_capture.py",
             "tools/audit_causal_lineage.py", "management_decision_evidence.py", "strategy_runtime_contract.py")
    hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names}
    return {"files": hashes, "sha256": hashlib.sha256(_canonical(hashes)).hexdigest()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--since", help="UTC ISO timestamp; selects decisions after the full journal audit")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.output.exists() or args.events.resolve() == args.output.resolve():
            parser.error("output must be a new report; existing files are never overwritten")
        before = args.events.read_bytes()
        code_before = _code_hash()
        report = audit_capture(_decode(before), since=args.since)
        after = args.events.read_bytes()
        digest_before, digest_after = (hashlib.sha256(value).hexdigest() for value in (before, after))
        report["source"] = {"path": str(args.events.resolve()), "sha256_before": digest_before,
                            "sha256_after": digest_after, "unchanged": digest_before == digest_after,
                            "bytes_before": len(before), "bytes_after": len(after),
                            "line_count": len(before.splitlines())}
        code_after = _code_hash()
        report["code"] = {**code_before, "sha256_after": code_after["sha256"],
                          "unchanged": code_before == code_after}
        report["audited_at_utc"] = datetime.now(timezone.utc).isoformat()
        if digest_before != digest_after or code_before != code_after:
            report["status"] = "blocked"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f"Management capture: {report['status']}; {report['summary']['decisions']} decisions; full_live_parity_verified=false")
    print(f"Report: {args.output.resolve()}")
    return 2 if report["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
