from copy import deepcopy
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from research.causal_comparison import SequenceEvent
from research.risk_trajectory import compare_risk
from tests.test_gold_exit_deals import _fixture
from tests.test_risk_trajectory import SPEC
from tools.compare_risk_trajectories import compare, paired_quotes
from tools.run_causal_controls import digest, save


@pytest.fixture
def archive(tmp_path):
    study, analysis = tmp_path / "study", tmp_path / "analysis"
    study.mkdir()
    analysis.mkdir()
    actual, _ = _fixture()
    actual["account_currency"] = "EUR"
    actual["positions"][0]["open_deal"]["comment"] = "c2_1_g55"
    ledger = {"signals": [actual], "account_currency": "EUR"}
    save(analysis / "observed_ledgers.json", ledger)
    save(analysis / "analysis_manifest.json", {
        "source_capture_manifest_sha256": "a" * 64,
        "artifacts": [{"name": "observed_ledgers.json", "sha256": digest(analysis / "observed_ledgers.json"),
                       "bytes": (analysis / "observed_ledgers.json").stat().st_size}],
    })
    start = pd.Timestamp(actual["positions"][0]["open_dt_utc"])
    tapes = {}
    for symbol, bids, asks in (("XAUUSD", [100.2, 99.2, 100.2], [100.4, 99.4, 100.4]),
                               ("EURUSD", [1, 1, 1], [1, 1, 1])):
        times = pd.date_range(start, periods=3, freq="s")
        frame = pd.DataFrame({"time_utc": times, "source_time_msc": times.as_unit("ms").asi8,
                              "bid": bids, "ask": asks})
        path = study / (symbol + ".parquet")
        frame.to_parquet(path)
        tapes[symbol] = {"path": str(path), "sha256": digest(path)}
    metadata_sha = save(study / "input_diagnostics.json", {"signals": [{"signal_id": "canal2_1", "direction": "BUY"}]})
    protocol_sha = save(study / "protocol.json", {
        "contract": "raw_message_control_diagnostic_v2", "account_currency": "EUR",
        "input_diagnostics_sha256": metadata_sha, "source_capture_manifest_sha256": "a" * 64,
        "expected_signal_ids": ["canal2_1"], "tapes": tapes,
        "execution_scenarios": [{"latency_ms": 0, "entry_fill_latency_ms": 0}],
        "currency_digits": 2, "contract_size": 100, "fx_max_age_ms": 5000,
        "broker_epoch_offset_seconds": 0, "admission_blockers": ["not_certified"],
        "start_utc": start.isoformat(), "cutoff_utc": (start + pd.Timedelta(seconds=2)).isoformat(),
    })
    result = {"entries": [{"ticket": "sim_1", "source": "signal_market", "opened_at": start,
                            "entry_price": "100.20", "volume": ".04"}],
              "exits": [{"ticket": "sim_1", "closed_at": start + pd.Timedelta(seconds=i),
                          "entry_price": "100.20", "exit_price": "100.70", "volume": vol,
                          "pnl_eur": net, "reason": "initial_tp"}
                         for i, vol, net in ((1, ".01", ".50"), (2, ".03", "1.50"))], "blockers": []}
    save(study / "independent_results.json", {"protocol_sha256": protocol_sha, "results": [
        {"signal_id": "canal2_1", "latency_ms": 0, "entry_fill_latency_ms": 0,
         "engine_mismatches": {"fast": [], "scalar": []}, "result": result}]})
    return study, analysis


def test_archived_control_reconciles_full_risk_without_claiming_certification(archive, tmp_path):
    study, analysis = archive
    output = tmp_path / "result.json"
    report = compare(study, analysis, output, max_market_gap_ms=5000)
    result = report["rows"][0]["comparison"]
    assert result["status"] == "exact_sampled_path_only"
    assert result["observed"]["metrics"]["minimum_from_origin"] == Decimal("-2.50")
    assert report["full_live_parity_verified"] is False
    assert report["inherited_admission_blockers"] == ["not_certified"]
    assert report["expected_controls"] == 1
    assert len(result["observed"]["samples_sha256"]) == len(result["simulated"]["samples_sha256"]) == 64
    with pytest.raises(ValueError, match="immutable"):
        compare(study, analysis, output, max_market_gap_ms=5000)


def test_native_source_mutation_is_rejected_not_reconciled_from_changed_totals(archive, tmp_path):
    study, analysis = archive
    with (analysis / "observed_ledgers.json").open("ab") as stream:
        stream.write(b" ")
    with pytest.raises(ValueError, match="source binding"):
        compare(study, analysis, tmp_path / "result.json", max_market_gap_ms=5000)


def test_deal_boundary_uses_new_causal_fx_not_fx_at_previous_market_tick():
    base = pd.Timestamp("2026-09-01T09:00:00Z")
    times = np.array([base.value, (base + pd.Timedelta(seconds=2)).value])
    fx_times = np.array([base.value, (base + pd.Timedelta(seconds=1)).value])
    event = SequenceEvent(1, "entry", base + pd.Timedelta(seconds=1), "BUY",
                          Decimal(101), Decimal(1), Decimal(0), "market")
    tape = paired_quotes((times, np.array([[100, 100], [100, 100]])),
                         (fx_times, np.array([[1, 1], [2, 2]])), [event],
                         start=base, end=base + pd.Timedelta(seconds=2))
    result = compare_risk([event], [], tape, spec=SPEC)
    assert result["observed"]["samples"][1]["floating"] == Decimal(-50)
    assert result["observed"]["samples"][1]["quote_at"] == base


@pytest.mark.parametrize("channel", ["canal1", "canal2"])
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_existing_native_adapter_and_shared_risk_work_for_both_channels(channel, direction):
    from tools.compare_causal_controls import observed_events
    from tests.test_risk_trajectory import quote

    actual, _ = _fixture(direction)
    actual["sig_id"] = channel + "_1"
    actual["channel"] = channel
    actual["positions"][0]["open_deal"]["comment"] = "c1_1_dv1" if channel == "canal1" else "c2_1_g55"
    before = deepcopy(actual)
    events = observed_events(actual)
    report = compare_risk(events, events, [quote(0, 100, 100.2), quote(1, 99, 99.2), quote(2, 100, 100.2)], spec=SPEC)
    assert report["status"] == "exact_sampled_path_only"
    assert actual == before
