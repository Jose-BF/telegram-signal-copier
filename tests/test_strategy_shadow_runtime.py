from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from strategy_shadow_contracts import ShadowManagementEvent, ShadowTick
from strategy_shadow_engine import advance_tick
from strategy_shadow_runtime import (
    ShadowRuntime,
    ShadowTickCursor,
    ShadowTickHistory,
)


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
async def test_runtime_exposes_earliest_active_tick_cursor_for_live_resume():
    runtime = ShadowRuntime()
    await register_dubai(runtime)

    assert runtime.active_tick_cursor() == ShadowTickCursor(
        from_msc=100,
        after_identity=None,
    )

    observed = tick(101, 4300.0, 4300.2, 1)

    await runtime.process_tick(observed)

    assert runtime.active_tick_cursor() == ShadowTickCursor(
        from_msc=101,
        after_identity=observed.identity,
    )


@pytest.mark.asyncio
async def test_runtime_cursor_is_cleared_after_cohort_finishes():
    runtime = ShadowRuntime()
    await register_dubai(runtime)
    await runtime.process_tick(tick(101, 4300.0, 4300.2, 1))
    await runtime.process_management(ShadowManagementEvent(
        event_id="close-1",
        signal_id="canal1_20700",
        action="CLOSE_ALL",
        observed_at_utc=iso(2),
        observed_tick_msc=102,
    ))
    await runtime.process_tick(tick(102, 4300.1, 4300.3, 2))

    assert runtime.active_tick_cursor() is None


@pytest.mark.asyncio
async def test_new_signal_after_idle_uses_its_own_registration_cursor():
    runtime = ShadowRuntime()
    await register_dubai(runtime)
    await runtime.process_tick(tick(101, 4300.0, 4300.2, 1))
    await runtime.process_management(ShadowManagementEvent(
        event_id="close-1",
        signal_id="canal1_20700",
        action="CLOSE_ALL",
        observed_at_utc=iso(2),
        observed_tick_msc=102,
    ))
    await runtime.process_tick(tick(102, 4300.1, 4300.3, 2))
    assert runtime.active_tick_cursor() is None

    await runtime.register_signal(
        channel="canal1",
        signal_id="canal1_20701",
        source_message_id=20701,
        direction="SELL",
        registered_at_utc=iso(3600),
        registered_tick_msc=3_600_100,
    )

    assert runtime.active_tick_cursor() == ShadowTickCursor(
        from_msc=3_600_100,
        after_identity=None,
    )


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
    registrations = {
        row["candidate_id"]: row
        for row in journal.records
        if row["ev"] == "strategy_shadow_registered"
    }
    transitions = {
        row["candidate_id"]: row
        for row in journal.records
        if row["ev"] == "strategy_shadow_transition"
    }
    for checkpoint in checkpoints:
        candidate_id = checkpoint["candidate_id"]
        expected_previous = transitions.get(
            candidate_id,
            registrations[candidate_id],
        )["state_hash"]
        assert checkpoint["previous_state_hash"] == expected_previous
        assert checkpoint["previous_state_hash"] != checkpoint["state_hash"]


@pytest.mark.asyncio
async def test_checkpoint_after_tick_only_change_recovers_without_false_corruption():
    journal = JournalCapture()
    runtime = ShadowRuntime(journal_sink=journal, checkpoint_seconds=300)
    await register_dubai(runtime)
    await runtime.process_tick(tick(101, 4300.0, 4300.2, 1))
    await runtime.process_tick(tick(102, 4300.1, 4300.3, 301))

    async def history_reader(cursor: ShadowTickCursor):
        return ShadowTickHistory(ticks=(), complete=True, evidence_id="done")

    recovered = ShadowRuntime()
    await recovered.recover(journal.records, history_reader=history_reader)

    states = recovered.states_for_signal("canal1_20700")
    assert states
    assert all("journal_hash_mismatch" not in state.evidence_blockers for state in states)


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
    assert all(state.positions[0].stop_price is None for state in changed)
    assert all(state.processed_management_ids == ("mgmt-1",) for state in changed)
    management_rows = [
        row for row in journal.records
        if row["ev"] == "strategy_shadow_transition"
        and row.get("reason") == "MOVE_SL_TO_BE"
    ]
    assert len(management_rows) == 3
    assert all(row["transition_tick_msc"] == 101 for row in management_rows)


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

    async def history_reader(cursor: ShadowTickCursor):
        assert cursor.from_msc == 102
        assert cursor.after_identity == ticks[1].identity
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

    async def history_reader(cursor: ShadowTickCursor):
        assert cursor == ShadowTickCursor(from_msc=100, after_identity=None)
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

    async def incomplete_history(_cursor: ShadowTickCursor):
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
        history_reader=lambda _cursor: ShadowTickHistory(
            ticks=(), complete=True, evidence_id="h3"
        ),
    )

    state = recovered.state("canal1_20700", "dubai_balanced_v1")
    assert state.status == "incomplete"
    assert "journal_hash_mismatch" in state.evidence_blockers


@pytest.mark.asyncio
async def test_recovery_blocks_a_record_whose_envelope_mislabels_the_state():
    journal = JournalCapture()
    runtime = ShadowRuntime(journal_sink=journal)
    await register_dubai(runtime)
    tampered = [dict(row) for row in journal.records]
    tampered[0]["sig"] = "canal1_wrong"

    recovered = ShadowRuntime()
    await recovered.recover(
        tampered,
        history_reader=lambda _cursor: ShadowTickHistory(
            ticks=(), complete=True, evidence_id="identity-corrupt",
        ),
    )

    state = recovered.state("canal1_20700", "dubai_balanced_v1")
    assert state.status == "incomplete"
    assert "journal_identity_mismatch" in state.evidence_blockers


@pytest.mark.asyncio
async def test_recovery_accepts_legacy_self_linked_checkpoint():
    journal = JournalCapture()
    runtime = ShadowRuntime(journal_sink=journal, checkpoint_seconds=300)
    await register_dubai(runtime)
    await runtime.process_tick(tick(101, 4300.0, 4300.2, 1))
    await runtime.process_tick(tick(102, 4300.1, 4300.3, 301))
    legacy = []
    for row in journal.records:
        if row["ev"] == "strategy_shadow_checkpoint":
            row = dict(row, previous_state_hash=row["state_hash"])
        legacy.append(row)

    recovered = ShadowRuntime()
    await recovered.recover(
        legacy,
        history_reader=lambda _cursor: ShadowTickHistory(
            ticks=(), complete=True, evidence_id="legacy"
        ),
    )

    states = recovered.states_for_signal("canal1_20700")
    assert states
    assert all("journal_hash_mismatch" not in state.evidence_blockers for state in states)


@pytest.mark.asyncio
async def test_recovery_marks_valid_state_incomplete_after_malformed_event():
    journal = JournalCapture()
    runtime = ShadowRuntime(journal_sink=journal)
    await register_dubai(runtime)
    await runtime.process_tick(tick(101, 4300.0, 4300.2, 1))
    damaged = [dict(row) for row in journal.records]
    for index, row in enumerate(damaged):
        if (
            row["ev"] == "strategy_shadow_transition"
            and row["candidate_id"] == "dubai_balanced_v1"
        ):
            damaged[index] = {**row, "state": {"invalid": True}}
            break

    recovered = ShadowRuntime()
    await recovered.recover(
        damaged,
        history_reader=lambda _cursor: ShadowTickHistory(
            ticks=(), complete=True, evidence_id="malformed-event",
        ),
    )

    state = recovered.state("canal1_20700", "dubai_balanced_v1")
    assert state.status == "incomplete"
    assert "journal_state_invalid" in state.evidence_blockers


@pytest.mark.asyncio
async def test_recovery_rejects_missing_hash_link_after_registration():
    journal = JournalCapture()
    runtime = ShadowRuntime(journal_sink=journal)
    await register_dubai(runtime)
    await runtime.process_tick(tick(101, 4300.0, 4300.2, 1))
    damaged = [dict(row) for row in journal.records]
    for index, row in enumerate(damaged):
        if (
            row["ev"] == "strategy_shadow_transition"
            and row["candidate_id"] == "dubai_balanced_v1"
        ):
            damaged[index] = {**row, "previous_state_hash": None}
            break

    recovered = ShadowRuntime()
    await recovered.recover(
        damaged,
        history_reader=lambda _cursor: ShadowTickHistory(
            ticks=(), complete=True, evidence_id="missing-link",
        ),
    )

    state = recovered.state("canal1_20700", "dubai_balanced_v1")
    assert state.status == "incomplete"
    assert "journal_hash_mismatch" in state.evidence_blockers


@pytest.mark.asyncio
async def test_long_recovery_yields_to_live_event_loop_work():
    journal = JournalCapture()
    runtime = ShadowRuntime(journal_sink=journal)
    await register_dubai(runtime)
    history = tuple(
        tick(101 + index, 4300.0, 4300.2, 1 + index / 1000)
        for index in range(600)
    )
    recovered = ShadowRuntime()

    recovery = asyncio.create_task(recovered.recover(
        journal.records,
        history_reader=lambda _cursor: ShadowTickHistory(
            ticks=history, complete=True, evidence_id="long-history",
        ),
    ))

    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert recovery.done() is False
    await recovery
