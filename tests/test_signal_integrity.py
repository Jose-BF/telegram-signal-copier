from datetime import datetime
from types import SimpleNamespace

import listener
from state import Signal


class FakeJournal:
    def __init__(self):
        self.events = []
        self.anomalies = []
        self.finalized = []

    def event(self, signal_id, ev, **fields):
        self.events.append({"sig": signal_id, "ev": ev, **fields})

    def anomaly(self, signal_id, category, severity, detail, **ctx):
        self.anomalies.append({
            "sig": signal_id,
            "category": category,
            "severity": severity,
            "detail": detail,
            **ctx,
        })

    def finalize_trade(self, signal_id, **fields):
        self.finalized.append({"sig": signal_id, **fields})


def _signal():
    sig = Signal(
        channel="canal2",
        message_id=13288,
        direction="SELL",
        timestamp=datetime(2026, 6, 3, 9, 32, 21),
        market_ticket=1380715618,
        extra_market_tickets=[1380715640, 1380715659, 1380715674],
    )
    sig.status = "closed"
    return sig


async def test_finalize_signal_blocks_when_mt5_still_has_open_position(
        monkeypatch):
    journal = FakeJournal()
    monkeypatch.setattr(listener, "journal", journal)
    monkeypatch.setattr(
        "MetaTrader5.positions_get",
        lambda: [
            SimpleNamespace(
                ticket=1380715690,
                magic=20260422,
                comment="c2_13288_B4",
                symbol="XAUUSD",
                volume=0.01,
                price_open=4568.20,
                sl=0.0,
                tp=0.0,
            )
        ],
    )

    sig = _signal()

    await listener._finalize_signal(sig, closed_by="BE")

    assert journal.finalized == []
    assert sig.status == "open"
    assert journal.events[0]["ev"] == "signal_integrity_snapshot"
    assert journal.events[0]["can_finalize"] is False
    assert journal.events[0]["open_tickets"] == [1380715690]
    issue = journal.anomalies[0]
    assert issue["category"] == "outcome"
    assert issue["severity"] == "critical"
    assert issue["code"] == "finalize_blocked_mt5_positions_open"
    assert issue["open_tickets"] == [1380715690]


async def test_finalize_signal_treats_enqueued_close_as_expected_transition(
        monkeypatch):
    journal = FakeJournal()
    monkeypatch.setattr(listener, "journal", journal)
    monkeypatch.setattr(
        "MetaTrader5.positions_get",
        lambda: [
            SimpleNamespace(
                ticket=1380715618,
                magic=20260422,
                comment="c2_13288",
                symbol="XAUUSD",
                volume=0.01,
                price_open=4568.20,
                sl=4558.0,
                tp=4572.0,
            )
        ],
    )

    sig = _signal()

    await listener._finalize_signal(sig, closed_by="CLOSE_ALL")

    assert journal.finalized == []
    assert sig.status == "open"
    snapshot = journal.events[0]
    assert snapshot["ev"] == "signal_integrity_snapshot"
    assert snapshot["reason"] == "mt5_positions_closing_async"
    assert snapshot["open_tickets"] == [1380715618]
    assert not journal.anomalies


async def test_finalize_signal_continues_when_mt5_has_no_open_positions(
        monkeypatch):
    journal = FakeJournal()
    monkeypatch.setattr(listener, "journal", journal)
    monkeypatch.setattr("MetaTrader5.positions_get", lambda: [])
    monkeypatch.setattr("MetaTrader5.history_deals_get", lambda position: [])
    monkeypatch.setattr(
        listener.executor,
        "account_evidence",
        lambda: {"currency": "EUR"},
    )

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(listener.asyncio, "sleep", no_sleep)

    sig = _signal()

    await listener._finalize_signal(sig, closed_by="TP")

    assert len(journal.finalized) == 1
    assert journal.finalized[0]["sig"] == "canal2_13288"
    assert journal.finalized[0]["closed_by"] == "TP"
    assert journal.finalized[0]["account_currency"] == "EUR"
    assert journal.finalized[0]["closed_at_utc"].endswith("+00:00")
    assert not journal.anomalies
