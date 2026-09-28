"""Test-only framed runtime transport; not a process-isolation test.

The mailbox owns synchronization with the replay owner. Its blocking call()
must run only on the client's transport thread; abort() must be thread-safe,
idempotent, and release both queued and already accepted calls.
"""

from __future__ import annotations

import os
import threading

from mt5_client import MT5ReadClient
from mt5_protocol import BrokerRequest
from mt5_read_protocol import (
    MAX_REQUEST_BYTES,
    ReadOperation,
    ReadRequest,
    decode_message,
    encode_message,
)
from mt5_trade_worker import TradeWorker
from mt5_worker import ReadWorker
from tests.mt5_read_fakes import FakeTradeMT5


MAX_MESSAGES = 2000


class _MailboxBackend(FakeTradeMT5):
    def __init__(self, mailbox):
        super().__init__()
        self.mailbox = mailbox

    def _model_call(self, name, *args, **kwargs):
        self._call(name, *args, **kwargs)
        return self.mailbox.call(name, *args, **kwargs)

    def positions_get(self, **kwargs):
        return self._model_call("positions_get", **kwargs)

    def orders_get(self, **kwargs):
        return self._model_call("orders_get", **kwargs)

    def symbol_info_tick(self, symbol):
        return self._model_call("symbol_info_tick", symbol)

    def symbol_info(self, symbol):
        return self._model_call("symbol_info", symbol)

    def order_send(self, request):
        return self._model_call("order_send", request)

    def history_deals_get(self, *args, **kwargs):
        return self._model_call("history_deals_get", *args, **kwargs)

    def history_orders_get(self, *args, **kwargs):
        return self._model_call("history_orders_get", *args, **kwargs)

    def order_calc_profit(self, *args):
        return self._model_call("order_calc_profit", *args)

    def order_calc_margin(self, *args):
        return self._model_call("order_calc_margin", *args)

    def copy_rates_from_pos(self, *args):
        return self._model_call("copy_rates_from_pos", *args)

    def copy_ticks_from(self, *args):
        return self._model_call("copy_ticks_from", *args)

    def copy_ticks_range(self, *args):
        return self._model_call("copy_ticks_range", *args)


class _InProcessWorker:
    def __init__(self, mailbox):
        self.pid = os.getpid()
        self._mailbox = mailbox
        self._dead = threading.Event()

    def is_alive(self):
        return not self._dead.is_set()

    def terminate(self):
        self._dead.set()
        self._mailbox.abort()

    def kill(self):
        self.terminate()

    def join(self, timeout=None):
        pass


class _InProcessConnection:
    def __init__(self, reader, process, mailbox, messages):
        self._reader = reader
        self._trader = TradeWorker(reader)
        self._process = process
        self._mailbox = mailbox
        self.messages = messages
        self._lock = threading.Lock()
        self._closed = False
        self._awaiting = False
        self._response = None
        self._shutdown = False
        self._entry_responses = getattr(mailbox.owner.runtime_identity, "entry_owner", "model") == "external_runtime"
        self._trade_response = None

    def send_bytes(self, payload):
        # Do not hold this lock across a mailbox call: close must unblock it.
        with self._lock:
            if self._closed or not self._process.is_alive():
                raise EOFError("in-process worker closed")
            if self._awaiting:
                raise ValueError("previous response not consumed")
            self._awaiting = True
        try:
            envelope = decode_message(payload, MAX_REQUEST_BYTES)
            if envelope.get("session_id") != self._reader.session_id:
                raise ValueError("invalid worker envelope")
            if len(self.messages) >= MAX_MESSAGES:
                raise ValueError("in-process envelope budget exhausted")
            self.messages.append(envelope)
            phase = envelope.get("phase")
            shutdown = False
            if phase is None:
                if set(envelope) != {"request", "deadline", "session_id"}:
                    raise ValueError("invalid worker envelope")
                request = ReadRequest.from_dict(envelope["request"])
                response = self._reader.handle(request, envelope["deadline"])
                shutdown = request.operation is ReadOperation.SHUTDOWN
            elif phase == "prepare":
                if set(envelope) != {"phase", "request", "deadline", "session_id"}:
                    raise ValueError("invalid trade prepare envelope")
                response = self._trader.prepare(
                    BrokerRequest.from_dict(envelope["request"]), envelope["deadline"],
                )
                self._mailbox.call("prepared", response.to_dict())
            elif phase == "commit":
                if set(envelope) != {"phase", "request_id", "deadline", "session_id"}:
                    raise ValueError("invalid trade commit envelope")
                if self._entry_responses:
                    self._mailbox.call("commit_started", envelope["request_id"])
                response = self._trader.commit(envelope["request_id"], envelope["deadline"])
            elif phase == "abort":
                if set(envelope) != {"phase", "request_id", "session_id"}:
                    raise ValueError("invalid trade abort envelope")
                response = {"aborted": self._trader.abort(envelope["request_id"])}
            else:
                raise ValueError("invalid trade phase")
            value = response.to_dict() if hasattr(response, "to_dict") else response
            raw = encode_message(value, self._reader.config.max_response_bytes)
            with self._lock:
                if self._closed or not self._process.is_alive():
                    raise EOFError("in-process worker closed during request")
                self._response = raw
                self._trade_response = value if phase == "commit" and self._entry_responses else None
                self._shutdown = shutdown
        except Exception:
            self.close()
            raise

    def recv_bytes(self, maxlength):
        with self._lock:
            if self._closed or not self._process.is_alive():
                raise EOFError("in-process worker closed")
            if self._response is None:
                raise ValueError("no in-process response available")
            raw = self._response
            self._response = None
            self._awaiting = False
            shutdown = self._shutdown
            trade_response = self._trade_response
            self._trade_response = None
        if len(raw) > maxlength:
            self.close()
            raise OSError("message_size_limit")
        if trade_response is not None:
            self._mailbox.call("trade_response", trade_response)
        if shutdown:
            self.close()
        return raw

    def close(self):
        with self._lock:
            self._closed = True
            self._response = None
        self._process.terminate()


class InProcessMT5Client(MT5ReadClient):
    """Real client execution/validation with a test-only in-process owner.

    messages contains at most MAX_MESSAGES decoded inbound envelopes. The
    backend always uses FakeMT5's demo/7 identity, never native MT5. A config
    with another identity fails the real account validation.
    """

    def __init__(self, config, *, mailbox, store_path):
        if config.owner_lock_path is not None:
            raise ValueError("in-process replay must not acquire a runtime owner lock")
        self.mailbox = mailbox
        self.messages = []
        super().__init__(config, backend_factory=FakeTradeMT5, store_path=store_path)

    def _spawn(self):
        process = _InProcessWorker(self.mailbox)
        reader = ReadWorker(_MailboxBackend(self.mailbox), self.config, self.session_id)
        connection = _InProcessConnection(reader, process, self.mailbox, self.messages)
        with self._ownership_lock:
            self._process = process
            self._connection = connection
        if self._stop.is_set():
            connection.close()
