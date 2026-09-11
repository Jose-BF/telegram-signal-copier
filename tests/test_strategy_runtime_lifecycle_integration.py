import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import live_auditor
import listener
import main
import pending_actions
import position_lifecycle_monitor as monitor
from signal_lifecycle import TerminalCause, evaluate_terminal_request
from state import Signal


_FIXTURE = Path(__file__).parent / "fixtures" / "canal2_2320_lifecycle.json"


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
        timezone.utc
    ).replace(tzinfo=None)


def _load_signal() -> tuple[Signal, dict]:
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    first = payload["first_fill"]
    plan = payload["entry_plan"]
    signal = Signal(
        channel=payload["channel"],
        message_id=payload["message_id"],
        direction=payload["direction"],
        timestamp=_utc(payload["signal_observed_at_utc"]),
        market_ticket=first["ticket"],
        market_fill_price=first["price"],
        live_strategy_id=payload["strategy_id"],
        live_strategy_fingerprint=payload["strategy_fingerprint"],
        candidate_entry_expires_at=_utc(plan["expires_at_utc"]),
        candidate_entry_legs=[
            {"index": index, "level": level, "volume": volume}
            for index, (level, volume) in enumerate(
                zip(plan["levels"], plan["volumes"], strict=True)
            )
        ],
        candidate_filled_leg_indexes=[
            index for index in plan["filled_leg_indexes"] if index != 0
        ],
    )
    return signal, payload


def test_canal2_2320_automatic_flat_keeps_four_entry_intents_alive():
    signal, payload = _load_signal()
    observed_at = _utc(
        payload["automatic_flat_check"]["observed_at_utc"]
    )

    decision = evaluate_terminal_request(
        signal,
        cause=TerminalCause.AUTOMATIC_FLAT,
        open_position_count=0,
        observed_at=observed_at,
    )

    assert decision.action == payload["expected"]["action"]
    assert decision.reason == payload["expected"]["reason"]
    assert list(decision.eligible_entry_indexes) == payload["expected"][
        "eligible_leg_indexes"
    ]


def test_all_live_observers_use_the_same_canal2_2320_lifecycle_decision():
    signal, payload = _load_signal()
    observed_at = _utc(
        payload["automatic_flat_check"]["observed_at_utc"]
    )

    decisions = (
        main._reconciler_terminal_decision(
            signal,
            n_open_mt5=0,
            observed_at=observed_at,
        ),
        monitor._automatic_flat_terminal_decision(
            signal,
            n_open=0,
            positions_complete=True,
            observed_at=observed_at,
        ),
        live_auditor._automatic_flat_terminal_decision(
            signal,
            n_open=0,
            observed_at=observed_at,
        ),
    )

    assert {decision.action for decision in decisions} == {"keep_alive"}
    assert {decision.reason for decision in decisions} == {
        "eligible_entry_intents"
    }
    assert {
        decision.eligible_entry_indexes for decision in decisions
    } == {(1, 2, 3, 4)}


def test_fixture_keeps_actual_mt5_separate_from_blocked_shadow_prediction():
    _signal, payload = _load_signal()

    assert payload["expected"]["actual_mt5_eur"] == 1.72
    assert payload["expected"]["incorrect_shadow_prediction_eur"] == -310.18
    assert payload["expected"]["shadow_prediction_blockers"] == [
        "money_contract_missing"
    ]


class _Journal:
    def __init__(self):
        self.events = []
        self.anomalies = []
        self.finalized = []

    def event(self, signal_id, event, **fields):
        self.events.append({"sig": signal_id, "event": event, **fields})

    def anomaly(self, signal_id, category, severity, detail, **fields):
        self.anomalies.append({
            "sig": signal_id,
            "category": category,
            "severity": severity,
            "detail": detail,
            **fields,
        })

    def finalize_trade(self, signal_id, **fields):
        self.finalized.append({"sig": signal_id, **fields})


@pytest.mark.asyncio
async def test_real_finalizer_refuses_canal2_2320_automatic_flat(monkeypatch):
    signal, _payload = _load_signal()
    journal = _Journal()
    monkeypatch.setattr(listener, "journal", journal)
    monkeypatch.setattr(
        listener,
        "_open_mt5_positions_for_signal",
        lambda _signal: [],
    )

    finalized = await listener._finalize_signal(
        signal,
        closed_by="RECONCILER",
        observed_at=_utc("2026-09-01T11:49:30.252+00:00"),
    )

    assert finalized is False
    assert signal.status == "open"
    assert journal.finalized == []
    decision = next(
        row for row in journal.events
        if row["event"] == "lifecycle_finalization_deferred"
    )
    assert decision["reason"] == "eligible_entry_intents"
    assert decision["eligible_entry_indexes"] == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_explicit_provider_close_finalizes_once_mt5_is_flat(monkeypatch):
    signal, _payload = _load_signal()
    signal.requested_close_reason = "PROVIDER_CLOSE"
    journal = _Journal()
    monkeypatch.setattr(listener, "journal", journal)
    monkeypatch.setattr(
        listener,
        "_open_mt5_positions_for_signal",
        lambda _signal: [],
    )
    monkeypatch.setattr(listener, "_realized_pl", lambda _signal: 1.72)
    monkeypatch.setattr(
        listener.executor,
        "account_evidence",
        lambda: {"currency": "EUR"},
    )
    monkeypatch.setattr(listener.asyncio, "sleep", lambda _seconds: None)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(listener.asyncio, "sleep", no_sleep)

    finalized = await listener._finalize_signal(
        signal,
        closed_by="PROVIDER_CLOSE",
        observed_at=_utc("2026-09-01T11:49:30.252+00:00"),
    )

    assert finalized is True
    assert signal.status == "closed"
    assert signal.lifecycle_cancelled_entry_indexes == [1, 2, 3, 4]
    assert len(journal.finalized) == 1
    assert journal.finalized[0]["total_pnl_usd"] == 1.72


@pytest.mark.asyncio
async def test_explicit_close_does_not_finalize_while_mt5_position_remains(
    monkeypatch,
):
    signal, _payload = _load_signal()
    signal.requested_close_reason = "PROVIDER_CLOSE"
    journal = _Journal()
    monkeypatch.setattr(listener, "journal", journal)
    monkeypatch.setattr(
        listener,
        "_open_mt5_positions_for_signal",
        lambda _signal: [{"ticket": signal.market_ticket}],
    )

    finalized = await listener._finalize_signal(
        signal,
        closed_by="PROVIDER_CLOSE",
        observed_at=_utc("2026-09-01T11:49:30.252+00:00"),
    )

    assert finalized is False
    assert signal.status == "open"
    assert signal.lifecycle_state == "closing"
    assert journal.finalized == []


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrent", [False, True])
async def test_repeated_close_confirmation_records_one_final_result(monkeypatch, concurrent):
    signal, _ = _load_signal()
    signal.requested_close_reason = "PROVIDER_CLOSE"
    journal = _Journal()
    monkeypatch.setattr(listener, "journal", journal)
    monkeypatch.setattr(listener, "_open_mt5_positions_for_signal", lambda _signal: [])
    monkeypatch.setattr(listener, "_realized_pl", lambda _signal: -24.99)
    monkeypatch.setattr(listener.executor, "account_evidence", lambda: {"currency": "EUR"})
    monkeypatch.setattr("MetaTrader5.history_deals_get", lambda **kwargs: [])
    original_sleep = asyncio.sleep

    async def yield_once(_seconds):
        await original_sleep(0)

    monkeypatch.setattr(listener.asyncio, "sleep", yield_once)
    if concurrent:
        results = await asyncio.gather(*(
            listener._finalize_signal(signal, closed_by="PROVIDER_CLOSE") for _ in range(3)
        ))
    else:
        results = [await listener._finalize_signal(signal, closed_by="PROVIDER_CLOSE") for _ in range(3)]

    assert results == [True, True, True]
    assert signal.status == "closed"
    assert len(journal.finalized) == 1
    assert journal.finalized[0]["total_pnl_usd"] == -24.99
    assert sum(row["event"] == "pos_summary" for row in journal.events) == 1


@pytest.mark.asyncio
async def test_failed_finalization_remains_retryable(monkeypatch):
    signal, _ = _load_signal()
    signal.requested_close_reason = "PROVIDER_CLOSE"
    journal = _Journal()
    monkeypatch.setattr(listener, "journal", journal)
    monkeypatch.setattr(listener, "_open_mt5_positions_for_signal", lambda _signal: [])
    monkeypatch.setattr(listener, "_realized_pl", lambda _signal: 1.72)
    monkeypatch.setattr(listener.executor, "account_evidence", lambda: {"currency": "EUR"})
    monkeypatch.setattr("MetaTrader5.history_deals_get", lambda **kwargs: [])

    async def no_sleep(_seconds):
        pass

    monkeypatch.setattr(listener.asyncio, "sleep", no_sleep)
    original_finalize = journal.finalize_trade

    def fail_once(*args, **kwargs):
        monkeypatch.setattr(journal, "finalize_trade", original_finalize)
        raise OSError("simulated unavailable journal")

    monkeypatch.setattr(journal, "finalize_trade", fail_once)
    assert await listener._finalize_signal(signal, closed_by="PROVIDER_CLOSE") is False
    assert signal.status == "open"
    assert await listener._finalize_signal(signal, closed_by="PROVIDER_CLOSE") is True
    assert len(journal.finalized) == 1


@pytest.mark.asyncio
async def test_cancelled_finalization_releases_guard_and_can_retry(monkeypatch):
    signal, _ = _load_signal()
    signal.requested_close_reason = "PROVIDER_CLOSE"
    journal = _Journal()
    monkeypatch.setattr(listener, "journal", journal)
    monkeypatch.setattr(listener, "_open_mt5_positions_for_signal", lambda _signal: [])
    monkeypatch.setattr(listener, "_realized_pl", lambda _signal: 1.72)
    monkeypatch.setattr(listener.executor, "account_evidence", lambda: {"currency": "EUR"})
    monkeypatch.setattr("MetaTrader5.history_deals_get", lambda **kwargs: [])
    entered = asyncio.Event()
    release = asyncio.Event()

    async def interrupted_sleep(_seconds):
        entered.set()
        await release.wait()

    monkeypatch.setattr(listener.asyncio, "sleep", interrupted_sleep)
    closing = asyncio.create_task(listener._finalize_signal(signal, closed_by="PROVIDER_CLOSE"))
    await entered.wait()
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert signal.status == "open"
    assert signal.journal_finalized is False
    assert journal.finalized == []
    release.set()
    assert await listener._finalize_signal(signal, closed_by="PROVIDER_CLOSE") is True
    assert len(journal.finalized) == 1


@pytest.mark.asyncio
async def test_finalization_guard_is_not_shared_between_signals(monkeypatch):
    first, _ = _load_signal()
    second, _ = _load_signal()
    second.message_id += 1
    for signal in (first, second):
        signal.requested_close_reason = "PROVIDER_CLOSE"
    journal = _Journal()
    monkeypatch.setattr(listener, "journal", journal)
    monkeypatch.setattr(listener, "_open_mt5_positions_for_signal", lambda _signal: [])
    monkeypatch.setattr(listener, "_realized_pl", lambda _signal: 1.72)
    monkeypatch.setattr(listener.executor, "account_evidence", lambda: {"currency": "EUR"})
    monkeypatch.setattr("MetaTrader5.history_deals_get", lambda **kwargs: [])
    first_paused = asyncio.Event()
    release_first = asyncio.Event()
    calls = 0

    async def pause_first(_seconds):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_paused.set()
            await release_first.wait()

    monkeypatch.setattr(listener.asyncio, "sleep", pause_first)
    first_task = asyncio.create_task(listener._finalize_signal(first, closed_by="PROVIDER_CLOSE"))
    await first_paused.wait()
    try:
        assert await asyncio.wait_for(listener._finalize_signal(second, closed_by="PROVIDER_CLOSE"), 1) is True
        assert first.journal_finalized is False
        assert second.journal_finalized is True
    finally:
        release_first.set()
        await first_task
    assert len(journal.finalized) == 2


@pytest.mark.asyncio
async def test_confirmed_pending_close_rechecks_terminal_signal(monkeypatch):
    signal, _payload = _load_signal()
    signal.requested_close_reason = "PROVIDER_CLOSE"
    signal.lifecycle_state = "closing"
    calls = []

    async def fake_finalize(sig, closed_by, **kwargs):
        calls.append((sig, closed_by, kwargs))
        return True

    monkeypatch.setattr(listener, "_finalize_signal", fake_finalize)

    finalized = await pending_actions._recheck_terminal_signal(
        signal,
        trigger="close_position_confirmed",
    )

    assert finalized is True
    assert calls[0][0] is signal
    assert calls[0][1] == "PROVIDER_CLOSE"
