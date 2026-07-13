import json

import pandas as pd

import strategy_farm
import strategy_policies


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
        [{"sig_id": "canal1_1", "status": "exact"}],
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
                "tick_time_contract": "mt5_utc_v2",
                "time_basis": "UTC",
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
        {"sig_id": "canal1_1", "status": "exact"},
        {"sig_id": "canal1_2", "status": "exact"},
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
    assert latest["provenance"]["status"] == "archived"
    assert (tmp_path / "runs" / fingerprint / "run_card.json").is_file()


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
        json.dumps({"sig_id": "canal1_1", "status": "exact"}) + "\n",
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
