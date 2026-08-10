
import csv
import hashlib
import json
from datetime import date, datetime, timedelta, timezone

import pandas as pd

import broker_money


def _mt5_with_tick(at_utc: datetime):
    from types import SimpleNamespace

    return SimpleNamespace(
        symbol_info_tick=lambda _symbol: SimpleNamespace(
            time=int(at_utc.timestamp()),
            time_msc=int(at_utc.timestamp() * 1000),
            bid=4100.0,
            ask=4100.2,
        )
    )


def _account_fingerprint(
    *,
    server: str = "VantageMarkets-Demo",
    login: int = 123456,
) -> str:
    payload = {
        "login": str(login),
        "server": server,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _journal_record(*, snapshot: dict, payload_sha256: str | None = None):
    semantic = {
        "sig": "bot",
        "ev": "broker_money_contract_snapshot",
        "record_reason": "rollover_window",
        "snapshot": snapshot,
    }
    digest = hashlib.sha256(
        json.dumps(
            semantic,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return {
        **semantic,
        "schema_version": 2,
        "event_id": "event-1",
        "session_id": "session-1",
        "ts": "2026-07-27T21:05:00.000+00:00",
        "monotonic_ns": 1,
        "code_commit": "deadbeef",
        "payload_sha256": payload_sha256 or digest,
    }


def _write_mql_swap_evidence(
    path,
    *,
    captured_gmt_epoch: int,
    account_server: str = "VantageMarkets-Demo",
    symbol: str = "XAUUSD",
    swap_short: float = 27.41,
    last_server_tick_epoch: int | None = None,
):
    captured_server_epoch = captured_gmt_epoch + 3 * 3600
    native_tick_epoch = (
        captured_server_epoch
        if last_server_tick_epoch is None
        else last_server_tick_epoch
    )
    row = {
        "schema_version": 1,
        "captured_server_epoch": captured_server_epoch,
        "captured_gmt_epoch": captured_gmt_epoch,
        "last_server_tick_epoch": native_tick_epoch,
        "server_utc_offset_seconds": 3 * 3600,
        "server_tick_lag_seconds": captured_server_epoch - native_tick_epoch,
        "terminal_build": 5100,
        "account_server": account_server,
        "instrument_symbol": symbol,
        "swap_mode": 1,
        "swap_long": -75.82,
        "swap_short": swap_short,
        "swap_rollover3days": 3,
        "point": 0.01,
        "contract_size": 100.0,
        "currency_profit": "USD",
        "swap_sunday": 0.0,
        "swap_monday": 1.0,
        "swap_tuesday": 1.0,
        "swap_wednesday": 3.0,
        "swap_thursday": 1.0,
        "swap_friday": 1.0,
        "swap_saturday": 0.0,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    return row


def test_capture_contract_derives_vantage_eurusd_sides_without_private_account_data(
    tmp_path,
):
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
                    point=0.01,
                    swap_mode=1,
                    swap_long=-75.82,
                    swap_short=27.41,
                    swap_rollover3days=3,
                )
            if symbol == "EURUSD":
                return SimpleNamespace(name="EURUSD")
            return None

        def symbol_select(self, _symbol, _enabled):
            return True

        def symbol_info_tick(self, symbol):
            if symbol == "EURUSD":
                return SimpleNamespace(bid=eurusd_bid, ask=eurusd_ask)
            assert symbol == "XAUUSD"
            raw_tick_time = datetime(
                2026, 7, 17, 10, 0, tzinfo=timezone.utc
            )
            return SimpleNamespace(
                time=int(raw_tick_time.timestamp()),
                time_msc=int(raw_tick_time.timestamp() * 1000),
                bid=4100.0,
                ask=4100.2,
            )

    captured_at = datetime(2026, 7, 17, 10, 0, tzinfo=timezone.utc)
    evidence = tmp_path / "broker_swap_evidence.csv"
    _write_mql_swap_evidence(
        evidence,
        captured_gmt_epoch=int(captured_at.timestamp()),
    )
    contract = capture_broker_money_contract.build_contract(
        FakeMT5(),
        instrument_symbol="XAUUSD",
        captured_at=captured_at,
        mql_evidence_path=evidence,
    )

    assert contract["account"] == {
        "server": "VantageMarkets-Demo",
        "fingerprint": _account_fingerprint(),
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
        "max_quote_interval_ms": 60000,
    }
    assert contract["live_validation"]["valid"] is True
    assert contract["schema_version"] == 2
    assert contract["costs"]["swap_model"] == "mt5_points_rollover_v1"
    assert contract["costs"]["rollover_clock"] == "broker_midnight"
    assert contract["costs"]["snapshot_bracket_max_seconds"] == 900
    assert len(contract["swap_snapshots"]) == 1
    snapshot = contract["swap_snapshots"][0]
    assert snapshot["captured_at_utc"] == captured_at.isoformat()
    assert snapshot["time_evidence"]["source"] == "mql5_service_v1"
    assert snapshot["time_evidence"]["utc_offset_seconds"] == 10800
    assert snapshot["specification"] == {
        "contract_size": 100.0,
        "currency_profit": "USD",
        "point": 0.01,
        "swap_long": -75.82,
        "swap_mode": 1,
        "swap_rollover3days": 3,
        "swap_short": 27.41,
        "weekday_multipliers": {
            "friday": 1.0,
            "monday": 1.0,
            "saturday": 0.0,
            "sunday": 0.0,
            "thursday": 1.0,
            "tuesday": 1.0,
            "wednesday": 3.0,
        },
    }
    assert len(snapshot["specification_sha256"]) == 64


def test_swap_snapshot_uses_native_mql_capture_time_not_python_wall_clock(
    tmp_path,
):
    from types import SimpleNamespace
    from tools import capture_broker_money_contract

    native_time = datetime(2026, 7, 27, 20, 55, tzinfo=timezone.utc)
    python_time = datetime(
        2026, 7, 27, 20, 55, 2, 500000, tzinfo=timezone.utc
    )
    evidence = tmp_path / "broker_swap_evidence.csv"
    _write_mql_swap_evidence(
        evidence,
        captured_gmt_epoch=int(native_time.timestamp()),
    )
    instrument = SimpleNamespace(
        name="XAUUSD",
        point=0.01,
        trade_contract_size=100.0,
        currency_profit="USD",
        swap_mode=1,
        swap_long=-75.82,
        swap_short=27.41,
        swap_rollover3days=3,
    )

    snapshot = capture_broker_money_contract.capture_swap_snapshot(
        _mt5_with_tick(native_time),
        instrument,
        account_server="VantageMarkets-Demo",
        captured_at=python_time,
        mql_evidence_path=evidence,
    )

    assert snapshot["captured_at_utc"] == native_time.isoformat()
    assert snapshot["time_evidence"]["evidence_age_seconds"] == 2.5


def test_default_native_evidence_path_is_isolated_to_active_terminal(
    tmp_path,
):
    from types import SimpleNamespace
    from tools import capture_broker_money_contract

    data_path = tmp_path / "terminal-A"
    common_path = tmp_path / "common"
    mt5 = SimpleNamespace(
        terminal_info=lambda: SimpleNamespace(
            data_path=str(data_path),
            commondata_path=str(common_path),
        )
    )

    result = capture_broker_money_contract._default_mql_evidence_path(mt5)

    assert result == (
        data_path
        / "MQL5"
        / "Files"
        / "TelegramSignalCopier"
        / "broker_swap_evidence.csv"
    )
    assert common_path not in result.parents


def test_capture_rejects_local_clock_offset_not_confirmed_by_server_tick(
    tmp_path,
):
    from types import SimpleNamespace
    from tools import capture_broker_money_contract

    local_clock = datetime(2026, 7, 27, 11, 0, tzinfo=timezone.utc)
    actual_tick = datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc)
    evidence = tmp_path / "broker_swap_evidence.csv"
    _write_mql_swap_evidence(
        evidence,
        captured_gmt_epoch=int(local_clock.timestamp()),
    )
    instrument = SimpleNamespace(
        name="XAUUSD",
        point=0.01,
        trade_contract_size=100.0,
        currency_profit="USD",
        swap_mode=1,
        swap_long=-75.82,
        swap_short=27.41,
        swap_rollover3days=3,
    )

    try:
        capture_broker_money_contract.capture_swap_snapshot(
            _mt5_with_tick(actual_tick),
            instrument,
            account_server="VantageMarkets-Demo",
            captured_at=local_clock,
            mql_evidence_path=evidence,
        )
    except RuntimeError as exc:
        assert str(exc) == "MQL5/Python server tick time mismatch"
    else:
        raise AssertionError("unverified workstation clock offset must block")


def test_capture_accepts_newer_symbol_tick_within_snapshot_age(tmp_path):
    from types import SimpleNamespace
    from tools import capture_broker_money_contract

    native_time = datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc)
    captured_at = datetime(2026, 7, 27, 10, 0, 5, tzinfo=timezone.utc)
    python_tick = datetime(2026, 7, 27, 10, 0, 3, tzinfo=timezone.utc)
    evidence = tmp_path / "broker_swap_evidence.csv"
    _write_mql_swap_evidence(
        evidence,
        captured_gmt_epoch=int(native_time.timestamp()),
    )
    instrument = SimpleNamespace(
        name="XAUUSD",
        point=0.01,
        trade_contract_size=100.0,
        currency_profit="USD",
        swap_mode=1,
        swap_long=-75.82,
        swap_short=27.41,
        swap_rollover3days=3,
    )

    snapshot = capture_broker_money_contract.capture_swap_snapshot(
        _mt5_with_tick(python_tick),
        instrument,
        account_server="VantageMarkets-Demo",
        captured_at=captured_at,
        mql_evidence_path=evidence,
    )

    assert snapshot["captured_at_utc"] == native_time.isoformat()


def test_capture_accepts_python_tick_encoded_in_broker_server_epoch(
    tmp_path,
):
    from types import SimpleNamespace
    from tools import capture_broker_money_contract

    native_time = datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc)
    captured_at = datetime(2026, 7, 27, 10, 0, 47, tzinfo=timezone.utc)
    server_tick = datetime(2026, 7, 27, 13, 0, 47, tzinfo=timezone.utc)
    evidence = tmp_path / "broker_swap_evidence.csv"
    _write_mql_swap_evidence(
        evidence,
        captured_gmt_epoch=int(native_time.timestamp()),
    )
    instrument = SimpleNamespace(
        name="XAUUSD",
        point=0.01,
        trade_contract_size=100.0,
        currency_profit="USD",
        swap_mode=1,
        swap_long=-75.82,
        swap_short=27.41,
        swap_rollover3days=3,
    )

    snapshot = capture_broker_money_contract.capture_swap_snapshot(
        _mt5_with_tick(server_tick),
        instrument,
        account_server="VantageMarkets-Demo",
        captured_at=captured_at,
        mql_evidence_path=evidence,
    )

    assert snapshot["time_evidence"]["python_tick_time_basis"] == (
        "broker_server_epoch"
    )
    assert snapshot["time_evidence"]["python_tick_advance_seconds"] == 47


def test_capture_uses_live_python_tick_when_service_tick_is_stale(tmp_path):
    from types import SimpleNamespace
    from tools import capture_broker_money_contract

    native_time = datetime(2026, 8, 10, 6, 15, tzinfo=timezone.utc)
    stale_server_tick = int(native_time.timestamp()) + 3 * 3600 - 7 * 86400
    evidence = tmp_path / "broker_swap_evidence.csv"
    _write_mql_swap_evidence(
        evidence,
        captured_gmt_epoch=int(native_time.timestamp()),
        last_server_tick_epoch=stale_server_tick,
    )
    instrument = SimpleNamespace(
        name="XAUUSD",
        point=0.01,
        trade_contract_size=100.0,
        currency_profit="USD",
        swap_mode=1,
        swap_long=-75.82,
        swap_short=27.41,
        swap_rollover3days=3,
    )
    live_server_tick = native_time + timedelta(hours=3)

    snapshot = capture_broker_money_contract.capture_swap_snapshot(
        _mt5_with_tick(live_server_tick),
        instrument,
        account_server="VantageMarkets-Demo",
        captured_at=native_time + timedelta(seconds=2),
        mql_evidence_path=evidence,
    )

    time_evidence = snapshot["time_evidence"]
    assert time_evidence["python_tick_time_basis"] == "broker_server_epoch"
    assert time_evidence["python_tick_advance_seconds"] == 0
    assert time_evidence["mql_tick_fresh"] is False


def test_capture_fails_closed_when_native_mql_swap_evidence_is_missing(
    tmp_path,
):
    from types import SimpleNamespace
    from tools import capture_broker_money_contract

    instrument = SimpleNamespace(
        name="XAUUSD",
        point=0.01,
        trade_contract_size=100.0,
        currency_profit="USD",
        swap_mode=1,
        swap_long=-75.82,
        swap_short=27.41,
        swap_rollover3days=3,
    )

    try:
        capture_broker_money_contract.capture_swap_snapshot(
            _mt5_with_tick(
                datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc)
            ),
            instrument,
            account_server="VantageMarkets-Demo",
            captured_at=datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc),
            mql_evidence_path=tmp_path / "missing.csv",
        )
    except RuntimeError as exc:
        assert str(exc) == "missing verified MQL5 broker swap evidence"
    else:
        raise AssertionError("missing native evidence must block capture")


def test_capture_rejects_stale_or_mismatched_native_mql_evidence(tmp_path):
    from types import SimpleNamespace
    from tools import capture_broker_money_contract

    captured_at = datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc)
    evidence = tmp_path / "broker_swap_evidence.csv"
    _write_mql_swap_evidence(
        evidence,
        captured_gmt_epoch=int(captured_at.timestamp()) - 301,
        account_server="OtherBroker-Demo",
    )
    instrument = SimpleNamespace(
        name="XAUUSD",
        point=0.01,
        trade_contract_size=100.0,
        currency_profit="USD",
        swap_mode=1,
        swap_long=-75.82,
        swap_short=27.41,
        swap_rollover3days=3,
    )

    try:
        capture_broker_money_contract.capture_swap_snapshot(
            _mt5_with_tick(captured_at),
            instrument,
            account_server="VantageMarkets-Demo",
            captured_at=captured_at,
            mql_evidence_path=evidence,
        )
    except RuntimeError as exc:
        assert str(exc) == "MQL5 broker swap evidence account mismatch"
    else:
        raise AssertionError("mismatched native evidence must block capture")

    _write_mql_swap_evidence(
        evidence,
        captured_gmt_epoch=int(captured_at.timestamp()) - 301,
    )
    try:
        capture_broker_money_contract.capture_swap_snapshot(
            _mt5_with_tick(captured_at),
            instrument,
            account_server="VantageMarkets-Demo",
            captured_at=captured_at,
            mql_evidence_path=evidence,
        )
    except RuntimeError as exc:
        assert str(exc) == "stale MQL5 broker swap evidence"
    else:
        raise AssertionError("stale native evidence must block capture")


def test_capture_rejects_native_mql_values_that_disagree_with_python(tmp_path):
    from types import SimpleNamespace
    from tools import capture_broker_money_contract

    captured_at = datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc)
    evidence = tmp_path / "broker_swap_evidence.csv"
    _write_mql_swap_evidence(
        evidence,
        captured_gmt_epoch=int(captured_at.timestamp()),
        swap_short=99.0,
    )
    instrument = SimpleNamespace(
        name="XAUUSD",
        point=0.01,
        trade_contract_size=100.0,
        currency_profit="USD",
        swap_mode=1,
        swap_long=-75.82,
        swap_short=27.41,
        swap_rollover3days=3,
    )

    try:
        capture_broker_money_contract.capture_swap_snapshot(
            _mt5_with_tick(captured_at),
            instrument,
            account_server="VantageMarkets-Demo",
            captured_at=captured_at,
            mql_evidence_path=evidence,
        )
    except RuntimeError as exc:
        assert str(exc) == "MQL5/Python swap evidence mismatch: swap_short"
    else:
        raise AssertionError("disagreeing native evidence must block capture")

def _contract():
    return {
        "schema_version": 1,
        "captured_at_utc": "2026-07-17T10:00:00+00:00",
        "account": {
            "server": "VantageMarkets-Demo",
            "fingerprint": _account_fingerprint(),
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
            "max_quote_interval_ms": 60000,
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


def _swap_snapshot(
    captured_at_utc: str,
    *,
    swap_long: float = -75.82,
    swap_short: float = 27.41,
    utc_offset_seconds: int = 10800,
):
    from tools import capture_broker_money_contract

    specification = {
        "swap_mode": 1,
        "swap_long": swap_long,
        "swap_short": swap_short,
        "swap_rollover3days": 3,
        "point": 0.01,
        "contract_size": 100.0,
        "currency_profit": "USD",
        "weekday_multipliers": {
            "sunday": 0.0,
            "monday": 1.0,
            "tuesday": 1.0,
            "wednesday": 3.0,
            "thursday": 1.0,
            "friday": 1.0,
            "saturday": 0.0,
        },
    }
    return {
        "captured_at_utc": captured_at_utc,
        "account_server": "VantageMarkets-Demo",
        "account_fingerprint": _account_fingerprint(),
        "instrument_symbol": "XAUUSD",
        "time_evidence": {
            "source": "mql5_service_v1",
            "evidence_sha256": "a" * 64,
            "utc_offset_seconds": utc_offset_seconds,
        },
        "specification": specification,
        "specification_sha256": (
            capture_broker_money_contract.specification_sha256(specification)
        ),
    }


def _swap_contract(*snapshots):
    contract = _contract()
    contract["schema_version"] = 2
    contract["costs"] = {
        "commission_model": "observed_zero_intraday",
        "fee_model": "observed_zero_intraday",
        "swap_model": "mt5_points_rollover_v1",
        "rollover_clock": "broker_midnight",
        "snapshot_bracket_max_seconds": 900,
        "zero_multiplier_bracket_max_seconds": 72 * 3600,
    }
    contract["swap_snapshots"] = list(snapshots)
    return contract


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


def test_conversion_tick_cache_exposes_the_exact_verified_day_contract(
    tmp_path,
    monkeypatch,
):
    day = date(2026, 8, 4)
    evidence = {
        "day": day.isoformat(),
        "symbol": "EURUSD",
        "parquet_sha256": "a" * 64,
        "contract_sha256": "b" * 64,
        "size_bytes": 1234,
        "utc_offset_seconds": 10800,
    }
    quotes = _quotes()
    monkeypatch.setattr(
        broker_money.ensure_replay_tick_cache,
        "load_valid_day_contract",
        lambda *_args, **_kwargs: dict(evidence),
    )
    monkeypatch.setattr(broker_money.pd, "read_parquet", lambda *_a, **_k: quotes)
    cache = broker_money.VerifiedConversionTickCache(
        tmp_path,
        symbol="EURUSD",
    )

    frame, error = cache.load_day(day)

    assert error is None
    assert not frame.empty
    assert cache.evidence_by_day == {day.isoformat(): evidence}


def _swap_converter(*snapshots, frame=None):
    quotes = _quotes() if frame is None else frame
    return broker_money.BrokerMoneyConverter(
        _swap_contract(*snapshots),
        quote_loader=lambda _day: (quotes, None),
    )


def test_points_swap_uses_bracketed_rollover_spec_and_historical_fx_side():
    quotes = pd.DataFrame([
        {
            "time_utc": "2026-07-27T20:59:59.900+00:00",
            "bid": 1.1399,
            "ask": 1.14,
        },
        {
            "time_utc": "2026-07-27T21:30:00.000+00:00",
            "bid": 1.1401,
            "ask": 1.1402,
        },
    ])
    quotes["time_utc"] = pd.to_datetime(quotes["time_utc"], utc=True)
    converter = _swap_converter(
        _swap_snapshot("2026-07-27T20:55:00+00:00"),
        _swap_snapshot("2026-07-27T21:05:00+00:00"),
        frame=quotes,
    )

    result = converter.convert_leg(
        direction="SELL",
        open_price=4100.0,
        close_price=4100.0,
        volume=0.01,
        open_time_utc="2026-07-27T20:30:00+00:00",
        close_time_utc="2026-07-27T21:30:00+00:00",
    )

    assert result["status"] == "verified"
    assert result["price_strategy_pnl"] == 0.0
    assert result["swap_strategy_pnl"] == 0.24
    assert result["strategy_pnl"] == 0.24
    assert result["swap"]["rollovers"][0]["server_day"] == "monday"
    assert result["swap"]["rollovers"][0]["multiplier"] == 1.0
    assert result["swap"]["rollovers"][0]["conversion"]["side"] == "ask"
    assert result["swap"]["rollovers"][0]["conversion"]["price"] == 1.14


def test_real_vantage_rollover_20260727_matches_observed_account_cent():
    """Frozen live proof: two 0.01 SELL positions each received +0.24 EUR."""
    quotes = pd.DataFrame([{
        "time_utc": "2026-07-27T20:59:56.872+00:00",
        "bid": 1.13717,
        "ask": 1.13731,
    }])
    quotes["time_utc"] = pd.to_datetime(quotes["time_utc"], utc=True)
    converter = _swap_converter(
        _swap_snapshot("2026-07-27T20:55:11+00:00"),
        _swap_snapshot("2026-07-27T21:00:11+00:00"),
        frame=quotes,
    )

    for observed_swap in (0.24, 0.24):
        result = converter.convert_leg(
            direction="SELL",
            open_price=4081.25,
            close_price=4081.25,
            volume=0.01,
            open_time_utc="2026-07-27T16:54:07+00:00",
            close_time_utc="2026-07-27T21:00:29+00:00",
        )

        assert result["status"] == "verified"
        assert result["price_strategy_pnl"] == 0.0
        assert result["swap_strategy_pnl"] == observed_swap
        assert result["strategy_pnl"] == observed_swap
        assert result["swap"]["rollovers"] == [{
            "rollover_utc": "2026-07-27T21:00:00+00:00",
            "server_day": "monday",
            "multiplier": 1.0,
            "rate": 27.41,
            "profit_currency_pnl": 0.2741,
            "strategy_pnl": 0.24,
            "conversion": {
                "symbol": "EURUSD",
                "side": "ask",
                "price": 1.13731,
                "time_utc": "2026-07-27T20:59:56.872000+00:00",
                "age_ms": 3128,
                "freshness": "within_max_age",
                "quote_interval_ms": None,
                "next_quote_utc": None,
            },
            "pre_snapshot_utc": "2026-07-27T20:55:11+00:00",
            "post_snapshot_utc": "2026-07-27T21:00:11+00:00",
            "specification_sha256": (
                _swap_snapshot(
                    "2026-07-27T20:55:11+00:00"
                )["specification_sha256"]
            ),
            "evidence_mode": "rollover_window",
        }]


def test_triple_rollover_applies_the_captured_weekday_multiplier():
    quotes = pd.DataFrame([
        {
            "time_utc": "2026-07-29T20:59:59.900+00:00",
            "bid": 1.14,
            "ask": 1.1402,
        },
        {
            "time_utc": "2026-07-29T21:30:00.000+00:00",
            "bid": 1.1401,
            "ask": 1.1403,
        },
    ])
    quotes["time_utc"] = pd.to_datetime(quotes["time_utc"], utc=True)
    converter = _swap_converter(
        _swap_snapshot("2026-07-29T20:55:00+00:00"),
        _swap_snapshot("2026-07-29T21:05:00+00:00"),
        frame=quotes,
    )

    result = converter.convert_leg(
        direction="BUY",
        open_price=4100.0,
        close_price=4100.0,
        volume=0.01,
        open_time_utc="2026-07-29T20:30:00+00:00",
        close_time_utc="2026-07-29T21:30:00+00:00",
    )

    assert result["status"] == "verified"
    assert result["swap"]["rollovers"][0]["server_day"] == "wednesday"
    assert result["swap"]["rollovers"][0]["multiplier"] == 3.0
    assert result["swap"]["rollovers"][0]["profit_currency_pnl"] == -2.2746
    assert result["swap_strategy_pnl"] == -2.0
    assert result["strategy_pnl"] == -2.0


def test_overnight_swap_blocks_without_two_close_matching_snapshots():
    quotes = pd.DataFrame([{
        "time_utc": "2026-07-27T20:59:59.900+00:00",
        "bid": 1.1399,
        "ask": 1.14,
    }])
    quotes["time_utc"] = pd.to_datetime(quotes["time_utc"], utc=True)
    converter = _swap_converter(
        _swap_snapshot("2026-07-27T20:55:00+00:00"),
        frame=quotes,
    )

    result = converter.convert_leg(
        direction="SELL",
        open_price=4100.0,
        close_price=4100.0,
        volume=0.01,
        open_time_utc="2026-07-27T20:30:00+00:00",
        close_time_utc="2026-07-27T21:30:00+00:00",
    )

    assert result["status"] == "blocked"
    assert result["strategy_pnl"] is None
    assert result["blockers"] == [
        "missing_swap_rollover_bracket:2026-07-27T21:00:00+00:00"
    ]


def test_intraday_points_swap_accepts_verified_tick_clock_offset():
    converter = _swap_converter(
        _swap_snapshot("2026-08-02T22:11:56+00:00"),
    )

    result = converter.convert_leg(
        direction="BUY",
        open_price=4100.0,
        close_price=4100.0,
        volume=0.01,
        open_time_utc="2026-08-05T10:00:00+00:00",
        close_time_utc="2026-08-05T12:00:00+00:00",
        verified_utc_offset_seconds=10800,
    )

    assert result["status"] == "verified"
    assert result["strategy_pnl"] == 0.0
    assert result["swap"] == {
        "status": "verified",
        "strategy_pnl": 0.0,
        "profit_currency_pnl": 0.0,
        "rollovers": [],
        "offset_evidence": "verified_tick_contract",
        "blockers": [],
    }


def test_intraday_points_swap_still_blocks_without_clock_evidence():
    converter = _swap_converter(
        _swap_snapshot("2026-08-02T22:11:56+00:00"),
    )

    result = converter.convert_leg(
        direction="BUY",
        open_price=4100.0,
        close_price=4100.0,
        volume=0.01,
        open_time_utc="2026-08-05T10:00:00+00:00",
        close_time_utc="2026-08-05T12:00:00+00:00",
    )

    assert result["status"] == "blocked"
    assert result["blockers"] == ["missing_swap_offset_evidence"]


def test_verified_tick_clock_never_substitutes_rollover_snapshots():
    converter = _swap_converter(
        _swap_snapshot("2026-08-02T22:11:56+00:00"),
    )

    result = converter.convert_leg(
        direction="BUY",
        open_price=4100.0,
        close_price=4100.0,
        volume=0.01,
        open_time_utc="2026-08-05T20:30:00+00:00",
        close_time_utc="2026-08-05T21:30:00+00:00",
        verified_utc_offset_seconds=10800,
    )

    assert result["status"] == "blocked"
    assert result["blockers"] == [
        "missing_swap_rollover_bracket:2026-08-05T21:00:00+00:00"
    ]


def test_overnight_swap_blocks_when_broker_spec_changes_across_rollover():
    converter = _swap_converter(
        _swap_snapshot("2026-07-27T20:55:00+00:00", swap_short=27.41),
        _swap_snapshot("2026-07-27T21:05:00+00:00", swap_short=30.0),
    )

    result = converter.convert_leg(
        direction="SELL",
        open_price=4100.0,
        close_price=4100.0,
        volume=0.01,
        open_time_utc="2026-07-27T20:30:00+00:00",
        close_time_utc="2026-07-27T21:30:00+00:00",
    )

    assert result["status"] == "blocked"
    assert result["blockers"] == [
        "swap_spec_changed_at_rollover:2026-07-27T21:00:00+00:00"
    ]


def test_zero_multiplier_weekend_rollovers_use_matching_closure_evidence():
    converter = _swap_converter(
        _swap_snapshot("2026-07-24T21:05:00+00:00"),
        _swap_snapshot("2026-07-26T21:05:00+00:00"),
        frame=pd.DataFrame(columns=["time_utc", "bid", "ask"]),
    )

    result = converter.convert_leg(
        direction="SELL",
        open_price=4100.0,
        close_price=4100.0,
        volume=0.01,
        open_time_utc="2026-07-24T21:30:00+00:00",
        close_time_utc="2026-07-26T21:30:00+00:00",
    )

    assert result["status"] == "verified"
    assert result["strategy_pnl"] == 0.0
    assert result["swap_strategy_pnl"] == 0.0
    assert [
        (row["server_day"], row["multiplier"], row["evidence_mode"])
        for row in result["swap"]["rollovers"]
    ] == [
        ("saturday", 0.0, "market_closure"),
        ("sunday", 0.0, "market_closure"),
    ]
    assert all(
        row["conversion"]["freshness"] == "not_required_zero"
        for row in result["swap"]["rollovers"]
    )


def test_zero_multiplier_weekend_rollover_blocks_changed_specification():
    converter = _swap_converter(
        _swap_snapshot(
            "2026-07-24T21:05:00+00:00",
            swap_short=27.41,
        ),
        _swap_snapshot(
            "2026-07-26T21:05:00+00:00",
            swap_short=30.0,
        ),
    )

    result = converter.convert_leg(
        direction="SELL",
        open_price=4100.0,
        close_price=4100.0,
        volume=0.01,
        open_time_utc="2026-07-24T21:30:00+00:00",
        close_time_utc="2026-07-26T21:30:00+00:00",
    )

    assert result["status"] == "blocked"
    assert result["blockers"] == [
        "swap_spec_changed_across_market_closure:"
        "2026-07-25T21:00:00+00:00"
    ]


def test_zero_multiplier_weekday_never_uses_market_closure_evidence():
    from tools import capture_broker_money_contract

    snapshots = [
        _swap_snapshot("2026-07-26T21:05:00+00:00"),
        _swap_snapshot("2026-07-28T21:05:00+00:00"),
    ]
    for snapshot in snapshots:
        snapshot["specification"]["weekday_multipliers"]["monday"] = 0.0
        snapshot["specification_sha256"] = (
            capture_broker_money_contract.specification_sha256(
                snapshot["specification"]
            )
        )
    converter = _swap_converter(
        *snapshots,
        frame=pd.DataFrame(columns=["time_utc", "bid", "ask"]),
    )

    result = converter.convert_leg(
        direction="SELL",
        open_price=4100.0,
        close_price=4100.0,
        volume=0.01,
        open_time_utc="2026-07-27T20:30:00+00:00",
        close_time_utc="2026-07-27T21:30:00+00:00",
    )

    assert result["status"] == "blocked"
    assert result["blockers"] == [
        "missing_swap_rollover_bracket:2026-07-27T21:00:00+00:00"
    ]


def test_mixed_weekend_and_monday_rollovers_use_each_evidence_rule():
    quotes = pd.DataFrame([
        {
            "time_utc": "2026-07-27T20:59:59.900+00:00",
            "bid": 1.1399,
            "ask": 1.14,
        },
        {
            "time_utc": "2026-07-27T21:00:00.100+00:00",
            "bid": 1.1400,
            "ask": 1.1401,
        },
    ])
    quotes["time_utc"] = pd.to_datetime(quotes["time_utc"], utc=True)
    converter = _swap_converter(
        _swap_snapshot("2026-07-24T21:05:00+00:00"),
        _swap_snapshot("2026-07-26T21:05:00+00:00"),
        _swap_snapshot("2026-07-27T20:55:00+00:00"),
        _swap_snapshot("2026-07-27T21:05:00+00:00"),
        frame=quotes,
    )

    result = converter.convert_leg(
        direction="SELL",
        open_price=4100.0,
        close_price=4100.0,
        volume=0.01,
        open_time_utc="2026-07-24T21:30:00+00:00",
        close_time_utc="2026-07-27T21:30:00+00:00",
    )

    assert result["status"] == "verified"
    assert result["strategy_pnl"] == 0.24
    assert [
        (row["server_day"], row["multiplier"], row["evidence_mode"])
        for row in result["swap"]["rollovers"]
    ] == [
        ("saturday", 0.0, "market_closure"),
        ("sunday", 0.0, "market_closure"),
        ("monday", 1.0, "rollover_window"),
    ]


def test_snapshot_policy_records_startup_changes_and_rollover_brackets_only():
    from tools import capture_broker_money_contract

    midday = _swap_snapshot("2026-07-27T12:00:00+00:00")
    unchanged = _swap_snapshot("2026-07-27T12:05:00+00:00")
    before_rollover = _swap_snapshot("2026-07-27T20:55:00+00:00")
    after_rollover = _swap_snapshot("2026-07-27T21:05:00+00:00")
    changed = _swap_snapshot(
        "2026-07-27T14:00:00+00:00",
        swap_short=30.0,
    )

    assert capture_broker_money_contract.snapshot_record_reason(
        midday,
        None,
    ) == "startup"
    assert capture_broker_money_contract.snapshot_record_reason(
        unchanged,
        midday,
    ) is None
    assert capture_broker_money_contract.snapshot_record_reason(
        changed,
        midday,
    ) == "specification_changed"
    assert capture_broker_money_contract.snapshot_record_reason(
        before_rollover,
        midday,
    ) == "rollover_window"
    assert capture_broker_money_contract.snapshot_record_reason(
        after_rollover,
        before_rollover,
    ) == "rollover_window"


def test_snapshot_history_is_recovered_from_the_authoritative_event_stream(
    tmp_path,
):
    from tools import capture_broker_money_contract

    first = _swap_snapshot("2026-07-27T20:55:00+00:00")
    second = _swap_snapshot("2026-07-27T21:05:00+00:00")
    events = tmp_path / "trade_events.jsonl"
    events.write_text(
        "\n".join([
            json.dumps({"ev": "heartbeat", "ts": "ignored"}),
            json.dumps(_journal_record(snapshot=first)),
            json.dumps(_journal_record(snapshot=second)),
        ]) + "\n",
        encoding="utf-8",
    )

    snapshots = capture_broker_money_contract.load_event_snapshots(
        events,
        account_server="VantageMarkets-Demo",
        account_fingerprint=_account_fingerprint(),
        instrument_symbol="XAUUSD",
    )
    duplicated = capture_broker_money_contract.merge_swap_snapshots(
        snapshots,
        [first],
    )

    assert snapshots == [first, second]
    assert duplicated == [first, second]


def test_snapshot_history_rejects_tampering_and_wrong_runtime_identity(
    tmp_path,
):
    from tools import capture_broker_money_contract

    snapshot = _swap_snapshot("2026-07-27T21:05:00+00:00")
    events = tmp_path / "trade_events.jsonl"
    events.write_text(
        json.dumps(
            _journal_record(
                snapshot=snapshot,
                payload_sha256="0" * 64,
            )
        )
        + "\n",
        encoding="utf-8",
    )

    try:
        capture_broker_money_contract.load_event_snapshots(
            events,
            account_server="VantageMarkets-Demo",
            account_fingerprint=_account_fingerprint(),
            instrument_symbol="XAUUSD",
        )
    except ValueError as exc:
        assert str(exc) == "invalid broker snapshot event payload line 1"
    else:
        raise AssertionError("tampered event payload must not be recovered")

    events.write_text(
        json.dumps(_journal_record(snapshot=snapshot)) + "\n",
        encoding="utf-8",
    )
    try:
        capture_broker_money_contract.load_event_snapshots(
            events,
            account_server="VantageMarkets-Demo",
            account_fingerprint=_account_fingerprint(login=999999),
            instrument_symbol="XAUUSD",
        )
    except ValueError as exc:
        assert str(exc) == "broker snapshot account mismatch line 1"
    else:
        raise AssertionError("another account snapshot must not be recovered")


def test_schema2_contract_rejects_snapshot_from_another_symbol():
    snapshot = _swap_snapshot("2026-07-27T21:05:00+00:00")
    snapshot["instrument_symbol"] = "WRONG_SYMBOL"
    contract = _swap_contract(snapshot)

    assert broker_money.validate_contract_metadata(contract) == [
        "swap_snapshot_0_instrument_symbol_mismatch"
    ]


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
    assert winner["formula"] == {
        "directional_delta": 0.07,
        "contract_size": 100.0,
        "volume": 0.01,
        "orientation": "account_base_profit_quote",
        "rounding": "ROUND_HALF_UP",
        "currency_digits": 2,
    }

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


def test_bracketed_quote_interval_uses_only_the_last_causal_price():
    frame = pd.DataFrame([
        {
            "time_utc": "2026-07-09T14:54:50.000+00:00",
            "bid": 1.14320,
            "ask": 1.14335,
        },
        {
            "time_utc": "2026-07-09T14:55:00.250+00:00",
            "bid": 1.15000,
            "ask": 1.15020,
        },
    ])
    frame["time_utc"] = pd.to_datetime(frame["time_utc"], utc=True)
    converter = _converter(frame)

    result = converter.convert_leg(
        direction="BUY",
        open_price=4120.0,
        close_price=4121.0,
        volume=0.01,
        open_time_utc="2026-07-09T14:20:00+00:00",
        close_time_utc="2026-07-09T14:55:00.000+00:00",
    )

    assert result["status"] == "verified"
    assert result["conversion"]["price"] == 1.14335
    assert result["conversion"]["time_utc"] == (
        "2026-07-09T14:54:50+00:00"
    )
    assert result["conversion"]["freshness"] == "bracketed_tick_interval"
    assert result["conversion"]["quote_interval_ms"] == 10250
    assert result["conversion"]["next_quote_utc"] == (
        "2026-07-09T14:55:00.250000+00:00"
    )


def test_bracketed_quote_interval_normalizes_unsorted_non_numeric_index():
    frame = pd.DataFrame(
        [
            {
                "time_utc": "2026-07-09T14:55:00.250+00:00",
                "bid": 1.15000,
                "ask": 1.15020,
            },
            {
                "time_utc": "2026-07-09T14:54:50.000+00:00",
                "bid": 1.14320,
                "ask": 1.14335,
            },
        ],
        index=["future", "causal"],
    )
    frame["time_utc"] = pd.to_datetime(frame["time_utc"], utc=True)
    converter = _converter(frame)

    result = converter.convert_leg(
        direction="BUY",
        open_price=4120.0,
        close_price=4121.0,
        volume=0.01,
        open_time_utc="2026-07-09T14:20:00+00:00",
        close_time_utc="2026-07-09T14:55:00.000+00:00",
    )

    assert result["status"] == "verified"
    assert result["conversion"]["price"] == 1.14335
    assert result["conversion"]["freshness"] == "bracketed_tick_interval"


def test_bracketed_quote_interval_rejects_a_feed_gap_beyond_contract():
    frame = pd.DataFrame([
        {
            "time_utc": "2026-07-09T14:53:00.000+00:00",
            "bid": 1.14320,
            "ask": 1.14335,
        },
        {
            "time_utc": "2026-07-09T14:55:00.250+00:00",
            "bid": 1.15000,
            "ask": 1.15020,
        },
    ])
    frame["time_utc"] = pd.to_datetime(frame["time_utc"], utc=True)
    converter = _converter(frame)

    result = converter.convert_leg(
        direction="BUY",
        open_price=4120.0,
        close_price=4121.0,
        volume=0.01,
        open_time_utc="2026-07-09T14:20:00+00:00",
        close_time_utc="2026-07-09T14:55:00.000+00:00",
    )

    assert result["status"] == "blocked"
    assert result["blockers"] == ["stale_conversion_quote:EURUSD"]


def test_exact_zero_profit_does_not_require_a_conversion_quote():
    converter = _converter(_quotes().iloc[:0].copy())

    result = converter.convert_leg(
        direction="BUY",
        open_price=4120.0,
        close_price=4120.0,
        volume=0.01,
        open_time_utc="2026-07-09T14:20:00+00:00",
        close_time_utc="2026-07-09T14:55:00+00:00",
    )

    assert result["status"] == "verified"
    assert result["strategy_pnl"] == 0.0
    assert result["profit_currency_pnl"] == 0.0
    assert result["conversion"]["side"] == "not_required_zero"
    assert result["conversion"]["freshness"] == "not_required_zero"
    assert result["formula"] == {
        "directional_delta": 0.0,
        "contract_size": 100.0,
        "volume": 0.01,
    }
    assert result["blockers"] == []


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


def test_actual_validation_does_not_double_count_a_modeled_swap():
    quotes = pd.DataFrame([{
        "time_utc": "2026-07-27T20:59:59.900+00:00",
        "bid": 1.1399,
        "ask": 1.14,
    }])
    quotes["time_utc"] = pd.to_datetime(quotes["time_utc"], utc=True)
    converter = _swap_converter(
        _swap_snapshot("2026-07-27T20:55:00+00:00"),
        _swap_snapshot("2026-07-27T21:05:00+00:00"),
        frame=quotes,
    )
    trade = {
        "sig_id": "canal2_99999",
        "direction": "SELL",
        "tickets": [{
            "ticket": 99999,
            "open_dt_utc": "2026-07-27T20:30:00+00:00",
            "open_price": 4100.0,
            "close_dt_utc": "2026-07-27T21:30:00+00:00",
            "close_price": 4100.0,
            "volume": 0.01,
            "is_closed": True,
            "pnl_net": 0.24,
            "pnl_components": {
                "profit": 0.0,
                "commission": 0.0,
                "swap": 0.24,
                "fee": 0.0,
                "net": 0.24,
            },
        }],
    }

    validation = broker_money.validate_executed_money_contract(
        [trade],
        converter,
    )

    assert validation["verified"] is True
    assert validation["exact_tickets"] == 1
    assert validation["rows"][0]["expected_swap"] == 0.24
    assert validation["rows"][0]["observed_swap"] == 0.24

