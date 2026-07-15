import json
from datetime import datetime, timezone

import pytest

import log_pattern_review as review
import recursive_log_learning as learning


PATTERN_ID = "execution.invalid_stops.modify_sltp"
FIX_COMMIT = "6386be66cc986bdf00c1d0c5e773277cbfa6392e"
VERIFIED_COMMIT = "c" * 40
TEST_NODE = (
    "tests/test_pending_actions.py::TestModifyPreconditions::"
    "test_invalid_stop_waits_without_mt5_submission"
)


def _fixed_now():
    return datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)


def _corpus_builder(review_metadata):
    return learning.build_learning_outputs(
        events=[{
            "ts": "2026-07-13T06:42:46+00:00",
            "sig": "canal2_3099",
            "ev": "mt5_action_failed",
            "kind": "MODIFY_SLTP",
            "last_retcode": 10016,
            "attempts": 1,
        }],
        replay_rows=[],
        accounting_rows=[],
        observed_rows=[],
        provider_catalog={},
        strategy_farm={},
        review_metadata=review_metadata,
    )


class FakeVerifier:
    def __init__(self, fail_at=None):
        self.fail_at = fail_at
        self.calls = []

    def _call(self, name):
        self.calls.append(name)
        if self.fail_at == name:
            raise review.ReviewError({
                "commit": "fix commit does not exist",
                "collect": "regression test node does not collect",
                "exact": "regression test failed",
                "suite": "complete test suite failed",
            }[name])

    def resolve_ancestor_commit(self, revision):
        self._call("commit")
        assert revision == "6386be6"
        return FIX_COMMIT, VERIFIED_COMMIT

    def collect_test(self, node):
        self._call("collect")
        assert node == TEST_NODE

    def run_exact_test(self, node):
        self._call("exact")
        assert node == TEST_NODE

    def run_full_suite(self):
        self._call("suite")


def _cover(tmp_path, **overrides):
    values = {
        "pattern_id": PATTERN_ID,
        "rule_version": "pending-actions.invalid-stop-preflight.v1",
        "fix_commit": "6386be6",
        "regression_test": TEST_NODE,
        "reviewer": "project_owner",
        "ledger_path": tmp_path / "log_pattern_reviews.json",
        "verifier": FakeVerifier(),
        "corpus_builder": _corpus_builder,
        "now_utc": _fixed_now,
    }
    values.update(overrides)
    return review.cover_pattern(**values)


def test_successful_cover_records_only_verified_evidence(tmp_path):
    verifier = FakeVerifier()

    decision = _cover(tmp_path, verifier=verifier)

    stored = json.loads((tmp_path / "log_pattern_reviews.json").read_text(
        encoding="utf-8",
    ))
    row = stored["reviews"][PATTERN_ID]
    assert decision.pattern_id == PATTERN_ID
    assert decision.status == "covered"
    assert row["fix_commit"] == FIX_COMMIT
    assert row["covered_after_utc"] == "2026-07-15T10:00:00+00:00"
    assert row["verification"] == {
        "test_passed": True,
        "full_suite_passed": True,
        "corpus_rebuild_deterministic": True,
        "source_fingerprint": decision.source_fingerprint,
        "verified_commit": VERIFIED_COMMIT,
    }
    assert len(decision.source_fingerprint) == 64
    assert verifier.calls == ["commit", "collect", "exact", "suite"]


@pytest.mark.parametrize(
    ("fail_at", "message"),
    [
        ("commit", "fix commit does not exist"),
        ("collect", "test node does not collect"),
        ("exact", "regression test failed"),
        ("suite", "complete test suite failed"),
    ],
)
def test_failed_external_proof_leaves_ledger_byte_identical(
    tmp_path, fail_at, message,
):
    ledger = tmp_path / "log_pattern_reviews.json"
    original = b'{"schema_version":1,"reviews":{}}\n'
    ledger.write_bytes(original)

    with pytest.raises(review.ReviewError, match=message):
        _cover(tmp_path, verifier=FakeVerifier(fail_at=fail_at))

    assert ledger.read_bytes() == original


def test_unknown_pattern_is_rejected_before_repository_commands(tmp_path):
    verifier = FakeVerifier()

    with pytest.raises(review.ReviewError, match="pattern does not exist"):
        _cover(tmp_path, pattern_id="execution.not_in_corpus", verifier=verifier)

    assert verifier.calls == []
    assert not (tmp_path / "log_pattern_reviews.json").exists()


def test_nondeterministic_corpus_leaves_ledger_unchanged(tmp_path):
    ledger = tmp_path / "log_pattern_reviews.json"
    original = b'{"schema_version":1,"reviews":{}}\n'
    ledger.write_bytes(original)
    calls = 0

    def unstable_builder(review_metadata):
        nonlocal calls
        calls += 1
        outputs = _corpus_builder(review_metadata)
        if calls == 3:
            return learning.LearningOutputs(
                report=outputs.report,
                registry=outputs.registry,
                report_bytes=outputs.report_bytes + b"changed",
                registry_bytes=outputs.registry_bytes,
            )
        return outputs

    with pytest.raises(review.ReviewError, match="not deterministic"):
        _cover(tmp_path, corpus_builder=unstable_builder)

    assert ledger.read_bytes() == original


def test_existing_different_decision_requires_explicit_review(tmp_path):
    ledger = tmp_path / "log_pattern_reviews.json"
    ledger.write_text(json.dumps({
        "schema_version": 1,
        "reviews": {PATTERN_ID: {
            "status": "dismissed",
            "dismissal_reason": "provider announcement",
            "reviewed_by": "project_owner",
            "reviewed_at_utc": "2026-07-14T10:00:00+00:00",
            "source_fingerprint": "d" * 64,
        }},
    }), encoding="utf-8")

    with pytest.raises(review.ReviewError, match="already has a review"):
        _cover(tmp_path)


def test_dismissal_requires_reason_and_records_corpus_fingerprint(tmp_path):
    ledger = tmp_path / "log_pattern_reviews.json"

    with pytest.raises(review.ReviewError, match="dismissal reason"):
        review.dismiss_pattern(
            pattern_id=PATTERN_ID,
            reason=" ",
            reviewer="project_owner",
            ledger_path=ledger,
            corpus_builder=_corpus_builder,
            now_utc=_fixed_now,
        )

    decision = review.dismiss_pattern(
        pattern_id=PATTERN_ID,
        reason="One historical broker outage; no code rule is warranted.",
        reviewer="project_owner",
        ledger_path=ledger,
        corpus_builder=_corpus_builder,
        now_utc=_fixed_now,
    )

    stored = json.loads(ledger.read_text(encoding="utf-8"))
    row = stored["reviews"][PATTERN_ID]
    assert decision.status == "dismissed"
    assert row["dismissal_reason"].startswith("One historical")
    assert row["source_fingerprint"] == decision.source_fingerprint
    assert len(decision.source_fingerprint) == 64
