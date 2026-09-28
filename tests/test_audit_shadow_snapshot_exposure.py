import pytest

from tools.audit_shadow_snapshot_exposure import evaluate_snapshot
from tools.audit_week_shadow_control_path import utc_ms


AT = "2026-09-18T12:00:00+00:00"
SOURCE_AT = utc_ms(AT) + 10_800_000
SNAPSHOT = {"sig": "canal2_1", "event_id": "snapshot", "ticket": 101,
            "ts": AT, "position_exists": True}
MARK = {"event_id": "snapshot", "ticket": 101,
        "native_mark_status": "native_mark_profit_matches_same_prior_quote",
        "native_mark_latest_matching_quote_age_ms": 100}
POSITIONS = [
    {"signal_id": "canal2_1", "position_id": ticket, "volume": volume,
     "entry_msc": SOURCE_AT - age, "exit_msc": SOURCE_AT + 30_000,
     "deal_tickets": [ticket + 1000, ticket + 2000]}
    for ticket, volume, age in ((101, "0.04", 30_000), (102, "0.03", 20_000),
                                (103, "0.03", 6_000))
]
SHADOW = [{"event_id": "state", "state_hash": "hash",
           "ts": "2026-09-18T11:59:51+00:00",
           "state": {"positions": [
               {"status": "open", "volume": "0.04"},
               {"status": "open", "volume": "0.03"}]}}]


def test_stable_snapshot_exposes_missing_virtual_dca():
    row = evaluate_snapshot(SNAPSHOT, MARK, POSITIONS, SHADOW,
                            offset_seconds=10_800)
    assert row["status"] == "stable_exposure_difference"
    assert row["native_open_volume"] == "0.10"
    assert row["shadow_open_volume"] == "0.07"
    assert row["volume_delta"] == "-0.03"


def test_snapshot_near_shadow_or_native_transition_is_blocked():
    shadow = [*SHADOW, {"event_id": "recent", "ts": "2026-09-18T11:59:59+00:00",
                        "state": SHADOW[0]["state"]}]
    row = evaluate_snapshot(SNAPSHOT, MARK, POSITIONS, shadow,
                            offset_seconds=10_800)
    assert row["status"] == "blocked_recent_shadow_event"
    positions = [*POSITIONS[:2], {**POSITIONS[2], "entry_msc": SOURCE_AT - 2_000}]
    row = evaluate_snapshot(SNAPSHOT, MARK, positions, SHADOW,
                            offset_seconds=10_800)
    assert row["status"] == "blocked_recent_native_deal"


def test_snapshot_requires_open_position_mark_and_full_native_lifecycle():
    assert evaluate_snapshot({**SNAPSHOT, "position_exists": False}, MARK,
                             POSITIONS, SHADOW, offset_seconds=10_800)["status"] == "blocked_closed_snapshot"
    assert evaluate_snapshot(SNAPSHOT, {**MARK, "native_mark_status": None},
                             POSITIONS, SHADOW, offset_seconds=10_800)["status"] == "blocked_native_mark"
    positions = [{**POSITIONS[0], "deal_tickets": [1, 2, 3]}, *POSITIONS[1:]]
    assert evaluate_snapshot(SNAPSHOT, MARK, positions, SHADOW,
                             offset_seconds=10_800)["status"] == "blocked_partial_native_lifecycle"


def test_post_exit_journal_event_does_not_disprove_earlier_position_read():
    positions = [{**POSITIONS[0], "exit_msc": SOURCE_AT - 19}, *POSITIONS[1:]]
    row = evaluate_snapshot(SNAPSHOT, MARK, positions, SHADOW,
                            offset_seconds=10_800)
    assert row["status"] == "blocked_post_exit_emit_read_order_unknown"
    assert row["journal_after_native_exit_ms"] == 19


def test_shadow_state_clock_regression_is_not_silently_ordered():
    earlier = {**SHADOW[0], "event_id": "earlier",
               "ts": "2026-09-18T11:59:40+00:00"}
    with pytest.raises(ValueError, match="shadow state time regression"):
        evaluate_snapshot(SNAPSHOT, MARK, POSITIONS, [SHADOW[0], earlier],
                          offset_seconds=10_800)


def test_shadow_transition_tick_before_snapshot_but_emission_after_is_blocked():
    delayed = {"event_id": "delayed_exit", "ev": "strategy_shadow_transition",
               "transition": "virtual_position_closed",
               "transition_tick_msc": utc_ms(AT) - 4_000,
               "ts": "2026-09-18T12:00:00.010+00:00",
               "state": {"positions": [{"status": "closed", "volume": "0.03"}]}}
    row = evaluate_snapshot(SNAPSHOT, MARK, POSITIONS, [*SHADOW, delayed],
                            offset_seconds=10_800)
    assert row["status"] == "blocked_shadow_tick_emit_order_unknown"
