"""A queued client intent cannot overwrite an independently executed native exit."""

from types import SimpleNamespace

import pytest

import position_lifecycle_monitor as monitor
from state import Signal


@pytest.mark.parametrize("intent", ["guard", "rescue", "close_first", "risk_free"])
@pytest.mark.parametrize("reason,comment,price,tag", [
    (4, "", 60., "SL"), (5, "", 105., "TP1"), (4, "[be]", 100., "LOSS_BE"),
])
def test_native_exit_wins_over_queued_client_close(monkeypatch, intent, reason, comment, price, tag):
    signal = Signal(channel="canal2", message_id=98766, direction="BUY", market_ticket=1001)
    signal.sl = 70.
    signal.tps = [105.]
    signal.tp_by_ticket[1001] = 105.
    field = {"guard": "basket_guard_close_tickets", "rescue": "be_rescue_tickets",
             "close_first": "close_first_tickets", "risk_free": "risk_free_close_tickets"}[intent]
    setattr(signal, field, [1001])
    signal.basket_guard_trigger_reason = "non_negative_time_exit"
    rows = [SimpleNamespace(ticket=1, entry=0, volume=.04, time_msc=1, price=100., profit=0., reason=3),
            SimpleNamespace(ticket=2, entry=1, volume=.04, time_msc=2, price=price,
                            profit=(price - 100.) * 4, reason=reason, comment=comment)]
    monkeypatch.setattr(monitor.mt5, "history_deals_get", lambda **_: rows)
    closure, = monitor._classify_closures(signal)
    assert closure["closed_by_tag"] == tag
    assert closure["classification_source"] == "broker_reason_and_effective_level"
    assert closure["pnl"] == (price - 100.) * 4
