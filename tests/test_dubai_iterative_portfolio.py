from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import numpy as np
import pandas as pd

from research.dubai_iterative.engine import (
    EntryRecord,
    ExecutionAssumptions,
    ExitRecord,
    SimulationResult,
)
from research.dubai_iterative.portfolio import (
    build_portfolio_tape,
    reconstruct_portfolio,
)


def _path(signal_id: str, prices: tuple[float, ...]):
    start = datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc)
    moments = tuple(start + timedelta(seconds=index) for index in range(len(prices)))
    return SimpleNamespace(
        signal_id=signal_id,
        direction="BUY",
        times_ns=np.asarray(
            [int(moment.timestamp() * 1_000_000_000) for moment in moments],
            dtype=np.int64,
        ),
        bid=np.asarray(prices, dtype=float),
        ask=np.asarray(prices, dtype=float),
        fx_bid=np.ones(len(prices), dtype=float),
        fx_ask=np.ones(len(prices), dtype=float),
        fx_valid=np.ones(len(prices), dtype=bool),
        contract_size=100.0,
        conversion_orientation="identity",
        currency_digits=2,
    ), moments


def _result(
    signal_id: str,
    *,
    ticket: str,
    entry_index: int,
    exit_index: int,
    entry_price: float,
    exit_price: float,
    pnl: str,
    moments: tuple[datetime, ...],
) -> SimulationResult:
    return SimulationResult(
        signal_id=signal_id,
        strategy_fingerprint="strategy",
        confidence_layer="counterfactual_entry",
        entries=(EntryRecord(
            ticket=ticket,
            tick_index=entry_index,
            opened_at=moments[entry_index],
            entry_price=entry_price,
            volume=0.01,
            source="test",
        ),),
        exits=(ExitRecord(
            ticket=ticket,
            tick_index=exit_index,
            closed_at=moments[exit_index],
            entry_price=entry_price,
            exit_price=exit_price,
            volume=0.01,
            pnl_eur=Decimal(pnl),
            reason="test_exit",
        ),),
        pnl_eur=Decimal(pnl),
        exit_reason="test_exit",
        max_favourable_eur=Decimal("0.00"),
        max_adverse_eur=Decimal("0.00"),
        max_floating_drawdown_eur=Decimal("0.00"),
        max_favourable_move=0.0,
        max_adverse_move=0.0,
        blockers=(),
        last_tick_index=exit_index,
        unfilled=False,
        filled_volume=0.01,
    )


def test_portfolio_reconstruction_measures_overlapping_equity_and_exposure():
    first_path, moments = _path("first", (100.0, 101.0, 99.0, 102.0))
    second_path, _ = _path("second", (100.0, 101.0, 99.0, 102.0))
    first = _result(
        "first",
        ticket="a",
        entry_index=0,
        exit_index=3,
        entry_price=100.0,
        exit_price=102.0,
        pnl="2.00",
        moments=moments,
    )
    second = _result(
        "second",
        ticket="b",
        entry_index=1,
        exit_index=2,
        entry_price=101.0,
        exit_price=99.0,
        pnl="-2.00",
        moments=moments,
    )

    report = reconstruct_portfolio(
        (first_path, second_path),
        (first, second),
    )

    assert report.blockers == ()
    assert report.net_eur == Decimal("0.00")
    assert report.peak_equity_eur == Decimal("1.00")
    assert report.minimum_equity_eur == Decimal("-3.00")
    assert report.max_drawdown_eur == Decimal("4.00")
    assert report.max_concurrent_volume == 0.02
    assert report.max_concurrent_signals == 2
    assert report.timeline_points == 4


def test_portfolio_reconstruction_rejects_an_exit_money_mismatch():
    path, moments = _path("first", (100.0, 101.0, 102.0))
    inconsistent = _result(
        "first",
        ticket="a",
        entry_index=0,
        exit_index=2,
        entry_price=100.0,
        exit_price=102.0,
        pnl="3.00",
        moments=moments,
    )

    report = reconstruct_portfolio((path,), (inconsistent,))

    assert report.net_eur is None
    assert report.max_drawdown_eur is None
    assert report.blockers == ("exit_money_mismatch:first:a",)


def test_portfolio_reconstruction_binds_the_execution_cost_scenario():
    path, moments = _path("first", (100.0, 101.0, 102.0))
    priced_with_cost = _result(
        "first",
        ticket="a",
        entry_index=0,
        exit_index=2,
        entry_price=100.0,
        exit_price=101.9,
        pnl="1.90",
        moments=moments,
    )

    wrong = reconstruct_portfolio((path,), (priced_with_cost,))
    matching = reconstruct_portfolio(
        (path,),
        (priced_with_cost,),
        execution=ExecutionAssumptions(exit_slippage=0.1),
    )

    assert wrong.blockers == ("execution_scenario_mismatch:first:a",)
    assert matching.blockers == ()
    assert matching.net_eur == Decimal("1.90")


def test_portfolio_reconstruction_fails_closed_on_missing_signal_result():
    first_path, _ = _path("first", (100.0, 101.0))
    second_path, _ = _path("second", (100.0, 101.0))

    report = reconstruct_portfolio((first_path, second_path), ())

    assert report.blockers == (
        "missing_result:first",
        "missing_result:second",
    )
    assert report.net_eur is None


def test_portfolio_reconstruction_preserves_real_microsecond_tick_identity():
    path, _ = _path("first", (100.0, 102.0))
    start = datetime(
        2026, 7, 27, 0, 25, 43, 531000, tzinfo=timezone.utc
    )
    moments = (start, start + timedelta(seconds=1))
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    path.times_ns = np.asarray([
        int((moment - epoch) // timedelta(microseconds=1)) * 1_000
        for moment in moments
    ], dtype=np.int64)
    result = _result(
        "first",
        ticket="a",
        entry_index=0,
        exit_index=1,
        entry_price=100.0,
        exit_price=102.0,
        pnl="2.00",
        moments=moments,
    )

    report = reconstruct_portfolio((path,), (result,))

    assert report.blockers == ()
    assert report.net_eur == Decimal("2.00")


def test_portfolio_uses_canonical_tape_for_duplicate_timestamp_order():
    path, moments = _path("first", (100.0, 101.0, 99.0, 102.0))
    moments = (moments[0], moments[1], moments[1], moments[2])
    path.times_ns = np.asarray([
        int(moment.timestamp() * 1_000_000_000) for moment in moments
    ], dtype=np.int64)
    second_path = SimpleNamespace(**vars(path))
    second_path.signal_id = "second"
    first = _result(
        "first", ticket="a", entry_index=0, exit_index=3,
        entry_price=100.0, exit_price=102.0, pnl="2.00", moments=moments,
    )
    second = _result(
        "second", ticket="b", entry_index=2, exit_index=3,
        entry_price=99.0, exit_price=102.0, pnl="3.00", moments=moments,
    )

    class Source:
        def load_day(self, _day):
            return pd.DataFrame({
                "time_utc": moments,
                "bid": (100.0, 101.0, 99.0, 102.0),
                "ask": (100.0, 101.0, 99.0, 102.0),
            }), {"verified": True}, []

    report = reconstruct_portfolio(
        (path, second_path),
        (first, second),
        market_tick_source=Source(),
    )

    assert report.blockers == ()
    assert report.timeline_points == 4
    assert report.net_eur == Decimal("5.00")
    assert report.max_drawdown_eur == Decimal("2.00")


def test_same_tick_entry_and_exit_does_not_remain_open_in_portfolio():
    path, moments = _path("first", (100.0, 50.0))
    immediate = _result(
        "first", ticket="a", entry_index=0, exit_index=0,
        entry_price=100.0, exit_price=100.0, pnl="0.00", moments=moments,
    )

    report = reconstruct_portfolio((path,), (immediate,))

    assert report.blockers == ()
    assert report.net_eur == Decimal("0.00")
    assert report.max_drawdown_eur == Decimal("0.00")


def test_canonical_portfolio_tape_is_reusable_without_reloading_source():
    path, moments = _path("first", (100.0, 101.0, 102.0))
    result = _result(
        "first", ticket="a", entry_index=0, exit_index=2,
        entry_price=100.0, exit_price=102.0, pnl="2.00", moments=moments,
    )

    class Source:
        calls = 0

        def load_day(self, _day):
            self.calls += 1
            return pd.DataFrame({
                "time_utc": moments,
                "bid": (100.0, 101.0, 102.0),
                "ask": (100.0, 101.0, 102.0),
            }), {"verified": True}, []

    source = Source()
    tape = build_portfolio_tape((path,), market_tick_source=source)
    first = reconstruct_portfolio((path,), (result,), portfolio_tape=tape)
    second = reconstruct_portfolio((path,), (result,), portfolio_tape=tape)

    assert source.calls == 1
    assert first == second
    assert first.net_eur == Decimal("2.00")


def test_portfolio_fails_closed_when_entry_tick_index_is_out_of_range():
    path, moments = _path("first", (100.0, 101.0, 102.0))
    result = _result(
        "first", ticket="a", entry_index=0, exit_index=2,
        entry_price=100.0, exit_price=102.0, pnl="2.00", moments=moments,
    )
    bad_entry = replace(result.entries[0], tick_index=99)
    malformed = replace(result, entries=(bad_entry,))

    report = reconstruct_portfolio((path,), (malformed,))

    assert report.net_eur is None
    assert report.blockers == ("entry_tick_mismatch:first:a",)


def test_canonical_portfolio_tape_arrays_are_read_only():
    path, moments = _path("first", (100.0, 101.0, 102.0))

    class Source:
        def load_day(self, _day):
            return pd.DataFrame({
                "time_utc": moments,
                "bid": (100.0, 101.0, 102.0),
                "ask": (100.0, 101.0, 102.0),
            }), {"verified": True}, []

    tape = build_portfolio_tape((path,), market_tick_source=Source())

    assert tape.blockers == ()
    assert all(not array.flags.writeable for array in (
        tape.times_ns,
        tape.bid_points,
        tape.ask_points,
        tape.fx_bid_points,
        tape.fx_ask_points,
        tape.fx_valid,
    ))


def test_canonical_tape_accepts_a_causal_quote_inside_verified_bracket():
    path, moments = _path("first", (100.0, 101.0, 102.0))
    moments = (
        moments[0],
        moments[0] + timedelta(seconds=10),
        moments[0] + timedelta(seconds=20),
    )
    path.times_ns = np.asarray(
        [int(moment.timestamp() * 1_000_000_000) for moment in moments],
        dtype=np.int64,
    )
    path.conversion_orientation = "account_base_profit_quote"

    class MarketSource:
        def load_day(self, _day):
            return pd.DataFrame({
                "time_utc": moments,
                "bid": (100.0, 101.0, 102.0),
                "ask": (100.1, 101.1, 102.1),
            }), {"verified": True}, []

    class ConversionSource:
        def load_day(self, _day):
            return pd.DataFrame({
                "time_utc": (
                    moments[0],
                    moments[0] + timedelta(seconds=30),
                ),
                "bid": (1.10, 1.20),
                "ask": (1.11, 1.21),
            }), {"verified": True}, []

    tape = build_portfolio_tape(
        (path,),
        market_tick_source=MarketSource(),
        conversion_tick_source=ConversionSource(),
        max_conversion_age_ms=5_000,
        max_conversion_interval_ms=60_000,
    )

    assert tape.blockers == ()
    assert tape.fx_valid.tolist() == [True, True, True]
    assert tape.fx_bid_points.tolist() == [110_000, 110_000, 110_000]
    assert tape.max_conversion_interval_ms == 60_000
    assert tape.valuation_mode == "verified_asof_or_bracketed_conversion"


def test_canonical_tape_rejects_a_conversion_feed_gap_beyond_contract():
    path, moments = _path("first", (100.0, 101.0))
    moments = (moments[0], moments[0] + timedelta(seconds=10))
    path.times_ns = np.asarray(
        [int(moment.timestamp() * 1_000_000_000) for moment in moments],
        dtype=np.int64,
    )
    path.conversion_orientation = "account_base_profit_quote"

    class MarketSource:
        def load_day(self, _day):
            return pd.DataFrame({
                "time_utc": moments,
                "bid": (100.0, 101.0),
                "ask": (100.1, 101.1),
            }), {"verified": True}, []

    class ConversionSource:
        def load_day(self, _day):
            return pd.DataFrame({
                "time_utc": (
                    moments[0],
                    moments[0] + timedelta(seconds=61),
                ),
                "bid": (1.10, 1.20),
                "ask": (1.11, 1.21),
            }), {"verified": True}, []

    tape = build_portfolio_tape(
        (path,),
        market_tick_source=MarketSource(),
        conversion_tick_source=ConversionSource(),
        max_conversion_age_ms=5_000,
        max_conversion_interval_ms=60_000,
    )

    assert tape.blockers == ()
    assert tape.fx_valid.tolist() == [True, False]
