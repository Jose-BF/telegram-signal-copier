from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import gold_555_live_candidate
from signal_lifecycle import (
    TerminalCause,
    apply_lifecycle_decision,
    evaluate_terminal_request,
)
from state import Signal


def _signal(*, expires_in_minutes: int = 20) -> tuple[Signal, datetime]:
    now = datetime(2026, 9, 2, 10, 0, 0)
    signal = Signal(
        channel="canal2",
        message_id=2320,
        direction="BUY",
        timestamp=now - timedelta(minutes=10),
        market_ticket=1903871553,
        market_fill_price=4386.07,
        live_strategy_id=gold_555_live_candidate.CANDIDATE_ID,
        live_strategy_fingerprint=(
            gold_555_live_candidate.CANDIDATE_FINGERPRINT
        ),
        candidate_entry_expires_at=now + timedelta(
            minutes=expires_in_minutes
        ),
        candidate_entry_legs=[
            {"index": index, "volume": volume}
            for index, volume in enumerate((0.04, 0.03, 0.03, 0.03, 0.03))
        ],
        candidate_filled_leg_indexes=[],
    )
    return signal, now


def test_automatic_flat_keeps_signal_alive_while_entries_are_eligible():
    signal, now = _signal()

    decision = evaluate_terminal_request(
        signal,
        cause=TerminalCause.AUTOMATIC_FLAT,
        open_position_count=0,
        observed_at=now,
    )

    assert decision.action == "keep_alive"
    assert decision.reason == "eligible_entry_intents"
    assert decision.eligible_entry_indexes == (1, 2, 3, 4)
    assert decision.cancelled_entry_indexes == ()


@pytest.mark.parametrize(
    "cause",
    (
        TerminalCause.PROVIDER_CLOSE,
        TerminalCause.STRATEGY_STOP,
        TerminalCause.TIME_EXIT,
        TerminalCause.RETRACTION,
        TerminalCause.OPERATOR_CLOSE,
    ),
)
def test_explicit_terminal_causes_cancel_pending_entries_and_finalize_when_flat(
    cause,
):
    signal, now = _signal()

    decision = evaluate_terminal_request(
        signal,
        cause=cause,
        open_position_count=0,
        observed_at=now,
    )

    assert decision.action == "finalize"
    assert decision.reason == cause.value
    assert decision.cancelled_entry_indexes == (1, 2, 3, 4)


def test_explicit_close_waits_for_mt5_to_confirm_zero_exposure():
    signal, now = _signal()

    decision = evaluate_terminal_request(
        signal,
        cause=TerminalCause.PROVIDER_CLOSE,
        open_position_count=1,
        observed_at=now,
    )

    assert decision.action == "defer"
    assert decision.reason == "open_positions"
    assert decision.cancelled_entry_indexes == (1, 2, 3, 4)


def test_expired_entry_plan_allows_automatic_finalization():
    signal, now = _signal(expires_in_minutes=-1)

    decision = evaluate_terminal_request(
        signal,
        cause=TerminalCause.AUTOMATIC_FLAT,
        open_position_count=0,
        observed_at=now,
    )

    assert decision.action == "finalize"
    assert decision.reason == "no_eligible_entry_intents"


@pytest.mark.parametrize(
    "mutation",
    ("unknown_strategy", "fingerprint_mismatch", "missing_expiry"),
)
def test_unknown_or_incomplete_contract_evidence_fails_closed(mutation):
    signal, now = _signal()
    if mutation == "unknown_strategy":
        signal.live_strategy_id = "unknown"
    elif mutation == "fingerprint_mismatch":
        signal.live_strategy_fingerprint = "f" * 64
    else:
        signal.candidate_entry_expires_at = None

    decision = evaluate_terminal_request(
        signal,
        cause=TerminalCause.AUTOMATIC_FLAT,
        open_position_count=0,
        observed_at=now,
    )

    assert decision.action == "keep_alive"
    assert decision.reason == "lifecycle_evidence_incomplete"
    assert decision.blockers


def test_incomplete_mt5_position_evidence_blocks_every_finalization():
    signal, now = _signal(expires_in_minutes=-1)

    decision = evaluate_terminal_request(
        signal,
        cause=TerminalCause.AUTOMATIC_FLAT,
        open_position_count=0,
        positions_complete=False,
        observed_at=now,
    )

    assert decision.action == "defer"
    assert decision.reason == "position_evidence_incomplete"


def test_applying_same_decision_twice_is_idempotent_and_serializable():
    signal, now = _signal()
    decision = evaluate_terminal_request(
        signal,
        cause=TerminalCause.PROVIDER_CLOSE,
        open_position_count=1,
        observed_at=now,
    )

    first = apply_lifecycle_decision(signal, decision)
    second = apply_lifecycle_decision(signal, decision)

    assert first == second
    assert signal.lifecycle_cancelled_entry_indexes == [1, 2, 3, 4]
    assert signal.lifecycle_terminal_cause == "provider_close"
    assert signal.lifecycle_last_decision["action"] == "defer"


def test_legacy_signal_without_a_candidate_plan_keeps_existing_flat_semantics():
    signal = Signal(channel="canal1", message_id=10, direction="SELL")

    decision = evaluate_terminal_request(
        signal,
        cause=TerminalCause.AUTOMATIC_FLAT,
        open_position_count=0,
        observed_at=datetime(2026, 9, 2, 10, 0, 0),
    )

    assert decision.action == "finalize"
    assert decision.reason == "no_eligible_entry_intents"
