"""Spawn-safe doubles. This module never imports the native MT5 extension."""

from collections import namedtuple
import ctypes
import os
import sys
import time

Account = namedtuple("Account", "login server currency")
Terminal = namedtuple("Terminal", "connected")
Symbol = namedtuple("Symbol", "name point")
Tick = namedtuple("Tick", "time_msc bid ask")
Position = namedtuple("Position", "ticket symbol volume")
TradeResult = namedtuple(
    "TradeResult",
    "retcode order deal volume price comment bid ask",
)


def hold_native_gil(seconds):
    if os.name == "nt":
        sleep = ctypes.PyDLL("kernel32").Sleep
        sleep.argtypes, sleep.restype = [ctypes.c_ulong], None
        sleep(round(seconds * 1000))
    else:
        sleep = ctypes.PyDLL(None).usleep
        sleep.argtypes, sleep.restype = [ctypes.c_uint], ctypes.c_int
        # usleep implementations may reject intervals >= one second.
        for _ in range(int(seconds * 10)):
            sleep(100_000)


class FakeMT5:
    def __init__(self, *, delay=0, native=False, mode="found"):
        self.delay, self.native, self.mode = delay, native, mode
        self.error = (1, "Success")
        self.calls = []
        self.login = 7
        self.connected = True

    def _call(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        self.error = (1, "Success")

    def last_error(self):
        self.calls.append(("last_error", (), {}))
        return self.error

    def initialize(self, *args, **kwargs):
        self._call("initialize", *args, **kwargs)
        return True

    def shutdown(self):
        self._call("shutdown")

    def account_info(self):
        self._call("account_info")
        return Account(self.login, "demo", "EUR")

    def terminal_info(self):
        self._call("terminal_info")
        return Terminal(self.connected)

    def symbol_select(self, symbol, enabled):
        self._call("symbol_select", symbol, enabled)
        return True

    def symbol_info(self, symbol):
        self._call("symbol_info", symbol)
        return Symbol(symbol, 0.01)

    def symbol_info_tick(self, symbol):
        self._call("symbol_info_tick", symbol)
        if self.native:
            hold_native_gil(self.delay)
        else:
            time.sleep(self.delay)
        if self.mode == "switch_account":
            self.login += 1
        if self.mode == "die":
            os._exit(23)
        return Tick(int(time.time() * 1000), 2500.0, 2500.2)

    def positions_get(self, **kwargs):
        self._call("positions_get", **kwargs)
        if self.mode == "empty":
            return ()
        if self.mode == "none":
            self.error = (-10004, "No connection")
            return None
        if self.mode == "error_empty":
            self.error = (-1, "Failure")
            return ()
        return (Position(42, "XAUUSD", 0.01),)

    def orders_get(self, **kwargs):
        self._call("orders_get", **kwargs)
        return ()

    def history_deals_get(self, *args, **kwargs):
        self._call("history_deals_get", *args, **kwargs)
        return ()

    def history_orders_get(self, *args, **kwargs):
        self._call("history_orders_get", *args, **kwargs)
        return ()

    def copy_rates_from_pos(self, *args):
        self._call("copy_rates_from_pos", *args)
        return []

    def copy_ticks_from(self, *args):
        self._call("copy_ticks_from", *args)
        return []

    def copy_ticks_range(self, *args):
        self._call("copy_ticks_range", *args)
        return []

    def order_calc_profit(self, *args):
        self._call("order_calc_profit", *args)
        return 0.0

    def order_calc_margin(self, *args):
        self._call("order_calc_margin", *args)
        return 12.5


class EntryProbeBackend(FakeMT5):
    """Reports whether the application entry script ran inside the worker."""

    def symbol_info_tick(self, symbol):
        self._call("symbol_info_tick", symbol)
        return {
            "application_entry_reran": "mt5_read_application_marker" in sys.modules,
            "pid": os.getpid(),
        }


class NoisyBackend(FakeMT5):
    def __init__(self, *, noise="print"):
        super().__init__()
        self.noise = noise

    def positions_get(self, **kwargs):
        if self.noise == "descriptor":
            os.write(1, b"backend descriptor output must not enter the JSON protocol\n")
        elif self.noise == "original_stdout":
            sys.__stdout__.write("backend original stdout must not enter the JSON protocol\n")
            sys.__stdout__.flush()
        else:
            print("backend output must not enter the JSON protocol")
        return super().positions_get(**kwargs)


class SlowInitializeBackend(FakeMT5):
    def __init__(self, *, marker, seconds=10):
        super().__init__()
        self.marker = marker
        self.seconds = seconds

    def initialize(self, *args, **kwargs):
        with open(self.marker, "wb"):
            pass
        hold_native_gil(self.seconds)
        return super().initialize(*args, **kwargs)


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

    def __init__(
        self,
        *,
        marker=None,
        trade_mode="done",
        trade_delay=0,
        trade_native=False,
    ):
        super().__init__()
        self.marker = marker
        self.trade_mode = trade_mode
        self.trade_delay = trade_delay
        self.trade_native = trade_native

    def order_send(self, request):
        self._call("order_send", request)
        if self.marker:
            with open(self.marker, "a", encoding="ascii") as stream:
                stream.write("send\n")
        if self.trade_mode == "die":
            os._exit(23)
        if self.trade_native:
            hold_native_gil(self.trade_delay)
        else:
            time.sleep(self.trade_delay)
        if self.trade_mode == "none":
            self.error = (-10004, "No connection")
            return None
        if self.trade_mode == "partial":
            return TradeResult(10010, 701, 702, 0.005, 2500.25, "partial", 2500.0, 2500.2)
        if self.trade_mode == "placed":
            return TradeResult(10008, 701, 0, 0.0, 0.0, "placed", 2500.0, 2500.2)
        if self.trade_mode == "reject":
            return TradeResult(10020, 0, 0, 0.0, 0.0, "price changed", 2500.0, 2500.2)
        return TradeResult(10009, 701, 702, 0.01, 2500.25, "done", 2500.0, 2500.2)


class PriorityProbeMT5(FakeTradeMT5):
    """Record externally visible transport order across reads and trades."""

    def __init__(self, *, marker, delay=0.15):
        super().__init__(marker=None)
        self.marker = marker
        self.delay = delay

    def symbol_info_tick(self, symbol):
        self._call("symbol_info_tick", symbol)
        time.sleep(self.delay)
        return Tick(int(time.time() * 1000), 2500.0, 2500.2)

    def positions_get(self, **kwargs):
        with open(self.marker, "a", encoding="ascii") as stream:
            stream.write("read\n")
        return super().positions_get(**kwargs)

    def order_send(self, request):
        with open(self.marker, "a", encoding="ascii") as stream:
            stream.write("trade\n")
        marker, self.marker = self.marker, None
        try:
            return super().order_send(request)
        finally:
            self.marker = marker
