# Dubai Investing Iterative Strategy Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic, bounded feedback loop that discovers and independently verifies Dubai Investing demo-strategy candidates from exact replay evidence without importing or changing live trading code.

**Architecture:** A new offline `research.dubai_iterative` package loads certified replay paths, evaluates immutable strategy genomes with a vectorized engine, diagnoses measurable failure modes, and creates bounded mutations and crossovers. A chronological coordinator keeps development and challenge dates isolated, while a separate scalar oracle recalculates finalists before an immutable report is published under `runtime_data/dubai_strategy_runs/`.

**Tech Stack:** Python 3.14, NumPy, pandas, PyArrow, matplotlib, pytest, existing replay/tick/money contracts.

---

## File Map

- `research/__init__.py`: marks the offline research namespace.
- `research/dubai_iterative/__init__.py`: public package version and exports.
- `research/dubai_iterative/contracts.py`: immutable genomes, folds, budgets, fingerprints and stop state.
- `research/dubai_iterative/dataset.py`: fail-closed Dubai replay and tick-path loader.
- `research/dubai_iterative/engine.py`: fast causal strategy evaluator.
- `research/dubai_iterative/evolution.py`: diagnostics, mutation, crossover, Pareto archive and bounded search loop.
- `research/dubai_iterative/oracle.py`: independent scalar finalist evaluator.
- `research/dubai_iterative/reporting.py`: immutable run identity, compact artifacts and charts.
- `research/dubai_iterative/__main__.py`: command-line entry point and progress output.
- `tests/test_dubai_iterative_*.py`: focused unit, integration and acceptance coverage.
- `README.md`: operator commands and confidence labels.
- `AGENTS.md`: current module names and offline/live boundary.

### Task 1: Immutable strategy and search contracts

**Files:**
- Create: `research/__init__.py`
- Create: `research/dubai_iterative/__init__.py`
- Create: `research/dubai_iterative/contracts.py`
- Create: `tests/test_dubai_iterative_contracts.py`

- [ ] **Step 1: Write failing contract tests**

```python
from research.dubai_iterative.contracts import SearchBudget, StrategyGenome


def test_genome_fingerprint_is_order_independent_and_stable():
    first = StrategyGenome.baseline().with_change(target_mode="fixed_basket", target_value=2.0)
    second = StrategyGenome.from_dict(first.to_dict())
    assert first.fingerprint == second.fingerprint


def test_budget_stops_on_first_reached_limit():
    budget = SearchBudget(max_generations=50, max_evaluations=1_000_000,
                          max_wall_seconds=7200, patience_generations=8,
                          max_lineage_depth=12)
    assert budget.stop_reason(generation=50, evaluations=10, elapsed_seconds=1,
                              stale_generations=0, deepest_lineage=1) == "max_generations"
    assert budget.stop_reason(generation=1, evaluations=1_000_000, elapsed_seconds=1,
                              stale_generations=0, deepest_lineage=1) == "max_evaluations"
    assert budget.stop_reason(generation=1, evaluations=10, elapsed_seconds=7200,
                              stale_generations=0, deepest_lineage=1) == "max_wall_seconds"
    assert budget.stop_reason(generation=8, evaluations=10, elapsed_seconds=1,
                              stale_generations=8, deepest_lineage=1) == "no_improvement"
    assert budget.stop_reason(generation=1, evaluations=10, elapsed_seconds=1,
                              stale_generations=0, deepest_lineage=12) == "max_lineage_depth"


def test_genome_rejects_more_than_observed_dubai_exposure():
    candidate = StrategyGenome.baseline().with_change(
        leg_count=4, volume_weights=(0.02, 0.01, 0.01, 0.01))
    assert candidate.validation_errors() == ["planned_volume_exceeds_0.04"]
```

- [ ] **Step 2: Run the contract tests and verify RED**

Run: `python -m pytest tests/test_dubai_iterative_contracts.py -q`

Expected: collection fails because `research.dubai_iterative.contracts` does not exist.

- [ ] **Step 3: Implement canonical contracts**

Implement frozen dataclasses with JSON-safe enums and no runtime imports:

```python
@dataclass(frozen=True)
class SearchBudget:
    max_generations: int = 50
    max_evaluations: int = 1_000_000
    max_wall_seconds: int = 7_200
    patience_generations: int = 8
    max_lineage_depth: int = 12

    def stop_reason(self, *, generation: int, evaluations: int,
                    elapsed_seconds: float, stale_generations: int,
                    deepest_lineage: int) -> str | None:
        checks = (
            (generation >= self.max_generations, "max_generations"),
            (evaluations >= self.max_evaluations, "max_evaluations"),
            (elapsed_seconds >= self.max_wall_seconds, "max_wall_seconds"),
            (stale_generations >= self.patience_generations, "no_improvement"),
            (deepest_lineage >= self.max_lineage_depth, "max_lineage_depth"),
        )
        return next((reason for reached, reason in checks if reached), None)
```

`StrategyGenome` must include entry, leg allocation, target, BE, stop, profit-lock,
time-exit, provider-management and causal-context fields. Its SHA-256 fingerprint
comes from sorted canonical JSON and excludes parent IDs and human descriptions.
`validation_errors()` rejects non-finite numbers, invalid state combinations,
future-dependent features and planned volume above 0.04 lots.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest tests/test_dubai_iterative_contracts.py -q`

Expected: all contract tests pass.

- [ ] **Step 5: Commit**

```powershell
git add research tests/test_dubai_iterative_contracts.py
git commit -m "feat: define bounded Dubai research contracts"
```

### Task 2: Fail-closed Dubai dataset

**Files:**
- Create: `research/dubai_iterative/dataset.py`
- Create: `tests/test_dubai_iterative_dataset.py`

- [ ] **Step 1: Write failing loader tests**

Create synthetic replay, audit and tick fixtures proving that the loader:

```python
def test_loader_keeps_exact_dubai_and_reports_every_exclusion(tmp_path):
    sources = write_dataset_fixture(tmp_path, statuses={
        "canal1_1": "exact", "canal1_2": "blocked", "canal2_3": "exact"})
    dataset = load_dubai_dataset(**sources, from_date="2026-07-27",
                                 to_date="2026-08-14")
    assert [path.signal_id for path in dataset.paths] == ["canal1_1"]
    assert dataset.exclusions == {"blocked": ["canal1_2"]}


def test_loader_uses_fill_event_milliseconds_and_executable_quote_side(tmp_path):
    dataset = load_one_buy_fixture(tmp_path)
    path = dataset.paths[0]
    assert path.opened_at.isoformat() == "2026-07-27T09:00:00.125000+00:00"
    assert path.exit_quotes.tolist() == path.bid.tolist()


def test_loader_refuses_stale_or_unverified_tick_contract(tmp_path):
    sources = write_dataset_fixture(tmp_path, tick_contract_valid=False)
    dataset = load_dubai_dataset(**sources)
    assert dataset.paths == ()
    assert dataset.exclusions["invalid_tick_contract"] == ["canal1_1"]
```

- [ ] **Step 2: Run loader tests and verify RED**

Run: `python -m pytest tests/test_dubai_iterative_dataset.py -q`

Expected: import fails because `dataset.py` does not exist.

- [ ] **Step 3: Implement immutable path loading**

Define `DubaiPath`, `DubaiLeg`, `ProviderEvent` and `DubaiDataset`. Read only
`replay_trades.jsonl`, `observed_tick_replay_audit.jsonl`, verified XAUUSD tick
Parquet, verified EUR conversion ticks and `broker_money_contract.json`.

Use fill-event timestamps when present, preserve every leg's actual open price,
volume and causal TP/SL histories, and build BUY exits from Bid and SELL exits
from Ask. Keep blocked/mismatch/missing rows grouped by named reason. Cache each
market day once and hash all input files.

- [ ] **Step 4: Run loader tests and verify GREEN**

Run: `python -m pytest tests/test_dubai_iterative_dataset.py -q`

Expected: all loader tests pass.

- [ ] **Step 5: Commit**

```powershell
git add research/dubai_iterative/dataset.py tests/test_dubai_iterative_dataset.py
git commit -m "feat: load exact Dubai replay paths"
```

### Task 3: Fast causal strategy engine

**Files:**
- Create: `research/dubai_iterative/engine.py`
- Create: `tests/test_dubai_iterative_engine.py`

- [ ] **Step 1: Write failing simulation tests**

Cover BUY and SELL quote sides, different fill prices, per-leg provider targets,
fixed basket exits, partial runners, BE, profit locks, fixed loss, time exit,
same-tick priority and adverse slippage:

```python
def test_same_tick_emergency_stop_precedes_profit_rule():
    path = synthetic_path_with_same_tick_extremes()
    genome = StrategyGenome.baseline().with_change(
        stop_mode="basket_money", stop_value=10.0,
        target_mode="fixed_basket", target_value=2.0)
    result = simulate(path, genome)
    assert result.exit_reason == "basket_stop"


def test_profit_lock_closes_after_measured_giveback():
    path = synthetic_move_path([0.0, 1.0, 3.0, 4.0, 2.5])
    genome = StrategyGenome.baseline().with_change(
        target_mode="none", profit_lock_arm=3.0, profit_lock_giveback=1.0)
    result = simulate(path, genome)
    assert result.exit_reason == "profit_lock"
    assert result.max_favourable_move == 4.0


def test_engine_never_reads_ticks_after_decision_exit():
    path = synthetic_path_with_poisoned_future_ticks()
    result = simulate(path, StrategyGenome.baseline())
    assert result.blockers == ()
    assert result.last_tick_index < path.poisoned_from_index
```

- [ ] **Step 2: Run engine tests and verify RED**

Run: `python -m pytest tests/test_dubai_iterative_engine.py -q`

Expected: import fails because `engine.py` does not exist.

- [ ] **Step 3: Implement deterministic state-machine evaluation**

Precompute directional per-leg and basket price paths once. Evaluate rules in
this fixed priority: invalid evidence, emergency basket stop, explicit provider
close when enabled, effective SL, target/partial close, profit lock, inactivity
and absolute time limit. Every result records ticket closes, EUR P&L, maximum
favourable/adverse excursion, floating drawdown, giveback, decision timestamps,
reason and blockers.

Implement actual-MT5 entry first, then causal alternatives using only ticks
after the Telegram observation: delay, adverse pullback and favorable momentum
confirmation with explicit expiry. A hypothetical BUY fills on Ask and SELL on
Bid. Unfilled entries remain zero-exposure rows rather than disappearing.

- [ ] **Step 4: Run engine tests and verify GREEN**

Run: `python -m pytest tests/test_dubai_iterative_engine.py -q`

Expected: all engine tests pass.

- [ ] **Step 5: Commit**

```powershell
git add research/dubai_iterative/engine.py tests/test_dubai_iterative_engine.py
git commit -m "feat: simulate causal Dubai strategy genomes"
```

### Task 4: Structured critique and targeted refinement

**Files:**
- Create: `research/dubai_iterative/evolution.py`
- Create: `tests/test_dubai_iterative_evolution.py`

- [ ] **Step 1: Write failing feedback tests**

```python
def test_giveback_diagnosis_generates_profit_protection_children():
    diagnosis = diagnose(candidate_result(max_floating=18.0, pnl=-12.0))
    children = mutate_from_diagnosis(StrategyGenome.baseline(), diagnosis,
                                     seed=7)
    assert "profit_given_back" in diagnosis.labels
    assert any(child.profit_lock_arm is not None for child in children)


def test_fingerprints_prevent_duplicate_children():
    population = deduplicate([StrategyGenome.baseline(), StrategyGenome.baseline()])
    assert len(population) == 1


def test_challenge_metrics_are_not_available_to_mutation():
    critic = RecordingCritic()
    evolve_generation(training_results(), critic=critic,
                      challenge_results=poisoned_challenge_results())
    assert critic.seen_signal_ids == training_signal_ids()
```

- [ ] **Step 2: Run evolution tests and verify RED**

Run: `python -m pytest tests/test_dubai_iterative_evolution.py -q`

Expected: import fails because `evolution.py` does not exist.

- [ ] **Step 3: Implement diagnostics and variation**

Create structured labels for profit giveback, stop-before-recovery, premature
target, stagnation, harmful/helpful BE, harmful/helpful provider management,
entry timing cost, marginal-leg damage and day concentration. Each mutation
changes one compatible field and records parent fingerprint, label and expected
effect. Crossover combines only compatible entry, exposure and management
blocks. Reject duplicate, invalid, hindsight-dependent and over-complex
children before simulation.

Maintain a Pareto archive over chronological-fold profit, realized/floating
drawdown, worst day, concentration and genome complexity. Do not collapse the
archive into one profit score.

- [ ] **Step 4: Run evolution tests and verify GREEN**

Run: `python -m pytest tests/test_dubai_iterative_evolution.py -q`

Expected: all feedback tests pass.

- [ ] **Step 5: Commit**

```powershell
git add research/dubai_iterative/evolution.py tests/test_dubai_iterative_evolution.py
git commit -m "feat: refine Dubai strategies from measured failures"
```

### Task 5: Bounded chronological coordinator and checkpoints

**Files:**
- Modify: `research/dubai_iterative/evolution.py`
- Create: `tests/test_dubai_iterative_search.py`

- [ ] **Step 1: Write failing anti-loop and fold tests**

```python
@pytest.mark.parametrize("limit,expected", [
    ({"max_generations": 2}, "max_generations"),
    ({"max_evaluations": 5}, "max_evaluations"),
    ({"max_wall_seconds": 0}, "max_wall_seconds"),
    ({"patience_generations": 1}, "no_improvement"),
])
def test_search_always_stops_at_configured_boundary(limit, expected, tmp_path):
    report = run_search(tiny_dataset(), budget=SearchBudget(**limit),
                        output_dir=tmp_path)
    assert report.stop_reason == expected


def test_resume_produces_same_result_as_uninterrupted_run(tmp_path):
    uninterrupted = run_seeded_search(tiny_dataset(), generations=4)
    checkpoint = run_seeded_search(tiny_dataset(), generations=2,
                                   output_dir=tmp_path)
    resumed = resume_search(checkpoint.path, generations=4)
    assert resumed.frontier_fingerprints == uninterrupted.frontier_fingerprints


def test_known_historical_overfit_is_not_promoted():
    result = evaluate_known_split(train_net=100.33, challenge_net=-83.57)
    assert result.confidence == "retrospective_unstable"
```

- [ ] **Step 2: Run search tests and verify RED**

Run: `python -m pytest tests/test_dubai_iterative_search.py -q`

Expected: missing coordinator functions fail.

- [ ] **Step 3: Implement the bounded loop**

Use an ordinary `for generation in range(max_generations)` loop, never recursive
calls. Before and after each batch check all five budget limits. Keep a global
fingerprint set, maximum lineage depth, atomic generation checkpoint and fixed
NumPy random state. Stop with one of: `max_generations`, `max_evaluations`,
`max_wall_seconds`, `no_improvement`, `max_lineage_depth`,
`population_exhausted`, `user_interrupt` or `completed`.

Run four expanding folds from the design. Each fold creates candidates using
development dates only, freezes them, and then evaluates its challenge dates.
Challenge outcomes can enter the final stability report but cannot generate or
mutate candidates within that fold.

- [ ] **Step 4: Run search tests and verify GREEN**

Run: `python -m pytest tests/test_dubai_iterative_search.py -q`

Expected: all stop, resume, determinism and isolation tests pass.

- [ ] **Step 5: Commit**

```powershell
git add research/dubai_iterative/evolution.py tests/test_dubai_iterative_search.py
git commit -m "feat: bound and checkpoint iterative strategy search"
```

### Task 6: Independent scalar oracle and stress gates

**Files:**
- Create: `research/dubai_iterative/oracle.py`
- Create: `tests/test_dubai_iterative_oracle.py`

- [ ] **Step 1: Write failing independent-verification tests**

```python
def test_oracle_accepts_exact_engine_result():
    certificate = certify_candidate(tiny_paths(), candidate(), fast_results())
    assert certificate.status == "pass"


def test_oracle_blocks_one_cent_disagreement():
    rows = fast_results()
    rows[0] = replace(rows[0], pnl_eur=rows[0].pnl_eur + Decimal("0.01"))
    certificate = certify_candidate(tiny_paths(), candidate(), rows)
    assert certificate.status == "blocked"
    assert certificate.mismatches[0].field == "pnl_eur"


def test_stress_gate_rejects_candidate_that_flips_under_costs():
    stressed = stress_candidate(candidate(), latency_ms=2000,
                                entry_slip=0.25, exit_slip=0.25,
                                spread_addition=0.20)
    assert stressed.promotion_eligible is False
```

- [ ] **Step 2: Run oracle tests and verify RED**

Run: `python -m pytest tests/test_dubai_iterative_oracle.py -q`

Expected: import fails because `oracle.py` does not exist.

- [ ] **Step 3: Implement the independent oracle**

Use scalar tick iteration and `Decimal` money rounding. Do not import
`research.dubai_iterative.engine`. It may share immutable contracts and loaded
source records, but independently selects fills, rule transitions, exit side,
time and money conversion. Compare every finalist leg's fill, close, reason and
EUR result.

Run base, 250 ms, 1 s and 2 s latency plus adverse spread/slippage scenarios.
Record parameter-neighborhood stability, portfolio overlap, floating drawdown,
recovery duration and day-block bootstrap. Any mismatch or invalid money
contract blocks ranking.

- [ ] **Step 4: Run oracle tests and verify GREEN**

Run: `python -m pytest tests/test_dubai_iterative_oracle.py -q`

Expected: all oracle and stress tests pass.

- [ ] **Step 5: Commit**

```powershell
git add research/dubai_iterative/oracle.py tests/test_dubai_iterative_oracle.py
git commit -m "feat: independently certify Dubai finalists"
```

### Task 7: Immutable reports, progress and CLI

**Files:**
- Create: `research/dubai_iterative/reporting.py`
- Create: `research/dubai_iterative/__main__.py`
- Create: `tests/test_dubai_iterative_reporting.py`
- Create: `tests/test_dubai_iterative_cli.py`

- [ ] **Step 1: Write failing report and CLI tests**

```python
def test_run_identity_is_stable_and_conflicting_bytes_fail(tmp_path):
    first = publish_run(sample_report(), tmp_path)
    second = publish_run(sample_report(), tmp_path)
    assert first.run_id == second.run_id
    corrupt(first.run_dir / "frontier.json")
    with pytest.raises(ProvenanceConflictError):
        publish_run(sample_report(), tmp_path)


def test_cli_emits_bounded_progress_and_stop_reason(tmp_path, capsys):
    code = main(["--fixture", "tiny", "--max-generations", "2",
                 "--output-root", str(tmp_path), "--progress"])
    output = capsys.readouterr().out
    assert code == 0
    assert "Generacion 2/2" in output
    assert "Parada: max_generations" in output
```

- [ ] **Step 2: Run report tests and verify RED**

Run: `python -m pytest tests/test_dubai_iterative_reporting.py tests/test_dubai_iterative_cli.py -q`

Expected: imports fail because reporting and CLI modules do not exist.

- [ ] **Step 3: Implement compact immutable output**

Publish under `runtime_data/dubai_strategy_runs/<run-id>/`:

```text
run_card.json
frontier.json
generation_summary.jsonl
candidate_matrix.parquet
signal_results.parquet
charts/equity.png
charts/floating_drawdown.png
charts/generation_progress.png
```

The run identity binds source hashes, exact signal IDs, exclusions, grammar
version, fold definitions, seed, budgets, engine versions and stress scenarios.
Write files atomically and reject conflicting reruns. Console progress shows
fold, generation, evaluated/maximum recipes, frontier size, stale generations,
elapsed time and active stopping limit.

Add CLI arguments for all source paths, dates, seed and five stopping limits.
Defaults are 50 generations, 1,000,000 evaluated recipes, 7,200 seconds,
8 stale generations and lineage depth 12. `Ctrl+C` writes a resumable checkpoint
and exits without a traceback.

- [ ] **Step 4: Run report tests and verify GREEN**

Run: `python -m pytest tests/test_dubai_iterative_reporting.py tests/test_dubai_iterative_cli.py -q`

Expected: all reporting, conflict, progress and interrupt tests pass.

- [ ] **Step 5: Commit**

```powershell
git add research/dubai_iterative tests/test_dubai_iterative_reporting.py tests/test_dubai_iterative_cli.py
git commit -m "feat: publish bounded Dubai research runs"
```

### Task 8: Historical acceptance, profiling and documentation

**Files:**
- Create: `tests/test_dubai_iterative_acceptance.py`
- Modify: `README.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Write the fixed-window acceptance test**

```python
@pytest.mark.slow
def test_robust_window_contract(real_dubai_sources):
    dataset = load_dubai_dataset(**real_dubai_sources,
                                 from_date="2026-07-27",
                                 to_date="2026-08-14")
    assert len(dataset.paths) == 42
    assert sum(len(items) for items in dataset.exclusions.values()) == 3
    assert dataset.actual_pnl_eur == Decimal("-224.64")


@pytest.mark.slow
def test_fast_and_oracle_agree_for_every_frontier_candidate(real_dubai_sources):
    run = run_search(load_dubai_dataset(**real_dubai_sources),
                     budget=SearchBudget(max_generations=3,
                                         max_evaluations=5_000,
                                         max_wall_seconds=600,
                                         patience_generations=2,
                                         max_lineage_depth=4))
    assert run.oracle_mismatches == ()
    assert run.confidence in {"retrospective_unstable", "demo_candidate"}
```

- [ ] **Step 2: Run acceptance tests and verify RED if a contract is missing**

Run: `python -m pytest tests/test_dubai_iterative_acceptance.py -q -m slow`

Expected: the first execution exposes any source-path, count, money or oracle
contract not yet handled. Fix production research code, not the expected facts.

- [ ] **Step 3: Profile before increasing the search budget**

Run:

```powershell
python -m cProfile -o runtime_data/dubai_search_profile.prof `
  -m research.dubai_iterative --from 2026-07-27 --to 2026-08-14 `
  --max-generations 2 --max-evaluations 10000 --max-wall-seconds 600
```

Inspect the top cumulative functions with `python -m pstats
runtime_data/dubai_search_profile.prof`. Optimize only measured hotspots,
prefer cached NumPy path matrices, and rerun the acceptance and oracle tests
after each optimization.

- [ ] **Step 4: Document operator workflow and safety labels**

Add the exact run/resume commands, artifact locations, stopping-limit meanings,
`retrospective_unstable`, `demo_candidate` and `validated_edge` definitions to
`README.md`. Add the new package map and explicit prohibition on live imports or
automatic deployment to `AGENTS.md`.

- [ ] **Step 5: Run focused and full verification**

Run:

```powershell
python -m pytest tests/test_dubai_iterative_contracts.py `
  tests/test_dubai_iterative_dataset.py `
  tests/test_dubai_iterative_engine.py `
  tests/test_dubai_iterative_evolution.py `
  tests/test_dubai_iterative_search.py `
  tests/test_dubai_iterative_oracle.py `
  tests/test_dubai_iterative_reporting.py `
  tests/test_dubai_iterative_cli.py `
  tests/test_dubai_iterative_acceptance.py -q
python -m pytest -q
git diff --check
```

Expected: all focused tests and the complete suite pass; `git diff --check`
prints nothing.

- [ ] **Step 6: Run the first bounded historical search**

```powershell
python -m research.dubai_iterative `
  --from 2026-07-27 --to 2026-08-14 `
  --max-generations 50 --max-evaluations 1000000 `
  --max-wall-seconds 7200 --patience-generations 8 `
  --max-lineage-depth 12 --seed 20260817 --progress
```

Expected: the process ends with a named stop reason, an immutable run directory,
42 exact paths, three visible exclusions, no oracle mismatches and no live or VM
changes. A positive candidate remains `demo_candidate` until fresh forward data
and project sample-size gates pass.

- [ ] **Step 7: Commit**

```powershell
git add README.md AGENTS.md tests/test_dubai_iterative_acceptance.py
git commit -m "docs: operationalize iterative Dubai research"
```
