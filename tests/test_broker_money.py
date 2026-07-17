

def test_capture_contract_derives_vantage_eurusd_sides_without_private_account_data():
    from types import SimpleNamespace
    from tools import capture_broker_money_contract

    eurusd_ask = 1.14389
    eurusd_bid = 1.14348

    class FakeMT5:
        def account_info(self):
            return SimpleNamespace(
                login=123456,
                name="PRIVATE NAME",
                server="VantageMarkets-Demo",
                currency="EUR",
                currency_digits=2,
            )

        def symbol_info(self, symbol):
            if symbol == "XAUUSD":
                return SimpleNamespace(
                    name="XAUUSD",
                    trade_calc_mode=4,
                    trade_contract_size=100.0,
                    trade_tick_size=0.01,
                    trade_tick_value_profit=1 / eurusd_ask,
                    trade_tick_value_loss=1 / eurusd_bid,
                    currency_profit="USD",
                )
            if symbol == "EURUSD":
                return SimpleNamespace(name="EURUSD")
            return None

        def symbol_select(self, _symbol, _enabled):
            return True

        def symbol_info_tick(self, symbol):
            assert symbol == "EURUSD"
            return SimpleNamespace(bid=eurusd_bid, ask=eurusd_ask)

    contract = capture_broker_money_contract.build_contract(
        FakeMT5(),
        instrument_symbol="XAUUSD",
        captured_at=datetime(2026, 7, 17, 10, 0, tzinfo=timezone.utc),
    )

    assert contract["account"] == {
        "server": "VantageMarkets-Demo",
        "currency": "EUR",
        "currency_digits": 2,
    }
    assert "login" not in str(contract)
    assert "PRIVATE NAME" not in str(contract)
    assert contract["conversion"] == {
        "symbol": "EURUSD",
        "orientation": "account_base_profit_quote",
        "positive_profit_side": "ask",
        "negative_profit_side": "bid",
        "max_quote_age_ms": 5000,
    }
    assert contract["live_validation"]["valid"] is True

from datetime import datetime, timezone

import pandas as pd

import broker_money


def _contract():
    return {
        "schema_version": 1,
        "captured_at_utc": "2026-07-17T10:00:00+00:00",
        "account": {
            "server": "VantageMarkets-Demo",
            "currency": "EUR",
            "currency_digits": 2,
        },
        "instrument": {
            "symbol": "XAUUSD",
            "trade_calc_mode": 4,
            "contract_size": 100.0,
            "tick_size": 0.01,
            "currency_profit": "USD",
        },
        "conversion": {
            "symbol": "EURUSD",
            "orientation": "account_base_profit_quote",
            "max_quote_age_ms": 5000,
        },
        "costs": {
            "commission_model": "observed_zero_intraday",
            "fee_model": "observed_zero_intraday",
            "swap_model": "intraday_only_zero",
        },
        "live_validation": {
            "valid": True,
            "tick_value_profit_delta": 0.0,
            "tick_value_loss_delta": 0.0,
        },
    }


def _quotes():
    frame = pd.DataFrame([
        {
            "time_utc": "2026-07-09T14:54:47.900+00:00",
            "bid": 1.14320,
            "ask": 1.14335,
        },
        {
            "time_utc": "2026-07-09T14:54:48.545+00:00",
            "bid": 1.14326,
            "ask": 1.14340,
        },
    ])
    frame["time_utc"] = pd.to_datetime(frame["time_utc"], utc=True)
    return frame


def _converter(frame=None):
    quotes = _quotes() if frame is None else frame
    return broker_money.BrokerMoneyConverter(
        _contract(),
        quote_loader=lambda _day: (quotes, None),
    )


def test_profit_and_loss_use_the_correct_historical_conversion_side():
    converter = _converter()

    winner = converter.convert_leg(
        direction="SELL",
        open_price=4123.18,
        close_price=4123.11,
        volume=0.01,
        open_time_utc="2026-07-09T14:24:54+00:00",
        close_time_utc="2026-07-09T14:54:48.534+00:00",
    )
    loser = converter.convert_leg(
        direction="SELL",
        open_price=4123.18,
        close_price=4123.61,
        volume=0.01,
        open_time_utc="2026-07-09T14:24:54+00:00",
        close_time_utc="2026-07-09T14:54:48.534+00:00",
    )

    assert winner["status"] == "verified"
    assert winner["profit_currency_pnl"] == 0.07
    assert winner["strategy_pnl"] == 0.06
    assert winner["conversion"]["side"] == "ask"
    assert winner["conversion"]["price"] == 1.14335

    assert loser["status"] == "verified"
    assert loser["profit_currency_pnl"] == -0.43
    assert loser["strategy_pnl"] == -0.38
    assert loser["conversion"]["side"] == "bid"
    assert loser["conversion"]["price"] == 1.1432


def test_stale_conversion_quote_blocks_money_without_hiding_price_path():
    frame = _quotes().iloc[:1].copy()
    converter = _converter(frame)

    result = converter.convert_leg(
        direction="BUY",
        open_price=4120.0,
        close_price=4121.0,
        volume=0.01,
        open_time_utc="2026-07-09T14:20:00+00:00",
        close_time_utc="2026-07-09T14:55:00+00:00",
    )

    assert result["status"] == "blocked"
    assert result["strategy_pnl"] is None
    assert result["blockers"] == ["stale_conversion_quote:EURUSD"]


def test_apply_money_contract_sums_position_cents_and_preserves_price_value():
    converter = _converter()
    row = {
        "provider_signal_id": "canal1_20801",
        "status": "simulated_price_path",
        "result_unit": "xauusd_price_units",
        "money_status": "unverified",
        "strategy_value": -0.36,
        "strategy_pnl": None,
        "blockers": [],
        "legs": [
            {
                "status": "simulated",
                "open_time_utc": "2026-07-09T14:24:54+00:00",
                "open_price": 4123.18,
                "close_time_utc": "2026-07-09T14:54:48.534+00:00",
                "close_price": 4123.11,
                "volume": 0.01,
            },
            {
                "status": "simulated",
                "open_time_utc": "2026-07-09T14:24:54+00:00",
                "open_price": 4123.18,
                "close_time_utc": "2026-07-09T14:54:48.534+00:00",
                "close_price": 4123.61,
                "volume": 0.01,
            },
        ],
    }

    result = broker_money.apply_money_contract(
        row,
        direction="SELL",
        converter=converter,
    )

    assert result["status"] == "simulated_price_path"
    assert result["strategy_value"] == -0.36
    assert result["result_unit"] == "xauusd_price_units"
    assert result["money_status"] == "verified"
    assert result["pnl_currency"] == "EUR"
    assert result["strategy_pnl"] == -0.32
    assert [leg["strategy_pnl"] for leg in result["legs"]] == [0.06, -0.38]


def test_actual_deals_must_reconcile_to_every_cent_before_contract_is_verified():
    converter = _converter()
    trade = {
        "sig_id": "canal1_20801",
        "direction": "SELL",
        "tickets": [
            {
                "ticket": 1567171589,
                "open_dt_utc": "2026-07-09T14:24:54+00:00",
                "open_price": 4123.18,
                "close_dt_utc": "2026-07-09T14:54:48.534+00:00",
                "close_price": 4123.11,
                "volume": 0.01,
                "is_closed": True,
                "pnl_net": 0.06,
                "pnl_components": {
                    "profit": 0.06,
                    "commission": 0.0,
                    "swap": 0.0,
                    "fee": 0.0,
                    "net": 0.06,
                },
            },
            {
                "ticket": 1567171573,
                "open_dt_utc": "2026-07-09T14:24:54+00:00",
                "open_price": 4123.18,
                "close_dt_utc": "2026-07-09T14:54:48.534+00:00",
                "close_price": 4123.61,
                "volume": 0.01,
                "is_closed": True,
                "pnl_net": -0.38,
                "pnl_components": {
                    "profit": -0.38,
                    "commission": 0.0,
                    "swap": 0.0,
                    "fee": 0.0,
                    "net": -0.38,
                },
            },
        ],
    }

    validation = broker_money.validate_executed_money_contract(
        [trade],
        converter,
    )

    assert validation["verified"] is True
    assert validation["tickets_checked"] == 2
    assert validation["exact_tickets"] == 2
    assert validation["mismatched_tickets"] == 0
    assert validation["blocked_tickets"] == 0
    assert validation["blockers"] == []


def test_one_cent_actual_difference_keeps_money_contract_closed():
    converter = _converter()
    trade = {
        "sig_id": "canal1_20801",
        "direction": "SELL",
        "tickets": [{
            "ticket": 1567171589,
            "open_dt_utc": "2026-07-09T14:24:54+00:00",
            "open_price": 4123.18,
            "close_dt_utc": "2026-07-09T14:54:48.534+00:00",
            "close_price": 4123.11,
            "volume": 0.01,
            "is_closed": True,
            "pnl_net": 0.07,
            "pnl_components": {
                "profit": 0.07,
                "commission": 0.0,
                "swap": 0.0,
                "fee": 0.0,
                "net": 0.07,
            },
        }],
    }

    validation = broker_money.validate_executed_money_contract(
        [trade],
        converter,
    )

    assert validation["verified"] is False
    assert validation["mismatched_tickets"] == 1
    assert validation["rows"][0]["difference"] == -0.01
    assert validation["blockers"] == ["actual_money_reconciliation_mismatch:1"]


def test_actual_validation_uses_deal_milliseconds_when_close_is_second_truncated():
    converter = _converter()
    raw_server_ms = int(
        datetime(2026, 7, 9, 17, 54, 48, 856000, tzinfo=timezone.utc).timestamp()
        * 1000
    )
    trade = {
        "sig_id": "canal1_20801",
        "direction": "SELL",
        "tickets": [{
            "ticket": 1567171589,
            "open_dt_utc": "2026-07-09T14:24:54+00:00",
            "open_price": 4123.18,
            "close_dt_utc": "2026-07-09T14:54:48+00:00",
            "close_price": 4123.11,
            "volume": 0.01,
            "is_closed": True,
            "pnl_net": 0.06,
            "pnl_components": {
                "profit": 0.06,
                "commission": 0.0,
                "swap": 0.0,
                "fee": 0.0,
            },
            "close_deal": {"time_msc": raw_server_ms},
        }],
    }

    validation = broker_money.validate_executed_money_contract(
        [trade],
        converter,
    )

    assert validation["verified"] is True
    assert validation["blocked_tickets"] == 0


def test_actual_validation_adds_observed_swap_to_price_pnl():
    frame = pd.DataFrame([{
        "time_utc": "2026-07-12T22:01:34.900+00:00",
        "bid": 1.1399,
        "ask": 1.14,
    }])
    frame["time_utc"] = pd.to_datetime(frame["time_utc"], utc=True)
    converter = _converter(frame)
    trade = {
        "sig_id": "canal1_20827",
        "direction": "SELL",
        "tickets": [{
            "ticket": 1575068804,
            "open_dt_utc": "2026-07-10T17:38:26+00:00",
            "open_price": 4104.1,
            "close_dt_utc": "2026-07-12T22:01:35+00:00",
            "close_price": 4097.0,
            "volume": 0.01,
            "is_closed": True,
            "pnl_net": 6.48,
            "pnl_components": {
                "profit": 6.23,
                "commission": 0.0,
                "swap": 0.25,
                "fee": 0.0,
            },
        }],
    }

    validation = broker_money.validate_executed_money_contract(
        [trade],
        converter,
    )

    assert validation["verified"] is True
    assert validation["exact_tickets"] == 1

