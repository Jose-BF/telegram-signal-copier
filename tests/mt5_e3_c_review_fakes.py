"""Offline broker double for E3-C boundary review."""

from tests.mt5_read_fakes import FakeTradeMT5


class ReviewBoundaryMT5(FakeTradeMT5):
    def positions_get(self, **kwargs):
        self._call("positions_get", **kwargs)
        position = {"ticket": 55, "symbol": "XAUUSD", "magic": 20260422,
                    "volume": 0.01, "type": 0}
        if kwargs.get("ticket", 55) != 55:
            return ()
        return (position,)

    def copy_ticks_range(self, symbol, start, end, flags):
        self._call("copy_ticks_range", symbol, start, end, flags)
        rows = [{"time_msc": 20750, "bid": 2500.0, "ask": 2500.2,
                 "last": 2500.1, "flags": 6, "volume_real": 1.0}]
        return [row for row in rows
                if start.timestamp() * 1000 <= row["time_msc"]
                <= end.timestamp() * 1000]
