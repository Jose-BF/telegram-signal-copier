from __future__ import annotations

import json
import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import main
import strategy_shadow_runtime


async def _async_none():
    return None


@pytest.fixture(autouse=True)
def clear_shadow_conversion_cache():
    main._shadow_conversion_tick_cache.clear()
    yield
    main._shadow_conversion_tick_cache.clear()


def money_contract(
    orientation="account_base_profit_quote",
    *,
    utc_offset_seconds=0,
):
    return {
        "captured_at_utc": "2026-08-27T07:59:00+00:00",
        "account": {"currency": "EUR", "currency_digits": 2},
        "instrument": {
            "symbol": "XAUUSD",
            "currency_profit": "USD",
            "contract_size": 100.0,
        },
        "conversion": {
            "orientation": orientation,
            "symbol": None if orientation == "identity" else "EURUSD",
            "max_quote_age_ms": 5000,
            "max_quote_interval_ms": 60000,
        },
        "costs": {
            "commission_model": "observed_zero_intraday",
            "fee_model": "observed_zero_intraday",
            "swap_model": "intraday_only_zero",
        },
        "swap_snapshots": [{
            "captured_at_utc": "2026-08-27T07:59:00+00:00",
            "time_evidence": {
                "utc_offset_seconds": utc_offset_seconds,
            },
        }],
        "schema_version": 1,
    }


def test_main_connects_telegram_before_background_shadow_recovery():
    source = inspect.getsource(main.main)
    telegram_start = source.index("await client.start")

    assert "await _initialize_strategy_shadows" not in source[:telegram_start]
    shadow_start = source.index(
        "asyncio.ensure_future(_initialize_strategy_shadows"
    )
    assert telegram_start < shadow_start


@pytest.mark.asyncio
async def test_shadow_runtime_is_not_visible_until_recovery_finishes(
    monkeypatch,
    tmp_path,
):
    import asyncio

    release = asyncio.Event()
    runtime_kwargs = {}
    confirmations = []

    class SlowRuntime:
        async def recover(self, _records, *, history_reader):
            del history_reader
            await release.wait()
            return ()

    runtime = SlowRuntime()

    def build_runtime(**kwargs):
        runtime_kwargs.update(kwargs)
        return runtime

    strategy_shadow_runtime.install_runtime(None)
    monkeypatch.setattr(main.config, "STRATEGY_SHADOW_ENABLED", True)
    monkeypatch.setattr(main.config, "STRATEGY_SHADOW_CHECKPOINT_SECONDS", 300)
    monkeypatch.setattr(
        main.config, "STRATEGY_SHADOW_SLOWDOWN_THRESHOLD_MS", 20.0,
    )
    monkeypatch.setattr(main.config, "STRATEGY_C1_BALANCED_V1_ENABLED", True)
    monkeypatch.setattr(main.config, "GOLD_NOW_LIVE_POLICY", "555")
    monkeypatch.setattr(
        main.strategy_shadow_runtime,
        "ShadowRuntime",
        build_runtime,
    )
    monkeypatch.setattr(main.journal, "event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        main.journal,
        "confirm_event",
        lambda receipt: confirmations.append(receipt) or True,
    )

    task = asyncio.create_task(
        main._initialize_strategy_shadows(tmp_path / "missing.jsonl")
    )
    await asyncio.sleep(0)

    assert strategy_shadow_runtime.installed_runtime() is None

    release.set()
    await task
    assert strategy_shadow_runtime.installed_runtime() is runtime
    assert await runtime_kwargs["journal_confirmer"]("receipt-1") is True
    assert confirmations == ["receipt-1"]


def test_live_tick_batch_exposes_pending_archive_tail(monkeypatch):
    cursor_tick = main._shadow_tick_from_values(
        time_msc=20_000,
        bid=4300.0,
        ask=4300.2,
        last=4300.1,
        flags=6,
        volume_real=1.0,
        factors={"positive": 100.0, "negative": 100.0},
        money_evidence_id="money-cursor",
    )
    archived = main._shadow_tick_from_values(
        time_msc=20_001,
        bid=4300.1,
        ask=4300.3,
        last=4300.2,
        flags=6,
        volume_real=1.0,
        factors={"positive": 100.0, "negative": 100.0},
        money_evidence_id="money-archived",
    )
    latest = main._shadow_tick_from_values(
        time_msc=20_002,
        bid=4300.2,
        ask=4300.4,
        last=4300.3,
        flags=6,
        volume_real=1.0,
        factors={"positive": 100.0, "negative": 100.0},
        money_evidence_id="money-latest",
    )
    monkeypatch.setattr(
        main,
        "_shadow_tick_history",
        lambda *_args, **_kwargs: strategy_shadow_runtime.ShadowTickHistory(
            ticks=(archived,), complete=True, evidence_id="archive-short",
        ),
    )

    history = main._shadow_live_tick_batch(
        strategy_shadow_runtime.ShadowTickCursor(
            from_msc=cursor_tick.time_msc,
            after_identity=cursor_tick.identity,
        ),
        latest,
    )

    assert history.complete is True
    assert history.blocker is None
    assert history.pending_reason == "live_archive_tail_pending"


def test_live_tick_batch_bounds_catchup_without_skipping_archived_ticks(
    monkeypatch,
):
    cursor_tick = main._shadow_tick_from_values(
        time_msc=20_000,
        bid=4300.0,
        ask=4300.2,
        last=4300.1,
        flags=6,
        volume_real=1.0,
        factors={"positive": 100.0, "negative": 100.0},
        money_evidence_id="money-cursor",
    )
    archived = main._shadow_tick_from_values(
        time_msc=80_000,
        bid=4301.0,
        ask=4301.2,
        last=4301.1,
        flags=6,
        volume_real=2.0,
        factors={"positive": 100.0, "negative": 100.0},
        money_evidence_id="money-archived",
    )
    latest = main._shadow_tick_from_values(
        time_msc=200_000,
        bid=4302.0,
        ask=4302.2,
        last=4302.1,
        flags=6,
        volume_real=3.0,
        factors={"positive": 100.0, "negative": 100.0},
        money_evidence_id="money-latest",
    )
    calls = []

    def bounded_history(from_msc, *, until_msc, after_identity):
        calls.append((from_msc, until_msc, after_identity))
        return strategy_shadow_runtime.ShadowTickHistory(
            ticks=(archived,),
            complete=True,
            evidence_id="bounded-history",
        )

    monkeypatch.setattr(
        main.config,
        "STRATEGY_SHADOW_LIVE_MAX_BATCH_MS",
        60_000,
        raising=False,
    )
    monkeypatch.setattr(main, "_shadow_tick_history", bounded_history)

    history = main._shadow_live_tick_batch(
        strategy_shadow_runtime.ShadowTickCursor(
            from_msc=cursor_tick.time_msc,
            after_identity=cursor_tick.identity,
        ),
        latest,
    )

    assert calls == [(20_000, 80_000, cursor_tick.identity)]
    assert history.complete is True
    assert history.ticks == (archived,)
    assert history.pending_reason == "live_catchup_chunk_remaining"


def test_conversion_factors_follow_verified_broker_orientation():
    factors = main._shadow_conversion_factors(
        money_contract(),
        conversion_bid=1.14,
        conversion_ask=1.15,
    )

    assert factors["positive"] == pytest.approx(100.0 / 1.15)
    assert factors["negative"] == pytest.approx(100.0 / 1.14)


def test_identity_money_contract_needs_no_conversion_quote():
    factors = main._shadow_conversion_factors(
        money_contract("identity"),
        conversion_bid=None,
        conversion_ask=None,
    )

    assert factors == {"positive": 100.0, "negative": 100.0}


def test_current_shadow_tick_contains_exact_primitive_evidence(monkeypatch):
    xau_tick = SimpleNamespace(
        time_msc=123456,
        bid=4300.0,
        ask=4300.2,
        last=4300.1,
        flags=6,
        volume_real=2.0,
    )
    eur_tick = SimpleNamespace(
        time_msc=123450,
        bid=1.14,
        ask=1.15,
    )
    fake_mt5 = SimpleNamespace(
        symbol_info_tick=lambda symbol: (
            xau_tick if symbol == "XAUUSD" else eur_tick
        )
    )
    monkeypatch.setattr(main.executor, "mt5", fake_mt5)
    monkeypatch.setattr(main, "_load_shadow_money_contract", money_contract)

    observed = main._shadow_tick_snapshot()

    assert observed.time_msc == 123456
    assert observed.bid == 4300.0
    assert observed.ask == 4300.2
    assert observed.money_evidence_id
    assert observed.money_factor("BUY", favourable=True) == pytest.approx(
        100.0 / 1.15
    )


def test_current_shadow_tick_normalizes_verified_broker_clock(monkeypatch):
    observed_utc = datetime(2026, 8, 27, 8, 0, tzinfo=timezone.utc)
    server_msc = int(
        (observed_utc + timedelta(hours=3)).timestamp() * 1000
    )
    xau_tick = SimpleNamespace(
        time_msc=server_msc,
        bid=4300.0,
        ask=4300.2,
        last=4300.1,
        flags=6,
        volume_real=2.0,
    )
    fake_mt5 = SimpleNamespace(
        symbol_info_tick=lambda _symbol: xau_tick,
    )
    monkeypatch.setattr(main.executor, "mt5", fake_mt5)
    monkeypatch.setattr(
        main.broker_tick_clock,
        "utc_now",
        lambda: observed_utc,
    )
    monkeypatch.setattr(
        main,
        "_load_shadow_money_contract",
        lambda: money_contract("identity", utc_offset_seconds=10_800),
    )

    observed = main._shadow_tick_snapshot()

    assert observed is not None
    assert observed.time_msc == int(observed_utc.timestamp() * 1000)
    assert observed.observed_at_utc == observed_utc.isoformat()


def test_shadow_history_queries_server_clock_and_returns_utc_ticks(monkeypatch):
    start_utc = datetime(2026, 8, 27, 8, 0, tzinfo=timezone.utc)
    end_utc = start_utc + timedelta(seconds=2)
    start_msc = int(start_utc.timestamp() * 1000)
    end_msc = int(end_utc.timestamp() * 1000)
    offset = 10_800
    raw_tick_msc = start_msc + offset * 1000 + 1000
    queries = []

    def copy_ticks(symbol, from_dt, until_dt, flags):
        queries.append((symbol, from_dt, until_dt, flags))
        return [{
            "time_msc": raw_tick_msc,
            "bid": 4300.0,
            "ask": 4300.2,
            "last": 4300.1,
            "flags": 6,
            "volume_real": 1.0,
        }]

    fake_mt5 = SimpleNamespace(COPY_TICKS_ALL=0, copy_ticks_range=copy_ticks)
    monkeypatch.setattr(main.executor, "mt5", fake_mt5)
    monkeypatch.setattr(
        main,
        "_load_shadow_money_contract",
        lambda: money_contract("identity", utc_offset_seconds=offset),
    )

    history = main._shadow_tick_history(start_msc, until_msc=end_msc)

    assert history.complete is True
    assert [tick.time_msc for tick in history.ticks] == [start_msc + 1000]
    assert queries[0][1] == (
        start_utc + timedelta(seconds=offset, milliseconds=-1)
    )
    assert queries[0][2] == (
        end_utc + timedelta(seconds=offset, milliseconds=1)
    )


def test_current_price_tick_survives_missing_money_conversion(monkeypatch):
    xau_tick = SimpleNamespace(
        time_msc=123456,
        bid=4300.0,
        ask=4300.2,
        last=4300.1,
        flags=6,
        volume_real=2.0,
    )
    stale_eur_tick = SimpleNamespace(
        time_msc=100000,
        bid=1.14,
        ask=1.15,
    )
    fake_mt5 = SimpleNamespace(
        symbol_info_tick=lambda symbol: (
            xau_tick if symbol == "XAUUSD" else stale_eur_tick
        )
    )
    monkeypatch.setattr(main.executor, "mt5", fake_mt5)
    monkeypatch.setattr(main, "_load_shadow_money_contract", money_contract)

    observed = main._shadow_tick_snapshot()

    assert observed is not None
    assert observed.bid == 4300.0
    assert observed.money_evidence_id is None
    assert observed.money_factor("BUY", favourable=True) is None


def test_current_tick_uses_prior_conversion_when_latest_quote_is_in_future(
    monkeypatch,
):
    main._shadow_conversion_tick_cache.clear()
    xau_tick = SimpleNamespace(
        time_msc=20_000,
        bid=4300.0,
        ask=4300.2,
        last=4300.1,
        flags=6,
        volume_real=2.0,
    )
    future_eur_tick = SimpleNamespace(
        time_msc=20_005,
        bid=1.15,
        ask=1.16,
    )
    prior_eur_tick = {
        "time_msc": 19_995,
        "bid": 1.14,
        "ask": 1.15,
    }
    fake_mt5 = SimpleNamespace(
        COPY_TICKS_ALL=0,
        symbol_info_tick=lambda symbol: (
            xau_tick if symbol == "XAUUSD" else future_eur_tick
        ),
        copy_ticks_range=lambda symbol, *_args: [prior_eur_tick],
    )
    monkeypatch.setattr(main.executor, "mt5", fake_mt5)
    monkeypatch.setattr(main, "_load_shadow_money_contract", money_contract)

    observed = main._shadow_tick_snapshot()

    assert observed is not None
    assert observed.money_evidence_id
    assert observed.money_factor("BUY", favourable=True) == pytest.approx(
        100.0 / 1.15
    )


def test_conversion_cache_accepts_mt5_array_without_boolean_coercion():
    class Mt5Rows:
        def __bool__(self):
            raise ValueError("ambiguous array truth value")

        def __iter__(self):
            return iter(({
                "time_msc": 10_000,
                "bid": 1.14,
                "ask": 1.15,
            },))

    cached = main._shadow_cache_conversion_rows("EURUSD", Mt5Rows())

    assert cached == [{"time_msc": 10_000, "bid": 1.14, "ask": 1.15}]


def test_shadow_history_preserves_price_ticks_when_conversion_evidence_is_stale(
    monkeypatch,
):
    xau_rows = [
        {
            "time_msc": 20_000,
            "bid": 4300.0,
            "ask": 4300.2,
            "last": 4300.1,
            "flags": 6,
            "volume_real": 1.0,
        }
    ]
    eur_rows = [
        {
            "time_msc": 1_000,
            "bid": 1.14,
            "ask": 1.15,
        }
    ]

    def copy_ticks(symbol, *_args):
        return xau_rows if symbol == "XAUUSD" else eur_rows

    fake_mt5 = SimpleNamespace(COPY_TICKS_ALL=0, copy_ticks_range=copy_ticks)
    monkeypatch.setattr(main.executor, "mt5", fake_mt5)
    monkeypatch.setattr(main, "_load_shadow_money_contract", money_contract)

    history = main._shadow_tick_history(10_000, until_msc=21_000)

    assert isinstance(history, strategy_shadow_runtime.ShadowTickHistory)
    assert history.complete is True
    assert len(history.ticks) == 1
    assert history.ticks[0].bid == 4300.0
    assert history.ticks[0].money_evidence_id is None
    assert history.ticks[0].money_factor("BUY", favourable=True) is None


def test_shadow_history_resumes_after_full_tick_identity_without_skipping(
    monkeypatch,
):
    xau_rows = [
        {
            "time_msc": 20_000,
            "bid": 4300.0,
            "ask": 4300.2,
            "last": 4300.1,
            "flags": 6,
            "volume_real": 1.0,
        },
        {
            "time_msc": 20_000,
            "bid": 4300.1,
            "ask": 4300.3,
            "last": 4300.2,
            "flags": 6,
            "volume_real": 2.0,
        },
        {
            "time_msc": 20_001,
            "bid": 4300.2,
            "ask": 4300.4,
            "last": 4300.3,
            "flags": 6,
            "volume_real": 3.0,
        },
    ]
    fake_mt5 = SimpleNamespace(
        COPY_TICKS_ALL=0,
        copy_ticks_range=lambda *_args: xau_rows,
    )
    monkeypatch.setattr(main.executor, "mt5", fake_mt5)
    monkeypatch.setattr(
        main,
        "_load_shadow_money_contract",
        lambda: money_contract("identity"),
    )

    history = main._shadow_tick_history(
        20_000,
        until_msc=20_001,
        after_identity=(20_000, 4300.0, 4300.2, 4300.1, 6, 1.0),
    )

    assert history.complete is True
    assert [tick.identity for tick in history.ticks] == [
        (20_000, 4300.1, 4300.3, 4300.2, 6, 2.0),
        (20_001, 4300.2, 4300.4, 4300.3, 6, 3.0),
    ]


def test_shadow_history_matches_same_quote_when_mt5_flags_differ(monkeypatch):
    xau_rows = [
        {
            "time_msc": 20_000,
            "bid": 4300.0,
            "ask": 4300.2,
            "last": 4300.1,
            "flags": 134,
            "volume_real": 1.0,
        },
        {
            "time_msc": 20_001,
            "bid": 4300.1,
            "ask": 4300.3,
            "last": 4300.2,
            "flags": 134,
            "volume_real": 2.0,
        },
    ]
    monkeypatch.setattr(
        main.executor,
        "mt5",
        SimpleNamespace(
            COPY_TICKS_ALL=0,
            copy_ticks_range=lambda *_args: xau_rows,
        ),
    )
    monkeypatch.setattr(
        main,
        "_load_shadow_money_contract",
        lambda: money_contract("identity"),
    )

    history = main._shadow_tick_history(
        20_000,
        until_msc=20_001,
        after_identity=(20_000, 4300.0, 4300.2, 4300.1, 6, 1.0),
    )

    assert history.complete is True
    assert [tick.time_msc for tick in history.ticks] == [20_001]


@pytest.mark.parametrize(
    "cursor_row_flags",
    [(6, 6), (134, 150)],
    ids=["exact", "flags-fallback"],
)
def test_shadow_history_refuses_nonconsecutive_ambiguous_same_ms_cursor(
    monkeypatch,
    cursor_row_flags,
):
    first_flags, last_flags = cursor_row_flags
    xau_rows = [
        {
            "time_msc": 20_000,
            "bid": 4300.0,
            "ask": 4300.2,
            "last": 4300.1,
            "flags": first_flags,
            "volume_real": 1.0,
        },
        {
            "time_msc": 20_000,
            "bid": 4299.0,
            "ask": 4299.2,
            "last": 4299.1,
            "flags": 6,
            "volume_real": 2.0,
        },
        {
            "time_msc": 20_000,
            "bid": 4300.0,
            "ask": 4300.2,
            "last": 4300.1,
            "flags": last_flags,
            "volume_real": 1.0,
        },
        {
            "time_msc": 20_001,
            "bid": 4300.5,
            "ask": 4300.7,
            "last": 4300.6,
            "flags": 6,
            "volume_real": 3.0,
        },
    ]
    monkeypatch.setattr(
        main.executor,
        "mt5",
        SimpleNamespace(
            COPY_TICKS_ALL=0,
            copy_ticks_range=lambda *_args: xau_rows,
        ),
    )
    monkeypatch.setattr(
        main,
        "_load_shadow_money_contract",
        lambda: money_contract("identity"),
    )

    history = main._shadow_tick_history(
        20_000,
        until_msc=20_001,
        after_identity=(20_000, 4300.0, 4300.2, 4300.1, 6, 1.0),
    )

    assert history.complete is False
    assert history.ticks == ()
    assert history.blocker == "historical_tick_cursor_ambiguous"


@pytest.mark.parametrize(
    "cursor_row_flags",
    [(6, 6), (134, 150)],
    ids=["exact", "flags-fallback"],
)
def test_shadow_history_keeps_last_consecutive_equivalent_cursor_match(
    monkeypatch,
    cursor_row_flags,
):
    first_flags, last_flags = cursor_row_flags
    xau_rows = [
        {
            "time_msc": 20_000,
            "bid": 4300.0,
            "ask": 4300.2,
            "last": 4300.1,
            "flags": first_flags,
            "volume_real": 1.0,
        },
        {
            "time_msc": 20_000,
            "bid": 4300.0,
            "ask": 4300.2,
            "last": 4300.1,
            "flags": last_flags,
            "volume_real": 1.0,
        },
        {
            "time_msc": 20_001,
            "bid": 4300.5,
            "ask": 4300.7,
            "last": 4300.6,
            "flags": 6,
            "volume_real": 3.0,
        },
    ]
    monkeypatch.setattr(
        main.executor,
        "mt5",
        SimpleNamespace(
            COPY_TICKS_ALL=0,
            copy_ticks_range=lambda *_args: xau_rows,
        ),
    )
    monkeypatch.setattr(
        main,
        "_load_shadow_money_contract",
        lambda: money_contract("identity"),
    )

    history = main._shadow_tick_history(
        20_000,
        until_msc=20_001,
        after_identity=(20_000, 4300.0, 4300.2, 4300.1, 6, 1.0),
    )

    assert history.complete is True
    assert [tick.time_msc for tick in history.ticks] == [20_001]


def test_shadow_history_refuses_unknown_tick_cursor(monkeypatch):
    xau_rows = [{
        "time_msc": 20_001,
        "bid": 4300.2,
        "ask": 4300.4,
        "last": 4300.3,
        "flags": 6,
        "volume_real": 3.0,
    }]
    fake_mt5 = SimpleNamespace(
        COPY_TICKS_ALL=0,
        copy_ticks_range=lambda *_args: xau_rows,
    )
    monkeypatch.setattr(main.executor, "mt5", fake_mt5)
    monkeypatch.setattr(
        main,
        "_load_shadow_money_contract",
        lambda: money_contract("identity"),
    )

    history = main._shadow_tick_history(
        20_000,
        until_msc=20_001,
        after_identity=(20_000, 4300.0, 4300.2, 4300.1, 6, 1.0),
    )

    assert history.complete is False
    assert history.ticks == ()
    assert history.blocker == "historical_tick_cursor_unavailable"


def test_live_shadow_batch_contains_every_intermediate_broker_tick(monkeypatch):
    xau_rows = [
        {
            "time_msc": 20_000,
            "bid": 4300.0,
            "ask": 4300.2,
            "last": 4300.1,
            "flags": 6,
            "volume_real": 1.0,
        },
        {
            "time_msc": 20_001,
            "bid": 4299.5,
            "ask": 4299.7,
            "last": 4299.6,
            "flags": 6,
            "volume_real": 2.0,
        },
        {
            "time_msc": 20_002,
            "bid": 4300.5,
            "ask": 4300.7,
            "last": 4300.6,
            "flags": 6,
            "volume_real": 3.0,
        },
    ]
    fake_mt5 = SimpleNamespace(
        COPY_TICKS_ALL=0,
        copy_ticks_range=lambda *_args: xau_rows,
    )
    monkeypatch.setattr(main.executor, "mt5", fake_mt5)
    monkeypatch.setattr(
        main,
        "_load_shadow_money_contract",
        lambda: money_contract("identity"),
    )
    latest = main._shadow_tick_from_values(
        time_msc=20_002,
        bid=4300.5,
        ask=4300.7,
        last=4300.6,
        flags=6,
        volume_real=3.0,
        factors={"positive": 100.0, "negative": 100.0},
        money_evidence_id="money-latest",
    )

    batch = main._shadow_live_tick_batch(
        strategy_shadow_runtime.ShadowTickCursor(
            from_msc=20_000,
            after_identity=(20_000, 4300.0, 4300.2, 4300.1, 6, 1.0),
        ),
        latest,
    )

    assert batch.complete is True
    assert [tick.time_msc for tick in batch.ticks] == [20_001, 20_002]


def test_live_shadow_batch_accepts_proven_prefix_while_latest_is_pending(
    monkeypatch,
):
    xau_rows = [
        {
            "time_msc": 20_000,
            "bid": 4300.0,
            "ask": 4300.2,
            "last": 4300.1,
            "flags": 6,
            "volume_real": 1.0,
        },
        {
            "time_msc": 20_001,
            "bid": 4299.5,
            "ask": 4299.7,
            "last": 4299.6,
            "flags": 6,
            "volume_real": 2.0,
        },
    ]
    fake_mt5 = SimpleNamespace(
        COPY_TICKS_ALL=0,
        copy_ticks_range=lambda *_args: xau_rows,
    )
    monkeypatch.setattr(main.executor, "mt5", fake_mt5)
    monkeypatch.setattr(
        main,
        "_load_shadow_money_contract",
        lambda: money_contract("identity"),
    )
    latest = main._shadow_tick_from_values(
        time_msc=20_002,
        bid=4300.5,
        ask=4300.7,
        last=4300.6,
        flags=6,
        volume_real=3.0,
        factors={"positive": 100.0, "negative": 100.0},
        money_evidence_id="money-latest",
    )

    batch = main._shadow_live_tick_batch(
        strategy_shadow_runtime.ShadowTickCursor(
            from_msc=20_000,
            after_identity=(20_000, 4300.0, 4300.2, 4300.1, 6, 1.0),
        ),
        latest,
    )

    assert batch.complete is True
    assert [tick.time_msc for tick in batch.ticks] == [20_001]
    assert batch.pending_reason == "live_archive_tail_pending"


def test_live_shadow_batch_waits_when_only_cursor_is_archived(monkeypatch):
    xau_rows = [{
        "time_msc": 20_000,
        "bid": 4300.0,
        "ask": 4300.2,
        "last": 4300.1,
        "flags": 6,
        "volume_real": 1.0,
    }]
    fake_mt5 = SimpleNamespace(
        COPY_TICKS_ALL=0,
        copy_ticks_range=lambda *_args: xau_rows,
    )
    monkeypatch.setattr(main.executor, "mt5", fake_mt5)
    monkeypatch.setattr(
        main,
        "_load_shadow_money_contract",
        lambda: money_contract("identity"),
    )
    latest = main._shadow_tick_from_values(
        time_msc=20_001,
        bid=4300.5,
        ask=4300.7,
        last=4300.6,
        flags=6,
        volume_real=3.0,
        factors={"positive": 100.0, "negative": 100.0},
        money_evidence_id="money-latest",
    )

    batch = main._shadow_live_tick_batch(
        strategy_shadow_runtime.ShadowTickCursor(
            from_msc=20_000,
            after_identity=(20_000, 4300.0, 4300.2, 4300.1, 6, 1.0),
        ),
        latest,
    )

    assert batch.complete is True
    assert batch.ticks == ()
    assert batch.pending_reason == "live_archive_tail_pending"


def test_shadow_journal_loader_ignores_unrelated_events(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join([
            json.dumps({"sig": "bot", "ev": "heartbeat"}),
            json.dumps({
                "sig": "canal1_1",
                "ev": "strategy_shadow_registered",
                "candidate_id": "dubai_balanced_v1",
            }),
            "not-json",
        ]),
        encoding="utf-8",
    )

    assert main._load_shadow_journal_records(path) == [{
        "sig": "canal1_1",
        "ev": "strategy_shadow_registered",
        "candidate_id": "dubai_balanced_v1",
    }]


def test_candidate_background_loops_add_shadow_without_removing_live(
    monkeypatch,
):
    monkeypatch.setattr(main.config, "STRATEGY_C2_GOLD_NOW_555_ENABLED", True)
    monkeypatch.setattr(main.config, "STRATEGY_SHADOW_ENABLED", True)

    loops = main._candidate_background_loops()

    assert main.gold_555_entry_watch_loop in loops
    assert main._strategy_shadow_loop in loops


@pytest.mark.asyncio
async def test_unexpected_shadow_tick_failure_disables_only_shadow_runtime(
    monkeypatch,
):
    events = []

    class FailingRuntime:
        async def process_tick(self, _tick):
            raise RuntimeError("shadow journal unavailable")

    runtime = FailingRuntime()
    strategy_shadow_runtime.install_runtime(runtime)
    monkeypatch.setattr(
        main.journal,
        "event",
        lambda signal_id, event, **fields: events.append(
            (signal_id, event, fields)
        ),
    )

    processed = await main._process_strategy_shadow_tick(runtime, object())

    assert processed is False
    assert strategy_shadow_runtime.installed_runtime() is None
    assert events == [(
        "bot",
        "strategy_shadow_runtime_disabled",
        {
            "operation": "process_tick",
            "error_type": "RuntimeError",
            "error": "shadow journal unavailable",
        },
    )]


@pytest.mark.asyncio
async def test_successful_shadow_tick_reports_processed():
    calls = []

    class HealthyRuntime:
        async def process_tick(self, tick):
            calls.append(tick)

    runtime = HealthyRuntime()
    observed = object()

    processed = await main._process_strategy_shadow_tick(runtime, observed)

    assert processed is True
    assert calls == [observed]


@pytest.mark.asyncio
async def test_unexpected_shadow_tick_batch_failure_disables_only_shadow_runtime(
    monkeypatch,
):
    events = []

    class FailingRuntime:
        async def process_tick_batch(self, _cursor, _history):
            raise RuntimeError("shadow batch journal unavailable")

    runtime = FailingRuntime()
    strategy_shadow_runtime.install_runtime(runtime)
    monkeypatch.setattr(
        main.journal,
        "event",
        lambda signal_id, event, **fields: events.append(
            (signal_id, event, fields)
        ),
    )

    processed = await main._process_strategy_shadow_tick_batch(
        runtime,
        object(),
        object(),
    )

    assert processed is False
    assert strategy_shadow_runtime.installed_runtime() is None
    assert events == [(
        "bot",
        "strategy_shadow_runtime_disabled",
        {
            "operation": "process_tick_batch",
            "error_type": "RuntimeError",
            "error": "shadow batch journal unavailable",
        },
    )]


@pytest.mark.asyncio
async def test_successful_shadow_tick_batch_reports_processed_once():
    calls = []

    class HealthyRuntime:
        async def process_tick_batch(self, cursor, history):
            calls.append((cursor, history))

    runtime = HealthyRuntime()
    cursor = object()
    history = object()

    processed = await main._process_strategy_shadow_tick_batch(
        runtime,
        cursor,
        history,
    )

    assert processed is True
    assert calls == [(cursor, history)]


@pytest.mark.asyncio
async def test_tick_continuity_pause_keeps_shadow_runtime_installed(
    monkeypatch,
):
    events = []
    sleep_calls = []
    calls = 0
    cursor = (20_000, 4300.0, 4300.2, 4300.1, 6, 1.0)
    latest = main._shadow_tick_from_values(
        time_msc=20_001,
        bid=4300.5,
        ask=4300.7,
        last=4300.6,
        flags=6,
        volume_real=2.0,
        factors={"positive": 100.0, "negative": 100.0},
        money_evidence_id="money-latest",
    )

    class Runtime:
        def active_tick_cursor(self):
            return strategy_shadow_runtime.ShadowTickCursor(
                from_msc=cursor[0],
                after_identity=cursor,
            )

    runtime = Runtime()
    strategy_shadow_runtime.install_runtime(runtime)
    monkeypatch.setattr(main.config, "STRATEGY_SHADOW_ENABLED", True)
    async def fake_sleep(delay):
        sleep_calls.append(delay)

    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main, "_shadow_tick_snapshot", lambda: latest)
    monkeypatch.setattr(
        main.journal,
        "event",
        lambda signal_id, event, **fields: events.append(
            (signal_id, event, fields)
        ),
    )

    def incomplete_batch(_cursor, _latest):
        nonlocal calls
        calls += 1
        if calls == 3:
            monkeypatch.setattr(
                main.config,
                "STRATEGY_SHADOW_ENABLED",
                False,
            )
        return strategy_shadow_runtime.ShadowTickHistory(
            ticks=(),
            complete=False,
            evidence_id=f"missing-{calls}",
        )

    monkeypatch.setattr(main, "_shadow_live_tick_batch", incomplete_batch)

    await main._strategy_shadow_loop(interval_s=0.01)

    assert calls == 3
    assert strategy_shadow_runtime.installed_runtime() is runtime
    assert max(sleep_calls) >= 1.0
    assert events == [(
        "bot",
        "strategy_shadow_tick_continuity_waiting",
        {
            "consecutive_failures": 3,
            "evidence_id": "missing-3",
            "blocker": None,
            "cursor_from_msc": 20_000,
            "cursor_after_identity": list(cursor),
            "latest_identity": list(latest.identity),
        },
    )]


@pytest.mark.asyncio
async def test_sustained_archive_tail_delay_is_recorded_without_pausing_prefix(
    monkeypatch,
):
    events = []
    sleep_calls = []
    processed_batches = []
    calls = 0
    cursor = (20_000, 4300.0, 4300.2, 4300.1, 6, 1.0)
    latest = main._shadow_tick_from_values(
        time_msc=20_001,
        bid=4300.5,
        ask=4300.7,
        last=4300.6,
        flags=6,
        volume_real=2.0,
        factors={"positive": 100.0, "negative": 100.0},
        money_evidence_id="money-latest",
    )

    class Runtime:
        def active_tick_cursor(self):
            return strategy_shadow_runtime.ShadowTickCursor(
                from_msc=cursor[0],
                after_identity=cursor,
            )

        async def process_tick_batch(self, queried_cursor, history):
            assert history.ticks == ()
            processed_batches.append((queried_cursor, history))

    runtime = Runtime()
    strategy_shadow_runtime.install_runtime(runtime)
    monkeypatch.setattr(main.config, "STRATEGY_SHADOW_ENABLED", True)
    async def fake_sleep(delay):
        sleep_calls.append(delay)

    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main, "_shadow_tick_snapshot", lambda: latest)
    monkeypatch.setattr(
        main.journal,
        "event",
        lambda signal_id, event, **fields: events.append(
            (signal_id, event, fields)
        ),
    )

    def pending_batch(_cursor, _latest):
        nonlocal calls
        calls += 1
        if calls == 3:
            monkeypatch.setattr(main.config, "STRATEGY_SHADOW_ENABLED", False)
        return strategy_shadow_runtime.ShadowTickHistory(
            ticks=(),
            complete=True,
            evidence_id=f"pending-{calls}",
            pending_reason="live_archive_tail_pending",
        )

    monkeypatch.setattr(main, "_shadow_live_tick_batch", pending_batch)

    await main._strategy_shadow_loop(interval_s=0.01)

    assert calls == 3
    assert len(processed_batches) == 3
    assert max(sleep_calls) >= 1.0
    assert events == [(
        "bot",
        "strategy_shadow_tick_archive_tail_waiting",
        {
            "consecutive_waits": 3,
            "evidence_id": "pending-3",
            "reason": "live_archive_tail_pending",
            "cursor_from_msc": 20_000,
            "cursor_after_identity": list(cursor),
            "latest_identity": list(latest.identity),
            "batch_tick_count": 0,
        },
    )]


@pytest.mark.asyncio
async def test_tick_continuity_resumes_from_same_cursor(monkeypatch):
    events = []
    processed = []
    calls = 0
    cursor = (20_000, 4300.0, 4300.2, 4300.1, 6, 1.0)
    latest = main._shadow_tick_from_values(
        time_msc=20_001,
        bid=4300.5,
        ask=4300.7,
        last=4300.6,
        flags=6,
        volume_real=2.0,
        factors={"positive": 100.0, "negative": 100.0},
        money_evidence_id="money-latest",
    )

    class Runtime:
        def active_tick_cursor(self):
            return strategy_shadow_runtime.ShadowTickCursor(
                from_msc=cursor[0],
                after_identity=cursor,
            )

        async def process_tick_batch(self, queried_cursor, history):
            assert queried_cursor.after_identity == cursor
            processed.extend(tick.identity for tick in history.ticks)
            monkeypatch.setattr(
                main.config,
                "STRATEGY_SHADOW_ENABLED",
                False,
            )

        async def process_tick(self, _tick):
            monkeypatch.setattr(main.config, "STRATEGY_SHADOW_ENABLED", False)
            raise AssertionError("loop must process one cursor-bound batch")

    runtime = Runtime()
    strategy_shadow_runtime.install_runtime(runtime)
    monkeypatch.setattr(main.config, "STRATEGY_SHADOW_ENABLED", True)
    async def fake_sleep(_delay):
        return None

    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main, "_shadow_tick_snapshot", lambda: latest)
    monkeypatch.setattr(
        main.journal,
        "event",
        lambda signal_id, event, **fields: events.append(
            (signal_id, event, fields)
        ),
    )

    def recovering_batch(_cursor, _latest):
        nonlocal calls
        calls += 1
        if calls <= 3:
            return strategy_shadow_runtime.ShadowTickHistory(
                ticks=(),
                complete=False,
                evidence_id=f"missing-{calls}",
            )
        return strategy_shadow_runtime.ShadowTickHistory(
            ticks=(latest,),
            complete=True,
            evidence_id="recovered-4",
        )

    monkeypatch.setattr(main, "_shadow_live_tick_batch", recovering_batch)

    await main._strategy_shadow_loop(interval_s=0.01)

    assert calls == 4
    assert processed == [latest.identity]
    assert strategy_shadow_runtime.installed_runtime() is runtime
    assert [event for _, event, _ in events] == [
        "strategy_shadow_tick_continuity_waiting",
        "strategy_shadow_tick_continuity_resumed",
    ]
    assert events[-1][2] == {
        "consecutive_failures": 3,
        "evidence_id": "recovered-4",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker", [
    "historical_tick_cursor_unavailable",
    "historical_tick_cursor_ambiguous",
])
async def test_historical_cursor_budget_quarantines_gap_then_processes_younger_cohort(
    monkeypatch,
    blocker,
):
    events = []
    processed = []
    quarantine_calls = []
    batch_calls = 0
    old_identity = (20_000, 4300.0, 4300.2, 4300.1, 6, 1.0)
    younger_identity = (25_000, 4301.0, 4301.2, 4301.1, 6, 2.0)
    latest = main._shadow_tick_from_values(
        time_msc=30_000,
        bid=4302.0,
        ask=4302.2,
        last=4302.1,
        flags=6,
        volume_real=3.0,
        factors={"positive": 100.0, "negative": 100.0},
        money_evidence_id="money-latest",
    )

    class Runtime:
        def __init__(self):
            self.cursor = strategy_shadow_runtime.ShadowTickCursor(
                from_msc=old_identity[0],
                after_identity=old_identity,
            )

        def active_tick_cursor(self):
            return self.cursor

        async def quarantine_tick_cursor(
            self,
            cursor,
            *,
            history,
            consecutive_failures,
        ):
            quarantine_calls.append((cursor, history, consecutive_failures))
            self.cursor = strategy_shadow_runtime.ShadowTickCursor(
                from_msc=younger_identity[0],
                after_identity=younger_identity,
            )
            return ("quarantined-old-cohort",)

        async def process_tick_batch(self, queried_cursor, history):
            assert queried_cursor.after_identity == younger_identity
            processed.extend(tick.identity for tick in history.ticks)
            monkeypatch.setattr(main.config, "STRATEGY_SHADOW_ENABLED", False)

        async def process_tick(self, _tick):
            monkeypatch.setattr(main.config, "STRATEGY_SHADOW_ENABLED", False)
            raise AssertionError("loop must process one cursor-bound batch")

    runtime = Runtime()
    strategy_shadow_runtime.install_runtime(runtime)
    monkeypatch.setattr(main.config, "STRATEGY_SHADOW_ENABLED", True)
    monkeypatch.setattr(main.asyncio, "sleep", lambda _delay: _async_none())
    monkeypatch.setattr(main, "_shadow_tick_snapshot", lambda: latest)
    monkeypatch.setattr(
        main.journal,
        "event",
        lambda signal_id, event, **fields: events.append(
            (signal_id, event, fields)
        ),
    )

    def history_for_cursor(cursor, _latest):
        nonlocal batch_calls
        batch_calls += 1
        if cursor.after_identity == old_identity:
            if batch_calls == 61:
                monkeypatch.setattr(
                    main.config, "STRATEGY_SHADOW_ENABLED", False,
                )
            return strategy_shadow_runtime.ShadowTickHistory(
                ticks=(),
                complete=False,
                evidence_id=f"missing-{batch_calls}",
                blocker=blocker,
            )
        assert cursor.after_identity == younger_identity
        return strategy_shadow_runtime.ShadowTickHistory(
            ticks=(latest,),
            complete=True,
            evidence_id="younger-history",
        )

    monkeypatch.setattr(main, "_shadow_live_tick_batch", history_for_cursor)

    await main._strategy_shadow_loop(interval_s=0.01)

    assert batch_calls == 61
    assert len(quarantine_calls) == 1
    quarantined_cursor, quarantined_history, failures = quarantine_calls[0]
    assert quarantined_cursor.after_identity == old_identity
    assert quarantined_history.evidence_id == "missing-60"
    assert quarantined_history.blocker == blocker
    assert failures == 60
    assert processed == [latest.identity]
    assert "strategy_shadow_tick_continuity_resumed" not in {
        event for _, event, _ in events
    }


@pytest.mark.asyncio
async def test_missing_cursor_recovery_before_budget_does_not_quarantine(
    monkeypatch,
):
    processed = []
    quarantine_calls = []
    batch_calls = 0
    cursor_identity = (20_000, 4300.0, 4300.2, 4300.1, 6, 1.0)
    latest = main._shadow_tick_from_values(
        time_msc=20_001,
        bid=4300.5,
        ask=4300.7,
        last=4300.6,
        flags=6,
        volume_real=2.0,
        factors={"positive": 100.0, "negative": 100.0},
        money_evidence_id="money-latest",
    )

    class Runtime:
        def active_tick_cursor(self):
            return strategy_shadow_runtime.ShadowTickCursor(
                from_msc=cursor_identity[0],
                after_identity=cursor_identity,
            )

        async def quarantine_tick_cursor(self, *args, **kwargs):
            quarantine_calls.append((args, kwargs))
            return ()

        async def process_tick_batch(self, queried_cursor, history):
            assert queried_cursor.after_identity == cursor_identity
            processed.extend(tick.identity for tick in history.ticks)
            monkeypatch.setattr(main.config, "STRATEGY_SHADOW_ENABLED", False)

        async def process_tick(self, _tick):
            monkeypatch.setattr(main.config, "STRATEGY_SHADOW_ENABLED", False)
            raise AssertionError("loop must process one cursor-bound batch")

    runtime = Runtime()
    strategy_shadow_runtime.install_runtime(runtime)
    monkeypatch.setattr(main.config, "STRATEGY_SHADOW_ENABLED", True)
    monkeypatch.setattr(main.asyncio, "sleep", lambda _delay: _async_none())
    monkeypatch.setattr(main, "_shadow_tick_snapshot", lambda: latest)
    monkeypatch.setattr(main.journal, "event", lambda *_args, **_kwargs: None)

    def transient_history(_cursor, _latest):
        nonlocal batch_calls
        batch_calls += 1
        if batch_calls < 60:
            return strategy_shadow_runtime.ShadowTickHistory(
                ticks=(),
                complete=False,
                evidence_id=f"transient-{batch_calls}",
                blocker="historical_tick_cursor_unavailable",
            )
        return strategy_shadow_runtime.ShadowTickHistory(
            ticks=(latest,),
            complete=True,
            evidence_id="recovered-60",
        )

    monkeypatch.setattr(main, "_shadow_live_tick_batch", transient_history)

    await main._strategy_shadow_loop(interval_s=0.01)

    assert batch_calls == 60
    assert quarantine_calls == []
    assert processed == [latest.identity]


@pytest.mark.asyncio
async def test_missing_cursor_budget_is_independent_and_resumes_after_switch(
    monkeypatch,
):
    quarantine_calls = []
    batch_calls = 0
    first_identity = (20_000, 4300.0, 4300.2, 4300.1, 6, 1.0)
    second_identity = (20_000, 4299.9, 4300.1, 4300.0, 6, 2.0)
    latest = main._shadow_tick_from_values(
        time_msc=20_001,
        bid=4300.5,
        ask=4300.7,
        last=4300.6,
        flags=6,
        volume_real=3.0,
        factors={"positive": 100.0, "negative": 100.0},
        money_evidence_id="money-latest",
    )

    class Runtime:
        def __init__(self):
            self.identity = first_identity

        def active_tick_cursor(self):
            return strategy_shadow_runtime.ShadowTickCursor(
                from_msc=self.identity[0],
                after_identity=self.identity,
            )

        async def quarantine_tick_cursor(
            self,
            cursor,
            *,
            history,
            consecutive_failures,
        ):
            quarantine_calls.append((cursor, history, consecutive_failures))
            return ()

    runtime = Runtime()
    strategy_shadow_runtime.install_runtime(runtime)
    monkeypatch.setattr(main.config, "STRATEGY_SHADOW_ENABLED", True)
    monkeypatch.setattr(main.asyncio, "sleep", lambda _delay: _async_none())
    monkeypatch.setattr(main, "_shadow_tick_snapshot", lambda: latest)
    monkeypatch.setattr(main.journal, "event", lambda *_args, **_kwargs: None)

    def missing_history(_cursor, _latest):
        nonlocal batch_calls
        batch_calls += 1
        if batch_calls == 59:
            runtime.identity = second_identity
        if batch_calls == 60:
            assert quarantine_calls == []
            runtime.identity = first_identity
        if batch_calls == 61:
            monkeypatch.setattr(main.config, "STRATEGY_SHADOW_ENABLED", False)
        return strategy_shadow_runtime.ShadowTickHistory(
            ticks=(),
            complete=False,
            evidence_id=f"same-ms-{batch_calls}",
            blocker="historical_tick_cursor_unavailable",
        )

    monkeypatch.setattr(main, "_shadow_live_tick_batch", missing_history)

    await main._strategy_shadow_loop(interval_s=0.01)

    assert batch_calls == 61
    assert len(quarantine_calls) == 1
    quarantined_cursor, quarantined_history, failures = quarantine_calls[0]
    assert quarantined_cursor.after_identity == first_identity
    assert quarantined_history.blocker == "historical_tick_cursor_unavailable"
    assert failures == 60


@pytest.mark.asyncio
async def test_round_robin_missing_same_ms_cursors_each_reach_quarantine_budget(
    monkeypatch,
):
    runtime = strategy_shadow_runtime.ShadowRuntime()

    async def register(signal_id, registered_tick_msc):
        await runtime.register_signal(
            channel="canal1",
            signal_id=signal_id,
            source_message_id=int(signal_id.rsplit("_", 1)[-1]),
            direction="BUY",
            registered_at_utc="1970-01-01T00:00:00+00:00",
            registered_tick_msc=registered_tick_msc,
        )

    await register("canal1_101", 10)
    await register("canal1_102", 20)
    await register("canal1_103", 30)

    cursor_a_tick = main._shadow_tick_from_values(
        time_msc=100,
        bid=4300.0,
        ask=4300.2,
        last=4300.1,
        flags=6,
        volume_real=1.0,
        factors={"positive": 100.0, "negative": 100.0},
        money_evidence_id="money-a",
    )
    cursor_b_tick = main._shadow_tick_from_values(
        time_msc=100,
        bid=4299.0,
        ask=4299.2,
        last=4299.1,
        flags=6,
        volume_real=2.0,
        factors={"positive": 100.0, "negative": 100.0},
        money_evidence_id="money-b",
    )
    cursor_c_tick = main._shadow_tick_from_values(
        time_msc=200,
        bid=4301.0,
        ask=4301.2,
        last=4301.1,
        flags=6,
        volume_real=3.0,
        factors={"positive": 100.0, "negative": 100.0},
        money_evidence_id="money-c",
    )
    latest = main._shadow_tick_from_values(
        time_msc=300,
        bid=4302.0,
        ask=4302.2,
        last=4302.1,
        flags=6,
        volume_real=4.0,
        factors={"positive": 100.0, "negative": 100.0},
        money_evidence_id="money-latest",
    )

    async def seed_cursor(from_msc, observed, evidence_id):
        await runtime.process_tick_batch(
            strategy_shadow_runtime.ShadowTickCursor(from_msc, None),
            strategy_shadow_runtime.ShadowTickHistory(
                ticks=(observed,),
                complete=True,
                evidence_id=evidence_id,
            ),
        )

    await seed_cursor(10, cursor_a_tick, "seed-a")
    await seed_cursor(20, cursor_b_tick, "seed-b")
    await seed_cursor(30, cursor_c_tick, "seed-c")

    query_counts = {"a": 0, "b": 0, "c": 0}
    total_queries = 0
    strategy_shadow_runtime.install_runtime(runtime)
    monkeypatch.setattr(main.config, "STRATEGY_SHADOW_ENABLED", True)
    monkeypatch.setattr(main.asyncio, "sleep", lambda _delay: _async_none())
    monkeypatch.setattr(main, "_shadow_tick_snapshot", lambda: latest)
    monkeypatch.setattr(main.journal, "event", lambda *_args, **_kwargs: None)

    def history_for_cursor(cursor, _latest):
        nonlocal total_queries
        total_queries += 1
        if cursor.after_identity == cursor_a_tick.identity:
            cohort = "a"
        elif cursor.after_identity == cursor_b_tick.identity:
            cohort = "b"
        elif cursor.after_identity == cursor_c_tick.identity:
            query_counts["c"] += 1
            monkeypatch.setattr(
                main.config, "STRATEGY_SHADOW_ENABLED", False,
            )
            return strategy_shadow_runtime.ShadowTickHistory(
                ticks=(latest,),
                complete=True,
                evidence_id="younger-c-valid",
            )
        else:
            pytest.fail(f"unexpected round-robin cursor: {cursor}")
        query_counts[cohort] += 1
        if total_queries >= 130:
            monkeypatch.setattr(
                main.config, "STRATEGY_SHADOW_ENABLED", False,
            )
        return strategy_shadow_runtime.ShadowTickHistory(
            ticks=(),
            complete=False,
            evidence_id=f"missing-{cohort}-{query_counts[cohort]}",
            blocker="historical_tick_cursor_unavailable",
        )

    monkeypatch.setattr(main, "_shadow_live_tick_batch", history_for_cursor)

    await main._strategy_shadow_loop(interval_s=0.01)

    assert query_counts == {"a": 60, "b": 60, "c": 1}
    for signal_id in ("canal1_101", "canal1_102"):
        states = runtime.states_for_signal(signal_id)
        assert states
        assert all(state.status == "incomplete" for state in states)
        assert all(
            "historical_tick_cursor_unavailable" in state.evidence_blockers
            for state in states
        )
    younger_states = runtime.states_for_signal("canal1_103")
    assert younger_states
    assert all(
        state.last_tick_identity == latest.identity
        for state in younger_states
    )


@pytest.mark.asyncio
async def test_generic_history_failure_never_quarantines_cursor(monkeypatch):
    quarantine_calls = []
    batch_calls = 0
    cursor_identity = (20_000, 4300.0, 4300.2, 4300.1, 6, 1.0)
    latest = main._shadow_tick_from_values(
        time_msc=20_001,
        bid=4300.5,
        ask=4300.7,
        last=4300.6,
        flags=6,
        volume_real=2.0,
        factors={"positive": 100.0, "negative": 100.0},
        money_evidence_id="money-latest",
    )

    class Runtime:
        def active_tick_cursor(self):
            return strategy_shadow_runtime.ShadowTickCursor(
                from_msc=cursor_identity[0],
                after_identity=cursor_identity,
            )

        async def quarantine_tick_cursor(self, *args, **kwargs):
            quarantine_calls.append((args, kwargs))
            return ()

    strategy_shadow_runtime.install_runtime(Runtime())
    monkeypatch.setattr(main.config, "STRATEGY_SHADOW_ENABLED", True)
    monkeypatch.setattr(main.asyncio, "sleep", lambda _delay: _async_none())
    monkeypatch.setattr(main, "_shadow_tick_snapshot", lambda: latest)
    monkeypatch.setattr(main.journal, "event", lambda *_args, **_kwargs: None)

    def unavailable_history(_cursor, _latest):
        nonlocal batch_calls
        batch_calls += 1
        if batch_calls == 60:
            monkeypatch.setattr(main.config, "STRATEGY_SHADOW_ENABLED", False)
        return strategy_shadow_runtime.ShadowTickHistory(
            ticks=(),
            complete=False,
            evidence_id=f"archive-error-{batch_calls}",
            blocker="tick_history_unavailable",
        )

    monkeypatch.setattr(main, "_shadow_live_tick_batch", unavailable_history)

    await main._strategy_shadow_loop(interval_s=0.01)

    assert batch_calls == 60
    assert quarantine_calls == []


@pytest.mark.asyncio
async def test_quarantine_failure_disables_only_shadow_runtime(monkeypatch):
    events = []
    batch_calls = 0
    cursor_identity = (20_000, 4300.0, 4300.2, 4300.1, 6, 1.0)
    latest = main._shadow_tick_from_values(
        time_msc=20_001,
        bid=4300.5,
        ask=4300.7,
        last=4300.6,
        flags=6,
        volume_real=2.0,
        factors={"positive": 100.0, "negative": 100.0},
        money_evidence_id="money-latest",
    )

    class Runtime:
        def active_tick_cursor(self):
            return strategy_shadow_runtime.ShadowTickCursor(
                from_msc=cursor_identity[0],
                after_identity=cursor_identity,
            )

        async def quarantine_tick_cursor(self, *args, **kwargs):
            del args, kwargs
            monkeypatch.setattr(main.config, "STRATEGY_SHADOW_ENABLED", False)
            raise RuntimeError("shadow quarantine journal unavailable")

    runtime = Runtime()
    strategy_shadow_runtime.install_runtime(runtime)
    monkeypatch.setattr(main.config, "STRATEGY_SHADOW_ENABLED", True)
    monkeypatch.setattr(main.asyncio, "sleep", lambda _delay: _async_none())
    monkeypatch.setattr(main, "_shadow_tick_snapshot", lambda: latest)
    monkeypatch.setattr(
        main.journal,
        "event",
        lambda signal_id, event, **fields: events.append(
            (signal_id, event, fields)
        ),
    )

    def missing_history(_cursor, _latest):
        nonlocal batch_calls
        batch_calls += 1
        if batch_calls == 61:
            monkeypatch.setattr(main.config, "STRATEGY_SHADOW_ENABLED", False)
        return strategy_shadow_runtime.ShadowTickHistory(
            ticks=(),
            complete=False,
            evidence_id=f"missing-{batch_calls}",
            blocker="historical_tick_cursor_unavailable",
        )

    monkeypatch.setattr(main, "_shadow_live_tick_batch", missing_history)

    await main._strategy_shadow_loop(interval_s=0.01)

    assert batch_calls == 60
    assert strategy_shadow_runtime.installed_runtime() is None
    assert events[-1] == (
        "bot",
        "strategy_shadow_runtime_disabled",
        {
            "operation": "quarantine_tick_cursor",
            "error_type": "RuntimeError",
            "error": "shadow quarantine journal unavailable",
        },
    )


@pytest.mark.asyncio
async def test_live_loop_does_not_requery_one_quote_only_because_flags_differ(
        monkeypatch):
    archived = (20_000, 4300.0, 4300.2, 4300.1, 134, 1.0)
    latest = main._shadow_tick_from_values(
        time_msc=20_000,
        bid=4300.0,
        ask=4300.2,
        last=4300.1,
        flags=6,
        volume_real=1.0,
        factors={"positive": 100.0, "negative": 100.0},
        money_evidence_id="money-latest",
    )

    class Runtime:
        def active_tick_cursor(self):
            return strategy_shadow_runtime.ShadowTickCursor(
                from_msc=archived[0],
                after_identity=archived,
            )

    strategy_shadow_runtime.install_runtime(Runtime())
    monkeypatch.setattr(main.config, "STRATEGY_SHADOW_ENABLED", True)

    def snapshot():
        monkeypatch.setattr(main.config, "STRATEGY_SHADOW_ENABLED", False)
        return latest

    monkeypatch.setattr(main, "_shadow_tick_snapshot", snapshot)
    monkeypatch.setattr(
        main,
        "_shadow_live_tick_batch",
        lambda *_args: pytest.fail("same market quote was queried twice"),
    )

    await main._strategy_shadow_loop(interval_s=0.01)


@pytest.mark.asyncio
async def test_invalid_shadow_configuration_disables_only_shadows(
    monkeypatch,
    tmp_path,
):
    events = []
    monkeypatch.setattr(main.config, "STRATEGY_SHADOW_ENABLED", True)
    monkeypatch.setattr(main.config, "STRATEGY_SHADOW_CHECKPOINT_SECONDS", 0)
    monkeypatch.setattr(
        main.journal,
        "event",
        lambda signal_id, event, **fields: events.append(
            (signal_id, event, fields)
        ),
    )

    restored = await main._initialize_strategy_shadows(
        tmp_path / "missing.jsonl"
    )

    assert restored == 0
    assert strategy_shadow_runtime.installed_runtime() is None
    assert events[-1][1] == "strategy_shadow_startup_disabled"


@pytest.mark.asyncio
async def test_shadow_runtime_uses_the_configured_live_control(
    monkeypatch,
    tmp_path,
):
    events = []
    monkeypatch.setattr(main.config, "STRATEGY_SHADOW_ENABLED", True)
    monkeypatch.setattr(main.config, "STRATEGY_SHADOW_CHECKPOINT_SECONDS", 300)
    monkeypatch.setattr(
        main.config, "STRATEGY_SHADOW_SLOWDOWN_THRESHOLD_MS", 20.0,
    )
    monkeypatch.setattr(main.config, "STRATEGY_C1_BALANCED_V1_ENABLED", True)
    monkeypatch.setattr(main.config, "GOLD_NOW_LIVE_POLICY", "c490")
    monkeypatch.setattr(
        main.journal,
        "event",
        lambda signal_id, event, **fields: events.append(
            (signal_id, event, fields)
        ),
    )

    restored = await main._initialize_strategy_shadows(
        tmp_path / "missing.jsonl"
    )

    assert restored == 0
    startup = next(row for row in events if row[1] == "strategy_shadow_runtime_started")
    assert startup[2]["controls"] == {
        "canal1": "dubai_balanced_v1",
        "canal2": "gold_now_c490_v1",
    }
    manifest = startup[2]["catalog_manifest"]
    assert manifest["schema_version"] == 1
    assert len(manifest["policies"]) == 6
    assert len(manifest["manifest_hash"]) == 64
    strategy_shadow_runtime.install_runtime(None)


@pytest.mark.asyncio
async def test_unsupported_live_control_disables_only_shadow_runtime(
    monkeypatch,
    tmp_path,
):
    events = []
    monkeypatch.setattr(main.config, "STRATEGY_SHADOW_ENABLED", True)
    monkeypatch.setattr(main.config, "STRATEGY_SHADOW_CHECKPOINT_SECONDS", 300)
    monkeypatch.setattr(
        main.config, "STRATEGY_SHADOW_SLOWDOWN_THRESHOLD_MS", 20.0,
    )
    monkeypatch.setattr(main.config, "STRATEGY_C1_BALANCED_V1_ENABLED", True)
    monkeypatch.setattr(main.config, "GOLD_NOW_LIVE_POLICY", "legacy")
    monkeypatch.setattr(
        main.journal,
        "event",
        lambda signal_id, event, **fields: events.append(
            (signal_id, event, fields)
        ),
    )

    restored = await main._initialize_strategy_shadows(
        tmp_path / "missing.jsonl"
    )

    assert restored == 0
    assert strategy_shadow_runtime.installed_runtime() is None
    assert events[-1][1] == "strategy_shadow_startup_disabled"
