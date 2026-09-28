from argparse import Namespace
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from tests.test_gold_exit_deals import _fixture
from tools import compare_causal_controls as comparison
from tools import run_causal_controls as controls


BASE = datetime(2026, 9, 8, 10, tzinfo=timezone.utc)


def test_frozen_reader_hashes_the_bytes_it_parses(tmp_path):
    path = tmp_path / "source.json"
    expected = controls.save(path, {"version": 1})
    parsed, identity = controls.read_frozen(path)
    assert parsed == {"version": 1} and identity == expected
    path.write_text('{"version": 2}', encoding="utf-8")
    with pytest.raises(ValueError, match="changed during execution"):
        controls.verify_frozen(path, identity)


def frozen_study(tmp_path):
    raw = [{"ev": "telegram_raw", "channel": "canal2", "message_id": 1,
            "message_revision_id": "rev-1", "date_utc": BASE.isoformat(),
            "ts": BASE.isoformat(), "text": "BUY GOLD NOW"}]
    raw_hash = controls.save(tmp_path / "raw_messages.json", raw)
    tapes = {}
    for symbol, bid, ask in (("XAUUSD", 100., 100.2), ("EURUSD", 1., 1.)):
        tape = tmp_path / f"{symbol}.parquet"
        timestamps = pd.to_datetime([BASE, BASE + timedelta(seconds=1), BASE + timedelta(seconds=2)])
        pd.DataFrame({"time_utc": timestamps, "source_time_msc": timestamps.astype("datetime64[ms, UTC]").astype("int64"),
                      "bid": [bid] * 3, "ask": [ask] * 3}).to_parquet(tape)
        tapes[symbol] = {"path": str(tape), "sha256": controls.digest(tape)}
    protocol = {"implementation": controls.identity(), "raw_messages_sha256": raw_hash,
        "search_candidate_budget": 0, "latencies_ms": list(controls.LATENCIES),
        "execution_scenarios": list(controls.SCENARIOS), "max_wall_seconds": controls.MAX_WALL_SECONDS,
        "start_utc": BASE.isoformat(), "cutoff_utc": (BASE + timedelta(seconds=2)).isoformat(),
        "sticker_directions": controls.STICKERS, "expected_signal_ids": ["canal2_1"],
        "genomes": {channel: asdict(value) for channel, value in controls.policies().items()},
        "tapes": tapes, "broker_epoch_offset_seconds": 0, "contract_size": 100,
        "currency_digits": 2, "fx_max_age_ms": 5000}
    controls.save(tmp_path / "protocol.json", protocol)


def test_changed_protocol_during_execution_cannot_label_old_results(tmp_path):
    frozen_study(tmp_path)
    script = """
import json
from argparse import Namespace
from pathlib import Path
from tools import run_causal_controls as controls
from research.dubai_iterative import engine
original = engine.simulate
def changed(*args, **kwargs):
    result = original(*args, **kwargs)
    path = Path(STUDY) / 'protocol.json'
    payload = json.loads(path.read_text())
    payload['changed_while_running'] = True
    path.write_text(json.dumps(payload))
    return result
engine.simulate = changed
controls.run(Namespace(study=Path(STUDY)))
""".replace("STUDY", repr(str(tmp_path)))
    result = subprocess.run([sys.executable, "-c", script], cwd=controls.ROOT,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode != 0
    assert "frozen input changed during execution: protocol.json" in result.stderr
    assert not (tmp_path / "independent_results.json").exists()


def comparison_fixture(tmp_path):
    study, analysis = tmp_path / "study", tmp_path / "analysis"
    study.mkdir()
    analysis.mkdir()
    actual, mirror = _fixture()
    actual["positions"][0]["open_deal"]["comment"] = "c2_1_g55"
    ledger_hash = controls.save(analysis / "observed_ledgers.json", {"signals": [actual]})
    controls.save(analysis / "analysis_manifest.json", {
        "source_capture_manifest_sha256": "a" * 64,
        "artifacts": [{"name": "observed_ledgers.json", "sha256": ledger_hash,
                       "bytes": (analysis / "observed_ledgers.json").stat().st_size}]})
    raw_hash = controls.save(study / "raw_messages.json", [])
    metadata_hash = controls.save(study / "input_diagnostics.json", {
        "signals": [{"signal_id": "canal2_1", "direction": "BUY"}]})
    protocol_hash = controls.save(study / "protocol.json", {
        "raw_messages_sha256": raw_hash, "input_diagnostics_sha256": metadata_hash,
        "source_capture_manifest_sha256": "a" * 64, "expected_signal_ids": ["canal2_1"],
        "latencies_ms": [0], "execution_scenarios": [{"latency_ms": 0, "entry_fill_latency_ms": 0}],
        "admission_blockers": ["not_certified"]})
    controls.save(study / "independent_results.json", {
        "protocol_sha256": protocol_hash,
        "results": [{"signal_id": "canal2_1", "latency_ms": 0, "entry_fill_latency_ms": 0,
            "engine_mismatches": {"scalar": [], "fast": []},
            "result": {"blockers": [], "entries": [{"ticket": "sim_1", "source": "causal_signal_market",
                "opened_at": actual["positions"][0]["open_dt_utc"], "entry_price": "100.20", "volume": "0.04"}],
                "exits": [row | {"ticket": "sim_1"} for row in mirror]}}]})
    return Namespace(study=study, analysis=analysis)


def test_comparison_consumes_frozen_metadata_not_current_parser(tmp_path, monkeypatch):
    args = comparison_fixture(tmp_path)
    import research.causal_replay as replay
    def wrong_compiler(*args, **kwargs):
        raise AssertionError("current compiler must not reinterpret a frozen run")
    monkeypatch.setattr(replay, "compile_signals", wrong_compiler)
    comparison.compare(args)
    report = controls.read(args.study / "first_divergences.json")
    assert report["statuses"] == {"exact_facts_only": 1}
    assert report["full_live_parity_verified"] is False


def test_comparison_rejects_modified_causal_metadata(tmp_path):
    args = comparison_fixture(tmp_path)
    path = args.study / "input_diagnostics.json"
    value = controls.read(path)
    value["signals"][0]["direction"] = "SELL"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="frozen causal metadata"):
        comparison.compare(args)
    assert not (args.study / "first_divergences.json").exists()


@pytest.mark.parametrize("comment", ["c2_2_g55", "c2_1_B9_g55", "unbound"])
def test_logical_leg_requires_exact_signal_metadata(comment):
    with pytest.raises(ValueError, match="logical slot"):
        comparison.observed_slot("canal2_1", comment)
