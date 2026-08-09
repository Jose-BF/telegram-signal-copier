from datetime import date
from decimal import Decimal

import pandas as pd

import strategy_simulator
from strategy_policies import StrategyPolicy
from tools import ensure_replay_tick_cache


def _write_tick_contract(cache_dir, day):
    frame = pd.read_parquet(
        cache_dir / f"{day.isoformat()}.parquet"
    )
    content_digest = ensure_replay_tick_cache.tick_content_sha256(frame)
    ensure_replay_tick_cache.write_day_contract(
        cache_dir,
        day,
        time_evidence={
            "source_time_basis": "mt5_server_epoch",
            "utc_offset_seconds": 10_800,
            "offset_detection_method": "fill_anchor",
            "offset_reference": {"signal_id": "canal1_calibrator"},
        },
        semantic_validation={
            "valid": True,
            "anchors_checked": 1,
            "anchors_matched": 1,
            "max_time_delta_ms": 0,
            "max_price_delta": 0.0,
            "errors": [],
        },
        source_verification={
            "verified": True,
            "method": "full_day_vs_two_half_days_v1",
            "content_digest": "time_bid_ask_sequence_sha256_v1",
            "symbol": "XAUUSD",
            "primary_row_count": len(frame),
            "verification_row_count": len(frame),
            "primary_content_sha256": content_digest,
            "verification_content_sha256": content_digest,
            "errors": [],
        },
        symbol="XAUUSD",
    )


def _ticks(rows):
    frame = pd.DataFrame(rows, columns=["time_utc", "bid", "ask"])
    frame["time_utc"] = pd.to_datetime(
        frame["time_utc"],
        utc=True,
        format="mixed",
    )
    return frame


def _ticket(**overrides):
    ticket = {
        "ticket": 101,
        "open_dt_utc": "2026-07-06T10:00:00+00:00",
        "open_price": 100.0,
        "close_dt_utc": "2026-07-06T10:20:00+00:00",
        "close_price": 90.0,
        "close_reason": "sl",
        "is_closed": True,
        "volume": 1.0,
        "pnl_net": -10.0,
        "sl_history": [
            {
                "ts": "2026-07-06T10:00:00+00:00",
                "status": "confirmed",
                "source": "SL/TP[0]",
                "sl": 90.0,
            }
        ],
        "tp_history": [
            {
                "ts": "2026-07-06T10:00:00+00:00",
                "status": "confirmed",
                "source": "SL/TP[0]",
                "tp": 110.0,
            }
        ],
    }
    ticket.update(overrides)
    return ticket


def _trade(**overrides):
    trade = {
        "sig_id": "canal1_1",
        "channel": "canal1",
        "direction": "BUY",
        "open_dt_utc": "2026-07-06T10:00:00+00:00",
        "close_dt_utc": "2026-07-06T10:20:00+00:00",
        "pnl_real_mt5": -10.0,
        "decisions": {
            "strategy_snapshot": {
                "time_stop_at": "2026-07-06T10:05:00+00:00",
                "time_stop_min": 5,
            }
        },
        "management": [],
        "tickets": [_ticket()],
    }
    trade.update(overrides)
    return trade


def test_no_be_leaves_trade_without_be_unchanged_even_with_time_stop():
    ticks = _ticks([
        {"time_utc": "2026-07-06T10:05:00+00:00", "bid": 105.0, "ask": 105.2},
        {"time_utc": "2026-07-06T10:20:00+00:00", "bid": 90.0, "ask": 90.2},
    ])

    result = strategy_simulator.simulate_trade(
        _trade(),
        ticks,
        strategy_name="no_be",
        baseline_audit={"status": "exact"},
        default_unit_value=1.0,
    )

    assert result["status"] == "unchanged"
    assert result["strategy_pnl"] == -10.0
    assert result["actual_pnl"] == -10.0
    assert result["delta_pnl"] == 0.0
    assert result["tickets"][0]["status"] == "unchanged_no_strategy_event"
    assert result["tickets"][0]["pnl_source"] == "mt5_actual"
    assert "time_stop" not in result["tickets"][0]["close_reason"]


def test_no_be_removes_only_be_sl_and_continues_to_later_tp():
    ticket = _ticket(
        close_dt_utc="2026-07-06T10:06:00+00:00",
        close_price=100.0,
        close_reason="be",
        pnl_net=0.0,
        sl_history=[
            {
                "ts": "2026-07-06T10:00:00+00:00",
                "status": "confirmed",
                "source": "SL/TP[0]",
                "sl": 90.0,
            },
            {
                "ts": "2026-07-06T10:05:00+00:00",
                "status": "confirmed",
                "source": "BE #101 -> 100.0",
                "sl": 100.0,
            },
        ],
    )
    trade = _trade(
        close_dt_utc="2026-07-06T10:06:00+00:00",
        pnl_real_mt5=0.0,
        management=[{
            "ts": "2026-07-06T10:05:00+00:00",
            "classified": "MOVE_SL_TO_BE",
            "applied": True,
        }],
        tickets=[ticket],
    )
    ticks = _ticks([
        {"time_utc": "2026-07-06T10:04:00+00:00", "bid": 104.0, "ask": 104.2},
        {"time_utc": "2026-07-06T10:06:00+00:00", "bid": 100.0, "ask": 100.2},
        {"time_utc": "2026-07-06T10:10:00+00:00", "bid": 110.0, "ask": 110.2},
    ])

    result = strategy_simulator.simulate_trade(
        trade,
        ticks,
        strategy_name="no_be",
        baseline_audit={"status": "exact"},
        default_unit_value=1.0,
    )

    assert result["status"] == "simulated"
    assert result["strategy_pnl"] == 10.0
    assert result["actual_pnl"] == 0.0
    assert result["delta_pnl"] == 10.0
    assert result["tickets"][0]["changed_rules"] == ["ignored_be_sl"]
    assert result["tickets"][0]["close_reason"] == "tp"
    assert result["tickets"][0]["close_time_utc"] == "2026-07-06T10:10:00+00:00"


def test_strategy_blocks_when_baseline_tick_replay_is_not_exact():
    result = strategy_simulator.simulate_trade(
        _trade(),
        _ticks([]),
        strategy_name="no_be",
        baseline_audit={"status": "mismatch", "blockers": ["no_level_touch"]},
        default_unit_value=1.0,
    )

    assert result["status"] == "blocked"
    assert result["blockers"] == ["baseline_not_exact:mismatch", "no_level_touch"]
    assert result["strategy_pnl"] is None


def test_executed_mt5_mode_accepts_confirmed_external_intervention_baseline():
    result = strategy_simulator.simulate_trade(
        _trade(),
        _ticks([]),
        strategy_name="follow_actual",
        baseline_audit={
            "status": "external_intervention",
            "blockers": ["manual_close_confirmed"],
        },
        level_timeline_authority="mt5_execution",
        default_unit_value=1.0,
    )

    assert result["status"] == "unchanged"
    assert result["strategy_pnl"] == -10.0
    assert result["blockers"] == []
    assert result["tickets"][0]["pnl_source"] == "mt5_actual"


def test_provider_mode_keeps_external_intervention_baseline_blocked():
    result = strategy_simulator.simulate_trade(
        _trade(),
        _ticks([]),
        strategy_name="follow_actual",
        baseline_audit={
            "status": "external_intervention",
            "blockers": ["manual_close_confirmed"],
        },
        level_timeline_authority="canonical_provider",
        default_unit_value=1.0,
    )

    assert result["status"] == "blocked"
    assert result["blockers"] == [
        "baseline_not_exact:external_intervention",
        "manual_close_confirmed",
    ]


def test_executed_mt5_mode_accepts_delayed_close_observation_baseline():
    result = strategy_simulator.simulate_trade(
        _trade(),
        _ticks([]),
        strategy_name="follow_actual",
        baseline_audit={
            "status": "delayed_close_observation",
            "limitations": ["per_ticket_close_time_unavailable:101"],
        },
        level_timeline_authority="mt5_execution",
        default_unit_value=1.0,
    )

    assert result["status"] == "unchanged"
    assert result["strategy_pnl"] == -10.0
    assert result["blockers"] == []


def test_no_be_uses_explicit_horizon_close_when_tp_or_sl_never_touch():
    ticket = _ticket(
        close_dt_utc="2026-07-06T10:06:00+00:00",
        close_price=100.0,
        close_reason="be",
        pnl_net=0.0,
        sl_history=[
            {
                "ts": "2026-07-06T10:00:00+00:00",
                "status": "confirmed",
                "source": "SL/TP[0]",
                "sl": 90.0,
            },
            {
                "ts": "2026-07-06T10:05:00+00:00",
                "status": "confirmed",
                "source": "BE #101 -> 100.0",
                "sl": 100.0,
            },
        ],
    )
    trade = _trade(
        close_dt_utc="2026-07-06T10:06:00+00:00",
        pnl_real_mt5=0.0,
        management=[{
            "ts": "2026-07-06T10:05:00+00:00",
            "classified": "MOVE_SL_TO_BE",
        }],
        tickets=[ticket],
    )
    ticks = _ticks([
        {"time_utc": "2026-07-06T10:04:00+00:00", "bid": 104.0, "ask": 104.2},
        {"time_utc": "2026-07-06T23:59:00+00:00", "bid": 104.0, "ask": 104.2},
    ])

    result = strategy_simulator.simulate_trade(
        trade,
        ticks,
        strategy_name="no_be",
        baseline_audit={"status": "exact"},
        default_unit_value=1.0,
    )

    assert result["status"] == "simulated"
    assert result["strategy_pnl"] == 4.0
    assert result["tickets"][0]["close_reason"] == "horizon_close"
    assert "horizon_close:eod" in result["assumptions"]


def test_report_uses_global_mt5_calibration_for_be_only_tickets(tmp_path):
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    _ticks([
        {"time_utc": "2026-07-06T10:00:00+00:00", "bid": 100.0, "ask": 100.2},
        {"time_utc": "2026-07-06T10:10:00+00:00", "bid": 110.0, "ask": 110.2},
        {"time_utc": "2026-07-06T11:00:00+00:00", "bid": 200.0, "ask": 200.2},
        {"time_utc": "2026-07-06T11:10:00+00:00", "bid": 210.0, "ask": 210.2},
    ]).to_parquet(cache_dir / "2026-07-06.parquet", index=False)
    _write_tick_contract(cache_dir, date(2026, 7, 6))

    calibrator = _trade(
        sig_id="canal1_calibrator",
        open_dt_utc="2026-07-06T11:00:00+00:00",
        close_dt_utc="2026-07-06T11:10:00+00:00",
        pnl_real_mt5=100.0,
        tickets=[_ticket(
            ticket=201,
            open_dt_utc="2026-07-06T11:00:00+00:00",
            open_price=200.0,
            close_dt_utc="2026-07-06T11:10:00+00:00",
            close_price=210.0,
            close_reason="tp",
            pnl_net=100.0,
        )],
    )
    be_only = _trade(
        sig_id="canal1_be",
        open_dt_utc="2026-07-06T10:00:00+00:00",
        close_dt_utc="2026-07-06T10:05:00+00:00",
        pnl_real_mt5=0.0,
        management=[{
            "ts": "2026-07-06T10:05:00+00:00",
            "classified": "MOVE_SL_TO_BE",
        }],
        tickets=[_ticket(
            ticket=101,
            close_dt_utc="2026-07-06T10:05:00+00:00",
            close_price=100.0,
            close_reason="be",
            pnl_net=0.0,
            sl_history=[
                {
                    "ts": "2026-07-06T10:00:00+00:00",
                    "status": "confirmed",
                    "source": "SL/TP[0]",
                    "sl": 90.0,
                },
                {
                    "ts": "2026-07-06T10:05:00+00:00",
                    "status": "confirmed",
                    "source": "BE #101 -> 100.0",
                    "sl": 100.0,
                },
            ],
        )],
    )

    report = strategy_simulator.build_simulation_report(
        [be_only, calibrator],
        [
            {"sig_id": "canal1_be", "status": "exact"},
            {"sig_id": "canal1_calibrator", "status": "exact"},
        ],
        strategy_name="no_be",
        tick_cache_dir=cache_dir,
        from_date="2026-07-06",
        to_date="2026-07-06",
        default_unit_value=None,
    )

    be_result = next(
        row for row in report["trades"] if row["sig_id"] == "canal1_be")
    assert be_result["strategy_pnl"] == 100.0
    assert be_result["tickets"][0]["pnl_source"] == "global_mt5_calibrated"
    assert "global_mt5_calibrated:101" in be_result["assumptions"]


def _managed_ticket(ticket, tp):
    return _ticket(
        ticket=ticket,
        close_dt_utc="2026-07-06T10:06:00+00:00",
        close_price=100.0,
        close_reason="be",
        pnl_net=0.0,
        sl_history=[
            {
                "ts": "2026-07-06T10:00:00+00:00",
                "status": "confirmed",
                "source": "initial",
                "sl": 90.0,
            },
            {
                "ts": "2026-07-06T10:05:00+00:00",
                "status": "confirmed",
                "source": f"BE #{ticket}",
                "sl": 100.0,
            },
        ],
        tp_history=[{
            "ts": "2026-07-06T10:00:00+00:00",
            "status": "confirmed",
            "source": "initial",
            "tp": tp,
        }],
    )


def test_policy_closes_nearest_leg_protects_next_and_keeps_runner():
    policy = StrategyPolicy(
        policy_id="close_1_be_1_runner_1",
        close_legs=1,
        be_legs=1,
        runner_legs=1,
        base_leg_count=3,
    )
    trade = _trade(
        pnl_real_mt5=0.0,
        management=[{
            "ts": "2026-07-06T10:05:00+00:00",
            "classified": "MOVE_SL_TO_BE",
            "applied": True,
        }],
        tickets=[
            _managed_ticket(101, 105.0),
            _managed_ticket(102, 110.0),
            _managed_ticket(103, 115.0),
        ],
    )
    ticks = _ticks([
        {"time_utc": "2026-07-06T10:04:00+00:00", "bid": 102.0, "ask": 102.2},
        {"time_utc": "2026-07-06T10:05:00+00:00", "bid": 104.0, "ask": 104.2},
        {"time_utc": "2026-07-06T10:06:00+00:00", "bid": 99.0, "ask": 99.2},
        {"time_utc": "2026-07-06T10:10:00+00:00", "bid": 115.0, "ask": 115.2},
    ])

    result = strategy_simulator.simulate_trade(
        trade,
        ticks,
        strategy_name=policy.policy_id,
        policy=policy,
        baseline_audit={"status": "exact"},
        default_unit_value=1.0,
    )

    assert result["status"] == "simulated"
    # The protected leg gaps from 104 to 99, so its SL at 100 is filled at
    # the first tradable bid (99), not at the theoretical trigger level.
    assert result["strategy_pnl"] == 18.0
    assert [row["leg_action"] for row in result["tickets"]] == [
        "close_now", "move_to_be", "runner"]
    assert [row["close_reason"] for row in result["tickets"]] == [
        "management_close", "sl", "tp"]
    assert result["tickets"][0]["close_price"] == 104.0
    assert result["tickets"][1]["close_price"] == 99.0
    assert result["tickets"][2]["close_price"] == 115.0


def test_policy_blocks_when_leg_targets_only_exist_after_management():
    policy = StrategyPolicy(
        policy_id="close_1_runner_1",
        close_legs=1,
        be_legs=0,
        runner_legs=1,
        base_leg_count=2,
    )
    tickets = [_managed_ticket(101, 105.0), _managed_ticket(102, 110.0)]
    for ticket in tickets:
        ticket["tp_history"][0]["ts"] = "2026-07-06T10:06:00+00:00"
    trade = _trade(
        management=[{
            "ts": "2026-07-06T10:05:00+00:00",
            "classified": "MOVE_SL_TO_BE",
        }],
        tickets=tickets,
    )

    result = strategy_simulator.simulate_trade(
        trade,
        _ticks([{
            "time_utc": "2026-07-06T10:05:00+00:00",
            "bid": 104.0,
            "ask": 104.2,
        }]),
        strategy_name=policy.policy_id,
        policy=policy,
        baseline_audit={"status": "exact"},
        default_unit_value=1.0,
    )

    assert result["status"] == "blocked"
    assert "missing_causal_tp_at_trigger:101" in result["blockers"]
    assert "missing_causal_tp_at_trigger:102" in result["blockers"]


def test_policy_does_not_use_observed_mt5_be_as_provider_trigger():
    policy = StrategyPolicy(
        policy_id="close_all",
        close_legs=1,
        be_legs=0,
        runner_legs=0,
        base_leg_count=1,
    )
    trade = _trade(management=[], tickets=[_managed_ticket(101, 105.0)])

    result = strategy_simulator.simulate_trade(
        trade,
        _ticks([{
            "time_utc": "2026-07-06T10:05:00+00:00",
            "bid": 104.0,
            "ask": 104.2,
        }]),
        strategy_name=policy.policy_id,
        policy=policy,
        baseline_audit={"status": "exact"},
        default_unit_value=1.0,
    )

    assert result["status"] == "blocked"
    assert result["management_trigger_source"] is None
    assert "missing_provider_management_trigger:MOVE_SL_TO_BE" in result["blockers"]


def test_policy_uses_provider_level_timeline_instead_of_ticket_history():
    policy = StrategyPolicy(
        policy_id="runner",
        close_legs=0,
        be_legs=0,
        runner_legs=1,
        base_leg_count=1,
    )
    ticket = _managed_ticket(101, 105.0)
    trade = _trade(
        management=[{
            "ts": "2026-07-06T10:05:00+00:00",
            "classified": "MOVE_SL_TO_BE",
        }],
        tickets=[ticket],
    )
    provider_signal = {
        "provider_signal_id": "canal1_1",
        "management_events": [{
            "telegram_ts_utc": "2026-07-06T10:05:00+00:00",
            "classified_action": "MOVE_SL_TO_BE",
        }],
        "level_timeline": [{
            "telegram_ts_utc": "2026-07-06T10:00:00+00:00",
            "tps": [110.0],
            "sl": 90.0,
        }],
    }
    ticks = _ticks([
        {"time_utc": "2026-07-06T10:06:00+00:00", "bid": 105.0, "ask": 105.2},
        {"time_utc": "2026-07-06T10:10:00+00:00", "bid": 110.0, "ask": 110.2},
    ])

    result = strategy_simulator.simulate_trade(
        trade,
        ticks,
        strategy_name=policy.policy_id,
        policy=policy,
        provider_signal=provider_signal,
        require_provider_timeline=True,
        baseline_audit={"status": "exact"},
        default_unit_value=1.0,
    )

    assert result["status"] == "simulated"
    assert result["level_timeline_source"] == "canonical_provider"
    assert result["tickets"][0]["close_reason"] == "tp"
    assert result["tickets"][0]["close_price"] == 110.0


def test_executed_mt5_mode_uses_provider_trigger_but_confirmed_mt5_levels():
    policy = StrategyPolicy(
        policy_id="runner",
        close_legs=0,
        be_legs=0,
        runner_legs=1,
        base_leg_count=1,
    )
    ticket = _managed_ticket(101, 108.0)
    trade = _trade(tickets=[ticket])
    provider_signal = {
        "provider_signal_id": "canal1_1",
        "management_events": [{
            "observed_ts_utc": "2026-07-06T10:05:00+00:00",
            "classified_action": "MOVE_SL_TO_BE",
        }],
        "level_timeline": [{
            "observed_ts_utc": "2026-07-06T10:00:00+00:00",
            "tps": [110.0],
            "sl": 90.0,
        }],
    }
    ticks = _ticks([
        {
            "time_utc": "2026-07-06T10:06:00.000+00:00",
            "bid": 105.0,
            "ask": 105.2,
        },
        {"time_utc": "2026-07-06T10:10:00+00:00", "bid": 108.0, "ask": 108.2},
        {"time_utc": "2026-07-06T10:15:00+00:00", "bid": 110.0, "ask": 110.2},
    ])

    result = strategy_simulator.simulate_trade(
        trade,
        ticks,
        strategy_name=policy.policy_id,
        policy=policy,
        provider_signal=provider_signal,
        require_provider_timeline=True,
        level_timeline_authority="mt5_execution",
        baseline_audit={"status": "exact"},
        default_unit_value=1.0,
    )

    assert result["status"] == "simulated"
    assert result["entry_authority"] == "mt5_deals"
    assert result["level_timeline_source"] == "execution_ticket_history"
    assert result["management_trigger_source"] == (
        "canonical_provider_management"
    )
    assert result["tickets"][0]["close_reason"] == "tp"
    assert result["tickets"][0]["close_price"] == 108.0


def test_executed_policy_ignores_tp_reassignment_tagged_as_be():
    policy = StrategyPolicy(
        policy_id="no_be",
        close_legs=0,
        be_legs=0,
        runner_legs=1,
        base_leg_count=1,
    )
    ticket = _managed_ticket(101, 110.0)
    ticket["tp_history"].append({
        "ts": "2026-07-06T10:05:00+00:00",
        "status": "confirmed",
        "source": "SL/TP[0] (BE)",
        "tp": 105.0,
    })
    trade = _trade(tickets=[ticket])
    provider_signal = {
        "provider_signal_id": "canal1_1",
        "management_events": [{
            "observed_ts_utc": "2026-07-06T10:05:00+00:00",
            "classified_action": "MOVE_SL_TO_BE",
        }],
    }
    ticks = _ticks([
        {
            "time_utc": "2026-07-06T10:06:00+00:00",
            "bid": 105.0,
            "ask": 105.2,
        },
        {
            "time_utc": "2026-07-06T10:10:00+00:00",
            "bid": 110.0,
            "ask": 110.2,
        },
    ])

    result = strategy_simulator.simulate_trade(
        trade,
        ticks,
        strategy_name=policy.policy_id,
        policy=policy,
        provider_signal=provider_signal,
        require_provider_timeline=True,
        level_timeline_authority="mt5_execution",
        baseline_audit={"status": "exact"},
        default_unit_value=1.0,
    )

    assert result["status"] == "simulated"
    assert result["tickets"][0]["close_reason"] == "tp"
    assert result["tickets"][0]["close_price"] == 110.0


def test_executed_counterfactual_uses_verified_broker_money_converter():
    class VerifiedMoney:
        currency = "EUR"
        quantum = Decimal("0.01")

        def convert_leg(self, **kwargs):
            assert kwargs["open_price"] == 100.0
            assert kwargs["close_price"] == 108.0
            assert kwargs["volume"] == 1.0
            assert kwargs["close_time_utc"] == (
                "2026-07-06T10:10:00.850+00:00"
            )
            assert kwargs["verified_utc_offset_seconds"] == 10_800
            return {
                "status": "verified",
                "strategy_pnl": 42.17,
                "pnl_currency": "EUR",
                "profit_currency_pnl": 8.0,
                "conversion": {
                    "symbol": "EURUSD",
                    "price": 1.08,
                },
                "blockers": [],
            }

    policy = StrategyPolicy(
        policy_id="runner",
        close_legs=0,
        be_legs=0,
        runner_legs=1,
        base_leg_count=1,
    )
    trade = _trade(tickets=[_managed_ticket(101, 108.0)])
    provider_signal = {
        "management_events": [{
            "observed_ts_utc": "2026-07-06T10:05:00+00:00",
            "classified_action": "MOVE_SL_TO_BE",
        }],
    }
    ticks = _ticks([
        {
            "time_utc": "2026-07-06T10:06:00.000+00:00",
            "bid": 105.0,
            "ask": 105.2,
        },
        {
            "time_utc": "2026-07-06T10:10:00.850+00:00",
            "bid": 108.0,
            "ask": 108.2,
        },
    ])

    result = strategy_simulator.simulate_trade(
        trade,
        ticks,
        strategy_name=policy.policy_id,
        policy=policy,
        provider_signal=provider_signal,
        require_provider_timeline=True,
        level_timeline_authority="mt5_execution",
        money_converter=VerifiedMoney(),
        verified_utc_offset_seconds=10_800,
        baseline_audit={"status": "exact"},
    )

    ticket = result["tickets"][0]
    assert result["strategy_pnl"] == 42.17
    assert ticket["strategy_pnl"] == 42.17
    assert ticket["pnl_source"] == "verified_broker_money_contract"
    assert ticket["pnl_currency"] == "EUR"
    assert ticket["money_status"] == "verified"
    assert ticket["money_blockers"] == []


def test_money_conversion_failure_blocks_counterfactual_row():
    class BlockedMoney:
        currency = "EUR"

        def convert_leg(self, **kwargs):
            return {
                "status": "blocked",
                "strategy_pnl": None,
                "pnl_currency": "EUR",
                "profit_currency_pnl": 8.0,
                "conversion": None,
                "blockers": ["stale_conversion_quote:EURUSD"],
            }

    policy = StrategyPolicy(
        policy_id="runner",
        close_legs=0,
        be_legs=0,
        runner_legs=1,
        base_leg_count=1,
    )
    result = strategy_simulator.simulate_trade(
        _trade(tickets=[_managed_ticket(101, 108.0)]),
        _ticks([
            {
                "time_utc": "2026-07-06T10:10:00+00:00",
                "bid": 108.0,
                "ask": 108.2,
            },
        ]),
        strategy_name=policy.policy_id,
        policy=policy,
        provider_signal={
            "management_events": [{
                "observed_ts_utc": "2026-07-06T10:05:00+00:00",
                "classified_action": "MOVE_SL_TO_BE",
            }],
        },
        require_provider_timeline=True,
        level_timeline_authority="mt5_execution",
        money_converter=BlockedMoney(),
        baseline_audit={"status": "exact"},
    )

    assert result["status"] == "blocked"
    assert result["strategy_pnl"] is None
    assert "stale_conversion_quote:EURUSD" in result["blockers"]


def test_management_choice_exposes_each_provider_option_as_a_trigger():
    policy = StrategyPolicy(
        policy_id="runner",
        close_legs=0,
        be_legs=0,
        runner_legs=1,
        base_leg_count=1,
        trigger_action="MOVE_SL_TO_BE",
    )
    provider_signal = {
        "management_events": [{
            "observed_ts_utc": "2026-07-06T10:05:00+00:00",
            "classified_action": "MANAGEMENT_CHOICE",
            "execution_options": [
                {"action": "CLOSE_ALL"},
                {"action": "MOVE_SL_TO_BE"},
            ],
        }]
    }

    trigger, source = strategy_simulator._management_trigger(
        {}, policy, provider_signal=provider_signal)

    assert trigger.isoformat() == "2026-07-06T10:05:00+00:00"
    assert source == "canonical_provider_management"


def test_required_provider_timeline_rejects_execution_only_management_event():
    policy = StrategyPolicy(
        policy_id="runner",
        close_legs=0,
        be_legs=0,
        runner_legs=1,
        base_leg_count=1,
    )
    trade = _trade(
        management=[{
            "ts": "2026-07-06T10:05:00+00:00",
            "classified": "MOVE_SL_TO_BE",
        }],
        tickets=[_managed_ticket(101, 105.0)],
    )
    provider_signal = {
        "provider_signal_id": "canal1_1",
        "management_events": [],
        "level_timeline": [{
            "telegram_ts_utc": "2026-07-06T10:00:00+00:00",
            "tps": [105.0],
            "sl": 90.0,
        }],
    }

    result = strategy_simulator.simulate_trade(
        trade,
        _ticks([]),
        strategy_name=policy.policy_id,
        policy=policy,
        provider_signal=provider_signal,
        require_provider_timeline=True,
        baseline_audit={"status": "exact"},
        default_unit_value=1.0,
    )

    assert result["status"] == "blocked"
    assert "missing_provider_management_trigger:MOVE_SL_TO_BE" in result["blockers"]


def test_executed_mt5_mode_falls_back_to_confirmed_be_transition():
    policy = StrategyPolicy(
        policy_id="runner",
        close_legs=0,
        be_legs=0,
        runner_legs=1,
        base_leg_count=1,
    )
    trade = _trade(
        management=[],
        tickets=[_managed_ticket(101, 108.0)],
    )

    result = strategy_simulator.simulate_trade(
        trade,
        _ticks([
            {
                "time_utc": "2026-07-06T10:06:00+00:00",
                "bid": 100.0,
                "ask": 100.2,
            },
            {
                "time_utc": "2026-07-06T10:10:00+00:00",
                "bid": 108.0,
                "ask": 108.2,
            },
        ]),
        strategy_name=policy.policy_id,
        policy=policy,
        provider_signal={
            "provider_signal_id": "canal1_1",
            "management_events": [],
        },
        require_provider_timeline=True,
        level_timeline_authority="mt5_execution",
        baseline_audit={"status": "exact"},
        default_unit_value=1.0,
    )

    assert result["status"] == "simulated"
    assert result["management_trigger_utc"] == (
        "2026-07-06T10:05:00+00:00"
    )
    assert result["management_trigger_source"] == (
        "confirmed_mt5_level_history"
    )
    assert result["tickets"][0]["close_reason"] == "tp"
    assert result["tickets"][0]["close_price"] == 108.0


def test_provider_management_before_trade_open_is_not_applied():
    policy = StrategyPolicy(
        policy_id="runner",
        close_legs=0,
        be_legs=0,
        runner_legs=1,
        base_leg_count=1,
    )
    trade = _trade(tickets=[_managed_ticket(101, 105.0)])
    provider_signal = {
        "provider_signal_id": "canal1_1",
        "management_events": [{
            "telegram_ts_utc": "2026-07-06T09:59:00+00:00",
            "classified_action": "MOVE_SL_TO_BE",
        }],
        "level_timeline": [{
            "telegram_ts_utc": "2026-07-06T09:58:00+00:00",
            "tps": [105.0],
            "sl": 90.0,
        }],
    }

    result = strategy_simulator.simulate_trade(
        trade,
        _ticks([]),
        strategy_name=policy.policy_id,
        policy=policy,
        provider_signal=provider_signal,
        require_provider_timeline=True,
        baseline_audit={"status": "exact"},
        default_unit_value=1.0,
    )

    assert result["status"] == "blocked"
    assert "management_trigger_before_trade_open" in result["blockers"]


def test_provider_management_before_later_leg_fill_blocks_trade():
    policy = StrategyPolicy(
        policy_id="close_1_runner_1",
        close_legs=1,
        be_legs=0,
        runner_legs=1,
        base_leg_count=2,
    )
    first = _managed_ticket(101, 105.0)
    second = _managed_ticket(102, 110.0)
    second["open_dt_utc"] = "2026-07-06T10:06:00+00:00"
    trade = _trade(tickets=[first, second])
    provider_signal = {
        "provider_signal_id": "canal1_1",
        "management_events": [{
            "observed_ts_utc": "2026-07-06T10:05:00+00:00",
            "classified_action": "MOVE_SL_TO_BE",
        }],
        "level_timeline": [{
            "observed_ts_utc": "2026-07-06T10:00:00+00:00",
            "tps": [105.0, 110.0],
            "sl": 90.0,
        }],
    }

    result = strategy_simulator.simulate_trade(
        trade,
        _ticks([]),
        strategy_name=policy.policy_id,
        policy=policy,
        provider_signal=provider_signal,
        require_provider_timeline=True,
        baseline_audit={"status": "exact"},
        default_unit_value=1.0,
    )

    assert result["status"] == "blocked"
    assert "management_trigger_before_ticket_open:102" in result["blockers"]


def test_provider_levels_are_not_available_before_bot_observed_them():
    policy = StrategyPolicy(
        policy_id="runner",
        close_legs=0,
        be_legs=0,
        runner_legs=1,
        base_leg_count=1,
    )
    trade = _trade(tickets=[_managed_ticket(101, 105.0)])
    provider_signal = {
        "provider_signal_id": "canal1_1",
        "management_events": [{
            "telegram_ts_utc": "2026-07-06T10:04:00+00:00",
            "observed_ts_utc": "2026-07-06T10:05:00+00:00",
            "classified_action": "MOVE_SL_TO_BE",
        }],
        "level_timeline": [{
            "telegram_ts_utc": "2026-07-06T10:00:00+00:00",
            "observed_ts_utc": "2026-07-06T10:06:00+00:00",
            "tps": [105.0],
            "sl": 90.0,
        }],
    }

    result = strategy_simulator.simulate_trade(
        trade,
        _ticks([]),
        strategy_name=policy.policy_id,
        policy=policy,
        provider_signal=provider_signal,
        require_provider_timeline=True,
        baseline_audit={"status": "exact"},
        default_unit_value=1.0,
    )

    assert result["status"] == "blocked"
    assert "missing_causal_tp_at_trigger:101" in result["blockers"]


def test_policy_blocks_when_canonical_provider_timeline_is_required_but_missing():
    policy = StrategyPolicy(
        policy_id="runner",
        close_legs=0,
        be_legs=0,
        runner_legs=1,
        base_leg_count=1,
    )
    trade = _trade(
        management=[{
            "ts": "2026-07-06T10:05:00+00:00",
            "classified": "MOVE_SL_TO_BE",
        }],
        tickets=[_managed_ticket(101, 105.0)],
    )

    result = strategy_simulator.simulate_trade(
        trade,
        _ticks([]),
        strategy_name=policy.policy_id,
        policy=policy,
        provider_signal=None,
        require_provider_timeline=True,
        baseline_audit={"status": "exact"},
        default_unit_value=1.0,
    )

    assert result["status"] == "blocked"
    assert "missing_canonical_provider_signal" in result["blockers"]


def test_policy_does_not_reopen_leg_that_hit_tp_before_management():
    policy = StrategyPolicy(
        policy_id="close_all",
        close_legs=1,
        be_legs=0,
        runner_legs=0,
        base_leg_count=1,
    )
    trade = _trade(
        management=[{
            "ts": "2026-07-06T10:05:00+00:00",
            "classified": "MOVE_SL_TO_BE",
        }],
        tickets=[_managed_ticket(101, 103.0)],
    )
    ticks = _ticks([
        {"time_utc": "2026-07-06T10:04:00+00:00", "bid": 103.0, "ask": 103.2},
        {"time_utc": "2026-07-06T10:05:00+00:00", "bid": 104.0, "ask": 104.2},
    ])

    result = strategy_simulator.simulate_trade(
        trade,
        ticks,
        strategy_name=policy.policy_id,
        policy=policy,
        baseline_audit={"status": "exact"},
        default_unit_value=1.0,
    )

    assert result["tickets"][0]["close_reason"] == "tp"
    assert result["tickets"][0]["close_time_utc"] == "2026-07-06T10:04:00+00:00"
    assert result["tickets"][0]["strategy_pnl"] == 3.0


def test_sell_management_close_uses_ask_price():
    policy = StrategyPolicy(
        policy_id="close_all",
        close_legs=1,
        be_legs=0,
        runner_legs=0,
        base_leg_count=1,
    )
    ticket = _managed_ticket(101, 90.0)
    ticket["sl_history"] = [
        {
            "ts": "2026-07-06T10:00:00+00:00",
            "status": "confirmed",
            "source": "initial",
            "sl": 110.0,
        },
        {
            "ts": "2026-07-06T10:05:00+00:00",
            "status": "confirmed",
            "source": "BE #101",
            "sl": 100.0,
        },
    ]
    trade = _trade(
        direction="SELL",
        management=[{
            "ts": "2026-07-06T10:05:00+00:00",
            "classified": "MOVE_SL_TO_BE",
        }],
        tickets=[ticket],
    )
    ticks = _ticks([
        {"time_utc": "2026-07-06T10:05:00+00:00", "bid": 95.0, "ask": 95.2},
    ])

    result = strategy_simulator.simulate_trade(
        trade,
        ticks,
        strategy_name=policy.policy_id,
        policy=policy,
        baseline_audit={"status": "exact"},
        default_unit_value=1.0,
    )

    assert result["tickets"][0]["touch_side"] == "ask"
    assert result["tickets"][0]["close_price"] == 95.2
    assert result["tickets"][0]["strategy_pnl"] == 4.8


def test_shared_result_cache_reuses_identical_leg_replay(monkeypatch):
    trade = _trade(
        management=[{
            "ts": "2026-07-06T10:05:00+00:00",
            "classified": "MOVE_SL_TO_BE",
        }],
        tickets=[_managed_ticket(101, 110.0)],
    )
    ticks = _ticks([
        {"time_utc": "2026-07-06T10:05:00+00:00", "bid": 104.0, "ask": 104.2},
        {"time_utc": "2026-07-06T10:10:00+00:00", "bid": 110.0, "ask": 110.2},
    ])
    first = StrategyPolicy(
        policy_id="runner_a",
        close_legs=0,
        be_legs=0,
        runner_legs=1,
        base_leg_count=1,
    )
    second = StrategyPolicy(
        policy_id="runner_b",
        close_legs=0,
        be_legs=0,
        runner_legs=1,
        base_leg_count=1,
    )
    calls = 0
    original = strategy_simulator._simulate_ticket_policy

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(strategy_simulator, "_simulate_ticket_policy", counted)
    result_cache = {}
    for policy in (first, second):
        strategy_simulator.simulate_trade(
            trade,
            ticks,
            strategy_name=policy.policy_id,
            policy=policy,
            baseline_audit={"status": "exact"},
            default_unit_value=1.0,
            result_cache=result_cache,
        )

    assert calls == 1
    assert len(result_cache) == 1


def test_portfolio_mfe_and_giveback_follow_aggregate_open_equity():
    policy = StrategyPolicy(
        policy_id="runner",
        close_legs=0,
        be_legs=0,
        runner_legs=1,
        base_leg_count=1,
    )
    ticket = _managed_ticket(101, 110.0)
    trade = _trade(
        pnl_real_mt5=0.0,
        management=[{
            "ts": "2026-07-06T10:05:00+00:00",
            "classified": "MOVE_SL_TO_BE",
        }],
        tickets=[ticket],
    )
    ticks = _ticks([
        {"time_utc": "2026-07-06T10:05:00+00:00", "bid": 104.0, "ask": 104.2},
        {"time_utc": "2026-07-06T10:10:00+00:00", "bid": 108.0, "ask": 108.2},
        {"time_utc": "2026-07-06T23:59:00+00:00", "bid": 104.0, "ask": 104.2},
    ])

    result = strategy_simulator.simulate_trade(
        trade,
        ticks,
        strategy_name=policy.policy_id,
        policy=policy,
        baseline_audit={"status": "exact"},
        default_unit_value=1.0,
    )

    assert result["strategy_pnl"] == 4.0
    assert result["mfe_pnl"] == 8.0
    assert result["mae_pnl"] == 4.0
    assert result["profit_giveback"] == 4.0
    assert result["mfe_capture_ratio"] == 0.5


def test_stop_loss_uses_first_tradable_tick_when_price_gaps_past_level():
    policy = StrategyPolicy(
        policy_id="runner",
        close_legs=0,
        be_legs=0,
        runner_legs=1,
        base_leg_count=1,
    )
    trade = _trade(
        management=[{
            "ts": "2026-07-06T10:05:00+00:00",
            "classified": "MOVE_SL_TO_BE",
        }],
        tickets=[_managed_ticket(101, 110.0)],
    )
    ticks = _ticks([
        {"time_utc": "2026-07-06T10:05:00+00:00", "bid": 101.0, "ask": 101.2},
        {"time_utc": "2026-07-06T10:06:00+00:00", "bid": 89.5, "ask": 89.7},
    ])

    result = strategy_simulator.simulate_trade(
        trade,
        ticks,
        strategy_name=policy.policy_id,
        policy=policy,
        baseline_audit={"status": "exact"},
        default_unit_value=1.0,
    )

    ticket = result["tickets"][0]
    assert ticket["close_reason"] == "sl"
    assert ticket["close_price"] == 89.5
    assert ticket["strategy_pnl"] == -10.5
