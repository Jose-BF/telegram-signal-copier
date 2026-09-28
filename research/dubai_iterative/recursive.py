"""Staged development-only campaigns over the existing finite search engine."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import time

from research.iterative_provenance import implementation_identity
from .contracts import SearchBudget
from .robustness import simulation_behavior_digest
from .search import _callable_identity, _evaluator_configuration, _replace_checkpoint, run_search


@dataclass(frozen=True)
class CampaignPolicy:
    initial_per_family: int = 500
    step_per_family: int = 500
    max_evaluations: int = 10_000
    max_wall_seconds: int = 13_500
    population_size: int = 50
    minimum_gain: str = "1.00"
    relative_gain: str = "0.02"
    risk_tolerance: str = "0.05"
    extra_cost_per_lot: str = "10.00"
    minimum_participation: float = .25
    maximum_concentration: float = .60

    def __post_init__(self):
        for key in ("initial_per_family", "step_per_family", "max_evaluations", "max_wall_seconds", "population_size"):
            if type(getattr(self, key)) is not int or getattr(self, key) <= 0:
                raise ValueError(f"{key} must be a positive integer")
        if self.initial_per_family % self.population_size or self.step_per_family % self.population_size:
            raise ValueError("stage budgets must contain complete populations")
        if self.max_evaluations < self.initial_per_family:
            raise ValueError("initial stage exceeds campaign budget")
        for key in ("minimum_gain", "relative_gain", "risk_tolerance", "extra_cost_per_lot"):
            value = Decimal(getattr(self, key))
            if not value.is_finite() or value < 0:
                raise ValueError(f"{key} must be finite and nonnegative")
        for key in ("minimum_participation", "maximum_concentration"):
            value = getattr(self, key)
            if type(value) not in (int, float) or not 0 < value <= 1:
                raise ValueError(f"{key} must be in (0, 1]")


@dataclass(frozen=True)
class DevelopmentDataset:
    paths: tuple
    source_hashes: dict


def development_metrics(evaluation, eligible_ids, blocks, *, policy=CampaignPolicy()):
    rows = evaluation.results
    observed = [result.signal_id for _, result in rows]
    complete = (len(observed) == len(set(observed)) and set(observed) == set(eligible_ids)
                and bool(observed) and not evaluation.blockers and evaluation.net_eur is not None
                and evaluation.max_drawdown_eur is not None
                and all(result.pnl_eur is not None for _, result in rows))
    daily = defaultdict(lambda: Decimal("0"))
    for day, result in rows:
        if result.pnl_eur is not None:
            daily[day] += result.pnl_eur - Decimal(str(result.filled_volume)) * Decimal(policy.extra_cost_per_lot)
    return {
        "fingerprint": evaluation.genome.fingerprint, "complete": complete,
        "adjusted_net": str(sum(daily.values(), Decimal("0"))) if complete else None,
        "drawdown": str(evaluation.max_drawdown_eur) if complete else None,
        "worst_day": str(min(daily.values())) if complete else None,
        "blocks": [str(sum((daily[day] for day in block), Decimal("0"))) for block in blocks] if complete else [],
        "participation": evaluation.participation_rate,
        "concentration": evaluation.positive_day_concentration,
        "blockers": list(evaluation.blockers),
        "evaluated_signals": len(rows), "eligible_signals": len(eligible_ids),
        "behavior": hashlib.sha256("|".join(simulation_behavior_digest(result) for _, result in rows).encode()).hexdigest(),
    }


def _eligible(row, policy):
    if not row["complete"] or len(row["blocks"]) != 3:
        return False
    return (Decimal(row["adjusted_net"]) > 0
            and sum(Decimal(v) > 0 for v in row["blocks"]) >= 2
            and row["participation"] >= policy.minimum_participation
            and row["concentration"] is not None
            and row["concentration"] <= policy.maximum_concentration)


def _rank(row):
    return (-Decimal(row["adjusted_net"]) / max(Decimal("1"), Decimal(row["drawdown"])),
            -Decimal(row["adjusted_net"]), row["fingerprint"])


def extension_decision(previous, current, policy):
    before = sorted((row for row in previous if _eligible(row, policy)), key=_rank)
    after = sorted((row for row in current if _eligible(row, policy)), key=_rank)
    if not after:
        return {"extend": False, "reason": "no_stable_positive_development_candidate"}
    if not before:
        # A single new winner is insufficient evidence to authorize another round.
        behaviors = {r.get("behavior", r["fingerprint"]) for r in after[:3]}
        return {"extend": len(behaviors) >= 3, "reason": "new_stable_cluster" if len(behaviors) >= 3 else "isolated_gain"}
    parent = before[0]
    gain = max(Decimal(policy.minimum_gain), abs(Decimal(parent["adjusted_net"])) * Decimal(policy.relative_gain))
    tolerance = Decimal(policy.risk_tolerance)
    for child in after:
        differences = [Decimal(b) - Decimal(a) for a, b in zip(parent["blocks"], child["blocks"])]
        if (Decimal(child["adjusted_net"]) - Decimal(parent["adjusted_net"]) >= gain
                and Decimal(child["drawdown"]) <= Decimal(parent["drawdown"]) * (1 + tolerance)
                and Decimal(child["worst_day"]) >= Decimal(parent["worst_day"]) - max(Decimal(".10"), abs(Decimal(parent["worst_day"])) * tolerance)
                and sum(v >= 0 for v in differences) >= 2 and sum(v > 0 for v in differences) >= 2):
            return {"extend": True, "reason": "material_multi_block_gain", "before": parent["fingerprint"], "after": child["fingerprint"]}
    return {"extend": False, "reason": "no_material_stable_gain"}


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _json(value):
    return json.loads(json.dumps(value, sort_keys=True, default=str, allow_nan=False))


def _save(path, value, *, replace=False):
    data = json.dumps(_json(value), sort_keys=True, indent=2, allow_nan=False) + "\n"
    if replace:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(data, encoding="ascii")
        _replace_checkpoint(temporary, path)
    else:
        with path.open("x", encoding="ascii") as stream:
            stream.write(data)


def run_recursive_campaign(dataset, *, fold, eligible_signal_days, families, search_space,
                           evaluator, output_dir, policy=CampaignPolicy(), seed=20260913,
                           experiment_context=None, resume=False, clock=time.monotonic,
                           progress_callback=None, max_stages=None):
    """Round-robin family stages; challenge paths never reach search or decisions.

    Clean stage boundaries are resumable. Interrupted, uncommitted stages fail
    closed instead of guessing which candidate receipts belong to a checkpoint.
    The time limit is checked between generations, as in the existing engine.
    """
    paths = tuple(p for p in dataset.paths if fold.development_contains(str(p.day)))
    eligible = {key: day for key, day in eligible_signal_days.items() if fold.development_contains(day)}
    observed = [p.signal_id for p in paths]
    if not paths or len(observed) != len(set(observed)) or not set(observed) <= eligible.keys():
        raise ValueError("development denominator does not cover every unique path")
    if any(str(p.day) != eligible[p.signal_id] for p in paths):
        raise ValueError("development denominator day mismatch")
    days = sorted(set(eligible.values()))
    if len(days) < 3:
        raise ValueError("three development days are required")
    blocks = tuple(tuple(days[i * len(days) // 3:(i + 1) * len(days) // 3]) for i in range(3))
    if not families or len(families) * policy.initial_per_family > policy.max_evaluations:
        raise ValueError("initial families exceed campaign budget")
    domains, family_domains = set(), {}
    for name, plan in families.items():
        if not name.replace("_", "").isalnum():
            raise ValueError("invalid family name")
        fingerprints = {g.fingerprint for g in plan.variants}
        if domains & fingerprints:
            raise ValueError("family domains overlap")
        domains.update(fingerprints)
        family_domains[name] = fingerprints
        for genome in plan.variants:
            if genome.validation_errors() or search_space.validation_errors(genome):
                raise ValueError("invalid family domain")
        if not {g.fingerprint for g in plan.seeds} <= fingerprints:
            raise ValueError("family seeds outside domain")
    development = DevelopmentDataset(paths, dict(dataset.source_hashes))
    identity = _json({"schema_version": 1, "implementation": implementation_identity(),
        "policy": asdict(policy), "fold": asdict(fold), "blocks": blocks,
        "eligible_signal_days": eligible, "loaded_ids": observed, "sources": dataset.source_hashes,
        "families": {name: plan.identity() for name, plan in families.items()}, "family_order": list(families),
        "search_space": asdict(search_space), "seed": seed, "context": experiment_context,
        "evaluator": _callable_identity(evaluator), "execution": _evaluator_configuration(evaluator),
        "scout_fraction": "approximately_one_third_existing_engine", "selected_policy": None})
    output = Path(output_dir)
    state_path, protocol_path = output / "state.json", output / "protocol.json"
    if resume:
        if json.loads(protocol_path.read_text()) != identity:
            raise ValueError("campaign identity changed")
        state = json.loads(state_path.read_text())
        if state["active_family"] is not None:
            raise ValueError("interrupted stage requires explicit receipt/checkpoint reconciliation")
        for name, digest in state["artifacts"].items():
            artifact = (output / name).resolve()
            if not artifact.is_relative_to(output.resolve()) or not artifact.is_file() or _sha(artifact) != digest:
                raise ValueError(f"campaign artifact changed: {name}")
        counted = 0
        for name, family in state["families"].items():
            rows = []
            for path in sorted((output / name).glob("generation_*.json")):
                if str(path.relative_to(output)) not in state["artifacts"]:
                    raise ValueError("unbound campaign artifact")
                rows.extend(json.loads(path.read_text())["candidates"])
            if rows != family["snapshot"] or len(rows) != family["evaluations"]:
                raise ValueError("campaign artifact count differs from state")
            if rows:
                checkpoint = json.loads((output / name / "checkpoint.json").read_text())
                if checkpoint["evaluations"] != len(rows):
                    raise ValueError("campaign artifact count differs from checkpoint")
            counted += len(rows)
        if counted != state["total_evaluations"]:
            raise ValueError("campaign artifact count differs from total")
        if state["status"] == "complete":
            return state
    else:
        output.mkdir(parents=True, exist_ok=False)
        _save(protocol_path, identity)
        state = {"status": "running", "active_family": None, "total_evaluations": 0,
            "elapsed_seconds": 0., "selected_policy": None, "artifacts": {}, "stages": [], "next_family": 0,
            "families": {name: {"evaluations": 0, "extend": True, "stop_reason": None,
                                "snapshot": [], "elapsed_seconds": 0.} for name in families}}
        _save(state_path, state)
    started, carried = clock(), state["elapsed_seconds"]
    completed_stages = 0
    while any(f["extend"] for f in state["families"].values()):
        order = list(enumerate(families.items()))
        cursor = state["next_family"]
        for family_index, (name, plan) in order[cursor:] + order[:cursor]:
            family = state["families"][name]
            if not family["extend"]:
                continue
            elapsed = carried + max(0., clock() - started)
            remaining_seconds = int(policy.max_wall_seconds - elapsed)
            remaining = policy.max_evaluations - state["total_evaluations"]
            if remaining_seconds <= 0 or remaining < policy.population_size:
                state["status"] = "complete"
                state["stop_reason"] = "max_wall_seconds" if remaining_seconds <= 0 else "max_evaluations"
                break
            allowance = policy.initial_per_family if not family["evaluations"] else policy.step_per_family
            allowance = min(allowance, remaining - remaining % policy.population_size)
            target = family["evaluations"] + allowance
            directory = output / name
            directory.mkdir(exist_ok=True)
            prior_rows = []
            for path in sorted(directory.glob("generation_*.json")):
                prior_rows.extend(json.loads(path.read_text())["candidates"])
            stage_rows = []
            baseline = list(family["snapshot"])
            state["active_family"] = name
            _save(state_path, state, replace=True)

            def receive(current_fold, generation, evaluations):
                nonlocal baseline
                rows = [dict(development_metrics(item, eligible, blocks, policy=policy), genome=item.genome.to_dict())
                        for item in evaluations]
                if any(row["fingerprint"] not in family_domains[name] for row in rows):
                    raise ValueError("search escaped the declared family domain")
                stage_rows.extend(rows)
                path = directory / f"generation_{generation:05}.json"
                _save(path, {"fold": current_fold.name, "generation": generation, "candidates": rows})
                state["artifacts"][str(path.relative_to(output))] = _sha(path)
                if not family["evaluations"] and len(stage_rows) <= allowance // 2:
                    baseline = list(stage_rows)

            report = run_search(development, fold=fold, search_space=search_space, evaluator=evaluator,
                budget=SearchBudget(max_evaluations=target, max_generations=policy.max_evaluations,
                    max_wall_seconds=max(1, int(family["elapsed_seconds"]) + remaining_seconds),
                    patience_generations=policy.max_evaluations, max_lineage_depth=policy.max_evaluations),
                output_dir=directory, seed=seed + family_index, population_size=policy.population_size,
                resume_from=directory / "checkpoint.json" if family["evaluations"] else None,
                evaluation_callback=receive, progress_callback=progress_callback,
                experiment_context={"campaign_protocol_sha256": _sha(protocol_path)}, clock=clock,
                **plan.search_options())
            if report.challenge_evaluations:
                raise ValueError("challenge leakage in development stage")
            if report.evaluations - family["evaluations"] != len(stage_rows):
                raise ValueError("candidate receipts disagree with checkpoint count")
            all_rows = prior_rows + stage_rows
            if len({r["fingerprint"] for r in all_rows}) != len(all_rows):
                raise ValueError("candidate evaluated more than once")
            decision = extension_decision(baseline, all_rows, policy)
            if report.stop_reason != "max_evaluations" or report.evaluations >= len(plan.variants):
                decision = {"extend": False, "reason": report.stop_reason if report.evaluations < len(plan.variants) else "domain_exhausted"}
            state["total_evaluations"] += len(stage_rows)
            family.update(evaluations=report.evaluations, elapsed_seconds=report.elapsed_seconds,
                extend=decision["extend"], stop_reason=decision["reason"], snapshot=all_rows)
            state["stages"].append({"family": name, "evaluations": report.evaluations, "decision": decision})
            state["artifacts"][str(report.checkpoint_path.relative_to(output))] = _sha(report.checkpoint_path)
            state["active_family"] = None
            state["next_family"] = (family_index + 1) % len(families)
            state["elapsed_seconds"] = carried + max(0., clock() - started)
            if implementation_identity() != identity["implementation"]:
                raise ValueError("campaign implementation identity changed during execution")
            _save(state_path, state, replace=True)
            completed_stages += 1
            if max_stages is not None and completed_stages >= max_stages:
                state["status"] = "paused"
                _save(state_path, state, replace=True)
                return state
        if state["status"] == "complete":
            break
    state["status"] = "complete"
    state.setdefault("stop_reason", "family_stopping_rules")
    state["elapsed_seconds"] = carried + max(0., clock() - started)
    if state["elapsed_seconds"] >= policy.max_wall_seconds:
        state["stop_reason"] = "max_wall_seconds"
    _save(state_path, state, replace=True)
    return state
