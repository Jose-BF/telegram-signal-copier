from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pytest

from research.causal_comparison import SequenceEvent, compare_sequences
from research.causal_replay import time_ns
from research.risk_trajectory import RiskSpec
from tools import compare_week_causal_risk as week_risk
from tools import run_week_causal_controls as weekly
from tools.run_causal_controls import encode


def _case(*, entry_price=100, conversion_time=None):
    start = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)
    moments = [start + timedelta(seconds=index) for index in range(3)]
    observed = [SequenceEvent(1, "entry", moments[0], "BUY", 100, "0.01", 0, "market"),
                SequenceEvent(1, "exit", moments[2], "BUY", 101, "0.01", "1.00", "tp")]
    replay = [SequenceEvent(1, "entry", moments[0], "BUY", entry_price, "0.01", 0, "market"),
              SequenceEvent(1, "exit", moments[2], "BUY", 101, "0.01", "1.00", "tp")]
    sequence = compare_sequences(observed, replay)
    row = {"signal_id": "canal2_1", "channel": "canal2", "scenario": {"latency_ms": 0},
           "comparison": asdict_sequence(sequence)}
    stamps = np.asarray([time_ns(moment) for moment in moments], dtype=np.int64)
    market = stamps, np.asarray([[100, 100], [98, 98], [101, 101]], dtype=float)
    fx_at = conversion_time or moments[0]
    conversion = (np.asarray([time_ns(fx_at)], dtype=np.int64),
                  np.asarray([[1.0, 1.0]], dtype=float))
    spec = RiskSpec("EUR", 2, Decimal(100), "account_base_profit_quote", 5000, 5000)
    return row, market, conversion, spec


def asdict_sequence(value):
    # The persisted sequence report serializes datetimes and Decimals to strings.
    from tools.run_causal_controls import encode
    import json
    return json.loads(encode(value))


def test_week_risk_matches_complete_common_path_and_detects_entry_price_delta():
    row, market, conversion, spec = _case()
    exact = week_risk.risk_row(row, market, conversion, spec)
    assert exact["comparison"]["status"] == "exact_sampled_path_only"
    assert exact["comparison"]["drawdown_delta_eur"] == "0.00"
    assert exact["comparison"]["common_tick_and_event_marks"] == 3

    changed, market, conversion, spec = _case(entry_price=100.5)
    mismatch = week_risk.risk_row(changed, market, conversion, spec)
    assert mismatch["comparison"]["status"] == "mismatch"
    assert mismatch["comparison"]["floating_difference_marks"] > 0
    assert mismatch["comparison"]["same_exposure_floating_difference_marks"] > 0
    assert mismatch["comparison"]["drawdown_delta_eur"] is not None


def test_week_risk_preserves_upstream_blocker_and_strict_fx_gap():
    row, market, conversion, spec = _case()
    row["comparison"] = {"status": "blocked", "blockers": ["no_native_basket_observed"]}
    blocked = week_risk.risk_row(row, market, conversion, spec)
    assert blocked["comparison"]["status"] == "blocked"
    assert "no_native_basket_observed" in blocked["comparison"]["blockers"][0]

    row, market, conversion, _ = _case()
    spec = RiskSpec("EUR", 2, Decimal(100), "account_base_profit_quote", 500, 5000)
    stale = week_risk.risk_row(row, market, conversion, spec)
    assert stale["comparison"]["status"] == "blocked"
    assert "stale_conversion_quote" in stale["comparison"]["blockers"]


def test_week_risk_blocks_open_position_instead_of_reporting_drawdown():
    row, market, conversion, spec = _case()
    row["comparison"]["comparisons"] = [item for item in row["comparison"]["comparisons"]
                                            if item["kind"] == "entry"]
    row["comparison"]["observed_event_count"] = 1
    row["comparison"]["simulated_event_count"] = 1
    outcome = week_risk.risk_row(row, market, conversion, spec)
    assert outcome["comparison"]["status"] == "blocked"
    assert "open or overclosed" in outcome["comparison"]["blockers"][0]


def test_week_risk_keeps_verified_empty_fill_path_without_claiming_decision_parity():
    row, market, conversion, spec = _case()
    row["comparison"] = {"status": "exact_facts_only", "comparisons": [],
                         "observed_event_count": 0, "simulated_event_count": 0}
    outcome = week_risk.risk_row(row, market, conversion, spec)
    assert outcome["comparison"]["status"] == "exact_empty_fill_path_only"
    assert outcome["comparison"]["drawdown_delta_eur"] == "0.00"
    assert outcome["comparison"]["decision_sequence_verified"] is False


def test_week_risk_assembly_rejects_missing_scenario_and_source_hash(tmp_path, monkeypatch):
    inputs = [tmp_path / f"input_{index}.json" for index in range(3)]
    for path in inputs:
        path.write_text("{}", encoding="utf-8")
    hashes = {str(path): week_risk.digest(path) for path in inputs}
    signal_id = "canal2_1"
    protocol = {"raw_signal_ids": [signal_id], "raw_semantics_complete": False,
                "clock_admitted": False}
    sequence = {"native_baskets_missing_from_raw": [],
                "raw_signals_without_native_basket": []}
    monkeypatch.setattr(week_risk, "_inputs", lambda *args: (
        tuple(inputs), hashes, protocol, sequence))
    rows = [{"signal_id": signal_id, "scenario": scenario,
             "comparison": {"status": "blocked", "blockers": ["test"]}}
            for scenario in weekly.SCENARIOS]
    fragment = {"contract": week_risk.CONTRACT, "signal_ids": [signal_id],
                "batch_index": 0, "expected_batch_count": 1,
                "scenario_count": len(weekly.SCENARIOS), "inputs_sha256": hashes,
                "risk_scope": "modeled_per_basket_common_tick_grid",
                "raw_semantics_complete": False, "source_clock_admitted": False,
                "full_live_parity_verified": False,
                "observed_account_equity_compared": False,
                "shared_account_drawdown_compared": False,
                "sources_sha256": {name: week_risk.digest(week_risk.ROOT / name)
                                   for name in week_risk.SOURCES},
                "rows": rows, "statuses": {"blocked": len(rows)}}
    fragment_path = tmp_path / "fragment.json"
    fragment_path.write_bytes(encode(fragment))
    output = tmp_path / "assembled.json"
    result = week_risk.assemble(*inputs, [fragment_path], output)
    assert result["rows"] == len(weekly.SCENARIOS)
    assert result["statuses"] == {"blocked": len(weekly.SCENARIOS)}
    assembled = week_risk._source(output)
    assert assembled["raw_semantics_complete"] is False
    assert assembled["source_clock_admitted"] is False

    fragment["rows"] = rows[:-1]
    fragment["statuses"] = {"blocked": len(rows) - 1}
    fragment_path.write_bytes(encode(fragment))
    with pytest.raises(ValueError, match="identity or rows"):
        week_risk.assemble(*inputs, [fragment_path], tmp_path / "missing.json")
    fragment["rows"] = rows
    fragment["statuses"] = {"blocked": len(rows)}
    fragment["sources_sha256"] = {}
    fragment_path.write_bytes(encode(fragment))
    with pytest.raises(ValueError, match="identity or rows"):
        week_risk.assemble(*inputs, [fragment_path], tmp_path / "source.json")
