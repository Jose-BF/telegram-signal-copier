from types import SimpleNamespace

import pytest

import listener
from state import Signal


def deals(magic, *, closed_volume=.04, fee=-.7):
    return [SimpleNamespace(magic=magic, entry=0, volume=.04, profit=0., commission=-.2, swap=0., fee=-.1),
            SimpleNamespace(magic=0, entry=1, volume=closed_volume, profit=10., commission=-.2, swap=-.3, fee=fee)]


def signal():
    return Signal(channel="canal2", message_id=98766, direction="BUY", market_ticket=1001)


def test_final_money_includes_fees_and_manual_close_with_zero_magic(monkeypatch):
    sig = signal()
    monkeypatch.setattr("mt5_runtime.mt5.history_deals_get", lambda **_: deals(sig.magic))
    assert listener._realized_pl(sig) == pytest.approx(8.5)


@pytest.mark.parametrize("missing", [None, [], "only_open", "partial_close", "wrong_magic"])
def test_missing_or_incomplete_ticket_cannot_be_reported_as_known_money(monkeypatch, missing):
    sig = signal()
    history = deals(sig.magic)
    if missing == "only_open":
        history = history[:1]
    elif missing == "partial_close":
        history = deals(sig.magic, closed_volume=.02)
    elif missing == "wrong_magic":
        history = deals(777)
    else:
        history = missing
    monkeypatch.setattr("mt5_runtime.mt5.history_deals_get", lambda **_: history)
    assert listener._realized_pl(sig) is None


def test_one_complete_ticket_does_not_hide_another_missing_ticket(monkeypatch):
    sig = signal()
    sig.dca_tickets = [1002]
    monkeypatch.setattr("mt5_runtime.mt5.history_deals_get",
                        lambda position: deals(sig.magic) if position == 1001 else None)
    assert listener._realized_pl(sig) is None
