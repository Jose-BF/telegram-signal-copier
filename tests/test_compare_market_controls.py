from copy import deepcopy
from argparse import Namespace
import hashlib
from pathlib import Path

import pytest

from tools import compare_market_controls as comparison
from tools import run_protection_controls as controls


def fixture():
    result = {"signal_id": "canal2_1", "blockers": [], "entries": [], "exits": []}
    protocol = {
        "contract": "fixed_market_regression_v2", "status": "retrospective_not_oos",
        "independent_of_observed_outcomes": True, "search_candidates": 0,
        "full_live_parity_verified": False, "expected_signal_ids": ["canal2_1"],
        "implementation": {"test": "frozen"}, "max_engine_evaluations": 3,
    }
    results = {"denominator": 1, "engine_evaluations": 3, "search_candidates": 0,
        "implementation": protocol["implementation"], "results": [{"signal_id": "canal2_1",
        "scalar": deepcopy(result), "fast": deepcopy(result), "oracle": deepcopy(result),
        "mismatches": {"scalar": [], "fast": []}}]}
    results["protocol_sha256"] = hashlib.sha256(controls.encode(protocol)).hexdigest()
    return protocol, results


def test_accepts_complete_independent_control_without_certifying_it():
    protocol, results = fixture()
    comparison.validate_control(protocol, results, protocol["implementation"], results["protocol_sha256"])


@pytest.mark.parametrize("change", ["missing", "duplicate", "unexpected", "count", "identity",
                                    "observed", "stale", "search", "embedded_signal",
                                    "genome", "execution", "unbound_legacy"])
def test_rejects_invalid_control_contract(change):
    protocol, results = fixture()
    current = protocol["implementation"]
    if change == "missing":
        results["results"] = []
    elif change == "duplicate":
        results["results"] *= 2
    elif change == "unexpected":
        results["results"][0]["signal_id"] = "canal1_1"
    elif change == "count":
        results["engine_evaluations"] = 2
    elif change == "identity":
        results["implementation"] = {}
    elif change == "observed":
        protocol["independent_of_observed_outcomes"] = False
    elif change == "stale":
        current = {"different": True}
    elif change == "search":
        results["search_candidates"] = 1
    elif change == "embedded_signal":
        results["results"][0]["fast"]["signal_id"] = "canal2_2"
    elif change == "genome":
        protocol["genome"] = {"changed": True}
    elif change == "execution":
        protocol["execution"] = {"entry_fill_latency_ms": 999}
    elif change == "unbound_legacy":
        protocol["contract"] = "fixed_market_regression_v1"
        del results["protocol_sha256"]
    with pytest.raises(ValueError):
        comparison.validate_control(protocol, results, current,
                                    hashlib.sha256(controls.encode(protocol)).hexdigest())


def test_recomputes_disagreement_instead_of_trusting_empty_recorded_mismatches():
    protocol, results = fixture()
    results["results"][0]["fast"]["blockers"] = ["changed"]
    rows = comparison.compare_rows(protocol, results, {}, {"canal2_1": "BUY"})
    assert rows[0]["comparison"]["status"] == "blocked"
    assert rows[0]["engine_mismatches"]["fast"] == ["blockers"]


def test_retains_missing_observed_case_and_out_of_scope_channel():
    protocol, results = fixture()
    actual = {"canal1_2": {"n_positions": 3}, "canal2_9": {"n_positions": 5}}
    rows = comparison.compare_rows(protocol, results, actual, {"canal2_1": "BUY"})
    assert [row["signal_id"] for row in rows] == ["canal2_1", "canal1_2", "canal2_9"]
    assert [row["comparison"]["status"] for row in rows] == ["blocked", "out_of_scope", "blocked"]
    assert all(row["comparison"]["full_live_parity_verified"] is False for row in rows)


def test_missing_direction_does_not_infer_no_trade():
    protocol, results = fixture()
    rows = comparison.compare_rows(protocol, results, {"canal2_1": {"n_positions": 0}}, {})
    assert rows[0]["comparison"]["status"] == "blocked"
    assert "direction" in rows[0]["comparison"]["blockers"][0]


def cli_fixture(tmp_path, monkeypatch):
    protocol, results = fixture()
    results["source_admission_blockers"] = ["retained_diagnostic_scope"]
    study, source, analysis = (tmp_path / name for name in ("study", "source", "analysis"))
    for folder in (study, source, analysis):
        folder.mkdir()
    raw_path = source / "raw_messages.json"
    raw_sha = controls.save(raw_path, [])
    metadata_sha = controls.save(source / "input_diagnostics.json", {
        "signals": [{"signal_id": "canal2_1", "direction": "BUY"}]})
    source_sha = controls.save(source / "protocol.json", {
        "raw_messages_sha256": raw_sha, "input_diagnostics_sha256": metadata_sha,
        "expected_signal_ids": ["canal2_1"], "source_capture_manifest_sha256": "capture"})
    protocol.update({"sources": {str(raw_path): raw_sha}, "source_protocol_sha256": source_sha})
    results["protocol_sha256"] = controls.save(study / "protocol.json", protocol)
    controls.save(study / "results.json", results)
    manifest_path = analysis / "analysis_manifest.json"
    manifest_sha = controls.save(manifest_path, {"source_capture_manifest_sha256": "capture"})
    observed = {"paths": {"analysis_manifest": manifest_path},
        "hashes": {"analysis_manifest_sha256": manifest_sha},
        "ledger": {"positions": 3, "signals": [{"sig_id": "canal1_2", "n_positions": 3}]}}
    monkeypatch.setattr(controls, "identity", lambda: protocol["implementation"])
    monkeypatch.setattr(controls, "_workspace_path", lambda path: Path(path).resolve())
    monkeypatch.setattr(controls, "_load_actual_sources", lambda *args: observed)
    return Namespace(study=study, source_study=source, analysis=analysis,
        measurement=tmp_path, event_prefix=tmp_path, event_delta=tmp_path,
        out=tmp_path / "comparison.json")


def test_comparison_writes_immutable_diagnostic_and_preserves_denominator(tmp_path, monkeypatch):
    args = cli_fixture(tmp_path, monkeypatch)
    report = comparison.compare(args)
    assert report["statuses"] == {"blocked": 1, "out_of_scope": 1}
    assert report["denominators"]["observed_signals"] == 1
    assert report["denominators"]["frozen_gold_controls"] == 1
    assert report["mass_search_authorized"] is False
    with pytest.raises(FileExistsError):
        comparison.compare(args)


def test_changed_frozen_metadata_prevents_writing_any_comparison(tmp_path, monkeypatch):
    args = cli_fixture(tmp_path, monkeypatch)
    (args.source_study / "input_diagnostics.json").write_text('{"signals": []}')
    with pytest.raises(ValueError, match="metadata identity"):
        comparison.compare(args)
    assert not args.out.exists()


def test_input_mutation_during_comparison_prevents_report(tmp_path, monkeypatch):
    args = cli_fixture(tmp_path, monkeypatch)
    original = comparison.compare_rows

    def mutate(*values):
        result = original(*values)
        (args.study / "results.json").write_text('{}')
        return result

    monkeypatch.setattr(comparison, "compare_rows", mutate)
    with pytest.raises(ValueError, match="frozen input changed"):
        comparison.compare(args)
    assert not args.out.exists()


def test_changed_execution_cannot_relabel_previous_result(tmp_path, monkeypatch):
    args = cli_fixture(tmp_path, monkeypatch)
    path = args.study / "protocol.json"
    protocol = controls.read(path)
    protocol["execution"] = {"entry_fill_latency_ms": 999}
    path.write_bytes(controls.encode(protocol))
    with pytest.raises(ValueError, match="result protocol"):
        comparison.compare(args)
    assert not args.out.exists()


@pytest.mark.parametrize("reason,mechanism", [("initial_sl", "sl"), ("initial_tp", "tp")])
def test_initial_protections_are_native_exit_mechanisms(reason, mechanism):
    result = {"blockers": [], "entries": [{"ticket": "sim_1", "source": "causal_signal_market",
        "opened_at": "2026-09-08T00:00:00Z", "entry_price": 4400, "volume": 0.01}],
        "exits": [{"ticket": "sim_1", "reason": reason, "closed_at": "2026-09-08T00:00:01Z",
                   "exit_price": 4399, "volume": 0.01, "pnl_eur": -1}]}
    events = comparison.simulated_events(result, "BUY")
    assert events[-1].mechanism == mechanism
