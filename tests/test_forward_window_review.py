"""Independent synthetic regressions for the configurable forward window."""

from datetime import timedelta

import pytest

from tests.test_run_simulator_forward import _frozen_protocol, _inputs
from tools import prepare_simulator_forward as forward
from tools import run_protection_controls as controls
from tools import run_simulator_forward as runner


THURSDAY = forward.utc("2026-09-10T13:00:00Z")


def _produce(protocol, directory, start, end):
    events, tapes, clock, metadata = _inputs(directory, start=start, end=end)
    data = directory / "dataset"
    runner.produce(
        protocol, events=events, market_tape=tapes["XAUUSD"],
        conversion_tape=tapes["EURUSD"], clock_evidence=clock,
        metadata_evidence=metadata, cutoff=end, out=data, now=end,
    )
    return data


@pytest.mark.parametrize("kind", ["clock", "metadata"])
@pytest.mark.parametrize("repair_hash", [False, True])
def test_consuming_dataset_rechecks_retained_evidence_window(
    tmp_path, monkeypatch, kind, repair_hash,
):
    end = THURSDAY + timedelta(hours=1)
    protocol = _frozen_protocol(tmp_path, monkeypatch, start_utc=THURSDAY, end_utc=end)
    data = _produce(protocol, tmp_path, THURSDAY, end)
    evidence_path = data / f"{kind}_evidence.json"
    value = controls.read(evidence_path)
    value["captured_at_utc"] = forward.END.isoformat()
    evidence_path.write_bytes(controls.encode(value))
    if repair_hash:
        dataset_protocol = controls.read(data / "protocol.json")
        dataset_protocol["source_evidence"][kind]["sha256"] = controls.digest(evidence_path)
        (data / "protocol.json").write_bytes(controls.encode(dataset_protocol))

    try:
        checked = forward.check(protocol, data, now=end)
        result = runner.execute(protocol, data, out=tmp_path / "results", now=end)
    except ValueError:
        return
    assert checked["status"] == result["status"] == "blocked", (
        f"{kind} evidence from the old window was accepted: "
        f"check={checked['status']}; run={result['status']}; "
        f"portfolio_verified={result['portfolio']['verified']}"
    )
    assert result["portfolio"]["verified"] is False
