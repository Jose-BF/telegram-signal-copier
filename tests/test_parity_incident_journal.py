from __future__ import annotations

import pytest

from parity_incident_journal import append_registry, load_registry


def _registry(*, status: str = "open", observation: str = "run-1") -> dict:
    incident = {
        "incident_id": "parity_example",
        "scope": "signal",
        "channel": "canal2",
        "signal_id": "canal2_100",
        "blocker": "control_outcome_mismatch",
        "candidate_id": None,
        "category": "prospective_control_parity",
        "required_action": "fix_entry_fill_or_money_model_then_replay_same_signal",
        "verification_required": "replay_same_signal_without_this_blocker",
        "blocks_comparison": True,
        "evidence": {"delta": 1.0},
        "evidence_digest": "a" * 64,
        "status": status,
        "verification_state": (
            "failing" if status == "open" else "exact_repair_replay"
        ),
        "first_observation_id": "run-1",
        "last_observation_id": observation,
        "resolved_observation_id": (
            None if status == "open" else observation
        ),
        "resolution_evidence": (
            None
            if status == "open"
            else "same_signal_repaired_with_current_engine"
        ),
        "occurrences": 1,
        "regression_count": 0,
        "evidence_digests": ["a" * 64],
    }
    open_count = int(status != "resolved")
    return {
        "schema_version": 1,
        "observation_id": observation,
        "open_count": open_count,
        "comparison_blocking_open_count": open_count,
        "regressed_count": 0,
        "resolved_count": int(status == "resolved"),
        "ranking_blocked": bool(open_count),
        "comparison_blocked": bool(open_count),
        "unresolved_incident_ids": (
            [incident["incident_id"]] if open_count else []
        ),
        "incidents": [incident],
    }


def test_incident_journal_round_trips_and_deduplicates(tmp_path):
    path = tmp_path / "strategy_shadow_incidents.jsonl"
    registry = _registry()

    assert append_registry(path, registry) == 1
    original = path.read_bytes()
    assert append_registry(path, registry) == 0
    assert path.read_bytes() == original
    assert load_registry(path) == registry


def test_incident_journal_preserves_resolution_history(tmp_path):
    path = tmp_path / "strategy_shadow_incidents.jsonl"
    append_registry(path, _registry())
    resolved = _registry(status="resolved", observation="run-2")

    assert append_registry(path, resolved) == 1
    assert load_registry(path) == resolved
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_incident_journal_fails_closed_on_corruption(tmp_path):
    path = tmp_path / "strategy_shadow_incidents.jsonl"
    path.write_text('{"broken":true}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="incident journal"):
        load_registry(path)
    with pytest.raises(ValueError, match="incident journal"):
        append_registry(path, _registry())
