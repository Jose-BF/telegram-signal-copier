from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from tools.audit_observed_tp_lifecycle import observed_case


BASE = datetime(2026, 9, 18, 12, tzinfo=timezone.utc)


def case():
    base_ns = int(BASE.timestamp()) * 1_000_000_000
    row = {"position_id": 42, "direction": "BUY", "target": 101,
           "accepted_response_utc": BASE.isoformat(),
           "native_exit_utc": (BASE + timedelta(milliseconds=4)).isoformat(),
           "terminal_corridor": {"first_post_accept_touch_to_native_exit_ms": 3}}
    position = {"entry_price": 100}
    tape = (base_ns + np.array([1, 2, 3], dtype=np.int64) * 1_000_000,
            np.array([101.0, 101.1, 101.2]),
            np.array([101.1, 101.2, 101.3]))
    flags = np.array([2, 2, 2], dtype=np.uint32)
    return row, position, tape, flags


def test_observed_native_fill_waits_after_first_terminal_touch():
    row, position, tape, flags = case()
    result = observed_case(row, position, tape, flags)
    assert result["status"] == "observed_fill_after_terminal_touch"
    assert result["terminal_to_fill_ms"] == 3
    assert result["relevant_side_updates"] == 3
    assert result["position_open_after_touch_until_deal"] is True


def test_same_clock_terminal_ticks_are_not_silently_ordered():
    row, position, tape, flags = case()
    tape = (np.array([tape[0][0], tape[0][0], tape[0][2]]), tape[1], tape[2])
    with pytest.raises(ValueError, match="same-clock"):
        observed_case(row, position, tape, flags)
