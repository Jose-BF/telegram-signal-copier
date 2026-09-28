from copy import deepcopy

import pytest

from research.dubai_receipt_coverage import coverage_summary, window_row, CoverageBudget, BoundedRawSource
from tests.test_dubai_annual_dataset import annual_case
from tests.test_dubai_entry_probe import probe_case
from tests.test_strategy_study import case


def entry(mid=1, **changes):
    row = dict(signal_id=f"canal1_{mid}", message_id=mid, received_utc="2026-01-05T12:00:00+00:00",
        published_utc="2026-01-05T12:00:00+00:00", direction="BUY", source_evidence_tier="legacy_channel_tag_only",
        initial_receipt_supported=True, fresh_initial_receipt_within_5s=True, entry_reasons=[],
        first_source_row_sha256="a" * 64)
    row.update(changes)
    return row


def test_existing_coverage_engine_checks_raw_window_without_strategy(annual_case):
    from research.dubai_annual_dataset import AnnualInputs
    inputs = AnnualInputs(*annual_case()[:3])
    result = window_row(entry(), 1200, inputs, CoverageBudget())
    assert result["status"] == "quote_window_ready"
    assert result["coverage"]["market_quotes_in_horizon"] == 1201
    assert not result["reasons"]
    longer = window_row(entry(), 3600, inputs, CoverageBudget())
    assert longer["status"] == "quote_window_blocked"
    assert "market_horizon_coverage_missing" in longer["reasons"]


def test_unsupported_receipt_does_not_decode_quotes(annual_case, monkeypatch):
    from research.dubai_annual_dataset import AnnualInputs
    inputs = AnnualInputs(*annual_case()[:3])
    monkeypatch.setattr(inputs.raw, "window", lambda *_: pytest.fail("unsupported receipt decoded prices"))
    bad = entry(initial_receipt_supported=False, fresh_initial_receipt_within_5s=False,
                entry_reasons=["first_directional_revision_is_edit"])
    row = window_row(bad, 1200, inputs, CoverageBudget())
    assert row["status"] == "receipt_unsupported" and row["coverage"] is None


def test_summary_retains_bad_receipts_and_every_horizon(annual_case):
    from research.dubai_annual_dataset import AnnualInputs
    inputs = AnnualInputs(*annual_case()[:3])
    entries = [entry(), entry(2, initial_receipt_supported=False, fresh_initial_receipt_within_5s=False,
                             entry_reasons=["publication_after_receipt"])]
    rows = [window_row(e, h, inputs, CoverageBudget()) for e in entries for h in (1200, 3600)]
    out = coverage_summary(entries, rows, horizons=(1200, 3600))
    assert out["status_counts"] == {"quote_window_ready": 1, "quote_window_blocked": 1, "receipt_unsupported": 2}
    assert out["horizons"]["1200"]["retained_identities"] == 2
    assert out["horizons"]["1200"]["fresh_receipt_quote_ready"] == 1
    assert out["horizons"]["3600"]["fresh_receipt_quote_ready"] == 0
    assert out["strategy_evaluations"] == 0 and not out["engine_dataset_ready"]


@pytest.mark.parametrize("issue", ["missing", "duplicate", "wrong_tier"])
def test_summary_fails_for_missing_or_mixed_identity(annual_case, issue):
    from research.dubai_annual_dataset import AnnualInputs
    inputs = AnnualInputs(*annual_case()[:3])
    rows = [window_row(entry(), 1200, inputs, CoverageBudget())]
    if issue == "missing": rows = []
    elif issue == "duplicate": rows *= 2
    else: rows[0]["source_evidence_tier"] = "canonical_chat_revision"
    with pytest.raises(ValueError):
        coverage_summary([entry()], rows, horizons=(1200,))


def test_quote_decode_budget_fails_before_loading_a_day():
    budget = CoverageBudget()
    budget.source_quotes = 150_000_000
    raw = object.__new__(BoundedRawSource)
    raw.budget, raw.cache = budget, {}
    raw.records = {("XAUUSD", "2026-01-05"): {"rows": 1}}
    with pytest.raises(ValueError, match="source quote budget"):
        raw.day("XAUUSD", "2026-01-05")
    assert budget.source_quotes == 150_000_000


def test_window_quote_budget_preserves_counter_on_failure():
    budget = CoverageBudget()
    budget.window_quotes = 300_000_000
    with pytest.raises(ValueError):
        budget.charge_window(1)
    assert budget.window_quotes == 300_000_000


def test_summary_independent_of_row_and_input_order(annual_case):
    from research.dubai_annual_dataset import AnnualInputs
    inputs = AnnualInputs(*annual_case()[:3])
    entries = [entry(), entry(2)]
    rows = [window_row(e, 1200, inputs, CoverageBudget()) for e in entries]
    before = deepcopy((entries, rows))
    a = coverage_summary(entries, rows, horizons=(1200,))
    b = coverage_summary(entries[::-1], rows[::-1], horizons=(1200,))
    assert a == b and (entries, rows) == before


def complete_fixture(annual_case, tmp_path, monkeypatch):
    from research import dubai_receipt_inventory as receipt
    from tests.test_dubai_paired_clocks import paired_fixture
    prior, coverage, raw_quotes, unused = paired_fixture(annual_case, tmp_path)
    # The synthetic quote archive covers January; production dates stay fixed.
    monkeypatch.setattr(receipt, "PERIOD", {"start_utc": "2026-01-01T00:00:00Z",
        "canonical_start_utc": "2026-01-01T00:00:00Z", "end_exclusive_utc": "2026-02-01T00:00:00Z"})
    receipts = tmp_path / "receipt_inventory"
    receipt.run_inventory(prior, receipts)
    return receipts, coverage, raw_quotes, tmp_path / "receipt_coverage"


def test_complete_archive_freezes_before_quote_decode_and_verifies(annual_case, tmp_path, monkeypatch):
    import pandas as pd
    from research.dubai_receipt_coverage import run_coverage, verify_coverage
    args = complete_fixture(annual_case, tmp_path, monkeypatch)
    read = pd.read_parquet
    def check_freeze(*values, **kwargs):
        assert (args[-1] / "protocol.json").is_file()
        return read(*values, **kwargs)
    monkeypatch.setattr(pd, "read_parquet", check_freeze)
    summary = run_coverage(*args)
    assert summary["window_rows"] == 4
    assert summary["horizons"]["1200"]["quote_window_ready"] == 1
    assert summary["horizons"]["14400"]["quote_window_ready"] == 0
    assert summary["usage"]["source_quotes_decoded"] == 2404
    assert verify_coverage(args[-1])["status"] == "verified_receipt_quote_coverage_only"
    with pytest.raises(ValueError, match="immutable"):
        run_coverage(*args)


@pytest.mark.parametrize("issue", ["source_changed", "missing", "coverage_resealed"])
def test_incomplete_or_modified_coverage_never_verifies(annual_case, tmp_path, monkeypatch, issue):
    from research.dubai_receipt_coverage import run_coverage, verify_coverage
    from research.strategy_study import _read, _encode, _digest, _sha
    args = complete_fixture(annual_case, tmp_path, monkeypatch)
    run_coverage(*args)
    output = args[-1]
    if issue == "source_changed":
        args[2].write_bytes(args[2].read_bytes() + b"\n")
    elif issue == "missing":
        (output / "coverage.json").unlink()
    else:
        target = output / "coverage.json"
        rows = _read(target)
        rows[0]["coverage"]["market_quotes_in_horizon"] += 1
        target.write_bytes(_encode(rows))
        manifest = _read(output / "manifest.json")
        manifest["artifacts"][target.name]["sha256"] = _digest(target)
        del manifest["receipt_coverage_identity_sha256"]
        manifest["receipt_coverage_identity_sha256"] = _sha(manifest)
        (output / "manifest.json").write_bytes(_encode(manifest))
    with pytest.raises(ValueError):
        verify_coverage(output)
