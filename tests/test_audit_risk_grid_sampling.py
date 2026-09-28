from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from research.causal_comparison import SequenceEvent
from research.risk_trajectory import RiskQuote, RiskSpec, reconstruct_risk
from tools.audit_risk_grid_sampling import compare_sample_grids


def test_same_path_attributes_hidden_drawdown_to_missing_quote_grid():
    base = datetime(2026, 9, 18, tzinfo=timezone.utc)
    at = lambda second: base + timedelta(seconds=second)
    events = [
        SequenceEvent(1, "entry", at(1), "BUY", D(100), D(1), D(0), "native"),
        SequenceEvent(1, "exit", at(5), "BUY", D(101), D(1), D(1), "native"),
    ]
    quotes = [RiskQuote(at(i), D(price), D(price)) for i, price in
              ((0, 100), (1, 100), (2, 102), (3, 98), (4, 101), (5, 101))]
    spec = RiskSpec("USD", 2, D(1), "identity", 5000, 5000)
    path = reconstruct_risk(events, quotes, spec=spec)

    comparison = compare_sample_grids(path["samples"], events, {at(2), at(4)})

    assert comparison["full_tick_max_drawdown_eur"] == "4.00"
    assert comparison["transition_only_max_drawdown_eur"] == "1.00"
    assert comparison["transition_plus_event_max_drawdown_eur"] == "1.00"
    assert comparison["event_boundary_drawdown_contribution_eur"] == "0.00"
    assert comparison["grid_omission_drawdown_gap_eur"] == "3.00"
    assert comparison["full_trough_present_in_transition_plus_event_grid"] is False
    assert comparison["event_boundary_count"] == 2


def test_event_boundaries_remain_even_without_transition_ticks():
    base = datetime(2026, 9, 18, tzinfo=timezone.utc)
    events = [
        SequenceEvent(1, "entry", base, "BUY", D(100), D(1), D(-1), "native"),
        SequenceEvent(1, "exit", base + timedelta(seconds=2), "BUY", D(101), D(1), D(1), "native"),
    ]
    quotes = [RiskQuote(base + timedelta(seconds=i), D(100 + i), D(100 + i))
              for i in range(3)]
    path = reconstruct_risk(events, quotes,
                            spec=RiskSpec("USD", 2, D(1), "identity", 5000, 5000))

    comparison = compare_sample_grids(path["samples"], events, set())

    assert comparison["event_boundary_count"] == 2
    assert comparison["transition_timestamp_count"] == 0
    assert comparison["transition_only_max_drawdown_eur"] == "0.00"
    assert comparison["event_boundary_drawdown_contribution_eur"] == "1.00"
    assert comparison["grid_omission_drawdown_gap_eur"] == "0.00"


def test_missing_shadow_tick_inside_full_path_is_not_silently_dropped():
    base = datetime(2026, 9, 18, tzinfo=timezone.utc)
    events = [SequenceEvent(1, "entry", base, "BUY", D(100), D(1), D(0), "native")]
    samples = [{"at": base, "total": D(0)},
               {"at": base + timedelta(seconds=2), "total": D(-1)}]
    with pytest.raises(ValueError, match="shadow transition absent"):
        compare_sample_grids(samples, events, {base + timedelta(seconds=1)})
