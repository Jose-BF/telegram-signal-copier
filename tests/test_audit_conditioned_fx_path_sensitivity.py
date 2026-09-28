from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import numpy as np
import pytest

from research.causal_comparison import SequenceEvent
from tools.audit_conditioned_fx_path_sensitivity import bracketed_quotes, exit_timing, path_difference


BASE = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)


def test_bracketed_quotes_keep_prior_fx_price_and_only_attach_future_clock():
    base_ns = int(BASE.timestamp() * 1_000_000_000)
    market = (np.array([base_ns, base_ns + 10_000_000_000], dtype=np.int64),
              np.array([100.0, 99.0]), np.array([100.1, 99.1]))
    conversion = (np.array([base_ns - 2_000_000_000,
                            base_ns + 12_000_000_000], dtype=np.int64),
                  np.array([1.10, 1.20]), np.array([1.11, 1.21]))
    events = (SequenceEvent(1, "entry", BASE, "BUY", D(100), D(".01"),
                            D(0), "native"),
              SequenceEvent(1, "exit", BASE + timedelta(seconds=10), "BUY",
                            D(99), D(".01"), D(0), "native"))
    quotes = bracketed_quotes(market, conversion, events)
    assert len(quotes) == 2
    assert all(quote.conversion_bid == D("1.1") for quote in quotes)
    assert all(quote.conversion_next_at == BASE + timedelta(seconds=12)
               for quote in quotes)


def test_path_difference_detects_exposure_even_when_final_money_matches():
    def sample(at, count):
        return {"at": at, "ordinal": 0, "total": D(5), "floating": D(0),
                "realized": D(5), "long_volume": D(count),
                "short_volume": D(0), "open_count": count, "positions": {}}

    native = {"samples": [sample(BASE, 0), sample(BASE + timedelta(seconds=1), 0)]}
    model = {"samples": [sample(BASE, 1), sample(BASE + timedelta(seconds=1), 0)]}
    result = path_difference(native, model)
    assert result["counts"]["comparable"] == 2
    assert result["counts"]["any"] == 1
    assert result["counts"]["open_count"] == 1
    assert result["max_abs_total_difference_eur"] == D(0)


def test_exit_timing_pairs_logical_slot_and_rejects_missing_leg():
    native = [SequenceEvent(2, "exit", BASE + timedelta(seconds=2), "BUY",
                            D(101), D(".01"), D(".87"), "native")]
    model = [SequenceEvent(2, "exit", BASE + timedelta(seconds=1), "BUY",
                           D(101), D(".01"), D(".87"), "per_leg_target")]
    result = exit_timing(native, model)
    assert result[0]["slot"] == 2
    assert result[0]["model_minus_native_ms"] == -1000
    with pytest.raises(ValueError, match="slot sets"):
        exit_timing(native, [])
