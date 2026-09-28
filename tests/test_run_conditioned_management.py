from dataclasses import asdict
import json

import pytest

from research.causal_replay import compile_signals, utc
from tests.test_causal_replay import message
from tests.test_compare_risk_trajectories import archive
from tools.run_causal_controls import digest, encode, policies, save
from tools.run_conditioned_management import run


@pytest.fixture
def conditioned_archive(archive):
    study, analysis = archive
    protocol = json.loads((study / "protocol.json").read_text())
    raw = [message(ts=protocol["start_utc"], date_utc=protocol["start_utc"]),
           message(2, "CLOSE ALL", ts=protocol["cutoff_utc"], date_utc=protocol["cutoff_utc"],
                   reply_to_msg_id=1)]
    protocol["raw_messages_sha256"] = save(study / "raw_messages.json", raw)
    protocol["sticker_directions"] = {}
    signals, diagnostics = compile_signals(raw, start=utc(protocol["start_utc"]),
        cutoff=utc(protocol["cutoff_utc"]), sticker_directions={})
    metadata = {"signals": [asdict(row) for row in signals], "diagnostics": diagnostics}
    (study / "input_diagnostics.json").write_bytes(encode(metadata))
    protocol["input_diagnostics_sha256"] = digest(study / "input_diagnostics.json")
    protocol["genomes"] = {channel: asdict(genome) for channel, genome in policies().items()}
    (study / "protocol.json").write_bytes(encode(protocol))
    return study, analysis


def test_cli_reuses_existing_engine_keeps_different_exits_and_no_certification(conditioned_archive, tmp_path):
    study, analysis = conditioned_archive
    out = tmp_path / "comparison.json"
    report = run(study, analysis, out, max_market_gap_ms=5000)
    assert report["expected_signals"] == 1
    row = report["rows"][0]
    assert row["management"]["status"] == "evaluated_conditioned_management"
    assert row["management"]["engine_mismatches"] == {"scalar": [], "fast": []}
    assert row["risk"]["status"] == "mismatch"
    assert row["risk"]["observed"]["metrics"]["final_net"] != row["risk"]["simulated"]["metrics"]["final_net"]
    assert report["entry_decisions_verified"] is report["full_live_parity_verified"] is False
    assert report["inherited_admission_blockers"] == ["not_certified"]
    with pytest.raises(ValueError, match="immutable"):
        run(study, analysis, out, max_market_gap_ms=5000)


def test_changed_raw_messages_are_rejected(conditioned_archive, tmp_path):
    study, analysis = conditioned_archive
    with (study / "raw_messages.json").open("ab") as stream:
        stream.write(b" ")
    with pytest.raises(ValueError, match="binding"):
        run(study, analysis, tmp_path / "out.json", max_market_gap_ms=5000)


def test_missing_native_signal_is_retained_in_denominator(conditioned_archive, tmp_path):
    study, analysis = conditioned_archive
    ledger = analysis / "observed_ledgers.json"
    ledger.write_bytes(encode({"account_currency": "EUR", "signals": []}))
    manifest = json.loads((analysis / "analysis_manifest.json").read_text())
    manifest["artifacts"][0].update(sha256=digest(ledger), bytes=ledger.stat().st_size)
    (analysis / "analysis_manifest.json").write_bytes(encode(manifest))
    report = run(study, analysis, tmp_path / "out.json", max_market_gap_ms=5000)
    assert report["expected_signals"] == len(report["rows"]) == 1
    assert report["rows"][0]["status"] == "blocked"
    assert report["rows"][0]["blockers"] == ["missing_or_unexpected_native_signal"]


def test_missing_cost_fact_blocks_case_without_aborting_other_evidence(conditioned_archive, tmp_path):
    study, analysis = conditioned_archive
    ledger = analysis / "observed_ledgers.json"
    value = json.loads(ledger.read_text())
    del value["signals"][0]["positions"][0]["deals"][0]["fee"]
    ledger.write_bytes(encode(value))
    manifest = json.loads((analysis / "analysis_manifest.json").read_text())
    manifest["artifacts"][0].update(sha256=digest(ledger), bytes=ledger.stat().st_size)
    (analysis / "analysis_manifest.json").write_bytes(encode(manifest))
    report = run(study, analysis, tmp_path / "out.json", max_market_gap_ms=5000)
    assert report["rows"][0]["status"] == "blocked"
    assert report["rows"][0]["blockers"] == ["native_cost_fact_invalid"]
