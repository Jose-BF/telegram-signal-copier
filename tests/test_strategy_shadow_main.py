from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import main
import strategy_shadow_runtime


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


def test_shadow_history_marks_incomplete_when_conversion_evidence_is_stale(
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
    assert history.complete is False
    assert history.ticks == ()


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
        (20_000, 4300.0, 4300.2, 4300.1, 6, 1.0),
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
        (20_000, 4300.0, 4300.2, 4300.1, 6, 1.0),
        latest,
    )

    assert batch.complete is True
    assert [tick.time_msc for tick in batch.ticks] == [20_001]


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
        (20_000, 4300.0, 4300.2, 4300.1, 6, 1.0),
        latest,
    )

    assert batch.complete is True
    assert batch.ticks == ()


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
async def test_tick_continuity_pause_keeps_shadow_runtime_installed(
    monkeypatch,
):
    events = []
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
        def earliest_active_tick_identity(self):
            return cursor

    runtime = Runtime()
    strategy_shadow_runtime.install_runtime(runtime)
    monkeypatch.setattr(main.config, "STRATEGY_SHADOW_ENABLED", True)
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
    assert events == [(
        "bot",
        "strategy_shadow_tick_continuity_waiting",
        {
            "consecutive_failures": 3,
            "evidence_id": "missing-3",
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
        def earliest_active_tick_identity(self):
            return cursor

        async def process_tick(self, tick):
            processed.append(tick.identity)
            monkeypatch.setattr(
                main.config,
                "STRATEGY_SHADOW_ENABLED",
                False,
            )

    runtime = Runtime()
    strategy_shadow_runtime.install_runtime(runtime)
    monkeypatch.setattr(main.config, "STRATEGY_SHADOW_ENABLED", True)
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
