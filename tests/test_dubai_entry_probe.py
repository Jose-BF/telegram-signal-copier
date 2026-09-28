from copy import deepcopy
import json

import numpy as np
import pandas as pd
import pytest

from research import dubai_entry_probe as probe
from research.dubai_annual_coverage import coverage_metrics, encoded, msc
from research.dubai_entry_probe import budget_reasons, compare_results, select_days
from tests.test_strategy_study import BASE, case, digest, write_json


def trigger(key, stamp, scenario="revision_time"):
    return {"trigger_id": key, "trigger_utc": stamp, "scenario": scenario}


def test_first_complete_day_per_month_keeps_every_case_and_both_clocks():
    rows = [trigger("later", "2026-01-05T12:00:00Z"),
            trigger("b", "2026-01-02T13:00:00Z"), trigger("a", "2026-01-02T12:00:00Z"),
            trigger("initial", "2026-01-02T14:00:00Z", "publication_initial"),
            trigger("only_initial", "2026-02-01T12:00:00Z", "publication_initial"),
            trigger("feb", "2026-02-02T12:00:00Z"), trigger("partial", "2026-03-01T09:00:00Z")]
    before = deepcopy(rows)
    days, selected = select_days(rows, start_utc="2026-01-01T00:00:00Z",
                                end_exclusive_utc="2026-03-01T12:00:00Z")
    assert days == ["2026-01-02", "2026-02-02"]
    assert [r["trigger_id"] for r in selected] == ["a", "b", "initial", "feb"]
    assert rows == before


def test_selection_ignores_coverage_outcomes_and_input_order():
    rows = [trigger("first", "2026-01-02T12:00:00Z"), trigger("later", "2026-01-03T12:00:00Z")]
    kwargs = {"start_utc": "2026-01-01T00:00:00Z", "end_exclusive_utc": "2026-02-01T00:00:00Z"}
    days, selected = select_days(rows, **kwargs)
    rows[0].update(quote_coverage_pass=False, pnl_eur=-100)
    rows[1].update(quote_coverage_pass=True, pnl_eur=100)
    other_days, other = select_days(rows[::-1], **kwargs)
    assert other_days == days
    assert [r["trigger_id"] for r in other] == [r["trigger_id"] for r in selected] == ["first"]


@pytest.mark.parametrize("issue", ["duplicate", "unknown_scenario", "naive", "outside"])
def test_selection_fails_closed_for_mixed_or_ambiguous_inventory(issue):
    rows = [trigger("first", "2026-01-02T12:00:00Z")]
    if issue == "duplicate":
        rows += rows
    elif issue == "unknown_scenario":
        rows[0]["scenario"] = "receipt"
    elif issue == "naive":
        rows[0]["trigger_utc"] = "2026-01-02T12:00:00"
    else:
        rows[0]["trigger_utc"] = "2025-12-31T12:00:00Z"
    with pytest.raises(ValueError):
        select_days(rows, start_utc="2026-01-01T00:00:00Z", end_exclusive_utc="2026-02-01T00:00:00Z")


def test_results_compare_money_times_volume_and_blockers_not_implementation_digest():
    reference = {"blockers": [], "unfilled": False, "behavior_digest": "oracle",
                 "net_profit": "1.20", "exit_time": "2026-01-02T12:01:00Z", "volume": "0.01"}
    engines = {name: dict(reference, behavior_digest=name) for name in ("scalar", "fast", "oracle")}
    assert compare_results(engines) == ("simulated", [], {"scalar": [], "fast": []})
    engines["fast"]["volume"] = "0.02"
    status, reasons, mismatches = compare_results(engines)
    assert status == "engine_disagreement" and not reasons
    assert mismatches == {"scalar": [], "fast": ["volume"]}


@pytest.mark.parametrize("unfilled,blockers,status", [(True, [], "unfilled"), (False, ["bad_fx"], "engine_blocked")])
def test_engine_blocks_and_unfilled_are_not_successful_trades(unfilled, blockers, status):
    engines = {name: {"blockers": blockers, "unfilled": unfilled} for name in ("scalar", "fast", "oracle")}
    assert compare_results(engines)[0] == status


@pytest.mark.parametrize("engine", ["scalar", "fast", "oracle"])
def test_missing_engine_is_not_parity(engine):
    engines = {name: {"blockers": [], "unfilled": False} for name in ("scalar", "fast", "oracle") if name != engine}
    with pytest.raises(ValueError, match="three engines"):
        compare_results(engines)


def test_budget_checks_whole_denominator_including_all_three_engines():
    budget = {"max_signals": 64, "max_evaluations": 192, "max_path_quotes": 1_000_000,
              "max_source_quotes": 20_000_000, "max_wall_seconds": 600}
    assert not budget_reasons(64, 1_000_000, 20_000_000, budget)
    assert budget_reasons(65, 1_000_001, 20_000_001, budget) == [
        "signal_budget_exceeded", "evaluation_budget_exceeded", "path_quote_budget_exceeded", "source_quote_budget_exceeded"]
    assert budget_reasons(1, 1, 1, dict(budget, max_evaluations=2)) == ["evaluation_budget_exceeded"]


@pytest.fixture
def probe_case(case, tmp_path):
    def build(*, blocked=False, wrong_coverage=False):
        config_path, _, config = case(changes=lambda c: c.update(horizon_seconds=1200))
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()
        contract, binding = raw_dir / "contract.json", raw_dir / "binding.json"
        contract.write_bytes(encoded({"clock_admitted": False, "engine_dataset_ready": False, "server": "synthetic"}))
        binding.write_bytes(encoded({"server": "synthetic"}))
        times = msc(BASE.isoformat()) + np.arange(1202, dtype=np.int64) * 1000
        records, tapes = [], {}
        for symbol in ("XAUUSD", "EURUSD"):
            directory = raw_dir / symbol
            directory.mkdir()
            price, meta_path = directory / "2026-01-05.parquet", directory / "2026-01-05.json"
            bid = np.array([100., 100., *([103.] * 1200)]) if symbol == "XAUUSD" else np.ones(1202)
            ask = bid + .2 if symbol == "XAUUSD" else bid
            pd.DataFrame({"time": times // 1000, "time_msc": times, "bid": bid, "ask": ask}).to_parquet(price, index=False)
            meta = {"symbol": symbol, "server": "synthetic", "source_epoch_day": "2026-01-05", "rows": len(times),
                    "status": "raw_reads_consistent", "artifact": price.name, "sha256": digest(price)}
            meta_path.write_bytes(encoded(meta))
            records.append({"symbol": symbol, "source_epoch_day": "2026-01-05", "metadata_path": str(meta_path),
                "metadata_sha256": digest(meta_path), "rows": len(times), "status": meta["status"],
                "artifact_path": str(price), "sha256": digest(price)})
            tapes[symbol] = (times * 1_000_000, bid, ask)
        raw_audit = tmp_path / "raw_audit.json"
        raw_audit.write_bytes(encoded({"inputs": {"raw_dir": str(raw_dir), "contract_sha256": digest(contract),
            "binding_sha256": digest(binding)}, "raw_day_artifacts": records}))
        coverage_protocol = {"broker_clock": {"status": "declared_hypothesis", "segments": [
            {"start_utc": "2026-01-01T00:00:00Z", "end_exclusive_utc": "2026-02-01T00:00:00Z", "utc_offset_seconds": 0}]},
            "max_market_gap_ms": 1000, "max_fx_age_ms": 1000, "max_fx_interval_ms": 1000,
            "raw_audit_sha256": digest(raw_audit), "threshold_reference": {"path": str(config_path), "sha256": digest(config_path)}}

        def archive(directory, files, inputs, identity_key, extra=None):
            directory.mkdir()
            for name, data in files.items():
                (directory / name).write_bytes(data)
            manifest = {"inputs": inputs, "artifacts": {name: {"sha256": digest(directory / name)} for name in files}, **(extra or {})}
            manifest[identity_key] = probe._sha(manifest)
            (directory / "manifest.json").write_bytes(encoded(manifest))

        coverage_dir = tmp_path / "coverage"
        archive(coverage_dir, {"protocol.json": encoded(coverage_protocol)}, {"watched_files": {}}, "audit_identity_sha256")
        rows = []
        for scenario, key, stamp in [("revision_time", "main", BASE.isoformat()),
                                    ("publication_initial", "initial", BASE.isoformat()),
                                    *([("revision_time", "blocked", "2026-01-05T12:30:00Z")] if blocked else [])]:
            rows.append(dict(trigger(key, stamp, scenario), direction="BUY", chat_id=1642806869,
                received_utc=None, clock_is_hypothesis=True, causal_rule_applied=True, engine_admitted=False,
                provider_events=[], published_utc=stamp, trigger_event_id=key))
        coverage_rows = []
        for row in rows:
            metrics = coverage_metrics(msc(row["trigger_utc"]), 1200, tapes["XAUUSD"], tapes["EURUSD"], coverage_protocol)
            if wrong_coverage:
                metrics["market_quotes_in_horizon"] += 1
            coverage_rows.append({"trigger_id": row["trigger_id"], "scenario": row["scenario"],
                "trigger_utc": row["trigger_utc"], "horizon_seconds": 1200, **metrics,
                "raw_source_days": ["XAUUSD:2026-01-05", "EURUSD:2026-01-05"], "missing_raw_source_days": []})
        stream_dir = tmp_path / "stream"
        archive(stream_dir, {"triggers.jsonl": b"".join(encoded(r) for r in rows),
                "coverage.jsonl": b"".join(encoded(r) for r in coverage_rows),
                "protocol.json": encoded({"rolling_protocol": {"start": "2026-01-01T00:00:00Z", "end_exclusive": "2026-02-01T00:00:00Z"}})},
                {"watched_files": {}, "protected_archive_dirs": [], "coverage_protocol_sha256": digest(coverage_dir / "protocol.json")},
                "stream_identity_sha256", {"schema_version": "dubai_entry_stream_v1", "engine_dataset_ready": False})
        return stream_dir, coverage_dir, raw_audit, tmp_path / "probe"
    return build


def test_raw_to_three_engine_integration_freezes_before_decode_and_does_not_aggregate(probe_case, monkeypatch):
    args = probe_case()
    original_day, original_evaluate = probe.ProbeRawSource.day, probe.evaluate_path

    def day(self, *values):
        assert (args[-1] / "protocol.json").exists()
        return original_day(self, *values)

    def evaluate(*values):
        assert (args[-1] / "protocol.json").exists()
        return original_evaluate(*values)

    monkeypatch.setattr(probe.ProbeRawSource, "day", day)
    monkeypatch.setattr(probe, "evaluate_path", evaluate)
    report = probe.run_probe(*args)
    assert len(report["rows"]) == 2
    assert report["engine_evaluations"] == 6
    assert all(r["status"] == "simulated" for r in report["rows"])
    assert all(r["engines"]["oracle"]["pnl_eur"] == "1.00" for r in report["rows"])
    assert report["portfolio"] is None and report["aggregate_profit"] is None
    assert report["selection"] == {"selected_policy": None, "promotion_eligible": False}
    assert not report["money_contract_verified"] and not report["engine_dataset_ready"]
    assert probe.verify_probe(args[-1])["engine_evaluations"] == 6
    with pytest.raises(ValueError, match="already exists"):
        probe.run_probe(*args)


def test_missing_window_retained_no_replacement_or_engine_call(probe_case):
    args = probe_case(blocked=True)
    result = probe.run_probe(*args)
    assert result["summary"]["revision_time"]["selected"] == 2
    assert result["summary"]["revision_time"]["status_counts"] == {"simulated": 1, "data_blocked": 1}
    blocked = next(r for r in result["rows"] if r["trigger_id"] == "blocked")
    assert blocked["reasons"] and not blocked["engines"]
    assert result["engine_evaluations"] == 6


def test_quote_revalidation_disagreement_stops_before_any_engine(probe_case, monkeypatch):
    args = probe_case(wrong_coverage=True)
    monkeypatch.setattr(probe, "evaluate_path", lambda *_: pytest.fail("unexpected engine call"))
    with pytest.raises(ValueError, match="coverage revalidation incident"):
        probe.run_probe(*args)
    assert not (args[-1] / "results.json").exists()


def test_over_budget_blocks_whole_cohort_before_decode_or_engines(probe_case, monkeypatch):
    args = probe_case()
    monkeypatch.setattr(probe, "BUDGET", dict(probe.BUDGET, max_signals=1))
    monkeypatch.setattr(probe.ProbeRawSource, "day", lambda *_: pytest.fail("unexpected quote decode"))
    monkeypatch.setattr(probe, "evaluate_path", lambda *_: pytest.fail("unexpected engine call"))
    result = probe.run_probe(*args)
    assert len(result["rows"]) == 2 and result["engine_evaluations"] == 0
    assert all(r["status"] == "budget_blocked" for r in result["rows"])


@pytest.mark.parametrize("tamper", ["result", "source", "implementation", "incomplete"])
def test_verification_binds_results_sources_code_and_completeness(probe_case, monkeypatch, tamper):
    args = probe_case()
    probe.run_probe(*args)
    if tamper == "result":
        (args[-1] / "results.json").write_text("{}", encoding="utf-8")
    elif tamper == "source":
        (args[0] / "triggers.jsonl").write_text("{}", encoding="utf-8")
    elif tamper == "implementation":
        monkeypatch.setattr(probe, "_IMPORTED", {"research/dubai_entry_probe.py": "0" * 64})
    else:
        (args[-1] / "unexpected.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        probe.verify_probe(args[-1])
