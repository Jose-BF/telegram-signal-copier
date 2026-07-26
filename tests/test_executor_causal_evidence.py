from types import SimpleNamespace

import pytest

import causal_trace
import executor


def _trace(sig_id="canal2_380"):
    return {
        "sig_id": sig_id,
        "action_id": "action_fixed",
        "attempt_id": "attempt_fixed",
        "decision_id": "decision_fixed",
        "message_revision_id": "msgrev_fixed",
        "action_revision": 0,
    }


def _trade_result(**overrides):
    values = {
        "retcode": executor.mt5.TRADE_RETCODE_DONE,
        "deal": 7001,
        "order": 8001,
        "volume": 0.01,
        "price": 4056.53,
        "bid": 4056.49,
        "ask": 4056.53,
        "comment": "Request executed",
        "request_id": 91,
        "retcode_external": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _position(ticket=8001, *, position_type=None):
    return SimpleNamespace(
        ticket=ticket,
        symbol="XAUUSD",
        magic=20260422,
        type=(
            executor.mt5.ORDER_TYPE_BUY
            if position_type is None
            else position_type
        ),
        volume=0.01,
        price_open=4056.53,
        price_current=4057.10,
        sl=4047.53,
        tp=4059.53,
        profit=0.57,
        comment="c2_380",
    )


def _tick():
    return SimpleNamespace(
        time=1784820626,
        time_msc=1784820626390,
        bid=4056.49,
        ask=4056.53,
        last=4056.51,
        volume=12,
        flags=6,
        volume_real=12.0,
    )


def _symbol_info():
    return SimpleNamespace(
        point=0.01,
        digits=2,
        trade_stops_level=20,
        trade_freeze_level=10,
    )


def test_market_open_records_one_correlated_attempt_without_extra_ipc(
        monkeypatch):
    events = []
    counts = {
        "tick": 0,
        "positions": 0,
        "order_send": 0,
        "symbol_info": 0,
    }
    tick = _tick()
    result = _trade_result()

    def symbol_info_tick(symbol):
        counts["tick"] += 1
        return tick

    def positions_get(ticket=None):
        counts["positions"] += 1
        return [_position(ticket or 8001)]

    def order_send(request):
        counts["order_send"] += 1
        return result

    def symbol_info(symbol):
        counts["symbol_info"] += 1
        return _symbol_info()

    monkeypatch.setattr(executor.mt5, "symbol_info_tick", symbol_info_tick)
    monkeypatch.setattr(executor.mt5, "positions_get", positions_get)
    monkeypatch.setattr(executor.mt5, "order_send", order_send)
    monkeypatch.setattr(executor.mt5, "symbol_info", symbol_info)
    monkeypatch.setattr(
        executor,
        "_emit_event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )

    with causal_trace.bind_message_revision(
        "msgrev_fixed",
        decision_id="decision_fixed",
    ):
        opened = executor.open_market_with_fill(
            "BUY",
            0.01,
            sl=None,
            tp=4059.53,
            comment="c2_380",
            magic=20260422,
        )

    assert opened == (8001, 4056.53)
    assert counts == {
        "tick": 1,
        "positions": 1,
        "order_send": 1,
        "symbol_info": 0,
    }
    attempt = next(fields for _, ev, fields in events
                   if ev == "mt5_action_attempt")
    requested = next(fields for _, ev, fields in events
                     if ev == "mt5_order_requested")
    response = next(fields for _, ev, fields in events
                    if ev == "mt5_order_result")

    assert attempt["operation"] == "OPEN_MARKET"
    assert attempt["broker_request_sent"] is True
    assert attempt["source_tick"]["time_msc"] == tick.time_msc
    assert attempt["source_tick"]["bid"] == tick.bid
    assert attempt["source_tick"]["ask"] == tick.ask
    assert attempt["position_before"] is None
    assert attempt["symbol_contract"] is None
    assert attempt["result"]["retcode_external"] == 0
    assert requested["action_id"] == response["action_id"]
    assert requested["attempt_id"] == response["attempt_id"]
    assert requested["message_revision_id"] == "msgrev_fixed"


def test_modify_records_reused_position_tick_and_contract(
        monkeypatch):
    events = []
    counts = {
        "positions": 0,
        "tick": 0,
        "symbol_info": 0,
        "order_send": 0,
        "orders": 0,
    }
    position = _position(ticket=101)
    tick = _tick()
    symbol = _symbol_info()

    def positions_get(ticket):
        counts["positions"] += 1
        return [position]

    def symbol_info_tick(name):
        counts["tick"] += 1
        return tick

    def symbol_info(name):
        counts["symbol_info"] += 1
        return symbol

    def order_send(request):
        counts["order_send"] += 1
        return _trade_result(order=101)

    def orders_get(ticket):
        counts["orders"] += 1
        return []

    monkeypatch.setattr(executor.mt5, "positions_get", positions_get)
    monkeypatch.setattr(executor.mt5, "symbol_info_tick", symbol_info_tick)
    monkeypatch.setattr(executor.mt5, "symbol_info", symbol_info)
    monkeypatch.setattr(executor.mt5, "order_send", order_send)
    monkeypatch.setattr(executor.mt5, "orders_get", orders_get)
    monkeypatch.setattr(
        executor,
        "_emit_event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )

    retcode = executor.modify_sltp_rc(
        101,
        new_sl=4055.0,
        new_tp=4060.0,
        expected_magic=20260422,
        trace=_trace(),
    )

    assert retcode == executor.mt5.TRADE_RETCODE_DONE
    assert counts == {
        "positions": 1,
        "tick": 1,
        "symbol_info": 1,
        "order_send": 1,
        "orders": 0,
    }
    attempt = next(fields for _, ev, fields in events
                   if ev == "mt5_action_attempt")
    assert attempt["action_id"] == "action_fixed"
    assert attempt["attempt_id"] == "attempt_fixed"
    assert attempt["operation"] == "MODIFY_SLTP"
    assert attempt["request"]["position"] == 101
    assert attempt["position_before"]["ticket"] == 101
    assert attempt["position_before"]["sl"] == 4047.53
    assert attempt["source_tick"]["time_msc"] == tick.time_msc
    assert attempt["symbol_contract"] == {
        "point": 0.01,
        "digits": 2,
        "trade_stops_level": 20,
        "trade_freeze_level": 10,
    }


@pytest.mark.parametrize(
    ("operation", "invoke", "expected_calls"),
    [
        (
            "CLOSE_POSITION",
            lambda: executor.close_position_rc(
                101,
                expected_magic=20260422,
                trace=_trace(),
            ),
            {"positions": 1, "orders": 0, "tick": 1, "order_send": 1},
        ),
        (
            "CANCEL_PENDING",
            lambda: executor.cancel_pending_rc(
                101,
                expected_magic=20260422,
                trace=_trace(),
            ),
            {"positions": 0, "orders": 1, "tick": 0, "order_send": 1},
        ),
    ],
)
def test_close_and_cancel_record_attempt_without_extra_ipc(
        monkeypatch, operation, invoke, expected_calls):
    events = []
    counts = {"positions": 0, "orders": 0, "tick": 0, "order_send": 0}

    def positions_get(ticket):
        counts["positions"] += 1
        return [_position(
            ticket=ticket,
            position_type=executor.mt5.POSITION_TYPE_BUY,
        )]

    def orders_get(ticket):
        counts["orders"] += 1
        return [SimpleNamespace(ticket=ticket, magic=20260422)]

    def symbol_info_tick(symbol):
        counts["tick"] += 1
        return _tick()

    def order_send(request):
        counts["order_send"] += 1
        return _trade_result(order=101)

    monkeypatch.setattr(executor.mt5, "positions_get", positions_get)
    monkeypatch.setattr(executor.mt5, "orders_get", orders_get)
    monkeypatch.setattr(executor.mt5, "symbol_info_tick", symbol_info_tick)
    monkeypatch.setattr(executor.mt5, "order_send", order_send)
    monkeypatch.setattr(
        executor,
        "_emit_event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )

    assert invoke() == executor.mt5.TRADE_RETCODE_DONE

    assert counts == expected_calls
    attempt = next(fields for _, ev, fields in events
                   if ev == "mt5_action_attempt")
    assert attempt["operation"] == operation
    assert attempt["broker_request_sent"] is True


def test_validation_failure_records_no_broker_request(
        monkeypatch):
    events = []
    sent = []
    monkeypatch.setattr(
        executor.mt5,
        "positions_get",
        lambda ticket: [_position(ticket=ticket)],
    )
    monkeypatch.setattr(
        executor.mt5,
        "order_send",
        lambda request: sent.append(request),
    )
    monkeypatch.setattr(
        executor,
        "_emit_event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )
    monkeypatch.setattr(executor, "_emit_anomaly", lambda *args, **kwargs: None)

    retcode = executor.modify_sltp_rc(
        101,
        new_sl=4055.0,
        expected_magic=999,
        trace=_trace(),
    )

    assert retcode == executor.mt5.TRADE_RETCODE_INVALID
    assert sent == []
    attempt = next(fields for _, ev, fields in events
                   if ev == "mt5_action_attempt")
    assert attempt["broker_request_sent"] is False
    assert attempt["result"]["retcode"] == (
        executor.mt5.TRADE_RETCODE_INVALID
    )


def test_order_send_exception_is_recorded_and_re_raised(
        monkeypatch):
    events = []
    monkeypatch.setattr(
        executor.mt5,
        "positions_get",
        lambda ticket: [_position(ticket=ticket)],
    )
    monkeypatch.setattr(
        executor.mt5,
        "symbol_info_tick",
        lambda symbol: _tick(),
    )
    monkeypatch.setattr(
        executor.mt5,
        "symbol_info",
        lambda symbol: _symbol_info(),
    )

    def fail(request):
        raise TimeoutError("IPC timeout")

    monkeypatch.setattr(executor.mt5, "order_send", fail)
    monkeypatch.setattr(
        executor,
        "_emit_event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )

    with pytest.raises(TimeoutError, match="IPC timeout"):
        executor.modify_sltp_rc(
            101,
            new_sl=4055.0,
            expected_magic=20260422,
            trace=_trace(),
        )

    attempts = [fields for _, ev, fields in events
                if ev == "mt5_action_attempt"]
    assert len(attempts) == 1
    assert attempts[0]["broker_request_sent"] is True
    assert attempts[0]["exception"]["type"] == "TimeoutError"
