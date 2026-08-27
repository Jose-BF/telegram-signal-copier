from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from strategy_shadow_contracts import ShadowManagementEvent, ShadowTick
from strategy_shadow_engine import advance_tick
from strategy_shadow_runtime import ShadowRuntime, ShadowTickHistory


BASE = datetime(2026, 8, 27, 8, 0, tzinfo=timezone.utc)
GOLD_IDS = {
    "gold_now_555_v1",
    "gold_now_b210_v1",
    "gold_now_c490_v1",
}
DUBAI_IDS = {
    "dubai_balanced_v1",
    "dubai_frontloaded_30m_v1",
    "dubai_frontloaded_40m_v1",
}


def iso(seconds: float = 0.0) -> str:
    return (BASE + timedelta(seconds=seconds)).isoformat()


def tick(msc: int, bid: float, ask: float, seconds: float = 0.0) -> ShadowTick:
    return ShadowTick(
        time_msc=msc,
        bid=bid,
        ask=ask,
        observed_at_utc=iso(seconds),
        positive_eur_per_move_lot=100.0,
        negative_eur_per_move_lot=100.0,
        money_evidence_id="money-1",
    )


class JournalCapture:
    def __init__(self):
        self.records: list[dict] = []

    def __call__(self, signal_id: str, event: str, **fields):
        self.records.append({"sig": signal_id, "ev": event, **fields})


async def register_dubai(runtime: ShadowRuntime):
    return await runtime.register_signal(
        channel="canal1",
        signal_id="canal1_20700",
        source_message_id=20700,
        direction="BUY",
        registered_at_utc=iso(),
        registered_tick_msc=100,
    )


async def register_gold(runtime: ShadowRuntime):
    return await runtime.register_signal(
        channel="canal2",
        signal_id="canal2_380",
        source_message_id=380,
        direction="BUY",
        registered_at_utc=iso(),
        registered_tick_msc=100,
        reference_price=4300.0,
    )


@pytest.mark.asyncio
async def test_runtime_registers_only_the_signal_channel_and_is_idempotent():
    journal = JournalCapture()
    runtime = ShadowRuntime(journal_sink=journal)

    first = await register_dubai(runtime)
    second = await register_dubai(runtime)

    assert {state.candidate_id for state in first} == DUBAI_IDS
    assert second == ()
    assert runtime.active_candidate_ids() == DUBAI_IDS
    assert len([row for row in journal.records
                if row["ev"] == "strategy_shadow_registered"]) == 3


@pytest.mark.asyncio
async def test_candidate_exception_is_isolated_and_siblings_continue():
    journal = JournalCapture()

    def faulty_advance(policy, state, observed_tick):
        if policy.candidate_id == "gold_now_b210_v1":
            raise RuntimeError("injected failure")
        return advance_tick(policy, state, observed_tick)

    runtime = ShadowRuntime(
        journal_sink=journal,
        engine_advance=faulty_advance,
    )
    await register_gold(runtime)

    await runtime.process_tick(tick(101, 4298.7, 4298.9))

    assert runtime.status("gold_now_b210_v1") == "disabled"
    assert runtime.status("gold_now_555_v1") == "running"
    assert runtime.status("gold_now_c490_v1") == "running"
    assert any(
        row["ev"] == "strategy_shadow_candidate_disabled"
        and row["candidate_id"] == "gold_now_b210_v1"
        for row in journal.records
    )


@pytest.mark.asyncio
async def test_runtime_records_transitions_but_not_unchanged_ticks():
    journal = JournalCapture()
    runtime = ShadowRuntime(journal_sink=journal, checkpoint_seconds=300)
    await register_dubai(runtime)
    registration_count = len(journal.records)

    await runtime.process_tick(tick(101, 4300.0, 4300.2, 1))
    after_fill = len(journal.records)
    await runtime.process_tick(tick(102, 4300.1, 4300.3, 2))

    assert after_fill > registration_count
    assert len(journal.records) == after_fill
    transition = next(
        row for row in journal.records
        if row["ev"] == "strategy_shadow_transition"
    )
    assert transition["state_hash"]
    assert transition["state"]["positions"]
    assert transition["tick"]["time_msc"] == 101


@pytest.mark.asyncio
async def test_periodic_checkpoint_contains_recoverable_full_state():
    journal = JournalCapture()
    runtime = ShadowRuntime(journal_sink=journal, checkpoint_seconds=300)
    await register_dubai(runtime)
    await runtime.process_tick(tick(101, 4300.0, 4300.2, 1))

    await runtime.process_tick(tick(102, 4300.1, 4300.3, 301))

    checkpoints = [
        row for row in journal.records
        if row["ev"] == "strategy_shadow_checkpoint"
    ]
    assert len(checkpoints) == 3
    assert all(row["state_hash"] for row in checkpoints)


@pytest.mark.asyncio
async def test_management_routes_to_all_candidates_of_one_signal():
    journal = JournalCapture()
    runtime = ShadowRuntime(journal_sink=journal)
    await register_dubai(runtime)
    await runtime.process_tick(tick(101, 4300.0, 4300.2, 1))
    event = ShadowManagementEvent(
        event_id="mgmt-1",
        signal_id="canal1_20700",
        action="MOVE_SL_TO_BE",
        observed_at_utc=iso(2),
        observed_tick_msc=101,
    )

    changed = await runtime.process_management(event)

    assert {state.candidate_id for state in changed} == DUBAI_IDS
    assert all(state.positions[0].stop_price == state.positions[0].entry_price
               for state in changed)


@pytest.mark.asyncio
async def test_recovery_catchup_matches_uninterrupted_terminal_state():
    ticks = (
        tick(101, 4300.0, 4300.2, 1),
        tick(102, 4296.0, 4296.2, 2),
        tick(103, 4310.2, 4310.4, 3),
        tick(104, 4307.9, 4308.1, 4),
    )

    uninterrupted = ShadowRuntime()
    await register_dubai(uninterrupted)
    for observed in ticks:
        await uninterrupted.process_tick(observed)
    expected = uninterrupted.state("canal1_20700", "dubai_balanced_v1")

    journal = JournalCapture()
    interrupted = ShadowRuntime(journal_sink=journal)
    await register_dubai(interrupted)
    for observed in ticks[:2]:
        await interrupted.process_tick(observed)

    async def history_reader(from_msc: int):
        assert from_msc == 102
        return ShadowTickHistory(ticks=ticks[2:], complete=True, evidence_id="h1")

    recovered = ShadowRuntime(journal_sink=JournalCapture())
    await recovered.recover(journal.records, history_reader=history_reader)
    actual = recovered.state("canal1_20700", "dubai_balanced_v1")

    assert actual.state_hash == expected.state_hash
    assert actual.realized_eur == expected.realized_eur
    assert actual.exit_reason == expected.exit_reason


@pytest.mark.asyncio
async def test_recovery_before_555_entry_matches_uninterrupted_state():
    ticks = (
        tick(101, 4298.7, 4298.9, 1),
        tick(102, 4300.3, 4300.5, 2),
    )
    uninterrupted = ShadowRuntime()
    await register_gold(uninterrupted)
    for observed in ticks:
        await uninterrupted.process_tick(observed)
    expected = uninterrupted.state("canal2_380", "gold_now_555_v1")

    journal = JournalCapture()
    interrupted = ShadowRuntime(journal_sink=journal)
    await register_gold(interrupted)

    async def history_reader(from_msc: int):
        assert from_msc == 100
        return ShadowTickHistory(ticks=ticks, complete=True, evidence_id="h2")

    recovered = ShadowRuntime()
    await recovered.recover(journal.records, history_reader=history_reader)
    actual = recovered.state("canal2_380", "gold_now_555_v1")

    assert actual.state_hash == expected.state_hash


@pytest.mark.asyncio
async def test_incomplete_history_blocks_only_recovered_pairs():
    journal = JournalCapture()
    runtime = ShadowRuntime(journal_sink=journal)
    await register_dubai(runtime)
    await runtime.process_tick(tick(101, 4300.0, 4300.2, 1))

    async def incomplete_history(_from_msc: int):
        return ShadowTickHistory(ticks=(), complete=False, evidence_id="h-gap")

    recovered = ShadowRuntime()
    await recovered.recover(journal.records, history_reader=incomplete_history)

    states = recovered.states_for_signal("canal1_20700")
    assert len(states) == 3
    assert all(state.status == "incomplete" for state in states)
    assert all("tick_gap" in state.evidence_blockers for state in states)


@pytest.mark.asyncio
async def test_corrupt_checkpoint_hash_is_rejected_without_crashing_recovery():
    journal = JournalCapture()
    runtime = ShadowRuntime(journal_sink=journal)
    await register_dubai(runtime)
    corrupt = [dict(row) for row in journal.records]
    corrupt[0] = dict(corrupt[0], state_hash="0" * 64)

    recovered = ShadowRuntime()
    await recovered.recover(
        corrupt,
        history_reader=lambda _msc: ShadowTickHistory(
            ticks=(), complete=True, evidence_id="h3"
        ),
    )

    state = recovered.state("canal1_20700", "dubai_balanced_v1")
    assert state.status == "incomplete"
    assert "journal_hash_mismatch" in state.evidence_blockers
