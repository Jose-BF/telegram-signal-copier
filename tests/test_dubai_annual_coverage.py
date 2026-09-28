import json

import numpy as np
import pandas as pd
import pytest

from research.dubai_annual_coverage import (
    CLOCKS, RawSource, coverage_metrics, digest, encoded, msc, normalize_raw_clock,
    scenario_membership, summarize, write_audit,
)


SEGMENTS = [
    {"start_utc": "2025-12-31T00:00:00Z", "end_exclusive_utc": "2026-03-08T07:00:00Z", "utc_offset_seconds": 7200},
    {"start_utc": "2026-03-08T07:00:00Z", "end_exclusive_utc": "2026-11-01T06:00:00Z", "utc_offset_seconds": 10800},
    {"start_utc": "2026-11-01T06:00:00Z", "end_exclusive_utc": "2027-01-02T00:00:00Z", "utc_offset_seconds": 7200},
]
PROTOCOL = {"broker_clock": {"segments": SEGMENTS}, "max_market_gap_ms": 5000,
            "max_fx_age_ms": 5000, "max_fx_interval_ms": 60000}


def raw(times):
    times = np.asarray(times, dtype=np.int64)
    return pd.DataFrame({"time": times // 1000, "time_msc": times, "bid": 1.0, "ask": 1.1})


def tape(times):
    times = np.asarray(times, dtype=np.int64) * 1_000_000
    return times, np.ones(len(times)), np.full(len(times), 1.1)


@pytest.mark.parametrize("value,offset", [
    ("2026-01-02T12:30:00.123Z", 7200),
    ("2026-03-06T12:30:00.123Z", 7200),
    ("2026-03-09T12:30:00.123Z", 10800),
    ("2026-03-20T12:30:00.123Z", 10800),
    ("2026-09-11T23:30:00.123Z", 10800),
])
def test_declared_clock_preserves_milliseconds_and_duplicates(value, offset):
    moment = msc(value)
    result = normalize_raw_clock(raw([moment + offset * 1000] * 2), SEGMENTS)
    assert result[0].tolist() == [moment * 1_000_000] * 2


@pytest.mark.parametrize("value", ["2026-03-08T09:30:00Z", "2026-11-01T08:30:00Z"])
def test_nonexistent_and_ambiguous_raw_epoch_rejected(value):
    with pytest.raises(ValueError, match="ambiguously"):
        normalize_raw_clock(raw([msc(value)]), SEGMENTS)


@pytest.mark.parametrize("corruption", ["seconds", "reverse", "invalid_quote"])
def test_no_silent_unit_repair_sort_or_price_repair(corruption):
    frame = raw([msc("2026-07-27T12:00:00Z"), msc("2026-07-27T12:00:01Z")])
    if corruption == "seconds":
        frame["time_msc"] //= 1000
    elif corruption == "reverse":
        frame = frame.iloc[::-1]
    else:
        frame.loc[0, "ask"] = 0.9
    with pytest.raises(ValueError, match="invalid raw"):
        normalize_raw_clock(frame, SEGMENTS)


def test_exact_gap_limit_and_complete_horizon():
    start = msc("2026-07-27T12:00:00Z")
    quotes = tape(start + np.arange(0, 25_001, 5000))
    result = coverage_metrics(start, 20, quotes, quotes, PROTOCOL)
    assert result["quote_coverage_pass"]
    assert result["market_quotes_in_horizon"] == 5
    assert result["max_internal_market_gap_ms"] == 5000


def test_internal_gap_records_exact_incident_not_dropped():
    start = msc("2026-07-27T12:00:00Z")
    quotes = tape([start, start + 5001, start + 10_000, start + 15_000, start + 20_000])
    result = coverage_metrics(start, 20, quotes, quotes, PROTOCOL)
    assert result["coverage_reasons"] == ["market_gap_exceeds_contract"]
    assert result["first_market_gap_over_limit"]["gap_ms"] == 5001


def test_future_fx_quote_never_fills_missing_prior_quote():
    start = msc("2026-07-27T12:00:00Z")
    market = tape(start + np.arange(0, 21_000, 1000))
    conversion = tape([start + 1, start + 20_000])
    result = coverage_metrics(start, 20, market, conversion, PROTOCOL)
    assert "conversion_gap_or_stale" in result["coverage_reasons"]
    assert result["invalid_conversion_points"] == 1


def test_empty_tapes_and_missing_tail_have_explicit_reasons():
    start = msc("2026-07-27T12:00:00Z")
    result = coverage_metrics(start, 20, tape([]), tape([]), PROTOCOL)
    assert not result["quote_coverage_pass"]
    assert result["coverage_reasons"] == ["conversion_coverage_missing", "market_horizon_coverage_missing", "market_start_coverage_missing"]
    quotes = tape(start + np.arange(0, 19_001, 1000))
    assert "market_horizon_coverage_missing" in coverage_metrics(start, 20, quotes, quotes, PROTOCOL)["coverage_reasons"]


def test_server_midnight_is_outside_intraday_money_scope():
    start = msc("2026-07-27T20:59:50Z")
    quotes = tape(start + np.arange(0, 21_000, 1000))
    result = coverage_metrics(start, 20, quotes, quotes, PROTOCOL)
    assert result["coverage_reasons"] == ["overnight_outside_money_universe"]


def test_rolling_membership_uses_each_clock_without_imputing_edits():
    entries = [{"entry_id": "entry", "publication_reference_utc": "2026-02-25T23:59:59Z",
        "known_unedited_component_utc": None,
        "known_revision_or_initial_component_utc": "2026-02-26T00:01:00Z", "received_utc": None}]
    rolling = {"start": "2026-01-01T00:00:00Z", "end_exclusive": "2026-03-13T00:00:00Z",
               "development_days": 56, "check_days": 14, "horizon_seconds": 14400}
    folds, usage = scenario_membership(entries, rolling)
    assert len(folds) == 8
    assert usage[("publication_reference", "entry")]["purged_folds"] == ["rolling_01"]
    assert usage[("publication_reference", "entry")]["warmup_only"]
    assert usage[("known_revision_or_initial_component", "entry")]["check_folds"] == ["rolling_01"]
    assert usage[("known_unedited_component", "entry")]["membership_status"] == "clock_unavailable"
    assert usage[("observed_receipt", "entry")]["warmup_only"] is None


def test_summary_retains_unavailable_clocks_in_each_denominator():
    rows = [{"message_clock": clock, "horizon_seconds": 1200, "publication_reference_utc": "2026-01-02T00:00:00Z",
        "start_utc": None, "quote_coverage_pass": False, "initial_at_reference_supported": False,
        "engine_admitted": False, "coverage_reasons": ["message_clock_unavailable"], "check_folds": []} for clock in CLOCKS]
    result = summarize(rows)
    assert len(result["groups"]) == 4
    assert all(group["all_entry_hypotheses"] == 1 for group in result["groups"].values())


def test_source_midnight_and_empty_missing_days_are_not_interchangeable(tmp_path):
    directory = tmp_path / "raw"
    directory.mkdir()
    (directory / "XAUUSD").mkdir()
    contract = directory / "contract.json"
    binding = directory / "binding.json"
    contract.write_bytes(encoded({"clock_admitted": False, "engine_dataset_ready": False, "server": "demo"}))
    binding.write_bytes(encoded({"server": "demo"}))
    records = []
    for day, values in [("2026-07-27", [msc("2026-07-27T23:59:59.999Z")]),
                        ("2026-07-28", [msc("2026-07-28T00:00:00.001Z")]), ("2026-07-29", [])]:
        price = directory / "XAUUSD" / f"{day}.parquet"
        metadata = directory / "XAUUSD" / f"{day}.json"
        if values:
            raw(values).to_parquet(price, index=False)
        meta = {"symbol": "XAUUSD", "server": "demo", "source_epoch_day": day, "rows": len(values),
                "status": "raw_reads_consistent" if values else "no_ticks_returned",
                "artifact": price.name if values else None, "sha256": digest(price) if values else None}
        metadata.write_bytes(encoded(meta))
        records.append({"symbol": "XAUUSD", "source_epoch_day": day, "metadata_path": str(metadata),
            "metadata_sha256": digest(metadata), "rows": len(values), "status": meta["status"],
            **({"artifact_path": str(price), "sha256": digest(price)} if values else {})})
    audit_path = tmp_path / "raw_audit.json"
    audit_path.write_bytes(encoded({"inputs": {"raw_dir": str(directory), "contract_sha256": digest(contract),
        "binding_sha256": digest(binding)}, "raw_day_artifacts": records}))

    def watch(path, expected):
        if digest(path) != expected:
            raise ValueError("frozen hash mismatch")

    source = RawSource(audit_path, SEGMENTS, watch)
    start = msc("2026-07-27T20:59:59Z")
    quotes, days, missing = source.window("XAUUSD", start, start + 2000)
    assert quotes[0].tolist() == [(start + 999) * 1_000_000, (start + 1001) * 1_000_000]
    assert not missing
    assert days == ["XAUUSD:2026-07-27", "XAUUSD:2026-07-28"]
    assert len(source.day("XAUUSD", "2026-07-29")[0]) == 0
    assert source.window("EURUSD", start, start + 2000)[2]
    (directory / "XAUUSD" / "2026-07-27.parquet").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="hash"):
        RawSource(audit_path, SEGMENTS, watch)


def test_archive_is_immutable_and_hash_bound(tmp_path):
    result = {"summary": {}, "protocol": {}, "rows": [], "folds": [], "clock_references": [],
              "inputs": {"watched_files": {}, "protected_archive_dirs": []}, "environment": {}}
    output = tmp_path / "output"
    manifest = write_audit(result, output)
    for name, proof in manifest["artifacts"].items():
        assert digest(output / name) == proof["sha256"]
    with pytest.raises(ValueError, match="already exists"):
        write_audit(result, output)
    assert json.loads((output / "manifest.json").read_text())["engine_admitted"] is False
