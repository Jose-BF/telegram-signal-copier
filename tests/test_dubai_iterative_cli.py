from __future__ import annotations

import json

from research.dubai_iterative.__main__ import main


def test_cli_emits_bounded_progress_and_stop_reason(tmp_path, capsys):
    code = main([
        "--fixture",
        "tiny",
        "--max-generations",
        "2",
        "--patience-generations",
        "10",
        "--output-root",
        str(tmp_path),
        "--progress",
    ])
    output = capsys.readouterr().out

    assert code == 0
    assert "Generacion 1/2" in output
    assert "Generacion 2/2" in output
    assert "Parada: max_generations" in output
    assert "Estrategias evaluadas:" in output


def test_cli_fixture_publishes_a_bound_run(tmp_path):
    code = main([
        "--fixture",
        "tiny",
        "--max-generations",
        "1",
        "--output-root",
        str(tmp_path),
    ])

    cards = list(tmp_path.glob("*/run_card.json"))
    assert code == 0
    assert len(cards) == 1
    card = json.loads(cards[0].read_text(encoding="utf-8"))
    assert card["stop_reasons"] == ["max_generations"]
    assert card["live_code_changed"] is False
    assert card["confidence"] in {"retrospective_unstable", "demo_candidate"}
