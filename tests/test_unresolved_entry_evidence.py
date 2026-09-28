"""Expiry and flat observations cannot resolve a possibly committed entry."""

from datetime import timedelta

import pytest

import listener
import position_lifecycle_monitor as monitor
from signal_lifecycle import TerminalCause, evaluate_terminal_request
from tests.test_finalizer_money_completeness import deals
from tests.test_signal_lifecycle import _signal


@pytest.mark.parametrize("cause", list(TerminalCause))
@pytest.mark.parametrize("expired", [False, True])
def test_unresolved_execution_blocks_terminal_even_when_flat_or_expired(cause, expired):
    signal, now = _signal(expires_in_minutes=-1 if expired else 20)
    signal.candidate_entry_reconcile_pending_indexes = [1]
    decision = evaluate_terminal_request(signal, cause=cause, open_position_count=0, observed_at=now)
    assert decision.action == "defer"
    assert decision.reason == "entry_execution_unresolved"
    assert decision.blockers == ("entry_execution_unresolved",)


def test_reconciled_execution_releases_terminal_gate():
    signal, now = _signal(expires_in_minutes=-1)
    signal.candidate_entry_reconcile_pending_indexes = [1]
    assert evaluate_terminal_request(signal, cause="automatic_flat", open_position_count=0,
                                     observed_at=now).action == "defer"
    signal.candidate_entry_reconcile_pending_indexes.clear()
    assert evaluate_terminal_request(signal, cause="automatic_flat", open_position_count=0,
                                     observed_at=now + timedelta(seconds=1)).action == "finalize"


def test_final_money_cannot_use_only_known_tickets_while_entry_unresolved(monkeypatch):
    signal, _ = _signal()
    signal.candidate_entry_reconcile_pending_indexes = [1]
    monkeypatch.setattr("mt5_runtime.mt5.history_deals_get", lambda **_: deals(signal.magic))
    assert listener._realized_pl(signal) is None
    signal.candidate_entry_reconcile_pending_indexes.clear()
    assert listener._realized_pl(signal) == pytest.approx(8.5)


def test_partial_money_observation_cannot_arm_a_profit_guard(monkeypatch):
    signal, now = _signal()
    signal.candidate_first_fill_at = now
    signal.candidate_entry_reconcile_pending_indexes = [1]
    summary = dict(pl=40., n_open=1, open_tickets=[signal.market_ticket], positions_complete=True)
    monkeypatch.setattr(monitor, "_floating_pl_summary", lambda _: dict(summary))
    result = monitor._signal_pl_summary(signal)
    assert result["total_pl"] is None and result["realized_complete"] is False
    assert result["unresolved_entry_indexes"] == [1]
    assert result["floating_pl"] == 40.
    decision = monitor._apply_gold_555_basket_guard(signal, result, now=now)
    assert decision.action == "evidence_incomplete" and not signal.basket_guard_armed
    signal.candidate_entry_reconcile_pending_indexes.clear()
    recovered = monitor._signal_pl_summary(signal)
    assert recovered["total_pl"] == 40. and recovered["realized_complete"] is True
