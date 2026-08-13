import pytest

import listener
from state import Signal


def _signal():
    return Signal(
        "canal2",
        1474,
        "BUY",
        market_ticket=101,
        extra_market_tickets=[102, 103, 104, 105],
    )


@pytest.mark.asyncio
async def test_secure_basket_queues_only_proved_partials(monkeypatch):
    signal = _signal()
    signal.build_context = lambda: (_ for _ in ()).throw(RuntimeError("unused"))
    snapshot = {
        "account_currency": "EUR",
        "realized_complete": True,
        "realized_pnl": 0.0,
        "missing_realized_tickets": [],
        "open_legs": [
            {
                "ticket": ticket,
                "current_pnl": 4.0,
                "stop_pnl": -10.0,
                "target_distance": float(index),
                "sl": 4350.0,
                "tp": 4370.0 + index,
            }
            for index, ticket in enumerate(
                [101, 102, 103, 104, 105], start=1
            )
        ],
    }
    closed = []
    modified = []
    events = []

    async def fake_run(fn, *args, **kwargs):
        if fn is listener.executor.risk_free_basket_snapshot:
            return snapshot
        raise AssertionError(f"unexpected MT5 call: {fn}")

    monkeypatch.setattr(listener, "_run", fake_run)
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_close_position",
        lambda sig, ticket, label="": closed.append(ticket),
    )
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_modify_sl",
        lambda *args, **kwargs: modified.append((args, kwargs)),
    )
    monkeypatch.setattr(listener.logger, "log_action", lambda *a, **kw: None)
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig, ev, **kw: events.append((sig, ev, kw)),
    )
    monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **kw: None)

    await listener._execute_one_action(
        signal,
        {
            "action": "SECURE_BASKET",
            "confidence": 0.95,
            "_reason": "provider_generic_risk_free",
        },
        raw_text="Make your trade risk free",
    )

    assert closed == [101, 102, 103, 104]
    assert modified == []
    assert signal.status == "open"
    assert signal.risk_free_close_tickets == [101, 102, 103, 104]
    decision = next(row for row in events if row[1] == "risk_free_basket_decision")
    assert decision[2]["status"] == "secure"
    assert decision[2]["projected_floor"] == pytest.approx(5.0)
    assert decision[2]["account_currency"] == "EUR"


@pytest.mark.asyncio
async def test_secure_basket_preserves_trade_when_proof_is_incomplete(
        monkeypatch):
    signal = _signal()
    signal.build_context = lambda: (_ for _ in ()).throw(RuntimeError("unused"))
    events = []
    closes = []

    async def fake_run(fn, *args, **kwargs):
        if fn is listener.executor.risk_free_basket_snapshot:
            return {
                "account_currency": "EUR",
                "realized_complete": False,
                "realized_pnl": None,
                "missing_realized_tickets": [99],
                "open_legs": [],
            }
        raise AssertionError(f"unexpected MT5 call: {fn}")

    monkeypatch.setattr(listener, "_run", fake_run)
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_close_position",
        lambda *args, **kwargs: closes.append(args),
    )
    monkeypatch.setattr(listener.logger, "log_action", lambda *a, **kw: None)
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig, ev, **kw: events.append((sig, ev, kw)),
    )
    monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **kw: None)
    monkeypatch.setattr(listener, "_schedule_detached", lambda value: value)
    monkeypatch.setattr(listener, "notify", lambda *a, **kw: None)

    await listener._execute_one_action(
        signal,
        {
            "action": "SECURE_BASKET",
            "confidence": 0.95,
            "_reason": "provider_generic_risk_free",
        },
        raw_text="Make your trade risk free",
    )

    assert closes == []
    decision = next(row for row in events if row[1] == "risk_free_basket_decision")
    assert decision[2]["status"] == "incomplete_evidence"
    assert signal.status == "open"
