"""Write immutable pure-guard diagnostics from local or bounded VM evidence."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import timedelta
from decimal import Decimal
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research.captured_guard_replay import verify_captured_guard
from tools.audit_week_material_coverage import pair_management_evaluations
from tools.collect_vm_week_material import build_segments, validate_segment_result
from tools.audit_native_money_anchor import cents
from tools.plan_vm_week_lifecycle import native_at
from tools.probe_vm_signal_lifecycle import digest, selected_management_fields, utc
from research.risk_metrics import money_path_metrics


MAX_JOURNAL_BYTES = 200_000_000
MAX_CAPTURED_EVENTS = 100_000
GUARDS = {"gold_555_basket_guard", "dubai_basket_guard"}
JOURNAL_MILLISECOND_UTC = re.compile(r"\.\d{3}(?:Z|[+-]00:00)$")
SOURCES = (Path(__file__), Path(__file__).with_name("probe_vm_signal_lifecycle.py"),
           Path(__file__).resolve().parents[1] / "research" / "captured_guard_replay.py",
           Path(__file__).resolve().parents[1] / "basket_management.py",
           Path(__file__).resolve().parents[1] / "gold_555_live_candidate.py",
           Path(__file__).resolve().parents[1] / "dubai_live_candidate.py")
SEGMENT_SOURCES = SOURCES + (Path(__file__).with_name("collect_vm_week_material.py"),
                             Path(__file__).with_name("audit_week_material_coverage.py"),
                             Path(__file__).with_name("audit_native_money_anchor.py"),
                             Path(__file__).with_name("plan_vm_week_lifecycle.py"),
                             Path(__file__).resolve().parents[1] / "research" / "risk_metrics.py")


def summarize_guard_risk(events):
    rows, blockers, known, volumes = [], Counter(), [], []
    previous_at = None
    guard_starts = [event for event in events
                    if event.get("ev") == "bot_internal_decision_started"
                    and event.get("management_kind") in GUARDS]
    decision_counts = Counter(event.get("decision_id") for event in guard_starts)
    for event in guard_starts:
        row = {"decision_id": event.get("decision_id"), "event_id": event.get("event_id"),
               "status": "blocked", "issues": []}
        decision_id = event.get("decision_id")
        if not isinstance(decision_id, str) or not decision_id or decision_counts[decision_id] != 1:
            row["issues"].append("guard_decision_identity_duplicated_or_missing")
        inputs = event.get("decision_inputs")
        summary = inputs.get("summary") if isinstance(inputs, dict) else None
        try:
            observed_at = utc(inputs["now_utc"])
            capture_at = utc(event["ts"])
            capture_upper = (capture_at + timedelta(milliseconds=1)
                             if JOURNAL_MILLISECOND_UTC.search(str(event["ts"]))
                             else capture_at)
            if (observed_at >= capture_upper if capture_upper != capture_at
                    else observed_at > capture_at):
                row["issues"].append("observation_after_capture_start")
            if previous_at is not None and observed_at < previous_at:
                row["issues"].append("observation_clock_regression")
            previous_at = observed_at
            row["guard_evaluated_at_utc"] = observed_at.isoformat()
        except (KeyError, TypeError, ValueError):
            row["issues"].append("observation_clock_invalid")
        if not isinstance(summary, dict):
            row["issues"].append("summary_missing")
        else:
            tick_msc = summary.get("source_tick_time_msc")
            if tick_msc is not None:
                if type(tick_msc) is int and tick_msc > 0:
                    row["source_tick_time_msc"] = tick_msc
                else:
                    row["issues"].append("source_tick_identity_invalid")
            interval = tuple(summary.get(key) for key in (
                "positions_read_started_utc", "positions_read_completed_utc",
                "positions_read_elapsed_ms"))
            if any(value is not None for value in interval):
                try:
                    started, completed = utc(interval[0]), utc(interval[1])
                    if type(interval[2]) not in (int, float):
                        raise ValueError("position read elapsed time invalid")
                    elapsed_ms = float(interval[2])
                    if (started > completed or completed > utc(inputs["now_utc"])
                            or completed > utc(event["ts"])
                            or not math.isfinite(elapsed_ms) or elapsed_ms < 0):
                        raise ValueError("position read interval inverted or late")
                    row.update(positions_read_started_utc=started.isoformat(),
                               positions_read_completed_utc=completed.isoformat(),
                               positions_read_elapsed_ms=elapsed_ms)
                    wall_ms = (completed - started).total_seconds() * 1000
                    tolerance_ms = max(10.0, min(100.0, elapsed_ms * 0.01))
                    if abs(wall_ms - elapsed_ms) > tolerance_ms:
                        row["issues"].append("positions_read_wall_monotonic_disagree")
                except (KeyError, TypeError, ValueError):
                    row["issues"].append("positions_read_interval_invalid")
            volume = None
            tickets = summary.get("open_tickets")
            count = summary.get("n_open")
            if (summary.get("positions_complete") is not True
                    or type(count) is not int or count < 0
                    or not isinstance(tickets, list) or len(tickets) != count
                    or any(type(ticket) is not int or ticket <= 0 for ticket in tickets)
                    or len(tickets) != len(set(tickets))):
                row["issues"].append("open_exposure_incomplete")
            if summary.get("realized_complete") is not True:
                row["issues"].append("realized_money_incomplete")
            try:
                floating = cents(summary["floating_pl"])
                realized = cents(summary["realized_pl"])
                total = cents(summary["total_pl"])
                if floating + realized != total:
                    row["issues"].append("money_components_mismatch")
                row.update(floating_eur=str(floating), realized_eur=str(realized),
                           observed_total_eur=str(total), open_tickets=tickets)
                if summary.get("lots_total") is not None:
                    volume = Decimal(str(summary["lots_total"]))
                    if not volume.is_finite() or volume < 0:
                        row["issues"].append("open_volume_invalid")
                    else:
                        row["observed_open_volume"] = str(volume)
            except (KeyError, TypeError, ValueError, ArithmeticError):
                row["issues"].append("account_money_missing_or_invalid")
        if not row["issues"]:
            row["status"] = "observed"
            known.append(Decimal(row["observed_total_eur"]))
            if volume is not None:
                volumes.append(volume)
        blockers.update(row["issues"])
        rows.append(row)
    metrics = money_path_metrics(known, origin=Decimal(0))
    sampled = ({"minimum_from_origin_eur": str(metrics["minimum_from_origin"]),
                "maximum_from_origin_eur": str(metrics["maximum_from_origin"]),
                "max_drawdown_eur": str(metrics["max_drawdown"]),
                "last_observed_total_eur": str(metrics["final_net"])} if metrics else None)
    return {"status": "no_guard_evaluations" if not rows else "sampled_observed" if not blockers else "partial",
            "evaluation_count": len(rows), "known_sample_count": len(known),
            "blocked_sample_count": len(rows) - len(known), "blockers": dict(sorted(blockers.items())),
            "known_sample_metrics": sampled, "all_guard_samples_metrics": sampled if not blockers else None,
            "max_observed_open_volume": str(max(volumes)) if volumes else None,
            "rows": rows, "full_live_path_parity_verified": False,
            "native_position_universe_verified": False,
            "bounded_read_interval_sample_count": sum(
                "positions_read_started_utc" in row for row in rows),
            "observation_clock_semantics": (
                "mt5_position_read_bracket_when_present_else_pre_guard_unknown"
                if any("positions_read_started_utc" in row for row in rows)
                else "mt5_read_completed_before_guard_evaluation_exact_read_time_unknown"),
            "scope": "mt5_observed_bot_known_positions_at_guard_samples_not_between_sample_extrema"}


def compare_native_ticket_universe(risk_rows, basket, native):
    """Compare ticket sets at guard time; the earlier MT5 read time remains unknown."""
    if native.get("currency") != "EUR" or len(risk_rows) > MAX_CAPTURED_EVENTS:
        raise ValueError("native account currency or guard sample budget invalid")
    source = native.get("deals")
    if not isinstance(source, list):
        raise ValueError("native deals missing")
    by_ticket = {deal["ticket"]: deal for deal in source}
    if len(by_ticket) != len(source):
        raise ValueError("native deal identity duplicated")
    tickets = basket.get("native_deal_tickets")
    if (not isinstance(tickets, list) or len(tickets) != basket.get("deal_count")
            or len(set(tickets)) != len(tickets) or any(ticket not in by_ticket for ticket in tickets)):
        raise ValueError("basket native deal denominator inconsistent")
    deals = []
    for ticket in tickets:
        deal = by_ticket[ticket]
        position = deal.get("position_id")
        entry = deal.get("entry")
        volume = Decimal(str(deal.get("volume")))
        if (type(position) is not int or position <= 0 or entry not in (0, 1)
                or not volume.is_finite() or volume <= 0):
            raise ValueError("native position deal invalid")
        deals.append((native_at(deal["time_msc"]), ticket, position, entry, volume))
    deals.sort()
    balances = {}
    for _, _, position, entry, volume in deals:
        remaining = balances.get(position, Decimal(0)) + (volume if entry == 0 else -volume)
        if remaining < 0:
            raise ValueError("native close exceeds position volume")
        balances[position] = remaining
    if len(balances) != basket.get("position_count") or any(balances.values()):
        raise ValueError("native basket positions or closures incomplete")
    receipt = utc(basket["receipt_utc"])
    rows = []
    for sample in risk_rows:
        row = {"decision_id": sample.get("decision_id"), "status": "blocked_guard_sample"}
        if sample.get("status") != "observed":
            pass
        elif basket.get("clock_status") != "direct_for_all_native_days":
            row["status"] = "blocked_clock_not_direct"
        else:
            try:
                at = utc(sample["guard_evaluated_at_utc"])
                if at < receipt:
                    raise ValueError("guard precedes signal receipt")
                read_started = sample.get("positions_read_started_utc")
                read_completed = sample.get("positions_read_completed_utc")
                if read_started is not None and read_completed is not None:
                    lower, upper = utc(read_started), utc(read_completed)
                    if lower < receipt or not lower <= upper <= at:
                        raise ValueError("position read interval outside basket lifecycle")
                    if any(lower <= deal_at <= upper for deal_at, *_ in deals):
                        row["status"] = "blocked_native_deal_during_position_read"
                    else:
                        open_volume = {}
                        for deal_at, _, position, entry, volume in deals:
                            if deal_at > upper:
                                break
                            open_volume[position] = open_volume.get(position, Decimal(0)) + (
                                volume if entry == 0 else -volume)
                        expected = sorted(position for position, volume in open_volume.items() if volume > 0)
                        observed = sorted(sample["open_tickets"])
                        row.update(status=("matches_at_bounded_read_interval" if expected == observed
                                           else "differs_at_bounded_read_interval"),
                                   native_open_tickets_at_read=expected,
                                   bot_open_tickets_at_read=observed,
                                   positions_read_started_utc=lower.isoformat(),
                                   positions_read_completed_utc=upper.isoformat())
                elif any(deal_at == at for deal_at, *_ in deals):
                    row["status"] = "blocked_native_deal_at_guard_time"
                else:
                    open_volume = {}
                    for deal_at, _, position, entry, volume in deals:
                        if deal_at > at:
                            break
                        open_volume[position] = open_volume.get(position, Decimal(0)) + (
                            volume if entry == 0 else -volume)
                    expected = sorted(position for position, volume in open_volume.items() if volume > 0)
                    observed = sorted(sample["open_tickets"])
                    row.update(status=("matches_at_guard_eval_not_read_time" if expected == observed
                                       else "differs_at_guard_eval_read_time_unknown"),
                               native_open_tickets_at_guard_eval=expected,
                               bot_open_tickets_at_read=observed,
                               guard_evaluated_at_utc=at.isoformat())
            except (KeyError, TypeError, ValueError):
                row["status"] = "blocked_guard_or_native_clock_invalid"
        rows.append(row)
    return {"status": "diagnostic_only", "sample_count": len(rows),
            "statuses": dict(sorted(Counter(row["status"] for row in rows).items())),
            "rows": rows, "native_position_universe_verified": False,
            "exact_read_timestamp_verified": False,
            "scope": "native_positions_at_guard_evaluation_vs_bot_positions_at_earlier_unknown_read_time"}


def _guard_rows(events, expected_commit):
    starts, finals, duplicate_ids = {}, {}, set()
    guard_count = 0
    for row in events:
        if row["management_kind"] not in GUARDS:
            continue
        guard_count += 1
        target = starts if row["ev"] == "bot_internal_decision_started" else finals
        key = row.get("decision_id")
        if not isinstance(key, str) or not key or key in target:
            duplicate_ids.add(key)
        target[key] = row
    rows = []
    for decision_id in sorted(starts.keys() | finals.keys(), key=str):
        start, final = starts.get(decision_id), finals.get(decision_id)
        if decision_id in duplicate_ids or start is None or final is None:
            rows.append({"decision_id": decision_id, "signal_id": (start or final).get("sig"),
                         "status": "blocked", "issues": ["guard_pair_missing_or_duplicated"],
                         "full_live_path_parity_verified": False})
        else:
            rows.append(verify_captured_guard(start, final, expected_commit=expected_commit))
    return rows, guard_count


def _summaries(rows):
    statuses = Counter(row["status"] for row in rows)
    issues = Counter(issue for row in rows for issue in row["issues"])
    return dict(sorted(statuses.items())), dict(sorted(issues.items()))


def _save(output, report):
    with output.open("x", encoding="utf-8") as target:
        json.dump(report, target, ensure_ascii=True, indent=2)
        target.write("\n")


def run_journal(source, output, *, expected_commit=None):
    source, output = Path(source), Path(output)
    if output.exists():
        raise FileExistsError(output)
    if source.stat().st_size > MAX_JOURNAL_BYTES:
        raise ValueError("local journal exceeds diagnostic byte budget")
    input_sha = digest(source)
    source_hashes = {str(path): digest(path) for path in SOURCES}
    events = []
    with source.open(encoding="utf-8") as stream:
        for line in stream:
            if '"management_decision_inputs_v1"' not in line:
                continue
            events.append(selected_management_fields(json.loads(line)))
            if len(events) > MAX_CAPTURED_EVENTS:
                raise ValueError("management event budget exceeded")
    rows, guard_count = _guard_rows(events, expected_commit)
    if digest(source) != input_sha or any(digest(path) != value for path, value in source_hashes.items()):
        raise ValueError("diagnostic inputs changed during replay")
    statuses, issues = _summaries(rows)
    report = {"contract": "captured_pure_guard_replay_diagnostic_v1", "status": "diagnostic_only",
              "input": {"path": str(source.resolve()), "sha256": input_sha},
              "source_sha256": source_hashes, "expected_commit": expected_commit,
              "management_event_count": len(events), "guard_event_count": guard_count,
              "guard_pair_count": len(rows), "statuses": statuses,
              "issues": issues, "rows": rows,
              "source_version_admitted": False,
              "full_live_path_parity_verified": False,
              "limitations": ["Local journal is not the frozen September VM cohort.",
                              "Only the pure basket guards are re-evaluated; entry, trailing, broker actions and risk paths are not.",
                              "Matching a declared commit string does not verify the historical source or session contract."]}
    _save(output, report)
    return report


def run_segments(plan_path, signal, segment_paths, output, *, expected_commit=None,
                 native_deals_path=None):
    plan_path, output = Path(plan_path), Path(output)
    segment_paths = [Path(path) for path in segment_paths]
    if output.exists():
        raise FileExistsError(output)
    if not segment_paths or any(path.stat().st_size > 64_000_000 for path in segment_paths):
        raise ValueError("bounded segment inputs missing or oversized")
    plan_sha = digest(plan_path)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    native_deals_path = Path(native_deals_path) if native_deals_path is not None else None
    if native_deals_path is not None and native_deals_path.stat().st_size > 16_000_000:
        raise ValueError("native deals exceed bounded input budget")
    native_sha = digest(native_deals_path) if native_deals_path is not None else None
    if native_deals_path is not None and plan.get("inputs_sha256", {}).get(str(native_deals_path)) != native_sha:
        raise ValueError("native deal source not bound to lifecycle plan")
    segments = build_segments(plan)
    expected = {index for index, segment in enumerate(segments) if signal in segment["active_signals"]}
    if not expected:
        raise ValueError("signal absent from native lifecycle plan")
    input_hashes = {str(path): digest(path) for path in segment_paths}
    source_hashes = {str(path): digest(path) for path in SEGMENT_SOURCES}
    by_index, protocols = {}, set()
    for path in segment_paths:
        report = json.loads(path.read_text(encoding="utf-8"))
        index = report.get("segment_index")
        if index not in expected or index in by_index or report.get("segment_count") != len(segments):
            raise ValueError("duplicate, extraneous or wrong-count segment")
        contract = report.get("contract")
        if (contract not in {"bounded_vm_week_material_segment_v2",
                             "bounded_vm_week_material_segment_v3"}
                or report.get("local_inputs_sha256", {}).get(str(plan_path)) != plan_sha):
            raise ValueError("v2/v3 segment or plan source proof missing")
        if contract == "bounded_vm_week_material_segment_v3" and (
                report.get("indexed_prefix_time_coverage_verified") is not True
                or report.get("full_source_time_coverage_verified") is not False
                or not report.get("index_sha256")):
            raise ValueError("indexed prefix segment proof missing")
        validate_segment_result(report, segments[index])
        protocols.add((contract, report.get("index_sha256"),
                       tuple(sorted(report["local_inputs_sha256"].items()))))
        by_index[index] = report
    if set(by_index) != expected or len(protocols) != 1:
        raise ValueError("incomplete native basket segments or mixed collector versions")
    events = [row for index in sorted(expected) for row in by_index[index]["management_events"]
              if row["sig"] == signal]
    if len(events) > MAX_CAPTURED_EVENTS:
        raise ValueError("management event budget exceeded")
    if any(selected_management_fields(row) != row for row in events):
        raise ValueError("management segment projection changed after capture")
    pairing = pair_management_evaluations(events)
    rows, guard_count = _guard_rows(events, expected_commit)
    observed_risk = summarize_guard_risk(events)
    basket = next(row for row in plan["rows"] if row["signal_id"] == signal)
    native_ticket_diagnostic = (compare_native_ticket_universe(
        observed_risk["rows"], basket, json.loads(native_deals_path.read_text(encoding="utf-8")))
        if native_deals_path is not None else {"status": "not_provided",
                                            "native_position_universe_verified": False})
    if (digest(plan_path) != plan_sha or any(digest(path) != value for path, value in input_hashes.items())
            or native_deals_path is not None and digest(native_deals_path) != native_sha
            or any(digest(path) != value for path, value in source_hashes.items())):
        raise ValueError("segment diagnostic inputs changed during replay")
    statuses, issues = _summaries(rows)
    input_mode = ("bounded_v3_indexed_prefix_segments" if by_index[next(iter(by_index))]["contract"]
                  == "bounded_vm_week_material_segment_v3" else "bounded_v2_segments")
    report = {"contract": "captured_pure_guard_replay_diagnostic_v1", "status": "diagnostic_only",
              "input_mode": input_mode, "signal_id": signal,
              "plan": {"path": str(plan_path.resolve()), "sha256": plan_sha},
              "native_deals": ({"path": str(native_deals_path.resolve()), "sha256": native_sha}
                               if native_deals_path is not None else None),
              "input_sha256": input_hashes, "source_sha256": source_hashes,
              "expected_segment_count": len(expected), "collected_segment_count": len(by_index),
              "management_capture": pairing, "management_event_count": len(events),
              "observed_guard_risk": observed_risk,
              "native_ticket_diagnostic": native_ticket_diagnostic,
              "guard_event_count": guard_count, "guard_pair_count": len(rows),
              "statuses": statuses, "issues": issues, "rows": rows,
              "expected_commit": expected_commit, "source_version_admitted": False,
              "full_live_path_parity_verified": False,
              "limitations": ["Collected journal windows are not a full policy or broker execution replay.",
                              "v3 proves indexed-prefix coverage only; v2 still assumes approximate timestamp order.",
                              "Guard P/L covers bot-known MT5 positions; native ticket universe and between-sample drawdown are unverified.",
                              "Guard evaluation time follows the MT5 read; the exact position-read timestamp is not captured.",
                              "Native ticket sets at guard evaluation are diagnostic, not exact read-time parity.",
                              "Only the pure basket guards are evaluated; entry, trailing and simulated risk paths remain separate.",
                              "Historical source/session admission and native trajectory comparison remain open."]}
    _save(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--signal")
    parser.add_argument("--segment", type=Path, action="append")
    parser.add_argument("--deals", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-commit")
    args = parser.parse_args()
    if args.journal and not any((args.plan, args.signal, args.segment, args.deals)):
        report = run_journal(args.journal, args.output, expected_commit=args.expected_commit)
    elif args.plan and args.signal and args.segment and not args.journal:
        report = run_segments(args.plan, args.signal, args.segment, args.output,
                              expected_commit=args.expected_commit, native_deals_path=args.deals)
    else:
        parser.error("use --journal alone or --plan, --signal and --segment together")
    print(json.dumps({key: report[key] for key in ("guard_pair_count", "statuses", "issues")}))


if __name__ == "__main__":
    main()
