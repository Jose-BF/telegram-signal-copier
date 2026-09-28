"""Bounded recording for offline load probes; never imports native MT5."""

from collections import deque
import json
from pathlib import Path
from types import SimpleNamespace
import time

from tests.mt5_read_fakes import FakeMT5, FakeTradeMT5


class BoundedReadMT5(FakeMT5):
    def __init__(self):
        super().__init__()
        self.calls = deque(maxlen=32)


class OrderedTradeMT5(FakeTradeMT5):
    def __init__(self, *, order_log):
        super().__init__()
        self.order_log = Path(order_log)
        self.calls = deque(maxlen=32)

    def symbol_info_tick(self, symbol):
        time.sleep(.15)
        return super().symbol_info_tick(symbol)

    def positions_get(self, **kwargs):
        return [SimpleNamespace(
            ticket=42, symbol="XAUUSD", magic=111, type=0, volume=.01,
            price_open=2500.0, sl=2499.0, tp=2502.0,
        )]

    def order_send(self, request):
        kind = "close" if request.get("position") else "entry"
        with self.order_log.open("a", encoding="ascii") as stream:
            stream.write(kind + "\n")
        return super().order_send(request)


class MixedLoadMT5(FakeTradeMT5):
    def __init__(self):
        super().__init__()
        self.calls = deque(maxlen=32)

    def positions_get(self, **kwargs):
        return [SimpleNamespace(
            ticket=42, symbol="XAUUSD", magic=111, type=0, volume=.01,
            price_open=2500.0, sl=2499.0, tp=2502.0,
        )]


class PendingRevisionMT5(FakeTradeMT5):
    def __init__(self, *, magic, order_log):
        super().__init__(trade_delay=.3, trade_native=True)
        self.magic = magic
        self.order_log = Path(order_log)

    def positions_get(self, **kwargs):
        return [SimpleNamespace(
            ticket=12345, symbol="XAUUSD", magic=self.magic, type=0, volume=.01,
            price_open=2500.0, sl=2480.0, tp=2530.0,
        )]

    def order_send(self, request):
        with self.order_log.open("a", encoding="ascii") as stream:
            stream.write(json.dumps(request) + "\n")
        return super().order_send(request)
