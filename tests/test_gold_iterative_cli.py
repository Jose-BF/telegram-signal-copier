from __future__ import annotations

import json

import pandas as pd

from research.gold_iterative.__main__ import _parser, main


def _run_dirs(output_root):
    return sorted(
        path.parent
        for path in output_root.glob("*/run_card.json")
    )


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
    assert search.replay_path == "runtime_data/replay_trades.jsonl"
    assert search.audit_path == "runtime_data/observed_tick_replay_audit.jsonl"
    assert search.provider_catalog_path == "runtime_data/provider_signal_catalog.json"
    assert search.raw_events_path == "runtime_data/trade_events.jsonl"
    assert search.max_total_volume == 1.0


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
