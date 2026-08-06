from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from provider_zone_simulator import simulate_zone_policy
from provider_zone_spec import ProviderZoneSpec, ZoneState
from zone_entry_policies import zone_policy_by_id
from zone_fill_auditor import audit_zone_depths


BASE = datetime(2026, 8, 4, 10, tzinfo=timezone.utc)


def t(seconds):
    return BASE + timedelta(seconds=seconds)


def frame(rows):
    return pd.DataFrame(rows, columns=["time_utc", "bid", "ask"])


def spec(*states, management=()):
    return ProviderZoneSpec(
        provider_signal_id="canal2_9000",
        channel="canal2",
        ready_at_utc=states[0].observed_utc,
        ready_states=tuple(states),
        management_events=tuple(management),
        execution_batches=(),
        blockers=(),
        warnings=(),
        source_sha256="0" * 64,
    )


def buy_state(at, zone=(100.0, 105.0)):
    return ZoneState(at, "BUY", zone, (110.0,), 95.0)


def test_auditor_does_not_import_candidate_simulator():
    source = Path("zone_fill_auditor.py").read_text(encoding="utf-8")

    assert "provider_zone_simulator" not in source


def test_auditor_reports_first_touch_for_each_depth():
    ticks = frame([
        (t(0), 105.1, 105.3),
        (t(1), 104.7, 104.9),
        (t(2), 102.7, 102.9),
        (t(3), 101.7, 101.9),
    ])

    audit = audit_zone_depths(
        spec(buy_state(BASE)),
        ticks,
        fractions=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
        horizon_at=t(3),
    )

    assert audit["status"] == "audited"
    assert audit["touched_depths"] == [0.0, 0.2, 0.4, 0.6]
    assert audit["first_touch_by_depth"]["0.0"] == t(1).isoformat()
    assert audit["first_touch_by_depth"]["0.4"] == t(2).isoformat()
    assert audit["maximum_penetration_pct"] == 62.0


def test_auditor_applies_range_revision_only_after_observation():
    ticks = frame([
        (t(1), 104.8, 105.0),
        (t(3), 102.8, 103.0),
    ])

    audit = audit_zone_depths(
        spec(buy_state(BASE), buy_state(t(2), zone=(98.0, 103.0))),
        ticks,
        fractions=(0.0, 0.5, 1.0),
        horizon_at=t(3),
    )

    assert audit["first_touch_by_depth"]["0.0"] == t(1).isoformat()
    assert audit["maximum_penetration_pct"] == 0.0
    assert audit["touched_depths"] == [0.0]


def test_auditor_agrees_with_candidate_zone_diagnostics():
    zone_spec = spec(buy_state(BASE))
    ticks = frame([
        (t(0), 105.1, 105.3),
        (t(1), 104.7, 104.9),
        (t(2), 102.4, 102.6),
        (t(3), 99.8, 100.0),
        (t(4), 110.0, 110.2),
    ])
    fractions = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)

    audit = audit_zone_depths(
        zone_spec,
        ticks,
        fractions=fractions,
        horizon_at=t(4),
    )
    candidate = simulate_zone_policy(
        zone_spec,
        ticks,
        zone_policy_by_id("five_equal_limits"),
        horizon_at=t(4),
    )

    assert audit["touched_depths"] == candidate["zone_diagnostics"][
        "touched_depths"
    ]
    assert audit["maximum_penetration_pct"] == candidate[
        "zone_diagnostics"
    ]["maximum_penetration_pct"]


def test_auditor_blocks_invalid_ticks_without_partial_depths():
    audit = audit_zone_depths(
        spec(buy_state(BASE)),
        frame([(t(0), 105.0, 104.0)]),
        fractions=(0.0, 1.0),
        horizon_at=t(1),
    )

    assert audit["status"] == "blocked"
    assert audit["touched_depths"] == []
    assert audit["blockers"] == ["crossed_quote:0"]
