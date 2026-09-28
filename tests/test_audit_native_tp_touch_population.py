from decimal import Decimal as D

import numpy as np
import pytest

from tools.audit_native_tp_touch_population import (
    accepted_target_response,
    target_from_deal,
    terminal_touches,
)


def test_target_requires_native_tp_reason_comment_and_fill_price():
    assert target_from_deal({"reason": 5, "comment": "[tp 4283.14]",
                             "price": 4283.14}) == D("4283.14")
    assert target_from_deal({"reason": 3, "comment": "bot_close", "price": 4283.14}) is None
    with pytest.raises(ValueError, match="target identity"):
        target_from_deal({"reason": 5, "comment": "[tp 4283.14]", "price": 4283.15})


def test_terminal_touch_uses_executable_side_updates_and_acceptance_clock():
    tape = (np.array([1, 2, 3, 4], dtype=np.int64) * 1_000_000,
            np.array([100.0, 101.0, 101.0, 99.0]),
            np.array([100.1, 101.1, 101.1, 99.1]))
    flags = np.array([2, 1, 2, 2], dtype=np.uint32)
    result = terminal_touches(tape, flags, entry_ns=1_000_000,
                              exit_ns=4_000_000, direction="BUY", target=D(101),
                              accepted_ns=2_500_000)
    assert result["qualifying_tick_count"] == 2
    assert result["qualifying_side_update_count"] == 1
    assert result["qualifying_side_updates_after_accepted_response"] == 1
    assert result["first_touch_to_native_exit_ms"] == 1


def test_accepted_tp_requires_matching_attempt_and_does_not_infer_missing_receipt():
    position = {"position_id": 42, "signal_id": "canal2_9"}
    assert accepted_target_response(None, position, D(101), exit_ns=4_000_000)[1] == "missing_client_tp_receipt_trace"
    trace = {"native_position_id": 42, "signal_id": "canal2_9",
             "target_level": 101, "initial_order_requested_tp": None,
             "first_target_accepted_response_utc_msc": 2,
             "attempts": [{"retcode": 10009, "request_tp": 101,
                           "responded_utc_msc": 2}]}
    accepted, status = accepted_target_response(trace, position, D(101),
                                                exit_ns=4_000_000)
    assert accepted == 2_000_000
    assert status == "client_target_accepted_response_before_native_exit"
