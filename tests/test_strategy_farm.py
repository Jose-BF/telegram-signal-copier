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
