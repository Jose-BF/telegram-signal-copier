from datetime import datetime, timedelta, timezone
import json
import sys

import pandas as pd
import pytest

from tools.audit_native_money_anchor import digest
from tools import assess_week_causal_portfolio as portfolio
from tools import run_week_causal_controls as weekly
from strategy_runtime_contract import strategy_contract_by_id


START = "2026-09-17T00:00:00+00:00"
END = "2026-09-18T00:00:00+00:00"
OBSERVED = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)


def test_weekly_dubai_entry_lifecycle_matches_declared_strategy_contract():
    policy = weekly.policies()["canal1"]
    contract = strategy_contract_by_id("dubai_balanced_v1")
    assert policy.source_strategy_fingerprint == contract.strategy_fingerprint
    assert policy.volume_weights == contract.entry.volumes
    assert policy.entry_ladder_step == contract.entry.ladder_step
    assert policy.entry_expiry_min == contract.entry.expiry_minutes
    assert policy.stop_mode == "basket_money"
    assert policy.stop_value == contract.protection.basket_stop_eur
    assert policy.profit_lock_arm == contract.protection.profit_arm_eur
    assert policy.profit_lock_giveback == contract.protection.profit_giveback_eur
    assert policy.time_exit_min == contract.protection.time_exit_minutes
    assert policy.time_exit_mode == contract.protection.time_exit_mode
    assert policy.provider_management_mode == contract.terminal.provider_management_mode
    assert policy.pending_entry_policy == contract.terminal.pending_entry_policy
    assert policy.pending_entry_policy == "until_expiry"


@pytest.fixture
def isolated_replay_imports(monkeypatch):
    # The standalone runner starts clean; pytest may have imported live modules
    # while collecting unrelated tests in the same interpreter.
    for name in ("MetaTrader5", "listener", "executor", "gold_555_live_candidate",
                 "dubai_live_candidate", "research.gold_iterative.live_parity"):
        monkeypatch.delitem(sys.modules, name, raising=False)


def message(index):
    stamp = (OBSERVED + timedelta(seconds=index)).isoformat()
    return {"ev": "telegram_raw", "channel": "canal2", "message_id": index,
            "message_revision_id": f"rev-{index}", "date_utc": OBSERVED.isoformat(),
            "ts": stamp, "text": "Buy Gold Now", "is_edit": False,
            "edit_date_utc": None, "reply_to_msg_id": None, "sticker_id": None}


def inputs(tmp_path, *, count=2):
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps({"contract": "frozen_raw_telegram_slice_v1",
                               "complete_week_claim": False,
                               "raw_row_count": count, "rows": [message(i) for i in range(1, count + 1)],
                               "start_utc": START, "end_utc": END}), encoding="utf-8")
    readiness = tmp_path / "readiness.json"
    readiness.write_text(json.dumps({
        "contract": "weekly_raw_causal_input_readiness_v1", "status": "diagnostic_only",
        "all_received_signals_covered": True,
        "max_entry_age_s": weekly.WEEKLY_ENTRY_MAX_AGE_S,
        "stale_entry_candidate_ids": [],
        "frozen_slice_temporal_completeness_proven": False,
        "start_utc": START, "end_utc": END,
        "compiled_signal_ids": [f"canal2_{i}" for i in range(1, count + 1)],
        "inputs_sha256": {str(raw): digest(raw)}}), encoding="utf-8")
    broker = tmp_path / "broker.json"
    broker.write_text(json.dumps({"account": {"currency": "EUR", "currency_digits": 2,
                                                 "server": "demo"},
                                  "instrument": {"contract_size": 100.0},
                                  "conversion": {"orientation": "account_base_profit_quote",
                                                 "max_quote_age_ms": 5000}}), encoding="utf-8")
    raw_dir = tmp_path / "ticks"
    hashes = {}
    for symbol in ("XAUUSD", "EURUSD"):
        folder = raw_dir / symbol
        folder.mkdir(parents=True)
        stamps = [int((OBSERVED + timedelta(seconds=i)).timestamp() * 1000)
                  + weekly.OFFSET_SECONDS * 1000 for i in range(1, 6)]
        bid = [100.0, 98.8, 100.3, 100.8, 100.5] if symbol == "XAUUSD" else [1.1] * 5
        data = folder / "2026-09-17.parquet"
        pd.DataFrame({"time_msc": stamps, "bid": bid,
                      "ask": [value + (0.2 if symbol == "XAUUSD" else 0.01)
                              for value in bid]}).to_parquet(data, index=False)
        meta = folder / "2026-09-17.json"
        meta.write_text(json.dumps({"status": "raw_reads_consistent", "symbol": symbol,
                                    "source_epoch_day": "2026-09-17", "sha256": digest(data),
                                    "rows": 5}), encoding="utf-8")
        hashes[str(data)] = digest(data)
        hashes[str(meta)] = digest(meta)
    anchor = tmp_path / "anchor.json"
    anchor.write_text(json.dumps({"scope": {"source_epoch_start": START,
                                            "source_epoch_end_exclusive": END,
                                            "currency": "EUR", "server": "demo"},
                                  "clock_admitted": False,
                                  "independent_clock_evidence": {"days": {
                                      "2026-09-17": {"status": "direct_anchor_available"}}},
                                  "input_sha256": hashes}), encoding="utf-8")
    return raw, readiness, anchor, broker, raw_dir


def test_week_prepare_keeps_all_raw_signals_and_declares_clock_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(weekly, "_identity", lambda: {"test": "source"})
    values = inputs(tmp_path)
    protocol_path = tmp_path / "protocol.json"
    report = weekly.prepare(*values, protocol_path)
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    assert report["signals"] == 2
    assert protocol["raw_signal_ids"] == ["canal2_1", "canal2_2"]
    assert protocol["search_candidate_budget"] == 0
    assert protocol["batch_size"] == weekly.BATCH_SIZE
    assert protocol["execution_money_assumptions"]["rollover"] == "not_admitted"
    assert protocol["all_days_direct_clock"] is True
    assert protocol["clock_admitted"] is False
    assert protocol["risk_scope"] == "isolated_signal_only"
    assert protocol["shared_account_equity_reconstructed"] is False
    assert protocol["genomes"]["canal1"]["pending_entry_policy"] == "until_expiry"
    assert len(protocol["tapes"]["XAUUSD"]) == 1
    with pytest.raises(FileExistsError):
        weekly.prepare(*values, protocol_path)


def test_week_prepare_rejects_truncation_and_noncausal_input(tmp_path, monkeypatch):
    monkeypatch.setattr(weekly, "_identity", lambda: {"test": "source"})
    values = inputs(tmp_path)
    raw, readiness, anchor, broker, raw_dir = values
    data = json.loads(raw.read_text(encoding="utf-8"))
    data["rows"][0]["native_deal"] = 123
    raw.write_text(json.dumps(data), encoding="utf-8")
    report = json.loads(readiness.read_text(encoding="utf-8"))
    report["inputs_sha256"][str(raw)] = digest(raw)
    readiness.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="noncausal"):
        weekly.prepare(*values, tmp_path / "bad-protocol.json")


def test_week_prepare_rejects_excess_signals_without_truncation(tmp_path, monkeypatch):
    monkeypatch.setattr(weekly, "_identity", lambda: {"test": "source"})
    values = inputs(tmp_path, count=weekly.MAX_SIGNALS + 1)
    with pytest.raises(ValueError, match="denominator changed or exceeded budget"):
        weekly.prepare(*values, tmp_path / "protocol.json")
    assert not (tmp_path / "protocol.json").exists()


def test_batches_cover_every_signal_once_without_shortening_quote_horizon():
    ids = [f"canal2_{index}" for index in range(1, 11)]
    first, count = weekly._batch_ids(ids, 0)
    second, other_count = weekly._batch_ids(ids, 1)
    assert count == other_count == 2
    assert first + second == ids
    assert len(first) == weekly.BATCH_SIZE
    with pytest.raises(ValueError, match="batch index"):
        weekly._batch_ids(ids, 2)


def test_week_scenarios_separate_price_stress_from_latency_faults():
    keys = [weekly._scenario_key(row) for row in weekly.SCENARIOS]
    assert len(set(keys)) == len(keys)
    assert {row.get("entry_slippage") for row in weekly.SCENARIOS
            if row.get("entry_slippage")} == {0.10, 0.50}
    assert {row["latency_ms"] for row in weekly.SCENARIOS
            if row["latency_ms"] >= 60_000} == {60_000, 180_000}
    assert {row["entry_fill_latency_ms"] for row in weekly.SCENARIOS
            if row["entry_fill_latency_ms"] >= 60_000} == {60_000, 180_000}


def test_frozen_tape_detects_changed_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(weekly, "_identity", lambda: {"test": "source"})
    values = inputs(tmp_path)
    protocol_path = tmp_path / "protocol.json"
    weekly.prepare(*values, protocol_path)
    proof = json.loads(protocol_path.read_text(encoding="utf-8"))["tapes"]["XAUUSD"]
    stamps, bid, ask = weekly._load_tape(proof)
    assert len(stamps) == len(bid) == len(ask) == 5
    assert stamps[0] == int((OBSERVED + timedelta(seconds=1)).timestamp() * 1_000_000_000)
    with open(proof[0]["data"], "ab") as stream:
        stream.write(b"tamper")
    with pytest.raises(ValueError, match="changed"):
        weekly._load_tape(proof)


def test_week_run_executes_all_frozen_scenarios_without_observed_results(
        tmp_path, monkeypatch, isolated_replay_imports):
    monkeypatch.setattr(weekly, "_identity", lambda: {"test": "source"})
    values = inputs(tmp_path)
    protocol_path = tmp_path / "protocol.json"
    weekly.prepare(*values, protocol_path)
    output = tmp_path / "results.json"
    summary = weekly.run(protocol_path, output)
    report = json.loads(output.read_text(encoding="utf-8"))
    assert summary["signals"] == 2
    assert report["signal_count"] == 2
    assert report["scenario_count"] == len(weekly.SCENARIOS)
    assert len(report["results"]) == 2 * len(weekly.SCENARIOS)
    assert {row["signal_id"] for row in report["results"]} == {"canal2_1", "canal2_2"}
    assert report["full_live_parity_verified"] is False
    assert report["independent_account_equity_verified"] is False
    assert report["shared_account_equity_reconstructed"] is False
    assert report["risk_scope"] == "isolated_signal_only"
    assert all("native_deal" not in row for row in report["results"])
    assert all(not any(row["engine_mismatches"].values())
               for row in report["results"] if "engine_mismatches" in row)
    assert all("result" in row for row in report["results"])
    assert report["last_market_tick_utc"] < report["requested_cutoff_utc"]
    assert any(row["censored_data_end"] for row in report["results"])
    assert all(row["status"] == "censored_data_end" for row in report["results"]
               if row["censored_data_end"] and not any(row["engine_mismatches"].values())
               and not row["result"]["blockers"])
    assembled = tmp_path / "assembled.json"
    merged = weekly.assemble(protocol_path, [output], assembled)
    final = json.loads(assembled.read_text(encoding="utf-8"))
    assert merged["signals"] == 2
    assert final["batch_count"] == 1
    assert final["signal_ids"] == ["canal2_1", "canal2_2"]
    assert final["execution_money_assumptions"]["commission"] == "hypothetical_zero"
    assert final["risk_scope"] == "isolated_signal_only"
    assert final["shared_account_equity_reconstructed"] is False
    assert final["coverage_status"] == "blocked_simulation"
    assert final["censored_row_count"] == sum(
        row["censored_data_end"] for row in final["results"])
    assert final["censored_row_count"] > 0
    assert len(final["results"]) == 2 * len(weekly.SCENARIOS)
    with pytest.raises(FileExistsError):
        weekly.assemble(protocol_path, [output], assembled)
    changed = tmp_path / "changed-batch.json"
    malformed = dict(report)
    malformed["results"] = [dict(row) for row in report["results"]]
    malformed["results"][1]["latency_ms"] = malformed["results"][0]["latency_ms"]
    malformed["results"][1]["entry_fill_latency_ms"] = malformed["results"][0]["entry_fill_latency_ms"]
    changed.write_text(json.dumps(malformed), encoding="utf-8")
    with pytest.raises(ValueError, match="scenario matrix incomplete"):
        weekly.assemble(protocol_path, [changed], tmp_path / "bad-assembly.json")


def test_week_assembly_rejects_falsely_clean_batch_counts(
        tmp_path, monkeypatch, isolated_replay_imports):
    monkeypatch.setattr(weekly, "_identity", lambda: {"test": "source"})
    values = inputs(tmp_path)
    protocol_path = tmp_path / "protocol.json"
    weekly.prepare(*values, protocol_path)
    output = tmp_path / "results.json"
    weekly.run(protocol_path, output)
    report = json.loads(output.read_text(encoding="utf-8"))
    report["row_status_counts"] = {"diagnostic_only": len(report["results"])}
    bad = tmp_path / "bad-counts.json"
    bad.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="batch status counts mismatch"):
        weekly.assemble(protocol_path, [bad], tmp_path / "assembled.json")


def test_week_assembly_rejects_missing_batches(tmp_path, monkeypatch):
    monkeypatch.setattr(weekly, "_identity", lambda: {"test": "source"})
    values = inputs(tmp_path, count=weekly.BATCH_SIZE + 1)
    protocol_path = tmp_path / "protocol.json"
    weekly.prepare(*values, protocol_path)
    with pytest.raises(ValueError, match="batch index"):
        weekly.run(protocol_path, tmp_path / "unindexed.json")
    with pytest.raises(ValueError, match="batch denominator incomplete"):
        weekly.assemble(protocol_path, [], tmp_path / "assembled.json")


def test_week_run_rejects_changed_frozen_input(tmp_path, monkeypatch):
    monkeypatch.setattr(weekly, "_identity", lambda: {"test": "source"})
    values = inputs(tmp_path)
    protocol_path = tmp_path / "protocol.json"
    weekly.prepare(*values, protocol_path)
    with values[0].open("a", encoding="utf-8") as stream:
        stream.write(" ")
    output = tmp_path / "results.json"
    with pytest.raises(ValueError, match="frozen weekly causal input changed"):
        weekly.run(protocol_path, output)
    assert not output.exists()


def test_portfolio_phase_keeps_censored_week_denominator(
        tmp_path, monkeypatch, isolated_replay_imports):
    monkeypatch.setattr(weekly, "_identity", lambda: {"test": "source"})
    values = inputs(tmp_path)
    protocol_path = tmp_path / "protocol.json"
    weekly.prepare(*values, protocol_path)
    batch_path = tmp_path / "batch.json"
    weekly.run(protocol_path, batch_path)
    assembled_path = tmp_path / "assembled.json"
    weekly.assemble(protocol_path, [batch_path], assembled_path)
    output_path = tmp_path / "portfolio.json"
    summary = portfolio.assess(protocol_path, assembled_path, output_path)
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert summary["signal_count"] == 2
    assert summary["complete_scenarios"] == 4
    assert summary["censored_risk_scenarios"] == 5
    assert report["status"] == "partial_censored"
    assert report["shared_account_equity_reconstructed"] is True
    assert report["all_hypothetical_scenarios_closed"] is False
    assert report["risk_scope"] == "hypothetical_shared_account"
    assert report["scenario_status_counts"] == {
        "blocked_prior_stage": 2, "censored_terminal_mark": 5,
        "diagnostic_only": 4}
    assert len(report["scenarios"]) == len(weekly.SCENARIOS)
    censored = [row for row in report["scenarios"] if row["status"] == "censored_terminal_mark"]
    complete = [row for row in report["scenarios"] if row["status"] == "diagnostic_only"]
    blocked = [row for row in report["scenarios"] if row["status"] == "blocked_prior_stage"]
    assert len(censored) == 5
    assert all(row["account"] is not None and row["hypothetical_exit_complete"] is False
               and row["censored_terminal_signal_ids"] for row in censored)
    assert all(row["account_net_interpretation"] ==
               "hypothetical_terminal_liquidation_not_realized" for row in censored)
    assert len(complete) == 4
    assert len(blocked) == 2
    assert all(row["account"] is None and row["excluded_signals"] for row in blocked)
    assert all(row["account"]["max_drawdown_eur"] is not None for row in complete)
    assert report["full_live_parity_verified"] is False
    tampered = json.loads(assembled_path.read_text(encoding="utf-8"))
    tampered["results"][0]["note"] = "changed-after-assembly"
    tampered_path = tmp_path / "tampered-assembled.json"
    tampered_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="assembled rows differ"):
        portfolio.assess(protocol_path, tampered_path, tmp_path / "invalid-portfolio.json")
