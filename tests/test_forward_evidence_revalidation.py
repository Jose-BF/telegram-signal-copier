"""Consumption validates retained evidence again, not just producer-time checks."""
from datetime import timedelta
from pathlib import Path

import pytest

from tests.test_forward_window_review import THURSDAY, _produce
from tests.test_run_simulator_forward import _frozen_protocol
from tools import prepare_simulator_forward as forward
from tools import run_protection_controls as controls
from tools import run_simulator_forward as runner


@pytest.mark.parametrize("kind", ["clock", "metadata"])
@pytest.mark.parametrize("mutation", ["missing_proof", "bad_source_hash", "bad_source_bytes",
                                      "bad_contract", "future_date", "bad_semantics", "wrong_cutoff"])
def test_forward_rejects_changed_evidence_at_consumption(tmp_path, monkeypatch, kind, mutation):
    end = THURSDAY + timedelta(hours=1)
    protocol = _frozen_protocol(tmp_path, monkeypatch, start_utc=THURSDAY, end_utc=end)
    data = _produce(protocol, tmp_path, THURSDAY, end)
    dataset_path = data / "protocol.json"
    dataset = controls.read(dataset_path)
    proof = dataset["source_evidence"][kind]
    retained = Path(proof["path"])
    value = controls.read(retained)
    if mutation == "missing_proof":
        del dataset["source_evidence"][kind]
    elif mutation == "bad_source_hash":
        proof["source_sha256"] = "0" * 64
    elif mutation == "bad_source_bytes":
        source = Path(proof["source_path"])
        source.write_bytes(source.read_bytes() + b"\n")
    else:
        if mutation == "bad_contract":
            value["contract"] = "unrecognized"
        elif mutation == "future_date":
            value["captured_at_utc"] = (end + timedelta(seconds=121)).isoformat()
        elif mutation == "bad_semantics":
            if kind == "clock":
                value["broker_epoch_offset_seconds"] = 0
            else:
                value["symbols"]["XAUUSD"]["volume_step"] = .1
        elif mutation == "wrong_cutoff":
            value["event_cutoff_utc"] = (end - timedelta(minutes=1)).isoformat()
        retained.write_bytes(controls.encode(value))
        proof["sha256"] = controls.digest(retained)
    dataset_path.write_bytes(controls.encode(dataset))
    with pytest.raises(ValueError):
        forward.check(protocol, data, now=end)
    with pytest.raises(ValueError):
        runner.execute(protocol, data, out=tmp_path / "results", now=end)
    assert not (tmp_path / "results").exists()


def test_omitting_evidence_cannot_fall_back_to_legacy(tmp_path, monkeypatch):
    end = THURSDAY + timedelta(hours=1)
    protocol = _frozen_protocol(tmp_path, monkeypatch, start_utc=THURSDAY, end_utc=end)
    data = _produce(protocol, tmp_path, THURSDAY, end)
    path = data / "protocol.json"
    value = controls.read(path)
    del value["source_evidence"]
    value.pop("contract", None)
    path.write_bytes(controls.encode(value))
    with pytest.raises(ValueError, match="evidence required"):
        forward.check(protocol, data, now=end)
