from dataclasses import replace
import json

import pytest

from research.dubai_iterative.shared_replay import simulate_shared, summarize_shared_risk
from research.shared_risk_reducer import SharedRiskOnlineReducer
from research.shared_risk_trace import (
    SharedRiskTraceWriter, iter_shared_risk_trace,
)
from tests.test_shared_policy_replay import PROFILE
from tests.test_shared_risk_grid import gap_spec


@pytest.mark.parametrize("unsupported", [False, True])
def test_durable_trace_round_trip_preserves_every_risk_event(tmp_path, unsupported):
    item = gap_spec()
    specs = [item]
    if unsupported:
        specs.append(replace(item, channel="canal2",
                             path=replace(item.path, signal_id="canal2_unknown"),
                             genome=item.genome.with_change(entry_mode="actual_mt5")))
    retained = simulate_shared(specs, profile=PROFILE)
    directory = tmp_path / "trace"
    with SharedRiskTraceWriter(directory) as writer:
        streamed = simulate_shared(specs, profile=PROFILE, risk_sink=writer,
                                   retain_risk=False)
        manifest = writer.finish(streamed)

    assert manifest["risk_point_count"] == len(retained.risk)
    assert manifest["risk_grid_count"] == len(retained.risk_grid)
    assert manifest["source_binding"] == "external_required_for_historical_claims"
    events = list(iter_shared_risk_trace(directory))
    assert tuple(value for kind, value in events if kind == "point") == retained.risk
    assert tuple(value for kind, value in events if kind == "frame") == retained.risk_grid
    reducer = SharedRiskOnlineReducer()
    for kind, value in events:
        if kind == "point":
            reducer.record_point(value)
        else:
            reducer.record_frame(value)
    actual = reducer.finalize(streamed)
    expected = summarize_shared_risk(retained)
    for key in ("expected_scopes", "metrics", "known_sample_metrics",
                "final_total_minor", "blockers"):
        assert actual[key] == expected[key]


def test_incomplete_trace_is_not_readable(tmp_path):
    directory = tmp_path / "interrupted"
    with SharedRiskTraceWriter(directory):
        pass
    with pytest.raises(FileNotFoundError):
        list(iter_shared_risk_trace(directory))
    with pytest.raises(FileExistsError):
        SharedRiskTraceWriter(directory)


def test_modified_trace_fails_integrity_check(tmp_path):
    directory = tmp_path / "trace"
    with SharedRiskTraceWriter(directory) as writer:
        report = simulate_shared([gap_spec()], profile=PROFILE, risk_sink=writer,
                                 retain_risk=False)
        writer.finish(report)
    with (directory / "risk.jsonl.gz").open("ab") as stream:
        stream.write(b"modified")
    with pytest.raises(ValueError, match="integrity"):
        list(iter_shared_risk_trace(directory))


def test_trace_manifest_count_change_is_detected(tmp_path):
    directory = tmp_path / "trace"
    with SharedRiskTraceWriter(directory) as writer:
        report = simulate_shared([gap_spec()], profile=PROFILE, risk_sink=writer,
                                 retain_risk=False)
        writer.finish(report)
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["risk_point_count"] += 1
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="count mismatch"):
        list(iter_shared_risk_trace(directory))
