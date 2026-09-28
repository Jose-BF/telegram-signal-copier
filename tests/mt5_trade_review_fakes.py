"""Spawn-safe owned position for E3-B repair review."""

from tests.mt5_read_fakes import FakeTradeMT5


class OwnedTradeMT5(FakeTradeMT5):
    def positions_get(self, **kwargs):
        self._call("positions_get", **kwargs)
        return ({"ticket": 55, "symbol": "XAUUSD", "magic": 222,
                 "volume": 0.02, "type": self.POSITION_TYPE_BUY,
                 "sl": 2480.0, "tp": 2530.0},)
