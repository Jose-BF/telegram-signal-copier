from dataclasses import asdict

import pytest

from tests.test_shared_transport import job
from tools.run_causal_controls import save
from tools.run_shared_transport_controls import run


def protocol(**changes):
    case = {"case_id": "both_channels", "capacity": 8, "cutoff_ns": 100,
            "jobs": [asdict(job("first", service=20)),
                     asdict(job("close", "CLOSE_POSITION", channel="canal2", at=1))],
            "passive_events": []}
    return {"contract": "shared_transport_hypotheses_v1", "clock": "session_relative_nanoseconds",
            "cases": [case]} | changes


def test_report_preserves_inputs_and_remains_diagnostic(tmp_path):
    path, out = tmp_path / "input.json", tmp_path / "output.json"
    save(path, protocol())
    report = run(path, out)
    assert report["expected_cases"] == 1
    assert report["cases"][0]["result"]["rows"][1]["started_ns"] == 20
    assert report["full_live_parity_verified"] is report["portfolio_admitted"] is False
    assert len(report["input"]["sha256"]) == 64
    with pytest.raises(ValueError, match="immutable"):
        run(path, out)


@pytest.mark.parametrize("data", [protocol(clock="UTC"), protocol(cases=[]), protocol(extra=True)])
def test_undeclared_scope_is_not_silently_accepted(tmp_path, data):
    path, out = tmp_path / "input.json", tmp_path / "output.json"
    save(path, data)
    with pytest.raises(ValueError):
        run(path, out)
    assert not out.exists()


def test_failed_or_incomplete_case_remains_in_denominator(tmp_path):
    path, out = tmp_path / "input.json", tmp_path / "output.json"
    data = protocol()
    case = dict(data["cases"][0], case_id="hung", jobs=[asdict(job("hung", service=None, deadline=10))])
    data["cases"].append(case)
    save(path, data)
    report = run(path, out)
    assert report["expected_cases"] == len(report["cases"]) == 2
    assert report["cases"][1]["result"]["status"] == "blocked"
