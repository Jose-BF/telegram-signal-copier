"""Fail-closed parity between MT5 results and the deployed strategy logic.

This module intentionally does not predict entries.  It conditions the strategy
on the fills that MT5 actually accepted, then independently replays management
through the scalar, compiled and oracle engines.  Prospective Telegram replay is
a different evidence role and must never be presented as this mirror.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from typing import Any, Iterable, Mapping

from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.dataset import SignalPath
from research.dubai_iterative.engine import SimulationResult, simulate
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.oracle import OracleResult, oracle_simulate
from research.gold_iterative.ledger_evidence import ledger_ticket_evidence
from research.gold_iterative.exit_deals import compare_exit_deals, mirror_exit_rows


_CENT = Decimal("0.01")
_EVIDENCE_ROLES = {
    "actual_mt5": "observed_broker_result",
    "live_logic_mirror": "strategy_replay_conditioned_on_actual_mt5_fills",
    "shadow_prediction": "prospective_replay_from_telegram_and_ticks",
}


def certify_live_logic_mirror(
    *,
    paths: Iterable[SignalPath],
    actual_rows: Iterable[Mapping[str, Any]],
    audit_rows: Iterable[Mapping[str, Any]],
    genome: StrategyGenome,
    fast_evaluator: FastEvaluator | None = None,
) -> dict[str, Any]:
    """Certify strategy management against reconciled MT5 signal baskets.

    The gate is deliberately strict: exact tick audit, strategy identity,
    three-engine agreement, entry facts and account-currency results must all
    agree for every signal and ticket. This does not certify the observed exit
    decision/deal sequence, or say anything about whether a
    prospective replay can predict those fills from Telegram and ticks.
    """

    actual_by_id, actual_map_blockers = _unique_rows(actual_rows, "actual")
    audit_by_id, audit_map_blockers = _unique_rows(audit_rows, "audit")
    path_by_id, path_map_blockers = _unique_paths(paths)
    evaluator = fast_evaluator or FastEvaluator()
    expected_live_fingerprint = (
        genome.source_strategy_fingerprint or genome.fingerprint
    )
    rows: list[dict[str, Any]] = []

    for signal_id in sorted(actual_by_id):
        actual = actual_by_id[signal_id]
        audit = audit_by_id.get(signal_id)
        path = path_by_id.get(signal_id)
        blockers: list[str] = []
        mismatches: list[str] = []

        if str(actual.get("channel") or "") != "canal2":
            blockers.append("actual_channel_mismatch")
        entry_count = _non_negative_int(actual.get("n_positions"))
        if entry_count is None:
            blockers.append("actual_entry_count_invalid")
            entry_count = 0
        actual_money = _money(actual.get("pnl_real_mt5"))
        if actual_money is None:
            blockers.append("actual_money_invalid")
        if entry_count > 0 and actual.get("reconciled_ok") is not True:
            blockers.append("mt5_reconciliation_incomplete")
        snapshot = actual.get("strategy_snapshot")
        snapshot_fingerprint = (
            str(snapshot.get("live_strategy_fingerprint") or "")
            if isinstance(snapshot, Mapping)
            else ""
        )
        if snapshot_fingerprint != expected_live_fingerprint:
            blockers.append("live_strategy_fingerprint_mismatch")

        blockers.extend(_audit_blockers(audit, entry_count))
        if (
            entry_count == 0
            and actual.get("no_position_outcome_verified") is not True
        ):
            blockers.append("live_no_position_outcome_unverified")

        mirror_money: Decimal | None = None
        mirror_entries: int | None = None
        mirror_exit_reason: str | None = None
        engine_agreement = False
        engine_digest: str | None = None
        simulated_exits = None
        ticket_rows, ledger_blockers = _bind_ledger(actual, path)
        blockers.extend(ledger_blockers)

        if entry_count == 0 and not blockers:
            mirror_money = Decimal("0.00")
            mirror_entries = 0
            mirror_exit_reason = "verified_no_position"
            engine_agreement = True
            simulated_exits = []
            if actual_money != Decimal("0.00"):
                mismatches.append("money_mismatch")
        elif entry_count > 0:
            if path is None:
                blockers.append("actual_fill_path_missing")
            else:
                blockers.extend(_path_blockers(path, entry_count, actual_money))
            if path is not None and not blockers:
                mirror_genome = _actual_fill_genome(genome, path)
                if mirror_genome is None:
                    blockers.append("strategy_cannot_cover_actual_leg_count")
                else:
                    scalar = simulate(path, mirror_genome)
                    fast = evaluator(path, mirror_genome)
                    oracle = oracle_simulate(path, mirror_genome)
                    signatures = tuple(
                        _result_signature(result)
                        for result in (scalar, fast, oracle)
                    )
                    engine_agreement = signatures[0] == signatures[1] == signatures[2]
                    engine_digest = _canonical_hash(signatures)
                    if not engine_agreement:
                        blockers.append("simulation_engines_disagree")
                    result_blockers = sorted(
                        {
                            str(value)
                            for result in (scalar, fast, oracle)
                            for value in result.blockers
                            if str(value)
                        }
                    )
                    blockers.extend(
                        f"simulation:{value}" for value in result_blockers
                    )
                    mirror_money = _money(scalar.pnl_eur)
                    mirror_entries = len(scalar.entries)
                    mirror_exit_reason = scalar.exit_reason
                    simulated_exits = mirror_exit_rows(scalar.exits)
                    if mirror_money is None:
                        blockers.append("live_logic_mirror_money_missing")
                    if mirror_entries != entry_count:
                        mismatches.append("entry_count_mismatch")
                    if (
                        mirror_money is not None
                        and actual_money is not None
                        and mirror_money != actual_money
                    ):
                        mismatches.append("money_mismatch")
                    ticket_rows, ticket_blockers, ticket_mismatches = (
                        _compare_tickets(path, scalar, ticket_rows)
                    )
                    blockers.extend(ticket_blockers)
                    mismatches.extend(ticket_mismatches)

        exit_comparison = compare_exit_deals(actual, simulated_exits)
        blockers.extend(f"exit_deals:{reason}" for reason in exit_comparison["blockers"])
        mismatches.extend(f"exit_deals:{reason}" for reason in exit_comparison["mismatches"])

        if mirror_entries is None:
            for ticket_row in ticket_rows:
                ticket_row["status"] = "blocked"
                ticket_row["blockers"] = list(dict.fromkeys(
                    ticket_row["blockers"] + blockers + ["mirror_not_evaluated"]
                ))

        blockers = list(dict.fromkeys(blockers))
        mismatches = list(dict.fromkeys(mismatches))
        status = "blocked" if blockers else "mismatch" if mismatches else "exact"
        rows.append({
            "signal_id": signal_id,
            "day": str(actual.get("signal_dt_utc") or actual.get("day") or "")[:10],
            "status": status,
            "actual_mt5_eur": _money_text(actual_money),
            "live_logic_mirror_eur": _money_text(mirror_money),
            "net_delta_eur": _money_text(
                None
                if actual_money is None or mirror_money is None
                else mirror_money - actual_money
            ),
            "actual_entry_count": entry_count,
            "mirror_entry_count": mirror_entries,
            "mirror_exit_reason": mirror_exit_reason,
            "engine_agreement": engine_agreement,
            "engine_result_digest": engine_digest,
            "ticket_rows": ticket_rows,
            "mirror_exit_rows": simulated_exits,
            "exit_deal_comparison": exit_comparison,
            "blockers": blockers + mismatches,
        })

    global_blockers = list(dict.fromkeys(
        actual_map_blockers
        + audit_map_blockers
        + path_map_blockers
        + (["no_actual_mt5_signals"] if not rows else [])
    ))
    exact = sum(row["status"] == "exact" for row in rows)
    mismatched = sum(row["status"] == "mismatch" for row in rows)
    blocked = sum(row["status"] == "blocked" for row in rows)
    actual_total = _sum_money(row["actual_mt5_eur"] for row in rows)
    mirror_total = (
        None
        if blocked or global_blockers
        else _sum_money(row["live_logic_mirror_eur"] for row in rows)
    )
    if global_blockers or blocked:
        parity_status = "blocked"
    elif mismatched:
        parity_status = "mismatch"
    else:
        parity_status = "exact"

    return {
        "schema_version": 4,
        "comparison_contract": "ledger_bound_entry_and_exit_deals_v4",
        "research_genome_fingerprint": genome.fingerprint,
        "live_strategy_fingerprint": expected_live_fingerprint,
        "evidence_roles": dict(_EVIDENCE_ROLES),
        "actual_mt5": {
            "signals": len(rows),
            "entries": sum(int(row["actual_entry_count"]) for row in rows),
            "net_eur": _money_text(actual_total),
        },
        "live_logic_mirror": {
            "signals": len(rows),
            "exact_signals": exact,
            "mismatched_signals": mismatched,
            "blocked_signals": blocked,
            "net_eur": _money_text(mirror_total),
        },
        "shadow_prediction": {
            "status": "not_part_of_live_logic_parity",
            "net_eur": None,
        },
        "parity": {
            "status": parity_status,
            "net_delta_eur": _money_text(
                None
                if actual_total is None or mirror_total is None
                else mirror_total - actual_total
            ),
            "blockers": global_blockers,
        },
        "management_replay_allowed": parity_status == "exact" and bool(rows),
        "historical_extension_allowed": False,
        "remaining_end_to_end_gates": [
            "prospective_entry_outcome_parity",
            "prospective_entry_trigger_parity",
            "broker_fill_parity",
            "observed_exit_decision_and_deal_sequence_parity",
            "deterministic_terminal_lifecycle_parity",
        ],
        "rows": rows,
    }


def _actual_fill_genome(
    genome: StrategyGenome,
    path: SignalPath,
) -> StrategyGenome | None:
    leg_count = len(path.legs)
    if leg_count <= 0:
        return None
    if genome.target_mode == "per_leg_steps" and leg_count > len(genome.target_steps):
        return None
    return genome.with_change(
        entry_mode="actual_mt5",
        entry_value=None,
        entry_confirmation_value=None,
        entry_expiry_min=max(genome.entry_expiry_min, 1),
        entry_ladder_mode="simultaneous",
        entry_ladder_step=None,
        leg_count=leg_count,
        volume_weights=tuple(float(leg.volume) for leg in path.legs),
        target_steps=tuple(genome.target_steps[:leg_count]),
        pending_entry_policy="until_expiry",
        source_strategy_fingerprint=None,
        parent_fingerprints=(),
        mutation_reason=None,
        lineage_depth=0,
    )


def _path_blockers(
    path: SignalPath,
    entry_count: int,
    actual_money: Decimal | None,
) -> list[str]:
    blockers: list[str] = []
    if path.entry_evidence_kind != "actual_mt5":
        blockers.append("actual_fill_evidence_missing")
    if len(path.legs) != entry_count:
        blockers.append("actual_fill_leg_count_mismatch")
    path_money = _money(path.actual_pnl_eur)
    if path_money is None:
        blockers.append("actual_fill_money_missing")
    elif actual_money is not None and path_money != actual_money:
        blockers.append("actual_sources_money_disagree")
    tickets: set[str] = set()
    for leg in path.legs:
        ticket = str(leg.ticket or "")
        if not ticket:
            blockers.append("actual_ticket_identity_missing")
        elif ticket in tickets:
            blockers.append(f"duplicate_actual_ticket:{ticket}")
        tickets.add(ticket)
        if _money(leg.actual_pnl_eur) is None:
            blockers.append(f"actual_ticket_money_invalid:{ticket}")
        volume = _number(leg.volume)
        if volume is None or volume <= 0:
            blockers.append(f"actual_ticket_volume_invalid:{ticket}")
        if leg.closed_at is None:
            blockers.append(f"actual_ticket_not_closed:{ticket}")
    ticket_total = _sum_money(leg.actual_pnl_eur for leg in path.legs)
    if ticket_total is not None and path_money is not None and ticket_total != path_money:
        blockers.append("actual_ticket_money_sum_mismatch")
    return blockers


def _bind_ledger(
    actual: Mapping[str, Any],
    path: SignalPath | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    evidence, ledger_blockers = ledger_ticket_evidence(actual)
    blockers = list(ledger_blockers)
    legs = {str(leg.ticket or ""): leg for leg in path.legs} if path else {}
    rows: list[dict[str, Any]] = []
    for ticket in sorted(evidence.keys() | legs.keys()):
        observed = evidence.get(ticket)
        leg = legs.get(ticket)
        issues = list(observed["blockers"]) if observed else []
        if observed is None:
            issues.append(f"ledger_path_ticket_unbound:{ticket}")
        elif leg is None:
            issues.append(f"ledger_path_ticket_missing:{ticket}")
        else:
            if path.direction != observed["direction"]:
                issues.append(f"ledger_path_direction_mismatch:{ticket}")
            market_symbols = {
                str(item.get("symbol") or "").strip().upper()
                for item in path.market_evidence if isinstance(item, Mapping)
            }
            if not path.market_evidence or market_symbols != {observed["symbol"]}:
                issues.append(f"ledger_path_symbol_mismatch_or_missing:{ticket}")
            if (
                _number(leg.open_price) != observed["open_price"]
                or _number(leg.volume) != observed["volume"]
                or leg.opened_at != observed["opened_at"]
            ):
                issues.append(f"ledger_path_entry_facts_mismatch:{ticket}")
            if _money(leg.actual_pnl_eur) != observed["net_eur"]:
                issues.append(f"ledger_path_money_mismatch:{ticket}")
        observed = observed or {}
        opened = observed.get("opened_at")
        costs = observed.get("costs")
        rows.append({
            "ticket": ticket, "status": "blocked",
            "actual_mt5_eur": _money_text(observed.get("net_eur")),
            "mirror_eur": None, "net_delta_eur": None,
            "actual_volume": str(observed["volume"]) if observed.get("volume") is not None else None,
            "mirror_closed_volume": None, "mirror_entry_count": None,
            "mirror_exit_count": None, "blockers": issues,
            "actual_entry_price": str(observed["open_price"]) if observed.get("open_price") is not None else None,
            "actual_opened_at": opened.isoformat() if opened is not None else None,
            "actual_direction": observed.get("direction"),
            "actual_symbol": observed.get("symbol"),
            "ledger_closed_volume": str(observed["closed_volume"]) if observed.get("closed_volume") is not None else None,
            "ledger_deal_count": observed.get("deal_count"),
            "ledger_costs": {key: _money_text(value) for key, value in costs.items()} if costs is not None else None,
            "ledger_position_indices": observed.get("source_position_indices", []),
            "path_net_eur": _money_text(_money(leg.actual_pnl_eur)) if leg else None,
        })
        blockers.extend(issues)
    positions = actual.get("positions")
    if isinstance(positions, (list, tuple)):
        represented = {index for row in rows for index in row["ledger_position_indices"]}
        for index in range(len(positions)):
            if index not in represented:
                rows.append({
                    "ticket": "", "status": "blocked", "actual_mt5_eur": None,
                    "mirror_eur": None, "net_delta_eur": None, "actual_volume": None,
                    "mirror_closed_volume": None, "mirror_entry_count": None,
                    "mirror_exit_count": None, "ledger_position_indices": [index],
                    "blockers": [f"ledger_position_unbound:{index}"],
                })
    return rows, list(dict.fromkeys(blockers))


def _compare_tickets(
    path: SignalPath,
    result: SimulationResult,
    ledger_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    actual = {str(leg.ticket): leg for leg in path.legs}
    ledger = {row["ticket"]: row for row in ledger_rows}
    entries: dict[str, list[Any]] = {}
    exits: dict[str, list[Any]] = {}
    for entry in result.entries:
        entries.setdefault(str(entry.ticket or ""), []).append(entry)
    for exit_record in result.exits:
        exits.setdefault(str(exit_record.ticket or ""), []).append(exit_record)
    blockers: list[str] = []
    mismatches: list[str] = []
    rows: list[dict[str, Any]] = []
    # Partial closes may share a ticket, but cannot offset another ticket's error.
    for ticket in sorted(actual.keys() | entries.keys() | exits.keys()):
        leg = actual.get(ticket)
        ticket_entries = entries.get(ticket, [])
        ticket_exits = exits.get(ticket, [])
        issues: list[str] = []
        missing: list[str] = []
        observed = ledger.get(ticket, {})
        actual_money = _money(observed.get("actual_mt5_eur"))
        simulated_money = (
            _sum_money(item.pnl_eur for item in ticket_exits)
            if ticket_exits else None
        )
        closed_volume = _sum_numbers(item.volume for item in ticket_exits)
        if leg is None:
            issues.append(f"unexpected_mirror_ticket:{ticket}")
        else:
            if len(ticket_entries) != 1:
                issues.append(f"ticket_entry_count_mismatch:{ticket}")
            else:
                entry = ticket_entries[0]
                if (
                    _number(entry.entry_price) != _number(leg.open_price)
                    or _number(entry.volume) != _number(leg.volume)
                    or entry.opened_at != leg.opened_at
                    or entry.source != "observed_mt5_fill"
                ):
                    issues.append(f"ticket_entry_facts_mismatch:{ticket}")
            if not ticket_exits:
                issues.append(f"ticket_exit_missing:{ticket}")
            elif simulated_money is None:
                missing.append(f"mirror_ticket_money_missing:{ticket}")
            if closed_volume is None or any(
                _number(item.volume) is None or _number(item.volume) <= 0
                for item in ticket_exits
            ):
                missing.append(f"mirror_ticket_volume_invalid:{ticket}")
            elif closed_volume != _number(leg.volume):
                issues.append(f"ticket_exit_volume_mismatch:{ticket}")
            if (
                simulated_money is not None and actual_money is not None
                and simulated_money != actual_money
            ):
                issues.append(f"ticket_money_mismatch:{ticket}")
        blockers.extend(missing)
        mismatches.extend(issues)
        rows.append({
            **observed,
            "ticket": ticket,
            "status": "blocked" if missing else "mismatch" if issues else "exact",
            "actual_mt5_eur": _money_text(actual_money),
            "mirror_eur": _money_text(simulated_money),
            "net_delta_eur": _money_text(
                None if actual_money is None or simulated_money is None
                else simulated_money - actual_money
            ),
            "actual_volume": observed.get("actual_volume"),
            "mirror_closed_volume": str(closed_volume) if closed_volume is not None else None,
            "mirror_entry_count": len(ticket_entries),
            "mirror_exit_count": len(ticket_exits),
            "blockers": missing + issues,
        })
    ticket_total = _sum_money(item.pnl_eur for item in result.exits)
    if ticket_total is not None and ticket_total != _money(result.pnl_eur):
        blockers.append("mirror_ticket_money_sum_mismatch")
    return rows, blockers, mismatches


def _number(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _sum_numbers(values: Iterable[object]) -> Decimal | None:
    total = Decimal(0)
    for value in values:
        number = _number(value)
        if number is None:
            return None
        total += number
    return total


def _audit_blockers(
    audit: Mapping[str, Any] | None,
    entry_count: int,
) -> list[str]:
    if audit is None:
        return ["observed_tick_audit_missing"]
    blockers: list[str] = []
    if audit.get("status") != "exact":
        blockers.append("observed_tick_audit_not_exact")
    ticket_count = _non_negative_int(audit.get("ticket_count"))
    exact_tickets = _non_negative_int(audit.get("exact_tickets"))
    if ticket_count != entry_count or exact_tickets != entry_count:
        blockers.append("observed_tick_ticket_count_mismatch")
    if _non_negative_int(audit.get("blocked_tickets")) != 0:
        blockers.append("observed_tick_has_blocked_tickets")
    if _non_negative_int(audit.get("mismatch_tickets")) != 0:
        blockers.append("observed_tick_has_mismatched_tickets")
    if [value for value in audit.get("blockers") or () if str(value)]:
        blockers.append("observed_tick_has_blockers")
    return blockers


def _result_signature(result: SimulationResult | OracleResult) -> Mapping[str, Any]:
    return {
        "pnl_eur": _money_text(_money(result.pnl_eur)),
        "exit_reason": result.exit_reason,
        "blockers": list(result.blockers),
        "unfilled": bool(result.unfilled),
        "filled_volume": format(Decimal(str(result.filled_volume)), "f"),
        "entries": [
            {
                "ticket": item.ticket,
                "tick_index": int(item.tick_index),
                "opened_at": item.opened_at.isoformat(),
                "entry_price": format(Decimal(str(item.entry_price)), "f"),
                "volume": format(Decimal(str(item.volume)), "f"),
                "source": item.source,
            }
            for item in result.entries
        ],
        "exits": [
            {
                "ticket": item.ticket,
                "tick_index": int(item.tick_index),
                "closed_at": item.closed_at.isoformat(),
                "entry_price": format(Decimal(str(item.entry_price)), "f"),
                "exit_price": format(Decimal(str(item.exit_price)), "f"),
                "volume": format(Decimal(str(item.volume)), "f"),
                "pnl_eur": _money_text(_money(item.pnl_eur)),
                "reason": item.reason,
            }
            for item in result.exits
        ],
    }


def _unique_rows(
    rows: Iterable[Mapping[str, Any]],
    role: str,
) -> tuple[dict[str, Mapping[str, Any]], list[str]]:
    output: dict[str, Mapping[str, Any]] = {}
    blockers: list[str] = []
    for row in rows:
        signal_id = str(row.get("sig_id") or row.get("signal_id") or "")
        if not signal_id:
            blockers.append(f"{role}_signal_identity_missing")
            continue
        if signal_id in output:
            blockers.append(f"duplicate_{role}_signal:{signal_id}")
            continue
        output[signal_id] = row
    return output, blockers


def _unique_paths(
    paths: Iterable[SignalPath],
) -> tuple[dict[str, SignalPath], list[str]]:
    output: dict[str, SignalPath] = {}
    blockers: list[str] = []
    for path in paths:
        if path.signal_id in output:
            blockers.append(f"duplicate_actual_fill_path:{path.signal_id}")
            continue
        output[path.signal_id] = path
    return output, blockers


def _money(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        normalized = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not normalized.is_finite():
        return None
    return normalized.quantize(_CENT, rounding=ROUND_HALF_UP)


def _money_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value.quantize(_CENT), ".2f")


def _sum_money(values: Iterable[object]) -> Decimal | None:
    total = Decimal("0.00")
    for value in values:
        normalized = _money(value)
        if normalized is None:
            return None
        total += normalized
    return total.quantize(_CENT)


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if normalized < 0 or normalized != value:
        return None
    return normalized


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()
