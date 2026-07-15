# Recursive Reliability Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make recursive reliability learning auditable, freshness-safe and capable of proving that a fixed pattern stays fixed without changing live trading behavior.

**Architecture:** Keep `recursive_log_learning.py` as a deterministic offline detector. Add a focused review service and thin CLI that are the only supported writers of the human review ledger, plus a publication-status module consumed by the VM watcher. The watcher always attempts learning and publishes a current positive or negative status, while runtime trading modules remain independent.

**Tech Stack:** Python 3.14, standard library (`argparse`, `hashlib`, `json`, `pathlib`, `subprocess`, `tempfile`), pytest, existing JSON/JSONL artifacts and Git.

---

## File Map

- Modify `recursive_log_learning.py`: validate versioned reviews, expose default-corpus builds and publish latest evidence time.
- Create `log_pattern_review.py`: review validation, Git/pytest verification, deterministic corpus proof and atomic ledger writes.
- Create `tools/review_log_pattern.py`: command-line entry point only.
- Create `log_learning_publication.py`: build authoritative status from dependencies and artifact fingerprints.
- Modify `tools/run_bot_watch.py`: always run learning, publish status and stage the new artifacts.
- Create `tests/test_log_pattern_review.py`: review-service TDD coverage.
- Create `tests/test_log_learning_publication.py`: status-contract TDD coverage.
- Modify `tests/test_recursive_log_learning.py`: versioned review and recurrence contract.
- Modify `tests/test_run_bot_watch.py`: unconditional publication and staging contract.
- Create `data/log_pattern_reviews.json`: verified decisions only.
- Generate `data/log_learning_status.json`, `data/log_learning_report.json`, `data/log_pattern_registry.json` and the already-required schema-v3 `data/provider_signal_catalog.json`.

### Task 1: Harden The Detector Contract

**Files:**
- Modify: `tests/test_recursive_log_learning.py`
- Modify: `recursive_log_learning.py`

- [ ] **Step 1: Write failing review-schema and freshness tests**

Add tests that use a versioned ledger and require tool-generated verification:

```python
def _covered_review(covered_after="2026-07-14T08:00:00+00:00"):
    return {
        "schema_version": 1,
        "reviews": {
            "execution.invalid_stops.modify_sltp": {
                "status": "covered",
                "rule_version": "pending-actions.invalid-stop-preflight.v1",
                "fix_commit": "6386be66cc986bdf00c1d0c5e773277cbfa6392e",
                "regression_test": (
                    "tests/test_pending_actions.py::TestModifyPreconditions::"
                    "test_invalid_stop_waits_without_mt5_submission"
                ),
                "reviewed_by": "project_owner",
                "reviewed_at_utc": covered_after,
                "covered_after_utc": covered_after,
                "verification": {
                    "test_passed": True,
                    "full_suite_passed": True,
                    "corpus_rebuild_deterministic": True,
                    "source_fingerprint": "a" * 64,
                    "verified_commit": "b" * 40,
                },
            }
        },
    }


def test_covered_review_requires_versioned_verified_evidence():
    review = _covered_review()
    review["reviews"]["execution.invalid_stops.modify_sltp"][
        "verification"
    ]["test_passed"] = False
    with pytest.raises(ValueError, match="verified coverage evidence"):
        _build(review_metadata=review)


def test_report_exposes_latest_retained_evidence_timestamp():
    outputs = _build(events=[_invalid_stop(
        1, ts="2026-07-15T07:01:02.345+00:00", attempts=1
    )])
    assert outputs.report["corpus"]["latest_evidence_utc"] == (
        "2026-07-15T07:01:02.345+00:00"
    )
```

- [ ] **Step 2: Run the tests and confirm RED**

Run:

```powershell
python -m pytest tests/test_recursive_log_learning.py -q
```

Expected: failures because the old flat review contract accepts
`shadow_corpus_passed` and the report has no `latest_evidence_utc`.

- [ ] **Step 3: Implement strict review validation and a default-corpus API**

In `recursive_log_learning.py`:

```python
REVIEW_SCHEMA_VERSION = 1


def review_map(review_metadata: dict | None) -> dict:
    value = review_metadata or {"schema_version": REVIEW_SCHEMA_VERSION,
                                "reviews": {}}
    if value.get("schema_version") != REVIEW_SCHEMA_VERSION:
        raise ValueError("unsupported log review schema_version")
    reviews = value.get("reviews")
    if not isinstance(reviews, dict):
        raise ValueError("log review ledger requires a reviews object")
    return reviews


def source_fingerprint(outputs: LearningOutputs) -> str:
    return _fingerprint(outputs.registry["source_fingerprints"])


def build_default_learning_outputs(
    *, review_metadata: dict | None = None,
) -> LearningOutputs:
    reviews = load_json(DEFAULT_REVIEWS, {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "reviews": {},
    }) if review_metadata is None else review_metadata
    return build_learning_outputs(
        events=load_jsonl(DEFAULT_EVENTS),
        replay_rows=load_jsonl(DEFAULT_REPLAY),
        accounting_rows=load_jsonl(DEFAULT_ACCOUNTING),
        observed_rows=load_jsonl(DEFAULT_OBSERVED),
        provider_catalog=load_json(DEFAULT_PROVIDER),
        strategy_farm=load_json(DEFAULT_STRATEGY_FARM),
        review_metadata=reviews,
    )
```

Change `merge_review_metadata` so `covered` requires `fix_commit`, the exact
test node and all three true verification booleans, plus 64/40-character
fingerprints. Preserve the existing strict `last_seen > covered_after`
regression rule. Compute `latest_evidence_utc` from parseable event and pattern
timestamps and include it under `report["corpus"]`. Make `main()` call the new
default-corpus helper when all source paths are defaults while preserving
custom-path support used by tests and tools.

- [ ] **Step 4: Run detector tests and confirm GREEN**

```powershell
python -m pytest tests/test_recursive_log_learning.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit the detector contract**

```powershell
git add recursive_log_learning.py tests/test_recursive_log_learning.py
git commit -m "learn: require verified pattern coverage evidence"
```

### Task 2: Add The Verified Review Command

**Files:**
- Create: `tests/test_log_pattern_review.py`
- Create: `log_pattern_review.py`
- Create: `tools/review_log_pattern.py`

- [ ] **Step 1: Write failing atomicity and validation tests**

Create tests around a real temporary ledger and injected command runner:

```python
def test_failed_exact_test_leaves_ledger_byte_identical(tmp_path):
    ledger = tmp_path / "reviews.json"
    original = b'{"schema_version":1,"reviews":{}}\n'
    ledger.write_bytes(original)
    verifier = FakeVerifier(exact_test_returncode=1)

    with pytest.raises(review.ReviewError, match="regression test failed"):
        review.cover_pattern(
            pattern_id="execution.invalid_stops.modify_sltp",
            rule_version="pending-actions.invalid-stop-preflight.v1",
            fix_commit="6386be6",
            regression_test=(
                "tests/test_pending_actions.py::TestModifyPreconditions::"
                "test_invalid_stop_waits_without_mt5_submission"
            ),
            reviewer="project_owner",
            ledger_path=ledger,
            verifier=verifier,
            corpus_builder=stable_corpus_builder,
            now_utc=fixed_now,
        )

    assert ledger.read_bytes() == original


def test_successful_cover_records_only_verified_evidence(tmp_path):
    ledger = tmp_path / "reviews.json"
    result = review.cover_pattern(
        pattern_id="execution.invalid_stops.modify_sltp",
        rule_version="pending-actions.invalid-stop-preflight.v1",
        fix_commit="6386be6",
        regression_test=(
            "tests/test_pending_actions.py::TestModifyPreconditions::"
            "test_invalid_stop_waits_without_mt5_submission"
        ),
        reviewer="project_owner",
        ledger_path=ledger,
        verifier=FakeVerifier(),
        corpus_builder=stable_corpus_builder,
        now_utc=fixed_now,
    )
    stored = json.loads(ledger.read_text(encoding="utf-8"))
    row = stored["reviews"][result.pattern_id]
    assert row["verification"]["test_passed"] is True
    assert row["verification"]["full_suite_passed"] is True
    assert row["verification"]["corpus_rebuild_deterministic"] is True
    assert row["covered_after_utc"] == "2026-07-15T10:00:00+00:00"
```

Also cover unknown pattern, unreachable commit, failed collection, failed full
suite, nondeterministic corpus, missing dismissal reason and conflicting
existing review.

- [ ] **Step 2: Run the tests and confirm RED**

```powershell
python -m pytest tests/test_log_pattern_review.py -q
```

Expected: collection error because `log_pattern_review.py` does not exist.

- [ ] **Step 3: Implement the review service and CLI**

`log_pattern_review.py` defines:

```python
class ReviewError(RuntimeError):
    pass


@dataclass(frozen=True)
class VerificationResult:
    resolved_fix_commit: str
    verified_commit: str
    exact_test_command: list[str]
    full_suite_command: list[str]


class RepositoryVerifier:
    def __init__(self, root: Path = ROOT, runner=subprocess.run):
        self.root = root
        self.runner = runner

    def _run(self, command: list[str], timeout: int = 600):
        return self.runner(
            command, cwd=self.root, capture_output=True, text=True,
            check=False, timeout=timeout,
        )

    def _require(self, command: list[str], message: str, timeout: int = 600):
        result = self._run(command, timeout)
        if result.returncode != 0:
            detail = result.stderr or result.stdout or "no command output"
            raise ReviewError(f"{message}: {detail.strip()}")
        return result

    def resolve_ancestor_commit(self, revision: str) -> tuple[str, str]:
        resolved = self._require(
            ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"],
            "fix commit does not exist",
        ).stdout.strip()
        head = self._require(
            ["git", "rev-parse", "HEAD"], "cannot resolve HEAD"
        ).stdout.strip()
        self._require(
            ["git", "merge-base", "--is-ancestor", resolved, head],
            "fix commit is not an ancestor of HEAD",
        )
        return resolved, head

    def collect_test(self, node: str) -> None:
        self._require(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", node],
            "regression test node does not collect",
        )

    def run_exact_test(self, node: str) -> None:
        self._require(
            [sys.executable, "-m", "pytest", "-q", node],
            "regression test failed",
        )

    def run_full_suite(self) -> None:
        self._require(
            [sys.executable, "-m", "pytest", "-q"],
            "complete test suite failed",
            timeout=1800,
        )


@dataclass(frozen=True)
class ReviewDecision:
    pattern_id: str
    status: str
    source_fingerprint: str
```

Implement `cover_pattern` with keyword-only `pattern_id`, `rule_version`,
`fix_commit`, `regression_test`, `reviewer`, `ledger_path`, `verifier`,
`corpus_builder` and `now_utc` parameters. Implement `dismiss_pattern` with
keyword-only `pattern_id`, `reason`, `reviewer`, `ledger_path`,
`corpus_builder` and `now_utc` parameters. Both return `ReviewDecision`.

Build current outputs first to prove the pattern exists. For coverage, verify
Git and pytest, construct the prospective ledger in memory, build it twice and
compare both output byte pairs. Only then write canonical JSON through a
temporary file and `Path.replace`. Reject overwriting a different existing
decision without an explicit future command; this version has no force flag.

`tools/review_log_pattern.py` inserts the repository root into `sys.path`,
defines `cover` and `dismiss` argparse subcommands, calls the service, prints a
short proof summary and returns nonzero on `ReviewError`.

- [ ] **Step 4: Run review tests and confirm GREEN**

```powershell
python -m pytest tests/test_log_pattern_review.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Prove the CLI exposes both operations**

```powershell
python tools/review_log_pattern.py --help
python tools/review_log_pattern.py cover --help
python tools/review_log_pattern.py dismiss --help
```

Expected: exit code zero and required arguments are listed.

- [ ] **Step 6: Commit the review command**

```powershell
git add log_pattern_review.py tools/review_log_pattern.py tests/test_log_pattern_review.py
git commit -m "learn: verify pattern reviews before promotion"
```

### Task 3: Add The Freshness Publication Contract

**Files:**
- Create: `tests/test_log_learning_publication.py`
- Create: `log_learning_publication.py`

- [ ] **Step 1: Write failing success and failure status tests**

```python
def test_success_status_binds_report_registry_sources_and_commit(tmp_path):
    report, registry = write_matching_artifacts(tmp_path)
    status_path = tmp_path / "log_learning_status.json"
    status = publication.publish_status(
        status_path=status_path,
        report_path=report,
        registry_path=registry,
        dependencies={"accounting": True, "observed_ticks": True,
                      "provider_catalog": True, "strategy_farm": True},
        build_returncode=0,
        git_commit="a" * 40,
        git_dirty=False,
        attempted_at_utc="2026-07-15T10:00:00+00:00",
    )
    assert status["ok"] is True
    assert status["fresh"] is True
    assert len(status["publication_id"]) == 64
    assert status["report_sha256"] == sha256(report.read_bytes()).hexdigest()


def test_failed_dependency_publishes_fresh_negative_status(tmp_path):
    report, registry = write_matching_artifacts(tmp_path)
    status = publication.publish_status(
        status_path=tmp_path / "status.json",
        report_path=report,
        registry_path=registry,
        dependencies={"provider_catalog": False},
        build_returncode=0,
        git_commit="a" * 40,
        git_dirty=True,
        attempted_at_utc="2026-07-15T10:00:00+00:00",
    )
    assert status["ok"] is False
    assert status["fresh"] is False
    assert status["blockers"] == ["dependency_failed:provider_catalog"]
    assert status["conclusions_allowed"] is False
```

Add tests for learner failure, missing artifacts, malformed JSON, mismatched
source fingerprints and a report hash that disagrees with
`registry_fingerprint`.

- [ ] **Step 2: Run the tests and confirm RED**

```powershell
python -m pytest tests/test_log_learning_publication.py -q
```

Expected: collection error because the publication module does not exist.

- [ ] **Step 3: Implement atomic status publication**

`log_learning_publication.py` exposes:

```python
STATUS_SCHEMA_VERSION = 1


def publish_status(
    *, status_path: Path, report_path: Path, registry_path: Path,
    dependencies: Mapping[str, bool], build_returncode: int | None,
    git_commit: str, git_dirty: bool, attempted_at_utc: str,
    error: str | None = None,
) -> dict:
    """Write and return an authoritative status for one build attempt."""
```

Canonicalize mappings before hashing. Validate the report and registry JSON,
require matching `source_fingerprints`, require the report's
`registry_fingerprint` to equal the registry byte hash and derive a stable
publication ID from source, artifact and commit fingerprints. Set
`conclusions_allowed` only when every dependency passed, artifacts are valid,
and the report itself says `safe_for_strategy_simulation`. Always write the
status atomically, including failure paths.

- [ ] **Step 4: Run status tests and confirm GREEN**

```powershell
python -m pytest tests/test_log_learning_publication.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit the status contract**

```powershell
git add log_learning_publication.py tests/test_log_learning_publication.py
git commit -m "learn: publish authoritative learning freshness"
```

### Task 4: Make The Watcher Publish Learning Unconditionally

**Files:**
- Modify: `tests/test_run_bot_watch.py`
- Modify: `tools/run_bot_watch.py`

- [ ] **Step 1: Write failing watcher orchestration tests**

Replace the conditional-learning expectation with explicit dependency input:

```python
def test_push_pipeline_runs_learning_after_an_upstream_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(watch, "_clear_mutable_offline_outputs", lambda: None)
    monkeypatch.setattr(watch, "_regenerate_ledger", lambda: False)
    monkeypatch.setattr(
        watch,
        "_regenerate_recursive_learning_outputs",
        lambda dependencies: calls.append(dict(dependencies)) or False,
    )
    monkeypatch.setattr(watch, "_git", clean_fake_git)

    watch._push_session_data()

    assert len(calls) == 1
    assert calls[0]["ledger"] is False
    assert calls[0]["replay"] is False
    assert calls[0]["provider_catalog"] is False
```

Add tests that a successful learning subprocess publishes matching status, a
failed subprocess publishes `build_failed`, `_clear_mutable_offline_outputs`
removes stale status, and staging includes `data/log_learning_status.json` plus
`data/log_pattern_reviews.json`.

- [ ] **Step 2: Run watcher tests and confirm RED**

```powershell
python -m pytest tests/test_run_bot_watch.py -q
```

Expected: failures because learning takes no dependency map and is skipped
when an earlier builder fails.

- [ ] **Step 3: Implement unconditional status-aware publication**

In `tools/run_bot_watch.py`:

```python
LOG_LEARNING_STATUS_FILE = REPO_DIR / "data" / "log_learning_status.json"
LOG_PATTERN_REVIEWS_FILE = REPO_DIR / "data" / "log_pattern_reviews.json"


def _regenerate_recursive_learning_outputs(
    dependencies: dict[str, bool],
) -> bool:
    LOG_LEARNING_REPORT_FILE.unlink(missing_ok=True)
    LOG_PATTERN_REGISTRY_FILE.unlink(missing_ok=True)
    LOG_LEARNING_STATUS_FILE.unlink(missing_ok=True)
    attempted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    returncode = None
    error = None
    try:
        completed = subprocess.run(
            [sys.executable, "recursive_log_learning.py", "--quiet"],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=120,
        )
        returncode = completed.returncode
        error = completed.stderr or completed.stdout or None
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
    finally:
        publication.publish_status(
            status_path=LOG_LEARNING_STATUS_FILE,
            report_path=LOG_LEARNING_REPORT_FILE,
            registry_path=LOG_PATTERN_REGISTRY_FILE,
            dependencies=dependencies,
            build_returncode=returncode,
            git_commit=_local_head(),
            git_dirty=bool(_git("status", "--porcelain").stdout.strip()),
            attempted_at_utc=attempted_at,
            error=error,
        )
    return json.loads(LOG_LEARNING_STATUS_FILE.read_text(
        encoding="utf-8"
    ))["ok"]
```

Refactor `_push_session_data()` to initialize every builder result to `False`,
run causal builders only when their prerequisites pass, and always call the
learning function exactly once at the end with the complete map. Add both new
files to the staging list. Clear stale status but never delete the review
ledger.

- [ ] **Step 4: Run watcher and adjacent tests**

```powershell
python -m pytest tests/test_run_bot_watch.py tests/test_recursive_log_learning.py tests/test_log_learning_publication.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit watcher integration**

```powershell
git add tools/run_bot_watch.py tests/test_run_bot_watch.py
git commit -m "watch: always publish recursive learning status"
```

### Task 5: Backfill Proven Coverage And Regenerate Artifacts

**Files:**
- Create: `data/log_pattern_reviews.json`
- Modify: `data/provider_signal_catalog.json`
- Modify: `data/log_learning_report.json`
- Modify: `data/log_pattern_registry.json`
- Create: `data/log_learning_status.json`

- [ ] **Step 1: Verify the real invalid-stop fix evidence**

```powershell
git show --stat --oneline 6386be6
python -m pytest tests/test_pending_actions.py::TestModifyPreconditions::test_invalid_stop_waits_without_mt5_submission -q
```

Expected: commit `6386be6` exists and the exact regression test passes.

- [ ] **Step 2: Promote the proven pattern through the supported command**

```powershell
python tools/review_log_pattern.py cover `
  execution.invalid_stops.modify_sltp `
  --rule-version pending-actions.invalid-stop-preflight.v1 `
  --fix-commit 6386be6 `
  --test tests/test_pending_actions.py::TestModifyPreconditions::test_invalid_stop_waits_without_mt5_submission `
  --reviewer project_owner
```

Expected: the command runs the exact test, full suite and deterministic corpus
proof, then writes one covered review. If any proof fails, do not hand-edit the
ledger; fix the discovered issue first.

- [ ] **Step 3: Rebuild all default learning artifacts**

```powershell
python provider_signal_catalog.py --quiet
python recursive_log_learning.py --quiet
```

Use a short Python command calling `log_learning_publication.publish_status`
with the actual default paths and every dependency set from its current status
file. Expected: report and registry hashes match the new status. A diagnostic
status is acceptable when replay gates are genuinely blocked; stale or invalid
status is not.

- [ ] **Step 4: Verify deterministic tracked artifacts**

```powershell
python -m pytest `
  tests/test_recursive_log_learning.py `
  tests/test_log_pattern_review.py `
  tests/test_log_learning_publication.py `
  tests/test_run_bot_watch.py -q
python recursive_log_learning.py --quiet
git diff --check
```

Expected: targeted tests pass, a second build creates no new learning-artifact
diff and `git diff --check` emits nothing.

- [ ] **Step 5: Run the complete repository suite**

```powershell
python -m pytest -q
```

Expected: zero failures. Record any intentional skip and do not describe the
feature as complete if a new or relevant test is skipped.

- [ ] **Step 6: Commit generated evidence**

```powershell
git add data/log_pattern_reviews.json data/log_learning_status.json `
  data/log_learning_report.json data/log_pattern_registry.json `
  data/provider_signal_catalog.json
git commit -m "data: publish verified recursive learning state"
```

### Task 6: Final Isolation And Requirement Audit

**Files:**
- Inspect only unless a failing requirement requires a TDD fix.

- [ ] **Step 1: Prove live modules do not import offline learning**

```powershell
rg -n "recursive_log_learning|log_pattern_review|log_learning_publication" `
  main.py listener.py executor.py pending_actions.py monitor.py `
  position_lifecycle_monitor.py
```

Expected: no matches.

- [ ] **Step 2: Inspect branch scope and commits**

```powershell
git status -sb
git log --oneline --decorate origin/main..HEAD
git diff --stat origin/main...HEAD
```

Expected: no unintended live-strategy changes from this closure layer. Any
pre-existing feature-branch simulator commits remain visible and separate.

- [ ] **Step 3: Run final fresh verification**

```powershell
python -m pytest -q
git diff --check
git status -sb
```

Expected: zero failures, no whitespace errors, and only intentionally retained
changes. Do not push; report the exact commits and await explicit user approval.
