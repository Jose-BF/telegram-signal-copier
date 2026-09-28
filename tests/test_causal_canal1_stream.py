from datetime import datetime, timedelta, timezone

import pytest

from research.causal_canal1_stream import (
    compile_canal1_stream, route_compiled_canal1_stream,
)
from research.causal_lifecycle import PREFIX_SOURCE


BASE = datetime(2026, 9, 17, 14, tzinfo=timezone.utc)


def raw(message_id, seconds, text=None, *, sticker=None, reply=None, revision=1):
    at = (BASE + timedelta(seconds=seconds)).isoformat()
    return {"ev": "telegram_raw", "channel": "canal1", "message_id": message_id,
            "message_revision_id": f"rev-{message_id}-{revision}",
            "date_utc": at, "ts": at, "text": text, "is_edit": revision > 1,
            "edit_date_utc": None, "reply_to_msg_id": reply,
            "sticker_id": sticker}


def test_stream_detaches_statically_assigned_text_and_keeps_orphan_management():
    unrelated = dict(raw(9, -20, "Move SL to BE"), channel="canal2")
    rows = [unrelated, raw(10, -10, "Move SL to BE"),
            raw(11, 0, sticker="buy-sticker"),
            raw(12, 30, "BUY GOLD NOW 4366 TP1: 4370 SL: 4355"),
            raw(13, 40, "Move SL to BE", reply=12)]

    stream = compile_canal1_stream(
        rows, start=BASE - timedelta(minutes=1), cutoff=BASE + timedelta(minutes=5),
        sticker_directions={"buy-sticker": "BUY"}, max_entry_age_s=120)

    assert [row.signal_id for row in stream.stickers] == ["canal1_11"]
    assert stream.stickers[0].provider_events == ()
    assert [row.signal_id for row in stream.texts] == ["canal1_12"]
    assert stream.texts[0].provider_events[0].action == "LEVEL_UPDATE"
    assert [(row.message_id, row.event.action, row.reply_to_msg_id)
            for row in stream.management] == [
                (10, "MOVE_SL_TO_BE", None), (13, "MOVE_SL_TO_BE", 12)]
    assert [(row.kind, row.message_id) for row in stream.timeline] == [
        ("management", 10), ("sticker", 11), ("text_candidate", 12),
        ("management", 13)]
    assert stream.static_provider_events_detached == 2
    assert stream.legacy_static_root_diagnostic_count == 1
    assert not any(row["reason"] == "unresolved_management_root"
                   for row in stream.diagnostics)


def test_stream_rejects_conflicting_revision_and_ambiguous_receipt_order():
    sticker = raw(11, 0, sticker="buy-sticker")
    conflict = dict(sticker, text="Move SL to BE")
    with pytest.raises(ValueError, match="revision identity conflict"):
        compile_canal1_stream(
            [sticker, conflict], start=BASE - timedelta(minutes=1),
            cutoff=BASE + timedelta(minutes=5),
            sticker_directions={"buy-sticker": "BUY"}, max_entry_age_s=120)

    same_clock = raw(12, 0, "BUY GOLD NOW 4366 TP1: 4370 SL: 4355")
    with pytest.raises(ValueError, match="ambiguous canal1 message order"):
        compile_canal1_stream(
            [sticker, same_clock], start=BASE - timedelta(minutes=1),
            cutoff=BASE + timedelta(minutes=5),
            sticker_directions={"buy-sticker": "BUY"}, max_entry_age_s=120)


def test_raw_stream_reply_tracks_open_or_fallback_text_branch():
    rows = [raw(11, 0, sticker="buy-sticker"),
            raw(12, 30, "BUY GOLD NOW 4366 TP1: 4370 SL: 4355"),
            raw(13, 40, "Move SL to BE", reply=12)]
    stream = compile_canal1_stream(
        rows, start=BASE - timedelta(minutes=1), cutoff=BASE + timedelta(minutes=5),
        sticker_directions={"buy-sticker": "BUY"}, max_entry_age_s=120)

    def branch(sticker_closed):
        applied = []

        def replay(signal):
            return {"signal_id": signal.signal_id,
                    "entries": [{"ticket": f"sim_{signal.signal_id}",
                                 "opened_at": signal.observed_at.isoformat(),
                                 "volume": 0.04}],
                    "exits": ([{"ticket": "sim_canal1_11",
                               "closed_at": (BASE + timedelta(seconds=20)).isoformat(),
                               "volume": 0.04}] if signal.signal_id == "canal1_11"
                              and sticker_closed else []),
                    "blockers": []}

        def state(signal_id, prior, at):
            return {"status": "closed" if sticker_closed and signal_id == "canal1_11"
                    else "open", "observed_at": at.isoformat(),
                    "lifecycle_source": PREFIX_SOURCE}

        def update(event, prior):
            applied.append((event.message_id, prior["signal_id"]))
            return prior

        report = route_compiled_canal1_stream(
            stream,
            replay_entry=replay, apply_text_update=lambda signal, prior: prior,
            apply_management=update, resolve_state=state,
            initial_universe_complete=True)
        return report, applied

    open_branch, open_applied = branch(False)
    flat_branch, flat_applied = branch(True)
    assert [row["status"] for row in open_branch["decisions"]] == [
        "sticker_entry", "targets_open_signal", "management_targets_signal"]
    assert [row["status"] for row in flat_branch["decisions"]] == [
        "sticker_entry", "eligible_text_fallback", "management_targets_signal"]
    assert open_applied == [(13, "canal1_11")]
    assert flat_applied == [(13, "canal1_12")]


def test_unresolved_management_is_a_timeline_blocker_not_just_a_warning():
    stream = compile_canal1_stream(
        [raw(11, 0, sticker="buy-sticker"),
         raw(12, 10, "Move SL to BE if price reaches 4370"),
         raw(13, 20, "BUY GOLD NOW 4366 TP1: 4370 SL: 4355")],
        start=BASE - timedelta(minutes=1), cutoff=BASE + timedelta(minutes=5),
        sticker_directions={"buy-sticker": "BUY"}, max_entry_age_s=120)

    assert [(row.kind, row.message_id) for row in stream.timeline] == [
        ("sticker", 11), ("unresolved", 12), ("text_candidate", 13)]
    assert stream.unresolved[0].reason == "conditional_management_unsupported"
    report = route_compiled_canal1_stream(
        stream,
        replay_entry=lambda signal: {"signal_id": signal.signal_id,
                                     "entries": [], "exits": [], "blockers": []},
        initial_universe_complete=True)
    assert [row["status"] for row in report["decisions"]] == [
        "sticker_entry", "blocked_unresolved_provider_event",
        "blocked_incomplete_prior_universe"]


def test_causal_grammar_routes_direct_close_and_be_but_preserves_optional_choice():
    stream = compile_canal1_stream(
        [raw(11, 0, sticker="buy-sticker"),
         raw(12, 10, "Close this now guys"),
         raw(13, 20, "SL to BE and let's see if we catch the move"),
         raw(14, 30, "If you don't want risk close now")],
        start=BASE - timedelta(minutes=1), cutoff=BASE + timedelta(minutes=5),
        sticker_directions={"buy-sticker": "BUY"}, max_entry_age_s=120)

    assert [(row.message_id, row.event.action) for row in stream.management] == [
        (12, "CLOSE_ALL"), (13, "MOVE_SL_TO_BE")]
    assert [(row.message_id, row.reason) for row in stream.unresolved] == [
        (14, "conditional_management_unsupported")]


@pytest.mark.parametrize("choice,expected_kind,expected_status", [
    ("hold", "optional_hold", "optional_management_held"),
    ("close", "management", "management_targets_signal"),
])
def test_optional_close_choice_is_fixed_before_replay_and_preserves_source_modality(
    choice, expected_kind, expected_status,
):
    rows = [raw(11, 0, sticker="buy-sticker"),
            raw(12, 10, "Back around entry if you dont want to take risk close now")]
    stream = compile_canal1_stream(
        rows, start=BASE - timedelta(minutes=1), cutoff=BASE + timedelta(minutes=5),
        sticker_directions={"buy-sticker": "BUY"}, max_entry_age_s=120,
        optional_close_choice=choice)
    assert [(row.kind, row.message_id) for row in stream.timeline] == [
        ("sticker", 11), (expected_kind, 12)]
    assert stream.unresolved == ()
    applied = []

    def apply(message, prior):
        applied.append((message.message_id, message.event.payload))
        return prior

    report = route_compiled_canal1_stream(
        stream, replay_entry=lambda signal: {
            "signal_id": signal.signal_id,
            "entries": [{"ticket": "ticket-11", "opened_at": BASE.isoformat(),
                         "volume": 0.04}], "exits": [], "blockers": []},
        apply_management=apply, initial_universe_complete=True)
    assert [row["status"] for row in report["decisions"]] == [
        "sticker_entry", expected_status]
    assert report["prior_universe_complete"] is True
    if choice == "hold":
        assert applied == []
    else:
        assert applied[0][0] == 12
        assert applied[0][1]["source_modality"] == "optional"
        assert applied[0][1]["chosen_option"] == "CLOSE_ALL"


def test_optional_choice_does_not_resolve_conditional_or_unrecognized_actions():
    rows = [raw(11, 0, sticker="buy-sticker"),
            raw(12, 10, "Move SL to BE if price reaches 4370")]
    for choice in ("hold", "close"):
        stream = compile_canal1_stream(
            rows, start=BASE - timedelta(minutes=1),
            cutoff=BASE + timedelta(minutes=5),
            sticker_directions={"buy-sticker": "BUY"}, max_entry_age_s=120,
            optional_close_choice=choice)
        assert [(item.kind, item.message_id) for item in stream.timeline] == [
            ("sticker", 11), ("unresolved", 12)]
    with pytest.raises(ValueError, match="optional close choice"):
        compile_canal1_stream(
            rows, start=BASE - timedelta(minutes=1),
            cutoff=BASE + timedelta(minutes=5),
            sticker_directions={"buy-sticker": "BUY"}, max_entry_age_s=120,
            optional_close_choice="auto")


@pytest.mark.parametrize("choice", ["hold", "close"])
def test_optional_close_edit_with_same_semantics_is_not_a_second_action(choice):
    edited = raw(12, 25, "Back around entry if you dont want to take risk close now",
                 revision=2)
    rows = [raw(11, 0, sticker="buy-sticker"),
            raw(12, 10, edited["text"]), edited]
    stream = compile_canal1_stream(
        rows, start=BASE - timedelta(minutes=1), cutoff=BASE + timedelta(minutes=5),
        sticker_directions={"buy-sticker": "BUY"}, max_entry_age_s=120,
        optional_close_choice=choice)
    assert [(item.kind, item.message_id) for item in stream.timeline] == [
        ("sticker", 11),
        ("management" if choice == "close" else "optional_hold", 12),
        ("optional_repeat", 12)]
    applied = []

    def replay(signal):
        return {"signal_id": signal.signal_id,
                "entries": [{"ticket": "ticket-11", "opened_at": BASE.isoformat(),
                             "volume": 0.04}], "exits": [], "blockers": []}

    def apply(message, prior):
        applied.append(message.message_revision_id)
        return prior

    report = route_compiled_canal1_stream(
        stream, replay_entry=replay, apply_management=apply,
        initial_universe_complete=True)
    assert report["decisions"][-1]["status"] == "optional_management_repeated"
    assert applied == (["rev-12-1"] if choice == "close" else [])
