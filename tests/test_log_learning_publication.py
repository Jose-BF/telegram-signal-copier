import json
from hashlib import sha256

import pytest

import log_learning_publication as publication


def _canonical_bytes(value):
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _write_matching_artifacts(tmp_path, *, safe=True):
    source_fingerprints = {
        "events": "a" * 64,
        "replay": "b" * 64,
        "accounting": "c" * 64,
        "observed_ticks": "d" * 64,
        "provider_catalog": "e" * 64,
        "strategy_farm": "f" * 64,
        "review_metadata": "1" * 64,
    }
    registry = {
        "schema_version": 1,
        "source_fingerprints": source_fingerprints,
        "summary": {"patterns": 0},
        "patterns": [],
    }
    registry_bytes = _canonical_bytes(registry)
    report = {
        "schema_version": 1,
        "mode": "verified_simulation" if safe else "diagnostic_only",
        "safe_for_strategy_simulation": safe,
        "hard_gate_blockers": [] if safe else ["market_replay"],
        "corpus": {
            "latest_evidence_utc": "2026-07-15T09:59:00+00:00",
            "source_fingerprints": source_fingerprints,
        },
        "registry_fingerprint": sha256(registry_bytes).hexdigest(),
    }
    report_path = tmp_path / "log_learning_report.json"
    registry_path = tmp_path / "log_pattern_registry.json"
    report_path.write_bytes(_canonical_bytes(report))
    registry_path.write_bytes(registry_bytes)
    (tmp_path / "expected-log-learning-report.json").write_bytes(
        report_path.read_bytes()
    )
    (tmp_path / "expected-log-pattern-registry.json").write_bytes(
        registry_path.read_bytes()
    )
    return report_path, registry_path


@pytest.fixture(autouse=True)
def _verified_repository_state(monkeypatch):
    monkeypatch.setattr(
        publication,
        "_read_repository_state",
        lambda root: {
            "git_commit": "9" * 40,
            "git_dirty": False,
            "source_dirty": False,
        },
    )
    monkeypatch.setattr(
        publication,
        "_expected_learning_bytes",
        lambda root: (
            (root / "expected-log-learning-report.json").read_bytes(),
            (root / "expected-log-pattern-registry.json").read_bytes(),
        ),
    )


def _publish(tmp_path, report, registry, **overrides):
    values = {
        "status_path": tmp_path / "log_learning_status.json",
        "report_path": report,
        "registry_path": registry,
        "repo_root": tmp_path,
        "dependencies": {
            "accounting": True,
            "observed_ticks": True,
            "provider_catalog": True,
            "strategy_farm": True,
        },
        "build_returncode": 0,
        "attempted_at_utc": "2026-07-15T10:00:00+00:00",
    }
    values.update(overrides)
    return publication.publish_status(**values)


def test_success_status_binds_report_registry_sources_and_commit(tmp_path):
    report, registry = _write_matching_artifacts(tmp_path)

    status = _publish(tmp_path, report, registry)

    assert status["ok"] is True
    assert status["fresh"] is True
    assert status["artifacts_valid"] is True
    assert status["conclusions_allowed"] is True
    assert len(status["publication_id"]) == 64
    assert status["report_sha256"] == sha256(report.read_bytes()).hexdigest()
    assert status["registry_sha256"] == sha256(registry.read_bytes()).hexdigest()
    assert status["latest_evidence_utc"] == "2026-07-15T09:59:00+00:00"
    assert status["blockers"] == []


def test_failed_dependency_publishes_current_negative_status(tmp_path):
    report, registry = _write_matching_artifacts(tmp_path)

    status = _publish(
        tmp_path,
        report,
        registry,
        dependencies={"provider_catalog": False, "accounting": True},
    )

    assert status["ok"] is False
    assert status["fresh"] is False
    assert status["artifacts_valid"] is True
    assert status["blockers"] == ["dependency_failed:provider_catalog"]
    assert status["conclusions_allowed"] is False
    assert len(status["publication_id"]) == 64


def test_diagnostic_report_can_be_fresh_without_authorizing_conclusions(tmp_path):
    report, registry = _write_matching_artifacts(tmp_path, safe=False)

    status = _publish(tmp_path, report, registry)

    assert status["ok"] is True
    assert status["fresh"] is True
    assert status["conclusions_allowed"] is False
    assert status["strategy_blockers"] == ["market_replay"]


def test_failed_learning_build_writes_status_without_artifacts(tmp_path):
    status_path = tmp_path / "log_learning_status.json"

    status = publication.publish_status(
        status_path=status_path,
        report_path=tmp_path / "missing-report.json",
        registry_path=tmp_path / "missing-registry.json",
        dependencies={"provider_catalog": True},
        build_returncode=1,
        repo_root=tmp_path,
        attempted_at_utc="2026-07-15T10:00:00+00:00",
        error="learner crashed",
    )

    assert status["ok"] is False
    assert status["fresh"] is False
    assert status["artifacts_valid"] is False
    assert status["publication_id"] is None
    assert "learning_build_failed:1" in status["blockers"]
    assert json.loads(status_path.read_text(encoding="utf-8")) == status


def test_malformed_or_mismatched_artifacts_fail_closed(tmp_path):
    report, registry = _write_matching_artifacts(tmp_path)
    report.write_text("{broken", encoding="utf-8")

    malformed = _publish(tmp_path, report, registry)

    assert malformed["ok"] is False
    assert malformed["publication_id"] is None
    assert any(item.startswith("artifacts_invalid:") for item in malformed["blockers"])

    report, registry = _write_matching_artifacts(tmp_path)
    registry_payload = json.loads(registry.read_text(encoding="utf-8"))
    registry_payload["source_fingerprints"]["events"] = "8" * 64
    registry.write_bytes(_canonical_bytes(registry_payload))

    mismatched = _publish(tmp_path, report, registry)

    assert mismatched["ok"] is False
    assert any("source fingerprints differ" in item for item in mismatched["blockers"])


def test_publication_id_is_stable_across_attempt_timestamps(tmp_path):
    report, registry = _write_matching_artifacts(tmp_path)
    first = _publish(tmp_path, report, registry)
    second = _publish(
        tmp_path,
        report,
        registry,
        attempted_at_utc="2026-07-15T11:00:00+00:00",
    )

    assert first["publication_id"] == second["publication_id"]
    assert first["attempted_at_utc"] != second["attempted_at_utc"]


def test_internally_consistent_stale_report_fails_current_corpus_check(tmp_path):
    report, registry = _write_matching_artifacts(tmp_path)
    stale = json.loads(report.read_text(encoding="utf-8"))
    stale["stale_but_internally_consistent"] = True
    report.write_bytes(_canonical_bytes(stale))

    status = _publish(tmp_path, report, registry)

    assert status["ok"] is False
    assert status["fresh"] is False
    assert any("current repository corpus" in item for item in status["blockers"])


def test_uncommitted_source_changes_block_freshness(
    tmp_path, monkeypatch,
):
    report, registry = _write_matching_artifacts(tmp_path)
    monkeypatch.setattr(
        publication,
        "_read_repository_state",
        lambda root: {
            "git_commit": "9" * 40,
            "git_dirty": True,
            "source_dirty": True,
        },
    )

    status = _publish(tmp_path, report, registry)

    assert status["ok"] is False
    assert status["fresh"] is False
    assert status["source_dirty"] is True
    assert "uncommitted_source_changes" in status["blockers"]
    assert status["conclusions_allowed"] is False
