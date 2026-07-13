# Recursive Reliability Learning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct the July 13 replay/runtime defects and make every retained session improve a deterministic, review-gated reliability registry.

**Architecture:** Keep live execution and offline learning physically separate. First establish a semantic UTC-v3 tick contract and honest simulation gates, then harden impossible MT5 modifications and provider semantics, and finally build a deterministic corpus auditor that the post-session watcher publishes without ever modifying runtime rules.

**Tech Stack:** Python 3, pytest, JSON/JSONL, pandas/pyarrow Parquet, MetaTrader5 API, existing event journal and watcher.

---

## File Map

- `tools/ensure_replay_tick_cache.py`: broker-server timestamp conversion, UTC-v3 contracts and fill-anchor validation.
- `observed_tick_replay_validator.py`: consumes only semantically valid tick contracts.
- `simulation_run_provenance.py`: separates byte integrity from exact-market validity and conclusion permission.
- `strategy_farm.py`: labels blocked runs diagnostic-only and never ranks them.
- `executor.py`: validates stop modifications before MT5 submission and preserves safer existing protection.
- `pending_actions.py`: coalesces equivalent modifications and fails fast on structurally impossible stops.
- `live_auditor.py`: applies new-ticket adoption grace before orphan incidents.
- `provider_signal_catalog.py`: record types and deterministic management semantics.
- `recursive_log_learning.py`: deterministic whole-corpus health and normalized pattern registry.
- `tools/run_bot_watch.py`: post-session learner invocation and artifact staging.
- `analysis/daily_report.py`: honest cohort/calendar P&L and account currency.
- `analysis/bot_execution_quality.py`: current BE event vocabulary.
- `AGENTS.md`: UTC-v3 and report-status documentation.

### Task 1: UTC-v3 Tick Contracts

**Files:**
- Modify: `tools/ensure_replay_tick_cache.py`
- Modify: `mt5_tick_cache.py`
- Test: `tests/test_ensure_replay_tick_cache.py`
- Test: `tests/test_observed_tick_replay_validator.py`

- [ ] **Step 1: Write failing server-clock conversion tests**

```python
def test_mt5_tick_source_converts_vantage_server_epoch_to_utc(monkeypatch):
    source = _source_with_tick_clock(
        monkeypatch,
        current_server_utc=datetime(2026, 7, 14, 15, 0, tzinfo=timezone.utc),
        current_real_utc=datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc),
        raw_tick_server_epoch=datetime(2026, 7, 13, 11, 0, tzinfo=timezone.utc),
    )
    ticks = source.fetch_ticks(
        datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 13, 8, 1, tzinfo=timezone.utc),
    )
    assert ticks.iloc[0].time_utc.to_pydatetime() == datetime(
        2026, 7, 13, 8, 0, tzinfo=timezone.utc)
```

```python
def test_v2_contract_is_rejected_even_when_hash_matches(tmp_path):
    _write_legacy_v2_contract(tmp_path, date(2026, 7, 13))
    assert load_valid_day_contract(tmp_path, date(2026, 7, 13)) is None
```

```python
def test_fill_anchor_rejects_three_hour_shift(tmp_path):
    contract = build_day_contract(
        _ticks_at("2026-07-13T11:00:00Z", bid=4059.37, ask=4059.61),
        anchors=[_fill("2026-07-13T08:00:00Z", 4059.61)],
    )
    assert contract["semantic_time_valid"] is False
    assert contract["semantic_errors"] == ["fill_anchor_outside_tolerance"]
```

- [ ] **Step 2: Run the focused tests and confirm they fail for v2/direct-UTC behavior**

Run: `python -m pytest tests/test_ensure_replay_tick_cache.py tests/test_observed_tick_replay_validator.py -q`

Expected: failures show direct UTC requests, accepted v2 sidecars and missing semantic anchor fields.

- [ ] **Step 3: Implement day-specific server-clock conversion and v3 sidecars**

```python
TICK_TIME_CONTRACT = "mt5_server_epoch_utc_v3"

@dataclass(frozen=True)
class TickTimeEvidence:
    source_time_basis: str
    utc_offset_seconds: int
    offset_detection_method: str
    reference_server_epoch: int
    reference_observed_utc: str

def server_epoch_to_utc(series, offset_seconds: int):
    return pd.to_datetime(series, unit="ms", utc=True) - pd.Timedelta(
        seconds=offset_seconds)
```

`MT5TickSource` must shift request bounds by the detected offset and expose its
`TickTimeEvidence`. `write_day_contract()` must include this evidence plus
`semantic_time_valid`, anchor count/tolerance and errors. `load_valid_day_contract()`
must reject old contracts, hash mismatches and semantic failures.

- [ ] **Step 4: Run focused tests and confirm green**

Run: `python -m pytest tests/test_ensure_replay_tick_cache.py tests/test_observed_tick_replay_validator.py -q`

- [ ] **Step 5: Commit**

```bash
git add tools/ensure_replay_tick_cache.py mt5_tick_cache.py tests/test_ensure_replay_tick_cache.py tests/test_observed_tick_replay_validator.py
git commit -m "fix: validate broker tick timestamps in utc"
```

### Task 2: Honest Simulation And Provenance Gates

**Files:**
- Modify: `simulation_run_provenance.py`
- Modify: `strategy_farm.py`
- Test: `tests/test_simulation_run_provenance.py`
- Test: `tests/test_strategy_farm.py`

- [ ] **Step 1: Write failing independent-gate tests**

```python
def test_integrity_does_not_authorize_conclusions_when_replay_is_blocked():
    evidence = build_run_evidence(
        **_valid_inputs(),
        market_replay={"exact": 0, "blocked": 12, "mismatched": 0},
    )
    assert evidence["validation"] == {
        "artifact_integrity_verified": True,
        "market_replay_verified": False,
        "conclusions_allowed": False,
        "mode": "diagnostic_only",
    }
```

```python
def test_diagnostic_farm_has_no_ranking_or_selected_policy():
    report = build_farm_report(**_blocked_market_replay_inputs())
    assert report["validation"]["mode"] == "diagnostic_only"
    assert report["ranking"] == []
    assert report["selected_policy"] is None
```

- [ ] **Step 2: Run tests and confirm the current `verified_now` field is insufficient**

Run: `python -m pytest tests/test_simulation_run_provenance.py tests/test_strategy_farm.py -q`

- [ ] **Step 3: Add explicit validation state**

```python
validation = {
    "artifact_integrity_verified": not reproducibility_errors,
    "market_replay_verified": exact == selected and blocked == 0 and mismatched == 0,
}
validation["conclusions_allowed"] = all(validation.values())
validation["mode"] = (
    "verified_simulation" if validation["conclusions_allowed"]
    else "diagnostic_only"
)
```

Keep `reproducibility.verified_now` for schema compatibility, but define it as
artifact integrity only and expose the stronger validation next to it. Archive
blocked diagnostics immutably while excluding policy metrics from ranking.

- [ ] **Step 4: Run focused tests and commit**

Run: `python -m pytest tests/test_simulation_run_provenance.py tests/test_strategy_farm.py -q`

```bash
git add simulation_run_provenance.py strategy_farm.py tests/test_simulation_run_provenance.py tests/test_strategy_farm.py
git commit -m "fix: separate replay validity from artifact integrity"
```

### Task 3: Fail-Fast MT5 Stop Management

**Files:**
- Modify: `executor.py`
- Modify: `pending_actions.py`
- Modify: `live_auditor.py`
- Test: `tests/test_executor_anomalies.py`
- Test: `tests/test_pending_actions.py`
- Test: `tests/test_live_auditor.py`

- [ ] **Step 1: Write failing stop-precondition and coalescing tests**

```python
def test_tp_only_change_keeps_safer_existing_sell_sl(monkeypatch):
    position = _sell_position(price_current=4061.50, sl=4060.95, tp=4055.0)
    request = _capture_modify_request(monkeypatch, position)
    executor.modify_sltp_rc(position.ticket, new_sl=4059.61, new_tp=4052.0)
    assert request["sl"] == 4060.95
    assert request["tp"] == 4052.0
```

```python
@pytest.mark.asyncio
async def test_temporarily_invalid_stop_waits_without_mt5_submission(monkeypatch):
    action = _make_action(new_sl=4059.61)
    sent = []
    monkeypatch.setattr(executor, "modify_sltp_rc", lambda *a, **k: sent.append(a))
    monkeypatch.setattr(executor, "modify_precondition", lambda *a, **k: "wait_market")
    result = await PendingQueue()._try_once(action)
    assert result == "WAIT_PRECONDITION"
    assert action.attempts == 0
    assert sent == []
```

```python
def test_equivalent_modify_actions_are_coalesced():
    queue = PendingQueue()
    queue.add(_action(ticket=1, sl=4059.61, tp=4052.0))
    queue.add(_action(ticket=1, sl=4059.61, tp=4052.0))
    assert len(queue._actions) == 1
```

```python
def test_new_ticket_is_not_orphan_during_adoption_grace():
    issues = _audit_new_fill(age_seconds=0.145)
    assert "mt5_orphan_position" not in {issue.code for issue in issues}
```

- [ ] **Step 2: Run focused tests and observe repeated-stop behavior fail**

Run: `python -m pytest tests/test_executor_anomalies.py tests/test_pending_actions.py tests/test_live_auditor.py -q`

- [ ] **Step 3: Implement structural validation, preservation and incident keys**

```python
@dataclass(frozen=True)
class StopValidation:
    valid_now: bool
    can_become_valid: bool
    reason: str | None
    effective_sl: float | None

def validate_position_stop(position, tick, symbol_info, requested_sl, *, tp_only):
    if requested_sl is None:
        return StopValidation(True, False, None, position.sl or None)
    is_buy = position.type == mt5.ORDER_TYPE_BUY
    point = float(getattr(symbol_info, "point", 0.0) or 0.0)
    stop_gap = float(getattr(symbol_info, "trade_stops_level", 0) or 0) * point
    market_side = float(tick.bid if is_buy else tick.ask)
    valid_now = (
        requested_sl < market_side - stop_gap
        if is_buy else requested_sl > market_side + stop_gap
    )
    if valid_now:
        return StopValidation(True, False, None, requested_sl)
    existing_sl = float(position.sl or 0.0)
    existing_valid = existing_sl > 0 and (
        existing_sl < market_side - stop_gap
        if is_buy else existing_sl > market_side + stop_gap
    )
    if tp_only and existing_valid:
        return StopValidation(True, True, "requested_sl_waits_for_market", existing_sl)
    return StopValidation(False, True, "requested_sl_waits_for_market", existing_sl or None)
```

`PendingQueue.add()` replaces an older equivalent ticket/action payload instead
of appending it. `_try_once()` uses validation evidence to distinguish a
temporarily near-market stop from a permanent malformed request. A waiting
action is reevaluated on ticks but does not call MT5 or increment attempts until
its price precondition passes. Structural incidents carry one stable key and
aggregate ticket/attempt totals.

- [ ] **Step 4: Run focused tests and commit**

Run: `python -m pytest tests/test_executor_anomalies.py tests/test_pending_actions.py tests/test_live_auditor.py -q`

```bash
git add executor.py pending_actions.py live_auditor.py tests/test_executor_anomalies.py tests/test_pending_actions.py tests/test_live_auditor.py
git commit -m "fix: prevent repeated impossible stop modifications"
```

### Task 4: Canonical Provider Semantics

**Files:**
- Modify: `provider_signal_catalog.py`
- Modify: `strategy_simulator.py`
- Test: `tests/test_provider_signal_catalog.py`
- Test: `tests/test_strategy_simulator.py`

- [ ] **Step 1: Write failing record-type and management tests**

```python
def test_context_photo_replies_do_not_create_formal_signal():
    report = build_catalog_report(_four_hour_support_post(), [])
    assert report["signals"][0]["record_type"] == "context_setup"
    assert report["summary"]["formal_signals"] == 0
```

```python
@pytest.mark.parametrize(("text", "action", "modality"), [
    ("Move SL to 4061", "move_sl", "direct"),
    ("Close TP7 when happy", "close", "optional"),
    ("TP1 4035 / SL 4025", "update_levels", "direct"),
])
def test_management_semantics_survive_classifier_disagreement(text, action, modality):
    row = canonical_management_event(_raw(text), classifier={"action": "informational"})
    assert row["classified_action"] == action
    assert row["modality"] == modality
    assert row["semantic_source"] == "deterministic_parser"
```

- [ ] **Step 2: Run focused tests and confirm context inflation/action loss**

Run: `python -m pytest tests/test_provider_signal_catalog.py tests/test_strategy_simulator.py -q`

- [ ] **Step 3: Implement record types and deterministic action precedence**

```python
RECORD_TYPES = {
    "formal_signal", "context_setup", "daily_summary",
    "management_only", "unknown_candidate",
}

def choose_management_semantics(deterministic, classifier):
    if deterministic and deterministic["action"] != "informational":
        return {**deterministic, "semantic_source": "deterministic_parser"}
    return {**classifier, "semantic_source": "classifier"}
```

Only complete `formal_signal` rows enter strategy denominators. Preserve every
other row as evidence. Store `has_photo`, media reference/hash when available,
and extraction state without inventing executable levels.

- [ ] **Step 4: Run focused tests and commit**

Run: `python -m pytest tests/test_provider_signal_catalog.py tests/test_strategy_simulator.py -q`

```bash
git add provider_signal_catalog.py strategy_simulator.py tests/test_provider_signal_catalog.py tests/test_strategy_simulator.py
git commit -m "feat: preserve canonical provider message semantics"
```

### Task 5: Deterministic Recursive Learner

**Files:**
- Create: `recursive_log_learning.py`
- Create: `tests/test_recursive_log_learning.py`

- [ ] **Step 1: Write failing deterministic-registry tests**

```python
def test_repeated_versions_collapse_to_one_pattern():
    report, registry = build_learning_outputs(
        events=_invalid_stop_versions(raw_count=1170),
        replay_rows=[], accounting_rows=[], observed_rows=[], provider_catalog={}
    )
    pattern = registry["patterns"][0]
    assert pattern["pattern_id"] == "execution.invalid_stops.modify_sltp"
    assert pattern["occurrences"] == 1
    assert pattern["raw_events"] == 1170
```

```python
def test_rerun_is_byte_deterministic(tmp_path):
    first = write_learning_outputs(**_corpus(), output_dir=tmp_path)
    second = write_learning_outputs(**_corpus(), output_dir=tmp_path)
    assert first.report_bytes == second.report_bytes
    assert first.registry_bytes == second.registry_bytes
```

```python
def test_covered_status_requires_reviewed_rule_and_test():
    with pytest.raises(ValueError, match="coverage evidence"):
        merge_review_metadata(_pattern(), {"status": "covered"})
```

```python
def test_strategy_gate_requires_all_layers():
    health = build_health(_all_green_except(market_replay=False))
    assert health["safe_for_strategy_simulation"] is False
    assert health["mode"] == "diagnostic_only"
```

- [ ] **Step 2: Run tests and confirm module is absent**

Run: `python -m pytest tests/test_recursive_log_learning.py -q`

- [ ] **Step 3: Implement pure aggregation and CLI**

```python
@dataclass(frozen=True)
class LearningOutputs:
    report: dict
    registry: dict
    report_bytes: bytes
    registry_bytes: bytes

def build_learning_outputs(*, events, replay_rows, accounting_rows,
                           observed_rows, provider_catalog, review_metadata=None):
    patterns = collect_normalized_patterns(events, replay_rows, observed_rows)
    registry = build_registry(patterns, review_metadata or {})
    report = build_health_report(
        events, replay_rows, accounting_rows, observed_rows,
        provider_catalog, registry,
    )
    return LearningOutputs(report, registry, canonical(report), canonical(registry))
```

The CLI reads repository defaults, writes atomically and excludes generated
timestamps from deterministic content. Evidence counts are rebuilt from the
whole retained corpus; reruns never increment previous derived counts.

- [ ] **Step 4: Run focused tests and commit**

Run: `python -m pytest tests/test_recursive_log_learning.py -q`

```bash
git add recursive_log_learning.py tests/test_recursive_log_learning.py
git commit -m "feat: add recursive reliability pattern registry"
```

### Task 6: Watcher Integration And Honest Reports

**Files:**
- Modify: `tools/run_bot_watch.py`
- Modify: `tests/test_run_bot_watch.py`
- Modify: `analysis/daily_report.py`
- Modify: `analysis/bot_execution_quality.py`
- Modify: `AGENTS.md`
- Add tests to the nearest existing analysis test module or create: `tests/test_analysis_reports.py`

- [ ] **Step 1: Write failing watcher and report tests**

```python
def test_watcher_runs_learner_after_catalog_and_observed_replay(monkeypatch):
    calls = _capture_pipeline(monkeypatch)
    watch._push_session_data("watcher exit")
    assert calls.index("recursive_log_learning") > calls.index("provider_catalog")
    assert "data/log_learning_report.json" in calls.added
    assert "data/log_pattern_registry.json" in calls.added
```

```python
def test_daily_report_separates_signal_cohort_from_server_calendar():
    report = build_daily_report(_july_13_fixture())
    assert report["signal_cohort_pnl"] == pytest.approx(15.12)
    assert report["server_calendar_pnl"] == pytest.approx(45.12)
    assert report["currency"] == "EUR"
```

```python
def test_execution_quality_accepts_classifier_be_event():
    report = analyze_execution([{"ev": "be_armed_classifier"}])
    assert report["explicit_be_applied"] == 1
```

- [ ] **Step 2: Run focused tests and confirm missing artifacts/incorrect labels**

Run: `python -m pytest tests/test_run_bot_watch.py tests/test_analysis_reports.py -q`

- [ ] **Step 3: Integrate post-session learner and repair reporting vocabulary**

The watcher removes stale learner outputs before invocation, accepts a
diagnostic report as a successful build, and stages only files produced by the
current run. Reports use account currency and distinct cohort/calendar labels.
Update `AGENTS.md` to require UTC-v3 contracts and mark unrepaired historical
analysis scripts as historical rather than authoritative.

- [ ] **Step 4: Run focused tests and commit**

Run: `python -m pytest tests/test_run_bot_watch.py tests/test_analysis_reports.py -q`

```bash
git add tools/run_bot_watch.py tests/test_run_bot_watch.py analysis/daily_report.py analysis/bot_execution_quality.py tests/test_analysis_reports.py AGENTS.md
git commit -m "feat: publish recursive session health evidence"
```

### Task 7: Regenerate, Verify And Publish

**Files:**
- Regenerate: `data/provider_signal_catalog.json`
- Regenerate: `data/log_learning_report.json`
- Regenerate: `data/log_pattern_registry.json`
- Regenerate when local MT5 proves v3 semantics: `data/replay_tick_cache_status.json`, `data/observed_tick_replay_audit.jsonl`, `data/strategy_farm.json`, `data/simulation_runs/**`

- [ ] **Step 1: Run non-market builders on the retained corpus**

Run: `python provider_signal_catalog.py --quiet`

Run: `python recursive_log_learning.py --quiet`

- [ ] **Step 2: Run exact replay only if valid Vantage MT5 history is available**

Run: `python tools/ensure_replay_tick_cache.py --ensure --quiet`

Run: `python observed_tick_replay_validator.py --quiet`

Run: `python strategy_farm.py`

Expected: either exact UTC-v3 replay with `verified_simulation`, or an explicit
non-zero/diagnostic result. Never convert a blocked result into performance
numbers.

- [ ] **Step 3: Run complete verification**

Run: `python -m pytest -q`

Expected: all tests pass, with only the repository's documented skip(s).

Run: `git diff --check`

Expected: no whitespace errors.

- [ ] **Step 4: Review repository state and current remote**

Run: `git status -sb`

Run: `git diff --stat origin/main...HEAD`

Run: `git fetch origin main`

If the VM added data, rebase and rerun the complete suite.

- [ ] **Step 5: Push the verified linear history**

Run: `git push origin HEAD:main`

Expected: GitHub `main` points at the verified implementation and includes all
previous unpushed simulator-provenance commits.
