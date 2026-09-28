from datetime import datetime, timezone
from decimal import Decimal

import pytest

from research.causal_comparison import SequenceEvent
from tools.audit_week_conditioned_controls import (
    native_events, receipt_states, require_native_risk_metrics,
)


START = datetime(2026, 9, 17, 13, 45, tzinfo=timezone.utc)
CUTOFF = datetime(2026, 9, 17, 14, 55, tzinfo=timezone.utc)
OFFSET = 10_800_000


def position(entry_ms, exit_ms, *, costs="0.00", direction="BUY"):
    return {"entry_msc": entry_ms + OFFSET, "exit_msc": exit_ms + OFFSET,
            "entry_price": "4350.00", "exit_price": "4351.00",
            "volume": "0.04", "actual_net_eur": "3.50",
            "costs_eur": costs, "direction": direction}


def test_native_conditioning_uses_entries_only_and_keeps_exit_money_separate():
    opened = int(datetime(2026, 9, 17, 14, 0, tzinfo=timezone.utc).timestamp() * 1000)
    entries, exits = native_events(
        [position(opened, opened + 1000)],
        direction="BUY", start=START, cutoff=CUTOFF)
    assert len(entries) == len(exits) == 1
    assert entries[0].kind == "entry" and entries[0].money == 0
    assert exits[0].kind == "exit" and str(exits[0].money) == "3.50"
    assert entries[0].price == 4350 and entries[0].volume == exits[0].volume


def test_native_conditioning_rejects_costs_ambiguous_ordinals_and_out_of_window():
    opened = int(datetime(2026, 9, 17, 14, 0, tzinfo=timezone.utc).timestamp() * 1000)
    with pytest.raises(ValueError, match="costs unsupported"):
        native_events([position(opened, opened + 1000, costs="0.01")],
                      direction="BUY", start=START, cutoff=CUTOFF)
    with pytest.raises(ValueError, match="ordinal ambiguous"):
        native_events([position(opened, opened + 1000),
                       position(opened, opened + 2000)],
                      direction="BUY", start=START, cutoff=CUTOFF)
    with pytest.raises(ValueError, match="crosses conditioned window"):
        native_events([position(opened, opened + 4_000_000)],
                      direction="BUY", start=START, cutoff=CUTOFF)


def test_conditioned_observed_money_must_reproduce_frozen_native_path():
    observed = {"metrics": {"final_net": "20.02", "max_drawdown": "178.72"}}
    frozen = {"path": {"metrics": {"final_net": "20.02",
                                    "max_drawdown": "178.72"}}}
    require_native_risk_metrics(observed, frozen)
    frozen["path"]["metrics"]["max_drawdown"] = "178.71"
    with pytest.raises(ValueError, match="differs from frozen native path"):
        require_native_risk_metrics(observed, frozen)


def test_tp_receipt_state_does_not_use_future_acceptance_at_model_exit():
    at = datetime(2026, 9, 18, 12, 27, 58, 962000, tzinfo=timezone.utc)
    msc = int(at.timestamp() * 1000)
    leg = {"signal_id": "canal2_3171", "leg_index": 0,
           "native_position_id": 123, "target_level": "4356.37",
           "initial_order_requested_tp": None,
           "first_target_accepted_response_utc_msc": msc + 4379,
           "attempts": [{"attempt_id": "first", "started_utc_msc": msc - 100,
                         "responded_utc_msc": msc + 4379, "retcode": 10009,
                         "request_tp": "4356.37"}]}
    position = {"position_id": 123, "entry_msc": msc + OFFSET - 2000,
                "exit_price": "4356.37"}
    exit_event = SequenceEvent(1, "exit", at, "SELL", Decimal("4356.37"),
                               Decimal("0.04"), Decimal("1.75"), "per_leg_target")
    result = receipt_states("canal2_3171", [position], [exit_event], {123: leg})
    assert result[0]["status"] == "pending_target_outcome_unknown"
    assert result[0]["client_confirmed_tp"] is None
    assert result[0]["first_target_accepted_minus_model_ms"] == 4379
    assert receipt_states("canal2_3171", [position], [exit_event], {})[0]["status"] == "missing_receipt_trace"
    leg["initial_order_requested_tp"] = "4356.37"
    assert receipt_states("canal2_3171", [position], [exit_event], {123: leg})[0]["status"] == "initial_tp_acceptance_unproven"
    leg["initial_order_requested_tp"] = None
    leg["target_level"] = "4356.38"
    with pytest.raises(ValueError, match="identity or level"):
        receipt_states("canal2_3171", [position], [exit_event], {123: leg})
