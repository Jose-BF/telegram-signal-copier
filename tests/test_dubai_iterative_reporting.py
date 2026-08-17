from __future__ import annotations

import json
from dataclasses import replace

import pandas as pd
import pytest

from research.dubai_iterative.reporting import (
    ProvenanceConflictError,
    ResearchArtifacts,
    publish_run,
)


def _artifacts():
    return ResearchArtifacts(
        run_card={
            "schema_version": 1,
            "source_hashes": {"fixture": "abc123"},
            "signal_ids": ["canal1_1", "canal1_2"],
            "exclusions": {"blocked": ["canal1_3"]},
            "seed": 7,
            "folds": [{"name": "tiny"}],
            "budget": {"max_generations": 2},
            "search_space": {"max_total_volume": 0.20},
            "grammar_version": 1,
            "confidence": "retrospective_unstable",
        },
        frontier=(
            {
                "fingerprint": "aaa",
                "plain_strategy": "Cerrar la cesta al alcanzar 5 EUR",
                "development_net_eur": 8.0,
                "challenge_net_eur": -2.0,
                "max_drawdown_eur": 4.0,
            },
        ),
        generation_rows=(
            {"fold": "tiny", "generation": 1, "evaluated": 4, "frontier_size": 1},
            {"fold": "tiny", "generation": 2, "evaluated": 8, "frontier_size": 1},
        ),
        candidate_rows=(
            {"fingerprint": "aaa", "net_eur": 8.0, "drawdown_eur": 4.0},
            {"fingerprint": "bbb", "net_eur": 2.0, "drawdown_eur": 7.0},
        ),
        signal_rows=(
            {"fingerprint": "aaa", "signal_id": "canal1_1", "pnl_eur": 5.0},
            {"fingerprint": "aaa", "signal_id": "canal1_2", "pnl_eur": 3.0},
        ),
    )


def test_run_identity_is_stable_and_conflicting_bytes_fail(tmp_path):
    first = publish_run(_artifacts(), tmp_path)
    second = publish_run(_artifacts(), tmp_path)

    assert first.run_id == second.run_id
    assert first.run_dir == second.run_dir
    (first.run_dir / "frontier.json").write_text("corrupt", encoding="utf-8")

    with pytest.raises(ProvenanceConflictError, match="frontier.json"):
        publish_run(_artifacts(), tmp_path)


def test_publish_run_writes_compact_tables_and_readable_charts(tmp_path):
    published = publish_run(_artifacts(), tmp_path)

    expected = {
        "run_card.json",
        "frontier.json",
        "generation_summary.jsonl",
        "candidate_matrix.parquet",
        "signal_results.parquet",
        "charts/equity.png",
        "charts/floating_drawdown.png",
        "charts/generation_progress.png",
    }
    actual = {
        path.relative_to(published.run_dir).as_posix()
        for path in published.run_dir.rglob("*")
        if path.is_file()
    }
    assert expected <= actual
    assert json.loads((published.run_dir / "run_card.json").read_text(encoding="utf-8"))["run_id"] == published.run_id
    assert len(pd.read_parquet(published.run_dir / "candidate_matrix.parquet")) == 2
    assert len(pd.read_parquet(published.run_dir / "signal_results.parquet")) == 2
    assert all((published.run_dir / name).stat().st_size > 1_000 for name in (
        "charts/equity.png",
        "charts/floating_drawdown.png",
        "charts/generation_progress.png",
    ))


def test_same_run_identity_rejects_different_candidate_results(tmp_path):
    publish_run(_artifacts(), tmp_path)
    changed = replace(
        _artifacts(),
        candidate_rows=(
            {"fingerprint": "aaa", "net_eur": 999.0, "drawdown_eur": 4.0},
        ),
    )

    with pytest.raises(ProvenanceConflictError, match="candidate_matrix"):
        publish_run(changed, tmp_path)
