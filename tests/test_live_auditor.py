from datetime import datetime
from types import SimpleNamespace

import gold_live_candidate
from state import Signal
from live_auditor import AuditSettings, LiveAuditor
from pending_actions import PendingAction, PendingQueue, snapshot


class FakeJournal:
    def __init__(self):
        self.events = []
        self.anomalies = []

    def event(self, signal_id, ev, **fields):
        self.events.append({"sig": signal_id, "ev": ev, **fields})

    def anomaly(self, signal_id, category, severity, detail, **ctx):
        self.anomalies.append({
            "sig": signal_id,
            "category": category,
            "severity": severity,
            "detail": detail,
            **ctx,
        })


def _signal():
    sig = Signal(
        channel="canal2",
        message_id=13111,
        direction="SELL",
        timestamp=datetime(2026, 5, 29, 15, 4, 14),
        market_ticket=1365772408,
        extra_market_tickets=[1365772471],
        market_fill_price=4575.36,
        range_low=4575.0,
        range_high=4579.0,
        tps=[4572.0, 4570.0],
        sl=4583.0,
    )
    sig.status = "open"
    return sig


def _pos(ticket, *, sl=0.0, tp=0.0, comment="c2_13111"):
    return SimpleNamespace(
        ticket=ticket,
        magic=20260422,
        sl=sl,
        tp=tp,
        comment=comment,
        price_open=4575.36,
    )


def test_audit_snapshot_records_levels_missing_in_mt5():
    journal = FakeJournal()
    auditor = LiveAuditor(
        settings=AuditSettings(
            level_apply_grace_s=0,
            naked_after_s=999,
            no_position_after_s=90,
            pending_stuck_after_s=30,
            snapshot_every_s=0,
        ),
        journal=journal,
    )

    auditor.audit_cycle(
        signals=[_signal()],
        positions=[_pos(1365772408), _pos(1365772471)],
        pending_actions=[],
        now=datetime(2026, 5, 29, 15, 5, 0),
    )

    snapshots = [e for e in journal.events if e["ev"] == "audit_snapshot"]
    assert len(snapshots) == 1
    assert snapshots[0]["state_tickets"] == [1365772408, 1365772471]
    assert snapshots[0]["mt5_open_tickets"] == [1365772408, 1365772471]
    assert snapshots[0]["tickets_without_sl"] == [1365772408, 1365772471]
    assert snapshots[0]["tickets_without_tp"] == [1365772408, 1365772471]
    assert snapshots[0]["mt5_levels"] == [
        {"ticket": 1365772408, "sl": 0.0, "tp": 0.0},
        {"ticket": 1365772471, "sl": 0.0, "tp": 0.0},
    ]

    issue = journal.anomalies[0]
    assert issue["sig"] == "canal2_13111"
    assert issue["category"] == "levels"
    assert issue["severity"] == "warning"
    assert issue["code"] == "levels_not_applied"
    assert issue["tickets_without_sl"] == [1365772408, 1365772471]
    assert issue["tickets_without_tp"] == [1365772408, 1365772471]


def test_gold_candidate_requires_sl_but_intentionally_has_no_tp():
    journal = FakeJournal()
    auditor = LiveAuditor(
        settings=AuditSettings(
            level_apply_grace_s=0,
            naked_after_s=0,
            snapshot_every_s=0,
        ),
        journal=journal,
    )
    signal = _signal()
    signal.live_strategy_id = gold_live_candidate.CANDIDATE_ID
    signal.live_strategy_fingerprint = (
        gold_live_candidate.CANDIDATE_FINGERPRINT
    )

    auditor.audit_cycle(
        signals=[signal],
        positions=[
            _pos(1365772408, sl=4583.0, tp=0.0, comment="c2_13111_gv1"),
            _pos(1365772471, sl=4583.0, tp=0.0, comment="c2_13111_B1_gv1"),
        ],
        pending_actions=[],
        now=datetime(2026, 5, 29, 15, 5, 0),
    )

    codes = {issue["code"] for issue in journal.anomalies}
    assert "levels_not_applied" not in codes
    assert "mt5_position_naked" not in codes


def test_orphan_mt5_position_with_bot_magic_is_detected():
    journal = FakeJournal()
    auditor = LiveAuditor(
        settings=AuditSettings(
            snapshot_every_s=0,
            orphan_confirmation_s=0,
        ),
        journal=journal,
    )

    auditor.audit_cycle(
        signals=[],
        positions=[_pos(999, sl=4583.0, tp=4572.0, comment="c2_13111")],
        pending_actions=[],
        now=datetime(2026, 5, 29, 15, 5, 0),
    )

    issue = journal.anomalies[0]
    assert issue["sig"] == "bot"
    assert issue["category"] == "mt5"
    assert issue["severity"] == "critical"
    assert issue["code"] == "mt5_orphan_position"
    assert issue["ticket"] == 999
    assert issue["parsed_signal_id"] == "canal2_13111"


def test_orphan_scale_out_leg_matching_open_signal_is_adopted():
    journal = FakeJournal()
    auditor = LiveAuditor(
        settings=AuditSettings(snapshot_every_s=0),
        journal=journal,
    )
    sig = _signal()
    orphan = _pos(1365772499, comment="c2_13111_B2")
    orphan.price_open = 4575.12

    auditor.audit_cycle(
        signals=[sig],
        positions=[
            _pos(1365772408, sl=4583.0, tp=4572.0),
            _pos(1365772471, sl=4583.0, tp=4570.0),
            orphan,
        ],
        pending_actions=[],
        now=datetime(2026, 5, 29, 15, 5, 0),
    )

    assert sig.extra_market_tickets == [1365772471, 1365772499]
    assert sig.extra_market_fill_prices[-1] == 4575.12
    adopted = [
        e for e in journal.events
        if e["ev"] == "mt5_orphan_position_adopted"
    ]
    assert len(adopted) == 1
    assert adopted[0]["sig"] == "canal2_13111"
    assert adopted[0]["ticket"] == 1365772499
    assert not [
        a for a in journal.anomalies
        if a.get("code") == "mt5_orphan_position"
    ]


def test_orphan_scale_out_leg_waits_for_open_tracking_grace():
    journal = FakeJournal()
    auditor = LiveAuditor(
        settings=AuditSettings(
            snapshot_every_s=0,
            orphan_adoption_grace_s=2.0,
        ),
        journal=journal,
    )
    sig = _signal()
    sig.timestamp = datetime(2026, 5, 29, 15, 5, 0)
    orphan = _pos(1365772499, comment="c2_13111_B2")

    auditor.audit_cycle(
        signals=[sig],
        positions=[
            _pos(1365772408, sl=4583.0, tp=4572.0),
            _pos(1365772471, sl=4583.0, tp=4570.0),
            orphan,
        ],
        pending_actions=[],
        now=datetime(2026, 5, 29, 15, 5, 0, 500000),
    )

    assert sig.extra_market_tickets == [1365772471]
    assert not [
        e for e in journal.events
        if e["ev"] == "mt5_orphan_position_adopted"
    ]
    assert not [
        a for a in journal.anomalies
        if a.get("code") == "mt5_orphan_position"
    ]


def test_scale_out_leg_is_silent_while_listener_is_still_opening_batch():
    journal = FakeJournal()
    auditor = LiveAuditor(
        settings=AuditSettings(
            snapshot_every_s=0,
            orphan_adoption_grace_s=0,
            orphan_confirmation_s=0,
        ),
        journal=journal,
    )
    sig = _signal()
    sig.opening_extra_legs = True
    orphan = _pos(1365772499, comment="c2_13111_B2")

    auditor.audit_cycle(
        signals=[sig],
        positions=[
            _pos(1365772408, sl=4583.0, tp=4572.0),
            _pos(1365772471, sl=4583.0, tp=4570.0),
            orphan,
        ],
        pending_actions=[],
        now=datetime(2026, 5, 29, 15, 5, 0),
    )

    assert sig.extra_market_tickets == [1365772471]
    assert not [
        event for event in journal.events
        if event["ev"] == "mt5_orphan_position_adopted"
    ]
    assert not [
        anomaly for anomaly in journal.anomalies
        if anomaly.get("code") == "mt5_orphan_position"
    ]


def test_audit_issue_resolution_is_logged_once_levels_are_applied():
    journal = FakeJournal()
    auditor = LiveAuditor(
        settings=AuditSettings(
            level_apply_grace_s=0,
            naked_after_s=999,
            no_position_after_s=90,
            pending_stuck_after_s=30,
            snapshot_every_s=0,
        ),
        journal=journal,
    )
    sig = _signal()

    auditor.audit_cycle(
        signals=[sig],
        positions=[_pos(1365772408), _pos(1365772471)],
        pending_actions=[],
        now=datetime(2026, 5, 29, 15, 5, 0),
    )
    auditor.audit_cycle(
        signals=[sig],
        positions=[
            _pos(1365772408, sl=4583.0, tp=4572.0),
            _pos(1365772471, sl=4583.0, tp=4570.0),
        ],
        pending_actions=[],
        now=datetime(2026, 5, 29, 15, 5, 5),
    )

    resolved = [e for e in journal.events if e["ev"] == "audit_issue_resolved"]
    assert len(resolved) == 1
    assert resolved[0]["sig"] == "canal2_13111"
    assert resolved[0]["code"] == "levels_not_applied"


def test_unattributed_mt5_level_change_is_reported_once():
    journal = FakeJournal()
    auditor = LiveAuditor(
        settings=AuditSettings(snapshot_every_s=0),
        journal=journal,
    )
    sig = _signal()
    baseline = [
        _pos(1365772408, sl=4583.0, tp=4572.0),
        _pos(1365772471, sl=4583.0, tp=4570.0),
    ]
    changed = [
        _pos(1365772408, sl=4575.36, tp=4572.0),
        _pos(1365772471, sl=4583.0, tp=4570.0),
    ]

    auditor.audit_cycle(
        signals=[sig],
        positions=baseline,
        pending_actions=[],
        now=datetime(2026, 5, 29, 15, 5, 0),
    )
    auditor.audit_cycle(
        signals=[sig],
        positions=changed,
        pending_actions=[],
        now=datetime(2026, 5, 29, 15, 5, 5),
    )
    auditor.audit_cycle(
        signals=[sig],
        positions=changed,
        pending_actions=[],
        now=datetime(2026, 5, 29, 15, 5, 10),
    )

    events = [
        event for event in journal.events
        if event["ev"] == "mt5_level_change_unattributed"
    ]
    anomalies = [
        anomaly for anomaly in journal.anomalies
        if anomaly.get("code") == "mt5_level_change_unattributed"
    ]
    assert len(events) == 1
    assert len(anomalies) == 1
    assert events[0]["ticket"] == 1365772408
    assert events[0]["changed_fields"] == ["sl"]
    assert events[0]["previous"] == {"sl": 4583.0, "tp": 4572.0}
    assert events[0]["current"] == {"sl": 4575.36, "tp": 4572.0}
    assert events[0]["expected"] == {"sl": 4583.0, "tp": 4572.0}
    assert events[0]["sl"] == 4575.36
    assert events[0]["tp"] == 4572.0
    assert events[0]["observed_interval_start_utc"] == (
        "2026-05-29T15:05:00+00:00"
    )
    assert events[0]["observed_interval_end_utc"] == (
        "2026-05-29T15:05:05+00:00"
    )


def test_confirmed_bot_level_change_is_not_reported_as_unattributed():
    journal = FakeJournal()
    auditor = LiveAuditor(
        settings=AuditSettings(snapshot_every_s=0),
        journal=journal,
    )
    sig = _signal()

    auditor.audit_cycle(
        signals=[sig],
        positions=[
            _pos(1365772408, sl=4583.0, tp=4572.0),
            _pos(1365772471, sl=4583.0, tp=4570.0),
        ],
        pending_actions=[],
        now=datetime(2026, 5, 29, 15, 5, 0),
    )
    sig.sl_by_ticket[1365772408] = 4575.36
    auditor.audit_cycle(
        signals=[sig],
        positions=[
            _pos(1365772408, sl=4575.36, tp=4572.0),
            _pos(1365772471, sl=4583.0, tp=4570.0),
        ],
        pending_actions=[],
        now=datetime(2026, 5, 29, 15, 5, 5),
    )

    assert not [
        anomaly for anomaly in journal.anomalies
        if anomaly.get("code") == "mt5_level_change_unattributed"
    ]


def test_unattributed_tp_change_is_visible_in_forensic_event():
    journal = FakeJournal()
    auditor = LiveAuditor(
        settings=AuditSettings(snapshot_every_s=0),
        journal=journal,
    )
    sig = _signal()

    auditor.audit_cycle(
        signals=[sig],
        positions=[
            _pos(1365772408, sl=4583.0, tp=4572.0),
            _pos(1365772471, sl=4583.0, tp=4570.0),
        ],
        pending_actions=[],
        now=datetime(2026, 5, 29, 15, 5, 0),
    )
    auditor.audit_cycle(
        signals=[sig],
        positions=[
            _pos(1365772408, sl=4583.0, tp=4568.0),
            _pos(1365772471, sl=4583.0, tp=4570.0),
        ],
        pending_actions=[],
        now=datetime(2026, 5, 29, 15, 5, 5),
    )

    events = [
        event for event in journal.events
        if event["ev"] == "mt5_level_change_unattributed"
    ]
    assert len(events) == 1
    assert events[0]["changed_fields"] == ["tp"]
    assert events[0]["current"]["tp"] == 4568.0


def test_pending_bot_level_change_is_not_reported_as_unattributed():
    journal = FakeJournal()
    auditor = LiveAuditor(
        settings=AuditSettings(snapshot_every_s=0),
        journal=journal,
    )
    sig = _signal()

    auditor.audit_cycle(
        signals=[sig],
        positions=[
            _pos(1365772408, sl=4583.0, tp=4572.0),
            _pos(1365772471, sl=4583.0, tp=4570.0),
        ],
        pending_actions=[],
        now=datetime(2026, 5, 29, 15, 5, 0),
    )
    auditor.audit_cycle(
        signals=[sig],
        positions=[
            _pos(1365772408, sl=4575.36, tp=4572.0),
            _pos(1365772471, sl=4583.0, tp=4570.0),
        ],
        pending_actions=[{
            "sig_id": "canal2_13111",
            "kind": "MODIFY_SLTP",
            "ticket": 1365772408,
            "new_sl": 4575.36,
            "new_tp": None,
        }],
        now=datetime(2026, 5, 29, 15, 5, 5),
    )

    assert not [
        anomaly for anomaly in journal.anomalies
        if anomaly.get("code") == "mt5_level_change_unattributed"
    ]


def test_recently_confirmed_action_is_evidence_not_a_stuck_pending_action():
    journal = FakeJournal()
    auditor = LiveAuditor(
        settings=AuditSettings(
            pending_stuck_after_s=30,
            snapshot_every_s=0,
        ),
        journal=journal,
    )
    sig = _signal()

    auditor.audit_cycle(
        signals=[sig],
        positions=[
            _pos(1365772408, sl=4575.36, tp=4572.0),
            _pos(1365772471, sl=4583.0, tp=4570.0),
        ],
        pending_actions=[{
            "sig_id": "canal2_13111",
            "kind": "MODIFY_SLTP",
            "ticket": 1365772408,
            "new_sl": 4575.36,
            "new_tp": 4572.0,
            "state": "confirmed_recent",
            "age_s": 45.0,
            "attempts": 1,
            "last_retcode": 10009,
        }],
        now=datetime(2026, 5, 29, 15, 5, 5),
    )

    assert not [
        anomaly for anomaly in journal.anomalies
        if anomaly.get("code") == "pending_action_stuck"
    ]
    audit_snapshot = next(
        event for event in journal.events
        if event["ev"] == "audit_snapshot"
    )
    assert audit_snapshot["pending_actions_count"] == 0


def test_pending_action_stuck_is_detected_for_audit():
    journal = FakeJournal()
    auditor = LiveAuditor(
        settings=AuditSettings(
            pending_stuck_after_s=30,
            snapshot_every_s=0,
        ),
        journal=journal,
    )
    sig = _signal()

    auditor.audit_cycle(
        signals=[sig],
        positions=[
            _pos(1365772408, sl=4583.0, tp=4572.0),
            _pos(1365772471, sl=4583.0, tp=4570.0),
        ],
        pending_actions=[{
            "sig_id": "canal2_13111",
            "kind": "MODIFY_SLTP",
            "ticket": 1365772408,
            "age_s": 45.0,
            "attempts": 200,
            "last_retcode": 10016,
            "label": "SL->85.0 #1365772408",
        }],
        now=datetime(2026, 5, 29, 15, 5, 0),
    )

    issue = journal.anomalies[0]
    assert issue["sig"] == "canal2_13111"
    assert issue["category"] == "mt5"
    assert issue["severity"] == "warning"
    assert issue["code"] == "pending_action_stuck"
    assert issue["ticket"] == 1365772408
    assert issue["age_s"] == 45.0


def test_market_precondition_wait_is_not_reported_as_stuck_retry():
    journal = FakeJournal()
    auditor = LiveAuditor(
        settings=AuditSettings(
            pending_stuck_after_s=30,
            snapshot_every_s=0,
        ),
        journal=journal,
    )

    auditor.audit_cycle(
        signals=[_signal()],
        positions=[
            _pos(1365772408, sl=4583.0, tp=4572.0),
            _pos(1365772471, sl=4583.0, tp=4570.0),
        ],
        pending_actions=[{
            "sig_id": "canal2_13111",
            "kind": "MODIFY_SLTP",
            "ticket": 1365772408,
            "age_s": 45.0,
            "attempts": 0,
            "last_retcode": None,
            "state": "waiting_market",
            "waiting_reason": "requested_sl_waits_for_market",
            "label": "BE #1365772408",
        }],
        now=datetime(2026, 5, 29, 15, 5, 0),
    )

    assert not [
        anomaly for anomaly in journal.anomalies
        if anomaly.get("code") == "pending_action_stuck"
    ]


def test_missing_position_waits_for_disappearance_grace():
    journal = FakeJournal()
    auditor = LiveAuditor(
        settings=AuditSettings(
            snapshot_every_s=0,
            no_position_after_s=0,
            no_position_missing_grace_s=45,
        ),
        journal=journal,
    )
    sig = _signal()

    auditor.audit_cycle(
        signals=[sig],
        positions=[
            _pos(1365772408, sl=4583.0, tp=4572.0),
            _pos(1365772471, sl=4583.0, tp=4570.0),
        ],
        pending_actions=[],
        now=datetime(2026, 5, 29, 15, 5, 0),
    )
    auditor.audit_cycle(
        signals=[sig],
        positions=[],
        pending_actions=[],
        now=datetime(2026, 5, 29, 15, 5, 5),
    )

    assert not [
        a for a in journal.anomalies
        if a.get("code") == "signal_without_mt5_position"
    ]

    auditor.audit_cycle(
        signals=[sig],
        positions=[],
        pending_actions=[],
        now=datetime(2026, 5, 29, 15, 5, 51),
    )

    issue = [
        a for a in journal.anomalies
        if a.get("code") == "signal_without_mt5_position"
    ][0]
    assert issue["missing_for_s"] == 46.0


def test_scale_out_missing_expected_legs_is_detected_after_grace():
    journal = FakeJournal()
    auditor = LiveAuditor(
        settings=AuditSettings(
            snapshot_every_s=0,
            expected_legs_after_s=15,
        ),
        journal=journal,
    )
    sig = _signal()
    sig.entry_mode = "scale_out"

    auditor.audit_cycle(
        signals=[sig],
        positions=[
            _pos(1365772408, sl=4583.0, tp=4572.0),
            _pos(1365772471, sl=4583.0, tp=4570.0),
        ],
        pending_actions=[],
        now=datetime(2026, 5, 29, 15, 5, 0),
    )

    issue = [
        a for a in journal.anomalies
        if a.get("code") == "scale_out_missing_expected_legs"
    ][0]
    assert issue["sig"] == "canal2_13111"
    assert issue["category"] == "fill"
    assert issue["severity"] == "critical"
    assert issue["expected_legs"] == 5
    assert issue["state_legs"] == 2
    assert issue["missing_legs"] == 3
    assert issue["state_tickets"] == [1365772408, 1365772471]


def test_scale_out_missing_legs_is_suppressed_while_opening_is_in_progress():
    journal = FakeJournal()
    auditor = LiveAuditor(
        settings=AuditSettings(
            snapshot_every_s=0,
            expected_legs_after_s=15,
        ),
        journal=journal,
    )
    sig = _signal()
    sig.entry_mode = "scale_out"
    sig.opening_extra_legs = True

    auditor.audit_cycle(
        signals=[sig],
        positions=[
            _pos(1365772408, sl=4583.0, tp=4572.0),
            _pos(1365772471, sl=4583.0, tp=4570.0),
        ],
        pending_actions=[],
        now=datetime(2026, 5, 29, 15, 5, 0),
    )

    assert not [
        anomaly for anomaly in journal.anomalies
        if anomaly.get("code") == "scale_out_missing_expected_legs"
    ]


def test_pending_actions_snapshot_is_read_only_and_serializable():
    sig = _signal()
    q = PendingQueue()
    action = PendingAction(
        kind="MODIFY_SLTP",
        ticket=1365772408,
        signal=sig,
        new_sl=4585.0,
        new_tp=None,
        created_at=100.0,
        attempts=7,
        last_retcode=10016,
        label="SL->4585 #1365772408",
    )
    q._actions.append(action)

    result = snapshot(queue_obj=q, now=145.25)

    assert result == [{
        "sig_id": "canal2_13111",
        "kind": "MODIFY_SLTP",
        "ticket": 1365772408,
        "action_id": action.action_id,
        "decision_id": action.decision_id,
        "message_revision_id": None,
        "action_revision": 0,
        "new_sl": 4585.0,
        "new_tp": None,
        "age_s": 45.2,
        "attempts": 7,
        "last_retcode": 10016,
        "state": "retrying",
        "waiting_reason": None,
        "applied_tp": None,
        "label": "SL->4585 #1365772408",
    }]
    assert len(q._actions) == 1


def test_orphan_mt5_position_requires_confirmation_before_critical_alert():
    journal = FakeJournal()
    auditor = LiveAuditor(
        settings=AuditSettings(
            snapshot_every_s=0,
            orphan_confirmation_s=2.0,
        ),
        journal=journal,
    )
    orphan = _pos(999, sl=4583.0, tp=4572.0, comment="c2_13111")

    auditor.audit_cycle(
        signals=[],
        positions=[orphan],
        pending_actions=[],
        now=datetime(2026, 5, 29, 15, 5, 0),
    )

    assert journal.anomalies == []

    auditor.audit_cycle(
        signals=[],
        positions=[orphan],
        pending_actions=[],
        now=datetime(2026, 5, 29, 15, 5, 2, 100000),
    )

    issues = [
        item for item in journal.anomalies
        if item.get("code") == "mt5_orphan_position"
    ]
    assert len(issues) == 1
    assert issues[0]["ticket"] == 999
