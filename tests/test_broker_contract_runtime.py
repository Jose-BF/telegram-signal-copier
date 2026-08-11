import json
import asyncio

import main


def _snapshot(captured_at, fingerprint):
    specification = {
        "swap_mode": 1,
        "swap_long": -75.82,
        "swap_short": 27.41,
        "swap_rollover3days": 3,
        "point": 0.01,
        "contract_size": 100.0,
        "currency_profit": "USD",
        "weekday_multipliers": {
            "sunday": 0.0,
            "monday": 1.0,
            "tuesday": 1.0,
            "wednesday": 3.0,
            "thursday": 1.0,
            "friday": 1.0,
            "saturday": 0.0,
        },
    }
    return {
        "captured_at_utc": captured_at,
        "account_server": "VantageMarkets-Demo",
        "account_fingerprint": "a" * 64,
        "instrument_symbol": "XAUUSD",
        "time_evidence": {
            "source": "mql5_service_v1",
            "evidence_sha256": "b" * 64,
            "utc_offset_seconds": 10800,
        },
        "specification": specification,
        "specification_sha256": (
            main.broker_contract.specification_sha256(specification)
        ),
    }


def _contract(*snapshots):
    return {
        "schema_version": 2,
        "account": {
            "server": "VantageMarkets-Demo",
            "fingerprint": "a" * 64,
            "currency": "EUR",
            "currency_digits": 2,
        },
        "instrument": {
            "symbol": "XAUUSD",
            "trade_calc_mode": 4,
            "contract_size": 100.0,
            "tick_size": 0.01,
            "currency_profit": "USD",
        },
        "conversion": {
            "symbol": "EURUSD",
            "orientation": "account_base_profit_quote",
            "max_quote_age_ms": 5000,
            "max_quote_interval_ms": 60000,
        },
        "costs": {
            "commission_model": "observed_zero_intraday",
            "fee_model": "observed_zero_intraday",
            "swap_model": "mt5_points_rollover_v1",
            "rollover_clock": "broker_midnight",
            "snapshot_bracket_max_seconds": 900,
            "zero_multiplier_bracket_max_seconds": 72 * 3600,
        },
        "swap_snapshots": list(snapshots),
        "live_validation": {
            "valid": True,
        },
    }


def test_runtime_snapshot_is_journaled_and_preserves_previous_history(
    tmp_path,
    monkeypatch,
):
    previous = _snapshot("2026-07-27T20:55:00+00:00", "same")
    current = _snapshot("2026-07-27T21:05:00+00:00", "same")
    path = tmp_path / "broker_money_contract.json"
    path.write_text(
        json.dumps(_contract(previous)),
        encoding="utf-8",
    )
    events = []

    monkeypatch.setattr(
        main.broker_contract,
        "build_contract",
        lambda *_args, **_kwargs: _contract(current),
    )
    monkeypatch.setattr(
        main.broker_contract,
        "snapshot_record_reason",
        lambda _current, _previous: "rollover_window",
    )
    monkeypatch.setattr(
        main.journal,
        "event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)),
    )

    result = main._capture_broker_money_contract_snapshot(
        path=path,
        force=False,
    )

    assert result == "rollover_window"
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["swap_snapshots"] == [previous, current]
    assert events == [(
        "bot",
        "broker_money_contract_snapshot",
        {
            "record_reason": "rollover_window",
            "snapshot": current,
        },
    )]


def test_runtime_snapshot_does_not_write_or_log_unchanged_midday_probe(
    tmp_path,
    monkeypatch,
):
    previous = _snapshot("2026-07-27T12:00:00+00:00", "same")
    current = _snapshot("2026-07-27T12:05:00+00:00", "same")
    path = tmp_path / "broker_money_contract.json"
    original = json.dumps(_contract(previous))
    path.write_text(original, encoding="utf-8")

    monkeypatch.setattr(
        main.broker_contract,
        "build_contract",
        lambda *_args, **_kwargs: _contract(current),
    )
    monkeypatch.setattr(
        main.broker_contract,
        "snapshot_record_reason",
        lambda _current, _previous: None,
    )
    monkeypatch.setattr(
        main.journal,
        "event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unchanged probes are not journaled")
        ),
    )

    result = main._capture_broker_money_contract_snapshot(path=path)

    assert result is None
    assert path.read_text(encoding="utf-8") == original


def test_unchanged_probe_repairs_invalid_stored_contract_before_ready(
    tmp_path,
    monkeypatch,
):
    previous = _snapshot("2026-07-27T12:00:00+00:00", "same")
    current = _snapshot("2026-07-27T12:05:00+00:00", "same")
    path = tmp_path / "broker_money_contract.json"
    invalid = _contract(previous)
    invalid["live_validation"]["valid"] = False
    path.write_text(json.dumps(invalid), encoding="utf-8")

    monkeypatch.setattr(
        main.broker_contract,
        "build_contract",
        lambda *_args, **_kwargs: _contract(current),
    )
    monkeypatch.setattr(
        main.broker_contract,
        "snapshot_record_reason",
        lambda _current, _previous: None,
    )
    monkeypatch.setattr(
        main.journal,
        "event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("repairing derived metadata needs no new snapshot")
        ),
    )

    assert main._try_capture_broker_money_contract_snapshot(
        path=path,
    ) is True
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["live_validation"]["valid"] is True
    assert stored["swap_snapshots"] == [previous]


def test_runtime_startup_recovers_snapshot_history_from_event_stream(
    tmp_path,
    monkeypatch,
):
    previous = _snapshot("2026-07-27T20:55:00+00:00", "same")
    current = _snapshot("2026-07-27T21:05:00+00:00", "same")
    path = tmp_path / "broker_money_contract.json"
    events_path = tmp_path / "trade_events.jsonl"
    events_path.write_text("authoritative events\n", encoding="utf-8")
    loaded_paths = []

    monkeypatch.setattr(
        main.broker_contract,
        "load_event_snapshots",
        lambda source, **_identity: loaded_paths.append(source) or [previous],
    )
    monkeypatch.setattr(
        main.broker_contract,
        "build_contract",
        lambda *_args, **_kwargs: _contract(current),
    )
    monkeypatch.setattr(main.journal, "event", lambda *_args, **_kwargs: None)

    result = main._capture_broker_money_contract_snapshot(
        path=path,
        events_path=events_path,
        force=True,
    )

    assert result == "startup"
    assert loaded_paths == [events_path]
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["swap_snapshots"] == [previous, current]


def test_runtime_snapshot_rejects_invalid_contract_before_marking_ready(
    tmp_path,
    monkeypatch,
):
    current = _snapshot("2026-07-27T21:05:00+00:00", "same")
    invalid = _contract(current)
    invalid["live_validation"]["valid"] = False
    path = tmp_path / "broker_money_contract.json"
    events = []

    monkeypatch.setattr(
        main.broker_contract,
        "build_contract",
        lambda *_args, **_kwargs: invalid,
    )
    monkeypatch.setattr(
        main.journal,
        "event",
        lambda *args, **kwargs: events.append((args, kwargs)),
    )

    assert main._try_capture_broker_money_contract_snapshot(
        path=path,
        force=True,
    ) is False
    assert path.exists() is False
    assert all(
        args[1] != "broker_money_contract_snapshot"
        for args, _fields in events
    )


def test_runtime_snapshot_requires_durable_event_before_writing_contract(
    tmp_path,
    monkeypatch,
):
    current = _snapshot("2026-07-27T21:05:00+00:00", "same")
    path = tmp_path / "broker_money_contract.json"
    receipt = object()

    monkeypatch.setattr(
        main.broker_contract,
        "build_contract",
        lambda *_args, **_kwargs: _contract(current),
    )
    monkeypatch.setattr(main.journal, "event", lambda *_a, **_k: receipt)
    monkeypatch.setattr(
        main.journal,
        "confirm_event",
        lambda observed, **_kwargs: observed is not receipt,
    )

    try:
        main._capture_broker_money_contract_snapshot(
            path=path,
            events_path=tmp_path / "trade_events.jsonl",
            force=True,
        )
    except RuntimeError as exc:
        assert str(exc) == "broker money snapshot journal write failed"
    else:
        raise AssertionError("a non-durable snapshot must not be accepted")
    assert path.exists() is False


def test_runtime_recovery_event_must_be_durable_before_ready(
    monkeypatch,
):
    receipt = object()
    main._last_broker_contract_error = "RuntimeError: prior failure"
    main._broker_contract_ready = False

    monkeypatch.setattr(
        main,
        "_capture_broker_money_contract_snapshot",
        lambda **_kwargs: "startup",
    )
    monkeypatch.setattr(main.journal, "event", lambda *_a, **_k: receipt)
    monkeypatch.setattr(
        main.journal,
        "confirm_event",
        lambda observed, **_kwargs: observed is not receipt,
    )

    assert main._try_capture_broker_money_contract_snapshot() is False
    assert main._broker_contract_ready is False
    assert main._last_broker_contract_error == (
        "RuntimeError: broker money recovery journal write failed"
    )


def test_runtime_snapshot_failure_is_visible_but_never_stops_trading(
    tmp_path,
    monkeypatch,
):
    anomalies = []
    monkeypatch.setattr(
        main,
        "_capture_broker_money_contract_snapshot",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("MT5 unavailable")
        ),
    )
    monkeypatch.setattr(
        main.journal,
        "anomaly",
        lambda *args, **kwargs: anomalies.append((args, kwargs)),
    )

    assert main._try_capture_broker_money_contract_snapshot(
        path=tmp_path / "contract.json"
    ) is False
    assert anomalies[0][0][:3] == ("bot", "mt5", "warning")
    assert "MT5 unavailable" in anomalies[0][0][3]


async def test_runtime_monitor_notifies_only_capture_state_transitions(
    monkeypatch,
):
    outcomes = iter([False, False, True])
    notifications = []
    main._broker_contract_ready = True

    async def no_wait(_seconds):
        return None

    async def capture_notification(text):
        notifications.append(text)

    def capture():
        try:
            return next(outcomes)
        except StopIteration as exc:
            raise asyncio.CancelledError from exc

    monkeypatch.setattr(main.asyncio, "sleep", no_wait)
    monkeypatch.setattr(
        main,
        "_try_capture_broker_money_contract_snapshot",
        capture,
    )
    monkeypatch.setattr(main, "notify", capture_notification)

    try:
        await main._broker_money_contract_monitor(interval_sec=30)
    except asyncio.CancelledError:
        pass

    assert len(notifications) == 2
    assert "INTERRUMPIDO" in notifications[0]
    assert "sigue operando" in notifications[0]
    assert "RECUPERADO" in notifications[1]


async def test_runtime_monitor_ignores_one_transient_failed_sample(
    monkeypatch,
):
    outcomes = iter([False, True])
    notifications = []
    main._broker_contract_ready = True

    async def no_wait(_seconds):
        return None

    def capture():
        try:
            return next(outcomes)
        except StopIteration as exc:
            raise asyncio.CancelledError from exc

    monkeypatch.setattr(main.asyncio, "sleep", no_wait)
    monkeypatch.setattr(
        main,
        "_try_capture_broker_money_contract_snapshot",
        capture,
    )
    monkeypatch.setattr(
        main,
        "notify",
        lambda text: notifications.append(text),
    )

    try:
        await main._broker_money_contract_monitor(interval_sec=30)
    except asyncio.CancelledError:
        pass

    assert notifications == []


async def test_runtime_monitor_survives_notification_transport_failure(
    monkeypatch,
):
    outcomes = iter([False, False, True])
    capture_calls = []
    journal_events = []
    main._broker_contract_ready = True

    async def no_wait(_seconds):
        return None

    async def failed_notification(_text):
        raise RuntimeError("Telegram unavailable")

    def capture():
        try:
            result = next(outcomes)
        except StopIteration as exc:
            raise asyncio.CancelledError from exc
        capture_calls.append(result)
        return result

    monkeypatch.setattr(main.asyncio, "sleep", no_wait)
    monkeypatch.setattr(
        main,
        "_try_capture_broker_money_contract_snapshot",
        capture,
    )
    monkeypatch.setattr(main, "notify", failed_notification)
    monkeypatch.setattr(
        main.journal,
        "event",
        lambda sig, ev, **fields: journal_events.append(
            (sig, ev, fields)
        ),
    )

    try:
        await main._broker_money_contract_monitor(interval_sec=30)
    except asyncio.CancelledError:
        pass

    assert capture_calls == [False, False, True]
    assert [event[1] for event in journal_events] == [
        "broker_money_contract_status_notify_failed",
    ]


async def test_runtime_monitor_moves_capture_off_the_telegram_event_loop(
    monkeypatch,
):
    calls = []
    main._broker_contract_ready = True

    async def no_wait(_seconds):
        return None

    def capture():
        raise asyncio.CancelledError

    async def inline_thread(function, *args, **kwargs):
        calls.append((function, args, kwargs))
        return function(*args, **kwargs)

    monkeypatch.setattr(main.asyncio, "sleep", no_wait)
    monkeypatch.setattr(main.asyncio, "to_thread", inline_thread)
    monkeypatch.setattr(
        main,
        "_try_capture_broker_money_contract_snapshot",
        capture,
    )

    try:
        await main._broker_money_contract_monitor(interval_sec=30)
    except asyncio.CancelledError:
        pass

    assert calls == [(capture, (), {})]
