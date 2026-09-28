from types import SimpleNamespace
from datetime import datetime

import pytest

import position_lifecycle_monitor as monitor
from state import Signal
from basket_observation import basket_summary_reads, drive_reads


def position(ticket, profit, volume=.01):
    return SimpleNamespace(ticket=ticket, profit=profit, volume=volume, price_open=100., swap=-99.)


def history(profit, closed_volume=.01):
    return [SimpleNamespace(entry=0, volume=.01, profit=0., commission=-.10, swap=0., fee=0.),
            SimpleNamespace(entry=1, volume=closed_volume, profit=profit, commission=-.10, swap=-.05, fee=-.01)]


@pytest.mark.parametrize("missing", [False, True])
def test_existing_live_summary_preserves_read_order_and_money_definition(monkeypatch, missing):
    signal = Signal(channel="canal1", message_id=42, direction="BUY", market_ticket=11,
                    extra_market_tickets=[12, 13])
    calls = []
    monkeypatch.setattr(monitor.mt5, "symbol_info_tick", lambda symbol: calls.append("tick") or SimpleNamespace(bid=105., ask=105.2))
    monkeypatch.setattr(monitor.mt5, "positions_get", lambda: calls.append("positions") or [position(11, 4.), position(90, 500.)])
    monkeypatch.setattr(monitor.mt5, "history_deals_get", lambda position: calls.append(position) or (
        None if missing and position == 13 else history(2. if position == 12 else 3.)))
    monkeypatch.setattr(monitor, "_journal_event", lambda *args, **kwargs: None)
    summary = monitor._signal_pl_summary(signal)
    assert calls == ["tick", "positions", 12, 13]
    assert summary["floating_pl"] == 4.
    assert summary["open_tickets"] == [11]
    assert summary["realized_pl"] == pytest.approx(1.74 if missing else 4.48)
    assert summary["realized_complete"] is (not missing)
    assert summary["total_pl"] == (None if missing else pytest.approx(8.48))
    started = datetime.fromisoformat(summary["positions_read_started_utc"])
    completed = datetime.fromisoformat(summary["positions_read_completed_utc"])
    assert started.tzinfo is not None and started <= completed
    assert summary["positions_read_elapsed_ms"] >= 0
    assert signal.basket_guard_known_tickets == [11, 12, 13]
    assert set(signal.basket_guard_realized_by_ticket) == ({12} if missing else {12, 13})


@pytest.mark.parametrize("positions, expected_complete, expected_calls", [
    (None, False, ["symbol_info_tick", "positions_get"]),
    ([], True, ["symbol_info_tick", "positions_get", "history_deals_get_position"]),
    ([position(11, 4.)], True, ["symbol_info_tick", "positions_get"]),
])
def test_unknown_and_empty_positions_have_distinct_money_evidence(positions, expected_complete, expected_calls):
    calls, cache = [], {}
    def read(request):
        calls.append(request.operation.value)
        if request.operation.value == "symbol_info_tick":
            return None
        return positions if request.operation.value == "positions_get" else history(2.)
    summary = drive_reads(basket_summary_reads("XAUUSD", "BUY", lambda: [11], lambda: [], cache), read)
    assert calls == expected_calls
    assert summary["realized_complete"] is expected_complete
    assert summary["current_price"] is None
    if positions is None:
        assert summary["total_pl"] is None and cache == {}
    elif not positions:
        assert summary["total_pl"] == pytest.approx(1.74)
    else:
        assert summary["total_pl"] == 4.


def test_sell_price_and_string_history_cache_match_monitor_contract():
    requests = []
    cache = {"12": 2.}
    def read(request):
        requests.append(request.operation.value)
        return (SimpleNamespace(bid=105., ask=105.2) if request.operation.value == "symbol_info_tick"
                else [position(11, 3.)])
    result = drive_reads(basket_summary_reads("XAUUSD", "SELL", lambda: [11, 12, 11], lambda: [12], cache), read)
    assert result["current_price"] == 105.2 and result["total_pl"] == 5.
    assert requests == ["symbol_info_tick", "positions_get"]
    assert cache == {"12": 2.}


def test_native_read_error_closes_generator_without_requesting_next_stage():
    flow = basket_summary_reads("XAUUSD", "BUY", lambda: [11], lambda: [], {})
    def fail(request):
        raise RuntimeError("read failed")
    with pytest.raises(RuntimeError, match="read failed"):
        drive_reads(flow, fail)
    with pytest.raises(StopIteration):
        next(flow)
