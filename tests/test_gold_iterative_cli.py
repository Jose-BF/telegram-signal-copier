from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from dataclasses import asdict
from research.dubai_iterative.contracts import SearchSpace
from research.dubai_iterative.engine import ExecutionAssumptions

import research.gold_iterative.__main__ as gold_cli
from research.gold_iterative.__main__ import _parser, main
from tests.test_search_execution_profiles import (
    fixed_control_dataset, integrated_execution, own_rules_config,
)


def test_gold_profile_is_opt_in_and_loads_nested_json_without_legacy_overrides(tmp_path):
    execution = integrated_execution()
    config = tmp_path / "execution.json"
    config.write_text(json.dumps(asdict(execution)), encoding="utf-8")
    assert gold_cli._execution_from_args(_parser().parse_args(["search"])) == ExecutionAssumptions()
    args = _parser().parse_args(["search", "--execution-profile", str(config)])
    assert gold_cli._execution_from_args(args) == execution
    conflicting = _parser().parse_args([
        "search", "--execution-profile", str(config), "--search-latency-ms", "0",
    ])
    with pytest.raises(ValueError, match="cannot be combined"):
        gold_cli._execution_from_args(conflicting)


def test_gold_profile_rejects_legacy_anchors_before_dataset_or_output(tmp_path, monkeypatch, capsys):
    config = tmp_path / "execution.json"
    config.write_text(json.dumps(asdict(integrated_execution())), encoding="utf-8")
    output = tmp_path / "must-not-exist"
    def expensive(_args):
        pytest.fail("incompatible profile must fail before dataset loading")
    monkeypatch.setattr(gold_cli, "_load_dataset", expensive)
    assert main(["search", "--execution-profile", str(config), "--output-root", str(output)]) == 2
    message = capsys.readouterr().out
    assert "--own-rules-config is required" in message
    assert "no seeds or mutations were dropped" in message
    assert not output.exists()


@pytest.mark.parametrize("payload, expected", [
    ('{"latency_ms":0,"latency_ms":1}', "duplicate"),
    ('{"entry_slippage":NaN}', "nonfinite"),
    ('{"unknown":1}', "unknown"),
    ('{"entry_fill_latency_ms":1.5}', "integer"),
    ('{"market":{}}', "required"),
])
def test_gold_profile_json_fails_closed_before_loading_data(tmp_path, monkeypatch, capsys, payload, expected):
    config = tmp_path / "bad.json"
    config.write_text(payload, encoding="utf-8")
    monkeypatch.setattr(gold_cli, "_load_dataset", lambda args: pytest.fail("unexpected data access"))
    assert main(["search", "--execution-profile", str(config)]) == 2
    assert expected in capsys.readouterr().out


def test_gold_cli_profile_runs_fixed_control_archives_all_worlds_and_rejects_changed_resume(tmp_path, monkeypatch, capsys):
    from dataclasses import replace
    execution = integrated_execution()
    config = tmp_path / "profile.json"
    config.write_text(json.dumps(asdict(execution)), encoding="utf-8")
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps(own_rules_config()), encoding="utf-8")
    output = tmp_path / "control"
    # Only the data sources are synthetic; operators, gates and persistence are real.
    monkeypatch.setattr(gold_cli, "_load_dataset", lambda args: fixed_control_dataset())
    monkeypatch.setattr(gold_cli, "_verified_portfolio_tape", lambda *args: pytest.fail("fixture cannot read real portfolio sources"))
    argv = [
        "search", "--fixture", "tiny", "--execution-profile", str(config), "--own-rules-config", str(rules),
        "--max-generations", "1", "--max-evaluations", "1", "--population-size", "1",
        "--oracle-finalists", "1", "--bootstrap-samples", "20", "--workers", "1", "--output-root", str(output),
    ]
    assert main(argv) == 0, capsys.readouterr().out
    run_dir, = _run_dirs(output)
    card = json.loads((run_dir / "run_card.json").read_text(encoding="utf-8"))
    meta = card["run_metadata"]
    assert meta["total_evaluations"] == 1
    assert meta["execution_profile_scope"] == "own_rules_v1"
    assert meta["historical_admission_interface"] == "not_connected_to_m7"
    assert meta["own_rules_config"]["domain_size"] == 6
    assert meta["execution"] == json.loads(json.dumps(asdict(execution)))
    certification, = meta["oracle_world_certification"]
    assert certification["certified_worlds"] == 6
    assert all(item["portfolio"]["status"] == "passed" for item in certification["worlds"])
    for world in meta["execution_validation_worlds"]:
        assert world["market"] == asdict(execution.market)
        assert world["entry_fill_latency_ms"] == 1_000
        assert world["protection"] == json.loads(json.dumps(asdict(execution.protection)))
    candidates = pd.read_parquet(run_dir / "candidate_matrix.parquet")
    assert len(candidates) == 1
    assert main(["resume", *argv[1:]]) == 0, capsys.readouterr().out
    assert _run_dirs(output) == [run_dir]
    before = _history_snapshot(output)
    changed = replace(execution, market=replace(execution.market, close_acknowledgement_delay_ms=999))
    config.write_text(json.dumps(asdict(changed)), encoding="utf-8")
    assert main(["resume", *argv[1:]]) == 2
    assert "no compatible Gold checkpoint" in capsys.readouterr().out
    assert _history_snapshot(output) == before
    config.write_text(json.dumps(asdict(execution)), encoding="utf-8")
    changed_rules = own_rules_config()
    changed_rules["mutations"]["trailing_distance"] = [24.]
    rules.write_text(json.dumps(changed_rules), encoding="utf-8")
    assert main(["resume", *argv[1:]]) == 2
    assert _history_snapshot(output) == before


def _run_dirs(output_root):
    return sorted(
        path.parent
        for path in output_root.glob("*/run_card.json")
    )


def test_experiment_key_changes_with_implementation(monkeypatch):
    monkeypatch.setattr(
        gold_cli, "implementation_identity", lambda: {"sha256": "old"},
        raising=False,
    )
    args = ({"fixture": "same-data"}, SearchSpace(), ExecutionAssumptions(), 7)
    first = gold_cli._experiment_key(*args)
    assert gold_cli._experiment_key(*args) == first
    monkeypatch.setattr(
        gold_cli, "implementation_identity", lambda: {"sha256": "new"},
    )
    assert gold_cli._experiment_key(*args) != first


def _search_args(output_root, *, generations=1, progress=False):
    args = [
        "search",
        "--fixture",
        "tiny",
        "--max-generations",
        str(generations),
        "--patience-generations",
        "10",
        "--population-size",
        "8",
        "--oracle-finalists",
        "2",
        "--output-root",
        str(output_root),
    ]
    if progress:
        args.append("--progress")
    return args


def test_gold_cli_has_explicit_commands_and_safe_runtime_defaults():
    inspect = _parser().parse_args(["inspect"])
    search = _parser().parse_args(["search"])

    assert inspect.command == "inspect"
    assert search.command == "search"
    assert search.from_date == "2026-07-27"
    assert search.signal_scope == "now"
    assert search.replay_path == "runtime_data/replay_trades.jsonl"
    assert search.audit_path == "runtime_data/observed_tick_replay_audit.jsonl"
    assert search.provider_catalog_path == "runtime_data/provider_signal_catalog.json"
    assert search.provider_media_annotations == (
        "research/gold_iterative/provider_claim_annotations.json"
    )
    assert search.provider_media_evidence == (
        "runtime_data/telemetry_latest/telegram_media.jsonl"
    )
    assert search.raw_events_path == "runtime_data/trade_events.jsonl"
    assert search.max_total_volume == 1.0
    assert search.minimum_future_challenge_folds == 12
    assert search.minimum_future_challenge_signals == 100
    assert search.minimum_future_filled_signals == 100


def test_inspect_reports_complete_and_incomplete_days_without_running_search(capsys):
    code = main(["inspect", "--fixture", "tiny"])
    output = capsys.readouterr().out
    assert output.startswith("{"), repr(output)
    payload = json.loads(output)

    assert code == 0
    assert payload["eligible_signals"] == 3
    assert payload["loaded_paths"] == 3
    assert payload["complete_days"] == [
        "2026-08-24",
        "2026-08-25",
        "2026-08-26",
    ]
    assert payload["incomplete_days"] == []
    assert payload["folds"] == 1


def test_search_prints_bounded_progress_and_publishes_deterministically(
    tmp_path,
    capsys,
):
    first_code = main(_search_args(tmp_path, generations=2, progress=True))
    first_output = capsys.readouterr().out
    run_dirs = _run_dirs(tmp_path)

    assert first_code == 0
    assert "Generacion 1/2" in first_output
    assert "evaluadas" in first_output
    assert "ETA" in first_output
    assert "Parada: max_generations" in first_output
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    first_frontier = (run_dir / "frontier.json").read_bytes()
    first_manifest = (run_dir / "artifact_manifest.json").read_bytes()
    card = json.loads((run_dir / "run_card.json").read_text(encoding="utf-8"))
    candidates = pd.read_parquet(run_dir / "candidate_matrix.parquet")
    assert card["run_metadata"]["budget"]["max_generations"] == 2
    assert card["run_metadata"]["stop_reasons"] == ["max_generations"]
    assert card["run_metadata"]["live_code_changed"] is False
    identity = card["run_metadata"]["implementation"]
    assert identity == gold_cli.implementation_identity()
    key = card["run_metadata"]["experiment_key"]
    checkpoints = tuple((tmp_path / ".checkpoints" / key).glob("*/checkpoint.json"))
    assert checkpoints
    for checkpoint in checkpoints:
        payload = json.loads(checkpoint.read_text(encoding="utf-8"))
        assert payload["experiment_context"]["implementation"] == identity
    metadata = pq.read_schema(run_dir / "candidate_matrix.parquet").metadata
    assert metadata[b"iterative_implementation_sha256"].decode() == identity["sha256"]
    assert metadata[b"gold_experiment_key"].decode() == key
    assert card["run_metadata"]["future_evidence_policy"] == {
        "minimum_future_challenge_folds": 1,
        "minimum_future_challenge_signals": 1,
        "minimum_future_filled_signals": 1,
        "scope": "post_first_discovery_only",
    }
    validation = card["run_metadata"]["cross_fold_validation"]
    assert len(validation["stability_assessments"]) == (
        validation["stability_considered_count"]
    )
    assert card["run_metadata"]["chronological_challenge"]["complete"] is True
    assert {"fold", "generation", "strategy_fingerprint"} <= set(
        candidates.columns
    )

    second_code = main(_search_args(tmp_path, generations=2, progress=False))
    capsys.readouterr()
    assert second_code == 0
    assert _run_dirs(tmp_path) == [run_dir]
    assert (run_dir / "frontier.json").read_bytes() == first_frontier
    assert (run_dir / "artifact_manifest.json").read_bytes() == first_manifest


def test_resume_verify_and_provider_comparison_are_explicit_commands(
    tmp_path,
    capsys,
    monkeypatch,
):
    assert main(_search_args(tmp_path, generations=1)) == 0
    capsys.readouterr()

    resume_args = _search_args(tmp_path, generations=2)
    resume_args[0] = "resume"
    assert main(resume_args) == 0
    resume_output = capsys.readouterr().out
    assert "Reanudando" in resume_output
    cards = [
        json.loads((run_dir / "run_card.json").read_text(encoding="utf-8"))
        for run_dir in _run_dirs(tmp_path)
    ]
    assert {card["run_metadata"]["budget"]["max_generations"] for card in cards} == {
        1,
        2,
    }

    final_dir = next(
        run_dir
        for run_dir in _run_dirs(tmp_path)
        if json.loads((run_dir / "run_card.json").read_text(encoding="utf-8"))[
            "run_metadata"
        ]["budget"]["max_generations"] == 2
    )
    assert main(["verify", "--run-dir", str(final_dir)]) == 0
    assert "VERIFICADO" in capsys.readouterr().out

    assert main([
        "compare-provider-claims",
        "--run-dir",
        str(final_dir),
    ]) == 0
    comparison = capsys.readouterr().out
    assert "provider_pips" in comparison
    assert "NO VERIFICADA" in comparison
    assert "no selecciona estrategias" in comparison

    original_identity = gold_cli.implementation_identity()
    monkeypatch.setattr(gold_cli, "implementation_identity", lambda: {
        **original_identity, "sha256": "different-implementation",
    })
    before = _history_snapshot(tmp_path)
    assert main(resume_args) == 2
    assert "no compatible Gold checkpoint" in capsys.readouterr().out
    assert _history_snapshot(tmp_path) == before

    def must_not_revalidate_implementation():
        raise AssertionError("archive-byte verification must not load current identity")

    monkeypatch.setattr(gold_cli, "implementation_identity", must_not_revalidate_implementation)
    assert main(["verify", "--run-dir", str(final_dir)]) == 0
    assert "bytes archivados; no revalidacion del motor" in capsys.readouterr().out


def _history_snapshot(root):
    return {
        path.relative_to(root).as_posix(): (
            (path.read_bytes(), path.stat().st_mtime_ns) if path.is_file() else None
        )
        for path in root.rglob("*")
    }


def test_resume_rejects_legacy_root_without_creating_spool(tmp_path, capsys):
    legacy = tmp_path / ".checkpoints/gold_fold_01/checkpoint.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"schema_version": 3}', encoding="utf-8")
    before = _history_snapshot(tmp_path)
    args = _search_args(tmp_path)
    args[0] = "resume"
    assert main(args) == 2
    assert "legacy histories are not migrated" in capsys.readouterr().out
    assert _history_snapshot(tmp_path) == before


def test_resume_rejects_legacy_checkpoint_copied_under_current_key(tmp_path, capsys):
    argv = _search_args(tmp_path)
    args = _parser().parse_args(argv)
    key = gold_cli._experiment_key(
        gold_cli._tiny_dataset().source_hashes, SearchSpace(),
        ExecutionAssumptions(), args.seed, population_size=args.population_size,
    )
    legacy = tmp_path / ".checkpoints" / key / "gold_fold_01/checkpoint.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"schema_version": 3}', encoding="utf-8")
    before = _history_snapshot(tmp_path)
    argv[0] = "resume"
    assert main(argv) == 2
    assert "checkpoint schema version does not match" in capsys.readouterr().out
    assert _history_snapshot(tmp_path) == before


@pytest.mark.parametrize("kind", ("legacy", "implementation", "experiment"))
def test_spool_rejects_stale_fragments_before_writing(tmp_path, kind):
    history = tmp_path / "history"
    fragment = history / "gold_fold_01/generation-000001.parquet"
    fragment.parent.mkdir(parents=True)
    schema = gold_cli._CANDIDATE_SCHEMA
    if kind != "legacy":
        schema = schema.with_metadata({
            b"iterative_implementation_sha256": (
                b"old" if kind == "implementation"
                else gold_cli.implementation_identity()["sha256"].encode()
            ),
            b"gold_experiment_key": b"old" if kind == "experiment" else b"same",
        })
    pq.write_table(pa.Table.from_pylist([], schema=schema), fragment)
    before = _history_snapshot(tmp_path)
    with pytest.raises(ValueError, match="fragment provenance"):
        gold_cli._CandidateFragmentSpool(
            history, tmp_path / "must-not-create", experiment_key="same",
        )
    assert _history_snapshot(tmp_path) == before


def test_spool_resumes_identical_identity_and_records_it(tmp_path):
    history = tmp_path / "history"
    spool = gold_cli._CandidateFragmentSpool(history, tmp_path, experiment_key="same")
    spool.append(SimpleNamespace(name="gold_fold_01"), 1, ())
    fragment = history / "gold_fold_01/generation-000001.parquet"
    before = fragment.read_bytes()
    resumed = gold_cli._CandidateFragmentSpool(history, tmp_path, experiment_key="same")
    resumed.append(SimpleNamespace(name="gold_fold_01"), 1, ())
    assert fragment.read_bytes() == before
    output, rows = resumed.materialize()
    assert rows == 0
    assert pq.read_schema(output).metadata == spool.schema.metadata


def test_verify_rejects_a_modified_published_artifact(tmp_path, capsys):
    assert main(_search_args(tmp_path)) == 0
    capsys.readouterr()
    run_dir = _run_dirs(tmp_path)[0]
    frontier = run_dir / "frontier.json"
    frontier.write_bytes(frontier.read_bytes() + b"\n")

    assert main(["verify", "--run-dir", str(run_dir)]) == 2
    output = capsys.readouterr().out
    assert "ERROR" in output
    assert "immutable artifact conflict" in output
    assert "VERIFICADO" not in output


def test_real_provider_hypotheses_are_built_from_selected_simulations(monkeypatch):
    captured = {}

    def fake_builder(evaluations, *, paths, provider_scorecard):
        captured.update({
            "evaluations": evaluations,
            "paths": paths,
            "provider_scorecard": provider_scorecard,
        })
        return ("hypothesis",)

    monkeypatch.setattr(
        gold_cli,
        "build_candidate_pip_hypotheses",
        fake_builder,
        raising=False,
    )

    result = gold_cli._provider_hypotheses(
        SimpleNamespace(fixture=None),
        evaluations=("evaluation",),
        paths=("path",),
        provider_scorecard={"provider": "Gold Signals"},
    )

    assert result == ("hypothesis",)
    assert captured["evaluations"] == ("evaluation",)
    assert captured["paths"] == ("path",)


def test_search_uses_detailed_certified_results_for_provider_accounting(
    tmp_path,
    monkeypatch,
    capsys,
):
    captured = {}

    def capture_hypotheses(
        _args,
        *,
        evaluations,
        paths,
        provider_scorecard,
    ):
        captured.update({
            "evaluations": evaluations,
            "paths": paths,
            "provider_scorecard": provider_scorecard,
        })
        return ()

    monkeypatch.setattr(
        gold_cli,
        "_provider_hypotheses",
        capture_hypotheses,
    )

    assert main(_search_args(tmp_path)) == 0
    capsys.readouterr()

    results = tuple(
        result
        for evaluation in captured["evaluations"]
        for _day, result in evaluation.results
    )
    assert results
    assert any(result.exits for result in results)
    assert all(result.behavior_digest is None for result in results)
    run_dir = _run_dirs(tmp_path)[0]
    card = json.loads(
        (run_dir / "run_card.json").read_text(encoding="utf-8")
    )
    assert card["run_metadata"]["provider_accounting_contract"] == {
        "input": "oracle_certified_full_window_detail_v1",
        "model": "candidate_exit_and_mfe_hypotheses_v1",
    }


def test_chronological_diagnostics_accepts_a_fold_with_any_complete_candidate():
    report = SimpleNamespace(fold_reports=(
        SimpleNamespace(
            fold=SimpleNamespace(name="fold_01"),
            challenge_evaluations=(
                SimpleNamespace(net_eur=1, blockers=()),
                SimpleNamespace(
                    net_eur=None,
                    blockers=("path_ended_before_strategy_exit",),
                ),
            ),
        ),
    ))

    diagnostics = gold_cli._chronological_diagnostics(report)

    assert diagnostics["complete"] is True
    assert diagnostics["folds"][0] == {
        "fold": "fold_01",
        "candidate_count": 2,
        "complete_candidate_count": 1,
        "rejected_candidate_count": 1,
        "rejection_reasons": {
            "missing_net_eur": 1,
            "path_ended_before_strategy_exit": 1,
        },
    }


def test_chronological_diagnostics_rejects_a_fold_without_complete_candidate():
    report = SimpleNamespace(fold_reports=(
        SimpleNamespace(
            fold=SimpleNamespace(name="fold_01"),
            challenge_evaluations=(
                SimpleNamespace(net_eur=None, blockers=()),
                SimpleNamespace(net_eur=1, blockers=("missing_conversion",)),
            ),
        ),
    ))

    diagnostics = gold_cli._chronological_diagnostics(report)

    assert diagnostics["complete"] is False
    assert diagnostics["folds"][0]["complete_candidate_count"] == 0
    assert diagnostics["folds"][0]["rejection_reasons"] == {
        "missing_conversion": 1,
        "missing_net_eur": 1,
    }
