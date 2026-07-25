import hashlib
import json
import time
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

import simulation_oracle


UTC = timezone.utc


def _ticks(rows):
    frame = pd.DataFrame(rows, columns=["time_utc", "bid", "ask"])
    frame["time_utc"] = pd.to_datetime(frame["time_utc"], utc=True)
    return frame


def _event(ts, level, source):
    return {"ts": ts, "level": level, "source": source}


def test_buy_take_profit_uses_bid_and_target_fill():
    result = simulation_oracle.replay_first_close(
        direction="BUY",
        opened_at=datetime(2026, 7, 6, 10, 0, tzinfo=UTC),
        open_price=100.0,
        ticks=_ticks([
            {
                "time_utc": "2026-07-06T10:01:00.100+00:00",
                "bid": 109.9,
                "ask": 110.1,
            },
            {
                "time_utc": "2026-07-06T10:01:00.200+00:00",
                "bid": 110.2,
                "ask": 110.4,
            },
        ]),
        sl_events=[_event("2026-07-06T10:00:00+00:00", 90.0, "initial")],
        tp_events=[_event("2026-07-06T10:00:00+00:00", 110.0, "initial")],
        horizon_at=datetime(2026, 7, 6, 23, 59, 59, tzinfo=UTC),
        tick_size=0.01,
    )

    assert result["status"] == "simulated"
    assert result["close_reason"] == "tp"
    assert result["quote_side"] == "bid"
    assert result["close_price"] == 110.0
    assert result["touch_price"] == 110.2
    assert result["close_time_utc"] == "2026-07-06T10:01:00.200000+00:00"


def test_sell_take_profit_uses_ask():
    result = simulation_oracle.replay_first_close(
        direction="SELL",
        opened_at=datetime(2026, 7, 6, 10, 0, tzinfo=UTC),
        open_price=100.0,
        ticks=_ticks([
            {
                "time_utc": "2026-07-06T10:01:00.100+00:00",
                "bid": 90.0,
                "ask": 90.2,
            },
            {
                "time_utc": "2026-07-06T10:01:00.200+00:00",
                "bid": 89.7,
                "ask": 89.9,
            },
        ]),
        sl_events=[_event("2026-07-06T10:00:00+00:00", 110.0, "initial")],
        tp_events=[_event("2026-07-06T10:00:00+00:00", 90.0, "initial")],
        horizon_at=datetime(2026, 7, 6, 23, 59, 59, tzinfo=UTC),
        tick_size=0.01,
    )

    assert result["status"] == "simulated"
    assert result["close_reason"] == "tp"
    assert result["quote_side"] == "ask"
    assert result["close_price"] == 90.0
    assert result["touch_price"] == 89.9


def test_stop_gap_fills_at_first_executable_quote():
    result = simulation_oracle.replay_first_close(
        direction="BUY",
        opened_at=datetime(2026, 7, 6, 10, 0, tzinfo=UTC),
        open_price=100.0,
        ticks=_ticks([
            {
                "time_utc": "2026-07-06T10:01:00+00:00",
                "bid": 95.0,
                "ask": 95.2,
            },
            {
                "time_utc": "2026-07-06T10:02:00+00:00",
                "bid": 87.5,
                "ask": 87.7,
            },
        ]),
        sl_events=[_event("2026-07-06T10:00:00+00:00", 90.0, "initial")],
        tp_events=[_event("2026-07-06T10:00:00+00:00", 110.0, "initial")],
        horizon_at=datetime(2026, 7, 6, 23, 59, 59, tzinfo=UTC),
        tick_size=0.01,
    )

    assert result["status"] == "simulated"
    assert result["close_reason"] == "sl"
    assert result["trigger_level"] == 90.0
    assert result["close_price"] == 87.5


def test_conflicting_quotes_in_one_millisecond_block_when_order_changes_outcome():
    timestamp = "2026-07-06T10:01:00.123+00:00"
    result = simulation_oracle.replay_first_close(
        direction="BUY",
        opened_at=datetime(2026, 7, 6, 10, 0, tzinfo=UTC),
        open_price=100.0,
        ticks=_ticks([
            {"time_utc": timestamp, "bid": 110.1, "ask": 110.3},
            {"time_utc": timestamp, "bid": 89.8, "ask": 90.0},
        ]),
        sl_events=[_event("2026-07-06T10:00:00+00:00", 90.0, "initial")],
        tp_events=[_event("2026-07-06T10:00:00+00:00", 110.0, "initial")],
        horizon_at=datetime(2026, 7, 6, 23, 59, 59, tzinfo=UTC),
        tick_size=0.01,
    )

    assert result["status"] == "blocked"
    assert result["blockers"] == [
        "ambiguous_duplicate_tick_outcome:2026-07-06T10:01:00.123000+00:00"
    ]


def test_conflicting_levels_at_one_timestamp_block():
    result = simulation_oracle.replay_first_close(
        direction="BUY",
        opened_at=datetime(2026, 7, 6, 10, 0, tzinfo=UTC),
        open_price=100.0,
        ticks=_ticks([{
            "time_utc": "2026-07-06T10:01:00+00:00",
            "bid": 105.0,
            "ask": 105.2,
        }]),
        sl_events=[
            _event("2026-07-06T10:00:00+00:00", 90.0, "first"),
            _event("2026-07-06T10:00:00+00:00", 91.0, "second"),
        ],
        tp_events=[_event("2026-07-06T10:00:00+00:00", 110.0, "initial")],
        horizon_at=datetime(2026, 7, 6, 23, 59, 59, tzinfo=UTC),
        tick_size=0.01,
    )

    assert result["status"] == "blocked"
    assert result["blockers"] == [
        "conflicting_sl_events:2026-07-06T10:00:00+00:00"
    ]


def test_invalid_quote_blocks_instead_of_being_dropped():
    result = simulation_oracle.replay_first_close(
        direction="BUY",
        opened_at=datetime(2026, 7, 6, 10, 0, tzinfo=UTC),
        open_price=100.0,
        ticks=_ticks([{
            "time_utc": "2026-07-06T10:01:00+00:00",
            "bid": 101.0,
            "ask": 100.9,
        }]),
        sl_events=[_event("2026-07-06T10:00:00+00:00", 90.0, "initial")],
        tp_events=[_event("2026-07-06T10:00:00+00:00", 110.0, "initial")],
        horizon_at=datetime(2026, 7, 6, 23, 59, 59, tzinfo=UTC),
        tick_size=0.01,
    )

    assert result["status"] == "blocked"
    assert result["blockers"] == ["crossed_quote:0"]


def test_large_tick_window_is_evaluated_in_vectorized_time():
    count = 50_000
    frame = pd.DataFrame({
        "time_utc": pd.date_range(
            "2026-07-06T10:00:00+00:00",
            periods=count,
            freq="ms",
        ),
        "bid": [100.0] * count,
        "ask": [100.2] * count,
    })

    started = time.perf_counter()
    result = simulation_oracle.replay_first_close(
        direction="BUY",
        opened_at=datetime(2026, 7, 6, 10, 0, tzinfo=UTC),
        open_price=100.0,
        ticks=frame,
        sl_events=[_event(
            "2026-07-06T10:00:00+00:00",
            90.0,
            "initial",
        )],
        tp_events=[_event(
            "2026-07-06T10:00:00+00:00",
            110.0,
            "initial",
        )],
        horizon_at=datetime(2026, 7, 6, 23, 59, 59, tzinfo=UTC),
        tick_size=0.01,
    )
    elapsed = time.perf_counter() - started

    assert result["status"] == "simulated"
    assert result["close_reason"] == "horizon_close"
    assert elapsed < 2.0


def _money_contract(**overrides):
    contract = {
        "schema_version": 1,
        "account": {"currency": "EUR", "currency_digits": 2},
        "instrument": {
            "symbol": "XAUUSD",
            "trade_calc_mode": 4,
            "contract_size": 100.0,
            "tick_size": 0.01,
            "currency_profit": "USD",
        },
        "conversion": {
            "orientation": "account_base_profit_quote",
            "symbol": "EURUSD",
            "max_quote_age_ms": 5_000,
            "max_quote_interval_ms": 60_000,
        },
        "costs": {
            "commission_model": "observed_zero_intraday",
            "fee_model": "observed_zero_intraday",
            "swap_model": "intraday_only_zero",
        },
        "live_validation": {"valid": True},
    }
    for key, value in overrides.items():
        contract[key] = value
    return contract


def _conversion_quotes():
    return _ticks([
        {
            "time_utc": "2026-07-06T10:00:00+00:00",
            "bid": 1.10,
            "ask": 1.20,
        },
        {
            "time_utc": "2026-07-06T10:00:01+00:00",
            "bid": 1.11,
            "ask": 1.21,
        },
    ])


def test_independent_money_uses_conversion_side_and_rounds_to_cents():
    loads = []

    def load_quotes(day):
        loads.append(day)
        return _conversion_quotes(), None

    oracle = simulation_oracle.IndependentMoneyOracle(
        _money_contract(),
        quote_loader=load_quotes,
    )

    winner = oracle.convert_leg(
        direction="BUY",
        open_price=100.0,
        close_price=110.0,
        volume=0.01,
        open_time_utc="2026-07-06T09:59:00+00:00",
        close_time_utc="2026-07-06T10:00:00.500+00:00",
    )
    loser = oracle.convert_leg(
        direction="BUY",
        open_price=100.0,
        close_price=90.0,
        volume=0.01,
        open_time_utc="2026-07-06T09:59:00+00:00",
        close_time_utc="2026-07-06T10:00:00.500+00:00",
    )

    assert winner["status"] == "verified"
    assert winner["profit_currency_pnl"] == 10.0
    assert winner["strategy_pnl"] == 8.33
    assert winner["conversion"]["side"] == "ask"
    assert winner["conversion"]["price"] == 1.2
    assert loser["strategy_pnl"] == -9.09
    assert loser["conversion"]["side"] == "bid"
    assert loser["conversion"]["price"] == 1.1
    assert len(loads) == 1


def test_prepared_tick_window_preserves_close_semantics():
    frame = _ticks([
        {
            "time_utc": "2026-07-06T10:01:00+00:00",
            "bid": 109.9,
            "ask": 110.1,
        },
        {
            "time_utc": "2026-07-06T10:02:00+00:00",
            "bid": 110.2,
            "ask": 110.4,
        },
    ])

    prepared, blockers = simulation_oracle.prepare_tick_window(frame)
    result = simulation_oracle.replay_first_close(
        direction="BUY",
        opened_at=datetime(2026, 7, 6, 10, 0, tzinfo=UTC),
        open_price=100.0,
        ticks=prepared,
        sl_events=[_event(
            "2026-07-06T10:00:00+00:00",
            90.0,
            "initial",
        )],
        tp_events=[_event(
            "2026-07-06T10:00:00+00:00",
            110.0,
            "initial",
        )],
        horizon_at=datetime(2026, 7, 6, 23, 59, 59, tzinfo=UTC),
        tick_size=0.01,
    )

    assert blockers == []
    assert result["status"] == "simulated"
    assert result["close_reason"] == "tp"
    assert result["close_time_utc"] == "2026-07-06T10:02:00+00:00"


def test_independent_money_rejects_stale_conversion_quote():
    oracle = simulation_oracle.IndependentMoneyOracle(
        _money_contract(),
        quote_loader=lambda _day: (_conversion_quotes().iloc[:1], None),
    )

    result = oracle.convert_leg(
        direction="BUY",
        open_price=100.0,
        close_price=110.0,
        volume=0.01,
        open_time_utc="2026-07-06T09:59:00+00:00",
        close_time_utc="2026-07-06T10:00:10+00:00",
    )

    assert result["status"] == "blocked"
    assert result["blockers"] == ["stale_conversion_quote:EURUSD"]


def test_independent_money_rejects_unsupported_cost_model():
    contract = _money_contract()
    contract["costs"]["commission_model"] = "unknown"

    try:
        simulation_oracle.IndependentMoneyOracle(
            contract,
            quote_loader=lambda _day: (_conversion_quotes(), None),
        )
    except ValueError as exc:
        assert str(exc) == "unsupported_commission_model"
    else:
        raise AssertionError("unsupported commission model was accepted")


def test_independent_money_rejects_overnight_without_cost_evidence():
    oracle = simulation_oracle.IndependentMoneyOracle(
        _money_contract(),
        quote_loader=lambda _day: (_conversion_quotes(), None),
    )

    result = oracle.convert_leg(
        direction="BUY",
        open_price=100.0,
        close_price=110.0,
        volume=0.01,
        open_time_utc="2026-07-06T23:59:00+00:00",
        close_time_utc="2026-07-07T00:01:00+00:00",
    )

    assert result["status"] == "blocked"
    assert result["blockers"] == ["overnight_cost_model_unverified"]


def _identity_money_oracle():
    contract = _money_contract()
    contract["account"] = {"currency": "USD", "currency_digits": 2}
    contract["instrument"]["contract_size"] = 1.0
    contract["conversion"] = {
        "orientation": "identity",
        "symbol": None,
        "max_quote_age_ms": 5_000,
        "max_quote_interval_ms": 60_000,
    }
    return simulation_oracle.IndependentMoneyOracle(
        contract,
        quote_loader=lambda _day: (pd.DataFrame(), None),
    )


def _oracle_ticket(ticket, tp, **overrides):
    row = {
        "ticket": ticket,
        "open_dt_utc": "2026-07-06T10:00:00+00:00",
        "open_price": 100.0,
        "close_dt_utc": "2026-07-06T10:06:00+00:00",
        "close_price": 100.0,
        "close_reason": "be",
        "is_closed": True,
        "volume": 1.0,
        "pnl_net": 0.0,
        "sl_history": [
            {
                "ts": "2026-07-06T10:00:00+00:00",
                "status": "confirmed",
                "source": "SL/TP initial",
                "sl": 90.0,
            },
            {
                "ts": "2026-07-06T10:05:00+00:00",
                "status": "confirmed",
                "source": f"BE #{ticket}",
                "sl": 100.0,
            },
        ],
        "tp_history": [{
            "ts": "2026-07-06T10:00:00+00:00",
            "status": "confirmed",
            "source": "SL/TP initial",
            "tp": tp,
        }],
    }
    row.update(overrides)
    return row


def _oracle_trade(tickets):
    return {
        "sig_id": "canal2_380",
        "channel": "canal2",
        "direction": "BUY",
        "open_dt_utc": "2026-07-06T10:00:00+00:00",
        "pnl_real_mt5": 0.0,
        "tickets": tickets,
    }


def _oracle_provider():
    return {
        "provider_signal_id": "canal2_380",
        "management_events": [{
            "observed_ts_utc": "2026-07-06T10:05:00+00:00",
            "classified_action": "MOVE_SL_TO_BE",
        }],
    }


def test_policy_oracle_independently_allocates_close_be_and_runner_legs():
    trade = _oracle_trade([
        _oracle_ticket(101, 105.0),
        _oracle_ticket(102, 110.0),
        _oracle_ticket(103, 115.0),
    ])
    policy = {
        "policy_id": "close_1_be_1_runner_1",
        "mode": "risk_free_allocation",
        "close_legs": 1,
        "be_legs": 1,
        "runner_legs": 1,
        "base_leg_count": 3,
        "trigger_action": "MOVE_SL_TO_BE",
        "entry_policy": "actual_mt5",
        "horizon_policy": "eod_close",
    }
    ticks = _ticks([
        {
            "time_utc": "2026-07-06T10:04:00+00:00",
            "bid": 102.0,
            "ask": 102.2,
        },
        {
            "time_utc": "2026-07-06T10:05:00+00:00",
            "bid": 104.0,
            "ask": 104.2,
        },
        {
            "time_utc": "2026-07-06T10:06:00+00:00",
            "bid": 99.0,
            "ask": 99.2,
        },
        {
            "time_utc": "2026-07-06T10:10:00+00:00",
            "bid": 115.0,
            "ask": 115.2,
        },
    ])

    result = simulation_oracle.replay_policy_trade(
        trade=trade,
        ticks=ticks,
        policy=policy,
        provider_signal=_oracle_provider(),
        money_oracle=_identity_money_oracle(),
        tick_size=0.01,
    )

    assert result["status"] == "simulated"
    assert result["strategy_pnl"] == 18.0
    assert [row["leg_action"] for row in result["tickets"]] == [
        "close_now",
        "move_to_be",
        "runner",
    ]
    assert [row["close_reason"] for row in result["tickets"]] == [
        "management_close",
        "sl",
        "tp",
    ]
    assert [row["close_price"] for row in result["tickets"]] == [
        104.0,
        99.0,
        115.0,
    ]


def test_policy_oracle_does_not_treat_be_reassignment_as_new_take_profit():
    ticket = _oracle_ticket(
        101,
        110.0,
        tp_history=[
            {
                "ts": "2026-07-06T10:00:00+00:00",
                "status": "confirmed",
                "source": "SL/TP[0]",
                "tp": 110.0,
            },
            {
                "ts": "2026-07-06T10:05:00+00:00",
                "status": "confirmed",
                "source": "SL/TP[0] (BE)",
                "tp": 105.0,
            },
        ],
    )
    policy = {
        "policy_id": "no_be",
        "mode": "risk_free_allocation",
        "close_legs": 0,
        "be_legs": 0,
        "runner_legs": 1,
        "base_leg_count": 1,
        "trigger_action": "MOVE_SL_TO_BE",
        "entry_policy": "actual_mt5",
        "horizon_policy": "eod_close",
    }

    result = simulation_oracle.replay_policy_trade(
        trade=_oracle_trade([ticket]),
        ticks=_ticks([
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
        ]),
        policy=policy,
        provider_signal=_oracle_provider(),
        money_oracle=_identity_money_oracle(),
        tick_size=0.01,
    )

    assert result["status"] == "simulated"
    assert result["tickets"][0]["close_reason"] == "tp"
    assert result["tickets"][0]["close_price"] == 110.0


def test_policy_oracle_prefers_mt5_snapshot_over_same_time_confirmation():
    timestamp = "2026-07-06T10:00:00+00:00"
    ticket = _oracle_ticket(
        101,
        111.0,
        tp_history=[
            {
                "ts": timestamp,
                "status": "confirmed",
                "source": "SL/TP[0]",
                "tp": 110.0,
            },
            {
                "ts": timestamp,
                "status": "snapshot",
                "source": "SL/TP[0]",
                "tp": 111.0,
            },
        ],
    )
    policy = {
        "policy_id": "no_be",
        "mode": "risk_free_allocation",
        "close_legs": 0,
        "be_legs": 0,
        "runner_legs": 1,
        "base_leg_count": 1,
        "trigger_action": "MOVE_SL_TO_BE",
        "entry_policy": "actual_mt5",
        "horizon_policy": "eod_close",
    }

    result = simulation_oracle.replay_policy_trade(
        trade=_oracle_trade([ticket]),
        ticks=_ticks([
            {
                "time_utc": "2026-07-06T10:06:00+00:00",
                "bid": 110.0,
                "ask": 110.2,
            },
            {
                "time_utc": "2026-07-06T10:07:00+00:00",
                "bid": 111.0,
                "ask": 111.2,
            },
        ]),
        policy=policy,
        provider_signal=_oracle_provider(),
        money_oracle=_identity_money_oracle(),
        tick_size=0.01,
    )

    assert result["status"] == "simulated"
    assert result["tickets"][0]["close_reason"] == "tp"
    assert result["tickets"][0]["close_price"] == 111.0


def test_policy_oracle_blocks_missing_volume_without_fallback():
    ticket = _oracle_ticket(101, 110.0, volume=None)
    policy = {
        "policy_id": "no_be",
        "mode": "risk_free_allocation",
        "close_legs": 0,
        "be_legs": 0,
        "runner_legs": 1,
        "base_leg_count": 1,
        "trigger_action": "MOVE_SL_TO_BE",
        "entry_policy": "actual_mt5",
        "horizon_policy": "eod_close",
    }

    result = simulation_oracle.replay_policy_trade(
        trade=_oracle_trade([ticket]),
        ticks=_ticks([{
            "time_utc": "2026-07-06T10:10:00+00:00",
            "bid": 110.0,
            "ask": 110.2,
        }]),
        policy=policy,
        provider_signal=_oracle_provider(),
        money_oracle=_identity_money_oracle(),
        tick_size=0.01,
    )

    assert result["status"] == "blocked"
    assert result["blockers"] == ["invalid_ticket_volume:101"]


def test_policy_oracle_blocks_management_before_ticket_open():
    ticket = _oracle_ticket(
        101,
        110.0,
        open_dt_utc="2026-07-06T10:06:00+00:00",
    )
    policy = {
        "policy_id": "no_be",
        "mode": "risk_free_allocation",
        "close_legs": 0,
        "be_legs": 0,
        "runner_legs": 1,
        "base_leg_count": 1,
        "trigger_action": "MOVE_SL_TO_BE",
        "entry_policy": "actual_mt5",
        "horizon_policy": "eod_close",
    }

    result = simulation_oracle.replay_policy_trade(
        trade=_oracle_trade([ticket]),
        ticks=_ticks([{
            "time_utc": "2026-07-06T10:10:00+00:00",
            "bid": 110.0,
            "ask": 110.2,
        }]),
        policy=policy,
        provider_signal=_oracle_provider(),
        money_oracle=_identity_money_oracle(),
        tick_size=0.01,
    )

    assert result["status"] == "blocked"
    assert result["blockers"] == [
        "management_trigger_before_trade_open"
    ]


def test_policy_oracle_without_provider_be_keeps_actual_trade_unchanged():
    ticket = _oracle_ticket(
        101,
        110.0,
        sl_history=[{
            "ts": "2026-07-06T10:00:00+00:00",
            "status": "confirmed",
            "source": "SL/TP initial",
            "sl": 90.0,
        }],
        pnl_net=-10.0,
        close_price=90.0,
        close_reason="sl",
    )
    policy = {
        "policy_id": "no_be",
        "mode": "risk_free_allocation",
        "close_legs": 0,
        "be_legs": 0,
        "runner_legs": 1,
        "base_leg_count": 1,
        "trigger_action": "MOVE_SL_TO_BE",
        "entry_policy": "actual_mt5",
        "horizon_policy": "eod_close",
    }

    result = simulation_oracle.replay_policy_trade(
        trade=_oracle_trade([ticket]),
        ticks=_ticks([{
            "time_utc": "2026-07-06T10:10:00+00:00",
            "bid": 90.0,
            "ask": 90.2,
        }]),
        policy=policy,
        provider_signal={"management_events": []},
        money_oracle=_identity_money_oracle(),
        tick_size=0.01,
    )

    assert result["status"] == "unchanged"
    assert result["strategy_pnl"] == -10.0
    assert result["management_trigger_utc"] is None
    assert result["tickets"][0]["leg_action"] == (
        "unchanged_no_provider_trigger"
    )


def test_policy_oracle_uses_confirmed_mt5_be_when_provider_trigger_is_missing():
    policy = {
        "policy_id": "no_be",
        "mode": "risk_free_allocation",
        "close_legs": 0,
        "be_legs": 0,
        "runner_legs": 1,
        "base_leg_count": 1,
        "trigger_action": "MOVE_SL_TO_BE",
        "entry_policy": "actual_mt5",
        "horizon_policy": "eod_close",
    }

    result = simulation_oracle.replay_policy_trade(
        trade=_oracle_trade([_oracle_ticket(101, 110.0)]),
        ticks=_ticks([{
            "time_utc": "2026-07-06T10:10:00+00:00",
            "bid": 100.0,
            "ask": 100.2,
        }]),
        policy=policy,
        provider_signal={"management_events": []},
        money_oracle=_identity_money_oracle(),
        tick_size=0.01,
    )

    assert result["status"] == "simulated"
    assert result["management_trigger_utc"] == (
        "2026-07-06T10:05:00+00:00"
    )
    assert result["management_trigger_source"] == (
        "confirmed_mt5_level_history"
    )
    assert result["blockers"] == []


def _write_verified_tick_day(cache_dir, day, frame, *, symbol=None):
    cache_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = cache_dir / f"{day}.parquet"
    frame.to_parquet(parquet_path, index=False)
    digest = hashlib.sha256(parquet_path.read_bytes()).hexdigest()
    content_digest = hashlib.sha256()
    content_digest.update(b"time_bid_ask_sequence_sha256_v1\0")
    content_digest.update(str(len(frame)).encode("ascii") + b"\0")
    normalized_time = pd.to_datetime(frame["time_utc"], utc=True)
    for values in (
        normalized_time.astype("int64").to_numpy(dtype="<i8", copy=False),
        frame["bid"].to_numpy(dtype="<f8", copy=False),
        frame["ask"].to_numpy(dtype="<f8", copy=False),
    ):
        content_digest.update(np.ascontiguousarray(values).tobytes())
    quote_digest = content_digest.hexdigest()
    verified_symbol = symbol or "XAUUSD"
    payload = {
        "tick_time_contract": "mt5_server_epoch_utc_v3",
        "time_basis": "UTC",
        "source_time_basis": "mt5_server_epoch",
        "utc_offset_seconds": 10_800,
        "offset_detection_method": "fill_anchor",
        "offset_reference": {"signal_id": "canal2_380"},
        "semantic_time_valid": True,
        "anchor_validation": {
            "valid": True,
            "anchors_checked": 1,
            "anchors_matched": 1,
            "max_time_delta_ms": 0,
            "max_price_delta": 0.0,
            "errors": [],
        },
        "coverage": {
            "complete_from_utc": f"{day}T00:00:00+00:00",
            "complete_through_utc": (
                pd.Timestamp(day, tz="UTC") + pd.Timedelta(days=1)
            ).isoformat(),
            "row_count": len(frame),
        },
        "source_verification": {
            "verified": True,
            "method": "full_day_vs_two_half_days_v1",
            "content_digest": "time_bid_ask_sequence_sha256_v1",
            "symbol": verified_symbol,
            "primary_row_count": len(frame),
            "verification_row_count": len(frame),
            "primary_content_sha256": quote_digest,
            "verification_content_sha256": quote_digest,
            "errors": [],
        },
        "parquet_sha256": digest,
    }
    payload["symbol"] = verified_symbol
    sidecar = cache_dir / f"{day}.parquet.meta.json"
    sidecar.write_text(
        json.dumps(payload, sort_keys=True),
        encoding="utf-8",
    )
    return parquet_path, sidecar


def test_independent_tick_cache_verifies_hash_contract_and_preserves_order(
    tmp_path,
):
    cache_dir = tmp_path / "ticks"
    frame = _ticks([
        {
            "time_utc": "2026-07-06T10:00:00.123+00:00",
            "bid": 100.0,
            "ask": 100.2,
        },
        {
            "time_utc": "2026-07-06T10:00:00.123+00:00",
            "bid": 100.1,
            "ask": 100.3,
        },
    ])
    parquet_path, sidecar = _write_verified_tick_day(
        cache_dir,
        "2026-07-06",
        frame,
    )
    loader = simulation_oracle.IndependentTickCache(
        cache_dir,
        expected_symbol="XAUUSD",
        require_market_session=False,
    )

    loaded, evidence, blockers = loader.load_day(date(2026, 7, 6))

    assert blockers == []
    assert loaded["bid"].tolist() == [100.0, 100.1]
    assert evidence["parquet_sha256"] == hashlib.sha256(
        parquet_path.read_bytes()
    ).hexdigest()
    assert evidence["contract_sha256"] == hashlib.sha256(
        sidecar.read_bytes()
    ).hexdigest()
    assert evidence["row_count"] == 2


def test_independent_tick_cache_detects_artifact_changed_after_sidecar(
    tmp_path,
):
    cache_dir = tmp_path / "ticks"
    frame = _ticks([{
        "time_utc": "2026-07-06T10:00:00+00:00",
        "bid": 100.0,
        "ask": 100.2,
    }])
    parquet_path, _sidecar = _write_verified_tick_day(
        cache_dir,
        "2026-07-06",
        frame,
    )
    mutated = frame.copy()
    mutated.loc[0, "bid"] = 99.0
    mutated.to_parquet(parquet_path, index=False)
    loader = simulation_oracle.IndependentTickCache(
        cache_dir,
        expected_symbol="XAUUSD",
        require_market_session=False,
    )

    loaded, evidence, blockers = loader.load_day(date(2026, 7, 6))

    assert loaded.empty
    assert evidence is None
    assert blockers == ["parquet_hash_mismatch:2026-07-06"]


def test_independent_tick_cache_rejects_wrong_conversion_symbol(tmp_path):
    cache_dir = tmp_path / "ticks"
    frame = _ticks([{
        "time_utc": "2026-07-06T10:00:00+00:00",
        "bid": 1.1,
        "ask": 1.2,
    }])
    _write_verified_tick_day(
        cache_dir,
        "2026-07-06",
        frame,
        symbol="GBPUSD",
    )
    loader = simulation_oracle.IndependentTickCache(
        cache_dir,
        expected_symbol="EURUSD",
        require_market_session=False,
    )

    loaded, evidence, blockers = loader.load_day(date(2026, 7, 6))

    assert loaded.empty
    assert evidence is None
    assert blockers == ["tick_symbol_mismatch:2026-07-06"]


def test_independent_tick_cache_rejects_missing_source_verification(tmp_path):
    cache_dir = tmp_path / "ticks"
    frame = _ticks([{
        "time_utc": "2026-07-06T10:00:00+00:00",
        "bid": 100.0,
        "ask": 100.2,
    }])
    _parquet, sidecar = _write_verified_tick_day(
        cache_dir,
        "2026-07-06",
        frame,
    )
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload.pop("source_verification")
    sidecar.write_text(json.dumps(payload), encoding="utf-8")
    loader = simulation_oracle.IndependentTickCache(
        cache_dir,
        expected_symbol="XAUUSD",
        require_market_session=False,
    )

    loaded, evidence, blockers = loader.load_day(date(2026, 7, 6))

    assert loaded.empty
    assert evidence is None
    assert blockers == ["missing_tick_source_verification:2026-07-06"]


def test_independent_tick_cache_recomputes_source_content_digest(tmp_path):
    cache_dir = tmp_path / "ticks"
    frame = _ticks([{
        "time_utc": "2026-07-06T10:00:00+00:00",
        "bid": 100.0,
        "ask": 100.2,
    }])
    _parquet, sidecar = _write_verified_tick_day(
        cache_dir,
        "2026-07-06",
        frame,
    )
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload["source_verification"]["primary_content_sha256"] = "b" * 64
    payload["source_verification"]["verification_content_sha256"] = "b" * 64
    sidecar.write_text(json.dumps(payload), encoding="utf-8")
    loader = simulation_oracle.IndependentTickCache(
        cache_dir,
        expected_symbol="XAUUSD",
        require_market_session=False,
    )

    loaded, evidence, blockers = loader.load_day(date(2026, 7, 6))

    assert loaded.empty
    assert evidence is None
    assert blockers == ["tick_source_content_hash_mismatch:2026-07-06"]
