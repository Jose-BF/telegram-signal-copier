import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

import strategy_farm
import strategy_policies


def _exact_baseline(sig_id):
    return {
        "sig_id": sig_id,
        "status": "exact",
        "validation_contract": "causal_path_v2",
        "fill_price_authority": "mt5_deals",
        "market_session_contract": "vantage_xauusd_standard_v1",
    }


def _row(
    pnl,
    *,
    status="simulated",
    channel="canal1",
    assumptions=None,
    mfe_pnl=None,
    tickets=None,
):
    return {
        "status": status,
        "strategy_pnl": pnl,
        "channel": channel,
        "assumptions": assumptions or [],
        "mfe_pnl": mfe_pnl,
        "tickets": tickets or [],
    }


def test_policy_metrics_include_return_quality_and_drawdown():
    metrics = strategy_farm.calculate_policy_metrics([
        _row(10.0),
        _row(-5.0),
        _row(0.0),
        _row(20.0),
        _row(-15.0),
    ])

    assert metrics["total_trades"] == 5
    assert metrics["blocked_trades"] == 0
    assert metrics["net_pnl"] == 10.0
    assert metrics["gross_profit"] == 30.0
    assert metrics["gross_loss"] == 20.0
    assert metrics["profit_factor"] == 1.5
    assert metrics["win_rate"] == 0.5
    assert metrics["expectancy"] == 2.0
    assert metrics["max_drawdown"] == 15.0
    assert metrics["worst_trade"] == -15.0
    assert metrics["max_consecutive_losses"] == 1


def test_blocked_rows_are_visible_and_prevent_decision_pnl():
    metrics = strategy_farm.calculate_policy_metrics([
        _row(10.0),
        _row(None, status="blocked"),
    ])

    assert metrics["coverage"] == 0.5
    assert metrics["exploratory_net_pnl"] == 10.0
    assert metrics["net_pnl"] is None
    assert metrics["blocked_trades"] == 1


def test_selection_requires_minimum_sample_clean_calibration_and_oos():
    scores = [
        {
            "policy_id": "stable",
            "metrics": {
                "total_trades": 250,
                "blocked_trades": 0,
                "unsafe_calibration_trades": 0,
                "net_pnl": 100.0,
                "expectancy": 0.4,
                "profit_factor": 1.4,
                "max_drawdown": 20.0,
            },
        },
        {
            "policy_id": "fragile",
            "metrics": {
                "total_trades": 250,
                "blocked_trades": 0,
                "unsafe_calibration_trades": 0,
                "net_pnl": 140.0,
                "expectancy": 0.56,
                "profit_factor": 1.2,
                "max_drawdown": 80.0,
            },
        },
    ]

    without_oos = strategy_farm.select_strategy(
        scores, minimum_trades=200, oos_validated=False)
    with_oos = strategy_farm.select_strategy(
        scores, minimum_trades=200, oos_validated=True)

    assert without_oos["selected_policy"] is None
    assert "oos_not_validated" in without_oos["global_blockers"]
    assert with_oos["selected_policy"] == "stable"


def test_exploratory_ranking_excludes_incomplete_or_estimated_policies():
    def score(policy_id, *, blocked=0, unsafe=0, net=10.0):
        return {
            "policy_id": policy_id,
            "metrics": {
                "total_trades": 200,
                "blocked_trades": blocked,
                "unsafe_calibration_trades": unsafe,
                "net_pnl": net if blocked == 0 else None,
                "exploratory_net_pnl": net,
                "expectancy": 1.0,
                "max_drawdown": 5.0,
            },
        }

    selection = strategy_farm.select_strategy(
        [
            score("clean"),
            score("partial", blocked=1, net=100.0),
            score("estimated", unsafe=1, net=100.0),
        ],
        minimum_trades=200,
        oos_validated=False,
    )

    assert selection["exploratory_ranking"] == ["clean"]
    assert selection["ranking_excluded"] == {
        "partial": ["blocked_trades:1"],
        "estimated": ["unsafe_pnl_calibration:1"],
    }


def test_unsafe_global_calibration_blocks_strict_selection():
    metrics = strategy_farm.calculate_policy_metrics([
        _row(10.0, assumptions=["global_mt5_calibrated:101"]),
        _row(5.0),
    ])
    selection = strategy_farm.select_strategy(
        [{"policy_id": "estimated", "metrics": metrics}],
        minimum_trades=2,
        oos_validated=True,
    )

    assert selection["selected_policy"] is None
    assert selection["policy_blockers"]["estimated"] == [
        "unsafe_pnl_calibration:1"
    ]


def test_ticket_level_counterfactual_calibration_is_still_estimated():
    metrics = strategy_farm.calculate_policy_metrics([
        _row(
            10.0,
            tickets=[{
                "changed_rules": ["ignored_be_sl"],
                "pnl_source": "ticket_mt5_calibrated",
            }],
        )
    ])

    assert metrics["unsafe_calibration_trades"] == 1


def test_metrics_report_mfe_giveback_and_slippage_stress():
    metrics = strategy_farm.calculate_policy_metrics([
        _row(
            6.0,
            mfe_pnl=10.0,
            tickets=[{
                "changed_rules": ["policy_be"],
                "pnl_per_price_unit": 1.0,
            }],
        ),
        _row(
            -2.0,
            mfe_pnl=3.0,
            tickets=[{
                "changed_rules": ["ignored_be_sl"],
                "pnl_per_price_unit": 2.0,
            }],
        ),
    ])

    assert metrics["total_mfe_pnl"] == 13.0
    assert metrics["total_profit_giveback"] == 9.0
    assert metrics["mfe_capture_ratio"] == round(4.0 / 13.0, 4)
    assert metrics["slippage_stress"]["0.10_price"] == 3.7
    assert metrics["slippage_stress"]["0.25_price"] == 3.25
    assert metrics["slippage_stress"]["0.50_price"] == 2.5


def test_policy_score_is_compact_unless_trade_details_are_requested():
    policy = strategy_policies.default_policy_catalog()[0]
    rows = [_row(1.0)]

    compact = strategy_farm.build_policy_score(
        policy, rows, include_trades=False)
    detailed = strategy_farm.build_policy_score(
        policy, rows, include_trades=True)

    assert "trades" not in compact
    assert detailed["trades"] == rows


def test_canonical_scope_uses_when_bot_observed_signal_for_date_window():
    scope = strategy_farm._canonical_scope(
        {"signals": [{
            "provider_signal_id": "canal2_1",
            "channel": "canal2",
            "signal_ts_utc": "2026-07-05T23:59:59+00:00",
            "first_observed_utc": "2026-07-06T00:00:01+00:00",
            "semantic_status": "complete",
            "execution_count": 0,
        }]},
        "2026-07-06",
        "2026-07-06",
    )

    assert scope["provider_signals"] == 1
    assert scope["unexecuted_signals"] == 1


def test_canonical_scope_excludes_nonformal_provider_records():
    catalog = {
        "signals": [
            {
                "provider_signal_id": "canal2_1",
                "channel": "canal2",
                "record_type": "formal_signal",
                "first_observed_utc": "2026-07-13T08:00:00+00:00",
                "semantic_status": "complete",
                "execution_count": 1,
            },
            {
                "provider_signal_id": "canal2_2",
                "channel": "canal2",
                "record_type": "context_setup",
                "first_observed_utc": "2026-07-13T09:00:00+00:00",
                "semantic_status": "classified",
                "execution_count": 0,
            },
        ]
    }

    scope = strategy_farm._canonical_scope(
        catalog, "2026-07-13", "2026-07-13")

    assert scope == {
        "provider_signals": 1,
        "complete_signals": 1,
        "incomplete_signals": 0,
        "executed_signals": 1,
        "unexecuted_signals": 0,
        "by_channel": {"canal1": 0, "canal2": 1},
    }


def test_blocked_actual_control_is_a_global_selection_blocker():
    selection = strategy_farm.select_strategy(
        [{
            "policy_id": "follow_actual",
            "metrics": {
                "total_trades": 10,
                "blocked_trades": 3,
                "unsafe_calibration_trades": 0,
                "net_pnl": None,
                "exploratory_net_pnl": 5.0,
                "expectancy": 0.5,
                "max_drawdown": 2.0,
            },
        }],
        minimum_trades=1,
        oos_validated=True,
    )

    assert "baseline_replay_blocked:3" in selection["global_blockers"]
    assert selection["selected_policy"] is None


def test_farm_passes_canonical_provider_signal_to_every_counterfactual(
    tmp_path,
    monkeypatch,
):
    policy = strategy_policies.StrategyPolicy(
        policy_id="runner",
        close_legs=0,
        be_legs=0,
        runner_legs=1,
        base_leg_count=1,
    )
    provider_signal = {
        "provider_signal_id": "canal1_1",
        "execution_sig_ids": ["canal1_1"],
        "signal_ts_utc": "2026-07-06T10:00:00+00:00",
        "semantic_status": "complete",
        "execution_count": 1,
        "channel": "canal1",
        "level_timeline": [],
    }
    captured = []

    def fake_simulate(*args, **kwargs):
        captured.append(kwargs)
        return _row(0.0, status="unchanged")

    monkeypatch.setattr(
        strategy_farm.strategy_simulator,
        "simulate_trade",
        fake_simulate,
    )

    strategy_farm.build_farm_report(
        [{
            "sig_id": "canal1_1",
            "channel": "canal1",
            "direction": "BUY",
            "open_dt_utc": "2026-07-06T10:00:00+00:00",
            "tickets": [],
        }],
        [_exact_baseline("canal1_1")],
        tick_cache_dir=tmp_path / "ticks",
        policies=[policy],
        catalog={"signals": [provider_signal]},
        from_date="2026-07-06",
        minimum_trades=1,
    )

    assert captured[0]["provider_signal"] is provider_signal
    assert captured[0]["require_provider_timeline"] is True


def _farm_trade(sig_id):
    return {
        "sig_id": sig_id,
        "channel": "canal1",
        "direction": "BUY",
        "open_dt_utc": "2026-07-06T10:00:00+00:00",
        "close_dt_utc": "2026-07-06T10:05:00+00:00",
        "tickets": [],
    }


def _provider_signal(sig_id):
    return {
        "provider_signal_id": sig_id,
        "execution_sig_ids": [sig_id],
        "first_observed_utc": "2026-07-06T09:59:59+00:00",
        "signal_ts_utc": "2026-07-06T09:59:58+00:00",
        "semantic_status": "complete",
        "execution_count": 1,
        "channel": "canal1",
        "level_timeline": [],
    }


class _FakeTickLoader:
    def __init__(self, tick_cache_dir):
        self.required_days = ["2026-07-06"]
        self.verified_contracts = {
            "2026-07-06": {
                "day": "2026-07-06",
                "tick_time_contract": "mt5_server_epoch_utc_v3",
                "time_basis": "UTC",
                "source_time_basis": "mt5_server_epoch",
                "utc_offset_seconds": 10_800,
                "offset_detection_method": "fill_anchor",
                "offset_reference": {"signal_id": "canal1_1"},
                "semantic_time_valid": True,
                "anchor_validation": {
                    "valid": True,
                    "anchors_checked": 1,
                    "anchors_matched": 1,
                    "max_time_delta_ms": 0,
                    "max_price_delta": 0.0,
                    "errors": [],
                },
                "parquet_sha256": "a" * 64,
                "size_bytes": 123,
            }
        }

    def load_ticks_for_trade(self, trade, *, pad_minutes=5):
        return pd.DataFrame(), []


def test_farm_execution_exposes_exact_provenance_payloads(
    tmp_path,
    monkeypatch,
):
    policies = [strategy_policies.StrategyPolicy(
        policy_id="runner",
        close_legs=0,
        be_legs=0,
        runner_legs=1,
        base_leg_count=1,
    )]
    trades = [_farm_trade("canal1_2"), _farm_trade("canal1_1")]
    baselines = [
        _exact_baseline("canal1_1"),
        _exact_baseline("canal1_2"),
    ]
    catalog = {
        "signals": [
            _provider_signal("canal1_1"),
            _provider_signal("canal1_2"),
        ]
    }
    monkeypatch.setattr(
        strategy_farm.observed_tick_replay_validator,
        "ReplayTickFrameCache",
        _FakeTickLoader,
    )
    monkeypatch.setattr(
        strategy_farm.strategy_simulator,
        "simulate_trade",
        lambda *args, **kwargs: _row(0.0, status="unchanged"),
    )

    execution = strategy_farm.build_farm_execution(
        trades,
        baselines,
        tick_cache_dir=tmp_path / "ticks",
        policies=policies,
        catalog=catalog,
        from_date="2026-07-06",
        minimum_trades=1,
    )

    assert execution.report["executed_trade_count"] == 2
    assert [
        row["sig_id"]
        for row in execution.selected_payloads["replay_trades"]
    ] == ["canal1_2", "canal1_1"]
    assert [
        row["sig_id"]
        for row in execution.selected_payloads["effective_baselines"]
    ] == ["canal1_2", "canal1_1"]
    assert execution.required_tick_days == ["2026-07-06"]
    assert execution.verified_tick_contracts["2026-07-06"][
        "parquet_sha256"
    ] == "a" * 64
    assert execution.market_replay_summary == {
        "selected_trades": 2,
        "exact": 2,
        "blocked": 0,
        "mismatched": 0,
    }


def test_legacy_exact_baseline_without_causal_contract_is_blocked(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        strategy_farm.observed_tick_replay_validator,
        "ReplayTickFrameCache",
        _FakeTickLoader,
    )
    monkeypatch.setattr(
        strategy_farm.strategy_simulator,
        "simulate_trade",
        lambda *args, **kwargs: _row(None, status="blocked"),
    )

    execution = strategy_farm.build_farm_execution(
        [_farm_trade("canal1_1")],
        [{"sig_id": "canal1_1", "status": "exact"}],
        tick_cache_dir=tmp_path / "ticks",
        policies=[strategy_policies.StrategyPolicy(
            policy_id="runner",
            close_legs=0,
            be_legs=0,
            runner_legs=1,
            base_leg_count=1,
        )],
        catalog={"signals": [_provider_signal("canal1_1")]},
        from_date="2026-07-06",
        minimum_trades=1,
    )

    assert execution.market_replay_summary == {
        "selected_trades": 1,
        "exact": 0,
        "blocked": 1,
        "mismatched": 0,
    }
    effective = execution.selected_payloads["effective_baselines"][0][
        "baseline"
    ]
    assert effective["status"] == "blocked"
    assert effective["blockers"] == [
        "causal_path_contract_unverified",
        "fill_price_authority_unverified",
        "market_session_contract_unverified",
    ]


class _BlockedTickLoader:
    def __init__(self, tick_cache_dir):
        self.required_days = ["2026-07-06"]
        self.verified_contracts = {}

    def load_ticks_for_trade(self, trade, *, pad_minutes=5):
        return pd.DataFrame(), ["invalid_tick_cache_contract:2026-07-06"]


def test_blocked_market_replay_has_no_ranking_or_selected_policy(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        strategy_farm.observed_tick_replay_validator,
        "ReplayTickFrameCache",
        _BlockedTickLoader,
    )
    policy = strategy_policies.StrategyPolicy(
        policy_id="follow_actual",
        mode="follow_actual",
        close_legs=0,
        be_legs=0,
        runner_legs=1,
        base_leg_count=1,
    )

    report = strategy_farm.build_farm_report(
        [_farm_trade("canal1_1")],
        [_exact_baseline("canal1_1")],
        tick_cache_dir=tmp_path / "ticks",
        policies=[policy],
        catalog={"signals": [_provider_signal("canal1_1")]},
        minimum_trades=1,
    )

    assert report["validation"]["mode"] == "diagnostic_only"
    assert report["validation"]["market_replay_verified"] is False
    assert report["selection"]["selected_policy"] is None
    assert report["selection"]["exploratory_ranking"] == []
    assert "market_replay_not_exact" in report["selection"]["global_blockers"]


def _write_empty_farm_inputs(root):
    replay = root / "replay.jsonl"
    baseline = root / "baseline.jsonl"
    catalog = root / "catalog.json"
    replay.write_text("", encoding="utf-8")
    baseline.write_text("", encoding="utf-8")
    catalog.write_text(
        '{"schema_version":1,"signals":[]}\n',
        encoding="utf-8",
    )
    return {"replay": replay, "baseline": baseline, "catalog": catalog}


def test_cli_writes_latest_report_with_run_card_reference(tmp_path):
    paths = _write_empty_farm_inputs(tmp_path)

    exit_code = strategy_farm.main([
        "--replay", str(paths["replay"]),
        "--baseline", str(paths["baseline"]),
        "--catalog", str(paths["catalog"]),
        "--tick-cache-dir", str(tmp_path / "ticks"),
        "--output", str(tmp_path / "strategy_farm.json"),
        "--run-archive-dir", str(tmp_path / "runs"),
        "--quiet",
    ])

    latest = json.loads((tmp_path / "strategy_farm.json").read_text())
    fingerprint = latest["provenance"]["run_fingerprint"]
    assert exit_code == 0
    assert latest["provenance"]["status"] == "diagnostic_archived"
    assert latest["validation"]["mode"] == "diagnostic_only"
    assert (tmp_path / "runs" / fingerprint / "run_card.json").is_file()


def test_cli_preserves_ordered_provider_execution_scenarios(tmp_path):
    paths = _write_empty_farm_inputs(tmp_path)
    output = tmp_path / "strategy_farm.json"

    exit_code = strategy_farm.main([
        "--replay", str(paths["replay"]),
        "--baseline", str(paths["baseline"]),
        "--catalog", str(paths["catalog"]),
        "--tick-cache-dir", str(tmp_path / "ticks"),
        "--output", str(output),
        "--run-archive-dir", str(tmp_path / "runs"),
        "--provider-latency-ms", "250",
        "--provider-latency-ms", "0",
        "--provider-volume-per-leg", "0.02",
        "--quiet",
    ])

    report = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert report["provider_configuration"] == {
        "latency_scenarios_ms": [250, 0],
        "volume_per_leg": 0.02,
    }
    assert report["provider_scope"]["latency_scenarios_ms"] == [250, 0]


def test_detailed_cli_reference_matches_exact_latest_report_bytes(tmp_path):
    paths = _write_empty_farm_inputs(tmp_path)
    output = tmp_path / "strategy_farm_details.json"

    exit_code = strategy_farm.main([
        "--replay", str(paths["replay"]),
        "--baseline", str(paths["baseline"]),
        "--catalog", str(paths["catalog"]),
        "--tick-cache-dir", str(tmp_path / "ticks"),
        "--output", str(output),
        "--run-archive-dir", str(tmp_path / "runs"),
        "--include-trades",
        "--quiet",
    ])

    latest = json.loads(output.read_text())
    run_dir = tmp_path / "runs" / latest["provenance"]["run_fingerprint"]
    card = json.loads((run_dir / "run_card.json").read_text())
    artifact = card["artifacts"][0]
    assert exit_code == 0
    assert artifact["retained"] is False
    assert artifact["size_bytes"] == output.stat().st_size
    assert artifact["sha256"] == strategy_farm.simulation_run_provenance.sha256_file(
        output
    )


def test_cli_rejects_missing_catalog_without_reusing_output(tmp_path):
    paths = _write_empty_farm_inputs(tmp_path)
    paths["catalog"].unlink()
    output = tmp_path / "strategy_farm.json"
    output.write_text('{"generated_at":"stale"}\n', encoding="utf-8")

    exit_code = strategy_farm.main([
        "--replay", str(paths["replay"]),
        "--baseline", str(paths["baseline"]),
        "--catalog", str(paths["catalog"]),
        "--output", str(output),
        "--run-archive-dir", str(tmp_path / "runs"),
        "--quiet",
    ])

    assert exit_code != 0
    assert not output.exists()


class _MissingContractTickLoader:
    def __init__(self, tick_cache_dir):
        self.required_days = ["2026-07-06"]
        self.verified_contracts = {}

    def load_ticks_for_trade(self, trade, *, pad_minutes=5):
        return pd.DataFrame(), ["missing_tick_cache:2026-07-06"]


def test_cli_does_not_archive_unverified_tick_run(tmp_path, monkeypatch):
    paths = _write_empty_farm_inputs(tmp_path)
    paths["replay"].write_text(
        json.dumps(_farm_trade("canal1_1")) + "\n",
        encoding="utf-8",
    )
    paths["baseline"].write_text(
        json.dumps(_exact_baseline("canal1_1")) + "\n",
        encoding="utf-8",
    )
    paths["catalog"].write_text(
        json.dumps({"signals": [_provider_signal("canal1_1")]}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        strategy_farm.observed_tick_replay_validator,
        "ReplayTickFrameCache",
        _MissingContractTickLoader,
    )
    monkeypatch.setattr(
        strategy_farm.strategy_simulator,
        "simulate_trade",
        lambda *args, **kwargs: _row(None, status="blocked"),
    )
    output = tmp_path / "strategy_farm.json"

    exit_code = strategy_farm.main([
        "--replay", str(paths["replay"]),
        "--baseline", str(paths["baseline"]),
        "--catalog", str(paths["catalog"]),
        "--tick-cache-dir", str(tmp_path / "ticks"),
        "--output", str(output),
        "--run-archive-dir", str(tmp_path / "runs"),
        "--quiet",
    ])

    latest = json.loads(output.read_text())
    assert exit_code == 0
    assert latest["provenance"]["status"] == "incomplete"
    assert latest["provenance"]["errors"] == [
        "unverified_tick_contract:2026-07-06",
    ]
    assert not (tmp_path / "runs").exists()


def _provider_first_signal(
    signal_id,
    *,
    executed,
    entry_ready=True,
):
    observed = "2026-07-06T10:00:00+00:00"
    entry_contract = {
        "status": "ready" if entry_ready else "blocked",
        "trigger_observed_utc": observed if entry_ready else None,
        "direction": "BUY",
        "blockers": [] if entry_ready else ["missing_entry_evidence"],
    }
    return {
        "provider_signal_id": signal_id,
        "record_type": "formal_signal",
        "channel": "canal1",
        "first_observed_utc": observed,
        "signal_ts_utc": "2026-07-06T09:59:59+00:00",
        "semantic_status": "complete" if entry_ready else "incomplete",
        "execution_count": 1 if executed else 0,
        "execution_sig_ids": [signal_id] if executed else [],
        "effective_tps": [101.0],
        "effective_sl": 99.0,
        "level_timeline": [{
            "observed_ts_utc": observed,
            "tps": [101.0],
            "sl": 99.0,
        }],
        "management_events": [],
        "entry_contract": entry_contract,
    }


class _ProviderFirstTickLoader:
    def __init__(self, tick_cache_dir):
        self.required_days = []
        self.verified_contracts = {}

    def load_ticks_for_trade(self, trade, *, pad_minutes=5):
        day = str(trade["open_dt_utc"])[:10]
        if day not in self.required_days:
            self.required_days.append(day)
        opened = datetime.fromisoformat(trade["open_dt_utc"])
        return pd.DataFrame({
            "time_utc": [opened, opened + timedelta(seconds=1)],
            "bid": [99.8, 101.0],
            "ask": [100.0, 101.2],
        }), []


def _two_provider_policies():
    return [
        strategy_policies.StrategyPolicy(
            policy_id="provider_a",
            close_legs=0,
            be_legs=0,
            runner_legs=1,
            base_leg_count=1,
        ),
        strategy_policies.StrategyPolicy(
            policy_id="provider_b",
            close_legs=0,
            be_legs=0,
            runner_legs=1,
            base_leg_count=1,
        ),
    ]


def test_provider_first_farm_emits_every_signal_policy_pair(
    tmp_path,
    monkeypatch,
):
    catalog = {"signals": [
        _provider_first_signal("executed", executed=True),
        _provider_first_signal("unexecuted", executed=False),
        _provider_first_signal(
            "missing_entry",
            executed=False,
            entry_ready=False,
        ),
    ]}
    monkeypatch.setattr(
        strategy_farm.observed_tick_replay_validator,
        "ReplayTickFrameCache",
        _ProviderFirstTickLoader,
    )

    report = strategy_farm.build_farm_report(
        [],
        [],
        tick_cache_dir=tmp_path / "ticks",
        policies=_two_provider_policies(),
        catalog=catalog,
        from_date="2026-07-06",
        to_date="2026-07-06",
        minimum_trades=1,
    )

    assert report["provider_scope"] == {
        "formal_signals": 3,
        "policy_count": 2,
        "latency_scenarios_ms": [0],
        "rows_expected": 6,
        "rows_emitted": 6,
        "simulated_rows": 4,
        "blocked_rows": 2,
        "signals_omitted": [],
    }
    rows = [
        row
        for group in report["provider_policy_results"]
        for row in group["results"]
    ]
    assert len(rows) == 6
    assert {
        (row["provider_signal_id"], row["policy_id"])
        for row in rows
    } == {
        (signal_id, policy_id)
        for signal_id in ("executed", "unexecuted", "missing_entry")
        for policy_id in ("provider_a", "provider_b")
    }
    assert all(
        row["status"] == "simulated_price_path"
        for row in rows
        if row["provider_signal_id"] in {"executed", "unexecuted"}
    )
    blocked = [
        row for row in rows
        if row["provider_signal_id"] == "missing_entry"
    ]
    assert len(blocked) == 2
    assert all(row["status"] == "blocked" for row in blocked)
    assert all("missing_entry_evidence" in row["blockers"] for row in blocked)
    assert report["validation"] == {
        "price_path_mode": "provider_first",
        "money_mode": "diagnostic_only",
        "market_replay_verified": False,
        "mode": "diagnostic_only",
    }
    assert report["selection"]["selected_policy"] is None
    assert report["selection"]["exploratory_ranking"] == []
    assert "broker_money_contract_unverified" in report["selection"][
        "global_blockers"
    ]
    assert "executed_baseline_validation" in report


def test_provider_first_farm_accounts_for_every_latency_scenario(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        strategy_farm.observed_tick_replay_validator,
        "ReplayTickFrameCache",
        _ProviderFirstTickLoader,
    )
    signal = _provider_first_signal("signal", executed=False)

    report = strategy_farm.build_farm_report(
        [],
        [],
        tick_cache_dir=tmp_path / "ticks",
        policies=_two_provider_policies(),
        catalog={"signals": [signal]},
        provider_latency_scenarios_ms=(0, 250, 1000),
    )

    assert report["provider_scope"]["rows_expected"] == 6
    assert report["provider_scope"]["rows_emitted"] == 6
    rows = [
        row
        for group in report["provider_policy_results"]
        for row in group["results"]
    ]
    assert {
        row["latency_scenario_ms"] for row in rows
    } == {0, 250, 1000}


class _ProviderMissingContractTickLoader(_ProviderFirstTickLoader):
    def load_ticks_for_trade(self, trade, *, pad_minutes=5):
        day = str(trade["open_dt_utc"])[:10]
        self.required_days.append(day)
        return pd.DataFrame(), [f"invalid_tick_cache_contract:{day}"]


def test_provider_first_farm_preserves_specific_tick_contract_blocker(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        strategy_farm.observed_tick_replay_validator,
        "ReplayTickFrameCache",
        _ProviderMissingContractTickLoader,
    )
    policy = _two_provider_policies()[0]

    report = strategy_farm.build_farm_report(
        [],
        [],
        tick_cache_dir=tmp_path / "ticks",
        policies=[policy],
        catalog={"signals": [
            _provider_first_signal("signal", executed=False),
        ]},
    )

    row = report["provider_policy_results"][0]["results"][0]
    assert row["status"] == "blocked"
    assert row["strategy_value"] is None
    assert row["blockers"] == [
        "invalid_tick_cache_contract:2026-07-06"
    ]
    assert row["entry"]["blockers"] == row["blockers"]
    assert "missing_ticks" not in row["blockers"]


def test_provider_first_farm_fails_closed_on_row_identity_mismatch(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        strategy_farm.observed_tick_replay_validator,
        "ReplayTickFrameCache",
        _ProviderFirstTickLoader,
    )

    def wrong_signal_id(spec, ticks, policy, **kwargs):
        return {
            "provider_signal_id": "wrong",
            "policy_id": policy.policy_id,
            "status": "blocked",
            "entry": {"latency_ms": spec.latency_ms},
            "strategy_value": None,
            "strategy_pnl": None,
            "blockers": ["test"],
            "legs": [],
        }

    monkeypatch.setattr(
        strategy_farm.provider_strategy_simulator,
        "simulate_provider_policy",
        wrong_signal_id,
    )

    with pytest.raises(RuntimeError, match="provider farm row accounting"):
        strategy_farm.build_farm_report(
            [],
            [],
            tick_cache_dir=tmp_path / "ticks",
            policies=_two_provider_policies(),
            catalog={"signals": [
                _provider_first_signal("expected", executed=False),
            ]},
        )
