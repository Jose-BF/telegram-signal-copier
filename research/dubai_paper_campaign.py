"""Bounded retrospective screening for paper observation, never live admission."""

from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from itertools import product
import json
import math
from pathlib import Path
import time

from research import strategy_study as fixed
from research.dubai_annual_dataset import AnnualInputs
from research.dubai_entry_probe import SCENARIOS
from research.dubai_family_catalog import fixed_controls
from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.recursive import _save
from research.dubai_shared_lab import capability_blockers, execution_profiles, reserve_schedule
from research.execution_profile import execution_from_mapping


SCHEMA = "dubai_paper_screen_v1"
PROFILE_NAMES = ("reference", "adverse_execution")
BUDGET = {"max_candidates": 128, "max_evaluations": 350000,
          "max_quote_visits": 5_000_000_000, "max_wall_seconds": 7200}
POLICY = {"extra_cost_eur_per_lot_round_trip": "10.00", "minimum_coverage": .8,
          "minimum_executed": {"revision_time": 80, "publication_initial": 20},
          "minimum_months": 6, "positive_month_fraction": 2 / 3,
          "maximum_positive_day_concentration": .35,
          "minimum_check_windows": {"revision_time": 8, "publication_initial": 5},
          "minimum_executed_per_check": 3, "positive_check_fraction": .6,
          "recent_days": 56, "minimum_recent_executed": {"revision_time": 10, "publication_initial": 3},
          "maximum_finalists": 2}
OWN_SOURCES = ("research/dubai_paper_campaign.py", "tools/run_dubai_paper_campaign.py")
_IMPORTED = {name: fixed._digest(fixed.ROOT / name) for name in OWN_SOURCES if (fixed.ROOT / name).exists()}


def candidate_grid(reference):
    controls = fixed_controls(reference)
    bases = {r["family"]: StrategyGenome.from_dict(r["strategy"]) for r in controls}
    candidates = {}

    def add(family, **changes):
        genome = bases[family].with_change(**changes)
        if genome.validation_errors():
            raise ValueError(f"invalid frozen candidate: {family}: {genome.validation_errors()}")
        candidates.setdefault(genome.fingerprint, {"family": family, "fingerprint": genome.fingerprint,
            "strategy": genome.to_dict(), "parameter_selection": "predeclared_paper_screen_grid"})

    for family in bases:
        add(family)
    for target, hold in product((3., 8., 12.), (5, 15)):
        add("market", target_steps=(target,), time_exit_min=hold)
    for seconds, target in product((5., 15., 60.), (5., 10.)):
        add("delay", entry_value=seconds, target_steps=(target,))
    for family in ("pullback", "momentum"):
        for distance, target in product((.5, 1., 3., 5.), (3., 5., 10.)):
            add(family, entry_value=distance, target_steps=(target,))
    for distance, confirmation, target in product((1., 3., 5.), (.5, 1.), (5., 10.)):
        add("adverse_reversal", entry_value=distance, entry_confirmation_value=confirmation, target_steps=(target,))
    for targets in ((2., 5.), (5., 10.), (5., 15.)):
        add("split_targets", target_steps=targets)
    for family in ("adverse_ladder", "favourable_ladder"):
        for distance, targets in product((1., 3., 5.), ((3., 8.), (5., 10.))):
            add(family, entry_ladder_step=distance, target_steps=targets)
    for trigger, target in product((1., 2., 5.), (5., 10.)):
        add("break_even", be_trigger=trigger, target_steps=(target,))
    for distance in (1., 2., 5.):
        add("trailing", trailing_distance=distance)
    for arm, giveback in ((2., 1.), (6., 1.), (6., 3.)):
        add("profit_lock", profit_lock_arm=arm, profit_lock_giveback=giveback)
    for target, runner in product((2., 4., 6.), (8., 12.)):
        add("partial_runner", target_value=target, runner_target=runner)
    add("partial_runner", be_mode="price", be_trigger=3.)
    for hold in (1, 10, 15):
        add("time_exit", time_exit_min=hold)
    for spread in (.3, .9):
        add("spread_filter", context_filter_value=spread)
    for hour in (10., 15., 18.):
        add("utc_cutoff", context_filter_value=hour)
    if len(candidates) > BUDGET["max_candidates"]:
        raise ValueError("candidate budget exceeded")
    return list(candidates.values())


def _decimal(value):
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("missing or invalid money") from exc
    if not result.is_finite():
        raise ValueError("nonfinite money")
    return result


def summarize_rows(rows):
    ids = [r["trigger_id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate signal in candidate statistics")
    daily, monthly = defaultdict(lambda: Decimal(0)), defaultdict(lambda: Decimal(0))
    counts = Counter(r["status"] for r in rows)
    allowed = {"simulated", "unfilled", "exposure_not_admitted", "day_data_blocked"}
    engine_complete = not (set(counts) - allowed)
    executed, usable = 0, 0
    for row in rows:
        if row["status"] not in {"simulated", "unfilled", "exposure_not_admitted"}:
            continue
        net, volume = _decimal(row["pnl_eur"]), _decimal(row["filled_volume"])
        if volume < 0 or (row["status"] != "simulated" and (net or volume)):
            raise ValueError("nontrading row contains execution or money")
        usable += 1
        executed += volume > 0
        adjusted = net - volume * Decimal(POLICY["extra_cost_eur_per_lot_round_trip"])
        daily[row["day"]] += adjusted
        monthly[row["day"][:7]] += adjusted
    balance = peak = drawdown = Decimal(0)
    for day in sorted(daily):
        balance += daily[day]
        peak = max(peak, balance)
        drawdown = max(drawdown, peak - balance)
    positives = [v for v in daily.values() if v > 0]
    return {"eligible": len(rows), "usable": usable, "executed": executed,
        "data_blocked": counts["day_data_blocked"], "status_counts": dict(counts),
        "engine_complete": engine_complete, "full_universe_complete": usable == len(rows) and bool(rows),
        "coverage_fraction": usable / len(rows) if rows else 0.,
        "net_after_cost_eur": str(balance.quantize(Decimal(".01"))) if usable and engine_complete else None,
        "closed_day_drawdown_eur": str(drawdown.quantize(Decimal(".01"))) if usable and engine_complete else None,
        "worst_known_day_eur": str(min(daily.values()).quantize(Decimal(".01"))) if daily and engine_complete else None,
        "positive_day_concentration": float(max(positives) / sum(positives)) if positives else None,
        "monthly": {k: str(v.quantize(Decimal(".01"))) for k, v in sorted(monthly.items())}}


def _positive(stats, minimum):
    return (stats["engine_complete"] and stats["coverage_fraction"] >= POLICY["minimum_coverage"]
            and stats["executed"] >= minimum and stats["net_after_cost_eur"] is not None
            and _decimal(stats["net_after_cost_eur"]) > 0)


def _rank(stats, fingerprint):
    ratios = [_decimal(s["net_after_cost_eur"]) / max(Decimal(1), _decimal(s["closed_day_drawdown_eur"])) for s in stats]
    return (-min(ratios), -min(_decimal(s["net_after_cost_eur"]) for s in stats), fingerprint)


def rolling_choices(groups, folds):
    candidates = sorted({k[0] for k in groups})
    reports = []
    for fold in folds:
        if not fold["complete_calendar_check"]:
            continue
        scenario, development, check = fold["scenario"], set(fold["development_entry_ids"]), set(fold["check_entry_ids"])
        if development & check:
            raise ValueError("development and check overlap")
        choices = []
        for fingerprint in candidates:
            stats = {name: summarize_rows([r for r in groups[(fingerprint, scenario, name)] if r["trigger_id"] in development])
                     for name in PROFILE_NAMES}
            if all(_positive(s, 20 if scenario == "revision_time" else 8) for s in stats.values()):
                choices.append((_rank(list(stats.values()), fingerprint), fingerprint, stats))
        winner = min(choices) if choices else None
        reports.append({"fold_id": fold["fold_id"], "scenario": scenario,
            "status": "retrospective_not_fresh_oos", "candidate": winner[1] if winner else None,
            "development": winner[2] if winner else None,
            "check": {name: summarize_rows([r for r in groups[(winner[1], scenario, name)] if r["trigger_id"] in check])
                      for name in PROFILE_NAMES} if winner else None})
    return reports


def screen_report(groups, folds, candidates, *, period_end=None):
    end = date.fromisoformat(period_end or max((r["day"] for rows in groups.values() for r in rows), default="2000-01-01"))
    recent_start = (end - timedelta(days=POLICY["recent_days"])).isoformat()
    evaluations, qualified = [], []
    for candidate in candidates:
        fingerprint, summary, reasons = candidate["fingerprint"], {}, []
        for scenario, profile in product(SCENARIOS, PROFILE_NAMES):
            rows = groups[(fingerprint, scenario, profile)]
            stats = summarize_rows(rows)
            recent = summarize_rows([r for r in rows if r["day"] >= recent_start])
            checks = []
            for fold in folds:
                if fold["scenario"] == scenario and fold["complete_calendar_check"]:
                    ids = set(fold["check_entry_ids"])
                    check = summarize_rows([r for r in rows if r["trigger_id"] in ids])
                    valid = (check["engine_complete"] and check["coverage_fraction"] >= POLICY["minimum_coverage"]
                             and check["executed"] >= POLICY["minimum_executed_per_check"])
                    checks.append({"fold_id": fold["fold_id"], "usable": valid, "statistics": check})
            valid_checks = [c for c in checks if c["usable"]]
            positive_checks = sum(_positive(c["statistics"], POLICY["minimum_executed_per_check"]) for c in valid_checks)
            key = scenario + ":" + profile
            summary[key] = {"statistics": stats, "recent": recent, "checks": checks,
                            "usable_checks": len(valid_checks), "positive_checks": positive_checks}
            if not _positive(stats, POLICY["minimum_executed"][scenario]):
                reasons.append(key + ":insufficient_positive_covered_sample")
            if (len(stats["monthly"]) < POLICY["minimum_months"]
                    or sum(_decimal(v) > 0 for v in stats["monthly"].values()) < math.ceil(len(stats["monthly"]) * POLICY["positive_month_fraction"])):
                reasons.append(key + ":monthly_instability")
            if stats["positive_day_concentration"] is None or stats["positive_day_concentration"] > POLICY["maximum_positive_day_concentration"]:
                reasons.append(key + ":positive_day_concentration")
            if not _positive(recent, POLICY["minimum_recent_executed"][scenario]):
                reasons.append(key + ":recent_window_not_positive")
            if (len(valid_checks) < POLICY["minimum_check_windows"][scenario]
                    or positive_checks < math.ceil(len(valid_checks) * POLICY["positive_check_fraction"])):
                reasons.append(key + ":rolling_instability_or_insufficient_checks")
        report = {"fingerprint": fingerprint, "family": candidate["family"], "groups": summary,
                  "paper_screen_pass": not reasons, "reasons": reasons}
        evaluations.append(report)
        if not reasons:
            qualified.append((_rank([s["statistics"] for s in summary.values()], fingerprint), fingerprint))
    return {"schema_version": SCHEMA, "status": "retrospective_screen_not_live_certification",
        "candidate_count": len(candidates), "candidate_reports": evaluations,
        "rolling_choices": rolling_choices(groups, folds),
        "paper_finalists": [key for _, key in sorted(qualified)[:POLICY["maximum_finalists"]]],
        "paper_candidate": None, "finalist_three_engine_verification_required": True,
        "selection": {"selected_policy": None, "promotion_eligible": False},
        "money_contract_verified": False, "account_currency_money_verified": False,
        "live_activation_allowed": False, "ready_for_massive_search": False,
        "annual_portfolio": None, "full_year_profit_claimed": False,
        "limits": ["Totals are hypothetical sums of covered-day results, not full-year account returns.",
            "Closed-day drawdown excludes within-day floating equity; finalists require portfolio reconstruction.",
            "All windows and nominated rules remain retrospective; no fresh OOS or guaranteed future fills.",
            "Two clocks and two execution profiles are sensitivities, not independent samples.",
            "Uncovered days are unknown, never zero; no capital, margin, stop-out or overnight certification."]}


def _identity():
    sources = {name: fixed._digest(fixed.ROOT / name) for name in OWN_SOURCES}
    if any(sources.get(name) != sha for name, sha in _IMPORTED.items()):
        raise ValueError("loaded screening code changed; restart with a new archive")
    return {"existing": fixed.current_identity(), "screen_sources": sources}


class ScreenBudget:
    def __init__(self, state):
        self.started, self.state = time.monotonic(), state
        self.carried = state["elapsed_seconds"]

    def check(self):
        self.state["elapsed_seconds"] = self.carried + time.monotonic() - self.started
        if self.state["elapsed_seconds"] >= BUDGET["max_wall_seconds"]:
            raise TimeoutError("screen time budget exhausted")

    def charge(self, count):
        self.check()
        if self.state["evaluations"] + 1 > BUDGET["max_evaluations"] or self.state["quote_visits"] + count > BUDGET["max_quote_visits"]:
            raise ValueError("screen evaluation or quote budget exhausted")
        self.state["evaluations"] += 1
        self.state["quote_visits"] += count


def screen_day(day, candidates, evaluators, budget):
    records = []
    paths = {p.signal_id: p for p in day.paths}
    for candidate in candidates:
        genome = StrategyGenome.from_dict(candidate["strategy"])
        reservations = {r["trigger_id"]: r for r in reserve_schedule(day.rows, genome, day.horizon_seconds)}
        for profile_name, fast in evaluators.items():
            for row in day.rows:
                reservation = reservations[row["trigger_id"]]
                record = {"fingerprint": genome.fingerprint, "family": candidate["family"],
                    "scenario": day.scenario, "profile": profile_name, "day": day.day,
                    "trigger_id": row["trigger_id"], "trigger_utc": row["trigger_utc"],
                    "reservation": reservation, "pnl_eur": None, "filled_volume": 0., "result_digest": None}
                if not day.complete:
                    record.update(status="day_data_blocked", reasons=sorted(set(row["reasons"] + ["complete_day_coverage_required"])))
                elif not reservation["admitted"]:
                    record.update(status="exposure_not_admitted", reasons=[reservation["reason"]], pnl_eur="0.00")
                else:
                    path = paths[row["trigger_id"]]
                    budget.charge(len(path.times_ns))
                    result = fast(path, genome)
                    reasons = list(result.blockers)
                    opened = sum((_decimal(e.volume) for e in result.entries), Decimal(0))
                    closed = sum((_decimal(e.volume) for e in result.exits), Decimal(0))
                    if opened != closed or opened > sum((_decimal(v) for v in genome.volume_weights), Decimal(0)):
                        reasons.append("position_outside_reserved_envelope")
                    start = fixed._utc_explicit(row["trigger_utc"])
                    if (any(e.opened_at < start for e in result.entries)
                            or any(e.closed_at > start + timedelta(seconds=day.horizon_seconds) for e in result.exits)):
                        reasons.append("position_time_outside_reserved_envelope")
                    if result.pnl_eur is None:
                        reasons.append("missing_simulated_money")
                    record.update(status="engine_incident" if reasons else "unfilled" if result.unfilled else "simulated",
                        reasons=sorted(set(reasons)), pnl_eur=str(result.pnl_eur) if result.pnl_eur is not None else None,
                        filled_volume=result.filled_volume, result_digest=fixed._sha(asdict(result)),
                        exit_reason=result.exit_reason, entries=len(result.entries), exits=len(result.exits))
                records.append(record)
    budget.check()
    return {"scenario": day.scenario, "day": day.day, "complete_data": day.complete, "rows": records}


def _groups(output, state):
    groups = defaultdict(list)
    for name in state["days"]:
        for row in fixed._read(output / name)["rows"]:
            groups[(row["fingerprint"], row["scenario"], row["profile"])].append(row)
    return groups


def _verify_files(output, state):
    for name, sha in state["artifacts"].items():
        path = (output / name).resolve()
        if not path.is_relative_to(output) or path.is_symlink() or fixed._digest(path) != sha:
            raise ValueError("screen artifact hash or path mismatch")


def run_campaign(stream_dir, coverage_dir, raw_audit_path, output_dir, *, resume=False):
    output = Path(output_dir).resolve()
    if output.exists() and not resume:
        raise ValueError("immutable campaign output already exists")
    inputs = AnnualInputs(stream_dir, coverage_dir, raw_audit_path)
    if any(output.is_relative_to(root.resolve()) or root.resolve().is_relative_to(output) for root in inputs.protected_dirs):
        raise ValueError("output overlaps protected sources")
    if any(Path(path).is_relative_to(output) for path in inputs.watched):
        raise ValueError("output contains protected source")
    candidates = candidate_grid(inputs.reference["strategy"])
    profiles = execution_profiles(inputs.reference["execution"], policy_extension="own_rule_be_partial_v1")
    for profile in profiles.values():
        profile["protection"]["request_quote_binding"] = "timestamp_and_ordinal"
    for candidate, profile in product(candidates, profiles.values()):
        blockers = capability_blockers(StrategyGenome.from_dict(candidate["strategy"]), profile)
        if blockers:
            raise ValueError(f"frozen grid capability blocked: {blockers}")
    days = sorted({(row["trigger_utc"][:10], row["scenario"]) for row in inputs.triggers})
    planned = len(inputs.triggers) * len(candidates) * len(profiles)
    visits = sum(inputs.coverage[(r["trigger_id"], 1200)]["market_quotes_in_horizon"] for r in inputs.triggers) * len(candidates) * len(profiles)
    if planned > BUDGET["max_evaluations"] or visits > BUDGET["max_quote_visits"]:
        raise ValueError("full frozen annual grid exceeds budget; no truncation")
    protocol = {"schema_version": SCHEMA, "implementation": _identity(), "sources": dict(inputs.watched),
        "candidates": candidates, "profiles": profiles, "policy": POLICY, "budget": BUDGET,
        "triggers": inputs.triggers, "folds": inputs.folds, "rolling": inputs.rolling,
        "inventory": inputs.inventory(), "days": [list(d) for d in days], "horizon_seconds": 1200,
        "planned_evaluations": planned, "planned_quote_visits": visits, "money": inputs.reference["money"],
        "channel": "canal1", "live_activation_allowed": False, "data_use": "retrospective_paper_screen_only"}
    protocol["identity_sha256"] = fixed._sha(protocol)
    if resume:
        if fixed._read(output / "protocol.json") != json.loads(fixed._encode(protocol)):
            raise ValueError("campaign resume identity mismatch")
        state = fixed._read(output / "state.json")
        _verify_files(output, state)
        if state["status"] == "complete":
            verify_campaign(output)
            return fixed._read(output / "screen.json")
        if state["status"] != "running":
            raise ValueError("blocked campaign cannot silently resume")
    else:
        output.mkdir(parents=True, exist_ok=False)
        _save(output / "protocol.json", protocol)
        state = {"status": "running", "evaluations": 0, "quote_visits": 0, "elapsed_seconds": 0.,
                 "days": [], "artifacts": {"protocol.json": fixed._digest(output / "protocol.json")}}
        _save(output / "state.json", state)
    budget = ScreenBudget(state)
    evaluators = {name: FastEvaluator(execution=execution_from_mapping(profile)) for name, profile in profiles.items()}
    print(json.dumps({"event": "paper_grid_frozen", "candidates": len(candidates), "days": len(days),
        "triggers": len(inputs.triggers), "planned_evaluations": planned, "planned_quote_visits": visits}), flush=True)
    for index, (day, scenario) in enumerate(days):
        name = f"day_{scenario}_{day}.json"
        if name in state["days"]:
            continue
        if (output / name).exists():
            raise ValueError("uncheckpointed day receipt requires reconciliation")
        budget.check()
        loaded = inputs.load_day(scenario, day, 1200)
        try:
            report = screen_day(loaded, candidates, evaluators, budget)
        finally:
            for evaluator in evaluators.values():
                evaluator.clear_cache()
        _save(output / name, report)
        state["days"].append(name)
        state["artifacts"][name] = fixed._digest(output / name)
        if any(row["status"] == "engine_incident" for row in report["rows"]):
            state["status"] = "blocked_engine_incident"
        _save(output / "state.json", state, replace=True)
        print(json.dumps({"event": "paper_day_checked", "day": day, "scenario": scenario,
            "days_done": len(state["days"]), "days_total": len(days), "complete_data": loaded.complete,
            "evaluations": state["evaluations"], "elapsed_seconds": round(state["elapsed_seconds"], 2)}), flush=True)
        if state["status"] != "running":
            raise ValueError("engine incident retained; screening stopped without ranking")
        if index % 20 == 0:
            inputs.verify_sources()
            if _identity() != protocol["implementation"]:
                raise ValueError("screening implementation changed")
    inputs.verify_sources()
    report = screen_report(_groups(output, state), inputs.folds, candidates, period_end=inputs.rolling["end_exclusive"][:10])
    _save(output / "screen.json", report)
    state["artifacts"]["screen.json"] = fixed._digest(output / "screen.json")
    budget.check()
    state["status"] = "complete"
    _save(output / "state.json", state, replace=True)
    verify_campaign(output)
    return report


def verify_campaign(output_dir):
    output = Path(output_dir).resolve()
    protocol, state = fixed._read(output / "protocol.json"), fixed._read(output / "state.json")
    if state["status"] != "complete" or protocol["schema_version"] != SCHEMA:
        raise ValueError("campaign incomplete or wrong schema")
    if protocol["implementation"] != _identity():
        raise ValueError("screening implementation identity mismatch")
    if protocol["identity_sha256"] != fixed._sha({k: v for k, v in protocol.items() if k != "identity_sha256"}):
        raise ValueError("screen protocol identity mismatch")
    if protocol["policy"] != POLICY or protocol["budget"] != BUDGET or protocol["live_activation_allowed"] is not False:
        raise ValueError("screening policy or safety contract changed")
    fixed._verify_sources(protocol["sources"])
    _verify_files(output, state)
    expected_days = [f"day_{scenario}_{day}.json" for day, scenario in protocol["days"]]
    if state["days"] != expected_days or set(state["artifacts"]) != {"protocol.json", "screen.json", *expected_days}:
        raise ValueError("screen day denominator incomplete")
    groups = _groups(output, state)
    expected = {(c["fingerprint"], scenario, profile) for c in protocol["candidates"] for scenario, profile in product(SCENARIOS, PROFILE_NAMES)}
    if set(groups) != expected:
        raise ValueError("screen candidate or profile denominator incomplete")
    for (_, scenario, _), rows in groups.items():
        expected_ids = {r["trigger_id"] for r in protocol["triggers"] if r["scenario"] == scenario}
        if len(rows) != len(expected_ids) or {r["trigger_id"] for r in rows} != expected_ids:
            raise ValueError("screen signal denominator incomplete")
    recomputed = screen_report(groups, protocol["folds"], protocol["candidates"], period_end=protocol["rolling"]["end_exclusive"][:10])
    if fixed._encode(recomputed) != fixed._encode(fixed._read(output / "screen.json")):
        raise ValueError("screen summary differs from retained rows")
    count = sum(r["result_digest"] is not None for rows in groups.values() for r in rows)
    if count != state["evaluations"] or count > BUDGET["max_evaluations"] or state["quote_visits"] > BUDGET["max_quote_visits"]:
        raise ValueError("screen evaluation accounting mismatch")
    return {"status": "verified_paper_screen_only", "identity_sha256": protocol["identity_sha256"],
            "evaluations": count, "candidate_count": len(protocol["candidates"]),
            "paper_finalists": recomputed["paper_finalists"], "live_activation_allowed": False}
