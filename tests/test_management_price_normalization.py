import pytest

import listener
from state import Signal


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "short_price,expected_price",
    [
        (75.0, 4575.0),
        (85.0, 4585.0),
    ],
)
async def test_move_sl_to_price_expands_canal2_short_gold_level(
        monkeypatch, short_price, expected_price):
    calls = []
    events = []

    def fake_enqueue_modify_sl(signal, ticket, price, label=""):
        calls.append({"ticket": ticket, "price": price, "label": label})

    monkeypatch.setattr(listener.pending_actions, "enqueue_modify_sl",
                        fake_enqueue_modify_sl)
    monkeypatch.setattr(listener.logger, "log_action",
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(listener.journal, "event",
                        lambda sig_id, ev, **kw: events.append({
                            "sig_id": sig_id, "ev": ev, **kw
                        }))

    sig = Signal(
        channel="canal2",
        message_id=13111,
        direction="SELL",
        market_ticket=1365772408,
        extra_market_tickets=[1365772471],
        market_fill_price=4575.36,
        range_low=4575.0,
        range_high=4579.0,
        tps=[4572.0, 4570.0, 4568.0, 4566.0],
        sl=4583.0,
    )

    outcome = await listener._execute_one_action(
        sig,
        {"action": "MOVE_SL_TO_PRICE",
         "price": short_price,
         "confidence": 1.0},
        raw_text=f"Move SL to {int(short_price)}",
    )

    assert [c["price"] for c in calls] == [expected_price, expected_price]
    assert outcome == "requested"
    assert all(str(expected_price) in c["label"] for c in calls)

    normalized = [e for e in events if e["ev"] == "mgmt_price_normalized"]
    assert normalized == [{
        "sig_id": "canal2_13111",
        "ev": "mgmt_price_normalized",
        "action": "MOVE_SL_TO_PRICE",
        "raw_price": short_price,
        "normalized_price": expected_price,
        "reference_price": 4575.36,
        "raw_snippet": f"Move SL to {int(short_price)}",
    }]


@pytest.mark.asyncio
async def test_move_sl_repairs_dropped_hundreds_digit_from_live_context(
        monkeypatch):
    calls = []
    events = []

    monkeypatch.setattr(
        listener.pending_actions,
        "enqueue_modify_sl",
        lambda signal, ticket, price, label="", **kwargs: calls.append({
            "ticket": ticket,
            "price": price,
            "label": label,
        }),
    )
    monkeypatch.setattr(listener.logger, "log_action", lambda *a, **k: None)
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig_id, ev, **kw: events.append({
            "sig_id": sig_id,
            "ev": ev,
            **kw,
        }),
    )

    signal = Signal(
        channel="canal1",
        message_id=21321,
        direction="SELL",
        market_ticket=1739049504,
        extra_market_tickets=[1739049522],
        market_fill_price=4346.36,
        range_low=4345.0,
        range_high=4350.0,
        tps=[4340.0, 4335.0, 4330.0, 4325.0],
        sl=4365.0,
    )

    await listener._execute_one_action(
        signal,
        {
            "action": "MOVE_SL_TO_PRICE",
            "price": 4050.0,
            "confidence": 0.95,
        },
        raw_text="Running +50 pips move sl to 4050.00 around be",
    )

    assert [call["price"] for call in calls] == [4350.0, 4350.0]
    repair = next(event for event in events
                  if event["ev"] == "mgmt_price_normalized")
    assert repair["raw_price"] == 4050.0
    assert repair["normalized_price"] == 4350.0
    assert repair["reference_price"] == 4346.36
