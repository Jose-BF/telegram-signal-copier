import json

import pytest

from research import dubai_annual_dataset as annual
from research.dubai_annual_coverage import encoded
from research.strategy_study import _digest, _sha
from tests.test_dubai_entry_probe import probe_case
from tests.test_strategy_study import case


@pytest.fixture
def annual_case(probe_case):
    def build(**kwargs):
        args = probe_case(**kwargs)
        folder = args[0]
        (folder / "rolling_folds.jsonl").write_bytes(b"")
        manifest = json.loads((folder / "manifest.json").read_text())
        manifest["artifacts"]["rolling_folds.jsonl"] = {"sha256": _digest(folder / "rolling_folds.jsonl")}
        manifest["stream_identity_sha256"] = _sha({k: v for k, v in manifest.items() if k != "stream_identity_sha256"})
        (folder / "manifest.json").write_bytes(encoded(manifest))
        return args
    return build


def test_inventory_keeps_both_scenarios_without_decoding_prices(annual_case, monkeypatch):
    args = annual_case(blocked=True)
    import pandas as pd
    monkeypatch.setattr(pd, "read_parquet", lambda *_args, **_kwargs: pytest.fail("inventory must be lazy"))
    inputs = annual.AnnualInputs(*args[:3])
    inventory = inputs.inventory()
    assert inventory["scenarios"]["revision_time"]["triggers"] == 2
    assert inventory["scenarios"]["publication_initial"]["triggers"] == 1
    assert inventory["scenarios"]["revision_time"]["horizons"]["1200"]["data_ready"] == 1
    assert not inventory["money_contract_verified"]


def test_daily_bridge_keeps_blocked_ids_in_existing_dataset(annual_case):
    inputs = annual.AnnualInputs(*annual_case(blocked=True)[:3])
    day = inputs.load_day("revision_time", "2026-01-05", 1200)
    dataset = day.as_strategy_dataset()
    assert dataset.eligible_signal_ids == ("main", "blocked")
    assert [p.signal_id for p in dataset.paths] == ["main"]
    assert not day.complete
    assert day.rows[1]["status"] == "data_blocked"
    assert any("blocked" in values for values in dataset.exclusions.values())
    assert not dataset.actual_evidence_signal_ids
    inputs.verify_sources()


def test_day_budget_is_checked_before_decoding_or_truncation(annual_case, monkeypatch):
    inputs = annual.AnnualInputs(*annual_case()[:3])
    monkeypatch.setattr(inputs.raw, "day", lambda *_: pytest.fail("unexpected quote decode"))
    with pytest.raises(ValueError, match="budget"):
        inputs.load_day("revision_time", "2026-01-05", 1200, max_path_quotes=1)


def test_day_revalidates_coverage_and_canonical_tape(annual_case):
    inputs = annual.AnnualInputs(*annual_case()[:3])
    day = inputs.load_day("revision_time", "2026-01-05", 1200)
    assert day.complete
    assert day.portfolio_tape.blockers == ()
    assert len(day.paths[0].times_ns) == 1201
    assert day.paths[0].signal_observed_at.isoformat() == "2026-01-05T12:00:00+00:00"


def test_coverage_mismatch_never_enters_the_dataset(annual_case):
    inputs = annual.AnnualInputs(*annual_case(wrong_coverage=True)[:3])
    with pytest.raises(ValueError, match="coverage revalidation"):
        inputs.load_day("revision_time", "2026-01-05", 1200)


@pytest.mark.parametrize("scenario,horizon", [("observed_receipt", 1200), ("revision_time", 86400)])
def test_unsupported_clock_or_horizon_not_invented(annual_case, scenario, horizon):
    inputs = annual.AnnualInputs(*annual_case()[:3])
    with pytest.raises(ValueError):
        inputs.load_day(scenario, "2026-01-05", horizon)


def test_modified_in_memory_inventory_is_rejected(annual_case):
    inputs = annual.AnnualInputs(*annual_case()[:3])
    inputs.triggers[0]["direction"] = "SELL"
    with pytest.raises(ValueError, match="memory"):
        inputs.verify_sources()
