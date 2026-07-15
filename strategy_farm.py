"""Batch and score auditable management policies over one shared replay set."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from math import isfinite
from numbers import Integral, Real
from pathlib import Path
from typing import Iterable

import pandas as pd

import observed_tick_replay_validator
import provider_strategy_simulator
import provider_trade_spec
import simulation_run_provenance
import strategy_policies
import strategy_simulator


DATA_DIR = Path(__file__).parent / "data"
DEFAULT_REPLAY = DATA_DIR / "replay_trades.jsonl"
DEFAULT_BASELINE = DATA_DIR / "observed_tick_replay_audit.jsonl"
DEFAULT_CATALOG = DATA_DIR / "provider_signal_catalog.json"
DEFAULT_TICK_CACHE = DATA_DIR / "ticks_cache"
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


def calculate_policy_metrics(rows: Iterable[dict]) -> dict:
    rows = list(rows)
    usable = [row for row in rows if row.get("status") != "blocked"]
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
    mismatched = sum(status == "mismatch" for status in statuses)
    return {
        "selected_trades": len(statuses),
        "exact": exact,
        "blocked": len(statuses) - exact - mismatched,
        "mismatched": mismatched,
    }


def _require_current_causal_contract(baseline: dict | None) -> dict | None:
    if not isinstance(baseline, dict) or baseline.get("status") != "exact":
        return baseline
    blockers = []
    if baseline.get("validation_contract") != "causal_path_v2":
        blockers.append("causal_path_contract_unverified")
    if baseline.get("fill_price_authority") != "mt5_deals":
        blockers.append("fill_price_authority_unverified")
    if not blockers:
        return baseline
    return {
        **baseline,
        "status": "blocked",
        "blockers": list(dict.fromkeys(
            [*(baseline.get("blockers") or []), *blockers]
        )),
    }


def _market_replay_verified(summary: dict[str, int]) -> bool:
    selected = summary["selected_trades"]
    return (
        selected > 0
        and summary["exact"] == selected
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


def _formal_signal_for_spec(signal: dict) -> dict:
    if signal.get("record_type") == "formal_signal":
        return signal
    # Catalog v1 contained only formal signals and omitted this discriminator.
    if "record_type" not in signal:
        return {**signal, "record_type": "formal_signal"}
    return signal


def _provider_tick_window(
    spec: provider_trade_spec.ProviderTradeSpec,
) -> tuple[dict | None, list[str]]:
    trigger = spec.trigger_observed_utc
    if trigger is None:
        return None, []
    try:
        threshold = trigger + timedelta(milliseconds=spec.latency_ms)
        horizon = datetime.combine(
            threshold.date(),
            time(23, 59, 59, 999999),
            tzinfo=timezone.utc,
        )
    except (OverflowError, ValueError):
        return None, ["entry_threshold_out_of_range"]
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


def _build_provider_policy_results(
    provider_signals: list[dict],
    policies: list[strategy_policies.StrategyPolicy],
    tick_loader: observed_tick_replay_validator.ReplayTickFrameCache,
    *,
    latency_scenarios_ms: tuple[int, ...],
    volume_per_leg: float,
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
            tick_window, tick_blockers = _provider_tick_window(spec)
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
    unit_value, unit_source = strategy_simulator._global_unit_value(
        selected_trades, None)
    tick_loader = observed_tick_replay_validator.ReplayTickFrameCache(
        tick_cache_dir)
    rows_by_policy = {policy.policy_id: [] for policy in policies}
    effective_baselines: list[dict] = []

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
            baseline = _require_current_causal_contract(baseline)
        effective_baselines.append({
            "sig_id": str(trade.get("sig_id")),
            "baseline": baseline,
        })
        result_cache: dict = {}
        portfolio_cache: dict = {}
        for policy in policies:
            rows_by_policy[policy.policy_id].append(
                strategy_simulator.simulate_trade(
                    trade,
                    ticks,
                    strategy_name=policy.policy_id,
                    policy=policy,
                    result_cache=result_cache,
                    portfolio_cache=portfolio_cache,
                    baseline_audit=baseline,
                    provider_signal=provider_signal,
                    require_provider_timeline=True,
                    default_unit_value=unit_value,
                    default_unit_source=unit_source,
                    horizon_policy=policy.horizon_policy,
                )
            )

    scores = []
    for policy in policies:
        rows = rows_by_policy[policy.policy_id]
        scores.append(build_policy_score(
            policy,
            rows,
            include_trades=include_trades,
        ))

    executed_selection = select_strategy(
        scores,
        minimum_trades=minimum_trades,
        oos_validated=False,
    )
    canonical_scope = _canonical_scope(catalog, from_date, to_date)
    market_replay_summary = _market_replay_summary(effective_baselines)
    market_replay_verified = _market_replay_verified(market_replay_summary)
    if not market_replay_verified:
        if "market_replay_not_exact" not in executed_selection["global_blockers"]:
            executed_selection["global_blockers"].append(
                "market_replay_not_exact"
            )
        executed_selection["selected_policy"] = None
        executed_selection["exploratory_ranking"] = []

    provider_signals = _provider_signals_in_scope(
        catalog,
        from_date,
        to_date,
    )
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
    )
    diagnostic_blockers = ["broker_money_contract_unverified"]
    if not market_replay_verified:
        diagnostic_blockers.append("market_replay_not_exact")
    selection = {
        "minimum_trades": minimum_trades,
        "oos_validated": False,
        "selected_policy": None,
        "global_blockers": diagnostic_blockers,
        "policy_blockers": {},
        "exploratory_ranking": [],
        "ranking_excluded": {},
        "ranking_rule": None,
    }

    calibration = {
        "unit_value": round(unit_value, 8),
        "source": unit_source,
    }

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "from_date": from_date,
        "to_date": to_date,
        "executed_trade_count": len(selected_trades),
        "policy_count": len(policies),
        "includes_trade_details": include_trades,
        "calibration": calibration,
        "canonical_scope": canonical_scope,
        "provider_scope": provider_scope,
        "provider_configuration": {
            "latency_scenarios_ms": list(latency_scenarios_ms),
            "volume_per_leg": provider_volume_per_leg,
        },
        "market_replay": market_replay_summary,
        "validation": {
            "price_path_mode": "provider_first",
            "money_mode": "diagnostic_only",
            "market_replay_verified": market_replay_verified,
            "mode": "diagnostic_only",
        },
        "selection": selection,
        "policies": scores,
        "provider_policy_results": provider_policy_results,
        "executed_baseline_validation": {
            "executed_trade_count": len(selected_trades),
            "calibration": calibration,
            "market_replay": market_replay_summary,
            "selection": executed_selection,
            "policies": scores,
        },
    }
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
    ).report


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_report(report: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(simulation_run_provenance.pretty_json_bytes(report))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the auditable management strategy farm")
    parser.add_argument("--replay", type=Path, default=DEFAULT_REPLAY)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--tick-cache-dir", type=Path, default=DEFAULT_TICK_CACHE)
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
    args = parser.parse_args(argv)

    required_inputs = {
        "replay_trades": args.replay,
        "observed_baseline": args.baseline,
        "provider_catalog": args.catalog,
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

    trades = strategy_simulator.load_jsonl(args.replay)
    baseline_rows = strategy_simulator.load_jsonl(args.baseline)
    catalog = _load_json(args.catalog)
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
    )
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
        source_files={
            "strategy_farm": Path(__file__),
            "strategy_policies": Path(strategy_policies.__file__),
            "strategy_simulator": Path(strategy_simulator.__file__),
            "provider_trade_spec": Path(provider_trade_spec.__file__),
            "provider_strategy_simulator": Path(
                provider_strategy_simulator.__file__
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
        },
        required_tick_days=execution.required_tick_days,
        tick_contracts=execution.verified_tick_contracts,
        market_replay=execution.market_replay_summary,
    )
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
    write_report(report, args.output)
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
