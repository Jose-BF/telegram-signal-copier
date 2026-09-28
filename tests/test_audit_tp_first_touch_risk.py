from datetime import datetime, timezone
from decimal import Decimal

import pytest

from research.causal_comparison import SequenceEvent
from tools.audit_tp_first_touch_risk import early_exit_events


ENTRY = SequenceEvent(101, "entry", datetime(2026, 9, 18, 12, tzinfo=timezone.utc),
                      "BUY", Decimal("100.0"), Decimal("0.04"), Decimal("0.00"), "native")
EXIT = SequenceEvent(101, "exit", datetime(2026, 9, 18, 12, 0, 2, tzinfo=timezone.utc),
                     "BUY", Decimal("101.0"), Decimal("0.04"), Decimal("3.50"), "native_tp")
TOUCH_MS = int(datetime(2026, 9, 18, 12, 0, 1, tzinfo=timezone.utc).timestamp() * 1000)
ROW = {"native_position_id": 101, "first_target_touch_utc_msc": TOUCH_MS,
       "target_level": "101.0", "status": "retained_touch_precedes_native_tp_exit"}


def test_early_exit_changes_only_time_and_mechanism():
    entry, early = early_exit_events({101: [ENTRY, EXIT]}, [ROW])
    assert entry == ENTRY
    assert early.at == datetime(2026, 9, 18, 12, 0, 1, tzinfo=timezone.utc)
    assert early.price == EXIT.price and early.money == EXIT.money
    assert early.volume == EXIT.volume
    assert early.mechanism == "hypothetical_first_post_response_touch"


def test_early_exit_blocks_unbound_or_late_touch():
    with pytest.raises(ValueError, match="unbound"):
        early_exit_events({101: [ENTRY, EXIT]}, [{**ROW, "native_position_id": 102}])
    with pytest.raises(ValueError, match="chronology"):
        early_exit_events({101: [ENTRY, EXIT]}, [{**ROW, "first_target_touch_utc_msc": TOUCH_MS + 2000}])
    with pytest.raises(ValueError, match="target"):
        early_exit_events({101: [ENTRY, EXIT]}, [{**ROW, "target_level": "102.0"}])
