from pathlib import Path

from tools import audit_shadow_read_availability as availability
from tools.audit_shadow_read_availability import classify_transition


BASE = {"sig": "canal2_1", "candidate_id": "gold_555", "event_id": "event-1",
        "transition": "virtual_fill", "transition_tick_msc": 1_780_000_000_000,
        "tick": {"time_msc": 1_780_000_000_000},
        "ts": "2026-05-28T20:26:41.250000+00:00"}


def test_paired_read_window_bounds_backdated_shadow_decision():
    row = classify_transition({
        **BASE, "tick_batch_read_started_utc": "2026-05-28T20:26:41+00:00",
        "tick_batch_read_completed_utc": "2026-05-28T20:26:41.100000+00:00"})
    assert row["status"] == "read_window_observed"
    assert row["source_to_read_complete_ms"] == 1_100
    assert row["read_duration_ms"] == 100
    assert row["read_complete_to_emit_ms"] == 150
    assert row["source_to_emit_ms"] == 1_250


def test_legacy_transition_has_unknown_read_window_not_implicit_parity():
    row = classify_transition(BASE)
    assert row["status"] == "missing_read_window"
    assert row["source_to_emit_ms"] == 1_250
    assert "source_to_read_complete_ms" not in row


def test_partial_reversed_and_future_clocks_are_blocked():
    start = "2026-05-28T20:26:41+00:00"
    end = "2026-05-28T20:26:41.100000+00:00"
    assert classify_transition({**BASE, "tick_batch_read_started_utc": start})[
        "status"] == "blocked_unpaired_read_window"
    assert classify_transition({**BASE, "tick_batch_read_started_utc": end,
                                "tick_batch_read_completed_utc": start})[
        "status"] == "blocked_clock_order"
    assert classify_transition({**BASE, "tick_batch_read_started_utc": start,
                                "tick_batch_read_completed_utc": "2026-05-28T20:26:42+00:00"})[
        "status"] == "blocked_clock_order"
    assert classify_transition({**BASE, "tick_batch_read_started_utc": start,
                                "tick_batch_read_completed_utc": "2026-05-28T20:26:39+00:00"})[
        "status"] == "blocked_clock_order"


def test_tick_identity_and_emission_order_are_required():
    assert classify_transition({**BASE, "transition_tick_msc": 1})[
        "status"] == "blocked_tick_identity"
    assert classify_transition({**BASE, "ts": "2026-05-28T20:26:39+00:00"})[
        "status"] == "blocked_emit_before_tick"
    assert classify_transition({**BASE, "tick": None})["status"] == "not_tick_driven"


def test_audit_keeps_unregistered_control_transition_in_denominator(tmp_path, monkeypatch):
    source = tmp_path / "shadow.gz"
    manifest = tmp_path / "manifest.json"
    source.write_bytes(b"source")
    manifest.write_text("{}", encoding="utf-8")
    bound = {**BASE, "ev": "strategy_shadow_transition", "channel": "canal2"}
    missing_registration = {**bound, "event_id": "event-2", "sig": "canal2_2"}
    monkeypatch.setattr(availability, "shadow_rows",
                        lambda *_args: [bound, missing_registration])
    monkeypatch.setattr(availability, "controls_and_states",
                        lambda _rows: ({"canal2": "gold_555"},
                                       {"canal2_1": [bound]},
                                       {("canal2_1", "gold_555")}, "manifest-hash"))

    report = availability.audit(source, manifest)

    assert report["transition_count"] == 2
    assert report["ticked_with_emit_clock_count"] == 1
    assert report["read_window_observed_count"] == 0
    assert report["statuses"] == {
        "blocked_registration_or_state_chain": 1, "missing_read_window": 1}
    assert report["decision_availability_parity_verified"] is False
    assert all(Path(name).exists() for name in report["inputs_sha256"])
