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
    assert attempt["source_tick_lookup_state"] == "found"
    assert attempt["validation_tick_lookup_state"] == "not_queried"
    assert attempt["position_lookup_state"] == "not_queried"
    assert attempt["order_lookup_state"] == "not_queried"
    assert attempt["symbol_info_lookup_state"] == "not_queried"
    assert attempt["position_before"] is None
    assert attempt["symbol_contract"] is None
    assert attempt["result"]["retcode_external"] == 0
    assert attempt["request"] == {
        "action": executor.mt5.TRADE_ACTION_DEAL,
        "symbol": executor.config.MT5_SYMBOL,
        "volume": 0.01,
        "type": executor.mt5.ORDER_TYPE_BUY,
        "price": 4056.53,
        "deviation": 30,
        "magic": 20260422,
        "comment": "c2_380",
        "type_time": executor.mt5.ORDER_TIME_GTC,
        "type_filling": executor.mt5.ORDER_FILLING_IOC,
        "tp": 4059.53,
    }
    assert requested["action_id"] == response["action_id"]
    assert requested["attempt_id"] == response["attempt_id"]
    assert requested["message_revision_id"] == "msgrev_fixed"


def test_market_open_installs_money_valued_sl_in_the_initial_request(
        monkeypatch):
    requests = []
    events = []

    monkeypatch.setattr(
        executor.mt5,
        "symbol_info_tick",
        lambda _symbol: _tick(),
    )
    monkeypatch.setattr(
        executor.mt5,
        "symbol_info",
        lambda _symbol: _symbol_info(),
    )
    monkeypatch.setattr(
        executor.mt5,
        "order_calc_profit",
        lambda order_type, _symbol, volume, entry, exit_price: (
            (1.0 if order_type == executor.mt5.ORDER_TYPE_BUY else -1.0)
            * (exit_price - entry)
            * volume
            * 100.0
            * 0.9
        ),
    )
    monkeypatch.setattr(
        executor.mt5,
        "order_send",
        lambda request: requests.append(dict(request)) or _trade_result(),
    )
    monkeypatch.setattr(
        executor.mt5,
        "positions_get",
        lambda ticket=None: [_position(ticket or 8001)],
    )
    monkeypatch.setattr(
        executor,
        "_emit_event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )

    opened = executor.open_market_with_fill(
        "BUY",
        0.01,
        comment="c1_21754",
        magic=20260421,
        loss_budget=25.0,
    )

    assert opened == (8001, 4056.53)
    assert len(requests) == 1
    assert requests[0]["sl"] == 4028.76
    requested = next(
        fields for _, event, fields in events
        if event == "mt5_order_requested"
    )
    assert requested["requested_loss_budget"] == 25.0
    assert requested["sl"] == 4028.76


def test_market_open_with_unparseable_comment_is_still_observable(
        monkeypatch):
    events = []
    monkeypatch.setattr(
        executor.mt5,
        "symbol_info_tick",
        lambda symbol: _tick(),
    )
    monkeypatch.setattr(
        executor.mt5,
        "order_send",
        lambda request: _trade_result(),
    )
    monkeypatch.setattr(
        executor.mt5,
        "positions_get",
        lambda ticket=None: [_position(ticket or 8001)],
    )
    monkeypatch.setattr(
        executor,
        "_emit_event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )

    opened = executor.open_market_with_fill(
        "BUY",
        0.01,
        comment="",
    )

    assert opened == (8001, 4056.53)
    assert any(ev == "mt5_order_requested" for _, ev, _ in events)
    assert any(ev == "mt5_action_attempt" for _, ev, _ in events)
    assert {sig for sig, _, _ in events} == {"bot"}


def test_market_open_reuses_existing_sl_validation_reads_as_evidence(
        monkeypatch):
    events = []
    counts = {
        "tick": 0,
        "symbol_info": 0,
        "positions": 0,
        "order_send": 0,
    }
    price_tick = _tick()
    validation_tick = SimpleNamespace(
        **{
            **vars(_tick()),
            "time_msc": _tick().time_msc + 1,
            "bid": 4056.40,
            "ask": 4056.44,
        }
    )
    ticks = iter((price_tick, validation_tick))

    def symbol_info_tick(symbol):
        counts["tick"] += 1
        return next(ticks)

    def symbol_info(symbol):
        counts["symbol_info"] += 1
        return _symbol_info()

    def order_send(request):
        counts["order_send"] += 1
        return _trade_result()

    def positions_get(ticket=None):
        counts["positions"] += 1
        return [_position(ticket or 8001)]

    monkeypatch.setattr(executor.mt5, "symbol_info_tick", symbol_info_tick)
    monkeypatch.setattr(executor.mt5, "symbol_info", symbol_info)
    monkeypatch.setattr(executor.mt5, "order_send", order_send)
    monkeypatch.setattr(executor.mt5, "positions_get", positions_get)
    monkeypatch.setattr(
        executor,
        "_emit_event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )

    opened = executor.open_market_with_fill(
        "BUY",
        0.01,
        sl=4056.30,
        tp=4059.53,
        comment="c2_380",
        magic=20260422,
    )

    assert opened == (8001, 4056.53)
    assert counts == {
        "tick": 2,
        "symbol_info": 1,
        "positions": 1,
        "order_send": 1,
    }
    attempt = next(fields for _, ev, fields in events
                   if ev == "mt5_action_attempt")
    assert attempt["source_tick"]["time_msc"] == price_tick.time_msc
    assert attempt["validation_tick"]["time_msc"] == (
        validation_tick.time_msc
    )
    assert attempt["source_tick_lookup_state"] == "found"
    assert attempt["validation_tick_lookup_state"] == "found"
    assert attempt["symbol_info_lookup_state"] == "found"
    assert attempt["symbol_contract"] == {
        "point": 0.01,
        "digits": 2,
        "trade_stops_level": 20,
        "trade_freeze_level": 10,
    }
    assert attempt["request"]["sl"] == pytest.approx(4056.20)


def test_pending_limit_records_one_attempt_without_extra_mt5_reads(
        monkeypatch):
    events = []
    sent = []
    monkeypatch.setattr(
        executor.mt5,
        "order_send",
        lambda request: sent.append(dict(request)) or _trade_result(
            order=8100,
        ),
    )
    monkeypatch.setattr(
        executor,
        "_emit_event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )

    with causal_trace.bind_message_revision(
        "msgrev_limit",
        decision_id="decision_limit",
    ) as context:
        ticket = executor.place_limit(
            "BUY",
            4051.0,
            0.01,
            sl=4047.0,
            tp=4059.0,
            comment="DCA_c2_380_4051.0",
            magic=20260422,
        )
        declared = causal_trace.declared_action_ids(context)

    assert ticket == 8100
    assert len(sent) == 1
    attempt = next(
        fields for _, ev, fields in events
        if ev == "mt5_action_attempt"
    )
    requested = next(
        fields for _, ev, fields in events
        if ev == "mt5_order_requested"
    )
    result = next(
        fields for _, ev, fields in events
        if ev == "mt5_order_result"
    )
    assert attempt["operation"] == "PLACE_LIMIT"
    assert attempt["request"] == sent[0]
    assert attempt["broker_request_sent"] is True
    assert attempt["source_tick"] is None
    assert requested["action_id"] == attempt["action_id"]
    assert result["attempt_id"] == attempt["attempt_id"]
    assert declared == [attempt["action_id"]]


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
    assert attempt["request"] == {
        "action": executor.mt5.TRADE_ACTION_SLTP,
        "position": 101,
        "sl": 4055.0,
        "tp": 4060.0,
    }
    assert attempt["position_before"]["ticket"] == 101
    assert attempt["position_before"]["sl"] == 4047.53
    assert attempt["position_lookup_state"] == "found"
    assert attempt["order_lookup_state"] == "not_queried"
    assert attempt["source_tick_lookup_state"] == "found"
    assert attempt["symbol_info_lookup_state"] == "found"
    assert attempt["source_tick"]["time_msc"] == tick.time_msc
    assert attempt["symbol_contract"] == {
        "point": 0.01,
        "digits": 2,
        "trade_stops_level": 20,
        "trade_freeze_level": 10,
    }


def test_modify_revalidation_does_not_report_partial_request_as_full_success(
        monkeypatch):
    events = []
    sent = []
    position = _position(
        ticket=101,
        position_type=executor.mt5.ORDER_TYPE_SELL,
    )
    position.price_open = 4059.61
    position.price_current = 4059.98
    position.sl = 4060.95
    position.tp = 4055.0
    tick = _tick()
    tick.bid = 4059.98
    tick.ask = 4060.20

    monkeypatch.setattr(
        executor.mt5,
        "positions_get",
        lambda ticket: [position],
    )
    monkeypatch.setattr(
        executor.mt5,
        "symbol_info_tick",
        lambda symbol: tick,
    )
    monkeypatch.setattr(
        executor.mt5,
        "symbol_info",
        lambda symbol: SimpleNamespace(
            point=0.01,
            digits=2,
            trade_stops_level=0,
            trade_freeze_level=0,
        ),
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
    trace = _trace()
    trace.update({
        "expected_magic": 20260422,
        "preflight_status": "ready",
        "preflight_reason": None,
        "preflight_effective_sl": 4059.61,
        "preflight_effective_tp": 4052.0,
        "preflight_deferred_sl": None,
    })

    retcode = executor.modify_sltp_rc(
        101,
        new_sl=4059.61,
        new_tp=4052.0,
        expected_magic=20260422,
        trace=trace,
    )

    assert retcode == executor.mt5.TRADE_RETCODE_INVALID_STOPS
    assert sent == []
    attempt = next(fields for _, ev, fields in events
                   if ev == "mt5_action_attempt")
    assert attempt["broker_request_sent"] is False
    assert attempt["request"] is None
    assert attempt["result"]["retcode"] == (
        executor.mt5.TRADE_RETCODE_INVALID_STOPS
    )
    assert attempt["source_tick"]["time_msc"] == tick.time_msc
    assert attempt["position_before"]["sl"] == 4060.95


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


def test_market_tick_exception_is_recorded_and_re_raised(monkeypatch):
    events = []

    def fail_tick(symbol):
        raise TimeoutError("tick IPC timeout")

    monkeypatch.setattr(executor.mt5, "symbol_info_tick", fail_tick)
    monkeypatch.setattr(
        executor,
        "_emit_event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )

    with pytest.raises(TimeoutError, match="tick IPC timeout"):
        executor.open_market_with_fill(
            "BUY",
            0.01,
            comment="c2_380",
        )

    requested = [
        fields for _, ev, fields in events
        if ev == "mt5_order_requested"
    ]
    attempts = [
        fields for _, ev, fields in events
        if ev == "mt5_action_attempt"
    ]
    assert len(requested) == 1
    assert requested[0]["preflight_status"] == "source_tick_error"
    assert len(attempts) == 1
    assert attempts[0]["broker_request_sent"] is False
    assert attempts[0]["exception"]["type"] == "TimeoutError"


def test_modify_validation_exception_is_recorded_and_re_raised(
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
    monkeypatch.setattr(
        executor,
        "evaluate_position_sltp",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ValueError("invalid position snapshot")
        ),
    )
    monkeypatch.setattr(
        executor,
        "_emit_event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )

    with pytest.raises(ValueError, match="invalid position snapshot"):
        executor.modify_sltp_rc(
            101,
            new_sl=4055.0,
            expected_magic=20260422,
            trace=_trace(),
        )

    attempts = [
        fields for _, ev, fields in events
        if ev == "mt5_action_attempt"
    ]
    assert len(attempts) == 1
    assert attempts[0]["broker_request_sent"] is False
    assert attempts[0]["exception"]["type"] == "ValueError"


def test_none_order_result_reads_mt5_last_error_once(monkeypatch):
    events = []
    last_error_calls = []
    monkeypatch.setattr(
        executor.mt5,
        "symbol_info_tick",
        lambda symbol: _tick(),
    )
    monkeypatch.setattr(executor.mt5, "order_send", lambda request: None)
    monkeypatch.setattr(
        executor.mt5,
        "last_error",
        lambda: last_error_calls.append(True) or (1, "ipc down"),
    )
    monkeypatch.setattr(
        executor,
        "_emit_event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )
    monkeypatch.setattr(executor, "_emit_anomaly", lambda *args, **kwargs: None)

    assert executor.open_market_with_fill(
        "BUY",
        0.01,
        comment="c2_380",
    ) is None

    assert len(last_error_calls) == 1
    attempt = next(
        fields for _, ev, fields in events
        if ev == "mt5_action_attempt"
    )
    assert attempt["last_error"] == "(1, 'ipc down')"


def test_modify_records_unavailable_position_and_order_queries(
        monkeypatch):
    events = []
    counts = {"positions": 0, "orders": 0}

    def positions_get(ticket):
        counts["positions"] += 1
        return None

    def orders_get(ticket):
        counts["orders"] += 1
        return None

    monkeypatch.setattr(executor.mt5, "positions_get", positions_get)
    monkeypatch.setattr(executor.mt5, "orders_get", orders_get)
    monkeypatch.setattr(
        executor,
        "_emit_event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )

    retcode = executor.modify_sltp_rc(
        101,
        new_sl=4055.0,
        expected_magic=20260422,
        trace=_trace(),
    )

    assert retcode == executor.mt5.TRADE_RETCODE_INVALID
    assert counts == {"positions": 1, "orders": 1}
    attempt = next(
        fields for _, ev, fields in events
        if ev == "mt5_action_attempt"
    )
    assert attempt["position_lookup_state"] == "unavailable"
    assert attempt["order_lookup_state"] == "unavailable"
    assert attempt["source_tick_lookup_state"] == "not_queried"


def test_market_tick_failure_keeps_action_root_and_lookup_evidence(
        monkeypatch):
    events = []
    counts = {"tick": 0, "select": 0, "send": 0}

    def symbol_info_tick(symbol):
        counts["tick"] += 1
        return None

    def symbol_select(symbol, selected):
        counts["select"] += 1
        return True

    monkeypatch.setattr(executor.mt5, "symbol_info_tick", symbol_info_tick)
    monkeypatch.setattr(executor.mt5, "symbol_select", symbol_select)
    monkeypatch.setattr(executor.mt5, "last_error", lambda: (1, "no tick"))
    monkeypatch.setattr(
        executor.mt5,
        "order_send",
        lambda request: counts.__setitem__("send", counts["send"] + 1),
    )
    monkeypatch.setattr(
        executor,
        "_emit_event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )

    with causal_trace.bind_message_revision(
        "msgrev_fixed",
        decision_id="decision_fixed",
    ) as context:
        opened = executor.open_market_with_fill(
            "BUY",
            0.01,
            sl=4047.0,
            tp=4059.0,
            comment="c2_380",
            magic=20260422,
        )
        declared = causal_trace.declared_action_ids(context)

    assert opened is None
    assert counts == {"tick": 2, "select": 1, "send": 0}
    attempt = next(
        fields for _, ev, fields in events
        if ev == "mt5_action_attempt"
    )
    requested = next(
        fields for _, ev, fields in events
        if ev == "mt5_order_requested"
    )
    assert declared == [attempt["action_id"]]
    assert requested["action_id"] == attempt["action_id"]
    assert requested["requested_price"] is None
    assert requested["preflight_status"] == "source_tick_unavailable"
    assert attempt["source_tick_lookup_state"] == "unavailable"
    assert attempt["broker_request_sent"] is False
    assert attempt["exception"]["type"] == "RuntimeError"
