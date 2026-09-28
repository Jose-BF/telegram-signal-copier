from decimal import Decimal as D

import numpy as np
import pytest

from research.causal_comparison import SequenceEvent
from research.risk_trajectory import RiskSpec, reconstruct_risk
from tools.audit_native_week_risk_path import (
    native_events, normalized_utc, quote_grid, source_msc, summarize_path,
)


def event(kind, source_millis, *, money="0"):
    return SequenceEvent(10, kind, normalized_utc(source_millis), "BUY", D(100), D(1), D(money), "native")


def test_full_tick_grid_keeps_duplicates_and_uses_fx_available_at_event_boundary():
    market = (np.array([1000, 2000, 2000, 3000], dtype=np.int64),
              np.array([100., 99., 98., 101.]), np.array([100., 99., 98., 101.]))
    fx = (np.array([900, 1400, 2400, 4000], dtype=np.int64),
          np.array([1., 1.1, 1.2, 1.3]), np.array([1., 1.1, 1.2, 1.3]))
    events = [event("entry", 1500), event("exit", 3500, money="10")]
    quotes = quote_grid(market, fx, events)
    assert [source_msc(q.at) for q in quotes] == [1000, 1500, 2000, 2000, 3000, 3500]
    assert source_msc(quotes[1].market_at) == 1000
    assert source_msc(quotes[1].conversion_at) == 1400
    assert [q.bid for q in quotes[2:4]] == [D(99), D(98)]
    spec = RiskSpec("EUR", 2, D(100), "account_base_profit_quote", 5000, 5000)
    report = reconstruct_risk(events, quotes, spec=spec)
    summary = summarize_path(report)
    assert summary["sample_count"] == 6
    assert summary["metrics"]["final_net"] == "10"
    assert summary["metrics"]["minimum_from_origin"] == "-181.82"
    assert summary["sample_stream_sha256"]


def test_native_event_money_is_bound_to_fills_and_swap_does_not_disappear():
    entry = {"ticket": 1, "position_id": 10, "symbol": "XAUUSD", "entry": 0,
             "type": 0, "time_msc": 1500, "volume": 1, "price": 100,
             "profit": 0, "commission": -1, "swap": 0, "fee": 0, "reason": 3}
    close = {**entry, "ticket": 2, "entry": 1, "type": 1, "time_msc": 3500,
             "price": 101, "profit": 10, "commission": 0, "reason": 4}
    position = {"position_id": 10, "deal_tickets": [1, 2], "actual_net_eur": "9.00"}
    events = native_events([entry, close], [position])[10]
    assert [row.money for row in events] == [D(-1), D(10)]
    assert [row.mechanism for row in events] == ["native_reason_3", "native_reason_4"]
    close["swap"] = -1
    with pytest.raises(ValueError, match="swap unsupported"):
        native_events([entry, close], [position])
