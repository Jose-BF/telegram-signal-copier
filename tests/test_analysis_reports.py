from types import SimpleNamespace

import executor
from analysis import bot_execution_quality, daily_report


def _position(position_id, pnl, server_close):
    return {
        "position_id": position_id,
        "pnl_net": pnl,
        "is_closed": True,
        "close_deal": {"time_utc": server_close},
    }


def test_daily_report_separates_signal_cohort_from_server_calendar():
    ledger = [
        {
            "sig_id": "canal1_1",
            "channel": "canal1",
            "direction": "BUY",
            "signal_dt_utc": "2026-07-10T20:00:00+00:00",
            "pnl_real_mt5": 30.0,
            "positions": [
                _position(1, 30.0, "2026-07-13T00:01:00+00:00")],
        },
        {
            "sig_id": "canal1_2",
            "channel": "canal1",
            "direction": "SELL",
            "signal_dt_utc": "2026-07-13T08:00:00+00:00",
            "pnl_real_mt5": 20.12,
            "positions": [
                _position(2, 20.12, "2026-07-13T11:00:00+00:00")],
        },
        {
            "sig_id": "canal2_3",
            "channel": "canal2",
            "direction": "BUY",
            "signal_dt_utc": "2026-07-13T09:00:00+00:00",
            "pnl_real_mt5": -5.0,
            "positions": [
                _position(3, -5.0, "2026-07-13T12:00:00+00:00")],
        },
    ]
    accounting = [
        {"sig_id": "canal1_1", "status": "exact"},
        {"sig_id": "canal1_2", "status": "exact"},
        {"sig_id": "canal2_3", "status": "exact"},
    ]
    events = [{
        "ts": "2026-07-13T06:00:00+00:00",
        "sig": "bot",
        "ev": "mt5_account_connected",
        "currency": "EUR",
        "login": 123,
    }]

    report = daily_report.build_daily_report(
        "2026-07-13",
        ledger,
        accounting_rows=accounting,
        events=events,
    )

    assert report["signal_cohort_pnl"] == 15.12
    assert report["server_calendar_pnl"] == 45.12
    assert report["currency"] == "EUR"
    assert report["currency_source"] == "mt5_account_connected"
    assert report["signal_cohort"]["wins"] == 1
    assert report["signal_cohort"]["losses"] == 1


def test_reconstructed_trade_is_not_called_a_win_or_loss():
    ledger = [{
        "sig_id": "canal2_1",
        "channel": "canal2",
        "signal_dt_utc": "2026-07-13T08:00:00+00:00",
        "pnl_real_mt5": 10.0,
        "positions": [],
    }]

    report = daily_report.build_daily_report(
        "2026-07-13",
        ledger,
        accounting_rows=[{"sig_id": "canal2_1", "status": "reconstructed"}],
        events=[],
    )

    assert report["signal_cohort"]["wins"] == 0
    assert report["signal_cohort"]["losses"] == 0
    assert report["signal_cohort"]["unclassified_outcomes"] == 1
    assert report["currency"] is None


def test_execution_quality_accepts_classifier_be_event():
    events = [
        {"ev": "market_filled", "latency_ms": 100},
        {"ev": "signal_closed", "total_pl": 0.0},
        {
            "ev": "mgmt_msg",
            "action": "MOVE_SL_TO_BE",
            "will_apply": True,
        },
        {"ev": "be_armed_classifier"},
    ]

    result = bot_execution_quality.classify_execution(events)

    assert result["factors"]["be_armed_events"] == 1
    assert not any("sin be_armed" in issue for issue in result["issues"])


def test_executor_account_evidence_is_safe_and_serializable(monkeypatch):
    monkeypatch.setattr(
        executor.mt5,
        "account_info",
        lambda: SimpleNamespace(
            login=123,
            server="VantageInternational-Demo",
            name="Demo",
            currency="EUR",
            balance=10123.45,
            equity=10120.0,
        ),
    )

    assert executor.account_evidence() == {
        "login": 123,
        "server": "VantageInternational-Demo",
        "name": "Demo",
        "currency": "EUR",
        "balance": 10123.45,
        "equity": 10120.0,
    }


def test_executor_account_evidence_handles_unavailable_account(monkeypatch):
    monkeypatch.setattr(executor.mt5, "account_info", lambda: None)

    assert executor.account_evidence() == {}
