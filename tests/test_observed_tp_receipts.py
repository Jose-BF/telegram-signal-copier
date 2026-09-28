import pytest

from research.observed_tp_receipts import tp_receipt_state


LEG = {"target_level": "101.00", "initial_order_requested_tp": None,
       "attempts": [
           {"attempt_id": "first", "request_tp": "101.00", "started_utc_msc": 100,
            "responded_utc_msc": 300, "retcode": 10016},
           {"attempt_id": "second", "request_tp": "101.00", "started_utc_msc": 400,
            "responded_utc_msc": 600, "retcode": 10009},
           {"attempt_id": "repeat", "request_tp": "101.00", "started_utc_msc": 700,
            "responded_utc_msc": 900, "retcode": 10009}]}


def test_tp_receipt_state_never_uses_future_rejection_or_acceptance():
    pending = tp_receipt_state(LEG, 200)
    assert pending["target_status"] == "pending_target_outcome_unknown"
    assert pending["confirmed_tp"] is None
    changed_future = {**LEG, "attempts": [{**LEG["attempts"][0], "retcode": 10009},
                                          *LEG["attempts"][1:]]}
    assert tp_receipt_state(changed_future, 200) == pending
    assert tp_receipt_state(LEG, 350)["target_status"] == "target_not_confirmed_in_client_trace"
    assert tp_receipt_state(LEG, 500)["target_status"] == "pending_target_outcome_unknown"
    assert tp_receipt_state(LEG, 650)["target_status"] == "client_accepted_target_broker_unobserved"
    assert tp_receipt_state(LEG, 800)["target_status"] == "client_accepted_target_redundant_pending"


def test_tp_receipt_state_blocks_equal_millisecond_ordering():
    assert tp_receipt_state(LEG, 100)["target_status"] == "clock_tie_blocked"
    assert tp_receipt_state(LEG, 300)["target_status"] == "clock_tie_blocked"


def test_tp_receipt_state_rejects_duplicate_and_reverse_response():
    with pytest.raises(ValueError, match="duplicate"):
        tp_receipt_state({**LEG, "attempts": LEG["attempts"] * 2}, 200)
    with pytest.raises(ValueError, match="chronology"):
        tp_receipt_state({**LEG, "attempts": [{**LEG["attempts"][0],
                                               "responded_utc_msc": 90}]}, 200)
