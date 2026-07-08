import pandas as pd

import strategy_simulator


def _ticks(rows):
    frame = pd.DataFrame(rows, columns=["time_utc", "bid", "ask"])
    frame["time_utc"] = pd.to_datetime(frame["time_utc"], utc=True)
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
