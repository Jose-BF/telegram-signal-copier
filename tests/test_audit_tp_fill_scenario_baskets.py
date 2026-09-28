"""Basket sensitivity must replace only the intended native TP exit."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from research.causal_comparison import SequenceEvent
from tools.audit_tp_fill_scenario_baskets import replace_exits


BASE = datetime(2026, 9, 18, 12, tzinfo=timezone.utc)


def position():
    return {"position_id": 17, "exit_msc": int((BASE + timedelta(hours=3)).timestamp() * 1000),
            "direction": "BUY", "exit_price": "101.00", "volume": "0.04",
            "actual_net_eur": "3.47"}


def native():
    return (
        SequenceEvent(1, "entry", BASE - timedelta(seconds=1), "BUY",
                      Decimal("100.00"), Decimal("0.04"), Decimal("0"), "market"),
        SequenceEvent(1, "exit", BASE, "BUY", Decimal("101.00"),
                      Decimal("0.04"), Decimal("3.47"), "tp"),
    )


def test_replaces_exact_exit_with_world_fill_and_preserves_entry():
    world = {"status": "risk_path_compared",
             "fill_at": (BASE + timedelta(milliseconds=500)).isoformat(),
             "fill_price": "100.90", "scenario_booked_eur": "3.12"}

    result = replace_exits(native(), [position()], {17: world})

    assert result[0] == native()[0]
    assert result[1].at == BASE + timedelta(milliseconds=500)
    assert result[1].price == Decimal("100.90")
    assert result[1].money == Decimal("3.12")
    assert result[1].mechanism == "hypothetical_passive_tp"


def test_rejects_unreconciled_native_exit_identity():
    wrong = {**position(), "actual_net_eur": "3.48"}
    world = {"status": "risk_path_compared", "fill_at": BASE.isoformat(),
             "fill_price": "101.00", "scenario_booked_eur": "3.47"}

    with pytest.raises(ValueError, match="identity ambiguous"):
        replace_exits(native(), [wrong], {17: world})
