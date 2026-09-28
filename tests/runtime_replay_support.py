"""One-owner replay book and bounded mailbox for runtime validation only."""

import asyncio
from concurrent.futures import Future
from copy import deepcopy
from dataclasses import asdict, replace
import threading

from research.dubai_iterative.client import ClientBook
from research.dubai_iterative.engine import _ReplayBoundary, _ReplayRepeat, _simulation_steps
from research.dubai_iterative.protection import ProtectionBlocked
from research.dubai_iterative.runtime_control import (
    NativeCloseCommand, NativeCloseResponse, NativeEntryCommand, NativeEntryResponse,
    NativeEntryReconciliation, NativeProtectionEffect,
)
from research.dubai_iterative.shared_replay import SharedReplayProfile
from research.management_observation import ReadCycleProfile


class ReplayOwner:
    """Only this event-loop thread may advance quotes or mutate the broker book."""

    def __init__(self, spec, identity):
        self.thread = threading.get_ident()
        self.spec = replace(spec, management_reads=ReadCycleProfile())
        self.runtime_identity = identity
        self.external_entry_open = identity.entry_owner == "external_runtime"
        self.entry_receipts = []
        self.close_receipts = []
        self.profile = SharedReplayProfile(account_currency="EUR", name="shared_interquote_reads_v2",
                                           read_symbol=identity.symbol)
        self.interquote = True
        self.monitor_busy = False
        self.broker = None
        self.risk_count = 0
        self.risk = []
        self.passive_events = []
        self.result = None
        self.index = -1
        self.stream = _simulation_steps(self.spec.path, self.spec.genome,
                                       execution=self.spec.execution, _transport=self)
        self.state = next(self.stream)

    def _assert_owner(self):
        if threading.get_ident() != self.thread:
            raise RuntimeError("replay state accessed outside its owner thread")

    def client_book(self, profile):
        return ClientBook(profile)

    def bind_broker(self, access):
        self.broker = access

    def reserve_risk(self, count=1):
        if type(count) is not int or count < 1:
            raise ValueError("positive risk reservation required")
        if self.risk_count + count > 10_000:
            raise ProtectionBlocked("runtime_control_risk_budget_exhausted")
        self.risk_count += count

    def passive(self, ticket, now, *, target):
        self.passive_events.append((ticket, now, target))

    def _resume(self, command=None):
        try:
            self.state = self.stream.send(command)
        except StopIteration as done:
            self.result = done.value
            self.state = None

    def advance(self):
        self._assert_owner()
        if self.state is None:
            return False
        if not isinstance(self.state, _ReplayBoundary):
            self._resume()
        if self.state is None:
            return False
        assert isinstance(self.state, _ReplayBoundary)
        self.index = self.state.tick_index
        self._resume()
        # Drain zero-delay model entry/ACK work without inventing a new quote.
        for _ in range(4):
            if self.state is None or not self.state.continuing:
                break
            self._record_risk()
            self._resume(_ReplayRepeat(self.index))
        if self.state is not None:
            self._record_risk()
        return True

    def _record_risk(self):
        self.risk.extend({**asdict(row), "tick_index": self.state.tick_index,
                          "time_ns": self.state.time_ns} for row in self.state.snapshots)

    def snapshot(self):
        self._assert_owner()
        return self.broker.runtime_control.snapshot()

    def apply(self, request, *, request_id=None):
        self._assert_owner()
        if request.get("action") == 1 and "position" in request:
            if request_id is None or request.get("type") not in (0, 1):
                raise ValueError("native close requires request identity and direction")
            snapshot = self.snapshot()
            command = NativeCloseCommand(request_id, request["position"], request["symbol"], request["magic"],
                "BUY" if request["type"] == 0 else "SELL", request["volume"], request["price"],
                snapshot["quote_index"], snapshot["time_ns"], request.get("comment", ""))
            receipt = self.broker.runtime_control.apply_close(command)
            self.close_receipts.append(receipt)
            self.risk.extend(self.broker.runtime_control.drain_risk())
            return receipt.native_result()
        if request.get("action") == 1 and "position" not in request:
            if request_id is None or request.get("type") not in (0, 1):
                raise ValueError("native entry requires request identity and direction")
            snapshot = self.snapshot()
            command = NativeEntryCommand(
                request_id, request["symbol"], request["magic"],
                "BUY" if request["type"] == 0 else "SELL", request["volume"],
                request["price"], request.get("sl", 0.), request.get("tp", 0.),
                snapshot["quote_index"], snapshot["time_ns"],
                comment=request.get("comment", ""),
            )
            receipt = self.broker.runtime_control.apply_entry(command)
            self.entry_receipts.append(receipt)
            self.risk.extend(self.broker.runtime_control.drain_risk())
            return receipt.native_result()
        if request.get("action") != 6:
            raise ValueError("runtime control permits position SLTP only")
        snapshot = self.snapshot()
        effect = NativeProtectionEffect(int(request["position"]), request["sl"], request["tp"],
                                        snapshot["quote_index"], snapshot["time_ns"])
        return self.broker.runtime_control.apply_protection(effect)

    def observe_entry_response(self, execution_id):
        self._assert_owner()
        snapshot = self.snapshot()
        return self.broker.runtime_control.observe_entry_response(NativeEntryResponse(
            execution_id, snapshot["quote_index"], snapshot["time_ns"],
        ))

    def observe_entry_reconciliation(self, request, outcome):
        self._assert_owner()
        snapshot = self.snapshot()
        payload = request.payload
        return self.broker.runtime_control.observe_entry_reconciliation(NativeEntryReconciliation(
            request.request_id, outcome["order"], outcome["deal"], payload["symbol"],
            payload["magic"], payload["direction"], payload["comment"],
            outcome["filled_volume"], outcome["price"],
            snapshot["quote_index"], snapshot["time_ns"],
        ))

    def observe_close_response(self, execution_id):
        self._assert_owner()
        snapshot = self.snapshot()
        return self.broker.runtime_control.observe_close_response(NativeCloseResponse(
            execution_id, snapshot["quote_index"], snapshot["time_ns"],
        ))

    def finish(self):
        self._assert_owner()
        self.monitor_busy = False
        self.external_entry_open = False
        while self.advance():
            pass
        return self.result

    def close(self):
        self._assert_owner()
        self.stream.close()


class RuntimeMailbox:
    """Marshal synchronous backend calls onto the replay owner's asyncio loop."""

    def __init__(self, owner, *, max_calls=2000):
        self.owner, self.max_calls = owner, max_calls
        self.loop = asyncio.get_running_loop()
        self.inbox = asyncio.Queue()
        self.applied = asyncio.Queue()
        self.prepared = asyncio.Queue()
        self.records = []
        self.pending = set()
        self.lock = threading.Lock()
        self.closed = False
        self.hold_responses = False
        self.hold_preparations = False
        self.unknown_after_effect = False
        self.unknown_reads = set()
        self.held_responses = []
        self.held_preparations = []
        self.committing_request_id = None
        self.task = asyncio.create_task(self._pump())

    def call(self, name, *args, **kwargs):
        if threading.get_ident() == self.owner.thread:
            raise RuntimeError("synchronous backend call would block replay owner")
        future = Future()
        call = (name, deepcopy(args), deepcopy(kwargs), future)
        try:
            with self.lock:
                if self.closed:
                    raise RuntimeError("runtime mailbox closed")
                self.pending.add(future)
                self.loop.call_soon_threadsafe(self.inbox.put_nowait, call)
            # Only the real client owns deadlines. Keep its transport occupied
            # until the owner replies or abort explicitly releases this call.
            return future.result()
        finally:
            with self.lock:
                self.pending.discard(future)

    def abort(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            for future in self.pending:
                if not future.done():
                    future.set_exception(RuntimeError("runtime mailbox aborted"))
            self.loop.call_soon_threadsafe(self.inbox.put_nowait, None)

    def _reply(self, future, value):
        value = deepcopy(value)
        with self.lock:
            if not future.done():
                future.set_result(value)

    def _fail(self, future, error):
        with self.lock:
            if not future.done():
                future.set_exception(error)

    def release_responses(self):
        self.owner._assert_owner()
        self.hold_responses = False
        for future, value in self.held_responses:
            self._reply(future, value)
        self.held_responses.clear()

    def release_preparations(self):
        self.owner._assert_owner()
        self.hold_preparations = False
        for future, value in self.held_preparations:
            self._reply(future, value)
        self.held_preparations.clear()

    async def _pump(self):
        while True:
            call = await self.inbox.get()
            if call is None:
                return
            name, args, kwargs, future = call
            if future.done():
                continue
            try:
                if self.closed or len(self.records) >= self.max_calls:
                    raise RuntimeError("runtime mailbox closed or budget exhausted")
                if name == "order_calc_profit":
                    self.owner._assert_owner()
                    index = self.owner.index
                    row = {"name": name, "args": args, "kwargs": kwargs,
                           "quote_index": index,
                           "time_ns": int(self.owner.spec.path.times_ns[index]) if index >= 0 else None,
                           "valuation_profile": self.owner.runtime_identity.valuation_profile,
                           "native_valuation_verified": False}
                    self.records.append(row)
                    access = getattr(getattr(self.owner, "broker", None), "runtime_control", None)
                    calc_profit = getattr(access, "calc_profit", None)
                    if calc_profit is None:
                        raise ValueError("runtime control requires an explicit valuation hypothesis")
                    self._reply(future, calc_profit(*args, **kwargs))
                    continue
                before = self.owner.snapshot()
                row = {"name": name, "args": args, "kwargs": kwargs,
                       "quote_index": before["quote_index"], "time_ns": before["time_ns"]}
                self.records.append(row)
                if name == "clock_ns":
                    value = before["time_ns"]
                elif name == "positions_get":
                    value = [p for p in before["positions"]
                             if ("ticket" not in kwargs or p["ticket"] == kwargs["ticket"])
                             and ("symbol" not in kwargs or p["symbol"] == kwargs["symbol"])]
                    if any(p.get("profit", 0.) is None for p in value):
                        value = None
                elif name == "orders_get":
                    value = []
                elif name in {"history_deals_get", "history_orders_get"}:
                    access = getattr(getattr(self.owner, "broker", None), "runtime_control", None)
                    history = getattr(access, "history", None)
                    history_range = getattr(access, "history_range", None)
                    if history is None:
                        raise ValueError("runtime control history is not implemented")
                    if name == "history_deals_get" and not args and set(kwargs) == {"position"}:
                        value = history(kwargs["position"])
                    elif len(args) == 2 and set(kwargs) <= {"group"} and history_range is not None:
                        value = history_range(
                            "deals" if name == "history_deals_get" else "orders",
                            args[0], args[1], kwargs.get("group"),
                        )
                    else:
                        raise ValueError("runtime control history scope is not implemented")
                elif name in {"symbol_info", "symbol_info_tick"}:
                    value = (before["symbol" if name == "symbol_info" else "tick"]
                             if args[0] == self.owner.runtime_identity.symbol else None)
                elif name == "prepared":
                    value = None
                    self.prepared.put_nowait(deepcopy(args[0]))
                    if self.hold_preparations:
                        self.held_preparations.append((future, value))
                        continue
                elif name == "commit_started":
                    self.committing_request_id = args[0]
                    value = None
                elif name == "trade_response":
                    response = args[0]
                    receipts = [(receipt, self.owner.observe_entry_response) for receipt in self.owner.entry_receipts
                                if receipt.request_id == response["request_id"]]
                    receipts.extend((receipt, self.owner.observe_close_response) for receipt in self.owner.close_receipts
                                    if receipt.request_id == response["request_id"])
                    value = None
                    if receipts and response["outcome"]["state"] in {"DONE", "REJECTED"}:
                        if len(receipts) != 1:
                            raise ValueError("ambiguous native execution response")
                        receipt, observe = receipts[0]
                        if response["outcome"]["retcode"] != receipt.retcode:
                            raise ValueError("native execution response mismatch")
                        value = observe(receipt.execution_id)
                    self.committing_request_id = None
                elif name == "order_send":
                    if args[0].get("action") == 1:
                        value = self.owner.apply(args[0], request_id=self.committing_request_id)
                    else:
                        value = self.owner.apply(args[0])
                    row["result"] = value
                    row["positions_after"] = self.owner.snapshot()["positions"]
                    self.applied.put_nowait(deepcopy(row))
                    if self.unknown_after_effect:
                        value = None
                    if self.hold_responses:
                        self.held_responses.append((future, value))
                        continue
                else:
                    raise ValueError("unsupported runtime mailbox operation")
                if name in self.unknown_reads and name in {
                    "positions_get", "history_deals_get", "history_orders_get", "symbol_info_tick"
                }:
                    row["injected_unknown_read"] = True
                    value = None
                self._reply(future, value)
            except Exception as exc:
                self._fail(future, exc)

    async def close(self):
        self.abort()
        await self.task
