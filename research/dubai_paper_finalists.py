"""Independent-engine recheck of frozen paper finalists, with no order adapter."""

from collections import Counter
from decimal import Decimal
from pathlib import Path
import json
import time

from research import strategy_study as fixed
from research import dubai_paper_campaign as screen
from research.dubai_annual_dataset import AnnualInputs
from research.dubai_entry_probe import compare_results
from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.recursive import _save
from research.dubai_shared_lab import run_day


SCHEMA = "dubai_paper_finalists_v1"
BUDGET = {"max_finalists": 2, "max_evaluations": 16000, "max_quote_visits": 200_000_000, "max_wall_seconds": 7200}
OWN_SOURCES = ("research/dubai_paper_finalists.py", "tools/verify_dubai_paper_finalists.py")
_IMPORTED = {name: fixed._digest(fixed.ROOT / name) for name in OWN_SOURCES if (fixed.ROOT / name).exists()}


class FinalistBudget:
    def __init__(self):
        self.started, self.evaluations, self.quote_visits = time.monotonic(), 0, 0

    def check(self):
        if time.monotonic() - self.started >= BUDGET["max_wall_seconds"]:
            raise TimeoutError("finalist time budget exhausted")

    def charge(self, count):
        self.check()
        if self.evaluations + 1 > BUDGET["max_evaluations"] or self.quote_visits + count > BUDGET["max_quote_visits"]:
            raise ValueError("finalist evaluation or quote budget exhausted")
        self.evaluations += 1
        self.quote_visits += count


def compare_screen_cell(cell, screened_rows):
    wanted = [r for r in screened_rows if r["fingerprint"] == cell["strategy_fingerprint"]
              and r["profile"] == cell["execution_profile"] and r["scenario"] == cell["scenario"] and r["day"] == cell["day"]]
    lookup = {r["trigger_id"]: r for r in wanted}
    if len(wanted) != len(lookup) or {r["trigger_id"] for r in cell["rows"]} != set(lookup) or len(cell["rows"]) != len(wanted):
        raise ValueError("screen/replay signal denominator mismatch")
    if cell["status"] not in {"diagnostic_complete", "diagnostic_complete_no_admissions", "data_blocked"}:
        raise ValueError("screen replay engine or portfolio incident")
    for row in cell["rows"]:
        prior = lookup[row["trigger_id"]]
        if row["status"] != prior["status"] or row["reservation"] != prior["reservation"]:
            raise ValueError("screen/replay admission mismatch")
        if prior["result_digest"] is not None:
            status, reasons, mismatches = compare_results(row["engines"])
            if status != row["status"] or reasons or any(mismatches.values()):
                raise ValueError("three-engine screen replay discrepancy")
            if fixed._sha(row["engines"]["fast"]) != prior["result_digest"]:
                raise ValueError("screen/replay exact fast result differs")
        elif row["engines"]:
            raise ValueError("screen/replay unexpected execution")


def observation_manifest(candidate, screen_identity, finalist_identity):
    genome = StrategyGenome.from_dict(candidate["strategy"])
    if genome.validation_errors() or genome.fingerprint != candidate["fingerprint"]:
        raise ValueError("candidate fingerprint mismatch")
    return {"schema_version": "canal1_paper_observation_candidate_v1", "channel": "canal1",
        "candidate_id": "canal1_paper_" + genome.fingerprint[:12], "family": candidate["family"],
        "strategy": genome.to_dict(), "strategy_fingerprint": genome.fingerprint,
        "screen_identity_sha256": screen_identity, "finalist_identity_sha256": finalist_identity,
        "execution_mode": "observation_only", "broker_orders_allowed": False,
        "production_activation_allowed": False, "current_dubai_runtime_compatible": False,
        "fresh_forward_verified": False, "money_contract_verified": False,
        "account_currency_money_verified": False,
        "reservation": {"max_baskets": 2, "max_lots": .04, "max_configured_loss_usd": 40,
                        "horizon_seconds": 1200, "release": "strictly_after_horizon_not_after_future_exit"},
        "runtime_blockers": ["Current DubaiLivePolicy is a different schema1 provider-managed basket policy.",
            "No audited runtime adapter for this frozen schema2 own-rule strategy.",
            "Current account, exposure, pending entries and deployed version not verified by this offline study.",
            "Original Telegram receipt and historical initial edited messages are not reconstructed.",
            "Prospective signal/order/acknowledgement/deal/recovery evidence is still required."],
        "not_a_bot_configuration": True}


def _identity():
    own = {name: fixed._digest(fixed.ROOT / name) for name in OWN_SOURCES}
    if any(own.get(name) != sha for name, sha in _IMPORTED.items()):
        raise ValueError("loaded finalist implementation changed")
    return {"screen": screen._identity(), "finalist_sources": own}


def _summary(cells, candidates, protocol):
    statistics = {}
    for candidate in candidates:
        key, groups = candidate["fingerprint"], {}
        for scenario in screen.SCENARIOS:
            for profile in screen.PROFILE_NAMES:
                selected = [c for c in cells if c["strategy_fingerprint"] == key and c["scenario"] == scenario
                            and c["execution_profile"] == profile]
                portfolios = [c["portfolio"] for c in selected if c["portfolio"] is not None]
                groups[scenario + ":" + profile] = {"cells": len(selected),
                    "status_counts": dict(Counter(c["status"] for c in selected)),
                    "known_day_hypothetical_net_before_extra_cost_eur": str(sum((Decimal(p["net_eur"]) for p in portfolios), Decimal("0.00"))),
                    "max_known_intraday_drawdown_eur": str(max((Decimal(p["max_drawdown_eur"]) for p in portfolios), default=Decimal("0.00"))),
                    "max_concurrent_signals": max((p["max_concurrent_signals"] for p in portfolios), default=0),
                    "max_concurrent_volume": max((p["max_concurrent_volume"] for p in portfolios), default=0)}
        statistics[key] = groups
    return {"schema_version": SCHEMA, "status": "paper_finalists_replayed_not_production_ready",
        "paper_candidates": [observation_manifest(c, protocol["screen_identity_sha256"], protocol["identity_sha256"]) for c in candidates],
        "cell_status_counts": dict(Counter(c["status"] for c in cells)), "statistics": statistics,
        "full_year_account_return": None, "selected_policy": None, "production_activation_allowed": False,
        "scope": "Exact three-engine and frozen-screen equality; complete-day hypothetical portfolios only."}


def run_finalists(screen_dir, stream_dir, coverage_dir, raw_audit_path, output_dir):
    budget = FinalistBudget()
    source, output = Path(screen_dir).resolve(), Path(output_dir).resolve()
    if output.exists():
        raise ValueError("immutable finalist output already exists")
    checked = screen.verify_campaign(source)
    screen_protocol, screen_state = fixed._read(source / "protocol.json"), fixed._read(source / "state.json")
    ids = checked["paper_finalists"]
    if not ids or len(ids) > BUDGET["max_finalists"]:
        raise ValueError("no qualified paper finalist or finalist budget exceeded")
    by_id = {c["fingerprint"]: c for c in screen_protocol["candidates"]}
    candidates = [by_id[key] for key in ids]
    inputs = AnnualInputs(stream_dir, coverage_dir, raw_audit_path)
    if any(output.is_relative_to(root.resolve()) or root.resolve().is_relative_to(output) for root in [source, *inputs.protected_dirs]):
        raise ValueError("finalist output overlaps protected inputs")
    if (inputs.triggers != screen_protocol["triggers"] or inputs.rolling != screen_protocol["rolling"]
            or inputs.reference["money"] != screen_protocol["money"] or inputs.watched != screen_protocol["sources"]):
        raise ValueError("finalist annual input identity differs from screening")
    sources = dict(inputs.watched)
    sources.update({str(source / name): sha for name, sha in screen_state["artifacts"].items()})
    sources[str(source / "state.json")] = fixed._digest(source / "state.json")
    if any(Path(path).is_relative_to(output) for path in sources):
        raise ValueError("finalist output contains a protected source")
    planned = len(inputs.triggers) * len(ids) * len(screen.PROFILE_NAMES) * 3
    quotes = sum(inputs.coverage[(r["trigger_id"], 1200)]["market_quotes_in_horizon"] for r in inputs.triggers) * len(ids) * len(screen.PROFILE_NAMES) * 3
    if planned > BUDGET["max_evaluations"] or quotes > BUDGET["max_quote_visits"]:
        raise ValueError("finalist full matrix exceeds budget")
    protocol = {"schema_version": SCHEMA, "implementation": _identity(), "sources": sources,
        "screen_directory": str(source), "screen_identity_sha256": checked["identity_sha256"],
        "candidates": candidates, "profiles": screen_protocol["profiles"], "days": screen_protocol["days"],
        "budget": BUDGET, "planned_evaluations": planned, "planned_quote_visits": quotes,
        "production_activation_allowed": False}
    protocol["identity_sha256"] = fixed._sha(protocol)
    fixed._verify_sources(sources)
    output.mkdir(parents=True, exist_ok=False)
    _save(output / "protocol.json", protocol)
    artifacts = {"protocol.json": fixed._digest(output / "protocol.json")}
    cells = []
    for day_name, scenario in protocol["days"]:
        budget.check()
        day = inputs.load_day(scenario, day_name, 1200)
        name = f"day_{scenario}_{day_name}.json"
        screened = fixed._read(source / name)["rows"]
        current, errors = [], []
        for candidate in candidates:
            for profile_name, profile in protocol["profiles"].items():
                cell = run_day(day, candidate, profile_name, profile, budget)
                try:
                    compare_screen_cell(cell, screened)
                except ValueError as exc:
                    cell["screen_replay_incident"] = str(exc)
                    errors.append(str(exc))
                current.append(cell)
        _save(output / name, {"cells": current, "errors": errors})
        artifacts[name] = fixed._digest(output / name)
        cells.extend(current)
        print(json.dumps({"event": "paper_finalist_day_checked", "day": day_name, "scenario": scenario,
                          "evaluations": budget.evaluations, "errors": errors}), flush=True)
        if errors:
            raise ValueError("finalist incident preserved; no nomination or activation")
    inputs.verify_sources()
    fixed._verify_sources(sources)
    report = _summary(cells, candidates, protocol)
    _save(output / "finalists.json", report)
    artifacts["finalists.json"] = fixed._digest(output / "finalists.json")
    manifest = {"schema_version": SCHEMA, "identity_sha256": protocol["identity_sha256"], "artifacts": artifacts,
                "evaluations": budget.evaluations, "quote_visits": budget.quote_visits}
    _save(output / "manifest.json", manifest)
    verify_finalists(output)
    return report


def verify_finalists(output_dir):
    output = Path(output_dir).resolve()
    protocol, manifest = fixed._read(output / "protocol.json"), fixed._read(output / "manifest.json")
    if protocol["implementation"] != _identity() or protocol["budget"] != BUDGET:
        raise ValueError("finalist implementation or budget mismatch")
    if protocol["identity_sha256"] != fixed._sha({k: v for k, v in protocol.items() if k != "identity_sha256"}):
        raise ValueError("finalist protocol identity mismatch")
    if manifest["identity_sha256"] != protocol["identity_sha256"] or protocol["production_activation_allowed"] is not False:
        raise ValueError("finalist safety or identity mismatch")
    fixed._verify_sources(protocol["sources"])
    screen_checked = screen.verify_campaign(protocol["screen_directory"])
    if screen_checked["paper_finalists"] != [c["fingerprint"] for c in protocol["candidates"]]:
        raise ValueError("finalist nomination changed")
    expected = {"protocol.json", "finalists.json", *(f"day_{scenario}_{day}.json" for day, scenario in protocol["days"])}
    if set(manifest["artifacts"]) != expected:
        raise ValueError("finalist day denominator incomplete")
    for name, sha in manifest["artifacts"].items():
        path = output / name
        if path.is_symlink() or fixed._digest(path) != sha:
            raise ValueError("finalist artifact hash mismatch")
    cells, evaluated = [], 0
    for day, scenario in protocol["days"]:
        name = f"day_{scenario}_{day}.json"
        payload = fixed._read(output / name)
        if payload["errors"]:
            raise ValueError("unresolved finalist incident")
        current = payload["cells"]
        keys = [(c["strategy_fingerprint"], c["execution_profile"]) for c in current]
        wanted = {(c["fingerprint"], name) for c in protocol["candidates"] for name in screen.PROFILE_NAMES}
        if len(keys) != len(wanted) or set(keys) != wanted or any(c["day"] != day or c["scenario"] != scenario for c in current):
            raise ValueError("finalist cell denominator mismatch")
        screened = fixed._read(Path(protocol["screen_directory"]) / name)["rows"]
        for cell in current:
            compare_screen_cell(cell, screened)
            evaluated += sum(len(row["engines"]) for row in cell["rows"])
        cells.extend(current)
    if evaluated != manifest["evaluations"] or evaluated > BUDGET["max_evaluations"] or manifest["quote_visits"] > BUDGET["max_quote_visits"]:
        raise ValueError("finalist evaluation accounting mismatch")
    report = _summary(cells, protocol["candidates"], protocol)
    if fixed._encode(report) != fixed._encode(fixed._read(output / "finalists.json")):
        raise ValueError("finalist summary mismatch")
    return {"status": "verified_current_paper_finalists_only", "identity_sha256": protocol["identity_sha256"],
            "evaluations": evaluated, "paper_candidates": [c["candidate_id"] for c in report["paper_candidates"]],
            "production_activation_allowed": False}
