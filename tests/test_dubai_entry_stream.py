from copy import deepcopy
from datetime import timedelta

import pytest

from research.dubai_entry_stream import (
    build_stream, classify_snapshot, compare_legacy, make_events, replay_events, to_causal_signals, write_stream,
)
from research.telegram_export import _hash, _iso, _utc


def protocol():
    return {"schema_version": "dubai_entry_stream_protocol_v1", "search_candidates": 0,
        "engine_dataset_ready": False, "pair_seconds": 300, "semantic_labels": [],
        "period": {"start": "2026-01-01T00:00:00Z", "end_exclusive": "2026-09-13T12:20:46Z"},
        "rolling_protocol": {"start": "2026-01-01T00:00:00Z", "end_exclusive": "2026-09-13T12:20:46Z",
                             "development_days": 56, "check_days": 14, "horizon_seconds": 14400}}


def message(mid, seconds=0, *, kind="sticker", direction="BUY", text=None, edit_delay=None, origin=None):
    published = _iso(_utc("2026-01-02T12:00:00Z") + timedelta(seconds=seconds))
    snapshot = {"published_utc": published,
        "edited_utc": _iso(_utc(published) + timedelta(seconds=edit_delay)) if edit_delay is not None else None,
        "has_edit_marker": edit_delay is not None, "is_sticker": kind in ("sticker", "unknown"),
        "is_gold_sticker": kind == "sticker", "direction": direction,
        "text": text if text is not None else f"GOLD {direction} NOW 4000\nTP1: 4010\nSL: 3990" if kind == "text" else "",
        "forward_origin": origin or {}, "reply_to_message_id": None,
        "source_id": "source", "occurrence_id": f"occurrence:{mid}:{edit_delay}", "revision_id": f"revision:{mid}:{edit_delay}"}
    snapshot["raw_snapshot_sha256"] = _hash(snapshot)
    return {"message_id": mid, "chat_id": 1642806869, "snapshots": [snapshot]}


def replay(messages, scenario="revision_time"):
    events, _, _ = make_events(messages, protocol(), scenario)
    return replay_events(events, scenario=scenario)


@pytest.mark.parametrize("order", [("sticker", "text"), ("text", "sticker")])
def test_first_available_component_opens_one_entry(order):
    triggers, decisions = replay([message(1, kind=order[0]), message(2, 5, kind=order[1])])
    assert len(triggers) == 1
    assert triggers[0]["trigger_message_id"] == 1
    assert [r["action"] for r in decisions] == ["new_entry", "pair_complement"]


@pytest.mark.parametrize("delay,count", [(300, 1), (301, 2)])
def test_pair_window_has_an_exact_boundary(delay, count):
    triggers, _ = replay([message(1), message(2, delay, kind="text")])
    assert len(triggers) == count


def test_edit_availability_can_reverse_publication_order_without_backdating():
    triggers, decisions = replay([message(1, edit_delay=60), message(2, 5, kind="text", edit_delay=5)])
    assert len(triggers) == 1
    assert triggers[0]["trigger_message_id"] == 2
    assert triggers[0]["trigger_utc"] == "2026-01-02T12:00:10Z"
    assert decisions[-1]["action"] == "pair_complement"


def test_old_published_message_cannot_pair_on_nearby_edit_alone():
    triggers, _ = replay([message(1, edit_delay=3600), message(2, 3599, kind="text", edit_delay=2)])
    assert len(triggers) == 2


@pytest.mark.parametrize("change", ["same_form", "other_direction", "other_origin", "intervening_entry", "unknown_sticker"])
def test_distinct_offers_are_not_silently_merged(change):
    rows = [message(1), message(2, 20, kind="text")]
    if change == "same_form":
        rows[1] = message(2, 20)
    elif change == "other_direction":
        rows[1] = message(2, 20, kind="text", direction="SELL")
    elif change == "other_origin":
        rows[1] = message(2, 20, kind="text", origin={"forwarded_from": "Another channel"})
    elif change == "intervening_entry":
        rows.insert(1, message(3, 10, direction="SELL"))
    else:
        rows.insert(1, message(3, 10, kind="unknown"))
    triggers, _ = replay(rows)
    assert len(triggers) == (3 if change == "intervening_entry" else 2)


def test_second_text_is_an_offer_even_when_identical_and_reply_linked():
    rows = [message(1), message(2, 10, kind="text"), message(3, 20, kind="text")]
    rows[-1]["snapshots"][0]["reply_to_message_id"] = 2
    triggers, decisions = replay(rows)
    assert len(triggers) == 2
    assert decisions[-1]["action"] == "new_entry"


def test_again_can_be_first_complement_without_creating_a_second_entry():
    triggers, _ = replay([message(1), message(2, 10, kind="text", text="GOLD BUY AGAIN NOW 4000")])
    assert len(triggers) == 1


@pytest.mark.parametrize("prefix", ["SCALP", "SCALP TRADE", "Scalptrade"])
def test_scalp_trade_prefix_does_not_hide_an_explicit_gold_command(prefix):
    triggers, decisions = replay([message(1, direction="SELL"),
        message(2, 30, kind="text", direction="SELL", text=f"{prefix} SELL GOLD NOW 5017\nTP1: 5014\nSL: 5026")])
    assert len(triggers) == 1
    assert decisions[-1]["action"] == "pair_complement"


def test_old_revision_never_reopens_or_retracts_a_message():
    row = message(1)
    row["snapshots"].append(message(1, direction="SELL", edit_delay=86400)["snapshots"][0])
    triggers, decisions = replay([row])
    assert len(triggers) == 1 and triggers[0]["direction"] == "BUY"
    assert decisions[-1]["reason"] == "direction_change_after_trigger"


def test_nonentry_can_become_entry_only_when_new_revision_is_available():
    row = message(1, kind="text", text="Wait for the next signal")
    row["snapshots"].append(message(1, kind="text", edit_delay=40)["snapshots"][0])
    triggers, _ = replay([row])
    assert len(triggers) == 1 and triggers[0]["trigger_utc"] == "2026-01-02T12:00:40Z"


def test_simultaneous_distinct_messages_do_not_depend_on_input_order():
    rows = [message(1), message(2, kind="text")]
    forward, reverse = replay(rows), replay(rows[::-1])
    assert forward == reverse
    assert len(forward[0]) == 2
    assert all(t["same_timestamp_entry_batch"] for t in forward[0])


@pytest.mark.parametrize("second_kind", ["sticker", "text"])
def test_conflicting_same_time_versions_block_even_if_one_version_is_not_an_entry(second_kind):
    row = message(1)
    row["snapshots"].append(message(1, kind=second_kind, direction="SELL",
                                    text="This is context" if second_kind == "text" else None)["snapshots"][0])
    triggers, decisions = replay([row])
    assert not triggers
    assert all(r["action"] == "blocked" for r in decisions)


def test_future_catalog_summary_and_relationships_do_not_change_past_decisions():
    row = message(1)
    before = replay([row])
    row["direction"] = None
    row["issues"] = ["direction_conflict"]
    row["kind"] = "other"
    row["companion_proposal"] = {"signal_id": "future"}
    row["snapshots"].append(message(1, direction="SELL", edit_delay=86400)["snapshots"][0])
    after = replay([row])
    assert before[0] == after[0]
    assert before[1] == after[1][:len(before[1])]


@pytest.mark.parametrize("text", [
    "We are back in the sell zone now. Wait for the next entry.",
    "SELL NOW IN THE ZONE", "If price falls, BUY GOLD NOW", "DO NOT BUY GOLD NOW",
    "GOLD BUY LIMIT AT 4000", "GOLD UPDATE: earlier BUY NOW hit TP1",
])
def test_context_conditional_and_symbol_less_messages_are_not_entries(text):
    snapshot = message(1, kind="text", text=text)["snapshots"][0]
    assert classify_snapshot(snapshot, {})[0] == "context"


def test_semantic_label_is_bound_to_snapshot_not_message_id():
    snapshot = message(1, kind="text", text="Lets buy Some gold now")["snapshots"][0]
    labels = {snapshot["raw_snapshot_sha256"]: {"direction": "BUY"}}
    assert classify_snapshot(snapshot, labels)[:2] == ("text", "BUY")
    changed = message(1, kind="text", text="We bought some gold yesterday")["snapshots"][0]
    assert classify_snapshot(changed, labels)[0] == "context"


def test_duplicate_export_snapshot_is_not_a_second_delivery():
    row = message(1)
    duplicate = deepcopy(row["snapshots"][0])
    duplicate["source_id"] = "source2"
    row["snapshots"].append(duplicate)
    events, _, evidence = make_events([row], protocol(), "revision_time")
    assert len(events) == 1
    assert len(evidence[events[0]["event_id"]]) == 2


def test_equivalent_exports_with_different_filenames_preserve_one_event_and_every_raw_hash():
    row = message(1)
    first = row["snapshots"][0]
    first["media"] = {"path": "export1/sticker (2).webp", "sha256": "a" * 64, "issue": None}
    second = deepcopy(first)
    second["media"]["path"] = "export2/sticker.webp"
    second["source_id"] = "other_source"
    second["raw_snapshot_sha256"] = "b" * 64
    row["snapshots"].append(second)
    events, _, evidence = make_events([row], protocol(), "revision_time")
    assert len(events) == 1
    assert {r["raw_snapshot_sha256"] for r in evidence[events[0]["event_id"]]} == {first["raw_snapshot_sha256"], "b" * 64}
    assert len(replay([row])[0]) == 1


def test_different_media_bytes_at_one_time_remain_a_real_conflict():
    row = message(1)
    row["snapshots"][0]["media"] = {"path": "same.webp", "sha256": "a" * 64, "issue": None}
    changed = deepcopy(row["snapshots"][0])
    changed["media"]["sha256"] = "b" * 64
    changed["raw_snapshot_sha256"] = "c" * 64
    row["snapshots"].append(changed)
    assert not replay([row])[0]


def test_initial_scenario_preserves_edited_only_message_as_unavailable():
    row = message(1, edit_delay=20)
    events, blocked, _ = make_events([row], protocol(), "publication_initial")
    assert not events
    assert blocked[0]["clock_block_reason"] == "edited_snapshot_unavailable_in_initial_scenario"


def test_bridge_preserves_hypothetical_clock_and_has_no_provider_management():
    triggers, _ = replay([message(1, edit_delay=20)])
    signals = to_causal_signals(triggers, scenario="revision_time")
    assert signals[0].observed_at == _utc(triggers[0]["trigger_utc"])
    assert signals[0].published_at == _utc(triggers[0]["published_utc"])
    assert not signals[0].provider_events
    assert triggers[0]["received_utc"] is None and not triggers[0]["engine_admitted"]
    with pytest.raises(ValueError):
        to_causal_signals(triggers, scenario="publication_initial")


def test_legacy_comparison_cannot_drive_grouping_or_drop_splits():
    triggers, decisions = replay([message(1), message(2, 301, kind="text")])
    legacy = [{"entry_id": "legacy", "directional_message_ids": [1, 2],
               "known_revision_or_initial_component_utc": "2026-01-02T12:00:00Z", "review_flags": ["late_complement"]}]
    compared = compare_legacy(legacy, triggers, decisions)
    assert compared[0]["status"] == "split"
    assert len(compared[0]["trigger_ids"]) == 2


def test_full_stream_accounts_for_every_message_in_both_scenarios(tmp_path):
    rows = [message(1), message(2, 30, kind="text", edit_delay=30), message(3, 60, kind="unknown")]
    result = build_stream(rows, [], protocol())
    assert len(result["message_dispositions"]) == 6
    assert all(s["source_messages"] == 3 for s in result["summary"].values())
    assert all(r["valid"] for r in result["prefix_checks"])
    result.update(inputs={"protected_archive_dirs": []}, environment={})
    manifest = write_stream(result, tmp_path / "out")
    assert not manifest["engine_dataset_ready"]
    with pytest.raises(ValueError):
        write_stream(result, tmp_path / "out")
