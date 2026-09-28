from collections import namedtuple
import time

import pytest

from mt5_protocol import BrokerRequest, IntentKey, IntentState
from mt5_trade_protocol import TradePreparation, TradeResponse
from mt5_trade_worker import TradeWorker
from mt5_worker import ReadWorker, WorkerConfig
from tests.mt5_read_fakes import FakeMT5


TradeResult = namedtuple(
    "TradeResult",
    "retcode order deal volume price comment bid ask",
)
Position = namedtuple(
    "Position",
    "ticket symbol volume type magic price_open sl tp",
)


class FakeTradeMT5(FakeMT5):
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_PENDING = 5
    TRADE_ACTION_SLTP = 6
    TRADE_ACTION_MODIFY = 7
    TRADE_ACTION_REMOVE = 8
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TYPE_BUY_LIMIT = 2
    ORDER_TYPE_SELL_LIMIT = 3
    POSITION_TYPE_BUY = 0
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1

    def __init__(self, *, trade_result=None, **kwargs):
        super().__init__(**kwargs)
        self.trade_result = trade_result or TradeResult(
            10009, 701, 702, 0.01, 2500.25, "done", 2500.0, 2500.2
        )
        self.positions = ()
        self.orders = ()

    def symbol_info(self, symbol):
        self._call("symbol_info", symbol)
        return {
            "name": symbol,
            "point": 0.01,
            "digits": 2,
            "trade_stops_level": 10,
        }

    def positions_get(self, **kwargs):
        self._call("positions_get", **kwargs)
        return self.positions

    def orders_get(self, **kwargs):
        self._call("orders_get", **kwargs)
        return self.orders

    def order_send(self, request):
        self._call("order_send", request)
        if self.mode == "none":
            self.error = (-10004, "No connection")
            return None
        return self.trade_result


def request(operation="OPEN_MARKET", **overrides):
    payload = {"symbol": "XAUUSD"}
    if operation == "OPEN_MARKET":
        payload.update({
            "direction": "BUY",
            "volume": 0.01,
            "sl": 2499.0,
            "tp": 2502.0,
            "loss_budget": None,
            "protection_policy": "required",
            "magic": 111,
            "comment": "c1_3086_1",
            "deviation": 30,
        })
    payload.update(overrides)
    key = IntentKey("demo/7", "canal1", "canal1_3086", 1, "entry-1", operation, 0)
    return BrokerRequest.create(
        key,
        payload,
        request_id="request-1",
        attempt_id="attempt-1",
        action_id="action-1",
    )


def worker(backend=None):
    backend = backend or FakeTradeMT5()
    reader = ReadWorker(backend, WorkerConfig(7, "demo", ("XAUUSD",)), "session-1")
    assert reader.handle(reader_request("initialize"), time.monotonic() + 1).value["ready"]
    return TradeWorker(reader), backend


def reader_request(operation):
    from mt5_read_protocol import ReadOperation, ReadRequest

    return ReadRequest(ReadOperation(operation))


def test_market_prepare_persists_exact_native_request_before_commit():
    owner, backend = worker()
    req = request()

    prepared = owner.prepare(req, time.monotonic() + 1)

    assert isinstance(prepared, TradePreparation)
    assert prepared.error is None
    assert prepared.native_request == {
        "action": 1,
        "symbol": "XAUUSD",
        "volume": 0.01,
        "type": 0,
        "price": 2500.2,
        "deviation": 30,
        "magic": 111,
        "comment": "c1_3086_1",
        "type_time": 0,
        "type_filling": 1,
        "sl": 2499.0,
        "tp": 2502.0,
    }
    assert not any(call[0] == "order_send" for call in backend.calls)

    response = owner.commit(req.request_id, time.monotonic() + 1)

    assert isinstance(response, TradeResponse)
    assert response.outcome.state is IntentState.DONE
    assert response.outcome.order == 701
    assert [call[0] for call in backend.calls].count("order_send") == 1


def test_commit_preserves_placed_partial_and_missing_as_unresolved():
    cases = [
        (TradeResult(10008, 801, 0, 0.0, 0.0, "placed", 0.0, 0.0), IntentState.PLACED),
        (TradeResult(10010, 802, 803, 0.005, 2500.3, "partial", 0.0, 0.0), IntentState.DONE_PARTIAL),
        (None, IntentState.UNKNOWN),
    ]
    for result, expected in cases:
        backend = FakeTradeMT5(mode="none") if result is None else FakeTradeMT5(trade_result=result)
        owner, backend = worker(backend)
        req = request()
        assert owner.prepare(req, time.monotonic() + 1).error is None

        response = owner.commit(req.request_id, time.monotonic() + 1)

        assert response.outcome.state is expected
        assert [call[0] for call in backend.calls].count("order_send") == 1


def test_required_protection_failure_blocks_before_order_send():
    backend = FakeTradeMT5()
    backend.symbol_info_tick = lambda _symbol: None
    owner, backend = worker(backend)

    prepared = owner.prepare(request(), time.monotonic() + 1)

    assert prepared.error == "source_tick_unavailable"
    assert prepared.native_request is None
    with pytest.raises(KeyError):
        owner.commit("request-1", time.monotonic() + 1)
    assert not any(call[0] == "order_send" for call in backend.calls)


def test_account_fingerprint_mismatch_blocks_before_order_send():
    owner, backend = worker()
    req = request()
    req = BrokerRequest.create(
        IntentKey(
            "other/99",
            req.intent_key.channel,
            req.intent_key.signal_root,
            req.intent_key.generation,
            req.intent_key.leg,
            req.intent_key.operation,
            req.intent_key.revision,
        ),
        dict(req.payload),
        request_id=req.request_id,
        attempt_id=req.attempt_id,
        action_id=req.action_id,
    )

    prepared = owner.prepare(req, time.monotonic() + 1)

    assert prepared.error == "account_fingerprint_mismatch"
    assert not any(call[0] == "order_send" for call in backend.calls)


def test_account_change_after_prepare_becomes_unknown_without_sending():
    owner, backend = worker()
    req = request()
    assert owner.prepare(req, time.monotonic() + 1).error is None
    backend.login = 99

    response = owner.commit(req.request_id, time.monotonic() + 1)

    assert response.outcome.state is IntentState.UNKNOWN
    assert response.outcome.error == "account_mismatch"
    assert not any(call[0] == "order_send" for call in backend.calls)


def test_trade_recovers_after_same_account_terminal_reconnects():
    owner, backend = worker()
    backend.connected = False
    disconnected = owner.reader.handle(
        reader_request("terminal_info"), time.monotonic() + 1
    )
    assert disconnected.error == "terminal_disconnected"
    backend.connected = True

    prepared = owner.prepare(request(), time.monotonic() + 1)

    assert prepared.error is None


def test_read_only_conversion_symbol_cannot_receive_trade_effects():
    backend = FakeTradeMT5()
    reader = ReadWorker(
        backend,
        WorkerConfig(
            7,
            "demo",
            ("XAUUSD", "EURUSD"),
            trade_symbols=("XAUUSD",),
        ),
        "session-1",
    )
    assert reader.handle(
        reader_request("initialize"), time.monotonic() + 1
    ).state.value == "FOUND"
    owner = TradeWorker(reader)

    prepared = owner.prepare(
        request(symbol="EURUSD"),
        time.monotonic() + 1,
    )

    assert prepared.error == "symbol_not_allowed"
    assert not any(call[0] == "order_send" for call in backend.calls)


@pytest.mark.parametrize("operation", ["MODIFY_SLTP", "CLOSE_POSITION", "CANCEL_PENDING"])
def test_management_revalidates_ticket_symbol_and_magic(operation):
    backend = FakeTradeMT5()
    backend.positions = (
        Position(42, "XAUUSD", 0.01, 0, 999, 2500.0, 2490.0, 2510.0),
    )
    backend.orders = backend.positions
    owner, backend = worker(backend)
    payload = {
        "symbol": "XAUUSD",
        "ticket": 42,
        "expected_magic": 111,
    }
    if operation == "MODIFY_SLTP":
        payload.update(new_sl=2495.0, new_tp=None)
    elif operation == "CLOSE_POSITION":
        payload.update(deviation=30)

    prepared = owner.prepare(request(operation, **payload), time.monotonic() + 1)

    assert prepared.error == "magic_mismatch"
    assert prepared.native_request is None
    assert not any(call[0] == "order_send" for call in backend.calls)


def test_expired_commit_is_unknown_and_never_sends():
    owner, backend = worker()
    req = request()
    assert owner.prepare(req, time.monotonic() + 1).error is None

    response = owner.commit(req.request_id, time.monotonic() - 0.001)

    assert response.outcome.state is IntentState.UNKNOWN
    assert response.outcome.error == "deadline_before_order_send"
    assert not any(call[0] == "order_send" for call in backend.calls)


def test_abort_discards_prepared_trade_without_sending():
    owner, backend = worker()
    req = request()
    assert owner.prepare(req, time.monotonic() + 1).error is None

    assert owner.abort(req.request_id) is True
    assert owner.abort(req.request_id) is False
    with pytest.raises(KeyError):
        owner.commit(req.request_id, time.monotonic() + 1)
    assert not any(call[0] == "order_send" for call in backend.calls)


def test_limit_modify_close_and_cancel_build_exact_requests_inside_owner():
    position = Position(42, "XAUUSD", 0.01, 0, 111, 2500.0, 2480.0, 2520.0)
    cases = [
        (
            "PLACE_LIMIT",
            {
                "direction": "BUY",
                "volume": 0.02,
                "price": 2490.0,
                "sl": 2480.0,
                "tp": 2520.0,
                "magic": 111,
                "comment": "c1_limit",
                "deviation": 30,
            },
            {"action": 5, "type": 2, "price": 2490.0},
        ),
        (
            "MODIFY_SLTP",
            {"ticket": 42, "expected_magic": 111, "new_sl": 2490.0, "new_tp": 2525.0},
            {"action": 6, "position": 42, "sl": 2490.0, "tp": 2525.0},
        ),
        (
            "CLOSE_POSITION",
            {"ticket": 42, "expected_magic": 111, "deviation": 30},
            {"action": 1, "position": 42, "type": 1, "price": 2500.0},
        ),
        (
            "CANCEL_PENDING",
            {"ticket": 42, "expected_magic": 111},
            {"action": 8, "order": 42},
        ),
    ]
    for operation, payload, expected in cases:
        backend = FakeTradeMT5()
        backend.positions = (position,)
        backend.orders = (position,)
        owner, backend = worker(backend)
        req = request(operation, **payload)

        prepared = owner.prepare(req, time.monotonic() + 1)

        assert prepared.error is None
        for key, value in expected.items():
            assert prepared.native_request[key] == value
        assert not any(call[0] == "order_send" for call in backend.calls)
        assert owner.commit(req.request_id, time.monotonic() + 1).outcome.state is IntentState.DONE


def test_modify_stop_invalid_at_current_market_never_sends():
    backend = FakeTradeMT5()
    backend.positions = (
        Position(42, "XAUUSD", 0.01, 0, 111, 2500.0, 2480.0, 2520.0),
    )
    owner, backend = worker(backend)

    prepared = owner.prepare(
        request(
            "MODIFY_SLTP",
            ticket=42,
            expected_magic=111,
            new_sl=2500.0,
            new_tp=None,
        ),
        time.monotonic() + 1,
    )

    assert prepared.error == "requested_sl_waits_for_market"
    assert not any(call[0] == "order_send" for call in backend.calls)
