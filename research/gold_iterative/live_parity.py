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
    three-engine agreement, entry count and account-currency result must all
    agree for every signal.  This intentionally says nothing about whether a
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

        if entry_count == 0 and not blockers:
            mirror_money = Decimal("0.00")
            mirror_entries = 0
            mirror_exit_reason = "verified_no_position"
            engine_agreement = True
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
        "schema_version": 1,
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
    return blockers


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
