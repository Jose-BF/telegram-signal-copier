from datetime import datetime, timezone
from decimal import Decimal

import numpy as np
import pytest

from tools.audit_tp_quote_corridors import (
    accepted_from_segment, direct_day_anchor, quote_corridor,
)


def at(text):
    return datetime.fromisoformat(text)


def fixture_segment():
    signal = "canal2_3106"
    open_action, modify_action = "open_a", "modify_a"
    lineage = {"decision_id": "decision_a", "session_id": "session_a"}
    return {"segment": {"start_utc": "2026-09-17T14:10:00+00:00",
                        "end_utc": "2026-09-17T14:15:00+00:00",
                        "active_signals": [signal]},
            "events": [
                {"ev": "mt5_order_requested", "sig": signal, "action_id": open_action,
                 "tp": None},
                {"ev": "mt5_order_result", "sig": signal, "action_id": open_action,
                 "deal": 11, "order": 123, "retcode": 10009,
                 "price": 4370.1, "volume": 0.04},
                {"ev": "mt5_modify_requested", "sig": signal, "action_id": modify_action,
                 "ticket": 123, "new_tp": 4370.6, "ts": "2026-09-17T14:12:58.478+00:00",
                 **lineage},
                {"ev": "mt5_action_attempt", "sig": signal, "action_id": modify_action,
                 "ticket": 123, "attempt_id": "attempt_a", "operation": "MODIFY_SLTP",
                 "request_tp": 4370.6, "broker_request_sent": True,
                 "result_retcode": 10009,
                 "broker_request_started_utc": "2026-09-17T14:12:58.600+00:00",
                 "broker_response_received_utc": "2026-09-17T14:12:58.631+00:00",
                 **lineage},
                {"ev": "mt5_modify_confirmed", "sig": signal, "action_id": modify_action,
                 "ticket": 123, "attempt_id": "attempt_a", "retcode": 10009,
                 "ts": "2026-09-17T14:12:58.648+00:00", **lineage},
                {"ev": "mt5_position_snapshot", "sig": signal, "action_id": modify_action,
                 "ticket": 123, "attempt_id": "attempt_a", "retcode": 10009,
                 "event_id": "snapshot_a", "after_action": "MODIFY_SLTP",
                 "position_exists": True, "tp": 4370.6,
                 "ts": "2026-09-17T14:12:58.648+00:00", **lineage},
            ]}


def fixture_position():
    entry = at("2026-09-17T14:12:58.384+00:00")
    return {"entry_msc": int(entry.timestamp() * 1000) + 10_800_000,
            "deal_tickets": [11, 22], "position_id": 123,
            "entry_price": 4370.1, "volume": 0.04}


def test_archival_acceptance_requires_bound_request_attempt_response_and_snapshot():
    segment = fixture_segment()
    model_at = at("2026-09-17T14:13:04.965+00:00")
    result = accepted_from_segment(segment, "canal2_3106", fixture_position(),
                                   model_at, Decimal("4370.60"))
    assert result["accepted_at"] == at("2026-09-17T14:12:58.648+00:00")
    assert result["snapshot_event_id"] == "snapshot_a"
    assert accepted_from_segment(segment, "canal2_3106", fixture_position(),
                                 at("2026-09-17T14:12:58.640+00:00"),
                                 Decimal("4370.60")) is None
    segment["events"][3]["request_tp"] = 4370.7
    with pytest.raises(ValueError, match="chain differs"):
        accepted_from_segment(segment, "canal2_3106", fixture_position(),
                              model_at, Decimal("4370.60"))
    segment["events"][3]["request_tp"] = 4370.6
    segment["events"][1]["price"] = 4370.2
    with pytest.raises(ValueError, match="opening result differs"):
        accepted_from_segment(segment, "canal2_3106", fixture_position(),
                              model_at, Decimal("4370.60"))


def test_retained_quote_corridor_uses_executable_side_and_open_interval():
    start = at("2026-09-18T12:00:00+00:00")
    end = at("2026-09-18T12:00:01+00:00")
    stamps = np.array([int(start.timestamp() * 1e9),
                       int(start.timestamp() * 1e9) + 500_000_000,
                       int(end.timestamp() * 1e9)], dtype=np.int64)
    bids = np.array([100.1, 100.2, 100.3])
    asks = np.array([100.3, 100.4, 100.5])
    flags = np.array([2, 4, 2], dtype=np.uint32)
    result = quote_corridor(stamps, bids, asks, flags, model_at=start, native_at=end,
                            direction="BUY", target=Decimal("100.0"))
    assert result["retained_tick_count"] == result["target_qualifying_tick_count"] == 2
    assert result["target_qualifying_side_update_tick_count"] == 1
    assert result["largest_retained_tick_gap_ms"] == 500
    sell = quote_corridor(stamps, bids, asks, flags, model_at=start, native_at=end,
                          direction="SELL", target=Decimal("100.0"))
    assert sell["target_qualifying_tick_count"] == 0
    with pytest.raises(ValueError, match="invalid TP quote corridor"):
        quote_corridor(stamps, bids, asks, flags, model_at=end, native_at=start,
                       direction="BUY", target=Decimal("100"))


def test_quote_corridor_requires_independent_same_day_clock_anchor():
    anchor = {"independent_clock_evidence": {"days": {
        "2026-09-17": {"status": "direct_anchor_available",
                       "offset_seconds": 10_800, "anchors": [{"id": "a"}]}}}}
    assert direct_day_anchor(anchor, "2026-09-17")["anchor_count"] == 1
    with pytest.raises(ValueError, match="missing: 2026-09-18"):
        direct_day_anchor(anchor, "2026-09-18")
    anchor["independent_clock_evidence"]["days"]["2026-09-17"]["offset_seconds"] = 7200
    with pytest.raises(ValueError, match="missing: 2026-09-17"):
        direct_day_anchor(anchor, "2026-09-17")
