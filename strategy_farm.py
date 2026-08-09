"""Batch and score auditable management policies over one shared replay set."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from numbers import Integral, Real
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

import broker_market_sessions
import broker_money
import executed_simulation_contract
import mt5_tick_cache
import pipeline_progress
from broker_market_sessions import (
    MARKET_SESSION_CONTRACT,
    broker_session_close_utc,
)
import observed_tick_replay_validator
import provider_strategy_simulator
import provider_trade_spec
import replay_source_contract
import runtime_paths
import simulation_certifier
import simulation_oracle
import simulation_run_provenance
import strategy_policies
import strategy_simulator
from tools import ensure_money_tick_cache, ensure_replay_tick_cache


DATA_DIR = runtime_paths.active_data_dir()
DEFAULT_REPLAY = DATA_DIR / "replay_trades.jsonl"
DEFAULT_BASELINE = DATA_DIR / "observed_tick_replay_audit.jsonl"
DEFAULT_CATALOG = DATA_DIR / "provider_signal_catalog.json"
DEFAULT_TICK_CACHE = DATA_DIR / "ticks_cache"
DEFAULT_MONEY_CONTRACT = DATA_DIR / "broker_money_contract.json"
DEFAULT_MONEY_TICK_CACHE = DATA_DIR / "money_ticks_cache"
DEFAULT_MONEY_TICK_STATUS = DATA_DIR / "money_tick_cache_status.json"
DEFAULT_OUTPUT = DATA_DIR / "strategy_farm.json"
DEFAULT_RUN_ARCHIVE = DATA_DIR / "simulation_runs"
SCHEMA_VERSION = 1
UNSAFE_CALIBRATION_PREFIXES = (
    "default_unit_value",
    "global_mt5_calibrated",
    "cli_default_unit_value",
)
UNSAFE_CALIBRATION_SOURCES = {
    "ticket_mt5_calibrated",
    "trade_mt5_calibrated",
    "global_mt5_calibrated",
    "default_unit_value",
    "cli_default_unit_value",
}


@dataclass(frozen=True)
class FarmExecution:
    report: dict
    selected_payloads: dict[str, list]
    policies: list[dict]
    required_tick_days: list[str]
    verified_tick_contracts: dict[str, dict]
    market_replay_summary: dict[str, int]


def _simulation_source_files() -> dict[str, Path]:
    repo_dir = Path(__file__).parent
    return {
        "broker_market_sessions": Path(broker_market_sessions.__file__),
        "strategy_farm": Path(__file__),
        "strategy_policies": Path(strategy_policies.__file__),
        "strategy_simulator": Path(strategy_simulator.__file__),
        "provider_trade_spec": Path(provider_trade_spec.__file__),
        "provider_strategy_simulator": Path(
            provider_strategy_simulator.__file__
        ),
        "broker_money": Path(broker_money.__file__),
        "executed_simulation_contract": Path(
            executed_simulation_contract.__file__
        ),
        "mt5_tick_cache": Path(mt5_tick_cache.__file__),
        "runtime_paths": Path(runtime_paths.__file__),
        "capture_broker_money_contract": (
            repo_dir / "tools" / "capture_broker_money_contract.py"
        ),
        "ensure_money_tick_cache": (
            repo_dir / "tools" / "ensure_money_tick_cache.py"
        ),
        "observed_tick_replay_validator": Path(
            observed_tick_replay_validator.__file__
        ),
        "ensure_replay_tick_cache": Path(
            observed_tick_replay_validator.ensure_replay_tick_cache.__file__
        ),
        "simulation_run_provenance": Path(
            simulation_run_provenance.__file__
        ),
        "simulation_certifier": Path(simulation_certifier.__file__),
        "simulation_oracle": Path(simulation_oracle.__file__),
        "replay_source_contract": Path(replay_source_contract.__file__),
    }


def _semantic_artifact_paths(
    *,
    input_files: dict[str, Path],
    source_files: dict[str, Path],
    market_tick_cache_dir: Path,
    conversion_tick_cache_dir: Path,
) -> dict[str, Path]:
    paths = {
        **{
            f"input:{role}": Path(path)
            for role, path in input_files.items()
        },
        **{
            f"code:{role}": Path(path)
            for role, path in source_files.items()
        },
    }
    for prefix, cache_dir in (
        ("market_tick", Path(market_tick_cache_dir)),
        ("conversion_tick", Path(conversion_tick_cache_dir)),
    ):
        if not cache_dir.is_dir():
            continue
        for path in sorted(cache_dir.glob("*.parquet")):
            paths[f"{prefix}:{path.name}"] = path
        for path in sorted(cache_dir.glob("*.parquet.meta.json")):
            paths[f"{prefix}:{path.name}"] = path
    return paths


def _snapshot_semantic_artifacts(
    paths: dict[str, Path],
) -> dict[str, dict]:
    snapshot = {}
    for role, path in sorted(paths.items()):
        if not path.is_file():
            continue
        snapshot[role] = {
            "path": str(path.resolve()),
            "size_bytes": path.stat().st_size,
            "sha256": simulation_run_provenance.sha256_file(path),
        }
    return snapshot


def _changed_semantic_artifacts(
    snapshot: dict[str, dict],
) -> list[str]:
    changed = []
    for role, record in sorted(snapshot.items()):
        path = Path(record["path"])
        if (
            not path.is_file()
            or path.stat().st_size != record["size_bytes"]
            or simulation_run_provenance.sha256_file(path)
            != record["sha256"]
        ):
            changed.append(role)
    return changed


def _money(value: float | None) -> float | None:
    return None if value is None else round(float(value), 2)


def _unsafe_calibration(row: dict) -> bool:
    if any(
        str(assumption).startswith(UNSAFE_CALIBRATION_PREFIXES)
        for assumption in row.get("assumptions") or []
    ):
        return True
    return any(
        ticket.get("changed_rules")
        and ticket.get("pnl_source") in UNSAFE_CALIBRATION_SOURCES
        for ticket in row.get("tickets") or []
    )


def _max_consecutive_losses(values: list[float]) -> int:
    longest = 0
    current = 0
    for value in values:
        if value < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _max_drawdown(values: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def _metric_row_time(row: dict) -> datetime | None:
    values = [row.get("open_dt_utc"), row.get("signal_dt_utc")]
    values.extend(
        ticket.get("open_time_utc") or ticket.get("open_dt_utc")
        for ticket in row.get("tickets") or []
    )
    parsed = []
    for value in values:
        if value in (None, ""):
            continue
        try:
            timestamp = datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            )
        except (TypeError, ValueError):
            continue
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            continue
        parsed.append(timestamp.astimezone(timezone.utc))
    return min(parsed) if parsed else None


def calculate_policy_metrics(rows: Iterable[dict]) -> dict:
    rows = list(rows)
    sequence_rows = [
        (
            _metric_row_time(row),
            str(row.get("sig_id") or ""),
            row,
        )
        for row in rows
    ]
    sequence_order_verified = all(
        timestamp is not None and bool(sig_id)
        for timestamp, sig_id, _row in sequence_rows
    )
    if sequence_order_verified:
        rows = [
            row
            for _timestamp, _sig_id, row in sorted(
                sequence_rows,
                key=lambda item: (item[0], item[1]),
            )
        ]
    usable = [
        row
        for row in rows
        if row.get("status") != "blocked"
        and row.get("strategy_pnl") is not None
    ]
    blocked = len(rows) - len(usable)
    values = [float(row.get("strategy_pnl") or 0.0) for row in usable]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    flats = len(values) - len(wins) - len(losses)
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    decided = len(wins) + len(losses)
    exploratory_net = sum(values)
    unsafe = sum(_unsafe_calibration(row) for row in usable)
    mfe_values = [
        float(row["mfe_pnl"])
        for row in usable
        if row.get("mfe_pnl") is not None
    ]
    total_mfe = sum(mfe_values)
    total_giveback = sum(
        max(
            0.0,
            float(row.get("mfe_pnl") or 0.0)
            - float(row.get("strategy_pnl") or 0.0),
        )
        for row in usable
        if row.get("mfe_pnl") is not None
    )
    changed_exit_exposure = sum(
        abs(float(ticket.get("pnl_per_price_unit") or 0.0))
        for row in usable
        for ticket in row.get("tickets") or []
        if ticket.get("changed_rules")
    )
    slippage_stress = {
        f"{slippage:.2f}_price": (
            _money(exploratory_net - slippage * changed_exit_exposure)
            if blocked == 0 else None
        )
        for slippage in (0.10, 0.25, 0.50)
    }
    return {
        "total_trades": len(rows),
        "sequence_order_verified": sequence_order_verified,
        "usable_trades": len(usable),
        "blocked_trades": blocked,
        "coverage": round(len(usable) / len(rows), 4) if rows else 0.0,
        "wins": len(wins),
        "losses": len(losses),
        "flat": flats,
        "win_rate": round(len(wins) / decided, 4) if decided else None,
        "gross_profit": _money(gross_profit),
        "gross_loss": _money(gross_loss),
        "profit_factor": (
            round(gross_profit / gross_loss, 4) if gross_loss > 0 else None
        ),
        "expectancy": (
            _money(exploratory_net / len(values)) if values else None
        ),
        "exploratory_net_pnl": _money(exploratory_net),
        "net_pnl": _money(exploratory_net) if blocked == 0 else None,
        "max_drawdown": _money(_max_drawdown(values)),
        "best_trade": _money(max(values)) if values else None,
        "worst_trade": _money(min(values)) if values else None,
        "max_consecutive_losses": _max_consecutive_losses(values),
        "unsafe_calibration_trades": unsafe,
        "total_mfe_pnl": _money(total_mfe) if mfe_values else None,
        "total_profit_giveback": (
            _money(total_giveback) if mfe_values else None
        ),
        "mfe_capture_ratio": (
            round(exploratory_net / total_mfe, 4) if total_mfe > 0 else None
        ),
        "changed_exit_price_exposure": round(changed_exit_exposure, 8),
        "slippage_stress": slippage_stress,
    }


def _policy_blockers(metrics: dict, minimum_trades: int) -> list[str]:
    blockers: list[str] = []
    if metrics.get("sequence_order_verified") is False:
        blockers.append("trade_sequence_order_unverified")
    if int(metrics.get("total_trades") or 0) < minimum_trades:
        blockers.append(
            f"sample_below_minimum:{metrics.get('total_trades', 0)}<{minimum_trades}"
        )
    if int(metrics.get("blocked_trades") or 0) > 0:
        blockers.append(f"blocked_trades:{metrics['blocked_trades']}")
    if int(metrics.get("unsafe_calibration_trades") or 0) > 0:
        blockers.append(
            f"unsafe_pnl_calibration:{metrics['unsafe_calibration_trades']}"
        )
    if metrics.get("net_pnl") is not None and float(metrics["net_pnl"]) <= 0:
        blockers.append("non_positive_net_pnl")
    if (
        metrics.get("expectancy") is not None
        and float(metrics["expectancy"]) <= 0
    ):
        blockers.append("non_positive_expectancy")
    return blockers


def _robust_score(metrics: dict) -> float:
    net = float(
        metrics.get("net_pnl")
        if metrics.get("net_pnl") is not None
        else metrics.get("exploratory_net_pnl") or 0.0
    )
    drawdown = max(float(metrics.get("max_drawdown") or 0.0), 1.0)
    return net / drawdown


def _blocked_independent_certification(
    *,
    expected_pairs: set[tuple[str, str]],
    blockers: Iterable[str],
) -> dict:
    return {
        "schema_version": simulation_certifier.SCHEMA_VERSION,
        "rows_expected": len(expected_pairs),
        "rows_checked": 0,
        "certified_rows": 0,
        "mismatched_rows": 0,
        "blocked_rows": len(expected_pairs),
        "tickets_expected": 0,
        "certified_tickets": 0,
        "mismatched_tickets": 0,
        "blocked_tickets": 0,
        "proof_sha256": simulation_certifier.sha256_json([]),
        "deterministic": True,
        "complete": False,
        "conclusions_allowed": False,
        "blockers": list(dict.fromkeys(str(item) for item in blockers)),
    }


def _certification_trade_days(trade: dict) -> tuple[list, list[str]]:
    days = set()
    blockers: list[str] = []
    tickets = list(trade.get("tickets") or [])
    for index, ticket in enumerate(tickets):
        label = ticket.get("ticket") or ticket.get("position_id") or index
        for field in ("open_dt_utc", "close_dt_utc"):
            value = ticket.get(field)
            if value in (None, "") and field == "close_dt_utc":
                continue
            try:
                parsed = datetime.fromisoformat(
                    str(value).replace("Z", "+00:00")
                )
            except (TypeError, ValueError):
                blockers.append(
                    f"invalid_certification_{field}:{label}"
                )
                continue
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                blockers.append(
                    f"naive_certification_{field}:{label}"
                )
                continue
            days.add(parsed.astimezone(timezone.utc).date())
    if not days:
        blockers.append("missing_certification_trade_days")
    return sorted(days), list(dict.fromkeys(blockers))


def _build_independent_certification(
    *,
    trades: list[dict],
    policies: list[strategy_policies.StrategyPolicy],
    rows_by_policy: dict[str, list[dict]],
    providers: dict[str, dict],
    tick_cache_dir: Path | None,
    money_contract_path: Path | None,
    money_tick_cache_dir: Path | None,
    expected_proof_sha256: str | None = None,
) -> tuple[dict, list[dict]]:
    policy_payloads = {
        policy.policy_id: policy.to_dict()
        for policy in policies
    }
    expected_pairs = {
        (str(trade.get("sig_id") or ""), policy.policy_id)
        for trade in trades
        for policy in policies
    }
    expected_count = len(trades) * len(policies)
    if len(expected_pairs) != expected_count or any(
        not sig_id or not policy_id
        for sig_id, policy_id in expected_pairs
    ):
        return (
            _blocked_independent_certification(
                expected_pairs=expected_pairs,
                blockers=["invalid_or_duplicate_certification_identity"],
            ),
            [],
        )
    if money_contract_path is None or not Path(
        money_contract_path
    ).is_file():
        return (
            _blocked_independent_certification(
                expected_pairs=expected_pairs,
                blockers=["independent_money_contract_missing"],
            ),
            [],
        )
    if tick_cache_dir is None or not Path(tick_cache_dir).is_dir():
        return (
            _blocked_independent_certification(
                expected_pairs=expected_pairs,
                blockers=["independent_market_tick_cache_missing"],
            ),
            [],
        )

    try:
        money_contract = json.loads(
            Path(money_contract_path).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return (
            _blocked_independent_certification(
                expected_pairs=expected_pairs,
                blockers=["independent_money_contract_invalid_json"],
            ),
            [],
        )
    if not isinstance(money_contract, dict):
        return (
            _blocked_independent_certification(
                expected_pairs=expected_pairs,
                blockers=["independent_money_contract_invalid_json"],
            ),
            [],
        )

    instrument = money_contract.get("instrument") or {}
    account = money_contract.get("account") or {}
    conversion = money_contract.get("conversion") or {}
    orientation = conversion.get("orientation")
    if (
        orientation != "identity"
        and (
            money_tick_cache_dir is None
            or not Path(money_tick_cache_dir).is_dir()
        )
    ):
        return (
            _blocked_independent_certification(
                expected_pairs=expected_pairs,
                blockers=["independent_conversion_tick_cache_missing"],
            ),
            [],
        )

    market_cache = simulation_oracle.IndependentTickCache(
        Path(tick_cache_dir),
        expected_symbol=str(instrument.get("symbol") or ""),
        require_market_session=True,
    )
    conversion_cache = None
    if orientation != "identity":
        conversion_cache = simulation_oracle.IndependentTickCache(
            Path(money_tick_cache_dir),
            expected_symbol=str(conversion.get("symbol") or ""),
            require_market_session=False,
        )

    def identity_quote_loader(_day):
        return pd.DataFrame(), None

    quote_loader = (
        conversion_cache.quote_loader
        if conversion_cache is not None
        else identity_quote_loader
    )
    try:
        money_oracle = simulation_oracle.IndependentMoneyOracle(
            money_contract,
            quote_loader=quote_loader,
        )
    except (TypeError, ValueError) as exc:
        return (
            _blocked_independent_certification(
                expected_pairs=expected_pairs,
                blockers=[
                    "independent_money_contract_invalid:"
                    f"{type(exc).__name__}"
                ],
            ),
            [],
        )

    candidate_rows: dict[tuple[str, str], list[dict]] = {}
    for policy_id, rows in rows_by_policy.items():
        for row in rows:
            key = (str(row.get("sig_id") or ""), str(policy_id))
            candidate_rows.setdefault(key, []).append(row)

    certificates: list[dict] = []
    money_contract_sha256 = simulation_run_provenance.sha256_file(
        Path(money_contract_path)
    )
    tick_size = float(instrument.get("tick_size"))
    currency_digits = int(account.get("currency_digits"))
    for trade in trades:
        sig_id = str(trade.get("sig_id") or "")
        trade_days, day_blockers = _certification_trade_days(trade)
        market_frames: list[pd.DataFrame] = []
        market_evidence: list[dict] = []
        conversion_evidence: list[dict] = []
        artifact_blockers = list(day_blockers)
        for day in trade_days:
            frame, evidence, blockers = market_cache.load_day(day)
            artifact_blockers.extend(blockers)
            if evidence is not None:
                market_evidence.append(evidence)
            if not frame.empty:
                market_frames.append(frame)
            if conversion_cache is not None:
                _frame, evidence, blockers = conversion_cache.load_day(day)
                artifact_blockers.extend(blockers)
                if evidence is not None:
                    conversion_evidence.append(evidence)
        ticks = (
            pd.concat(market_frames, ignore_index=True)
            if market_frames
            else pd.DataFrame(columns=["time_utc", "bid", "ask"])
        )
        prepared_ticks, preparation_blockers = (
            simulation_oracle.prepare_tick_window(ticks)
        )
        artifact_blockers.extend(preparation_blockers)

        provider_signal = providers.get(sig_id)
        for policy in policies:
            policy_payload = policy_payloads[policy.policy_id]
            policy_artifact_blockers = list(artifact_blockers)
            if policy.mode != "follow_actual":
                policy_artifact_blockers.extend(
                    simulation_oracle.counterfactual_horizon_blockers(
                        trade=trade,
                        market_tick_evidence=market_evidence,
                        conversion_tick_evidence=conversion_evidence,
                        require_conversion=conversion_cache is not None,
                    )
                )
            candidates = candidate_rows.get(
                (sig_id, policy.policy_id),
                [],
            )
            if len(candidates) == 1:
                candidate = candidates[0]
            else:
                candidate = {
                    "sig_id": sig_id,
                    "strategy": policy.policy_id,
                    "status": "blocked",
                    "blockers": [
                        (
                            "missing_candidate_row"
                            if not candidates
                            else "duplicate_candidate_row"
                        )
                    ],
                    "tickets": [],
                }
            if policy_artifact_blockers:
                oracle = {
                    "sig_id": sig_id,
                    "strategy": policy.policy_id,
                    "status": "blocked",
                    "blockers": list(dict.fromkeys(
                        policy_artifact_blockers
                    )),
                    "tickets": [],
                }
            else:
                oracle = simulation_oracle.replay_policy_trade(
                    trade=trade,
                    ticks=prepared_ticks,
                    policy=policy_payload,
                    provider_signal=provider_signal,
                    money_oracle=money_oracle,
                    tick_size=tick_size,
                )
            source_evidence = simulation_certifier.build_source_evidence(
                trade=trade,
                provider_signal=provider_signal,
                policy=policy_payload,
                market_tick_evidence=market_evidence,
                conversion_tick_evidence=conversion_evidence,
                money_contract_sha256=money_contract_sha256,
            )
            certificates.append(simulation_certifier.certify_trade(
                candidate=candidate,
                oracle=oracle,
                tick_size=tick_size,
                currency_digits=currency_digits,
                source_evidence=source_evidence,
            ))

    summary = simulation_certifier.summarize_run(
        certificates=certificates,
        expected_pairs=expected_pairs,
        expected_proof_sha256=expected_proof_sha256,
    )
    return summary, certificates


def _apply_independent_certification_gate(
    report: dict,
    certification: dict,
) -> None:
    report["independent_certification"] = certification
    complete = bool(
        certification.get("complete")
        and certification.get("conclusions_allowed")
        and not certification.get("blockers")
    )
    report.setdefault("validation", {})[
        "independent_certification_complete"
    ] = complete
    if complete:
        return
    report["validation"]["mode"] = "diagnostic_only"
    selection = report.setdefault("selection", {})
    selection["selected_policy"] = None
    selection["exploratory_ranking"] = []
    blockers = selection.setdefault("global_blockers", [])
    blocker = "independent_simulation_certification_incomplete"
    if blocker not in blockers:
        blockers.append(blocker)


def select_strategy(
    scores: Iterable[dict],
    *,
    minimum_trades: int,
    oos_validated: bool,
) -> dict:
    scores = list(scores)
    policy_blockers = {
        row["policy_id"]: _policy_blockers(row["metrics"], minimum_trades)
        for row in scores
    }
    global_blockers = [] if oos_validated else ["oos_not_validated"]
    actual_control = next(
        (row for row in scores if row.get("policy_id") == "follow_actual"),
        None,
    )
    if actual_control is not None:
        blocked_actual = int(
            actual_control.get("metrics", {}).get("blocked_trades") or 0)
        if blocked_actual:
            global_blockers.append(
                f"baseline_replay_blocked:{blocked_actual}")
    eligible_candidates = [
        row
        for row in scores
        if not policy_blockers[row["policy_id"]]
    ]
    exploratory = sorted(
        eligible_candidates,
        key=lambda row: (
            _robust_score(row["metrics"]),
            float(row["metrics"].get("exploratory_net_pnl") or 0.0),
        ),
        reverse=True,
    )
    selected = None
    if not global_blockers and exploratory:
        selected = exploratory[0]["policy_id"]
    return {
        "minimum_trades": minimum_trades,
        "oos_validated": oos_validated,
        "selected_policy": selected,
        "global_blockers": global_blockers,
        "policy_blockers": policy_blockers,
        "exploratory_ranking": [row["policy_id"] for row in exploratory],
        "ranking_excluded": {
            policy_id: blockers
            for policy_id, blockers in policy_blockers.items()
            if blockers
        },
        "ranking_rule": "net_pnl_divided_by_max_drawdown",
    }


def _provider_signals_in_scope(
    catalog: dict | None,
    from_date: str | None,
    to_date: str | None,
) -> list[dict]:
    selected = []
    for signal in (catalog or {}).get("signals") or []:
        # Catalog v1 contained only formal signals. Schema v2 retains context,
        # summaries and unresolved candidates beside them, but those records
        # must never enter strategy denominators.
        if signal.get("record_type", "formal_signal") != "formal_signal":
            continue
        ts = signal.get("first_observed_utc") or signal.get("signal_ts_utc")
        day = str(ts or "")[:10]
        if not day:
            continue
        if from_date and day < from_date:
            continue
        if to_date and day > to_date:
            continue
        selected.append(signal)
    return selected


def _canonical_scope(
    catalog: dict | None,
    from_date: str | None,
    to_date: str | None,
) -> dict:
    selected = _provider_signals_in_scope(catalog, from_date, to_date)
    return {
        "provider_signals": len(selected),
        "complete_signals": sum(
            row.get("semantic_status") == "complete" for row in selected),
        "incomplete_signals": sum(
            row.get("semantic_status") != "complete" for row in selected),
        "executed_signals": sum(
            int(row.get("execution_count") or 0) > 0 for row in selected),
        "unexecuted_signals": sum(
            int(row.get("execution_count") or 0) == 0 for row in selected),
        "by_channel": {
            channel: sum(row.get("channel") == channel for row in selected)
            for channel in ("canal1", "canal2")
        },
    }


def _provider_by_execution(catalog: dict | None) -> dict[str, dict]:
    linked: dict[str, dict] = {}
    for signal in (catalog or {}).get("signals") or []:
        for sig_id in signal.get("execution_sig_ids") or []:
            linked[str(sig_id)] = signal
    return linked


def _market_replay_summary(effective_baselines: list[dict]) -> dict[str, int]:
    statuses = [
        str((row.get("baseline") or {}).get("status") or "blocked").lower()
        for row in effective_baselines
    ]
    exact = sum(status == "exact" for status in statuses)
    external = sum(
        status == "external_intervention" for status in statuses
    )
    delayed = sum(
        status == "delayed_close_observation" for status in statuses
    )
    mismatched = sum(status == "mismatch" for status in statuses)
    return {
        "selected_trades": len(statuses),
        "exact": exact,
        "external_interventions": external,
        "delayed_close_observations": delayed,
        "blocked": len(statuses) - exact - external - delayed - mismatched,
        "mismatched": mismatched,
    }


def _require_current_causal_contract(
    baseline: dict | None,
    *,
    current_tick_contracts: dict[str, dict] | None = None,
    required_days: Iterable[str] = (),
) -> dict | None:
    if not isinstance(baseline, dict):
        return baseline
    if baseline.get("status") not in {
        "exact",
        "external_intervention",
        "delayed_close_observation",
    }:
        return baseline
    original_status = baseline.get("status")
    blockers = []
    if baseline.get("validation_contract") != "causal_path_v3":
        blockers.append("causal_path_contract_unverified")
    if baseline.get("fill_price_authority") != "mt5_deals":
        blockers.append("fill_price_authority_unverified")
    if baseline.get("market_session_contract") != MARKET_SESSION_CONTRACT:
        blockers.append("market_session_contract_unverified")
    baseline_evidence = baseline.get("tick_contract_evidence")
    current_tick_contracts = current_tick_contracts or {}
    for day in sorted(set(str(day) for day in required_days)):
        expected = (
            baseline_evidence.get(day)
            if isinstance(baseline_evidence, dict)
            else None
        )
        current = current_tick_contracts.get(day)
        if not isinstance(expected, dict):
            blockers.append(
                f"baseline_tick_contract_evidence_missing:{day}"
            )
            continue
        if not isinstance(current, dict):
            blockers.append(f"current_tick_contract_missing:{day}")
            continue
        comparable_fields = (
            "symbol",
            "parquet_sha256",
            "contract_sha256",
        )
        if any(
            not expected.get(field)
            or expected.get(field) != current.get(field)
            for field in comparable_fields
        ):
            blockers.append(f"baseline_tick_contract_mismatch:{day}")
    if not blockers:
        return baseline
    return {
        **baseline,
        "status": "blocked",
        "blockers": list(dict.fromkeys(
            [*(baseline.get("blockers") or []), *blockers]
        )),
    }


def _counterfactual_horizon_blockers(
    trade: dict,
    current_tick_contracts: dict[str, dict],
) -> list[str]:
    blockers: list[str] = []
    opened_values = {
        opened
        for ticket in trade.get("tickets") or []
        if (
            opened := strategy_simulator._parse_dt(
                ticket.get("open_dt_utc")
            )
        ) is not None
    }
    trade_opened = strategy_simulator._parse_dt(trade.get("open_dt_utc"))
    if trade_opened is not None:
        opened_values.add(trade_opened)
    for opened in sorted(opened_values):
        day = opened.date().isoformat()
        contract = current_tick_contracts.get(day)
        if not isinstance(contract, dict):
            blockers.append(f"missing_policy_horizon_contract:{day}")
            continue
        coverage = contract.get("coverage")
        if (
            not isinstance(coverage, dict)
            or coverage.get("coverage_source") == "legacy_parquet_bounds"
        ):
            blockers.append(f"unverified_policy_horizon_coverage:{day}")
            continue
        try:
            session_close = broker_session_close_utc(
                opened,
                utc_offset_seconds=contract.get("utc_offset_seconds"),
            )
        except ValueError:
            session_close = None
        utc_day_end = opened.replace(
            hour=23,
            minute=59,
            second=59,
            microsecond=0,
        )
        if session_close is None or session_close <= opened:
            blockers.append(f"invalid_policy_horizon:{day}")
            continue
        horizon = min(utc_day_end, session_close)
        if not ensure_replay_tick_cache.coverage_satisfies_window(
            contract,
            opened,
            horizon,
        ):
            blockers.append(f"incomplete_policy_horizon:{day}")
    return list(dict.fromkeys(blockers))


def _market_replay_verified(summary: dict[str, int]) -> bool:
    selected = summary["selected_trades"]
    return (
        selected > 0
        and summary["exact"] == selected
        and summary["blocked"] == 0
        and summary["mismatched"] == 0
    )


def _market_replay_strategy_eligible(summary: dict[str, int]) -> bool:
    selected = summary["selected_trades"]
    accounted = (
        int(summary.get("exact") or 0)
        + int(summary.get("external_interventions") or 0)
        + int(summary.get("delayed_close_observations") or 0)
    )
    return (
        selected > 0
        and accounted == selected
        and summary["blocked"] == 0
        and summary["mismatched"] == 0
    )



def _provider_farm_configuration(
    latency_scenarios_ms: Iterable[int] | None,
    volume_per_leg: float,
) -> tuple[tuple[int, ...], float]:
    latency_values = tuple(
        (0,) if latency_scenarios_ms is None else latency_scenarios_ms
    )
    if not latency_values:
        raise ValueError("provider latency scenarios cannot be empty")
    if any(
        isinstance(value, bool)
        or not isinstance(value, Integral)
        or value < 0
        for value in latency_values
    ):
        raise ValueError(
            "provider latency scenarios must be non-negative integers"
        )
    latencies = tuple(int(value) for value in latency_values)
    if len(set(latencies)) != len(latencies):
        raise ValueError("provider latency scenarios must be unique")

    if (
        isinstance(volume_per_leg, bool)
        or not isinstance(volume_per_leg, Real)
        or not isfinite(float(volume_per_leg))
        or float(volume_per_leg) <= 0
    ):
        raise ValueError("provider volume per leg must be positive and finite")
    return latencies, float(volume_per_leg)


def strategy_data_preflight(
    trades: list[dict],
    catalog: dict,
    *,
    tick_cache_dir: Path,
    money_tick_cache_dir: Path,
    from_date: str | None = None,
    to_date: str | None = None,
    provider_latency_scenarios_ms: Iterable[int] | None = None,
) -> dict:
    """Prove every market and conversion day before running the farm."""
    latencies, _volume = _provider_farm_configuration(
        provider_latency_scenarios_ms,
        0.01,
    )
    since = ensure_replay_tick_cache._parse_dt(from_date)
    until = ensure_replay_tick_cache._parse_dt(to_date)
    offsets = ensure_replay_tick_cache.verified_cache_offset_candidates(
        Path(tick_cache_dir),
        expected_symbol="XAUUSD",
    )
    provider_days = ensure_replay_tick_cache.required_provider_dates(
        catalog,
        since=since,
        until=until,
        latency_scenarios_ms=latencies,
        offset_candidates_seconds=(
            offsets
            or ensure_replay_tick_cache.DEFAULT_OFFSET_CANDIDATES_SECONDS
        ),
    )
    market = ensure_replay_tick_cache.build_status(
        trades,
        cache_dir=Path(tick_cache_dir),
        since=since,
        until=until,
        pad_minutes=5,
        expected_symbol="XAUUSD",
        additional_required_days=provider_days,
    )
    money_windows = ensure_money_tick_cache.required_money_day_windows(
        trades,
        since=since,
        until=until,
        additional_required_days=provider_days,
    )
    money = ensure_money_tick_cache._classify_cache_days(
        money_windows,
        cache_dir=Path(money_tick_cache_dir),
        symbol="EURUSD",
    )
    errors = [
        f"market_tick_{status}:{day}"
        for status, key in (
            ("missing", "missing_days"),
            ("invalid", "invalid_days"),
            ("incomplete", "incomplete_days"),
        )
        for day in market.get(key) or []
    ]
    errors.extend(
        f"money_tick_{status}:{day.isoformat()}"
        for status, key in (
            ("missing", "missing"),
            ("invalid", "invalid"),
            ("incomplete", "incomplete"),
        )
        for day in money[key]
    )
    required_days = sorted({
        *(market.get("required_days") or []),
        *(day.isoformat() for day in money_windows),
    })
    return {
        "ok": not errors,
        "required_tick_days": required_days,
        "provider_required_days": [
            day.isoformat() for day in provider_days
        ],
        "verified_offset_candidates_seconds": offsets,
        "errors": errors,
        "market_status": market,
        "money_status": {
            key: [day.isoformat() for day in money[key]]
            for key in ("cached", "missing", "invalid", "incomplete")
        },
    }


def _formal_signal_for_spec(signal: dict) -> dict:
    if signal.get("record_type") == "formal_signal":
        return signal
    # Catalog v1 contained only formal signals and omitted this discriminator.
    if "record_type" not in signal:
        return {**signal, "record_type": "formal_signal"}
    return signal


def _provider_tick_window(
    spec: provider_trade_spec.ProviderTradeSpec,
    *,
    utc_offset_seconds: int | None,
) -> tuple[dict | None, list[str]]:
    trigger = spec.trigger_observed_utc
    if trigger is None:
        return None, []
    try:
        threshold = trigger + timedelta(milliseconds=spec.latency_ms)
        horizon = broker_session_close_utc(
            threshold,
            utc_offset_seconds=utc_offset_seconds,
        )
    except (OverflowError, ValueError):
        if utc_offset_seconds is None:
            return None, [
                f"missing_broker_session_offset:{trigger.date().isoformat()}"
            ]
        return None, ["entry_threshold_out_of_range"]
    if horizon is None or threshold >= horizon:
        return None, ["provider_trigger_outside_broker_session"]
    return {
        "sig_id": spec.provider_signal_id,
        "provider_signal_id": spec.provider_signal_id,
        "signal_dt_utc": threshold.isoformat(),
        "open_dt_utc": threshold.isoformat(),
        "close_dt_utc": horizon.isoformat(),
    }, []

def _slice_provider_ticks(ticks: pd.DataFrame, tick_window: dict) -> pd.DataFrame:
    if ticks.empty or "time_utc" not in ticks.columns:
        return ticks
    try:
        tick_times = pd.to_datetime(ticks["time_utc"], utc=True, format="mixed")
        start = pd.Timestamp(tick_window["open_dt_utc"])
        end = pd.Timestamp(tick_window["close_dt_utc"])
    except (OverflowError, TypeError, ValueError):
        return ticks
    return ticks.loc[(tick_times >= start) & (tick_times <= end)]


def _replace_missing_tick_blocker(
    row: dict,
    tick_blockers: Iterable[str],
) -> dict:
    tick_blockers = list(dict.fromkeys(str(item) for item in tick_blockers))
    if not tick_blockers:
        return row

    result = dict(row)
    existing = [
        str(item)
        for item in result.get("blockers") or []
        if str(item) != "missing_ticks"
    ]
    result["status"] = "blocked"
    result["strategy_value"] = None
    result["strategy_pnl"] = None
    result["blockers"] = list(dict.fromkeys((*existing, *tick_blockers)))
    entry = dict(result.get("entry") or {})
    entry_blockers = [
        str(item)
        for item in entry.get("blockers") or []
        if str(item) != "missing_ticks"
    ]
    entry["status"] = "blocked"
    entry["blockers"] = list(dict.fromkeys(
        (*entry_blockers, *tick_blockers)
    ))
    result["entry"] = entry
    return result


def _load_money_converter(
    contract_path: Path | None,
    tick_cache_dir: Path | None,
) -> tuple[broker_money.BrokerMoneyConverter | None, dict]:
    if contract_path is None or tick_cache_dir is None:
        return None, {
            "contract_file": str(contract_path) if contract_path is not None else None,
            "contract_verified": False,
            "blockers": ["broker_money_contract_not_requested"],
        }
    path = Path(contract_path)
    if not path.is_file():
        return None, {
            "contract_file": str(path),
            "contract_verified": False,
            "blockers": ["missing_broker_money_contract"],
        }
    try:
        contract = broker_money.load_contract(path)
        blockers = broker_money.validate_contract_metadata(contract)
        if blockers:
            return None, {
                "contract_file": str(path),
                "contract_verified": False,
                "blockers": blockers,
            }
        converter = broker_money.BrokerMoneyConverter(
            contract,
            tick_cache_dir=tick_cache_dir,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return None, {
            "contract_file": str(path),
            "contract_verified": False,
            "blockers": [
                f"broker_money_contract_load_failed:{type(exc).__name__}"
            ],
        }
    return converter, {
        "contract_file": str(path),
        "contract_verified": True,
        "account_currency": converter.currency,
        "blockers": [],
    }


def _apply_provider_money_contract(
    groups: list[dict],
    provider_specs: list[dict],
    converter: broker_money.BrokerMoneyConverter,
) -> tuple[list[dict], dict[str, int]]:
    """Attach cent-rounded account-currency P&L to virtual provider rows.

    Price-path simulation remains the source of truth for fills. This step only
    translates those already-determined fills into the broker account currency
    and turns a missing conversion quote into an explicit row blocker.
    """
    spec_by_key = {
        (
            str(spec.get("provider_signal_id") or ""),
            int(spec.get("latency_ms") or 0),
        ): spec
        for spec in provider_specs
    }
    updated_groups: list[dict] = []
    verified_rows = 0
    blocked_rows = 0
    for group in groups:
        updated_group = dict(group)
        updated_results: list[dict] = []
        for raw_row in group.get("results") or []:
            row = dict(raw_row)
            if row.get("status") != "simulated_price_path":
                row.setdefault("money_status", "not_applicable")
                updated_results.append(row)
                continue
            key = (
                str(row.get("provider_signal_id") or ""),
                int(row.get("latency_scenario_ms") or 0),
            )
            spec = spec_by_key.get(key)
            if spec is None or str(spec.get("direction") or "") not in {
                "BUY",
                "SELL",
            }:
                row["status"] = "blocked"
                row["money_status"] = "blocked"
                row["strategy_pnl"] = None
                row["money_blockers"] = ["missing_provider_money_spec"]
                row["blockers"] = list(dict.fromkeys(
                    [*(row.get("blockers") or []),
                     "missing_provider_money_spec"]
                ))
                blocked_rows += 1
                updated_results.append(row)
                continue
            converted = broker_money.apply_money_contract(
                row,
                direction=str(spec["direction"]),
                converter=converter,
            )
            if converted.get("money_status") == "verified":
                verified_rows += 1
            else:
                converted["status"] = "blocked"
                converted["blockers"] = list(dict.fromkeys(
                    [*(converted.get("blockers") or []),
                     *(converted.get("money_blockers") or [])]
                ))
                blocked_rows += 1
            updated_results.append(converted)
        updated_group["results"] = updated_results
        updated_groups.append(updated_group)
    return updated_groups, {
        "rows": verified_rows + blocked_rows,
        "verified_rows": verified_rows,
        "blocked_rows": blocked_rows,
    }


def _executed_money_summary(
    rows_by_policy: dict[str, list[dict]],
) -> dict:
    rows = [
        row
        for policy_rows in rows_by_policy.values()
        for row in policy_rows
    ]
    verified_rows = 0
    blockers: list[str] = []
    for row in rows:
        unsafe = _unsafe_calibration(row)
        if (
            row.get("status") != "blocked"
            and row.get("strategy_pnl") is not None
            and not unsafe
        ):
            verified_rows += 1
            continue
        blockers.extend(str(item) for item in row.get("blockers") or [])
        blockers.extend(
            str(item)
            for ticket in row.get("tickets") or []
            for item in ticket.get("money_blockers") or []
        )
        if unsafe:
            blockers.append(
                f"unsafe_counterfactual_money:{row.get('sig_id')}:"
                f"{row.get('strategy')}"
            )
    return {
        "rows": len(rows),
        "verified_rows": verified_rows,
        "blocked_rows": len(rows) - verified_rows,
        "blockers": list(dict.fromkeys(blockers)),
    }


def _build_provider_policy_results(
    provider_signals: list[dict],
    policies: list[strategy_policies.StrategyPolicy],
    tick_loader: observed_tick_replay_validator.ReplayTickFrameCache,
    *,
    latency_scenarios_ms: tuple[int, ...],
    volume_per_leg: float,
    progress_step: Callable[[str], None] | None = None,
) -> tuple[list[dict], dict, list[dict]]:
    signal_ids = [
        str(signal.get("provider_signal_id") or "")
        for signal in provider_signals
    ]
    if any(not signal_id for signal_id in signal_ids):
        raise RuntimeError("provider farm row accounting: missing signal id")
    duplicate_ids = sorted(
        signal_id
        for signal_id, count in Counter(signal_ids).items()
        if count > 1
    )
    if duplicate_ids:
        raise RuntimeError(
            "provider farm row accounting: duplicate signal ids: "
            + ",".join(duplicate_ids)
        )

    rows_by_policy = {policy.policy_id: [] for policy in policies}
    selected_specs: list[dict] = []
    for signal in provider_signals:
        formal_signal = _formal_signal_for_spec(signal)
        for latency_ms in latency_scenarios_ms:
            spec = provider_trade_spec.build_trade_spec(
                formal_signal,
                latency_ms=latency_ms,
                volume_per_leg=volume_per_leg,
            )
            selected_specs.append(spec.to_dict())
            ticks = pd.DataFrame()
            tick_window = None
            tick_blockers: list[str] = []
            if (
                spec.entry_ready
                and spec.trigger_observed_utc is not None
            ):
                contract, contract_error = tick_loader.load_contract_for_day(
                    spec.trigger_observed_utc.date()
                )
                if contract_error is not None:
                    tick_blockers.append(contract_error)
                else:
                    tick_window, window_blockers = _provider_tick_window(
                        spec,
                        utc_offset_seconds=contract.get(
                            "utc_offset_seconds"
                        ),
                    )
                    tick_blockers.extend(window_blockers)
            if spec.entry_ready and tick_window is not None:
                ticks, missing = tick_loader.load_ticks_for_trade(
                    tick_window,
                    pad_minutes=0,
                )
                tick_blockers.extend(missing)
                if tick_blockers:
                    ticks = pd.DataFrame()
                else:
                    ticks = _slice_provider_ticks(ticks, tick_window)
                    ticks, prepare_blocker = (
                        provider_strategy_simulator.prepare_replay_ticks(ticks)
                    )
                    if prepare_blocker is not None:
                        tick_blockers.append(prepare_blocker)
                        ticks = pd.DataFrame()

            provider_result_cache: dict = {}
            for policy in policies:
                row = provider_strategy_simulator.simulate_provider_policy(
                    spec,
                    ticks,
                    policy,
                    result_cache=provider_result_cache,
                )
                row = _replace_missing_tick_blocker(row, tick_blockers)
                row["latency_scenario_ms"] = latency_ms
                rows_by_policy[policy.policy_id].append(row)
                if progress_step is not None:
                    progress_step(
                        "Proveedor "
                        f"{spec.provider_signal_id} / {policy.policy_id} / "
                        f"{latency_ms} ms"
                    )

    groups = [
        {
            "policy_id": policy.policy_id,
            "policy": policy.to_dict(),
            "results": rows_by_policy[policy.policy_id],
        }
        for policy in policies
    ]
    rows = [row for group in groups for row in group["results"]]
    expected = Counter(
        (signal_id, policy.policy_id, latency_ms)
        for signal_id in signal_ids
        for policy in policies
        for latency_ms in latency_scenarios_ms
    )
    emitted = Counter(
        (
            str(row.get("provider_signal_id") or ""),
            str(row.get("policy_id") or ""),
            row.get("latency_scenario_ms"),
        )
        for row in rows
    )
    allowed_statuses = {"blocked", "simulated_price_path"}
    if emitted != expected or any(
        row.get("status") not in allowed_statuses for row in rows
    ):
        raise RuntimeError("provider farm row accounting mismatch")

    expected_per_signal = len(policies) * len(latency_scenarios_ms)
    emitted_per_signal = Counter(
        str(row.get("provider_signal_id") or "") for row in rows
    )
    omitted = [
        signal_id
        for signal_id in signal_ids
        if emitted_per_signal[signal_id] != expected_per_signal
    ]
    scope = {
        "formal_signals": len(provider_signals),
        "policy_count": len(policies),
        "latency_scenarios_ms": list(latency_scenarios_ms),
        "rows_expected": len(expected),
        "rows_emitted": len(rows),
        "simulated_rows": sum(
            row.get("status") == "simulated_price_path" for row in rows
        ),
        "blocked_rows": sum(row.get("status") == "blocked" for row in rows),
        "signals_omitted": omitted,
    }
    if scope["rows_emitted"] != scope["rows_expected"] or omitted:
        raise RuntimeError("provider farm row accounting mismatch")
    return groups, scope, selected_specs


def build_policy_score(
    policy: strategy_policies.StrategyPolicy,
    rows: list[dict],
    *,
    include_trades: bool,
) -> dict:
    score = {
        "policy_id": policy.policy_id,
        "policy": policy.to_dict(),
        "metrics": calculate_policy_metrics(rows),
        "channel_metrics": {
            channel: calculate_policy_metrics(
                row for row in rows if row.get("channel") == channel)
            for channel in ("canal1", "canal2")
        },
    }
    if include_trades:
        score["trades"] = rows
    return score


def build_farm_execution(
    trades: list[dict],
    baseline_rows: list[dict],
    *,
    tick_cache_dir: Path,
    policies: list[strategy_policies.StrategyPolicy] | None = None,
    catalog: dict | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    minimum_trades: int = 200,
    include_trades: bool = False,
    provider_latency_scenarios_ms: Iterable[int] | None = None,
    provider_volume_per_leg: float = 0.01,
    money_contract_path: Path | None = None,
    money_tick_cache_dir: Path | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> FarmExecution:
    policies = policies or strategy_policies.default_policy_catalog()
    latency_scenarios_ms, provider_volume_per_leg = (
        _provider_farm_configuration(
            provider_latency_scenarios_ms,
            provider_volume_per_leg,
        )
    )
    selected_trades = [
        trade
        for trade in trades
        if strategy_simulator._date_in_range(trade, from_date, to_date)
    ]
    baselines = strategy_simulator._baseline_by_sig(baseline_rows)
    providers = _provider_by_execution(catalog)
    provider_signals = _provider_signals_in_scope(
        catalog,
        from_date,
        to_date,
    )
    progress_total = (
        len(selected_trades) * len(policies)
        + len(provider_signals) * len(latency_scenarios_ms) * len(policies)
    )
    progress_current = 0

    def progress_step(label: str) -> None:
        nonlocal progress_current
        progress_current += 1
        if progress_callback is not None:
            progress_callback(progress_current, progress_total, label)

    if progress_callback is not None and progress_total == 0:
        progress_callback(0, 0, "Sin combinaciones para simular")
    unit_value, unit_source = strategy_simulator._global_unit_value(
        selected_trades, None)
    tick_loader = observed_tick_replay_validator.ReplayTickFrameCache(
        tick_cache_dir)
    money_converter, money_contract = _load_money_converter(
        money_contract_path,
        money_tick_cache_dir,
    )
    rows_by_policy = {policy.policy_id: [] for policy in policies}
    effective_baselines: list[dict] = []

    def verified_trade_offset_seconds(trade: dict) -> int | None:
        offsets: set[int] = set()
        for day in observed_tick_replay_validator._required_tick_days(
            trade,
            5,
        ):
            contract = tick_loader.verified_contracts.get(day) or {}
            value = contract.get("utc_offset_seconds")
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or abs(value) > 14 * 3600
            ):
                return None
            offsets.add(value)
        if len(offsets) != 1:
            return None
        return next(iter(offsets))

    for trade in selected_trades:
        ticks, missing = tick_loader.load_ticks_for_trade(trade, pad_minutes=5)
        if not ticks.empty:
            ticks = strategy_simulator._normalise_ticks(ticks)
        baseline = baselines.get(str(trade.get("sig_id")))
        provider_signal = providers.get(str(trade.get("sig_id")))
        if missing:
            baseline = {
                "status": "blocked",
                "blockers": list(dict.fromkeys(missing)),
            }
        else:
            baseline = _require_current_causal_contract(
                baseline,
                current_tick_contracts=tick_loader.verified_contracts,
                required_days=(
                    observed_tick_replay_validator._required_tick_days(
                        trade,
                        5,
                    )
                ),
            )
        effective_baselines.append({
            "sig_id": str(trade.get("sig_id")),
            "baseline": baseline,
        })
        verified_utc_offset_seconds = verified_trade_offset_seconds(trade)
        counterfactual_horizon_blockers = (
            _counterfactual_horizon_blockers(
                trade,
                tick_loader.verified_contracts,
            )
        )
        result_cache: dict = {}
        portfolio_cache: dict = {}
        for policy in policies:
            policy_baseline = baseline
            if (
                policy.mode != "follow_actual"
                and counterfactual_horizon_blockers
            ):
                policy_baseline = {
                    **(baseline or {}),
                    "status": "blocked",
                    "blockers": list(dict.fromkeys([
                        *((baseline or {}).get("blockers") or []),
                        *counterfactual_horizon_blockers,
                    ])),
                }
            rows_by_policy[policy.policy_id].append(
                strategy_simulator.simulate_trade(
                    trade,
                    ticks,
                    strategy_name=policy.policy_id,
                    policy=policy,
                    result_cache=result_cache,
                    portfolio_cache=portfolio_cache,
                    baseline_audit=policy_baseline,
                    provider_signal=provider_signal,
                    require_provider_timeline=True,
                    level_timeline_authority="mt5_execution",
                    money_converter=money_converter,
                    verified_utc_offset_seconds=(
                        verified_utc_offset_seconds
                    ),
                    default_unit_value=unit_value,
                    default_unit_source=unit_source,
                    horizon_policy=policy.horizon_policy,
                )
            )
            progress_step(
                f"Ejecutada {trade.get('sig_id')} / {policy.policy_id}"
            )

    scores = []
    for policy in policies:
        rows = rows_by_policy[policy.policy_id]
        scores.append(build_policy_score(
            policy,
            rows,
            include_trades=include_trades,
        ))
    (
        independent_certification,
        independent_certificates,
    ) = _build_independent_certification(
        trades=selected_trades,
        policies=policies,
        rows_by_policy=rows_by_policy,
        providers=providers,
        tick_cache_dir=tick_cache_dir,
        money_contract_path=money_contract_path,
        money_tick_cache_dir=money_tick_cache_dir,
    )
    executed_contract = executed_simulation_contract.validate_contract(
        selected_trades,
        policies,
        rows_by_policy,
    )
    executed_scope = {
        "executed_trades": len(selected_trades),
        "policy_count": len(policies),
        "rows_expected": executed_contract["rows_expected"],
        "rows_emitted": executed_contract["rows_emitted"],
        "blocked_rows": executed_contract["blocked_rows"],
        "entry_invariant_failures": executed_contract[
            "entry_invariant_failures"
        ],
    }

    executed_selection = select_strategy(
        scores,
        minimum_trades=minimum_trades,
        oos_validated=False,
    )
    canonical_scope = _canonical_scope(catalog, from_date, to_date)
    market_replay_summary = _market_replay_summary(effective_baselines)
    market_replay_verified = _market_replay_verified(market_replay_summary)
    market_replay_strategy_eligible = _market_replay_strategy_eligible(
        market_replay_summary
    )
    if not market_replay_strategy_eligible:
        if "market_replay_not_exact" not in executed_selection["global_blockers"]:
            executed_selection["global_blockers"].append(
                "market_replay_not_exact"
            )
        executed_selection["selected_policy"] = None
        executed_selection["exploratory_ranking"] = []

    (
        provider_policy_results,
        provider_scope,
        provider_specs,
    ) = _build_provider_policy_results(
        provider_signals,
        policies,
        tick_loader,
        latency_scenarios_ms=latency_scenarios_ms,
        volume_per_leg=provider_volume_per_leg,
        progress_step=progress_step,
    )
    actual_money_validation = {
        "verified": False,
        "account_currency": None,
        "tickets_checked": 0,
        "exact_tickets": 0,
        "mismatched_tickets": 0,
        "blocked_tickets": 0,
        "blockers": list(money_contract["blockers"]),
    }
    provider_money_summary = {
        "rows": 0,
        "verified_rows": 0,
        "blocked_rows": 0,
    }
    if money_converter is not None:
        actual_money_validation = (
            broker_money.validate_executed_money_contract(
                selected_trades,
                money_converter,
            )
        )
        provider_policy_results, provider_money_summary = (
            _apply_provider_money_contract(
                provider_policy_results,
                provider_specs,
                money_converter,
            )
        )
    provider_policy_scores = [
        {
            "policy_id": group["policy_id"],
            "policy": group["policy"],
            "metrics": calculate_policy_metrics(group["results"]),
        }
        for group in provider_policy_results
    ]
    executed_money_summary = _executed_money_summary(rows_by_policy)
    primary_money_blockers = list(dict.fromkeys(
        [
            *(money_contract.get("blockers") or []),
            *(actual_money_validation.get("blockers") or []),
            *(executed_money_summary.get("blockers") or []),
        ]
    ))
    provider_money_blockers = list(dict.fromkeys(
        [
            *(money_contract.get("blockers") or []),
            *[
                str(blocker)
                for group in provider_policy_results
                for row in group.get("results") or []
                for blocker in row.get("money_blockers") or []
            ],
        ]
    ))
    money_verified = bool(
        money_contract.get("contract_verified")
        and actual_money_validation.get("verified")
        and executed_money_summary["rows"] > 0
        and executed_money_summary["rows"] == executed_money_summary[
            "verified_rows"
        ]
    )
    provider_money_verified = bool(
        money_contract.get("contract_verified")
        and provider_money_summary["rows"] > 0
        and provider_money_summary["rows"] == provider_money_summary[
            "verified_rows"
        ]
    )
    if money_verified:
        money_mode = "verified_account_currency"
    elif money_contract.get("contract_verified"):
        money_mode = "account_currency_diagnostic"
    else:
        money_mode = "diagnostic_only"
    primary_blockers = []
    if not money_contract.get("contract_verified"):
        primary_blockers.append("broker_money_contract_unverified")
    primary_blockers.extend(
        blocker for blocker in primary_money_blockers
        if blocker not in primary_blockers
    )
    if not market_replay_strategy_eligible:
        primary_blockers.append("market_replay_not_exact")
    if not executed_contract["complete"]:
        primary_blockers.append("executed_replay_contract_incomplete")
    selection = copy.deepcopy(executed_selection)
    for blocker in primary_blockers:
        if blocker not in selection["global_blockers"]:
            selection["global_blockers"].append(blocker)
    non_statistical_blockers = [
        blocker
        for blocker in selection["global_blockers"]
        if blocker != "oos_not_validated"
    ]
    if non_statistical_blockers:
        selection["selected_policy"] = None
        selection["exploratory_ranking"] = []

    calibration = {
        "unit_value": round(unit_value, 8),
        "source": unit_source,
    }

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "primary_universe": "executed_mt5",
        "from_date": from_date,
        "to_date": to_date,
        "executed_trade_count": len(selected_trades),
        "policy_count": len(policies),
        "includes_trade_details": include_trades,
        "calibration": calibration,
        "executed_scope": executed_scope,
        "executed_replay_contract": executed_contract,
        "canonical_scope": canonical_scope,
        "provider_scope": provider_scope,
        "provider_diagnostics": {
            "ranking_eligible": False,
            "formal_signals": provider_scope["formal_signals"],
            "money_verified": provider_money_verified,
            "money_blockers": provider_money_blockers,
            "purpose": (
                "coverage_and_missed_opportunity_diagnostics_only"
            ),
        },
        "provider_configuration": {
            "latency_scenarios_ms": list(latency_scenarios_ms),
            "volume_per_leg": provider_volume_per_leg,
        },
        "market_replay": market_replay_summary,
        "validation": {
            "primary_universe": "executed_mt5",
            "price_path_mode": "executed_mt5_entries",
            "entry_authority": "mt5_deals",
            "level_timeline_authority": "confirmed_mt5_history",
            "management_trigger_authority": (
                "canonical_telegram_observed"
            ),
            "money_mode": money_mode,
            "money_contract_verified": bool(
                money_contract.get("contract_verified")
            ),
            "account_currency_money_verified": money_verified,
            "market_replay_verified": market_replay_verified,
            "market_replay_strategy_eligible": market_replay_strategy_eligible,
            "executed_contract_complete": executed_contract["complete"],
            "mode": (
                "verified_executed_counterfactuals"
                if (
                    money_verified
                    and market_replay_strategy_eligible
                    and executed_contract["complete"]
                )
                else "diagnostic_only"
            ),
        },
        "money_contract": money_contract,
        "money_validation": actual_money_validation,
        "executed_money": executed_money_summary,
        "provider_money": provider_money_summary,
        "selection": selection,
        "policies": scores,
        "provider_policy_scores": provider_policy_scores,
        "provider_policy_results": provider_policy_results,
        "executed_baseline_validation": {
            "executed_trade_count": len(selected_trades),
            "calibration": calibration,
            "market_replay": market_replay_summary,
            "selection": executed_selection,
            "policies": scores,
        },
    }
    _apply_independent_certification_gate(
        report,
        independent_certification,
    )
    effective_providers = [
        {
            "sig_id": str(trade.get("sig_id")),
            "provider_signal": providers.get(str(trade.get("sig_id"))),
        }
        for trade in selected_trades
    ]
    return FarmExecution(
        report=report,
        selected_payloads={
            "replay_trades": selected_trades,
            "effective_baselines": effective_baselines,
            "effective_provider_links": effective_providers,
            "provider_scope": provider_signals,
            "provider_trade_specs": provider_specs,
            "provider_latency_scenarios_ms": list(latency_scenarios_ms),
            "provider_volume_per_leg": [provider_volume_per_leg],
            "provider_policy_results": provider_policy_results,
            "provider_policy_scores": provider_policy_scores,
            "money_validation": actual_money_validation,
            "independent_certificates": independent_certificates,
        },
        policies=[policy.to_dict() for policy in policies],
        required_tick_days=tick_loader.required_days,
        verified_tick_contracts=tick_loader.verified_contracts,
        market_replay_summary=market_replay_summary,
    )


def build_farm_report(
    trades: list[dict],
    baseline_rows: list[dict],
    *,
    tick_cache_dir: Path,
    policies: list[strategy_policies.StrategyPolicy] | None = None,
    catalog: dict | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    minimum_trades: int = 200,
    include_trades: bool = False,
    provider_latency_scenarios_ms: Iterable[int] | None = None,
    provider_volume_per_leg: float = 0.01,
    money_contract_path: Path | None = None,
    money_tick_cache_dir: Path | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> dict:
    return build_farm_execution(
        trades,
        baseline_rows,
        tick_cache_dir=tick_cache_dir,
        policies=policies,
        catalog=catalog,
        from_date=from_date,
        to_date=to_date,
        minimum_trades=minimum_trades,
        include_trades=include_trades,
        provider_latency_scenarios_ms=provider_latency_scenarios_ms,
        provider_volume_per_leg=provider_volume_per_leg,
        money_contract_path=money_contract_path,
        money_tick_cache_dir=money_tick_cache_dir,
        progress_callback=progress_callback,
    ).report


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_report(report: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(simulation_run_provenance.pretty_json_bytes(report))


def compact_latest_report(
    report: dict,
    publication: simulation_run_provenance.PublicationResult,
) -> dict:
    """Keep routine analysis small while linking the lossless full report."""
    compact = copy.deepcopy(report)
    provider_rows = compact.pop("provider_policy_results", [])
    compact["provider_policy_result_count"] = sum(
        len(group.get("results") or [])
        for group in provider_rows
        if isinstance(group, dict)
    )
    archive_ref = {
        "available": False,
        "run_fingerprint": compact.get("provenance", {}).get(
            "run_fingerprint"
        ),
        "result_fingerprint": compact.get("provenance", {}).get(
            "result_fingerprint"
        ),
        "run_card": compact.get("provenance", {}).get("run_card"),
    }
    if publication.run_dir is not None:
        card_path = publication.run_dir / "run_card.json"
        card = json.loads(card_path.read_text(encoding="utf-8"))
        artifact = next(
            (
                item
                for item in card.get("artifacts") or []
                if isinstance(item, dict) and item.get("retained")
            ),
            None,
        )
        if artifact is not None:
            run_card_ref = str(archive_ref.get("run_card") or "run_card.json")
            artifact_ref = (
                Path(run_card_ref).parent / str(artifact["path"])
            ).as_posix()
            archive_ref.update({
                "available": True,
                "path": artifact_ref,
                "compression": artifact.get("compression") or "none",
                "size_bytes": artifact.get("size_bytes"),
                "sha256": artifact.get("sha256"),
                "canonical_size_bytes": artifact.get(
                    "canonical_size_bytes", artifact.get("size_bytes")
                ),
                "canonical_sha256": artifact.get(
                    "canonical_sha256", artifact.get("sha256")
                ),
            })
    compact["details_archive"] = archive_ref
    return compact


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the auditable management strategy farm")
    parser.add_argument("--replay", type=Path, default=DEFAULT_REPLAY)
    parser.add_argument("--replay-manifest", type=Path)
    parser.add_argument("--ledger-source", type=Path)
    parser.add_argument("--events-source", type=Path)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--tick-cache-dir", type=Path, default=DEFAULT_TICK_CACHE)
    parser.add_argument(
        "--money-contract",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--money-tick-cache-dir",
        type=Path,
        default=DEFAULT_MONEY_TICK_CACHE,
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--run-archive-dir",
        type=Path,
        default=DEFAULT_RUN_ARCHIVE,
    )
    parser.add_argument("--from", dest="from_date")
    parser.add_argument("--to", dest="to_date")
    parser.add_argument("--minimum-trades", type=int, default=200)
    parser.add_argument("--include-trades", action="store_true")
    parser.add_argument(
        "--provider-latency-ms",
        action="append",
        type=int,
        dest="provider_latency_scenarios_ms",
        help="Repeat to preserve an ordered virtual-entry latency scenario",
    )
    parser.add_argument(
        "--provider-volume-per-leg",
        type=float,
        default=0.01,
    )
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--progress", action="store_true")
    args = parser.parse_args(argv)

    default_replay_selected = (
        args.replay.resolve() == DEFAULT_REPLAY.resolve()
    )
    replay_source_dir = (
        DATA_DIR if default_replay_selected else args.replay.parent
    )
    replay_manifest = (
        args.replay_manifest
        or replay_source_contract.default_manifest_path(args.replay)
    )
    ledger_source = (
        args.ledger_source or replay_source_dir / "ledger.jsonl"
    )
    events_source = (
        args.events_source or replay_source_dir / "trade_events.jsonl"
    )
    money_contract = (
        args.money_contract
        or replay_source_dir / "broker_money_contract.json"
    )
    required_inputs = {
        "replay_trades": args.replay,
        "replay_source_manifest": replay_manifest,
        "replay_source_ledger": ledger_source,
        "replay_source_events": events_source,
        "observed_baseline": args.baseline,
        "provider_catalog": args.catalog,
        "broker_money_contract": money_contract,
    }
    args.output.unlink(missing_ok=True)
    missing = [
        role for role, path in required_inputs.items() if not path.is_file()
    ]
    if missing:
        print(
            f"Missing strategy-farm inputs: {', '.join(missing)}",
            file=sys.stderr,
        )
        return 1

    def replay_source_errors() -> list[str]:
        return replay_source_contract.validate_manifest(
            replay_path=args.replay,
            ledger_path=ledger_source,
            events_path=events_source,
            manifest_path=replay_manifest,
        )

    def reject_changed_replay_sources(stage: str) -> bool:
        errors = replay_source_errors()
        if not errors:
            return False
        print(
            f"Invalid replay source contract ({stage}): "
            + ", ".join(errors),
            file=sys.stderr,
        )
        return True

    if reject_changed_replay_sources("before_farm"):
        return 1

    source_files = _simulation_source_files()
    semantic_paths = _semantic_artifact_paths(
        input_files=required_inputs,
        source_files=source_files,
        market_tick_cache_dir=args.tick_cache_dir,
        conversion_tick_cache_dir=args.money_tick_cache_dir,
    )
    semantic_snapshot = _snapshot_semantic_artifacts(semantic_paths)

    def reject_changed_semantic_artifacts(stage: str) -> bool:
        changed = _changed_semantic_artifacts(semantic_snapshot)
        if not changed:
            return False
        labels = [
            role.split(":", 1)[1]
            if role.startswith(("input:", "code:"))
            else role
            for role in changed
        ]
        print(
            f"Semantic artifacts changed ({stage}): "
            + ", ".join(
                f"semantic_artifact_changed:{label}"
                for label in labels
            ),
            file=sys.stderr,
        )
        return True

    trades = strategy_simulator.load_jsonl(args.replay)
    baseline_rows = strategy_simulator.load_jsonl(args.baseline)
    catalog = _load_json(args.catalog)
    preflight = strategy_data_preflight(
        trades,
        catalog,
        tick_cache_dir=args.tick_cache_dir,
        money_tick_cache_dir=args.money_tick_cache_dir,
        from_date=args.from_date,
        to_date=args.to_date,
        provider_latency_scenarios_ms=args.provider_latency_scenarios_ms,
    )
    if not preflight["ok"]:
        print(
            "Strategy farm data preflight failed: "
            + ", ".join(preflight["errors"]),
            file=sys.stderr,
        )
        return 1
    progress_reporter = (
        pipeline_progress.ProgressReporter() if args.progress else None
    )

    def report_progress(current: int, total: int, label: str) -> None:
        if progress_reporter is None:
            return
        if total == 0 or current >= total:
            progress_reporter.complete(current, total, label)
        else:
            progress_reporter.update(current, total, label)

    execution = build_farm_execution(
        trades,
        baseline_rows,
        tick_cache_dir=args.tick_cache_dir,
        catalog=catalog,
        from_date=args.from_date,
        to_date=args.to_date,
        minimum_trades=args.minimum_trades,
        include_trades=args.include_trades,
        provider_latency_scenarios_ms=args.provider_latency_scenarios_ms,
        provider_volume_per_leg=args.provider_volume_per_leg,
        money_contract_path=money_contract,
        money_tick_cache_dir=args.money_tick_cache_dir,
        progress_callback=report_progress if args.progress else None,
    )
    unexpected_tick_days = sorted(
        set(execution.required_tick_days)
        - set(preflight["required_tick_days"])
    )
    if unexpected_tick_days:
        print(
            "Strategy farm required-day contract drift: "
            + ", ".join(
                f"unexpected_tick_day:{day}"
                for day in unexpected_tick_days
            ),
            file=sys.stderr,
        )
        return 1
    if reject_changed_replay_sources("after_farm"):
        return 1
    if reject_changed_semantic_artifacts("after_farm"):
        return 1
    provider_configuration = execution.report["provider_configuration"]
    evidence = simulation_run_provenance.build_run_evidence(
        repo_dir=Path(__file__).parent,
        report=execution.report,
        parameters={
            "from_date": args.from_date,
            "to_date": args.to_date,
            "minimum_trades": args.minimum_trades,
            "include_trades": args.include_trades,
            "tick_pad_minutes": 5,
            "provider_latency_scenarios_ms": provider_configuration[
                "latency_scenarios_ms"
            ],
            "provider_volume_per_leg": provider_configuration[
                "volume_per_leg"
            ],
        },
        selected_payloads=execution.selected_payloads,
        policies=execution.policies,
        input_files=required_inputs,
        source_files=source_files,
        required_tick_days=execution.required_tick_days,
        tick_contracts=execution.verified_tick_contracts,
        market_replay=execution.market_replay_summary,
    )
    if reject_changed_replay_sources("after_provenance"):
        return 1
    if reject_changed_semantic_artifacts("after_provenance"):
        return 1
    try:
        publication = simulation_run_provenance.publish_run_archive(
            report=execution.report,
            evidence=evidence,
            archive_root=args.run_archive_dir,
            output_path=args.output,
            include_trades=args.include_trades,
            repo_dir=Path(__file__).parent,
        )
    except simulation_run_provenance.ProvenanceConflictError as exc:
        args.output.unlink(missing_ok=True)
        print(f"Simulation provenance conflict: {exc}", file=sys.stderr)
        return 2

    report = publication.report
    output_report = (
        report
        if args.include_trades
        else compact_latest_report(report, publication)
    )
    write_report(output_report, args.output)
    if not args.quiet:
        print(f"Policies: {report['policy_count']}")
        print(f"Executed trades: {report['executed_trade_count']}")
        print(
            "Provider signals: "
            f"{report['canonical_scope']['provider_signals']}")
        print(
            "Selected policy: "
            f"{report['selection']['selected_policy'] or 'NONE'}")
        print(
            "Selection blockers: "
            f"{', '.join(report['selection']['global_blockers']) or 'none'}")
        print(f"Provenance: {publication.status}")
        print(f"Run fingerprint: {evidence['run_fingerprint']}")
        print(f"Output: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
