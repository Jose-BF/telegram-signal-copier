from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import research.dubai_iterative.__main__ as dubai_cli
import research.gold_iterative.__main__ as gold_cli
from research.dubai_iterative.certification import certify_genome_worlds
from research.dubai_iterative.contracts import SearchBudget, SearchSpace
from research.dubai_iterative.engine import ExecutionAssumptions, simulate
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.oracle import oracle_simulate
from research.dubai_iterative.portfolio import PortfolioTape, reconstruct_portfolio
from research.dubai_iterative.search import ChronologicalFold, SearchCheckpointError, run_search
from research.execution_profile import execution_to_scenario
from tests.test_iterative_market import market
from tests.test_iterative_protection import BASE_NS, path, policy, profile
from tests.test_strategy_study import BASE, case, digest, write_json



def _oracle_fields(execution):
    """Engine-only fields (per-request latency, quote lag) must stay at defaults for the oracle."""
    data = asdict(execution)
    assert data.pop("latency") is None and data.pop("quote_view_lag_ms") == 0
    return data

def integrated_execution(**changes):
    return replace(
        ExecutionAssumptions(
            latency_ms=125,
            entry_fill_latency_ms=1_000,
            entry_slippage=0.02,
            exit_slippage=0.03,
            spread_addition=0.02,
            protection=profile(),
            market=market(),
        ),
        **changes,
    )


def validation_worlds(channel, execution, *, preserve_costs=False):
    if channel == "gold":
        return gold_cli._execution_validation_worlds(execution, preserve_costs=preserve_costs)
    return dubai_cli._certification_worlds(
        execution, execution_to_scenario(execution, "search_execution"),
        preserve_costs=preserve_costs,
    )


@pytest.mark.parametrize("channel", ["gold", "dubai"])
def test_six_worlds_keep_entry_fill_and_nested_execution_objects(channel):
    execution = integrated_execution()
    worlds = validation_worlds(channel, execution)
    assert len(worlds) == len({name for name, _, _ in worlds}) == 6
    for name, actual, oracle in worlds:
        assert actual.entry_fill_latency_ms == execution.entry_fill_latency_ms, name
        assert actual.protection is execution.protection, name
        assert actual.market is execution.market, name
        assert oracle.protection is execution.protection, name
        assert oracle.market is execution.market, name
        assert asdict(oracle) == {"name": name, **_oracle_fields(actual)}


@pytest.mark.parametrize("channel", ["gold", "dubai"])
def test_opt_in_latency_worlds_keep_costs_and_legacy_worlds_stay_unchanged(channel):
    execution = ExecutionAssumptions(entry_slippage=.07, exit_slippage=.09, spread_addition=.04)
    legacy = validation_worlds(channel, execution)
    explicit = validation_worlds(channel, execution, preserve_costs=True)
    for worlds, costs in ((legacy, (0., 0., 0.)), (explicit, (.07, .09, .04))):
        for name, item, scenario in worlds:
            if name.startswith("latency_"):
                assert (item.entry_slippage, item.exit_slippage, item.spread_addition) == costs
            assert asdict(scenario) == {"name": name, **_oracle_fields(item)}
            assert item.protection is item.market is None
            assert item.entry_fill_latency_ms == 0
    adverse = next(item for name, item, _ in explicit if name == "adverse_costs")
    assert (adverse.latency_ms, adverse.entry_slippage, adverse.exit_slippage, adverse.spread_addition) == (500, .1, .1, .1)


@pytest.mark.parametrize("channel", ["gold", "dubai"])
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_six_worlds_keep_installed_stop_during_entry_ack_and_certify_portfolio(channel, direction):
    execution = integrated_execution(market=market(entry_acknowledgement_delay_ms=10_000))
    quotes = [100.] * 5 + [69.] * 10
    if direction == "SELL":
        quotes = [200 - value for value in quotes]
    tape = path(quotes, direction=direction)
    strategy = policy(target_mode="none", target_steps=())
    worlds = validation_worlds(channel, execution, preserve_costs=True)
    portfolio_tape = PortfolioTape(
        tape.times_ns, np.rint(tape.bid * 100).astype(np.int64),
        np.rint(tape.ask * 100).astype(np.int64),
        np.full(len(quotes), 100_000, dtype=np.int64),
        np.full(len(quotes), 100_000, dtype=np.int64), tape.fx_valid,
        5_000, 5_000, "synthetic_control",
    )
    portfolio_executions = []

    def portfolio(paths, results, *, execution, portfolio_tape):
        portfolio_executions.append(execution)
        return reconstruct_portfolio(paths, results, execution=execution, portfolio_tape=portfolio_tape)

    report = certify_genome_worlds(
        (tape,), strategy, worlds=worlds,
        evaluator_factory=lambda item: FastEvaluator(execution=item),
        portfolio_tape=portfolio_tape, portfolio_reconstructor=portfolio,
    )
    assert report.status == "pass", [(item.name, item.blockers, item.certificate.mismatches) for item in report.worlds]
    assert report.certified_worlds == report.world_count == 6
    assert portfolio_executions == [execution for _, execution, _ in worlds]
    for world, (_, active, scenario) in zip(report.worlds, worlds):
        scalar = simulate(tape, strategy, execution=active)
        oracle = oracle_simulate(tape, strategy, execution=scenario)
        for result in (scalar, oracle, world.fast_results[0]):
            assert result.blockers == ()
            assert len(result.entries) == len(result.exits) == 1
            request, fill, ack = result.market_events
            assert (request.kind, fill.kind, ack.kind) == (
                "entry_requested", "entry_filled", "entry_acknowledged",
            )
            assert fill.timestamp_ns - request.timestamp_ns == 1_000_000_000
            assert ack.timestamp_ns - fill.timestamp_ns == 10_000_000_000
            assert result.entries[0].acknowledged_ns == ack.timestamp_ns
            assert result.exits[0].tick_index == 5
            assert result.exits[0].reason == "initial_sl"
            assert ack.timestamp_ns > BASE_NS + 5_000_000_000
            assert world.portfolio.net_eur == result.pnl_eur
        scalar_payload = asdict(scalar)
        assert scalar_payload.pop("behavior_digest") is None
        assert scalar_payload == asdict(oracle)


@pytest.mark.parametrize("channel", ["gold", "dubai"])
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_six_worlds_keep_installed_stop_while_own_rule_market_close_is_pending(channel, direction):
    execution = integrated_execution(
        market=market(entry_acknowledgement_delay_ms=1_000, close_processing_delay_ms=2_000),
        protection=profile(processing_delay_ms=10_000),
    )
    quotes = [100.] * 5 + [97., 69., 69., 69., 69.]
    if direction == "SELL":
        quotes = [200 - value for value in quotes]
    tape = path(quotes, direction=direction)
    strategy = policy(target_mode="none", target_steps=(), hard_stop_eur_per_leg=8.)
    for name, active, scenario in validation_worlds(channel, execution, preserve_costs=True):
        scalar = simulate(tape, strategy, execution=active)
        fast = FastEvaluator(execution=active)(tape, strategy)
        oracle = oracle_simulate(tape, strategy, execution=scenario)
        for result in (scalar, fast, oracle):
            assert result.blockers == (), (name, result.blockers)
            assert len(result.exits) == 1
            assert result.exits[0].tick_index == 6
            assert result.exits[0].reason == "initial_sl"
            assert [(row.kind, row.tick_index) for row in result.market_events[-3:]] == [
                ("close_requested", 5), ("close_rejected", 7), ("close_acknowledged", 8),
            ]
            assert result.market_events[-2].reason == "position_already_closed"
        assert fast.pnl_eur == scalar.pnl_eur == oracle.pnl_eur


def test_profile_stress_baseline_is_not_idealized_and_summary_keeps_all_profiles(monkeypatch):
    execution = integrated_execution(market=market(entry_acknowledgement_delay_ms=10_000))
    worlds = validation_worlds("dubai", execution, preserve_costs=True)
    tape = path([100.] * 5 + [69.] * 10)
    strategy = policy(target_mode="none", target_steps=())
    calls = []

    def observed_oracle(path, genome, *, execution):
        result = oracle_simulate(path, genome, execution=execution)
        calls.append((execution, result))
        return result

    monkeypatch.setattr(dubai_cli, "oracle_simulate", observed_oracle)
    report = dubai_cli._ProfileStresser(worlds)((tape,), strategy)
    assert [scenario for scenario, _ in calls] == [scenario for _, _, scenario in worlds]
    assert calls[0][1].entries[0].acknowledged_ns > BASE_NS + 5_000_000_000
    assert report.base_net_eur == Decimal("-124.80")
    assert report.promotion_eligible is False
    summary = dubai_cli._stress_summary(report)
    assert summary["base_world"] == asdict(worlds[0][2])
    assert len(summary["scenarios"]) == 5
    for row in summary["scenarios"]:
        assert row["entry_fill_latency_ms"] == 1_000
        assert row["market"] == asdict(execution.market)
        assert row["protection"] == asdict(execution.protection)


def test_profile_stress_keeps_missing_results_in_every_world_denominator():
    execution = integrated_execution()
    worlds = validation_worlds("dubai", execution, preserve_costs=True)
    incomplete = replace(path([100.] * 4), signal_id="incomplete")
    report = dubai_cli._ProfileStresser(worlds)(
        (path([100.] * 5 + [69.] * 10), incomplete), policy(target_mode="none", target_steps=()),
    )
    assert report.base_net_eur is None and report.base_blockers
    assert report.promotion_eligible is False
    for row in report.scenarios:
        assert len(row.results) == 2
        assert row.results[1].signal_id == "incomplete"
        assert row.net_eur is None and row.blockers


@pytest.mark.parametrize("change", [
    {"be_mode": "price", "be_trigger": 1.},
    {"target_mode": "fixed_basket", "target_value": 10., "target_steps": ()},
    {"provider_management_mode": "explicit_close_only"},
    {"context_filter_mode": "min_reward_risk", "context_filter_value": 2.},
    {"entry_mode": "actual_mt5"},
])
def test_profile_space_rejects_inputs_and_mutations_instead_of_filtering_them(change):
    space = dubai_cli._profile_search_space(SearchSpace(), integrated_execution())
    strategy = policy()
    assert space.validation_errors(strategy) == ()
    candidate = strategy.with_change(**change).with_lineage(
        parent_fingerprints=(strategy.fingerprint,), mutation_reason="controlled_mutation", lineage_depth=1,
    )
    with pytest.raises(ValueError, match="search domain blocked before evaluation") as error:
        space.validation_errors(candidate)
    assert candidate.fingerprint in str(error.value)
    assert "controlled_mutation" in str(error.value)
    assert "no seeds or mutations were dropped" in str(error.value)


def test_profile_space_accepts_client_side_basket_cap_with_market_profile():
    space = dubai_cli._profile_search_space(SearchSpace(), integrated_execution())
    assert space.validation_errors(policy().with_change(stop_mode="basket_money", stop_value=10.)) == ()


def fixed_seeds(search_space, *, seed):
    return ()


def fixed_scouts(search_space, *, seed, count):
    return ()


def fixed_neighborhood(parent, search_space):
    return ()


def fixed_mutator(*args, **kwargs):
    return ()


def fixed_control_dataset():
    template = gold_cli._tiny_dataset()
    paths = []
    for original in template.paths:
        tape = path([100.] * 8 + [101.] * 8)
        offset = int(original.signal_observed_at.timestamp() * 1_000_000_000) - BASE_NS
        paths.append(replace(
            tape, signal_id=original.signal_id, day=original.day,
            signal_observed_at=original.signal_observed_at, opened_at=original.opened_at,
            legs=tuple(replace(leg, opened_at=original.opened_at) for leg in tape.legs),
            times_ns=tape.times_ns + offset,
            market_evidence=({"synthetic_control": True},),
        ))
    return replace(template, paths=tuple(paths), source_hashes={"fixture": "fixed_profile_control_v1"})


def control_portfolio_tape(args, dataset):
    paths = sorted(dataset.paths, key=lambda item: item.day)
    times = np.concatenate([item.times_ns for item in paths])
    return PortfolioTape(
        times, np.rint(np.concatenate([item.bid for item in paths]) * 100).astype(np.int64),
        np.rint(np.concatenate([item.ask for item in paths]) * 100).astype(np.int64),
        np.full(len(times), 100_000, dtype=np.int64), np.full(len(times), 100_000, dtype=np.int64),
        np.full(len(times), True), 5_000, 5_000, "fixed_synthetic_control",
    )


def own_rules_config():
    return {"schema_version": 1,
            "seeds": [policy(stop_mode="fixed_move", stop_value=5., hard_stop_eur_per_leg=20.).to_dict()],
            "mutations": {"target_steps": [[.6], [.7]], "stop_value": [4.]}}


def test_own_rules_config_admits_seeds_mutations_and_shared_crossover_without_legacy(tmp_path):
    from research.dubai_iterative.evolution import crossover
    config = tmp_path / "rules.json"
    config.write_text(json.dumps(own_rules_config()), encoding="utf-8")
    space = dubai_cli._profile_search_space(SearchSpace(), integrated_execution())
    plan = dubai_cli._load_own_rules_plan(config, space, population_size=8)
    assert len(plan.seeds) == 1 and len(plan.variants) == 6
    children = plan.neighborhood(plan.seeds[0], space)
    assert len(children) == 3
    assert all(child.parent_fingerprints == (plan.seeds[0].fingerprint,) for child in children)
    assert all(child.mutation_reason.startswith("own_rules:") for child in children)
    assert plan.scouts(space, seed=7, count=6) == plan.scouts(space, seed=7, count=6)
    for candidate in (*plan.seeds, *plan.variants, *children):
        assert space.validation_errors(candidate) == ()
        assert candidate.provider_management_mode == "ignore"
        assert candidate.schema_version == 2
    known = {genome.fingerprint for genome in plan.variants}
    for left in plan.variants:
        for right in plan.variants:
            assert all(genome.fingerprint in known for genome in crossover(left, right, search_space=space, seed=7))
    assert plan.mutate(plan.seeds[0], None, search_space=space, seed=7)


@pytest.mark.parametrize("change, expected", [
    ({"be_mode": ["price"]}, "unsupported own-rules mutation"),
    ({"target_steps": [[.6, .7]]}, "target_step_count_mismatch"),
    ({"entry_expiry_min": [False]}, "must be an integer"),
    ({"trailing_distance": [True]}, "must be a finite number"),
    ({"trailing_distance": [20., 20.]}, "duplicate"),
    ({"time_exit_min": [240]}, "outside_loaded_path_horizon"),
])
def test_invalid_own_rules_mutation_domain_fails_before_any_evaluation(tmp_path, change, expected):
    payload = own_rules_config()
    payload["mutations"] = change
    config = tmp_path / "rules.json"
    config.write_text(json.dumps(payload), encoding="utf-8")
    space = dubai_cli._profile_search_space(SearchSpace(), integrated_execution())
    with pytest.raises(ValueError, match=expected):
        dubai_cli._load_own_rules_plan(config, space, population_size=8)


@pytest.mark.parametrize("field", ["entry_fill_latency_ms", "protection", "market"])
def test_checkpoint_resumes_same_profile_but_rejects_changed_execution_even_with_stale_context(tmp_path, field):
    execution = integrated_execution()
    strategy = policy(entry_mode="no_entry")
    dataset = SimpleNamespace(paths=(path([100., 100.]),), source_hashes={"fixture": "fixed-control"})
    kwargs = dict(
        dataset=dataset, fold=ChronologicalFold("fixed", "2026-09-08", "2026-09-08", "2026-09-09", "2026-09-09"),
        budget=SearchBudget(max_generations=1, max_evaluations=1),
        search_space=dubai_cli._profile_search_space(SearchSpace(), execution),
        output_dir=tmp_path, seed=7, population_size=1,
        evaluator=FastEvaluator(execution=execution),
        baseline_genome=strategy, initial_genomes=(strategy,),
        seed_population_factory=fixed_seeds, scout_population_factory=fixed_scouts,
        neighborhood_factory=fixed_neighborhood, mutator=fixed_mutator,
        experiment_context={"execution": asdict(execution), "execution_profile_scope": "own_rules_v1"},
    )
    first = run_search(**kwargs)
    assert first.evaluations == 1
    checkpoint = tmp_path / "checkpoint.json"
    stored = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert stored["experiment_context"]["evaluator_configuration"]["execution"] == json.loads(json.dumps(asdict(execution)))
    assert stored["search_space"] == asdict(SearchSpace())
    resumed = run_search(**kwargs, resume_from=checkpoint)
    assert resumed.evaluations == first.evaluations
    if field == "entry_fill_latency_ms":
        changed = replace(execution, entry_fill_latency_ms=999)
    elif field == "protection":
        changed = replace(execution, protection=replace(execution.protection, processing_delay_ms=999))
    else:
        changed = replace(execution, market=replace(execution.market, entry_acknowledgement_delay_ms=999))
    before = (checkpoint.read_bytes(), checkpoint.stat().st_mtime_ns)
    kwargs["evaluator"] = FastEvaluator(execution=changed)
    with pytest.raises(SearchCheckpointError, match="experiment context"):
        run_search(**kwargs, resume_from=checkpoint)
    assert (checkpoint.read_bytes(), checkpoint.stat().st_mtime_ns) == before


def test_gold_experiment_key_binds_every_execution_profile_component_and_scope(monkeypatch):
    monkeypatch.setattr(gold_cli, "implementation_identity", lambda: {"sha256": "fixed-implementation"})
    execution = integrated_execution()
    def key(item, **kwargs):
        return gold_cli._experiment_key({"fixture": "fixed"}, SearchSpace(), item, 7, **kwargs)
    original = key(execution)
    alternatives = (
        replace(execution, entry_fill_latency_ms=999),
        replace(execution, protection=replace(execution.protection, stops_level_points=21)),
        replace(execution, market=replace(execution.market, close_acknowledgement_delay_ms=999)),
    )
    assert all(key(item) != original for item in alternatives)
    assert key(execution, execution_profile_scope="own_rules_v1") != original


def three_day_study(case, tmp_path, channel, *, blocked=False):
    from research.telegram_export import CHATS, prepare_exports, write_admission

    config_path, _, config = case()
    chat = 3828356530 if channel == "gold" else 1642806869
    messages = [
        {"id": day + 1, "type": "message", "date_unixtime": str(int((BASE + timedelta(days=day)).timestamp())),
         "text": "BUY GOLD NOW"}
        for day in range(3 + int(blocked))
    ]
    messages.extend([
        {"id": 10, "type": "message", "date_unixtime": str(int(BASE.timestamp())), "text": "Market commentary"},
        {"id": 11, "type": "message", "date_unixtime": str(int(BASE.timestamp()) - 1), "text": "BUY GOLD NOW"},
    ])
    source = tmp_path / "three_days.json"
    write_json(source, {"id": chat, "messages": messages})
    admission = tmp_path / "three_day_admission"
    end = BASE + timedelta(days=4)
    write_admission(prepare_exports([source], start=BASE, end=end), admission)
    config["admission"] = {"path": str(admission), "sha256": digest(admission / "manifest.json")}
    config["cohort"] = {"chat_id": chat, "name": CHATS[chat][0]}
    config["period"]["end_exclusive_utc"] = end.isoformat()
    config["max_fx_age_ms"] = 5000
    for proofs in config["sources"].values():
        proof = proofs[0]
        original = pd.read_parquet(proof["path"])
        frames = []
        for day in range(3):
            frame = original.copy()
            frame["time_utc"] += pd.Timedelta(days=day)
            frame["source_time_msc"] += day * 86_400_000
            frames.append(frame)
        pd.concat(frames, ignore_index=True).to_parquet(proof["path"])
        proof["sha256"] = digest(proof["path"])
    write_json(config_path, config)
    profile_path, rules_path = tmp_path / "execution.json", tmp_path / "rules.json"
    write_json(profile_path, config["execution"])
    write_json(rules_path, {"schema_version": 1, "seeds": [config["strategy"]], "mutations": {}})
    output = tmp_path / "search_output"
    args = ["search"] if channel == "gold" else []
    args += ["--study-config", str(config_path), "--execution-profile", str(profile_path),
             "--own-rules-config", str(rules_path), "--max-hold-minutes", "2",
             "--output-root", str(output), "--max-generations", "1", "--max-evaluations", "1",
             "--population-size", "1", "--workers", "1", "--oracle-finalists", "1"]
    if channel == "gold":
        args += ["--bootstrap-samples", "16"]
    return config_path, config, output, args


@pytest.mark.parametrize("channel", ["gold", "dubai"])
@pytest.mark.parametrize("blocked", [False, True])
def test_m7_study_cli_uses_real_loader_january_folds_and_retains_all_identities(
    case, tmp_path, monkeypatch, capsys, channel, blocked,
):
    from research.strategy_study_dataset import StudyDatasetBundle, load_study_dataset

    cli = gold_cli if channel == "gold" else dubai_cli
    config_path, _, output, args = three_day_study(case, tmp_path, channel, blocked=blocked)
    expected = load_study_dataset(config_path)
    events = []
    original_verify = StudyDatasetBundle.verify_sources
    original_search, original_publish = cli.run_chronological_search, cli.publish_run

    def verify(bundle):
        original_verify(bundle)
        events.append("verify")

    def search(*args, **kwargs):
        assert events[-1] == "verify"
        events.append("search_enter")
        result = original_search(*args, **kwargs)
        events.append("search_exit")
        return result

    def publish(*args, **kwargs):
        assert events[-1] == "verify"
        events.append("publish")
        return original_publish(*args, **kwargs)

    monkeypatch.setattr(StudyDatasetBundle, "verify_sources", verify)
    monkeypatch.setattr(cli, "run_chronological_search", search)
    monkeypatch.setattr(cli, "publish_run", publish)

    def forbidden(*args, **kwargs):
        raise AssertionError("M7 must not read legacy/default research inputs")

    monkeypatch.setattr(dubai_cli, "VerifiedParquetTickSource", forbidden)
    monkeypatch.setattr(dubai_cli, "load_dubai_dataset", forbidden)
    monkeypatch.setattr(gold_cli, "VerifiedParquetTickSource", forbidden)
    monkeypatch.setattr(gold_cli, "load_gold_now_dataset", forbidden)
    monkeypatch.setattr(gold_cli, "load_gold_direct_dataset", forbidden)
    monkeypatch.setattr(gold_cli, "build_scorecard", forbidden)
    assert cli.main(args) == 0, capsys.readouterr().out
    assert events[events.index("search_exit") + 1] == "verify"
    assert events[-1] == "publish"
    run_dir, = [item for item in output.iterdir() if item.is_dir() and not item.name.startswith(".")]
    card = json.loads((run_dir / "run_card.json").read_text(encoding="utf-8"))
    assert card["historical_admission_interface"] == "connected_to_m7_diagnostic"
    assert card["study_input"]["identity"] == expected.identity
    assert card["study_input"]["inventory"] == expected.inventory
    assert card["eligible_signal_ids"] == list(expected.dataset.eligible_signal_ids)
    assert card["exclusions"] == {reason: list(ids) for reason, ids in expected.dataset.exclusions.items()}
    assert card["selection"]["ranking_allowed"] is False
    assert card["selection"]["selected_strategy_fingerprint"] is None
    assert card["selection"]["promotion_eligible"] is False
    assert card["account_currency_money_verified"] is False
    assert card["observed_accounting_available"] is False
    assert card["signal_coverage"]["complete"] is (not blocked)
    if channel == "gold":
        assert card["financial_totals"]["actual_mt5"]["amount"] is None
        assert card["financial_totals"]["actual_mt5"]["known_amount"] is None
        assert card["run_metadata"]["total_evaluations"] == 1
        assert card["complete_days"] == ["2026-01-05", "2026-01-06", "2026-01-07"]
    else:
        assert card["actual_pnl_eur"] is card["loaded_actual_pnl_eur"] is None
        assert card["total_evaluations"] == 1
        assert card["folds"][0]["development_from"] == "2026-01-05"
        assert card["folds"][0]["challenge_from"] == "2026-01-07"
    checkpoints = list((output / ".checkpoints").rglob("checkpoint.json"))
    assert len(checkpoints) == 1
    context = json.loads(checkpoints[0].read_text(encoding="utf-8"))["experiment_context"]
    assert context["study_input"] == card["study_input"]
    assert context["execution"] == json.loads(json.dumps(expected.config["execution"]))
    frontier = json.loads((run_dir / "frontier.json").read_text(encoding="utf-8"))
    assert all(row["status"] == "diagnostic_only" and "retrospective_rank" not in row for row in frontier)


@pytest.mark.parametrize("channel", ["gold", "dubai"])
@pytest.mark.parametrize("mismatch", ["profile", "seed", "horizon", "implicit_horizon", "missing_profile", "missing_rules", "cohort"])
def test_m7_contract_mismatch_fails_before_search(case, tmp_path, monkeypatch, capsys, channel, mismatch):
    cli = gold_cli if channel == "gold" else dubai_cli
    config_path, config, output, args = three_day_study(case, tmp_path, channel)
    if mismatch == "profile":
        config["execution"]["entry_fill_latency_ms"] = 7
        write_json(config_path, config)
    elif mismatch == "seed":
        config["strategy"]["target_steps"] = [2.]
        write_json(config_path, config)
    elif mismatch == "horizon":
        args[args.index("--max-hold-minutes") + 1] = "3"
    elif mismatch == "cohort":
        cli = dubai_cli if channel == "gold" else gold_cli
        args = args[1:] if channel == "gold" else ["search", *args]
        if channel == "gold":
            offset = args.index("--bootstrap-samples")
            del args[offset:offset + 2]
    else:
        option = {"implicit_horizon": "--max-hold-minutes", "missing_profile": "--execution-profile",
                  "missing_rules": "--own-rules-config"}[mismatch]
        offset = args.index(option)
        del args[offset:offset + 2]
    def forbidden(*args, **kwargs):
        raise AssertionError("Invalid study contracts must not enter search")
    monkeypatch.setattr(cli, "run_chronological_search", forbidden)
    assert cli.main(args) == 2
    assert "ERROR" in capsys.readouterr().out
    assert not output.exists()


@pytest.mark.parametrize("channel", ["gold", "dubai"])
@pytest.mark.parametrize("option,value", [
    ("--fixture", "tiny"), ("--from", "2026-07-27"), ("--to", "2026-08-14"),
    ("--replay-path", "runtime_data/replay_trades.jsonl"),
    ("--audit-path", "runtime_data/observed_tick_replay_audit.jsonl"),
    ("--money-contract", "runtime_data/broker_money_contract.json"),
    ("--market-tick-cache", "runtime_data/ticks_cache"),
    ("--conversion-tick-cache", "runtime_data/money_ticks_cache"),
])
def test_m7_rejects_explicit_legacy_inputs_even_equal_to_defaults(channel, option, value):
    cli = gold_cli if channel == "gold" else dubai_cli
    argv = ["search"] if channel == "gold" else []
    args = cli._parser().parse_args([
        *argv, "--study-config", "must-not-open.json", "--execution-profile", "must-not-open.json",
        "--own-rules-config", "must-not-open.json", "--max-hold-minutes", "2", option, value,
    ])
    with pytest.raises(ValueError, match="cannot be combined") as error:
        cli._execution_from_args(args)
    assert option in str(error.value)


@pytest.mark.parametrize("channel,option,value", [
    ("gold", "--signal-scope", "now"), ("gold", "--provider-catalog-path", "unused.json"),
    ("gold", "--provider-media-annotations", "unused.json"),
    ("gold", "--provider-media-evidence", "unused.jsonl"), ("gold", "--raw-events-path", "unused.jsonl"),
    ("dubai", "--parent-parquet", "unused.parquet"), ("dubai", "--parent-limit", "12"),
])
def test_m7_rejects_channel_specific_legacy_selectors(channel, option, value):
    test_m7_rejects_explicit_legacy_inputs_even_equal_to_defaults(channel, option, value)


@pytest.mark.parametrize("channel", ["gold", "dubai"])
@pytest.mark.parametrize("stage", ["load", "search", "artifacts"])
def test_m7_source_change_prevents_publication(case, tmp_path, monkeypatch, capsys, channel, stage):
    from research import strategy_study_dataset as bridge

    cli = gold_cli if channel == "gold" else dubai_cli
    config_path, _, output, args = three_day_study(case, tmp_path, channel)
    owner, function = {
        "load": (bridge, "load_study_dataset"),
        "search": (cli, "run_chronological_search"),
        "artifacts": (cli, "build_gold_research_artifacts" if channel == "gold" else "_build_artifacts"),
    }[stage]
    original = getattr(owner, function)
    def changed_source(*args, **kwargs):
        result = original(*args, **kwargs)
        config_path.write_bytes(config_path.read_bytes() + b"\n")
        return result
    monkeypatch.setattr(owner, function, changed_source)
    assert cli.main(args) == 2
    assert "changed" in capsys.readouterr().out
    assert not list(output.glob("*/run_card.json"))


@pytest.mark.parametrize("channel", ["gold", "dubai"])
@pytest.mark.parametrize("fx_interval_ms", [None, 60_000])
def test_m7_canonical_portfolio_uses_bundle_sources_and_explicit_fx_limits(
    case, tmp_path, monkeypatch, channel, fx_interval_ms,
):
    from research.strategy_study_dataset import load_study_dataset
    from research.dubai_iterative.contracts import StrategyGenome
    from research.execution_profile import execution_from_mapping

    config_path, config, _, _ = three_day_study(case, tmp_path, channel)
    if fx_interval_ms is not None:
        config["max_fx_interval_ms"] = fx_interval_ms
        write_json(config_path, config)
    bundle = load_study_dataset(config_path)
    build = dubai_cli.build_portfolio_tape
    calls = []
    def capture(paths, **kwargs):
        calls.append(kwargs)
        return build(paths, **kwargs)
    monkeypatch.setattr(dubai_cli, "build_portfolio_tape", capture)
    args = SimpleNamespace(_study_bundle=bundle)
    tape = dubai_cli._verified_portfolio_tape(args, bundle.dataset)
    assert calls == [{"market_tick_source": bundle.market_tick_source,
                      "conversion_tick_source": bundle.conversion_tick_source,
                      "max_conversion_age_ms": 5000, "max_conversion_interval_ms": fx_interval_ms or 5000}]
    assert not tape.blockers
    execution = execution_from_mapping(bundle.config["execution"])
    genome = StrategyGenome.from_dict(bundle.config["strategy"])
    results = [simulate(path, genome, execution=execution) for path in bundle.dataset.paths]
    portfolio = reconstruct_portfolio(bundle.dataset.paths, results, execution=execution, portfolio_tape=tape)
    assert portfolio.net_eur == Decimal("3.00")
    assert not portfolio.blockers
    bundle.verify_sources()


def test_m7_gold_resume_reuses_bound_input_and_rejects_changed_identity(case, tmp_path, capsys):
    config_path, config, output, args = three_day_study(case, tmp_path, "gold")
    assert gold_cli.main(args) == 0, capsys.readouterr().out
    archives = {path: path.read_bytes() for path in output.glob("*/*") if path.is_file()}
    assert gold_cli.main(["resume", *args[1:]]) == 0, capsys.readouterr().out
    assert all(path.read_bytes() == content for path, content in archives.items())
    config["period"]["end_exclusive_utc"] = (BASE + timedelta(days=3)).isoformat()
    write_json(config_path, config)
    assert gold_cli.main(["resume", *args[1:]]) == 2
    assert "no compatible Gold checkpoint" in capsys.readouterr().out
    assert all(path.read_bytes() == content for path, content in archives.items())
