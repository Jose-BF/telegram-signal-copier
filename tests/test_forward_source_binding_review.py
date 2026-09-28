"""Retained forward evidence must match the source whose proof it declares."""

from datetime import timedelta
from pathlib import Path

import pytest

from tests.test_forward_window_review import THURSDAY, _produce
from tests.test_run_simulator_forward import _frozen_protocol
from tools import prepare_simulator_forward as forward
from tools import run_protection_controls as controls
from tools import run_simulator_forward as runner


@pytest.mark.parametrize("kind", ["clock", "metadata"])
def test_source_proof_cannot_bind_evidence_with_different_content(tmp_path, monkeypatch, kind):
    end = THURSDAY + timedelta(hours=1)
    protocol = _frozen_protocol(tmp_path, monkeypatch, start_utc=THURSDAY, end_utc=end)
    data = _produce(protocol, tmp_path, THURSDAY, end)
    dataset_path = data / "protocol.json"
    dataset = controls.read(dataset_path)
    proof = dataset["source_evidence"][kind]
    source = Path(proof["source_path"])
    value = controls.read(source)
    if kind == "clock":
        value["captured_at_utc"] = forward.END.isoformat()
    else:
        value["symbols"]["XAUUSD"]["volume_step"] = 0.1
    source.write_bytes(controls.encode(value))
    proof["source_sha256"] = controls.digest(source)
    dataset_path.write_bytes(controls.encode(dataset))

    # All declared byte hashes are correct, but the retained projection is not.
    assert controls.digest(Path(proof["path"])) == proof["sha256"]
    assert controls.digest(source) == proof["source_sha256"]
    try:
        checked = forward.check(protocol, data, now=end)
        result = runner.execute(protocol, data, out=tmp_path / "results", now=end)
    except ValueError:
        return
    assert checked["status"] == result["status"] == "blocked", (
        f"{kind} source differs from its retained evidence: "
        f"check={checked['status']}; run={result['status']}; "
        f"portfolio_verified={result['portfolio']['verified']}"
    )
    assert result["portfolio"]["verified"] is False
