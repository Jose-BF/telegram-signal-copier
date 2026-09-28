"""The TP risk audit must retain quote and native-exit ordering."""

from datetime import datetime, timedelta, timezone

import numpy as np

from tools.audit_tp_fill_scenario_risk import simulate_fill


BASE = datetime(2026, 9, 18, 12, tzinfo=timezone.utc)


def at(ms):
    return BASE + timedelta(milliseconds=ms)


def tape(stamps, bids, flags, asks=None):
    return ((np.array([int(at(ms).timestamp() * 1_000) * 1_000_000
                       for ms in stamps], dtype=np.int64),
             np.array(bids, dtype=float),
             np.array(asks if asks is not None else
                      [round(bid + 0.1, 2) for bid in bids], dtype=float)),
            np.array(flags, dtype=np.uint32))


def row(exit_ms):
    return {"accepted_response_utc": at(0).isoformat(),
            "native_exit_utc": at(exit_ms).isoformat(),
            "position_id": 1, "direction": "BUY", "target": "101.00"}


def test_deferred_fill_uses_first_quote_after_due_even_without_bid_update():
    market, flags = tape([10, 50, 110, 150], [101.1, 99.5, 99.5, 98.0],
                         [2, 2, 4, 2], [101.2, 99.6, 99.7, 98.1])

    result = simulate_fill(row(180), {"entry_price": "100.00"}, market,
                           flags, delay_ms=100, price_mode="executable_quote",
                           cutoff_ns=int(at(200).timestamp() * 1_000_000_000))

    assert result["status"] == "filled"
    assert result["first_touch_at"] == at(10)
    assert result["fill_at"] == at(110)
    assert result["fill_price"] == 99.5
    assert result["processed_quote_updates"] == 3


def test_touch_after_native_exit_does_not_create_hypothetical_tp():
    market, flags = tape([10, 110], [100.5, 101.1], [2, 2])

    result = simulate_fill(row(100), {"entry_price": "100.00"}, market,
                           flags, delay_ms=0, price_mode="installed_level",
                           cutoff_ns=int(at(200).timestamp() * 1_000_000_000))

    assert result["status"] == "no_pre_exit_touch"
    assert result["position_status"] == "open"
