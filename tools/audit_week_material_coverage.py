"""Reconcile bounded material segments against the full native week denominator."""

from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.collect_vm_week_material import build_segments
from tools.audit_native_money_anchor import cents
from tools.plan_vm_week_lifecycle import native_at
from tools.probe_vm_signal_lifecycle import digest, selected_management_fields, utc


AUDITOR_SOURCES = tuple(Path(__file__).with_name(name) for name in (
    "audit_week_material_coverage.py", "collect_vm_week_material.py",
    "probe_vm_signal_lifecycle.py", "plan_vm_week_lifecycle.py",
    "audit_native_money_anchor.py"))


def bind_entries(events, native_entries, clock_status):
    rows = []
    native_tickets = {deal["ticket"] for deal in native_entries}
    successful = [row for row in events if row["ev"] == "mt5_order_result"
                  and row.get("retcode") == 10009]
    extras = [row.get("deal") for row in successful if row.get("deal") not in native_tickets]
    for deal in native_entries:
        matches = [row for row in successful if row.get("deal") == deal["ticket"]]
        issues = []
        clock_order = "not_evaluated"
        if len(matches) != 1:
            issues.append("native_entry_has_no_unique_success_result")
        else:
            result = matches[0]
            if (result.get("order") != deal["order"] or result.get("retcode") != 10009
                    or result.get("direction") != {0: "BUY", 1: "SELL"}.get(deal["type"])):
                issues.append("native_entry_order_or_direction_mismatch")
            try:
                if (Decimal(str(result["price"])) != Decimal(str(deal["price"]))
                        or Decimal(str(result["volume"])) != Decimal(str(deal["volume"]))):
                    issues.append("native_entry_price_or_volume_mismatch")
            except (KeyError, ValueError, TypeError, ArithmeticError):
                issues.append("native_entry_price_or_volume_missing")
            lineage = tuple(result.get(key) for key in ("action_id", "attempt_id", "decision_id", "session_id"))
            if any(not isinstance(value, str) or not value for value in lineage):
                issues.append("native_entry_result_lineage_missing")
            requests = [row for row in events if row["ev"] == "mt5_order_requested"
                        and tuple(row.get(key) for key in ("action_id", "attempt_id", "decision_id", "session_id")) == lineage]
            attempts = [row for row in events if row["ev"] == "mt5_action_attempt"
                        and row.get("operation") == "OPEN_MARKET"
                        and tuple(row.get(key) for key in ("action_id", "attempt_id", "decision_id", "session_id")) == lineage]
            if len(requests) != 1 or len(attempts) != 1:
                issues.append("native_entry_request_or_attempt_lineage_missing")
            else:
                request, attempt = requests[0], attempts[0]
                try:
                    if (Decimal(str(request["lot"])) != Decimal(str(deal["volume"]))
                            or request.get("direction") != result.get("direction")
                            or attempt.get("broker_request_sent") is not True
                            or attempt.get("result_retcode") != 10009):
                        issues.append("native_entry_request_or_attempt_facts_mismatch")
                except (KeyError, ValueError, TypeError, ArithmeticError):
                    issues.append("native_entry_request_or_attempt_facts_missing")
                if clock_status == "direct_for_all_native_days":
                    try:
                        ordered = (utc(request["ts"]) <= utc(attempt["broker_request_started_utc"])
                                   <= native_at(deal["time_msc"]) <= utc(attempt["broker_response_received_utc"])
                                   <= utc(result["ts"]))
                        clock_order = "inside_call" if ordered else "cross_clock_order_anomaly"
                    except (KeyError, ValueError, TypeError):
                        clock_order = "clock_evidence_missing"
                else:
                    clock_order = "offset_hypothesis"
        rows.append({"native_deal": deal["ticket"], "native_order": deal["order"],
                     "status": "bound_result_request_attempt" if not issues else "blocked",
                     "issues": issues, "clock_order": clock_order})
    return rows, extras


def summarize_modification_actions(events, *, complete):
    kinds = {"mt5_modify_requested", "mt5_action_attempt", "mt5_modify_confirmed",
             "mt5_position_snapshot", "mt5_action_coalesced"}
    groups = {}
    orphan_attempts = 0
    for event in events:
        kind = event.get("ev")
        if kind not in kinds or (kind == "mt5_action_attempt"
                                  and event.get("operation") != "MODIFY_SLTP") or (
                kind == "mt5_position_snapshot" and event.get("after_action") != "MODIFY_SLTP"):
            continue
        key = (event.get("action_id"), event.get("ticket"))
        if not isinstance(key[0], str) or not key[0] or type(key[1]) is not int:
            if kind == "mt5_action_attempt":
                orphan_attempts += 1
            continue
        groups.setdefault(key, {name: [] for name in kinds})[kind].append(event)
    requested = {key: group for key, group in groups.items() if group["mt5_modify_requested"]}
    orphan_attempts += sum(len(group["mt5_action_attempt"]) for key, group in groups.items()
                           if key not in requested)
    result = {"status": "diagnostic_only" if complete else "incomplete_segments",
              "requested_action_count": len(requested),
              "attempt_count": sum(len(group["mt5_action_attempt"]) for group in groups.values()),
              "orphan_attempt_count": orphan_attempts,
              "actions": [], "statuses": {}, "full_source_time_coverage_verified": False,
              "limitations": ["Requested protection is not broker-installed protection.",
                              "A post-accept position snapshot bounds an observed level, not its server install time.",
                              "Initial SL/TP attached to a market order is outside this modification series."]}
    if not complete:
        return result

    def observed_chain(request, attempt, confirm, snapshot):
        if (attempt.get("broker_request_sent") is not True
                or attempt.get("result_retcode") != 10009
                or confirm.get("retcode") != 10009
                or snapshot.get("retcode") != 10009
                or snapshot.get("position_exists") is not True):
            return False
        if any(not isinstance(request.get(field), str) or not request[field]
               for field in ("action_id", "decision_id", "session_id")):
            return False
        lineage = ("action_id", "ticket", "decision_id", "session_id")
        if any(any(row.get(field) != request.get(field) for field in lineage)
               for row in (attempt, confirm, snapshot)):
            return False
        if not attempt.get("attempt_id") or any(row.get("attempt_id") != attempt["attempt_id"]
                                                 for row in (confirm, snapshot)):
            return False
        try:
            if not (utc(request["ts"]) <= utc(attempt["broker_request_started_utc"])
                    <= utc(attempt["broker_response_received_utc"]) <= utc(confirm["ts"])
                    <= utc(snapshot["ts"])):
                return False
            levels = [level for level in ("sl", "tp") if request.get(f"new_{level}") is not None]
            return bool(levels) and all(
                Decimal(str(request[f"new_{level}"]))
                == Decimal(str(attempt[f"request_{level}"]))
                == Decimal(str(snapshot[level]))
                and (confirm.get(f"new_{level}") is None
                     or Decimal(str(confirm[f"new_{level}"]))
                     == Decimal(str(request[f"new_{level}"]))) for level in levels)
        except (KeyError, ValueError, TypeError, InvalidOperation):
            return False

    for (action_id, ticket), group in sorted(requested.items()):
        requests, attempts = group["mt5_modify_requested"], group["mt5_action_attempt"]
        confirms, snapshots = group["mt5_modify_confirmed"], group["mt5_position_snapshot"]
        successful = [row for row in attempts if row.get("result_retcode") == 10009]
        observed = (len(requests) == len(successful) == len(confirms) == len(snapshots) == 1
                    and observed_chain(requests[0], successful[0], confirms[0], snapshots[0]))
        if observed:
            status = "observed_after_accepted_modify"
        elif any(row.get("result_retcode") == 10009 for row in attempts):
            status = "accepted_without_matching_snapshot"
        elif attempts and not snapshots and all(row.get("broker_request_sent") is True
                              and type(row.get("result_retcode")) is int
                              and row["result_retcode"] != 10009 for row in attempts) and not confirms:
            status = "rejected_without_accepted_modify"
        elif not attempts and not confirms and not snapshots:
            status = ("coalesced_without_direct_attempt" if group["mt5_action_coalesced"]
                      else "requested_without_direct_attempt")
        else:
            status = "unresolved_modify_lineage"
        result["actions"].append({"action_id": action_id, "ticket": ticket,
                                  "status": status, "request_count": len(requests),
                                  "attempt_count": len(attempts),
                                  "attempt_retcodes": dict(sorted(Counter(
                                      str(row.get("result_retcode")) for row in attempts).items())),
                                  "confirmation_count": len(confirms),
                                  "post_modify_snapshot_count": len(snapshots),
                                  "coalesced_count": len(group["mt5_action_coalesced"]),
                                  "server_install_time_known": False})
    result["statuses"] = dict(sorted(Counter(row["status"] for row in result["actions"]).items()))
    return result


def prior_protection_snapshot(events, deal, clock_status):
    level = "sl" if deal["reason"] == 4 else "tp"
    try:
        exit_at = native_at(deal["time_msc"])
        candidates = [row for row in events if row["ev"] == "mt5_position_snapshot"
                      and row.get("ticket") == deal["position_id"]
                      and row.get("after_action") == "MODIFY_SLTP"
                      and row.get("position_exists") is True
                      and row.get(level) is not None and utc(row["ts"]) <= exit_at]
    except (KeyError, ValueError, TypeError):
        return {"status": "native_clock_or_snapshot_time_missing", "chain_verified": False}
    if not candidates:
        return {"status": "no_prior_observed_protection_snapshot", "chain_verified": False}
    snapshot = max(candidates, key=lambda row: utc(row["ts"]))
    attempt = [row for row in events if row["ev"] == "mt5_action_attempt"
               and row.get("operation") == "MODIFY_SLTP"
               and row.get("action_id") == snapshot.get("action_id")
               and row.get("attempt_id") == snapshot.get("attempt_id")
               and row.get("ticket") == deal["position_id"]]
    request = [row for row in events if row["ev"] == "mt5_modify_requested"
               and row.get("action_id") == snapshot.get("action_id")
               and row.get("ticket") == deal["position_id"]]
    confirmed = [row for row in events if row["ev"] == "mt5_modify_confirmed"
                 and row.get("action_id") == snapshot.get("action_id")
                 and row.get("attempt_id") == snapshot.get("attempt_id")
                 and row.get("ticket") == deal["position_id"]]
    try:
        observed_level = Decimal(str(snapshot[level]))
        exit_price = Decimal(str(deal["price"]))
        request_level = Decimal(str(request[0].get(f"new_{level}"))) if len(request) == 1 else None
        attempt_level = Decimal(str(attempt[0].get(f"request_{level}"))) if len(attempt) == 1 else None
    except (InvalidOperation, KeyError, TypeError, ValueError):
        return {"status": "protection_level_evidence_invalid", "chain_verified": False}
    chronology = False
    if len(attempt) == len(request) == len(confirmed) == 1:
        try:
            chronology = (utc(request[0]["ts"]) <= utc(attempt[0]["broker_request_started_utc"])
                          <= utc(attempt[0]["broker_response_received_utc"])
                          <= utc(confirmed[0]["ts"]) <= utc(snapshot["ts"]) <= exit_at)
        except (KeyError, TypeError, ValueError):
            pass
    chain = (chronology and snapshot.get("retcode") == 10009
             and confirmed[0].get("retcode") == 10009
             and attempt[0].get("result_retcode") == 10009
             and attempt[0].get("broker_request_sent") is True
             and attempt_level == request_level == observed_level)
    status = ("observed_after_accepted_modify" if clock_status == "direct_for_all_native_days"
              else "observed_after_accepted_modify_clock_hypothesis") if chain else "snapshot_lineage_unverified"
    return {"status": status, "chain_verified": chain, "observed_at_utc": snapshot["ts"],
            "observed_level": str(observed_level),
            "native_exit_price_minus_observed_level": str(exit_price - observed_level),
            "native_clock_status": clock_status,
            "server_install_time_known": False}


def classify_exits(events, native_exits, clock_status="direct_for_all_native_days"):
    rows = []
    for deal in native_exits:
        ticket = deal["position_id"]
        identity_matched = False
        identity_issues = []
        if deal["reason"] in (4, 5):
            status = "native_sl" if deal["reason"] == 4 else "native_tp"
            candidate_count = 0
            protection = prior_protection_snapshot(events, deal, clock_status)
        elif deal["reason"] == 3:
            protection = None
            requests = [row for row in events if row["ev"] == "mt5_close_requested" and row.get("ticket") == ticket]
            attempts = [row for row in events if row["ev"] == "mt5_action_attempt"
                        and row.get("operation") == "CLOSE_POSITION" and row.get("ticket") == ticket
                        and row.get("broker_request_sent") is True and row.get("result_retcode") == 10009]
            confirmations = [row for row in events if row["ev"] == "mt5_close_result"
                             and row.get("ticket") == ticket and row.get("retcode") == 10009]
            chains = [(request, attempt, confirmation)
                      for request in requests for attempt in attempts for confirmation in confirmations
                      if request.get("action_id") == attempt.get("action_id") == confirmation.get("action_id")
                      and attempt.get("attempt_id") == confirmation.get("attempt_id")]
            candidate_count = len(chains)
            claims = [chain for chain in chains if chain[1].get("result_deal") == deal["ticket"]]
            if claims:
                if (len(claims) != 1 or sum(row.get("result_deal") == deal["ticket"]
                                            for row in events if row["ev"] == "mt5_action_attempt") != 1):
                    identity_issues.append("deal_claim_not_unique")
                else:
                    request, attempt, confirmation = claims[0]
                    lineage = tuple(attempt.get(key) for key in ("action_id", "decision_id", "session_id"))
                    if (any(not isinstance(value, str) or not value for value in lineage)
                            or any(tuple(row.get(key) for key in ("action_id", "decision_id", "session_id")) != lineage
                                   for row in (request, confirmation))):
                        identity_issues.append("close_lineage_mismatch")
                    if (attempt.get("result_order") != deal.get("order")
                            or attempt.get("request_position") != ticket
                            or attempt.get("request_action") != 1
                            or attempt.get("request_type") != deal.get("type")):
                        identity_issues.append("close_order_request_or_direction_mismatch")
                    try:
                        amount = Decimal(str(deal["volume"]))
                        price = Decimal(str(deal["price"]))
                        if (amount <= 0 or price <= 0
                                or Decimal(str(attempt["request_volume"])) != amount
                                or Decimal(str(attempt["result_volume"])) != amount
                                or Decimal(str(attempt["result_price"])) != price):
                            identity_issues.append("close_price_or_volume_mismatch")
                    except (KeyError, InvalidOperation, TypeError, ValueError):
                        identity_issues.append("close_price_or_volume_missing")
                    if clock_status == "direct_for_all_native_days":
                        try:
                            ordered = (utc(request["ts"]) <= utc(attempt["broker_request_started_utc"])
                                       <= native_at(deal["time_msc"])
                                       <= utc(attempt["broker_response_received_utc"])
                                       <= utc(attempt["ts"]) <= utc(confirmation["ts"]))
                            if not ordered:
                                identity_issues.append("close_clock_order_mismatch")
                        except (KeyError, TypeError, ValueError):
                            identity_issues.append("close_clock_evidence_missing")
                    identity_matched = not identity_issues
                status = ("expert_close_deal_identity_matched_order_history_missing"
                          if identity_matched and clock_status == "direct_for_all_native_days"
                          else "expert_close_deal_identity_matched_clock_hypothesis"
                          if identity_matched else "expert_close_deal_identity_mismatch")
            else:
                if candidate_count == 1 and chains[0][1].get("result_deal") is not None:
                    status = "expert_close_deal_identity_mismatch"
                    identity_issues.append("successful_attempt_claims_other_deal")
                else:
                    status = ("expert_close_candidate_without_deal_identity" if candidate_count == 1
                              else "expert_close_trace_missing_or_ambiguous")
        else:
            status = "native_exit_reason_unsupported"
            candidate_count = 0
            protection = None
        rows.append({"native_deal": deal["ticket"], "position_id": ticket,
                     "native_reason": deal["reason"], "status": status,
                     "protection_observation": protection,
                     "close_chain_candidates": candidate_count,
                     "native_deal_identity_matched": identity_matched,
                     "close_identity_issues": identity_issues,
                     "exact_native_close_binding_verified": False})
    return rows


def pair_management_evaluations(events):
    starts, completions, issues = {}, {}, []
    event_ids = [row.get("event_id") for row in events]
    if any(not isinstance(value, str) or not value for value in event_ids) or len(event_ids) != len(set(event_ids)):
        issues.append("management_event_identity_missing_or_duplicate")
    for row in events:
        decision_id = row.get("decision_id")
        if not isinstance(decision_id, str) or not decision_id:
            issues.append("management_decision_identity_missing")
            continue
        target = starts if row.get("ev") == "bot_internal_decision_started" else completions
        if decision_id in target:
            issues.append("management_decision_event_duplicated")
        target[decision_id] = row
    if starts.keys() != completions.keys():
        issues.append("management_start_or_completion_missing")
    error_count = 0
    for decision_id in starts.keys() & completions.keys():
        start, final = starts[decision_id], completions[decision_id]
        identity = ("sig", "session_id", "code_commit", "decision_id", "message_revision_id",
                    "parent_decision_id", "management_contract", "management_kind", "strategy_id",
                    "strategy_fingerprint", "direction")
        if (any(start.get(key) != final.get(key) for key in identity)
                or any(not start.get(key) for key in ("sig", "session_id", "code_commit",
                                                    "management_kind", "strategy_id",
                                                    "strategy_fingerprint", "direction"))):
            issues.append("management_pair_lineage_mismatch")
        try:
            if utc(start["ts"]) > utc(final["ts"]):
                issues.append("management_pair_time_reversed")
        except (KeyError, TypeError, ValueError):
            issues.append("management_pair_time_missing")
        declared = final.get("declared_action_ids")
        if (not isinstance(declared, list) or final.get("declared_action_count") != len(declared)
                or final.get("decision_status") not in {"completed", "error"}):
            issues.append("management_completion_invalid")
        if final.get("decision_status") == "error":
            error_count += 1
    return {"status": "blocked" if issues else "paired" if events else "no_evaluations_observed",
            "started": len(starts), "completed": len(completions), "errors": error_count,
            "issues": sorted(set(issues))}


def audit(plan, native, segment_reports, risk=None):
    segments = build_segments(plan)
    source_deals = {row["ticket"]: row for row in native["deals"]}
    if len(source_deals) != len(native["deals"]):
        raise ValueError("duplicate native deal ticket")
    risk_rows = {}
    if risk is not None:
        risk_rows = {row["signal_id"]: row for row in risk["baskets"]}
        if (risk.get("contract") != "native_week_full_tick_risk_diagnostic_v1"
                or risk.get("basket_count") != 40 or len(risk_rows) != 40
                or set(risk_rows) != {row["signal_id"] for row in plan["rows"]}
                or native.get("currency") != "EUR"):
            raise ValueError("native risk reference contract, denominator or currency mismatch")
    by_index = {}
    protocols = set()
    for report in segment_reports:
        index = report["segment_index"]
        if index in by_index or not 0 <= index < len(segments):
            raise ValueError("duplicate or invalid collected segment index")
        if (report.get("contract") not in {"bounded_vm_week_material_segment_v1",
                                          "bounded_vm_week_material_segment_v2",
                                          "bounded_vm_week_material_segment_v3"}
                or report.get("segment_count") != len(segments)
                or report.get("segment") != segments[index]
                or report.get("material_records_complete_within_segment") is not True
                or sum(row["count"] for row in report["material_event_counts"]) != len(report["events"])):
            raise ValueError("material segment contract or counts inconsistent")
        if report["contract"] in {"bounded_vm_week_material_segment_v2",
                                   "bounded_vm_week_material_segment_v3"} and (
                report.get("management_records_complete_within_segment") is not True
                or sum(row["count"] for row in report["management_event_counts"])
                != len(report["management_events"])):
            raise ValueError("management segment contract or counts inconsistent")
        if report["contract"] == "bounded_vm_week_material_segment_v3" and (
                report.get("indexed_prefix_time_coverage_verified") is not True
                or report.get("full_source_time_coverage_verified") is not False
                or not isinstance(report.get("index_sha256"), str)
                or not report["index_sha256"]):
            raise ValueError("indexed prefix proof or admission state missing")
        start, end = utc(segments[index]["start_utc"]), utc(segments[index]["end_utc"])
        for event in report["events"]:
            if (event.get("sig") not in segments[index]["active_signals"]
                    or not start <= utc(event["ts"]) < end
                    or not event.get("event_id")):
                raise ValueError("material segment event identity or time outside plan")
        if report["contract"] in {"bounded_vm_week_material_segment_v2",
                                  "bounded_vm_week_material_segment_v3"}:
            for event in report["management_events"]:
                if (event.get("sig") not in segments[index]["active_signals"]
                        or not start <= utc(event["ts"]) < end
                        or not event.get("event_id")
                        or selected_management_fields(event) != event):
                    raise ValueError("management segment event identity or time outside plan")
        protocols.add((report["contract"], tuple(sorted(report["local_inputs_sha256"].items())),
                       report.get("index_sha256")))
        by_index[index] = report
    if len(protocols) > 1:
        raise ValueError("mixed material collector versions")
    rows = []
    for basket in plan["rows"]:
        signal = basket["signal_id"]
        expected = [index for index, segment in enumerate(segments) if signal in segment["active_signals"]]
        missing = [index for index in expected if index not in by_index]
        events = [event for index in expected if index in by_index
                  for event in by_index[index]["events"] if event["sig"] == signal]
        management_events = [event for index in expected if index in by_index
                             for event in by_index[index].get("management_events", [])
                             if event["sig"] == signal]
        event_ids = [event["event_id"] for event in events]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError(f"material event duplicated across segments: {signal}")
        observed = [source_deals[ticket] for ticket in basket["native_deal_tickets"]]
        native_entries = [row for row in observed if row["entry"] == 0]
        native_exits = [row for row in observed if row["entry"] == 1]
        if missing:
            binding = "not_evaluated_incomplete_material_segments"
            entry_rows, extra_results, exit_rows = [], [], []
            management_capture = {"status": "not_evaluated_incomplete_segments"}
        else:
            entry_rows, extra_results = bind_entries(events, native_entries, basket["clock_status"])
            exit_rows = classify_exits(events, native_exits, basket["clock_status"])
            binding = ("native_entries_bound_to_request_attempt_result"
                       if not extra_results and all(row["status"] == "bound_result_request_attempt" for row in entry_rows)
                       else "opening_result_native_deal_mismatch")
            management_capture = (pair_management_evaluations(management_events)
                                  if all(by_index[index]["contract"] in {
                                      "bounded_vm_week_material_segment_v2",
                                      "bounded_vm_week_material_segment_v3"}
                                         for index in expected)
                                  else {"status": "not_collected_v1"})
        attempts = Counter((event.get("operation"), event.get("result_retcode"))
                           for event in events if event["ev"] == "mt5_action_attempt")
        modification_evidence = summarize_modification_actions(events, complete=not missing)
        risk_reference = None
        if risk is not None:
            reference = risk_rows[signal]
            path = reference["path"]
            net = sum((cents(deal.get("profit", 0)) + cents(deal.get("commission", 0))
                       + cents(deal.get("swap", 0)) + cents(deal.get("fee", 0))
                       for deal in observed), Decimal(0))
            if (net != Decimal(str(reference["native_net_eur"]))
                    or reference["direct_clock_anchor_for_all_event_days"]
                    != (basket["clock_status"] == "direct_for_all_native_days")):
                raise ValueError(f"native risk reference contradicts ledger or clock: {signal}")
            metrics = path.get("metrics") if not path.get("blockers") else None
            risk_reference = {"status": "diagnostic_only", "account_currency": "EUR",
                              "net_eur": str(net), "sample_count": path["sample_count"],
                              "known_sample_count": path["known_sample_metrics"]["known_samples"],
                              "fx_coverage_mode": path["fx_coverage_mode"],
                              "blockers": path["blockers"],
                              "max_drawdown_eur": metrics.get("max_drawdown") if metrics else None,
                              "minimum_from_origin_eur": metrics.get("minimum_from_origin") if metrics else None,
                              "max_gross_volume": metrics.get("max_gross_volume") if metrics else None,
                              "sample_stream_sha256": path["sample_stream_sha256"]}
        rows.append({"signal_id": signal, "channel": basket["channel"],
                     "clock_status": basket["clock_status"],
                     "expected_segment_count": len(expected), "collected_segment_count": len(expected) - len(missing),
                     "missing_segment_indices": missing, "material_event_count_partial": len(events),
                     "material_event_count_complete": len(events) if not missing else None,
                     "management_event_count_partial": len(management_events),
                     "management_capture": management_capture,
                     "native_position_count": basket["position_count"],
                     "native_deal_count": basket["deal_count"],
                     "native_entry_deal_count": len(native_entries),
                     "native_exit_deal_count": len(native_exits),
                     "opening_binding": binding,
                     "entry_facts": entry_rows, "unexpected_success_result_deals": extra_results,
                     "exit_facts": exit_rows,
                     "exact_exit_binding_verified": False,
                     "native_risk_reference": risk_reference,
                     "signal_closed_seen_in_collected_segments": any(event["ev"] == "signal_closed" for event in events),
                     "modification_evidence": modification_evidence,
                     "action_attempts_in_collected_segments": [
                         {"operation": operation, "retcode": retcode, "count": count}
                         for (operation, retcode), count in sorted(attempts.items(), key=lambda pair: str(pair[0]))]})
    covered = sum(not row["missing_segment_indices"] for row in rows)
    bound = sum(row["opening_binding"] == "native_entries_bound_to_request_attempt_result" for row in rows)
    management_covered = sum(row["management_capture"]["status"] == "paired" for row in rows)
    source_time_verified = (len(by_index) == len(segments)
                            and all(report["contract"] == "bounded_vm_week_material_segment_v4"
                                    and report.get("full_source_time_coverage_verified") is True
                                    for report in by_index.values()))
    return {"contract": "week_native_material_coverage_v1", "status": "diagnostic_only",
            "native_basket_count": len(rows), "planned_segment_count": len(segments),
            "collected_segment_count": len(by_index), "missing_segment_indices": sorted(set(range(len(segments))) - set(by_index)),
            "fully_covered_baskets": covered, "opening_bound_baskets": bound,
            "management_paired_baskets": management_covered,
            "rows": rows, "full_cohort_coverage_verified": covered == len(rows) and source_time_verified,
            "full_source_time_coverage_verified": source_time_verified,
            "full_simulator_path_parity_verified": False,
            "native_risk_reference_attached": risk is not None,
            "native_risk_global_clock_admitted": risk.get("clock_admitted") if risk is not None else None,
            "limitations": ["Collected v1/v2 time windows do not prove full-source coverage without a complete byte index.",
                            "A covered window is material-journal coverage, not complete raw-state or quote evidence.",
                            "Native time conversion remains provisional on days without direct clock anchors.",
                            "A requested or rejected protection change is not an installed broker stop.",
                            "Broker stop installation and full risk-path comparison are separate gates."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--deals", type=Path, required=True)
    parser.add_argument("--risk", type=Path)
    parser.add_argument("--segment", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    if plan["inputs_sha256"].get(str(args.deals)) != digest(args.deals):
        raise ValueError("material coverage native source mismatch")
    native = json.loads(args.deals.read_text(encoding="utf-8"))
    risk = json.loads(args.risk.read_text(encoding="utf-8")) if args.risk else None
    if risk is not None:
        basket_paths = [path for path in plan["inputs_sha256"] if Path(path).name == "native_week_reconciled.json"]
        if (len(basket_paths) != 1 or risk["inputs_sha256"].get(str(args.deals)) != digest(args.deals)
                or risk["inputs_sha256"].get(basket_paths[0]) != plan["inputs_sha256"][basket_paths[0]]):
            raise ValueError("native risk reference source hash mismatch")
    reports = []
    for path in args.segment:
        report = json.loads(path.read_text(encoding="utf-8"))
        if report["local_inputs_sha256"].get(str(args.plan)) != digest(args.plan):
            raise ValueError("material segment plan source mismatch")
        reports.append(report)
    result = audit(plan, native, reports, risk=risk)
    result["inputs_sha256"] = {str(path): digest(path) for path in
                               (args.plan, args.deals, *((args.risk,) if args.risk else ()),
                                *args.segment, *AUDITOR_SOURCES)}
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(result, target, indent=2, ensure_ascii=True)
        target.write("\n")
    print(json.dumps({key: result[key] for key in ("native_basket_count", "planned_segment_count",
                                                   "collected_segment_count", "fully_covered_baskets",
                                                   "opening_bound_baskets")}))


if __name__ == "__main__":
    main()
