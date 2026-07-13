import copy
import hashlib
import importlib
import json
from pathlib import Path

import pytest


def _provenance():
    return importlib.import_module("simulation_run_provenance")


def _report(generated_at="2026-07-13T10:00:00+00:00"):
    return {
        "schema_version": 1,
        "generated_at": generated_at,
        "from_date": "2026-07-06",
        "to_date": None,
        "executed_trade_count": 2,
        "policy_count": 1,
        "includes_trade_details": False,
        "canonical_scope": {"provider_signals": 2},
        "selection": {
            "selected_policy": None,
            "global_blockers": ["oos_not_validated"],
        },
        "policies": [{
            "policy_id": "follow_actual",
            "metrics": {"net_pnl": 1.0},
        }],
    }


def _evidence_args(root: Path, branch="main"):
    root.mkdir(parents=True, exist_ok=True)
    input_dir = root / "inputs"
    source_dir = root / "source"
    input_dir.mkdir()
    source_dir.mkdir()
    replay = input_dir / "replay.jsonl"
    baseline = input_dir / "baseline.jsonl"
    catalog = input_dir / "catalog.json"
    engine = source_dir / "engine.py"
    replay.write_text(
        '{"sig_id":"canal1_2"}\n{"sig_id":"canal1_1"}\n',
        encoding="utf-8",
    )
    baseline.write_text(
        '{"sig_id":"canal1_1","status":"exact"}\n',
        encoding="utf-8",
    )
    catalog.write_text('{"signals":[]}\n', encoding="utf-8")
    engine.write_text("ENGINE_VERSION = 1\n", encoding="utf-8")
    return {
        "repo_dir": root,
        "report": _report(),
        "parameters": {"from_date": "2026-07-06", "to_date": None},
        "selected_payloads": {
            "replay_trades": [
                {"sig_id": "canal1_2"},
                {"sig_id": "canal1_1"},
            ],
            "effective_baselines": [
                {"sig_id": "canal1_2", "baseline": None},
                {
                    "sig_id": "canal1_1",
                    "baseline": {"status": "exact"},
                },
            ],
        },
        "policies": [{
            "policy_id": "follow_actual",
            "mode": "follow_actual",
        }],
        "input_files": {
            "replay_trades": replay,
            "observed_baseline": baseline,
            "provider_catalog": catalog,
        },
        "source_files": {"engine": engine},
        "required_tick_days": ["2026-07-06"],
        "tick_contracts": {
            "2026-07-06": {
                "day": "2026-07-06",
                "tick_time_contract": "mt5_utc_v2",
                "time_basis": "UTC",
                "parquet_sha256": "a" * 64,
                "size_bytes": 123,
            }
        },
        "runtime": {
            "python": "3.14.2",
            "packages": {
                "pandas": "3.0.2",
                "numpy": "2.4.4",
                "pyarrow": "23.0.1",
            },
        },
        "git": {
            "commit": "1" * 40,
            "branch": branch,
            "dirty": False,
            "errors": [],
        },
    }


def test_run_identity_ignores_paths_mapping_order_branch_and_timestamp(tmp_path):
    provenance = _provenance()
    left = _evidence_args(tmp_path / "left", branch="main")
    right = _evidence_args(tmp_path / "right", branch="feature/replay")
    right["parameters"] = {"to_date": None, "from_date": "2026-07-06"}
    right["report"]["generated_at"] = "2026-07-13T11:00:00+00:00"

    card_left = provenance.build_run_evidence(**left)
    card_right = provenance.build_run_evidence(**right)

    assert card_left["run_fingerprint"] == card_right["run_fingerprint"]
    assert card_left["result_fingerprint"] == card_right["result_fingerprint"]
    assert card_left["reproducibility"]["git"]["branch"] == "main"
    assert card_right["reproducibility"]["git"]["branch"] == "feature/replay"


def test_replay_order_is_part_of_run_identity(tmp_path):
    provenance = _provenance()
    args = _evidence_args(tmp_path)
    first = provenance.build_run_evidence(**args)
    args["selected_payloads"]["replay_trades"].reverse()

    second = provenance.build_run_evidence(**args)

    assert first["run_fingerprint"] != second["run_fingerprint"]


def test_runtime_policy_source_and_tick_changes_change_run_identity(tmp_path):
    provenance = _provenance()
    roots = [tmp_path / name for name in ("runtime", "policy", "source", "tick")]
    cases = [_evidence_args(root) for root in roots]

    original = provenance.build_run_evidence(**cases[0])["run_fingerprint"]
    cases[0]["runtime"]["packages"]["pandas"] = "3.0.3"
    assert provenance.build_run_evidence(**cases[0])["run_fingerprint"] != original

    original = provenance.build_run_evidence(**cases[1])["run_fingerprint"]
    cases[1]["policies"][0]["mode"] = "risk_free_allocation"
    assert provenance.build_run_evidence(**cases[1])["run_fingerprint"] != original

    original = provenance.build_run_evidence(**cases[2])["run_fingerprint"]
    cases[2]["source_files"]["engine"].write_text(
        "ENGINE_VERSION = 2\n",
        encoding="utf-8",
    )
    assert provenance.build_run_evidence(**cases[2])["run_fingerprint"] != original

    original = provenance.build_run_evidence(**cases[3])["run_fingerprint"]
    cases[3]["tick_contracts"]["2026-07-06"]["parquet_sha256"] = "b" * 64
    assert provenance.build_run_evidence(**cases[3])["run_fingerprint"] != original


def test_result_fingerprint_preserves_semantic_policy_order():
    provenance = _provenance()
    report_a = _report(generated_at="2026-07-13T10:00:00+00:00")
    report_b = copy.deepcopy(report_a)
    report_b["generated_at"] = "2026-07-13T11:00:00+00:00"

    assert provenance.result_fingerprint(report_a) == provenance.result_fingerprint(report_b)

    report_b["policies"].append({
        "policy_id": "no_be",
        "metrics": {"net_pnl": 1.0},
    })
    assert provenance.result_fingerprint(report_a) != provenance.result_fingerprint(report_b)


def test_missing_input_or_tick_contract_marks_evidence_incomplete(tmp_path):
    provenance = _provenance()
    args = _evidence_args(tmp_path)
    args["input_files"]["provider_catalog"].unlink()
    args["tick_contracts"] = {}

    card = provenance.build_run_evidence(**args)

    assert card["reproducibility"]["verified_now"] is False
    assert card["reproducibility"]["errors"] == [
        "missing_input:provider_catalog",
        "unverified_tick_contract:2026-07-06",
    ]


def test_full_input_files_are_diagnostics_not_machine_specific_identity(tmp_path):
    provenance = _provenance()
    args = _evidence_args(tmp_path)

    card = provenance.build_run_evidence(**args)

    records = {
        row["role"]: row
        for row in card["reproducibility"]["input_artifacts"]
    }
    assert set(records) == {
        "observed_baseline",
        "provider_catalog",
        "replay_trades",
    }
    assert records["replay_trades"]["path"] == "inputs/replay.jsonl"
    assert len(records["replay_trades"]["sha256"]) == 64


def test_card_never_captures_environment_or_secret_values(tmp_path, monkeypatch):
    provenance = _provenance()
    monkeypatch.setenv("GEMINI_API_KEY", "must-not-appear")

    card = provenance.build_run_evidence(**_evidence_args(tmp_path))
    payload = json.dumps(card)

    assert "must-not-appear" not in payload
    assert "GEMINI_API_KEY" not in payload


def _complete_evidence(root: Path):
    provenance = _provenance()
    args = _evidence_args(root)
    report = args["report"]
    return provenance.build_run_evidence(**args), report


def _publish_args(root: Path, evidence: dict, report: dict, *, include_trades=False):
    return {
        "report": report,
        "evidence": evidence,
        "archive_root": root / "runs",
        "output_path": root / "strategy_farm.json",
        "include_trades": include_trades,
        "repo_dir": root,
    }


def test_compact_run_is_published_once_and_idempotent(tmp_path):
    provenance = _provenance()
    evidence, report = _complete_evidence(tmp_path)

    first = provenance.publish_run_archive(
        **_publish_args(tmp_path, evidence, report),
    )
    repeated_report = {**report, "generated_at": "later"}
    second = provenance.publish_run_archive(
        **_publish_args(tmp_path, evidence, repeated_report),
    )

    assert first.run_dir == second.run_dir
    assert first.idempotent is False
    assert second.idempotent is True
    assert len(list((tmp_path / "runs").glob("[0-9a-f]*"))) == 1
    assert (first.run_dir / "run_card.json").is_file()
    assert (first.run_dir / "strategy_farm.json").is_file()


def test_same_identity_with_different_result_fails_closed(tmp_path):
    provenance = _provenance()
    evidence, report = _complete_evidence(tmp_path)
    provenance.publish_run_archive(**_publish_args(tmp_path, evidence, report))
    changed = copy.deepcopy(evidence)
    changed["result_fingerprint"] = "f" * 64

    with pytest.raises(provenance.ProvenanceConflictError):
        provenance.publish_run_archive(
            **_publish_args(tmp_path, changed, report),
        )


def test_corrupt_retained_artifact_fails_closed(tmp_path):
    provenance = _provenance()
    evidence, report = _complete_evidence(tmp_path)
    first = provenance.publish_run_archive(
        **_publish_args(tmp_path, evidence, report),
    )
    (first.run_dir / "strategy_farm.json").write_text(
        "corrupt\n",
        encoding="utf-8",
    )

    with pytest.raises(provenance.ProvenanceConflictError):
        provenance.publish_run_archive(
            **_publish_args(tmp_path, evidence, report),
        )


def test_incomplete_evidence_marks_latest_report_without_archive(tmp_path):
    provenance = _provenance()
    evidence, report = _complete_evidence(tmp_path)
    evidence["reproducibility"]["verified_now"] = False
    evidence["reproducibility"]["errors"] = [
        "unverified_tick_contract:2026-07-06",
    ]

    result = provenance.publish_run_archive(
        **_publish_args(tmp_path, evidence, report),
    )

    assert result.status == "incomplete"
    assert result.run_dir is None
    assert result.report["provenance"]["status"] == "incomplete"
    assert not (tmp_path / "runs").exists()


def test_detailed_result_is_referenced_but_not_copied(tmp_path):
    provenance = _provenance()
    args = _evidence_args(tmp_path)
    report = args["report"]
    report["includes_trade_details"] = True
    evidence = provenance.build_run_evidence(**args)

    result = provenance.publish_run_archive(
        **_publish_args(tmp_path, evidence, report, include_trades=True),
    )

    card = json.loads((result.run_dir / "run_card.json").read_text())
    assert not (result.run_dir / "strategy_farm.json").exists()
    assert card["artifacts"][0]["retained"] is False


def test_idempotence_ignores_git_and_unselected_file_diagnostics(tmp_path):
    provenance = _provenance()
    args = _evidence_args(tmp_path)
    first_evidence = provenance.build_run_evidence(**args)
    first = provenance.publish_run_archive(
        **_publish_args(tmp_path, first_evidence, args["report"]),
    )
    args["git"] = {
        "commit": "2" * 40,
        "branch": "main-after-data-commit",
        "dirty": True,
        "errors": [],
    }
    with args["input_files"]["replay_trades"].open(
        "a",
        encoding="utf-8",
    ) as handle:
        handle.write('{"sig_id":"outside_selected_window"}\n')
    second_evidence = provenance.build_run_evidence(**args)

    second = provenance.publish_run_archive(
        **_publish_args(tmp_path, second_evidence, args["report"]),
    )

    assert second_evidence["run_fingerprint"] == first_evidence["run_fingerprint"]
    assert second.run_dir == first.run_dir
    assert second.idempotent is True


def test_existing_card_rejects_tampered_computational_identity(tmp_path):
    provenance = _provenance()
    evidence, report = _complete_evidence(tmp_path)
    published = provenance.publish_run_archive(
        **_publish_args(tmp_path, evidence, report),
    )
    card_path = published.run_dir / "run_card.json"
    card = json.loads(card_path.read_text(encoding="utf-8"))
    card["reproducibility"]["parameters"]["from_date"] = "2026-07-07"
    card_path.write_text(json.dumps(card), encoding="utf-8")

    with pytest.raises(provenance.ProvenanceConflictError):
        provenance.publish_run_archive(
            **_publish_args(tmp_path, evidence, report),
        )


def test_detailed_idempotent_report_matches_first_published_artifact(tmp_path):
    provenance = _provenance()
    args = _evidence_args(tmp_path)
    args["report"]["includes_trade_details"] = True
    first_evidence = provenance.build_run_evidence(**args)
    first = provenance.publish_run_archive(
        **_publish_args(
            tmp_path,
            first_evidence,
            args["report"],
            include_trades=True,
        ),
    )
    args["report"]["generated_at"] = "2026-07-13T12:00:00+00:00"
    second_evidence = provenance.build_run_evidence(**args)

    second = provenance.publish_run_archive(
        **_publish_args(
            tmp_path,
            second_evidence,
            args["report"],
            include_trades=True,
        ),
    )
    card = json.loads((first.run_dir / "run_card.json").read_text())
    expected_hash = card["artifacts"][0]["sha256"]

    assert second.idempotent is True
    assert hashlib.sha256(
        provenance.pretty_json_bytes(second.report)
    ).hexdigest() == expected_hash


def test_malformed_card_identity_fails_with_controlled_conflict(tmp_path):
    provenance = _provenance()
    evidence, report = _complete_evidence(tmp_path)
    published = provenance.publish_run_archive(
        **_publish_args(tmp_path, evidence, report),
    )
    card_path = published.run_dir / "run_card.json"
    card = json.loads(card_path.read_text(encoding="utf-8"))
    card["reproducibility"]["source_files"] = [{}]
    card_path.write_text(json.dumps(card), encoding="utf-8")

    with pytest.raises(provenance.ProvenanceConflictError):
        provenance.publish_run_archive(
            **_publish_args(tmp_path, evidence, report),
        )


def test_malformed_detailed_artifact_fails_with_controlled_conflict(tmp_path):
    provenance = _provenance()
    args = _evidence_args(tmp_path)
    args["report"]["includes_trade_details"] = True
    evidence = provenance.build_run_evidence(**args)
    published = provenance.publish_run_archive(
        **_publish_args(
            tmp_path,
            evidence,
            args["report"],
            include_trades=True,
        ),
    )
    card_path = published.run_dir / "run_card.json"
    card = json.loads(card_path.read_text(encoding="utf-8"))
    card["artifacts"][0]["size_bytes"] = "not-an-integer"
    card_path.write_text(json.dumps(card), encoding="utf-8")

    with pytest.raises(provenance.ProvenanceConflictError):
        provenance.publish_run_archive(
            **_publish_args(
                tmp_path,
                evidence,
                args["report"],
                include_trades=True,
            ),
        )
