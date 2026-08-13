from types import SimpleNamespace

import pytest

import listener
from classifier import classify_local
from interpretation_firewall import firewall_decision
from state import Signal


def _context(pnl):
    return SimpleNamespace(
        n_open=2,
        n_initial=2,
        floating_pnl_total=float(pnl),
        current_price=4055.0,
        elapsed_min=3.0,
        be_armed=False,
        summary_oneline=lambda: f"pl={pnl:+.2f}",
    )


def test_exact_or_instruction_becomes_one_contextual_action():
    actions = classify_local("Close overall profit OR set breakeven")

    assert [action["action"] for action in actions] == [
        "CLOSE_PROFIT_OR_BE"
    ]
    assert actions[0]["_reason"] == "close_profit_or_exact_be"


def test_contextual_profit_or_be_action_passes_firewall():
    signal = Signal("canal2", 700, "BUY")
    classification = {
        "action": "CLOSE_PROFIT_OR_BE",
        "confidence": 0.99,
        "_reason": "close_profit_or_exact_be",
    }

    decision = firewall_decision(
        signal,
        classification,
        raw_text="Close overall profit OR set breakeven",
    )

    assert decision.will_execute is True
    assert decision.policy == "auto_execute"


@pytest.mark.asyncio
async def test_positive_basket_closes_once_without_moving_be(monkeypatch):
    signal = Signal(
        "canal2",
        701,
        "BUY",
        market_ticket=1001,
        extra_market_tickets=[1002],
        pending_tickets=[2001],
    )
    signal.build_context = lambda: _context(4.25)
    closes = []
    cancels = []
    modifies = []
    finalized = []
    events = []

    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_close_position",
        lambda sig, ticket, label="": closes.append(ticket),
    )
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_cancel_pending",
        lambda sig, ticket, label="": cancels.append(ticket),
    )
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_modify_sl",
        lambda *a, **kw: modifies.append((a, kw)),
    )
    monkeypatch.setattr(listener.logger, "log_action", lambda *a, **kw: None)
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig, ev, **kw: events.append((sig, ev, kw)),
    )

    async def fake_finalize(sig, closed_by, notes=""):
        finalized.append((sig, closed_by, notes))

    monkeypatch.setattr(listener, "_finalize_signal", fake_finalize)

    outcome = await listener._execute_one_action(
        signal,
        {
            "action": "CLOSE_PROFIT_OR_BE",
            "confidence": 0.99,
            "_reason": "close_profit_or_exact_be",
        },
        raw_text="Close overall profit OR set breakeven",
    )

    assert closes == [1001, 1002]
    assert outcome == "requested"
    assert cancels == [2001]
    assert modifies == []
    assert signal.status == "closed"
    assert finalized[0][1] == "CLOSE_PROFIT_OR_BE"
    resolved = [row for row in events if row[1] == "close_profit_or_be_resolved"]
    assert resolved[0][2]["selected_action"] == "CLOSE_ALL"
    assert resolved[0][2]["floating_pnl"] == pytest.approx(4.25)


@pytest.mark.asyncio
async def test_non_positive_basket_sets_exact_be_without_closing(monkeypatch):
    signal = Signal(
        "canal2",
        702,
        "SELL",
        market_ticket=1101,
        extra_market_tickets=[1102],
    )
    signal.build_context = lambda: _context(-0.01)
    closes = []
    modifies = []
    events = []

    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_close_position",
        lambda sig, ticket, label="": closes.append(ticket),
    )
    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_modify_sl",
        lambda sig, ticket, price, **kw: modifies.append((ticket, price)),
    )
    monkeypatch.setattr(listener.logger, "log_action", lambda *a, **kw: None)
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig, ev, **kw: events.append((sig, ev, kw)),
    )
    monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **kw: None)

    async def fake_run(fn, *args, **kwargs):
        if fn is listener.executor.open_entry_prices:
            return {1101: 4057.25, 1102: 4056.80}
        raise AssertionError(f"unexpected MT5 call: {fn}")

    monkeypatch.setattr(listener, "_run", fake_run)

    outcome = await listener._execute_one_action(
        signal,
        {
            "action": "CLOSE_PROFIT_OR_BE",
            "confidence": 0.99,
            "_reason": "close_profit_or_exact_be",
        },
        raw_text="Close overall profit OR set breakeven",
    )

    assert closes == []
    assert outcome == "requested"
    assert modifies == [(1101, 4057.25), (1102, 4056.80)]
    assert signal.status == "open"
    assert signal.be_armed is True
    resolved = [row for row in events if row[1] == "close_profit_or_be_resolved"]
    assert resolved[0][2]["selected_action"] == "MOVE_SL_TO_BE"
    assert resolved[0][2]["floating_pnl"] == pytest.approx(-0.01)
