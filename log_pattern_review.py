"""Verified human review workflow for offline reliability patterns."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import recursive_log_learning as learning


ROOT = Path(__file__).parent
DEFAULT_LEDGER = learning.DEFAULT_REVIEWS
LEDGER_SCHEMA_VERSION = learning.REVIEW_SCHEMA_VERSION


class ReviewError(RuntimeError):
    """Raised when a review cannot be proven without mutating the ledger."""


@dataclass(frozen=True)
class ReviewDecision:
    pattern_id: str
    status: str
    source_fingerprint: str


class RepositoryVerifier:
    def __init__(
        self,
        root: Path = ROOT,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ) -> None:
        self.root = root
        self.runner = runner

    def _run(
        self,
        command: list[str],
        *,
        timeout: int = 600,
    ) -> subprocess.CompletedProcess:
        try:
            return self.runner(
                command,
                cwd=self.root,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ReviewError(
                f"command could not run: {' '.join(command)}: {exc}"
            ) from exc

    def _require(
        self,
        command: list[str],
        message: str,
        *,
        timeout: int = 600,
    ) -> subprocess.CompletedProcess:
        result = self._run(command, timeout=timeout)
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
            ["git", "rev-parse", "HEAD"],
            "cannot resolve HEAD",
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


def _empty_ledger() -> dict:
    return {"schema_version": LEDGER_SCHEMA_VERSION, "reviews": {}}


def load_review_ledger(path: Path) -> dict:
    if not path.exists():
        return _empty_ledger()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewError(f"cannot read review ledger {path}: {exc}") from exc
    try:
        learning.review_map(value)
    except ValueError as exc:
        raise ReviewError(f"invalid review ledger {path}: {exc}") from exc
    return value


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _atomic_write_ledger(path: Path, ledger: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_canonical_bytes(ledger))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _timestamp(now_utc: Callable[[], datetime] | None) -> str:
    current = (now_utc or (lambda: datetime.now(timezone.utc)))()
    if current.tzinfo is None:
        raise ReviewError("review clock must return a timezone-aware datetime")
    return current.astimezone(timezone.utc).isoformat(timespec="seconds")


def _build_corpus(
    builder: Callable[[dict], learning.LearningOutputs],
    ledger: dict,
) -> learning.LearningOutputs:
    try:
        return builder(copy.deepcopy(ledger))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ReviewError(f"learning corpus could not be rebuilt: {exc}") from exc


def _require_pattern(
    outputs: learning.LearningOutputs,
    pattern_id: str,
) -> dict:
    pattern = next(
        (
            row for row in outputs.registry.get("patterns", [])
            if row.get("pattern_id") == pattern_id
        ),
        None,
    )
    if pattern is None:
        raise ReviewError(f"pattern does not exist in current corpus: {pattern_id}")
    return pattern


def _require_new_decision(ledger: dict, pattern_id: str) -> None:
    if pattern_id in learning.review_map(ledger):
        raise ReviewError(
            f"pattern already has a review; explicit replacement is required: "
            f"{pattern_id}"
        )


def cover_pattern(
    *,
    pattern_id: str,
    rule_version: str,
    fix_commit: str,
    regression_test: str,
    reviewer: str,
    ledger_path: Path = DEFAULT_LEDGER,
    verifier: RepositoryVerifier | None = None,
    corpus_builder: Callable[[dict], learning.LearningOutputs] | None = None,
    now_utc: Callable[[], datetime] | None = None,
) -> ReviewDecision:
    if not all(value.strip() for value in (
        pattern_id, rule_version, fix_commit, regression_test, reviewer,
    )):
        raise ReviewError("coverage requires pattern, rule, commit, test and reviewer")

    ledger = load_review_ledger(ledger_path)
    _require_new_decision(ledger, pattern_id)
    builder = corpus_builder or (
        lambda reviews: learning.build_default_learning_outputs(
            review_metadata=reviews,
        )
    )
    current_outputs = _build_corpus(builder, ledger)
    _require_pattern(current_outputs, pattern_id)
    evidence_fingerprint = learning.source_fingerprint(current_outputs)

    repository = verifier or RepositoryVerifier()
    resolved_fix_commit, verified_commit = (
        repository.resolve_ancestor_commit(fix_commit)
    )
    repository.collect_test(regression_test)
    repository.run_exact_test(regression_test)
    repository.run_full_suite()

    reviewed_at = _timestamp(now_utc)
    prospective = copy.deepcopy(ledger)
    prospective["reviews"][pattern_id] = {
        "status": "covered",
        "rule_version": rule_version.strip(),
        "fix_commit": resolved_fix_commit,
        "regression_test": regression_test.strip(),
        "reviewed_by": reviewer.strip(),
        "reviewed_at_utc": reviewed_at,
        "covered_after_utc": reviewed_at,
        "verification": {
            "test_passed": True,
            "full_suite_passed": True,
            "corpus_rebuild_deterministic": True,
            "source_fingerprint": evidence_fingerprint,
            "verified_commit": verified_commit,
        },
    }

    first = _build_corpus(builder, prospective)
    second = _build_corpus(builder, prospective)
    if (
        first.report_bytes != second.report_bytes
        or first.registry_bytes != second.registry_bytes
    ):
        raise ReviewError("whole-corpus learning rebuild is not deterministic")
    if (
        learning.source_fingerprint(first) != evidence_fingerprint
        or learning.source_fingerprint(second) != evidence_fingerprint
    ):
        raise ReviewError("evidence corpus changed during review verification")
    promoted = _require_pattern(first, pattern_id)
    if promoted.get("status") != "covered":
        raise ReviewError(
            f"verified review did not produce covered status: "
            f"{promoted.get('status')}"
        )

    _atomic_write_ledger(ledger_path, prospective)
    return ReviewDecision(
        pattern_id=pattern_id,
        status="covered",
        source_fingerprint=evidence_fingerprint,
    )


def dismiss_pattern(
    *,
    pattern_id: str,
    reason: str,
    reviewer: str,
    ledger_path: Path = DEFAULT_LEDGER,
    corpus_builder: Callable[[dict], learning.LearningOutputs] | None = None,
    now_utc: Callable[[], datetime] | None = None,
) -> ReviewDecision:
    if not reason.strip():
        raise ReviewError("dismissal reason is required")
    if not pattern_id.strip() or not reviewer.strip():
        raise ReviewError("dismissal requires pattern and reviewer")

    ledger = load_review_ledger(ledger_path)
    _require_new_decision(ledger, pattern_id)
    builder = corpus_builder or (
        lambda reviews: learning.build_default_learning_outputs(
            review_metadata=reviews,
        )
    )
    current_outputs = _build_corpus(builder, ledger)
    _require_pattern(current_outputs, pattern_id)
    evidence_fingerprint = learning.source_fingerprint(current_outputs)
    reviewed_at = _timestamp(now_utc)

    prospective = copy.deepcopy(ledger)
    prospective["reviews"][pattern_id] = {
        "status": "dismissed",
        "dismissal_reason": reason.strip(),
        "reviewed_by": reviewer.strip(),
        "reviewed_at_utc": reviewed_at,
        "source_fingerprint": evidence_fingerprint,
    }
    rebuilt = _build_corpus(builder, prospective)
    dismissed = _require_pattern(rebuilt, pattern_id)
    if dismissed.get("status") != "dismissed":
        raise ReviewError("review did not produce dismissed status")
    if learning.source_fingerprint(rebuilt) != evidence_fingerprint:
        raise ReviewError("evidence corpus changed during dismissal review")

    _atomic_write_ledger(ledger_path, prospective)
    return ReviewDecision(
        pattern_id=pattern_id,
        status="dismissed",
        source_fingerprint=evidence_fingerprint,
    )
