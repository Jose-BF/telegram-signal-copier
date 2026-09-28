"""Recovering a confirmed fill is separate from proving its final money."""

from types import SimpleNamespace

import pytest

import listener
import position_lifecycle_monitor as monitor
from tests.test_e3_c_recovery_boundaries import _recovery


def history(magic, volume, profit, *, closed_volume=None):
    return [SimpleNamespace(magic=magic, entry=0, volume=volume, profit=0.,
                            commission=-.2, swap=0., fee=-.1),
            SimpleNamespace(magic=0, entry=1, volume=volume if closed_volume is None else closed_volume,
                            profit=profit, commission=-.2, swap=-.3, fee=-.7)]


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("evidence", ["complete", "unknown", "empty", "only_open", "partial", "wrong_owner"])
async def test_closed_recovery_keeps_ticket_in_complete_money_denominator(monkeypatch, direction, evidence):
    runtime, signal, record = _recovery(monkeypatch, direction)
    signal.candidate_entry_reconcile_pending_indexes = [1]
    monkeypatch.setattr(monitor, "_journal_event", lambda *a, **k: None)
    monkeypatch.setattr(monitor.executor.mt5, "positions_get", lambda **kw: [])
    assert await monitor.recover_durable_candidate_entries(runtime) == 1
    assert signal.dca_tickets == [record.ticket]
    assert signal.candidate_filled_leg_indexes == [1]
    assert signal.candidate_entry_prices_by_ticket[record.ticket] == record.fill_price
    assert not signal.candidate_entry_reconcile_pending_indexes
    missing = history(signal.magic, .03, -20.)
    if evidence == "unknown":
        missing = None
    elif evidence == "empty":
        missing = []
    elif evidence == "only_open":
        missing = missing[:1]
    elif evidence == "partial":
        missing = history(signal.magic, .03, -20., closed_volume=.01)
    elif evidence == "wrong_owner":
        missing = history(signal.magic + 1, .03, -20.)
    reads = []

    def read(position):
        reads.append(position)
        return history(signal.magic, .04, 10.) if position == signal.market_ticket else missing

    monkeypatch.setattr(listener.executor.mt5, "history_deals_get", read)
    money = listener._realized_pl(signal)
    assert record.ticket in reads
    if evidence == "complete":
        assert money == pytest.approx(-13.)
    else:
        assert money is None
    assert await monitor.recover_durable_candidate_entries(runtime) == 0
    assert signal.dca_tickets == [record.ticket]


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_unknown_positions_do_not_authorize_closed_recovery(monkeypatch, direction):
    runtime, signal, _ = _recovery(monkeypatch, direction)
    signal.candidate_entry_reconcile_pending_indexes = [1]
    monkeypatch.setattr(monitor, "_journal_anomaly", lambda *a, **k: None)
    monkeypatch.setattr(monitor.executor.mt5, "positions_get", lambda **kw: None)
    assert await monitor.recover_durable_candidate_entries(runtime) == 0
    assert signal.candidate_entry_reconcile_pending_indexes == [1]
    assert not signal.dca_tickets and not signal.candidate_filled_leg_indexes
