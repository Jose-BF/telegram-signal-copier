import pytest

import listener
from state import Signal


@pytest.mark.asyncio
async def test_real_provider_range_replaces_provisional_range(monkeypatch):
    signal = Signal(
        channel="canal1",
        message_id=22001,
        direction="BUY",
        market_ticket=9001,
        market_fill_price=4489.0,
        dca_placed=True,
    )
    events = []
    updates = []
    range_safety_calls = []

    async def no_close(_signal, lo, hi):
        range_safety_calls.append((lo, hi))
        return False

    async def no_apply(*args, **kwargs):
        return None

    monkeypatch.setattr(listener, "_handle_range_arrival_safety", no_close)
    monkeypatch.setattr(listener, "_apply_sl_tp", no_apply)
    monkeypatch.setattr(
        listener.journal,
        "event",
        lambda sig, ev, **fields: events.append((ev, fields)),
    )
    monkeypatch.setattr(
        listener.journal,
        "update_trade",
        lambda sig, **fields: updates.append(fields),
    )

    await listener._apply_interpreted_entry_levels(
        signal,
        {"direction": "BUY"},
        "canal1",
        reference_price=4489.0,
    )

    assert signal.range_source == "provisional"
    assert not any(ev == "range_arrived" for ev, _ in events)

    await listener._apply_interpreted_entry_levels(
        signal,
        {
            "direction": "BUY",
            "range": (4488.0, 4495.0),
            "tps": [4498.0, 4502.0, 4506.0, 4510.0],
            "sl": 4480.0,
        },
        "canal1",
        reference_price=4489.0,
        tg_ts="2026-08-20T10:00:00+00:00",
    )

    assert (signal.range_low, signal.range_high) == (4488.0, 4495.0)
    assert signal.range_source == "provider"
    assert signal.provider_tps == [4498.0, 4502.0, 4506.0, 4510.0]
    assert signal.provider_sl_received is True
    assert any(ev == "range_arrived" for ev, _ in events)
    assert any(update.get("range_low") == 4488.0 for update in updates)

    await listener._apply_interpreted_entry_levels(
        signal,
        {
            "direction": "BUY",
            "range": (4488.0, 4495.0),
            "tps": [4498.0, 4502.0, 4506.0, 4510.0],
            "sl": 4480.0,
        },
        "canal1",
        reference_price=4489.0,
        tg_ts="2026-08-20T10:00:01+00:00",
    )

    assert range_safety_calls == [(4488.0, 4495.0)]


@pytest.mark.asyncio
async def test_final_target_extends_provider_sequence_without_corrupting_order(
    monkeypatch,
):
    signal = Signal(
        channel="canal2",
        message_id=1752,
        direction="SELL",
        market_ticket=9101,
        market_fill_price=4477.0,
        tps=[4472.0, 4469.0, 4465.0, 4463.0],
        provider_tps=[4472.0, 4469.0, 4465.0],
        dca_placed=True,
    )

    async def no_apply(*args, **kwargs):
        return None

    monkeypatch.setattr(listener, "_apply_sl_tp", no_apply)
    monkeypatch.setattr(listener.journal, "event", lambda *args, **kwargs: None)
    monkeypatch.setattr(listener.journal, "update_trade", lambda *args, **kwargs: None)

    await listener._update_signal_from_parsed(
        signal,
        {"final_target": 4436.0},
        provider_fields={"final_target"},
    )

    assert signal.provider_tps == [4472.0, 4469.0, 4465.0, 4436.0]
    assert signal.tps == [4472.0, 4469.0, 4465.0, 4436.0]


@pytest.mark.asyncio
async def test_final_target_before_tp_wave_keeps_provisional_intermediate_targets(
    monkeypatch,
):
    signal = Signal(
        channel="canal2",
        message_id=1753,
        direction="SELL",
        market_ticket=9102,
        market_fill_price=4477.0,
        tps=[4472.0, 4469.0, 4466.0, 4463.0],
        tps_source="provisional",
        dca_placed=True,
    )

    async def no_apply(*args, **kwargs):
        return None

    monkeypatch.setattr(listener, "_apply_sl_tp", no_apply)
    monkeypatch.setattr(listener.journal, "event", lambda *args, **kwargs: None)
    monkeypatch.setattr(listener.journal, "update_trade", lambda *args, **kwargs: None)

    await listener._update_signal_from_parsed(
        signal,
        {"final_target": 4436.0},
        provider_fields={"final_target"},
    )

    assert signal.provider_final_target == 4436.0
    assert signal.provider_tps == [4436.0]
    assert signal.tps == [4472.0, 4469.0, 4466.0, 4463.0, 4436.0]
    assert signal.tps_source == "mixed"


@pytest.mark.asyncio
async def test_later_tp_wave_cannot_drop_confirmed_final_target(monkeypatch):
    signal = Signal(
        channel="canal2",
        message_id=1754,
        direction="SELL",
        market_ticket=9103,
        market_fill_price=4477.0,
        tps=[4472.0, 4469.0, 4465.0, 4436.0],
        provider_tps=[4472.0, 4469.0, 4465.0, 4436.0],
        provider_final_target=4436.0,
        tps_source="provider",
        dca_placed=True,
    )

    async def no_apply(*args, **kwargs):
        return None

    monkeypatch.setattr(listener, "_apply_sl_tp", no_apply)
    monkeypatch.setattr(listener.journal, "event", lambda *args, **kwargs: None)
    monkeypatch.setattr(listener.journal, "update_trade", lambda *args, **kwargs: None)

    await listener._update_signal_from_parsed(
        signal,
        {"tps": [4471.0, 4468.0, 4464.0, 4461.0]},
        provider_fields={"tps"},
        provider_values={"tps": [4471.0, 4468.0, 4464.0, 4461.0]},
    )

    assert signal.provider_final_target == 4436.0
    assert signal.provider_tps == [4471.0, 4468.0, 4464.0, 4461.0, 4436.0]
    assert signal.tps == [4471.0, 4468.0, 4464.0, 4461.0, 4436.0]
