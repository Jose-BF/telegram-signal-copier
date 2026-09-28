from copy import deepcopy

import numpy as np
import pytest

from research.dubai_receipt_controls import receipt_trigger, load_receipt_path, RunBudget, summarize_subset
from research.dubai_receipt_coverage import window_row, CoverageBudget
from research.dubai_paired_clocks import fixed_controls
from research.dubai_iterative.contracts import StrategyGenome
from tests.test_dubai_receipt_coverage import entry, complete_fixture
from tests.test_dubai_annual_dataset import annual_case
from tests.test_dubai_entry_probe import probe_case
from tests.test_strategy_study import case


def legacy(**changes):
    return entry(raw_chat_id=None, raw_message_revision_id=None, numeric_chat_observed=False,
        canonical_revision_observed=False, first_source_line_1based=1, retained_revision_is_edit=False, **changes)


def test_legacy_trigger_does_not_manufacture_canonical_revision_or_chat():
    value = legacy()
    original = deepcopy(value)
    trigger = receipt_trigger(value)
    assert trigger.signal_id == "receipt:canal1_1" and trigger.channel == "canal1"
    assert trigger.provider_events == ()
    assert not hasattr(trigger, "message_revision_id") and not hasattr(trigger, "chat_id")
    assert trigger.source_evidence_tier == "legacy_channel_tag_only"
    assert value == original


@pytest.mark.parametrize("changes", [
    {"initial_receipt_supported": False}, {"retained_revision_is_edit": True},
    {"direction": "HOLD"}, {"message_id": True}, {"signal_id": "canal2_1"},
    {"received_utc": "2026-01-05T11:59:59Z"}, {"first_source_row_sha256": "x"},
    {"source_evidence_tier": "canonical_chat_revision"},
    {"raw_message_revision_id": "invented"}, {"raw_chat_id": -1001642806869},
])
def test_receipt_adapter_rejects_inconsistent_input(changes):
    row = legacy()
    row.update(changes)
    with pytest.raises(ValueError):
        receipt_trigger(row)


def test_future_export_changes_do_not_change_trigger():
    row = legacy()
    a = receipt_trigger(row)
    row.update(export_crosswalk=[{"direction": "SELL", "date": "2099-01-01"}],
               history_diagnostics=["later_edit"], provider_events=[{"kind": "CLOSE_ALL"}])
    assert receipt_trigger(row) == a


def test_intermediate_receipt_keeps_observed_chat_without_claiming_revision():
    row = legacy()
    row.update(source_evidence_tier="legacy_observed_chat_without_revision",
               numeric_chat_observed=True, raw_chat_id=-1001642806869)
    trigger = receipt_trigger(row)
    assert trigger.source_evidence_tier == "legacy_observed_chat_without_revision"
    assert row["raw_message_revision_id"] is None and not hasattr(trigger, "revision_id")


def test_prior_comparison_checks_events_not_only_profit():
    from research.dubai_receipt_controls import compare_prior
    old = dict(case_id="receipt:canal1_1", control="market", profile="reference",
        strategy_fingerprint="a" * 64, status="simulated", reasons=[], mismatches={},
        engines={"oracle": {"pnl_eur": "1.00", "events": [{"time": 1}]}})
    new = deepcopy(old)
    new["engines"]["oracle"]["events"][0]["time"] = 2
    with pytest.raises(ValueError, match="exact prior row"):
        compare_prior([new], [old])


def test_receipt_path_matches_prior_path_without_canonical_fabrication(annual_case):
    from research.dubai_annual_dataset import AnnualInputs
    from research.dubai_paired_clocks import _load_path
    inputs = AnnualInputs(*annual_case()[:3])
    row = legacy()
    expected = window_row(row, 1200, inputs, CoverageBudget())
    base = StrategyGenome.from_dict(fixed_controls()["market"])
    path, audit = load_receipt_path(row, expected, inputs, base, RunBudget())
    old, unused = _load_path(dict(case_id="receipt:canal1_1", clock_kind="raw_receipt",
        direction="BUY", trigger_utc=row["received_utc"], published_utc=row["published_utc"],
        revision_id="observed-only-in-old-fixture"), inputs, base)
    assert audit["status"] == "data_ready" and path.provider_events == ()
    for name in ("times_ns", "bid", "ask", "fx_bid", "fx_ask", "fx_age_ms", "fx_valid"):
        np.testing.assert_array_equal(getattr(path, name), getattr(old, name))
    assert path.signal_id == old.signal_id
    assert path.market_evidence == old.market_evidence


def test_unsupported_receipt_never_builds_market_path(annual_case, monkeypatch):
    from research.dubai_annual_dataset import AnnualInputs
    inputs = AnnualInputs(*annual_case()[:3])
    row = legacy(initial_receipt_supported=False, fresh_initial_receipt_within_5s=False,
                 entry_reasons=["publication_after_receipt"])
    expected = window_row(row, 1200, inputs, CoverageBudget())
    monkeypatch.setattr(inputs.raw, "window", lambda *_: pytest.fail("decoded blocked receipt"))
    path, audit = load_receipt_path(row, expected, inputs,
        StrategyGenome.from_dict(fixed_controls()["market"]), RunBudget())
    assert path is None and audit["status"] == "data_blocked"


def test_coverage_difference_fails_before_engine(annual_case):
    from research.dubai_annual_dataset import AnnualInputs
    inputs = AnnualInputs(*annual_case()[:3])
    row = legacy()
    expected = window_row(row, 1200, inputs, CoverageBudget())
    expected["coverage"]["market_quotes_in_horizon"] += 1
    with pytest.raises(ValueError, match="frozen receipt coverage"):
        load_receipt_path(row, expected, inputs, StrategyGenome.from_dict(fixed_controls()["market"]), RunBudget())


def sample_result(mid, stamp, status, pnl=None, volume=None):
    return {"signal_id": f"canal1_{mid}", "received_utc": stamp, "status": status,
        "engines": {} if pnl is None else {"oracle": {"pnl_eur": pnl, "filled_volume": volume,
            "max_floating_drawdown_eur": "0.00"}}}


def test_summary_preserves_unknown_rows_and_complete_day_denominator():
    rows = [sample_result(1, "2026-01-01T12:00:00Z", "simulated", "2.00", .01),
        sample_result(2, "2026-01-01T13:00:00Z", "data_blocked"),
        sample_result(3, "2026-01-02T12:00:00Z", "unfilled", "0.00", 0)]
    summary = summarize_subset(rows)
    assert summary["retained"] == 3 and summary["evaluable"] == 2
    assert summary["hypothetical_known_net_after_cost_eur"] == "1.90"
    assert summary["complete_days"] == 1 and summary["retained_days"] == 2
    assert summary["complete_day_net_after_cost_eur"] == "0.00"
    assert summarize_subset(rows[1:2])["hypothetical_known_net_after_cost_eur"] is None
    assert summarize_subset(rows[::-1]) == summary


def test_source_and_engine_budgets_fail_before_increment():
    budget = RunBudget()
    budget.source_quotes = 150_000_000
    with pytest.raises(ValueError):
        budget.charge_source(1)
    assert budget.source_quotes == 150_000_000
    budget.quote_visits = 150_000_000
    with pytest.raises(ValueError):
        budget.charge(1)
    assert budget.evaluations == 0


def full_fixture(annual_case, tmp_path, monkeypatch):
    from research.dubai_receipt_coverage import run_coverage
    from research.dubai_paired_clocks import run_controls
    from research.strategy_study import _read
    receipts, coverage, raw, receipt_coverage = complete_fixture(annual_case, tmp_path, monkeypatch)
    run_coverage(receipts, coverage, raw, receipt_coverage)
    prior = tmp_path / "prior_controls"
    run_controls(_read(receipts / "protocol.json")["prior_audit_dir"], coverage, raw, prior)
    return receipt_coverage, prior, tmp_path / "receipt_controls"


def test_complete_archive_three_engines_old_rows_and_fresh_verifier(annual_case, tmp_path, monkeypatch):
    from research import dubai_receipt_controls as control
    args = full_fixture(annual_case, tmp_path, monkeypatch)
    evaluate = control._evaluate
    def frozen_first(*values, **kwargs):
        assert (args[-1] / "protocol.json").is_file()
        return evaluate(*values, **kwargs)
    monkeypatch.setattr(control, "_evaluate", frozen_first)
    summary = control.run_controls(*args)
    assert summary["retained_identities"] == 1 and summary["evaluation_rows"] == 8
    assert summary["usage"]["evaluations"] == 24
    assert summary["prior_exact_match"]["matched_rows"] == 8
    assert summary["selection"]["selected_policy"] is None
    assert control.verify_controls(args[-1])["status"] == "verified_expanded_fixed_controls_only"
    with pytest.raises(ValueError, match="immutable"):
        control.run_controls(*args)


@pytest.mark.parametrize("issue", ["source", "missing", "engine", "summary", "missing_result", "duplicate_result"])
def test_changed_source_incomplete_or_resealed_results_fail(annual_case, tmp_path, monkeypatch, issue):
    from research.dubai_receipt_controls import run_controls, verify_controls
    from research.strategy_study import _read, _encode, _digest, _sha
    from research.dubai_entry_probe import _rows
    args = full_fixture(annual_case, tmp_path, monkeypatch)
    run_controls(*args)
    output = args[-1]
    if issue == "source":
        source = args[0] / "summary.json"
        source.write_bytes(source.read_bytes() + b"\n")
    elif issue == "missing":
        (output / "inventory.json").unlink()
    else:
        name = "summary.json" if issue == "summary" else "results.jsonl"
        target = output / name
        if issue != "summary":
            rows = _rows(target)
            if issue == "engine":
                rows[0]["engines"]["scalar"]["pnl_eur"] = "12345.00"
            elif issue == "missing_result":
                rows.pop()
            else:
                rows.append(deepcopy(rows[0]))
            target.write_bytes(b"".join(_encode(r) for r in rows))
        else:
            row = _read(target)
            row["retained_identities"] += 1
            target.write_bytes(_encode(row))
        manifest = _read(output / "manifest.json")
        manifest["artifacts"][name]["sha256"] = _digest(target)
        del manifest["receipt_controls_identity_sha256"]
        manifest["receipt_controls_identity_sha256"] = _sha(manifest)
        (output / "manifest.json").write_bytes(_encode(manifest))
    with pytest.raises(ValueError):
        verify_controls(output)
