"""Batch and score auditable management policies over one shared replay set."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import observed_tick_replay_validator
import strategy_policies
import strategy_simulator


DATA_DIR = Path(__file__).parent / "data"
DEFAULT_REPLAY = DATA_DIR / "replay_trades.jsonl"
DEFAULT_BASELINE = DATA_DIR / "observed_tick_replay_audit.jsonl"
DEFAULT_CATALOG = DATA_DIR / "provider_signal_catalog.json"
DEFAULT_TICK_CACHE = DATA_DIR / "ticks_cache"
DEFAULT_OUTPUT = DATA_DIR / "strategy_farm.json"
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


def _canonical_scope(
    catalog: dict | None,
    from_date: str | None,
    to_date: str | None,
) -> dict:
    signals = list((catalog or {}).get("signals") or [])
    selected = []
    for signal in signals:
        ts = signal.get("first_observed_utc") or signal.get("signal_ts_utc")
        day = str(ts or "")[:10]
        if not day:
            continue
        if from_date and day < from_date:
            continue
        if to_date and day > to_date:
            continue
        selected.append(signal)
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
) -> dict:
    policies = policies or strategy_policies.default_policy_catalog()
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

    selection = select_strategy(
        scores,
        minimum_trades=minimum_trades,
        oos_validated=False,
    )
    canonical_scope = _canonical_scope(catalog, from_date, to_date)
    if canonical_scope["unexecuted_signals"]:
        selection["global_blockers"].append(
            "canonical_signals_not_simulated:"
            f"{canonical_scope['unexecuted_signals']}"
        )
        selection["selected_policy"] = None
    if canonical_scope["incomplete_signals"]:
        selection["global_blockers"].append(
            "canonical_signals_incomplete:"
            f"{canonical_scope['incomplete_signals']}"
        )
        selection["selected_policy"] = None

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "from_date": from_date,
        "to_date": to_date,
        "executed_trade_count": len(selected_trades),
        "policy_count": len(policies),
        "includes_trade_details": include_trades,
        "calibration": {
            "unit_value": round(unit_value, 8),
            "source": unit_source,
        },
        "canonical_scope": canonical_scope,
        "selection": selection,
        "policies": scores,
    }


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_report(report: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the auditable management strategy farm")
    parser.add_argument("--replay", type=Path, default=DEFAULT_REPLAY)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--tick-cache-dir", type=Path, default=DEFAULT_TICK_CACHE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--from", dest="from_date")
    parser.add_argument("--to", dest="to_date")
    parser.add_argument("--minimum-trades", type=int, default=200)
    parser.add_argument("--include-trades", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    report = build_farm_report(
        strategy_simulator.load_jsonl(args.replay),
        strategy_simulator.load_jsonl(args.baseline),
        tick_cache_dir=args.tick_cache_dir,
        catalog=_load_json(args.catalog),
        from_date=args.from_date,
        to_date=args.to_date,
        minimum_trades=args.minimum_trades,
        include_trades=args.include_trades,
    )
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
        print(f"Output: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
